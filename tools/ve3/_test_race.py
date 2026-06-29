"""Test race condition: 2 processes goi start_all cung luc."""
import sys, time, subprocess, json
sys.stdout.reconfigure(encoding='utf-8', errors='replace')

def log(msg):
    ts = time.strftime("%H:%M:%S")
    print(f"[{ts}] {msg}")
    sys.stdout.flush()

# Kill old
log("Killing old processes...")
subprocess.run(['taskkill', '/F', '/IM', 'GoogleChromePortable.exe'], capture_output=True)
for port in range(8100, 8102):
    subprocess.run(['powershell', '-Command',
        f'try {{ Get-NetTCPConnection -LocalPort {port} -State Listen -ErrorAction Stop | ForEach-Object {{ Stop-Process -Id $_.OwningProcess -Force }} }} catch {{}}'],
        capture_output=True, timeout=5)
time.sleep(3)

# Start 2 workers as separate processes at the same time
worker_code = r'''
import sys, time
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
sys.path.insert(0, r'd:\VE3_SUITE\server\flowkit')
sys.path.insert(0, r'd:\VE3_SUITE\tools\ve3')
from modules.flow_extension_auth import FlowExtensionAuth
import httpx

name = sys.argv[1]
servers = [
    {'name': 'sv1', 'chrome_path': r'D:\VE3_SUITE\GoogleChromePortable - Copy (1)\GoogleChromePortable.exe',
     'flow_account_bundle': 'dendalion41@gmail.com|Damuocmo212@|ag7b 35ne sdsx na3z xgkj kydl 3bmx 3fir'},
]

def log(msg):
    ts = time.strftime("%H:%M:%S")
    print(f"[{ts}] [{name}] {msg}", flush=True)

# Check port directly first (like a real worker)
url = FlowExtensionAuth.get_agent_url_by_index(0)
if url:
    log(f"Agent already running: {url}")
else:
    log("Agent not running — starting...")
    FlowExtensionAuth.start_all_instances(servers, r'D:\VE3_SUITE', log_func=log)

# Wait and check
for _ in range(30):
    url = FlowExtensionAuth.get_agent_url_by_index(0)
    if url:
        break
    time.sleep(2)

if url:
    try:
        h = httpx.get(f"{url}/health", timeout=5).json()
        log(f"RESULT: connected={h.get('extension_connected')}, token={h.get('flow_key_present')}")
    except Exception as e:
        log(f"RESULT: FAIL {e}")
else:
    log("RESULT: NO AGENT")
'''

import tempfile, os
script = os.path.join(tempfile.gettempdir(), '_test_worker.py')
with open(script, 'w', encoding='utf-8') as f:
    f.write('# -*- coding: utf-8 -*-\n' + worker_code)

log("Starting 2 workers simultaneously...")
p1 = subprocess.Popen([sys.executable, script, 'W1'], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
p2 = subprocess.Popen([sys.executable, script, 'W2'], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)

out1 = p1.communicate(timeout=600)[0]
out2 = p2.communicate(timeout=600)[0]

log("\n=== WORKER 1 ===")
for line in out1.strip().split('\n')[-5:]:
    log(f"  {line}")

log("\n=== WORKER 2 ===")
for line in out2.strip().split('\n')[-5:]:
    log(f"  {line}")

# Final check
import httpx
log("\n=== FINAL CHECK ===")
try:
    h = httpx.get('http://127.0.0.1:8100/health', timeout=5).json()
    log(f"Port 8100: connected={h.get('extension_connected')}, token={h.get('flow_key_present')}")
except:
    log("Port 8100: NOT RUNNING")
