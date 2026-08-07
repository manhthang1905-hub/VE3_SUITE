"""
phien_kiem — KIỂM PHIÊN ĐĂNG NHẬP theo BA TRẠNG THÁI (dùng chung nhà máy ẢNH + VIDEO).

═══════════════════════════════════════════════════════════════════════════════
VÌ SAO CÓ FILE NÀY — SỰ CỐ 07/08/2026, ĐỪNG GỠ
═══════════════════════════════════════════════════════════════════════════════

Chủ dự án nói một câu đúng và nó là chìa khoá: *"làm gì có chuyện gmail lại out
được"*. Một phiên Google đã đăng nhập sống hàng tháng tới hàng năm. Đo được hôm
07/08/2026:

  - 98 hồ sơ Chrome trên đĩa, file cookie CÒN NGUYÊN, KHÔNG sót khoá `Singleton*`
    -> phiên KHÔNG hề mất, hồ sơ KHÔNG bị xoá.
  - `/health` của nhà máy ảnh: `logged_in` đi từ 8 -> 41 trong 30 phút. Đó là BỘ
    ĐẾM TIẾN TRÌNH KIỂM, không phải phán quyết sức khoẻ — nhưng cái TÊN của nó
    làm cả người lẫn máy đọc nhầm thành "chỉ 8 tài khoản đăng nhập được".
  - Nhật ký: mỗi ảnh mất 600–780 giây thay vì ~90.

Mã cũ ở `_startup_check` (cả hai nhà máy) như sau:

    try:
        auth = a.auth()
        ok = bool(auth) and am.validate_auth(auth)
    except Exception:
        ok = False                 # LỖI 1
    if not ok:
        a.rest(1800)               # LỖI 2
        self.recovery.submit(...)  # LỖI 2: ĐĂNG NHẬP LẠI BẰNG MẬT KHẨU

LỖI 1 — không phân biệt "chứng minh được là chết" với "lúc này không kiểm được".
`except Exception` gom mạng chập, quá hạn, DNS hỏng, 429, 5xx… thành "phiên
chết". Chỉ mã 401 (và 403 KÈM dấu hiệu phiên) mới là bằng chứng. Mọi thứ khác là
*không biết*, và không biết thì KHÔNG được kết án.

LỖI 2 — bản án quá nặng so với bằng chứng. `recover()` = xoá sạch + ĐĂNG NHẬP
LẠI BẰNG MẬT KHẨU + hâm nóng: hành động đắt nhất và rủi ro nhất trong cả hệ (tốn
hạn mức đăng nhập của Google, kéo theo CAPTCHA, account bị park).

VÒNG XOÁY TỰ NUÔI — thứ file này tồn tại để chặn:
    khởi động bắn 64 lượt validate CÙNG LÚC -> Google chặn tốc độ (429) -> nhiều
    account bị kết án "chết" oan -> mỗi cái nghỉ 30 phút VÀ đăng nhập lại -> kho
    co lại -> ảnh chậm 8 lần -> càng nhiều lượt đăng nhập -> Google nghi ngờ ->
    CAPTCHA -> account bị park -> lại càng ít account.

═══════════════════════════════════════════════════════════════════════════════
BỐN NGUYÊN TẮC MÃ Ở ĐÂY THI HÀNH
═══════════════════════════════════════════════════════════════════════════════

1. BA trạng thái, không phải hai: SONG / CHET_CHAC_CHAN / KHONG_KIEM_DUOC.
2. KHÔNG KIỂM ĐƯỢC thì THỬ LẠI có giãn cách; hết lượt thử vẫn không rõ thì để
   account ở trạng thái "chưa rõ" và CHO WORKER CỨ DÙNG. Nếu phiên thật sự chết
   thì lần gọi THẬT sẽ trả 401 — và ĐÓ mới là bằng chứng. Kiểm đầu giờ chỉ là
   GỢI Ý, không phải bản án.
3. Nghỉ phải NGẮN và TĂNG DẦN theo số lần hỏng liên tiếp, xoá ngay khi chạy lại
   được. Mỗi phút một account khoẻ bị bắt nghỉ oan là một phút công suất bị cắt
   của dịch vụ đang bán cho khách thật.
4. Đăng nhập lại phải CÓ HẠN MỨC. Đăng nhập lại 50 account cùng lúc là tự bắn
   vào chân: Google nhìn thấy đúng cái mẫu hành vi đó.
"""
import os
import time
import queue
import threading
from collections import OrderedDict, deque


# ── BA TRẠNG THÁI ────────────────────────────────────────────────────────────
#: Có bằng chứng phiên còn dùng được (máy chủ Google nhận bearer).
SONG = "song"
#: Có BẰNG CHỨNG phiên đã chết: 401, hoặc 403 kèm dấu hiệu phiên/credential.
#: CHỈ trạng thái này mới được phép dẫn tới đăng nhập lại bằng mật khẩu.
CHET_CHAC_CHAN = "chet_chac_chan"
#: KHÔNG KẾT LUẬN ĐƯỢC lúc này: mạng chập, quá hạn, DNS, 429, 5xx, HTML "Sorry"…
#: Đây KHÔNG phải lỗi của account. Không nghỉ, không đăng nhập lại, cứ giao việc.
KHONG_KIEM_DUOC = "khong_kiem_duoc"

BA_TRANG_THAI = (SONG, CHET_CHAC_CHAN, KHONG_KIEM_DUOC)


def _so_env(ten, mac_dinh):
    try:
        return type(mac_dinh)(os.environ.get(ten, mac_dinh) or mac_dinh)
    except (TypeError, ValueError):
        return mac_dinh


# ── SỐ LẦN THỬ LẠI KHI KHÔNG KIỂM ĐƯỢC ──────────────────────────────────────
#: Một lần trượt kiểm KHÔNG BAO GIỜ đủ để kết luận. 3 lượt vì: một lượt trượt
#: hay là do một gói tin rơi (thử lại ngay là xong); hai lượt trượt liên tiếp
#: thường là Google đang chặn tốc độ cả cụm (giãn cách vài giây là qua); ba lượt
#: trượt cách nhau tới ~20 giây mà vẫn không có lấy MỘT phản hồi phân loại được
#: thì vấn đề nằm ngoài account (mạng nhà, DNS, WARP) — và lúc đó đăng nhập lại
#: cũng KHÔNG cứu được gì, nên vẫn không đăng nhập lại. Số này chỉ để tránh kết
#: án oan, không phải để "cố cho ra kết quả".
SO_LAN_THU_KIEM = _so_env("VEO3TOP_KIEM_SO_LAN_THU", 3)

#: Giãn cách giữa các lượt thử (giây). Tăng dần: gói tin rơi thì 2 giây là đủ,
#: còn 429 cả cụm thì cần lâu hơn — bắn lại ngay chỉ làm 429 nặng thêm.
GIAN_CACH_THU = (2.0, 6.0, 15.0)


# ── GIỚI HẠN SỐ LƯỢT KIỂM CHẠY SONG SONG ────────────────────────────────────
#: 07/08/2026 khởi động bắn 64 POST validate CÙNG LÚC từ MỘT IP. Google chặn tốc
#: độ, mã cũ đọc 429 thành "phiên chết" -> tự tạo ra thảm hoạ rồi tự tin vào nó.
#: 4 lượt song song + giãn cách nhỏ: 64 account kiểm xong trong ~10-20 giây, đủ
#: nhanh mà KHÔNG trông giống một cái bot quét.
KIEM_SONG_SONG = _so_env("VEO3TOP_KIEM_SONG_SONG", 4)
#: Nghỉ giữa hai lượt kiểm TRONG CÙNG một luồng (giây) -> trải đều theo thời gian.
GIAN_CACH_KIEM = _so_env("VEO3TOP_KIEM_GIAN_CACH", 0.4)

#: TRẦN THỜI GIAN cho CẢ đợt kiểm. Hết giờ thì thôi chờ, KHÔNG phải huỷ: luồng
#: nào chưa xong cứ chạy nốt ở nền và ghi kết quả sau. Có trần vì kiểm đầu giờ
#: chặn `start()`, mà account chưa kiểm xong thì đằng nào cũng được giao việc —
#: bắt cả nhà máy đứng chờ một phép kiểm là tự cắt công suất lần nữa.
KIEM_TRAN_GIAY = _so_env("VEO3TOP_KIEM_TRAN_GIAY", 180.0)


# ── MỨC NGHỈ: NGẮN, TĂNG DẦN, CÓ TRẦN ───────────────────────────────────────
#: Mức nghỉ ĐẦU TIÊN. `rest(1800)` cũ sai theo CẢ HAI chiều: quá nặng cho account
#: chỉ chập một lần (cắt 30 phút công suất của dịch vụ đang bán), mà lại không đủ
#: cho account hỏng thật (chữa xong sớm vẫn phải chờ hết 30 phút nếu không ai xoá).
NGHI_DAU_S = _so_env("VEO3TOP_NGHI_DAU", 45)
#: Hỏng liên tiếp thì nhân dần lên: 45s -> 135s -> 405s -> 900s (trần).
NHAN_NGHI = _so_env("VEO3TOP_NGHI_NHAN", 3.0)
#: Trần nghỉ. Quá mức này thì account đằng nào cũng đang xếp hàng chờ chữa; bắt
#: nghỉ lâu hơn chỉ giấu nó khỏi mắt người vận hành.
NGHI_TRAN_S = _so_env("VEO3TOP_NGHI_TRAN", 900)


# ── HẠN MỨC ĐĂNG NHẬP LẠI ───────────────────────────────────────────────────
#: Tối đa bấy nhiêu lượt ĐĂNG NHẬP LẠI BẰNG MẬT KHẨU trong một cửa sổ thời gian.
#: Phần còn lại XẾP HÀNG. Đây là cái van chặn "50 account cùng hỏng -> 50 lượt
#: đăng nhập" — mẫu hành vi mà Google trả lời bằng CAPTCHA.
CHUA_MOI_LUOT = _so_env("VEO3TOP_CHUA_MOI_LUOT", 3)
CHUA_CUA_SO_S = _so_env("VEO3TOP_CHUA_CUA_SO", 600)
#: Chu kỳ luồng nền rút hàng đợi chữa (giây).
CHUA_CHU_KY_S = _so_env("VEO3TOP_CHUA_CHU_KY", 30)


# ── dấu hiệu 403 THẬT SỰ là chuyện phiên/credential ──────────────────────────
# 403 KHÔNG mặc nhiên là phiên chết. Ở hệ này 403 hay gặp hai kiểu KHÁC HẲN:
#   - 403 + HTML "Sorry" của Google edge = CHẶN THEO IP (xem flow_client.classify
#     -> 'ip_block'). Đổi IP là qua. Đăng nhập lại KHÔNG cứu gì, chỉ đốt hạn mức.
#   - 403 PERMISSION_DENIED về project = lỗi CẤP PROJECT (xem project_pool
#     .is_project_error). Đổi project là qua.
# Nên chỉ coi 403 là phiên chết khi thân phản hồi nói thẳng về credential.
DAU_HIEU_PHIEN_CHET = (
    "UNAUTHENTICATED",
    "INVALID_AUTHENTICATION",
    "INVALID AUTHENTICATION",
    "ACCESS_TOKEN_EXPIRED",
    "CREDENTIALS_MISSING",
    "AUTHENTICATION_CREDENTIALS",
    "INVALID_GRANT",
    "TOKEN_EXPIRED",
)


def phan_loai_http(status, body=""):
    """Mã HTTP (+ thân phản hồi) -> (trạng thái, ghi chú). KHÔNG nuốt mã như trước.

    Căn cứ đã đo của dự án (xem `account_manager.validate_auth`, `flow_client
    .classify`): phép kiểm bắn một POST T2V với token reCAPTCHA GIẢ, nên
    - 200/201/400: request ĐÃ QUA lớp xác thực rồi mới bị từ chối vì token giả
      -> bearer SỐNG.
    - 401: bearer chết. Đây là bằng chứng DUY NHẤT chắc chắn.
    - 403: xem `DAU_HIEU_PHIEN_CHET` — mặc định KHÔNG kết án.
    - 429 / 5xx / còn lại: KHÔNG KẾT LUẬN.
    """
    try:
        status = int(status or 0)
    except (TypeError, ValueError):
        status = 0
    txt = str(body or "")
    hoa = txt.upper()
    if status in (200, 201, 400):
        return SONG, f"HTTP {status} (qua được lớp xác thực)"
    if status == 401:
        return CHET_CHAC_CHAN, "HTTP 401 UNAUTHENTICATED - bearer chết"
    if status == 403:
        for d in DAU_HIEU_PHIEN_CHET:
            if d in hoa:
                return CHET_CHAC_CHAN, f"HTTP 403 + {d} - phiên chết"
        return KHONG_KIEM_DUOC, "HTTP 403 nhưng KHÔNG có dấu hiệu phiên (chặn IP / lỗi project) - không kết án"
    if status == 429:
        return KHONG_KIEM_DUOC, "HTTP 429 bị chặn tốc độ - KHÔNG phải phiên chết"
    if 500 <= status <= 599:
        return KHONG_KIEM_DUOC, f"HTTP {status} lỗi máy chủ - KHÔNG phải phiên chết"
    return KHONG_KIEM_DUOC, f"HTTP {status} không phân loại được"


def phan_loai_ngoai_le(exc):
    """Ngoại lệ (mạng chập, quá hạn, DNS, TLS…) -> LUÔN là KHÔNG KIỂM ĐƯỢC.

    Đây chính là LỖI 1 của 07/08/2026: `except Exception: ok = False`.
    """
    return KHONG_KIEM_DUOC, f"{type(exc).__name__}: {str(exc)[:100]}"


# ── kiểm MỘT account ────────────────────────────────────────────────────────
def _cookie_trong_kho(account):
    """Cookie đang lưu trong `_auth_cache` của account (chuỗi) hoặc None."""
    try:
        d = account.cache._load(account.email)
    except Exception:
        return None
    if not d:
        return None
    return d.get("cookie") or None


def _thieu_cache_cuc_bo(account, am_mod, kp_mod):
    """Máy KHÔNG có bản ghi cache cho account này -> HỎI KHO SERVER trước.

    ═══ ĐÂY LÀ CHỖ ĐẺ RA 31 LƯỢT ĐĂNG NHẬP OAN HÔM 07/08/2026 ═══

    Đo được: server giữ 96 tài khoản `veo3_image` trạng thái `ready`, máy chỉ có
    65 bản ghi cache — 31 tài khoản CÓ PHIÊN HỢP LỆ TRÊN SERVER mà không có file
    nào trên máy. Mã cũ đọc "không có file" thành "phiên chết" rồi đi gõ lại mật
    khẩu 31 lần. Phiên của chúng nằm trên server, cách đó đúng một lời gọi HTTP.

    Bốn ngả, và ba trong bốn ngả KHÔNG được đăng nhập lại:
      CO_PHIEN       -> đã kéo về + ghi cache -> chạy lại `auth()` để dùng luôn.
      KHONG_HOI_DUOC -> im lặng KHÔNG phải bằng chứng -> CHƯA RÕ, để yên.
      KHONG_CO_PHIEN -> kho TRẢ LỜI là không có -> đây mới là bằng chứng.
      CHUA_CAU_HINH  -> máy chạy VE3_SUITE độc lập, KHÔNG có kho nào để hỏi ->
                        giữ nguyên hành vi cũ (kết án), vì ở chế độ đó thật sự
                        không còn đường nào khác ngoài đăng nhập lại.
    """
    d, kq = kp_mod.keo_phien_ve(account.email, account.cache)
    if kq == kp_mod.CO_PHIEN:
        try:
            auth2 = account.auth()
        except Exception as e:
            return phan_loai_ngoai_le(e)
        if auth2 and auth2.get("bearer") and auth2.get("project"):
            return am_mod.validate_auth_chi_tiet(auth2)
        return KHONG_KIEM_DUOC, "đã kéo phiên từ kho server về nhưng chưa dựng được bearer - chưa kết luận"
    if kq == kp_mod.KHONG_HOI_DUOC:
        return KHONG_KIEM_DUOC, "không hỏi được kho phiên trên server - KHÔNG phải bằng chứng phiên chết"
    if kq == kp_mod.KHONG_CO_PHIEN:
        return CHET_CHAC_CHAN, "máy không có cache VÀ kho server trả lời là KHÔNG có phiên"
    return CHET_CHAC_CHAN, "máy không có cookie nào và máy này không cấu hình kho phiên server"


def kiem_mot_lan(account, am_mod=None, fc_mod=None, kp_mod=None, force=False):
    """Kiểm MỘT lượt -> (trạng thái, ghi chú). KHÔNG nghỉ, KHÔNG chữa, KHÔNG ghi trạng thái.

    Đường đi:
      1) `account.auth()` — refresh bearer TỪ COOKIE, không mở Chrome.
         Ném ngoại lệ -> KHÔNG KIỂM ĐƯỢC (mạng, không phải account).
      2) Có bearer+project -> hỏi máy chủ thật qua `validate_auth_chi_tiet`
         (giữ nguyên mã HTTP, xem `phan_loai_http`).
      3) `auth()` trả None và máy KHÔNG có cookie -> HỎI KHO SERVER
         (`_thieu_cache_cuc_bo`), KHÔNG đi thẳng tới đăng nhập lại.
      4) `auth()` trả None nhưng máy CÓ cookie — CHỖ NÀY LÀ NƠI SINH RA OAN SAI
         CŨ. `auth()` trả None cho CẢ HAI trường hợp "cookie hết hạn thật" LẪN
         "gọi endpoint session bị 429 / mạng chập". Mã cũ đọc None thành "chết".
         Ở đây hỏi tiếp `flow_client.cookie_liveness` — hàm đó ĐÃ phân biệt sẵn
         ba mức alive/dead/transient (nó ra đời từ đúng sự cố churn 96->77).
    """
    if fc_mod is None:
        import flow_client as fc_mod
    if am_mod is None:
        import account_manager as am_mod
    if kp_mod is None:
        import kho_phien as kp_mod
    try:
        # `force=True` = ép làm mới thẻ TỪ COOKIE (vòng keep-warm của nhà máy
        # video dùng nó để giữ phiên ấm giữa hai đợt video, chứ không phải để
        # kiểm gắt hơn).
        auth = account.auth(force=True) if force else account.auth()
    except Exception as e:
        return phan_loai_ngoai_le(e)

    if auth and auth.get("bearer") and auth.get("project"):
        return am_mod.validate_auth_chi_tiet(auth)

    cookie = _cookie_trong_kho(account)
    if not cookie:
        return _thieu_cache_cuc_bo(account, am_mod, kp_mod)
    try:
        muc = fc_mod.cookie_liveness(cookie)
    except Exception as e:
        return phan_loai_ngoai_le(e)
    if muc == "dead":
        return CHET_CHAC_CHAN, "cookie_liveness=dead (200 nhưng không token / expires quá khứ)"
    if muc == "alive":
        # Cookie SỐNG mà không dựng nổi bearer/project -> trục trặc nhất thời
        # (hoặc thiếu project, thứ tự chữa được bằng warm chứ không cần mật khẩu).
        return KHONG_KIEM_DUOC, "cookie SỐNG nhưng chưa dựng được bearer/project - chưa kết luận"
    return KHONG_KIEM_DUOC, f"cookie_liveness={muc} - không kết luận"


def kiem_co_thu_lai(account, so_lan=None, ngu=time.sleep, am_mod=None, fc_mod=None,
                    kp_mod=None, gian_cach=GIAN_CACH_THU, log=None, force=False):
    """Kiểm CÓ THỬ LẠI -> (trạng thái, ghi chú, số lượt đã thử).

    Chỉ THỬ LẠI khi KHÔNG KIỂM ĐƯỢC. SỐNG hay CHẾT CHẮC CHẮN đều là kết luận có
    bằng chứng -> dừng ngay, không tốn thêm request.

    Một lần trượt kiểm KHÔNG được dẫn tới bất kỳ hình phạt nào — đó là yêu cầu
    trực tiếp của chủ dự án sau sự cố 07/08/2026.
    """
    n = int(so_lan or SO_LAN_THU_KIEM)
    tt, ghi_chu = KHONG_KIEM_DUOC, "chưa chạy"
    for i in range(max(1, n)):
        tt, ghi_chu = kiem_mot_lan(account, am_mod=am_mod, fc_mod=fc_mod, kp_mod=kp_mod,
                                   force=force)
        if tt != KHONG_KIEM_DUOC:
            return tt, ghi_chu, i + 1
        if i < n - 1:
            if log:
                log(f"kiểm {getattr(account, 'email', '?')} lượt {i + 1}/{n} chưa kết luận ({ghi_chu}) -> thử lại")
            cho = gian_cach[min(i, len(gian_cach) - 1)] if gian_cach else 0
            if cho:
                ngu(cho)
    return tt, ghi_chu, max(1, n)


# ── NGHỈ: ngắn, tăng dần, có trần ───────────────────────────────────────────
def nghi_bao_lau(so_lan_hong_lien_tiep):
    """Mức nghỉ (giây) theo số lần hỏng LIÊN TIẾP. 1->45s, 2->135s, 3->405s, >=4->900s."""
    try:
        k = max(1, int(so_lan_hong_lien_tiep))
    except (TypeError, ValueError):
        k = 1
    return min(float(NGHI_TRAN_S), float(NGHI_DAU_S) * (float(NHAN_NGHI) ** (k - 1)))


def nghi_tang_dan(account):
    """Cho account nghỉ NGẮN, mức tăng dần theo `rest_streak`. Trả số giây đã cho nghỉ.

    `Account.rest()` tự tăng `rest_streak`, `Account.clear_rest()` đưa nó về 0 —
    nên "chạy lại được một lần thì mức nghỉ về mức đầu" là hệ quả tự nhiên, miễn
    là mọi đường thành công đều gọi `clear_rest()`.
    """
    giay = nghi_bao_lau(int(getattr(account, "rest_streak", 0)) + 1)
    account.rest(giay)
    return giay


# ── GIỚI HẠN SỐ LƯỢT KIỂM SONG SONG ─────────────────────────────────────────
def chay_song_song(danh_sach, ham, so_song_song=None, gian_cach=None, ngu=time.sleep,
                   timeout=None):
    """Chạy `ham(x)` cho từng phần tử, TỐI ĐA `so_song_song` cái cùng lúc.

    Trả (số phần tử đã chạy, ĐỈNH số lượt chạy đồng thời). Trả về đỉnh để bài
    kiểm KHẲNG ĐỊNH ĐƯỢC là không còn cảnh 64 POST một phát — thứ đã tự tạo ra
    429 rồi tự diễn giải 429 thành "phiên chết" hôm 07/08/2026.
    """
    muc = list(danh_sach)
    if not muc:
        return 0, 0
    n = max(1, int(so_song_song if so_song_song is not None else KIEM_SONG_SONG))
    cach = GIAN_CACH_KIEM if gian_cach is None else gian_cach
    hang = queue.Queue()
    for x in muc:
        hang.put(x)
    trang_thai = {"hien": 0, "dinh": 0, "xong": 0}
    khoa = threading.Lock()

    def _tho():
        while True:
            try:
                x = hang.get_nowait()
            except queue.Empty:
                return
            with khoa:
                trang_thai["hien"] += 1
                trang_thai["dinh"] = max(trang_thai["dinh"], trang_thai["hien"])
            try:
                ham(x)
            except Exception:
                pass
            finally:
                with khoa:
                    trang_thai["hien"] -= 1
                    trang_thai["xong"] += 1
            if cach:
                ngu(cach)      # trải đều theo thời gian, không dồn cục

    ths = [threading.Thread(target=_tho, daemon=True) for _ in range(min(n, len(muc)))]
    for t in ths:
        t.start()
    # TRẦN CHUNG cho cả đợt, không phải trần cho từng luồng: `join(timeout=X)`
    # lặp qua n luồng có thể chờ tới n*X. Luồng chưa xong vẫn chạy tiếp ở nền.
    tran = KIEM_TRAN_GIAY if timeout is None else timeout
    han = (time.time() + float(tran)) if tran else None
    for t in ths:
        t.join(timeout=max(0.0, han - time.time()) if han else None)
    return trang_thai["xong"], trang_thai["dinh"]


# ── HẠN MỨC ĐĂNG NHẬP LẠI ───────────────────────────────────────────────────
class HanMucChua:
    """Van chặn số lượt ĐĂNG NHẬP LẠI BẰNG MẬT KHẨU trong một cửa sổ thời gian.

    `RecoveryManager` đã bảo đảm mỗi lúc chỉ MỘT Chrome login — nhưng nó chạy
    liên tục hết hàng đợi. 50 account hỏng cùng lúc vẫn thành 50 lượt đăng nhập
    nối đuôi nhau trong ít phút, và đó ĐÚNG là mẫu hành vi Google trả lời bằng
    CAPTCHA. Van này rải chúng ra theo thời gian; phần vượt hạn mức XẾP HÀNG chứ
    không bị bỏ.
    """

    def __init__(self, so_luot=None, cua_so_s=None, log=None, dong_ho=time.time,
                 tu_chay=True, chu_ky=None, ngu=time.sleep):
        self.so_luot = int(so_luot if so_luot is not None else CHUA_MOI_LUOT)
        self.cua_so_s = float(cua_so_s if cua_so_s is not None else CHUA_CUA_SO_S)
        self.log = log or (lambda *_: None)
        self.dong_ho = dong_ho
        self.ngu = ngu
        self.chu_ky = float(chu_ky if chu_ky is not None else CHUA_CHU_KY_S)
        self._moc = deque()             # mốc thời gian các lượt đã cho phép
        self._hang = OrderedDict()      # email -> hàm chữa (dedup theo email)
        self._khoa = threading.Lock()
        self._tu_chay = bool(tu_chay)
        self._luong = None
        self._dung = False

    # -- nội bộ --
    def _don(self, bay_gio):
        gioi_han = bay_gio - self.cua_so_s
        while self._moc and self._moc[0] <= gioi_han:
            self._moc.popleft()

    def con_luot(self):
        """Còn bao nhiêu lượt đăng nhập lại được phép ngay bây giờ."""
        with self._khoa:
            self._don(self.dong_ho())
            return max(0, self.so_luot - len(self._moc))

    def cho_doi(self):
        with self._khoa:
            return len(self._hang)

    def xin(self):
        """Xin MỘT lượt. True = được phép đăng nhập lại NGAY."""
        with self._khoa:
            bay_gio = self.dong_ho()
            self._don(bay_gio)
            if len(self._moc) >= self.so_luot:
                return False
            self._moc.append(bay_gio)
            return True

    def xep_hang(self, khoa, ham):
        """Xin lượt cho `khoa` (email). Được thì chạy `ham()` NGAY và trả "chay";
        hết hạn mức thì cất vào hàng đợi và trả "cho" (sẽ chạy khi cửa sổ nhả)."""
        with self._khoa:
            if khoa in self._hang:
                return "cho"           # đã xếp hàng rồi, không nhân đôi
        if self.xin():
            ham()
            return "chay"
        with self._khoa:
            self._hang[khoa] = ham
        self.log(f"hạn mức đăng nhập lại đã đầy ({self.so_luot}/{int(self.cua_so_s)}s) -> "
                 f"{khoa} XẾP HÀNG (đang chờ {len(self._hang)})")
        self._bat_luong()
        return "cho"

    def chay_hang_doi(self):
        """Rút hàng đợi trong phạm vi hạn mức còn lại. Trả số lượt đã cho chạy."""
        n = 0
        while True:
            with self._khoa:
                if not self._hang:
                    return n
            if not self.xin():
                return n
            with self._khoa:
                if not self._hang:
                    return n
                khoa, ham = self._hang.popitem(last=False)
            try:
                ham()
                n += 1
                self.log(f"tới lượt {khoa} -> đăng nhập lại (còn chờ {self.cho_doi()})")
            except Exception as e:
                self.log(f"chữa {khoa} lỗi: {type(e).__name__}: {e}")

    # -- luồng nền rút hàng đợi --
    def _bat_luong(self):
        if not self._tu_chay or self._luong is not None:
            return
        self._luong = threading.Thread(target=self._vong, daemon=True)
        self._luong.start()

    def _vong(self):
        while not self._dung:
            self.ngu(self.chu_ky)
            if self._dung:
                return
            try:
                self.chay_hang_doi()
            except Exception:
                pass

    def stop(self):
        self._dung = True


# ── kiểm CẢ ROSTER (dùng chung nhà máy ẢNH và VIDEO) ────────────────────────
def kiem_roster(accounts, submit_chua, log=lambda *_: None, han_muc=None, nhan="POOL",
                so_song_song=None, gian_cach=None, ngu=time.sleep, kiem=None,
                timeout=None, ghi_trang_thai=True):
    """Kiểm một nhóm account theo ĐÚNG bốn nguyên tắc ở đầu file. Trả dict đếm.

    `submit_chua(account)` — đưa account vào chữa NỀN (RecoveryManager). CHỈ được
    gọi cho account CHẾT CHẮC CHẮN, và chỉ khi `han_muc` còn lượt.

    Kết cục theo từng trạng thái:
      SONG            -> `clear_rest()`: chạy được thì bộ đếm hỏng-liên-tiếp về 0.
      CHET_CHAC_CHAN  -> nghỉ NGẮN tăng dần + xin lượt đăng nhập lại (có hạn mức).
      KHONG_KIEM_DUOC -> KHÔNG nghỉ, KHÔNG đăng nhập lại. Account ở trạng thái
                         "chưa rõ" và worker VẪN giao việc. Nếu phiên chết thật
                         thì lần gọi thật trả 401 — ĐÓ mới là bằng chứng.
    """
    kiem = kiem or kiem_co_thu_lai
    han_muc = han_muc if han_muc is not None else HanMucChua(log=log)
    dem = {"song": [], "chet_chac_chan": [], "khong_kiem_duoc": [], "cho_chua": []}
    khoa = threading.Lock()

    def _mot(a):
        tt, ghi_chu, so_lan = kiem(a, ngu=ngu)
        if ghi_trang_thai:
            a.trang_thai_phien = tt
        if tt == SONG:
            a.clear_rest()                       # chạy được -> xoá nghỉ + streak về 0
            with khoa:
                dem["song"].append(a)
            return
        if tt == CHET_CHAC_CHAN:
            giay = nghi_tang_dan(a)              # nghỉ NGẮN, tăng dần — KHÔNG phải 1800s cứng
            with khoa:
                dem["chet_chac_chan"].append(a)
            kq = han_muc.xep_hang(a.email, lambda a=a: submit_chua(a))
            if kq == "cho":
                with khoa:
                    dem["cho_chua"].append(a)
            log(f"{nhan}: {a.email} CHẾT CHẮC CHẮN sau {so_lan} lượt kiểm ({ghi_chu}) -> "
                f"nghỉ {int(giay)}s + {'đăng nhập lại NGAY' if kq == 'chay' else 'XẾP HÀNG đăng nhập lại'}")
            return
        # KHÔNG KIỂM ĐƯỢC — không kết án, không phạt.
        with khoa:
            dem["khong_kiem_duoc"].append(a)
        log(f"{nhan}: {a.email} CHƯA RÕ sau {so_lan} lượt kiểm ({ghi_chu}) -> "
            f"KHÔNG nghỉ, KHÔNG đăng nhập lại, vẫn giao việc (401 lúc chạy thật mới là bằng chứng)")

    xong, dinh = chay_song_song(accounts, _mot, so_song_song=so_song_song,
                                gian_cach=gian_cach, ngu=ngu, timeout=timeout)
    kq = {
        "tong": len(list(accounts)),
        "da_kiem": xong,
        "song": len(dem["song"]),
        "chet_chac_chan": len(dem["chet_chac_chan"]),
        "khong_kiem_duoc": len(dem["khong_kiem_duoc"]),
        "cho_chua": len(dem["cho_chua"]),
        "dinh_song_song": dinh,
    }
    log(f"pool {nhan}: kiểm {kq['da_kiem']}/{kq['tong']} account -> {kq['song']} SỐNG, "
        f"{kq['chet_chac_chan']} CHẾT CHẮC CHẮN (đang chữa nền, {kq['cho_chua']} xếp hàng), "
        f"{kq['khong_kiem_duoc']} CHƯA RÕ (vẫn chạy). Đỉnh kiểm song song {dinh}.")
    return kq


# ── đếm cho /health ─────────────────────────────────────────────────────────
def dem_trang_thai(accounts):
    """Đếm roster theo BA trạng thái + hai số dẫn xuất, TÊN KHÔNG THỂ ĐỌC NHẦM.

    07/08/2026 `/health` có đúng một khoá `logged_in`, và nó là BỘ ĐẾM TIẾN TRÌNH
    KIỂM (8 -> 41 trong 30 phút) chứ không phải số account đăng nhập được. Người
    đọc — kể cả người viết mã — hiểu nó thành "chỉ 8/96 account đăng nhập được"
    rồi báo sai cho chủ dự án. Từ nay KHÔNG con số nào mang hai nghĩa:

      phien_song            = ĐÃ KIỂM và có bằng chứng SỐNG
      phien_chet_chac_chan  = ĐÃ KIỂM và có bằng chứng CHẾT (401/403-phiên)
      phien_chua_kiem       = CHƯA có kết luận (chưa kiểm, hoặc kiểm không nổi)
      phien_da_kiem         = song + chet_chac_chan  (mẫu số của phép kiểm)
      phien_dung_duoc       = song + chua_kiem       (số account worker thực giao việc)
    """
    ds = list(accounts)
    song = sum(1 for a in ds if getattr(a, "trang_thai_phien", KHONG_KIEM_DUOC) == SONG)
    chet = sum(1 for a in ds if getattr(a, "trang_thai_phien", KHONG_KIEM_DUOC) == CHET_CHAC_CHAN)
    chua = len(ds) - song - chet
    return {
        "accounts": len(ds),
        "phien_song": song,
        "phien_chet_chac_chan": chet,
        "phien_chua_kiem": chua,
        "phien_da_kiem": song + chet,
        "phien_dung_duoc": song + chua,
    }
