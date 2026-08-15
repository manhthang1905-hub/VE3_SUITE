"""Kiểm HAI ĐIỂM CẮM trong `ve3_worker.py`.

Điều phải giữ đúng từng chi tiết:

* `_submit_image` trả **đúng 4** phần tử `(ok, media_name, sinfo, err)`
* `_submit_video` trả **đúng 3** phần tử `(ok, sinfo, err)`
* file media **thật sự** nằm ở `output_path`
* `vid/X.mp4` chỉ chạy khi có `img/X.png` cùng stem
* chưa có khoá → **lùi về đường cũ** và có ghi cảnh báo
"""

from __future__ import annotations

import base64

import pytest

from conftest import KHOA_GIA, FakeClient, job_anh, job_video

from ve3_worker import VE3Worker
from modules.google_flow_api import ImageInput, ImageInputType


CAU_HINH_API = {
    "generation_backend": "shopapi",     # VIDEO qua API
    "veo3top_image_mode": "shopapi",     # ANH qua API
    "flow_aspect_ratio": "portrait",
    "retry_count": 1,
    # Ghim so luong song song: khong thi worker se hoi GET /v1/me -> cham mang that.
    "max_concurrent": 2,
}


@pytest.fixture
def co_khoa(monkeypatch, sc):
    """Giả vờ máy đã có khoá, KHÔNG đọc khoá thật của người dùng."""
    monkeypatch.setattr(sc, "doc_khoa", lambda env=None: (KHOA_GIA, "kho khoa gia (bai kiem)"))
    return KHOA_GIA


@pytest.fixture
def khong_khoa(monkeypatch, sc):
    monkeypatch.setattr(sc, "doc_khoa", lambda env=None: ("", ""))


@pytest.fixture
def client_gia(monkeypatch, sc):
    """Mọi `tao_client` trả về client GIẢ -> không một byte nào ra mạng."""
    hop = {}

    def _dat(client):
        hop["client"] = client
        monkeypatch.setattr(sc, "tao_client", lambda **kw: client)
        return client

    return _dat


def _worker(tmp_path, nhat_ky, cau_hinh=None):
    du_an = tmp_path / "PROJECT"
    du_an.mkdir(exist_ok=True)
    cfg = dict(CAU_HINH_API)
    if cau_hinh:
        cfg.update(cau_hinh)
    return VE3Worker(project_dir=str(du_an), config=cfg, log_func=nhat_ky)


# ── ẢNH: đúng 4 phần tử ──────────────────────────────────────────────────────


def test_submit_image_tra_DUNG_4_phan_tu_va_ghi_file(tmp_path, nhat_ky, co_khoa,
                                                     client_gia, tai_ve_gia):
    client = client_gia(FakeClient(image_job=job_anh(n=1)))
    w = _worker(tmp_path, nhat_ky)
    assert w.use_shopapi_for_image is True

    dich = w.img_dir / "SC001.png"
    ket_qua = w._submit_image("mot canh dep", dich)

    assert len(ket_qua) == 4, "hop dong _submit_image la (ok, media_name, sinfo, err)"
    ok, media_name, sinfo, err = ket_qua
    assert ok is True and err == ""
    assert dich.exists() and dich.stat().st_size > 0
    assert sinfo["backend"] == "shopapi"
    assert media_name == "job_img_1"
    assert client.so.image_calls, "phai co goi len API"


def test_submit_image_ke_thua_ty_le_da_cau_hinh_cua_worker(tmp_path, nhat_ky, co_khoa,
                                                           client_gia, tai_ve_gia):
    client = client_gia(FakeClient(image_job=job_anh(n=1)))
    w = _worker(tmp_path, nhat_ky, {"flow_aspect_ratio": "portrait"})
    w._submit_image("x", w.img_dir / "A.png")
    assert client.so.image_calls[0]["aspect_ratio"] == "9:16"


def test_submit_image_ref_duoc_nhung_bytes_roi_upload_thanh_url(tmp_path, nhat_ky, co_khoa,
                                                                client_gia, tai_ve_gia):
    """Ref của tool là `ImageInput` mang mediaId của Flow — API KHÔNG hiểu mediaId."""
    client = client_gia(FakeClient(image_job=job_anh(n=1)))
    w = _worker(tmp_path, nhat_ky)

    # `_make_ref` nhúng base64 khi che do la "shopapi" (giong che do pool).
    w.nv_dir.mkdir(parents=True, exist_ok=True)
    (w.nv_dir / "NV01.png").write_bytes(b"\x89PNG-nhan-vat")
    ref = w._make_ref("NV01", "media/abc123")
    assert ref.base64_data, "_make_ref phai nhung bytes khi che do shopapi"

    ok, _, _, err = w._submit_image("x", w.img_dir / "B.png", refs=[ref])

    assert ok is True, err
    assert len(client.so.uploads) == 1
    assert client.so.uploads[0]["file"] == base64.b64decode(ref.base64_data)
    assert client.so.image_calls[0]["reference_images"][0].startswith("https://")


def test_submit_image_ref_khong_co_bytes_thi_bo_va_canh_bao(tmp_path, nhat_ky, co_khoa,
                                                            client_gia, tai_ve_gia):
    client = client_gia(FakeClient(image_job=job_anh(n=1)))
    w = _worker(tmp_path, nhat_ky)
    ref_rong = ImageInput(name="media/khong-co-bytes", input_type=ImageInputType.REFERENCE)

    ok, _, _, err = w._submit_image("x", w.img_dir / "C.png", refs=[ref_rong])

    assert ok is True, err
    assert client.so.uploads == []
    assert any(lv == "WARN" and "khong co bytes" in m for lv, m in nhat_ky.dong)


def test_submit_image_loi_van_tra_du_4_phan_tu(tmp_path, nhat_ky, co_khoa,
                                               client_gia, tai_ve_gia):
    lop = type("InsufficientBalanceError", (Exception,), {})
    client_gia(FakeClient(image_loi=lop("het tien")))
    w = _worker(tmp_path, nhat_ky)

    ket_qua = w._submit_image("x", w.img_dir / "D.png")

    assert len(ket_qua) == 4
    ok, media_name, sinfo, err = ket_qua
    assert ok is False and media_name is None
    assert "HET TIEN" in err


# ── VIDEO: đúng 3 phần tử ────────────────────────────────────────────────────


def test_submit_video_tra_DUNG_3_phan_tu_va_ghi_file(tmp_path, nhat_ky, co_khoa,
                                                     client_gia, tai_ve_gia):
    client = client_gia(FakeClient(video_job=job_video()))
    w = _worker(tmp_path, nhat_ky)
    assert w.use_shopapi_for_video is True

    # Ràng buộc TÊN: vid/SC001.mp4 phải có img/SC001.png cùng stem.
    w.img_dir.mkdir(parents=True, exist_ok=True)
    (w.img_dir / "SC001.png").write_bytes(b"\x89PNG-scene")
    dich = w.vid_dir / "SC001.mp4"

    ket_qua = w._submit_video("canh mua roi", dich, "media/khong-dung-den")

    assert len(ket_qua) == 3, "hop dong _submit_video la (ok, sinfo, err)"
    ok, sinfo, err = ket_qua
    assert ok is True and err == ""
    assert dich.exists() and dich.stat().st_size > 0
    assert sinfo["engine"] == "veo3" and sinfo["duration"] == 8


def test_submit_video_luon_gui_veo3_8_giay(tmp_path, nhat_ky, co_khoa, client_gia, tai_ve_gia):
    client = client_gia(FakeClient(video_job=job_video()))
    w = _worker(tmp_path, nhat_ky)
    w.img_dir.mkdir(parents=True, exist_ok=True)
    (w.img_dir / "SC002.png").write_bytes(b"\x89PNG")
    w._submit_video("x", w.vid_dir / "SC002.mp4", "")
    goi = client.so.video_calls[0]
    assert (goi["engine"], goi["duration"]) == ("veo3", 8)


def test_submit_video_thieu_anh_cung_stem_thi_bao_ro(tmp_path, nhat_ky, co_khoa,
                                                     client_gia, tai_ve_gia):
    client = client_gia(FakeClient(video_job=job_video()))
    w = _worker(tmp_path, nhat_ky)
    w.img_dir.mkdir(parents=True, exist_ok=True)   # co thu muc nhung KHONG co SC404.png

    ket_qua = w._submit_video("x", w.vid_dir / "SC404.mp4", "")

    assert len(ket_qua) == 3
    ok, _, err = ket_qua
    assert ok is False
    assert "khong thay anh scene" in err
    assert client.so.video_calls == []


def test_submit_video_dung_ty_le_video_da_cau_hinh(tmp_path, nhat_ky, co_khoa,
                                                   client_gia, tai_ve_gia):
    client = client_gia(FakeClient(video_job=job_video()))
    w = _worker(tmp_path, nhat_ky, {"flow_aspect_ratio": "portrait"})
    w.img_dir.mkdir(parents=True, exist_ok=True)
    (w.img_dir / "SC003.png").write_bytes(b"\x89PNG")
    w._submit_video("x", w.vid_dir / "SC003.mp4", "")
    assert client.so.video_calls[0]["aspect_ratio"] == "9:16"


# ── Chưa có khoá → LÙI VỀ ĐƯỜNG CŨ, có cảnh báo ─────────────────────────────


def test_chua_co_khoa_thi_lui_ve_duong_cu_va_canh_bao_TO(tmp_path, nhat_ky, khong_khoa):
    w = _worker(tmp_path, nhat_ky)

    # Da chon "shopapi" nhung khong co khoa -> co PHAI tat.
    assert w.veo3top_image_mode == "shopapi"
    assert w.generation_backend == "shopapi"
    assert w.use_shopapi_for_image is False
    assert w.use_shopapi_for_video is False

    van_ban = nhat_ky.text()
    assert "CHUA CO KHOA" in van_ban
    assert "LUI VE" in van_ban
    assert any(lv == "WARN" for lv, _ in nhat_ky.dong), "phai la muc WARN, khong duoc im lang"


def test_chua_co_khoa_thi_submit_image_KHONG_di_vao_nhanh_moi(tmp_path, nhat_ky, khong_khoa,
                                                              client_gia, tai_ve_gia):
    """Nhánh mới bị tắt -> `_submit_image` phải rơi xuống backend cũ (server pool)."""
    client = client_gia(FakeClient(image_job=job_anh(n=1)))
    w = _worker(tmp_path, nhat_ky)
    w.pool = None            # khong co server -> duong cu bao loi, KHONG goi API

    ok, media_name, sinfo, err = w._submit_image("x", w.img_dir / "E.png")

    assert ok is False
    assert client.so.image_calls == [], "khong co khoa ma van goi API la sai"
    assert "No server available" in err


def test_chua_co_khoa_thi_submit_video_KHONG_di_vao_nhanh_moi(tmp_path, nhat_ky, khong_khoa,
                                                              client_gia, tai_ve_gia):
    client = client_gia(FakeClient(video_job=job_video()))
    w = _worker(tmp_path, nhat_ky)
    w.pool = None

    ok, sinfo, err = w._submit_video("x", w.vid_dir / "F.mp4", "")

    assert ok is False
    assert client.so.video_calls == []
    assert "No server available" in err


# ── Backend cũ KHÔNG bị đụng vào ─────────────────────────────────────────────


def test_backend_cu_van_giu_nguyen_hanh_vi(tmp_path, nhat_ky, co_khoa, client_gia, tai_ve_gia):
    """Chọn veo3top_b_pool thì nhánh shopapi phải nằm im, kể cả khi CÓ khoá."""
    client = client_gia(FakeClient(image_job=job_anh(n=1), video_job=job_video()))
    w = _worker(tmp_path, nhat_ky, {"generation_backend": "veo3top_b_pool",
                                    "veo3top_image_mode": "pool"})
    assert w.use_shopapi_for_image is False
    assert w.use_shopapi_for_video is False
    assert w.use_veo3top_for_image is True


def test_gia_tri_backend_la_bi_ep_ve_mac_dinh_cu(tmp_path, nhat_ky, co_khoa):
    w = _worker(tmp_path, nhat_ky, {"generation_backend": "khong-ton-tai",
                                    "veo3top_image_mode": "khong-ton-tai"})
    assert w.generation_backend == "server"
    assert w.veo3top_image_mode == ""


def test_ca_anh_va_video_qua_api_thi_khoi_can_auth_chrome(tmp_path, nhat_ky, co_khoa):
    """Không mở Chrome lần nào -> không cần bearer token / project id của Flow."""
    w = _worker(tmp_path, nhat_ky)
    assert w._veo3top_only is True


def test_chi_anh_qua_api_thi_VAN_can_auth_cho_video(tmp_path, nhat_ky, co_khoa):
    w = _worker(tmp_path, nhat_ky, {"generation_backend": "server"})
    assert w.use_shopapi_for_image is True
    assert w.use_shopapi_for_video is False
    assert w._veo3top_only is False


# ── Trần song song đọc từ /v1/me, KHÔNG gõ cứng ──────────────────────────────


def test_tran_song_song_lay_tu_v1_me_khong_go_cung(tmp_path, nhat_ky, co_khoa, monkeypatch, sc):
    """Số luồng đọc từ `/v1/me` — và đọc LẠI mỗi pha, không đông cứng lúc khởi động.

    ⚠ Bài này trước đây khẳng định `w.max_concurrent == 7`, tức là hỏi một lần
    lúc khởi động rồi giữ nguyên cả lượt chạy. Đó chính là chỗ tự bóp mình: đọc
    trúng lúc đông khách được 2 thì bốn tiếng sau vẫn chạy 2, dù nhà máy đã rỗng
    từ lâu. Nay `max_concurrent` chỉ còn là **trần của người dùng** (không ghim →
    trần CỨNG), còn con số thật lấy ở `_shopapi_luong` ngay trước mỗi mẻ.
    """
    monkeypatch.setattr(sc, "tran_song_song",
                        lambda loai, api_key=None, mac_dinh=1, client=None: 7)
    w = _worker(tmp_path, nhat_ky, {"max_concurrent": 0})
    # KHONG go cung con so: may chu nang tran la `tran_cung` an theo ngay
    # (da lech mot lan - anh 128 -> 384). Doi chieu voi nguon that.
    assert w.max_concurrent == sc.tran_cung("image"), (
        "khong ghim -> tran nguoi dung = tran CUNG cua anh")
    assert w._shopapi_luong("image", w.max_concurrent) == 7, "so that phai den tu /v1/me"


def test_nguoi_dung_ghim_so_thi_ton_trong_khong_hoi_may_chu(tmp_path, nhat_ky, co_khoa,
                                                            monkeypatch, sc):
    def _khong_duoc_goi(*a, **kw):
        raise AssertionError("khoi dong KHONG duoc hoi /v1/me nua")
    monkeypatch.setattr(sc, "tran_song_song", _khong_duoc_goi)
    w = _worker(tmp_path, nhat_ky, {"max_concurrent": 3})
    assert w.max_concurrent == 3


def test_tran_bang_0_thi_KHONG_chay_lieu_1_job(tmp_path, nhat_ky, co_khoa,
                                               monkeypatch, sc):
    """`0` = nhà máy loại đó ĐANG DỪNG → phải CHỜ rồi hỏi lại, không phải chạy 1 job.

    Bản trước trả `1` ở đây, nghĩa là gửi một job vào một nhà máy đã đóng cửa:
    chắc chắn ăn `503`, rồi tool ghi "lỗi" cho một việc hoàn toàn không có lỗi.
    """
    monkeypatch.setattr(sc, "tran_song_song",
                        lambda loai, api_key=None, mac_dinh=1, client=None: 0)
    w = _worker(tmp_path, nhat_ky, {"max_concurrent": 0})
    monkeypatch.setattr(w, "_sleep_with_stop", lambda giay: True)   # ngu gia, khong cho that

    assert w._shopapi_luong("image", w.max_concurrent) == 0, \
        "nha may dung ma van tra >0 la du 1 job cung sai"
    assert any(lv == "WARN" and "DANG DUNG" in m for lv, m in nhat_ky.dong)


def test_hoi_v1_me_that_bai_thi_khong_lam_chet_luot_chay(tmp_path, nhat_ky, co_khoa,
                                                         monkeypatch, sc):
    def _no(*a, **kw):
        raise RuntimeError("mat mang")
    monkeypatch.setattr(sc, "tran_song_song", _no)
    w = _worker(tmp_path, nhat_ky, {"max_concurrent": 0})
    assert w.max_concurrent >= 1
    assert w._shopapi_luong("image", w.max_concurrent) >= 1, "mat mang -> doan thap, khong dung im"


# ── Không có server Chrome nào vẫn phải chạy được ────────────────────────────


def test_khong_co_server_url_van_chay_duoc_khi_anh_di_shopapi(tmp_path, nhat_ky, co_khoa):
    """Đi toàn API thì KHÔNG bước nào chạm tới một server Chrome nào.

    ⚠ `use_veo3top_for_image` KHÔNG bao gồm shopapi — nó chỉ là
    `("blank", "account", "pool")`. Chốt chặn "Khong co server URL" trong
    `run` tha `use_veo3top_for_image` mà quên vế shopapi, nên chạy
    toàn API vẫn chết ngay ở cửa dù không cần server nào.

    Đây là lớp chặn THỨ BA cùng một kiểu (sau cổng token và cổng "Thieu server"
    bên GUI) — sửa GUI mà quên chỗ này thì chỉ đẩy lỗi xuống sâu hơn một bước.
    """
    w = _worker(tmp_path, nhat_ky, {"local_server_url": "", "local_server_list": []})

    assert w.use_shopapi_for_image is True
    assert w.use_veo3top_for_image is False, "shopapi KHONG nam trong nhom veo3top"
    assert w.pool is None, "khong cau hinh server thi khong co pool - dung nhu vay"

    ket = w.run()
    assert "Khong co server URL" not in " ".join(ket.get("errors") or []), (
        "chan oan: di toan API thi khong can server Chrome nao")


def test_thieu_khoa_thi_VAN_doi_server_nhu_cu(tmp_path, nhat_ky, khong_khoa):
    """Thiếu khoá -> worker lùi về đường cũ, mà đường cũ THẬT SỰ cần server."""
    w = _worker(tmp_path, nhat_ky, {"local_server_url": "", "local_server_list": []})

    assert w.use_shopapi_for_image is False, "thieu khoa -> phai lui ve duong cu"
    ket = w.run()
    assert "Khong co server URL" in " ".join(ket.get("errors") or [])


# ── Ref nhân vật: shopapi cũng embed từ file local ───────────────────────────


def test_ref_local_duoc_chap_nhan_khi_di_shopapi(tmp_path, nhat_ky, co_khoa):
    """CHẠY THẬT 07/08/2026: `missing refs -> nv1` cho CẢ 147 scene, `Anh: 0/147`.

    `_pool_ref_local` sinh ra để chữa đúng lỗi "0 ảnh" này cho `pool`, nhưng lại
    khoá cứng đúng chữ `pool`. Nhánh shopapi cũng bỏ qua media_id (API KHÔNG
    hiểu mediaId của Flow — ref đi bằng bytes) nên rơi vào y hệt cái bẫy.

    `_make_ref` đã liệt kê cả hai chế độ từ đầu; chỉ mỗi hàm này bị bỏ sót.
    """
    w = _worker(tmp_path, nhat_ky)
    w.nv_dir.mkdir(parents=True, exist_ok=True)
    (w.nv_dir / "nv1.png").write_bytes(b"\x89PNG-nhan-vat")

    assert w._pool_ref_local("nv1") is True, (
        "co file nv/nv1.png ma van bao thieu -> ca me anh se hong sach")


def test_khong_co_file_thi_van_bao_thieu(tmp_path, nhat_ky, co_khoa):
    w = _worker(tmp_path, nhat_ky)
    w.nv_dir.mkdir(parents=True, exist_ok=True)
    assert w._pool_ref_local("nv_khong_ton_tai") is False


def test_hai_ham_ref_phai_dong_y_voi_nhau(tmp_path, nhat_ky, co_khoa):
    """`_make_ref` nhúng bytes cho chế độ nào thì `_pool_ref_local` phải nhận
    chế độ đó — lệch nhau chính là cách sinh ra `missing refs` oan."""
    w = _worker(tmp_path, nhat_ky)
    w.nv_dir.mkdir(parents=True, exist_ok=True)
    (w.nv_dir / "nv1.png").write_bytes(b"\x89PNG-nhan-vat")

    ref = w._make_ref("nv1", "")
    assert ref.base64_data, "_make_ref phai nhung bytes o che do shopapi"
    assert w._pool_ref_local("nv1") is True, "nhung duoc bytes thi PHAI coi la co ref"


# ── Viết lại prompt vi phạm policy: shopapi phải là nguồn ĐẦU TIÊN ───────────
#
# ĐO THẬT 07/08/2026 (TL1-0742): 145/147 anh ra ngon, hai canh chet vi bo loc:
#     Scene 4: prompt co dau hieu vi pham policy, thu viet lai (vong 1/3)
#     Scene 4: khong viet lai duoc prompt hop le
#     Scene 4 FAIL [failed: TERMINAL policy]
# May NHAN DIEN policy chay dung, nhung khau VIET LAI khong co nguon nao dung
# duoc: danh sach chi co VOV / Claude Pool / DeepSeek / claude.exe. Chay toan
# API thi DeepSeek het khoa, claude.exe khong cai -> ba vong deu truot.


def _thu_tu_provider(w):
    """Tên các provider `_call_rewrite_llm` thử, theo đúng thứ tự."""
    ten = []
    for n in ("_call_shopapi_rewrite", "_call_claude_cli_rewrite", "_call_vov_direct_rewrite",
              "_call_claude_pool_rewrite", "_call_deepseek_rewrite"):
        setattr(w, n, (lambda nn: (lambda *a, **k: (ten.append(nn), None)[1]))(n))
    w._call_rewrite_llm("viet lai giup")
    return ten


def test_shopapi_dung_DAU_khi_chay_toan_api(tmp_path, nhat_ky, co_khoa):
    """Luc nay no la nguon DUY NHAT chac chan dung duoc — tool vua goi no hang
    tram lan bang dung khoa do de tao chinh may tam anh nay."""
    w = _worker(tmp_path, nhat_ky, {"excel_engine": "claude_cli"})
    assert _thu_tu_provider(w)[0] == "_call_shopapi_rewrite"


def test_thieu_khoa_thi_KHONG_chen_shopapi_len_dau(tmp_path, nhat_ky, khong_khoa):
    """Không khoá thì nó chắc chắn trả None — đẩy lên đầu chỉ tổ phí một vòng."""
    w = _worker(tmp_path, nhat_ky, {"excel_engine": "claude_cli"})
    assert _thu_tu_provider(w)[0] != "_call_shopapi_rewrite"


def test_khong_co_khoa_thi_tra_None_de_di_tiep_provider_sau(tmp_path, nhat_ky, khong_khoa):
    """Giao kèo của mọi provider: thiếu cấu hình -> `None`, KHÔNG ném lỗi."""
    w = _worker(tmp_path, nhat_ky)
    assert w._call_shopapi_rewrite("bat ky") is None


def test_mot_model_nghen_thi_doi_model_con_lai(tmp_path, nhat_ky, co_khoa, monkeypatch):
    import requests as _rq
    da = []

    class _R:
        def __init__(self, ma, noi_dung=""):
            self.status_code = ma
            self._nd = noi_dung

        def json(self):
            return {"choices": [{"message": {"content": self._nd}}]}

    def post(url, headers=None, json=None, timeout=None):
        m = (json or {})["model"]
        da.append(m)
        return _R(503) if m == "claude-sonnet-5" else _R(200, "cau da viet lai")

    monkeypatch.setattr(_rq, "post", post)
    w = _worker(tmp_path, nhat_ky)
    assert w._call_shopapi_rewrite("viet lai") == "cau da viet lai"
    assert da == ["claude-sonnet-5", "claude-opus-5"], da


def test_nhan_ca_ma_201(tmp_path, nhat_ky, co_khoa, monkeypatch):
    """api.shopapi.vn tung tra 201 — bam dung 200 la vut ket qua hop le."""
    import requests as _rq

    class _R:
        status_code = 201

        def json(self):
            return {"choices": [{"message": {"content": "  cau   moi  "}}]}

    monkeypatch.setattr(_rq, "post", lambda *a, **k: _R())
    w = _worker(tmp_path, nhat_ky)
    assert w._call_shopapi_rewrite("viet lai") == "cau moi"


# ── Nhà máy nghẽn KHÔNG phải prompt hỏng ─────────────────────────────────────


def test_nhan_ra_loi_do_NGHEN_chu_khong_phai_prompt(tmp_path, nhat_ky, co_khoa):
    """Viết lại prompt để né bộ lọc là đúng — nhưng chỉ khi prompt thật sự bị chặn."""
    w = _worker(tmp_path, nhat_ky, {})
    for cau in ("May chu bao qua tai (429 / resource_exhausted)",
                "EngineUnavailableError: Nha may anh hien khong co cho nao nhan viec",
                "503 service unavailable",
                "Nhà máy đang dừng, không có máy xử lý nào online",
                "rate limit exceeded"):
        assert w._loi_do_nghen(cau), "khong nhan ra nghen: {0}".format(cau)
    for cau in ("content_rejected: prompt vi pham chinh sach",
                "PUBLIC_ERROR_UNSAFE_GENERATION",
                "prompt contains prohibited content",
                ""):
        assert not w._loi_do_nghen(cau), "nham loi noi dung thanh nghen: {0}".format(cau)


def test_NGHEN_thi_KHONG_chay_last_resort_prompt():
    """Một cú nghẹn thoáng qua của nhà máy không được thành 'lượt trắng'.

    Đã dính thật lúc 17:17:02 ngày 15/08/2026, mã TH1-0182:

        me video lo 1 -> ban them 1 job | tran may chu 172
        Video scene 81: thu last-resort prompt de tranh fail
        Video scene 81 FAIL (0.0s) [error: retry lt sau]
        KET QUA: CO LOI - Video: 0/1
        TH1-0182: lượt trắng 1/3

    Cùng giây đó mã TH1-0097 nhận đúng lỗi ấy nhưng xử lý đúng: "nha may DANG
    DUNG (503) -> cho 30s roi tham do lai". Ba lượt trắng là mã bị ĐỖ LẠI, dù
    nó chẳng có gì sai.

    Vòng viết lại prompt ở trên đã kiểm `_is_policy_violation_error`; khối
    last-resort thì quên — nên `503` cũng kéo nó chạy, tốn thêm một lượt gửi
    nữa (cũng `503`) rồi ghi scene là hỏng.
    """
    import ast, inspect
    import ve3_worker
    nguon = inspect.getsource(ve3_worker)
    cay = ast.parse(nguon)
    for node in ast.walk(cay):
        if isinstance(node, ast.FunctionDef) and node.name == "_fail_status_for":
            break
    i = nguon.find("video_last_resort_enabled")
    assert i > 0, "khong tim thay khoi last-resort"
    quanh = nguon[max(0, i - 300):i + 300]
    assert "_loi_do_nghen(error_text)" in quanh, (
        "khoi last-resort van chay ke ca khi nha may nghen -> bien 503 thanh "
        "scene hong va an mot luot trang")


def test_di_TOAN_API_thi_khong_bao_ERROR_thieu_server(tmp_path, nhat_ky, co_khoa):
    """`ServerPool` là đường Chrome/VM — đi API thì không có server, và đó là ĐÚNG.

    Log 17:16–17:17 ngày 15/08/2026 có `ERROR Khong co server URL!` ở CẢ SÁU mã
    đang chạy tốt. ERROR giả làm hỏng đúng thứ log sinh ra để làm: người đọc
    quét tìm ERROR, thấy nó ở mọi mã, rồi thôi không tin dòng ERROR nào nữa.
    """
    import inspect
    import ve3_worker
    nguon = inspect.getsource(ve3_worker)
    i = nguon.find('"Khong co server URL!"')
    assert i > 0, "khong tim thay dong ERROR"
    truoc = nguon[max(0, i - 1500):i]
    assert "use_shopapi_for_image and self.use_shopapi_for_video" in truoc, (
        "che do toan API van roi vao nhanh ERROR 'Khong co server URL!'")


# ── Cảnh hỏng phải TỰ NÓI VÌ SAO ─────────────────────────────────────────────


def test_dong_FAIL_phai_IN_CA_LY_DO():
    """Một cảnh hỏng trong 0,0 giây mà log không nói vì sao là ngõ cụt chẩn đoán.

    Log 17:17:02 và 17:30:51 ngày 15/08/2026, mã TH1-0182:

        Video scene 81 FAIL (0.0s) [error: retry lt sau]

    Câu lỗi đã nằm sẵn trong `error_text` — chỉ là không ai đưa nó ra. Mất hai
    lượt chạy và một vòng đọc mã nguồn mới biết đó là "khong thay anh scene".
    """
    import inspect
    import ve3_worker
    nguon = inspect.getsource(ve3_worker)
    for cho in ("Video scene {0} -> FAIL", "Scene {0} -> FAIL"):
        i = nguon.find(cho)
        assert i > 0, "khong tim thay dong FAIL: {0}".format(cho)
        assert "error_text" in nguon[i:i + 400], (
            "dong FAIL khong in ly do: {0}".format(cho))


def test_THIEU_ANH_NGUON_thi_danh_dau_dung_lai_ANH():
    """Thiếu file ảnh nguồn thì phải dựng lại ẢNH, không phải thử lại VIDEO mãi.

    Excel ghi ảnh "done" nhưng file trên đĩa đã mất, nên bước video đọc
    `img_path` không thấy và trả về ngay trong 0,0 giây. Lượt sau lặp lại y hệt
    vì không ai bảo pha ảnh làm lại cảnh đó — ba lượt là mã bị ĐỖ LẠI vĩnh
    viễn, trong khi chỉ cần dựng lại một tấm ảnh.
    """
    import inspect
    import ve3_worker
    nguon = inspect.getsource(ve3_worker)
    i = nguon.find("_thieu_anh = self._anh_nguon_hong(error_text)")
    assert i > 0, "khong nhan ra truong hop anh nguon hong"
    quanh = nguon[i:i + 2600]
    assert 'status_img="error"' in quanh, (
        "anh hong ma khong danh dau dung lai ANH -> ma se hong lai y het luot sau")
    assert 'status_vid=""' in quanh, (
        "van ghi video la hong -> luot sau bo qua canh nay thay vi lam lai")
    assert ".unlink()" in quanh, (
        "khong xoa file hong -> pha ANH thay file con do va bo qua, vong lap van kin")


def test_nhan_ra_CA_HAI_kieu_anh_nguon_hong(tmp_path, nhat_ky, co_khoa):
    """Hai kiểu đã gặp thật, cùng một mã TH1-0182 cảnh 81.

    Thiếu hẳn file, và file còn đó nhưng RUỘT KHÔNG PHẢI ẢNH. Cả hai đều bất
    biến qua các lượt chạy — cảnh 81 hỏng y hệt ở 17:17:02, 17:30:51 và
    17:43:39 ngày 15/08/2026 — nên thử lại VIDEO là vô ích, phải dựng lại ẢNH.
    """
    w = _worker(tmp_path, nhat_ky, {})
    assert w._anh_nguon_hong("shopapi-vid: khong thay anh scene D:/x/img/81.png")
    assert w._anh_nguon_hong(
        "shopapi-vid: upload anh scene that bai: InvalidRequestError: duoi file la "
        '".png" nhung noi dung ben trong khong phai PNG, JPEG hay WebP.')
    # KHÔNG được nuốt hai loại lỗi khác — chúng có cách chữa hoàn toàn khác.
    assert not w._anh_nguon_hong("content_rejected: prompt vi pham chinh sach")
    assert not w._anh_nguon_hong("may chu bao qua tai (429 / resource_exhausted)")
    assert not w._anh_nguon_hong("")


def test_xoa_anh_hong_o_CA_img_backup():
    """Xoá mỗi `img/` là vô ích — finalize chép bản hỏng từ `img_backup/` trở lại.

    Log 18:02:24 ngày 15/08/2026, hai giây sau khi xoá:

        Video scene 81: da xoa anh nguon hong 81.png
        Finalize: 0 mp4 + 1 png img/ (tong 148 files)

    Tấm ảnh hỏng đã về chỗ cũ, và lượt sau lặp lại y hệt.
    """
    import inspect
    import ve3_worker
    nguon = inspect.getsource(ve3_worker)
    i = nguon.find("_thieu_anh = self._anh_nguon_hong(error_text)")
    assert i > 0
    quanh = nguon[i:i + 1600]
    assert "img_backup" in quanh, (
        "chi xoa o img/ -> finalize chep ban hong tro lai, vong lap van kin")


def test_la_anh_that_doc_MAGIC_BYTES_khong_nhin_duoi_ten(tmp_path):
    """Máy chủ nhận dạng ảnh bằng magic bytes — ta phải kiểm bằng CÙNG MỘT THƯỚC.

    Không thì tool nghĩ ảnh xong, máy chủ nghĩ ảnh hỏng, và không bên nào sai
    theo cách của mình.
    """
    import ve3_worker
    f = ve3_worker.VE3Worker._la_anh_that
    (tmp_path / "a.png").write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 20)
    (tmp_path / "b.png").write_bytes(b"\xff\xd8\xff" + b"\x00" * 20)          # JPEG
    (tmp_path / "c.png").write_bytes(b"RIFF" + b"\x00" * 4 + b"WEBP" + b"\x00" * 10)
    assert f(tmp_path / "a.png") and f(tmp_path / "b.png") and f(tmp_path / "c.png")
    # Ba kiểu "file .png mà không phải ảnh" đã gặp hoặc dễ gặp.
    (tmp_path / "x.png").write_bytes(b"<html>loi 500</html>")
    (tmp_path / "y.png").write_bytes(b"")
    (tmp_path / "z.png").write_bytes(b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 10)
    assert not f(tmp_path / "x.png"), "trang loi HTML luu thanh .png ma van qua cua"
    assert not f(tmp_path / "y.png"), "file rong ma van qua cua"
    assert not f(tmp_path / "z.png"), "mp4 doi ten thanh .png ma van qua cua"
    assert not f(tmp_path / "khong-co.png")


def test_pha_ANH_khong_danh_dau_DONE_cho_file_khong_phai_anh():
    """`img_path.exists()` không đủ — và nó GHI ĐÈ dấu `error` của pha video.

    Vòng lặp kín đã đo thật, TH1-0182 cảnh 81 hỏng BỐN lượt liên tiếp lúc
    17:17, 17:30, 17:43 và 18:02 ngày 15/08/2026:

        pha ảnh  : có file          -> đánh dấu "done"
        pha video: máy chủ từ chối  -> FAIL, đánh dấu "error"
        lượt sau : y hệt
    """
    import inspect
    import ve3_worker
    nguon = inspect.getsource(ve3_worker)
    i = nguon.find("if img_path.exists() and (media_id or not self._can_media_id_canh()):")
    assert i > 0, "khong tim thay nhanh danh dau done"
    quanh = nguon[i:i + 700]
    assert "_la_anh_that" in quanh, (
        "van danh dau 'done' chi vi file ton tai -> ghi de dau 'error' cua pha video")
    assert ".unlink()" in quanh, "phat hien file hong ma khong xoa -> luot sau lai qua cua"


# ── Excel nói "done" mà đĩa không có ảnh ─────────────────────────────────────


def _w_dia(tmp_path):
    import ve3_worker

    class _W:
        img_dir = tmp_path / "img"
        project_dir = tmp_path
        _la_anh_that = staticmethod(ve3_worker.VE3Worker._la_anh_that)
        _anh_scene_con_dung_duoc = ve3_worker.VE3Worker._anh_scene_con_dung_duoc

    (tmp_path / "img").mkdir(exist_ok=True)
    (tmp_path / "img_backup").mkdir(exist_ok=True)
    return _W()


PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 30


def test_video_da_dung_xong_thi_KHONG_coi_la_thieu_anh(tmp_path):
    """`_finalize_img` xoá png gốc khỏi `img/` sau khi ghép video.

    Bỏ sót nhánh này là dựng lại hàng trăm tấm ảnh ĐÃ CÓ VIDEO — vừa tốn tiền
    vừa phá việc đã xong. Đây là lý do nhánh "done" tồn tại ngay từ đầu.
    """
    w = _w_dia(tmp_path)
    (tmp_path / "img" / "7.mp4").write_bytes(b"\x00\x00\x00 ftypisom")
    assert w._anh_scene_con_dung_duoc(7) is True


def test_chi_con_ban_luu_trong_img_backup_van_tinh(tmp_path):
    w = _w_dia(tmp_path)
    (tmp_path / "img_backup" / "8.png").write_bytes(PNG)
    assert w._anh_scene_con_dung_duoc(8) is True


def test_khong_co_gi_o_ca_BA_noi_moi_la_thieu(tmp_path):
    w = _w_dia(tmp_path)
    assert w._anh_scene_con_dung_duoc(9) is False


def test_file_rac_khong_tinh_la_co_anh(tmp_path):
    w = _w_dia(tmp_path)
    (tmp_path / "img" / "10.png").write_bytes(b"<html>loi 500</html>")
    assert w._anh_scene_con_dung_duoc(10) is False


def test_nhanh_DONE_phai_duoc_DIA_xac_nhan():
    """Hai pha nhìn hai nguồn khác nhau thì cảnh đó chết vĩnh viễn.

    Bắt được nguyên văn ở TH1-0104 lúc 01:38:23 ngày 15/08/2026:

        PHASE 3 (nhìn Excel):  Scenes can tao: 0/131
        PHASE 4 (nhìn đĩa)  :  Skip scene 111: chua co anh hoac media_id

    Pha ảnh bảo không thiếu gì, pha video bảo thiếu ảnh, và không ai dựng lại.
    Phép kiểm magic-bytes thêm trước đó nằm BÊN DƯỚI nhánh `done` nên không bao
    giờ tới lượt.
    """
    import inspect
    import ve3_worker
    nguon = inspect.getsource(ve3_worker._generate_scenes) \
        if hasattr(ve3_worker, "_generate_scenes") \
        else inspect.getsource(ve3_worker.VE3Worker._generate_scenes)
    i = nguon.find('status_img.lower() == "done"')
    assert i > 0, "khong tim thay nhanh done"
    sau = nguon[i:i + 500]
    assert "_anh_scene_con_dung_duoc" in sau, (
        "nhanh 'done' van tin Excel tuyet doi -> canh mat anh chet vinh vien")
    assert "pending.append(scene)" in sau, "phat hien thieu anh ma khong dua vao hang dung lai"


# ── media_id của CẢNH: ai thật sự cần? ───────────────────────────────────────


def test_shopapi_KHONG_bat_buoc_media_id_cua_canh(tmp_path, nhat_ky, co_khoa):
    """Đi API thì `media_id` của cảnh là ô trống KHÔNG AI ĐỌC.

    Ba chỗ có thể dùng tới nó, không chỗ nào dùng:

    * `_submit_video_shopapi` chỉ nhận đường dẫn `img/<id>.png` rồi tự upload.
    * `_load_media_ids` chỉ đọc trang NHÂN VẬT (`wb.get_characters()`).
    * `_make_ref` nhúng base64 từ `nv/<tên>.png` vì API không hiểu `mediaId`.
    """
    w = _worker(tmp_path, nhat_ky)
    assert w._can_media_id_canh() is False


def test_che_do_flow_cu_VAN_bat_buoc_media_id(tmp_path, nhat_ky, co_khoa):
    """Flow/Chrome đi Image-to-Video bằng `mediaId`, thiếu mã là cảnh vô dụng
    thật — nới ở đây là hỏng chế độ cũ."""
    w = _worker(tmp_path, nhat_ky, {"generation_backend": "flow_api",
                                    "veo3top_image_mode": "flow"})
    assert w._can_media_id_canh() is True


def test_pha_anh_va_pha_video_HOI_CUNG_MOT_HAM(tmp_path, nhat_ky, co_khoa):
    """Đóng đinh cái đã sinh ra lỗi: hai pha tự viết điều kiện riêng rồi lệch.

    Pha 4 (video) tha `media_id` cho shopapi từ lâu; pha 3 (ảnh) vẫn bắt. Kết
    quả đo thật 15/08/2026 lúc 10:28: TH2-0139 dựng lại đúng 12 cảnh (47–58) đã
    có ảnh trên đĩa, TH2-0162 cũng đúng 12 — mỗi cảnh là 100₫ và một suất thợ
    lấy về một ô Excel chẳng ai đọc.
    """
    import inspect

    for ham in (VE3Worker._generate_scenes, VE3Worker._generate_videos):
        nguon = inspect.getsource(ham)
        assert "_can_media_id_canh" in nguon, (
            f"{ham.__name__} khong hoi ham chung -> hai pha se lech lai")
        assert 'generation_backend != "veo3top_b_pool"' not in nguon, (
            f"{ham.__name__} tu che lai dieu kien thay vi hoi ham chung")


def test_co_anh_tren_dia_thi_KHONG_dung_lai_du_thieu_media_id(tmp_path, nhat_ky, co_khoa):
    """Cửa pha 3 phải mở cho ảnh đã có, khi mã không bắt buộc."""
    import inspect

    nguon = inspect.getsource(VE3Worker._generate_scenes)
    assert "img_path.exists() and (media_id or not self._can_media_id_canh())" in nguon, (
        "cua pha 3 van doi media_id -> moi luot chay lai dung het anh cu")
