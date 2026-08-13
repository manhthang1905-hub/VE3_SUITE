"""Cổng hàng chờ — **đóng đinh sự cố 66 job ngày 07/08/2026**.

═══════════════════════════════════════════════════════════════════════════════
 SỐ ĐO THẬT, TRÊN MÁY CHỦ THẬT — ĐỌC TRƯỚC KHI SỬA HAY GỠ BÀI NÀO
═══════════════════════════════════════════════════════════════════════════════

Ngày 07/08/2026, tool anh em (`dola-seedance-api`) bắn **66 job video một lúc**
vào `api.shopapi.vn`. Nhà máy chỉ tiêu hoá được **~16 job song song**. Trong 20
phút:

* **33 job nằm xếp hàng cho tới lúc hết hạn — chưa chạy một giây nào**;
* **14 job "vượt quá thời gian chờ" + 13 job "không còn đủ thời gian để thử
  lại"** = **27 job hỏng**;
* **kho tài khoản KHÔNG hề cạn.** Không tài nguyên nào thiếu, không job nào
  chạy lỗi. Thuần tuý là bắn quá tay vào một cái hàng đã dài.

VE3_SUITE gọi cùng một máy chủ, cùng một cách, nên nó dính đúng bẫy đó.

VÌ SAO `NhipDo` (AIMD) KHÔNG BẮT ĐƯỢC CA NÀY — điểm quan trọng nhất cả file:
`NhipDo` là vòng dò **theo sau**. Nó chỉ biết có nghẽn khi (a) một job xong êm
và báo về thời gian nằm hàng chờ, hoặc (b) đã ăn `429`. Hôm đó **cả hai đều
không bao giờ tới**: job chết trong hàng nên chẳng cái nào "xong êm" để báo, và
máy chủ cũng không trả `429` — nó nhận job bình thường rồi để chúng chết già.

`CongHangCho` là tín hiệu **đi trước**: nó đọc `queue_position` /
`estimated_seconds` mà máy chủ đã trả sẵn ngay trong phản hồi `202` lúc nhận
job, nên thấy hàng dài **trước khi** có job nào kịp chết.

Không bài nào chạm mạng thật hay tiêu tiền thật: đồng hồ giả, client giả.
"""

from __future__ import annotations

import threading

import pytest

import shopapi_batch as sb
import shopapi_common as _sc
import shopapi_image_client as sic
import shopapi_video_client as svc
from conftest import job_anh, job_video
# Dùng lại nguyên bộ đồ giả của bộ bài chạy-cả-mẻ. Chép một bộ thứ hai là hai bộ
# trôi khác nhau rồi cùng xanh nhưng đo hai thứ khác nhau.
from test_shopapi_batch import (  # noqa: F401 — đều là fixture
    me_nhanh,
    ngu_gia,
    tran_gia,
)


# ═══════════════════════════════════════════════════════════════════════════════
#  Đồ giả
# ═══════════════════════════════════════════════════════════════════════════════


class DongHoGia:
    """Đồng hồ tự tay vặn — dựng lại 20 phút trong vài micro-giây."""

    def __init__(self, bat_dau=1000.0):
        self.luc = float(bat_dau)

    def __call__(self):
        return self.luc

    def tien(self, giay):
        self.luc += float(giay)


def cong(tran=16, han_giay=1200.0, **kw):
    """Cổng dùng đồng hồ giả. Trả `(cổng, đồng_hồ)`.

    Mặc định lấy đúng số của sự cố: trần 16 (sức tiêu hoá thật) và hạn chờ 1.200
    giây cho một job.
    """
    dh = DongHoGia()
    c = sb.CongHangCho(tran=tran, han_giay=han_giay, _dong_ho=dh, **kw)
    return c, dh


class FakeJobs:
    """`client.jobs.wait` — nhánh MỚI (`create` + `wait`) đi qua đây."""

    def __init__(self, so, job):
        self._so = so
        self._job = job

    def wait(self, job_id, timeout=None, on_progress=None, estimated_seconds=None,
             **kw):
        self._so.waits.append({"job_id": job_id, "timeout": timeout,
                               "estimated_seconds": estimated_seconds})
        if on_progress is not None:
            on_progress({"status": "running", "progress": 50})
        return self._job


class FakeTaiNguyen:
    """`client.images` / `client.videos` có ĐỦ `create` — khuôn máy chủ thật."""

    def __init__(self, so, ten, job_202, loi=None):
        self._so = so
        self._ten = ten
        self._job_202 = job_202
        self._loi = loi

    def create(self, **kwargs):
        getattr(self._so, self._ten).append(kwargs)
        if self._loi is not None:
            raise self._loi
        return dict(self._job_202)


class GhiNhanMoi:
    def __init__(self):
        self.image_calls = []
        self.video_calls = []
        self.uploads = []
        self.waits = []


class ClientDayDu:
    """Client giả có `create` + `jobs.wait` — khuôn ĐÚNG của SDK thật.

    `conftest.FakeClient` cố ý chỉ có `create_and_wait` (khuôn của SDK cũ), nên
    nó chạy nhánh LÙI của `tao_va_cho`. Lớp này chạy nhánh MỚI — chỗ duy nhất
    đọc được `queue_position`.
    """

    def __init__(self, *, job_xong, vi_tri=None, uoc_giay=None, loi=None,
                 job_id="job_202"):
        self.so = GhiNhanMoi()
        job_202 = {"id": job_id, "status": "queued"}
        if vi_tri is not None:
            job_202["queue_position"] = vi_tri
        if uoc_giay is not None:
            job_202["estimated_seconds"] = uoc_giay
        self.images = FakeTaiNguyen(self.so, "image_calls", job_202, loi)
        self.videos = FakeTaiNguyen(self.so, "video_calls", job_202, loi)
        self.jobs = FakeJobs(self.so, job_xong)
        self.uploads = _UploadGia(self.so)


class _UploadGia:
    def __init__(self, so):
        self._so = so

    def upload_file(self, file, filename=None, content_type=None):
        self._so.uploads.append({"file": file, "filename": filename})
        return "https://cdn.example.invalid/up/{0}".format(len(self._so.uploads))


def _loi_gia(ten_lop, thong_diep, **thuoc_tinh):
    lop = type(ten_lop, (Exception,), {})
    exc = lop(thong_diep)
    exc.message = thong_diep
    for k, v in thuoc_tinh.items():
        setattr(exc, k, v)
    return exc


# ═══════════════════════════════════════════════════════════════════════════════
#  1. BÀI QUAN TRỌNG NHẤT — hàng chờ dài thì NGỪNG GỬI
# ═══════════════════════════════════════════════════════════════════════════════


def test_hang_cho_dai_thi_NGUNG_gui_them():
    """`queue_position` vượt trần → cổng đóng, dù còn thừa chỗ "đang bay".

    Tái hiện trực tiếp 33 job "chưa chạy một giây nào". Trần 16 nghĩa là đứng
    thứ 20 thì phải chờ trọn hơn một lượt dựng của cả xưởng mới tới lượt mình.
    """
    c, _ = cong(tran=16)
    assert c.giu_cho(1) == 1

    c.ghi_nhan_tao(vi_tri=20, uoc_giay=30.0)

    assert c.giu_cho(1) == 0, (
        "hang dang dai 20 ma cong van cho gui = dung loi da mat 27 job hom 07/08"
    )
    assert "HANG CHO DANG DAI" in c.ly_do()
    assert "20" in c.ly_do(), "ly do phai noi ra con so, khong noi chung chung"


def test_hang_cho_NGAN_thi_van_gui_binh_thuong():
    """Chặn nhầm là bóp chết công suất — đừng sửa một cực đoan bằng cực đoan kia."""
    c, _ = cong(tran=16)
    c.giu_cho(1)
    c.ghi_nhan_tao(vi_tri=3, uoc_giay=45.0)   # đúng ví dụ CONTRACT.md §2.1
    assert c.giu_cho(1) == 1
    assert c.ly_do() == ""


def test_uoc_luong_cho_VUOT_HAN_thi_ngung_gui():
    """`estimated_seconds` vượt hạn chờ → gửi thêm là gửi job đi chết.

    Đây là 14 job "vượt quá thời gian chờ": máy chủ nói thẳng "còn 1.500 giây
    nữa mới tới lượt" trong khi tool chỉ chờ được 1.200.
    """
    c, _ = cong(tran=16, han_giay=1200.0)
    c.giu_cho(1)
    c.ghi_nhan_tao(vi_tri=1, uoc_giay=1500.0)
    assert c.giu_cho(1) == 0
    assert "chet trong hang" in c.ly_do()


def test_bien_an_toan_chan_ca_uoc_luong_SAT_HAN():
    """Ước lượng của máy chủ là LẠC QUAN — còn dưới 20% biên thì coi như đã hết."""
    c, _ = cong(tran=16, han_giay=1200.0)
    c.giu_cho(1)
    c.ghi_nhan_tao(uoc_giay=1100.0)
    assert c.giu_cho(1) == 0, "1100 x 1,2 = 1320 > 1200 -> phai chan"

    c2, _ = cong(tran=16, han_giay=1200.0)
    c2.giu_cho(1)
    c2.ghi_nhan_tao(uoc_giay=500.0)
    assert c2.giu_cho(1) == 1, "500 x 1,2 = 600 < 1200 -> con rong, dung chan nham"


def test_tran_dang_bay_KHONG_BAO_GIO_len_toi_66():
    """66 ÷ 16 ≈ 4,1 — đúng hệ số của ngày 07/08. Khoá hệ số 1,5 lại."""
    c, _ = cong(tran=16)
    assert c.tran_dang_bay() == 24
    assert c.giu_cho(66) == 24, "xin o at 66 cho (dung canh da xay ra) -> chi duoc 24"
    assert c.giu_cho(1) == 0
    assert c.dang_bay == 24


def test_giu_cho_va_kiem_tra_nam_trong_CUNG_MOT_KHOA():
    """Tách "hỏi còn chỗ" khỏi "giữ chỗ" là mọi luồng cùng thấy còn rồi cùng gửi."""
    c, _ = cong(tran=4)          # trần đang bay = 6
    duoc = []
    bat_dau = threading.Barrier(20)

    def _xin():
        bat_dau.wait()
        duoc.append(c.giu_cho(1))

    luong = [threading.Thread(target=_xin) for _ in range(20)]
    for t in luong:
        t.start()
    for t in luong:
        t.join()

    assert sum(duoc) == 6, "20 luong tranh 6 cho phai ra dung 6"


# ═══════════════════════════════════════════════════════════════════════════════
#  2. `queue_position` TĂNG DẦN → nhịp gửi GIẢM DẦN
# ═══════════════════════════════════════════════════════════════════════════════


def test_vi_tri_tang_dan_thi_nhip_gui_GIAM_DAN():
    """Cổng phải phản ứng THEO MỨC ĐỘ, không phải bật/tắt."""
    nhip = []
    for vi_tri in (1, 4, 9):
        c, _ = cong(tran=8)      # trần đang bay = 12, ngưỡng vị trí = 8
        c.giu_cho(3)
        c.ghi_nhan_tao(vi_tri=vi_tri)
        nhip.append(c.giu_cho(99))

    assert nhip[0] > 0 and nhip[1] > 0
    assert nhip[2] == 0, "hang vuot nguong thi dung han"
    assert nhip == sorted(nhip, reverse=True), (
        "vi tri tang ma nhip gui khong giam don dieu: {0}".format(nhip))


def test_dang_bay_cang_nhieu_thi_cho_gui_cang_it():
    duoc = []
    for da_bay in (0, 5, 10, 15):
        c, _ = cong(tran=10)     # trần đang bay = 15
        for _ in range(da_bay):
            c.giu_cho(1)
        duoc.append(c.giu_cho(99))
    assert duoc == [15, 10, 5, 0], duoc


def test_tra_cho_RUT_NGAN_hang_cho_dang_biet():
    """Job của ta ra khỏi hàng thì người phía sau nhích lên — cổng phải biết.

    Không có luật này thì một `queue_position` cũ khoá cổng vĩnh viễn: số mới
    chỉ có khi gửi job mới, mà job mới đang bị chính nó chặn.
    """
    c, _ = cong(tran=4)
    c.giu_cho(2)
    c.ghi_nhan_tao(vi_tri=6)
    assert c.giu_cho(1) == 0

    c.tra_cho(2)
    assert c.vi_tri_hang_cho == 4
    assert c.giu_cho(1) >= 1, "hang da ngan lai thi phai mo ra, dung ket vinh vien"


# ═══════════════════════════════════════════════════════════════════════════════
#  3. `429` / `resource_exhausted` → LÙI NHỊP, không gửi lại ngay
# ═══════════════════════════════════════════════════════════════════════════════


def test_429_thi_KHONG_gui_lai_ngay():
    c, dh = cong(tran=16)
    assert c.giu_cho(1) == 1

    c.bi_nghen(None)

    assert c.giu_cho(1) == 0, "429 xong gui lai NGAY la chua lui nhip gi ca"
    assert c.cho_bao_lau() == pytest.approx(sb.LUI_NHIP_GIAY)
    assert "KHONG gui lai ngay" in c.ly_do()

    dh.tien(sb.LUI_NHIP_GIAY + 0.1)
    assert c.giu_cho(1) == 1, "het quang lui thi phai chay tiep, khong duoc cam luon"


def test_429_ton_trong_Retry_After_cua_may_chu():
    c, dh = cong(tran=16)
    c.bi_nghen(90.0)
    assert c.cho_bao_lau() == pytest.approx(90.0)
    dh.tien(60.0)
    assert c.giu_cho(1) == 0, "moi cho 60/90 giay da gui la chua ton trong Retry-After"
    dh.tien(31.0)
    assert c.giu_cho(1) == 1


def test_lui_nhip_ap_cho_CA_ME_chu_khong_rieng_luong_bi_chan():
    """Một luồng ăn `429` mà các luồng kia vẫn bắn tiếp thì cú lùi đó vô nghĩa."""
    c, _ = cong(tran=16)
    c.giu_cho(5)
    c.bi_nghen(None)
    assert c.giu_cho(10) == 0


def test_resource_exhausted_la_NGHEN_chu_khong_phai_viec_hong():
    """`JobFailedError(code="resource_exhausted")` = nhà máy hết chỗ.

    Nó về tới tool trông y hệt một job hỏng thường (cùng lớp với "prompt bị
    chặn"), nên rất dễ bị ghi là "cảnh hỏng" rồi bắn tiếp cái sau — tức là đạp
    ga đúng lúc máy chủ vừa nói hết chỗ. Job kiểu này **đã hoàn 100% tiền** nên
    trả việc về đầu hàng chờ mới là đúng cả về tiền lẫn về nhịp.
    """
    het_cho = _loi_gia("JobFailedError", "het may ranh", code="resource_exhausted")
    nghen = _sc.phan_loai_nghen(het_cho)
    assert nghen is not None, "resource_exhausted bi dem la 'viec hong' -> tool dap ga"
    assert nghen.ma == 429, "nha may van song, chi la dang chat -> 429 chu khong phai 503"

    hong_that = _loi_gia("JobFailedError", "noi dung bi chan", code="content_rejected")
    assert _sc.phan_loai_nghen(hong_that) is None, (
        "prompt bi chan la loi cua DONG VIEC, lui nhip cho no la bop tool vo co")


def test_resource_exhausted_TRA_VIEC_VE_HANG_CHO_chu_khong_dem_that_bai(
        tran_gia, nhat_ky, me_nhanh):
    """Chạy thật qua `chay_ca_me`: việc dính `resource_exhausted` phải chạy lại."""
    tran_gia(8)
    so_lan = {}

    def _chay(v):
        so_lan[v] = so_lan.get(v, 0) + 1
        if v == 2 and so_lan[v] == 1:
            raise _loi_gia("JobFailedError", "het may ranh", code="resource_exhausted")
        return v

    ket = sb.chay_ca_me(list(range(6)), _chay, "image", log=nhat_ky, **me_nhanh)

    assert ket == list(range(6)), "KHONG duoc mat viec nao"
    assert so_lan[2] == 2, "viec dinh resource_exhausted phai duoc CHAY LAI"
    assert any("TRA VE DAU HANG CHO" in m for _lv, m in nhat_ky.dong)


# ═══════════════════════════════════════════════════════════════════════════════
#  4. `concurrent_jobs = 0` → chờ rồi hỏi lại, KHÔNG treo vô hạn
# ═══════════════════════════════════════════════════════════════════════════════


def test_tran_0_thi_dong_cong_va_CHO():
    c, _ = cong(tran=16)
    c.dat_tran(0)
    assert c.giu_cho(1) == 0
    assert "DANG DUNG" in c.ly_do()
    assert c.cho_bao_lau() > 0


def test_tran_0_roi_mo_lai_thi_chay_tiep_chu_khong_treo():
    c, dh = cong(tran=16)
    c.dat_tran(0)
    dh.tien(sb.CHO_KHI_DUNG + 0.1)
    assert c.giu_cho(1) == 0, "tran van 0 thi van dong"
    c.dat_tran(16)
    assert c.giu_cho(1) == 1, "may chu mo lai ma cong van cam thi do la treo"


def test_troi_TRONG_thi_luon_cho_qua_MOT_job_tham_do():
    """Chốt chống treo: không job nào bay thì không bao giờ có số hàng chờ mới."""
    c, _ = cong(tran=16)
    c.giu_cho(1)
    c.ghi_nhan_tao(vi_tri=999, uoc_giay=99999.0)
    assert c.giu_cho(1) == 0

    c.tra_cho(1)
    assert c.dang_bay == 0
    assert c.giu_cho(1) == 1, "troi trong ma van cam = treo vinh vien"


def test_tham_do_van_TON_TRONG_quang_lui_nhip():
    """Chốt chống treo KHÔNG được phá luật "429 thì đừng gửi lại ngay"."""
    c, dh = cong(tran=16)
    c.bi_nghen(None)
    assert c.dang_bay == 0
    assert c.giu_cho(1) == 0
    dh.tien(sb.LUI_NHIP_GIAY + 0.1)
    assert c.giu_cho(1) == 1


def test_me_bi_cong_chan_van_THOAT_RA_chu_khong_treo(tran_gia, nhat_ky, ngu_gia):
    """Hàng dài mãi thì bỏ số việc còn lại **có kiểm soát**, và nói rõ là không mất.

    Treo vô hạn là kiểu hỏng tệ nhất vì không ai biết nó đang hỏng. Việc bỏ lại
    vẫn nguyên trong Excel (status ≠ done) nên lượt chạy sau nhặt lại được.
    """
    tran_gia(4)
    c, _ = cong(tran=4)
    c.giu_cho(6)                 # lấp kín trần đang bay, không bao giờ nhả

    ket = sb.chay_ca_me(list(range(5)), lambda v: v, "image", log=nhat_ky,
                        ngu=ngu_gia, cong=c, cho_toi_da=60.0)

    assert ket == [None] * 5, "viec chua chay phai la None, khong phai gia tri bia"
    assert ngu_gia.lan, "phai co ngu giua cac lan hoi lai, khong duoc quay tit"
    assert any("van dai qua lau" in m for _lv, m in nhat_ky.dong)
    assert any("lan chay sau nhat lai duoc" in m for _lv, m in nhat_ky.dong)


# ═══════════════════════════════════════════════════════════════════════════════
#  5. CHẠY THẬT QUA `chay_ca_me` — tái hiện ca 66 job
# ═══════════════════════════════════════════════════════════════════════════════


def test_66_VIEC_KHONG_CON_BAY_CUNG_LUC_NUA(tran_gia, nhat_ky, me_nhanh):
    """**Bài quan trọng nhất cả file.** 66 việc, nhà máy tiêu hoá 16.

    Trước bản sửa: `nhip` dò lên tới trần 16 rồi cứ thế bắn từng lô 16 — nhưng
    vì mỗi lô CHỜ XONG mới gửi lô sau, đỉnh đang-bay của VE3 vốn đã bằng cỡ lô.
    Chỗ hỏng thật là **lô được phép to bằng trần mà không ai hỏi hàng chờ dài
    bao nhiêu**. Bài này khoá lại: đỉnh số job bay cùng lúc không bao giờ vượt
    trần "đang bay" (16 × 1,5 = 24), và không việc nào mất.
    """
    tran_gia(16)
    dang_bay = {"n": 0, "dinh": 0}
    khoa = threading.Lock()

    def _chay(v):
        with khoa:
            dang_bay["n"] += 1
            dang_bay["dinh"] = max(dang_bay["dinh"], dang_bay["n"])
            xep_hang = max(0, dang_bay["n"] - 16)
        # Máy chủ thật: 16 chạy được ngay, phần dư đứng xếp hàng.
        sb.bao_hang_cho(xep_hang, float(xep_hang) * 95.0)
        with khoa:
            dang_bay["n"] -= 1
        return v

    ket = sb.chay_ca_me(list(range(66)), _chay, "video", log=nhat_ky, **me_nhanh)

    assert dang_bay["dinh"] <= me_nhanh["cong"].tran_dang_bay(), (
        "dinh {0} job bay cung luc, vuot tran {1} -> ca 66 job ngay 07/08/2026 "
        "TAI DIEN".format(dang_bay["dinh"], me_nhanh["cong"].tran_dang_bay()))
    assert dang_bay["dinh"] < 66
    assert ket == list(range(66)), "chan nhip KHONG duoc lam mat viec"


def test_mot_viec_hong_giua_me_KHONG_keo_ca_me_chet(tran_gia, nhat_ky, me_nhanh):
    """Tính chất đã đúng từ trước — cổng mới không được làm hỏng nó.

    Và quan trọng không kém: chạy xong thì cổng phải nhả HẾT chỗ. Rò chỗ ở
    nhánh lỗi là cổng chỉ tăng mà không giảm, tới lúc nào đó khoá cứng cả mẻ mà
    không một dòng nào nói vì sao.
    """
    tran_gia(8)

    def _chay(v):
        if v == 3:
            raise RuntimeError("noi dung bi tu choi")
        return v

    ket = sb.chay_ca_me(list(range(6)), _chay, "image", log=nhat_ky, **me_nhanh)

    assert ket[3] is False
    assert [ket[i] for i in (0, 1, 2, 4, 5)] == [0, 1, 2, 4, 5]
    assert me_nhanh["cong"].dang_bay == 0, (
        "chay xong het ma cong van dem job dang bay = RO CHO, me sau se khoa cung")


def test_cong_nha_HET_cho_du_lo_an_429(tran_gia, nhat_ky, sc, me_nhanh):
    """Trả chỗ phải đúng bằng số đã giữ, kể cả khi cả lô bị từ chối ở cửa."""
    tran_gia(4)
    so_lan = {}

    def _chay(v):
        so_lan[v] = so_lan.get(v, 0) + 1
        if so_lan[v] == 1:
            raise sc.BiNghen(429)
        return v

    sb.chay_ca_me(list(range(6)), _chay, "image", log=nhat_ky, **me_nhanh)
    assert me_nhanh["cong"].dang_bay == 0


# ═══════════════════════════════════════════════════════════════════════════════
#  6. ĐỌC HAI TRƯỜNG, VÀ KHÔNG LÀM HỎNG HỢP ĐỒNG SẴN CÓ
# ═══════════════════════════════════════════════════════════════════════════════


def test_doc_dung_queue_position_va_estimated_seconds():
    """Đúng khuôn `202` của CONTRACT.md §2.1."""
    assert _sc.doc_hang_cho({
        "id": "job_x7k2m9p4qr8s", "status": "queued",
        "estimated_cost": "1000000000", "estimated_seconds": 45, "queue_position": 3,
    }) == (3, 45.0)


def test_thieu_truong_thi_tra_None_chu_khong_doan_0():
    """`None` = "máy chủ không nói", KHÁC HẲN `0` = "hàng rỗng, vào ngay".

    Đoán bừa `0` là mở toang cổng đúng lúc mình mù thông tin nhất.
    """
    assert _sc.doc_hang_cho({"id": "job_cu", "status": "queued"}) == (None, None)
    assert _sc.doc_hang_cho({"queue_position": "ba"}) == (None, None)


def test_may_chu_khong_tra_truong_nao_thi_GIU_NGUYEN_so_dang_biet():
    c, _ = cong(tran=16)
    c.giu_cho(1)
    c.ghi_nhan_tao(vi_tri=30)
    c.ghi_nhan_tao(vi_tri=None, uoc_giay=None)
    assert c.vi_tri_hang_cho == 30
    assert c.giu_cho(1) == 0


def test_video_van_gui_engine_veo3_va_duration_8_qua_duong_MOI(tmp_path, tai_ve_gia,
                                                               nhat_ky):
    """⛔ BẪY SỐ MỘT của VE3 — đổi đường gửi không được đổi cặp engine/duration.

    `tao_va_cho` thay `create_and_wait` bằng `create` + `jobs.wait`. Nếu tham số
    rơi rớt ở khâu chuyển thì mỗi job là một `422 unsupported_parameter` — một
    mã 200 scene là 200 lần lỗi liên tiếp.
    """
    anh = tmp_path / "img" / "SC001.png"
    anh.parent.mkdir(parents=True)
    anh.write_bytes(b"\x89PNG-gia")
    client = ClientDayDu(job_xong=job_video(), vi_tri=3, uoc_giay=45.0)

    ket = svc.generate(str(anh), "canh mua", str(tmp_path / "vid" / "SC001.mp4"),
                       client=client, log=nhat_ky)

    assert len(ket) == 3, "phai khop chu ky (ok, info, err) cua _submit_video"
    assert ket[0] is True, ket[2]
    goi = client.so.video_calls[0]
    assert goi["engine"] == "veo3"
    assert goi["duration"] == 8, "veo3 CHI nhan 8 giay; 10 la 422"
    assert goi["aspect_ratio"] == "16:9"
    assert goi["image_url"].startswith("https://")


def test_duong_MOI_doc_duoc_queue_position_va_bao_cho_cong(tmp_path, tai_ve_gia,
                                                           nhat_ky):
    """Đây là toàn bộ lý do tách `create` khỏi `wait`."""
    anh = tmp_path / "img" / "S.png"
    anh.parent.mkdir(parents=True)
    anh.write_bytes(b"\x89PNG")
    client = ClientDayDu(job_xong=job_video(), vi_tri=7, uoc_giay=660.0)

    c, _ = cong(tran=16)
    sb._cuc_bo.cong = c
    try:
        svc.generate(str(anh), "x", str(tmp_path / "a.mp4"), client=client,
                     log=nhat_ky)
    finally:
        sb._cuc_bo.cong = None

    assert c.vi_tri_hang_cho == 7, "queue_position khong toi duoc cong"
    assert any("dung thu 7 trong hang" in m for _lv, m in nhat_ky.dong), (
        "nguoi dung phai THAY duoc la dang xep hang, khong phai tool treo")


def test_anh_van_tra_du_thong_tin_qua_duong_MOI(tmp_path, tai_ve_gia, nhat_ky):
    """`n>1`: toàn bộ ảnh ở `outputs` — đổi đường gửi không được mất ảnh đã trả tiền."""
    client = ClientDayDu(job_xong=job_anh(n=3), vi_tri=1)
    ok, info, err = sic.generate_image("x", str(tmp_path / "img" / "A.png"), n=3,
                                       client=client, log=nhat_ky)
    assert ok is True, err
    assert len(info["paths"]) == 3
    assert client.so.image_calls[0]["n"] == 3


def test_uoc_luong_vuot_HAN_thi_ghi_canh_bao_TO(tmp_path, tai_ve_gia, nhat_ky):
    """Đúng cảnh 14 job "vượt quá thời gian chờ" — phải hiện lên, không nuốt."""
    anh = tmp_path / "img" / "S.png"
    anh.parent.mkdir(parents=True)
    anh.write_bytes(b"\x89PNG")
    client = ClientDayDu(job_xong=job_video(), vi_tri=40, uoc_giay=1500.0)

    svc.generate(str(anh), "x", str(tmp_path / "a.mp4"), client=client,
                 log=nhat_ky, timeout=1200)

    assert any("het han NGAY TRONG HANG CHO" in m for _lv, m in nhat_ky.dong)


def test_client_KHONG_CO_create_thi_LUI_VE_create_and_wait(tmp_path, tai_ve_gia,
                                                           nhat_ky):
    """Bản SDK cũ / client giả chỉ có `create_and_wait` vẫn phải chạy được.

    Mất tín hiệu hàng chờ thì tool chạy đúng như bản cũ — chậm hơn về điều nhịp,
    nhưng **không bao giờ gãy**.
    """
    from conftest import FakeClient

    anh = tmp_path / "img" / "S.png"
    anh.parent.mkdir(parents=True)
    anh.write_bytes(b"\x89PNG")
    client = FakeClient(video_job=job_video())

    ok, info, err = svc.generate(str(anh), "x", str(tmp_path / "a.mp4"),
                                 client=client, log=nhat_ky)
    assert ok is True, err
    assert client.so.video_calls[0]["engine"] == "veo3"


def test_bao_hang_cho_NGOAI_ME_khong_lam_gi_ca():
    """`generate_image` gọi lẻ một phát không cần biết cổng là gì."""
    sb._cuc_bo.cong = None
    sb.bao_hang_cho(999, 9999.0)      # không được ném
    sb.bao_nghen(30.0)


# ── Bộ điều nhịp HỎI TRẠNG THÁI ─────────────────────────────────────────────
#
# Đo 12/08/2026: đẩy 300 job ảnh chỉ bằng POST -> máy chủ dựng 134 job đồng thời,
# 222 ảnh/phút. Cùng ngày, đo qua `create_and_wait` lại ra "bão hoà ở 40 chỗ,
# p50 vọt 36 -> 186s". Khác biệt duy nhất: cách sau hỏi thăm TỪNG job, và 100
# job x hỏi mỗi 1-5s là vài nghìn request/phút trên hạn mức 1.000.
#
# Trần thật của dây chuyền là NGÂN SÁCH LỜI GỌI của tool, không phải sức chứa
# nhà máy — và ngân sách thì chia được.


def test_cang_nhieu_job_bay_thi_hoi_cang_thua(sc):
    nhip = sc.NhipHoiTham(moi_giay=4.0)
    assert nhip.nhip() == sc.HOI_TOI_THIEU, "chua co job nao ma da hoi thua"
    for _ in range(8):
        nhip.vao()
    assert nhip.nhip() == 2.0, "8 job / 4 moi giay = 2s"
    for _ in range(72):
        nhip.vao()
    assert nhip.nhip() == 20.0, "80 job / 4 moi giay = 20s"


def test_nhip_hoi_bi_kep_hai_dau(sc):
    nhip = sc.NhipHoiTham(moi_giay=1000.0)
    nhip.vao()
    assert nhip.nhip() == sc.HOI_TOI_THIEU, "hoi day hon 1s la phi request"
    nhip = sc.NhipHoiTham(moi_giay=0.5)
    for _ in range(500):
        nhip.vao()
    assert nhip.nhip() == sc.HOI_TOI_DA, "hoi thua qua thi biet job xong rat muon"


def test_tra_cho_thi_nhip_hoi_dan_lai(sc):
    nhip = sc.NhipHoiTham(moi_giay=2.0)
    for _ in range(20):
        nhip.vao()
    cao = nhip.nhip()
    for _ in range(18):
        nhip.ra()
    assert nhip.nhip() < cao, "job xong roi ma van hoi thua nhu luc dong"
    assert nhip.dang_bay == 2


def test_ngan_sach_giu_duoc_du_bao_nhieu_job(sc):
    """Số request/giây phải xấp xỉ ngân sách, BẤT KỂ có bao nhiêu job bay.

    Đây là cả lý do lớp này tồn tại: tổng tải lên máy chủ không đổi, nên thêm
    job không bao giờ phá trần rate-limit.
    """
    for n in (10, 50, 120):
        nhip = sc.NhipHoiTham(moi_giay=5.0)
        for _ in range(n):
            nhip.vao()
        req_moi_giay = n / nhip.nhip()
        assert 4.0 <= req_moi_giay <= 6.0, (
            "{0} job -> {1:.1f} req/giay, lech ngan sach 5".format(n, req_moi_giay))


def test_doc_trang_thai_hong_LIEN_TIEP_thi_NEM_chu_khong_quay_vong_cam(sc):
    """Lỗi VĨNH VIỄN không được nuốt như lỗi thoáng qua.

    Vòng chờ từng bọc `retrieve` trong `except: continue` trần. Client thiếu
    `retrieve`, khoá hết quyền, endpoint đổi tên — tất cả đều bị coi là "mạng
    chập", và vòng lặp quay tới hết `timeout` rồi mới báo. Sai chỗ nào cũng ra
    đúng một triệu chứng: treo.
    """
    class _JobsHong:
        def retrieve(self, job_id):
            raise RuntimeError("khong co quyen")

    class _C:
        jobs = _JobsHong()

    import time as _t
    t0 = _t.time()
    with pytest.raises(RuntimeError):
        sc._cho_job_xong(_C(), "job_x", timeout=300, uoc_giay=0)
    assert _t.time() - t0 < 60, "quay vong cam thay vi nem ra ngay"


def test_client_KHONG_CO_retrieve_thi_di_duong_SDK_tu_dau(sc, monkeypatch):
    """Phát hiện thiếu `retrieve` ở CỬA, đừng phát hiện giữa vòng lặp."""
    goi = {"wait": 0}

    class _Jobs:
        def wait(self, job_id, timeout=None, on_progress=None, estimated_seconds=None, **kw):
            goi["wait"] += 1
            return {"id": job_id, "status": "succeeded"}

    class _TaiNguyen:
        def create(self, **kw):
            return {"id": "job_x", "status": "queued"}

    class _C:
        images = _TaiNguyen()
        jobs = _Jobs()

    ra = sc.tao_va_cho(_C(), "images", timeout=30, prompt="x")
    assert goi["wait"] == 1, "khong lui ve jobs.wait khi thieu retrieve"
    assert (ra.get("status") if isinstance(ra, dict) else ra["status"]) == "succeeded"
