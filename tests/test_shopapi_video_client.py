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


# ── Dọn ảnh khung đầu khỏi kho ───────────────────────────────────────────────
#
# CONTRACT.md §6 đặt trần 200 FILE CÒN SỐNG và 500 MB cho kho của một khách, và
# file đã có job dùng sống 2 giờ kể từ lần dùng gần nhất. Mỗi job video upload
# một ảnh khung đầu, client trước nay không xoá cái nào. Đo 11/08/2026: 456
# video/giờ × 2 giờ = ~900 file còn sống trên trần 200.
#
# Nó nổ theo kiểu khó đoán nhất — job mới bị từ chối ở khâu upload, tức hỏng ở
# một chỗ chẳng liên quan gì tới nội dung lẫn nhà máy.


def test_video_xong_thi_XOA_anh_khung_dau_khoi_kho(tmp_path, anh_scene, tai_ve_gia, nhat_ky):
    client = FakeClient(video_job=job_video())
    ok, _info, err = svc.generate(str(anh_scene), "canh mua",
                                  str(tmp_path / "vid" / "SC001.mp4"),
                                  client=client, log=nhat_ky)
    assert ok and not err
    assert len(client.uploads.da_xoa) == 1, "khong don anh khung dau -> tran 200 file se no"
    assert client.uploads.da_xoa[0].startswith("upl_")


def test_moi_dung_ma_upload_tu_URL_co_chu_ky_dai(tmp_path):
    """URL thật kéo theo cả chuỗi tham số chữ ký — moi mã phải không dính chúng."""
    that = ("https://cdn.shopapi.vn/shopapi/uploads/usr_ylghhdawbfx3kl7n6y71s0wp/"
            "2026/08/11/upl_rh0kp0npms36fw99a7spkldj.png?X-Amz-Algorithm=AWS4-HMAC-SHA256"
            "&X-Amz-Signature=1bf30dad&x-id=GetObject")
    assert svc._ma_upload(that) == "upl_rh0kp0npms36fw99a7spkldj"
    assert svc._ma_upload("https://cdn.example.invalid/khong-phai-kho.png") is None
    assert svc._ma_upload("") is None
    assert svc._ma_upload(None) is None


def test_job_HONG_thi_KHONG_duoc_xoa_anh(tmp_path, anh_scene, nhat_ky):
    """Hỏng thì `chay_ca_me` trả job về hàng chờ và chạy lại.

    Xoá ảnh lúc đó là biến một lần thử lại bình thường thành hỏng vĩnh viễn:
    lượt sau không còn `img/SC001.png` trên kho để mà dựng.
    """
    client = FakeClient(video_loi=_loi_gia("APIError", "nha may nga"))
    ok, _info, err = svc.generate(str(anh_scene), "x", str(tmp_path / "a.mp4"),
                                  client=client, log=nhat_ky)
    assert not ok and err
    assert client.uploads.da_xoa == [], "da xoa anh cua mot job SE DUOC CHAY LAI"


def test_tai_video_that_bai_thi_KHONG_xoa_anh(tmp_path, anh_scene, nhat_ky, sc, monkeypatch):
    """Video dựng xong nhưng tải về hỏng -> vẫn còn đường thử lại, giữ ảnh."""
    def _no(url, dest_path, timeout=600.0):
        raise OSError("mat mang giua chung")

    monkeypatch.setattr(sc, "tai_ve", _no)
    client = FakeClient(video_job=job_video())
    ok, _info, err = svc.generate(str(anh_scene), "x", str(tmp_path / "a.mp4"),
                                  client=client, log=nhat_ky)
    assert not ok and err
    assert client.uploads.da_xoa == []


def test_don_khong_duoc_thi_VAN_tra_video_ve(tmp_path, anh_scene, tai_ve_gia, nhat_ky):
    """Video đã nằm trên đĩa và khách đã trả tiền — dọn trượt không được làm hỏng job.

    Dọn trượt chỉ có nghĩa là file đó chờ hết 2 giờ như trước, đúng bằng hành vi cũ.
    """
    client = FakeClient(video_job=job_video())
    client.uploads.loi_xoa = _loi_gia("APIError", "kho tu choi")
    ok, info, err = svc.generate(str(anh_scene), "x", str(tmp_path / "a.mp4"),
                                 client=client, log=nhat_ky)
    assert ok and not err, "don truot KHONG duoc lam hong job da xong"
    assert info["bytes"] > 0
    assert any("khong don duoc" in m for _lv, m in nhat_ky.dong), "nuot loi im lang"


def test_khach_tu_dua_URL_thi_KHONG_dong_vao(tmp_path, tai_ve_gia, nhat_ky):
    """URL sẵn không phải file của ta upload — xoá là xoá đồ người khác."""
    client = FakeClient(video_job=job_video())
    ok, _info, _err = svc.generate(
        "https://cdn.shopapi.vn/shopapi/uploads/usr_ai_do/2026/08/11/upl_cuanguoikhac.png",
        "x", str(tmp_path / "a.mp4"), client=client, log=nhat_ky)
    assert ok
    assert client.uploads.da_xoa == [], "xoa file ma khach tu dua URL vao"
