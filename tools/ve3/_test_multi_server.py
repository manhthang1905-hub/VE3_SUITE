"""Test 3 servers cung luc — verify port dung, khong trung."""
import sys, time
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
sys.path.insert(0, r'd:\VE3_SUITE\server\flowkit')
from modules.flow_extension_auth import FlowExtensionAuth
import httpx

servers = [
    {'name': 'sv1', 'chrome_path': r'D:\VE3_SUITE\GoogleChromePortable - Copy (1)\GoogleChromePortable.exe',
     'flow_account_bundle': 'dendalion41@gmail.com|Damuocmo212@|ag7b 35ne sdsx na3z xgkj kydl 3bmx 3fir'},
    {'name': 'sv2', 'chrome_path': r'D:\VE3_SUITE\GoogleChromePortable - Copy (2)\GoogleChromePortable.exe',
     'flow_account_bundle': 'lua789001@gmail.com|ZKGdMeBOVTEexf|dcw3 v7ux 747r ibfg t6do y4fk j4gv zsrr'},
    {'name': 'sv3', 'chrome_path': r'D:\VE3_SUITE\GoogleChromePortable - Copy (3)\GoogleChromePortable.exe',
     'flow_account_bundle': 'ellazokarenkaren@gmail.com|YSx4PoELpc113U7|einz y5zm xa52 5xlm 5qyh ey7t bnui muem'},
]

def log(msg):
    ts = time.strftime("%H:%M:%S")
    print(f"[{ts}] {msg}")
    sys.stdout.flush()

log("=" * 60)
log("TEST: 3 servers — verify each gets correct port")
log("=" * 60)

FlowExtensionAuth.start_all_instances(servers, r'D:\VE3_SUITE', log_func=log)

log("\n" + "=" * 60)
log("RESULTS")
log("=" * 60)

all_pass = True
for i, srv in enumerate(servers):
    port = 8100 + i
    name = srv['name']
    log(f"\n--- {name} (expected port {port}) ---")
    try:
        h = httpx.get(f'http://127.0.0.1:{port}/health', timeout=5).json()
        inst = h.get('instance', '?')
        conn = h.get('extension_connected', False)
        tok = h.get('flow_key_present', False)
        log(f"  instance={inst}, connected={conn}, token={tok}")
        # Verify instance name matches
        if inst != name:
            log(f"  WRONG INSTANCE! Expected {name}, got {inst}")
            all_pass = False
        elif not conn or not tok:
            log(f"  NOT READY")
            all_pass = False
        else:
            t = httpx.get(f'http://127.0.0.1:{port}/api/get-token', timeout=5).json()
            if t.get('success'):
                log(f"  Token: {t['token'][:25]}... OK")
            else:
                log(f"  Token: FAIL")
                all_pass = False
    except Exception as e:
        log(f"  ERROR: {e}")
        all_pass = False

log(f"\n{'ALL PASSED' if all_pass else 'SOME FAILED'} — ports verified unique per server")
