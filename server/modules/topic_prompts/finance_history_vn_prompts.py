"""
Finance History VN topic prompts - Lich su Tai chinh phong cach Viet Nam.
Phong cach hoat hinh gan gui khan gia Viet:
- Nhan vat cartoon chi tiet mang net Viet Nam (da ngam, toc den, trang phuc Viet)
- Background gan gui: pho co, cho, quan ca phe, cong so Viet Nam
- Mau am nhung mang sac thai Viet: vang dong, do gach, xanh la, trang
- Bieu do, so lieu, ban do thay cho text
- Icons, symbols, arrows thay cho chu
- NO TEXT trong scenes (AI tao text sai chinh ta)

Khac voi finance_history (phong cach My/phuong Tay):
- Nhan vat Viet Nam (da ngam, mat den, toc den, trang phuc ao dai/ao ba ba/casual Viet)
- Boi canh Viet Nam (pho co Ha Noi, Sai Gon, cho Ben Thanh, song nuoc mien Tay)
- Kien truc Viet (nha pho, chua, dinh, nha mat pho)
- Mau sac Viet (do gach, vang dong, xanh ngoc, trang)
"""


class FinanceHistoryVNPrompts:
    """Prompts cho chu de Finance History phong cach Viet Nam."""

    TOPIC_NAME = "finance_history_vn"
    TOPIC_LABEL = "Tai chinh/Lich su (Phong cach Viet)"

    # ========== FALLBACK & UTILITIES ==========
    def fallback_style(self) -> str:
        """Style suffix cho fallback prompts khi API fail."""
        return "Detailed cartoon illustration, Vietnamese aesthetic, warm earthy color palette, Vietnamese characters with dark hair and warm skin tones, Vietnamese architecture and street scenes, soft warm lighting."

    def fallback_video_style(self) -> str:
        return "Smooth cartoon animation, gentle camera panning, warm Vietnamese atmosphere"

    def split_scene_prompt(self, duration: float, min_shots: int, max_shots: int,
                           srt_start: str, srt_end: str, srt_text: str,
                           visual_moment: str, characters_used: str,
                           location_used: str, char_locks: list, loc_locks: list) -> str:
        """Prompt de chia scene dai thanh nhieu illustrations."""
        return f"""You are an ILLUSTRATOR for an educational YouTube channel about financial history and economics, targeting VIETNAMESE audiences.
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
5. Use charts, maps, data visualizations, Vietnamese historical scenes to illustrate concepts
6. NO TEXT/WORDS in images - use NUMBERS, ARROWS, ICONS, SYMBOLS instead
7. Vietnamese aesthetic: Vietnamese people, Vietnamese architecture, Vietnamese street scenes

Examples of good splits:
- Economic timeline: Vietnamese historical scene -> Data/chart visualization -> Modern consequence
- Comparison: Vietnam economy vs other Asian economies -> Contrast/result
- Cause-effect: Policy introduced -> Economic impact on Vietnamese society -> Long-term outcome

Return JSON only:
{{
    "shots": [
        {{
            "shot_number": 1,
            "duration": 5.0,
            "srt_text": "portion of narration for this panel",
            "visual_moment": "what the illustration shows - with Vietnamese context and economic data",
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
        """Tra ve nhan vat mac dinh cho finance history VN (nguoi Viet).

        Args:
            override_prompt: Neu co, dung lam portrait_prompt thay vi mac dinh.
                             Doc tu Google Sheet col L sheet THONG TIN.

        Returns:
            dict voi keys: name, role, portrait_prompt, character_lock, is_minor
        """
        if override_prompt and override_prompt.strip():
            prompt = override_prompt.strip()
            lock = prompt
            for cut_phrase in ["He stands in", "She stands in", "Standing in", "He is standing", "She is standing",
                               "Anh ta dung", "Nhan vat dung"]:
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

        # Mac dinh: Nhan vat Viet Nam (phien ban Viet cua intellectual historian)
        return {
            "name": "Narrator",
            "role": "protagonist",
            "portrait_prompt": (
                "Detailed cartoon character, a thoughtful Vietnamese intellectual man in his late 40s, "
                "with a kind face, warm dark brown eyes behind thin modern glasses, "
                "neat short black hair with subtle grey at the temples, clean-shaven with warm tan skin. "
                "He wears a light blue polo shirt, dark navy trousers, and clean brown leather shoes. "
                "He stands in a cozy Vietnamese coffee shop with wooden tables, tropical plants, "
                "vintage Vietnamese posters on brick walls, warm golden light from hanging lanterns, "
                "looking directly at the viewer with a warm, analytical expression. "
                "Detailed cartoon illustration style, Vietnamese aesthetic, warm earthy tones, soft lighting."
            ),
            "character_lock": (
                "Detailed cartoon Vietnamese intellectual man in his late 40s, kind face, "
                "warm dark brown eyes behind thin modern glasses, neat short black hair with subtle grey at the temples, "
                "clean-shaven, warm tan skin, light blue polo shirt, dark navy trousers, "
                "clean brown leather shoes, warm analytical expression, "
                "detailed cartoon illustration style, Vietnamese aesthetic"
            ),
            "is_minor": False,
        }

    # ========== STEP 1: Analyze Content ==========
    def step1_analyze(self, sampled_text: str) -> str:
        return f"""Analyze this financial history / economics content and extract key information for visual illustration targeting VIETNAMESE audiences.

NOTE: The content is provided in sampled format (beginning + middle + end) to capture the full narrative arc.

CONTENT (SAMPLED):
{sampled_text}

This is a FINANCIAL HISTORY / ECONOMICS video with DETAILED CARTOON ILLUSTRATION style designed for VIETNAMESE viewers.
Analyze the content to understand:
- What economic or historical topic is being discussed?
- What countries, time periods, and economic policies are covered?
- What data, statistics, and economic indicators are mentioned?
- What is the main argument or thesis being presented?
- What historical figures, companies, or institutions are involved?

The visual style uses VIETNAMESE AESTHETIC:
- Detailed cartoon illustration with Vietnamese cultural elements
- Characters: Vietnamese people (dark hair, warm skin tones, Vietnamese clothing styles)
- Settings: Vietnamese environments (old quarter streets, markets, coffee shops, modern Vietnamese offices)
- Color palette: warm earthy tones (brick red, golden yellow, jade green, cream white) - colors of Vietnamese culture
- Rich detailed backgrounds with Vietnamese architecture (tube houses, pagodas, French colonial buildings)
- Data shown as VISUAL CHARTS, GRAPHS, MAPS with numbers (NO text labels)
- NO TEXT/WORDS in the image (AI generates text with spelling errors)
- Numbers, arrows, percentage symbols, currency symbols (especially dong symbol ₫) ARE OK

Return JSON only:
{{
    "setting": {{
        "era": "historical period and modern analysis",
        "location": "countries and settings relevant to the content",
        "atmosphere": "intellectual, warm, educational, Vietnamese cultural warmth"
    }},
    "themes": ["theme1", "theme2", "theme3"],
    "visual_style": {{
        "cinematography": "detailed cartoon illustration, Vietnamese aesthetic, rich backgrounds with Vietnamese architecture",
        "color_palette": "warm earthy tones, brick red, golden yellow, jade green, cream, Vietnamese cultural colors",
        "lighting": "warm soft lighting, golden hour, cozy Vietnamese coffee shop atmosphere"
    }},
    "context_lock": "Detailed cartoon illustration, Vietnamese aesthetic, warm earthy color palette, Vietnamese characters with dark hair, rich detailed Vietnamese backgrounds, soft warm lighting, educational documentary for Vietnamese audience"
}}
"""

    # ========== STEP 2: Segments ==========
    def step2_segments(self, context_lock: str, themes: list, total_duration: float,
                       total_srt: int, sampled_text: str) -> str:
        themes_str = ', '.join(themes) if themes else 'Not specified'
        return f"""Analyze this financial history / economics content and divide it into logical segments for video illustration targeting VIETNAMESE audiences.

IMPORTANT: Your segment analysis will be used by later steps to create DETAILED CARTOON ILLUSTRATIONS with Vietnamese aesthetic.
Make your "message" and "key_elements" DETAILED enough to guide illustration creation.
Focus on: HISTORICAL SCENES (with Vietnamese context where relevant), DATA VISUALIZATIONS, MAPS, ECONOMIC DIAGRAMS. NO text/words in images.

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
2. key_elements: List of VISUAL elements (Vietnamese historical scenes, data charts, maps, economic indicators, Vietnamese architecture)
3. visual_summary: 2-3 sentences describing what CARTOON ILLUSTRATIONS with Vietnamese aesthetic should show
4. mood: The emotional/intellectual tone (analytical, dramatic, surprising, sobering, triumphant, cautionary)
5. characters_involved: Which people/roles appear (narrator/historian, historical figures, Vietnamese people, workers, etc.)

Return JSON only:
{{
    "segments": [
        {{
            "segment_id": 1,
            "segment_name": "Introduction/Hook",
            "message": "DETAILED description: what economic argument is introduced, what historical context",
            "key_elements": ["Vietnamese historical scene", "data visualization", "map", "economic indicator"],
            "visual_summary": "2-3 sentences describing cartoon illustrations with Vietnamese aesthetic and data visualizations",
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
        return f"""Based on the content analysis below, identify the narrator/host character and any other characters for DETAILED CARTOON ILLUSTRATION with VIETNAMESE aesthetic.

IMPORTANT VISUAL STYLE - VIETNAMESE CHARACTERS:
- This is a DETAILED CARTOON illustration with Vietnamese cultural identity
- NOT Western/American looking - characters are VIETNAMESE
- Narrator/Host: a warm Vietnamese intellectual (like a Vietnamese professor, historian, or researcher)
- Vietnamese features: warm tan/olive skin tone, dark black hair, dark brown/black eyes
- Clothing: smart casual Vietnamese style (ao so mi, quan tay, or modern Vietnamese fashion)
  * Options: polo shirt, casual blazer, or simple button-up shirt (NOT Western professor with bow tie)
  * Can wear glasses (thin modern frames, NOT round Western professor glasses)
- Vietnamese settings and cultural elements in backgrounds
- Historical figures: cartoon versions maintaining their ethnicity
- Other people in scenes: detailed cartoon Vietnamese people

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
- Narrator/Host: a warm, friendly Vietnamese intellectual
  * VIETNAMESE features: warm tan skin, dark black hair (short neat style or slightly longer modern style)
  * Dark brown or black eyes, friendly warm expression
  * Modern Vietnamese smart casual clothing
- MUST include a DISTINCTIVE VISUAL IDENTIFIER (modern thin glasses, specific hairstyle, rolled-up sleeves, distinctive watch, etc.)
- MUST specify FULL OUTFIT: top (shirt/polo + color), bottom (pants/trousers + color), footwear (shoes + color)
- Historical figures mentioned in content: DO NOT create separate character entries for them. Show them as generic cartoon people in scenes. Only create reference images for the NARRATOR.
- Background people: detailed cartoon Vietnamese people (NOT silhouettes)
- MAXIMUM 1-2 characters total. The narrator is the ONLY character that needs a reference image.

For each character, provide:
1. portrait_prompt: Detailed cartoon Vietnamese character on simple background
2. character_lock: Full character description to be COPY-PASTED into every scene prompt
3. is_minor: true if character is a child

IMPORTANT: The character_lock must include ALL of these elements:
- Ethnicity: Vietnamese
- Face: warm tan skin, specific features
- Hair: dark black hair, specific style
- Distinctive feature: thin modern glasses, specific watch, etc.
- Eyes: dark brown/black eyes
- Expression: default expression (warm, friendly, thoughtful)
- Clothing TOP: specific garment + color (e.g., light blue polo shirt, white button-up shirt with rolled sleeves)
- Clothing BOTTOM: specific garment + color (e.g., dark navy trousers, khaki chinos)
- Footwear: specific type + color (e.g., brown leather loafers, clean white sneakers)
- Style suffix: detailed cartoon illustration, Vietnamese aesthetic, warm earthy tones

Example character_lock: "warm friendly Vietnamese cartoon historian with short neat dark black hair, thin modern glasses, warm tan skin, dark brown eyes, thoughtful friendly expression, light blue polo shirt, dark navy trousers, brown leather loafers, detailed cartoon illustration style, Vietnamese aesthetic"

Return JSON:
{{
    "characters": [
        {{
            "id": "char_id",
            "name": "Name or Role",
            "role": "protagonist/narrator/supporting",
            "portrait_prompt": "Detailed cartoon Vietnamese character, warm tan skin, [face description], dark black hair [hairstyle], [distinctive feature], wearing [full outfit], standing in warm Vietnamese coffee shop setting, detailed cartoon illustration style, Vietnamese aesthetic, warm earthy tones",
            "character_lock": "warm friendly Vietnamese cartoon [role] with dark black hair [style], [distinctive feature], warm tan skin, dark brown eyes, [expression], [full outfit], detailed cartoon illustration style, Vietnamese aesthetic",
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
        return f"""Based on the content analysis below, identify all locations/settings for DETAILED CARTOON ILLUSTRATION with VIETNAMESE aesthetic.

CONTENT CONTEXT:
- Setting: {setting.get('location', 'Not specified')}
- Visual style: {context_lock}

LOCATION HINTS (from segments):
{locs_str}

CONTENT SEGMENTS ANALYSIS:
{segment_insights}

SAMPLE SRT CONTENT:
{targeted_srt_text[:6000] if targeted_srt_text else 'Use segment analysis above'}

LOCATION ILLUSTRATION RULES - VIETNAMESE AESTHETIC:
- Detailed cartoon illustration with VIETNAMESE cultural elements
- Warm earthy color palette: brick red, golden yellow, jade green, cream white
- Vietnamese architecture and settings
- Include: Vietnamese architectural details, furniture, cultural objects
- EMPTY SPACES with NO characters/people
- Types of locations to consider (VIETNAMESE versions):
  * Vietnamese coffee shop (quan ca phe) - narrator's base, warm, with Vietnamese iced coffee, wooden furniture, plants
  * Old quarter streets (pho co) - tube houses, lanterns, motorbikes, street vendors
  * Modern Vietnamese office - clean, bright, with city view
  * Vietnamese market (cho) - colorful stalls, produce, bustling atmosphere
  * Historical Vietnamese settings - French colonial buildings, traditional houses, pagodas
  * City panoramas - Ho Chi Minh City skyline, Hanoi old quarter aerial view
  * River/waterfront scenes - Mekong delta, Saigon river
  * Data/chart room - clean backgrounds with Vietnamese cultural touches for overlaying economic data
  * Countryside - rice paddies, rural Vietnamese landscape

Return JSON only:
{{
    "locations": [
        {{
            "id": "loc_id",
            "name": "Location Name",
            "location_prompt": "Detailed cartoon illustration of [Vietnamese location], [architectural details], [Vietnamese cultural objects], [atmosphere], warm earthy color palette, soft lighting, Vietnamese aesthetic, detailed editorial illustration style, no people, no characters",
            "location_lock": "detailed cartoon [Vietnamese location] with [key Vietnamese feature], warm earthy tones, Vietnamese aesthetic (10-15 words)",
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
        return f"""You are an ILLUSTRATOR for an educational YouTube channel about financial history and economics, targeting VIETNAMESE audiences.
Create exactly {image_count} illustration scenes for this content segment.

IMPORTANT: Each illustration will become a VIDEO CLIP. The visual must CLOSELY MATCH the narration content.
Think about: What HISTORICAL SCENE or DATA VISUALIZATION best illustrates this economic concept, with VIETNAMESE aesthetic?

SEGMENT INFO:
- Name: "{seg_name}"
- Message: "{message}"
- Duration: {seg_duration:.1f} seconds total
- Required: EXACTLY {image_count} illustrations
- Each illustration covers {min_scene_duration}-{max_scene_duration} seconds of narration
- MINIMUM {min_scene_duration} seconds per scene - NO scene shorter than {min_scene_duration}s
- MAXIMUM {max_scene_duration} seconds per scene - NO scene longer than {max_scene_duration}s

VISUAL STYLE (VIETNAMESE AESTHETIC):
{context_lock}

CHARACTERS (Vietnamese cartoon):
{chr(10).join(char_locks) if char_locks else 'No characters defined'}

LOCATIONS (Vietnamese settings):
{chr(10).join(loc_locks) if loc_locks else 'No locations defined'}

SRT CONTENT FOR THIS SEGMENT:
{srt_text}

ILLUSTRATION TECHNIQUES FOR FINANCE/HISTORY (VIETNAMESE AESTHETIC):
- HISTORICAL SCENES: Vietnamese and world historical settings in cartoon style (markets, government buildings, factories, harbors)
- When the content discusses OTHER COUNTRIES (USA, Europe, Asia): show those countries' settings BUT maintain the warm Vietnamese illustration style
- DATA VISUALIZATIONS: charts, graphs with NUMBERS and ARROWS (no text labels) - line charts, bar charts, pie charts
- MAPS: cartoon maps showing countries, trade routes, economic zones with arrows and symbols
- TIMELINE SCENES: showing progression through decades with visual indicators
- COMPARISON/CONTRAST: split screen showing before/after, country A vs country B
- NARRATOR IN COFFEE SHOP: Vietnamese historian character at Vietnamese coffee shop with iced coffee, explaining with visual aids
- CITY PANORAMAS: Vietnamese cityscapes (Saigon skyline, Hanoi old quarter) or foreign cities in Vietnamese illustration style
- IMPORTANT: NO TEXT/WORDS/LABELS in the image - use NUMBERS, CURRENCY SYMBOLS (₫, $, €), ARROWS, PERCENTAGE SIGNS instead

INSTRUCTIONS:
1. Create EXACTLY {image_count} illustration scenes - no more, no less
2. Each scene must VISUALLY REPRESENT the narration content
3. DURATION RULES: Each scene MUST be {min_scene_duration}-{max_scene_duration} seconds. NEVER create a scene shorter than {min_scene_duration}s or longer than {max_scene_duration}s.
4. Use Vietnamese aesthetic for ALL illustrations (even when depicting foreign countries)
5. Use EXACT character/location IDs from the lists above
6. scene_id: just use 1, 2, 3...
6. NARRATOR PRESENCE: The narrator/historian character should appear in AT LEAST 60% of scenes (at coffee shop, walking through scenes, pointing at chart). Other scenes can be pure historical illustrations or data visualizations.
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
            "visual_moment": "DETAILED description of what the illustration shows - Vietnamese aesthetic, include historical scenes, data visualizations, maps. NO text/words in image",
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
        return f"""You are an illustrator planning each scene's visual approach for an educational YouTube channel about financial history, targeting VIETNAMESE audiences.

IMPORTANT: Each scene becomes a VIDEO CLIP. Plan visuals that CLOSELY MATCH the narration.

VISUAL STYLE: Detailed cartoon illustration, Vietnamese aesthetic, warm earthy color palette, rich Vietnamese backgrounds.

CONTENT CONTEXT:
{context_lock}

CONTENT SEGMENTS:
{segments_info if segments_info else 'Not specified'}

CHARACTERS (Vietnamese cartoon):
{char_info if char_info else 'Not specified'}

LOCATIONS (Vietnamese settings):
{loc_info if loc_info else 'Not specified'}

SCENES TO PLAN:
{scenes_text}

For EACH scene, plan the cartoon illustration using these techniques:
1. artistic_intent: What economic concept or historical event to visualize? How to make it relatable for Vietnamese viewers?
2. shot_type: Composition (panoramic cityscape, close-up character, split screen comparison, bird's eye map, data visualization, coffee shop with visual aid)
3. character_action: What is narrator doing? (at coffee shop explaining, walking through Vietnamese street, pointing at chart, observing from balcony)
4. mood: Overall feeling (analytical, dramatic, surprising, sobering, triumphant, cautionary, nostalgic)
5. lighting: Warm golden / soft cafe lamp / dramatic contrast / historical atmosphere
6. color_palette: Dominant colors for this specific scene (using Vietnamese earthy tones)
7. key_focus: What viewer should notice first (data trend, historical scene, character reaction, map)

VISUAL TECHNIQUES FOR FINANCE/HISTORY (VIETNAMESE AESTHETIC):
- PANORAMIC CITYSCAPES: Vietnamese cities or foreign cities rendered in Vietnamese illustration style
- SPLIT SCREEN comparisons (before/after, country A vs B) - USE for 15-20% of scenes
- DATA VISUALIZATIONS as part of the scene (charts on walls, floating graphs, desk with papers showing numbers)
- MAPS with arrows showing trade flows, economic movement, capital flows
- HISTORICAL SCENES: factories, markets, harbors in warm Vietnamese illustration style
- NARRATOR AT COFFEE SHOP: Vietnamese historian in cozy cafe, iced Vietnamese coffee, surrounded by books
- VIETNAMESE STREET SCENES: old quarter, motorbikes, street vendors, lanterns, tube houses
- TIMELINE TRANSITIONS: visual indicators showing passage of decades
- IMPORTANT: NO TEXT/WORDS/LABELS in prompts - use NUMBERS, ARROWS, CURRENCY SYMBOLS, PERCENTAGE SIGNS

COMPOSITION VARIETY REQUIREMENT:
- At least 2-3 scenes MUST use SPLIT SCREEN or panoramic comparison
- At least 2-3 scenes should show DATA VISUALIZATIONS or MAPS
- Mix: narrator coffee shop scenes, historical illustrations, city panoramas, data scenes, Vietnamese street scenes

Return JSON only:
{{
    "scene_plans": [
        {{
            "scene_id": 1,
            "artistic_intent": "Show the contrast between perceived wealth and actual economic fundamentals",
            "shot_type": "Split screen comparison, warm vs cool tones",
            "character_action": "Narrator at Vietnamese coffee shop, holding up a small tablet showing two charts",
            "mood": "Analytical, myth-busting",
            "lighting": "Warm cafe lamp on narrator side, cool blue on data side",
            "color_palette": "Golden amber narrator, brick red accents, jade green data visualization",
            "key_focus": "GDP chart showing growth timeline with key turning point marked"
        }}
    ]
}}
"""

    # ========== STEP 7: Scene Prompts ==========
    def step7_scene_prompts(self, context_lock: str, scenes_text: str,
                             batch_size: int) -> str:
        return f"""Create detailed CARTOON ILLUSTRATION prompts with VIETNAMESE AESTHETIC for these {batch_size} scenes.

#1 PRIORITY - CONTENT MATCHING (QUAN TRONG NHAT):
- The img_prompt MUST illustrate EXACTLY what the narrator is SAYING in the "Text" field
- Read the "Text" field carefully - this is what viewers HEAR. The image MUST match what they hear.
- If Text talks about "tăng trưởng GDP thập niên 1990" → image MUST show 1990s Vietnamese economic scene
- If Text talks about "khủng hoảng tài chính" → image MUST show financial crisis scene
- If Text talks about "xuất khẩu gạo" → image MUST show rice export/trade scene
- DO NOT create generic "narrator ngồi ở quán cà phê" images for every scene
- Each scene's image must be SPECIFIC to its narration content
- "Visual moment" is a GUIDE, but "Text" is the TRUTH - if they conflict, follow "Text"
- The narrator can appear IN the historical scene (not always at cafe)

VISUAL STYLE - VIETNAMESE AESTHETIC (MUST follow for ALL scenes):
{context_lock}

ABSOLUTE RULE - NO TEXT IN IMAGES:
- NEVER include ANY English, Vietnamese, or other language WORDS in image prompts
- NEVER use quoted strings like 'WEALTH CREATION', 'GDP GROWTH' etc.
- NEVER request readable text on signs, documents, banners, headlines, labels
- INSTEAD USE: numbers (3.5%, $100M), currency symbols (₫, $, €), arrows (↑↓), flag icons, building icons, percentage signs (%)
- INSTEAD OF text labels, use VISUAL REPRESENTATIONS: a hospital = building with red cross, a school = building with book icon, wealth = gold coins/stacks

CRITICAL ILLUSTRATION STYLE RULES - VIETNAMESE AESTHETIC:
- DETAILED cartoon illustration with Vietnamese cultural identity
- Characters are VIETNAMESE: warm tan skin, dark black hair, dark eyes
- Warm earthy color palette: brick red, golden yellow, jade green, cream white
- Rich detailed backgrounds with Vietnamese architecture (tube houses, French colonial, modern Vietnamese)
- Vietnamese cultural elements: iced coffee (ca phe sua da), conical hats in background, motorbikes, lanterns, tropical plants
- Other people in scenes: detailed cartoon Vietnamese people (NOT Western-looking)
- Show economic data as VISUAL CHARTS, GRAPHS with numbers and arrows (NO text labels)
- Use MAPS with arrows for trade/capital flows
- When depicting OTHER countries: show their settings but maintain Vietnamese illustration style warmth
- DO NOT write meta-labels like "data visualization:" - just DESCRIBE what the illustration shows

REFERENCE FILE ANNOTATIONS:
- Narrator character MUST have reference file: "warm Vietnamese cartoon historian (nv1.png)"
- Location MUST have reference file: "in cozy Vietnamese coffee shop (loc_cafe.png)"
- Character description from character_lock should be INCLUDED in the prompt
- Format: "[full character_lock description] (nv_xxx.png) [action] in [location description] (loc_xxx.png)"

SCENES TO PROCESS ({batch_size} scenes - create EXACTLY {batch_size} prompts):
{scenes_text}

CRITICAL REQUIREMENTS:
1. Create EXACTLY {batch_size} scene prompts
2. #1 PRIORITY: Each img_prompt MUST illustrate the SPECIFIC content from the "Text" field - NOT generic "narrator ở quán cà phê"
3. Include the FULL character description (from character_lock) in every prompt where character appears
4. Describe data visualizations as ACTUAL OBJECTS in the scene
5. NO TEXT/WORDS/LABELS in image prompts - use NUMBERS, ARROWS, CURRENCY SYMBOLS, PERCENTAGE SIGNS instead
6. End every prompt with: "detailed cartoon illustration style, Vietnamese aesthetic, warm earthy tones, soft lighting"

For each scene, create:
1. img_prompt: Detailed illustration prompt describing what the image SHOWS (not meta-instructions). NO TEXT IN IMAGE.
2. video_prompt: Animation description (camera movement, character actions, data animation). MUST end with style: "detailed cartoon animation style, Vietnamese aesthetic, warm earthy tones, soft lighting"
   - CRITICAL: video_prompt MUST maintain the SAME detailed cartoon animation style as img_prompt
   - Character in video: use FULL character_lock description (Vietnamese features, specific clothes, etc.)
   - Other people in video: detailed cartoon Vietnamese people (NOT silhouettes)
   - DO NOT create realistic/photorealistic video - keep detailed cartoon animation style
   - Vietnamese setting: motorbikes, tropical plants, French colonial architecture
   - Color palette: brick red, golden, jade green, warm earthy tones

Example img_prompt (GOOD - narrator at Vietnamese coffee shop):
"Cozy Vietnamese coffee shop with wooden tables and green tropical plants, vintage Vietnamese posters on brick walls, warm Vietnamese cartoon historian with short neat dark black hair, thin modern glasses, warm tan skin, light blue polo shirt (nv1.png) sitting at wooden table with Vietnamese iced coffee (ca phe sua da) in tall glass, holding up a small cartoon globe, warm golden light from hanging lanterns, motorbikes visible through open front, ceiling fan slowly turning (loc_cafe.png), detailed cartoon illustration style, Vietnamese aesthetic, warm earthy tones, soft lighting"

Example img_prompt (GOOD - Vietnamese historical scene):
"Panoramic cartoon illustration of 1960s Saigon harbor in Vietnamese illustration style, cargo ships at busy docks, French colonial warehouse buildings with warm yellow facades, Vietnamese dock workers in conical hats loading crates, large upward-pointing green arrow symbol floating above the harbor indicating trade growth, warm golden sunset over Saigon River, detailed period Vietnamese architecture, tropical trees lining the waterfront (loc_harbor.png), detailed cartoon illustration style, Vietnamese aesthetic, warm earthy tones, soft lighting"

Example img_prompt (GOOD - data visualization Vietnamese style):
"SPLIT SCREEN composition divided by vertical golden bamboo line. LEFT SIDE warm brick red tones: cartoon illustration of thriving Vietnamese factory with workers, motorbikes parked outside, Vietnamese flag, upward green arrow and numbers 7.2% above. RIGHT SIDE cooler jade green tones: same factory in later period looking modernized with new technology, fewer workers, numbers 3.1% above, warm Vietnamese cartoon historian with short dark hair, thin glasses (nv1.png) standing between both sides gesturing at comparison, detailed cartoon illustration style, Vietnamese aesthetic, warm earthy tones, soft lighting"

BAD examples (DO NOT write like this - has text/words):
- "Chart labeled 'GDP Growth Rate' with text annotations"
- "Document with text 'DOI MOI REFORM'"
- "Sign reading 'Bank' on the building"
- "Banner saying 'Economic Development'"
GOOD versions (visual only, NO words - use icons, flags, numbers):
- "Large cartoon line chart on warm parchment background, green line trending upward, Vietnamese flag icon at top, other country flag icons below, numbers 7.2%, 3.5% next to data points"
- "Two golden scale pans: LEFT pan heavy with factory icons and dong currency stacks, RIGHT pan lighter with hospital and school building icons, golden arrow pointing from left to right"
- "Modern Vietnamese bank building with Vietnamese flag, golden coin stacks visible through glass windows, green upward arrows"
- "Vietnamese family in modern apartment, laptop on table, large green checkmark above, Vietnamese flag pin on shelf"

Example video_prompt (GOOD - includes full style):
"Cozy Vietnamese coffee shop with tropical plants and hanging lanterns. Vietnamese cartoon historian with short dark hair, thin glasses, light blue polo shirt (nv1.png) sitting at wooden table with iced coffee, picks up small cartoon globe and rotates it while explaining. Ceiling fan slowly turning above, motorbikes passing by outside. Camera slowly zooms in on his expression. Detailed cartoon animation style, Vietnamese aesthetic, warm earthy tones, soft lighting"

Return JSON only with EXACTLY {batch_size} scenes:
{{
    "scenes": [
        {{
            "scene_id": 1,
            "img_prompt": "DETAILED illustration prompt with Vietnamese aesthetic, character description (nv_xxx.png) and location (loc_xxx.png), NO TEXT IN IMAGE, detailed cartoon illustration style, Vietnamese aesthetic, warm earthy tones, soft lighting",
            "video_prompt": "Character description (nv_xxx.png) [action], [camera movement], detailed cartoon animation style, Vietnamese aesthetic, warm earthy tones, soft lighting"
        }}
    ]
}}
"""

    # ========== STEP 8: Thumbnail ==========
    def step8_thumbnail(self, setting: dict, themes: list, visual_style: dict,
                        context_lock: str, protagonist, chars_info: str,
                        locs_info: str, char_ids: list, loc_ids: list) -> str:
        return f"""You are a YouTube thumbnail designer for an EDUCATIONAL FINANCIAL HISTORY channel targeting VIETNAMESE audiences.
Create 3 compelling thumbnail image prompts in DETAILED CARTOON ILLUSTRATION style with VIETNAMESE AESTHETIC.

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

VISUAL STYLE RULES - VIETNAMESE AESTHETIC:
- Detailed cartoon illustration with Vietnamese cultural identity
- Warm earthy color palette (brick red, golden yellow, jade green, cream)
- Vietnamese characters (tan skin, dark hair, dark eyes)
- Rich detailed Vietnamese backgrounds
- Character must match the character_lock description exactly
- Thumbnails CAN have TEXT/WORDS (unlike scene images) - use bold, short Vietnamese text for CTR
- Include BOLD typography that hooks Vietnamese viewers

RULES FOR PROMPTS:
1. Write in English, detailed cartoon illustration style with Vietnamese aesthetic
2. MUST annotate references EXACTLY like this:
   - Character: "warm Vietnamese historian (nv1.png)" or "(nv1.png) looking at chart"
   - Location: "in cozy Vietnamese cafe (loc1.png)"
3. Include the FULL character description (from character_lock) in every prompt
4. Each prompt MUST be unique in composition and emotional appeal
5. End every prompt with: "detailed cartoon illustration style, Vietnamese aesthetic, warm earthy tones, soft lighting"
6. Include BOLD SHORT TEXT (1-3 words in Vietnamese or English) as visual hook in at least 2 of 3 thumbnails

CREATE EXACTLY 3 THUMBNAIL PROMPTS:

VERSION 1 - "portrait_main" (CHARACTER CLOSE-UP):
Goal: Vietnamese narrator/historian with strong expression representing the video's core economic thesis.
Style: Close-up, Vietnamese character looking at viewer with warm analytical expression, Vietnamese cultural element nearby (iced coffee, books, chart), bold text.
Emotion: Intellectual curiosity, friendly expertise.

VERSION 2 - "concept_visual" (DATA/HISTORY SCENE):
Goal: The most compelling economic data point or historical scene. Makes Vietnamese viewers think "Wait, really?!"
Style: Character presenting surprising data, Vietnamese street scene or coffee shop background, bold text label.
Emotion: Surprise, revelation.

VERSION 3 - "youtube_ctr" (MAXIMUM CLICK-THROUGH):
Goal: Maximum CTR using contrast/surprise for Vietnamese audience.
Style: Split screen or dramatic contrast, character with surprised/knowing expression, Vietnamese cultural elements, bold floating text hook.
Emotion: "Everything you thought was WRONG", contrarian revelation.

Return JSON only:
{{
  "thumbnails": [
    {{
      "thumb_id": 1,
      "version_desc": "portrait_main",
      "img_prompt": "...full prompt with Vietnamese aesthetic, (nvX.png) and (locX.png), ending with detailed cartoon illustration style, Vietnamese aesthetic, warm earthy tones, soft lighting",
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
