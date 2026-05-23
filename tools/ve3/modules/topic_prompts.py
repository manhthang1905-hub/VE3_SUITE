"""
modules/topic_prompts.py
Topic-specific prompt templates for ProgressivePromptsGenerator.
Supports: 'story' (narrative/drama) and 'psychology' (educational/documentary)
"""

import json


def get_topic_prompts(topic: str = "story"):
    """Return topic-specific prompt object."""
    if topic == "psychology":
        return PsychologyPrompts()
    return StoryPrompts()  # default


# ============================================================
# BASE CLASS
# ============================================================
class BaseTopicPrompts:
    VISUAL_STYLE = (
        "Photorealistic, cinematic quality, 4K, fine film grain. "
        "Natural lighting, shallow depth of field."
    )

    def fallback_style(self) -> str:
        return self.VISUAL_STYLE

    def has_narrator_role(self) -> bool:
        return False

    def get_default_character(self, override: str = "") -> dict:
        return None


# ============================================================
# STORY PROMPTS  (narrative / drama)
# ============================================================
class StoryPrompts(BaseTopicPrompts):
    VISUAL_STYLE = (
        "Contemporary emotional realism. Sony A7III, Kodak Portra 400 film aesthetic, "
        "natural window lighting, muted warm-tone bias, intimate portrait framing, "
        "4K, fine film grain."
    )

    # ── Step 1: Analyze overall story ──────────────────────────────────────
    def step1_analyze(self, sampled_text: str) -> str:
        return f"""You are a professional cinematographer and story analyst.
Analyze this story/script text and extract key visual information.

STORY TEXT:
{sampled_text}

Return ONLY valid JSON (no markdown, no explanation):
{{
  "setting": {{"era": "contemporary|historical|future", "location": "primary location", "mood": "overall tone"}},
  "themes": ["theme1", "theme2"],
  "context_lock": "One paragraph: the visual world, color palette, lighting mood, and emotional tone that will stay consistent throughout ALL scenes.",
  "characters_mentioned": ["name1", "name2"],
  "locations_mentioned": ["location1", "location2"],
  "total_estimated_scenes": 20
}}"""

    # ── Step 2: Segment story ───────────────────────────────────────────────
    def step2_segments(self, context_lock: str, themes: list,
                       total_duration: float, n_entries: int,
                       sampled_text: str) -> str:
        themes_str = ", ".join(themes) if themes else "drama, emotion"
        return f"""You are a cinematic story structure expert.
Divide this story into 3-7 narrative segments (acts/chapters).

CONTEXT: {context_lock}
THEMES: {themes_str}
TOTAL DURATION: {total_duration:.0f}s ({n_entries} subtitle entries)

STORY TEXT:
{sampled_text}

Return ONLY valid JSON:
{{
  "segments": [
    {{
      "segment_id": 1,
      "segment_name": "Opening",
      "message": "What this segment conveys emotionally",
      "srt_range_start": 1,
      "srt_range_end": 30,
      "estimated_duration": 90.0,
      "image_count": 8,
      "key_insight": "Core visual/emotional insight for this segment"
    }}
  ]
}}"""

    # ── Step 3: Characters ─────────────────────────────────────────────────
    def step3_characters(self, setting: dict, context_lock: str,
                         all_characters_mentioned: list,
                         segment_insights: str, targeted_srt_text: str) -> str:
        chars_str = ", ".join(all_characters_mentioned) if all_characters_mentioned else "unknown"
        setting_str = f"{setting.get('era','contemporary')}, {setting.get('location','')}" if setting else ""
        return f"""You are a casting director and visual artist creating character reference sheets.

STORY SETTING: {setting_str}
CONTEXT: {context_lock}
CHARACTERS MENTIONED: {chars_str}

STORY EXCERPTS:
{targeted_srt_text}

SEGMENT INSIGHTS:
{segment_insights}

Create 2-4 main characters. Each needs a precise, Midjourney-style visual description usable as an image generation reference (NO proper names, describe appearance only).

Return ONLY valid JSON:
{{
  "characters": [
    {{
      "id": "nv1",
      "role": "protagonist",
      "name": "Main Female Lead",
      "gender": "female",
      "age_range": "30-35",
      "appearance": "Detailed physical description: hair, eyes, build, typical dress style",
      "english_prompt": "Portrait of a [age] [ethnicity] woman, [hair], [eyes], [expression], [clothing style], photorealistic, 8K, studio lighting",
      "vietnamese_prompt": "Mo ta tieng Viet",
      "is_child": false
    }}
  ]
}}"""

    # ── Step 4: Locations ──────────────────────────────────────────────────
    def step4_locations(self, setting: dict, context_lock: str,
                        char_names: list, all_locations_hints: list,
                        segment_insights: str, targeted_srt_text: str) -> str:
        locs_str = ", ".join(all_locations_hints) if all_locations_hints else ""
        setting_str = str(setting) if setting else ""
        return f"""You are a production designer creating location reference sheets.

STORY SETTING: {setting_str}
CONTEXT: {context_lock}
LOCATIONS MENTIONED: {locs_str}

STORY EXCERPTS:
{targeted_srt_text}

Create 2-4 key locations as visual reference images (NO proper names, describe space only).

Return ONLY valid JSON:
{{
  "locations": [
    {{
      "id": "loc1",
      "name": "Primary Home Interior",
      "description": "Detailed visual description of the space",
      "english_prompt": "Interior of a [style] [room type], [lighting], [key props], [atmosphere], architectural photography, 8K",
      "vietnamese_prompt": "Mo ta tieng Viet"
    }}
  ]
}}"""

    # ── Step 5: Director plan (scenes from segment) ────────────────────────
    def step5_director_plan(self, image_count: int, seg_name: str,
                            message: str, seg_duration: float,
                            scene_duration: float,
                            min_scene_duration: float, max_scene_duration: float,
                            context_lock: str, char_locks: str,
                            loc_locks: str, srt_text: str) -> str:
        return f"""You are a film director creating a shot-by-shot visual plan.

SEGMENT: "{seg_name}"
EMOTIONAL MESSAGE: {message}
SEGMENT DURATION: {seg_duration:.0f}s → need {image_count} scenes
SCENE DURATION RANGE: {min_scene_duration:.0f}s - {max_scene_duration:.0f}s each

VISUAL CONTEXT: {context_lock}
CHARACTERS: {char_locks}
LOCATIONS: {loc_locks}

SUBTITLE TEXT:
{srt_text}

Plan exactly {image_count} scenes that visually tell this segment. Each scene must have a clear visual action that SHOWS (not tells) the story moment.

Return ONLY valid JSON:
{{
  "scenes": [
    {{
      "scene_id": 1,
      "srt_start": "00:00:00,000",
      "srt_end": "00:00:05,000",
      "duration": 5.0,
      "srt_text": "The subtitle text for this scene",
      "visual_moment": "What the camera sees - specific action/composition",
      "characters_used": "nv1",
      "location_used": "loc1",
      "camera": "MCU 50mm/mDOF/static",
      "lighting": "Warm ambient, AGP",
      "emotional_beat": "isolation, shame"
    }}
  ]
}}"""

    # ── Step 6: Scene planning (refine visual plans) ───────────────────────
    def step6_scene_planning(self, context_lock: str, segments_info: str,
                             char_info: str, loc_info: str,
                             scenes_text: str) -> str:
        return f"""You are a cinematographer refining scene visual plans into detailed shot specifications.

VISUAL CONTEXT: {context_lock}
CHARACTERS: {char_info}
LOCATIONS: {loc_info}

SCENES TO PLAN:
{scenes_text}

For each scene, create a detailed artistic plan that will guide image generation.

Return ONLY valid JSON:
{{
  "scene_plans": [
    {{
      "scene_id": 1,
      "artistic_intent": "The emotional/visual goal of this shot",
      "composition": "Detailed camera composition description",
      "lighting": "Specific lighting setup and mood",
      "color_palette": "Key colors and their emotional role",
      "key_visual_elements": ["element1", "element2"]
    }}
  ]
}}"""

    # ── Step 7: Image + Video prompts ──────────────────────────────────────
    def step7_scene_prompts(self, context_lock: str, scenes_text: str,
                            n_scenes: int) -> str:
        return f"""You are an expert AI image and video prompt engineer.
Generate precise, detailed prompts for {n_scenes} scenes.

VISUAL WORLD: {context_lock}

SCENES:
{scenes_text}

Rules:
- img_prompt: For image generation. Include shot type, lens, DOF, subject action, setting details, lighting, and style.
  ALWAYS reference character images as (nv1.png), (nv2.png) etc. and location as (loc1.png) etc.
  End with: "Photorealistic, 4K, fine film grain, Kodak Portra 400."
- video_prompt: For video generation (5-8 seconds). Describe MOTION: what moves, how, at what pace.
  Focus on subtle, realistic movement. End with: "Photorealistic, 4K, 24fps."
- mp2_visual_action: Short visual description for production reference.
  Format: "Subject action | Setting, time | Key props | Lighting code"
- mp3_shotlist: Production shorthand.
  Format: "[scene_id] SHOT_TYPE LENS/DOF/MOVEMENT. Subject description | Setting | Foreground | Lighting. SRT quote."

Return ONLY valid JSON:
{{
  "scenes": [
    {{
      "scene_id": 1,
      "mp2_visual_action": "Woman (nv1.png) standing rigid... | Kitchen (loc1.png), evening | Props | AG warm",
      "mp3_shotlist": "[1] MCU 50mm/mDOF/static. Woman mid-30s... | Kitchen | ML marble | AG down. \\\"SRT text.\\\"",
      "img_prompt": "Medium close-up, 50mm lens... (nv1.png)... (loc1.png)... Photorealistic, 4K, fine film grain, Kodak Portra 400.",
      "video_prompt": "The woman stands completely still... Photorealistic, 4K, 24fps."
    }}
  ]
}}"""

    # ── Step 8: Thumbnail ──────────────────────────────────────────────────
    def step8_thumbnail(self, setting=None, themes=None, visual_style=None,
                        context_lock: str = "", protagonist=None, chars_info: str = "",
                        locs_info: str = "", char_ids=None, loc_ids=None, title: str = "") -> str:
        setting = setting or {}
        themes = themes or []
        visual_style = visual_style or {}
        protagonist = protagonist or {}
        char_ids = char_ids or []
        loc_ids = loc_ids or []

        def _get_value(obj, key, default=""):
            if isinstance(obj, dict):
                return obj.get(key, default)
            return getattr(obj, key, default)

        setting_str = json.dumps(setting, ensure_ascii=False) if setting else "N/A"
        themes_str = ", ".join(themes) if themes else "drama, curiosity, emotional realism"
        protagonist_id = _get_value(protagonist, "id", char_ids[0] if char_ids else "nv1")
        protagonist_name = _get_value(protagonist, "name", "Main character")
        protagonist_role = _get_value(protagonist, "role", "protagonist")
        visual_style_str = json.dumps(visual_style, ensure_ascii=False) if visual_style else "N/A"
        allowed_chars = ", ".join(char_ids) if char_ids else protagonist_id
        allowed_locs = ", ".join(loc_ids) if loc_ids else "optional"
        location_rule = (
            f"If a location strengthens the concept, include one location reference token such as ({loc_ids[0]}.png). "
            "If not needed, keep the background generic but believable."
            if loc_ids else
            "Do not invent or reference any location token. Keep the background generic, clean, and believable."
        )

        return f"""You are a senior YouTube thumbnail strategist and AI image prompt engineer.
Create exactly 3 thumbnail concepts for a story-driven YouTube video.

Your job is not to write generic image prompts. Your job is to create thumbnail base-image prompts that are:
- photorealistic and believable
- highly clickable in a crowded YouTube feed
- readable in under 1 second at small size
- emotionally charged or curiosity-driven
- visually simple, bold, and design-friendly
- clearly based on the provided character/location references

STORY SETTING: {setting_str}
THEMES: {themes_str}
VISUAL CONTEXT: {context_lock}
VISUAL STYLE HINTS: {visual_style_str}
PROTAGONIST: {protagonist_name} ({protagonist_id}, role={protagonist_role})
CHARACTERS:
{chars_info}

LOCATIONS:
{locs_info}

LOCKED THUMBNAIL TEXT GUIDANCE:
{title or "(none provided)"}

Available character ids: {allowed_chars}
Available location ids: {allowed_locs}

Thumbnail rules:
- Use mostly 1 dominant subject. 2 subjects maximum only if conflict or tension is visually essential.
- The protagonist should usually be the largest and clearest subject in frame.
- Build a strong visual hook: shock, tension, danger, discovery, emotional pain, secret, confrontation, or impossible-to-ignore moment.
- Favor close-up, medium close-up, or tight medium shots over wide scenic shots.
- Keep the background simple and supportive, never cluttered.
- This must be a complete thumbnail prompt, not just a base image prompt.
- Reserve about 65-70% of the frame for large on-image thumbnail text, and place the visual subject mostly within the remaining 30-35%.
- Compose off-center. Do not put the subject in the middle if that reduces text space.
- The image must feel like real commercial/editorial photography that has been art directed for YouTube, not like fantasy art or raw AI art.
- Use believable lighting, realistic skin and material texture, natural imperfections, and real-world depth.
- Avoid plastic skin, surreal effects, over-stylized bokeh, excessive glow, fake HDR, CGI look, and generic stock photo vibes.
- CAMERA REALISM: Every img_prompt must specify a real camera body and lens combination (e.g. Canon EOS R5 with 85mm f/1.4, Sony A7IV with 35mm f/1.8, Nikon Z6 with 50mm f/1.2) to anchor the image in photographic reality.
- FILM TEXTURE: Include a film stock or color science reference (e.g. Kodak Portra 400, Fujifilm Pro 400H color science, CineStill 800T tungsten cast) to give the image organic color rendering instead of digital perfection.
- SKIN AND MATERIAL: Explicitly request visible skin pores, fine peach fuzz, subtle undereye texture, natural lip moisture, real fabric weave, and micro-wrinkles. Mention "shot on location" or "behind-the-scenes editorial" energy to push realism.
- LIGHTING SPECIFICITY: Instead of generic "dramatic lighting", specify a real lighting setup (e.g. single large softbox at 45 degrees camera-left with natural fill from a window, golden hour backlight with negative fill, overhead practical fluorescent with bounce card).
- ANTI-AI RENDERING: Include this clause in every prompt: "Avoid AI-generated look, avoid airbrushed skin, avoid symmetrical perfection, avoid uncanny valley eyes, avoid oversaturated colors, avoid plastic hair texture."
- The prompt must explicitly instruct the model to render bold on-image thumbnail text.
- If locked thumbnail text guidance is provided, preserve that on-image text exactly. Do not invent a new headline. Your job is to improve the visual illustration around the provided text, not to rewrite the text.
- Every img_prompt must explicitly include reference tokens like ({protagonist_id}.png) and use only allowed ids when needed.
- {location_rule}
- The target audience skews older adults, often age 50+, so prioritize dignity, emotional clarity, seriousness, curiosity, and trust over loud youth-style clickbait.
- The hook should feel intelligent and human, not exaggerated or trashy.
- Do not invent any new character id or location id outside the allowed ids.

Create 3 distinct concepts:
1. Character Hook: strongest face/emotion-driven version.
2. Conflict Hook: a tense, story-rich moment with immediate curiosity.
3. CTR Hook: the most aggressively clickable but still realistic and tasteful version.

For each concept:
- version_desc must be short and specific.
- img_prompt must be one strong English production prompt for a 16:9 YouTube thumbnail base image.
- img_prompt_portrait must be a 9:16 adaptation of the same concept.
- characters_used must list the ids used, comma-separated.
- location_used must list the main location id if used, otherwise empty.

Prompt-writing requirements for img_prompt and img_prompt_portrait:
- Explicitly say the image is a photorealistic complete YouTube thumbnail.
- Emphasize instant clarity, strong focal point, high subject separation, and click appeal.
- Mention realistic commercial photography, editorial realism, believable dramatic lighting, and clean composition.
- Mention what exact emotion/action/moment the viewer sees.
- Explicitly mention a large text block covering about two-thirds of the frame and a subject cluster occupying about one-third.
- Start the prompt by specifying a real camera and lens (e.g. "Shot on Canon EOS R5, 85mm f/1.4") and a film stock or color science (e.g. "Kodak Portra 400 color rendering"). This is critical to anchor photographic realism.
- Include realistic skin/material detail language: "visible skin pores, natural undereye texture, fine peach fuzz, real fabric weave, subtle skin imperfections."
- Specify a concrete lighting setup rather than vague terms: e.g. "large softbox at 45° camera-left with warm window fill" instead of just "dramatic lighting."
- Include an instruction in this exact pattern:
  Render on-image text EXACTLY as this content only: <headline text>
  Highlight words: <3 important words>
  Font: EXTRA-BOLD sans-serif, white + yellow emphasis, heavy shadow and stroke for mobile readability.
- When locked text guidance is provided, the <headline text> and highlight words must match that guidance exactly.
- The headline text must be short, emotionally strong, curiosity-driven, easy for viewers over 50 to process quickly, and written like a real YouTube thumbnail.
- Add a short avoid clause inside the prompt such as: No watermark, no logo, no extra text clutter, no extra fingers, no distorted anatomy, no AI-looking texture, no airbrushed skin, no symmetrical perfection, no uncanny valley eyes, no plastic hair.

Return ONLY valid JSON:
{{
  "thumbnails": [
    {{
      "thumb_id": 1,
      "version_desc": "Character Hook",
      "img_prompt": "English prompt only",
      "img_prompt_portrait": "English prompt only",
      "characters_used": "{protagonist_id}",
      "location_used": ""
    }},
    {{
      "thumb_id": 2,
      "version_desc": "Conflict Hook",
      "img_prompt": "English prompt only",
      "img_prompt_portrait": "English prompt only",
      "characters_used": "{protagonist_id}",
      "location_used": ""
    }},
    {{
      "thumb_id": 3,
      "version_desc": "CTR Hook",
      "img_prompt": "English prompt only",
      "img_prompt_portrait": "English prompt only",
      "characters_used": "{protagonist_id}",
      "location_used": ""
    }}
  ]
}}"""

    # ── Split scene ────────────────────────────────────────────────────────
    def split_scene_prompt(self, duration: float, min_shots: int, max_shots: int,
                           srt_start: str, srt_end: str, srt_text: str,
                           visual_moment: str, characters_used: str,
                           location_used: str, char_locks: str,
                           loc_locks: str) -> str:
        return f"""Split this long scene ({duration:.1f}s) into {min_shots}-{max_shots} shorter shots.

ORIGINAL SCENE:
- Time: {srt_start} → {srt_end} ({duration:.1f}s)
- Text: {srt_text}
- Visual moment: {visual_moment}
- Characters: {characters_used}
- Location: {location_used}

CHARACTERS: {char_locks}
LOCATIONS: {loc_locks}

Each shot should be {duration/max(min_shots,1):.1f}-{duration/max(max_shots,1):.1f}s.
Create natural visual progression (e.g., wide → medium → close).

Return ONLY valid JSON:
{{
  "shots": [
    {{
      "shot_num": 1,
      "duration": {duration/2:.1f},
      "srt_text": "First part of text",
      "visual_moment": "What camera sees in this shot",
      "camera": "WS 35mm/dF/static",
      "characters_used": "{characters_used}",
      "location_used": "{location_used}"
    }}
  ]
}}"""

    def fallback_style(self) -> str:
        return self.VISUAL_STYLE

    def has_narrator_role(self) -> bool:
        return False

    def get_default_character(self, override: str = "") -> dict:
        return None


# ============================================================
# PSYCHOLOGY PROMPTS  (educational / documentary)
# ============================================================
class PsychologyPrompts(BaseTopicPrompts):
    VISUAL_STYLE = (
        "Educational documentary style. Clean, modern, high-contrast. "
        "Soft studio lighting, neutral backgrounds, professional look. 4K."
    )

    def step1_analyze(self, sampled_text: str) -> str:
        return f"""Analyze this psychology/educational content.

TEXT:
{sampled_text}

Return ONLY valid JSON:
{{
  "setting": {{"era": "contemporary", "location": "educational setting", "mood": "informative, engaging"}},
  "themes": ["psychology concept 1", "concept 2"],
  "context_lock": "Clean educational visual world: minimalist backgrounds, professional lighting, thoughtful graphics.",
  "characters_mentioned": [],
  "locations_mentioned": ["studio", "office"],
  "total_estimated_scenes": 20
}}"""

    def step2_segments(self, context_lock, themes, total_duration, n_entries, sampled_text):
        return f"""Divide this educational content into 3-5 topic segments.

CONTEXT: {context_lock}
DURATION: {total_duration:.0f}s

CONTENT:
{sampled_text}

Return ONLY valid JSON:
{{"segments": [{{"segment_id": 1, "segment_name": "Introduction", "message": "Core concept introduced",
"srt_range_start": 1, "srt_range_end": 20, "estimated_duration": 60.0,
"image_count": 5, "key_insight": "Visual metaphor for this concept"}}]}}"""

    def step3_characters(self, setting, context_lock, all_characters_mentioned,
                         segment_insights, targeted_srt_text):
        return f"""Create narrator/presenter character for this educational video.

CONTEXT: {context_lock}

Return ONLY valid JSON:
{{"characters": [{{"id": "nv1", "role": "narrator", "name": "Narrator",
"gender": "neutral", "age_range": "25-40",
"appearance": "Professional presenter, smart casual attire",
"english_prompt": "Portrait of a professional presenter, smart casual outfit, neutral background, studio lighting, photorealistic, 8K",
"vietnamese_prompt": "Nguoi dan chuong trinh chuyen nghiep", "is_child": false}}]}}"""

    def step4_locations(self, setting, context_lock, char_names,
                        all_locations_hints, segment_insights, targeted_srt_text):
        return f"""Create location references for educational video.

Return ONLY valid JSON:
{{"locations": [{{"id": "loc1", "name": "Studio Background",
"description": "Clean minimalist studio with subtle gradient background",
"english_prompt": "Minimalist studio background, soft gradient, professional, clean, 8K",
"vietnamese_prompt": "Phong nghi hinh chuyen nghiep"}}]}}"""

    def step5_director_plan(self, image_count, seg_name, message, seg_duration,
                            scene_duration, min_scene_duration, max_scene_duration,
                            context_lock, char_locks, loc_locks, srt_text):
        return f"""Create visual plan for educational segment "{seg_name}".
MESSAGE: {message}
Need {image_count} scenes from: {srt_text}

Return ONLY valid JSON:
{{"scenes": [{{"scene_id": 1, "srt_start": "00:00:00,000", "srt_end": "00:00:05,000",
"duration": 5.0, "srt_text": "Text", "visual_moment": "Presenter explaining concept",
"characters_used": "nv1", "location_used": "loc1",
"camera": "MCU 50mm/mDOF/static", "lighting": "Studio key light",
"emotional_beat": "informative, clear"}}]}}"""

    def step6_scene_planning(self, context_lock, segments_info, char_info, loc_info, scenes_text):
        return f"""Plan these educational scenes visually.
SCENES: {scenes_text}

Return ONLY valid JSON:
{{"scene_plans": [{{"scene_id": 1, "artistic_intent": "Clear visual communication",
"composition": "Clean presenter shot", "lighting": "Professional studio",
"color_palette": "Neutral professional", "key_visual_elements": ["presenter", "graphics"]}}]}}"""

    def step7_scene_prompts(self, context_lock, scenes_text, n_scenes):
        return f"""You are an expert AI image and video prompt engineer specializing in educational psychology content.

Your task: Generate precise, detailed prompts for {n_scenes} scenes that will create visually consistent, emotionally engaging educational videos.

VISUAL WORLD CONTEXT:
{context_lock}

SCENES TO PROCESS:
{scenes_text}

---

CRITICAL INSTRUCTIONS FOR IMAGE PROMPTS (img_prompt):

1. VISUAL STYLE FOUNDATION:
   - Specify the exact visual style from context_lock
   - Describe character design in EXTREME DETAIL:
     * Head shape, size, proportions
     * Eyes: shape, size, color, expression (e.g., "tiny black dot eyes, no eyebrows")
     * Mouth: shape, size (e.g., "tiny simple curved mouth line only")
     * Body: proportions, posture (e.g., "smooth rounded head merging into torso, no neck")
     * Limbs: shape, length (e.g., "long narrow tubular limbs")
     * Hands/feet: shape, details (e.g., "small drooping rounded hands with minimal finger suggestion")
     * Clothing: describe or state "no clothes, no accessories"
   - Background: color, texture, style (e.g., "warm off-white paper background")
   - Lighting: type, direction (e.g., "soft pencil shading, subtle ground shadow")
   - ALWAYS reference character images as (nv1.png), (nv2.png) etc.

2. STORYTELLING RULES:
   - All secondary figures must use the same visual style as main character
   - Props and environment must match the established style
   - Keep composition clear and focused on the educational message

3. SHOT COMPOSITION:
   - Specify shot type: wide shot, medium shot, close-up, etc.
   - Specify composition: centered, off-center, rule of thirds, etc.
   - Consider the emotional impact and educational clarity

4. PROHIBITIONS - List ALL forbidden elements:
   - never real humans, never photoreal
   - absolutely no readable text, no letters, no numbers
   - no captions, no subtitles, no signs, no logos, no watermarks
   - no interface text, no menus

5. CHARACTER CONSISTENCY WARNINGS:
   - Explicitly state what must NOT change from reference
   - Example: "never make the eyes oval, never add eyebrows, never change the reference silhouette"

6. END WITH STYLE TAG:
   - For illustration style: "Simple illustration, educational, clean, 4K."
   - For photorealistic: "Photorealistic, 4K, fine film grain, Kodak Portra 400."

---

CRITICAL INSTRUCTIONS FOR VIDEO PROMPTS (video_prompt):

IMPORTANT: Your video_prompt MUST include the text "[SECTION 2]", "[SECTION 3]", "[SECTION 4]", "[SECTION 5]", "[SECTION 6]", "[SECTION 7]" as literal markers in the output. These are NOT instructions to remove - they are REQUIRED text that must appear in the final prompt.

VIDEO PROMPT STRUCTURE (follow this EXACT order and include the section markers):

**SECTION 1: VISUAL STYLE FOUNDATION - Copy from img_prompt**
Start by copying the character description and visual style from img_prompt.
Example:
"Hand-drawn pencil sketch animation in the same exact visual language as the provided reference image,
do not redesign the main character, with the same pale desaturated sage-gray mascot, smooth rounded head
merging directly into the torso, no neck, tiny black dot eyes, no eyebrows, tiny simple curved mouth or
tiny mouth line only, no nose, no ears, long narrow tubular limbs, small drooping rounded hands with
minimal finger suggestion, slightly oversized rounded feet, no clothes, no accessories, warm off-white
paper background, thin graphite outlines, soft pencil shading, subtle ground shadow"

Then add the literal text "[SECTION 2]" followed by:
"concrete visual storytelling, specific props and surroundings, simple secondary figures in the same
mascot style, all figures must stay in the same stylized mascot universe, never real humans, never photoreal"

Then add the literal text "[SECTION 3]" followed by:
"absolutely no readable text, no letters, no numbers, no captions, no subtitles, no signs, no logos,
no watermarks"

Then add the literal text "[SECTION 4]" followed by:
"never make the eyes oval, never add eyebrows, never change the reference silhouette"

Then add the literal text "[SECTION 5]" followed by:
"Faithfully visualize this narration beat in a direct and instantly legible way: [INSERT EXACT SRT TEXT HERE]."

Then add the literal text "[SECTION 6]" followed by:
Describe the SPECIFIC movement and action:
- What the character does (e.g., "The centered mascot enters the room, hears a fragment of conversation, freezes subtly")
- How the environment reacts (e.g., "the social energy around them shifts into quiet tension immediately")
- Camera angle and shot type (e.g., "first-impression radar room shot, eye-level medium shot, split-vignette explanatory layout")

Then add the literal text "[SECTION 7]" followed by:
"Use a composition, pose, and supporting props that stay clearly distinct from adjacent scenes."

---

EXAMPLE OUTPUT FORMAT:

Return ONLY valid JSON:
{{
  "scenes": [
    {{
      "scene_id": 1,
      "mp2_visual_action": "Character (nv1.png) performing action | Setting (loc1.png) | Props | Lighting",
      "mp3_shotlist": "[1] SHOT_TYPE LENS/DOF. Character description | Setting | Props | Lighting. \\"SRT quote.\\"",

      "img_prompt": "Medium close-up, centered composition. [DETAILED CHARACTER DESCRIPTION from context_lock]. Character is [specific pose/action related to SRT]. [BACKGROUND DESCRIPTION]. [LIGHTING DESCRIPTION]. Reference: (nv1.png). Simple illustration, educational, clean, 4K. absolutely no readable text, no letters, no numbers, no captions, no subtitles, no signs, no logos, no watermarks.",

      "video_prompt": "Hand-drawn pencil sketch animation in the same exact visual language as (nv1.png), smooth rounded head, tiny black dot eyes, no eyebrows, tiny mouth line, no nose, no ears, long narrow limbs, warm off-white background, thin graphite outlines, soft pencil shading. [SECTION 2] concrete visual storytelling, specific props, simple secondary figures in the same mascot style, never real humans, never photoreal. [SECTION 3] absolutely no readable text, no letters, no numbers, no captions, no subtitles, no signs, no logos, no watermarks. [SECTION 4] never make the eyes oval, never add eyebrows, never change the reference silhouette. [SECTION 5] Faithfully visualize this narration beat: [INSERT EXACT SRT TEXT HERE]. [SECTION 6] The character [describe specific action and movement], [environment reaction], Camera: [shot type, angle, movement]. [SECTION 7] Use a composition, pose, and supporting props that stay clearly distinct from adjacent scenes."
    }}
  ]
}}

---

QUALITY CHECKLIST - Verify each prompt has:

For img_prompt:
✓ Detailed character description (head, eyes, mouth, body, limbs, hands, feet)
✓ Visual style specification
✓ Shot type and composition
✓ Background and lighting
✓ Character reference (nv1.png)
✓ Complete prohibition list
✓ Style tag at end

For video_prompt:
✓ Section 1: Visual style foundation (copied from img_prompt)
✓ Section 2: Storytelling rules
✓ Section 3: Text prohibition (complete list)
✓ Section 4: Character consistency warnings
✓ Section 5: "Faithfully visualize this narration beat: [SRT TEXT]"
✓ Section 6: Specific action + camera angle + shot type
✓ Section 7: Scene distinction requirement

---

Now generate prompts for all {n_scenes} scenes following these instructions EXACTLY."""

    def step8_thumbnail(self, setting=None, themes=None, visual_style=None,
                        context_lock: str = "", protagonist=None, chars_info: str = "",
                        locs_info: str = "", char_ids=None, loc_ids=None, title: str = ""):
        setting = setting or {}
        themes = themes or []
        visual_style = visual_style or {}
        protagonist = protagonist or {}
        char_ids = char_ids or []
        loc_ids = loc_ids or []

        def _get_value(obj, key, default=""):
            if isinstance(obj, dict):
                return obj.get(key, default)
            return getattr(obj, key, default)

        protagonist_id = _get_value(protagonist, "id", char_ids[0] if char_ids else "nv1")
        allowed_chars = ", ".join(char_ids) if char_ids else protagonist_id
        allowed_locs = ", ".join(loc_ids) if loc_ids else "optional"
        themes_str = ", ".join(themes) if themes else "psychology, human behavior, self-improvement"
        visual_style_str = json.dumps(visual_style, ensure_ascii=False) if visual_style else "N/A"
        location_rule = (
            f"If environment context helps, use one location token such as ({loc_ids[0]}.png), but keep it understated."
            if loc_ids else
            "Do not invent or reference any location token. Keep the environment subtle, believable, and generic."
        )

        return f"""You are a senior YouTube thumbnail designer for educational psychology content.
Create exactly 3 highly clickable thumbnail concepts using ILLUSTRATION/ANIMATION style.

CONTEXT: {context_lock}
SETTING: {json.dumps(setting, ensure_ascii=False) if setting else "N/A"}
THEMES: {themes_str}
VISUAL STYLE (MUST FOLLOW): {visual_style_str}
MAIN PRESENTER / CHARACTER: {protagonist_id}
CHARACTERS:
{chars_info}

LOCATIONS:
{locs_info}

LOCKED THUMBNAIL TEXT GUIDANCE:
{title or "(none provided)"}
Available character ids: {allowed_chars}
Available location ids: {allowed_locs}

CRITICAL STYLE REQUIREMENTS:
This channel uses ILLUSTRATION/ANIMATION style, NOT photorealistic photography.
- Style: Simple, clean illustration or 2D animation aesthetic
- Characters: Stylized, minimalist figures (can be stick figures, simple shapes, or cartoon-style)
- Colors: Flat colors or simple gradients, muted/warm palette, professional but approachable
- Background: Clean, minimal, simple geometric shapes or soft gradients
- Lighting: Flat or simple shading, no complex photorealistic lighting
- Texture: Smooth, clean, vector-like or hand-drawn illustration feel
- Overall: Think "educational animation" or "explainer video" aesthetic, NOT stock photography

If VISUAL STYLE specifies illustration details (color palette, character style, background style), follow those exactly.

COMPOSITION RULES:
- Reserve 50-70% of frame for LARGE BOLD TEXT (black or dark text with yellow/orange highlights)
- Place visual elements in remaining 30-50% of frame
- Keep composition EXTREMELY SIMPLE - one clear concept, one main character/object
- Text should be the dominant element, visual supports the message
- Use off-center composition to leave space for text
- Standard 16:9 YouTube thumbnail format

CHARACTER RULES:
- Use simple, stylized character design (not realistic human)
- Character can be: stick figure, simple cartoon, minimalist illustration, or abstract representation
- Expression should be clear and exaggerated for emotional impact
- Keep character design consistent with channel style
- Every prompt must include ({protagonist_id}.png) reference
- {location_rule}

CONCEPT REQUIREMENTS:
- ONE clear psychological concept per thumbnail
- Visual metaphor or symbolic representation (e.g., person alone = loneliness, tangled lines = overthinking)
- Immediate emotional recognition (viewer understands concept in 1 second)
- Avoid clutter, multiple subjects, or complex scenes
- Target audience: 50+ years old, seeking insight and self-understanding
- Tone: Thoughtful, relatable, non-judgmental, educational

TEXT INTEGRATION:
Each img_prompt must include this instruction:
"Render large bold on-image text in Spanish:
HEADLINE: <main text in CAPS, black or dark color>
HIGHLIGHT: <2-3 key words in yellow/orange>
Font: Extra-bold sans-serif, high contrast, mobile-readable
Text placement: <specify left/right/top/bottom based on visual composition>"

When locked text guidance is provided, use that exact text. Otherwise create compelling psychological hook text.

AVOID:
- Photorealistic style, real photography, camera/lens references
- Complex lighting, shadows, or 3D rendering
- Detailed realistic faces or anatomy
- Cluttered backgrounds or multiple elements
- Childish or cartoonish exaggeration (keep it mature and thoughtful)
- Generic stock illustration vibes

CREATE 3 CONCEPTS:
1. Emotional Isolation Hook - Focus on loneliness, disconnection, or feeling misunderstood
2. Behavioral Pattern Hook - Focus on habits, routines, or repetitive behaviors
3. Self-Discovery Hook - Focus on realization, breakthrough, or hidden truth

For each concept, provide:
- version_desc: Short description of the hook
- img_prompt: Complete English prompt for 16:9 illustration-style thumbnail
- img_prompt_portrait: 9:16 vertical adaptation
- characters_used: "{protagonist_id}"
- location_used: "" (or location id if used)

PROMPT STRUCTURE:
Start each img_prompt with:
"Illustration style YouTube thumbnail for psychology content. [Describe the visual concept]. Simple, clean, minimalist design. Flat colors, [specify color palette]. [Describe character pose/expression]. [Describe background]. Large bold Spanish text: [specify text and placement]. Professional educational aesthetic. 16:9 format."

Return ONLY valid JSON:
{{
  "thumbnails": [
    {{
      "thumb_id": 1,
      "version_desc": "Emotional Isolation",
      "img_prompt": "English prompt only",
      "img_prompt_portrait": "English prompt only",
      "characters_used": "{protagonist_id}",
      "location_used": ""
    }},
    {{
      "thumb_id": 2,
      "version_desc": "Behavioral Pattern",
      "img_prompt": "English prompt only",
      "img_prompt_portrait": "English prompt only",
      "characters_used": "{protagonist_id}",
      "location_used": ""
    }},
    {{
      "thumb_id": 3,
      "version_desc": "Self-Discovery",
      "img_prompt": "English prompt only",
      "img_prompt_portrait": "English prompt only",
      "characters_used": "{protagonist_id}",
      "location_used": ""
    }}
  ]
}}"""

    def split_scene_prompt(self, duration, min_shots, max_shots, srt_start, srt_end,
                           srt_text, visual_moment, characters_used, location_used,
                           char_locks, loc_locks):
        return f"""Split educational scene ({duration:.1f}s) into {min_shots}-{max_shots} shots.
TEXT: {srt_text}

Return ONLY valid JSON:
{{"shots": [{{"shot_num": 1, "duration": {duration/2:.1f}, "srt_text": "{srt_text[:80]}",
"visual_moment": "Presenter making key point", "camera": "MCU 50mm",
"characters_used": "{characters_used}", "location_used": "{location_used}"}}]}}"""

    def has_narrator_role(self) -> bool:
        return True

    def get_default_character(self, override: str = "") -> dict:
        if override:
            return {"id": "nv1", "role": "narrator", "english_prompt": override}
        return {"id": "nv1", "role": "narrator",
                "english_prompt": "Professional presenter, smart casual, studio lighting, photorealistic"}
