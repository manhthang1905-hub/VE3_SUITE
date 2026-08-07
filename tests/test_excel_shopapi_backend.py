"""Kiểm nhánh `api_shop` — tạo Excel bằng LLM của api.shopapi.vn.

Nhánh này KHÔNG có mã mới cho việc gọi mạng: `_run_via_api` đã có sẵn và nói
chuẩn OpenAI, mà shopapi cũng nói chuẩn OpenAI. Việc duy nhất phải làm đúng là
**phân giải cấu hình** — địa chỉ, khoá, model. Nên đó là thứ bộ kiểm này canh.

Hai điều đáng canh nhất, cả hai đều đã hỏng thật một lần:

* **Khoá phải lấy từ kho khoá của máy, KHÔNG từ `settings.yaml`.** File cấu hình
  đó nằm trong kho mã — mở `tools/srt-to-excel/config/settings.yaml` ra là thấy
  khoá DeepSeek đã bị đẩy lên lịch sử git. Khoá `sk_live_` có ví tiền thật.

* **Mã 201 vẫn là thành công.** `api.shopapi.vn` trả `201` cho `/v1/chat/
  completions`; bản cũ so `!= 200` nên vứt sạch câu trả lời hợp lệ.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

SUITE_ROOT = Path(__file__).resolve().parents[1]
SRT_DIR = SUITE_ROOT / "tools" / "srt-to-excel"


@pytest.fixture
def Engine():
    """Nạp `ClaudeCliEngine` mà không để hai gói `modules` giẫm lên nhau.

    ⚠ CÓ HAI GÓI TÊN `modules` TRONG KHO NÀY: `tools/ve3/modules` và
    `tools/srt-to-excel/modules`. `tests/conftest.py` đặt `tools/ve3` lên
    `sys.path` cho bộ kiểm worker, nên chạy CẢ BỘ thì `import modules...` trúng
    gói của ve3 và bài kiểm này chết với `ModuleNotFoundError` — dù chạy một
    mình vẫn xanh. Đã dính đúng một lần, mất mấy phút mới hiểu.

    Nên: đẩy srt-to-excel lên đầu path, dọn `modules*` khỏi `sys.modules`, nạp,
    rồi TRẢ NGUYÊN hiện trạng để bài kiểm sau vẫn thấy gói của ve3.
    """
    path_cu = list(sys.path)
    modules_cu = {k: v for k, v in sys.modules.items()
                  if k == "modules" or k.startswith("modules.")}
    for ten in modules_cu:
        del sys.modules[ten]
    sys.path.insert(0, str(SRT_DIR))
    try:
        from modules.claude_cli_engine import ClaudeCliEngine
        yield ClaudeCliEngine
    finally:
        for ten in [k for k in sys.modules
                    if k == "modules" or k.startswith("modules.")]:
            del sys.modules[ten]
        sys.modules.update(modules_cu)
        sys.path[:] = path_cu


# ── Phân giải cấu hình ───────────────────────────────────────────────────────


@pytest.mark.parametrize("backend, lui_ve_cli", [
    ("api_shop", False),
    ("api_shop_cli", True),
])
def test_hai_nhanh_shopapi_deu_di_duong_http(Engine, backend, lui_ve_cli):
    eng = Engine({"claude_cli_backend": backend})
    assert eng.backend == backend, "backend hop le KHONG duoc bi rot ve 'cli'"
    assert eng._uses_api is True
    assert eng._api_fallback_cli is lui_ve_cli


def test_dia_chi_va_model_mac_dinh_dung(Engine):
    eng = Engine({"claude_cli_backend": "api_shop"})
    assert eng.api_base_url == "https://api.shopapi.vn/v1"
    # `fable` = model viet lach sang tao cua shopapi. Viec o day la VIET PROMPT
    # ta canh, nen mac dinh phai la no chu khong phai model code/suy luan.
    assert eng.api_model == "claude-fable-5"


def test_doi_duoc_model_khi_muon_re_hon(Engine):
    eng = Engine({"claude_cli_backend": "api_shop", "shopapi_model": "claude-sonnet-5"})
    assert eng.api_model == "claude-sonnet-5"


def test_backend_la_rac_thi_ve_cli_chu_khong_no(Engine):
    eng = Engine({"claude_cli_backend": "api_shopp"})
    assert eng.backend == "cli"


# ── Khoá: kho khoá của máy, KHÔNG phải settings.yaml ─────────────────────────


def test_khoa_lay_tu_kho_khoa_cua_may(Engine, monkeypatch):
    monkeypatch.setattr(Engine, "_doc_khoa_shopapi", staticmethod(lambda: "sk_live_TU_KHO_KHOA"))
    eng = Engine({"claude_cli_backend": "api_shop"})
    assert eng.api_key == "sk_live_TU_KHO_KHOA"


def test_khong_co_khoa_thi_rong_chu_khong_no(Engine, monkeypatch):
    """Máy chưa lưu khoá bao giờ -> `_run_via_api` báo 'chua cau hinh' tử tế."""
    monkeypatch.setattr(Engine, "_doc_khoa_shopapi", staticmethod(lambda: ""))
    eng = Engine({"claude_cli_backend": "api_shop"})
    assert eng.api_key == ""

    with pytest.raises(RuntimeError, match="chua cau hinh"):
        eng._run_via_api("bat ky")


def test_doc_khoa_khong_bao_gio_nem_ra_ngoai(Engine, monkeypatch):
    """Kho khoá hỏng/không tìm thấy module -> trả rỗng, KHÔNG làm sập lượt tạo Excel."""
    import builtins
    that = builtins.__import__

    def gay_loi(ten, *a, **k):
        if ten == "shopapi_common":
            raise ImportError("gia vo may nay khong co module")
        return that(ten, *a, **k)

    monkeypatch.setattr(builtins, "__import__", gay_loi)
    assert Engine._doc_khoa_shopapi() == ""


# ── Mã 2xx: 201 vẫn là thành công ────────────────────────────────────────────


class _RespGia:
    """Response giả đủ dùng cho `_run_via_api` (nó chỉ cần status + iter_lines)."""

    def __init__(self, status, dong):
        self.status_code = status
        self._dong = dong
        self.text = ""

    def iter_lines(self):
        for d in self._dong:
            yield d.encode("utf-8")

    def close(self):
        pass


@pytest.mark.parametrize("ma", [200, 201])
def test_moi_ma_2xx_deu_doc_duoc_noi_dung(Engine, monkeypatch, ma):
    """⚠ shopapi tra 201 — bam dung 200 la vut sach cau tra loi HOP LE."""
    import requests as _rq

    dong = [
        'data: {"choices":[{"delta":{"content":"xin "}}]}',
        'data: {"choices":[{"delta":{"content":"chao"}}]}',
        "data: [DONE]",
    ]
    monkeypatch.setattr(_rq, "post", lambda *a, **k: _RespGia(ma, dong))

    eng = Engine({"claude_cli_backend": "api_shop", "shopapi_api_key": "sk_live_gia"})
    assert eng._run_via_api("bat ky") == "xin chao"


def test_ma_loi_that_van_phai_hong(Engine, monkeypatch):
    """Nới sang 2xx KHÔNG được nới nhầm sang 4xx/5xx."""
    import requests as _rq, time as _t
    monkeypatch.setattr(_rq, "post", lambda *a, **k: _RespGia(500, []))
    monkeypatch.setattr(_t, "sleep", lambda *_: None)

    eng = Engine({"claude_cli_backend": "api_shop", "shopapi_api_key": "sk_live_gia"})
    with pytest.raises(RuntimeError, match="HTTP 500"):
        eng._run_via_api("bat ky")


def test_tra_ve_rong_thi_bao_hong_chu_khong_nuot(Engine, monkeypatch):
    """ĐANG XẢY RA THẬT (07/08/2026): shopapi tra 201 kem noi dung RONG,
    `completion_tokens=0`, ca ba model. Phai bao hong ro rang de con lui ve CLI,
    tuyet doi khong tra chuoi rong cho phan dung Excel."""
    import requests as _rq, time as _t
    monkeypatch.setattr(_rq, "post", lambda *a, **k: _RespGia(201, ["data: [DONE]"]))
    monkeypatch.setattr(_t, "sleep", lambda *_: None)

    eng = Engine({"claude_cli_backend": "api_shop", "shopapi_api_key": "sk_live_gia"})
    with pytest.raises(RuntimeError, match="empty content"):
        eng._run_via_api("bat ky")
