#!/usr/bin/env python3
"""
_gen_mt2_mt3_channels.py
Generate 20 new channels: MT2-T1..T10 (stick figure), MT3-T1..T10 (nv2 yellow + nv3 navy).
Run from: tools/srt-to-excel/reference_characters/success/
"""
import shutil
import sys
import yaml
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent

LANGUAGES = {
    1: "Spanish", 2: "Vietnamese", 3: "English", 4: "French", 5: "German",
    6: "Portuguese", 7: "Japanese", 8: "Korean", 9: "Italian", 10: "Turkish",
}

NEGATIVE_PROMPT = (
    "no real humans, no photorealism, no 3D, no glossy vector art, "
    "no sterile flat icon look, no neon colors, no readable text, no letters, "
    "no numbers, no captions, no signs, no logos, no watermark"
)

CHAR_MT2 = {
    "source_image": "nv (1).png",
    "full_desc": (
        "minimalist black-and-white stick-figure character matching nv1.png, "
        "thin single-line limbs, round white head, small blocky black eyes, "
        "tiny neutral mouth, short messy hair strands, small oval hands, "
        "small oval feet, no clothing detail"
    ),
    "outfit_video": "no clothing detail, pure black-and-white stick figure preserved exactly",
    "outfit_thumb": "no clothing detail",
    "line_style": "clean even black ink outline",
    "line_short": "black-ink",
    "shadow_plural": "soft gray graphite shadows",
    "anchor": "stick-figure nv1",
    "style_prefix": "stick",
    "palette_prefix": "black ink, white",
    "lock_extra": "thin stick proportions, small blocky eyes, tiny mouth, clean even black ink outline",
    "lock_suffix": "preserve the reference silhouette, proportions, eyes and hair",
    "prep": "with",
}

CHAR_NV2 = {
    "source_image": "nv (2).png",
    "full_desc": (
        "gender-neutral minimalist 2D cartoon character matching nv1.png, "
        "compact rounded body, large round smooth bald head, "
        "small curved closed happy eyes, tiny curved smile, "
        "simple rounded stub hands, thin simple legs, small oval feet, "
        "golden-yellow oversized crewneck T-shirt"
    ),
    "outfit_video": "golden-yellow oversized crewneck T-shirt preserved exactly",
    "outfit_thumb": "golden-yellow T-shirt",
    "line_style": "soft rounded black outline",
    "line_short": "rounded-black",
    "shadow_plural": "soft gray shadows",
    "anchor": "round smooth-headed nv1",
    "style_prefix": "yellow_tee",
    "palette_prefix": "golden yellow",
    "lock_extra": "compact rounded proportions, closed happy eyes, curved smile, soft rounded black outline",
    "lock_suffix": "preserve the reference silhouette, proportions, eyes and outfit",
    "prep": "in",
}

CHAR_NV3 = {
    "source_image": "nv (3).png",
    "full_desc": (
        "gender-neutral minimalist 2D cartoon character matching nv1.png, "
        "confident upright posture, large round white head, "
        "small half-closed confident eyes, subtle smirk, "
        "short messy dark-outlined hair, simple hands, "
        "white low-top sneakers, navy-blue crewneck T-shirt, "
        "cream-white casual trousers"
    ),
    "outfit_video": (
        "navy-blue crewneck T-shirt, cream-white casual trousers, "
        "white low-top sneakers preserved exactly"
    ),
    "outfit_thumb": "navy-blue T-shirt, cream trousers",
    "line_style": "bold clean black outline with white body fill",
    "line_short": "bold-black",
    "shadow_plural": "soft gray shadows",
    "anchor": "confident white-headed nv1",
    "style_prefix": "navy_tee",
    "palette_prefix": "navy blue, cream white",
    "lock_extra": "confident upright proportions, half-closed eyes, subtle smirk, bold clean black outline",
    "lock_suffix": "preserve the reference silhouette, proportions, eyes, hair and outfit",
    "prep": "in",
}

BG_COLORS = {
    "MT2-T1":  "soft peach",
    "MT2-T2":  "warm sand",
    "MT2-T3":  "light warm gray",
    "MT2-T4":  "soft lavender",
    "MT2-T5":  "cool mist blue",
    "MT2-T6":  "pale sage green",
    "MT2-T7":  "blush pink",
    "MT2-T8":  "soft mint",
    "MT2-T9":  "warm vanilla",
    "MT2-T10": "pale eucalyptus",
    "MT3-T1":  "soft sage",
    "MT3-T2":  "light lilac",
    "MT3-T3":  "pale sky blue",
    "MT3-T4":  "soft rose",
    "MT3-T5":  "cool pearl gray",
    "MT3-T6":  "warm apricot",
    "MT3-T7":  "soft coral blush",
    "MT3-T8":  "warm wheat",
    "MT3-T9":  "golden sand",
    "MT3-T10": "warm linen",
}

CULTURAL_FIELDS = [
    "audience_culture_note",
    "cultural_props",
    "cultural_metaphors",
    "cultural_emotion_style",
]


def get_char(channel_id):
    if channel_id.startswith("MT2-"):
        return CHAR_MT2
    n = int(channel_id.split("-T")[1])
    return CHAR_NV2 if n <= 5 else CHAR_NV3


def get_tier(channel_id):
    return int(channel_id.split("-T")[1])


def read_mt1_cultural(tier):
    mt1_path = SCRIPT_DIR / f"MT1-T{tier}" / "style.yaml"
    if not mt1_path.exists():
        raise FileNotFoundError(f"MT1-T{tier}/style.yaml not found")
    data = yaml.safe_load(mt1_path.read_text(encoding="utf-8"))
    return {k: str(data.get(k, "")) for k in CULTURAL_FIELDS}


def sq(s):
    return "'" + s.replace("'", "''") + "'"


def generate_style_yaml(channel_id):
    tier = get_tier(channel_id)
    c = get_char(channel_id)
    bg = BG_COLORS[channel_id]
    lang = LANGUAGES[tier]
    cultural = read_mt1_cultural(tier)

    style_name = f"{c['style_prefix']}_{bg.replace(' ', '_').replace('-', '_')}"
    d = c["full_desc"]
    prep = c["prep"]

    image_style = (
        f"Clean minimalist 2D self-development illustration matching nv1.png - "
        f"{d} - "
        f"{c['line_style']}, flat soft cel shading, {bg} background, "
        f"{c['shadow_plural']}, sparse motivational editorial composition with clear focal hierarchy, "
        f"calm encouraging growth-minded mood, {NEGATIVE_PROMPT}"
    )

    video_style = (
        f"Same clean minimalist 2D self-development illustration style, "
        f"gentle slow hand-drawn motion, {c['outfit_video']}, "
        f"{bg} tint, {c['shadow_plural']}, all props and secondary silhouettes drawn in the same "
        f"{c['line_short']}-outline cel-shaded style"
    )

    thumbnail_style = (
        f"Clean self-development YouTube thumbnail, {c['anchor']} character {prep} {c['outfit_thumb']}, "
        f"{bg} negative space, strong readable motivational pose, "
        f"one simple symbolic growth prop, high-contrast clickable composition, "
        f"no readable text, no watermark"
    )

    scene_plan_style = (
        f"Clean minimalist self-development illustration language, "
        f"{c['anchor']} character {prep} {c['outfit_thumb']}, {bg} space, "
        f"grounded everyday props, motivational body language, "
        f"clear one-glance before/after cause-and-effect"
    )

    palette = f"{c['palette_prefix']}, {bg}, charcoal outline, soft gray shadow"

    reference_lock = (
        f"Use nv1.png as the exact identity and style anchor: "
        f"{d}, {c['lock_extra']}. "
        f"Keep this exact character consistent, no redesign, {c['lock_suffix']}."
    )

    technical_suffix = (
        f"same exact clean minimalist self-development channel style for all figures, "
        f"props, rooms, shadows and symbolic elements, consistent nv1 reference when used, "
        f"{c['line_style']}, soft cel shading, {bg} tint, readable focal hierarchy, "
        f"no readable text, no letters, no numbers, no watermark"
    )

    engagement_rules = (
        f"keep the {c['anchor']} character as the motivational anchor, "
        f"stage one clear everyday prop showing a habit, goal or progress near nv1, "
        f"use posture and soft shadow to show direct before/after cause-and-effect, "
        f"keep the self-development idea instantly readable, "
        f"preserve clean {bg} negative space without making the scene feel empty"
    )

    default_character_prompt = (
        f"Clean minimalist 2D self-development channel character, "
        f"{d}, {c['line_style']}, flat soft cel shading, "
        f"{bg} background, soft gray floor shadow, "
        f"calm encouraging expression, no text, no watermark"
    )

    default_character_lock = f"{d}, {c['line_style']}, flat cel shading"

    lines = [
        f"# Self-development channel {channel_id} - audience: {lang}",
        f"# Topic: success (phat trien ban than / self-development). Generated by _gen_mt2_mt3_channels.py",
        f"style_name: {style_name}",
        f"image_style: {image_style}",
        f"video_style: {video_style}",
        f"thumbnail_style: {thumbnail_style}",
        f"scene_plan_style: {scene_plan_style}",
        f"palette: {palette}",
        f"negative_prompt: {NEGATIVE_PROMPT}",
        f"reference_lock: {sq(reference_lock)}",
        f"technical_suffix: {technical_suffix}",
        f"engagement_rules: {engagement_rules}",
        f"default_character_prompt: {default_character_prompt}",
        f"default_character_lock: {default_character_lock}",
        f"audience_language: {lang}",
        f"audience_culture_note: {sq(cultural['audience_culture_note'])}",
        f"cultural_props: {cultural['cultural_props']}",
        f"cultural_metaphors: {sq(cultural['cultural_metaphors'])}",
        f"cultural_emotion_style: {sq(cultural['cultural_emotion_style'])}",
        "",
    ]
    return "\n".join(lines)


def main():
    if sys.stdout and hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

    channels = [f"MT2-T{i}" for i in range(1, 11)] + [f"MT3-T{i}" for i in range(1, 11)]

    created = 0
    for ch in channels:
        char = get_char(ch)
        ch_dir = SCRIPT_DIR / ch

        ch_dir.mkdir(exist_ok=True)

        src_img = SCRIPT_DIR / char["source_image"]
        dst_img = ch_dir / "nv1.png"
        shutil.copy2(str(src_img), str(dst_img))

        content = generate_style_yaml(ch)
        (ch_dir / "style.yaml").write_text(content, encoding="utf-8")

        tier = get_tier(ch)
        print(f"  [OK] {ch} ({LANGUAGES[tier]}) - {char['style_prefix']} on {BG_COLORS[ch]}")
        created += 1

    print(f"\n{'='*60}")
    print(f"  Created {created} channels successfully!")
    print(f"{'='*60}")


if __name__ == "__main__":
    main()
