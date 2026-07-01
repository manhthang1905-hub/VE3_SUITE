"""
Token Factory — Chrome trắng (ultra account, đi qua WARP) đẻ recaptcha token liên tục.

- Mở GoogleChromePortable.exe (launcher) với cờ remote-debugging + WARP socks5.
- Điều khiển qua CDP (websocket), KHÔNG selenium (chrome 148 vs chromedriver 149 lệch).
- Mint token: window.grecaptcha.enterprise.execute(SITE_KEY,{action:'VIDEO_GENERATION'}).
- Lấy bearer: fetch('/fx/api/auth/session') trong page (credentials:include).
- LUẬT: token phải mint khi Chrome đang đi đúng IP WARP mà generate sẽ dùng (cùng socks 40000).
"""
import json, time, itertools, subprocess, requests, websocket

SITE_KEY = "6LdsFiUsAAAAAIjVDZcuLhaHiDn5nnHVXVRQGeMV"
ACTION = "VIDEO_GENERATION"
WARP_SOCKS = "socks5://127.0.0.1:40000"


class ChromeAccount:
    def __init__(self, launcher_path: str, debug_port: int, profile_url="https://labs.google/fx/tools/flow"):
        self.launcher = launcher_path
        self.port = debug_port
        self.url = profile_url
        self.ws = None
        self._id = itertools.count(1)

    # ---- process ----
    def launch(self, through_warp=True):
        import os
        port_dir = os.path.dirname(self.launcher)
        # kill chromes from this portable dir only
        subprocess.run(["powershell", "-NoProfile", "-Command",
            f"Get-CimInstance Win32_Process -Filter \"Name='chrome.exe'\" | "
            f"Where-Object {{ $_.ExecutablePath -like '{port_dir}*' }} | "
            f"ForEach-Object {{ Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }}"],
            capture_output=True)
        time.sleep(2)
        # Cờ quan sát từ token-chrome của veo3top: giấu automation (score reCAPTCHA cao), blank offscreen.
        args = [self.launcher, f"--remote-debugging-port={self.port}", "--remote-allow-origins=*",
                "--disable-blink-features=AutomationControlled",   # KEY: che automation -> token score cao
                "--no-first-run", "--no-default-browser-check", "--disable-extensions",
                "--disable-background-networking", "--disable-sync", "--mute-audio",
                "--disable-features=WebRtcHideLocalIpsWithMdns,WebRTC-MDNS-Responder,PrivacySandboxSettings4,CalculateNativeWinOcclusion",
                "--window-size=320,480", "--window-position=-30000,0"]   # offscreen (blank)
        if through_warp:
            args += [f"--proxy-server={WARP_SOCKS}", "--proxy-bypass-list=<-loopback>;127.0.0.1;localhost"]
        # token-chrome của tool chạy DIRECT (no proxy) — mint token IP nhà, generate mới qua WARP.
        args += [self.url]
        subprocess.Popen(args)
        return self._wait_page()

    def _wait_page(self, timeout=40):
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                for t in requests.get(f"http://localhost:{self.port}/json", timeout=3).json():
                    if t.get("type") == "page" and "labs.google" in (t.get("url") or ""):
                        self.ws = websocket.create_connection(t["webSocketDebuggerUrl"], max_size=None)
                        self._cmd("Runtime.enable")
                        return True
            except Exception:
                pass
            time.sleep(1)
        return False

    def connect_existing(self):
        return self._wait_page(timeout=5)

    # ---- CDP ----
    def _cmd(self, method, **params):
        i = next(self._id)
        self.ws.send(json.dumps({"id": i, "method": method, "params": params}))
        while True:
            m = json.loads(self.ws.recv())
            if m.get("id") == i:
                return m

    def _eval(self, expr, timeout_ms=60000):
        r = self._cmd("Runtime.evaluate", expression=expr, awaitPromise=True,
                      returnByValue=True, userGesture=True, timeout=timeout_ms)
        return r.get("result", {}).get("result", {}).get("value")

    # ---- high level ----
    def grecaptcha_ready(self):
        return bool(self._eval("!!(window.grecaptcha&&window.grecaptcha.enterprise&&window.grecaptcha.enterprise.execute)"))

    def wait_ready(self, timeout=30):
        deadline = time.time() + timeout
        while time.time() < deadline:
            if self.grecaptcha_ready():
                return True
            time.sleep(1)
        return False

    def reload(self):
        self._cmd("Page.enable"); self._cmd("Page.reload", ignoreCache=True)

    def email(self):
        return self._eval("(async()=>{const j=await (await fetch('/fx/api/auth/session',{credentials:'include'})).json();return (j.user||{}).email;})()")

    def bearer(self):
        return self._eval("(async()=>{const j=await (await fetch('/fx/api/auth/session',{credentials:'include'})).json();return j.access_token;})()")

    def first_project_id(self):
        js = (r"(async()=>{const u='/fx/api/trpc/project.searchUserProjects?input='+"
              r"encodeURIComponent(JSON.stringify({json:{pageSize:5,toolName:'PINHOLE',cursor:null},"
              r"meta:{values:{cursor:['undefined']}}}));const j=await (await fetch(u,{credentials:'include'})).json();"
              r"try{return j.result.data.json.result.projects[0].projectId;}catch(e){return null;}})()")
        return self._eval(js)

    def create_project(self, title="auto"):
        """Tạo project PINHOLE qua trpc (chrome đã login). Trả projectId hoặc None."""
        js = (r"""(async()=>{try{
          const r=await fetch('/fx/api/trpc/project.createProject',{method:'POST',credentials:'include',
            headers:{'Content-Type':'application/json'},
            body:JSON.stringify({json:{projectTitle:'%s',toolName:'PINHOLE'}})});
          const j=await r.json();
          const d=j.result&&j.result.data&&j.result.data.json;
          return (d&&(d.projectId||d.result&&d.result.projectId))||JSON.stringify(j).slice(0,150);
        }catch(e){return 'ERR:'+e;}})()""" % title)
        return self._eval(js)

    def ensure_project(self):
        pid = self.first_project_id()
        if pid:
            return pid
        pid = self.create_project()
        if pid and len(str(pid)) == 36 and "-" in str(pid):
            return pid
        return None

    def mint_token(self):
        return self._eval(f"(async()=>{{try{{return await window.grecaptcha.enterprise.execute('{SITE_KEY}',{{action:'{ACTION}'}});}}catch(e){{return null;}}}})()")

    def cookies(self):
        """Tất cả cookie (CDP) — dùng flow_client.labs_cookie_header() để lọc cho download."""
        self._cmd("Network.enable")
        return self._cmd("Network.getAllCookies").get("result", {}).get("cookies", [])

    def close(self):
        """Đóng chrome của portable dir này (giải phóng port khi rotate account)."""
        try:
            if self.ws: self.ws.close()
        except Exception:
            pass
        import os
        port_dir = os.path.dirname(self.launcher)
        subprocess.run(["powershell", "-NoProfile", "-Command",
            f"Get-CimInstance Win32_Process -Filter \"Name='chrome.exe'\" | "
            f"Where-Object {{ $_.ExecutablePath -like '{port_dir}*' }} | "
            f"ForEach-Object {{ Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }}"],
            capture_output=True)
