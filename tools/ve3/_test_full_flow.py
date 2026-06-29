"""Full E2E: start_all (sv1) → token → upload → gen image."""
import sys, time, random, struct, zlib
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

# ── Step 1: start_all ──
log("=" * 50)
log("FULL E2E TEST — Extension Auth")
log("=" * 50)
FlowExtensionAuth.start_all_instances(servers, r'D:\VE3_SUITE', log_func=log)

# ── Step 2: Check health ──
log("\n--- Health ---")
try:
    h = httpx.get('http://127.0.0.1:8100/health', timeout=5).json()
    connected = h.get('extension_connected', False)
    has_token = h.get('flow_key_present', False)
    log(f"connected={connected}, token={has_token}")
    results['health'] = connected and has_token
except Exception as e:
    log(f"FAIL: {e}")
    results['health'] = False

# ── Step 3: Get token ──
log("\n--- Token ---")
auth = FlowExtensionAuth('http://127.0.0.1:8100', log_func=log)
token = auth.get_token()
results['token'] = bool(token)
if token:
    log(f"Token: {token[:40]}... ({len(token)} chars)")
else:
    log("Token: FAIL")

# ── Step 4: Get/ensure project ──
log("\n--- Project ---")
project_id = auth.get_project_id()
if not project_id:
    project_id = auth.ensure_project()
results['project'] = bool(project_id)
if project_id:
    log(f"Project: {project_id}")
else:
    log("Project: FAIL")

# ── Step 5: Upload image ──
log("\n--- Upload ---")
if token and project_id:
    def make_png(w, h, r, g, b):
        raw = b''
        for _ in range(h):
            raw += b'\x00' + bytes([r, g, b]) * w
        def chunk(ct, data):
            c = ct + data
            return struct.pack('>I', len(data)) + c + struct.pack('>I', zlib.crc32(c) & 0xffffffff)
        return b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('>IIBBBBB', w, h, 8, 2, 0, 0, 0)) + chunk(b'IDAT', zlib.compress(raw)) + chunk(b'IEND', b'')

    test_img = Path(r'd:\VE3_SUITE\tools\ve3\_test_ref.png')
    test_img.write_bytes(make_png(200, 200, 150, 100, 80))
    media_id = auth.upload_image(str(test_img), token, project_id)
    results['upload'] = bool(media_id)
    if media_id:
        log(f"Media: {media_id}")
    else:
        log("Upload: FAIL")
    test_img.unlink(missing_ok=True)
else:
    results['upload'] = False
    log("Skip — no token/project")

# ── Step 6: Generate image ──
log("\n--- Generate Image ---")
if token and project_id:
    session = f";{int(time.time()*1000)}"
    body = {
        "bearer_token": token,
        "project_id": project_id,
        "flow_url": f"https://aisandbox-pa.googleapis.com/v1/projects/{project_id}/flowMedia:batchGenerateImages",
        "body_json": {
            "clientContext": {"sessionId": session, "projectId": project_id, "tool": "PINHOLE"},
            "requests": [{
                "clientContext": {"sessionId": session, "projectId": project_id, "tool": "PINHOLE"},
                "seed": random.randint(100000, 999999),
                "imageModelName": "NARWHAL",
                "imageAspectRatio": "IMAGE_ASPECT_RATIO_LANDSCAPE",
                "prompt": "A golden meadow with wildflowers, warm afternoon sunlight",
                "imageInputs": []
            }]
        }
    }
    try:
        start = time.time()
        resp = httpx.post('http://127.0.0.1:8100/api/generate-image', json=body, timeout=120)
        elapsed = time.time() - start
        r = resp.json()
        if r.get("success"):
            log(f"Image gen: OK ({elapsed:.0f}s)")
            results['gen_image'] = True
        else:
            log(f"Image gen: FAIL — {r.get('error', '?')[:80]}")
            results['gen_image'] = False
    except Exception as e:
        log(f"Image gen error: {e}")
        results['gen_image'] = False
else:
    results['gen_image'] = False

# ── Summary ──
log("\n" + "=" * 50)
log("RESULTS")
log("=" * 50)
all_pass = True
for name, ok in results.items():
    status = "PASS" if ok else "FAIL"
    if not ok:
        all_pass = False
    log(f"  [{status}] {name}")
log(f"\n{'ALL PASSED' if all_pass else 'SOME FAILED'}")
