"""Kiểm phần dùng chung: tỉ lệ khung, đọc `outputs`, kho khoá, dịch lỗi."""

from __future__ import annotations

import os

import pytest

from conftest import KHOA_GIA, job_anh


# ── Tỉ lệ khung ──────────────────────────────────────────────────────────────


@pytest.mark.parametrize("dau_vao,mong_doi", [
    ("IMAGE_ASPECT_RATIO_PORTRAIT", "9:16"),
    ("IMAGE_ASPECT_RATIO_LANDSCAPE", "16:9"),
    ("IMAGE_ASPECT_RATIO_SQUARE", "1:1"),
    ("VIDEO_ASPECT_RATIO_PORTRAIT", "9:16"),
    ("VIDEO_ASPECT_RATIO_LANDSCAPE", "16:9"),
    ("portrait", "9:16"),
    ("landscape", "16:9"),
    ("square", "1:1"),
    ("9:16", "9:16"),
    ("4:3", "4:3"),
])
def test_ty_le_api_quy_ve_chuoi_may_chu_hieu(sc, dau_vao, mong_doi):
    assert sc.ty_le_api(dau_vao) == mong_doi


def test_ty_le_api_khong_nhan_ra_thi_lay_mac_dinh_chu_khong_nem_loi(sc):
    # Sai tỉ lệ chỉ xấu khung hình; ném lỗi là hỏng cả job đã trả tiền.
    assert sc.ty_le_api("mot-thu-la-hoac") == "16:9"
    assert sc.ty_le_api(None) == "16:9"


def test_ty_le_api_doc_duoc_enum_cua_google_flow_api(sc):
    from modules.google_flow_api import AspectRatio, VideoAspectRatio
    assert sc.ty_le_api(AspectRatio.PORTRAIT) == "9:16"
    assert sc.ty_le_api(VideoAspectRatio.LANDSCAPE) == "16:9"


# ── outputs vs output ────────────────────────────────────────────────────────


def test_lay_outputs_n_bang_1_doc_tu_output(sc):
    job = job_anh(n=1)
    assert "outputs" not in job          # máy chủ KHÔNG gửi outputs khi n=1
    assert len(sc.lay_outputs(job)) == 1


def test_lay_outputs_n_lon_hon_1_phai_doc_tu_outputs_khong_phai_output(sc):
    """BẪY: `output` chỉ là ảnh ĐẦU TIÊN. Đọc nhầm là mất n-1 ảnh đã trả tiền."""
    job = job_anh(n=4)
    ket_qua = sc.lay_outputs(job)
    assert len(ket_qua) == 4
    urls = [sc.url_cua_output(o) for o in ket_qua]
    assert urls == ["https://cdn.example.invalid/anh{0}.png".format(i) for i in (1, 2, 3, 4)]


def test_lay_outputs_job_rong_tra_danh_sach_rong(sc):
    assert sc.lay_outputs(None) == []
    assert sc.lay_outputs({"id": "x", "status": "failed"}) == []


# ── Kho khoá ─────────────────────────────────────────────────────────────────


def test_doc_khoa_uu_tien_bien_moi_truong(sc):
    key, nguon = sc.doc_khoa(env={"SHOPAPI_KEY": KHOA_GIA})
    assert key == KHOA_GIA
    assert "SHOPAPI_KEY" in nguon
    assert KHOA_GIA not in nguon          # nguồn KHÔNG được lộ chính khoá


def test_doc_khoa_doc_file_tro_boi_bien_moi_truong(sc, tmp_path):
    f = tmp_path / "khoa.txt"
    f.write_text(KHOA_GIA + "\n", encoding="utf-8")
    key, nguon = sc.doc_khoa(env={"SHOPAPI_KEY_FILE": str(f)})
    assert key == KHOA_GIA
    assert KHOA_GIA not in nguon


def test_luu_khoa_roi_doc_lai_duoc_va_nam_ngoai_kho_ma(sc, tmp_path):
    env = {"APPDATA": str(tmp_path), "XDG_CONFIG_HOME": str(tmp_path)}
    duong_dan = sc.luu_khoa(KHOA_GIA, env=env)
    assert os.path.exists(duong_dan)
    assert str(tmp_path) in duong_dan     # nằm trong thư mục người dùng, không phải kho mã
    key, nguon = sc.doc_khoa(env=env)
    assert key == KHOA_GIA
    assert nguon == "kho khoa rieng cua may nay"
    sc.quen_khoa(env=env)
    assert sc.doc_khoa(env=env) == ("", "")


def test_khong_co_khoa_o_dau_ca_thi_tra_rong(sc, tmp_path):
    assert sc.doc_khoa(env={"APPDATA": str(tmp_path), "XDG_CONFIG_HOME": str(tmp_path)}) == ("", "")


def test_che_khoa_va_redact_khong_de_lo_khoa(sc):
    che = sc.che_khoa(KHOA_GIA)
    assert KHOA_GIA not in che
    assert che.startswith("sk_live_")
    dong_log = "loi khi goi voi key {0} tren /v1/images".format(KHOA_GIA)
    assert KHOA_GIA not in sc.redact(dong_log)


# ── Dịch lỗi sang tiếng Việt ─────────────────────────────────────────────────


def _loi_gia(ten_lop, thong_diep, **thuoc_tinh):
    """Dựng ngoại lệ MANG ĐÚNG TÊN LỚP của SDK mà không cần cài SDK."""
    lop = type(ten_lop, (Exception,), {})
    exc = lop(thong_diep)
    exc.message = thong_diep
    for k, v in thuoc_tinh.items():
        setattr(exc, k, v)
    return exc


def test_het_tien_noi_ro_chua_bi_tru_dong_nao(sc):
    msg = sc.mo_ta_loi(_loi_gia("InsufficientBalanceError", "So du khong du. Can 500d."))
    assert "HET TIEN" in msg
    assert "CHUA bi tru" in msg


def test_sai_khoa_chi_duong_tao_khoa_moi(sc):
    msg = sc.mo_ta_loi(_loi_gia("AuthenticationError", "Key nay sai hoac da bi thu hoi."))
    assert "KHOA API HONG" in msg
    assert "api-keys" in msg


def test_429_noi_ro_so_giay_can_cho(sc):
    msg = sc.mo_ta_loi(_loi_gia("RateLimitError", "Ban gui hoi nhanh.", retry_after=12.0))
    assert "429" in msg
    assert "12 giay" in msg


def test_503_noi_ro_job_chua_tao_va_khong_bi_tru_tien(sc):
    msg = sc.mo_ta_loi(_loi_gia("EngineUnavailableError", "Cum xu ly tam ban."))
    assert "503" in msg
    assert "KHONG bi tru tien" in msg


def test_422_nhac_dung_bay_8_giay_va_10_giay(sc):
    msg = sc.mo_ta_loi(_loi_gia("UnsupportedParameterError", "duration khong hop le"))
    assert "422" in msg
    assert "8 giay" in msg and "10 giay" in msg


def test_loi_la_hoac_van_ra_mot_dong_doc_duoc_khong_nuot(sc):
    msg = sc.mo_ta_loi(ValueError("cai gi do la lam"))
    assert "ValueError" in msg
    assert "cai gi do la lam" in msg


def test_thong_diep_loi_bi_che_khoa_truoc_khi_ghi_log(sc):
    """Máy chủ đôi khi nhắc lại tham số gửi lên -> không thể tin là nó sạch sẵn."""
    msg = sc.mo_ta_loi(_loi_gia("AuthenticationError", "key {0} bi thu hoi".format(KHOA_GIA)))
    assert KHOA_GIA not in msg


# ── Tải file ─────────────────────────────────────────────────────────────────


def test_tai_ve_ghi_ra_dung_duong_dan_va_khong_de_lai_file_part(sc, tmp_path, monkeypatch):
    """Không dùng mạng: giả `httpx` bằng một client cục bộ."""
    class _Resp:
        status_code = 200
        headers = {"Content-Length": "5"}

        def iter_bytes(self, n):
            yield b"12345"

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    class _Http:
        def __init__(self, **kw):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def stream(self, method, url):
            return _Resp()

    import httpx
    monkeypatch.setattr(httpx, "Client", _Http)
    dich = tmp_path / "sau" / "nua" / "a.png"
    sc.tai_ve("https://x.invalid/a.png", str(dich))
    assert dich.exists() and dich.read_bytes() == b"12345"
    assert not (tmp_path / "sau" / "nua" / "a.png.part").exists()


# ── Bộ lọc nội dung chặn prompt -> phải bật đường VIẾT LẠI ───────────────────
#
# Hai bài dưới đây giữ MỘT hợp đồng bắc qua hai file: `mo_ta_loi` (bên
# `veo3top_engine`) phải nhả ra mã máy đọc, và `_is_policy_violation_error` (bên
# `tools/ve3`) phải bắt được nó. Đứt một đầu là cả bộ máy viết lại prompt 3 vòng
# nằm im, cảnh bị chặn đếm thành "hỏng" và người dùng mất cảnh không rõ lý do.
#
# ĐÃ DÍNH THẬT (job_rqv2tbpk3fytiykdwzgp2zna): máy chủ trả
# `code="content_rejected"` kèm `message` là VĂN XUÔI TIẾNG VIỆT, nên không một
# marker tiếng Anh nào ("policy", "unsafe", "violat"…) khớp được.


def test_job_bi_chan_giu_lai_ma_may_doc_chu_khong_chi_van_xuoi(sc):
    """`mo_ta_loi` phải kèm `job.error.code`, vì `message` là tiếng Việt đổi lúc nào không hay."""
    van_xuoi_that = (
        "Mô tả (prompt) của bạn bị chặn, nhưng nhà cung cấp mô hình cho biết VIẾT "
        "LẠI MÔ TẢ là dùng được — nội dung không bị cấm hẳn."
    )
    msg = sc.mo_ta_loi(_loi_gia("JobFailedError", van_xuoi_that, code="content_rejected"))
    assert "content_rejected" in msg
    assert van_xuoi_that in msg, "van xuoi cua may chu van phai con de nguoi doc hieu"


def test_job_hong_khong_co_ma_van_ra_mot_dong_doc_duoc(sc):
    """Thiếu `code` thì vẫn phải ra câu tử tế, tuyệt đối không nổ `AttributeError`."""
    msg = sc.mo_ta_loi(_loi_gia("JobFailedError", "cum xu ly chet giua chung"))
    assert "JOB HONG" in msg
    assert "cum xu ly chet giua chung" in msg


@pytest.mark.parametrize("ten_lop, thuoc_tinh", [
    ("JobFailedError", {"code": "content_rejected"}),
    ("ContentRejectedError", {}),
])
def test_worker_nhan_ra_day_la_loi_policy_de_bat_duong_viet_lai(sc, ten_lop, thuoc_tinh):
    """Hợp đồng bắc cầu: chuỗi `mo_ta_loi` nhả ra PHẢI lọt cửa `_is_policy_violation_error`."""
    from ve3_worker import VE3Worker

    msg = sc.mo_ta_loi(_loi_gia(ten_lop, "Mô tả của bạn bị chặn.", **thuoc_tinh))
    # Gọi như hàm thuần: nó chỉ soi chuỗi, không đụng gì tới trạng thái worker.
    assert VE3Worker._is_policy_violation_error(None, msg) is True
