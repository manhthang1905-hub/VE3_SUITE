"""Full test: start → token → upload → gen → force refresh → token again."""
import sys, time, struct, zlib, random
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
sys.path.insert(0, r'd:\VE3_SUITE\server\flowkit')
from pathlib import Path
from modules.flow_extension_auth import FlowExtensionAuth
import httpx

servers = [
    {
        'name': 'sv1',
        'chrome_path': r'D:\VE3_SUITE\GoogleChromePortable - Copy (1)\GoogleChromePortable.exe',
        'flow_account_bundle': 'dendalion41@gmail.com|Damuocmo212@|ag7b 35ne sdsx na3z xgkj kydl 3bmx 3fir',
    },
]
results = {}

def log(msg):
    ts = time.strftime("%H:%M:%S")
    print(f"[{ts}] {msg}")
    sys.stdout.flush()

log("=" * 50)
log("TEST: start + token + upload + gen + REFRESH")
log("=" * 50)

# Step 1: Start
FlowExtensionAuth.start_all_instances(servers, r'D:\VE3_SUITE', log_func=log)
auth = FlowExtensionAuth('http://127.0.0.1:8100', log_func=log)

# Step 2: Token
token = auth.get_token()
results['token'] = bool(token)
log(f"Token: {'OK' if token else 'FAIL'} ({len(token) if token else 0} chars)")

# Step 3: Project
pid = auth.get_project_id()
if not pid:
    pid = auth.ensure_project()
results['project'] = bool(pid)
log(f"Project: {pid if pid else 'FAIL'}")

# Step 4: Upload
if token and pid:
    def make_png(w, h):
        raw = b''.join(b'\x00' + bytes([100,150,200]) * w for _ in range(h))
        def chunk(ct, d):
            c = ct + d
            return struct.pack('>I', len(d)) + c + struct.pack('>I', zlib.crc32(c) & 0xffffffff)
        return b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('>IIBBBBB', w, h, 8, 2, 0, 0, 0)) + chunk(b'IDAT', zlib.compress(raw)) + chunk(b'IEND', b'')
    img = Path(r'd:\VE3_SUITE\tools\ve3\_test_ref.png')
    img.write_bytes(make_png(100, 100))
    media = auth.upload_image(str(img), token, pid)
    results['upload'] = bool(media)
    log(f"Upload: {media if media else 'FAIL'}")
    img.unlink(missing_ok=True)

# Step 5: Generate
if token and pid:
    body = {
        "bearer_token": token, "project_id": pid,
        "flow_url": f"https://aisandbox-pa.googleapis.com/v1/projects/{pid}/flowMedia:batchGenerateImages",
        "body_json": {"clientContext": {"sessionId": f";{int(time.time()*1000)}", "projectId": pid, "tool": "PINHOLE"},
            "requests": [{"clientContext": {"sessionId": f";{int(time.time()*1000)}", "projectId": pid, "tool": "PINHOLE"},
                "seed": random.randint(100000, 999999), "imageModelName": "NARWHAL",
                "imageAspectRatio": "IMAGE_ASPECT_RATIO_LANDSCAPE",
                "prompt": "A calm ocean sunset with golden light", "imageInputs": []}]}
    }
    try:
        r = httpx.post('http://127.0.0.1:8100/api/generate-image', json=body, timeout=120).json()
        results['gen'] = r.get('success', False)
        log(f"Gen: {'OK' if r.get('success') else 'FAIL - ' + r.get('error','?')[:60]}")
    except Exception as e:
        results['gen'] = False
        log(f"Gen: ERROR {e}")

# Step 6: FORCE REFRESH (simulate token expired → re-setup Chrome)
log("\n--- FORCE REFRESH (simulate 401) ---")
from modules.flow_extension_auth import _ExtensionInstanceManager
chrome_dir = Path(r'D:\VE3_SUITE\GoogleChromePortable - Copy (1)')
log("Killing Chrome (simulate expired)...")
_ExtensionInstanceManager._kill_chrome_for_dir(chrome_dir)
time.sleep(3)

# Re-run start_all (same as force_refresh flow)
log("Re-starting (y het luc dau)...")
FlowExtensionAuth.start_all_instances(servers, r'D:\VE3_SUITE', log_func=log)

# Get fresh token
token2 = auth.get_token()
results['refresh'] = bool(token2)
if token2:
    same = token2 == token
    log(f"Refresh token: OK ({'same' if same else 'NEW'} - {len(token2)} chars)")
else:
    log("Refresh token: FAIL")

# Summary
log("\n" + "=" * 50)
all_pass = all(results.values())
for k, v in results.items():
    log(f"  [{'PASS' if v else 'FAIL'}] {k}")
log(f"\n{'ALL PASSED' if all_pass else 'SOME FAILED'}")
