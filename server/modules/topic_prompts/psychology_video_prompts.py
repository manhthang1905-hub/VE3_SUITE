"""
Psychology Video topic prompts - Tam ly / Giao duc (VIDEO ONLY).
Phien ban rut gon: chi 1 nhan vat, khong tao anh scene, chi tao video T2V truc tiep.

Flow:
1. Tao 1 nhan vat tham chieu (portrait)
2. Khong tao locations
3. Step 7: chi tao video_prompt (khong co img_prompt)
4. Chrome dung T2V (text-to-video) truc tiep

Style: Minimalist cartoon, round white head, clean black outlines, paper texture.
"""


class PsychologyVideoPrompts:
    """Prompts cho chu de Psychology / Education - VIDEO ONLY mode."""

    TOPIC_NAME = "psychology_video"
    TOPIC_LABEL = "Tam ly Video (Chi tao video)"

    # ========== VIDEO-ONLY FLAG ==========
    def is_video_only(self) -> bool:
        """Video-only mode: khong tao anh scene, chi T2V truc tiep."""
        return True

    # ========== FALLBACK & UTILITIES ==========
    def fallback_style(self) -> str:
        return "Clean cartoon illustration, minimalist style, clean black outlines, paper texture background."

    def fallback_video_style(self) -> str:
        return "Smooth illustration animation, gentle movement, clean cartoon style"

    def split_scene_prompt(self, duration: float, min_shots: int, max_shots: int,
                           srt_start: str, srt_end: str, srt_text: str,
                           visual_moment: str, characters_used: str,
                           location_used: str, char_locks: list, loc_locks: list) -> str:
        return f"""You are creating VIDEO SCENES for an educational YouTube channel. This scene is {duration:.1f} seconds - TOO LONG (max 8s).
Split it into {min_shots}-{max_shots} DISTINCT video clips.

ORIGINAL SCENE:
- Duration: {duration:.1f}s (from {srt_start} to {srt_end})
- Narration: "{srt_text}"
- Visual concept: "{visual_moment}"

AVAILABLE CHARACTERS:
{chr(10).join(char_locks) if char_locks else 'None'}

RULES:
1. Each clip MUST be 4-8 seconds. MINIMUM 4 seconds per clip
2. Each clip shows DIFFERENT aspect of the concept
3. All clips cover the FULL narration
4. Include FULL character description in EVERY clip
5. Use visual metaphors as physical objects character interacts with

Return JSON only:
{{
    "shots": [
        {{
            "shot_number": 1,
            "duration": 5.0,
            "srt_text": "portion of narration",
            "visual_moment": "what the video shows",
            "shot_purpose": "why this clip",
            "characters_used": "{characters_used}",
            "location_used": "",
            "camera": "camera movement"
        }}
    ]
}}"""

    def has_narrator_role(self) -> bool:
        """Psychology khong co narrator rieng."""
        return False

    # ========== STEP 1: Analyze Content ==========
    def step1_analyze(self, sampled_text: str) -> str:
        return f"""Analyze this educational/psychology content for VIDEO ILLUSTRATION.

NOTE: Content is sampled (beginning + middle + end).

CONTENT (SAMPLED):
{sampled_text}

This is an EDUCATIONAL/PSYCHOLOGY video with CARTOON ANIMATION style.
Analyze:
- What psychological concept or life lesson is being taught?
- What emotions and situations are described?
- What visual metaphors can illustrate these concepts?
- What is the main character's journey/transformation?

The visual style uses:
- Cute minimalist character with round white head, simple dot eyes
- Clean black outline animation style
- Visual metaphors as physical objects
- NO TEXT/WORDS in the video
- Warm, accessible, educational aesthetic

Return JSON only:
{{
    "setting": {{
        "era": "modern day",
        "location": "everyday life settings",
        "atmosphere": "warm, relatable, educational"
    }},
    "themes": ["theme1", "theme2", "theme3"],
    "visual_style": {{
        "cinematography": "minimalist cartoon animation, clean black outlines",
        "color_palette": "warm tones, soft pastels, clean whites",
        "lighting": "warm soft lighting"
    }},
    "context_lock": "Cute minimalist cartoon animation, clean black outline style, paper texture background, educational YouTube aesthetic"
}}
"""

    # ========== STEP 2: Segments ==========
    def step2_segments(self, context_lock: str, themes: list, total_duration: float,
                       total_srt: int, sampled_text: str) -> str:
        themes_str = ', '.join(themes) if themes else 'Not specified'
        return f"""Analyze this educational/psychology content and divide into logical segments for VIDEO creation.

CONTENT CONTEXT:
{context_lock}

THEMES: {themes_str}
TOTAL DURATION: {total_duration:.1f} seconds
TOTAL SRT ENTRIES: {total_srt}

CONTENT (SAMPLED):
{sampled_text}

CRITICAL REQUIREMENT:
- Segments MUST cover ALL {total_srt} SRT entries
- First segment starts at srt_range_start: 1
- Last segment MUST end at srt_range_end: {total_srt}
- NO gaps between segments

Return JSON only:
{{
    "segments": [
        {{
            "segment_id": 1,
            "segment_name": "Introduction/Hook",
            "message": "DETAILED description of concept",
            "key_elements": ["visual metaphor", "character emotion", "contrast"],
            "visual_summary": "2-3 sentences describing video scenes",
            "mood": "reflective/anxious/hopeful",
            "characters_involved": ["main character"],
            "estimated_duration": 15.0,
            "srt_range_start": 1,
            "srt_range_end": 25,
            "importance": "high/medium/low"
        }}
    ],
    "summary": "Brief overview"
}}
"""

    # ========== STEP 3: Characters ==========
    def step3_characters(self, setting: dict, context_lock: str,
                         all_characters_mentioned: list, segment_insights: str,
                         targeted_srt_text: str) -> str:
        return f"""Create EXACTLY 1 main character for CARTOON VIDEO ANIMATION.

IMPORTANT: VIDEO-ONLY MODE - Create only ONE character. This character will appear in all video scenes.
Other people will be described as "simple silhouette figures" directly in video prompts.

CONTENT CONTEXT:
- Setting: {setting.get('location', 'Not specified')}
- Visual style: {context_lock}

CONTENT SEGMENTS ANALYSIS:
{segment_insights}

SAMPLE SRT CONTENT:
{targeted_srt_text[:6000] if targeted_srt_text else 'Use segment analysis above'}

CHARACTER DESIGN RULES:
- EXACTLY 1 character - the main protagonist
- Cute minimalist character with round white head, simple dot eyes, gentle expression
- MUST have DISTINCTIVE VISUAL IDENTIFIER (small green sprout, tiny flower, antenna, etc.)
- MUST specify FULL OUTFIT: top + color, bottom + color, footwear + color
- Clean black outline illustration style
- This character will be used as REFERENCE for all video scenes

IMPORTANT: character_lock must include ALL:
- Head: round white head
- Distinctive feature: unique visual identifier
- Eyes: simple dot eyes
- Expression: default expression
- Clothing TOP: garment + color
- Clothing BOTTOM: garment + color
- Footwear: type + color
- Style: clean black outline illustration style

Example: "cute minimalist character with round white head, small green sprout on top, simple dot eyes, gentle expression, blue t-shirt, beige pants, white sneakers, clean black outline illustration style"

Return JSON (EXACTLY 1 character):
{{
    "characters": [
        {{
            "id": "char_1",
            "name": "Main Character",
            "role": "protagonist",
            "portrait_prompt": "Cute minimalist cartoon character, round white head, simple dot eyes, [expression], [distinctive feature], wearing [clothing], standing on pure white background, clean black outline illustration style",
            "character_lock": "cute minimalist character with round white head, [distinctive feature], simple dot eyes, [expression], [clothing details], clean black outline illustration style",
            "is_minor": false
        }}
    ]
}}
"""

    # ========== STEP 4: Locations ==========
    def step4_locations(self, setting: dict, context_lock: str, char_names: list,
                        all_locations_hints: list, segment_insights: str,
                        targeted_srt_text: str) -> str:
        # Video-only mode: khong can locations
        return f"""VIDEO-ONLY MODE: No location reference images needed.
Locations will be described directly in video prompts.

Return empty locations:
{{
    "locations": []
}}
"""

    # ========== STEP 5: Director Plan ==========
    def step5_director_plan(self, image_count: int, seg_name: str, message: str,
                            seg_duration: float, scene_duration: float,
                            min_scene_duration: int, max_scene_duration: int,
                            context_lock: str, char_locks: list, loc_locks: list,
                            srt_text: str) -> str:
        return f"""You are creating VIDEO CLIPS for an educational YouTube channel. Create exactly {image_count} video scenes.

IMPORTANT: Each scene will be a TEXT-TO-VIDEO clip. The description must be SELF-CONTAINED with full character details.

SEGMENT INFO:
- Name: "{seg_name}"
- Message: "{message}"
- Duration: {seg_duration:.1f} seconds total
- Required: EXACTLY {image_count} video clips
- Each clip: {min_scene_duration}-{max_scene_duration} seconds
- MINIMUM {min_scene_duration}s per scene, MAXIMUM {max_scene_duration}s per scene

VISUAL STYLE:
{context_lock}

CHARACTERS (for reference - include full description in every scene):
{chr(10).join(char_locks) if char_locks else 'No characters defined'}

SRT CONTENT:
{srt_text}

VIDEO TECHNIQUES:
- VISUAL METAPHORS: abstract concepts as physical objects (scales, chains, walls, masks)
- CONTRAST SCENES: before/after, split compositions
- CHARACTER EXPRESSIONS: exaggerated emotions
- SILHOUETTE PEOPLE: background/crowd as simple dark silhouettes
- NO TEXT/WORDS in the video

INSTRUCTIONS:
1. EXACTLY {image_count} scenes
2. Each scene VISUALLY REPRESENTS the narration
3. Use visual metaphors for psychology concepts
4. scene_id: 1, 2, 3...
5. MAIN CHARACTER in AT LEAST 80% of scenes
6. characters_used: always include main character ID
7. location_used: leave EMPTY (no location references in video-only mode)

Return JSON only:
{{
    "scenes": [
        {{
            "scene_id": 1,
            "srt_indices": [1, 2, 3],
            "srt_start": "timestamp",
            "srt_end": "timestamp",
            "duration": {scene_duration:.1f},
            "srt_text": "narration text",
            "visual_moment": "DETAILED description of video scene with visual metaphors",
            "characters_used": "nv1",
            "location_used": "",
            "camera": "camera movement (pan, zoom, static, etc.)",
            "lighting": "warm/soft/dramatic"
        }}
    ]
}}
Create exactly {image_count} video clips!"""

    # ========== STEP 6: Scene Planning ==========
    def step6_scene_planning(self, context_lock: str, segments_info: str,
                              char_info: str, loc_info: str, scenes_text: str) -> str:
        return f"""Plan each VIDEO SCENE's visual approach for an educational YouTube channel.

VIDEO-ONLY MODE: Each scene will be generated as TEXT-TO-VIDEO.
Include FULL character description in every scene plan.

VISUAL STYLE: Minimalist cartoon animation, clean black outlines, paper texture background.

CONTENT CONTEXT:
{context_lock}

CHARACTERS (include full description in each scene):
{char_info if char_info else 'Not specified'}

SCENES TO PLAN:
{scenes_text}

For EACH scene, plan:
1. artistic_intent: What concept/emotion? What VISUAL METAPHOR?
2. shot_type: Composition (centered, split screen, close-up, wide)
3. character_action: What is character doing? Expression?
4. mood: Overall feeling
5. lighting: Warm soft / dramatic / contrast
6. color_palette: Dominant colors
7. key_focus: What viewer notices first

TECHNIQUES:
- SPLIT SCREEN comparisons (15-20% of scenes)
- Visual metaphors as PHYSICAL OBJECTS
- ICONS/SYMBOLS instead of text
- Silhouette crowds
- Character emotion close-ups

Return JSON only:
{{
    "scene_plans": [
        {{
            "scene_id": 1,
            "artistic_intent": "Show contrast between phone users and character",
            "shot_type": "Wide scene, centered",
            "character_action": "Character sits calmly while others hold phones",
            "mood": "Calm contrast",
            "lighting": "Warm lighting",
            "color_palette": "Warm earth tones",
            "key_focus": "Contrast between phone users and character"
        }}
    ]
}}
"""

    # ========== STEP 7: Video Prompts (VIDEO-ONLY) ==========
    def step7_scene_prompts(self, context_lock: str, scenes_text: str,
                             batch_size: int) -> str:
        return f"""Create detailed TEXT-TO-VIDEO prompts for these {batch_size} scenes.

#1 PRIORITY - CONTENT MATCHING (QUAN TRONG NHAT):
- The video_prompt MUST illustrate EXACTLY what the narrator is SAYING in the "Text" field
- Read the "Text" field carefully - this is what viewers HEAR. The video MUST match what they hear.
- DO NOT create generic/abstract videos. Each video must be SPECIFIC to its narration content.
- "Visual moment" is a GUIDE, but "Text" is the TRUTH - follow "Text" if they conflict.

VIDEO-ONLY MODE: Each prompt generates a VIDEO directly (no image first).
The video_prompt must be SELF-CONTAINED with FULL character description and scene details.

VISUAL STYLE:
{context_lock}

CRITICAL VIDEO STYLE RULES:
- Clean minimalist cartoon ANIMATION with clean black outlines
- Paper texture background
- Main character: include FULL character_lock description in EVERY prompt
- Other people: "simple silhouette figures"
- Abstract concepts as PHYSICAL OBJECTS character interacts with
- ICONS/SYMBOLS instead of text (heart, question mark, arrows, checkmark)
- NO TEXT/WORDS in video
- NO (nv1.png) or (loc1.png) references - describe everything in text

SCENES TO PROCESS ({batch_size} scenes):
{scenes_text}

CRITICAL REQUIREMENTS:
1. Create EXACTLY {batch_size} video prompts
2. Each video_prompt must be SELF-CONTAINED (include full character description)
3. NO reference file annotations like (nv1.png) - describe character in TEXT
4. Include camera movement and animation details
5. 60-120 words per video_prompt for best T2V results
6. img_prompt must be EMPTY string ""

VIDEO PROMPT STRUCTURE (follow this template):
"[Full character description from character_lock], [action/pose], [scene setting], [visual metaphors/objects], [camera movement], [lighting], clean minimalist cartoon animation, paper texture background"

EXAMPLE video_prompt (GOOD):
"Cute minimalist character with round white head, small green sprout on top, simple dot eyes, gentle expression, blue t-shirt, beige pants, white sneakers, sitting at a restaurant table surrounded by simple silhouette people all holding up smartphones photographing food. Character calmly picks up chopsticks and starts eating. Camera slowly zooms toward character. Warm soft lighting, clean black outline animation style, paper texture background"

EXAMPLE video_prompt (GOOD - visual metaphor):
"Cute minimalist character with round white head, small green sprout on top, simple dot eyes, worried expression, blue t-shirt, beige pants, standing behind giant smartphone screen with app icons arranged like prison bars, hands gripping the bars. Large red exclamation mark icon floats above. Camera slowly pulls back to reveal full scene. Dark blue glow from screen, clean black outline animation style, paper texture background"

EXAMPLE video_prompt (GOOD - split screen):
"SPLIT composition. LEFT warm golden tones: cute minimalist character with round white head, green sprout, blue t-shirt, sitting at real cafe table holding warm coffee cup, genuine smile, sun icon above. RIGHT cold blue tones: same character sitting alone staring at phone, slouched posture, tired eyes, moon icon above. Camera slowly pans from left to right. Clean black outline animation style, paper texture background"

Return JSON only with EXACTLY {batch_size} scenes:
{{
    "scenes": [
        {{
            "scene_id": 1,
            "img_prompt": "",
            "video_prompt": "FULL character description, action, scene, visual metaphors, camera movement, lighting, clean minimalist cartoon animation, paper texture background"
        }}
    ]
}}
"""

    # ========== STEP 8: Thumbnail ==========
    def step8_thumbnail(self, setting: dict, themes: list, visual_style: dict,
                        context_lock: str, protagonist, chars_info: str,
                        locs_info: str, char_ids: list, loc_ids: list) -> str:
        return f"""Create 3 YouTube thumbnail prompts in CARTOON ILLUSTRATION style.

CONTENT CONTEXT:
- Setting: {setting}
- Themes: {themes}
- Context lock: {context_lock}

MAIN CHARACTER: {protagonist.id} ({protagonist.name})
Character description: {protagonist.character_lock or protagonist.english_prompt}

AVAILABLE REFERENCE IDs:
- Characters: {char_ids}

RULES:
1. MUST include (nvX.png) reference for character
2. Include FULL character description in every prompt
3. Thumbnails CAN have TEXT/WORDS for CTR
4. End with: "clean black outline illustration style, paper texture background"

CREATE 3 THUMBNAILS:

VERSION 1 - "portrait_main": Character close-up with strong expression
VERSION 2 - "concept_visual": Visual metaphor scene
VERSION 3 - "youtube_ctr": Maximum CTR with contrast/surprise

Return JSON only:
{{
  "thumbnails": [
    {{
      "thumb_id": 1,
      "version_desc": "portrait_main",
      "img_prompt": "...with (nvX.png)..., clean black outline illustration style, paper texture background",
      "characters_used": "nv1",
      "location_used": ""
    }},
    {{
      "thumb_id": 2,
      "version_desc": "concept_visual",
      "img_prompt": "...",
      "characters_used": "nv1",
      "location_used": ""
    }},
    {{
      "thumb_id": 3,
      "version_desc": "youtube_ctr",
      "img_prompt": "...",
      "characters_used": "nv1",
      "location_used": ""
    }}
  ]
}}
"""
