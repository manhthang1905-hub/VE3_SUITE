"""
video_pool_browser — NHÀ MÁY VIDEO browser-based (generate IN-PAGE). OPT-IN, TÁCH RIÊNG với video_factory
(curl_cffi đang chạy tốt). Để DÀNH: khi video bị siết reCAPTCHA nhiều thì bật cái này thử.

Cơ chế (giống image_pool_browser, chỉ khác bước generate là VIDEO + async):
- Account gmail: chrome đã login+warm + IPv6 (reuse image_pool_browser.Account: prepare/cdp/ipv6/project + state).
- 1 job video: upload ref (curl_cffi, bearer từ page) -> media_id; GENERATE IN-PAGE (mint token VIDEO_GENERATION +
  fetch batchAsyncGenerateVideoReferenceImages) -> operations; POLL + DOWNLOAD bằng curl_cffi (proven, không token).
- Slot lifecycle + GOOD/BAD/DEAD + cooldown hồi + persist: reuse HẠ TẦNG image (nhưng state riêng .veo3top_vidpool_state.json).

API: POST /generate {image_path, prompt, out_path, aspect?, seed?} -> {success, info, error} (khớp video_factory_client).
CHƯA test end-to-end đầy đủ (video gen lâu) — dùng opt-in, test kỹ trước khi thay video chính.
"""
import os, sys, json, time, threading, queue, argparse, random, base64
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)
import flow_client as fc
from cdp_chrome import SITE_KEY
import image_pool_browser as IB   # reuse Account (prepare+chrome+ipv6), load_gmail_accounts, _load_login

SUITE = os.path.dirname(_HERE)
STATE_FILE = os.path.join(SUITE, ".veo3top_vidpool_state.json")
N_SLOTS = int(os.environ.get("VEO3TOP_VID_POOL_ACCOUNTS", "8") or "8")
LOGIN_CONCURRENCY = int(os.environ.get("VEO3TOP_VID_LOGIN_CONCURRENCY", "3") or "3")
GEN_ATTEMPTS = int(os.environ.get("VEO3TOP_VID_GEN_ATTEMPTS", "40") or "40")   # kiên nhẫn như video_factory
POLL_MAX = int(os.environ.get("VEO3TOP_VID_POLL_MAX", "180") or "180")   # 180×5s = 15' chờ render (video lâu -> để RỘNG, khỏi 'render timeout' oan)
JOB_MAX_CYCLES = 30
VID_MODEL = os.environ.get("VEO3TOP_VID_MODEL", fc.MODEL_I2V_LITE)


VID_ACCOUNTS_FILE = os.path.join(_HERE, "vid_accounts.txt")


def load_video_accounts():
    """Account RIENG cho nha may VIDEO (khac 169 account anh). Doc vid_accounts.txt: email|password|totp/dong."""
    out = []; seen = set()
    try:
        with open(VID_ACCOUNTS_FILE, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "|" not in line:
                    continue
                p = line.split("|")
                e = p[0].strip().lower()
                if e and e not in seen:
                    seen.add(e)
                    out.append({"email": p[0].strip(), "password": p[1].strip() if len(p) > 1 else "",
                                "totp": p[2].strip() if len(p) > 2 else ""})
    except Exception as e:
        _log(f"load_video_accounts loi: {e}")
    return out


def upsert_video_account(email, password, totp):
    """Them/Sua account video trong vid_accounts.txt. Tra (ok, msg)."""
    email = (email or "").strip().lower()
    if "@" not in email:
        return False, "email khong hop le"
    try:
        lines = []
        if os.path.exists(VID_ACCOUNTS_FILE):
            with open(VID_ACCOUNTS_FILE, encoding="utf-8") as f:
                lines = f.read().splitlines()
        new = f"{email}|{password}|{totp}"
        found = False
        for i, ln in enumerate(lines):
            if ln.strip() and not ln.startswith("#") and ln.split("|")[0].strip().lower() == email:
                lines[i] = new; found = True; break
        if not found:
            lines.append(new)
        with open(VID_ACCOUNTS_FILE, "w", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")
        return True, ("da SUA" if found else "da THEM")
    except Exception as e:
        return False, str(e)


def _log(msg):
    print(f"[vidpool] {msg}", flush=True)


def generate_video_inpage(account, media_id, prompt, aspect, seed):
    """Generate video IN-PAGE (reCAPTCHA VIDEO_GENERATION trong browser). Trả (kind, operations|info)."""
    proj = account.project
    url = fc.GEN_I2V   # batchAsyncGenerateVideoReferenceImages?key=KEY
    ref = [{"imageUsageType": "IMAGE_USAGE_TYPE_ASSET", "mediaId": media_id}] if media_id else []
    js = r'''(async()=>{
      try{
        const proj=%s, KEY=%s, SITE=%s, MODEL=%s, ASPECT=%s, SEED=%d, PROMPT=%s, REF=%s, URL=%s;
        const sess=await (await fetch('/fx/api/auth/session',{credentials:'include'})).json();
        if(!sess||!sess.access_token) return {k:'auth'};
        const tok=await grecaptcha.enterprise.execute(SITE,{action:'VIDEO_GENERATION'});
        const ctx={sessionId:';'+Date.now(),projectId:proj,tool:'PINHOLE',userPaygateTier:'PAYGATE_TIER_TWO',
          recaptchaContext:{applicationType:'RECAPTCHA_APPLICATION_TYPE_WEB',token:tok}};
        const req={aspectRatio:ASPECT,seed:SEED,textInput:{prompt:PROMPT},videoModelKey:MODEL};
        if(REF.length) req.referenceImages=REF;
        const body={clientContext:ctx,requests:[req]};
        const r=await fetch(URL,{method:'POST',headers:{'Content-Type':'text/plain;charset=UTF-8','Authorization':'Bearer '+sess.access_token},body:JSON.stringify(body)});
        const status=r.status; const txt=await r.text(); let j=null; try{j=JSON.parse(txt);}catch(e){}
        return {status:status, body:txt.slice(0,300), json:j};
      }catch(e){return {k:'jserr', body:String(e)};}
    })()''' % (json.dumps(proj), json.dumps(fc.KEY), json.dumps(SITE_KEY), json.dumps(VID_MODEL),
               json.dumps(aspect), int(seed), json.dumps(prompt), json.dumps(ref), json.dumps(url))
    try:
        res = account.cdp.ev(js, to=90000)   # submit video (trả operation): IPv6 chậm -> để 90s cho rộng, khỏi cắt oan
    except Exception as e:
        return "retry", str(e)[:120]
    if not isinstance(res, dict):
        return "retry", "no result"
    if res.get("k") == "auth":
        return "auth", None
    if res.get("k") == "jserr":
        return "retry", res.get("body", "")[:150]
    status = res.get("status"); j = res.get("json") or {}; body = res.get("body") or ""
    if status == 200:
        ops = fc.operation_names(j)
        return ("ok", ops) if ops else ("retry", "200 no operation")
    if status == 401:
        return "auth", None
    if status == 429 or "RESOURCE_EXHAUSTED" in body:
        return "quota", None
    if status == 403 or "UNUSUAL_ACTIVITY" in body or "PERMISSION_DENIED" in body:
        return "flagged", body[:120]
    return "other", body[:120]


class VideoPoolBrowser:
    def __init__(self, n_slots=N_SLOTS, log=_log):
        self.log = log
        self.q = queue.Queue()
        self.n_slots = n_slots
        self.candidates = []; self._cand_lock = threading.Lock(); self._cand_i = 0; self.active = {}
        self.login_sem = threading.Semaphore(LOGIN_CONCURRENCY)
        self._running = False; self._slots = []
        self.total_done = 0; self.total_fail = 0; self.started_ts = time.time(); self._clock = threading.Lock()
        self._login = IB._load_login()
        self._media_cache = {}
        self._state = self._load_state()

    def _load_state(self):
        try: return json.load(open(STATE_FILE, encoding="utf-8"))
        except Exception: return {}
    def _save_state(self):
        try: json.dump(self._state, open(STATE_FILE, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        except Exception: pass
    def _mark(self, email, **kw):
        with self._clock:
            d = self._state.get(email, {}); d.update(kw); d["ts"] = int(time.time()); now = int(time.time())
            if kw.get("state") == "bad": d["until"] = now + IB.BAD_COOLDOWN
            elif kw.get("state") == "dead": d["until"] = now + IB.DEAD_COOLDOWN
            elif kw.get("state") in ("good", "ready"): d.pop("until", None)
            self._state[email] = d; self._save_state()
    def _in_cooldown(self, email):
        st = self._state.get(email, {})
        return st.get("state") in ("bad", "dead") and time.time() < st.get("until", 0)

    def _next_candidate(self):
        with self._cand_lock:
            n = len(self.candidates)
            for _ in range(n):
                a = self.candidates[self._cand_i % n]; self._cand_i += 1
                if a.email in self.active or self._in_cooldown(a.email):
                    continue
                self.active[a.email] = a; return a
        return None
    def _release(self, a):
        with self._cand_lock: self.active.pop(a.email, None)

    def start(self):
        os.makedirs(IB.POOL_VID_PROFILES, exist_ok=True)
        gmails = load_video_accounts()   # 10 account RIENG cho video (vid_accounts.txt)
        gmails.sort(key=lambda x: (0 if self._state.get(x["email"], {}).get("state") == "good"
                                   else (2 if self._state.get(x["email"], {}).get("state") == "bad" else 1)))
        self.candidates = [IB.Account(i, a["email"], a["password"], a["totp"], profile_base=IB.POOL_VID_PROFILES)
                           for i, a in enumerate(gmails)]
        self.log(f"pool VIDEO browser: {len(self.candidates)} ứng viên, {self.n_slots} slot, model {VID_MODEL}")
        self._running = True
        for s in range(self.n_slots):
            t = threading.Thread(target=self._slot_worker, args=(s,), daemon=True); t.start(); self._slots.append(t)
        return True
    def stop(self):
        self._running = False
        for a in list(self.active.values()):
            try: a.close()
            except Exception: pass

    def _slot_worker(self, slot):
        while self._running:
            a = self._next_candidate()
            if a is None:
                time.sleep(5); continue
            try:
                a.idx = 20 + slot   # dải cổng RIÊNG (image service 0-7, video 20-27) -> không trùng khi chạy cùng
                if not a.prepare(self._login, self.login_sem, self.log):
                    self._mark(a.email, state=a.state, reason="login/warm fail"); a.close(); continue
                self._mark(a.email, state="ready", project=a.project)
                while self._running and a.state == "ready":
                    try:
                        job = self.q.get(timeout=3)
                    except queue.Empty:
                        continue
                    if job.get("_cycles", 0) > JOB_MAX_CYCLES:
                        job["_result"] = (False, {}, "quá nhiều lượt"); job["_event"].set(); continue
                    job["_cycles"] = job.get("_cycles", 0) + 1
                    a.busy = True
                    try:
                        outcome = self._process(a, job)
                    except Exception as e:
                        outcome = ("fail", f"exception: {e}")
                    a.busy = False
                    if outcome == "success":
                        a.wins += 1
                        if not a.good: a.good = True; self._mark(a.email, state="good", project=a.project)
                        with self._clock: self.total_done += 1
                        job["_event"].set()
                    elif outcome == "retry_soft":
                        a.fails += 1; self.q.put(job)
                    elif isinstance(outcome, tuple) and outcome[0] == "reauth":
                        self.q.put(job); a.state = "new"
                    elif isinstance(outcome, tuple) and outcome[0] == "bad":
                        self.q.put(job); a.state = "bad"; self._mark(a.email, state="bad", reason="flagged")
                        self.log(f"⛔ {a.email}: BAD -> cách ly")
                    else:
                        if "_result" not in job:
                            job["_result"] = (False, {}, outcome[1] if isinstance(outcome, tuple) else str(outcome))
                        with self._clock: self.total_fail += 1
                        job["_event"].set()
            finally:
                a.close(); self._release(a)

    def _process(self, a, job):
        bearer = None; cookie = None
        try:
            bearer = a.cdp.bearer(); cookie = fc.labs_cookie_header(a.cdp.cookies())
        except Exception:
            return ("reauth", "no bearer")
        if not bearer:
            return ("reauth", "no bearer")
        project = a.project
        seed = job.get("seed") or (int(time.time() * 1000) % 2_000_000)
        aspect = job.get("aspect") or "VIDEO_ASPECT_RATIO_LANDSCAPE"
        img = job.get("image_path")
        # 1) upload ref (curl_cffi, direct) -> media_id (cache theo (email,img))
        media_id = self._media_cache.get((a.email, img)) if img else None
        if img and not media_id:
            sess = f";{int(time.time()*1000)}"
            media_id, err = fc.upload_image(bearer, project, sess, img, aspect=aspect)
            if not media_id:
                if err and err.startswith("401"):
                    return ("reauth", "upload 401")
                return "retry_soft"
            self._media_cache[(a.email, img)] = media_id
        # 2) generate IN-PAGE (kiên nhẫn) + 3) poll + 4) download (curl_cffi)
        for attempt in range(GEN_ATTEMPTS):
            kind, data = generate_video_inpage(a, media_id, job["prompt"], aspect, seed)
            if kind == "ok":
                op = (data or [None])[0]
                if not op:
                    continue
                pkind, _ = fc.poll_until_done(bearer, [op], max_attempts=POLL_MAX, interval=5)
                if pkind == "failed":
                    job["_result"] = (False, {"media_id": op}, "render FAILED (policy)"); return ("fail", "policy")
                if pkind == "auth":
                    return ("reauth", "poll 401")
                if pkind != "done":
                    return "retry_soft"
                os.makedirs(os.path.dirname(job["out_path"]), exist_ok=True)
                for _ in range(10):
                    try:
                        n, _u = fc.media_url_and_download(op, cookie, job["out_path"])
                        job["_result"] = (True, {"media_id": op, "bytes": n, "account": a.email,
                                                 "egress": "ipv6" if a.ipv6 else "ipmay"}, "")
                        return "success"
                    except Exception:
                        time.sleep(3)
                return ("fail", "download fail")
            elif kind == "auth":
                return ("reauth", "gen 401")
            elif kind == "quota":
                time.sleep(1.0 + random.uniform(0, 0.8)); continue
            elif kind == "flagged":
                # video ít bị flag; nếu flag liên tục -> account bad
                if attempt >= 6:
                    return ("bad", "flagged nhiều")
                time.sleep(1.0); continue
            else:
                time.sleep(1.0)
        return "retry_soft"

    def submit(self, image_path, prompt, out_path, aspect=None, seed=None, timeout=1600):
        job = {"image_path": image_path, "prompt": prompt, "out_path": out_path, "aspect": aspect,
               "seed": seed, "_event": threading.Event()}
        self.q.put(job)
        if not job["_event"].wait(timeout=timeout):
            return False, {}, "video pool browser: timeout"
        return job.get("_result", (False, {}, "no result"))

    def health(self):
        up = time.time() - self.started_ts
        return {"slots": self.n_slots, "candidates": len(self.candidates),
                "active": [{"email": a.email, "state": a.state, "good": a.good, "busy": a.busy, "wins": a.wins}
                           for a in list(self.active.values())],
                "queue": self.q.qsize(), "done": self.total_done, "fail": self.total_fail,
                "video_per_hour": round(self.total_done / up * 3600, 1) if up > 5 else 0, "uptime_s": round(up)}


_FACTORY = None

class _Handler(BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def _send(self, code, obj):
        b = json.dumps(obj).encode("utf-8")
        self.send_response(code); self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(b))); self.end_headers(); self.wfile.write(b)
    def do_GET(self):
        if self.path == "/health": self._send(200, _FACTORY.health())
        else: self._send(404, {"error": "nf"})
    def do_POST(self):
        n = int(self.headers.get("Content-Length", 0) or 0)
        try:
            data = json.loads(self.rfile.read(n).decode("utf-8") or "{}") if n else {}
        except Exception:
            self._send(400, {"error": "bad json"}); return
        if self.path == "/shutdown":
            self._send(200, {"ok": True}); threading.Thread(target=_FACTORY.stop, daemon=True).start(); return
        if self.path == "/generate":
            if not data.get("out_path"):
                self._send(400, {"error": "thieu out_path"}); return
            ok, info, err = _FACTORY.submit(data.get("image_path"), data.get("prompt"), data["out_path"],
                                            aspect=data.get("aspect"), seed=data.get("seed"))
            self._send(200, {"success": bool(ok), "info": info, "error": err}); return
        self._send(404, {"error": "nf"})


def main():
    global _FACTORY
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8790)
    ap.add_argument("--accounts", type=int, default=N_SLOTS)
    args = ap.parse_args()
    _FACTORY = VideoPoolBrowser(n_slots=args.accounts)
    _FACTORY.start()
    srv = ThreadingHTTPServer(("127.0.0.1", args.port), _Handler)
    _log(f"HTTP video browser nghe 127.0.0.1:{args.port} (POST /generate)")
    try:
        srv.serve_forever()
    finally:
        _FACTORY.stop()


if __name__ == "__main__":
    main()
