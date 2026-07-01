"""
Veo3topProvider — tích hợp phương pháp tạo video veo3top (đã khám phá + kiểm chứng) vào FlowKit.
Dùng cho 1 project / 1 account: tái dùng chrome account (đã login, mà tool mở để upload ảnh)
để lấy bearer + cookie + mint recaptcha token; generate I2V bằng curl_cffi(chrome)+WARP; download.

KHÔNG đụng luồng cũ — chỉ chạy khi generation_backend == "veo3top".
Cơ chế (xem ../VEO3TOP_MECHANISM_NOTES.md): curl_cffi impersonate=chrome (diệt "Sorry"),
token chrome anti-automation (score cao), WARP 1 IP, 403 UNUSUAL = retry token mới.
"""
import os, sys, time, json, itertools, subprocess, threading
import requests, websocket

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)
import warp
import flow_client as fc

SITE_KEY = "6LdsFiUsAAAAAIjVDZcuLhaHiDn5nnHVXVRQGeMV"
ACTION = "VIDEO_GENERATION"


class Veo3topProvider:
    def __init__(self, chrome_exe, profile_dir, debug_port=9850,
                 video_aspect="VIDEO_ASPECT_RATIO_LANDSCAPE", log=print):
        self.chrome_exe = chrome_exe
        self.profile_dir = profile_dir
        self.port = debug_port
        self.video_aspect = video_aspect
        self.log = log
        self.ws = None
        self._id = itertools.count(1)
        self.bearer = None
        self.cookie = None
        self.project = None
        self._proc = None
        self._ws_lock = threading.Lock()   # serialize CDP calls khi nhiều luồng

    # ---------- chrome (connect existing or launch) ----------
    def _page_ws(self):
        try:
            for t in requests.get(f"http://localhost:{self.port}/json", timeout=3).json():
                if t.get("type") == "page" and "labs.google" in (t.get("url") or ""):
                    return t["webSocketDebuggerUrl"]
        except Exception:
            return None
        return None

    def _launch(self):
        args = [self.chrome_exe,
                f"--remote-debugging-port={self.port}", "--remote-allow-origins=*",
                f"--user-data-dir={self.profile_dir}",
                "--disable-blink-features=AutomationControlled",
                "--no-first-run", "--no-default-browser-check", "--mute-audio",
                "--disable-background-networking", "--disable-sync",
                "--window-size=320,480", "--window-position=-30000,0",
                "https://labs.google/fx/tools/flow"]
        self._proc = subprocess.Popen(args)

    def start(self):
        """Ensure WARP + chrome (reuse if open) + auth. Trả True nếu sẵn sàng."""
        ip = warp.ensure_proxy_mode()
        self.log(f"[veo3top] WARP egress={ip}")
        ws_url = self._page_ws()
        if not ws_url:
            self.log(f"[veo3top] launch account chrome (port {self.port})...")
            self._launch()
            for _ in range(40):
                ws_url = self._page_ws()
                if ws_url:
                    break
                time.sleep(1)
        if not ws_url:
            self.log("[veo3top] chrome/labs.google not available", "ERROR") if self.log else None
            return False
        self.ws = websocket.create_connection(ws_url, max_size=None)
        self._cmd("Runtime.enable")
        if not self._wait_grecaptcha(30):
            self.log("[veo3top] grecaptcha not ready")
            return False
        return self._refresh_auth()

    # ---------- CDP ----------
    def _reconnect(self):
        """ws rớt (chrome recycle/offscreen) -> nối lại; launch lại nếu cần."""
        for _ in range(20):
            ws_url = self._page_ws()
            if not ws_url:
                if not self._proc or self._proc.poll() is not None:
                    self._launch()
                time.sleep(2); continue
            try:
                self.ws = websocket.create_connection(ws_url, max_size=None)
                self._cmd_raw("Runtime.enable"); self._cmd_raw("Network.enable")
                self._wait_grecaptcha(20)
                return True
            except Exception:
                time.sleep(1)
        return False

    def _cmd_raw(self, method, **params):
        i = next(self._id)
        self.ws.send(json.dumps({"id": i, "method": method, "params": params}))
        while True:
            m = json.loads(self.ws.recv())
            if m.get("id") == i:
                return m

    def _cmd(self, method, **params):
        # 1 CDP websocket dung chung -> serialize moi call (nhieu luong generate goi _mint/_refresh_auth).
        with self._ws_lock:
            try:
                return self._cmd_raw(method, **params)
            except (ConnectionResetError, websocket.WebSocketConnectionClosedException, OSError, BrokenPipeError):
                if self._reconnect():
                    return self._cmd_raw(method, **params)
                raise

    def _eval(self, expr, to=60000):
        r = self._cmd("Runtime.evaluate", expression=expr, awaitPromise=True,
                      returnByValue=True, userGesture=True, timeout=to)
        return r.get("result", {}).get("result", {}).get("value")

    def _wait_grecaptcha(self, timeout=30):
        end = time.time() + timeout
        while time.time() < end:
            if self._eval("!!(window.grecaptcha&&window.grecaptcha.enterprise&&window.grecaptcha.enterprise.execute)"):
                return True
            time.sleep(1)
        return False

    def _refresh_auth(self):
        self.bearer = self._eval("(async()=>{const j=await (await fetch('/fx/api/auth/session',{credentials:'include'})).json();return j.access_token;})()")
        self._cmd("Network.enable")
        cks = self._cmd("Network.getAllCookies").get("result", {}).get("cookies", [])
        self.cookie = fc.labs_cookie_header(cks)
        if not self.project:
            self.project = self._eval(
                r"(async()=>{const u='/fx/api/trpc/project.searchUserProjects?input='+encodeURIComponent(JSON.stringify({json:{pageSize:5,toolName:'PINHOLE',cursor:null},meta:{values:{cursor:['undefined']}}}));const j=await (await fetch(u,{credentials:'include'})).json();try{return j.result.data.json.result.projects[0].projectId;}catch(e){return null;}})()")
        return bool(self.bearer)

    def _mint(self):
        return self._eval(f"(async()=>{{try{{return await window.grecaptcha.enterprise.execute('{SITE_KEY}',{{action:'{ACTION}'}});}}catch(e){{return null;}}}})()")

    # ---------- main API (gọi từ worker thay cho server) ----------
    # 1 Excel = 1 account. Throttle sau ~40 video -> KHÔNG hammer 1 scene mãi (gây kẹt).
    # Thay vào: nghỉ tăng dần (escalating) + giới hạn thời gian/scene -> fail nhanh, sang scene khác.
    # Khi account throttle sâu (nhiều scene fail liên tiếp) -> nghỉ account DÀI cho hồi.
    MAX_ATTEMPTS = 60
    COOLDOWN_EVERY = 6          # cứ N UNUSUAL liên tiếp thì nghỉ (nghỉ tăng dần)
    COOLDOWN_BASE = 20          # nghỉ cơ bản, x1.5 mỗi vòng, cap 90s
    RETRY_DELAY = 1.2
    SCENE_BUDGET_SEC = 300      # ngân sách chờ-throttle tối đa/scene (5') -> quá thì fail, sang scene khác
    DEEP_THROTTLE_FAILS = 3     # số scene fail-throttle liên tiếp -> nghỉ account dài
    ACCOUNT_REST_SEC = 180      # nghỉ account khi throttle sâu (cho rate-limit hồi)

    def submit_video(self, prompt, media_id, out_path, seed=None, max_attempts=None, poll_max=48):
        """I2V: media_id đã upload sẵn (trong Excel). Trả (success, info_dict, error_text) — KHỚP _submit_video.
        Retry escalating + budget/scene + nghỉ account khi throttle sâu (tránh kẹt như log 47')."""
        if seed is None:
            seed = int(time.time() * 1000) % 2_000_000
        if not self.project:
            return False, {}, "veo3top: no project_id"
        # nghỉ account dài NẾU scene trước đã throttle sâu (cho account hồi trước khi thử scene mới)
        if getattr(self, "_consec_scene_fail", 0) >= self.DEEP_THROTTLE_FAILS:
            self.log(f"[veo3top] account throttle sâu ({self._consec_scene_fail} scene fail) -> nghỉ {self.ACCOUNT_REST_SEC}s cho hồi")
            time.sleep(self.ACCOUNT_REST_SEC)
            self._consec_scene_fail = 0
        t_scene = time.time()
        attempts = max_attempts or self.MAX_ATTEMPTS
        last = ""; consec_unusual = 0
        for attempt in range(attempts):
            tok = self._mint()
            if not tok:
                time.sleep(0.6); last = "no token"; continue
            payload = fc.build_payload(prompt, self.project, tok, seed,
                                       aspect=self.video_aspect, reference_media_id=media_id)
            kind, data = fc.generate(self.bearer, payload, url=fc.GEN_I2V)
            if kind == "ok":
                consec_unusual = 0
                mid = (fc.operation_names(data) or [None])[0]
                if not mid:
                    last = "200 but no media id"; continue
                os.makedirs(os.path.dirname(out_path), exist_ok=True)
                # 1) poll status bằng batchCheck (bearer) -> biết SUCCESSFUL/FAILED nhanh
                pkind, _ = fc.poll_until_done(self.bearer, [mid], max_attempts=poll_max, interval=5)
                if pkind == "failed":
                    last = "render FAILED (policy/transient)"; continue   # retry token/prompt
                if pkind == "auth":
                    self._refresh_auth()
                if pkind != "done":
                    self._consec_scene_fail = getattr(self, "_consec_scene_fail", 0) + 1
                    return False, {"media_id": mid}, "veo3top: render timeout"
                # 2) tải bằng cookie account (getMediaUrlRedirect)
                for _ in range(12):
                    try:
                        n, url = fc.media_url_and_download(mid, self.cookie, str(out_path))
                        self._consec_scene_fail = 0   # thành công -> reset đếm throttle
                        return True, {"media_id": mid, "bytes": n}, ""
                    except Exception:
                        time.sleep(5)
                return False, {"media_id": mid}, "veo3top: downloaded URL not ready"
            elif kind == "auth":
                self._refresh_auth(); last = "auth refresh"; continue
            elif kind == "unusual":
                # throttle tạm thời -> retry token mới; nghỉ TĂNG DẦN; hết budget/scene -> bỏ scene này
                consec_unusual += 1
                last = "UNUSUAL (token score/throttle)"
                if time.time() - t_scene > self.SCENE_BUDGET_SEC:
                    self._consec_scene_fail = getattr(self, "_consec_scene_fail", 0) + 1
                    return False, {}, f"veo3top: scene bỏ sau {int(time.time()-t_scene)}s throttle (sẽ chạy lại lượt sau)"
                if consec_unusual % self.COOLDOWN_EVERY == 0:
                    rounds = consec_unusual // self.COOLDOWN_EVERY
                    nap = min(90, int(self.COOLDOWN_BASE * (1.5 ** (rounds - 1))))
                    self.log(f"[veo3top] {consec_unusual} UNUSUAL -> nghỉ {nap}s (attempt {attempt+1}/{attempts})")
                    self._refresh_auth()
                    time.sleep(nap)
                else:
                    time.sleep(self.RETRY_DELAY)
                continue
            elif kind == "ip_block":
                warp.ensure_proxy_mode(); last = "ip_block (sorry)"; time.sleep(self.RETRY_DELAY); continue
            else:
                last = f"other: {data}"; time.sleep(0.5)
        self._consec_scene_fail = getattr(self, "_consec_scene_fail", 0) + 1
        return False, {}, f"veo3top: exhausted after {attempts} ({last}) — account throttle/captcha"

    def close(self):
        try:
            if self.ws: self.ws.close()
        except Exception:
            pass
        # KHÔNG kill chrome ở đây (tool có thể đang dùng cho việc khác).
