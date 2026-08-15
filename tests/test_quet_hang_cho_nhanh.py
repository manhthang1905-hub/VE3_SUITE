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


# ── Vòng quét phải TỰ NÓI nó mất bao lâu ─────────────────────────────────────


class _AppBao:
    def __init__(self, gui):
        self.dong = []
        self._log = lambda m, lv="INFO", kenh="": self.dong.append((lv, m))
        self._bao_nhip_quet = gui.VE3App._bao_nhip_quet.__get__(self)
        self.QUET_CHAM_GIAY = gui.VE3App.QUET_CHAM_GIAY
        self.QUET_BAO_MOI = gui.VE3App.QUET_BAO_MOI


def test_quet_cham_thi_NOI_RA_kem_ma_cham_nhat(gui):
    """"Hàng chờ chậm" mà không có số thì mọi phép chữa đều là đoán.

    Log 17:14–17:20 ngày 15/08/2026: vòng bò 6 giây một mã. Đo `openpyxl` trên
    máy dev thì một quyển 121 cảnh chỉ mất 0,04 giây — nên chỗ tốn thời gian
    KHÔNG nằm ở nơi ai cũng nghĩ, và phải đo ở máy người dùng mới biết.
    """
    a = _AppBao(gui)
    a._bao_nhip_quet([("TH1-0001", 1.0), ("TH2-0002", 30.0), ("TH3-0003", 2.0)])
    assert a.dong, "quet 33 giay ma khong noi gi"
    _lv, m = a.dong[0]
    assert "TH2-0002" in m, "khong chi ra ma nao an het thoi gian: {0}".format(m)
    assert "3 ma" in m and "s/ma" in m, m


def test_quet_nhanh_thi_IM_LANG(gui):
    """Vòng khoẻ chỉ mất vài trăm mili-giây — nói ra là biến log thành nhiễu."""
    a = _AppBao(gui)
    a._bao_nhip_quet([("TH1-0001", 0.05)] * 20)
    assert not a.dong


def test_KHONG_noi_lai_qua_day(gui):
    """Đây là dòng chẩn đoán, không phải dòng theo dõi."""
    a = _AppBao(gui)
    a._bao_nhip_quet([("TH1-0001", 99.0)])
    a._bao_nhip_quet([("TH1-0001", 99.0)])
    assert len(a.dong) == 1, "moi vong mot dong -> log ngap, khong ai doc nua"
