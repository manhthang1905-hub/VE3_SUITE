"""Test PRODUCTION scenario: workers are separate processes, run sequentially per server."""
import sys, time, subprocess, struct, zlib
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
sys.path.insert(0, r'd:\VE3_SUITE\server\flowkit')
from pathlib import Path
from modules.flow_extension_auth import _ExtensionInstanceManager, FlowExtensionAuth
import httpx

srv1 = {'name': 'sv1', 'chrome_path': r'D:\VE3_SUITE\GoogleChromePortable - Copy (1)\GoogleChromePortable.exe',
        'flow_account_bundle': 'dendalion41@gmail.com|Damuocmo212@|ag7b 35ne sdsx na3z xgkj kydl 3bmx 3fir'}
srv2 = {'name': 'sv2', 'chrome_path': r'D:\VE3_SUITE\GoogleChromePortable - Copy (2)\GoogleChromePortable.exe',
        'flow_account_bundle': 'lua789001@gmail.com|ZKGdMeBOVTEexf|dcw3 v7ux 747r ibfg t6do y4fk j4gv zsrr'}

results = {}
def log(msg):
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)

def make_png():
    raw = b''.join(b'\x00' + bytes([100,150,200])*50 for _ in range(50))
    def chunk(ct, d):
        c = ct+d; return struct.pack('>I',len(d))+c+struct.pack('>I',zlib.crc32(c)&0xffffffff)
    return b'\x89PNG\r\n\x1a\n'+chunk(b'IHDR',struct.pack('>IIBBBBB',50,50,8,2,0,0,0))+chunk(b'IDAT',zlib.compress(raw))+chunk(b'IEND',b'')

log("=" * 60)
log("PRODUCTION TEST")
log("=" * 60)

# ═══ 1: Excel xong → worker sv1 start + auth + release ═══
log("\n--- 1. Worker sv1: Excel xong → start + auth ---")
t0 = time.time()
ok = _ExtensionInstanceManager.start_one(0, srv1, r'D:\VE3_SUITE', log=log)
results['1_sv1_start'] = ok
auth = FlowExtensionAuth('http://127.0.0.1:8100', log_func=log)
token = auth.get_token()
pid = auth.get_project_id() or auth.ensure_project()
img = Path(r'd:\VE3_SUITE\tools\ve3\_t.png'); img.write_bytes(make_png())
media = auth.upload_image(str(img), token, pid) if token and pid else None
img.unlink(missing_ok=True)
results['1_sv1_auth'] = bool(token) and bool(pid) and bool(media)
_ExtensionInstanceManager.release_chrome('sv1', log=log)
log(f"token={'OK' if token else 'FAIL'} project={'OK' if pid else 'FAIL'} media={'OK' if media else 'FAIL'} ({time.time()-t0:.0f}s)")

# ═══ 2: Worker sv2: same flow ═══
log("\n--- 2. Worker sv2: Excel xong → start + auth ---")
t0 = time.time()
ok = _ExtensionInstanceManager.start_one(1, srv2, r'D:\VE3_SUITE', log=log)
results['2_sv2_start'] = ok
auth2 = FlowExtensionAuth('http://127.0.0.1:8101', log_func=log)
token2 = auth2.get_token()
pid2 = auth2.get_project_id() or auth2.ensure_project()
img = Path(r'd:\VE3_SUITE\tools\ve3\_t.png'); img.write_bytes(make_png())
media2 = auth2.upload_image(str(img), token2, pid2) if token2 and pid2 else None
img.unlink(missing_ok=True)
results['2_sv2_auth'] = bool(token2) and bool(pid2) and bool(media2)
_ExtensionInstanceManager.release_chrome('sv2', log=log)
log(f"token={'OK' if token2 else 'FAIL'} project={'OK' if pid2 else 'FAIL'} media={'OK' if media2 else 'FAIL'} ({time.time()-t0:.0f}s)")

# ═══ 3: Token từ RAM (không Chrome) ═══
log("\n--- 3. Token from RAM (no Chrome) ---")
t1 = httpx.get('http://127.0.0.1:8100/api/get-token', timeout=5).json()
t2 = httpx.get('http://127.0.0.1:8101/api/get-token', timeout=5).json()
results['3_ram_token'] = t1.get('success') and t2.get('success')
log(f"sv1: {'OK' if t1.get('success') else 'FAIL'}, sv2: {'OK' if t2.get('success') else 'FAIL'}, Chrome: 0")

# ═══ 4: 401 → kill + start lai (sv1) ═══
log("\n--- 4. Token expired sv1 → kill + start lai ---")
t0 = time.time()
ok = _ExtensionInstanceManager.start_one(0, srv1, r'D:\VE3_SUITE', log=log)
results['4_refresh_start'] = ok
if ok:
    auth3 = FlowExtensionAuth('http://127.0.0.1:8100', log_func=log)
    token3 = auth3.get_token()
    results['4_refresh_token'] = bool(token3)
    _ExtensionInstanceManager.release_chrome('sv1', log=log)
    log(f"token: {'OK' if token3 else 'FAIL'} ({time.time()-t0:.0f}s)")

# ═══ 5: 401 → kill + start lai (sv2) ═══
log("\n--- 5. Token expired sv2 → kill + start lai ---")
t0 = time.time()
ok = _ExtensionInstanceManager.start_one(1, srv2, r'D:\VE3_SUITE', log=log)
results['5_refresh_start'] = ok
if ok:
    auth4 = FlowExtensionAuth('http://127.0.0.1:8101', log_func=log)
    token4 = auth4.get_token()
    results['5_refresh_token'] = bool(token4)
    _ExtensionInstanceManager.release_chrome('sv2', log=log)
    log(f"token: {'OK' if token4 else 'FAIL'} ({time.time()-t0:.0f}s)")

# ═══ 6: Final check ═══
log("\n--- 6. Final state ---")
chrome = subprocess.run(['tasklist', '/FI', 'IMAGENAME eq chrome.exe', '/FO', 'CSV'],
    capture_output=True, text=True, timeout=5)
chrome_n = sum(1 for l in chrome.stdout.splitlines() if 'chrome' in l.lower())
results['6_no_chrome'] = chrome_n == 0
results['6_sv1_alive'] = _ExtensionInstanceManager._is_agent_alive(8100)
results['6_sv2_alive'] = _ExtensionInstanceManager._is_agent_alive(8101)
log(f"Chrome: {chrome_n}, sv1 agent: {'alive' if results['6_sv1_alive'] else 'dead'}, sv2 agent: {'alive' if results['6_sv2_alive'] else 'dead'}")

# ═══ SUMMARY ═══
log("\n" + "=" * 60)
all_pass = all(results.values())
for k, v in results.items():
    log(f"  [{'PASS' if v else 'FAIL'}] {k}")
log(f"\n{'ALL PASSED' if all_pass else 'SOME FAILED'}")
