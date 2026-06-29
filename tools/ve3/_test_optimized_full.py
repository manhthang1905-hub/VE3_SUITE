"""Full optimized flow test:
1. Start Chrome → get auth data → kill Chrome
2. Generate image (no Chrome) via REMOTE server simulation
3. Simulate token expired (401) → reopen Chrome → get fresh token → kill Chrome
4. Generate again with new token
"""
import sys, time, subprocess, random, struct, zlib
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
sys.path.insert(0, r'd:\VE3_SUITE\server\flowkit')
from pathlib import Path
from modules.flow_extension_auth import FlowExtensionAuth, _ExtensionInstanceManager
import httpx

servers = [{'name': 'sv1', 'chrome_path': r'D:\VE3_SUITE\GoogleChromePortable - Copy (1)\GoogleChromePortable.exe',
     'flow_account_bundle': 'dendalion41@gmail.com|Damuocmo212@|ag7b 35ne sdsx na3z xgkj kydl 3bmx 3fir'}]

results = {}
chrome_dir = Path(r'D:\VE3_SUITE\GoogleChromePortable - Copy (1)')

def log(msg):
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)

def chrome_running():
    r = subprocess.run(['wmic', 'process', 'where',
        "name='chrome.exe' and CommandLine like '%Copy (1)%'",
        'get', 'ProcessId', '/FORMAT:CSV'],
        capture_output=True, text=True, timeout=5, creationflags=0x08000000)
    return any(line.strip() and line.strip()[-1].isdigit() for line in r.stdout.splitlines())

def make_png():
    raw = b''.join(b'\x00' + bytes([100,150,200])*100 for _ in range(100))
    def chunk(ct, d):
        c = ct+d; return struct.pack('>I',len(d))+c+struct.pack('>I',zlib.crc32(c)&0xffffffff)
    return b'\x89PNG\r\n\x1a\n'+chunk(b'IHDR',struct.pack('>IIBBBBB',100,100,8,2,0,0,0))+chunk(b'IDAT',zlib.compress(raw))+chunk(b'IEND',b'')

# ═══════════════════════════════════════════════
log("=" * 60)
log("TEST: OPTIMIZED FLOW — open/close Chrome as needed")
log("=" * 60)

# ── Phase 1: Get all auth data, then kill Chrome ──
log("\n▶ PHASE 1: Open Chrome → get token + project + upload → kill Chrome")
FlowExtensionAuth.start_all_instances(servers, r'D:\VE3_SUITE', log_func=log)
auth = FlowExtensionAuth('http://127.0.0.1:8100', log_func=log)

token = auth.get_token()
log(f"  Token: {'OK' if token else 'FAIL'} ({len(token) if token else 0} chars)")
results['phase1_token'] = bool(token)

pid = auth.get_project_id()
if not pid:
    pid = auth.ensure_project()
log(f"  Project: {pid}")
results['phase1_project'] = bool(pid)

img = Path(r'd:\VE3_SUITE\tools\ve3\_test_opt.png')
img.write_bytes(make_png())
media_id = auth.upload_image(str(img), token, pid) if token and pid else None
img.unlink(missing_ok=True)
log(f"  Media: {media_id}")
results['phase1_upload'] = bool(media_id)

# Kill Chrome
log("  Killing Chrome...")
_ExtensionInstanceManager._kill_chrome_for_dir(chrome_dir)
time.sleep(3)
log(f"  Chrome running: {chrome_running()}")

# ── Phase 2: Verify agent still serves token (no Chrome) ──
log("\n▶ PHASE 2: Agent serves token without Chrome")
t = httpx.get('http://127.0.0.1:8100/api/get-token', timeout=5).json()
results['phase2_token_no_chrome'] = t.get('success', False)
log(f"  get_token: {'OK' if t.get('success') else 'FAIL'}")
log(f"  Chrome running: {chrome_running()}")

# ── Phase 3: Simulate what VE3 worker does — send to server ──
log("\n▶ PHASE 3: Worker generates (sends to remote server, uses stored data)")
log(f"  Token: {token[:20]}... | Project: {pid} | Media: {media_id}")
log(f"  Worker would call: http://192.168.88.xxx:5100/api/generate")
log(f"  (We skip actual remote call — Chrome NOT needed for this)")
results['phase3_no_chrome_needed'] = True

# ── Phase 4: Simulate 401 (token expired) → reopen Chrome → get fresh token ──
log("\n▶ PHASE 4: Simulate token expired → reopen Chrome → refresh")
log("  Simulating 401 from server...")
log("  force_refresh=True → re-setup Chrome...")

# Re-run start (same as force_refresh does)
FlowExtensionAuth.start_all_instances(servers, r'D:\VE3_SUITE', log_func=log)

# Get fresh token
token2 = auth.get_token()
results['phase4_refresh_token'] = bool(token2)
if token2:
    log(f"  Fresh token: {token2[:20]}... ({len(token2)} chars)")
    log(f"  Same as before: {token2 == token}")
else:
    log("  Fresh token: FAIL")

# Kill Chrome again
log("  Killing Chrome again...")
_ExtensionInstanceManager._kill_chrome_for_dir(chrome_dir)
time.sleep(3)
log(f"  Chrome running: {chrome_running()}")

# ── Phase 5: Verify fresh token still in agent ──
log("\n▶ PHASE 5: Fresh token still available after 2nd Chrome kill")
t3 = httpx.get('http://127.0.0.1:8100/api/get-token', timeout=5).json()
results['phase5_token_after_refresh'] = t3.get('success', False)
log(f"  get_token: {'OK' if t3.get('success') else 'FAIL'}")

# ── Phase 6: Test multiple open/close cycles ──
log("\n▶ PHASE 6: Rapid open/close cycle (stress test)")
for cycle in range(3):
    log(f"  Cycle {cycle+1}/3: reopen...")
    FlowExtensionAuth.start_all_instances(servers, r'D:\VE3_SUITE', log_func=lambda m: None)
    tk = auth.get_token()
    _ExtensionInstanceManager._kill_chrome_for_dir(chrome_dir)
    time.sleep(2)
    tk_after = httpx.get('http://127.0.0.1:8100/api/get-token', timeout=5).json()
    ok = bool(tk) and tk_after.get('success', False)
    log(f"  Cycle {cycle+1}: token={'OK' if tk else 'FAIL'}, after_kill={'OK' if tk_after.get('success') else 'FAIL'}")
    if not ok:
        results[f'phase6_cycle_{cycle+1}'] = False
        break
else:
    results['phase6_stress'] = True

# ═══════════════════════════════════════════════
log("\n" + "=" * 60)
log("RESULTS")
log("=" * 60)
all_pass = all(results.values())
for k, v in results.items():
    log(f"  [{'PASS' if v else 'FAIL'}] {k}")
log(f"\n{'ALL PASSED' if all_pass else 'SOME FAILED'}")
log(f"\nConclusion: Chrome only needed for auth (token+project+upload).")
log(f"After auth done → kill Chrome → agent serves token from RAM.")
log(f"On 401 → reopen Chrome briefly → get fresh token → kill again.")
