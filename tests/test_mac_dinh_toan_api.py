"""Chốt MẶC ĐỊNH của bản này: mở tool lên là đi API shopapi cho CẢ BA khâu.

Chủ dự án yêu cầu rõ: "chạy tool thì tool sẽ đi với option api" — Excel dùng
`claude-sonnet-5` của shopapi, ảnh và video không dùng pool nữa mà qua shopapi.

Bốn khoá cấu hình phải cùng đứng đúng chỗ, và chúng nằm ở HAI hàm khác nhau
trong `ve3_gui.py` (`_load_config` lo ảnh/video, `_build_excel_runtime_config`
lo Excel). Lệch một cái là tool lặng lẽ chạy đường cũ: mở Chrome, ngốn pool, mà
KHÔNG có một dòng lỗi nào — đúng kiểu hỏng khó thấy nhất.

Bài kiểm đọc thẳng mã nguồn thay vì dựng GUI: `ve3_gui.py` cần tkinter +
customtkinter và một màn hình thật, không dựng nổi trong CI. Đọc `setdefault`
bằng AST vẫn bắt được đúng thứ cần bắt — ai đổi giá trị mặc định là đỏ ngay.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

VE3_GUI = Path(__file__).resolve().parents[1] / "tools" / "ve3" / "ve3_gui.py"


def _cay():
    return ast.parse(VE3_GUI.read_text(encoding="utf-8", errors="replace"))


def _ham(ten):
    for node in ast.walk(_cay()):
        if isinstance(node, ast.FunctionDef) and node.name == ten:
            return node
    raise AssertionError("khong tim thay ham {0} trong ve3_gui.py".format(ten))


def _setdefault(ten_ham):
    """`{khoá: giá trị}` của mọi lời gọi `cfg.setdefault("k", <hằng>)` trong hàm."""
    ra = {}
    for node in ast.walk(_ham(ten_ham)):
        if not isinstance(node, ast.Call):
            continue
        f = node.func
        if not (isinstance(f, ast.Attribute) and f.attr == "setdefault"):
            continue
        if len(node.args) != 2:
            continue
        try:
            ra[ast.literal_eval(node.args[0])] = ast.literal_eval(node.args[1])
        except Exception:
            pass
    return ra


def _gan(ten_ham):
    """`{khoá: giá trị}` của mọi phép gán `self.config_data["k"] = <hằng>`."""
    ra = {}
    for node in ast.walk(_ham(ten_ham)):
        if not isinstance(node, ast.Assign):
            continue
        for t in node.targets:
            if isinstance(t, ast.Subscript):
                try:
                    ra[ast.literal_eval(t.slice)] = ast.literal_eval(node.value)
                except Exception:
                    pass
    return ra


# ── Ảnh + video: shopapi, KHÔNG phải pool ────────────────────────────────────


def test_video_mac_dinh_di_shopapi():
    cau_hinh = _gan("_load_config")
    assert cau_hinh.get("generation_backend") == "shopapi"
    # Máy cũ chỉ có `generation_mode`; đặt thiếu là ý cũ đè lên mặc định mới.
    assert cau_hinh.get("generation_mode") == "shopapi"


def test_anh_mac_dinh_di_shopapi():
    assert _gan("_load_config").get("veo3top_image_mode") == "shopapi"


# ── Excel: LLM của shopapi, KHÔNG phải claude.exe / DeepSeek ─────────────────


def test_excel_mac_dinh_dung_engine_mot_lan_goi():
    # ⚠ "claude_cli" là tên LỊCH SỬ của engine, không có nghĩa là chạy claude.exe.
    # Đường vận chuyển do `claude_cli_backend` quyết định (bài kiểm dưới).
    assert _setdefault("_build_excel_runtime_config").get("excel_engine") == "claude_cli"


def test_excel_mac_dinh_di_http_shopapi_khong_mo_claude_exe():
    assert _setdefault("_build_excel_runtime_config").get("claude_cli_backend") == "api_shop"


def test_excel_dung_dung_model_chu_du_an_chot():
    """`claude-sonnet-5`. `fable-5` dang 503, va sonnet-5 re nhat trong ba con."""
    import sys

    SRT = Path(__file__).resolve().parents[1] / "tools" / "srt-to-excel"
    path_cu, mods_cu = list(sys.path), {k: v for k, v in sys.modules.items()
                                        if k == "modules" or k.startswith("modules.")}
    for t in mods_cu:
        del sys.modules[t]
    sys.path.insert(0, str(SRT))
    try:
        from modules.claude_cli_engine import ClaudeCliEngine
        eng = ClaudeCliEngine({"claude_cli_backend": "api_shop"})
        assert eng.api_model == "claude-sonnet-5"
        assert eng.api_base_url == "https://api.shopapi.vn/v1"
    finally:
        for t in [k for k in sys.modules if k == "modules" or k.startswith("modules.")]:
            del sys.modules[t]
        sys.modules.update(mods_cu)
        sys.path[:] = path_cu


# ── Không còn đường nào lặng lẽ quay về pool/claude.exe ──────────────────────


@pytest.mark.parametrize("khoa, cam", [
    ("generation_backend", ("veo3top_b_pool", "server", "veo3top")),
    ("veo3top_image_mode", ("pool", "account", "blank")),
])
def test_khong_con_mac_dinh_nao_tro_ve_pool(khoa, cam):
    assert _gan("_load_config").get(khoa) not in cam
