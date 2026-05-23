"""
VE3 Tool - Progressive Prompts Generator
=========================================
Táº¡o prompts theo tá»«ng step, má»—i step lÆ°u vÃ o Excel ngay.
API cÃ³ thá»ƒ Ä‘á»c context tá»« Excel Ä‘á»ƒ há»c tá»« nhá»¯ng gÃ¬ Ä‘Ã£ lÃ m.

Flow (Top-Down Planning):
    Step 1:   PhÃ¢n tÃ­ch story â†’ Excel (story_analysis)
    Step 1.5: PhÃ¢n tÃ­ch ná»™i dung con â†’ Excel (story_segments)
              - Chia cÃ¢u chuyá»‡n thÃ nh cÃ¡c pháº§n
              - Má»—i pháº§n cáº§n bao nhiÃªu áº£nh Ä‘á»ƒ truyá»n táº£i
    Step 2:   Táº¡o characters â†’ Excel (characters)
    Step 3:   Táº¡o locations â†’ Excel (characters vá»›i loc_xxx)
    Step 4:   Táº¡o director_plan â†’ Excel (director_plan)
              - Dá»±a vÃ o segments Ä‘á»ƒ phÃ¢n bá»• scenes
    Step 4.5: LÃªn káº¿ hoáº¡ch chi tiáº¿t tá»«ng scene â†’ Excel (scene_planning)
              - Ã Ä‘á»“ nghá»‡ thuáº­t cho má»—i scene
              - GÃ³c mÃ¡y, cáº£m xÃºc, Ã¡nh sÃ¡ng
    Step 5:   Táº¡o scene prompts â†’ Excel (scenes)
              - Äá»c planning Ä‘á»ƒ viáº¿t prompt chÃ­nh xÃ¡c

Lá»£i Ã­ch:
    - Fail recovery: Resume tá»« step bá»‹ fail
    - Debug: Xem Excel biáº¿t step nÃ o sai
    - Kiá»ƒm soÃ¡t: CÃ³ thá»ƒ sá»­a Excel giá»¯a chá»«ng
    - Cháº¥t lÆ°á»£ng: API Ä‘á»c context tá»« Excel
    - Top-down: LÃªn káº¿ hoáº¡ch trÆ°á»›c, prompt sau
"""

import sys
import os

# Fix Windows encoding issues
if sys.platform == "win32":
    if sys.stdout and hasattr(sys.stdout, 'reconfigure'):
        try:
            sys.stdout.reconfigure(encoding='utf-8', errors='replace')
        except:
            pass
    if sys.stderr and hasattr(sys.stderr, 'reconfigure'):
        try:
            sys.stderr.reconfigure(encoding='utf-8', errors='replace')
        except:
            pass


import json
import re
import time
from pathlib import Path
from datetime import timedelta
from typing import Dict, Any, List, Optional, Tuple, Callable
from dataclasses import dataclass
from enum import Enum
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed

from modules.utils import (
    get_logger,
    parse_srt_file,
    group_srt_into_scenes,
    format_srt_time,
    parse_srt_time,
)
from modules.excel_manager import (
    PromptWorkbook,
    Character,
    Location,
    Scene
)


PROJECT_PREFIX_TO_TOPIC = {
    "TL": "psychology",
    "TH": "finance",
    "KA": "story",
    "TA": "story",
}

TOPIC_TO_REF_DIR = {
    "psychology": "psychology",
    "finance": "finance",
}


def _topic_from_project_code(project_code: str) -> str:
    import re
    m = re.match(r"^([A-Za-z]+)\d*-", str(project_code or "").strip())
    if m:
        return PROJECT_PREFIX_TO_TOPIC.get(m.group(1).upper(), "")
    return ""


def resolve_styled_reference_channel(value: str, project_code: str = "", topic: str = "") -> str:
    """Resolve project codes like TL1-0002 → TL1-T2 or TH1-0003 → TH1-T3."""
    candidates = []
    for item in [value, project_code]:
        item = str(item or "").strip()
        if item and item not in candidates:
            candidates.append(item)
        import re
        m = re.match(r"^([A-Za-z]+\d+)-0*(\d+)$", item, flags=re.IGNORECASE)
        if m:
            mapped = f"{m.group(1).upper()}-T{int(m.group(2))}"
            if mapped not in candidates:
                candidates.append(mapped)
    resolved_topic = topic or _topic_from_project_code(project_code) or _topic_from_project_code(value) or "psychology"
    ref_dir_name = TOPIC_TO_REF_DIR.get(resolved_topic, resolved_topic)
    tool_dir = Path(__file__).resolve().parents[1]
    root = tool_dir / "reference_characters" / ref_dir_name
    for candidate in candidates:
        if (root / candidate / "nv1.png").exists() or (root / candidate / "style.yaml").exists():
            return candidate
    return candidates[0] if candidates else ""


def resolve_psychology_reference_channel(value: str, project_code: str = "") -> str:
    return resolve_styled_reference_channel(value, project_code, "psychology")


# Channel-to-audience language mapping for prompt localisation.
# Hardcoded map serves as fast lookup + fallback. New channels are auto-discovered
# from style.yaml's audience_language field — no code change needed.
CHANNEL_LANGUAGE_MAP = {
    "TL1-T1": "Spanish",
    "TL1-T2": "Vietnamese",
    "TL1-T3": "English",
    "TL1-T4": "French",
    "TL1-T5": "German",
    "TL1-T6": "Portuguese",
    "TL1-T7": "Japanese",
    "TL1-T8": "Korean",
    "TL1-T9": "Italian",
    "TL1-T10": "Turkish",
    "TH1-T1": "Vietnamese",
    "TH1-T2": "English",
    "TH1-T3": "Spanish",
    "TH1-T4": "French",
    "TH1-T5": "German",
    "TH1-T6": "Portuguese",
    "TH1-T7": "Japanese",
    "TH1-T8": "Korean",
    "TH1-T9": "Italian",
    "TH1-T10": "Turkish",
}


def _read_language_from_style_yaml(channel_id: str) -> str:
    """Auto-discover audience_language from a channel's style.yaml.
    This allows adding new channels without editing Python code."""
    try:
        import yaml
        tool_dir = Path(__file__).resolve().parents[1]
        for ref_dir in TOPIC_TO_REF_DIR.values():
            style_path = tool_dir / "reference_characters" / ref_dir / channel_id / "style.yaml"
            if style_path.exists():
                data = yaml.safe_load(style_path.read_text(encoding="utf-8")) or {}
                lang = str(data.get("audience_language", "") or "").strip()
                if lang:
                    return lang
    except Exception:
        pass
    return ""


def get_channel_language(channel: str, project_code: str = "") -> str:
    """Return the audience language name for a given channel or project code.
    Priority: hardcoded map → auto-discover from style.yaml → 'English' default."""
    resolved = resolve_styled_reference_channel(channel or project_code, project_code)
    # 1. Fast lookup from hardcoded map
    lang = CHANNEL_LANGUAGE_MAP.get(resolved, "")
    if lang:
        return lang
    # 2. Try raw keys against hardcoded map
    for key in [channel, project_code]:
        key_upper = str(key or "").strip().upper()
        if key_upper in CHANNEL_LANGUAGE_MAP:
            return CHANNEL_LANGUAGE_MAP[key_upper]
    # 3. Auto-discover from style.yaml (supports new channels with zero code change)
    for candidate in [resolved, channel, project_code]:
        candidate = str(candidate or "").strip()
        if candidate:
            lang = _read_language_from_style_yaml(candidate)
            if lang:
                # Cache for future lookups in this session
                CHANNEL_LANGUAGE_MAP[candidate.upper()] = lang
                return lang
    return "English"  # safe default

# â”€â”€ Prompt Quality Engine (cáº£i tiáº¿n Step 7) â”€â”€
try:
    from modules.prompt_quality import (
        SYSTEM_PROMPT_SCENE_PROMPTS,
        SYSTEM_PROMPT_QA_REVIEW,
        get_scene_system_prompt,
        build_scene_prompt_request,
        build_qa_review_request,
        build_fix_prompt,
        postprocess_img_prompt,
        postprocess_video_prompt,
        build_fallback_prompt,
        prompt_needs_single_frame_fallback,
        check_narration_keywords_in_prompt,
        check_unsupported_prompt_details,
        check_psychology_prompt_quality,
        score_psychology_scene_prompt_pair,
        normalize_psychology_style_profile,
        configure_prompt_quality_ai,
        _strip_forced_psychology_cultural_props,
        estimate_scene_count,
        format_scene_estimate,
    )
    PROMPT_QUALITY_ENABLED = True
except ImportError:
    PROMPT_QUALITY_ENABLED = False


class StepStatus(Enum):
    """Tráº¡ng thÃ¡i cá»§a má»—i step."""
    NOT_STARTED = "not_started"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    FAILED = "failed"


@dataclass
class StepResult:
    """Káº¿t quáº£ cá»§a má»—i step."""
    step_name: str
    status: StepStatus
    message: str = ""
    data: Any = None


class ProgressivePromptsGenerator:
    """
    Generator táº¡o prompts theo tá»«ng step.
    Má»—i step Ä‘á»c context tá»« Excel vÃ  lÆ°u káº¿t quáº£ vÃ o Excel.
    """

    DEEPSEEK_URL = "https://api.deepseek.com/v1/chat/completions"

    def __init__(self, config: dict):
        """
        Args:
            config: Config chá»©a API keys vÃ  settings
        """
        self.config = config
        try:
            from modules.topic_prompts import get_topic_prompts, normalize_topic_key
            requested_topic = normalize_topic_key(config.get("topic", "story"))
            self.topic_prompts = get_topic_prompts(requested_topic)
            self.topic = getattr(self.topic_prompts, "TOPIC_NAME", requested_topic)
        except Exception:
            self.topic = str(config.get("topic", "story") or "story").strip().lower()
            self.topic_prompts = None
        self.config["topic"] = self.topic
        self.logger = get_logger("progressive_prompts")
        self.psychology_style_profile = self._load_psychology_style_profile()
        self.config["psychology_style_profile"] = self.psychology_style_profile
        if self.topic_prompts and hasattr(self.topic_prompts, "set_style_profile"):
            self.topic_prompts.set_style_profile(self.psychology_style_profile)
        self.ai_provider = self._resolve_ai_provider()

        # DeepSeek config
        keys_list = config.get("deepseek_api_keys", [])
        single_key = config.get("deepseek_api_key", "")
        if single_key and single_key.strip():
            keys_list = [single_key] + [k for k in keys_list if k and k.strip() != single_key.strip()]
        self.deepseek_keys = [k for k in keys_list if k and k.strip()]
        self.deepseek_index = 0
        self.deepseek_model = (config.get("deepseek_model", "") or "deepseek-chat").strip()
        self.deepseek_thinking_type = (config.get("deepseek_thinking_type", "") or "").strip()
        if PROMPT_QUALITY_ENABLED and self.deepseek_keys:
            configure_prompt_quality_ai(self.deepseek_keys[0], self.deepseek_model)

        # VOV direct OpenAI-compatible config
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

        # Claude Pool / local tool config
        self.claude_pool_base_url = (config.get("claude_pool_base_url", "") or "").strip().rstrip("/")
        self.claude_pool_api_key = (config.get("claude_pool_api_key", "") or "").strip()
        self.claude_pool_model = (config.get("claude_pool_model", "") or "gpt-5.4-pro").strip()
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

        # Callback for logging
        self.log_callback: Optional[Callable] = None
        self._mixed_provider_lock = threading.Lock()
        self._mixed_provider_seq = 0

        # Test provider config
        self._test_provider()

    @property
    def _is_styled(self) -> bool:
        try:
            from modules.topic_prompts import is_styled_topic
            return is_styled_topic(self.topic)
        except Exception:
            return self.topic in ("psychology", "finance")

    def _load_psychology_style_profile(self) -> Dict[str, str]:
        """Load per-channel style.yaml for styled topics (psychology, finance, etc.)."""
        profile = {}
        if not self._is_styled:
            return normalize_psychology_style_profile(profile) if PROMPT_QUALITY_ENABLED else profile

        explicit = str(self.config.get("psychology_style_file", "") or "").strip()
        channel = resolve_styled_reference_channel(
            self.config.get("reference_channel") or self.config.get("channel") or "",
            self.config.get("project_code") or "",
            self.topic,
        )
        ref_dir_name = TOPIC_TO_REF_DIR.get(self.topic, self.topic)
        tool_dir = Path(__file__).resolve().parents[1]
        candidates = []
        if explicit:
            candidates.append(Path(explicit))
        if channel:
            style_dir = tool_dir / "reference_characters" / ref_dir_name / channel
            candidates.extend([style_dir / "style.yaml", style_dir / "style.yml"])

        loaded_from = ""
        for path in candidates:
            try:
                if path.exists() and path.is_file():
                    import yaml
                    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
                    if isinstance(data, dict):
                        profile.update(data)
                        loaded_from = str(path)
                        break
            except Exception as e:
                self.logger.warning(f"Cannot load style profile {path}: {e}")

        if loaded_from:
            profile.setdefault("style_source", loaded_from)
        inline_profile = self.config.get("psychology_style_profile")
        if isinstance(inline_profile, dict):
            profile.update(inline_profile)
        flat_aliases = {
            "psychology_style_name": "style_name",
            "psychology_style_prompt": "image_style",
            "psychology_image_style": "image_style",
            "psychology_video_style_prompt": "video_style",
            "psychology_video_style": "video_style",
            "psychology_thumbnail_style_prompt": "thumbnail_style",
            "psychology_thumbnail_style": "thumbnail_style",
            "psychology_negative_prompt": "negative_prompt",
        }
        for config_key, profile_key in flat_aliases.items():
            value = str(self.config.get(config_key, "") or "").strip()
            if value:
                profile[profile_key] = value
        profile = normalize_psychology_style_profile(profile) if PROMPT_QUALITY_ENABLED else profile
        if loaded_from:
            self.logger.info(f"Loaded {self.topic} style profile: {profile.get('style_name')} from {loaded_from}")
        return profile

    def _resolve_ai_provider(self) -> str:
        provider = (self.config.get("excel_ai_provider", "") or "").strip().lower()
        if provider in ("deepseek", "deepseek_vov", "claude_pool", "vov_direct"):
            return provider
        if (self.config.get("vov_direct_base_url") or "").strip() or (self.config.get("vov_direct_api_key") or "").strip():
            return "vov_direct"
        if (self.config.get("claude_pool_base_url") or "").strip() or (self.config.get("claude_pool_api_key") or "").strip():
            return "claude_pool"
        return "deepseek"

    def _provider_display_name(self) -> str:
        if self.ai_provider == "claude_pool":
            return "Claude Pool"
        if self.ai_provider == "vov_direct":
            return "VOV Direct"
        if self.ai_provider == "deepseek_vov":
            return "DeepSeek + VOV"
        return "DeepSeek"

    def _vov_direct_candidate_models(self) -> List[str]:
        ordered: List[str] = []
        if (
            self._vov_direct_active_model
            and self._vov_direct_active_model not in self._vov_direct_bad_models
            and self._vov_direct_active_model not in self._vov_direct_demoted_models
        ):
            ordered.append(self._vov_direct_active_model)
        for model in self.vov_direct_model_chain:
            if model in self._vov_direct_bad_models:
                continue
            if model in self._vov_direct_demoted_models:
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
            if model in self._claude_pool_bad_models:
                continue
            if model in self._claude_pool_demoted_models:
                continue
            if model not in ordered:
                ordered.append(model)
        # If everything was demoted, give them another chance in chain order.
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

    def _sync_runtime_config_to_workbook(self, workbook: PromptWorkbook, code: str) -> None:
        """Persist runtime topic/reference metadata so VE3 does not fall back to stale workbook config."""
        if not hasattr(workbook, "workbook") or workbook.workbook is None:
            return
        if "config" not in workbook.workbook.sheetnames:
            ws = workbook.workbook.create_sheet("config")
            ws["A1"] = "key"
            ws["B1"] = "value"
        else:
            ws = workbook.workbook["config"]

        def set_value(key: str, value: str) -> bool:
            value = str(value or "")
            for row in range(2, ws.max_row + 1):
                cell_key = ws.cell(row=row, column=1).value
                if cell_key and str(cell_key).strip().lower() == key.lower():
                    if str(ws.cell(row=row, column=2).value or "") != value:
                        ws.cell(row=row, column=2, value=value)
                        return True
                    return False
            next_row = ws.max_row + 1
            ws.cell(row=next_row, column=1, value=key)
            ws.cell(row=next_row, column=2, value=value)
            return True

        changed = False
        changed |= set_value("topic", self.topic)
        changed |= set_value("project_code", code)
        reference_channel = resolve_styled_reference_channel(self.config.get("reference_channel") or "", code, self.topic)
        changed |= set_value("reference_channel", reference_channel)
        if self._is_styled:
            ref = self.config.get("psychology_reference_image", "") or ""
            changed |= set_value("psychology_reference_image", ref)
            profile = self.psychology_style_profile or {}
            changed |= set_value("psychology_style_name", profile.get("style_name", ""))
            changed |= set_value("psychology_style_source", profile.get("style_source", ""))
            changed |= set_value("psychology_image_style", profile.get("image_style", ""))
            changed |= set_value("psychology_video_style", profile.get("video_style", ""))
            changed |= set_value("psychology_thumbnail_style", profile.get("thumbnail_style", ""))
            changed |= set_value("psychology_negative_prompt", profile.get("negative_prompt", ""))
            changed |= set_value("psychology_style_prompt", profile.get("image_style", ""))
            changed |= set_value("psychology_video_style_prompt", profile.get("video_style", ""))
            changed |= set_value("psychology_thumbnail_style_prompt", profile.get("thumbnail_style", ""))
            changed |= set_value("psychology_audience_language", profile.get("audience_language", ""))
            changed |= set_value("psychology_audience_culture_note", profile.get("audience_culture_note", ""))
            changed |= set_value("psychology_cultural_props", profile.get("cultural_props", ""))
            changed |= set_value("psychology_cultural_metaphors", profile.get("cultural_metaphors", ""))
            changed |= set_value("psychology_cultural_emotion_style", profile.get("cultural_emotion_style", ""))
        if changed:
            workbook.save()

    def _psychology_audience_contract(self, strict: bool = False) -> str:
        profile = self.psychology_style_profile or {}
        language = str(profile.get("audience_language", "") or "").strip()
        if not language:
            return ""
        strict_line = ""
        if strict:
            strict_line = (
                "\nNON-NEGOTIABLE: The narration decides the visual. Use audience-specific settings, props, "
                "rituals, or metaphors only when they make the current spoken idea clearer. Do not add a prop "
                "just to satisfy audience fit."
            )
        return f"""
AUDIENCE INSIGHT BIBLE:
- Audience language: {language}
- Cultural context: {profile.get('audience_culture_note', '')}
- Preferred props/settings/rituals: {profile.get('cultural_props', '')}
- Preferred {self.topic} metaphors: {profile.get('cultural_metaphors', '')}
- Emotional expression style: {profile.get('cultural_emotion_style', '')}
Goal: viewers should understand the spoken {self.topic} idea within one second; cultural fit is optional support, not the subject.{strict_line}
"""

    def _normalize_cultural_text(self, text: str) -> str:
        import re
        import unicodedata

        normalized = unicodedata.normalize("NFKD", str(text or "").lower())
        normalized = "".join(ch for ch in normalized if not unicodedata.combining(ch))
        normalized = re.sub(r"[^a-z0-9]+", " ", normalized)
        return " ".join(normalized.split())

    def _psychology_cultural_terms(self) -> list:
        import re

        profile = self.psychology_style_profile or {}
        raw_parts = []
        for part in re.split(r"[,|:;]", str(profile.get("cultural_props", "") or "")):
            part = part.strip()
            if part:
                raw_parts.append(part)
        for item in str(profile.get("cultural_metaphors", "") or "").split("|"):
            item = item.split(":", 1)[-1] if ":" in item else item
            for part in re.split(r"[,;]", item):
                part = part.strip()
                if part:
                    raw_parts.append(part)
        generic = {
            "anxiety", "stress", "boundary", "connection", "letting", "loneliness",
            "growth", "courage", "acceptance", "forgiveness", "emotion", "feeling",
            "calm", "peace", "people", "character", "silhouette", "symbolic",
            "metaphor", "visual", "clear", "warm", "soft", "simple",
        }
        terms = []
        for part in raw_parts:
            words = [
                w for w in self._normalize_cultural_text(part).split()
                if len(w) >= 3 and w not in generic
            ]
            candidates = []
            if len(words) >= 2:
                candidates.append(" ".join(words[:3]))
                candidates.append(" ".join(words[:2]))
            candidates.extend(words[:2])
            for candidate in candidates:
                if len(candidate) >= 4 and candidate not in terms:
                    terms.append(candidate)
        return terms[:80]

    def _scene_has_psychology_cultural_anchor(self, scene: dict) -> bool:
        profile = self.psychology_style_profile or {}
        if not str(profile.get("audience_language", "") or "").strip():
            return True
        combined = " ".join(str(scene.get(k, "") or "") for k in [
            "visual_moment", "primary_subject", "primary_action", "visual_anchor",
            "viewer_attention", "subtext_delivery", "key_focus",
        ])
        combined_norm = self._normalize_cultural_text(combined)
        return any(term in combined_norm for term in self._psychology_cultural_terms())

    def _select_psychology_cultural_anchor(self, scene: dict) -> str:
        profile = self.psychology_style_profile or {}
        cultural_metaphors = str(profile.get("cultural_metaphors", "") or "")
        cultural_props = str(profile.get("cultural_props", "") or "")
        source = self._normalize_cultural_text(" ".join(str(scene.get(k, "") or "") for k in [
            "srt_text", "visual_moment", "primary_subject", "primary_action", "visual_anchor",
        ]))
        concept_aliases = {
            "anxiety": ["anxiety", "worry", "fear", "nervous", "lo lang", "so hai", "ansiedad", "anxiete", "angst", "ansiedade", "kaygi"],
            "stress": ["stress", "pressure", "overwhelm", "ap luc", "cang thang", "estres", "druck", "pressao", "stres"],
            "boundary": ["boundary", "limit", "say no", "ranh gioi", "gioi han", "limite", "grenze", "sinir"],
            "connection": ["relationship", "connect", "friend", "love", "ket noi", "moi quan he", "relacion", "relation", "beziehung"],
            "letting_go": ["let go", "release", "buong bo", "soltar", "lacher", "loslassen", "birakma"],
            "loneliness": ["lonely", "alone", "isolated", "co don", "soledad", "einsamkeit", "solidao", "yalnizlik"],
            "growth": ["growth", "improve", "change", "goal", "phat trien", "crecimiento", "wachstum", "crescimento", "gelisim"],
            "courage": ["courage", "brave", "dung cam", "coraje", "mut", "coragem", "cesaret"],
            "acceptance": ["accept", "embrace", "chap nhan", "aceptar", "accepter", "akzeptieren", "aceitar", "kabul"],
            "forgiveness": ["forgive", "forgiveness", "tha thu", "perdonar", "pardonner", "vergeben", "perdoar", "affetmek"],
        }
        parsed = {}
        for part in cultural_metaphors.split("|"):
            if ":" not in part:
                continue
            key, visual = part.split(":", 1)
            key = self._normalize_cultural_text(key).replace(" ", "_")
            visual = visual.strip()
            if key and visual:
                parsed[key] = visual
        for concept, aliases in concept_aliases.items():
            if concept in parsed and any(self._normalize_cultural_text(alias) in source for alias in aliases):
                return parsed[concept]
        props = [p.strip() for p in cultural_props.split(",") if p.strip()]
        if props:
            return props[0]
        if parsed:
            return next(iter(parsed.values()))
        return ""

    def _enforce_psychology_cultural_anchor(self, scene: dict) -> dict:
        # DISABLED: Do not force cultural props into every scene
        # Let the SRT content decide what props are needed
        return scene

    def _has_provider_config(self) -> bool:
        if self.ai_provider == "vov_direct":
            return bool(self.vov_direct_base_url and self.vov_direct_api_key and self.vov_direct_model)
        if self.ai_provider == "claude_pool":
            return bool(self.claude_pool_base_url and self.claude_pool_api_key and self.claude_pool_model)
        return bool(self.deepseek_keys)

    def _test_provider(self):
        """Test provider config vÃ  ghi log sá»›m."""
        if not self._has_provider_config():
            if self.ai_provider == "vov_direct" and self._can_fallback_to_claude_pool():
                self._log("  WARNING: VOV Direct not configured or unavailable. Falling back to Claude Pool.", "WARN")
                self.ai_provider = "claude_pool"
                self._test_claude_pool()
                return
            if self.ai_provider == "deepseek":
                if self._can_fallback_to_vov_direct():
                    self._log("  WARNING: DeepSeek not configured. Falling back to VOV Direct.", "WARN")
                    self.ai_provider = "vov_direct"
                    self._test_vov_direct()
                    return
                if self._can_fallback_to_claude_pool():
                    self._log("  WARNING: DeepSeek not configured. Falling back to Claude Pool.", "WARN")
                    self.ai_provider = "claude_pool"
                    self._test_provider()
                    return
            self._log(f"  WARNING: {self._provider_display_name()} is not configured!", "WARN")
            return
        if self.ai_provider == "vov_direct":
            self._test_vov_direct()
            return
        if self.ai_provider == "claude_pool":
            self._test_claude_pool()
            return
        self._test_api_keys()
        if not self.deepseek_keys:
            if self._can_fallback_to_vov_direct():
                self._log("  WARNING: No working DeepSeek keys. Falling back to VOV Direct.", "WARN")
                self.ai_provider = "vov_direct"
                self._test_vov_direct()
            elif self._can_fallback_to_claude_pool():
                self._log("  WARNING: No working DeepSeek keys. Falling back to Claude Pool.", "WARN")
                self.ai_provider = "claude_pool"
                self._test_claude_pool()

    def _can_fallback_to_vov_direct(self) -> bool:
        return bool(self.vov_direct_base_url and self.vov_direct_api_key and self.vov_direct_model)

    def _can_fallback_to_claude_pool(self) -> bool:
        return bool(self.claude_pool_base_url and self.claude_pool_api_key and self.claude_pool_model)

    def _test_api_keys(self):
        """Test API keys vÃ  loáº¡i bá» keys khÃ´ng hoáº¡t Ä‘á»™ng."""
        import requests

        working_keys = []
        for i, key in enumerate(self.deepseek_keys):
            try:
                headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
                data = {
                    "model": self.deepseek_model,
                    "messages": [{"role": "user", "content": "Say OK"}],
                    "max_tokens": 5
                }
                if self.deepseek_thinking_type:
                    data["thinking"] = {"type": self.deepseek_thinking_type}
                resp = requests.post(self.DEEPSEEK_URL, headers=headers, json=data, timeout=15)
                if resp.status_code == 200:
                    working_keys.append(key)
                    self._log(f"  DeepSeek key #{i+1}: OK")
                else:
                    self._log(f"  DeepSeek key #{i+1}: SKIP (status {resp.status_code})")
            except Exception as e:
                self._log(f"  DeepSeek key #{i+1}: SKIP ({e})")

        self.deepseek_keys = working_keys
        if not working_keys:
            self._log("  WARNING: No working API keys!")

    def _test_claude_pool(self):
        import requests

        health_url = f"{self.claude_pool_base_url}/health"
        chat_url = f"{self.claude_pool_base_url}/v1/chat/completions"
        headers = {"Content-Type": "application/json"}
        if self.claude_pool_api_key:
            headers["Authorization"] = f"Bearer {self.claude_pool_api_key}"

        try:
            resp = requests.get(health_url, timeout=10)
            if resp.status_code == 200:
                self._log("  Claude Pool health: OK")
            elif resp.status_code == 404:
                self._log("  Claude Pool health: endpoint not exposed, continuing", "INFO")
            else:
                self._log(f"  Claude Pool health: status {resp.status_code}", "WARN")
        except Exception as e:
            self._log(f"  Claude Pool health: SKIP ({e})", "WARN")

        try:
            data = {
                "model": self.claude_pool_model_chain[0],
                "messages": [{"role": "user", "content": "Say OK"}],
                "max_tokens": 8,
                "temperature": 0,
            }
            resp = requests.post(chat_url, headers=headers, json=data, timeout=20)
            if resp.status_code == 200:
                self._log(f"  Claude Pool model '{self.claude_pool_model_chain[0]}': OK")
            else:
                self._log(f"  Claude Pool model '{self.claude_pool_model_chain[0]}': SKIP (status {resp.status_code})", "WARN")
        except Exception as e:
            self._log(f"  Claude Pool model '{self.claude_pool_model_chain[0]}': SKIP ({e})", "WARN")

    def _test_vov_direct(self):
        import requests

        models_url = f"{self.vov_direct_base_url}/models"
        chat_url = f"{self.vov_direct_base_url}/chat/completions"
        headers = {"Content-Type": "application/json"}
        if self.vov_direct_api_key:
            headers["Authorization"] = f"Bearer {self.vov_direct_api_key}"

        try:
            resp = requests.get(models_url, headers=headers, timeout=15)
            self._log(f"  VOV Direct models: status {resp.status_code}" if resp.status_code != 200 else "  VOV Direct models: OK", "WARN" if resp.status_code != 200 else "INFO")
        except Exception as e:
            self._log(f"  VOV Direct models: SKIP ({e})", "WARN")

        try:
            data = {
                "model": self.vov_direct_model_chain[0],
                "messages": [{"role": "user", "content": "Say OK"}],
                "max_tokens": 8,
                "temperature": 0,
            }
            resp = requests.post(chat_url, headers=headers, json=data, timeout=20)
            if resp.status_code == 200:
                self._log(f"  VOV Direct model '{self.vov_direct_model_chain[0]}': OK")
            else:
                self._log(f"  VOV Direct model '{self.vov_direct_model_chain[0]}': SKIP (status {resp.status_code})", "WARN")
        except Exception as e:
            self._log(f"  VOV Direct model '{self.vov_direct_model_chain[0]}': SKIP ({e})", "WARN")

    def _log(self, msg: str, level: str = "INFO"):
        """Log message."""
        if self.log_callback:
            self.log_callback(msg, level)
        else:
            print(msg)

    def _select_mixed_provider(self) -> str:
        deepseek_slots = max(1, int(self.config.get("deepseek_parallel_slots", 4) or 4))
        vov_slots = max(0, int(self.config.get("vov_direct_parallel_slots", 2) or 2))
        total_slots = deepseek_slots + vov_slots
        if total_slots <= 0:
            return "deepseek"
        with self._mixed_provider_lock:
            slot = self._mixed_provider_seq % total_slots
            self._mixed_provider_seq += 1
        return "vov_direct" if slot >= deepseek_slots else "deepseek"

    def _call_api(
        self,
        prompt: str,
        temperature: float = 0.7,
        max_tokens: int = 8192,
        system_prompt: Optional[str] = None,
    ) -> Optional[str]:
        """
        Gá»i DeepSeek API.
        system_prompt: náº¿u cÃ³ sáº½ gá»­i thÃªm role=system (dÃ¹ng cho Step 7 prompt quality).

        Returns:
            Response text hoáº·c None náº¿u fail
        """
        if self.ai_provider == "vov_direct":
            return self._call_vov_direct_api(
                prompt=prompt,
                temperature=temperature,
                max_tokens=max_tokens,
                system_prompt=system_prompt,
            )
        if self.ai_provider == "claude_pool":
            return self._call_claude_pool_api(
                prompt=prompt,
                temperature=temperature,
                max_tokens=max_tokens,
                system_prompt=system_prompt,
            )

        provider = self._select_mixed_provider() if self.ai_provider == "deepseek_vov" else "deepseek"
        if provider == "vov_direct" and self.vov_direct_base_url and self.vov_direct_api_key:
            result = self._call_vov_direct_api(
                prompt=prompt,
                temperature=temperature,
                max_tokens=max_tokens,
                system_prompt=system_prompt,
            )
            if result:
                return result
            self._log("  VOV Direct unavailable. Falling back to DeepSeek.", "WARN")

        result = self._call_deepseek_api(
            prompt=prompt,
            temperature=temperature,
            max_tokens=max_tokens,
            system_prompt=system_prompt,
        )
        if result:
            return result

        self._log("  DeepSeek unavailable. Falling back to VOV Direct.", "WARN")
        result = self._call_vov_direct_api(
            prompt=prompt,
            temperature=temperature,
            max_tokens=max_tokens,
            system_prompt=system_prompt,
        )
        if result:
            return result

        if self._can_fallback_to_claude_pool():
            self._log("  VOV Direct unavailable. Falling back to Claude Pool.", "WARN")
            return self._call_claude_pool_api(
                prompt=prompt,
                temperature=temperature,
                max_tokens=max_tokens,
                system_prompt=system_prompt,
            )
        return None

    def _call_deepseek_api(
        self,
        prompt: str,
        temperature: float = 0.7,
        max_tokens: int = 8192,
        system_prompt: Optional[str] = None,
    ) -> Optional[str]:
        import random
        import requests

        if not self.deepseek_keys:
            self._log("  ERROR: No DeepSeek API keys available!", "ERROR")
            return None

        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": prompt})

        data = {
            "model": self.deepseek_model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        if self.deepseek_thinking_type:
            data["thinking"] = {"type": self.deepseek_thinking_type}

        attempts_per_key = int(self.config.get("excel_ai_attempts_per_key", self.config.get("api_attempts_per_key", 3)) or 3)
        timeout_seconds = int(self.config.get("excel_ai_timeout_seconds", self.config.get("api_timeout_seconds", 180)) or 180)
        max_attempts = max(6, len(self.deepseek_keys) * attempts_per_key)
        last_error = None

        for attempt in range(max_attempts):
            key = self.deepseek_keys[self.deepseek_index % len(self.deepseek_keys)]
            self.deepseek_index += 1
            headers = {
                "Authorization": f"Bearer {key}",
                "Content-Type": "application/json",
            }

            try:
                resp = requests.post(self.DEEPSEEK_URL, headers=headers, json=data, timeout=timeout_seconds)
                if resp.status_code == 200:
                    try:
                        payload = resp.json()
                    except ValueError as e:
                        last_error = f"Invalid JSON response: {e}"
                        wait_seconds = min(20, 2 ** attempt) + random.uniform(0.2, 1.2)
                        self._log(
                            f"  API invalid JSON response, retrying in {wait_seconds:.1f}s "
                            f"[attempt {attempt + 1}/{max_attempts}]",
                            "WARN",
                        )
                        time.sleep(wait_seconds)
                        continue

                    choices = payload.get("choices") or []
                    if not choices:
                        last_error = "Response missing choices"
                        wait_seconds = min(20, 2 ** attempt) + random.uniform(0.2, 1.2)
                        self._log(
                            f"  API returned no choices, retrying in {wait_seconds:.1f}s "
                            f"[attempt {attempt + 1}/{max_attempts}]",
                            "WARN",
                        )
                        time.sleep(wait_seconds)
                        continue

                    content = (((choices[0] or {}).get("message") or {}).get("content") or "").strip()
                    if not content:
                        last_error = "Empty message content"
                        wait_seconds = min(20, 2 ** attempt) + random.uniform(0.2, 1.2)
                        self._log(
                            f"  API returned empty content, retrying in {wait_seconds:.1f}s "
                            f"[attempt {attempt + 1}/{max_attempts}]",
                            "WARN",
                        )
                        time.sleep(wait_seconds)
                        continue
                    return content

                retry_after = resp.headers.get("Retry-After")
                if resp.status_code == 429:
                    wait_seconds = float(retry_after) if retry_after else min(90, 10 * (attempt + 1))
                    self._log(
                        f"  API rate limit (429), retrying in {wait_seconds:.0f}s "
                        f"[attempt {attempt + 1}/{max_attempts}]",
                        "WARN",
                    )
                    time.sleep(wait_seconds)
                    continue

                if resp.status_code in (408, 409, 500, 502, 503, 504):
                    wait_seconds = min(30, 2 ** attempt) + random.uniform(0.2, 1.2)
                    self._log(
                        f"  API transient error {resp.status_code}, retrying in {wait_seconds:.1f}s "
                        f"[attempt {attempt + 1}/{max_attempts}]",
                        "WARN",
                    )
                    time.sleep(wait_seconds)
                    continue

                if resp.status_code in (401, 402, 403):
                    last_error = f"Auth/key error {resp.status_code}"
                    self._log(
                        f"  API key rejected with {resp.status_code}, trying next key "
                        f"[attempt {attempt + 1}/{max_attempts}]",
                        "WARN",
                    )
                    continue

                if resp.status_code >= 400:
                    last_error = f"{resp.status_code} - {resp.text[:200]}"
                    self._log(f"  API error: {resp.status_code} - {resp.text[:200]}", "ERROR")
                    return None
            except requests.RequestException as e:
                last_error = str(e)
                wait_seconds = min(20, 2 ** attempt) + random.uniform(0.2, 1.2)
                self._log(
                    f"  API exception: {e} - retrying in {wait_seconds:.1f}s "
                    f"[attempt {attempt + 1}/{max_attempts}]",
                    "WARN",
                )
                time.sleep(wait_seconds)

        if last_error:
            self._log(f"  API failed after retries: {last_error}", "ERROR")
        else:
            self._log("  API failed after retries", "ERROR")
        return None

    def _call_vov_direct_api(
        self,
        prompt: str,
        temperature: float = 0.7,
        max_tokens: int = 8192,
        system_prompt: Optional[str] = None,
    ) -> Optional[str]:
        import random
        import requests

        if not (self.vov_direct_base_url and self.vov_direct_api_key and self.vov_direct_model):
            self._log("  ERROR: VOV Direct is not fully configured!", "ERROR")
            if self._can_fallback_to_claude_pool():
                self._log("  VOV Direct unavailable. Falling back to Claude Pool.", "WARN")
                return self._call_claude_pool_api(prompt, temperature=temperature, max_tokens=max_tokens, system_prompt=system_prompt)
            return None

        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": prompt})

        headers = {
            "Authorization": f"Bearer {self.vov_direct_api_key}",
            "Content-Type": "application/json",
        }
        attempts = int(self.config.get("excel_ai_attempts_per_key", self.config.get("api_attempts_per_key", 3)) or 3)
        timeout_seconds = int(self.config.get("excel_ai_timeout_seconds", self.config.get("api_timeout_seconds", 180)) or 180)
        max_attempts = max(3, attempts)
        url = f"{self.vov_direct_base_url}/chat/completions"
        last_error = None

        for model_name in self._vov_direct_candidate_models():
            data = {
                "model": model_name,
                "messages": messages,
                "temperature": temperature,
                "max_tokens": max_tokens,
            }
            if self._vov_direct_active_model == model_name:
                self._log(f"  VOV Direct using sticky model: {model_name}", "INFO")
            else:
                self._log(f"  VOV Direct trying model: {model_name}", "INFO")
            for attempt in range(max_attempts):
                try:
                    resp = requests.post(url, headers=headers, json=data, timeout=timeout_seconds)
                    if resp.status_code == 200:
                        try:
                            payload = resp.json()
                        except ValueError as e:
                            last_error = f"{model_name}: Invalid JSON response: {e}"
                            wait_seconds = min(20, 2 ** attempt) + random.uniform(0.2, 1.2)
                            self._log(
                                f"  VOV Direct invalid JSON response on {model_name}, retrying in {wait_seconds:.1f}s "
                                f"[attempt {attempt + 1}/{max_attempts}]",
                                "WARN",
                            )
                            time.sleep(wait_seconds)
                            continue

                        choices = payload.get("choices") or []
                        if not choices:
                            last_error = f"{model_name}: Response missing choices"
                            wait_seconds = min(20, 2 ** attempt) + random.uniform(0.2, 1.2)
                            self._log(
                                f"  VOV Direct returned no choices on {model_name}, retrying in {wait_seconds:.1f}s "
                                f"[attempt {attempt + 1}/{max_attempts}]",
                                "WARN",
                            )
                            time.sleep(wait_seconds)
                            continue

                        content = (((choices[0] or {}).get("message") or {}).get("content") or "").strip()
                        if not content:
                            last_error = f"{model_name}: Empty message content"
                            wait_seconds = min(20, 2 ** attempt) + random.uniform(0.2, 1.2)
                            self._log(
                                f"  VOV Direct returned empty content on {model_name}, retrying in {wait_seconds:.1f}s "
                                f"[attempt {attempt + 1}/{max_attempts}]",
                                "WARN",
                            )
                            time.sleep(wait_seconds)
                            continue
                        self._mark_vov_direct_success(model_name)
                        return content

                    retry_after = resp.headers.get("Retry-After")
                    if resp.status_code == 429:
                        demoted = self._mark_vov_direct_transient_failure(model_name)
                        wait_seconds = float(retry_after) if retry_after else min(90, 10 * (attempt + 1))
                        self._log(
                            f"  VOV Direct rate limit on {model_name} (429), retrying in {wait_seconds:.0f}s "
                            f"[attempt {attempt + 1}/{max_attempts}]",
                            "WARN",
                        )
                        if demoted:
                            self._log(f"  VOV Direct demoting model {model_name} after repeated rate limits; switching to next model", "WARN")
                            break
                        time.sleep(wait_seconds)
                        continue

                    if resp.status_code in (408, 409, 500, 502, 503, 504):
                        demoted = self._mark_vov_direct_transient_failure(model_name)
                        wait_seconds = min(30, 2 ** attempt) + random.uniform(0.2, 1.2)
                        self._log(
                            f"  VOV Direct transient error {resp.status_code} on {model_name}, retrying in {wait_seconds:.1f}s "
                            f"[attempt {attempt + 1}/{max_attempts}]",
                            "WARN",
                        )
                        if demoted:
                            self._log(f"  VOV Direct demoting model {model_name} after repeated transient errors; switching to next model", "WARN")
                            break
                        time.sleep(wait_seconds)
                        continue

                    if resp.status_code in (400, 401, 402, 403, 404):
                        last_error = f"{model_name}: Auth/key/quota/model error {resp.status_code}"
                        self._vov_direct_bad_models.add(model_name)
                        if self._vov_direct_active_model == model_name:
                            self._vov_direct_active_model = None
                        self._log(f"  VOV Direct model {model_name} returned {resp.status_code}, switching model", "WARN")
                        break

                    if resp.status_code >= 400:
                        last_error = f"{model_name}: {resp.status_code} - {resp.text[:200]}"
                        self._vov_direct_bad_models.add(model_name)
                        if self._vov_direct_active_model == model_name:
                            self._vov_direct_active_model = None
                        self._log(f"  VOV Direct model {model_name} error: {resp.status_code} - {resp.text[:200]}", "WARN")
                        break
                except requests.RequestException as e:
                    last_error = f"{model_name}: {e}"
                    demoted = self._mark_vov_direct_transient_failure(model_name)
                    wait_seconds = min(20, 2 ** attempt) + random.uniform(0.2, 1.2)
                    self._log(
                        f"  VOV Direct exception on {model_name}: {e} - retrying in {wait_seconds:.1f}s "
                        f"[attempt {attempt + 1}/{max_attempts}]",
                        "WARN",
                    )
                    if demoted:
                        self._log(f"  VOV Direct demoting model {model_name} after repeated exceptions; switching to next model", "WARN")
                        break
                    time.sleep(wait_seconds)

            if self._vov_direct_active_model != model_name:
                self._vov_direct_bad_models.add(model_name)
                self._log(f"  VOV Direct switching away from model: {model_name}", "WARN")

        if self._can_fallback_to_claude_pool():
            self._log("  VOV Direct exhausted. Falling back to Claude Pool GPT chain.", "WARN")
            return self._call_claude_pool_api(prompt, temperature=temperature, max_tokens=max_tokens, system_prompt=system_prompt)

        if last_error:
            self._log(f"  VOV Direct failed after model chain retries: {last_error}", "ERROR")
        else:
            self._log("  VOV Direct failed after model chain retries", "ERROR")
        return None

    def _call_claude_pool_api(
        self,
        prompt: str,
        temperature: float = 0.7,
        max_tokens: int = 8192,
        system_prompt: Optional[str] = None,
    ) -> Optional[str]:
        import random
        import requests

        if not (self.claude_pool_base_url and self.claude_pool_api_key and self.claude_pool_model):
            self._log("  ERROR: Claude Pool is not fully configured!", "ERROR")
            return None

        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": prompt})

        headers = {
            "Authorization": f"Bearer {self.claude_pool_api_key}",
            "Content-Type": "application/json",
        }
        attempts = int(self.config.get("excel_ai_attempts_per_key", self.config.get("api_attempts_per_key", 3)) or 3)
        timeout_seconds = int(self.config.get("excel_ai_timeout_seconds", self.config.get("api_timeout_seconds", 180)) or 180)
        max_attempts = max(3, attempts)
        url = f"{self.claude_pool_base_url}/v1/chat/completions"
        last_error = None

        for model_name in self._claude_pool_candidate_models():
            data = {
                "model": model_name,
                "messages": messages,
                "temperature": temperature,
                "max_tokens": max_tokens,
            }
            if model_name in self._claude_pool_stream_required_models:
                data["stream"] = True
            if self._claude_pool_active_model == model_name:
                self._log(f"  Claude Pool using sticky model: {model_name}", "INFO")
            else:
                self._log(f"  Claude Pool trying model: {model_name}", "INFO")
            for attempt in range(max_attempts):
                try:
                    resp = requests.post(url, headers=headers, json=data, timeout=timeout_seconds)
                    if resp.status_code == 200:
                        ctype = str(resp.headers.get("Content-Type", "") or "").lower()
                        if "text/event-stream" in ctype:
                            content = self._read_sse_chat_content(resp)
                            if content:
                                self._mark_claude_pool_success(model_name)
                                return content
                            last_error = f"{model_name}: Empty SSE content"
                            wait_seconds = min(20, 2 ** attempt) + random.uniform(0.2, 1.2)
                            self._log(
                                f"  Claude Pool returned empty SSE content on {model_name}, retrying in {wait_seconds:.1f}s "
                                f"[attempt {attempt + 1}/{max_attempts}]",
                                "WARN",
                            )
                            time.sleep(wait_seconds)
                            continue
                        try:
                            payload = resp.json()
                        except ValueError as e:
                            last_error = f"{model_name}: Invalid JSON response: {e}"
                            wait_seconds = min(20, 2 ** attempt) + random.uniform(0.2, 1.2)
                            self._log(
                                f"  Claude Pool invalid JSON response on {model_name}, retrying in {wait_seconds:.1f}s "
                                f"[attempt {attempt + 1}/{max_attempts}]",
                                "WARN",
                            )
                            time.sleep(wait_seconds)
                            continue

                        choices = payload.get("choices") or []
                        if not choices:
                            last_error = f"{model_name}: Response missing choices"
                            wait_seconds = min(20, 2 ** attempt) + random.uniform(0.2, 1.2)
                            self._log(
                                f"  Claude Pool returned no choices on {model_name}, retrying in {wait_seconds:.1f}s "
                                f"[attempt {attempt + 1}/{max_attempts}]",
                                "WARN",
                            )
                            time.sleep(wait_seconds)
                            continue

                        content = (((choices[0] or {}).get("message") or {}).get("content") or "").strip()
                        if not content:
                            last_error = f"{model_name}: Empty message content"
                            wait_seconds = min(20, 2 ** attempt) + random.uniform(0.2, 1.2)
                            self._log(
                                f"  Claude Pool returned empty content on {model_name}, retrying in {wait_seconds:.1f}s "
                                f"[attempt {attempt + 1}/{max_attempts}]",
                                "WARN",
                            )
                            time.sleep(wait_seconds)
                            continue
                        self._mark_claude_pool_success(model_name)
                        return content

                    retry_after = resp.headers.get("Retry-After")
                    if resp.status_code == 429:
                        demoted = self._mark_claude_pool_transient_failure(model_name)
                        wait_seconds = float(retry_after) if retry_after else min(90, 10 * (attempt + 1))
                        self._log(
                            f"  Claude Pool rate limit on {model_name} (429), retrying in {wait_seconds:.0f}s "
                            f"[attempt {attempt + 1}/{max_attempts}]",
                            "WARN",
                        )
                        if demoted:
                            self._log(
                                f"  Claude Pool demoting model {model_name} after repeated rate limits; switching to next model",
                                "WARN",
                            )
                            break
                        time.sleep(wait_seconds)
                        continue

                    if resp.status_code in (408, 409, 500, 502, 503, 504):
                        demoted = self._mark_claude_pool_transient_failure(model_name)
                        wait_seconds = min(30, 2 ** attempt) + random.uniform(0.2, 1.2)
                        self._log(
                            f"  Claude Pool transient error {resp.status_code} on {model_name}, retrying in {wait_seconds:.1f}s "
                            f"[attempt {attempt + 1}/{max_attempts}]",
                            "WARN",
                        )
                        if demoted:
                            self._log(
                                f"  Claude Pool demoting model {model_name} after repeated transient errors; switching to next model",
                                "WARN",
                            )
                            break
                        time.sleep(wait_seconds)
                        continue

                    if resp.status_code in (400, 401, 402, 403):
                        body_text = (resp.text or "")[:300]
                        if resp.status_code == 400 and "stream must be set to true" in body_text.lower():
                            self._claude_pool_stream_required_models.add(model_name)
                            data["stream"] = True
                            self._log(f"  Claude Pool model {model_name} requires stream=true, retrying", "WARN")
                            continue
                        last_error = f"{model_name}: Auth/key/quota error {resp.status_code}"
                        self._claude_pool_bad_models.add(model_name)
                        if self._claude_pool_active_model == model_name:
                            self._claude_pool_active_model = None
                        self._log(f"  Claude Pool model {model_name} returned {resp.status_code}, switching model", "WARN")
                        break

                    if resp.status_code >= 400:
                        last_error = f"{model_name}: {resp.status_code} - {resp.text[:200]}"
                        self._claude_pool_bad_models.add(model_name)
                        if self._claude_pool_active_model == model_name:
                            self._claude_pool_active_model = None
                        self._log(f"  Claude Pool model {model_name} error: {resp.status_code} - {resp.text[:200]}", "WARN")
                        break
                except requests.RequestException as e:
                    last_error = f"{model_name}: {e}"
                    demoted = self._mark_claude_pool_transient_failure(model_name)
                    wait_seconds = min(20, 2 ** attempt) + random.uniform(0.2, 1.2)
                    self._log(
                        f"  Claude Pool exception on {model_name}: {e} - retrying in {wait_seconds:.1f}s "
                        f"[attempt {attempt + 1}/{max_attempts}]",
                        "WARN",
                    )
                    if demoted:
                        self._log(
                            f"  Claude Pool demoting model {model_name} after repeated exceptions; switching to next model",
                            "WARN",
                        )
                        break
                    time.sleep(wait_seconds)

            if self._claude_pool_active_model != model_name:
                self._claude_pool_bad_models.add(model_name)
                self._log(f"  Claude Pool switching away from model: {model_name}", "WARN")

        if last_error:
            self._log(f"  Claude Pool failed after model chain retries: {last_error}", "ERROR")
        else:
            self._log("  Claude Pool failed after model chain retries", "ERROR")
        return None

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

    def _extract_json(self, text: str) -> Optional[dict]:
        """Extract JSON tá»« response text - vá»›i repair cho truncated JSON."""
        import re

        if not text:
            return None

        # Loáº¡i bá» <think>...</think> tags (DeepSeek)
        text = re.sub(r'<think>.*?</think>', '', text, flags=re.DOTALL)

        # Thá»­ parse trá»±c tiáº¿p
        try:
            return json.loads(text.strip())
        except:
            pass

        # TÃ¬m JSON trong code block
        match = re.search(r'```(?:json)?\s*([\s\S]*?)\s*```', text)
        if match:
            try:
                return json.loads(match.group(1))
            except:
                # Thá»­ repair
                repaired = self._repair_truncated_json(match.group(1))
                if repaired:
                    try:
                        return json.loads(repaired)
                    except:
                        pass

        # TÃ¬m JSON object
        match = re.search(r'\{[\s\S]*\}', text)
        if match:
            json_str = match.group(0)
            try:
                return json.loads(json_str)
            except:
                # Thá»­ repair truncated JSON
                repaired = self._repair_truncated_json(json_str)
                if repaired:
                    try:
                        return json.loads(repaired)
                    except:
                        pass

        # TÃ¬m JSON báº¯t Ä‘áº§u báº±ng { nhÆ°ng cÃ³ thá»ƒ bá»‹ cáº¯t cuá»‘i
        start_idx = text.find('{')
        if start_idx != -1:
            json_str = text[start_idx:]
            repaired = self._repair_truncated_json(json_str)
            if repaired:
                try:
                    return json.loads(repaired)
                except:
                    pass

        return None

    def _sanitize_location_reference_prompt(
        self,
        prompt: str,
        char_names: Optional[List[str]] = None,
    ) -> str:
        """
        Ã‰p location prompt thÃ nh reference plate cá»§a bá»‘i cáº£nh:
        - KhÃ´ng cÃ³ ngÆ°á»i / nhÃ¢n váº­t / silhouette
        - Chá»‰ mÃ´ táº£ khÃ´ng gian, kiáº¿n trÃºc, Ä‘áº¡o cá»¥, Ã¡nh sÃ¡ng
        """
        import re

        if not prompt:
            return ""

        cleaned = " ".join(str(prompt).split())

        for name in (char_names or []):
            if name:
                cleaned = re.sub(rf"\b{re.escape(name)}\b", "the subject", cleaned, flags=re.IGNORECASE)

        negative_rules = (
            " Empty environment reference plate only, no people, no person, no character, "
            "no human figure, no crowd, no portrait, no face, no body, no silhouette."
        )

        lowered = cleaned.lower()
        if "no people" not in lowered and "empty environment" not in lowered:
            cleaned = cleaned.rstrip(". ") + "." + negative_rules

        return cleaned

    def _sanitize_character_reference_text(self, text: str) -> str:
        """
        Loáº¡i bá» cÃ¡c mÃ´ táº£ dá»… bá»‹ block á»Ÿ prompt tham chiáº¿u nhÃ¢n váº­t:
        - so sÃ¡nh giá»‘ng ngÆ°á»i ná»•i tiáº¿ng / public figure
        - phrases kiá»ƒu "similar features to X", "looks like X", "resembling X"
        """
        import re

        if not text:
            return ""

        cleaned = " ".join(str(text).split())

        likeness_patterns = [
            r"\bwith\s+similar\s+features\s+to\s+[^,.;|]+",
            r"\bsimilar\s+features\s+to\s+[^,.;|]+",
            r"\blooks?\s+like\s+[^,.;|]+",
            r"\blooks?\s+similar\s+to\s+[^,.;|]+",
            r"\bresembling\s+[^,.;|]+",
            r"\bstyled\s+like\s+[^,.;|]+",
            r"\bin\s+the\s+likeness\s+of\s+[^,.;|]+",
            r"\binspired\s+by\s+[^,.;|]+",
            r"\bmodeled\s+after\s+[^,.;|]+",
            r"\blookalike\s+of\s+[^,.;|]+",
            r"\bface\s+similar\s+to\s+[^,.;|]+",
            r"\bpublic\s+figure\s+look\b",
            r"\bcelebrity\s+look\b",
            r"\bcelebrity\s+features\b",
        ]

        for pattern in likeness_patterns:
            cleaned = re.sub(pattern, "", cleaned, flags=re.IGNORECASE)

        # Dá»n pháº§n thá»«a sau khi xÃ³a phrase.
        cleaned = re.sub(r"\s+,", ",", cleaned)
        cleaned = re.sub(r"\s+\|", " |", cleaned)
        cleaned = re.sub(r"\(\s*\)", "", cleaned)
        cleaned = re.sub(r"\s{2,}", " ", cleaned).strip(" ,.;|")

        return cleaned

    def _repair_truncated_json(self, json_str: str) -> Optional[str]:
        """Repair JSON bá»‹ truncated (thiáº¿u closing brackets)."""
        if not json_str:
            return None

        # Äáº¿m brackets
        open_braces = json_str.count('{')
        close_braces = json_str.count('}')
        open_brackets = json_str.count('[')
        close_brackets = json_str.count(']')

        # Náº¿u balanced thÃ¬ return nguyÃªn
        if open_braces == close_braces and open_brackets == close_brackets:
            return json_str

        # Náº¿u cÃ³ nhiá»u close hÆ¡n open -> JSON khÃ´ng valid
        if close_braces > open_braces or close_brackets > open_brackets:
            return None

        # Cáº¯t bá» pháº§n dá»Ÿ dang cuá»‘i vÃ  thÃªm closing brackets
        # TÃ¬m vá»‹ trÃ­ cuá»‘i cÃ¹ng cÃ³ thá»ƒ lÃ  káº¿t thÃºc há»£p lá»‡
        for i in range(len(json_str) - 1, max(0, len(json_str) - 200), -1):
            char = json_str[i]
            if char in '}]"':
                test_str = json_str[:i+1]
                # Äáº¿m láº¡i
                ob = test_str.count('{')
                cb = test_str.count('}')
                oB = test_str.count('[')
                cB = test_str.count(']')
                # ThÃªm closing cáº§n thiáº¿t
                suffix = ']' * max(0, oB - cB) + '}' * max(0, ob - cb)
                repaired = test_str + suffix
                try:
                    json.loads(repaired)
                    return repaired
                except:
                    continue

        # Fallback: ThÃªm closing brackets Ä‘Æ¡n giáº£n
        suffix = ']' * max(0, open_brackets - close_brackets)
        suffix += '}' * max(0, open_braces - close_braces)
        return json_str + suffix

    def _sample_text(self, text: str, total_chars: int = 10000) -> str:
        """
        Láº¥y máº«u text thÃ´ng minh: Ä‘áº§u + giá»¯a + cuá»‘i.
        Thay vÃ¬ gá»­i 15-20k chars, chá»‰ gá»­i ~8k nhÆ°ng bao phá»§ toÃ n bá»™ ná»™i dung.

        Args:
            text: Full text
            total_chars: Tá»•ng sá»‘ kÃ½ tá»± muá»‘n láº¥y (default 8000)

        Returns:
            Sampled text vá»›i markers [BEGINNING], [MIDDLE], [END]
        """
        if len(text) <= total_chars:
            return text

        # Chia tá»· lá»‡: 40% Ä‘áº§u, 30% giá»¯a, 30% cuá»‘i
        begin_chars = int(total_chars * 0.30)
        middle_chars = int(total_chars * 0.40)
        end_chars = int(total_chars * 0.3)

        # Láº¥y pháº§n Ä‘áº§u
        begin_text = text[:begin_chars]

        # Láº¥y pháº§n giá»¯a (tá»« khoáº£ng 40% Ä‘áº¿n 60% cá»§a text)
        middle_start = len(text) // 2 - middle_chars // 2
        middle_text = text[middle_start:middle_start + middle_chars]

        # Láº¥y pháº§n cuá»‘i
        end_text = text[-end_chars:]

        sampled = f"""[BEGINNING - First {begin_chars} chars]
{begin_text}

[MIDDLE - Around center of story]
{middle_text}

[END - Last {end_chars} chars]
{end_text}"""

        return sampled

    def _load_suite_config_json(self) -> Dict[str, Any]:
        """Load config/config.json from suite root if available."""
        try:
            suite_root = Path(__file__).resolve().parents[3]
            cfg_path = suite_root / "config" / "config.json"
            if not cfg_path.exists():
                return {}
            return json.loads(cfg_path.read_text(encoding="utf-8"))
        except Exception:
            return {}

    def _open_spreadsheet(self, gc, spreadsheet_name_or_url: str):
        """Open spreadsheet by URL/key/name."""
        raw = str(spreadsheet_name_or_url or "").strip()
        if not raw:
            raise ValueError("Missing spreadsheet name")

        if "docs.google.com/spreadsheets/d/" in raw:
            key = raw.split("/d/")[1].split("/")[0]
            return gc.open_by_key(key)
        if len(raw) >= 30 and "/" not in raw and " " not in raw:
            # Likely spreadsheet key
            return gc.open_by_key(raw)
        return gc.open(raw)

    def _fetch_thumbnail_sheet_context(self, code: str) -> Dict[str, str]:
        """
        Fetch title + thumb text from Google Sheet INPUT:
          - Col A: code
          - Col T: title
          - Col U: text thumb
        Retry on failures.
        """
        cfg = self._load_suite_config_json()
        if not cfg:
            return {"title": "", "text_thumb": "", "source": "no_config"}

        spreadsheet_name = (
            cfg.get("SPREADSHEET_NAME")
            or cfg.get("sheet_name")
            or ""
        )
        worksheet_name = cfg.get("SHEET_NAME", "INPUT") or "INPUT"
        cred_name = (
            cfg.get("CREDENTIAL_PATH")
            or cfg.get("SERVICE_ACCOUNT_JSON")
            or "creds.json"
        )

        suite_root = Path(__file__).resolve().parents[3]
        cred_path = Path(cred_name)
        if not cred_path.is_absolute():
            cred_path = suite_root / "config" / cred_name

        if not cred_path.exists():
            self._log(f"  [WARN] Missing Google creds file: {cred_path}", "WARN")
            return {"title": "", "text_thumb": "", "source": "missing_creds"}
        if not spreadsheet_name:
            self._log("  [WARN] Missing SPREADSHEET_NAME in config/config.json", "WARN")
            return {"title": "", "text_thumb": "", "source": "missing_sheet_name"}

        max_retries = int(cfg.get("sheet_retry_count", 8) or 8)
        sleep_seconds = float(cfg.get("sheet_retry_delay_seconds", 2.5) or 2.5)

        scopes = [
            "https://www.googleapis.com/auth/spreadsheets.readonly",
            "https://www.googleapis.com/auth/drive.readonly",
        ]

        last_error = ""
        for attempt in range(1, max_retries + 1):
            try:
                import gspread
                from google.oauth2.service_account import Credentials

                creds = Credentials.from_service_account_file(str(cred_path), scopes=scopes)
                gc = gspread.authorize(creds)
                sh = self._open_spreadsheet(gc, spreadsheet_name)
                ws = sh.worksheet(worksheet_name)
                values = ws.get_all_values()

                target = str(code or "").strip().upper()
                for row in values[1:]:
                    if not row:
                        continue
                    row_code = str(row[0] if len(row) > 0 else "").strip().upper()
                    if row_code != target:
                        continue
                    title = str(row[19] if len(row) > 19 else "").strip()      # Col T
                    text_thumb = str(row[20] if len(row) > 20 else "").strip() # Col U
                    self._log(f"  [SHEET] Loaded INPUT row for {target}: title={bool(title)}, text_thumb={bool(text_thumb)}")
                    return {"title": title, "text_thumb": text_thumb, "source": "sheet"}

                self._log(f"  [SHEET] Code {target} not found in {worksheet_name}!A")
                return {"title": "", "text_thumb": "", "source": "not_found"}
            except Exception as e:
                last_error = f"{type(e).__name__}: {e}"
                if attempt < max_retries:
                    self._log(
                        f"  [SHEET] Read failed ({attempt}/{max_retries}) -> retry in {sleep_seconds:.1f}s: {last_error}",
                        "WARN",
                    )
                    time.sleep(sleep_seconds)
                else:
                    self._log(f"  [SHEET] Read failed after {max_retries} retries: {last_error}", "ERROR")

        return {"title": "", "text_thumb": "", "source": "error"}

    def _get_srt_for_range(self, srt_entries: list, start_idx: int, end_idx: int) -> str:
        """
        Láº¥y SRT text cho má»™t range cá»¥ thá»ƒ.

        Args:
            srt_entries: List of SRT entries
            start_idx: 1-based start index
            end_idx: 1-based end index

        Returns:
            Formatted SRT text
        """
        srt_text = ""
        for i, entry in enumerate(srt_entries, 1):
            if start_idx <= i <= end_idx:
                srt_text += f"[{i}] {entry.start_time} --> {entry.end_time}\n{entry.text}\n\n"
        return srt_text

    def _srt_time_to_seconds(self, ts) -> float:
        """Convert SRT timestamp/timedelta to seconds."""
        if hasattr(ts, "total_seconds"):
            return float(ts.total_seconds())
        parts = str(ts).replace(",", ":").split(":")
        if len(parts) != 4:
            raise ValueError(f"Invalid SRT timestamp: {ts}")
        return int(parts[0]) * 3600 + int(parts[1]) * 60 + int(parts[2]) + int(parts[3]) / 1000.0

    def _build_srt_scene_units(
        self,
        seg_entries: list,
        global_start_index: int,
        min_dur: float,
        max_dur: float,
        target_dur: float,
    ) -> list:
        """
        Deterministically split contiguous SRT entries into scene units.
        AI will only enrich these units, never redefine them.
        """
        if not seg_entries:
            return []

        units = []
        current = []

        def current_duration(entries_subset):
            if not entries_subset:
                return 0.0
            return self._srt_time_to_seconds(entries_subset[-1].end_time) - self._srt_time_to_seconds(entries_subset[0].start_time)

        def finalize(entries_subset):
            if not entries_subset:
                return
            start_local = seg_entries.index(entries_subset[0])
            end_local = seg_entries.index(entries_subset[-1])
            indices = list(range(global_start_index + start_local, global_start_index + end_local + 1))
            units.append({
                "scene_id": len(units) + 1,
                "srt_indices": indices,
                "srt_start": entries_subset[0].start_time,
                "srt_end": entries_subset[-1].end_time,
                "duration": round(current_duration(entries_subset), 2),
                "srt_text": " ".join(str(e.text).strip() for e in entries_subset),
                "visual_moment": "",
                "characters_used": "",
                "location_used": "",
                "camera": "Medium shot",
                "lighting": "Natural lighting",
            })

        for i, entry in enumerate(seg_entries):
            current.append(entry)
            dur = current_duration(current)
            next_dur = None
            if i + 1 < len(seg_entries):
                next_dur = self._srt_time_to_seconds(seg_entries[i + 1].end_time) - self._srt_time_to_seconds(current[0].start_time)

            if dur < min_dur:
                continue

            if dur <= max_dur:
                if next_dur is not None and next_dur <= max_dur and abs(next_dur - target_dur) < abs(dur - target_dur):
                    continue
                finalize(current)
                current = []
                continue

            # Over max duration.
            if len(current) == 1:
                finalize(current)
                current = []
            else:
                overflow = current.pop()
                finalize(current)
                current = [overflow]

        if current:
            # Merge very short tail into previous when possible.
            if units and current_duration(current) < min_dur:
                prev_indices = units[-1]["srt_indices"]
                prev_start = prev_indices[0]
                prev_end = prev_indices[-1]
                prev_entries = [seg_entries[idx - global_start_index] for idx in range(prev_start, prev_end + 1)]
                merged = prev_entries + current
                if current_duration(merged) <= max_dur:
                    units.pop()
                    finalize(merged)
                else:
                    finalize(current)
            else:
                finalize(current)

        # Re-number local scene ids cleanly.
        for i, unit in enumerate(units, 1):
            unit["scene_id"] = i
        return units

    def _align_scenes_with_source_srt(self, scenes: list, srt_entries: list) -> tuple[int, int]:
        """
        Äá»“ng bá»™ láº¡i srt_indices / srt_text / srt_start / srt_end tá»« file SRT nguá»“n.

        Æ¯u tiÃªn:
        1. DÃ¹ng srt_indices náº¿u há»£p lá»‡
        2. Náº¿u thiáº¿u/sai, match chÃ­nh xÃ¡c srt_text theo thá»© tá»± scene trong story

        Returns:
            (aligned_count, warning_count)
        """
        if not scenes or not srt_entries:
            return (0, 0)

        srt_map = {i + 1: e for i, e in enumerate(srt_entries)}
        aligned = 0
        warned = 0
        search_cursor = 1
        max_span = 6

        for scene in scenes:
            raw_indices = scene.get("srt_indices") or []
            valid_indices = [int(i) for i in raw_indices if int(i) in srt_map] if isinstance(raw_indices, list) else []

            matched_entries = [srt_map[i] for i in valid_indices] if valid_indices else []

            # Fallback: exact sequential match against source SRT text.
            if not matched_entries:
                scene_text = " ".join(str(scene.get("srt_text") or "").split())
                if scene_text:
                    best = None
                    upper = min(len(srt_entries), search_cursor + 20)
                    for start in range(search_cursor, upper + 1):
                        concat = ""
                        for end in range(start, min(start + max_span - 1, len(srt_entries)) + 1):
                            concat = (concat + " " + str(srt_map[end].text).strip()).strip()
                            if concat == scene_text:
                                best = list(range(start, end + 1))
                                break
                        if best:
                            break

                    if best:
                        valid_indices = best
                        matched_entries = [srt_map[i] for i in best]
                        scene["srt_indices"] = best

            if matched_entries:
                scene["srt_text"] = " ".join(str(e.text).strip() for e in matched_entries)
                scene["srt_start"] = matched_entries[0].start_time
                scene["srt_end"] = matched_entries[-1].end_time
                search_cursor = valid_indices[-1] + 1 if valid_indices else search_cursor
                aligned += 1
            else:
                warned += 1

        return (aligned, warned)

    def _normalize_character_ids(self, characters_used, valid_char_ids: set) -> str:
        """
        Normalize character IDs tá»« API response vá» format chuáº©n (nv_xxx).

        Váº¥n Ä‘á»: API cÃ³ thá»ƒ tráº£ vá» "john, mary" thay vÃ¬ "nv_john, nv_mary"
        Giáº£i phÃ¡p: Map vá» IDs Ä‘Ã£ biáº¿t trong valid_char_ids

        Args:
            characters_used: String tá»« API nhÆ° "john, mary" hoáº·c "nv_john"
            valid_char_ids: Set of valid IDs nhÆ° {"nv_john", "nv_mary", "loc_office"}

        Returns:
            Normalized string nhÆ° "nv_john, nv_mary"
        """
        if self.topic_prompts:
            normalized_by_topic = self.topic_prompts.normalize_scene_characters(characters_used)
            if normalized_by_topic != characters_used:
                return normalized_by_topic
        if characters_used in ("[]", [], None):
            return ""
        if not characters_used or not valid_char_ids:
            return characters_used

        import re
        raw_ids = []

        if isinstance(characters_used, list):
            tokens = []
            for item in characters_used:
                if isinstance(item, str):
                    tokens.append(item)
                elif isinstance(item, dict):
                    for key in ("id", "character_id", "name"):
                        value = item.get(key)
                        if value:
                            tokens.append(str(value))
                            break
                elif item is not None:
                    tokens.append(str(item))
        else:
            tokens = str(characters_used).split(",")

        # Strip parenthetical annotations: "nv1 (in photo)" -> "nv1"
        # Also strip qualifiers like "(off-screen)", "(mentioned)", "(voice only)"
        for token in tokens:
            token = token.strip()
            token = re.sub(r'\s*\(.*?\)', '', token).strip()
            if token and token != "[]":
                raw_ids.append(token)

        normalized = []

        # Build lookup (lowercase -> original)
        id_lookup = {cid.lower(): cid for cid in valid_char_ids}
        # Also add versions without prefix
        for cid in list(valid_char_ids):
            if cid.startswith("nv_"):
                id_lookup[cid[3:].lower()] = cid  # "john" -> "nv_john"
            if cid.startswith("loc_"):
                id_lookup[cid[4:].lower()] = cid  # "office" -> "loc_office"

        for raw_id in raw_ids:
            raw_lower = raw_id.lower()

            # TÃ¬m trong lookup
            if raw_lower in id_lookup:
                normalized.append(id_lookup[raw_lower])
            elif raw_id in valid_char_ids:
                normalized.append(raw_id)
            elif f"nv_{raw_id}" in valid_char_ids:
                normalized.append(f"nv_{raw_id}")
            else:
                # KhÃ´ng tÃ¬m tháº¥y - giá»¯ nguyÃªn nhÆ°ng thÃªm nv_ prefix náº¿u chÆ°a cÃ³
                if not raw_id.startswith("nv_") and not raw_id.startswith("loc_"):
                    normalized.append(f"nv_{raw_id}")
                else:
                    normalized.append(raw_id)

        return ", ".join(normalized)

    def _normalize_location_id(self, location_used, valid_loc_ids: set) -> str:
        """
        Normalize location ID tá»« API response vá» format chuáº©n (loc_xxx).

        Args:
            location_used: String tá»« API nhÆ° "office" hoáº·c "loc_office"
            valid_loc_ids: Set of valid location IDs

        Returns:
            Normalized ID nhÆ° "loc_office"
        """
        if not location_used or not valid_loc_ids:
            return location_used

        if isinstance(location_used, list):
            if not location_used:
                return ""
            first = location_used[0]
            if isinstance(first, dict):
                for key in ("id", "location_id", "name"):
                    if first.get(key):
                        raw_id = str(first.get(key)).strip()
                        break
                else:
                    raw_id = str(first).strip()
            else:
                raw_id = str(first).strip()
        elif isinstance(location_used, dict):
            raw_id = str(
                location_used.get("id")
                or location_used.get("location_id")
                or location_used.get("name")
                or ""
            ).strip()
        else:
            raw_id = str(location_used).strip()
            if "," in raw_id:
                parts = [p.strip() for p in raw_id.split(",") if p.strip()]
                if parts:
                    for part in parts:
                        part_lower = part.lower()
                        if part in valid_loc_ids:
                            raw_id = part
                            break
                        if part_lower in {lid.lower() for lid in valid_loc_ids}:
                            raw_id = next(lid for lid in valid_loc_ids if lid.lower() == part_lower)
                            break
                    else:
                        raw_id = parts[0]

        if not raw_id:
            return ""
        raw_lower = raw_id.lower()

        # Build lookup
        id_lookup = {lid.lower(): lid for lid in valid_loc_ids}
        for lid in list(valid_loc_ids):
            if lid.startswith("loc_"):
                id_lookup[lid[4:].lower()] = lid  # "office" -> "loc_office"

        # TÃ¬m trong lookup
        if raw_lower in id_lookup:
            return id_lookup[raw_lower]
        elif raw_id in valid_loc_ids:
            return raw_id
        elif f"loc_{raw_id}" in valid_loc_ids:
            return f"loc_{raw_id}"
        else:
            # KhÃ´ng tÃ¬m tháº¥y - thÃªm loc_ prefix náº¿u chÆ°a cÃ³
            if not raw_id.startswith("loc_"):
                return f"loc_{raw_id}"
            return raw_id

    def _normalize_scene_kind(self, value: str) -> str:
        allowed = {
            "character_reaction",
            "interaction",
            "object_detail",
            "environment_story",
            "movement_transition",
        }
        raw = str(value or "").strip().lower().replace(" ", "_").replace("-", "_")
        if raw in allowed:
            return raw
        if "object" in raw or "detail" in raw:
            return "object_detail"
        if "environment" in raw or "space" in raw or "room" in raw:
            return "environment_story"
        if "interact" in raw or "dialog" in raw or "pair" in raw:
            return "interaction"
        if "move" in raw or "transition" in raw or "walk" in raw:
            return "movement_transition"
        return "character_reaction"

    def _infer_scene_kind(self, srt_text: str = "", characters_used: str = "", primary_subject: str = "") -> str:
        inferred_kind, _, _ = self._classify_story_visual_beat(
            srt_text=srt_text,
            characters_used=characters_used,
            primary_subject=primary_subject,
        )
        return inferred_kind

    def _normalize_subject_mode(self, value: str, characters_used: str = "", scene_kind: str = "") -> str:
        allowed = {"character", "pair", "object", "environment"}
        raw = str(value or "").strip().lower().replace(" ", "_").replace("-", "_")
        if raw in allowed:
            return raw
        char_count = len([c for c in str(characters_used or "").split(",") if c.strip() and c.strip() != "[]"])
        if scene_kind == "object_detail":
            return "object"
        if scene_kind == "environment_story":
            return "environment"
        if char_count >= 2:
            return "pair"
        return "character"

    def _scene_uses_closeup_fallback(self, text: str) -> bool:
        import re
        cleaned = " ".join(str(text or "").split()).strip().lower()
        if not cleaned:
            return False
        body_part_hit = re.search(
            r"\b(eye|eyes|mouth|lip|lips|face|profile|jaw|throat|cheek|cheekbone|tear|tears|hand|hands|fingers|knuckles|wedding band|ring on her hand)\b",
            cleaned,
            flags=re.IGNORECASE,
        )
        if not body_part_hit:
            return False
        concrete_prop_hit = re.search(
            r"\b(folder|envelope|paper|document|printout|email|swatch|sample book|sample|paint|mug|fork|plate|carafe|coffee|phone|coat|letter|photo|photograph|map|drawing|cake|dress|pillow|poster|deed|llc)\b",
            cleaned,
            flags=re.IGNORECASE,
        )
        return not bool(concrete_prop_hit)

    def _extract_story_visual_anchor(self, srt_text: str = "", location_used: str = "") -> str:
        import re

        text = " ".join(str(srt_text or "").split())
        lower = text.lower()
        loc = str(location_used or "").strip()
        if re.fullmatch(r"loc\d+", loc, flags=re.IGNORECASE):
            loc = ""

        prioritized_patterns = [
            (r"\bgood design\b", "The room's deliberate arrangement"),
            (r"\bright place at the right time\b", "The room's deliberate arrangement"),
            (r"\bwhat home\b|\bhome was not\b|\bhome is not\b", "The arrangement of the room"),
            (r"\boperations director\b", "The two adults facing each other"),
            (r"\bsecond hand\b", "The watch second hand"),
            (r"\bwatch\b", "The watch"),
            (r"\bmanila folder\b", "The manila folder"),
            (r"\bfolder\b", "The folder"),
            (r"\baffidavit\b", "The affidavit"),
            (r"\bdeed\b", "The deed"),
            (r"\bllc\b", "The LLC paperwork"),
            (r"\bshell company\b", "The shell company paperwork"),
            (r"\bemail\b", "The printed email"),
            (r"\bprintout\b", "The printout"),
            (r"\bletter\b", "The letter"),
            (r"\benvelope\b", "The envelope"),
            (r"\bpaper(?:s)?\b", "The paper on the table"),
            (r"\bdocument(?:s)?\b", "The document"),
            (r"\bcertificate\b", "The certificate"),
            (r"\breport\b", "The report"),
            (r"\bphone\b", "The phone"),
            (r"\bvoicemail\b", "The phone screen"),
            (r"\bcoffee maker\b", "The coffee maker"),
            (r"\bcarafe\b", "The coffee carafe"),
            (r"\bmug\b", "The mug"),
            (r"\bcoffee\b", "The coffee setup"),
            (r"\bcake\b", "The cake"),
            (r"\bdress\b", "The dress"),
            (r"\bpillow\b", "The pillow arrangement"),
            (r"\bcoat\b", "The coat"),
            (r"\bjacket\b", "The jacket"),
            (r"\bgrocery list\b", "The grocery list"),
            (r"\bmap\b", "The map"),
            (r"\bdrawing\b", "The drawing"),
            (r"\bblueprint\b", "The floor plan drawing"),
            (r"\bphotograph\b|\bphoto\b", "The photograph"),
            (r"\bposter\b", "The poster"),
            (r"\btable\b", "The table surface"),
            (r"\bdoorway\b|\bdoor\b", "The doorway"),
            (r"\bdesk\b", "The desk"),
            (r"\bchair\b", "The chair"),
            (r"\bwindow\b", "The window"),
            (r"\bspare room\b|\bstudio\b", "The spare room"),
            (r"\bkitchen\b", "The kitchen space"),
            (r"\bliving room\b", "The living room space"),
            (r"\bhouse\b|\bhome\b", "The house interior"),
            (r"\bporch\b", "The porch"),
            (r"\bdriveway\b", "The driveway"),
            (r"\bcourthouse\b|\bcourtroom\b", "The courtroom space"),
            (r"\boffice\b", "The office space"),
            (r"\btrail\b", "The trail"),
            (r"\bcar\b", "The car interior"),
        ]
        for pattern, replacement in prioritized_patterns:
            if re.search(pattern, lower, flags=re.IGNORECASE):
                return replacement

        if loc:
            return f"The space within {loc}"

        noun_match = re.search(r"\b(the|a|an)\s+([a-z][a-z' -]{2,40})", lower)
        if noun_match:
            phrase = noun_match.group(2).strip(" ,.;:-")
            if phrase and not re.search(
                r"\b(i|we|he|she|they|it|this|that|makes|make|feel|feels|stay|weeks|situation|version|way|truth|point|matter|matters|good design|home)\b",
                phrase,
                flags=re.IGNORECASE,
            ):
                return f"The {phrase}"

        return ""

    def _classify_story_visual_beat(
        self,
        srt_text: str = "",
        characters_used: str = "",
        primary_subject: str = "",
        location_used: str = "",
    ) -> Tuple[str, str, str]:
        import re

        text = " ".join([str(srt_text or ""), str(primary_subject or "")]).lower()
        char_ids = [c for c in str(characters_used or "").split(",") if c.strip() and c.strip() != "[]"]
        char_count = len(char_ids)
        anchor = self._extract_story_visual_anchor(srt_text=srt_text, location_used=location_used)

        movement_hit = re.search(
            r"\b(walk|walking|walked|drove|drive|driving|enter|entered|leave|left|cross|crossed|arrive|arrived|open(?:ed)? the door|reach(?:ed)? the door|step(?:s|ped)? into|move(?:s|d)? in|came in|went back)\b",
            text,
            flags=re.IGNORECASE,
        )
        object_hit = re.search(
            r"\b(folder|document|affidavit|banner|letter|card|phone|paper|certificate|report|dress|cake|window frame|cufflink|envelope|deed|llc|printout|email|poster|map|drawing|photo|photograph|mug|coffee maker|carafe|pillow|coat|jacket|grocery list)\b",
            text,
            flags=re.IGNORECASE,
        )
        space_hit = re.search(
            r"\b(house|room|kitchen|office|corridor|courthouse|courtroom|car interior|trail|building|steps|doorway|living room|porch|driveway|studio|desk|table|window)\b",
            text,
            flags=re.IGNORECASE,
        )
        interaction_hit = re.search(
            r"\b(looked at|looks at|look at each other|said to|told her|told him|asked|answered|hugged|kissed|stood with|sat with|faced each other|between them|the two of them|they both)\b",
            text,
            flags=re.IGNORECASE,
        )
        abstract_exposition = re.search(
            r"\b(i knew|i realized|i thought|i understood|it wasn't|it was not|what mattered|what home is|good design|the truth was|the point was|i trusted|he didn't know|she noticed|i could tell)\b",
            text,
            flags=re.IGNORECASE,
        )

        if movement_hit:
            return ("movement_transition", "pair" if char_count >= 2 else "character", anchor)
        if object_hit:
            return ("object_detail", "object", anchor)
        if char_count >= 2 and interaction_hit:
            return ("interaction", "pair", anchor)
        if space_hit or abstract_exposition:
            return ("environment_story", "environment", anchor)
        return ("character_reaction", "character", anchor)

    def _sanitize_scene_spec_text(self, value: str, fallback: str = "") -> str:
        import re
        text = " ".join(str(value or "").split())
        if not text:
            return fallback
        original = text
        text = re.sub(
            r"\b(split[- ]screen|composite|layered|superimposed|translucent|ghostly|overlay|montage|flashback insert|simultaneous actions?)\b",
            "",
            text,
            flags=re.IGNORECASE,
        )
        text = re.sub(r"\b\d+\)", "", text)
        text = re.sub(r"\b(?:left side|right side|first|second|third)\b", "", text, flags=re.IGNORECASE)
        text = re.sub(r"\s{2,}", " ", text).strip(" ,.;:-")
        if (not text or len(text) < 12) and fallback:
            return fallback
        if re.search(r"\b(sequence of|series of|montage|split[- ]screen|composite|layered|superimposed|translucent|ghostly|overlay)\b", original, flags=re.IGNORECASE) and fallback:
            return fallback
        return text or fallback

    def _extract_primary_clause(self, text: str) -> str:
        import re
        cleaned = " ".join(str(text or "").split())
        if not cleaned:
            return ""
        cleaned = re.sub(
            r"^(?:a|an|the)?\s*(?:static|tight|wide|medium|close-up|close up|extreme close-up|extreme close up|medium shot|wide shot|medium wide shot|medium-wide shot|handheld|composed)\s+(?:shot|frame|view)\s+(?:of|on|from)\s+",
            "",
            cleaned,
            flags=re.IGNORECASE,
        )
        cleaned = re.sub(
            r"^(?:a|an)\s+(?:close-up|close up|extreme close-up|extreme close up|medium shot|wide shot|medium wide shot|medium-wide shot)\s+(?:of|on)\s+",
            "",
            cleaned,
            flags=re.IGNORECASE,
        )
        for sep in [".", ";", " then ", " while ", " as ", " with "]:
            if sep in cleaned:
                cleaned = cleaned.split(sep)[0].strip()
                break
        return cleaned.strip(" ,.;:-")

    def _looks_non_visual_subject(self, text: str) -> bool:
        import re
        cleaned = " ".join(str(text or "").split()).strip()
        if not cleaned:
            return True
        if len(cleaned.split()) > 12:
            return True
        if re.search(r"\b(i|we|they|he|she)\b.*\b(and|but|because|when|while|that|than)\b", cleaned, flags=re.IGNORECASE):
            return True
        if re.search(r"\b(smell|sound|silence|thought|clearer in my mind|version|conversation|story|way that|feeling)\b", cleaned, flags=re.IGNORECASE):
            return True
        return False

    def _looks_multi_action(self, text: str) -> bool:
        import re
        cleaned = " ".join(str(text or "").split())
        if not cleaned:
            return True
        return bool(
            re.search(
                r"\b(simultaneous|multiple|multi[- ]task|frantic motion|and .* and|while .* and|sequence of|series of)\b",
                cleaned,
                flags=re.IGNORECASE,
            )
        )

    def _derive_visual_subject(self, scene: dict, scene_kind: str, subject_mode: str, characters_used: str) -> str:
        import re
        visual = self._extract_primary_clause(scene.get("visual_moment", ""))
        anchor = self._sanitize_scene_spec_text(scene.get("visual_anchor", ""))
        story_anchor = self._extract_story_visual_anchor(
            srt_text=scene.get("srt_text", ""),
            location_used=scene.get("location_used", ""),
        )
        loc = str(scene.get("location_used", "") or "").strip()
        if re.fullmatch(r"loc\d+", loc, flags=re.IGNORECASE):
            loc = ""
        char_ids = [c.strip() for c in str(characters_used or "").split(",") if c.strip() and c.strip() != "[]"]
        char_count = len(char_ids)

        if scene_kind == "object_detail":
            if story_anchor:
                return story_anchor
            if anchor:
                return anchor
            if visual:
                return visual
            return "The key prop in frame"

        if scene_kind == "environment_story":
            if story_anchor:
                return story_anchor
            if anchor:
                return anchor
            if loc:
                return f"The space within {loc}"
            return "The environment in frame"

        if subject_mode == "pair" or char_count >= 2:
            return "The two adults in frame"

        if char_count >= 1:
            if visual and re.search(r"\bhand\b", visual, flags=re.IGNORECASE):
                return visual
            return "The adult in frame"

        if visual:
            return visual
        if anchor:
            return anchor
        return "The primary visual subject in frame"

    def _derive_visual_action(self, scene: dict, scene_kind: str, subject_mode: str, characters_used: str) -> str:
        import re
        visual = " ".join(str(scene.get("visual_moment", "") or "").split())
        anchor = self._sanitize_scene_spec_text(scene.get("visual_anchor", ""))
        story_anchor = self._extract_story_visual_anchor(
            srt_text=scene.get("srt_text", ""),
            location_used=scene.get("location_used", ""),
        )
        char_ids = [c.strip() for c in str(characters_used or "").split(",") if c.strip() and c.strip() != "[]"]
        if scene_kind == "object_detail":
            if story_anchor:
                return f"The frame centers {story_anchor.lower()}"
            if re.search(r"\b(hand|notification|folder|phone|deed|leaf|leaves|iv|cup|briefcase|coat)\b", visual, flags=re.IGNORECASE):
                clause = self._extract_primary_clause(visual)
                if clause:
                    return clause
            return f"The frame centers the visual detail of {anchor}" if anchor else "The object becomes the focal visual beat"
        if scene_kind == "environment_story":
            if story_anchor:
                return f"The space holds on {story_anchor.lower()}"
            return f"The space holds on {anchor}" if anchor else "The environment holds a quiet visual beat"
        if subject_mode == "pair" or len(char_ids) >= 2:
            return "The adult response lands in a single shared beat"
        if visual:
            return self._extract_primary_clause(visual) or "The adult holds a single visible beat"
        return "The adult holds a single visible beat"

    def _stabilize_scene_spec(
        self,
        scene: dict,
        scene_kind: str,
        subject_mode: str,
        primary_subject: str,
        primary_action: str,
        visual_anchor: str,
    ) -> Tuple[str, str, str, str, str]:
        import re

        chars = scene.get("characters_used", "")
        inferred_kind, inferred_mode, story_anchor = self._classify_story_visual_beat(
            srt_text=scene.get("srt_text", ""),
            characters_used=chars,
            primary_subject=primary_subject,
            location_used=scene.get("location_used", ""),
        )

        subject_bad = self._looks_non_visual_subject(primary_subject) or self._scene_uses_closeup_fallback(primary_subject)
        action_bad = self._looks_multi_action(primary_action)
        closeup_fallback = self._scene_uses_closeup_fallback(primary_subject) or self._scene_uses_closeup_fallback(primary_action)

        if inferred_kind in {"object_detail", "environment_story", "interaction", "movement_transition"}:
            if scene_kind == "character_reaction" and (closeup_fallback or subject_bad or action_bad):
                scene_kind = inferred_kind
                subject_mode = inferred_mode

        if scene_kind == "object_detail":
            subject_mode = "object"
            if subject_bad or closeup_fallback:
                primary_subject = story_anchor or self._derive_visual_subject(scene, scene_kind, subject_mode, chars)
            if action_bad or closeup_fallback:
                primary_action = self._derive_visual_action(scene, scene_kind, subject_mode, chars)
            if not visual_anchor or self._scene_uses_closeup_fallback(visual_anchor):
                visual_anchor = story_anchor or primary_subject

        elif scene_kind == "environment_story":
            subject_mode = "environment"
            if subject_bad or closeup_fallback:
                primary_subject = story_anchor or self._derive_visual_subject(scene, scene_kind, subject_mode, chars)
            if action_bad or closeup_fallback:
                primary_action = self._derive_visual_action(scene, scene_kind, subject_mode, chars)
            if not visual_anchor or self._scene_uses_closeup_fallback(visual_anchor):
                visual_anchor = story_anchor or primary_subject

        elif scene_kind == "interaction":
            subject_mode = "pair"
            if subject_bad or closeup_fallback:
                primary_subject = "The two adults in frame"
            if action_bad or closeup_fallback:
                primary_action = self._derive_visual_action(scene, scene_kind, subject_mode, chars)
            if not visual_anchor or self._scene_uses_closeup_fallback(visual_anchor):
                visual_anchor = story_anchor or primary_subject

        elif scene_kind == "movement_transition":
            if subject_mode not in {"character", "pair"}:
                subject_mode = inferred_mode if inferred_mode in {"character", "pair"} else "character"
            if subject_bad:
                primary_subject = "The adult in motion" if subject_mode == "character" else "The two adults in motion"
            if action_bad or closeup_fallback:
                primary_action = self._derive_visual_action(scene, scene_kind, subject_mode, chars)
            if not visual_anchor:
                visual_anchor = story_anchor or primary_subject

        if re.search(r"\b(sequence|montage|split-screen|overlay|composite|ghostly|translucent|layered)\b", primary_action, flags=re.IGNORECASE):
            primary_action = self._derive_visual_action(scene, scene_kind, subject_mode, chars)

        return scene_kind, subject_mode, primary_subject, primary_action, visual_anchor

    def _audit_scene_specs(self, scenes: list) -> dict:
        audit = {
            "non_reaction_closeup_fallbacks": 0,
            "object_scenes_without_object_anchor": 0,
            "environment_scenes_without_space_anchor": 0,
        }
        for scene in scenes or []:
            scene_kind = str(scene.get("scene_kind", "") or "")
            subject = str(scene.get("primary_subject", "") or "")
            action = str(scene.get("primary_action", "") or "")
            anchor = str(scene.get("visual_anchor", "") or "")
            combined = " ".join([subject.lower(), action.lower(), anchor.lower()])

            closeupish = self._scene_uses_closeup_fallback(subject) or self._scene_uses_closeup_fallback(action)
            if scene_kind != "character_reaction" and closeupish:
                audit["non_reaction_closeup_fallbacks"] += 1

            if scene_kind == "object_detail":
                if not any(k in combined for k in [
                    "folder", "document", "paper", "email", "printout", "phone", "dress", "cake", "envelope",
                    "deed", "map", "drawing", "photo", "coat", "mug", "carafe", "pillow", "poster", "swatch",
                ]):
                    audit["object_scenes_without_object_anchor"] += 1

            if scene_kind == "environment_story":
                if not any(k in combined for k in [
                    "room", "house", "kitchen", "living room", "office", "courtroom", "space", "doorway",
                    "porch", "driveway", "studio", "table", "window", "desk", "arrangement", "interior",
                ]):
                    audit["environment_scenes_without_space_anchor"] += 1

        return audit

    def _apply_minor_safe_scene(self, scene: dict, minor_char_ids=None) -> dict:
        import re
        scene = dict(scene or {})
        minor_char_ids = minor_char_ids or set()
        char_ids = [c.strip() for c in str(scene.get("characters_used", "") or "").split(",") if c.strip() and c.strip() != "[]"]
        adult_ids = [cid for cid in char_ids if cid not in minor_char_ids]
        minor_ids = [cid for cid in char_ids if cid in minor_char_ids]
        if not minor_ids:
            return scene

        scene["characters_used"] = ", ".join(adult_ids)
        must_not_show = str(scene.get("must_not_show", "") or "")
        if "no child visible" not in must_not_show.lower():
            must_not_show = (must_not_show.rstrip(" ;.") + "; no child visible").strip(" ;.")
        scene["must_not_show"] = must_not_show

        primary_subject = str(scene.get("primary_subject", "") or "")
        primary_action = str(scene.get("primary_action", "") or "")
        visual_anchor = str(scene.get("visual_anchor", "") or "")
        visual_moment = str(scene.get("visual_moment", "") or "")
        subject_mode = str(scene.get("subject_mode", "") or "")

        if adult_ids:
            if subject_mode == "pair" and len(adult_ids) == 1:
                scene["subject_mode"] = "character"
            primary_subject = re.sub(r"\bnv\d+\b", "the adult in frame", primary_subject, flags=re.IGNORECASE)
            primary_action = re.sub(r"\bnv\d+\b", "the adult in frame", primary_action, flags=re.IGNORECASE)
            if re.search(r"\b(child|children|kid|kids|boy|girl|daughter|son|teen|minor)\b", primary_subject, flags=re.IGNORECASE):
                scene["primary_subject"] = "The adult in frame"
            if re.search(r"\b(child|children|kid|kids|boy|girl|daughter|son|teen|minor)\b", primary_action, flags=re.IGNORECASE):
                scene["primary_action"] = "The adult registers the moment through a restrained visible reaction"
            return scene

        surrogate = visual_anchor or self._extract_primary_clause(visual_moment)
        if re.search(r"\b(coat|jacket|glove|rake|column|railing|step|steps|door|porch|drawing|toy|blanket|phone|folder|deed|leaf|leaves)\b", surrogate, flags=re.IGNORECASE):
            scene["scene_kind"] = "object_detail"
            scene["subject_mode"] = "object"
            scene["primary_subject"] = surrogate
            scene["primary_action"] = "The frame implies the off-screen presence without showing the child directly"
        else:
            scene["scene_kind"] = "environment_story"
            scene["subject_mode"] = "environment"
            scene["primary_subject"] = surrogate or "The environment marked by the child's off-screen presence"
            scene["primary_action"] = "The space carries the emotional trace of the off-screen child"
        return scene

    def _extract_psychology_concept_from_srt(self, srt_text: str) -> tuple:
        """Extract a visual concept from SRT when no concept rule matches.
        Handles both Latin (Vietnamese/English) and non-Latin (Japanese/Korean/etc.) text.
        """
        import re
        import unicodedata
        text = " ".join(str(srt_text or "").split())
        if not text or len(text) < 10:
            return ("", "", "")

        # Detect if text is primarily non-Latin (CJK, Korean, etc.)
        alpha_chars = [c for c in text if c.isalpha()]
        latin_ratio = sum(1 for c in alpha_chars if c.isascii()) / max(len(alpha_chars), 1)
        is_non_latin = latin_ratio < 0.5

        if is_non_latin:
            # Do not invent a generic metaphor for languages we cannot parse locally.
            # The API-supplied visual_contract/visual_moment must carry the translation.
            return ("", "", "")

        # Latin text path (Vietnamese/English/French/etc.)
        norm = unicodedata.normalize("NFKD", text.lower())
        norm = "".join(ch for ch in norm if not unicodedata.combining(ch))
        tokens = re.findall(r'\b[a-z]{4,}\b', norm)
        vn_stop = {'khong', 'trong', 'cung', 'minh', 'duoc', 'nhung', 'cac', 'khi', 'neu',
                   'nhieu', 'theo', 'dang', 'hoac', 'viec', 'nhin', 'nghe', 'ngay', 'gia',
                   'chung', 'them', 'sau', 'nhat', 'that', 'muon', 'cuoc', 'phai', 'luon',
                   'chia', 'rang', 'thanh', 'dieu', 'tham', 'mang', 'xuat', 'tren', 'duoi',
                   'giua', 'qua', 'vay', 'nay', 'day', 'kia', 'con', 'hay', 'biet', 'thay',
                   'nghi', 'nao', 'lam', 'ban', 'quen', 'nen', 'toan', 'moi', 'chua', 'roi'}
        content_tokens = [t for t in tokens if t not in vn_stop and len(t) >= 4][:8]
        if not content_tokens:
            return ("", "", "")
        concept_hint = " ".join(content_tokens[:3])
        subject = f"nv1 beside a simple symbolic object representing {concept_hint}"
        action = f"nv1 interacts with the symbolic representation of {concept_hint}"
        anchor = f"the visual metaphor for {concept_hint}"
        return (subject, action, anchor)

    def _apply_psychology_scene_spec(self, scene: dict) -> dict:
        import re
        import unicodedata

        scene = dict(scene or {})
        raw_chars = str(scene.get("characters_used", "") or "").strip()
        if self.topic_prompts:
            raw_chars = self.topic_prompts.normalize_scene_characters(raw_chars)
        scene["characters_used"] = "nv1"
        scene["location_used"] = ""

        source = " ".join(str(scene.get(k, "") or "") for k in ["srt_text", "visual_moment", "message", "primary_subject", "primary_action"])
        source = " ".join(source.split())
        low = unicodedata.normalize("NFKD", source.lower())
        low = "".join(ch for ch in low if not unicodedata.combining(ch))
        existing_primary_subject = self._clean_psychology_spec_text(scene.get("primary_subject", ""))
        existing_primary_action = self._clean_psychology_spec_text(scene.get("primary_action", ""))
        needs_metaphor_fallback = (
            self._looks_non_visual_subject(existing_primary_subject)
            or self._looks_weak_psychology_spec(existing_primary_subject)
            or self._looks_multi_action(existing_primary_action)
            or self._looks_weak_psychology_spec(existing_primary_action)
        )

        concept_rules = [
            (r"\b(anxiety|worry|worried|fear|afraid|lo lang|so hai|bat an|lo au)\b", "a soft storm cloud hovering above nv1", "nv1 steadies their posture beneath a small symbolic storm cloud", "the storm cloud turning into calm pastel droplets"),
            (r"\b(stress|pressure|overwhelm|ap luc|cang thang|qua tai|suc ep)\b", "a stack of rounded stones pressing near nv1", "nv1 gently removes one stone from the stack", "the heavy stack becoming lighter"),
            (r"\b(habit|routine|loop|pattern|thoi quen|vong lap|lap lai|quen thuoc)\b", "a simple circular path around nv1", "nv1 notices the repeated loop and steps toward a small opening", "the broken loop in the path"),
            (r"\b(procrastination|delay|avoid|tri hoan|ne tranh|cham tre)\b", "a tiny task mountain beside nv1", "nv1 takes one small first step toward the mountain", "one small step at the base of the mountain"),
            (r"\b(confidence|self[- ]?worth|believe|tu tin|gia tri ban than|tin vao|niem tin)\b", "nv1 beside a small growing plant", "nv1 waters the plant as it grows upright", "the small plant growing stronger"),
            (r"\b(boundary|limit|say no|ranh gioi|gioi han|tu choi|pham vi)\b", "a soft protective circle around nv1", "nv1 calmly raises one hand beside the boundary", "the gentle boundary line protecting personal space"),
            (r"\b(compare|comparison|jealous|so sanh|ghen ti|ganh ty)\b", "nv1 facing a simplified mirror and shadow silhouettes", "nv1 turns from the mirror toward their own small path", "the mirror losing its grip on nv1"),
            (r"\b(relationship|connect|lonely|friend|love|ket noi|moi quan he|co don|yeu thuong|tinh ban)\b", "nv1 near a small bridge between two soft islands", "nv1 reaches toward the bridge while silhouettes stay anonymous", "the small bridge symbolizing connection"),
            (r"\b(choice|decision|decide|choose|lua chon|quyet dinh|chon lua)\b", "a forked pastel path in front of nv1", "nv1 pauses and points toward one clear path", "the forked path becoming easier to choose"),
            (r"\b(thought|mind|belief|thinking|suy nghi|niem tin|tam tri|suy tu)\b", "a cluster of simple thought bubbles above nv1", "nv1 sorts the bubbles into a calmer shape", "the tangled thought bubbles becoming clear"),
            (r"\b(anger|angry|gian|tuc gian)\b", "a harmless joke mask casting a sharp shadow", "nv1 notices the sharp shadow beneath the smiling mask", "the sharp shadow underneath the joke mask"),
            (r"\b(emotion|feeling|sad|cam xuc|buon|xuc cam)\b", "nv1 holding a soft glowing emotion orb", "nv1 observes the orb instead of pushing it away", "the emotion orb becoming easier to understand"),
            (r"\b(goal|growth|improve|change|muc tieu|phat trien|thay doi|tot hon|tien bo)\b", "nv1 climbing three simple pastel steps", "nv1 places one foot on the next step", "the next small step upward"),
            (r"\b(accept|acceptance|embrace|chap nhan|dong y|tha thu)\b", "nv1 opening their hands to receive a soft glowing shape", "nv1 gently holds the shape without resistance", "the accepted shape settling peacefully"),
            (r"\b(forgive|forgiveness|let go|tha thu|buong bo|tha)\b", "nv1 releasing a small weight into a calm stream", "nv1 watches the weight float away gently", "the released weight drifting into soft light"),
            (r"\b(gratitude|grateful|thankful|biet on|cam on|tri an)\b", "nv1 holding a small glowing heart", "nv1 places the heart gently on a simple altar", "the glowing heart radiating warmth"),
            (r"\b(patience|wait|waiting|kien nhan|cho doi|nhan nai)\b", "nv1 sitting beside a small hourglass", "nv1 observes the sand flowing calmly", "the hourglass showing gentle passage of time"),
            (r"\b(courage|brave|bravery|dung cam|can dam|dung manh)\b", "nv1 facing a small symbolic door", "nv1 reaches for the door handle with steady hand", "the door beginning to open"),
            (r"\b(hope|hopeful|optimis|hy vong|tich cuc|lac quan)\b", "nv1 beside a small sunrise on the horizon", "nv1 looks toward the growing light", "the sunrise bringing soft warm glow"),
        ]

        subject = ""
        action = ""
        anchor = ""

        if re.search(r"\b(phone|smartphone|screen|notification|message|signal|scroll|swipe|badge)\b", low):
            subject = "nv1 with the phone or notification detail named by the narration"
            action = "nv1's hand, gaze, and posture show the exact phone-related pressure in the narration"
            anchor = "the phone screen or notification signal that drives the scene"

        # Cultural metaphors are taste references only. They must not be injected
        # as hard scene specs because they can replace the actual narration.
        _profile = getattr(self, 'psychology_style_profile', {}) or {}
        _cultural_metaphors = str(_profile.get('cultural_metaphors', '') or '')
        if False and _cultural_metaphors and needs_metaphor_fallback:
            # Build a lookup: concept_keyword -> (subject, action, anchor)
            _concept_map = {
                'anxiety': ['anxiety', 'anxious', 'worry', 'worried', 'fear', 'afraid', 'nervous', 'uneasy', 'lo lang', 'so hai', 'bat an', 'lo au', 'ansiedad', 'ansioso', 'anxiete', 'anxieux', 'angst', 'aengstlich', 'ansiedade', 'ansia', 'kaygi', 'endise'],
                'stress': ['stress', 'pressure', 'overwhelm', 'ap luc', 'cang thang', 'qua tai', 'estres', 'druck', 'pressao', 'stres'],
                'boundary': ['boundary', 'limit', 'say no', 'ranh gioi', 'gioi han', 'limite', 'grenze', 'sinir'],
                'connection': ['relationship', 'connect', 'friend', 'love', 'ket noi', 'moi quan he', 'relacion', 'relation', 'beziehung', 'relacao', 'ilgilenme'],
                'letting_go': ['let go', 'release', 'forgive', 'buong bo', 'tha thu', 'soltar', 'lacher', 'loslassen', 'birakma'],
                'loneliness': ['lonely', 'alone', 'isolated', 'co don', 'soledad', 'solitude', 'einsamkeit', 'solidao', 'yalnizlik'],
                'growth': ['growth', 'improve', 'change', 'goal', 'phat trien', 'crecimiento', 'croissance', 'wachstum', 'crescimento', 'gelisim'],
                'courage': ['courage', 'brave', 'dung cam', 'coraje', 'courage', 'mut', 'coragem', 'cesaret'],
                'acceptance': ['accept', 'embrace', 'chap nhan', 'aceptar', 'accepter', 'akzeptieren', 'aceitar', 'kabul'],
                'forgiveness': ['forgive', 'forgiveness', 'tha thu', 'perdonar', 'pardonner', 'vergeben', 'perdoar', 'affetmek'],
            }
            # Parse cultural_metaphors: "anxiety: rain on shrine | stress: too many..."
            _parsed_metaphors = {}
            for _part in _cultural_metaphors.split('|'):
                _part = _part.strip()
                if ':' in _part:
                    _key, _visual = _part.split(':', 1)
                    _parsed_metaphors[_key.strip().lower()] = _visual.strip()

            # Try to match SRT text against concept keywords
            for _concept_key, _keywords in _concept_map.items():
                if any(kw in low for kw in _keywords):
                    if _concept_key in _parsed_metaphors:
                        _visual = _parsed_metaphors[_concept_key]
                        # Ensure nv1 is in the visual description
                        if 'nv1' in _visual:
                            subject = _visual.split('while')[0].strip() if 'while' in _visual else _visual[:100]
                            action = _visual
                            anchor = _visual.split('|')[0].strip()[:80]
                        else:
                            subject = f"nv1 in scene: {_visual[:80]}"
                            action = f"nv1 {_visual}"
                            anchor = _visual[:80]
                        break

        # Fallback: if cultural_metaphors didn't match, use generic concept_rules
        if not subject:
            for pattern, matched_subject, matched_action, matched_anchor in concept_rules:
                if re.search(pattern, low, flags=re.IGNORECASE):
                    subject, action, anchor = matched_subject, matched_action, matched_anchor
                    break

        visual_moment = self._sanitize_scene_spec_text(scene.get("visual_moment", ""), fallback="")
        visual_moment = self._clean_psychology_spec_text(visual_moment)
        if self._looks_weak_psychology_spec(visual_moment):
            visual_moment = ""

        # If no concept rule matched, try to extract from SRT directly
        if not subject:
            if visual_moment and not self._looks_non_visual_subject(visual_moment):
                subject = self._clean_psychology_spec_text(self._extract_primary_clause(visual_moment))
            else:
                srt_subject, srt_action, srt_anchor = self._extract_psychology_concept_from_srt(scene.get("srt_text", ""))
                if srt_subject:
                    subject = srt_subject
                else:
                    subject = "nv1 with one simple symbolic object from the narration" if scene["characters_used"] == "nv1" else "one simple symbolic object from the narration"
        if not action:
            if visual_moment:
                action = self._clean_psychology_spec_text(self._extract_primary_clause(visual_moment))
            else:
                srt_subject, srt_action, srt_anchor = self._extract_psychology_concept_from_srt(scene.get("srt_text", ""))
                if srt_action:
                    action = srt_action
                else:
                    action = "the central idea is shown through one clear visual metaphor from the narration"
        if not anchor:
            srt_subject, srt_action, srt_anchor = self._extract_psychology_concept_from_srt(scene.get("srt_text", ""))
            anchor = srt_anchor or self._clean_psychology_spec_text(scene.get("visual_anchor", "")) or subject

        primary_subject = existing_primary_subject
        primary_action = existing_primary_action
        visual_anchor = self._clean_psychology_spec_text(scene.get("visual_anchor", ""))

        if self._looks_non_visual_subject(primary_subject) or self._looks_weak_psychology_spec(primary_subject):
            primary_subject = subject
        if self._looks_multi_action(primary_action) or self._looks_weak_psychology_spec(primary_action):
            primary_action = action
        if not visual_anchor or self._looks_non_visual_subject(visual_anchor) or self._looks_weak_psychology_spec(visual_anchor):
            visual_anchor = anchor

        current_kind = str(scene.get("scene_kind", "") or "").strip()
        current_mode = str(scene.get("subject_mode", "") or "").strip()
        scene["scene_kind"] = current_kind if current_kind in {
            "character_reaction", "interaction", "object_detail", "environment_story", "movement_transition"
        } else "character_reaction"
        scene["subject_mode"] = current_mode if current_mode in {"character", "pair", "object", "environment"} else "character"
        scene["primary_subject"] = primary_subject or subject
        scene["primary_action"] = primary_action or action
        scene["visual_anchor"] = visual_anchor or anchor
        scene["visual_moment"] = f"{scene['primary_subject']}: {scene['primary_action']}"
        scene["must_not_show"] = "no readable text, no captions, no labels, no UI text, no chart text, no document text, no watermark, no photo/cinematic camera style, no named/reference characters except nv1"
        scene = self._enforce_psychology_cultural_anchor(scene)
        return scene

    def _looks_weak_psychology_spec(self, value: str) -> bool:
        import re
        text = " ".join(str(value or "").split()).strip().lower()
        if not text:
            return True
        if len(text.split()) <= 2:
            return True
        if re.fullmatch(r"(?:and|or|the|a|an|row|transaction row|checks and talks about|talks about|checks)", text):
            return True
        # If text contains significant non-Latin characters (CJK, Korean, etc.),
        # trust it as valid - don't apply Latin-only keyword check
        alpha_chars = [c for c in text if c.isalpha()]
        latin_ratio = sum(1 for c in alpha_chars if c.isascii()) / max(len(alpha_chars), 1)
        if latin_ratio < 0.5:
            return False  # Non-Latin text is assumed valid from AI planning
        psychology_keywords = (
            r"\b(nv1|cloud|storm|stone|stack|loop|path|mountain|plant|boundary|mirror|bridge|"
            r"thought|bubble|emotion|orb|step|metaphor|symbolic|idea|choice|mind|calm|worry|"
            r"stress|anxiety|lo lang|ap luc|tam tri|cam xuc|hourglass|door|sunrise|heart|"
            r"weight|stream|altar|acceptance|forgiveness|gratitude|patience|courage|hope|"
            r"phone|screen|room|studio|subway|bench|table|plate|window|hoodie|silhouette|"
            r"chair|floor|lamp|counter|hands|chest|shoulders)\b"
        )
        if not re.search(psychology_keywords, text):
            if re.search(r"\b(transaction|laptop|fork|pasta|bank|document|folder|row)\b", text):
                return True
        return False

    def _clean_psychology_spec_text(self, value: str) -> str:
        import re
        text = _strip_forced_psychology_cultural_props(value)
        text = " ".join(str(text or "").split()).strip()
        if not text:
            return ""
        text = re.sub(r"\b(split[- ]screen|composite|layered|superimposed|translucent|ghostly|overlay|montage|flashback insert|simultaneous actions?)\b", "", text, flags=re.IGNORECASE)
        text = re.sub(r"(?:^|\s)\d+\.\s*", " ", text)
        text = re.sub(r"\b(?:left side|right side|left frame|right frame)\b", "", text, flags=re.IGNORECASE)
        text = re.sub(r"\bphotorealistic\b|\b8k\b|\bARRI Alexa\b|\bSony Venice\b|\banamorphic\b|\bfilm grain\b|\bcinematic color grade\b|\bshallow depth of field\b|\bbokeh\b", "", text, flags=re.IGNORECASE)
        text = re.sub(r"\b(?:cinematic|film|movie)\s+(?:shot|still|frame|sequence|camera|look|style)\b", "clean illustration", text, flags=re.IGNORECASE)
        text = re.sub(r"\bnv(?!1\b)\d+\b", "anonymous silhouette", text, flags=re.IGNORECASE)
        text = re.sub(r"\bloc\d+\b", "simple warm background", text, flags=re.IGNORECASE)
        text = re.sub(r"\b(Megan|husband|wife|Wells Fargo|HomeGoods|leftover pasta|open marriages)\b", "", text, flags=re.IGNORECASE)
        text = re.sub(r"\s{2,}", " ", text)
        return text.strip(" ,.;:-")

    def _psychology_scene_text_overlap(self, candidate: str, scene: dict) -> int:
        """Return rough content-token overlap between a proposed visual and the locked narration/spec."""
        import re
        import unicodedata

        def tokens(value: str) -> set:
            value = unicodedata.normalize("NFKD", str(value or "").lower())
            value = "".join(ch for ch in value if not unicodedata.combining(ch))
            stop = {
                "the", "and", "with", "that", "this", "from", "into", "while", "their",
                "they", "them", "you", "your", "scene", "visual", "metaphor", "symbolic",
                "simple", "soft", "warm", "clear", "nv1", "character", "viewer",
            }
            return {t for t in re.findall(r"\b[a-z0-9][a-z0-9-]{3,}\b", value) if t not in stop}

        source = " ".join(str(scene.get(k, "") or "") for k in [
            "srt_text", "visual_moment", "primary_subject", "primary_action", "visual_anchor",
        ])
        return len(tokens(candidate) & tokens(source))

    def _psychology_text_token_overlap(self, candidate: str, source: str) -> int:
        import re
        import unicodedata

        def tokens(value: str) -> set:
            value = unicodedata.normalize("NFKD", str(value or "").lower())
            value = "".join(ch for ch in value if not unicodedata.combining(ch))
            stop = {
                "the", "and", "with", "that", "this", "from", "into", "while", "their",
                "they", "them", "you", "your", "scene", "visual", "metaphor", "symbolic",
                "simple", "soft", "warm", "clear", "nv1", "character", "viewer",
            }
            return {t for t in re.findall(r"\b[a-z0-9][a-z0-9-]{3,}\b", value) if t not in stop}

        return len(tokens(candidate) & tokens(source))

    def _is_generic_psychology_anchor(self, value: str) -> bool:
        import re
        text = " ".join(str(value or "").split()).strip().lower()
        if not text:
            return True
        return bool(re.search(
            r"\b(the visual metaphor for|simple symbolic object representing|symbolic representation of|"
            r"emotion orb becoming easier|tangled thought bubbles becoming clear|"
            r"window seat holding cold coffee|room walls slowly closing inward|"
            r"two coffee mugs steaming)\b",
            text,
            flags=re.IGNORECASE,
        ))

    def _merge_scene_plan_spec(self, scene: dict, scene_plan: dict = None, minor_char_ids=None) -> dict:
        merged = dict(scene or {})
        plan = dict(scene_plan or {})
        for key in [
            "segment_id",
            "sequence_id",
            "sequence_role",
            "shot_function",
            "beat_type",
            "emotional_turn",
            "continuity_from_prev",
            "transition_to_next",
            "viewer_attention",
            "subtext_delivery",
            "continuity_note",
            "artistic_intent",
            "character_action",
            "key_focus",
            "visual_contract",
            "alignment_notes",
        ]:
            if plan.get(key):
                merged[key] = plan.get(key)
        if plan.get("scene_kind"):
            merged["scene_kind"] = plan.get("scene_kind")
        if plan.get("subject_mode"):
            merged["subject_mode"] = plan.get("subject_mode")
        if plan.get("primary_subject"):
            merged["primary_subject"] = plan.get("primary_subject")
        if plan.get("primary_action"):
            merged["primary_action"] = plan.get("primary_action")
        if not self._is_styled and plan.get("key_focus"):
            merged["visual_anchor"] = plan.get("key_focus")
        elif not self._is_styled and plan.get("viewer_attention"):
            merged["visual_anchor"] = plan.get("viewer_attention")
        if plan.get("shot_type") and not merged.get("camera"):
            merged["camera"] = plan.get("shot_type")
        if plan.get("lighting") and not merged.get("lighting"):
            merged["lighting"] = plan.get("lighting")
        if self._is_styled:
            merged = self._apply_psychology_scene_spec(merged)
            contract = self._clean_psychology_spec_text(merged.get("visual_contract", ""))
            if contract:
                merged["visual_contract"] = contract
                if self._looks_weak_psychology_spec(merged.get("primary_subject", "")) or self._is_generic_psychology_anchor(merged.get("primary_subject", "")):
                    merged["primary_subject"] = self._extract_primary_clause(contract)[:220] or merged.get("primary_subject", "")
                if self._looks_weak_psychology_spec(merged.get("visual_anchor", "")) or self._is_generic_psychology_anchor(merged.get("visual_anchor", "")):
                    merged["visual_anchor"] = self._extract_primary_clause(contract)[:220] or merged.get("visual_anchor", "")
            plan_focus = self._clean_psychology_spec_text(plan.get("key_focus", ""))
            plan_attention = self._clean_psychology_spec_text(plan.get("viewer_attention", ""))
            current_anchor = self._clean_psychology_spec_text(merged.get("visual_anchor", ""))
            generic_anchor = self._is_generic_psychology_anchor(current_anchor)
            for candidate in [plan_focus, plan_attention]:
                if not candidate:
                    continue
                # Step 6 fields are useful artistic guidance, but they must not overwrite
                # a stronger SRT-derived visual anchor with a repeated channel motif.
                refined_source = " ".join(str(merged.get(k, "") or "") for k in [
                    "srt_text", "primary_subject", "primary_action", "visual_moment",
                ])
                candidate_overlap = self._psychology_text_token_overlap(candidate, refined_source)
                current_overlap = self._psychology_text_token_overlap(current_anchor, refined_source)
                current_unsupported = bool(check_unsupported_prompt_details(
                    srt_text=merged.get("srt_text", ""),
                    img_prompt=current_anchor,
                    primary_subject=merged.get("primary_subject", ""),
                    primary_action=merged.get("primary_action", ""),
                    visual_anchor="",
                ))
                if (
                    (generic_anchor or not current_anchor or current_unsupported or current_overlap < 2)
                    and candidate_overlap >= 2
                ):
                    merged["visual_anchor"] = candidate
                    break
            return merged
        merged = self._strengthen_scene_spec_for_prompting(merged, plan)
        return self._ensure_scene_spec_fields(merged, minor_char_ids=minor_char_ids)

    def _strengthen_scene_spec_for_prompting(self, scene: dict, plan: dict = None) -> dict:
        scene = dict(scene or {})
        plan = dict(plan or {})
        srt_text = str(scene.get("srt_text") or "").strip()
        srt_low = srt_text.lower()
        primary_action = str(scene.get("primary_action") or "").strip()
        primary_action_low = primary_action.lower()
        primary_subject = str(scene.get("primary_subject") or "").strip()
        scene_kind = str(scene.get("scene_kind") or "").strip().lower()
        subject_mode = str(scene.get("subject_mode") or "").strip().lower()
        plan_action = str(plan.get("character_action") or "").strip()
        plan_attention = str(plan.get("viewer_attention") or plan.get("key_focus") or "").strip()

        human_terms = [" he ", " she ", " his ", " her ", " megan", " husband", " wife", " narrator", " hand", " hands", " fingers", " gaze", " eyes "]
        has_human_primary = any(term in f" {primary_action_low} " for term in human_terms)
        has_human_plan = any(term in f" {plan_action.lower()} " for term in human_terms)

        if scene_kind == "object_detail" and not has_human_primary and has_human_plan:
            scene["primary_action"] = plan_action
            primary_action_low = plan_action.lower()

        if "eating leftover pasta" in srt_low or ("leftover pasta" in srt_low and "eating" in srt_low):
            scene["scene_kind"] = "object_detail"
            scene["subject_mode"] = "object"
            scene["primary_subject"] = "The leftover pasta and fork in front of the narrator"
            scene["primary_action"] = "The narrator lifts a fork from the leftover pasta"
            scene["visual_anchor"] = "the fork lifting from the leftover pasta"
            return scene

        if "wasn't looking at it" in srt_low and "laptop" in srt_low:
            if scene_kind == "object_detail" and subject_mode == "object":
                scene["primary_subject"] = "The leftover pasta and Megan's open laptop on the oak table"
                scene["primary_action"] = "Megan sits behind the open laptop but looks away from it"
                scene["visual_anchor"] = "Megan turned away from the open laptop"
                return scene

        if "put my fork down" in srt_low or "fork down" in srt_low:
            scene["scene_kind"] = "object_detail"
            scene["subject_mode"] = "object"
            scene["primary_subject"] = "The fork beside the plate of leftover pasta"
            scene["primary_action"] = "A hand sets the fork beside the pasta plate"
            scene["visual_anchor"] = "the fork resting beside the cold pasta plate"
            return scene

        if scene_kind == "environment_story":
            if "making coffee" in srt_low and any(k in srt_low for k in ["stood in the kitchen", "stood at the counter", "kitchen making coffee"]):
                scene["primary_subject"] = "The narrator at the kitchen counter with his coffee"
                scene["primary_action"] = "He stands at the counter making coffee in a house that feels emotionally wrong"
                scene["visual_anchor"] = "the coffee cup under the misaligned kitchen light"
                return scene
            if any(k in srt_low for k in ["got into her car and drove away", "got into his car and drove away", "drove away"]):
                scene["primary_subject"] = "The woman crossing the gas station lot toward her car"
                scene["primary_action"] = "She gets into her car and drives away"
                scene["visual_anchor"] = "the fountain drink cup and chip bag against the store glow"
                return scene
            if any(k in srt_low for k in ["drove to work on autopilot", "driving to work on autopilot"]):
                scene["scene_kind"] = "movement_transition"
                scene["subject_mode"] = "character"
                scene["primary_subject"] = "The narrator driving alone to work"
                scene["primary_action"] = "He drives on autopilot without reacting"
                scene["visual_anchor"] = "his hands fixed on the steering wheel"
                return scene

        if scene_kind == "object_detail":
            if any(k in srt_low for k in ["wells fargo", "homegoods", "transaction", "charge from homegoods"]):
                scene["primary_subject"] = "The HomeGoods transaction row on the laptop screen"
                scene["primary_action"] = "The transaction row remains fixed on the screen as he scans it"
                scene["visual_anchor"] = "the HomeGoods charge in the Wells Fargo transaction list"
                return scene
            if any(k in srt_low for k in ["texts, calls", "45 minutes", "11 at night", "6 in the morning", "phone bill"]):
                scene["primary_subject"] = "The call-and-text rows for the unknown number"
                scene["primary_action"] = "The records remain fixed on screen as the late-hour pattern becomes clear"
                scene["visual_anchor"] = "45-minute duration beside 11 p.m. and 6 a.m. timestamps"
                return scene

        if scene_kind == "object_detail" and "open marriages" in srt_low and not has_human_primary:
            scene["scene_kind"] = "interaction"
            scene["subject_mode"] = "pair"
            scene["primary_subject"] = "The two adults in frame"
            scene["primary_action"] = "Megan explains the idea while her husband listens in silence"
            scene["visual_anchor"] = plan_attention or scene.get("visual_anchor") or "the fork lying down beside the cold pasta"

        return scene

    def _extract_visual_concept(self, scene_data: dict, artistic_intent: str = "") -> dict:
        """
        Extract unique visual concept from scene data to make each prompt distinct.
        This is the ROOT FIX for prompt similarity issues.

        Returns dict with:
        - visual_focus: The ONE thing viewer's eye lands on first
        - visual_metaphor: Visual equivalent of any metaphor in SRT
        - concrete_props: 2-3 specific props that MUST appear
        - body_language_key: Defining body language
        - emotional_visual: How to show emotion through composition/lighting/color
        """
        srt_text = str(scene_data.get("srt_text") or "").strip()
        primary_action = str(scene_data.get("primary_action") or "").strip()
        visual_anchor = str(scene_data.get("visual_anchor") or "").strip()
        primary_subject = str(scene_data.get("primary_subject") or "").strip()

        if not srt_text:
            return {}

        prompt = f"""You are a visual director translating narration into concrete visual concepts.

SRT NARRATION: {srt_text}
ARTISTIC INTENT: {artistic_intent or 'Convey the narration visually'}
CURRENT VISUAL ANCHOR: {visual_anchor}
CURRENT PRIMARY ACTION: {primary_action}
CURRENT PRIMARY SUBJECT: {primary_subject}

Extract the UNIQUE visual concept for this specific scene that makes it DIFFERENT from other scenes:

1. visual_focus: The ONE thing the viewer's eye should land on first (a gesture, object, expression, spatial relationship). Must be specific to THIS narration, not generic.

2. visual_metaphor: If the SRT uses metaphor or abstract concept, what is the concrete visual equivalent? Leave empty if narration is literal.

3. concrete_props: List 2-3 specific props/objects that MUST appear to convey this exact narration. Be specific (not "phone" but "phone screen showing empty chat window").

4. body_language_key: The defining body language or posture that conveys the emotion. Be specific (not "sad posture" but "shoulders drawn inward, hand gripping fabric over chest").

5. emotional_visual: How to show the emotion through composition, lighting, or color contrast (not just facial expression). Be specific about visual technique.

CRITICAL: Each field must be UNIQUE to this narration. Avoid generic descriptions that could apply to any scene.

Return JSON only:
{{
    "visual_focus": "one clear focal point specific to this narration",
    "visual_metaphor": "visual metaphor or empty string",
    "concrete_props": ["specific prop 1", "specific prop 2"],
    "body_language_key": "defining gesture or posture",
    "emotional_visual": "how composition/lighting/color conveys emotion"
}}
"""

        # Call API with retry
        for retry in range(2):
            response = self._call_api(prompt, temperature=0.3, max_tokens=512)
            if response:
                data = self._extract_json(response)
                if data and isinstance(data, dict):
                    # Validate and clean
                    result = {
                        "visual_focus": str(data.get("visual_focus") or "").strip(),
                        "visual_metaphor": str(data.get("visual_metaphor") or "").strip(),
                        "concrete_props": data.get("concrete_props") or [],
                        "body_language_key": str(data.get("body_language_key") or "").strip(),
                        "emotional_visual": str(data.get("emotional_visual") or "").strip(),
                    }

                    # Ensure concrete_props is a list
                    if isinstance(result["concrete_props"], str):
                        result["concrete_props"] = [result["concrete_props"]]

                    return result

            time.sleep(1)

        # Fallback: extract from existing fields
        return {
            "visual_focus": visual_anchor or primary_subject,
            "visual_metaphor": "",
            "concrete_props": [visual_anchor] if visual_anchor else [],
            "body_language_key": primary_action,
            "emotional_visual": "",
        }

    def _build_reference_files(self, characters_used: str, location_used: str, char_image_lookup: dict, loc_image_lookup: dict) -> list:
        refs = []
        if self._is_styled and str(characters_used or "").strip() == "nv1":
            img = char_image_lookup.get("nv1", "nv1.png") or "nv1.png"
            if img not in refs:
                refs.append(img)
        for cid in [c.strip() for c in str(characters_used or "").split(",") if c.strip() and c.strip() != "[]"]:
            img = char_image_lookup.get(cid, f"{cid}.png")
            if img and img not in refs:
                refs.append(img)
        loc_id = str(location_used or "").strip()
        if loc_id:
            loc_img = loc_image_lookup.get(loc_id, f"{loc_id}.png")
            if loc_img and loc_img not in refs:
                refs.append(loc_img)
        return refs

    def _sanitize_story_segment(self, seg: dict, chunk_text: str) -> dict:
        seg = dict(seg or {})
        source = (chunk_text or "").lower()

        risky_terms = {
            "worksheet": ["worksheet", "work sheet"],
            "statistics": ["statistics", "stat sheet", "statistical printout"],
            "bookmarked articles": ["bookmarked article", "bookmarked articles", "highlighted article", "highlighted articles"],
            "research packet": ["research packet", "research pages", "research printout", "research printouts"],
            "research notes": ["research notes", "annotated notes", "margin notes", "underlined passages"],
            "manila folder": ["manila folder", "file folder"],
            "index cards": ["index card", "index cards", "cue card", "cue cards", "reason card", "reason cards"],
            "browser tabs": ["browser tabs", "multiple tabs", "tabs open"],
        }

        def _filter_unsupported_items(items):
            filtered = []
            for item in items or []:
                text = str(item or "").strip()
                if not text:
                    continue
                lower = text.lower()
                blocked = False
                for variants in risky_terms.values():
                    if any(v in lower for v in variants) and not any(v in source for v in variants):
                        blocked = True
                        break
                if not blocked:
                    filtered.append(text)
            return filtered

        key_elements = seg.get("key_elements", [])
        if isinstance(key_elements, str):
            try:
                key_elements = json.loads(key_elements) if key_elements.startswith("[") else [key_elements]
            except:
                key_elements = [key_elements]
        seg["key_elements"] = _filter_unsupported_items(key_elements)

        continuity_markers = seg.get("continuity_markers", [])
        if isinstance(continuity_markers, str):
            try:
                continuity_markers = json.loads(continuity_markers) if continuity_markers.startswith("[") else [continuity_markers]
            except:
                continuity_markers = [continuity_markers]
        seg["continuity_markers"] = _filter_unsupported_items(continuity_markers)

        forbidden = seg.get("forbidden_inventions", [])
        if isinstance(forbidden, str):
            try:
                forbidden = json.loads(forbidden) if forbidden.startswith("[") else [forbidden]
            except:
                forbidden = [forbidden]
        existing_lower = " ".join(str(x).lower() for x in forbidden)
        for label in risky_terms.keys():
            if label not in existing_lower:
                forbidden.append(f"no {label} unless stated in SRT")
        seg["forbidden_inventions"] = forbidden

        if not seg.get("dramatic_question"):
            seg["dramatic_question"] = "What changes emotionally or relationally in this segment?"
        if not seg.get("emotional_shift"):
            seg["emotional_shift"] = "emotion tightens across the segment"
        if not seg.get("visual_arc"):
            seg["visual_arc"] = "progress from setup to pressure to visible consequence"

        return seg

    def _audit_cinematic_sequences(self, director_plan: list, scene_plans: list = None) -> list:
        """Audit sequence flow locally so the pipeline can scale beyond one-off prompt luck."""
        from collections import defaultdict, Counter

        plan_lookup = {int(p.get("scene_id", 0)): dict(p) for p in (scene_plans or []) if p.get("scene_id")}
        by_sequence = defaultdict(list)
        for scene in director_plan or []:
            seq_id = str(scene.get("sequence_id") or f"scene_{scene.get('scene_id')}")
            by_sequence[seq_id].append(dict(scene))

        audits = []
        for seq_id, items in by_sequence.items():
            items = sorted(items, key=lambda x: int(x.get("scene_id", 0) or 0))
            merged = []
            for item in items:
                scene_id = int(item.get("scene_id", 0) or 0)
                plan = plan_lookup.get(scene_id, {})
                combined = dict(item)
                combined.update({k: v for k, v in plan.items() if v not in ("", None, [])})
                merged.append(combined)

            issues = []
            score = 10.0

            roles = [str(x.get("sequence_role", "")).strip() for x in merged]
            functions = [str(x.get("shot_function", "")).strip() for x in merged]
            kinds = [str(x.get("scene_kind", "")).strip() for x in merged]
            modes = [str(x.get("subject_mode", "")).strip() for x in merged]
            anchors = [str(x.get("visual_anchor", "")).strip().lower() for x in merged if str(x.get("visual_anchor", "")).strip()]

            repeated_anchor_counts = {a: c for a, c in Counter(anchors).items() if c >= 2}
            if any(c >= 3 for c in repeated_anchor_counts.values()):
                issues.append("anchor repetition too high")
                score -= 1.5

            consecutive_kind_repeats = sum(1 for a, b in zip(kinds, kinds[1:]) if a and a == b)
            if consecutive_kind_repeats >= 2 and len(merged) >= 4:
                issues.append("too many repeated scene kinds")
                score -= 1.5

            consecutive_mode_repeats = sum(1 for a, b in zip(modes, modes[1:]) if a and a == b)
            if consecutive_mode_repeats >= 3 and len(merged) >= 4:
                issues.append("subject-mode variation too weak")
                score -= 1.0

            unique_functions = {f for f in functions if f}
            if len(unique_functions) <= 2 and len(merged) >= 4:
                issues.append("shot-function range too narrow")
                score -= 1.5

            if len(merged) >= 4 and not any(k in {"object_detail", "environment_story"} for k in kinds):
                issues.append("missing object/environment relief")
                score -= 1.0

            if roles and roles[0] != "opening":
                issues.append("sequence does not open cleanly")
                score -= 0.5
            if roles and roles[-1] != "closing":
                issues.append("sequence does not close cleanly")
                score -= 0.5

            if merged and str(merged[0].get("shot_function", "")).strip() not in {"establish", "reveal", "pressure"}:
                issues.append("opening shot function weak")
                score -= 0.5
            if merged and str(merged[-1].get("shot_function", "")).strip() not in {"aftermath", "transition", "reaction", "pressure"}:
                issues.append("closing shot function weak")
                score -= 0.5

            audits.append({
                "sequence_id": seq_id,
                "scene_ids": [int(x.get("scene_id", 0) or 0) for x in merged],
                "score": max(0.0, round(score, 2)),
                "issues": issues,
                "repeated_anchor_counts": repeated_anchor_counts,
                "kinds": kinds,
                "functions": functions,
                "modes": modes,
                "needs_refine": score < 8.5 or bool(issues),
            })

        return audits

    def _ensure_scene_spec_fields(self, scene: dict, minor_char_ids=None) -> dict:
        """Äiá»n scene-spec tá»‘i thiá»ƒu náº¿u workbook cÅ© chÆ°a cÃ³."""
        scene = dict(scene or {})
        if self._is_styled:
            return self._apply_psychology_scene_spec(scene)
        chars = scene.get("characters_used", "")
        srt_based_subject = (scene.get("visual_moment", "")[:180] or scene.get("srt_text", "")[:180] or "Primary subject in frame")
        srt_based_action = (scene.get("visual_moment", "")[:180] or scene.get("srt_text", "")[:180] or "Visible action in frame")
        primary_subject = self._sanitize_scene_spec_text(
            scene.get("primary_subject", ""),
            fallback=srt_based_subject,
        )
        scene_kind = self._normalize_scene_kind(
            scene.get("scene_kind") or self._infer_scene_kind(
                srt_text=scene.get("srt_text", ""),
                characters_used=chars,
                primary_subject=primary_subject,
            )
        )
        subject_mode = self._normalize_subject_mode(
            scene.get("subject_mode", ""),
            characters_used=chars,
            scene_kind=scene_kind,
        )
        primary_action = self._sanitize_scene_spec_text(
            scene.get("primary_action", ""),
            fallback=srt_based_action,
        )
        if self._looks_non_visual_subject(primary_subject):
            primary_subject = self._derive_visual_subject(scene, scene_kind, subject_mode, chars)
        if self._looks_multi_action(primary_action):
            primary_action = self._derive_visual_action(scene, scene_kind, subject_mode, chars)
        visual_anchor = self._sanitize_scene_spec_text(
            scene.get("visual_anchor", ""),
            fallback=primary_subject[:180],
        )
        scene_kind, subject_mode, primary_subject, primary_action, visual_anchor = self._stabilize_scene_spec(
            scene=scene,
            scene_kind=scene_kind,
            subject_mode=subject_mode,
            primary_subject=primary_subject,
            primary_action=primary_action,
            visual_anchor=visual_anchor,
        )
        must_not_show = self._sanitize_scene_spec_text(
            scene.get("must_not_show", ""),
            fallback="No overlay, no split-screen, no collage, no extra people",
        )
        scene["scene_kind"] = scene_kind
        scene["subject_mode"] = subject_mode
        scene["primary_subject"] = primary_subject
        scene["primary_action"] = primary_action
        scene["visual_anchor"] = visual_anchor
        scene["must_not_show"] = must_not_show
        return self._apply_minor_safe_scene(scene, minor_char_ids=minor_char_ids)

    def _split_long_scene_cinematically(
        self,
        scene: dict,
        char_locks: list,
        loc_locks: list
    ) -> list:
        """
        Chia má»™t scene dÃ i (> 8s) thÃ nh multiple shots má»™t cÃ¡ch nghá»‡ thuáº­t.
        Gá»i API Ä‘á»ƒ quyáº¿t Ä‘á»‹nh cÃ¡ch chia dá»±a trÃªn ná»™i dung, khÃ´ng pháº£i cÃ´ng thá»©c.

        Returns:
            List of split scenes, or None if failed
        """
        duration = scene.get("duration", 0)
        srt_text = scene.get("srt_text", "")
        visual_moment = scene.get("visual_moment", "")
        characters_used = scene.get("characters_used", "")
        location_used = scene.get("location_used", "")
        srt_start = scene.get("srt_start", "")
        srt_end = scene.get("srt_end", "")

        # TÃ­nh sá»‘ shots cáº§n thiáº¿t (target 5-7s má»—i shot)
        min_shots = max(2, int(duration / 7))
        max_shots = max(2, int(duration / 4))

        prompt = f"""You are a FILM DIRECTOR. This scene is {duration:.1f} seconds - TOO LONG for one shot (max 8s).
Split it into {min_shots}-{max_shots} DISTINCT cinematic shots.

ORIGINAL SCENE:
- Duration: {duration:.1f}s (from {srt_start} to {srt_end})
- Narration: "{srt_text}"
- Visual concept: "{visual_moment}"
- Characters: {characters_used}
- Location: {location_used}

AVAILABLE CHARACTERS:
{chr(10).join(char_locks) if char_locks else 'None'}

AVAILABLE LOCATIONS:
{chr(10).join(loc_locks) if loc_locks else 'None'}

RULES FOR SPLITTING:
1. Each shot MUST be 3-8 seconds (divide the {duration:.1f}s total)
2. Each shot must show DIFFERENT aspect: angle, focus, emotion
3. All shots together must cover the FULL narration
4. Use EXACT character/location IDs from the lists above
5. Think cinematically - what sequence of shots tells this story best?

Examples of good splits:
- Character making decision: Close-up face â†’ Insert object â†’ Wide shot reaction
- Two people talking: Speaker close-up â†’ Listener reaction â†’ Two-shot
- Action sequence: Wide establishing â†’ Medium action â†’ Close-up detail

Return JSON only:
{{
    "shots": [
        {{
            "shot_number": 1,
            "duration": 5.0,
            "srt_text": "portion of narration for this shot",
            "visual_moment": "what viewer sees - specific and purposeful",
            "shot_purpose": "why this shot at this moment",
            "characters_used": "{characters_used}",
            "location_used": "{location_used}",
            "camera": "shot type and movement"
        }}
    ]
}}"""

        response = self._call_api(prompt, temperature=0.5, max_tokens=2000)
        if not response:
            return None

        data = self._extract_json(response)
        if not data or "shots" not in data:
            return None

        shots = data["shots"]
        if not shots or len(shots) < 2:
            return None

        # Validate total duration roughly matches original
        total_split_duration = sum(s.get("duration", 0) for s in shots)
        if abs(total_split_duration - duration) > duration * 0.3:  # Allow 30% variance
            # Adjust durations proportionally
            ratio = duration / total_split_duration if total_split_duration > 0 else 1
            for shot in shots:
                shot["duration"] = round(shot.get("duration", 5) * ratio, 2)

        # Convert shots to scene format
        split_scenes = []
        for shot in shots:
            split_scene = {
                "scene_id": 0,  # Will be assigned later
                "srt_indices": scene.get("srt_indices", []),
                "srt_start": srt_start,  # Keep original timing reference
                "srt_end": srt_end,
                "duration": shot.get("duration", 5.0),
                "srt_text": shot.get("srt_text", srt_text),
                "visual_moment": shot.get("visual_moment", ""),
                "shot_purpose": shot.get("shot_purpose", ""),
                "characters_used": shot.get("characters_used", characters_used),
                "location_used": shot.get("location_used", location_used),
                "camera": shot.get("camera", ""),
                "lighting": scene.get("lighting", "")
            }
            split_scenes.append(split_scene)

        return split_scenes

    # =========================================================================
    # STEP 1: PHÃ‚N TÃCH STORY
    # =========================================================================

    def step_analyze_story(
        self,
        project_dir: Path,
        code: str,
        workbook: PromptWorkbook,
        srt_entries: list,
        txt_content: str = ""
    ) -> StepResult:
        """
        Step 1: PhÃ¢n tÃ­ch story vÃ  lÆ°u vÃ o Excel.

        Output sheet: story_analysis
        - setting: Bá»‘i cáº£nh (thá»i Ä‘áº¡i, Ä‘á»‹a Ä‘iá»ƒm)
        - themes: Chá»§ Ä‘á» chÃ­nh
        - visual_style: Phong cÃ¡ch visual
        - context_lock: Prompt context chung
        """
        import time
        step_start = time.time()

        self._log("\n" + "="*60)
        self._log("[STEP 1/7] PhÃ¢n tÃ­ch story...")
        self._log("="*60)

        # Check if already done
        try:
            existing = workbook.get_story_analysis()
            if existing and existing.get("setting"):
                self._log("  -> ÄÃ£ cÃ³ story_analysis, skip!")
                workbook.update_step_status("step_1", "COMPLETED", 1, 1, "Already done")
                return StepResult("analyze_story", StepStatus.COMPLETED, "Already done")
        except:
            pass

        # Prepare story text - OPTIMIZED: Use sampled text instead of full 15k
        if txt_content:
            story_text = txt_content
        else:
            story_text = " ".join([e.text for e in srt_entries])

        # Sample text: 8k chars thay vÃ¬ 15k - tiáº¿t kiá»‡m ~50% tokens
        sampled_text = self._sample_text(story_text, total_chars=8000)
        self._log(f"  Text: {len(story_text)} chars â†’ sampled {len(sampled_text)} chars")

        # Build prompt
        if self.topic_prompts:
            prompt = self.topic_prompts.step1_analyze(sampled_text)
        else:
            prompt = f"""Analyze this story and extract key information for visual production.

NOTE: The story is provided in sampled format (beginning + middle + end) to capture the full narrative arc.

STORY (SAMPLED):
{sampled_text}

Return JSON only:
{{
    "setting": {{
        "era": "time period (e.g., 1950s, medieval, modern day)",
        "location": "primary location type",
        "atmosphere": "overall mood/atmosphere"
    }},
    "themes": ["theme1", "theme2", "theme3"],
    "visual_style": {{
        "cinematography": "visual style description",
        "color_palette": "dominant colors",
        "lighting": "lighting style"
    }},
    "context_lock": "A single sentence describing the visual world (used as prefix for all image prompts)"
}}
"""

        # Call API
        response = self._call_api(prompt, temperature=0.5)
        if not response:
            self._log("  ERROR: API call failed!", "ERROR")
            return StepResult("analyze_story", StepStatus.FAILED, "API call failed")

        # Parse response
        data = self._extract_json(response)
        if not data:
            self._log("  ERROR: Could not parse JSON!", "ERROR")
            return StepResult("analyze_story", StepStatus.FAILED, "JSON parse failed")

        if self._is_styled:
            profile = self.psychology_style_profile or {}
            data.setdefault("visual_style", {})
            if isinstance(data["visual_style"], dict):
                data["visual_style"]["cinematography"] = profile.get("image_style", data["visual_style"].get("cinematography", ""))
                data["visual_style"]["color_palette"] = profile.get("palette", data["visual_style"].get("color_palette", ""))
            themes = data.get("themes") or []
            if isinstance(themes, (list, tuple)):
                theme_text = ", ".join(str(t).strip() for t in themes if str(t).strip())
            else:
                theme_text = str(themes or "").strip()
            setting = data.get("setting") if isinstance(data.get("setting"), dict) else {}
            atmosphere = str(setting.get("atmosphere", "") or "").strip()
            location = str(setting.get("location", "") or "").strip()
            semantic_context = str(data.get("context_lock", "") or "").strip()
            if not semantic_context or semantic_context == profile.get("image_style", ""):
                semantic_parts = [theme_text, location, atmosphere]
                semantic_context = "; ".join(part for part in semantic_parts if part)
            default_context = "educational tension, cause-and-effect, and concrete everyday behavior" if self.topic == "finance" else "psychological tension, emotional cause-and-effect, and concrete everyday behavior"
            data["context_lock"] = semantic_context or default_context
            data["psychology_style_name"] = profile.get("style_name", "")

        # Save to Excel
        try:
            workbook.save_story_analysis(data)
            workbook.save()
            self._log(f"  -> Saved story_analysis to Excel")
            self._log(f"     Setting: {data.get('setting', {}).get('era', 'N/A')}, {data.get('setting', {}).get('location', 'N/A')}")
            self._log(f"     Context: {data.get('context_lock', 'N/A')[:80]}...")

            # TRACKING: Cáº­p nháº­t tráº¡ng thÃ¡i vá»›i thá»i gian
            elapsed = int(time.time() - step_start)
            workbook.update_step_status("step_1", "COMPLETED", 1, 1,
                f"{elapsed}s - {data.get('context_lock', '')[:40]}...")

            return StepResult("analyze_story", StepStatus.COMPLETED, "Success", data)
        except Exception as e:
            self._log(f"  ERROR: Could not save to Excel: {e}", "ERROR")
            elapsed = int(time.time() - step_start)
            workbook.update_step_status("step_1", "ERROR", 0, 0, f"{elapsed}s - {str(e)[:80]}")
            return StepResult("analyze_story", StepStatus.FAILED, str(e))

    # =========================================================================
    # STEP 2: PHÃ‚N TÃCH Ná»˜I DUNG CON (STORY SEGMENTS)
    # =========================================================================

    def step_analyze_story_segments(
        self,
        project_dir: Path,
        code: str,
        workbook: PromptWorkbook,
        srt_entries: list,
        txt_content: str = ""
    ) -> StepResult:
        """
        Step 1.5: PhÃ¢n tÃ­ch cÃ¢u chuyá»‡n thÃ nh cÃ¡c ná»™i dung con (segments).

        Logic top-down:
        1. XÃ¡c Ä‘á»‹nh cÃ¡c pháº§n ná»™i dung chÃ­nh trong cÃ¢u chuyá»‡n
        2. Má»—i pháº§n cáº§n truyá»n táº£i thÃ´ng Ä‘iá»‡p gÃ¬
        3. Má»—i pháº§n cáº§n bao nhiÃªu áº£nh Ä‘á»ƒ thá»ƒ hiá»‡n Ä‘áº§y Ä‘á»§
        4. Æ¯á»›c tÃ­nh thá»i gian tá»« SRT

        Output sheet: story_segments
        """
        import time
        step_start = time.time()

        self._log("\n" + "="*60)
        self._log("[STEP 2/7] PhÃ¢n tÃ­ch ná»™i dung con (story segments)...")
        self._log("="*60)

        # Check if already done
        try:
            existing = workbook.get_story_segments()
            if existing and len(existing) > 0:
                self._log(f"  -> ÄÃ£ cÃ³ {len(existing)} segments, skip!")
                workbook.update_step_status("step_2", "COMPLETED", len(existing), len(existing), "Already done")
                return StepResult("analyze_story_segments", StepStatus.COMPLETED, "Already done")
        except:
            pass

        # TRACKING: Khá»Ÿi táº¡o SRT coverage Ä‘á»ƒ Ä‘á»‘i chiáº¿u
        self._log(f"  Khá»Ÿi táº¡o SRT coverage tracking...")
        workbook.init_srt_coverage(srt_entries)

        # Read context from previous step
        story_analysis = {}
        try:
            story_analysis = workbook.get_story_analysis() or {}
        except:
            pass

        context_lock = story_analysis.get("context_lock", "")
        themes = story_analysis.get("themes", [])

        # Prepare story text - Use FULL SRT with index markers for accurate segmentation
        # Step 2 needs to see ALL content to create correct segments
        if txt_content:
            story_text = txt_content
        else:
            story_text = " ".join([e.text for e in srt_entries])

        # Build indexed SRT text so AI can create accurate srt_range_start/end
        indexed_srt = ""
        for i, entry in enumerate(srt_entries, 1):
            indexed_srt += f"[{i}] {entry.text}\n"

        # For very long SRT (>20k chars), split into 2 API calls
        # DeepSeek context ~32k: indexed_srt typically ~60 chars/entry
        srt_text_for_prompt = indexed_srt
        needs_split = len(indexed_srt) > 20000
        self._log(f"  Text: {len(story_text)} chars, indexed SRT: {len(indexed_srt)} chars"
                  f"{' (will split into 2 calls)' if needs_split else ''}")

        # TÃ­nh tá»•ng thá»i gian tá»« SRT
        total_duration = 0
        if srt_entries:
            try:
                # Parse end time cá»§a entry cuá»‘i
                last_entry = srt_entries[-1]
                end_time = last_entry.end_time  # Format: "00:01:30,500"
                parts = end_time.replace(',', ':').split(':')
                total_duration = int(parts[0]) * 3600 + int(parts[1]) * 60 + int(parts[2]) + int(parts[3]) / 1000
            except:
                total_duration = len(srt_entries) * 3  # Æ¯á»›c tÃ­nh 3s/entry

        self._log(f"  Tá»•ng thá»i gian SRT: {total_duration:.1f}s ({len(srt_entries)} entries)")

        # Build prompt - OPTIMIZED: Produce richer segment insights for later steps
        def _build_segment_prompt(srt_content, entry_start, entry_end, total_entries, is_part=False, part_label=""):
            if self.topic_prompts:
                return self.topic_prompts.segment_prompt(
                    srt_content=srt_content,
                    entry_start=entry_start,
                    entry_end=entry_end,
                    total_entries=total_entries,
                    total_duration=total_duration,
                    context_lock=context_lock,
                    themes=themes,
                    is_part=is_part,
                    part_label=part_label,
                )
            part_note = f"\nNOTE: This is {part_label}. Create segments ONLY for SRT [{entry_start}] to [{entry_end}]." if is_part else ""
            return f"""Analyze this story and divide it into content segments for video creation.

IMPORTANT: Your segment analysis will be used by later steps to create visuals WITHOUT re-reading the full story.
So make your "message" and "key_elements" DETAILED enough to guide visual creation.

STORY CONTEXT:
{context_lock}

THEMES: {', '.join(themes) if themes else 'Not specified'}

TOTAL DURATION: {total_duration:.1f} seconds
TOTAL SRT ENTRIES: {total_entries}
{part_note}

FULL SRT CONTENT (with index numbers):
{srt_content}

TASK: Divide the story into logical segments. Each segment is a distinct part of the narrative.

CRITICAL REQUIREMENT:
- Your segments MUST cover ALL SRT entries from [{entry_start}] to [{entry_end}]
- First segment starts at srt_range_start: {entry_start}
- Last segment MUST end at srt_range_end: {entry_end}
- NO gaps between segments (segment N ends where segment N+1 starts)
- Use the [index] numbers to set accurate srt_range_start and srt_range_end

For each segment, provide DETAILED information (this will guide image creation):
1. message: The narrative purpose - what story is being told? What happens?
2. key_elements: List of VISUAL elements (characters, locations, objects, actions, emotions)
3. visual_summary: A 2-3 sentence description of what images should show for this segment
4. mood: The emotional tone (tense, warm, sad, hopeful, dramatic, etc.)
5. characters_involved: Which characters appear in this segment
6. dramatic_question: What tension, uncertainty, or dramatic question drives this segment?
7. emotional_shift: How the emotion changes across the segment
8. visual_arc: How the images should progress across the segment like a mini-sequence
9. continuity_markers: Concrete recurring visual anchors that should carry across the segment
10. forbidden_inventions: Concrete props/documents/details the later steps must NOT invent unless the SRT explicitly says them

GUIDELINES:
- Each segment typically covers 10-30 SRT entries (a distinct narrative beat)
- Each segment should have 3-6 images
- Important emotional moments may need more images
- Action sequences need more images than dialogue
- Think like a filmmaker building a sequence, not like a summarizer listing facts.
- The segment must feel playable as a short scene block with setup, pressure, reveal, reaction, or aftermath.
- DO NOT invent paperwork, statistics, notes, bookmarked articles, research packets, folders, screens, or props unless they are clearly supported by the SRT itself.
- If the SRT is abstract or conversational, describe only the strongest grounded visual truth that can be shown on screen.

Return JSON only:
{{
    "segments": [
        {{
            "segment_id": 1,
            "segment_name": "Opening/Introduction",
            "message": "DETAILED narrative: what happens, who is involved, what's the conflict/emotion",
            "key_elements": ["character doing action", "specific location", "emotional state", "important object"],
            "visual_summary": "2-3 sentences describing what the images should show.",
            "mood": "melancholic/hopeful/tense/etc",
            "characters_involved": ["main character", "supporting character"],
            "dramatic_question": "What is changing or being threatened here?",
            "emotional_shift": "from guarded calm to emotional fracture",
            "visual_arc": "begin on shared domestic geometry, tighten into emotional distance, end on a revealing object or silence beat",
            "continuity_markers": ["oak table", "cold pasta", "open laptop"],
            "forbidden_inventions": ["no paperwork unless stated in SRT", "no statistics unless stated in SRT"],
            "image_count": 3,
            "estimated_duration": 15.0,
            "srt_range_start": {entry_start},
            "srt_range_end": 10,
            "importance": "high/medium/low"
        }}
    ],
    "total_images": 20,
    "summary": "Brief overview of the story structure"
}}
"""

        if needs_split:
            # Split into chunks of ~100 SRT entries each for reliable segmentation
            CHUNK_SIZE = 100
            n_chunks = max(1, -(-len(srt_entries) // CHUNK_SIZE))  # ceiling division
            self._log(f"  Splitting into {n_chunks} chunks (~{CHUNK_SIZE} entries each)")

            all_segments = []
            for chunk_i in range(n_chunks):
                c_start = chunk_i * CHUNK_SIZE
                c_end = min((chunk_i + 1) * CHUNK_SIZE, len(srt_entries))
                chunk_entries = srt_entries[c_start:c_end]

                srt_chunk_text = "\n".join(
                    f"[{c_start + j + 1}] {e.text}" for j, e in enumerate(chunk_entries)
                )

                self._log(f"  Chunk {chunk_i+1}/{n_chunks}: SRT {c_start+1}-{c_end}...")
                chunk_prompt = _build_segment_prompt(
                    srt_chunk_text, c_start + 1, c_end, len(srt_entries),
                    is_part=True,
                    part_label=f"Part {chunk_i+1} of {n_chunks} (SRT {c_start+1}-{c_end})"
                )

                MAX_RETRIES = 3
                chunk_data = None
                for retry in range(MAX_RETRIES):
                    resp = self._call_api(chunk_prompt, temperature=0.3, max_tokens=8192)
                    if resp:
                        chunk_data = self._extract_json(resp)
                        if chunk_data and "segments" in chunk_data:
                            self._log(f"    -> {len(chunk_data['segments'])} segments")
                            break
                    import time
                    time.sleep(2 ** retry)

                if chunk_data and "segments" in chunk_data:
                    all_segments.extend(chunk_data["segments"])
                else:
                    # Fallback for this chunk
                    self._log(f"    -> FALLBACK for chunk {chunk_i+1}", "WARN")
                    chunk_dur = sum(1 for _ in chunk_entries) * (total_duration / len(srt_entries))
                    chunk_images = max(1, int(chunk_dur / 6.5))
                    chunk_text = " ".join(e.text.strip() for e in chunk_entries)
                    all_segments.append({
                        "segment_id": 0,
                        "segment_name": chunk_text.split('.')[0][:60] or f"Part {chunk_i+1}",
                        "message": chunk_text[:500],
                        "key_elements": [],
                        "dramatic_question": "",
                        "emotional_shift": "",
                        "visual_arc": "",
                        "continuity_markers": [],
                        "forbidden_inventions": [],
                        "image_count": chunk_images,
                        "srt_range_start": c_start + 1,
                        "srt_range_end": c_end,
                        "importance": "medium"
                    })

            # Re-number segment IDs
            for i, seg in enumerate(all_segments):
                seg["segment_id"] = i + 1

            if all_segments:
                data = {
                    "segments": all_segments,
                    "total_images": sum(s.get("image_count", 0) for s in all_segments),
                    "summary": f"Merged from {n_chunks}-part analysis"
                }
            else:
                data = None
        else:
            # Single call - text fits in one API call
            prompt = _build_segment_prompt(indexed_srt, 1, len(srt_entries), len(srt_entries))

            # Call API
            response = self._call_api(prompt, temperature=0.3, max_tokens=8192)
            if not response:
                self._log("  ERROR: API call failed!", "ERROR")
                return StepResult("analyze_story_segments", StepStatus.FAILED, "API call failed")

            # Parse response
            data = self._extract_json(response)

        if not data or "segments" not in data:
            self._log("  ERROR: Could not parse segments from API!", "ERROR")
            self._log(f"  API Response (first 500 chars): {response[:500] if response else 'None'}", "DEBUG")

            # === FALLBACK: Táº¡o segments Ä‘Æ¡n giáº£n dá»±a trÃªn SRT ===
            self._log("  -> Creating FALLBACK segments based on SRT duration...")
            total_srt = len(srt_entries)
            # Parse end_time from last SRT entry
            try:
                last_entry = srt_entries[-1]
                parts = last_entry.end_time.replace(',', ':').split(':')
                total_duration = int(parts[0])*3600 + int(parts[1])*60 + int(parts[2]) + int(parts[3])/1000
            except:
                total_duration = len(srt_entries) * 3  # Fallback: 3s per entry

            # TÃ­nh sá»‘ segments (~60s má»—i segment, ~12 áº£nh)
            num_segments = max(1, int(total_duration / 60))
            entries_per_seg = max(1, total_srt // num_segments)
            images_per_seg = max(1, int(60 / 5))  # ~12 áº£nh per 60s

            segments = []
            for i in range(num_segments):
                seg_start = i * entries_per_seg + 1
                seg_end = min((i + 1) * entries_per_seg, total_srt)
                if i == num_segments - 1:
                    seg_end = total_srt  # Last segment gets all remaining

                segments.append({
                    "segment_id": i + 1,
                    "segment_name": f"Part {i + 1}",
                    "message": f"Story segment {i + 1}",
                    "key_elements": [],
                    "dramatic_question": "",
                    "emotional_shift": "",
                    "visual_arc": "",
                    "continuity_markers": [],
                    "forbidden_inventions": [],
                    "image_count": images_per_seg,
                    "srt_range_start": seg_start,
                    "srt_range_end": seg_end
                })

            self._log(f"  -> Created {len(segments)} fallback segments")
            data = {"segments": segments}

        segments = data["segments"]
        total_srt = len(srt_entries)

        # Normalize segments to ensure continuous, gap-free coverage across the full SRT range.
        normalized_segments = []
        cursor = 1
        for seg in sorted(segments, key=lambda x: int(x.get("srt_range_start", 0) or 0)):
            if cursor > total_srt:
                break

            try:
                seg_end = int(seg.get("srt_range_end", cursor) or cursor)
            except:
                seg_end = cursor

            seg_end = max(cursor, min(total_srt, seg_end))
            seg_start = cursor

            chunk_entries = srt_entries[seg_start - 1:seg_end]
            chunk_text = " ".join(e.text.strip() for e in chunk_entries if e.text.strip())
            fallback_name = chunk_text.split('.')[0][:60].strip() if chunk_text else f"Part {len(normalized_segments) + 1}"

            normalized = dict(seg)
            normalized["segment_id"] = len(normalized_segments) + 1
            normalized["srt_range_start"] = seg_start
            normalized["srt_range_end"] = seg_end
            normalized["segment_name"] = (normalized.get("segment_name") or "").strip() or fallback_name
            normalized["message"] = (normalized.get("message") or "").strip() or chunk_text[:500] or f"Story segment {len(normalized_segments) + 1}"
            normalized["key_elements"] = normalized.get("key_elements") or []
            normalized["image_count"] = max(1, int(normalized.get("image_count", 1) or 1))
            if "estimated_duration" not in normalized:
                normalized["estimated_duration"] = (seg_end - seg_start + 1) * (total_duration / total_srt)
            normalized = self._sanitize_story_segment(normalized, chunk_text)

            normalized_segments.append(normalized)
            cursor = seg_end + 1

        if cursor <= total_srt:
            remaining_entries = srt_entries[cursor - 1:total_srt]
            remaining_text = " ".join(e.text.strip() for e in remaining_entries if e.text.strip())
            remaining_duration = len(remaining_entries) * (total_duration / total_srt)
            normalized_segments.append({
                "segment_id": len(normalized_segments) + 1,
                "segment_name": remaining_text.split('.')[0][:60].strip() or f"Part {len(normalized_segments) + 1}",
                "message": remaining_text[:500] or "Continuing the narrative",
                "key_elements": [],
                "dramatic_question": "",
                "emotional_shift": "",
                "visual_arc": "",
                "continuity_markers": [],
                "forbidden_inventions": [],
                "image_count": max(1, int(remaining_duration / 6.5)),
                "estimated_duration": remaining_duration,
                "srt_range_start": cursor,
                "srt_range_end": total_srt,
                "importance": "medium",
            })

        segments = normalized_segments
        data["segments"] = segments

        # VALIDATION: Check if segments cover all SRT entries
        if segments:
            last_seg = segments[-1]
            last_srt_end = last_seg.get("srt_range_end", 0)

            if last_srt_end < total_srt:
                # FIX: Extend coverage to include all SRT entries
                missing_entries = total_srt - last_srt_end
                self._log(f"  [WARN] Segments only cover SRT 1-{last_srt_end}, missing {missing_entries} entries")
                self._log(f"  -> Auto-fixing: extending coverage to SRT {total_srt}")

                # Calculate how many additional images needed (~5s per image)
                missing_duration = missing_entries * (total_duration / total_srt)
                additional_images = max(1, int(missing_duration / 5))

                # Either extend last segment or add new segment
                if missing_entries <= 50:  # Small gap - extend last segment
                    segments[-1]["srt_range_end"] = total_srt
                    segments[-1]["image_count"] = segments[-1].get("image_count", 1) + additional_images
                    self._log(f"     -> Extended last segment to SRT {total_srt} (+{additional_images} images)")
                else:
                    # Larger gap - add new segment(s)
                    remaining = missing_entries
                    current_start = last_srt_end + 1
                    seg_id = len(segments) + 1

                    while remaining > 0:
                        chunk = min(remaining, 100)  # Max 100 entries per segment
                        chunk_images = max(1, int(chunk * (total_duration / total_srt) / 5))
                        # Build message from actual SRT content for better context
                        chunk_srt_entries = srt_entries[current_start - 1 : current_start - 1 + chunk]
                        chunk_text = " ".join(e.text.strip() for e in chunk_srt_entries if e.text.strip())
                        chunk_message = chunk_text[:300] if chunk_text else "Continuing the narrative"
                        # Build a meaningful segment name from first sentence of SRT
                        first_sentence = chunk_text.split('.')[0][:60] if chunk_text else ""
                        seg_name = first_sentence if first_sentence else f"Continuation Part {seg_id - len(data['segments'])}"
                        new_seg = {
                            "segment_id": seg_id,
                            "segment_name": seg_name,
                            "message": chunk_message,
                            "key_elements": [],
                            "dramatic_question": "",
                            "emotional_shift": "",
                            "visual_arc": "",
                            "continuity_markers": [],
                            "forbidden_inventions": [],
                            "image_count": chunk_images,
                            "estimated_duration": chunk * (total_duration / total_srt),
                            "srt_range_start": current_start,
                            "srt_range_end": current_start + chunk - 1,
                            "importance": "medium"
                        }
                        segments.append(new_seg)
                        self._log(f"     -> Added segment {seg_id}: SRT {current_start}-{current_start + chunk - 1} ({chunk_images} images)")

                        current_start += chunk
                        remaining -= chunk
                        seg_id += 1

                data["segments"] = segments

        sanitized_segments = []
        for seg in data["segments"]:
            seg_start = int(seg.get("srt_range_start", 1) or 1)
            seg_end = int(seg.get("srt_range_end", seg_start) or seg_start)
            chunk_entries = srt_entries[max(0, seg_start - 1):min(len(srt_entries), seg_end)]
            chunk_text = " ".join(e.text.strip() for e in chunk_entries if e.text.strip())
            sanitized_segments.append(self._sanitize_story_segment(seg, chunk_text))
        data["segments"] = sanitized_segments

        # Save to Excel
        try:
            workbook.save_story_segments(data["segments"], data.get("total_images", 0), data.get("summary", ""))
            workbook.save()

            total_images = sum(s.get("image_count", 0) for s in data["segments"])
            self._log(f"  -> Saved {len(data['segments'])} segments ({total_images} total images)")
            for seg in data["segments"][:5]:
                self._log(f"     - {seg.get('segment_name')}: {seg.get('image_count')} images")

            # TRACKING: Cáº­p nháº­t vÃ  kiá»ƒm tra coverage
            coverage = workbook.update_srt_coverage_segments(data["segments"])
            self._log(f"\n  [STATS] SRT COVERAGE (sau Step 1.5):")
            self._log(f"     Total SRT: {coverage['total_srt']}")
            self._log(f"     Covered by segments: {coverage['covered_by_segment']} ({coverage['coverage_percent']}%)")

            # Determine status based on coverage
            elapsed = int(time.time() - step_start)
            if coverage['uncovered'] > 0:
                self._log(f"     [WARN] UNCOVERED: {coverage['uncovered']} entries", "WARN")
                status = "PARTIAL" if coverage['coverage_percent'] >= 50 else "ERROR"
                workbook.update_step_status("step_2", status,
                    coverage['total_srt'], coverage['covered_by_segment'],
                    f"{elapsed}s - {len(data['segments'])} segs, {coverage['uncovered']} uncovered")
            else:
                workbook.update_step_status("step_2", "COMPLETED",
                    coverage['total_srt'], coverage['covered_by_segment'],
                    f"{elapsed}s - {len(data['segments'])} segs, {total_images} imgs")

            return StepResult("analyze_story_segments", StepStatus.COMPLETED, "Success", data)
        except Exception as e:
            self._log(f"  ERROR: Could not save to Excel: {e}", "ERROR")
            elapsed = int(time.time() - step_start)
            workbook.update_step_status("step_2", "ERROR", 0, 0, f"{elapsed}s - {str(e)[:80]}")
            return StepResult("analyze_story_segments", StepStatus.FAILED, str(e))

    # =========================================================================
    # STEP 2: Táº O CHARACTERS
    # =========================================================================

    def step_create_characters(
        self,
        project_dir: Path,
        code: str,
        workbook: PromptWorkbook,
        srt_entries: list,
        txt_content: str = ""
    ) -> StepResult:
        """
        Step 2: Táº¡o characters dá»±a trÃªn story_analysis.

        Input: Äá»c story_analysis tá»« Excel
        Output sheet: characters
        """
        import time
        step_start = time.time()

        self._log("\n" + "="*60)
        self._log("[STEP 3/7] Táº¡o characters...")
        self._log("="*60)

        # Check if already done
        existing_chars = workbook.get_characters()
        if self._is_styled:
            non_loc_existing = [
                c for c in existing_chars
                if str(getattr(c, "role", "") or "").strip().lower() != "location"
            ]
            wrong_existing = [
                c for c in non_loc_existing
                if str(getattr(c, "id", "") or "").strip().lower() != "nv1"
            ]
            nv1_existing = next(
                (
                    c for c in non_loc_existing
                    if str(getattr(c, "id", "") or "").strip().lower() == "nv1"
                ),
                None,
            )
            existing_locs = [
                c for c in existing_chars
                if str(getattr(c, "role", "") or "").strip().lower() == "location"
            ]
            story_locs_existing = bool(existing_locs)
            if wrong_existing or story_locs_existing:
                workbook.clear_characters()
                existing_chars = []
                self._log(f"  -> {self.topic.title()} topic: cleared story characters/locations from characters sheet")
            elif nv1_existing:
                update = {
                    "name": f"{self.topic.title()} Reference",
                    "role": "protagonist",
                    "english_prompt": "",
                    "vietnamese_prompt": "",
                    "character_lock": f"existing local {self.topic} reference image",
                    "image_file": "nv1.png",
                    "is_child": False,
                }
                if not str(getattr(nv1_existing, "status", "") or "").strip():
                    update["status"] = "pending"
                workbook.update_character("nv1", **update)
                workbook.save()
                workbook.update_step_status("step_3", "COMPLETED", 1, 1, f"{self.topic} local reference nv1")
                self._log(f"  -> {self.topic.title()} topic: confirmed only nv1 reference character")
                return StepResult("create_characters", StepStatus.COMPLETED, f"{self.topic} local reference nv1", {"characters": [nv1_existing.to_dict()]})
        elif existing_chars and len(existing_chars) > 0:
            self._log(f"  -> ÄÃ£ cÃ³ {len(existing_chars)} characters, skip!")
            workbook.update_step_status("step_3", "COMPLETED", len(existing_chars), len(existing_chars), "Already done")
            return StepResult("create_characters", StepStatus.COMPLETED, "Already done")

        if self._is_styled:
            char = Character(
                id="nv1",
                name=f"{self.topic.title()} Reference",
                role="protagonist",
                english_prompt="",
                vietnamese_prompt="",
                character_lock=f"existing local {self.topic} reference image",
                image_file="nv1.png",
                status="pending",
                is_child=False,
            )
            workbook.add_character(char)
            workbook.save()
            workbook.update_step_status("step_3", "COMPLETED", 1, 1, f"{self.topic} local reference nv1")
            self._log(f"  -> {self.topic.title()} topic: tao nv1 metadata, khong goi API tao character prompt")
            return StepResult("create_characters", StepStatus.COMPLETED, f"{self.topic} local reference nv1", {"characters": [char.to_dict()]})

        # Read story_analysis from Excel
        story_analysis = {}
        try:
            story_analysis = workbook.get_story_analysis() or {}
        except:
            pass

        context_lock = story_analysis.get("context_lock", "")
        setting = story_analysis.get("setting", {})

        # OPTIMIZED: Táº­n dá»¥ng insights tá»« Step 1.5 (segments)
        story_segments = workbook.get_story_segments() or []

        # Build rich context tá»« segments thay vÃ¬ Ä‘á»c láº¡i full text
        segment_insights = ""
        all_characters_mentioned = set()
        all_key_elements = []

        for seg in story_segments:
            seg_name = seg.get("segment_name", "")
            message = seg.get("message", "")
            visual_summary = seg.get("visual_summary", "")
            key_elements = seg.get("key_elements", [])
            chars_involved = seg.get("characters_involved", [])
            mood = seg.get("mood", "")

            segment_insights += f"""
SEGMENT "{seg_name}":
- Story: {message}
- Visuals: {visual_summary}
- Mood: {mood}
- Characters: {', '.join(chars_involved) if isinstance(chars_involved, list) else chars_involved}
- Key elements: {', '.join(key_elements) if isinstance(key_elements, list) else key_elements}
"""
            if isinstance(chars_involved, list):
                all_characters_mentioned.update(chars_involved)
            if isinstance(key_elements, list):
                all_key_elements.extend(key_elements)

        # Chá»‰ dÃ¹ng TARGETED text tá»« SRT cho cÃ¡c segment chÃ­nh (Ä‘áº§u + giá»¯a + cuá»‘i)
        # thay vÃ¬ gá»­i full text
        targeted_srt_text = ""
        if story_segments and srt_entries:
            # Láº¥y 3 segments: Ä‘áº§u, giá»¯a, cuá»‘i
            target_segments = [story_segments[0]]
            if len(story_segments) > 2:
                target_segments.append(story_segments[len(story_segments)//2])
                target_segments.append(story_segments[-1])
            elif len(story_segments) > 1:
                target_segments.append(story_segments[-1])

            for seg in target_segments:
                srt_start = seg.get("srt_range_start", 1)
                srt_end = seg.get("srt_range_end", min(srt_start + 20, len(srt_entries)))
                # Chá»‰ láº¥y 10 entries Ä‘áº§u cá»§a má»—i segment
                entries_to_take = min(10, srt_end - srt_start + 1)
                targeted_srt_text += f"\n[From segment '{seg.get('segment_name')}']\n"
                targeted_srt_text += self._get_srt_for_range(srt_entries, srt_start, srt_start + entries_to_take - 1)

        self._log(f"  Using {len(story_segments)} segment insights + targeted SRT (~{len(targeted_srt_text)} chars)")

        # Build prompt - dÃ¹ng SEGMENT INSIGHTS thay vÃ¬ full text
        prompt = f"""Based on the story analysis below, identify all characters and create visual descriptions.

STORY CONTEXT (from Step 1):
- Era: {setting.get('era', 'Not specified')}
- Location: {setting.get('location', 'Not specified')}
- Visual style: {context_lock}

CHARACTERS TO LOOK FOR (from Step 1.5 segments):
{', '.join(all_characters_mentioned) if all_characters_mentioned else 'Analyze from story segments below'}

STORY SEGMENTS ANALYSIS (from Step 1.5 - this tells you WHAT happens and WHO is involved):
{segment_insights}

SAMPLE SRT CONTENT (for character dialogue/description details):
{targeted_srt_text[:8000] if targeted_srt_text else 'Use segment analysis above'}

For each character, provide:
1. portrait_prompt: Portrait on pure white background, 85mm lens, front-facing, Caucasian, photorealistic 8K, NO TEXT
2. character_lock: Short 10-15 word description for scene prompts
3. is_minor: true if under 18 (child, teenager, baby, etc.)

ABSOLUTE SAFETY RULES:
- Do NOT reference any celebrity, actor, singer, influencer, politician, or public figure
- Do NOT write phrases like "similar features to X", "looks like X", "resembling X", "inspired by X"
- Describe only original physical traits: age range, hair, eyes, clothing, expression, build
- Character references must be fully original and non-comparative

Return JSON:
{{
    "characters": [
        {{
            "id": "char_id",
            "name": "Name",
            "role": "protagonist/supporting/narrator",
            "portrait_prompt": "Portrait on pure white background, 85mm lens, [age]-year-old Caucasian [man/woman], [hair], [eyes], [clothing], front-facing neutral expression, photorealistic 8K, no text, no watermark",
            "character_lock": "[age] Caucasian [man/woman], [hair], [eyes], [clothing]",
            "is_minor": false
        }}
    ]
}}
"""

        # Call API
        response = self._call_api(prompt, temperature=0.5)
        if not response:
            self._log("  ERROR: API call failed!", "ERROR")
            return StepResult("create_characters", StepStatus.FAILED, "API call failed")

        # Parse response
        data = self._extract_json(response)
        if not data or "characters" not in data:
            self._log("  ERROR: Could not parse characters!", "ERROR")
            return StepResult("create_characters", StepStatus.FAILED, "JSON parse failed")

        # Save to Excel
        try:
            minor_count = 0
            char_counter = 0  # Äáº¿m Ä‘á»ƒ táº¡o ID Ä‘Æ¡n giáº£n: nv1, nv2, nv3...

            for char_data in data["characters"]:
                role = char_data.get("role", "supporting").lower()

                # Táº¡o ID Ä‘Æ¡n giáº£n vÃ  nháº¥t quÃ¡n
                if role == "narrator" or "narrator" in char_data.get("name", "").lower():
                    char_id = "nvc"  # Narrator luÃ´n lÃ  nvc
                else:
                    char_counter += 1
                    char_id = f"nv{char_counter}"  # nv1, nv2, nv3...

                # Detect tráº» vá»‹ thÃ nh niÃªn (dÆ°á»›i 18 tuá»•i)
                is_minor = char_data.get("is_minor", False)
                if isinstance(is_minor, str):
                    is_minor = is_minor.lower() in ("true", "yes", "1")

                char = Character(
                    id=char_id,
                    name=char_data.get("name", ""),
                    role=char_data.get("role", "supporting"),
                    english_prompt=self._sanitize_character_reference_text(
                        char_data.get("portrait_prompt", "")
                    ),
                    character_lock=self._sanitize_character_reference_text(
                        char_data.get("character_lock", "")
                    ),
                    vietnamese_prompt=char_data.get("vietnamese_description", ""),
                    image_file=f"{char_id}.png",
                    is_child=is_minor,
                    status="skip" if is_minor else "pending",  # Skip táº¡o áº£nh cho tráº» em
                )
                workbook.add_character(char)

                if is_minor:
                    minor_count += 1

            workbook.save()
            self._log(f"  -> Saved {len(data['characters'])} characters to Excel")
            if minor_count > 0:
                self._log(f"  -> [WARN] {minor_count} characters lÃ  tráº» em (sáº½ KHÃ”NG táº¡o áº£nh)")
            for c in data["characters"][:3]:
                minor_tag = " [MINOR]" if c.get("is_minor") else ""
                self._log(f"     - {c.get('name', 'N/A')} ({c.get('role', 'N/A')}){minor_tag}")
            if len(data["characters"]) > 3:
                self._log(f"     ... vÃ  {len(data['characters']) - 3} characters khÃ¡c")

            # Update step status with duration
            elapsed = int(time.time() - step_start)
            workbook.update_step_status("step_3", "COMPLETED", len(data['characters']), len(data['characters']),
                f"{elapsed}s - {len(data['characters'])} chars")

            return StepResult("create_characters", StepStatus.COMPLETED, "Success", data)
        except Exception as e:
            self._log(f"  ERROR: Could not save to Excel: {e}", "ERROR")
            elapsed = int(time.time() - step_start)
            workbook.update_step_status("step_3", "ERROR", 0, 0, f"{elapsed}s - {str(e)[:80]}")
            return StepResult("create_characters", StepStatus.FAILED, str(e))

    # =========================================================================
    # STEP 4: Táº O LOCATIONS
    # =========================================================================

    def step_create_locations(
        self,
        project_dir: Path,
        code: str,
        workbook: PromptWorkbook,
        srt_entries: list,
        txt_content: str = ""
    ) -> StepResult:
        """
        Step 3: Táº¡o locations dá»±a trÃªn story_analysis + characters.

        Input: Äá»c story_analysis, characters tá»« Excel
        Output sheet: locations
        """
        import time
        step_start = time.time()

        self._log("\n" + "="*60)
        self._log("[STEP 4/7] Táº¡o locations...")
        self._log("="*60)

        # Check if already done
        existing_locs = workbook.get_locations()
        if self._is_styled:
            if existing_locs:
                workbook.clear_characters()
                char = Character(
                    id="nv1",
                    name=f"{self.topic.title()} Reference",
                    role="protagonist",
                    english_prompt="",
                    vietnamese_prompt="",
                    character_lock=f"existing local {self.topic} reference image",
                    image_file="nv1.png",
                    status="pending",
                    is_child=False,
                )
                workbook.add_character(char)
                workbook.save()
                self._log(f"  -> {self.topic.title()} topic: removed story locations and kept only nv1")
            workbook.update_step_status("step_4", "COMPLETED", 0, 0, f"{self.topic} uses no generated locations")
            return StepResult("create_locations", StepStatus.COMPLETED, f"{self.topic} skips generated locations", {"locations": []})
        if existing_locs and len(existing_locs) > 0:
            self._log(f"  -> ÄÃ£ cÃ³ {len(existing_locs)} locations, skip!")
            workbook.update_step_status("step_4", "COMPLETED", len(existing_locs), len(existing_locs), "Already done")
            return StepResult("create_locations", StepStatus.COMPLETED, "Already done")

        # Read context from Excel
        story_analysis = {}
        try:
            story_analysis = workbook.get_story_analysis() or {}
        except:
            pass

        characters = workbook.get_characters()
        char_names = [c.name for c in characters] if characters else []

        context_lock = story_analysis.get("context_lock", "")
        setting = story_analysis.get("setting", {})

        # OPTIMIZED: Táº­n dá»¥ng insights tá»« Step 1.5 (segments)
        story_segments = workbook.get_story_segments() or []

        # Build rich context tá»« segments thay vÃ¬ Ä‘á»c láº¡i full text
        segment_insights = ""
        all_locations_hints = set()

        for seg in story_segments:
            seg_name = seg.get("segment_name", "")
            message = seg.get("message", "")
            visual_summary = seg.get("visual_summary", "")
            key_elements = seg.get("key_elements", [])
            mood = seg.get("mood", "")

            segment_insights += f"""
SEGMENT "{seg_name}":
- Story: {message}
- Visuals: {visual_summary}
- Mood: {mood}
- Key elements: {', '.join(key_elements) if isinstance(key_elements, list) else key_elements}
"""
            # Extract location hints tá»« key_elements
            if isinstance(key_elements, list):
                for elem in key_elements:
                    elem_lower = elem.lower()
                    if any(word in elem_lower for word in ["room", "house", "office", "street", "park", "school", "hospital", "forest", "beach", "city", "village", "building", "kitchen", "bedroom", "garden", "car", "restaurant", "cafe", "church"]):
                        all_locations_hints.add(elem)

        # Chá»‰ láº¥y targeted SRT tá»« vÃ i segment Ä‘á»ƒ cÃ³ thÃªm context
        targeted_srt_text = ""
        if story_segments and srt_entries:
            target_segments = [story_segments[0]]
            if len(story_segments) > 2:
                target_segments.append(story_segments[len(story_segments)//2])
                target_segments.append(story_segments[-1])
            elif len(story_segments) > 1:
                target_segments.append(story_segments[-1])

            for seg in target_segments:
                srt_start = seg.get("srt_range_start", 1)
                entries_to_take = min(8, len(srt_entries) - srt_start + 1)
                targeted_srt_text += f"\n[From segment '{seg.get('segment_name')}']\n"
                targeted_srt_text += self._get_srt_for_range(srt_entries, srt_start, srt_start + entries_to_take - 1)

        self._log(f"  Using {len(story_segments)} segment insights + targeted SRT (~{len(targeted_srt_text)} chars)")

        # Build prompt - dÃ¹ng SEGMENT INSIGHTS thay vÃ¬ full text
        prompt = f"""Based on the story analysis below, identify all locations and create visual descriptions.

STORY CONTEXT (from Step 1):
- Era: {setting.get('era', 'Not specified')}
- Location type: {setting.get('location', 'Not specified')}
- Visual style: {context_lock}
- Characters: {', '.join(char_names[:5])}

LOCATION HINTS (from Step 1.5 key_elements):
{', '.join(all_locations_hints) if all_locations_hints else 'Analyze from story segments below'}

STORY SEGMENTS ANALYSIS (from Step 1.5 - shows WHERE scenes take place):
{segment_insights}

SAMPLE SRT CONTENT (for location description details):
{targeted_srt_text[:6000] if targeted_srt_text else 'Use segment analysis above'}

ABSOLUTE LOCATION RULES:
- Every loc*.png is a CLEAN ENVIRONMENT REFERENCE only
- No people, no characters, no body parts, no silhouettes, no crowd
- Describe architecture, room layout, props, furniture, weather, lighting, atmosphere only
- Even if a scene usually contains a person, the location reference must show the SAME place EMPTY
- location_lock must describe the environment only, never a person or action

For each location, provide:
1. location_prompt: Full description for generating an EMPTY environment reference image
2. location_lock: Short description to use in scene prompts

Return JSON only:
{{
    "locations": [
        {{
            "id": "loc_id",
            "name": "Location Name",
            "location_prompt": "detailed empty environment description for image generation, no people, no character, no silhouette",
            "location_lock": "short environment-only description for scene prompts (10-15 words)",
            "lighting_default": "default lighting for this location"
        }}
    ]
}}
"""

        # Call API
        response = self._call_api(prompt, temperature=0.5)
        if not response:
            self._log("  ERROR: API call failed!", "ERROR")
            return StepResult("create_locations", StepStatus.FAILED, "API call failed")

        # Parse response
        data = self._extract_json(response)
        if not data or "locations" not in data:
            self._log("  ERROR: Could not parse locations!", "ERROR")
            return StepResult("create_locations", StepStatus.FAILED, "JSON parse failed")

        # Save to Excel - LÆ¯U VÃ€O SHEET CHARACTERS vá»›i id loc_xxx
        try:
            loc_counter = 0  # Äáº¿m Ä‘á»ƒ táº¡o ID Ä‘Æ¡n giáº£n: loc1, loc2, loc3...

            for loc_data in data["locations"]:
                loc_counter += 1
                loc_id = f"loc{loc_counter}"  # ÄÆ¡n giáº£n: loc1, loc2, loc3...

                # Táº¡o Character vá»›i role="location" thay vÃ¬ Location riÃªng
                loc_char = Character(
                    id=loc_id,
                    name=loc_data.get("name", ""),
                    role="location",  # ÄÃ¡nh dáº¥u lÃ  location
                    english_prompt=self._sanitize_location_reference_prompt(
                        loc_data.get("location_prompt", ""),
                        char_names=char_names,
                    ),
                    character_lock=loc_data.get("location_lock", ""),
                    vietnamese_prompt=loc_data.get("lighting_default", ""),  # DÃ¹ng field nÃ y cho lighting
                    image_file=f"{loc_id}.png",
                    status="pending",
                )
                workbook.add_character(loc_char)  # ThÃªm vÃ o characters sheet

            workbook.save()
            self._log(f"  -> Saved {len(data['locations'])} locations to characters sheet")
            for loc in data["locations"][:3]:
                self._log(f"     - {loc.get('name', 'N/A')}")

            # Update step status with duration
            elapsed = int(time.time() - step_start)
            workbook.update_step_status("step_4", "COMPLETED", len(data['locations']), len(data['locations']),
                f"{elapsed}s - {len(data['locations'])} locs")

            return StepResult("create_locations", StepStatus.COMPLETED, "Success", data)
        except Exception as e:
            self._log(f"  ERROR: Could not save to Excel: {e}", "ERROR")
            elapsed = int(time.time() - step_start)
            workbook.update_step_status("step_4", "ERROR", 0, 0, f"{elapsed}s - {str(e)[:80]}")
            return StepResult("create_locations", StepStatus.FAILED, str(e))

    # =========================================================================
    # STEP 5: Táº O DIRECTOR'S PLAN (OPTIMIZED - SEGMENT-FIRST)
    # =========================================================================

    def step_create_director_plan(
        self,
        project_dir: Path,
        code: str,
        workbook: PromptWorkbook,
        srt_entries: list
    ) -> StepResult:
        """
        Step 4: Táº¡o director's plan - OPTIMIZED vá»›i segment-first approach.

        THAY Äá»”I SO Vá»šI PHIÃŠN Báº¢N CÅ¨:
        - CÅ¨: Chia SRT theo character count (~6000 chars) â†’ batch processing
        - Má»šI: Xá»­ lÃ½ BY SEGMENT tá»« Step 1.5, táº­n dá»¥ng segment insights

        Má»—i segment Ä‘Ã£ cÃ³:
        - message: Ná»™i dung chÃ­nh cá»§a segment
        - visual_summary: MÃ´ táº£ visual cáº§n show
        - key_elements: CÃ¡c yáº¿u tá»‘ quan trá»ng
        - mood: Tone cáº£m xÃºc
        - characters_involved: NhÃ¢n váº­t xuáº¥t hiá»‡n
        - image_count: Sá»‘ scenes cáº§n táº¡o

        â†’ API chá»‰ cáº§n quyáº¿t Ä‘á»‹nh HOW to visualize, khÃ´ng cáº§n re-read toÃ n bá»™ story
        """
        # Redirect to basic version which is complete
        return self.step_create_director_plan_basic(project_dir, code, workbook, srt_entries)

    def _process_segment_sub_batch(self, seg_name, message, visual_summary, key_elements,
                                    mood, chars_involved, image_count, srt_start, srt_end,
                                    srt_entries, context_lock, char_locks, loc_locks):
        """Helper: Xá»­ lÃ½ sub-batch nhá» cá»§a segment (dÃ¹ng khi segment quÃ¡ lá»›n hoáº·c retry fail)."""
        import time

        # Láº¥y SRT text cho sub-batch
        seg_srt_text = self._get_srt_for_range(srt_entries, srt_start, srt_end)

        # TÃ­nh duration
        seg_duration = (srt_end - srt_start + 1) * 3  # ~3s per entry

        # Build character/location info
        relevant_chars = []
        if isinstance(chars_involved, list):
            for char_name in chars_involved:
                for cid, clock in char_locks.items():
                    if char_name.lower() in clock.lower() or char_name.lower() in cid.lower():
                        relevant_chars.append(f"- {cid}: {clock}")
                        break
        if not relevant_chars:
            relevant_chars = [f"- {cid}: {clock}" for cid, clock in list(char_locks.items())[:5]]

        relevant_locs = [f"- {lid}: {llock}" for lid, llock in list(loc_locks.items())[:3]]

        # Build prompt
        prompt = f"""Create {image_count} cinematic shots for this story segment.

SEGMENT: "{seg_name}"
Story: {message}
Visuals: {visual_summary}
Mood: {mood}
Key elements: {', '.join(key_elements) if isinstance(key_elements, list) else key_elements}

VISUAL STYLE: {context_lock}

CHARACTERS:
{chr(10).join(relevant_chars) if relevant_chars else 'Use generic descriptions'}

LOCATIONS:
{chr(10).join(relevant_locs) if relevant_locs else 'Use generic descriptions'}

SRT ({srt_end - srt_start + 1} entries):
{seg_srt_text[:3000]}

TASK: Create EXACTLY {image_count} scenes (~{seg_duration/image_count:.1f}s each)

Return JSON only:
{{
    "scenes": [
        {{
            "scene_id": 1,
            "srt_indices": [list of SRT indices],
            "srt_start": "00:00:00,000",
            "srt_end": "00:00:05,000",
            "duration": {seg_duration/image_count:.1f},
            "srt_text": "narration",
            "visual_moment": "specific visual",
            "characters_used": "nv_xxx",
            "location_used": "loc_xxx",
            "camera": "shot type",
            "lighting": "lighting"
        }}
    ]
}}
"""

        # Call API with retry (simpler - 3 retries)
        MAX_RETRIES = 3
        for retry in range(MAX_RETRIES):
            response = self._call_api(prompt, temperature=0.5, max_tokens=4096)
            if response:
                data = self._extract_json(response)
                if data and "scenes" in data:
                    self._log(f"     -> Sub-batch got {len(data['scenes'])} scenes")
                    return data["scenes"]
            time.sleep(2 ** retry)

        # Náº¿u fail, tráº£ vá» empty list (khÃ´ng táº¡o fallback cho sub-batch)
        self._log(f"     -> Sub-batch failed after {MAX_RETRIES} retries", "WARNING")
        return []

    def _step_create_director_plan_legacy(
        self,
        project_dir: Path,
        code: str,
        workbook: PromptWorkbook,
        srt_entries: list
    ) -> StepResult:
        """
        Legacy fallback: Xá»­ lÃ½ SRT theo character-batch khi khÃ´ng cÃ³ segments.
        Chá»‰ dÃ¹ng khi Step 1.5 chÆ°a cháº¡y.
        """
        self._log("  Using legacy character-batch mode...")

        story_analysis = workbook.get_story_analysis() or {}
        characters = workbook.get_characters()
        minor_char_ids = {c.id for c in characters if getattr(c, "is_child", False)}
        locations = workbook.get_locations()
        minor_char_ids = {c.id for c in characters if getattr(c, "is_child", False)}
        context_lock = story_analysis.get("context_lock", "")

        char_locks = [f"- {c.id}: {c.character_lock}" for c in characters if c.character_lock]
        loc_locks = [f"- {loc.id}: {loc.location_lock}" for loc in locations if hasattr(loc, 'location_lock') and loc.location_lock]

        # Build valid ID sets for normalization
        valid_char_ids = {c.id for c in characters}
        valid_loc_ids = {loc.id for loc in locations}

        # Chia SRT entries thÃ nh batches ~6000 chars
        MAX_BATCH_CHARS = 6000
        batches = []
        current_batch = []
        current_chars = 0

        for i, entry in enumerate(srt_entries):
            entry_text = f"[{i+1}] {entry.start_time} --> {entry.end_time}\n{entry.text}\n\n"
            entry_len = len(entry_text)

            if current_chars + entry_len > MAX_BATCH_CHARS and current_batch:
                batches.append(current_batch)
                current_batch = []
                current_chars = 0

            current_batch.append((i, entry))
            current_chars += entry_len

        if current_batch:
            batches.append(current_batch)

        all_scenes = []
        scene_id_counter = 1

        for batch_idx, batch_entries in enumerate(batches):
            batch_start = batch_entries[0][0]
            batch_end = batch_entries[-1][0]

            srt_text = ""
            for idx, entry in batch_entries:
                srt_text += f"[{idx+1}] {entry.start_time} --> {entry.end_time}\n{entry.text}\n\n"

            prompt = f"""Create cinematic shots for this content.

CONTEXT: {context_lock}

CHARACTERS:
{chr(10).join(char_locks[:5]) if char_locks else 'Generic'}

LOCATIONS:
{chr(10).join(loc_locks[:3]) if loc_locks else 'Generic'}

SRT (entries {batch_start+1}-{batch_end+1}):
{srt_text}

Create scenes (~8s each). Return JSON:
{{"scenes": [{{"scene_id": {scene_id_counter}, "srt_indices": [], "srt_start": "", "srt_end": "", "duration": 8, "srt_text": "", "visual_moment": "", "characters_used": "", "location_used": "", "camera": "", "lighting": ""}}]}}
"""

            response = self._call_api(prompt, temperature=0.5, max_tokens=4096)
            data = self._extract_json(response) if response else None

            if data and "scenes" in data:
                for scene in data["scenes"]:
                    scene["scene_id"] = scene_id_counter

                    # Normalize IDs tá»« API response
                    raw_chars = scene.get("characters_used", "")
                    raw_loc = scene.get("location_used", "")
                    scene["characters_used"] = self._normalize_character_ids(raw_chars, valid_char_ids)
                    scene["location_used"] = self._normalize_location_id(raw_loc, valid_loc_ids)

                    all_scenes.append(scene)
                    scene_id_counter += 1

        if not all_scenes:
            return StepResult("create_director_plan", StepStatus.FAILED, "No scenes created")

        workbook.save_director_plan(all_scenes)
        workbook.save()
        return StepResult("create_director_plan", StepStatus.COMPLETED, "Success (legacy)", {"scenes": all_scenes})

    # =========================================================================
    # STEP 5 BASIC: Táº O DIRECTOR'S PLAN (SEGMENT-BASED, NO 8s LIMIT)
    # =========================================================================

    def step_create_director_plan_basic(
        self,
        project_dir: Path,
        code: str,
        workbook: PromptWorkbook,
        srt_entries: list,
    ) -> StepResult:
        """
        Step 4 BASIC: Táº¡o director's plan dá»±a trÃªn story segments.

        KhÃ¡c vá»›i phiÃªn báº£n thÆ°á»ng:
        - KHÃ”NG giá»›i háº¡n 8s
        - Sá»‘ scenes = tá»•ng image_count tá»« táº¥t cáº£ segments
        - Duration = segment_duration / image_count
        - Dá»±a hoÃ n toÃ n vÃ o káº¿ hoáº¡ch tá»« Step 1.5

        Input: story_segments, characters, locations, SRT
        Output: director_plan vá»›i sá»‘ scenes = planned images
        """
        self._log("\n" + "="*60)
        self._log("[STEP 5/7] Creating director's plan (segment-based)...")
        self._log("="*60)

        # Check if already done
        try:
            existing_plan = workbook.get_director_plan()
            if existing_plan and len(existing_plan) > 0:
                self._log(f"  -> Already has {len(existing_plan)} scenes, skip!")
                return StepResult("create_director_plan_basic", StepStatus.COMPLETED, "Already done")
        except:
            pass

        # Read story segments (REQUIRED for basic mode)
        story_segments = workbook.get_story_segments() or []
        if not story_segments:
            self._log("  ERROR: No story segments! Run step 1.5 first.", "ERROR")
            return StepResult("create_director_plan_basic", StepStatus.FAILED, "No story segments")

        total_planned_images = sum(s.get("image_count", 0) for s in story_segments)
        self._log(f"  Story segments: {len(story_segments)} segments, {total_planned_images} planned images")

        # Read context
        story_analysis = workbook.get_story_analysis() or {}
        characters = workbook.get_characters()
        minor_char_ids = {c.id for c in characters if getattr(c, "is_child", False)}
        locations = workbook.get_locations()

        context_lock = story_analysis.get("context_lock", "")

        # Build character/location info + valid ID sets for normalization
        char_locks = []
        valid_char_ids    = set()
        char_image_lookup = {}      # id -> image_file
        for c in characters:
            valid_char_ids.add(c.id)
            char_image_lookup[c.id] = getattr(c, 'image_file', f"{c.id}.png") or f"{c.id}.png"
            if c.character_lock:
                char_locks.append(f"- {c.id}: {c.character_lock}")

        loc_locks = []
        valid_loc_ids    = set()
        loc_image_lookup = {}       # id -> image_file
        for loc in locations:
            valid_loc_ids.add(loc.id)
            loc_image_lookup[loc.id] = getattr(loc, 'image_file', f"{loc.id}.png") or f"{loc.id}.png"
            if hasattr(loc, 'location_lock') and loc.location_lock:
                loc_locks.append(f"- {loc.id}: {loc.location_lock}")

        self._log(f"  Valid char IDs: {valid_char_ids}")
        self._log(f"  Valid loc IDs:  {valid_loc_ids}")


        # Process segments PARALLEL - moi segment doc lap (dung SRT slice rieng)
        all_scenes = []
        total_entries = len(srt_entries)
        MAX_PARALLEL = max(1, min(int(self.config.get("max_parallel_api", 6)), 8))
        min_dur_global = float(self.config.get("min_scene_duration", 5))
        max_dur_global = float(self.config.get("max_scene_duration", 8))
        global_scene_units = group_srt_into_scenes(
            srt_entries,
            min_duration=min_dur_global,
            max_duration=max_dur_global,
        )

        self._log(f"  Processing {len(story_segments)} segments ({MAX_PARALLEL} concurrent)...")
        self._log(
            f"  SRT-first global grouping: {len(global_scene_units)} scenes "
            f"(target {min_dur_global:.0f}-{max_dur_global:.0f}s)"
        )

        # HELPER: Process single segment - returns (seg_idx, scenes_list, actual_image_count)
        def process_segment_basic(seg_idx_seg):
            seg_idx, seg = seg_idx_seg
            local_scenes = []

            seg_id = seg.get("segment_id", seg_idx + 1)
            seg_name = seg.get("segment_name", "")
            image_count = seg.get("image_count", 1)
            srt_start = seg.get("srt_range_start", 1)
            srt_end = seg.get("srt_range_end", total_entries)
            message = seg.get("message", "")
            dramatic_question = seg.get("dramatic_question", "")
            emotional_shift = seg.get("emotional_shift", "")
            visual_arc = seg.get("visual_arc", "")
            continuity_markers = seg.get("continuity_markers", [])
            forbidden_inventions = seg.get("forbidden_inventions", [])

            self._log(f"  Segment {seg_id}/{len(story_segments)}: {seg_name} ({image_count} images, SRT {srt_start}-{srt_end})")

            # Get SRT entries for this segment
            seg_entries = [e for i, e in enumerate(srt_entries, 1) if srt_start <= i <= srt_end]

            if not seg_entries:
                self._log(f"     -> No SRT entries for this segment, skip")
                return (seg_idx, [], 0)

            min_dur = float(self.config.get("min_scene_duration", 5))
            max_dur = float(self.config.get("max_scene_duration", 8))
            target_dur = (min_dur + max_dur) / 2

            try:
                seg_duration = self._srt_time_to_seconds(seg_entries[-1].end_time) - self._srt_time_to_seconds(seg_entries[0].start_time)
            except Exception as e:
                seg_duration = len(seg_entries) * target_dur
                self._log(f"     [WARN] Duration parse failed ({e}), fallback={seg_duration:.1f}s")

            scene_units = []
            for unit in global_scene_units:
                indices = unit.get("srt_indices") or []
                first_idx = min(indices) if indices else 0
                if srt_start <= first_idx <= srt_end:
                    scene_units.append(dict(unit))
            actual_scene_count = len(scene_units)
            if actual_scene_count != image_count:
                self._log(
                    f"     -> Seg {seg_id}: planned={image_count} -> srt_first={actual_scene_count} "
                    f"({seg_duration:.1f}s total, target {min_dur:.0f}-{max_dur:.0f}s)"
                )

            if not scene_units:
                return (seg_idx, [], 0)

            for unit_idx, unit in enumerate(scene_units):
                prev_unit = scene_units[unit_idx - 1] if unit_idx > 0 else None
                next_unit = scene_units[unit_idx + 1] if unit_idx + 1 < len(scene_units) else None
                if unit_idx == 0:
                    sequence_role = "opening"
                elif unit_idx == len(scene_units) - 1:
                    sequence_role = "closing"
                elif unit_idx == 1:
                    sequence_role = "build"
                elif unit_idx == len(scene_units) - 2:
                    sequence_role = "release"
                else:
                    sequence_role = "middle"
                default_function = {
                    "opening": "establish",
                    "build": "pressure",
                    "middle": "reveal",
                    "release": "aftermath",
                    "closing": "transition",
                }.get(sequence_role, "reveal")
                default_beat = {
                    "opening": "discovery",
                    "build": "confrontation",
                    "middle": "reflection",
                    "release": "reaction",
                    "closing": "release",
                }.get(sequence_role, "reflection")
                unit["segment_id"] = seg_id
                unit["sequence_id"] = f"seg_{seg_id}"
                unit["sequence_role"] = sequence_role
                unit["shot_function"] = default_function
                unit["beat_type"] = default_beat
                unit["emotional_turn"] = "emotion shifts within the beat"
                unit["continuity_from_prev"] = (prev_unit or {}).get("srt_text", "")[:160]
                unit["transition_to_next"] = (next_unit or {}).get("srt_text", "")[:160]

            BATCH_SCENES = 10
            n_batches = max(1, -(-len(scene_units) // BATCH_SCENES))
            enriched_scenes = []

            for batch_i in range(n_batches):
                batch_units = scene_units[batch_i * BATCH_SCENES:(batch_i + 1) * BATCH_SCENES]
                scenes_text = ""
                for unit in batch_units:
                    scenes_text += f"""
Scene {unit['scene_id']}:
- Segment/Sequence: segment {unit.get('segment_id')} / {unit.get('sequence_id')}
- Sequence role: {unit.get('sequence_role')}
- SRT indices: {unit['srt_indices']}
- SRT time: {unit['srt_start']} --> {unit['srt_end']}
- Duration: {unit['duration']:.2f}s
- Narration: "{unit['srt_text']}"
- Continuity from previous beat: "{unit.get('continuity_from_prev', '')}"
- Transition to next beat: "{unit.get('transition_to_next', '')}"
"""

                enrich_prompt = f"""You are a FILM DIRECTOR. Enrich pre-defined SRT scenes.

SEGMENT: "{seg_name}" (batch {batch_i+1}/{n_batches})
STORY: {message}
DRAMATIC QUESTION: {dramatic_question}
EMOTIONAL SHIFT: {emotional_shift}
VISUAL ARC: {visual_arc}
CONTINUITY MARKERS: {", ".join(continuity_markers) if isinstance(continuity_markers, list) else continuity_markers}
FORBIDDEN INVENTIONS: {", ".join(forbidden_inventions) if isinstance(forbidden_inventions, list) else forbidden_inventions}
CONTEXT: {context_lock}

CHARACTERS (use EXACT IDs):
{chr(10).join(char_locks) if char_locks else 'None'}

LOCATIONS (use EXACT IDs):
{chr(10).join(loc_locks) if loc_locks else 'None'}

                IMPORTANT:
- The scene boundaries are already LOCKED from the source SRT.
- DO NOT change scene count.
- DO NOT change srt_indices.
- DO NOT change srt_text.
- DO NOT change srt_start or srt_end.
- Your job is ONLY to enrich each scene with:
  visual_moment, characters_used, location_used, camera, lighting,
  scene_kind, subject_mode, primary_subject, primary_action, visual_anchor, must_not_show,
  shot_function, beat_type, emotional_turn, continuity_from_prev, transition_to_next
- visual_moment MUST directly illustrate the exact narration.
- location_used must be exactly one loc_id when possible.
- characters_used must contain only valid IDs from the list.
- scene_kind must be exactly one of:
  character_reaction / interaction / object_detail / environment_story / movement_transition
- subject_mode must be exactly one of:
  character / pair / object / environment
- primary_subject must be the single main thing visible in frame.
- primary_action must be the single main visible beat in frame.
- visual_anchor must be one concrete visual detail that keeps continuity.
- must_not_show must list what should NOT appear in the frame, especially:
  extra people, overlays, split-screen, collage, montage, ghostly layers, minors if unsafe to depict.
- ONE SCENE = ONE SHOT = ONE PRIMARY SUBJECT = ONE PRIMARY ACTION.
- Never use composite, split-screen, layered, translucent, ghostly, overlay, flashback insert, or simultaneous actions.
- If narration mentions a concrete object/document/room/arrangement/place, DO NOT fall back to face close-up.
- Do NOT invent concrete props, paperwork, notes, cards, screens, or devices unless the narration actually supports them.
- If narration is informational or reflective, choose the strongest concrete visual anchor implied by the text, not an eye/mouth/hand reaction.
- Forbidden fallback subjects unless the narration is explicitly about that body detail:
  eyes, mouth, lips, face, profile, cheek, hands, fingers, wedding ring.
- If the narration is about a room, design choice, absence, evidence, paperwork, object placement, or spatial change, prefer object_detail or environment_story.
- If two adults are clearly relating or confronting each other, prefer interaction/pair instead of a single reaction close-up.
- SEQUENCE RULE: all scenes in the same segment belong to one mini-sequence. Their visual strategy must progress like a film sequence, not repeat the same framing with a new prop.
- sequence_role tells you where the shot sits in the sequence. Use it.
- shot_function must be exactly one of:
  establish / reveal / reaction / pressure / evidence / aftermath / transition
- beat_type must be exactly one of:
  discovery / reaction / confrontation / reflection / movement / evidence / absence / release
- emotional_turn should describe the emotional shift of THIS beat in under 10 words.
- continuity_from_prev should name the one thing carried from the previous beat.
- transition_to_next should name the one thing this shot sets up for the next beat.

SCENES:
{scenes_text}

Return JSON only:
{{
  "scenes": [
    {{
      "scene_id": 1,
      "visual_moment": "specific visible beat that directly matches narration",
      "characters_used": "nv1",
      "location_used": "loc1",
      "camera": "Close-up / Wide / Medium",
      "lighting": "lighting description",
      "scene_kind": "object_detail",
      "subject_mode": "object",
      "primary_subject": "The manila folder on the courtroom table",
      "primary_action": "The folder lands flat on the wood surface",
      "visual_anchor": "The folder's worn edge and tab",
      "must_not_show": "No split-screen, no overlay, no extra people",
      "shot_function": "evidence",
      "beat_type": "evidence",
      "emotional_turn": "tension hardens into proof",
      "continuity_from_prev": "the argument still hangs in the room",
      "transition_to_next": "the other person must react to the folder"
    }}
  ]
}}
Return EXACTLY {len(batch_units)} scenes.
"""

                if self._is_styled:
                    enrich_prompt = f"""You are an educational psychology visual planner. Enrich pre-defined SRT scenes into clear illustration concepts.

SEGMENT: "{seg_name}" (batch {batch_i+1}/{n_batches})
MESSAGE: {message}
EMOTIONAL SHIFT: {emotional_shift}
VISUAL ARC: {visual_arc}
CONTEXT: {context_lock}
CHANNEL STYLE: {self.psychology_style_profile.get('scene_plan_style') or self.psychology_style_profile.get('image_style')}
PALETTE: {self.psychology_style_profile.get('palette')}
NEGATIVE RULES: {self.psychology_style_profile.get('negative_prompt')}
{self._psychology_audience_contract(strict=True)}

RECURRING CHARACTER:
- Reference character: fixed psychology character supplied as an image reference by the generation API. Use only when a stable presenter/learner/observer helps the image.

LOCATIONS (use exact IDs only when useful):
{chr(10).join(loc_locks) if loc_locks else 'None'}

IMPORTANT:
- Scene boundaries are locked from the source SRT. Do not change count, IDs, timings, or narration.
- visual_moment must directly illustrate the exact narration first; only use a metaphor when the SRT has no concrete visual action.
- Before choosing a metaphor, write a visual_contract in English: 2-5 concrete things the final image/video must show so a viewer can understand this exact narration. For non-English narration, translate the meaning into this contract. Do not use any prop or metaphor outside this contract unless the narration itself supports it.
- Cultural/audience anchors are optional support, not requirements. Never add tea, coffee, rain, window seats, emotion orbs, thought bubbles, doors, paths, mirrors, or plants unless they are the best fit for THIS narration beat.
- characters_used must be exactly "nv1" or "". Never invent other character IDs.
- Other people must be anonymous silhouettes/background figures, not references.
- location_used must be exactly one loc_id when useful, otherwise empty.
- scene_kind must be one of: character_reaction / interaction / object_detail / environment_story / movement_transition.
- subject_mode must be one of: character / pair / object / environment.
- primary_subject is the single idea/person/object the viewer notices first.
- primary_action is one visible emotional state, body-language beat, symbolic action, or metaphor.
- visual_anchor is one concrete SRT-derived object, body-language detail, spatial change, or metaphor. It must not be copied from a previous scene unless the SRT explicitly continues the same object.
- must_not_show must forbid readable text, captions, labels, extra named characters, and photo/cinema-camera style.
- Prefer visuals that match CHANNEL STYLE, but vary the concrete motif scene by scene. Repeated fallback motifs weaken audience comprehension.
- Prefer audience-specific everyday situations and props only when the narration supports them; otherwise create a fresh SRT-derived visual object or body-language beat.
- ANTI-REPETITION RULE: within this batch, do not reuse the same primary_subject or visual_anchor wording. If two scenes share a theme, show different evidence: object detail, posture, room arrangement, distance, threshold, pressure shape, or social silhouette behavior.
- No readable words, UI text, chart labels, document text, signage, watermark.
- One scene = one illustration = one primary subject = one primary action.

SCENES:
{scenes_text}

Return JSON only:
{{
  "scenes": [
    {{
      "scene_id": 1,
      "visual_moment": "clear psychology illustration or metaphor matching narration",
      "characters_used": "nv1",
      "location_used": "",
      "camera": "medium shot / close-up / wide shot",
      "lighting": "lighting that matches the channel style",
      "scene_kind": "character_reaction",
      "subject_mode": "character",
      "primary_subject": "nv1 facing the symbolic problem",
      "primary_action": "nv1 pauses beside a simple visual metaphor",
      "visual_anchor": "one concrete metaphor from the narration",
      "visual_contract": "must show: the literal situation or object from the narration; the visible emotional reaction/body language; the cause-effect idea; must not replace this with unrelated props",
      "alignment_notes": "why this visual matches the exact narration",
      "must_not_show": "no readable text, no new character IDs, obey channel negative rules",
      "shot_function": "reveal",
      "beat_type": "reflection",
      "emotional_turn": "idea becomes visible",
      "continuity_from_prev": "one visual idea carried forward",
      "transition_to_next": "one visual idea set up next"
    }}
  ]
}}
Return EXACTLY {len(batch_units)} scenes.
"""

                batch_data = None
                for retry in range(3):
                    resp = self._call_api(enrich_prompt, temperature=0.35, max_tokens=4096)
                    if resp:
                        batch_data = self._extract_json(resp)
                        if batch_data and "scenes" in batch_data and len(batch_data["scenes"]) == len(batch_units):
                            break
                    time.sleep(2 ** retry)

                enrich_by_id = {}
                if batch_data and "scenes" in batch_data:
                    enrich_by_id = {int(s.get("scene_id", 0)): s for s in batch_data["scenes"]}
                    self._log(f"     -> [Seg{seg_id}] Batch {batch_i+1}/{n_batches}: enriched {len(enrich_by_id)} scenes")
                else:
                    self._log(f"     -> [Seg{seg_id}] Batch {batch_i+1}/{n_batches}: FALLBACK ENRICH", "WARNING")

                for unit in batch_units:
                    enrich = enrich_by_id.get(int(unit["scene_id"]), {})
                    unit["visual_moment"] = enrich.get("visual_moment") or f"[SRT-first] {unit['srt_text'][:180]}"
                    unit["characters_used"] = enrich.get("characters_used", "")
                    unit["location_used"] = enrich.get("location_used", "")
                    unit["camera"] = enrich.get("camera") or "Medium shot"
                    unit["lighting"] = enrich.get("lighting") or "Natural lighting"
                    unit["scene_kind"] = enrich.get("scene_kind", "")
                    unit["subject_mode"] = enrich.get("subject_mode", "")
                    unit["primary_subject"] = enrich.get("primary_subject", "")
                    unit["primary_action"] = enrich.get("primary_action", "")
                    unit["visual_anchor"] = enrich.get("visual_anchor", "")
                    unit["visual_contract"] = enrich.get("visual_contract", "")
                    unit["alignment_notes"] = enrich.get("alignment_notes", "")
                    unit["shot_function"] = enrich.get("shot_function") or unit.get("shot_function", "")
                    unit["beat_type"] = enrich.get("beat_type") or unit.get("beat_type", "")
                    unit["emotional_turn"] = enrich.get("emotional_turn") or unit.get("emotional_turn", "")
                    unit["continuity_from_prev"] = enrich.get("continuity_from_prev") or unit.get("continuity_from_prev", "")
                    unit["transition_to_next"] = enrich.get("transition_to_next") or unit.get("transition_to_next", "")
                    must_not_show = enrich.get("must_not_show", "")
                    if minor_char_ids and any(cid.strip() in minor_char_ids for cid in str(unit["characters_used"]).split(",") if cid.strip() and cid.strip() != "[]"):
                        must_not_show = f"{must_not_show}; no child visible"
                    unit["must_not_show"] = must_not_show
                    unit = self._ensure_scene_spec_fields(unit, minor_char_ids=minor_char_ids)
                    enriched_scenes.append(unit)

            local_scenes.extend(enriched_scenes)
            return (seg_idx, local_scenes, actual_scene_count)

        # Execute segments in parallel
        segment_results = {}
        with ThreadPoolExecutor(max_workers=MAX_PARALLEL) as executor:
            futures = {executor.submit(process_segment_basic, (i, seg)): i for i, seg in enumerate(story_segments)}
            for future in as_completed(futures):
                seg_idx = futures[future]
                try:
                    result_idx, scenes, _ = future.result()
                    segment_results[result_idx] = scenes
                except Exception as e:
                    self._log(f"     -> Segment {seg_idx+1} failed: {e}", "ERROR")
                    segment_results[seg_idx] = []

        # Merge results in order and assign scene_ids
        scene_id_counter = 1
        for seg_idx in range(len(story_segments)):
            seg_scenes = segment_results.get(seg_idx, [])
            for scene in seg_scenes:
                scene["scene_id"] = scene_id_counter
                # Normalize IDs
                raw_chars = scene.get("characters_used", "")
                raw_loc   = scene.get("location_used", "")
                norm_chars = self._normalize_character_ids(raw_chars, valid_char_ids)
                norm_loc = self._normalize_location_id(raw_loc, valid_loc_ids)
                if self._is_styled:
                    norm_chars = "nv1"
                    norm_loc = ""
                scene["characters_used"] = norm_chars
                scene["location_used"]   = norm_loc

                # Auto-build reference_files tá»« normalized IDs
                refs = []
                for cid in [c.strip() for c in (norm_chars or "").split(",") if c.strip() and c.strip() != "[]"]:
                    img = char_image_lookup.get(cid, f"{cid}.png")
                    if img and img not in refs:
                        refs.append(img)
                if norm_loc and norm_loc.strip():
                    loc_img = loc_image_lookup.get(norm_loc.strip(), f"{norm_loc.strip()}.png")
                    if loc_img and loc_img not in refs:
                        refs.append(loc_img)
                scene["reference_files"] = refs
                scene = self._ensure_scene_spec_fields(scene, minor_char_ids=minor_char_ids)
                scene["reference_files"] = self._build_reference_files(
                    scene.get("characters_used", ""),
                    scene.get("location_used", ""),
                    char_image_lookup,
                    loc_image_lookup,
                )
                if self._is_styled:
                    scene["reference_files"] = ["nv1.png"]

                all_scenes.append(scene)
                scene_id_counter += 1

        # Verify total scene count
        if len(all_scenes) != total_planned_images:
            self._log(f"  Note: Created {len(all_scenes)} scenes (planned: {total_planned_images})")

        if not all_scenes:
            self._log("  ERROR: No scenes created!", "ERROR")
            return StepResult("create_director_plan_basic", StepStatus.FAILED, "No scenes created")

        if not self._is_styled:
            spec_audit = self._audit_scene_specs(all_scenes)
            self._log(
                "  Scene-spec audit: "
                f"non_reaction_closeup_fallbacks={spec_audit['non_reaction_closeup_fallbacks']}, "
                f"object_scenes_without_object_anchor={spec_audit['object_scenes_without_object_anchor']}, "
                f"environment_scenes_without_space_anchor={spec_audit['environment_scenes_without_space_anchor']}"
            )
        else:
            psy_bad_specs = [
                s for s in all_scenes
                if s.get("characters_used") not in {"", "nv1"}
                or s.get("location_used")
                or not s.get("primary_subject")
                or not s.get("primary_action")
                or not s.get("visual_anchor")
            ]
            self._log(f"  {self.topic.title()} scene-spec audit: {len(all_scenes) - len(psy_bad_specs)}/{len(all_scenes)} specs ready")
            if psy_bad_specs:
                self._log(f"  [WARN] {self.topic.title()} specs still weak: {[s.get('scene_id') for s in psy_bad_specs[:10]]}", "WARN")
            audience_fit_count = sum(1 for s in all_scenes if self._scene_has_psychology_cultural_anchor(s))
            self._log(f"  {self.topic.title()} audience-fit audit: {audience_fit_count}/{len(all_scenes)} specs use optional audience anchors")

        # â”€â”€ Recalculate sequential DISPLAY timestamps from duration â”€â”€
        # API timestamps (srt_start/srt_end) are unreliable narration markers.
        # For video assembly we need sequential cumulative timecodes.
        aligned_count = 0
        warned_count = 0
        if not all(
            sc.get("srt_start")
            and sc.get("srt_end")
            and float(sc.get("duration") or 0) > 0
            for sc in all_scenes
        ):
            aligned_count, warned_count = self._align_scenes_with_source_srt(all_scenes, srt_entries)
            self._log(f"  SRT alignment check: {aligned_count}/{len(all_scenes)} scenes synced to source SRT")
            if warned_count:
                self._log(f"  [WARN] {warned_count} scene(s) could not be re-aligned from source SRT", "WARN")
        else:
            self._log("  SRT alignment check: skipped; SRT-first timings already locked")

        total_duration = sum(float(sc.get("duration") or 0) for sc in all_scenes)
        self._enforce_scene_timing_bounds(all_scenes, min_dur_global, max_dur_global)
        total_duration = sum(float(sc.get("duration") or 0) for sc in all_scenes)

        # Save to Excel
        try:
            workbook.save_director_plan(all_scenes)
            workbook.save()
            self._log(f"  -> Saved {len(all_scenes)} scenes to director_plan")
            self._log(f"     Total duration: {total_duration:.1f}s")

            workbook.update_step_status(
                "step_5",
                "COMPLETED",
                len(all_scenes),
                len(all_scenes),
                f"{int(total_duration)}s - {len(all_scenes)} scenes",
            )


            # TRACKING: Cáº­p nháº­t vÃ  kiá»ƒm tra coverage
            coverage = workbook.update_srt_coverage_scenes(all_scenes)
            self._log(f"\n  [STATS] SRT COVERAGE (sau Step 4 BASIC):")
            self._log(f"     Total SRT: {coverage['total_srt']}")
            self._log(f"     Covered by scenes: {coverage['covered_by_scene']} ({coverage['coverage_percent']}%)")
            if coverage['uncovered'] > 0:
                self._log(f"     [WARN] UNCOVERED: {coverage['uncovered']} entries", "WARN")
                uncovered_list = workbook.get_uncovered_srt_entries()
                if uncovered_list:
                    self._log(f"     Missing SRT: {[u['srt_index'] for u in uncovered_list[:10]]}...")

            return StepResult("create_director_plan_basic", StepStatus.COMPLETED, "Success", {"scenes": all_scenes})
        except Exception as e:
            self._log(f"  ERROR: Could not save to Excel: {e}", "ERROR")
            return StepResult("create_director_plan_basic", StepStatus.FAILED, str(e))

    def _enforce_scene_timing_bounds(self, scenes: list, min_dur: float, max_dur: float) -> None:
        prev_end = None
        fixed = 0
        for scene in scenes:
            try:
                start_sec = self._srt_time_to_seconds(scene.get("srt_start", ""))
                end_sec = self._srt_time_to_seconds(scene.get("srt_end", ""))
            except Exception:
                continue

            duration = max(0.01, end_sec - start_sec)
            planned = float(scene.get("duration") or scene.get("planned_duration") or duration)

            if prev_end is not None and start_sec < prev_end - 0.01:
                start_sec = prev_end
                duration = max(0.01, end_sec - start_sec)

            if duration > max_dur:
                duration = min(max_dur, max(min_dur, planned))
                end_sec = start_sec + duration
                fixed += 1
            elif duration < min_dur and planned >= min_dur:
                duration = min(max_dur, planned)
                end_sec = start_sec + duration
                fixed += 1

            scene["srt_start"] = format_srt_time(parse_srt_time("00:00:00,000") + timedelta(seconds=start_sec))
            scene["srt_end"] = format_srt_time(parse_srt_time("00:00:00,000") + timedelta(seconds=end_sec))
            scene["duration"] = round(duration, 2)
            scene["planned_duration"] = round(duration, 2)
            prev_end = end_sec

        if fixed:
            self._log(f"  Timing guard: fixed {fixed} scene timestamp span(s) to {min_dur:.0f}-{max_dur:.0f}s")

    # =========================================================================
    # STEP 6: LÃŠN Káº¾ HOáº CH CHI TIáº¾T Tá»ªNG SCENE
    # =========================================================================

    def step_plan_scenes(
        self,
        project_dir: Path,
        code: str,
        workbook: PromptWorkbook,
    ) -> StepResult:
        """
        Step 4.5: LÃªn káº¿ hoáº¡ch chi tiáº¿t cho tá»«ng scene TRÆ¯á»šC KHI viáº¿t prompt.

        Má»¥c Ä‘Ã­ch: XÃ¡c Ä‘á»‹nh Ã½ Ä‘á»“ nghá»‡ thuáº­t cho má»—i scene
        - Scene nÃ y muá»‘n truyá»n táº£i gÃ¬?
        - GÃ³c mÃ¡y nÃªn tháº¿ nÃ o?
        - NhÃ¢n váº­t Ä‘ang lÃ m gÃ¬, cáº£m xÃºc ra sao?
        - Ãnh sÃ¡ng, mÃ u sáº¯c, mood?

        Input: director_plan, story_segments, characters, locations
        Output: scene_planning sheet
        """
        import time
        step_start = time.time()

        self._log("\n" + "="*60)
        self._log("[STEP 6/7] LÃªn káº¿ hoáº¡ch chi tiáº¿t tá»«ng scene...")
        self._log("="*60)

        # Check if already done
        try:
            existing = workbook.get_scene_planning()
            if existing and len(existing) > 0:
                self._log(f"  -> ÄÃ£ cÃ³ {len(existing)} scene plans, skip!")
                workbook.update_step_status("step_6", "COMPLETED", len(existing), len(existing), "Already done")
                return StepResult("plan_scenes", StepStatus.COMPLETED, "Already done")
        except:
            pass

        # Read director plan
        director_plan = workbook.get_director_plan()
        if not director_plan:
            self._log("  ERROR: No director plan! Run step 4 first.", "ERROR")
            return StepResult("plan_scenes", StepStatus.FAILED, "No director plan")

        # Read context
        story_analysis = workbook.get_story_analysis() or {}
        story_segments = workbook.get_story_segments() or []
        characters = workbook.get_characters()
        minor_char_ids = {c.id for c in characters if getattr(c, "is_child", False)}
        locations = workbook.get_locations()
        director_plan = [self._ensure_scene_spec_fields(scene, minor_char_ids=minor_char_ids) for scene in director_plan]

        context_lock = story_analysis.get("context_lock", "")

        # Build character info
        char_info = "\n".join([f"- {c.id}: {c.character_lock}" for c in characters if c.character_lock])
        loc_info = "\n".join([f"- {loc.id}: {loc.location_lock}" for loc in locations if hasattr(loc, 'location_lock') and loc.location_lock])

        # Build segments info
        segments_info = ""
        for seg in story_segments:
            segments_info += (
                f"- Segment {seg.get('segment_id')}: {seg.get('segment_name')} "
                f"({seg.get('message', '')[:100]}) | dramatic_question={seg.get('dramatic_question', '')[:80]} "
                f"| emotional_shift={seg.get('emotional_shift', '')[:80]} "
                f"| visual_arc={seg.get('visual_arc', '')[:100]}\n"
            )

        self._log(f"  Director plan: {len(director_plan)} scenes")
        self._log(f"  Story segments: {len(story_segments)}")

        # Process in batches - PARALLEL processing
        # DeepSeek context limited â†’ smaller batches = better quality per scene
        BATCH_SIZE = 8
        MAX_PARALLEL = max(1, min(int(self.config.get("max_parallel_api", 6)), 8))  # Throttle for single-key API use
        all_plans = []

        # Prepare all batches
        batches = []
        for batch_start in range(0, len(director_plan), BATCH_SIZE):
            batch = director_plan[batch_start:batch_start + BATCH_SIZE]
            batch_num = batch_start // BATCH_SIZE + 1
            batches.append((batch_num, batch_start, batch))

        total_batches = len(batches)
        self._log(f"  Processing {total_batches} batches in parallel (max {MAX_PARALLEL} concurrent)")

        def process_single_batch(batch_info):
            """Process a single batch - called in parallel"""
            batch_num, batch_start, batch = batch_info

            # Format scenes for prompt
            scenes_text = ""
            for local_idx, scene in enumerate(batch):
                global_idx = batch_start + local_idx
                prev_scene = director_plan[global_idx - 1] if global_idx > 0 else {}
                next_scene = director_plan[global_idx + 1] if global_idx + 1 < len(director_plan) else {}
                scenes_text += f"""
Scene {scene.get('scene_id')}:
- Time: {scene.get('srt_start')} â†’ {scene.get('srt_end')} ({scene.get('duration', 0):.1f}s)
- âš  NARRATION (PRIMARY): {scene.get('srt_text', '')}
- Visual moment: {scene.get('visual_moment', '')}
- Segment/Sequence: segment {scene.get('segment_id', 0)} / {scene.get('sequence_id', '')}
- Sequence role: {scene.get('sequence_role', '')}
- Shot function hint: {scene.get('shot_function', '')}
- Beat type hint: {scene.get('beat_type', '')}
- Emotional turn hint: {scene.get('emotional_turn', '')}
- Continuity from prev hint: {scene.get('continuity_from_prev', '')}
- Transition to next hint: {scene.get('transition_to_next', '')}
- Scene kind: {scene.get('scene_kind', '')}
- Subject mode: {scene.get('subject_mode', '')}
- Primary subject: {scene.get('primary_subject', '')}
- Primary action: {scene.get('primary_action', '')}
- Visual anchor: {scene.get('visual_anchor', '')}
- Must not show: {scene.get('must_not_show', '')}
- Characters: {scene.get('characters_used', '')}
- Location: {scene.get('location_used', '')}
- Previous scene beat: {prev_scene.get('primary_subject', '')} / {prev_scene.get('primary_action', '')}
- Next scene beat: {next_scene.get('primary_subject', '')} / {next_scene.get('primary_action', '')}
"""

            prompt = f"""You are a film director planning each scene's artistic vision.

STORY CONTEXT:
{context_lock}

STORY SEGMENTS (narrative structure):
{segments_info if segments_info else 'Not specified'}

CHARACTERS:
{char_info if char_info else 'Not specified'}

LOCATIONS:
{loc_info if loc_info else 'Not specified'}

SCENES TO PLAN:
{scenes_text}

CRITICAL RULE: The NARRATION (âš  PRIMARY) is the absolute truth. Your artistic plan MUST illustrate exactly what is being narrated. If the narration mentions a specific object, place, action, or emotion â€” the scene plan MUST include it. Do NOT invent or substitute content that isn't in the narration.
DIRECTOR PLAN RULE: scene_kind, subject_mode, primary_subject, primary_action, visual_anchor, and must_not_show are LOCKED scene-spec fields from Step 5. You may refine them, but you must not contradict or expand beyond them.

For EACH scene, create an artistic plan that includes:
1. artistic_intent: What emotion/message should this scene convey? (MUST match narration content)
2. shot_type: ONE shot only. Single frame only. Never a sequence, montage, split-screen, collage, or multiple shots.
3. character_action: One visible action or stillness beat. Never multiple actions in a list.
4. mood: Overall feeling (tense, warm, melancholic, hopeful, etc.)
5. lighting: Type of lighting (soft, harsh, dramatic, natural, etc.)
6. color_palette: Dominant colors for the scene
7. key_focus: What should viewer's eye be drawn to? (MUST be something mentioned in narration)
8. sequence_role: Keep or refine the role of this shot inside its sequence
9. shot_function: Choose exactly one of establish / reveal / reaction / pressure / evidence / aftermath / transition
10. beat_type: Choose exactly one of discovery / reaction / confrontation / reflection / movement / evidence / absence / release
11. emotional_turn: Emotional shift of this beat in under 10 words
12. viewer_attention: What the viewer should notice first in frame
13. subtext_delivery: What this shot implies beneath the literal action
14. continuity_note: What must visually connect this shot to the previous/next shot
15. scene_kind: Choose exactly one of:
   - character_reaction
   - interaction
   - object_detail
   - environment_story
   - movement_transition
16. subject_mode: Choose exactly one of:
   - character
   - pair
   - object
   - environment
17. primary_subject: The single main thing visible in frame
18. primary_action: The single main visible action or visual beat

SHOT DESIGN RULES:
- Think like a film shot list, not like a montage editor.
- Each scene must be ONE frameable shot.
- Consecutive scenes in the same sequence must progress in shot purpose, not just repeat the same room with a slightly different object.
- Use previous scene beat and next scene beat to avoid visual redundancy.
- If narration points to a prop/detail, prefer object_detail over showing a face.
- If narration is about space/absence/context, prefer environment_story and do not force a character into frame.
- If narration is about two people relating, use interaction/pair.
- Do not introduce new concrete props or paperwork in key_focus/primary_subject unless they already exist in the narration or locked scene spec.
- Do not use eye/mouth/hand/profile close-ups as generic fallback when the narration offers a stronger object, space, evidence, or relationship beat.
- Do not force characters into scenes that work better as object or environment shots.
- Do not write "sequence of three shots", "1) 2) 3)", "multiple frames", or similar.
- Forbidden planning language: split-screen, composite, layered, superimposed, translucent, ghostly, flashback insert, overlay, juxtaposed versions, simultaneous actions.
- If the narration compresses multiple ideas, choose the single strongest visual anchor instead of combining them in one frame.

Return JSON only:
{{
    "scene_plans": [
        {{
            "scene_id": 1,
            "segment_id": 3,
            "sequence_id": "seg_3",
            "sequence_role": "build",
            "shot_function": "reaction",
            "beat_type": "reaction",
            "emotional_turn": "hurt turns into withdrawal",
            "viewer_attention": "her still face framed by the doorway",
            "subtext_delivery": "the room feels larger than her emotional safety",
            "continuity_note": "keep the same doorway geography from the previous shot",
            "artistic_intent": "Show the protagonist's isolation and loneliness",
            "shot_type": "Wide shot from the doorway",
            "character_action": "She sits alone, shoulders slumped, staring at the window",
            "mood": "Melancholic, contemplative",
            "lighting": "Soft diffused light from window, shadows on face",
            "color_palette": "Cool blues and grays, muted tones",
            "key_focus": "Her small figure inside the empty room",
            "scene_kind": "character_reaction",
            "subject_mode": "character",
            "primary_subject": "The woman alone in the room",
            "primary_action": "She sits still and stares toward the window"
        }}
    ]
}}
"""

            if self._is_styled:
                prompt = f"""You are planning educational psychology illustrations, not film shots.

CHANNEL CONTEXT:
{context_lock}
CHANNEL STYLE:
{self.psychology_style_profile.get('scene_plan_style') or self.psychology_style_profile.get('image_style')}
PALETTE:
{self.psychology_style_profile.get('palette')}
NEGATIVE RULES:
{self.psychology_style_profile.get('negative_prompt')}
{self._psychology_audience_contract(strict=True)}

SEGMENTS:
{segments_info if segments_info else 'Not specified'}

RECURRING CHARACTER:
{char_info if char_info else '- Reference character: fixed local psychology character supplied as an image reference by the generation API'}

LOCATIONS:
{loc_info if loc_info else 'Not specified'}

SCENES TO PLAN:
{scenes_text}

CRITICAL RULE: The NARRATION is the absolute truth. Each plan must make the spoken psychology idea clear, concrete, and visually engaging.
AUDIENCE RULE: Audience fit is optional support. Use audience-specific settings, props, rituals, or metaphors only when they directly clarify the current narration; never add them as a quota.
DIRECTOR PLAN RULE: scene_kind, subject_mode, primary_subject, primary_action, visual_anchor, and must_not_show are locked scene-spec fields. Refine only when it improves narration alignment.

For EACH scene, create an educational illustration plan with:
1. artistic_intent: what psychology idea the viewer should understand
2. shot_type: one frameable illustration composition, not a film camera spec
3. character_action: one visible emotion, posture, gesture, or symbolic action
4. mood: warm, reflective, curious, hopeful, calm, or emotionally honest
5. lighting: lighting that matches the channel style
6. color_palette: palette that matches the channel style
7. key_focus: the first visual anchor viewers notice
8. sequence_role: keep/refine role within the segment
9. shot_function: establish / reveal / reaction / pressure / evidence / aftermath / transition
10. beat_type: discovery / reaction / confrontation / reflection / movement / evidence / absence / release
11. emotional_turn: emotional shift under 10 words
12. viewer_attention: what the viewer notices first
13. subtext_delivery: what the metaphor/body language communicates
14. continuity_note: what visual motif carries between adjacent illustrations
15. scene_kind: character_reaction / interaction / object_detail / environment_story / movement_transition
16. subject_mode: character / pair / object / environment
17. primary_subject: one main visible idea/person/object
18. primary_action: one main visible action or symbolic beat
19. visual_contract: English contract for the final prompts: 2-5 concrete visual obligations from the narration, including any literal object/place/body reaction that must remain visible
20. alignment_notes: one short reason the chosen visual contract matches the exact narration
21. video_motion_contract: one concrete image-to-video motion that can animate the same image prompt keyframe without changing subject, pose, props, layout, or character design
22. distinctiveness_note: how this scene's composition, pose, and supporting props differ from the previous and next scene

PSYCHOLOGY VISUAL RULES:
- Use nv1 only when useful; other people are anonymous silhouettes/background figures.
- Never invent new character IDs.
- Prefer symbolic props and visual metaphors that match the channel style over cinematic drama.
- No readable labels, captions, UI text, chart text, document text, signs, or watermarks.
- Avoid cinema-camera vocabulary; keep the plan in clean illustration language.
- One scene = one illustration = one primary subject = one primary action.
- Add a video_motion_contract that reuses the same illustrated setup and only changes visible motion, camera drift, or prop/light response.
- Add a distinctiveness_note so adjacent scenes do not reuse the same composition, pose, or supporting props unless the narration repeats them.

Return JSON only with the same scene_plans schema:
{{
    "scene_plans": [
        {{
            "scene_id": 1,
            "segment_id": 3,
            "sequence_id": "seg_3",
            "sequence_role": "build",
            "shot_function": "reveal",
            "beat_type": "reflection",
            "emotional_turn": "confusion becomes visible",
            "viewer_attention": "nv1 beside a simple tangled-line metaphor",
            "subtext_delivery": "the messy knot shows inner overwhelm without text",
            "continuity_note": "carry the same channel style and palette",
            "artistic_intent": "Make the abstract psychology idea easy to understand",
            "shot_type": "one frameable illustration composition in the channel style",
            "character_action": "nv1 studies the metaphor with a thoughtful posture",
            "mood": "warm, reflective, engaging",
            "lighting": "lighting that matches the channel style",
            "color_palette": "channel palette",
            "key_focus": "the symbolic object tied to the narration",
            "scene_kind": "character_reaction",
            "subject_mode": "character",
            "primary_subject": "nv1 and the symbolic object",
            "primary_action": "nv1 reacts thoughtfully to the visible metaphor",
            "visual_contract": "must show the literal narration idea, one concrete visible cue, and the emotional cause-effect; do not replace it with unrelated cultural props",
            "video_motion_contract": "animate the same keyframe with one small visible gesture, one prop/light response, and a final held pose",
            "distinctiveness_note": "different pose, composition, and supporting props from adjacent scenes",
            "alignment_notes": "the chosen object/body language is directly named or implied by the narration"
        }}
    ]
}}
"""

            # Call API with retry logic
            MAX_RETRIES = 3
            data = None

            for retry in range(MAX_RETRIES):
                response = self._call_api(prompt, temperature=0.4, max_tokens=8192)
                if not response:
                    time.sleep(2 ** retry)  # Exponential backoff
                    continue

                # Parse response
                data = self._extract_json(response)
                if data and "scene_plans" in data:
                    plan_by_id = {}
                    for plan in data["scene_plans"]:
                        try:
                            plan_id = int(plan.get("scene_id", 0))
                        except:
                            plan_id = 0
                        if plan_id:
                            plan_by_id[plan_id] = dict(plan or {})
                    normalized_plans = []
                    for scene in batch:
                        merged_plan = dict(plan_by_id.get(int(scene.get("scene_id", 0)), {}))
                        merged_plan["scene_id"] = merged_plan.get("scene_id") or scene.get("scene_id")
                        merged_plan["segment_id"] = merged_plan.get("segment_id") or scene.get("segment_id", 0)
                        merged_plan["sequence_id"] = merged_plan.get("sequence_id") or scene.get("sequence_id", "")
                        merged_plan["sequence_role"] = merged_plan.get("sequence_role") or scene.get("sequence_role", "")
                        merged_plan["shot_function"] = merged_plan.get("shot_function") or scene.get("shot_function", "") or "reaction"
                        merged_plan["beat_type"] = merged_plan.get("beat_type") or scene.get("beat_type", "") or "reflection"
                        merged_plan["emotional_turn"] = merged_plan.get("emotional_turn") or scene.get("emotional_turn", "") or "emotion holds in place"
                        merged_plan["viewer_attention"] = merged_plan.get("viewer_attention") or merged_plan.get("key_focus") or scene.get("visual_anchor", "")
                        merged_plan["subtext_delivery"] = merged_plan.get("subtext_delivery") or scene.get("visual_moment", "")[:150]
                        merged_plan["continuity_note"] = merged_plan.get("continuity_note") or scene.get("transition_to_next", "") or scene.get("continuity_from_prev", "")
                        merged_plan["visual_contract"] = merged_plan.get("visual_contract") or scene.get("visual_contract", "")
                        merged_plan["video_motion_contract"] = merged_plan.get("video_motion_contract") or merged_plan.get("character_action") or scene.get("primary_action", "")
                        merged_plan["distinctiveness_note"] = merged_plan.get("distinctiveness_note") or "vary composition, pose, and supporting props from adjacent scenes"
                        merged_plan["alignment_notes"] = merged_plan.get("alignment_notes") or scene.get("alignment_notes", "")
                        visual_concept_source = dict(scene)
                        visual_concept_source.update(merged_plan)
                        visual_concept = self._extract_visual_concept(
                            visual_concept_source,
                            artistic_intent=merged_plan.get("artistic_intent", ""),
                        )
                        merged_plan.update(visual_concept)
                        normalized_plans.append(merged_plan)
                    data["scene_plans"] = normalized_plans
                    break  # Success!
                else:
                    time.sleep(2 ** retry)

            if not data or "scene_plans" not in data:
                # Fallback: create basic plans for this batch
                fallback_plans = []
                for scene in batch:
                    fallback_plan = {
                        "scene_id": scene.get("scene_id"),
                        "segment_id": scene.get("segment_id", 0),
                        "sequence_id": scene.get("sequence_id", ""),
                        "sequence_role": scene.get("sequence_role", ""),
                        "shot_function": scene.get("shot_function", "") or "reaction",
                        "beat_type": scene.get("beat_type", "") or "reflection",
                        "emotional_turn": scene.get("emotional_turn", "") or "emotion holds in place",
                        "viewer_attention": scene.get("visual_anchor", "") or scene.get("primary_subject", ""),
                        "subtext_delivery": scene.get("visual_moment", "")[:150],
                        "continuity_note": scene.get("transition_to_next", "") or scene.get("continuity_from_prev", ""),
                        "artistic_intent": f"Convey the moment: {scene.get('visual_moment', '')[:100]}",
                        "shot_type": scene.get("camera", "Medium shot"),
                        "character_action": "As described in visual moment",
                        "mood": "Matches the narration tone",
                        "lighting": scene.get("lighting", "Natural lighting"),
                        "color_palette": "Neutral tones",
                        "key_focus": "Main subject of the scene",
                        "scene_kind": scene.get("scene_kind", "") or "character_reaction",
                        "subject_mode": scene.get("subject_mode", "") or "character",
                        "primary_subject": scene.get("primary_subject", "") or scene.get("visual_moment", "")[:120] or "Main subject in narration",
                        "primary_action": scene.get("primary_action", "") or scene.get("srt_text", "")[:120] or "Visible action from narration",
                        "visual_contract": scene.get("visual_contract", "") or f"must show the exact narration beat: {scene.get('srt_text', '')[:220]}",
                        "video_motion_contract": scene.get("primary_action", "") or "animate the same keyframe with one small visible gesture, one prop/light response, and a final held pose",
                        "distinctiveness_note": "vary composition, pose, and supporting props from adjacent scenes",
                        "alignment_notes": "fallback contract copied from source narration",
                    }
                    visual_concept_source = dict(scene)
                    visual_concept_source.update(fallback_plan)
                    fallback_plan.update(self._extract_visual_concept(
                        visual_concept_source,
                        artistic_intent=fallback_plan.get("artistic_intent", ""),
                    ))
                    fallback_plans.append(fallback_plan)
                return (batch_num, fallback_plans, True)  # True = fallback used

            return (batch_num, data["scene_plans"], False)  # False = API success

        def refine_weak_sequence(sequence_id: str, sequence_scenes: list, current_plans: list):
            scenes_text = ""
            for scene in sequence_scenes:
                current = next((p for p in current_plans if int(p.get("scene_id", 0)) == int(scene.get("scene_id", 0))), {})
                scenes_text += f"""
Scene {scene.get('scene_id')}:
- Narration: {scene.get('srt_text', '')}
- Locked kind/mode: {scene.get('scene_kind', '')} / {scene.get('subject_mode', '')}
- Locked subject/action/anchor: {scene.get('primary_subject', '')} | {scene.get('primary_action', '')} | {scene.get('visual_anchor', '')}
- Current sequence role/function: {current.get('sequence_role', scene.get('sequence_role', ''))} / {current.get('shot_function', scene.get('shot_function', ''))}
- Current beat/emotion: {current.get('beat_type', scene.get('beat_type', ''))} / {current.get('emotional_turn', scene.get('emotional_turn', ''))}
- Current shot/focus: {current.get('shot_type', '')} | {current.get('key_focus', current.get('viewer_attention', ''))}
- Visual contract: {current.get('visual_contract', scene.get('visual_contract', ''))}
- Video motion contract: {current.get('video_motion_contract', scene.get('primary_action', ''))}
- Distinctiveness note: {current.get('distinctiveness_note', '')}
"""

            prompt = f"""You are refining a weak film sequence so it plays like a real shot progression.

GOAL:
- Keep the same scene count and scene IDs
- Keep each scene faithful to its narration
- Improve sequence flow, shot variation, continuity, and emotional progression
- Do NOT invent paperwork, props, evidence, documents, tabs, folders, notes, or readable screens unless the narration explicitly supports them

SEQUENCE ID: {sequence_id}

WEAKNESS TO FIX:
- Too much repetition or insufficient progression between adjacent shots
- Sequence must feel like a designed mini-scene, not repeated coverage
- If this is psychology/educational illustration, fix repeated symbolic motifs by choosing a narration-specific object, posture, spatial arrangement, or social behavior for each scene.
- Do not reuse the same visual_anchor wording across scenes unless the narration explicitly continues the same object.
- Give every scene a video_motion_contract that animates the same image-prompt keyframe without changing subject, pose, props, layout, or character design.
- Give every scene a distinctiveness_note describing the different composition, pose, or supporting props compared with adjacent scenes.

SCENES:
{scenes_text}

Return JSON only:
{{
  "scene_plans": [
    {{
      "scene_id": 1,
      "sequence_role": "opening",
      "shot_function": "establish",
      "beat_type": "discovery",
      "emotional_turn": "guarded calm starts to tighten",
      "viewer_attention": "what the viewer notices first",
      "subtext_delivery": "what the shot implies beneath the literal action",
      "continuity_note": "what carries into the next shot",
      "artistic_intent": "what this shot should make the viewer feel",
      "shot_type": "single shot type only",
      "character_action": "one visible action or stillness beat",
      "mood": "tone",
      "lighting": "lighting approach",
      "color_palette": "palette",
      "key_focus": "one clear focus",
      "visual_anchor": "one narration-specific concrete visual anchor, not a repeated fallback motif",
      "visual_contract": "2-5 English visual obligations copied from the narration, not from audience-prop habits",
      "video_motion_contract": "one concrete motion for animating the same image prompt setup without redesigning the scene",
      "distinctiveness_note": "how composition, pose, and supporting props differ from adjacent scenes",
      "alignment_notes": "why this visual still matches the narration",
      "scene_kind": "interaction",
      "subject_mode": "pair",
      "primary_subject": "locked or refined single subject",
      "primary_action": "locked or refined single action"
    }}
  ]
}}
Return EXACTLY {len(sequence_scenes)} scene_plans.
"""

            response = self._call_api(prompt, temperature=0.35, max_tokens=4096)
            data = self._extract_json(response) if response else None
            if not data or "scene_plans" not in data:
                return None
            plan_by_id = {}
            for plan in data["scene_plans"]:
                try:
                    scene_id = int(plan.get("scene_id", 0))
                except:
                    scene_id = 0
                if scene_id:
                    plan_by_id[scene_id] = plan
            refined = []
            for scene in sequence_scenes:
                scene_id = int(scene.get("scene_id", 0) or 0)
                base = next((p for p in current_plans if int(p.get("scene_id", 0)) == scene_id), {}) or {}
                merged = dict(base)
                merged.update(plan_by_id.get(scene_id, {}))
                merged["scene_id"] = scene_id
                merged["segment_id"] = merged.get("segment_id") or scene.get("segment_id", 0)
                merged["sequence_id"] = merged.get("sequence_id") or scene.get("sequence_id", sequence_id)
                merged["sequence_role"] = merged.get("sequence_role") or scene.get("sequence_role", "")
                merged["shot_function"] = merged.get("shot_function") or scene.get("shot_function", "")
                merged["beat_type"] = merged.get("beat_type") or scene.get("beat_type", "")
                merged["emotional_turn"] = merged.get("emotional_turn") or scene.get("emotional_turn", "")
                merged["viewer_attention"] = merged.get("viewer_attention") or merged.get("key_focus") or scene.get("visual_anchor", "")
                merged["subtext_delivery"] = merged.get("subtext_delivery") or scene.get("visual_moment", "")[:150]
                merged["continuity_note"] = merged.get("continuity_note") or scene.get("transition_to_next", "") or scene.get("continuity_from_prev", "")
                merged["visual_contract"] = merged.get("visual_contract") or scene.get("visual_contract", "")
                merged["video_motion_contract"] = merged.get("video_motion_contract") or merged.get("character_action") or scene.get("primary_action", "")
                merged["distinctiveness_note"] = merged.get("distinctiveness_note") or "vary composition, pose, and supporting props from adjacent scenes"
                merged["alignment_notes"] = merged.get("alignment_notes") or scene.get("alignment_notes", "")
                refined.append(merged)
            return refined

        # Execute batches in parallel
        batch_results = {}
        with ThreadPoolExecutor(max_workers=MAX_PARALLEL) as executor:
            future_to_batch = {executor.submit(process_single_batch, b): b[0] for b in batches}

            for future in as_completed(future_to_batch):
                batch_num = future_to_batch[future]
                try:
                    result_batch_num, plans, used_fallback = future.result()
                    batch_results[result_batch_num] = plans
                    status = "fallback" if used_fallback else "OK"
                    self._log(f"     Batch {result_batch_num}/{total_batches}: {len(plans)} plans [{status}]")
                except Exception as e:
                    self._log(f"     Batch {batch_num} error: {e}", "ERROR")
                    batch_results[batch_num] = []

        # Combine results in order
        for batch_num in sorted(batch_results.keys()):
            all_plans.extend(batch_results[batch_num])

        if not all_plans:
            self._log("  ERROR: No scene plans created!", "ERROR")
            return StepResult("plan_scenes", StepStatus.FAILED, "No plans created")

        if True:
            initial_audits = self._audit_cinematic_sequences(director_plan, all_plans)
            weak_audits = [a for a in initial_audits if a.get("needs_refine")]
            if weak_audits:
                self._log(
                    (f"  {self.topic.title()} illustration audit before save: " if self._is_styled else "  Cinematic audit before save: ")
                    + "; ".join(
                        f"{a['sequence_id']} score={a['score']} issues={', '.join(a['issues'][:3]) or 'none'}"
                        for a in weak_audits[:8]
                    )
                )

                refined_plan_map = {int(p.get("scene_id", 0)): dict(p) for p in all_plans if p.get("scene_id")}
                for audit in weak_audits:
                    seq_id = audit.get("sequence_id")
                    seq_scenes = [s for s in director_plan if str(s.get("sequence_id", "")) == str(seq_id)]
                    current_plans = [refined_plan_map.get(int(s.get("scene_id", 0)), {}) for s in seq_scenes]
                    refined = refine_weak_sequence(seq_id, seq_scenes, current_plans)
                    if refined:
                        self._log(f"     -> Refined weak sequence {seq_id} ({audit['score']})")
                        for plan in refined:
                            scene_id = int(plan.get("scene_id", 0) or 0)
                            if scene_id:
                                refined_plan_map[scene_id] = plan

                all_plans = [refined_plan_map.get(int(s.get("scene_id", 0)), {}) for s in director_plan]

            final_audits = self._audit_cinematic_sequences(director_plan, all_plans)
            weak_after = [a for a in final_audits if a.get("needs_refine")]
            self._log(
                (f"  {self.topic.title()} illustration audit final: " if self._is_styled else "  Cinematic audit final: ")
                + "; ".join(
                    f"{a['sequence_id']} score={a['score']}"
                    for a in final_audits[:8]
                )
            )
            if weak_after:
                self._log(
                    "  [WARN] Remaining weak sequences: "
                    + "; ".join(
                        f"{a['sequence_id']} issues={', '.join(a['issues'][:3])}"
                        for a in weak_after[:8]
                    ),
                    "WARN"
                )

        # Save to Excel
        try:
            workbook.save_scene_planning(all_plans)
            workbook.save()
            self._log(f"  -> Saved {len(all_plans)} scene plans to Excel")

            # Update step status with duration
            elapsed = int(time.time() - step_start)
            workbook.update_step_status("step_6", "COMPLETED", len(all_plans), len(all_plans),
                f"{elapsed}s - {len(all_plans)} plans")

            return StepResult("plan_scenes", StepStatus.COMPLETED, "Success", {"plans": all_plans})
        except Exception as e:
            self._log(f"  ERROR: Could not save: {e}", "ERROR")
            elapsed = int(time.time() - step_start)
            workbook.update_step_status("step_6", "ERROR", 0, 0, f"{elapsed}s - {str(e)[:80]}")
            return StepResult("plan_scenes", StepStatus.FAILED, str(e))

    # =========================================================================
    # STEP 7: Táº O SCENE PROMPTS (BATCH)
    # =========================================================================

    def step_create_scene_prompts(
        self,
        project_dir: Path,
        code: str,
        workbook: PromptWorkbook,
        batch_size: int = 5
    ) -> StepResult:
        """
        Step 5: Táº¡o prompts cho tá»«ng scene (theo batch).

        Input: Äá»c director_plan, characters, locations tá»« Excel
        Output: ThÃªm scenes vÃ o sheet scenes
        """
        import time
        step_start = time.time()

        self._log("\n" + "="*60)
        self._log("[STEP 7/7] Táº¡o scene prompts...")
        self._log("="*60)

        # Read director plan
        try:
            director_plan = workbook.get_director_plan()
            if not director_plan:
                self._log("  ERROR: No director plan found! Run step 4 first.", "ERROR")
                return StepResult("create_scene_prompts", StepStatus.FAILED, "No director plan")
        except Exception as e:
            self._log(f"  ERROR: Could not read director plan: {e}", "ERROR")
            return StepResult("create_scene_prompts", StepStatus.FAILED, str(e))
        characters = workbook.get_characters()
        minor_char_ids = {c.id for c in characters if getattr(c, "is_child", False)}
        locations = workbook.get_locations()

        # Check existing scenes
        existing_scenes = workbook.get_scenes()
        existing_ids = {s.scene_id for s in existing_scenes} if existing_scenes else set()

        # Find scenes that need prompts
        pending_scenes = [self._ensure_scene_spec_fields(s, minor_char_ids=minor_char_ids) for s in director_plan if s.get("scene_id") not in existing_ids]

        if not pending_scenes:
            self._log(f"  -> ÄÃ£ cÃ³ {len(existing_scenes)} scenes, skip!")
            workbook.update_step_status("step_7", "COMPLETED", len(existing_scenes), len(existing_scenes), "Already done")
            return StepResult("create_scene_prompts", StepStatus.COMPLETED, "Already done")

        self._log(f"  -> Cáº§n táº¡o prompts cho {len(pending_scenes)} scenes...")

        # Read context
        story_analysis = {}
        try:
            story_analysis = workbook.get_story_analysis() or {}
        except:
            pass

        characters = workbook.get_characters()
        minor_char_ids = {c.id for c in characters if getattr(c, "is_child", False)}
        locations = workbook.get_locations()

        # Äá»c scene planning (káº¿ hoáº¡ch chi tiáº¿t tá»« step 4.5)
        scene_planning = {}
        try:
            plans = workbook.get_scene_planning() or []
            for plan in plans:
                scene_planning[plan.get("scene_id")] = plan
            self._log(f"  Loaded {len(scene_planning)} scene plans from step 4.5")
        except:
            pass

        context_lock = story_analysis.get("context_lock", "")

        # Build character/location lookup - bao gá»“m cáº£ image_file cho reference
        char_lookup = {}
        char_image_lookup = {}  # id -> image_file (nvc.png, nvp1.png...)
        for c in characters:
            if c.character_lock:
                char_lookup[c.id] = c.character_lock
            # Láº¥y image_file, máº·c Ä‘á»‹nh lÃ  {id}.png
            img_file = c.image_file if c.image_file else f"{c.id}.png"
            char_image_lookup[c.id] = img_file

        loc_lookup = {}
        loc_image_lookup = {}  # id -> image_file (loc_xxx.png)
        for loc in locations:
            if hasattr(loc, 'location_lock') and loc.location_lock:
                loc_lookup[loc.id] = loc.location_lock
            # Láº¥y image_file, máº·c Ä‘á»‹nh lÃ  {id}.png
            img_file = loc.image_file if hasattr(loc, 'image_file') and loc.image_file else f"{loc.id}.png"
            loc_image_lookup[loc.id] = img_file

        # Process in batches - PARALLEL API calls
        total_created = 0
        MAX_PARALLEL = max(1, min(int(self.config.get("max_parallel_api", 6)), 8))  # Throttle for single-key API use

        # Prepare all batches
        all_batches = []
        for batch_start in range(0, len(pending_scenes), batch_size):
            batch = pending_scenes[batch_start:batch_start + batch_size]
            batch_num = batch_start // batch_size + 1
            all_batches.append((batch_num, batch))

        total_batches = len(all_batches)
        self._log(f"  Processing {total_batches} batches in parallel (max {MAX_PARALLEL} concurrent)")

        def process_single_batch(batch_info):
            """Process a single batch â€” dÃ¹ng prompt_quality engine cho Step 7."""
            batch_num, batch = batch_info
            effective_batch = [
                self._merge_scene_plan_spec(scene, scene_planning.get(scene.get("scene_id")), minor_char_ids=minor_char_ids)
                for scene in batch
            ]

            min_dur = float(self.config.get("min_scene_duration", 5))
            max_dur = float(self.config.get("max_scene_duration", 8))

            if PROMPT_QUALITY_ENABLED:
                # â”€â”€ HIGH QUALITY PATH â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
                # DÃ¹ng structured template tá»« prompt_quality.py
                visual_style = story_analysis.get("visual_style", {}).get("cinematography", "cinematic")

                user_prompt = build_scene_prompt_request(
                    batch=effective_batch,
                    context_lock=context_lock,
                    char_lookup=char_lookup,
                    char_image_lookup=char_image_lookup,
                    loc_lookup=loc_lookup,
                    loc_image_lookup=loc_image_lookup,
                    scene_planning=scene_planning,
                    visual_style=visual_style,
                    min_dur=min_dur,
                    max_dur=max_dur,
                    minor_char_ids=minor_char_ids,
                    topic=self.topic,
                    style_profile=self.psychology_style_profile,
                )
                sys_prompt = get_scene_system_prompt(self.topic, self.psychology_style_profile)

                def repair_single_scene_with_api(original, plan, safe_char_ids, minor_refs, loc_id, reason):
                    """Try one focused AI repair before falling back to rule-based prompts."""
                    try:
                        repair_prompt = build_fix_prompt(
                            scene=original,
                            context_lock=context_lock,
                            char_lookup=char_lookup,
                            char_image_lookup=char_image_lookup,
                            loc_lookup=loc_lookup,
                            loc_image_lookup=loc_image_lookup,
                            scene_planning=scene_planning,
                            rejection_reason=reason,
                            minor_char_ids=minor_char_ids,
                            topic=self.topic,
                            style_profile=self.psychology_style_profile,
                        )
                        repair_response = self._call_api(
                            repair_prompt,
                            temperature=0.45,
                            max_tokens=4096,
                            system_prompt=sys_prompt,
                        )
                        repair_data = self._extract_json(repair_response) if repair_response else None
                        if not repair_data or not repair_data.get("scenes"):
                            return None
                        fixed = repair_data["scenes"][0]
                        fixed_img = postprocess_img_prompt(
                            fixed.get("img_prompt", ""),
                            safe_char_ids,
                            loc_id,
                            char_image_lookup,
                            loc_image_lookup,
                            context_lock,
                            minor_mode=bool(minor_refs),
                            minor_image_refs=minor_refs,
                            srt_text=original.get("srt_text", ""),
                            primary_subject=original.get("primary_subject", ""),
                            primary_action=original.get("primary_action", ""),
                            visual_anchor=original.get("visual_anchor", ""),
                            topic=self.topic,
                            style_profile=self.psychology_style_profile,
                        )
                        fixed_vid = postprocess_video_prompt(
                            fixed.get("video_prompt", ""),
                            original.get("duration", 6.0),
                            visual_moment=original.get("visual_moment", ""),
                            srt_text=original.get("srt_text", ""),
                            camera=original.get("camera", ""),
                            mood=original.get("mood", ""),
                            scene_kind=original.get("scene_kind", ""),
                            subject_mode=original.get("subject_mode", ""),
                            primary_subject=original.get("primary_subject", ""),
                            primary_action=original.get("primary_action", ""),
                            visual_anchor=original.get("visual_anchor", ""),
                            shot_function=original.get("shot_function", ""),
                            sequence_role=original.get("sequence_role", ""),
                            topic=self.topic,
                            style_profile=self.psychology_style_profile,
                            img_prompt=fixed.get("img_prompt", ""),
                        )
                        if not fixed_img or not fixed_vid or prompt_needs_single_frame_fallback(fixed_img):
                            return None
                        if self._is_styled:
                            fixed_issues = check_psychology_prompt_quality(
                                fixed_img,
                                original.get("srt_text", ""),
                                safe_char_ids,
                                style_profile=self.psychology_style_profile,
                                primary_subject=original.get("primary_subject", ""),
                                primary_action=original.get("primary_action", ""),
                                visual_anchor=original.get("visual_anchor", ""),
                            )
                            if fixed_issues:
                                return None
                        return fixed_img, fixed_vid
                    except Exception as exc:
                        self._log(f"    focused prompt repair failed: {exc}", "WARN")
                        return None

                MAX_RETRIES = 3
                for retry in range(MAX_RETRIES):
                    response = self._call_api(
                        user_prompt,
                        temperature=0.6,
                        max_tokens=8192,
                        system_prompt=sys_prompt,
                    )
                    if response:
                        data = self._extract_json(response)
                        if data and "scenes" in data:
                            # Post-process má»—i scene
                            for s in data["scenes"]:
                                scene_id = int(s.get("scene_id", 0))
                                original = next((o for o in effective_batch if int(o.get("scene_id", 0)) == scene_id), None)
                                if not original:
                                    continue
                                plan = scene_planning.get(scene_id, {})
                                char_ids = [c.strip() for c in (original.get("characters_used") or "").split(",") if c.strip() and c.strip() != "[]"]
                                safe_char_ids = [cid for cid in char_ids if cid not in minor_char_ids]
                                minor_refs = [char_image_lookup.get(cid, f"{cid}.png") for cid in char_ids if cid in minor_char_ids]
                                loc_id   = (original.get("location_used") or "").strip()
                                raw_img_prompt = s.get("img_prompt", "")
                                raw_video_prompt = s.get("video_prompt", "")
                                s["img_prompt"] = postprocess_img_prompt(
                                    raw_img_prompt,
                                    safe_char_ids, loc_id,
                                    char_image_lookup, loc_image_lookup,
                                    context_lock,
                                    minor_mode=bool(minor_refs),
                                    minor_image_refs=minor_refs,
                                    srt_text=original.get("srt_text", ""),
                                    primary_subject=original.get("primary_subject", ""),
                                    primary_action=original.get("primary_action", ""),
                                    visual_anchor=original.get("visual_anchor", ""),
                                    topic=self.topic,
                                    style_profile=self.psychology_style_profile,
                                )
                                if self._is_styled:
                                    psy_issues = check_psychology_prompt_quality(
                                        s.get("img_prompt", ""),
                                        original.get("srt_text", ""),
                                        safe_char_ids,
                                        style_profile=self.psychology_style_profile,
                                        primary_subject=original.get("primary_subject", ""),
                                        primary_action=original.get("primary_action", ""),
                                        visual_anchor=original.get("visual_anchor", ""),
                                    )
                                    if psy_issues or not str(raw_img_prompt or "").strip():
                                        reason = (
                                            "The first API image prompt was empty or failed psychology quality checks: "
                                            f"{', '.join(psy_issues or ['empty raw image prompt'])}. "
                                            "Regenerate this single scene using the locked primary_subject, primary_action, visual_anchor, "
                                            "Focus/key_focus, viewer_attention, and subtext_delivery. Return a concrete final prompt, not fallback scaffolding."
                                        )
                                        repaired = repair_single_scene_with_api(
                                            original, plan, safe_char_ids, minor_refs, loc_id, reason
                                        )
                                        if repaired:
                                            s["img_prompt"], s["video_prompt"] = repaired
                                        else:
                                            self._log(
                                                f"    Scene {scene_id}: focused repair failed, using rule fallback ({psy_issues or ['empty raw image prompt']})",
                                                "WARN",
                                            )
                                            img_p, vid_p = build_fallback_prompt(
                                                original,
                                                char_lookup,
                                                char_image_lookup,
                                                loc_lookup,
                                                loc_image_lookup,
                                                context_lock,
                                                minor_char_ids=minor_char_ids,
                                                scene_plan=plan,
                                                topic=self.topic,
                                                style_profile=self.psychology_style_profile,
                                            )
                                            s["img_prompt"] = img_p
                                            s["video_prompt"] = vid_p

                                if self._is_styled and not str(raw_video_prompt or "").strip():
                                    repaired = repair_single_scene_with_api(
                                        original,
                                        plan,
                                        safe_char_ids,
                                        minor_refs,
                                        loc_id,
                                        "The first API video prompt was empty. Regenerate this single scene with one concrete visible movement and emotional arc from the same keyframe.",
                                    )
                                    if repaired:
                                        s["img_prompt"], s["video_prompt"] = repaired
                                    else:
                                        img_p, vid_p = build_fallback_prompt(
                                            original,
                                            char_lookup,
                                            char_image_lookup,
                                            loc_lookup,
                                            loc_image_lookup,
                                            context_lock,
                                            minor_char_ids=minor_char_ids,
                                            scene_plan=plan,
                                            topic=self.topic,
                                            style_profile=self.psychology_style_profile,
                                        )
                                        s["img_prompt"] = img_p
                                        s["video_prompt"] = vid_p

                                s["video_prompt"] = postprocess_video_prompt(
                                    s.get("video_prompt", ""),
                                    original.get("duration", 6.0),
                                    visual_moment = original.get("visual_moment", ""),
                                    srt_text      = original.get("srt_text", ""),
                                    camera        = original.get("camera", ""),
                                    mood          = original.get("mood", ""),
                                    scene_kind    = original.get("scene_kind", ""),
                                    subject_mode  = original.get("subject_mode", ""),
                                    primary_subject = original.get("primary_subject", ""),
                                    primary_action = original.get("primary_action", ""),
                                    visual_anchor = original.get("visual_anchor", ""),
                                    shot_function = original.get("shot_function", ""),
                                    sequence_role = original.get("sequence_role", ""),
                                    topic         = self.topic,
                                    style_profile=self.psychology_style_profile,
                                    img_prompt=s.get("img_prompt", ""),
                                )

                                if prompt_needs_single_frame_fallback(s.get("img_prompt", "")):
                                    img_p, vid_p = build_fallback_prompt(
                                        original,
                                        char_lookup,
                                        char_image_lookup,
                                        loc_lookup,
                                        loc_image_lookup,
                                        context_lock,
                                        minor_char_ids=minor_char_ids,
                                        scene_plan=plan,
                                        topic=self.topic,
                                        style_profile=self.psychology_style_profile,
                                    )
                                    s["img_prompt"] = img_p
                                    s["video_prompt"] = vid_p

                            return (batch_num, effective_batch, data["scenes"], None)
                    time.sleep(2 ** retry)

                # Fallback náº¿u API tháº¥t báº¡i
                fallback_scenes = []
                for scene in effective_batch:
                    img_p, vid_p = build_fallback_prompt(
                        scene, char_lookup, char_image_lookup,
                        loc_lookup, loc_image_lookup, context_lock,
                        minor_char_ids=minor_char_ids,
                        scene_plan=scene_planning.get(scene.get("scene_id"), {}),
                        topic=self.topic,
                        style_profile=self.psychology_style_profile,
                    )
                    if self._is_styled:
                        char_ids = [c.strip() for c in (scene.get("characters_used") or "").split(",") if c.strip() and c.strip() != "[]"]
                        safe_char_ids = [cid for cid in char_ids if cid not in minor_char_ids]
                        minor_refs = [char_image_lookup.get(cid, f"{cid}.png") for cid in char_ids if cid in minor_char_ids]
                        repaired = repair_single_scene_with_api(
                            scene,
                            scene_planning.get(scene.get("scene_id"), {}),
                            safe_char_ids,
                            minor_refs,
                            (scene.get("location_used") or "").strip(),
                            "The batch API response failed or could not be parsed after retries. Regenerate this single scene with a complete concrete image prompt and video prompt.",
                        )
                        if repaired:
                            img_p, vid_p = repaired
                    fallback_scenes.append({
                        "scene_id": scene.get("scene_id"),
                        "img_prompt": img_p,
                        "video_prompt": vid_p,
                    })
                return (batch_num, effective_batch, fallback_scenes, "API failed â€” used quality fallback")

            else:
                # â”€â”€ LEGACY PATH (náº¿u prompt_quality khÃ´ng load Ä‘Æ°á»£c) â”€â”€â”€â”€â”€â”€â”€
                scenes_text = ""
                for scene in batch:
                    char_ids = [cid.strip() for cid in scene.get("characters_used", "").split(",") if cid.strip() and cid.strip() != "[]"]
                    char_ids = [cid for cid in char_ids if cid not in minor_char_ids]
                    char_parts = []
                    char_refs  = []
                    for cid in char_ids:
                        desc = char_lookup.get(cid, cid)
                        img  = char_image_lookup.get(cid, f"{cid}.png")
                        char_parts.append(f"{desc} ({img})")
                        char_refs.append(img)
                    loc_id   = scene.get("location_used", "")
                    loc_desc = loc_lookup.get(loc_id, loc_id)
                    loc_img  = loc_image_lookup.get(loc_id, f"{loc_id}.png") if loc_id else ""
                    if loc_desc and loc_img:
                        loc_desc = f"{loc_desc} ({loc_img})"
                    scene_id = scene.get("scene_id")
                    plan = scene_planning.get(scene_id, {})
                    plan_info = ""
                    if plan:
                        plan_info = (
                            f"\n- Intent: {plan.get('artistic_intent','')}"
                            f"\n- Shot: {plan.get('shot_type','')}"
                            f"\n- Mood: {plan.get('mood','')}"
                        )
                    scenes_text += (
                        f"\nScene {scene_id}:"
                        f"\n- Text: {scene.get('srt_text','')}"
                        f"\n- Visual: {scene.get('visual_moment','')}"
                        f"\n- Chars: {', '.join(char_parts)}"
                        f"\n- Location: {loc_desc}"
                        f"{plan_info}\n"
                    )

                prompt = (
                    f"Create detailed cinematic image prompts for {len(batch)} scenes.\n"
                    f"CONTEXT: {context_lock}\n"
                    f"RULES: Each prompt must include (character.png) and (location.png) refs "
                    f"and be UNIQUE per scene. 80-120 words per prompt.\n\n"
                    f"{scenes_text}\n"
                    f'Return JSON: {{"scenes":[{{"scene_id":1,"img_prompt":"...","video_prompt":"..."}}]}}'
                )

                MAX_RETRIES = 3
                for retry in range(MAX_RETRIES):
                    response = self._call_api(prompt, temperature=0.5, max_tokens=8192)
                    if response:
                        data = self._extract_json(response)
                        if data and "scenes" in data:
                            return (batch_num, effective_batch, data["scenes"], None)
                    time.sleep(2 ** retry)

                return (batch_num, effective_batch, None, "API failed")

        # Execute batches in parallel
        batch_results = {}
        with ThreadPoolExecutor(max_workers=MAX_PARALLEL) as executor:
            future_to_batch = {executor.submit(process_single_batch, b): b[0] for b in all_batches}

            for future in as_completed(future_to_batch):
                batch_num = future_to_batch[future]
                try:
                    result = future.result()
                    batch_results[result[0]] = result  # Store by batch_num
                    status = "OK" if result[2] else "FAILED"
                    self._log(f"     Batch {result[0]}/{total_batches}: [{status}]")
                except Exception as e:
                    self._log(f"     Batch {batch_num} error: {e}", "ERROR")

        # Process and save results sequentially (Excel not thread-safe)
        for batch_num in sorted(batch_results.keys()):
            _, batch, api_scenes, error = batch_results[batch_num]

            if not api_scenes:
                self._log(f"  Batch {batch_num}: skipped ({error})", "WARNING")
                continue

            # Validate vÃ  táº¡o fallback cho scenes thiáº¿u
            if len(api_scenes) < len(batch):
                self._log(f"  [WARN] Batch {batch_num}: API returned {len(api_scenes)}, expected {len(batch)} - ADDING MISSING")

                # TÃ¬m scene_ids Ä‘Ã£ cÃ³ tá»« API
                api_scene_ids = {int(s.get("scene_id", 0)) for s in api_scenes}

                # Táº¡o fallback cho scenes thiáº¿u
                for original in batch:
                    orig_id = int(original.get("scene_id", 0))
                    if orig_id not in api_scene_ids:
                        # Táº¡o fallback prompt
                        srt_text = original.get("srt_text", "")
                        visual_moment = original.get("visual_moment", "")
                        chars_used = original.get("characters_used") or ""
                        loc_used = original.get("location_used") or ""

                        img_p, vid_p = build_fallback_prompt(
                            original,
                            char_lookup,
                            char_image_lookup,
                            loc_lookup,
                            loc_image_lookup,
                            context_lock,
                            minor_char_ids=minor_char_ids,
                            scene_plan=scene_planning.get(orig_id, {}),
                            topic=self.topic,
                            style_profile=self.psychology_style_profile,
                        )
                        fallback_scene = {
                            "scene_id": orig_id,
                            "img_prompt": img_p,
                            "video_prompt": vid_p,
                        }
                        api_scenes.append(fallback_scene)
                        self._log(f"     -> Created fallback for scene {orig_id}")

            # Check duplicates - chá»‰ skip náº¿u >80% trÃ¹ng láº·p
            seen_prompts = set()
            duplicate_count = 0
            for s in api_scenes:
                prompt_key = s.get("img_prompt", "")[:100]
                if prompt_key in seen_prompts:
                    duplicate_count += 1
                else:
                    seen_prompts.add(prompt_key)

            if len(api_scenes) > 0 and duplicate_count > len(api_scenes) * 0.8:
                self._log(f"  Batch {batch_num}: >80% duplicates ({duplicate_count}/{len(api_scenes)}), skipped!", "ERROR")
                continue

            # Save scenes
            try:
                for scene_data in api_scenes:
                    scene_id = int(scene_data.get("scene_id", 0))
                    original = next((s for s in batch if int(s.get("scene_id", 0)) == scene_id), None)
                    if not original:
                        continue

                    img_prompt = scene_data.get("img_prompt", "")
                    video_prompt = scene_data.get("video_prompt", "")

                    # Post-process: ensure reference annotations
                    clean_chars_used = original.get("characters_used", "")
                    char_ids = [cid.strip() for cid in (clean_chars_used or "").split(",") if cid.strip() and cid.strip() != "[]"]
                    loc_id = original.get("location_used") or ""

                    if self._is_styled:
                        clean_chars_used = self.topic_prompts.normalize_scene_characters(clean_chars_used) if self.topic_prompts else clean_chars_used
                        if clean_chars_used not in {"", "nv1"}:
                            clean_chars_used = "nv1"
                        char_ids = ["nv1"] if clean_chars_used == "nv1" else []
                        loc_id = ""
                        ref_files = []
                        img_prompt = postprocess_img_prompt(
                            img_prompt,
                            char_ids,
                            loc_id,
                            char_image_lookup,
                            loc_image_lookup,
                            context_lock,
                            srt_text=original.get("srt_text", ""),
                            primary_subject=original.get("primary_subject", ""),
                            primary_action=original.get("primary_action", ""),
                            visual_anchor=original.get("visual_anchor", ""),
                            topic=self.topic,
                            style_profile=self.psychology_style_profile,
                        )
                        psy_issues = check_psychology_prompt_quality(
                            img_prompt,
                            original.get("srt_text", ""),
                            char_ids,
                            style_profile=self.psychology_style_profile,
                            primary_subject=original.get("primary_subject", ""),
                            primary_action=original.get("primary_action", ""),
                            visual_anchor=original.get("visual_anchor", ""),
                        )
                        if psy_issues:
                            fallback_img, fallback_vid = build_fallback_prompt(
                                original,
                                char_lookup,
                                char_image_lookup,
                                loc_lookup,
                                loc_image_lookup,
                                context_lock,
                                minor_char_ids=minor_char_ids,
                                scene_plan=scene_planning.get(scene_id, {}),
                                topic=self.topic,
                                style_profile=self.psychology_style_profile,
                            )
                            img_prompt = fallback_img
                            video_prompt = fallback_vid
                    else:
                        for cid in char_ids:
                            img_file = char_image_lookup.get(cid, f"{cid}.png")
                            if img_file and f"({img_file})" not in img_prompt:
                                img_prompt = img_prompt.rstrip(". ") + f" ({img_file})."

                        if loc_id:
                            loc_img = loc_image_lookup.get(loc_id, f"{loc_id}.png")
                            if loc_img and f"({loc_img})" not in img_prompt:
                                img_prompt = img_prompt.rstrip(". ") + f" (reference: {loc_img})."

                        if self.topic_prompts:
                            clean_chars_used = self.topic_prompts.normalize_scene_characters(clean_chars_used)
                        if clean_chars_used == "[]":
                            clean_chars_used = ""

                        ref_files = [char_image_lookup.get(cid, f"{cid}.png") for cid in char_ids]
                        if loc_id:
                            ref_files.append(loc_image_lookup.get(loc_id, f"{loc_id}.png"))

                        img_prompt = postprocess_img_prompt(
                            img_prompt,
                            char_ids,
                            loc_id,
                            char_image_lookup,
                            loc_image_lookup,
                            context_lock,
                            srt_text=original.get("srt_text", ""),
                            primary_subject=original.get("primary_subject", ""),
                            primary_action=original.get("primary_action", ""),
                            visual_anchor=original.get("visual_anchor", ""),
                            topic=self.topic,
                            style_profile=self.psychology_style_profile,
                        )

                    video_prompt = postprocess_video_prompt(
                        video_prompt,
                        original.get("duration", 6.0),
                        visual_moment=original.get("visual_moment", ""),
                        srt_text=original.get("srt_text", ""),
                        camera=original.get("camera", ""),
                        mood=original.get("mood", ""),
                        scene_kind=original.get("scene_kind", ""),
                        subject_mode=original.get("subject_mode", ""),
                        primary_subject=original.get("primary_subject", ""),
                        primary_action=original.get("primary_action", ""),
                        visual_anchor=original.get("visual_anchor", ""),
                        shot_function=original.get("shot_function", ""),
                        sequence_role=original.get("sequence_role", ""),
                        topic=self.topic,
                        style_profile=self.psychology_style_profile,
                        img_prompt=img_prompt,
                    )

                    scene = Scene(
                        scene_id=scene_id,
                        srt_start=original.get("srt_start", ""),
                        srt_end=original.get("srt_end", ""),
                        duration=original.get("duration", 0),
                        planned_duration=original.get("planned_duration") or original.get("duration", 0),
                        srt_text=original.get("srt_text", ""),
                        scene_kind=original.get("scene_kind", ""),
                        subject_mode=original.get("subject_mode", ""),
                        primary_subject=original.get("primary_subject", ""),
                        primary_action=original.get("primary_action", ""),
                        visual_anchor=original.get("visual_anchor", ""),
                        must_not_show=original.get("must_not_show", ""),
                        img_prompt=img_prompt,
                        video_prompt=video_prompt,
                        characters_used=clean_chars_used,
                        location_used="" if self._is_styled else original.get("location_used", ""),
                        reference_files=json.dumps(ref_files) if ref_files else "",
                        status_img="pending",
                        status_vid="pending"
                    )
                    workbook.add_scene(scene)
                    total_created += 1

                workbook.save()
            except Exception as e:
                self._log(f"  Batch {batch_num} save error: {e}", "ERROR")

        self._log(f"\n  -> Total: Created {total_created} scene prompts")

        elapsed = int(time.time() - step_start)
        if total_created > 0:
            # Update step status with duration
            workbook.update_step_status("step_7", "COMPLETED", total_created, total_created,
                f"{elapsed}s - {total_created} prompts")
            return StepResult("create_scene_prompts", StepStatus.COMPLETED, f"Created {total_created} scenes")
        else:
            workbook.update_step_status("step_7", "ERROR", 0, 0, f"{elapsed}s - No scenes created")
            return StepResult("create_scene_prompts", StepStatus.FAILED, "No scenes created")

    # =========================================================================
    # STEP 7.5: QA VALIDATION â€” phÃ¡t hiá»‡n & sá»­a prompt lá»‡ch SRT
    # =========================================================================

    def step_validate_prompts(
        self,
        project_dir: Path,
        code: str,
        workbook: PromptWorkbook,
        srt_entries: list,
    ) -> StepResult:
        """
        Step 7.5: RÃ  soÃ¡t cháº¥t lÆ°á»£ng prompt.

        Phase 1: Gá»­i batch scenes cho AI cháº¥m Ä‘iá»ƒm alignment SRT â†” img_prompt
        Phase 2: Chá»‰ sá»­a láº¡i cÃ¡c scene bá»‹ lá»‡ch (score < 7), 1 scene/láº§n

        Input: scenes sheet (Ä‘Ã£ táº¡o tá»« Step 7)
        Output: cáº­p nháº­t img_prompt + video_prompt cho scenes bá»‹ lá»‡ch
        """
        import time
        step_start = time.time()

        self._log("\n" + "="*60)
        self._log("[STEP 7.5] RÃ  soÃ¡t cháº¥t lÆ°á»£ng prompts...")
        self._log("="*60)

        if not PROMPT_QUALITY_ENABLED:
            self._log("  prompt_quality module not available, skip QA", "WARN")
            return StepResult("validate_prompts", StepStatus.COMPLETED, "Skipped - no module")

        # Read all scenes
        scenes = workbook.get_scenes()
        if not scenes:
            self._log("  No scenes found, skip QA", "WARN")
            return StepResult("validate_prompts", StepStatus.COMPLETED, "No scenes")

        # Read context
        story_analysis = workbook.get_story_analysis() or {}
        characters = workbook.get_characters()
        locations = workbook.get_locations()
        minor_char_ids = {c.id for c in characters if getattr(c, "is_child", False)}
        context_lock = story_analysis.get("context_lock", "")

        # Read director_plan for visual_moment and other context
        director_plan = workbook.get_director_plan() or []
        director_plan = [self._ensure_scene_spec_fields(scene, minor_char_ids=minor_char_ids) for scene in director_plan]
        dp_lookup = {s.get("scene_id"): s for s in director_plan}

        # Build lookups
        char_lookup = {}
        char_image_lookup = {}
        for c in characters:
            if c.character_lock:
                char_lookup[c.id] = c.character_lock
            char_image_lookup[c.id] = c.image_file if c.image_file else f"{c.id}.png"

        loc_lookup = {}
        loc_image_lookup = {}
        for loc in locations:
            if hasattr(loc, 'location_lock') and loc.location_lock:
                loc_lookup[loc.id] = loc.location_lock
            loc_image_lookup[loc.id] = (
                loc.image_file if hasattr(loc, 'image_file') and loc.image_file
                else f"{loc.id}.png"
            )

        # Read scene_planning
        scene_planning = {}
        try:
            plans = workbook.get_scene_planning() or []
            for plan in plans:
                scene_planning[plan.get("scene_id")] = plan
        except:
            pass

        normalized_img_count = 0
        normalized_video_count = 0
        for scene_obj in scenes:
            dp_scene = dp_lookup.get(scene_obj.scene_id)
            if not dp_scene:
                continue
            merged_scene = self._merge_scene_plan_spec(
                dp_scene,
                scene_planning.get(scene_obj.scene_id, {}),
                minor_char_ids=minor_char_ids,
            )
            char_ids = [c.strip() for c in (merged_scene.get("characters_used") or "").split(",") if c.strip() and c.strip() != "[]"]
            safe_char_ids = [cid for cid in char_ids if cid not in minor_char_ids]
            minor_refs = [char_image_lookup.get(cid, f"{cid}.png") for cid in char_ids if cid in minor_char_ids]
            loc_id = (merged_scene.get("location_used") or "").strip()
            old_img = scene_obj.img_prompt or ""
            new_img = postprocess_img_prompt(
                old_img,
                safe_char_ids,
                loc_id,
                char_image_lookup,
                loc_image_lookup,
                context_lock,
                minor_mode=bool(minor_refs),
                minor_image_refs=minor_refs,
                srt_text=merged_scene.get("srt_text", ""),
                primary_subject=merged_scene.get("primary_subject", ""),
                primary_action=merged_scene.get("primary_action", ""),
                visual_anchor=merged_scene.get("visual_anchor", ""),
                topic=self.topic,
                style_profile=self.psychology_style_profile,
            )
            if new_img and new_img != old_img:
                workbook.update_scene(scene_obj.scene_id, img_prompt=new_img)
                scene_obj.img_prompt = new_img
                normalized_img_count += 1
            old_video = scene_obj.video_prompt or ""
            new_video = postprocess_video_prompt(
                old_video,
                merged_scene.get("duration", getattr(scene_obj, "duration", 6.0) or 6.0),
                visual_moment=merged_scene.get("visual_moment", ""),
                srt_text=merged_scene.get("srt_text", ""),
                camera=merged_scene.get("camera", ""),
                mood=merged_scene.get("mood", ""),
                scene_kind=merged_scene.get("scene_kind", ""),
                subject_mode=merged_scene.get("subject_mode", ""),
                primary_subject=merged_scene.get("primary_subject", ""),
                primary_action=merged_scene.get("primary_action", ""),
                visual_anchor=merged_scene.get("visual_anchor", ""),
                shot_function=merged_scene.get("shot_function", ""),
                sequence_role=merged_scene.get("sequence_role", ""),
                topic=self.topic,
                style_profile=self.psychology_style_profile,
                img_prompt=scene_obj.img_prompt or "",
            )
            if new_video and new_video != old_video:
                workbook.update_scene(scene_obj.scene_id, video_prompt=new_video)
                scene_obj.video_prompt = new_video
                normalized_video_count += 1

        if normalized_img_count or normalized_video_count:
            workbook.save()
            if normalized_img_count:
                self._log(f"  Normalized {normalized_img_count} existing image prompts before QA")
            self._log(f"  Normalized {normalized_video_count} existing video prompts before QA")

        # â”€â”€ PHASE 1: DETECT â€” batch 15 scenes, AI cháº¥m Ä‘iá»ƒm â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
        self._log(f"  Phase 1: Detecting misaligned prompts ({len(scenes)} scenes)...")

        QA_BATCH_SIZE = 15
        MAX_PARALLEL = max(1, min(int(self.config.get("max_parallel_api", 6)), 8))

        # Build scene data for QA
        scene_data_list = []
        scenes_sorted = sorted(scenes, key=lambda x: int(getattr(x, "scene_id", 0) or 0))
        for idx, s in enumerate(scenes_sorted):
            dp_scene = self._merge_scene_plan_spec(
                dp_lookup.get(s.scene_id, {}),
                scene_planning.get(s.scene_id, {}),
                minor_char_ids=minor_char_ids,
            )
            prev_dp = self._merge_scene_plan_spec(
                dp_lookup.get(scenes_sorted[idx - 1].scene_id, {}) if idx > 0 else {},
                scene_planning.get(scenes_sorted[idx - 1].scene_id, {}) if idx > 0 else {},
                minor_char_ids=minor_char_ids,
            ) if idx > 0 else {}
            next_dp = self._merge_scene_plan_spec(
                dp_lookup.get(scenes_sorted[idx + 1].scene_id, {}) if idx + 1 < len(scenes_sorted) else {},
                scene_planning.get(scenes_sorted[idx + 1].scene_id, {}) if idx + 1 < len(scenes_sorted) else {},
                minor_char_ids=minor_char_ids,
            ) if idx + 1 < len(scenes_sorted) else {}
            scene_data_list.append({
                "scene_id": s.scene_id,
                "srt_text": s.srt_text or "",
                "img_prompt": s.img_prompt or "",
                "video_prompt": s.video_prompt or "",
                "sequence_id": dp_scene.get("sequence_id", ""),
                "sequence_role": dp_scene.get("sequence_role", ""),
                "primary_subject": dp_scene.get("primary_subject", ""),
                "primary_action": dp_scene.get("primary_action", ""),
                "visual_anchor": dp_scene.get("visual_anchor", ""),
                "prev_beat": f"{prev_dp.get('primary_subject', '')} / {prev_dp.get('primary_action', '')}".strip(" /"),
                "next_beat": f"{next_dp.get('primary_subject', '')} / {next_dp.get('primary_action', '')}".strip(" /"),
            })

        # Prepare batches
        qa_batches = []
        for i in range(0, len(scene_data_list), QA_BATCH_SIZE):
            batch = scene_data_list[i:i + QA_BATCH_SIZE]
            qa_batches.append(batch)

        self._log(f"  -> {len(qa_batches)} QA batches ({QA_BATCH_SIZE} scenes each)")

        # Process QA batches in parallel
        failed_scenes = []  # list of (scene_id, reason)

        def qa_single_batch(batch):
            """Review a single batch, return list of failed scene_ids."""
            user_prompt = build_qa_review_request(batch, topic=self.topic, style_profile=self.psychology_style_profile)
            response = self._call_api(
                user_prompt,
                temperature=0.2,
                max_tokens=4096,
                system_prompt=SYSTEM_PROMPT_QA_REVIEW,
            )
            if not response:
                return []

            data = self._extract_json(response)
            if not data or "reviews" not in data:
                return []

            fails = []
            for review in data["reviews"]:
                score = review.get("score", 10)
                # Stricter threshold: 8.5 instead of 7 for better narration alignment
                if score < 8.5:
                    fails.append((
                        review.get("scene_id"),
                        review.get("reason", f"score={score}"),
                    ))
            return fails

        with ThreadPoolExecutor(max_workers=MAX_PARALLEL) as executor:
            futures = {executor.submit(qa_single_batch, batch): i
                       for i, batch in enumerate(qa_batches)}
            for future in futures:
                try:
                    result = future.result(timeout=120)
                    failed_scenes.extend(result)
                except Exception as e:
                    self._log(f"  QA batch error: {e}", "WARN")

        # ── NEW: Check prompt uniqueness ──────────────────────────────────────
        self._log(f"  Checking prompt uniqueness across {len(scene_data_list)} scenes...")
        uniqueness_issues = self._check_prompt_uniqueness(scene_data_list)
        if uniqueness_issues:
            self._log(f"  -> Found {len(uniqueness_issues)} uniqueness issues")
            for issue in uniqueness_issues:
                scene_ids = issue["scene_ids"]
                similarity = issue["similarity"]
                reason = f"Prompt too similar to scene {scene_ids[1]} (similarity={similarity:.2f})"
                # Only flag the first scene in the pair to avoid double-fixing
                if (scene_ids[0], reason) not in failed_scenes:
                    failed_scenes.append((scene_ids[0], reason))
        else:
            self._log(f"  ✓ All prompts are sufficiently unique")

        if not failed_scenes:
            elapsed = f"{time.time() - step_start:.1f}"
            self._log(f"  âœ“ All {len(scenes)} prompts passed QA! ({elapsed}s)")
            return StepResult("validate_prompts", StepStatus.COMPLETED,
                              f"All {len(scenes)} passed")

        unique_failed = []
        seen_failed = set()
        for scene_id, reason in failed_scenes:
            if scene_id in seen_failed:
                continue
            seen_failed.add(scene_id)
            unique_failed.append((scene_id, reason))
        failed_scenes = unique_failed

        def _locally_correlates_failure(scene_id, reason):
            scene_obj = next((x for x in scenes_sorted if x.scene_id == scene_id), None)
            dp_scene = dp_lookup.get(scene_id, {})
            if not scene_obj or not dp_scene:
                return True
            prompt_text = scene_obj.img_prompt or ""
            reason_low = str(reason or "").lower()
            truncated_claim = any(term in reason_low for term in ["truncated", "cut off", "incomplete prompt"])
            if truncated_claim and len(prompt_text) >= 1000:
                has_visual_anchor = any(
                    str(dp_scene.get(key, "") or "").strip().lower()[:40] in prompt_text.lower()
                    for key in ["primary_subject", "primary_action", "visual_anchor"]
                    if str(dp_scene.get(key, "") or "").strip()
                )
                if has_visual_anchor or "depict this exact script idea" in prompt_text.lower():
                    return False
            if self._is_styled:
                dp_scene = self._merge_scene_plan_spec(
                    dp_scene,
                    scene_planning.get(scene_id, {}),
                    minor_char_ids=minor_char_ids,
                )
                char_ids = [c.strip() for c in (dp_scene.get("characters_used") or "").split(",") if c.strip() and c.strip() != "[]"]
                if check_psychology_prompt_quality(
                    prompt_text,
                    dp_scene.get("srt_text", ""),
                    char_ids,
                    style_profile=self.psychology_style_profile,
                    primary_subject=dp_scene.get("primary_subject", ""),
                    primary_action=dp_scene.get("primary_action", ""),
                    visual_anchor=dp_scene.get("visual_anchor", ""),
                ):
                    return True
            if prompt_needs_single_frame_fallback(prompt_text):
                return True
            unsupported = check_unsupported_prompt_details(
                srt_text=dp_scene.get("srt_text", ""),
                img_prompt=prompt_text,
                primary_subject=dp_scene.get("primary_subject", ""),
                primary_action=dp_scene.get("primary_action", ""),
                visual_anchor=dp_scene.get("visual_anchor", ""),
            )
            if unsupported:
                return True
            valid_keywords, missing = check_narration_keywords_in_prompt(
                dp_scene.get("srt_text", ""),
                prompt_text,
            )
            if not valid_keywords and missing:
                return True
            hard_fail_terms = [
                "missing", "different", "wrong", "unrelated", "object", "action",
                "subject", "anchor", "doesn't", "not in prompt",
            ]
            return any(term in reason_low for term in hard_fail_terms)

        if len(failed_scenes) >= max(25, int(len(scenes_sorted) * 0.45)):
            corroborated = [(scene_id, reason) for scene_id, reason in failed_scenes if _locally_correlates_failure(scene_id, reason)]
            self._log(
                f"  [Guard] High fail rate detected: {len(failed_scenes)}/{len(scenes_sorted)}. "
                f"Local validation corroborated {len(corroborated)} scenes."
            )
            if corroborated:
                failed_scenes = corroborated

        self._log(f"  -> Found {len(failed_scenes)} misaligned scenes: "
                  f"{[f[0] for f in failed_scenes]}")

        # â”€â”€ PHASE 2: FIX â€” 1 scene/láº§n, full context â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
        self._log(f"  Phase 2: Fixing {len(failed_scenes)} scenes...")

        min_accept_score = float(self.config.get("psychology_prompt_min_score", 9.0) or 9.0)

        def _score_candidate(candidate, dp_scene, safe_char_ids):
            scored_scene = {
                "srt_text": dp_scene.get("srt_text", ""),
                "characters_used": ",".join(safe_char_ids),
                "img_prompt": candidate.get("img_prompt", ""),
                "video_prompt": candidate.get("video_prompt", ""),
            }
            score_data = score_psychology_scene_prompt_pair(
                scored_scene,
                style_profile=self.psychology_style_profile,
            )
            return float(score_data.get("score", 0) or 0), score_data

        def _candidate_hard_alignment_issues(candidate, dp_scene, reason=""):
            img_prompt = str((candidate or {}).get("img_prompt", "") or "")
            reason_low = str(reason or "").lower()
            issues = []
            if self._is_styled:
                char_ids = [
                    c.strip()
                    for c in str(dp_scene.get("characters_used", "") or "").split(",")
                    if c.strip() and c.strip() != "[]"
                ]
                psy_issues = check_psychology_prompt_quality(
                    img_prompt,
                    dp_scene.get("srt_text", ""),
                    char_ids,
                    style_profile=self.psychology_style_profile,
                    primary_subject=dp_scene.get("primary_subject", ""),
                    primary_action=dp_scene.get("primary_action", ""),
                    visual_anchor=dp_scene.get("visual_anchor", ""),
                )
                issues.extend(psy_issues)
            unsupported = check_unsupported_prompt_details(
                srt_text=dp_scene.get("srt_text", ""),
                img_prompt=img_prompt,
                primary_subject=dp_scene.get("primary_subject", ""),
                primary_action=dp_scene.get("primary_action", ""),
                visual_anchor=dp_scene.get("visual_anchor", ""),
            )
            issues.extend(unsupported)
            hard_reason_terms = [
                "contradict", "different setting", "completely different", "wrong setting",
                "wrong action", "incorrectly forces", "unrelated", "not match", "does not match",
            ]
            if any(term in reason_low for term in hard_reason_terms):
                # A QA-confirmed contradiction is a hard failure unless the prompt now
                # clearly names the locked scene spec and avoids unsupported details.
                core_text = " ".join(str(dp_scene.get(k, "") or "") for k in [
                    "primary_subject", "primary_action", "visual_anchor",
                ]).lower()
                prompt_low = img_prompt.lower()
                core_hits = sum(
                    1 for token in set(re.findall(r"\b[a-zA-Z][a-zA-Z-]{3,}\b", core_text))
                    if token not in {"with", "that", "this", "from", "into", "through", "visual", "metaphor", "symbolic"}
                    and token in prompt_low
                )
                if core_hits < 3 or unsupported:
                    issues.append("qa-confirmed contradiction not resolved")
            return issues

        def _normalize_candidate(raw_img, raw_vid, dp_scene, safe_char_ids, minor_refs, loc_id):
            img = str(raw_img or "").strip()
            vid = str(raw_vid or "").strip()
            if img:
                img = postprocess_img_prompt(
                    img,
                    safe_char_ids,
                    loc_id,
                    char_image_lookup,
                    loc_image_lookup,
                    context_lock,
                    minor_mode=bool(minor_refs),
                    minor_image_refs=minor_refs,
                    srt_text=dp_scene.get("srt_text", ""),
                    primary_subject=dp_scene.get("primary_subject", ""),
                    primary_action=dp_scene.get("primary_action", ""),
                    visual_anchor=dp_scene.get("visual_anchor", ""),
                    topic=self.topic,
                    style_profile=self.psychology_style_profile,
                )
            if vid:
                vid = postprocess_video_prompt(
                    vid,
                    dp_scene.get("duration", 6.0),
                    visual_moment=dp_scene.get("visual_moment", ""),
                    srt_text=dp_scene.get("srt_text", ""),
                    camera=dp_scene.get("camera", ""),
                    mood=dp_scene.get("mood", ""),
                    scene_kind=dp_scene.get("scene_kind", ""),
                    subject_mode=dp_scene.get("subject_mode", ""),
                    primary_subject=dp_scene.get("primary_subject", ""),
                    primary_action=dp_scene.get("primary_action", ""),
                    visual_anchor=dp_scene.get("visual_anchor", ""),
                    shot_function=dp_scene.get("shot_function", ""),
                    sequence_role=dp_scene.get("sequence_role", ""),
                    topic=self.topic,
                    style_profile=self.psychology_style_profile,
                    img_prompt=img,
                )
            if not img or not vid:
                return None
            return {"img_prompt": img, "video_prompt": vid}

        fixed_count = 0
        for scene_id, reason in failed_scenes:
            # Get original scene data from director_plan
            dp_scene = dp_lookup.get(scene_id)
            if not dp_scene:
                self._log(f"  Scene {scene_id}: no director_plan data, skip", "WARN")
                continue
            dp_scene = self._merge_scene_plan_spec(
                dp_scene,
                scene_planning.get(scene_id, {}),
                minor_char_ids=minor_char_ids,
            )

            self._log(f"  Fixing scene {scene_id} (reason: {reason})...")

            char_ids = [c.strip() for c in (dp_scene.get("characters_used") or "").split(",") if c.strip() and c.strip() != "[]"]
            safe_char_ids = [cid for cid in char_ids if cid not in minor_char_ids]
            minor_refs = [char_image_lookup.get(cid, f"{cid}.png") for cid in char_ids if cid in minor_char_ids]
            loc_id = (dp_scene.get("location_used") or "").strip()
            best = None
            best_score = -1.0
            best_score_data = {}

            def _consider_candidate(label, raw_img, raw_vid):
                nonlocal best, best_score, best_score_data
                candidate = _normalize_candidate(raw_img, raw_vid, dp_scene, safe_char_ids, minor_refs, loc_id)
                if not candidate:
                    return
                hard_issues = _candidate_hard_alignment_issues(candidate, dp_scene, reason)
                if hard_issues:
                    self._log(f"    candidate {label}: rejected ({', '.join(hard_issues[:3])})", "WARN")
                    return
                score_value, score_data = _score_candidate(candidate, dp_scene, safe_char_ids)
                if score_value > best_score:
                    best = candidate
                    best_score = score_value
                    best_score_data = score_data
                    self._log(f"    candidate {label}: score={score_value:.2f}")

            current_scene = next((x for x in scenes_sorted if x.scene_id == scene_id), None)
            if current_scene:
                _consider_candidate("current+postprocess", current_scene.img_prompt or "", current_scene.video_prompt or "")

            fallback_img, fallback_vid = build_fallback_prompt(
                dp_scene,
                char_lookup,
                char_image_lookup,
                loc_lookup,
                loc_image_lookup,
                context_lock,
                minor_char_ids=minor_char_ids,
                scene_plan=scene_planning.get(scene_id, {}),
                topic=self.topic,
                style_profile=self.psychology_style_profile,
            )
            _consider_candidate("rule-fallback", fallback_img, fallback_vid)
            new_img = None
            new_vid = None

            for retry in range(3):
                guidance = (
                    f"{reason}. Attempt {retry + 1}/3. Improve the local score by making the prompt more concrete: "
                    "follow the locked primary_subject, primary_action, and visual_anchor; include Specific movement, "
                    "Emotional arc, and Performance direction; avoid repeated generic props unless they are in the SRT."
                )
                user_prompt = build_fix_prompt(
                    scene=dp_scene,
                    context_lock=context_lock,
                    char_lookup=char_lookup,
                    char_image_lookup=char_image_lookup,
                    loc_lookup=loc_lookup,
                    loc_image_lookup=loc_image_lookup,
                    scene_planning=scene_planning,
                    rejection_reason=guidance,
                    minor_char_ids=minor_char_ids,
                    topic=self.topic,
                    style_profile=self.psychology_style_profile,
                )
                response = self._call_api(
                    user_prompt,
                    temperature=0.45 + (retry * 0.08),
                    max_tokens=4096,
                    system_prompt=get_scene_system_prompt(self.topic, self.psychology_style_profile),
                )
                if not response:
                    time.sleep(2)
                    continue

                data = self._extract_json(response)
                if data and "scenes" in data and data["scenes"]:
                    s = data["scenes"][0]
                    new_img = s.get("img_prompt", "")
                    new_vid = s.get("video_prompt", "")

                    # Post-process
                    char_ids = [c.strip() for c in (dp_scene.get("characters_used") or "").split(",") if c.strip() and c.strip() != "[]"]
                    safe_char_ids = [cid for cid in char_ids if cid not in minor_char_ids]
                    minor_refs = [char_image_lookup.get(cid, f"{cid}.png") for cid in char_ids if cid in minor_char_ids]
                    loc_id = (dp_scene.get("location_used") or "").strip()

                    if new_img:
                        new_img = postprocess_img_prompt(
                            new_img, safe_char_ids, loc_id,
                            char_image_lookup, loc_image_lookup, context_lock,
                            minor_mode=bool(minor_refs),
                            minor_image_refs=minor_refs,
                            srt_text=dp_scene.get("srt_text", ""),
                            primary_subject=dp_scene.get("primary_subject", ""),
                            primary_action=dp_scene.get("primary_action", ""),
                            visual_anchor=dp_scene.get("visual_anchor", ""),
                            topic=self.topic,
                            style_profile=self.psychology_style_profile,
                        )
                        if self._is_styled and check_psychology_prompt_quality(
                            new_img,
                            dp_scene.get("srt_text", ""),
                            safe_char_ids,
                            style_profile=self.psychology_style_profile,
                            primary_subject=dp_scene.get("primary_subject", ""),
                            primary_action=dp_scene.get("primary_action", ""),
                            visual_anchor=dp_scene.get("visual_anchor", ""),
                        ):
                            fallback_img, fallback_vid = build_fallback_prompt(
                                dp_scene,
                                char_lookup,
                                char_image_lookup,
                                loc_lookup,
                                loc_image_lookup,
                                context_lock,
                                minor_char_ids=minor_char_ids,
                                scene_plan=scene_planning.get(scene_id, {}),
                                topic=self.topic,
                                style_profile=self.psychology_style_profile,
                            )
                            new_img = fallback_img
                            new_vid = fallback_vid
                    if new_vid:
                        new_vid = postprocess_video_prompt(
                            new_vid,
                            dp_scene.get("duration", 6.0),
                            visual_moment=dp_scene.get("visual_moment", ""),
                            srt_text=dp_scene.get("srt_text", ""),
                            camera=dp_scene.get("camera", ""),
                            mood=dp_scene.get("mood", ""),
                            scene_kind=dp_scene.get("scene_kind", ""),
                            subject_mode=dp_scene.get("subject_mode", ""),
                            primary_subject=dp_scene.get("primary_subject", ""),
                            primary_action=dp_scene.get("primary_action", ""),
                            visual_anchor=dp_scene.get("visual_anchor", ""),
                            shot_function=dp_scene.get("shot_function", ""),
                            sequence_role=dp_scene.get("sequence_role", ""),
                            topic=self.topic,
                            style_profile=self.psychology_style_profile,
                            img_prompt=new_img,
                        )
                    _consider_candidate(f"ai-repair-{retry + 1}", new_img, new_vid)
                    if best_score >= min_accept_score:
                        break
                time.sleep(2)

            if best:
                workbook.update_scene(scene_id, img_prompt=best["img_prompt"], video_prompt=best["video_prompt"])
                try:
                    workbook.save()
                except Exception as exc:
                    self._log(f"  Scene {scene_id}: save-after-fix failed: {exc}", "WARN")
                fixed_count += 1
                if best_score >= min_accept_score:
                    self._log(f"  Scene {scene_id} fixed with best score {best_score:.2f}")
                else:
                    missing = (
                        best_score_data.get("img", {}).get("missing", [])[:2]
                        + best_score_data.get("video", {}).get("missing", [])[:2]
                    )
                    self._log(
                        f"  Scene {scene_id}: saved best available score {best_score:.2f} below target {min_accept_score:.2f}; missing={missing}",
                        "WARN",
                    )
                self._log(f"  âœ“ Scene {scene_id} fixed!")
            else:
                self._log(f"  Scene {scene_id}: no valid candidate generated", "WARN")

        # Save all fixes
        workbook.save()

        elapsed = f"{time.time() - step_start:.1f}"
        self._log(f"  QA complete: {fixed_count}/{len(failed_scenes)} fixed ({elapsed}s)")

        return StepResult("validate_prompts", StepStatus.COMPLETED,
                          f"Fixed {fixed_count}/{len(failed_scenes)} scenes")

    def _remove_style_boilerplate(self, prompt: str) -> str:
        """Remove common style boilerplate so similarity focuses on scene-specific content."""
        text = str(prompt or "").lower()
        boilerplate_phrases = [
            "dark gray streetwear cool-flat psychology illustration",
            "matching the provided reference character",
            "provided reference character",
            "use the provided reference image",
            "preserve the reference character exactly",
            "reference character style",
            "channel art style",
            "psychology illustration",
            "clean illustration",
            "no real humans",
            "no photorealism",
            "no readable text",
            "no text",
            "no watermark",
            "flat design",
            "paper texture",
            "muted cool tones",
            "dark charcoal gray",
            "cool gray-lavender background",
            "soft oval shadow",
            "clean dark outline",
            "style",
            "palette",
            "character",
            "nv1",
        ]
        for phrase in boilerplate_phrases:
            text = text.replace(phrase, " ")
        text = re.sub(r"\([^)]*\.(?:png|jpg|jpeg|webp)\)", " ", text)
        text = re.sub(r"\b(?:visual focus|scene elements|body language|emotional tone|visual metaphor)\s*:", " ", text)
        text = re.sub(r"[^a-z0-9가-힣]+", " ", text)
        stopwords = {
            "the", "and", "with", "for", "from", "into", "that", "this", "show", "shows", "showing",
            "through", "while", "clean", "soft", "simple", "image", "scene", "visual", "focus", "tone",
            "elements", "body", "language", "emotional", "metaphor", "prompt", "must", "exact", "identity",
        }
        return " ".join(token for token in text.split() if len(token) > 2 and token not in stopwords)

    def _calculate_prompt_similarity(self, prompt_a: str, prompt_b: str) -> float:
        """Calculate Jaccard similarity after removing common style boilerplate."""
        clean_a = self._remove_style_boilerplate(prompt_a)
        clean_b = self._remove_style_boilerplate(prompt_b)
        words_a = set(clean_a.split())
        words_b = set(clean_b.split())
        if not words_a or not words_b:
            return 0.0
        return len(words_a & words_b) / len(words_a | words_b)

    def _check_prompt_uniqueness(self, scenes_data: list, threshold: float = 0.85) -> list:
        """Flag nearby scene prompts that remain too similar after boilerplate removal."""
        issues = []
        scenes_sorted = sorted(
            [scene for scene in scenes_data if scene.get("img_prompt")],
            key=lambda item: int(item.get("scene_id", 0) or 0),
        )
        for idx, scene_a in enumerate(scenes_sorted):
            for scene_b in scenes_sorted[idx + 1:idx + 4]:
                similarity = self._calculate_prompt_similarity(
                    scene_a.get("img_prompt", ""),
                    scene_b.get("img_prompt", ""),
                )
                if similarity > threshold:
                    issues.append({
                        "scene_ids": [scene_a.get("scene_id"), scene_b.get("scene_id")],
                        "similarity": similarity,
                        "reason": "Prompts too similar after style boilerplate removal",
                    })
        return issues

    # =========================================================================
    # MAIN: RUN ALL STEPS
    # =========================================================================

    def run_all_steps(
        self,
        project_dir: Path,
        code: str,
        log_callback: Callable = None,
        step_callback: Callable = None,   # fn(step_idx, status, msg="")
    ) -> bool:
        """
        Cháº¡y táº¥t cáº£ steps theo thá»© tá»±.
        Má»—i step kiá»ƒm tra xem Ä‘Ã£ xong chÆ°a, náº¿u xong thÃ¬ skip.

        Args:
            step_callback: fn(step_idx: int, status: str, msg: str = "")
                           status: "running" | "done" | "error"

        Returns:
            True náº¿u thÃ nh cÃ´ng (táº¥t cáº£ steps completed)
        """
        self.log_callback = log_callback
        project_dir = Path(project_dir)

        def _step(idx, status, msg=""):
            if step_callback:
                try:
                    step_callback(idx, status, msg)
                except Exception:
                    pass

        self._log("\n" + "="*70)
        self._log("  PROGRESSIVE PROMPTS GENERATOR")
        self._log("  Moi step luu vao Excel, co the resume neu fail")
        self._log("="*70)

        # Paths
        srt_path   = project_dir / f"{code}.srt"
        txt_path   = project_dir / f"{code}.txt"
        excel_path = project_dir / f"{code}_prompts.xlsx"

        if not srt_path.exists():
            self._log(f"ERROR: SRT not found: {srt_path}", "ERROR")
            return False

        # Parse SRT
        srt_entries = parse_srt_file(srt_path)
        if not srt_entries:
            self._log("ERROR: No SRT entries found!", "ERROR")
            return False

        self._log(f"  SRT: {len(srt_entries)} entries")

        # Read TXT if exists
        txt_content = ""
        if txt_path.exists():
            try:
                txt_content = txt_path.read_text(encoding='utf-8')
                self._log(f"  TXT: {len(txt_content)} chars")
            except:
                pass

        # Load/create workbook
        workbook = PromptWorkbook(excel_path).load_or_create()
        self._sync_runtime_config_to_workbook(workbook, code)

        # Step 1: Analyze story
        _step(1, "running", "Phan tich cau chuyen...")
        result = self.step_analyze_story(project_dir, code, workbook, srt_entries, txt_content)
        if result.status == StepStatus.FAILED:
            _step(1, "error", "Step 1 FAILED")
            self._log("Step 1 FAILED! Stopping.", "ERROR")
            return False
        _step(1, "done")

        # Step 1.5: Analyze story segments
        _step(2, "running", "Phan tich segments...")
        result = self.step_analyze_story_segments(project_dir, code, workbook, srt_entries, txt_content)
        if result.status == StepStatus.FAILED:
            _step(2, "error", "Step 2 FAILED")
            self._log("Step 1.5 FAILED! Stopping.", "ERROR")
            return False
        _step(2, "done")

        # Steps 2 & 3: Characters + Locations PARALLEL (doc lap nhau hoan toan)
        _step(3, "running", "Tao nhan vat...")
        _step(4, "running", "Tao boi canh...")

        step2_result = [None]
        step3_result = [None]

        def _run_chars():
            step2_result[0] = self.step_create_characters(
                project_dir, code, workbook, srt_entries, txt_content)

        def _run_locs():
            step3_result[0] = self.step_create_locations(
                project_dir, code, workbook, srt_entries, txt_content)

        from concurrent.futures import ThreadPoolExecutor, wait, ALL_COMPLETED
        with ThreadPoolExecutor(max_workers=2) as ex:
            f2 = ex.submit(_run_chars)
            f3 = ex.submit(_run_locs)
            wait([f2, f3], return_when=ALL_COMPLETED)

        if step2_result[0] and step2_result[0].status == StepStatus.FAILED:
            _step(3, "error", "Step 3 FAILED")
            self._log("Step 2 (characters) FAILED! Stopping.", "ERROR")
            return False
        _step(3, "done")

        if step3_result[0] and step3_result[0].status == StepStatus.FAILED:
            _step(4, "error", "Step 4 FAILED")
            self._log("Step 3 (locations) FAILED! Stopping.", "ERROR")
            return False
        _step(4, "done")

        # Step 4: Create director plan
        _step(5, "running", "Ke hoach dao dien...")
        result = self.step_create_director_plan(project_dir, code, workbook, srt_entries)
        if result.status == StepStatus.FAILED:
            _step(5, "error", "Step 5 FAILED")
            self._log("Step 4 FAILED! Stopping.", "ERROR")
            return False
        _step(5, "done")

        # Step 4.5: Len ke hoach chi tiet tung scene
        _step(6, "running", "Y do nghe thuat...")
        result = self.step_plan_scenes(project_dir, code, workbook)
        if result.status == StepStatus.FAILED:
            _step(6, "error", "Step 6 FAILED")
            self._log("Step 4.5 FAILED! Stopping.", "ERROR")
            return False
        _step(6, "done")

        # Step 7: Create scene prompts
        _step(7, "running", "Tao prompts tung scene...")
        result = self.step_create_scene_prompts(project_dir, code, workbook)
        if result.status == StepStatus.FAILED:
            _step(7, "error", "Step 7 FAILED")
            self._log("Step 5 FAILED!", "ERROR")
            return False
        _step(7, "done")

        # Step 7.5: QA Validation â€” detect & fix misaligned prompts
        self._log("\n" + "="*60)
        self._log("  [STEP 7.5] Ra soat chat luong prompts...")
        self._log("="*60)
        try:
            result = self.step_validate_prompts(project_dir, code, workbook, srt_entries)
            if result.status == StepStatus.FAILED:
                self._log("  [WARN] Step 7.5 (QA) failed - continuing anyway", "WARN")
            else:
                self._log(f"  -> QA: {result.message}")
        except Exception as e:
            self._log(f"  [WARN] Step 7.5 (QA) crashed - continuing anyway: {e}", "WARN")

        # Step 8: Generate Suno music prompts
        self._log("\n" + "="*60)
        self._log("  [STEP 8/8] Tao Suno music prompts...")
        self._log("="*60)
        try:
            result = self.step_generate_music_prompts(project_dir, code, workbook)
            if result.status == StepStatus.FAILED:
                self._log("  [WARN] Step 8 (music) failed - continuing anyway", "WARN")
            else:
                self._log("  -> Music prompts saved to 'music' sheet")
        except Exception as e:
            self._log(f"  [WARN] Step 8 (music) crashed - continuing anyway: {e}", "WARN")

        # Step 9: Generate thumbnail prompts
        self._log("\n" + "="*60)
        self._log("  [STEP 9/9] Tao thumbnail prompts...")
        self._log("="*60)
        try:
            result = self.step_generate_thumbnail_prompts(project_dir, code, workbook)
            if result.status == StepStatus.FAILED:
                self._log("  [WARN] Step 9 (thumbnail) failed - continuing anyway", "WARN")
            else:
                self._log("  -> Thumbnail prompts saved to 'thumbnail' sheet")
        except Exception as e:
            self._log(f"  [WARN] Step 9 (thumbnail) crashed - continuing anyway: {e}", "WARN")

        self._log("\n" + "="*70)
        self._log("  ALL STEPS COMPLETED!")
        self._log("="*70)

        return True

    # =========================================================================
    # STEP 8: SUNO MUSIC PROMPT GENERATION
    # =========================================================================

    def step_generate_music_prompts(
        self,
        project_dir: Path,
        code: str,
        workbook: PromptWorkbook,
    ) -> StepResult:
        """
        Step 8: Táº¡o Suno music prompts tá»« story data.

        Logic:
        1. TÃ­nh tá»•ng thá»i lÆ°á»£ng video tá»« director_plan
        2. Chia thÃ nh N tracks (má»—i track ~105s = 1:45 Suno average)
        3. Má»—i track: thu tháº­p ná»™i dung/cáº£m xÃºc â†’ gá»i DeepSeek API
        4. LÆ°u vÃ o music sheet (music_id, start_time, suno_prompt, ...)

        Suno prompt format (1 field duy nháº¥t):
            "[Style]. [Instruments]. [Mood/Emotion]. [Atmosphere]. No vocals."
        """
        SUNO_TRACK_SECONDS = 105   # Target 1:45/track (Suno max ~2:00)
        MIN_TRACKS         = 3
        MAX_TRACKS         = 30
        MAX_PARALLEL       = 3    # Batch size cho DeepSeek

        self._log(f"  Generating Suno music prompts for: {code}")

        # â”€â”€ 1. Load data tá»« Excel â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
        director_plan   = workbook.get_director_plan() or []
        story_segments  = workbook.get_story_segments() or []
        scene_plans     = workbook.get_scene_planning() or []
        story_analysis  = workbook.get_story_analysis() or {}

        if not director_plan:
            return StepResult("music_prompts", StepStatus.FAILED,
                              "No director_plan found - run Step 5 first")

        # â”€â”€ 2. TÃ­nh sá»‘ tracks â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
        total_seconds = sum(float(s.get("duration") or 0) for s in director_plan)
        if total_seconds < 30:
            return StepResult("music_prompts", StepStatus.FAILED,
                              f"Total duration too short: {total_seconds:.0f}s")

        num_tracks = max(MIN_TRACKS,
                         min(MAX_TRACKS, round(total_seconds / SUNO_TRACK_SECONDS)))
        self._log(f"  Video: {total_seconds:.0f}s = {total_seconds/60:.1f}min "
                  f"â†’ {num_tracks} tracks (~{total_seconds/num_tracks:.0f}s each)")

        # â”€â”€ 3. Build scene_plans lookup {scene_id â†’ plan} â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
        plan_by_id = {str(p.get("scene_id", "")): p for p in scene_plans}

        # â”€â”€ 4. Build story context â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
        setting = story_analysis.get("setting", "contemporary setting")
        context = story_analysis.get("context", "")[:300]
        genre   = story_analysis.get("genre", "drama")
        themes  = story_analysis.get("themes", "")
        if isinstance(themes, list):
            themes = ", ".join(themes)

        seg_summaries = []
        for seg in story_segments:
            seg_summaries.append(
                f"- [{seg.get('segment_name','?')}]: {seg.get('message','')[:120]}"
            )
        story_arc_text = "\n".join(seg_summaries[:16])

        # â”€â”€ 5. Divide scenes into tracks â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
        total_scenes = len(director_plan)
        track_infos  = []   # list of dicts with track metadata

        def _fmt_secs(s):
            s = float(s)
            h = int(s // 3600); m = int((s % 3600) // 60); sec = s % 60
            ms = int((sec % 1) * 1000)
            return f"{h:02d}:{m:02d}:{int(sec):02d},{ms:03d}"

        # Build cumulative start times
        cum_start = 0.0
        for sc in director_plan:
            sc["_abs_start"] = cum_start
            cum_start += float(sc.get("duration") or 0)

        for i in range(num_tracks):
            s_idx = int(i * total_scenes / num_tracks)
            e_idx = int((i + 1) * total_scenes / num_tracks) - 1
            e_idx = min(e_idx, total_scenes - 1)

            track_scenes = director_plan[s_idx: e_idx + 1]
            track_dur    = sum(float(sc.get("duration") or 0) for sc in track_scenes)
            start_sec    = track_scenes[0].get("_abs_start", 0)
            start_ts     = _fmt_secs(start_sec)

            # SRT text sample (first 300 chars concatenated)
            narration_sample = " ".join(
                str(sc.get("srt_text") or "")
                for sc in track_scenes[:20]
            )[:300]

            # Mood from scene_planning
            moods = []
            for sc in track_scenes:
                sp = plan_by_id.get(str(sc.get("scene_id") or sc.get("plan_id") or ""))
                if sp:
                    m = sp.get("mood", "")
                    if m and m not in moods:
                        moods.append(m)
            mood_summary = ", ".join(moods[:4]) if moods else "emotional"

            # Matching story segment
            seg_for_track = None
            for seg in story_segments:
                sr_s = int(seg.get("srt_range_start") or 0)
                sr_e = int(seg.get("srt_range_end") or 0)
                scene_srt_start = int(track_scenes[0].get("srt_indices", [s_idx+1])[0]
                                      if track_scenes[0].get("srt_indices")
                                      else s_idx + 1)
                if sr_s <= scene_srt_start <= sr_e:
                    seg_for_track = seg
                    break
            seg_name    = seg_for_track.get("segment_name", f"Part {i+1}") if seg_for_track else f"Part {i+1}"
            seg_message = seg_for_track.get("message", "")[:150] if seg_for_track else ""

            track_infos.append({
                "music_id":    str(i + 1),
                "start_time":  start_ts,
                "duration":    round(track_dur),
                "scene_range": f"{s_idx+1}-{e_idx+1}",
                "status":      "pending",
                "title":       f"Track {i+1}: {seg_name}",
                "mood":        mood_summary,
                # Filled by API below:
                "suno_prompt": "",
                "style_tags":  "",
                "suno_url":    "",
                # Internal context (not saved)
                "_narration":  narration_sample,
                "_seg_name":   seg_name,
                "_seg_msg":    seg_message,
                "_mood":       mood_summary,
                "_track_num":  i + 1,
                "_total":      num_tracks,
                "_duration":   round(track_dur),
            })

        self._log(f"  Built {len(track_infos)} track plans, calling DeepSeek API...")

        # â”€â”€ 6. System prompt â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
        SYSTEM_PROMPT = f"""You are a professional music composer and Suno AI prompt specialist.
Your task: write INSTRUMENTAL music prompts for a {genre} video/story.

STORY CONTEXT:
Setting: {setting}
{context}
Overall arc:
{story_arc_text}

OUTPUT RULES:
- Return a JSON array. Each element:
    {{"music_id": <int>, "title": <str>, "mood": <str>, "suno_prompt": <str>}}
- mood: SHORT descriptor, max 4 words (e.g. "melancholic, haunting", "hopeful, tender",
  "tense, foreboding", "uplifting, triumphant"). No long sentences.
- suno_prompt = ONE combined string Suno will use directly (max 220 chars). Format:
    "[mood]. [Style]. [Instruments]. [Tempo/feel]. No vocals, instrumental only."
  Example: "Melancholic, haunting. Cinematic ambient, sparse piano and low cello drones,
  slow and sorrowful tempo. No vocals, instrumental only."
- mood field and the start of suno_prompt must match (same words)
- DO NOT use generic phrases like "background music" or "relaxing"
- Match the SPECIFIC emotional content of each segment
- Vary instrumentation across tracks to create a musical journey
- Return ONLY valid JSON array, no markdown, no explanation."""

        # â”€â”€ 7. Process in parallel batches â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
        def _prompt_for_batch(batch: list) -> list:
            """Call API for one batch of tracks, return updated dicts."""
            batch_desc = []
            for t in batch:
                batch_desc.append(
                    f"Track {t['_track_num']}/{t['_total']} "
                    f"(scenes {t['scene_range']}, {t['_duration']}s):\n"
                    f"  Segment: {t['_seg_name']}\n"
                    f"  Story beat: {t['_seg_msg']}\n"
                    f"  Scene moods detected: {t['_mood']}\n"
                    f"  Narration excerpt: \"{t['_narration']}\"\n"
                )

            user_prompt = (
                f"Generate Suno music prompts for these {len(batch)} tracks:\n\n"
                + "\n---\n".join(batch_desc)
                + f"\n\nReturn JSON array with {len(batch)} objects, "
                  f"music_id matching: {[t['music_id'] for t in batch]}"
            )

            resp = self._call_api(
                user_prompt,
                temperature=0.75,
                max_tokens=2000,
                system_prompt=SYSTEM_PROMPT,
            )
            if not resp:
                return batch  # return unchanged

            parsed = self._extract_json(resp)
            if not isinstance(parsed, list):
                if isinstance(parsed, dict) and "tracks" in parsed:
                    parsed = parsed["tracks"]
                else:
                    return batch

            by_id = {str(item.get("music_id")): item for item in parsed
                     if isinstance(item, dict)}

            for t in batch:
                api = by_id.get(t["music_id"])
                if api:
                    raw_prompt = api.get("suno_prompt", "")
                    t["suno_prompt"] = raw_prompt[:250] if raw_prompt else ""
                    t["title"]       = api.get("title", t["title"])
                    # LÆ°u mood clean tá»« API (2-4 words) Ä‘á»ƒ merge vÃ o suno_prompt
                    clean_mood = api.get("mood", "").strip()
                    if clean_mood:
                        t["mood"] = clean_mood

            return batch

        # Split into batches of MAX_PARALLEL
        batches = []
        for i in range(0, len(track_infos), MAX_PARALLEL):
            batches.append(track_infos[i: i + MAX_PARALLEL])

        completed_tracks = [None] * len(track_infos)

        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = {executor.submit(_prompt_for_batch, batch): idx
                       for idx, batch in enumerate(batches)}
            for future in as_completed(futures):
                batch_idx = futures[future]
                try:
                    result_batch = future.result()
                    for t in result_batch:
                        global_idx = int(t["music_id"]) - 1
                        completed_tracks[global_idx] = t
                    self._log(f"    Batch {batch_idx+1}/{len(batches)}: "
                              f"{len(result_batch)} tracks [OK]")
                except Exception as e:
                    self._log(f"    Batch {batch_idx+1} failed: {e}", "WARN")

        # â”€â”€ 8. Build final track list (strip internal _ keys) â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
        final_tracks = []
        for t in completed_tracks:
            if t is None:
                continue
            clean = {k: v for k, v in t.items() if not k.startswith("_")}

            # Fallback náº¿u prompt quÃ¡ ngáº¯n
            if len(clean.get("suno_prompt", "")) < 30:
                clean["suno_prompt"] = (
                    f"Cinematic {genre} instrumental score. "
                    f"Piano with orchestral strings. "
                    f"{clean.get('mood','Emotional').capitalize()} atmosphere. "
                    f"No vocals, instrumental only."
                )

            # â”€â”€ Merge mood vÃ o Ä‘áº§u suno_prompt â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
            # Suno chá»‰ cÃ³ 1 Ã´ nháº­p â†’ gá»™p mood vÃ o Ä‘áº§u prompt
            # mood á»Ÿ Ä‘Ã¢y lÃ  clean 2-4 words tá»« API (khÃ´ng pháº£i list dÃ i)
            mood_val   = clean.get("mood", "").strip()
            prompt_val = clean.get("suno_prompt", "").strip()
            # Chá»‰ prepend náº¿u mood chÆ°a náº±m á»Ÿ Ä‘áº§u prompt
            if mood_val and not prompt_val.lower().startswith(mood_val.lower()[:12]):
                clean["suno_prompt"] = f"{mood_val.capitalize()}. {prompt_val}"

            final_tracks.append(clean)


        # â”€â”€ 9. Save to Excel â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
        try:
            workbook.save_music_tracks(final_tracks)
            self._log(f"  -> Saved {len(final_tracks)} tracks to 'music' sheet")
            for t in final_tracks:
                self._log(
                    f"     Track {t['music_id']:>2}: {t['start_time']} "
                    f"({t['duration']}s) | {t['title']}"
                )
                if t.get("suno_prompt"):
                    self._log(f"       Prompt: {t['suno_prompt'][:90]}...")
            return StepResult("music_prompts", StepStatus.COMPLETED,
                              f"{len(final_tracks)} tracks generated",
                              {"tracks": final_tracks})
        except Exception as e:
            self._log(f"  ERROR saving music sheet: {e}", "ERROR")
            return StepResult("music_prompts", StepStatus.FAILED, str(e))

    # =========================================================================
    # STEP 9: THUMBNAIL PROMPTS
    # =========================================================================

    def _generate_psychology_thumbnails(
        self,
        code: str,
        workbook: "PromptWorkbook",
        main_char: Any,
        sheet_title: str,
        sheet_text_thumb: str,
        scene_brief: str,
    ) -> "StepResult":
        """
        Generate psychology thumbnails with multi-step pipeline:
        1. Extract CTR concept
        2. Create visual scene
        3. Design text layout
        4. Combine into final prompt
        5. Critic check
        6. Rewrite if needed
        """
        import json
        from modules.excel_manager import Thumbnail

        self._log(f"  [PSY-THUMB] Using {self.topic} thumbnail pipeline")
        style = self.psychology_style_profile or {}
        image_style = style.get("image_style", "")
        thumbnail_style = style.get("thumbnail_style", image_style)
        negative_prompt = style.get("negative_prompt", "")
        thumbnail_negative_prompt = style.get("thumbnail_negative_prompt", "")
        if not thumbnail_negative_prompt:
            thumbnail_negative_prompt = negative_prompt
            thumbnail_negative_prompt = thumbnail_negative_prompt.replace(
                "no readable text",
                "no extra readable text except the exact requested thumbnail text",
            )
            thumbnail_negative_prompt = thumbnail_negative_prompt.replace(
                "no text",
                "no extra text except the exact requested thumbnail text",
            )
        # Resolve audience language for this channel (e.g. Spanish, Japanese, etc.)
        channel = resolve_psychology_reference_channel(
            self.config.get("reference_channel") or "", code,
        )
        channel_language = get_channel_language(channel, code)
        self._log(f"  [PSY-THUMB] Channel={channel}, language={channel_language}")
        audience_contract = self._psychology_audience_contract(strict=True)

        # STEP 1: Extract CTR concept
        concept_prompt = f"""
You are a YouTube thumbnail strategist for psychology explainer channels.

Input:
- Title: {sheet_title or "(empty)"}
- Thumbnail text: {sheet_text_thumb or "(empty)"}
{audience_contract}

Extract the core psychological hook for a high-CTR thumbnail.

Return ONLY valid JSON:
{{
  "emotion": "...",
  "visual_contrast": "...",
  "concrete_object": "...",
  "curiosity_trigger": "..."
}}

Rules:
- emotion: main psychological feeling (anxiety, control, obsession, confusion, etc.)
- visual_contrast: visual opposition that shows the conflict (clean vs messy, order vs chaos, etc.)
- concrete_object: 1-2 simple everyday objects that symbolize the concept; choose culturally familiar objects from the AUDIENCE INSIGHT BIBLE whenever possible
- curiosity_trigger: what makes viewer want to click

Keep it simple and thumbnail-readable. No abstract concepts that can't be drawn.
"""
        concept_resp = self._call_api(concept_prompt, temperature=0.6, max_tokens=400)
        concept_data = self._extract_json(concept_resp or "")
        if not isinstance(concept_data, dict):
            concept_data = {
                "emotion": "psychological tension",
                "visual_contrast": "control vs chaos",
                "concrete_object": "simple everyday object",
                "curiosity_trigger": sheet_title or "psychological concept"
            }

        emotion = str(concept_data.get("emotion", "psychological tension"))
        visual_contrast = str(concept_data.get("visual_contrast", "control vs chaos"))
        concrete_object = str(concept_data.get("concrete_object", "simple object"))

        # STEP 2: Create visual scene
        scene_prompt = f"""
You are a visual director for psychology YouTube thumbnails in a fixed channel style.

Create a thumbnail scene description.

Input:
- Title: {sheet_title}
- Emotion: {emotion}
- Visual contrast: {visual_contrast}
- Concrete object: {concrete_object}
- Channel thumbnail style: {thumbnail_style}
- Channel image style: {image_style}
- Negative rules: {thumbnail_negative_prompt}
{audience_contract}

Return ONLY valid JSON:
{{
  "scene_description": "...",
  "character_placement": "...",
  "character_emotion": "..."
}}

Rules:
- scene_description: describe the environment showing the visual contrast. Keep it simple, thumbnail-readable, and faithful to the channel style.
- scene_description may use an audience-specific setting, prop, ritual, or metaphor from the AUDIENCE INSIGHT BIBLE only when it strengthens the thumbnail idea; do not add one as a quota.
- character_placement: where to place {main_char.id} (slightly left, center-left, etc.) to leave negative space for text
- character_emotion: how {main_char.id} expresses the emotion through posture, tiny face, or surrounding props

Style must be exactly the channel thumbnail/image style above. Do not drift to a shared default TL style.
Audience fit must be obvious in one glance without adding extra readable text.
"""
        scene_resp = self._call_api(scene_prompt, temperature=0.65, max_tokens=500)
        scene_data = self._extract_json(scene_resp or "")
        if not isinstance(scene_data, dict):
            scene_data = {
                "scene_description": f"channel-style thumbnail environment showing {visual_contrast}",
                "character_placement": "slightly left or center-left",
                "character_emotion": f"posture showing {emotion}"
            }

        scene_desc = str(scene_data.get("scene_description", ""))
        char_placement = str(scene_data.get("character_placement", "slightly left"))
        char_emotion = str(scene_data.get("character_emotion", ""))

        # STEP 3: Design text layout
        text_design_prompt = f"""
You are a typography designer for high-CTR YouTube thumbnails.

Input text: "{sheet_text_thumb}"

Create text design specification for this {channel_language} text.

Return ONLY valid JSON:
{{
  "main_word": "...",
  "secondary_words": "...",
  "text_placement": "...",
  "hierarchy_note": "..."
}}

Rules:
- main_word: the most important word(s) that should be VERY LARGE
- secondary_words: remaining words that should be smaller but still bold
- text_placement: where to place text (right side, slightly above center, etc.)
- hierarchy_note: brief note on size/emphasis relationship

The main word will be placed on a yellow rectangle #FFD400, black bold condensed sans-serif.
"""
        text_resp = self._call_api(text_design_prompt, temperature=0.5, max_tokens=400)
        text_data = self._extract_json(text_resp or "")
        if not isinstance(text_data, dict) or not text_data.get("main_word"):
            # Fallback: split text_thumb by spaces and take first part as main
            words = (sheet_text_thumb or "").strip().upper().split()
            if len(words) >= 2:
                text_data = {
                    "main_word": words[0],
                    "secondary_words": " ".join(words[1:]),
                    "text_placement": "right side, slightly above center",
                    "hierarchy_note": "main word very large, secondary smaller"
                }
            else:
                text_data = {
                    "main_word": sheet_text_thumb.upper() if sheet_text_thumb else "TEXT",
                    "secondary_words": "",
                    "text_placement": "right side, center",
                    "hierarchy_note": "single large word"
                }

        main_word = str(text_data.get("main_word", "")).strip()
        secondary_words = str(text_data.get("secondary_words", "")).strip()
        text_placement = str(text_data.get("text_placement", "right side, slightly above center"))

        # STEP 4: Combine into final prompt
        secondary_text_line = ""
        if secondary_words:
            secondary_text_line = f'Place "{secondary_words}" smaller but still bold, tightly underneath or slightly offset. It can be black text without a box or on a smaller yellow tag with a layered look.'

        final_prompt_template = f"""
{thumbnail_style}

Use attached {main_char.id}.png briefly as the character reference; do not re-describe the character. Spend visual detail on the title-specific setting, action, emotion, and metaphor; use audience-specific props only when they clarify the idea.

Scene: {scene_desc}. Place {main_char.id} {char_placement}, {char_emotion}, leaving intentional negative space for text on the right. Keep the environment minimal, readable, and specific to the title rather than generic.

TEXT DESIGN - HIGH CTR REFINED:
Include the exact {channel_language} text: "{sheet_text_thumb}".
Place the text on the {text_placement}, not touching the top edge.
Make "{main_word}" the main focus: very large, bold condensed sans-serif (Anton / Bebas Neue style), black #000000, tight letter spacing.
Put "{main_word}" on a strong yellow rectangle #FFD400.
{secondary_text_line}
Use a slight 2-5 degree rotation for dynamic energy.
Add a very soft drop shadow only under the yellow rectangle, not heavy.
No gradients, no glow, no outline on text.
Text must feel like a bold graphic element, not a caption.

Lighting and rendering must match the channel style. Clean composition, emotionally intriguing, ultra sharp, YouTube thumbnail, aspect ratio 16:9. {thumbnail_negative_prompt}. No camera lens terms, no watermark.
"""

        # STEP 5: Critic check
        critic_prompt = f"""
You are a strict psychology thumbnail QA critic.

Review this thumbnail prompt and check for violations:

{final_prompt_template}

Return ONLY valid JSON:
{{
  "approved": true/false,
  "issues": ["..."],
  "fixed_prompt": "..." (only if not approved)
}}

Check for:
- Does it violate the channel style or negative rules? (VIOLATION)
- Does it reference {main_char.id}.png? (REQUIRED)
- Does it include exact text "{sheet_text_thumb}"? (REQUIRED)
- Does it specify yellow box #FFD400 for main word? (REQUIRED)
- Does it specify text placement and typography hierarchy? (REQUIRED)
- Is it too cluttered or complex for a thumbnail? (VIOLATION)
- Does it preserve the channel thumbnail style? (REQUIRED)

If violations found, rewrite the prompt to fix them.
"""
        critic_resp = self._call_api(critic_prompt, temperature=0.3, max_tokens=1200)
        critic_data = self._extract_json(critic_resp or "")

        final_img_prompt = final_prompt_template
        issues = []

        if isinstance(critic_data, dict):
            if not critic_data.get("approved", True):
                issues = critic_data.get("issues", [])
                fixed = str(critic_data.get("fixed_prompt", "")).strip()
                if fixed and len(fixed) > 200:
                    final_img_prompt = fixed
                    self._log(f"  [PSY-THUMB] Critic fixed issues: {'; '.join(issues[:3])}")

        # Create 3 variants with same base prompt but different version_desc
        thumbnails = []
        for idx, variant in enumerate([
            ("portrait_main", "main portrait style"),
            ("dramatic_scene", "dramatic scene emphasis"),
            ("youtube_ctr", "maximum CTR optimization")
        ], 1):
            version_desc, note = variant

            # Embed text overlay and highlight info into img_prompt since Thumbnail doesn't have those fields
            variant_prompt = final_img_prompt
            if idx == 1:
                variant_prompt = f"[VARIANT: {note}] " + final_img_prompt

            thumbnails.append({
                "thumb_id": idx,
                "version_desc": version_desc,
                "img_prompt": variant_prompt,
                "characters_used": main_char.id,
                "location_used": "",
                "reference_files": json.dumps([f"{main_char.id}.png"])
            })

        # Save to workbook
        workbook.clear_thumbnails()
        saved = 0
        for item in thumbnails:
            thumb = Thumbnail(
                thumb_id=item["thumb_id"],
                version_desc=item["version_desc"],
                img_prompt=item["img_prompt"],
                characters_used=item["characters_used"],
                location_used=item.get("location_used", ""),
                reference_files=item["reference_files"],
                img_path="",
                img_path_portrait="",
                status_img="pending",
                status_portrait="pending",
            )
            workbook.add_thumbnail(thumb)
            saved += 1

        workbook.save()
        self._log(f"  [PSY-THUMB] Generated {saved} {self.topic} thumbnails")
        if issues:
            self._log(f"  [PSY-THUMB] Issues fixed: {'; '.join(issues[:3])}")

        return StepResult("thumbnail_prompts", StepStatus.COMPLETED, f"{saved} psychology thumbnails generated")

    def step_generate_thumbnail_prompts(
        self,
        project_dir: Path,
        code: str,
        workbook: PromptWorkbook,
    ) -> StepResult:
        """Step 9: Tao 3 thumbnail prompts va luu vao sheet 'thumbnail'."""
        import re
        from modules.excel_manager import Thumbnail

        story_analysis = workbook.get_story_analysis() or {}
        characters = workbook.get_characters() or []
        locations = workbook.get_locations() or []
        scenes = workbook.get_scenes() or []
        sheet_ctx = self._fetch_thumbnail_sheet_context(code)
        sheet_title = str(sheet_ctx.get("title", "") or "").strip()
        sheet_text_thumb = str(sheet_ctx.get("text_thumb", "") or "").strip()

        if not characters:
            return StepResult("thumbnail_prompts", StepStatus.FAILED, "No characters")
        if not scenes:
            return StepResult("thumbnail_prompts", StepStatus.FAILED, "No scenes")

        try:
            existing = workbook.get_thumbnails()
            if existing and any((t.img_prompt or "").strip() for t in existing):
                return StepResult("thumbnail_prompts", StepStatus.COMPLETED, f"Already has {len(existing)} thumbnails")
        except Exception:
            pass

        non_child_chars = [c for c in characters if not getattr(c, "is_child", False)]
        main_char = next((c for c in non_child_chars if str(getattr(c, "role", "")).lower() in ("main", "protagonist", "lead")), None)
        if not main_char and non_child_chars:
            main_char = non_child_chars[0]
        if not main_char:
            main_char = characters[0]

        top_scenes = [s for s in scenes if getattr(s, "img_prompt", "")]
        top_scenes = sorted(top_scenes, key=lambda s: len(str(getattr(s, "srt_text", "") or "")), reverse=True)[:8]
        scene_lines = []
        for s in top_scenes:
            sid = getattr(s, "scene_id", "")
            txt = str(getattr(s, "srt_text", "") or "")[:120]
            scene_lines.append(f"- scene {sid}: {txt}")
        scene_brief = "\n".join(scene_lines) if scene_lines else "- (no scene summary)"

        is_psychology = self._is_styled

        if is_psychology:
            return self._generate_psychology_thumbnails(
                code=code,
                workbook=workbook,
                main_char=main_char,
                sheet_title=sheet_title,
                sheet_text_thumb=sheet_text_thumb,
                scene_brief=scene_brief,
            )

        # Story thumbnail generation continues below with existing logic
        def _truncate_words(text: str, limit: int = 90) -> str:
            s = re.sub(r"\s+", " ", str(text or "")).strip()
            if len(s) <= limit:
                return s
            cut = s[:limit]
            if " " in cut:
                cut = cut.rsplit(" ", 1)[0]
            return cut.strip(" .,-:;")

        def _split_hook_lines(text: str) -> List[str]:
            t = re.sub(r"\s+", " ", str(text or "")).strip()
            if not t:
                return []
            parts = re.split(r"[.!?;:,]+|\.\.\.|—|-", t)
            lines = [re.sub(r"\s+", " ", p).strip() for p in parts if re.sub(r"\s+", " ", p).strip()]
            if not lines:
                lines = [t]
            return [_truncate_words(ln, 90).upper() for ln in lines[:3]]

        def _extract_focus_tokens(text: str) -> List[str]:
            src = str(text or "")
            tokens: List[str] = []
            for m in re.findall(r"(?:[$€£]\s?\d[\d,\.]*[KMBkmb]?)|(?:\d[\d,\.]*\s?(?:AM|PM|A\.M\.|P\.M\.|YEARS?|MONTHS?|DAYS?))", src, flags=re.IGNORECASE):
                t = re.sub(r"\s+", " ", m).strip().upper()
                if t and t not in tokens:
                    tokens.append(t)

            raw_words = re.findall(r"[A-Za-z][A-Za-z'\-]{3,}", src.upper())
            stop = {
                "THAT", "THIS", "WITH", "FROM", "THEY", "THEM", "YOUR", "HAVE", "WERE", "WHEN",
                "THERE", "AFTER", "BEFORE", "ABOUT", "WOULD", "COULD", "SHOULD", "THEIR", "HIS",
                "HER", "SHE", "HE", "AND", "FOR", "BUT", "NOT", "YOU", "ARE", "WAS", "HAD",
            }
            for w in raw_words:
                if w in stop:
                    continue
                if w not in tokens:
                    tokens.append(w)
                if len(tokens) >= 12:
                    break
            return tokens

        def _clean_overlay_text(text: str, max_lines: int = 3, max_words_total: int = 22, max_words_per_line: int = 9) -> str:
            raw = str(text or "").replace("\r", "\n").strip()
            raw = raw.replace(" / ", "\n").replace(" | ", "\n")
            lines = []
            for ln in raw.split("\n"):
                l = _truncate_words(ln, 52).upper().strip()
                l = l.strip(" .,:;!?-")
                if l:
                    lines.append(l)
            # remove duplicates while preserving order
            uniq = []
            for ln in lines:
                if ln not in uniq:
                    uniq.append(ln)
            if not uniq:
                return ""

            cropped: List[str] = []
            total_words = 0
            for ln in uniq:
                words = [w for w in ln.split() if w]
                if not words:
                    continue
                words = words[:max_words_per_line]
                room = max_words_total - total_words
                if room <= 0:
                    break
                words = words[:room]
                if not words:
                    break
                cropped.append(" ".join(words))
                total_words += len(words)
                if len(cropped) >= max_lines:
                    break
            return "\n".join(cropped[:max_lines])

        def _generate_overlay_pack(title: str, text_thumb: str) -> List[Dict[str, Any]]:
            """
            Generate 3 high-CTR overlay variants + layout hints from INPUT!T/U.
            """
            base_layouts = [
                "subject_left_text_right_upper",
                "subject_right_text_left_upper",
                "subject_center_text_bottom",
            ]
            if not (title or text_thumb):
                return []

            prompt = f"""
You are a YouTube thumbnail copywriter.
Write 3 HIGH-CTR text overlays for emotional drama thumbnails.

INPUT TITLE:
{title or "(empty)"}

INPUT THUMB TEXT (core facts to preserve):
{text_thumb or "(empty)"}

Return ONLY valid JSON:
{{
  "variants": [
    {{
      "overlay": "LINE 1\\nLINE 2\\nLINE 3",
      "highlight_words": ["WORD1","WORD2"],
      "layout": "subject_left_text_right_upper"
    }},
    {{
      "overlay": "LINE 1\\nLINE 2\\nLINE 3",
      "highlight_words": ["WORD1","WORD2"],
      "layout": "subject_right_text_left_upper"
    }},
    {{
      "overlay": "LINE 1\\nLINE 2\\nLINE 3",
      "highlight_words": ["WORD1","WORD2"],
      "layout": "subject_center_text_bottom"
    }}
  ]
}}

Rules:
- Overlay must sound natural, punchy, human, not robotic.
- Keep core facts/entities/numbers from INPUT THUMB TEXT.
- 2-3 lines only.
- MAX 22 WORDS total.
- Each line 5-9 words ideal.
- ALL CAPS.
- 3 variants must be meaningfully different hooks, not reword duplicates.
- highlight_words must appear in overlay exactly.
- No weird punctuation, no trailing dots, no broken words.
- Story genre audience watches on TV: text can be longer, but keep clean line breaks and high readability.
"""
            resp = self._call_api(prompt, temperature=0.45, max_tokens=1400)
            data = self._extract_json(resp or "")
            variants = data.get("variants") if isinstance(data, dict) else None
            if not isinstance(variants, list):
                return []

            out: List[Dict[str, Any]] = []
            for i, v in enumerate(variants[:3]):
                if not isinstance(v, dict):
                    continue
                ov = _clean_overlay_text(v.get("overlay", ""))
                if not ov:
                    continue
                hls = v.get("highlight_words", [])
                if not isinstance(hls, list):
                    hls = []
                hls = [str(x).strip().upper() for x in hls if str(x).strip()]
                layout = str(v.get("layout", "") or "").strip()
                if not layout:
                    layout = base_layouts[i]
                out.append({"overlay": ov, "highlight_words": hls, "layout": layout})
            return out

        def _normalize_overlay(overlay: str, title: str, text_thumb: str, variant_idx: int = 0) -> str:
            raw = str(overlay or "").replace("\r", "\n").strip()
            raw = raw.replace(" / ", "\n").replace(" | ", "\n")
            model_lines = [ln.strip(" -\t") for ln in raw.split("\n") if ln.strip(" -\t")]
            hook_lines = _split_hook_lines(text_thumb)
            title_line = _truncate_words(str(title or ""), 80).upper()
            lines: List[str] = []

            if hook_lines:
                base = hook_lines[0]
                alt = hook_lines[1] if len(hook_lines) > 1 else ""
                reveal = hook_lines[2] if len(hook_lines) > 2 else ""
                # Deterministic variants from INPUT!U to avoid model drift.
                if variant_idx == 0:
                    lines = [base, alt or title_line, reveal or "THEN SHE TOLD THE TRUTH"]
                elif variant_idx == 1:
                    lines = [base, reveal or alt or title_line, "THEN EVERYTHING CHANGED"]
                else:
                    lines = [base, alt or title_line, "WHO KNEW THE TRUTH"]
            else:
                lines = [_truncate_words(ln, 64).upper() for ln in model_lines[:3]]

            lines = [ln for ln in lines if ln]
            if not lines:
                base = title
                if base:
                    base = re.sub(r"\s+", " ", base).strip()
                    lines = [base]
            if not lines:
                lines = ["SHOCKING TRUTH REVEALED"]
            cleaned: List[str] = []
            for ln in lines:
                l = _truncate_words(ln, 90).upper()
                if l and l not in cleaned:
                    cleaned.append(l)
                if len(cleaned) >= 3:
                    break
            lines = cleaned[:3]

            # Enforce: if INPUT!U exists, overlay must keep at least one key hook line from text thumb.
            if hook_lines and not any(h in "\n".join(lines) for h in hook_lines[:2]):
                if lines:
                    lines[0] = hook_lines[0]
                else:
                    lines = [hook_lines[0]]
            return _clean_overlay_text("\n".join(lines), max_lines=3, max_words_total=22, max_words_per_line=9)

        def _pick_highlights(overlay: str, highlights: Any, seed_tokens: List[str], variant_idx: int = 0) -> List[str]:
            if isinstance(highlights, list):
                vals = [str(x).strip().upper() for x in highlights if str(x).strip()]
            else:
                vals = []

            overlay_upper = overlay.upper()
            cleaned: List[str] = []
            for h in vals:
                if h and h in overlay_upper and h not in cleaned:
                    cleaned.append(h)

            for s in seed_tokens:
                if s and s in overlay_upper and s not in cleaned:
                    cleaned.append(s)

            if not cleaned:
                tokens = re.findall(r"[A-Z0-9$]{4,}", overlay_upper)
                for tk in tokens:
                    if tk not in cleaned:
                        cleaned.append(tk)
                    if len(cleaned) >= 6:
                        break
            if not cleaned:
                return []

            shift = variant_idx % len(cleaned)
            rotated = cleaned[shift:] + cleaned[:shift]
            return rotated[:3]

        def _build_img_prompt(base_prompt: str, overlay: str, highlights: List[str], layout_hint: str) -> str:
            p = re.sub(r"\s+", " ", str(base_prompt or "")).strip()
            if not p:
                p = "Cinematic YouTube thumbnail, emotional drama, photorealistic, high contrast lighting."
            layout_guides = {
                "subject_left_text_right_upper": (
                    "Composition: subject on LEFT 1/3 of frame. Reserve RIGHT 2/3 as dedicated text area. "
                    "Place text in a tall stacked block across the right side. Do not place text over face, eyes, or hands."
                ),
                "subject_right_text_left_upper": (
                    "Composition: subject on RIGHT 1/3 of frame. Reserve LEFT 2/3 as dedicated text area. "
                    "Place text in a tall stacked block across the left side. Do not place text over face, eyes, or hands."
                ),
                "subject_center_text_bottom": (
                    "Composition: subject slightly off-center occupying ~1/3 frame. Reserve remaining ~2/3 for multi-line text block. "
                    "Keep chest/face area unobstructed."
                ),
            }
            layout_text = layout_guides.get(layout_hint, layout_guides["subject_center_text_bottom"])
            overlay_verbatim = str(overlay or "").strip()
            overlay_render_text = (
                overlay_verbatim
                .replace("<<BEGIN_TEXT>>", "")
                .replace("<<END_TEXT>>", "")
                .strip()
            )
            p += (
                f" {layout_text}"
                " Text block scale: very large story headline, about 30-45% of frame height, tuned for TV readability."
                " Text block behavior: adaptive font size, automatic line-wrap, preserve full text content."
                " Ultra realistic, cinematic depth of field, 4K quality, clean composition."
                f" Render on-image text EXACTLY as this content only: {overlay_render_text}"
                " Do NOT render any helper labels, bracketed tags, placeholders, or meta-instructions."
                f" Highlight words: {', '.join(highlights) if highlights else 'NONE'}."
                " Font: EXTRA-BOLD sans-serif, white + yellow emphasis, heavy shadow and stroke for mobile readability."
                " No watermark, no logo, no extra text clutter."
            )
            return p.strip()

        def _enhance_visual_prompt(
            base_prompt: str,
            variant_desc: str,
            overlay_text: str,
            layout_hint: str,
            title: str,
            text_thumb: str,
            main_character_line: str,
            locations_info: str,
            scene_highlights: str,
            is_psychology: bool = False,
        ) -> str:
            """
            Use DeepSeek as visual director to improve specificity while keeping story hook.
            """
            if is_psychology:
                director_prompt = f"""
You are a senior YouTube thumbnail visual director for psychology explainer channels.
Rewrite and improve ONE image prompt for maximum click-through, with this channel's fixed style.

Output: ONE single English paragraph prompt only. No JSON. No bullets. No markdown.

Hard requirements:
- Channel style: {self.psychology_style_profile.get('thumbnail_style') or self.psychology_style_profile.get('image_style')}
- Palette: {self.psychology_style_profile.get('palette')}
- Use the provided reference image only as the identity/style anchor; preserve the reference character exactly and do not describe the character in detail.
- Include 1-3 simple title-specific props/settings or secondary figures/objects that support the psychological concept and match the channel style; use audience-specific props only when they clarify the idea.
- Keep composition clean for text readability.
- Negative rules: {self.psychology_style_profile.get('negative_prompt')}
- Do not include any rendered text content inside scene objects.

Context:
- Variant: {variant_desc}
- Layout hint: {layout_hint}
- Title: {title or "(empty)"}
- Text thumb (verbatim overlay source): {text_thumb or "(empty)"}
- Main character: {main_character_line}
- Scene highlights: {scene_highlights[:650]}
- Existing draft prompt: {base_prompt or "(empty)"}
- Overlay text to support (do not rewrite here): {overlay_text[:400]}
"""
            else:
                director_prompt = f"""
You are a senior YouTube thumbnail visual director for emotional story channels.
Rewrite and improve ONE image prompt for maximum click-through, with realism and cinematic storytelling.

Output: ONE single English paragraph prompt only. No JSON. No bullets. No markdown.

Hard requirements:
- Keep emotional-drama style similar to high-performing thumbnails.
- One dominant main subject with strong restrained emotion.
- Optional secondary subject/background reaction, slightly blurred.
- Include 2-4 meaningful storytelling props that imply reveal/twist (documents, phone call time, police lights reflection, coffee cup, open laptop, keycard, safe, etc.) based on context.
- Specify camera language clearly: shot size + angle.
- Specify lighting language clearly: high-contrast, warm/cool contrast, mood.
- Keep composition clean for text readability.
- Keep photorealistic, cinematic, 4K, shallow depth of field.
- No logos/watermarks/clutter.
- Do not include any rendered text content inside scene objects.

Context:
- Variant: {variant_desc}
- Layout hint: {layout_hint}
- Title: {title or "(empty)"}
- Text thumb (verbatim overlay source): {text_thumb or "(empty)"}
- Main character: {main_character_line}
- Locations: {locations_info[:500]}
- Scene highlights: {scene_highlights[:650]}
- Existing draft prompt: {base_prompt or "(empty)"}
- Overlay text to support (do not rewrite here): {overlay_text[:400]}
"""
            resp = self._call_api(director_prompt, temperature=0.45, max_tokens=900)
            if not resp:
                return base_prompt or ""
            out = " ".join(str(resp).split()).strip().strip("\"'")
            if len(out) < 80:
                return base_prompt or ""
            return out

        def _is_valid_candidate(item: Dict[str, Any]) -> bool:
            img_prompt = str(item.get("img_prompt", "") or "").strip()
            overlay = str(item.get("text_overlay", "") or "").strip()
            highlights = item.get("highlight_words", [])
            if not isinstance(highlights, list):
                return False
            if not img_prompt or not overlay:
                return False
            return True

        story_analysis = workbook.get_story_analysis() or {}
        characters = workbook.get_characters() or []
        locations = workbook.get_locations() or []
        scenes = workbook.get_scenes() or []
        sheet_ctx = self._fetch_thumbnail_sheet_context(code)
        sheet_title = str(sheet_ctx.get("title", "") or "").strip()
        sheet_text_thumb = str(sheet_ctx.get("text_thumb", "") or "").strip()

        if not characters:
            return StepResult("thumbnail_prompts", StepStatus.FAILED, "No characters")
        if not scenes:
            return StepResult("thumbnail_prompts", StepStatus.FAILED, "No scenes")

        try:
            existing = workbook.get_thumbnails()
            if existing and any((t.img_prompt or "").strip() for t in existing):
                return StepResult("thumbnail_prompts", StepStatus.COMPLETED, f"Already has {len(existing)} thumbnails")
        except Exception:
            pass

        non_child_chars = [c for c in characters if not getattr(c, "is_child", False)]
        main_char = next((c for c in non_child_chars if str(getattr(c, "role", "")).lower() in ("main", "protagonist", "lead")), None)
        if not main_char and non_child_chars:
            main_char = non_child_chars[0]
        if not main_char:
            main_char = characters[0]

        story_context = str(story_analysis.get("context", "") or "")[:800]
        genre = str(story_analysis.get("genre", "drama") or "drama")
        setting = str(story_analysis.get("setting", "") or "")[:300]

        top_scenes = [s for s in scenes if getattr(s, "img_prompt", "")]
        top_scenes = sorted(top_scenes, key=lambda s: len(str(getattr(s, "srt_text", "") or "")), reverse=True)[:8]
        scene_lines = []
        for s in top_scenes:
            sid = getattr(s, "scene_id", "")
            txt = str(getattr(s, "srt_text", "") or "")[:120]
            scene_lines.append(f"- scene {sid}: {txt}")
        scene_brief = "\n".join(scene_lines) if scene_lines else "- (no scene summary)"

        char_lines = []
        for c in non_child_chars[:6]:
            char_lines.append(f"- {c.id} ({c.name}): {c.character_lock or c.english_prompt}")
        chars_info = "\n".join(char_lines) if char_lines else f"- {main_char.id} ({main_char.name})"

        loc_lines = []
        for loc in locations[:4]:
            loc_lines.append(f"- {loc.id} ({loc.name}): {getattr(loc, 'location_lock', '') or getattr(loc, 'english_prompt', '')}")
        locs_info = "\n".join(loc_lines) if loc_lines else "- loc_1 (main setting)"

        is_psychology = self._is_styled

        if is_psychology:
            return self._generate_psychology_thumbnails(
                code=code,
                workbook=workbook,
                main_char=main_char,
                sheet_title=sheet_title,
                sheet_text_thumb=sheet_text_thumb,
                scene_brief=scene_brief,
            )

        ref_files_default = "[]"
        style_instruction = """- Each img_prompt must be photorealistic and cinematic.
- Keep protagonist as central focus in all 3 prompts.
- Follow emotional-drama thumbnail style: power reversal, shocking reveal, restrained but intense facial emotion."""

        generator_prompt = f"""
Create exactly 3 YouTube thumbnail prompts for this {genre} story video.
Return ONLY valid JSON:
{{
  "thumbnails": [
    {{
      "thumb_id": 1,
      "version_desc": "portrait_main",
      "img_prompt": "...",
      "text_overlay": "...",
      "highlight_words": ["...", "..."],
      "characters_used": "{main_char.id}",
      "location_used": "",
      "reference_files": {ref_files_default}
    }},
    {{
      "thumb_id": 2,
      "version_desc": "dramatic_scene",
      "img_prompt": "...",
      "text_overlay": "...",
      "highlight_words": ["...", "..."],
      "characters_used": "{main_char.id}",
      "location_used": "",
      "reference_files": {ref_files_default}
    }},
    {{
      "thumb_id": 3,
      "version_desc": "youtube_ctr",
      "img_prompt": "...",
      "text_overlay": "...",
      "highlight_words": ["...", "..."],
      "characters_used": "{main_char.id}",
      "location_used": "",
      "reference_files": {ref_files_default}
    }}
  ]
}}

Rules:
{style_instruction}
- Add annotation style references when possible: ({main_char.id}.png), (loc_xxx.png)
- 3 prompts must be different concepts with high click-through potential.
- Tone must maximize curiosity and emotional tension to increase click-through rate.
- text_overlay is required and MUST use INPUT!U text verbatim when available.
- Do NOT paraphrase/rewrite/shorten INPUT!U.
- highlight_words is required: choose 1-3 words/phrases in text_overlay to emphasize (money, police, lawyer, betrayal, etc.).
- In img_prompt include guidance for readable overlay area and typography style:
  bold sans-serif, white + yellow contrast, soft shadow, clean composition.
- No watermark, no logo, no extra clutter.

External sheet inputs (highest priority):
- Title (INPUT!T): {sheet_title or "(empty)"}
- Thumb text idea (INPUT!U): {sheet_text_thumb or "(empty)"}

How to use external sheet inputs:
- Use Title + Thumb text to build scene/background/composition.
- If INPUT!U has content, text_overlay MUST be exactly INPUT!U (verbatim).
- Do not rewrite INPUT!U; only optimize visual composition around it.

Story context:
Setting: {setting}
Context: {story_context}

Characters:
{chars_info}

Locations:
{locs_info}

Scene highlights:
{scene_brief}
"""

        if is_psychology:
            critic_system = f"""
You are a strict YouTube thumbnail QA critic.
You must enforce:
- 3 thumbnails, each with compelling emotional CTR framing.
- text_overlay must equal INPUT!U verbatim when INPUT!U is provided.
- highlight_words must be words present in text_overlay.
- image prompt must use this channel style: {self.psychology_style_profile.get('thumbnail_style') or self.psychology_style_profile.get('image_style')}
- image prompt must obey these negative rules: {self.psychology_style_profile.get('negative_prompt')}
- Must use the uploaded character reference image as the identity/style source and preserve the same channel style.
- text_overlay must preserve the core hook from INPUT!U (key claim, key number/entity).
Return ONLY valid JSON.
"""
        else:
            critic_system = """
You are a strict YouTube thumbnail QA critic.
You must enforce:
- 3 thumbnails, each with compelling emotional CTR framing.
- text_overlay must equal INPUT!U verbatim when INPUT!U is provided.
- highlight_words must be words present in text_overlay.
- image prompt must be cinematic photoreal style.
- text_overlay must preserve the core hook from INPUT!U (key claim, key number/entity).
Return ONLY valid JSON.
"""

        candidate_data: Optional[Dict[str, Any]] = None
        candidate_thumbs: Optional[List[Dict[str, Any]]] = None
        issues: List[str] = []

        gen_resp = self._call_api(generator_prompt, temperature=0.72, max_tokens=2600)
        if gen_resp:
            parsed = self._extract_json(gen_resp)
            thumbs = parsed.get("thumbnails") if isinstance(parsed, dict) else None
            if isinstance(thumbs, list) and thumbs:
                candidate_data = parsed
                candidate_thumbs = thumbs
            else:
                issues.append("generator returned invalid JSON thumbnails")
        else:
            issues.append("generator API failed")

        if candidate_thumbs:
            critic_prompt = f"""
Review this candidate JSON and fix it if needed.
Return ONLY valid JSON with this schema:
{{
  "approved": true/false,
  "issues": ["..."],
  "thumbnails": [ ... exactly 3 objects with required fields ... ]
}}

Candidate:
{json.dumps(candidate_data, ensure_ascii=False)}
"""
            critic_resp = self._call_api(
                critic_prompt,
                temperature=0.25,
                max_tokens=2600,
                system_prompt=critic_system,
            )
            critic_data = self._extract_json(critic_resp or "")
            critic_thumbs = critic_data.get("thumbnails") if isinstance(critic_data, dict) else None
            if isinstance(critic_thumbs, list) and critic_thumbs:
                candidate_thumbs = critic_thumbs
                for msg in (critic_data.get("issues") or []):
                    if msg:
                        issues.append(str(msg))

        if not candidate_thumbs or len(candidate_thumbs) < 3:
            style_constraint = (self.psychology_style_profile.get("thumbnail_style") or self.psychology_style_profile.get("image_style")) if is_psychology else "cinematic photoreal details"
            rewrite_prompt = f"""
Regenerate strictly valid JSON for 3 YouTube thumbnails.
Must include all fields: thumb_id, version_desc, img_prompt, text_overlay, highlight_words, characters_used, location_used, reference_files.
Use these constraints:
- text_overlay must equal INPUT!U verbatim when INPUT!U is provided.
- highlight_words must appear in text_overlay.
- img_prompt must include {style_constraint}.
- If Thumb text exists, preserve its core hook and key facts in text_overlay.

Context:
- Code: {code}
- Title: {sheet_title or "(empty)"}
- Thumb text: {sheet_text_thumb or "(empty)"}
- Main character: {main_char.id} ({main_char.name})
- {'Psychology explainer' if is_psychology else 'Story'} genre: {genre}
- Prior issues: {'; '.join(issues) if issues else 'none'}
"""
            rewrite_resp = self._call_api(rewrite_prompt, temperature=0.6, max_tokens=2600)
            rewrite_data = self._extract_json(rewrite_resp or "")
            rewrite_thumbs = rewrite_data.get("thumbnails") if isinstance(rewrite_data, dict) else None
            if isinstance(rewrite_thumbs, list) and rewrite_thumbs:
                candidate_thumbs = rewrite_thumbs

        if not candidate_thumbs:
            return StepResult("thumbnail_prompts", StepStatus.FAILED, f"Invalid thumbnail JSON ({'; '.join(issues[:3])})")

        workbook.clear_thumbnails()
        saved = 0
        fixed_overlay_text = str(sheet_text_thumb or "").strip()
        default_layouts = [
            "subject_left_text_right_upper",
            "subject_right_text_left_upper",
            "subject_center_text_bottom",
        ]
        seed_tokens = _extract_focus_tokens(sheet_text_thumb or sheet_title)
        for idx, item in enumerate(candidate_thumbs[:3]):
            if not isinstance(item, dict):
                continue
            try:
                thumb_id = int(item.get("thumb_id", saved + 1))
            except Exception:
                thumb_id = saved + 1

            if fixed_overlay_text:
                overlay_text = fixed_overlay_text
                highlights = _pick_highlights(
                    overlay_text,
                    item.get("highlight_words", []),
                    seed_tokens=seed_tokens,
                    variant_idx=idx,
                )
                layout_hint = default_layouts[idx % len(default_layouts)]
            else:
                overlay_text = _normalize_overlay(item.get("text_overlay", ""), sheet_title, sheet_text_thumb, variant_idx=idx)
                highlights = _pick_highlights(
                    overlay_text,
                    item.get("highlight_words", []),
                    seed_tokens=seed_tokens,
                    variant_idx=idx,
                )
                layout_hint = default_layouts[idx % len(default_layouts)]

            raw_main_character = char_lines[0] if char_lines else f"{main_char.id} ({main_char.name})"
            variant_desc = str(item.get("version_desc", "") or f"v{idx+1}")
            enhanced_base = _enhance_visual_prompt(
                base_prompt=str(item.get("img_prompt", "") or ""),
                variant_desc=variant_desc,
                overlay_text=overlay_text,
                layout_hint=layout_hint,
                title=sheet_title,
                text_thumb=sheet_text_thumb,
                main_character_line=raw_main_character,
                locations_info=locs_info,
                scene_highlights=scene_brief,
                is_psychology=is_psychology,
            )

            img_prompt = _build_img_prompt(enhanced_base, overlay_text, highlights, layout_hint)

            repaired_item = {
                "img_prompt": img_prompt,
                "text_overlay": overlay_text,
                "highlight_words": highlights,
            }
            if not _is_valid_candidate(repaired_item):
                continue

            chars_used = str(item.get("characters_used", "") or "").strip() or str(main_char.id)
            loc_used = str(item.get("location_used", "") or "").strip()

            refs = item.get("reference_files", [])
            if not isinstance(refs, list):
                refs = []

            if not refs:
                found = re.findall(r"\(([A-Za-z0-9_]+)\.png\)", img_prompt)
                for rid in found:
                    refs.append(f"{rid}.png")

            thumb = Thumbnail(
                thumb_id=thumb_id,
                version_desc=str(item.get("version_desc", Thumbnail.VERSION_DESCS.get(thumb_id, f"v{thumb_id}"))),
                img_prompt=img_prompt,
                characters_used=chars_used,
                location_used=loc_used,
                reference_files=json.dumps(refs, ensure_ascii=False) if refs else "",
                status_img="pending",
            )
            workbook.add_thumbnail(thumb)
            saved += 1

        workbook.save()
        if saved <= 0:
            return StepResult("thumbnail_prompts", StepStatus.FAILED, "No thumbnail prompts saved")
        if issues:
            self._log(f"  [THUMB-OPT] QA issues fixed: {'; '.join(issues[:5])}")
        return StepResult("thumbnail_prompts", StepStatus.COMPLETED, f"{saved} thumbnails generated")
