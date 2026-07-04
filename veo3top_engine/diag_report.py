"""
diag_report — ĐỌC log + /health -> BÁO CÁO SỨC KHỎE rõ ràng (để Claude/user "theo dõi" là biết ngay lỗi gì, ở đâu).
+ ROTATION: tự cắt log khi phình (giữ phần MỚI NHẤT) -> log không to vô tận (dọn rác).

Dùng:
  python diag_report.py            # in báo cáo sức khỏe video+ảnh
  python diag_report.py --rotate   # chỉ dọn log (cắt bớt nếu > cap)
  python diag_report.py --watch    # in báo cáo mỗi 30s

Thiết kế để đọc bằng mắt HOẶC bằng Claude: mỗi mục có nhãn rõ (SỐNG/nghỉ, loại lỗi, chẩn đoán).
"""
import os, sys, json, time, re, collections

_HERE = os.path.dirname(os.path.abspath(__file__))
_SUITE = os.path.dirname(_HERE)
VIDEO_LOG = os.path.join(_SUITE, ".veo3top_pool.log")
IMAGE_LOG = os.path.join(_SUITE, ".veo3top_imgpool.log")

ROTATE_MAX_MB = 12      # log > 12MB -> cắt
ROTATE_KEEP_MB = 6      # giữ lại 6MB cuối (phần mới nhất)


# ---------------- ROTATION (dọn rác) ----------------
def rotate_log(path, max_mb=ROTATE_MAX_MB, keep_mb=ROTATE_KEEP_MB):
    """Cắt log nếu > max_mb: giữ keep_mb CUỐI (phần mới nhất, lỗi gần đây). Trả (rotated, size_before)."""
    try:
        if not os.path.exists(path):
            return False, 0
        sz = os.path.getsize(path)
        if sz <= max_mb * 1024 * 1024:
            return False, sz
        keep = keep_mb * 1024 * 1024
        with open(path, "rb") as f:
            f.seek(-keep, os.SEEK_END)
            f.readline()               # bỏ dòng cắt dở đầu tiên
            tail = f.read()
        marker = f"===== [diag_report] LOG ROTATED {time.strftime('%Y-%m-%d %H:%M:%S')} (cat tu {sz//1024//1024}MB -> {keep_mb}MB, giu phan moi nhat) =====\n".encode()
        with open(path, "wb") as f:
            f.write(marker + tail)
        return True, sz
    except Exception as e:
        print(f"[rotate] loi {path}: {e}")
        return False, 0


def rotate_all():
    for p in (VIDEO_LOG, IMAGE_LOG):
        r, sz = rotate_log(p)
        if r:
            print(f"[rotate] {os.path.basename(p)}: {sz//1024//1024}MB -> {ROTATE_KEEP_MB}MB (giu phan moi)")


# ---------------- ĐỌC /health ----------------
def _health(port):
    try:
        import requests
        return requests.get(f"http://127.0.0.1:{port}/health", timeout=4).json()
    except Exception:
        return None


# ---------------- PARSE log (đếm lỗi + trạng thái) ----------------
def _tail_lines(path, n=4000):
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            return f.readlines()[-n:]
    except Exception:
        return []


def parse_video(lines):
    kinds = collections.Counter()
    warm_fail = collections.Counter()
    heal_fail = collections.Counter()   # account -> so lan chua KHONG thanh
    heal_ok = collections.Counter()
    last_pool = None
    last_keepwarm = None
    for ln in lines:
        # loai noise HTTP
        if "ConnectionReset" in ln or "socketserver" in ln or "Traceback" in ln:
            continue
        m = re.search(r"\[(recaptcha_quota|unusual|ratelimit|ip_block|auth)@", ln)
        if m: kinds[m.group(1)] += 1
        if "recaptcha_quota" in ln: kinds["recaptcha_quota_line"] += 1
        m = re.search(r"FAIL=([A-Z_]+)", ln)
        if m: warm_fail[m.group(1)] += 1
        m = re.search(r"VIDEO: (\S+@\S+) chữa KHÔNG thành", ln)
        if m: heal_fail[m.group(1)] += 1
        m = re.search(r"VIDEO: (\S+@\S+) ĐÃ CHỮA XONG", ln)
        if m: heal_ok[m.group(1)] += 1
        if "pool VIDEO:" in ln:
            last_pool = ln.split("pool VIDEO:")[1].strip()[:90]
        if "keep-warm VIDEO:" in ln:
            last_keepwarm = ln.split("keep-warm VIDEO:")[1].strip()[:90]
        if "Saved" in ln: kinds["saved_video"] += 1
    return {"kinds": kinds, "warm_fail": warm_fail, "heal_fail": heal_fail,
            "heal_ok": heal_ok, "last_pool": last_pool, "last_keepwarm": last_keepwarm}


def parse_image(lines):
    c = collections.Counter()
    for ln in lines:
        if re.search(r"\bok\b", ln, re.I) and ("tạo" in ln or "🎨" in ln): c["gen"] += 1
        if "reCAPTCHA" in ln: c["recaptcha"] += 1
        if "nghỉ" in ln and "winner" in ln: c["account_rest"] += 1
        if "ready qua COOKIE" in ln: c["ready_cookie"] += 1
    return c


# ---------------- BÁO CÁO ----------------
def report():
    print("=" * 64)
    print(f"  VE3 DIAG — {time.strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 64)

    # log size
    for p in (VIDEO_LOG, IMAGE_LOG):
        if os.path.exists(p):
            print(f"  log {os.path.basename(p)}: {os.path.getsize(p)//1024} KB")

    # ---- VIDEO ----
    hv = _health(8788)
    print("\n[VIDEO service :8788] " + ("UP" if hv else "TẮT"))
    if hv:
        accs = hv.get("accounts", [])
        alive = [a["name"] for a in accs if a.get("resting_in", 0) == 0]
        rest = [(a["name"], int(a.get("resting_in", 0))) for a in accs if a.get("resting_in", 0) > 0]
        busy = [a["name"] for a in accs if a.get("busy")]
        print(f"  uptime {hv.get('uptime_s',0)//60}m | done={hv.get('done')} fail={hv.get('fail')} | {hv.get('video_per_hour')}/h | queue={hv.get('queue')}")
        print(f"  SỐNG ({len(alive)}): {','.join(alive) or '-'}   busy: {','.join(busy) or '-'}")
        if rest:
            print(f"  nghỉ ({len(rest)}): " + ", ".join(f"{n}({s//60}m)" for n, s in rest))
        wins = [(a["name"], a.get("wins", 0)) for a in accs if a.get("wins", 0) > 0]
        if wins:
            print(f"  ĐÃ RA VIDEO: " + ", ".join(f"{n}={w}" for n, w in wins))

    pv = parse_video(_tail_lines(VIDEO_LOG))
    if pv["last_pool"]: print(f"  [startup] pool: {pv['last_pool']}")
    if pv["last_keepwarm"]: print(f"  [keep-warm] {pv['last_keepwarm']}")
    k = pv["kinds"]
    print(f"  lỗi submit (log gần đây): recaptcha_quota/unusual={k.get('recaptcha_quota',0)+k.get('unusual',0)} "
          f"ratelimit={k.get('ratelimit',0)} auth/401={k.get('auth',0)} | video Saved={k.get('saved_video',0)}")
    if pv["warm_fail"]:
        print(f"  warm FAIL (lý do): " + ", ".join(f"{r}×{n}" for r, n in pv["warm_fail"].most_common()))
    if pv["heal_fail"]:
        print(f"  chữa KHÔNG thành: " + ", ".join(f"{e.split('@')[0]}×{n}" for e, n in pv["heal_fail"].most_common(6)))
    if pv["heal_ok"]:
        print(f"  chữa XONG: " + ", ".join(f"{e.split('@')[0]}×{n}" for e, n in pv["heal_ok"].most_common(6)))

    # ---- IMAGE ----
    hi = _health(8789)
    print("\n[IMAGE service :8789] " + ("UP" if hi else "TẮT"))
    if hi and isinstance(hi, dict):
        for key in ("done", "fail", "alive", "accounts", "image_per_hour", "queue"):
            if key in hi: print(f"  {key}={hi[key]}", end="")
        print()

    # ---- CHẨN ĐOÁN tự động ----
    print("\n[CHẨN ĐOÁN]")
    diag = []
    if hv:
        accs = hv.get("accounts", [])
        n_alive = len([a for a in accs if a.get("resting_in", 0) == 0])
        if n_alive == 0:
            diag.append("❌ 0 account video SỐNG -> video KHÔNG chạy. Chờ keep-warm chữa (~1-2p) hoặc check cookie hết hạn.")
        elif n_alive < len(accs) // 2:
            diag.append(f"⚠ chỉ {n_alive}/{len(accs)} account sống -> throughput thấp. Nhiều account đang nghỉ/chữa.")
        else:
            diag.append(f"✓ {n_alive}/{len(accs)} account video sống.")
        rc = k.get("recaptcha_quota", 0) + k.get("unusual", 0)
        if rc > 50 and k.get("saved_video", 0) == 0:
            diag.append(f"❌ {rc} lỗi reCAPTCHA mà 0 video Saved -> token video trượt hết (check visible=True / IPv6 pool chai).")
        elif rc > 50:
            diag.append(f"⚠ reCAPTCHA trượt nhiều ({rc}) nhưng vẫn ra video -> rate thấp, retry đang gánh (IPv6 pool có thể chai).")
    if pv["warm_fail"].get("NO_PROJECT", 0) > 0:
        diag.append("⚠ có warm FAIL=NO_PROJECT -> nếu account thật sự khỏe (cookie sống) thì đã có HTTP fallback; nếu lặp nhiều = account bị Flow hạn chế thật.")
    if not hv:
        diag.append("• video service TẮT -> chưa có mã tới phase video, hoặc service chết (check .veo3top_pool.log).")
    for d in diag: print("  " + d)
    print("=" * 64)


if __name__ == "__main__":
    if "--rotate" in sys.argv:
        rotate_all(); sys.exit(0)
    rotate_all()   # luôn dọn rác trước khi báo cáo
    if "--watch" in sys.argv:
        while True:
            os.system("cls" if os.name == "nt" else "clear")
            report(); time.sleep(30)
    else:
        report()
