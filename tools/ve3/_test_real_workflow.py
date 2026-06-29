"""Test real VE3 workflow: simulate what actually happens when tool runs.

Scenario: 3 workers on 3 different servers (like real queue dispatch).
Each worker: start_one → get token → get project → upload → release Chrome.
Then: simulate 401 → reopen → get new token → release.
"""
import sys, time, subprocess, threading, os, struct, zlib
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
sys.path.insert(0, r'd:\VE3_SUITE\server\flowkit')
from pathlib import Path
from modules.flow_extension_auth import _ExtensionInstanceManager, FlowExtensionAuth
import httpx

servers = [
    {'name': 'sv1', 'index': 0, 'chrome_path': r'D:\VE3_SUITE\GoogleChromePortable - Copy (1)\GoogleChromePortable.exe',
     'flow_account_bundle': 'dendalion41@gmail.com|Damuocmo212@|ag7b 35ne sdsx na3z xgkj kydl 3bmx 3fir'},
    {'name': 'sv2', 'index': 1, 'chrome_path': r'D:\VE3_SUITE\GoogleChromePortable - Copy (2)\GoogleChromePortable.exe',
     'flow_account_bundle': 'lua789001@gmail.com|ZKGdMeBOVTEexf|dcw3 v7ux 747r ibfg t6do y4fk j4gv zsrr'},
]

results = {}
lock = threading.Lock()

def log(msg):
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)

def make_png():
    raw = b''.join(b'\x00' + bytes([100,150,200])*50 for _ in range(50))
    def chunk(ct, d):
        c = ct+d; return struct.pack('>I',len(d))+c+struct.pack('>I',zlib.crc32(c)&0xffffffff)
    return b'\x89PNG\r\n\x1a\n'+chunk(b'IHDR',struct.pack('>IIBBBBB',50,50,8,2,0,0,0))+chunk(b'IDAT',zlib.compress(raw))+chunk(b'IEND',b'')

def simulate_worker(srv, worker_name):
    """Simulate what a real VE3 worker does."""
    idx = srv['index']
    name = srv['name']
    port = 8100 + idx
    worker_results = {}

    try:
        log(f"[{worker_name}] Starting on {name} (port {port})")

        # Step 1: start_one (on-demand)
        ok = _ExtensionInstanceManager.start_one(idx, srv, r'D:\VE3_SUITE',
            log=lambda m: log(f"[{worker_name}] {m}"))
        worker_results['start'] = ok
        if not ok:
            log(f"[{worker_name}] start_one FAILED")
            return worker_results

        # Step 2: get token
        auth = FlowExtensionAuth(f'http://127.0.0.1:{port}', log_func=lambda m: log(f"[{worker_name}] {m}"))
        token = auth.get_token()
        worker_results['token'] = bool(token)
        if not token:
            log(f"[{worker_name}] get_token FAILED")
            return worker_results

        # Step 3: get/create project
        pid = auth.get_project_id()
        if not pid:
            pid = auth.ensure_project()
        worker_results['project'] = bool(pid)

        # Step 4: upload reference image
        if token and pid:
            img = Path(f'd:\\VE3_SUITE\\tools\\ve3\\_test_{worker_name}.png')
            img.write_bytes(make_png())
            media = auth.upload_image(str(img), token, pid)
            img.unlink(missing_ok=True)
            worker_results['upload'] = bool(media)
            log(f"[{worker_name}] Auth done: token=OK, project={pid[:8]}..., media={'OK' if media else 'FAIL'}")

        # Step 5: release Chrome (Phase 1 done)
        _ExtensionInstanceManager.release_chrome(name, log=lambda m: log(f"[{worker_name}] {m}"))
        worker_results['release'] = True

        # Step 6: verify token still available (simulate Phase 2-4 generation)
        t = httpx.get(f'http://127.0.0.1:{port}/api/get-token', timeout=5).json()
        worker_results['token_after_release'] = t.get('success', False)
        log(f"[{worker_name}] Token after release: {'OK' if t.get('success') else 'FAIL'}")

    except Exception as e:
        log(f"[{worker_name}] ERROR: {e}")
        worker_results['error'] = str(e)

    with lock:
        results[worker_name] = worker_results
    return worker_results


log("=" * 60)
log("REAL WORKFLOW TEST — Simulate actual VE3 tool operation")
log("=" * 60)

# ═══ Phase A: 2 workers on DIFFERENT servers (normal operation) ═══
log("\n=== Phase A: 2 workers on different servers (parallel) ===")
t0 = time.time()
threads = []
for i, srv in enumerate(servers):
    t = threading.Thread(target=simulate_worker, args=(srv, f"W{i+1}"))
    t.start()
    threads.append(t)
for t in threads:
    t.join(timeout=180)
elapsed_a = time.time() - t0
log(f"\nPhase A done in {elapsed_a:.0f}s")

# ═══ Phase B: Simulate token expired (401) → reopen Chrome ═══
log("\n=== Phase B: Token expired → reopen Chrome → fresh token ===")
for srv in servers:
    name = srv['name']
    port = 8100 + srv['index']
    log(f"\n[{name}] Simulating 401...")
    t0 = time.time()
    ok = _ExtensionInstanceManager.reopen_chrome_for_instance(name, r'D:\VE3_SUITE',
        log=lambda m, n=name: log(f"[{n}] {m}"))
    elapsed = time.time() - t0
    if ok:
        t = httpx.get(f'http://127.0.0.1:{port}/api/get-token', timeout=5).json()
        token_ok = t.get('success', False)
        results[f'{name}_reopen'] = ok and token_ok
        log(f"[{name}] Reopen: OK ({elapsed:.0f}s), token: {'OK' if token_ok else 'FAIL'}")
        _ExtensionInstanceManager.release_chrome(name, log=lambda m, n=name: log(f"[{n}] {m}"))
    else:
        results[f'{name}_reopen'] = False
        log(f"[{name}] Reopen: FAIL")

# ═══ Phase C: Second project on same server (token from RAM) ═══
log("\n=== Phase C: Second project on same server (token from agent RAM) ===")
srv = servers[0]
port = 8100
t = httpx.get(f'http://127.0.0.1:{port}/api/get-token', timeout=5).json()
results['second_project_token'] = t.get('success', False)
log(f"sv1 token (no Chrome): {'OK' if t.get('success') else 'FAIL'}")

# If token still valid, worker can proceed without Chrome
# Only need Chrome again for ensure_project (new project)
log("sv1 needs new project → reopen Chrome...")
ok = _ExtensionInstanceManager.reopen_chrome_for_instance('sv1', r'D:\VE3_SUITE',
    log=lambda m: log(f"[sv1] {m}"))
if ok:
    auth = FlowExtensionAuth(f'http://127.0.0.1:{port}', log_func=log)
    pid = auth.ensure_project()
    results['second_project'] = bool(pid)
    log(f"New project: {pid[:8] if pid else 'FAIL'}...")
    _ExtensionInstanceManager.release_chrome('sv1', log=log)

# ═══ Phase D: Verify cleanup — no Chrome, agents alive ═══
log("\n=== Phase D: Final state check ===")
chrome_count = 0
try:
    r = subprocess.run(['tasklist', '/FI', 'IMAGENAME eq chrome.exe', '/FO', 'CSV'],
        capture_output=True, text=True, timeout=5)
    chrome_count = sum(1 for l in r.stdout.splitlines() if 'chrome' in l.lower())
except Exception:
    pass
log(f"Chrome processes: {chrome_count}")
results['no_chrome'] = chrome_count == 0

for srv in servers:
    port = 8100 + srv['index']
    alive = _ExtensionInstanceManager._is_agent_alive(port)
    log(f"Agent {srv['name']} (port {port}): {'alive' if alive else 'dead'}")
    results[f"agent_{srv['name']}"] = alive

# ═══ SUMMARY ═══
log("\n" + "=" * 60)
log("SUMMARY")
log("=" * 60)
all_pass = True
for k, v in sorted(results.items()):
    if isinstance(v, dict):
        sub_pass = all(v.values())
        status = 'PASS' if sub_pass else 'FAIL'
        if not sub_pass:
            all_pass = False
        log(f"  [{status}] {k}: {v}")
    else:
        status = 'PASS' if v else 'FAIL'
        if not v:
            all_pass = False
        log(f"  [{status}] {k}")

log(f"\n{'ALL PASSED' if all_pass else 'SOME FAILED'}")
