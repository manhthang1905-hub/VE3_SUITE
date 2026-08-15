"""Đi toàn API thì hàng chờ phải TÁCH TRẠM ảnh / video.

Đo thật 15/08/2026: máy khác chạy nhiều mã cùng lúc mà ảnh chỉ ra ~3 cái/phút,
trong khi mỗi mã còn cả chục ảnh chưa làm và nhà máy ảnh đang rảnh. Nguyên do
không nằm ở tốc độ dựng ảnh (~50 giây/ảnh, vẫn bình thường) mà ở chỗ **không
còn chỗ làm nào để phát việc ảnh**: mọi mã vào hàng chờ ở chế độ `all`, làm ảnh
xong thì làm tiếp video NGAY TRONG chỗ làm đó, mà video thì ~580 giây một cái
và mỗi mã 80–100 cái.
"""

from __future__ import annotations

import inspect
import sys
from pathlib import Path

import pytest

SUITE_ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def gui():
    for p in (str(SUITE_ROOT / "tools" / "ve3"), str(SUITE_ROOT / "veo3top_engine")):
        if p not in sys.path:
            sys.path.insert(0, p)
    import ve3_gui
    return ve3_gui


# ── Cửa tách trạm ────────────────────────────────────────────────────────────


def test_tach_tram_KHONG_khoa_cung_vao_rieng_backend_pool(gui):
    """Cửa từng viết `if _pool_mode:` nên chế độ API không bao giờ với tới."""
    nguon = inspect.getsource(gui.VE3App._queue_ve3_loop)
    i = nguon.find('_run_mode = "all"; _stage = "all"')
    assert i > 0, "khong tim thay cho chon tram"
    cua = nguon[i:i + 400]
    assert "che_do_toan_api(cfg)" in cua, (
        "di API van vao voi _stage='all' -> ma video giu cho lam hang gio, "
        "ma can anh dung ngoai voi no_free_pair")


def test_stage_all_KHONG_duoc_coi_la_ro_rieng(gui):
    """`_so_ma_song_song_shopapi` chỉ tách rổ cho "image"/"video".

    Đây là lý do cái chặn `max_codes` tách trạm nhìn thì đúng mà chạy thì
    không: nó luôn được truyền `"all"`.
    """
    nguon = inspect.getsource(gui.VE3App._so_ma_song_song_shopapi)
    assert 'pha in ("image", "video")' in nguon
    assert '"all"' not in nguon, "coi 'all' la mot pha rieng thi ro chung khong con y nghia"


# ── Rổ chỗ làm phải đủ cho CẢ HAI trạm ───────────────────────────────────────


class _App:
    """Bản tối giản của `VE3App` — chỉ đủ để chạy hai hàm đang kiểm."""

    MA_SONG_SONG_TOI_DA = 24
    MA_MOI_PHA_TOI_THIEU = 4

    def __init__(self, gui, tran_anh, tran_video, dat_tay=8):
        self._gui = gui
        self._tran = {"image": tran_anh, "video": tran_video}
        self.config_data = {"veo3top_image_mode": "shopapi",
                            "generation_backend": "shopapi",
                            "shopapi_ma_song_song": dat_tay}

    _so_ma_song_song_shopapi = None   # gán ở fixture
    _so_cho_lam_ao = None


@pytest.fixture
def app(gui, monkeypatch):
    def _tao(tran_anh, tran_video, dat_tay=8):
        a = _App(gui, tran_anh, tran_video, dat_tay)
        import shopapi_common as sc
        monkeypatch.setattr(sc, "tran_song_song",
                            lambda loai, mac_dinh=0, **kw: a._tran.get(loai, mac_dinh))
        a._so_ma_song_song_shopapi = gui.VE3App._so_ma_song_song_shopapi.__get__(a)
        a._so_cho_lam_ao = gui.VE3App._so_cho_lam_ao.__get__(a)
        return a
    return _tao


def test_ro_cho_lam_du_cho_ca_hai_tram(app):
    """Hai trạm cùng đầy thì rổ chung phải chứa nổi.

    Rổ hẹp hơn tổng hai trạm là kiểu hỏng câm nhất: cửa `max_codes` của trạm
    ảnh vẫn báo còn chỗ, mà mã vẫn bị đá ra ở cửa ngay sau đó với lý do
    `no_free_pair` — không nhắc gì tới trạm nào.
    """
    a = app(tran_anh=1536, tran_video=832)
    ro_anh = a._so_ma_song_song_shopapi("image")
    ro_video = a._so_ma_song_song_shopapi("video")
    assert a._so_cho_lam_ao() >= ro_anh + ro_video, (
        f"ro cho lam {a._so_cho_lam_ao()} < {ro_anh}+{ro_video} -> tram nao chay "
        "truoc vet sach, tram kia dung ngoai")


def test_nha_may_hep_van_giu_du_san_cu(app):
    """Trần máy chủ tụt thì rổ không được hẹp hơn mức tổng cũ."""
    a = app(tran_anh=60, tran_video=20, dat_tay=8)
    assert a._so_cho_lam_ao() >= 8


def test_moi_tram_luon_co_it_nhat_vai_cho(app):
    """Nhà máy ảnh hẹp cũng không được bóp trạm ảnh về 0 — việc ảnh sẽ tồn mãi."""
    a = app(tran_anh=1, tran_video=832)
    assert a._so_ma_song_song_shopapi("image") >= _App.MA_MOI_PHA_TOI_THIEU


def test_khong_tram_nao_nhan_thi_chay_nguyen_khoi_chu_KHONG_bo_qua(gui):
    """Mã còn việc mà không trạm nào nhận thì phải chạy, không được biến mất.

    Hai hàm dò trạm đều nuốt lỗi rồi trả `False`. Nếu `_project_pending_img_vid`
    đọc Excel hỏng, mã đó rơi vào khe giữa hai trạm — và nếu cửa này `continue`
    thì nó không bao giờ chạy nữa, không một dòng nào nói vì sao. Đúng kiểu
    hỏng câm mà cả dự án này đi chữa.
    """
    nguon = inspect.getsource(gui.VE3App._queue_ve3_loop)
    i = nguon.find('_run_mode = "all"; _stage = "all"')
    cua = nguon[i:i + 1200]
    j = cua.find('elif self._project_needs_video(pd)')
    assert j > 0
    sau = cua[j:]
    assert 'elif _pool_mode:' in sau, (
        "nhanh bo qua 'no_stage' con ap cho ca che do API -> ma cam lang bien mat")
