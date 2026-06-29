"""Test parallel startup — 2 servers should start near-simultaneously."""
import sys, time, threading
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
sys.path.insert(0, r'd:\VE3_SUITE\server\flowkit')
from modules.flow_extension_auth import FlowExtensionAuth, _ExtensionInstanceManager
import httpx

servers = [
    {'name': 'sv1', 'chrome_path': r'D:\VE3_SUITE\GoogleChromePortable - Copy (1)\GoogleChromePortable.exe',
     'flow_account_bundle': 'dendalion41@gmail.com|Damuocmo212@|ag7b 35ne sdsx na3z xgkj kydl 3bmx 3fir'},
    {'name': 'sv2', 'chrome_path': r'D:\VE3_SUITE\GoogleChromePortable - Copy (2)\GoogleChromePortable.exe',
     'flow_account_bundle': 'lua789001@gmail.com|ZKGdMeBOVTEexf|dcw3 v7ux 747r ibfg t6do y4fk j4gv zsrr'},
]

def log(msg):
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)

# Test 1: Parallel startup speed
log("=== TEST: Parallel startup (2 servers) ===")
ready_event = threading.Event()
t0 = time.time()
FlowExtensionAuth.start_all_instances(servers, r'D:\VE3_SUITE', log_func=log, ready_event=ready_event)
total = time.time() - t0
log(f"\nTotal startup time: {total:.0f}s")

# Test 2: Verify both ready
log("\n=== VERIFY ===")
for i, srv in enumerate(servers):
    port = 8100 + i
    try:
        h = httpx.get(f'http://127.0.0.1:{port}/health', timeout=5).json()
        log(f"  {srv['name']} (port {port}): instance={h.get('instance')}, connected={h.get('extension_connected')}, token={h.get('flow_key_present')}")
    except Exception as e:
        log(f"  {srv['name']} (port {port}): FAIL {e}")

# Test 3: ready_event was set
log(f"\n  ready_event set: {ready_event.is_set()}")

# Test 4: Release Chrome + verify token survives
log("\n=== TEST: Release Chrome ===")
for srv in servers:
    _ExtensionInstanceManager.release_chrome(srv['name'], log=log)

time.sleep(2)
for i, srv in enumerate(servers):
    port = 8100 + i
    try:
        t = httpx.get(f'http://127.0.0.1:{port}/api/get-token', timeout=5).json()
        log(f"  {srv['name']}: token={'OK' if t.get('success') else 'FAIL'} (Chrome released)")
    except Exception as e:
        log(f"  {srv['name']}: FAIL {e}")

# Test 5: Reopen Chrome
log("\n=== TEST: Reopen Chrome ===")
ok = _ExtensionInstanceManager.reopen_chrome_for_instance('sv1', r'D:\VE3_SUITE', log=log)
log(f"  Reopen sv1: {'OK' if ok else 'FAIL'}")
if ok:
    t = httpx.get('http://127.0.0.1:8100/api/get-token', timeout=5).json()
    log(f"  Token after reopen: {'OK' if t.get('success') else 'FAIL'}")

# Cleanup
_ExtensionInstanceManager.release_chrome('sv1', log=log)

log(f"\n=== DONE (total {total:.0f}s for 2 servers) ===")
