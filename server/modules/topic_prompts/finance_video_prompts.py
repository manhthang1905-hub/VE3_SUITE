"""
Finance History Video topic prompts - Lich su Tai chinh (VIDEO ONLY).
Phien ban rut gon: chi 1 nhan vat narrator, khong tao anh scene, chi tao video T2V truc tiep.

Flow:
1. Tao 1 nhan vat narrator tham chieu (portrait)
2. Khong tao locations
3. Step 7: chi tao video_prompt (khong co img_prompt)
4. Chrome dung T2V (text-to-video) truc tiep

Style: Detailed cartoon illustration, editorial comic, warm color palette.
"""


class FinanceVideoPrompts:
    """Prompts cho chu de Finance History - VIDEO ONLY mode."""

    TOPIC_NAME = "finance_video"
    TOPIC_LABEL = "Tai chinh Video (Chi tao video)"

    # ========== VIDEO-ONLY FLAG ==========
    def is_video_only(self) -> bool:
        """Video-only mode: khong tao anh scene, chi T2V truc tiep."""
        return True

    # ========== FALLBACK & UTILITIES ==========
    def fallback_style(self) -> str:
        return "Detailed cartoon illustration, warm color palette, editorial illustration style, soft warm lighting."

    def fallback_video_style(self) -> str:
        return "Smooth cartoon animation, gentle camera panning, warm atmosphere"

    def split_scene_prompt(self, duration: float, min_shots: int, max_shots: int,
                           srt_start: str, srt_end: str, srt_text: str,
                           visual_moment: str, characters_used: str,
                           location_used: str, char_locks: list, loc_locks: list) -> str:
        return f"""You are creating VIDEO SCENES for a financial history YouTube channel. This scene is {duration:.1f} seconds - TOO LONG (max 8s).
Split it into {min_shots}-{max_shots} DISTINCT video clips.

ORIGINAL SCENE:
- Duration: {duration:.1f}s (from {srt_start} to {srt_end})
- Narration: "{srt_text}"
- Visual concept: "{visual_moment}"

AVAILABLE CHARACTERS:
{chr(10).join(char_locks) if char_locks else 'None'}

RULES:
1. Each clip MUST be 4-8 seconds. MINIMUM 4 seconds
2. Each clip shows DIFFERENT aspect of the economic concept
3. All clips cover the FULL narration
4. Include FULL character description in EVERY clip
5. Use charts, maps, historical scenes to illustrate

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
        """Finance history co narrator/historian role."""
        return True

    # ========== STEP 1: Analyze Content ==========
    def step1_analyze(self, sampled_text: str) -> str:
        return f"""Analyze this financial history / economics content for VIDEO creation.

NOTE: Content is sampled (beginning + middle + end).

CONTENT (SAMPLED):
{sampled_text}

This is a FINANCIAL HISTORY video with DETAILED CARTOON ANIMATION style.
Analyze:
- What economic or historical topic is discussed?
- What countries, time periods, policies are covered?
- What data, statistics, indicators are mentioned?
- What is the main argument or thesis?

Visual style:
- Detailed cartoon animation (editorial comics, Studio Ghibli aesthetic)
- Warm color palette (golden, amber, earth tones)
- Rich detailed backgrounds
- Data as VISUAL CHARTS with numbers (NO text labels)
- Historical scenes in cartoon style
- NO TEXT/WORDS in video

Return JSON only:
{{
    "setting": {{
        "era": "historical period and modern analysis",
        "location": "countries and settings",
        "atmosphere": "intellectual, warm, educational"
    }},
    "themes": ["theme1", "theme2", "theme3"],
    "visual_style": {{
        "cinematography": "detailed cartoon animation, editorial style",
        "color_palette": "warm golden tones, amber, earth tones",
        "lighting": "warm soft lighting, golden hour"
    }},
    "context_lock": "Detailed cartoon animation, editorial style, warm color palette, rich detailed backgrounds, soft warm lighting"
}}
"""

    # ========== STEP 2: Segments ==========
    def step2_segments(self, context_lock: str, themes: list, total_duration: float,
                       total_srt: int, sampled_text: str) -> str:
        themes_str = ', '.join(themes) if themes else 'Not specified'
        return f"""Analyze this financial history content and divide into logical segments for VIDEO creation.

CONTENT CONTEXT:
{context_lock}

THEMES: {themes_str}
TOTAL DURATION: {total_duration:.1f} seconds
TOTAL SRT ENTRIES: {total_srt}

CONTENT (SAMPLED):
{sampled_text}

CRITICAL REQUIREMENT:
- Segments MUST cover ALL {total_srt} SRT entries
- First segment: srt_range_start: 1
- Last segment MUST end at srt_range_end: {total_srt}
- NO gaps

Return JSON only:
{{
    "segments": [
        {{
            "segment_id": 1,
            "segment_name": "Introduction/Hook",
            "message": "DETAILED description of economic argument",
            "key_elements": ["historical scene", "data visualization", "map"],
            "visual_summary": "2-3 sentences describing video scenes",
            "mood": "analytical/dramatic/surprising",
            "characters_involved": ["narrator"],
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
        return f"""Create EXACTLY 1 narrator/historian character for CARTOON VIDEO ANIMATION.

IMPORTANT: VIDEO-ONLY MODE - Create only ONE narrator character.
This character will appear in all video scenes as the host/historian.
Historical figures will be described directly in video prompts (no separate reference).

CONTENT CONTEXT:
- Setting: {setting.get('location', 'Not specified')}
- Visual style: {context_lock}

CONTENT SEGMENTS ANALYSIS:
{segment_insights}

SAMPLE SRT CONTENT:
{targeted_srt_text[:6000] if targeted_srt_text else 'Use segment analysis above'}

CHARACTER DESIGN RULES:
- EXACTLY 1 narrator character
- Detailed cartoon character (NOT minimalist - rich, warm, like editorial comics)
- Warm, intellectual (professor, historian, researcher type)
- MUST have DISTINCTIVE FEATURE (specific glasses, bow tie, vest, beard, etc.)
- MUST specify FULL OUTFIT: top + color, bottom + color, footwear + color
- Detailed cartoon illustration style
- This will be the ONLY reference image created

IMPORTANT: character_lock must include ALL:
- Face: specific features
- Hair: style and color
- Distinctive feature: glasses, beard, bow tie, etc.
- Eyes: style and color
- Expression: thoughtful, warm, analytical
- Clothing TOP: garment + color
- Clothing BOTTOM: garment + color
- Footwear: type + color
- Style: detailed cartoon illustration style, warm color palette

Example: "warm friendly cartoon historian with grey wavy hair, round glasses, kind brown eyes, thoughtful expression, mustard yellow sweater over white collared shirt, dark brown trousers, brown oxford shoes, detailed cartoon illustration style"

Return JSON (EXACTLY 1 character):
{{
    "characters": [
        {{
            "id": "char_1",
            "name": "Narrator",
            "role": "narrator",
            "portrait_prompt": "Detailed cartoon character, [face], [hair], [distinctive feature], wearing [outfit], standing in warm-lit study room, detailed cartoon illustration style, warm color palette",
            "character_lock": "warm friendly cartoon [role] with [hair], [distinctive feature], [eyes], [expression], [outfit], detailed cartoon illustration style",
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
        return f"""You are creating VIDEO CLIPS for a financial history YouTube channel. Create exactly {image_count} video scenes.

IMPORTANT: Each scene will be a TEXT-TO-VIDEO clip with full character description.

SEGMENT INFO:
- Name: "{seg_name}"
- Message: "{message}"
- Duration: {seg_duration:.1f} seconds total
- Required: EXACTLY {image_count} video clips
- Each clip: {min_scene_duration}-{max_scene_duration} seconds
- MINIMUM {min_scene_duration}s, MAXIMUM {max_scene_duration}s

VISUAL STYLE:
{context_lock}

CHARACTERS (include full description in every scene):
{chr(10).join(char_locks) if char_locks else 'No characters defined'}

SRT CONTENT:
{srt_text}

VIDEO TECHNIQUES FOR FINANCE/HISTORY:
- HISTORICAL SCENES: period-appropriate cartoon (factories, markets, parliaments)
- DATA VISUALIZATIONS: charts with NUMBERS and ARROWS (no text)
- MAPS: trade routes, economic zones with arrows
- NARRATOR IN STUDY: historian at desk explaining
- CITY PANORAMAS: cartoon cityscapes showing economic change
- COMPARISON: split composition before/after
- NO TEXT/WORDS - use NUMBERS, CURRENCY SYMBOLS, ARROWS, PERCENTAGE SIGNS

INSTRUCTIONS:
1. EXACTLY {image_count} scenes
2. Each scene VISUALLY REPRESENTS the narration
3. scene_id: 1, 2, 3...
4. NARRATOR in AT LEAST 60% of scenes
5. characters_used: narrator ID when present
6. location_used: leave EMPTY (no location references)

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
            "visual_moment": "DETAILED description with historical/data elements",
            "characters_used": "nvc",
            "location_used": "",
            "camera": "camera movement",
            "lighting": "warm golden/soft/dramatic"
        }}
    ]
}}
Create exactly {image_count} video clips!"""

    # ========== STEP 6: Scene Planning ==========
    def step6_scene_planning(self, context_lock: str, segments_info: str,
                              char_info: str, loc_info: str, scenes_text: str) -> str:
        return f"""Plan each VIDEO SCENE for a financial history YouTube channel.

VIDEO-ONLY MODE: Each scene generates a video directly from text.

VISUAL STYLE: Detailed cartoon animation, warm color palette, editorial style.

CONTENT CONTEXT:
{context_lock}

CHARACTERS (include full description in each scene):
{char_info if char_info else 'Not specified'}

SCENES TO PLAN:
{scenes_text}

For EACH scene, plan:
1. artistic_intent: Economic concept/historical event to visualize
2. shot_type: Composition (panoramic, close-up, split screen, data visualization)
3. character_action: Narrator action (at desk, walking through history, pointing at chart)
4. mood: Feeling (analytical, dramatic, surprising, cautionary)
5. lighting: Warm golden / dramatic / contrast
6. color_palette: Dominant colors
7. key_focus: What viewer notices first

TECHNIQUES:
- PANORAMIC CITYSCAPES (economic boom/decline)
- SPLIT SCREEN comparisons (15-20% of scenes)
- DATA VISUALIZATIONS (charts, floating graphs)
- MAPS with arrows (trade flows, capital movement)
- HISTORICAL SCENES (factories, harbors, farms)
- NARRATOR AT DESK (warm study with books)

Return JSON only:
{{
    "scene_plans": [
        {{
            "scene_id": 1,
            "artistic_intent": "Show economic contrast",
            "shot_type": "Split screen",
            "character_action": "Narrator gestures at comparison",
            "mood": "Analytical",
            "lighting": "Warm study lamp",
            "color_palette": "Golden amber, blue-grey",
            "key_focus": "GDP chart with turning point"
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
- DO NOT create generic videos. Each video must be SPECIFIC to its narration content.
- "Visual moment" is a GUIDE, but "Text" is the TRUTH - follow "Text" if they conflict.

VIDEO-ONLY MODE: Each prompt generates a VIDEO directly (no image first).
The video_prompt must be SELF-CONTAINED with FULL character description.

VISUAL STYLE:
{context_lock}

ABSOLUTE RULE - NO TEXT IN VIDEOS:
- NEVER include ANY words/text in video prompts
- Use: numbers (3.5%, $100M), currency symbols ($, €), arrows, flag icons, percentage signs
- Instead of text labels use VISUAL REPRESENTATIONS: hospital = building with red cross, wealth = gold coins

CRITICAL VIDEO STYLE RULES:
- DETAILED cartoon ANIMATION (NOT minimalist - rich, warm, editorial style)
- Warm color palette: golden, amber, earth tones
- Rich detailed backgrounds
- Characters have DETAILED features (specific face, hair, clothes)
- Other people: detailed cartoon people (NOT silhouettes)
- Data as VISUAL CHARTS with numbers and arrows
- NO (nv1.png) or (loc1.png) references - describe everything in TEXT

SCENES TO PROCESS ({batch_size} scenes):
{scenes_text}

CRITICAL REQUIREMENTS:
1. EXACTLY {batch_size} video prompts
2. Each video_prompt must be SELF-CONTAINED (include full character description)
3. NO reference file annotations like (nvc.png) - describe character in TEXT
4. Include camera movement and animation details
5. 60-120 words per video_prompt
6. img_prompt must be EMPTY string ""

VIDEO PROMPT STRUCTURE:
"[Full character description], [action/pose], [scene setting with rich details], [data/historical elements], [camera movement], [lighting], detailed cartoon animation, warm color palette"

EXAMPLE video_prompt (GOOD - narrator at desk):
"Warm cozy study room filled with bookshelves and vintage maps on walls. A warm friendly cartoon historian with grey wavy hair, round glasses, kind brown eyes, wearing mustard yellow sweater over white collared shirt, sits at wooden desk covered with open books. He holds up a small cartoon globe and gestures knowingly. Desk lamp casts warm golden light, coffee mug steaming on desk. Camera slowly zooms in on his expression. Detailed cartoon animation, warm color palette, soft lighting"

EXAMPLE video_prompt (GOOD - historical scene):
"Panoramic cartoon of 1890s Stockholm harbor, tall sailing ships at wooden docks, warehouse buildings with warm brick facades. Workers load crates of iron ore while green upward arrow symbol floats above harbor. A warm friendly cartoon historian with grey wavy hair and round glasses stands at edge observing. Camera slowly pans left to right across the harbor. Warm golden sunset lighting, detailed cartoon animation"

EXAMPLE video_prompt (GOOD - data visualization):
"SPLIT composition divided by golden line. LEFT warm amber tones: thriving 1950s Swedish factory, smokestacks running, workers streaming in, green upward arrow with 4.2% above. RIGHT cooler tones: same factory in 1990s, quieter, fewer workers, red downward arrow with 1.1%. Warm friendly historian with grey hair and round glasses stands between both sides gesturing at comparison. Camera slowly pulls back. Detailed cartoon animation, warm color palette"

Return JSON only with EXACTLY {batch_size} scenes:
{{
    "scenes": [
        {{
            "scene_id": 1,
            "img_prompt": "",
            "video_prompt": "FULL character description, action, scene details, data/historical elements, camera movement, detailed cartoon animation, warm color palette"
        }}
    ]
}}
"""

    # ========== STEP 8: Thumbnail ==========
    def step8_thumbnail(self, setting: dict, themes: list, visual_style: dict,
                        context_lock: str, protagonist, chars_info: str,
                        locs_info: str, char_ids: list, loc_ids: list) -> str:
        return f"""Create 3 YouTube thumbnail prompts for a FINANCIAL HISTORY channel.

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
2. Include FULL character description
3. Thumbnails CAN have TEXT/WORDS for CTR
4. End with: "detailed cartoon illustration style, warm color palette, soft lighting"

CREATE 3 THUMBNAILS:

VERSION 1 - "portrait_main": Narrator close-up with economic visual
VERSION 2 - "concept_visual": Data/history scene, surprising economic fact
VERSION 3 - "youtube_ctr": Maximum CTR with contrast/surprise

Return JSON only:
{{
  "thumbnails": [
    {{
      "thumb_id": 1,
      "version_desc": "portrait_main",
      "img_prompt": "...with (nvX.png)..., detailed cartoon illustration style, warm color palette, soft lighting",
      "characters_used": "nvc",
      "location_used": ""
    }},
    {{
      "thumb_id": 2,
      "version_desc": "concept_visual",
      "img_prompt": "...",
      "characters_used": "nvc",
      "location_used": ""
    }},
    {{
      "thumb_id": 3,
      "version_desc": "youtube_ctr",
      "img_prompt": "...",
      "characters_used": "nvc",
      "location_used": ""
    }}
  ]
}}
"""
