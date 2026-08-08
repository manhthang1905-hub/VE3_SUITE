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
    # Chu du an chot `claude-sonnet-5`. Do that cung ung ho: `claude-fable-5`
    # dang tra 503 engine_unavailable, con sonnet-5 chay on va re nhat.
    assert eng.api_model == "claude-sonnet-5"


def test_doi_duoc_model_khi_muon_khac(Engine):
    eng = Engine({"claude_cli_backend": "api_shop", "shopapi_model": "claude-opus-5"})
    assert eng.api_model == "claude-opus-5"


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


# ── Chính sách thử lại ───────────────────────────────────────────────────────
#
# ĐO THẬT 07/08/2026: 20 lượt gọi ở 5 luồng song song vào api.shopapi.vn ->
# 17 lượt hỏng vì `502 service_unavailable` / `503 engine_unavailable`. Máy chủ
# tự nói "Vui lòng thử lại sau ít phút" trong khi bản cũ chờ 1s rồi 2s rồi bỏ.
# Pipeline Excel chạy `chunk_parallel` luồng nên nó CHẮC CHẮN chạm mức đó.


class _DemResp:
    """Đếm số lần bị gọi, luôn trả cùng một mã."""

    def __init__(self, ma, hop=None, retry_after=None):
        self.status_code = ma
        self.text = "loi gia"
        self.headers = {"Retry-After": str(retry_after)} if retry_after else {}
        self._hop = hop if hop is not None else {}
        self._hop["n"] = self._hop.get("n", 0)

    def iter_lines(self):
        return iter(())

    def close(self):
        pass


def _dem_lan_goi(monkeypatch, ma, retry_after=None):
    """Trả `hop` có `hop['n']` = số lần `requests.post` thực sự bị gọi."""
    import requests as _rq, time as _t
    hop = {"n": 0, "ngu": []}

    def post(*a, **k):
        hop["n"] += 1
        return _DemResp(ma, retry_after=retry_after)

    monkeypatch.setattr(_rq, "post", post)
    monkeypatch.setattr(_t, "sleep", lambda s: hop["ngu"].append(s))
    return hop


@pytest.mark.parametrize("ma", sorted({400, 401, 402, 403, 404, 409, 422}))
def test_loi_do_minh_gui_sai_thi_hong_NGAY_khong_thu_lai(Engine, monkeypatch, ma):
    """Gửi lại y hệt thì ra y hệt. Thử lại chỉ tổ chậm — và với 402 (hết tiền)
    còn làm người đọc log tưởng bị trừ tiền nhiều lần."""
    hop = _dem_lan_goi(monkeypatch, ma)
    eng = Engine({"claude_cli_backend": "api_shop", "shopapi_api_key": "sk_live_gia",
                  "shopapi_model_chain": []})

    with pytest.raises(RuntimeError, match=f"HTTP {ma}"):
        eng._run_via_api("bat ky")
    assert hop["n"] == 1, f"HTTP {ma} KHONG duoc thu lai, nhung da goi {hop['n']} lan"
    assert hop["ngu"] == [], "khong duoc cho mot giay nao cho loi khong the cuu"


@pytest.mark.parametrize("ma", [429, 500, 502, 503])
def test_loi_nghen_thi_thu_lai_du_so_lan(Engine, monkeypatch, ma):
    """502/503 là thứ đã làm hỏng 17/20 lượt — phải kiên nhẫn, không bỏ sớm."""
    hop = _dem_lan_goi(monkeypatch, ma)
    eng = Engine({"claude_cli_backend": "api_shop", "shopapi_api_key": "sk_live_gia",
                  "claude_cli_api_retries": 5, "shopapi_model_chain": []})

    with pytest.raises(RuntimeError, match=f"HTTP {ma}"):
        eng._run_via_api("bat ky")
    assert hop["n"] == 5


def test_quang_cho_dai_dan_va_co_tran(Engine, monkeypatch):
    hop = _dem_lan_goi(monkeypatch, 503)
    eng = Engine({"claude_cli_backend": "api_shop", "shopapi_api_key": "sk_live_gia",
                  "claude_cli_api_retries": 8, "shopapi_model_chain": []})

    with pytest.raises(RuntimeError):
        eng._run_via_api("bat ky")

    # So phan NGUYEN: moi quang cho co pha ngau nhien ~1s chong dong bo, nen hai
    # quang lien tiep da cham tran co the lech nhau chut - do khong phai loi.
    cho = [int(s) for s in hop["ngu"]]
    assert cho == sorted(cho), f"quang cho phai DAI DAN: {hop['ngu']}"
    assert cho[0] >= 2, "nhip dau phai >=2s; 1s la qua ngan cho 'thu lai sau it phut'"
    assert max(cho) <= 46, "phai co tran, khong duoc cho vo tan"
    assert sum(cho) >= 60, "tong kien nhan phai qua duoc mot nhip nghen"


def test_retry_after_cua_may_chu_thang_con_so_tu_tinh(Engine, monkeypatch):
    """Máy chủ biết rõ hơn ta bao giờ nó rảnh."""
    hop = _dem_lan_goi(monkeypatch, 503, retry_after=30)
    eng = Engine({"claude_cli_backend": "api_shop", "shopapi_api_key": "sk_live_gia",
                  "claude_cli_api_retries": 3, "shopapi_model_chain": []})

    with pytest.raises(RuntimeError):
        eng._run_via_api("bat ky")
    assert all(s >= 30 for s in hop["ngu"]), f"phai ton trong Retry-After: {hop['ngu']}"


def test_retry_after_rac_thi_bo_qua_chu_khong_no(Engine, monkeypatch):
    hop = _dem_lan_goi(monkeypatch, 503, retry_after="lat nua nhe")
    eng = Engine({"claude_cli_backend": "api_shop", "shopapi_api_key": "sk_live_gia",
                  "claude_cli_api_retries": 2, "shopapi_model_chain": []})

    with pytest.raises(RuntimeError):
        eng._run_via_api("bat ky")
    assert hop["n"] == 2


def test_nghen_roi_hoi_lai_duoc_thi_van_tra_ket_qua(Engine, monkeypatch):
    """503 một nhịp rồi máy chủ tỉnh -> phải ra kết quả, KHÔNG được bỏ cuộc."""
    import requests as _rq, time as _t
    hop = {"n": 0}

    def post(*a, **k):
        hop["n"] += 1
        if hop["n"] == 1:
            return _DemResp(503)
        return _RespGia(201, ['data: {"choices":[{"delta":{"content":"xong"}}]}',
                              "data: [DONE]"])

    monkeypatch.setattr(_rq, "post", post)
    monkeypatch.setattr(_t, "sleep", lambda *_: None)

    eng = Engine({"claude_cli_backend": "api_shop", "shopapi_api_key": "sk_live_gia"})
    assert eng._run_via_api("bat ky") == "xong"
    assert hop["n"] == 2


# ── Chuỗi model dự phòng ─────────────────────────────────────────────────────
#
# ĐO THẬT 07/08/2026 — gọi `claude-sonnet-5` và `claude-opus-5` CÙNG LÚC, 12 vòng:
#     ít nhất một model sống : 12/12
#     cả hai cùng chết       : 0/12
# Các model nằm sau cụm xử lý khác nhau nên nghẽn ĐỘC LẬP. Bám chết một model là
# tự nhận tỉ lệ hỏng của riêng nó; đổi model thì gần như luôn có đường đi.


def test_model_chinh_nghen_thi_doi_sang_model_du_phong(Engine, monkeypatch):
    import requests as _rq, time as _t
    da_goi = []

    def post(url, headers=None, json=None, **k):
        m = (json or {}).get("model")
        da_goi.append(m)
        if m == "claude-sonnet-5":
            return _DemResp(503)
        return _RespGia(200, ['data: {"choices":[{"delta":{"content":"cuu duoc"}}]}',
                              "data: [DONE]"])

    monkeypatch.setattr(_rq, "post", post)
    monkeypatch.setattr(_t, "sleep", lambda *_: None)

    eng = Engine({"claude_cli_backend": "api_shop", "shopapi_api_key": "sk_live_gia",
                  "claude_cli_api_retries": 2})
    assert eng._run_via_api("bat ky") == "cuu duoc"
    assert "claude-opus-5" in da_goi, f"phai da thu model du phong: {da_goi}"


def test_loi_khong_cuu_duoc_thi_KHONG_doi_model(Engine, monkeypatch):
    """Sai khoá / hết tiền / prompt hỏng: đổi model cũng ra y hệt, chỉ tốn thời gian."""
    hop = _dem_lan_goi(monkeypatch, 401)
    eng = Engine({"claude_cli_backend": "api_shop", "shopapi_api_key": "sk_live_gia"})

    with pytest.raises(RuntimeError, match="HTTP 401"):
        eng._run_via_api("bat ky")
    assert hop["n"] == 1, f"401 phai dung ngay, khong doi model: {hop['n']} lan goi"


def test_chuoi_model_bo_trung_va_giu_thu_tu(Engine):
    eng = Engine({"claude_cli_backend": "api_shop",
                  "shopapi_model": "claude-opus-5"})
    ds = eng._chuoi_model()
    assert ds[0] == "claude-opus-5", "model chu du an chon phai di TRUOC"
    assert len(ds) == len(set(ds)), f"khong duoc lap model: {ds}"


def test_tat_duoc_chuoi_model(Engine):
    eng = Engine({"claude_cli_backend": "api_shop", "shopapi_model_chain": []})
    assert eng._chuoi_model() == ["claude-sonnet-5"]


def test_nhanh_khac_khong_bi_gan_chuoi_model_cua_shopapi(Engine):
    """VOV/DeepSeek đã có cơ chế riêng — không được lén đổi hành vi của chúng."""
    eng = Engine({"claude_cli_backend": "api_ds"})
    assert eng._chuoi_model() == [eng.api_model]


# ── Thứ tự leo thang: ĐỔI MODEL trước, CHỜ sau ───────────────────────────────
#
# Bản đầu vắt kiệt 6 lần thử trên MỘT model (2+4+8+16+32 = 62 giây ngủ) RỒI mới
# đổi model. Log chạy thật: mot khuc mat ~110 giay ma gan het la nam cho, trong
# khi model kia dang tra loi binh thuong.
#
# `engine_unavailable` nghia la DUNG CUM DO dang chet -> ngoi cho chinh no hoi
# la cho thu kho toi nhat. Va da do: ca hai model cung chet 0/12 vong.


def test_doi_model_NGAY_khong_ngu_truoc(Engine, monkeypatch):
    """Sonnet 503 -> Opus phải được gọi ngay, KHÔNG chờ một giây nào."""
    import requests as _rq, time as _t
    da_goi, da_ngu = [], []

    def post(url, headers=None, json=None, **k):
        m = (json or {}).get("model")
        da_goi.append(m)
        if m == "claude-sonnet-5":
            return _DemResp(503)
        return _RespGia(200, ['data: {"choices":[{"delta":{"content":"xong"}}]}',
                              "data: [DONE]"])

    monkeypatch.setattr(_rq, "post", post)
    monkeypatch.setattr(_t, "sleep", lambda s: da_ngu.append(s))

    eng = Engine({"claude_cli_backend": "api_shop", "shopapi_api_key": "sk_live_gia"})
    assert eng._run_via_api("bat ky") == "xong"
    assert da_goi == ["claude-sonnet-5", "claude-opus-5"], da_goi
    assert da_ngu == [], f"doi model thi KHONG duoc ngu truoc, da ngu: {da_ngu}"


def test_chi_ngu_khi_CA_CHUOI_model_deu_nghen(Engine, monkeypatch):
    """Cả hai cùng chết mới đáng nằm chờ — và mỗi vòng phải thử LẠI cả hai."""
    import requests as _rq, time as _t
    da_goi, da_ngu = [], []

    def post(url, headers=None, json=None, **k):
        da_goi.append((json or {}).get("model"))
        return _DemResp(503)

    monkeypatch.setattr(_rq, "post", post)
    monkeypatch.setattr(_t, "sleep", lambda s: da_ngu.append(s))

    eng = Engine({"claude_cli_backend": "api_shop", "shopapi_api_key": "sk_live_gia",
                  "claude_cli_api_retries": 3})
    with pytest.raises(RuntimeError):
        eng._run_via_api("bat ky")

    # 3 vong x N model; ngu 2 lan (giua cac vong, khong ngu sau vong chot).
    so_model = len(eng._chuoi_model())
    assert so_model >= 4, "chuoi phai gom MOI model shopapi, xem _CHUOI_MODEL_MAC_DINH"
    assert len(da_goi) == 3 * so_model, da_goi
    for m in eng._chuoi_model():
        assert da_goi.count(m) == 3, f"moi vong phai thu lai {m}: {da_goi}"
    assert len(da_ngu) == 2, f"chi ngu giua cac vong: {da_ngu}"


def test_loi_khong_cuu_duoc_dung_ngay_khong_thu_model_con_lai(Engine, monkeypatch):
    hop = _dem_lan_goi(monkeypatch, 402)
    eng = Engine({"claude_cli_backend": "api_shop", "shopapi_api_key": "sk_live_gia"})

    with pytest.raises(RuntimeError, match="HTTP 402"):
        eng._run_via_api("bat ky")
    assert hop["n"] == 1, f"het tien thi dung ngay, doi model cung the: {hop['n']} lan"


# ── Chuỗi model phải gồm MỌI model shopapi ───────────────────────────────────
#
# ĐO THẬT 08/08/2026, dung luc chu du an chay that:
#     claude-sonnet-5  0/4  [503, 503, 503, 503]
#     claude-opus-5    0/4  [503, 503, 503, 503]
#     claude-fable-5   2/4  [503, 503, 200, 200]   <-- con DUY NHAT con song
# Chuoi hai model truot sach du ngay canh co model dang phuc vu. Mot phut sau
# ca bon deu 200 -> cua so nghen RAT NGAN va RAI KHONG DEU giua cac cum.


def test_chuoi_gom_moi_model_shopapi(Engine):
    ds = Engine({"claude_cli_backend": "api_shop"})._chuoi_model()
    for m in ("claude-sonnet-5", "claude-opus-5", "claude-fable-5", "gpt-5.6"):
        assert m in ds, f"thieu {m} -> mat mot cua thoat khi cum khac chet: {ds}"


def test_re_truoc_dat_sau(Engine):
    """Chỉ leo lên model đắt khi model rẻ thật sự không dùng được."""
    ds = Engine({"claude_cli_backend": "api_shop"})._chuoi_model()
    assert ds.index("claude-sonnet-5") < ds.index("claude-opus-5") < ds.index("claude-fable-5")


def test_hai_model_dau_chet_thi_model_thu_ba_van_cuu_duoc(Engine, monkeypatch):
    """Dung kich ban da xay ra that: sonnet + opus cung 503, fable con song."""
    import requests as _rq, time as _t
    da_goi = []

    def post(url, headers=None, json=None, **k):
        m = (json or {})["model"]
        da_goi.append(m)
        if m in ("claude-sonnet-5", "claude-opus-5"):
            return _DemResp(503)
        return _RespGia(200, ['data: {"choices":[{"delta":{"content":"cuu duoc"}}]}',
                              "data: [DONE]"])

    monkeypatch.setattr(_rq, "post", post)
    monkeypatch.setattr(_t, "sleep", lambda *_: None)

    eng = Engine({"claude_cli_backend": "api_shop", "shopapi_api_key": "sk_live_gia"})
    assert eng._run_via_api("bat ky") == "cuu duoc"
    assert da_goi[:3] == ["claude-sonnet-5", "claude-opus-5", "claude-fable-5"], da_goi


def test_hai_ban_chuoi_model_phai_KHOP_nhau(Engine):
    """`ve3_worker._call_shopapi_rewrite` giữ một bản chép của cùng danh sách —
    lệch nhau là một bên mất cửa thoát mà không ai thấy."""
    import re as _re
    from pathlib import Path as _P

    ds_engine = Engine({"claude_cli_backend": "api_shop"})._chuoi_model()
    nguon = (_P(__file__).resolve().parents[1] / "tools" / "ve3" / "ve3_worker.py").read_text(
        encoding="utf-8", errors="replace")
    khoi = nguon.split('shopapi_model_chain") or [', 1)[1].split("]", 1)[0]
    ds_worker = _re.findall(r'"([^"]+)"', khoi)
    assert ds_worker == ds_engine, f"engine={ds_engine} worker={ds_worker}"
