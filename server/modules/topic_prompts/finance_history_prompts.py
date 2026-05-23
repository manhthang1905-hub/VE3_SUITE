"""
Finance History topic prompts - Lich su Tai chinh / Kinh te.
Phong cach hoat hinh chi tiet kieu editorial illustration / Studio Ghibli:
- Nhan vat cartoon chi tiet (khong phai minimalist tron trang)
- Background giau chi tiet: thanh pho, van phong, lich su
- Mau am, anh sang warm
- Bieu do, so lieu, ban do thay cho text
- Icons, symbols, arrows thay cho chu
- NO TEXT trong scenes (AI tao text sai chinh ta)

Tham khao: kenh "Chill Financial Historian" YouTube
"""


class FinanceHistoryPrompts:
    """Prompts cho chu de Finance History / Economics."""

    TOPIC_NAME = "finance_history"
    TOPIC_LABEL = "Lich su Tai chinh / Kinh te"

    # ========== FALLBACK & UTILITIES ==========
    def fallback_style(self) -> str:
        """Style suffix cho fallback prompts khi API fail."""
        return "Detailed cartoon illustration, warm color palette, editorial illustration style, rich detailed backgrounds, soft warm lighting."

    def fallback_video_style(self) -> str:
        return "Smooth cartoon animation, gentle camera panning, warm atmosphere"

    def split_scene_prompt(self, duration: float, min_shots: int, max_shots: int,
                           srt_start: str, srt_end: str, srt_text: str,
                           visual_moment: str, characters_used: str,
                           location_used: str, char_locks: list, loc_locks: list) -> str:
        """Prompt de chia scene dai thanh nhieu illustrations."""
        return f"""You are an ILLUSTRATOR for an educational YouTube channel about financial history and economics.
This scene is {duration:.1f} seconds - TOO LONG for one illustration (max 8s).
Split it into {min_shots}-{max_shots} DISTINCT illustration panels.

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
1. Each panel MUST be 4-8 seconds (divide the {duration:.1f}s total). MINIMUM 4 seconds per panel
2. Each panel must show DIFFERENT aspect of the economic concept being explained
3. All panels together must cover the FULL narration
4. Use EXACT character/location IDs from the lists above
5. Use charts, maps, data visualizations, historical scenes to illustrate concepts
6. NO TEXT/WORDS in images - use NUMBERS, ARROWS, ICONS, SYMBOLS instead

Examples of good splits:
- Economic timeline: Historical scene -> Data/chart visualization -> Modern consequence
- Comparison: Country A economy -> Country B economy -> Contrast/result
- Cause-effect: Policy introduced -> Economic impact -> Long-term outcome

Return JSON only:
{{
    "shots": [
        {{
            "shot_number": 1,
            "duration": 5.0,
            "srt_text": "portion of narration for this panel",
            "visual_moment": "what the illustration shows - with economic data visualizations",
            "shot_purpose": "why this illustration at this moment",
            "characters_used": "{characters_used}",
            "location_used": "{location_used}",
            "camera": "composition style"
        }}
    ]
}}"""

    def has_narrator_role(self) -> bool:
        """Finance history co narrator/historian role."""
        return True

    def get_default_character(self, override_prompt: str = "") -> dict:
        """Tra ve nhan vat mac dinh cho finance history.

        Args:
            override_prompt: Neu co, dung lam portrait_prompt thay vi mac dinh.
                             Doc tu Google Sheet col L sheet THONG TIN.

        Returns:
            dict voi keys: name, role, portrait_prompt, character_lock, is_minor
        """
        if override_prompt and override_prompt.strip():
            # Dung prompt tu Google Sheet
            prompt = override_prompt.strip()
            # Tao character_lock tu portrait_prompt (bo phan location/background)
            lock = prompt
            # Cat bo phan "He stands in..." hoac "standing in..." neu co
            for cut_phrase in ["He stands in", "She stands in", "Standing in", "He is standing", "She is standing"]:
                idx = lock.find(cut_phrase)
                if idx > 0:
                    lock = lock[:idx].rstrip(", .")
                    break
            return {
                "name": "Narrator",
                "role": "protagonist",
                "portrait_prompt": prompt,
                "character_lock": lock,
                "is_minor": False,
            }

        # Mac dinh: Intellectual historian
        return {
            "name": "Narrator",
            "role": "protagonist",
            "portrait_prompt": (
                "Detailed cartoon character, a thoughtful intellectual man in his late 40s, "
                "with a kind, square-jawed face, warm hazel eyes behind round tortoiseshell glasses, "
                "a neatly trimmed salt-and-pepper beard, and slightly tousled dark brown hair with grey streaks at the temples. "
                "He wears a forest green corduroy blazer over a cream-colored cable-knit sweater, "
                "a burgundy checkered shirt collar peeking out, dark olive chinos, and brown leather brogue shoes. "
                "He stands in a warm-lit, book-filled study with a large historical map of Sweden on the wall behind him, "
                "looking directly at the viewer with a warm, analytical expression. "
                "Detailed cartoon illustration style, Studio Ghibli aesthetic, warm color palette, soft lighting."
            ),
            "character_lock": (
                "Detailed cartoon intellectual man in his late 40s, kind square-jawed face, "
                "warm hazel eyes behind round tortoiseshell glasses, neatly trimmed salt-and-pepper beard, "
                "slightly tousled dark brown hair with grey streaks at the temples, "
                "forest green corduroy blazer over cream-colored cable-knit sweater, "
                "burgundy checkered shirt collar peeking out, dark olive chinos, brown leather brogue shoes, "
                "warm analytical expression, detailed cartoon illustration style, Studio Ghibli aesthetic"
            ),
            "is_minor": False,
        }

    # ========== STEP 1: Analyze Content ==========
    def step1_analyze(self, sampled_text: str) -> str:
        return f"""Analyze this financial history / economics content and extract key information for visual illustration.

NOTE: The content is provided in sampled format (beginning + middle + end) to capture the full narrative arc.

CONTENT (SAMPLED):
{sampled_text}

This is a FINANCIAL HISTORY / ECONOMICS video with DETAILED CARTOON ILLUSTRATION style (like editorial illustration or Studio Ghibli).
Analyze the content to understand:
- What economic or historical topic is being discussed?
- What countries, time periods, and economic policies are covered?
- What data, statistics, and economic indicators are mentioned?
- What is the main argument or thesis being presented?
- What historical figures, companies, or institutions are involved?

The visual style uses:
- Detailed cartoon illustration (NOT minimalist - rich, warm, detailed like editorial comics)
- Warm color palette (golden, amber, warm blues, earth tones)
- Rich detailed backgrounds (cities, offices, historical settings, landscapes)
- Data shown as VISUAL CHARTS, GRAPHS, MAPS with numbers (NO text labels)
- Historical scenes illustrated in cartoon style
- NO TEXT/WORDS in the image (AI generates text with spelling errors)
- Numbers, arrows, percentage symbols, currency symbols ARE OK

Return JSON only:
{{
    "setting": {{
        "era": "historical period and modern analysis",
        "location": "countries and settings relevant to the content",
        "atmosphere": "intellectual, warm, educational, documentary-like"
    }},
    "themes": ["theme1", "theme2", "theme3"],
    "visual_style": {{
        "cinematography": "detailed cartoon illustration, editorial comic style, rich backgrounds",
        "color_palette": "warm golden tones, amber, earth tones, soft blues",
        "lighting": "warm soft lighting, golden hour, cozy study atmosphere"
    }},
    "context_lock": "Detailed cartoon illustration, editorial comic style, warm color palette, rich detailed backgrounds, soft warm lighting, educational documentary aesthetic"
}}
"""

    # ========== STEP 2: Segments ==========
    def step2_segments(self, context_lock: str, themes: list, total_duration: float,
                       total_srt: int, sampled_text: str) -> str:
        themes_str = ', '.join(themes) if themes else 'Not specified'
        return f"""Analyze this financial history / economics content and divide it into logical segments for video illustration.

IMPORTANT: Your segment analysis will be used by later steps to create DETAILED CARTOON ILLUSTRATIONS.
Make your "message" and "key_elements" DETAILED enough to guide illustration creation.
Focus on: HISTORICAL SCENES, DATA VISUALIZATIONS, MAPS, ECONOMIC DIAGRAMS. NO text/words in images.

CONTENT CONTEXT:
{context_lock}

THEMES: {themes_str}

TOTAL DURATION: {total_duration:.1f} seconds
TOTAL SRT ENTRIES: {total_srt}

CONTENT (SAMPLED):
{sampled_text}

TASK: Divide the content into logical segments based on the economic argument structure or historical timeline.

CRITICAL REQUIREMENT:
- Your segments MUST cover ALL {total_srt} SRT entries
- First segment starts at srt_range_start: 1
- Last segment MUST end at srt_range_end: {total_srt}
- NO gaps between segments

For each segment, provide:
1. message: What economic concept/historical period is being discussed? What data is presented?
2. key_elements: List of VISUAL elements (historical scenes, data charts, maps, economic indicators, buildings, factories)
3. visual_summary: 2-3 sentences describing what CARTOON ILLUSTRATIONS should show
4. mood: The emotional/intellectual tone (analytical, dramatic, surprising, sobering, triumphant, cautionary)
5. characters_involved: Which people/roles appear (narrator/historian, historical figures, workers, politicians, etc.)

Return JSON only:
{{
    "segments": [
        {{
            "segment_id": 1,
            "segment_name": "Introduction/Hook",
            "message": "DETAILED description: what economic argument is introduced, what historical context",
            "key_elements": ["historical scene", "data visualization", "map", "economic indicator"],
            "visual_summary": "2-3 sentences describing cartoon illustrations with data visualizations",
            "mood": "analytical/dramatic/surprising/etc",
            "characters_involved": ["narrator", "historical figures"],
            "estimated_duration": 15.0,
            "srt_range_start": 1,
            "srt_range_end": 25,
            "importance": "high/medium/low"
        }}
    ],
    "summary": "Brief overview of the economic argument structure"
}}
"""

    # ========== STEP 3: Characters ==========
    def step3_characters(self, setting: dict, context_lock: str,
                         all_characters_mentioned: list, segment_insights: str,
                         targeted_srt_text: str) -> str:
        chars_str = ', '.join(all_characters_mentioned) if all_characters_mentioned else 'Analyze from content segments below'
        return f"""Based on the content analysis below, identify the narrator/host character and any other characters for DETAILED CARTOON ILLUSTRATION.

IMPORTANT VISUAL STYLE:
- This is a DETAILED CARTOON illustration style (like editorial comics, Studio Ghibli aesthetic)
- NOT minimalist - characters have DETAILED features, realistic proportions in cartoon style
- Narrator/Host: a thoughtful intellectual character (like a historian, professor, or researcher)
- Historical figures: cartoon versions of real people if mentioned
- Other people in scenes: detailed cartoon people (workers, politicians, citizens, etc.)
- Style reference: warm, detailed, like "Chill Financial Historian" YouTube channel

CONTENT CONTEXT:
- Setting: {setting.get('location', 'Not specified')}
- Visual style: {context_lock}

PEOPLE MENTIONED (from segments):
{chars_str}

CONTENT SEGMENTS ANALYSIS:
{segment_insights}

SAMPLE SRT CONTENT:
{targeted_srt_text[:8000] if targeted_srt_text else 'Use segment analysis above'}

CHARACTER DESIGN RULES:
- ONLY ONE NARRATOR CHARACTER: Create exactly 1 narrator/host character. Do NOT create multiple narrator characters.
- Narrator/Host: a warm, intellectual cartoon character - think professor or historian type
  * Detailed face with expressive eyes, specific hairstyle and color
  * Thoughtful, analytical expressions
  * Smart casual or academic clothing
- MUST include a DISTINCTIVE VISUAL IDENTIFIER (specific glasses, bow tie, vest, distinctive hair, beard, etc.)
- MUST specify FULL OUTFIT: top (sweater/shirt/vest + color), bottom (pants/trousers + color), footwear (shoes + color)
- Historical figures mentioned in content: DO NOT create separate character entries for them. Show them as generic cartoon people in scenes (e.g., "a 19th century businessman in top hat"). Only create reference images for the NARRATOR.
- Background people: detailed cartoon people (NOT silhouettes - this is NOT minimalist style)
- MAXIMUM 1-2 characters total. The narrator is the ONLY character that needs a reference image.

For each character, provide:
1. portrait_prompt: Detailed cartoon character on simple background
2. character_lock: Full character description to be COPY-PASTED into every scene prompt
3. is_minor: true if character is a child

IMPORTANT: The character_lock must include ALL of these elements:
- Face: specific features (round face, square jaw, etc.)
- Hair: specific style and color
- Distinctive feature: glasses, beard, bow tie, etc.
- Eyes: specific eye style and color
- Expression: default expression (thoughtful, warm, analytical)
- Clothing TOP: specific garment + color (e.g., mustard yellow sweater, brown vest over white shirt)
- Clothing BOTTOM: specific garment + color (e.g., dark brown trousers, navy pants)
- Footwear: specific type + color (e.g., brown oxford shoes, dark loafers)
- Style suffix: detailed cartoon illustration style, warm color palette

Example character_lock: "warm friendly cartoon historian with grey wavy hair, round glasses, kind brown eyes, thoughtful expression, mustard yellow sweater over white collared shirt, dark brown trousers, brown oxford shoes, detailed cartoon illustration style"

Return JSON:
{{
    "characters": [
        {{
            "id": "char_id",
            "name": "Name or Role",
            "role": "protagonist/narrator/supporting",
            "portrait_prompt": "Detailed cartoon character, [face description], [hairstyle], [distinctive feature], wearing [full outfit], standing in warm-lit study room, detailed cartoon illustration style, warm color palette",
            "character_lock": "warm friendly cartoon [role] with [hair], [distinctive feature], [eyes], [expression], [full outfit], detailed cartoon illustration style",
            "is_minor": false
        }}
    ]
}}
"""

    # ========== STEP 4: Locations ==========
    def step4_locations(self, setting: dict, context_lock: str, char_names: list,
                        all_locations_hints: list, segment_insights: str,
                        targeted_srt_text: str) -> str:
        locs_str = ', '.join(all_locations_hints) if all_locations_hints else 'Analyze from content segments below'
        return f"""Based on the content analysis below, identify all locations/settings for DETAILED CARTOON ILLUSTRATION.

CONTENT CONTEXT:
- Setting: {setting.get('location', 'Not specified')}
- Visual style: {context_lock}

LOCATION HINTS (from segments):
{locs_str}

CONTENT SEGMENTS ANALYSIS:
{segment_insights}

SAMPLE SRT CONTENT:
{targeted_srt_text[:6000] if targeted_srt_text else 'Use segment analysis above'}

LOCATION ILLUSTRATION RULES:
- Detailed cartoon illustration style with RICH backgrounds (NOT minimalist)
- Warm color palette: golden, amber, earth tones, soft blues
- Historical accuracy in period-appropriate settings
- Include: architecture, furniture, objects, atmosphere details
- EMPTY SPACES with NO characters/people
- Types of locations to consider:
  * Study/library (narrator's base - warm, book-filled, desk with lamp)
  * Historical settings (factories, government buildings, markets, streets)
  * City panoramas (skylines, aerial views, harbors)
  * Data/chart rooms (clean backgrounds for overlaying economic data)
  * Countryside/landscape scenes

Return JSON only:
{{
    "locations": [
        {{
            "id": "loc_id",
            "name": "Location Name",
            "location_prompt": "Detailed cartoon illustration of [location], [architectural details], [furniture and objects], [atmosphere], warm color palette, soft lighting, detailed editorial illustration style, no people, no characters",
            "location_lock": "detailed cartoon [location] with [key feature], warm tones, editorial style (10-15 words)",
            "lighting_default": "warm golden lighting"
        }}
    ]
}}
"""

    # ========== STEP 5: Director Plan ==========
    def step5_director_plan(self, image_count: int, seg_name: str, message: str,
                            seg_duration: float, scene_duration: float,
                            min_scene_duration: int, max_scene_duration: int,
                            context_lock: str, char_locks: list, loc_locks: list,
                            srt_text: str) -> str:
        return f"""You are an ILLUSTRATOR for an educational YouTube channel about financial history and economics.
Create exactly {image_count} illustration scenes for this content segment.

IMPORTANT: Each illustration will become a VIDEO CLIP. The visual must CLOSELY MATCH the narration content.
Think about: What HISTORICAL SCENE or DATA VISUALIZATION best illustrates this economic concept?

SEGMENT INFO:
- Name: "{seg_name}"
- Message: "{message}"
- Duration: {seg_duration:.1f} seconds total
- Required: EXACTLY {image_count} illustrations
- Each illustration covers {min_scene_duration}-{max_scene_duration} seconds of narration
- MINIMUM {min_scene_duration} seconds per scene - NO scene shorter than {min_scene_duration}s
- MAXIMUM {max_scene_duration} seconds per scene - NO scene longer than {max_scene_duration}s

VISUAL STYLE:
{context_lock}

CHARACTERS (detailed cartoon):
{chr(10).join(char_locks) if char_locks else 'No characters defined'}

LOCATIONS (detailed cartoon):
{chr(10).join(loc_locks) if loc_locks else 'No locations defined'}

SRT CONTENT FOR THIS SEGMENT:
{srt_text}

ILLUSTRATION TECHNIQUES FOR FINANCE/HISTORY:
- HISTORICAL SCENES: period-appropriate cartoon illustrations (factories, markets, parliaments, streets)
- DATA VISUALIZATIONS: charts, graphs with NUMBERS and ARROWS (no text labels) - line charts going up/down, bar charts, pie charts
- MAPS: cartoon maps showing countries, trade routes, economic zones with arrows and symbols
- TIMELINE SCENES: showing progression through decades with visual indicators
- COMPARISON/CONTRAST: split screen showing before/after, country A vs country B
- NARRATOR IN STUDY: historian character at desk with books, explaining with visual aids
- CITY PANORAMAS: detailed cartoon cityscapes showing economic prosperity or decline
- IMPORTANT: NO TEXT/WORDS/LABELS in the image - use NUMBERS, CURRENCY SYMBOLS ($, €, kr), ARROWS, PERCENTAGE SIGNS instead

INSTRUCTIONS:
1. Create EXACTLY {image_count} illustration scenes - no more, no less
2. Each scene must VISUALLY REPRESENT the narration content
3. DURATION RULES: Each scene MUST be {min_scene_duration}-{max_scene_duration} seconds. NEVER create a scene shorter than {min_scene_duration}s or longer than {max_scene_duration}s.
4. Use historical scenes and data visualizations to illustrate economic concepts
5. Use EXACT character/location IDs from the lists above
6. scene_id: just use 1, 2, 3...
6. NARRATOR PRESENCE: The narrator/historian character should appear in AT LEAST 60% of scenes (presenting, observing, at desk). Other scenes can be pure historical illustrations or data visualizations.
7. REFERENCES ACCURACY:
   - characters_used: ONLY narrator or named characters who appear
   - location_used: ONLY ONE location per scene

Return JSON only:
{{
    "scenes": [
        {{
            "scene_id": 1,
            "srt_indices": [list of SRT indices covered],
            "srt_start": "timestamp",
            "srt_end": "timestamp",
            "duration": {scene_duration:.1f},
            "srt_text": "narration text for this scene",
            "visual_moment": "DETAILED description of what the illustration shows - include historical scenes, data visualizations, maps. NO text/words in image",
            "characters_used": "nv_xxx",
            "location_used": "loc_xxx",
            "camera": "composition (centered, split screen, panoramic, close-up, wide, bird's eye)",
            "lighting": "warm golden/soft/dramatic"
        }}
    ]
}}
Create exactly {image_count} illustrations!"""

    # ========== STEP 6: Scene Planning ==========
    def step6_scene_planning(self, context_lock: str, segments_info: str,
                              char_info: str, loc_info: str, scenes_text: str) -> str:
        return f"""You are an illustrator planning each scene's visual approach for an educational YouTube channel about financial history.

IMPORTANT: Each scene becomes a VIDEO CLIP. Plan visuals that CLOSELY MATCH the narration.

VISUAL STYLE: Detailed cartoon illustration, editorial comic style, warm color palette, rich backgrounds.

CONTENT CONTEXT:
{context_lock}

CONTENT SEGMENTS:
{segments_info if segments_info else 'Not specified'}

CHARACTERS (detailed cartoon):
{char_info if char_info else 'Not specified'}

LOCATIONS (detailed cartoon):
{loc_info if loc_info else 'Not specified'}

SCENES TO PLAN:
{scenes_text}

For EACH scene, plan the cartoon illustration using these techniques:
1. artistic_intent: What economic concept or historical event to visualize? What makes it compelling?
2. shot_type: Composition (panoramic cityscape, close-up character, split screen comparison, bird's eye map, data visualization, study room with visual aid)
3. character_action: What is narrator doing? (at desk explaining, walking through historical scene, pointing at chart, observing from distance)
4. mood: Overall feeling (analytical, dramatic, surprising, sobering, triumphant, cautionary, nostalgic)
5. lighting: Warm golden / soft study lamp / dramatic contrast / historical atmosphere
6. color_palette: Dominant colors for this specific scene
7. key_focus: What viewer should notice first (data trend, historical scene, character reaction, map)

VISUAL TECHNIQUES FOR FINANCE/HISTORY:
- PANORAMIC CITYSCAPES showing economic boom or decline (detailed cartoon cities)
- SPLIT SCREEN comparisons (before/after economic policy, country A vs B) - USE for 15-20% of scenes
- DATA VISUALIZATIONS as part of the scene (charts on walls, floating graphs, desk with papers showing numbers)
- MAPS with arrows showing trade flows, economic migration, capital movement
- HISTORICAL SCENES: factories, markets, parliaments, harbors, farms in period-appropriate cartoon style
- NARRATOR AT DESK: historian in warm study, surrounded by books, explaining with visual props
- TIMELINE TRANSITIONS: visual indicators showing passage of decades
- IMPORTANT: NO TEXT/WORDS/LABELS in prompts - use NUMBERS, ARROWS, CURRENCY SYMBOLS, PERCENTAGE SIGNS

COMPOSITION VARIETY REQUIREMENT:
- At least 2-3 scenes MUST use SPLIT SCREEN or panoramic comparison
- At least 2-3 scenes should show DATA VISUALIZATIONS or MAPS
- Mix: narrator study scenes, historical illustrations, city panoramas, data scenes

Return JSON only:
{{
    "scene_plans": [
        {{
            "scene_id": 1,
            "artistic_intent": "Show the contrast between perceived socialist wealth and actual capitalist foundation",
            "shot_type": "Split screen comparison, warm vs cool tones",
            "character_action": "Narrator at desk, holding up two historical photographs",
            "mood": "Analytical, myth-busting",
            "lighting": "Warm study lamp on narrator side, cool blue on data side",
            "color_palette": "Golden amber narrator, blue-grey data visualization",
            "key_focus": "GDP chart showing growth timeline with key turning point marked"
        }}
    ]
}}
"""

    # ========== STEP 7: Scene Prompts ==========
    def step7_scene_prompts(self, context_lock: str, scenes_text: str,
                             batch_size: int) -> str:
        return f"""Create detailed CARTOON ILLUSTRATION prompts for these {batch_size} scenes.

#1 PRIORITY - CONTENT MATCHING (QUAN TRONG NHAT):
- The img_prompt MUST illustrate EXACTLY what the narrator is SAYING in the "Text" field
- Read the "Text" field carefully - this is what viewers HEAR. The image MUST match what they hear.
- If Text talks about "GDP growth in 1950s" → image MUST show 1950s economic scene with growth charts
- If Text talks about "banking crisis" → image MUST show bank/financial panic scene
- If Text talks about "trade routes" → image MUST show maps with trade arrows
- DO NOT create generic "narrator at desk" images for every scene
- Each scene's image must be SPECIFIC to its narration content
- "Visual moment" is a GUIDE, but "Text" is the TRUTH - if they conflict, follow "Text"
- The narrator can appear IN the historical scene (not always at desk)

VISUAL STYLE (MUST follow for ALL scenes):
{context_lock}

ABSOLUTE RULE - NO TEXT IN IMAGES:
- NEVER include ANY English, Swedish, or other language WORDS in image prompts
- NEVER use quoted strings like 'WEALTH CREATION', 'Folkhemmet', 'FREE HEALTHCARE' etc.
- NEVER request readable text on signs, documents, banners, headlines, labels
- INSTEAD USE: numbers (3.5%, $100M), currency symbols ($, €, kr), arrows (↑↓), flag icons, building icons, percentage signs (%)
- INSTEAD OF text labels, use VISUAL REPRESENTATIONS: a hospital = building with red cross, a school = building with book icon, wealth = gold coins/stacks

CRITICAL ILLUSTRATION STYLE RULES:
- DETAILED cartoon illustration (NOT minimalist - rich, warm, editorial comic style)
- Warm color palette: golden, amber, earth tones, warm blues
- Rich detailed backgrounds with architectural details, furniture, objects
- Characters have DETAILED features (specific face, hair, clothes - NOT round white heads)
- Other people in scenes: detailed cartoon people (NOT silhouettes)
- Show economic data as VISUAL CHARTS, GRAPHS with numbers and arrows (NO text labels)
- Use MAPS with arrows for trade/capital flows
- Historical scenes in period-appropriate cartoon style
- DO NOT write meta-labels like "data visualization:" - just DESCRIBE what the illustration shows

REFERENCE FILE ANNOTATIONS:
- Narrator character MUST have reference file: "warm cartoon historian (nv1.png)"
- Location MUST have reference file: "in warm study room (loc_study.png)"
- Character description from character_lock should be INCLUDED in the prompt
- Format: "[full character_lock description] (nv_xxx.png) [action] in [location description] (loc_xxx.png)"

SCENES TO PROCESS ({batch_size} scenes - create EXACTLY {batch_size} prompts):
{scenes_text}

CRITICAL REQUIREMENTS:
1. Create EXACTLY {batch_size} scene prompts
2. #1 PRIORITY: Each img_prompt MUST illustrate the SPECIFIC content from the "Text" field - NOT generic "narrator at desk"
3. Include the FULL character description (from character_lock) in every prompt where character appears
4. Describe data visualizations as ACTUAL OBJECTS in the scene
5. NO TEXT/WORDS/LABELS in image prompts - use NUMBERS, ARROWS, CURRENCY SYMBOLS, PERCENTAGE SIGNS instead
6. End every prompt with: "detailed cartoon illustration style, warm color palette, soft lighting"

For each scene, create:
1. img_prompt: Detailed illustration prompt describing what the image SHOWS (not meta-instructions). NO TEXT IN IMAGE.
2. video_prompt: Animation description (camera movement, character actions, data animation). MUST end with style: "detailed cartoon animation style, warm color palette, soft lighting"
   - CRITICAL: video_prompt MUST maintain the SAME detailed cartoon animation style as img_prompt
   - Character in video: use FULL character_lock description (detailed features, specific clothes, etc.)
   - Other people in video: detailed cartoon people (NOT silhouettes - rich editorial style)
   - DO NOT create realistic/photorealistic video - keep detailed cartoon animation style
   - Data elements: animated charts, arrows growing, numbers appearing
   - Color palette: golden, amber, earth tones, warm blues

Example img_prompt (GOOD - narrator at desk):
"Warm cozy study room filled with bookshelves and vintage maps on walls, warm cartoon historian with grey wavy hair, round glasses, kind brown eyes, mustard yellow sweater over white collared shirt (nv1.png) sitting at large wooden desk covered with open books and papers, holding up a small cartoon globe, desk lamp casting warm golden light, large window showing twilight sky behind, coffee mug on desk (loc_study.png), detailed cartoon illustration style, warm color palette, soft lighting"

Example img_prompt (GOOD - historical scene):
"Panoramic cartoon illustration of 1890s Stockholm harbor, tall sailing ships and early steamships at wooden docks, warehouse buildings with warm brick facades, workers loading crates of iron ore, large upward-pointing green arrow symbol floating above the harbor indicating economic growth, warm golden sunset lighting, detailed period architecture, cobblestone waterfront (loc_harbor.png), detailed cartoon illustration style, warm color palette, soft lighting"

Example img_prompt (GOOD - data visualization):
"SPLIT SCREEN composition divided by vertical golden line. LEFT SIDE warm amber tones: cartoon illustration of thriving 1950s Swedish factory with smokestacks, workers streaming in, upward green arrow and numbers 4.2% above. RIGHT SIDE cooler tones: same factory in 1990s looking quieter, fewer workers, downward red arrow and numbers 1.1% above, warm cartoon historian with grey wavy hair, round glasses (nv1.png) standing between both sides gesturing at comparison, detailed cartoon illustration style, warm color palette, soft lighting"

BAD examples (DO NOT write like this - has text/words):
- "Chart labeled 'GDP Growth Rate' with text annotations showing 'Sweden leads Europe'"
- "Document with text 'WEALTH CREATION' and 'WELFARE STATE'"
- "Sign reading 'Folkhemmet' on the building"
- "Blueprint with text 'Hem PC Reform'"
- "Banner saying 'FREE HEALTHCARE'"
GOOD versions (visual only, NO words - use icons, flags, numbers):
- "Large cartoon line chart on warm parchment, green line trending upward, Swedish flag icon at top, other country flag icons below, numbers 3.5%, 4.2% next to data points"
- "Two golden scale pans: LEFT pan heavy with factory icons and coin stacks, RIGHT pan lighter with hospital and school building icons, golden arrow pointing from left to right"
- "Warm brick building with Swedish flag on top, family silhouettes visible through windows, golden warm glow emanating from inside"
- "Desktop computer icon on desk with large green checkmark above, Swedish flag pin on the monitor"
- "Hospital building with large red cross symbol on front, green checkmark icon floating above"

Example video_prompt (GOOD - includes full style):
"Warm cozy study room with bookshelves and vintage maps. Cartoon historian with grey wavy hair, round glasses, mustard yellow sweater (nv1.png) sitting at wooden desk, picks up small cartoon globe and rotates it while gesturing knowingly. Desk lamp flickers warm golden light, coffee mug steaming on desk. Camera slowly zooms in on his expression. Detailed cartoon animation style, warm color palette, soft lighting"

Return JSON only with EXACTLY {batch_size} scenes:
{{
    "scenes": [
        {{
            "scene_id": 1,
            "img_prompt": "DETAILED illustration prompt with character description (nv_xxx.png) and location (loc_xxx.png), NO TEXT IN IMAGE, detailed cartoon illustration style, warm color palette, soft lighting",
            "video_prompt": "Character description (nv_xxx.png) [action], [camera movement], detailed cartoon animation style, warm color palette, soft lighting"
        }}
    ]
}}
"""

    # ========== STEP 8: Thumbnail ==========
    def step8_thumbnail(self, setting: dict, themes: list, visual_style: dict,
                        context_lock: str, protagonist, chars_info: str,
                        locs_info: str, char_ids: list, loc_ids: list) -> str:
        return f"""You are a YouTube thumbnail designer for an EDUCATIONAL FINANCIAL HISTORY channel.
Create 3 compelling thumbnail image prompts in DETAILED CARTOON ILLUSTRATION style.

CONTENT CONTEXT:
- Setting: {setting}
- Themes: {themes}
- Visual style: {visual_style}
- Context lock: {context_lock}

MAIN CHARACTER: {protagonist.id} ({protagonist.name})
Character description: {protagonist.character_lock or protagonist.english_prompt}

ALL CHARACTERS:
{chars_info}

LOCATIONS:
{locs_info}

AVAILABLE REFERENCE IDs:
- Characters: {char_ids}
- Locations: {loc_ids}

VISUAL STYLE RULES:
- Detailed cartoon illustration, editorial comic style
- Warm color palette (golden, amber, earth tones)
- Rich detailed backgrounds
- Character must match the character_lock description exactly
- Thumbnails CAN have TEXT/WORDS (unlike scene images) - use bold, short text for CTR
- Include BOLD typography that hooks viewers

RULES FOR PROMPTS:
1. Write in English, detailed cartoon illustration style
2. MUST annotate references EXACTLY like this:
   - Character: "warm historian (nv1.png)" or "(nv1.png) looking at chart"
   - Location: "in warm study (loc1.png)"
3. Include the FULL character description (from character_lock) in every prompt
4. Each prompt MUST be unique in composition and emotional appeal
5. End every prompt with: "detailed cartoon illustration style, warm color palette, soft lighting"
6. Include BOLD SHORT TEXT (1-3 words) as visual hook in at least 2 of 3 thumbnails

CREATE EXACTLY 3 THUMBNAIL PROMPTS:

VERSION 1 - "portrait_main" (CHARACTER CLOSE-UP):
Goal: Narrator/historian with strong expression representing the video's core economic thesis.
Style: Close-up, character looking at viewer with analytical/knowing expression, economic visual element nearby (chart, globe, money), bold text with key concept.
Emotion: Intellectual curiosity, "I know something you don't".

VERSION 2 - "concept_visual" (DATA/HISTORY SCENE):
Goal: The most compelling economic data point or historical scene. Makes viewers think "Wait, really?!"
Style: Character presenting surprising data (chart going opposite direction than expected, map showing unexpected connections), bold text label.
Emotion: Surprise, revelation, myth-busting.

VERSION 3 - "youtube_ctr" (MAXIMUM CLICK-THROUGH):
Goal: Maximum CTR using contrast/surprise. Character reacting to shocking economic fact.
Style: Split screen or dramatic contrast between economic myth vs reality, character with surprised/knowing expression, bold floating text hook.
Emotion: "Everything you thought was WRONG", contrarian revelation.

Return JSON only:
{{
  "thumbnails": [
    {{
      "thumb_id": 1,
      "version_desc": "portrait_main",
      "img_prompt": "...full prompt with (nvX.png) and (locX.png), ending with detailed cartoon illustration style, warm color palette, soft lighting",
      "characters_used": "nv1",
      "location_used": "loc1"
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
