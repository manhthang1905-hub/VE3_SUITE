"""Vòng phát việc phải quét hết danh sách mã trong tích tắc, không phải 16 phút.

Đo thật 12:57–13:06 ngày 15/08/2026: vòng bò **~9 giây một mã**, chín phút xét
được 82/109 mã — 27 mã cuối KHÔNG hề được nhìn tới. Hai mươi chỗ làm rơi hết
vào mã video nằm đầu danh sách, nên không một mã ảnh nào được phát việc
(`image-only: 0`, `me image lo: 0`). Nhìn từ giao diện thì đúng như người dùng
nói: "bên ảnh xử lý ít như kiểu có giới hạn gì đó".
"""

from __future__ import annotations

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


class _App:
    """Chỉ đủ để chạy `_get_project_state_cached`."""

    def __init__(self, gui, ep):
        self._ep = ep
        self._project_state_cache = {}
        self._project_state_cache_ttl = 30.0
        self._get_project_state_cached = \
            gui.VE3App._get_project_state_cached.__get__(self)
        self._project_excel_path = lambda pd: self._ep
        self._excel_is_locked = lambda ep: False
        self._log = lambda *a, **k: None


def test_file_KHONG_doi_thi_KHONG_doc_lai_du_da_lau(gui, tmp_path, monkeypatch):
    """Chữ ký `(mtime, size)` khớp = file không đổi = dữ liệu cũ vẫn đúng.

    Hạn 30 giây chồng lên trên không làm nó đúng hơn, chỉ bắt `openpyxl` mở
    lại một quyển 100+ cảnh — và đó là toàn bộ cái giá 9 giây/mã.
    """
    ep = tmp_path / "X_prompts.xlsx"
    ep.write_bytes(b"gia")
    a = _App(gui, ep)

    so_lan = {"n": 0}

    class _WB:
        def __init__(self, p):
            so_lan["n"] += 1

        def load_or_create(self): pass
        def get_scenes(self): return []
        def get_stats(self): return {}
        def get_processing_summary(self): return None
        def get_thumbnails(self): return []

    import types
    gia = types.ModuleType("modules.excel_manager")
    gia.PromptWorkbook = _WB
    monkeypatch.setitem(sys.modules, "modules.excel_manager", gia)

    a._get_project_state_cached(tmp_path)
    assert so_lan["n"] == 1

    # Giả vờ đã trôi qua rất lâu. File KHÔNG đổi -> không được đọc lại.
    for muc in a._project_state_cache.values():
        muc["ts"] = 0.0
    a._get_project_state_cached(tmp_path)
    assert so_lan["n"] == 1, (
        "doc lai du file khong doi -> vong phat viec bo 9 giay/ma, "
        "ma cuoi danh sach khong bao gio toi luot")


def test_file_DOI_thi_PHAI_doc_lai(gui, tmp_path, monkeypatch):
    """Worker vừa ghi Excel xong thì lượt sau phải thấy số mới.

    Nới quá tay ở đây là hàng chờ nhìn mãi một trạng thái cũ — mã đã xong vẫn
    được phát lại, mã vừa có việc thì không ai biết.
    """
    ep = tmp_path / "X_prompts.xlsx"
    ep.write_bytes(b"gia")
    a = _App(gui, ep)

    so_lan = {"n": 0}

    class _WB:
        def __init__(self, p):
            so_lan["n"] += 1

        def load_or_create(self): pass
        def get_scenes(self): return []
        def get_stats(self): return {}
        def get_processing_summary(self): return None
        def get_thumbnails(self): return []

    import types
    gia = types.ModuleType("modules.excel_manager")
    gia.PromptWorkbook = _WB
    monkeypatch.setitem(sys.modules, "modules.excel_manager", gia)

    a._get_project_state_cached(tmp_path)
    assert so_lan["n"] == 1

    ep.write_bytes(b"gia-da-doi-kich-thuoc")   # size đổi -> chữ ký đổi
    a._get_project_state_cached(tmp_path)
    assert so_lan["n"] == 2, "file da doi ma van tra ban cu -> hang cho nhin so lieu chet"
