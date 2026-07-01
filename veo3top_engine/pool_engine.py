"""
POOL ENGINE — đa-account rotation (đúng cách veo3top dùng 21 account ultra).
Đã kiểm chứng (xem VEO3TOP_MECHANISM_NOTES.md):
- Cơ chế: token blank-chrome (anti-automation) + generate curl_cffi(chrome) qua WARP 1 IP.
- Giới hạn = per-account throttle (~5-15 req/account) -> rotate account.
- IP KHÔNG cần xoay; project=None vẫn generate được.

Kiến trúc:
- N account-worker chạy song song (mặc định 2-3 chrome cùng lúc cho nhẹ máy).
  Mỗi worker: mở chrome account (anti-automation, DIRECT) -> mint token + generate prompt
  từ hàng đợi -> nếu K fail liên tiếp = account throttle -> đóng chrome, lấy account kế.
- Pool poll/download riêng: nhận media_id -> poll SUCCESSFUL -> tải mp4.
"""
import os, glob, time, threading, queue, re, sys
from concurrent.futures import ThreadPoolExecutor

import warp
import flow_client as fc
from chrome_factory import ChromeAccount

THROTTLE_K = 4          # số fail liên tiếp -> coi account throttled, rotate
PER_ACCOUNT_MAX = 12    # tối đa video/account 1 lượt trước khi chủ động rotate (tránh burn sâu)


def discover_accounts(base=r"D:\VE3_SUITE"):
    accs = []
    for p in sorted(glob.glob(os.path.join(base, "GoogleChromePortable - Copy (*)", "GoogleChromePortable.exe"))):
        m = re.search(r"Copy \((\d+)\)", p)
        n = int(m.group(1)) if m else 0
        accs.append((n, p))
    accs.sort()
    return [p for _, p in accs]


class PoolEngine:
    def __init__(self, prompts, out_dir, account_launchers,
                 concurrency=2, base_port=9300, poll_threads=4, aspect="VIDEO_ASPECT_RATIO_PORTRAIT"):
        self.prompts = queue.Queue()
        for i, p in enumerate(prompts):
            self.prompts.put((i, p))
        self.total = len(prompts)
        self.out_dir = out_dir; os.makedirs(out_dir, exist_ok=True)
        self.accounts = queue.Queue()
        for a in account_launchers:
            self.accounts.put(a)
        self.concurrency = concurrency
        self.base_port = base_port
        self.poll_threads = poll_threads
        self.aspect = aspect
        self.jobs = queue.Queue()          # (media_id, idx, prompt, cookie)
        self.done = []; self.failed = []
        self._lock = threading.Lock()
        self._stop = False
        self._submitted = 0

    def _seed(self, idx):
        return (int(time.time()) * 100 + idx) % 2_000_000

    # ---- 1 account worker ----
    def account_worker(self, port):
        while not self._stop:
            # Hết prompt để submit -> không mở thêm account (tránh phí chrome)
            if self.prompts.empty() or self._submitted >= self.total:
                return
            try:
                launcher = self.accounts.get_nowait()
            except queue.Empty:
                return
            label = re.search(r"Copy \((\d+)\)", launcher)
            label = label.group(1) if label else "?"
            acc = ChromeAccount(launcher, port)
            try:
                if not acc.launch(through_warp=False) or not acc.wait_ready(40):
                    print(f"[acc{label}] chrome/grecaptcha fail -> skip"); continue
                bearer = acc.bearer()
                project = acc.first_project_id()
                cookie = fc.labs_cookie_header(acc.cookies())
                email = acc.email()
                print(f"[acc{label}] {email} project={project} -> producing")
            except Exception as e:
                print(f"[acc{label}] setup err {e}"); continue

            consec = 0; made = 0
            while not self._stop and made < PER_ACCOUNT_MAX:
                if self.prompts.empty():
                    break
                # mỗi video: thử tới 8 token mới
                got = False
                for _ in range(8):
                    if self.prompts.empty():
                        break
                    try:
                        idx, prompt = self.prompts.get_nowait()
                    except queue.Empty:
                        break
                    tok = acc.mint_token()
                    if not tok:
                        self.prompts.put((idx, prompt)); time.sleep(0.5); continue
                    payload = fc.build_payload(prompt, project, tok, self._seed(idx), aspect=self.aspect)
                    kind, data = fc.generate(bearer, payload)
                    if kind == "ok":
                        mid = (fc.operation_names(data) or [None])[0]
                        if mid:
                            self.jobs.put((mid, idx, prompt, cookie))
                            with self._lock: self._submitted += 1
                            print(f"[acc{label}] gen OK #{idx} media={mid[:8]} ({self._submitted}/{self.total})")
                        consec = 0; made += 1; got = True
                        break
                    elif kind == "auth":
                        bearer = acc.bearer(); self.prompts.put((idx, prompt)); continue
                    else:  # unusual / ip_block / other -> retry token mới, trả prompt lại
                        self.prompts.put((idx, prompt)); consec += 1
                        if consec >= THROTTLE_K:
                            break
                if consec >= THROTTLE_K or not got:
                    print(f"[acc{label}] throttled (consec={consec}) -> rotate account"); break
            try: acc.close()
            except Exception: pass

    # ---- poll + download pool ----
    def poll_worker(self):
        while not self._stop:
            if len(self.done) + len(self.failed) >= self.total:
                return
            try:
                mid, idx, prompt, cookie = self.jobs.get(timeout=5)
            except queue.Empty:
                if self._submitted >= self.total and self.jobs.empty():
                    return
                continue
            # cần bearer để poll; dùng cookie->không, poll cần Bearer. Lấy lại bearer từ...
            # poll dùng curl_cffi + key, KHÔNG cần Bearer hợp lệ của đúng account? -> cần Bearer.
            # Giải: poll bằng cookie-account qua media.getMediaUrlRedirect (status ngầm khi có URL).
            dst = os.path.join(self.out_dir, f"veo3_{idx}_{mid[:8]}.mp4")
            ok = False
            for attempt in range(80):
                try:
                    n, url = fc.media_url_and_download(mid, cookie, dst)
                    print(f"[dl] #{idx} {n} bytes -> {os.path.basename(dst)}")
                    with self._lock: self.done.append(dst)
                    ok = True; break
                except Exception:
                    time.sleep(6)   # chưa render xong -> chờ
            if not ok:
                with self._lock: self.failed.append((idx, prompt))
                print(f"[dl] #{idx} media={mid[:8]} timeout/fail")

    # ---- run ----
    def run(self):
        print(f"[pool] WARP: {warp.ensure_proxy_mode()} | accounts={self.accounts.qsize()} total={self.total}")
        pollers = [threading.Thread(target=self.poll_worker, daemon=True) for _ in range(self.poll_threads)]
        for p in pollers: p.start()
        t0 = time.time()
        with ThreadPoolExecutor(max_workers=self.concurrency) as ex:
            for w in range(self.concurrency):
                ex.submit(self.account_worker, self.base_port + w)
        # đợi poll/download xong
        while len(self.done) + len(self.failed) < self.total and time.time() - t0 < 3600:
            if self.prompts.empty() and self.jobs.empty() and self._submitted <= len(self.done) + len(self.failed):
                break
            time.sleep(3)
        self._stop = True
        dt = time.time() - t0
        print(f"\n=== POOL DONE: {len(self.done)} videos, {len(self.failed)} failed, "
              f"{self.total - len(self.done) - len(self.failed)} unsubmitted in {dt:.0f}s "
              f"(~{round(len(self.done)/dt*3600) if dt else 0} video/h) ===")
        return self.done


def _download_via_redirect_uses_status():
    """Ghi chú: media.getMediaUrlRedirect chỉ trả mp4 khi render XONG; nếu chưa xong trả !=video ->
    raise -> ta retry. Vậy nó kiêm luôn 'poll' mà không cần Bearer. (đã xác nhận status SUCCESSFUL mới có URL)."""


if __name__ == "__main__":
    n_videos = int(sys.argv[1]) if len(sys.argv) > 1 else 6
    conc = int(sys.argv[2]) if len(sys.argv) > 2 else 2
    base = ["a lion in a desert storm", "an astronaut in an alien jungle", "a samurai in neon city rain",
            "a dragon over snowy mountains", "a dancer in a golden wheat field", "a wolf in a misty forest",
            "a surfer on a giant ocean wave", "a knight in a castle courtyard", "a tiger in a bamboo forest",
            "a falcon over red canyons", "a fox in autumn leaves", "a whale under moonlit ocean"]
    prompts = [f"cinematic 8k shot, {base[i % len(base)]}, dramatic lighting" for i in range(n_videos)]
    accs = discover_accounts()
    # bỏ các account đã burn trong test (10=antonio). Ưu tiên account chưa đụng.
    print("accounts found:", len(accs))
    eng = PoolEngine(prompts, r"D:\VE3_SUITE\veo3top_engine\out", accs, concurrency=conc)
    eng.run()
