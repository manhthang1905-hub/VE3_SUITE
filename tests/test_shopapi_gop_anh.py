"""Kiểm việc **gộp nhiều ảnh cùng prompt vào MỘT job `n=k`**.

VÌ SAO ĐÁNG MỘT BỘ KIỂM RIÊNG: trần song song là tài nguyên khan hiếm nhất của
nhánh API. `k` ảnh cùng prompt gộp lại chỉ tiêu **một** chỗ trong trần và **một**
lần xếp hàng, thay vì `k` chỗ và `k` lần — nên đây là món hời rẻ nhất, và cũng là
chỗ dễ mất ảnh nhất nếu đọc sai khuôn dữ liệu.

Hai cái bẫy được canh ở đây:

* `n>1` thì ảnh nằm ở **`outputs`**, `output` chỉ là file đầu → đọc nhầm là mất
  `n-1` ảnh ĐÃ TRẢ TIỀN mà không có lỗi nào báo;
* ảnh **đầu** phải giữ ĐÚNG tên người gọi yêu cầu → ràng buộc `vid/X.mp4` phải
  có `img/X.png` cùng stem phụ thuộc vào nó.
"""

from __future__ import annotations

import os

import pytest

from conftest import KHOA_GIA, FakeClient, job_anh

import shopapi_image_client as sic

from ve3_worker import VE3Worker


CAU_HINH = {
    "generation_backend": "server",
    "veo3top_image_mode": "shopapi",
    "flow_aspect_ratio": "landscape",
    "retry_count": 1,
    "max_concurrent": 2,
}


@pytest.fixture
def co_khoa(monkeypatch, sc):
    monkeypatch.setattr(sc, "doc_khoa", lambda env=None: (KHOA_GIA, "kho khoa gia (bai kiem)"))
    return KHOA_GIA


def _worker(tmp_path, nhat_ky, cau_hinh=None):
    du_an = tmp_path / "PROJECT"
    du_an.mkdir(exist_ok=True)
    cfg = dict(CAU_HINH)
    if cau_hinh:
        cfg.update(cau_hinh)
    return VE3Worker(project_dir=str(du_an), config=cfg, log_func=nhat_ky)


# ── Một job, nhiều file ──────────────────────────────────────────────────────


def test_n_anh_mot_job_ghi_du_N_file_va_file_dau_giu_dung_stem(tmp_path, nhat_ky, tai_ve_gia):
    client = FakeClient(image_job=job_anh(n=3))
    dich = tmp_path / "img" / "SC007.png"

    ok, info, err = sic.generate_image("mot canh", str(dich), n=3, client=client,
                                       log=nhat_ky, timeout=5)

    assert ok is True, err
    assert len(client.so.image_calls) == 1, "3 anh phai di trong DUNG MOT job"
    assert client.so.image_calls[0]["n"] == 3
    # Đủ 3 file, và file ĐẦU giữ nguyên tên người gọi yêu cầu.
    assert dich.exists()
    assert (tmp_path / "img" / "SC007_2.png").exists()
    assert (tmp_path / "img" / "SC007_3.png").exists()
    assert info["extra_paths"] == [str(tmp_path / "img" / "SC007_2.png"),
                                   str(tmp_path / "img" / "SC007_3.png")]


def test_out_paths_dua_anh_thu_i_ve_dung_file_cua_scene_thu_i(tmp_path, nhat_ky, tai_ve_gia):
    """Nhiều scene KHÁC NHAU cùng một prompt: mỗi scene phải nhận file mang tên nó."""
    client = FakeClient(image_job=job_anh(n=3))
    dich = [str(tmp_path / "img" / x) for x in ("SC001.png", "SC042.png", "SC099.png")]

    ok, info, err = sic.generate_image("canh trung nhau", dich[0], out_paths=dich,
                                       client=client, log=nhat_ky, timeout=5)

    assert ok is True, err
    assert len(client.so.image_calls) == 1
    assert client.so.image_calls[0]["n"] == 3
    assert info["paths"] == dich
    for p in dich:
        assert os.path.exists(p), "scene {0} phai co file rieng cua no".format(p)
    # KHÔNG được đẻ ra `SC001_2.png` — tên đó không thuộc scene nào cả.
    assert not (tmp_path / "img" / "SC001_2.png").exists()


def test_xin_qua_8_anh_thi_CAT_CON_8_va_bao_TO(tmp_path, nhat_ky, tai_ve_gia, sc):
    """Gửi `n>8` là 400 cho CẢ job → mất luôn 8 ảnh hợp lệ. Cắt còn 8 vẫn dùng được."""
    client = FakeClient(image_job=job_anh(n=8))
    dich = [str(tmp_path / "img" / "S{0}.png".format(i)) for i in range(12)]

    ok, _info, err = sic.generate_image("x", dich[0], out_paths=dich,
                                        client=client, log=nhat_ky, timeout=5)

    assert ok is True, err
    assert client.so.image_calls[0]["n"] == sc.MAX_ANH_MOT_JOB == 8
    assert any(lv == "WARN" and "CAT CON" in m for lv, m in nhat_ky.dong)


def test_may_chu_tra_THIEU_anh_thi_bao_loi_chu_khong_im_lang(tmp_path, nhat_ky, tai_ve_gia):
    """Xin 3 nhận 1: hai scene còn lại PHẢI biết là chưa xong, không được đánh done."""
    client = FakeClient(image_job=job_anh(n=1))       # may chu chi tra 1
    dich = [str(tmp_path / "img" / x) for x in ("A.png", "B.png", "C.png")]

    ok, info, err = sic.generate_image("x", dich[0], out_paths=dich,
                                       client=client, log=nhat_ky, timeout=5)

    assert ok is False
    assert "chi nhan duoc 1" in err
    assert info["paths"] == [dich[0]], "cai da ve dia thi van phai bao la da ve"


# ── Worker: gom nhóm rồi trả từ kho, KHÔNG gọi API lần hai ───────────────────


def test_worker_gop_cac_scene_TRUNG_prompt_vao_mot_job(tmp_path, nhat_ky, co_khoa,
                                                       monkeypatch, sc, tai_ve_gia):
    client = FakeClient(image_job=job_anh(n=3))
    monkeypatch.setattr(sc, "tao_client", lambda **kw: client)
    w = _worker(tmp_path, nhat_ky)

    cong_viec = [("canh y het nhau", w.img_dir / "S1.png", None, w.aspect_ratio),
                 ("canh y het nhau", w.img_dir / "S2.png", None, w.aspect_ratio),
                 ("canh y het nhau", w.img_dir / "S3.png", None, w.aspect_ratio)]
    da_gop = w._shopapi_gop_anh_cung_prompt(cong_viec)

    assert da_gop == 3
    assert len(client.so.image_calls) == 1, "3 scene trung prompt -> DUNG 1 job"
    assert client.so.image_calls[0]["n"] == 3
    for ten in ("S1.png", "S2.png", "S3.png"):
        assert (w.img_dir / ten).exists()

    # Và `_submit_image` lấy thẳng từ kho, KHÔNG bắn job thứ hai (= không trả tiền hai lần).
    ok, media_name, sinfo, err = w._submit_image("canh y het nhau", w.img_dir / "S2.png")
    assert ok is True and err == ""
    assert media_name == "job_img_1"
    assert sinfo["backend"] == "shopapi"
    assert len(client.so.image_calls) == 1, "da co san ma van goi API lai la tra tien hai lan"


def test_moi_ket_qua_gop_chi_dung_DUNG_MOT_LAN(tmp_path, nhat_ky, co_khoa,
                                               monkeypatch, sc, tai_ve_gia):
    client = FakeClient(image_job=job_anh(n=2))
    monkeypatch.setattr(sc, "tao_client", lambda **kw: client)
    w = _worker(tmp_path, nhat_ky)
    w._shopapi_gop_anh_cung_prompt(
        [("p", w.img_dir / "A.png", None, w.aspect_ratio),
         ("p", w.img_dir / "B.png", None, w.aspect_ratio)])

    w._submit_image("p", w.img_dir / "A.png")
    assert len(client.so.image_calls) == 1
    # Lần hai cho CÙNG file (ví dụ chạy lại vì viết lại prompt) -> phải gọi API thật.
    w._submit_image("p khac han", w.img_dir / "A.png")
    assert len(client.so.image_calls) == 2


def test_KHONG_gop_khi_prompt_khac_nhau(tmp_path, nhat_ky, co_khoa, monkeypatch, sc,
                                        tai_ve_gia):
    client = FakeClient(image_job=job_anh(n=2))
    monkeypatch.setattr(sc, "tao_client", lambda **kw: client)
    w = _worker(tmp_path, nhat_ky)

    da_gop = w._shopapi_gop_anh_cung_prompt(
        [("canh mot", w.img_dir / "A.png", None, w.aspect_ratio),
         ("canh hai", w.img_dir / "B.png", None, w.aspect_ratio)])

    assert da_gop == 0
    assert client.so.image_calls == [], "khac prompt ma gop la ra sai anh"


def test_KHONG_gop_khi_anh_tham_chieu_khac_nhau(tmp_path, nhat_ky, co_khoa, monkeypatch,
                                                sc, tai_ve_gia):
    """Cùng prompt mà khác ref là khác ảnh — gộp vào là sai nhân vật."""
    client = FakeClient(image_job=job_anh(n=2))
    monkeypatch.setattr(sc, "tao_client", lambda **kw: client)
    w = _worker(tmp_path, nhat_ky)
    w.nv_dir.mkdir(parents=True, exist_ok=True)
    (w.nv_dir / "NV01.png").write_bytes(b"\x89PNG-mot")
    (w.nv_dir / "NV02.png").write_bytes(b"\x89PNG-hai-dai-hon")
    r1 = w._make_ref("NV01", "media/1")
    r2 = w._make_ref("NV02", "media/2")

    da_gop = w._shopapi_gop_anh_cung_prompt(
        [("cung prompt", w.img_dir / "A.png", [r1], w.aspect_ratio),
         ("cung prompt", w.img_dir / "B.png", [r2], w.aspect_ratio)])

    assert da_gop == 0
    assert client.so.image_calls == []


def test_gop_hong_thi_KHONG_lam_chet_gi_ca(tmp_path, nhat_ky, co_khoa, monkeypatch, sc,
                                           tai_ve_gia):
    """Gộp là tối ưu, không phải đường sống: hỏng thì các ảnh đi đường bình thường."""
    client = FakeClient(image_loi=RuntimeError("may chu do chung"))
    monkeypatch.setattr(sc, "tao_client", lambda **kw: client)
    w = _worker(tmp_path, nhat_ky)

    da_gop = w._shopapi_gop_anh_cung_prompt(
        [("p", w.img_dir / "A.png", None, w.aspect_ratio),
         ("p", w.img_dir / "B.png", None, w.aspect_ratio)])

    assert da_gop == 0
    assert w._shopapi_anh_gop == {}, "gop hong thi khong duoc nhet gi vao kho"
    assert any(lv == "WARN" and "gop that bai" in m for lv, m in nhat_ky.dong)


def test_anh_da_co_tren_dia_thi_khong_gop_lai(tmp_path, nhat_ky, co_khoa, monkeypatch, sc,
                                              tai_ve_gia):
    client = FakeClient(image_job=job_anh(n=2))
    monkeypatch.setattr(sc, "tao_client", lambda **kw: client)
    w = _worker(tmp_path, nhat_ky)
    w.img_dir.mkdir(parents=True, exist_ok=True)
    (w.img_dir / "A.png").write_bytes(b"da co san")

    da_gop = w._shopapi_gop_anh_cung_prompt(
        [("p", w.img_dir / "A.png", None, w.aspect_ratio),
         ("p", w.img_dir / "B.png", None, w.aspect_ratio)])

    assert da_gop == 0, "con moi mot cai chua co -> gop cung nhu khong"
    assert client.so.image_calls == []
