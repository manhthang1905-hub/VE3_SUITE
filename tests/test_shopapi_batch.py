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
    """Đọc kích thước từng lô từ log — cách duy nhất soi nhịp mà không phá đóng gói."""
    ra = []
    for _lv, m in nhat_ky.dong:
        if "job CUNG LUC" in m:
            ra.append(int(m.split("-> ban ")[1].split(" job")[0]))
    return ra


# ── Số luồng: lấy từ máy chủ, KHÔNG gõ cứng ──────────────────────────────────


def test_so_luong_lay_dung_con_so_may_chu_tra_ve(tran_gia, nhat_ky):
    tran_gia(9)
    assert sb.so_luong_song_song("image", log=nhat_ky) == 9


def test_so_luong_bi_chan_tren_boi_tran_nguoi_dung_dat(tran_gia, nhat_ky):
    """Máy chủ rộng bao nhiêu cũng không được phá giới hạn người dùng đã đặt."""
    tran_gia(50)
    assert sb.so_luong_song_song("image", tran_tool=4, log=nhat_ky) == 4


def test_so_luong_khong_bao_gio_vuot_tran_CUNG_cua_loai_job(tran_gia, nhat_ky, sc):
    """Một lời `/v1/me` trả số vô lý cũng không biến thành 9999 luồng."""
    tran_gia(9999)
    assert sb.so_luong_song_song("image", log=nhat_ky) == sc.tran_cung("image") == 128
    tran_gia(9999)
    assert sb.so_luong_song_song("video", log=nhat_ky) == sc.tran_cung("video") == 64


def test_hoi_khong_duoc_thi_doan_THAP_chu_khong_dung_im(monkeypatch, sc, nhat_ky):
    def _hong(*a, **kw):
        raise RuntimeError("mat mang")

    monkeypatch.setattr(sc, "tran_song_song", _hong)
    assert sb.so_luong_song_song("image", log=nhat_ky) == 1


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
    """Trần máy chủ là mức KHÔNG ĐƯỢC VƯỢT — kể cả khi vòng dò đang muốn tăng."""
    tran_gia(3)

    ket = sb.chay_ca_me(list(range(30)), lambda v: v, "image", log=nhat_ky)

    assert ket == list(range(30))
    assert max(_lo_da_ban(nhat_ky)) <= 3


def test_tran_nguoi_dung_dat_van_thang_khi_may_chu_rong(tran_gia, nhat_ky):
    tran_gia(64)
    sb.chay_ca_me(list(range(30)), lambda v: v, "image", tran_tool=2, log=nhat_ky)
    assert max(_lo_da_ban(nhat_ky)) <= 2


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
