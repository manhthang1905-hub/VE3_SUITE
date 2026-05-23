#!/usr/bin/env python3
"""
validate_channel.py — Validate a new channel (psychology or finance) is properly configured.

Usage:
    python validate_channel.py TL1-T11
    python validate_channel.py TH1-T1
    python validate_channel.py --all
"""
import sys
import yaml
from pathlib import Path

if sys.stdout and hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

TOOL_DIR = Path(__file__).resolve().parent
REF_ROOTS = [
    TOOL_DIR / "reference_characters" / "psychology",
    TOOL_DIR / "reference_characters" / "finance",
    TOOL_DIR / "reference_characters" / "success",
]

REQUIRED_FIELDS = [
    "style_name", "image_style", "video_style", "thumbnail_style",
    "scene_plan_style", "palette", "negative_prompt", "reference_lock",
    "technical_suffix", "engagement_rules", "default_character_prompt",
    "default_character_lock", "audience_language", "audience_culture_note",
    "cultural_props", "cultural_metaphors", "cultural_emotion_style",
]

MIN_LENGTHS = {
    "image_style": 100,
    "audience_culture_note": 100,
    "cultural_props": 50,
    "cultural_metaphors": 100,
    "cultural_emotion_style": 80,
    "reference_lock": 50,
    "default_character_prompt": 50,
}

MOJIBAKE_MARKERS = ["Ã", "Â", "â€", "â€”", "â€“", "Ä", "Æ", "�"]


def _find_ref_root(channel_id: str) -> Path:
    for root in REF_ROOTS:
        if (root / channel_id).exists():
            return root
    return REF_ROOTS[0]


def validate_channel(channel_id: str) -> list:
    """Validate a single channel. Returns list of issues (empty = OK)."""
    issues = []
    REF_ROOT = _find_ref_root(channel_id)
    channel_dir = REF_ROOT / channel_id

    # 1. Folder exists
    if not channel_dir.exists():
        issues.append(f"Folder does not exist: {channel_dir}")
        return issues

    # 2. nv1.png exists
    nv1_path = channel_dir / "nv1.png"
    if not nv1_path.exists():
        issues.append("Missing nv1.png (character reference image)")

    # 3. style.yaml exists
    style_path = channel_dir / "style.yaml"
    if not style_path.exists():
        issues.append("Missing style.yaml")
        return issues

    # 4. Parse YAML
    try:
        data = yaml.safe_load(style_path.read_text(encoding="utf-8")) or {}
    except Exception as e:
        issues.append(f"YAML parse error: {e}")
        return issues

    # 5. Check required fields
    for field in REQUIRED_FIELDS:
        value = str(data.get(field, "") or "").strip()
        if not value:
            issues.append(f"Missing required field: {field}")
        elif "CHANGE_ME" in value:
            issues.append(f"Field still has CHANGE_ME placeholder: {field}")
        else:
            min_len = MIN_LENGTHS.get(field, 0)
            if min_len and len(value) < min_len:
                issues.append(f"Field '{field}' too short ({len(value)} chars, need {min_len}+)")

    # 6. Check style uniqueness
    style_name = data.get("style_name", "")
    if style_name:
        for other_dir in REF_ROOT.iterdir():
            if other_dir.name == channel_id or other_dir.name.startswith("_"):
                continue
            other_yaml = other_dir / "style.yaml"
            if other_yaml.exists():
                try:
                    other_data = yaml.safe_load(other_yaml.read_text(encoding="utf-8")) or {}
                    if other_data.get("style_name") == style_name:
                        issues.append(f"style_name '{style_name}' is duplicate with {other_dir.name}")
                except Exception:
                    pass

    # 7. Check audience_language is unique
    lang = data.get("audience_language", "")
    if lang:
        for other_dir in REF_ROOT.iterdir():
            if other_dir.name == channel_id or other_dir.name.startswith("_"):
                continue
            other_yaml = other_dir / "style.yaml"
            if other_yaml.exists():
                try:
                    other_data = yaml.safe_load(other_yaml.read_text(encoding="utf-8")) or {}
                    if other_data.get("audience_language") == lang:
                        issues.append(f"audience_language '{lang}' already used by {other_dir.name}")
                except Exception:
                    pass

    # 8. Check cultural_metaphors format (pipe-separated, colon concepts)
    metaphors = str(data.get("cultural_metaphors", "") or "")
    if metaphors and "CHANGE_ME" not in metaphors:
        parts = [p.strip() for p in metaphors.split("|") if p.strip()]
        if len(parts) < 5:
            issues.append(f"cultural_metaphors has only {len(parts)} concepts (need 5+)")
        for part in parts:
            if ":" not in part:
                issues.append(f"cultural_metaphors entry missing ':' separator: {part[:50]}")

    # 9. Check cultural_props has enough items
    props = str(data.get("cultural_props", "") or "")
    if props and "CHANGE_ME" not in props:
        items = [p.strip() for p in props.split(",") if p.strip()]
        if len(items) < 5:
            issues.append(f"cultural_props has only {len(items)} items (need 5+)")

    # 10. Check no-text rules in negative_prompt
    neg = str(data.get("negative_prompt", "") or "").lower()
    if "no readable text" not in neg and "no text" not in neg:
        issues.append("negative_prompt should include 'no readable text'")

    # 11. Check encoding is clean enough for API prompts.
    raw_text = style_path.read_text(encoding="utf-8")
    bad_markers = [marker for marker in MOJIBAKE_MARKERS if marker in raw_text]
    if bad_markers:
        issues.append(f"style.yaml appears to contain mojibake/encoding artifacts: {', '.join(bad_markers[:5])}")

    # 12. Check positive style does not contradict no-text/no-logo policy.
    positive_visual_text = " ".join(str(data.get(k, "") or "") for k in [
        "image_style",
        "video_style",
        "reference_lock",
        "default_character_prompt",
        "default_character_lock",
    ]).lower()
    positive_without_negations = positive_visual_text
    for phrase in [
        "no readable text",
        "no text",
        "no extra text",
        "no watermark",
        "no chest logo or lettering",
        "no logo or lettering",
        "no chest logo",
        "no logo",
        "no lettering",
        "without text",
        "without logo",
        "without lettering",
    ]:
        positive_without_negations = positive_without_negations.replace(phrase, "")
    if ("no readable text" in neg or "no text" in neg) and any(
        term in positive_visual_text
        for term in [" chest logo", " visible logo", " lettering", " readable text", " text on "]
    ):
        if any(
            term in positive_without_negations
            for term in [" chest logo", " visible logo", " lettering", " readable text", " text on "]
        ):
            issues.append("positive style mentions logo/text/lettering while negative_prompt forbids readable text")

    return issues


def discover_all_channels() -> list:
    """Find all TL1-T* and TH1-T* channel directories."""
    channels = []
    for ref_root in REF_ROOTS:
        if ref_root.exists():
            for d in sorted(ref_root.iterdir()):
                if d.is_dir() and not d.name.startswith("_") and d.name not in channels:
                    if (d / "style.yaml").exists() or (d / "nv1.png").exists():
                        channels.append(d.name)
    return channels


def main():
    if len(sys.argv) < 2:
        print("Usage: python validate_channel.py TL1-T11  (psychology)")
        print("       python validate_channel.py TH1-T1   (finance)")
        print("       python validate_channel.py --all")
        sys.exit(1)

    channels = discover_all_channels() if sys.argv[1] == "--all" else [sys.argv[1]]

    total_issues = 0
    for ch in channels:
        issues = validate_channel(ch)
        if issues:
            print(f"\n  [FAIL] {ch}:")
            for issue in issues:
                print(f"     - {issue}")
            total_issues += len(issues)
            continue

        style_path = _find_ref_root(ch) / ch / "style.yaml"
        lang = "?"
        if style_path.exists():
            try:
                data = yaml.safe_load(style_path.read_text(encoding="utf-8")) or {}
                lang = data.get("audience_language", "?")
            except Exception:
                pass
        print(f"  [OK] {ch} ({lang}) - all checks passed")

    print(f"\n{'=' * 60}")
    if total_issues:
        print(f"  [FAIL] {total_issues} issue(s) found across {len(channels)} channel(s)")
    else:
        print(f"  [OK] All {len(channels)} channel(s) validated successfully!")
    print(f"{'=' * 60}")
    sys.exit(1 if total_issues else 0)


if __name__ == "__main__":
    main()
