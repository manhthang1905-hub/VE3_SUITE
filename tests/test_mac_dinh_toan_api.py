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


# ── Cổng token Flow phải BIẾT về shopapi ─────────────────────────────────────
#
# ĐÃ CHẶN NHẦM THẬT: chọn shopapi cho cả ảnh lẫn video, khoá đã lưu, bấm chạy ->
# "Can token hop le hoac it nhat 1 pair co du gmail bundle va chrome path".
#
# Gốc: `_build_cfg` chạy TRƯỚC khi worker khởi động và nó không biết gì về
# shopapi, trong khi `ve3_worker._shopapi_only` thừa biết là không cần bearer.
# Cổng khắt khe hơn worker = cấu hình hợp lệ bị chặn ngay ở cửa, và người dùng
# không có cách nào đi tiếp vì họ ĐÚNG là không cần token.


@pytest.fixture
def cong():
    """`_chi_dung_shopapi` gọi rời khỏi `self` — nó không đụng thuộc tính nào."""
    import sys
    VE3 = Path(__file__).resolve().parents[1] / "tools" / "ve3"
    if str(VE3) not in sys.path:
        sys.path.insert(0, str(VE3))
    import ve3_gui
    return ve3_gui.VE3App._chi_dung_shopapi


CA_HAI_SHOPAPI = {"veo3top_image_mode": "shopapi", "generation_backend": "shopapi"}


def test_ca_hai_khau_shopapi_va_co_khoa_thi_KHONG_doi_token(cong):
    assert cong(None, dict(CA_HAI_SHOPAPI)) is True


def test_may_cu_chi_co_generation_mode_van_nhan_ra(cong):
    """Máy cũ không có `generation_backend` — đọc thiếu là chặn oan."""
    assert cong(None, {"veo3top_image_mode": "shopapi",
                       "generation_mode": "shopapi"}) is True


@pytest.mark.parametrize("ten, cfg", [
    ("anh con di pool", {"veo3top_image_mode": "pool", "generation_backend": "shopapi"}),
    ("video con di server", {"veo3top_image_mode": "shopapi", "generation_backend": "server"}),
    ("ca hai deu duong cu", {"veo3top_image_mode": "pool", "generation_backend": "server"}),
    ("chua chon gi", {}),
])
def test_con_mot_khau_di_duong_cu_thi_VAN_phai_doi_token(cong, ten, cfg):
    """Còn một khâu đường cũ là còn mở Chrome, mà đường cũ vẫn cần auth thật."""
    assert cong(None, cfg) is False, ten


def test_chua_luu_khoa_thi_VAN_phai_doi_token(cong, monkeypatch):
    """Thiếu khoá thì worker tự lùi về đường cũ — mà đường cũ cần auth. Bỏ qua
    cổng lúc này chỉ để nó chết sâu hơn ở giữa lượt chạy."""
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "veo3top_engine"))
    import shopapi_common as sc
    monkeypatch.setattr(sc, "doc_khoa", lambda env=None: ("", ""))
    assert cong(None, dict(CA_HAI_SHOPAPI)) is False


def test_kho_khoa_hong_thi_chan_chu_khong_no(cong, monkeypatch):
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "veo3top_engine"))
    import shopapi_common as sc

    def no(env=None):
        raise OSError("kho khoa hong")

    monkeypatch.setattr(sc, "doc_khoa", no)
    assert cong(None, dict(CA_HAI_SHOPAPI)) is False


# ── Cổng "Thiếu server" và cổng "Thiếu cấu hình AI" cũng phải biết shopapi ───
#
# Sau khi go duoc cong token, chu du an dam tiep vao "Them server trong Cai dat
# truoc!" — cung mot loai loi: cong ra doi TRUOC nhanh shopapi.
#
# Di toan API thi khong buoc nao cham toi mot server Chrome, va `excel_ai_provider`
# (DeepSeek/VOV/Claude Pool) khong duoc dung toi mot lan nao.


@pytest.fixture
def cong_excel():
    import sys
    VE3 = Path(__file__).resolve().parents[1] / "tools" / "ve3"
    if str(VE3) not in sys.path:
        sys.path.insert(0, str(VE3))
    import ve3_gui
    return ve3_gui.VE3App._excel_di_shopapi


EXCEL_API = {"excel_engine": "claude_cli", "claude_cli_backend": "api_shop"}


def test_excel_di_shopapi_thi_khong_doi_khoa_provider_cu(cong_excel):
    assert cong_excel(None, dict(EXCEL_API)) is True


def test_excel_api_shop_cli_cung_tinh(cong_excel):
    assert cong_excel(None, dict(EXCEL_API, claude_cli_backend="api_shop_cli")) is True


@pytest.mark.parametrize("ten, cfg", [
    ("engine cu (DeepSeek/VOV nhieu buoc)", {"excel_engine": "api", "claude_cli_backend": "api_shop"}),
    ("van chay claude.exe", {"excel_engine": "claude_cli", "claude_cli_backend": "cli"}),
    ("di VOV", {"excel_engine": "claude_cli", "claude_cli_backend": "api"}),
    ("di DeepSeek", {"excel_engine": "claude_cli", "claude_cli_backend": "api_ds"}),
])
def test_excel_khong_di_shopapi_thi_van_kiem_nhu_cu(cong_excel, ten, cfg):
    assert cong_excel(None, cfg) is False, ten


def test_excel_chua_co_khoa_thi_van_kiem_nhu_cu(cong_excel, monkeypatch):
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "veo3top_engine"))
    import shopapi_common as sc
    monkeypatch.setattr(sc, "doc_khoa", lambda env=None: ("", ""))
    assert cong_excel(None, dict(EXCEL_API)) is False


def test_khoa_dat_thang_trong_cfg_cung_duoc_chap_nhan(cong_excel, monkeypatch):
    """Không phụ thuộc kho khoá: worker headless nhận khoá qua runtime config."""
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "veo3top_engine"))
    import shopapi_common as sc
    monkeypatch.setattr(sc, "doc_khoa", lambda env=None: ("", ""))
    assert cong_excel(None, dict(EXCEL_API, shopapi_api_key="sk_live_abc123def456ghi")) is True


# ── Hàng chờ: phải có "chỗ làm" khi chạy toàn API ────────────────────────────
#
# Vong lap hang cho chi giao viec cho mot "pair" (server + tai khoan Flow). Di
# toan API thi KHONG co server nao, cung KHONG co tai khoan nao -> pair rong ->
# vong lap quay mai ma khong phat mot viec nao. Khong loi, khong canh bao, chi
# la khong co gi xay ra — kieu hong kho doan nhat.


class _AppGia:
    """Chỉ mang `config_data` + mượn đúng các hàm pair của `VE3App`.

    Dựng `VE3App` thật cần tkinter + màn hình; các hàm này chỉ đọc
    `self.config_data` nên mượn sang là chạy được, và vẫn kiểm ĐÚNG mã thật.
    """

    def __init__(self, config_data):
        import sys
        VE3 = Path(__file__).resolve().parents[1] / "tools" / "ve3"
        if str(VE3) not in sys.path:
            sys.path.insert(0, str(VE3))
        import ve3_gui
        self.config_data = config_data
        for ten in ("_chi_dung_shopapi", "_so_ma_song_song_shopapi", "_pair_ao_shopapi"):
            setattr(self, ten, getattr(ve3_gui.VE3App, ten).__get__(self))
        # Đường pair THẬT còn cần mấy thứ này; bài kiểm không dựng server nào.
        self.server_status_cache = []
        self._server_pair_debug_enabled = False
        self._get_flow_account_map = lambda: {}
        self._pair_account_name = lambda row, idx: ""
        self._status_accepts_tasks = lambda st: False


def _goi(ten_ham, app, *a, **k):
    import sys
    VE3 = Path(__file__).resolve().parents[1] / "tools" / "ve3"
    if str(VE3) not in sys.path:
        sys.path.insert(0, str(VE3))
    import ve3_gui
    return getattr(ve3_gui.VE3App, ten_ham)(app, *a, **k)


CFG_API = {"veo3top_image_mode": "shopapi", "generation_backend": "shopapi",
           "local_server_list": []}


def test_hang_cho_co_cho_lam_khi_chay_toan_api():
    app = _AppGia(dict(CFG_API))
    pairs = _goi("_get_server_pairs", app, only_available=True)
    assert pairs, "khong co pair -> hang cho dung im vinh vien, khong bao gi"
    assert all(p["available"] for p in pairs)
    assert len({p["pair_id"] for p in pairs}) == len(pairs), "pair_id phai khac nhau"


def test_so_cho_lam_dieu_chinh_duoc():
    app = _AppGia(dict(CFG_API, shopapi_ma_song_song=5))
    assert len(_goi("_get_server_pairs", app, only_available=True)) == 5


def test_so_cho_lam_rac_thi_ve_mac_dinh_chu_khong_no():
    app = _AppGia(dict(CFG_API, shopapi_ma_song_song="nhieu vao"))
    assert len(_goi("_get_server_pairs", app, only_available=True)) == 3


def test_cho_lam_ao_KHONG_dung_ServerPool_cho_server_khong_ton_tai():
    """Danh sách khác rỗng là `_init_server_pool` dựng pool cho một server ma."""
    app = _AppGia(dict(CFG_API))
    pair = _goi("_get_server_pairs", app, only_available=True)[0]
    cfg = _goi("_build_project_pair_cfg", app, dict(CFG_API), pair)

    assert cfg["local_server_list"] == []
    assert cfg["local_server_url"] == ""
    assert cfg["generation_backend"] == "shopapi", "khong duoc lam mat backend"
    assert cfg["veo3top_image_mode"] == "shopapi"


def test_da_khai_server_that_thi_KHONG_dung_cho_lam_ao():
    """Người dùng khai server thật -> tôn trọng, đừng lén thay bằng pair ảo."""
    app = _AppGia(dict(CFG_API, local_server_list=[
        {"url": "http://127.0.0.1:8801", "name": "Sv-1", "enabled": True}]))
    pairs = _goi("_get_server_pairs", app, only_available=False)
    assert not any(p.get("ao_shopapi") for p in pairs), [p["pair_id"] for p in pairs]
