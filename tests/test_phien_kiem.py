"""Bộ kiểm cho `veo3top_engine/phien_kiem.py` — LUẬT KIỂM PHIÊN BA TRẠNG THÁI.

═══════════════════════════════════════════════════════════════════════════════
BÀI KIỂM NÀY GHIM LẠI SỰ CỐ 07/08/2026, ĐỪNG "TỐI ƯU" NÓ ĐI
═══════════════════════════════════════════════════════════════════════════════

Số đo hôm đó:
  - 98 hồ sơ Chrome trên đĩa, file cookie CÒN NGUYÊN, không sót khoá `Singleton*`
    -> không một phiên nào thật sự mất.
  - `/health` nhà máy ảnh: `logged_in` bò từ 8 lên 41 trong 30 phút -> nó là BỘ
    ĐẾM TIẾN TRÌNH KIỂM, không phải số account đăng nhập được.
  - Ảnh mất 600–780 giây thay vì ~90.

Mã cũ: `except Exception: ok=False` -> `rest(1800)` + ĐĂNG NHẬP LẠI BẰNG MẬT
KHẨU, và 64 lượt kiểm bắn cùng lúc lúc khởi động. Vòng xoáy tự nuôi: kiểm dồn ->
429 -> kết án oan -> đăng nhập hàng loạt -> CAPTCHA -> mất account.

KHÔNG bài nào ở đây chạm mạng thật, mở Chrome thật hay đăng nhập thật: mọi thứ
đi qua module GIẢ tiêm vào bằng tham số.
"""

from __future__ import annotations

import pytest

import phien_kiem as pk


# ── Đồ giả ───────────────────────────────────────────────────────────────────

class KhoGia:
    """Thay `AuthCache`: chỉ giữ dict trong bộ nhớ, KHÔNG chạm đĩa."""

    def __init__(self, data=None):
        self.data = dict(data or {})
        self.da_ghi = []

    def _load(self, email):
        return self.data.get(email)

    def _save(self, email, d):
        self.data[email] = d
        self.da_ghi.append(email)

    def _fresh(self, d):
        return bool(d and d.get("bearer"))

    def _refresh_from_cookie(self, email, d):
        nd = dict(d)
        nd["bearer"] = "bearer-moi"
        return nd


class TaiKhoanGia:
    """Thay `Account` của hai nhà máy: đủ những gì `phien_kiem` đụng tới."""

    def __init__(self, email="a@gmail.com", auth=None, cache=None, nem=None):
        self.email = email
        self.name = email.split("@")[0]
        self.cache = cache if cache is not None else KhoGia()
        self._auth = auth
        self._nem = nem
        self.resume_at = 0.0
        self.rest_streak = 0
        self.trang_thai_phien = pk.KHONG_KIEM_DUOC
        self.so_lan_goi_auth = 0
        self.nghi = []          # lịch sử các mức nghỉ đã bị áp

    def auth(self, force=False):
        """Mô phỏng `Account.auth()` thật: có cookie + project trong kho thì dựng
        được thẻ, không thì trả None. Nhờ vậy bài kiểm thấy được hiệu quả của
        bước "kéo phiên từ kho server về" (nó ghi thẳng vào kho này)."""
        self.so_lan_goi_auth += 1
        if self._nem:
            raise self._nem
        if self._auth is not None:
            return self._auth
        d = self.cache._load(self.email)
        if d and d.get("cookie") and d.get("project"):
            return {"bearer": "b", "cookie": d["cookie"], "project": d["project"]}
        return None

    def rest(self, secs):
        self.rest_streak += 1
        self.resume_at = secs
        self.nghi.append(secs)

    def clear_rest(self):
        self.rest_streak = 0
        self.resume_at = 0.0


class PhanHoiGia:
    def __init__(self, status_code, text=""):
        self.status_code = status_code
        self.text = text


class AmGia:
    """Thay `account_manager`: trả sẵn kết quả phân loại, KHÔNG bắn POST nào."""

    def __init__(self, ket_qua):
        self.ket_qua = ket_qua
        self.so_lan = 0

    def validate_auth_chi_tiet(self, auth, timeout=30):
        self.so_lan += 1
        kq = self.ket_qua
        return kq(self.so_lan) if callable(kq) else kq


class FcGia:
    def __init__(self, muc="transient"):
        self.muc = muc
        self.so_lan = 0

    def cookie_liveness(self, cookie, timeout=25):
        self.so_lan += 1
        return self.muc


class KpGia:
    """Thay `kho_phien`: khai đúng bốn hằng số + `keo_phien_ve`."""

    CO_PHIEN = "co_phien"
    KHONG_CO_PHIEN = "khong_co_phien"
    KHONG_HOI_DUOC = "khong_hoi_duoc"
    CHUA_CAU_HINH = "chua_cau_hinh"

    def __init__(self, ket_qua="chua_cau_hinh", phien=None):
        self.ket_qua = ket_qua
        self.phien = phien
        self.so_lan = 0

    def keo_phien_ve(self, email, cache, kho=None, log=lambda *_: None):
        self.so_lan += 1
        if self.ket_qua == self.CO_PHIEN:
            d = self.phien or {"bearer": None, "ts": 0, "cookie": "ck", "project": "p1", "email": email}
            cache._save(email, d)
            return d, self.ket_qua
        return None, self.ket_qua


def khong_ngu(_s):
    """Thay `time.sleep`: bài kiểm không được chờ thật."""
    return None


def kiem_gia(am=None, fc=None, kp=None, force=False):
    """Hàm kiểm đã tiêm sẵn đồ giả, đúng chữ ký `kiem_roster` chờ đợi."""
    def _f(a, ngu=khong_ngu):
        return pk.kiem_co_thu_lai(a, ngu=ngu, am_mod=am, fc_mod=fc, kp_mod=kp, force=force)
    return _f


# ── 1. PHÂN LOẠI: ba trạng thái, không phải hai ─────────────────────────────

@pytest.mark.parametrize("ma,than,mong_doi", [
    (200, "", pk.SONG),
    (201, "", pk.SONG),
    (400, "INVALID_ARGUMENT", pk.SONG),      # qua xác thực rồi mới chê token giả
    (401, "UNAUTHENTICATED", pk.CHET_CHAC_CHAN),
    (429, "TOO_MUCH_TRAFFIC", pk.KHONG_KIEM_DUOC),
    (500, "", pk.KHONG_KIEM_DUOC),
    (503, "", pk.KHONG_KIEM_DUOC),
    (0, "", pk.KHONG_KIEM_DUOC),
])
def test_phan_loai_http(ma, than, mong_doi):
    assert pk.phan_loai_http(ma, than)[0] == mong_doi


def test_403_khong_co_dau_hieu_phien_thi_khong_ket_an():
    """403 hay là CHẶN THEO IP (HTML "Sorry") hoặc lỗi CẤP PROJECT.

    Kết án phiên chết vì một cái 403 kiểu đó = đăng nhập lại cho một thứ mà đăng
    nhập lại không cứu được, tức đốt hạn mức đăng nhập của Google không công.
    """
    assert pk.phan_loai_http(403, "<html>Sorry...</html>")[0] == pk.KHONG_KIEM_DUOC
    assert pk.phan_loai_http(403, "PERMISSION_DENIED on project")[0] == pk.KHONG_KIEM_DUOC


def test_403_kem_dau_hieu_phien_thi_la_chet_that():
    assert pk.phan_loai_http(403, '{"reason":"UNAUTHENTICATED"}')[0] == pk.CHET_CHAC_CHAN
    assert pk.phan_loai_http(403, "ACCESS_TOKEN_EXPIRED")[0] == pk.CHET_CHAC_CHAN


@pytest.mark.parametrize("loi", [
    TimeoutError("het gio"),
    ConnectionError("mang chap"),
    OSError("DNS hong"),
    RuntimeError("gi do la"),
])
def test_moi_ngoai_le_deu_la_khong_ket_luan_duoc(loi):
    """ĐÂY LÀ LỖI 1 CỦA 07/08/2026: `except Exception: ok = False`."""
    assert pk.phan_loai_ngoai_le(loi)[0] == pk.KHONG_KIEM_DUOC


# ── 2. KIỂM MỘT ACCOUNT ─────────────────────────────────────────────────────

def test_auth_nem_ngoai_le_thi_chua_ro_chu_khong_phai_chet():
    a = TaiKhoanGia(nem=ConnectionError("mạng chập"))
    tt, _ghi_chu = pk.kiem_mot_lan(a, am_mod=AmGia((pk.SONG, "")), fc_mod=FcGia(), kp_mod=KpGia())
    assert tt == pk.KHONG_KIEM_DUOC


def test_co_bearer_thi_hoi_may_chu_that():
    a = TaiKhoanGia(auth={"bearer": "b", "project": "p"})
    am = AmGia((pk.CHET_CHAC_CHAN, "HTTP 401"))
    assert pk.kiem_mot_lan(a, am_mod=am, fc_mod=FcGia(), kp_mod=KpGia())[0] == pk.CHET_CHAC_CHAN
    assert am.so_lan == 1


def test_khong_dung_duoc_bearer_nhung_cookie_con_song_thi_chua_ro():
    """`auth()` trả None KHÔNG phải bằng chứng: 429 ở endpoint session cũng ra None."""
    kho = KhoGia({"a@gmail.com": {"cookie": "ck"}})
    a = TaiKhoanGia(auth=None, cache=kho)
    tt, _ = pk.kiem_mot_lan(a, am_mod=AmGia((pk.SONG, "")), fc_mod=FcGia("transient"), kp_mod=KpGia())
    assert tt == pk.KHONG_KIEM_DUOC


def test_cookie_liveness_dead_moi_la_chet_that():
    kho = KhoGia({"a@gmail.com": {"cookie": "ck"}})
    a = TaiKhoanGia(auth=None, cache=kho)
    tt, _ = pk.kiem_mot_lan(a, am_mod=AmGia((pk.SONG, "")), fc_mod=FcGia("dead"), kp_mod=KpGia())
    assert tt == pk.CHET_CHAC_CHAN


# ── 3. THỬ LẠI: một lần trượt KHÔNG được kết luận gì ────────────────────────

def test_khong_kiem_duoc_thi_thu_lai_du_so_lan():
    a = TaiKhoanGia(nem=TimeoutError("x"))
    tt, _ghi, so_lan = pk.kiem_co_thu_lai(a, ngu=khong_ngu, am_mod=AmGia((pk.SONG, "")),
                                          fc_mod=FcGia(), kp_mod=KpGia())
    assert tt == pk.KHONG_KIEM_DUOC
    assert so_lan == pk.SO_LAN_THU_KIEM >= 3
    assert a.so_lan_goi_auth == pk.SO_LAN_THU_KIEM


def test_co_ket_luan_thi_dung_ngay_khong_ton_them_request():
    a = TaiKhoanGia(auth={"bearer": "b", "project": "p"})
    am = AmGia((pk.SONG, ""))
    tt, _ghi, so_lan = pk.kiem_co_thu_lai(a, ngu=khong_ngu, am_mod=am, fc_mod=FcGia(), kp_mod=KpGia())
    assert (tt, so_lan, am.so_lan) == (pk.SONG, 1, 1)


def test_chap_mot_nhip_roi_on_lai_thi_ket_luan_la_song():
    """Gói tin rơi ở lượt đầu KHÔNG được biến thành bản án."""
    a = TaiKhoanGia(auth={"bearer": "b", "project": "p"})
    am = AmGia(lambda n: (pk.KHONG_KIEM_DUOC, "429") if n == 1 else (pk.SONG, "200"))
    tt, _ghi, so_lan = pk.kiem_co_thu_lai(a, ngu=khong_ngu, am_mod=am, fc_mod=FcGia(), kp_mod=KpGia())
    assert (tt, so_lan) == (pk.SONG, 2)


def test_giua_cac_lan_thu_co_gian_cach_va_gian_cach_tang_dan():
    da_ngu = []
    a = TaiKhoanGia(nem=TimeoutError("x"))
    pk.kiem_co_thu_lai(a, ngu=da_ngu.append, am_mod=AmGia((pk.SONG, "")),
                       fc_mod=FcGia(), kp_mod=KpGia())
    assert len(da_ngu) == pk.SO_LAN_THU_KIEM - 1
    assert da_ngu == sorted(da_ngu) and da_ngu[0] > 0


# ── 4. MỨC NGHỈ: ngắn, tăng dần, có trần, xoá khi chạy lại được ─────────────

def test_muc_nghi_dau_tien_phai_ngan():
    assert pk.nghi_bao_lau(1) == pk.NGHI_DAU_S
    assert pk.nghi_bao_lau(1) < 120, "nghỉ 30 phút cho lần hỏng đầu là cắt công suất oan"


def test_muc_nghi_tang_dan_va_co_tran():
    day = [pk.nghi_bao_lau(k) for k in range(1, 8)]
    assert day == sorted(day)
    assert day[-1] == pk.NGHI_TRAN_S
    assert max(day) <= pk.NGHI_TRAN_S


def test_hong_lien_tiep_thi_nghi_dai_dan_chay_lai_duoc_thi_ve_muc_dau():
    a = TaiKhoanGia()
    m1 = pk.nghi_tang_dan(a)
    m2 = pk.nghi_tang_dan(a)
    assert m2 > m1
    a.clear_rest()                      # một lần dùng thành công
    assert a.rest_streak == 0
    assert pk.nghi_tang_dan(a) == m1     # mức nghỉ về mức đầu


# ── 5. KIỂM CẢ ROSTER — bài quan trọng nhất ─────────────────────────────────

def _han_muc(so_luot=99):
    return pk.HanMucChua(so_luot=so_luot, cua_so_s=600, tu_chay=False)


def test_mang_chap_thi_KHONG_dang_nhap_lai_va_KHONG_nghi():
    """BÀI QUAN TRỌNG NHẤT — đây chính là lỗi đã xảy ra 07/08/2026.

    64 account, mọi lượt kiểm đều ném lỗi mạng. Đúng không có gì được xảy ra:
    không một lượt đăng nhập lại, không một giây nghỉ.
    """
    ds = [TaiKhoanGia(f"tk{i}@gmail.com", nem=TimeoutError("mạng chập")) for i in range(64)]
    da_chua = []
    kq = pk.kiem_roster(ds, submit_chua=da_chua.append, han_muc=_han_muc(), ngu=khong_ngu,
                        so_song_song=8, gian_cach=0)
    assert da_chua == [], "mạng chập KHÔNG được dẫn tới đăng nhập lại"
    assert all(a.nghi == [] for a in ds), "mạng chập KHÔNG được dẫn tới nghỉ"
    assert kq["khong_kiem_duoc"] == 64 and kq["chet_chac_chan"] == 0
    assert all(a.trang_thai_phien == pk.KHONG_KIEM_DUOC for a in ds)


@pytest.mark.parametrize("ma,than", [(429, "TOO_MUCH_TRAFFIC"), (500, ""), (503, ""),
                                     (403, "<html>Sorry</html>")])
def test_429_5xx_403_chan_ip_deu_khong_bi_ket_an(ma, than):
    ds = [TaiKhoanGia(auth={"bearer": "b", "project": "p"})]
    da_chua = []
    pk.kiem_roster(ds, submit_chua=da_chua.append, han_muc=_han_muc(), ngu=khong_ngu,
                   gian_cach=0, kiem=kiem_gia(am=AmGia(pk.phan_loai_http(ma, than))))
    assert da_chua == [] and ds[0].nghi == []


def test_401_thi_CO_dang_nhap_lai_va_CO_nghi_nhung_nghi_NGAN():
    """Vá quá tay thành "không bao giờ chữa" cũng là một cách làm hỏng hệ."""
    ds = [TaiKhoanGia(auth={"bearer": "b", "project": "p"})]
    da_chua = []
    pk.kiem_roster(ds, submit_chua=da_chua.append, han_muc=_han_muc(), ngu=khong_ngu,
                   gian_cach=0, kiem=kiem_gia(am=AmGia(pk.phan_loai_http(401, "UNAUTHENTICATED"))))
    assert da_chua == ds
    assert ds[0].nghi == [pk.NGHI_DAU_S]
    assert ds[0].nghi[0] < 1800, "1800 giây cố định là mức phạt cũ, đã bỏ"
    assert ds[0].trang_thai_phien == pk.CHET_CHAC_CHAN


def test_kiem_ra_song_thi_xoa_an_dang_treo():
    a = TaiKhoanGia(auth={"bearer": "b", "project": "p"})
    a.rest(600); a.rest(600)            # đang bị phạt từ trước
    pk.kiem_roster([a], submit_chua=lambda _a: None, han_muc=_han_muc(), ngu=khong_ngu,
                   gian_cach=0, kiem=kiem_gia(am=AmGia((pk.SONG, "200"))))
    assert (a.rest_streak, a.resume_at, a.trang_thai_phien) == (0, 0.0, pk.SONG)


def test_50_account_cung_hong_KHONG_ban_50_luot_dang_nhap():
    """Đăng nhập lại 50 account cùng lúc là tự bắn vào chân — Google nhìn thấy
    đúng cái mẫu hành vi đó rồi trả lời bằng CAPTCHA."""
    ds = [TaiKhoanGia(f"tk{i}@gmail.com", auth={"bearer": "b", "project": "p"}) for i in range(50)]
    han = pk.HanMucChua(so_luot=3, cua_so_s=600, tu_chay=False)
    da_chua = []
    kq = pk.kiem_roster(ds, submit_chua=da_chua.append, han_muc=han, ngu=khong_ngu,
                        gian_cach=0, kiem=kiem_gia(am=AmGia(pk.phan_loai_http(401, ""))))
    assert len(da_chua) == 3, "hạn mức phải chặn, phần còn lại XẾP HÀNG"
    assert han.cho_doi() == 47 and kq["cho_chua"] == 47
    assert kq["chet_chac_chan"] == 50, "vẫn phải nhận ra cả 50 cái là chết thật"


def test_hang_doi_chua_duoc_rut_dan_khi_cua_so_nha_ra():
    dong_ho = {"t": 1000.0}
    han = pk.HanMucChua(so_luot=2, cua_so_s=100, tu_chay=False, dong_ho=lambda: dong_ho["t"])
    ds = [TaiKhoanGia(f"tk{i}@gmail.com", auth={"bearer": "b", "project": "p"}) for i in range(5)]
    da_chua = []
    pk.kiem_roster(ds, submit_chua=da_chua.append, han_muc=han, ngu=khong_ngu,
                   gian_cach=0, kiem=kiem_gia(am=AmGia(pk.phan_loai_http(401, ""))))
    assert len(da_chua) == 2 and han.cho_doi() == 3
    dong_ho["t"] += 101          # cửa sổ trôi qua
    assert han.chay_hang_doi() == 2 and han.cho_doi() == 1
    dong_ho["t"] += 101
    assert han.chay_hang_doi() == 1 and han.cho_doi() == 0


def test_so_luot_kiem_song_song_bi_gioi_han():
    """64 POST một phát là thứ tự tạo ra 429 rồi tự đọc 429 thành "phiên chết"."""
    ds = [TaiKhoanGia(f"tk{i}@gmail.com", auth={"bearer": "b", "project": "p"}) for i in range(64)]
    kq = pk.kiem_roster(ds, submit_chua=lambda _a: None, han_muc=_han_muc(), ngu=khong_ngu,
                        so_song_song=4, gian_cach=0, kiem=kiem_gia(am=AmGia((pk.SONG, ""))))
    assert kq["da_kiem"] == 64
    assert kq["dinh_song_song"] <= 4, "không được bắn cả roster cùng lúc"


def test_chay_song_song_van_chay_het_va_khong_vuot_tran():
    dem = {"n": 0}
    xong, dinh = pk.chay_song_song(range(30), lambda _x: dem.__setitem__("n", dem["n"] + 1),
                                   so_song_song=3, gian_cach=0)
    assert (xong, dem["n"]) == (30, 30) and dinh <= 3


def test_mac_dinh_gioi_han_song_song_du_nho():
    assert 1 <= pk.KIEM_SONG_SONG <= 8, "mặc định phải nhỏ hơn hẳn số account"


def test_kiem_khong_duoc_chan_nha_may_qua_lau():
    """Kiểm đầu giờ chặn `start()`. Một lượt kiểm treo KHÔNG được giữ cả nhà máy:
    account chưa kiểm xong thì đằng nào cũng được giao việc."""
    import threading as _th
    import time as _t
    cho = _th.Event()
    t0 = _t.time()
    xong, _dinh = pk.chay_song_song([1, 2, 3], lambda _x: cho.wait(30),
                                    so_song_song=3, gian_cach=0, timeout=0.3)
    troi = _t.time() - t0
    cho.set()
    assert troi < 3, "phải bỏ chờ khi hết trần, không đứng đợi từng luồng"
    assert xong == 0


# ── 6. ACCOUNT "CHƯA RÕ" VẪN ĐƯỢC GIAO VIỆC ────────────────────────────────

def test_chua_ro_van_duoc_tinh_la_dung_duoc():
    """Kiểm đầu giờ chỉ là GỢI Ý. Nếu phiên chết thật thì 401 lúc chạy thật sẽ nói."""
    ds = [TaiKhoanGia(nem=TimeoutError("x")) for _ in range(3)]
    pk.kiem_roster(ds, submit_chua=lambda _a: None, han_muc=_han_muc(), ngu=khong_ngu, gian_cach=0)
    so = pk.dem_trang_thai(ds)
    assert so["phien_dung_duoc"] == 3 and so["phien_chua_kiem"] == 3
    assert all(a.resume_at == 0.0 for a in ds), "chưa rõ mà bắt nghỉ là tự cắt công suất"


# ── 7. /health: KHÔNG con số nào mang hai nghĩa ────────────────────────────

def test_dem_trang_thai_tach_ro_chua_kiem_voi_da_kiem_va_chet():
    ds = [TaiKhoanGia("song@x.com"), TaiKhoanGia("chet@x.com"), TaiKhoanGia("chua@x.com")]
    ds[0].trang_thai_phien = pk.SONG
    ds[1].trang_thai_phien = pk.CHET_CHAC_CHAN
    so = pk.dem_trang_thai(ds)
    assert so["phien_song"] == 1
    assert so["phien_chet_chac_chan"] == 1
    assert so["phien_chua_kiem"] == 1
    assert so["phien_da_kiem"] == 2         # KHÔNG gộp "chưa kiểm" vào mẫu số
    assert so["phien_dung_duoc"] == 2       # sống + chưa rõ
    assert so["accounts"] == 3


def test_khong_con_khoa_logged_in_o_dau_ca():
    """`logged_in` là cái tên đã đánh lừa cả người vận hành lẫn chủ dự án."""
    so = pk.dem_trang_thai([TaiKhoanGia()])
    assert "logged_in" not in so and "not_logged_in" not in so


def test_moi_con_so_chi_mang_mot_nghia():
    """Tổng ba nhóm rời nhau phải đúng bằng tổng roster — không nhóm nào chồng lấn."""
    ds = [TaiKhoanGia(f"tk{i}@x.com") for i in range(7)]
    for a in ds[:2]:
        a.trang_thai_phien = pk.SONG
    for a in ds[2:4]:
        a.trang_thai_phien = pk.CHET_CHAC_CHAN
    so = pk.dem_trang_thai(ds)
    assert so["phien_song"] + so["phien_chet_chac_chan"] + so["phien_chua_kiem"] == len(ds)
    assert so["phien_da_kiem"] + so["phien_chua_kiem"] == len(ds)


# ── 8. HẠN MỨC ĐĂNG NHẬP LẠI ───────────────────────────────────────────────

def test_han_muc_dem_theo_cua_so_truot():
    dong_ho = {"t": 0.0}
    han = pk.HanMucChua(so_luot=2, cua_so_s=60, tu_chay=False, dong_ho=lambda: dong_ho["t"])
    assert (han.xin(), han.xin(), han.xin()) == (True, True, False)
    dong_ho["t"] = 61
    assert han.xin() is True


def test_xep_hang_khong_nhan_doi_mot_email():
    han = pk.HanMucChua(so_luot=0, cua_so_s=60, tu_chay=False)
    han.xep_hang("a@x.com", lambda: None)
    han.xep_hang("a@x.com", lambda: None)
    assert han.cho_doi() == 1
