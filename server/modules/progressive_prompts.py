"""
VE3 Tool - Progressive Prompts Generator
=========================================
Tạo prompts theo từng step, mỗi step lưu vào Excel ngay.
API có thể đọc context từ Excel để học từ những gì đã làm.

Flow (Top-Down Planning):
    Step 1:   Phân tích story → Excel (story_analysis)
    Step 1.5: Phân tích nội dung con → Excel (story_segments)
              - Chia câu chuyện thành các phần
              - Mỗi phần cần bao nhiêu ảnh để truyền tải
    Step 2:   Tạo characters → Excel (characters)
    Step 3:   Tạo locations → Excel (characters với loc_xxx)
    Step 4:   Tạo director_plan → Excel (director_plan)
              - Dựa vào segments để phân bổ scenes
    Step 4.5: Lên kế hoạch chi tiết từng scene → Excel (scene_planning)
              - Ý đồ nghệ thuật cho mỗi scene
              - Góc máy, cảm xúc, ánh sáng
    Step 5:   Tạo scene prompts → Excel (scenes)
              - Đọc planning để viết prompt chính xác

Lợi ích:
    - Fail recovery: Resume từ step bị fail
    - Debug: Xem Excel biết step nào sai
    - Kiểm soát: Có thể sửa Excel giữa chừng
    - Chất lượng: API đọc context từ Excel
    - Top-down: Lên kế hoạch trước, prompt sau
"""

import sys
import os

# Fix Windows encoding issues
if sys.platform == "win32":
    if sys.stdout and hasattr(sys.stdout, 'reconfigure'):
        try:
            sys.stdout.reconfigure(encoding='utf-8', errors='replace')
        except:
            pass
    if sys.stderr and hasattr(sys.stderr, 'reconfigure'):
        try:
            sys.stderr.reconfigure(encoding='utf-8', errors='replace')
        except:
            pass


import json
import time
from pathlib import Path
from typing import Dict, Any, List, Optional, Tuple, Callable
from dataclasses import dataclass
from enum import Enum
from concurrent.futures import ThreadPoolExecutor, as_completed

from modules.utils import (
    get_logger,
    parse_srt_file,
)
from modules.excel_manager import (
    PromptWorkbook,
    Character,
    Location,
    Scene
)


class StepStatus(Enum):
    """Trạng thái của mỗi step."""
    NOT_STARTED = "not_started"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    FAILED = "failed"


@dataclass
class StepResult:
    """Kết quả của mỗi step."""
    step_name: str
    status: StepStatus
    message: str = ""
    data: Any = None


def parse_srt_timestamp(ts: str) -> float:
    """
    Parse SRT timestamp to seconds.
    Format: "HH:MM:SS,mmm" or "HH:MM:SS" or number (already seconds)

    v1.0.48: Helper để tính planned_duration
    """
    if ts is None:
        return 0.0

    # Already a number
    if isinstance(ts, (int, float)):
        return float(ts)

    ts_str = str(ts).strip()
    if not ts_str:
        return 0.0

    try:
        # Handle timedelta string format "0:01:23"
        if ts_str.count(':') == 2 and ',' not in ts_str and '.' not in ts_str:
            parts = ts_str.split(':')
            h, m, s = int(parts[0]), int(parts[1]), float(parts[2])
            return h * 3600 + m * 60 + s

        # Handle SRT format "00:01:23,456"
        if ',' in ts_str:
            time_part, ms_part = ts_str.split(',')
            h, m, s = map(int, time_part.split(':'))
            ms = int(ms_part)
            return h * 3600 + m * 60 + s + ms / 1000

        # Handle "00:01:23.456"
        if '.' in ts_str and ts_str.count(':') == 2:
            parts = ts_str.split(':')
            h, m = int(parts[0]), int(parts[1])
            s = float(parts[2])
            return h * 3600 + m * 60 + s

        # Try parsing as float directly
        return float(ts_str)
    except:
        return 0.0


def calc_planned_duration(srt_start, srt_end) -> float:
    """
    Tính planned_duration (giây) từ srt_start và srt_end.

    v1.0.48: Simple calculation for video editing
    """
    start_sec = parse_srt_timestamp(srt_start)
    end_sec = parse_srt_timestamp(srt_end)

    if end_sec > start_sec:
        return round(end_sec - start_sec, 2)
    return 0.0


class ProgressivePromptsGenerator:
    """
    Generator tạo prompts theo từng step.
    Mỗi step đọc context từ Excel và lưu kết quả vào Excel.
    """

    DEEPSEEK_URL = "https://api.deepseek.com/v1/chat/completions"

    def __init__(self, config: dict):
        """
        Args:
            config: Config chứa API keys và settings
        """
        self.config = config
        self.topic = config.get("topic", "story")  # story | psychology
        self.logger = get_logger("progressive_prompts")

        # Topic-specific prompts
        try:
            from modules.topic_prompts import get_topic_prompts
            self.topic_prompts = get_topic_prompts(self.topic)
        except ImportError:
            try:
                # Fallback: direct import
                import importlib
                topic_pkg = importlib.import_module("modules.topic_prompts")
                self.topic_prompts = topic_pkg.get_topic_prompts(self.topic)
            except ImportError:
                # Last resort: add parent to path and retry
                _parent = str(Path(__file__).parent.parent)
                if _parent not in sys.path:
                    sys.path.insert(0, _parent)
                from modules.topic_prompts import get_topic_prompts
                self.topic_prompts = get_topic_prompts(self.topic)

        # Character template override (tu Google Sheet col L sheet THONG TIN)
        self.character_template = config.get("character_template", "")

        # API keys
        self.deepseek_keys = [k for k in config.get("deepseek_api_keys", []) if k and k.strip()]
        self.deepseek_index = 0

        # Callback for logging
        self.log_callback: Optional[Callable] = None

        # Test API key
        if self.deepseek_keys:
            self._test_api_keys()

    def _test_api_keys(self):
        """Test API keys và loại bỏ keys không hoạt động."""
        import requests

        working_keys = []
        for i, key in enumerate(self.deepseek_keys):
            try:
                headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
                data = {
                    "model": "deepseek-chat",
                    "messages": [{"role": "user", "content": "Say OK"}],
                    "max_tokens": 5
                }
                resp = requests.post(self.DEEPSEEK_URL, headers=headers, json=data, timeout=15)
                if resp.status_code == 200:
                    working_keys.append(key)
                    self._log(f"  DeepSeek key #{i+1}: OK")
                else:
                    self._log(f"  DeepSeek key #{i+1}: SKIP (status {resp.status_code})")
            except Exception as e:
                self._log(f"  DeepSeek key #{i+1}: SKIP ({e})")

        self.deepseek_keys = working_keys
        if not working_keys:
            self._log("  WARNING: No working API keys!")

    def _log(self, msg: str, level: str = "INFO"):
        """Log message."""
        if self.log_callback:
            self.log_callback(msg, level)
        else:
            print(msg)

    def _call_api(self, prompt: str, temperature: float = 0.7, max_tokens: int = 8192) -> Optional[str]:
        """
        Gọi DeepSeek API với retry logic để tránh mid-process failures.

        Returns:
            Response text hoặc None nếu fail sau tất cả retries
        """
        import requests
        import time

        if not self.deepseek_keys:
            self._log("  ERROR: No API keys available!", "ERROR")
            return None

        max_retries = 15  # Increased for multiple machines sharing API
        base_delay = 3  # seconds

        for attempt in range(max_retries):
            key = self.deepseek_keys[self.deepseek_index % len(self.deepseek_keys)]
            self.deepseek_index += 1

            headers = {
                "Authorization": f"Bearer {key}",
                "Content-Type": "application/json"
            }

            data = {
                "model": "deepseek-chat",
                "messages": [{"role": "user", "content": prompt}],
                "temperature": temperature,
                "max_tokens": max_tokens
            }

            try:
                resp = requests.post(self.DEEPSEEK_URL, headers=headers, json=data, timeout=120)

                if resp.status_code == 200:
                    # Success!
                    if attempt > 0:
                        self._log(f"  API success after {attempt + 1} attempts", "INFO")
                    return resp.json()["choices"][0]["message"]["content"]

                elif resp.status_code == 429:
                    # Rate limit - retry with exponential backoff
                    delay = base_delay * (2 ** attempt)
                    self._log(f"  Rate limit hit (429), retry {attempt + 1}/{max_retries} after {delay}s", "WARN")
                    if attempt < max_retries - 1:
                        time.sleep(delay)
                        continue
                    else:
                        self._log(f"  API error after {max_retries} retries: {resp.status_code}", "ERROR")
                        return None

                elif resp.status_code >= 500:
                    # Server error - retry with exponential backoff
                    delay = base_delay * (2 ** attempt)
                    self._log(f"  Server error ({resp.status_code}), retry {attempt + 1}/{max_retries} after {delay}s", "WARN")
                    if attempt < max_retries - 1:
                        time.sleep(delay)
                        continue
                    else:
                        self._log(f"  API error after {max_retries} retries: {resp.status_code}", "ERROR")
                        return None

                else:
                    # Client error (4xx except 429) - don't retry
                    self._log(f"  API error: {resp.status_code} - {resp.text[:200]}", "ERROR")
                    return None

            except requests.exceptions.Timeout:
                delay = base_delay * (2 ** attempt)
                self._log(f"  Timeout, retry {attempt + 1}/{max_retries} after {delay}s", "WARN")
                if attempt < max_retries - 1:
                    time.sleep(delay)
                    continue
                else:
                    self._log(f"  Timeout after {max_retries} retries", "ERROR")
                    return None

            except Exception as e:
                delay = base_delay * (2 ** attempt)
                self._log(f"  API exception: {e}, retry {attempt + 1}/{max_retries} after {delay}s", "WARN")
                if attempt < max_retries - 1:
                    time.sleep(delay)
                    continue
                else:
                    self._log(f"  API exception after {max_retries} retries: {e}", "ERROR")
                    return None

        return None

    def _extract_json(self, text: str) -> Optional[dict]:
        """Extract JSON từ response text - với repair cho truncated JSON."""
        import re

        if not text:
            return None

        # Loại bỏ <think>...</think> tags (DeepSeek)
        text = re.sub(r'<think>.*?</think>', '', text, flags=re.DOTALL)

        # Thử parse trực tiếp
        try:
            return json.loads(text.strip())
        except:
            pass

        # Tìm JSON trong code block
        match = re.search(r'```(?:json)?\s*([\s\S]*?)\s*```', text)
        if match:
            try:
                return json.loads(match.group(1))
            except:
                # Thử repair
                repaired = self._repair_truncated_json(match.group(1))
                if repaired:
                    try:
                        return json.loads(repaired)
                    except:
                        pass

        # Tìm JSON object
        match = re.search(r'\{[\s\S]*\}', text)
        if match:
            json_str = match.group(0)
            try:
                return json.loads(json_str)
            except:
                # Thử repair truncated JSON
                repaired = self._repair_truncated_json(json_str)
                if repaired:
                    try:
                        return json.loads(repaired)
                    except:
                        pass

        # Tìm JSON bắt đầu bằng { nhưng có thể bị cắt cuối
        start_idx = text.find('{')
        if start_idx != -1:
            json_str = text[start_idx:]
            repaired = self._repair_truncated_json(json_str)
            if repaired:
                try:
                    return json.loads(repaired)
                except:
                    pass

        return None

    def _repair_truncated_json(self, json_str: str) -> Optional[str]:
        """Repair JSON bị truncated (thiếu closing brackets)."""
        if not json_str:
            return None

        # Đếm brackets
        open_braces = json_str.count('{')
        close_braces = json_str.count('}')
        open_brackets = json_str.count('[')
        close_brackets = json_str.count(']')

        # Nếu balanced thì return nguyên
        if open_braces == close_braces and open_brackets == close_brackets:
            return json_str

        # Nếu có nhiều close hơn open -> JSON không valid
        if close_braces > open_braces or close_brackets > open_brackets:
            return None

        # Cắt bỏ phần dở dang cuối và thêm closing brackets
        # Tìm vị trí cuối cùng có thể là kết thúc hợp lệ
        for i in range(len(json_str) - 1, max(0, len(json_str) - 200), -1):
            char = json_str[i]
            if char in '}]"':
                test_str = json_str[:i+1]
                # Đếm lại
                ob = test_str.count('{')
                cb = test_str.count('}')
                oB = test_str.count('[')
                cB = test_str.count(']')
                # Thêm closing cần thiết
                suffix = ']' * max(0, oB - cB) + '}' * max(0, ob - cb)
                repaired = test_str + suffix
                try:
                    json.loads(repaired)
                    return repaired
                except:
                    continue

        # Fallback: Thêm closing brackets đơn giản
        suffix = ']' * max(0, open_brackets - close_brackets)
        suffix += '}' * max(0, open_braces - close_braces)
        return json_str + suffix

    def _sample_text(self, text: str, total_chars: int = 8000) -> str:
        """
        Lấy mẫu text thông minh: đầu + giữa + cuối.
        Thay vì gửi 15-20k chars, chỉ gửi ~8k nhưng bao phủ toàn bộ nội dung.

        Args:
            text: Full text
            total_chars: Tổng số ký tự muốn lấy (default 8000)

        Returns:
            Sampled text với markers [BEGINNING], [MIDDLE], [END]
        """
        if len(text) <= total_chars:
            return text

        # Chia tỷ lệ: 40% đầu, 30% giữa, 30% cuối
        begin_chars = int(total_chars * 0.4)
        middle_chars = int(total_chars * 0.3)
        end_chars = int(total_chars * 0.3)

        # Lấy phần đầu
        begin_text = text[:begin_chars]

        # Lấy phần giữa (từ khoảng 40% đến 60% của text)
        middle_start = len(text) // 2 - middle_chars // 2
        middle_text = text[middle_start:middle_start + middle_chars]

        # Lấy phần cuối
        end_text = text[-end_chars:]

        sampled = f"""[BEGINNING - First {begin_chars} chars]
{begin_text}

[MIDDLE - Around center of story]
{middle_text}

[END - Last {end_chars} chars]
{end_text}"""

        return sampled

    def _get_srt_for_range(self, srt_entries: list, start_idx: int, end_idx: int) -> str:
        """
        Lấy SRT text cho một range cụ thể.

        Args:
            srt_entries: List of SRT entries
            start_idx: 1-based start index
            end_idx: 1-based end index

        Returns:
            Formatted SRT text
        """
        srt_text = ""
        for i, entry in enumerate(srt_entries, 1):
            if start_idx <= i <= end_idx:
                srt_text += f"[{i}] {entry.start_time} --> {entry.end_time}\n{entry.text}\n\n"
        return srt_text

    def _normalize_character_ids(self, characters_used: str, valid_char_ids: set) -> str:
        """
        Normalize character IDs từ API response về format chuẩn (nv_xxx).

        Vấn đề: API có thể trả về "john, mary" thay vì "nv_john, nv_mary"
        Giải pháp: Map về IDs đã biết trong valid_char_ids

        Args:
            characters_used: String từ API như "john, mary" hoặc "nv_john"
            valid_char_ids: Set of valid IDs như {"nv_john", "nv_mary", "loc_office"}

        Returns:
            Normalized string như "nv_john, nv_mary"
        """
        if not characters_used or not valid_char_ids:
            return characters_used

        raw_ids = [x.strip() for x in characters_used.split(",") if x.strip()]
        normalized = []

        # Build lookup (lowercase -> original)
        id_lookup = {cid.lower(): cid for cid in valid_char_ids}
        # Also add versions without prefix
        for cid in list(valid_char_ids):
            if cid.startswith("nv_"):
                id_lookup[cid[3:].lower()] = cid  # "john" -> "nv_john"
            if cid.startswith("loc_"):
                id_lookup[cid[4:].lower()] = cid  # "office" -> "loc_office"

        for raw_id in raw_ids:
            raw_lower = raw_id.lower()

            # Tìm trong lookup
            if raw_lower in id_lookup:
                normalized.append(id_lookup[raw_lower])
            elif raw_id in valid_char_ids:
                normalized.append(raw_id)
            elif f"nv_{raw_id}" in valid_char_ids:
                normalized.append(f"nv_{raw_id}")
            else:
                # Không tìm thấy - giữ nguyên nhưng thêm nv_ prefix nếu chưa có
                if not raw_id.startswith("nv_") and not raw_id.startswith("loc_"):
                    normalized.append(f"nv_{raw_id}")
                else:
                    normalized.append(raw_id)

        return ", ".join(normalized)

    def _normalize_location_id(self, location_used: str, valid_loc_ids: set) -> str:
        """
        Normalize location ID từ API response về format chuẩn (loc_xxx).

        Args:
            location_used: String từ API như "office" hoặc "loc_office"
            valid_loc_ids: Set of valid location IDs

        Returns:
            Normalized ID như "loc_office"
        """
        if not location_used or not valid_loc_ids:
            return location_used

        raw_id = location_used.strip()
        raw_lower = raw_id.lower()

        # Build lookup
        id_lookup = {lid.lower(): lid for lid in valid_loc_ids}
        for lid in list(valid_loc_ids):
            if lid.startswith("loc_"):
                id_lookup[lid[4:].lower()] = lid  # "office" -> "loc_office"

        # Tìm trong lookup
        if raw_lower in id_lookup:
            return id_lookup[raw_lower]
        elif raw_id in valid_loc_ids:
            return raw_id
        elif f"loc_{raw_id}" in valid_loc_ids:
            return f"loc_{raw_id}"
        else:
            # Không tìm thấy - thêm loc_ prefix nếu chưa có
            if not raw_id.startswith("loc_"):
                return f"loc_{raw_id}"
            return raw_id

    def _split_long_scene_cinematically(
        self,
        scene: dict,
        char_locks: list,
        loc_locks: list
    ) -> list:
        """
        Chia một scene dài (> 8s) thành multiple shots một cách nghệ thuật.
        Gọi API để quyết định cách chia dựa trên nội dung, không phải công thức.

        Returns:
            List of split scenes, or None if failed
        """
        duration = scene.get("duration", 0)
        srt_text = scene.get("srt_text", "")
        visual_moment = scene.get("visual_moment", "")
        characters_used = scene.get("characters_used", "")
        location_used = scene.get("location_used", "")
        srt_start = scene.get("srt_start", "")
        srt_end = scene.get("srt_end", "")

        # Tính số shots cần thiết (target 5-7s mỗi shot)
        min_shots = max(2, int(duration / 7))
        max_shots = max(2, int(duration / 4))

        # v1.0.433: dung topic_prompts
        prompt = self.topic_prompts.split_scene_prompt(
            duration, min_shots, max_shots, srt_start, srt_end,
            srt_text, visual_moment, characters_used, location_used,
            char_locks, loc_locks
        )

        response = self._call_api(prompt, temperature=0.5, max_tokens=2000)
        if not response:
            return None

        data = self._extract_json(response)
        if not data or "shots" not in data:
            return None

        shots = data["shots"]
        if not shots or len(shots) < 2:
            return None

        # Validate total duration roughly matches original
        total_split_duration = sum(s.get("duration", 0) for s in shots)
        if abs(total_split_duration - duration) > duration * 0.3:  # Allow 30% variance
            # Adjust durations proportionally
            ratio = duration / total_split_duration if total_split_duration > 0 else 1
            for shot in shots:
                shot["duration"] = round(shot.get("duration", 5) * ratio, 2)

        # Convert shots to scene format
        split_scenes = []
        for shot in shots:
            split_scene = {
                "scene_id": 0,  # Will be assigned later
                "srt_indices": scene.get("srt_indices", []),
                "srt_start": srt_start,  # Keep original timing reference
                "srt_end": srt_end,
                "duration": shot.get("duration", 5.0),
                "srt_text": shot.get("srt_text", srt_text),
                "visual_moment": shot.get("visual_moment", ""),
                "shot_purpose": shot.get("shot_purpose", ""),
                "characters_used": shot.get("characters_used", characters_used),
                "location_used": shot.get("location_used", location_used),
                "camera": shot.get("camera", ""),
                "lighting": scene.get("lighting", "")
            }
            split_scenes.append(split_scene)

        return split_scenes

    # =========================================================================
    # STEP 1: PHÂN TÍCH STORY
    # =========================================================================

    def step_analyze_story(
        self,
        project_dir: Path,
        code: str,
        workbook: PromptWorkbook,
        srt_entries: list,
        txt_content: str = ""
    ) -> StepResult:
        """
        Step 1: Phân tích story và lưu vào Excel.

        Output sheet: story_analysis
        - setting: Bối cảnh (thời đại, địa điểm)
        - themes: Chủ đề chính
        - visual_style: Phong cách visual
        - context_lock: Prompt context chung
        """
        import time
        step_start = time.time()

        self._log("\n" + "="*60)
        self._log("[STEP 1/7] Phân tích story...")
        self._log("="*60)

        # Check if already done
        try:
            existing = workbook.get_story_analysis()
            if existing and existing.get("setting"):
                self._log("  -> Đã có story_analysis, skip!")
                workbook.update_step_status("step_1", "COMPLETED", 1, 1, "Already done")
                return StepResult("analyze_story", StepStatus.COMPLETED, "Already done")
        except:
            pass

        # Prepare story text - OPTIMIZED: Use sampled text instead of full 15k
        if txt_content:
            story_text = txt_content
        else:
            story_text = " ".join([e.text for e in srt_entries])

        # Sample text: 8k chars thay vì 15k - tiết kiệm ~50% tokens
        sampled_text = self._sample_text(story_text, total_chars=8000)
        self._log(f"  Text: {len(story_text)} chars → sampled {len(sampled_text)} chars")

        # Build prompt - v1.0.431: dung topic_prompts
        prompt = self.topic_prompts.step1_analyze(sampled_text)

        # Call API
        response = self._call_api(prompt, temperature=0.5)
        if not response:
            self._log("  ERROR: API call failed!", "ERROR")
            return StepResult("analyze_story", StepStatus.FAILED, "API call failed")

        # Parse response
        data = self._extract_json(response)
        if not data:
            self._log("  ERROR: Could not parse JSON!", "ERROR")
            return StepResult("analyze_story", StepStatus.FAILED, "JSON parse failed")

        # Save to Excel
        try:
            workbook.save_story_analysis(data)
            workbook.save()
            self._log(f"  -> Saved story_analysis to Excel")
            self._log(f"     Setting: {data.get('setting', {}).get('era', 'N/A')}, {data.get('setting', {}).get('location', 'N/A')}")
            self._log(f"     Context: {data.get('context_lock', 'N/A')[:80]}...")

            # TRACKING: Cập nhật trạng thái với thời gian
            elapsed = int(time.time() - step_start)
            workbook.update_step_status("step_1", "COMPLETED", 1, 1,
                f"{elapsed}s - {data.get('context_lock', '')[:40]}...")

            return StepResult("analyze_story", StepStatus.COMPLETED, "Success", data)
        except Exception as e:
            self._log(f"  ERROR: Could not save to Excel: {e}", "ERROR")
            elapsed = int(time.time() - step_start)
            workbook.update_step_status("step_1", "ERROR", 0, 0, f"{elapsed}s - {str(e)[:80]}")
            return StepResult("analyze_story", StepStatus.FAILED, str(e))

    # =========================================================================
    # STEP 2: PHÂN TÍCH NỘI DUNG CON (STORY SEGMENTS)
    # =========================================================================

    def step_analyze_story_segments(
        self,
        project_dir: Path,
        code: str,
        workbook: PromptWorkbook,
        srt_entries: list,
        txt_content: str = ""
    ) -> StepResult:
        """
        Step 1.5: Phân tích câu chuyện thành các nội dung con (segments).

        Logic top-down:
        1. Xác định các phần nội dung chính trong câu chuyện
        2. Mỗi phần cần truyền tải thông điệp gì
        3. Mỗi phần cần bao nhiêu ảnh để thể hiện đầy đủ
        4. Ước tính thời gian từ SRT

        Output sheet: story_segments
        """
        import time
        step_start = time.time()

        self._log("\n" + "="*60)
        self._log("[STEP 2/7] Phân tích nội dung con (story segments)...")
        self._log("="*60)

        # Check if already done
        try:
            existing = workbook.get_story_segments()
            if existing and len(existing) > 0:
                self._log(f"  -> Đã có {len(existing)} segments, skip!")
                workbook.update_step_status("step_2", "COMPLETED", len(existing), len(existing), "Already done")
                return StepResult("analyze_story_segments", StepStatus.COMPLETED, "Already done")
        except:
            pass

        # TRACKING: Khởi tạo SRT coverage để đối chiếu
        self._log(f"  Khởi tạo SRT coverage tracking...")
        workbook.init_srt_coverage(srt_entries)

        # Read context from previous step
        story_analysis = {}
        try:
            story_analysis = workbook.get_story_analysis() or {}
        except:
            pass

        context_lock = story_analysis.get("context_lock", "")
        themes = story_analysis.get("themes", [])

        # Prepare story text - OPTIMIZED: Use sampled text
        if txt_content:
            story_text = txt_content
        else:
            story_text = " ".join([e.text for e in srt_entries])

        # Sample text: 10k chars để có đủ context cho segment analysis
        sampled_text = self._sample_text(story_text, total_chars=10000)
        self._log(f"  Text: {len(story_text)} chars → sampled {len(sampled_text)} chars")

        # Tính tổng thời gian từ SRT
        total_duration = 0
        if srt_entries:
            try:
                # Parse end time của entry cuối
                last_entry = srt_entries[-1]
                end_time = last_entry.end_time  # Format: "00:01:30,500"
                parts = end_time.replace(',', ':').split(':')
                total_duration = int(parts[0]) * 3600 + int(parts[1]) * 60 + int(parts[2]) + int(parts[3]) / 1000
            except:
                total_duration = len(srt_entries) * 3  # Ước tính 3s/entry

        self._log(f"  Tổng thời gian SRT: {total_duration:.1f}s ({len(srt_entries)} entries)")

        # Build prompt - v1.0.431: dung topic_prompts
        prompt = self.topic_prompts.step2_segments(
            context_lock, themes, total_duration, len(srt_entries), sampled_text
        )

        # PHASE 1: Call API for segment division only (no image_count)
        self._log(f"  [PHASE 1] Calling API for segment division...")
        response = self._call_api(prompt, temperature=0.3, max_tokens=4096)
        if not response:
            self._log("  ERROR: API call failed!", "ERROR")
            return StepResult("analyze_story_segments", StepStatus.FAILED, "API call failed")

        # Parse response
        data = self._extract_json(response)
        if not data or "segments" not in data:
            self._log("  ERROR: Could not parse segments from API!", "ERROR")
            self._log(f"  API Response (first 500 chars): {response[:500] if response else 'None'}", "DEBUG")

            # === FALLBACK: Tạo segments đơn giản dựa trên SRT ===
            self._log("  -> Creating FALLBACK segments based on SRT duration...")
            total_srt = len(srt_entries)
            # Parse end_time from last SRT entry
            try:
                last_entry = srt_entries[-1]
                parts = last_entry.end_time.replace(',', ':').split(':')
                total_duration = int(parts[0])*3600 + int(parts[1])*60 + int(parts[2]) + int(parts[3])/1000
            except:
                total_duration = len(srt_entries) * 3  # Fallback: 3s per entry

            # Tính số segments (~60s mỗi segment, ~12 ảnh)
            num_segments = max(1, int(total_duration / 60))
            entries_per_seg = max(1, total_srt // num_segments)
            images_per_seg = max(1, int(60 / 5))  # ~12 ảnh per 60s

            segments = []
            for i in range(num_segments):
                seg_start = i * entries_per_seg + 1
                seg_end = min((i + 1) * entries_per_seg, total_srt)
                if i == num_segments - 1:
                    seg_end = total_srt  # Last segment gets all remaining

                segments.append({
                    "segment_id": i + 1,
                    "segment_name": f"Part {i + 1}",
                    "message": f"Story segment {i + 1}",
                    "key_elements": [],
                    "image_count": images_per_seg,
                    "srt_range_start": seg_start,
                    "srt_range_end": seg_end
                })

            self._log(f"  -> Created {len(segments)} fallback segments")
            data = {"segments": segments}

        segments = data["segments"]
        total_srt = len(srt_entries)

        # =====================================================================
        # v1.0.319: CLAMP - Đảm bảo tất cả segment ranges nằm trong [1, total_srt]
        # Bug: API trả về ranges vượt quá tổng SRT (ví dụ: SRT 31-33 khi chỉ có 28 entries)
        # → Step 5 không tìm được SRT entries → "No SRT entries" → retry 5 lần vô ích
        # =====================================================================
        valid_segments = []
        for seg in segments:
            seg_start = seg.get("srt_range_start", 1)
            seg_end = seg.get("srt_range_end", total_srt)
            # Clamp to valid range
            seg["srt_range_start"] = max(1, min(seg_start, total_srt))
            seg["srt_range_end"] = max(1, min(seg_end, total_srt))
            # Bỏ segment nếu range vô nghĩa (start > total_srt)
            if seg["srt_range_start"] > total_srt:
                self._log(f"  [CLAMP] Bỏ segment '{seg.get('segment_name')}': SRT {seg_start}-{seg_end} vượt quá {total_srt} entries")
                continue
            if seg_start != seg["srt_range_start"] or seg_end != seg["srt_range_end"]:
                self._log(f"  [CLAMP] Segment '{seg.get('segment_name')}': SRT {seg_start}-{seg_end} → {seg['srt_range_start']}-{seg['srt_range_end']}")
            valid_segments.append(seg)
        segments = valid_segments
        data["segments"] = segments

        # =====================================================================
        # PHASE 2: Calculate image_count for EACH segment individually IN PARALLEL
        # ROOT CAUSE FIX: Single API call with all segments hits max_tokens limit
        # → API must compress → reduces image_count to fit response
        # SOLUTION: Call API separately for each segment to get accurate count
        # OPTIMIZATION: Call all APIs in parallel for speed
        # v1.0.72: Support "small" mode - Segment 1 detailed, Segments 2+ key moments only
        # =====================================================================
        excel_mode = self.config.get("excel_mode", "full").lower()
        self._log(f"\n  [PHASE 2] Calculating image_count for {len(segments)} segments (parallel)...")
        if excel_mode == "small":
            self._log(f"  [SMALL MODE] Segment 1: 5-8s/scene | Segments 2+: KEY MOMENTS only")

        def _calculate_image_count_for_segment(seg_with_idx):
            """Helper function to calculate image count for one segment"""
            idx, seg = seg_with_idx
            seg_start = seg.get("srt_range_start", 1)
            seg_end = seg.get("srt_range_end", total_srt)
            srt_count = seg_end - seg_start + 1

            # Get SRT entries for this segment
            seg_entries = srt_entries[seg_start-1:seg_end]
            seg_text = " ".join([e.text for e in seg_entries])
            seg_duration = srt_count * (total_duration / total_srt) if total_duration > 0 else srt_count * 3

            # v1.0.72: SMALL MODE - Different logic for Segment 1 vs Segments 2+
            is_small_mode = excel_mode == "small"
            is_segment_1 = (idx == 1)

            if is_small_mode and not is_segment_1:
                # SMALL MODE - Segments 2+: KEY MOMENTS only (20-30s per image)
                min_images = max(1, int(seg_duration / 30))  # 30s max per image
                max_images = max(2, int(seg_duration / 20))  # 20s min per image
                target_images = max(2, int((min_images + max_images) / 2))

                calc_prompt = f"""Identify KEY VISUAL MOMENTS for this story segment.

SEGMENT: "{seg.get('segment_name', f'Segment {idx}')}"
NARRATIVE: {seg.get('message', 'Not specified')}
MOOD: {seg.get('mood', 'Not specified')}

DURATION: ~{seg_duration:.1f} seconds
CONTENT SAMPLE: {seg_text[:300]}...

TASK: This is a SUMMARY segment. Identify only the MOST IMPORTANT visual moments.
Do NOT cover every detail - only key turning points, emotional peaks, and memorable images.

REQUIREMENTS:
- Minimum: {min_images} images
- Maximum: {max_images} images
- Target: {target_images} images (1 image per 20-30 seconds)

Return JSON only:
{{{{
    "image_count": {target_images},
    "key_moments": ["moment 1", "moment 2", ...]
}}}}"""
                mode_label = "SMALL-KEY"
            else:
                # NORMAL/FULL/SMALL-Segment1: Standard 5-8s per image
                min_images = max(1, int(seg_duration / 8))  # 8s max per image
                max_images = max(1, int(seg_duration / 5))  # 5s min per image
                target_images = int((min_images + max_images) / 2)

                calc_prompt = f"""Calculate the number of IMAGES needed for this story segment.

SEGMENT: "{seg.get('segment_name', f'Segment {idx}')}"
NARRATIVE: {seg.get('message', 'Not specified')}
MOOD: {seg.get('mood', 'Not specified')}

SRT RANGE: {seg_start} to {seg_end} ({srt_count} entries, ~{seg_duration:.1f}s)
CONTENT SAMPLE: {seg_text[:500]}...

TASK: Calculate how many images this segment needs for video creation.

CRITICAL REQUIREMENTS:
- Minimum: {min_images} images (8s per image max)
- Maximum: {max_images} images (5s per image min)
- Target: {target_images} images (balance)

CONSIDER:
- Emotional scenes: More images for impact
- Action/fast pacing: More images
- Dialogue-heavy: Fewer images but >= minimum
- One image typically covers 5-8 SRT entries

Return JSON only:
{{{{
    "image_count": {target_images},
    "reasoning": "Brief explanation (optional)"
}}}}"""
                mode_label = "SMALL-SEG1" if (is_small_mode and is_segment_1) else "NORMAL"

            calc_response = self._call_api(calc_prompt, temperature=0.2, max_tokens=500)

            if calc_response:
                calc_data = self._extract_json(calc_response)
                if calc_data and "image_count" in calc_data:
                    calculated_count = calc_data["image_count"]
                    # Clamp to range
                    final_count = max(min_images, min(calculated_count, max_images))
                    return (idx, final_count, srt_count, f"API-{mode_label}")
                else:
                    # Fallback: Use target
                    return (idx, target_images, srt_count, f"fallback-{mode_label}")
            else:
                # API failed, use target
                return (idx, target_images, srt_count, f"fallback-{mode_label}")

        # Execute in parallel with ThreadPoolExecutor
        max_workers = min(10, len(segments))  # Limit to 10 concurrent calls
        results = []

        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            # Submit all tasks
            futures = {
                executor.submit(_calculate_image_count_for_segment, (idx, seg)): idx
                for idx, seg in enumerate(segments, start=1)
            }

            # Collect results as they complete
            for future in as_completed(futures):
                try:
                    result = future.result()
                    results.append(result)
                    idx, count, srt_count, source = result
                    self._log(f"     Segment {idx}/{len(segments)}: {count} images ({srt_count} SRT) [{source}]")
                except Exception as e:
                    idx = futures[future]
                    self._log(f"     Segment {idx}/{len(segments)}: ERROR - {e}", "ERROR")
                    # Fallback for failed segment
                    seg = segments[idx-1]
                    seg_start = seg.get("srt_range_start", 1)
                    seg_end = seg.get("srt_range_end", total_srt)
                    srt_count = seg_end - seg_start + 1
                    fallback_count = max(1, srt_count // 4)
                    results.append((idx, fallback_count, srt_count, "error"))

        # Sort results by segment index and apply to segments
        results.sort(key=lambda x: x[0])
        for idx, count, srt_count, source in results:
            segments[idx-1]["image_count"] = count

        total_images_calculated = sum(s.get("image_count", 0) for s in segments)
        self._log(f"  [PHASE 2] Completed! Total images: {total_images_calculated}")
        self._log(f"     Average ratio: {total_srt / total_images_calculated:.1f} SRT/image")

        # Update data
        data["segments"] = segments
        data["total_images"] = total_images_calculated

        # =====================================================================
        # VALIDATION 1: Check PROPORTIONAL image_count vs SRT entries
        # ROOT CAUSE FIX: API có thể trả segment với 833 SRT entries nhưng chỉ 4 images
        # Điều này gây mất 70% nội dung! Cần split segment quá lớn.
        #
        # STRATEGY (user suggestion):
        # - Ratio > 30 (severe): Chia nhỏ 1/2 và GỌI LẠI API
        # - Ratio 15-30 (moderate): Local split (không cần gọi API)
        # =====================================================================
        MAX_SRT_PER_IMAGE = 15  # Threshold for local split
        SEVERE_RATIO = 30  # Threshold for API retry with smaller input

        def _retry_segment_with_api(seg_start, seg_end, seg_name, depth=0):
            """Recursively split and retry API when ratio is too high"""
            if depth > 3:  # Max 3 levels of splitting
                return None

            srt_count = seg_end - seg_start + 1
            if srt_count < 30:  # Too small to split further
                return None

            # Get SRT text for this range
            range_entries = srt_entries[seg_start-1:seg_end]
            range_text = " ".join([e.text for e in range_entries])

            # Call API for this smaller range
            retry_prompt = f"""Analyze this PORTION of a story and divide it into segments for video creation.

SRT RANGE: {seg_start} to {seg_end} ({srt_count} entries)

STORY PORTION:
{range_text[:3000]}

TASK: Divide this portion into 2-4 logical segments.
- Total images should be approximately: {max(2, int(srt_count / 10))}
- Each segment needs at least 1 image

Return JSON only:
{{
    "segments": [
        {{
            "segment_id": 1,
            "segment_name": "Sub-part 1",
            "message": "What happens in this part",
            "key_elements": ["visual elements"],
            "image_count": 3,
            "srt_range_start": {seg_start},
            "srt_range_end": {seg_start + srt_count//2 - 1}
        }}
    ]
}}
"""
            self._log(f"     [RETRY] Calling API for SRT {seg_start}-{seg_end} (depth={depth})...")
            response = self._call_api(retry_prompt, temperature=0.3, max_tokens=2048)

            if response:
                retry_data = self._extract_json(response)
                if retry_data and "segments" in retry_data:
                    retry_segs = retry_data["segments"]
                    # Validate the retry results
                    valid_results = []
                    for rs in retry_segs:
                        rs_start = rs.get("srt_range_start", seg_start)
                        rs_end = rs.get("srt_range_end", seg_end)
                        rs_images = rs.get("image_count", 1)
                        rs_count = rs_end - rs_start + 1
                        rs_ratio = rs_count / max(1, rs_images)

                        if rs_ratio > SEVERE_RATIO:
                            # Still too high! Recursively split
                            self._log(f"     [RETRY] Segment still has ratio {rs_ratio:.1f}, splitting further...")
                            sub_result = _retry_segment_with_api(rs_start, rs_end, rs.get("segment_name", ""), depth + 1)
                            if sub_result:
                                valid_results.extend(sub_result)
                            else:
                                # Fallback: local split
                                valid_results.append(rs)
                        else:
                            valid_results.append(rs)

                    if valid_results:
                        self._log(f"     [RETRY] Got {len(valid_results)} valid sub-segments from API")
                        return valid_results

            return None

        if segments:
            self._log(f"\n  [VALIDATION] Checking segment proportions...")
            validated_segments = []
            next_seg_id = 1

            for seg in segments:
                seg_start = seg.get("srt_range_start", 1)
                seg_end = seg.get("srt_range_end", seg_start)
                image_count = seg.get("image_count", 1)
                srt_count = seg_end - seg_start + 1
                ratio = srt_count / max(1, image_count)

                if ratio > SEVERE_RATIO:
                    # SEVERE! Try API retry with smaller input
                    self._log(f"  [SEVERE] Segment '{seg.get('segment_name')}': {srt_count} SRT / {image_count} images = {ratio:.1f} ratio")
                    self._log(f"     -> Attempting API retry with split input...")

                    retry_result = _retry_segment_with_api(seg_start, seg_end, seg.get("segment_name", ""))

                    if retry_result:
                        # Use API result
                        for rs in retry_result:
                            rs["segment_id"] = next_seg_id
                            validated_segments.append(rs)
                            self._log(f"     -> Added from API: Segment {next_seg_id} ({rs.get('srt_range_start')}-{rs.get('srt_range_end')})")
                            next_seg_id += 1
                    else:
                        # API retry failed, use local split
                        self._log(f"     -> API retry failed, using local split...")
                        entries_per_sub = 80

                        remaining_entries = srt_count
                        current_start = seg_start
                        sub_index = 1

                        while remaining_entries > 0:
                            chunk_entries = min(remaining_entries, entries_per_sub)
                            chunk_images = max(1, int(chunk_entries / 8))
                            chunk_end = current_start + chunk_entries - 1

                            new_seg = {
                                "segment_id": next_seg_id,
                                "segment_name": f"{seg.get('segment_name', 'Part')} ({sub_index})",
                                "message": seg.get("message", ""),
                                "key_elements": seg.get("key_elements", []),
                                "image_count": chunk_images,
                                "srt_range_start": current_start,
                                "srt_range_end": chunk_end,
                                "importance": seg.get("importance", "medium")
                            }
                            validated_segments.append(new_seg)
                            self._log(f"     -> Local split: Segment {next_seg_id}: SRT {current_start}-{chunk_end} ({chunk_images} images)")

                            current_start = chunk_end + 1
                            remaining_entries -= chunk_entries
                            next_seg_id += 1
                            sub_index += 1

                elif ratio > MAX_SRT_PER_IMAGE:
                    # MODERATE! Use local split (no API call needed)
                    self._log(f"  [WARN] Segment '{seg.get('segment_name')}': {srt_count} SRT / {image_count} images = {ratio:.1f} ratio (moderate)")
                    self._log(f"     -> Using local split...")

                    entries_per_sub = 80

                    remaining_entries = srt_count
                    current_start = seg_start
                    sub_index = 1

                    while remaining_entries > 0:
                        chunk_entries = min(remaining_entries, entries_per_sub)
                        chunk_images = max(1, int(chunk_entries / 8))
                        chunk_end = current_start + chunk_entries - 1

                        new_seg = {
                            "segment_id": next_seg_id,
                            "segment_name": f"{seg.get('segment_name', 'Part')} ({sub_index})",
                            "message": seg.get("message", ""),
                            "key_elements": seg.get("key_elements", []),
                            "image_count": chunk_images,
                            "srt_range_start": current_start,
                            "srt_range_end": chunk_end,
                            "importance": seg.get("importance", "medium")
                        }
                        validated_segments.append(new_seg)
                        self._log(f"     -> Segment {next_seg_id}: SRT {current_start}-{chunk_end} ({chunk_images} images)")

                        current_start = chunk_end + 1
                        remaining_entries -= chunk_entries
                        next_seg_id += 1
                        sub_index += 1
                else:
                    # Segment OK, but update segment_id
                    seg["segment_id"] = next_seg_id
                    validated_segments.append(seg)
                    self._log(f"  [OK] Segment {next_seg_id} '{seg.get('segment_name')}': {srt_count} SRT / {image_count} images = {ratio:.1f} ratio")
                    next_seg_id += 1

            # Update segments with validated list
            if len(validated_segments) != len(segments):
                self._log(f"\n  [FIX] Split {len(segments)} segments -> {len(validated_segments)} segments")
            segments = validated_segments
            data["segments"] = segments

        # =====================================================================
        # VALIDATION 2: Check if segments cover ALL SRT entries
        # If missing, CALL API for missing range (instead of empty auto-add)
        # =====================================================================
        if segments:
            last_seg = segments[-1]
            last_srt_end = last_seg.get("srt_range_end", 0)

            if last_srt_end < total_srt:
                missing_entries = total_srt - last_srt_end
                missing_start = last_srt_end + 1
                self._log(f"  [WARN] Segments only cover SRT 1-{last_srt_end}, missing {missing_entries} entries")
                self._log(f"  -> Calling API for missing range SRT {missing_start}-{total_srt}...")

                # Get SRT text for missing range
                missing_srt_entries = srt_entries[last_srt_end:total_srt]
                missing_text = " ".join([e.text for e in missing_srt_entries])
                missing_text_sampled = self._sample_text(missing_text, total_chars=4000)

                # Calculate expected images for missing range
                missing_duration = missing_entries * (total_duration / total_srt)
                expected_images = max(2, int(missing_duration / 5))

                # Call API for missing range
                missing_prompt = f"""Analyze this CONTINUATION portion of a story and divide it into segments for video creation.

THIS IS A CONTINUATION - the story started earlier. Analyze what happens in THIS PORTION.

SRT RANGE: {missing_start} to {total_srt} ({missing_entries} entries)
ESTIMATED DURATION: {missing_duration:.1f} seconds

STORY CONTINUATION:
{missing_text_sampled}

TASK: Divide this continuation into 2-6 logical segments.
Each segment should have:
- message: DETAILED description of what happens (2-3 sentences minimum)
- key_elements: Visual elements for image creation
- visual_summary: What images should show
- image_count: Number of images needed (~{expected_images} total for this range)

Return JSON only:
{{
    "segments": [
        {{
            "segment_id": 1,
            "segment_name": "Continuation Scene Name",
            "message": "DETAILED: What happens, who is involved, emotions, actions",
            "key_elements": ["character doing action", "specific visual", "emotion"],
            "visual_summary": "What images should show for this segment",
            "mood": "emotional tone",
            "characters_involved": [],
            "image_count": 5,
            "srt_range_start": {missing_start},
            "srt_range_end": {missing_start + missing_entries // 3}
        }}
    ]
}}
"""
                api_response = self._call_api(missing_prompt, temperature=0.3, max_tokens=3000)

                api_segments = []
                if api_response:
                    api_data = self._extract_json(api_response)
                    if api_data and "segments" in api_data:
                        api_segments = api_data["segments"]
                        self._log(f"     -> API returned {len(api_segments)} segments for missing range")

                        # Adjust segment IDs, validate ranges, and RECALCULATE image_count
                        seg_id = len(segments) + 1
                        for seg in api_segments:
                            seg["segment_id"] = seg_id
                            # Ensure srt_range is within missing range
                            seg_start = seg.get("srt_range_start", missing_start)
                            seg_end = seg.get("srt_range_end", min(seg_start + 100, total_srt))
                            seg["srt_range_start"] = max(missing_start, seg_start)
                            seg["srt_range_end"] = min(total_srt, seg_end)

                            # CRITICAL: Recalculate image_count based on SRT entries
                            # API may return unreasonable values (e.g., 185 for 308 entries)
                            seg_entries = seg["srt_range_end"] - seg["srt_range_start"] + 1
                            seg["image_count"] = max(2, int(seg_entries / 10))  # ~10 SRT per image

                            segments.append(seg)
                            self._log(f"     -> Added API segment {seg_id}: '{seg.get('segment_name')}' (SRT {seg['srt_range_start']}-{seg['srt_range_end']}, {seg['image_count']} imgs)")
                            seg_id += 1

                # If API failed or incomplete, fallback to auto-add
                current_coverage = max(s.get("srt_range_end", 0) for s in segments) if segments else 0
                if current_coverage < total_srt:
                    remaining = total_srt - current_coverage
                    self._log(f"     -> Still missing {remaining} entries, using fallback auto-add...")

                    current_start = current_coverage + 1
                    seg_id = len(segments) + 1

                    while remaining > 0:
                        chunk = min(remaining, 100)
                        chunk_images = max(1, int(chunk / 10))
                        new_seg = {
                            "segment_id": seg_id,
                            "segment_name": f"Continuation Part {seg_id - len(data['segments'])}",
                            "message": f"Continuing the narrative from SRT {current_start}",
                            "key_elements": ["continuation", "story progression"],
                            "visual_summary": f"Visual continuation of the story from timestamp {current_start}",
                            "mood": "neutral",
                            "image_count": chunk_images,
                            "srt_range_start": current_start,
                            "srt_range_end": min(current_start + chunk - 1, total_srt),
                            "importance": "medium"
                        }
                        segments.append(new_seg)
                        self._log(f"     -> Fallback segment {seg_id}: SRT {current_start}-{new_seg['srt_range_end']} ({chunk_images} images)")

                        current_start = new_seg['srt_range_end'] + 1
                        remaining -= chunk
                        seg_id += 1

                data["segments"] = segments

        # =====================================================================
        # VALIDATION 3: GLOBAL image count check - CRITICAL FIX
        # ROOT CAUSE: API may plan too few images globally even if each segment
        # passes local validation (e.g., 66 images for 459 SRT = 7.0 ratio)
        # v1.0.75: SKIP this check for SMALL mode - we want fewer images intentionally
        # =====================================================================
        excel_mode_check = self.config.get("excel_mode", "full").lower()
        skip_global_check = (excel_mode_check == "small")

        if skip_global_check and segments:
            total_images = sum(s.get("image_count", 0) for s in segments)
            self._log(f"\n  [SMALL MODE] Skipping GLOBAL CHECK - keeping {total_images} images")

        if segments and not skip_global_check:
            total_images = sum(s.get("image_count", 0) for s in segments)
            global_ratio = len(srt_entries) / max(1, total_images)

            # Calculate minimum required images (4 SRT per image max)
            min_required_images = int(len(srt_entries) / 4)

            if total_images < min_required_images:
                shortage = min_required_images - total_images
                shortage_pct = (shortage / min_required_images) * 100

                self._log(f"\n  [GLOBAL CHECK] INSUFFICIENT total images!")
                self._log(f"     Total SRT: {len(srt_entries)}")
                self._log(f"     Planned images: {total_images}")
                self._log(f"     Global ratio: {global_ratio:.1f} SRT/image")
                self._log(f"     Required minimum: {min_required_images} (4 SRT/image max)")
                self._log(f"     Shortage: {shortage} images ({shortage_pct:.0f}%)")
                self._log(f"  -> AUTO-FIX: Proportionally increasing image_count across all segments...")

                # Calculate multiplier to reach minimum
                multiplier = min_required_images / total_images

                # Apply multiplier to each segment
                for seg in segments:
                    old_count = seg.get("image_count", 1)
                    new_count = max(1, int(old_count * multiplier))
                    seg["image_count"] = new_count

                # Recalculate total
                new_total = sum(s.get("image_count", 0) for s in segments)
                new_ratio = len(srt_entries) / max(1, new_total)

                self._log(f"     New total images: {new_total}")
                self._log(f"     New global ratio: {new_ratio:.1f} SRT/image")
                self._log(f"  [FIX] Applied {multiplier:.2f}x multiplier to all segments")

                data["segments"] = segments

        # Save to Excel
        try:
            workbook.save_story_segments(data["segments"], data.get("total_images", 0), data.get("summary", ""))
            workbook.save()

            total_images = sum(s.get("image_count", 0) for s in data["segments"])
            self._log(f"  -> Saved {len(data['segments'])} segments ({total_images} total images)")
            for seg in data["segments"][:5]:
                self._log(f"     - {seg.get('segment_name')}: {seg.get('image_count')} images")

            # TRACKING: Cập nhật và kiểm tra coverage
            coverage = workbook.update_srt_coverage_segments(data["segments"])
            self._log(f"\n  [STATS] SRT COVERAGE (sau Step 1.5):")
            self._log(f"     Total SRT: {coverage['total_srt']}")
            self._log(f"     Covered by segments: {coverage['covered_by_segment']} ({coverage['coverage_percent']}%)")

            # Determine status based on coverage
            elapsed = int(time.time() - step_start)
            if coverage['uncovered'] > 0:
                self._log(f"     [WARN] UNCOVERED: {coverage['uncovered']} entries", "WARN")
                status = "PARTIAL" if coverage['coverage_percent'] >= 50 else "ERROR"
                workbook.update_step_status("step_2", status,
                    coverage['total_srt'], coverage['covered_by_segment'],
                    f"{elapsed}s - {len(data['segments'])} segs, {coverage['uncovered']} uncovered")
            else:
                workbook.update_step_status("step_2", "COMPLETED",
                    coverage['total_srt'], coverage['covered_by_segment'],
                    f"{elapsed}s - {len(data['segments'])} segs, {total_images} imgs")

            return StepResult("analyze_story_segments", StepStatus.COMPLETED, "Success", data)
        except Exception as e:
            self._log(f"  ERROR: Could not save to Excel: {e}", "ERROR")
            elapsed = int(time.time() - step_start)
            workbook.update_step_status("step_2", "ERROR", 0, 0, f"{elapsed}s - {str(e)[:80]}")
            return StepResult("analyze_story_segments", StepStatus.FAILED, str(e))

    # =========================================================================
    # STEP 2: TẠO CHARACTERS
    # =========================================================================

    def step_create_characters(
        self,
        project_dir: Path,
        code: str,
        workbook: PromptWorkbook,
        srt_entries: list,
        txt_content: str = ""
    ) -> StepResult:
        """
        Step 2: Tạo characters dựa trên story_analysis.

        Input: Đọc story_analysis từ Excel
        Output sheet: characters
        """
        import time
        step_start = time.time()

        self._log("\n" + "="*60)
        self._log("[STEP 3/7] Tạo characters...")
        self._log("="*60)

        # Check if already done
        existing_chars = workbook.get_characters()
        if existing_chars and len(existing_chars) > 0:
            self._log(f"  -> Đã có {len(existing_chars)} characters, skip!")
            workbook.update_step_status("step_3", "COMPLETED", len(existing_chars), len(existing_chars), "Already done")
            return StepResult("create_characters", StepStatus.COMPLETED, "Already done")

        # === v1.0.448: Check predefined character (finance topics) ===
        # Ưu tiên: (1) Google Sheet col L override → (2) default từ topic prompts
        if hasattr(self.topic_prompts, 'get_default_character'):
            override = self.character_template or ""
            default_char = self.topic_prompts.get_default_character(override)
            if default_char:
                self._log(f"  -> Dùng nhân vật mặc định (skip API call)")
                if override:
                    self._log(f"  -> Override từ Google Sheet col L")
                try:
                    char_id = "nv1"
                    char = Character(
                        id=char_id,
                        name=default_char.get("name", "Narrator"),
                        role=default_char.get("role", "protagonist"),
                        english_prompt=default_char.get("portrait_prompt", ""),
                        character_lock=default_char.get("character_lock", ""),
                        vietnamese_prompt="",
                        image_file=f"{char_id}.png",
                        is_child=default_char.get("is_minor", False),
                        status="pending",
                    )
                    workbook.add_character(char)
                    workbook.save()
                    self._log(f"  -> Saved predefined character: {char.name} ({char.role})")
                    self._log(f"     character_lock: {char.character_lock[:100]}...")
                    elapsed = int(time.time() - step_start)
                    workbook.update_step_status("step_3", "COMPLETED", 1, 1,
                        f"{elapsed}s - 1 char (predefined)")
                    return StepResult("create_characters", StepStatus.COMPLETED, "Predefined character", {"characters": [default_char]})
                except Exception as e:
                    self._log(f"  WARN: Predefined character failed: {e}, fallback to API", "WARN")

        # Read story_analysis from Excel
        story_analysis = {}
        try:
            story_analysis = workbook.get_story_analysis() or {}
        except:
            pass

        context_lock = story_analysis.get("context_lock", "")
        setting = story_analysis.get("setting", {})

        # OPTIMIZED: Tận dụng insights từ Step 1.5 (segments)
        story_segments = workbook.get_story_segments() or []

        # Build rich context từ segments thay vì đọc lại full text
        segment_insights = ""
        all_characters_mentioned = set()
        all_key_elements = []

        for seg in story_segments:
            seg_name = seg.get("segment_name", "")
            message = seg.get("message", "")
            visual_summary = seg.get("visual_summary", "")
            key_elements = seg.get("key_elements", [])
            chars_involved = seg.get("characters_involved", [])
            mood = seg.get("mood", "")

            segment_insights += f"""
SEGMENT "{seg_name}":
- Story: {message}
- Visuals: {visual_summary}
- Mood: {mood}
- Characters: {', '.join(chars_involved) if isinstance(chars_involved, list) else chars_involved}
- Key elements: {', '.join(key_elements) if isinstance(key_elements, list) else key_elements}
"""
            if isinstance(chars_involved, list):
                all_characters_mentioned.update(chars_involved)
            if isinstance(key_elements, list):
                all_key_elements.extend(key_elements)

        # Chỉ dùng TARGETED text từ SRT cho các segment chính (đầu + giữa + cuối)
        # thay vì gửi full text
        targeted_srt_text = ""
        if story_segments and srt_entries:
            # Lấy 3 segments: đầu, giữa, cuối
            target_segments = [story_segments[0]]
            if len(story_segments) > 2:
                target_segments.append(story_segments[len(story_segments)//2])
                target_segments.append(story_segments[-1])
            elif len(story_segments) > 1:
                target_segments.append(story_segments[-1])

            for seg in target_segments:
                srt_start = seg.get("srt_range_start", 1)
                srt_end = seg.get("srt_range_end", min(srt_start + 20, len(srt_entries)))
                # Chỉ lấy 10 entries đầu của mỗi segment
                entries_to_take = min(10, srt_end - srt_start + 1)
                targeted_srt_text += f"\n[From segment '{seg.get('segment_name')}']\n"
                targeted_srt_text += self._get_srt_for_range(srt_entries, srt_start, srt_start + entries_to_take - 1)

        self._log(f"  Using {len(story_segments)} segment insights + targeted SRT (~{len(targeted_srt_text)} chars)")

        # Build prompt - v1.0.431: dung topic_prompts
        prompt = self.topic_prompts.step3_characters(
            setting, context_lock, all_characters_mentioned,
            segment_insights, targeted_srt_text
        )

        # Call API
        response = self._call_api(prompt, temperature=0.5)
        if not response:
            self._log("  ERROR: API call failed!", "ERROR")
            return StepResult("create_characters", StepStatus.FAILED, "API call failed")

        # Parse response
        data = self._extract_json(response)
        if not data or "characters" not in data:
            self._log("  ERROR: Could not parse characters!", "ERROR")
            return StepResult("create_characters", StepStatus.FAILED, "JSON parse failed")

        # Save to Excel
        try:
            minor_count = 0
            char_counter = 0  # Đếm để tạo ID đơn giản: nv1, nv2, nv3...
            nvc_used = False  # v1.0.540: Chi 1 narrator duoc gan ID "nvc"

            for char_data in data["characters"]:
                role = char_data.get("role", "supporting").lower()

                # Tạo ID đơn giản và nhất quán
                # v1.0.433: narrator detection chi ap dung cho story topic
                # v1.0.540: Chi narrator DAU TIEN la nvc, cac narrator sau dung nv{counter}
                if not nvc_used and self.topic_prompts.has_narrator_role() and (role == "narrator" or "narrator" in char_data.get("name", "").lower()):
                    char_id = "nvc"  # Narrator DAU TIEN la nvc
                    nvc_used = True
                else:
                    char_counter += 1
                    char_id = f"nv{char_counter}"  # nv1, nv2, nv3...

                # Detect trẻ vị thành niên (dưới 18 tuổi)
                is_minor = char_data.get("is_minor", False)
                if isinstance(is_minor, str):
                    is_minor = is_minor.lower() in ("true", "yes", "1")

                char = Character(
                    id=char_id,
                    name=char_data.get("name", ""),
                    role=char_data.get("role", "supporting"),
                    english_prompt=char_data.get("portrait_prompt", ""),
                    character_lock=char_data.get("character_lock", ""),
                    vietnamese_prompt=char_data.get("vietnamese_description", ""),
                    image_file=f"{char_id}.png",
                    is_child=is_minor,
                    status="skip" if is_minor else "pending",  # Skip tạo ảnh cho trẻ em
                )
                workbook.add_character(char)

                if is_minor:
                    minor_count += 1

            workbook.save()
            self._log(f"  -> Saved {len(data['characters'])} characters to Excel")
            if minor_count > 0:
                self._log(f"  -> [WARN] {minor_count} characters là trẻ em (sẽ KHÔNG tạo ảnh)")
            for c in data["characters"][:3]:
                minor_tag = " [MINOR]" if c.get("is_minor") else ""
                self._log(f"     - {c.get('name', 'N/A')} ({c.get('role', 'N/A')}){minor_tag}")
            if len(data["characters"]) > 3:
                self._log(f"     ... và {len(data['characters']) - 3} characters khác")

            # Update step status with duration
            elapsed = int(time.time() - step_start)
            workbook.update_step_status("step_3", "COMPLETED", len(data['characters']), len(data['characters']),
                f"{elapsed}s - {len(data['characters'])} chars")

            return StepResult("create_characters", StepStatus.COMPLETED, "Success", data)
        except Exception as e:
            self._log(f"  ERROR: Could not save to Excel: {e}", "ERROR")
            elapsed = int(time.time() - step_start)
            workbook.update_step_status("step_3", "ERROR", 0, 0, f"{elapsed}s - {str(e)[:80]}")
            return StepResult("create_characters", StepStatus.FAILED, str(e))

    # =========================================================================
    # STEP 4: TẠO LOCATIONS
    # =========================================================================

    def step_create_locations(
        self,
        project_dir: Path,
        code: str,
        workbook: PromptWorkbook,
        srt_entries: list,
        txt_content: str = ""
    ) -> StepResult:
        """
        Step 3: Tạo locations dựa trên story_analysis + characters.

        Input: Đọc story_analysis, characters từ Excel
        Output sheet: locations
        """
        import time
        step_start = time.time()

        self._log("\n" + "="*60)
        self._log("[STEP 4/7] Tạo locations...")
        self._log("="*60)

        # v1.0.446: Video-only topics skip locations entirely
        is_video_only = getattr(self.topic_prompts, 'is_video_only', lambda: False)()
        if is_video_only:
            self._log("  -> VIDEO-ONLY mode: Skip locations (no reference images needed)")
            workbook.update_step_status("step_4", "COMPLETED", 0, 0, "Video-only: no locations")
            workbook.save()
            return StepResult("create_locations", StepStatus.COMPLETED, "Video-only: skipped")

        # Check if already done
        existing_locs = workbook.get_locations()
        if existing_locs and len(existing_locs) > 0:
            self._log(f"  -> Đã có {len(existing_locs)} locations, skip!")
            workbook.update_step_status("step_4", "COMPLETED", len(existing_locs), len(existing_locs), "Already done")
            return StepResult("create_locations", StepStatus.COMPLETED, "Already done")

        # Read context from Excel
        story_analysis = {}
        try:
            story_analysis = workbook.get_story_analysis() or {}
        except:
            pass

        characters = workbook.get_characters()
        char_names = [c.name for c in characters] if characters else []

        context_lock = story_analysis.get("context_lock", "")
        setting = story_analysis.get("setting", {})

        # OPTIMIZED: Tận dụng insights từ Step 1.5 (segments)
        story_segments = workbook.get_story_segments() or []

        # Build rich context từ segments thay vì đọc lại full text
        segment_insights = ""
        all_locations_hints = set()

        for seg in story_segments:
            seg_name = seg.get("segment_name", "")
            message = seg.get("message", "")
            visual_summary = seg.get("visual_summary", "")
            key_elements = seg.get("key_elements", [])
            mood = seg.get("mood", "")

            segment_insights += f"""
SEGMENT "{seg_name}":
- Story: {message}
- Visuals: {visual_summary}
- Mood: {mood}
- Key elements: {', '.join(key_elements) if isinstance(key_elements, list) else key_elements}
"""
            # Extract location hints từ key_elements
            if isinstance(key_elements, list):
                for elem in key_elements:
                    elem_lower = elem.lower()
                    if any(word in elem_lower for word in ["room", "house", "office", "street", "park", "school", "hospital", "forest", "beach", "city", "village", "building", "kitchen", "bedroom", "garden", "car", "restaurant", "cafe", "church"]):
                        all_locations_hints.add(elem)

        # Chỉ lấy targeted SRT từ vài segment để có thêm context
        targeted_srt_text = ""
        if story_segments and srt_entries:
            target_segments = [story_segments[0]]
            if len(story_segments) > 2:
                target_segments.append(story_segments[len(story_segments)//2])
                target_segments.append(story_segments[-1])
            elif len(story_segments) > 1:
                target_segments.append(story_segments[-1])

            for seg in target_segments:
                srt_start = seg.get("srt_range_start", 1)
                entries_to_take = min(8, len(srt_entries) - srt_start + 1)
                targeted_srt_text += f"\n[From segment '{seg.get('segment_name')}']\n"
                targeted_srt_text += self._get_srt_for_range(srt_entries, srt_start, srt_start + entries_to_take - 1)

        self._log(f"  Using {len(story_segments)} segment insights + targeted SRT (~{len(targeted_srt_text)} chars)")

        # Build prompt - v1.0.431: dung topic_prompts
        prompt = self.topic_prompts.step4_locations(
            setting, context_lock, char_names,
            all_locations_hints, segment_insights, targeted_srt_text
        )

        # Call API
        response = self._call_api(prompt, temperature=0.5)
        if not response:
            self._log("  ERROR: API call failed!", "ERROR")
            return StepResult("create_locations", StepStatus.FAILED, "API call failed")

        # Parse response
        data = self._extract_json(response)
        if not data or "locations" not in data:
            self._log("  ERROR: Could not parse locations!", "ERROR")
            return StepResult("create_locations", StepStatus.FAILED, "JSON parse failed")

        # Save to Excel - LƯU VÀO SHEET CHARACTERS với id loc_xxx
        try:
            loc_counter = 0  # Đếm để tạo ID đơn giản: loc1, loc2, loc3...

            for loc_data in data["locations"]:
                loc_counter += 1
                loc_id = f"loc{loc_counter}"  # Đơn giản: loc1, loc2, loc3...

                # Tạo Character với role="location" thay vì Location riêng
                loc_char = Character(
                    id=loc_id,
                    name=loc_data.get("name", ""),
                    role="location",  # Đánh dấu là location
                    english_prompt=loc_data.get("location_prompt", ""),
                    character_lock=loc_data.get("location_lock", ""),
                    vietnamese_prompt=loc_data.get("lighting_default", ""),  # Dùng field này cho lighting
                    image_file=f"{loc_id}.png",
                    status="pending",
                )
                workbook.add_character(loc_char)  # Thêm vào characters sheet

            workbook.save()
            self._log(f"  -> Saved {len(data['locations'])} locations to characters sheet")
            for loc in data["locations"][:3]:
                self._log(f"     - {loc.get('name', 'N/A')}")

            # Update step status with duration
            elapsed = int(time.time() - step_start)
            workbook.update_step_status("step_4", "COMPLETED", len(data['locations']), len(data['locations']),
                f"{elapsed}s - {len(data['locations'])} locs")

            return StepResult("create_locations", StepStatus.COMPLETED, "Success", data)
        except Exception as e:
            self._log(f"  ERROR: Could not save to Excel: {e}", "ERROR")
            elapsed = int(time.time() - step_start)
            workbook.update_step_status("step_4", "ERROR", 0, 0, f"{elapsed}s - {str(e)[:80]}")
            return StepResult("create_locations", StepStatus.FAILED, str(e))

    # =========================================================================
    # STEP 5: TẠO DIRECTOR'S PLAN (OPTIMIZED - SEGMENT-FIRST)
    # =========================================================================

    def step_create_director_plan(
        self,
        project_dir: Path,
        code: str,
        workbook: PromptWorkbook,
        srt_entries: list
    ) -> StepResult:
        """
        Step 4: Tạo director's plan - OPTIMIZED với segment-first approach.

        THAY ĐỔI SO VỚI PHIÊN BẢN CŨ:
        - CŨ: Chia SRT theo character count (~6000 chars) → batch processing
        - MỚI: Xử lý BY SEGMENT từ Step 1.5, tận dụng segment insights

        Mỗi segment đã có:
        - message: Nội dung chính của segment
        - visual_summary: Mô tả visual cần show
        - key_elements: Các yếu tố quan trọng
        - mood: Tone cảm xúc
        - characters_involved: Nhân vật xuất hiện
        - image_count: Số scenes cần tạo

        → API chỉ cần quyết định HOW to visualize, không cần re-read toàn bộ story
        """
        # Redirect to basic version which is complete
        return self.step_create_director_plan_basic(project_dir, code, workbook, srt_entries)

    def _process_segment_sub_batch(self, seg_name, message, visual_summary, key_elements,
                                    mood, chars_involved, image_count, srt_start, srt_end,
                                    srt_entries, context_lock, char_locks, loc_locks):
        """Helper: Xử lý sub-batch nhỏ của segment (dùng khi segment quá lớn hoặc retry fail)."""
        import time

        # Lấy SRT text cho sub-batch
        seg_srt_text = self._get_srt_for_range(srt_entries, srt_start, srt_end)

        # Tính duration
        seg_duration = (srt_end - srt_start + 1) * 3  # ~3s per entry

        # Build character/location info
        relevant_chars = []
        if isinstance(chars_involved, list):
            for char_name in chars_involved:
                for cid, clock in char_locks.items():
                    if char_name.lower() in clock.lower() or char_name.lower() in cid.lower():
                        relevant_chars.append(f"- {cid}: {clock}")
                        break
        if not relevant_chars:
            relevant_chars = [f"- {cid}: {clock}" for cid, clock in list(char_locks.items())[:5]]

        relevant_locs = [f"- {lid}: {llock}" for lid, llock in list(loc_locks.items())[:3]]

        # Build prompt
        prompt = f"""Create {image_count} visual scenes for this content segment.

SEGMENT: "{seg_name}"
Story: {message}
Visuals: {visual_summary}
Mood: {mood}
Key elements: {', '.join(key_elements) if isinstance(key_elements, list) else key_elements}

VISUAL STYLE: {context_lock}

CHARACTERS:
{chr(10).join(relevant_chars) if relevant_chars else 'Use generic descriptions'}

LOCATIONS:
{chr(10).join(relevant_locs) if relevant_locs else 'Use generic descriptions'}

SRT ({srt_end - srt_start + 1} entries):
{seg_srt_text[:3000]}

TASK: Create EXACTLY {image_count} scenes (~{seg_duration/image_count:.1f}s each)

Return JSON only:
{{
    "scenes": [
        {{
            "scene_id": 1,
            "srt_indices": [list of SRT indices],
            "srt_start": "00:00:00,000",
            "srt_end": "00:00:05,000",
            "duration": {seg_duration/image_count:.1f},
            "srt_text": "narration",
            "visual_moment": "specific visual",
            "characters_used": "nv_xxx",
            "location_used": "loc_xxx",
            "camera": "shot type",
            "lighting": "lighting"
        }}
    ]
}}
"""

        # Call API with retry (simpler - 3 retries)
        MAX_RETRIES = 3
        for retry in range(MAX_RETRIES):
            response = self._call_api(prompt, temperature=0.5, max_tokens=4096)
            if response:
                data = self._extract_json(response)
                if data and "scenes" in data:
                    self._log(f"     -> Sub-batch got {len(data['scenes'])} scenes")
                    return data["scenes"]
            time.sleep(2 ** retry)

        # Nếu fail, trả về empty list (không tạo fallback cho sub-batch)
        self._log(f"     -> Sub-batch failed after {MAX_RETRIES} retries", "WARNING")
        return []

        # Execute segments in parallel
        segment_results = {}
        with ThreadPoolExecutor(max_workers=MAX_PARALLEL) as executor:
            futures = {executor.submit(process_segment, (i, seg)): i for i, seg in enumerate(story_segments)}
            for future in as_completed(futures):
                seg_idx = futures[future]
                try:
                    result_idx, scenes = future.result()
                    segment_results[result_idx] = scenes
                except Exception as e:
                    self._log(f"     -> Segment {seg_idx+1} failed: {e}", "ERROR")
                    segment_results[seg_idx] = []

        # Merge results in order and assign scene_ids
        scene_id_counter = 1
        for seg_idx in range(len(story_segments)):
            seg_scenes = segment_results.get(seg_idx, [])
            segment_id = seg_idx + 1  # Segment 1, 2, 3...
            for scene in seg_scenes:
                scene["scene_id"] = scene_id_counter
                scene["segment_id"] = segment_id  # LƯU segment_id
                all_scenes.append(scene)
                scene_id_counter += 1

        # Kiểm tra có scenes không
        if not all_scenes:
            self._log("  ERROR: No scenes created!", "ERROR")
            return StepResult("create_director_plan", StepStatus.FAILED, "No scenes created")

        # Save to Excel
        try:
            workbook.save_director_plan(all_scenes)
            workbook.save()
            self._log(f"  -> Saved {len(all_scenes)} scenes to director_plan")
            self._log(f"     Total duration: {sum(s.get('duration', 0) for s in all_scenes):.1f}s")

            # TRACKING: Cập nhật và kiểm tra coverage
            coverage = workbook.update_srt_coverage_scenes(all_scenes)
            self._log(f"\n  [STATS] SRT COVERAGE (sau Step 4):")
            self._log(f"     Total SRT: {coverage['total_srt']}")
            self._log(f"     Covered by scenes: {coverage['covered_by_scene']} ({coverage['coverage_percent']}%)")

            total_duration = sum(s.get('duration', 0) for s in all_scenes)

            # Determine status based on coverage
            elapsed = int(time.time() - step_start)
            if coverage['uncovered'] > 0:
                self._log(f"     [WARN] UNCOVERED: {coverage['uncovered']} entries", "WARN")
                uncovered_list = workbook.get_uncovered_srt_entries()
                if uncovered_list:
                    self._log(f"     Missing SRT: {[u['srt_index'] for u in uncovered_list[:10]]}...")
                status = "PARTIAL" if coverage['coverage_percent'] >= 80 else "ERROR"
                workbook.update_step_status("step_5", status,
                    coverage['total_srt'], coverage['covered_by_scene'],
                    f"{elapsed}s - {len(all_scenes)} scenes, {coverage['uncovered']} uncovered")
            else:
                workbook.update_step_status("step_5", "COMPLETED",
                    coverage['total_srt'], coverage['covered_by_scene'],
                    f"{elapsed}s - {len(all_scenes)} scenes, {total_duration:.0f}s")

            return StepResult("create_director_plan", StepStatus.COMPLETED, "Success", {"scenes": all_scenes})
        except Exception as e:
            self._log(f"  ERROR: Could not save to Excel: {e}", "ERROR")
            elapsed = int(time.time() - step_start)
            workbook.update_step_status("step_5", "ERROR", 0, 0, f"{elapsed}s - {str(e)[:80]}")
            return StepResult("create_director_plan", StepStatus.FAILED, str(e))

    def _step_create_director_plan_legacy(
        self,
        project_dir: Path,
        code: str,
        workbook: PromptWorkbook,
        srt_entries: list
    ) -> StepResult:
        """
        Legacy fallback: Xử lý SRT theo character-batch khi không có segments.
        Chỉ dùng khi Step 1.5 chưa chạy.
        """
        self._log("  Using legacy character-batch mode...")

        story_analysis = workbook.get_story_analysis() or {}
        characters = workbook.get_characters()
        locations = workbook.get_locations()
        context_lock = story_analysis.get("context_lock", "")

        char_locks = [f"- {c.id}: {c.character_lock}" for c in characters if c.character_lock]
        loc_locks = [f"- {loc.id}: {loc.location_lock}" for loc in locations if hasattr(loc, 'location_lock') and loc.location_lock]

        # Build valid ID sets for normalization
        valid_char_ids = {c.id for c in characters}
        valid_loc_ids = {loc.id for loc in locations}

        # Chia SRT entries thành batches ~6000 chars
        MAX_BATCH_CHARS = 6000
        batches = []
        current_batch = []
        current_chars = 0

        for i, entry in enumerate(srt_entries):
            entry_text = f"[{i+1}] {entry.start_time} --> {entry.end_time}\n{entry.text}\n\n"
            entry_len = len(entry_text)

            if current_chars + entry_len > MAX_BATCH_CHARS and current_batch:
                batches.append(current_batch)
                current_batch = []
                current_chars = 0

            current_batch.append((i, entry))
            current_chars += entry_len

        if current_batch:
            batches.append(current_batch)

        all_scenes = []
        scene_id_counter = 1

        for batch_idx, batch_entries in enumerate(batches):
            batch_start = batch_entries[0][0]
            batch_end = batch_entries[-1][0]

            srt_text = ""
            for idx, entry in batch_entries:
                srt_text += f"[{idx+1}] {entry.start_time} --> {entry.end_time}\n{entry.text}\n\n"

            prompt = f"""Create visual scenes for this content.

CONTEXT: {context_lock}

CHARACTERS:
{chr(10).join(char_locks[:5]) if char_locks else 'Generic'}

LOCATIONS:
{chr(10).join(loc_locks[:3]) if loc_locks else 'Generic'}

SRT (entries {batch_start+1}-{batch_end+1}):
{srt_text}

Create scenes (~8s each). Return JSON:
{{"scenes": [{{"scene_id": {scene_id_counter}, "srt_indices": [], "srt_start": "", "srt_end": "", "duration": 8, "srt_text": "", "visual_moment": "", "characters_used": "", "location_used": "", "camera": "", "lighting": ""}}]}}
"""

            response = self._call_api(prompt, temperature=0.5, max_tokens=4096)
            data = self._extract_json(response) if response else None

            if data and "scenes" in data:
                for scene in data["scenes"]:
                    scene["scene_id"] = scene_id_counter

                    # Normalize IDs từ API response
                    raw_chars = scene.get("characters_used", "")
                    raw_loc = scene.get("location_used", "")
                    scene["characters_used"] = self._normalize_character_ids(raw_chars, valid_char_ids)
                    scene["location_used"] = self._normalize_location_id(raw_loc, valid_loc_ids)

                    all_scenes.append(scene)
                    scene_id_counter += 1

        if not all_scenes:
            return StepResult("create_director_plan", StepStatus.FAILED, "No scenes created")

        workbook.save_director_plan(all_scenes)
        workbook.save()
        return StepResult("create_director_plan", StepStatus.COMPLETED, "Success (legacy)", {"scenes": all_scenes})

    # =========================================================================
    # STEP 5 BASIC: TẠO DIRECTOR'S PLAN (SEGMENT-BASED, NO 8s LIMIT)
    # =========================================================================

    def step_create_director_plan_basic(
        self,
        project_dir: Path,
        code: str,
        workbook: PromptWorkbook,
        srt_entries: list,
    ) -> StepResult:
        """
        Step 4 BASIC: Tạo director's plan dựa trên story segments.

        Khác với phiên bản thường:
        - KHÔNG giới hạn 8s
        - Số scenes = tổng image_count từ tất cả segments
        - Duration = segment_duration / image_count
        - Dựa hoàn toàn vào kế hoạch từ Step 1.5

        Input: story_segments, characters, locations, SRT
        Output: director_plan với số scenes = planned images
        """
        self._log("\n" + "="*60)
        self._log("[STEP 5/7] Creating director's plan (segment-based)...")
        self._log("="*60)

        # Check if already done
        try:
            existing_plan = workbook.get_director_plan()
            if existing_plan and len(existing_plan) > 0:
                self._log(f"  -> Already has {len(existing_plan)} scenes, skip!")
                workbook.update_step_status("step_5", "COMPLETED", len(existing_plan), len(existing_plan), "Already done")
                return StepResult("create_director_plan_basic", StepStatus.COMPLETED, "Already done")
        except:
            pass

        # Read story segments (REQUIRED for basic mode)
        story_segments = workbook.get_story_segments() or []
        if not story_segments:
            self._log("  ERROR: No story segments! Run step 1.5 first.", "ERROR")
            return StepResult("create_director_plan_basic", StepStatus.FAILED, "No story segments")

        total_planned_images = sum(s.get("image_count", 0) for s in story_segments)
        self._log(f"  Story segments: {len(story_segments)} segments, {total_planned_images} planned images")

        # Read context
        story_analysis = workbook.get_story_analysis() or {}
        characters = workbook.get_characters()
        locations = workbook.get_locations()

        context_lock = story_analysis.get("context_lock", "")

        # Build character/location info + valid ID sets for normalization
        char_locks = []
        valid_char_ids = set()  # Để normalize IDs từ API response
        for c in characters:
            valid_char_ids.add(c.id)
            if c.character_lock:
                char_locks.append(f"- {c.id}: {c.character_lock}")

        loc_locks = []
        valid_loc_ids = set()  # Để normalize IDs từ API response
        for loc in locations:
            valid_loc_ids.add(loc.id)
            if hasattr(loc, 'location_lock') and loc.location_lock:
                loc_locks.append(f"- {loc.id}: {loc.location_lock}")

        self._log(f"  Valid char IDs: {valid_char_ids}")
        self._log(f"  Valid loc IDs: {valid_loc_ids}")

        # Process segments in PARALLEL
        all_scenes = []
        total_entries = len(srt_entries)
        MAX_PARALLEL = self.config.get("max_parallel_api", 6)

        self._log(f"  Processing {len(story_segments)} segments in parallel (max {MAX_PARALLEL} concurrent)...")

        # HELPER: Process single segment - returns (seg_idx, scenes_list, actual_image_count)
        def process_segment_basic(seg_idx_seg):
            seg_idx, seg = seg_idx_seg
            local_scenes = []

            seg_id = seg.get("segment_id", seg_idx + 1)
            seg_name = seg.get("segment_name", "")
            image_count = seg.get("image_count", 1)
            srt_start = seg.get("srt_range_start", 1)
            srt_end = seg.get("srt_range_end", total_entries)
            message = seg.get("message", "")

            # v1.0.319: Clamp SRT range to valid bounds
            srt_start = max(1, min(srt_start, total_entries))
            srt_end = max(1, min(srt_end, total_entries))

            self._log(f"  Segment {seg_id}/{len(story_segments)}: {seg_name} ({image_count} images, SRT {srt_start}-{srt_end})")

            # Get SRT entries for this segment
            seg_entries = [e for i, e in enumerate(srt_entries, 1) if srt_start <= i <= srt_end]

            if not seg_entries:
                self._log(f"     -> No SRT entries for this segment, skip")
                return (seg_idx, [], 0)

            # Calculate segment duration
            try:
                first_entry = seg_entries[0]
                last_entry = seg_entries[-1]

                # Parse timestamps
                def parse_time(ts):
                    parts = ts.replace(',', ':').split(':')
                    return int(parts[0])*3600 + int(parts[1])*60 + int(parts[2]) + int(parts[3])/1000

                seg_start_time = parse_time(first_entry.start_time)
                seg_end_time = parse_time(last_entry.end_time)
                seg_duration = seg_end_time - seg_start_time
            except:
                seg_duration = len(seg_entries) * 5  # Fallback: 5s per entry

            # ÁP DỤNG 8s RULE dựa trên mode
            max_scene_duration = self.config.get("max_scene_duration", 8)
            min_scene_duration = self.config.get("min_scene_duration", 5)
            excel_mode = self.config.get("excel_mode", "full").lower()

            # BASIC mode: Chỉ Segment 1 áp dụng 8s rule
            # FULL mode: Tất cả segments áp dụng 8s rule
            should_apply_8s_rule = (excel_mode == "full") or (excel_mode == "basic" and seg_id == 1)

            if should_apply_8s_rule:
                original_image_count = image_count
                # Tính số scenes tối thiểu để mỗi scene <= max_scene_duration
                min_scenes_needed = max(1, int(seg_duration / max_scene_duration))
                if seg_duration / min_scenes_needed > max_scene_duration:
                    min_scenes_needed += 1  # Thêm 1 scene nếu vẫn vượt

                # Sử dụng số lớn hơn giữa planned và min_scenes_needed
                if min_scenes_needed > image_count:
                    image_count = min_scenes_needed
                    mode_label = "BASIC Seg 1" if excel_mode == "basic" else "FULL"
                    self._log(f"     -> [{mode_label}] Segment {seg_id}: {original_image_count} planned → {image_count} scenes (max {max_scene_duration}s/scene)")

            # Calculate duration per scene
            scene_duration = seg_duration / image_count if image_count > 0 else seg_duration
            entries_per_scene = len(seg_entries) / image_count if image_count > 0 else len(seg_entries)

            # Build SRT text for API prompt
            srt_text = ""
            for i, entry in enumerate(seg_entries):
                idx = srt_start + i
                srt_text += f"[{idx}] {entry.start_time} --> {entry.end_time}\n{entry.text}\n\n"

            # Call API to create scenes - v1.0.431: dung topic_prompts
            prompt = self.topic_prompts.step5_director_plan(
                image_count, seg_name, message, seg_duration, scene_duration,
                min_scene_duration, max_scene_duration,
                context_lock, char_locks, loc_locks, srt_text
            )

            # Call API with retry logic
            MAX_RETRIES = 3
            data = None

            for retry in range(MAX_RETRIES):
                response = self._call_api(prompt, temperature=0.5, max_tokens=8192)
                if response:
                    data = self._extract_json(response)
                    if data and "scenes" in data:
                        break
                time.sleep(2 ** retry)

            # If all retries failed, create fallback scenes
            if not data or "scenes" not in data:
                self._log(f"     -> All retries failed, creating {image_count} fallback scenes", "WARNING")
                for i in range(image_count):
                    start_idx = int(i * entries_per_scene)
                    end_idx = min(int((i + 1) * entries_per_scene), len(seg_entries))
                    scene_ents = seg_entries[start_idx:end_idx] if seg_entries else []

                    fallback_scene = {
                        "scene_id": 0,  # Will be assigned after merge
                        "srt_indices": list(range(srt_start + start_idx, srt_start + end_idx)),
                        "srt_start": scene_ents[0].start_time if scene_ents else "",
                        "srt_end": scene_ents[-1].end_time if scene_ents else "",
                        "duration": scene_duration,
                        "srt_text": " ".join([e.text for e in scene_ents]) if scene_ents else "",
                        "visual_moment": f"[Auto] Scene {i+1}/{image_count} from: {seg_name}",
                        "characters_used": "",
                        "location_used": "",
                        "camera": "Medium shot",
                        "lighting": "Natural lighting"
                    }
                    local_scenes.append(fallback_scene)
                return (seg_idx, local_scenes, image_count)

            # Process API response
            api_scenes = data["scenes"]
            self._log(f"     -> Got {len(api_scenes)} scenes from API")

            # Ensure correct scene count - add missing if needed
            if len(api_scenes) < image_count:
                self._log(f"     -> Warning: Expected {image_count}, got {len(api_scenes)} - ADDING MISSING")
                existing_srt_indices = set()
                for s in api_scenes:
                    indices = s.get("srt_indices", [])
                    if isinstance(indices, list):
                        existing_srt_indices.update(indices)

                all_seg_indices = list(range(srt_start, srt_end + 1))
                missing_indices = [i for i in all_seg_indices if i not in existing_srt_indices]

                scenes_needed = image_count - len(api_scenes)
                if missing_indices and scenes_needed > 0:
                    indices_per_scene_fill = max(1, len(missing_indices) // scenes_needed)
                    for i in range(scenes_needed):
                        start_i = i * indices_per_scene_fill
                        end_i = min((i + 1) * indices_per_scene_fill, len(missing_indices))
                        scene_indices = missing_indices[start_i:end_i]
                        if not scene_indices:
                            continue
                        scene_ents = [e for idx, e in enumerate(srt_entries, 1) if idx in scene_indices]
                        fill_scene = {
                            "scene_id": 0,
                            "srt_indices": scene_indices,
                            "srt_start": scene_ents[0].start_time if scene_ents else "",
                            "srt_end": scene_ents[-1].end_time if scene_ents else "",
                            "duration": scene_duration,
                            "srt_text": " ".join([e.text for e in scene_ents]) if scene_ents else "",
                            "visual_moment": f"[Auto-fill] Scene covering SRT {scene_indices[0]}-{scene_indices[-1]}",
                            "characters_used": "",
                            "location_used": "",
                            "camera": "Medium shot",
                            "lighting": "Natural lighting"
                        }
                        api_scenes.append(fill_scene)
                    self._log(f"     -> Added {scenes_needed} auto-fill scenes, total: {len(api_scenes)}")

            # v1.0.451: Đảm bảo mỗi scene đều có srt_text populated
            # API có thể trả về srt_text trống hoặc thiếu → lấy từ SRT entries thực tế
            for sc in api_scenes:
                if not sc.get("srt_text"):
                    sc_indices = sc.get("srt_indices", [])
                    if isinstance(sc_indices, list) and sc_indices:
                        sc_ents = [e for idx, e in enumerate(srt_entries, 1) if idx in sc_indices]
                        sc["srt_text"] = " ".join([e.text for e in sc_ents]) if sc_ents else ""

            local_scenes.extend(api_scenes)
            return (seg_idx, local_scenes, image_count)

        # Execute segments in parallel
        segment_results = {}
        with ThreadPoolExecutor(max_workers=MAX_PARALLEL) as executor:
            futures = {executor.submit(process_segment_basic, (i, seg)): i for i, seg in enumerate(story_segments)}
            for future in as_completed(futures):
                seg_idx = futures[future]
                try:
                    result_idx, scenes, _ = future.result()
                    segment_results[result_idx] = scenes
                except Exception as e:
                    self._log(f"     -> Segment {seg_idx+1} failed: {e}", "ERROR")
                    segment_results[seg_idx] = []

        # =====================================================================
        # v1.0.89: RETRY LOGIC - Đảm bảo 100% segments có data
        # Nếu segment nào trả về empty, retry với exponential backoff
        # =====================================================================
        failed_segments = [i for i in range(len(story_segments)) if not segment_results.get(i)]

        if failed_segments:
            self._log(f"\n  [RETRY] Found {len(failed_segments)} failed segments, retrying sequentially...")

            SEGMENT_RETRY_MAX = 5
            for seg_idx in failed_segments:
                seg = story_segments[seg_idx]
                seg_name = seg.get("segment_name", f"Segment {seg_idx+1}")

                for retry_attempt in range(SEGMENT_RETRY_MAX):
                    delay = 3 * (2 ** retry_attempt)  # 3, 6, 12, 24, 48 seconds
                    self._log(f"     Retry {retry_attempt+1}/{SEGMENT_RETRY_MAX} for segment {seg_idx+1} ({seg_name}) after {delay}s...")
                    time.sleep(delay)

                    try:
                        result_idx, scenes, _ = process_segment_basic((seg_idx, seg))
                        if scenes:
                            segment_results[seg_idx] = scenes
                            self._log(f"     -> SUCCESS! Got {len(scenes)} scenes on retry {retry_attempt+1}")
                            break
                    except Exception as e:
                        self._log(f"     -> Retry {retry_attempt+1} failed: {e}", "WARNING")

                # v1.0.89: Nếu vẫn fail sau tất cả retries, tạo GUARANTEED fallback scenes
                if not segment_results.get(seg_idx):
                    self._log(f"     -> All retries exhausted for segment {seg_idx+1}, creating GUARANTEED fallback scenes", "WARNING")

                    # Lấy thông tin segment để tạo fallback
                    image_count = seg.get("image_count", 1)
                    srt_start = seg.get("srt_range_start", 1)
                    srt_end = seg.get("srt_range_end", len(srt_entries))
                    seg_entries = [e for i, e in enumerate(srt_entries, 1) if srt_start <= i <= srt_end]

                    entries_per_scene = max(1, len(seg_entries) / image_count) if image_count > 0 else len(seg_entries)
                    fallback_scenes = []

                    for i in range(image_count):
                        start_idx = int(i * entries_per_scene)
                        end_idx = min(int((i + 1) * entries_per_scene), len(seg_entries))
                        scene_ents = seg_entries[start_idx:end_idx] if seg_entries else []

                        fallback_scene = {
                            "scene_id": 0,  # Will be assigned after merge
                            "srt_indices": list(range(srt_start + start_idx, srt_start + min(end_idx, len(seg_entries)))),
                            "srt_start": scene_ents[0].start_time if scene_ents else "",
                            "srt_end": scene_ents[-1].end_time if scene_ents else "",
                            "duration": 5.0,  # Default 5s
                            "srt_text": " ".join([e.text for e in scene_ents]) if scene_ents else "",
                            "visual_moment": f"[Fallback] Scene {i+1}/{image_count} from: {seg_name}",
                            "characters_used": "",
                            "location_used": "",
                            "camera": "Medium shot",
                            "lighting": "Natural lighting"
                        }
                        fallback_scenes.append(fallback_scene)

                    segment_results[seg_idx] = fallback_scenes
                    self._log(f"     -> Created {len(fallback_scenes)} guaranteed fallback scenes for segment {seg_idx+1}")

        # Merge results in order and assign scene_ids
        scene_id_counter = 1
        for seg_idx in range(len(story_segments)):
            seg_scenes = segment_results.get(seg_idx, [])
            segment_id = seg_idx + 1  # Segment 1, 2, 3...
            for scene in seg_scenes:
                scene["scene_id"] = scene_id_counter
                scene["segment_id"] = segment_id  # LƯU segment_id để biết scene thuộc segment nào
                # Normalize IDs
                raw_chars = scene.get("characters_used", "")
                raw_loc = scene.get("location_used", "")
                scene["characters_used"] = self._normalize_character_ids(raw_chars, valid_char_ids)
                scene["location_used"] = self._normalize_location_id(raw_loc, valid_loc_ids)
                all_scenes.append(scene)
                scene_id_counter += 1

        # Verify total scene count
        if len(all_scenes) != total_planned_images:
            self._log(f"  Note: Created {len(all_scenes)} scenes (planned: {total_planned_images})")

        if not all_scenes:
            self._log("  ERROR: No scenes created!", "ERROR")
            return StepResult("create_director_plan_basic", StepStatus.FAILED, "No scenes created")

        # =====================================================================
        # POST-PROCESSING: Fill any SRT gaps to ensure 100% coverage
        # This catches cases where API didn't return proper srt_indices
        # =====================================================================
        all_covered_indices = set()
        for scene in all_scenes:
            indices = scene.get("srt_indices", [])
            if isinstance(indices, list):
                all_covered_indices.update(indices)

        all_srt_indices = set(range(1, len(srt_entries) + 1))
        uncovered = sorted(all_srt_indices - all_covered_indices)

        if uncovered:
            self._log(f"\n  [GAP-FILL] Found {len(uncovered)} uncovered SRT entries, creating fill scenes...")

            # Group consecutive indices into chunks
            chunks = []
            if uncovered:
                current_chunk = [uncovered[0]]
                for idx in uncovered[1:]:
                    if idx == current_chunk[-1] + 1:
                        current_chunk.append(idx)
                    else:
                        chunks.append(current_chunk)
                        current_chunk = [idx]
                chunks.append(current_chunk)

            # Create fill scenes for each chunk (max 10 SRT per scene)
            for chunk in chunks:
                # Split large chunks into smaller scenes
                chunk_start = 0
                while chunk_start < len(chunk):
                    chunk_end = min(chunk_start + 10, len(chunk))
                    scene_indices = chunk[chunk_start:chunk_end]

                    scene_ents = [e for idx, e in enumerate(srt_entries, 1) if idx in scene_indices]
                    if scene_ents:
                        fill_scene = {
                            "scene_id": scene_id_counter,
                            "segment_id": 0,  # Gap-fill scenes don't belong to specific segment
                            "srt_indices": scene_indices,
                            "srt_start": scene_ents[0].start_time,
                            "srt_end": scene_ents[-1].end_time,
                            "duration": 5.0,  # Default 5s
                            "srt_text": " ".join([e.text for e in scene_ents]),
                            "visual_moment": f"[Gap-fill] Scene covering SRT {scene_indices[0]}-{scene_indices[-1]}",
                            "characters_used": "",
                            "location_used": "",
                            "camera": "Medium shot",
                            "lighting": "Natural lighting"
                        }
                        all_scenes.append(fill_scene)
                        scene_id_counter += 1

                    chunk_start = chunk_end

            self._log(f"  [GAP-FILL] Added fill scenes, total: {len(all_scenes)}")

        # =====================================================================
        # v1.0.413: SORT scenes theo timeline (srt_start) rồi reassign scene_id
        # Fix: gap-fill scenes bị đặt cuối thay vì đúng vị trí timeline
        # =====================================================================
        def _parse_time_for_sort(ts):
            """Parse timestamp string thành seconds để sort."""
            if not ts:
                return 999999  # Đẩy scenes không có timestamp xuống cuối
            try:
                ts_str = str(ts).replace(',', ':')
                parts = ts_str.split(':')
                if len(parts) >= 4:
                    return int(parts[0])*3600 + int(parts[1])*60 + int(parts[2]) + int(parts[3])/1000
                elif len(parts) == 3:
                    # H:MM:SS or H:MM:SS.ffffff
                    h, m, s = parts
                    return int(h)*3600 + int(m)*60 + float(s)
                else:
                    return float(ts_str)
            except:
                return 999999

        all_scenes.sort(key=lambda s: _parse_time_for_sort(s.get("srt_start", "")))

        # Reassign scene_id theo thứ tự timeline mới
        for new_id, scene in enumerate(all_scenes, 1):
            scene["scene_id"] = new_id

        self._log(f"  [SORT] Sorted {len(all_scenes)} scenes by srt_start, reassigned scene_ids 1-{len(all_scenes)}")

        # =====================================================================
        # v1.0.441: DURATION ENFORCEMENT - Content-based duration from SRT
        # 1. Tính duration thực từ SRT timestamps
        # 2. SPLIT scenes quá dài (>max_dur) thành nhiều scenes nhỏ
        # 3. MERGE scenes quá ngắn (<min_dur) với scene kế bên (multi-pass)
        # v1.0.442: SKIP SPLIT/MERGE khi excel_mode=small (giữ nguyên key moments)
        # =====================================================================
        min_dur = self.config.get("min_scene_duration", 4)
        max_dur = self.config.get("max_scene_duration", 8)
        _excel_mode = self.config.get("excel_mode", "full").lower()
        _skip_duration_enforcement = (_excel_mode == "small")

        def _calc_actual_duration(scene):
            """Tính duration thực từ srt_start/srt_end timestamps."""
            s_start = _parse_time_for_sort(scene.get("srt_start", ""))
            s_end = _parse_time_for_sort(scene.get("srt_end", ""))
            if s_start < 999999 and s_end < 999999 and s_end > s_start:
                return s_end - s_start
            return scene.get("duration", 5.0)

        def _merge_two_scenes(a, b):
            """Gộp 2 scenes liên tiếp thành 1."""
            merged = {
                "scene_id": a["scene_id"],
                "segment_id": a.get("segment_id", b.get("segment_id", 0)),
                "srt_start": a.get("srt_start", ""),
                "srt_end": b.get("srt_end", a.get("srt_end", "")),
                "duration": round(a.get("duration", 0) + b.get("duration", 0), 2),
                "srt_text": (a.get("srt_text", "") + " " + b.get("srt_text", "")).strip(),
                "visual_moment": a.get("visual_moment", "") or b.get("visual_moment", ""),
                "characters_used": a.get("characters_used", "") or b.get("characters_used", ""),
                "location_used": a.get("location_used", "") or b.get("location_used", ""),
                "camera": a.get("camera", "") or b.get("camera", ""),
                "lighting": a.get("lighting", "") or b.get("lighting", ""),
            }
            idx1 = a.get("srt_indices", [])
            idx2 = b.get("srt_indices", [])
            if isinstance(idx1, list) and isinstance(idx2, list):
                merged["srt_indices"] = sorted(set(idx1 + idx2))
            else:
                merged["srt_indices"] = idx1 or idx2
            return merged

        # Cập nhật duration thực cho tất cả scenes
        for scene in all_scenes:
            scene["duration"] = round(_calc_actual_duration(scene), 2)

        # --- PHASE 1: SPLIT scenes quá dài ---
        # v1.0.442: Skip SPLIT khi small mode (key moments cần giữ nguyên duration dài)
        if _skip_duration_enforcement:
            self._log(f"  [SMALL MODE] Skip SPLIT/MERGE - giữ nguyên {len(all_scenes)} key moment scenes")

        if not _skip_duration_enforcement:
            split_count = 0
            split_result = []
            for scene in all_scenes:
                dur = scene.get("duration", 0)
                if dur > max_dur:
                    # Tính số scenes cần chia
                    n_parts = max(2, int(dur / max_dur) + (1 if dur % max_dur > 0 else 0))
                    indices = scene.get("srt_indices", [])
                    if isinstance(indices, list) and len(indices) >= n_parts:
                        # Chia đều srt_indices
                        chunk_size = max(1, len(indices) // n_parts)
                        for p in range(n_parts):
                            start_i = p * chunk_size
                            end_i = min((p + 1) * chunk_size, len(indices)) if p < n_parts - 1 else len(indices)
                            sub_indices = indices[start_i:end_i]
                            if not sub_indices:
                                continue
                            sub_entries = [e for idx, e in enumerate(srt_entries, 1) if idx in sub_indices]
                            sub_scene = dict(scene)  # shallow copy
                            sub_scene["srt_indices"] = sub_indices
                            if sub_entries:
                                sub_scene["srt_start"] = sub_entries[0].start_time
                                sub_scene["srt_end"] = sub_entries[-1].end_time
                                sub_scene["srt_text"] = " ".join([e.text for e in sub_entries])
                                sub_scene["duration"] = round(_calc_actual_duration(sub_scene), 2)
                            else:
                                sub_scene["duration"] = round(dur / n_parts, 2)
                            split_result.append(sub_scene)
                        split_count += 1
                    else:
                        split_result.append(scene)
                else:
                    split_result.append(scene)

            if split_count > 0:
                self._log(f"  [SPLIT] Split {split_count} long scenes (>{max_dur}s), {len(all_scenes)} → {len(split_result)} scenes")
                all_scenes = split_result

            # --- PHASE 2: MERGE scenes quá ngắn (multi-pass) ---
            total_merge_count = 0
            for pass_num in range(5):  # Max 5 passes
                short_count = sum(1 for s in all_scenes if s.get("duration", 0) < min_dur)
                if short_count == 0:
                    break

                merged_scenes = []
                i = 0
                pass_merges = 0
                skip_next = False

                while i < len(all_scenes):
                    if skip_next:
                        skip_next = False
                        i += 1
                        continue

                    current = all_scenes[i]
                    cur_dur = current.get("duration", 0)

                    if cur_dur >= min_dur:
                        merged_scenes.append(current)
                        i += 1
                        continue

                    # Scene quá ngắn - thử gộp
                    merged = False

                    # Thử gộp với scene SAU
                    if i + 1 < len(all_scenes):
                        next_scene = all_scenes[i + 1]
                        combined = cur_dur + next_scene.get("duration", 0)
                        if combined <= max_dur:
                            merged_scenes.append(_merge_two_scenes(current, next_scene))
                            pass_merges += 1
                            skip_next = True
                            merged = True

                    # Nếu không gộp được với SAU, thử gộp với TRƯỚC
                    if not merged and merged_scenes:
                        prev = merged_scenes[-1]
                        combined = prev.get("duration", 0) + cur_dur
                        if combined <= max_dur:
                            merged_scenes[-1] = _merge_two_scenes(prev, current)
                            pass_merges += 1
                            merged = True

                    if not merged:
                        merged_scenes.append(current)

                    i += 1

                total_merge_count += pass_merges
                all_scenes = merged_scenes

                if pass_merges == 0:
                    break  # Không merge được nữa

            if total_merge_count > 0:
                self._log(f"  [MERGE] Merged {total_merge_count} short scenes (<{min_dur}s) in {pass_num + 1} passes")

        # Reassign scene_id
        for new_id, scene in enumerate(all_scenes, 1):
            scene["scene_id"] = new_id

        # Log duration stats
        durations = [s.get("duration", 0) for s in all_scenes]
        under_min = sum(1 for d in durations if d < min_dur)
        over_max = sum(1 for d in durations if d > max_dur)
        self._log(f"  [DURATION] Total: {len(all_scenes)} scenes, min={min(durations):.1f}s, max={max(durations):.1f}s, avg={sum(durations)/len(durations):.1f}s")
        if under_min > 0:
            self._log(f"  [DURATION] Still {under_min} scenes under {min_dur}s (cannot merge without exceeding {max_dur}s)")
        if over_max > 0:
            self._log(f"  [DURATION] {over_max} scenes over {max_dur}s")

        # Save to Excel
        try:
            workbook.save_director_plan(all_scenes)
            workbook.save()
            self._log(f"  -> Saved {len(all_scenes)} scenes to director_plan")
            self._log(f"     Total duration: {sum(s.get('duration', 0) for s in all_scenes):.1f}s")

            # TRACKING: Cập nhật và kiểm tra coverage
            coverage = workbook.update_srt_coverage_scenes(all_scenes)
            self._log(f"\n  [STATS] SRT COVERAGE (sau Step 4 BASIC):")
            self._log(f"     Total SRT: {coverage['total_srt']}")
            self._log(f"     Covered by scenes: {coverage['covered_by_scene']} ({coverage['coverage_percent']}%)")
            if coverage['uncovered'] > 0:
                self._log(f"     [WARN] UNCOVERED: {coverage['uncovered']} entries", "WARN")
                uncovered_list = workbook.get_uncovered_srt_entries()
                if uncovered_list:
                    self._log(f"     Missing SRT: {[u['srt_index'] for u in uncovered_list[:10]]}...")

            # v1.0.89: Update step_5 status in Excel (was missing!)
            total_duration = sum(s.get('duration', 0) for s in all_scenes)
            workbook.update_step_status("step_5", "COMPLETED",
                coverage['total_srt'], coverage['covered_by_scene'],
                f"{len(all_scenes)} scenes, {total_duration:.0f}s")
            workbook.save()

            return StepResult("create_director_plan_basic", StepStatus.COMPLETED, "Success", {"scenes": all_scenes})
        except Exception as e:
            self._log(f"  ERROR: Could not save to Excel: {e}", "ERROR")
            workbook.update_step_status("step_5", "ERROR", 0, 0, str(e)[:80])
            return StepResult("create_director_plan_basic", StepStatus.FAILED, str(e))

    # =========================================================================
    # STEP 6: LÊN KẾ HOẠCH CHI TIẾT TỪNG SCENE
    # =========================================================================

    def step_plan_scenes(
        self,
        project_dir: Path,
        code: str,
        workbook: PromptWorkbook,
    ) -> StepResult:
        """
        Step 4.5: Lên kế hoạch chi tiết cho từng scene TRƯỚC KHI viết prompt.

        Mục đích: Xác định ý đồ nghệ thuật cho mỗi scene
        - Scene này muốn truyền tải gì?
        - Góc máy nên thế nào?
        - Nhân vật đang làm gì, cảm xúc ra sao?
        - Ánh sáng, màu sắc, mood?

        Input: director_plan, story_segments, characters, locations
        Output: scene_planning sheet
        """
        import time
        step_start = time.time()

        self._log("\n" + "="*60)
        self._log("[STEP 6/7] Lên kế hoạch chi tiết từng scene...")
        self._log("="*60)

        # Check if already done
        try:
            existing = workbook.get_scene_planning()
            if existing and len(existing) > 0:
                self._log(f"  -> Đã có {len(existing)} scene plans, skip!")
                workbook.update_step_status("step_6", "COMPLETED", len(existing), len(existing), "Already done")
                return StepResult("plan_scenes", StepStatus.COMPLETED, "Already done")
        except:
            pass

        # Read director plan
        director_plan = workbook.get_director_plan()
        if not director_plan:
            self._log("  ERROR: No director plan! Run step 4 first.", "ERROR")
            return StepResult("plan_scenes", StepStatus.FAILED, "No director plan")

        # Read context
        story_analysis = workbook.get_story_analysis() or {}
        story_segments = workbook.get_story_segments() or []
        characters = workbook.get_characters()
        locations = workbook.get_locations()

        context_lock = story_analysis.get("context_lock", "")

        # Build character info
        char_info = "\n".join([f"- {c.id}: {c.character_lock}" for c in characters if c.character_lock])
        loc_info = "\n".join([f"- {loc.id}: {loc.location_lock}" for loc in locations if hasattr(loc, 'location_lock') and loc.location_lock])

        # Build segments info
        segments_info = ""
        for seg in story_segments:
            segments_info += f"- Segment {seg.get('segment_id')}: {seg.get('segment_name')} ({seg.get('message', '')[:100]})\n"

        self._log(f"  Director plan: {len(director_plan)} scenes")
        self._log(f"  Story segments: {len(story_segments)}")

        # Process in batches - PARALLEL processing
        BATCH_SIZE = 15
        MAX_PARALLEL = self.config.get("max_parallel_api", 6)  # From settings.yaml
        all_plans = []

        # Prepare all batches
        batches = []
        for batch_start in range(0, len(director_plan), BATCH_SIZE):
            batch = director_plan[batch_start:batch_start + BATCH_SIZE]
            batch_num = batch_start // BATCH_SIZE + 1
            batches.append((batch_num, batch_start, batch))

        total_batches = len(batches)
        self._log(f"  Processing {total_batches} batches in parallel (max {MAX_PARALLEL} concurrent)")

        def process_single_batch(batch_info):
            """Process a single batch - called in parallel"""
            batch_num, batch_start, batch = batch_info

            # Format scenes for prompt
            scenes_text = ""
            for scene in batch:
                scenes_text += f"""
Scene {scene.get('scene_id')}:
- Time: {scene.get('srt_start')} → {scene.get('srt_end')} ({scene.get('duration', 0):.1f}s)
- Text: {(scene.get('srt_text') or '')[:200]}
- Visual moment: {scene.get('visual_moment') or ''}
- Characters: {scene.get('characters_used') or ''}
- Location: {scene.get('location_used') or ''}
"""

            # v1.0.431: dung topic_prompts
            prompt = self.topic_prompts.step6_scene_planning(
                context_lock, segments_info, char_info, loc_info, scenes_text
            )

            # Call API with retry logic
            MAX_RETRIES = 3
            data = None

            for retry in range(MAX_RETRIES):
                response = self._call_api(prompt, temperature=0.4, max_tokens=8192)
                if not response:
                    time.sleep(2 ** retry)  # Exponential backoff
                    continue

                # Parse response
                data = self._extract_json(response)
                if data and "scene_plans" in data:
                    break  # Success!
                else:
                    time.sleep(2 ** retry)

            if not data or "scene_plans" not in data:
                # Fallback: create basic plans for this batch
                fallback_plans = []
                for scene in batch:
                    fallback_plan = {
                        "scene_id": scene.get("scene_id"),
                        "artistic_intent": f"Convey the moment: {(scene.get('visual_moment') or '')[:100]}",
                        "shot_type": scene.get("camera") or "Medium shot",
                        "character_action": "As described in visual moment",
                        "mood": "Matches the narration tone",
                        "lighting": scene.get("lighting", "Natural lighting"),
                        "color_palette": "Neutral tones",
                        "key_focus": "Main subject of the scene"
                    }
                    fallback_plans.append(fallback_plan)
                return (batch_num, fallback_plans, True)  # True = fallback used

            return (batch_num, data["scene_plans"], False)  # False = API success

        # Execute batches in parallel
        batch_results = {}
        with ThreadPoolExecutor(max_workers=MAX_PARALLEL) as executor:
            future_to_batch = {executor.submit(process_single_batch, b): b[0] for b in batches}

            for future in as_completed(future_to_batch):
                batch_num = future_to_batch[future]
                try:
                    result_batch_num, plans, used_fallback = future.result()
                    batch_results[result_batch_num] = plans
                    status = "fallback" if used_fallback else "OK"
                    self._log(f"     Batch {result_batch_num}/{total_batches}: {len(plans)} plans [{status}]")
                except Exception as e:
                    self._log(f"     Batch {batch_num} error: {e}", "ERROR")
                    batch_results[batch_num] = []

        # Combine results in order
        for batch_num in sorted(batch_results.keys()):
            all_plans.extend(batch_results[batch_num])

        if not all_plans:
            self._log("  ERROR: No scene plans created!", "ERROR")
            return StepResult("plan_scenes", StepStatus.FAILED, "No plans created")

        # Save to Excel
        try:
            workbook.save_scene_planning(all_plans)
            workbook.save()
            self._log(f"  -> Saved {len(all_plans)} scene plans to Excel")

            # Update step status with duration
            elapsed = int(time.time() - step_start)
            workbook.update_step_status("step_6", "COMPLETED", len(all_plans), len(all_plans),
                f"{elapsed}s - {len(all_plans)} plans")

            return StepResult("plan_scenes", StepStatus.COMPLETED, "Success", {"plans": all_plans})
        except Exception as e:
            self._log(f"  ERROR: Could not save: {e}", "ERROR")
            elapsed = int(time.time() - step_start)
            workbook.update_step_status("step_6", "ERROR", 0, 0, f"{elapsed}s - {str(e)[:80]}")
            return StepResult("plan_scenes", StepStatus.FAILED, str(e))

    # =========================================================================
    # STEP 7: TẠO SCENE PROMPTS (BATCH)
    # =========================================================================

    def step_create_scene_prompts(
        self,
        project_dir: Path,
        code: str,
        workbook: PromptWorkbook,
        batch_size: int = 10
    ) -> StepResult:
        """
        Step 5: Tạo prompts cho từng scene (theo batch).

        Input: Đọc director_plan, characters, locations từ Excel
        Output: Thêm scenes vào sheet scenes
        """
        import time
        step_start = time.time()

        self._log("\n" + "="*60)
        self._log("[STEP 7/7] Tạo scene prompts...")
        self._log("="*60)

        # Read director plan
        try:
            director_plan = workbook.get_director_plan()
            if not director_plan:
                self._log("  ERROR: No director plan found! Run step 4 first.", "ERROR")
                return StepResult("create_scene_prompts", StepStatus.FAILED, "No director plan")
        except Exception as e:
            self._log(f"  ERROR: Could not read director plan: {e}", "ERROR")
            return StepResult("create_scene_prompts", StepStatus.FAILED, str(e))

        # Check existing scenes
        existing_scenes = workbook.get_scenes()
        existing_ids = {s.scene_id for s in existing_scenes} if existing_scenes else set()

        # Find scenes that need prompts
        pending_scenes = [s for s in director_plan if s.get("scene_id") not in existing_ids]

        if not pending_scenes:
            self._log(f"  -> Đã có {len(existing_scenes)} scenes, skip!")
            workbook.update_step_status("step_7", "COMPLETED", len(existing_scenes), len(existing_scenes), "Already done")
            workbook.save()  # v1.0.81: SAVE ngay sau update!
            self._log(f"  [SAVED] step_7 = COMPLETED ({len(existing_scenes)} scenes)")
            return StepResult("create_scene_prompts", StepStatus.COMPLETED, "Already done")

        self._log(f"  -> Cần tạo prompts cho {len(pending_scenes)} scenes...")

        # Read context
        story_analysis = {}
        try:
            story_analysis = workbook.get_story_analysis() or {}
        except:
            pass

        characters = workbook.get_characters()
        locations = workbook.get_locations()

        # Đọc scene planning (kế hoạch chi tiết từ step 4.5)
        scene_planning = {}
        try:
            plans = workbook.get_scene_planning() or []
            for plan in plans:
                scene_planning[plan.get("scene_id")] = plan
            self._log(f"  Loaded {len(scene_planning)} scene plans from step 4.5")
        except:
            pass

        context_lock = story_analysis.get("context_lock", "")

        # Build character/location lookup - bao gồm cả image_file cho reference
        char_lookup = {}
        char_image_lookup = {}  # id -> image_file (nvc.png, nvp1.png...)
        for c in characters:
            if c.character_lock:
                char_lookup[c.id] = c.character_lock
            # Lấy image_file, mặc định là {id}.png
            img_file = c.image_file if c.image_file else f"{c.id}.png"
            char_image_lookup[c.id] = img_file

        loc_lookup = {}
        loc_image_lookup = {}  # id -> image_file (loc_xxx.png)
        for loc in locations:
            if hasattr(loc, 'location_lock') and loc.location_lock:
                loc_lookup[loc.id] = loc.location_lock
            # Lấy image_file, mặc định là {id}.png
            img_file = loc.image_file if hasattr(loc, 'image_file') and loc.image_file else f"{loc.id}.png"
            loc_image_lookup[loc.id] = img_file

        # Process in batches - PARALLEL API calls
        total_created = 0
        MAX_PARALLEL = self.config.get("max_parallel_api", 6)  # From settings.yaml

        # Prepare all batches
        all_batches = []
        for batch_start in range(0, len(pending_scenes), batch_size):
            batch = pending_scenes[batch_start:batch_start + batch_size]
            batch_num = batch_start // batch_size + 1
            all_batches.append((batch_num, batch))

        total_batches = len(all_batches)
        self._log(f"  Processing {total_batches} batches in parallel (max {MAX_PARALLEL} concurrent)")

        def process_single_batch(batch_info):
            """Process a single batch - called in parallel"""
            batch_num, batch = batch_info

            # Build scenes text for prompt
            scenes_text = ""
            for scene in batch:
                char_ids = [cid.strip() for cid in (scene.get("characters_used") or "").split(",") if cid.strip()]
                char_desc_parts = []
                char_refs = []
                for cid in char_ids:
                    desc = char_lookup.get(cid, cid)
                    img = char_image_lookup.get(cid, f"{cid}.png")
                    char_desc_parts.append(f"{desc} ({img})")
                    char_refs.append(img)
                char_desc = ", ".join(char_desc_parts)

                loc_id = scene.get("location_used") or ""
                loc_desc = loc_lookup.get(loc_id, loc_id)
                loc_img = loc_image_lookup.get(loc_id, f"{loc_id}.png") if loc_id else ""
                if loc_desc and loc_img:
                    loc_desc = f"{loc_desc} ({loc_img})"

                scene_id = scene.get('scene_id')
                plan = scene_planning.get(scene_id, {})
                plan_info = ""
                if plan:
                    plan_info = f"""
- [ARTISTIC PLAN from Step 4.5]:
  * Intent: {plan.get('artistic_intent') or ''}
  * Shot type: {plan.get('shot_type') or ''}
  * Action: {plan.get('character_action') or ''}
  * Mood: {plan.get('mood') or ''}
  * Lighting: {plan.get('lighting') or ''}
  * Colors: {plan.get('color_palette') or ''}
  * Focus: {plan.get('key_focus') or ''}"""

                scenes_text += f"""
Scene {scene_id}:
- Time: {scene.get('srt_start')} --> {scene.get('srt_end')}
- Text: {scene.get('srt_text') or ''}
- Visual moment: {scene.get('visual_moment') or ''}
- Characters: {char_desc}
- Location: {loc_desc}
- Camera: {scene.get('camera') or ''}
- Lighting: {scene.get('lighting') or ''}
- Reference files: {', '.join(char_refs + ([loc_img] if loc_img else []))}
{plan_info}
"""

            # v1.0.431: dung topic_prompts
            prompt = self.topic_prompts.step7_scene_prompts(
                context_lock, scenes_text, len(batch)
            )

            # Call API with retry
            MAX_RETRIES = 3
            for retry in range(MAX_RETRIES):
                response = self._call_api(prompt, temperature=0.5, max_tokens=8192)
                if response:
                    data = self._extract_json(response)
                    if data and "scenes" in data:
                        return (batch_num, batch, data["scenes"], None)  # Success
                time.sleep(2 ** retry)

            return (batch_num, batch, None, "API failed")  # Failed

        # Execute batches in parallel
        batch_results = {}
        with ThreadPoolExecutor(max_workers=MAX_PARALLEL) as executor:
            future_to_batch = {executor.submit(process_single_batch, b): b[0] for b in all_batches}

            for future in as_completed(future_to_batch):
                batch_num = future_to_batch[future]
                try:
                    result = future.result()
                    batch_results[result[0]] = result  # Store by batch_num
                    status = "OK" if result[2] else "FAILED"
                    self._log(f"     Batch {result[0]}/{total_batches}: [{status}]")
                except Exception as e:
                    self._log(f"     Batch {batch_num} error: {e}", "ERROR")

        # v1.0.392: Thu thập TẤT CẢ scenes vào memory → sort theo scene_id → save 1 LẦN
        # Tránh file lock contention khi Chrome workers đọc Excel cùng lúc
        import re
        all_scenes_to_add = []  # List of (scene_id_int, Scene) tuples

        for batch_num in sorted(batch_results.keys()):
            _, batch, api_scenes, error = batch_results[batch_num]

            if not api_scenes:
                self._log(f"  Batch {batch_num}: skipped ({error})", "WARNING")
                continue

            # Validate và tạo fallback cho scenes thiếu
            if len(api_scenes) < len(batch):
                self._log(f"  [WARN] Batch {batch_num}: API returned {len(api_scenes)}, expected {len(batch)} - ADDING MISSING")

                # Tìm scene_ids đã có từ API
                api_scene_ids = {int(s.get("scene_id", 0)) for s in api_scenes}

                # Tạo fallback cho scenes thiếu
                for original in batch:
                    orig_id = int(original.get("scene_id", 0))
                    if orig_id not in api_scene_ids:
                        srt_text = original.get("srt_text") or ""
                        visual_moment = original.get("visual_moment") or ""
                        chars_used = original.get("characters_used") or ""
                        loc_used = original.get("location_used") or ""

                        fallback_prompt = f"Scene: {visual_moment or srt_text[:200]}. "
                        fallback_prompt += self.topic_prompts.fallback_style()

                        if chars_used:
                            for cid in chars_used.split(","):
                                cid = cid.strip()
                                if cid:
                                    img = char_image_lookup.get(cid, f"{cid}.png")
                                    fallback_prompt += f" ({img})"
                        if loc_used:
                            img = loc_image_lookup.get(loc_used, f"{loc_used}.png")
                            fallback_prompt += f" (reference: {img})"

                        fallback_scene = {
                            "scene_id": orig_id,
                            "img_prompt": fallback_prompt,
                            "video_prompt": f"Smooth camera movement: {visual_moment or srt_text[:100]}"
                        }
                        api_scenes.append(fallback_scene)
                        self._log(f"     -> Created fallback for scene {orig_id}")

            # Check duplicates - nếu >80% trùng lặp, tạo unique fallback thay vì skip
            seen_prompts = {}
            duplicate_count = 0
            for s in api_scenes:
                prompt_key = s.get("img_prompt", "")[:100]
                if prompt_key in seen_prompts:
                    duplicate_count += 1
                else:
                    seen_prompts[prompt_key] = True

            if len(api_scenes) > 0 and duplicate_count > len(api_scenes) * 0.8:
                self._log(f"  Batch {batch_num}: >80% duplicates ({duplicate_count}/{len(api_scenes)}), creating UNIQUE fallbacks!", "WARN")

                seen_for_dedup = set()
                for i, scene_data in enumerate(api_scenes):
                    prompt_key = scene_data.get("img_prompt", "")[:100]
                    scene_id = scene_data.get("scene_id", i)

                    if prompt_key in seen_for_dedup:
                        orig = next((s for s in batch if int(s.get("scene_id", 0)) == int(scene_id)), None)
                        if orig:
                            srt_text = (orig.get("srt_text") or "")[:150]
                            visual = (orig.get("visual_moment") or "")[:100]
                            chars_used = orig.get("characters_used") or ""
                            loc_used = orig.get("location_used") or ""

                            unique_prompt = f"Scene {scene_id}: {visual or srt_text}. "
                            unique_prompt += self.topic_prompts.fallback_style()

                            if chars_used:
                                for cid in chars_used.split(","):
                                    cid = cid.strip()
                                    if cid:
                                        img = char_image_lookup.get(cid, f"{cid}.png")
                                        unique_prompt += f" ({img})"
                            if loc_used:
                                img = loc_image_lookup.get(loc_used, f"{loc_used}.png")
                                unique_prompt += f" (reference: {img})"

                            scene_data["img_prompt"] = unique_prompt
                            self._log(f"     -> Created unique fallback for scene {scene_id}")
                    else:
                        seen_for_dedup.add(prompt_key)

            # v1.0.446: Check video-only mode
            is_video_only = getattr(self.topic_prompts, 'is_video_only', lambda: False)()

            # Build Scene objects in memory (NOT saving yet)
            for scene_data in api_scenes:
                scene_id = int(scene_data.get("scene_id", 0))
                original = next((s for s in batch if int(s.get("scene_id", 0)) == scene_id), None)
                if not original:
                    continue

                img_prompt = scene_data.get("img_prompt", "")
                video_prompt = scene_data.get("video_prompt", "")

                # v1.0.446: Video-only mode - no img_prompt, giữ character reference cho T2V
                if is_video_only:
                    img_prompt = ""  # Video-only: no image prompt
                    # Giữ characters_used từ director_plan (Step 5) - T2V cần biết nhân vật
                    char_ids = [cid.strip() for cid in (original.get("characters_used") or "").split(",") if cid.strip()]
                    loc_id = ""  # Video-only: không cần location reference
                    ref_files = [char_image_lookup.get(cid, f"{cid}.png") for cid in char_ids]
                    chars_used_str = ",".join(char_ids) if char_ids else ""
                    loc_used_str = ""
                else:
                    # v1.0.423: Không ép thêm refs từ Step 5 nữa
                    # Để API Step 7 tự quyết định refs dựa trên nội dung scene
                    # Chỉ lấy từ original làm fallback nếu prompt không có refs
                    char_ids = [cid.strip() for cid in (original.get("characters_used") or "").split(",") if cid.strip()]
                    loc_id = original.get("location_used") or ""

                    # Parse prompt to extract ACTUAL character/location IDs used
                    # v1.0.423: Regex match cả ID có chữ (nvc, nvp1, loc_office...)
                    char_pattern = r'\(([nN][vV][_a-zA-Z0-9]+)\.png\)'
                    prompt_char_matches = re.findall(char_pattern, img_prompt)
                    if prompt_char_matches:
                        # Lọc bỏ những match có "loc" (tránh nhầm)
                        char_ids = list(set(m for m in prompt_char_matches if not m.lower().startswith('loc')))

                    loc_pattern = r'\(([lL][oO][cC][_a-zA-Z0-9]+)\.png\)'
                    prompt_loc_matches = re.findall(loc_pattern, img_prompt)
                    if prompt_loc_matches:
                        loc_id = prompt_loc_matches[0]  # Chỉ 1 location

                    ref_files = [char_image_lookup.get(cid, f"{cid}.png") for cid in char_ids]
                    if loc_id:
                        ref_files.append(loc_image_lookup.get(loc_id, f"{loc_id}.png"))

                    chars_used_str = ",".join(char_ids) if char_ids else ""
                    loc_used_str = loc_id if loc_id else ""

                video_note = ""
                excel_mode = self.config.get("excel_mode", "full").lower()
                segment_id = original.get("segment_id", 1)
                if excel_mode == "basic" and segment_id > 1:
                    video_note = "SKIP"

                srt_start = original.get("srt_start", "")
                srt_end = original.get("srt_end", "")
                planned_duration = calc_planned_duration(srt_start, srt_end)

                scene = Scene(
                    scene_id=scene_id,
                    srt_start=srt_start,
                    srt_end=srt_end,
                    duration=original.get("duration", 0),
                    planned_duration=planned_duration,
                    srt_text=original.get("srt_text", ""),
                    img_prompt=img_prompt,
                    video_prompt=video_prompt,
                    characters_used=chars_used_str,
                    location_used=loc_used_str,
                    reference_files=json.dumps(ref_files) if ref_files else "",
                    status_img="skip" if is_video_only else "pending",
                    status_vid="pending",
                    video_note=video_note,
                    segment_id=segment_id
                )
                all_scenes_to_add.append((scene_id, scene))
                total_created += 1

        # v1.0.392: Sort theo scene_id rồi add vào workbook → save 1 LẦN DUY NHẤT
        all_scenes_to_add.sort(key=lambda x: x[0])
        self._log(f"\n  -> Collected {total_created} scenes, sorting by scene_id and saving...")

        try:
            for _, scene in all_scenes_to_add:
                workbook.add_scene(scene)
            workbook.save()
            self._log(f"  -> [SAVED] {total_created} scenes to Excel (sorted by scene_id, 1 save)")
        except Exception as e:
            self._log(f"  [ERROR] Save failed: {e} - retrying...", "ERROR")
            time.sleep(5)
            try:
                workbook.save()
                self._log(f"  -> [SAVED] Retry success!")
            except Exception as e2:
                self._log(f"  [CRITICAL] Save retry failed: {e2}", "ERROR")

        # v1.0.81: ĐƠN GIẢN - Sau Step 7 xong → update status → SAVE
        elapsed = int(time.time() - step_start)
        total_in_plan = len(director_plan)
        final_scene_count = len(existing_ids) + total_created

        if final_scene_count > 0:
            # Có scenes → COMPLETED
            workbook.update_step_status("step_7", "COMPLETED", final_scene_count, total_in_plan,
                f"{elapsed}s - {final_scene_count} scenes")
            workbook.save()  # QUAN TRỌNG: SAVE ngay sau khi update status!
            self._log(f"  [SAVED] step_7 = COMPLETED ({final_scene_count}/{total_in_plan} scenes)")
            return StepResult("create_scene_prompts", StepStatus.COMPLETED, f"{final_scene_count} scenes")
        else:
            # Không có scenes → ERROR
            workbook.update_step_status("step_7", "ERROR", 0, total_in_plan, f"{elapsed}s - No scenes")
            workbook.save()
            return StepResult("create_scene_prompts", StepStatus.FAILED, "No scenes created")

    # =========================================================================
    # MAIN: RUN ALL STEPS
    # =========================================================================

    def run_all_steps(
        self,
        project_dir: Path,
        code: str,
        log_callback: Callable = None
    ) -> bool:
        """
        Chạy tất cả steps theo thứ tự.
        Mỗi step kiểm tra xem đã xong chưa, nếu xong thì skip.

        Returns:
            True nếu thành công (tất cả steps completed)
        """
        self.log_callback = log_callback
        project_dir = Path(project_dir)

        self._log("\n" + "="*70)
        self._log("  PROGRESSIVE PROMPTS GENERATOR")
        self._log("  Mỗi step lưu vào Excel, có thể resume nếu fail")
        self._log("="*70)

        # Paths
        srt_path = project_dir / f"{code}.srt"
        txt_path = project_dir / f"{code}.txt"
        excel_path = project_dir / f"{code}_prompts.xlsx"

        if not srt_path.exists():
            self._log(f"ERROR: SRT not found: {srt_path}", "ERROR")
            return False

        # v1.0.318: GUARD - Nếu đã có ảnh → Excel đã xong → KHÔNG chạy lại
        _img_dir = project_dir / "img"
        if _img_dir.exists():
            _img_count = len(list(_img_dir.glob("*.png"))) + len(list(_img_dir.glob("*.jpg")))
            if _img_count > 0:
                self._log(f"\n  [GUARD] Đã có {_img_count} ảnh trong img/ - Excel đã hoàn thành!")
                self._log("  → KHÔNG chạy lại Excel. Return True.")
                return True

        # Parse SRT
        srt_entries = parse_srt_file(srt_path)
        if not srt_entries:
            self._log("ERROR: No SRT entries found!", "ERROR")
            return False

        self._log(f"  SRT: {len(srt_entries)} entries")

        # Read TXT if exists
        txt_content = ""
        if txt_path.exists():
            try:
                txt_content = txt_path.read_text(encoding='utf-8')
                self._log(f"  TXT: {len(txt_content)} chars")
            except:
                pass

        # Load/create workbook
        workbook = PromptWorkbook(excel_path).load_or_create()

        # v1.0.79: EARLY CHECK - Nếu Excel đã có scenes thì chỉ fix status, không chạy lại
        # Fix bug: Máy cập nhật lên v1.0.78 nhưng Excel cũ có step_7 chưa COMPLETED
        #          → Tool chạy lại toàn bộ 7 steps thay vì chỉ fix status
        # v1.0.82: EARLY CHECK dựa vào TRẠNG THÁI 7 BƯỚC, không phải scenes
        # Logic đúng: Nếu step_7 = COMPLETED thì skip, không cần check scenes
        # v1.0.88: Khi skip, đánh dấu TẤT CẢ steps COMPLETED để không bị pick up lại
        try:
            step7_status = workbook.get_step_status("step_7")
            if step7_status.get("status") == "COMPLETED":
                existing_scenes = workbook.get_scenes() or []
                self._log(f"\n  [EARLY CHECK] step_7 = COMPLETED ({len(existing_scenes)} scenes)")

                # v1.0.88: Fix all steps to COMPLETED to prevent re-picking
                all_steps = ["step_1", "step_1.5", "step_2", "step_3", "step_4", "step_4.5", "step_5", "step_6", "step_7", "step_8"]
                fixed_count = 0
                for step in all_steps:
                    step_status = workbook.get_step_status(step)
                    if step_status.get("status") != "COMPLETED":
                        workbook.update_step_status(step, "COMPLETED", {"fixed_by": "early_check_v1.0.88"})
                        fixed_count += 1
                if fixed_count > 0:
                    workbook.save()
                    self._log(f"  [EARLY CHECK] Fixed {fixed_count} incomplete steps → COMPLETED")

                self._log("  -> Skipping! All 7 steps already done.")
                self._log("\n" + "="*70)
                self._log("  SKIPPED - Excel already complete!")
                self._log("="*70)
                return True
        except Exception as e:
            self._log(f"  [EARLY CHECK] Warning: {e}", "WARN")
            # v1.0.318: Nếu đã có ảnh → KHÔNG chạy lại (dù check lỗi)
            try:
                _img_dir = Path(project_dir) / "img"
                if _img_dir.exists():
                    _img_count = len(list(_img_dir.glob("*.png"))) + len(list(_img_dir.glob("*.jpg")))
                    if _img_count > 0:
                        self._log(f"  [EARLY CHECK] Đã có {_img_count} ảnh - KHÔNG chạy lại Excel!", "WARN")
                        return True
            except Exception:
                pass
            # Continue with normal flow if check fails AND no images

        # Run steps
        all_success = True

        # Step 1: Analyze story
        result = self.step_analyze_story(project_dir, code, workbook, srt_entries, txt_content)
        if result.status == StepStatus.FAILED:
            self._log("Step 1 FAILED! Stopping.", "ERROR")
            return False

        # Step 1.5: Analyze story segments (nội dung con)
        result = self.step_analyze_story_segments(project_dir, code, workbook, srt_entries, txt_content)
        if result.status == StepStatus.FAILED:
            self._log("Step 1.5 FAILED! Stopping.", "ERROR")
            return False

        # Step 2: Create characters
        result = self.step_create_characters(project_dir, code, workbook, srt_entries, txt_content)
        if result.status == StepStatus.FAILED:
            self._log("Step 2 FAILED! Stopping.", "ERROR")
            return False

        # Step 3: Create locations
        result = self.step_create_locations(project_dir, code, workbook, srt_entries, txt_content)
        if result.status == StepStatus.FAILED:
            self._log("Step 3 FAILED! Stopping.", "ERROR")
            return False

        # Step 4: Create director plan (sử dụng segments để guide số lượng scenes)
        result = self.step_create_director_plan(project_dir, code, workbook, srt_entries)
        if result.status == StepStatus.FAILED:
            self._log("Step 4 FAILED! Stopping.", "ERROR")
            return False

        # Step 4.5: Lên kế hoạch chi tiết từng scene (artistic planning)
        result = self.step_plan_scenes(project_dir, code, workbook)
        if result.status == StepStatus.FAILED:
            self._log("Step 4.5 FAILED! Stopping.", "ERROR")
            return False

        # Step 5: Create scene prompts (đọc từ scene planning)
        result = self.step_create_scene_prompts(project_dir, code, workbook)
        if result.status == StepStatus.FAILED:
            self._log("Step 5 FAILED!", "ERROR")
            return False

        # v1.0.78: FINAL CHECK - Đảm bảo step_7 được đánh dấu COMPLETED
        # Fix bug: step_7 không được update sau khi Step 7 hoàn thành
        try:
            scenes = workbook.get_scenes()
            if scenes and len(scenes) > 0:
                step7_status = workbook.get_step_status("step_7")
                if step7_status.get("status") != "COMPLETED":
                    self._log(f"  [FINAL CHECK] Updating step_7 status: {len(scenes)} scenes exist")
                    workbook.update_step_status("step_7", "COMPLETED", len(scenes), len(scenes),
                        "Final check - scenes exist")
        except Exception as e:
            self._log(f"  [FINAL CHECK] Warning: {e}", "WARN")

        # Step 8: Thumbnail prompts
        result = self.step_8_thumbnail_prompts(project_dir, code, workbook)
        if result.status == StepStatus.FAILED:
            self._log("Step 8 (Thumbnail) FAILED! Continuing anyway...", "WARN")
            # Không block - thumbnail là optional, scenes vẫn chạy được

        self._log("\n" + "="*70)
        self._log("  ALL STEPS COMPLETED!")
        self._log("="*70)

        return True

    def step_8_thumbnail_prompts(
        self,
        project_dir: Path,
        code: str,
        workbook: "PromptWorkbook"
    ) -> "StepResult":
        """
        Step 8: Tạo 3 thumbnail prompts thu hút nhất dựa vào nội dung câu chuyện.

        Output: sheet 'thumbnail' trong Excel
        - v1 (portrait_main): Nhân vật chính đẹp/thu hút, người xem muốn hướng tới
        - v2 (dramatic_scene): Cảnh kịch tính/tò mò nhất, nhân vật chính làm trọng tâm
        - v3 (youtube_ctr): Công thức CTR YouTube - click-worthy nhất

        Mỗi prompt có annotation nhân vật/bối cảnh như scenes: (nv1.png), (loc1.png)
        """
        import time
        import re
        step_start = time.time()

        self._log("\n" + "="*60)
        self._log("[STEP 8/8] Tao thumbnail prompts...")
        self._log("="*60)

        # Check if already done
        try:
            step8_status = workbook.get_step_status("step_8")
            if step8_status.get("status") == "COMPLETED":
                existing = workbook.get_thumbnails()
                if existing:
                    self._log(f"  -> Da co {len(existing)} thumbnails, skip!")
                    return StepResult("thumbnail_prompts", StepStatus.COMPLETED, "Already done")
        except Exception:
            pass

        # Collect context
        story_analysis = workbook.get_story_analysis() or {}
        characters = workbook.get_characters()
        locations = workbook.get_locations()

        # Filter: only non-child characters
        main_chars = [c for c in characters if not c.is_child]
        # Find protagonist first
        protagonist = next((c for c in main_chars if c.role in ("main", "protagonist", "lead")), None)
        if not protagonist and main_chars:
            protagonist = main_chars[0]

        if not protagonist:
            self._log("  WARN: Khong tim thay nhan vat chinh, dung char dau tien", "WARN")
            if characters:
                protagonist = characters[0]
            else:
                self._log("  ERROR: Khong co nhan vat nao!", "ERROR")
                workbook.update_step_status("step_8", "ERROR", 0, 3, "No characters found")
                workbook.save()
                return StepResult("thumbnail_prompts", StepStatus.FAILED, "No characters")

        # Build character info for API
        char_info_lines = []
        for c in main_chars[:5]:  # max 5 chars
            char_info_lines.append(
                f"  - {c.id} ({c.name}, role={c.role}): {c.character_lock or c.english_prompt}"
            )
        chars_info = "\n".join(char_info_lines)

        loc_info_lines = []
        for loc in (locations or [])[:5]:
            loc_info_lines.append(f"  - {loc.id} ({loc.name}): {getattr(loc, 'location_lock', '') or getattr(loc, 'english_prompt', '')}")
        locs_info = "\n".join(loc_info_lines) if loc_info_lines else "  (No locations defined)"

        context_lock = story_analysis.get("context_lock", "")
        setting = story_analysis.get("setting", {})
        themes = story_analysis.get("themes", [])
        visual_style = story_analysis.get("visual_style", {})

        # Build available ref IDs for API
        char_ids = [c.id for c in main_chars[:5]]
        loc_ids = [loc.id for loc in (locations or [])[:5]] if locations else []

        prompt = self.topic_prompts.step8_thumbnail(
            setting=setting, themes=themes, visual_style=visual_style,
            context_lock=context_lock, protagonist=protagonist,
            chars_info=chars_info, locs_info=locs_info,
            char_ids=char_ids, loc_ids=loc_ids
        )

        self._log(f"  Calling API for 3 thumbnail prompts...")
        response = self._call_api(prompt, temperature=0.75, max_tokens=4096)
        if not response:
            self._log("  ERROR: API call failed!", "ERROR")
            workbook.update_step_status("step_8", "ERROR", 0, 3, "API failed")
            workbook.save()
            return StepResult("thumbnail_prompts", StepStatus.FAILED, "API failed")

        data = self._extract_json(response)
        if not data or "thumbnails" not in data:
            self._log("  ERROR: JSON parse failed!", "ERROR")
            workbook.update_step_status("step_8", "ERROR", 0, 3, "JSON parse failed")
            workbook.save()
            return StepResult("thumbnail_prompts", StepStatus.FAILED, "JSON parse failed")

        thumbnails_data = data["thumbnails"]
        if not thumbnails_data:
            self._log("  ERROR: Empty thumbnails!", "ERROR")
            workbook.update_step_status("step_8", "ERROR", 0, 3, "Empty response")
            workbook.save()
            return StepResult("thumbnail_prompts", StepStatus.FAILED, "Empty thumbnails")

        # Clear existing thumbnails and save new ones
        workbook.clear_thumbnails()

        from modules.excel_manager import Thumbnail
        saved = 0
        for item in thumbnails_data[:3]:
            thumb_id = int(item.get("thumb_id", saved + 1))
            img_prompt = str(item.get("img_prompt", "")).strip()
            if not img_prompt:
                continue

            # Parse reference files from prompt (same logic as scenes)
            chars_used = str(item.get("characters_used", "")).strip()
            loc_used = str(item.get("location_used", "")).strip()

            # Build reference_files list
            ref_files = []
            char_ids_in_prompt = re.findall(r'\(([nN][vV]_?\d+)\.png\)', img_prompt)
            loc_ids_in_prompt = re.findall(r'\(([lL][oO][cC]_?\d+)\.png\)', img_prompt)
            for cid in set(char_ids_in_prompt):
                ref_files.append(f"nv/{cid}.png")
            for lid in set(loc_ids_in_prompt):
                ref_files.append(f"loc/{lid}.png")

            import json
            thumb = Thumbnail(
                thumb_id=thumb_id,
                version_desc=str(item.get("version_desc", Thumbnail.VERSION_DESCS.get(thumb_id, f"v{thumb_id}"))),
                img_prompt=img_prompt,
                characters_used=chars_used,
                location_used=loc_used,
                reference_files=json.dumps(ref_files) if ref_files else "",
            )
            workbook.add_thumbnail(thumb)
            saved += 1
            self._log(f"  [v{thumb_id}] {thumb.version_desc}: {img_prompt[:80]}...")

        elapsed = round(time.time() - step_start, 1)
        workbook.update_step_status("step_8", "COMPLETED", saved, 3, f"{elapsed}s - {saved} thumbnails")
        workbook.save()
        self._log(f"  [SAVED] step_8 = COMPLETED ({saved}/3 thumbnails, {elapsed}s)")

        return StepResult("thumbnail_prompts", StepStatus.COMPLETED, f"{saved} thumbnails created")
