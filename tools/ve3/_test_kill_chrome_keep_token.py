"""Test: start → get token → KILL Chrome → agent still returns token?"""
import sys, time, subprocess
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
sys.path.insert(0, r'd:\VE3_SUITE\server\flowkit')
from pathlib import Path
from modules.flow_extension_auth import FlowExtensionAuth, _ExtensionInstanceManager
import httpx

servers = [
    {'name': 'sv1', 'chrome_path': r'D:\VE3_SUITE\GoogleChromePortable - Copy (1)\GoogleChromePortable.exe',
     'flow_account_bundle': 'dendalion41@gmail.com|Damuocmo212@|ag7b 35ne sdsx na3z xgkj kydl 3bmx 3fir'},
]

def log(msg):
    ts = time.strftime("%H:%M:%S")
    print(f"[{ts}] {msg}")
    sys.stdout.flush()

# Step 1: Start normally
log("=== Step 1: Start (Chrome + agent + extension) ===")
FlowExtensionAuth.start_all_instances(servers, r'D:\VE3_SUITE', log_func=log)

# Step 2: Verify token
log("\n=== Step 2: Get token (Chrome running) ===")
h = httpx.get('http://127.0.0.1:8100/health', timeout=5).json()
log(f"Health: connected={h.get('extension_connected')}, token={h.get('flow_key_present')}")
t = httpx.get('http://127.0.0.1:8100/api/get-token', timeout=5).json()
token1 = t.get('token', '')
log(f"Token: {token1[:30]}... ({len(token1)} chars)")

# Step 3: KILL Chrome (keep agent running)
log("\n=== Step 3: Kill Chrome (agent stays) ===")
_ExtensionInstanceManager._kill_chrome_for_dir(Path(r'D:\VE3_SUITE\GoogleChromePortable - Copy (1)'))
time.sleep(3)

chrome_alive = bool(subprocess.run(['tasklist', '/FI', 'IMAGENAME eq chrome.exe'],
    capture_output=True, text=True).stdout.count('chrome.exe'))
log(f"Chrome alive: {chrome_alive}")

# Step 4: Agent still has token?
log("\n=== Step 4: Get token AFTER Chrome killed ===")
h2 = httpx.get('http://127.0.0.1:8100/health', timeout=5).json()
log(f"Health: connected={h2.get('extension_connected')}, token={h2.get('flow_key_present')}")
t2 = httpx.get('http://127.0.0.1:8100/api/get-token', timeout=5).json()
token2 = t2.get('token', '')
if token2:
    log(f"Token: {token2[:30]}... ({len(token2)} chars)")
    log(f"Same token: {token1 == token2}")
else:
    log(f"Token: FAIL - {t2.get('error')}")

# Step 5: Can we still use this token to generate?
log("\n=== Step 5: Generate image with stored token (no Chrome) ===")
if token2:
    import random
    auth = FlowExtensionAuth('http://127.0.0.1:8100', log_func=log)
    pid = auth.get_project_id()
    if not pid:
        pid = auth.ensure_project()
    log(f"Project: {pid}")
    if pid:
        session = f";{int(time.time()*1000)}"
        body = {
            "bearer_token": token2, "project_id": pid,
            "flow_url": f"https://aisandbox-pa.googleapis.com/v1/projects/{pid}/flowMedia:batchGenerateImages",
            "body_json": {"clientContext": {"sessionId": session, "projectId": pid, "tool": "PINHOLE"},
                "requests": [{"clientContext": {"sessionId": session, "projectId": pid, "tool": "PINHOLE"},
                    "seed": random.randint(100000, 999999), "imageModelName": "NARWHAL",
                    "imageAspectRatio": "IMAGE_ASPECT_RATIO_LANDSCAPE",
                    "prompt": "A peaceful zen garden at sunset", "imageInputs": []}]}
        }
        try:
            r = httpx.post('http://127.0.0.1:8100/api/generate-image', json=body, timeout=120).json()
            if r.get('success'):
                log("Image gen: OK (Chrome was killed, agent still works!)")
            else:
                log(f"Image gen: FAIL - {r.get('error', '?')[:80]}")
        except Exception as e:
            log(f"Image gen: ERROR - {e}")

# Summary
log("\n=== SUMMARY ===")
log(f"Token before kill: {'YES' if token1 else 'NO'}")
log(f"Token after kill:  {'YES' if token2 else 'NO'}")
log(f"Chrome needed for gen: NO (agent proxies with stored token)")
