"""Test start_all — verify multi-instance Chrome + agent + extension."""
import sys, time
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
sys.path.insert(0, r'd:\VE3_SUITE\server\flowkit')
from modules.flow_extension_auth import FlowExtensionAuth
import httpx

servers = [
    {
        'name': 'sv1',
        'chrome_path': r'D:\VE3_SUITE\GoogleChromePortable - Copy (1)\GoogleChromePortable.exe',
        'flow_account_bundle': 'dendalion41@gmail.com|Damuocmo212@|ag7b 35ne sdsx na3z xgkj kydl 3bmx 3fir',
    },
    {
        'name': 'sv2',
        'chrome_path': r'D:\VE3_SUITE\GoogleChromePortable - Copy (2)\GoogleChromePortable.exe',
        'flow_account_bundle': 'lua789001@gmail.com|ZKGdMeBOVTEexf|dcw3 v7ux 747r ibfg t6do y4fk j4gv zsrr',
    },
]

def log(msg):
    ts = time.strftime("%H:%M:%S")
    print(f"[{ts}] {msg}")
    sys.stdout.flush()

log("=== Testing start_all (2 servers) ===")
FlowExtensionAuth.start_all_instances(servers, r'D:\VE3_SUITE', log_func=log)

log("\n=== CHECK RESULTS ===")
for i, srv in enumerate(servers):
    name = srv['name']
    port = 8100 + i
    log(f"\n--- {name} (port {port}) ---")
    try:
        h = httpx.get(f'http://127.0.0.1:{port}/health', timeout=5).json()
        log(f"  Health: instance={h.get('instance')}, connected={h.get('extension_connected')}, token={h.get('flow_key_present')}")
    except Exception as e:
        log(f"  Health FAIL: {e}")
    try:
        t = httpx.get(f'http://127.0.0.1:{port}/api/get-token', timeout=5).json()
        if t.get('success'):
            log(f"  Token: {t['token'][:30]}... OK ({len(t['token'])} chars)")
        else:
            log(f"  Token: FAIL - {t.get('error')}")
    except Exception as e:
        log(f"  Token FAIL: {e}")

log("\n=== DONE ===")
