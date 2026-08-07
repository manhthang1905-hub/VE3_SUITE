"""Bộ kiểm cho `veo3top_engine/kho_phien.py` — KÉO PHIÊN VỀ THAY VÌ GÕ LẠI MẬT KHẨU.

═══════════════════════════════════════════════════════════════════════════════
SỐ ĐO 07/08/2026 MÀ BỘ KIỂM NÀY GHIM LẠI
═══════════════════════════════════════════════════════════════════════════════

  - Kho trên server: 96 tài khoản `veo3_image` trạng thái `ready` (đủ cookie +
    project).
  - Kho cache cục bộ `_auth_cache`: 65 bản ghi, cả 65 đều đủ cookie + project,
    đều mới (0–0,4 giờ), KHÔNG bản ghi nào hỏng.
  - 31 tài khoản có phiên hợp lệ TRÊN SERVER mà KHÔNG có file nào trên máy.

Trước bản vá, 31 cái đó rơi vào `Account.auth() -> None -> "phiên chết" ->
rest(1800) + ĐĂNG NHẬP LẠI BẰNG MẬT KHẨU`. Đăng nhập hàng loạt là thứ đẻ ra
CAPTCHA. Phiên của chúng nằm trên server, cách đó đúng một lời gọi HTTP.

KHÔNG bài nào ở đây chạm mạng thật: mọi lời gọi đi qua `mo_url` giả tiêm vào.
"""

from __future__ import annotations

import io
import json
import urllib.error

import pytest

import kho_phien as kp
import phien_kiem as pk
from test_phien_kiem import KhoGia, TaiKhoanGia, AmGia, FcGia, khong_ngu, kiem_gia


MAU = "https://api.test.local/admin/accounts/session?email={email}"


class PhanHoiGia(io.BytesIO):
    """Đủ giống thứ `urllib.request.urlopen` trả về để dùng trong `with`."""

    def __init__(self, status=200, body=None):
        super().__init__(json.dumps(body if body is not None else {}).encode("utf-8"))
        self.status = status

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def mo_url_gia(status=200, body=None, nem=None, ghi=None):
    def _mo(req, timeout=None):
        if ghi is not None:
            ghi.append(req.full_url)
        if nem is not None:
            raise nem
        return PhanHoiGia(status, body)
    return _mo


def kho(status=200, body=None, nem=None, ghi=None, mau=MAU, **kw):
    return kp.KhoPhien(mau=mau, token="tk", mo_url=mo_url_gia(status, body, nem, ghi), **kw)


# ── 1. Đọc được mọi khuôn thân phản hồi đang tồn tại ────────────────────────

@pytest.mark.parametrize("body", [
    {"session_state": {"cookie": "CK", "project_id": "P1"}},
    {"session_state": {"cookie": "CK", "project": "P1"}},
    {"cookie": "CK", "project_id": "P1"},
    {"items": [{"email": "a@gmail.com", "session_state": {"cookie": "CK", "project_id": "P1"}}]},
])
def test_doc_phien_chap_nhan_moi_khuon(body):
    """Worker đọc `project_id`, `auth_cache` của engine ghi `project`. Hai tên cho
    cùng một thứ đã tồn tại sẵn ở hai lớp mã — nhận cả hai, đừng bắt bên nào đoán."""
    assert kp.doc_phien(body, "a@gmail.com") == ("CK", "P1")


def test_doc_phien_lay_dung_email_trong_danh_sach():
    body = {"items": [{"email": "khac@gmail.com", "session_state": {"cookie": "SAI"}},
                      {"email": "a@gmail.com", "session_state": {"cookie": "DUNG"}}]}
    assert kp.doc_phien(body, "a@gmail.com")[0] == "DUNG"


# ── 2. Ba (bốn) kết quả của một lần hỏi kho ────────────────────────────────

def test_200_co_cookie_la_CO_PHIEN():
    k = kho(body={"session_state": {"cookie": "CK", "project_id": "P1"}})
    assert k.lay("a@gmail.com") == (kp.CO_PHIEN, "CK", "P1")


def test_200_khong_cookie_la_KHONG_CO_PHIEN():
    """200 mà rỗng = kho TRẢ LỜI là không giữ phiên nào. Đó là câu trả lời."""
    assert kho(body={})[0] if False else kho(body={}).lay("a@x.com")[0] == kp.KHONG_CO_PHIEN


@pytest.mark.parametrize("status", [404, 500, 502, 503])
def test_ma_loi_KHONG_duoc_coi_la_khong_co_phien(status):
    """404 vừa có thể là "không có tài khoản" vừa có thể là "endpoint chưa tồn
    tại". Đoán sai chiều nào cũng dẫn về đúng vòng xoáy đăng nhập oan."""
    k = kho(nem=urllib.error.HTTPError(MAU, status, "loi", None, None))
    assert k.lay("a@x.com")[0] == kp.KHONG_HOI_DUOC


@pytest.mark.parametrize("loi", [TimeoutError("het gio"), ConnectionError("mang"), OSError("dns")])
def test_server_khong_tra_loi_thi_KHONG_HOI_DUOC(loi):
    assert kho(nem=loi).lay("a@x.com")[0] == kp.KHONG_HOI_DUOC


def test_than_phan_hoi_khong_phai_json_thi_KHONG_HOI_DUOC():
    class Rac(io.BytesIO):
        status = 200
        def __enter__(self): return self
        def __exit__(self, *a): return False

    k = kp.KhoPhien(mau=MAU, token="t", mo_url=lambda req, timeout=None: Rac(b"<html>"))
    assert k.lay("a@x.com")[0] == kp.KHONG_HOI_DUOC


def test_khong_cau_hinh_kho_thi_noi_ro_la_CHUA_CAU_HINH():
    """Máy chạy VE3_SUITE độc lập (không qua shopapi) khác hẳn máy hỏi mà không được."""
    assert kp.KhoPhien(mau="", token="").lay("a@x.com")[0] == kp.CHUA_CAU_HINH


def test_email_duoc_ma_hoa_trong_url():
    ghi = []
    kho(body={}, ghi=ghi).lay("a+b@gmail.com")
    assert "a%2Bb%40gmail.com" in ghi[0]


# ── 3. Van chống hỏi dồn ───────────────────────────────────────────────────

def test_hoi_hut_thi_nghi_truoc_khi_hoi_lai():
    """`Account.auth()` được gọi MỖI JOB. Không có van này thì một account thiếu
    cookie sẽ bắn vào server vài chục lần một phút — đúng lỗi "hỏi dồn" đang chữa."""
    ghi = []
    dh = {"t": 0.0}
    k = kho(nem=TimeoutError("x"), ghi=ghi, dong_ho=lambda: dh["t"], nghi_hoi_lai=300)
    for _ in range(20):
        k.lay("a@x.com")
    assert len(ghi) == 1
    dh["t"] = 301
    k.lay("a@x.com")
    assert len(ghi) == 2


def test_hoi_duoc_roi_thi_khong_con_bi_van_chan():
    ghi = []
    k = kho(body={"cookie": "CK", "project_id": "P"}, ghi=ghi)
    k.lay("a@x.com"); k.lay("a@x.com")
    assert len(ghi) == 2


# ── 4. Kéo về là phải GHI XUỐNG CACHE CỤC BỘ ───────────────────────────────

def test_keo_phien_ve_ghi_xuong_cache_de_lan_sau_khoi_hoi_lai():
    kho_cache = KhoGia()
    d, kq = kp.keo_phien_ve("a@gmail.com", kho_cache,
                            kho=kho(body={"cookie": "CK", "project_id": "P1"}))
    assert kq == kp.CO_PHIEN
    assert kho_cache.da_ghi == ["a@gmail.com"]
    luu = kho_cache.data["a@gmail.com"]
    assert luu["cookie"] == "CK" and luu["project"] == "P1"
    # bearer để trống CỐ Ý: bearer Google sống ~30 phút, gửi cái cũ chỉ tổ 401.
    assert luu["bearer"] is None and luu["ts"] == 0
    assert d is luu


def test_keo_phien_ve_khong_ghi_gi_khi_kho_khong_co():
    kho_cache = KhoGia()
    d, kq = kp.keo_phien_ve("a@gmail.com", kho_cache, kho=kho(body={}))
    assert (d, kq) == (None, kp.KHONG_CO_PHIEN)
    assert kho_cache.da_ghi == []


def test_khong_bao_gio_ghi_cookie_ra_log():
    """Log bị dán vào chat, bị chụp màn hình. Cookie labs.google còn hạn là
    quyền truy cập đầy đủ vào tài khoản."""
    dong = []
    kp.keo_phien_ve("a@gmail.com", KhoGia(), kho=kho(body={"cookie": "COOKIE-BI-MAT", "project_id": "P"}),
                    log=dong.append)
    assert dong and all("COOKIE-BI-MAT" not in d for d in dong)


# ── 5. HỢP NHẤT VỚI LUẬT KIỂM — bài quan trọng nhất của cả việc này ────────

class KpThat:
    """Bọc `kho_phien` thật nhưng tiêm client giả -> vẫn đi hết logic thật."""

    CO_PHIEN = kp.CO_PHIEN
    KHONG_CO_PHIEN = kp.KHONG_CO_PHIEN
    KHONG_HOI_DUOC = kp.KHONG_HOI_DUOC
    CHUA_CAU_HINH = kp.CHUA_CAU_HINH

    def __init__(self, client):
        self.client = client
        self.so_lan = 0

    def keo_phien_ve(self, email, cache, kho=None, log=lambda *_: None):
        self.so_lan += 1
        return kp.keo_phien_ve(email, cache, kho=self.client, log=log)


def _tk_thieu_cache(email="a@gmail.com"):
    """Tài khoản KHÔNG có bản ghi nào trong `_auth_cache` — đúng 31 cái hôm 07/08."""
    return TaiKhoanGia(email, auth=None, cache=KhoGia())


def test_thieu_cache_ma_server_CO_phien_thi_KEO_VE_va_KHONG_dang_nhap_lan_nao():
    """BÀI QUAN TRỌNG NHẤT: đây là cảnh 31 tài khoản hôm 07/08/2026."""
    ds = [_tk_thieu_cache(f"tk{i}@gmail.com") for i in range(31)]
    kpm = KpThat(kho(body={"cookie": "CK", "project_id": "P1"}))
    da_chua = []
    han = pk.HanMucChua(so_luot=99, cua_so_s=600, tu_chay=False)
    kq = pk.kiem_roster(ds, submit_chua=da_chua.append, han_muc=han, ngu=khong_ngu, gian_cach=0,
                        kiem=kiem_gia(am=AmGia((pk.SONG, "200")), fc=FcGia(), kp=kpm))
    assert da_chua == [], "server có phiên mà vẫn đăng nhập lại = đúng lỗi đang chữa"
    assert kq["song"] == 31 and kq["chet_chac_chan"] == 0
    assert all(a.nghi == [] for a in ds)
    # và phiên đã nằm trên đĩa để lần sau khỏi hỏi lại
    assert all(a.cache.data[a.email]["cookie"] == "CK" for a in ds)


def test_thieu_cache_va_server_KHONG_co_phien_thi_moi_duoc_dang_nhap_lai():
    """Vá quá tay thành "không bao giờ đăng nhập lại" cũng làm hỏng hệ."""
    ds = [_tk_thieu_cache()]
    kpm = KpThat(kho(body={}))
    da_chua = []
    pk.kiem_roster(ds, submit_chua=da_chua.append, han_muc=pk.HanMucChua(so_luot=9, tu_chay=False),
                   ngu=khong_ngu, gian_cach=0,
                   kiem=kiem_gia(am=AmGia((pk.SONG, "")), fc=FcGia(), kp=kpm))
    assert da_chua == ds
    assert ds[0].trang_thai_phien == pk.CHET_CHAC_CHAN
    assert ds[0].nghi == [pk.NGHI_DAU_S]


def test_server_khong_tra_loi_thi_KHONG_coi_la_phien_chet():
    ds = [_tk_thieu_cache()]
    kpm = KpThat(kho(nem=TimeoutError("server im")))
    da_chua = []
    pk.kiem_roster(ds, submit_chua=da_chua.append, han_muc=pk.HanMucChua(so_luot=9, tu_chay=False),
                   ngu=khong_ngu, gian_cach=0,
                   kiem=kiem_gia(am=AmGia((pk.SONG, "")), fc=FcGia(), kp=kpm))
    assert da_chua == []
    assert ds[0].trang_thai_phien == pk.KHONG_KIEM_DUOC
    assert ds[0].nghi == []


def test_31_tai_khoan_thieu_cache_KHONG_ban_31_luot_dang_nhap():
    """Kể cả khi kho server cũng chịu thua cả 31 cái, hạn mức vẫn phải chặn —
    31 lượt đăng nhập nối đuôi nhau chính là mẫu hành vi đẻ ra CAPTCHA."""
    ds = [_tk_thieu_cache(f"tk{i}@gmail.com") for i in range(31)]
    kpm = KpThat(kho(body={}))
    han = pk.HanMucChua(so_luot=3, cua_so_s=600, tu_chay=False)
    da_chua = []
    kq = pk.kiem_roster(ds, submit_chua=da_chua.append, han_muc=han, ngu=khong_ngu, gian_cach=0,
                        kiem=kiem_gia(am=AmGia((pk.SONG, "")), fc=FcGia(), kp=kpm))
    assert len(da_chua) == 3 and han.cho_doi() == 28
    assert kq["chet_chac_chan"] == 31


def test_may_khong_cau_hinh_kho_thi_giu_nguyen_hanh_vi_cu():
    """VE3_SUITE chạy độc lập: KHÔNG có kho nào để hỏi -> đăng nhập lại là đường
    duy nhất còn lại (vẫn qua hạn mức và vẫn nghỉ ngắn)."""
    ds = [_tk_thieu_cache()]
    kpm = KpThat(kp.KhoPhien(mau="", token=""))
    da_chua = []
    pk.kiem_roster(ds, submit_chua=da_chua.append, han_muc=pk.HanMucChua(so_luot=9, tu_chay=False),
                   ngu=khong_ngu, gian_cach=0,
                   kiem=kiem_gia(am=AmGia((pk.SONG, "")), fc=FcGia(), kp=kpm))
    assert da_chua == ds


def test_keo_ve_roi_van_di_tiep_duong_kiem_binh_thuong():
    """Kéo được phiên nhưng Google trả 401 cho chính phiên đó -> vẫn kết luận chết.

    Bước 3 là một đường lấy phiên, KHÔNG phải một cái cớ để bỏ qua phép kiểm.
    """
    ds = [_tk_thieu_cache()]
    kpm = KpThat(kho(body={"cookie": "CK", "project_id": "P"}))
    da_chua = []
    kq = pk.kiem_roster(ds, submit_chua=da_chua.append, han_muc=pk.HanMucChua(so_luot=9, tu_chay=False),
                        ngu=khong_ngu, gian_cach=0,
                        kiem=kiem_gia(am=AmGia(pk.phan_loai_http(401, "")), fc=FcGia(), kp=kpm))
    assert kq["chet_chac_chan"] == 1 and da_chua == ds


def test_khong_hoi_lai_kho_khi_may_da_co_cookie():
    """Có cookie cục bộ rồi thì KHÔNG được đụng tới server: bước 2 rẻ hơn bước 3,
    và hỏi kho cho 96 tài khoản mỗi lần kiểm là tự dựng lại cảnh hỏi dồn."""
    a = TaiKhoanGia("a@gmail.com", auth=None, cache=KhoGia({"a@gmail.com": {"cookie": "ck"}}))
    kpm = KpThat(kho(body={"cookie": "CK", "project_id": "P"}))
    pk.kiem_roster([a], submit_chua=lambda _a: None, han_muc=pk.HanMucChua(tu_chay=False),
                   ngu=khong_ngu, gian_cach=0,
                   kiem=kiem_gia(am=AmGia((pk.SONG, "")), fc=FcGia("transient"), kp=kpm))
    assert kpm.so_lan == 0


# ── 6. Nhà máy ảnh BẢN CHROME (`image_pool_browser`, cái chạy thật ở :8789) ─

def _tk_pool_browser(email="a@gmail.com"):
    import image_pool_browser as ipb
    a = object.__new__(ipb.Account)      # không chạy __init__: nó đụng đĩa/Chrome
    a.email = email
    return ipb, a


def test_pool_browser_thieu_cache_thi_hoi_kho_truoc_khi_ket_luan_out(monkeypatch):
    """`_cookie_alive() is False` là thứ mở đường cho `_wipe_profile` + đăng nhập
    lại. Thiếu bản ghi trên máy KHÔNG được dẫn thẳng tới đó."""
    ipb, a = _tk_pool_browser()
    kho_cache = KhoGia()
    monkeypatch.setattr(ipb, "_AUTHCACHE", kho_cache)
    monkeypatch.setattr(ipb.kp, "keo_phien_ve",
                        lambda email, cache, **kw: (cache._save(email, {"cookie": "CK"}),
                                                    ({"cookie": "CK"}, kp.CO_PHIEN))[1])
    monkeypatch.setattr(ipb.fc, "cookie_liveness", lambda ck, timeout=25: "alive")
    assert a._cookie_alive(tries=1) is True
    assert kho_cache.da_ghi == ["a@gmail.com"]


def test_pool_browser_may_lan_kho_deu_khong_co_thi_moi_la_out_that(monkeypatch):
    ipb, a = _tk_pool_browser()
    monkeypatch.setattr(ipb, "_AUTHCACHE", KhoGia())
    monkeypatch.setattr(ipb.kp, "keo_phien_ve", lambda *ar, **kw: (None, kp.KHONG_CO_PHIEN))
    assert a._cookie_alive(tries=1) is False


def test_pool_browser_khong_hoi_duoc_kho_thi_giu_nguyen_hanh_vi_cu(monkeypatch):
    """Kho im lặng không được biến thành "còn sống" (che mất account hỏng thật),
    cũng không được ném lỗi ra ngoài."""
    ipb, a = _tk_pool_browser()
    monkeypatch.setattr(ipb, "_AUTHCACHE", KhoGia())
    monkeypatch.setattr(ipb.kp, "keo_phien_ve", lambda *ar, **kw: (_ for _ in ()).throw(TimeoutError()))
    assert a._cookie_alive(tries=1) is False


# ── 7. Cấu hình đọc từ môi trường ─────────────────────────────────────────

def test_mau_url_ghep_tu_SHOPAPI_API_URL(monkeypatch):
    monkeypatch.delenv("VEO3TOP_KHO_PHIEN_URL", raising=False)
    monkeypatch.setenv("SHOPAPI_API_URL", "https://api.shopapi.vn/")
    assert kp.mau_url() == "https://api.shopapi.vn" + kp.DUONG_MAC_DINH
    assert "{email}" in kp.mau_url()


def test_mau_url_rieng_thang_the_SHOPAPI_API_URL(monkeypatch):
    monkeypatch.setenv("SHOPAPI_API_URL", "https://api.shopapi.vn")
    monkeypatch.setenv("VEO3TOP_KHO_PHIEN_URL", "http://noi-khac/{email}")
    assert kp.mau_url() == "http://noi-khac/{email}"


def test_khong_cau_hinh_gi_thi_khong_co_kho(monkeypatch):
    monkeypatch.delenv("VEO3TOP_KHO_PHIEN_URL", raising=False)
    monkeypatch.delenv("SHOPAPI_API_URL", raising=False)
    assert kp.mau_url() == "" and kp.da_cau_hinh() is False
