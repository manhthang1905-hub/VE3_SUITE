"""Bộ kiểm NẠP LẠI NÓNG roster — vá sự cố sản xuất 07/08/2026.

═══════════════════════════════════════════════════════════════════════════════
SỰ CỐ ĐANG ĐƯỢC VÁ (số đo thật, không phải giả định)
═══════════════════════════════════════════════════════════════════════════════

`curl http://127.0.0.1:8789/health` của nhà máy ảnh trả:

    slots: 64, candidates: 64, not_logged_in: 50, logged_in: 8, known_good: 45

trong khi CSDL kho có **96/96 tài khoản sẵn sàng**. Hậu quả:
mỗi ảnh mất **600–780 giây** thay vì ~90 — `workers/veo3/logs/worker-20260807.jsonl`
ghi rõ `Đang tạo ảnh 1/1 (627s)`, `(748s)`, `(769s)`. Một khách thật
(`gunc94@gmail.com`) có 4 job bò ở tốc độ đó.

Gốc: nhà máy nạp roster **một lần lúc khởi động** rồi không bao giờ nạp lại, và
cách duy nhất để nạp phiên mới là khởi động lại — tức GIẾT job đang chạy dở của
khách. Nên cái giá để sửa luôn rơi vào khách, và lỗi sống dai.

═══════════════════════════════════════════════════════════════════════════════
NGUYÊN TẮC CỦA BỘ KIỂM NÀY
═══════════════════════════════════════════════════════════════════════════════

KHÔNG chạm mạng thật, KHÔNG mở Chrome thật, KHÔNG ghi vào file trạng thái sản
xuất. Mọi bài dưới đây chạy trên logic thuần + object giả.

Bài quan trọng nhất là `test_khong_dung_tai_khoan_dang_ban_*`: nếu nạp lại nóng
mà cắt ngang job đang chạy thì ta vừa xây lại đúng cái giá mà cả tính năng này
sinh ra để tránh.
"""

import os
import sys
import threading
import time
from pathlib import Path

import pytest

SUITE_ROOT = Path(__file__).resolve().parents[1]
ENGINE_DIR = SUITE_ROOT / "veo3top_engine"
if str(ENGINE_DIR) not in sys.path:
    sys.path.insert(0, str(ENGINE_DIR))

import roster_reload as rr  # noqa: E402


# ═════════════════════════════════════════════════════════════════════════════
# PHẦN A — logic hợp nhất roster (thuần, không phụ thuộc nhà máy nào)
# ═════════════════════════════════════════════════════════════════════════════

class TaiKhoanGia:
    """Đứng thay lớp `Account` của cả ba nhà máy."""

    def __init__(self, email, busy=False):
        self.email = email
        self.busy = busy
        self.retired = False
        self.dau_vet = f"object-{email}"   # để khẳng định KHÔNG bị dựng lại

    def __repr__(self):
        return f"<TaiKhoanGia {self.email} busy={self.busy}>"


def _hop_nhat(hien_tai, mong_muon):
    return rr.hop_nhat_roster(
        hien_tai, mong_muon,
        lay_khoa=lambda a: a.email,
        lay_khoa_mong_muon=lambda d: d["email"],
        dang_ban=lambda a: a.busy,
        tao_moi=lambda d: TaiKhoanGia(d["email"]),
    )


def test_tai_khoan_con_trong_roster_thi_GIU_NGUYEN_OBJECT_khong_dung_lai():
    """Dựng lại object = vứt cookie/bearer/IPv6 và giết job đang chạy trên đó."""
    cu = TaiKhoanGia("a@x.com")
    kq = _hop_nhat([cu], [{"email": "a@x.com"}])
    assert kq.danh_sach[0] is cu
    assert kq.danh_sach[0].dau_vet == "object-a@x.com"
    assert kq.them == [] and kq.bo == []


def test_them_tai_khoan_moi_va_bo_tai_khoan_chet():
    """Đúng phần cứu 07/08: tài khoản đăng nhập xong SAU lúc nhà máy khởi động
    rốt cuộc cũng vào được ca, còn tài khoản đã rời kho thì bị gỡ."""
    cu = [TaiKhoanGia("song@x.com"), TaiKhoanGia("chet@x.com")]
    kq = _hop_nhat(cu, [{"email": "song@x.com"}, {"email": "moi@x.com"}])
    assert kq.them == ["moi@x.com"]
    assert kq.bo == ["chet@x.com"]
    emails = [a.email for a in kq.danh_sach]
    assert emails == ["song@x.com", "moi@x.com"]
    assert kq.danh_sach[0] is cu[0]           # cái còn lại KHÔNG bị đụng


def test_KHONG_BO_tai_khoan_dang_ban_du_no_da_roi_kho():
    """RÀNG BUỘC SỐ MỘT. Tài khoản đang chạy job của khách không bao giờ bị gỡ,
    kể cả khi nó đã rời danh sách mới. Nó được dọn ở lượt nạp SAU."""
    ban = TaiKhoanGia("dangchay@x.com", busy=True)
    ranh = TaiKhoanGia("ranh@x.com", busy=False)
    kq = _hop_nhat([ban, ranh], [{"email": "khac@x.com"}])
    assert kq.giu_lai_vi_ban == ["dangchay@x.com"]
    assert kq.bo == ["ranh@x.com"]            # chỉ cái RẢNH bị gỡ
    assert ban in kq.danh_sach                # cái BẬN vẫn ở lại nguyên vẹn
    assert ban.retired is False


def test_luot_nap_sau_moi_don_not_tai_khoan_da_ranh():
    """Cái bận được hoãn, nhưng KHÔNG bị quên: xong việc là lượt sau dọn."""
    ban = TaiKhoanGia("dangchay@x.com", busy=True)
    kq1 = _hop_nhat([ban], [{"email": "khac@x.com"}])
    assert kq1.giu_lai_vi_ban == ["dangchay@x.com"]
    ban.busy = False                          # job xong
    kq2 = _hop_nhat(kq1.danh_sach, [{"email": "khac@x.com"}])
    assert kq2.bo == ["dangchay@x.com"]
    assert [a.email for a in kq2.danh_sach] == ["khac@x.com"]


def test_khoa_email_bo_qua_hoa_thuong_va_khoang_trang():
    """Cùng một tài khoản mà bị đếm hai lần = hai phiên Chrome trên một hồ sơ."""
    cu = TaiKhoanGia("Ai@X.com")
    kq = _hop_nhat([cu], [{"email": "  ai@x.COM "}])
    assert kq.them == [] and kq.bo == []
    assert kq.danh_sach == [cu]


def test_kho_co_email_trung_thi_chi_lay_mot():
    kq = _hop_nhat([], [{"email": "a@x.com"}, {"email": "A@X.com"}, {"email": "b@x.com"}])
    assert [a.email for a in kq.danh_sach] == ["a@x.com", "b@x.com"]


def test_mot_muc_du_lieu_rac_khong_chan_95_muc_con_lai():
    def _tao(d):
        if d["email"] == "rac@x.com":
            raise ValueError("thiếu password")
        return TaiKhoanGia(d["email"])

    kq = rr.hop_nhat_roster(
        [], [{"email": "rac@x.com"}, {"email": "tot@x.com"}],
        lay_khoa=lambda a: a.email, lay_khoa_mong_muon=lambda d: d["email"],
        dang_ban=lambda a: a.busy, tao_moi=_tao,
    )
    assert [a.email for a in kq.danh_sach] == ["tot@x.com"]


# ═════════════════════════════════════════════════════════════════════════════
# PHẦN B — cảnh báo KHO LỆCH (thứ đã im lặng suốt sự cố)
# ═════════════════════════════════════════════════════════════════════════════

def test_canh_bao_khi_dang_nhap_duoc_qua_it_so_voi_kho():
    """Đúng số đo 07/08: 8 đăng nhập được / 96 sẵn sàng trong kho."""
    cb = rr.canh_bao_kho_lech(8, 96, uptime_s=3600)
    assert cb, "8/96 mà KHÔNG cảnh báo là để lỗi 07/08 sống lại"
    assert "8/96" in cb
    assert "600-780" in cb          # số đo nằm ngay trong câu cảnh báo


def test_khong_canh_bao_khi_kho_va_nha_may_khop_nhau():
    assert rr.canh_bao_kho_lech(96, 96, uptime_s=3600) == ""
    assert rr.canh_bao_kho_lech(60, 96, uptime_s=3600) == ""   # 0.62 > ngưỡng 0.5


def test_canh_bao_o_ngay_duoi_nguong():
    assert rr.canh_bao_kho_lech(47, 96, uptime_s=3600) != ""
    assert rr.canh_bao_kho_lech(48, 96, uptime_s=3600) == ""


def test_khong_keu_oan_luc_nha_may_dang_am_may():
    """Vừa bật thì `logged_in` đương nhiên thấp. Cảnh báo lúc đó thành tiếng ồn,
    mà tiếng ồn thì bị bỏ qua — kể cả lần lệch THẬT."""
    assert rr.canh_bao_kho_lech(0, 96, uptime_s=10) == ""
    assert rr.canh_bao_kho_lech(0, 96, uptime_s=rr.AN_HAN_GIAY + 1) != ""


def test_kho_qua_nho_thi_ty_le_vo_nghia_nen_im():
    assert rr.canh_bao_kho_lech(0, 1, uptime_s=3600) == ""
    assert rr.canh_bao_kho_lech(0, 3, uptime_s=3600) == ""


def test_bao_cao_co_du_so_lieu_truoc_va_sau():
    """07/08 mọi thứ đều 'ok: true'. Nên chỉ 'ok' là KHÔNG đủ — phải có con số."""
    kq = _hop_nhat([TaiKhoanGia("cu@x.com")], [{"email": "moi@x.com"}])
    bao = rr.dung_bao_cao({"logged_in": 8}, {"logged_in": 40}, kq, "cảnh báo mẫu")
    assert bao["ok"] is True
    assert bao["truoc"]["logged_in"] == 8 and bao["sau"]["logged_in"] == 40
    assert bao["them"] == 1 and bao["bo"] == 1
    assert bao["them_email"] == ["moi@x.com"] and bao["bo_email"] == ["cu@x.com"]
    assert bao["giu_lai_vi_ban"] == 0
    assert bao["canh_bao"] == "cảnh báo mẫu"


# ═════════════════════════════════════════════════════════════════════════════
# PHẦN C — nhà máy ẢNH đang chạy trên :8789 (`image_pool_browser.py`)
# ═════════════════════════════════════════════════════════════════════════════

@pytest.fixture
def pool(monkeypatch):
    """Một `ImagePoolBrowser` KHÔNG start(): không Chrome, không sqlite thật,
    không ghi đè file trạng thái sản xuất."""
    import image_pool_browser as ipb

    p = ipb.ImagePoolBrowser(n_slots=4, log=lambda *a, **k: None)
    monkeypatch.setattr(p, "_save_state", lambda: None)   # KHÔNG đụng file thật
    monkeypatch.setattr(ipb, "_AUTHCACHE", None)
    monkeypatch.setattr(ipb, "_profile_logged_in", lambda e, on_read_error=False: True)
    p._state = {}
    p.candidates = []
    p.started_ts = time.time() - 10_000        # qua thời gian ân hạn
    return p


def _dat_kho(monkeypatch, emails):
    import image_pool_browser as ipb
    monkeypatch.setattr(ipb, "load_gmail_accounts",
                        lambda: [{"email": e, "password": "p", "totp": "t"} for e in emails])


def test_nap_lai_them_tai_khoan_moi_vao_nha_may_dang_chay(pool, monkeypatch):
    """Chính xác kịch bản 07/08: nhà máy bật với 64, kho có 96."""
    import image_pool_browser as ipb
    pool.candidates = [ipb.Account(i, f"a{i}@x.com", "p", "t") for i in range(64)]
    _dat_kho(monkeypatch, [f"a{i}@x.com" for i in range(96)])

    bao = pool.reload_accounts()

    assert bao["ok"] is True
    assert bao["truoc"]["candidates"] == 64
    assert bao["sau"]["candidates"] == 96
    assert bao["them"] == 32 and bao["bo"] == 0
    assert len(pool.candidates) == 96


def test_nap_lai_bo_tai_khoan_da_roi_kho(pool, monkeypatch):
    import image_pool_browser as ipb
    pool.candidates = [ipb.Account(0, "con@x.com", "p", "t"),
                       ipb.Account(1, "roi@x.com", "p", "t")]
    _dat_kho(monkeypatch, ["con@x.com"])

    bao = pool.reload_accounts()

    assert bao["bo_email"] == ["roi@x.com"]
    assert [a.email for a in pool.candidates] == ["con@x.com"]


def test_khong_dung_tai_khoan_dang_ban_o_nha_may_anh(pool, monkeypatch):
    """BÀI QUAN TRỌNG NHẤT.

    Dựng cảnh: một tài khoản ĐANG chạy job (slot đang giữ nó trong `active`),
    và kho vừa bỏ nó ra. Nạp lại xong, job đó phải còn nguyên: object không bị
    thay, không bị gỡ khỏi roster, `active` không bị đụng.
    """
    import image_pool_browser as ipb
    dang_chay = ipb.Account(0, "dangchay@x.com", "p", "t")
    dang_chay.busy = True
    dang_chay.project = "project-dang-dung"
    dang_chay.wins = 7
    ranh = ipb.Account(1, "ranh@x.com", "p", "t")
    pool.candidates = [dang_chay, ranh]
    pool.active = {"dangchay@x.com": dang_chay}          # slot đang giữ
    _dat_kho(monkeypatch, ["moi@x.com"])                 # kho bỏ CẢ HAI

    bao = pool.reload_accounts()

    # job đang chạy còn NGUYÊN: cùng object, cùng trạng thái
    assert dang_chay in pool.candidates
    assert pool.candidates[0] is dang_chay
    assert dang_chay.project == "project-dang-dung" and dang_chay.wins == 7
    assert pool.active["dangchay@x.com"] is dang_chay
    # còn cái RẢNH thì bị gỡ bình thường
    assert bao["giu_lai_vi_ban_email"] == ["dangchay@x.com"]
    assert bao["bo_email"] == ["ranh@x.com"]
    assert bao["them_email"] == ["moi@x.com"]


def test_tai_khoan_ban_duoc_don_not_o_luot_nap_sau(pool, monkeypatch):
    import image_pool_browser as ipb
    a = ipb.Account(0, "dangchay@x.com", "p", "t")
    pool.candidates = [a]
    pool.active = {"dangchay@x.com": a}
    _dat_kho(monkeypatch, ["moi@x.com"])

    assert pool.reload_accounts()["giu_lai_vi_ban"] == 1
    pool.active.clear()                                   # job xong, slot nhả
    bao2 = pool.reload_accounts()
    assert bao2["bo_email"] == ["dangchay@x.com"]
    assert [x.email for x in pool.candidates] == ["moi@x.com"]


def test_kho_doc_ra_rong_thi_GIU_NGUYEN_roster(pool, monkeypatch):
    """sqlite bị khoá/sai đường dẫn KHÔNG có nghĩa là kho trống. Tin nó là tự
    tay gỡ hết tài khoản đang chạy tốt."""
    import image_pool_browser as ipb
    pool.candidates = [ipb.Account(0, "a@x.com", "p", "t")]
    _dat_kho(monkeypatch, [])

    bao = pool.reload_accounts()

    assert bao["ok"] is False
    assert "rỗng" in bao["loi"]
    assert [a.email for a in pool.candidates] == ["a@x.com"]


def test_nap_lai_go_han_nghi_nologin_cho_tai_khoan_vua_co_phien(pool, monkeypatch):
    """Nạp phiên xong mà state cũ vẫn ghi 'nologin' + `until` thì `_in_cooldown`
    vẫn gạt nó ra — lượt nạp coi như vô ích. Phải gỡ hạn nghỉ đó."""
    pool._state = {"a@x.com": {"state": "nologin", "until": time.time() + 9999}}
    _dat_kho(monkeypatch, ["a@x.com"])

    bao = pool.reload_accounts()

    assert bao["go_han_nologin"] == 1
    assert pool._state["a@x.com"]["state"] == "new"
    assert "until" not in pool._state["a@x.com"]
    assert pool._in_cooldown("a@x.com") is False


def test_health_keu_len_khi_kho_lech(pool, monkeypatch):
    """Chạy chậm 8 lần mà IM LẶNG là lý do lỗi 07/08 sống được cả ca."""
    pool.n_kho_san_sang = 96
    pool._ever_ready = {f"a{i}@x.com" for i in range(8)}   # đúng 8/96

    h = pool.health()

    assert h["logged_in"] == 8 and h["kho_san_sang"] == 96
    assert h["roster_canh_bao"], "/health phải mang theo cảnh báo, không im lặng"
    assert "8/96" in h["roster_canh_bao"]


def test_health_im_khi_moi_thu_binh_thuong(pool):
    pool.n_kho_san_sang = 96
    pool._ever_ready = {f"a{i}@x.com" for i in range(90)}
    assert pool.health()["roster_canh_bao"] == ""


def test_nap_lai_giua_luc_nhieu_luong_doc_khong_mat_tai_khoan(pool, monkeypatch):
    """Nhà máy chạy `ThreadingHTTPServer` + một luồng thợ cho mỗi tài khoản. Sửa
    danh sách giữa lúc các luồng đó đang đọc là chỗ dễ sinh lỗi đua nhất.

    Ở đây 8 luồng đọc liên tục trong lúc 30 lượt nạp lại chạy song song. Yêu cầu:
    KHÔNG ngoại lệ, KHÔNG bao giờ đọc phải danh sách nửa vời (thiếu tài khoản
    hoặc trùng tài khoản).
    """
    import image_pool_browser as ipb

    kho_a = [f"a{i}@x.com" for i in range(40)]
    kho_b = [f"a{i}@x.com" for i in range(20)] + [f"b{i}@x.com" for i in range(30)]
    pool.candidates = [ipb.Account(i, e, "p", "t") for i, e in enumerate(kho_a)]

    hien_tai = {"kho": kho_a}
    monkeypatch.setattr(
        ipb, "load_gmail_accounts",
        lambda: [{"email": e, "password": "p", "totp": "t"} for e in hien_tai["kho"]])

    loi = []
    chay = threading.Event()
    chay.set()

    def _doc():
        try:
            while chay.is_set():
                ds = list(pool.candidates)          # đúng cách `_next_candidate` đọc
                emails = [a.email for a in ds]
                assert len(emails) == len(set(emails)), "roster có tài khoản TRÙNG"
                assert len(emails) in (40, 50), f"roster nửa vời: {len(emails)}"
        except Exception as e:  # noqa: BLE001
            loi.append(e)

    doc = [threading.Thread(target=_doc, daemon=True) for _ in range(8)]
    for t in doc:
        t.start()
    try:
        for i in range(30):
            hien_tai["kho"] = kho_b if i % 2 == 0 else kho_a
            pool.reload_accounts()
    finally:
        chay.clear()
        for t in doc:
            t.join(timeout=5)

    assert not loi, f"lỗi đua khi nạp lại: {loi[:3]}"
    assert len(pool.candidates) == len(set(a.email for a in pool.candidates))


def test_route_reload_accounts_ton_tai_va_tra_so_lieu(pool, monkeypatch):
    """Route phải có mặt trong `do_POST` và trả đúng thân báo cáo."""
    import image_pool_browser as ipb
    import inspect

    nguon = inspect.getsource(ipb._Handler.do_POST)
    assert "/reload_accounts" in nguon

    pool.candidates = [ipb.Account(0, "a@x.com", "p", "t")]
    _dat_kho(monkeypatch, ["a@x.com", "b@x.com"])
    bao = pool.reload_accounts()
    for khoa in ("ok", "truoc", "sau", "them", "bo", "giu_lai_vi_ban", "canh_bao"):
        assert khoa in bao
    assert bao["truoc"]["candidates"] == 1 and bao["sau"]["candidates"] == 2


# ═════════════════════════════════════════════════════════════════════════════
# PHẦN D — nhà máy ẢNH bản curl (`image_factory.py`, :8789 khi tắt browser pool)
# ═════════════════════════════════════════════════════════════════════════════

@pytest.fixture
def imgf(monkeypatch):
    monkeypatch.setenv("VEO3TOP_POOL_USE_IPV6", "0")   # KHÔNG gọi netsh
    import image_factory as imf

    f = imf.ImageFactory(log=lambda *a, **k: None)
    monkeypatch.setattr(f, "_startup_check", lambda accounts=None: 0)   # KHÔNG gọi mạng
    monkeypatch.setattr(f, "_co_phien", lambda email: True)
    f.started_ts = time.time() - 10_000
    return f


def _kho_anh(monkeypatch, emails):
    import image_factory as imf
    monkeypatch.setattr(imf, "load_image_pool_accounts",
                        lambda: [{"name": e.split("@")[0], "email": e,
                                  "chrome_path": "", "bundle": f"{e}||"} for e in emails])


def test_image_factory_them_moi_va_bo_chet(imgf, monkeypatch):
    import image_factory as imf
    imgf.accounts = [imf.Account("cu", "cu@x.com", "", imgf.cache),
                     imf.Account("roi", "roi@x.com", "", imgf.cache)]
    _kho_anh(monkeypatch, ["cu@x.com", "moi@x.com"])

    bao = imgf.reload_accounts()

    assert bao["them_email"] == ["moi@x.com"] and bao["bo_email"] == ["roi@x.com"]
    assert [a.email for a in imgf.accounts] == ["cu@x.com", "moi@x.com"]


def test_image_factory_khong_dung_account_dang_ban(imgf, monkeypatch):
    import image_factory as imf
    ban = imf.Account("ban", "ban@x.com", "", imgf.cache)
    ban.busy = True
    ban.wins = 5
    imgf.accounts = [ban]
    _kho_anh(monkeypatch, ["khac@x.com"])

    bao = imgf.reload_accounts()

    assert bao["giu_lai_vi_ban_email"] == ["ban@x.com"]
    assert ban in imgf.accounts
    assert ban.retired is False and ban.wins == 5


def test_image_factory_khong_go_account_dang_duoc_chua_nen(imgf, monkeypatch):
    """Gỡ giữa lúc RecoveryManager đang chữa = ghi cookie cho object không ai
    dùng, và để lại Chrome mồ côi."""
    import image_factory as imf
    a = imf.Account("chua", "chua@x.com", "", imgf.cache)
    a._recovering = True
    imgf.accounts = [a]
    _kho_anh(monkeypatch, ["khac@x.com"])

    bao = imgf.reload_accounts()

    assert bao["giu_lai_vi_ban_email"] == ["chua@x.com"]
    assert a.retired is False


def test_image_factory_account_bi_go_thi_duoc_danh_dau_retired(imgf, monkeypatch):
    import image_factory as imf
    a = imf.Account("roi", "roi@x.com", "", imgf.cache)
    imgf.accounts = [a]
    _kho_anh(monkeypatch, ["khac@x.com"])

    imgf.reload_accounts()

    assert a.retired is True, "luồng thợ của nó phải biết đường tự thoát"


def test_luong_tho_thoat_SAU_khi_job_hien_tai_xong_chu_khong_cat_ngang(imgf):
    """Gỡ một account KHÔNG BAO GIỜ được cắt ngang job đang chạy.

    Dựng cảnh: job vào tay thợ, GIỮA lúc xử lý thì account bị `retired` (đúng
    như nạp lại nóng làm). Yêu cầu: job vẫn chạy hết và trả kết quả, rồi luồng
    thợ mới thoát.
    """
    import image_factory as imf
    import queue as _q

    a = imf.Account("a", "a@x.com", "", imgf.cache)
    imgf.accounts = [a]
    imgf._running = True
    da_xu_ly = []

    def _process_gia(account, job):
        account.retired = True          # nạp lại nóng gỡ account NGAY LÚC NÀY
        time.sleep(0.05)                # job vẫn đang chạy dở
        job["_result"] = (True, {"account": account.email}, "")
        da_xu_ly.append(job)
        return "success"

    imgf._process = _process_gia
    job = {"prompt": "x", "out_path": "y", "_event": threading.Event()}
    imgf.q.put(job)

    t = threading.Thread(target=imgf._worker, args=(a,), daemon=True)
    t.start()
    assert job["_event"].wait(timeout=5), "job đang chạy dở BỊ GIẾT khi gỡ account"
    t.join(timeout=5)

    assert not t.is_alive(), "luồng thợ của account đã gỡ phải tự thoát"
    assert job["_result"][0] is True and len(da_xu_ly) == 1
    assert imgf.total_done == 1


def test_luong_tho_thoat_ngay_neu_account_da_retired_tu_truoc(imgf):
    import image_factory as imf
    a = imf.Account("a", "a@x.com", "", imgf.cache)
    a.retired = True
    imgf._running = True
    t = threading.Thread(target=imgf._worker, args=(a,), daemon=True)
    t.start(); t.join(timeout=5)
    assert not t.is_alive()


def test_image_factory_health_mang_theo_canh_bao_kho_lech(imgf, monkeypatch):
    import image_factory as imf
    imgf.accounts = [imf.Account(f"a{i}", f"a{i}@x.com", "", imgf.cache) for i in range(64)]
    for a in imgf.accounts[:8]:
        a.phien_ok = True                 # đúng số đo: 8 đăng nhập được
    # 56 cái còn lại phải là ĐÃ KIỂM VÀ CHẾT thì cảnh báo mới đúng. Từ bản vá
    # kiểm-phiên-ba-trạng-thái (07/08/2026), account "chưa kiểm" VẪN được worker
    # giao việc nên nó KHÔNG bị tính là thiếu — xem `phien_kiem.dem_trang_thai`.
    for a in imgf.accounts[8:]:
        a.phien_ok = False
    imgf.n_kho_san_sang = 96

    h = imgf.health()

    # `logged_in` đã bị xoá hẳn: nó là bộ đếm tiến trình kiểm đội lốt số account
    # đăng nhập được (đo được 8 -> 41 trong 30 phút). Bốn khoá `phien_*` thay nó.
    assert "logged_in" not in h
    assert h["phien_song"] == 8 and h["phien_dung_duoc"] == 8 and h["kho_san_sang"] == 96
    assert "8/96" in h["roster_canh_bao"]


def test_image_factory_kho_rong_thi_giu_nguyen(imgf, monkeypatch):
    import image_factory as imf
    imgf.accounts = [imf.Account("a", "a@x.com", "", imgf.cache)]
    _kho_anh(monkeypatch, [])
    bao = imgf.reload_accounts()
    assert bao["ok"] is False
    assert [a.email for a in imgf.accounts] == ["a@x.com"]


# ═════════════════════════════════════════════════════════════════════════════
# PHẦN E — nhà máy VIDEO (`video_factory.py`, :8788)
# ═════════════════════════════════════════════════════════════════════════════

@pytest.fixture
def vidf(monkeypatch):
    monkeypatch.setenv("VEO3TOP_POOL_USE_IPV6", "0")
    import video_factory as vf

    f = vf.VideoFactory(log=lambda *a, **k: None)
    monkeypatch.setattr(f, "_startup_check", lambda accounts=None: 0)
    monkeypatch.setattr(f, "_co_phien", lambda email: True)
    f.started_ts = time.time() - 10_000
    return f


def _kho_video(monkeypatch, emails):
    import video_factory as vf
    monkeypatch.setattr(vf, "load_pool_accounts",
                        lambda: [{"name": e.split("@")[0], "email": e,
                                  "chrome_path": "", "bundle": f"{e}||"} for e in emails])


def test_video_factory_them_moi_va_bo_chet(vidf, monkeypatch):
    import video_factory as vf
    vidf.accounts = [vf.Account("cu", "cu@x.com", "", vidf.cache),
                     vf.Account("roi", "roi@x.com", "", vidf.cache)]
    _kho_video(monkeypatch, ["cu@x.com", "moi@x.com"])

    bao = vidf.reload_accounts()

    assert bao["them_email"] == ["moi@x.com"] and bao["bo_email"] == ["roi@x.com"]
    assert [a.email for a in vidf.accounts] == ["cu@x.com", "moi@x.com"]
    assert bao["truoc"]["accounts"] == 2 and bao["sau"]["accounts"] == 2


def test_video_factory_khong_dung_account_dang_ban(vidf, monkeypatch):
    import video_factory as vf
    ban = vf.Account("ban", "ban@x.com", "", vidf.cache)
    ban.busy = True
    vidf.accounts = [ban]
    _kho_video(monkeypatch, ["khac@x.com"])

    bao = vidf.reload_accounts()

    assert bao["giu_lai_vi_ban_email"] == ["ban@x.com"]
    assert ban in vidf.accounts and ban.retired is False


def test_video_factory_luong_tho_thoat_sau_khi_job_xong(vidf):
    import video_factory as vf

    a = vf.Account("a", "a@x.com", "", vidf.cache)
    vidf._running = True

    def _process_gia(account, job):
        account.retired = True
        time.sleep(0.05)
        job["_result"] = (True, {"account": account.email}, "")
        return "success"

    vidf._process = _process_gia
    job = {"image_path": "i", "prompt": "p", "out_path": "o", "_event": threading.Event()}
    vidf.q.put(job)
    t = threading.Thread(target=vidf._worker, args=(a,), daemon=True)
    t.start()
    assert job["_event"].wait(timeout=5)
    t.join(timeout=5)
    assert not t.is_alive() and job["_result"][0] is True


def test_video_factory_health_mang_theo_canh_bao(vidf):
    import video_factory as vf
    vidf.accounts = [vf.Account(f"a{i}", f"a{i}@x.com", "", vidf.cache) for i in range(10)]
    vidf.accounts[0].phien_ok = True
    for a in vidf.accounts[1:]:
        a.phien_ok = False      # ĐÃ KIỂM VÀ CHẾT (khác "chưa kiểm" — cái đó vẫn chạy việc)
    vidf.n_kho_san_sang = 10
    h = vidf.health()
    assert "logged_in" not in h
    assert h["phien_song"] == 1 and "1/10" in h["roster_canh_bao"]


def test_ca_ba_nha_may_deu_co_route_nap_lai_nong():
    """Yêu cầu: CẢ HAI nhà máy phải có đường nạp nóng — và nhà máy ẢNH đang
    chạy thật trên :8789 là `image_pool_browser`, không phải `image_factory`
    (xem `workers/veo3/engine/factory_client.py` dòng 3). Nên phủ cả ba."""
    import inspect
    import image_factory, image_pool_browser, video_factory

    for mod in (image_factory, image_pool_browser, video_factory):
        nguon = inspect.getsource(mod._Handler.do_POST)
        assert "/reload_accounts" in nguon, f"{mod.__name__} thiếu route nạp nóng"
    assert hasattr(image_factory.ImageFactory, "reload_accounts")
    assert hasattr(image_pool_browser.ImagePoolBrowser, "reload_accounts")
    assert hasattr(video_factory.VideoFactory, "reload_accounts")
