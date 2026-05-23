"""Topic-specific prompt templates for the SRT-to-Excel generator."""

import unicodedata


def normalize_topic_key(value: str = "story") -> str:
    text = str(value or "story").strip().lower()
    text = unicodedata.normalize("NFKD", text)
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = text.replace("_", " ").replace("-", " ")
    return " ".join(text.split())


class TopicPrompts:
    TOPIC_NAME = "story"

    def set_style_profile(self, profile: dict) -> None:
        self.style_profile = profile or {}

    def normalize_scene_characters(self, value: str) -> str:
        return value

    def get_default_character(self, override: str = ""):
        return None

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

    def segment_prompt(self, srt_content: str, entry_start: int, entry_end: int,
                       total_entries: int, total_duration: float, context_lock: str,
                       themes: list, is_part: bool = False, part_label: str = "") -> str:
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


class StoryPrompts(TopicPrompts):
    TOPIC_NAME = "story"


class _StyledTopicPrompts(TopicPrompts):
    """Base class for topics that use a fixed reference character + style profile (psychology, finance, etc.)."""

    TOPIC_NAME = "styled"
    TOPIC_LABEL = "educational"
    TOPIC_DOMAIN = "educational content"
    TOPIC_THEMES_DEFAULT = ["educational concept", "human behavior", "self-improvement"]
    TOPIC_VISUAL_RULES_LABEL = "EDUCATIONAL"
    TOPIC_SEGMENT_EXAMPLE_MESSAGE = "DETAILED explanation of the idea or situation"
    TOPIC_SEGMENT_EXAMPLE_ELEMENTS = ["visual metaphor", "nv1 emotional pose", "symbolic object", "anonymous silhouettes"]
    TOPIC_SEGMENT_EXAMPLE_SUMMARY = "2-3 sentences describing visuals in the channel style for this segment."
    TOPIC_SEGMENT_EXAMPLE_QUESTION = "What internal conflict or realization is being explored?"
    TOPIC_DEFAULT_LOCK = (
        "cute minimalist channel character with round white head, simple dot eyes, "
        "calm thoughtful expression, small green sprout on top, soft blue shirt, beige pants, "
        "white sneakers, clean black outline illustration style"
    )
    TOPIC_DEFAULT_PORTRAIT = (
        "Cute minimalist channel character, round white head, simple dot eyes, "
        "calm thoughtful expression, small green sprout on top, soft blue shirt, beige pants, "
        "white sneakers, standing on pure white background, clean black outline illustration style, "
        "paper texture, no text, no watermark"
    )
    TOPIC_DEFAULT_IMAGE_STYLE = "Clean minimalist illustration style, paper texture background, warm educational YouTube aesthetic"
    TOPIC_METAPHOR_LABEL = "metaphors"

    def __init__(self):
        self.style_profile = {}

    def _style(self, key: str, default: str = "") -> str:
        return str((self.style_profile or {}).get(key) or default).strip()

    def _audience_insight_block(self, strict: bool = False) -> str:
        audience_language = self._style("audience_language", "")
        if not audience_language:
            return ""
        audience_culture_note = self._style("audience_culture_note", "")
        cultural_props = self._style("cultural_props", "")
        cultural_metaphors = self._style("cultural_metaphors", "")
        cultural_emotion_style = self._style("cultural_emotion_style", "")
        strict_line = ""
        if strict:
            strict_line = (
                "\nSRT-FIRST RULE: The narration decides the visual. Use audience-familiar settings, props, "
                "rituals, or metaphors only when they directly clarify the current SRT idea. "
                "Do not force cultural objects into every segment."
            )
        return f"""
AUDIENCE INSIGHT BIBLE:
- Audience language: {audience_language}
- Cultural context: {audience_culture_note or 'Universal daily life for this language audience'}
- Preferred props/settings/rituals: {cultural_props or 'Use concrete daily-life objects familiar to this audience'}
- Preferred {self.TOPIC_METAPHOR_LABEL}: {cultural_metaphors or 'Use audience-familiar metaphors, not generic symbols'}
- Emotional expression style: {cultural_emotion_style or 'Match how this audience usually reads vulnerability, restraint, warmth, and healing'}
Goal: the viewer should feel "this was made for people like me" within one second.{strict_line}
"""

    def normalize_scene_characters(self, value: str) -> str:
        text = str(value or "").strip()
        if not text or text in ("[]", "none", "None"):
            return ""
        return "nv1"

    def get_default_character(self, override: str = ""):
        if override and override.strip():
            prompt = override.strip()
            return {
                "name": "Main Character",
                "role": "protagonist",
                "portrait_prompt": prompt,
                "character_lock": prompt,
                "is_minor": False,
            }
        lock = self._style("default_character_lock", self.TOPIC_DEFAULT_LOCK)
        portrait_prompt = self._style("default_character_prompt", self.TOPIC_DEFAULT_PORTRAIT)
        return {
            "name": "Main Character",
            "role": "protagonist",
            "portrait_prompt": portrait_prompt,
            "character_lock": lock,
            "is_minor": False,
        }

    def step1_analyze(self, sampled_text: str) -> str:
        image_style = self._style("image_style", self.TOPIC_DEFAULT_IMAGE_STYLE)
        palette = self._style("palette", "warm soft pastels, white space, gentle contrast")
        negative_prompt = self._style("negative_prompt", "no readable text in images")
        audience_block = self._audience_insight_block(strict=True)
        return f"""Analyze this {self.TOPIC_DOMAIN} for visual production.

CONTENT (SAMPLED):
{sampled_text}

This channel uses one fixed visual style and one recurring reference character across the whole video.
CHANNEL IMAGE STYLE: {image_style}
CHANNEL PALETTE: {palette}
NEGATIVE RULES: {negative_prompt}
{audience_block}Visuals should explain {self.TOPIC_LABEL} ideas through relatable situations, symbolic objects, emotional contrast, and simple visual metaphors. Do not rely on written words inside images.

Return JSON only:
{{
    "setting": {{
        "era": "modern day",
        "location": "everyday life environments and clean conceptual spaces",
        "atmosphere": "warm, reflective, educational"
    }},
    "themes": {self.TOPIC_THEMES_DEFAULT},
    "visual_style": {{
        "cinematography": "{image_style}",
        "color_palette": "{palette}",
        "lighting": "lighting/rendering matching the channel style"
    }},
    "context_lock": "{image_style}, one recurring character nv1, anonymous silhouette/background people only for crowds, audience-cultural context is optional background flavor only, SRT narration must decide settings and props, {negative_prompt}"
}}
"""

    def segment_prompt(self, srt_content: str, entry_start: int, entry_end: int,
                       total_entries: int, total_duration: float, context_lock: str,
                       themes: list, is_part: bool = False, part_label: str = "") -> str:
        part_note = f"\nNOTE: This is {part_label}. Create segments ONLY for SRT [{entry_start}] to [{entry_end}]." if is_part else ""
        image_style = self._style("image_style", context_lock or f"channel {self.TOPIC_LABEL} illustration style")
        palette = self._style("palette", "")
        negative_prompt = self._style("negative_prompt", "no readable text")
        audience_line = self._audience_insight_block(strict=True)
        default_themes = ", ".join(self.TOPIC_THEMES_DEFAULT)
        return f"""Analyze this {self.TOPIC_DOMAIN} and divide it into educational visual segments.

CONTENT CONTEXT:
{context_lock}
CHANNEL IMAGE STYLE: {image_style}
CHANNEL PALETTE: {palette}
NEGATIVE RULES: {negative_prompt}
{audience_line}

THEMES: {', '.join(themes) if themes else default_themes}
TOTAL DURATION: {total_duration:.1f} seconds
TOTAL SRT ENTRIES: {total_entries}
{part_note}

FULL SRT CONTENT (with index numbers):
{srt_content}

CRITICAL REQUIREMENT:
- Your segments MUST cover ALL SRT entries from [{entry_start}] to [{entry_end}]
- First segment starts at srt_range_start: {entry_start}
- Last segment MUST end at srt_range_end: {entry_end}
- NO gaps between segments
- Use [index] numbers accurately

{self.TOPIC_VISUAL_RULES_LABEL} VISUAL RULES:
- Use only the recurring main character nv1 for reference character scenes
- Other people are anonymous silhouettes or simple background figures, not separate character IDs
- Prefer visual metaphors, contrast panels, symbolic objects, and emotional body language that fit the channel style
- The SRT narration decides the visual. Use audience-familiar props/settings only when they directly clarify the current SRT idea. Do not force cultural objects into every segment.
- No labels, captions, words, charts with readable text, documents, or screens unless explicitly in SRT

Return JSON only:
{{
    "segments": [
        {{
            "segment_id": 1,
            "segment_name": "Concept Introduction",
            "message": "{self.TOPIC_SEGMENT_EXAMPLE_MESSAGE}",
            "key_elements": {self.TOPIC_SEGMENT_EXAMPLE_ELEMENTS},
            "visual_summary": "{self.TOPIC_SEGMENT_EXAMPLE_SUMMARY}",
            "mood": "reflective/anxious/hopeful/empowering",
            "characters_involved": ["nv1"],
            "dramatic_question": "{self.TOPIC_SEGMENT_EXAMPLE_QUESTION}",
            "emotional_shift": "from confusion to insight",
            "visual_arc": "begin with relatable problem, show symbolic pressure, end with clearer emotional understanding",
            "continuity_markers": ["nv1", "channel style", "channel palette"],
            "forbidden_inventions": ["no readable text", "no extra character reference IDs", "no documents unless stated in SRT"],
            "image_count": 3,
            "estimated_duration": 15.0,
            "srt_range_start": {entry_start},
            "srt_range_end": 10,
            "importance": "high/medium/low"
        }}
    ],
    "total_images": 20,
    "summary": "Brief overview of the educational content structure"
}}
"""


class PsychologyPrompts(_StyledTopicPrompts):
    TOPIC_NAME = "psychology"
    TOPIC_LABEL = "psychology"
    TOPIC_DOMAIN = "psychology / self-improvement educational content"
    TOPIC_THEMES_DEFAULT = ["psychological concept", "human behavior", "self-improvement lesson"]
    TOPIC_VISUAL_RULES_LABEL = "PSYCHOLOGY"
    TOPIC_SEGMENT_EXAMPLE_MESSAGE = "DETAILED explanation of the psychological idea or emotional situation"
    TOPIC_SEGMENT_EXAMPLE_ELEMENTS = ["visual metaphor", "nv1 emotional pose", "symbolic object", "anonymous silhouettes"]
    TOPIC_SEGMENT_EXAMPLE_SUMMARY = "2-3 sentences describing psychology visuals in the channel style for this segment."
    TOPIC_SEGMENT_EXAMPLE_QUESTION = "What internal conflict or realization is being explored?"
    TOPIC_DEFAULT_LOCK = (
        "cute minimalist psychology channel character with round white head, simple dot eyes, "
        "calm thoughtful expression, small green sprout on top, soft blue shirt, beige pants, "
        "white sneakers, clean black outline illustration style"
    )
    TOPIC_DEFAULT_PORTRAIT = (
        "Cute minimalist psychology channel character, round white head, simple dot eyes, "
        "calm thoughtful expression, small green sprout on top, soft blue shirt, beige pants, "
        "white sneakers, standing on pure white background, clean black outline illustration style, "
        "paper texture, no text, no watermark"
    )
    TOPIC_DEFAULT_IMAGE_STYLE = "Clean minimalist psychology illustration style, paper texture background, warm educational YouTube aesthetic"
    TOPIC_METAPHOR_LABEL = "psychology metaphors"


class FinancePrompts(_StyledTopicPrompts):
    TOPIC_NAME = "finance"
    TOPIC_LABEL = "finance"
    TOPIC_DOMAIN = "personal finance / financial literacy educational content"
    TOPIC_THEMES_DEFAULT = ["financial concept", "money management", "financial literacy lesson"]
    TOPIC_VISUAL_RULES_LABEL = "FINANCE"
    TOPIC_SEGMENT_EXAMPLE_MESSAGE = "DETAILED explanation of the financial concept: what money decision is being discussed, why it matters, what the viewer should understand"
    TOPIC_SEGMENT_EXAMPLE_ELEMENTS = ["financial visual metaphor", "nv1 with money/chart/investment prop", "before/after financial contrast", "anonymous silhouettes"]
    TOPIC_SEGMENT_EXAMPLE_SUMMARY = "2-3 sentences describing how to visually illustrate this financial concept through the character's situation."
    TOPIC_SEGMENT_EXAMPLE_QUESTION = "What financial decision, risk, or opportunity is being explored?"
    TOPIC_DEFAULT_LOCK = (
        "cute minimalist finance channel character with round white head, simple dot eyes, "
        "calm confident expression, small coin emblem on top, soft navy shirt, beige pants, "
        "white sneakers, clean black outline illustration style"
    )
    TOPIC_DEFAULT_PORTRAIT = (
        "Cute minimalist finance channel character, round white head, simple dot eyes, "
        "calm confident expression, small coin emblem on top, soft navy shirt, beige pants, "
        "white sneakers, standing on pure white background, clean black outline illustration style, "
        "paper texture, no text, no watermark"
    )
    TOPIC_DEFAULT_IMAGE_STYLE = "Clean minimalist finance illustration style, paper texture background, warm educational YouTube aesthetic"
    TOPIC_METAPHOR_LABEL = "finance metaphors"

    def step1_analyze(self, sampled_text: str) -> str:
        image_style = self._style("image_style", self.TOPIC_DEFAULT_IMAGE_STYLE)
        palette = self._style("palette", "warm soft pastels, white space, gentle contrast")
        negative_prompt = self._style("negative_prompt", "no readable text in images")
        audience_block = self._audience_insight_block(strict=True)
        return f"""Analyze this personal finance / financial literacy content for visual production.

CONTENT (SAMPLED):
{sampled_text}

This channel uses one fixed visual style and one recurring reference character across the whole video.
CHANNEL IMAGE STYLE: {image_style}
CHANNEL PALETTE: {palette}
NEGATIVE RULES: {negative_prompt}
{audience_block}
FINANCE VISUAL APPROACH:
- Show financial concepts through concrete everyday money situations: saving, spending, investing, budgeting, debt management
- Use financial visual metaphors: growing plants from coins (compound interest), stacking blocks (wealth building), leaking bucket (wasteful spending), shield/umbrella (financial protection), ladder/stairs (financial goals), chains (debt), open door (opportunity)
- Props: coins, piggy banks, wallets, simple charts showing growth/decline, houses, cars, shopping bags, bills, phones with banking apps
- Settings: home offices, kitchen tables with bills, banks, shopping areas, workplaces
- Show the character in relatable money situations that make the viewer think "that's me"
- Do NOT use abstract or therapy-like imagery. Finance is practical, concrete, and action-oriented.
- Do not rely on written words inside images.

Return JSON only:
{{
    "setting": {{
        "era": "modern day",
        "location": "everyday financial life environments: homes, offices, banks, shopping areas",
        "atmosphere": "warm, practical, empowering"
    }},
    "themes": ["financial concept", "money management", "financial literacy lesson"],
    "visual_style": {{
        "cinematography": "{image_style}",
        "color_palette": "{palette}",
        "lighting": "lighting/rendering matching the channel style"
    }},
    "context_lock": "{image_style}, one recurring character nv1, anonymous silhouette/background people only for crowds, show financial concepts through concrete money situations and visual metaphors, {negative_prompt}"
}}
"""

    def segment_prompt(self, srt_content: str, entry_start: int, entry_end: int,
                       total_entries: int, total_duration: float, context_lock: str,
                       themes: list, is_part: bool = False, part_label: str = "") -> str:
        part_note = f"\nNOTE: This is {part_label}. Create segments ONLY for SRT [{entry_start}] to [{entry_end}]." if is_part else ""
        image_style = self._style("image_style", context_lock or "channel finance illustration style")
        palette = self._style("palette", "")
        negative_prompt = self._style("negative_prompt", "no readable text")
        audience_line = self._audience_insight_block(strict=True)
        return f"""Analyze this personal finance / financial literacy content and divide it into visual segments.

CONTENT CONTEXT:
{context_lock}
CHANNEL IMAGE STYLE: {image_style}
CHANNEL PALETTE: {palette}
NEGATIVE RULES: {negative_prompt}
{audience_line}

THEMES: {', '.join(themes) if themes else 'personal finance, money management, financial literacy'}
TOTAL DURATION: {total_duration:.1f} seconds
TOTAL SRT ENTRIES: {total_entries}
{part_note}

FULL SRT CONTENT (with index numbers):
{srt_content}

CRITICAL REQUIREMENT:
- Your segments MUST cover ALL SRT entries from [{entry_start}] to [{entry_end}]
- First segment starts at srt_range_start: {entry_start}
- Last segment MUST end at srt_range_end: {entry_end}
- NO gaps between segments
- Use [index] numbers accurately

FINANCE VISUAL RULES:
- Use only the recurring main character nv1 for reference character scenes
- Other people are anonymous silhouettes or simple background figures, not separate character IDs
- Show financial concepts through concrete visual metaphors: growing coin plants, stacking savings blocks, piggy banks filling up, investment ladders, debt chains breaking, budget pie charts (no readable text on them)
- Props must be finance-related: coins, bills, wallets, simple charts, houses, cars, phones with banking apps, shopping bags, piggy banks
- Settings must be everyday financial situations: home desk with bills, bank interior, shopping area, workplace, kitchen table
- The SRT narration decides the visual. Use audience-familiar financial settings only when they directly clarify the current SRT idea.
- No readable words, labels, captions, UI text, chart text, document text, signs, numbers, logos, or watermarks
- Finance visuals should feel practical and actionable, NOT abstract or emotional like psychology

Return JSON only:
{{
    "segments": [
        {{
            "segment_id": 1,
            "segment_name": "Financial Concept Introduction",
            "message": "DETAILED explanation of the financial concept: what money decision is discussed, why it matters",
            "key_elements": ["financial visual metaphor", "nv1 with money prop", "before/after financial contrast", "anonymous silhouettes"],
            "visual_summary": "2-3 sentences describing how to visually show this financial concept through the character.",
            "mood": "practical/concerned/hopeful/empowering",
            "characters_involved": ["nv1"],
            "dramatic_question": "What financial decision, risk, or opportunity is being explored?",
            "emotional_shift": "from financial confusion to understanding",
            "visual_arc": "begin with relatable money problem, show the financial concept visually, end with clearer financial understanding",
            "continuity_markers": ["nv1", "channel style", "financial props"],
            "forbidden_inventions": ["no readable text", "no extra character reference IDs", "no documents unless stated in SRT"],
            "image_count": 3,
            "estimated_duration": 15.0,
            "srt_range_start": {entry_start},
            "srt_range_end": 10,
            "importance": "high/medium/low"
        }}
    ],
    "total_images": 20,
    "summary": "Brief overview of the financial education content structure"
}}
"""


class SuccessPrompts(_StyledTopicPrompts):
    TOPIC_NAME = "success"
    TOPIC_LABEL = "self-development"
    TOPIC_DOMAIN = "self-development / personal success educational content"
    TOPIC_THEMES_DEFAULT = ["success habit", "personal growth", "self-improvement lesson"]
    TOPIC_VISUAL_RULES_LABEL = "SUCCESS"
    TOPIC_SEGMENT_EXAMPLE_MESSAGE = "DETAILED explanation of the success habit, growth concept, or personal breakthrough"
    TOPIC_SEGMENT_EXAMPLE_ELEMENTS = ["visual metaphor", "nv1 motivated pose", "symbolic growth object", "anonymous silhouettes"]
    TOPIC_SEGMENT_EXAMPLE_SUMMARY = "2-3 sentences describing self-development visuals in the channel style for this segment."
    TOPIC_SEGMENT_EXAMPLE_QUESTION = "What habit, goal, or personal breakthrough is being explored?"
    TOPIC_DEFAULT_LOCK = (
        "cute minimalist self-development channel character with round white head, simple dot eyes, "
        "motivated confident expression, short spiky dark hair, soft terracotta sweater, khaki pants, "
        "white sneakers, clean black outline illustration style"
    )
    TOPIC_DEFAULT_PORTRAIT = (
        "Cute minimalist self-development channel character, round white head, simple dot eyes, "
        "motivated confident expression, short spiky dark hair, soft terracotta sweater, khaki pants, "
        "white sneakers, standing on pure white background, clean black outline illustration style, "
        "paper texture, no text, no watermark"
    )
    TOPIC_DEFAULT_IMAGE_STYLE = "Clean minimalist self-development illustration style, paper texture background, warm motivational YouTube aesthetic"
    TOPIC_METAPHOR_LABEL = "success metaphors"

    def step1_analyze(self, sampled_text: str) -> str:
        image_style = self._style("image_style", self.TOPIC_DEFAULT_IMAGE_STYLE)
        palette = self._style("palette", "warm soft pastels, white space, gentle contrast")
        negative_prompt = self._style("negative_prompt", "no readable text in images")
        audience_block = self._audience_insight_block(strict=True)
        return f"""Analyze this self-development / personal success content for visual production.

CONTENT (SAMPLED):
{sampled_text}

This channel uses one fixed visual style and one recurring reference character across the whole video.
CHANNEL IMAGE STYLE: {image_style}
CHANNEL PALETTE: {palette}
NEGATIVE RULES: {negative_prompt}
{audience_block}
SELF-DEVELOPMENT VISUAL APPROACH:
- Show personal growth concepts through concrete daily life situations: building habits, setting goals, overcoming procrastination, morning routines, discipline, time management
- Use growth and motivation visual metaphors: climbing stairs step by step (progress), planting seeds that grow (long-term habits), building blocks stacking (skill building), opening doors (new opportunities), sunrise/dawn (fresh starts), path splitting (choices), weight lifting off shoulders (relief from bad habits)
- Props: alarm clocks, notebooks/planners, running shoes, books, small plants growing, water bottles, to-do lists (no readable text), calendars, dumbbells, mirrors
- Settings: bedrooms at dawn, study desks, parks for morning walks, gyms, kitchen tables, home workspaces
- Show before/after contrasts: lazy couch vs active morning, cluttered desk vs organized workspace, heavy clouds vs clear sky
- The character should feel relatable and human — show struggles, small wins, and gradual progress, NOT perfection
- Do NOT use therapy/clinical imagery or abstract emotional concepts. Self-development is practical, action-based, and forward-looking.
- Do not rely on written words inside images.

Return JSON only:
{{
    "setting": {{
        "era": "modern day",
        "location": "everyday self-improvement environments: bedrooms, desks, parks, gyms, kitchens",
        "atmosphere": "warm, motivating, encouraging"
    }},
    "themes": ["success habit", "personal growth", "self-improvement lesson"],
    "visual_style": {{
        "cinematography": "{image_style}",
        "color_palette": "{palette}",
        "lighting": "lighting/rendering matching the channel style"
    }},
    "context_lock": "{image_style}, one recurring character nv1, anonymous silhouette/background people only for crowds, show self-development concepts through concrete daily situations and growth metaphors, {negative_prompt}"
}}
"""

    def segment_prompt(self, srt_content: str, entry_start: int, entry_end: int,
                       total_entries: int, total_duration: float, context_lock: str,
                       themes: list, is_part: bool = False, part_label: str = "") -> str:
        part_note = f"\nNOTE: This is {part_label}. Create segments ONLY for SRT [{entry_start}] to [{entry_end}]." if is_part else ""
        image_style = self._style("image_style", context_lock or "channel self-development illustration style")
        palette = self._style("palette", "")
        negative_prompt = self._style("negative_prompt", "no readable text")
        audience_line = self._audience_insight_block(strict=True)
        return f"""Analyze this self-development / personal success content and divide it into visual segments.

CONTENT CONTEXT:
{context_lock}
CHANNEL IMAGE STYLE: {image_style}
CHANNEL PALETTE: {palette}
NEGATIVE RULES: {negative_prompt}
{audience_line}

THEMES: {', '.join(themes) if themes else 'success habits, personal growth, self-improvement'}
TOTAL DURATION: {total_duration:.1f} seconds
TOTAL SRT ENTRIES: {total_entries}
{part_note}

FULL SRT CONTENT (with index numbers):
{srt_content}

CRITICAL REQUIREMENT:
- Your segments MUST cover ALL SRT entries from [{entry_start}] to [{entry_end}]
- First segment starts at srt_range_start: {entry_start}
- Last segment MUST end at srt_range_end: {entry_end}
- NO gaps between segments
- Use [index] numbers accurately

SELF-DEVELOPMENT VISUAL RULES:
- Use only the recurring main character nv1 for reference character scenes
- Other people are anonymous silhouettes or simple background figures, not separate character IDs
- Show growth through concrete visual metaphors: climbing stairs (progress), planting seeds (habits), building blocks (skills), sunrise (fresh start), path splitting (choices), opening doors (opportunity), weight lifting off shoulders (overcoming)
- Props must relate to self-improvement: alarm clocks, notebooks, running shoes, books, small growing plants, water bottles, calendars, dumbbells, mirrors
- Settings must be everyday improvement situations: bedroom at dawn, study desk, park walk, gym, kitchen, home workspace
- Show before/after contrasts: the struggle THEN the small win — not just the result
- The SRT narration decides the visual. Use audience-familiar growth settings only when they directly clarify the current SRT idea.
- No readable words, labels, captions, UI text, chart text, document text, signs, numbers, logos, or watermarks
- Self-development visuals should feel motivating and practical, NOT clinical or abstract like psychology

Return JSON only:
{{
    "segments": [
        {{
            "segment_id": 1,
            "segment_name": "Growth Concept Introduction",
            "message": "DETAILED explanation of the self-development concept: what habit or mindset is discussed, what the viewer should change",
            "key_elements": ["growth visual metaphor", "nv1 in daily improvement situation", "before/after contrast", "motivational prop"],
            "visual_summary": "2-3 sentences describing how to visually show this growth concept through the character's daily situation.",
            "mood": "determined/struggling/hopeful/empowering",
            "characters_involved": ["nv1"],
            "dramatic_question": "What habit, goal, or personal breakthrough is being explored?",
            "emotional_shift": "from procrastination/confusion to motivation/clarity",
            "visual_arc": "begin with relatable struggle, show the growth effort, end with small visible progress",
            "continuity_markers": ["nv1", "channel style", "growth props"],
            "forbidden_inventions": ["no readable text", "no extra character reference IDs", "no documents unless stated in SRT"],
            "image_count": 3,
            "estimated_duration": 15.0,
            "srt_range_start": {entry_start},
            "srt_range_end": 10,
            "importance": "high/medium/low"
        }}
    ],
    "total_images": 20,
    "summary": "Brief overview of the self-development content structure"
}}
"""


def is_styled_topic(topic: str) -> bool:
    key = normalize_topic_key(topic)
    cls = TOPIC_MAPPING.get(key)
    return cls is not None and issubclass(cls, _StyledTopicPrompts)


TOPIC_MAPPING = {
    "story": StoryPrompts,
    "truyen": StoryPrompts,
    "psychology": PsychologyPrompts,
    "tam ly": PsychologyPrompts,
    "finance": FinancePrompts,
    "tai chinh": FinancePrompts,
    "success": SuccessPrompts,
    "phat trien ban than": SuccessPrompts,
}


def get_topic_prompts(topic: str = "story"):
    key = normalize_topic_key(topic)
    return TOPIC_MAPPING.get(key, StoryPrompts)()
