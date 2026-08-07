"""Kiểm `veo3top_engine/shopapi_video_client.py`.

Bài quan trọng nhất cả file: **luôn gửi `engine="veo3"` kèm `duration=8`.**
Sai cặp này là 422 `unsupported_parameter` cho TỪNG job — một mã 200 scene là
200 lần lỗi liên tiếp.
"""

from __future__ import annotations

import pytest

import shopapi_video_client as svc
from conftest import FakeClient, job_video


def _loi_gia(ten_lop, thong_diep, **thuoc_tinh):
    lop = type(ten_lop, (Exception,), {})
    exc = lop(thong_diep)
    exc.message = thong_diep
    for k, v in thuoc_tinh.items():
        setattr(exc, k, v)
    return exc


@pytest.fixture
def anh_scene(tmp_path):
    p = tmp_path / "img" / "SC001.png"
    p.parent.mkdir(parents=True)
    p.write_bytes(b"\x89PNG-gia")
    return p


# ── BẪY SỐ MỘT: cặp engine/duration ─────────────────────────────────────────


def test_LUON_gui_engine_veo3_va_duration_8(tmp_path, anh_scene, tai_ve_gia, nhat_ky):
    client = FakeClient(video_job=job_video())
    svc.generate(str(anh_scene), "canh mua", str(tmp_path / "vid" / "SC001.mp4"),
                 client=client, log=nhat_ky)
    goi = client.so.video_calls[0]
    assert goi["engine"] == "veo3"
    assert goi["duration"] == 8, "veo3 CHI nhan 8 giay; 10 la 422 unsupported_parameter"


def test_hang_so_module_khop_nhau(tmp_path):
    """Đổi ENGINE mà quên đổi DURATION là 422 hàng loạt — chốt lại bằng bài kiểm."""
    assert (svc.ENGINE, svc.DURATION) in (("veo3", 8), ("seedance", 10))


def test_khong_bao_gio_gui_engine_auto(tmp_path, anh_scene, tai_ve_gia, nhat_ky):
    """`auto` KHÔNG phải cơ chế dự phòng — nó chỉ chọn máy rảnh trong cùng engine."""
    client = FakeClient(video_job=job_video())
    svc.generate(str(anh_scene), "x", str(tmp_path / "a.mp4"), client=client, log=nhat_ky)
    assert client.so.video_calls[0]["engine"] != "auto"


# ── Đường thành công ─────────────────────────────────────────────────────────


def test_tra_dung_3_phan_tu_va_ghi_file_that(tmp_path, anh_scene, tai_ve_gia, nhat_ky):
    client = FakeClient(video_job=job_video())
    dich = tmp_path / "vid" / "SC001.mp4"

    ket_qua = svc.generate(str(anh_scene), "x", str(dich), client=client, log=nhat_ky)

    assert len(ket_qua) == 3, "phai khop chu ky (ok, info, err) cua video_factory_client"
    ok, info, err = ket_qua
    assert ok is True and err == ""
    assert dich.exists() and dich.stat().st_size > 0
    assert info["engine"] == "veo3" and info["duration"] == 8
    assert info["backend"] == "shopapi"


def test_anh_scene_duoc_upload_thanh_url_truoc_khi_gui(tmp_path, anh_scene, tai_ve_gia, nhat_ky):
    client = FakeClient(video_job=job_video())
    svc.generate(str(anh_scene), "x", str(tmp_path / "a.mp4"), client=client, log=nhat_ky)
    assert client.so.uploads[0]["file"] == str(anh_scene)
    assert client.so.video_calls[0]["image_url"].startswith("https://")


def test_ty_le_duoc_doi_sang_chuoi_may_chu_hieu(tmp_path, anh_scene, tai_ve_gia, nhat_ky):
    client = FakeClient(video_job=job_video())
    svc.generate(str(anh_scene), "x", str(tmp_path / "a.mp4"),
                 aspect="VIDEO_ASPECT_RATIO_PORTRAIT", client=client, log=nhat_ky)
    assert client.so.video_calls[0]["aspect_ratio"] == "9:16"


def test_seed_bi_bo_qua_va_co_ghi_log(tmp_path, anh_scene, tai_ve_gia, nhat_ky):
    client = FakeClient(video_job=job_video())
    svc.generate(str(anh_scene), "x", str(tmp_path / "a.mp4"), seed=42,
                 client=client, log=nhat_ky)
    assert "seed" not in client.so.video_calls[0]
    assert any("seed" in m for _, m in nhat_ky.dong)


# ── Lỗi ──────────────────────────────────────────────────────────────────────


def test_thieu_anh_scene_thi_bao_ro_khong_gui_job(tmp_path, tai_ve_gia, nhat_ky):
    client = FakeClient(video_job=job_video())
    ok, _, err = svc.generate(str(tmp_path / "khong-co.png"), "x",
                              str(tmp_path / "a.mp4"), client=client, log=nhat_ky)
    assert ok is False
    assert "khong thay anh scene" in err
    assert client.so.video_calls == [], "thieu anh thi KHONG duoc tao job (khoi mat tien)"


@pytest.mark.parametrize("ten_lop,mong_doi", [
    ("InsufficientBalanceError", "HET TIEN"),
    ("AuthenticationError", "KHOA API HONG"),
    ("RateLimitError", "429"),
    ("EngineUnavailableError", "503"),
    ("UnsupportedParameterError", "422"),
])
def test_loi_ra_thong_bao_tieng_viet_ro_rang(tmp_path, anh_scene, tai_ve_gia, nhat_ky,
                                             ten_lop, mong_doi):
    client = FakeClient(video_loi=_loi_gia(ten_lop, "may chu bao loi"))
    dich = tmp_path / "vid" / "SC001.mp4"

    ok, _, err = svc.generate(str(anh_scene), "x", str(dich), client=client, log=nhat_ky)

    assert ok is False
    assert mong_doi in err, err
    assert err.startswith("shopapi-vid:")
    assert not dich.exists()


def test_job_bao_xong_nhung_khong_co_file(tmp_path, anh_scene, tai_ve_gia, nhat_ky):
    client = FakeClient(video_job={"id": "job_rong", "status": "succeeded"})
    ok, _, err = svc.generate(str(anh_scene), "x", str(tmp_path / "a.mp4"),
                              client=client, log=nhat_ky)
    assert ok is False
    assert "khong co file ket qua" in err
