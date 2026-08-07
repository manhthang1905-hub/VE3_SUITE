"""Kiểm `veo3top_engine/shopapi_image_client.py` — client GIẢ, không chạm mạng."""

from __future__ import annotations

import os

import pytest

import shopapi_image_client as sic
from conftest import FakeClient, job_anh


def _loi_gia(ten_lop, thong_diep, **thuoc_tinh):
    lop = type(ten_lop, (Exception,), {})
    exc = lop(thong_diep)
    exc.message = thong_diep
    for k, v in thuoc_tinh.items():
        setattr(exc, k, v)
    return exc


# ── Đường thành công ─────────────────────────────────────────────────────────


def test_tra_dung_3_phan_tu_va_GHI_FILE_THAT_RA_DIA(tmp_path, tai_ve_gia, nhat_ky):
    client = FakeClient(image_job=job_anh(n=1))
    dich = tmp_path / "img" / "SC001.png"

    ket_qua = sic.generate_image("mot con meo", str(dich), client=client, log=nhat_ky)

    assert len(ket_qua) == 3, "phai khop chu ky (ok, info, err) cua image_factory_client"
    ok, info, err = ket_qua
    assert ok is True and err == ""
    # Hợp đồng: hàm TỰ ghi file; caller chỉ kiểm file có tồn tại.
    assert dich.exists() and dich.stat().st_size > 0
    assert info["backend"] == "shopapi"
    assert info["media_name"] == "job_img_1"   # không rỗng -> Excel không coi là thiếu ref
    assert info["bytes"] == dich.stat().st_size


def test_tu_tao_thu_muc_cha_neu_chua_co(tmp_path, tai_ve_gia, nhat_ky):
    client = FakeClient(image_job=job_anh(n=1))
    dich = tmp_path / "chua" / "he" / "ton" / "tai" / "X.png"
    ok, _, err = sic.generate_image("x", str(dich), client=client, log=nhat_ky)
    assert ok is True, err
    assert dich.exists()


def test_ty_le_duoc_doi_sang_chuoi_may_chu_hieu(tmp_path, tai_ve_gia, nhat_ky):
    client = FakeClient(image_job=job_anh(n=1))
    sic.generate_image("x", str(tmp_path / "a.png"),
                       aspect="IMAGE_ASPECT_RATIO_PORTRAIT", client=client, log=nhat_ky)
    assert client.so.image_calls[0]["aspect_ratio"] == "9:16"


# ── BẪY: n>1 phải đọc `outputs` ──────────────────────────────────────────────


def test_n_lon_hon_1_lay_tu_outputs_chu_khong_phai_output(tmp_path, tai_ve_gia, nhat_ky):
    """`output` chỉ là ảnh đầu tiên -> chỉ đọc nó là mất n-1 ảnh ĐÃ TRẢ TIỀN."""
    client = FakeClient(image_job=job_anh(n=3))
    dich = tmp_path / "img" / "SC009.png"

    ok, info, err = sic.generate_image("x", str(dich), n=3, client=client, log=nhat_ky)

    assert ok is True, err
    assert client.so.image_calls[0]["n"] == 3
    # Cả 3 ảnh phải nằm trên đĩa, không chỉ cái đầu.
    assert dich.exists()
    assert (tmp_path / "img" / "SC009_2.png").exists()
    assert (tmp_path / "img" / "SC009_3.png").exists()
    assert len(info["extra_paths"]) == 2
    assert len(tai_ve_gia) == 3


def test_anh_dau_tien_giu_dung_ten_caller_yeu_cau(tmp_path, tai_ve_gia, nhat_ky):
    """Ràng buộc tên: `vid/X.mp4` cần `img/X.png` cùng stem -> ảnh đầu KHÔNG được đổi tên."""
    client = FakeClient(image_job=job_anh(n=2))
    dich = tmp_path / "img" / "SC042.png"
    sic.generate_image("x", str(dich), n=2, client=client, log=nhat_ky)
    assert tai_ve_gia[0][1] == str(dich)


# ── Ảnh tham chiếu: đường dẫn máy phải được UPLOAD thành URL ─────────────────


def test_duong_dan_may_duoc_upload_thanh_url_truoc_khi_gui(tmp_path, tai_ve_gia, nhat_ky):
    ref = tmp_path / "nv" / "NV01.png"
    ref.parent.mkdir(parents=True)
    ref.write_bytes(b"\x89PNG-gia")
    client = FakeClient(image_job=job_anh(n=1))

    ok, _, err = sic.generate_image("x", str(tmp_path / "a.png"),
                                    reference_images=[str(ref)], client=client, log=nhat_ky)

    assert ok is True, err
    assert len(client.so.uploads) == 1
    assert client.so.uploads[0]["file"] == str(ref)
    gui_len = client.so.image_calls[0]["reference_images"]
    # Máy chủ không nhìn thấy ổ D của bạn -> phải là URL, tuyệt đối không phải đường dẫn.
    assert all(u.startswith("https://") for u in gui_len)
    assert str(ref) not in gui_len


def test_bytes_anh_cung_duoc_upload(tmp_path, tai_ve_gia, nhat_ky):
    client = FakeClient(image_job=job_anh(n=1))
    sic.generate_image("x", str(tmp_path / "a.png"),
                       reference_images=[b"\x89PNG-bytes"], client=client, log=nhat_ky)
    assert len(client.so.uploads) == 1
    assert client.so.image_calls[0]["reference_images"][0].startswith("https://")


def test_url_san_thi_khong_upload_lai(tmp_path, tai_ve_gia, nhat_ky):
    client = FakeClient(image_job=job_anh(n=1))
    sic.generate_image("x", str(tmp_path / "a.png"),
                       reference_images=["https://co-san.invalid/a.png"],
                       client=client, log=nhat_ky)
    assert client.so.uploads == []
    assert client.so.image_calls[0]["reference_images"] == ["https://co-san.invalid/a.png"]


def test_qua_10_anh_tham_chieu_thi_CAT_BOT_va_canh_bao_TO(tmp_path, tai_ve_gia, nhat_ky):
    """Gửi mù 13 cái là 400 cho CẢ job -> mất luôn 10 cái hợp lệ. Cắt còn 10 vẫn ra ảnh."""
    client = FakeClient(image_job=job_anh(n=1))
    refs = [b"anh-%d" % i for i in range(13)]

    ok, _, err = sic.generate_image("x", str(tmp_path / "a.png"),
                                    reference_images=refs, client=client, log=nhat_ky)

    assert ok is True, err
    assert len(client.so.image_calls[0]["reference_images"]) == 10
    assert any(lv == "WARN" and "CAT BOT" in m for lv, m in nhat_ky.dong), \
        "phai co canh bao TO, khong duoc cat bot am tham"


def test_dung_10_anh_thi_khong_cat_khong_canh_bao(tmp_path, tai_ve_gia, nhat_ky):
    client = FakeClient(image_job=job_anh(n=1))
    sic.generate_image("x", str(tmp_path / "a.png"),
                       reference_images=[b"a%d" % i for i in range(10)],
                       client=client, log=nhat_ky)
    assert len(client.so.image_calls[0]["reference_images"]) == 10
    assert not any("CAT BOT" in m for _, m in nhat_ky.dong)


# ── Lỗi: KHÔNG được nuốt ─────────────────────────────────────────────────────


@pytest.mark.parametrize("ten_lop,mong_doi", [
    ("InsufficientBalanceError", "HET TIEN"),
    ("AuthenticationError", "KHOA API HONG"),
    ("RateLimitError", "429"),
    ("EngineUnavailableError", "503"),
    ("ContentRejectedError", "NOI DUNG BI TU CHOI"),
])
def test_loi_ra_thong_bao_tieng_viet_ro_rang(tmp_path, tai_ve_gia, nhat_ky, ten_lop, mong_doi):
    client = FakeClient(image_loi=_loi_gia(ten_lop, "may chu bao loi"))
    dich = tmp_path / "a.png"

    ok, info, err = sic.generate_image("x", str(dich), client=client, log=nhat_ky)

    assert ok is False
    assert mong_doi in err, err
    assert err.startswith("shopapi-img:")
    assert not dich.exists(), "loi thi KHONG duoc de lai file rong tren dia"


def test_upload_hong_thi_bao_ro_khong_gui_job(tmp_path, tai_ve_gia, nhat_ky):
    client = FakeClient(image_job=job_anh(n=1),
                        upload_loi=_loi_gia("APIConnectionError", "mat mang"))
    ok, _, err = sic.generate_image("x", str(tmp_path / "a.png"),
                                    reference_images=[b"a"], client=client, log=nhat_ky)
    assert ok is False
    assert "upload anh tham chieu that bai" in err
    assert client.so.image_calls == [], "upload hong thi KHONG duoc tinh tien tao anh"


def test_job_bao_xong_nhung_khong_co_file_thi_coi_la_that_bai(tmp_path, tai_ve_gia, nhat_ky):
    job = {"id": "job_rong", "status": "succeeded", "progress": 100}
    client = FakeClient(image_job=job)
    ok, _, err = sic.generate_image("x", str(tmp_path / "a.png"), client=client, log=nhat_ky)
    assert ok is False
    assert "khong co file ket qua" in err
