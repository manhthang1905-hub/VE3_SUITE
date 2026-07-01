"""
ChromeCDP — helper nhỏ điều khiển 1 chrome account qua CDP (launch trực tiếp chrome.exe + profile).
Dùng chung cho token_factory / auth_cache / provider_b (cách B). Có reconnect khi ws rớt.
"""
import os, json, time, itertools, subprocess
import requests, websocket

SITE_KEY = "6LdsFiUsAAAAAIjVDZcuLhaHiDn5nnHVXVRQGeMV"
ACTION = "VIDEO_GENERATION"
FLOW_URL = "https://labs.google/fx/tools/flow"

# Đăng ký PID chrome veo3top vào <SUITE_ROOT>\.veo3top_pids\<port>.pid để GUI dọn được khi tắt
# (chrome có thể orphan khỏi subprocess, hoặc blank-mode dùng system chrome không có 'GoogleChromePortable').
_PID_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".veo3top_pids")

def _register_pid(port, pid):
    try:
        os.makedirs(_PID_DIR, exist_ok=True)
        with open(os.path.join(_PID_DIR, f"{port}.pid"), "w") as f:
            f.write(str(pid))
    except Exception:
        pass

def _unregister_pid(port):
    try:
        os.remove(os.path.join(_PID_DIR, f"{port}.pid"))
    except Exception:
        pass


def _pid_alive(pid):
    try:
        # tasklist trả về dòng chứa PID nếu tiến trình còn sống (ẩn cửa sổ)
        out = subprocess.run(["tasklist", "/FI", f"PID eq {int(pid)}", "/NH"],
                             capture_output=True, text=True, timeout=8,
                             creationflags=0x08000000)
        return out.stdout and (f" {pid} " in out.stdout or f"\t{pid}\t" in out.stdout
                               or str(pid) in out.stdout.split())
    except Exception:
        return True   # không chắc -> coi như sống (không xoá nhầm)


def cleanup_dead_pidfiles():
    """Xoá file .pid trỏ tới chrome ĐÃ CHẾT (tồn dư sau crash/force-stop) -> thư mục PID không phình.
    Không kill gì — chỉ dọn rác metadata."""
    try:
        if not os.path.isdir(_PID_DIR):
            return
        for name in os.listdir(_PID_DIR):
            if not name.endswith(".pid"):
                continue
            p = os.path.join(_PID_DIR, name)
            try:
                pid = int(open(p).read().strip() or "0")
                if pid and not _pid_alive(pid):
                    os.remove(p)
            except Exception:
                pass
    except Exception:
        pass


class ChromeCDP:
    def __init__(self, chrome_exe, profile_dir, port, offscreen=True, log=lambda *_: None):
        self.chrome_exe = chrome_exe
        self.profile_dir = profile_dir
        self.port = port
        self.offscreen = offscreen
        self.log = log
        self.ws = None
        self._id = itertools.count(1)
        self._proc = None

    def _page_ws(self):
        try:
            for t in requests.get(f"http://localhost:{self.port}/json", timeout=3).json():
                if t.get("type") == "page" and "labs.google" in (t.get("url") or ""):
                    return t["webSocketDebuggerUrl"]
        except Exception:
            return None
        return None

    def _launch(self):
        # Flags KHỚP veo3top (đã soi lệnh mở chrome captcha thật của nó) — quan trọng cho điểm reCAPTCHA:
        #  - --source-restrictions=no-ipv6: ÉP token chrome đi IPv4 residential (điểm cao). Máy giờ default IPv6,
        #    nếu mint qua IPv6 -> Google chấm thấp -> TOO_MUCH_TRAFFIC. veo3top ép IPv4.
        #  - tắt PrivacySandboxSettings4 + WebRTC MDNS + media... -> fingerprint sạch hơn (bớt lộ bot).
        args = [self.chrome_exe, f"--remote-debugging-port={self.port}", "--remote-allow-origins=*",
                f"--user-data-dir={self.profile_dir}",
                "--no-sandbox", "--test-type", "--disable-dev-shm-usage", "--disable-extensions",
                "--disable-browser-side-navigation",
                "--js-flags=--max-old-space-size=512",
                "--force-webrtc-ip-handling-policy=disable_non_proxied_udp",
                "--disable-features=WebRtcHideLocalIpsWithMdns,WebRTC-MDNS-Responder,GlobalMediaControls,"
                "ImageService,InternalMediaSession,PrivacySandboxSettings4,CalculateNativeWinOcclusion",
                "--disable-background-timer-throttling", "--disable-backgrounding-occluded-windows",
                "--disable-renderer-backgrounding",
                "--disable-blink-features=AutomationControlled",
                "--no-first-run", "--no-default-browser-check", "--mute-audio",
                "--disable-background-networking", "--disable-sync",
                "--net-log-capture-mode=None", "--source-restrictions=no-ipv6",
                "--disable-gpu", "--disable-software-rasterizer", "--disable-gpu-rasterization",
                "--window-size=320,480"]
        # "FAKE DNS GOOGLE" (soi veo3top: chrome captcha nối 8.8.8.8/8.8.4.4) = ép chrome resolve DNS
        # qua Google DoH (dns.google) -> reCAPTCHA/Google domain đi đường Google, token điểm tốt hơn.
        if os.environ.get("VEO3TOP_FAKE_DNS", "1") != "0":
            args += ["--dns-over-https-mode=secure",
                     "--dns-over-https-templates=https://dns.google/dns-query"]
        if self.offscreen:
            args.append("--window-position=-30000,0")
        args.append(FLOW_URL)
        self._proc = subprocess.Popen(args)
        _register_pid(self.port, self._proc.pid)   # để GUI dọn được khi tắt (kể cả orphan)

    def connect(self, launch_timeout=45):
        ws_url = self._page_ws()
        if not ws_url:
            self._launch()
            end = time.time() + launch_timeout
            while time.time() < end and not ws_url:
                time.sleep(1); ws_url = self._page_ws()
        if not ws_url:
            return False
        self.ws = websocket.create_connection(ws_url, max_size=None)
        self._cmd_raw("Runtime.enable")
        return True

    # ---- CDP w/ reconnect ----
    def _cmd_raw(self, method, **params):
        i = next(self._id)
        self.ws.send(json.dumps({"id": i, "method": method, "params": params}))
        while True:
            m = json.loads(self.ws.recv())
            if m.get("id") == i:
                return m

    def _reconnect(self):
        for _ in range(20):
            ws_url = self._page_ws()
            if not ws_url:
                if not self._proc or self._proc.poll() is not None:
                    self._launch()
                time.sleep(2); continue
            try:
                self.ws = websocket.create_connection(ws_url, max_size=None)
                self._cmd_raw("Runtime.enable")
                return True
            except Exception:
                time.sleep(1)
        return False

    def cmd(self, method, **params):
        try:
            return self._cmd_raw(method, **params)
        except (ConnectionResetError, websocket.WebSocketConnectionClosedException, OSError, BrokenPipeError):
            if self._reconnect():
                return self._cmd_raw(method, **params)
            raise

    def ev(self, expr, to=60000):
        r = self.cmd("Runtime.evaluate", expression=expr, awaitPromise=True,
                     returnByValue=True, userGesture=True, timeout=to)
        return r.get("result", {}).get("result", {}).get("value")

    # ---- high level ----
    def grecaptcha_ready(self):
        return bool(self.ev("!!(window.grecaptcha&&window.grecaptcha.enterprise&&window.grecaptcha.enterprise.execute)"))

    def wait_ready(self, timeout=35, verify_token=True):
        """Chờ reCAPTCHA SẴN SÀNG. Không chỉ chờ hàm execute tồn tại — mà (mặc định) MINT THỬ 1 token thật
        để chắc SCRIPT ĐÃ LOAD XONG (như veo3top 'đợi load script'). Mint sớm khi script chưa load = token
        vô nghĩa/không dùng được -> phải chờ mint ra token mới coi là ready."""
        end = time.time() + timeout
        while time.time() < end:
            if self.grecaptcha_ready():
                if not verify_token:
                    return True
                try:
                    if self.mint_token():     # token THẬT mint được -> script reCAPTCHA đã load hẳn
                        return True
                except Exception:
                    pass
            time.sleep(1)
        return False

    def email(self):
        return self.ev("(async()=>{const j=await (await fetch('/fx/api/auth/session',{credentials:'include'})).json();return (j.user||{}).email;})()")

    def bearer(self):
        return self.ev("(async()=>{const j=await (await fetch('/fx/api/auth/session',{credentials:'include'})).json();return j.access_token;})()")

    def cookies(self):
        self.cmd("Network.enable")
        return self.cmd("Network.getAllCookies").get("result", {}).get("cookies", [])

    def first_project_id(self):
        return self.ev(r"(async()=>{const u='/fx/api/trpc/project.searchUserProjects?input='+encodeURIComponent(JSON.stringify({json:{pageSize:5,toolName:'PINHOLE',cursor:null},meta:{values:{cursor:['undefined']}}}));const j=await (await fetch(u,{credentials:'include'})).json();try{return j.result.data.json.result.projects[0].projectId;}catch(e){return null;}})()")

    def mint_token(self, action=None):
        a = action or ACTION   # video='VIDEO_GENERATION' (default); ảnh truyền 'IMAGE_GENERATION'
        return self.ev(f"(async()=>{{try{{return await window.grecaptcha.enterprise.execute('{SITE_KEY}',{{action:'{a}'}});}}catch(e){{return null;}}}})()")

    def close(self, kill=False):
        try:
            if self.ws: self.ws.close()
        except Exception:
            pass
        # CHI kill dung process chrome factory TU MO (theo PID tree) — KHONG kill theo path
        # (neu kill theo path system Chrome se giet luon Chrome ca nhan cua user!).
        if kill and self._proc is not None:
            try:
                subprocess.run(["taskkill", "/PID", str(self._proc.pid), "/T", "/F"],
                               capture_output=True, creationflags=0x08000000)  # CREATE_NO_WINDOW: ẩn cửa sổ taskkill
            except Exception:
                pass
            _unregister_pid(self.port)
            self._proc = None
