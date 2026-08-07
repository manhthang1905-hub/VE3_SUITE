"""
kho_phien — KÉO PHIÊN TỪ KHO SERVER VỀ MÁY, thay cho việc gõ lại mật khẩu.

═══════════════════════════════════════════════════════════════════════════════
SỐ ĐO 07/08/2026 — ĐỪNG GỠ KHỐI NÀY
═══════════════════════════════════════════════════════════════════════════════

Đo trên máy thật:

  - Kho trên server: **96 tài khoản `veo3_image` trạng thái `ready`** — server
    ĐANG GIỮ SẴN cookie + project của cả 96.
  - Kho cache cục bộ `<engine>/veo3top_engine/_auth_cache`: **65 bản ghi**. Cả
    65 đều ĐỦ `cookie` + `project`, đều mới (0–0,4 giờ). KHÔNG bản ghi nào hỏng.
  - **31 tài khoản có phiên hợp lệ TRÊN SERVER nhưng KHÔNG có file cache nào
    trên máy.**

Chuỗi nhân quả của 31 cái đó, trước bản vá này:

    Account.auth():
        d = self.cache._load(self.email)              # -> None vì KHÔNG CÓ FILE
        if not force and self.cache._fresh(d): ...    # -> bỏ qua
        if d and d.get("cookie") and d.get("project"):# -> False
            ...
        return None                                   # -> bị đọc là "phiên chết"

    _startup_check: None -> ok=False -> rest(1800) + recovery.submit(...)
    recover(): xoá sạch + ĐĂNG NHẬP LẠI BẰNG MẬT KHẨU + hâm nóng + mở Chrome.

Nhưng phiên của 31 tài khoản đó KHÔNG CHẾT. Nó nằm trên server, cách đó đúng
một lời gọi HTTP.

═══════════════════════════════════════════════════════════════════════════════
GỐC THẬT: HAI KHO PHIÊN, CHỈ CHẢY MỘT CHIỀU
═══════════════════════════════════════════════════════════════════════════════

Worker đăng nhập, thu phiên, đẩy LÊN server (`POST /admin/accounts/sessions`,
xem `workers/veo3/tools/session_ingest.py`; và `AccountBridge._write_auth_cache`
đổ ngược xuống đĩa CHO ĐÚNG ROSTER CA ĐÓ). Nhà máy thì chỉ đọc kho TRÊN MÁY.
Không có đường chảy ngược cho một tài khoản LẺ.

Nên mỗi khi máy thiếu một bản ghi — máy mới, đổi ổ, dọn dẹp, ca trước không mượn
tài khoản đó, hoặc nó đơn giản là chưa từng chạy trên máy này — nhà máy không đi
hỏi server mà đi GÕ LẠI MẬT KHẨU. Càng nạp đủ kho càng nhiều Chrome đăng nhập:
nạp 96 tài khoản LÊN SERVER, máy vẫn chỉ có 65, thành 31 lượt đăng nhập oan. Và
đăng nhập hàng loạt chính là thứ đẻ ra CAPTCHA.

═══════════════════════════════════════════════════════════════════════════════
THỨ TỰ BẮT BUỘC, TỪ RẺ TỚI ĐẮT — ĐỪNG BAO GIỜ NHẢY CÓC
═══════════════════════════════════════════════════════════════════════════════

  1. cache cục bộ còn hạn                     -> dùng          (auth_cache._fresh)
  2. cache cục bộ có cookie                   -> làm mới thẻ   (_refresh_from_cookie)
  3. KHO SERVER có phiên -> KÉO VỀ, ghi cache, dùng            ← FILE NÀY
  4. hết cách                                 -> mới đăng nhập bằng mật khẩu

═══════════════════════════════════════════════════════════════════════════════
HỢP ĐỒNG VỚI SERVER (endpoint đọc phiên theo email)
═══════════════════════════════════════════════════════════════════════════════

Tính tới 07/08/2026, `apps/api` CHƯA có endpoint chỉ-đọc trả `session_state` cho
một email. Thứ duy nhất trả phiên về là `POST /internal/v1/accounts/lease` —
nhưng đó là MƯỢN (có vòng đời lease/heartbeat/release), không dùng được cho việc
"nhà máy tự nhặt lại phiên của chính tài khoản nó đang giữ".

File này chờ một endpoint chỉ-đọc, cấu hình bằng biến môi trường nên KHÔNG phải
sửa mã khi server chốt đường dẫn:

    VEO3TOP_KHO_PHIEN_URL   mẫu URL, có `{email}` (đã url-encode hộ).
                            vd: https://api.shopapi.vn/admin/accounts/session?email={email}
    VEO3TOP_KHO_PHIEN_TOKEN khoá Bearer (mặc định đọc SHOPAPI_ADMIN_TOKEN)
    SHOPAPI_API_URL         nếu không đặt URL mẫu thì ghép với DUONG_MAC_DINH

Thân phản hồi chấp nhận mọi dạng dưới đây (khớp `AccountView` của worker và
CONTRACT 4.5, để không bắt bên nào đổi):

    {"session_state": {"cookie": "...", "project_id": "..."}}
    {"cookie": "...", "project_id": "..."}          (cấp cao nhất)
    {"items": [{"email": "...", "session_state": {...}}]}

CHỈ HTTP 200 MỚI LÀ CÂU TRẢ LỜI. 404/5xx/timeout đều là "KHÔNG HỎI ĐƯỢC" — vì
404 vừa có thể là "không có tài khoản này" vừa có thể là "endpoint chưa tồn
tại", và đoán sai chiều nào cũng dẫn tới đúng cái vòng xoáy đăng nhập oan.
"""
import json
import os
import threading
import time
import urllib.error
import urllib.parse
import urllib.request


# ── BA KẾT QUẢ (cùng tinh thần với `phien_kiem`) ────────────────────────────
#: Server trả 200 và CÓ phiên dùng được -> đã ghi xuống cache cục bộ.
CO_PHIEN = "co_phien"
#: Server trả 200 và nói rõ KHÔNG có phiên -> đây là bằng chứng, được phép
#: chuyển sang đường đắt nhất (đăng nhập bằng mật khẩu).
KHONG_CO_PHIEN = "khong_co_phien"
#: Không hỏi được (mạng, timeout, 404, 5xx, thân phản hồi lạ). KHÔNG phải bằng
#: chứng gì cả -> TUYỆT ĐỐI không đăng nhập lại vì cái này.
KHONG_HOI_DUOC = "khong_hoi_duoc"
#: Máy này không cấu hình kho server (chạy VE3_SUITE độc lập, không qua shopapi).
#: Khác hẳn "hỏi mà không được": ở đây KHÔNG CÓ KHO NÀO ĐỂ HỎI.
CHUA_CAU_HINH = "chua_cau_hinh"

#: Đường mặc định khi chỉ có `SHOPAPI_API_URL`. Đổi được bằng VEO3TOP_KHO_PHIEN_URL.
DUONG_MAC_DINH = "/admin/accounts/session?email={email}"

#: Hỏi hụt một email thì NGHỈ bấy nhiêu giây mới hỏi lại. `Account.auth()` được
#: gọi mỗi job; không có van này thì một account thiếu cookie sẽ bắn vào server
#: vài chục lần một phút — đúng cái lỗi "hỏi dồn" mà cả bản vá này đi chữa.
NGHI_HOI_LAI_S = int(os.environ.get("VEO3TOP_KHO_PHIEN_NGHI", "300") or "300")
HET_GIO_S = int(os.environ.get("VEO3TOP_KHO_PHIEN_TIMEOUT", "20") or "20")


def _env(*ten):
    for t in ten:
        v = (os.environ.get(t) or "").strip()
        if v:
            return v
    return ""


def mau_url():
    """Mẫu URL đọc phiên, hoặc chuỗi rỗng nếu máy này không cấu hình kho."""
    mau = _env("VEO3TOP_KHO_PHIEN_URL")
    if mau:
        return mau
    goc = _env("SHOPAPI_API_URL")
    if goc:
        return goc.rstrip("/") + DUONG_MAC_DINH
    return ""


def khoa():
    return _env("VEO3TOP_KHO_PHIEN_TOKEN", "SHOPAPI_ADMIN_TOKEN")


def da_cau_hinh():
    return bool(mau_url())


def doc_phien(payload, email=""):
    """Rút (cookie, project) từ thân phản hồi. Trả ("", "") nếu không có.

    Chấp nhận nhiều dạng vì hai bên đã tồn tại sẵn hai cách đặt tên: worker đọc
    `project_id`, còn `auth_cache` của engine ghi/đọc `project`. Ép một bên đổi
    tên chỉ để hàm này gọn hơn là đổi rủi ro lấy vẻ đẹp.
    """
    if not isinstance(payload, dict):
        return "", ""
    nut = payload
    items = payload.get("items") or payload.get("results")
    if isinstance(items, list) and items:
        nut = None
        for it in items:
            if not isinstance(it, dict):
                continue
            if not email or str(it.get("email") or "").strip().lower() == str(email).strip().lower():
                nut = it
                break
        if nut is None:
            return "", ""
    ss = nut.get("session_state")
    if not isinstance(ss, dict):
        ss = {}
    cookie = str(ss.get("cookie") or nut.get("cookie") or "")
    project = str(ss.get("project_id") or ss.get("project")
                  or nut.get("project_id") or nut.get("project") or "")
    return cookie, project


class KhoPhien:
    """Client CHỈ-ĐỌC tới kho phiên trên server. Một cái duy nhất, dùng chung.

    Cố ý KHÔNG dùng `curl_cffi`: đây là server của chính mình, không phải Google,
    không cần giả dấu tay TLS. `urllib` đủ và không thêm phụ thuộc.
    """

    def __init__(self, mau=None, token=None, timeout=None, log=None,
                 mo_url=None, dong_ho=time.time, nghi_hoi_lai=None):
        self._mau = mau
        self._token = token
        self.timeout = int(timeout or HET_GIO_S)
        self.log = log or (lambda *_: None)
        self._mo_url = mo_url          # tiêm được -> bài kiểm KHÔNG chạm mạng
        self.dong_ho = dong_ho
        self.nghi_hoi_lai = float(nghi_hoi_lai if nghi_hoi_lai is not None else NGHI_HOI_LAI_S)
        self._hut = {}                 # email -> ts lần hỏi hụt gần nhất
        self._khoa = threading.Lock()

    # -- cấu hình --
    def mau(self):
        return self._mau if self._mau is not None else mau_url()

    def token(self):
        return self._token if self._token is not None else khoa()

    def da_cau_hinh(self):
        return bool(self.mau())

    # -- van chống hỏi dồn --
    def _dang_nghi(self, email):
        with self._khoa:
            moc = self._hut.get(email)
        # KHÔNG có mốc = chưa hỏi hụt lần nào -> được hỏi. (Dùng `0` làm mặc định
        # là sai: với đồng hồ tiêm vào bắt đầu từ 0, mọi email đều trông như vừa
        # hỏi hụt xong và van khoá luôn lượt hỏi ĐẦU TIÊN.)
        if moc is None:
            return False
        return (self.dong_ho() - moc) < self.nghi_hoi_lai

    def _ghi_hut(self, email):
        with self._khoa:
            self._hut[email] = self.dong_ho()

    def _xoa_hut(self, email):
        with self._khoa:
            self._hut.pop(email, None)

    # -- gọi mạng --
    def _goi(self, url):
        """Trả (mã HTTP, thân đã phân tích) hoặc (0, None) nếu không gọi được."""
        req = urllib.request.Request(url, method="GET")
        tk = self.token()
        if tk:
            req.add_header("Authorization", f"Bearer {tk}")
        req.add_header("Accept", "application/json")
        mo = self._mo_url or urllib.request.urlopen
        try:
            with mo(req, timeout=self.timeout) as r:
                ma = getattr(r, "status", None) or getattr(r, "code", 200)
                raw = r.read().decode("utf-8", errors="replace")
        except urllib.error.HTTPError as e:
            return int(getattr(e, "code", 0) or 0), None
        except Exception:
            return 0, None
        try:
            return int(ma), json.loads(raw or "{}")
        except ValueError:
            return int(ma), None

    def lay(self, email):
        """Hỏi kho về MỘT email -> (kết quả, cookie, project). KHÔNG ghi đĩa."""
        if not self.da_cau_hinh():
            return CHUA_CAU_HINH, "", ""
        if self._dang_nghi(email):
            return KHONG_HOI_DUOC, "", ""
        url = self.mau().replace("{email}", urllib.parse.quote(str(email or ""), safe=""))
        ma, body = self._goi(url)
        if ma != 200 or body is None:
            self._ghi_hut(email)
            return KHONG_HOI_DUOC, "", ""
        cookie, project = doc_phien(body, email)
        if not cookie:
            # 200 mà không có cookie = KHO NÓI RÕ nó không giữ phiên nào dùng
            # được. Đây là câu trả lời, không phải sự im lặng.
            self._xoa_hut(email)
            return KHONG_CO_PHIEN, "", ""
        self._xoa_hut(email)
        return CO_PHIEN, cookie, project


#: Một client dùng chung cho cả tiến trình (van chống hỏi dồn phải dùng chung
#: mới có tác dụng — mỗi worker một client là mỗi worker một van).
_KHO = None
_KHOA_KHO = threading.Lock()


def kho_chung(log=None):
    global _KHO
    if _KHO is None:
        with _KHOA_KHO:
            if _KHO is None:
                _KHO = KhoPhien(log=log)
    return _KHO


def dat_kho_chung(kho):
    """Đặt client dùng chung (bài kiểm tiêm bản giả; đặt None để trả về mặc định)."""
    global _KHO
    with _KHOA_KHO:
        _KHO = kho


def keo_phien_ve(email, cache, kho=None, log=lambda *_: None):
    """BƯỚC 3: kho server có phiên -> kéo về, GHI XUỐNG CACHE CỤC BỘ, trả dict auth.

    Trả `(dict_auth_hoặc_None, kết_quả)`. Ghi xuống đĩa để lần sau khỏi hỏi lại —
    đó là nửa còn thiếu của đường chảy: kéo về mà không ghi thì mỗi lần khởi động
    lại là một trận hỏi dồn mới.

    `bearer=None, ts=0` là CỐ Ý và khớp đúng thứ `AccountBridge._write_auth_cache`
    của worker ghi: bearer Google chỉ sống ~30 phút (`auth_cache.BEARER_TTL`), gửi
    bearer cũ chỉ tổ dính 401. Engine thấy thiếu bearer sẽ tự làm mới TỪ COOKIE mà
    không mở Chrome — đúng đường nhanh nhất.
    """
    kho = kho or kho_chung(log=log)
    kq, cookie, project = kho.lay(email)
    if kq != CO_PHIEN:
        return None, kq
    d = {"bearer": None, "ts": 0, "cookie": cookie, "project": project or None, "email": email}
    try:
        cache._save(email, d)
    except Exception as e:
        # Ghi hụt thì vẫn TRẢ phiên ra dùng cho lượt này — mất một lần ghi đĩa
        # còn hơn đẩy một account khoẻ sang đường đăng nhập lại.
        log(f"kho_phien: kéo được phiên {email} nhưng ghi cache hụt ({type(e).__name__}: {e})")
    # KHÔNG BAO GIỜ log cookie: log bị dán vào chat, bị chụp màn hình, và một
    # cookie labs.google còn hạn là quyền truy cập đầy đủ vào tài khoản đó.
    log(f"kho_phien: KÉO PHIÊN TỪ KHO SERVER về cho {email} "
        f"(project {str(project)[:8] or 'chưa có'}) -> KHÔNG cần đăng nhập lại")
    return d, kq
