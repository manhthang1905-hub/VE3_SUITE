"""Comprehensive test: on-demand Chrome, per-port lock, release, reopen, race condition, CMD count."""
import sys, time, subprocess, threading, struct, zlib, random, os
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
sys.path.insert(0, r'd:\VE3_SUITE\server\flowkit')
from pathlib import Path
from modules.flow_extension_auth import _ExtensionInstanceManager, FlowExtensionAuth
import httpx

servers = [
    {'name': 'sv1', 'chrome_path': r'D:\VE3_SUITE\GoogleChromePortable - Copy (1)\GoogleChromePortable.exe',
     'flow_account_bundle': 'dendalion41@gmail.com|Damuocmo212@|ag7b 35ne sdsx na3z xgkj kydl 3bmx 3fir'},
    {'name': 'sv2', 'chrome_path': r'D:\VE3_SUITE\GoogleChromePortable - Copy (2)\GoogleChromePortable.exe',
     'flow_account_bundle': 'lua789001@gmail.com|ZKGdMeBOVTEexf|dcw3 v7ux 747r ibfg t6do y4fk j4gv zsrr'},
]
results = {}

def log(msg):
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)

def count_cmd():
    r = subprocess.run(['wmic', 'process', 'where', "name='cmd.exe'", 'get', 'ProcessId', '/FORMAT:CSV'],
                       capture_output=True, text=True, timeout=5, creationflags=0x08000000)
    return sum(1 for l in r.stdout.splitlines() if l.strip() and l.strip()[-1].isdigit())

def chrome_count():
    try:
        return len([p for p in subprocess.run(['tasklist', '/FI', 'IMAGENAME eq chrome.exe', '/FO', 'CSV'],
            capture_output=True, text=True, timeout=5).stdout.splitlines() if 'chrome' in p.lower()])
    except Exception:
        return 0

log("=" * 60)
log("COMPREHENSIVE TEST — On-demand Chrome")
log("=" * 60)

cmd_before = count_cmd()
log(f"CMD processes before: {cmd_before}")

# ═══ TEST 1: start_one sv1 — correct port ═══
log("\n--- TEST 1: start_one sv1 (port 8100) ---")
t0 = time.time()
ok = _ExtensionInstanceManager.start_one(0, servers[0], r'D:\VE3_SUITE', log=log)
elapsed = time.time() - t0
h = httpx.get('http://127.0.0.1:8100/health', timeout=5).json()
results['T1_start_sv1'] = ok and h.get('extension_connected') and h.get('flow_key_present')
log(f"RESULT: {'PASS' if results['T1_start_sv1'] else 'FAIL'} ({elapsed:.0f}s)")

# ═══ TEST 2: start_one sv2 — different port ═══
log("\n--- TEST 2: start_one sv2 (port 8101) ---")
ok = _ExtensionInstanceManager.start_one(1, servers[1], r'D:\VE3_SUITE', log=log)
h = httpx.get('http://127.0.0.1:8101/health', timeout=5).json()
results['T2_start_sv2'] = ok and h.get('instance') == 'sv2'
log(f"RESULT: {'PASS' if results['T2_start_sv2'] else 'FAIL'}")

# ═══ TEST 3: get token + project + upload (while Chrome open) ═══
log("\n--- TEST 3: Full auth flow (token + project + upload) ---")
auth = FlowExtensionAuth('http://127.0.0.1:8100', log_func=log)
token = auth.get_token()
pid = auth.get_project_id()
if not pid:
    pid = auth.ensure_project()

def make_png():
    raw = b''.join(b'\x00' + bytes([100,150,200])*50 for _ in range(50))
    def chunk(ct, d):
        c = ct+d; return struct.pack('>I',len(d))+c+struct.pack('>I',zlib.crc32(c)&0xffffffff)
    return b'\x89PNG\r\n\x1a\n'+chunk(b'IHDR',struct.pack('>IIBBBBB',50,50,8,2,0,0,0))+chunk(b'IDAT',zlib.compress(raw))+chunk(b'IEND',b'')

img = Path(r'd:\VE3_SUITE\tools\ve3\_test_comp.png')
img.write_bytes(make_png())
media = auth.upload_image(str(img), token, pid) if token and pid else None
img.unlink(missing_ok=True)
results['T3_auth'] = bool(token) and bool(pid) and bool(media)
log(f"token={'OK' if token else 'FAIL'}, project={'OK' if pid else 'FAIL'}, media={'OK' if media else 'FAIL'}")
log(f"RESULT: {'PASS' if results['T3_auth'] else 'FAIL'}")

# ═══ TEST 4: release Chrome — token survives ═══
log("\n--- TEST 4: Release Chrome, token survives ---")
_ExtensionInstanceManager.release_chrome('sv1', log=log)
_ExtensionInstanceManager.release_chrome('sv2', log=log)
time.sleep(2)
t1 = httpx.get('http://127.0.0.1:8100/api/get-token', timeout=5).json()
t2 = httpx.get('http://127.0.0.1:8101/api/get-token', timeout=5).json()
results['T4_release'] = t1.get('success') and t2.get('success')
log(f"sv1 token: {'OK' if t1.get('success') else 'FAIL'}, sv2 token: {'OK' if t2.get('success') else 'FAIL'}")
log(f"Chrome count: {chrome_count()}")
log(f"RESULT: {'PASS' if results['T4_release'] else 'FAIL'}")

# ═══ TEST 5: reopen Chrome (401 refresh) ═══
log("\n--- TEST 5: Reopen Chrome (simulate 401) ---")
t0 = time.time()
ok = _ExtensionInstanceManager.reopen_chrome_for_instance('sv1', r'D:\VE3_SUITE', log=log)
elapsed = time.time() - t0
results['T5_reopen'] = ok
log(f"RESULT: {'PASS' if ok else 'FAIL'} ({elapsed:.0f}s)")
_ExtensionInstanceManager.release_chrome('sv1', log=log)

# ═══ TEST 6: per-port lock — 2 processes try same port ═══
log("\n--- TEST 6: Per-port lock (race condition) ---")
worker_code = r'''
import sys, time, os
sys.path.insert(0, r'd:\VE3_SUITE\server\flowkit')
sys.path.insert(0, r'd:\VE3_SUITE\tools\ve3')
from modules.flow_extension_auth import _ExtensionInstanceManager
name = sys.argv[1]
srv = {'name': 'sv1', 'chrome_path': r'D:\VE3_SUITE\GoogleChromePortable - Copy (1)\GoogleChromePortable.exe',
       'flow_account_bundle': 'dendalion41@gmail.com|Damuocmo212@|ag7b 35ne sdsx na3z xgkj kydl 3bmx 3fir'}
ok = _ExtensionInstanceManager.start_one(0, srv, r'D:\VE3_SUITE',
    log=lambda m: print(f'[{name}] {m}', flush=True))
print(f'[{name}] RESULT: {ok}', flush=True)
'''
import tempfile
script = os.path.join(tempfile.gettempdir(), '_test_race_worker.py')
with open(script, 'w', encoding='utf-8') as f:
    f.write(worker_code)
# Kill agent to force both workers to start
_ExtensionInstanceManager._kill_on_port(8100, log)
time.sleep(2)
p1 = subprocess.Popen([sys.executable, script, 'W1'], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
p2 = subprocess.Popen([sys.executable, script, 'W2'], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
out1 = p1.communicate(timeout=180)[0]
out2 = p2.communicate(timeout=180)[0]
w1_ok = 'RESULT: True' in out1
w2_ok = 'RESULT: True' in out2
results['T6_race'] = w1_ok and w2_ok
log(f"W1: {'PASS' if w1_ok else 'FAIL'}, W2: {'PASS' if w2_ok else 'FAIL'}")
log(f"RESULT: {'PASS' if results['T6_race'] else 'FAIL'}")

# ═══ TEST 7: CMD process count (should not grow much) ═══
log("\n--- TEST 7: CMD process accumulation ---")
cmd_after = count_cmd()
cmd_diff = cmd_after - cmd_before
results['T7_cmd'] = cmd_diff < 10
log(f"CMD before: {cmd_before}, after: {cmd_after}, diff: {cmd_diff}")
log(f"RESULT: {'PASS' if results['T7_cmd'] else 'FAIL (too many CMD)'}")

# ═══ TEST 8: idempotent release ═══
log("\n--- TEST 8: Idempotent release ---")
_ExtensionInstanceManager.release_chrome('sv1', log=log)
_ExtensionInstanceManager.release_chrome('sv1', log=log)
_ExtensionInstanceManager.release_chrome('sv1', log=log)
results['T8_idempotent'] = True
log("RESULT: PASS (no crash)")

# ═══ SUMMARY ═══
log("\n" + "=" * 60)
log("SUMMARY")
log("=" * 60)
all_pass = all(results.values())
for k, v in results.items():
    log(f"  [{'PASS' if v else 'FAIL'}] {k}")
log(f"\n{'ALL PASSED' if all_pass else 'SOME FAILED'}")
