"""
Story topic prompts - Phim truyen / Drama.
Anh photorealistic, nhan vat thuc te, boi canh cinematic.
"""


class StoryPrompts:
    """Prompts cho chu de Story (hien tai - mac dinh)."""

    TOPIC_NAME = "story"
    TOPIC_LABEL = "Phim truyen / Drama"

    # ========== FALLBACK & UTILITIES ==========
    def fallback_style(self) -> str:
        """Style suffix cho fallback prompts khi API fail."""
        return "Cinematic 4K, dramatic lighting, photorealistic, film quality."

    def fallback_video_style(self) -> str:
        return "Smooth cinematic camera movement"

    def split_scene_prompt(self, duration: float, min_shots: int, max_shots: int,
                           srt_start: str, srt_end: str, srt_text: str,
                           visual_moment: str, characters_used: str,
                           location_used: str, char_locks: list, loc_locks: list) -> str:
        """Prompt de chia scene dai thanh nhieu shots."""
        return f"""You are a FILM DIRECTOR. This scene is {duration:.1f} seconds - TOO LONG for one shot (max 8s).
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
1. Each shot MUST be 4-8 seconds (divide the {duration:.1f}s total). MINIMUM 4 seconds per shot
2. Each shot must show DIFFERENT aspect: angle, focus, emotion
3. All shots together must cover the FULL narration
4. Use EXACT character/location IDs from the lists above
5. Think cinematically - what sequence of shots tells this story best?

Examples of good splits:
- Character making decision: Close-up face -> Insert object -> Wide shot reaction
- Two people talking: Speaker close-up -> Listener reaction -> Two-shot
- Action sequence: Wide establishing -> Medium action -> Close-up detail

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

    def has_narrator_role(self) -> bool:
        """Story co narrator rieng (nvc)."""
        return True

    # ========== STEP 1: Analyze Story ==========
    def step1_analyze(self, sampled_text: str) -> str:
        return f"""Analyze this story and extract key information for visual production.

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

    # ========== STEP 2: Segments ==========
    def step2_segments(self, context_lock: str, themes: list, total_duration: float,
                       total_srt: int, sampled_text: str) -> str:
        themes_str = ', '.join(themes) if themes else 'Not specified'
        return f"""Analyze this story and divide it into content segments for video creation.

IMPORTANT: Your segment analysis will be used by later steps to create visuals WITHOUT re-reading the full story.
So make your "message" and "key_elements" DETAILED enough to guide visual creation.

STORY CONTEXT:
{context_lock}

THEMES: {themes_str}

TOTAL DURATION: {total_duration:.1f} seconds
TOTAL SRT ENTRIES: {total_srt}

STORY CONTENT (SAMPLED - beginning + middle + end):
{sampled_text}

TASK: Divide the story into logical segments. Each segment is a distinct part of the narrative.

CRITICAL REQUIREMENT:
- Your segments MUST cover ALL {total_srt} SRT entries
- First segment starts at srt_range_start: 1
- Last segment MUST end at srt_range_end: {total_srt}
- NO gaps between segments (segment N ends where segment N+1 starts)

For each segment, provide DETAILED information (this will guide image creation):
1. message: The narrative purpose - what story is being told? What happens?
2. key_elements: List of VISUAL elements (characters, locations, objects, actions, emotions)
3. visual_summary: A 2-3 sentence description of what images should show for this segment
4. mood: The emotional tone (tense, warm, sad, hopeful, dramatic, etc.)
5. characters_involved: Which characters appear in this segment

YOUR TASK: Divide the story into logical narrative segments ONLY.
DO NOT calculate image_count - focus on identifying distinct story parts.

Return JSON only:
{{
    "segments": [
        {{
            "segment_id": 1,
            "segment_name": "Opening/Introduction",
            "message": "DETAILED narrative: what happens, who is involved, what's the conflict/emotion",
            "key_elements": ["character doing action", "specific location", "emotional state", "important object"],
            "visual_summary": "2-3 sentences describing what the images should show",
            "mood": "melancholic/hopeful/tense/etc",
            "characters_involved": ["main character", "supporting character"],
            "estimated_duration": 15.0,
            "srt_range_start": 1,
            "srt_range_end": 25,
            "importance": "high/medium/low"
        }}
    ],
    "summary": "Brief overview of the story structure"
}}
"""

    # ========== STEP 3: Characters ==========
    def step3_characters(self, setting: dict, context_lock: str,
                         all_characters_mentioned: list, segment_insights: str,
                         targeted_srt_text: str) -> str:
        chars_str = ', '.join(all_characters_mentioned) if all_characters_mentioned else 'Analyze from story segments below'
        return f"""Based on the story analysis below, identify all characters and create visual descriptions.

STORY CONTEXT (from Step 1):
- Era: {setting.get('era', 'Not specified')}
- Location: {setting.get('location', 'Not specified')}
- Visual style: {context_lock}

CHARACTERS TO LOOK FOR (from Step 1.5 segments):
{chars_str}

STORY SEGMENTS ANALYSIS (from Step 1.5 - this tells you WHAT happens and WHO is involved):
{segment_insights}

SAMPLE SRT CONTENT (for character dialogue/description details):
{targeted_srt_text[:8000] if targeted_srt_text else 'Use segment analysis above'}

For each character, provide:
1. portrait_prompt: Portrait on pure white background, 85mm lens, front-facing, Caucasian, photorealistic 8K, NO TEXT
2. character_lock: Short 10-15 word description for scene prompts
3. is_minor: true if under 18 (child, teenager, baby, etc.)

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

    # ========== STEP 4: Locations ==========
    def step4_locations(self, setting: dict, context_lock: str, char_names: list,
                        all_locations_hints: list, segment_insights: str,
                        targeted_srt_text: str) -> str:
        locs_str = ', '.join(all_locations_hints) if all_locations_hints else 'Analyze from story segments below'
        return f"""Based on the story analysis below, identify all locations and create visual descriptions.

STORY CONTEXT (from Step 1):
- Era: {setting.get('era', 'Not specified')}
- Location type: {setting.get('location', 'Not specified')}
- Visual style: {context_lock}
- Characters: {', '.join(char_names[:5])}

LOCATION HINTS (from Step 1.5 key_elements):
{locs_str}

STORY SEGMENTS ANALYSIS (from Step 1.5 - shows WHERE scenes take place):
{segment_insights}

SAMPLE SRT CONTENT (for location description details):
{targeted_srt_text[:6000] if targeted_srt_text else 'Use segment analysis above'}

For each location, provide:
1. location_prompt: Full description for generating a reference image
2. location_lock: Short description to use in scene prompts

IMPORTANT RULES FOR LOCATION IMAGES:
- Locations MUST be EMPTY SPACES with NO PEOPLE at all
- ABSOLUTELY NO children under 18 years old
- ABSOLUTELY NO human figures, faces, or body parts
- Only show: architecture, environment, landscape, objects, furniture, nature
- Focus on: lighting, atmosphere, mood, spatial composition

Return JSON only:
{{
    "locations": [
        {{
            "id": "loc_id",
            "name": "Location Name",
            "location_prompt": "detailed location description for image generation",
            "location_lock": "short description for scene prompts (10-15 words)",
            "lighting_default": "default lighting for this location"
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
        return f"""You are a FILM DIRECTOR. Create exactly {image_count} cinematic shots for this story segment.

SEGMENT INFO:
- Name: "{seg_name}"
- Message: "{message}"
- Duration: {seg_duration:.1f} seconds total
- Required: EXACTLY {image_count} scenes
- IMPORTANT: Each scene must be {min_scene_duration}-{max_scene_duration} seconds (average ~{scene_duration:.1f}s)
- MINIMUM {min_scene_duration} seconds per scene - NO scene shorter than {min_scene_duration}s
- MAXIMUM {max_scene_duration} seconds per scene - NO scene longer than {max_scene_duration}s

STORY CONTEXT:
{context_lock}

CHARACTERS:
{chr(10).join(char_locks) if char_locks else 'No characters defined'}

LOCATIONS:
{chr(10).join(loc_locks) if loc_locks else 'No locations defined'}

SRT CONTENT FOR THIS SEGMENT:
{srt_text}

INSTRUCTIONS:
1. Create EXACTLY {image_count} scenes - no more, no less
2. Each scene duration: {min_scene_duration}s <= duration <= {max_scene_duration}s
3. Distribute the SRT content evenly across all {image_count} scenes
4. Each scene = one cinematic shot that supports the narration
5. Use EXACT character/location IDs from the lists above
6. scene_id: just use 1, 2, 3... (will be renumbered later)
7. IMPORTANT - REFERENCES ACCURACY:
   - characters_used: ONLY characters who ACTUALLY APPEAR in that specific scene's narration/action. Do NOT add characters just because they exist in the story. If a scene shows only scenery or has no character, leave characters_used EMPTY ""
   - location_used: ONLY ONE location per scene (the main setting). Do NOT list multiple locations. If unclear, use the most relevant one
   - Analyze the srt_text carefully to determine which characters are speaking or being described

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
            "visual_moment": "what viewer sees - specific and purposeful",
            "characters_used": "nv_xxx, nv_yyy",
            "location_used": "loc_xxx",
            "camera": "shot type (close-up, wide, medium, etc.)",
            "lighting": "lighting description"
        }}
    ]
}}
Create exactly {image_count} scenes!"""

    # ========== STEP 6: Scene Planning ==========
    def step6_scene_planning(self, context_lock: str, segments_info: str,
                              char_info: str, loc_info: str, scenes_text: str) -> str:
        return f"""You are a film director planning each scene's artistic vision.

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

For EACH scene, create an artistic plan that includes:
1. artistic_intent: What emotion/message should this scene convey?
2. shot_type: Camera angle and framing (close-up, medium, wide, etc.)
3. character_action: What are characters doing? Their body language, expression?
4. mood: Overall feeling (tense, warm, melancholic, hopeful, etc.)
5. lighting: Type of lighting (soft, harsh, dramatic, natural, etc.)
6. color_palette: Dominant colors for the scene
7. key_focus: What should viewer's eye be drawn to?

Return JSON only:
{{
    "scene_plans": [
        {{
            "scene_id": 1,
            "artistic_intent": "Show the protagonist's isolation and loneliness",
            "shot_type": "Wide shot, slowly pushing in",
            "character_action": "Sitting alone, shoulders slumped, staring at window",
            "mood": "Melancholic, contemplative",
            "lighting": "Soft diffused light from window, shadows on face",
            "color_palette": "Cool blues and grays, muted tones",
            "key_focus": "Character's face and empty space around them"
        }}
    ]
}}
"""

    # ========== STEP 7: Scene Prompts ==========
    def step7_scene_prompts(self, context_lock: str, scenes_text: str,
                             batch_size: int) -> str:
        return f"""Create detailed image prompts for these {batch_size} scenes.

#1 PRIORITY - CONTENT MATCHING (MOST IMPORTANT):
- The img_prompt MUST illustrate EXACTLY what the narrator is SAYING in the "Text" field
- Read the "Text" field carefully - this is what viewers HEAR. The image MUST match what they hear.
- DO NOT create generic/abstract images that could match ANY scene
- Each scene's image must be SPECIFIC to its narration content
- "Visual moment" is a GUIDE, but "Text" is the TRUTH - if they conflict, follow "Text"

VISUAL CONTEXT (use as prefix):
{context_lock}

IMPORTANT - REFERENCE FILE ANNOTATIONS:
- ONLY include references for characters/locations that ACTUALLY APPEAR in the scene
- Each character who appears MUST have their reference file in parentheses: "a man (nv1.png)"
- Location where scene takes place MUST have reference file: "in the room (loc1.png)"
- Format: "Description of person (nv_xxx.png) doing action in location (loc_xxx.png)"
- Character files always start with "nv_" or "nv", location files always start with "loc_" or "loc"
- Do NOT add references for characters who are NOT in the scene
- Use ONLY ONE location per scene (the main setting)

SCENES TO PROCESS ({batch_size} scenes - create EXACTLY {batch_size} prompts):
{scenes_text}

CRITICAL REQUIREMENTS:
1. Create EXACTLY {batch_size} scene prompts - one for EACH scene listed above
2. Each img_prompt MUST be UNIQUE - do NOT copy/repeat prompts between scenes
3. Each prompt should reflect the specific visual_moment and text of that scene
4. Use the exact scene_id from the input

For each scene, create:
1. img_prompt: UNIQUE detailed image generation prompt with REFERENCE ANNOTATIONS
2. video_prompt: Motion/video prompt if this becomes a video clip

Example img_prompt:
"Close-up shot, 85mm lens, a 35-year-old man with tired eyes (nv_john.png) sitting at a desk, looking worried, soft window light, in a modern office (loc_office.png), cinematic, 4K"

Return JSON only with EXACTLY {batch_size} scenes:
{{
    "scenes": [
        {{
            "scene_id": 1,
            "img_prompt": "UNIQUE detailed prompt with (character.png) and (location.png) annotations...",
            "video_prompt": "camera movement and action description..."
        }}
    ]
}}
"""

    # ========== STEP 8: Thumbnails ==========
    def step8_thumbnail(self, setting: dict, themes: list, visual_style: dict,
                        context_lock: str, protagonist, chars_info: str,
                        locs_info: str, char_ids: list, loc_ids: list) -> str:
        return f"""You are an expert YouTube thumbnail designer and cinematographer.
Create 3 compelling thumbnail image prompts for a YouTube video based on this story.

STORY CONTEXT:
- Setting: {setting}
- Themes: {themes}
- Visual style: {visual_style}
- Context lock: {context_lock}

MAIN CHARACTER (protagonist): {protagonist.id} ({protagonist.name})
Character description: {protagonist.character_lock or protagonist.english_prompt}

ALL CHARACTERS:
{chars_info}

LOCATIONS:
{locs_info}

AVAILABLE REFERENCE IDs:
- Characters: {char_ids}
- Locations: {loc_ids}

RULES FOR PROMPTS:
1. Write in English, cinematic style, highly detailed
2. MUST annotate references EXACTLY like this:
   - Character: "a beautiful woman (nv1.png)" or "(nv1.png) standing proud"
   - Location: "in the grand hall (loc1.png)" or "(loc2.png) background"
3. Choose the most emotionally powerful character + location combination
4. Each prompt MUST be unique in composition, angle, and emotional tone
5. THUMBNAIL OPTIMIZED: close-up face or upper body, strong contrast, bold expression

CREATE EXACTLY 3 THUMBNAIL PROMPTS:

VERSION 1 - "portrait_main" (ASPIRATIONAL PORTRAIT):
Goal: Main character at their most beautiful/powerful/attractive. The ideal version viewers want to see or become.
Style: Glamorous close-up, perfect lighting, aspirational expression (confident, serene, powerful).
Emotion: Desire, admiration, aspiration.

VERSION 2 - "dramatic_scene" (CURIOSITY / TENSION):
Goal: The most dramatic, tense, or emotionally charged moment. Creates "what happened?!" reaction.
Style: Medium shot or close-up, dynamic composition, intense expression (fear, rage, tears, shock).
Emotion: Curiosity, tension, suspense.

VERSION 3 - "youtube_ctr" (MAXIMUM CLICK-THROUGH):
Goal: Maximum CTR using proven YouTube formula: expressive face + implicit context + visual hook.
Style: Extreme close-up face with BIG EMOTION, simple high-contrast background, one clear focal point.
Emotion: Surprise, shock, disbelief, intense joy — whatever fits the story best.

Return JSON only:
{{
  "thumbnails": [
    {{
      "thumb_id": 1,
      "version_desc": "portrait_main",
      "img_prompt": "...full prompt with (nvX.png) and (locX.png) annotations...",
      "characters_used": "nv1",
      "location_used": "loc1"
    }},
    {{
      "thumb_id": 2,
      "version_desc": "dramatic_scene",
      "img_prompt": "...",
      "characters_used": "nv1",
      "location_used": "loc1"
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
