"""
SRT ➜ Excel Tool — Prompt Quality Engine
==========================================
Tất cả logic tạo prompt chất lượng cao cho image / video generation.

Nguyên tắc:
  1. img_prompt  — structured blocks: STYLE | SUBJECT | ACTION | SETTING | LIGHTING | MOOD | TECHNICAL
  2. video_prompt — camera movement tối giản, tập trung vào hành động nhìn thấy được
  3. Mỗi scene phải UNIQUE theo nội dung narration thực sự
  4. Tương thích Flux / Imagen / Midjourney / SDXL format
"""

from typing import Optional, Dict, Any, List, Set, Tuple
import re
import os
import json


_PROMPT_QUALITY_DEEPSEEK_API_KEY = ""
_PROMPT_QUALITY_DEEPSEEK_MODEL = "deepseek-chat"


def configure_prompt_quality_ai(api_key: str = "", model: str = "") -> None:
    global _PROMPT_QUALITY_DEEPSEEK_API_KEY, _PROMPT_QUALITY_DEEPSEEK_MODEL
    if str(api_key or "").strip():
        _PROMPT_QUALITY_DEEPSEEK_API_KEY = str(api_key).strip()
    if str(model or "").strip():
        _PROMPT_QUALITY_DEEPSEEK_MODEL = str(model).strip()


# ─────────────────────────────────────────────────────────────────────────────
# CINEMATIC STYLE PRESETS
# ─────────────────────────────────────────────────────────────────────────────

STYLE_PRESETS: Dict[str, str] = {
    "cinematic":     "35mm anamorphic lens, shallow depth of field, cinematic color grade, film grain",
    "documentary":   "handheld 24mm lens, natural light, vérité style, authentic texture",
    "commercial":    "85mm lens, soft diffused lighting, clean polished look, vibrant colors",
    "noir":          "low-key lighting, deep shadows, high contrast, 50mm lens, desaturated",
    "golden_hour":   "warm golden sunlight, long shadows, lens flare, 85mm, dreamlike glow",
    "dramatic":      "dramatic side lighting, 35mm, strong contrast, intense color grade",
    "intimate":      "close-up 85mm–135mm, extremely shallow DOF, soft bokeh, warm tones",
    "epic":          "wide 24mm, expansive composition, dynamic lighting, IMAX quality",
    "melancholic":   "overcast light, muted palette, 50mm, slow motion feel, heavy atmosphere",
    "default":       "cinematic still frame, 50mm lens f/2.0, photorealistic, 8K ultra-detailed",
}

LIGHTING_VOCAB: Dict[str, str] = {
    "day_interior":    "soft diffused daylight through windows, gentle shadows, natural warmth",
    "day_exterior":    "bright natural sunlight, crisp shadows, clear sky, vibrant",
    "night_interior":  "warm interior lamps, deep shadows, cozy but slightly tense atmosphere",
    "night_exterior":  "cool moonlight + artificial street lights, deep blue shadows",
    "golden_hour":     "warm golden sunlight at low angle, long shadows, glowing rim light",
    "overcast":        "soft flat light, no harsh shadows, melancholic mood, even exposure",
    "dramatic":        "strong single-source light, deep shadows, chiaroscuro, moody",
    "practical":       "lit by practical sources (lamps, screens, candles), intimate feel",
    "default":         "natural cinematic lighting, motivated by scene context",
}

SAFE_VIDEO_CAMERAS: List[str] = [
    "detail close-up",
    "close-up",
    "medium close-up",
    "medium shot",
    "medium wide",
    "wide shot",
]

SAFE_VIDEO_MOTIONS: List[str] = [
    "static frame",
    "very slow push-in",
    "very slow pull-back",
    "gentle pan left",
    "gentle pan right",
]

CAMERA_MOVEMENT: Dict[str, str] = {
    "static":          "static locked-off camera, no movement",
    "slow_push":       "imperceptible slow push-in (1.05x over scene duration), intimate reveal",
    "slow_pull":       "gentle slow pull-back, expanding the emotional space",
    "pan_right":       "slow deliberate pan right, following subject or revealing environment",
    "pan_left":        "slow deliberate pan left",
    "tilt_up":         "slow tilt up, from detail to wider context",
    "tilt_down":       "slow tilt down, descending into detail",
    "handheld":        "subtle handheld micro-tremor, 2-3px drift, authentic feel",
    "dolly_in":        "smooth dolly push toward subject, building tension",
    "dolly_out":       "smooth dolly pull from subject, creating distance or revelation",
    "crane_up":        "slow crane/jib up, rising above subject to reveal scale",
    "orbit":           "slow orbital move around subject (15° arc over duration)",
    "default":         "subtle camera drift, imperceptible slow push-in",
}

TECHNICAL_SUFFIX = (
    "photorealistic, ultra-detailed, 8K resolution, "
    "professional cinema camera (ARRI Alexa or Sony Venice), "
    "color graded, no watermark, no text overlay"
)

PSYCHOLOGY_STRICT_NEGATIVE_PROMPT = (
    "no real humans, no photorealism, no 3D, no readable text, no text, no letters, "
    "no numbers, no captions, no subtitles, no signs, no logos, no watermark"
)

PSYCHOLOGY_STYLE_HINT = (
    "Minimalist hand-drawn 2D psychological explainer illustration matching the provided nv1 reference image, "
    "same exact visual language as the reference, off-white paper background, warm gray monochrome palette, "
    "clean negative space, all figures, props, rooms, and symbolic elements in the same simple sketch style as nv1, "
    "editorial composition, preserve the reference character exactly, do not redesign the main character, "
    f"{PSYCHOLOGY_STRICT_NEGATIVE_PROMPT}"
)

PSYCHOLOGY_NV1_REFERENCE_LOCK = (
    "Use the provided reference image as the recurring character identity and style source. "
    "Preserve the reference character exactly; do not create a new character design; "
    "do not change the reference silhouette, proportions, eyes, or simple facial design."
)

PSYCHOLOGY_TECHNICAL_SUFFIX = (
    "same exact sketch style for all figures, objects, environments, and symbolic elements, "
    "clean high-quality hand-drawn illustration, consistent nv1 reference when used, "
    "no readable text, no letters, no numbers, no captions, no signs, no logos, no watermark"
)

PSYCHOLOGY_ENGAGEMENT_RULES = (
    "centered main character as the clear focal point, detailed supporting props only when grounded in the line, "
    "same-style secondary silhouettes, body language, direct visual cause-and-effect, instantly obvious emotional meaning, "
    "avoid generic symbolism when a concrete real-life situation can express the line more precisely"
)

DEFAULT_PSYCHOLOGY_STYLE_PROFILE: Dict[str, str] = {
    "style_name": "default_minimalist",
    "image_style": PSYCHOLOGY_STYLE_HINT,
    "video_style": (
        "Same minimalist hand-drawn psychological explainer style on off-white paper, warm gray tones, "
        "main character from provided reference image as the emotional anchor, all secondary figures, "
        "environments, and props in the same sketch style"
    ),
    "thumbnail_style": (
        "Minimalist psychological YouTube thumbnail, clean 2D illustration, soft desaturated palette, "
        "light beige or off-white paper background, high contrast but refined"
    ),
    "scene_plan_style": (
        "clean educational illustration language, warm cream paper, black outlines, soft pastel accents"
    ),
    "palette": "warm gray monochrome, off-white paper, clean negative space",
    "negative_prompt": PSYCHOLOGY_STRICT_NEGATIVE_PROMPT,
    "reference_lock": PSYCHOLOGY_NV1_REFERENCE_LOCK,
    "technical_suffix": PSYCHOLOGY_TECHNICAL_SUFFIX,
    "engagement_rules": PSYCHOLOGY_ENGAGEMENT_RULES,
    "audience_language": "",
    "audience_culture_note": "",
    "cultural_props": "",
    "cultural_metaphors": "",
    "cultural_emotion_style": "",
}


def _strengthen_psychology_negative_prompt(negative_prompt: str) -> str:
    parts: List[str] = []
    seen: Set[str] = set()
    for part in re.split(r"[,;]", str(negative_prompt or "")):
        part = " ".join(part.strip().split())
        if part and part.lower() not in seen:
            seen.add(part.lower())
            parts.append(part)
    for part in PSYCHOLOGY_STRICT_NEGATIVE_PROMPT.split(", "):
        if part.lower() not in seen:
            seen.add(part.lower())
            parts.append(part)
    return ", ".join(parts)


def _strengthen_psychology_reference_lock(reference_lock: str) -> str:
    lock = " ".join(str(reference_lock or "").split()).strip()
    required = PSYCHOLOGY_NV1_REFERENCE_LOCK
    if not lock:
        return required
    low = lock.lower()
    additions = []
    if "provided reference" not in low and "nv1" not in low:
        additions.append("Use the provided reference image as the recurring character identity and style source")
    if "do not" not in low or "redesign" not in low:
        additions.append("do not redesign the main character")
    if "silhouette" not in low:
        additions.append("preserve the reference silhouette and proportions")
    if "eyes" not in low:
        additions.append("preserve the simple eye and facial design")
    if additions:
        lock = lock.rstrip(". ") + "; " + "; ".join(additions) + "."
    return lock


def normalize_style_profile(style_profile: Optional[Dict[str, Any]] = None) -> Dict[str, str]:
    """Return a complete style profile with safe defaults."""
    profile = dict(DEFAULT_PSYCHOLOGY_STYLE_PROFILE)
    provided_keys: Set[str] = set()
    if isinstance(style_profile, dict):
        for key, value in style_profile.items():
            if value is None:
                continue
            if isinstance(value, (list, tuple)):
                value = ", ".join(str(v).strip() for v in value if str(v).strip())
            key = str(key)
            provided_keys.add(key)
            profile[key] = str(value).strip()
    has_custom_style = bool(style_profile) and any(
        key in provided_keys for key in {"style_name", "image_style", "video_style", "thumbnail_style", "palette"}
    )
    if has_custom_style:
        image_style = str(profile.get("image_style", "")).strip() or DEFAULT_PSYCHOLOGY_STYLE_PROFILE["image_style"]
        if "video_style" not in provided_keys or not str(profile.get("video_style", "")).strip():
            profile["video_style"] = f"Same channel illustration style as the image prompt: {image_style}"
        if "thumbnail_style" not in provided_keys or not str(profile.get("thumbnail_style", "")).strip():
            profile["thumbnail_style"] = f"High-CTR YouTube thumbnail in this channel style: {image_style}"
        if "scene_plan_style" not in provided_keys or not str(profile.get("scene_plan_style", "")).strip():
            profile["scene_plan_style"] = image_style
        if "technical_suffix" not in provided_keys or not str(profile.get("technical_suffix", "")).strip():
            profile["technical_suffix"] = PSYCHOLOGY_TECHNICAL_SUFFIX
    profile["negative_prompt"] = _strengthen_psychology_negative_prompt(profile.get("negative_prompt", ""))
    profile["reference_lock"] = _strengthen_psychology_reference_lock(profile.get("reference_lock", ""))
    for key, fallback in DEFAULT_PSYCHOLOGY_STYLE_PROFILE.items():
        if not str(profile.get(key, "")).strip():
            profile[key] = fallback
    return profile


def _strip_attached_character_description(style_text: str) -> str:
    """Keep art direction, remove verbose nv1 anatomy/outfit details because nv1.png is attached."""
    text = str(style_text or "").strip()
    if not text:
        return text
    if " matching nv1.png" in text.lower():
        text = re.split(r"\s+matching\s+nv1\.png", text, maxsplit=1, flags=re.IGNORECASE)[0].strip()
    character_terms = [
        "body", "head", "eyes", "mouth", "face", "cheek", "limbs", "shirt", "sweater",
        "hoodie", "pants", "jogger", "sneakers", "shoes", "turtleneck", "drawstrings",
        "emblem", "logo", "pocket", "eyebrows", "skin", "chubby", "round white",
        "dot eyes", "tiny", "normal-height", "short round", "one hand",
    ]
    kept = []
    for part in [p.strip() for p in text.split(",") if p.strip()]:
        low = part.lower()
        if any(term in low for term in character_terms):
            continue
        kept.append(part)
    cleaned = ", ".join(kept)
    if len(cleaned) < 80:
        cleaned = text.split(" matching nv1.png", 1)[0].strip()
    return cleaned.strip(" ,.")


def _attached_character_detail_fragments(style_profile: Optional[Dict[str, Any]] = None) -> List[str]:
    fragments: List[str] = []
    character_terms = [
        "body", "head", "eyes", "mouth", "face", "cheek", "limbs", "shirt", "sweater",
        "hoodie", "pants", "jogger", "sneakers", "shoes", "turtleneck", "drawstrings",
        "emblem", "logo", "pocket", "eyebrows", "skin", "chubby", "round white",
        "dot eyes", "tiny", "normal-height", "short round", "one hand",
    ]
    if isinstance(style_profile, dict):
        sources = [
            style_profile.get("image_style", ""),
            style_profile.get("video_style", ""),
            style_profile.get("thumbnail_style", ""),
            style_profile.get("scene_plan_style", ""),
            style_profile.get("default_character_prompt", ""),
            style_profile.get("default_character_lock", ""),
        ]
    else:
        sources = []
    for source in sources:
        for part in [p.strip() for p in str(source or "").split(",") if p.strip()]:
            low = part.lower()
            if any(term in low for term in character_terms) and part not in fragments:
                fragments.append(part)
    return sorted(fragments, key=len, reverse=True)


def _strip_attached_character_design_from_prompt(prompt: str, style_profile: Optional[Dict[str, Any]] = None) -> str:
    cleaned = str(prompt or "")
    for fragment in _attached_character_detail_fragments(style_profile):
        cleaned = re.sub(r"\s*,?\s*" + re.escape(fragment) + r"\s*,?\s*", ", ", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r",\s*,+", ",", cleaned)
    cleaned = re.sub(r"\s{2,}", " ", cleaned)
    cleaned = re.sub(r"(?:^|[|.])\s*,+", lambda m: m.group(0).replace(",", ""), cleaned)
    return cleaned.strip(" ,.;|")


def _normalize_psychology_reference_language(prompt: str, has_reference_character: bool = True) -> str:
    """Keep filename/internal IDs out of model-facing prompt text when media_id refs are attached."""
    cleaned = str(prompt or "")
    if not has_reference_character:
        return cleaned

    replacements = [
        (r"matching the attached nv1\.png channel art style", "matching the provided reference character's channel art style"),
        (r"matching nv1\.png", "matching the provided reference character"),
        (r"attached nv1\.png reference image", "provided reference image"),
        (r"Use attached nv1\.png as the recurring character reference; do not describe or redesign nv1\.", "Use the provided reference image as the recurring character identity source; preserve the reference character exactly and do not create a new character design."),
        (r"Use attached nv1\.png for character identity; do not describe or redesign the character\.", "Use the provided reference image as the character identity source; preserve the reference character exactly and do not create a new character design."),
        (r"Use attached nv1\.png for recurring character identity; do not redesign or over-describe nv1\.", "Use the provided reference image as the recurring character identity source; preserve the reference character exactly and do not over-describe it."),
        (r"use attached nv1\.png for recurring character identity; no redesign, no detailed character restatement", "use the provided reference image as the recurring character identity source; preserve the reference character exactly; no redesign, no detailed character restatement"),
        (r"Use attached nv1\.png briefly as the character reference", "Use the provided reference image as the character identity source"),
        (r"Use the attached character reference for identity; do not describe or redesign the character\.", "Use the provided reference image as the character identity source; preserve the reference character exactly and do not create a new character design."),
        (r"Use the attached character reference without re-describing the character", "Use the provided reference image as the character identity source without re-describing the character"),
        (r"Use the attached character reference briefly", "Use the provided reference image as the character identity source"),
        (r"attached character reference", "provided reference image"),
        (r"attached reference image", "provided reference image"),
        (r"brief attached nv1\.png reference", "brief provided reference image instruction"),
        (r"nv1\.png as the attached identity/style anchor", "the provided reference image as the identity/style anchor"),
        (r"attached nv1\.png", "the provided reference image"),
        (r"nv1\.png", "the provided reference image"),
        (r"nv1 from the provided reference image", "the reference character from the provided reference image"),
        (r"nv1 from attached", "the reference character from the provided reference image"),
        (r"\bnv1\b", "the reference character"),
    ]
    for pattern, replacement in replacements:
        cleaned = re.sub(pattern, replacement, cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"do not describe or redesign\s*\.", "do not describe or redesign the reference character.", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\bthe reference character\s+the reference character\b", "the reference character", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\s{2,}", " ", cleaned)
    return cleaned.strip()


def _runtime_image_style(style_profile: Optional[Dict[str, Any]] = None) -> str:
    profile = normalize_style_profile(style_profile)
    art_style = _strip_attached_character_description(profile.get("image_style", ""))
    palette = str(profile.get("palette", "") or "").strip()
    negative = str(profile.get("negative_prompt", "") or "").strip()
    return (
        f"{art_style}, matching the provided reference character's channel art style. "
        f"Palette: {palette}. "
        "Use the provided reference image as the character identity source; preserve the reference character exactly and do not create a new character design. "
        f"{negative}"
    ).strip()


def _extract_style_essentials(style_profile: Optional[Dict[str, Any]] = None, topic: str = "") -> str:
    profile = normalize_style_profile(style_profile)
    full_style = _strip_attached_character_description(profile.get("image_style", ""))
    palette = str(profile.get("palette", "") or "").strip()
    lowered = full_style.lower()
    essentials: List[str] = []

    tc = _get_topic_config(topic)
    style_label = tc.get("prompt_label", "psychology illustration")
    for keyword, label in [
        ("illustration", style_label),
        ("hand-drawn", "hand-drawn"),
        ("flat", "flat design"),
        ("minimal", "minimalist"),
        ("paper", "paper texture"),
        ("editorial", "editorial explainer"),
    ]:
        if keyword in lowered and label not in essentials:
            essentials.append(label)

    if not essentials and full_style:
        essentials.append(full_style[:120].strip(" ,.;"))
    if palette:
        essentials.append(palette)
    essentials.append("provided reference character identity")
    return ", ".join(item for item in essentials if item)


def _runtime_video_style(style_profile: Optional[Dict[str, Any]] = None) -> str:
    profile = normalize_style_profile(style_profile)
    video_style = _strip_attached_character_description(profile.get("video_style", ""))
    negative = str(profile.get("negative_prompt", "") or "").strip()
    reference_lock = str(profile.get("reference_lock", "") or PSYCHOLOGY_NV1_REFERENCE_LOCK).strip()
    style_parts = [
        video_style,
        "same exact visual language as the provided reference image",
        "same exact sketch style for all figures, props, rooms, shadows, and symbolic elements",
        "off-white paper background",
        "warm gray tones",
        "the reference character is the emotional anchor",
    ]
    style_sentence = ", ".join(part.strip(" ,.") for part in style_parts if str(part or "").strip())
    return (
        f"{style_sentence}. "
        f"{reference_lock} "
        f"{negative}"
    ).strip()


def _profile_style_terms(style_profile: Optional[Dict[str, Any]] = None) -> List[str]:
    profile = normalize_style_profile(style_profile)
    text = " ".join([
        profile.get("style_name", ""),
        profile.get("image_style", ""),
        profile.get("palette", ""),
    ]).lower()
    terms = []
    for token in re.findall(r"[a-z0-9][a-z0-9-]{3,}", text):
        if token in {
            "with", "from", "provided", "reference", "image", "style", "same",
            "main", "character", "background", "clean", "prompt", "psychology", "finance",
        }:
            continue
        if token not in terms:
            terms.append(token)
    return terms[:10]

PSYCHOLOGY_FORBIDDEN_STYLE_TERMS = [
    "photorealistic",
    "ultra-detailed",
    "8K",
    "ARRI Alexa",
    "Sony Venice",
    "cinematic color grade",
    "color graded",
    "anamorphic lens",
    "anamorphic",
    "film grain",
    "shallow depth of field",
    "shallow DOF",
    "bokeh",
    "chiaroscuro",
    "IMAX",
]


# ─────────────────────────────────────────────────────────────────────────────
# TOPIC PROMPT CONFIG — per-topic visual direction for all prompt functions
# ─────────────────────────────────────────────────────────────────────────────

TOPIC_PROMPT_CONFIG: Dict[str, Dict[str, str]] = {
    "psychology": {
        "director_role": "PSYCHOLOGY ILLUSTRATOR",
        "topic_label": "PSYCHOLOGY",
        "topic_desc": "psychology and self-improvement",
        "visual_approach": "emotional introspection, mental health metaphors, therapeutic visuals",
        "metaphor_examples": "mirrors, knots, bridges, mazes, clouds lifting, scales, emotional body language",
        "metaphor_hint": (
            "Use psychological visual metaphors: mirrors, knots, bridges, mazes, clouds lifting, "
            "scales, emotional body language, contrast panels. Show mental and emotional concepts "
            "through relatable introspective situations."
        ),
        "prop_examples": "journal, mirror, tangled rope, bridge, empty chair, growing plant",
        "setting_examples": "quiet rooms, park benches, rainy windows, therapy-like spaces",
        "mood_spectrum": "reflective, anxious, vulnerable, hopeful, healing",
        "forbidden_style": "cinematic, photorealistic, 3D, film grain",
        "video_motion_fallback": (
            "nv1 makes one small deliberate hand gesture toward the main symbolic object "
            "as the surrounding shapes shift outward in response, making the spoken idea readable through motion"
        ),
        "quality_question": (
            "what single image would make the spoken psychology idea clear, "
            "emotionally relatable, and worth watching?"
        ),
        "prompt_label": "psychology illustration",
        "theme_context_default": (
            "nervous-system regulation, emotional self-awareness, "
            "boundaries versus isolation, and protecting inner peace"
        ),
        "sample_tail_theme_default": (
            "nervous-system regulation, emotional self-awareness, "
            "boundaries versus isolation, and protecting inner peace"
        ),
        "fallback_subject": "a concrete symbolic psychology moment from the narration",
    },
    "finance": {
        "director_role": "FINANCE ILLUSTRATOR",
        "topic_label": "FINANCE",
        "topic_desc": "personal finance and financial literacy",
        "visual_approach": "concrete money situations, financial decision points, wealth building journeys",
        "metaphor_examples": (
            "growing coin plants, stacking savings blocks, leaking buckets, "
            "investment ladders, breaking debt chains"
        ),
        "metaphor_hint": (
            "Use financial visual metaphors: growth charts, coin stacks, savings jars, "
            "investment trees, debt chains, safety nets, open doors of opportunity. "
            "Show money concepts through relatable everyday financial situations."
        ),
        "prop_examples": "coins, piggy banks, wallets, simple charts, houses, phones with banking apps",
        "setting_examples": "home offices, kitchen tables with bills, bank interiors, shopping areas",
        "mood_spectrum": "practical, concerned, planning, relieved, empowered",
        "forbidden_style": "cinematic, photorealistic, 3D, film grain",
        "video_motion_fallback": (
            "nv1 places one coin on a growing stack as surrounding financial elements shift "
            "to show the spoken idea readable through motion"
        ),
        "quality_question": (
            "what single image would make the spoken financial concept clear, "
            "actionable, and worth watching?"
        ),
        "prompt_label": "finance illustration",
        "theme_context_default": (
            "personal finance, saving habits, investment basics, "
            "debt management, and building financial security"
        ),
        "sample_tail_theme_default": (
            "personal finance, saving habits, investment basics, "
            "debt management, and building financial security"
        ),
        "fallback_subject": "a concrete symbolic finance moment from the narration",
    },
    "success": {
        "director_role": "SELF-DEVELOPMENT ILLUSTRATOR",
        "topic_label": "SELF-DEVELOPMENT",
        "topic_desc": "self-development, personal growth and success habits",
        "visual_approach": "daily habits, goal setting, discipline building, personal transformation",
        "metaphor_examples": (
            "climbing stairs, planting seeds, building blocks, opening doors, "
            "sunrise, path splitting"
        ),
        "metaphor_hint": (
            "Use growth and motivation visual metaphors: climbing steps, planting seeds, "
            "building blocks, opening doors, morning routines, habit trackers, before/after "
            "contrasts. Show personal growth through relatable daily discipline situations."
        ),
        "prop_examples": "alarm clocks, notebooks, running shoes, growing plants, water bottles, dumbbells",
        "setting_examples": "bedrooms at dawn, study desks, parks, gyms, kitchen tables",
        "mood_spectrum": "determined, struggling, motivated, disciplined, triumphant",
        "forbidden_style": "cinematic, photorealistic, 3D, film grain",
        "video_motion_fallback": (
            "nv1 takes one deliberate step forward as the surrounding growth elements rise "
            "to show the spoken idea readable through motion"
        ),
        "quality_question": (
            "what single image would make the spoken self-development idea clear, "
            "motivating, and worth watching?"
        ),
        "prompt_label": "self-development illustration",
        "theme_context_default": (
            "personal growth, daily discipline, goal setting, "
            "habit building, and self-improvement journey"
        ),
        "sample_tail_theme_default": (
            "personal growth, daily discipline, goal setting, "
            "habit building, and self-improvement journey"
        ),
        "fallback_subject": "a concrete symbolic self-development moment from the narration",
    },
}

_TOPIC_KEY_MAP: Dict[str, str] = {
    "psychology": "psychology",
    "tam ly": "psychology",
    "finance": "finance",
    "tai chinh": "finance",
    "success": "success",
    "phat trien ban than": "success",
}


def _resolve_topic_key(topic: str = "") -> str:
    """Normalize topic string to a canonical key in TOPIC_PROMPT_CONFIG. Returns '' for non-styled topics."""
    import unicodedata
    value = str(topic or "").strip().lower()
    value = unicodedata.normalize("NFKD", value)
    value = "".join(ch for ch in value if not unicodedata.combining(ch))
    value = value.replace("_", " ").replace("-", " ")
    value = " ".join(value.split())
    return _TOPIC_KEY_MAP.get(value, "")


def _get_topic_config(topic: str = "") -> Dict[str, str]:
    """Return the topic config dict, defaulting to psychology for unknown styled topics."""
    key = _resolve_topic_key(topic)
    return TOPIC_PROMPT_CONFIG.get(key, TOPIC_PROMPT_CONFIG["psychology"])


def _is_psychology_topic(topic: str = "") -> bool:
    return bool(_resolve_topic_key(topic))


def _strip_psychology_forbidden_style(prompt: str) -> str:
    cleaned = str(prompt or "")
    for term in PSYCHOLOGY_FORBIDDEN_STYLE_TERMS:
        cleaned = re.sub(re.escape(term), "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\b(?:cinematic|film|movie)\s+(?:still|shot|frame|look|style|lighting|camera|sequence)\b", "clean illustration", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\b(?:dslr|camera model|lens|f/\d+(?:\.\d+)?|depth of field|dof|rim light|volumetric lighting)\b", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\b(Megan|Wells Fargo|HomeGoods|leftover pasta|open marriages)\b", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\b(?:transaction row|bank screen|laptop screen|fork|pasta plate)\b", "simple symbolic object", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\s*,\s*,+", ",", cleaned)
    cleaned = re.sub(r"\s{2,}", " ", cleaned)
    cleaned = re.sub(r"\|\s*\[TECHNICAL\]\s*[,. ]*(?=\||$)", "", cleaned, flags=re.IGNORECASE)
    return cleaned.strip(" ,.;|")


FORCED_PSYCHOLOGY_CULTURAL_PROP_RE = re.compile(
    r",?\s*grounded through\s+"
    r"(coffee mug on desk|journal with pen|cozy couch pillow|window frame with rain|"
    r"small potted succulent|headphones on table|therapy-style armchair|scented candle|"
    r"tote bag on door hook|water bottle beside yoga mat)",
    flags=re.IGNORECASE,
)
FORCED_PSYCHOLOGY_GROUNDED_THROUGH_RE = re.compile(
    r",?\s*grounded through\s+[^|.;]+",
    flags=re.IGNORECASE,
)
FORCED_PSYCHOLOGY_CANNED_PROP_RE = re.compile(
    r"\b(coffee mug on desk|journal with pen|cozy couch pillow|window frame with rain|"
    r"small potted succulent|headphones on table|therapy-style armchair|scented candle|"
    r"tote bag on door hook|water bottle beside yoga mat)\b",
    flags=re.IGNORECASE,
)


def _strip_forced_psychology_cultural_props(text: str, allowed_context: str = "") -> str:
    cleaned = FORCED_PSYCHOLOGY_CULTURAL_PROP_RE.sub("", str(text or ""))
    cleaned = FORCED_PSYCHOLOGY_GROUNDED_THROUGH_RE.sub("", cleaned)
    allowed_low = str(allowed_context or "").lower()

    def _remove_canned_prop(match: re.Match) -> str:
        prop = match.group(0)
        return prop if prop.lower() in allowed_low else ""

    cleaned = FORCED_PSYCHOLOGY_CANNED_PROP_RE.sub(_remove_canned_prop, cleaned)
    cleaned = re.sub(r"\s*,\s*,+", ",", cleaned)
    cleaned = re.sub(r"\s{2,}", " ", cleaned)
    return cleaned.strip(" ,.;|")


def _normalize_psychology_anchor_fragment(text: str) -> str:
    cleaned = _strip_forced_psychology_cultural_props(text)
    cleaned = re.sub(r"\bnv1\b", "the reference character", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\s{2,}", " ", cleaned)
    return cleaned.strip(" ,.;|")


def _build_psychology_quality_fallback(
    srt_text: str = "",
    primary_subject: str = "",
    primary_action: str = "",
    visual_anchor: str = "",
    char_ids: Optional[List[str]] = None,
    style_profile: Optional[Dict[str, Any]] = None,
) -> str:
    profile = normalize_style_profile(style_profile)
    subject = _normalize_psychology_anchor_fragment(primary_subject) or "the central idea from the narration"
    action = _normalize_psychology_anchor_fragment(primary_action) or _normalize_psychology_anchor_fragment(visual_anchor) or "one clear, readable emotional beat"
    anchor = _normalize_psychology_anchor_fragment(visual_anchor) or action or subject
    audience_hint = _build_psychology_audience_visual_hint(profile)
    narration_clause = ""
    if srt_text:
        narration_clause = "Translate the narration into one concrete English visual, not literal on-image text. "
    return (
        f"{_runtime_image_style(profile).rstrip('. ')}. "
        f"Show {subject}. "
        f"Action/body language: {action}. "
        f"Visual anchor: {anchor}. "
        f"Clear focal hierarchy: viewer first notices {anchor}. "
        "Make the emotional cause-and-effect readable through posture, spacing, and one narration-grounded object or visual metaphor. "
        f"{narration_clause}"
        f"{audience_hint} "
        "No labels, captions, UI, readable marks, camera terms, or cinematic photo language."
    )


def _collapse_style_blocks_only(prompt: str, style_profile: Optional[Dict[str, Any]] = None) -> str:
    cleaned = str(prompt or "").strip()
    if not cleaned:
        return cleaned

    if "|" in cleaned:
        parts = [part.strip() for part in re.split(r"\s*\|\s*", cleaned) if part.strip()]
    else:
        parts = [part.strip() for part in re.split(r"(?<=[.!?])\s+", cleaned) if part.strip()]

    style_terms = [
        "[style]", "illustration style", "channel style", "palette:", "matching the provided reference",
        "provided reference image", "preserve the reference character", "same exact channel style",
        "paper texture", "clean outline", "flat design", "technical", "negative rules",
    ]
    content_parts: List[str] = []
    style_parts: List[str] = []
    seen_style: Set[str] = set()

    for part in parts:
        low = part.lower()
        is_style = any(term in low for term in style_terms)
        if is_style:
            key = re.sub(r"\s+", " ", re.sub(r"[^a-z0-9 ]+", " ", low)).strip()[:120]
            if key and key not in seen_style:
                seen_style.add(key)
                style_parts.append(part)
        else:
            content_parts.append(part)

    if not style_parts:
        return cleaned
    separator = " | " if "|" in cleaned else " "
    return separator.join(content_parts + style_parts[:2]).strip(" ,.;|")


def _enforce_visual_first_prompt_structure(prompt: str) -> str:
    """
    Enforce visual-first structure when API returns style-first prompts.

    Problem: API sometimes ignores "START with visual_focus" instruction
    Solution: Detect and reorder sentences to put visual content first
    """
    if not prompt:
        return prompt

    sentences = [s.strip() for s in prompt.split(".") if s.strip()]
    if len(sentences) < 2:
        return prompt.strip()

    # Style keywords that should NOT be at the start
    style_keywords = [
        "style", "illustration", "palette", "flat design", "reference character",
        "channel art", "paper texture", "dark charcoal", "cool gray",
        "photorealism", "watermark", "no text", "no letters", "no captions",
        "clean outline", "muted cool tones", "matching the provided"
    ]

    # Visual starters that indicate concrete content
    visual_starters = [
        "visual focus", "show", "a ", "an ", "the ", "one ", "single ",
        "close-up", "wide shot", "overhead", "inside", "at ", "in ",
        "scene elements", "body language", "action/body language", "emotional tone"
    ]

    visual_sentences = []
    style_sentences = []

    for sentence in sentences:
        lowered = sentence.lower()
        is_style = any(keyword in lowered for keyword in style_keywords)
        starts_visual = any(lowered.startswith(starter) for starter in visual_starters)

        # If sentence contains style keywords AND doesn't start with visual content
        if is_style and not starts_visual:
            style_sentences.append(sentence)
        else:
            visual_sentences.append(sentence)

    # If no visual content found, return original
    if not visual_sentences:
        return prompt.strip()

    # Reorder: visual first, then max 3 style sentences
    reordered = visual_sentences + style_sentences[:3]
    return ". ".join(reordered).strip() + "."


def _collapse_duplicate_psychology_style_blocks(prompt: str, style_profile: Optional[Dict[str, Any]] = None) -> str:
    cleaned = str(prompt or "")
    if "[STYLE]" not in cleaned.upper():
        return cleaned.strip()

    runtime_style = _runtime_image_style(style_profile).rstrip(". ").lower()
    runtime_prefix = runtime_style[:80]
    parts = [part.strip() for part in re.split(r"\s*\|\s*", cleaned) if part.strip()]
    collapsed: List[str] = []
    seen_style = False

    for part in parts:
        part_low = part.lower().strip()
        if part_low.startswith("[style]"):
            if seen_style:
                continue
            seen_style = True
            collapsed.append(part)
            continue
        if seen_style and runtime_prefix and part_low.startswith(runtime_prefix):
            continue
        collapsed.append(part)

    return " | ".join(collapsed).strip(" ,.;|")


def _sample_style_image_tail(srt_text: str, theme_context: str = "", topic: str = "") -> str:
    tc = _get_topic_config(topic)
    theme = (theme_context or tc.get("sample_tail_theme_default", "")).strip()
    topic_label = tc.get("prompt_label", "illustration")
    return (
        f"{topic_label.capitalize()} theme: {theme}. "
        "Build one clear, narration-grounded visual with a strong focal point, concrete body language, and readable emotional cause-and-effect. "
        "Use cultural props only when they directly fit the narration. Prefer a concrete real-life situation over vague symbolism. "
    )


def _sample_style_image_prompt(
    srt_text: str,
    theme_context: str = "",
    concrete_visual: str = "",
    subject: str = "",
    action: str = "",
    style_profile: Optional[Dict[str, Any]] = None,
    topic: str = "",
) -> str:
    profile = normalize_style_profile(style_profile)
    audience_hint = _build_psychology_audience_visual_hint(profile)
    parts = []
    visual_sentence = str(concrete_visual or "").strip()
    if visual_sentence:
        parts.append(f"Visual anchor: {visual_sentence}")
    if subject:
        parts.append(f"Subject: {str(subject).strip()}")
    if action:
        parts.append(f"Action/body language: {str(action).strip()}")
    visual_sentence = ". ".join(part for part in parts if part.strip())
    if visual_sentence:
        visual_sentence = (
            f"Concrete visual plan: {visual_sentence}. "
            "Make the prop positions, body posture, facial expression, and visible cause-and-effect readable in one glance. "
        )
    return (
        f"{_runtime_image_style(profile).rstrip('. ')}. "
        f"{_sample_style_image_tail(srt_text, theme_context, topic=topic)} "
        f"{visual_sentence}"
        f"{audience_hint} "
        "No labels, captions, UI, readable marks, camera terms, or cinematic photo language."
    )


def _normalize_vi_text_for_video(text: str) -> str:
    import unicodedata

    normalized = unicodedata.normalize("NFKD", str(text or "").lower())
    normalized = "".join(ch for ch in normalized if not unicodedata.combining(ch))
    return " ".join(normalized.replace("_", " ").replace("-", " ").split())


def _is_generic_psychology_video_motion(prompt: str) -> bool:
    low = str(prompt or "").lower()
    generic_markers = [
        "use specific movement by the character",
        "surrounding simple figures, objects, rooms, and environment",
        "surrounding social figures, phones, rooms, and environment",
        "prefer literal scene action tied directly to the narration rather than vague atmospheric motion",
    ]
    if any(marker in low for marker in generic_markers):
        return True
    if "specific movement:" not in low:
        return True
    if "one small visible body adjustment" in low and "shifts subtly in light, position, or rhythm" in low:
        return True
    concrete_terms = [
        "steps", "turns", "opens", "lowers", "places", "presses", "recedes", "expands",
        "brightens", "drifts", "pauses", "enters", "touches", "nods", "sinks", "curls",
        "extends", "removes", "holds", "lets", "glows", "fades", "moves", "settles",
        "breath", "shoulders", "hand", "door", "phone", "stone", "boundary", "silhouette",
    ]
    tail = low.split("specific movement:", 1)[-1]
    return not any(term in tail for term in concrete_terms)


def _is_concrete_psychology_video_action(text: str) -> bool:
    """Return True when a director-plan action is specific enough to drive motion."""
    low = _normalize_vi_text_for_video(text)
    if not low or len(low.split()) < 5:
        return False
    generic_fragments = [
        "visual metaphor for",
        "spoken psychology idea",
        "main symbolic object",
        "central psychology idea",
        "one concrete visible movement",
    ]
    if any(fragment in low for fragment in generic_fragments):
        return False
    concrete_terms = [
        "nv1", "hand", "hands", "chest", "shoulder", "shoulders", "gaze", "body",
        "phone", "screen", "door", "threshold", "pot", "pots", "cloud", "shadow",
        "circle", "line", "path", "loop", "plate", "stack", "key", "backpack",
        "refrigerator", "table", "cup", "balcony", "silhouette", "silhouettes",
        "opens", "turns", "lowers", "places", "presses", "traces", "hovers",
        "covers", "gestures", "nods", "sinks", "wraps", "reaches", "receives",
        "stiffens", "curls", "drifts", "extends", "settles",
    ]
    return any(term in low for term in concrete_terms)


def _format_director_action_as_motion(action_text: str) -> str:
    action = " ".join(str(action_text or "").split()).strip(" .")
    if not action:
        return ""
    if not re.search(r"\bnv1\b|\bmain character\b|\bcentral figure\b|\bfigure\b", action, flags=re.IGNORECASE):
        action = f"nv1 stays emotionally present as {action[0].lower() + action[1:] if action else action}"
    return (
        f"{action[:260]}; the nearest prop, light, silhouette, or symbolic shape reacts subtly "
        "so the cause-and-effect of the narration is readable"
    )


_ABSTRACT_VIDEO_MOTION_TERMS = (
    "represents", "represented by", "symbolizes", "symbolising", "symbolizing",
    "feels", "feeling", "processes", "understands", "realizes", "recognizes",
    "emotional data", "early training", "inner world", "inner tension", "psychology", "finance",
    "trauma", "sensitivity", "awareness", "perception", "subconscious",
    "mental", "emotional point", "emotional/narrative point",
)

_VISIBLE_VIDEO_MOTION_TERMS = (
    "hand", "hands", "finger", "fingers", "thumb", "chest", "shoulder", "shoulders",
    "head", "gaze", "eyes", "breath", "body", "posture", "arm", "arms", "knee", "feet",
    "leans", "turns", "lowers", "raises", "lifts", "presses", "hovers", "pauses",
    "tightens", "relaxes", "settles", "stiffens", "curls", "drifts", "slides", "moves",
    "light", "shadow", "glow", "reflection", "screen", "phone", "door", "table", "cup",
    "book", "window", "symbol", "shape", "prop", "silhouette", "object",
)


def _motion_is_too_abstract(motion: str) -> bool:
    text = " ".join(str(motion or "").split()).lower()
    if len(text) < 50:
        return True
    has_visible = any(term in text for term in _VISIBLE_VIDEO_MOTION_TERMS)
    return not has_visible or any(term in text for term in _ABSTRACT_VIDEO_MOTION_TERMS)


def _extract_visual_motion_anchor(img_prompt: str) -> str:
    text = " ".join(str(img_prompt or "").split())
    if not text:
        return "the main prop or simple visible shape"
    sentences = [s.strip(" .") for s in re.split(r"(?<=[.!?])\s+", text) if s.strip()]
    preferred_terms = (
        "thumb", "finger", "fingertip", "hand", "hands", "chopsticks", "phone", "screen",
        "cup", "bowl", "book", "journal", "door", "window", "table", "chair", "light",
        "shadow", "glow", "silhouette", "reflection", "mouth", "eyes", "shoulder", "chest",
    )
    cleaned_sentences = [_clean_video_visual_text(sentence, 170) for sentence in sentences[:7]]
    for sentence in cleaned_sentences:
        low = sentence.lower()
        if sentence and any(term in low for term in preferred_terms):
            return sentence
    return cleaned_sentences[0] if cleaned_sentences and cleaned_sentences[0] else "the main prop or simple visible shape"


def _build_visible_motion_fallback(img_prompt: str, srt_text: str = "") -> str:
    anchor = _extract_visual_motion_anchor(img_prompt)
    low = anchor.lower()
    if any(term in low for term in ["thumb", "finger", "hand", "chopsticks"]):
        return f"the reference character's fingers tighten, pause, then lower slightly around {anchor}; the nearby light or shadow responds with a small visible shift before the hand settles into a held pose"
    if any(term in low for term in ["phone", "screen"]):
        return f"the reference character's gaze drops toward {anchor}, one shoulder curls inward, then the screen glow dims and steadies as the character holds still"
    if any(term in low for term in ["door", "window"]):
        return f"the reference character shifts weight toward {anchor}, one hand moves closer without rushing, then the surrounding light opens slightly and the body settles into a guarded final pose"
    if any(term in low for term in ["cup", "bowl", "table", "chair"]):
        return f"the reference character makes a tiny hand or shoulder adjustment beside {anchor}; the object casts a softer shadow, then the body becomes still in a clear final pose"
    return f"the reference character makes one specific visible gesture around {anchor}; the nearest prop or light shifts once, then the character holds a restrained final pose"


def _enforce_visible_motion(motion: str, img_prompt: str = "", srt_text: str = "") -> str:
    motion = " ".join(str(motion or "").split()).strip(" .")
    if not motion or _motion_is_too_abstract(motion):
        return _build_visible_motion_fallback(img_prompt, srt_text)
    return motion


def _extract_motion_from_image_prompt(img_prompt: str, srt_text: str = "") -> str:
    """
    Extract motion description from image prompt using DeepSeek API.

    This is the ROOT CAUSE FIX: video prompts now look at image prompts
    to generate specific motion based on actual visual content.

    Returns: Specific motion description (60-80 words) or empty string if failed.
    """
    try:
        api_key = _PROMPT_QUALITY_DEEPSEEK_API_KEY or os.environ.get("DEEPSEEK_API_KEY", "")
        model = _PROMPT_QUALITY_DEEPSEEK_MODEL or "deepseek-chat"
        if not api_key:
            return ""

        # Call DeepSeek API
        import requests

        system_prompt = """You are a video motion director. Extract visible physical motion from an image prompt to guide video animation.

Your job: Identify what should visibly MOVE in the video to tell the story.

Return JSON only:
{
    "motion_suggestion": "ONE specific visible motion that makes the narration readable (60-90 words)"
}

Motion rules:
- Write ONLY visible physical motion.
- Required structure: body movement by the reference character + one prop/light/symbol reaction copied from the image prompt + final held pose or visual state.
- Mention concrete objects from the image prompt.
- Describe subtle, restrained motion, not dramatic acting.
- Convert abstract concepts into visible action.
- Do NOT describe thoughts, concepts, labels, or invisible feelings.
- Do NOT use: represents, symbolizes, emotional data, early training, feels, processes, understands, inner world, trauma, sensitivity.
- Do NOT say the character scans/reads/feels something unless you also describe the visible hand, gaze, shoulder, prop, light, or symbol movement."""

        user_prompt = f"""Image prompt:
{img_prompt[:800]}

Narration context: {srt_text[:200]}

Extract visible physical motion for video animation.
Use the image prompt objects directly. Return body movement + prop/light/symbol reaction + final pose."""

        response = requests.post(
            "https://api.deepseek.com/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json"
            },
            json={
                "model": model,
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt}
                ],
                "temperature": 0.3,
                "max_tokens": 400
            },
            timeout=30
        )

        if response.status_code != 200:
            return ""

        result = response.json()
        content = result["choices"][0]["message"]["content"]

        # Parse JSON
        content = content.strip()
        if content.startswith("```"):
            lines = content.split("\n")
            content = "\n".join(lines[1:-1])

        data = json.loads(content)
        motion = data.get("motion_suggestion", "").strip()

        # Validate motion quality
        if motion and len(motion) > 50 and len(motion) < 600:  # Increased from 300 to 600
            return motion

        return ""

    except Exception as e:
        # Silent fail - fallback to original logic
        return ""


def _derive_video_movement(
    srt_text: str,
    primary_action: str = "",
    visual_anchor: str = "",
    visual_moment: str = "",
    primary_subject: str = "",
    style_profile: Optional[Dict[str, Any]] = None,
    img_prompt: str = "",
    topic: str = "",
) -> str:
    """
    Derive video movement description.

    NEW: If img_prompt is provided, extract visual elements from it first.
    This creates motion descriptions based on actual visual content.
    """
    # NEW: Try to extract motion from image prompt first
    if img_prompt and len(img_prompt) > 100:
        motion_from_image = _extract_motion_from_image_prompt(img_prompt, srt_text)
        if motion_from_image:
            return motion_from_image
        return _enforce_visible_motion("", img_prompt=img_prompt, srt_text=srt_text)

    # Fallback to original logic
    combined = _normalize_vi_text_for_video(" ".join([
        srt_text,
        primary_action,
        visual_anchor,
        visual_moment,
        primary_subject,
    ]))

    for candidate in [primary_action, visual_moment, primary_subject, visual_anchor]:
        if _is_concrete_psychology_video_action(candidate):
            return _format_director_action_as_motion(candidate)

    rules = [
        (
            ["celular", "palomita", "mensaje", "whatsapp", "perfil", "conexion"],
            "nv1 hovers a tense hand above a blank phone on the kitchen table, then slowly pulls the hand to the chest as the phone glow shrinks and the room tightens around the body",
        ),
        (
            ["alarma", "cuerpo", "informacion", "preocupacion"],
            "a small alarm-shaped shadow pulses near nv1's chest, then nv1 lowers the phone and places one hand over the chest as the shadow softens into a calmer shape",
        ),
        (
            ["escuela", "mochila", "piso", "pregunte"],
            "a backpack slips from nv1's shoulder onto the floor while nv1 pauses in the doorway, waiting for a caring gesture that never arrives",
        ),
        (
            ["refrigerador", "recipientes", "frios", "resolver", "comida"],
            "nv1 opens a simple refrigerator, sees two cold containers, then slowly lowers the shoulders and reaches for one container alone",
        ),
        (
            ["exigencia", "responsividad", "cumplir", "callar"],
            "nv1 stands rigid beneath a heavy stack of plates and covers the mouth with one hand while the surrounding lines press inward, showing compliance without comfort",
        ),
        (
            ["patrones", "crianza", "1967", "tres"],
            "three simple clay pots slide into view in a neat row while nv1 points gently from one pot to the next, making three parenting patterns visible without labels",
        ),
        (
            ["donde", "estaba", "quien", "aviso", "cuidado", "alivio"],
            "one version of nv1 opens questioning hands toward a distant silhouette while a smaller inner nv1 wraps arms around the chest for relief",
        ),
        (
            ["cuidar", "revisar", "distingues", "checking"],
            "nv1 traces the same small loop around a blank phone again and again, then pauses as the loop line becomes visible around the hand",
        ),
        (
            ["puerta", "abierta", "casa", "vacia"],
            "nv1 stands in an open doorway where an empty house shape warms with light, shoulders easing as the threshold becomes welcoming instead of hollow",
        ),
        (
            ["noi co", "say yes", "luon noi", "co the", "met", "tam tri", "body", "mind", "tired"],
            "nv1 gives a small repeated yes-nod toward several approaching request shapes, then the shoulders slowly sink and one hand touches the chest as the body shows fatigue before the mind catches up",
        ),
        (
            ["tin nhan", "message", "phone", "dien thoai", "hon da", "stone", "nguc", "chest"],
            "a small phone notification bubble descends like a heavy stone onto nv1's chest while nv1's shoulders curl inward and one hand moves protectively toward the pressure point",
        ),
        (
            ["qua tai", "overload", "lam hai long", "pleasing", "nhieu nguoi", "many people", "requests", "demands"],
            "anonymous silhouettes extend small request shapes toward nv1 as the stack in front of nv1 grows heavy, then nv1 slowly removes one weight from the pile to show overload becoming visible",
        ),
        (
            ["ranh gioi", "boundary", "gioi han", "wall", "buc tuong", "door", "canh cua", "tay nam", "handle"],
            "nv1 gently turns the door handle and opens the door a small amount while the cold wall lines behind it fade into softer boundary lines",
        ),
        (
            ["tieng on", "noise", "buoc lui", "step back", "binh yen", "peace", "tho", "breath"],
            "nv1 takes one clear step backward as jagged noise shapes recede from the frame and a calm open breathing space expands around the chest",
        ),
        (
            ["lua chon", "choice", "quyet dinh", "chon", "nervous", "he than kinh", "an toan", "safe", "safety"],
            "nv1 places one small calming choice-shape onto a simple path, and the protective line around the body steadies into a smoother circle to show the nervous system learning safety",
        ),
        (
            ["bien mat", "disappear", "duoc yen", "chon dieu gi", "allowed", "buoc vao", "enter"],
            "nv1 remains visible inside a soft boundary circle and gently lets one friendly shape enter while the other shapes pause outside the boundary",
        ),
        (
            ["bao ve nang luong", "protect energy", "energy", "tinh yeu", "healthy love", "love", "khong roi di", "does not leave"],
            "the soft boundary around nv1 brightens and steadies while one warm same-style silhouette stays nearby instead of moving away",
        ),
        (
            ["co don", "lonely", "alone", "isolated", "disconnect", "mat ket noi", "tach roi"],
            "surrounding silhouettes drift outward and leave quiet empty space around centered nv1 while nv1 remains still as the emotional anchor",
        ),
        (
            ["man hinh", "screen", "cell phone", "celular"],
            "nv1 lowers the phone slightly as the small screen glow softens and the nearby notification shape fades back from the center of attention",
        ),
        (
            ["calm", "binh tinh", "peace", "paz", "calma", "yen"],
            "nv1's shoulders drop into a calmer posture as a soft breath line expands outward and the surrounding clutter shapes settle farther away",
        ),
    ]
    for keys, movement in rules:
        if sum(1 for key in keys if key in combined) >= 2:
            return movement

    source = " ".join(part for part in [primary_action, visual_anchor, visual_moment, primary_subject] if str(part or "").strip())
    source = " ".join(str(source or "").split())
    if _is_concrete_psychology_video_action(source):
        return _format_director_action_as_motion(source)
    tc = _get_topic_config(topic)
    return tc.get("video_motion_fallback", "nv1 makes one small deliberate hand gesture toward the main symbolic object as the surrounding shapes shift outward in response, making the spoken idea readable through motion")


_derive_psychology_video_movement = _derive_video_movement


def _derive_emotional_arc(
    srt_text: str = "",
    primary_action: str = "",
    visual_anchor: str = "",
    visual_moment: str = "",
    primary_subject: str = "",
    style_profile: Optional[Dict[str, Any]] = None,
) -> str:
    combined = _normalize_vi_text_for_video(" ".join([
        srt_text,
        primary_action,
        visual_anchor,
        visual_moment,
        primary_subject,
    ]))
    profile = normalize_style_profile(style_profile)
    emotion_style = str(profile.get("cultural_emotion_style", "") or "").strip()

    arc_rules = [
        (
            ["anxiety", "worry", "fear", "lo lang", "so hai", "bat an", "nervous"],
            "Emotional arc: start with a small guarded pause and slightly curled shoulders, then show one visible breath, softer posture, and the nearby prop or light settling from tension into quiet steadiness",
        ),
        (
            ["stress", "pressure", "overwhelm", "ap luc", "cang thang", "qua tai"],
            "Emotional arc: begin with the body compressed by pressure, let the hands release one small burden, then end with the chest and shoulders visibly easing as the surrounding objects become less crowded",
        ),
        (
            ["boundary", "limit", "say no", "ranh gioi", "gioi han"],
            "Emotional arc: begin with hesitation, then a calm decisive gesture creates space, ending with nv1 standing a little steadier inside a protected boundary",
        ),
        (
            ["lonely", "alone", "isolated", "co don", "mat ket noi"],
            "Emotional arc: begin in still loneliness, let one tiny gesture acknowledge the emptiness, then end with a softer posture and a small warm detail that suggests being held by the space",
        ),
        (
            ["connect", "relationship", "love", "friend", "ket noi"],
            "Emotional arc: begin with distance, then a small reciprocal gesture between nv1 and a same-style silhouette or paired prop, ending with warmer body orientation and shared space",
        ),
        (
            ["let go", "release", "forgive", "buong bo", "tha thu"],
            "Emotional arc: begin with nv1 holding tension close, then slowly release the object or weight, ending with open hands and a calmer forward-facing posture",
        ),
        (
            ["growth", "change", "improve", "goal", "phat trien"],
            "Emotional arc: begin with uncertainty, then one modest step or caring gesture toward the growth symbol, ending with quiet pride rather than dramatic triumph",
        ),
        (
            ["calm", "peace", "binh tinh", "yen", "accept", "acceptance"],
            "Emotional arc: begin with a faint guarded posture, then slow breath and softened hands, ending in composed stillness that feels earned",
        ),
    ]
    for keys, arc in arc_rules:
        if any(key in combined for key in keys):
            if emotion_style:
                return f"{arc}. Match this audience's emotional expression style: {emotion_style[:260]}"
            return arc
    if emotion_style:
        return (
            "Emotional arc: start with a readable inner tension, shift through one restrained body-language change, "
            f"and end with a subtle emotional release. Match this audience's emotional expression style: {emotion_style[:260]}"
        )
    return (
        "Emotional arc: start with a readable inner tension, shift through one restrained body-language change, "
        "and end with a subtle emotional release that feels human and earned"
    )


def _clean_video_visual_text(text: str, max_len: int) -> str:
    cleaned = _strip_psychology_meta_instruction_language(str(text or ""))
    cleaned = _normalize_psychology_reference_language(cleaned, True)
    replacements = {
        "abstract": "simple visible",
        "conceptual": "concrete visible",
        "symbolic weight": "small heavy stone shape",
        "emotion orb": "soft light spot",
        "thought bubble": "small blank round shape",
        "thought bubbles": "small blank round shapes",
        "glowing orb": "soft light spot",
    }
    for old, new in replacements.items():
        cleaned = re.sub(re.escape(old), new, cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(
        r"\b(?:ABSOLUTELY\s+)?NO\b[^.]{0,260}(?:text|letters|watermark|caption|logo|numbers|signs)[^.]*\.?",
        " ",
        cleaned,
        flags=re.IGNORECASE,
    )
    cleaned = re.sub(r"\b(?:photorealistic|cinematic color grade|8K|ARRI|Sony Venice|sound|audio|smell)\b", " ", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\s{2,}", " ", cleaned).strip(" .")
    if len(cleaned) <= max_len:
        return cleaned.rstrip(" ,.;")
    cut = cleaned[:max_len]
    boundary = max(cut.rfind("."), cut.rfind(";"), cut.rfind(","))
    if boundary >= max(80, int(max_len * 0.55)):
        cut = cut[:boundary]
    else:
        space = cut.rfind(" ")
        if space >= max(80, int(max_len * 0.55)):
            cut = cut[:space]
    return cut.rstrip(" ,.;")


def _compact_psychology_video_motion(motion: str, max_len: int = 360) -> str:
    cleaned = _clean_video_visual_text(motion, max_len)
    if not cleaned:
        return cleaned
    cleaned = re.sub(r"\brepresents\b", "shows", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\bsymbolizes\b", "shows", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\bfeels\b", "shows through posture", cleaned, flags=re.IGNORECASE)
    return cleaned[:max_len].rstrip(" ,.;")


def _sample_style_video_prompt(
    srt_text: str,
    theme_context: str = "",
    movement: str = "",
    primary_action: str = "",
    visual_anchor: str = "",
    visual_moment: str = "",
    primary_subject: str = "",
    style_profile: Optional[Dict[str, Any]] = None,
    img_prompt: str = "",
    topic: str = "",
) -> str:
    profile = normalize_style_profile(style_profile)
    audience_hint = _build_psychology_audience_visual_hint(profile)
    movement_text = str(movement or "").strip()
    if not movement_text or _is_generic_psychology_video_motion(movement_text):
        movement_text = _derive_video_movement(
            srt_text=srt_text,
            primary_action=primary_action,
            visual_anchor=visual_anchor,
            visual_moment=visual_moment,
            primary_subject=primary_subject,
            style_profile=style_profile,
            img_prompt=img_prompt,
            topic=topic,
        )
    movement_text = _compact_psychology_video_motion(movement_text)
    visual_base = _clean_video_visual_text(_image_prompt_visual_base(img_prompt) or primary_subject or visual_anchor or primary_action, 360)
    narration_beat = _clean_video_visual_text(srt_text or visual_moment or primary_action or visual_anchor, 220)
    emotional_arc = _derive_emotional_arc(
        srt_text=srt_text,
        primary_action=primary_action,
        visual_anchor=visual_anchor,
        visual_moment=visual_moment,
        primary_subject=primary_subject,
        style_profile=style_profile,
    )
    emotional_arc = _clean_video_visual_text(emotional_arc, 220)
    prompt = (
        f"{_runtime_video_style(profile).rstrip('. ')}. "
        "Animate the exact illustrated setup from the image prompt; do not invent a new scene or redesign the character. "
        f"Faithfully visualize this narration beat in a direct and instantly legible way: {narration_beat}. "
        f"Image-derived visual base: {visual_base}. "
        f"Specific movement: {movement_text}. "
        f"{emotional_arc}. "
        "Use a composition, pose, and supporting props that stay clearly distinct from adjacent scenes. "
        "Performance direction: use one restrained body-language change, one prop or light response, and a final held pose; keep every movement visible, simple, and concrete. "
        "ABSOLUTELY NO readable text, letters, numbers, words, writing, captions, subtitles, signs, logos, or watermarks in the video."
    )
    return _strip_psychology_meta_instruction_language(_normalize_psychology_reference_language(prompt, True))


def _image_prompt_visual_base(img_prompt: str) -> str:
    text = _strip_psychology_meta_instruction_language(str(img_prompt or ""))
    text = _normalize_psychology_reference_language(text, True)
    text = re.sub(
        r"\b(?:ABSOLUTELY\s+)?NO\b[^.]{0,220}(?:text|letters|watermark|caption|logo)[^.]*\.?",
        " ",
        text,
        flags=re.IGNORECASE,
    )
    text = re.sub(
        r"\b(?:Theme context|Clear focal hierarchy|Technical|Palette)\s*:[^.]+\.?",
        " ",
        text,
        flags=re.IGNORECASE,
    )
    text = re.sub(r"\[[A-Z][A-Z _-]{2,}\]", " ", text)
    sentences = [s.strip(" .") for s in re.split(r"(?<=[.!?])\s+", text) if s.strip()]
    preferred = []
    for sentence in sentences:
        low = sentence.lower()
        if any(term in low for term in [
            "reference character", "phone", "screen", "hand", "thumb", "table", "room",
            "silhouette", "chair", "window", "door", "cup", "bowl", "shadow", "glow",
            "restaurant", "family", "message", "chat", "prop", "body language",
        ]):
            preferred.append(sentence)
    base = ". ".join((preferred or sentences)[:3])
    base = re.sub(r"\s{2,}", " ", base).strip(" .")
    return base[:520]


def _extract_psychology_concept_keywords(srt_text: str) -> List[str]:
    import unicodedata

    text = str(srt_text or "")
    if not text:
        return []
    norm = unicodedata.normalize("NFKD", text.lower())
    norm = "".join(ch for ch in norm if not unicodedata.combining(ch))
    rules = [
        (r"\b(lo lang|so hai|bat an|lo au|anxiety|worry|fear)\b", "anxiety storm cloud"),
        (r"\b(ap luc|cang thang|qua tai|suc ep|stress|pressure|overwhelm)\b", "stress stone stack"),
        (r"\b(thoi quen|vong lap|lap lai|habit|routine|loop|pattern)\b", "habit loop path"),
        (r"\b(tri hoan|ne tranh|cham tre|procrastination|delay|avoid)\b", "procrastination task mountain"),
        (r"\b(tu tin|gia tri ban than|tin vao|self worth|confidence)\b", "confidence growing plant"),
        (r"\b(ranh gioi|gioi han|tu choi|boundary|limit|say no)\b", "personal boundary circle"),
        (r"\b(so sanh|ghen ti|ganh ty|compare|comparison|jealous)\b", "comparison mirror"),
        (r"\b(ket noi|moi quan he|co don|yeu thuong|relationship|connect|lonely|love)\b", "relationship bridge"),
        (r"\b(lua chon|quyet dinh|chon lua|choice|decision|choose)\b", "decision forked path"),
        (r"\b(suy nghi|niem tin|tam tri|thought|mind|belief|thinking)\b", "thought bubbles"),
        (r"\b(gian|tuc gian|anger|angry)\b", "anger shadow under a joke mask"),
        (r"\b(cam xuc|buon|emotion|feeling|sad)\b", "emotion orb"),
        (r"\b(muc tieu|phat trien|thay doi|tot hon|tien bo|goal|growth|improve|change)\b", "growth steps"),
        (r"\b(chap nhan|accept|acceptance|embrace)\b", "acceptance glowing shape"),
        (r"\b(tha thu|buong bo|forgive|forgiveness|let go)\b", "letting go released weight"),
        (r"\b(biet on|cam on|tri an|gratitude|grateful|thankful)\b", "gratitude glowing heart"),
        (r"\b(kien nhan|cho doi|nhan nai|patience|waiting)\b", "patience hourglass"),
        (r"\b(dung cam|can dam|courage|brave|bravery)\b", "courage opening door"),
        (r"\b(hy vong|tich cuc|lac quan|hope|hopeful|optimis)\b", "hope sunrise"),
    ]
    concepts = [label for pattern, label in rules if re.search(pattern, norm, flags=re.IGNORECASE)]
    return concepts[:4]


def _concept_supported_by_prompt(concept: str, prompt: str) -> bool:
    low = str(prompt or "").lower()
    concept_low = str(concept or "").lower()
    if any(part in low for part in concept_low.split() if len(part) >= 4):
        return True
    support_terms = {
        "anxiety storm cloud": ["anxiety", "worry", "fear", "shallow breathing", "tight chest", "cloud", "storm", "trembling"],
        "stress stone stack": ["overload", "overwhelmed", "too many", "many people", "requests", "demands", "stone", "weight", "stack", "heavy", "pressure"],
        "habit loop path": ["loop", "repeating", "routine", "small choice", "daily", "path", "cycle"],
        "procrastination task mountain": ["mountain", "unfinished", "delayed", "task", "avoid", "steps"],
        "confidence growing plant": ["confidence", "self-worth", "growing", "plant", "sprout", "upright posture"],
        "personal boundary circle": ["boundary", "door", "handle", "wall", "threshold", "circle", "protected", "choose what may enter", "quiet space"],
        "comparison mirror": ["mirror", "comparison", "side-by-side", "envy", "looking at others"],
        "relationship bridge": ["love", "healthy love", "connection", "relationship", "bridge", "stable connection", "does not leave", "nearby silhouette"],
        "decision forked path": ["choice", "choose", "decision", "allowed to enter", "forked", "path", "hand selecting", "small daily choice"],
        "thought bubbles": ["mind", "thought", "mental", "realize", "awareness", "tired body", "body before the mind", "lowered shoulders"],
        "anger shadow under a joke mask": ["anger", "angry", "joke", "mask", "shadow", "sharp"],
        "emotion orb": ["emotion", "feeling", "sad", "soft glow", "body language"],
        "growth steps": ["growth", "progress", "steps", "small daily choice", "learn", "safety"],
        "acceptance glowing shape": ["accept", "embrace", "open hands", "glowing shape"],
        "letting go released weight": ["let go", "release", "weight", "stone", "open hands"],
        "gratitude glowing heart": ["gratitude", "thankful", "heart", "warm glow"],
        "patience hourglass": ["patience", "waiting", "hourglass", "slow breath"],
        "courage opening door": ["courage", "brave", "door", "handle", "opening"],
        "hope sunrise": ["hope", "hopeful", "sunrise", "light", "opening"],
    }
    return any(term in low for term in support_terms.get(concept_low, []))

def _has_quality_anchor(prompt: str) -> bool:
    low = str(prompt or "").lower()
    anchors = [
        "metaphor", "symbolic", "body language", "emotion", "overwhelm", "habit", "thought",
        "mind", "mirror", "knot", "bridge", "maze", "cloud", "plant", "scale", "silhouette",
        "paper texture", "pastel", "psychology", "finance", "success", "self-development",
        "illustration", "anime", "cartoon", "toon", "manga", "watercolor", "vector",
        "flat color", "storybook", "coin", "savings", "investment", "goal", "discipline",
    ]
    return any(anchor in low for anchor in anchors)


_has_psychology_quality_anchor = _has_quality_anchor


def _normalize_for_culture_match(text: str) -> str:
    import unicodedata

    normalized = unicodedata.normalize("NFKD", str(text or "").lower())
    normalized = "".join(ch for ch in normalized if not unicodedata.combining(ch))
    normalized = re.sub(r"[^a-z0-9]+", " ", normalized)
    return " ".join(normalized.split())


def _culture_terms_from_profile(style_profile: Optional[Dict[str, Any]] = None) -> List[str]:
    profile = normalize_style_profile(style_profile)
    raw_parts: List[str] = []
    value = str(profile.get("cultural_props", "") or "")
    for part in re.split(r"[,|:;]", value):
        part = part.strip()
        if part:
            raw_parts.append(part)
    metaphors = str(profile.get("cultural_metaphors", "") or "")
    for item in metaphors.split("|"):
        item = item.split(":", 1)[-1] if ":" in item else item
        for part in re.split(r"[,;]", item):
            part = part.strip()
            if part:
                raw_parts.append(part)
    context = str(profile.get("audience_culture_note", "") or "")
    for token in re.findall(r"\b[a-zA-Z][a-zA-Z\-]{4,}\b", context):
        if token.lower() not in {
            "design", "viewers", "settings", "concepts", "audiences", "through",
            "emotional", "psychology", "finance", "personal", "growth", "self", "should",
            "include", "props", "daily", "vulnerability", "healing",
        }:
            raw_parts.append(token)

    generic_psychology_terms = {
        "anxiety", "stress", "boundary", "connection", "letting", "loneliness",
        "growth", "courage", "acceptance", "forgiveness", "emotion", "feeling",
        "calm", "peace", "self", "improvement", "psychology", "finance", "viewers",
        "people", "character", "silhouette", "silhouettes", "symbolic",
        "metaphor", "visual", "clear", "warm", "soft", "simple",
    }
    terms: List[str] = []
    for part in raw_parts:
        norm = _normalize_for_culture_match(part)
        if not norm:
            continue
        words = [w for w in norm.split() if len(w) >= 3 and w not in generic_psychology_terms]
        candidates = []
        if len(words) >= 2:
            candidates.append(" ".join(words[:3]))
            candidates.append(" ".join(words[:2]))
        candidates.extend(words[:2])
        for candidate in candidates:
            if len(candidate) >= 4 and candidate not in terms:
                terms.append(candidate)
    return terms[:80]


def _prompt_has_audience_cultural_fit(prompt: str, style_profile: Optional[Dict[str, Any]] = None) -> bool:
    profile = normalize_style_profile(style_profile)
    if not str(profile.get("audience_language", "") or "").strip():
        return True
    prompt_norm = _normalize_for_culture_match(prompt)
    terms = _culture_terms_from_profile(profile)
    if not terms:
        return True
    hits = [term for term in terms if term in prompt_norm]
    if hits:
        return True
    language = _normalize_for_culture_match(profile.get("audience_language", ""))
    return bool(language and language in prompt_norm)


def _build_psychology_audience_visual_hint(style_profile: Optional[Dict[str, Any]] = None) -> str:
    profile = normalize_style_profile(style_profile)
    language = str(profile.get("audience_language", "") or "").strip()
    if not language:
        return ""
    hint = f"Audience-cultural fit: if the narration calls for a setting, prop, or ritual, choose one that feels natural to {language}-speaking viewers; otherwise keep the visual narration-driven"
    return hint + "."


def _has_forbidden_style_term(prompt: str, term: str) -> bool:
    low = str(prompt or "").lower()
    target = str(term or "").lower()
    if not target or target not in low:
        return False
    negated_patterns = [
        rf"\bno\s+{re.escape(target)}\b",
        rf"\bwithout\s+{re.escape(target)}\b",
        rf"\bavoid\s+{re.escape(target)}\b",
        rf"\bnot\s+{re.escape(target)}\b",
    ]
    for match in re.finditer(re.escape(target), low):
        context = low[max(0, match.start() - 32):match.end()]
        if any(re.search(pattern, context) for pattern in negated_patterns):
            continue
        return True
    return False


def _score_from_missing(base: float, missing: List[str], critical_failures: List[str], minor_penalty: float = 0.35) -> float:
    score = base - len(missing) * minor_penalty - len(critical_failures) * 1.5
    return max(0.0, min(10.0, round(score, 2)))


def score_psychology_sample_style_img(
    img_prompt: str,
    srt_text: str = "",
    char_ids: Optional[List[str]] = None,
    style_profile: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    prompt = str(img_prompt or "")
    low = prompt.lower()
    char_ids = char_ids or []
    missing: List[str] = []
    strengths: List[str] = []
    critical_failures: List[str] = []

    profile = normalize_style_profile(style_profile)
    if style_profile:
        style_terms = _profile_style_terms(profile)
        if style_terms and any(term in low for term in style_terms[:8]):
            strengths.append("channel style profile")
        else:
            missing.append("channel style profile terms")
        required_phrases = {
            "focal point": "focal",
            "emotional readability": "emotion",
            "concrete visual detail": "detail",
        }
        alternatives = {
            "focal point": ["clear focal point", "focal hierarchy", "viewer notices", "eye is drawn"],
            "emotional readability": ["subtext", "feels", "body language", "posture", "emotional"],
            "concrete visual detail": ["object", "room", "phone", "table", "chair", "door", "hands", "face", "shadow", "silhouette"],
        }
    else:
        required_phrases = {
            "sample style": "minimalist hand-drawn 2d psychological explainer illustration",
            "off-white background": "off-white paper background",
            "warm gray palette": "warm gray",
            "main character": "main character from provided reference",
            "same sketch style": "same exact sketch style",
            "focal point": "focal",
            "emotional readability": "emotion",
        }
        alternatives = {
            "main character": ["main character from provided reference", "character from reference image"],
            "focal point": ["clear focal point", "focal hierarchy", "viewer notices", "eye is drawn"],
            "emotional readability": ["subtext", "feels", "body language", "posture", "emotional"],
        }
    for label, phrase in required_phrases.items():
        accepted = phrase in low or any(alt in low for alt in alternatives.get(label, []))
        if accepted:
            strengths.append(label)
        else:
            missing.append(label)

    guards = ["no real humans", "no photorealism", "no 3d", "no text", "no letters", "no watermark"]
    if style_profile:
        guards = [guard for guard in guards if guard in profile["negative_prompt"].lower()] or ["no readable text", "no text", "no watermark"]
    for guard in guards:
        if guard in low:
            strengths.append(guard)
        else:
            missing.append(guard)

    forbidden = [term for term in PSYCHOLOGY_FORBIDDEN_STYLE_TERMS if _has_forbidden_style_term(prompt, term)]
    hard_forbidden = [term for term in forbidden if term.lower() not in {"photorealistic"}]
    if hard_forbidden:
        critical_failures.append("forbidden style terms: " + ", ".join(hard_forbidden[:5]))
    if re.search(r"\bnv(?!1\b)\d+\b|\bnv_[\w-]+\b", prompt, flags=re.IGNORECASE):
        critical_failures.append("invented non-nv1 character reference")
    if "camera:" in low and "motion:" in low and "action:" in low:
        critical_failures.append("image prompt contains old video schema")
    if PSYCHOLOGY_META_INSTRUCTION_TERMS.search(prompt):
        critical_failures.append("contains internal meta instruction language")
    if not _has_quality_anchor(prompt):
        critical_failures.append("missing visual quality anchor")
    if PSYCHOLOGY_LOW_QUALITY_GENERIC_TERMS.search(prompt):
        critical_failures.append("generic low-quality phrasing")

    if srt_text:
        concepts = _extract_psychology_concept_keywords(srt_text)
        if concepts:
            concept_hits = [concept for concept in concepts if _concept_supported_by_prompt(concept, prompt)]
            if concept_hits:
                strengths.append("narration concept")
            else:
                missing.append("narration concept: " + ", ".join(concepts[:3]))
        ok, keywords_missing = check_narration_keywords_in_prompt(srt_text, prompt)
        if not ok and keywords_missing:
            missing.append("narration keywords: " + ", ".join(keywords_missing[:4]))

    if len(prompt) < 650:
        missing.append("sample-level detail length")
    elif len(prompt) >= 900:
        strengths.append("sample-level detail length")

    score = _score_from_missing(10.0, missing, critical_failures, minor_penalty=0.28)
    return {
        "score": score,
        "critical_failures": critical_failures,
        "missing": missing,
        "strengths": strengths,
        "length": len(prompt),
    }


def score_psychology_sample_style_video(
    video_prompt: str,
    srt_text: str = "",
    style_profile: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    prompt = str(video_prompt or "")
    low = prompt.lower()
    missing: List[str] = []
    strengths: List[str] = []
    critical_failures: List[str] = []

    profile = normalize_style_profile(style_profile)
    if style_profile:
        style_terms = _profile_style_terms({
            **profile,
            "image_style": profile.get("video_style", profile.get("image_style", "")),
        })
        if style_terms and any(term in low for term in style_terms[:8]):
            strengths.append("channel video style profile")
        else:
            missing.append("channel video style profile terms")
        required_phrases = {}
    else:
        required_phrases = {
            "sample video style": "same minimalist hand-drawn psychological explainer style",
            "off-white paper": "off-white paper",
            "warm gray tones": "warm gray tones",
            "emotional anchor": "emotional anchor",
            "same sketch style": "same sketch style",
        }
    for label, phrase in required_phrases.items():
        _any_topic_style = any(f"{tv['prompt_label'].split()[0].lower()} style" in low for tv in TOPIC_PROMPT_CONFIG.values())
        accepted = phrase in low or (label == "sample video style" and style_profile and _any_topic_style) or (label == "same sketch style" and "same exact sketch style" in low)
        if accepted:
            strengths.append(label)
        else:
            missing.append(label)

    guards = [
        "no readable text", "no letters", "no numbers", "no captions", "no subtitles",
        "no signs", "no logos", "no watermark",
    ]
    if style_profile:
        profile_negative = profile["negative_prompt"].lower()
        guards = [guard for guard in guards if guard in profile_negative] or guards
    for guard in guards:
        if guard in low:
            strengths.append(guard)
        else:
            missing.append(guard)

    if any(term in low for term in ["same exact visual language", "same channel style", "same exact sketch style"]):
        strengths.append("style/reference lock")
    else:
        missing.append("style/reference lock")
    if any(term in low for term in ["do not redesign", "preserve the reference character", "preserve the reference silhouette"]):
        strengths.append("character redesign lock")
    else:
        missing.append("character redesign lock")
    if any(term in low for term in ["faithfully visualize this narration beat", "animate this exact illustrated setup", "animate the exact illustrated setup"]):
        strengths.append("direct narration beat grounding")
    else:
        missing.append("direct narration beat grounding")
    if any(term in low for term in ["image-derived visual base", "image prompt", "exact illustrated setup", "same keyframe"]):
        strengths.append("image-derived video grounding")
    else:
        missing.append("image-derived video grounding")
    if any(term in low for term in ["distinct from adjacent scenes", "supporting props", "composition, pose"]):
        strengths.append("adjacent-scene distinctiveness")
    else:
        missing.append("adjacent-scene distinctiveness")

    abstraction_hits = [
        term for term in [
            "glowing emotion orb", "emotion orb", "thought bubbles", "abstract weight shapes",
            "represents", "symbolizes", "inner world", "emotional data",
        ] if term in low
    ]
    if abstraction_hits:
        missing.append("over-abstract video language")

    if "camera:" in low and "motion:" in low and "action:" in low:
        critical_failures.append("old CAMERA/MOTION/ACTION schema")
    if PSYCHOLOGY_META_INSTRUCTION_TERMS.search(prompt):
        critical_failures.append("contains internal meta instruction language")
    if re.search(r"\b(sound|audio|smell|scent|duration|seconds|asset id|reference file|nv1\.png)\b", low):
        critical_failures.append("video mentions forbidden non-visual/technical/reference terms")
    if _is_generic_psychology_video_motion(prompt):
        missing.append("generic video movement")
    else:
        strengths.append("concrete video movement")
    concrete_motion_terms = [
        "steps", "turns", "opens", "lowers", "places", "presses", "recedes", "expands",
        "brightens", "drifts", "pauses", "enters", "touches", "nods", "sinks", "curls",
        "extends", "removes", "holds", "lets", "glows", "fades", "moves", "settles",
        "breath", "shoulders", "hands", "gaze", "posture", "release", "softened",
    ]
    if any(term in low for term in concrete_motion_terms):
        strengths.append("visible movement verb")
    if srt_text and len(str(srt_text).strip()) >= 8:
        if str(srt_text).strip().lower()[:24] in low:
            strengths.append("contains exact narration")
        elif _prompt_visualizes_srt_core(prompt, srt_text):
            strengths.append("visualizes narration idea")
        else:
            missing.append("narration idea")
    if len(prompt) < 450:
        missing.append("sample-level video detail length")
    elif len(prompt) >= 600:
        strengths.append("sample-level video detail length")

    score = _score_from_missing(10.0, missing, critical_failures, minor_penalty=0.32)
    return {
        "score": score,
        "critical_failures": critical_failures,
        "missing": missing,
        "strengths": strengths,
        "length": len(prompt),
    }


def score_psychology_scene_prompt_pair(
    scene: Dict[str, Any],
    style_profile: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    char_ids = scene.get("characters_used", []) or []
    if isinstance(char_ids, str):
        char_ids = [cid.strip() for cid in char_ids.split(",") if cid.strip() and cid.strip() != "[]"]
    img_score = score_psychology_sample_style_img(
        scene.get("img_prompt", ""),
        scene.get("srt_text", ""),
        char_ids=char_ids,
        style_profile=style_profile or scene.get("style_profile"),
    )
    video_score = score_psychology_sample_style_video(
        scene.get("video_prompt", ""),
        scene.get("srt_text", ""),
        style_profile=style_profile or scene.get("style_profile"),
    )
    pair_score = round((float(img_score["score"]) + float(video_score["score"])) / 2, 2)
    critical_failures = img_score["critical_failures"] + video_score["critical_failures"]
    return {
        "score": pair_score,
        "img": img_score,
        "video": video_score,
        "critical_failures": critical_failures,
    }


def _ensure_psychology_prompt_quality(
    prompt: str,
    char_ids: List[str],
    srt_text: str = "",
    primary_subject: str = "",
    primary_action: str = "",
    visual_anchor: str = "",
    style_profile: Optional[Dict[str, Any]] = None,
) -> str:
    profile = normalize_style_profile(style_profile)
    primary_subject = _strip_forced_psychology_cultural_props(primary_subject)
    primary_action = _strip_forced_psychology_cultural_props(primary_action)
    visual_anchor = _strip_forced_psychology_cultural_props(visual_anchor)
    cleaned = _strip_psychology_forbidden_style(prompt)

    # Remove forced cultural props that don't match SRT content
    cleaned = _strip_forced_psychology_cultural_props(cleaned)
    cleaned = _collapse_duplicate_psychology_style_blocks(cleaned, profile)

    if "nv1" in char_ids:
        cleaned = _strip_attached_character_design_from_prompt(cleaned, profile)
        cleaned = _normalize_psychology_reference_language(cleaned, True)
    audience_hint = _build_psychology_audience_visual_hint(profile)
    low_cleaned = cleaned.lower()
    style_terms = _profile_style_terms(profile)
    _style_language_markers = [
        "hand-drawn", "channel style", "provided reference character",
    ]
    for _tc_val in TOPIC_PROMPT_CONFIG.values():
        _pl = _tc_val.get("prompt_label", "")
        if _pl:
            _style_language_markers.extend([f"2d {_pl.split()[0].lower()}", f"{_pl.split()[0].lower()} editorial", _pl])
    has_style_language = (
        (style_terms and any(term in low_cleaned for term in style_terms[:8]))
        or any(term in low_cleaned for term in _style_language_markers)
    )
    core_source = " ".join([primary_subject, primary_action, visual_anchor])
    core_tokens = [
        token for token in re.findall(r"\b[a-zA-Z][a-zA-Z-]{3,}\b", core_source.lower())
        if token not in {"with", "that", "this", "from", "into", "through", "character", "reference"}
    ]
    core_token_hits = sum(1 for token in dict.fromkeys(core_tokens) if token in low_cleaned)
    hybrid_ready = (
        len(cleaned) >= 260
        and not PSYCHOLOGY_META_INSTRUCTION_TERMS.search(cleaned)
        and _has_quality_anchor(cleaned)
        and (
            _prompt_visualizes_srt_core(cleaned, srt_text, primary_subject, primary_action, visual_anchor)
            or core_token_hits >= 3
        )
    )
    if hybrid_ready:
        if not has_style_language:
            cleaned = f"{_runtime_image_style(profile).rstrip('. ')}. " + cleaned
        if "nv1" in char_ids and "provided reference image" not in cleaned.lower() and "reference character" not in cleaned.lower():
            cleaned = cleaned.rstrip(". ") + " Use the provided reference image as the character identity source; preserve the reference character exactly."
        if not any(term in cleaned.lower() for term in ["clear focal", "focal hierarchy", "focal point", "viewer first notices", "eye is drawn"]):
            focal = visual_anchor or primary_action or primary_subject or "the central narration beat"
            cleaned = cleaned.rstrip(". ") + f" Clear focal hierarchy: viewer first notices {focal}."
        if not any(guard in cleaned.lower() for guard in ["no readable text", "no text", "no letters", "no watermark"]):
            cleaned = cleaned.rstrip(". ") + f" {profile['negative_prompt']}."
        cleaned = _strip_psychology_forbidden_style(cleaned)
        cleaned = _strip_forced_psychology_cultural_props(cleaned)
        cleaned = _normalize_psychology_reference_language(cleaned, "nv1" in char_ids)
        return _collapse_duplicate_psychology_style_blocks(cleaned, profile)
    if "depict this exact script idea literally and clearly:" in cleaned.lower():
        if style_terms and not any(term in cleaned.lower() for term in style_terms[:5]):
            cleaned = f"{_runtime_image_style(profile).rstrip('. ')}. " + cleaned
        if (
            "same exact channel style" not in cleaned.lower()
            and "consistent style" not in cleaned.lower()
            and "same exact sketch style" not in cleaned.lower()
        ):
            cleaned = cleaned.rstrip(". ") + ", same exact channel style for every figure, prop, room, and symbolic object."
        if "direct visual cause-and-effect" not in cleaned.lower():
            cleaned = cleaned.rstrip(". ") + " Show direct visual cause-and-effect through grounded props and body language."
        if "avoid generic symbolism" not in cleaned.lower():
            cleaned = cleaned.rstrip(". ") + " Avoid generic symbolism when a concrete real-life situation can express the line more precisely."
        if audience_hint and not _prompt_visualizes_srt_core(cleaned, srt_text, primary_subject, primary_action, visual_anchor):
            cleaned = cleaned.rstrip(". ") + " " + audience_hint
        if "nv1" in char_ids and "provided reference image" not in cleaned.lower():
            cleaned = cleaned.rstrip(". ") + " Use the provided reference image as the recurring character identity source; preserve the reference character exactly and do not create a new character design."
        if profile["negative_prompt"].lower() not in cleaned.lower():
            cleaned = cleaned.rstrip(". ") + f" {profile['negative_prompt']}."
        cleaned = _strip_psychology_forbidden_style(cleaned)
        cleaned = _strip_forced_psychology_cultural_props(cleaned)
        cleaned = _normalize_psychology_reference_language(cleaned, "nv1" in char_ids)
        return _collapse_duplicate_psychology_style_blocks(cleaned, profile)
    existing_block_count = len(re.findall(r"\[[A-Z][A-Z _-]{2,}\]", cleaned))
    needs_root_fallback = (
        existing_block_count >= 2
        or len(cleaned) < 220
        or not _has_quality_anchor(cleaned)
        or not (
            _prompt_visualizes_srt_core(cleaned, srt_text, primary_subject, primary_action, visual_anchor)
            or core_token_hits >= 2
        )
    )
    if needs_root_fallback:
        cleaned = _build_psychology_quality_fallback(
            srt_text=srt_text,
            primary_subject=primary_subject,
            primary_action=primary_action,
            visual_anchor=visual_anchor,
            char_ids=char_ids,
            style_profile=profile,
        )
    else:
        if not has_style_language:
            cleaned = f"{_runtime_image_style(profile).rstrip('. ')}. " + cleaned
        if "nv1" in char_ids and "provided reference image" not in cleaned.lower() and "reference character" not in cleaned.lower():
            cleaned = cleaned.rstrip(". ") + " Use the provided reference image as the recurring character identity source; preserve the reference character exactly."
        if "clear focal hierarchy" not in cleaned.lower():
            focal = visual_anchor or primary_action or primary_subject or "the central narration beat"
            cleaned = cleaned.rstrip(". ") + f" Clear focal hierarchy: viewer first notices {focal}."
        if "no readable text" not in cleaned.lower():
            cleaned = cleaned.rstrip(". ") + f" {profile['negative_prompt']}."
    cleaned = _strip_psychology_forbidden_style(cleaned)
    cleaned = _strip_forced_psychology_cultural_props(cleaned, allowed_context=srt_text)
    cleaned = _normalize_psychology_reference_language(cleaned, "nv1" in char_ids)
    action_fragment = _normalize_psychology_anchor_fragment(primary_action)
    if action_fragment and action_fragment.lower() not in cleaned.lower():
        cleaned = cleaned.rstrip(". ") + f". Action/body language: {action_fragment}."
    return _collapse_duplicate_psychology_style_blocks(cleaned, profile).strip()


def get_scene_system_prompt(topic: str = "story", style_profile: Optional[Dict[str, Any]] = None) -> str:
    if not _is_psychology_topic(topic):
        return SYSTEM_PROMPT_SCENE_PROMPTS
    profile = normalize_style_profile(style_profile)
    runtime_image_style = _runtime_image_style(profile)
    runtime_video_style = _runtime_video_style(profile)
    audience_language = str(profile.get('audience_language', '') or '').strip()
    audience_culture_note = str(profile.get('audience_culture_note', '') or '').strip()
    cultural_props = str(profile.get('cultural_props', '') or '').strip()
    cultural_metaphors = str(profile.get('cultural_metaphors', '') or '').strip()
    cultural_emotion_style = str(profile.get('cultural_emotion_style', '') or '').strip()
    audience_block = ""
    if audience_language:
        emotion_block = f"\nEMOTIONAL EXPRESSION STYLE: {cultural_emotion_style}" if cultural_emotion_style else ""
        audience_block = f"""

TARGET AUDIENCE: {audience_language}-speaking viewers.
{('CULTURAL CONTEXT: ' + audience_culture_note) if audience_culture_note else ''}{emotion_block}
AUDIENCE FIT GUIDANCE: The narration decides the image. If the narration naturally involves a setting, object, or ritual, prefer one that feels familiar to {audience_language} audiences. Do NOT force cultural props into every scene."""
    tc = _get_topic_config(topic)
    topic_label = tc["topic_label"]
    topic_desc = tc["topic_desc"]
    topic_metaphor_hint = tc["metaphor_hint"]
    topic_question = tc["quality_question"]
    return f"""You are an EDUCATIONAL {topic_label} ILLUSTRATOR and VISUAL METAPHOR DIRECTOR for AI image generation.

YOUR MISSION: For each scene, write one clear, engaging {topic_desc} image prompt and one matching image-to-video prompt in this channel's fixed visual style.

CHANNEL STYLE PROFILE:
- Style name: {profile['style_name']}
- Image style: {runtime_image_style}
- Video style: {runtime_video_style}
- Palette: {profile['palette']}
- Negative rules: {profile['negative_prompt']}
- Character reference: the provided reference image defines the recurring character identity/style; preserve that reference character exactly and do not spend prompt tokens re-describing it.
{audience_block}

CRITICAL RULE #1 - NARRATION IS ABSOLUTE TRUTH:
The NARRATION field contains the exact spoken idea. It may be in any language. You MUST translate the narration idea into English for the img_prompt and video_prompt output. NEVER copy non-English text directly into the output prompts. The image/video prompt must describe the visual scene entirely in English.

CRITICAL RULE #2 - ABSOLUTELY NO TEXT IN IMAGES:
The generated images must contain ZERO text, letters, words, writing, captions, labels, signs, numbers, or any readable characters in any language. This is non-negotiable. Do not describe text appearing in the scene. If the narration mentions reading, writing, or text, show the OBJECT (book, phone, screen) but describe it as blank or with abstract shapes instead of readable content.

HYBRID PROMPT METHOD:
Use the existing workbook planning as the foundation. The locked scene spec and ARTISTIC VISION fields are not suggestions to ignore; they are the visual design brief. Write clean final prompts, not instructions for another model.

IMAGE PROMPT METHOD:
Start with the runtime image style sentence, then describe one frameable {topic_desc} illustration. Use primary_subject and primary_action as the frame foundation. Use key_focus and viewer_attention as the main visual hook. Use subtext_delivery and artistic_intent to make the emotional meaning readable. If visual_anchor conflicts with key_focus, prefer key_focus and the NARRATION. Do not copy non-English narration text into the prompt.

VIDEO PROMPT METHOD:
Start with the runtime video style sentence, then animate the exact illustrated setup from the image prompt; do not invent a new scene or redesign the reference character. The movement must come from primary_action, character_action, key_focus, viewer_attention, or a narration-relevant prop/light/space response. Every video_prompt should make the narration beat instantly legible, reuse the image prompt's subject/pose/props/layout/lighting, add one subtle visible movement plus a restrained emotional arc, and include composition/pose/supporting props that stay distinct from adjacent scenes. Do not use camera gear, duration, sound, smell, or invisible atmosphere.
LOCKED SCENE SPEC: If primary_subject, primary_action, visual_anchor, scene_kind, or subject_mode are provided for a scene, they are mandatory anchors for both img_prompt and video_prompt. Do not replace them with a generic prop, mood, or repeated channel motif.

CRITICAL {topic_label} IMAGE RULES:
1. Every scene prompt must be unique and derived from the narration.
2. ALL output prompts (img_prompt, video_prompt) MUST be written entirely in English regardless of the narration language.
3. {topic_metaphor_hint}
4. Use only the provided reference image as the stable recurring character identity source. Refer to it briefly as the reference character; do not write long character descriptions or create a new character.
5. Other people must be anonymous silhouettes or simple background figures, never new named/reference characters.
6. No readable words, labels, captions, UI text, chart text, document text, signs, numbers, logos, or watermarks in the image/video.
7. Follow the channel style profile exactly. Do not drift to a different channel's sample style.
8. The narration and locked scene spec decide the image. Use a cultural prop/setting only if it directly clarifies this exact SRT line. Do NOT add props merely because they appear in the audience profile.
9. Every video_prompt must contain a concrete visible movement and emotional arc so the character or visual object feels emotionally alive.
10. img_prompt: 80-150 words; every prompt must include clear focal hierarchy and one memorable symbolic visual anchor.
11. Do not reuse the same prop or action across a batch unless the narration itself repeats it. A culturally familiar prop is only useful when it clarifies the current SRT line.
12. Ask: {topic_question}
13. Never output old internal scaffolding labels, translation instructions, or prompt-writing instructions. Write only the final image/video prompt text."""

MINOR_WORD_REPLACEMENTS: List[Tuple[str, str]] = [
    (r"\b\d{1,2}\s*-\s*year-old\b", "young"),
    (r"\bchild(?:ren)?\b", "person"),
    (r"\bkid(?:s)?\b", "person"),
    (r"\bboy(?:s)?\b", "person"),
    (r"\bgirl(?:s)?\b", "person"),
    (r"\bteen(?:ager)?s?\b", "person"),
    (r"\btoddler(?:s)?\b", "person"),
    (r"\bbab(?:y|ies)\b", "person"),
    (r"\binfant(?:s)?\b", "person"),
    (r"\bminor(?:s)?\b", "person"),
    (r"\bson\b", "family member"),
    (r"\bdaughter\b", "family member"),
]

UNSUPPORTED_DETAIL_VARIANTS: Dict[str, List[str]] = {
    "worksheet": ["worksheet", "work sheet"],
    "statistics sheet": ["statistics sheet", "statistics printout", "printed statistics", "stat sheet", "stats sheet"],
    "research packet": ["research packet", "research pages", "research printout", "research printouts"],
    "article pages": ["article pages", "printed articles", "article printouts"],
    "highlighted article": ["highlighted article", "highlighted articles", "bookmarked article", "bookmarked articles"],
    "browser tabs": ["browser tabs", "multiple tabs", "tabs open", "bookmarks bar", "bookmark tabs"],
    "screen content": [
        "search results", "text-heavy article", "text heavy article", "article on screen",
        "relationship article", "browser window", "article visible on screen", "webpage on screen",
        "screen full of text", "screen showing an article"
    ],
    "reason card": ["reason card", "reason cards", "index card", "index cards", "cue card", "cue cards"],
    "manila folder": ["manila folder", "file folder"],
    "margin notes": ["margin notes", "annotated notes", "underlined passages"],
    "receipt": ["receipt", "store receipt", "sales receipt", "order form"],
    "paperwork props": [
        "printout", "printouts", "printed pages", "stack of papers", "paper stack",
        "papers spread", "paperwork", "folders", "folder tabs", "labeled tabs",
        "notes spread", "written notes", "notebook timeline", "notebook",
        "binders", "binder clips", "organized case file", "case file", "timeline notes"
    ],
}

VIDEO_AUDIO_TERMS = re.compile(
    r"\b(sound|audio|silence|silent|quiet|voice|hear|hearing|listen|music|noise|smell|odor|scent|gurgle|thud|traffic)\b",
    flags=re.IGNORECASE,
)
VIDEO_ABSTRACT_TERMS = re.compile(
    r"\b(atmosphere|tension|energy|vibe|palpable|unsaid|devastation|weight of|feeling of)\b",
    flags=re.IGNORECASE,
)
VIDEO_UNSAFE_MOTION_TERMS = re.compile(
    r"\b(micro-?tremor|breathing camera|handheld|orbit|crane|whip pan|rack focus|dolly|tilt|shake|shaky|unstable|surreal|distort(?:ed|ion)?|contort(?:ed|ion)?|shudder(?:ing)?|pulsing)\b",
    flags=re.IGNORECASE,
)
VIDEO_NAME_OR_ASSET_TERMS = re.compile(
    r"\b(?:nv|loc|char|env)_?\d+\b|\([^)]+\.(?:png|jpg|jpeg)\)",
    flags=re.IGNORECASE,
)
MONTAGE_TERMS = re.compile(
    r"\b(sequence of|series of|montage|split[- ]screen|composite|superimposed|superimpose|ghostly|translucent|layered|flashback insert|insert shot|diptych|triptych|three shots|multiple shots|simultaneous actions?|two simultaneous actions?|shot 1|shot 2|1\)|2\)|3\))\b",
    flags=re.IGNORECASE,
)
MINOR_VISIBLE_TERMS = re.compile(
    r"\b(child|children|kid|kids|boy|girl|daughter|son|teen|teenage|toddler|baby|infant|juvenile|9-year-old|10-year-old|11-year-old|12-year-old|13-year-old|14-year-old|15-year-old|16-year-old|17-year-old)\b",
    flags=re.IGNORECASE,
)
STORY_SUMMARY_STYLE_PREFIX = re.compile(
    r"^\[STYLE\]\s*(?:A\s+modern|Modern\s+day|This\s+story|An\s+intimate|A\s+visually)[^|]{20,}\|\s*",
    flags=re.IGNORECASE,
)
PSYCHOLOGY_LOW_QUALITY_GENERIC_TERMS = re.compile(
    r"\b(beautiful scene|interesting moment|nice composition|subtle atmosphere|generic illustration|main subject of the scene|as described|visual moment|appropriate setting)\b",
    flags=re.IGNORECASE,
)
PSYCHOLOGY_META_INSTRUCTION_TERMS = re.compile(
    r"\b(theme context|depict this exact script idea|animate this exact script idea|"
    r"translate to english visual description|translate the idea into english visual action|"
    r"spend detail on the narration-grounded|spend detail on narration-specific|"
    r"literal scene action tied directly to the narration)\b",
    flags=re.IGNORECASE,
)


def _strip_psychology_meta_instruction_language(prompt: str) -> str:
    """Remove internal prompt-writing scaffolding from final model-facing prompts."""
    cleaned = str(prompt or "")
    replacements = [
        (r"\bTheme context\s*:\s*", ""),
        (r"\bDepict this exact script idea(?:\s+literally)?(?:\s+and\s+clearly)?\s*(?:\([^)]*\))?\s*:\s*", ""),
        (r"\bAnimate this exact script idea(?:\s+clearly)?\s*:\s*", ""),
        (r"\s*\(\s*translate(?: the idea)? into English [^)]*\)\s*", " "),
        (r"\btranslate(?: the idea)? into English visual (?:description|action)\s*:\s*", ""),
        (r"\bUse the provided reference image as the recurring character identity source;\s*", "Use the provided reference image as the character identity source; "),
        (r"\bSpend detail on the narration[- ](?:grounded|specific)[^.]*\.\s*", ""),
        (r"\bLiteral scene action tied directly to the narration, not vague atmosphere\.\s*", ""),
    ]
    for pattern, replacement in replacements:
        cleaned = re.sub(pattern, replacement, cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\s{2,}", " ", cleaned)
    cleaned = re.sub(r"\s+\|", " |", cleaned)
    cleaned = re.sub(r"\|\s+", "| ", cleaned)
    return cleaned.strip(" ,.;|")

def _sanitize_story_summary_style(prompt: str) -> str:
    if not prompt:
        return prompt
    cleaned = " ".join(str(prompt).split())
    cleaned = STORY_SUMMARY_STYLE_PREFIX.sub("[STYLE] ", cleaned)
    cleaned = re.sub(r"\[STYLE\]\s*\|\s*", "[STYLE] ", cleaned)
    return cleaned.strip()


def _sanitize_prohibited_image_terms(prompt: str) -> str:
    if not prompt:
        return prompt
    cleaned = " ".join(str(prompt).split())
    for pattern, replacement in MINOR_WORD_REPLACEMENTS:
        cleaned = re.sub(pattern, replacement, cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\bteenage child\b", "person", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\bteen child\b", "person", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\bteenage person\b", "adult person", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\bteen person\b", "adult person", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\bfamily of three extras:\s*a man,\s*a woman,\s*and a person\b", "three adult extras", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\ba three adult extras\b", "three adult extras", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\bfamily of three extras\b", "three adult extras", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\bfamily of four extras\b", "four adult extras", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\s{2,}", " ", cleaned).strip()
    return cleaned


def _parse_structured_prompt_blocks(prompt: str) -> Tuple[List[str], Dict[str, str]]:
    parts = [part.strip() for part in str(prompt or "").split("|") if part.strip()]
    order: List[str] = []
    blocks: Dict[str, str] = {}
    for part in parts:
        match = re.match(r"^\[(\w+)\]\s*(.*)$", part)
        if match:
            key = match.group(1).upper()
            blocks[key] = match.group(2).strip()
            order.append(key)
        else:
            key = f"RAW_{len(order)}"
            blocks[key] = part
            order.append(key)
    return order, blocks


def _assemble_structured_prompt(order: List[str], blocks: Dict[str, str]) -> str:
    rendered = []
    for key in order:
        value = " ".join(str(blocks.get(key, "")).split()).strip(" ,;|")
        if not value:
            continue
        if key.startswith("RAW_"):
            rendered.append(value)
        else:
            rendered.append(f"[{key}] {value}")
    return " | ".join(rendered).strip()


def _cleanup_prompt_fragment(text: str) -> str:
    cleaned = " ".join(str(text or "").split())
    cleaned = re.sub(r"\s+([,;:.])", r"\1", cleaned)
    cleaned = re.sub(r"([,;])\s*([,;])+", r"\1", cleaned)
    cleaned = re.sub(r"(,|;)\s*(\||$)", r"\2", cleaned)
    cleaned = re.sub(r"\bwith\s*(?:and)?\s*(?:,|;|$)", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\band\s*(?:,|;|$)", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\s{2,}", " ", cleaned)
    return cleaned.strip(" ,;.")


def _remove_variant_clauses(text: str, variants: List[str]) -> str:
    updated = str(text or "")
    for variant in variants:
        escaped = re.escape(variant)
        patterns = [
            rf"(?:,|;)\s*[^|.;]*\b{escaped}\b[^|.;]*",
            rf"\bwith\s+[^|.;]*\b{escaped}\b[^|.;]*",
            rf"\band\s+[^|.;]*\b{escaped}\b[^|.;]*",
            rf"[^|.;]*\b{escaped}\b[^|.;]*(?:,|;)?",
        ]
        for pattern in patterns:
            updated = re.sub(pattern, "", updated, flags=re.IGNORECASE)
    return _cleanup_prompt_fragment(updated)


def _make_grounded_action_fallback(
    primary_subject: str = "",
    primary_action: str = "",
    visual_anchor: str = "",
) -> str:
    subj = str(primary_subject or "").strip()
    act = str(primary_action or "").strip()
    anchor = str(visual_anchor or "").strip()
    if subj and act:
        return f"{subj} {act}"
    if act and anchor:
        return f"the frame centers on {anchor} as {act}"
    if anchor:
        return f"the frame holds on {anchor} as the beat settles"
    if subj:
        return f"{subj} holds in a quiet restrained beat"
    return "the frame holds on the grounded dramatic beat"


def _make_grounded_setting_fallback(
    visual_anchor: str = "",
    primary_subject: str = "",
) -> str:
    anchor = str(visual_anchor or "").strip()
    subj = str(primary_subject or "").strip()
    if anchor:
        return f"the setting is organized around {anchor} without invented extras"
    if subj:
        return f"the setting stays grounded around {subj} and the immediate space"
    return "the setting stays grounded in the immediate lived-in space"


def repair_unsupported_prompt_details(
    img_prompt: str,
    srt_text: str,
    primary_subject: str = "",
    primary_action: str = "",
    visual_anchor: str = "",
) -> str:
    prompt = str(img_prompt or "").strip()
    unsupported = check_unsupported_prompt_details(
        srt_text=srt_text,
        img_prompt=prompt,
        primary_subject=primary_subject,
        primary_action=primary_action,
        visual_anchor=visual_anchor,
    )
    if not unsupported:
        return prompt

    order, blocks = _parse_structured_prompt_blocks(prompt)
    target_keys = [key for key in order if key in {"SUBJECT", "ACTION", "SETTING"}]
    for label in unsupported:
        variants = UNSUPPORTED_DETAIL_VARIANTS.get(label, [])
        if not variants:
            continue
        for key in target_keys:
            blocks[key] = _remove_variant_clauses(blocks.get(key, ""), variants)

    if "ACTION" in blocks and len(blocks.get("ACTION", "").split()) < 6:
        blocks["ACTION"] = _make_grounded_action_fallback(
            primary_subject=primary_subject,
            primary_action=primary_action,
            visual_anchor=visual_anchor,
        )
    if "SETTING" in blocks and len(blocks.get("SETTING", "").split()) < 6:
        blocks["SETTING"] = _make_grounded_setting_fallback(
            visual_anchor=visual_anchor,
            primary_subject=primary_subject,
        )

    repaired = _assemble_structured_prompt(order, blocks)
    repaired = _sanitize_story_summary_style(repaired)
    repaired = _sanitize_prohibited_image_terms(repaired)
    return repaired


def _split_minor_characters(
    char_ids: List[str],
    minor_char_ids: Optional[Set[str]] = None,
) -> Tuple[List[str], List[str]]:
    char_ids = [cid for cid in char_ids if cid and cid != "[]"]
    minor_ids = [cid for cid in char_ids if cid in (minor_char_ids or set())]
    adult_ids = [cid for cid in char_ids if cid not in (minor_char_ids or set())]
    return adult_ids, minor_ids


def _sanitize_minor_safe_img_prompt(
    prompt: str,
    minor_image_refs: Optional[List[str]] = None,
) -> str:
    if not prompt:
        return prompt

    cleaned = " ".join(str(prompt).split())

    for img in (minor_image_refs or []):
        cleaned = cleaned.replace(f"({img})", "")
        cleaned = cleaned.replace(img, "")

    for pattern, replacement in MINOR_WORD_REPLACEMENTS:
        cleaned = re.sub(pattern, replacement, cleaned, flags=re.IGNORECASE)

    cleaned = re.sub(r"\bnv\d+\b", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\bnvc\.png\b|\bnvp\d+\.png\b", "", cleaned, flags=re.IGNORECASE)

    negative = (
        " No child visible, no young person visible, no juvenile face or body, "
        "focus on adult reaction, environment, props, and off-screen presence only."
    )
    if "no child visible" not in cleaned.lower():
        cleaned = cleaned.rstrip(". ") + "." + negative

    cleaned = re.sub(r"\s{2,}", " ", cleaned).strip()
    return cleaned


# ─────────────────────────────────────────────────────────────────────────────
# MAIN SYSTEM PROMPT — sent as system message to DeepSeek
# ─────────────────────────────────────────────────────────────────────────────

SYSTEM_PROMPT_SCENE_PROMPTS = """You are a SENIOR CINEMATOGRAPHER and VISUAL DIRECTOR specializing in creating prompts for AI image generation (Flux, Midjourney, Imagen).

YOUR MISSION: For each scene, write a highly detailed, cinematically rich image prompt AND a specific video motion prompt.

IMAGE PROMPT STRUCTURE (use ALL sections, separated by " | "):
[STYLE] Camera specs, shooting style, visual language
[SUBJECT] Who or what is in frame - detailed physical description + reference file annotation
[ACTION] Exact body language, posture, facial expression, gesture, movement
[SETTING] Location details, architecture, props, environment specifics + reference file annotation
[LIGHTING] Light source, quality, direction, shadows, atmosphere
[MOOD] Emotional tone, psychological feeling the image should evoke
[TECHNICAL] Quality keywords, camera model, color grade style

VIDEO PROMPT STRUCTURE:
CAMERA: [simple movement] | ACTION: [single visible action or visual beat]

VIDEO PROMPT RULES (CRITICAL):
- ACTION must be SPECIFIC to this exact narration — describe only one visible action or one simple visual beat
  GOOD: "she pauses with her hand on the door handle"
  GOOD: "the crooked paper letters hang above the decorated table"
  BAD: "clock ticking faintly" (audio detail — FORBIDDEN)
  BAD: "the silence in the room" (audio/abstract atmosphere — FORBIDDEN)
- CAMERA movement must match the EMOTIONAL BEAT:
  Revelation/surprise -> static or very slow push-in
  Loss/separation -> static or very slow pull-back
  Contemplation -> static
  Action/urgency -> gentle pan or locked medium shot
  Wide establishing -> static wide
- Keep the motion natural and minimal. Avoid shaky, horror-like, unstable, or overly stylized motion.

CRITICAL IMAGE RULES:
1. Every scene prompt MUST be UNIQUE - derive directly from the NARRATION text (the ⚠ PRIMARY field)
2. Use CINEMATIC vocabulary: "shallow DOF", "bokeh", "rim light", "chiaroscuro", "motivated lighting"
3. Character references: write as description then (nvc.png)
4. Location references: write as description then (loc1.png) and treat every loc*.png as an EMPTY ENVIRONMENT plate only, never as a source of people or character poses
5. NO generic phrases: "beautiful scene", "interesting moment", "subtle ambient atmosphere"
6. img_prompt: 80-150 words; video_prompt: 8-20 words
7. Ask: what SPECIFIC VISUAL best illustrates what the narrator is SAYING right now?
8. The VISUAL DIRECTION is a hint — the NARRATION is the truth. If they conflict, follow the NARRATION."""


# ─────────────────────────────────────────────────────────────────────────────
# BUILD STEP-7 PROMPT (gọi AI sinh img_prompt + video_prompt)
# ─────────────────────────────────────────────────────────────────────────────

SYSTEM_PROMPT_SCENE_PROMPTS = """You are a SENIOR CINEMATOGRAPHER and VISUAL DIRECTOR specializing in creating prompts for AI image generation (Flux, Midjourney, Imagen).

YOUR MISSION: For each scene, write a highly detailed, cinematically rich image prompt AND a restrained video motion prompt.

🚨 CRITICAL RULE #1 - NARRATION IS ABSOLUTE TRUTH:
The NARRATION field (marked with ⚠) contains the EXACT words being spoken in the video.
Your image MUST illustrate EXACTLY what is being narrated - not what would look artistic, not what the visual hint suggests, but EXACTLY what the narrator is saying RIGHT NOW.

EXAMPLES OF CORRECT NARRATION-FIRST THINKING:
❌ WRONG: Narration says "laundry keeps drying" → you show a refrigerator (because it's also an appliance)
✅ CORRECT: Narration says "laundry keeps drying" → you show clothes tumbling in a dryer or hanging wet laundry

❌ WRONG: Narration says "smell of chlorine from gym bag" → you show just a generic gym bag
✅ CORRECT: Narration says "smell of chlorine from gym bag" → you show damp gym bag with pool-related visual cues (wet strap, water droplets, swimming goggles edge visible)

❌ WRONG: Narration says "I looked up at him" → you show an artistic wide shot of the room
✅ CORRECT: Narration says "I looked up at him" → you show her face tilted upward looking at him, or his figure from her low POV

❌ WRONG: Narration says "the clock over the stove keeps marking off the minutes" → you show a refrigerator
✅ CORRECT: Narration says "the clock over the stove keeps marking off the minutes" → you show a clock mounted above a stove

🚨 CRITICAL RULE #2 - SPECIFIC OBJECTS MUST APPEAR:
If the narration mentions a SPECIFIC OBJECT (laundry, dryer, clock, stove, bag, phone, coat, table, chair, door, window, etc.), that EXACT object MUST be the primary subject or clearly visible in your prompt. Do not substitute with a different object from the same category.

IMAGE PROMPT STRUCTURE (use ALL sections, separated by " | "):
[STYLE] Camera specs, shooting style, visual language
[SUBJECT] Who or what is in frame - detailed physical description + reference file annotation
[ACTION] Exact body language, posture, facial expression, gesture, movement
[SETTING] Location details, architecture, props, environment specifics + reference file annotation
[LIGHTING] Light source, quality, direction, shadows, atmosphere
[MOOD] Emotional tone, psychological feeling the image should evoke
[TECHNICAL] Quality keywords, camera model, color grade style

VIDEO PROMPT STRUCTURE:
CAMERA: [one framing] | MOTION: [one simple movement] | ACTION: [one visible action or visual beat]

VIDEO PROMPT RULES (CRITICAL):
- The image keyframe is the primary visual truth. Never invent movement that the image cannot naturally support.
- The narration is the primary story truth. Use it to choose one beat, not to restage the whole sentence.
- CAMERA must be one of: detail close-up, close-up, medium close-up, medium shot, medium wide, wide shot
- MOTION must be one of: static frame, very slow push-in, very slow pull-back, gentle pan left, gentle pan right
- ACTION must describe only one visible action or one simple visual beat.
  GOOD: "she pauses with her hand on the door handle"
  GOOD: "the paper edge shifts under her hand"
  BAD: "clock ticking faintly" (audio detail - forbidden)
  BAD: "the silence in the room" (abstract atmosphere - forbidden)
- Prefer static frame when unsure.
- Keep motion natural and minimal. Avoid shaky, horror-like, unstable, compound, or overly stylized motion.
- Never mention duration, sound, smell, atmosphere, technical specs, names, asset IDs, or invisible emotional abstractions in video_prompt.

CRITICAL IMAGE RULES:
1. Every scene prompt MUST be UNIQUE - derive directly from the narration text.
2. Use CINEMATIC vocabulary: "shallow DOF", "bokeh", "rim light", "chiaroscuro", "motivated lighting"
3. Character references: write as description then (nvc.png)
4. Location references: write as description then (loc1.png) and treat every loc*.png as an EMPTY ENVIRONMENT plate only, never as a source of people or character poses
5. NO generic phrases: "beautiful scene", "interesting moment", "subtle ambient atmosphere"
6. img_prompt: 80-150 words; video_prompt: 10-24 words
7. Ask: what SPECIFIC OBJECT or ACTION from the narration should be the visual focus?
8. The VISUAL DIRECTION is artistic guidance ONLY - the NARRATION is the truth. If they conflict, ALWAYS follow the narration."""

SYSTEM_PROMPT_PSYCHOLOGY_SCENE_PROMPTS = """You are an EDUCATIONAL PSYCHOLOGY ILLUSTRATOR and VISUAL METAPHOR DIRECTOR for AI image generation.

YOUR MISSION: For each scene, write one clear, engaging psychology illustration prompt and one matching image-to-video prompt in the configured channel style.

CRITICAL RULE #1 - NARRATION IS ABSOLUTE TRUTH:
The NARRATION field contains the exact spoken idea. The image/video prompt must translate the idea into a clean English visual scene and make it instantly understandable to a viewer.

CRITICAL RULE #2 - VISUAL CONCEPT DRIVES THE PROMPT:
Each scene includes a VISUAL CONCEPT section with 5 unique fields extracted from the narration:
- visual_focus: The ONE thing the viewer's eye should land on first
- visual_metaphor: Visual equivalent of abstract concepts
- concrete_props: 2-3 specific props that MUST appear
- body_language_key: Defining body language or posture
- emotional_visual: How composition/lighting/color conveys emotion

BUILD YOUR PROMPT IN THIS ORDER:
1. START with the visual_focus (the unique focal point for THIS scene)
2. ADD concrete_props (the specific objects that make this scene distinct)
3. ADD body_language_key (the defining gesture/posture)
4. ADD emotional_visual (how to show emotion through visual technique)
5. ADD visual_metaphor if present (the symbolic element)
6. THEN add style essentials (channel style, palette, reference character)
7. END with technical guards (no text, no watermark)

DO NOT start with full style boilerplate. DO NOT make 80% of the prompt identical across scenes.

PSYCHOLOGY IMAGE PROMPT METHOD:
Use workbook planning as the foundation. Start with the VISUAL CONCEPT fields to make each prompt unique, then add channel style as supporting context. Do not output internal scaffolding phrases.
If a visual_contract is supplied, treat it as the mandatory bridge between non-English narration and the final English prompt. The final image/video must satisfy that contract before adding style or metaphor.

VIDEO PROMPT METHOD:
[channel video style sentence]. Describe one concrete visible movement and one emotional arc from the same keyframe. Use posture, hand tension, gaze direction, prop/light response, or a narration-relevant object. Do not use camera gear, duration, sound, smell, or invisible atmosphere.
LOCKED SCENE SPEC: When the scene includes primary_subject, primary_action, visual_anchor, visual_contract, scene_kind, or subject_mode, treat those fields as mandatory anchors. The video_prompt must animate that concrete beat, not a generic repeated channel prop.

LEGACY STRUCTURED BLOCKS ARE STILL ACCEPTED, but for psychology prefer clean final natural language because it produces stronger viewer-readable prompts.

VIDEO PROMPT RULES:
- CAMERA must be one of: detail close-up, close-up, medium close-up, medium shot, medium wide, wide shot
- MOTION must be one of: static frame, very slow push-in, very slow pull-back, gentle pan left, gentle pan right
- ACTION must describe only one visible beat that could naturally animate from the keyframe
- Never mention sound, smell, text, asset IDs, duration, invisible emotion, or technical camera gear

CRITICAL PSYCHOLOGY IMAGE RULES:
1. Every scene prompt must be unique and derived from the narration and VISUAL CONCEPT fields.
2. Use visual metaphors, relatable self-improvement situations, contrast panels as physical spaces, symbolic objects, and emotional body language.
3. Use only the provided reference image as the stable recurring character identity source. Refer to it briefly as the reference character; do not write long character descriptions or create a new character.
4. Other people must be anonymous silhouettes or simple background figures, never new named/reference characters.
5. No readable words, labels, captions, UI text, charts with text, documents with text, or watermarks.
6. Avoid camera-gear vocabulary; keep the style as clean editorial illustration.
7. img_prompt: 80-150 words; every prompt must include a [COMPOSITION] block with clear focal hierarchy and one memorable symbolic visual anchor.
8. Do not reuse the same prop or action across a batch unless the narration itself repeats it.
9. Ask: what single image would make the spoken psychology idea clear, emotionally relatable, and worth watching?"""

def build_scene_prompt_request(
    batch: List[Dict[str, Any]],
    context_lock: str,
    char_lookup: Dict[str, str],
    char_image_lookup: Dict[str, str],
    loc_lookup: Dict[str, str],
    loc_image_lookup: Dict[str, str],
    scene_planning: Dict[int, Dict],
    visual_style: str = "cinematic",
    min_dur: float = 5.0,
    max_dur: float = 8.0,
    minor_char_ids: Optional[Set[str]] = None,
    topic: str = "story",
    style_profile: Optional[Dict[str, Any]] = None,
) -> str:
    """
    Tạo user-prompt gửi cho DeepSeek (Step 7).
    System prompt được gửi riêng qua system field.
    
    Returns: user prompt string
    """
    profile = normalize_style_profile(style_profile)
    style_hint = _runtime_image_style(profile) if _is_psychology_topic(topic) else STYLE_PRESETS.get(visual_style, STYLE_PRESETS["default"])
    
    scenes_text = ""
    for scene in batch:
        scene_id = scene.get("scene_id")
        srt_text  = scene.get("srt_text", "")
        visual    = scene.get("visual_moment", "")
        camera    = scene.get("camera", "")
        lighting  = scene.get("lighting", "")
        duration  = scene.get("duration", 6.0)
        scene_kind = scene.get("scene_kind", "")
        subject_mode = scene.get("subject_mode", "")
        primary_subject = scene.get("primary_subject", "")
        primary_action = scene.get("primary_action", "")
        visual_anchor = scene.get("visual_anchor", "")
        must_not_show = scene.get("must_not_show", "")
        visual_contract = scene.get("visual_contract", "")
        alignment_notes = scene.get("alignment_notes", "")
        
        # Resolve characters
        char_ids = [c.strip() for c in scene.get("characters_used", "").split(",") if c.strip() and c.strip() != "[]"]
        adult_char_ids, minor_ids = _split_minor_characters(char_ids, minor_char_ids)
        char_parts = []
        char_refs  = []
        for cid in adult_char_ids:
            desc = char_lookup.get(cid, cid)
            img  = char_image_lookup.get(cid, f"{cid}.png")
            char_parts.append(f"{desc} → ref: ({img})")
            char_refs.append(img)
        
        # Resolve location
        loc_id   = str(scene.get("location_used") or "").strip()
        loc_desc = loc_lookup.get(loc_id, loc_id)
        loc_img  = loc_image_lookup.get(loc_id, f"{loc_id}.png") if loc_id else ""
        loc_str  = (
            f"{loc_desc} → ref: ({loc_img}) [environment only, empty plate, no people]"
            if loc_id else "Not specified"
        )
        
        # Artistic plan from step 6
        plan = scene_planning.get(scene_id, {})
        visual_focus = str(plan.get("visual_focus", "") or "").strip()
        visual_metaphor = str(plan.get("visual_metaphor", "") or "").strip()
        concrete_props_raw = plan.get("concrete_props", "")
        if isinstance(concrete_props_raw, list):
            concrete_props = ", ".join(str(item).strip() for item in concrete_props_raw if str(item).strip())
        else:
            concrete_props = str(concrete_props_raw or "").strip()
        body_language_key = str(plan.get("body_language_key", "") or "").strip()
        emotional_visual = str(plan.get("emotional_visual", "") or "").strip()

        plan_block = ""
        if plan:
            plan_block = f"""  ARTISTIC VISION (from director):
    Intent   : {plan.get("artistic_intent", "")}
    Sequence : {plan.get("sequence_id", scene.get("sequence_id", ""))} / {plan.get("sequence_role", scene.get("sequence_role", ""))}
    Function : {plan.get("shot_function", scene.get("shot_function", ""))}
    Beat type: {plan.get("beat_type", scene.get("beat_type", ""))}
    Emotion  : {plan.get("emotional_turn", scene.get("emotional_turn", ""))}
    Shot type: {plan.get("shot_type", camera or "Medium shot")}
    Action   : {plan.get("character_action", "")}
    Mood     : {plan.get("mood", "")}
    Lighting : {plan.get("lighting", lighting or "Natural")}
    Colors   : {plan.get("color_palette", "")}
    Focus    : {plan.get("key_focus", "")}
    Contract : {plan.get("visual_contract", visual_contract)}
    Alignment: {plan.get("alignment_notes", alignment_notes)}
    Attention: {plan.get("viewer_attention", "")}
    Subtext  : {plan.get("subtext_delivery", "")}
    Continuity: {plan.get("continuity_note", "")}
    Kind     : {plan.get("scene_kind", "")}
    Subject  : {plan.get("primary_subject", "")}
    Beat     : {plan.get("primary_action", "")}
    Mode     : {plan.get("subject_mode", "")}
  VISUAL CONCEPT (unique to this scene):
    Visual focus    : {visual_focus}
    Visual metaphor : {visual_metaphor}
    Concrete props  : {concrete_props}
    Body language   : {body_language_key}
    Emotional visual: {emotional_visual}"""

        minor_mode_block = ""
        if minor_ids:
            minor_mode_block = """  MINOR-SAFE MODE:
    This scene includes a child/minor in the narration.
    DO NOT show the child directly.
    DO NOT use any child reference image.
    Visualize the moment through one or more of:
    - adult reaction shot
    - empty environment after/before the action
    - symbolic props or traces of presence
    - framing where the child's presence is only implied off-screen
    Keep the emotional truth of the narration without depicting the minor."""
        
        scenes_text += f"""
━━━ SCENE {scene_id} ━━━ ({duration:.1f}s)
  ⚠ NARRATION (THIS IS THE TRUTH — your image MUST illustrate this): "{srt_text}"
  VISUAL HINT (use ONLY if it matches the narration): {visual}
  LOCKED SCENE SPEC:
    segment      : {scene.get("segment_id", "")}
    sequence     : {scene.get("sequence_id", "")}
    sequence_role: {scene.get("sequence_role", "")}
    shot_function: {scene.get("shot_function", "")}
    beat_type    : {scene.get("beat_type", "")}
    emotion_turn : {scene.get("emotional_turn", "")}
    from_prev    : {scene.get("continuity_from_prev", "")}
    to_next      : {scene.get("transition_to_next", "")}
    kind         : {scene_kind}
    subject_mode : {subject_mode}
    primary_subj : {primary_subject}
    primary_act  : {primary_action}
    anchor       : {visual_anchor}
    visual_contract: {visual_contract}
    alignment_notes: {alignment_notes}
    must_not_show: {must_not_show}
  CHARACTERS  : {chr(10).join('    ' + p for p in char_parts) if char_parts else '    None'}
  LOCATION    : {loc_str}
  REFERENCES  : {", ".join(char_refs + ([loc_img] if loc_img else []))}
{plan_block}
{minor_mode_block}
"""
    
    if _is_psychology_topic(topic):
        audience_language = str(profile.get('audience_language', '') or '').strip()
        audience_culture_note = str(profile.get('audience_culture_note', '') or '').strip()
        cultural_props = str(profile.get('cultural_props', '') or '').strip()
        cultural_metaphors = str(profile.get('cultural_metaphors', '') or '').strip()
        cultural_emotion_style = str(profile.get('cultural_emotion_style', '') or '').strip()
        audience_block = ""
        if audience_language:
            props_line = f"\nOPTIONAL PROP EXAMPLES (use only when the narration calls for this kind of object): {cultural_props}" if cultural_props else ""
            metaphors_line = f"\nCULTURAL METAPHOR BANK (optional examples, use only when the current narration matches the concept):\n{cultural_metaphors}" if cultural_metaphors else ""
            emotion_line = f"\nEMOTION STYLE: {cultural_emotion_style}" if cultural_emotion_style else ""
            audience_block = f"""AUDIENCE LANGUAGE: {audience_language}
CULTURAL CONTEXT: {audience_culture_note or 'Universal'}{props_line}{metaphors_line}{emotion_line}
AUDIENCE FIT GUIDANCE: The narration decides the image. If the narration naturally involves a setting, object, or ritual, prefer one that feels familiar to {audience_language} audiences. Do NOT force cultural props into every scene.
"""
        tc = _get_topic_config(topic)
        _topic_label_user = tc.get("prompt_label", "psychology illustration").split(" ")[0]
        user_prompt = f"""Generate {_topic_label_user} illustration prompts for {len(batch)} scene(s).

GLOBAL CHANNEL STYLE: {_strip_attached_character_description(context_lock)}
STYLE ESSENTIALS (short, support only): {_extract_style_essentials(profile, topic=topic)}
VIDEO STYLE: {_runtime_video_style(profile)}
PALETTE: {profile['palette']}
NEGATIVE RULES: {profile['negative_prompt']}
CHARACTER REFERENCE: the provided reference image defines the recurring character identity/style. Preserve the reference character exactly; do not re-describe it in detail or create a new character. Spend detail on the narration-specific setting, action, body language, and metaphor.
{audience_block}TARGET DURATION per scene: {min_dur:.0f}–{max_dur:.0f} seconds

━━━━━━━━━━━ SCENES ━━━━━━━━━━━
{scenes_text}
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

OUTPUT FORMAT (JSON only, no explanation):
{{
    "scenes": [
        {{
            "scene_id": <int>,
            "img_prompt": "[clean final image prompt: 90-150 words, English only, start with this scene's unique visual focus, style appears only after the unique visual concept]",
            "video_prompt": "{_runtime_video_style(profile)} [clean final image-to-video prompt: 45-90 words, English only, one visible movement and emotional arc]"
        }}
    ]
}}

{tc['topic_label']} RULES:
- PRIMARY SOURCE is the NARRATION (which may be in any language). Translate the narration idea into English for your img_prompt and video_prompt output. ALL OUTPUT MUST BE IN ENGLISH.
- Build each img_prompt in this priority order: VISUAL CONCEPT visual_focus, concrete_props, body_language_key, emotional_visual, visual_metaphor, then visual_contract, primary_subj, primary_act, and style essentials.
- Do not begin img_prompt with GLOBAL CHANNEL STYLE or a long repeated style sentence. Style should be one short clause after the scene-specific visual concept.
- This is a HYBRID rewrite: the workbook planning data is the foundation. Do not rethink the scene from scratch.
- VISUAL CONTRACT is mandatory when present. The image and video must satisfy those concrete obligations before adding style, metaphor, or audience flavor.
- If visual_contract conflicts with any repeated motif, cultural prop, or generic symbol, obey visual_contract and the NARRATION.
- If the locked anchor is generic or conflicts with Focus/key_focus, prefer VISUAL CONCEPT visual_focus and the NARRATION.
- Do NOT output old internal scaffolding labels, translation instructions, or prompt-writing instructions. Write only the final image/video prompt text.
- Use the provided reference image as the identity/style anchor whenever the recurring character appears. Preserve the reference character exactly; do not describe a new character design and do not spend tokens repeating the character's appearance.
- Use one clean key visual per scene: one primary subject, one primary action, one instantly readable metaphor or relatable everyday moment.
- Use audience-cultural context only as background taste. Do not add a cultural prop unless it directly supports the current SRT line.
- Design for a 10/10 viewer experience: the viewer should understand the narration from the image alone within one second.
- Prefer concrete real-life situations over vague symbols when the line describes an action, object, room, phone, doorway, social situation, isolation, peace, boundary, or nervous-system state.
- Use same-style secondary figures only as anonymous silhouettes/simple background figures. Never invent new character IDs.
- ABSOLUTELY NO text, letters, words, writing, captions, labels, UI text, chart text, document text, signs, numbers, or watermarks in the image. If narration mentions reading/writing, show the object (book, phone) but with blank/abstract content, never readable text.
- Obey NEGATIVE RULES exactly. Avoid camera-gear vocabulary and cinematic language unless the channel style explicitly asks for it.
- video_prompt must be natural language in this channel style, not CAMERA/MOTION/ACTION blocks.
- video_prompt must include one concrete visible action using the narration's object, action, metaphor, or emotional body language.
- video_prompt must include a clear emotional arc through posture, tiny pause, hand tension, shoulder movement, gaze direction, and prop/light response where relevant.
- Never use the generic sentence "Use specific movement by the character" as the final video prompt.
- If narration is abstract, convert it into one visible movement of nv1 plus one narration-relevant object, boundary, silhouette, or environment response.
- video_prompt must animate only visible movement tied to the narration; no sound, smell, invisible atmosphere, technical camera gear, asset IDs, duration, or reference filenames.
- LOCKED SCENE SPEC is guidance, but NARRATION is absolute truth.
- Return EXACTLY {len(batch)} scene objects.
"""
        return user_prompt

    user_prompt = f"""Generate detailed cinematic prompts for {len(batch)} scene(s).

GLOBAL VISUAL STYLE: {context_lock}
SHOOTING STYLE: {style_hint}
TARGET DURATION per scene: {min_dur:.0f}–{max_dur:.0f} seconds

━━━━━━━━━━━ SCENES ━━━━━━━━━━━
{scenes_text}
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

OUTPUT FORMAT (JSON only, no explanation):
{{
    "scenes": [
        {{
            "scene_id": <int>,
            "img_prompt": "[STYLE] ... | [SUBJECT] ... (nvc.png) | [ACTION] ... | [SETTING] ... (loc1.png) | [LIGHTING] ... | [MOOD] ... | [TECHNICAL] photorealistic, 8K, ARRI Alexa, cinematic color grade",
            "video_prompt": "CAMERA: <framing> | MOTION: <simple movement> | ACTION: <single visible action or visual beat>"
        }}
    ]
}}

REMINDER:
- img_prompt: 80-150 words, structured with | separators, MUST include (ref.png) annotations
- video_prompt: short, simple, visual only
- Do NOT mention duration
- Do NOT mention sound, silence, smell, tension, atmosphere, or other invisible sensory effects
- Do NOT mention names, asset IDs, or reference image filenames
- Use exactly one framing, one motion, and one visible action
- Do NOT invent actions that are not already plausible from the image and narration
- If the scene is object_detail or environment_story, the object/space must NOT appear to move by itself.
- For object_detail or environment_story, prefer camera-only motion and describe the object/space as still in frame.
- Do NOT invent concrete props, documents, cards, worksheets, articles, folders, screens, or devices unless they are explicitly grounded in the narration or locked scene spec
- If the narration is mostly conversational or internal, do not fabricate paperwork/evidence props just to make the frame feel specific
- If several consecutive scenes share the same room and conversation, vary the frame by the true narrative beat, not by adding made-up objects
- Respect sequence_role and shot_function so adjacent frames feel like a designed shot progression, not repeated coverage
- Use continuity_from_prev / transition_to_next / continuity_note to preserve spatial and emotional flow
- Prefer static camera when unsure
- SINGLE-FRAME RULE: one scene = one shot = one primary subject = one primary action
- NEVER write a sequence, montage, collage, split-screen, or numbered sub-shots
- NEVER use overlay language such as composite, layered, translucent, ghostly, superimposed, simultaneous actions, or flashback insert
- LOCKED SCENE SPEC is mandatory: follow scene_kind, subject_mode, primary_subject, primary_action, visual_anchor, and must_not_show
- If scene_kind is object_detail or subject_mode is object, make the object/prop the true subject and do not force a character face into frame
- If scene_kind is environment_story or subject_mode is environment, let the space tell the beat and do not force a character into frame
- If scene_kind is interaction or subject_mode is pair, keep the visible human subjects to two people maximum
- primary_subject and primary_action from the director plan are hard anchors and should override generic instincts
- PRIMARY SOURCE is the NARRATION ⚠ — the image MUST illustrate what is being narrated
- VISUAL DIRECTION is artistic guidance only — never override the story content
- Every scene must be UNIQUE — reflect the EXACT narration words
- If a scene is marked MINOR-SAFE MODE, do not depict the child directly; use adult reaction, props, environment, or off-screen implication instead
- Return EXACTLY {len(batch)} scene objects
"""
    return user_prompt


def _prompt_visualizes_srt_core(prompt: str, srt_text: str, primary_subject: str = "", primary_action: str = "", visual_anchor: str = "") -> bool:
    if not srt_text or not prompt:
        return True
    import unicodedata
    def norm(text):
        text = unicodedata.normalize("NFKD", str(text or "").lower())
        text = "".join(ch for ch in text if not unicodedata.combining(ch))
        return " ".join(re.findall(r"\b\w{3,}\b", text))
    srt_norm = norm(srt_text)
    prompt_norm = norm(prompt)
    srt_words = [w for w in srt_norm.split() if len(w) >= 4]
    if not srt_words:
        return True
    hits = sum(1 for w in srt_words if w in prompt_norm)
    if hits >= max(2, len(srt_words) // 3):
        return True
    if primary_subject and norm(primary_subject) in prompt_norm:
        return True
    if primary_action and norm(primary_action) in prompt_norm:
        return True
    if visual_anchor and norm(visual_anchor) in prompt_norm:
        return True
    return False


def check_psychology_prompt_quality(
    img_prompt: str,
    srt_text: str = "",
    char_ids: Optional[List[str]] = None,
    style_profile: Optional[Dict[str, Any]] = None,
    primary_subject: str = "",
    primary_action: str = "",
    visual_anchor: str = "",
) -> List[str]:
    issues: List[str] = []
    prompt = str(img_prompt or "")
    low = prompt.lower()
    if not prompt.strip():
        return ["empty prompt"]
    profile = normalize_style_profile(style_profile)
    style_terms = _profile_style_terms(style_profile)
    _cq_style_markers = ["hand-drawn", "channel style", "provided reference character"]
    for _tc_val in TOPIC_PROMPT_CONFIG.values():
        _pl = _tc_val.get("prompt_label", "")
        if _pl:
            _cq_style_markers.extend([f"2d {_pl.split()[0].lower()}", f"{_pl.split()[0].lower()} editorial", _pl])
    has_style_language = any(term in low for term in _cq_style_markers)
    if style_terms and not any(term in low for term in style_terms[:6]) and not has_style_language:
        issues.append("missing channel style profile terms")
    forbidden = [term for term in PSYCHOLOGY_FORBIDDEN_STYLE_TERMS if term.lower() in low]
    if forbidden:
        issues.append("forbidden style terms: " + ", ".join(forbidden[:5]))
    if PSYCHOLOGY_META_INSTRUCTION_TERMS.search(prompt):
        issues.append("contains internal meta instruction language")
    text_guard_phrases = ["no readable text", "no text", "no letters", "no messy text", "without text", "avoid text"]
    profile_text_guards = [
        phrase for phrase in text_guard_phrases
        if phrase in profile.get("negative_prompt", "").lower()
    ]
    accepted_text_guards = text_guard_phrases + profile_text_guards
    if not any(phrase in low for phrase in accepted_text_guards):
        issues.append("missing no-readable-text guard")
    if not _has_quality_anchor(prompt):
        issues.append("missing visual quality anchor")
    if not any(term in low for term in ["clear focal", "focal hierarchy", "focal point", "foreground", "center", "viewer", "eye is drawn", "first notices"]):
        issues.append("missing focal hierarchy")
    if not _prompt_has_audience_cultural_fit(prompt, profile):
        language = str(profile.get("audience_language", "") or "").strip()
        if language:
            pass
    core_source = " ".join([primary_subject, primary_action, visual_anchor])
    core_tokens = [
        token for token in re.findall(r"\b[a-zA-Z][a-zA-Z-]{3,}\b", core_source.lower())
        if token not in {"with", "that", "this", "from", "into", "through", "character", "reference"}
    ]
    core_token_hits = sum(1 for token in dict.fromkeys(core_tokens) if token in low)
    visual_core_ok = (
        _prompt_visualizes_srt_core(prompt, srt_text, primary_subject, primary_action, visual_anchor)
        or core_token_hits >= 3
    )
    if not visual_core_ok:
        issues.append("missing SRT visual core")
    if "nv1" in (char_ids or []) and "provided reference image" not in low and "reference character" not in low:
        issues.append("missing nv1 reference lock")
    if re.search(r"\bnv(?!1\b)\d+\b|\bnv_[\w-]+\b", prompt, flags=re.IGNORECASE):
        issues.append("invented non-nv1 character reference")
    if srt_text:
        concepts = _extract_psychology_concept_keywords(srt_text)
        if concepts:
            concept_hits = [concept for concept in concepts if _concept_supported_by_prompt(concept, prompt)]
            if not concept_hits:
                issues.append("missing narration concept: " + ", ".join(concepts[:3]))
        if not visual_core_ok:
            ok, missing = check_narration_keywords_in_prompt(srt_text, prompt)
            if not ok and missing:
                issues.append("missing narration keywords: " + ", ".join(missing[:4]))
    return issues


# ─────────────────────────────────────────────────────────────────────────────
# POST-PROCESS: chuẩn hóa và enrichs prompt sau khi AI trả về
# ─────────────────────────────────────────────────────────────────────────────

def postprocess_img_prompt(
    prompt: str,
    char_ids: List[str],
    loc_id: str,
    char_image_lookup: Dict[str, str],
    loc_image_lookup: Dict[str, str],
    context_lock: str = "",
    minor_mode: bool = False,
    minor_image_refs: Optional[List[str]] = None,
    srt_text: str = "",  # NEW: for keyword validation
    primary_subject: str = "",
    primary_action: str = "",
    visual_anchor: str = "",
    topic: str = "story",
    style_profile: Optional[Dict[str, Any]] = None,
) -> str:
    """
    Làm sạch và đảm bảo reference annotations có trong prompt.
    - Inject (nvc.png) nếu thiếu
    - Inject (loc1.png) nếu thiếu
    - Đảm bảo TECHNICAL suffix có
    - Kiểm tra narration keywords (NEW)
    """
    if not prompt:
        if _is_psychology_topic(topic):
            anchor = visual_anchor or primary_action or primary_subject or srt_text[:140] or "the central narration beat"
            subject = primary_subject or anchor
            action = primary_action or anchor
            prompt = (
                f"{_runtime_image_style(style_profile).rstrip('. ')}. "
                f"Show {subject}. {action}. "
                f"Clear focal hierarchy: viewer first notices {anchor}. "
                "Make the emotional meaning readable through simple body language, one concrete symbolic object, and clean negative space. "
                "No readable text, no letters, no captions, no labels, no watermark."
            )
        else:
            return prompt

    prompt = _sanitize_story_summary_style(prompt)
    prompt = _sanitize_prohibited_image_terms(prompt)
    if _is_psychology_topic(topic):
        profile = normalize_style_profile(style_profile)
        prompt = _strip_psychology_meta_instruction_language(prompt)
        prompt = _strip_psychology_forbidden_style(prompt)
        prompt = _strip_forced_psychology_cultural_props(prompt)
        prompt = _collapse_style_blocks_only(prompt, profile)
        primary_subject = _strip_forced_psychology_cultural_props(primary_subject)
        primary_action = _strip_forced_psychology_cultural_props(primary_action)
        visual_anchor = _strip_forced_psychology_cultural_props(visual_anchor)
        if "nv1" in char_ids:
            prompt = _strip_attached_character_design_from_prompt(prompt, profile)
            prompt = _collapse_style_blocks_only(prompt, profile)
    prompt = re.sub(
        r"\(([^()]*?\.png(?:\s*,\s*[^()]*?\.png)+)\)",
        lambda m: " ".join(
            f"({part.strip()})" for part in m.group(1).split(",") if part.strip()
        ),
        prompt,
    )

    # ALWAYS remove generic nvc.png (AI sometimes generates it even when we have specific IDs)
    prompt = prompt.replace("(nvc.png)", "")
    prompt = re.sub(r"\bnvc\.png\b", "", prompt, flags=re.IGNORECASE)

    # Remove malformed location references
    prompt = re.sub(r"\(loc[^)]*,[^)]*\.png\)", "", prompt, flags=re.IGNORECASE)
    prompt = re.sub(r"\bloc_loc[\w, ]*\.png\b", "", prompt, flags=re.IGNORECASE)

    # Remove bare IDs without .png extension. For psychology, keep nv1 semantic
    # reference language intact so style text does not become "same style as ,".
    if _is_psychology_topic(topic):
        prompt = re.sub(r"\bnv(?!1\b)\d+\b(?!\s*\.png)", "", prompt, flags=re.IGNORECASE)
        prompt = re.sub(r"\bnv1\b", "the reference character", prompt, flags=re.IGNORECASE)
    else:
        prompt = re.sub(r"\bnv\d+\b(?!\s*\.png)", "", prompt, flags=re.IGNORECASE)
    prompt = re.sub(r"\bloc\d+\b(?!\s*\.png)", "", prompt, flags=re.IGNORECASE)

    if prompt_needs_single_frame_fallback(prompt):
        if _is_psychology_topic(topic):
            anchor = visual_anchor or primary_action or primary_subject or srt_text[:140] or "the central narration beat"
            subject = primary_subject or anchor
            action = primary_action or anchor
            prompt = (
                f"{_runtime_image_style(style_profile).rstrip('. ')}. "
                f"Show {subject}. {action}. "
                f"Clear focal hierarchy: viewer first notices {anchor}. "
                "Make the emotional meaning readable through simple body language, one concrete symbolic object, and clean negative space. "
                "No readable text, no letters, no captions, no labels, no watermark."
            )
        else:
            return ""

    # NEW: Check narration keyword matching
    if srt_text:
        is_valid, missing = check_narration_keywords_in_prompt(srt_text, prompt)
        if not is_valid and missing:
            # Log warning for debugging (will be caught by QA validation)
            import sys
            print(f"⚠️  [KEYWORD CHECK] Prompt missing SRT keywords: {missing}", file=sys.stderr)

        prompt = repair_unsupported_prompt_details(
            img_prompt=prompt,
            srt_text=srt_text,
            primary_subject=primary_subject,
            primary_action=primary_action,
            visual_anchor=visual_anchor,
        )
        unsupported = check_unsupported_prompt_details(
            srt_text=srt_text,
            img_prompt=prompt,
            primary_subject=primary_subject,
            primary_action=primary_action,
            visual_anchor=visual_anchor,
        )
        if unsupported:
            import sys
            print(f"[DETAIL CHECK] Prompt invented unsupported details: {unsupported}", file=sys.stderr)
            if _is_psychology_topic(topic):
                anchor = visual_anchor or primary_action or primary_subject or srt_text[:140] or "the central narration beat"
                subject = primary_subject or anchor
                action = primary_action or anchor
                prompt = (
                    f"{_runtime_image_style(style_profile).rstrip('. ')}. "
                    f"Show {subject}. {action}. "
                    f"Clear focal hierarchy: viewer first notices {anchor}. "
                    "Use only the narration and locked scene spec; avoid unsupported paperwork, screens, labels, and readable details. "
                    "No readable text, no letters, no captions, no labels, no watermark."
                )
            else:
                return ""

    # Đảm bảo reference annotations
    if not _is_psychology_topic(topic):
        for cid in char_ids:
            img = char_image_lookup.get(cid, f"{cid}.png")
            if img and f"({img})" not in prompt:
                prompt = prompt.rstrip(". ") + f" ({img})."

    if loc_id:
        loc_img = loc_image_lookup.get(loc_id, f"{loc_id}.png")
        if loc_img and f"({loc_img})" not in prompt:
            prompt = prompt.rstrip(". ") + f" ({loc_img})."
    
    # Đảm bảo có technical suffix keywords
    if _is_psychology_topic(topic):
        prompt = _ensure_psychology_prompt_quality(
            prompt,
            char_ids=char_ids,
            srt_text=srt_text,
            primary_subject=primary_subject,
            primary_action=primary_action,
            visual_anchor=visual_anchor,
            style_profile=style_profile,
        )
    else:
        tech_keywords = ["8K", "photorealistic", "cinematic"]
        if not any(kw.lower() in prompt.lower() for kw in tech_keywords):
            prompt = prompt.rstrip(". ") + " | [TECHNICAL] photorealistic, 8K, cinematic color grade."

    if _is_psychology_topic(topic):
        quality_issues = check_psychology_prompt_quality(
            prompt,
            srt_text=srt_text,
            char_ids=char_ids,
            style_profile=style_profile,
            primary_subject=primary_subject,
            primary_action=primary_action,
            visual_anchor=visual_anchor,
        )
        if quality_issues:
            prompt = _ensure_psychology_prompt_quality(
                prompt,
                char_ids=char_ids,
                srt_text=srt_text,
                primary_subject=primary_subject,
                primary_action=primary_action,
                visual_anchor=visual_anchor,
                style_profile=style_profile,
            )

    if minor_mode:
        prompt = _sanitize_minor_safe_img_prompt(prompt, minor_image_refs=minor_image_refs)
        if MINOR_VISIBLE_TERMS.search(prompt):
            return ""
    else:
        prompt = _sanitize_prohibited_image_terms(prompt)

    if _is_psychology_topic(topic):
        prompt = _normalize_psychology_reference_language(prompt, "nv1" in char_ids or "nv1.png" in str(prompt).lower())
        prompt = _strip_psychology_meta_instruction_language(prompt)
        # CRITICAL: Enforce visual-first structure (fix API instability)
        prompt = _enforce_visual_first_prompt_structure(prompt)

    return prompt.strip()


def check_narration_keywords_in_prompt(srt_text: str, img_prompt: str) -> Tuple[bool, List[str]]:
    """
    Kiểm tra xem các từ khóa quan trọng trong SRT có xuất hiện trong prompt không.

    Returns:
        (is_valid, missing_keywords)
    """
    if not srt_text or not img_prompt:
        return True, []

    import unicodedata

    # Remove common words
    stop_words = {'the', 'a', 'an', 'and', 'or', 'but', 'in', 'on', 'at', 'to', 'for',
                  'of', 'with', 'by', 'from', 'as', 'is', 'was', 'are', 'were', 'been',
                  'be', 'have', 'has', 'had', 'do', 'does', 'did', 'will', 'would',
                  'could', 'should', 'may', 'might', 'must', 'can', 'that', 'this',
                  'it', 'he', 'she', 'they', 'i', 'you', 'we', 'my', 'your', 'his',
                  'her', 'their', 'our', 'me', 'him', 'them', 'us', 'what', 'which',
                  'who', 'when', 'where', 'why', 'how', 'all', 'each', 'every', 'both',
                  'few', 'more', 'most', 'other', 'some', 'such', 'no', 'nor', 'not',
                  'only', 'own', 'same', 'so', 'than', 'too', 'very', 'just', 'now',
                  'about', 'something', 'anything', 'everything', 'nothing', 'know'}

    # Detect if SRT is predominantly Vietnamese/non-ASCII
    non_ascii_chars = sum(1 for ch in srt_text if ord(ch) > 127)
    is_non_ascii_content = non_ascii_chars > len(srt_text) * 0.1

    if is_non_ascii_content:
        # For Vietnamese/non-ASCII SRT: normalize to non-accented form and extract content tokens
        # The img_prompt is always in English, so we check concept anchors appear, not literal words.
        # Strategy: NFKD-normalize SRT, extract tokens ≥4 chars that are pure latin after stripping
        # combining chars. These are the non-accented Vietnamese root words. Then check if any
        # appear in the NFKD-normalized prompt. If none extractable, return True (no false alarm).
        norm_srt = unicodedata.normalize("NFKD", srt_text.lower())
        norm_srt = "".join(ch for ch in norm_srt if not unicodedata.combining(ch))
        vn_tokens = re.findall(r'\b[a-z]{4,}\b', norm_srt)
        vn_stop = {'khong', 'trong', 'cung', 'minh', 'duoc', 'nhung', 'cac', 'khi', 'neu',
                   'nhieu', 'theo', 'dang', 'hoac', 'viec', 'nhin', 'nghe', 'ngay', 'gia',
                   'chung', 'them', 'sau', 'nhat', 'that', 'muon', 'cuoc', 'that', 'toan',
                   'phai', 'luon', 'chia', 'rang', 'thanh', 'dieu', 'tham', 'mang', 'xuat',
                   'tren', 'dudi', 'giua', 'qua', 'vay', 'nay', 'day', 'kia', 'con', 'hay',
                   'biet', 'thay', 'nghi', 'the', 'the', 'nao', 'lam', 'ban', 'quen', 'nen'}
        srt_keywords = [t for t in vn_tokens if t not in vn_stop and t not in stop_words]
    else:
        # Extract words from SRT (lowercase, alphanumeric only, min 4 chars)
        srt_words = re.findall(r'\b[a-z]{4,}\b', srt_text.lower())
        srt_keywords = [w for w in srt_words if w not in stop_words]

    # Count unique keywords
    from collections import Counter
    keyword_freq = Counter(srt_keywords)

    if is_non_ascii_content:
        # For Vietnamese SRT: prompt is in English so literal Vietnamese words won't match.
        # Instead just verify the prompt has meaningful content (not all boilerplate).
        # The concept-level alignment is enforced by check_psychology_prompt_quality separately.
        # We check that the NFKD-normalized root tokens (≥4 chars, repeated 2+ times) that
        # also appear verbatim in the NFKD-normalized prompt are not ALL missing.
        norm_prompt = unicodedata.normalize("NFKD", img_prompt.lower())
        norm_prompt = "".join(ch for ch in norm_prompt if not unicodedata.combining(ch))
        repeated = [w for w, freq in keyword_freq.items() if freq >= 2]
        if not repeated:
            return True, []
        found_in_prompt = [w for w in repeated if w in norm_prompt]
        if found_in_prompt or len(repeated) <= 1:
            return True, []
        # Some repeated root tokens exist but none appear — report top missing ones
        missing = [w for w in repeated[:4] if w not in norm_prompt]
        is_valid = len(missing) == 0
        return is_valid, missing

    # Specific objects that MUST appear if mentioned (English story content)
    critical_objects = {
        'laundry', 'dryer', 'washer', 'washing', 'clothes', 'refrigerator', 'fridge',
        'clock', 'stove', 'oven', 'table', 'chair', 'desk', 'door', 'window', 'coat',
        'jacket', 'bag', 'backpack', 'phone', 'cell', 'mobile', 'text', 'message',
        'chlorine', 'pool', 'swimming', 'gym', 'kitchen', 'bedroom', 'bathroom',
        'living', 'room', 'hallway', 'stairs', 'car', 'vehicle', 'street', 'road',
        'book', 'paper', 'document', 'letter', 'note', 'pen', 'pencil', 'computer',
        'laptop', 'screen', 'monitor', 'television', 'tv', 'remote', 'keys', 'wallet',
        'purse', 'glass', 'cup', 'mug', 'plate', 'bowl', 'fork', 'knife', 'spoon'
    }

    # Important keywords = critical objects OR mentioned 2+ times
    important_keywords = []
    for word, freq in keyword_freq.items():
        if word in critical_objects or freq >= 2:
            important_keywords.append(word)

    # Check if important keywords appear in prompt
    prompt_lower = img_prompt.lower()
    missing = []

    # Synonym mapping for flexible matching
    synonyms = {
        'laundry': ['clothes', 'washing', 'dryer', 'washer', 'garment', 'fabric', 'linen'],
        'chlorine': ['pool', 'swimming', 'swim', 'aquatic', 'water'],
        'call': ['phone', 'calling', 'called', 'telephone', 'dial'],
        'text': ['message', 'texting', 'texted', 'sms'],
        'refrigerator': ['fridge', 'appliance', 'cooler'],
        'stove': ['oven', 'range', 'cooktop', 'burner'],
        'coat': ['jacket', 'outerwear'],
        'bag': ['backpack', 'satchel', 'tote', 'purse'],
        'clock': ['time', 'watch', 'timepiece'],
        'dryer': ['laundry', 'clothes', 'tumble', 'drum'],
    }

    for kw in important_keywords[:6]:  # Check top 6 most important
        if kw not in prompt_lower:
            # Check for synonyms
            found_synonym = False
            if kw in synonyms:
                for syn in synonyms[kw]:
                    if syn in prompt_lower:
                        found_synonym = True
                        break

            if not found_synonym:
                missing.append(kw)

    is_valid = len(missing) == 0
    return is_valid, missing


def check_unsupported_prompt_details(
    srt_text: str,
    img_prompt: str,
    primary_subject: str = "",
    primary_action: str = "",
    visual_anchor: str = "",
) -> List[str]:
    """Flag concrete fabricated details not grounded in narration or scene spec."""
    if not srt_text or not img_prompt:
        return []

    source = " ".join([
        str(srt_text or ""),
        str(primary_subject or ""),
        str(primary_action or ""),
        str(visual_anchor or ""),
    ]).lower()
    prompt_lower = str(img_prompt or "").lower()

    unsupported: List[str] = []
    for canonical, variants in UNSUPPORTED_DETAIL_VARIANTS.items():
        if any(v in prompt_lower for v in variants):
            if not any(v in source for v in variants):
                unsupported.append(canonical)

    return unsupported


def prompt_needs_single_frame_fallback(prompt: str) -> bool:
    if not prompt:
        return True
    compact = " ".join(str(prompt).split())
    if MONTAGE_TERMS.search(compact):
        return True
    if re.search(r"\b(left frame|right frame|first close-up|second close-up|third close-up|foreground.*background.*overlay)\b", compact, flags=re.IGNORECASE):
        return True
    if compact.count("[STYLE]") > 1 or compact.count("[SUBJECT]") > 1:
        return True
    return False


def _sanitize_video_action_text(text: str) -> str:
    if not text:
        return "the subject holds still"

    cleaned = " ".join(str(text).split())
    source_low = cleaned.lower()
    if "second hand" in source_low and "watch" in source_low:
        return "the watch second hand sweeps smoothly around the dial"
    if "watch" in source_low and any(k in source_low for k in ["ran", "run", "glide", "sweep", "moved", "continuous"]):
        return "the watch hand glides in one smooth continuous sweep"
    cleaned = re.sub(r"\[[^\]]+\]", "", cleaned)
    cleaned = VIDEO_NAME_OR_ASSET_TERMS.sub("", cleaned)
    cleaned = VIDEO_AUDIO_TERMS.sub("", cleaned)
    cleaned = VIDEO_ABSTRACT_TERMS.sub("", cleaned)
    cleaned = VIDEO_UNSAFE_MOTION_TERMS.sub("", cleaned)
    cleaned = re.sub(r"\b(?:seconds?|secs?|duration)\b.*$", "", cleaned, flags=re.IGNORECASE)

    for sep in [".", ";", ":", " - ", " -- ", " — ", ", then ", " and then "]:
        if sep in cleaned:
            cleaned = cleaned.split(sep)[0].strip()
            break

    cleaned = re.sub(
        r"^(?:[A-Z][a-z]+)\s+(?=(?:looks?|lowers?|raises?|pauses?|holds?|stands?|sits?|walks?|steps?|turns?|opens?|closes?|sets?|puts?|takes?|leans?|stares?|remains?|waits?|smiles?|cries?|eats?))",
        "",
        cleaned,
    )
    cleaned = cleaned.lower()
    cleaned = re.sub(r"\s{2,}", " ", cleaned).strip(" ,.|")
    if len(cleaned.split()) > 12:
        cleaned = " ".join(cleaned.split()[:12]).strip(" ,.|")

    if not cleaned or len(cleaned.split()) < 3 or cleaned in {"the", "a", "an", "an object", "object detail shot"}:
        return "the subject holds still"
    return cleaned


def _has_human_presence_in_video_beat(*parts: str) -> bool:
    source = " ".join(str(part or "") for part in parts).lower()
    human_terms = [
        "he ", "she ", "his ", "her ", "wife", "husband", "megan", "narrator",
        "man ", "woman ", "couple", "both", "their ", "hand", "hands", "fingers",
        "jaw", "shoulders", "gaze", "face", "eyes",
    ]
    return any(term in source for term in human_terms)


def _has_human_mediated_object_action(*parts: str) -> bool:
    source = " ".join(str(part or "") for part in parts).lower()
    if not any(term in source for term in ["fork", "plate", "pasta", "laptop", "phone", "table"]):
        return False
    human_action_terms = [
        "eat", "eating", "eats", "ate", "lift", "lifts", "lifting", "lower",
        "lowers", "lowering", "set", "sets", "setting", "put", "puts", "placing",
        "place", "placed", "hold", "holds", "holding", "grip", "grips", "gesture",
        "gestures", "point", "points", "touch", "touches", "resting his hand", "resting her hand",
    ]
    return any(term in source for term in human_action_terms) or _has_human_presence_in_video_beat(source)


def _derive_human_video_action_from_primary_action(primary_action: str = "", srt_text: str = "") -> str:
    source = " ".join([str(primary_action or ""), str(srt_text or "")]).lower()
    if any(term in source for term in ["holds a steady gaze", "holds his gaze", "looking at", "looks directly", "gaze"]):
        return "she holds his gaze across the table"
    if any(term in source for term in ["explain", "explains", "speaks", "speaking", "told me", "telling"]):
        return "she speaks across the table"
    if any(term in source for term in ["absorbs", "registers", "notice", "realizes", "sinks in"]):
        return "he looks toward her across the table"
    if any(term in source for term in ["frozen", "stunned", "sits motionless", "sits in silence"]):
        return "he remains frozen opposite her"
    if any(term in source for term in ["separated", "apart", "opposite sides"]):
        return "they remain seated on opposite sides of the table"
    return "they remain seated across the table"


def _derive_environment_video_action(primary_action: str = "", srt_text: str = "", visual_anchor: str = "") -> str:
    source = " ".join([str(primary_action or ""), str(srt_text or ""), str(visual_anchor or "")]).lower()
    if any(term in source for term in ["drive away", "drove away", "gets into her car", "got into her car"]):
        return "she gets into her car and drives away"
    if any(term in source for term in ["making coffee", "coffee", "counter"]) and any(term in source for term in ["stood", "stands", "standing", "kitchen"]):
        return "he stands at the counter with his coffee"
    if any(term in source for term in ["driving to work", "drove to work", "autopilot"]):
        return "he drives on without reacting"
    if any(term in source for term in ["crossing the lot", "came out", "fountain drink", "bag of chips"]):
        return "she crosses the lot toward her car"
    if any(term in source for term in ["separated", "apart", "opposite sides", "across the table", "between them"]):
        return "both remain separated across the table"
    return "both remain still within the room"


def _sanitize_generic_video_specifics(
    action_text: str,
    srt_text: str = "",
    scene_kind: str = "",
    subject_mode: str = "",
    primary_subject: str = "",
    visual_anchor: str = "",
) -> str:
    cleaned = " ".join(str(action_text or "").split()).lower()
    source = " ".join([
        cleaned,
        str(srt_text or "").lower(),
        str(primary_subject or "").lower(),
        str(visual_anchor or "").lower(),
    ])

    if str(scene_kind or "").strip().lower() == "environment_story" and _has_human_presence_in_video_beat(cleaned, srt_text, primary_subject):
        return _derive_environment_video_action(primary_subject, srt_text, visual_anchor)

    screen_specific_terms = [
        "diagram", "statistics", "statistical", "bookmarked", "article", "articles",
        "tabs", "tab", "search results", "research", "model", "models",
    ]
    if any(term in source for term in ["laptop", "screen", "browser"]) and any(term in source for term in screen_specific_terms):
        kind = str(scene_kind or "").strip().lower()
        if kind in {"interaction", "character_reaction"} or str(subject_mode or "").strip().lower() in {"character", "pair"}:
            return _derive_human_video_action_from_primary_action(primary_subject, srt_text)
        if any(term in source for term in ["gesture", "gestures", "point", "points", "touch", "touches", "hand near"]):
            return "she gestures toward the open laptop"
        return "the open laptop remains still in frame"

    if str(scene_kind or "").strip().lower() == "object_detail" and _has_human_mediated_object_action(cleaned, srt_text, primary_subject, visual_anchor):
        if any(term in source for term in ["fork", "pasta", "plate"]) and any(term in source for term in ["put", "set", "placed", "down"]):
            return "a hand sets the fork beside the plate"
        if any(term in source for term in ["fork", "pasta", "plate"]) and any(term in source for term in ["eat", "eating", "lift", "lifts", "holding", "holds"]):
            return "a hand lifts a fork from the pasta"
        if any(term in source for term in ["laptop", "screen"]) and any(term in source for term in ["gesture", "point", "touch", "hand"]):
            return "a hand pauses beside the open laptop"

    return cleaned


def _is_inanimate_video_scene(
    scene_kind: str = "",
    subject_mode: str = "",
    primary_subject: str = "",
) -> bool:
    mode = str(subject_mode or "").strip().lower()
    kind = str(scene_kind or "").strip().lower()
    subject = str(primary_subject or "").strip().lower()
    if mode in {"character", "pair"}:
        return False
    if kind in {"interaction", "character_reaction", "movement_transition"}:
        return False
    if mode in {"object", "environment"}:
        return True
    if kind in {"object_detail", "environment_story"}:
        return True
    object_terms = [
        "laptop", "phone", "screen", "folder", "document", "paper", "printout",
        "plate", "pasta", "table", "clock", "bag", "coat", "mug", "cup",
        "dishwasher", "chair", "door", "window", "receipt", "notebook",
    ]
    human_terms = [
        "man", "woman", "husband", "wife", "megan", "narrator", "person",
        "face", "eyes", "hand", "hands", "shoulder", "body", "adult", "couple",
    ]
    return any(t in subject for t in object_terms) and not any(t in subject for t in human_terms)


def _sanitize_inanimate_video_action(
    action_text: str,
    primary_subject: str = "",
    visual_anchor: str = "",
    srt_text: str = "",
) -> str:
    source = " ".join([
        str(action_text or ""),
        str(primary_subject or ""),
        str(visual_anchor or ""),
        str(srt_text or ""),
    ]).lower()
    subject = str(primary_subject or visual_anchor or "the object").strip().lower()

    if any(k in source for k in ["transaction row", "charge", "wells fargo", "homegoods"]):
        return "the transaction row remains fixed on the screen"
    if any(k in source for k in ["call-and-text rows", "call and text rows", "unknown number", "45-minute", "11 p.m.", "6 a.m.", "6 a.m", "11 at night", "6 in the morning"]):
        return "the call-and-text rows remain fixed on screen"
    if _has_human_mediated_object_action(action_text, srt_text, primary_subject, visual_anchor):
        if any(k in source for k in ["fork", "pasta", "plate"]) and any(k in source for k in ["put", "set", "placed", "down"]):
            return "a hand sets the fork beside the plate"
        if any(k in source for k in ["fork", "pasta", "plate"]) and any(k in source for k in ["eat", "eating", "lift", "lifts", "holding", "holds"]):
            return "a hand lifts a fork from the pasta"
        if any(k in source for k in ["laptop", "screen"]):
            return "a hand pauses beside the open laptop"
    if any(k in source for k in ["fork", "pasta", "plate"]):
        return "the plate and fork remain still in frame"
    if any(k in source for k in ["laptop", "screen"]):
        return "the open laptop remains still in frame"
    if any(k in source for k in ["phone", "contacts", "screen dark"]):
        return "the phone remains still in the hand"
    if any(k in source for k in ["folder", "document", "paper", "printout", "note", "receipt"]):
        return "the papers remain fixed in place"
    if any(k in source for k in ["chair", "table", "doorway", "door", "window", "room", "kitchen"]):
        return "the frame holds on the still space"

    if subject and subject not in {"the object", "the subject"}:
        return f"{subject[:60]} remains still in frame"
    return "the subject remains still in frame"


def _derive_safe_video_camera(camera: str = "", visual_moment: str = "", srt_text: str = "") -> str:
    source = " ".join([(camera or ""), (visual_moment or ""), (srt_text or "")]).lower()
    if any(k in source for k in ["detail", "hand", "letter", "phone", "mug", "dress", "cake", "document", "ring", "note"]):
        return "detail close-up"
    if any(k in source for k in ["close-up", "close up", "face", "eyes", "tear", "expression"]):
        return "close-up"
    if any(k in source for k in ["wide", "room", "doorway", "hallway", "living room", "kitchen", "outside", "porch"]):
        return "wide shot"
    if any(k in source for k in ["two-shot", "two shot", "waist", "table", "counter"]):
        return "medium shot"
    return "medium close-up"


def _derive_safe_video_motion(camera: str = "", mood: str = "", action: str = "") -> str:
    source = " ".join([(camera or ""), (mood or ""), (action or "")]).lower()
    if re.search(r"\b(walk|walks|walking|enter|enters|entering|leave|leaves|leaving|cross|crosses|crossing|step|steps|stepping|turns toward)\b", source):
        return "gentle pan right"
    if any(k in source for k in ["alone", "lonely", "distance", "empty", "loss", "grief"]):
        return "very slow pull-back"
    if any(k in source for k in ["realize", "notice", "discovers", "reveal", "recognition", "shock"]):
        return "very slow push-in"
    return "static frame"


def _derive_sequence_aware_video_motion(
    camera: str = "",
    mood: str = "",
    action: str = "",
    scene_kind: str = "",
    subject_mode: str = "",
    primary_subject: str = "",
    shot_function: str = "",
    sequence_role: str = "",
) -> str:
    is_inanimate = _is_inanimate_video_scene(
        scene_kind=scene_kind,
        subject_mode=subject_mode,
        primary_subject=primary_subject,
    )
    base = _derive_safe_video_motion(camera=camera, mood=mood, action=action)
    shot = str(shot_function or "").strip().lower()
    role = str(sequence_role or "").strip().lower()

    if is_inanimate:
        if not shot and not role:
            if str(scene_kind or "").strip().lower() == "environment_story":
                return "very slow pull-back"
            if str(scene_kind or "").strip().lower() == "object_detail":
                return "very slow push-in"
        if shot in {"reveal", "evidence", "pressure"}:
            return "very slow push-in"
        if shot in {"aftermath", "transition"} or role == "closing":
            return "very slow pull-back"
        return "static frame"

    kind = str(scene_kind or "").strip().lower()
    if not shot and not role:
        if kind == "character_reaction":
            return "very slow push-in"
        if kind == "environment_story":
            return "very slow pull-back"
        if kind == "movement_transition":
            return "gentle pan right"

    if shot in {"reveal", "reaction", "pressure", "evidence"}:
        return "very slow push-in"
    if shot in {"aftermath", "transition"} or role in {"release", "closing"}:
        return "very slow pull-back"
    if shot == "establish":
        cam_low = str(camera or "").lower()
        if "wide" in cam_low:
            return "static frame"
        return "very slow pull-back"
    return base


def _auto_video_prompt(duration: float, visual_moment: str = "", **kwargs) -> str:
    """Alias kept for backward compatibility."""
    return _build_video_prompt_from_content(duration, visual_moment=visual_moment, **kwargs)


# ─────────────────────────────────────────────────────────────────────────────
# FALLBACK PROMPT — khi API hoàn toàn thất bại
# ─────────────────────────────────────────────────────────────────────────────

def _build_video_prompt_from_content(
    duration: float,
    visual_moment: str = "",
    srt_text: str = "",
    camera: str = "",
    mood: str = "",
    scene_kind: str = "",
    subject_mode: str = "",
    primary_subject: str = "",
    primary_action: str = "",
    visual_anchor: str = "",
    shot_function: str = "",
    sequence_role: str = "",
) -> str:
    """Build a constrained safe video prompt from scene content."""
    is_inanimate = _is_inanimate_video_scene(
        scene_kind=scene_kind,
        subject_mode=subject_mode,
        primary_subject=primary_subject,
    )
    action = _sanitize_video_action_text(visual_moment or srt_text)
    action = _sanitize_generic_video_specifics(
        action,
        srt_text=srt_text,
        scene_kind=scene_kind,
        subject_mode=subject_mode,
        primary_subject=f"{primary_subject} {primary_action}".strip(),
        visual_anchor=visual_anchor,
    )
    allow_human_tableau = (
        str(scene_kind or "").strip().lower() == "environment_story"
        and _has_human_presence_in_video_beat(action, srt_text, primary_action)
    )
    if is_inanimate:
        if not allow_human_tableau:
            action = _sanitize_inanimate_video_action(
                action_text=action,
                primary_subject=f"{primary_subject} {primary_action}".strip(),
                visual_anchor=visual_anchor,
                srt_text=srt_text or visual_moment,
            )
    framing = _derive_safe_video_camera(camera=camera, visual_moment=visual_moment, srt_text=srt_text)
    motion = _derive_sequence_aware_video_motion(
        camera=camera,
        mood=mood,
        action=action,
        scene_kind=scene_kind,
        subject_mode=subject_mode,
        primary_subject=primary_subject,
        shot_function=shot_function,
        sequence_role=sequence_role,
    )
    return f"CAMERA: {framing} | MOTION: {motion} | ACTION: {action}"


def postprocess_video_prompt(
    prompt: str,
    duration: float,
    visual_moment: str = "",
    srt_text: str = "",
    camera: str = "",
    mood: str = "",
    scene_kind: str = "",
    subject_mode: str = "",
    primary_subject: str = "",
    primary_action: str = "",
    visual_anchor: str = "",
    shot_function: str = "",
    sequence_role: str = "",
    topic: str = "story",
    style_profile: Optional[Dict[str, Any]] = None,
    img_prompt: str = "",
) -> str:
    """Normalize video prompt into a constrained image-to-video schema."""
    if _is_psychology_topic(topic):
        raw = _strip_forced_psychology_cultural_props(prompt)
        primary_subject = _strip_forced_psychology_cultural_props(primary_subject)
        primary_action = _strip_forced_psychology_cultural_props(primary_action)
        visual_anchor = _strip_forced_psychology_cultural_props(visual_anchor)
        visual_moment = _strip_forced_psychology_cultural_props(visual_moment)
        raw = " ".join(str(raw or "").split())
        raw = _normalize_psychology_reference_language(raw, True)
        raw = _strip_psychology_meta_instruction_language(raw)
        low = raw.lower()
        first_cultural_prop = ""
        if style_profile:
            first_cultural_prop = str(style_profile.get("cultural_props", "") or "").split(",")[0].strip().lower()
        source_for_prop = " ".join([srt_text, primary_action, visual_anchor, visual_moment, primary_subject]).lower()
        repeated_cultural_prop = (
            bool(first_cultural_prop)
            and first_cultural_prop in low
            and first_cultural_prop not in source_for_prop
            and _is_concrete_psychology_video_action(" ".join([primary_action, visual_moment, primary_subject, visual_anchor]))
        )
        movement = _derive_video_movement(
            srt_text=srt_text or visual_moment,
            primary_action=primary_action,
            visual_anchor=visual_anchor,
            visual_moment=visual_moment,
            primary_subject=primary_subject,
            style_profile=style_profile,
            img_prompt=img_prompt,
            topic=topic,
        )

        # ROOT CAUSE FIX: If we extracted motion from img_prompt, check if it's better than raw
        # Extracted motion from image is ALWAYS more specific than generic fallback
        has_extracted_motion = (
            img_prompt
            and len(img_prompt) > 100
            and movement
            and len(movement) > 100
            and not any(generic in movement.lower() for generic in [
                "the reference character observes",
                "the reference character types a reply then deletes",
                "the reference character makes one small deliberate gesture",
            ])
        )

        required_hybrid_markers = [
            "faithfully visualize this narration beat",
            "image-derived visual base",
            "no letters",
            "no numbers",
            "no logos",
            "distinct from adjacent scenes",
        ]
        must_rebuild_from_image = bool(img_prompt and not all(marker in low for marker in required_hybrid_markers))

        if (
            raw
            and len(raw) >= 120
            and "camera:" not in low
            and "motion:" not in low
            and not PSYCHOLOGY_META_INSTRUCTION_TERMS.search(raw)
            and not _is_generic_psychology_video_motion(raw)
            and not repeated_cultural_prop
            and not has_extracted_motion  # NEW: Don't early return if we have extracted motion
            and not img_prompt
            and not must_rebuild_from_image
        ):
            return _normalize_psychology_reference_language(raw, True)
        if "animate this exact script idea clearly" in low and "no text" in low:
            needs_emotion = "emotional arc:" not in low or "performance direction:" not in low
            if _is_generic_psychology_video_motion(raw) or "specific movement:" not in low or needs_emotion or repeated_cultural_prop:
                prefix = raw.split("Specific movement:", 1)[0].rstrip(". ")
                if "use specific movement by the character" in prefix.lower():
                    prefix = re.sub(
                        r"Use specific movement by the character.*$",
                        "",
                        prefix,
                        flags=re.IGNORECASE,
                    ).rstrip(". ")
                emotional_arc = _derive_emotional_arc(
                    srt_text=srt_text or visual_moment,
                    primary_action=primary_action,
                    visual_anchor=visual_anchor,
                    visual_moment=visual_moment,
                    primary_subject=primary_subject,
                    style_profile=style_profile,
                )
                rebuilt = (
                    prefix
                    + f". Specific movement: {movement}. Literal scene action tied directly to the narration, not vague atmosphere. "
                    + f"{emotional_arc}. Performance direction: prioritize posture, tiny pauses, hand tension, shoulder movement, gaze direction, and nearby prop response so the emotion is felt without exaggerated acting."
                )
                return _strip_psychology_meta_instruction_language(_normalize_psychology_reference_language(rebuilt, True))
            return raw
        return _strip_psychology_meta_instruction_language(_normalize_psychology_reference_language(_sample_style_video_prompt(
            srt_text or visual_moment,
            "",
            movement,
            primary_action=primary_action,
            visual_anchor=visual_anchor,
            visual_moment=visual_moment,
            primary_subject=primary_subject,
            style_profile=style_profile,
            img_prompt=img_prompt,
            topic=topic,
        ), True))
    raw = " ".join(str(prompt or "").split())
    raw = re.sub(r"\|\s*DURATION\s*:\s*[^|]+", "", raw, flags=re.IGNORECASE)
    raw = re.sub(r"\|\s*(ATMOSPHERE|VISUAL_FOCUS)\s*:", "| ACTION:", raw, flags=re.IGNORECASE)

    camera_match = re.search(r"CAMERA\s*:\s*([^|]+)", raw, flags=re.IGNORECASE)
    motion_match = re.search(r"MOTION\s*:\s*([^|]+)", raw, flags=re.IGNORECASE)
    action_match = re.search(r"ACTION\s*:\s*([^|]+)", raw, flags=re.IGNORECASE)

    action_text = action_match.group(1).strip() if action_match else (visual_moment or srt_text or raw)
    action_text = _sanitize_video_action_text(action_text)
    action_text = _sanitize_generic_video_specifics(
        action_text,
        srt_text=srt_text or visual_moment,
        scene_kind=scene_kind,
        subject_mode=subject_mode,
        primary_subject=f"{primary_subject} {primary_action}".strip(),
        visual_anchor=visual_anchor,
    )
    is_inanimate = _is_inanimate_video_scene(
        scene_kind=scene_kind,
        subject_mode=subject_mode,
        primary_subject=primary_subject,
    )
    allow_human_tableau = (
        str(scene_kind or "").strip().lower() == "environment_story"
        and _has_human_presence_in_video_beat(action_text, srt_text, primary_action)
    )
    if is_inanimate:
        if not allow_human_tableau:
            action_text = _sanitize_inanimate_video_action(
                action_text=action_text,
                primary_subject=f"{primary_subject} {primary_action}".strip(),
                visual_anchor=visual_anchor,
                srt_text=srt_text or visual_moment,
            )

    camera_text = camera_match.group(1).strip().lower() if camera_match else ""
    motion_text = motion_match.group(1).strip().lower() if motion_match else ""

    if camera_text not in SAFE_VIDEO_CAMERAS:
        camera_text = _derive_safe_video_camera(camera=camera, visual_moment=visual_moment, srt_text=srt_text)
    suggested_motion = _derive_sequence_aware_video_motion(
        camera=camera,
        mood=mood,
        action=action_text,
        scene_kind=scene_kind,
        subject_mode=subject_mode,
        primary_subject=primary_subject,
        shot_function=shot_function,
        sequence_role=sequence_role,
    )
    if motion_text not in SAFE_VIDEO_MOTIONS:
        motion_text = suggested_motion
    elif motion_text == "static frame" and suggested_motion != "static frame":
        motion_text = suggested_motion

    if VIDEO_AUDIO_TERMS.search(action_text) or VIDEO_ABSTRACT_TERMS.search(action_text):
        action_text = _sanitize_video_action_text(visual_moment or srt_text)
        action_text = _sanitize_generic_video_specifics(
            action_text,
            srt_text=srt_text or visual_moment,
            scene_kind=scene_kind,
            subject_mode=subject_mode,
            primary_subject=f"{primary_subject} {primary_action}".strip(),
            visual_anchor=visual_anchor,
        )
        if is_inanimate:
            if not allow_human_tableau:
                action_text = _sanitize_inanimate_video_action(
                    action_text=action_text,
                    primary_subject=f"{primary_subject} {primary_action}".strip(),
                    visual_anchor=visual_anchor,
                    srt_text=srt_text or visual_moment,
                )

    kind = str(scene_kind or "").strip().lower()
    mode = str(subject_mode or "").strip().lower()
    if kind in {"interaction", "character_reaction"} or mode in {"character", "pair"}:
        if (
            action_text in {
                "the open laptop remains still in frame",
                "the plate and fork remain still in frame",
                "the subject remains still in frame",
            }
            or (
                any(term in action_text for term in ["laptop", "screen", "plate", "fork", "pasta"])
                and not _has_human_presence_in_video_beat(action_text)
            )
        ):
            action_text = _derive_human_video_action_from_primary_action(primary_action, srt_text)

    return f"CAMERA: {camera_text} | MOTION: {motion_text} | ACTION: {action_text}".strip()


def build_fallback_prompt(
    scene: Dict[str, Any],
    char_lookup: Dict[str, str],
    char_image_lookup: Dict[str, str],
    loc_lookup: Dict[str, str],
    loc_image_lookup: Dict[str, str],
    context_lock: str = "",
    minor_char_ids: Optional[Set[str]] = None,
    scene_plan: Optional[Dict[str, Any]] = None,
    topic: str = "story",
    style_profile: Optional[Dict[str, Any]] = None,
) -> tuple:
    """
    Tạo img_prompt + video_prompt fallback chất lượng cao
    khi AI API thất bại.
    
    Returns: (img_prompt, video_prompt)
    """
    srt_text    = scene.get("srt_text", "")
    visual      = scene.get("visual_moment", "")
    camera      = scene.get("camera", "Medium shot")
    lighting    = scene.get("lighting", "Natural lighting")
    duration    = scene.get("duration", 6.0)
    plan        = scene_plan or {}
    shot_type   = plan.get("shot_type") or camera
    subject_mode = (plan.get("subject_mode") or scene.get("subject_mode") or "").strip().lower()
    primary_subject = (plan.get("primary_subject") or scene.get("primary_subject") or "").strip()
    primary_action = (plan.get("primary_action") or scene.get("primary_action") or "").strip()
    visual_anchor = (plan.get("key_focus") or scene.get("visual_anchor") or "").strip()
    visual_contract = (plan.get("visual_contract") or scene.get("visual_contract") or "").strip()
    mood = (plan.get("mood") or "Authentic, emotionally resonant, narrative-driven").strip()
    
    char_ids = [c.strip() for c in scene.get("characters_used", "").split(",") if c.strip() and c.strip() != "[]"]
    adult_char_ids, minor_ids = _split_minor_characters(char_ids, minor_char_ids)
    loc_id   = scene.get("location_used", "").strip()
    
    # Build SUBJECT block
    subject_parts = []
    char_refs = []
    if minor_ids and not adult_char_ids:
        subject = visual_anchor or primary_subject or "An environment detail that implies an off-screen family presence"
    elif subject_mode in {"object", "environment"} and primary_subject:
        subject = primary_subject
    else:
        for cid in adult_char_ids:
            desc = char_lookup.get(cid, f"person ({cid})")
            img  = char_image_lookup.get(cid, f"{cid}.png")
            subject_parts.append(f"{desc} ({img})")
            char_refs.append(img)
        subject = ", ".join(subject_parts) if subject_parts else (primary_subject or "An emotionally resonant environment detail")
    
    # Build SETTING block
    loc_desc = loc_lookup.get(loc_id, "an interior space") if loc_id else "an appropriate setting"
    loc_img  = loc_image_lookup.get(loc_id, f"{loc_id}.png") if loc_id else ""
    setting  = f"{loc_desc} ({loc_img})" if loc_img else loc_desc
    
    # Compose prompt
    if minor_ids:
        action = (
            visual_anchor
            or "adult reaction and environmental traces that imply the narrated beat without showing the child directly"
        )
    else:
        action = primary_action or visual_anchor or visual or srt_text[:120]
    if _is_psychology_topic(topic):
        if adult_char_ids and "nv1" in adult_char_ids:
            subject = subject or "the reference character from the provided reference image within the narration-specific scene"
            subject = re.sub(r"\s*\(\s*nv1\.png\s*\)", "", subject, flags=re.IGNORECASE)
        elif not subject or "emotionally resonant environment detail" in subject.lower():
            tc = _get_topic_config(topic)
            subject = primary_subject or visual_anchor or tc.get("fallback_subject", "a concrete symbolic moment from the narration")
        tc = _get_topic_config(topic)
        theme_context = (context_lock or tc.get("theme_context_default", "")).strip()
        if adult_char_ids and "nv1" in adult_char_ids:
            theme_context = _strip_attached_character_description(theme_context)
        img_prompt = _sample_style_image_prompt(
            srt_text=srt_text,
            theme_context=theme_context,
            concrete_visual=visual_contract or visual_anchor,
            subject=subject,
            action=action,
            style_profile=style_profile,
            topic=topic,
        )
        img_prompt = _ensure_psychology_prompt_quality(
            img_prompt,
            char_ids=adult_char_ids,
            srt_text=srt_text,
            primary_subject=primary_subject or subject,
            primary_action=primary_action or action,
            visual_anchor=visual_anchor,
            style_profile=style_profile,
        )
    else:
        img_prompt = (
            f"[STYLE] Cinematic still frame, {shot_type}, anamorphic lens, film grain"
            f" | [SUBJECT] {subject}"
            f" | [ACTION] {action}"
            f" | [SETTING] {setting}"
            f" | [LIGHTING] {lighting}"
            f" | [MOOD] {mood}"
            f" | [TECHNICAL] {TECHNICAL_SUFFIX}"
        )

    img_prompt = _sanitize_story_summary_style(img_prompt)
    img_prompt = _sanitize_prohibited_image_terms(img_prompt)

    if minor_ids:
        minor_refs = [char_image_lookup.get(cid, f"{cid}.png") for cid in minor_ids]
        img_prompt = _sanitize_minor_safe_img_prompt(img_prompt, minor_image_refs=minor_refs)
    
    if _is_psychology_topic(topic):
        video_prompt = _sample_style_video_prompt(
            srt_text,
            context_lock,
            primary_action or visual_contract or visual_anchor or visual,
            primary_action=primary_action,
            visual_anchor=visual_contract or visual_anchor,
            visual_moment=visual,
            primary_subject=primary_subject or subject,
            style_profile=style_profile,
            img_prompt=img_prompt,
            topic=topic,
        )
    else:
        video_prompt = _auto_video_prompt(
            duration,
            visual,
            srt_text=srt_text,
            camera=shot_type,
            mood=mood,
            scene_kind=scene.get("scene_kind", ""),
            subject_mode=subject_mode,
            primary_subject=primary_subject,
            primary_action=primary_action,
            visual_anchor=visual_anchor,
            shot_function=scene.get("shot_function", ""),
            sequence_role=scene.get("sequence_role", ""),
        )
    
    return img_prompt, video_prompt


# ─────────────────────────────────────────────────────────────────────────────
# ESTIMATE SCENE COUNT — tính trước số scenes cho audio dài
# ─────────────────────────────────────────────────────────────────────────────

def estimate_scene_count(
    total_duration_seconds: float,
    target_duration_per_scene: float = 6.5,
) -> Dict[str, Any]:
    """
    Ước tính số scenes từ tổng thời lượng audio.
    
    Ví dụ:
        30 phút = 1800s / 6.5s ≈ 277 scenes
        60 phút = 3600s / 6.5s ≈ 554 scenes
    """
    n_scenes = int(total_duration_seconds / target_duration_per_scene)
    api_calls_per_batch = 10  # batch_size mặc định
    total_batches = (n_scenes + api_calls_per_batch - 1) // api_calls_per_batch
    
    # Thời gian ước tính (2s/call × parallel 4)
    est_minutes = total_batches * 2 / 4 / 60
    
    return {
        "total_duration_s":    total_duration_seconds,
        "total_duration_min":  total_duration_seconds / 60,
        "n_scenes":            n_scenes,
        "target_dur_per_scene": target_duration_per_scene,
        "total_batches":       total_batches,
        "est_api_calls":       total_batches * 7,  # 7 steps worth
        "est_minutes":         round(est_minutes, 1),
    }


def format_scene_estimate(est: Dict[str, Any]) -> str:
    """Format ước tính thành chuỗi dễ đọc."""
    return (
        f"Audio: {est['total_duration_min']:.1f} phut -> "
        f"~{est['n_scenes']} scenes ({est['target_dur_per_scene']:.1f}s/scene) -> "
        f"~{est['total_batches']} batches -> "
        f"~{est['est_minutes']} phut xu ly (4 parallel API calls)"
    )


# ─────────────────────────────────────────────────────────────────────────────
# STEP 7.5: QA REVIEW — phát hiện và sửa prompt lệch SRT
# ─────────────────────────────────────────────────────────────────────────────

SYSTEM_PROMPT_QA_REVIEW = """You are a strict QA REVIEWER for AI-generated image prompts.

YOUR TASK: Check whether each image prompt (img_prompt) accurately illustrates the narration text (srt_text).

SCORING RULES (1-10):
- 9-10: img_prompt directly illustrates the specific content of srt_text (characters, objects, actions mentioned)
- 7-8: img_prompt captures the emotional tone and general idea but misses specific details
- 4-6: img_prompt is loosely related but shows different content than what srt_text describes
- 1-3: img_prompt shows completely unrelated content (WRONG scene, different objects/actions/characters)

CRITICAL: A narration-style story (voiceover) often uses abstract language. The img_prompt should visualize WHAT IS BEING DESCRIBED, not the literal words. For example:
- srt_text: "We were not passionate people" → img_prompt showing a couple doing things side by side without eye contact = score 9 (correct visualization)
- srt_text: "She was a capable hostess" → img_prompt showing seatbelt buckle = score 2 (WRONG, completely unrelated)
- srt_text: "Someone brought out a speaker" → img_prompt showing cookies from oven = score 1 (WRONG)
- If narration references shared history or backstory to deepen the meaning of a present object or place
  (for example when/where the couple bought a table), the prompt may score highly by showing the present object
  with emotional weight. It does NOT need to invent receipts, flashbacks, store signage, calendars, or literal labels.
- If narration presents a relational idea or argument ("healthy for us", "strengthen their bond", "not the only model", "I wanted to say something sharp"),
  the prompt may score highly by showing the speaker advancing the idea and the listener absorbing or resisting it.
  Do NOT require literal diagrams, readable slogans, symbolic text overlays, or exaggerated gestures if the dramatic exchange is already clear.

Focus on: Does the IMAGE show what the NARRATION is talking about at this moment in the story?"""


def build_qa_review_request(
    batch: List[Dict[str, Any]],
    topic: str = "story",
    style_profile: Optional[Dict[str, Any]] = None,
) -> str:
    """Build prompt for QA review — detect misaligned scenes with stricter criteria."""
    scenes_text = ""
    for scene in batch:
        img_prompt = str(scene.get("img_prompt", "") or "")
        video_prompt = str(scene.get("video_prompt", "") or "")
        scenes_text += f"""
Scene {scene.get('scene_id')}:
  NARRATION: "{scene.get('srt_text', '')}"
  SEQUENCE: "{scene.get('sequence_id', '')}" / role={scene.get('sequence_role', '')}
  LOCKED SUBJECT: "{scene.get('primary_subject', '')}"
  LOCKED ACTION: "{scene.get('primary_action', '')}"
  LOCKED ANCHOR: "{scene.get('visual_anchor', '')}"
  PREV BEAT: "{scene.get('prev_beat', '')}"
  NEXT BEAT: "{scene.get('next_beat', '')}"
  IMG_PROMPT_LENGTH: {len(img_prompt)}
  IMG_PROMPT_FULL: "{img_prompt}"
  VIDEO_PROMPT_LENGTH: {len(video_prompt)}
  VIDEO_PROMPT_FULL: "{video_prompt}"
"""

    topic_rules = ""
    if _is_psychology_topic(topic):
        profile = normalize_style_profile(style_profile)
        tc = _get_topic_config(topic)
        topic_rules = f"""
{tc['topic_label']} STYLE QA:
- Channel style name: {profile['style_name']}
- Required image style: {_runtime_image_style(profile)}
- Required video style: {_runtime_video_style(profile)}
- Negative rules: {profile['negative_prompt']}
- If img_prompt does not follow the required channel style, score <= 6.
- If img_prompt uses camera-gear or realistic-photo production language against the channel style, score <= 6.
- If it invents readable labels, captions, chart text, UI text, document text, or signage, score <= 6.
- If it invents named/reference characters other than nv1, score <= 5.
- If img_prompt lacks a clear focal hierarchy, emotional body language, or one memorable symbolic visual anchor, score <= 8.
- If img_prompt is generic and could fit many different SRT lines, score <= 7.
- If video_prompt uses CAMERA/MOTION/ACTION blocks, score <= 6.
- If video_prompt only says generic movement like "Use specific movement by the character" without naming a concrete visible motion, score <= 8.
- If video_prompt does not animate the exact SRT idea through a visible body, prop, boundary, silhouette, object, or environment movement, score <= 8.
"""

    return f"""Review these {len(batch)} scenes for NARRATION-PROMPT ALIGNMENT.

{topic_rules}

{scenes_text}

For each scene, score the alignment between NARRATION and IMG_PROMPT on a scale of 0-10:

SCORING CRITERIA:
10 = Perfect: Every key element in narration is visualized in the prompt
9  = Excellent: All major elements present, minor details may vary
8  = Good: Core story beat is captured, some artistic interpretation
7  = Acceptable: General mood matches but missing 1-2 key details
6  = Weak: Prompt captures setting but misses the specific action/object mentioned
5  = Poor: Prompt shows related scene but not what narrator is actually describing
0-4 = Failed: Prompt shows completely different content

🚨 CRITICAL RULES:
1. If narration mentions a SPECIFIC OBJECT (laundry, dryer, clock, stove, bag, phone, coat, table, etc.),
   that object MUST appear in the prompt. If it doesn't, score ≤ 6.
2. If narration describes a SPECIFIC ACTION (looking up, sitting, standing, waiting, etc.),
   that action MUST be in the prompt. If it's replaced with a generic pose, score ≤ 7.
3. If prompt shows a DIFFERENT object from the same category (refrigerator instead of dryer),
   score ≤ 5.
4. If the prompt ignores the locked primary_subject / primary_action / visual_anchor, score ≤ 7.
5. If the prompt repeats a generic setup that does not advance from PREV BEAT toward NEXT BEAT, lower the score unless the narration truly calls for a hold.
6. If narration includes backstory or memory that gives meaning to a present object/place, do NOT require invented flashback evidence, receipts, labels, calendars, or signage. Score based on whether the present visual truth carries that meaning.
7. If narration expresses an abstract relational claim, persuasion, or imagined reply, a strong speaker-listener composition may be enough. Do NOT require readable laptop text, diagrams, charts, slogans, or literalized symbolic props if the conversational beat is already visible.
8. If a prompt captures the emotional and relational beat through body language, blocking, and continuity, prefer that over forcing extra exposition props into frame.

Return JSON only — no explanation:
{{
    "reviews": [
        {{
            "scene_id": <int>,
            "score": <0-10>,
            "reason": "<why this score, what's missing or wrong>",
            "missing_elements": ["<list key narration elements not in prompt>"]
        }}
    ]
}}

IMPORTANT: Return EXACTLY {len(batch)} reviews, one per scene. Be strict — the goal is 9-10 alignment, not just "good enough"."""


def build_fix_prompt(
    scene: Dict[str, Any],
    context_lock: str,
    char_lookup: Dict[str, str],
    char_image_lookup: Dict[str, str],
    loc_lookup: Dict[str, str],
    loc_image_lookup: Dict[str, str],
    scene_planning: Dict[int, Dict],
    rejection_reason: str = "",
    minor_char_ids: Optional[Set[str]] = None,
    topic: str = "story",
    style_profile: Optional[Dict[str, Any]] = None,
) -> str:
    """Build prompt to regenerate a SINGLE rejected scene's prompts."""
    scene_id = scene.get("scene_id")
    srt_text = scene.get("srt_text", "")
    visual = scene.get("visual_moment", "")
    duration = scene.get("duration", 6.0)
    scene_kind = scene.get("scene_kind", "")
    subject_mode = scene.get("subject_mode", "")
    primary_subject = scene.get("primary_subject", "")
    primary_action = scene.get("primary_action", "")
    visual_anchor = scene.get("visual_anchor", "")
    must_not_show = scene.get("must_not_show", "")

    # Characters
    char_ids = [c.strip() for c in scene.get("characters_used", "").split(",") if c.strip() and c.strip() != "[]"]
    adult_char_ids, minor_ids = _split_minor_characters(char_ids, minor_char_ids)
    char_parts = []
    char_refs = []
    for cid in adult_char_ids:
        desc = char_lookup.get(cid, cid)
        img = char_image_lookup.get(cid, f"{cid}.png")
        char_parts.append(f"{desc} → ref: ({img})")
        char_refs.append(img)

    # Location
    loc_id = scene.get("location_used", "").strip()
    loc_desc = loc_lookup.get(loc_id, loc_id)
    loc_img = loc_image_lookup.get(loc_id, f"{loc_id}.png") if loc_id else ""
    loc_str = f"{loc_desc} → ref: ({loc_img})" if loc_id else "Not specified"

    # Artistic plan
    plan = scene_planning.get(scene_id, {})
    plan_block = ""
    if plan:
        plan_block = f"""ARTISTIC VISION:
    Intent   : {plan.get("artistic_intent", "")}
    Shot type: {plan.get("shot_type", "")}
    Action   : {plan.get("character_action", "")}
    Mood     : {plan.get("mood", "")}
    Lighting : {plan.get("lighting", "")}
    Colors   : {plan.get("color_palette", "")}
    Focus    : {plan.get("key_focus", "")}"""

    rejection_note = ""
    if rejection_reason:
        rejection_note = f"""
⛔ PREVIOUS PROMPT WAS REJECTED: {rejection_reason}
You MUST fix this issue. The new prompt MUST illustrate EXACTLY what the narration says."""

    minor_note = ""
    if minor_ids:
        minor_note = """
MINOR-SAFE MODE:
- The narration involves a child/minor.
- Do NOT show the child directly.
- Do NOT mention or use child reference images.
- Express the moment through adult reaction, props, environment, or off-screen implication only."""

    style_rules = """
ABSOLUTE RULE: The img_prompt MUST depict what the narrator is describing RIGHT NOW.
- If the narration says "I drove to the house with the radio on" → show the character DRIVING with RADIO ON
- If the narration says "His scissors cut crooked on the left side" → show the CROOKED CUT LETTERS
- DO NOT show flashbacks, metaphors, or other characters doing unrelated things
- DO NOT use split-screen, composite, layered, translucent, ghostly, overlay, or flashback insert framing
- Follow the locked scene spec. One frame only. One primary subject. One primary action.
- Ask yourself: "If someone reads the SRT text, would they recognize this image as illustrating it?" If not, you've failed.
"""
    img_schema = "[STYLE] ... | [SUBJECT] ... (ref.png) | [ACTION] ... | [SETTING] ... (loc.png) | [LIGHTING] ... | [MOOD] ... | [TECHNICAL] photorealistic, 8K, cinematic color grade"
    if _is_psychology_topic(topic):
        profile = normalize_style_profile(style_profile)
        style_rules = f"""
ABSOLUTE RULE: The img_prompt MUST make the spoken idea clear as one engaging educational visual in this channel's fixed style.
- Channel style name: {profile['style_name']}
- Start with this image style: {_runtime_image_style(profile)}
- Character reference: use the provided reference image as the identity/style source; preserve the reference character exactly and do not re-describe it in detail.
- Use these negative rules: {profile['negative_prompt']}
- Use the locked scene spec and planning fields as the foundation: primary_subject, primary_action, visual_anchor, viewer_attention, key_focus, subtext_delivery, and artistic_intent.
- ALL output prompts MUST be written entirely in English. If the narration is not in English, translate the idea to English.
- Keep the reference character as the emotional anchor using the provided reference image, while spending prompt detail on the narration-specific environment, objects, and body language.
- Add supporting props only when they directly fit the narration; use same-style secondary silhouettes, body language, and direct visual cause-and-effect so the meaning is immediately obvious.
- Avoid generic symbolism if a concrete real-life situation can express the line more precisely.
- If the recurring character is used, rely on the provided reference image for identity. Other people must be anonymous silhouettes/background figures.
- ABSOLUTELY NO text, letters, words, writing, captions, labels, signs, or readable characters in the image.
- video_prompt must start with this video style: {_runtime_video_style(profile)}
- video_prompt must describe one concrete visible motion tied to the narration and one clear emotional arc.
- Never use generic movement-only text; the repaired video prompt must name the moving body part, narration-relevant object, boundary, silhouette, or symbolic shape.
- Do not use CAMERA/MOTION/ACTION blocks for repairs.
- Do not output old internal scaffolding labels, translation instructions, or prompt-writing instructions.
"""
        img_schema = f"{_runtime_image_style(profile)} [clean final image prompt using primary_subject, primary_action, key_focus/viewer_attention, and subtext_delivery; 90-150 words; no readable text or letters.]"
        video_schema = f"{_runtime_video_style(profile)} [clean final video prompt with one concrete visible movement and emotional arc; 45-90 words; no camera gear, no text or letters.]"
    else:
        video_schema = "CAMERA: <framing> | MOTION: <simple movement> | ACTION: <single visible action from narration>"

    return f"""Regenerate prompts for this SINGLE scene. This is a QA fix — the previous prompt did not match the narration.
{rejection_note}

GLOBAL CONTEXT: {_strip_attached_character_description(context_lock)}

━━━ SCENE {scene_id} ━━━ ({duration:.1f}s)
  ⚠ NARRATION (THIS IS THE ONLY TRUTH — ignore any visual direction that contradicts it): "{srt_text}"
  LOCKED SCENE SPEC:
    kind         : {scene_kind}
    subject_mode : {subject_mode}
    primary_subj : {primary_subject}
    primary_act  : {primary_action}
    anchor       : {visual_anchor}
    must_not_show: {must_not_show}
  CHARACTERS: {chr(10).join('    ' + p for p in char_parts) if char_parts else '    None'}
  LOCATION: {loc_str}
  REFERENCES: {", ".join(char_refs + ([loc_img] if loc_img else []))}
{plan_block}
{minor_note}

{style_rules}

OUTPUT FORMAT (JSON only):
{{
    "scenes": [
        {{
            "scene_id": {scene_id},
            "img_prompt": "{img_schema}",
            "video_prompt": "{video_schema}"
        }}
    ]
}}"""


# ─────────────────────────────────────────────────────────────────────────────
# BACKWARD-COMPATIBLE ALIASES — old names still importable
# ─────────────────────────────────────────────────────────────────────────────
normalize_psychology_style_profile = normalize_style_profile
