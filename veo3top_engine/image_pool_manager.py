"""
image_pool_manager — QUẢN LÝ + CHUẨN BỊ account cho nhà máy ảnh (chạy TRƯỚC tool / theo dõi / kiểm soát).
Mục tiêu: hết "mù mờ" — thấy account nào READY/GOOD/BAD(flagged)/DEAD(login fail) + LÝ DO, chuẩn bị sẵn,
kiểm tra/sửa thủ công. Dùng chung state (.veo3top_imgpool_state.json) với image_pool_browser.

Lệnh:
  python image_pool_manager.py status                 # bảng trạng thái mọi account
  python image_pool_manager.py prepare [N] [--probe]  # login+warm N account chưa sẵn (--probe: test 1 ảnh -> good/bad)
  python image_pool_manager.py check <email>          # MỞ chrome (hiện) để kiểm tra/sửa thủ công
  python image_pool_manager.py reset <email|all>      # xoá trạng thái (cho thử lại)
  python image_pool_manager.py probe <email>          # test tạo 1 ảnh account đó (good/bad)
"""
import os, sys, json, time, argparse, sqlite3, threading, queue as _queue
_HERE = os.path.dirname(os.path.abspath(__file__))
PREP_CONCURRENCY = int(os.environ.get("VEO3TOP_IMG_PREP_CONCURRENCY", "5") or "5")  # login SONG SONG (mặc định 5, chỉnh ở GUI)
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)
import image_pool_browser as P
import ipv6_transport as ip6t
import flow_client as fc
import account_manager as am
try:
    from auth_cache import AuthCache
    _AC = AuthCache(log=lambda *_: None)
except Exception:
    _AC = None


def cookie_check(email, sf=None):
    """CHECK account bằng COOKIE (curl_cffi, KHÔNG mở chrome) — như video. Trả:
    'alive' (cookie->bearer OK, dùng được) | 'cookie_dead' | 'no_cookie'."""
    if _AC is None:
        return "no_cookie"
    d = _AC._load(email)
    if not d or not d.get("cookie"):
        return "no_cookie"
    try:
        bearer, _ = fc.bearer_from_cookie(d["cookie"])
    except Exception:
        bearer = None
    if not bearer:
        return "cookie_dead"
    proj = d.get("project") or _load_state(sf).get(email, {}).get("project")
    if not proj:
        return "alive"   # cookie sống (chưa rõ project) — vẫn coi là còn phiên
    try:
        return "alive" if am.validate_auth({"bearer": bearer, "project": proj}) else "cookie_dead"
    except Exception:
        return "alive"


def checkcookies_parallel(concurrency=12, log=print, on_done=None, sf=None, acct_loader=None):
    """CHECK NHANH mọi account có cookie bằng curl (song song, không chrome). Cập nhật state['cookie']."""
    accts = (acct_loader or P.load_gmail_accounts)()
    work = _queue.Queue()
    for a in accts: work.put(a["email"])
    lock = threading.Lock(); n = {"i": 0}; tot = len(accts)
    def _w():
        while True:
            try: e = work.get_nowait()
            except _queue.Empty: return
            r = cookie_check(e, sf)
            with _STATE_LOCK:   # dung lock CHUNG voi _mark -> khong ghi de nhau (giu du lieu)
                s = _load_state(sf); d = s.get(e, {}); d["cookie"] = r; d["cookie_ts"] = int(time.time())
                s[e] = d; _save_state(s, sf)
            with lock: n["i"] += 1; i = n["i"]
            if on_done: on_done(e, r)
            if i % 20 == 0: log(f"  cookie-check {i}/{tot}...")
    ths = [threading.Thread(target=_w, daemon=True) for _ in range(concurrency)]
    for t in ths: t.start()
    for t in ths: t.join()
    log("cookie-check xong.")


IMG_STATE = P.STATE_FILE
try:
    import video_pool_browser as _VB
    VID_STATE = _VB.STATE_FILE
except Exception:
    VID_STATE = os.path.join(P.SUITE, ".veo3top_vidpool_state.json")


_STATE_LOCK = threading.Lock()   # CHONG RACE: prepare song song -> nhieu luong ghi state -> mat du lieu


def _load_state(sf=None):
    try: return json.load(open(sf or IMG_STATE, encoding="utf-8"))
    except Exception: return {}

def _save_state(s, sf=None):
    # GHI ATOMIC (temp + replace) -> khong hong file khi ghi song song / dang doc
    path = sf or IMG_STATE
    try:
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(s, f, ensure_ascii=False, indent=1)
        os.replace(tmp, path)
    except Exception:
        try:
            json.dump(s, open(path, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        except Exception:
            pass

def _mark(email, sf=None, **kw):
    # LOCK: load->sua->luu ATOMIC trong process -> khong luong nao ghi de mat du lieu luong khac
    with _STATE_LOCK:
        s = _load_state(sf); d = s.get(email, {}); d.update(kw); d["ts"] = int(time.time())
        now = int(time.time())
        if kw.get("state") == "bad": d["until"] = now + P.BAD_COOLDOWN
        elif kw.get("state") == "dead": d["until"] = now + P.DEAD_COOLDOWN
        elif kw.get("state") in ("good", "ready"): d.pop("until", None)
        s[email] = d; _save_state(s, sf)


def status_rows(sf=None, acct_loader=None):
    """Trả list dict trạng thái mọi account (cho GUI/monitor). Không in.
    acct_loader: hàm trả list account (ảnh=169 gmail; video=10 account riêng)."""
    accts = (acct_loader or P.load_gmail_accounts)(); st = _load_state(sf)
    rows = []
    for a in accts:
        e = a["email"]; d = st.get(e, {})
        cd = 0
        if d.get("until", 0) > time.time():
            cd = int((d["until"] - time.time()) / 60)
        rows.append({
            "email": e, "state": d.get("state", "unknown"), "reason": d.get("reason", ""),
            "project": (d.get("project") or "")[:8], "cooldown_min": cd,
            "cookie": d.get("cookie", ""),   # alive/cookie_dead/no_cookie (check nhanh không chrome)
            "last": time.strftime("%m-%d %H:%M", time.localtime(d["ts"])) if d.get("ts") else "",
            "has_pass": bool(a.get("password")), "has_totp": bool(a.get("totp")),
        })
    return rows


def status_summary(sf=None, acct_loader=None):
    cnt = {}
    for r in status_rows(sf, acct_loader):
        cnt[r["state"]] = cnt.get(r["state"], 0) + 1
    return cnt


def cmd_status():
    accts = P.load_gmail_accounts(); st = _load_state()
    cnt = {}
    print(f"{'EMAIL':40} {'STATE':8} {'REASON':28} {'LAST':17} PROJECT")
    print("-" * 105)
    for a in accts:
        e = a["email"]; d = st.get(e, {})
        state = d.get("state", "unknown"); cnt[state] = cnt.get(state, 0) + 1
        reason = d.get("reason", "")
        last = time.strftime("%m-%d %H:%M", time.localtime(d["ts"])) if d.get("ts") else ""
        cd = ""
        if d.get("until", 0) > time.time():
            cd = f" (nghỉ {int((d['until']-time.time())/60)}p)"
        print(f"{e:40} {state:8} {(reason+cd)[:28]:28} {last:17} {(d.get('project') or '')[:8]}")
    print("-" * 105)
    print("TỔNG:", ", ".join(f"{k}={v}" for k, v in sorted(cnt.items())), f"| {len(accts)} account")


def _prepare_one(idx, email, password, totp, probe=False, visible=False, log=print, sf=None, profile_base=None):
    # dải cổng RIÊNG cho manager prepare (40+) -> không trùng image service (0-7) / video (20-27)
    a = P.Account(40 + idx, email, password, totp, profile_base=profile_base)
    login = P._load_login()
    ok = a.prepare(login, __import__("threading").Semaphore(1), log)
    if not ok:
        _mark(email, sf=sf, state="dead", reason="login/warm fail")
        a.close(); return "dead"
    # ready = login+warm OK -> session/cookie SỐNG (đã _save_cookie ra auth_cache) -> đánh dấu cookie=alive
    _mark(email, sf=sf, state="ready", reason="", project=a.project, cookie="alive")
    if probe:
        # test 1 ảnh -> good/bad (CẢNH BÁO: test-gen có thể góp phần flag account) — CHỈ dùng cho pool ẢNH
        try:
            kind, data = a.generate_inpage("a red apple on white table", P.IMG_MODELS[0],
                                           "IMAGE_ASPECT_RATIO_LANDSCAPE", 12345, [])
            if kind == "ok":
                _mark(email, sf=sf, state="good", reason="probe ok", project=a.project, cookie="alive"); res = "good"
            elif kind == "flagged":
                _mark(email, sf=sf, state="bad", reason="flagged (403/unusual)", cookie="alive"); res = "bad"
            elif kind == "quota":
                _mark(email, sf=sf, state="ready", reason="quota (het luot tam)", cookie="alive"); res = "quota"
            else:
                _mark(email, sf=sf, state="ready", reason=f"probe {kind}", cookie="alive"); res = kind
        finally:
            a.close()
        return res
    a.close(); return "ready"


def select_todo(n, sf=None, acct_loader=None):
    """Chọn n account chưa sẵn (bỏ ready/good + đang cooldown) để chuẩn bị."""
    accts = (acct_loader or P.load_gmail_accounts)(); st = _load_state(sf); todo = []
    for a in accts:
        d = st.get(a["email"], {})
        if d.get("state") in ("ready", "good"): continue
        if d.get("state") in ("bad", "dead") and d.get("until", 0) > time.time(): continue
        todo.append(a)
        if len(todo) >= n: break
    return todo


def prepare_parallel(todo, probe=False, concurrency=None, log=print, on_done=None, sf=None, profile_base=None):
    """Login+warm SONG SONG nhiều chrome (mỗi luồng 1 slot port/IPv6 riêng). IPv6 pool vô hạn -> xong nhanh."""
    concurrency = concurrency or PREP_CONCURRENCY
    concurrency = max(1, min(concurrency, len(todo) or 1))
    slots = _queue.Queue()
    for i in range(concurrency): slots.put(i)      # slot idx -> port/IPv6 riêng, tái dùng khi luồng xong
    work = _queue.Queue()
    for a in todo: work.put(a)
    done = {"n": 0}; total = len(todo); lock = threading.Lock()

    def _worker():
        while True:
            try: a = work.get_nowait()
            except _queue.Empty: return
            slot = slots.get()
            t0 = time.time()
            try:
                r = _prepare_one(slot, a["email"], a["password"], a["totp"], probe=probe, log=log, sf=sf, profile_base=profile_base)
            except Exception as e:
                r = f"err:{e}"
            finally:
                slots.put(slot)
            with lock:
                done["n"] += 1; i = done["n"]
            log(f"  [{i}/{total}] {a['email']:36} -> {r} ({time.time()-t0:.0f}s)")
            if on_done:
                try: on_done(a["email"], r)
                except Exception: pass

    ths = [threading.Thread(target=_worker, daemon=True) for _ in range(concurrency)]
    for t in ths: t.start()
    for t in ths: t.join()


def cmd_prepare(n, probe=False):
    todo = select_todo(n)
    print(f"Chuẩn bị {len(todo)} account SONG SONG {min(PREP_CONCURRENCY, len(todo) or 1)} chrome (probe={probe})...", flush=True)
    prepare_parallel(todo, probe=probe, log=lambda m: print(m, flush=True))
    print("Xong. Chạy 'status' để xem tổng quan.", flush=True)


def cmd_probe(email):
    a = _find(email)
    if not a: print("Không thấy account"); return
    print(f"Probe {email}...", flush=True)
    r = _prepare_one(0, a["email"], a["password"], a["totp"], probe=True)
    print(f"-> {r}", flush=True)


def cmd_check(email):
    """Mở chrome HIỆN (visible) với profile account để kiểm tra/sửa thủ công."""
    a = _find(email)
    if not a: print("Không thấy account"); return
    from cdp_chrome import ChromeCDP
    acc = P.Account(0, a["email"], a["password"], a["totp"])
    acc._start_ipv6()
    proxy = acc.ipv6.proxy_url() if acc.ipv6 else None
    os.makedirs(acc.profile, exist_ok=True)
    print(f"Mở chrome (HIỆN) {email} — profile {acc.profile}\nĐăng nhập/kiểm tra thủ công. Ctrl+C để đóng.", flush=True)
    c = ChromeCDP(P._CHROME, acc.profile, 9550, offscreen=False, log=print, proxy=proxy)
    c.connect(launch_timeout=45)
    try:
        while True: time.sleep(2)
    except KeyboardInterrupt:
        c.close(kill=True)
        try: acc.ipv6.stop()
        except Exception: pass


def cmd_reset(email):
    s = _load_state()
    if email == "all":
        _save_state({}); print("Đã xoá toàn bộ trạng thái."); return
    if email in s:
        del s[email]; _save_state(s); print(f"Đã reset {email}")
    else:
        print("Không có trong state")


def _find(email):
    for a in P.load_gmail_accounts():
        if a["email"].lower() == email.lower(): return a
    return None


def db_upsert_account(email, password, totp):
    """Them/Sua account trong DB gmail_profile_manager (email|password|totp). Tra (ok, msg)."""
    email = (email or "").strip().lower()
    if "@" not in email:
        return False, "email khong hop le"
    try:
        c = sqlite3.connect(P.GPM_DB)
        cols = [r[1] for r in c.execute("PRAGMA table_info(accounts)")]
        row = c.execute("SELECT id FROM accounts WHERE lower(email)=?", (email,)).fetchone()
        if row:
            c.execute("UPDATE accounts SET password=?, totp_secret=? WHERE id=?", (password, totp, row[0]))
            msg = "da SUA"
        else:
            base = {"email": email, "password": password, "totp_secret": totp}
            if "gmail_status" in cols: base["gmail_status"] = "unknown"
            if "health_status" in cols: base["health_status"] = "unknown"
            if "created_at" in cols: base["created_at"] = __import__("datetime").datetime.now().isoformat()
            keys = [k for k in base if k in cols]
            c.execute(f"INSERT INTO accounts ({','.join(keys)}) VALUES ({','.join('?'*len(keys))})",
                      [base[k] for k in keys])
            msg = "da THEM"
        c.commit(); c.close()
        return True, msg
    except Exception as e:
        return False, str(e)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["status", "prepare", "check", "reset", "probe", "checkcookies"])
    ap.add_argument("arg", nargs="?", default=None)
    ap.add_argument("--probe", action="store_true")
    a = ap.parse_args()
    if a.cmd == "status": cmd_status()
    elif a.cmd == "prepare": cmd_prepare(int(a.arg or 10), probe=a.probe)
    elif a.cmd == "check": cmd_check(a.arg)
    elif a.cmd == "reset": cmd_reset(a.arg or "all")
    elif a.cmd == "probe": cmd_probe(a.arg)
    elif a.cmd == "checkcookies": checkcookies_parallel(log=lambda m: print(m, flush=True))


if __name__ == "__main__":
    main()
