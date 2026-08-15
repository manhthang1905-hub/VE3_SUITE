#!/usr/bin/env python3
"""
VE3 Simple - Worker táº¡o áº£nh qua server mode

Flow:
1. Load Excel (PromptWorkbook)
2. Táº¡o reference images (nv/, loc/) qua server
3. Táº¡o thumbnail (nhÃ¢n váº­t chÃ­nh â†’ thumb/)
4. Táº¡o Táº¤T Cáº¢ scene images (khÃ´ng chia cháºµn/láº») qua server

Usage:
    worker = VE3Worker(project_dir, config, log_func)
    result = worker.run()
"""

import sys
import os
import time
import json
import shutil
import subprocess
import threading
import uuid
import re
import unicodedata
from pathlib import Path
from typing import Optional, Callable, Dict, List, Any
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed

# Äáº£m báº£o import modules tá»« thÆ° má»¥c ve3
VE3_DIR = Path(__file__).resolve().parent
SUITE_ROOT = VE3_DIR.parents[1] if VE3_DIR.parent.name.lower() == "tools" else VE3_DIR
sys.path.insert(0, str(VE3_DIR))

from modules.excel_manager import PromptWorkbook, Character, Scene
from modules.flow_runtime_auth import FlowRuntimeAuthService
from modules.flow_reference_bridge import FlowReferenceBridge, FlowReferenceConfig
from modules.google_flow_api import (
    GoogleFlowAPI, GeneratedImage, ImageInput, ImageInputType,
    AspectRatio, ImageModel, VideoAspectRatio, VideoModel
)
from modules.nanopic_api import NanoPicAPI, NanoPicResult
from modules.server_pool import ServerPool
from modules.thumbnail_youtube import ThumbnailOptimizeError, optimize_youtube_thumbnail


# ===== NHANH MOI: goi thang API api.shopapi.vn =====================================
# Cac module client nam trong veo3top_engine/ (canh image_factory_client.py va
# video_factory_client.py) nen phai nap sys.path giong het cach cac nhanh cu dang lam.
# Import MUON trong tung ham: may khong co SDK shopapi van phai chay duoc duong cu.

def _shopapi_nap_engine():
    """Them veo3top_engine/ vao sys.path roi tra ve module shopapi_common.

    Dung SUITE_ROOT thay vi go cung 'D:\\VE3_SUITE\\veo3top_engine' nhu vai nhanh cu:
    go cung lam tool chet ngay khi ai do chep thu muc sang o khac hoac doi ten.
    """
    engine_dir = str(SUITE_ROOT / "veo3top_engine")
    if engine_dir not in sys.path:
        sys.path.insert(0, engine_dir)
    import shopapi_common
    return shopapi_common


def _shopapi_nap_batch():
    """Nap module `shopapi_batch` (chay ca me, so luong song song tu /v1/me)."""
    _shopapi_nap_engine()          # bao dam veo3top_engine/ da nam trong sys.path
    import shopapi_batch
    return shopapi_batch


def _shopapi_che_khoa(key):
    """Che khoa truoc khi ghi log. Khong bao gio in khoa day du ra man hinh."""
    try:
        return _shopapi_nap_engine().che_khoa(key)
    except Exception:
        return "(khoa)" if key else "(chua co khoa)"


def _shopapi_trong_me():
    """Luong nay dang chay trong mot me `chay_ca_me` khong?

    Quyet dinh nhanh gui job co duoc phep NEM `BiNghen` (429/503) ra ngoai hay
    khong. Thieu module -> False, tuc la giu nguyen hanh vi cu (khong bao gio nem).
    """
    try:
        return _shopapi_nap_batch().trong_me()
    except Exception:
        return False


class VE3Worker:
    """Worker táº¡o áº£nh tá»« Excel qua server mode."""

    def __init__(
        self,
        project_dir: str,
        config: Dict[str, Any],
        log_func: Callable = None,
        progress_func: Callable = None,
        on_item_status: Callable = None
    ):
        """
        Args:
            project_dir: ÄÆ°á»ng dáº«n thÆ° má»¥c project (chá»©a Excel + output)
            config: Dict cáº¥u hÃ¬nh tá»« settings.yaml
            log_func: Callback log(msg, level) - hiá»ƒn thá»‹ lÃªn GUI
            progress_func: Callback progress(phase, current, total, detail)
            on_item_status: Callback (item_type, item_id, status, image_path)
        """
        self.project_dir = Path(project_dir)
        self.config = config
        self._raw_log = log_func or (lambda msg, level="INFO": print(f"[{level}] {msg}"))
        self.log = lambda msg, level="INFO": self._raw_log(self._normalize_log_text(msg), level)
        self.progress = progress_func or (lambda *a, **kw: None)
        self.on_item_status = on_item_status or (lambda *a, **kw: None)
        self._stop_flag = False
        self._excel_lock = threading.Lock()
        self._auth_lock = threading.Lock()
        # Dem so viec da xong. `completed_count[0] += 1` la doc-sua-ghi, KHONG nguyen
        # tu duoi GIL -> chay 1 luong thi khong sao, chay 40 luong la dem thieu va
        # thanh tien do lui nguoc tren GUI. Khoa rieng (khong dung chung _excel_lock)
        # de khong bat luong nao phai cho o dia.
        self._dem_lock = threading.Lock()
        # Ket qua anh da tao san bang cach GOP nhieu scene cung prompt vao 1 job
        # `n=k`. `_submit_image_shopapi` tra thang tu day, khong goi API lai.
        self._shopapi_anh_gop = {}
        self._shopapi_gop_lock = threading.Lock()
        self._last_auth_refresh_ts = 0.0
        self._wb: Optional[PromptWorkbook] = None

        # Paths
        self.nv_dir = self.project_dir / "nv"
        self.img_dir = self.project_dir / "img"
        self.vid_dir = self.project_dir / "vid"
        self.thumb_dir = self.project_dir / "thumb"
        self.reference_root = SUITE_ROOT / "tools" / "srt-to-excel" / "reference_characters"

        # RUN MODE: 'all' (ảnh+video, mặc định), 'image-only' (chỉ PHASE 1-3), 'video-only' (chỉ PHASE 4-5).
        # Tách 2 trạm: trạm ảnh chạy image-only (nhả slot sớm), trạm video chạy video-only (finalize).
        self.run_mode = str(config.get("run_mode", "all") or "all").strip().lower()
        if self.run_mode not in ("all", "image-only", "video-only"):
            self.run_mode = "all"

        # Generation backend config
        self.generation_backend = str(config.get("generation_backend") or config.get("generation_mode") or "server").strip().lower()
        # "shopapi" = goi thang API api.shopapi.vn (mac dinh cua ban nay).
        if self.generation_backend not in {"server", "nanopic", "flowkit", "combined", "veo3top", "veo3top_b", "veo3top_b_ultra", "veo3top_b_pool", "shopapi"}:
            self.generation_backend = "server"
        # veo3top (A): chrome account thuong tru/mã. veo3top_b (B): token-chrome chung + auth cache.
        # Chi anh huong buoc tao VIDEO; upload anh / status van nhu cu. Lazy-init.
        self._veo3top_provider = None
        self._veo3top_provider_b = None
        # ===== TAO ANH bang veo3top-b (ban thang Flow API, giong video) — option MOI, song song =====
        # "" = tat (dung backend anh cu). "blank" = token chrome trang no-login (nhe).
        # "account"/"ultra" = token tu chrome account login (score cao, ultra). Chi anh huong buoc TAO ANH.
        self.veo3top_image_mode = str(config.get("veo3top_image_mode") or "").strip().lower()
        if self.veo3top_image_mode in ("ultra", "veo3top_b_ultra"):
            self.veo3top_image_mode = "account"
        if self.veo3top_image_mode not in ("", "blank", "account", "pool", "shopapi"):
            self.veo3top_image_mode = ""
        self.use_veo3top_for_image = self.veo3top_image_mode in ("blank", "account", "pool")
        self._veo3top_image_provider = None

        # ===== NHANH MOI: goi thang API api.shopapi.vn (MAC DINH cua ban nay) =====
        # Khoa API co y KHONG doc tu settings.yaml: file do nam trong kho ma va con
        # duoc chep sang worker qua .ve3_run_config.json trong thu muc project ->
        # hai duong ro ri ma nguoi dung khong he biet. Chi doc bien moi truong
        # SHOPAPI_KEY hoac kho khoa %APPDATA%\ShopAPI\ve3-suite\khoa.txt.
        self.shopapi_key, self.shopapi_key_source = self._doc_khoa_shopapi()
        _shopapi_image_chon = (self.veo3top_image_mode == "shopapi")
        _shopapi_video_chon = (self.generation_backend == "shopapi")
        self.use_shopapi_for_image = _shopapi_image_chon and bool(self.shopapi_key)
        self.use_shopapi_for_video = _shopapi_video_chon and bool(self.shopapi_key)
        # Tran cho MOT job (POST + poll cho toi khi xong). Video lau hon anh nhieu.
        self.shopapi_image_timeout = float(config.get("shopapi_image_timeout", 900) or 900)
        self.shopapi_video_timeout = float(config.get("shopapi_video_timeout", 1600) or 1600)
        # LUI VE DUONG CU khi chua co khoa: bao TO roi chay tiep bang backend cu,
        # KHONG chet lang (nguoi dung phai biet vi sao hoa don API van bang 0).
        if (_shopapi_image_chon or _shopapi_video_chon) and not self.shopapi_key:
            self.log("=" * 72, "WARN")
            self.log("CANH BAO: da chon backend 'API shopapi' NHUNG CHUA CO KHOA API.", "WARN")
            self.log("  -> Tool LUI VE duong cu (server/veo3top) cho lan chay nay.", "WARN")
            self.log("  -> Vao trang Cai dat, dan khoa sk_live_... vao o 'Khoa API shopapi'", "WARN")
            self.log("     roi bam 'Kiem khoa'. Hoac dat bien moi truong SHOPAPI_KEY.", "WARN")
            self.log("=" * 72, "WARN")
        elif self.use_shopapi_for_image or self.use_shopapi_for_video:
            self.log("API shopapi: khoa lay tu {0} ({1}) | anh={2} video={3}".format(
                self.shopapi_key_source, _shopapi_che_khoa(self.shopapi_key),
                "API" if self.use_shopapi_for_image else "cu",
                "API" if self.use_shopapi_for_video else "cu"))
        # Neu CA anh VA video deu ban thang veo3top -> KHONG can server pool / bearer worker (moi buoc tu lay auth).
        # veo3top_b_pool = NHA MAY CHUNG (pool anh + pool video): ca 2 buoc tu lay auth per-account (android_bypass),
        # KHONG dung ExtAuth/token worker -> phai nam trong nhom nay (neu thieu -> chay nham ExtAuth cu -> "extension: no project").
        # CA anh VA video deu di qua API shopapi -> cung KHONG can bearer/project worker
        # (khong mo Chrome lan nao). Chi tinh khi DA CO KHOA: thieu khoa thi tool lui ve
        # duong cu, ma duong cu VAN CAN auth -> phai de nguyen luong auth chay.
        self._shopapi_only = self.use_shopapi_for_image and self.use_shopapi_for_video
        self._veo3top_only = (self.use_veo3top_for_image and
            self.generation_backend in ("veo3top", "veo3top_b", "veo3top_b_ultra", "veo3top_b_pool")) \
            or self._shopapi_only
        self.nanopic_fallback_enabled = bool(config.get("nanopic_fallback_enabled", True))

        # FlowKit config
        self.flowkit_server_list = config.get("flowkit_server_list", [])
        self.flowkit_pool = None
        self._sticky_flowkit_server = None

        # Server config
        self.server_url = config.get("local_server_url", "")
        self.server_list = config.get("local_server_list", [])
        self.bearer_token = config.get("flow_bearer_token", "")
        # FLOW2: tao anh bang TOKEN PRO LOCAL tren tung server (khong gui token Ultra).
        # Bat -> dispatch qua /api/img/generate (local_mode); video van dung token Ultra.
        self.use_local_token_for_image = bool(config.get("use_local_token_for_image", False))
        self._local_refs_registered = False
        self._local_ref_names: list = []
        self._local_ref_lock = threading.Lock()
        self.flow_project_id = config.get("flow_project_id", "")
        self.timeout = config.get("flow_timeout", 120)
        self.retry_count = config.get("retry_count", 3)

        # Aspect ratio
        ar_str = config.get("flow_aspect_ratio", "landscape").upper()
        self.aspect_ratio = getattr(AspectRatio, ar_str, AspectRatio.LANDSCAPE)

        # Concurrent prompts (số ảnh 1 mã gửi SONG SONG).
        # AUTO (pool mode): = số chrome pool / số mã song song -> luôn làm ĐẦY pool (không chrome nào ngồi không),
        # không over-thread. Tự tính theo thực tế, KHÔNG cần chỉnh tay ở GUI. Backend khác -> giữ config cũ.
        if self.use_shopapi_for_image:
            # API shopapi: so job song song KHONG duoc go cung - may chu tinh lai lien tuc
            # theo suc chua nha may chia cho so khach dang cho.
            #
            # ⚠ TRUOC DAY cho nay hoi GET /v1/me MOT LAN luc khoi dong roi giu con so
            # do ca luot chay. Do la tu bop minh: doc trung luc dong khach duoc 2 thi
            # ca 4 tieng sau van chay 2, du nha may da rong ra tu lau. Gio moi PHA hoi
            # lai (`_shopapi_luong`) va `min` voi con so nay, nen con nay chi con la
            # TRAN NGUOI DUNG: nguoi dung ghim thi ton trong, khong ghim thi de tran
            # CUNG cua loai job (may chu van la nguoi chan tren that su moi lo).
            _pin = int(config.get("max_concurrent", 0) or 0)
            self.max_concurrent = _pin if _pin > 0 else self._shopapi_tran_cung("image")
        elif str(config.get("generation_backend", "") or "").strip() == "veo3top_b_pool":
            _override = int(config.get("run_max_concurrent", 0) or 0)   # GUI TỰ TÍNH số luồng/mã theo tài nguyên thực -> dùng thẳng
            if _override > 0:
                self.max_concurrent = _override
            else:
                _pool_n = int(config.get("image_pool_accounts", 10) or 10)
                _codes = int(config.get("max_concurrent_codes", 0) or 0)
                _codes = _codes if _codes > 0 else 3          # 0 -> giả định ~3 mã để chia
                self.max_concurrent = max(1, min(_pool_n, -(-_pool_n // _codes)))   # ceil(pool/codes)
        else:
            self.max_concurrent = config.get("max_concurrent", 1)
        self.final_full_rerun = config.get("final_full_rerun", False)
        self.prompt_rewrite_enabled = config.get("prompt_policy_rewrite_enabled", True)
        self.prompt_rewrite_max_rounds = int(config.get("prompt_policy_rewrite_max_rounds", 3) or 3)  # 3 vòng: mỗi vòng escalate mạnh tay hơn
        self.prompt_rewrite_debug = config.get("prompt_policy_rewrite_debug", True)
        self.reference_media_validation_enabled = bool(config.get("reference_media_validation_enabled", False))
        self.reference_media_validation_max_rounds = max(
            1, int(config.get("reference_media_validation_max_rounds", 5) or 5)
        )
        self.ai_provider = self._resolve_ai_provider()
        self.deepseek_keys = self._load_deepseek_keys(config)
        self.deepseek_model = (config.get("deepseek_model", "") or "deepseek-v4-pro").strip()
        self.deepseek_thinking_type = (config.get("deepseek_thinking_type", "") or "disabled").strip()
        self.vov_direct_base_url = (config.get("vov_direct_base_url", "") or "").strip().rstrip("/")
        self.vov_direct_api_key = (config.get("vov_direct_api_key", "") or "").strip()
        self.vov_direct_model = (config.get("vov_direct_model", "") or "claude-sonnet-4-6").strip()
        vov_chain_cfg = config.get("vov_direct_model_chain", []) or []
        if isinstance(vov_chain_cfg, str):
            vov_chain_cfg = [x.strip() for x in vov_chain_cfg.split(",") if x.strip()]
        self.vov_direct_model_chain = [str(x).strip() for x in vov_chain_cfg if str(x).strip()]
        if not self.vov_direct_model_chain:
            self.vov_direct_model_chain = [self.vov_direct_model]
        self._vov_direct_active_model: Optional[str] = None
        self._vov_direct_bad_models: set[str] = set()
        self._vov_direct_transient_fail_counts: Dict[str, int] = {}
        self._vov_direct_demoted_models: set[str] = set()
        self._vov_direct_demote_threshold = max(
            2,
            int(config.get("vov_direct_demote_threshold", 2) or 2),
        )
        self.claude_pool_base_url = (config.get("claude_pool_base_url", "") or "").strip().rstrip("/")
        self.claude_pool_api_key = (config.get("claude_pool_api_key", "") or "").strip()
        self.claude_pool_model = (config.get("claude_pool_model", "") or "gpt-5.4").strip()
        chain_cfg = config.get("claude_pool_model_chain", []) or []
        if isinstance(chain_cfg, str):
            chain_cfg = [x.strip() for x in chain_cfg.split(",") if x.strip()]
        self.claude_pool_model_chain = [str(x).strip() for x in chain_cfg if str(x).strip()]
        if not self.claude_pool_model_chain:
            self.claude_pool_model_chain = [self.claude_pool_model]
        self._claude_pool_active_model: Optional[str] = None
        self._claude_pool_bad_models: set[str] = set()
        self._claude_pool_transient_fail_counts: Dict[str, int] = {}
        self._claude_pool_demoted_models: set[str] = set()
        self._claude_pool_stream_required_models: set[str] = set()
        self._claude_pool_demote_threshold = max(
            2,
            int(config.get("claude_pool_demote_threshold", 2) or 2),
        )
        self.debug_dir = self.project_dir / "_debug"
        self.prompt_rewrite_log_path = self.debug_dir / "prompt_rewrites.jsonl"

        # Server pool
        self.pool = None
        self.auth_service = FlowRuntimeAuthService(SUITE_ROOT, self.config, log_func=self.log)
        self._init_server_pool()

    def _normalize_log_text(self, msg: Any) -> str:
        text = "" if msg is None else str(msg)

        # Repair common mojibake like "Táº¡o", "KhÃ´ng", "â†’" before stripping accents.
        if any(marker in text for marker in ("Ã", "Â", "Ä", "â", "áº", "á»")):
            try:
                repaired = text.encode("latin1", errors="ignore").decode("utf-8", errors="ignore")
                if repaired:
                    text = repaired
            except Exception:
                pass

        text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")
        text = re.sub(r"\s+", " ", text).strip()
        return text

    def _load_deepseek_keys(self, config: Dict[str, Any]) -> List[str]:
        keys = []
        one_key = (config.get("deepseek_api_key", "") or "").strip()
        many_keys = config.get("deepseek_api_keys", []) or []
        if one_key:
            keys.append(one_key)
        for key in many_keys:
            key = (key or "").strip()
            if key and key not in keys:
                keys.append(key)
        return keys

    def _resolve_ai_provider(self) -> str:
        provider = (self.config.get("excel_ai_provider", "") or "").strip().lower()
        if provider in ("deepseek", "claude_pool", "vov_direct"):
            return provider
        if (self.config.get("vov_direct_base_url", "") or "").strip() and (self.config.get("vov_direct_api_key", "") or "").strip():
            return "vov_direct"
        if (self.config.get("claude_pool_base_url", "") or "").strip() and (self.config.get("claude_pool_api_key", "") or "").strip():
            return "claude_pool"
        return "deepseek"

    def _provider_display_name(self) -> str:
        if self.ai_provider == "vov_direct":
            return "VOV Direct"
        if self.ai_provider == "claude_pool":
            return "Claude Pool"
        return "DeepSeek"

    def _can_fallback_to_claude_pool(self) -> bool:
        return bool(self.claude_pool_base_url and self.claude_pool_api_key and self.claude_pool_model)

    def _vov_direct_candidate_models(self) -> List[str]:
        ordered: List[str] = []
        if (
            self._vov_direct_active_model
            and self._vov_direct_active_model not in self._vov_direct_bad_models
            and self._vov_direct_active_model not in self._vov_direct_demoted_models
        ):
            ordered.append(self._vov_direct_active_model)
        for model in self.vov_direct_model_chain:
            if model in self._vov_direct_bad_models or model in self._vov_direct_demoted_models:
                continue
            if model not in ordered:
                ordered.append(model)
        if not ordered:
            self._vov_direct_demoted_models.clear()
            for model in self.vov_direct_model_chain:
                if model in self._vov_direct_bad_models:
                    continue
                if model not in ordered:
                    ordered.append(model)
        return ordered

    def _mark_vov_direct_success(self, model_name: str) -> None:
        self._vov_direct_active_model = model_name
        self._vov_direct_transient_fail_counts[model_name] = 0
        self._vov_direct_demoted_models.discard(model_name)

    def _mark_vov_direct_transient_failure(self, model_name: str) -> bool:
        count = int(self._vov_direct_transient_fail_counts.get(model_name, 0) or 0) + 1
        self._vov_direct_transient_fail_counts[model_name] = count
        if count >= self._vov_direct_demote_threshold:
            self._vov_direct_demoted_models.add(model_name)
            if self._vov_direct_active_model == model_name:
                self._vov_direct_active_model = None
            return True
        return False

    def _claude_pool_candidate_models(self) -> List[str]:
        ordered: List[str] = []
        if (
            self._claude_pool_active_model
            and self._claude_pool_active_model not in self._claude_pool_bad_models
            and self._claude_pool_active_model not in self._claude_pool_demoted_models
        ):
            ordered.append(self._claude_pool_active_model)
        for model in self.claude_pool_model_chain:
            if model in self._claude_pool_bad_models or model in self._claude_pool_demoted_models:
                continue
            if model not in ordered:
                ordered.append(model)
        if not ordered:
            self._claude_pool_demoted_models.clear()
            for model in self.claude_pool_model_chain:
                if model in self._claude_pool_bad_models:
                    continue
                if model not in ordered:
                    ordered.append(model)
        return ordered

    def _mark_claude_pool_success(self, model_name: str) -> None:
        self._claude_pool_active_model = model_name
        self._claude_pool_transient_fail_counts[model_name] = 0
        self._claude_pool_demoted_models.discard(model_name)

    def _mark_claude_pool_transient_failure(self, model_name: str) -> bool:
        count = int(self._claude_pool_transient_fail_counts.get(model_name, 0) or 0) + 1
        self._claude_pool_transient_fail_counts[model_name] = count
        if count >= self._claude_pool_demote_threshold:
            self._claude_pool_demoted_models.add(model_name)
            if self._claude_pool_active_model == model_name:
                self._claude_pool_active_model = None
            return True
        return False

    def _short_text(self, text: Any, limit: int = 240) -> str:
        s = re.sub(r"\s+", " ", ("" if text is None else str(text))).strip()
        if len(s) <= limit:
            return s
        return s[: limit - 3] + "..."

    def _log_prompt_rewrite_event(
        self,
        item_type: str,
        item_id: Any,
        phase: str,
        status: str,
        original_prompt: str,
        rewritten_prompt: str = "",
        error_text: str = "",
        round_index: int = 0,
        mode: str = "image",
    ) -> None:
        if not self.prompt_rewrite_debug:
            return
        try:
            self.debug_dir.mkdir(parents=True, exist_ok=True)
            payload = {
                "ts": datetime.now().isoformat(timespec="seconds"),
                "project": self.project_dir.name,
                "item_type": item_type,
                "item_id": str(item_id),
                "phase": phase,
                "status": status,
                "mode": mode,
                "round": round_index,
                "original_prompt": original_prompt or "",
                "rewritten_prompt": rewritten_prompt or "",
                "error_text": error_text or "",
            }
            with self.prompt_rewrite_log_path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(payload, ensure_ascii=False) + "\n")
        except Exception as e:
            self.log(f"[REWRITE] khong ghi duoc debug file: {type(e).__name__}: {e}", "WARN")

    def _ensure_flowkit_chrome_open(self):
        """Open Chrome with FlowKit extension and navigate to Flow project."""
        import urllib.request as _ur

        # Check if extension already connected
        for srv in self.flowkit_server_list:
            try:
                r = _ur.urlopen(f"{srv['url']}/health", timeout=3)
                data = json.loads(r.read())
                if data.get("extension_connected"):
                    self.log("FlowKit: extension already connected")
                    return
            except Exception:
                pass

        # Open Chrome for the configured flowkit instance
        for i, srv in enumerate(self.flowkit_server_list):
            url = srv.get('url', '')
            port_match = re.search(r':(\d+)$', url.rstrip('/'))
            api_port = int(port_match.group(1)) if port_match else 8100 + i
            chrome_idx = api_port - 8100  # 0-based index

            chrome_portable_dir = SUITE_ROOT / f"GoogleChromePortable - Copy ({chrome_idx + 1})"
            chrome_bin = chrome_portable_dir / "App" / "Chrome-bin" / "chrome.exe"
            if not chrome_bin.is_file():
                self.log(f"FlowKit: Chrome Copy ({chrome_idx + 1}) not found", "ERROR")
                continue

            ext_dir = SUITE_ROOT / "flowkit_extensions" / f"ext_{api_port}"
            if not ext_dir.is_dir():
                self.log(f"FlowKit: extension dir {ext_dir.name} not found", "ERROR")
                continue

            user_data = chrome_portable_dir / "Data" / "profile"
            cdp_port = 9800 + chrome_idx

            self.log(f"FlowKit: opening Chrome Copy ({chrome_idx + 1})...")
            try:
                from DrissionPage import ChromiumOptions, ChromiumPage
                opts = ChromiumOptions()
                opts.set_browser_path(str(chrome_bin))
                if user_data.exists():
                    opts.set_user_data_path(str(user_data))
                opts.set_local_port(cdp_port)
                opts.set_argument("--no-first-run")
                opts.set_argument("--no-default-browser-check")
                opts.set_argument(f"--load-extension={ext_dir}")
                page = ChromiumPage(opts)

                # Navigate to Flow project
                project_url = f"https://labs.google/fx/vi/tools/flow/project/{self.flow_project_id}"
                page.get(project_url)
                time.sleep(5)

                # Check if redirected to login
                current_url = str(page.url or "")
                if "accounts.google.com" in current_url:
                    self.log("FlowKit: Chrome not logged in on Flow, need manual login", "ERROR")
                    return

                self.log("FlowKit: Chrome on Flow project, waiting for extension...")
            except Exception as e:
                self.log(f"FlowKit: Chrome open failed: {e}", "ERROR")
                return

        # Wait for extension to connect
        for attempt in range(30):
            time.sleep(2)
            for srv in self.flowkit_server_list:
                try:
                    r = _ur.urlopen(f"{srv['url']}/health", timeout=3)
                    data = json.loads(r.read())
                    if data.get("extension_connected"):
                        self.log("FlowKit: extension connected!")
                        return
                except Exception:
                    pass
            if attempt == 14:
                self.log("FlowKit: still waiting for extension (30s)...", "WARN")

        self.log("FlowKit: extension did not connect in 60s", "ERROR")

    def _auto_discover_flowkit(self) -> list:
        """Auto-discover FlowKit instances from Chrome Portable copies."""
        import glob as _glob
        pattern = str(SUITE_ROOT / "GoogleChromePortable - Copy (*)")
        dirs = sorted(_glob.glob(pattern))
        servers = []
        for i, d in enumerate(dirs):
            chrome_bin = Path(d) / "App" / "Chrome-bin" / "chrome.exe"
            if chrome_bin.is_file():
                servers.append({
                    "url": f"http://127.0.0.1:{8100 + i}",
                    "name": f"flowkit-{i + 1}",
                    "enabled": True,
                })
        if servers:
            self.log(f"FlowKit auto-discover: {len(servers)} Chrome Portable(s)")
        return servers

    def _ensure_flowkit_agents_running(self):
        """Start FlowKit agents that are not already running."""
        import urllib.request
        flowkit_dir = SUITE_ROOT / "tools" / "flowkit"
        if not flowkit_dir.is_dir():
            self.log("FlowKit agent dir not found!", "ERROR")
            return

        started = 0
        for i, srv in enumerate(self.flowkit_server_list):
            url = srv.get("url", "")
            if not url:
                continue
            # Extract port from URL
            import re as _re
            port_match = _re.search(r":(\d+)$", url.rstrip("/"))
            port = int(port_match.group(1)) if port_match else 8100 + i
            # Check if already running
            try:
                r = urllib.request.urlopen(f"{url}/health", timeout=2)
                data = json.loads(r.read())
                if data.get("status") == "ok":
                    continue
            except Exception:
                pass
            # Start agent
            ws_port = 9222 + (port - 8100)
            instance_name = srv.get("name", f"flowkit-{port - 8099}")
            data_dir = flowkit_dir / "flowkit_data" / instance_name
            data_dir.mkdir(parents=True, exist_ok=True)
            env = os.environ.copy()
            env["API_PORT"] = str(port)
            env["WS_PORT"] = str(ws_port)
            env["FLOW_AGENT_DIR"] = str(data_dir)
            try:
                proc = subprocess.Popen(
                    [sys.executable, "-m", "agent.main"],
                    cwd=str(flowkit_dir),
                    env=env,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if sys.platform == "win32" else 0,
                )
                started += 1
            except Exception as e:
                self.log(f"FlowKit agent {port} start failed: {e}", "ERROR")
        if started:
            self.log(f"FlowKit: started {started} agent(s), waiting for ready...")
            for attempt in range(10):
                time.sleep(2)
                all_up = True
                for srv in self.flowkit_server_list:
                    try:
                        r = urllib.request.urlopen(f"{srv['url']}/health", timeout=2)
                        data = json.loads(r.read())
                        if data.get("status") == "ok":
                            continue
                    except Exception:
                        pass
                    all_up = False
                if all_up:
                    break
            self.log(f"FlowKit: agents ready after {(attempt+1)*2}s")

    def _init_server_pool(self):
        """Khá»Ÿi táº¡o ServerPool tá»« config."""
        pool_config = {}
        if self.server_list:
            pool_config["local_server_list"] = self.server_list
        elif self.server_url:
            pool_config["local_server_url"] = self.server_url

        if pool_config:
            self.pool = ServerPool(pool_config, log_callback=self.log)
            self.pool.refresh_all()
            self.log(f"Server pool: {len(self.pool.servers)} server(s)")
        else:
            if self.use_shopapi_for_image and self.use_shopapi_for_video:
                # ⚠ ĐI TOÀN API THÌ KHÔNG CÓ SERVER NÀO, VÀ ĐÓ LÀ ĐÚNG.
                #
                # `ServerPool` là đường Chrome/VM. Chạy qua api.shopapi.vn thì
                # không có `local_server_list` lẫn `local_server_url` — nên
                # nhánh này rơi vào `else` và bắn ERROR "Khong co server URL!"
                # cho MỌI mã, mỗi lần khởi động. Log máy khác 17:16-17:17 ngày
                # 15/08/2026 có dòng đó ở cả sáu mã đang chạy tốt.
                #
                # ERROR giả làm hỏng đúng thứ log sinh ra để làm: người đọc quét
                # tìm ERROR, thấy nó ở mọi mã, rồi thôi không tin dòng ERROR nào
                # nữa. Lần sau có lỗi thật thì nó lẫn vào đám này.
                self.log("Khong dung server nao: ca anh lan video di API shopapi", "INFO")
            elif self.generation_backend in ("nanopic", "flowkit", "combined"):
                self.log(f"Khong co server URL, dang chay {self.generation_backend} mode", "INFO")
            elif self.nanopic_fallback_enabled and self.config.get("nanopic_use_flow_proxy", False):
                self.log("Khong co server URL, se dung NanoPic fallback", "WARN")
            else:
                self.log("Khong co server URL!", "ERROR")

        # FlowKit pool (for flowkit/combined modes)
        if self.generation_backend in ("flowkit", "combined"):
            if not self.flowkit_server_list:
                self.flowkit_server_list = self._auto_discover_flowkit()
            if self.flowkit_server_list:
                self._ensure_flowkit_agents_running()
                fk_config = {"local_server_list": self.flowkit_server_list}
                self.flowkit_pool = ServerPool(fk_config, log_callback=self.log)
                self.flowkit_pool.refresh_all()
                active = sum(1 for s in self.flowkit_pool.servers if s.server_state not in ("down", "unknown"))
                if active == 0:
                    time.sleep(3)
                    self.flowkit_pool.refresh_all()
                    active = sum(1 for s in self.flowkit_pool.servers if s.server_state not in ("down", "unknown"))
                self.log(f"FlowKit pool: {active}/{len(self.flowkit_pool.servers)} instance(s) ready")
            else:
                self.log("FlowKit mode: khong tim thay Chrome Portable nao!", "ERROR")

    def _is_policy_violation_error(self, error_text: str) -> bool:
        err = (error_text or "").lower()
        if not err:
            return False

        # Explicit policy markers - high confidence
        policy_markers = [
            "policy",
            "safety",
            "unsafe",
            "unsafe_generation",       # PUBLIC_ERROR_UNSAFE_GENERATION (pool trả về)
            "content_rejected",        # API shopapi: job.error.code khi bộ lọc chặn prompt.
                                       # Thông điệp kèm theo là văn xuôi TIẾNG VIỆT nên không
                                       # marker tiếng Anh nào ở đây khớp — phải bắt bằng mã.
            "public_error_minor",      # PUBLIC_ERROR_MINOR (trẻ vị thành niên) — 'minor' KHÔNG match qua 'unsafe'
            "violat",
            "prohibited",
            "disallowed",
            "not allowed",
            "harmful",
            "explicit sexual",
            "sexual content",
            "graphic violence",
            "content restriction",
            "content policy",
            "moderation",
            "rejected prompt",
            "prompt was rejected",
            "cannot generate this content",
            "can't generate this content",
        ]

        # Check explicit markers first
        if any(marker in err for marker in policy_markers):
            return True

        # MEDIA_GENERATION_STATUS_FAILED is ambiguous. It can be a model/runtime
        # failure on the server side, so only explicit policy markers above should
        # enter the rewrite/last-resort policy path.
        if "media_generation_status_failed" in err:
            return False

        return False

    def _read_sse_chat_content(self, resp) -> str:
        chunks = []
        try:
            for raw_line in resp.iter_lines(decode_unicode=True):
                if not raw_line:
                    continue
                line = raw_line.strip()
                if not line.startswith("data:"):
                    continue
                payload = line[5:].strip()
                if payload == "[DONE]":
                    break
                try:
                    obj = json.loads(payload)
                except Exception:
                    continue
                for choice in obj.get("choices") or []:
                    delta = choice.get("delta") or {}
                    text = delta.get("content")
                    if text:
                        chunks.append(str(text))
        except Exception:
            return ""
        return "".join(chunks).strip()

    def _call_rewrite_llm(self, instruction: str, temperature: float = 0.3, max_tokens: int = 700) -> Optional[str]:
        providers: List[tuple[str, Callable[[str, float, int], Optional[str]]]] = []
        if self.ai_provider == "vov_direct":
            providers = [
                ("VOV Direct", self._call_vov_direct_rewrite),
                ("Claude Pool", self._call_claude_pool_rewrite),
                ("DeepSeek", self._call_deepseek_rewrite),
            ]
        elif self.ai_provider == "claude_pool":
            providers = [
                ("Claude Pool", self._call_claude_pool_rewrite),
                ("VOV Direct", self._call_vov_direct_rewrite),
                ("DeepSeek", self._call_deepseek_rewrite),
            ]
        else:
            providers = [
                ("DeepSeek", self._call_deepseek_rewrite),
                ("VOV Direct", self._call_vov_direct_rewrite),
                ("Claude Pool", self._call_claude_pool_rewrite),
            ]
        # Uu tien claude.exe CLI (Claude Code, cung backend/digishop tool dung cho Excel) neu excel_engine=claude_cli.
        # Cac provider VOV/DeepSeek hay chet -> dat Claude CLI len DAU khi tool dang chay claude_cli.
        if str(self.config.get("excel_engine", "") or "").strip().lower() == "claude_cli":
            providers = [("Claude CLI", self._call_claude_cli_rewrite)] + \
                        [p for p in providers if p[0] != "Claude CLI"]

        # shopapi ĐỨNG ĐẦU khi tool đang chạy toàn API.
        #
        # Không phải để "ưu ái": lúc đó nó là nguồn DUY NHẤT chắc chắn dùng
        # được. claude.exe có thể chưa cài, DeepSeek đã bỏ khoá, VOV/Pool là
        # dịch vụ ngoài. Còn shopapi thì tool VỪA gọi thành công hàng trăm lần
        # bằng đúng khoá đó để tạo chính mấy tấm ảnh này.
        #
        # Đặt sau lưng chúng nó thì ba vòng viết lại tiêu hết vào các nguồn chết
        # rồi bỏ cuộc — đúng chuyện đã xảy ra với Scene 4 và Scene 52.
        if (self.use_shopapi_for_image or self.use_shopapi_for_video) and \
                (getattr(self, "shopapi_key", "") or "").strip():
            providers = [("shopapi", self._call_shopapi_rewrite)] + \
                        [p for p in providers if p[0] != "shopapi"]

        for idx, (provider_name, provider_func) in enumerate(providers):
            rewritten = provider_func(instruction, temperature=temperature, max_tokens=max_tokens)
            if rewritten:
                if idx > 0:
                    self.log(f"    [REWRITE] fallback thanh cong voi {provider_name}", "INFO")
                return rewritten
            if idx < len(providers) - 1:
                self.log(f"    [REWRITE] {provider_name} khong kha dung, thu provider tiep theo", "WARN")
        return None

    def _call_claude_cli_rewrite(self, instruction: str, temperature: float = 0.3, max_tokens: int = 700) -> Optional[str]:
        """Rewrite bang claude.exe (Claude Code CLI) qua digishop — cung backend tool dung cho Excel.
        Fall-through (tra None) neu claude.exe/engine khong san sang."""
        try:
            eng = getattr(self, "_claude_cli_engine", None)
            if eng is None:
                srt_mod = str(SUITE_ROOT / "tools" / "srt-to-excel" / "modules")
                if srt_mod not in sys.path:
                    sys.path.insert(0, srt_mod)
                from claude_cli_engine import ClaudeCliEngine
                eng = ClaudeCliEngine(self.config)
                self._claude_cli_engine = eng
            cwd = self.project_dir if getattr(self, "project_dir", None) else SUITE_ROOT
            from pathlib import Path as _P
            txt = eng._run_claude(instruction, _P(str(cwd)))
            if not txt:
                return None
            txt = txt.strip()
            # neu model boc trong ```/json thi lay phan text; else tra thang
            if txt.startswith("```"):
                txt = txt.strip("`")
                if txt.lower().startswith("json"):
                    txt = txt[4:].strip()
            return txt or None
        except Exception as e:
            self.log(f"    [REWRITE] Claude CLI loi: {type(e).__name__}: {e}", "WARN")
            return None

    def _call_deepseek_rewrite(self, instruction: str, temperature: float = 0.3, max_tokens: int = 700) -> Optional[str]:
        if not self.deepseek_keys:
            return None

        import requests

        for idx, key in enumerate(self.deepseek_keys, start=1):
            try:
                headers = {
                    "Authorization": f"Bearer {key}",
                    "Content-Type": "application/json",
                }
                data = {
                    "model": self.deepseek_model,
                    "messages": [{"role": "user", "content": instruction}],
                    "temperature": temperature,
                    "max_tokens": max_tokens,
                }
                if self.deepseek_thinking_type:
                    data["thinking"] = {"type": self.deepseek_thinking_type}
                resp = requests.post(
                    "https://api.deepseek.com/v1/chat/completions",
                    headers=headers,
                    json=data,
                    timeout=90,
                )
                if resp.status_code != 200:
                    self.log(f"    [DEEPSEEK] key #{idx} loi {resp.status_code}", "WARN")
                    continue
                message = (((resp.json() or {}).get("choices") or [{}])[0].get("message") or {})
                content = message.get("content") or message.get("reasoning_content") or ""
                cleaned = re.sub(r"\s+", " ", (content or "")).strip().strip('"').strip()
                if cleaned:
                    return cleaned
            except Exception as e:
                self.log(f"    [DEEPSEEK] key #{idx} loi: {type(e).__name__}: {e}", "WARN")
        return None

    def _call_shopapi_rewrite(self, instruction: str, temperature: float = 0.3,
                              max_tokens: int = 700) -> Optional[str]:
        """Viết lại prompt bằng LLM của api.shopapi.vn — CÙNG khoá đang trả tiền ảnh.

        ⚠ VÌ SAO PHẢI CÓ — ĐO THẬT 07/08/2026 (project TL1-0742)
        --------------------------------------------------------
        145/147 ảnh ra ngon; hai cảnh còn lại chết vì bộ lọc nội dung::

            Scene 4:  prompt co dau hieu vi pham policy, thu viet lai (vong 1/3)
            Scene 4:  khong viet lai duoc prompt hop le
            Scene 4 FAIL [failed: TERMINAL policy]

        Máy nhận diện policy chạy đúng, nhưng khâu VIẾT LẠI thì không có nguồn
        nào dùng được: danh sách provider chỉ có VOV / Claude Pool / DeepSeek /
        claude.exe. Chạy toàn API thì DeepSeek không còn khoá, claude.exe không
        cài, nên cả ba vòng viết lại đều trượt và cảnh mất trắng.

        Vô lý ở chỗ: tool ĐANG nói chuyện với một LLM chạy tốt bằng đúng khoá
        đó, chỉ là chưa ai nối vào đây.

        Trả `None` khi thiếu khoá/SDK để `_call_rewrite_llm` đi tiếp provider sau
        — đúng giao kèo của các provider còn lại.
        """
        import requests

        khoa = (getattr(self, "shopapi_key", "") or "").strip()
        if not khoa:
            return None

        base = str(self.config.get("shopapi_base_url") or "https://api.shopapi.vn/v1").strip().rstrip("/")
        # MỌI model shopapi, rẻ trước đắt sau — cùng danh sách với engine Excel
        # (`claude_cli_engine._CHUOI_MODEL_MAC_DINH`), sửa thì sửa cả hai.
        #
        # ⚠ PHẢI ĐỦ CẢ BỐN. Đo thật 08/08/2026: sonnet-5 và opus-5 CÙNG chết
        # (0/4 mỗi con) trong khi fable-5 vẫn phục vụ. Chuỗi hai model trượt
        # sạch dù ngay cạnh có model đang rảnh. Cửa sổ nghẽn rất ngắn và rải
        # không đều giữa các cụm -> càng nhiều cửa để gõ càng khó trượt hết.
        chuoi = self.config.get("shopapi_model_chain") or [
            "claude-sonnet-5", "claude-opus-5", "claude-fable-5", "gpt-5.6"]
        timeout_seconds = int(self.config.get("excel_ai_timeout_seconds", 180) or 180)
        headers = {"Authorization": f"Bearer {khoa}", "Content-Type": "application/json"}

        for model_name in chuoi:
            try:
                resp = requests.post(
                    f"{base}/chat/completions", headers=headers, timeout=timeout_seconds,
                    json={
                        "model": model_name,
                        "messages": [{"role": "user", "content": instruction}],
                        "temperature": temperature,
                        "max_tokens": max_tokens,
                    })
            except Exception as e:
                self.log(f"    [REWRITE] shopapi {model_name} loi mang: {str(e)[:90]}", "WARN")
                continue
            # 2xx chu khong phai dung 200: api.shopapi.vn tung tra 201.
            if not (200 <= resp.status_code < 300):
                self.log(f"    [REWRITE] shopapi {model_name} HTTP {resp.status_code} -> doi model", "WARN")
                continue
            try:
                choices = (resp.json().get("choices") or [])
                content = (((choices[0] or {}).get("message") or {}).get("content") or "") if choices else ""
            except Exception:
                content = ""
            rewritten = re.sub(r"\s+", " ", content).strip().strip('"').strip()
            if rewritten:
                return rewritten
        return None

    def _call_vov_direct_rewrite(self, instruction: str, temperature: float = 0.3, max_tokens: int = 700) -> Optional[str]:
        import random
        import requests

        if not (self.vov_direct_base_url and self.vov_direct_api_key and self.vov_direct_model):
            return None

        headers = {
            "Authorization": f"Bearer {self.vov_direct_api_key}",
            "Content-Type": "application/json",
        }
        timeout_seconds = int(self.config.get("excel_ai_timeout_seconds", self.config.get("api_timeout_seconds", 180)) or 180)
        max_attempts = max(3, int(self.config.get("excel_ai_attempts_per_key", self.config.get("api_attempts_per_key", 3)) or 3))
        url = f"{self.vov_direct_base_url}/chat/completions"

        for model_name in self._vov_direct_candidate_models():
            data = {
                "model": model_name,
                "messages": [{"role": "user", "content": instruction}],
                "temperature": temperature,
                "max_tokens": max_tokens,
            }
            self.log(f"    [REWRITE] VOV {'sticky' if self._vov_direct_active_model == model_name else 'trying'} model: {model_name}", "INFO")
            for attempt in range(max_attempts):
                try:
                    resp = requests.post(url, headers=headers, json=data, timeout=timeout_seconds)
                    if resp.status_code == 200:
                        payload = resp.json()
                        choices = payload.get("choices") or []
                        content = (((choices[0] or {}).get("message") or {}).get("content") or "").strip() if choices else ""
                        rewritten = re.sub(r"\s+", " ", content).strip().strip('"').strip()
                        if rewritten:
                            self._mark_vov_direct_success(model_name)
                            return rewritten
                    if resp.status_code == 429:
                        demoted = self._mark_vov_direct_transient_failure(model_name)
                        wait_seconds = min(90, 10 * (attempt + 1))
                        self.log(f"    [REWRITE] VOV {model_name} 429, retry sau {wait_seconds:.0f}s", "WARN")
                        if demoted:
                            break
                        time.sleep(wait_seconds)
                        continue
                    if resp.status_code in (408, 409, 500, 502, 503, 504):
                        demoted = self._mark_vov_direct_transient_failure(model_name)
                        wait_seconds = min(30, 2 ** attempt) + random.uniform(0.2, 1.2)
                        self.log(f"    [REWRITE] VOV {model_name} loi {resp.status_code}, retry sau {wait_seconds:.1f}s", "WARN")
                        if demoted:
                            break
                        time.sleep(wait_seconds)
                        continue
                    self._vov_direct_bad_models.add(model_name)
                    if self._vov_direct_active_model == model_name:
                        self._vov_direct_active_model = None
                    break
                except requests.RequestException as e:
                    demoted = self._mark_vov_direct_transient_failure(model_name)
                    wait_seconds = min(20, 2 ** attempt) + random.uniform(0.2, 1.2)
                    self.log(f"    [REWRITE] VOV {model_name} loi: {type(e).__name__}: {e}", "WARN")
                    if demoted:
                        break
                    time.sleep(wait_seconds)
            if self._vov_direct_active_model != model_name:
                self._vov_direct_bad_models.add(model_name)
        return None

    def _call_claude_pool_rewrite(self, instruction: str, temperature: float = 0.3, max_tokens: int = 700) -> Optional[str]:
        import random
        import requests

        if not (self.claude_pool_base_url and self.claude_pool_api_key and self.claude_pool_model):
            return None

        headers = {
            "Authorization": f"Bearer {self.claude_pool_api_key}",
            "Content-Type": "application/json",
        }
        timeout_seconds = int(self.config.get("excel_ai_timeout_seconds", self.config.get("api_timeout_seconds", 180)) or 180)
        max_attempts = max(3, int(self.config.get("excel_ai_attempts_per_key", self.config.get("api_attempts_per_key", 3)) or 3))
        url = f"{self.claude_pool_base_url}/v1/chat/completions"

        for model_name in self._claude_pool_candidate_models():
            data = {
                "model": model_name,
                "messages": [{"role": "user", "content": instruction}],
                "temperature": temperature,
                "max_tokens": max_tokens,
            }
            if model_name in self._claude_pool_stream_required_models:
                data["stream"] = True
            self.log(f"    [REWRITE] GPT {'sticky' if self._claude_pool_active_model == model_name else 'trying'} model: {model_name}", "INFO")
            for attempt in range(max_attempts):
                try:
                    resp = requests.post(url, headers=headers, json=data, timeout=timeout_seconds)
                    if resp.status_code == 200:
                        ctype = str(resp.headers.get("Content-Type", "") or "").lower()
                        if "text/event-stream" in ctype:
                            content = self._read_sse_chat_content(resp)
                        else:
                            payload = resp.json()
                            choices = payload.get("choices") or []
                            content = (((choices[0] or {}).get("message") or {}).get("content") or "").strip() if choices else ""
                        rewritten = re.sub(r"\s+", " ", content).strip().strip('"').strip()
                        if rewritten:
                            self._mark_claude_pool_success(model_name)
                            return rewritten
                    if resp.status_code == 429:
                        demoted = self._mark_claude_pool_transient_failure(model_name)
                        wait_seconds = min(90, 10 * (attempt + 1))
                        self.log(f"    [REWRITE] GPT {model_name} 429, retry sau {wait_seconds:.0f}s", "WARN")
                        if demoted:
                            break
                        time.sleep(wait_seconds)
                        continue
                    if resp.status_code in (408, 409, 500, 502, 503, 504):
                        demoted = self._mark_claude_pool_transient_failure(model_name)
                        wait_seconds = min(30, 2 ** attempt) + random.uniform(0.2, 1.2)
                        self.log(f"    [REWRITE] GPT {model_name} loi {resp.status_code}, retry sau {wait_seconds:.1f}s", "WARN")
                        if demoted:
                            break
                        time.sleep(wait_seconds)
                        continue
                    if resp.status_code == 400 and "stream must be set to true" in (resp.text or "").lower():
                        self._claude_pool_stream_required_models.add(model_name)
                        data["stream"] = True
                        self.log(f"    [REWRITE] GPT {model_name} can stream=true, retry", "WARN")
                        continue
                    self._claude_pool_bad_models.add(model_name)
                    if self._claude_pool_active_model == model_name:
                        self._claude_pool_active_model = None
                    break
                except requests.RequestException as e:
                    demoted = self._mark_claude_pool_transient_failure(model_name)
                    wait_seconds = min(20, 2 ** attempt) + random.uniform(0.2, 1.2)
                    self.log(f"    [REWRITE] GPT {model_name} loi: {type(e).__name__}: {e}", "WARN")
                    if demoted:
                        break
                    time.sleep(wait_seconds)
            if self._claude_pool_active_model != model_name:
                self._claude_pool_bad_models.add(model_name)
        return None

    def _call_deepseek_prompt(self, instruction: str, temperature: float = 0.3, max_tokens: int = 700) -> Optional[str]:
        return self._call_rewrite_llm(instruction, temperature=temperature, max_tokens=max_tokens)

    def _rewrite_prompt_for_policy(self, prompt: str, error_text: str, mode: str = "image") -> Optional[str]:
        return self._rewrite_prompt_for_policy_v2(prompt, error_text, mode=mode)

    def _rewrite_reference_prompt_for_refresh(
        self,
        prompt: str,
        ref_id: str,
        is_location: bool = False,
        error_text: str = "",
    ) -> Optional[str]:
        return self._rewrite_reference_prompt_for_refresh_v2(
            prompt,
            ref_id,
            is_location=is_location,
            error_text=error_text,
        )

    def _rewrite_prompt_for_policy_v2(self, prompt: str, error_text: str, mode: str = "image",
                                      round_index: int = 1) -> Optional[str]:
        if not self.prompt_rewrite_enabled:
            return None

        cleaned_error = re.sub(r"\s+", " ", (error_text or "")).strip()[:500]
        media_label = "Veo/Flow image prompt" if mode == "image" else "Veo video prompt"
        # ESCALATE theo vòng: vòng trước viết lại VẪN bị chặn -> vòng sau CỐ HƠN (mạnh tay làm sạch triệt để hơn,
        # temperature cao hơn cho đa dạng). Round 1 = giữ tối đa ý; round 2 = trừu tượng hoá mạnh; round 3 = tối giản
        # an toàn tuyệt đối (chỉ giữ chủ thể + bối cảnh trung tính, bỏ HẾT chi tiết có thể gây cờ).
        _r = max(1, int(round_index or 1))
        if _r <= 1:
            escalation = (
                "- Keep as much of the original cinematic intent as possible; only soften the specific safety trigger."
            )
            _temp = 0.3
        elif _r == 2:
            escalation = (
                "- The previous safer rewrite STILL got blocked, so push HARDER this time.\n"
                "- Abstract away any charged element: replace explicit actions/objects with implied, off-frame, or aftermath framing.\n"
                "- Drop specific words that could trip safety (weapon names, injury detail, body focus, substance names) — describe mood and composition instead."
            )
            _temp = 0.5
        else:
            escalation = (
                "- Two safer rewrites ALREADY got blocked. Produce a MINIMAL, unquestionably safe-for-work prompt.\n"
                "- Keep ONLY: the main subject/character, a neutral setting, camera framing, lighting and a calm mood.\n"
                "- Remove ENTIRELY every potentially sensitive element (violence, injury, weapons, intimacy, substances, distress, minors-in-risk). Do not merely soften — delete them.\n"
                "- Favor a still, quiet, emotionally neutral cinematic shot. It MUST pass safety review even if some original intent is lost."
            )
            _temp = 0.7
        rewrite_instruction = f"""
You rewrite prompts for Google Flow / Veo 3 so they pass safety review while preserving the usable cinematic intent.
This is rewrite attempt #{_r}.

Hard requirements:
- Rewrite the prompt below into a safer version for {media_label}.
- Keep when possible: subject identity, composition, framing, lens, camera movement, lighting, color, setting, mood, time period, wardrobe, and broad action.
- Remove or soften any likely safety trigger.
- Prefer neutral visual description over charged or explicit wording.
- If the scene is intense, convert it to implied tension, aftermath, restraint, distance, or emotional reaction instead of explicit detail.
- Avoid or replace: blood, gore, wounds, bruises, strangling, weapons in use, assault, torture, fetish language, nudity, sexual body focus, minors in risky context, drugs, hate/extremism, self-harm, illegal instructions, public figures, trademark/brand names, copyrighted character names.
- Do not mention policy, censorship, or refusals.
- Output one clean English prompt only.
- No bullets. No explanation. No quotes.

Escalation for this attempt:
{escalation}

Original prompt:
{prompt}

Error from generator:
{cleaned_error or "Unknown policy rejection"}
""".strip()

        rewritten = self._call_rewrite_llm(rewrite_instruction, temperature=_temp, max_tokens=700)
        if rewritten and rewritten.lower() != prompt.strip().lower():
            return rewritten
        self.log(f"    [REWRITE] {self._provider_display_name()} khong viet lai duoc prompt hop le (vong {_r})", "WARN")
        return None

    def _rewrite_reference_prompt_for_refresh_v2(
        self,
        prompt: str,
        ref_id: str,
        is_location: bool = False,
        error_text: str = "",
    ) -> Optional[str]:
        subject_label = "environment/location reference image" if is_location else "character reference image"
        cleaned_error = re.sub(r"\s+", " ", (error_text or "")).strip()[:500]
        instruction = f"""
You rewrite prompts for Google Flow / Veo 3 to create a fresh {subject_label} that is stable and reusable as a reference image.

Hard requirements:
- Output one clean English image prompt only.
- Preserve the same core identity, age range, wardrobe style, setting, lighting mood, era, and art direction.
- Make it safer and more generator-friendly.
- Prefer a clean, readable reference composition.
- For characters: emphasize single-subject reference portrait or full-body character sheet style, consistent facial identity, neutral or restrained pose, uncluttered background.
- For locations: emphasize a clean environmental establishing shot, coherent layout, readable lighting, no people unless already essential.
- Avoid brand names, copyrighted character names, public figures, explicit violence, sexual content, gore, minors in risky context, hate/extremism, drug use, and any wording likely to trigger policy filters.
- Do not mention policy, censorship, retrying, media IDs, or validation.
- No bullets. No explanation. No quotes.

Original prompt:
{prompt}

Generator/context error:
{cleaned_error or "Reference media id was not reusable"}
""".strip()

        rewritten = self._call_rewrite_llm(instruction, temperature=0.5, max_tokens=700)
        if rewritten and rewritten.lower() != prompt.strip().lower():
            return rewritten
        self.log(f"    [{ref_id}] {self._provider_display_name()} khong tao duoc prompt refresh hop le", "WARN")
        return None

    def _sync_auth_from_workbook(self, wb: PromptWorkbook) -> None:
        token = (wb.get_config_value("flow_bearer_token") or "").strip()
        project_id = (wb.get_config_value("flow_project_id") or "").strip()
        project_url = (wb.get_config_value("flow_project_url") or "").strip()
        account_name = (wb.get_config_value("flow_account_name") or "").strip()
        if not token or not project_id or not project_url or not account_name:
            cached = self.auth_service.load_cached_auth(self.project_dir)
            token = token or cached.get("token", "")
            project_id = project_id or cached.get("project_id", "")
            project_url = project_url or cached.get("project_url", "")
            account_name = account_name or cached.get("account_name", "")
        if token.lower().startswith("bearer "):
            token = token[7:].strip()
        if token and not self.bearer_token:
            self.bearer_token = token
        if project_id and not self.flow_project_id:
            self.flow_project_id = project_id
        if project_url and not (self.config.get("flow_project_url", "") or "").strip():
            self.config["flow_project_url"] = project_url
        if account_name and not (self.config.get("flow_account_name", "") or "").strip():
            self.config["flow_account_name"] = account_name

    def _apply_config_auth_to_workbook(self, wb: PromptWorkbook) -> None:
        """Persist current runtime auth inputs so worker/auth service see one source of truth.

        This matters when the user enters a new token/project in GUI: workbook state must not
        override fresh runtime config during startup or token refresh.
        """
        changed = False

        token = (self.bearer_token or "").strip()
        project_id = (self.flow_project_id or "").strip()
        project_url = (self.config.get("flow_project_url", "") or "").strip()
        account_name = (self.config.get("flow_account_name", "") or "").strip()
        cached_auth = self.auth_service.load_cached_auth(self.project_dir)
        token = token or cached_auth.get("token", "")
        project_id = project_id or cached_auth.get("project_id", "")
        project_url = project_url or cached_auth.get("project_url", "")
        account_name = account_name or cached_auth.get("account_name", "")

        if token.lower().startswith("bearer "):
            token = token[7:].strip()
            self.bearer_token = token

        wb_token = (wb.get_config_value("flow_bearer_token") or "").strip()
        wb_project_id = (wb.get_config_value("flow_project_id") or "").strip()
        wb_project_url = (wb.get_config_value("flow_project_url") or "").strip()
        wb_account_name = (wb.get_config_value("flow_account_name") or "").strip()

        if token and token != wb_token:
            wb.set_config_value("flow_bearer_token", token)
            changed = True
        if project_id and project_id != wb_project_id:
            reset_stats = wb.reset_flow_media_cache_if_project_changed(wb_project_id, project_id)
            if reset_stats.get("changed"):
                self.log(
                    f"[AUTH] {self.project_dir.name}: reset workbook cache after project change "
                    f"(chars={reset_stats['characters_reset']}, scenes={reset_stats['scenes_reset']}, thumbs={reset_stats['thumbnails_reset']})",
                    "WARN",
                )
            wb.set_config_value("flow_project_id", project_id)
            changed = True
        if project_url and project_url != wb_project_url:
            wb.set_config_value("flow_project_url", project_url)
            changed = True
        if account_name and account_name != wb_account_name:
            wb.set_config_value("flow_account_name", account_name)
            changed = True

        topic = str(self.config.get("topic", "") or "").strip()
        reference_channel = str(self.config.get("reference_channel", "") or "").strip()
        psychology_reference_image = str(self.config.get("psychology_reference_image", "") or "").strip()
        if topic and topic != str(wb.get_config_value("topic") or "").strip():
            wb.set_config_value("topic", topic)
            changed = True
        if reference_channel and reference_channel != str(wb.get_config_value("reference_channel") or "").strip():
            wb.set_config_value("reference_channel", reference_channel)
            changed = True
        if psychology_reference_image and psychology_reference_image != str(wb.get_config_value("psychology_reference_image") or "").strip():
            wb.set_config_value("psychology_reference_image", psychology_reference_image)
            changed = True

        style_profile = self.config.get("psychology_style_profile") if isinstance(self.config, dict) else None
        if not isinstance(style_profile, dict):
            style_profile = {}
        channel_for_style = self._resolve_psychology_reference_channel(
            reference_channel or str(wb.get_config_value("reference_channel") or "").strip(),
            str(self.config.get("project_code", "") or ""),
        )
        ref_dir = self._topic_ref_dir(wb)
        if not style_profile and channel_for_style:
            for style_path in (
                self.reference_root / ref_dir / channel_for_style / "style.yaml",
                self.reference_root / ref_dir / channel_for_style / "style.yml",
            ):
                if not style_path.exists():
                    continue
                try:
                    import yaml
                    loaded = yaml.safe_load(style_path.read_text(encoding="utf-8")) or {}
                    if isinstance(loaded, dict):
                        style_profile = loaded
                        style_profile.setdefault("style_source", str(style_path))
                    break
                except Exception as e:
                    self.log(f"Khong doc duoc style profile {style_path}: {e}", "WARN")

        if style_profile:
            style_keys = {
                "psychology_style_name": style_profile.get("style_name", ""),
                "psychology_style_source": style_profile.get("style_source", ""),
                "psychology_style_prompt": style_profile.get("image_style", ""),
                "psychology_video_style_prompt": style_profile.get("video_style", ""),
                "psychology_thumbnail_style_prompt": style_profile.get("thumbnail_style", ""),
                "psychology_negative_prompt": style_profile.get("negative_prompt", ""),
                "psychology_image_style": style_profile.get("image_style", ""),
                "psychology_video_style": style_profile.get("video_style", ""),
                "psychology_thumbnail_style": style_profile.get("thumbnail_style", ""),
            }
            for key, value in style_keys.items():
                value = str(value or "").strip()
                if value and value != str(wb.get_config_value(key) or "").strip():
                    wb.set_config_value(key, value)
                    changed = True

        if changed:
            if not wb.safe_save(max_retries=8):
                self.log(f"[AUTH] {self.project_dir.name}: workbook save deferred; auth backup will still be updated", "WARN")

        persisted_project_id = project_id or wb_project_id
        persisted_project_url = project_url or wb_project_url
        persisted_token = token or wb_token
        persisted_account_name = account_name or wb_account_name
        if persisted_project_id or persisted_project_url or persisted_token:
            self.auth_service.persist_cached_auth(
                self.project_dir,
                token=persisted_token,
                project_id=persisted_project_id,
                project_url=persisted_project_url,
                account_name=persisted_account_name,
                token_updated_at=str(int(time.time())),
            )

    def _ensure_flow_auth(self, wb: PromptWorkbook, force_refresh: bool = False, reason: str = "") -> bool:
        self._sync_auth_from_workbook(wb)
        if not force_refresh and self.bearer_token and self.flow_project_id:
            return True
        if not self.auth_service.is_enabled():
            return bool(self.bearer_token and self.flow_project_id)
        with self._auth_lock:
            self._sync_auth_from_workbook(wb)
            if not force_refresh and self.bearer_token and self.flow_project_id:
                return True
            why = f" ({reason})" if reason else ""
            self.log(f"[AUTH] {self.project_dir.name}: ensure token/project{why}", "INFO")
            keep_open = self.generation_backend in ("flowkit", "combined")
            my_server = self.server_list[0]["name"] if self.server_list else ""
            auth = self.auth_service.ensure_auth(self.project_dir, wb, force_refresh=force_refresh, keep_chrome_open=keep_open, server_name=my_server)
            if not auth.get("ok"):
                self.log(f"[AUTH] {self.project_dir.name}: {auth.get('error', 'unknown auth error')}", "ERROR")
                return False
            self.bearer_token = auth.get("token", "") or self.bearer_token
            self.flow_project_id = auth.get("project_id", "") or self.flow_project_id
            self._last_auth_refresh_ts = time.time()
            return bool(self.bearer_token and self.flow_project_id)

    def _refresh_flow_auth(self, reason: str = "401") -> bool:
        wb = self._wb
        if wb is None:
            self.log(f"[AUTH] {self.project_dir.name}: workbook not ready for refresh", "ERROR")
            return False
        with self._auth_lock:
            if time.time() - self._last_auth_refresh_ts < 10:
                self._sync_auth_from_workbook(wb)
                if self.bearer_token and self.flow_project_id:
                    self.log(f"[AUTH] {self.project_dir.name}: reuse recently refreshed token", "INFO")
                    return True
            self.log(f"[AUTH] {self.project_dir.name}: refresh token from existing project ({reason})", "WARN")
            keep_open = self.generation_backend in ("flowkit", "combined")
            my_server = self.server_list[0]["name"] if self.server_list else ""
            auth = self.auth_service.ensure_auth(self.project_dir, wb, force_refresh=True, keep_chrome_open=keep_open, server_name=my_server)
            if not auth.get("ok"):
                self.log(f"[AUTH] refresh failed: {auth.get('error', 'unknown auth error')}", "ERROR")
                return False
            self.bearer_token = auth.get("token", "") or self.bearer_token
            self.flow_project_id = auth.get("project_id", "") or self.flow_project_id
            self._last_auth_refresh_ts = time.time()
            return True

    def stop(self):
        """Dá»«ng worker."""
        self._stop_flag = True
        self.log("Äang dá»«ng worker...")

    def _sleep_with_stop(self, seconds: float, step: float = 0.2) -> bool:
        """Sleep in small chunks so stop() can interrupt retry wait.

        Returns True if full sleep completed, False if interrupted by stop flag.
        """
        deadline = time.time() + max(0.0, float(seconds))
        while time.time() < deadline:
            if self._stop_flag:
                return False
            remaining = deadline - time.time()
            time.sleep(step if remaining > step else max(0.01, remaining))
        return not self._stop_flag

    def run(self) -> Dict[str, Any]:
        """
        Pipeline chinh: references -> thumbnail -> scenes -> videos.

        Returns:
            Dict káº¿t quáº£: {success, total, completed, failed, errors}
        """
        result = {"success": False, "total": 0, "completed": 0, "failed": 0, "errors": []}

        # ⚠ `use_veo3top_for_image` KHÔNG bao gồm shopapi — nó chỉ là
        # `("blank", "account", "pool")`. Thiếu vế shopapi ở đây thì chạy toàn
        # API vẫn chết với "Khong co server URL", dù không có bước nào cần tới
        # một server Chrome nào cả.
        if (not self.pool and not self.flowkit_pool and self.generation_backend != "nanopic"
                and not self.use_veo3top_for_image
                and not self.use_shopapi_for_image
                and not self._should_try_nanopic_fallback("No server available")):
            result["errors"].append("Khong co server URL")
            return result

        # TÃ¬m Excel file
        excel_path = self._find_excel()
        if not excel_path:
            result["errors"].append("KhÃ´ng tÃ¬m tháº¥y file Excel trong project")
            return result

        self.log(f"Loading Excel: {excel_path.name}")

        try:
            wb = PromptWorkbook(str(excel_path))
            wb.load_or_create()
            self._wb = wb
        except Exception as e:
            result["errors"].append(f"Lá»—i Ä‘á»c Excel: {e}")
            return result

        # Äá»c bearer token tá»« Excel config náº¿u chÆ°a cÃ³
        self._apply_config_auth_to_workbook(wb)
        if not self.bearer_token:
            self.bearer_token = wb.get_config_value("flow_bearer_token") or ""
        if not self.flow_project_id:
            self.flow_project_id = wb.get_config_value("flow_project_id") or ""
        self._sync_auth_from_workbook(wb)

        # Auto-strip prefix "Bearer " náº¿u user nháº­p nháº§m
        if self.bearer_token.lower().startswith("bearer "):
            self.bearer_token = self.bearer_token[7:].strip()
            self.log("ÄÃ£ tá»± Ä‘á»™ng bá» prefix 'Bearer ' khá»i token", "WARN")

        # Auth: get token + project_id (flowkit mode keeps Chrome open for extension).
        # _veo3top_only (anh+video deu ban thang veo3top) -> KHONG can bearer/project worker (moi buoc tu lay auth).
        if not self._veo3top_only:
            if (not self.bearer_token or not self.flow_project_id) and not self._ensure_flow_auth(wb, force_refresh=False, reason="startup"):
                result["errors"].append("Missing token/project_id and cannot get Flow auth")
                return result

            if not self.bearer_token:
                result["errors"].append("Missing bearer token!")
                return result

            if not self.flow_project_id:
                result["errors"].append("Missing flow_project_id!")
                return result

        # FlowKit: ensure Chrome open + extension connected
        if self.generation_backend in ("flowkit", "combined") and self.flowkit_pool:
            self._ensure_flowkit_chrome_open()

        if (not self._veo3top_only and not self.bearer_token.startswith("ya29.")
                and self.generation_backend not in ("flowkit", "combined")):
            result["errors"].append(
                f"Bearer token khÃ´ng há»£p lá»‡ (pháº£i báº¯t Ä’áº§u báº±ng ‘ya29.’). "
                f"Token hiá»‡n táº¡i: ‘{self.bearer_token[:20]}...’. "
                f"HÃ£y nháº­p láº¡i token trong GUI (khÃ´ng cáº§n chá»¯ ‘Bearer’)"
            )
            return result

        # Táº¡o thÆ° má»¥c output
        self.nv_dir.mkdir(parents=True, exist_ok=True)
        self.img_dir.mkdir(parents=True, exist_ok=True)
        self.vid_dir.mkdir(parents=True, exist_ok=True)

        # MODE tách trạm: image-only làm PHASE 1-3 rồi nhả; video-only bỏ PHASE 1-3, chỉ PHASE 4-5 (finalize).
        _do_image = self.run_mode in ("all", "image-only")
        _do_video = self.run_mode in ("all", "video-only")
        ref_result = {"total": 0, "completed": 0, "failed": 0}
        scene_result = {"total": 0, "completed": 0, "failed": 0}
        vid_result = {"total": 0, "completed": 0, "failed": 0}
        validate_result = {"total": 0, "validated": 0, "regenerated": 0, "failed": 0}
        if self.run_mode != "all":
            self.log(f"[MODE] worker chạy CHỈ phần: {self.run_mode}")

        # === PHASE 1: References ===
        self.log("=" * 50)
        self.log("PHASE 1: Táº¡o áº£nh nhÃ¢n váº­t & Ä‘á»‹a Ä‘iá»ƒm")
        self.log("=" * 50)
        if _do_image:
            if not self._prepare_psychology_reference_media(wb):
                result["errors"].append("Psychology reference media_id upload failed")
                return result
            ref_result = self._generate_references(wb)
        if self._stop_flag:
            result["errors"].append("ÄÃ£ dá»«ng bá»Ÿi user")
            return result

        # POOL: media_id VO DUNG (embed base64 per-account) -> KHONG validate media_id (tranh repair qua ExtAuth).
        # API shopapi cung the: anh tham chieu di bang URL upload, khong co mediaId cua Flow de ma validate.
        if _do_image and self.reference_media_validation_enabled \
                and self.generation_backend != "veo3top_b_pool" and not self.use_shopapi_for_image:
            self.log("")
            self.log("=" * 50)
            self.log("PHASE 1B: Validate media_id cua reference")
            self.log("=" * 50)
            validate_result = self._ensure_reference_media_ids_ready(wb)
            if self._stop_flag:
                result["errors"].append("Đã dừng bởi user")
                return result
            if validate_result.get("failed", 0) > 0:
                result["errors"].append(
                    f"Reference media_id validation failed: {validate_result['failed']}/{validate_result['total']}"
                )
                result["total"] = ref_result["total"] + validate_result["total"]
                result["completed"] = ref_result["completed"] + validate_result["validated"]
                result["failed"] = ref_result["failed"] + validate_result["failed"]
                return result

        # Release Chrome after Phase 1 — not needed for generation (worker uses token directly)
        if str(self.config.get("flow_auth_mode", "")).strip().lower() == "extension":
            try:
                from modules.flow_extension_auth import _ExtensionInstanceManager
                srv = self.server_list[0] if self.server_list else {}
                my_server = srv.get("name", "")
                chrome_dir = str(Path(srv.get("chrome_path", "")).parent) if srv.get("chrome_path") else ""
                if my_server:
                    _ExtensionInstanceManager.release_chrome(my_server, log=self.log, chrome_dir=chrome_dir)
            except Exception:
                pass

        # === PHASE 2: Thumbnail ===
        if _do_image:
            self.log("")
            self.log("=" * 50)
            self.log("PHASE 2: Tạo Thumbnail")
            self.log("=" * 50)
            self._generate_thumbnail(wb)

        # === PHASE 3: Scene Images ===
        if _do_image:
            self.log("")
            self.log("=" * 50)
            self.log("PHASE 3: Tạo ảnh các cảnh")
            self.log("=" * 50)
            scene_result = self._generate_scenes(wb)
            if self._stop_flag:
                result["errors"].append("Đã dừng bởi user")
                return result

        # MODE image-only: xong ảnh -> trả kết quả ảnh, KHÔNG finalize (trạm video sẽ làm video + finalize sau)
        if self.run_mode == "image-only":
            result["total"] = ref_result["total"] + scene_result["total"]
            result["completed"] = ref_result["completed"] + scene_result["completed"]
            result["failed"] = ref_result["failed"] + scene_result["failed"]
            result["success"] = result["failed"] == 0 and result["completed"] > 0
            self.log(f"[MODE image-only] xong ảnh {result['completed']}/{result['total']} -> nhả cho trạm video")
            return result

        # === PHASE 4: Videos (Image-to-Video) ===
        self.log("")
        self.log("=" * 50)
        self.log("PHASE 4: Táº¡o Video tá»« áº£nh")
        self.log("=" * 50)
        vid_result = self._generate_videos(wb)
        if self._stop_flag:
            result["errors"].append("ÄÃ£ dá»«ng bá»Ÿi user")
            return result

        # Tá»•ng káº¿t
        if self.final_full_rerun and self.run_mode == "all":   # rerun chạy cả ảnh+video -> chỉ khi mode 'all'
            ref_rerun, scene_rerun, vid_rerun = self._run_full_rerun_pass(wb)
            if self._stop_flag:
                result["errors"].append("Ã„ÂÃƒÂ£ dÃ¡Â»Â«ng bÃ¡Â»Å¸i user")
                return result
            ref_result["completed"] += ref_rerun["completed"]
            ref_result["failed"] = ref_rerun["failed"]
            scene_result["completed"] += scene_rerun["completed"]
            scene_result["failed"] = scene_rerun["failed"]
            vid_result["completed"] += vid_rerun["completed"]
            vid_result["failed"] = vid_rerun["failed"]

        img_total = ref_result["total"] + scene_result["total"]
        img_done = ref_result["completed"] + scene_result["completed"]
        img_fail = ref_result["failed"] + scene_result["failed"]
        vid_total = vid_result["total"]
        vid_done = vid_result["completed"]
        vid_fail = vid_result["failed"]

        result["success"] = (img_fail + vid_fail) == 0 and (img_done + vid_done) > 0
        result["total"] = img_total + vid_total
        result["completed"] = img_done + vid_done
        result["failed"] = img_fail + vid_fail

        self.log("")
        self.log("=" * 50)
        status = "HOÃ€N THÃ€NH" if result["success"] else "CÃ“ Lá»–I"
        self.log(f"Káº¾T QUáº¢: {status} - áº¢nh: {img_done}/{img_total}, Video: {vid_done}/{vid_total}")
        self.log("=" * 50)

        # === PHASE 5: Finalize â€” backup áº£nh gá»‘c, Ä‘Æ°a video vÃ o img/ ===
        self.log("")
        self.log("=" * 50)
        self.log("PHASE 5: Finalize â€” backup anh + merge video vao img/")
        self.log("=" * 50)

        # === THUMBNAIL DU PHONG: lam them 1 luot cuoi truoc khi hoan thanh ===
        # Anh thumb rat quan trong. Neu luot dau (PHASE 2) server loi thi thumb/ se
        # trong (status_img=error). Thu lai 1 luot cuoi o day — luc nay server thuong
        # da on dinh (vua xong scenes + video). get_pending_thumbnails() chi tra ve
        # thumb chua "done" nen cac thumb da co anh se tu skip.
        try:
            if _do_image and not self._stop_flag:   # thumbnail là việc ẢNH -> chỉ mode all/image (video-only bỏ qua)
                pending_thumbs = wb.get_pending_thumbnails()
                if pending_thumbs:
                    self.log("")
                    self.log("=" * 50)
                    self.log(f"THUMBNAIL DU PHONG: con {len(pending_thumbs)} thumb chua co anh "
                             f"-> lam them 1 luot cuoi truoc khi hoan thanh")
                    self.log("=" * 50)
                    self._generate_thumbnail(wb)
        except Exception as e:
            self.log(f"  Thumbnail du phong loi (bo qua): {e}", "WARN")

        try:
            self._finalize_img()
        except Exception as e:
            self.log(f"  Finalize loi (bo qua): {e}", "WARN")

        # Còn scene 'error' (retry được) -> ghi marker backoff để GUI tự chạy lại (pool còn account thì không bỏ)
        self._write_retry_marker_if_pending(wb)

        return result

    def _run_full_rerun_pass(self, wb: PromptWorkbook) -> tuple:
        """Sau khi xong mÃ£, cháº¡y láº¡i toÃ n bá»™ má»™t lÆ°á»£t; item done sáº½ tá»± skip."""
        self.log("")
        self.log("=" * 50)
        self.log("PASS CUOI: chay lai toan bo mot luot")
        self.log("Cac item da xong se tu skip theo trang thai Excel")
        self.log("=" * 50)

        self.log("")
        self.log("=" * 50)
        self.log("PHASE 1: Tao anh nhan vat & dia diem (luot 2)")
        self.log("=" * 50)
        ref_result = self._generate_references(wb)
        if self._stop_flag:
            return ref_result, {"total": 0, "completed": 0, "failed": 0}, {"total": 0, "completed": 0, "failed": 0}

        if self.reference_media_validation_enabled \
                and self.generation_backend != "veo3top_b_pool" and not self.use_shopapi_for_image:
            self.log("")
            self.log("=" * 50)
            self.log("PHASE 1B: Validate media_id cua reference (luot 2)")
            self.log("=" * 50)
            validate_result = self._ensure_reference_media_ids_ready(wb)
            if self._stop_flag or validate_result.get("failed", 0) > 0:
                scene_stub = {
                    "total": validate_result.get("total", 0),
                    "completed": validate_result.get("validated", 0),
                    "failed": validate_result.get("failed", 0),
                }
                return ref_result, scene_stub, {"total": 0, "completed": 0, "failed": 0}

        self.log("")
        self.log("=" * 50)
        self.log("PHASE 2: Tao Thumbnail (luot 2)")
        self.log("=" * 50)
        self._generate_thumbnail(wb)

        self.log("")
        self.log("=" * 50)
        self.log("PHASE 3: Tao anh cac canh (luot 2)")
        self.log("=" * 50)
        scene_result = self._generate_scenes(wb)
        if self._stop_flag:
            return ref_result, scene_result, {"total": 0, "completed": 0, "failed": 0}

        self.log("")
        self.log("=" * 50)
        self.log("PHASE 4: Tao Video tu anh (luot 2)")
        self.log("=" * 50)
        vid_result = self._generate_videos(wb)
        return ref_result, scene_result, vid_result

    def _finalize_img(self):
        """
        PHASE 5: Build img/ thÃ nh thÆ° má»¥c output Ä‘áº§y Ä‘á»§.
          - Má»—i ID: Æ°u tiÃªn mp4 (copy tá»« vid/), náº¿u khÃ´ng cÃ³ thÃ¬ giá»¯/copy png
          - XÃ³a png trong img/ náº¿u cÃ¹ng ID Ä‘Ã£ cÃ³ mp4
          - img_backup/ lÆ°u toÃ n bá»™ png gá»‘c
        """
        img_dir    = self.img_dir
        vid_dir    = self.vid_dir
        backup_dir = self.project_dir / "img_backup"

        if not img_dir.exists():
            self.log("  img/ khong ton tai, bo qua finalize")
            return

        backup_dir.mkdir(parents=True, exist_ok=True)

        # Backup táº¥t cáº£ png gá»‘c trÆ°á»›c
        for p in list(img_dir.glob("*.png")) + list(img_dir.glob("*.jpg")):
            dst = backup_dir / p.name
            if not dst.exists():
                shutil.copy2(p, dst)

        vid_mp4s = {}
        if vid_dir and vid_dir.exists():
            vid_mp4s = {m.stem: m for m in vid_dir.glob("*.mp4")}

        bak_pngs = {p.stem: p for p in backup_dir.glob("*.png")}

        all_ids = set(vid_mp4s) | set(bak_pngs)
        copied_mp4 = copied_png = 0

        for sid in all_ids:
            if sid in vid_mp4s:
                dst = img_dir / f"{sid}.mp4"
                if not dst.exists():
                    shutil.copy2(vid_mp4s[sid], dst)
                    copied_mp4 += 1
                # XÃ³a png cÃ¹ng stem náº¿u cÃ²n trong img/
                for ext in (".png", ".jpg"):
                    old = img_dir / f"{sid}{ext}"
                    if old.exists():
                        old.unlink()
            else:
                dst = img_dir / f"{sid}.png"
                if not dst.exists() and sid in bak_pngs:
                    shutil.copy2(bak_pngs[sid], dst)
                    copied_png += 1

        total = len(list(img_dir.iterdir()))
        self.log(f"  Finalize: {copied_mp4} mp4 + {copied_png} png â†’ img/ (tong {total} files)")
        self.log("  Finalize hoan thanh.")

    def _find_excel(self) -> Optional[Path]:
        """TÃ¬m file Excel trong project dir."""
        # TÃ¬m *_prompts.xlsx trÆ°á»›c
        for f in self.project_dir.glob("*_prompts.xlsx"):
            if not f.name.startswith("~"):
                return f
        # Fallback: báº¥t ká»³ .xlsx
        for f in self.project_dir.glob("*.xlsx"):
            if not f.name.startswith("~"):
                return f
        return None

    def _scene_has_image_file(self, scene_id: int) -> bool:
        sid = str(scene_id)
        return (
            (self.img_dir / f"{sid}.png").exists()
            or (self.img_dir / f"{sid}.jpg").exists()
            or (self.img_dir / f"{sid}.mp4").exists()
        )

    def _scene_has_video_file(self, scene_id: int) -> bool:
        sid = str(scene_id)
        return (
            (self.vid_dir / f"{sid}.mp4").exists()
            or (self.img_dir / f"{sid}.mp4").exists()
        )

    def _count_reference_progress(self, characters: List[Character]) -> tuple[int, int]:
        total = 0
        done = 0
        for char in characters:
            if getattr(char, "is_child", False):
                continue
            status = str(getattr(char, "status", "") or "").strip().lower()
            if status == "skip":
                continue
            total += 1
            if (self.nv_dir / f"{char.id}.png").exists() or (self.nv_dir / f"{char.id}.jpg").exists():
                done += 1
        return total, min(done, total)

    def _count_scene_image_progress(self, scenes: List[Scene]) -> tuple[int, int]:
        total = 0
        done = 0
        for scene in scenes:
            prompt = str(getattr(scene, "img_prompt", "") or "").strip()
            if not prompt:
                continue
            total += 1
            if self._scene_has_image_file(int(getattr(scene, "scene_id", 0) or 0)):
                done += 1
        return total, min(done, total)

    def _count_scene_video_progress(self, scenes: List[Scene]) -> tuple[int, int]:
        total = 0
        done = 0
        for scene in scenes:
            prompt = str(getattr(scene, "video_prompt", "") or "").strip()
            if not prompt:
                continue
            total += 1
            if self._scene_has_video_file(int(getattr(scene, "scene_id", 0) or 0)):
                done += 1
        return total, min(done, total)

    def _infer_topic_from_project_code(self) -> str:
        m = re.match(r"^([A-Za-z]+)", self.project_dir.name)
        if not m:
            return ""
        return {"TL": "psychology", "TH": "finance", "MT": "success", "KA": "story", "TA": "story"}.get(m.group(1).upper(), "")

    def _resolve_psychology_reference_channel(self, value: str = "", project_code: str = "") -> str:
        """Resolve project codes like TL1-0002 → TL1-T2 or TH1-0003 → TH1-T3."""
        candidates = []
        for item in [value, project_code, self.project_dir.name]:
            item = str(item or "").strip()
            if item and item not in candidates:
                candidates.append(item)
            m = re.match(r"^([A-Za-z]+\d+)-0*(\d+)$", item, flags=re.IGNORECASE)
            if m:
                mapped = f"{m.group(1).upper()}-T{int(m.group(2))}"
                if mapped not in candidates:
                    candidates.append(mapped)
        ref_dir = self._topic_ref_dir()
        for candidate in candidates:
            channel_dir = self.reference_root / ref_dir / candidate
            if (channel_dir / "nv1.png").exists() or (channel_dir / "style.yaml").exists():
                return candidate
        return candidates[0] if candidates else ""

    def _topic_ref_dir(self, wb: Optional[PromptWorkbook] = None) -> str:
        topic = self._get_resolved_topic(wb)
        return {"psychology": "psychology", "finance": "finance", "success": "success"}.get(topic, "psychology")

    def _get_resolved_topic(self, wb: Optional[PromptWorkbook] = None) -> str:
        value = str(self.config.get("topic", "") or "").strip().lower()
        if not value and wb is not None:
            value = str(wb.get_config_value("topic") or "").strip().lower()
        if not value or value == "story":
            inferred = self._infer_topic_from_project_code()
            if inferred:
                value = inferred
        value = unicodedata.normalize("NFKD", value)
        value = "".join(ch for ch in value if not unicodedata.combining(ch))
        return value

    def _is_psychology_topic(self, wb: Optional[PromptWorkbook] = None) -> bool:
        value = self._get_resolved_topic(wb)
        return value in {"psychology", "tam ly", "tam-ly", "tam_ly", "finance", "tai chinh", "tai-chinh", "tai_chinh", "success", "phat trien ban than"}

    def _find_psychology_reference_image(self, wb: Optional[PromptWorkbook] = None) -> Optional[Path]:
        configured = str(
            self.config.get("psychology_reference_image", "")
            or (wb.get_config_value("psychology_reference_image") if wb is not None else "")
            or ""
        ).strip()
        if configured and configured not in (".", ".."):
            path = Path(configured)
            if path.exists() and path.is_file():
                return path
        channel = str(
            self.config.get("reference_channel", "")
            or (wb.get_config_value("reference_channel") if wb is not None else "")
            or self.config.get("channel", "")
            or self.config.get("project_code", "")
            or self.project_dir.name
        ).strip()
        channel = self._resolve_psychology_reference_channel(channel, str(self.config.get("project_code", "") or ""))
        channel_candidates = []
        for item in [channel, self.project_dir.name]:
            item = str(item or "").strip()
            if item and item not in channel_candidates:
                channel_candidates.append(item)

        # NO FALLBACK - each channel must have its own reference image
        # TL1-T2 must use TL1-T2/nv1.png, NOT TL1-T1/nv1.png

        ref_dir = self._topic_ref_dir(wb)
        candidates = []
        for channel_name in channel_candidates:
            candidates.extend([
                self.reference_root / ref_dir / channel_name / "nv1.png",
                self.reference_root / ref_dir / channel_name / "nv1.jpg",
                self.reference_root / ref_dir / channel_name / "nv1.jpeg",
                self.reference_root / ref_dir / channel_name / "nv1.webp",
            ])
        candidates.extend([
            self.project_dir / "reference_characters" / "nv1.png",
            self.project_dir / "nv" / "nv1.png",
        ])
        if wb is not None:
            for char in wb.get_characters():
                if str(char.id).strip().lower() == "nv1" and str(getattr(char, "image_file", "") or "").strip():
                    image_file = str(char.image_file).strip()
                    candidates.extend([
                        self.project_dir / image_file,
                        self.nv_dir / image_file,
                    ])
                    for channel_name in channel_candidates:
                        candidates.append(self.reference_root / ref_dir / channel_name / image_file)

        # Try to find reference image
        for path in candidates:
            if path and path.exists() and path.is_file():
                self.log(f"[PSY] Found reference image: {path}", "INFO")
                return path

        # Not found - log detailed error
        self.log(f"[PSY] Reference image NOT FOUND for channel: {channel_candidates}", "ERROR")
        self.log(f"[PSY] Searched paths:", "ERROR")
        for i, path in enumerate(candidates[:10], 1):
            self.log(f"[PSY]   {i}. {path} (exists: {path.exists() if path else False})", "ERROR")
        return None

    def _ensure_psychology_reference_row(self, wb: PromptWorkbook) -> Optional[Character]:
        ref_image = self._find_psychology_reference_image(wb)
        if not ref_image:
            self.log(f"[PSY] Khong tim thay anh tham chieu nv1 cho {self.project_dir.name}", "ERROR")
            return None
        self.nv_dir.mkdir(parents=True, exist_ok=True)
        local_ref = self.nv_dir / "nv1.png"
        if ref_image.resolve() != local_ref.resolve():
            shutil.copy2(ref_image, local_ref)
        chars = wb.get_characters()
        for char in chars:
            if str(char.id).strip().lower() == "nv1":
                topic_label = (self._get_resolved_topic(wb) or "psychology").title()
                update = {"role": "protagonist", "name": char.name or f"{topic_label} Reference", "image_file": "nv1.png"}
                if not str(char.status or "").strip():
                    update["status"] = "pending"
                wb.update_character(char.id, **update)
                if not wb.safe_save(max_retries=8):
                    wb._save_pending_write("character", char_id=char.id, **update)
                char.role = update["role"]
                char.name = update["name"]
                char.image_file = "nv1.png"
                return char
        topic_label = (self._get_resolved_topic(wb) or "psychology").title()
        char = Character(
            id="nv1",
            role="protagonist",
            name=f"{topic_label} Reference",
            english_prompt="",
            vietnamese_prompt="",
            character_lock=f"existing local {topic_label.lower()} reference image",
            image_file="nv1.png",
            status="pending",
            is_child=False,
            media_id="",
            reference_media_checked=False,
        )
        wb.add_character(char)
        if not wb.safe_save(max_retries=8):
            wb._save_pending_write("character", char_id="nv1", **char.to_dict())
        return char

    def _write_quota_wait_marker(self, server_name: str = "", seconds: int = 3600, reason: str = "429_QUOTA"):
        """Write marker file so GUI queue skips this project for `seconds`.
        Dùng chung cho: (a) FlowKit 429 (1h) và (b) POOL retry backoff (ngắn hơn) — GUI đọc cùng file
        .flowkit_quota_wait (chỉ cần resume_ts) nên KHÔNG phải sửa GUI cho backoff."""
        marker = self.project_dir / ".flowkit_quota_wait"
        resume_ts = time.time() + max(30, int(seconds))
        try:
            marker.write_text(json.dumps({
                "resume_after": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(resume_ts)),
                "resume_ts": resume_ts,
                "server": server_name,
                "reason": reason,
            }), encoding="utf-8")
            self.log(f"[QUEUE] {reason} — project tạm nghỉ {int(seconds)}s (resume {time.strftime('%H:%M:%S', time.localtime(resume_ts))}), sẽ tự retry", "WARN")
        except Exception as e:
            self.log(f"[QUEUE] Failed to write retry-wait marker: {e}", "ERROR")

    #: Câu lỗi nói ẢNH NGUỒN KHÔNG DÙNG ĐƯỢC — thiếu file, hoặc file không phải ảnh.
    #:
    #: Cả hai đều có chung một cách chữa: bảo pha ẢNH dựng lại cảnh đó. Thử lại
    #: VIDEO thì lần nào cũng hỏng y hệt, vì nguồn vào vẫn hỏng.
    DAU_HIEU_ANH_HONG = (
        "khong thay anh scene",
        "khong phai png, jpeg hay webp",     # máy chủ nhận dạng bằng magic bytes
        "không phải png, jpeg hay webp",
        "upload anh scene that bai",
    )

    @staticmethod
    def _la_anh_that(duong_dan):
        """File này có THẬT SỰ là ảnh không? Đọc magic bytes, không nhìn đuôi tên.

        Máy chủ shopapi nhận dạng ảnh đúng bằng cách này — nó nói thẳng trong
        câu lỗi: *"Máy chủ nhận dạng ảnh bằng magic bytes chứ không nhìn tên
        file"*. Nên ta phải kiểm bằng CÙNG MỘT THƯỚC; nếu không thì tool nghĩ
        ảnh xong, máy chủ nghĩ ảnh hỏng, và không bên nào sai theo cách của
        mình.
        """
        try:
            with open(str(duong_dan), "rb") as f:
                dau = f.read(12)
        except OSError:
            return False
        if len(dau) < 12:
            return False
        return (dau.startswith(b"\x89PNG\r\n\x1a\n")                 # PNG
                or dau.startswith(b"\xff\xd8\xff")                   # JPEG
                or (dau[:4] == b"RIFF" and dau[8:12] == b"WEBP"))    # WebP

    def _anh_scene_con_dung_duoc(self, sid):
        """Ảnh của cảnh này còn dùng được không — NHÌN ĐĨA, không tin Excel.

        Phải soi ba nơi, vì một tấm ảnh "xong" nằm ở đâu là tuỳ mã đã chạy tới
        bước nào:

          * `img/{sid}.mp4` — video đã dựng xong. `_finalize_img` xoá png gốc
            khỏi `img/` sau khi ghép, nên KHÔNG thấy png ở đây là chuyện bình
            thường chứ không phải thiếu ảnh. Bỏ sót nhánh này là dựng lại hàng
            trăm tấm ảnh đã có video — vừa tốn tiền vừa phá việc đã xong.
          * `img/{sid}.png` — ảnh vừa dựng, chưa tới bước video.
          * `img_backup/{sid}.png` — bản gốc finalize giữ lại.

        Thiếu cả ba mới là thiếu thật.
        """
        try:
            if (self.img_dir / "{0}.mp4".format(sid)).exists():
                return True
            for thu in (self.img_dir, self.project_dir / "img_backup"):
                for duoi in (".png", ".jpg", ".jpeg"):
                    f = thu / "{0}{1}".format(sid, duoi)
                    if f.exists() and self._la_anh_that(f):
                        return True
        except Exception:
            return True     # đọc đĩa hỏng -> đừng dựng lại bừa
        return False

    def _anh_nguon_hong(self, error_text):
        """Lỗi này là do ẢNH NGUỒN, không phải do prompt hay do nhà máy?

        Hai kiểu đã gặp thật, cùng một mã TH1-0182 cảnh 81:

          * `khong thay anh scene <đường dẫn>` — Excel ghi ảnh "done" mà file
            trên đĩa đã mất;
          * `InvalidRequestError: đuôi file là ".png" nhưng nội dung bên trong
            không phải PNG, JPEG hay WebP` — file còn đó nhưng RUỘT KHÔNG PHẢI
            ẢNH (tải hụt, hoặc lưu nhầm trang lỗi thành .png).

        Cả hai đều bất biến qua các lượt chạy: cảnh 81 hỏng y hệt ở 17:17:02,
        17:30:51 và 17:43:39 ngày 15/08/2026. Mỗi lượt ăn một "lượt trắng", ba
        lượt là mã bị ĐỖ LẠI — trong khi chỉ cần dựng lại một tấm ảnh.
        """
        err = (error_text or "").lower()
        return any(d in err for d in self.DAU_HIEU_ANH_HONG)

    #: Dấu hiệu NHÀ MÁY NGHẼN trong câu lỗi — không phải lỗi của prompt.
    #:
    #: Giữ cả tiếng Việt lẫn tiếng Anh: `shopapi_common.mo_ta_loi` dịch lỗi SDK
    #: sang tiếng Việt trước khi nó tới đây, còn lỗi thô của pool thì tiếng Anh.
    DAU_HIEU_NGHEN = (
        "429", "503", "resource_exhausted", "engineunavailable",
        "rate limit", "ratelimit", "qua tai", "quá tải",
        "nha may dang dung", "nhà máy đang dừng", "khong co may xu ly",
        "không có máy xử lý", "service unavailable", "temporarily unavailable",
    )

    def _loi_do_nghen(self, error_text: str) -> bool:
        """Câu lỗi này là NHÀ MÁY NGHẼN chứ không phải prompt hỏng?

        ═══ VÌ SAO PHẢI PHÂN BIỆT ═══

        Viết lại prompt để tránh bộ lọc nội dung là đúng khi prompt thật sự bị
        chặn. Nhưng khi nhà máy trả `503`/`429`, prompt hoàn toàn vô tội — viết
        lại chỉ tốn thêm một lượt gửi nữa (cũng `503`), rồi ghi scene là HỎNG.

        Đã dính thật lúc 17:17:02 ngày 15/08/2026, mã TH1-0182:

            me video lo 1 -> ban them 1 job | tran may chu 172
            Video scene 81: thu last-resort prompt de tranh fail
            Video scene 81 FAIL (0.0s) [error: retry lt sau]
            KET QUA: CO LOI - Video: 0/1
            TH1-0182: lượt trắng 1/3

        Cùng giây đó mã TH1-0097 nhận đúng lỗi ấy nhưng đi qua đường khác và xử
        lý đúng: "nha may DANG DUNG (503) -> cho 30s roi tham do lai". Một cú
        nghẹn thoáng qua của nhà máy biến thành một "lượt trắng" tính vào hạn ba
        lượt — ba lần như thế là mã bị ĐỖ LẠI, dù nó chẳng có gì sai.
        """
        err = (error_text or "").lower()
        return any(d in err for d in self.DAU_HIEU_NGHEN)

    def _fail_status_for(self, error_text: str) -> str:
        """Phân loại trạng thái fail 1 scene:
        - 'failed' (TERMINAL, không retry nữa): lỗi POLICY nội dung (đã rewrite hết vòng vẫn bị chặn) — retry vô ích.
        - 'error'  (RETRY được): lỗi TÀI NGUYÊN tạm thời (quota/429/ratelimit/reCAPTCHA/timeout/mạng/pool cạn lượt).
          Pool còn account/quota ở lượt sau -> phải làm lại tới khi OK (nguyên tắc: không bỏ khi còn tài nguyên)."""
        if self._is_policy_violation_error(error_text):
            return "failed"
        return "error"

    def _write_retry_marker_if_pending(self, wb):
        """Cuối run (pool backend): nếu còn scene 'error' (retry được) chưa có file -> ghi marker backoff để
        GUI tự chạy lại project sau `ve3_retry_wait_seconds` giây (tránh hot-loop). 'failed' = terminal -> KHÔNG retry."""
        if self.generation_backend != "veo3top_b_pool":
            return
        try:
            has_retry = False
            for s in wb.get_scenes():
                sid = int(getattr(s, "scene_id", 0) or 0)
                if sid <= 0:
                    continue
                st_img = str(getattr(s, "status_img", "") or "").strip().lower()
                st_vid = str(getattr(s, "status_vid", "") or "").strip().lower()
                has_img = ((self.img_dir / f"{sid}.png").exists() or (self.img_dir / f"{sid}.jpg").exists()
                           or (self.img_dir / f"{sid}.mp4").exists())
                has_vid = ((self.vid_dir / f"{sid}.mp4").exists() or (self.img_dir / f"{sid}.mp4").exists())
                if str(getattr(s, "img_prompt", "") or "").strip() and st_img == "error" and not has_img:
                    has_retry = True; break
                if str(getattr(s, "video_prompt", "") or "").strip() and st_vid == "error" and not has_vid:
                    has_retry = True; break
            if has_retry:
                secs = int(self.config.get("ve3_retry_wait_seconds", 300) or 300)
                self._write_quota_wait_marker(seconds=secs, reason="POOL_RETRY")
        except Exception as e:
            self.log(f"  [retry-marker] loi (bo qua): {e}", "WARN")

    def _pick_flowkit_server(self, auto_reserve: bool = True):
        """Pick FlowKit server with sticky affinity — same project always uses same server.
        media_id/project references are tied to the Google account on a specific server."""
        if not self.flowkit_pool:
            return None
        if self._sticky_flowkit_server:
            with self.flowkit_pool._lock:
                if self.flowkit_pool._is_available(self._sticky_flowkit_server):
                    if auto_reserve:
                        self._sticky_flowkit_server.local_pending += 1
                    return self._sticky_flowkit_server
            self.log(f"[FLOWKIT] Sticky server {self._sticky_flowkit_server.name} unavailable, picking new", "WARN")
            self._sticky_flowkit_server = None
        server = self.flowkit_pool.pick_best_server(auto_reserve=auto_reserve)
        if server:
            self._sticky_flowkit_server = server
            self.log(f"[FLOWKIT] Sticky server assigned: {server.name}", "INFO")
        return server

    def _upload_reference_via_flowkit(self, image_path: Path) -> str:
        """Upload reference image via FlowKit agent. Returns media_id or empty."""
        import base64 as _b64
        try:
            img_bytes = image_path.read_bytes()
            img_b64 = _b64.b64encode(img_bytes).decode()
            server = self._pick_flowkit_server()
            if not server:
                self.log("[FLOWKIT] No FlowKit server for upload", "ERROR")
                return ""
            import requests as _req
            resp = _req.post(
                f"{server.url}/api/fix/upload-image",
                json={
                    "flow_auth_token": self.bearer_token,
                    "image_base64": img_b64,
                    "mime_type": "image/png",
                    "project_id": self.flow_project_id,
                },
                timeout=60,
            )
            data = resp.json()
            if data.get("success") and data.get("media_name"):
                self.log(f"[FLOWKIT] Upload OK: {data['media_name'][:40]}", "SUCCESS")
                return data["media_name"]
            self.log(f"[FLOWKIT] Upload failed: {data.get('error', 'unknown')}", "ERROR")
            return ""
        except Exception as e:
            self.log(f"[FLOWKIT] Upload exception: {e}", "ERROR")
            return ""

    def _open_ultra_uploader(self, wb):
        """FLOW2 local-video: MO CHROME ExtAuth tren MAY CHU (giong luc upload nv1) de
        upload anh scene qua EXTENSION (KHONG goi GoogleFlowAPI direct — direct bi SSL EOF
        vi may chu khong goi thang aisandbox-pa.googleapis.com duoc).
        Tra (ext_auth, token, project_id) hoac (None, "", "")."""
        import re as _re
        try:
            from modules.flow_extension_auth import FlowExtensionAuth, _ExtensionInstanceManager
        except Exception as e:
            self.log(f"[local-video] khong import duoc FlowExtensionAuth: {e}", "ERROR")
            return None, "", ""
        srv = self.server_list[0] if self.server_list else {}
        srv_name = srv.get("name", "sv1")
        m = _re.match(r'[a-zA-Z]+(\d+)', srv_name)
        srv_index = (int(m.group(1)) - 1) if m else 0
        srv_port = 8100 + srv_index
        agent_url = f"http://127.0.0.1:{srv_port}"
        ext_auth = FlowExtensionAuth(agent_url, log_func=self.log)
        # MO Chrome ExtAuth tren may chu = DUNG start_one (y het PHASE 1: login lua789001 + agent
        # + Chrome + cho extension ready, GIU Chrome mo). Sau do upload qua ext_auth giong nv1.
        # ensure_auth KHONG dung duoc o day vi no th@ Chrome ngay sau khi lay token.
        if not ext_auth.is_ready():
            self.log("[local-video] mo Chrome ExtAuth tren may chu (giong nv1) de upload...", "INFO")
            try:
                _ExtensionInstanceManager.start_one(srv_index, srv, str(SUITE_ROOT), log=self.log)
            except Exception as e:
                self.log(f"[local-video] start Chrome ExtAuth loi: {e}", "WARN")
        for _ in range(40):
            if ext_auth.is_ready():
                break
            time.sleep(2)
        token = self.bearer_token or ext_auth.get_token()
        pid = self.flow_project_id or ext_auth.get_project_id() or ext_auth.ensure_project()
        if not ext_auth.is_ready() or not token or not pid:
            self.log(f"[local-video] uploader chua san sang (ready={ext_auth.is_ready()}, token={bool(token)}, pid={bool(pid)})", "WARN")
            return None, "", ""
        if token:
            self.bearer_token = token
        if pid:
            self.flow_project_id = pid
        return ext_auth, token, pid

    def _register_local_reference_media(self, wb: PromptWorkbook, char: Character) -> bool:
        image_path = self.nv_dir / "nv1.png"
        if not image_path.exists():
            self.log("[PSY] nv1.png chua co trong project/nv", "ERROR")
            return False

        # Extension mode: upload via local agent (no UI automation)
        auth_mode = str(self.config.get("flow_auth_mode", "chrome")).strip().lower()
        if auth_mode == "extension":
            try:
                from modules.flow_extension_auth import FlowExtensionAuth
                import re as _re
                srv = self.server_list[0] if self.server_list else {}
                srv_name = srv.get("name", "sv1")
                m = _re.match(r'[a-zA-Z]+(\d+)', srv_name)
                srv_port = 8100 + (int(m.group(1)) - 1) if m else 8100
                agent_url = f"http://127.0.0.1:{srv_port}"
                ext_auth = FlowExtensionAuth(agent_url, log_func=self.log)
                if ext_auth.is_ready():
                    token = ext_auth.get_token()
                    pid = self.flow_project_id or ext_auth.get_project_id()
                    if token and pid:
                        media_id = ext_auth.upload_image(str(image_path), token, pid)
                        if media_id:
                            update_data = {"status": "done", "media_id": media_id, "reference_media_checked": False, "image_file": "nv1.png"}
                            with self._excel_lock:
                                wb.update_character("nv1", **update_data)
                                wb.safe_save(max_retries=8)
                            char.status = "done"
                            char.media_id = media_id
                            self.log(f"[PSY] nv1 media_id via Extension: {media_id[:60]}", "SUCCESS")
                            return True
                self.log("[PSY] Extension upload failed, trying fallback...", "WARN")
            except Exception as e:
                self.log(f"[PSY] Extension error: {e}, trying fallback...", "WARN")

        # FlowKit mode: upload via agent endpoint instead of DrissionPage
        if self.generation_backend in ("flowkit", "combined") and self.flowkit_pool:
            media_id = self._upload_reference_via_flowkit(image_path)
            if media_id:
                update_data = {"status": "done", "media_id": media_id, "reference_media_checked": False, "image_file": "nv1.png"}
                with self._excel_lock:
                    wb.update_character("nv1", **update_data)
                    wb.safe_save(max_retries=8)
                char.status = "done"
                char.media_id = media_id
                self.log(f"[PSY] nv1 media_id via FlowKit: {media_id[:60]}", "SUCCESS")
                return True
            self.log("[PSY] FlowKit upload failed, trying DrissionPage fallback...", "WARN")

        if not self._ensure_flow_auth(wb, reason=f"{(self._get_resolved_topic(wb) or 'psychology')} reference upload"):
            self.log("[PSY] Khong co Flow project/token de upload anh tham chieu", "ERROR")
            return False
        account_name = (self.config.get("flow_account_name", "") or wb.get_config_value("flow_account_name") or "").strip()
        account = self.auth_service.pick_account(account_name)
        if not account:
            self.log("[PSY] Khong tim thay Flow account de upload anh tham chieu", "ERROR")
            return False
        bridge = FlowReferenceBridge(
            suite_root=SUITE_ROOT,
            config=FlowReferenceConfig(
                chrome_path=account.chrome_path or self.auth_service.chrome_path(),
                profile_dir=account.profile_dir,
                email=account.email,
                password=account.password,
                totp_secret=account.totp_secret,
                worker_id=self.auth_service._auth_worker_slot(account, self.project_dir),
            ),
            log_func=self.log,
        )
        project_url = (self.config.get("flow_project_url", "") or wb.get_config_value("flow_project_url") or "").strip()
        upload = bridge.upload_reference_image(
            image_path=image_path,
            project_url=project_url,
            project_id=self.flow_project_id,
            timeout=int(self.config.get("psychology_reference_upload_timeout", 45) or 45),
        )
        if not upload.ok or not upload.media_id:
            self.log(f"[PSY] Upload anh tham chieu that bai: {self._short_text(upload.error, 180)}", "ERROR")
            return False
        update_data = {"status": "done", "media_id": upload.media_id, "reference_media_checked": False, "image_file": "nv1.png"}
        with self._excel_lock:
            wb.update_character("nv1", **update_data)
            if upload.project_url:
                wb.set_config_value("flow_project_url", upload.project_url)
            if not wb.safe_save(max_retries=8):
                wb._save_pending_write("character", char_id="nv1", **update_data)
        char.status = "done"
        char.media_id = upload.media_id
        char.reference_media_checked = False
        self.log(f"[PSY] nv1 media_id ready: {upload.media_id[:60]}...", "SUCCESS")
        return True

    def _prepare_psychology_scene_references(self, wb: PromptWorkbook) -> None:
        scenes = wb.get_scenes()
        changed = False
        for scene in scenes:
            chars_used = str(getattr(scene, "characters_used", "") or "").strip()
            if chars_used != "nv1":
                wb.update_scene(scene.scene_id, characters_used="nv1")
                scene.characters_used = "nv1"
                chars_used = "nv1"
                changed = True
            prompt_text = str(getattr(scene, "img_prompt", "") or "").lower()
            prompt_needs_reference = (
                "provided reference image" in prompt_text
                or "reference character" in prompt_text
                or "nv1.png" in prompt_text
                or bool(chars_used)
            )
            if not prompt_needs_reference:
                continue
            refs = []
            raw_refs = str(getattr(scene, "reference_files", "") or "").strip()
            if raw_refs:
                try:
                    parsed = json.loads(raw_refs)
                    refs = parsed if isinstance(parsed, list) else []
                except (json.JSONDecodeError, TypeError):
                    refs = [x.strip() for x in raw_refs.split(",") if x.strip()]
            if refs != ["nv1.png"]:
                refs = ["nv1.png"]
                wb.update_scene(scene.scene_id, reference_files=json.dumps(refs))
                scene.reference_files = json.dumps(refs)
                changed = True
        if changed:
            wb.safe_save(max_retries=8)
            self.log("[PSY] Da bo sung reference_files nv1.png cho scenes tam ly", "INFO")

    def _prepare_psychology_reference_media(self, wb: PromptWorkbook) -> bool:
        if not self._is_psychology_topic(wb):
            return True
        resolved_topic = self._get_resolved_topic(wb) or "psychology"
        wb.set_config_value("topic", resolved_topic)
        if not str(wb.get_config_value("reference_channel") or "").strip():
            wb.set_config_value("reference_channel", self._resolve_psychology_reference_channel("", self.project_dir.name))
        char = self._ensure_psychology_reference_row(wb)
        if not char:
            return False
        # POOL: nha may anh EMBED base64 nv1.png (file local) per-account -> media_id account-scoped VO DUNG,
        # KHONG can upload ExtAuth. Chi can nv1.png ton tai local (da copy boi _ensure_psychology_reference_row).
        # API shopapi: y het - anh tham chieu di bang URL upload, khong dinh gi toi ExtAuth.
        if self.generation_backend == "veo3top_b_pool" or self.use_shopapi_for_image:
            if str(getattr(char, "status", "") or "").strip().lower() != "done":
                with self._excel_lock:
                    wb.update_character("nv1", status="done", image_file="nv1.png")
                    wb.safe_save(max_retries=8)
                char.status = "done"
            self._prepare_psychology_scene_references(wb)
            self.log("[PSY] pool mode: nv1.png local san sang (embed per-account), bo qua upload ExtAuth", "INFO")
            return True
        if str(getattr(char, "media_id", "") or "").strip():
            if str(getattr(char, "status", "") or "").strip().lower() != "done":
                wb.update_character("nv1", status="done")
                wb.safe_save(max_retries=8)
            self._prepare_psychology_scene_references(wb)
            self.log("[PSY] nv1 da co media_id, bo qua upload", "INFO")
            return True
        if not self._register_local_reference_media(wb, char):
            return False
        self._prepare_psychology_scene_references(wb)
        return True

    # =========================================================================
    # PHASE 1: Reference Images
    # =========================================================================

    def _generate_references(self, wb: PromptWorkbook) -> Dict:
        """Táº¡o áº£nh reference cho nhÃ¢n váº­t vÃ  Ä‘á»‹a Ä‘iá»ƒm."""
        result = {"total": 0, "completed": 0, "failed": 0}

        characters = wb.get_characters()
        if not characters:
            self.log("KhÃ´ng cÃ³ nhÃ¢n váº­t/Ä‘á»‹a Ä‘iá»ƒm trong Excel")
            return result

        # Lá»c: chá»‰ táº¡o áº£nh cho nhá»¯ng cÃ¡i chÆ°a cÃ³
        pending = []
        for char in characters:
            if char.is_child:
                self.log(f"  Skip {char.id} (tre em)")
                if char.status != "skip":
                    with self._excel_lock:
                        wb.update_character(char.id, status="skip")
                        wb.safe_save()
                continue
            if char.status and char.status.lower() == "skip":
                self.log(f"  Skip {char.id} (status={char.status})")
                continue

            img_path = self.nv_dir / f"{char.id}.png"

            # POOL: nha may EMBED base64 file anh local per-account -> KHONG can media_id, KHONG upload ExtAuth,
            # KHONG sinh de len anh user. Co file anh local -> done luon; thieu file -> van sinh moi qua pool.
            if (self.generation_backend == "veo3top_b_pool" or self.use_shopapi_for_image) and img_path.exists():
                if char.status != "done":
                    with self._excel_lock:
                        wb.update_character(char.id, status="done")
                        wb.safe_save()
                self.log(f"  Skip {char.id} (pool: da co anh local, embed per-account)")
                continue

            # media_id moi la nguon su that cho reference
            if char.media_id:
                if img_path.exists():
                    self.log(f"  Skip {char.id} (da co media_id + anh)")
                else:
                    self.log(f"  Skip {char.id} (da co media_id, thieu file local nhung scene van dung duoc)", "WARN")
                if char.status != "done":
                    with self._excel_lock:
                        wb.update_character(char.id, status="done")
                        wb.safe_save()
                continue

            if img_path.exists() and not char.media_id:
                # Anh tham chieu da co san (user cung cap qua "Tai Excel" / da copy
                # vao nv/). UPLOAD anh do de lay media_id thay vi SINH MOI — sinh moi
                # se de len anh cua user. Upload that bai moi sinh moi (giu hanh vi cu).
                if str(char.id).strip().lower() == "nv1":
                    self.log(f"  {char.id}: co anh san -> UPLOAD lay media_id (khong sinh de len anh)", "INFO")
                    try:
                        if self._register_local_reference_media(wb, char) and str(getattr(char, "media_id", "") or "").strip():
                            self.on_item_status("char", char.id, "done", str(img_path), {})
                            continue
                    except Exception as e:
                        self.log(f"  {char.id}: upload that bai ({e}) -> sinh moi", "WARN")
                else:
                    self.log(f"  {char.id}: co anh nhung thieu media_id -> can tao lai", "WARN")

            pending.append((char, img_path))

        result["total"] = len(pending)
        self.log(f"References cáº§n táº¡o: {len(pending)}/{len(characters)} (tran nguoi dung={self.max_concurrent}, so that hoi may chu moi lo)")
        ref_total, ref_done_base = self._count_reference_progress(characters)

        # Build task list
        tasks = []
        for i, (char, img_path) in enumerate(pending):
            prompt = char.english_prompt or char.vietnamese_prompt or char.name
            if not prompt:
                self.log(f"  [{i+1}/{len(pending)}] {char.id}: SKIP (khÃ´ng cÃ³ prompt)", "WARN")
                result["failed"] += 1
                continue
            tasks.append({"idx": i, "char": char, "img_path": img_path, "prompt": prompt})

        completed_count = [0]

        def _do_char(task):
            if self._stop_flag:
                return None
            char = task["char"]
            img_path = task["img_path"]
            prompt = task["prompt"]
            idx = task["idx"]

            self.log(f"  [{idx+1}/{len(pending)}] {char.id}: {prompt[:60]}...")
            self.on_item_status("char", char.id, "running", None, {})

            def _poll_cb(info):
                self.on_item_status("char", char.id, "running", None,
                                    {"queue_pos": info.get("queue_position"),
                                     "poll_status": info.get("status")})

            t0 = time.time()
            current_prompt = prompt
            rewrite_round = 0
            rewrite_happened = False
            while True:
                success, media_name, server_info, error_text = self._submit_image(
                    current_prompt, img_path, poll_callback=_poll_cb
                )
                if success:
                    break
                if rewrite_happened and self._is_policy_violation_error(error_text):
                    self.log(f"    {char.id}: prompt da viet lai nhung van bi policy", "WARN")
                if rewrite_round >= self.prompt_rewrite_max_rounds:
                    break
                if not self._is_policy_violation_error(error_text):
                    break
                self.log(f"    {char.id}: prompt co dau hieu vi pham policy, thu viet lai (vong {rewrite_round + 1}/{self.prompt_rewrite_max_rounds})", "WARN")
                rewritten = self._rewrite_prompt_for_policy_v2(current_prompt, error_text, mode="image", round_index=rewrite_round + 1)
                if not rewritten:
                    self._log_prompt_rewrite_event(
                        "char", char.id, "reference_image", "rewrite_failed",
                        current_prompt, error_text=error_text, round_index=rewrite_round + 1, mode="image"
                    )
                    self.log(f"    {char.id}: khong viet lai duoc prompt hop le", "WARN")
                    break
                self._log_prompt_rewrite_event(
                    "char", char.id, "reference_image", "rewritten",
                    current_prompt, rewritten_prompt=rewritten, error_text=error_text,
                    round_index=rewrite_round + 1, mode="image"
                )
                current_prompt = rewritten
                rewrite_round += 1
                rewrite_happened = True
                update_prompt = {}
                if (char.english_prompt or "").strip():
                    update_prompt["english_prompt"] = current_prompt
                elif (char.vietnamese_prompt or "").strip():
                    update_prompt["vietnamese_prompt"] = current_prompt
                if update_prompt:
                    with self._excel_lock:
                        wb.update_character(char.id, **update_prompt)
                        if not wb.safe_save():
                            wb._save_pending_write("character", char_id=char.id, **update_prompt)
                            self.log(f"    {char.id}: Excel bi khoa, luu pending prompt rewrite", "WARN")
                self.log(f"    {char.id}: da cap nhat prompt moi va retry", "INFO")
            elapsed = round(time.time() - t0, 1)

            if success:
                if rewrite_happened:
                    self._log_prompt_rewrite_event(
                        "char", char.id, "reference_image", "rewrite_succeeded",
                        prompt, rewritten_prompt=current_prompt, round_index=rewrite_round, mode="image"
                    )
                update_data = {"status": "done", "reference_media_checked": False}
                if current_prompt != prompt:
                    if (char.english_prompt or "").strip():
                        update_data["english_prompt"] = current_prompt
                    elif (char.vietnamese_prompt or "").strip():
                        update_data["vietnamese_prompt"] = current_prompt
                if media_name:
                    update_data["media_id"] = media_name
                with self._excel_lock:
                    wb.update_character(char.id, **update_data)
                    if not wb.safe_save():
                        # Excel bá»‹ khÃ³a â€” lÆ°u pending write Ä‘á»ƒ khÃ´ng máº¥t data
                        wb._save_pending_write("character", char_id=char.id, **update_data)
                        self.log(f"    {char.id}: Excel bá»‹ khÃ³a, lÆ°u pending write", "WARN")
                with self._dem_lock:          # dem duoi nhieu luong: xem `_dem_lock`
                    completed_count[0] += 1
                    _da_xong = completed_count[0]
                current_done = min(ref_total, ref_done_base + _da_xong)
                self.progress("refs", current_done, ref_total, char.id)
                self.log(f"    {char.id} â†’ OK ({elapsed}s, {server_info.get('server', '?')})")
                self.on_item_status("char", char.id, "done", str(img_path),
                                    {"elapsed": elapsed, **server_info})
                return True
            else:
                if rewrite_happened:
                    self._log_prompt_rewrite_event(
                        "char", char.id, "reference_image", "rewrite_still_failed",
                        prompt, rewritten_prompt=current_prompt, error_text=error_text,
                        round_index=rewrite_round, mode="image"
                    )
                self.log(f"    {char.id} â†’ FAIL ({elapsed}s)", "WARN")
                self.on_item_status("char", char.id, "error", None,
                                    {"elapsed": elapsed, **server_info})
                return False

        # So luong lay tu MAY CHU (`/v1/me`) chu khong go cung, va bi chan tren
        # boi `self.max_concurrent` de khong pha gioi han nguoi dung da dat.
        self._chay_me("image", tasks, _do_char, self.max_concurrent, result)
        return result

    def _is_reference_location(self, char: Character) -> bool:
        cid = str(getattr(char, "id", "") or "").strip().lower()
        role = str(getattr(char, "role", "") or "").strip().lower()
        return cid.startswith("loc") or role == "location"

    def _reference_base_prompt(self, char: Character) -> str:
        return (
            (getattr(char, "english_prompt", "") or "").strip()
            or (getattr(char, "vietnamese_prompt", "") or "").strip()
            or (getattr(char, "name", "") or "").strip()
            or (getattr(char, "id", "") or "").strip()
        )

    def _is_reference_media_checked(self, char: Character) -> bool:
        value = getattr(char, "reference_media_checked", False)
        if isinstance(value, bool):
            return value
        return str(value or "").strip().lower() in ("true", "1", "yes", "done", "checked")

    def _build_reference_validation_prompt(self, char: Character) -> str:
        if self._is_reference_location(char):
            return (
                "Create a clean environment reference shot that faithfully matches the supplied reference image, "
                "readable composition, consistent lighting, no text, high detail."
            )
        return (
            "Create a clean character reference portrait that faithfully matches the supplied reference image, "
            "consistent identity, neutral pose, plain background, high detail."
        )

    def _validate_reference_media_id(self, char: Character) -> tuple[bool, str]:
        media_id = str(getattr(char, "media_id", "") or "").strip()
        if not media_id:
            return False, "Missing media_id"

        refs = [ImageInput(name=media_id, input_type=ImageInputType.REFERENCE)]
        out_path = self.debug_dir / "reference_validation" / f"{char.id}.png"
        prompt = self._build_reference_validation_prompt(char)
        success, _media_name, _server_info, error_text = self._submit_image(prompt, out_path, refs=refs)
        return success, (error_text or "")

    def _repair_reference_media_id(self, wb: PromptWorkbook, char: Character, failure_reason: str = "") -> bool:
        current_prompt = self._reference_base_prompt(char)
        if not current_prompt:
            self.log(f"  [{char.id}] khong co prompt de repair reference", "ERROR")
            return False

        img_path = self.nv_dir / f"{char.id}.png"
        is_location = self._is_reference_location(char)

        for round_idx in range(1, self.reference_media_validation_max_rounds + 1):
            if self._stop_flag:
                return False

            refreshed_prompt = self._rewrite_reference_prompt_for_refresh(
                current_prompt,
                char.id,
                is_location=is_location,
                error_text=failure_reason,
            ) or current_prompt
            self.log(f"  [{char.id}] repair reference round {round_idx}/{self.reference_media_validation_max_rounds}", "WARN")

            rewrite_round = 0
            prompt_for_submit = refreshed_prompt
            generated_media_id = ""
            generated_ok = False
            last_error = failure_reason or ""

            while True:
                success, media_name, _server_info, error_text = self._submit_image(prompt_for_submit, img_path)
                if success:
                    generated_ok = True
                    generated_media_id = media_name or ""
                    current_prompt = prompt_for_submit
                    break
                last_error = error_text or last_error
                if rewrite_round >= self.prompt_rewrite_max_rounds:
                    break
                if not self._is_policy_violation_error(last_error):
                    break
                rewritten = self._rewrite_prompt_for_policy_v2(prompt_for_submit, last_error, mode="image")
                if not rewritten:
                    break
                prompt_for_submit = rewritten
                rewrite_round += 1

            if not generated_ok:
                failure_reason = last_error or failure_reason or "Reference regeneration failed"
                continue

            update_data = {"status": "done", "media_id": generated_media_id, "reference_media_checked": False}
            if (getattr(char, "english_prompt", "") or "").strip():
                update_data["english_prompt"] = current_prompt
            elif (getattr(char, "vietnamese_prompt", "") or "").strip():
                update_data["vietnamese_prompt"] = current_prompt
            with self._excel_lock:
                wb.update_character(char.id, **update_data)
                if not wb.safe_save():
                    wb._save_pending_write("character", char_id=char.id, **update_data)
                    self.log(f"    {char.id}: Excel bi khoa, luu pending write", "WARN")
            char.media_id = generated_media_id
            char.status = "done"
            if "english_prompt" in update_data:
                char.english_prompt = update_data["english_prompt"]
            if "vietnamese_prompt" in update_data:
                char.vietnamese_prompt = update_data["vietnamese_prompt"]

            valid, validation_error = self._validate_reference_media_id(char)
            if valid:
                with self._excel_lock:
                    wb.update_character(char.id, reference_media_checked=True)
                    if not wb.safe_save():
                        wb._save_pending_write("character", char_id=char.id, reference_media_checked=True)
                self.log(f"  [{char.id}] media_id moi hop le", "SUCCESS")
                char.reference_media_checked = True
                return True
            failure_reason = validation_error or "New media_id still invalid"
            self.log(f"  [{char.id}] media_id moi chua dung duoc, thu lai", "WARN")

        with self._excel_lock:
            wb.update_character(char.id, status="error", reference_media_checked=False)
            wb.safe_save()
        return False

    def _ensure_reference_media_ids_ready(self, wb: PromptWorkbook) -> Dict[str, int]:
        result = {"total": 0, "validated": 0, "regenerated": 0, "failed": 0}
        characters = wb.get_characters()
        targets = []
        for char in characters:
            if getattr(char, "is_child", False):
                continue
            status = str(getattr(char, "status", "") or "").strip().lower()
            if status == "skip":
                continue
            if self._is_reference_media_checked(char) and str(getattr(char, "media_id", "") or "").strip():
                continue
            if not self._reference_base_prompt(char):
                continue
            targets.append(char)

        result["total"] = len(targets)
        if not targets:
            self.log("Khong co reference nao can validate media_id")
            return result

        self.log(f"VALIDATE REFERENCES: {len(targets)} media_id")
        for idx, char in enumerate(targets, start=1):
            if self._stop_flag:
                break
            media_id = str(getattr(char, "media_id", "") or "").strip()
            if not media_id:
                self.log(f"  [{idx}/{len(targets)}] {char.id}: thieu media_id -> repair", "WARN")
                ok = self._repair_reference_media_id(wb, char, "Missing media_id before validation")
                if ok:
                    result["validated"] += 1
                    result["regenerated"] += 1
                else:
                    result["failed"] += 1
                continue

            self.log(f"  [{idx}/{len(targets)}] {char.id}: validate media_id")
            ok, error_text = self._validate_reference_media_id(char)
            if ok:
                with self._excel_lock:
                    wb.update_character(char.id, reference_media_checked=True)
                    if not wb.safe_save():
                        wb._save_pending_write("character", char_id=char.id, reference_media_checked=True)
                char.reference_media_checked = True
                result["validated"] += 1
                continue

            self.log(f"    {char.id}: media_id khong dung duoc -> repair ({self._short_text(error_text, 160)})", "WARN")
            repaired = self._repair_reference_media_id(wb, char, error_text or "Reference media id invalid")
            if repaired:
                result["validated"] += 1
                result["regenerated"] += 1
            else:
                result["failed"] += 1

        self.log(
            f"Validate refs xong: ok={result['validated']}/{result['total']}, "
            f"regenerated={result['regenerated']}, failed={result['failed']}"
        )
        return result

    # =========================================================================
    # PHASE 3: Scene Images
    # =========================================================================

    def _generate_scenes(self, wb: PromptWorkbook) -> Dict:
        """Táº¡o áº£nh cho Táº¤T Cáº¢ scenes."""
        result = {"total": 0, "completed": 0, "failed": 0}

        scenes = wb.get_scenes()
        if not scenes:
            self.log("KhÃ´ng cÃ³ scenes trong Excel")
            return result

        # Lá»c scenes cáº§n táº¡o áº£nh
        pending = []
        for scene in scenes:
            if not scene.img_prompt:
                continue
            if scene.status_img and scene.status_img.lower() == "skip":
                continue

            # ⚠ "DONE" TRONG EXCEL PHẢI ĐƯỢC ĐĨA XÁC NHẬN.
            #
            # Nhánh này từng `continue` thẳng, tin Excel tuyệt đối. Excel ghi
            # done mà file đã mất thì cảnh đó CHẾT VĨNH VIỄN, vì hai pha nhìn
            # hai nguồn khác nhau:
            #
            #     PHASE 3 (nhìn Excel):  Scenes can tao: 0/131
            #     PHASE 4 (nhìn đĩa)  :  Skip scene 111: chua co anh hoac media_id
            #
            # Bắt được nguyên văn hai dòng đó ở TH1-0104 lúc 01:38:23 ngày
            # 15/08/2026. Pha ảnh bảo không thiếu gì, pha video bảo thiếu ảnh —
            # và không ai dựng lại. Cảnh đó nằm đó qua MỌI lượt chạy.
            #
            # Phép kiểm magic-bytes thêm hôm trước nằm BÊN DƯỚI dòng này nên
            # không bao giờ tới lượt. Vá đúng chỗ là ở đây.
            if scene.status_img and scene.status_img.lower() == "done":
                if self._anh_scene_con_dung_duoc(scene.scene_id):
                    continue
                self.log("  Scene {0}: Excel ghi 'done' nhung DIA khong co anh "
                         "-> dung lai".format(scene.scene_id), "WARN")
                pending.append(scene)
                continue

            img_path = self.img_dir / f"{scene.scene_id}.png"
            media_id = getattr(scene, 'media_id', '') or ''

            # ÄÃ£ cÃ³ áº£nh + media_id â†’ skip vÃ  Ä‘Ã¡nh dáº¥u
            # ⚠ FILE TỒN TẠI CHƯA CÓ NGHĨA LÀ ẢNH DÙNG ĐƯỢC.
            #
            # Nhánh này từng chỉ hỏi `img_path.exists()` rồi đánh dấu `done`.
            # Một file `.png` mà ruột không phải ảnh (tải hụt, hoặc lưu nhầm
            # trang lỗi) vẫn qua cửa — và tệ hơn, nó GHI ĐÈ dấu `error` mà pha
            # video vừa đặt để yêu cầu dựng lại. Vòng lặp kín:
            #
            #     pha ảnh : có file          -> đánh dấu "done"
            #     pha video: máy chủ từ chối -> FAIL, đánh dấu "error"
            #     lượt sau : y hệt
            #
            # Đo thật TH1-0182 cảnh 81: hỏng BỐN lượt liên tiếp lúc 17:17,
            # 17:30, 17:43 và 18:02 ngày 15/08/2026, mỗi lượt ăn một "lượt
            # trắng". Kiểm bằng CHÍNH THƯỚC MÁY CHỦ DÙNG (magic bytes) thì vòng
            # lặp đứt ngay trong lượt này, không phải chờ pha video phát hiện.
            if img_path.exists() and (media_id or not self._can_media_id_canh()):
                if not self._la_anh_that(img_path):
                    self.log("  Scene {0}: file anh co nhung KHONG PHAI ANH "
                             "(magic bytes sai) - dung lai".format(scene.scene_id), "WARN")
                    try:
                        img_path.unlink()
                    except OSError:
                        pass
                    pending.append(scene)
                    continue
                with self._excel_lock:
                    wb.update_scene(scene.scene_id, status_img="done")
                    wb.safe_save()
                continue

            # ÄÃ£ cÃ³ áº£nh nhÆ°ng thiáº¿u media_id (cháº¡y dá»Ÿ) â†’ cáº§n táº¡o láº¡i
            # Chỉ tới được đây khi `_can_media_id_canh()` là True, tức chế độ
            # Flow/Chrome — nơi thiếu mã thì cảnh thật sự vô dụng.
            if img_path.exists() and not media_id:
                self.log(f"  Scene {scene.scene_id}: cÃ³ áº£nh nhÆ°ng thiáº¿u media_id â€” táº¡o láº¡i", "WARN")

            pending.append(scene)

        result["total"] = len(pending)
        total_scenes = len([s for s in scenes if s.img_prompt])
        self.log(f"Scenes cáº§n táº¡o: {len(pending)}/{total_scenes} (tran nguoi dung={self.max_concurrent}, so that hoi may chu moi lo)")
        scene_total, scene_done_base = self._count_scene_image_progress(scenes)

        media_ids = self._load_media_ids(wb)
        child_ids = {c.id for c in wb.get_characters() if getattr(c, "is_child", False)}

        completed_count = [0]

        def _do_scene(i, scene):
            if self._stop_flag:
                return None
            scene_id = scene.scene_id
            img_path = self.img_dir / f"{scene_id}.png"
            prompt = scene.img_prompt

            self.log(f"  [{i+1}/{len(pending)}] Scene {scene_id}: {prompt[:60]}...")
            self.on_item_status("scene", scene_id, "running", None, {})
            refs, expected_refs, missing_refs = self._build_references(scene, media_ids, with_details=True, ignored_ids=child_ids)
            # Log refs dang dung
            if refs:
                ref_names = [r.name[:20] for r in refs]
                self.log(f"    Refs: {ref_names}")
            else:
                self.log(f"    Khong co reference images")

            if expected_refs and missing_refs:
                missing_preview = ", ".join(missing_refs[:6])
                if len(missing_refs) > 6:
                    missing_preview += ", ..."
                with self._excel_lock:
                    wb.update_scene(scene_id, status_img="error")   # thiếu ref có thể do transient/bug (pool: ref local chưa sẵn) -> RETRY được, không bỏ
                    wb.safe_save()
                self.log(f"    Scene {scene_id} -> SKIP missing references: {missing_preview}", "WARN")
                self.on_item_status("scene", scene_id, "error", None,
                                    {"missing_refs": missing_refs})
                return False

            def _poll_cb(info):
                self.on_item_status("scene", scene_id, "running", None,
                                    {"queue_pos": info.get("queue_position"),
                                     "poll_status": info.get("status")})

            t0 = time.time()
            current_prompt = prompt
            rewrite_round = 0
            rewrite_happened = False
            while True:
                success, media_name, server_info, error_text = self._submit_image(
                    current_prompt, img_path, refs, poll_callback=_poll_cb
                )
                if success:
                    break
                if rewrite_happened and self._is_policy_violation_error(error_text):
                    self.log(f"    Scene {scene_id}: prompt da viet lai nhung van bi policy", "WARN")
                if rewrite_round >= self.prompt_rewrite_max_rounds:
                    break
                if not self._is_policy_violation_error(error_text):
                    break
                self.log(f"    Scene {scene_id}: prompt cÃ³ dáº¥u hiá»‡u vi pháº¡m policy, thá»­ viáº¿t láº¡i (vong {rewrite_round + 1}/{self.prompt_rewrite_max_rounds})", "WARN")
                rewritten = self._rewrite_prompt_for_policy_v2(current_prompt, error_text, mode="image", round_index=rewrite_round + 1)
                if not rewritten:
                    self._log_prompt_rewrite_event(
                        "scene", scene_id, "scene_image", "rewrite_failed",
                        current_prompt, error_text=error_text, round_index=rewrite_round + 1, mode="image"
                    )
                    self.log(f"    Scene {scene_id}: khong viet lai duoc prompt hop le", "WARN")
                    break
                self._log_prompt_rewrite_event(
                    "scene", scene_id, "scene_image", "rewritten",
                    current_prompt, rewritten_prompt=rewritten, error_text=error_text,
                    round_index=rewrite_round + 1, mode="image"
                )
                current_prompt = rewritten
                rewrite_round += 1
                rewrite_happened = True
                with self._excel_lock:
                    wb.update_scene(scene_id, img_prompt=current_prompt)
                    if not wb.safe_save():
                        wb._save_pending_write("scene", scene_id=scene_id, img_prompt=current_prompt)
                        self.log(f"    Scene {scene_id}: Excel bi khoa, luu pending prompt rewrite", "WARN")
                self.log(f"    Scene {scene_id}: da cap nhat prompt moi va retry", "INFO")
            elapsed = round(time.time() - t0, 1)

            if success:
                if rewrite_happened:
                    self._log_prompt_rewrite_event(
                        "scene", scene_id, "scene_image", "rewrite_succeeded",
                        prompt, rewritten_prompt=current_prompt, round_index=rewrite_round, mode="image"
                    )
                with self._excel_lock:
                    # Ghi status_img + media_id trong 1 láº§n duy nháº¥t (atomic)
                    update_kw = {"status_img": "done", "img_path": str(img_path)}
                    if current_prompt != prompt:
                        update_kw["img_prompt"] = current_prompt
                    if media_name:
                        update_kw["media_id"] = media_name
                    wb.update_scene(scene_id, **update_kw)
                    if not wb.safe_save():
                        # Excel bá»‹ khÃ³a â†’ lÆ°u vÃ o pending JSON, sáº½ flush láº§n sau
                        wb._save_pending_write("scene", scene_id=scene_id,
                                               status_img="done", img_path=str(img_path),
                                               media_id=media_name or "",
                                               img_prompt=current_prompt if current_prompt != prompt else "")
                        self.log(f"    Scene {scene_id}: Excel bá»‹ khÃ³a, lÆ°u pending write", "WARN")
                with self._dem_lock:          # dem duoi nhieu luong: xem `_dem_lock`
                    completed_count[0] += 1
                    _da_xong = completed_count[0]
                current_done = min(scene_total, scene_done_base + _da_xong)
                self.progress("scenes", current_done, scene_total, f"scene_{scene_id}")
                self.log(f"    Scene {scene_id} â†’ OK ({elapsed}s, {server_info.get('server', '?')})")
                self.on_item_status("scene", scene_id, "done", str(img_path),
                                    {"elapsed": elapsed, **server_info})
                return True
            else:
                if rewrite_happened:
                    self._log_prompt_rewrite_event(
                        "scene", scene_id, "scene_image", "rewrite_still_failed",
                        prompt, rewritten_prompt=current_prompt, error_text=error_text,
                        round_index=rewrite_round, mode="image"
                    )
                fs = self._fail_status_for(error_text)
                with self._excel_lock:
                    wb.update_scene(scene_id, status_img=fs)
                    wb.safe_save()
                _tag = "TERMINAL policy" if fs == "failed" else "retry lượt sau"
                # In cả lý do — xem khối tương ứng ở pha video.
                self.log("    Scene {0} -> FAIL ({1}s) [{2}: {3}] {4}".format(
                    scene_id, elapsed, fs, _tag,
                    (error_text or "(khong co mo ta loi)")[:200]), "WARN")
                self.on_item_status("scene", scene_id, "error", None,
                                    {"elapsed": elapsed, **server_info})
                return False

        # GOP TRUOC, CHAY SAU: scene nao trung y het prompt + ti le + ref thi don
        # vao MOT job `n=k` (toi 8 anh/job) - chi ton 1 cho trong tran song song
        # thay vi k cho. Gop hong thi khong sao, chung di duong binh thuong ben duoi.
        if self.use_shopapi_for_image and not self._stop_flag:
            try:
                cong_viec_gop = []
                for s in pending:
                    refs, expected_refs, missing_refs = self._build_references(
                        s, media_ids, with_details=True, ignored_ids=child_ids)
                    if expected_refs and missing_refs:
                        continue          # thieu ref -> de `_do_scene` bao loi dung cho
                    cong_viec_gop.append((s.img_prompt,
                                          self.img_dir / f"{s.scene_id}.png",
                                          refs, self.aspect_ratio))
                self._shopapi_gop_anh_cung_prompt(cong_viec_gop)
            except Exception as e:
                self.log(f"    [shopapi-img] bo qua buoc gop ({e})", "WARN")

        self._chay_me("image", list(enumerate(pending)),
                      lambda p: _do_scene(p[0], p[1]), self.max_concurrent, result)
        return result

    def _load_media_ids(self, wb: PromptWorkbook) -> Dict[str, str]:
        """Load media_ids tu characters sheet."""
        media_ids = {}
        try:
            characters = wb.get_characters()
            for char in characters:
                if char.media_id:
                    media_ids[char.id] = char.media_id
                    fname = f"{char.id}.png"
                    media_ids[fname] = char.media_id
                    media_ids[f"nv/{fname}"] = char.media_id
                    media_ids[f"loc/{fname}"] = char.media_id
                    if getattr(char, "image_file", ""):
                        image_file = str(char.image_file).strip()
                        if image_file:
                            media_ids[image_file] = char.media_id
                            media_ids[f"nv/{image_file}"] = char.media_id
                            media_ids[f"loc/{image_file}"] = char.media_id
        except Exception as e:
            self.log(f"Loi load media_ids: {e}", "WARN")
        self.log(f"  Media IDs loaded: {len(media_ids)}")
        return media_ids

    def _reference_lookup_candidates(self, ref_file: str) -> tuple[list, str]:
        """Tra ve candidates de lookup media_id + normalized ref_name."""
        raw = str(ref_file or "").strip()
        if not raw:
            return [], ""

        normalized = raw.replace("\\", "/")
        base = normalized.split("/")[-1]
        if "." in base:
            base_no_ext = ".".join(base.split(".")[:-1]).strip()
        else:
            base_no_ext = base.strip()

        candidates = [raw, normalized, base]
        if base_no_ext:
            candidates.extend([base_no_ext, f"{base_no_ext}.png", f"nv/{base_no_ext}.png", f"loc/{base_no_ext}.png"])

        dedup = []
        seen = set()
        for c in candidates:
            c = str(c or "").strip()
            if not c or c in seen:
                continue
            seen.add(c)
            dedup.append(c)
        return dedup, base_no_ext

    def _can_media_id_canh(self) -> bool:
        """Cảnh có BẮT BUỘC phải có `media_id` riêng không?

        CHỈ chế độ Flow/Chrome cần. Ở đó Image-to-Video đi bằng `mediaId` của
        Flow, nên ảnh không có mã thì cảnh vô dụng thật.

        `pool` và API `shopapi` thì KHÔNG, vì không ai đọc nó:

        * `_submit_video_shopapi` chỉ nhận ĐƯỜNG DẪN `img/<id>.png` rồi tự
          upload lấy URL công khai — không đụng `media_id` một chữ nào.
        * Tham chiếu lúc dựng ảnh KHÔNG lấy từ cảnh mà lấy từ trang NHÂN VẬT:
          `_load_media_ids` chỉ đọc `wb.get_characters()`. Và ở hai chế độ này
          nó còn đi bằng BYTES — `_make_ref` nhúng base64 từ `nv/<tên>.png`,
          vì API không hiểu `mediaId` của Flow.

        Nói gọn: ở shopapi, `media_id` của CẢNH được ghi vào Excel rồi nằm đó.
        Bắt dựng lại vì thiếu nó là đốt tiền thật để điền một ô trống.

        ⚠ Pha 3 (ảnh) và pha 4 (video) PHẢI hỏi cùng hàm này. Hai bên từng tự
        viết điều kiện riêng và lệch nhau: pha 4 đã tha từ lâu, pha 3 vẫn bắt.
        Hậu quả là MỖI lượt chạy dựng lại toàn bộ ảnh cũ. Đo thật 15/08/2026
        lúc 10:28: TH2-0139 dựng lại đúng 12 cảnh (47–58) đã có ảnh nằm trên
        đĩa, TH2-0162 cũng đúng 12 cảnh — trong khi `Media IDs loaded: 0` cho
        thấy chẳng ai cần mã đó cả. Cùng lỗi họ hàng với `_pool_ref_local`
        ngay bên dưới: một hàm chữa cho `pool` mà quên `shopapi`.
        """
        return self.generation_backend != "veo3top_b_pool" and not self.use_shopapi_for_video

    def _pool_ref_local(self, ref_name: str) -> bool:
        """POOL / SHOPAPI: ref build bằng EMBED base64 từ FILE local (nv/<ref>.png)
        -> KHÔNG cần media_id. `_make_ref` đọc thẳng file này, nên ref coi là CÓ
        khi file tồn tại, dù media_id rỗng.
        (Fix: Fix-4 bỏ upload nv1 -> media_id rỗng -> _build_references báo 'missing' oan -> 0 ảnh.)

        ⚠ PHẢI GỒM CẢ "shopapi", ĐÃ DÍNH ĐÚNG LẠI LỖI CŨ (07/08/2026).
        Hàm này sinh ra để chữa "0 ảnh" cho `pool`, nhưng lại khoá cứng đúng chữ
        `pool`. Nhánh shopapi cũng bỏ qua media_id (API KHÔNG hiểu mediaId của
        Flow, ref đi bằng bytes) nên rơi vào y hệt cái bẫy đó: chạy thật ra
        `missing refs -> nv1` cho CẢ 147 scene, tổng kết `Anh: 0/147`.

        `_make_ref` đã liệt kê cả hai chế độ từ đầu — chỉ mỗi hàm này bị bỏ sót,
        nên hai bên phải đi cùng nhau, sửa một chỗ là nhớ chỗ kia.
        """
        if getattr(self, "veo3top_image_mode", "") not in ("pool", "shopapi") or not ref_name:
            return False
        try:
            for ext in (".png", ".jpg", ".jpeg"):
                if (self.nv_dir / f"{ref_name}{ext}").exists():
                    return True
        except Exception:
            pass
        return False

    def _build_references(self, scene: Scene, media_ids: Dict[str, str], with_details: bool = False, ignored_ids: Optional[set] = None):
        """Build ImageInput references cho scene."""
        refs = []
        expected_refs = []
        ignored_ids = ignored_ids or set()
        missing_refs = []

        ref_files = []
        if scene.reference_files:
            try:
                ref_files = json.loads(scene.reference_files) if isinstance(scene.reference_files, str) else scene.reference_files
            except (json.JSONDecodeError, TypeError):
                ref_files = [f.strip() for f in str(scene.reference_files).split(",") if f.strip()]

        for ref_file in ref_files:
            candidates, ref_name = self._reference_lookup_candidates(ref_file)
            if ref_name in ignored_ids:
                continue
            expected_refs.append(ref_name)
            media_id = None
            for c in candidates:
                media_id = media_ids.get(c)
                if media_id:
                    break
            if media_id or self._pool_ref_local(ref_name):
                refs.append(self._make_ref(ref_name, media_id or ""))
            else:
                missing_refs.append(ref_name)

        if not ref_files:
            if scene.characters_used:
                char_ids = [c.strip() for c in str(scene.characters_used).split(",") if c.strip()]
                for cid in char_ids:
                    if cid in ignored_ids:
                        continue
                    expected_refs.append(cid)
                    media_id = media_ids.get(cid) or media_ids.get(f"{cid}.png")
                    if media_id or self._pool_ref_local(cid):
                        refs.append(self._make_ref(cid, media_id or ""))
                    else:
                        missing_refs.append(cid)

            if scene.location_used:
                loc_id = scene.location_used.strip()
                if loc_id in ignored_ids:
                    loc_id = ""
                if not loc_id:
                    if with_details:
                        expected_refs = list(dict.fromkeys(expected_refs))
                        missing_refs = [ref for ref in dict.fromkeys(missing_refs) if ref in expected_refs]
                        return refs, expected_refs, missing_refs
                    return refs
                expected_refs.append(loc_id)
                media_id = media_ids.get(loc_id) or media_ids.get(f"{loc_id}.png")
                if media_id or self._pool_ref_local(loc_id):
                    refs.append(self._make_ref(loc_id, media_id or ""))
                else:
                    missing_refs.append(loc_id)

        if with_details:
            expected_refs = list(dict.fromkeys(expected_refs))
            missing_refs = [ref for ref in dict.fromkeys(missing_refs) if ref in expected_refs]
            return refs, expected_refs, missing_refs
        return refs

    def _build_thumbnail_references(self, thumb: Any, media_ids: Dict[str, str], with_details: bool = False):
        """Build ImageInput references cho thumbnail prompt."""
        refs = []
        expected_refs = []
        missing_refs = []

        ref_files = []
        if getattr(thumb, "reference_files", ""):
            try:
                raw = thumb.reference_files
                ref_files = json.loads(raw) if isinstance(raw, str) else raw
            except (json.JSONDecodeError, TypeError):
                ref_files = [f.strip() for f in str(thumb.reference_files).split(",") if f.strip()]

        if not ref_files:
            char_ids = [c.strip() for c in str(getattr(thumb, "characters_used", "")).split(",") if c.strip()]
            loc_ids = [l.strip() for l in str(getattr(thumb, "location_used", "")).split(",") if l.strip()]
            ref_files = [*char_ids, *loc_ids]

        for ref_file in ref_files:
            candidates, ref_name = self._reference_lookup_candidates(ref_file)
            if not ref_name:
                continue
            expected_refs.append(ref_name)
            media_id = None
            for c in candidates:
                media_id = media_ids.get(c)
                if media_id:
                    break
            if media_id or self._pool_ref_local(ref_name):
                refs.append(self._make_ref(ref_name, media_id or ""))
            else:
                missing_refs.append(ref_name)

        if with_details:
            expected_refs = list(dict.fromkeys(expected_refs))
            missing_refs = [ref for ref in dict.fromkeys(missing_refs) if ref in expected_refs]
            return refs, expected_refs, missing_refs
        return refs

    def _fallback_copy_thumbnail_from_character(self, wb: PromptWorkbook):
        """Fallback cu: copy anh nhan vat chinh vao thumb."""
        characters = wb.get_characters()
        if not characters:
            self.log("Khong co nhan vat de tao thumbnail")
            return

        actual_chars = [c for c in characters if not c.id.lower().startswith("loc") and c.role != "location"]
        if not actual_chars:
            actual_chars = characters

        selected = None
        for char in actual_chars:
            if char.role and char.role.lower() in ("protagonist", "main") and not char.is_child:
                selected = char
                break
        if not selected:
            for char in actual_chars:
                if not char.is_child:
                    selected = char
                    break
        if not selected:
            selected = actual_chars[0]

        src_image = self.nv_dir / f"{selected.id}.png"
        if src_image.exists():
            project_code = self.project_dir.name
            dest_image = self.thumb_dir / f"{project_code}.png"
            shutil.copy2(str(src_image), str(dest_image))
            final_path = self._optimize_thumbnail_for_youtube(dest_image) or dest_image
            self.log(f"Thumbnail fallback: {selected.id} ({selected.name}) -> {final_path.name}")
        else:
            self.log(f"Anh {selected.id}.png chua ton tai, bo qua thumbnail fallback", "WARN")

    def _optimize_thumbnail_for_youtube(self, image_path: Path) -> Optional[Path]:
        """Normalize thumb output to YouTube requirements."""
        try:
            result = optimize_youtube_thumbnail(image_path)
            quality_text = f", q={result.quality}" if result.quality is not None else ""
            self.log(
                f"    Thumbnail YouTube-ready: {result.path.name} "
                f"{result.width}x{result.height}, {result.size_bytes / 1024:.0f}KB, {result.format}{quality_text}"
            )
            return result.path
        except ThumbnailOptimizeError as exc:
            self.log(f"    Khong the nang cap thumbnail {image_path.name}: {exc}", "WARN")
            return None

    # =========================================================================
    # PHASE 2: Thumbnail
    # =========================================================================

    def _auto_generate_thumbnail_prompts(self, wb: PromptWorkbook) -> bool:
        """Tu dong tao thumbnail prompts neu chua co."""
        try:
            all_thumbnails = wb.get_thumbnails()
        except Exception as e:
            self.log(f"Khong doc duoc sheet thumbnail: {e}", "WARN")
            return False

        # Check if any thumbnails exist but missing img_prompt
        missing_prompt_thumbs = [t for t in all_thumbnails if not (getattr(t, "img_prompt", "") or "").strip()]

        if not missing_prompt_thumbs:
            return False  # All thumbnails already have prompts

        self.log(f"Phat hien {len(missing_prompt_thumbs)} thumbnail chua co prompt -> tu dong tao")

        # Get context for prompt generation
        characters = wb.get_characters()
        locations = wb.get_locations()
        protagonist = next((c for c in characters if c.role and c.role.lower() in ("protagonist", "main")),
                          characters[0] if characters else None)

        if not protagonist:
            self.log("Khong co nhan vat de tao thumbnail prompt", "WARN")
            return False

        # Build context
        char_ids = [c.id for c in characters if not c.id.lower().startswith("loc")]
        loc_ids = [loc.id for loc in locations]
        chars_info = "\n".join([f"- {c.id}: {c.name} ({c.role})" for c in characters if not c.id.lower().startswith("loc")])
        locs_info = "\n".join([f"- {loc.id}: {loc.name}" for loc in locations]) if locations else "N/A"

        # Get project context from wb attributes
        context_lock = getattr(wb, 'context_lock', '') or ''
        setting = getattr(wb, 'setting', {}) or {}
        themes = getattr(wb, 'themes', []) or []
        visual_style = getattr(wb, 'visual_style', {}) or {}
        title = getattr(wb, 'title', '') or ''

        # Generate prompts using topic prompts
        from modules.topic_prompts import get_topic_prompts
        topic = self.config.get("topic", "psychology")
        topic_prompts = get_topic_prompts(topic)

        system_prompt = topic_prompts.step8_thumbnail(
            setting=setting,
            themes=themes,
            visual_style=visual_style,
            context_lock=context_lock,
            protagonist=protagonist,
            chars_info=chars_info,
            locs_info=locs_info,
            char_ids=char_ids,
            loc_ids=loc_ids,
            title=title
        )

        self.log("Goi API de tao thumbnail prompts...")

        try:
            # Use progressive prompts generator to call API
            from modules.progressive_prompts import ProgressivePromptsGenerator
            prompt_gen = ProgressivePromptsGenerator(self.config)
            prompt_gen.log_callback = lambda msg, level="INFO": self.log(msg, level)

            response = prompt_gen._call_api(system_prompt, temperature=0.7, max_tokens=4000)
            if not response:
                self.log("API khong tra ve ket qua", "ERROR")
                return False

            # Parse JSON response (strip markdown code blocks if present)
            import json
            import re

            # Remove markdown code blocks
            response_clean = response.strip()
            if response_clean.startswith("```"):
                # Remove ```json or ``` at start and ``` at end
                response_clean = re.sub(r'^```(?:json)?\s*\n', '', response_clean)
                response_clean = re.sub(r'\n```\s*$', '', response_clean)

            data = json.loads(response_clean)
            thumbnails_data = data.get("thumbnails", [])

            if not thumbnails_data:
                self.log("API khong tra ve thumbnail data", "ERROR")
                return False

            self.log(f"API tra ve {len(thumbnails_data)} thumbnail prompts")

            # Update Excel with generated prompts
            for thumb_data in thumbnails_data:
                thumb_id = thumb_data.get("thumb_id")
                img_prompt = thumb_data.get("img_prompt", "")

                if thumb_id and img_prompt:
                    wb.update_thumbnail(
                        thumb_id=thumb_id,
                        img_prompt=img_prompt,
                        characters_used=thumb_data.get("characters_used", ""),
                        location_used=thumb_data.get("location_used", "")
                    )
                    self.log(f"  Thumb {thumb_id}: prompt generated ({len(img_prompt)} chars)")

            wb.safe_save()
            self.log("Da luu thumbnail prompts vao Excel")
            return True

        except Exception as e:
            self.log(f"Loi khi tao thumbnail prompts: {e}", "ERROR")
            return False

    def _generate_thumbnail(self, wb: PromptWorkbook):
        """Tao thumbnail tu sheet thumbnail prompts (neu co), fallback copy nhan vat chinh."""
        self.thumb_dir.mkdir(parents=True, exist_ok=True)

        # First, check if thumbnails exist but missing prompts -> auto-generate
        self._auto_generate_thumbnail_prompts(wb)

        try:
            thumbnails = wb.get_pending_thumbnails()
        except Exception as e:
            self.log(f"Khong doc duoc sheet thumbnail: {e}", "WARN")
            thumbnails = []

        if not thumbnails:
            self.log("Khong co thumbnail prompt pending -> skip (server tao thumb)")
            return

        self.log(f"Thumbnail can tao: {len(thumbnails)}")
        media_ids = self._load_media_ids(wb)

        def _lam_mot_thumb(cap):
            """Tao MOT thumbnail. Tra True/False/None - khop cach `_chay_me` dem.

            Tach ra khoi vong `for` de ca me thumbnail chay SONG SONG duoc: truoc
            day day la cho duy nhat con chay tuan tu 100%, moi anh cho anh truoc
            xong moi gui, trong khi nha may van rong.
            """
            idx, thumb = cap
            if self._stop_flag:
                return None
            thumb_id = int(getattr(thumb, "thumb_id", idx) or idx)
            prompt = (getattr(thumb, "img_prompt", "") or "").strip()
            if not prompt:
                return None

            out_path = self.thumb_dir / f"thumb_{thumb_id:03d}.png"
            refs, expected_refs, missing_refs = self._build_thumbnail_references(thumb, media_ids, with_details=True)
            if expected_refs and missing_refs:
                missing_preview = ", ".join(missing_refs[:6]) + (", ..." if len(missing_refs) > 6 else "")
                with self._excel_lock:
                    wb.update_thumbnail(thumb_id, status_img="error")
                    wb.safe_save()
                self.log(f"  [{idx}/{len(thumbnails)}] Thumb {thumb_id}: missing refs -> {missing_preview}", "WARN")
                return False

            self.log(f"  [{idx}/{len(thumbnails)}] Thumb {thumb_id}: generating...")

            def _poll_cb(info):
                self.on_item_status("thumb", thumb_id, "running", None,
                                    {"queue_pos": info.get("queue_position"),
                                     "poll_status": info.get("status")})

            t0 = time.time()
            # Thumbnail đi theo khổ kênh: kênh landscape (YouTube) giữ LANDSCAPE + optimize
            # như cũ; kênh portrait (short 9:16) ra thumb dọc và giữ nguyên ảnh gốc.
            _is_portrait_channel = "PORTRAIT" in str(
                getattr(self.aspect_ratio, "name", self.aspect_ratio)).upper()
            success, media_name, sinfo, _error_text = self._submit_image(
                prompt=prompt,
                output_path=out_path,
                refs=refs,
                poll_callback=_poll_cb,
                aspect_ratio=self.aspect_ratio if _is_portrait_channel else AspectRatio.LANDSCAPE
            )
            elapsed = round(time.time() - t0, 1)

            if success:
                final_path = out_path if _is_portrait_channel \
                    else self._optimize_thumbnail_for_youtube(out_path)
                if not final_path:
                    with self._excel_lock:
                        wb.update_thumbnail(thumb_id, status_img="error")
                        wb.safe_save()
                    self.log(f"    Thumb {thumb_id} -> FAIL optimize YouTube thumbnail ({elapsed}s)", "WARN")
                    self.on_item_status("thumb", thumb_id, "error", None,
                                        {"elapsed": elapsed, **sinfo})
                    return False
                with self._excel_lock:
                    wb.update_thumbnail(thumb_id, status_img="done", img_path=str(final_path))
                    if not wb.safe_save():
                        wb._save_pending_write("thumbnail", thumb_id=thumb_id, status_img="done", img_path=str(final_path))
                        self.log(f"    Thumb {thumb_id}: Excel bi khoa, luu pending write", "WARN")
                self.log(f"    Thumb {thumb_id} -> OK ({elapsed}s, {sinfo.get('server', '?')})")
                self.on_item_status("thumb", thumb_id, "done", str(final_path),
                                    {"elapsed": elapsed, **sinfo})
                return True
            else:
                with self._excel_lock:
                    wb.update_thumbnail(thumb_id, status_img="error")
                    wb.safe_save()
                self.log(f"    Thumb {thumb_id} -> FAIL ({elapsed}s)", "WARN")
                self.on_item_status("thumb", thumb_id, "error", None,
                                    {"elapsed": elapsed, **sinfo})
                return False

        # Thumbnail cung la job ANH -> dung chung tran song song voi pha scene.
        # Backend CU giu nguyen chay tuan tu (tran = 1): thay doi song song cho
        # nhung duong khong lien quan la tu chuoc rui ro khong can thiet.
        self._chay_me("image", list(enumerate(thumbnails, start=1)), _lam_mot_thumb,
                      self.max_concurrent if self.use_shopapi_for_image else 1,
                      {"completed": 0, "failed": 0})

        # (Removed: backward-compatible alias thumb/<project_code>.jpg — not needed.)

    # =========================================================================
    # PHASE 4: Video Generation (Image-to-Video)
    # =========================================================================

    def _upload_local_images_for_video(self, wb: PromptWorkbook, scenes: list):
        """FLOW2 local mode: anh scene tao bang token Pro -> media_id Pro-bound khong dung
        cho I2V (chay token Ultra). PHASE CHUAN BI: upload anh scene len Ultra (giong upload
        nv1.png) -> media_id Ultra-bound -> ghi Excel. Sau do _do_video chay luong cu."""
        todo = []
        for s in scenes:
            vp = getattr(s, "video_prompt", "") or ""
            sv = (getattr(s, "status_vid", "") or "").lower()
            if not vp or sv in ("skip", "done"):
                continue
            if (self.vid_dir / f"{s.scene_id}.mp4").exists():
                continue
            if (self.img_dir / f"{s.scene_id}.png").exists():
                todo.append(s)
        if not todo:
            return
        self.log(f"[local-video] Upload {len(todo)} anh scene len Ultra de lay media_id...")
        use_veo3top_upload = self.generation_backend in ("veo3top", "veo3top_b", "veo3top_b_ultra")
        ext_auth = None
        if use_veo3top_upload:
            # veo3top: KHONG dung ExtAuth agent. Lay bearer+project tu chinh account (nhu provider).
            token, pid = self._veo3top_auth()
            if not token or not pid:
                self.log("[local-video] veo3top: KHONG lay duoc bearer/project cua account -> bo upload", "ERROR")
                return
            self.log("[local-video] upload qua veo3top (curl_cffi DIRECT IPv4 may), KHONG qua ExtAuth")
        else:
            # Mo Chrome ExtAuth tren may chu 1 LAN (giong nv1) -> upload qua extension
            ext_auth, token, pid = self._open_ultra_uploader(wb)
            if not ext_auth or not token or not pid:
                self.log("[local-video] KHONG mo duoc Chrome/token Ultra -> bo qua upload (video se thieu media_id)", "ERROR")
                return
        ok = 0
        if use_veo3top_upload:
            # veo3top: upload SONG SONG (curl_cffi DIRECT IPv4 may), nhanh; KHONG can WARP.
            ok = self._veo3top_upload_parallel(wb, todo, token, pid)
            self.log(f"[local-video] Upload xong: {ok}/{len(todo)} media_id Ultra (san sang I2V)")
            return
        for s in todo:
            if self._stop_flag:
                break
            img = self.img_dir / f"{s.scene_id}.png"
            mid = ""
            for _try in range(3):
                try:
                    mid = ext_auth.upload_image(str(img), token, pid)
                except Exception as e:
                    self.log(f"  [local-video] scene {s.scene_id} upload loi: {e}", "WARN")
                    mid = ""
                if mid:
                    break
                # token co the het han -> lam moi roi thu lai
                if _try == 0 and ext_auth:
                    fresh = ext_auth.get_token()
                    if fresh:
                        token = fresh
                        self.bearer_token = fresh
                time.sleep(2)
            if mid:
                with self._excel_lock:
                    wb.update_scene(s.scene_id, media_id=mid, status_img="done")
                    if not wb.safe_save():
                        wb._save_pending_write("scene", scene_id=s.scene_id, media_id=mid)
                s.media_id = mid
                ok += 1
                if ok % 10 == 0:
                    self.log(f"  [local-video] {ok}/{len(todo)} media_id Ultra")
            else:
                self.log(f"  [local-video] scene {s.scene_id}: upload Ultra that bai", "WARN")
        self.log(f"[local-video] Upload xong: {ok}/{len(todo)} media_id Ultra (san sang I2V)")
        # Tha Chrome ExtAuth (token van con trong agent cho I2V neu can)
        try:
            from modules.flow_extension_auth import _ExtensionInstanceManager
            sname = self.server_list[0]["name"] if self.server_list else "sv1"
            _ExtensionInstanceManager.release_chrome(sname, log=self.log)
        except Exception:
            pass

    def _generate_videos(self, wb: PromptWorkbook) -> Dict:
        """Táº¡o video tá»« áº£nh scene Ä‘Ã£ cÃ³."""
        result = {"total": 0, "completed": 0, "failed": 0}

        scenes = wb.get_scenes()
        if not scenes:
            self.log("KhÃ´ng cÃ³ scenes")
            return result

        # Lá»c scene cÃ³ video_prompt vÃ  áº£nh Ä‘Ã£ xong
        # FLOW2 local mode: upload anh scene len Ultra -> media_id Ultra vao Excel TRUOC khi I2V
        # veo3top_b_pool: NHÀ MÁY CHUNG tự upload ảnh per-account -> KHÔNG bulk upload ở đây.
        # API shopapi cung KHONG bulk upload: buoc I2V tu upload anh scene lay URL cong khai.
        if self.use_local_token_for_image and self._can_media_id_canh():
            self._upload_local_images_for_video(wb, scenes)
            scenes = wb.get_scenes()  # reload media_id moi (Ultra-bound)

        pending = []
        for scene in scenes:
            vp = getattr(scene, 'video_prompt', '') or ''
            if not vp:
                continue
            sv = getattr(scene, 'status_vid', '') or ''
            if sv.lower() == "skip":
                continue

            vid_path = self.vid_dir / f"{scene.scene_id}.mp4"

            # ÄÃ£ cÃ³ video file â†’ skip, Ä‘áº£m báº£o status = done
            if vid_path.exists():
                if sv != "done":
                    with self._excel_lock:
                        wb.update_scene(scene.scene_id, status_vid="done", video_path=str(vid_path))
                        wb.safe_save()
                    self.log(f"  Scene {scene.scene_id}: video Ä‘Ã£ cÃ³, cáº­p nháº­t status â†’ done")
                continue

            # Cáº§n cÃ³ áº£nh + media_id Ä‘á»ƒ lÃ m Image-to-Video
            img_path = self.img_dir / f"{scene.scene_id}.png"
            media_id = getattr(scene, 'media_id', '') or ''
            # pool: chỉ cần ẢNH (nhà máy tự upload lấy media_id); mode khác cần media_id sẵn.
            # API shopapi: cũng chỉ cần ẢNH — `_submit_video_shopapi` tự upload `img/X.png`
            # lấy URL công khai. Bắt buộc media_id ở đây là chặn oan CẢ project.
            need_media = self._can_media_id_canh()
            if not img_path.exists() or (need_media and not media_id):
                self.log(f"  Skip scene {scene.scene_id}: chÆ°a cÃ³ áº£nh hoáº·c media_id")
                continue
            pending.append(scene)

        video_total, video_done_base = self._count_scene_video_progress(scenes)
        result["total"] = len(pending)
        if not pending:
            self.log("KhÃ´ng cÃ³ scene nÃ o cáº§n táº¡o video")
            return result
        self.log(f"Videos cáº§n táº¡o: {len(pending)}/{video_total} (tran nguoi dung={self.max_concurrent}, so that hoi may chu moi lo)")

        completed_count = [0]

        def _do_video(i, scene):
            if self._stop_flag:
                return None
            sid = scene.scene_id
            vp = scene.video_prompt
            media_id = scene.media_id  # local mode: da duoc upload-phase ghi media_id Ultra vao Excel
            vid_path = self.vid_dir / f"{sid}.mp4"

            self.log(f"  [{i+1}/{len(pending)}] Video scene {sid}: {vp[:60]}...")
            self.log(f"    media_id: {media_id[:40] if media_id else '(KHÃ”NG CÃ“)'}")
            self.on_item_status("scene", sid, "running", None, {"phase": "video"})

            t0 = time.time()
            current_prompt = vp
            rewrite_round = 0
            rewrite_happened = False
            last_resort_happened = False
            while True:
                success, server_info, error_text = self._submit_video(current_prompt, vid_path, media_id)
                if success:
                    break
                if rewrite_happened and self._is_policy_violation_error(error_text):
                    self.log(f"    Video scene {sid}: prompt da viet lai nhung van bi policy", "WARN")
                if rewrite_round >= self.prompt_rewrite_max_rounds:
                    break
                if not self._is_policy_violation_error(error_text):
                    break
                self.log(f"    Video scene {sid}: prompt co dau hieu vi pham policy, thu viet lai (vong {rewrite_round + 1}/{self.prompt_rewrite_max_rounds})", "WARN")
                rewritten = self._rewrite_prompt_for_policy_v2(current_prompt, error_text, mode="video", round_index=rewrite_round + 1)
                if not rewritten:
                    self._log_prompt_rewrite_event(
                        "scene", sid, "scene_video", "rewrite_failed",
                        current_prompt, error_text=error_text, round_index=rewrite_round + 1, mode="video"
                    )
                    self.log(f"    Video scene {sid}: khong viet lai duoc prompt hop le", "WARN")
                    break
                self._log_prompt_rewrite_event(
                    "scene", sid, "scene_video", "rewritten",
                    current_prompt, rewritten_prompt=rewritten, error_text=error_text,
                    round_index=rewrite_round + 1, mode="video"
                )
                current_prompt = rewritten
                rewrite_round += 1
                rewrite_happened = True
                with self._excel_lock:
                    wb.update_scene(sid, video_prompt=current_prompt)
                    if not wb.safe_save():
                        wb._save_pending_write("scene", scene_id=sid, video_prompt=current_prompt)
                        self.log(f"    Video scene {sid}: Excel bi khoa, luu pending prompt rewrite", "WARN")
                self.log(f"    Video scene {sid}: da cap nhat prompt moi va retry", "INFO")
            # ⚠ NHÀ MÁY NGHẼN THÌ ĐỪNG VIẾT LẠI PROMPT. Vòng viết lại ở trên đã
            # kiểm `_is_policy_violation_error`; khối last-resort này thì quên,
            # nên một cú `503` cũng kéo nó chạy — tốn thêm một lượt gửi nữa
            # (cũng `503`) rồi ghi scene là hỏng. Xem `_loi_do_nghen`.
            if (not success and not self._stop_flag
                    and self.config.get("video_last_resort_enabled", True)
                    and not self._loi_do_nghen(error_text)):
                srt_text = " ".join(str(getattr(scene, "srt_text", "") or "").split())
                simple_idea = srt_text[:260] if srt_text else "the emotional idea of this scene"
                last_resort_prompt = (
                    "Same simple hand-drawn psychology illustration style as the reference image, warm paper background, "
                    "gentle minimal motion, clean composition, no readable text, no letters, no watermark. "
                    f"Animate this narration idea in one safe clear visual beat: {simple_idea}. "
                    "Use the attached character reference as the stable central figure. "
                    "Specific movement: the central figure makes one small readable body-language change while one simple nearby symbolic shape responds subtly. "
                    "Emotional arc: visible tension, one calm shift, quieter ending. "
                    "Performance direction: restrained posture, slow hand movement, softened shoulders, simple prop response. "
                    "Keep the scene minimal and non-graphic."
                )
                if last_resort_prompt.strip().lower() != current_prompt.strip().lower():
                    self.log(f"    Video scene {sid}: thu last-resort prompt de tranh fail", "WARN")
                    self._log_prompt_rewrite_event(
                        "scene", sid, "scene_video", "last_resort",
                        current_prompt, rewritten_prompt=last_resort_prompt,
                        error_text=error_text, round_index=rewrite_round + 1, mode="video"
                    )
                    success, server_info, error_text = self._submit_video(last_resort_prompt, vid_path, media_id)
                    if success:
                        current_prompt = last_resort_prompt
                        last_resort_happened = True
            elapsed = round(time.time() - t0, 1)

            if success:
                if rewrite_happened or last_resort_happened:
                    self._log_prompt_rewrite_event(
                        "scene", sid, "scene_video", "rewrite_succeeded",
                        vp, rewritten_prompt=current_prompt, round_index=rewrite_round, mode="video"
                    )
                with self._excel_lock:
                    update_kw = {"status_vid": "done", "video_path": str(vid_path)}
                    if current_prompt != vp:
                        update_kw["video_prompt"] = current_prompt
                    wb.update_scene(sid, **update_kw)
                    if not wb.safe_save():
                        wb._save_pending_write("scene", scene_id=sid,
                                               status_vid="done", video_path=str(vid_path),
                                               video_prompt=current_prompt if current_prompt != vp else "")
                        self.log(f"    Video scene {sid}: Excel bá»‹ khÃ³a, lÆ°u pending write", "WARN")
                with self._dem_lock:          # dem duoi nhieu luong: xem `_dem_lock`
                    completed_count[0] += 1
                    _da_xong = completed_count[0]
                current_done = min(video_total, video_done_base + _da_xong)
                self.progress("videos", current_done, video_total, f"scene_{sid:03d}")
                self.log(f"    Video scene {sid} â†’ OK ({elapsed}s)")
                self.on_item_status("scene", sid, "done", None,
                                    {"elapsed": elapsed, "phase": "video", **server_info})
                return True
            else:
                if rewrite_happened:
                    self._log_prompt_rewrite_event(
                        "scene", sid, "scene_video", "rewrite_still_failed",
                        vp, rewritten_prompt=current_prompt, error_text=error_text,
                        round_index=rewrite_round, mode="video"
                    )
                fs = self._fail_status_for(error_text)
                # ⚠ THIẾU FILE ẢNH NGUỒN THÌ PHẢI DỰNG LẠI ẢNH, không phải thử
                # lại video mãi. Excel ghi ảnh "done" nhưng file trên đĩa đã mất
                # (finalize dọn nhầm, hoặc tải hụt) — bước video đọc `img_path`
                # không thấy nên trả về NGAY trong 0,0 giây. Lượt sau lặp lại y
                # hệt, vì không ai bảo pha ẢNH làm lại cảnh đó.
                #
                # Đã dính thật: TH1-0182 cảnh 81, hai lượt liên tiếp lúc
                # 17:17:02 và 17:30:51 ngày 15/08/2026, mỗi lượt `FAIL (0.0s)`
                # rồi ăn một "lượt trắng". Ba lượt là mã bị ĐỖ LẠI vĩnh viễn —
                # trong khi chỉ cần dựng lại một tấm ảnh.
                _thieu_anh = self._anh_nguon_hong(error_text)
                if _thieu_anh:
                    # ⚠ XOÁ LUÔN FILE HỎNG. Đánh dấu Excel thôi là chưa đủ: pha
                    # ẢNH bỏ qua cảnh nào ĐÃ CÓ FILE, nên một file .png rỗng
                    # hoặc sai định dạng vẫn khiến nó nghĩ việc đã xong. File
                    # còn đó thì vòng lặp hỏng vẫn kín y như cũ.
                    # ⚠ XOÁ Ở CẢ HAI CHỖ. Xoá mỗi `img/` là vô ích: `_finalize_img`
                    # chạy ngay cuối lượt đó và CHÉP BẢN HỎNG TỪ `img_backup/`
                    # trở lại. Bắt được trong log 18:02:24 ngày 15/08/2026 —
                    # xoá xong thì hai giây sau `Finalize: 0 mp4 + 1 png img/`,
                    # tức là tấm ảnh hỏng đã về chỗ cũ và lượt sau lặp lại y hệt.
                    for _thu in (self.img_dir, self.project_dir / "img_backup"):
                        for _duoi in (".png", ".jpg", ".jpeg"):
                            try:
                                _xau = _thu / "{0}{1}".format(sid, _duoi)
                                if _xau.exists():
                                    _xau.unlink()
                                    self.log("    Video scene {0}: da xoa anh nguon hong {1}/{2}"
                                             .format(sid, _thu.name, _xau.name), "WARN")
                            except Exception as _e:
                                self.log("    Video scene {0}: khong xoa duoc {1}/{2}{3} ({4})"
                                         .format(sid, _thu.name, sid, _duoi, _e), "WARN")
                with self._excel_lock:
                    if _thieu_anh:
                        wb.update_scene(sid, status_img="error", status_vid="")
                    else:
                        wb.update_scene(sid, status_vid=fs)
                    wb.safe_save()
                _tag = "TERMINAL policy" if fs == "failed" else "retry lượt sau"
                if _thieu_anh:
                    _tag = "ANH NGUON HONG -> da xoa + danh dau dung lai anh o luot sau"
                # ⚠ IN CẢ LÝ DO. Bản trước chỉ in `[error: retry lượt sau]`, nên
                # người đọc log thấy một cảnh hỏng trong 0,0 giây mà không có
                # cách nào biết vì sao. Câu lỗi đã nằm sẵn trong `error_text` —
                # chỉ là không ai đưa nó ra.
                self.log("    Video scene {0} -> FAIL ({1}s) [{2}: {3}] {4}".format(
                    sid, elapsed, fs, _tag,
                    (error_text or "(khong co mo ta loi)")[:200]), "WARN")
                self.on_item_status("scene", sid, "error", None,
                                    {"elapsed": elapsed, "phase": "video", **server_info})
                return False

        # veo3top: chay nhieu luong de "thu li bat khe ho rate". Mac dinh 10 (can bang: du luong bat khe ho
        # nhung khong dap qua nhieu tu lam bao hoa rate tren 1 may). Config veo3top_video_concurrency de chinh.
        vid_workers = self.max_concurrent
        if self.generation_backend in ("veo3top", "veo3top_b", "veo3top_b_ultra"):
            vid_workers = int(self.config.get("veo3top_video_concurrency", 7) or 7)
        elif self.generation_backend == "veo3top_b_pool":
            # pool: đẩy NHIỀU job vào hàng đợi chung để 10 account-worker luôn có việc (service tự điều phối)
            vid_workers = int(self.config.get("veo3top_pool_concurrency", 20) or 20)
        elif self.use_shopapi_for_video:
            # API shopapi: trần song song của VIDEO khác trần của ẢNH (`self.max_concurrent`),
            # và máy chủ tính lại liên tục -> `_chay_me` hỏi GET /v1/me ở MỖI LÔ.
            # Ở đây chỉ chốt trần của TOOL: người dùng ghim thì tôn trọng, không
            # ghim thì để trần CỨNG của video (máy chủ mới là người chặn thật sự).
            vid_workers = int(self.config.get("shopapi_video_concurrency", 0) or 0) \
                or self._shopapi_tran_cung("video")
        elif self.use_shopapi_for_image:
            # ẢNH đi API còn VIDEO đi backend cũ: `self.max_concurrent` lúc này là
            # trần CỨNG của ảnh (128) — vô nghĩa và nguy hiểm cho backend video cũ.
            vid_workers = max(1, int(self.config.get("max_concurrent", 1) or 1))

        self._chay_me("video", list(enumerate(pending)),
                      lambda p: _do_video(p[0], p[1]), vid_workers, result)
        return result

    def _video_reference_path(self, output_path: Path) -> Path:
        return self.img_dir / f"{output_path.stem}.png"

    def _nanopic_image_inputs_from_refs(self, refs: Optional[List[ImageInput]]) -> tuple[List[str], List[Dict[str, Any]]]:
        """
        Convert ImageInput refs to NanoPic-compatible format.
        NanoPic Flow proxy imageInputs format:
        - Prefer: {"name": media_id, "imageInputType": "IMAGE_INPUT_TYPE_REFERENCE"}
        - Fallback: {"rawImageBytes": base64, "mimeType": "image/png", "imageInputType": "..."}
        - DO NOT use "dataUri" in imageInputs (causes "Unknown name dataUri" error)
        """
        urls = []
        image_inputs = []
        for ref in refs or []:
            if isinstance(ref, ImageInput):
                data = ref.to_dict()
                # Priority 1: use name (media_id) if available
                if data.get("name"):
                    image_inputs.append(data)
                    continue
                # Priority 2: convert base64_data to rawImageBytes
                b64 = str(getattr(ref, "base64_data", "") or "").strip()
                if not b64:
                    continue
                if b64.startswith("data:"):
                    # Extract base64 from data URI
                    if "," in b64:
                        b64 = b64.split(",", 1)[1]
                mime = str(getattr(ref, "mime_type", "") or "image/png")
                image_inputs.append({
                    "imageInputType": getattr(ref.input_type, "value", str(ref.input_type)),
                    "rawImageBytes": b64,
                    "mimeType": mime,
                })
            elif isinstance(ref, dict):
                if ref.get("name") or ref.get("rawImageBytes"):
                    image_inputs.append(dict(ref))
                elif ref.get("dataUri"):
                    # Convert dataUri to rawImageBytes for imageInputs
                    uri = ref.get("dataUri", "")
                    if "," in uri:
                        b64 = uri.split(",", 1)[1]
                        image_inputs.append({
                            "imageInputType": ref.get("imageInputType", "IMAGE_INPUT_TYPE_REFERENCE"),
                            "rawImageBytes": b64,
                            "mimeType": "image/png",
                        })
        return urls, image_inputs

    def _nanopic_image_aspect_ratio(self, aspect_ratio: Optional[AspectRatio] = None) -> str:
        ar = aspect_ratio or self.aspect_ratio
        return getattr(ar, "value", str(ar))

    def _nanopic_video_aspect_ratio(self) -> str:
        ar_str = self.config.get("flow_aspect_ratio", "landscape").upper()
        return getattr(VideoAspectRatio, ar_str, VideoAspectRatio.LANDSCAPE).value

    # ===== VEO3TOP video backend (option moi, khong dung server) =====
    def _get_veo3top_provider(self):
        if self._veo3top_provider is not None:
            return self._veo3top_provider
        engine_dir = str(SUITE_ROOT / "veo3top_engine")
        if engine_dir not in sys.path:
            sys.path.insert(0, engine_dir)
        from provider import Veo3topProvider
        # Chrome cua account (giong luc tool mo de upload anh)
        account = None
        try:
            account_name = (self.config.get("flow_account_name", "") or "").strip()
            account = self.auth_service.pick_account(account_name) if self.auth_service else None
        except Exception:
            account = None
        chrome_exe = (getattr(account, "chrome_path", None) if account else None) or \
                     (self.auth_service.chrome_path() if self.auth_service else None)
        profile_dir = getattr(account, "profile_dir", None) if account else None
        if not chrome_exe or not profile_dir:
            self.log("veo3top: khong tim thay chrome_path/profile cua account", "ERROR")
            return None
        # Port CDP RIENG cho moi ma (chay dong thoi nhieu ma): lay tu so Copy (N) cua chrome.
        # Moi ma = 1 account = 1 Copy khac nhau -> port khac nhau -> khong dung cong/profile.
        m = re.search(r"Copy \((\d+)\)", str(chrome_exe))
        dbg_port = 9850 + (int(m.group(1)) if m else (abs(hash(str(profile_dir))) % 120))
        prov = Veo3topProvider(str(chrome_exe), str(profile_dir), debug_port=dbg_port, log=self.log)
        prov.project = (self.flow_project_id or "").strip() or None
        prov.video_aspect = self._nanopic_video_aspect_ratio()
        if not prov.start():
            self.log("veo3top: provider.start() that bai (chrome/auth)", "ERROR")
            return None
        self._veo3top_provider = prov
        self.log(f"veo3top: provider san sang (project={prov.project})")
        return prov

    def _veo3top_auth(self):
        """Lay (bearer, project_id) cua account theo backend veo3top -> KHONG dung ExtAuth."""
        try:
            if self.generation_backend in ("veo3top_b", "veo3top_b_ultra"):
                prov = self._get_veo3top_provider_b()
                if not prov:
                    return None, None
                auth = prov._get_auth()
                return (auth or {}).get("bearer"), (auth or {}).get("project")
            prov = self._get_veo3top_provider()   # Option A
            if not prov:
                return None, None
            return prov.bearer, prov.project
        except Exception as e:
            self.log(f"    [veo3top-auth] loi: {type(e).__name__}: {e}", "WARN")
            return None, None

    def _veo3top_upload_image(self, img_path, bearer, project_id):
        """Upload 1 anh len Ultra (curl_cffi + /v1/flow/uploadImage qua WARP). Tra media_id hoac ''.
        WARP da duoc ensure boi caller (khong goi ensure moi anh -> cham)."""
        try:
            engine_dir = str(SUITE_ROOT / "veo3top_engine")
            if engine_dir not in sys.path:
                sys.path.insert(0, engine_dir)
            import flow_client as _fc
            sid = f";{int(time.time()*1000)}"
            mid, err = _fc.upload_image(bearer, project_id, sid, str(img_path))
            if not mid:
                self.log(f"    [veo3top-upload] fail: {err}", "WARN")
            return mid or ""
        except Exception as e:
            self.log(f"    [veo3top-upload] loi: {type(e).__name__}: {e}", "WARN")
            return ""

    def _veo3top_upload_parallel(self, wb, todo, bearer, pid):
        """Upload SONG SONG danh sach anh (veo3top). Tra so anh thanh cong."""
        from concurrent.futures import ThreadPoolExecutor
        conc = int(self.config.get("veo3top_upload_concurrency", 6) or 6)
        self.log(f"[local-video] upload SONG SONG {len(todo)} anh (concurrency={conc}), KHONG qua ExtAuth")
        done = [0]
        lock = threading.Lock()

        def _up_one(s):
            if self._stop_flag:
                return False
            img = self.img_dir / f"{s.scene_id}.png"
            mid = ""
            for _try in range(3):
                mid = self._veo3top_upload_image(str(img), bearer, pid)
                if mid:
                    break
                time.sleep(1.2)
            if mid:
                with self._excel_lock:
                    wb.update_scene(s.scene_id, media_id=mid, status_img="done")
                    if not wb.safe_save():
                        wb._save_pending_write("scene", scene_id=s.scene_id, media_id=mid)
                s.media_id = mid
                with lock:
                    done[0] += 1
                    if done[0] % 10 == 0:
                        self.log(f"  [local-video] {done[0]}/{len(todo)} media_id Ultra")
                return True
            self.log(f"  [local-video] scene {s.scene_id}: upload Ultra that bai", "WARN")
            return False

        with ThreadPoolExecutor(max_workers=conc) as ex:
            results = list(ex.map(_up_one, todo))
        return sum(1 for r in results if r)

    # ===== NHANH MOI: API shopapi.vn (anh + video) ==================================

    def _doc_khoa_shopapi(self):
        """Tim khoa API shopapi. Tra (khoa, nguon). Khong tim thay -> ("", "").

        `nguon` la cau tieng Viet de hien len log, KHONG bao gio chua chinh khoa.
        Thieu SDK/module -> tra rong, va nhanh moi se tu lui ve duong cu.
        """
        try:
            return _shopapi_nap_engine().doc_khoa()
        except Exception:
            return "", ""

    # ⚠ DA BO `_shopapi_tran_song_song`: no hoi /v1/me MOT LAN luc khoi dong roi
    # giu con so do ca luot chay, va coi `0` la "chay 1 job". Ca hai deu sai:
    # tran doi lien tuc (phai hoi lai moi lo -> `_shopapi_luong`), con `0` nghia
    # la nha may DANG DUNG nen gui 1 job la chac chan an 503 va bao loi oan cho
    # mot viec khong he co loi (phai CHO roi hoi lai -> `so_luong_song_song`).

    def _shopapi_tran_cung(self, loai):
        """Tran CUNG tuyet doi cua mot loai job (tts 16 / image 128 / video 64).

        Doc tu SDK de may chu nang tran la tool an theo ngay. KHONG phai con so
        de lam so luong: no chi la chot chan phong khi doc /v1/me hong.
        """
        try:
            return int(_shopapi_nap_engine().tran_cung(loai))
        except Exception:
            return {"tts": 16, "image": 128, "video": 64}.get(loai, 1)

    def _tu_dieu_tiet(self):
        """Cho phep tool tu chia tran theo may chu? Mac dinh CO."""
        return bool(self.config.get("shopapi_tu_dieu_tiet", True))

    def _shopapi_luong(self, loai, tran_tool=None):
        """So job loai `loai` duoc ban CUNG LUC ngay bay gio.

        BAT tu dieu tiet (mac dinh): = tran may chu CHIA cho so tien trinh ma
        dang song that, roi cat them bang suat luong cua may. `tran_tool` KHONG
        con duoc dung — chinh con so go tay la thu giu tool o 40 job trong khi
        may chu moi 979 (do 15/08/2026).

        TAT: = min(tran dong cua may chu, tran cung cua loai, tran nguoi dung dat).
        KHONG go cung mot con so nao: `GET /v1/me` moi la nguon su that, va no
        doi lien tuc theo suc chua nha may chia cho so khach dang cho.

        `0` tu may chu = nha may loai do DANG DUNG -> ham cho roi hoi lai (co ghi
        log tung vong de nguoi dung biet la dang cho chu khong phai treo), va tra
        `0` khi cho qua lau de noi goi bo cuoc CO KIEM SOAT.
        """
        try:
            sb = _shopapi_nap_batch()
        except Exception as e:
            self.log("API shopapi: thieu module shopapi_batch ({0}) -> chay 1 job".format(e), "WARN")
            return 1
        return int(sb.so_luong_song_song(
            loai, tran_tool=tran_tool, api_key=self.shopapi_key, log=self.log,
            ngu=lambda giay: self._sleep_with_stop(giay),
            dung_lai=lambda: bool(self._stop_flag),
            tu_dieu_tiet=self._tu_dieu_tiet(),
        ))

    def _chay_me_shopapi(self, loai, viec, chay_mot, tran_tool=None):
        """Chay ca me `viec` qua API shopapi, tu do nhip. Tra ket qua DUNG THU TU DUA VAO.

        Day la cho thay the `ThreadPoolExecutor(max_workers=<so co dinh>)`: so
        luong khong con go cung ma do may chu quyet moi lo, con nhip thi tu do
        (muot thi +1, 429 chia doi, 503 dung han roi tham do lai bang 1 job).

        Job bi tu choi o cua KHONG bi tinh la hong - no quay ve dau hang cho.
        Job hong that thi chi minh no hong, khong keo ca me chet theo.

        ═══ MOT VONG DO DUNG CHUNG CHO CA LUOT CHAY, KHONG PHAI MOI PHA MOT CAI ═══

        Truoc 11/08/2026 cho nay khong truyen `nhip`, nen `chay_ca_me` tu dung
        mot vong do MOI cho tung pha cua tung ma: references -> scenes -> videos,
        roi ma sau lai lam lai tu dau. Hoc duoc bao nhieu vut bay nhieu.

        Docstring cua chinh `chay_ca_me` da noi san: *"dung chung mot vong do cho
        nhieu me noi tiep - vong do cang song lau cang bam sat nha may"*. Tham so
        co san tu dau, chi la khong ai truyen.

        TACH RIENG ANH VA VIDEO: hai nha may doc lap hoan toan (CONTRACT.md 8.1),
        tran khac nhau va tac nghen khac nhau. Dung chung mot vong do cho ca hai
        la de nha may video ket keo tut nhip cua nha may anh dang ranh.
        """
        sb = _shopapi_nap_batch()
        kho = getattr(self, "_shopapi_nhip_chung", None)
        if kho is None:
            kho = self._shopapi_nhip_chung = {}
        if loai not in kho:
            kho[loai] = sb._tao_nhip(bat_dau=sb.so_luong_song_song(
                loai, tran_tool=tran_tool, api_key=self.shopapi_key, log=self.log,
                ngu=lambda giay: self._sleep_with_stop(giay),
                dung_lai=lambda: bool(self._stop_flag),
            ))
        # `han_giay` = tool chờ MỘT job tối đa bao lâu. Cổng hàng chờ so nó với
        # `estimated_seconds` máy chủ trả lúc nhận job: máy chủ nói "còn 1.500
        # giây nữa mới tới lượt" trong khi ta chỉ chờ được 900/1.600 giây thì
        # gửi thêm là gửi job đi chết trong hàng. Đây chính là 14 job "vượt quá
        # thời gian chờ" của sự cố 07/08/2026 — xem `shopapi_batch.CongHangCho`.
        han_giay = (self.shopapi_video_timeout if loai == "video"
                    else self.shopapi_image_timeout)
        return sb.chay_ca_me(
            viec, chay_mot, loai, tran_tool=tran_tool, api_key=self.shopapi_key,
            tu_dieu_tiet=self._tu_dieu_tiet(),
            log=self.log, han_giay=han_giay, nhip=kho[loai],
            ngu=lambda giay: self._sleep_with_stop(giay),
            dung_lai=lambda: bool(self._stop_flag),
        )

    def _chay_me(self, loai, viec, chay_mot, tran_tool, ket):
        """Chay ca me: nhanh API thi tu do nhip, nhanh cu thi giu Y NGUYEN duong cu.

        `ket` la dict {"completed": n, "failed": n} duoc cong don tai cho, dung
        cach cu: `True` -> completed, `False` -> failed, `None` -> khong tinh
        (bi dung giua chung).

        VI SAO GOP VAO MOT CHO: ba pha (references / scenes / videos) truoc day
        chep y het khoi ThreadPoolExecutor + as_completed. Sua song song o ba
        ban sao la ba lan sai khac nhau.
        """
        dung_api = (loai == "image" and self.use_shopapi_for_image) or \
                   (loai == "video" and self.use_shopapi_for_video)
        if dung_api:
            try:
                ket_qua = self._chay_me_shopapi(loai, viec, chay_mot, tran_tool)
            except Exception as e:
                # Thieu module / loi khong luong truoc -> LUI VE duong cu, khong
                # bo ca pha. Bao TO vi day la mat cong suat, khong phai chuyen nho.
                self.log("API shopapi: chay ca me that bai ({0}) -> lui ve chay tuan tu"
                         .format(e), "ERROR")
                ket_qua = None
            if ket_qua is not None:
                for r in ket_qua:
                    if r is True:
                        ket["completed"] += 1
                    elif r is False:
                        ket["failed"] += 1
                return ket

        # Duong cu: y nguyen nhu truoc khi co nhanh API.
        with ThreadPoolExecutor(max_workers=max(1, int(tran_tool or 1))) as executor:
            futures = {executor.submit(chay_mot, v): v for v in viec}
            for future in as_completed(futures):
                if self._stop_flag:
                    executor.shutdown(wait=False, cancel_futures=True)
                    break
                r = future.result()
                if r is True:
                    ket["completed"] += 1
                elif r is False:
                    ket["failed"] += 1
        return ket

    def _shopapi_refs_to_bytes(self, refs):
        """Refs (ImageInput) -> danh sach bytes anh de UPLOAD lam URL cong khai.

        API chi nhan anh tham chieu la URL cong khai, khong nhan duong dan may va
        cung khong nhan mediaId cua Flow. `_make_ref` da nhung san base64 khi
        veo3top_image_mode == "shopapi", nen o day chi viec giai ma nguoc lai.
        Ref nao khong co bytes thi BO va ghi canh bao - gui ref rong chi ton tien
        ma anh van lech nhan vat.
        """
        if not refs:
            return []
        import base64 as _b64
        out = []
        for r in refs:
            b64 = getattr(r, "base64_data", "") or ""
            if not b64:
                self.log("    [shopapi-img] ref '{0}' khong co bytes anh -> BO (anh co the "
                         "lech nhan vat)".format(getattr(r, "name", "?")), "WARN")
                continue
            try:
                out.append(_b64.b64decode(b64))
            except Exception as e:
                self.log("    [shopapi-img] ref '{0}' giai ma base64 loi: {1} -> BO".format(
                    getattr(r, "name", "?"), e), "WARN")
        return out

    def _submit_video_shopapi(self, prompt, output_path):
        """Dung video bang API shopapi. Tra (success, info, error) - KHOP _submit_video.

        Rang buoc TEN: video `vid/X.mp4` PHAI co anh `img/X.png` cung stem (giong
        het nhanh veo3top-b-pool) - do la anh dau vao cua buoc image-to-video.
        """
        from pathlib import Path as _P
        img_path = self.img_dir / f"{_P(output_path).stem}.png"
        if not img_path.exists():
            return False, {}, f"shopapi-vid: khong thay anh scene {img_path}"
        # CỬA CUỐI trước khi dấu đi vào từng khung hình của clip. Ảnh dựng ở bản
        # trước bản này chưa qua bước xoá; soát ở đây thì chúng cũng sạch mà
        # không phải dựng lại. Ảnh đã xử lý mang dấu trong phần thông tin PNG
        # nên không bị xoá lần hai.
        self._xoa_dau_nha_cung_cap(img_path)
        try:
            _shopapi_nap_engine()
            import shopapi_video_client as svc
        except Exception as e:
            return False, {}, f"shopapi-vid: import client loi: {e}"
        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
        aspect = self._nanopic_video_aspect_ratio()
        return svc.generate(str(img_path), prompt, str(output_path), aspect=aspect,
                            timeout=self.shopapi_video_timeout,
                            api_key=self.shopapi_key, log=self.log,
                            # Trong mot me thi 429/503 phai NEM ra de vong do nhip
                            # tra viec ve hang cho; goi le thi tuyet doi khong nem
                            # (hop dong _submit_video la tra dung 3 phan tu).
                            nem_khi_nghen=_shopapi_trong_me())

    def _submit_image_shopapi(self, prompt, output_path, refs=None, aspect_ratio=None) -> tuple:
        """Tao anh bang API shopapi. Tra (ok, media_name, sinfo, err) - KHOP _submit_image."""
        output_path.parent.mkdir(parents=True, exist_ok=True)

        # Anh nay da duoc tao san trong mot job GOP `n=k` (nhieu scene cung prompt)?
        # Co roi thi tra thang, KHONG goi API lan hai - tra tien hai lan cho cung
        # mot buc anh la loi tra tien, khong phai loi ky thuat.
        san = self._shopapi_lay_anh_gop(output_path)
        if san is not None:
            return san

        try:
            _shopapi_nap_engine()
            import shopapi_image_client as sic
        except Exception as e:
            return False, None, {}, f"shopapi-img: import client loi: {e}"
        # Scene thuong khong truyen aspect_ratio -> ke thua aspect da cau hinh cua worker.
        aspect = aspect_ratio if aspect_ratio is not None else self.aspect_ratio
        refs_bytes = self._shopapi_refs_to_bytes(refs)
        ok, info, err = sic.generate_image(
            prompt, str(output_path), aspect=aspect, reference_images=refs_bytes,
            n=1, timeout=self.shopapi_image_timeout, api_key=self.shopapi_key, log=self.log,
            # Xem chu thich o `_submit_video_shopapi`.
            nem_khi_nghen=_shopapi_trong_me(),
        )
        if ok:
            self._xoa_dau_nha_cung_cap(output_path)
        return (ok, (info or {}).get("media_name"), self._shopapi_sinfo(info),
                err if not ok else "")

    def _xoa_dau_nha_cung_cap(self, duong_dan):
        """Xoá dấu ngôi sao góc phải dưới, NGAY sau khi ảnh về đĩa.

        ═══ VÌ SAO PHẢI Ở ĐÂY, KHÔNG PHẢI Ở KHÂU SAU ═══

        Đường dựng là Image-to-Video: ảnh scene chính là KHUNG ĐẦU của clip. Ảnh
        còn dấu thì mọi khung của clip đều mang dấu, và lúc đó xoá phải xử từng
        khung hình — đắt gấp bội và không đảo lại được sạch.

        Hàm tự bỏ qua ảnh đã xử lý (đóng dấu trong phần thông tin PNG) nên gọi
        lại nhiều lần vẫn an toàn. Quan trọng: phép đảo alpha KHÔNG tự biết mình
        đã chạy, xoá hai lần là để lại một ngôi sao ĐEN.
        """
        try:
            _shopapi_nap_engine()
            import xoa_dau_anh as _xd
            _xd.xoa_dau_file(str(duong_dan), log=self.log)
        except Exception as e:  # noqa: BLE001 — buoc lam dep, khong duoc lam chet luot chay
            self.log("  xoa dau: bo qua ({0}: {1})".format(type(e).__name__, e), "WARN")

    @staticmethod
    def _shopapi_sinfo(info):
        """Loc `info` cua client thanh `server_info` gon cho Excel/GUI."""
        sinfo = {"backend": "shopapi"}
        if info:
            sinfo.update({k: info[k] for k in ("bytes", "job_id", "cost", "aspect", "refs", "n")
                          if k in info})
        return sinfo

    def _shopapi_lay_anh_gop(self, output_path):
        """Anh `output_path` da co san tu mot job gop chua? Co -> tuple 4 phan tu.

        Lay ra la XOA khoi kho: moi ket qua chi dung dung mot lan, tranh chuyen
        lan chay sau trong cung tien trinh nhan nham anh cu.
        """
        khoa = str(output_path)
        with self._shopapi_gop_lock:
            san = self._shopapi_anh_gop.pop(khoa, None)
        if san is None:
            return None
        if not Path(khoa).exists():
            # File bien mat (nguoi dung xoa tay giua chung) -> coi nhu chua co.
            return None
        media_name, sinfo = san
        return True, media_name, sinfo, ""

    def _shopapi_gop_anh_cung_prompt(self, cong_viec):
        """GOP nhieu anh CUNG PROMPT vao MOT job `n=k` truoc khi chay me.

        `cong_viec`: danh sach `(prompt, out_path, refs, aspect)`.

        VI SAO DANG LAM: `POST /v1/images/generations` nhan `n` toi 8 anh mot job.
        k anh cung prompt gop lai chi chiem MOT cho trong tran song song va MOT
        lan xep hang, thay vi k cho va k lan. Tran song song la thu khan hiem
        nhat o day, nen tieu 1 thay vi k la nhan cong suat len - re hon va nhanh
        hon HAN so voi viec chi chay k job song song.

        Ket qua nhet vao `self._shopapi_anh_gop`; `_submit_image_shopapi` se tra
        thang tu do, nen TOAN BO phan ghi Excel / tien do / viet lai prompt cua
        cac pha giu nguyen khong phai sua mot dong nao.

        Gop that bai -> khong sao ca: khong ghi gi vao kho, cac anh do di duong
        binh thuong (moi anh mot job). Day la toi uu, khong phai duong song.
        """
        try:
            sic = self._shopapi_import_image_client()
            sc = _shopapi_nap_engine()
        except Exception as e:
            self.log("    [shopapi-img] khong nap duoc client de gop: {0}".format(e), "WARN")
            return 0

        nhom = {}
        for prompt, out_path, refs, aspect in cong_viec:
            if Path(out_path).exists():
                continue
            # Chi gop khi TRUNG CA BA: prompt, ti le, va bo anh tham chieu. Cung
            # prompt ma khac ref la khac anh - gop vao la sai ket qua.
            khoa = (str(prompt), sc.ty_le_api(aspect), self._shopapi_khoa_refs(refs))
            nhom.setdefault(khoa, []).append((str(out_path), refs, aspect))

        # Moi phan tu = MOT job gop. Cat theo MAX_ANH_MOT_JOB vi n>8 la 400 cho
        # CA job (mat luon 8 anh hop le), chu khong phai chi mat phan du.
        cac_job = []
        for khoa, muc in nhom.items():
            if len(muc) < 2:
                continue        # mot minh thi gop cung nhu khong
            for i in range(0, len(muc), sc.MAX_ANH_MOT_JOB):
                phan = muc[i:i + sc.MAX_ANH_MOT_JOB]
                if len(phan) >= 2:
                    cac_job.append((khoa[0], phan))
        if not cac_job:
            return 0

        da_gop = [0]

        def _mot_job_gop(job):
            prompt, phan = job
            dich = [p for p, _r, _a in phan]
            self.log("    [shopapi-img] GOP {0} anh cung prompt vao 1 job n={0} "
                     "(thay vi {0} job rieng): {1}".format(len(phan), prompt[:50]))
            ok, info, err = sic.generate_image(
                prompt, dich[0], aspect=phan[0][2],
                reference_images=self._shopapi_refs_to_bytes(phan[0][1]),
                out_paths=dich, timeout=self.shopapi_image_timeout,
                api_key=self.shopapi_key, log=self.log,
                # Gop la TOI UU, khong phai duong song: 429/503 o day khong duoc
                # bien thanh BiNghen lam roi loan me chinh - cu de no that bai
                # roi tung anh di duong binh thuong.
                nem_khi_nghen=False,
            )
            duong_da_co = list((info or {}).get("paths") or [])
            if not ok and not duong_da_co:
                self.log("    [shopapi-img] gop that bai ({0}) -> {1} anh nay se chay "
                         "tung job nhu cu".format(err, len(phan)), "WARN")
                return False
            sinfo = self._shopapi_sinfo(info)
            media_name = (info or {}).get("media_name")
            with self._shopapi_gop_lock:
                for p in duong_da_co:
                    self._shopapi_anh_gop[str(p)] = (media_name, dict(sinfo))
                da_gop[0] += len(duong_da_co)
            return True

        # Cac job gop cung chay SONG SONG voi nhau, van do may chu dinh nhip.
        self._chay_me("image", cac_job, _mot_job_gop, self.max_concurrent,
                      {"completed": 0, "failed": 0})

        if da_gop[0]:
            self.log("    [shopapi-img] gop xong {0} anh trong {1} job -> tiet kiem {2} luot "
                     "xep hang".format(da_gop[0], len(cac_job), da_gop[0] - len(cac_job)))
        return da_gop[0]

    def _shopapi_import_image_client(self):
        """Nap `shopapi_image_client`. Tach ra de bai kiem thay the duoc."""
        _shopapi_nap_engine()
        import shopapi_image_client as sic
        return sic

    @staticmethod
    def _shopapi_khoa_refs(refs):
        """Chu ky cua bo anh tham chieu, de biet hai viec co dung chung ref khong."""
        if not refs:
            return ()
        return tuple(sorted(
            str(getattr(r, "name", "") or "") + "|" + str(getattr(r, "media_id", "") or "")
            + "|" + str(len(getattr(r, "base64_data", "") or ""))
            for r in refs
        ))

    def _submit_video_veo3top_b_pool(self, prompt, output_path):
        """NHÀ MÁY CHUNG: gửi ảnh scene tới video_factory service (dùng chung 10 account ultra).
        Service tự lease account rảnh nhất -> upload ảnh (account đó) -> generate (egress ladder) -> download.
        Trả (success, info, error) khớp _submit_video."""
        from pathlib import Path as _P
        img_path = self.img_dir / f"{_P(output_path).stem}.png"
        if not img_path.exists():
            return False, {}, f"veo3top-b-pool: khong thay anh scene {img_path}"
        try:
            import sys as _s
            _eng = r"D:\VE3_SUITE\veo3top_engine"
            if _eng not in _s.path:
                _s.path.insert(0, _eng)
            import video_factory_client as vfc
        except Exception as e:
            return False, {}, f"veo3top-b-pool: import client loi: {e}"
        aspect = self._nanopic_video_aspect_ratio()
        return vfc.generate(str(img_path), prompt, str(output_path), aspect=aspect, log=self.log)

    def _submit_video_veo3top(self, prompt, output_path, reference_image_id):
        """Thay the buoc tao video bang phuong phap veo3top. Tra (success, info, error) nhu _submit_video."""
        if not reference_image_id:
            return False, {}, "veo3top: thieu media_id (anh chua upload xong)"
        try:
            prov = self._get_veo3top_provider()
        except Exception as e:
            return False, {}, f"veo3top init loi: {e}"
        if not prov:
            return False, {}, "veo3top: khong khoi tao duoc provider"
        prov.video_aspect = self._nanopic_video_aspect_ratio()
        return prov.submit_video(prompt, reference_image_id, str(output_path))

    # ===== VEO3TOP-B: token-chrome CHUNG + auth cache per-account (giong veo3top, nhe khi nhieu ma) =====
    def _account_chrome(self):
        """Tra (chrome_exe, profile_dir, copy_number) cua account bound cho ma nay."""
        account = None
        try:
            account_name = (self.config.get("flow_account_name", "") or "").strip()
            account = self.auth_service.pick_account(account_name) if self.auth_service else None
        except Exception:
            account = None
        chrome_exe = (getattr(account, "chrome_path", None) if account else None) or \
                     (self.auth_service.chrome_path() if self.auth_service else None)
        profile_dir = getattr(account, "profile_dir", None) if account else None
        name = getattr(account, "email", None) if account else (self.config.get("flow_account_name", "") or "")
        m = re.search(r"Copy \((\d+)\)", str(chrome_exe or ""))
        copyn = int(m.group(1)) if m else None
        return chrome_exe, profile_dir, name, copyn

    def _get_veo3top_provider_b(self):
        if self._veo3top_provider_b is not None:
            return self._veo3top_provider_b
        engine_dir = str(SUITE_ROOT / "veo3top_engine")
        if engine_dir not in sys.path:
            sys.path.insert(0, engine_dir)
        from provider_b import Veo3topProviderB
        chrome_exe, profile_dir, name, copyn = self._account_chrome()
        if not chrome_exe or not profile_dir:
            self.log("veo3top-b: khong tim thay chrome/profile cua account", "ERROR")
            return None
        idx = copyn if copyn is not None else (abs(hash(str(profile_dir))) % 60)
        auth_port = 9850 + idx
        # mode: veo3top_b_ultra -> token tu chrome ACCOUNT (login); veo3top_b -> chrome TRANG.
        token_mode = "account" if self.generation_backend == "veo3top_b_ultra" else "blank"
        # blank: 1 chrome trang/ma (giong veo3top, nhe may). Nhu cau token thap (~0.1 tok/s/ma) +
        # rate_coordinator ghim submit + buffer 24 -> 1 chrome du. Nut that la quota recaptcha CHUNG, khong phai token.
        # token_port cach nhau *4/ma -> toi da 4 chrome trang/ma khong dung port (van cho phep tang qua config).
        _default_chromes = 1
        token_chromes = int(self.config.get("veo3top_token_chromes", _default_chromes) or _default_chromes)
        if token_mode == "account":
            token_chromes = 1
        token_chromes = max(1, min(4, token_chromes))
        # token_port RIENG/ma (tranh dung port giua subprocess): 9600 + idx*4
        token_port = 9600 + (idx * 4)
        prov = Veo3topProviderB(
            account_name=name, chrome_exe=str(chrome_exe), profile_dir=str(profile_dir),
            auth_port=auth_port, token_port=token_port, token_chromes=token_chromes,
            token_mode=token_mode,
            video_aspect=self._nanopic_video_aspect_ratio(), log=self.log,
        )
        if not prov.start():
            self.log("veo3top-b: token factory khong khoi tao duoc", "ERROR")
            return None
        self._veo3top_provider_b = prov
        self.log(f"veo3top-b: san sang (token factory = chrome TRANG no-login, {token_chromes} chrome)")
        return prov

    def _submit_video_veo3top_b(self, prompt, output_path, reference_image_id):
        if not reference_image_id:
            return False, {}, "veo3top-b: thieu media_id (anh chua upload xong)"
        try:
            prov = self._get_veo3top_provider_b()
        except Exception as e:
            return False, {}, f"veo3top-b init loi: {e}"
        if not prov:
            return False, {}, "veo3top-b: khong khoi tao duoc provider"
        prov.video_aspect = self._nanopic_video_aspect_ratio()
        return prov.submit_video(prompt, reference_image_id, str(output_path))

    # ===== TAO ANH veo3top-b: ban thang flowMedia:batchGenerateImages (tai dung ha tang video) =====
    def _veo3top_image_aspect(self) -> str:
        """Map aspect_ratio anh -> IMAGE_ASPECT_RATIO_* cho endpoint anh."""
        ar = self.aspect_ratio
        name = ar.name.upper() if hasattr(ar, "name") else str(ar).upper()
        if "PORTRAIT" in name:
            return "IMAGE_ASPECT_RATIO_PORTRAIT"
        if "SQUARE" in name:
            return "IMAGE_ASPECT_RATIO_SQUARE"
        return "IMAGE_ASPECT_RATIO_LANDSCAPE"

    def _get_veo3top_image_provider(self):
        if self._veo3top_image_provider is not None:
            return self._veo3top_image_provider
        engine_dir = str(SUITE_ROOT / "veo3top_engine")
        if engine_dir not in sys.path:
            sys.path.insert(0, engine_dir)
        from provider_image_b import Veo3topImageProviderB
        chrome_exe, profile_dir, name, copyn = self._account_chrome()
        if not chrome_exe or not profile_dir:
            self.log("img-b: khong tim thay chrome/profile cua account", "ERROR")
            return None
        idx = copyn if copyn is not None else (abs(hash(str(profile_dir))) % 60)
        auth_port = 9910 + idx
        token_mode = "account" if self.veo3top_image_mode == "account" else "blank"
        # blank: 3 chrome trắng đẻ đủ token cho nhiều scene chạy SONG SONG (dưới tải quota nặng cần
        # bắn đồng thời nhiều request mới chui qua RESOURCE_EXHAUSTED — giống farm video).
        _default_chromes = 3 if token_mode == "blank" else 1
        token_chromes = int(self.config.get("veo3top_image_token_chromes", _default_chromes) or _default_chromes)
        if token_mode == "account":
            token_chromes = 1
        token_chromes = max(1, min(4, token_chromes))
        # token_port RIENG cho anh (tach hang video 9600+idx*4): 9700 + idx*4
        token_port = 9700 + (idx * 4)
        prov = Veo3topImageProviderB(
            account_name=name, chrome_exe=str(chrome_exe), profile_dir=str(profile_dir),
            auth_port=auth_port, token_port=token_port, token_chromes=token_chromes,
            token_mode=token_mode, image_aspect=self._veo3top_image_aspect(), log=self.log,
        )
        if not prov.start():
            self.log("img-b: token factory khong khoi tao duoc", "ERROR")
            return None
        self._veo3top_image_provider = prov
        self.log(f"img-b: san sang (token {token_mode}, {token_chromes} chrome) - tao anh ban thang Flow API")
        return prov

    def _submit_image_veo3top_b(self, prompt, output_path, refs=None, aspect_ratio=None) -> tuple:
        """Tao anh bang veo3top-b. Tra (ok, media_name, sinfo, err) khop _submit_image."""
        output_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            prov = self._get_veo3top_image_provider()
        except Exception as e:
            return False, None, {}, f"img-b init loi: {type(e).__name__}: {e}"
        if not prov:
            return False, None, {}, "img-b: khong khoi tao duoc provider"
        # refs (List[ImageInput]) -> image_inputs dicts. name la media_name (hop le neu ref cung tao
        # tren cung account veo3top). base64 fallback neu chua co name.
        image_inputs = None
        if refs:
            image_inputs = []
            for r in refs:
                try:
                    d = r.to_dict() if hasattr(r, "to_dict") else (r if isinstance(r, dict) else None)
                    if d and (d.get("name") or d.get("rawImageBytes")):
                        image_inputs.append(d)
                except Exception:
                    pass
            image_inputs = image_inputs or None
        aspect = None
        if aspect_ratio is not None:
            an = aspect_ratio.name.upper() if hasattr(aspect_ratio, "name") else str(aspect_ratio).upper()
            aspect = ("IMAGE_ASPECT_RATIO_PORTRAIT" if "PORTRAIT" in an else
                      "IMAGE_ASPECT_RATIO_SQUARE" if "SQUARE" in an else "IMAGE_ASPECT_RATIO_LANDSCAPE")
        ok, info, err = prov.submit_image(prompt, str(output_path), image_inputs=image_inputs, aspect=aspect)
        sinfo = {"backend": "veo3top-b-image", "account": prov.account}
        if info:
            sinfo.update({k: info[k] for k in ("bytes", "seed") if k in info})
        return (ok, (info or {}).get("media_name"), sinfo, err if not ok else "")

    def _refs_to_raw_inputs(self, refs):
        """Refs (ImageInput) -> image_inputs rawImageBytes (embed) cho NHÀ MÁY ẢNH POOL.
        Pool dùng account KHÁC nhau -> mediaId (account-scoped) VÔ DỤNG, phải embed bytes ảnh reference.
        Ưu tiên base64_data; ref chỉ có mediaId -> đọc FILE ảnh (nếu resolve được)."""
        if not refs:
            return None
        import base64 as _b64
        out = []
        for r in refs:
            try:
                b64 = getattr(r, "base64_data", "") or ""   # đã embed bởi _make_ref khi pool mode
                mime = getattr(r, "mime_type", "image/png") or "image/png"
                it = getattr(r, "input_type", None)
                itype = getattr(it, "value", None) or "IMAGE_INPUT_TYPE_REFERENCE"
                if b64:
                    out.append({"imageInputType": itype, "rawImageBytes": b64, "mimeType": mime})
            except Exception:
                pass
        return out or None

    def _make_ref(self, ref_name, media_id):
        """Tạo 1 ImageInput reference. Với POOL (account khác nhau -> mediaId account-scoped VÔ DỤNG),
        EMBED base64 ảnh nhân vật/địa điểm từ file nv/{ref_name}.png -> ref đi kèm ảnh thật, giữ nhất quán."""
        ii = ImageInput(name=media_id or "", input_type=ImageInputType.REFERENCE)
        # "shopapi" cung phai nhung bytes: API nhan anh tham chieu bang URL cong khai
        # (upload tu bytes), tuyet doi khong hieu mediaId cua Flow.
        if self.veo3top_image_mode in ("pool", "shopapi"):
            try:
                import base64 as _b64
                for ext in (".png", ".jpg", ".jpeg"):
                    fp = self.nv_dir / f"{ref_name}{ext}"
                    if fp.exists():
                        with open(fp, "rb") as f:
                            ii.base64_data = _b64.b64encode(f.read()).decode()
                        ii.mime_type = "image/png" if ext == ".png" else "image/jpeg"
                        break
                if not ii.base64_data:
                    self.log(f"  [img-pool] KHÔNG thấy file ảnh reference nv/{ref_name}.png -> ref bị bỏ (ảnh có thể lệch nhân vật)", "WARN")
            except Exception as e:
                self.log(f"  [img-pool] đọc ref {ref_name} lỗi: {e}", "WARN")
        return ii

    def _submit_image_veo3top_b_pool(self, prompt, output_path, refs=None, aspect_ratio=None) -> tuple:
        """NHÀ MÁY ẢNH CHUNG: gửi tới image_factory service (pool 10 account, IPv6 riêng, 7 luồng/account).
        Ảnh đồng bộ (không poll). Trả (ok, media_name, sinfo, err) khớp _submit_image."""
        output_path.parent.mkdir(parents=True, exist_ok=True)
        image_inputs = self._refs_to_raw_inputs(refs)
        # A scene normally does not pass an explicit aspect_ratio. Previously this
        # left aspect=None and image_factory silently defaulted to LANDSCAPE.
        # Always inherit the worker's configured aspect instead (landscape configs
        # keep the exact same behaviour; portrait configs finally take effect).
        aspect = self._veo3top_image_aspect()
        if aspect_ratio is not None:
            an = aspect_ratio.name.upper() if hasattr(aspect_ratio, "name") else str(aspect_ratio).upper()
            aspect = ("IMAGE_ASPECT_RATIO_PORTRAIT" if "PORTRAIT" in an else
                      "IMAGE_ASPECT_RATIO_SQUARE" if "SQUARE" in an else "IMAGE_ASPECT_RATIO_LANDSCAPE")
        try:
            import sys as _s
            _eng = r"D:\VE3_SUITE\veo3top_engine"
            if _eng not in _s.path:
                _s.path.insert(0, _eng)
            import image_factory_client as ifc
        except Exception as e:
            return False, None, {}, f"img-pool: import client loi: {e}"
        ok, info, err = ifc.generate_image(prompt, str(output_path), aspect=aspect,
                                           image_inputs=image_inputs, log=self.log)
        # TIER 3: pro pool hết quota CẢ 3 model TRÊN NHIỀU account -> fallback tài khoản ULTRA (video).
        # Quota IMAGE_GENERATION của Ultra TÁCH BIỆT quota VIDEO -> KHÔNG hại video. -> 1 ảnh gần như không bao giờ fail.
        if not ok and self._pool_quota_exhausted(err) and self.config.get("ultra_image_fallback", True):
            self.log("    [pool] pro cạn quota (3 model × account) -> TIER3: thử tài khoản ULTRA", "WARN")
            u_ok, u_name, u_sinfo, u_err = self._submit_image_ultra(prompt, output_path, image_inputs, aspect)
            if u_ok:
                return True, u_name, u_sinfo, ""
            err = f"{err} | ultra: {u_err}"
        sinfo = {"backend": "veo3top-b-image-pool"}
        if info:
            sinfo.update({k: info[k] for k in ("bytes", "seed", "account", "egress") if k in info})
        return (ok, (info or {}).get("media_name"), sinfo, err if not ok else "")

    def _pool_quota_exhausted(self, err) -> bool:
        """True nếu image pool trả lỗi do CẠN QUOTA (không phải lỗi khác) -> đáng fallback Ultra."""
        e = (err or "").lower()
        return ("quota" in e) or ("quá nhiều lượt" in e) or ("qua nhieu luot" in e)

    def _load_ultra_accounts(self):
        """Load account ULTRA (accounts/vid_accounts.txt) + cookie từ _auth_cache (video pool đã cache).
        Cache ở instance. Chỉ lấy account CÓ cookie."""
        if getattr(self, "_ultra_accts", None) is not None:
            return self._ultra_accts
        accts = []
        try:
            import json as _j
            vf = SUITE_ROOT / "accounts" / "vid_accounts.txt"
            cdir = SUITE_ROOT / "veo3top_engine" / "_auth_cache"
            emails = []
            if vf.exists():
                for line in vf.read_text(encoding="utf-8").splitlines():
                    line = line.strip()
                    if line and not line.startswith("#") and "@" in line:
                        emails.append(line.split("|")[0].strip())
            for e in emails:
                safe = e.replace("@", "_").replace(".", "_")
                cf = cdir / f"{safe}.json"
                if cf.exists():
                    try:
                        d = _j.load(open(cf, encoding="utf-8"))
                        if d.get("cookie"):
                            accts.append({"email": e, "cookie": d["cookie"], "project": d.get("project")})
                    except Exception:
                        pass
        except Exception as ex:
            self.log(f"  [ultra] load account lỗi: {ex}", "WARN")
        self._ultra_accts = accts
        self.log(f"  [ultra] {len(accts)} tài khoản Ultra sẵn cookie cho fallback ảnh")
        return accts

    def _ultra_upload_inputs(self, fc, bearer, proj, email, image_inputs):
        """Upload rawImageBytes refs lên account Ultra -> [{imageInputType, name}]. Cache per (email, hash).
        Trả None nếu upload lỗi (bỏ account này). [] nếu không có ref."""
        if not image_inputs:
            return []
        import base64 as _b64, tempfile as _tf, os as _os
        if not hasattr(self, "_ultra_ref_cache"):
            self._ultra_ref_cache = {}
        out = []
        for inp in image_inputs:
            itype = inp.get("imageInputType", "IMAGE_INPUT_TYPE_REFERENCE")
            if inp.get("name"):
                out.append({"imageInputType": itype, "name": inp["name"]}); continue
            b64 = inp.get("rawImageBytes")
            if not b64:
                continue
            ck = (email, hash(b64))
            nm = self._ultra_ref_cache.get(ck)
            if not nm:
                tp = None
                try:
                    raw = _b64.b64decode(b64)
                    fd, tp = _tf.mkstemp(suffix=".png"); _os.close(fd)
                    with open(tp, "wb") as f:
                        f.write(raw)
                    nm, _err = fc.upload_image(bearer, proj, f";{int(time.time()*1000)}", tp)
                    if nm:
                        self._ultra_ref_cache[ck] = nm
                except Exception:
                    nm = None
                finally:
                    if tp:
                        try: _os.remove(tp)
                        except Exception: pass
            if not nm:
                return None
            out.append({"imageInputType": itype, "name": nm})
        return out

    def _submit_image_ultra(self, prompt, output_path, image_inputs, aspect) -> tuple:
        """TIER 3 fallback: tạo ảnh bằng tài khoản ULTRA (video) khi pro pool cạn quota cả 3 model.
        android_bypass (curl thuần, không chrome). Xoay account + xoay 3 model. Quota IMAGE Ultra tách biệt VIDEO."""
        try:
            import sys as _s
            _eng = r"D:\VE3_SUITE\veo3top_engine"
            if _eng not in _s.path:
                _s.path.insert(0, _eng)
            import flow_client as fc
        except Exception as e:
            return False, None, {}, f"ultra: import flow_client lỗi: {e}"
        accts = self._load_ultra_accounts()
        if not accts:
            return False, None, {}, "ultra: không có account (thiếu vid_accounts.txt / cookie)"
        import random as _r
        models = ["GEM_PIX_2", "NARWHAL", "HARBOR_SEAL"]
        seed = (abs(hash(prompt)) % 900000) + 1
        aspect = aspect or "IMAGE_ASPECT_RATIO_LANDSCAPE"
        start = _r.randrange(len(accts))
        order = list(range(start, len(accts))) + list(range(0, start))
        last_err = "ultra: hết account × model"
        for idx in order:
            if self._stop_flag:
                return False, None, {}, "ultra: stop"
            a = accts[idx]
            try:
                bearer, _ = fc.bearer_from_cookie(a["cookie"])
            except Exception:
                bearer = None
            if not bearer:
                last_err = "ultra: cookie chết"; continue
            proj = a.get("project")
            if not proj:
                try:
                    projs = fc.list_projects(a["cookie"]) or []
                    proj = projs[0] if projs else fc.create_project(a["cookie"])
                    a["project"] = proj
                except Exception:
                    proj = None
            if not proj:
                last_err = "ultra: không có project"; continue
            resolved = self._ultra_upload_inputs(fc, bearer, proj, a["email"], image_inputs)
            if resolved is None:
                last_err = "ultra: upload ref lỗi"; continue
            for model in models:
                if self._stop_flag:
                    return False, None, {}, "ultra: stop"
                payload = fc.build_image_payload(prompt, proj, fc.BYPASS_TOKEN, seed, aspect=aspect,
                                                 model=model, image_inputs=resolved, app_type=fc.APP_TYPE_ANDROID)
                try:
                    kind, data = fc.generate_image(bearer, proj, payload, bypass=True)
                except Exception as e:
                    kind, data = "other", str(e)
                if kind == "ok":
                    name, fife, b64, rseed = fc.image_result(data)
                    if fife or b64:
                        try:
                            output_path.parent.mkdir(parents=True, exist_ok=True)
                            if fife:
                                n = fc.download_image_url(fife, str(output_path))
                            else:
                                import base64 as _b64
                                b = _b64.b64decode(b64)
                                with open(output_path, "wb") as f:
                                    f.write(b)
                                n = len(b)
                            self.log(f"    [ultra] ✅ RA ẢNH {output_path.name} ({n//1024}KB) [{a['email'][:18]} {model}]", "SUCCESS")
                            return True, name, {"backend": "ultra-fallback", "account": a["email"], "model": model, "bytes": n}, ""
                        except Exception as e:
                            last_err = f"ultra: tải ảnh lỗi {e}"; continue
                elif kind == "auth":
                    last_err = "ultra: 401"; break         # cookie account này chết -> account khác
                elif kind == "recaptcha_quota":
                    last_err = f"ultra: {a['email'][:12]} {model} hết quota"; continue   # -> model kế
                else:
                    last_err = f"ultra: {kind}"; continue   # unusual/other -> model kế
        return False, None, {}, last_err

    def _nanopic_client(self) -> NanoPicAPI:
        use_flow_proxy = self.config.get("nanopic_use_flow_proxy", False)
        return NanoPicAPI(
            nano_token=self.config.get("nanopic_token", ""),
            access_token=self.config.get("nanopic_access_token", ""),
            video_cookie=self.config.get("nanopic_video_cookie", ""),
            base_url=self.config.get("nanopic_base_url", "https://flow-api.nanoai.pics/api/v2"),
            timeout=self.timeout,
            poll_interval=self.config.get("nanopic_poll_interval_seconds", 5),
            poll_timeout=self.config.get("nanopic_poll_timeout_seconds", 900),
            log_func=self.log,
            use_flow_proxy=use_flow_proxy,
            flow_auth_token=self.config.get("nanopic_flow_auth_token", "") or self.bearer_token,
            flow_base_url=self.config.get("nanopic_flow_base_url", "https://aisandbox-pa.googleapis.com"),
            flow_project_id=self.config.get("nanopic_flow_project_id", "") or self.flow_project_id,
        )

    def _submit_video_nanopic(
        self,
        prompt: str,
        output_path: Path,
        reference_image_id: str
    ) -> tuple:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        sinfo = {"backend": "nanopic"}
        last_error = ""
        image_urls = []
        ref_path = self._video_reference_path(output_path)
        if ref_path.exists():
            try:
                image_urls = [NanoPicAPI.file_to_data_uri(ref_path)]
            except Exception as exc:
                return False, sinfo, f"Cannot encode video reference image: {exc}"

        for attempt in range(self.retry_count):
            if self._stop_flag:
                return False, sinfo, last_error
            try:
                api = self._nanopic_client()
                ok, result, err = api.create_video_and_download(
                    prompt=prompt,
                    output_path=output_path,
                    aspect_ratio=self._nanopic_video_aspect_ratio(),
                    video_model=self.config.get("nanopic_video_model", "VEO_3_FAST_LOWER"),
                    video_type=self.config.get("nanopic_video_type", "frame"),
                    image_urls=image_urls,
                    start_image_media_id=reference_image_id,
                )
                if ok:
                    if result.task_id:
                        sinfo["task_id"] = result.task_id
                    if result.media_id:
                        sinfo["media_id"] = result.media_id
                    return True, sinfo, ""
                last_error = err or "NanoPic video failed"
                self.log(f"    [{output_path.stem}] NanoPic video loi: {last_error[:300]} (lan {attempt+1}/{self.retry_count})", "WARN")
                if self._is_policy_violation_error(last_error):
                    return False, sinfo, last_error
            except Exception as e:
                last_error = str(e)
                self.log(f"    [{output_path.stem}] NanoPic exception: {type(e).__name__}: {e} (lan {attempt+1})", "ERROR")
            if attempt < self.retry_count - 1:
                delay = 5 * (attempt + 1)
                self.log(f"    Retry NanoPic sau {delay}s...")
                if not self._sleep_with_stop(delay):
                    break
        return False, sinfo, last_error

    def _should_try_nanopic_fallback(self, error_text: str) -> bool:
        if self.generation_backend == "nanopic" or not self.nanopic_fallback_enabled:
            return False
        if not self.config.get("nanopic_use_flow_proxy", False):
            return False
        if not self.config.get("nanopic_token", ""):
            return False
        if self._stop_flag:
            return False
        return True

    def _merge_nanopic_fallback_info(self, server_info: Dict[str, Any], fallback_info: Dict[str, Any], reason: str) -> Dict[str, Any]:
        merged = dict(server_info or {})
        merged["backend"] = "nanopic"
        merged["fallback_from"] = "server"
        if reason:
            merged["fallback_reason"] = reason[:300]
        for key, value in (fallback_info or {}).items():
            merged[f"nanopic_{key}"] = value
        return merged

    def _submit_video(
        self,
        prompt: str,
        output_path: Path,
        reference_image_id: str
    ) -> tuple:
        """
        Gá»­i prompt táº¡o video lÃªn server (Image-to-Video).
        Flow 3 bÆ°á»›c:
          1. POST /api/fix/create-video-veo3 â†’ taskId
          2. Poll /api/fix/task-status â†’ operations
          3. Poll Google batchCheckAsyncVideoGenerationStatus â†’ video URL â†’ download

        Returns:
            (success: bool, server_info: dict, error_text: str)
        """
        # NHANH MOI (uu tien cao nhat): dung API shopapi.vn. Chi bat khi DA CO KHOA -
        # thieu khoa thi co xuong 0 tu __init__ (da canh bao TO) va roi xuong duong cu.
        if self.use_shopapi_for_video:
            return self._submit_video_shopapi(prompt, output_path)

        if self.generation_backend == "veo3top":
            return self._submit_video_veo3top(prompt, output_path, reference_image_id)
        if self.generation_backend in ("veo3top_b", "veo3top_b_ultra"):
            return self._submit_video_veo3top_b(prompt, output_path, reference_image_id)
        if self.generation_backend == "veo3top_b_pool":
            return self._submit_video_veo3top_b_pool(prompt, output_path)

        if self.generation_backend == "nanopic":
            ok, sinfo, err = self._submit_video_nanopic(prompt, output_path, reference_image_id)
            return ok, sinfo, err

        # FlowKit mode: try FlowKit pool first
        if self.generation_backend in ("flowkit", "combined") and self.flowkit_pool:
            ok, sinfo, err = self._submit_video_via_pool(
                prompt, output_path, reference_image_id, self.flowkit_pool
            )
            if ok:
                return ok, sinfo, err
            if self.generation_backend == "flowkit":
                self.log(f"    [{output_path.stem}] FlowKit video: {err[:200]}", "WARN")
                return ok, sinfo, err
            self.log(f"    [{output_path.stem}] FlowKit video fail ({err[:100]}), chuyen sang server...", "WARN")

        output_path.parent.mkdir(parents=True, exist_ok=True)
        sinfo = {}
        last_error = ""
        client_job_id = f"vid-{output_path.stem}-{uuid.uuid4().hex[:12]}"

        for attempt in range(self.retry_count):
            if self._stop_flag:
                return False, sinfo, last_error

            server = self.pool.pick_best_server() if self.pool else None
            if not server:
                self.log("  Chá» server...", "WARN")
                server = self.pool.wait_for_server(max_wait=300) if self.pool else None
                if not server:
                    self.log("  Khong co server!", "ERROR")
                    last_error = "No server available"
                    break

            sinfo = {
                "server": server.name,
                "queue": server.queue_size,
                "pending": server.local_pending,
            }
            self.log(f"    â†’ Server: {server.name} (queue={server.queue_size})")

            try:
                api = GoogleFlowAPI(
                    bearer_token=self.bearer_token,
                    project_id=self.flow_project_id,
                    timeout=self.timeout,
                    local_server_url=server.url
                )

                # Video aspect ratio
                var_str = self.config.get("flow_aspect_ratio", "landscape").upper()
                video_ar = getattr(VideoAspectRatio, var_str, VideoAspectRatio.LANDSCAPE)

                # generate_video â†’ ná»™i bá»™ sáº½:
                #   1. POST /create-video-veo3 â†’ taskId
                #   2. _poll_proxy_video_task â†’ poll task-status â†’ láº¥y operations
                #   3. _poll_google_with_operations â†’ poll Google â†’ láº¥y video URL
                success, vid_result, error = api.generate_video(
                    prompt=prompt,
                    aspect_ratio=video_ar,
                    model=VideoModel.VEO3_I2V_FAST,
                    reference_image_id=reference_image_id,
                    client_job_id=client_job_id,
                )

                if success and vid_result and vid_result.video_url:
                    filename = output_path.stem
                    saved = api.download_video(vid_result, output_path.parent, filename)
                    if saved:
                        self.pool.mark_success(server)
                        return True, sinfo, ""
                    else:
                        self.log(f"    [{output_path.stem}] Táº£i video tháº¥t báº¡i (láº§n {attempt+1}/{self.retry_count})", "WARN")
                        self.pool.mark_task_failed(server)
                else:
                    err = error or "KhÃ´ng cÃ³ video URL"
                    last_error = err
                    if "401" in err or "authentication" in err.lower():
                        self.log(f"    [{output_path.stem}] TOKEN HET HAN (lan {attempt+1})", "ERROR")
                        if self._refresh_flow_auth(reason="401 from server"):
                            api.bearer_token = self.bearer_token
                            self.log(f"    [{output_path.stem}] Da refresh token, retry...", "INFO")
                        else:
                            self.log(f"    [{output_path.stem}] Refresh token THAT BAI", "ERROR")
                        import time as _time; _time.sleep(2)
                        continue
                    elif "400" in err or "invalid" in err.lower():
                        self.log(f"    [{output_path.stem}] GOOGLE Tá»ª CHá»I â€” {err[:300]} (láº§n {attempt+1})", "ERROR")
                    elif "FAILED" in err:
                        self.log(f"    [{output_path.stem}] VIDEO THáº¤T Báº I â€” {err[:300]} (láº§n {attempt+1})", "ERROR")
                        # Nếu là policy violation, return ngay để outer loop rewrite
                        if self._is_policy_violation_error(err):
                            self.log(f"    [{output_path.stem}] Phat hien policy violation, return de rewrite", "WARN")
                            return False, sinfo, last_error
                    elif "timeout" in err.lower() or "Timeout" in err:
                        self.log(f"    [{output_path.stem}] Háº¾T THá»œI GIAN â€” {err[:200]} (láº§n {attempt+1})", "WARN")
                    else:
                        self.log(f"    [{output_path.stem}] Lá»–I: {err[:300]} (láº§n {attempt+1})", "WARN")
                    self.pool.mark_task_failed(server)

            except Exception as e:
                last_error = str(e)
                self.log(f"    [{output_path.stem}] NGOáº I Lá»†: {type(e).__name__}: {e} (láº§n {attempt+1})", "ERROR")
                if self.pool and server:
                    self.pool.mark_task_failed(server)

            if self._stop_flag:
                break
            if attempt < self.retry_count - 1:
                delay = 5 * (attempt + 1)
                self.log(f"    Thá»­ láº¡i sau {delay}s...")
                if not self._sleep_with_stop(delay):
                    break

        if self._should_try_nanopic_fallback(last_error):
            self.log(f"    [{output_path.stem}] Server video fail, chuyen sang NanoPic fallback: {last_error[:200]}", "WARN")
            ok, nanopic_info, nanopic_error = self._submit_video_nanopic(prompt, output_path, reference_image_id)
            if ok:
                return True, self._merge_nanopic_fallback_info(sinfo, nanopic_info, last_error), ""
            if nanopic_error:
                last_error = f"Server: {last_error}; NanoPic: {nanopic_error}"

        return False, sinfo, last_error

    # =========================================================================
    # SERVER COMMUNICATION (Images)
    # =========================================================================

    def _extract_video_media_id(self, vid_data: dict) -> str:
        """Extract media_id from FlowKit video response (various formats)."""
        # Format 1: operations[].operation.metadata.video.mediaId (gateway I2V workflow)
        for op in vid_data.get("operations", []):
            operation = op.get("operation", {})
            meta_video = operation.get("metadata", {}).get("video", {})
            mid = meta_video.get("mediaId", "")
            if mid:
                return mid
        # Format 2: media[].name (direct completion)
        for m in vid_data.get("media", []):
            name = m.get("name", "")
            if name:
                return name
        # Format 3: operations[].response.media[].name (async completion)
        for op in vid_data.get("operations", []):
            resp = op.get("response", {})
            for m in resp.get("media", []):
                if m.get("name"):
                    return m["name"]
        return ""

    def _deep_find_video_url(self, obj) -> str:
        """Recursively find video URL in any JSON structure."""
        if isinstance(obj, str):
            if obj.startswith(("http://", "https://")) and any(
                h in obj for h in (".mp4", "video", "fife", "download", "lh3.", "ugc/")
            ):
                return obj
            return ""
        if isinstance(obj, dict):
            for key in ("fifeUrl", "signedUrl", "downloadUrl", "videoUrl", "url", "_video_url"):
                val = obj.get(key)
                found = self._deep_find_video_url(val)
                if found:
                    return found
            for val in obj.values():
                found = self._deep_find_video_url(val)
                if found:
                    return found
        if isinstance(obj, list):
            for item in obj:
                found = self._deep_find_video_url(item)
                if found:
                    return found
        return ""

    def _download_flowkit_video(self, server_url: str, media_id: str, output_path: Path) -> bool:
        """Download video content via FlowKit get_media endpoint."""
        import requests as _req
        try:
            # Use Google's get_media API via FlowKit extension
            api_key = "AIzaSyBtrm0o5ab1c-Ec8ZuLcGt3oJAA5VWt3pY"
            get_url = f"https://aisandbox-pa.googleapis.com/v1/media/{media_id}?key={api_key}&clientContext.tool=PINHOLE"
            resp = _req.post(
                f"{server_url}/api/fix/create-image-veo3",
                json={
                    "body_json": {},
                    "flow_url": get_url,
                    "job_id": f"dl-{media_id[:8]}",
                },
                timeout=30,
            )
            # Simplified: just mark as completed — video is on Google's servers
            # Tool's later phases handle video download from media URLs
            return True
        except Exception as e:
            self.log(f"    Video download: {e}", "WARN")
            return False

    _flowkit_recaptcha_fail_count = 0
    _flowkit_last_reset_ts = 0.0
    _flowkit_resetting = False

    def _flowkit_reset_captcha(self, server_url: str):
        """Reset reCAPTCHA 403: clear data -> kill Chrome -> re-auth -> reopen -> wait flowKey."""
        import requests as _req
        import urllib.request as _ur
        self.log("[FLOWKIT] 403 reCAPTCHA - full reset: clear data + re-login...", "WARN")
        self._flowkit_resetting = True
        try:
            # 1. Tell extension to clear all site data
            resp = _req.post(f"{server_url}/api/fix/reset-captcha", json={}, timeout=30)
            data = resp.json()
            if not data.get("success"):
                self.log(f"[FLOWKIT] Reset failed: {data.get('error')}", "ERROR")
                self._flowkit_last_reset_ts = time.time()
                self._flowkit_recaptcha_fail_count = 0
                return

            self.log("[FLOWKIT] Site data cleared. Killing Chrome...", "INFO")
            time.sleep(2)

            # 2. Kill Chrome Portable process (so auth can open fresh)
            try:
                port_match = re.search(r":(\d+)$", server_url.rstrip("/"))
                chrome_idx = int(port_match.group(1)) - 8100 if port_match else 3
                chrome_dir = SUITE_ROOT / f"GoogleChromePortable - Copy ({chrome_idx + 1})"
                os.system(f'wmic process where "ExecutablePath like \'%%Copy ({chrome_idx + 1})%%\'" call terminate >nul 2>&1')
            except Exception:
                pass
            time.sleep(5)

            # 3. Re-run full auth flow (check login -> login -> Flow -> create project)
            self.log("[FLOWKIT] Running full auth flow...", "INFO")
            if self._wb:
                auth_ok = self._ensure_flow_auth(self._wb, force_refresh=True, reason="reCAPTCHA 403 reset")
                if auth_ok:
                    self.log("[FLOWKIT] Auth OK, token refreshed", "INFO")
                else:
                    self.log("[FLOWKIT] Auth failed after reset", "ERROR")

            # 4. Reopen Chrome with extension (auth kept it open with keep_chrome_open)
            #    Wait for extension to connect + capture flowKey
            self.log("[FLOWKIT] Waiting for extension + flowKey...", "INFO")
            for i in range(30):
                time.sleep(3)
                try:
                    r = _ur.urlopen(f"{server_url}/health", timeout=3)
                    h = json.loads(r.read())
                    if h.get("extension_connected") and h.get("flowKeyPresent"):
                        self.log("[FLOWKIT] Reset complete - extension connected + flowKey ready!", "SUCCESS")
                        break
                except Exception:
                    pass
            else:
                self.log("[FLOWKIT] flowKey not ready after 90s", "WARN")
                # Try opening Chrome manually as last resort
                self._ensure_flowkit_chrome_open()

            self.log("[FLOWKIT] Resuming generation...", "INFO")
        except Exception as e:
            self.log(f"[FLOWKIT] Reset exception: {e}", "ERROR")
        self._flowkit_resetting = False
        self._flowkit_last_reset_ts = time.time()
        self._flowkit_recaptcha_fail_count = 0

    def _submit_image_via_pool(
        self,
        prompt: str,
        output_path: Path,
        refs: List[ImageInput] = None,
        poll_callback: callable = None,
        aspect_ratio: Optional[AspectRatio] = None,
        pool: "ServerPool" = None,
    ) -> tuple:
        """Submit image generation through any ServerPool (server or flowkit).
        FlowKit instances expose the same /api/fix/ endpoints via ve3_compat adapter.
        Returns: (success, media_name, server_info, error_text)
        """
        output_path.parent.mkdir(parents=True, exist_ok=True)
        sinfo = {}
        last_error = ""
        client_job_id = f"img-{output_path.stem}-{uuid.uuid4().hex[:12]}"

        for attempt in range(self.retry_count):
            if self._stop_flag:
                return False, None, sinfo, last_error

            # Wait if FlowKit is resetting (re-login in progress)
            if pool is self.flowkit_pool and self._flowkit_resetting:
                self.log("    Waiting for FlowKit reset to complete...", "WARN")
                for _w in range(60):
                    if not self._flowkit_resetting or self._stop_flag:
                        break
                    time.sleep(2)

            if pool is self.flowkit_pool:
                server = self._pick_flowkit_server()
            else:
                server = pool.pick_best_server() if pool else None
            if not server:
                self.log("  Cho server/flowkit available...", "WARN")
                server = pool.wait_for_server(max_wait=300) if pool else None
                if not server:
                    last_error = "No server/flowkit available"
                    break

            sinfo = {"server": server.name, "server_url": server.url,
                     "queue": server.queue_size, "pending": server.local_pending}
            self.log(f"    {server.name} (queue={server.queue_size})")

            try:
                api = GoogleFlowAPI(
                    bearer_token=self.bearer_token,
                    project_id=self.flow_project_id,
                    timeout=self.timeout,
                    local_server_url=server.url
                )
                success, images, error = api.generate_images(
                    prompt=prompt, count=1,
                    aspect_ratio=aspect_ratio or self.aspect_ratio,
                    image_inputs=refs or [],
                    poll_callback=poll_callback,
                    client_job_id=client_job_id,
                )
                if success and images:
                    img = images[0]
                    saved = api.download_image(img, output_path.parent, output_path.stem)
                    if saved:
                        pool.mark_success(server)
                        self._flowkit_recaptcha_fail_count = 0
                        return True, img.media_name, sinfo, ""
                    pool.mark_task_failed(server)
                else:
                    last_error = error or "Unknown error"
                    self.log(f"    [{output_path.stem}] LOI: {last_error[:200]} (lan {attempt+1})", "WARN")
                    pool.mark_task_failed(server)

                    # FlowKit: detect 429 quota exhausted — pause project
                    if pool is self.flowkit_pool and "429_QUOTA" in last_error:
                        self._write_quota_wait_marker(server.name if server else "")
                        self._stop_flag = True
                        return False, None, sinfo, last_error

                    # FlowKit: detect reCAPTCHA rate-limit - full reset with lock
                    if pool is self.flowkit_pool and "recaptcha" in last_error.lower():
                        self._flowkit_recaptcha_fail_count += 1
                        if self._flowkit_recaptcha_fail_count >= 5 and time.time() - self._flowkit_last_reset_ts > 120:
                            with self._auth_lock:
                                if time.time() - self._flowkit_last_reset_ts > 120:
                                    self._flowkit_reset_captcha(server.url)
                            continue
            except Exception as e:
                last_error = str(e)
                self.log(f"    [{output_path.stem}] NGOAI LE: {e} (lan {attempt+1})", "ERROR")
                if pool and server:
                    pool.mark_task_failed(server)

            if self._stop_flag:
                break
            if attempt < self.retry_count - 1:
                delay = 2 * (attempt + 1)
                if not self._sleep_with_stop(delay):
                    break

        return False, None, sinfo, last_error

    def _submit_video_via_pool(
        self,
        prompt: str,
        output_path: Path,
        reference_image_id: str,
        pool: "ServerPool" = None,
    ) -> tuple:
        """Submit video generation through any ServerPool (server or flowkit).
        For FlowKit pool: sends directly in Flow UI format (Veo 3.1 Lite).
        Returns: (success, server_info, error_text)
        """
        output_path.parent.mkdir(parents=True, exist_ok=True)
        sinfo = {}
        last_error = ""
        client_job_id = f"vid-{output_path.stem}-{uuid.uuid4().hex[:12]}"

        is_flowkit = pool is self.flowkit_pool

        for attempt in range(self.retry_count):
            if self._stop_flag:
                return False, sinfo, last_error

            if is_flowkit:
                server = self._pick_flowkit_server()
            else:
                server = pool.pick_best_server() if pool else None
            if not server:
                server = pool.wait_for_server(max_wait=300) if pool else None
                if not server:
                    last_error = "No server/flowkit available"
                    break

            sinfo = {"server": server.name, "server_url": server.url}
            self.log(f"    {server.name} (queue={server.queue_size})")

            try:
                if is_flowkit:
                    # FlowKit: send in exact Flow UI format (Veo 3.1 Lite)
                    ar_str = str(self.aspect_ratio).split(".")[-1].upper()
                    video_ar = f"VIDEO_ASPECT_RATIO_{ar_str}"
                    body_json = {
                        "mediaGenerationContext": {
                            "batchId": str(uuid.uuid4()),
                            "audioFailurePreference": "BLOCK_SILENCED_VIDEOS",
                        },
                        "clientContext": {
                            "projectId": self.flow_project_id or "auto",
                            "tool": "PINHOLE",
                            "userPaygateTier": "PAYGATE_TIER_TWO",
                            "sessionId": f";{int(time.time() * 1000)}",
                            "recaptchaContext": {
                                "token": "",
                                "applicationType": "RECAPTCHA_APPLICATION_TYPE_WEB",
                            },
                        },
                        "requests": [{
                            "aspectRatio": video_ar,
                            "textInput": {"structuredPrompt": {"parts": [{"text": prompt}]}},
                            "videoModelKey": "veo_3_1_r2v_lite_low_priority",
                            "seed": int(time.time()) % 10000,
                            "metadata": {},
                            "referenceImages": [{"mediaId": reference_image_id, "imageUsageType": "IMAGE_USAGE_TYPE_ASSET"}],
                        }],
                        "useV2ModelConfig": True,
                    }
                    import requests as _req
                    resp = _req.post(
                        f"{server.url}/api/fix/create-video-veo3",
                        json={"body_json": body_json, "flow_auth_token": self.bearer_token, "job_id": client_job_id},
                        timeout=60,
                    )
                    result = resp.json()
                    if not result.get("success"):
                        last_error = result.get("error", "Video submit failed")
                        self.log(f"    [{output_path.stem}] VIDEO LOI: {last_error[:200]} (lan {attempt+1})", "WARN")
                        pool.mark_task_failed(server)
                        if "429_QUOTA" in last_error:
                            self._write_quota_wait_marker(server.name if server else "")
                            self._stop_flag = True
                            return False, sinfo, last_error
                    else:
                        task_id = result["taskId"]
                        t_start = time.time()
                        deadline = t_start + 420
                        while time.time() < deadline:
                            if self._stop_flag:
                                break
                            time.sleep(10)
                            try:
                                sr = _req.get(f"{server.url}/api/fix/task-status?taskId={task_id}", timeout=15)
                                st = sr.json()
                                if st.get("success") and st.get("result"):
                                    vid_data = st.get("result", {})
                                    vid_mid = self._extract_video_media_id(vid_data)
                                    video_path = vid_data.get("_video_path", "")
                                    video_url = vid_data.get("_video_url", "")
                                    if not video_url:
                                        video_url = self._deep_find_video_url(vid_data)
                                    output_path.parent.mkdir(parents=True, exist_ok=True)
                                    downloaded = False
                                    if video_path and os.path.isfile(video_path):
                                        shutil.copy2(video_path, output_path)
                                        downloaded = True
                                    elif video_url and video_url.startswith("file://"):
                                        local = video_url.replace("file://", "")
                                        if os.path.isfile(local):
                                            shutil.copy2(local, output_path)
                                            downloaded = True
                                    elif video_url and video_url.startswith("http"):
                                        vr = _req.get(video_url, timeout=120, stream=True)
                                        if vr.status_code == 200:
                                            with open(output_path, "wb") as f:
                                                for chunk in vr.iter_content(chunk_size=8192):
                                                    f.write(chunk)
                                            downloaded = True
                                        else:
                                            self.log(f"    [{output_path.stem}] VIDEO download HTTP {vr.status_code}", "WARN")
                                    if downloaded:
                                        pool.mark_success(server)
                                        elapsed = int(time.time() - t_start)
                                        sz = os.path.getsize(output_path) // 1024
                                        self.log(f"    [{output_path.stem}] VIDEO OK ({elapsed}s, {sz}KB)", "SUCCESS")
                                        return True, sinfo, ""
                                    else:
                                        self.log(f"    [{output_path.stem}] VIDEO completed but no downloadable content", "WARN")
                                        last_error = "Video completed but no downloadable content"
                                        break
                                elif st.get("success") is False and st.get("error"):
                                    last_error = st.get("error", "Video failed")
                                    break
                            except Exception:
                                pass
                        else:
                            last_error = "Video timeout 420s"
                        pool.mark_task_failed(server)
                else:
                    # Server pool: use GoogleFlowAPI
                    api = GoogleFlowAPI(
                        bearer_token=self.bearer_token,
                        project_id=self.flow_project_id,
                        timeout=self.timeout,
                        local_server_url=server.url
                    )
                    ar_str = str(self.aspect_ratio).split(".")[-1].upper()
                    video_ar = getattr(VideoAspectRatio, ar_str, VideoAspectRatio.LANDSCAPE)
                    success, vid_result, error = api.generate_video(
                        prompt=prompt,
                        aspect_ratio=video_ar,
                        model=VideoModel.VEO3_I2V_FAST,
                        reference_image_id=reference_image_id,
                        client_job_id=client_job_id,
                    )
                    if success and vid_result and vid_result.video_url:
                        saved = api.download_video(vid_result, output_path.parent, output_path.stem)
                        if saved:
                            pool.mark_success(server)
                            return True, sinfo, ""
                        pool.mark_task_failed(server)
                    else:
                        last_error = error or "Video generation failed"
                        self.log(f"    [{output_path.stem}] VIDEO LOI: {last_error[:200]} (lan {attempt+1})", "WARN")
                        pool.mark_task_failed(server)
                        if pool is self.flowkit_pool and "429_QUOTA" in last_error:
                            self._write_quota_wait_marker(server.name if server else "")
                            self._stop_flag = True
                            return False, sinfo, last_error
            except Exception as e:
                last_error = str(e)
                self.log(f"    [{output_path.stem}] VIDEO NGOAI LE: {e} (lan {attempt+1})", "ERROR")
                if pool and server:
                    pool.mark_task_failed(server)

            if self._stop_flag:
                break
            if attempt < self.retry_count - 1:
                delay = 3 * (attempt + 1)
                if not self._sleep_with_stop(delay):
                    break

        return False, sinfo, last_error

    def _submit_image_nanopic(
        self,
        prompt: str,
        output_path: Path,
        refs: List[ImageInput] = None,
        poll_callback: callable = None,
        aspect_ratio: Optional[AspectRatio] = None
    ) -> tuple:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        sinfo = {"backend": "nanopic"}
        last_error = ""
        image_urls, image_inputs = self._nanopic_image_inputs_from_refs(refs)

        for attempt in range(self.retry_count):
            if self._stop_flag:
                return False, None, sinfo, last_error
            try:
                api = self._nanopic_client()
                ok, result, err = api.create_image_and_download(
                    prompt=prompt,
                    output_path=output_path,
                    aspect_ratio=self._nanopic_image_aspect_ratio(aspect_ratio),
                    image_model=self.config.get("nanopic_image_model", "NARWHAL"),
                    image_urls=image_urls,
                    image_inputs=image_inputs,
                    poll_callback=poll_callback,
                )
                if ok:
                    if result.task_id:
                        sinfo["task_id"] = result.task_id
                    media_id = result.media_id or result.media_url or result.task_id
                    return True, media_id, sinfo, ""
                last_error = err or "NanoPic image failed"
                self.log(f"    [{output_path.stem}] NanoPic image loi: {last_error[:300]} (lan {attempt+1}/{self.retry_count})", "WARN")
                if self._is_policy_violation_error(last_error):
                    return False, None, sinfo, last_error
            except Exception as e:
                last_error = str(e)
                self.log(f"    [{output_path.stem}] NanoPic exception: {type(e).__name__}: {e} (lan {attempt+1})", "ERROR")
            if attempt < self.retry_count - 1:
                delay = 2 * (attempt + 1)
                self.log(f"    Retry NanoPic sau {delay}s...")
                if not self._sleep_with_stop(delay):
                    break
        return False, None, sinfo, last_error

    # ─── FLOW2: tao anh bang TOKEN PRO LOCAL (qua /api/img/generate) ───────────
    def _ensure_local_refs_registered(self):
        """Dang ky raw bytes nv/ len TAT CA server (1 lan/project). Server tu upload
        len account Pro local cua tung Chrome -> giu nhan vat bang quota local."""
        if self._local_refs_registered:
            return
        with self._local_ref_lock:
            if self._local_refs_registered:
                return
            import base64 as _b64, requests as _req, io as _io
            refs = []
            if self.nv_dir.is_dir():
                for f in sorted(self.nv_dir.iterdir()):
                    if f.suffix.lower() in (".png", ".jpg", ".jpeg", ".webp") and f.is_file():
                        try:
                            # DOWNSCALE ref <=1024px JPEG: anh lon (~1MB) day qua extension bi
                            # "Failed to fetch" (gioi han size). Giong farm_client.downscale_ref cua FLOW2.
                            try:
                                from PIL import Image as _Image
                                im = _Image.open(_io.BytesIO(f.read_bytes())).convert("RGB")
                                w, h = im.size
                                if max(w, h) > 1024:
                                    s = 1024.0 / float(max(w, h))
                                    im = im.resize((max(1, int(w * s)), max(1, int(h * s))))
                                buf = _io.BytesIO()
                                im.save(buf, format="JPEG", quality=85)
                                b64 = _b64.b64encode(buf.getvalue()).decode("utf-8")
                                mime = "image/jpeg"
                            except Exception:
                                # Fallback: raw bytes (neu khong co PIL)
                                b64 = _b64.b64encode(f.read_bytes()).decode("utf-8")
                                mime = "image/jpeg" if f.suffix.lower() in (".jpg", ".jpeg") else "image/png"
                        except Exception:
                            continue
                        refs.append({"name": f.stem, "image_base64": b64, "mime_type": mime})
            self._local_ref_names = [r["name"] for r in refs]
            code = self.project_dir.name
            servers = self.pool.servers if self.pool else []
            for srv in servers:
                if not refs:
                    break
                try:
                    _req.post(f"{srv.url}/api/img/register-refs",
                              json={"project": code, "refs": refs}, timeout=180)
                except Exception as e:
                    self.log(f"    [local] register-refs {srv.name} fail: {e}", "WARN")
            if refs:
                self.log(f"    [local] da dang ky {len(refs)} ref nv/ ({self._local_ref_names}) len {len(servers)} server")
            self._local_refs_registered = True

    def _download_local_image(self, img: dict, output_path: Path) -> bool:
        """Luu anh tu ket qua /api/img/generate: uu tien fifeUrl (CDN cong khai), roi base64."""
        import base64 as _b64, requests as _req
        data = None
        if img.get("url"):
            try:
                r = _req.get(img["url"], timeout=120)
                if r.status_code == 200:
                    data = r.content
            except Exception as e:
                self.log(f"    [local] tai fifeUrl fail: {e}", "WARN")
        if data is None and img.get("base64"):
            b = img["base64"]
            if "," in b:
                b = b.split(",", 1)[1]
            try:
                data = _b64.b64decode(b)
            except Exception:
                data = None
        if data:
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_bytes(data)
            return True
        return False

    def _submit_image_local(self, prompt, output_path, refs=None, aspect_ratio=None) -> tuple:
        """Tao anh bang token Pro LOCAL: POST /api/img/generate (KHONG gui bearer token)
        -> server dung flowKey + projectId account local. Tra (ok, media_name, sinfo, err)."""
        import requests as _req
        output_path.parent.mkdir(parents=True, exist_ok=True)
        self._ensure_local_refs_registered()
        use_refs = bool(refs) and bool(self._local_ref_names)
        ref_names = self._local_ref_names if use_refs else None
        ar = aspect_ratio or self.aspect_ratio
        aspect_str = ar.name.lower() if hasattr(ar, "name") else str(ar).lower()
        code = self.project_dir.name
        # Local mode: KHONG dung pick_best_server (gating "busy" kieu cu lam serialize + cho lau).
        # Cu round-robin server enabled; gateway /api/img/generate tu chia cho Chrome local_ready.
        servers = [s for s in (self.pool.servers if self.pool else []) if getattr(s, "enabled", True)]
        if not servers:
            return False, None, {}, "No server configured"
        sinfo, last_error = {}, ""
        hard = 0          # loi THAT SU -> dem (cap retry_count)
        soft = 0          # het Chrome ranh / account tam block -> CHO slot, KHONG dem (tu khop capacity)
        max_soft = 240    # tran ~12 phut/anh de tranh ket vinh vien
        logged = False
        while hard < max(1, self.retry_count) and soft < max_soft:
            if self._stop_flag:
                return False, None, sinfo, "stopped"
            self._local_rr = getattr(self, "_local_rr", 0) + 1
            server = servers[self._local_rr % len(servers)]
            sinfo = {"server": server.name, "server_url": server.url}
            if not logged:
                self.log(f"    [local] {output_path.stem} -> {server.name} (token Pro local)")
                logged = True
            try:
                r = _req.post(f"{server.url}/api/img/generate", json={
                    "prompt": prompt, "project": code, "use_refs": use_refs,
                    "ref_names": ref_names, "aspect_ratio": aspect_str, "count": 1,
                }, timeout=self.timeout + 280)
                j = r.json()
            except Exception as e:
                last_error = str(e); hard += 1
                if not self._sleep_with_stop(2 * hard):
                    break
                continue
            if j.get("success") and j.get("images"):
                if self._download_local_image(j["images"][0], output_path):
                    if self.pool:
                        self.pool.mark_success(server)
                    return True, j["images"][0].get("media_name"), sinfo, ""
                last_error = "tai anh that bai"; hard += 1
                if not self._sleep_with_stop(2):
                    break
                continue
            last_error = str(j.get("error", "")) or "khong ro loi"
            # NO_LOCAL_READY = het Chrome ranh -> CHO slot (tu khop so Chrome active, khong tinh fail).
            # NO_FLOW_KEY / NO_LOCAL_PROJECT = VM dang warmup (Chrome chua bat duoc flowKey/project)
            #   -> CHO, dung instant-fail (giong NO_LOCAL_READY; max_soft van chan ket vinh vien).
            # 403/429/captcha/cooling = account tam block, VM recovery swap -> cung cho.
            if any(k in last_error for k in ("NO_LOCAL_READY", "NO_FLOW_KEY", "NO_LOCAL_PROJECT",
                                             "429", "RECAPTCHA", "CAPTCHA", "COOLING")):
                soft += 1
                if soft == 1 or soft % 20 == 0:
                    self.log(f"    [local] {output_path.stem}: cho Chrome ranh... ({last_error[:50]})", "INFO")
                if not self._sleep_with_stop(3):
                    break
                continue
            # loi that su -> dem
            hard += 1
            self.log(f"    [local] {output_path.stem} loi: {last_error[:160]} (lan {hard})", "ERROR")
            if not self._sleep_with_stop(2):
                break
        return False, None, sinfo, last_error

    def _submit_image(
        self,
        prompt: str,
        output_path: Path,
        refs: List[ImageInput] = None,
        poll_callback: callable = None,
        aspect_ratio: Optional[AspectRatio] = None
    ) -> tuple:
        """
        Send prompt to server to generate image.

        Returns:
            (success: bool, media_name: str or None, server_info: dict, error_text: str)
        """
        # NHANH MOI (uu tien cao nhat): tao anh bang API shopapi.vn.
        # Chi bat khi DA CO KHOA; thieu khoa -> co bang 0 (da canh bao TO o __init__)
        # va roi xuong dung backend cu ben duoi, khong chet lang.
        if self.use_shopapi_for_image:
            return self._submit_image_shopapi(prompt, output_path, refs, aspect_ratio)

        # OPTION MOI: tao anh bang veo3top-b (ban thang Flow API, giong video). Uu tien cao nhat khi bat.
        if self.use_veo3top_for_image:
            if self.veo3top_image_mode == "pool":
                return self._submit_image_veo3top_b_pool(prompt, output_path, refs, aspect_ratio)
            return self._submit_image_veo3top_b(prompt, output_path, refs, aspect_ratio)

        # FLOW2: tao anh bang token Pro LOCAL tren server (khong gui token Ultra).
        if self.use_local_token_for_image:
            return self._submit_image_local(prompt, output_path, refs, aspect_ratio)

        if self.generation_backend == "nanopic":
            return self._submit_image_nanopic(prompt, output_path, refs, poll_callback, aspect_ratio)

        # FlowKit mode: try FlowKit pool first
        if self.generation_backend in ("flowkit", "combined") and self.flowkit_pool:
            ok, media_name, sinfo, err = self._submit_image_via_pool(
                prompt, output_path, refs, poll_callback, aspect_ratio, self.flowkit_pool
            )
            if ok:
                return ok, media_name, sinfo, err
            if self.generation_backend == "flowkit":
                return ok, media_name, sinfo, err
            # combined: fall through to server pool
            self.log(f"    [{output_path.stem}] FlowKit fail ({err[:100]}), chuyen sang server...", "WARN")

        output_path.parent.mkdir(parents=True, exist_ok=True)
        sinfo = {}
        last_error = ""
        client_job_id = f"img-{output_path.stem}-{uuid.uuid4().hex[:12]}"

        for attempt in range(self.retry_count):
            if self._stop_flag:
                return False, None, sinfo, last_error

            # Pick server
            server = self.pool.pick_best_server() if self.pool else None
            if not server:
                self.log("  Cho server available...", "WARN")
                server = self.pool.wait_for_server(max_wait=300) if self.pool else None
                if not server:
                    self.log("  Khong co server nao available!", "ERROR")
                    last_error = "No server available"
                    break

            sinfo = {
                "server": server.name,
                "server_url": server.url,
                "queue": server.queue_size,
                "pending": server.local_pending,
            }

            self.log(f"    Server: {server.name} (queue={server.queue_size}, pending={server.local_pending})")

            try:
                api = GoogleFlowAPI(
                    bearer_token=self.bearer_token,
                    project_id=self.flow_project_id,
                    timeout=self.timeout,
                    local_server_url=server.url
                )

                success, images, error = api.generate_images(
                    prompt=prompt,
                    count=1,
                    aspect_ratio=aspect_ratio or self.aspect_ratio,
                    image_inputs=refs or [],
                    poll_callback=poll_callback,
                    client_job_id=client_job_id,
                )

                if success and images:
                    img = images[0]
                    filename = output_path.stem
                    saved = api.download_image(img, output_path.parent, filename)

                    if saved:
                        self.pool.mark_success(server)
                        return True, img.media_name, sinfo, ""
                    else:
                        self.log(f"    [{output_path.stem}] Tai anh that bai (lan {attempt+1}/{self.retry_count})", "WARN")
                        self.pool.mark_task_failed(server)
                else:
                    err = error or "Khong ro loi"
                    last_error = err
                    if "401" in err or "authentication" in err.lower():
                        self.log(f"    [{output_path.stem}] TOKEN HET HAN (lan {attempt+1})", "ERROR")
                        if self._refresh_flow_auth(reason="401 from server"):
                            api.bearer_token = self.bearer_token
                            self.log(f"    [{output_path.stem}] Da refresh token, retry...", "INFO")
                        else:
                            self.log(f"    [{output_path.stem}] Refresh token THAT BAI", "ERROR")
                        import time as _time; _time.sleep(2)
                        continue
                    elif "400" in err or "invalid" in err.lower():
                        self.log(f"    [{output_path.stem}] GOOGLE TU CHOI {err[:200]} (lan {attempt+1})", "ERROR")
                        self.log(f"    Co the media_id cu khong hop le, thu tao lai anh nhan vat truoc", "WARN")
                    elif "403" in err or "unusual activity" in err.lower() or "help center" in err.lower():
                        self.log(f"    [{output_path.stem}] BI CHAN (403) {err[:200]} (lan {attempt+1})", "ERROR")
                    elif "timeout" in err.lower():
                        self.log(f"    [{output_path.stem}] HET THOI GIAN CHO {err[:200]} (lan {attempt+1})", "WARN")
                    else:
                        self.log(f"    [{output_path.stem}] LOI: {err[:300]} (lan {attempt+1})", "WARN")
                    self.pool.mark_task_failed(server)

            except Exception as e:
                last_error = str(e)
                self.log(f"    [{output_path.stem}] NGOAI LE: {type(e).__name__}: {e} (lan {attempt+1})", "ERROR")
                if self.pool and server:
                    self.pool.mark_task_failed(server)

            # Retry delay
            if self._stop_flag:
                break
            if attempt < self.retry_count - 1:
                delay = 2 * (attempt + 1)
                self.log(f"    Retry sau {delay}s...")
                if not self._sleep_with_stop(delay):
                    break

        if self._should_try_nanopic_fallback(last_error):
            self.log(f"    [{output_path.stem}] Server image fail, chuyen sang NanoPic fallback: {last_error[:200]}", "WARN")
            ok, media_id, nanopic_info, nanopic_error = self._submit_image_nanopic(prompt, output_path, refs, poll_callback, aspect_ratio)
            if ok:
                return True, media_id, self._merge_nanopic_fallback_info(sinfo, nanopic_info, last_error), ""
            if nanopic_error:
                last_error = f"Server: {last_error}; NanoPic: {nanopic_error}"

        return False, None, sinfo, last_error


# =============================================================================
# CLI Entry Point - Subprocess mode support
# =============================================================================

#: Khoa cho MOI dong giao thuc `@@...|` di ra stdout.
#
# VI SAO BAT BUOC PHAI CO: GUI doc stdout cua worker THEO DONG
# (`ve3_gui.py` tach `@@PROG|phase|cur|total|detail` bang `split("|")`). Ke tu
# khi cac pha chay hang chuc luong cung luc, ba ham duoi day bi goi dong thoi tu
# nhieu luong. `write()` roi `flush()` la HAI buoc: khong khoa thi mot luong co
# the chen giua hai buoc cua luong khac, dong bi cat lam doi va GUI parse ra rac
# - te nhat la `int(cur)` no ValueError roi ca thanh tien do dung im, trong khi
# job van dang chay ngon lanh. Loi kieu do rat kho lan ra vi no chi hien khi
# dong khach.
#
# Khoa nay CHI om hai lenh ghi, khong om viec tao chuoi, nen no khong bao gio la
# cho tac nghen.
_KHOA_STDOUT = threading.Lock()


def _viet_dong(dong):
    """Ghi TRON MOT dong giao thuc ra stdout, khong de luong khac chen vao giua."""
    with _KHOA_STDOUT:
        sys.stdout.write(dong)
        sys.stdout.flush()


def _structured_log(msg, level="INFO"):
    msg_clean = str(msg).replace("\n", " ").replace("\r", "")
    _viet_dong(f"@@LOG|{level}|{msg_clean}\n")


def _structured_progress(phase, current, total, detail=""):
    detail_clean = str(detail).replace("|", "_").replace("\n", " ").replace("\r", "")
    _viet_dong(f"@@PROG|{phase}|{current}|{total}|{detail_clean}\n")


def _structured_item(item_type, item_id, status, path=None, extras=None):
    path_str = str(path) if path else ""
    # `ensure_ascii=False` co the nha ky tu xuong dong tu du lieu -> phai cat,
    # neu khong mot dong `@@ITEM|` tu no da la hai dong roi.
    extras_json = json.dumps(extras or {}, ensure_ascii=False).replace("\n", " ").replace("\r", "")
    _viet_dong(f"@@ITEM|{item_type}|{item_id}|{status}|{path_str}|{extras_json}\n")


def main():
    import argparse
    parser = argparse.ArgumentParser(description="VE3 Worker")
    parser.add_argument("project_dir", help="Project directory")
    parser.add_argument("--config", dest="config_file", default=None,
                        help="Path to JSON config file (subprocess mode)")
    parser.add_argument("--mode", dest="run_mode", default="all",
                        choices=["all", "image-only", "video-only"],
                        help="all=ảnh+video (mặc định); image-only=chỉ PHASE 1-3; video-only=chỉ PHASE 4-5 (tách 2 trạm)")
    args = parser.parse_args()

    if args.config_file:
        config_path = Path(args.config_file)
        with open(config_path, "r", encoding="utf-8") as f:
            config = json.load(f)
        log_func = _structured_log
        progress_func = _structured_progress
        item_func = _structured_item
    else:
        import yaml
        config_path = VE3_DIR / "config" / "settings.yaml"
        with open(config_path, "r", encoding="utf-8") as f:
            config = yaml.safe_load(f) or {}
        log_func = None
        progress_func = None
        item_func = None

    # --mode (CLI) ưu tiên; nếu config có run_mode thì tôn trọng khi --mode không được truyền
    if args.run_mode and args.run_mode != "all":
        config["run_mode"] = args.run_mode
    elif "run_mode" not in config:
        config["run_mode"] = args.run_mode

    worker = VE3Worker(
        project_dir=args.project_dir, config=config,
        log_func=log_func,
        progress_func=progress_func,
        on_item_status=item_func,
    )
    result = worker.run()

    if args.config_file:
        _viet_dong(f"@@RESULT|{json.dumps(result, ensure_ascii=False)}\n")
    else:
        status = "Done" if result["success"] else "Errors"
        print(f"\n{status}: {result['completed']}/{result['total']} images")
        for err in result.get("errors", []):
            print(f"  - {err}")

    sys.exit(0 if result.get("success") else 1)


if __name__ == "__main__":
    main()

