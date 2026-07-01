"""
VEO3TOP ENGINE — orchestrator tạo video hàng loạt theo cơ chế veo3top.
Pipeline (đã chứng minh chạy thật, xem VEO3TOP_MECHANISM_NOTES.md):
  WARP socks5 (fake DNS, free) + Chrome trắng đẻ recaptcha token (cùng IP WARP)
  -> POST batchAsyncGenerateVideoText -> poll -> download.
  403 = bình thường -> retry token mới; degrade -> rotate IP (registration new).

MVP: 1 account/Chrome (nhiều account = mở nhiều ChromeAccount + chạy nhiều Engine,
hoặc mở rộng phần minter pool). Token mint serial/ chrome là bottleneck -> scale Chrome.
"""
import os, time, threading, queue, uuid
from concurrent.futures import ThreadPoolExecutor

import warp
import flow_client as fc
from chrome_factory import ChromeAccount

TOKEN_TTL = 90  # giây — token recaptcha hết hạn nhanh


class Engine:
    def __init__(self, launcher, debug_port, prompts, out_dir,
                 gen_threads=8, token_buffer=24, aspect="VIDEO_ASPECT_RATIO_PORTRAIT"):
        self.acc = ChromeAccount(launcher, debug_port)
        self.prompts = list(prompts)
        self.out_dir = out_dir
        self.gen_threads = gen_threads
        self.token_buffer = token_buffer
        self.aspect = aspect
        os.makedirs(out_dir, exist_ok=True)

        self.tokens = queue.Queue()          # (token, ts)
        self.jobs = queue.Queue()            # (media_id, prompt, seed)
        self.bearer = None
        self.project = None
        self.cookie = None
        self._cdp_lock = threading.Lock()    # 1 CDP ws / chrome -> serialize
        self._ip_lock = threading.Lock()
        self._unusual = 0                    # đếm UNUSUAL liên tiếp -> trigger rotate
        self._stop = False
        self.done = []
        self.failed = []

    # ---------- setup ----------
    def start_chrome(self):
        # Token-chrome chạy DIRECT (IP nhà) như tool veo3top — token reCAPTCHA không ràng IP.
        # Generate đi qua WARP bằng curl_cffi (fingerprint chrome) — KHÔNG cần rotate IP.
        print("[engine] ensuring WARP proxy (socks5 :40000) connected...")
        ip = warp.ensure_proxy_mode()
        print(f"[engine] WARP egress IP={ip} (1 IP đủ, không rotate)")
        print("[engine] launching token-chrome DIRECT (anti-automation)...")
        assert self.acc.launch(through_warp=False), "chrome launch failed"
        assert self.acc.wait_ready(40), "grecaptcha not ready"
        with self._cdp_lock:
            self.bearer = self.acc.bearer()
            self.project = self.acc.first_project_id()
            self.cookie = fc.labs_cookie_header(self.acc.cookies())
            email = self.acc.email()
        print(f"[engine] account={email} project={self.project}")

    def refresh_bearer(self):
        with self._cdp_lock:
            self.bearer = self.acc.bearer()
            self.cookie = fc.labs_cookie_header(self.acc.cookies())


    # ---------- token factory ----------
    def minter(self):
        while not self._stop:
            if self.tokens.qsize() >= self.token_buffer:
                time.sleep(0.3); continue
            with self._cdp_lock:
                tok = self.acc.mint_token()
            if tok:
                self.tokens.put((tok, time.time()))
            else:
                time.sleep(0.5)

    def next_token(self, timeout=30):
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                tok, ts = self.tokens.get(timeout=2)
            except queue.Empty:
                continue
            if time.time() - ts < TOKEN_TTL:
                return tok
        return None

    # ---------- generate ----------
    def gen_one(self, prompt, seed):
        for attempt in range(10):
            tok = self.next_token()
            if not tok:
                continue
            payload = fc.build_payload(prompt, self.project, tok, seed, aspect=self.aspect)
            kind, data = fc.generate(self.bearer, payload)
            if kind == "ok":
                mid = (fc.operation_names(data) or [None])[0]
                return mid
            if kind == "auth":
                self.refresh_bearer(); continue
            if kind == "ip_block":
                # Với curl_cffi(chrome) gần như không xảy ra. Nếu có: WARP reconnect 1 lần.
                print("[engine] ip_block (hiếm) -> warp reconnect"); warp.ensure_proxy_mode(); continue
            if kind == "unusual":
                # token score thấp -> retry token mới (BÌNH THƯỜNG, không cần rotate IP)
                continue
            time.sleep(0.3)
        return None

    def gen_worker(self, idx):
        while not self._stop:
            try:
                prompt, seed = self.work.get_nowait()
            except queue.Empty:
                return
            mid = self.gen_one(prompt, seed)
            if mid:
                print(f"[gen] OK seed={seed} media={mid[:8]} :: {prompt[:40]}")
                self.jobs.put((mid, prompt, seed))
            else:
                print(f"[gen] FAIL seed={seed} :: {prompt[:40]}")
                self.failed.append((prompt, seed))

    # ---------- poll + download ----------
    def poll_worker(self, total):
        while len(self.done) + len(self.failed) < total and not self._stop:
            try:
                mid, prompt, seed = self.jobs.get(timeout=5)
            except queue.Empty:
                continue
            kind, res = fc.poll_until_done(self.bearer, [mid])
            if kind == "auth":
                self.refresh_bearer(); self.jobs.put((mid, prompt, seed)); continue
            if kind == "failed":
                print(f"[poll] media={mid[:8]} FAILED render")
                self.failed.append((prompt, seed)); continue
            if kind == "timeout":
                print(f"[poll] media={mid[:8]} timeout"); self.failed.append((prompt, seed)); continue
            # kind == 'done' (SUCCESSFUL) -> tải qua labs.google (cookie), DIRECT
            try:
                dst = os.path.join(self.out_dir, f"veo3_{seed}_{mid[:8]}.mp4")
                n, url = fc.media_url_and_download(mid, self.cookie, dst)
                print(f"[dl] {n} bytes -> {dst}")
                self.done.append(dst)
            except Exception as e:
                print(f"[dl] fail media={mid[:8]}: {e}")
                self.failed.append((prompt, seed))

    # ---------- run ----------
    def run(self):
        self.start_chrome()
        self.work = queue.Queue()
        base_seed = int(time.time()) % 100000
        for i, p in enumerate(self.prompts):
            self.work.put((p, base_seed + i))
        total = len(self.prompts)

        threading.Thread(target=self.minter, daemon=True).start()
        # chờ pool có token
        while self.tokens.qsize() < min(4, self.token_buffer):
            time.sleep(0.5)

        poller = threading.Thread(target=self.poll_worker, args=(total,), daemon=True)
        poller.start()

        t0 = time.time()
        with ThreadPoolExecutor(max_workers=self.gen_threads) as ex:
            for i in range(self.gen_threads):
                ex.submit(self.gen_worker, i)
        # đợi poll/download xong
        while len(self.done) + len(self.failed) < total and time.time() - t0 < 1800:
            time.sleep(2)
        self._stop = True
        dt = time.time() - t0
        print(f"\n=== DONE: {len(self.done)} videos, {len(self.failed)} failed in {dt:.0f}s "
              f"(~{round(len(self.done)/dt*3600)} video/h) ===")
        return self.done


if __name__ == "__main__":
    import sys
    launcher = sys.argv[1] if len(sys.argv) > 1 else r"D:\VE3_SUITE\GoogleChromePortable - Copy (5)\GoogleChromePortable.exe"
    port = int(sys.argv[2]) if len(sys.argv) > 2 else 9223
    n = int(sys.argv[3]) if len(sys.argv) > 3 else 5
    prompts = [f"cinematic 8k shot, a {a} in a {b}, dramatic lighting"
               for a, b in [("lion","desert storm"),("astronaut","alien jungle"),
                            ("samurai","neon city rain"),("dragon","snowy mountain"),
                            ("dancer","golden wheat field"),("wolf","misty forest"),
                            ("surfer","giant ocean wave"),("knight","castle courtyard")]][:n]
    eng = Engine(launcher, port, prompts, r"D:\VE3_SUITE\veo3top_engine\out", gen_threads=4)
    eng.run()
