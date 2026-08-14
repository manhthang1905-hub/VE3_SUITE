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


@pytest.fixture(autouse=True)
def _quen_khoa():
    """Xoá câu trả lời "có khoá chưa" mà `ve3_gui` nhớ, trước VÀ sau mỗi bài.

    Cache đó là hành vi thật (xem `_KHOA_TTL`): `doc_khoa()` đọc file mỗi lần
    gọi, mà từ 11/08/2026 nó bị hỏi trong vòng hàng chờ và trong đường vẽ giao
    diện. Nhưng nhớ xuyên qua các bài kiểm thì bài sau đọc phải câu trả lời của
    bài trước, và `monkeypatch` trên `doc_khoa` thành vô hiệu.
    """
    import sys
    VE3 = Path(__file__).resolve().parents[1] / "tools" / "ve3"
    if str(VE3) not in sys.path:
        sys.path.insert(0, str(VE3))
    import ve3_gui
    ve3_gui.quen_khoa_shopapi()
    yield
    ve3_gui.quen_khoa_shopapi()


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
    import ve3_gui
    app = _AppGia(dict(CFG_API, shopapi_ma_song_song="nhieu vao"))
    assert (len(_goi("_get_server_pairs", app, only_available=True))
            == ve3_gui.SHOPAPI_MA_SONG_SONG_MAC_DINH)


def test_cho_lam_ao_KHONG_dung_ServerPool_cho_server_khong_ton_tai():
    """Danh sách khác rỗng là `_init_server_pool` dựng pool cho một server ma."""
    app = _AppGia(dict(CFG_API))
    pair = _goi("_get_server_pairs", app, only_available=True)[0]
    cfg = _goi("_build_project_pair_cfg", app, dict(CFG_API), pair)

    assert cfg["local_server_list"] == []
    assert cfg["local_server_url"] == ""
    assert cfg["generation_backend"] == "shopapi", "khong duoc lam mat backend"
    assert cfg["veo3top_image_mode"] == "shopapi"


def test_con_server_trong_cau_hinh_ma_chay_toan_API_thi_VAN_dung_cho_lam_ao():
    """CHẾ ĐỘ ĐANG CHẠY quyết định, không phải danh sách server còn sót lại.

    ═══ BÀI KIỂM NÀY ĐẢO NGƯỢC MỘT LUẬT CŨ, CỐ Ý ═══

    Bản trước đòi `local_server_list` PHẢI RỖNG mới dựng chỗ làm ảo, với lý do
    "người dùng khai server thật thì tôn trọng". Nghe hợp lý, nhưng nó bỏ sót
    đúng trường hợp phổ biến nhất: ai đã từng chạy đường Chrome thì danh sách 10
    server vẫn nằm nguyên trong `settings.yaml` sau khi chuyển sang API.

    Hậu quả đo được ngày 11/08/2026: chạy toàn API mà số mã song song vẫn bị ghim
    bằng số Chrome portable — những cái mà chế độ API KHÔNG mở, KHÔNG dùng, và
    người dùng không có lý do gì để nghĩ là còn liên quan. `shopapi_ma_song_song`
    cũng chết theo vì nó chỉ được đọc trong `_pair_ao_shopapi`: vặn con số đó
    không có tác dụng gì, và không có lấy một dòng log nào nói vì sao.

    Xoá server khỏi cấu hình không phải cách chữa — người dùng còn muốn quay lại
    đường Chrome. Nên cái quyết định phải là chế độ, và chỉ chế độ.
    """
    import ve3_gui
    app = _AppGia(dict(CFG_API, local_server_list=[
        {"url": "http://127.0.0.1:8801", "name": "Sv-1", "enabled": True}]))
    pairs = _goi("_get_server_pairs", app, only_available=False)
    assert all(p.get("ao_shopapi") for p in pairs), [p["pair_id"] for p in pairs]
    assert len(pairs) == ve3_gui.SHOPAPI_MA_SONG_SONG_MAC_DINH, (
        "so ma song song van bam theo so server Chrome thay vi theo cau hinh API")


def test_KHONG_chay_toan_api_thi_van_dung_server_that():
    """Còn một khâu đi đường cũ là còn cần Chrome — không được thay bằng pair ảo."""
    app = _AppGia({"veo3top_image_mode": "shopapi", "generation_backend": "veo3top_b",
                   "local_server_list": [
                       {"url": "http://127.0.0.1:8801", "name": "Sv-1", "enabled": True}]})
    pairs = _goi("_get_server_pairs", app, only_available=False)
    assert not any(p.get("ao_shopapi") for p in pairs), [p["pair_id"] for p in pairs]


def test_binding_server_cu_KHONG_chan_ma_khi_chay_toan_API():
    """Mã có `.ve3_binding.yaml` trỏ `sv9` vẫn phải chạy được ở chế độ API.

    ═══ ĐÂY LÀ CÁI BẪY ĐI KÈM CHỖ LÀM ẢO ═══

    Bật chỗ làm ảo xong thì `free_pairs` chỉ còn `server_name = "API shopapi"`.
    Mọi mã cũ đều mang binding từ hồi chạy Chrome (`bound_server_name: sv9`),
    nên nhánh `if bound_server` tìm không ra, ghi "missing from config. Waiting
    (will not reassign)" rồi trả `None` — và mã đó KHÔNG BAO GIỜ chạy nữa.

    Tức là gỡ một nút thắt mà quên chỗ này thì đổi "chạy chậm" lấy "đứng hẳn".
    Ngày 11/08/2026 có 75 mã đang mang binding trỏ vào sv1..sv10.
    """
    import ve3_gui
    app = _AppGia(dict(CFG_API, local_server_list=[
        {"url": "http://127.0.0.1:8801", "name": "sv9", "enabled": True}]))
    app._load_project_pair_binding = lambda pd: {
        "bound_server_name": "sv9", "bound_account_name": "ai_do@gmail.com"}
    app.queue_pair_last_used = {}

    pairs = _goi("_get_server_pairs", app, only_available=False)
    chon = _goi("_choose_pair_for_project", app, Path("TL1-0756"), pairs)

    assert chon is not None, "ma bi chan hoan toan vi mot binding Chrome cu"
    assert chon.get("ao_shopapi")


# ── Nhật ký ra ĐĨA ───────────────────────────────────────────────────────────


def test_co_ghi_nhat_ky_ra_file(tmp_path, monkeypatch):
    """VE3 phải để lại vết trên đĩa, không chỉ trong khung cửa sổ.

    Chiều 11/08/2026 tool chạy rồi dừng hẳn — 7 mã giữ lock, 0 job, 0 file trong
    90 giây, cả GUI lẫn 8 worker đều đã thoát. Không một dòng nào còn lại để
    biết vì sao, nên phải suy ngược từ log của MÁY CHỦ. Một tool chạy hàng giờ
    không người trông mà không để lại vết thì không gỡ lỗi được.
    """
    import sys
    VE3 = Path(__file__).resolve().parents[1] / "tools" / "ve3"
    if str(VE3) not in sys.path:
        sys.path.insert(0, str(VE3))
    import ve3_gui

    monkeypatch.setattr(ve3_gui, "LOG_DIR", tmp_path / "logs")
    monkeypatch.setattr(ve3_gui, "_log_file_handle", [None, ""])
    ve3_gui.ghi_log_file("mot dong thu", "ERROR", "ve3")

    ra = list((tmp_path / "logs").glob("ve3-*.log"))
    assert ra, "khong ghi file nhat ky nao"
    noi_dung = ra[0].read_text(encoding="utf-8")
    assert "mot dong thu" in noi_dung and "ERROR" in noi_dung


def test_ghi_nhat_ky_hong_KHONG_lam_chet_tool(monkeypatch):
    """Ghi log không bao giờ được làm chết thứ nó đang ghi lại."""
    import sys
    VE3 = Path(__file__).resolve().parents[1] / "tools" / "ve3"
    if str(VE3) not in sys.path:
        sys.path.insert(0, str(VE3))
    import ve3_gui

    class _ODiaHong:
        def mkdir(self, *a, **k):
            raise OSError("o dia day")

        def __truediv__(self, other):
            return self

    monkeypatch.setattr(ve3_gui, "LOG_DIR", _ODiaHong())
    monkeypatch.setattr(ve3_gui, "_log_file_handle", [None, ""])
    ve3_gui.ghi_log_file("van phai chay tiep", "INFO", None)   # khong duoc nem


def test_dong_stdout_khong_co_tien_to_KHONG_bi_vut_di():
    """Traceback của worker không bắt đầu bằng `@@LOG|` — phải được ghi lại.

    `stderr` gộp vào `stdout` ngay ở `Popen`, nên mọi traceback Python đi qua
    đúng vòng đọc đó. Bản trước để chúng rơi khỏi chuỗi `elif` mà không làm gì:
    worker chết vì lý do gì cũng không ai biết, log sạch bong.
    """
    nguon = VE3_GUI.read_text(encoding="utf-8", errors="replace")
    moc = nguon.find('elif line.startswith("@@RESULT|")')
    assert moc > 0, "khong tim thay vong doc stdout cua worker"
    khoi = nguon[moc:moc + 1800]
    assert "else:" in khoi, "van chua co nhanh bat dong khong co tien to"
    assert '"ERROR"' in khoi, "dong la phai duoc ghi o muc ERROR"


# ── Giao diện phải khớp CHẾ ĐỘ đang chạy ─────────────────────────────────────
#
# Trang Cài đặt có tám núm, và cả tám đều là núm của đường Chrome/pool: account
# pool, token chrome, recycle, luồng/account ultra, nghỉ 429, mã ảnh/video. Đi
# API shopapi thì KHÔNG cái nào được đọc.
#
# Ba con số thật sự quyết định thông lượng ở chế độ API lại không có mặt trên
# giao diện. Ngày 11/08/2026 đó chính là cách chúng bị bỏ ở mức làm tool chạy 1%
# công suất suốt nhiều giờ: màn hình đầy núm, không núm nào nối tới thứ đang bóp.


_KHOA_API_TREN_GUI = ("shopapi_ma_song_song", "max_concurrent", "shopapi_video_concurrency")


@pytest.mark.parametrize("khoa", _KHOA_API_TREN_GUI)
def test_ba_num_API_co_tren_giao_dien(khoa):
    """Chỉnh được từ GUI, không phải mở `settings.yaml` bằng tay."""
    nguon = VE3_GUI.read_text(encoding="utf-8", errors="replace")
    assert '"{0}"'.format(khoa) in nguon, (
        "khoa {0} khong xuat hien trong ve3_gui.py -> khong chinh duoc tu giao dien".format(khoa))


@pytest.mark.parametrize("khoa", _KHOA_API_TREN_GUI)
def test_ba_num_API_duoc_LUU_va_NAP(khoa):
    """Có ô nhập mà không lưu/nạp thì gõ xong mất — tệ hơn là không có ô."""
    nguon = VE3_GUI.read_text(encoding="utf-8", errors="replace")
    assert nguon.count('"{0}"'.format(khoa)) >= 2, (
        "khoa {0} chi xuat hien mot lan -> thieu duong luu HOAC duong nap".format(khoa))


def test_co_ham_lam_mo_num_pool_khi_di_API():
    """Núm không nối vào đâu phải trông khác núm còn sống."""
    nguon = VE3_GUI.read_text(encoding="utf-8", errors="replace")
    assert "_cap_nhat_num_theo_che_do" in nguon
    # Phải được GỌI, không chỉ định nghĩa rồi bỏ đó.
    assert nguon.count("_cap_nhat_num_theo_che_do") >= 2


def test_nhan_Parallel_jobs_KHONG_con_noi_cung_mot_cau():
    """Câu 'TU DONG (theo so chrome / so ma)' nói dối ở chế độ API.

    Không có chrome nào, và số mã do `shopapi_ma_song_song` quyết. Nhãn phải là
    widget có tên để đổi theo chế độ, không phải chuỗi chết.
    """
    nguon = VE3_GUI.read_text(encoding="utf-8", errors="replace")
    assert "self.lbl_parallel_jobs" in nguon, "nhan van la chuoi chet, khong doi theo che do"


def test_co_nut_doc_tran_may_chu():
    """Đặt 'đang xin' cạnh 'được cấp' — phép so duy nhất thấy được mình bỏ phí.

    11/08/2026: máy chủ cấp 691 chỗ ảnh, tool đặt lên 5,6. Không màn hình nào
    nói ra, vì không màn hình nào đặt hai con số đó cạnh nhau.
    """
    nguon = VE3_GUI.read_text(encoding="utf-8", errors="replace")
    assert "_doc_tran_may_chu" in nguon
    assert "tran_song_song" in nguon, "nut khong thuc su hoi /v1/me"


def test_doc_tran_chay_o_LUONG_NEN():
    """Một lời gọi mạng trong luồng Tk là cửa sổ đứng hình."""
    nguon = VE3_GUI.read_text(encoding="utf-8", errors="replace")
    moc = nguon.find("def _doc_tran_may_chu")
    assert moc > 0
    than = nguon[moc:moc + 3000]
    assert "threading.Thread" in than, "hoi /v1/me thang trong luong giao dien -> treo cua so"


# ── Hai bảng TRẠM phải đổi theo chế độ ───────────────────────────────────────
#
# Bộ chỉ số cũ ("ĐÃ LOGIN", "CÁCH LY 429", "ĐANG CHỮA", "SUBMIT/ACC"...) đo sức
# khoẻ kho Chrome/Gmail. Đi API thì không account nào login, không Chrome nào
# chạy, không ai bị 429 per-account — cả 16 ô đứng `-` và người vận hành nhìn
# vào một bảng chết, đúng ảnh chụp 11/08/2026.


def test_che_do_toan_api_o_CAP_MODULE_khong_phai_phuong_thuc():
    """Để ở cấp module thì không lớp nào gọi hụt được.

    Bản trước phép kiểm nằm hẳn trong `VE3App`, và `SettingsPage` gọi
    `self._chi_dung_shopapi(...)` — lớp khác, không có phương thức đó. Lời gọi
    ném `AttributeError`, `try/except` nuốt gọn, giao diện lặng lẽ coi như
    "không đi API". Sai mà không có triệu chứng.
    """
    import sys
    VE3 = Path(__file__).resolve().parents[1] / "tools" / "ve3"
    if str(VE3) not in sys.path:
        sys.path.insert(0, str(VE3))
    import ve3_gui
    assert callable(getattr(ve3_gui, "che_do_toan_api", None)), \
        "che_do_toan_api phai o cap module"
    assert ve3_gui.che_do_toan_api({"veo3top_image_mode": "pool",
                                    "generation_backend": "shopapi"}) is False


def test_VE3App_dung_CHUNG_mot_phep_kiem_che_do():
    """Hai bản cài đặt là hai câu trả lời khác nhau cho cùng một câu hỏi."""
    nguon = VE3_GUI.read_text(encoding="utf-8", errors="replace")
    moc = nguon.find("def _chi_dung_shopapi")
    than = nguon[moc:moc + 2500]
    assert "che_do_toan_api(cfg)" in than, "VE3App van co ban cai dat rieng"


def test_hai_bang_tram_co_bo_nhan_RIENG_cho_API():
    import sys
    VE3 = Path(__file__).resolve().parents[1] / "tools" / "ve3"
    if str(VE3) not in sys.path:
        sys.path.insert(0, str(VE3))
    import ve3_gui
    api = ve3_gui.HomePage.NHAN_TRAM_API
    pool = ve3_gui.HomePage.NHAN_TRAM_POOL
    assert set(api) == set(pool), "hai bo nhan phai phu dung 16 o"
    # Nhãn của đường Chrome KHÔNG được sót lại trong bộ API.
    for chet in ("ĐÃ LOGIN", "CÁCH LY 429", "ĐANG CHỮA", "SUBMIT/ACC"):
        assert chet not in api.values(), "{0} khong co nghia o che do API".format(chet)
    # HÀNG 1 — việc đi từ ta sang nhà máy, đọc trái sang phải là ra thủ phạm.
    for can in ("MÃ Ở PHA NÀY", "TA XIN / ĐƯỢC CẤP", "ĐANG CHẠY", "XẾP HÀNG"):
        assert can in api.values(), "thieu o {0}".format(can)
    # HÀNG 2 — hàng ra: nhanh chậm, hỏng, còn lại, bao giờ xong.
    for can in ("HỎNG (100 JOB)", "CÒN LẠI", "XONG SAU"):
        assert can in api.values(), "thieu o {0}".format(can)


def test_o_XEP_HANG_mang_MUI_TEN_XU_HUONG():
    """Một con số xếp hàng đứng yên không phân biệt được hai cảnh trái ngược.

        xếp hàng DÀI RA          = ta đẩy nhanh hơn nhà máy nhai -> trần là của
                                   NHÀ MÁY, đẩy thêm chỉ làm hàng dài hơn;
        xếp hàng ~0 mà chạy ít   = nhà máy rảnh mà job không tới -> nghẽn ở TA.

    Đo 15/08/2026 cảnh thứ nhất: tool đẩy 489 job cùng lúc, máy chủ chạy 361,
    hàng chờ leo 51 -> 454, sản lượng đứng ở 88 ảnh/phút. Không có mũi tên thì
    người vận hành nhìn "chạy 361 / chờ 454" và không biết nên vặn to hay nhỏ.
    """
    nguon = VE3_GUI.read_text(encoding="utf-8", errors="replace")
    than = ast.get_source_segment(nguon, _ham("_so_lieu_api_len_tram")) or ""
    assert "_cho_truoc" in than, "khong nho hang cho luot truoc -> khong tinh duoc xu huong"
    assert "↑" in than and "↓" in than, "o XEP HANG khong co mui ten xu huong"
    assert "_dai_ra" in than, "tinh xu huong ma khong dung no de chan doan"


def test_chan_doan_PHAN_BIET_nghen_o_TOOL_voi_nha_may_DA_DAY():
    """Đổ lỗi nhầm phía là thứ đắt nhất mà một dòng chẩn đoán có thể làm.

    Bản trước hễ thấy `chay < xin/2` là kết luận "nghẽn ở PHÍA TOOL" — kể cả
    lúc hàng chờ đang dài 454, tức là job CÓ tới nơi và đang đợi. Hai cảnh khác
    hẳn nhau: một cái bảo đi sửa tool, một cái bảo nhà máy đã hết sức.
    """
    nguon = VE3_GUI.read_text(encoding="utf-8", errors="replace")
    than = ast.get_source_segment(nguon, _ham("_so_lieu_api_len_tram")) or ""
    assert "ĐÃ BƠM ĐẦY NHÀ MÁY" in than,         "khong noi duoc canh nha may la tran"
    assert "NGHẼN Ở PHÍA TOOL" in than, "khong noi duoc canh nghen that o tool"
    # Soi ĐÚNG dòng điều kiện, đừng cắt cửa sổ ký tự quanh câu chữ: khối chú
    # thích ở giữa đẩy điều kiện ra ngoài cửa sổ và bài kiểm đỏ oan.
    dieu_kien = [d for d in than.splitlines() if "chay < xin * 0.5" in d]
    assert dieu_kien, "khong tim thay nhanh chan doan 'nghen o tool'"
    assert "cho <= max(" in dieu_kien[0], (
        "ket luan 'nghen o tool' ma khong doi hoi hang cho phai TRONG — hang cho "
        "dai nghia la job CO toi noi, chi la dang doi: " + dieu_kien[0].strip())


def test_o_XIN_tinh_theo_MA_DANG_O_PHA_khong_theo_cau_hinh():
    """`xin` = (mã đang ở pha này) × trần mỗi mã, KHÔNG phải (số mã cấu hình) × trần.

    Ảnh chụp 12/08/2026: bảng khai `TOOL ĐANG XIN 320` trong khi chỉ **1 mã** ở
    pha ảnh — xin thật là 40. Con số phóng đại 8 lần đó còn kéo dòng chẩn đoán
    sai theo: "xin 320 mà 0 job chạy" nghe như tool gãy, sự thật là chưa mã nào
    tới pha này.
    """
    nguon = VE3_GUI.read_text(encoding="utf-8", errors="replace")
    than = ast.get_source_segment(nguon, _ham("_so_lieu_api_len_tram")) or ""
    assert "xin = (ma or 0) * (tran_ma[loai] or 0)" in than, \
        "van tinh 'xin' theo so ma CAU HINH thay vi so ma DANG O PHA NAY"


def test_o_XIN_hoi_DUNG_HAM_ma_dong_co_dung():
    """Bảng phải hỏi đúng hàm động cơ gọi, nếu không nó vẽ một trần không ai áp.

    Từ khi bật tự điều tiết (15/08/2026), `max_concurrent` và
    `shopapi_video_concurrency` KHÔNG còn là trần — trần đến từ máy chủ chia cho
    số tiến trình mã đang sống, rồi cắt bằng suất luồng và nhịp gửi. Bảng vẫn
    đọc cấu hình, nên máy khác báo "XIN 40" (đúng bằng con số trong
    `settings.yaml`) trong khi động cơ đang xin một số hoàn toàn khác.

    Người vận hành so "xin 40 / chạy 3" rồi kết luận tool nghẽn — mà hai con số
    đó đến từ hai nguồn không liên quan gì nhau. Bảng nói dối còn tệ hơn bảng
    trống: nó gửi người ta đi chữa đúng chỗ không hỏng.
    """
    nguon = VE3_GUI.read_text(encoding="utf-8", errors="replace")
    than = ast.get_source_segment(nguon, _ham("_so_lieu_api_len_tram")) or ""
    assert "_hoi_tran(" in than, (
        "bang van tu tinh tran thay vi hoi `shopapi_batch._hoi_tran` — dung ham "
        "ma dong co goi moi lo")
    assert "tu_dieu_tiet=" in than, "khong truyen che do -> hoi sai nhanh"


def test_co_o_TI_LE_HONG():
    """Hỏng nhiều là thứ dễ bỏ qua nhất: bảng vẫn 'đang chạy', job vẫn nhúc nhích.

    Ngày 12/08/2026 video hỏng 54% suốt buổi mà không ô nào nói ra.
    """
    nguon = VE3_GUI.read_text(encoding="utf-8", errors="replace")
    moc = nguon.find("def _so_lieu_api_len_tram")
    than = nguon[moc:moc + 5000]
    assert "app_client_jobs_list" in than, "khong doc danh sach job -> khong biet ti le hong"


def test_KHONG_bay_so_noi_bo_cua_may_chu_len_bang_cua_minh():
    """VE3 là KHÁCH. Kho tài khoản và đội máy xử lý là của MÁY CHỦ.

    Bản chỉ số API đầu tiên bày ra `TÀI KHOẢN 94/96`, `MÁY XỬ LÝ 1`, `SỨC CHỨA
    1088` — VE3 không sở hữu, không điều khiển, và biết cũng không làm được gì.
    Bảng đầy số mà vẫn không trả lời được câu duy nhất cần trả lời: *ta đang làm
    được bao nhiêu, và cái gì đang chặn ta?*

    Chỉ hai loại được lên bảng: trạng thái CỦA TA, và ranh giới hợp đồng (trần
    máy chủ cấp cho ta — thứ ta phải tôn trọng).
    """
    import sys
    VE3 = Path(__file__).resolve().parents[1] / "tools" / "ve3"
    if str(VE3) not in sys.path:
        sys.path.insert(0, str(VE3))
    import ve3_gui
    for cua_nguoi_ta in ("TÀI KHOẢN", "MÁY XỬ LÝ", "SỨC CHỨA"):
        assert cua_nguoi_ta not in ve3_gui.HomePage.NHAN_TRAM_API.values(), (
            "{0} la so NOI BO cua may chu, khong phai thu VE3 quan ly duoc"
            .format(cua_nguoi_ta))


def test_ma_dang_chay_dem_tu_TIEN_TRINH_CON_khong_dem_file_lock():
    """`_boot()` xoá sạch `.queue_*.lock` mỗi lần một bản GUI khởi động.

    Đếm bằng lock thì chỉ cần mở thêm một cửa sổ VE3 thứ hai là bảng của bản
    đang chạy tụt hết về 0 — đã thấy đúng chuyện đó khi chụp màn hình.
    """
    nguon = VE3_GUI.read_text(encoding="utf-8", errors="replace")
    moc = nguon.find("def _con_lai_va_ma_theo_pha")
    assert moc > 0
    than = nguon[moc:moc + 3500]
    assert "queue_ve3_procs" in than, "van dem 'ma dang chay' bang file lock"


def test_san_luong_gio_dem_FILE_TREN_DIA():
    """Sản lượng phải đếm ở SẢN PHẨM CUỐI, không đọc lời khai của máy chủ.

    "Job succeeded" mà file chưa về đĩa thì chưa có gì để dùng — 738 job ngày
    11/08/2026 là đúng cảnh đó.
    """
    nguon = VE3_GUI.read_text(encoding="utf-8", errors="replace")
    moc = nguon.find("def _so_lieu_api_len_tram")
    than = nguon[moc:moc + 4000]
    assert "_count_production_today" in than, "khong do san luong that tu file"
    assert "3600" in than, "khong do theo cua so mot gio -> khong ra toc do"


def test_bang_tram_API_doc_v1_me_chu_khong_doc_health_pool():
    nguon = VE3_GUI.read_text(encoding="utf-8", errors="replace")
    moc = nguon.find("def _so_lieu_api_len_tram")
    assert moc > 0, "khong co ham dung so lieu API cho hai bang tram"
    than = nguon[moc:moc + 4000]
    assert "doc_v1_me" in than
    assert "8789" not in than and "8788" not in than, "van con doc /health cua pool"


def test_mau_o_van_de_o_CAP_MODULE():
    """`_so_lieu_api_len_tram` nằm ngoài `_work_body` nên không thấy biến cục bộ."""
    import sys
    VE3 = Path(__file__).resolve().parents[1] / "tools" / "ve3"
    if str(VE3) not in sys.path:
        sys.path.insert(0, str(VE3))
    import ve3_gui
    for ten in ("GREEN", "ORANGE", "RED", "GRAY"):
        assert hasattr(ve3_gui, ten), "{0} phai o cap module".format(ten)


def test_khoi_Chrome_duoc_GAP_khi_di_API_va_MO_LAI_duoc():
    """Mười dòng server chiếm nửa trang Cài đặt mà API không đọc dòng nào.

    Gấp chứ không xoá, và có nút mở lại: người dùng còn quay về đường Chrome.
    """
    nguon = VE3_GUI.read_text(encoding="utf-8", errors="replace")
    assert "_gap_khoi_chrome" in nguon
    assert "_mo_lai_khoi_chrome" in nguon
    assert "_ep_hien_chrome" in nguon, "bam Hien roi ma van bi gap lai sau lung nguoi dung"


def test_doc_v1_me_co_trong_shopapi_common():
    import sys
    ENGINE = Path(__file__).resolve().parents[1] / "veo3top_engine"
    if str(ENGINE) not in sys.path:
        sys.path.insert(0, str(ENGINE))
    import shopapi_common as sc
    assert callable(getattr(sc, "doc_v1_me", None))


def test_con_lai_anh_dem_o_img_backup_khong_dem_hut():
    """Sau finalize, ảnh đã thành video bị XOÁ khỏi `img/`.

    Đếm `img/` là đếm hụt và ô "CÒN LẠI" phình lên. Đo 11/08/2026: đếm `img/`
    ra 4.556 ảnh còn thiếu, sự thật là 1.889 — sai 2,4 lần, và sai theo hướng
    làm người vận hành tưởng còn cả núi việc chưa làm.
    """
    nguon = VE3_GUI.read_text(encoding="utf-8", errors="replace")
    moc = nguon.find("def _con_lai_va_ma_theo_pha")
    assert moc > 0
    than = nguon[moc:moc + 3500]
    assert "img_backup" in than, "dem con lai o img/ -> phinh len sau khi finalize"


def test_dong_cua_so_phai_GHI_LOG():
    """`_on_close` giết sạch subprocess rồi `destroy()` — im lặng thì y hệt crash.

    Worker thoát `exit code=1` đồng loạt, cửa sổ biến mất, không traceback,
    không sự kiện lỗi Windows. Ngày 11/08/2026 mất hai lượt chẩn đoán vì đúng
    chỗ này: cả hai lần "tool chết" đều là CỬA SỔ BỊ ĐÓNG, mà không có cách nào
    phân biệt với hỏng hóc thật.
    """
    nguon = VE3_GUI.read_text(encoding="utf-8", errors="replace")
    moc = nguon.find("def _on_close")
    assert moc > 0
    than = nguon[moc:moc + 1800]
    assert "self._log(" in than, "_on_close van cam nhu hen"
    assert "WM_DELETE_WINDOW" in than, "log khong noi ro day la lenh dong cua so"


# ── Mã chạy trắng phải bị ĐỖ LẠI, không được quay vòng vô tận ────────────────


def test_ma_chay_trang_nhieu_luot_thi_bi_do_lai():
    """Hàng chờ chỉ nhìn `success`, nên một đơn vị hỏng vĩnh viễn = vòng lặp bất tận.

    Đo log 12/08/2026: **126 lượt bật worker cho 21 mã**. TL3-0401 một mình 24
    lượt, mỗi lượt 4 giây và ra 0 sản phẩm — nó làm xong 61/62 đơn vị, đơn vị thứ
    62 hỏng vĩnh viễn, `success=False`, hàng chờ bật lại, lặp lại.

    Cái giá không nằm ở 4 giây đó mà ở CHỖ PAIR: mỗi lượt bật chiếm một trong 8
    chỗ. Cùng log có 1.426 dòng `skip no_free_pair`, và sản lượng 10 phút cuối là
    0 ảnh, 0 video.
    """
    import sys
    VE3 = Path(__file__).resolve().parents[1] / "tools" / "ve3"
    if str(VE3) not in sys.path:
        sys.path.insert(0, str(VE3))
    import ve3_gui

    class _App:
        # Kéo NGUYÊN mọi hằng + phương thức liên quan từ lớp thật. Chép tay từng
        # cái là bài kiểm đỏ mỗi lần sản phẩm thêm một hằng mới — đã xảy ra với
        # `HAN_DO_LAI_GIAY`.
        LUOT_TRANG_TOI_DA = ve3_gui.VE3App.LUOT_TRANG_TOI_DA
        HAN_DO_LAI_GIAY = getattr(ve3_gui.VE3App, "HAN_DO_LAI_GIAY", 3600)
        _ghi_so_luot_trang = ve3_gui.VE3App._ghi_so_luot_trang
        _ma_bi_do_lai = ve3_gui.VE3App._ma_bi_do_lai
        def _log(self, *a, **k):
            pass

    app = _App()
    trang = {"completed": 0, "failed": 1, "total": 1}
    for i in range(_App.LUOT_TRANG_TOI_DA - 1):
        app._ghi_so_luot_trang("TL3-0401", trang)
        assert not app._ma_bi_do_lai("TL3-0401"), "do lai qua som o luot {0}".format(i + 1)
    app._ghi_so_luot_trang("TL3-0401", trang)
    assert app._ma_bi_do_lai("TL3-0401"), "chay trang mai ma khong bao gio do lai"


def test_ra_duoc_san_pham_thi_XOA_het_luot_trang():
    """Hỏng thoáng qua (nhà máy vừa restart) không được cộng dồn thành đỗ lại."""
    import sys
    VE3 = Path(__file__).resolve().parents[1] / "tools" / "ve3"
    if str(VE3) not in sys.path:
        sys.path.insert(0, str(VE3))
    import ve3_gui

    class _App:
        # Kéo NGUYÊN mọi hằng + phương thức liên quan từ lớp thật. Chép tay từng
        # cái là bài kiểm đỏ mỗi lần sản phẩm thêm một hằng mới — đã xảy ra với
        # `HAN_DO_LAI_GIAY`.
        LUOT_TRANG_TOI_DA = ve3_gui.VE3App.LUOT_TRANG_TOI_DA
        HAN_DO_LAI_GIAY = getattr(ve3_gui.VE3App, "HAN_DO_LAI_GIAY", 3600)
        _ghi_so_luot_trang = ve3_gui.VE3App._ghi_so_luot_trang
        _ma_bi_do_lai = ve3_gui.VE3App._ma_bi_do_lai
        def _log(self, *a, **k):
            pass

    app = _App()
    app._ghi_so_luot_trang("MA", {"completed": 0, "failed": 1})
    app._ghi_so_luot_trang("MA", {"completed": 0, "failed": 1})
    app._ghi_so_luot_trang("MA", {"completed": 5, "failed": 1})   # có ra hàng
    for _ in range(_App.LUOT_TRANG_TOI_DA - 1):
        app._ghi_so_luot_trang("MA", {"completed": 0, "failed": 1})
    assert not app._ma_bi_do_lai("MA"), "khong xoa bo dem sau khi ra duoc san pham"


def test_bam_Reset_thi_go_co_do_lai():
    """Reset = 'thử lại mã này'. Không gỡ cờ thì reset xong vẫn bị bỏ qua."""
    nguon = VE3_GUI.read_text(encoding="utf-8", errors="replace")
    moc = nguon.find("Xac nhan Reset")
    than = nguon[moc:moc + 1200]
    assert "_luot_trang" in than, "Reset khong go co DO LAI -> nguoi dung bam ma khong hieu vi sao van bi bo qua"


def test_hang_cho_thuc_su_BO_QUA_ma_da_do_lai():
    nguon = VE3_GUI.read_text(encoding="utf-8", errors="replace")
    assert "_ma_bi_do_lai(pd.name)" in nguon, "co dem luot trang ma vong hang cho khong doc"


# ── Đếm sản lượng: hai lỗi đếm ĐÔI ───────────────────────────────────────────


def test_ma_goc_cat_dung_duoi_kho_luu():
    """Kho lưu đặt tên `TL3-0413_20260813_165010` — HAI đuôi, không phải một.

    `rsplit("_", 1)` chỉ rụng `_165010`, để lại `TL3-0413_20260813` — khác hẳn
    `TL3-0413` bên PROJECTS, nên phép chống-đếm-trùng không khớp. Đo 13/08/2026:
    24 mã nằm ở cả hai nơi và đều bị đếm hai lần.
    """
    import sys
    VE3 = Path(__file__).resolve().parents[1] / "tools" / "ve3"
    if str(VE3) not in sys.path:
        sys.path.insert(0, str(VE3))
    from ve3_gui import _ma_goc
    assert _ma_goc("TL3-0413_20260813_165010") == "TL3-0413"
    assert _ma_goc("TL3-0413") == "TL3-0413"
    # Đuôi KHÔNG phải ngày-giờ thì giữ nguyên, đừng cắt bừa tên của người ta.
    assert _ma_goc("TL2-0428_ban_nhap") == "TL2-0428_ban_nhap"
    assert _ma_goc("TL1-0745_2026") == "TL1-0745_2026"


def test_dem_video_KHU_TRUNG_theo_ten_file():
    """Xong việc thì mp4 được COPY sang `img/`, nên một sản phẩm nằm hai chỗ.

    Đo 13/08/2026 trên TL3-0413: `img/1.mp4` và `vid/1.mp4` cùng 3.089.785 byte,
    hash khớp — 49/49 file trùng tên. Đếm cả hai là nhân đôi sản lượng video, và
    con số phóng đại đó lên thẳng ô "VIDEO HÔM NAY" của giao diện.
    """
    # Đọc TRỌN thân hàm bằng AST. Bản trước cắt cứng 4.000 ký tự từ chỗ `def`,
    # nên chỉ cần thêm một khối chú thích ở phần đếm ảnh là `da_dem` rơi ra
    # ngoài lát cắt và bài này đỏ oan — đúng kiểu bài kiểm gác nhầm chỗ.
    nguon = VE3_GUI.read_text(encoding="utf-8", errors="replace")
    than = ast.get_source_segment(nguon, _ham("_count_production_today")) or ""
    assert than, "khong doc duoc than ham _count_production_today"
    assert "da_dem" in than, "khong khu trung mp4 -> video bi dem doi"
    # Chỉ soi ĐÚNG dòng duyệt hai thư mục mp4 — `pd / "img"` còn xuất hiện ở
    # phần đếm ảnh phía trên, tìm cả hàm là bắt nhầm.
    dong = [d for d in than.splitlines() if 'for sub in (pd /' in d]
    assert dong, "khong tim thay vong duyet thu muc mp4"
    assert dong[-1].find('"vid"') < dong[-1].find('"img"'), \
        "phai duyet vid/ TRUOC de ban goc thang, img/ chi la ban copy: " + dong[-1].strip()


def test_dem_san_luong_dedup_ca_MA_lan_FILE(tmp_path, monkeypatch):
    """Chạy thật trên cây thư mục giả: một mã ở hai nơi, mp4 ở hai thư mục."""
    import sys, time
    VE3 = Path(__file__).resolve().parents[1] / "tools" / "ve3"
    if str(VE3) not in sys.path:
        sys.path.insert(0, str(VE3))
    import ve3_gui

    du = tmp_path / "PROJECTS"
    kho = tmp_path / "old"
    for goc, ten in ((du, "TL9-0001"), (kho, "TL9-0001_20260813_165010")):
        d = goc / ten
        (d / "img_backup").mkdir(parents=True)
        (d / "vid").mkdir(parents=True)
        (d / "img").mkdir(parents=True)
        for i in range(3):
            (d / "img_backup" / f"{i}.png").write_bytes(b"x")
            (d / "vid" / f"{i}.mp4").write_bytes(b"y")
            (d / "img" / f"{i}.mp4").write_bytes(b"y")   # bản copy sau finalize

    monkeypatch.setattr(ve3_gui, "PROJECTS_DIR", du)
    monkeypatch.setattr(ve3_gui, "ARCHIVE_DIR", kho)

    class _App:
        _count_production_today = ve3_gui.VE3App._count_production_today

    anh, vid = _App()._count_production_today(tu_giay=time.time() - 3600)
    assert (anh, vid) == (3, 3), (
        "dem ra {0} anh / {1} video — dang le 3/3. Mot ma o hai noi + mp4 o hai "
        "thu muc = de dem gap 4 lan.".format(anh, vid))


# ── Máy KHÁC cập nhật qua GitHub phải nhận đúng trần song song ───────────────


def test_ba_num_API_co_MAC_DINH_trong_code():
    """`settings.yaml` KHÔNG theo dõi trong git và nằm trong `PROTECTED_PATHS`.

    Máy khác cập nhật sẽ không bao giờ nhận ba con số này qua file cấu hình —
    chúng phải sống trong code. Thiếu mặc định thì worker rơi về TRẦN CỨNG của
    loại job (ảnh 384), nhân 8 mã là **3.072 chỗ** — đúng con số đã giết nhà máy
    ngày 12/08/2026 (khai 3.072 luồng rồi tiến trình biến mất, 9 lần/ngày).
    """
    md = _setdefault("_load_config")
    assert md.get("max_concurrent") == 40, "thieu mac dinh tran ANH moi ma"
    assert md.get("shopapi_video_concurrency") == 16, "thieu mac dinh tran VIDEO moi ma"
    # `shopapi_ma_song_song` đặt bằng HẰNG SỐ chứ không phải số viết thẳng, nên
    # `literal_eval` của `_setdefault` bỏ qua — soi nguồn thay vì soi giá trị.
    nguon = VE3_GUI.read_text(encoding="utf-8", errors="replace")
    moc = nguon.find("def _load_config")
    assert 'setdefault("shopapi_ma_song_song"' in nguon[moc:moc + 6000], \
        "thieu mac dinh so ma song song trong _load_config"


def test_mac_dinh_KHONG_duoc_vuot_qua_muc_da_do():
    """Tải đặt lên nhà máy = (mã song song) × (trần mỗi mã). Giữ trong tầm đo được.

    Máy chủ đo được dựng 134 job ảnh đồng thời. Mặc định phải còn dư đầu cho
    AIMD chứ không nhảy thẳng lên hàng nghìn.
    """
    import sys
    VE3 = Path(__file__).resolve().parents[1] / "tools" / "ve3"
    if str(VE3) not in sys.path:
        sys.path.insert(0, str(VE3))
    import ve3_gui
    md = _setdefault("_load_config")
    ma = md.get("shopapi_ma_song_song") or ve3_gui.SHOPAPI_MA_SONG_SONG_MAC_DINH
    assert ma * md["max_concurrent"] <= 512, "tai anh vuot xa muc da do (134 dong thoi)"
    assert ma * md["shopapi_video_concurrency"] <= 256, "tai video vuot xa muc da do"


def test_file_mau_settings_co_ba_num():
    """Người dựng máy mới đọc `settings.example.yaml` — nó phải nói ra ba số này."""
    mau = (Path(__file__).resolve().parents[1] / "tools" / "ve3" / "config"
           / "settings.example.yaml").read_text(encoding="utf-8", errors="replace")
    for k in ("shopapi_ma_song_song", "max_concurrent", "shopapi_video_concurrency"):
        assert k in mau, "file mau thieu {0}".format(k)
    assert "generation_backend: shopapi" in mau, "file mau van tro ve duong Chrome cu"


# ── Máy cũ tự chuyển sang API — `settings.yaml` không đi theo bản cập nhật ───
#
# `tools/ve3/config/settings.yaml` bị chặn ba lớp khỏi mọi lần cập nhật:
# `updater.PROTECTED_PATHS`, `updater.GIT_PROTECTED_FILES`, và `.gitignore`.
# Ba lớp đều đúng — file đó giữ gmail|mật khẩu|totp của cả kho account. Nhưng
# hệ quả là **không có đường nào để bản cập nhật đổi được chế độ chạy**.
#
# Đã dính thật đêm 14/08/2026: máy thứ hai lên đúng phiên bản 527, `_sdk/` về
# đủ, mà chạy pool Chrome suốt đêm — `PHASE 4: Tao Video tu anh`, `sv5/...`,
# rồi 20 cảnh `FAIL (129.6s)` một lượt. Cấu hình nó vẫn `veo3top_b_pool`.


def _app_chuyen(cfg, tmp_path, monkeypatch):
    import sys
    VE3 = Path(__file__).resolve().parents[1] / "tools" / "ve3"
    if str(VE3) not in sys.path:
        sys.path.insert(0, str(VE3))
    import ve3_gui
    (tmp_path / "config").mkdir(parents=True, exist_ok=True)
    (tmp_path / "config" / "settings.yaml").write_text("cu: 1\n", encoding="utf-8")
    monkeypatch.setattr(ve3_gui, "VE3_DIR", tmp_path)
    monkeypatch.setattr(ve3_gui, "ghi_log_file", lambda *a, **k: None)

    class _App:
        _chuyen_may_cu_sang_api = ve3_gui.VE3App._chuyen_may_cu_sang_api

        def __init__(self):
            self.config_data = cfg
            self.da_luu = 0

        def _save_config(self):
            self.da_luu += 1

    return _App(), ve3_gui


def test_may_cu_de_pool_thi_duoc_CHUYEN_sang_api(tmp_path, monkeypatch):
    # ⚠ Ảnh là `pool`, video là `veo3top_b_pool` — HAI bộ từ vựng khác nhau.
    # Đây đúng là cấu hình của máy thứ hai đêm 14/08/2026.
    cfg = {"generation_backend": "veo3top_b_pool", "generation_mode": "veo3top_b_pool",
           "veo3top_image_mode": "pool"}
    app, ve3_gui = _app_chuyen(cfg, tmp_path, monkeypatch)
    app._chuyen_may_cu_sang_api()
    assert cfg["generation_backend"] == "shopapi"
    assert cfg["generation_mode"] == "shopapi", "may cu chi doc generation_mode -> phai doi ca hai"
    assert cfg["veo3top_image_mode"] == "shopapi"
    assert ve3_gui.cau_hinh_toan_api(cfg), "chuyen xong ma cong van bao khong phai che do API"
    assert app.da_luu == 1, "khong ghi xuong dia thi mo lai tool la mat"


def test_chuyen_xong_thi_KHONG_ep_lan_hai(tmp_path, monkeypatch):
    """Ai cố ý quay về pool sau đó phải được tôn trọng."""
    cfg = {"generation_backend": "veo3top_b_pool", "veo3top_image_mode": "pool"}
    app, _ = _app_chuyen(cfg, tmp_path, monkeypatch)
    app._chuyen_may_cu_sang_api()
    cfg["generation_backend"] = "veo3top_b_pool"      # người dùng tự chọn lại
    cfg["generation_mode"] = "veo3top_b_pool"
    app.da_luu = 0
    app._chuyen_may_cu_sang_api()
    assert cfg["generation_backend"] == "veo3top_b_pool", "ep lan hai la giat quyen cua nguoi dung"
    assert app.da_luu == 0


def test_chuyen_thi_CHEP_LUU_cau_hinh_cu_truoc(tmp_path, monkeypatch):
    cfg = {"generation_backend": "server", "veo3top_image_mode": "blank"}
    app, _ = _app_chuyen(cfg, tmp_path, monkeypatch)
    app._chuyen_may_cu_sang_api()
    luu = list((tmp_path / "config").glob("settings.yaml.truoc-api-*"))
    assert len(luu) == 1, "doi cau hinh ma khong chep luu -> khong co duong lui"
    assert luu[0].read_text(encoding="utf-8") == "cu: 1\n"


def test_backend_LA_KHONG_bi_doan_ho(tmp_path, monkeypatch):
    """Giá trị lạ = ai đó cắm tay. Để yên, chỉ đóng cửa không hỏi lại."""
    cfg = {"generation_backend": "mot_thu_la", "veo3top_image_mode": "mot_thu_la"}
    app, _ = _app_chuyen(cfg, tmp_path, monkeypatch)
    app._chuyen_may_cu_sang_api()
    assert cfg["generation_backend"] == "mot_thu_la"
    assert app.da_luu == 0


def test_may_da_o_api_san_thi_khong_dong_gi(tmp_path, monkeypatch):
    cfg = {"generation_backend": "shopapi", "veo3top_image_mode": "shopapi"}
    app, _ = _app_chuyen(cfg, tmp_path, monkeypatch)
    app._chuyen_may_cu_sang_api()
    assert app.da_luu == 0
    assert cfg["da_chuyen_sang_shopapi"] == _ve3_gui().CHUYEN_API_PHIEN
    assert not list((tmp_path / "config").glob("settings.yaml.truoc-api-*"))


def test_load_config_PHAI_GOI_chuyen_va_bao_duong_di():
    """Viết hàm mà quên gọi thì nó vô dụng y như chưa viết."""
    goi = {n.func.attr for n in ast.walk(_ham("_load_config"))
           if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)}
    assert "_chuyen_may_cu_sang_api" in goi
    assert "_bao_duong_di" in goi


def test_khoi_dong_PHAI_noi_ro_dang_di_duong_nao():
    """Cái ác đêm 14/08 không phải chạy pool — mà là KHÔNG DÒNG NÀO nói nó chạy pool."""
    nguon = VE3_GUI.read_text(encoding="utf-8", errors="replace")
    assert "[ĐƯỜNG ĐI]" in nguon, "khong co dong khai bao duong di luc khoi dong"
    than = ast.get_source_segment(nguon, _ham("_bao_duong_di")) or ""
    assert "pool cũ" in than, "khong noi ro khi dang di duong Chrome cu"
    assert "THIẾU" in than, "khong noi ro khi chon API ma thieu khoa"
    # Xếp hàng chứ không gọi thẳng: `_load_config` chạy TRƯỚC `_build()`.
    assert "_duong_di_cho_bao" in than
    assert "_duong_di_cho_bao" in (ast.get_source_segment(nguon, _ham("_boot")) or ""), \
        "xep hang roi khong ai nha ra thi khong ai doc duoc"


# ── Xoá khoá phải xoá THẬT, hoặc nói rõ là chưa ──────────────────────────────


def test_nut_la_XOA_KHOA_khong_phai_quen_khoa():
    nguon = VE3_GUI.read_text(encoding="utf-8", errors="replace")
    assert 'text="Xoa khoa"' in nguon
    assert 'text="Quen khoa"' not in nguon, "'Quen khoa' nghe nhu tam an, nguoi dung muon XOA"


def test_xoa_khoa_HOI_LAI_va_KIEM_LAI_sau_khi_xoa():
    """`quen_khoa()` chỉ xoá kho khoá của máy.

    Khoá đặt bằng `SHOPAPI_KEY` / `SHOPAPI_API_KEY` / `SHOPAPI_KEY_FILE` đứng
    TRƯỚC kho khoá trong `doc_khoa()`, nên vẫn còn nguyên sau khi xoá. Bấm xoá
    mà màn hình vẫn hiện khoá cũ và không một lời nào — người dùng tưởng đã gỡ
    khoá khỏi máy mà thật ra chưa. Đó là kiểu hỏng tệ hơn nút chết.
    """
    nguon = VE3_GUI.read_text(encoding="utf-8", errors="replace")
    than = ast.get_source_segment(nguon, _ham("_shopapi_forget_key")) or ""
    assert "askyesno" in than, "xoa khoa ma khong hoi lai"
    assert than.count("doc_khoa") >= 2, "phai doc_khoa LAI sau khi xoa de biet xoa hut hay khong"
    assert "SHOPAPI_KEY" in than, "xoa hut thi phai chi dich danh bien moi truong con giu khoa"
    assert "che_khoa" in than and "showinfo" in than and "showwarning" in than


# ── Hai bộ từ vựng backend: KHÔNG được gõ tay lần nữa ────────────────────────


def _ve3_gui():
    import sys
    VE3 = Path(__file__).resolve().parents[1] / "tools" / "ve3"
    if str(VE3) not in sys.path:
        sys.path.insert(0, str(VE3))
    import ve3_gui
    return ve3_gui


@pytest.mark.parametrize("anh", sorted({"", "blank", "account", "pool"}))
def test_MOI_backend_anh_cu_deu_duoc_chuyen(anh, tmp_path, monkeypatch):
    """Bẫy đã cắn thật: `veo3top_image_mode: pool` không khớp danh sách của VIDEO.

    Video dùng `veo3top_b_pool`, ảnh dùng `pool`. Bản đầu của
    `_chuyen_may_cu_sang_api` chỉ có một danh sách gõ tay — toàn giá trị video.
    Máy 528 chuyển được video, ảnh đứng yên, `cau_hinh_toan_api` đòi CẢ HAI nên
    vẫn False: tool tiếp tục tạo ảnh lẫn video bằng Chrome/pool, giao diện vẫn
    `TRẠM ẢNH`/`TRẠM VIDEO`, sửa xong y như chưa sửa.
    """
    cfg = {"generation_backend": "veo3top_b_pool", "veo3top_image_mode": anh}
    app, ve3_gui = _app_chuyen(cfg, tmp_path, monkeypatch)
    app._chuyen_may_cu_sang_api()
    assert cfg["veo3top_image_mode"] == "shopapi", "backend anh {0!r} bi bo quen".format(anh)
    assert ve3_gui.cau_hinh_toan_api(cfg), "cong API van dong -> van chay pool Chrome"


@pytest.mark.parametrize("vid", sorted({"server", "nanopic", "flowkit", "combined",
                                        "veo3top", "veo3top_b", "veo3top_b_ultra",
                                        "veo3top_b_pool"}))
def test_MOI_backend_video_cu_deu_duoc_chuyen(vid, tmp_path, monkeypatch):
    cfg = {"generation_backend": vid, "veo3top_image_mode": "pool"}
    app, ve3_gui = _app_chuyen(cfg, tmp_path, monkeypatch)
    app._chuyen_may_cu_sang_api()
    assert cfg["generation_backend"] == "shopapi", "backend video {0!r} bi bo quen".format(vid)
    assert ve3_gui.cau_hinh_toan_api(cfg)


def test_danh_sach_backend_cu_SUY_RA_tu_bang_chon_khong_go_tay():
    """Thêm backend mới vào bảng chọn thì phần chuyển đường phải tự biết.

    `VE3_SUITE` liệt kê backend bằng tay ở gần chục chỗ, và mỗi lần thêm một
    backend là một lớp lặng lẽ bỏ sót nó. Ở đây thì không: hai tập "backend cũ"
    được sinh ra từ chính hai bảng chọn.
    """
    g = _ve3_gui()
    assert g.BACKEND_VIDEO_CU == frozenset(
        v for v in g.BACKEND_VIDEO.values() if v != "shopapi")
    assert g.BACKEND_ANH_CU == frozenset(
        v for v in g.BACKEND_ANH.values() if v != "shopapi")
    assert "shopapi" not in g.BACKEND_VIDEO_CU and "shopapi" not in g.BACKEND_ANH_CU
    # Bằng chứng hai bộ từ vựng THẬT SỰ khác nhau — đừng gộp làm một lần nữa.
    assert "pool" in g.BACKEND_ANH_CU and "pool" not in g.BACKEND_VIDEO_CU
    assert "veo3top_b_pool" in g.BACKEND_VIDEO_CU and "veo3top_b_pool" not in g.BACKEND_ANH_CU
    assert "" in g.BACKEND_ANH_CU, "'Mac dinh' cua anh la duong cu, phai duoc chuyen"


def test_Settings_dung_CHUNG_bang_chon_o_cap_module():
    """Bảng chọn dựng riêng trong `SettingsPage` thì hai bên lệch nhau lúc nào không hay."""
    nguon = VE3_GUI.read_text(encoding="utf-8", errors="replace")
    assert "self.generation_backend_options = dict(BACKEND_VIDEO)" in nguon
    assert "self.image_backend_options = dict(BACKEND_ANH)" in nguon


# ── Cờ "đã chuyển" không được tự khoá lấy bản vá của chính nó ────────────────


def test_may_da_chuyen_HUT_boi_ban_cu_thi_duoc_chuyen_LAI(tmp_path, monkeypatch):
    """Đúng trạng thái máy thứ hai mắc kẹt ở bản 529, đo lúc 02:4x ngày 15/08/2026.

    Bản 528 chuyển hụt (được video, sót ảnh vì lẫn hai bộ từ vựng) rồi đóng cờ
    `da_chuyen_sang_shopapi: True`. Bản 529 sửa đúng logic nhưng vừa vào hàm đã
    gặp cờ và quay ra. Máy nằm chết ở `ảnh=pool · video=shopapi` — nửa vời, mà
    nửa đó đủ để `cau_hinh_toan_api` trả False nên vẫn chạy Chrome cho CẢ HAI.

    Cờ một-bit không phân biệt nổi "đã chuyển bằng bản hỏng" với "đã chuyển
    bằng bản đúng". Con số thì phân biệt được.
    """
    cfg = {"generation_backend": "shopapi", "generation_mode": "shopapi",
           "veo3top_image_mode": "pool", "da_chuyen_sang_shopapi": True}
    app, ve3_gui = _app_chuyen(cfg, tmp_path, monkeypatch)
    app._chuyen_may_cu_sang_api()
    assert cfg["veo3top_image_mode"] == "shopapi", "co cua ban hong dang khoa chinh ban va"
    assert ve3_gui.cau_hinh_toan_api(cfg)
    assert cfg["da_chuyen_sang_shopapi"] == ve3_gui.CHUYEN_API_PHIEN
    assert app.da_luu == 1


def test_da_chuyen_bang_ban_HIEN_TAI_thi_khong_dong_lai(tmp_path, monkeypatch):
    """Người dùng cố ý quay về pool sau bản đúng thì phải được tôn trọng."""
    g = _ve3_gui()
    cfg = {"generation_backend": "veo3top_b_pool", "veo3top_image_mode": "pool",
           "da_chuyen_sang_shopapi": g.CHUYEN_API_PHIEN}
    app, _ = _app_chuyen(cfg, tmp_path, monkeypatch)
    app._chuyen_may_cu_sang_api()
    assert cfg["generation_backend"] == "veo3top_b_pool"
    assert app.da_luu == 0


@pytest.mark.parametrize("co,mong_doi", [
    (None, 0), (True, 1), (False, 0), (1, 1), (2, 2), ("2", 2), ("rac", 0), ([], 0),
])
def test_doc_co_chuyen_chiu_duoc_moi_kieu_gia_tri(co, mong_doi):
    """Cờ đi qua yaml nên có thể về dưới đủ kiểu. Đọc hụt = chuyển nhầm hoặc kẹt."""
    g = _ve3_gui()
    assert g._phien_da_chuyen({} if co is None else {"da_chuyen_sang_shopapi": co}) == mong_doi


def test_moi_lan_sua_phep_chuyen_phai_TANG_so_phien():
    """Sửa `_chuyen_may_cu_sang_api` mà quên tăng số = máy đã chuyển hụt kẹt vĩnh viễn."""
    g = _ve3_gui()
    assert g.CHUYEN_API_PHIEN >= 2, (
        "ban 1 chuyen hut vi lan hai bo tu vung backend; may dinh ban do chi thoat ra "
        "duoc khi CHUYEN_API_PHIEN > 1"
    )
    nguon = VE3_GUI.read_text(encoding="utf-8", errors="replace")
    than = ast.get_source_segment(nguon, _ham("_chuyen_may_cu_sang_api")) or ""
    assert "CHUYEN_API_PHIEN" in than and "_phien_da_chuyen" in than, \
        "quay ve co mot-bit la lap lai dung cai bay cu"
    assert 'da_chuyen_sang_shopapi"] = True' not in than, \
        "van con dong co bang True — may sau nay lai kep chinh no lan nua"


# ── Sản lượng ẢNH: ba thư mục, không phải một ────────────────────────────────


def _cay_du_an(tmp_path, monkeypatch):
    """Dựng `PROJECTS/` + `old/` giả và trả về hàm đếm đã trỏ vào đó."""
    import sys
    VE3 = Path(__file__).resolve().parents[1] / "tools" / "ve3"
    if str(VE3) not in sys.path:
        sys.path.insert(0, str(VE3))
    import ve3_gui
    du, kho = tmp_path / "PROJECTS", tmp_path / "old"
    du.mkdir(); kho.mkdir()
    monkeypatch.setattr(ve3_gui, "PROJECTS_DIR", du)
    monkeypatch.setattr(ve3_gui, "ARCHIVE_DIR", kho)

    class _App:
        _count_production_today = ve3_gui.VE3App._count_production_today

    return du, _App()._count_production_today


def test_anh_NHAN_VAT_va_THUMBNAIL_cung_la_san_luong(tmp_path, monkeypatch):
    """Ảnh cảnh chỉ là phần lớn nhất, không phải toàn bộ.

    `nv/` (PHASE 1 sinh ảnh nhân vật/bối cảnh từ prompt tham chiếu) và `thumb/`
    (`_generate_thumbnail` sinh từ sheet thumbnail) đều là job ảnh gửi lên máy
    chủ và đều tính tiền. Đo 14/08/2026 trên 24 mã thật: 2.367 cảnh + 24 nhân
    vật + 72 thumbnail = 2.463. Chỉ đếm cảnh là báo hụt 96 ảnh (3,9%), và hụt
    đúng ở phần đắt — ảnh nhân vật phải qua vòng kiểm/sửa media_id.
    """
    import time
    du, dem = _cay_du_an(tmp_path, monkeypatch)
    d = du / "TL9-0001"
    for sub in ("img_backup", "nv", "thumb", "vid"):
        (d / sub).mkdir(parents=True)
    for i in range(5):
        (d / "img_backup" / "{0}.png".format(i)).write_bytes(b"x")
    (d / "nv" / "nv1.png").write_bytes(b"x")
    for i in (1, 2, 3):
        (d / "thumb" / "thumb_{0:03d}.png".format(i)).write_bytes(b"x")
    (d / "vid" / "1.mp4").write_bytes(b"y")

    anh, vid = dem(tu_giay=time.time() - 3600)
    assert anh == 9, "dem ra {0} — dang le 5 canh + 1 nhan vat + 3 thumbnail = 9".format(anh)
    assert vid == 1


def test_thumbnail_CHEP_tu_nhan_vat_KHONG_duoc_dem(tmp_path, monkeypatch):
    """`_fallback_copy_thumbnail_from_character` ghi `thumb/{MÃ}.png`.

    Nó CHÉP từ `nv/` chứ không sinh ảnh mới — đếm nó là đếm hai lần cùng một
    tấm. Chỉ `thumb_*` mới là ảnh do máy chủ sinh.
    """
    import time
    du, dem = _cay_du_an(tmp_path, monkeypatch)
    d = du / "TL9-0002"
    (d / "nv").mkdir(parents=True)
    (d / "thumb").mkdir(parents=True)
    (d / "nv" / "nv1.png").write_bytes(b"x")
    (d / "thumb" / "TL9-0002.png").write_bytes(b"x")     # bản chép — KHÔNG tính
    (d / "thumb" / "thumb_001.png").write_bytes(b"x")    # ảnh thật

    anh, _ = dem(tu_giay=time.time() - 3600)
    assert anh == 2, "dem ra {0} — dang le 1 nhan vat + 1 thumbnail that = 2".format(anh)


def test_anh_canh_van_lay_o_img_backup_khi_co(tmp_path, monkeypatch):
    """Sau finalize, `img/` đã xoá png thành video — đếm `img/` là đếm hụt."""
    import time
    du, dem = _cay_du_an(tmp_path, monkeypatch)
    d = du / "TL9-0003"
    (d / "img_backup").mkdir(parents=True)
    (d / "img").mkdir(parents=True)
    for i in range(4):
        (d / "img_backup" / "{0}.png".format(i)).write_bytes(b"x")
        (d / "img" / "{0}.mp4".format(i)).write_bytes(b"y")   # png đã thành mp4

    anh, vid = dem(tu_giay=time.time() - 3600)
    assert (anh, vid) == (4, 4)


def test_chua_finalize_thi_van_dem_duoc_anh_o_img(tmp_path, monkeypatch):
    """`_finalize_img` chép sang backup TRƯỚC rồi mới xoá — không có khe mất ảnh."""
    import time
    du, dem = _cay_du_an(tmp_path, monkeypatch)
    d = du / "TL9-0004"
    (d / "img").mkdir(parents=True)
    for i in range(3):
        (d / "img" / "{0}.png".format(i)).write_bytes(b"x")

    anh, _ = dem(tu_giay=time.time() - 3600)
    assert anh == 3


def test_BAN_SAO_THU_BA_o_AUTO_visual_KHONG_duoc_dem(tmp_path, monkeypatch):
    """Mỗi mã xong được chép làm BA bản, và bản thứ ba dễ bị quên.

    `_archive_project` chạy `copytree` hai lần: một vào `old/<MÃ>_<dấu thời
    gian>`, một vào `D:\AUTO\visual\<MÃ>` (`EDIT_VISUAL_DIR`). Bản ở
    `visual/` mang tên MÃ TRẦN, không có dấu thời gian — nên nếu ai đó đưa nó
    vào vòng đếm thì phép chống-trùng theo mã vẫn khớp và không lộ ra, nhưng
    một mã nằm ở PROJECTS + old + visual thì tổng phồng lên gấp bội.
    """
    import sys
    VE3 = Path(__file__).resolve().parents[1] / "tools" / "ve3"
    if str(VE3) not in sys.path:
        sys.path.insert(0, str(VE3))
    import ve3_gui
    nguon = VE3_GUI.read_text(encoding="utf-8", errors="replace")
    than = ast.get_source_segment(nguon, _ham("_count_production_today")) or ""
    assert "EDIT_VISUAL_DIR" not in than, (
        "visual/ la BAN SAO cua PROJECTS — dem no la nhan doi san luong")
    assert "PROJECTS_DIR" in than and "ARCHIVE_DIR" in than
