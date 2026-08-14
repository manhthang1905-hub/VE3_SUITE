"""Kiểm bộ máy chạy CẢ MẺ song song (`veo3top_engine/shopapi_batch.py`).

KHÔNG một byte nào ra mạng: mọi lời hỏi `/v1/me` đều bị thay bằng hàm giả, và
"chạy một việc" là một hàm Python thường.

Điều phải giữ đúng — mỗi cái là một cách hỏng đã thấy trong thực tế:

* số luồng **đọc từ máy chủ**, không gõ cứng, và không vượt trần cứng lẫn trần
  người dùng đặt;
* `concurrent_jobs = 0` là **nhà máy đang dừng** → chờ rồi hỏi lại, KHÔNG chạy
  liều 1 job, và cũng KHÔNG treo vô hạn;
* `429` → hạ nhịp, và việc bị từ chối **quay lại hàng chờ chứ không mất**;
* kết quả trả **đúng thứ tự đưa vào** dù xong lộn xộn;
* một job hỏng giữa mẻ **không kéo cả mẻ chết**.
"""

from __future__ import annotations

import time

import pytest

import shopapi_batch as sb


# ── Đồ nghề ──────────────────────────────────────────────────────────────────


@pytest.fixture
def tran_gia(monkeypatch, sc):
    """Thay `GET /v1/me` bằng một hàm giả. Trả về hàm đặt kịch bản trần."""

    def _dat(*gia_tri):
        """`_dat(0, 0, 5)` = hỏi lần 1 và 2 ra 0, từ lần 3 trở đi ra 5."""
        so_lan = {"n": 0}

        def _tran(loai, api_key=None, mac_dinh=1, client=None):
            i = so_lan["n"]
            so_lan["n"] += 1
            return gia_tri[min(i, len(gia_tri) - 1)]

        monkeypatch.setattr(sc, "tran_song_song", _tran)
        return so_lan

    return _dat


@pytest.fixture
def ngu_gia():
    """`ngu` giả: ghi lại đã "ngủ" bao nhiêu lần, KHÔNG chờ thật một giây nào."""
    da_ngu = []

    def _ngu(giay):
        da_ngu.append(float(giay))
        return True

    _ngu.lan = da_ngu
    return _ngu


@pytest.fixture
def me_nhanh():
    """Tham số cho `chay_ca_me` để mọi quãng CHỜ trôi qua tức thì, vẫn ĐÚNG LUẬT.

    Từ bản sửa ngày 07/08/2026, một cú `429` đặt quãng lùi nhịp cho **cả mẻ**
    (`CongHangCho.bi_nghen`) — nếu không thì việc bị từ chối quay về đầu hàng chờ
    rồi được gửi lại sau vài mili-giây, tức là "gửi lại NGAY", đúng thứ đề bài
    cấm. Quãng đó dài 15 giây thật.

    Fixture này **không tắt** luật ấy — nó chỉ thay đồng hồ: `ngu` vặn kim thay
    vì ngồi chờ. Vòng lặp vẫn phải đi qua đúng nhánh chờ, vẫn phải ghi log, chỉ
    là hết trong vài micro-giây. Đây là cách duy nhất giữ được cả tính đúng của
    sản phẩm lẫn tốc độ của bộ kiểm.
    """
    dong_ho = {"t": 1000.0}

    def _ngu(giay):
        """Vặn kim thay vì ngồi chờ — vòng lặp vẫn đi qua đúng nhánh chờ."""
        _ngu.lan.append(float(giay))
        dong_ho["t"] += float(giay)

    _ngu.lan = []
    return {"cong": sb.CongHangCho(_dong_ho=lambda: dong_ho["t"]), "ngu": _ngu}


def _lo_da_ban(nhat_ky):
    """Số job GỬI THÊM ở mỗi lượt — cách duy nhất soi nhịp mà không phá đóng gói."""
    ra = []
    for _lv, m in nhat_ky.dong:
        if "-> ban them " in m:
            ra.append(int(m.split("-> ban them ")[1].split(" job")[0]))
    return ra


def _dang_bay(nhat_ky):
    """Số job ĐANG BAY ở mỗi lượt — thước đo song song THẬT.

    Từ khi bỏ hàng rào mỗi lô, "số job gửi thêm lượt này" không còn là số job
    chạy cùng lúc: lượt sau chỉ lấp phần chỗ vừa trống. Trần máy chủ chặn TỔNG
    số job đang bay, nên đây mới là con số phải đem đi so với nó.
    """
    ra = []
    for _lv, m in nhat_ky.dong:
        if " dang bay, " in m:
            ra.append(int(m.split("(")[1].split(" dang bay")[0]))
    return ra


# ── Số luồng: lấy từ máy chủ, KHÔNG gõ cứng ──────────────────────────────────


def test_so_luong_lay_dung_con_so_may_chu_tra_ve(tran_gia, nhat_ky):
    tran_gia(9)
    assert sb.so_luong_song_song("image", log=nhat_ky) == 9


def test_so_luong_bi_chan_tren_boi_tran_nguoi_dung_dat(tran_gia, nhat_ky):
    """TẮT tự điều tiết thì máy chủ rộng bao nhiêu cũng không phá được trần người dùng.

    Bật tự điều tiết (mặc định) thì `tran_tool` KHÔNG còn là trần — trần đến từ
    máy chủ chia cho số tiến trình đang sống. Muốn ghim cứng thì tắt cờ; đó là
    lý do cờ tồn tại. Xem `test_TU_DIEU_TIET_bo_qua_tran_go_tay` ngay dưới.
    """
    tran_gia(50)
    assert sb.so_luong_song_song("image", tran_tool=4, log=nhat_ky,
                                 tu_dieu_tiet=False) == 4


def test_TU_DIEU_TIET_bo_qua_tran_go_tay_va_an_theo_may_chu(tran_gia, nhat_ky, monkeypatch, sc):
    """Con số gõ tay không được phép bóp nhà máy khi máy chủ đang mời rộng.

    Đây là lỗi người vận hành thấy ngày 15/08/2026: `/v1/me` mời 979 chỗ ảnh mà
    `max_concurrent: 40` giữ tool ở 40 — "xin 290 mà chỉ 22 job chạy thật".
    """
    monkeypatch.setattr(sc, "dem_ban_dang_chay", lambda *a, **k: 1)
    monkeypatch.setattr(sc, "phan_luong_cua_toi", lambda *a, **k: 10 ** 6)
    monkeypatch.setattr(sc, "tran_cung_may_chu", lambda *a, **k: 10 ** 6)
    tran_gia(500)
    assert sb.so_luong_song_song("image", tran_tool=4, log=nhat_ky,
                                 tu_dieu_tiet=True) == 500


def test_TU_DIEU_TIET_chia_deu_cho_so_tien_trinh_dang_song(tran_gia, nhat_ky, monkeypatch, sc):
    """Tám mã cùng chạy thì mỗi mã một phần tám, không phải mỗi mã trọn trần."""
    monkeypatch.setattr(sc, "phan_luong_cua_toi", lambda *a, **k: 10 ** 6)
    monkeypatch.setattr(sc, "tran_cung_may_chu", lambda *a, **k: 10 ** 6)
    tran_gia(800)
    for ban, mong_doi in ((1, 800), (2, 400), (8, 100)):
        monkeypatch.setattr(sc, "dem_ban_dang_chay", lambda *a, **k: ban)
        assert sb.so_luong_song_song("image", log=nhat_ky, tu_dieu_tiet=True) == mong_doi


def test_TU_DIEU_TIET_con_bi_cat_boi_SUAT_LUONG_cua_may(tran_gia, nhat_ky, monkeypatch, sc):
    """Máy chủ rộng mấy cũng không mở nổi nhiều luồng hơn máy này chịu."""
    monkeypatch.setattr(sc, "dem_ban_dang_chay", lambda *a, **k: 1)
    monkeypatch.setattr(sc, "tran_cung_may_chu", lambda *a, **k: 10 ** 6)
    monkeypatch.setattr(sc, "phan_luong_cua_toi", lambda *a, **k: 75)
    tran_gia(900)
    assert sb.so_luong_song_song("image", log=nhat_ky, tu_dieu_tiet=True) == 75


def test_so_luong_khong_bao_gio_vuot_tran_CUNG_cua_loai_job(tran_gia, nhat_ky, sc, monkeypatch):
    """Một lời `/v1/me` trả số vô lý cũng không biến thành 9999 luồng."""
    # KHONG go cung con so: may chu nang tran la `tran_cung` an theo ngay. Da
    # lech mot lan (anh 128 -> 384) va lam do bai kiem nay du ma van dung.
    # Doi chieu VOI NGUON THAT, chi doi hoi no la mot tran huu han > 0.
    # NGUỒN THẬT của trần cứng giờ là `/v1/me` (`hard_cap`), KHÔNG phải hằng số
    # chép trong SDK. Đo 15/08/2026: máy chủ khai ảnh 1.536 / video 832, còn
    # hằng số vẫn 384 / 64 — đối chiếu với bản chép là khoá tool vào số đã cũ.
    for loai, cung in (("image", 1536), ("video", 832)):
        monkeypatch.setattr(sc, "tran_cung_may_chu", lambda *a, **k: cung)
        tran_gia(9999)
        assert sb.so_luong_song_song(loai, log=nhat_ky, tu_dieu_tiet=False) == cung


def test_may_chu_khong_khai_tran_cung_thi_lui_ve_HANG_SO(tran_gia, nhat_ky, sc, monkeypatch):
    """Không đọc được `hard_cap` thì hằng số chép sẵn là lưới cuối, không phải 9999."""
    monkeypatch.setattr(sc, "tran_cung_may_chu", lambda loai, **k: sc.tran_cung(loai))
    for loai in ("image", "video"):
        tran_gia(9999)
        cung = sc.tran_cung(loai)
        assert 0 < cung < 9999
        assert sb.so_luong_song_song(loai, log=nhat_ky, tu_dieu_tiet=False) == cung


def test_hoi_khong_duoc_thi_doan_THAP_chu_khong_dung_im(monkeypatch, sc, nhat_ky):
    def _hong(*a, **kw):
        raise RuntimeError("mat mang")

    monkeypatch.setattr(sc, "tran_song_song", _hong)
    monkeypatch.setattr(sc, "phan_luong_cua_toi", lambda *a, **k: 10 ** 6)
    n = sb.so_luong_song_song("image", log=nhat_ky)
    assert n == sb.TRAN_KHOI_DONG_MU, "hong doc thi mo vua phai, khong dung im ma cung khong ve 1"
    assert 0 < n < sc.tran_cung("image"), "doan mu ma bang tran cung la dap vao nha may bang so bia"


# ── `0` = nhà máy đang dừng ──────────────────────────────────────────────────


def test_tran_0_thi_CHO_ROI_HOI_LAI_chu_khong_chay_1_job(tran_gia, nhat_ky, ngu_gia):
    """`0` KHÔNG phải "chạy 1 job": gửi vào nhà máy đóng cửa là chắc chắn 503."""
    dem = tran_gia(0, 0, 6)

    n = sb.so_luong_song_song("image", log=nhat_ky, ngu=ngu_gia)

    assert n == 6, "phai cho toi luc nha may mo lai roi moi tra so that"
    assert dem["n"] == 3, "phai HOI LAI, khong phai doan"
    assert len(ngu_gia.lan) == 2, "moi lan hoi lai phai co mot quang cho"
    assert any(lv == "WARN" and "DANG DUNG" in m for lv, m in nhat_ky.dong), \
        "phai bao cho nguoi dung biet la dang CHO, khong phai treo"


def test_nha_may_dung_mai_thi_bo_cuoc_CO_KIEM_SOAT_khong_treo(tran_gia, nhat_ky, ngu_gia):
    """Treo vô hạn là kiểu hỏng tệ nhất: không ai biết nó đang hỏng."""
    tran_gia(0)

    t0 = time.time()
    n = sb.so_luong_song_song("image", log=nhat_ky, ngu=ngu_gia,
                              cho_khi_dung=30.0, cho_toi_da=120.0)

    assert n == 0
    assert len(ngu_gia.lan) == 4, "120s / 30s = 4 vong cho roi bo"
    assert time.time() - t0 < 5.0, "khong duoc cho THAT trong bai kiem"
    assert any(lv == "ERROR" and "qua lau" in m for lv, m in nhat_ky.dong)


def test_nhan_lenh_dung_thi_thoi_cho_ngay(tran_gia, nhat_ky, ngu_gia):
    tran_gia(0)
    assert sb.so_luong_song_song("image", log=nhat_ky, ngu=ngu_gia,
                                 dung_lai=lambda: True) == 0
    assert ngu_gia.lan == [], "da bao dung thi khong duoc ngu them giay nao"


# ── Chạy cả mẻ: thứ tự, không mất việc, không chết chùm ──────────────────────


def test_ket_qua_dung_THU_TU_DUA_VAO_du_xong_lon_xon(tran_gia, nhat_ky):
    """Nơi gọi ghép kết quả với scene bằng VỊ TRÍ — trả sai thứ tự là ghi nhầm scene."""
    tran_gia(8)

    def _chay(v):
        # Việc số hiệu càng lớn xong càng SỚM -> thứ tự hoàn thành ngược hẳn.
        time.sleep((12 - v) / 2000.0)
        return "ket-{0}".format(v)

    ket = sb.chay_ca_me(list(range(12)), _chay, "image", log=nhat_ky)

    assert ket == ["ket-{0}".format(i) for i in range(12)]


def test_mot_job_hong_KHONG_keo_ca_me_chet(tran_gia, nhat_ky):
    """200 scene chạy 4 tiếng không được chết vì một prompt bị từ chối nội dung."""
    tran_gia(8)

    def _chay(v):
        if v == 3:
            raise RuntimeError("noi dung bi tu choi")
        return v

    ket = sb.chay_ca_me(list(range(6)), _chay, "image", log=nhat_ky)

    assert ket[3] is False, "viec hong dem la that bai"
    assert [ket[i] for i in (0, 1, 2, 4, 5)] == [0, 1, 2, 4, 5], "5 viec kia van xong"
    assert any("mot viec hong" in m for _lv, m in nhat_ky.dong)


def test_429_thi_HA_NHIP_va_viec_bi_tu_choi_KHONG_MAT(tran_gia, nhat_ky, sc, me_nhanh):
    """`429` = "bạn nhanh quá", KHÔNG phải "việc này hỏng"."""
    tran_gia(16)
    so_lan = {}

    def _chay(v):
        so_lan[v] = so_lan.get(v, 0) + 1
        # Ba việc bị chặn ở lần chạy ĐẦU, lần sau thì trơn.
        if v in (2, 3, 4) and so_lan[v] == 1:
            raise sc.BiNghen(429)
        return v

    ket = sb.chay_ca_me(list(range(10)), _chay, "image", log=nhat_ky, **me_nhanh)

    assert ket == list(range(10)), "KHONG duoc mat viec nao"
    assert so_lan[2] == 2 and so_lan[3] == 2 and so_lan[4] == 2, "phai chay lai"
    assert any("TRA VE DAU HANG CHO" in m for _lv, m in nhat_ky.dong)

    lo = _lo_da_ban(nhat_ky)
    # Lô ăn 429 phải kéo lô kế tiếp NHỎ LẠI (chia đôi), chứ không tăng đều tiếp.
    assert min(lo[1:]) < max(lo[:-1]), "nhip phai giam sau khi an 429, khong duoc tang deu"


def test_viec_bi_tu_choi_quay_ve_DAU_hang_cho(tran_gia, nhat_ky, sc, me_nhanh):
    """Về cuối hàng chờ là việc đó chờ hết cả mẻ — về đầu mới đúng."""
    tran_gia(2)
    thu_tu = []
    so_lan = {}

    def _chay(v):
        so_lan[v] = so_lan.get(v, 0) + 1
        if v == 0 and so_lan[v] == 1:
            raise sc.BiNghen(429)
        thu_tu.append(v)
        return v

    sb.chay_ca_me(list(range(8)), _chay, "image", log=nhat_ky, **me_nhanh)

    # Việc 0 bị hoãn ở lô đầu nhưng phải chạy lại NGAY lô sau, không phải cuối mẻ.
    assert thu_tu.index(0) < 4, "viec bi hoan phai duoc uu tien chay lai, thu tu={0}".format(thu_tu)


def test_503_thi_DUNG_HAN_roi_tham_do_lai_bang_MOT_job(tran_gia, nhat_ky, sc):
    """`503` khác `429`: chia đôi thành 8 luồng gõ cửa nhà máy đóng vẫn vô nghĩa.

    Bài này ngủ THẬT nhưng chỉ 20 mili-giây: quãng dừng của vòng dò đo bằng đồng
    hồ thật, nên ngủ giả (không nhích đồng hồ) sẽ làm nó không bao giờ hết hạn.
    Cách rẻ nhất để dựng lại tình huống là cho máy chủ gửi `Retry-After` cực ngắn.
    """
    tran_gia(16)
    so_lan = {}
    da_ngu = []

    def _chay(v):
        so_lan[v] = so_lan.get(v, 0) + 1
        if v == 1 and so_lan[v] == 1:
            raise sc.BiNghen(503, cho=0.02)     # Retry-After = 20ms
        return v

    def _ngu(giay):
        da_ngu.append(giay)
        time.sleep(min(float(giay), 0.05))
        return True

    ket = sb.chay_ca_me(list(range(6)), _chay, "image", log=nhat_ky,
                        ngu=_ngu, cho_khi_dung=0.02)

    assert ket == list(range(6)), "503 KHONG lam mat viec (va khong bi tru tien)"
    assert da_ngu, "gap 503 phai CHO chu khong ban tiep ngay"
    lo = _lo_da_ban(nhat_ky)
    assert 1 in lo[1:], "sau khi nha may dung phai tham do lai bang DUNG 1 job"


def test_nha_may_dung_giua_me_thi_bo_cuoc_co_kiem_soat(tran_gia, nhat_ky, ngu_gia, sc):
    """Nhà máy chết hẳn giữa mẻ: bỏ phần còn lại kèm log TO, không treo qua đêm."""
    tran_gia(16)

    def _chay(v):
        raise sc.BiNghen(503)

    t0 = time.time()
    ket = sb.chay_ca_me(list(range(5)), _chay, "image", log=nhat_ky, ngu=ngu_gia,
                        cho_khi_dung=30.0, cho_toi_da=90.0)

    assert time.time() - t0 < 5.0, "khong duoc cho THAT"
    assert all(r is None for r in ket), "viec chua chay -> None, khong phai 'that bai'"
    assert any(lv == "ERROR" and "qua lau" in m for lv, m in nhat_ky.dong)


def test_lo_KHONG_BAO_GIO_vuot_tran_may_chu(tran_gia, nhat_ky):
    """Trần máy chủ là mức KHÔNG ĐƯỢC VƯỢT — kể cả khi vòng dò đang muốn tăng.

    Đo TỔNG SỐ JOB ĐANG BAY, không đo số gửi thêm mỗi lượt: từ khi bỏ hàng rào,
    trần chặn cái thứ nhất chứ không chặn cái thứ hai.
    """
    tran_gia(3)

    ket = sb.chay_ca_me(list(range(30)), lambda v: v, "image", log=nhat_ky)

    assert ket == list(range(30))
    assert max(_dang_bay(nhat_ky)) <= 3


def test_tran_nguoi_dung_dat_van_thang_khi_may_chu_rong(tran_gia, nhat_ky):
    tran_gia(64)
    sb.chay_ca_me(list(range(30)), lambda v: v, "image", tran_tool=2, log=nhat_ky,
                  tu_dieu_tiet=False)
    assert max(_dang_bay(nhat_ky)) <= 2


def test_bat_dau_NGAY_o_tran_may_chu_chu_khong_bo_len_tu_1(tran_gia, nhat_ky):
    """Lượt gửi ĐẦU TIÊN phải dùng trọn chỗ máy chủ cấp.

    ═══ ĐÂY LÀ BÀI KIỂM ĐẮT NHẤT FILE NÀY ═══

    Bản trước bắt đầu ở nhịp 1 và tăng +1 mỗi lô mượt, nên muốn đạt nhịp N phải
    chạy hết N(N+1)/2 job. Đo ngày 11/08/2026: máy chủ cấp 691 chỗ ảnh, một mã
    87 scene bò tới nhịp 12 là hết việc, bình quân **6,7 job cùng lúc = 1% chỗ
    được cấp**. Mỗi pha lại dựng vòng dò MỚI nên mẻ nào cũng bò lại từ đầu —
    tool không bao giờ tích luỹ được gì.

    Quy ra tiền bạc thời gian: 4.206 job còn tồn chạy hết trong ~33 tiếng thay
    vì ~10 phút. Không một dòng lỗi nào, không một chỉ số nào đỏ.
    """
    tran_gia(50)
    sb.chay_ca_me(list(range(200)), lambda v: v, "image", log=nhat_ky)
    lo = _lo_da_ban(nhat_ky)
    assert lo, "khong ban lo nao"
    assert lo[0] == 50, (
        "luot dau chi ban {0} job trong khi may chu cap 50 — vong do dang bo len "
        "tu 1 tro lai".format(lo[0])
    )


def test_dung_chung_vong_do_thi_me_sau_KHONG_bo_lai_tu_dau(tran_gia, nhat_ky):
    """Truyền `nhip` dùng chung -> mẻ sau thừa hưởng nhịp đã dò được.

    Đây là lý do tham số `nhip` tồn tại, và trước 11/08/2026 không ai truyền nó:
    mỗi pha của mỗi mã dựng một vòng dò mới, học tới đâu vứt tới đó.
    """
    tran_gia(40)
    chung = sb._tao_nhip(bat_dau=40)

    sb.chay_ca_me(list(range(20)), lambda v: v, "image", log=nhat_ky, nhip=chung)
    sb.chay_ca_me(list(range(20)), lambda v: v, "image", log=nhat_ky, nhip=chung)

    lo = _lo_da_ban(nhat_ky)
    assert len(lo) >= 2
    assert all(n >= 20 for n in lo), (
        "me sau tut xuong {0} — vong do dung chung ma van bi dung lai tu dau".format(lo)
    )


def test_me_rong_thi_khong_goi_gi_ca(tran_gia, nhat_ky):
    dem = tran_gia(8)
    assert sb.chay_ca_me([], lambda v: v, "image", log=nhat_ky) == []
    assert dem["n"] == 0, "me rong ma van hoi /v1/me la dot han muc doc trang thai"


def test_dung_giua_chung_thi_phan_con_lai_la_None(tran_gia, nhat_ky):
    tran_gia(2)
    da_chay = []
    co_dung = {"v": False}

    def _chay(v):
        da_chay.append(v)
        co_dung["v"] = True         # bấm Dừng ngay sau việc đầu tiên
        return v

    ket = sb.chay_ca_me(list(range(10)), _chay, "image", log=nhat_ky,
                        dung_lai=lambda: co_dung["v"])

    assert len(da_chay) < 10
    assert ket[-1] is None
    assert any("nhan lenh DUNG" in m for _lv, m in nhat_ky.dong)


# ── Cờ "đang trong mẻ" ───────────────────────────────────────────────────────


def test_co_trong_me_chi_bat_ben_trong_me(tran_gia, nhat_ky):
    """Cờ này quyết định nhánh gửi có được NÉM `BiNghen` ra ngoài hay không."""
    tran_gia(4)
    assert sb.trong_me() is False

    thay = []
    sb.chay_ca_me([1, 2], lambda v: thay.append(sb.trong_me()), "image", log=nhat_ky)

    assert thay == [True, True]
    assert sb.trong_me() is False, "ra khoi me phai tra co ve nhu cu"


# ── Hai lớp an toàn của việc bỏ hàng rào ─────────────────────────────────────
#
# Bỏ hàng rào làm vòng lặp quay gần một lần cho MỖI job xong, thay vì 13 lần cho
# cả mẻ 88 việc. Hai thứ vốn vô hại ở nhịp cũ trở thành nguy hiểm ở nhịp mới, và
# cả hai đều hỏng theo kiểu tự bóp thông lượng — tức là đúng thứ vừa đi sửa.


def test_KHONG_hoi_v1_me_moi_vong_lap(tran_gia, nhat_ky):
    """CONTRACT.md §8.2b: hỏi `/v1/me` quá dày là tự đốt hạn mức đọc trạng thái.

    Đốt hết hạn mức thì `_hoi_tran` bắt đầu trả mức đoán, mức đoán kéo nhịp
    xuống, và thông lượng tụt — qua một đường vòng mà không dòng log nào nối hai
    đầu lại được.
    """
    dem = tran_gia(64)
    sb.chay_ca_me(list(range(200)), lambda v: v, "image", log=nhat_ky)
    assert dem["n"] <= 3, (
        "hoi /v1/me {0} lan cho MOT me — TRAN_TTL khong con tac dung".format(dem["n"]))


def test_doc_tran_hong_thi_GIU_tran_cu_chu_khong_tut_ve_1(tran_gia, nhat_ky, sc, monkeypatch):
    """Một cú mạng chập KHÔNG được đổi lấy cả phần còn lại của mẻ chạy ở tốc độ bò.

    `NhipDo.dat_tran(1)` nghiền nhịp xuống 1 ngay lập tức, mà luật tăng là +1 mỗi
    lô — nên trả `1` lúc đọc hỏng chính là dựng lại đúng cái bệnh vừa chữa.
    """
    lan = {"n": 0}

    def _tran(loai, api_key=None, mac_dinh=1, client=None):
        lan["n"] += 1
        if lan["n"] == 1:
            return 40
        raise OSError("mang chap")

    monkeypatch.setattr(sc, "tran_song_song", _tran)
    # TTL = 0 để mọi vòng đều phải đi hỏi lại -> lượt hỏi thứ hai trở đi đều hỏng.
    monkeypatch.setattr(sb, "TRAN_TTL", 0.0)

    ket = sb.chay_ca_me(list(range(120)), lambda v: v, "image", log=nhat_ky)

    assert ket == list(range(120)), "khong duoc mat viec"
    lo = _lo_da_ban(nhat_ky)
    assert lo and min(lo) > 1, (
        "co luot chi ban 1 job -> tran da tut ve 1 vi doc hong: {0}".format(lo))
    assert any("GIU tr" in m for _lv, m in nhat_ky.dong), "khong bao la dang giu tran cu"


def test_chua_tung_doc_duoc_lan_nao_thi_MO_VUA_PHAI_chu_khong_ve_1(nhat_ky, sc, monkeypatch):
    """Một cú mạng chập không phải bằng chứng nhà máy chỉ còn một chỗ.

    ═══ CÁI GIÁ CỦA VIỆC LÙI VỀ 1 ═══

    Phép đo 10 phút ngày 15/08/2026: lời hỏi `/v1/me` ĐẦU TIÊN mất 121 giây rồi
    hỏng (hạn mức 1.000 request/phút đang bão hoà vì tải của chính mình). Vòng
    dò khởi động ở 1 job, AIMD +1 mỗi lô bò lên 1 → 3 → 8 → 16 → 45. Cả mẻ chạy
    ở nhịp bò: **88 ảnh trong 657 giây**, trong khi ngay lúc đó nhà máy đang
    nhận 61 job đồng thời của chính mình.

    Mở vừa phải rồi để `429`/`503` nói — chúng đến từ lượt gửi THẬT nên đáng tin
    hơn hẳn một lời hỏi trạng thái không tới nơi.
    """
    def _no(loai, api_key=None, mac_dinh=1, client=None):
        raise OSError("mang chap")

    monkeypatch.setattr(sc, "tran_song_song", _no)
    monkeypatch.setattr(sc, "phan_luong_cua_toi", lambda *a, **k: 10 ** 6)
    ket = sb.chay_ca_me(list(range(3)), lambda v: v, "image", log=nhat_ky)
    assert ket == [0, 1, 2]
    assert sb.TRAN_KHOI_DONG_MU >= 16, "mo qua hep = bo len tu day, mat ca me"
    assert sb.so_luong_song_song("image", log=nhat_ky) == sb.TRAN_KHOI_DONG_MU


def test_mu_tit_van_KHONG_vuot_suat_luong_cua_may(nhat_ky, sc, monkeypatch):
    """Không đọc được gì cũng không được mở rộng hơn máy này chịu nổi."""
    def _no(loai, api_key=None, mac_dinh=1, client=None):
        raise OSError("mang chap")

    monkeypatch.setattr(sc, "tran_song_song", _no)
    monkeypatch.setattr(sc, "phan_luong_cua_toi", lambda *a, **k: 5)
    assert sb.so_luong_song_song("image", log=nhat_ky) == 5


def test_nha_may_dung_KHONG_bi_nho_lai_thanh_tran(tran_gia, nhat_ky, sc):
    """`0` = nhà máy đang dừng. Nhớ số 0 thì lượt sau tưởng nó vẫn chết.

    Phải tiêm đồng hồ giả vào CẢ `NhipDo` LẪN `CongHangCho`: `me_nhanh` chỉ vặn
    được đồng hồ của cái sau, nên quãng dừng 30 giây của vòng dò vẫn trôi thật
    và bài kiểm ngồi chờ nửa phút rồi vẫn đỏ.
    """
    sc.bootstrap_sdk()
    from shopapi._nhip_do import NhipDo

    dong_ho = {"t": 1000.0}

    def _ngu(giay):
        dong_ho["t"] += float(giay)

    tran_gia(0, 0, 32)   # dừng hai lượt rồi sống lại
    ket = sb.chay_ca_me(
        list(range(20)), lambda v: v, "image", log=nhat_ky,
        nhip=NhipDo(bat_dau=32, _dong_ho=lambda: dong_ho["t"]),
        cong=sb.CongHangCho(_dong_ho=lambda: dong_ho["t"]),
        ngu=_ngu,
    )
    assert ket == list(range(20)), "nha may song lai ma me van bo viec"
    assert max(_dang_bay(nhat_ky)) > 1, "song lai roi ma van bo tung job mot"


def test_het_luong_thi_TRA_VIEC_VE_HANG_CHO_chu_khong_lam_mat(tran_gia, nhat_ky, monkeypatch, me_nhanh):
    """`pool.submit` ném vì hệ điều hành hết luồng — việc KHÔNG được biến mất.

    Ở nhịp cũ (12 luồng) chuyện này không bao giờ xảy ra. Ở nhịp mới, một máy
    chạy 8 tiến trình mã × 88 luồng là 704 luồng — đúng vùng Windows bắt đầu từ
    chối. Để lỗi bay thẳng ra thì scene đã bốc khỏi hàng chờ biến mất không dấu
    vết, và người dùng chỉ phát hiện khi xem lại thấy thiếu cảnh.
    """
    tran_gia(16)
    that_bai = {"con": 2}
    that = sb.ThreadPoolExecutor.submit

    def _submit(self, fn, *a, **k):
        if that_bai["con"] > 0:
            that_bai["con"] -= 1
            raise RuntimeError("can't start new thread")
        return that(self, fn, *a, **k)

    monkeypatch.setattr(sb.ThreadPoolExecutor, "submit", _submit)

    ket = sb.chay_ca_me(list(range(24)), lambda v: v, "image", log=nhat_ky, **me_nhanh)

    assert ket == list(range(24)), "mat viec khi het luong"
    assert any("khong mo them duoc luong" in m for _lv, m in nhat_ky.dong)


# ── Trần theo SẢN LƯỢNG ĐO ĐƯỢC ──────────────────────────────────────────────
#
# Kiểu nghẽn mà `NhipDo` và `CongHangCho` đều mù: máy chủ NHẬN HẾT rồi chạy
# chậm. Không `429`, không `503`, `queued = 0`. Đo 15/08/2026: `/v1/me` khai
# `capacity 1088` cho nhà máy ảnh trong khi `workers_online = 1`; bắn 60 ảnh ra
# 8,5 ảnh/phút và 2 job chết vì quá hạn 300 giây, dù một ảnh chạy một mình chỉ
# mất 30 giây. Thứ duy nhất nhìn thấy nó là số job xong mỗi phút.


class _DongHo:
    def __init__(self): self.t = 0.0
    def __call__(self): return self.t
    def tien(self, giay): self.t += giay


def _vong(hq, dh, dang_bay, xong, giay):
    """Một cửa sổ: giữ `dang_bay` job cùng lúc, `xong` job hoàn thành."""
    for _ in range(xong):
        hq.ghi_xong()
    hq.nhip_tich(dang_bay)
    dh.tien(giay)
    hq.nhip_tich(dang_bay)
    return hq.chot_so()


def test_san_luong_con_len_thi_KHONG_chan():
    """Còn leo được thì cứ leo — đây là 'khai thác tối đa'."""
    dh = _DongHo(); hq = sb.DoHieuQua(cua_so=50.0, dong_ho=dh)
    assert _vong(hq, dh, 20, 40, 60.0)[0] > 0
    assert hq.cho_phep() == 0
    _vong(hq, dh, 40, 90, 60.0)          # đông gấp đôi, ra nhiều hơn
    assert hq.cho_phep() == 0, "dang len ma da chan"


def test_MOT_luot_xau_CHUA_duoc_ha_tran():
    """Vừa nâng số job lên thì chúng còn đang bay, chưa cái nào kịp xong.

    ═══ BÀI NÀY DỰNG LẠI ĐÚNG LỖI ĐÃ XẢY RA ═══

    Phép đo 10 phút ngày 15/08/2026, cửa sổ 45 giây trong khi một job ảnh mất
    30–300 giây. Vòng dò vừa nâng lên 45 job cùng lúc, cửa sổ kế đọc ra "6
    job/phút", so với kỷ lục "28 job/phút ở 8 job" rồi kết luận đã quá đỉnh và
    **khoá trần xuống 8**. Cả phần còn lại của phép đo chạy ở 8 job. Bộ leo đồi
    tự bóp mình bằng chính cái đáng lẽ để nới ra — 88 ảnh trong 657 giây.

    Một cửa sổ tệ là ống đang đầy. Hai cửa sổ tệ liên tiếp mới là nghẽn thật.
    """
    dh = _DongHo(); hq = sb.DoHieuQua(cua_so=50.0, dong_ho=dh)
    _vong(hq, dh, 8, 24, 60.0)                   # ky luc: 24 job/phut o 8
    _vong(hq, dh, 45, 6, 60.0)                   # vua nang len, ong dang day
    assert hq.cho_phep() == 0, "ha tran ngay o luot xau DAU TIEN — chinh la loi cu"


def test_HAI_luot_xau_lien_tiep_thi_moi_LUI():
    """Lặp lại thì không còn là ống đầy nữa — đó là nhà máy nghẽn thật."""
    dh = _DongHo(); hq = sb.DoHieuQua(cua_so=50.0, dong_ho=dh)
    _vong(hq, dh, 40, 100, 60.0)
    _vong(hq, dh, 120, 20, 60.0)
    _vong(hq, dh, 120, 18, 60.0)
    assert hq.cho_phep() == 40, (
        "khong lui ve muc tot nhat -> tiep tuc nhoi vao mot nha may dang nghen")


def test_mot_luot_xau_roi_TOT_lai_thi_quen_di():
    """Đếm lượt tệ phải reset, nếu không hai cú nhiễu cách xa nhau cũng cộng dồn."""
    dh = _DongHo(); hq = sb.DoHieuQua(cua_so=50.0, dong_ho=dh)
    _vong(hq, dh, 40, 100, 60.0)
    _vong(hq, dh, 120, 20, 60.0)     # xau lan 1
    _vong(hq, dh, 120, 95, 60.0)     # tot tro lai -> quen
    _vong(hq, dh, 120, 20, 60.0)     # xau lan 1 (khong phai lan 2)
    assert hq.cho_phep() == 0


def test_nha_may_khoe_lai_thi_MO_TRAN_ra_ngay():
    """Lùi không được phép là lùi vĩnh viễn — thợ về đông là phải ăn theo ngay."""
    dh = _DongHo(); hq = sb.DoHieuQua(cua_so=50.0, dong_ho=dh)
    _vong(hq, dh, 40, 100, 60.0)
    _vong(hq, dh, 120, 20, 60.0)
    _vong(hq, dh, 120, 18, 60.0)
    assert hq.cho_phep() == 40
    _vong(hq, dh, 40, 300, 60.0)                 # nha may khoe han len
    assert hq.cho_phep() == 0, "san luong pha ky luc ma van con giu tran cu"


def test_ky_luc_PHAI_DAN_chu_khong_ghim_mai():
    """Một phút vàng hồi nào đó không được làm mốc so sánh vĩnh viễn."""
    dh = _DongHo(); hq = sb.DoHieuQua(cua_so=50.0, dong_ho=dh)
    _vong(hq, dh, 40, 500, 60.0)                 # ky luc rat cao
    cao = hq._tot_nhat[0]
    for _ in range(12):
        _vong(hq, dh, 40, 0, 60.0)
    assert hq._tot_nhat[0] < cao * 0.5, "ky luc khong phai -> khong bao gio pha duoc nua"


def test_cua_so_phai_DAI_HON_tuoi_tho_mot_job():
    """45 giây là quá ngắn cho job 30–300 giây — đó là gốc của lỗi 15/08/2026."""
    assert sb.DoHieuQua.CUA_SO >= 120, (
        "cua so ngan hon tuoi tho job = do nhieu khoi dong thanh 'da qua dinh'")
    assert sb.DoHieuQua.XAU_LIEN_TIEP >= 2, "mot luot xau la nhieu, khong phai nghen"


def test_khong_bao_gio_ha_xuong_duoi_SAN():
    """Hạ tới 0 là tool đứng im — luôn còn đủ chỗ để đo lại lần sau."""
    dh = _DongHo(); hq = sb.DoHieuQua(cua_so=50.0, dong_ho=dh)
    _vong(hq, dh, 1, 60, 60.0)
    _vong(hq, dh, 200, 1, 60.0)
    _vong(hq, dh, 200, 1, 60.0)
    assert hq.cho_phep() >= sb.DoHieuQua.SAN


def test_chua_du_mot_cua_so_thi_CHUA_ket_luan_gi():
    """Chốt sổ sớm là đọc nhiễu thành xu hướng."""
    dh = _DongHo(); hq = sb.DoHieuQua(cua_so=45.0, dong_ho=dh)
    hq.ghi_xong(5); hq.nhip_tich(10); dh.tien(10.0)
    assert hq.chot_so() is None
    assert hq.cho_phep() == 0


def test_vong_chay_that_CO_dung_tran_san_luong():
    """Viết lớp mà quên cắm vào vòng chạy thì nó chỉ là mã chết."""
    import inspect
    than = inspect.getsource(sb.chay_ca_me)
    assert "DoHieuQua()" in than, "chua dung DoHieuQua trong chay_ca_me"
    assert "hieu_qua.ghi_xong()" in than, "khong ghi nhan job xong -> khong do duoc gi"
    assert "hieu_qua.cho_phep()" in than, "do xong ma khong dung ket qua de chan"


# ── Nhịp GỬI: khác hẳn số job SONG SONG ──────────────────────────────────────
#
# Trần máy chủ (`limit`) nói bao nhiêu job được CHẠY cùng lúc. Hạn mức
# `requests_per_minute` nói bao nhiêu lời gọi được GỬI mỗi phút. Mở đủ 979 chỗ
# chạy KHÔNG có nghĩa là được tạo 979 job trong một nhịp thở.
#
# Đo 15/08/2026, ngay sau khi sửa cho vòng dò khởi động đúng ở 979: mẻ bắn một
# loạt, ăn 2.651 lần `429`, 71 job chết vì quá hạn, và ra ĐÚNG 0 ẢNH. Trần song
# song thì đúng, cách tiêu nó thì sai.


def test_thung_gui_cho_BUNG_mot_loat_roi_moi_rot_deu():
    """Mẻ cỡ thường phải đi thẳng; quá mức bùng thì mới nhỏ giọt."""
    dh = _DongHo()
    th = sb.ThungGui(so_ban=1, ngan_sach=1800.0, dong_ho=dh)   # 10 job/giay
    assert th.xin(1000) == sb.TRAN_BUNG, "lo dau phai duoc bung tron muc cho phep"
    assert th.xin(1000) == 0, "bung xong ma van cho gui tiep = khong ghim gi ca"
    dh.tien(3.0)
    assert th.xin(1000) == 30, "rot deu sai nhip"


def test_thung_gui_KHONG_cho_don_qua_muc_bung():
    """Ngồi im mười phút rồi bắn một phát 6.000 job là đúng cái đã gây thảm hoạ."""
    dh = _DongHo()
    th = sb.ThungGui(so_ban=1, ngan_sach=1800.0, dong_ho=dh)
    th.xin(sb.TRAN_BUNG)
    dh.tien(600.0)
    assert th.xin(10 ** 6) == sb.TRAN_BUNG


def test_thung_gui_CHIA_cho_so_tien_trinh_dang_song():
    """Tám mã cùng chạy thì hạn mức request cũng phải chia tám."""
    dh = _DongHo()
    mot = sb.ThungGui(so_ban=1, ngan_sach=1800.0, dong_ho=dh)
    tam = sb.ThungGui(so_ban=8, ngan_sach=1800.0, dong_ho=dh)
    assert abs(mot.toc_do() - 8 * tam.toc_do()) < 1e-9


def test_thung_gui_luon_con_nhich_duoc():
    """Chia cho bao nhiêu tiến trình cũng không được về 0 — về 0 là đứng hẳn."""
    th = sb.ThungGui(so_ban=10 ** 6, ngan_sach=1.0)
    assert th.toc_do() > 0


def test_vong_chay_CO_ghim_nhip_gui():
    """Viết thùng mà quên cắm vào vòng chạy thì nó chỉ là mã chết."""
    import inspect
    than = inspect.getsource(sb.chay_ca_me)
    assert "ThungGui()" in than
    assert "thung.xin(" in than, "khong xin token truoc khi gui"
    assert "thung.cho_bao_lau()" in than, "het token ma khong nghi -> vong quay tit"


def test_job_HONG_khong_duoc_tinh_la_san_luong():
    """Bộ leo đồi đếm job hỏng là hàng ra thì nó leo lên đúng chỗ chết.

    Đo 15/08/2026: nhật ký báo "262 job/phút ở 715 job cùng lúc" trong khi số
    ảnh THẬT ra được là 0 — toàn bộ là `429`. Bộ leo đồi tưởng đang ở đỉnh phong
    độ và cứ thế nhồi thêm.
    """
    import inspect
    than = inspect.getsource(sb.chay_ca_me)
    assert 'if trang_thai == "xong":' in than, \
        "khong phan biet xong that voi hong -> dem ca job hong la san luong"
    boc = inspect.getsource(sb._boc)
    assert '"hong", gia_tri_khi_hong' in boc, "_boc van gop job hong vao nhanh 'xong'"


def test_viec_hong_VAN_giu_dung_cho_trong_ket_qua():
    """Tách 'hỏng' khỏi 'xong' không được làm lệch thứ tự kết quả."""
    def _chay(v):
        if v % 3 == 0:
            raise ValueError("hong that")
        return v * 10

    ket = sb.chay_ca_me(list(range(9)), _chay, "image", log=lambda *a, **k: None,
                        gia_tri_khi_hong=None, tu_dieu_tiet=False, tran_tool=4)
    assert ket == [None, 10, 20, None, 40, 50, None, 70, 80]
