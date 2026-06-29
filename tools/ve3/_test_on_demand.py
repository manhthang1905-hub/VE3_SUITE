"""Test on-demand Chrome: worker tu mo Chrome khi can, dong khi xong."""
import sys, time
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
sys.path.insert(0, r'd:\VE3_SUITE\server\flowkit')
from modules.flow_extension_auth import _ExtensionInstanceManager, FlowExtensionAuth
import httpx

srv1 = {'name': 'sv1', 'chrome_path': r'D:\VE3_SUITE\GoogleChromePortable - Copy (1)\GoogleChromePortable.exe',
        'flow_account_bundle': 'dendalion41@gmail.com|Damuocmo212@|ag7b 35ne sdsx na3z xgkj kydl 3bmx 3fir'}
srv2 = {'name': 'sv2', 'chrome_path': r'D:\VE3_SUITE\GoogleChromePortable - Copy (2)\GoogleChromePortable.exe',
        'flow_account_bundle': 'lua789001@gmail.com|ZKGdMeBOVTEexf|dcw3 v7ux 747r ibfg t6do y4fk j4gv zsrr'}

def log(msg):
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)

results = {}

# === Test 1: start_one for sv1 (index 0, port 8100) ===
log("=== Test 1: start_one sv1 (on-demand) ===")
t0 = time.time()
ok = _ExtensionInstanceManager.start_one(0, srv1, r'D:\VE3_SUITE', log=log)
t1 = time.time() - t0
log(f"Result: {'OK' if ok else 'FAIL'} ({t1:.0f}s)")
results['start_sv1'] = ok

# Check token
h = httpx.get('http://127.0.0.1:8100/health', timeout=5).json()
log(f"sv1: connected={h.get('extension_connected')}, token={h.get('flow_key_present')}")
results['sv1_ready'] = h.get('extension_connected') and h.get('flow_key_present')

# === Test 2: start_one for sv2 (index 1, port 8101) ===
log("\n=== Test 2: start_one sv2 (on-demand) ===")
t0 = time.time()
ok = _ExtensionInstanceManager.start_one(1, srv2, r'D:\VE3_SUITE', log=log)
t2 = time.time() - t0
log(f"Result: {'OK' if ok else 'FAIL'} ({t2:.0f}s)")
results['start_sv2'] = ok

h = httpx.get('http://127.0.0.1:8101/health', timeout=5).json()
log(f"sv2: connected={h.get('extension_connected')}, token={h.get('flow_key_present')}")
results['sv2_ready'] = h.get('extension_connected') and h.get('flow_key_present')

# === Test 3: release both ===
log("\n=== Test 3: Release Chrome (both servers) ===")
_ExtensionInstanceManager.release_chrome('sv1', log=log)
_ExtensionInstanceManager.release_chrome('sv2', log=log)
time.sleep(2)

t1 = httpx.get('http://127.0.0.1:8100/api/get-token', timeout=5).json()
t2_r = httpx.get('http://127.0.0.1:8101/api/get-token', timeout=5).json()
log(f"sv1 token after release: {'OK' if t1.get('success') else 'FAIL'}")
log(f"sv2 token after release: {'OK' if t2_r.get('success') else 'FAIL'}")
results['release_token'] = t1.get('success') and t2_r.get('success')

# === Test 4: reopen sv1 (simulate 401) ===
log("\n=== Test 4: Reopen sv1 (simulate 401 refresh) ===")
t0 = time.time()
ok = _ExtensionInstanceManager.reopen_chrome_for_instance('sv1', r'D:\VE3_SUITE', log=log)
t3 = time.time() - t0
log(f"Reopen: {'OK' if ok else 'FAIL'} ({t3:.0f}s)")
results['reopen_sv1'] = ok

# Release again
_ExtensionInstanceManager.release_chrome('sv1', log=log)

# === Test 5: start_one on already-running agent (should be fast) ===
log("\n=== Test 5: start_one sv1 again (agent already running) ===")
t0 = time.time()
ok = _ExtensionInstanceManager.start_one(0, srv1, r'D:\VE3_SUITE', log=log)
t4 = time.time() - t0
log(f"Result: {'OK' if ok else 'FAIL'} ({t4:.0f}s)")
results['restart_sv1'] = ok

_ExtensionInstanceManager.release_chrome('sv1', log=log)

# === Summary ===
log("\n" + "=" * 50)
all_pass = all(results.values())
for k, v in results.items():
    log(f"  [{'PASS' if v else 'FAIL'}] {k}")
log(f"\n{'ALL PASSED' if all_pass else 'SOME FAILED'}")
