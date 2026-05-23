"""
VE3 Tool - Prompts Generator Module
===================================
Sử dụng AI API để phân tích SRT và tạo prompts cho ảnh/video.
Hỗ trợ: DeepSeek API
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
from concurrent.futures import ThreadPoolExecutor, ProcessPoolExecutor, as_completed
from datetime import timedelta
from pathlib import Path
from typing import Dict, Any, List, Optional, Tuple, Callable
import threading

import requests

from modules.utils import (
    get_logger,
    parse_srt_file,
    group_srt_into_scenes,
    format_srt_time
)
from modules.excel_manager import (
    PromptWorkbook,
    Character,
    Location,
    Scene
)
from modules.prompts_loader import (
    get_analyze_story_prompt,
    get_generate_scenes_prompt,
    get_smart_divide_scenes_prompt,
    get_global_style
)


# ============================================================================
# MULTI AI CLIENT (DeepSeek)
# ============================================================================

class MultiAIClient:
    """
    Client hỗ trợ DeepSeek API.
    Tự động test và loại bỏ API keys không hoạt động khi khởi tạo.
    """

    DEEPSEEK_URL = "https://api.deepseek.com/v1/chat/completions"

    def __init__(self, config: dict, auto_filter: bool = True):
        """
        Config format:
        {
            "deepseek_api_keys": ["key1"],
        }

        auto_filter: Tự động test và loại bỏ API keys không hoạt động
        """
        self.config = config
        self.deepseek_keys = [k for k in config.get("deepseek_api_keys", []) if k and k.strip()]

        self.deepseek_index = 0

        # Parallel processing settings
        self.max_parallel_requests = config.get("max_parallel_requests", 5)
        self.parallel_enabled = config.get("parallel_enabled", True)
        self._request_lock = threading.Lock()

        self.logger = get_logger("multi_ai")

        # Auto filter exhausted APIs at startup
        if auto_filter:
            self._filter_working_apis()

    def _filter_working_apis(self):
        """Test và loại bỏ API keys không hoạt động - PARALLEL VERSION."""
        print("\n[API Filter] Dang kiem tra API keys...")

        results = {
            'deepseek': [],
        }
        results_lock = threading.Lock()

        def test_deepseek(key_info: Tuple[int, str]) -> Tuple[str, int, str, bool]:
            i, key = key_info
            result = self._test_deepseek_key(key)
            return ('deepseek', i, key, result)

        # Prepare all test tasks
        tasks = []
        tasks.extend([('deepseek', i, key) for i, key in enumerate(self.deepseek_keys)])

        if not tasks:
            print("[API Filter] CANH BAO: Khong co DeepSeek API key!")
            return

        # Use ThreadPoolExecutor for parallel API testing
        max_workers = min(len(tasks), 10)

        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = []

            # Submit all API tests
            for provider, i, key in tasks:
                if provider == 'deepseek':
                    futures.append(executor.submit(test_deepseek, (i, key)))

            # Process results as they complete
            for future in as_completed(futures):
                try:
                    provider, idx, key, success = future.result()
                    status = "OK" if success else "SKIP"
                    print(f"  {provider.capitalize()} key #{idx+1}: {status}")
                    if success:
                        with results_lock:
                            results[provider].append(key)
                except Exception as e:
                    self.logger.error(f"API test error: {e}")

        # Update with working keys only
        self.deepseek_keys = results['deepseek']

        print(f"[API Filter] Ket qua: {len(self.deepseek_keys)} DeepSeek")

        if len(self.deepseek_keys) == 0:
            print("[API Filter] CANH BAO: Khong co API nao hoat dong!")
        else:
            print(f"[API Filter] Se dung: DeepSeek")

    def _test_deepseek_key(self, key: str) -> bool:
        """Test DeepSeek key với request nhỏ."""
        try:
            headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
            data = {
                "model": "deepseek-chat",
                "messages": [{"role": "user", "content": "Say OK"}],
                "max_tokens": 5
            }
            resp = requests.post(self.DEEPSEEK_URL, headers=headers, json=data, timeout=15)
            return resp.status_code == 200
        except:
            return False

    def generate_content(
        self,
        prompt: str,
        temperature: float = 0.7,
        max_tokens: int = 8192,
        max_retries: int = 3
    ) -> str:
        """Generate content using DeepSeek API.

        Chi thu cac API da duoc filter la hoat dong.
        """

        last_error = None

        # Try DeepSeek
        if self.deepseek_keys:
            for attempt in range(max_retries):
                try:
                    result = self._call_deepseek(prompt, temperature, max_tokens)
                    if result:
                        return result
                except Exception as e:
                    last_error = e
                    error_str = str(e).lower()

                    if "rate" in error_str or "429" in error_str:
                        self.logger.warning("DeepSeek rate limit, trying next key...")
                        self.deepseek_index = (self.deepseek_index + 1) % len(self.deepseek_keys)
                        time.sleep(3)
                        continue
                    elif "invalid" in error_str or "unauthorized" in error_str:
                        self.logger.warning("DeepSeek key invalid, removing...")
                        if self.deepseek_keys:
                            self.deepseek_keys.pop(self.deepseek_index)
                            if self.deepseek_keys:
                                self.deepseek_index = self.deepseek_index % len(self.deepseek_keys)
                            else:
                                break
                        continue
                    elif any(kw in error_str for kw in ["prematurely", "connection", "timeout", "network", "reset", "eof", "closed"]):
                        # Connection error - retry with delay
                        self.logger.warning(f"DeepSeek connection error ({attempt+1}/{max_retries}): {e}")
                        if attempt < max_retries - 1:
                            wait_time = 5 * (attempt + 1)  # 5s, 10s, 15s
                            self.logger.info(f"Waiting {wait_time}s before retry...")
                            time.sleep(wait_time)
                            continue
                        else:
                            self.logger.error(f"DeepSeek max retries reached for connection error")
                            break
                    else:
                        self.logger.error(f"DeepSeek error: {e}")
                        break

        if last_error:
            raise last_error
        raise RuntimeError("Khong co DeepSeek API key hoat dong!")

    def _call_deepseek(self, prompt: str, temperature: float, max_tokens: int) -> str:
        """Call DeepSeek API."""
        api_key = self.deepseek_keys[self.deepseek_index]

        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json"
        }

        # Determine if prompt expects JSON response
        expects_json = any(kw in prompt.lower() for kw in ['json', 'output format', '{"', "{'"])

        # DeepSeek API giới hạn max_tokens = 8192
        deepseek_max_tokens = min(max_tokens, 8192)

        data = {
            "model": "deepseek-chat",
            "messages": [
                {
                    "role": "system",
                    "content": "You are a helpful assistant. When asked to output JSON, respond ONLY with valid JSON, no markdown code blocks, no explanations before or after the JSON."
                },
                {"role": "user", "content": prompt}
            ],
            "temperature": temperature,
            "max_tokens": deepseek_max_tokens
        }

        # Force JSON mode if prompt expects JSON
        if expects_json:
            data["response_format"] = {"type": "json_object"}

        print(f"[DeepSeek] Dang goi API... (prompt: {len(prompt)} ky tu, json_mode={expects_json}, max_tokens={deepseek_max_tokens}, cho 60-180s)")

        resp = requests.post(self.DEEPSEEK_URL, headers=headers, json=data, timeout=180)

        if resp.status_code == 200:
            result = resp.json()
            content = result["choices"][0]["message"]["content"]
            print(f"[DeepSeek] Thanh cong! Response: {len(content)} ky tu")

            # Log preview for debugging
            if content:
                preview = content[:300].replace('\n', ' ')
                print(f"[DeepSeek] Preview: {preview}...")

            return content
        else:
            error_text = resp.text[:500]
            print(f"[DeepSeek] Error {resp.status_code}: {error_text}")
            raise requests.RequestException(f"DeepSeek API error {resp.status_code}: {error_text}")

    def generate_batch_parallel(
        self,
        prompts: List[str],
        temperature: float = 0.7,
        max_tokens: int = 8192,
        max_workers: int = None
    ) -> List[str]:
        """
        Generate content for multiple prompts in parallel.

        Args:
            prompts: List of prompts to process
            temperature: Temperature for generation
            max_tokens: Max tokens per response
            max_workers: Max parallel workers (None = auto)

        Returns:
            List of responses in same order as prompts
        """
        if not prompts:
            return []

        # Single prompt - no parallelization needed
        if len(prompts) == 1:
            return [self.generate_content(prompts[0], temperature, max_tokens)]

        # Determine worker count
        if max_workers is None:
            # Use total available API keys as max workers
            total_keys = len(self.deepseek_keys)
            max_workers = min(self.max_parallel_requests, max(1, total_keys))

        print(f"[Parallel] Xu ly {len(prompts)} prompts voi {max_workers} workers...")

        # Results placeholder (preserve order)
        results = [None] * len(prompts)
        errors = []

        def process_prompt(idx_prompt: Tuple[int, str]) -> Tuple[int, str, Exception]:
            """Process single prompt and return (index, result, error)."""
            idx, prompt = idx_prompt
            try:
                # Thread-safe API selection
                with self._request_lock:
                    pass  # Lock just to serialize index updates

                result = self.generate_content(prompt, temperature, max_tokens)
                return (idx, result, None)
            except Exception as e:
                return (idx, "", e)

        # Execute in parallel
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = [
                executor.submit(process_prompt, (i, p))
                for i, p in enumerate(prompts)
            ]

            # Process as completed with progress
            completed = 0
            for future in as_completed(futures):
                idx, result, error = future.result()
                completed += 1

                if error:
                    errors.append((idx, error))
                    self.logger.warning(f"Prompt {idx+1} failed: {error}")
                    results[idx] = ""  # Empty result for failed
                else:
                    results[idx] = result

                print(f"[Parallel] Hoan thanh {completed}/{len(prompts)}...", end="\r")

        print(f"[Parallel] Hoan thanh {len(prompts)} prompts, {len(errors)} loi")

        # Retry failed prompts sequentially
        if errors:
            print(f"[Parallel] Retry {len(errors)} prompts that bi loi...")
            for idx, _ in errors:
                try:
                    results[idx] = self.generate_content(prompts[idx], temperature, max_tokens)
                except Exception as e:
                    self.logger.error(f"Retry failed for prompt {idx+1}: {e}")
                    results[idx] = ""

        return results


# ============================================================================
# LEGACY GEMINI CLIENT (for backwards compatibility)
# ============================================================================

class GeminiClient:
    """
    Client để gọi Gemini API (free tier).
    Hỗ trợ nhiều API keys và models - tự động chuyển khi gặp lỗi.
    """
    
    BASE_URL = "https://generativelanguage.googleapis.com/v1beta"
    
    def __init__(self, api_keys: list, models: list):
        """
        Khởi tạo Gemini client với nhiều API keys và models.
        
        Args:
            api_keys: List các API keys
            models: List các model names
        """
        if isinstance(api_keys, str):
            api_keys = [api_keys]
        if isinstance(models, str):
            models = [models]
            
        self.api_keys = api_keys
        self.models = models
        self.current_key_index = 0
        self.current_model_index = 0
        self.logger = get_logger("gemini_client")
    
    @property
    def current_api_key(self):
        return self.api_keys[self.current_key_index]
    
    @property
    def current_model(self):
        return self.models[self.current_model_index]
    
    def _next_key(self) -> bool:
        """Chuyển sang API key tiếp theo. Return False nếu hết keys."""
        self.current_key_index += 1
        if self.current_key_index >= len(self.api_keys):
            self.current_key_index = 0
            return False
        self.logger.info(f"Switching to API key #{self.current_key_index + 1}")
        return True
    
    def _next_model(self) -> bool:
        """Chuyển sang model tiếp theo. Return False nếu hết models."""
        self.current_model_index += 1
        if self.current_model_index >= len(self.models):
            self.current_model_index = 0
            return False
        self.logger.info(f"Switching to model: {self.current_model}")
        return True
    
    def generate_content(
        self,
        prompt: str,
        temperature: float = 0.7,
        max_tokens: int = 8192,
        max_retries: int = None
    ) -> str:
        """
        Gọi Gemini API với tự động retry qua nhiều keys/models.
        """
        if max_retries is None:
            max_retries = len(self.api_keys) * len(self.models) * 2
        
        last_error = None
        attempts = 0
        
        while attempts < max_retries:
            attempts += 1
            
            try:
                result = self._call_api(prompt, temperature, max_tokens)
                return result
                
            except requests.RequestException as e:
                last_error = e
                error_str = str(e)
                
                # Lỗi 429 (quota exceeded) - thử key khác
                if "429" in error_str:
                    self.logger.warning(f"Quota exceeded, trying next key/model...")
                    if not self._next_key():
                        if not self._next_model():
                            # Đã thử hết tất cả, đợi và retry
                            self.logger.info("All keys/models tried. Waiting 15s...")
                            import time
                            time.sleep(15)
                    continue
                
                # Lỗi 404 (model not found) - thử model khác
                elif "404" in error_str:
                    self.logger.warning(f"Model {self.current_model} not found, trying next...")
                    if not self._next_model():
                        raise
                    continue
                
                # Lỗi khác
                else:
                    raise
        
        raise last_error or RuntimeError("Max retries exceeded")
    
    def _call_api(
        self,
        prompt: str,
        temperature: float,
        max_tokens: int
    ) -> str:
        """Thực hiện gọi API một lần."""
        url = f"{self.BASE_URL}/models/{self.current_model}:generateContent"
        
        headers = {"Content-Type": "application/json"}
        
        payload = {
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {
                "temperature": temperature,
                "maxOutputTokens": max_tokens,
            }
        }
        
        params = {"key": self.current_api_key}
        
        self.logger.debug(f"Calling API: model={self.current_model}, key=#{self.current_key_index + 1}")
        
        response = requests.post(
            url,
            headers=headers,
            params=params,
            json=payload,
            timeout=120
        )
        
        if response.status_code != 200:
            self.logger.error(f"API Error: {response.status_code}")
            raise requests.RequestException(
                f"Gemini API error: {response.status_code} - {response.text}"
            )
        
        data = response.json()
        
        try:
            text = data["candidates"][0]["content"]["parts"][0]["text"]
            return text
        except (KeyError, IndexError) as e:
            raise ValueError(f"Invalid API response format: {e}")

# ============================================================================
# PROMPT TEMPLATES - Loaded from config/prompts.yaml
# ============================================================================
# Prompts are now loaded from external file for easy editing
# Edit config/prompts.yaml to customize prompts without changing code


# ============================================================================
# PROMPT GENERATOR CLASS
# ============================================================================

class PromptGenerator:
    """
    Class tạo prompts từ file SRT sử dụng DeepSeek API.

    Flow:
    1. Đọc SRT và gom thành scenes
    2. Gọi AI để phân tích nhân vật
    3. Gọi AI để tạo prompt cho từng scene
    4. Lưu vào Excel
    """

    def __init__(self, settings: Dict[str, Any]):
        """
        Khởi tạo PromptGenerator.

        Args:
            settings: Dictionary cấu hình từ settings.yaml
        """
        self.settings = settings
        self.logger = get_logger("prompt_generator")

        # Sử dụng MultiAIClient (DeepSeek)
        self.ai_client = MultiAIClient(settings)

        # Scene grouping settings
        self.min_scene_duration = settings.get("min_scene_duration", 5)  # Min 5s (đủ thời gian xem)
        self.max_scene_duration = settings.get("max_scene_duration", 8)  # Max 8s per scene

        # Parallel processing settings
        self.parallel_enabled = settings.get("parallel_enabled", True)
        self.max_parallel_batches = settings.get("max_parallel_batches", 3)  # Parallel batch processing
        self.batch_size = settings.get("prompt_batch_size", 10)  # Scenes per batch

        # V2 Flow: Timestamps từ SRT, không để AI quyết định
        self.use_v2_flow = settings.get("use_v2_flow", True)  # Mặc định dùng V2

    def _is_child_character(self, char_id: str) -> bool:
        """
        Check if a character ID represents a child (cannot use reference image).
        Children cause API policy violations when used as reference images.

        Child patterns:
        - nvc1: narrator as child
        - IDs ending with numbers that indicate child versions
        - Or any ID marked with is_child=true in analyze_story
        """
        if not char_id:
            return False

        # Remove .png extension if present
        char_id_clean = char_id.replace('.png', '').lower()

        # Known child character patterns
        child_patterns = ['nvc1', 'nv1c', 'child']

        for pattern in child_patterns:
            if pattern in char_id_clean:
                return True

        return False

    def _filter_children_from_refs(self, ref_files: list, return_filtered: bool = False) -> list:
        """
        Filter out child characters from reference_files list.
        Children should be described inline in img_prompt, not referenced.

        Args:
            ref_files: List of reference file names (e.g., ["nvc.png", "nvc1.png", "loc.png"])
            return_filtered: If True, return tuple (filtered_refs, filtered_children)

        Returns:
            Filtered list without child characters, or tuple if return_filtered=True
        """
        if not ref_files:
            return ([], []) if return_filtered else []

        filtered = []
        children = []
        for ref in ref_files:
            if self._is_child_character(ref):
                self.logger.info(f"  -> Filtered out child character from references: {ref}")
                children.append(ref)
                continue
            filtered.append(ref)

        if return_filtered:
            return filtered, children
        return filtered

    def _get_child_inline_description(self, child_ref: str, characters: list) -> str:
        """
        Get inline description for a child character using their character_lock.

        Args:
            child_ref: Child reference file name (e.g., "nvc1.png")
            characters: List of Character objects

        Returns:
            Inline description like "(Child: 8-year-old boy, messy brown hair...)"
        """
        if not characters:
            return ""

        # Extract character ID from filename (remove .png extension)
        char_id = child_ref.replace(".png", "")

        # Find matching character
        for char in characters:
            if char.id == char_id:
                # Use character_lock for inline description
                char_lock = getattr(char, 'character_lock', '')
                if char_lock:
                    return f"(Child: {char_lock})"
        return ""

    def _add_children_inline_to_prompt(
        self,
        img_prompt: str,
        filtered_children: list,
        characters: list
    ) -> str:
        """
        Add inline descriptions for filtered children to the img_prompt.

        Args:
            img_prompt: Original prompt
            filtered_children: List of child reference files that were filtered
            characters: List of Character objects

        Returns:
            Updated prompt with inline child descriptions
        """
        if not img_prompt or not filtered_children or not characters:
            return img_prompt

        # Collect inline descriptions
        inline_descs = []
        for child_ref in filtered_children:
            desc = self._get_child_inline_description(child_ref, characters)
            if desc:
                inline_descs.append(desc)
                self.logger.info(f"  -> Added inline description for {child_ref}: {desc[:50]}...")

        if inline_descs:
            # Add at the beginning or end of prompt
            inline_text = ", ".join(inline_descs)
            # Add to prompt - prepend to make children visible in the scene
            img_prompt = f"{inline_text} - {img_prompt}"

        return img_prompt

    def _add_filename_annotations_to_prompt(
        self,
        img_prompt: str,
        reference_files: list,
        characters: list = None,
        locations: list = None
    ) -> str:
        """
        Add filename annotations to img_prompt for Flow to match uploaded reference images.

        Args:
            img_prompt: Original prompt
            reference_files: List of reference files (e.g., ["nvc.png", "nv1.png", "loc_apartment.png"])
            characters: List of Character objects (optional, for better matching)
            locations: List of Location objects (optional, for better matching)

        Returns:
            Updated prompt with filename annotations

        Example:
            Input:  "A 30-year-old man walking in the living room"
            Output: "A 30-year-old man (nvc.png) walking in the living room (loc_apartment.png)"
        """
        if not img_prompt or not reference_files:
            return img_prompt

        result = img_prompt
        annotations_added = []

        # Build lookup maps
        char_map = {}  # id -> character_lock
        loc_map = {}   # id -> location_lock

        if characters:
            for c in characters:
                char_map[c.id] = c.character_lock or c.vietnamese_prompt or c.name

        if locations:
            for loc in locations:
                loc_map[loc.id] = loc.location_lock or loc.name

        # Add annotations for each reference file
        for ref_file in reference_files:
            # Ensure filename has extension
            if not any(ref_file.endswith(ext) for ext in ['.png', '.jpg', '.jpeg', '.webp']):
                ref_file = f"{ref_file}.png"

            ref_id = ref_file.replace('.png', '').replace('.jpg', '').replace('.jpeg', '').replace('.webp', '')
            annotation = f"({ref_file})"

            # Check if annotation already exists
            if annotation in result:
                annotations_added.append(ref_file)
                continue

            # Try to find where to insert annotation
            inserted = False

            # Method 1: Match character_lock/location_lock in prompt
            if ref_id in char_map and char_map[ref_id]:
                desc = char_map[ref_id]
                # Try to find description in prompt and add annotation after
                match_len = min(30, len(desc))
                if match_len > 5 and desc[:match_len] in result:
                    # Find end of description (next comma, period, or clause)
                    idx = result.find(desc[:match_len])
                    if idx >= 0:
                        # Find the end of this character description
                        end_idx = idx + match_len
                        for end_char in [',', '.', ' in ', ' at ', ' with ', ' and ']:
                            pos = result.find(end_char, end_idx)
                            if pos > 0 and pos < end_idx + 100:
                                end_idx = pos
                                break
                        # Insert annotation
                        result = result[:end_idx] + f" {annotation}" + result[end_idx:]
                        inserted = True
                        annotations_added.append(ref_file)

            if not inserted and ref_id in loc_map and loc_map[ref_id]:
                desc = loc_map[ref_id]
                match_len = min(20, len(desc))
                if match_len > 5 and desc[:match_len] in result:
                    idx = result.find(desc[:match_len])
                    if idx >= 0:
                        end_idx = idx + match_len
                        for end_char in [',', '.', ' with ', ' and ']:
                            pos = result.find(end_char, end_idx)
                            if pos > 0 and pos < end_idx + 80:
                                end_idx = pos
                                break
                        result = result[:end_idx] + f" {annotation}" + result[end_idx:]
                        inserted = True
                        annotations_added.append(ref_file)

            # Method 2: If not inserted, will be added at the end as consolidated annotation
            if not inserted:
                annotations_added.append(ref_file)

        # Method 3: Add consolidated reference annotation at the end
        # This ensures ALL reference files are mentioned even if not inserted inline
        missing_annotations = [f for f in reference_files if f"({f}" not in result and f not in [a for a in annotations_added if f"({a})" in result]]

        # Ensure all refs have .png extension for checking
        all_refs_normalized = []
        for ref in reference_files:
            if not any(ref.endswith(ext) for ext in ['.png', '.jpg', '.jpeg', '.webp']):
                ref = f"{ref}.png"
            all_refs_normalized.append(ref)

        # Check which refs are NOT yet in the result
        # Check for both (ref.png) and (reference: ref.png) patterns
        # Use case-insensitive matching
        result_lower = result.lower()
        refs_not_in_result = []
        for ref in all_refs_normalized:
            ref_lower = ref.lower()
            ref_base = ref.replace('.png', '').replace('.jpg', '').replace('.jpeg', '').replace('.webp', '')
            ref_base_lower = ref_base.lower()
            # Skip if already mentioned in any form (case-insensitive)
            if f"({ref_lower})" in result_lower:
                continue
            if f"({ref_base_lower})" in result_lower:
                continue
            if f"reference: {ref_lower}" in result_lower:
                continue
            if f"reference: {ref_base_lower}" in result_lower:
                continue
            refs_not_in_result.append(ref)

        # CRITICAL: Also check if result already has ANY "(reference:" annotation
        # to prevent duplicate reference annotations entirely
        if "(reference:" in result_lower:
            # Already has a reference annotation, don't add more
            return result

        if refs_not_in_result:
            # Add as consolidated annotation at end
            refs_str = ", ".join(refs_not_in_result)
            # Clean up ending
            result = result.rstrip('. ')
            result = f"{result} (reference: {refs_str})."

        return result

    def _generate_content(self, prompt: str, temperature: float = 0.7, max_tokens: int = 8192) -> str:
        """Generate content using DeepSeek API."""
        return self.ai_client.generate_content(prompt, temperature, max_tokens)

    def _generate_content_large(self, prompt: str, temperature: float = 0.7, max_tokens: int = 8192) -> str:
        """
        Generate content dùng DeepSeek API.

        DeepSeek API giới hạn max_tokens=8192. Nếu response bị truncate, return empty
        để trigger retry logic ở layer trên (chunk sẽ được chia nhỏ hơn).
        """
        print(f"[Director] Dùng DeepSeek (max_tokens={min(max_tokens, 8192)})")
        try:
            result = self.ai_client.generate_content(prompt, temperature, max_tokens)
            if result:
                print(f"[Director] DeepSeek trả về {len(result)} ký tự")

                # Check if JSON response is truncated (unclosed braces/brackets)
                if '{' in result or '[' in result:
                    open_braces = result.count('{') - result.count('}')
                    open_brackets = result.count('[') - result.count(']')

                    if open_braces > 0 or open_brackets > 0:
                        print(f"[Director] [WARN] JSON BỊ TRUNCATE! Braces: +{open_braces}, Brackets: +{open_brackets}")
                        print("[Director] Response không hoàn chỉnh - sẽ retry...")
                        return ""  # Return empty to trigger retry
                    else:
                        # JSON looks complete
                        return result
                else:
                    # Not JSON, return as-is
                    return result
        except Exception as e:
            self.logger.warning(f"[Director] DeepSeek failed: {e}")
            print(f"[Director] DeepSeek thất bại: {e}")

        return ""

    def generate_for_project(
        self,
        project_dir: Path,
        code: str,
        overwrite: bool = False,
        on_characters_ready: Callable = None,
        on_scenes_batch_ready: Callable = None,
        total_scenes_callback: Callable = None,
        fallback_only: bool = False
    ) -> bool:
        """
        Tạo prompts cho một project.

        Args:
            project_dir: Path đến thư mục project
            code: Mã project
            overwrite: Nếu True, ghi đè prompts đã có
            on_characters_ready: Callback được gọi ngay khi characters được save
                                 Signature: on_characters_ready(excel_path, proj_dir)
                                 Cho phép caller bắt đầu tạo ảnh nhân vật song song
            on_scenes_batch_ready: Callback được gọi sau mỗi batch scenes được save
                                   Signature: on_scenes_batch_ready(excel_path, proj_dir, saved_count, total_count)
                                   Cho phép caller bắt đầu tạo ảnh scenes song song
            total_scenes_callback: Callback để thông báo tổng số scenes cần tạo
                                   Signature: total_scenes_callback(total_count)
                                   Gọi trước khi bắt đầu generate để caller biết số lượng
            fallback_only: Nếu True, chỉ tạo fallback prompts (không gọi API)
                          Dùng cho run_srt.py - Workers sẽ gọi API sau

        Returns:
            True nếu thành công
        """
        project_dir = Path(project_dir)

        # Paths - support both flat (PROJECTS) and nested structure
        # Flat: PROJECTS/{code}/{code}.srt
        # Nested: {project}/srt/{code}.srt
        srt_path_flat = project_dir / f"{code}.srt"
        srt_path_nested = project_dir / "srt" / f"{code}.srt"

        if srt_path_flat.exists():
            srt_path = srt_path_flat
            excel_path = project_dir / f"{code}_prompts.xlsx"
        elif srt_path_nested.exists():
            srt_path = srt_path_nested
            excel_path = project_dir / "prompts" / f"{code}_prompts.xlsx"
        else:
            self.logger.error(f"SRT file không tồn tại: {srt_path_flat} hoặc {srt_path_nested}")
            return False
        
        # Load hoặc tạo Excel
        workbook = PromptWorkbook(excel_path).load_or_create()

        # === RESUME MODE CHECK ===
        # Kiểm tra characters và scenes đã có chưa
        existing_characters = workbook.get_characters()
        stats = workbook.get_stats()
        total_scenes = stats.get('total_scenes', 0)
        scenes_with_prompts = stats.get('scenes_with_prompts', 0)

        # Đã có đầy đủ scenes với prompts → skip
        if workbook.has_prompts() and not overwrite:
            if total_scenes > 0 and scenes_with_prompts >= total_scenes:
                self.logger.info("Prompts đã tồn tại đầy đủ, bỏ qua")
                return True

        # === RESUME MODE: Characters có, scenes thiếu ===
        resume_scenes_only = False
        if existing_characters and (total_scenes == 0 or scenes_with_prompts < total_scenes):
            self.logger.info(f"RESUME: Đã có {len(existing_characters)} nhân vật, tiếp tục tạo scenes...")
            resume_scenes_only = True

        # Clear dữ liệu cũ nếu overwrite
        if overwrite:
            self.logger.info("Xóa prompts cũ...")
            workbook.clear_characters()
            workbook.clear_scenes()
            workbook.save()
            resume_scenes_only = False  # Clear = tạo lại tất cả

        # Đọc và parse SRT
        self.logger.info(f"Đọc SRT file: {srt_path}")
        srt_entries = parse_srt_file(srt_path)
        
        if not srt_entries:
            self.logger.error("Không tìm thấy entries trong SRT file")
            return False
        
        self.logger.info(f"Tìm thấy {len(srt_entries)} SRT entries")

        # === FALLBACK-ONLY MODE: Tạo prompts cơ bản, không gọi API ===
        if fallback_only:
            self.logger.info("=" * 60)
            self.logger.info("[FALLBACK-ONLY] Tạo Excel cơ bản (workers sẽ gọi API sau)")
            self.logger.info("=" * 60)
            return self._generate_fallback_only(
                srt_entries=srt_entries,
                project_dir=project_dir,
                code=code,
                excel_path=excel_path,
                workbook=workbook
            )

        # === ĐỌC TXT FILE (nguồn chính cho story content) ===
        # TXT chứa nội dung đầy đủ hơn SRT, dùng để đạo diễn hiểu tổng thể
        txt_path = project_dir / f"{code}.txt"
        txt_content = ""
        if txt_path.exists():
            try:
                with open(txt_path, 'r', encoding='utf-8') as f:
                    txt_content = f.read().strip()
                self.logger.info(f"Đọc TXT file: {txt_path.name} ({len(txt_content)} chars)")
            except Exception as e:
                self.logger.warning(f"Không thể đọc TXT: {e}")

        # Tạo full story text để phân tích
        # Ưu tiên TXT (đầy đủ hơn), fallback về SRT text
        if txt_content:
            full_story = txt_content
            self.logger.info("[STORY] Sử dụng TXT làm nguồn chính")
        else:
            full_story = " ".join([e.text for e in srt_entries])
            self.logger.info("[STORY] Không có TXT, dùng SRT text")

        # Step 1: Phân tích nhân vật + bối cảnh
        # (Luôn phân tích để có context, nhưng chỉ lưu vào Excel nếu chưa có)
        self.logger.info("Phân tích nhân vật và bối cảnh...")
        characters, locations, context_lock, global_style = self._analyze_characters(full_story)

        if not characters:
            self.logger.error("Không thể phân tích nhân vật")
            return False

        self.logger.info(f"Tìm thấy {len(characters)} nhân vật, {len(locations)} bối cảnh")

        # === SKIP nếu RESUME MODE (characters đã có) ===
        if resume_scenes_only:
            self.logger.info("RESUME: Bỏ qua thêm nhân vật (đã có trong Excel)")
            # Dùng characters từ Excel thay vì phân tích lại
            characters = [c for c in existing_characters if c.role != 'location']
            locations_from_excel = [c for c in existing_characters if c.role == 'location']
            # Convert back to Location objects nếu cần
            self.logger.info(f"  Dùng {len(characters)} nhân vật + {len(locations_from_excel)} locations từ Excel")
        else:
            # Lưu nhân vật vào Excel (skip children - họ sẽ được mô tả inline trong scene)
            children_skipped = 0
            for char in characters:
                # Children don't need reference images - they will be described inline in scene prompts
                if char.is_child or char.english_prompt == "DO_NOT_GENERATE":
                    char.image_file = "NONE"
                    char.status = "skip"  # Skip image generation
                    children_skipped += 1
                    self.logger.info(f"  -> Skipping child character: {char.id} (will use inline description)")
                else:
                    char.image_file = f"{char.id}.png"
                    char.status = "pending"
                workbook.add_character(char)

            if children_skipped > 0:
                self.logger.info(f"  -> {children_skipped} child characters skipped for image generation")

            # Lưu locations (as Character with role="location")
            for loc in locations:
                loc_char = Character(
                    id=loc.id,
                    role="location",
                    name=loc.name,
                    english_prompt=loc.english_prompt,  # location_prompt - for generating reference image
                    character_lock=loc.location_lock,   # location_lock - for scene prompts (IMPORTANT!)
                    vietnamese_prompt=loc.location_lock,  # Keep for backwards compat
                    image_file=f"{loc.id}.png",
                    status="pending"
                )
                workbook.add_character(loc_char)

        workbook.save()
        self.logger.info(f"Đã lưu {len(characters)} nhân vật + {len(locations)} bối cảnh")

        # === PARALLEL OPTIMIZATION ===
        # Gọi callback để caller có thể bắt đầu tạo ảnh nhân vật SONG SONG
        # trong khi vẫn tiếp tục tạo scene prompts
        # Kiểm tra xem ảnh nhân vật đã có chưa, nếu chưa thì vẫn phải tạo
        need_character_images = False
        if resume_scenes_only:
            # Resume mode: Kiểm tra xem ảnh nhân vật đã có chưa
            nv_dir = project_dir / "nv"
            if nv_dir.exists():
                existing_images = list(nv_dir.glob("*.png")) + list(nv_dir.glob("*.jpg"))
                # So sánh số ảnh với số nhân vật (không tính location)
                char_count = len([c for c in existing_characters if c.role != 'location'])
                if len(existing_images) < char_count:
                    self.logger.info(f"[RESUME] Có {len(existing_images)}/{char_count} ảnh nhân vật → cần tạo thêm")
                    need_character_images = True
                else:
                    self.logger.info(f"[RESUME] Đã có đủ {len(existing_images)} ảnh nhân vật → bỏ qua")
            else:
                self.logger.info(f"[RESUME] Thư mục nv/ chưa có → cần tạo ảnh nhân vật")
                need_character_images = True
        else:
            # Không phải resume mode → luôn tạo ảnh
            need_character_images = True

        if on_characters_ready and need_character_images:
            self.logger.info("[PARALLEL] Characters ready! Triggering character image generation...")
            try:
                on_characters_ready(excel_path, project_dir)
            except Exception as e:
                self.logger.warning(f"[PARALLEL] Callback error (non-fatal): {e}")

        # === TÍNH TỔNG THỜI LƯỢNG VIDEO TỪ SRT ===
        video_duration_seconds = 0
        if srt_entries:
            last_entry = srt_entries[-1]
            if hasattr(last_entry.end_time, 'total_seconds'):
                video_duration_seconds = last_entry.end_time.total_seconds()
            elif hasattr(last_entry, 'end_time'):
                # Parse string timestamp
                try:
                    ts = str(last_entry.end_time).replace(',', '.')
                    parts = ts.split(':')
                    if len(parts) == 3:
                        h, m, s = parts
                        video_duration_seconds = int(h) * 3600 + int(m) * 60 + float(s)
                except:
                    video_duration_seconds = len(srt_entries) * 5  # Fallback

        self.logger.info(f"[VIDEO] Tổng thời lượng: {video_duration_seconds:.0f}s ({video_duration_seconds/60:.1f} phút)")

        # === V2 FLOW: TIMESTAMPS TỪ SRT, KHÔNG ĐỂ AI QUYẾT ĐỊNH ===
        if self.use_v2_flow:
            self.logger.info("=" * 60)
            self.logger.info("[V2 FLOW] SỬ DỤNG FLOW MỚI - TIMESTAMPS CHÍNH XÁC TỪ SRT")
            self.logger.info("=" * 60)

            # Gọi V2 flow
            success = self.generate_prompts_v2(
                srt_entries=srt_entries,
                characters=characters,
                locations=locations,
                global_style=global_style,
                excel_path=str(excel_path),
                project_dir=str(project_dir)
            )

            if success:
                self.logger.info("[V2 FLOW] [v] Hoàn thành!")
                return True
            else:
                self.logger.error("[V2 FLOW] [x] Thất bại!")
                # KHÔNG fallback về flow cũ - flow cũ tạo scenes lỗi
                # Nếu V2 fail, return False để retry hoặc dùng fallback_only
                return False

        # === OLD FLOW DISABLED ===
        # Flow cũ đã bị disable vì tạo ra scenes lỗi với global_style
        # Nếu cần fallback, dùng fallback_only mode thay vì flow cũ
        self.logger.warning("[OLD FLOW] Đã bị disable - dùng V2 flow hoặc fallback_only")
        return False

        # Code bên dưới đã bị disable - giữ lại để tham khảo
        # === STEP 1.5: TẠO BACKUP SCENES BẰNG AI ===
        # Mục đích: Backup có chất lượng như prompts thật (AI-generated, không phải keyword matching)
        self.logger.info("=" * 50)
        self.logger.info("Step 1.5: Tạo BACKUP SCENES bằng AI (quality backup)...")
        self.logger.info("=" * 50)

        # Format character info cho AI
        chars_info_list = []
        for c in characters:
            chars_info_list.append(f"- {c.id}: {c.name} ({c.role})")
        chars_info = "\n".join(chars_info_list) if chars_info_list else "- nvc: Narrator (người kể chuyện)"

        # Format location info cho AI
        locs_info_list = []
        for loc in locations:
            locs_info_list.append(f"- {loc.id}: {loc.name}")
        locs_info = "\n".join(locs_info_list) if locs_info_list else "- general: Bối cảnh chung"

        # Chia SRT thành scenes (time-based)
        backup_scenes = group_srt_into_scenes(
            srt_entries,
            min_duration=self.min_scene_duration,
            max_duration=self.max_scene_duration
        )
        self.logger.info(f"[BACKUP] Cần tạo backup cho {len(backup_scenes)} scenes")

        # Gọi AI để tạo backup prompts (batch để nhanh hơn)
        backup_scenes_data = []
        BATCH_SIZE = 15  # 15 scenes/batch để AI không bị quá tải

        # Danh sách shot types để AI chọn (đa dạng)
        shot_types = [
            "Close-up shot", "Medium shot", "Wide establishing shot",
            "Over-the-shoulder shot", "Low angle shot", "High angle shot",
            "Dutch angle shot", "Point-of-view shot", "Two-shot"
        ]

        for batch_idx in range(0, len(backup_scenes), BATCH_SIZE):
            batch = backup_scenes[batch_idx:batch_idx + BATCH_SIZE]
            batch_num = batch_idx // BATCH_SIZE + 1
            total_batches = (len(backup_scenes) + BATCH_SIZE - 1) // BATCH_SIZE

            self.logger.info(f"[BACKUP] Processing batch {batch_num}/{total_batches} ({len(batch)} scenes)...")

            # Format scenes cho prompt
            scenes_text = ""
            for i, scene in enumerate(batch):
                scene_id = scene.get("scene_id", batch_idx + i + 1)
                scene_text = scene.get("text", "")[:200]

                start_time = scene.get("start_time")
                if hasattr(start_time, 'strftime'):
                    time_str = start_time.strftime("%H:%M:%S")
                else:
                    time_str = str(scene.get("srt_start", "00:00:00"))

                scenes_text += f"\nScene {scene_id} [{time_str}]: \"{scene_text}\"\n"

            # Prompt để AI tạo backup
            backup_prompt = f"""Bạn là đạo diễn hình ảnh. Phân tích các scenes và tạo prompt cho mỗi scene.

NHÂN VẬT CÓ SẴN:
{chars_info}

BỐI CẢNH CÓ SẴN:
{locs_info}

CÁC LOẠI GÓC QUAY (chọn đa dạng, đừng lặp lại liên tục):
{', '.join(shot_types)}

SCENES CẦN PHÂN TÍCH:
{scenes_text}

Cho MỖI scene, trả về JSON với format:
{{
  "scenes": [
    {{
      "scene_id": 1,
      "characters": ["char_id1", "char_id2"],  // ID từ danh sách trên, dùng "nvc" nếu là narrator
      "location": "loc_id",  // ID từ danh sách trên
      "shot_type": "Medium shot",  // Chọn đa dạng, phù hợp nội dung
      "visual_description": "Mô tả ngắn cảnh quay",
      "img_prompt": "Prompt chi tiết cho ảnh, bao gồm shot type, nhân vật, bối cảnh, ánh sáng, style"
    }}
  ]
}}

QUY TẮC:
1. shot_type phải ĐA DẠNG (không dùng Medium shot cho tất cả)
2. Close-up cho cảm xúc, Wide shot cho establishing, Action shot cho chuyển động
3. img_prompt phải đủ chi tiết: shot type + nhân vật + hành động + bối cảnh + ánh sáng + style
4. Chỉ dùng character/location IDs từ danh sách có sẵn

Trả về JSON:"""

            try:
                # Gọi AI (dùng model nhỏ/nhanh cho backup)
                response = self._generate_content(backup_prompt, temperature=0.7, max_tokens=4000)

                if response:
                    json_data = self._extract_json(response)
                    if json_data and "scenes" in json_data:
                        for ai_scene in json_data["scenes"]:
                            scene_id = ai_scene.get("scene_id", 0)

                            # Tìm scene gốc để lấy timestamps
                            original_scene = None
                            for s in batch:
                                if s.get("scene_id") == scene_id:
                                    original_scene = s
                                    break

                            if not original_scene:
                                # Fallback: dùng index
                                idx = scene_id - batch_idx - 1
                                if 0 <= idx < len(batch):
                                    original_scene = batch[idx]

                            if original_scene:
                                start_time = original_scene.get("start_time")
                                end_time = original_scene.get("end_time")
                                if hasattr(start_time, 'strftime'):
                                    srt_start = start_time.strftime("%H:%M:%S") + ",000"
                                    srt_end = end_time.strftime("%H:%M:%S") + ",000"
                                    duration = (end_time - start_time).total_seconds()
                                else:
                                    srt_start = str(original_scene.get("srt_start", "00:00:00,000"))
                                    srt_end = str(original_scene.get("srt_end", "00:00:00,000"))
                                    # Random duration 5-8 giây cho video clips
                                    import random
                                    duration = random.uniform(5.0, 8.0)

                                chars_used = ai_scene.get("characters", ["nvc"])
                                backup_scenes_data.append({
                                    "scene_id": scene_id,
                                    "srt_start": srt_start,
                                    "srt_end": srt_end,
                                    "duration": round(duration, 2) if isinstance(duration, float) else duration,
                                    "text": original_scene.get("text", "")[:500],
                                    "characters_used": json.dumps(chars_used),
                                    "location_used": ai_scene.get("location", ""),
                                    "reference_files": json.dumps([f"{c}.png" for c in chars_used if c != "nvc"]),
                                    "img_prompt": ai_scene.get("img_prompt", ""),
                                    "shot_type": ai_scene.get("shot_type", "Medium shot"),
                                    "visual_description": ai_scene.get("visual_description", ""),
                                    "status": "backup"
                                })

                        self.logger.info(f"[BACKUP] Batch {batch_num}: AI tạo {len(json_data['scenes'])} backup prompts")
                    else:
                        self.logger.warning(f"[BACKUP] Batch {batch_num}: AI response invalid, using fallback")
                        # Fallback cho batch này
                        for scene in batch:
                            backup_scenes_data.append(self._create_simple_backup_scene(scene, characters, locations))
                else:
                    self.logger.warning(f"[BACKUP] Batch {batch_num}: No AI response, using fallback")
                    for scene in batch:
                        backup_scenes_data.append(self._create_simple_backup_scene(scene, characters, locations))

            except Exception as e:
                self.logger.warning(f"[BACKUP] Batch {batch_num} error: {e}, using fallback")
                for scene in batch:
                    backup_scenes_data.append(self._create_simple_backup_scene(scene, characters, locations))

            # Rate limiting
            if batch_idx + BATCH_SIZE < len(backup_scenes):
                time.sleep(1)

        self.logger.info(f"[BACKUP] Tổng cộng {len(backup_scenes_data)} backup scenes đã tạo")

        # Lưu backup vào Excel (director_plan sheet)
        try:
            existing_plan = workbook.get_director_plan()
            if not existing_plan:
                self.logger.info(f"[BACKUP] Lưu {len(backup_scenes_data)} backup scenes vào Excel...")
                workbook.save_director_plan(backup_scenes_data)
                workbook.save()
                self.logger.info(f"[BACKUP] [v] Đã lưu backup với character/location mapping!")
            else:
                self.logger.info(f"[BACKUP] Đã có {len(existing_plan)} scenes trong director_plan, skip backup")
        except Exception as e:
            self.logger.warning(f"[BACKUP] Lỗi lưu: {e}")

        # Step 2: Director's Treatment - Phân tích cấu trúc câu chuyện
        self.logger.info("=" * 50)
        self.logger.info("Step 2: Tạo DIRECTOR'S TREATMENT (Kịch bản đạo diễn)...")
        self.logger.info("=" * 50)
        directors_treatment = self._create_directors_treatment(full_story)
        if directors_treatment:
            self.logger.info(f"[Director's Treatment] Story parts: {len(directors_treatment.get('story_parts', []))}")

        # Step 3: DIRECTOR'S SHOOTING PLAN - Đạo diễn lên kế hoạch quay
        self.logger.info("=" * 50)
        self.logger.info("Step 3: DIRECTOR'S SHOOTING PLAN - Đạo diễn quyết định ảnh!")
        self.logger.info("=" * 50)

        # === PROGRESSIVE SAVE CALLBACK cho Director ===
        # Lưu mỗi part ngay khi hoàn thành (không đợi toàn bộ director xong)
        director_parts_saved = []

        def on_director_part_complete(part_data):
            """Callback để lưu từng part của director ngay khi hoàn thành."""
            try:
                part_num = part_data.get("part_number", 0)
                part_name = part_data.get("part_name", "Unknown")
                story_parts = part_data.get("story_parts", [])

                # Convert story_parts → scenes và lưu
                temp_shooting_plan = {"story_parts": story_parts}
                part_scenes = self._convert_shooting_plan_to_scenes(temp_shooting_plan)

                if part_scenes:
                    # Lưu vào director_plan sheet
                    for scene in part_scenes:
                        try:
                            # Check if scene already exists
                            existing = workbook.get_director_plan()
                            existing_ids = {p['plan_id'] for p in existing} if existing else set()

                            if scene['scene_id'] not in existing_ids:
                                workbook._ensure_director_plan_sheet()
                                ws = workbook.workbook[workbook.DIRECTOR_PLAN_SHEET]
                                next_row = ws.max_row + 1
                                ws.cell(row=next_row, column=1, value=scene.get("scene_id", 0))
                                ws.cell(row=next_row, column=2, value=scene.get("srt_start", ""))
                                ws.cell(row=next_row, column=3, value=scene.get("srt_end", ""))
                                ws.cell(row=next_row, column=4, value=scene.get("duration", 0))
                                ws.cell(row=next_row, column=5, value=scene.get("text", "")[:500])
                                ws.cell(row=next_row, column=6, value="pending")
                        except:
                            pass

                    workbook.save()
                    director_parts_saved.append(part_num)
                    self.logger.info(f"[DIRECTOR] Part {part_num} ({part_name}): {len(part_scenes)} scenes saved")

            except Exception as e:
                self.logger.warning(f"[DIRECTOR] Progressive save error: {e}")

        directors_shooting = self._create_directors_shooting_plan(
            full_story, srt_entries, characters, locations, global_style,
            on_part_complete=on_director_part_complete  # Truyền callback
        )

        using_director_prompts = False  # Flag để biết có dùng prompts từ đạo diễn không

        if directors_shooting and directors_shooting.get("shooting_plan"):
            # Dùng kế hoạch quay từ đạo diễn
            scenes_data = self._convert_shooting_plan_to_scenes(directors_shooting["shooting_plan"])
            using_director_prompts = True
            self.logger.info(f"[Director] [v] Sử dụng {len(scenes_data)} shots từ đạo diễn")
        else:
            # Fallback: Dùng smart_divide_scenes cũ
            self.logger.warning("[Director] Không có kế hoạch quay, sử dụng smart_divide_scenes...")
            scenes_data = self._smart_divide_scenes(srt_entries, characters, locations, directors_treatment, global_style)
            using_director_prompts = False

        self.logger.info(f"Tổng cộng {len(scenes_data)} scenes")

        # === LƯU DIRECTOR PLAN VÀO EXCEL ===
        try:
            existing_plan = workbook.get_director_plan()
            if not existing_plan or len(existing_plan) < len(scenes_data):
                self.logger.info(f"[DIRECTOR PLAN] Lưu {len(scenes_data)} scenes vào director_plan...")
                workbook.save_director_plan(scenes_data)
        except Exception as e:
            self.logger.warning(f"[DIRECTOR PLAN] Lỗi lưu: {e}")

        # === THÔNG BÁO TỔNG SỐ SCENES CHO CALLER ===
        if total_scenes_callback:
            try:
                total_scenes_callback(len(scenes_data))
                self.logger.info(f"[PIPELINE] Thông báo: {len(scenes_data)} scenes cần tạo")
            except Exception as e:
                self.logger.warning(f"[PIPELINE] total_scenes_callback error: {e}")

        # Step 3: Tạo prompts cho từng batch scenes (PARALLEL)
        self.logger.info("=" * 50)
        self.logger.info("Step 3: Tạo IMG PROMPTS cho scenes...")
        self.logger.info("=" * 50)

        if not scenes_data:
            self.logger.error("KHÔNG CÓ SCENES DATA! Dừng.")
            return False

        # === RESUME MODE: Kiểm tra scenes đã có trong Excel ===
        existing_scene_ids = set()
        existing_prompts_map = {}  # scene_id -> prompts data
        missing_scenes_data = []  # Scenes cần generate

        try:
            existing_scenes = workbook.get_scenes()
            for s in existing_scenes:
                # Chỉ đếm scenes đã có img_prompt (không rỗng)
                if s.img_prompt and s.img_prompt.strip():
                    existing_scene_ids.add(s.scene_id)
                    existing_prompts_map[s.scene_id] = {
                        "img_prompt": s.img_prompt,
                        "video_prompt": s.video_prompt or s.img_prompt,
                        "characters_used": s.characters_used,
                        "location_used": s.location_used or "",
                        "reference_files": s.reference_files or []
                    }

            if existing_scene_ids:
                self.logger.info(f"[RESUME] Phát hiện {len(existing_scene_ids)} scenes đã có prompts trong Excel")

                # Lọc ra scenes chưa có prompt
                for scene in scenes_data:
                    if scene["scene_id"] not in existing_scene_ids:
                        missing_scenes_data.append(scene)

                if not missing_scenes_data:
                    # Tất cả scenes đã có → skip hoàn toàn
                    self.logger.info(f"[RESUME] [v] Tất cả {len(scenes_data)} scenes đã có prompts - SKIP!")
                    return True
                else:
                    # Một số scenes thiếu → chỉ generate phần thiếu
                    self.logger.info(f"[RESUME] Cần tạo thêm {len(missing_scenes_data)}/{len(scenes_data)} scenes")
                    # Thay thế scenes_data bằng missing_scenes_data
                    original_scenes_data = scenes_data  # Lưu lại để merge sau
                    scenes_data = missing_scenes_data
            else:
                # Không có scene nào có prompt → tạo mới tất cả
                original_scenes_data = None
        except Exception as e:
            self.logger.warning(f"[RESUME] Không đọc được Excel scenes: {e}")
            original_scenes_data = None

        all_scene_prompts = []
        progressive_saved = False  # Flag để biết đã lưu progressive chưa

        # === NẾU ĐẠO DIỄN ĐÃ TẠO PROMPTS → DÙ LUÔN, KHÔNG CẦN GỌI AI NỮA ===
        if using_director_prompts:
            self.logger.info("[Director Flow] Đạo diễn đã tạo prompts! Sử dụng trực tiếp...")
            for scene in scenes_data:
                # Lấy prompts từ scene (đạo diễn đã điền)
                # Ưu tiên characters_used/location_used đã được set trong _convert_shooting_plan_to_scenes
                chars_used = scene.get("characters_used", scene.get("characters_in_scene", []))
                loc_used = scene.get("location_used", scene.get("location_id", ""))

                all_scene_prompts.append({
                    "img_prompt": scene.get("img_prompt", ""),
                    "video_prompt": scene.get("img_prompt", ""),  # Dùng chung img_prompt cho video
                    "characters_used": chars_used,
                    "location_used": loc_used,
                    "reference_files": scene.get("reference_files", []),
                    "shot_type": scene.get("shot_type", ""),
                    "camera_angle": scene.get("camera_angle", ""),
                })
            self.logger.info(f"[Director Flow] [v] Lấy {len(all_scene_prompts)} prompts từ đạo diễn")
        else:
            # === FLOW CŨ: Gọi AI tạo prompts ===
            self.logger.info("[Legacy Flow] Tạo prompts bằng AI...")

            # Chia scenes thành batches để tránh vượt quá context limit
            batch_size = self.batch_size
            batches = []
            for i in range(0, len(scenes_data), batch_size):
                batches.append(scenes_data[i:i + batch_size])

            total_batches = len(batches)
            self.logger.info(f"Chia thanh {total_batches} batches, moi batch {batch_size} scenes")

            if self.parallel_enabled and total_batches > 1:
                # PARALLEL PROCESSING: Process multiple batches concurrently
                self.logger.info(f"[Parallel] Xu ly {total_batches} batches song song (max {self.max_parallel_batches} workers)...")

                def process_batch(batch_info: Tuple[int, List]) -> Tuple[int, List]:
                    """Process single batch and return (batch_idx, prompts)."""
                    batch_idx, batch = batch_info
                    prompts = self._generate_scene_prompts(
                        characters, batch, context_lock,
                        locations=locations,
                        global_style_override=global_style
                    )
                    return (batch_idx, prompts)

                # Results placeholder (preserve order)
                batch_results = [None] * total_batches

                with ThreadPoolExecutor(max_workers=self.max_parallel_batches) as executor:
                    futures = [
                        executor.submit(process_batch, (i, batch))
                        for i, batch in enumerate(batches)
                    ]

                    # Process as completed with progress
                    completed = 0
                    for future in as_completed(futures):
                        try:
                            batch_idx, prompts = future.result()
                            batch_results[batch_idx] = prompts
                            completed += 1
                            print(f"[Parallel] Batch {completed}/{total_batches} hoan thanh")
                        except Exception as e:
                            self.logger.error(f"Batch failed: {e}")

                # Flatten results in order
                for prompts in batch_results:
                    if prompts:
                        all_scene_prompts.extend(prompts)
                    else:
                        # Batch failed, add empty prompts
                        self.logger.warning("Some batch failed, using empty prompts")

                print(f"[Parallel] Hoan thanh {len(all_scene_prompts)} scene prompts")
            else:
                # SEQUENTIAL PROCESSING (fallback) - LƯU SAU MỖI BATCH
                saved_scene_count = 0
                for i, batch in enumerate(batches):
                    self.logger.info(f"Xu ly batch {i + 1}/{total_batches}")

                    scene_prompts = self._generate_scene_prompts(
                        characters, batch, context_lock,
                        locations=locations,
                        global_style_override=global_style
                    )
                    all_scene_prompts.extend(scene_prompts)

                    # === PROGRESSIVE SAVE: Lưu batch này vào Excel ngay ===
                    try:
                        batch_start_idx = saved_scene_count
                        for j, (scene_data, prompts) in enumerate(zip(batch, scene_prompts)):
                            # Quick save với data cơ bản
                            chars_used = prompts.get("characters_used", [])
                            chars_str = json.dumps(chars_used) if isinstance(chars_used, list) else str(chars_used)

                            # Simple ref_files từ characters_used
                            ref_files = []
                            if chars_used:
                                if isinstance(chars_used, str):
                                    try:
                                        chars_used = json.loads(chars_used)
                                    except:
                                        chars_used = [chars_used]
                                for char_id in chars_used:
                                    if char_id and not char_id.endswith('.png'):
                                        ref_files.append(f"{char_id}.png")
                            refs_str = json.dumps(ref_files)

                            scene = Scene(
                                scene_id=scene_data["scene_id"],
                                srt_start=scene_data.get("srt_start", "00:00:00,000"),
                                srt_end=scene_data.get("srt_end", "00:00:05,000"),
                                duration=scene_data.get("duration", 5.0),
                                planned_duration=scene_data.get("planned_duration", 5.0),
                                srt_text=scene_data.get("text", "")[:500],
                                img_prompt=prompts.get("img_prompt", ""),
                                video_prompt=prompts.get("video_prompt", ""),
                                status_img="pending",
                                status_vid="pending",
                                characters_used=chars_str,
                                location_used=prompts.get("location_used", ""),
                                reference_files=refs_str
                            )
                            workbook.add_scene(scene)
                            saved_scene_count += 1

                            # Update director_plan status
                            try:
                                workbook.update_director_plan_status(scene_data["scene_id"], "done")
                            except:
                                pass

                        workbook.save()
                        progressive_saved = True  # Đánh dấu đã lưu progressive
                        self.logger.info(f"[PROGRESSIVE] Đã lưu batch {i+1}/{total_batches} ({saved_scene_count}/{len(scenes_data)} scenes)")

                        # Callback để thông báo có scenes mới
                        if on_scenes_batch_ready:
                            try:
                                on_scenes_batch_ready(excel_path, project_dir, saved_scene_count, len(scenes_data))
                            except Exception as e:
                                self.logger.warning(f"[PROGRESSIVE] Callback error: {e}")

                    except Exception as e:
                        self.logger.warning(f"[PROGRESSIVE] Lưu batch {i+1} lỗi: {e}")

                    # Rate limiting
                    if i + 1 < total_batches:
                        time.sleep(2)  # Tránh rate limit

        # === VALIDATE: Đảm bảo all_scene_prompts có đủ số lượng như scenes_data ===
        self.logger.info(f"Scenes: {len(scenes_data)}, Prompts: {len(all_scene_prompts)}")
        if len(all_scene_prompts) < len(scenes_data):
            self.logger.warning(f"THIẾU {len(scenes_data) - len(all_scene_prompts)} prompts! Tạo fallback...")
            while len(all_scene_prompts) < len(scenes_data):
                idx = len(all_scene_prompts)
                scene = scenes_data[idx]
                # Tạo prompt từ visual_moment hoặc text
                visual = scene.get("visual_moment", scene.get("text", ""))
                fallback_prompt = f"{scene.get('shot_type', 'Medium shot')}, {visual[:300]}, cinematic lighting, 4K photorealistic"
                all_scene_prompts.append({
                    "img_prompt": fallback_prompt,
                    "video_prompt": fallback_prompt,
                    "characters_used": scene.get("characters_in_scene", []),
                    "location_used": scene.get("location_id", ""),
                    "reference_files": []
                })
                self.logger.info(f"Created fallback prompt for scene {idx + 1}")

        # === LƯU SCENES VÀO EXCEL ===
        # Nếu đã lưu progressive (sequential mode), skip phần này
        if progressive_saved:
            self.logger.info(f"[PROGRESSIVE] Đã lưu {len(all_scene_prompts)} scenes progressive, skip save cuối")
        else:
            # Lưu scenes vào Excel (Director Flow hoặc Parallel)
            self.logger.info(f"Lưu {len(scenes_data)} scenes vào Excel...")

        for scene_data, prompts in zip(scenes_data, all_scene_prompts):
            if progressive_saved:
                continue  # Skip nếu đã lưu progressive
            # Convert lists to JSON strings for storage
            chars_used = prompts.get("characters_used", [])

            # === LOCATION: Ưu tiên từ AI, fallback từ scene_data (Director Flow) ===
            location_used = prompts.get("location_used", "")
            if not location_used:
                # Fallback: dùng location_id từ bước chia scene (smart_divide_scenes)
                location_used = scene_data.get("location_id", "")
                if location_used:
                    self.logger.debug(f"Scene {scene_data['scene_id']}: Using location_id from scene division: {location_used}")

            ref_files = prompts.get("reference_files", [])

            # === AUTO-GENERATE reference_files nếu AI không điền ===
            if not ref_files:
                ref_files = []

                # Ưu tiên 1: Lấy từ prompts (characters_used)
                if chars_used:
                    if isinstance(chars_used, str):
                        try:
                            chars_used = json.loads(chars_used)
                        except:
                            chars_used = [chars_used]
                    for char_id in chars_used:
                        if char_id and not char_id.endswith('.png'):
                            ref_files.append(f"{char_id}.png")
                        elif char_id:
                            ref_files.append(char_id)

                # Ưu tiên 2: Fallback từ scene_data (characters_in_scene từ smart_divide)
                if not ref_files:
                    chars_in_scene = scene_data.get("characters_in_scene", [])
                    if chars_in_scene:
                        if isinstance(chars_in_scene, str):
                            try:
                                chars_in_scene = json.loads(chars_in_scene)
                            except:
                                chars_in_scene = [chars_in_scene]
                        for char_id in chars_in_scene:
                            if char_id and not char_id.endswith('.png'):
                                ref_files.append(f"{char_id}.png")
                            elif char_id:
                                ref_files.append(char_id)
                        self.logger.debug(f"Scene {scene_data['scene_id']}: Using characters_in_scene: {chars_in_scene}")

                # Thêm location đã dùng - MAP sang actual location ID
                if location_used:
                    # Tìm actual location ID từ locations list
                    actual_loc_id = None

                    # Nếu location_used đã là ID thực (có file tương ứng trong locations)
                    for loc in locations:
                        if loc.id == location_used:
                            actual_loc_id = location_used
                            break

                    # Nếu không tìm thấy, location_used có thể là generic (loc1, loc2...)
                    # Thử match theo index hoặc dùng location đầu tiên
                    if not actual_loc_id and locations:
                        # Nếu là loc1, loc2... thử parse index
                        import re
                        match = re.match(r'loc(\d+)', location_used)
                        if match:
                            idx = int(match.group(1)) - 1  # loc1 -> index 0
                            if 0 <= idx < len(locations):
                                actual_loc_id = locations[idx].id
                                self.logger.debug(f"Scene: Mapped '{location_used}' to '{actual_loc_id}'")

                        # Fallback: dùng location đầu tiên
                        if not actual_loc_id:
                            actual_loc_id = locations[0].id
                            self.logger.debug(f"Scene: Fallback '{location_used}' to '{actual_loc_id}'")

                    # Thêm vào ref_files nếu tìm được
                    if actual_loc_id:
                        loc_file = f"{actual_loc_id}.png" if not actual_loc_id.endswith('.png') else actual_loc_id
                        if loc_file not in ref_files:
                            ref_files.append(loc_file)
                        # Cập nhật location_used thành actual ID
                        location_used = actual_loc_id

                if ref_files:
                    self.logger.debug(f"Scene {scene_data['scene_id']}: Auto-generated reference_files: {ref_files}")

            # === QUAN TRỌNG: Đảm bảo LUÔN có nhân vật trong reference ===
            # Kiểm tra xem có nhân vật nào trong ref_files không
            has_character = False
            if ref_files:
                for ref in ref_files:
                    ref_lower = str(ref).lower()
                    # nvc, nv1, nv2... là nhân vật (không phải loc)
                    if ref_lower.startswith('nv'):
                        has_character = True
                        break

            # Nếu không có nhân vật nào → chọn nhân vật PHÙ HỢP dựa trên scene_type
            if not has_character:
                scene_type = scene_data.get("scene_type", "FRAME_PRESENT")

                # Chọn nhân vật dựa trên loại scene
                if scene_type == "CHILDHOOD_FLASHBACK":
                    # Flashback tuổi thơ: CHỈ dùng mẹ trẻ (KHÔNG dùng child reference - API policy!)
                    # Child sẽ được mô tả chi tiết trong img_prompt thay vì dùng reference
                    default_chars = ["nv1_young.png"]
                    self.logger.info(f"Scene {scene_data['scene_id']}: CHILDHOOD_FLASHBACK → using young mother only (child described in prompt)")
                elif scene_type == "ADULT_FLASHBACK":
                    # Flashback trưởng thành: narrator trẻ
                    default_chars = ["nvc_young.png"]
                    self.logger.info(f"Scene {scene_data['scene_id']}: ADULT_FLASHBACK → using young narrator")
                elif scene_type == "EMOTIONAL_BEAT":
                    # Cảm xúc: narrator hiện tại (close-up)
                    default_chars = ["nvc.png"]
                    self.logger.info(f"Scene {scene_data['scene_id']}: EMOTIONAL_BEAT → using narrator close-up")
                else:  # FRAME_PRESENT, YOUTUBE_CTA, default
                    # Hiện tại: narrator hiện tại
                    default_chars = ["nvc.png"]
                    self.logger.info(f"Scene {scene_data['scene_id']}: {scene_type} → using current narrator")

                # Thêm vào ref_files
                if not ref_files:
                    ref_files = default_chars.copy()
                else:
                    # Có loc nhưng không có nhân vật → thêm nhân vật vào đầu
                    for char in reversed(default_chars):
                        ref_files.insert(0, char)

            # === QUAN TRỌNG: Filter children từ reference_files (API policy violation) ===
            # Children phải được mô tả trong img_prompt, không dùng reference image
            ref_files, filtered_children = self._filter_children_from_refs(ref_files, return_filtered=True)

            chars_str = json.dumps(chars_used) if isinstance(chars_used, list) else str(chars_used)
            refs_str = json.dumps(ref_files) if isinstance(ref_files, list) else str(ref_files)

            # Lấy thời gian thực từ scene_data (đã được tính trong _validate_and_split_scenes)
            start_time = scene_data.get("start_time", "")
            end_time = scene_data.get("end_time", "")
            duration = scene_data.get("duration_seconds", 0)

            # === QUAN TRỌNG: Thêm filename annotations vào prompt ===
            # Format: "A 30-year-old man (nvc.png) walking in the park (loc_park.png)"
            # Giúp Flow match uploaded images với prompt
            img_prompt = prompts.get("img_prompt", "")
            video_prompt = prompts.get("video_prompt", "")

            # === ADD INLINE CHILD DESCRIPTIONS ===
            # Children must be described inline since they can't use reference images
            if filtered_children:
                img_prompt = self._add_children_inline_to_prompt(img_prompt, filtered_children, characters)
                video_prompt = self._add_children_inline_to_prompt(video_prompt, filtered_children, characters)

            if ref_files:
                img_prompt = self._add_filename_annotations_to_prompt(
                    img_prompt, ref_files, characters, locations
                )
                video_prompt = self._add_filename_annotations_to_prompt(
                    video_prompt, ref_files, characters, locations
                )

            # QUAN TRONG: srt_start/srt_end la timestamps chinh
            # Fallback sang start_time/end_time neu khong co
            srt_start_val = scene_data.get("srt_start") or start_time or "00:00:00,000"
            srt_end_val = scene_data.get("srt_end") or end_time or "00:00:05,000"

            # Tinh duration neu chua co
            if not duration and srt_start_val and srt_end_val:
                try:
                    duration = parse_time_to_seconds(srt_end_val) - parse_time_to_seconds(srt_start_val)
                except:
                    duration = 5.0  # Default 5s

            # planned_duration: thoi luong dao dien len ke hoach (mac dinh = duration)
            # Nguoi dung co the thay doi trong Excel sau
            planned_duration = scene_data.get("planned_duration", duration)

            # CRITICAL: Clamp planned_duration to max 8 seconds (technical limit)
            MAX_DURATION = 8
            if planned_duration and planned_duration > MAX_DURATION:
                self.logger.warning(
                    f"Scene {scene_data['scene_id']}: planned_duration {planned_duration}s exceeds max {MAX_DURATION}s, clamping"
                )
                planned_duration = MAX_DURATION

            scene = Scene(
                scene_id=scene_data["scene_id"],
                srt_start=srt_start_val,              # Timestamp bat dau (HH:MM:SS,mmm)
                srt_end=srt_end_val,                  # Timestamp ket thuc (HH:MM:SS,mmm)
                duration=round(duration, 2),          # Do dai tu SRT (giay)
                planned_duration=round(planned_duration, 2),  # Thoi luong dao dien ke hoach
                srt_text=scene_data.get("text", "")[:500],  # Truncate nếu quá dài
                img_prompt=img_prompt,
                video_prompt=video_prompt,
                status_img="pending",
                status_vid="pending",
                characters_used=chars_str,
                location_used=location_used,
                reference_files=refs_str
            )
            workbook.add_scene(scene)

        # Save final (nếu chưa save progressive)
        if not progressive_saved:
            workbook.save()
            self.logger.info(f"Đã lưu {len(scenes_data)} scenes với prompts")
        else:
            self.logger.info(f"[PROGRESSIVE] Hoàn thành - đã lưu {len(scenes_data)} scenes")

        # === AUTO-RETRY: Tự động tạo scenes cho timeline gaps ===
        max_gap_retries = 3
        for retry_round in range(max_gap_retries):
            try:
                # Detect timeline gaps (khoảng thời gian không có scene nào)
                timeline_gaps = workbook.detect_timeline_gaps(video_duration_seconds)
                if not timeline_gaps:
                    self.logger.info(f"[TIMELINE CHECK] [v] Không có gaps trong timeline - hoàn thành!")
                    break

                total_gap_duration = sum(g['duration'] for g in timeline_gaps)
                self.logger.warning(
                    f"[TIMELINE RETRY {retry_round + 1}/{max_gap_retries}] "
                    f"Phát hiện {len(timeline_gaps)} gaps ({total_gap_duration:.0f}s thiếu):"
                )
                for gap in timeline_gaps[:5]:
                    self.logger.warning(
                        f"  - {gap['start_time']} → {gap['end_time']} ({gap['duration']:.0f}s)"
                    )
                if len(timeline_gaps) > 5:
                    self.logger.warning(f"  ... và {len(timeline_gaps) - 5} gaps nữa")

                # Lọc SRT entries cho từng gap và tạo scenes
                total_new_scenes = 0
                for gap in timeline_gaps:
                    gap_start = gap['start_seconds']
                    gap_end = gap['end_seconds']

                    # Lọc SRT entries trong khoảng gap
                    gap_srt_entries = []
                    for entry in srt_entries:
                        # Parse entry timestamp
                        entry_start = 0
                        entry_end = 0
                        if hasattr(entry.start_time, 'total_seconds'):
                            entry_start = entry.start_time.total_seconds()
                            entry_end = entry.end_time.total_seconds()
                        else:
                            try:
                                ts = str(entry.start_time).replace(',', '.')
                                parts = ts.split(':')
                                if len(parts) == 3:
                                    entry_start = int(parts[0]) * 3600 + int(parts[1]) * 60 + float(parts[2])
                                ts = str(entry.end_time).replace(',', '.')
                                parts = ts.split(':')
                                if len(parts) == 3:
                                    entry_end = int(parts[0]) * 3600 + int(parts[1]) * 60 + float(parts[2])
                            except:
                                continue

                        # Entry nằm trong gap?
                        if entry_start >= gap_start - 1 and entry_end <= gap_end + 1:
                            gap_srt_entries.append(entry)

                    if not gap_srt_entries:
                        self.logger.debug(f"[GAP] Không có SRT entries cho gap {gap['start_time']} → {gap['end_time']}")
                        continue

                    self.logger.info(
                        f"[GAP] Processing gap {gap['start_time']} → {gap['end_time']}: "
                        f"{len(gap_srt_entries)} SRT entries"
                    )

                    # Tạo scenes cho gap này (dùng time-based split)
                    gap_scenes = group_srt_into_scenes(
                        gap_srt_entries,
                        min_duration=self.min_scene_duration,
                        max_duration=self.max_scene_duration
                    )

                    if not gap_scenes:
                        continue

                    # Get next scene_id
                    existing_scenes = workbook.get_scenes()
                    next_scene_id = max([s.scene_id for s in existing_scenes], default=0) + 1

                    # Generate prompts và lưu
                    for i, scene in enumerate(gap_scenes):
                        scene_id = next_scene_id + i

                        # Format scene data
                        start_time = scene.get("start_time")
                        end_time = scene.get("end_time")
                        if hasattr(start_time, 'strftime'):
                            srt_start = start_time.strftime("%H:%M:%S") + ",000"
                            srt_end = end_time.strftime("%H:%M:%S") + ",000"
                        else:
                            srt_start = str(scene.get("srt_start", gap['start_time']))
                            srt_end = str(scene.get("srt_end", gap['end_time']))

                        scene_text = scene.get("text", "")

                        # === TÌM BACKUP DATA TỪ DIRECTOR_PLAN ===
                        # Backup đã có character/location mapping
                        # Ưu tiên: 1) Timestamp match, 2) Text similarity match
                        backup_chars = "[]"
                        backup_location = ""
                        backup_refs = "[]"
                        backup_prompt = ""
                        backup_found = False

                        try:
                            # Parse scene start time to seconds
                            scene_start_secs = 0
                            if hasattr(start_time, 'total_seconds'):
                                scene_start_secs = start_time.total_seconds()
                            else:
                                ts = srt_start.replace(',', '.')
                                parts = ts.split(':')
                                if len(parts) == 3:
                                    scene_start_secs = int(parts[0]) * 3600 + int(parts[1]) * 60 + float(parts[2])

                            # Tìm backup scene trong director_plan
                            backup_plans = workbook.get_director_plan()

                            # === STRATEGY 1: Timestamp match (±15s) ===
                            best_time_match = None
                            best_time_diff = float('inf')

                            for bp in backup_plans:
                                bp_start = bp.get("srt_start", "")
                                if bp_start:
                                    try:
                                        ts = bp_start.replace(',', '.')
                                        parts = ts.split(':')
                                        if len(parts) == 3:
                                            bp_start_secs = int(parts[0]) * 3600 + int(parts[1]) * 60 + float(parts[2])
                                            time_diff = abs(bp_start_secs - scene_start_secs)
                                            if time_diff < 15 and time_diff < best_time_diff:
                                                best_time_diff = time_diff
                                                best_time_match = bp
                                    except:
                                        pass

                            if best_time_match:
                                backup_chars = best_time_match.get("characters_used", "[]")
                                backup_location = best_time_match.get("location_used", "")
                                backup_refs = best_time_match.get("reference_files", "[]")
                                backup_prompt = best_time_match.get("img_prompt", "")
                                backup_found = True
                                self.logger.debug(f"[GAP] Timestamp match: {best_time_diff:.1f}s diff")

                            # === STRATEGY 2: Text similarity match (nếu timestamp không khớp) ===
                            if not backup_found and scene_text:
                                best_text_match = None
                                best_similarity = 0
                                scene_words = set(scene_text.lower().split())

                                for bp in backup_plans:
                                    bp_text = bp.get("srt_text", "") or bp.get("text", "")
                                    if bp_text:
                                        bp_words = set(bp_text.lower().split())
                                        # Jaccard similarity
                                        if scene_words and bp_words:
                                            intersection = len(scene_words & bp_words)
                                            union = len(scene_words | bp_words)
                                            similarity = intersection / union if union > 0 else 0
                                            if similarity > 0.5 and similarity > best_similarity:
                                                best_similarity = similarity
                                                best_text_match = bp

                                if best_text_match:
                                    backup_chars = best_text_match.get("characters_used", "[]")
                                    backup_location = best_text_match.get("location_used", "")
                                    backup_refs = best_text_match.get("reference_files", "[]")
                                    backup_prompt = best_text_match.get("img_prompt", "")
                                    backup_found = True
                                    self.logger.debug(f"[GAP] Text similarity match: {best_similarity:.2f}")

                        except Exception as e:
                            self.logger.debug(f"[GAP] Không tìm được backup: {e}")

                        # Tạo prompt - ưu tiên backup prompt nếu có
                        if backup_prompt:
                            final_prompt = backup_prompt
                        else:
                            # Không có backup prompt → tạo prompt tốt hơn với global_style
                            # Dùng _create_simple_backup_scene để có character/location mapping
                            fallback_data = self._create_simple_backup_scene(
                                scene, characters, locations
                            )
                            final_prompt = fallback_data.get("img_prompt", "")
                            if not final_prompt:
                                final_prompt = f"Cinematic illustration of: {scene_text[:200]}. 4K photorealistic, dramatic lighting."
                            # Nếu chưa có backup_chars, dùng từ fallback
                            if backup_chars == "[]":
                                backup_chars = fallback_data.get("characters_used", "[]")
                                backup_location = fallback_data.get("location_used", "")
                                backup_refs = fallback_data.get("reference_files", "[]")
                            self.logger.info(f"[GAP] Scene {scene_id}: dùng fallback prompt với character/location mapping")

                        new_scene = Scene(
                            scene_id=scene_id,
                            srt_start=srt_start,
                            srt_end=srt_end,
                            duration=scene.get("duration", gap['duration'] / len(gap_scenes)),
                            planned_duration=scene.get("duration", 5.0),
                            srt_text=scene_text[:500],
                            img_prompt=final_prompt,
                            video_prompt=final_prompt,
                            status_img="pending",
                            status_vid="pending",
                            characters_used=backup_chars,
                            location_used=backup_location,
                            reference_files=backup_refs
                        )
                        workbook.add_scene(new_scene)
                        total_new_scenes += 1

                workbook.save()
                self.logger.info(f"[TIMELINE RETRY] [v] Đã tạo thêm {total_new_scenes} scenes cho gaps")

                # Delay trước retry tiếp theo
                if retry_round < max_gap_retries - 1:
                    time.sleep(2)

            except Exception as e:
                self.logger.error(f"[TIMELINE RETRY] Lỗi: {e}")
                import traceback
                self.logger.debug(traceback.format_exc())
                break

        # Final check - force fill nếu vẫn còn gaps
        try:
            final_gaps = workbook.detect_timeline_gaps(video_duration_seconds)
            if final_gaps:
                total_missing = sum(g['duration'] for g in final_gaps)
                self.logger.warning(
                    f"[FINAL] Còn {len(final_gaps)} gaps ({total_missing:.0f}s) - FORCE FILL từ backup..."
                )

                # === FORCE FILL: Tạo scenes cho TẤT CẢ gaps còn lại ===
                backup_plans = workbook.get_director_plan()
                existing_scenes = workbook.get_scenes()
                next_scene_id = max([s.scene_id for s in existing_scenes], default=0) + 1
                force_filled = 0

                for gap in final_gaps:
                    gap_start = gap['start_seconds']
                    gap_end = gap['end_seconds']
                    gap_duration = gap['duration']

                    # Tìm backup scenes trong khoảng gap
                    relevant_backups = []
                    for bp in backup_plans:
                        bp_start = bp.get("srt_start", "")
                        if bp_start:
                            try:
                                ts = bp_start.replace(',', '.')
                                parts = ts.split(':')
                                if len(parts) == 3:
                                    bp_secs = int(parts[0]) * 3600 + int(parts[1]) * 60 + float(parts[2])
                                    if gap_start - 5 <= bp_secs <= gap_end + 5:
                                        relevant_backups.append(bp)
                            except:
                                pass

                    if relevant_backups:
                        # Dùng backup scenes
                        for bp in relevant_backups:
                            new_scene = Scene(
                                scene_id=next_scene_id,
                                srt_start=bp.get("srt_start", gap['start_time']),
                                srt_end=bp.get("srt_end", gap['end_time']),
                                duration=bp.get("duration", gap_duration),
                                planned_duration=bp.get("duration", 5.0),
                                srt_text=bp.get("srt_text", "")[:500],
                                img_prompt=bp.get("img_prompt", "Cinematic scene, 4K photorealistic"),
                                video_prompt=bp.get("img_prompt", "Cinematic scene"),
                                status_img="pending",
                                status_vid="pending",
                                characters_used=bp.get("characters_used", "[]"),
                                location_used=bp.get("location_used", ""),
                                reference_files=bp.get("reference_files", "[]")
                            )
                            workbook.add_scene(new_scene)
                            next_scene_id += 1
                            force_filled += 1
                    else:
                        # Không có backup → tạo scene placeholder với thông tin từ gap
                        new_scene = Scene(
                            scene_id=next_scene_id,
                            srt_start=gap['start_time'],
                            srt_end=gap['end_time'],
                            duration=gap_duration,
                            planned_duration=min(gap_duration, 8.0),
                            srt_text=f"[Gap {gap['start_time']} - {gap['end_time']}]",
                            img_prompt="Cinematic illustration, atmospheric scene, 4K photorealistic, dramatic lighting",
                            video_prompt="Cinematic scene with smooth camera movement",
                            status_img="pending",
                            status_vid="pending",
                            characters_used="[]",
                            location_used="",
                            reference_files="[]"
                        )
                        workbook.add_scene(new_scene)
                        next_scene_id += 1
                        force_filled += 1

                if force_filled > 0:
                    workbook.save()
                    self.logger.info(f"[FINAL] [v] Force filled {force_filled} scenes cho gaps còn lại!")
            else:
                self.logger.info("[FINAL] [v] Timeline đầy đủ - không còn gaps!")
        except Exception as e:
            self.logger.error(f"[FINAL] Lỗi force fill: {e}")

        return True
    
    def _analyze_characters(self, story_text: str) -> tuple:
        """
        Phân tích truyện và trích xuất nhân vật + bối cảnh.

        Args:
            story_text: Toàn bộ nội dung truyện

        Returns:
            Tuple (List[Character], List[Location], context_lock: str, global_style: str)
        """
        # Load prompt từ config/prompts.yaml
        prompt_template = get_analyze_story_prompt()
        # Increased limit for longer stories (30+ min videos)
        prompt = prompt_template.format(story_text=story_text[:30000])

        try:
            # DeepSeek chỉ có 8192 tokens output
            response = self._generate_content_large(prompt, temperature=0.5, max_tokens=8192)

            # Parse JSON từ response
            json_data = self._extract_json(response)

            if not json_data or "characters" not in json_data:
                self.logger.error(f"Invalid characters response: {response[:500]}")
                return [], [], "", ""

            # Extract context_lock and global_style (v5.0 format)
            context_lock = json_data.get("context_lock", "")
            global_style = json_data.get("global_style", "")

            # Extract world_setting (v6.0 - Director's Vision)
            world_setting = json_data.get("world_setting", {})
            if world_setting:
                self.logger.info(f"[Director's Vision] Era: {world_setting.get('era', 'N/A')}, Setting: {world_setting.get('setting', 'N/A')}")
                # Nếu không có context_lock, tạo từ world_setting
                if not context_lock:
                    context_lock = f"{world_setting.get('era', '')}, {world_setting.get('setting', '')}, {world_setting.get('visual_style', '')}"
                    self.logger.info(f"[Director's Vision] Generated context_lock: {context_lock[:100]}...")

            # Extract characters
            characters = []
            for char_data in json_data["characters"]:
                # portrait_prompt = full prompt for generating reference image (white background)
                # character_lock = short description for scene prompts (IMPORTANT!)
                portrait_prompt = char_data.get("portrait_prompt", char_data.get("english_prompt", ""))
                character_lock = char_data.get("character_lock", "")

                # Check if this is a child character
                is_child = char_data.get("is_child", False)
                status = char_data.get("status", "pending")

                # If portrait_prompt is DO_NOT_GENERATE, this is a child (no reference image)
                if portrait_prompt == "DO_NOT_GENERATE":
                    is_child = True
                    status = "skip"  # Auto set status để bỏ qua tạo ảnh
                    # Keep DO_NOT_GENERATE as marker - don't replace with character_lock
                    self.logger.info(f"  -> Child character detected: {char_data.get('id', '')} - status=skip, will use inline description")

                # Nếu is_child=True nhưng status chưa set, set luôn
                if is_child and status == "pending":
                    status = "skip"
                    self.logger.info(f"  -> Child character: {char_data.get('id', '')} - status=skip (no image generation)")

                characters.append(Character(
                    id=char_data.get("id", ""),
                    role=char_data.get("role", "supporting"),
                    name=char_data.get("name", ""),
                    english_prompt=portrait_prompt,  # Keep DO_NOT_GENERATE for children
                    character_lock=character_lock,    # For scene prompts (IMPORTANT!)
                    vietnamese_prompt=char_data.get("vietnamese_prompt", char_data.get("vietnamese_description", "")),
                    is_child=is_child,
                    status=status,  # skip cho trẻ em, pending cho người lớn
                ))

            # Extract locations (v5.0 format)
            locations = []
            for loc_data in json_data.get("locations", []):
                english_prompt = loc_data.get("location_prompt", loc_data.get("english_prompt", ""))

                locations.append(Location(
                    id=loc_data.get("id", ""),
                    name=loc_data.get("name", ""),
                    english_prompt=english_prompt,
                    location_lock=loc_data.get("location_lock", ""),
                    lighting_default=loc_data.get("lighting_default", ""),
                    image_file=loc_data.get("filename", ""),
                ))

            self.logger.info(f"Extracted {len(characters)} characters, {len(locations)} locations")
            return characters, locations, context_lock, global_style

        except Exception as e:
            self.logger.error(f"Failed to analyze characters: {e}")
            return [], [], "", ""

    def _create_directors_treatment(self, story_text: str) -> Optional[Dict]:
        """
        Tạo Director's Treatment - Kịch bản đạo diễn phân tích cấu trúc câu chuyện.

        Args:
            story_text: Toàn bộ nội dung câu chuyện

        Returns:
            Dict với story_parts, visual_guidelines, scene_mapping_guide
        """
        try:
            # Load prompt từ config/prompts.yaml
            prompt_template = self._load_prompt_template("directors_treatment")
            if not prompt_template:
                self.logger.warning("[Director's Treatment] Không tìm thấy prompt template, bỏ qua")
                return None

            # Increased limit for longer stories (30+ min videos)
            prompt = prompt_template.format(story_text=story_text[:30000])

            self.logger.info("[Director's Treatment] Đang phân tích cấu trúc câu chuyện...")
            response = self._generate_content(prompt, temperature=0.3, max_tokens=8000)
            json_data = self._extract_json(response)

            if not json_data or "story_parts" not in json_data:
                self.logger.warning("[Director's Treatment] AI không trả về story_parts")
                return None

            # Log story analysis
            story_analysis = json_data.get("story_analysis", {})
            self.logger.info(f"[Director's Treatment] Title: {story_analysis.get('title', 'N/A')}")
            self.logger.info(f"[Director's Treatment] Theme: {story_analysis.get('main_theme', 'N/A')}")

            # Log story parts
            for part in json_data.get("story_parts", []):
                self.logger.info(f"  Part {part.get('part_number')}: {part.get('part_name')} ({part.get('time_range', 'N/A')})")
                self.logger.info(f"    Tone: {part.get('emotional_tone', 'N/A')}")
                self.logger.info(f"    Strategy: {part.get('visual_strategy', 'N/A')[:80]}...")

            return json_data

        except Exception as e:
            self.logger.error(f"[Director's Treatment] Failed: {e}")
            return None

    def _create_directors_shooting_plan(
        self,
        story_text: str,
        srt_entries: List,
        characters: List,
        locations: List,
        global_style: str,
        on_part_complete: Callable = None  # Callback for progressive save
    ) -> Optional[Dict]:
        """
        Tạo Director's Shooting Plan - Kế hoạch quay phim chi tiết.
        ĐÂY LÀ BƯỚC QUAN TRỌNG NHẤT - Đạo diễn quyết định tạo bao nhiêu ảnh và ảnh gì!

        TWO-PASS STRATEGY cho video dài (> 5 phút):
        - Pass 1: Phân tích story structure (1 API call, response nhỏ)
        - Pass 2: Generate chi tiết cho từng part (N API calls, mỗi call nhỏ)

        Args:
            story_text: Toàn bộ nội dung câu chuyện
            srt_entries: List các SrtEntry với timestamps
            characters: List các Character đã phân tích
            locations: List các Location đã phân tích
            global_style: Global style string
            on_part_complete: Callback(part_data) gọi sau mỗi part hoàn thành (progressive save)

        Returns:
            Dict với shooting_plan chứa tất cả shots đã lên kế hoạch
        """
        try:
            # Load prompt template
            prompt_template = self._load_prompt_template("directors_shooting_plan")
            if not prompt_template:
                self.logger.warning("[Director's Shooting Plan] Không tìm thấy prompt template")
                return None

            # Calculate total duration from SRT
            if srt_entries:
                total_duration_seconds = srt_entries[-1].end_time.total_seconds()
            else:
                total_duration_seconds = 0

            # TWO-PASS STRATEGY: For videos > 5 minutes
            # Ưu điểm: Context liền mạch, không bị truncate, progressive save
            TWO_PASS_THRESHOLD = 300  # 5 minutes

            if total_duration_seconds > TWO_PASS_THRESHOLD:
                self.logger.info(f"[Director] Video dài {total_duration_seconds/60:.1f} phút - sử dụng TWO-PASS STRATEGY")
                return self._create_shooting_plan_two_pass(
                    story_text, srt_entries, characters, locations,
                    global_style, prompt_template, on_part_complete
                )

            # Normal processing for shorter videos (< 5 min)
            return self._create_shooting_plan_single(
                story_text, srt_entries, characters, locations,
                global_style, prompt_template
            )

        except Exception as e:
            self.logger.error(f"[Director's Shooting Plan] Failed: {e}")
            import traceback
            traceback.print_exc()
            return None

    def _create_shooting_plan_single(
        self,
        story_text: str,
        srt_entries: list,
        characters: list,
        locations: list,
        global_style: str,
        prompt_template: str
    ) -> Optional[Dict]:
        """Create shooting plan for shorter videos (< 15 min) - single call."""
        # Format SRT segments với timestamps
        srt_segments = "\n".join([
            f"[{self._format_timedelta(e.start_time)} - {self._format_timedelta(e.end_time)}] \"{e.text[:200]}\""
            for e in srt_entries
        ])

        # Format characters info
        chars_info = "NHÂN VẬT:\n" + "\n".join([
            f"- {c.id}: {c.name} - {c.character_lock or ''}"
            for c in characters
        ]) if characters else "Không có thông tin nhân vật"

        # Format locations info
        locs_info = "BỐI CẢNH:\n" + "\n".join([
            f"- {loc.id}: {loc.name} - {loc.location_lock or ''}"
            for loc in locations
        ]) if locations else "Không có thông tin bối cảnh"

        prompt = prompt_template.format(
            story_text=story_text[:30000],
            srt_segments=srt_segments[:60000],
            characters_info=chars_info,
            locations_info=locs_info,
            global_style=global_style or get_global_style()
        )

        self.logger.info("=" * 50)
        self.logger.info("[Director's Shooting Plan] Đạo diễn đang lên kế hoạch quay...")
        self.logger.info("=" * 50)

        # Temperature 0.5 để AI đa dạng hơn với planned_duration (không uniform 5s)
        response = self._generate_content_large(prompt, temperature=0.5, max_tokens=8192)

        self.logger.info(f"[Director's Shooting Plan] Response length: {len(response) if response else 0}")
        if response:
            self.logger.info(f"[Director's Shooting Plan] Response preview: {response[:500]}...")

        json_data = self._extract_json(response)

        if json_data:
            self.logger.info(f"[Director's Shooting Plan] JSON keys: {list(json_data.keys())}")
        else:
            self.logger.warning(f"[Director's Shooting Plan] Failed to extract JSON from response")

        if not json_data or "shooting_plan" not in json_data:
            self.logger.warning("[Director's Shooting Plan] AI không trả về shooting_plan")
            if json_data:
                self.logger.warning(f"[Director's Shooting Plan] Available keys: {list(json_data.keys())}")
            return None

        shooting_plan = json_data["shooting_plan"]

        # Log summary
        self.logger.info(f"[Director's Shooting Plan] Tổng thời lượng: {shooting_plan.get('total_duration', 'N/A')}")
        self.logger.info(f"[Director's Shooting Plan] Tổng số ảnh: {shooting_plan.get('total_images', 0)}")

        total_shots = 0
        for part in shooting_plan.get("story_parts", []):
            shots_count = len(part.get("shots", []))
            total_shots += shots_count
            self.logger.info(f"  Part {part.get('part_number')}: {part.get('part_name')} - {shots_count} shots")

        self.logger.info(f"[Director's Shooting Plan] Tổng shots thực tế: {total_shots}")

        return json_data

    def _create_shooting_plan_two_pass(
        self,
        story_text: str,
        srt_entries: list,
        characters: list,
        locations: list,
        global_style: str,
        prompt_template: str,
        on_part_complete: Callable = None
    ) -> Optional[Dict]:
        """
        TWO-PASS STRATEGY cho video dài (30-60 phút).

        Pass 1: Phân tích story structure → 5-8 parts (1 API call, ~2000 tokens)
        Pass 2: Generate chi tiết cho từng part (N API calls, mỗi call ~4000 tokens)

        Ưu điểm:
        - Context liền mạch (Pass 1 nhìn toàn bộ story)
        - Không bị truncate (mỗi call nhỏ)
        - Progressive save (lưu ngay sau mỗi part)
        - Fallback per-part (nếu 1 part fail, không ảnh hưởng parts khác)
        """
        from datetime import timedelta

        self.logger.info("=" * 60)
        self.logger.info("[TWO-PASS] BẮT ĐẦU TWO-PASS STRATEGY")
        self.logger.info("=" * 60)

        # ===================================================================
        # PASS 1: STORY STRUCTURE ANALYSIS
        # ===================================================================
        self.logger.info("[TWO-PASS] PASS 1: Phân tích cấu trúc story...")

        # Load story_structure_analysis prompt
        structure_template = self._load_prompt_template("story_structure_analysis")
        if not structure_template:
            self.logger.warning("[TWO-PASS] Không tìm thấy story_structure_analysis template, fallback to chunked")
            return self._create_shooting_plan_chunked(
                story_text, srt_entries, characters, locations,
                global_style, prompt_template, 300  # 5 min chunks
            )

        # Format characters info
        chars_info = "NHÂN VẬT:\n" + "\n".join([
            f"- {c.id}: {c.name} ({c.role}) - {c.character_lock or 'No lock'}"
            for c in characters
        ]) if characters else "Không có thông tin nhân vật"

        # Format locations info
        locs_info = "BỐI CẢNH:\n" + "\n".join([
            f"- {loc.id}: {loc.name} - {loc.location_lock or 'No lock'}"
            for loc in locations
        ]) if locations else "Không có thông tin bối cảnh"

        # Create SRT summary (just time ranges, not full text)
        srt_summary = []
        for i, entry in enumerate(srt_entries):
            if i % 10 == 0 or i == len(srt_entries) - 1:  # Every 10th entry
                srt_summary.append(
                    f"[{self._format_timedelta(entry.start_time)}] {entry.text[:100]}..."
                )
        srt_summary_text = "\n".join(srt_summary)

        # Build Pass 1 prompt
        pass1_prompt = structure_template.format(
            story_text=story_text[:40000],
            srt_summary=srt_summary_text,
            characters_info=chars_info,
            locations_info=locs_info
        )

        self.logger.info(f"[TWO-PASS] Pass 1 prompt: {len(pass1_prompt)} chars")

        # Call API for Pass 1 (small response expected)
        MAX_RETRIES = 3
        structure_data = None

        for attempt in range(MAX_RETRIES):
            if attempt > 0:
                self.logger.warning(f"[TWO-PASS] Pass 1 retry {attempt}/{MAX_RETRIES-1}")
                import time
                time.sleep(2)

            response = self._generate_content_large(pass1_prompt, temperature=0.3, max_tokens=4000)

            if response:
                json_data = self._extract_json(response)
                if json_data and "parts" in json_data:
                    structure_data = json_data
                    self.logger.info(f"[TWO-PASS] Pass 1 OK: {len(structure_data['parts'])} parts")
                    break
                else:
                    self.logger.warning(f"[TWO-PASS] Pass 1 attempt {attempt+1}: Invalid JSON")
            else:
                self.logger.warning(f"[TWO-PASS] Pass 1 attempt {attempt+1}: No response")

        if not structure_data:
            self.logger.error("[TWO-PASS] Pass 1 FAILED, fallback to chunked strategy")
            return self._create_shooting_plan_chunked(
                story_text, srt_entries, characters, locations,
                global_style, prompt_template, 300
            )

        # Log structure
        self.logger.info("[TWO-PASS] Story Structure:")
        for part in structure_data.get("parts", []):
            self.logger.info(f"  Part {part.get('part_number')}: {part.get('part_name')} ({part.get('time_range')})")

        # ===================================================================
        # PASS 2: DETAIL GENERATION (per part)
        # ===================================================================
        self.logger.info("=" * 60)
        self.logger.info("[TWO-PASS] PASS 2: Generate chi tiết cho từng part...")
        self.logger.info("=" * 60)

        all_parts = []
        total_shots = 0
        previous_summary = ""

        for part_idx, part_info in enumerate(structure_data.get("parts", [])):
            part_num = part_info.get("part_number", part_idx + 1)
            part_name = part_info.get("part_name", f"Part {part_num}")
            time_range = part_info.get("time_range", "")

            self.logger.info(f"[TWO-PASS] Processing Part {part_num}/{len(structure_data['parts'])}: {part_name}")

            # Parse time range to filter SRT entries
            part_srt_entries = self._filter_srt_by_time_range(srt_entries, time_range)

            if not part_srt_entries:
                self.logger.warning(f"[TWO-PASS] Part {part_num}: No SRT entries, using fallback")
                # Use estimated time range
                part_srt_entries = srt_entries[part_idx * 20:(part_idx + 1) * 20]

            # Format SRT for this part
            srt_segments = "\n".join([
                f"[{self._format_timedelta(e.start_time)} - {self._format_timedelta(e.end_time)}] \"{e.text[:200]}\""
                for e in part_srt_entries
            ])

            # Build Pass 2 prompt for this part
            part_context = f"""
=== PART {part_num}: {part_name} ===
Time Range: {time_range}
Narrative Purpose: {part_info.get('narrative_purpose', 'N/A')}
Content Summary: {part_info.get('content_summary', 'N/A')}
Emotional Goal: {part_info.get('emotional_goal', 'N/A')}
Visual Tone: {part_info.get('visual_tone', 'N/A')}
Key Characters: {', '.join(part_info.get('key_characters', []))}
Key Locations: {', '.join(part_info.get('key_locations', []))}
Estimated Shots: {part_info.get('estimated_shots', 5)}

{f'PREVIOUS PART SUMMARY: {previous_summary}' if previous_summary else ''}
"""

            pass2_prompt = prompt_template.format(
                story_text=part_context + "\n\nFULL STORY CONTEXT:\n" + story_text[:15000],
                srt_segments=srt_segments,
                characters_info=chars_info,
                locations_info=locs_info,
                global_style=global_style or "Cinematic, 4K photorealistic"
            )

            # Call API for Pass 2 (this part only)
            part_data = None

            for attempt in range(MAX_RETRIES):
                if attempt > 0:
                    self.logger.warning(f"[TWO-PASS] Part {part_num} retry {attempt}/{MAX_RETRIES-1}")
                    import time
                    time.sleep(2)

                # Temperature 0.5 để planned_duration đa dạng hơn
                response = self._generate_content_large(pass2_prompt, temperature=0.5, max_tokens=8000)

                if response:
                    json_data = self._extract_json(response)
                    if json_data and "shooting_plan" in json_data:
                        part_data = json_data["shooting_plan"]
                        break
                    else:
                        self.logger.warning(f"[TWO-PASS] Part {part_num} attempt {attempt+1}: Invalid JSON")

            # Fallback: Create simple shots from SRT
            if not part_data:
                self.logger.warning(f"[TWO-PASS] Part {part_num} FAILED, using SRT fallback")
                part_data = {
                    "story_parts": self._create_fallback_shots_from_srt(
                        part_srt_entries, part_num, part_num, global_style
                    )
                }

            # Extract story_parts from this part
            part_story_parts = part_data.get("story_parts", [])

            if not part_story_parts:
                self.logger.warning(f"[TWO-PASS] Part {part_num}: No story_parts, creating fallback")
                part_story_parts = self._create_fallback_shots_from_srt(
                    part_srt_entries, part_num, part_num, global_style
                )

            # Renumber parts and shots
            for sp in part_story_parts:
                shots = sp.get("shots", [])
                total_shots += len(shots)
                self.logger.info(f"  → {sp.get('part_name', 'Unknown')}: {len(shots)} shots")

            all_parts.extend(part_story_parts)

            # Update previous summary for continuity
            if part_story_parts:
                last_shots = []
                for sp in part_story_parts[-1:]:
                    last_shots.extend(sp.get("shots", [])[-2:])
                if last_shots:
                    previous_summary = f"Last scene: {last_shots[-1].get('prompt', '')[:200]}"

            # Progressive Save callback
            if on_part_complete and part_story_parts:
                try:
                    on_part_complete({
                        "part_number": part_num,
                        "part_name": part_name,
                        "story_parts": part_story_parts
                    })
                    self.logger.info(f"[TWO-PASS] Part {part_num} saved (progressive)")
                except Exception as e:
                    self.logger.warning(f"[TWO-PASS] Progressive save failed: {e}")

        # ===================================================================
        # FINALIZE
        # ===================================================================
        self.logger.info("=" * 60)
        self.logger.info(f"[TWO-PASS] HOÀN THÀNH: {len(all_parts)} parts, {total_shots} shots")
        self.logger.info("=" * 60)

        # Build final result
        total_duration = self._format_timedelta(srt_entries[-1].end_time) if srt_entries else "00:00:00"

        return {
            "shooting_plan": {
                "total_duration": total_duration,
                "total_images": total_shots,
                "story_parts": all_parts
            }
        }

    def _filter_srt_by_time_range(self, srt_entries: list, time_range: str) -> list:
        """Filter SRT entries by time range string (e.g., '00:02:30 - 00:08:00')."""
        try:
            if not time_range or " - " not in time_range:
                return []

            start_str, end_str = time_range.split(" - ")

            def parse_time(t: str) -> float:
                parts = t.strip().split(":")
                if len(parts) == 3:
                    h, m, s = parts
                    return int(h) * 3600 + int(m) * 60 + float(s)
                elif len(parts) == 2:
                    m, s = parts
                    return int(m) * 60 + float(s)
                return 0

            start_sec = parse_time(start_str)
            end_sec = parse_time(end_str)

            return [
                e for e in srt_entries
                if e.start_time.total_seconds() >= start_sec - 5 and
                   e.start_time.total_seconds() <= end_sec + 5
            ]
        except Exception as e:
            self.logger.warning(f"[TWO-PASS] Parse time range failed: {e}")
            return []

    def _create_shooting_plan_chunked(
        self,
        story_text: str,
        srt_entries: list,
        characters: list,
        locations: list,
        global_style: str,
        prompt_template: str,
        chunk_duration: int
    ) -> Optional[Dict]:
        """
        Create shooting plan for LONG videos (> 15 min) using chunking strategy.

        Split SRT into chunks, process each chunk, then merge results.
        """
        from datetime import timedelta

        # === CHECK SRT GAPS trước khi chunking ===
        self.logger.info(f"[SRT CHECK] Kiểm tra {len(srt_entries)} SRT entries...")
        if len(srt_entries) >= 2:
            for i in range(len(srt_entries) - 1):
                curr_end = srt_entries[i].end_time.total_seconds()
                next_start = srt_entries[i + 1].start_time.total_seconds()
                gap = next_start - curr_end

                if gap > 60:  # Gap > 1 phút
                    self.logger.warning(
                        f"[SRT CHECK] [WARN] GAP trong SRT: "
                        f"{self._format_timedelta(srt_entries[i].end_time)} -> "
                        f"{self._format_timedelta(srt_entries[i + 1].start_time)} "
                        f"(gap: {gap:.0f}s = {gap/60:.1f} phút)"
                    )

        # Split SRT entries into chunks based on time
        chunks = []
        current_chunk = []
        chunk_start_time = 0

        for entry in srt_entries:
            entry_start = entry.start_time.total_seconds()

            # Check if this entry belongs to current chunk or next
            if entry_start >= chunk_start_time + chunk_duration and current_chunk:
                chunks.append(current_chunk)
                current_chunk = []
                chunk_start_time = entry_start

            current_chunk.append(entry)

        # Don't forget the last chunk
        if current_chunk:
            chunks.append(current_chunk)

        self.logger.info(f"[Director CHUNKING] Chia thành {len(chunks)} phần:")
        for i, chunk in enumerate(chunks):
            chunk_start = self._format_timedelta(chunk[0].start_time)
            chunk_end = self._format_timedelta(chunk[-1].end_time)
            self.logger.info(f"  Chunk {i+1}: {chunk_start} - {chunk_end} ({len(chunk)} segments)")

        # Format shared info (characters, locations)
        chars_info = "NHÂN VẬT:\n" + "\n".join([
            f"- {c.id}: {c.name} - {c.character_lock or ''}"
            for c in characters
        ]) if characters else "Không có thông tin nhân vật"

        locs_info = "BỐI CẢNH:\n" + "\n".join([
            f"- {loc.id}: {loc.name} - {loc.location_lock or ''}"
            for loc in locations
        ]) if locations else "Không có thông tin bối cảnh"

        # Process each chunk with RETRY logic
        all_parts = []
        part_number_offset = 0
        shot_number_offset = 0
        MAX_RETRIES = 3  # Số lần retry tối đa

        # Track previous chunk context for continuity
        previous_chunk_summary = ""
        previous_last_shots = []

        for chunk_idx, chunk_entries in enumerate(chunks):
            chunk_num = chunk_idx + 1
            chunk_start = self._format_timedelta(chunk_entries[0].start_time)
            chunk_end = self._format_timedelta(chunk_entries[-1].end_time)

            self.logger.info("=" * 50)
            self.logger.info(f"[Director CHUNKING] Xử lý chunk {chunk_num}/{len(chunks)}: {chunk_start} - {chunk_end}")
            self.logger.info("=" * 50)

            # Format SRT for this chunk
            srt_segments = "\n".join([
                f"[{self._format_timedelta(e.start_time)} - {self._format_timedelta(e.end_time)}] \"{e.text[:200]}\""
                for e in chunk_entries
            ])

            # Build context from previous chunk for CONTINUITY
            continuity_context = ""
            if previous_chunk_summary:
                continuity_context = f"""
**CONTEXT TỪ PHẦN TRƯỚC (để đảm bảo liên tục):**
{previous_chunk_summary}

**3 SHOTS CUỐI CỦA PHẦN TRƯỚC (để transition mượt):**
{chr(10).join(previous_last_shots) if previous_last_shots else "Đây là phần đầu tiên"}

**YÊU CẦU LIÊN TỤC:**
- Giữ nguyên trang phục/ngoại hình nhân vật như phần trước
- Nếu cùng bối cảnh, giữ lighting/mood nhất quán
- Shot đầu tiên phải transition mượt từ shot cuối phần trước

"""

            # Add context about this being a chunk
            chunk_context = f"""
**LƯU Ý: Đây là PHẦN {chunk_num}/{len(chunks)} của video dài.**
- Thời gian: {chunk_start} đến {chunk_end}
- Hãy tạo shooting plan CHỈ cho phần này.
- Đánh số part bắt đầu từ {part_number_offset + 1}.
- Đánh số shot bắt đầu từ {shot_number_offset + 1}.
- **QUAN TRỌNG về srt_range**: Phải dùng CHÍNH XÁC thời gian từ SRT (bắt đầu từ {chunk_start}), KHÔNG được bắt đầu từ 00:00!
{continuity_context}
"""

            prompt = prompt_template.format(
                story_text=chunk_context + story_text[:20000],  # Shorter story for chunks
                srt_segments=srt_segments,
                characters_info=chars_info,
                locations_info=locs_info,
                global_style=global_style or get_global_style()
            )

            # ============================================================
            # FALLBACK STRATEGY:
            # 1. DeepSeek (3 retries)
            # 2. SRT Fallback (cuối cùng - luôn hoạt động)
            # ============================================================
            chunk_parts = None

            # === TIER 1: DeepSeek (3 retries) ===
            self.logger.info(f"[TIER 1] DeepSeek cho chunk {chunk_num}...")
            for attempt in range(MAX_RETRIES):
                if attempt > 0:
                    self.logger.warning(f"[TIER 1] Retry {attempt}/{MAX_RETRIES-1} for chunk {chunk_num}")
                    import time
                    time.sleep(2)

                response = self._generate_content_large(prompt, temperature=0.4, max_tokens=8192)

                if not response:
                    self.logger.error(f"[TIER 1] Chunk {chunk_num} attempt {attempt+1} - no response")
                    continue

                json_data = self._extract_json(response)

                if not json_data or "shooting_plan" not in json_data:
                    self.logger.error(f"[TIER 1] Chunk {chunk_num} attempt {attempt+1} - no shooting_plan")
                    continue

                chunk_plan = json_data["shooting_plan"]
                chunk_parts = chunk_plan.get("story_parts", [])

                if chunk_parts:
                    self.logger.info(f"[TIER 1] [OK] Chunk {chunk_num} succeeded with DeepSeek!")
                    break
                else:
                    self.logger.error(f"[TIER 1] Chunk {chunk_num} attempt {attempt+1} - empty story_parts")

            # === TIER 2: SRT Fallback (luôn hoạt động) ===
            if not chunk_parts:
                self.logger.warning(f"[TIER 2] [WARN] DeepSeek failed for chunk {chunk_num}, using SRT FALLBACK...")
                self.logger.warning(f"[TIER 2] Creating shots from {len(chunk_entries)} SRT entries...")
                chunk_parts = self._create_fallback_shots_from_srt(
                    chunk_entries,
                    part_number_offset + 1,
                    shot_number_offset + 1,
                    global_style
                )
                fallback_shots = sum(len(p.get("shots", [])) for p in chunk_parts) if chunk_parts else 0
                self.logger.info(f"[TIER 2] [OK] FALLBACK created {len(chunk_parts) if chunk_parts else 0} parts, {fallback_shots} shots")

            # Safety check - nếu vẫn không có chunk_parts, tạo empty list để tránh crash
            if not chunk_parts:
                self.logger.error(f"[Director CHUNKING] [!] CRITICAL: Chunk {chunk_num} has NO parts even after fallback!")
                chunk_parts = []

            # Adjust part and shot numbers + VALIDATE TIMESTAMPS
            chunk_start_sec = chunk_entries[0].start_time.total_seconds()
            chunk_end_sec = chunk_entries[-1].end_time.total_seconds()

            for part in chunk_parts:
                part_number_offset += 1
                part["part_number"] = part_number_offset

                for shot in part.get("shots", []):
                    shot_number_offset += 1
                    shot["shot_number"] = shot_number_offset

                    # === VALIDATE & FIX TIMESTAMPS ===
                    # Đảm bảo shot có timestamp nằm trong chunk's time range
                    srt_range = shot.get("srt_range", "")
                    if srt_range and " - " in srt_range:
                        try:
                            range_parts = srt_range.split(" - ")
                            start_str = range_parts[0].strip()
                            end_str = range_parts[1].strip() if len(range_parts) > 1 else start_str

                            # Parse time helper
                            def parse_time_to_sec(t):
                                parts = t.replace(",", ".").split(":")
                                if len(parts) == 3:
                                    return int(parts[0]) * 3600 + int(parts[1]) * 60 + float(parts[2])
                                elif len(parts) == 2:
                                    return int(parts[0]) * 60 + float(parts[1])
                                return 0

                            shot_start_sec = parse_time_to_sec(start_str)
                            shot_end_sec = parse_time_to_sec(end_str)
                            shot_duration = shot_end_sec - shot_start_sec

                            # Tính thời lượng chunk
                            chunk_duration = chunk_end_sec - chunk_start_sec

                            # Kiểm tra timestamp có nằm trong chunk không
                            if shot_start_sec < chunk_start_sec - 10 or shot_start_sec > chunk_end_sec + 10:
                                # AI đang tạo timestamp từ 00:00! Cần fix
                                # Nếu shot_start_sec < chunk_duration, nghĩa là AI đếm từ đầu chunk
                                if shot_start_sec < chunk_duration:
                                    # Cộng chunk_start_sec để có thời gian thực
                                    fixed_start_sec = chunk_start_sec + shot_start_sec
                                    fixed_end_sec = chunk_start_sec + shot_end_sec

                                    # Đảm bảo không vượt quá chunk_end
                                    if fixed_end_sec > chunk_end_sec + 10:
                                        fixed_end_sec = min(fixed_end_sec, chunk_end_sec)

                                    # Format lại timestamp
                                    fixed_srt_range = f"{self._format_timedelta_simple(fixed_start_sec)} - {self._format_timedelta_simple(fixed_end_sec)}"

                                    self.logger.info(
                                        f"[TIMESTAMP FIX] Shot {shot['shot_number']}: {srt_range} → {fixed_srt_range}"
                                    )
                                    shot["srt_range"] = fixed_srt_range
                                else:
                                    # Timestamp sai không theo pattern 00:00, log warning
                                    self.logger.warning(
                                        f"[TIMESTAMP FIX] Shot {shot['shot_number']}: {srt_range} nằm ngoài chunk "
                                        f"({self._format_timedelta_simple(chunk_start_sec)} - {self._format_timedelta_simple(chunk_end_sec)})"
                                    )
                        except Exception as e:
                            pass  # Ignore parsing errors

            all_parts.extend(chunk_parts)

            shots_in_chunk = sum(len(p.get("shots", [])) for p in chunk_parts)
            self.logger.info(f"[Director CHUNKING] Chunk {chunk_num}: {len(chunk_parts)} parts, {shots_in_chunk} shots")

            # === EXTRACT CONTEXT FOR NEXT CHUNK (Continuity) ===
            if chunk_parts:
                # Get all shots from this chunk
                chunk_all_shots = []
                for part in chunk_parts:
                    chunk_all_shots.extend(part.get("shots", []))

                # Extract last 3 shots for visual continuity
                if chunk_all_shots:
                    last_shots = chunk_all_shots[-3:] if len(chunk_all_shots) >= 3 else chunk_all_shots
                    previous_last_shots = []
                    for shot in last_shots:
                        shot_desc = f"- Shot {shot.get('shot_number')}: {shot.get('img_prompt', '')[:100]}..."
                        previous_last_shots.append(shot_desc)

                # Create summary of this chunk for context
                part_names = [p.get("part_name", "Unknown") for p in chunk_parts[:3]]
                locations_used = set()
                characters_used = set()
                for part in chunk_parts:
                    if part.get("location"):
                        locations_used.add(part.get("location"))
                    for shot in part.get("shots", []):
                        for char in shot.get("characters_in_shot", []):
                            if isinstance(char, str):
                                characters_used.add(char)
                            elif isinstance(char, dict):
                                characters_used.add(char.get("id", ""))

                previous_chunk_summary = f"""Phần {chunk_num} ({chunk_start} - {chunk_end}):
- Các cảnh: {', '.join(part_names)}
- Bối cảnh: {', '.join(locations_used) if locations_used else 'Không xác định'}
- Nhân vật xuất hiện: {', '.join(characters_used) if characters_used else 'Không xác định'}
- Tổng {shots_in_chunk} shots"""

                self.logger.info(f"[CONTINUITY] Saved context for next chunk: {len(previous_last_shots)} shots, summary ready")

        if not all_parts:
            self.logger.error("[Director CHUNKING] Không có parts nào được tạo!")
            return None

        # Calculate totals
        total_shots = sum(len(p.get("shots", [])) for p in all_parts)
        total_duration = srt_entries[-1].end_time.total_seconds() if srt_entries else 0

        # === GAP DETECTION: Kiểm tra và cảnh báo nếu có gap lớn ===
        all_shots = []
        for part in all_parts:
            all_shots.extend(part.get("shots", []))

        if all_shots:
            self.logger.info("[GAP CHECK] Kiểm tra gaps trong shooting plan...")
            gaps_found = []

            for i in range(len(all_shots) - 1):
                current_shot = all_shots[i]
                next_shot = all_shots[i + 1]

                # Parse thời gian từ srt_range
                try:
                    current_range = current_shot.get("srt_range", "")
                    next_range = next_shot.get("srt_range", "")

                    if " - " in current_range and " - " in next_range:
                        current_end = current_range.split(" - ")[1].strip()
                        next_start = next_range.split(" - ")[0].strip()

                        # Parse time (HH:MM:SS hoặc MM:SS)
                        def parse_time(t):
                            parts = t.replace(",", ".").split(":")
                            if len(parts) == 3:
                                return int(parts[0]) * 3600 + int(parts[1]) * 60 + float(parts[2])
                            elif len(parts) == 2:
                                return int(parts[0]) * 60 + float(parts[1])
                            return 0

                        end_sec = parse_time(current_end)
                        start_sec = parse_time(next_start)
                        gap_sec = start_sec - end_sec

                        # Cảnh báo nếu gap > 30 giây
                        if gap_sec > 30:
                            gap_info = f"Shot {current_shot.get('shot_number')} ({current_end}) -> Shot {next_shot.get('shot_number')} ({next_start}): GAP {gap_sec:.0f}s"
                            gaps_found.append((gap_sec, gap_info, i))
                            self.logger.warning(f"[GAP CHECK] [WARN] {gap_info}")
                except Exception as e:
                    pass  # Ignore parsing errors

            if gaps_found:
                self.logger.error(f"[GAP CHECK] [WARN] TÌM THẤY {len(gaps_found)} GAPS LỚN!")
                self.logger.error("[GAP CHECK] Đang tự động fill từ SRT entries...")

                # === AUTO-FILL GAPS TỪ SRT ===
                for gap_sec, gap_info, gap_idx in gaps_found:
                    if gap_sec > 30:  # Chỉ fill gaps > 30s
                        # Tìm thời gian gap
                        current_shot = all_shots[gap_idx]
                        next_shot = all_shots[gap_idx + 1]

                        # Parse timestamps
                        try:
                            current_range = current_shot.get("srt_range", "").split(" - ")
                            next_range = next_shot.get("srt_range", "").split(" - ")

                            def parse_ts(ts):
                                parts = ts.strip().replace(",", ".").split(":")
                                if len(parts) == 3:
                                    return int(parts[0]) * 3600 + int(parts[1]) * 60 + float(parts[2])
                                elif len(parts) == 2:
                                    return int(parts[0]) * 60 + float(parts[1])
                                return 0

                            gap_start = parse_ts(current_range[-1]) if current_range else 0
                            gap_end = parse_ts(next_range[0]) if next_range else 0

                            # Tìm SRT entries trong khoảng gap
                            missing_srt = []
                            for entry in srt_entries:
                                entry_start = entry.start_time.total_seconds()
                                entry_end = entry.end_time.total_seconds()
                                # Entry nằm trong gap?
                                if entry_start >= gap_start - 5 and entry_end <= gap_end + 5:
                                    missing_srt.append(entry)

                            if missing_srt:
                                self.logger.info(f"[GAP FILL] Tìm thấy {len(missing_srt)} SRT entries trong gap {gap_start:.0f}s - {gap_end:.0f}s")

                                # Tạo fallback shots cho missing entries
                                fill_shots = []
                                for entry in missing_srt:
                                    duration_seconds = (entry.end_time - entry.start_time).total_seconds()
                                    planned_duration = min(max(duration_seconds, 3), 8)

                                    shot_number_offset += 1
                                    fill_shot = {
                                        "shot_number": shot_number_offset,
                                        "srt_range": f"{self._format_timedelta(entry.start_time)} - {self._format_timedelta(entry.end_time)}",
                                        "srt_text": entry.text[:200] if entry.text else "",
                                        "planned_duration": int(planned_duration),
                                        "img_prompt": f"{global_style or 'Cinematic, 4K'}, medium shot, dramatic scene",
                                        "shot_type": "MEDIUM",
                                        "camera_angle": "EYE LEVEL",
                                        "emotional_weight": "MEDIUM",
                                        "reference_files": [],
                                        "characters_in_shot": [],
                                        "visual_description": "Auto-filled scene from SRT",
                                        "purpose": "Gap-fill shot"
                                    }
                                    fill_shots.append(fill_shot)

                                # Thêm vào part cuối cùng hoặc tạo part mới
                                if all_parts:
                                    # Tìm part phù hợp hoặc tạo mới
                                    new_part = {
                                        "part_number": len(all_parts) + 1,
                                        "part_name": f"GAP FILL ({gap_start:.0f}s - {gap_end:.0f}s)",
                                        "location": "",
                                        "shots": fill_shots
                                    }
                                    all_parts.append(new_part)
                                    self.logger.info(f"[GAP FILL] [OK] Đã thêm {len(fill_shots)} shots để fill gap")
                        except Exception as e:
                            self.logger.warning(f"[GAP FILL] Lỗi fill gap: {e}")
            else:
                self.logger.info("[GAP CHECK] [OK] Không có gaps lớn")

        # Đếm lại total shots sau khi fill gaps
        total_shots_final = sum(len(p.get("shots", [])) for p in all_parts)

        # Merge all parts into final shooting plan
        merged_plan = {
            "shooting_plan": {
                "total_duration": f"{int(total_duration // 60)}:{int(total_duration % 60):02d}",
                "total_images": total_shots_final,
                "story_parts": all_parts
            }
        }

        self.logger.info("=" * 50)
        self.logger.info(f"[Director CHUNKING] HOÀN THÀNH!")
        self.logger.info(f"  - Tổng parts: {len(all_parts)}")
        self.logger.info(f"  - Tổng shots: {total_shots_final} (ban đầu: {total_shots}, fill: {total_shots_final - total_shots})")
        self.logger.info(f"  - Thời lượng: {int(total_duration // 60)} phút")
        self.logger.info("=" * 50)

        return merged_plan

    def _format_timedelta(self, td) -> str:
        """Format timedelta thành HH:MM:SS"""
        if hasattr(td, 'total_seconds'):
            total_seconds = int(td.total_seconds())
            hours = total_seconds // 3600
            minutes = (total_seconds % 3600) // 60
            seconds = total_seconds % 60
            return f"{hours:02d}:{minutes:02d}:{seconds:02d}"
        return str(td)

    def _format_timedelta_simple(self, total_seconds: float) -> str:
        """Format seconds thành MM:SS hoặc HH:MM:SS"""
        total_seconds = int(total_seconds)
        hours = total_seconds // 3600
        minutes = (total_seconds % 3600) // 60
        seconds = total_seconds % 60
        if hours > 0:
            return f"{hours:02d}:{minutes:02d}:{seconds:02d}"
        return f"{minutes:02d}:{seconds:02d}"

    def _create_fallback_shots_from_srt(
        self,
        srt_entries: list,
        start_part_num: int,
        start_shot_num: int,
        global_style: str
    ) -> list:
        """
        FALLBACK: Tạo shots cơ bản từ SRT entries khi API fail.

        Đảm bảo không bao giờ bị thiếu content - dù AI fail, vẫn có scenes để render.
        """
        self.logger.info(f"[FALLBACK] Creating basic shots from {len(srt_entries)} SRT entries")

        parts = []
        current_part = {
            "part_number": start_part_num,
            "part_name": "AUTO SCENE",
            "location": "",
            "shots": []
        }

        shot_num = start_shot_num
        MAX_ENTRIES_PER_PART = 20  # Nhóm 20 entries thành 1 part

        for i, entry in enumerate(srt_entries):
            # Tính duration từ SRT
            duration_seconds = (entry.end_time - entry.start_time).total_seconds()
            planned_duration = min(max(duration_seconds, 3), 8)  # 3-8s

            # IMPORTANT: KHÔNG đưa dialogue/narration text trực tiếp vào img_prompt!
            # Điều này sẽ khiến AI vẽ text lên ảnh.
            # Thay vào đó, tạo visual description từ text.
            base_style = global_style or 'Cinematic, 4K photorealistic, natural lighting'

            # Tạo visual description từ text (không dùng text trực tiếp)
            entry_text = entry.text[:100] if entry.text else ""
            entry_text_lower = entry_text.lower()

            # Detect shot type từ text
            shot_type = "medium shot"
            if any(kw in entry_text_lower for kw in ['nói', 'hỏi', 'thì thầm', 'whisper', 'said', 'asked']):
                shot_type = "close-up shot, emotional expression"
            elif any(kw in entry_text_lower for kw in ['đi', 'chạy', 'walk', 'run', 'moving']):
                shot_type = "tracking shot, movement"
            elif any(kw in entry_text_lower for kw in ['nhìn', 'look', 'gaze', 'stare']):
                shot_type = "medium shot, contemplative mood"

            # Tạo visual cue từ text (không phải text gốc)
            visual_cue = "person in scene, natural expression"
            if any(kw in entry_text_lower for kw in ['buồn', 'sad', 'cry', 'khóc']):
                visual_cue = "emotional moment, melancholic expression"
            elif any(kw in entry_text_lower for kw in ['vui', 'happy', 'smile', 'cười']):
                visual_cue = "joyful moment, warm atmosphere"
            elif any(kw in entry_text_lower for kw in ['sợ', 'fear', 'afraid', 'lo']):
                visual_cue = "tense moment, worried expression"
            elif any(kw in entry_text_lower for kw in ['giận', 'angry', 'mad']):
                visual_cue = "intense moment, dramatic lighting"

            # Thêm SRT context vào prompt (để AI biết minh họa cho nội dung gì)
            # Dùng "Illustrating:" để AI hiểu đây là ngữ cảnh, không phải text cần vẽ
            srt_context = entry.text[:150].strip() if entry.text else ""
            if srt_context:
                img_prompt = f"{base_style}, {shot_type}, {visual_cue}, dramatic scene, subtle film grain. Illustrating: [{srt_context}] (reference: nvc.png)"
            else:
                img_prompt = f"{base_style}, {shot_type}, {visual_cue}, dramatic scene, subtle film grain (reference: nvc.png)"

            shot = {
                "shot_number": shot_num,
                "srt_range": f"{self._format_timedelta(entry.start_time)} - {self._format_timedelta(entry.end_time)}",
                "srt_text": entry.text[:200] if entry.text else "",
                "planned_duration": int(planned_duration),
                "img_prompt": img_prompt,
                "shot_type": "MEDIUM",
                "camera_angle": "EYE LEVEL",
                "emotional_weight": "MEDIUM",
                "reference_files": ["nvc.png"],  # Match hardcoded reference in img_prompt
                "characters_in_shot": ["nvc"],
                "visual_description": "Cinematic scene with natural lighting",
                "purpose": "Auto-generated fallback shot"
            }

            current_part["shots"].append(shot)
            shot_num += 1

            # Nhóm thành parts mới sau mỗi MAX_ENTRIES_PER_PART shots
            if len(current_part["shots"]) >= MAX_ENTRIES_PER_PART and i < len(srt_entries) - 1:
                parts.append(current_part)
                current_part = {
                    "part_number": start_part_num + len(parts),
                    "part_name": f"AUTO SCENE {len(parts) + 1}",
                    "location": "",
                    "shots": []
                }

        # Đừng quên part cuối
        if current_part["shots"]:
            parts.append(current_part)

        self.logger.info(f"[FALLBACK] Created {len(parts)} parts with {shot_num - start_shot_num} total shots")
        return parts

    def _create_simple_backup_scene(self, scene: Dict, characters: List, locations: List) -> Dict:
        """
        FALLBACK: Tạo backup scene đơn giản khi AI backup fails.
        Dùng keyword matching thay vì AI.
        """
        scene_text = scene.get("text", "")
        scene_text_lower = scene_text.lower()

        # Tìm characters bằng keyword matching
        chars_in_scene = []
        for c in characters:
            if c.name and c.name.lower() in scene_text_lower:
                chars_in_scene.append(c.id)
        if not chars_in_scene:
            chars_in_scene = ["nvc"]

        # Tìm location
        location_in_scene = locations[0].id if locations else ""
        for loc in locations:
            if loc.name and loc.name.lower() in scene_text_lower:
                location_in_scene = loc.id
                break

        # Format timestamps
        start_time = scene.get("start_time")
        end_time = scene.get("end_time")
        if hasattr(start_time, 'strftime'):
            srt_start = start_time.strftime("%H:%M:%S") + ",000"
            srt_end = end_time.strftime("%H:%M:%S") + ",000"
            duration = (end_time - start_time).total_seconds()
        else:
            srt_start = str(scene.get("srt_start", "00:00:00,000"))
            srt_end = str(scene.get("srt_end", "00:00:00,000"))
            # Random duration 5-8 giây cho video clips
            import random
            duration = random.uniform(5.0, 8.0)

        # Simple shot type detection
        shot_type = "Medium shot"
        if any(kw in scene_text_lower for kw in ['nói', 'hỏi', 'khóc', 'cười']):
            shot_type = "Close-up shot"
        elif any(kw in scene_text_lower for kw in ['nhìn ra', 'toàn cảnh', 'bầu trời']):
            shot_type = "Wide establishing shot"

        # Create prompt
        default_prompt = (
            f"{shot_type}, {scene_text[:150]}. "
            f"Characters: {', '.join(chars_in_scene)}. "
            f"Location: {location_in_scene or 'general'}. "
            f"Cinematic lighting, 4K photorealistic."
        )

        return {
            "scene_id": scene.get("scene_id", 0),
            "srt_start": srt_start,
            "srt_end": srt_end,
            "duration": round(duration, 2) if isinstance(duration, float) else duration,
            "text": scene_text[:500],
            "characters_used": json.dumps(chars_in_scene),
            "location_used": location_in_scene,
            "reference_files": json.dumps([f"{c}.png" for c in chars_in_scene if c != "nvc"]),
            "img_prompt": default_prompt,
            "shot_type": shot_type,
            "status": "backup_fallback"
        }

    def _convert_shooting_plan_to_scenes(self, shooting_plan: Dict) -> List[Dict[str, Any]]:
        """
        Chuyển đổi shooting_plan từ đạo diễn thành scenes data.

        Args:
            shooting_plan: Output từ directors_shooting_plan

        Returns:
            List scenes với format tương thích với hệ thống hiện tại
        """
        scenes = []
        scene_id = 1

        def normalize_timestamp(ts: str) -> str:
            """
            Chuẩn hóa timestamp về format SRT: HH:MM:SS,mmm
            Input có thể là: "00:00", "00:00:00", "00:00:00,000", "0:00", etc.
            """
            ts = ts.strip()
            if not ts:
                return "00:00:00,000"

            # Nếu có dấu phẩy (milliseconds)
            if "," in ts:
                main_part, ms = ts.rsplit(",", 1)
            else:
                main_part = ts
                ms = "000"

            # Đảm bảo ms là 3 chữ số
            ms = ms.ljust(3, "0")[:3]

            # Parse main part
            parts = main_part.split(":")
            if len(parts) == 2:
                # MM:SS -> HH:MM:SS
                mm, ss = parts
                hh = "00"
            elif len(parts) == 3:
                hh, mm, ss = parts
            else:
                return "00:00:00,000"

            # Pad với 0
            hh = hh.zfill(2)
            mm = mm.zfill(2)
            ss = ss.zfill(2)

            return f"{hh}:{mm}:{ss},{ms}"

        # Helper: Parse timestamp to seconds for validation
        def timestamp_to_seconds(ts: str) -> float:
            """Convert HH:MM:SS,mmm to seconds"""
            try:
                main_part = ts.split(",")[0]
                parts = main_part.split(":")
                if len(parts) == 3:
                    return int(parts[0]) * 3600 + int(parts[1]) * 60 + int(parts[2])
                elif len(parts) == 2:
                    return int(parts[0]) * 60 + int(parts[1])
                return 0
            except:
                return 0

        prev_end_seconds = 0  # Track previous scene end for gap detection

        for part in shooting_plan.get("story_parts", []):
            for shot in part.get("shots", []):
                # Parse srt_range để lấy timestamps
                srt_range = shot.get("srt_range", "00:00 - 00:08")
                times = srt_range.split(" - ")
                start_time = normalize_timestamp(times[0]) if len(times) > 0 else "00:00:00,000"
                end_time = normalize_timestamp(times[1]) if len(times) > 1 else "00:00:08,000"

                # ============== TIMESTAMP VALIDATION ==============
                start_secs = timestamp_to_seconds(start_time)
                end_secs = timestamp_to_seconds(end_time)

                # FIX 1: end_time < start_time (invalid!)
                if end_secs <= start_secs:
                    self.logger.warning(
                        f"[Validation] Shot {scene_id}: end_time ({end_time}) <= start_time ({start_time})! Auto-fixing..."
                    )
                    # Set end = start + 5 seconds (default duration)
                    end_secs = start_secs + 5
                    hours = int(end_secs // 3600)
                    minutes = int((end_secs % 3600) // 60)
                    seconds = int(end_secs % 60)
                    end_time = f"{hours:02d}:{minutes:02d}:{seconds:02d},000"

                # FIX 2: Gap detection - if this scene starts way after previous ends
                if prev_end_seconds > 0:
                    gap = start_secs - prev_end_seconds
                    if gap > 300:  # Gap > 5 minutes = suspicious
                        self.logger.warning(
                            f"[Validation] Shot {scene_id}: GAP of {int(gap)}s detected! "
                            f"(prev ended at {int(prev_end_seconds)}s, this starts at {int(start_secs)}s)"
                        )

                prev_end_seconds = end_secs
                # ============== END VALIDATION ==============

                # === Extract characters_used và location_used từ reference_files ===
                ref_files = shot.get("reference_files", [])
                chars_in_shot = shot.get("characters_in_shot", [])

                # Location: ưu tiên từ reference_files (loc_xxx.png), fallback từ part.location
                location_from_refs = ""
                chars_from_refs = []
                for ref in ref_files:
                    ref_name = ref.replace(".png", "").strip()
                    if ref_name.startswith("loc_"):
                        location_from_refs = ref_name  # e.g., "loc_courthouse"
                    else:
                        chars_from_refs.append(ref_name)  # e.g., "nvc"

                # Fallback location từ part level
                location_id = location_from_refs or (
                    part.get("location", "").split(",")[0].strip() if part.get("location") else ""
                )

                # Characters: ưu tiên characters_in_shot, fallback từ reference_files
                characters_used = chars_in_shot if chars_in_shot else chars_from_refs

                # Lấy planned_duration từ đạo diễn (nếu có)
                # Nếu không có, tính từ srt_range
                planned_duration = shot.get("planned_duration")

                # DEBUG: Log xem AI có trả về planned_duration không
                if planned_duration:
                    self.logger.debug(f"Shot {scene_id}: AI returned planned_duration={planned_duration}")
                else:
                    self.logger.warning(f"Shot {scene_id}: AI did NOT return planned_duration, will calculate from timestamps")

                if not planned_duration:
                    # Fallback: tính từ timestamps
                    try:
                        start_parts = start_time.split(",")[0].split(":")
                        end_parts = end_time.split(",")[0].split(":")
                        start_secs = int(start_parts[0]) * 3600 + int(start_parts[1]) * 60 + int(start_parts[2])
                        end_secs = int(end_parts[0]) * 3600 + int(end_parts[1]) * 60 + int(end_parts[2])
                        planned_duration = end_secs - start_secs
                    except:
                        planned_duration = 5  # Default 5 seconds

                # CRITICAL: Clamp planned_duration to max 8 seconds (technical limit)
                MAX_DURATION = 8
                if planned_duration > MAX_DURATION:
                    self.logger.warning(
                        f"Shot {scene_id}: planned_duration {planned_duration}s exceeds max {MAX_DURATION}s, clamping"
                    )
                    planned_duration = MAX_DURATION

                scene = {
                    "scene_id": scene_id,
                    "srt_start": start_time,
                    "srt_end": end_time,
                    "start_time": start_time,
                    "end_time": end_time,
                    "planned_duration": planned_duration,  # Thời lượng đạo diễn quyết định
                    "emotional_weight": shot.get("emotional_weight", ""),  # Mức độ quan trọng cảm xúc
                    "text": shot.get("srt_text", ""),
                    "scene_type": part.get("part_name", "SCENE"),
                    "location_id": location_id,
                    "characters_in_scene": characters_used,
                    "characters_used": characters_used,  # Thêm trực tiếp cho Excel
                    "location_used": location_id,  # Thêm trực tiếp cho Excel
                    "reference_files": ref_files,
                    "img_prompt": shot.get("img_prompt", ""),
                    "shot_type": shot.get("shot_type", ""),
                    "camera_angle": shot.get("camera_angle", ""),
                    "visual_description": shot.get("visual_description", ""),
                    "purpose": shot.get("purpose", ""),  # Mục đích của shot
                    # Đánh dấu đã có prompt từ đạo diễn
                    "from_director": True
                }

                # Filter children from reference_files
                scene["reference_files"] = self._filter_children_from_refs(scene["reference_files"])

                # VALIDATE & FIX: Check if action matches location in img_prompt
                srt_text = shot.get("srt_text", "")
                fixed_prompt = self._validate_and_fix_location(scene["img_prompt"], srt_text)
                if fixed_prompt != scene["img_prompt"]:
                    self.logger.warning(f"[Validation] Fixed location mismatch in scene {scene_id}")
                    scene["img_prompt"] = fixed_prompt

                scenes.append(scene)
                scene_id += 1

        self.logger.info(f"[Director] Đã chuyển đổi {len(scenes)} shots thành scenes")
        return scenes

    def _validate_and_fix_location(self, img_prompt: str, srt_text: str) -> str:
        """
        Validate và fix location mismatch trong img_prompt.

        Ví dụ lỗi: "LYING IN BED... hotel hallway" → Sửa thành "LYING IN BED... bedroom"

        Args:
            img_prompt: Prompt từ AI
            srt_text: Text gốc từ SRT (để xác định location đúng)

        Returns:
            Fixed img_prompt
        """
        if not img_prompt:
            return img_prompt

        import re
        prompt_lower = img_prompt.lower()
        srt_lower = srt_text.lower() if srt_text else ""

        # Định nghĩa các action → location mappings
        action_location_rules = [
            # BED actions → must be bedroom
            {
                "actions": ["lying in bed", "on the bed", "in bed", "bedroom", "on bed", "fell off the bed", "jumped up from bed"],
                "wrong_locations": ["hallway", "corridor", "street", "outdoor", "kitchen", "office", "restaurant"],
                "correct_location": "master bedroom, king-sized bed with silk sheets, elegant furniture, soft ambient lighting"
            },
            # KITCHEN actions → must be kitchen
            {
                "actions": ["cooking", "in the kitchen", "at the stove", "preparing food"],
                "wrong_locations": ["bedroom", "hallway", "office", "outdoor", "street"],
                "correct_location": "modern kitchen interior, stove, countertops, cooking utensils, warm lighting"
            },
            # BATHROOM actions
            {
                "actions": ["shower", "bathtub", "bathroom", "brushing teeth", "mirror"],
                "wrong_locations": ["bedroom", "kitchen", "office", "outdoor", "hallway"],
                "correct_location": "elegant bathroom, marble tiles, mirror, soft lighting"
            },
            # OUTDOOR actions
            {
                "actions": ["walking on street", "driving", "in the car", "outdoor", "park", "garden"],
                "wrong_locations": ["bedroom", "kitchen", "bathroom", "office interior"],
                "correct_location": "outdoor scene, natural daylight"
            },
            # RESTAURANT actions
            {
                "actions": ["dining", "at restaurant", "eating dinner", "at the table"],
                "wrong_locations": ["bedroom", "bathroom", "street", "office"],
                "correct_location": "elegant restaurant interior, dining tables, ambient lighting"
            },
        ]

        # Check SRT text for location hints
        srt_location_hints = {
            "bed": "bedroom",
            "bedroom": "bedroom",
            "master bedroom": "master bedroom",
            "kitchen": "kitchen",
            "bathroom": "bathroom",
            "restaurant": "restaurant",
            "office": "office",
            "car": "car interior",
            "street": "street",
            "courthouse": "courthouse",
            "hospital": "hospital",
        }

        # Find if SRT mentions a specific location
        srt_location = None
        for hint, loc in srt_location_hints.items():
            if hint in srt_lower:
                srt_location = loc
                break

        # Check each rule
        for rule in action_location_rules:
            # Check if any action is in the prompt
            action_found = any(action in prompt_lower for action in rule["actions"])

            if action_found:
                # Check if wrong location is in the prompt
                for wrong_loc in rule["wrong_locations"]:
                    if wrong_loc in prompt_lower:
                        self.logger.warning(f"[Validation] Action/Location mismatch: action implies {rule['actions'][0]} but found '{wrong_loc}'")

                        # Use SRT location if available, otherwise use rule's correct location
                        correct_loc = rule["correct_location"]
                        if srt_location:
                            if srt_location == "bedroom" or srt_location == "master bedroom":
                                correct_loc = "master bedroom, king-sized bed with silk sheets, elegant nightstands, soft warm lighting"
                            elif srt_location == "kitchen":
                                correct_loc = "modern kitchen, marble countertops, stainless steel appliances, warm lighting"

                        # Replace the wrong location with correct one
                        # Find the sentence containing the wrong location and replace
                        pattern = re.compile(r'[^.]*' + re.escape(wrong_loc) + r'[^.]*\.?', re.IGNORECASE)
                        fixed = pattern.sub(correct_loc + '. ', img_prompt)

                        # Clean up double spaces/periods
                        fixed = re.sub(r'\s+', ' ', fixed)
                        fixed = re.sub(r'\.\.+', '.', fixed)

                        return fixed.strip()

        return img_prompt

    def _load_prompt_template(self, prompt_name: str) -> Optional[str]:
        """Load a specific prompt template from prompts.yaml"""
        try:
            import yaml
            prompts_path = Path(__file__).parent.parent / "config" / "prompts.yaml"
            with open(prompts_path, 'r', encoding='utf-8') as f:
                prompts = yaml.safe_load(f)
            return prompts.get(prompt_name, None)
        except Exception as e:
            self.logger.error(f"Failed to load prompt {prompt_name}: {e}")
            return None

    def _smart_divide_scenes(self, srt_entries: List, characters: List = None, locations: List = None, directors_treatment: Dict = None, global_style: str = "") -> List[Dict[str, Any]]:
        """
        Chia scene theo hướng: TIME-BASED trước (max 8s), rồi AI phân tích nội dung.
        Sử dụng Director's Treatment để hướng dẫn visual strategy.

        Flow mới:
        1. Chia SRT thành các nhóm <= 8s (time-based, đảm bảo chính xác)
        2. AI phân tích nội dung mỗi nhóm → xác định location + visual_moment (dựa trên Director's Treatment)
        3. Sử dụng thông tin characters/locations đã phân tích để tạo visual chính xác

        Args:
            srt_entries: List các SrtEntry từ file SRT
            characters: List các Character đã phân tích
            locations: List các Location đã phân tích
            global_style: Global style string for consistent image styling

        Returns:
            List các scene data với: scene_id, start_time, end_time, text, srt_start, srt_end
        """
        characters = characters or []
        locations = locations or []

        # BƯỚC 1: Chia theo thời gian trước (max 8s/scene) - CHÍNH XÁC
        self.logger.info("Bước 1: Chia SRT theo thời gian (max 8s/scene)...")
        time_based_scenes = group_srt_into_scenes(
            srt_entries,
            min_duration=self.min_scene_duration,
            max_duration=self.max_scene_duration
        )
        self.logger.info(f"Đã chia thành {len(time_based_scenes)} scenes (max 8s/scene)")

        # DEBUG: Log từng scene để kiểm tra
        for i, s in enumerate(time_based_scenes):
            duration = 0
            if isinstance(s.get("start_time"), timedelta) and isinstance(s.get("end_time"), timedelta):
                duration = (s["end_time"] - s["start_time"]).total_seconds()
            self.logger.info(f"  Scene {i+1}: {s.get('srt_start', '')} -> {s.get('srt_end', '')} ({duration:.1f}s)")

        # VALIDATE: Nếu có scene nào > max_duration, chia lại
        needs_resplit = False
        for s in time_based_scenes:
            if isinstance(s.get("start_time"), timedelta) and isinstance(s.get("end_time"), timedelta):
                duration = (s["end_time"] - s["start_time"]).total_seconds()
                if duration > self.max_scene_duration + 0.5:  # +0.5 tolerance
                    self.logger.warning(f"Scene {s.get('scene_id')} duration={duration:.1f}s > {self.max_scene_duration}s!")
                    needs_resplit = True
                    break

        if needs_resplit:
            self.logger.warning("Re-splitting scenes to enforce max duration...")
            time_based_scenes = self._force_split_scenes(time_based_scenes, srt_entries)

        # BƯỚC 2: AI phân tích nội dung để tạo visual_moment và xác định location
        self.logger.info("Bước 2: AI phân tích nội dung để tạo visual_moment...")

        # Format thông tin characters cho AI
        chars_info = ""
        if characters:
            chars_info = "NHÂN VẬT ĐÃ XÁC ĐỊNH:\n" + "\n".join([
                f"- {c.id}: {c.name} ({c.role}) - {c.character_lock or c.vietnamese_prompt or ''}"
                for c in characters
            ])

        # Format thông tin locations cho AI
        locs_info = ""
        if locations:
            locs_info = "BỐI CẢNH ĐÃ XÁC ĐỊNH:\n" + "\n".join([
                f"- {loc.id}: {loc.name} - {loc.location_lock or ''}"
                for loc in locations
            ])

        # Format scenes cho AI
        scenes_for_ai = "\n".join([
            f"{i+1}. [{s.get('srt_start', '')} -> {s.get('srt_end', '')}] \"{s['text'][:300]}\""
            for i, s in enumerate(time_based_scenes)
        ])

        # Format Director's Treatment cho AI (nếu có)
        treatment_info = ""
        if directors_treatment:
            import json
            treatment_info = json.dumps(directors_treatment, indent=2, ensure_ascii=False)
            self.logger.info(f"[Smart Divide] Using Director's Treatment with {len(directors_treatment.get('story_parts', []))} story parts")

        # Load prompt
        prompt_template = get_smart_divide_scenes_prompt()
        if not prompt_template:
            self.logger.warning("Smart divide prompt not found, returning time-based scenes")
            return self._format_time_based_scenes(time_based_scenes, locations=locations)

        # Get default global style if not provided
        if not global_style:
            global_style = get_global_style()

        # Build full prompt với context + Director's Treatment
        try:
            prompt = prompt_template.format(
                srt_with_timestamps=scenes_for_ai,
                characters_info=chars_info,
                locations_info=locs_info,
                directors_treatment=treatment_info or "No director's treatment available - analyze story structure yourself",
                global_style=global_style
            )
        except KeyError:
            # Fallback nếu template không có placeholder
            prompt = prompt_template.format(srt_with_timestamps=scenes_for_ai, global_style=global_style)
            # Prepend context
            context_parts = []
            if treatment_info:
                context_parts.append(f"DIRECTOR'S TREATMENT:\n{treatment_info}")
            if chars_info:
                context_parts.append(chars_info)
            if locs_info:
                context_parts.append(locs_info)
            if context_parts:
                prompt = "\n\n".join(context_parts) + "\n\n" + prompt

        try:
            response = self._generate_content(prompt, temperature=0.4, max_tokens=8000)
            json_data = self._extract_json(response)

            if not json_data or "scenes" not in json_data:
                self.logger.warning("[Smart Divide] AI không trả về scenes, dùng time-based")
                return self._format_time_based_scenes(time_based_scenes, locations=locations)

            self.logger.info(f"[Smart Divide] AI trả về {len(json_data['scenes'])} scene analyses")

            # Extract NEW locations từ AI (locations chưa có trong danh sách ban đầu)
            new_locations = json_data.get("new_locations", [])
            if new_locations:
                self.logger.info(f"[Smart Divide] AI created {len(new_locations)} NEW locations:")
                for loc in new_locations:
                    self.logger.info(f"  - {loc.get('id')}: {loc.get('name')} - {loc.get('location_lock', '')[:50]}...")
                    # Add to locations list for reference
                    locations.append(Location(
                        id=loc.get("id", ""),
                        name=loc.get("name", ""),
                        english_prompt=loc.get("location_prompt", ""),
                        location_lock=loc.get("location_lock", ""),
                        lighting_default=loc.get("lighting_default", ""),
                        image_file=f"{loc.get('id', 'loc')}.png"
                    ))

            # Merge AI analysis vào time-based scenes
            ai_scenes_map = {s.get("scene_id", i+1): s for i, s in enumerate(json_data["scenes"])}

            final_scenes = []
            for i, scene in enumerate(time_based_scenes):
                scene_id = i + 1
                ai_data = ai_scenes_map.get(scene_id, {})

                # Format timestamps
                start_time = scene.get("srt_start", format_srt_time(scene["start_time"]) if isinstance(scene["start_time"], timedelta) else scene["start_time"])
                end_time = scene.get("srt_end", format_srt_time(scene["end_time"]) if isinstance(scene["end_time"], timedelta) else scene["end_time"])
                duration = scene.get("duration", (scene["end_time"] - scene["start_time"]).total_seconds() if isinstance(scene["start_time"], timedelta) else 5)

                # Lấy characters từ AI analysis
                chars_in_scene = ai_data.get("characters_in_scene", [])
                if not chars_in_scene and characters:
                    # Fallback: dùng nhân vật chính nếu AI không chỉ định
                    chars_in_scene = [characters[0].id] if characters else []

                # Scene type (PRESENT_ACTION, FLASHBACK, NARRATION, YOUTUBE_CTA)
                scene_type = ai_data.get("scene_type", "PRESENT_ACTION")
                age_note = ai_data.get("age_note", "")

                # Ưu tiên img_prompt từ AI, sau đó visual_moment, KHÔNG dùng scene text làm fallback!
                ai_img_prompt = ai_data.get("img_prompt", "")
                ai_visual_moment = ai_data.get("visual_moment", "")

                # KHÔNG DÙNG scene["text"] làm fallback - sẽ gây narration trong prompt!
                final_visual = ai_img_prompt or ai_visual_moment or ""

                # Map AI's location_id to actual location ID
                ai_location = ai_data.get("location_id", "")
                actual_location_id = ""
                if ai_location:
                    # Kiểm tra nếu ai_location là ID thực tế trong locations list
                    for loc in locations:
                        if loc.id == ai_location:
                            actual_location_id = ai_location
                            break
                    # Nếu không tìm thấy, có thể AI trả về generic (loc1, loc2...)
                    if not actual_location_id and locations:
                        import re
                        match = re.match(r'loc(\d+)', ai_location)
                        if match:
                            idx = int(match.group(1)) - 1  # loc1 -> index 0
                            if 0 <= idx < len(locations):
                                actual_location_id = locations[idx].id
                        # Fallback: dùng location đầu tiên
                        if not actual_location_id:
                            actual_location_id = locations[0].id
                elif locations:
                    # AI không trả về location, dùng location đầu tiên
                    actual_location_id = locations[0].id

                final_scenes.append({
                    "scene_id": scene_id,
                    "scene_type": scene_type,  # NEW: Type of scene
                    "age_note": age_note,      # NEW: Age adjustment for flashbacks
                    "location_id": actual_location_id,
                    "characters_in_scene": chars_in_scene,
                    "story_beat": ai_data.get("story_beat", ""),
                    "start_time": start_time,
                    "end_time": end_time,
                    "duration": round(duration, 2),  # Duration từ SRT timing (3-8s)
                    "text": scene["text"],  # Keep for subtitle reference
                    "visual_moment": final_visual,  # Use AI visual, NOT narration!
                    "img_prompt": ai_img_prompt,  # NEW: Direct img_prompt from AI
                    "shot_type": ai_data.get("shot_type", "Medium shot"),
                    "srt_start": start_time,
                    "srt_end": end_time,
                })

            self.logger.info(f"Hoàn thành: {len(final_scenes)} scenes với visual_moment từ AI")
            return final_scenes

        except Exception as e:
            self.logger.error(f"AI analysis failed: {e}, returning time-based scenes")
            return self._format_time_based_scenes(time_based_scenes, locations=locations)

    def _force_split_scenes(self, scenes: List[Dict], srt_entries: List) -> List[Dict]:
        """Force split scenes that exceed max_duration."""
        result = []
        scene_counter = 1

        for scene in scenes:
            if not isinstance(scene.get("start_time"), timedelta):
                scene["scene_id"] = scene_counter
                result.append(scene)
                scene_counter += 1
                continue

            duration = (scene["end_time"] - scene["start_time"]).total_seconds()

            if duration <= self.max_scene_duration + 0.5:
                scene["scene_id"] = scene_counter
                result.append(scene)
                scene_counter += 1
            else:
                # Scene quá dài, cần chia nhỏ
                srt_indices = scene.get("srt_indices", [])
                if srt_indices:
                    # Tìm SRT entries cho scene này
                    scene_entries = [e for e in srt_entries if e.index in srt_indices]
                    if scene_entries:
                        # Chia lại với max_duration nhỏ hơn
                        sub_scenes = group_srt_into_scenes(
                            scene_entries,
                            min_duration=1,  # Bỏ qua min để đảm bảo max
                            max_duration=self.max_scene_duration
                        )
                        for sub in sub_scenes:
                            sub["scene_id"] = scene_counter
                            result.append(sub)
                            scene_counter += 1
                        continue

                # Fallback: chia đều theo thời gian
                num_parts = int(duration / self.max_scene_duration) + 1
                part_duration = duration / num_parts
                start_sec = scene["start_time"].total_seconds()

                for i in range(num_parts):
                    part_start = timedelta(seconds=start_sec + i * part_duration)
                    part_end = timedelta(seconds=min(start_sec + (i + 1) * part_duration, scene["end_time"].total_seconds()))

                    result.append({
                        "scene_id": scene_counter,
                        "start_time": part_start,
                        "end_time": part_end,
                        "text": scene["text"],
                        "srt_start": format_srt_time(part_start),
                        "srt_end": format_srt_time(part_end),
                        "srt_indices": scene.get("srt_indices", []),
                    })
                    scene_counter += 1

        return result

    def _format_time_based_scenes(self, time_based_scenes: List[Dict], default_char: str = "nvc", locations: List = None) -> List[Dict[str, Any]]:
        """Format time-based scenes khi không có AI analysis."""
        # Lấy actual location ID từ locations list, không dùng generic loc1
        actual_location_id = ""
        if locations and len(locations) > 0:
            actual_location_id = locations[0].id

        formatted = []
        for i, scene in enumerate(time_based_scenes):
            start_time = format_srt_time(scene["start_time"]) if isinstance(scene["start_time"], timedelta) else scene.get("srt_start", "00:00:00,000")
            end_time = format_srt_time(scene["end_time"]) if isinstance(scene["end_time"], timedelta) else scene.get("srt_end", "00:00:08,000")
            duration = (scene["end_time"] - scene["start_time"]).total_seconds() if isinstance(scene["start_time"], timedelta) else 5

            formatted.append({
                "scene_id": i + 1,
                "location_id": actual_location_id,
                "characters_in_scene": [default_char],  # Default: nhân vật chính
                "story_beat": "",
                "start_time": start_time,
                "end_time": end_time,
                "duration": round(duration, 2),  # Duration từ SRT timing (3-8s)
                "text": scene["text"],
                "visual_moment": scene["text"],
                "shot_type": "Medium shot",
                "srt_start": start_time,
                "srt_end": end_time,
            })
        return formatted

    def _validate_and_split_scenes(self, scenes_data: List[Dict], srt_entries: List) -> List[Dict[str, Any]]:
        """
        Validate và chia lại những scene vượt quá max_duration.
        LUÔN check duration từ timestamps, không phụ thuộc srt_indices.

        Args:
            scenes_data: List scenes từ AI
            srt_entries: List SrtEntry gốc

        Returns:
            List scenes đã được validate và split nếu cần
        """
        def parse_time_to_seconds(ts: str) -> float:
            """Convert timestamp string "HH:MM:SS,mmm" hoặc "HH:MM:SS" to seconds."""
            if not ts:
                return 0
            ts = ts.replace(",", ".")
            parts = ts.split(":")
            if len(parts) == 3:
                h, m, s = parts
                return int(h) * 3600 + int(m) * 60 + float(s)
            return 0

        # Build lookup table: srt_index -> SrtEntry
        srt_lookup = {e.index: e for e in srt_entries}

        validated = []
        scene_counter = 1

        for scene in scenes_data:
            # LUÔN tính duration từ timestamps
            start_str = scene.get("start_time", "00:00:00")
            end_str = scene.get("end_time", "00:00:00")
            duration_from_ts = parse_time_to_seconds(end_str) - parse_time_to_seconds(start_str)

            # Lấy duration_seconds từ AI (fallback)
            duration = scene.get("duration_seconds", duration_from_ts) or duration_from_ts

            self.logger.debug(f"Scene {scene.get('scene_id')}: {start_str} -> {end_str} = {duration:.1f}s")

            if duration <= self.max_scene_duration:
                # Duration OK, giữ nguyên
                scene["scene_id"] = scene_counter
                scene["duration_seconds"] = duration
                scene["srt_start"] = start_str if "," in start_str else f"{start_str},000"
                scene["srt_end"] = end_str if "," in end_str else f"{end_str},000"
                validated.append(scene)
                scene_counter += 1
            else:
                # Duration > max, cần chia nhỏ
                self.logger.warning(f"Scene {scene.get('scene_id')} duration={duration:.1f}s > {self.max_scene_duration}s, SPLITTING!")

                # Tìm SRT entries trong khoảng thời gian này
                start_sec = parse_time_to_seconds(start_str)
                end_sec = parse_time_to_seconds(end_str)

                scene_entries = []
                for entry in srt_entries:
                    entry_start = entry.start_time.total_seconds()
                    entry_end = entry.end_time.total_seconds()
                    # Entry nằm trong khoảng scene
                    if entry_start >= start_sec - 0.5 and entry_end <= end_sec + 0.5:
                        scene_entries.append(entry)

                if scene_entries:
                    # Có SRT entries → chia theo entries
                    sub_scenes = group_srt_into_scenes(
                        scene_entries,
                        min_duration=self.min_scene_duration,
                        max_duration=self.max_scene_duration
                    )

                    for sub in sub_scenes:
                        sub_start = format_srt_time(sub["start_time"]) if isinstance(sub["start_time"], timedelta) else sub["start_time"]
                        sub_end = format_srt_time(sub["end_time"]) if isinstance(sub["end_time"], timedelta) else sub["end_time"]
                        sub_duration = (sub["end_time"] - sub["start_time"]).total_seconds() if isinstance(sub["start_time"], timedelta) else 5

                        validated.append({
                            "scene_id": scene_counter,
                            "location_id": scene.get("location_id", ""),
                            "story_beat": scene.get("story_beat", ""),
                            "start_time": sub_start,
                            "end_time": sub_end,
                            "duration": round(sub_duration, 2),  # Duration từ SRT timing
                            "text": sub["text"],
                            "visual_moment": scene.get("visual_moment", sub["text"]),
                            "shot_type": scene.get("shot_type", "Medium shot"),
                            "srt_start": sub_start,
                            "srt_end": sub_end,
                        })
                        scene_counter += 1
                else:
                    # Không tìm được entries → chia đều theo thời gian
                    self.logger.warning(f"No SRT entries found for scene, splitting evenly by time")
                    num_parts = int(duration / self.max_scene_duration) + 1
                    part_duration = duration / num_parts
                    original_text = scene.get("text", "")

                    for i in range(num_parts):
                        part_start_sec = start_sec + (i * part_duration)
                        part_end_sec = min(start_sec + ((i + 1) * part_duration), end_sec)

                        # Convert to timestamp string
                        def sec_to_ts(sec):
                            h = int(sec // 3600)
                            m = int((sec % 3600) // 60)
                            s = sec % 60
                            return f"{h:02d}:{m:02d}:{s:06.3f}".replace(".", ",")

                        validated.append({
                            "scene_id": scene_counter,
                            "location_id": scene.get("location_id", ""),
                            "story_beat": scene.get("story_beat", ""),
                            "start_time": sec_to_ts(part_start_sec),
                            "end_time": sec_to_ts(part_end_sec),
                            "duration": round(part_end_sec - part_start_sec, 2),  # Duration từ split
                            "text": original_text,
                            "visual_moment": scene.get("visual_moment", original_text),
                            "shot_type": scene.get("shot_type", "Medium shot"),
                            "srt_start": sec_to_ts(part_start_sec),
                            "srt_end": sec_to_ts(part_end_sec),
                        })
                        scene_counter += 1

        return validated

    def _fallback_time_based_division(self, srt_entries: List) -> List[Dict[str, Any]]:
        """Fallback: chia scene theo thời gian khi AI không hoạt động."""
        from modules.utils import group_srt_into_scenes
        return group_srt_into_scenes(
            srt_entries,
            min_duration=self.min_scene_duration,
            max_duration=self.max_scene_duration
        )

    def _generate_scene_prompts(
        self,
        characters: List[Character],
        scenes_data: List[Dict[str, Any]],
        context_lock: str = "",
        locations: List[Location] = None,
        global_style_override: str = ""
    ) -> List[Dict[str, str]]:
        """
        Tạo prompts cho một batch scenes.

        Args:
            characters: Danh sách nhân vật
            scenes_data: Danh sách scene data
            context_lock: Context lock string từ phân tích nhân vật
            locations: Danh sách locations
            global_style_override: Global style từ AI (nếu có)

        Returns:
            List các dict chứa img_prompt và video_prompt
        """
        locations = locations or []

        # Format thông tin nhân vật (v5.0 format) - use character_lock for scene prompts
        # IMPORTANT: character_lock is the short description for scenes, NOT english_prompt (portrait_prompt)
        # NEVER use portrait_prompt (english_prompt) for scenes - it has "white studio background"!
        def get_char_lock(char):
            """Get character_lock, extract from english_prompt if needed (remove white background)."""
            if char.character_lock and char.character_lock.strip():
                return char.character_lock
            # Fallback: extract basic description from english_prompt (remove studio/background refs)
            if char.english_prompt:
                prompt = char.english_prompt
                # Remove studio background references
                for phrase in ["Pure white studio background", "white studio background",
                               "Bright, even studio lighting", "studio lighting",
                               "Looking directly at camera", "neutral expression",
                               "8K, sharp focus", "high fidelity portraiture"]:
                    prompt = prompt.replace(phrase, "").replace(phrase.lower(), "")
                # Clean up
                prompt = " ".join(prompt.split())  # Remove extra spaces
                if len(prompt) > 20:  # Only use if meaningful
                    return prompt
            return f"{char.name} ({char.role})"  # Ultimate fallback

        characters_info = "\n".join([
            f"- ID: {char.id}\n"
            f"  Name: {char.name} ({char.role})\n"
            f"  character_lock: \"{get_char_lock(char)}\"\n"
            f"  reference_file: {char.id}.png"
            for char in characters
        ])

        # Format thông tin locations (v5.0 format) - include location_lock for AI to copy
        if locations:
            locations_info = "\n".join([
                f"- ID: {loc.id}\n"
                f"  Name: {loc.name}\n"
                f"  location_lock: \"{loc.location_lock}\"\n"
                f"  lighting: {loc.lighting_default}\n"
                f"  reference_file: {loc.id}.png"
                for loc in locations
            ])
        else:
            locations_info = "(No location references - describe locations based on story context)"

        # Kiểm tra xem có scenes nào đã có img_prompt từ smart_divide_scenes không
        scenes_with_prompts = [s for s in scenes_data if s.get("img_prompt")]
        if scenes_with_prompts and len(scenes_with_prompts) == len(scenes_data):
            # TẤT CẢ scenes đã có img_prompt từ smart_divide_scenes - dùng trực tiếp!
            self.logger.info(f"[Scene Prompts] All {len(scenes_data)} scenes already have img_prompt from smart_divide_scenes - using directly!")
            result = []
            for s in scenes_data:
                result.append({
                    "img_prompt": s.get("img_prompt", ""),
                    "video_prompt": s.get("img_prompt", ""),
                    "characters_used": s.get("characters_in_scene", []),
                    "location_used": s.get("location_id", ""),
                    "reference_files": []  # Will be populated later
                })
            return result

        # Format thông tin scenes (include location_id, story_beat, shot_type and visual_moment)
        # KHÔNG dùng s['text'] làm fallback cho visual_moment!
        pacing_script = "\n".join([
            f"{s['scene_id']}. [{s.get('shot_type', 'Medium shot')}] \"{s['text']}\"\n"
            f"   Scene Type: {s.get('scene_type', 'FRAME_PRESENT')}\n"
            f"   Location: {s.get('location_id', 'N/A')}\n"
            f"   Characters: {s.get('characters_in_scene', [])}\n"
            f"   Story beat: {s.get('story_beat', 'N/A')}\n"
            f"   Visual hint: {s.get('visual_moment', '') or '(Create visual based on narration meaning)'}"
            for s in scenes_data
        ])

        # Load global style - uu tien tu AI response
        global_style = global_style_override or get_global_style()

        # Load prompt từ config/prompts.yaml
        prompt_template = get_generate_scenes_prompt()

        # Try to format with all variables (v5.0 format)
        try:
            prompt = prompt_template.format(
                characters_info=characters_info,
                scenes_info=pacing_script,  # for backwards compat
                pacing_script=pacing_script,
                context_lock=context_lock or "Modern setting, natural lighting",
                global_style=global_style,
                locations_info=locations_info
            )
        except KeyError as e:
            # Fallback to simpler format
            self.logger.warning(f"Template format error: {e}, using simple format")
            prompt = f"""Create image prompts for these scenes:

Characters:
{characters_info}

Locations:
{locations_info}

Scenes:
{pacing_script}

Context: {context_lock or "Modern setting"}
Style: {global_style}

Return JSON: {{"scenes": [{{"scene_id": 1, "img_prompt": "...", "video_prompt": "..."}}]}}"""
        
        try:
            self.logger.info(f"[Scene Prompts] Generating for {len(scenes_data)} scenes...")
            response = self._generate_content(prompt, temperature=0.6)

            # Parse JSON
            json_data = self._extract_json(response)

            if not json_data or "scenes" not in json_data:
                self.logger.warning(f"[Scene Prompts] Invalid response - no 'scenes' key in JSON")
                self.logger.warning(f"[Scene Prompts] Raw response (first 500 chars): {str(response)[:500]}")
                # Return FALLBACK prompts (không để trống!)
                return self._create_fallback_prompts(scenes_data, characters, locations, global_style)

            self.logger.info(f"[Scene Prompts] Got {len(json_data['scenes'])} scene prompts from AI (need {len(scenes_data)})")

            # Kiểm tra nếu AI trả về thiếu prompts
            if len(json_data['scenes']) < len(scenes_data):
                self.logger.warning(f"[Scene Prompts] AI THIẾU {len(scenes_data) - len(json_data['scenes'])} prompts!")

            # Match prompts với scenes
            prompts_map = {s["scene_id"]: s for s in json_data["scenes"]}

            result = []
            missing_scenes = []
            for scene_data in scenes_data:
                scene_id = scene_data["scene_id"]
                if scene_id in prompts_map:
                    scene_result = prompts_map[scene_id]

                    # POST-PROCESS: Clean any narration text from img_prompt
                    img_prompt = scene_result.get("img_prompt", "")
                    scene_text = scene_data.get("text", "")[:100]  # First 100 chars of narration
                    img_prompt = self._clean_narration_from_prompt(img_prompt, scene_text)

                    result.append({
                        "img_prompt": img_prompt,
                        "video_prompt": scene_result.get("video_prompt", ""),
                        "characters_used": scene_result.get("characters_used", []),
                        "location_used": scene_result.get("location_used", ""),
                        "reference_files": scene_result.get("reference_files", [])
                    })
                else:
                    # Scene không có prompt từ AI - dùng fallback THÔNG MINH
                    missing_scenes.append(scene_id)
                    # GỌI FALLBACK METHOD thay vì dùng scene_text trực tiếp!
                    # Fallback sẽ tạo prompt dựa trên scene_type, KHÔNG dùng narration text
                    fallback_prompts = self._create_fallback_prompts(
                        [scene_data], characters, locations, global_style_override
                    )
                    if fallback_prompts:
                        result.append(fallback_prompts[0])
                    else:
                        # Ultimate fallback - generic nhưng KHÔNG có narration text
                        result.append({
                            "img_prompt": f"Medium shot, cinematic scene, natural lighting, 4K photorealistic",
                            "video_prompt": f"Medium shot, cinematic scene",
                            "characters_used": [],
                            "location_used": "",
                            "reference_files": []
                        })

            if missing_scenes:
                self.logger.warning(f"[Scene Prompts] Đã tạo prompt đơn giản cho {len(missing_scenes)} scenes thiếu: {missing_scenes}")

            return result
            
        except Exception as e:
            self.logger.error(f"Failed to generate scene prompts: {e}")
            # Return FALLBACK prompts (không để trống!)
            return self._create_fallback_prompts(scenes_data, characters, locations, global_style)

    def _create_fallback_prompts(
        self,
        scenes_data: List[Dict],
        characters: List = None,
        locations: List = None,
        global_style: str = ""
    ) -> List[Dict[str, str]]:
        """Tạo fallback prompts khi AI không trả về đúng.

        IMPORTANT: KHÔNG đưa narration/dialogue text vào img_prompt!
        img_prompt chỉ chứa mô tả hình ảnh (visual description).
        """
        self.logger.info(f"[Fallback] Tạo {len(scenes_data)} fallback prompts...")
        characters = characters or []
        locations = locations or []

        # Build character description map - THEM FILENAME ANNOTATION de Flow match anh reference
        char_desc = {}
        for c in characters:
            desc = c.character_lock or c.vietnamese_prompt or f"{c.name}"
            # Them annotation filename: "A 30-year-old man (nvc.png)"
            char_desc[c.id] = f"{desc} ({c.id}.png)"

        # Build location description map - THEM FILENAME ANNOTATION
        loc_desc = {}
        for loc in locations:
            desc = loc.location_lock or loc.name
            # Them annotation filename: "Modern apartment living room (loc_apartment.png)"
            loc_desc[loc.id] = f"{desc} ({loc.id}.png)"

        style_suffix = global_style or "Cinematic, 4K photorealistic, natural lighting"

        # Shot type rotation for variety (HOOK + variety pattern)
        shot_types_hook = ["WIDE LOW ANGLE", "EXTREME CLOSE-UP", "EXTREME CLOSE-UP"]  # First 3 scenes
        shot_types_cycle = ["WIDE", "CLOSE-UP", "MEDIUM", "EXTREME CLOSE-UP", "LOW ANGLE", "TWO-SHOT"]

        result = []
        for idx, scene in enumerate(scenes_data):
            # Get scene info
            # IMPORTANT: Ưu tiên visual_moment (đã được AI xử lý), KHÔNG dùng text (narration)!
            visual_moment = scene.get("visual_moment", "")
            scene_type = scene.get("scene_type", "FRAME_PRESENT")

            # SHOT TYPE VARIETY - Critical for cinematic feel!
            if scene.get("shot_type") and scene.get("shot_type") != "Medium shot":
                shot_type = scene.get("shot_type")
            elif idx < 3:
                # HOOK scenes (1-3) - must be dramatic!
                shot_type = shot_types_hook[idx]
            else:
                # Cycle through variety for remaining scenes
                shot_type = shot_types_cycle[(idx - 3) % len(shot_types_cycle)]

            # Map location_id to actual location ID, không dùng generic loc1
            raw_location = scene.get("location_id", "")
            location_id = ""
            if raw_location:
                # Kiểm tra nếu raw_location là ID thực tế trong locations list
                for loc in locations:
                    if loc.id == raw_location:
                        location_id = raw_location
                        break
                # Nếu không tìm thấy, có thể là generic (loc1, loc2...)
                if not location_id and locations:
                    import re
                    match = re.match(r'loc(\d+)', raw_location)
                    if match:
                        idx_loc = int(match.group(1)) - 1  # loc1 -> index 0
                        if 0 <= idx_loc < len(locations):
                            location_id = locations[idx_loc].id
                    # Fallback: dùng location đầu tiên
                    if not location_id:
                        location_id = locations[0].id
            elif locations:
                # Không có location trong scene data, dùng location đầu tiên
                location_id = locations[0].id

            chars_in_scene = scene.get("characters_in_scene", [])

            # Build character part
            char_parts = []
            for char_id in chars_in_scene:
                if char_id in char_desc:
                    char_parts.append(char_desc[char_id])

            # Build location part
            loc_part = loc_desc.get(location_id, "")

            # Build prompt - ONLY VISUAL DESCRIPTION, NO NARRATION TEXT!
            parts = [shot_type]

            # Character description - chọn đúng nhân vật theo scene_type!
            if char_parts:
                parts.append(", ".join(char_parts[:2]))  # Max 2 characters
            elif characters:
                # Chọn nhân vật PHÙ HỢP dựa trên scene_type
                if scene_type == "CHILDHOOD_FLASHBACK":
                    # Mẹ trẻ + con nhỏ
                    young_mother = char_desc.get("nv1_young", char_desc.get("nv1", "32-year-old mother"))
                    child = char_desc.get("nvc1", "8-year-old boy")
                    parts.append(f"{young_mother}, with {child}")
                elif scene_type == "ADULT_FLASHBACK":
                    # Narrator trẻ (25-28 tuổi)
                    young_narrator = char_desc.get("nvc_young", char_desc.get("nvc", "25-year-old man"))
                    parts.append(young_narrator)
                else:
                    # FRAME_PRESENT, EMOTIONAL_BEAT - narrator hiện tại
                    parts.append(char_desc.get("nvc", characters[0].character_lock or characters[0].name))

            # Visual moment - ONLY if it's actually visual (not narration)
            # Check if visual_moment looks like narration (contains certain patterns)
            if visual_moment and not self._looks_like_narration(visual_moment):
                parts.append(visual_moment[:200])
            else:
                # Create STORY-AWARE visual based on scene_type and scene text
                scene_text = scene.get("text", "").lower()

                # [HOT] HOOK SCENES (1-3) - CRITICAL FOR VIEWER RETENTION!
                # These scenes need EXTRA dramatic visuals to hook viewers immediately
                if idx < 3:
                    hook_visual = self._create_hook_visual(idx, scene_text, char_parts, loc_part)
                    if hook_visual:
                        parts.append(hook_visual)
                        # Skip normal processing for HOOK scenes
                        # Location and style will be added below
                        if loc_part:
                            parts.append(loc_part)
                        parts.append(style_suffix)
                        img_prompt = ". ".join([p for p in parts if p])

                        # Build reference_files với actual location ID
                        hook_actual_loc = None
                        if location_id:
                            if location_id in loc_desc:
                                hook_actual_loc = location_id
                            elif locations:
                                hook_actual_loc = locations[0].id

                        all_refs = [f"{c}.png" for c in chars_in_scene]
                        if hook_actual_loc:
                            all_refs.append(f"{hook_actual_loc}.png")

                        filtered_refs, filtered_children = self._filter_children_from_refs(all_refs, return_filtered=True)
                        # Add inline child descriptions
                        if filtered_children:
                            img_prompt = self._add_children_inline_to_prompt(img_prompt, filtered_children, characters)

                        result.append({
                            "img_prompt": img_prompt,
                            "video_prompt": img_prompt,
                            "characters_used": chars_in_scene,
                            "location_used": hook_actual_loc or location_id,
                            "reference_files": filtered_refs
                        })
                        continue  # Skip to next scene

                if scene_type == "CHILDHOOD_FLASHBACK":
                    # Analyze scene text for specific childhood visuals
                    if "work" in scene_text or "job" in scene_text:
                        parts.append("exhausted young mother in work uniform, late evening, warm nostalgic lighting")
                    elif "home" in scene_text or "door" in scene_text:
                        parts.append("tired mother entering small apartment, child waiting, warm embrace, soft tungsten lighting")
                    elif "bed" in scene_text or "sleep" in scene_text:
                        parts.append("mother tucking child into bed, small shared bedroom, dim warm bedside lamp")
                    else:
                        parts.append("warm childhood memory scene, mother and child together, soft nostalgic golden lighting")

                elif scene_type == "ADULT_FLASHBACK":
                    # Analyze scene text for specific adult flashback visuals
                    if "build" in scene_text or "hammer" in scene_text or "nail" in scene_text:
                        parts.append("man hammering wooden beam on house frame, sawdust on face, determined expression, sunny day")
                    elif "land" in scene_text or "buy" in scene_text or "save" in scene_text:
                        parts.append("proud man standing on empty lot holding property deed, hopeful smile, golden sunset")
                    elif "finish" in scene_text or "complete" in scene_text or "done" in scene_text:
                        parts.append("man standing proudly in front of finished house, arms crossed, sense of accomplishment")
                    else:
                        parts.append("young adult working hard toward his dream, hopeful determined expression, natural daylight")

                elif scene_type == "EMOTIONAL_BEAT":
                    # Analyze scene text for emotional context
                    if "believe" in scene_text or "trust" in scene_text:
                        parts.append("extreme close-up of eyes glistening with emotion, distant gaze, painful memory")
                    elif "betray" in scene_text or "court" in scene_text:
                        parts.append("close-up of face showing shock and disbelief, trembling lip, fighting back tears")
                    else:
                        parts.append("close-up contemplative expression, eyes reflecting deep thought, emotional weight visible")

                else:  # FRAME_PRESENT or default
                    # Analyze scene text for present-day context
                    if "court" in scene_text or "legal" in scene_text:
                        parts.append("sitting on courthouse steps, holding legal documents, worried expression, morning light")
                    elif "house" in scene_text or "home" in scene_text:
                        parts.append("standing outside house, conflicted expression, life's work at stake")
                    else:
                        parts.append("present day, contemplative moment, natural lighting, weight of situation visible")

            # Location
            if loc_part:
                parts.append(loc_part)

            # Style
            parts.append(style_suffix)

            img_prompt = ". ".join([p for p in parts if p])

            # Build reference_files, filtering out children (API policy)
            # QUAN TRONG: Dùng location ID thực tế từ locations list, không dùng generic loc1/loc2
            actual_location_id = None
            if location_id:
                # Kiểm tra nếu location_id đã là ID thực (có trong loc_desc)
                if location_id in loc_desc:
                    actual_location_id = location_id
                else:
                    # location_id là generic (loc1, loc2...), tìm location phù hợp từ list
                    # Ưu tiên: dùng location đầu tiên trong list nếu có
                    if locations:
                        actual_location_id = locations[0].id
                        self.logger.debug(f"Scene {idx+1}: Mapped '{location_id}' to '{actual_location_id}'")

            all_refs = [f"{c}.png" for c in chars_in_scene]
            if actual_location_id:
                all_refs.append(f"{actual_location_id}.png")

            filtered_refs, filtered_children = self._filter_children_from_refs(all_refs, return_filtered=True)
            # Add inline child descriptions
            if filtered_children:
                img_prompt = self._add_children_inline_to_prompt(img_prompt, filtered_children, characters)

            result.append({
                "img_prompt": img_prompt,
                "video_prompt": img_prompt,
                "characters_used": chars_in_scene,
                "location_used": actual_location_id or location_id,
                "reference_files": filtered_refs
            })

        self.logger.info(f"[Fallback] Đã tạo {len(result)} fallback prompts")
        return result

    def _looks_like_narration(self, text: str) -> bool:
        """Check if text looks like narration/dialogue rather than visual description.

        Narration patterns:
        - Contains quotes or spoken text
        - Starts with "I ", "My ", "We ", "She ", "He "
        - Contains past tense narrative phrases
        """
        if not text:
            return True

        text_lower = text.lower().strip()

        # Narration indicators
        narration_patterns = [
            # First person narrative
            text_lower.startswith("i "),
            text_lower.startswith("i'"),
            text_lower.startswith("my "),
            text_lower.startswith("we "),
            # Third person narrative
            text_lower.startswith("she "),
            text_lower.startswith("he "),
            text_lower.startswith("they "),
            # Contains dialogue markers
            '"' in text,
            "said" in text_lower,
            "told" in text_lower,
            "asked" in text_lower,
            # Past tense narrative
            "i was" in text_lower,
            "i had" in text_lower,
            "i remember" in text_lower,
            "by the time" in text_lower,
            "years old" in text_lower,
            # YouTube CTA
            "subscribe" in text_lower,
            "like button" in text_lower,
            "comment" in text_lower,
        ]

        return any(narration_patterns)

    def _create_hook_visual(self, scene_idx: int, scene_text: str, char_parts: List[str], loc_part: str) -> str:
        """Create dramatic HOOK visual for scenes 1-3 (idx 0-2).

        HOOK scenes are CRITICAL for viewer retention. They must be:
        - Scene 1 (idx=0): WIDE LOW ANGLE - character DEVASTATED, ISOLATED, overwhelming environment
        - Scene 2 (idx=1): EXTREME CLOSE-UP - face with RAW emotion, tears, pain
        - Scene 3 (idx=2): EXTREME CLOSE-UP - meaningful detail (trembling hands, documents, etc.)

        These visuals MUST include CHARACTER - never empty locations!
        """
        if scene_idx == 0:
            # SCENE 1: THE GRABBER - Tiny figure in overwhelming environment
            # MUST show character in state of devastation/isolation

            # Analyze scene_text for context clues
            if any(w in scene_text for w in ["court", "legal", "lawsuit", "judge"]):
                return "TINY figure hunched ALONE on MASSIVE courthouse steps, head in hands, DEVASTATED posture, dwarfed by towering columns, morning fog, dramatic low angle emphasizing isolation"
            elif any(w in scene_text for w in ["hospital", "doctor", "sick", "ill"]):
                return "TINY figure sitting ALONE in vast hospital corridor, hunched over in despair, fluorescent lights stretching endlessly, dramatic low angle emphasizing vulnerability"
            elif any(w in scene_text for w in ["house", "home", "evict", "lost"]):
                return "TINY figure standing ALONE before house, shoulders slumped in defeat, belongings scattered, dramatic low angle, overwhelming sky"
            elif any(w in scene_text for w in ["grave", "funeral", "death", "die"]):
                return "TINY figure kneeling ALONE at gravestone, hunched in grief, cemetery stretching endlessly, dramatic low angle, overcast sky"
            elif any(w in scene_text for w in ["mother", "mom", "parent"]):
                return "TINY figure sitting ALONE, head bowed in sorrow, clutching photograph of mother, DEVASTATING isolation visible, dramatic low angle"
            else:
                # Default dramatic opening
                return "TINY figure hunched ALONE in vast empty space, ISOLATED and OVERWHELMED, head bowed in despair, dramatic low angle emphasizing human fragility against overwhelming circumstances"

        elif scene_idx == 1:
            # SCENE 2: EMOTIONAL IMPACT - Face with raw emotion
            # MUST show extreme close-up of face with visible emotion

            if any(w in scene_text for w in ["betray", "trust", "lie", "deceive"]):
                return "EXTREME CLOSE-UP of face, eyes glistening with TEARS of betrayal, jaw clenched fighting back sobs, single tear rolling down cheek, raw disbelief visible"
            elif any(w in scene_text for w in ["remember", "memory", "past"]):
                return "EXTREME CLOSE-UP of face, eyes distant and glistening with painful memories, nostalgic ache visible, bittersweet pain etched in expression"
            elif any(w in scene_text for w in ["lost", "gone", "never"]):
                return "EXTREME CLOSE-UP of face, eyes red and swollen, DEVASTATED expression, lips trembling, the weight of loss carved into every feature"
            elif any(w in scene_text for w in ["mother", "mom"]):
                return "EXTREME CLOSE-UP of face, eyes filling with tears thinking of mother, profound grief and longing visible, chin trembling with emotion"
            else:
                # Default emotional close-up
                return "EXTREME CLOSE-UP of face showing RAW EMOTION, eyes glistening with unshed tears, profound pain visible in every feature, intimate and vulnerable moment"

        elif scene_idx == 2:
            # SCENE 3: DETAIL SHOT - Meaningful object or body part
            # Close-up on detail that tells the story

            if any(w in scene_text for w in ["court", "legal", "document", "paper"]):
                return "EXTREME CLOSE-UP of trembling hands clutching crumpled legal documents, knuckles white with tension, papers slightly shaking, desperation visible"
            elif any(w in scene_text for w in ["ring", "wedding", "marriage"]):
                return "EXTREME CLOSE-UP of trembling finger touching wedding ring, the weight of broken vows visible, intimate painful detail"
            elif any(w in scene_text for w in ["photo", "picture", "memory"]):
                return "EXTREME CLOSE-UP of weathered hands holding faded photograph, edges worn from years of touching, precious memory captured"
            elif any(w in scene_text for w in ["mother", "mom"]):
                return "EXTREME CLOSE-UP of hands clutching mother's keepsake, fingers tracing familiar pattern, tears visible on skin, profound connection"
            elif any(w in scene_text for w in ["key", "house", "home"]):
                return "EXTREME CLOSE-UP of trembling hands gripping house keys, knuckles white, the weight of losing everything visible in the tension"
            else:
                # Default detail shot
                return "EXTREME CLOSE-UP of trembling hands, knuckles white with tension, the weight of the moment visible in every crease and tremor, intimate emotional detail"

        return ""  # Should not reach here

    def _clean_narration_from_prompt(self, img_prompt: str, scene_text: str) -> str:
        """Remove any narration/dialogue text that might have been included in img_prompt.

        AI sometimes includes the scene text directly in the prompt, which causes
        image generators to render text as subtitles on the image.

        Args:
            img_prompt: The image prompt from AI
            scene_text: The narration/dialogue text from SRT (first ~100 chars)

        Returns:
            Cleaned img_prompt without narration text
        """
        if not img_prompt or not scene_text:
            return img_prompt

        import re

        # 1. Remove exact match of scene_text (or significant portion)
        # Try to match phrases that are clearly from the narration
        words = scene_text.split()
        if len(words) >= 5:
            # Try to match 5+ consecutive words from narration
            for i in range(len(words) - 4):
                phrase = " ".join(words[i:i+5])
                if phrase.lower() in img_prompt.lower():
                    # Found narration in prompt - try to remove it
                    # Find and remove the sentence containing this phrase
                    pattern = re.compile(r'[^.]*' + re.escape(phrase) + r'[^.]*\.?', re.IGNORECASE)
                    img_prompt = pattern.sub('', img_prompt)
                    self.logger.debug(f"[Clean] Removed narration phrase: '{phrase[:30]}...'")

        # 2. Remove common narration patterns
        narration_patterns = [
            r'By the time I was \d+ years old[^.]*\.?',
            r'I had saved[^.]*\.?',
            r'I decided to[^.]*\.?',
            r'It cost me[^.]*\.?',
            r'I remember[^.]*\.?',
            r'She (told|said|asked)[^.]*\.?',
            r'He (told|said|asked)[^.]*\.?',
            r'"[^"]*"',  # Remove quoted dialogue
        ]

        for pattern in narration_patterns:
            if re.search(pattern, img_prompt, re.IGNORECASE):
                img_prompt = re.sub(pattern, '', img_prompt, flags=re.IGNORECASE)
                self.logger.debug(f"[Clean] Removed pattern: {pattern[:30]}...")

        # 3. Clean up: remove double periods, extra spaces
        img_prompt = re.sub(r'\.\.+', '.', img_prompt)
        img_prompt = re.sub(r'\s+', ' ', img_prompt)
        img_prompt = img_prompt.strip()
        img_prompt = img_prompt.strip('.')

        # 4. If prompt is now too short, return original (something went wrong)
        if len(img_prompt) < 30:
            self.logger.warning(f"[Clean] Prompt too short after cleaning, may need manual review")

        return img_prompt

    def _extract_json(self, text: str) -> Optional[Dict]:
        """
        Trích xuất JSON từ response text.

        Hỗ trợ nhiều format: raw JSON, markdown code block, DeepSeek <think> tags.
        Cũng xử lý JSON bị truncated (chưa đóng đủ braces).
        """
        if not text:
            self.logger.warning("[_extract_json] Empty text received")
            return None

        if not text.strip():
            self.logger.warning("[_extract_json] Text is only whitespace")
            return None

        import re

        # Bước 1: Loại bỏ DeepSeek <think>...</think> tags
        clean_text = re.sub(r'<think>[\s\S]*?</think>', '', text).strip()

        # Kiểm tra sau khi loại bỏ <think> tags
        if not clean_text:
            self.logger.warning("[_extract_json] Text empty after removing <think> tags")
            self.logger.debug(f"[_extract_json] Original text (first 300 chars): {text[:300]}")
            return None

        # Bước 2: Thử parse trực tiếp
        try:
            return json.loads(clean_text)
        except json.JSONDecodeError as e:
            self.logger.warning(f"[_extract_json] Direct parse failed at position {e.pos}: {e.msg}")

        # Bước 3: Thử tìm JSON trong code block ```json ... ```
        # Improved: xử lý cả trường hợp không có closing ```
        json_block = re.search(r'```(?:json)?\s*\n?([\s\S]*?)\n?```', clean_text)
        if json_block:
            block_content = json_block.group(1).strip()
            # Nếu có ``` thừa ở cuối, cắt bỏ
            if block_content.endswith('```'):
                block_content = block_content[:-3].strip()
            try:
                return json.loads(block_content)
            except json.JSONDecodeError as e:
                self.logger.debug(f"[_extract_json] Code block parse failed: {e}")
                # Thử repair JSON trong code block nếu bị truncated
                if block_content.startswith('{'):
                    self.logger.info("[_extract_json] Attempting to repair JSON from code block...")
                    clean_text = block_content  # Dùng nội dung code block cho bước 4
        else:
            # Không tìm thấy code block đóng - thử tìm code block mở không đóng
            json_block_open = re.search(r'```(?:json)?\s*\n?([\s\S]*)', clean_text)
            if json_block_open:
                block_content = json_block_open.group(1).strip()
                # Loại bỏ trailing ``` nếu có
                block_content = re.sub(r'```\s*$', '', block_content).strip()
                self.logger.info(f"[_extract_json] Found unclosed code block, extracted {len(block_content)} chars")
                if block_content.startswith('{'):
                    clean_text = block_content  # Dùng cho bước 4

        # Bước 4: Tìm JSON object bắt đầu bằng { và kết thúc bằng }
        start_idx = clean_text.find('{')
        if start_idx != -1:
            # Đếm balanced braces và brackets
            brace_count = 0
            bracket_count = 0
            end_idx = -1
            last_brace_idx = start_idx

            for i, char in enumerate(clean_text[start_idx:], start_idx):
                if char == '{':
                    brace_count += 1
                    last_brace_idx = i
                elif char == '}':
                    brace_count -= 1
                    last_brace_idx = i
                    if brace_count == 0:
                        end_idx = i
                        break
                elif char == '[':
                    bracket_count += 1
                elif char == ']':
                    bracket_count -= 1

            # Nếu tìm được JSON balanced
            if end_idx > start_idx:
                json_str = clean_text[start_idx:end_idx + 1]
                try:
                    return json.loads(json_str)
                except json.JSONDecodeError as e:
                    self.logger.debug(f"[_extract_json] Balanced parse failed: {e}")
                    # Thử fix trailing commas
                    fixed_json = re.sub(r',(\s*[}\]])', r'\1', json_str)
                    try:
                        return json.loads(fixed_json)
                    except json.JSONDecodeError:
                        pass

            # Bước 4b: JSON bị truncated - thử repair bằng cách đóng braces
            elif brace_count > 0:
                self.logger.warning(f"[_extract_json] JSON truncated! Unclosed braces: {brace_count}, brackets: {bracket_count}")
                # Lấy text từ { đến hết
                json_str = clean_text[start_idx:]

                # Chiến lược repair mạnh mẽ hơn
                repair_attempts = [
                    # Attempt 1: Find last complete scene object and close there
                    lambda s: self._truncate_at_last_complete_scene(s, brace_count, bracket_count),
                    # Attempt 2: Find last complete string value and close
                    lambda s: self._truncate_at_last_complete_value(s, brace_count, bracket_count),
                    # Attempt 3: Simple close with ]} pattern
                    lambda s: self._simple_json_close(s, brace_count, bracket_count),
                ]

                for attempt, repair_fn in enumerate(repair_attempts):
                    try:
                        repaired = repair_fn(json_str)
                        if repaired:
                            result = json.loads(repaired)
                            self.logger.info(f"[_extract_json] Successfully repaired truncated JSON (strategy {attempt + 1})")
                            return result
                    except json.JSONDecodeError as e:
                        self.logger.debug(f"[_extract_json] Repair strategy {attempt + 1} failed: {e.msg}")
                        continue
                    except Exception as e:
                        self.logger.debug(f"[_extract_json] Repair strategy {attempt + 1} error: {e}")
                        continue

                self.logger.warning(f"[_extract_json] Could not repair truncated JSON")

        # Bước 5: Fallback - regex greedy
        json_match = re.search(r'\{[\s\S]*\}', clean_text)
        if json_match:
            try:
                return json.loads(json_match.group(0))
            except json.JSONDecodeError:
                pass

        self.logger.warning(f"[_extract_json] Could not extract JSON. Response length: {len(text)}")
        self.logger.warning(f"[_extract_json] Response starts with: {text[:200] if text else 'EMPTY'}")
        return None

    def _truncate_at_last_complete_scene(self, json_str: str, brace_count: int, bracket_count: int) -> Optional[str]:
        """
        Tìm scene cuối cùng hoàn chỉnh (có đầy đủ img_prompt) và truncate tại đó.
        Director's Shooting Plan trả về array of scenes, mỗi scene có img_prompt.
        """
        import re

        # Tìm tất cả các scene objects hoàn chỉnh (có cả img_prompt với closing quote)
        # Pattern: { ... "img_prompt": "..." ... }
        scene_pattern = r'\{\s*"scene_id"[^}]*"img_prompt"\s*:\s*"[^"]*"[^}]*\}'

        matches = list(re.finditer(scene_pattern, json_str, re.DOTALL))

        if matches:
            # Lấy vị trí kết thúc của scene cuối cùng hoàn chỉnh
            last_match = matches[-1]
            truncated = json_str[:last_match.end()]

            # Đếm lại braces/brackets trong phần truncated
            new_brace_count = truncated.count('{') - truncated.count('}')
            new_bracket_count = truncated.count('[') - truncated.count(']')

            # Đóng JSON
            suffix = ']' * max(0, new_bracket_count) + '}' * max(0, new_brace_count)
            return truncated + suffix

        return None

    def _truncate_at_last_complete_value(self, json_str: str, brace_count: int, bracket_count: int) -> Optional[str]:
        """
        Tìm vị trí cuối cùng có value hoàn chỉnh (string đóng đúng, number, boolean).
        """
        import re

        # Tìm pattern cuối cùng là một value hoàn chỉnh
        # Pattern: "key": "value" hoặc "key": number hoặc "key": true/false/null
        complete_value_patterns = [
            # String value với closing quote, có thể theo sau bởi , hoặc }
            r'"[^"]+"\s*:\s*"[^"]*"(?=\s*[,}\]])',
            # Number value
            r'"[^"]+"\s*:\s*-?\d+\.?\d*(?=\s*[,}\]])',
            # Boolean/null
            r'"[^"]+"\s*:\s*(?:true|false|null)(?=\s*[,}\]])',
            # Array/Object close
            r'[\]}](?=\s*[,}\]])',
        ]

        last_pos = 0
        for pattern in complete_value_patterns:
            for match in re.finditer(pattern, json_str):
                if match.end() > last_pos:
                    last_pos = match.end()

        if last_pos > 0:
            # Truncate tại vị trí này
            truncated = json_str[:last_pos]

            # Xử lý trailing comma
            truncated = re.sub(r',\s*$', '', truncated)

            # Đếm lại braces/brackets
            new_brace_count = truncated.count('{') - truncated.count('}')
            new_bracket_count = truncated.count('[') - truncated.count(']')

            # Đóng JSON
            suffix = ']' * max(0, new_bracket_count) + '}' * max(0, new_brace_count)
            return truncated + suffix

        return None

    def _simple_json_close(self, json_str: str, brace_count: int, bracket_count: int) -> Optional[str]:
        """
        Phương pháp đơn giản: tìm điểm an toàn cuối cùng và đóng.
        """
        import re

        # Tìm vị trí của closing quote cuối cùng (kết thúc một string value)
        # Sau đó truncate tại đó
        last_quote_pos = json_str.rfind('"')

        if last_quote_pos > 0:
            # Kiểm tra xem quote này có phải là closing quote không
            # (không phải escaped và số lượng quotes trước đó là lẻ)
            substr = json_str[:last_quote_pos + 1]

            # Đếm quotes không escaped
            quote_count = len(re.findall(r'(?<!\\)"', substr))

            if quote_count % 2 == 0:
                # Đây là closing quote của một string
                truncated = substr

                # Tìm và xóa incomplete key-value sau string này
                # Pattern: , "incomplete_key  hoặc , incomplete_value
                truncated = re.sub(r',\s*"[^"]*$', '', truncated)
                truncated = re.sub(r',\s*$', '', truncated)

                # Đếm lại braces/brackets
                new_brace_count = truncated.count('{') - truncated.count('}')
                new_bracket_count = truncated.count('[') - truncated.count(']')

                # Đóng JSON
                suffix = ']' * max(0, new_bracket_count) + '}' * max(0, new_brace_count)
                return truncated + suffix

        # Fallback: đơn giản thêm closing braces
        # Loại bỏ phần cuối có thể không hoàn chỉnh
        truncated = re.sub(r',\s*"[^"]*$', '', json_str)  # Incomplete key
        truncated = re.sub(r':\s*"[^"]*$', '""', truncated)  # Incomplete string value -> empty string
        truncated = re.sub(r',\s*$', '', truncated)

        new_brace_count = truncated.count('{') - truncated.count('}')
        new_bracket_count = truncated.count('[') - truncated.count(']')

        suffix = ']' * max(0, new_bracket_count) + '}' * max(0, new_brace_count)
        return truncated + suffix

    def update_excel_prompts_with_annotations(self, excel_path: str) -> bool:
        """
        Update existing Excel prompts with filename annotations.
        Use this to add annotations to prompts that were generated before this feature.

        Args:
            excel_path: Path to Excel file

        Returns:
            True if successful
        """
        from modules.excel_manager import PromptWorkbook

        try:
            excel_path = Path(excel_path)
            if not excel_path.exists():
                self.logger.error(f"Excel file not found: {excel_path}")
                return False

            workbook = PromptWorkbook(excel_path).load_or_create()

            # Load characters and locations
            characters = workbook.get_characters()
            locations = [c for c in characters if c.role == "location"]
            characters = [c for c in characters if c.role != "location"]

            # Update scenes
            scenes = workbook.get_scenes()
            updated_count = 0

            for scene in scenes:
                # Get reference_files
                ref_files = []
                if scene.reference_files:
                    try:
                        ref_files = json.loads(scene.reference_files) if scene.reference_files.startswith('[') else [scene.reference_files]
                    except:
                        ref_files = [f.strip() for f in scene.reference_files.split(',') if f.strip()]

                if not ref_files:
                    continue

                # Update img_prompt
                new_img_prompt = self._add_filename_annotations_to_prompt(
                    scene.img_prompt or "", ref_files, characters, locations
                )
                new_video_prompt = self._add_filename_annotations_to_prompt(
                    scene.video_prompt or "", ref_files, characters, locations
                )

                # Check if changed
                if new_img_prompt != scene.img_prompt or new_video_prompt != scene.video_prompt:
                    workbook.update_scene(
                        scene.scene_id,
                        img_prompt=new_img_prompt,
                        video_prompt=new_video_prompt
                    )
                    updated_count += 1
                    self.logger.info(f"Updated scene {scene.scene_id} with filename annotations")

            workbook.save()
            self.logger.info(f"Updated {updated_count} scenes with filename annotations")
            return True

        except Exception as e:
            self.logger.error(f"Error updating Excel prompts: {e}")
            return False

    # ============================================================================
    # V2 FLOW: TIMESTAMPS TỪ SRT, KHÔNG ĐỂ AI QUYẾT ĐỊNH
    # ============================================================================

    def _group_srt_entries_v2(
        self,
        srt_entries: list,
        characters: list,
        locations: list,
        chunk_size: int = 20
    ) -> list:
        """
        BƯỚC 2 (V2): Nhóm SRT entries thành scenes.

        AI CHỈ quyết định: entries nào thuộc cùng 1 scene
        CODE tự tính: timestamps từ entries

        Args:
            srt_entries: List các SRT entries
            characters: List characters đã phân tích
            locations: List locations đã phân tích
            chunk_size: Số entries mỗi chunk gửi API

        Returns:
            List scenes với timestamps CHÍNH XÁC từ SRT
        """
        self.logger.info(f"[V2] Bắt đầu nhóm {len(srt_entries)} SRT entries thành scenes...")

        all_scenes = []
        scene_id = 1

        # Chia thành chunks nhỏ để API xử lý chính xác
        for chunk_start in range(0, len(srt_entries), chunk_size):
            chunk_end = min(chunk_start + chunk_size, len(srt_entries))
            chunk_entries = srt_entries[chunk_start:chunk_end]

            self.logger.info(f"[V2] Xử lý chunk: entries {chunk_start+1}-{chunk_end}")

            # Tạo prompt ngắn gọn cho API
            entries_text = "\n".join([
                f"  {i+1}. [{self._format_timedelta(e.start_time)} - {self._format_timedelta(e.end_time)}] \"{e.text[:100]}\""
                for i, e in enumerate(chunk_entries)
            ])

            # Characters và locations info
            chars_list = ", ".join([f"{c.id} ({c.name})" for c in characters[:5]]) if characters else "nvc (narrator)"
            locs_list = ", ".join([f"{l.id} ({l.name})" for l in locations[:5]]) if locations else "generic location"

            prompt = f"""Nhóm các SRT entries sau thành scenes. Mỗi scene là 1 đoạn nội dung liên quan.

ENTRIES:
{entries_text}

CHARACTERS: {chars_list}
LOCATIONS: {locs_list}

QUY TẮC:
- Nhóm entries có nội dung liên quan thành 1 scene
- Mỗi scene tối đa 3-5 entries (để duration không quá 20s)
- Khi nội dung chuyển chủ đề/thời gian/địa điểm → scene mới
- Flashback (nhắc về quá khứ) = scene riêng
- Hiện tại (đang xảy ra) = scene riêng

OUTPUT FORMAT (JSON only, không markdown):
{{
  "groups": [
    {{
      "entries": [1, 2, 3],
      "scene_type": "FRAME_PRESENT",
      "main_character": "nvc",
      "location": "loc_courthouse",
      "emotion": "sad",
      "summary": "Đứng trước tòa án, đau khổ"
    }},
    {{
      "entries": [4, 5],
      "scene_type": "CHILDHOOD_FLASHBACK",
      "main_character": "nv1_young",
      "location": "loc_apartment",
      "emotion": "nostalgic",
      "summary": "Nhớ về tuổi thơ với mẹ"
    }}
  ]
}}"""

            # Gọi API
            try:
                response = self._generate_content(prompt, temperature=0.3, max_tokens=2000)
                json_data = self._extract_json(response)

                if json_data and "groups" in json_data:
                    groups = json_data["groups"]
                    self.logger.info(f"[V2] API trả về {len(groups)} groups")

                    # Chuyển đổi groups thành scenes với timestamps từ SRT
                    for group in groups:
                        entry_indices = group.get("entries", [])
                        if not entry_indices:
                            continue

                        # Lấy các entries trong group (chuyển từ 1-indexed sang 0-indexed)
                        group_entries = []
                        for idx in entry_indices:
                            actual_idx = idx - 1  # API trả về 1-indexed
                            if 0 <= actual_idx < len(chunk_entries):
                                group_entries.append(chunk_entries[actual_idx])

                        if not group_entries:
                            continue

                        # TIMESTAMPS TỪ SRT - KHÔNG SAI!
                        first_entry = group_entries[0]
                        last_entry = group_entries[-1]

                        scene = {
                            "scene_id": scene_id,
                            "srt_start": self._format_timedelta(first_entry.start_time),
                            "srt_end": self._format_timedelta(last_entry.end_time),
                            "duration_seconds": (last_entry.end_time - first_entry.start_time).total_seconds(),
                            "srt_text": " ".join([e.text for e in group_entries])[:500],
                            "scene_type": group.get("scene_type", "FRAME_PRESENT"),
                            "main_character": group.get("main_character", "nvc"),
                            "location": group.get("location", ""),
                            "emotion": group.get("emotion", "neutral"),
                            "summary": group.get("summary", ""),
                            "entry_indices": [chunk_start + (idx - 1) for idx in entry_indices]  # Global indices
                        }

                        all_scenes.append(scene)
                        scene_id += 1

                else:
                    self.logger.warning(f"[V2] API không trả về groups, dùng fallback")
                    # Fallback: mỗi entry = 1 scene
                    for entry in chunk_entries:
                        scene = self._create_fallback_scene_v2(entry, scene_id, characters, locations)
                        all_scenes.append(scene)
                        scene_id += 1

            except Exception as e:
                self.logger.error(f"[V2] API error: {e}, dùng fallback")
                # Fallback: mỗi entry = 1 scene
                for entry in chunk_entries:
                    scene = self._create_fallback_scene_v2(entry, scene_id, characters, locations)
                    all_scenes.append(scene)
                    scene_id += 1

        self.logger.info(f"[V2] Tạo được {len(all_scenes)} scenes từ {len(srt_entries)} entries")
        return all_scenes

    def _create_fallback_scene_v2(self, entry, scene_id: int, characters: list, locations: list) -> dict:
        """Tạo scene fallback từ 1 SRT entry."""
        text_lower = entry.text.lower() if entry.text else ""

        # Detect scene type từ keywords
        scene_type = "FRAME_PRESENT"
        main_char = "nvc"

        if any(kw in text_lower for kw in ['ngày xưa', 'hồi nhỏ', 'khi tôi còn', 'tuổi thơ', 'childhood']):
            scene_type = "CHILDHOOD_FLASHBACK"
            main_char = "nv1_young"
        elif any(kw in text_lower for kw in ['mẹ tôi', 'bà ấy', 'mother', 'she worked']):
            scene_type = "CHILDHOOD_FLASHBACK"
            main_char = "nv1_young"
        elif any(kw in text_lower for kw in ['tôi xây', 'tôi làm', 'i built', 'carpenter']):
            scene_type = "ADULT_FLASHBACK"
            main_char = "nvc_young"

        # Detect emotion
        emotion = "neutral"
        if any(kw in text_lower for kw in ['buồn', 'sad', 'cry', 'khóc', 'đau']):
            emotion = "sad"
        elif any(kw in text_lower for kw in ['vui', 'happy', 'smile', 'cười']):
            emotion = "happy"
        elif any(kw in text_lower for kw in ['giận', 'angry', 'tức']):
            emotion = "angry"

        return {
            "scene_id": scene_id,
            "srt_start": self._format_timedelta(entry.start_time),
            "srt_end": self._format_timedelta(entry.end_time),
            "duration_seconds": (entry.end_time - entry.start_time).total_seconds(),
            "srt_text": entry.text[:500] if entry.text else "",
            "scene_type": scene_type,
            "main_character": main_char,
            "location": "",
            "emotion": emotion,
            "summary": entry.text[:100] if entry.text else ""
        }

    def _create_shots_for_scene_v2(
        self,
        scene: dict,
        characters: list,
        locations: list,
        global_style: str
    ) -> list:
        """
        BƯỚC 3 (V2): Tạo shots cho 1 scene.

        Mỗi shot tối đa 8 giây.
        Timestamps được CHIA ĐỀU từ scene duration.

        Args:
            scene: Scene dict từ bước 2
            characters: List characters
            locations: List locations
            global_style: Style string

        Returns:
            List shots với timestamps chính xác
        """
        scene_id = scene["scene_id"]
        duration = scene.get("duration_seconds", 5)
        srt_text = scene.get("srt_text", "")
        scene_type = scene.get("scene_type", "FRAME_PRESENT")
        main_char = scene.get("main_character", "nvc")
        location = scene.get("location", "")
        emotion = scene.get("emotion", "neutral")

        # Parse timestamps
        try:
            start_parts = scene["srt_start"].replace(",", ".").split(":")
            start_seconds = int(start_parts[0]) * 3600 + int(start_parts[1]) * 60 + float(start_parts[2])
        except:
            start_seconds = 0

        # Tính số shots cần thiết (mỗi shot max 8s)
        max_shot_duration = 8
        num_shots = max(1, int(duration / max_shot_duration) + (1 if duration % max_shot_duration > 2 else 0))
        shot_duration = duration / num_shots if num_shots > 0 else duration

        # Build character description chi tiết
        char_desc = ""
        char_lock = ""
        for c in characters:
            if c.id == main_char:
                char_lock = c.character_lock or ""
                char_desc = f"{c.name}: {char_lock}" if char_lock else f"{c.name}"
                break
        if not char_desc:
            char_desc = f"main character ({main_char})"

        # Build location description chi tiết
        loc_desc = ""
        loc_lock = ""
        for l in locations:
            if l.id == location:
                loc_lock = l.location_lock or ""
                loc_desc = f"{l.name}: {loc_lock}" if loc_lock else f"{l.name}"
                break

        # Emotion to cinematic lighting/mood
        emotion_cinematics = {
            "sad": "soft diffused lighting, muted colors, melancholic atmosphere, shallow depth of field",
            "happy": "warm golden hour lighting, vibrant colors, uplifting atmosphere",
            "angry": "harsh dramatic lighting, high contrast, intense shadows, red undertones",
            "fear": "low-key lighting, deep shadows, cold blue tones, unsettling atmosphere",
            "love": "soft romantic lighting, warm tones, bokeh background, intimate framing",
            "neutral": "natural balanced lighting, cinematic color grading"
        }
        cinematic_mood = emotion_cinematics.get(emotion, emotion_cinematics["neutral"])

        # Scene summary từ bước phân tích
        scene_summary = scene.get("summary", "")

        prompt = f"""You are a professional film director creating VISUAL shot descriptions for a dramatic video.

=== STORY MOMENT ===
NARRATION TEXT: "{srt_text}"
SCENE SUMMARY: {scene_summary if scene_summary else "Visualize the narration above"}
SCENE TYPE: {scene_type}
EMOTIONAL TONE: {emotion}

=== CHARACTER IN THIS SCENE ===
CHARACTER ID: {main_char}
APPEARANCE: {char_desc}
{f"CHARACTER LOCK (exact look): {char_lock}" if char_lock else ""}

=== SETTING ===
LOCATION: {loc_desc if loc_desc else "Setting based on narration context"}
{f"LOCATION LOCK: {loc_lock}" if loc_lock else ""}

=== CINEMATIC REQUIREMENTS ===
VISUAL STYLE: {global_style}
MOOD LIGHTING: {cinematic_mood}
DURATION: {duration:.1f}s → {num_shots} shot(s)

=== CRITICAL RULES ===
1. ILLUSTRATE THE STORY - Each shot must VISUALLY show what's happening in the narration
2. CHARACTER ACTIONS - Show the character DOING something relevant to the narration (not just standing)
3. EMOTIONAL EXPRESSION - Face must show {emotion} emotion with specific micro-expressions
4. NO TEXT/WORDS in the image - describe visually, don't include dialogue
5. DETAILED DESCRIPTION - Include specific details: clothing wrinkles, lighting direction, background elements
6. REFERENCE FORMAT - End with: ({main_char}.png){f", ({location}.png)" if location else ""}

=== SHOT VARIETY ===
- ESTABLISHING WIDE (24mm): Full environment + character, context setting
- MEDIUM (50mm): Upper body, gestures, interaction with environment
- CLOSE-UP (85mm): Face emotions, tears, expressions, shallow DOF
- EXTREME CLOSE-UP (100mm): Eyes reflecting emotion, hands trembling, details
- LOW ANGLE: Empowering, dramatic, larger than life
- HIGH ANGLE: Vulnerable, small, overwhelmed

=== OUTPUT FORMAT (JSON only) ===
{{
  "shots": [
    {{
      "shot_type": "CLOSE-UP",
      "img_prompt": "[Shot type], [lens mm], [CHARACTER DESCRIPTION: age, ethnicity, hair, clothing], [SPECIFIC ACTION the character is doing], [FACIAL EXPRESSION matching {emotion}], [LIGHTING: {cinematic_mood}], [BACKGROUND/ENVIRONMENT], {global_style}, 4K cinematic. ({main_char}.png)"
    }}
  ]
}}

=== EXAMPLE HIGH-QUALITY PROMPT ===
"Close-up shot, 85mm portrait lens, 35-year-old Caucasian man with salt-and-pepper hair and tired eyes, wearing a faded blue work shirt, his weathered hands gripping an old photograph, tears welling in his eyes as memories flood back, soft diffused window light casting gentle shadows on his face, bokeh background of a modest living room, photorealistic, 4K cinematic quality. (nvc.png)"

NOW CREATE {num_shots} SHOTS that VISUALLY TELL THIS STORY MOMENT: "{scene_summary if scene_summary else srt_text[:100]}"
"""

        shots = []

        try:
            response = self._generate_content(prompt, temperature=0.7, max_tokens=3000)
            json_data = self._extract_json(response)

            if json_data and "shots" in json_data:
                api_shots = json_data["shots"]

                # Tính timestamps cho mỗi shot
                current_time = start_seconds
                for i, api_shot in enumerate(api_shots):
                    shot_end = current_time + shot_duration

                    # Build reference_files list
                    ref_files = api_shot.get("reference_files", [f"{main_char}.png"])
                    # Đảm bảo location cũng có trong reference_files nếu có
                    if location and f"{location}.png" not in ref_files:
                        ref_files.append(f"{location}.png")

                    # Lấy img_prompt từ API
                    img_prompt = api_shot.get("img_prompt", "")

                    # QUAN TRỌNG: Thêm annotations (nvc.png) vào prompt nếu chưa có
                    if img_prompt and ref_files:
                        img_prompt = self._add_filename_annotations_to_prompt(
                            img_prompt,
                            ref_files,
                            characters,
                            locations
                        )

                    shot = {
                        "scene_id": scene_id,
                        "shot_number": i + 1,
                        "srt_start": self._seconds_to_timestamp(current_time),
                        "srt_end": self._seconds_to_timestamp(shot_end),
                        "duration": shot_duration,
                        "shot_type": api_shot.get("shot_type", "MEDIUM"),
                        "img_prompt": img_prompt,
                        "reference_files": ref_files,
                        "srt_text": srt_text[:200],
                        # QUAN TRỌNG: Thêm main_character và location để lưu vào Excel
                        "main_character": main_char,
                        "location": location
                    }
                    shots.append(shot)
                    current_time = shot_end

            else:
                # Fallback: tạo shots đơn giản
                shots = self._create_fallback_shots_v2(scene, num_shots, shot_duration, start_seconds, global_style)

        except Exception as e:
            self.logger.warning(f"[V2] Shot API error: {e}, dùng fallback")
            shots = self._create_fallback_shots_v2(scene, num_shots, shot_duration, start_seconds, global_style)

        return shots

    def _create_fallback_shots_v2(
        self,
        scene: dict,
        num_shots: int,
        shot_duration: float,
        start_seconds: float,
        global_style: str
    ) -> list:
        """Tạo shots fallback không cần API - chất lượng cao, minh họa câu chuyện."""
        shots = []
        main_char = scene.get("main_character", "nvc")
        location = scene.get("location", "")
        srt_text = scene.get("srt_text", "")
        emotion = scene.get("emotion", "neutral")
        summary = scene.get("summary", "")
        scene_type = scene.get("scene_type", "FRAME_PRESENT")

        # Shot types với lens info và purpose
        shot_configs = [
            ("Close-up shot", "85mm portrait lens", "face and emotional expression, shallow depth of field, bokeh background"),
            ("Medium shot", "50mm lens", "upper body showing gestures and body language, environmental context"),
            ("Wide establishing shot", "24mm wide-angle lens", "full body in complete environment, context and atmosphere"),
            ("Extreme close-up", "100mm macro lens", "eyes or hands detail, intense emotional focus, cinematic intimacy"),
            ("Low angle shot", "35mm lens from below", "dramatic empowering perspective, character prominence"),
            ("Over-the-shoulder shot", "50mm lens", "perspective view, connection to environment or memory")
        ]

        # Emotion → detailed cinematic description
        emotion_map = {
            "sad": {
                "lighting": "soft diffused lighting from window, muted blue-grey color grading",
                "expression": "tears welling in eyes, downturned lips, furrowed brow, looking down",
                "atmosphere": "melancholic, heavy, contemplative silence"
            },
            "happy": {
                "lighting": "warm golden hour sunlight, vibrant natural colors",
                "expression": "genuine bright smile reaching the eyes, relaxed features, joyful gaze",
                "atmosphere": "uplifting, warm, peaceful contentment"
            },
            "angry": {
                "lighting": "harsh dramatic side-lighting, high contrast shadows, red undertones",
                "expression": "intense furrowed brow, clenched jaw, fierce piercing gaze, flared nostrils",
                "atmosphere": "tense, confrontational, explosive tension"
            },
            "fear": {
                "lighting": "low-key lighting, cold blue tones, deep unsettling shadows",
                "expression": "wide anxious eyes, pale complexion, tense shoulders, trembling",
                "atmosphere": "dread, vulnerability, impending danger"
            },
            "love": {
                "lighting": "soft romantic backlighting, warm pink tones, gentle lens flare",
                "expression": "tender loving gaze, soft gentle smile, eyes full of affection",
                "atmosphere": "intimate, warm, emotional connection"
            },
            "nostalgic": {
                "lighting": "soft dreamy haze, warm sepia undertones, gentle diffusion",
                "expression": "distant wistful gaze, slight melancholic smile, remembering",
                "atmosphere": "bittersweet memory, passage of time, reflection"
            },
            "neutral": {
                "lighting": "natural balanced lighting, cinematic color grading",
                "expression": "contemplative thoughtful expression, observant gaze",
                "atmosphere": "calm, observational, everyday moment"
            }
        }
        mood = emotion_map.get(emotion, emotion_map["neutral"])

        # Build reference_files
        ref_files = [f"{main_char}.png"]
        if location:
            ref_files.append(f"{location}.png")

        # Location reference
        loc_ref = f", ({location}.png)" if location else ""

        # Story context để minh họa
        story_context = summary if summary else srt_text[:100]

        current_time = start_seconds
        for i in range(num_shots):
            config = shot_configs[i % len(shot_configs)]
            shot_type, lens_desc, framing_desc = config
            shot_end = current_time + shot_duration

            # Tạo prompt chi tiết minh họa câu chuyện
            img_prompt = (
                f"{shot_type}, {lens_desc}, {framing_desc}. "
                f"Character ({main_char}.png) {mood['expression']}. "
                f"{mood['lighting']}. "
                f"{mood['atmosphere']}. "
                f"{global_style}, photorealistic, 4K cinematic quality{loc_ref}. "
                f"Visualizing: {story_context}"
            )

            shot = {
                "scene_id": scene["scene_id"],
                "shot_number": i + 1,
                "srt_start": self._seconds_to_timestamp(current_time),
                "srt_end": self._seconds_to_timestamp(shot_end),
                "duration": shot_duration,
                "shot_type": shot_type,
                "img_prompt": img_prompt,
                "reference_files": ref_files,
                "srt_text": srt_text[:200],
                "main_character": main_char,
                "location": location
            }
            shots.append(shot)
            current_time = shot_end

        return shots

    def _seconds_to_timestamp(self, seconds: float) -> str:
        """Chuyển seconds thành timestamp HH:MM:SS,mmm"""
        hours = int(seconds // 3600)
        minutes = int((seconds % 3600) // 60)
        secs = seconds % 60
        return f"{hours:02d}:{minutes:02d}:{secs:06.3f}".replace(".", ",")

    def _timestamp_to_seconds_v2(self, timestamp: str) -> float:
        """Chuyển timestamp HH:MM:SS,mmm thành seconds"""
        try:
            ts = timestamp.replace(",", ".")
            parts = ts.split(":")
            if len(parts) == 3:
                return int(parts[0]) * 3600 + int(parts[1]) * 60 + float(parts[2])
            elif len(parts) == 2:
                return int(parts[0]) * 60 + float(parts[1])
            return float(ts)
        except:
            return 0

    def generate_prompts_v2(
        self,
        srt_entries: list,
        characters: list,
        locations: list,
        global_style: str,
        excel_path: str,
        project_dir: str
    ) -> bool:
        """
        FLOW V2: Tạo prompts với timestamps CHÍNH XÁC từ SRT.

        Bước 1: Phân tích story (đã có sẵn characters, locations)
        Bước 2: Nhóm SRT entries thành scenes
        Bước 3: Tạo shots cho mỗi scene
        Bước 4: Lưu vào Excel

        Args:
            srt_entries: List SRT entries
            characters: List characters từ bước 1
            locations: List locations từ bước 1
            global_style: Style string
            excel_path: Path to Excel file
            project_dir: Project directory

        Returns:
            True nếu thành công
        """
        self.logger.info("=" * 60)
        self.logger.info("[V2 FLOW] BẮT ĐẦU TẠO PROMPTS VỚI TIMESTAMPS CHÍNH XÁC")
        self.logger.info("=" * 60)

        try:
            # BƯỚC 2: Nhóm SRT entries thành scenes
            self.logger.info("\n[V2 BƯỚC 2] Nhóm SRT entries thành scenes...")
            scenes = self._group_srt_entries_v2(srt_entries, characters, locations)
            self.logger.info(f"[V2 BƯỚC 2] [v] Tạo được {len(scenes)} scenes")

            # BƯỚC 3: Tạo shots cho mỗi scene - XỬ LÝ THEO BATCH ĐỂ TRÁNH LỖI TOKEN
            self.logger.info("\n[V2 BƯỚC 3] Tạo shots cho mỗi scene (batch mode)...")
            all_shots = []
            BATCH_SIZE = 5  # Xử lý 5 scenes mỗi batch để tránh lỗi token
            total_batches = (len(scenes) + BATCH_SIZE - 1) // BATCH_SIZE

            for batch_idx in range(total_batches):
                batch_start = batch_idx * BATCH_SIZE
                batch_end = min(batch_start + BATCH_SIZE, len(scenes))
                batch_scenes = scenes[batch_start:batch_end]

                self.logger.info(f"\n  [Batch {batch_idx + 1}/{total_batches}] Scenes {batch_start + 1}-{batch_end}...")

                for scene in batch_scenes:
                    try:
                        shots = self._create_shots_for_scene_v2(scene, characters, locations, global_style)

                        # === VALIDATE MỖI SHOT ===
                        validated_shots = []
                        for shot in shots:
                            # Validate timestamp
                            if not shot.get("srt_start") or not shot.get("srt_end"):
                                self.logger.warning(f"    [WARN] Shot thiếu timestamp, dùng fallback")
                                continue

                            # Validate prompt
                            if not shot.get("img_prompt") or len(shot.get("img_prompt", "")) < 20:
                                self.logger.warning(f"    [WARN] Shot thiếu prompt, dùng fallback")
                                continue

                            # Validate references - đảm bảo luôn có
                            main_char = shot.get("main_character", "nvc")
                            if not shot.get("reference_files"):
                                shot["reference_files"] = [f"{main_char}.png"]
                                if shot.get("location"):
                                    shot["reference_files"].append(f"{shot['location']}.png")

                            validated_shots.append(shot)

                        # Nếu không có shot valid, dùng fallback cho scene
                        if not validated_shots:
                            self.logger.warning(f"    [WARN] Scene {scene['scene_id']} không có shot valid, tạo fallback...")
                            num_shots = max(1, int(scene.get("duration_seconds", 5) / 8) + 1)
                            start_secs = self._timestamp_to_seconds_v2(scene.get("srt_start", "00:00:00,000"))
                            validated_shots = self._create_fallback_shots_v2(
                                scene, num_shots, scene.get("duration_seconds", 5) / num_shots,
                                start_secs, global_style
                            )

                        all_shots.extend(validated_shots)
                        self.logger.info(f"    Scene {scene['scene_id']}: {len(validated_shots)} shots ({scene['srt_start']} - {scene['srt_end']})")

                    except Exception as scene_err:
                        self.logger.error(f"    [FAIL] Scene {scene['scene_id']} lỗi: {scene_err}, tạo fallback...")
                        # Tạo fallback cho scene lỗi
                        num_shots = max(1, int(scene.get("duration_seconds", 5) / 8) + 1)
                        start_secs = self._timestamp_to_seconds_v2(scene.get("srt_start", "00:00:00,000"))
                        fallback_shots = self._create_fallback_shots_v2(
                            scene, num_shots, scene.get("duration_seconds", 5) / num_shots,
                            start_secs, global_style
                        )
                        all_shots.extend(fallback_shots)

                # Nghỉ giữa các batch để tránh rate limit
                if batch_idx < total_batches - 1:
                    import time
                    time.sleep(1)

            self.logger.info(f"\n[V2 BƯỚC 3] [v] Tạo được {len(all_shots)} shots tổng cộng")

            # === SẮP XẾP VÀ VALIDATE TIMESTAMPS ===
            # Đảm bảo thứ tự đúng và không có timestamp nhảy bất thường
            def get_start_seconds(shot):
                try:
                    parts = shot["srt_start"].replace(",", ".").split(":")
                    return int(parts[0]) * 3600 + int(parts[1]) * 60 + float(parts[2])
                except:
                    return 0

            all_shots.sort(key=get_start_seconds)
            self.logger.info("[V2] [v] Đã sắp xếp shots theo timestamp")

            # === VALIDATE: Kiểm tra timestamp không nhảy bất thường ===
            # Nếu shot N có start_time > shot N-1 end_time + 10s → cảnh báo
            validated_shots = []
            prev_end_seconds = 0
            MAX_GAP_SECONDS = 15  # Cho phép gap tối đa 15 giây

            for shot in all_shots:
                shot_start = get_start_seconds(shot)
                shot_end_str = shot.get("srt_end", "")
                try:
                    end_parts = shot_end_str.replace(",", ".").split(":")
                    shot_end = int(end_parts[0]) * 3600 + int(end_parts[1]) * 60 + float(end_parts[2])
                except:
                    shot_end = shot_start + 5

                # Kiểm tra gap bất thường
                gap = shot_start - prev_end_seconds
                if prev_end_seconds > 0 and gap > MAX_GAP_SECONDS:
                    self.logger.warning(f"  [WARN] Gap bất thường {gap:.1f}s tại {shot['srt_start']}, điều chỉnh...")
                    # Điều chỉnh timestamp để liên tục
                    duration = shot_end - shot_start
                    shot["srt_start"] = self._seconds_to_timestamp(prev_end_seconds)
                    shot["srt_end"] = self._seconds_to_timestamp(prev_end_seconds + duration)
                    shot_end = prev_end_seconds + duration

                validated_shots.append(shot)
                prev_end_seconds = shot_end

            all_shots = validated_shots
            self.logger.info(f"[V2] [v] Validated {len(all_shots)} shots với timestamps liên tục")

            # BƯỚC 4: Lưu vào Excel
            self.logger.info("\n[V2 BƯỚC 4] Lưu vào Excel...")
            from .excel_manager import PromptWorkbook, Scene

            workbook = PromptWorkbook(excel_path)
            workbook.load_or_create()

            # === CLEAR SCENES CŨ TRƯỚC KHI THÊM MỚI ===
            workbook.clear_scenes()
            self.logger.info("[V2] [v] Đã xóa scenes cũ")

            # Đánh số scene_id mới theo thứ tự
            for idx, shot in enumerate(all_shots):
                # === VALIDATE VÀ ĐẢM BẢO REFERENCE ĐẦY ĐỦ ===
                main_char = shot.get("main_character", "nvc") or "nvc"
                location = shot.get("location", "")

                # Đảm bảo reference_files luôn có ít nhất character
                ref_files = shot.get("reference_files", [])
                if not ref_files or not isinstance(ref_files, list):
                    ref_files = []

                # Đảm bảo character file trong reference
                char_file = f"{main_char}.png"
                if char_file not in ref_files:
                    ref_files.insert(0, char_file)

                # Đảm bảo location file trong reference nếu có location
                if location:
                    loc_file = f"{location}.png"
                    if loc_file not in ref_files:
                        ref_files.append(loc_file)

                # Chuyển reference_files thành JSON string
                ref_files_str = json.dumps(ref_files)

                # Chuyển characters_used thành JSON list
                if isinstance(main_char, list):
                    chars_used_str = json.dumps(main_char)
                else:
                    chars_used_str = json.dumps([main_char]) if main_char else '["nvc"]'

                scene_obj = Scene(
                    scene_id=idx + 1,  # Đánh số lại theo thứ tự
                    srt_start=shot["srt_start"],
                    srt_end=shot["srt_end"],
                    duration=shot.get("duration", 5),
                    planned_duration=shot.get("duration", 5),
                    srt_text=shot.get("srt_text", "")[:500],
                    img_prompt=shot.get("img_prompt", ""),
                    video_prompt=shot.get("img_prompt", ""),  # Dùng chung
                    status_img="pending",
                    status_vid="pending",
                    characters_used=chars_used_str,
                    location_used=shot.get("location", ""),
                    reference_files=ref_files_str
                )
                workbook.add_scene(scene_obj)

            workbook.save()
            self.logger.info(f"[V2 BƯỚC 4] [v] Đã lưu {len(all_shots)} shots vào Excel")

            self.logger.info("\n" + "=" * 60)
            self.logger.info("[V2 FLOW] HOÀN THÀNH!")
            self.logger.info("=" * 60)

            return True

        except Exception as e:
            self.logger.error(f"[V2 FLOW] Lỗi: {e}")
            import traceback
            self.logger.error(traceback.format_exc())
            return False

    def _analyze_srt_for_characters(self, srt_entries: List) -> dict:
        """
        Phân tích nội dung SRT để đoán nhân vật và bối cảnh.
        TRÍCH XUẤT mô tả chi tiết từ SRT để tạo prompt độc đáo cho mỗi project.

        Returns:
            dict với keys: narrator_type, characters, locations, theme, extracted_descriptions
        """
        import re

        # Gộp tất cả text từ SRT - giữ nguyên case để trích xuất mô tả
        all_text_original = " ".join([
            entry.get("text", "") if isinstance(entry, dict) else str(entry)
            for entry in srt_entries
        ])
        all_text = all_text_original.lower()

        result = {
            "narrator_gender": "neutral",  # male, female, neutral
            "narrator_age": "adult",       # child, young, adult, elderly
            "characters": [],              # List of detected character types with descriptions
            "locations": [],               # List of detected location types with descriptions
            "theme": "general",            # romance, action, family, horror, etc.
            "ethnicity": "western",        # western (default/American), asian, african
            "extracted_char_descriptions": [],  # Mô tả nhân vật trích từ SRT
            "extracted_loc_descriptions": [],   # Mô tả bối cảnh trích từ SRT
        }

        # === TRÍCH XUẤT MÔ TẢ NHÂN VẬT TỪ SRT ===
        # Tìm các pattern mô tả người: "a/an/the [adj] [adj] man/woman/boy/girl..."
        char_patterns = [
            # English patterns
            r'(?:a|an|the)\s+((?:\w+\s+){0,3})(man|woman|boy|girl|child|lady|gentleman|guy|person|old man|old woman|young man|young woman)',
            r'((?:\w+\s+){0,3})(father|mother|dad|mom|grandfather|grandmother|grandpa|grandma|uncle|aunt|brother|sister|son|daughter)',
            r'(?:he|she) (?:was|is|looked|appeared)\s+((?:\w+\s*){1,5})',
            r'(?:his|her) ((?:\w+\s+){0,2})(face|eyes|hair|smile|voice|hands)',
            # Vietnamese patterns
            r'(?:một|người)\s+((?:\w+\s+){0,3})(đàn ông|phụ nữ|con trai|con gái|ông già|bà già|thanh niên|cô gái|chàng trai)',
            r'((?:\w+\s+){0,3})(bố|mẹ|cha|má|ông|bà|anh|chị|em|con)',
        ]

        extracted_chars = []
        for pattern in char_patterns:
            matches = re.findall(pattern, all_text, re.IGNORECASE)
            for match in matches:
                if isinstance(match, tuple):
                    desc = " ".join(match).strip()
                else:
                    desc = match.strip()
                if desc and len(desc) > 3 and desc not in extracted_chars:
                    extracted_chars.append(desc)

        result["extracted_char_descriptions"] = extracted_chars[:10]  # Max 10

        # === TRÍCH XUẤT MÔ TẢ BỐI CẢNH TỪ SRT ===
        loc_patterns = [
            # English patterns
            r'(?:in|at|on|inside|outside|near|by)\s+(?:a|an|the)\s+((?:\w+\s+){0,4})(house|home|room|office|street|park|beach|mountain|city|village|hospital|school|restaurant|cafe|garden|forest|lake|river|building|church|store|shop)',
            r'(?:the)\s+((?:\w+\s+){0,3})(sky|sun|moon|rain|snow|night|morning|evening|sunset|sunrise)',
            r'(?:a|an|the)\s+((?:\w+\s+){0,3})(old|abandoned|beautiful|dark|bright|quiet|busy|empty|crowded)\s+(\w+)',
            # Vietnamese patterns
            r'(?:ở|tại|trong|ngoài|bên)\s+((?:\w+\s+){0,4})(nhà|phòng|đường|công viên|bãi biển|núi|thành phố|làng|bệnh viện|trường|quán|vườn|rừng|hồ|sông)',
            r'((?:\w+\s+){0,3})(trời|nắng|mưa|tuyết|đêm|sáng|chiều|hoàng hôn|bình minh)',
        ]

        extracted_locs = []
        for pattern in loc_patterns:
            matches = re.findall(pattern, all_text, re.IGNORECASE)
            for match in matches:
                if isinstance(match, tuple):
                    desc = " ".join(match).strip()
                else:
                    desc = match.strip()
                if desc and len(desc) > 3 and desc not in extracted_locs:
                    extracted_locs.append(desc)

        result["extracted_loc_descriptions"] = extracted_locs[:10]  # Max 10

        # === PHÁT HIỆN GIỚI TÍNH NGƯỜI KỂ ===
        male_vi = ["tôi là đàn ông", "anh ấy", "ông ấy", "chú ấy", "bố tôi", "cha tôi", "con trai"]
        female_vi = ["tôi là phụ nữ", "cô ấy", "bà ấy", "dì ấy", "mẹ tôi", "con gái"]
        male_en = ["i am a man", "he said", "his father", "my father", "the boy", "the man"]
        female_en = ["i am a woman", "she said", "her mother", "my mother", "the girl", "the woman"]

        male_count = sum(1 for k in male_vi + male_en if k in all_text)
        female_count = sum(1 for k in female_vi + female_en if k in all_text)

        if male_count > female_count + 2:
            result["narrator_gender"] = "male"
        elif female_count > male_count + 2:
            result["narrator_gender"] = "female"

        # === PHÁT HIỆN TUỔI ===
        child_kw = ["con nít", "em bé", "trẻ con", "child", "kid", "baby", "tuổi thơ", "childhood"]
        elderly_kw = ["ông bà", "già", "elderly", "grandfather", "grandmother", "old age", "về già"]
        young_kw = ["thanh niên", "trẻ tuổi", "young", "teenager", "youth", "tuổi trẻ"]

        if any(k in all_text for k in child_kw):
            result["narrator_age"] = "child"
        elif any(k in all_text for k in elderly_kw):
            result["narrator_age"] = "elderly"
        elif any(k in all_text for k in young_kw):
            result["narrator_age"] = "young"

        # === PHÁT HIỆN NHÂN VẬT ===
        char_patterns = [
            # (pattern keywords, character type, gender, age)
            (["mẹ", "mother", "mom", "má"], "Mother Figure", "female", "adult"),
            (["bố", "father", "dad", "ba", "cha"], "Father Figure", "male", "adult"),
            (["con trai", "son", "cậu bé", "boy"], "Son/Boy", "male", "young"),
            (["con gái", "daughter", "cô bé", "girl"], "Daughter/Girl", "female", "young"),
            (["ông", "grandfather", "ông nội", "ông ngoại"], "Grandfather", "male", "elderly"),
            (["bà", "grandmother", "bà nội", "bà ngoại"], "Grandmother", "female", "elderly"),
            (["vợ", "wife", "người yêu", "girlfriend", "bạn gái"], "Wife/Lover", "female", "adult"),
            (["chồng", "husband", "boyfriend", "bạn trai"], "Husband/Lover", "male", "adult"),
            (["bạn", "friend", "đồng nghiệp", "colleague"], "Friend", "neutral", "adult"),
            (["thầy", "teacher", "giáo viên", "professor"], "Teacher", "neutral", "adult"),
            (["bác sĩ", "doctor", "y tá", "nurse"], "Doctor", "neutral", "adult"),
            (["em bé", "baby", "infant", "trẻ sơ sinh"], "Baby", "neutral", "child"),
        ]

        detected_chars = []
        for keywords, char_type, gender, age in char_patterns:
            if any(k in all_text for k in keywords):
                detected_chars.append({
                    "type": char_type,
                    "gender": gender,
                    "age": age
                })
        result["characters"] = detected_chars[:4]  # Max 4 characters

        # === PHÁT HIỆN BỐI CẢNH ===
        loc_patterns = [
            (["nhà", "home", "phòng", "room", "căn hộ", "apartment"], "Home Interior"),
            (["trường", "school", "lớp học", "classroom"], "School"),
            (["bệnh viện", "hospital", "phòng khám", "clinic"], "Hospital"),
            (["công viên", "park", "vườn", "garden"], "Park/Garden"),
            (["biển", "beach", "ocean", "sea", "bãi biển"], "Beach/Ocean"),
            (["núi", "mountain", "đồi", "hill"], "Mountain"),
            (["thành phố", "city", "downtown", "urban"], "City"),
            (["làng", "village", "quê", "countryside", "nông thôn"], "Village/Countryside"),
            (["nhà hàng", "restaurant", "quán", "cafe", "coffee"], "Restaurant/Cafe"),
            (["văn phòng", "office", "công ty", "company"], "Office"),
            (["đêm", "night", "tối", "dark"], "Night Scene"),
            (["mưa", "rain", "storm", "bão"], "Rainy Scene"),
        ]

        detected_locs = []
        for keywords, loc_type in loc_patterns:
            if any(k in all_text for k in keywords):
                detected_locs.append(loc_type)
        result["locations"] = detected_locs[:5]  # Max 5 locations

        # === PHÁT HIỆN CHỦ ĐỀ ===
        theme_patterns = [
            (["tình yêu", "love", "yêu", "hôn", "kiss", "trái tim", "heart"], "romance"),
            (["gia đình", "family", "bố mẹ", "con cái", "parents", "children"], "family"),
            (["chiến tranh", "war", "battle", "soldier", "lính"], "war"),
            (["ma", "ghost", "horror", "sợ", "scary", "kinh dị"], "horror"),
            (["hài", "comedy", "funny", "laugh", "cười"], "comedy"),
            (["phiêu lưu", "adventure", "journey", "travel", "du lịch"], "adventure"),
            (["thành công", "success", "business", "kinh doanh", "công việc"], "business"),
        ]

        for keywords, theme in theme_patterns:
            if any(k in all_text for k in keywords):
                result["theme"] = theme
                break

        # === PHÁT HIỆN DÂN TỘC/VĂN HÓA ===
        # Mặc định là Western/American, chỉ đổi nếu phát hiện từ khóa khác
        asian_kw = ["việt nam", "vietnam", "asian", "châu á", "nhật", "japan", "hàn", "korea",
                    "trung quốc", "china", "thái lan", "thailand", "singapore"]
        african_kw = ["africa", "african", "châu phi"]

        if any(k in all_text for k in asian_kw):
            result["ethnicity"] = "asian"
        elif any(k in all_text for k in african_kw):
            result["ethnicity"] = "african"
        # else: giữ mặc định "western"

        return result

    def _build_character_prompt(self, char_info: dict, ethnicity: str) -> str:
        """Tạo prompt cho nhân vật dựa trên thông tin phân tích."""
        gender = char_info.get("gender", "neutral")
        age = char_info.get("age", "adult")
        char_type = char_info.get("type", "Person")

        # Base ethnicity
        eth_map = {
            "asian": "Asian",
            "western": "Caucasian",
            "african": "African",
            "neutral": ""
        }
        eth_str = eth_map.get(ethnicity, "")

        # Gender
        gender_map = {
            "male": "man",
            "female": "woman",
            "neutral": "person"
        }
        gender_str = gender_map.get(gender, "person")

        # Age
        age_map = {
            "child": "young child",
            "young": "young adult in their 20s",
            "adult": "adult in their 30s-40s",
            "elderly": "elderly person in their 60s-70s"
        }
        age_str = age_map.get(age, "adult")

        # Build prompt
        prompt = f"{age_str} {eth_str} {gender_str}".strip()
        prompt = prompt.replace("  ", " ")

        # Add character-specific details
        type_details = {
            "Mother Figure": "warm maternal expression, caring demeanor, comfortable home attire",
            "Father Figure": "strong protective presence, kind eyes, casual smart clothing",
            "Son/Boy": "energetic expression, casual youthful clothing, bright eyes",
            "Daughter/Girl": "sweet expression, youthful clothing, innocent charm",
            "Grandfather": "wise weathered face, gentle smile, traditional comfortable clothing",
            "Grandmother": "warm nurturing face, silver hair, traditional comfortable clothing",
            "Wife/Lover": "loving expression, elegant casual wear, graceful demeanor",
            "Husband/Lover": "devoted expression, smart casual clothing, strong presence",
            "Friend": "friendly open expression, casual modern clothing, approachable demeanor",
            "Teacher": "intelligent expression, professional attire, authoritative but kind",
            "Doctor": "professional appearance, white coat or medical attire, competent demeanor",
            "Baby": "innocent cherubic face, soft features, baby clothing",
        }

        details = type_details.get(char_type, "expressive face, appropriate attire for the scene")

        return f"{prompt}, {details}, photorealistic portrait, cinematic lighting, 8K quality"

    def _build_narrator_from_analysis(self, analysis: dict) -> tuple:
        """Tạo CHARACTER_LOCK và COSTUME_LOCK cho narrator dựa trên phân tích."""
        gender = analysis.get("narrator_gender", "neutral")
        age = analysis.get("narrator_age", "adult")
        ethnicity = analysis.get("ethnicity", "western")

        eth_map = {"asian": "Asian", "western": "Caucasian", "african": "African", "neutral": ""}
        eth_str = eth_map.get(ethnicity, "Caucasian")

        # Build character description based on analysis
        if gender == "male":
            if age == "elderly":
                char_lock = f"elderly {eth_str} man in his 60s, silver-grey hair, wise weathered face, gentle tired eyes, dignified expression"
            elif age == "young":
                char_lock = f"young {eth_str} man in his 20s, youthful face, expressive eyes, slight smile, energetic yet thoughtful"
            else:
                char_lock = f"middle-aged {eth_str} man in his 30s-40s, short dark hair, gentle tired eyes, slight stubble, warm complexion"
        elif gender == "female":
            if age == "elderly":
                char_lock = f"elderly {eth_str} woman in her 60s, silver hair, warm weathered face, kind eyes, graceful aging"
            elif age == "young":
                char_lock = f"young {eth_str} woman in her 20s, expressive face, gentle features, bright eyes, youthful glow"
            else:
                char_lock = f"middle-aged {eth_str} woman in her 30s-40s, elegant features, warm eyes, gentle expression, graceful demeanor"
        else:
            # Neutral - default to contemplative adult
            char_lock = f"adult {eth_str} person, contemplative expression, gentle eyes, thoughtful demeanor, timeless appearance"

        # Costume based on theme
        theme = analysis.get("theme", "general")
        costume_map = {
            "romance": "wearing elegant casual attire, soft colors, romantic style",
            "family": "wearing comfortable home clothes, warm sweater, relaxed appearance",
            "war": "wearing simple practical clothing, muted colors, weathered appearance",
            "horror": "wearing dark clothing, slightly disheveled, tense posture",
            "business": "wearing smart business casual, neat appearance, professional",
            "adventure": "wearing practical outdoor clothing, ready for action",
            "general": "wearing comfortable dark blue knit sweater over white collared shirt, casual but neat"
        }
        costume_lock = costume_map.get(theme, costume_map["general"])

        return char_lock, costume_lock

    def _generate_fallback_only(
        self,
        srt_entries: List,
        project_dir: Path,
        code: str,
        excel_path: Path,
        workbook
    ) -> bool:
        """
        Tạo Excel với fallback prompts kiểu "Narrator + Flashback".

        PHÂN TÍCH SRT để tạo nhân vật phù hợp với kịch bản.

        Ratio: 30% Narrator, 70% Flashback
        - Narrator: Nhân vật kể chuyện tại location cố định (an toàn, chỉ đổi góc máy)
        - Flashback: Minh họa nội dung SRT + ALL references (Flow tự chọn)
        """
        from .excel_manager import Scene, Character as CharObj
        from .utils import group_srt_into_scenes
        import json

        self.logger.info("[FALLBACK] Bắt đầu tạo Excel (30% Narrator + 70% Flashback)...")

        # === BƯỚC 0: PHÂN TÍCH SRT ĐỂ ĐOÁN NHÂN VẬT ===
        analysis = self._analyze_srt_for_characters(srt_entries)
        self.logger.info(f"[FALLBACK] Phân tích SRT: narrator={analysis['narrator_gender']}/{analysis['narrator_age']}, "
                        f"theme={analysis['theme']}, chars={len(analysis['characters'])}, locs={len(analysis['locations'])}")

        # === BƯỚC 1: Định nghĩa LOCK cho Narrator (dựa trên phân tích) ===
        CHARACTER_LOCK, COSTUME_LOCK = self._build_narrator_from_analysis(analysis)

        LOCATION_LOCK = (
            "cozy American living room corner, warm table lamp lighting, "
            "wooden bookshelf with classic books, comfortable leather armchair, "
            "fireplace in background, evening atmosphere, intimate storytelling setting"
        )

        # === BƯỚC 2: Tạo nhân vật narrator trong characters sheet ===
        default_char = Character(
            id="nvc",
            name="Narrator",
            role="narrator",
            vietnamese_prompt="Người kể chuyện",
            english_prompt=f"{CHARACTER_LOCK}, {COSTUME_LOCK}",
            character_lock=CHARACTER_LOCK,
            image_file="nvc.png",
            status="pending"
        )
        workbook.add_character(default_char)
        self.logger.info(f"[FALLBACK] [v] Narrator: {analysis['narrator_gender']}, {analysis['narrator_age']}")

        # === BƯỚC 3: Tạo nhân vật dựa trên phân tích SRT ===
        # ID phải bắt đầu bằng "nv" để smart_engine nhận diện
        flashback_chars = []
        ethnicity = analysis.get("ethnicity", "western")

        # Lấy mô tả đã trích xuất từ SRT
        extracted_char_descs = analysis.get("extracted_char_descriptions", [])
        extracted_loc_descs = analysis.get("extracted_loc_descriptions", [])

        self.logger.info(f"[FALLBACK] Trích xuất từ SRT: {len(extracted_char_descs)} char, {len(extracted_loc_descs)} loc")
        if extracted_char_descs:
            self.logger.info(f"[FALLBACK] Mô tả nhân vật: {extracted_char_descs[:3]}")
        if extracted_loc_descs:
            self.logger.info(f"[FALLBACK] Mô tả bối cảnh: {extracted_loc_descs[:3]}")

        # Thêm nhân vật từ phân tích (kết hợp với mô tả trích xuất)
        for i, char_info in enumerate(analysis.get("characters", [])[:3]):
            char_prompt = self._build_character_prompt(char_info, ethnicity)
            # Thêm mô tả trích xuất từ SRT nếu có
            if i < len(extracted_char_descs):
                srt_desc = extracted_char_descs[i]
                char_prompt = f"{char_prompt}. Story context: {srt_desc}"
            flashback_chars.append({
                "id": f"nv{i+1}",
                "name": char_info.get("type", f"Character {i+1}"),
                "role": "supporting",
                "english_prompt": char_prompt
            })

        # Nếu không đủ 3 nhân vật, thêm từ mô tả trích xuất hoặc generic
        generic_chars = [
            {"type": "Main Character", "gender": "neutral", "age": "adult"},
            {"type": "Supporting Character", "gender": "neutral", "age": "adult"},
            {"type": "Background Character", "gender": "neutral", "age": "adult"},
        ]
        while len(flashback_chars) < 3:
            idx = len(flashback_chars)
            char_info = generic_chars[idx]
            char_prompt = self._build_character_prompt(char_info, ethnicity)
            # Thêm mô tả từ SRT nếu có
            if idx < len(extracted_char_descs):
                srt_desc = extracted_char_descs[idx]
                char_prompt = f"{char_prompt}. Story context: {srt_desc}"
            flashback_chars.append({
                "id": f"nv{idx+1}",
                "name": char_info.get("type", f"Character {idx+1}"),
                "role": "supporting",
                "english_prompt": char_prompt
            })

        all_char_refs = ["nvc.png"]
        for fc in flashback_chars:
            char_obj = Character(
                id=fc["id"],
                name=fc["name"],
                role=fc["role"],
                vietnamese_prompt=fc["name"],
                english_prompt=fc["english_prompt"],
                image_file=f"{fc['id']}.png",
                status="pending"
            )
            workbook.add_character(char_obj)
            all_char_refs.append(f"{fc['id']}.png")
            self.logger.info(f"[FALLBACK] [v] {fc['id']}: {fc['name']}")

        # === BƯỚC 4: Lưu backup_characters ===
        backup_chars = [{
            "id": "nvc", "name": "Narrator",
            "character_lock": CHARACTER_LOCK, "costume_lock": COSTUME_LOCK,
            "image_file": "nvc.png"
        }]
        for fc in flashback_chars:
            backup_chars.append({
                "id": fc["id"], "name": fc["name"],
                "character_lock": fc["english_prompt"],
                "image_file": f"{fc['id']}.png"
            })
        workbook.save_backup_characters(backup_chars)

        # === BƯỚC 5: Tạo locations dựa trên phân tích SRT ===
        # QUAN TRỌNG: Locations cũng add vào CHARACTERS sheet (như Character với role=location)

        # Bảng mapping location type -> prompt details (American/Western style)
        location_prompts = {
            "Home Interior": {
                "lock": "American suburban home interior, comfortable sofa, family photos, warm lighting, modern decor",
                "english_prompt": "Photorealistic American home interior. Spacious living room with comfortable leather sofa, family photos on mantle, warm lamp lighting, hardwood floors. Cozy suburban atmosphere, empty room, 8K cinematic quality."
            },
            "School": {
                "lock": "American high school, lockers in hallway, classroom with desks, bright fluorescent lighting",
                "english_prompt": "Photorealistic American school scene. Empty high school hallway with metal lockers, classroom with individual desks, American flag, bright lighting. Typical US school atmosphere, no people, 8K cinematic quality."
            },
            "Hospital": {
                "lock": "modern American hospital, clean corridors, medical equipment, professional healthcare facility",
                "english_prompt": "Photorealistic American hospital scene. Modern medical facility with clean white corridors, advanced equipment, empty professional healthcare environment. Contemporary healthcare setting, no people, 8K cinematic quality."
            },
            "Park/Garden": {
                "lock": "American city park, green lawn, oak trees, park benches, peaceful atmosphere",
                "english_prompt": "Photorealistic American park scene. Beautiful city park with manicured lawns, tall oak trees, empty wooden benches. Central Park style atmosphere, peaceful and serene, no people, 8K cinematic quality."
            },
            "Beach/Ocean": {
                "lock": "California beach, golden sand, Pacific ocean waves, palm trees, pristine coastline",
                "english_prompt": "Photorealistic California beach scene. Golden sandy beach, Pacific ocean waves, palm trees swaying in breeze. West coast vibes, sunset lighting, pristine empty beach, no people, 8K cinematic quality."
            },
            "Mountain": {
                "lock": "Rocky Mountains landscape, snow-capped peaks, pine forests, vast American wilderness",
                "english_prompt": "Photorealistic Rocky Mountains landscape. Majestic snow-capped peaks, dense pine forests, crystal clear lake, American wilderness. Epic national park scenery, untouched nature, no people, 8K cinematic quality."
            },
            "City": {
                "lock": "New York City street, skyscrapers, yellow taxis, urban architecture",
                "english_prompt": "Photorealistic New York City scene. Iconic Manhattan street with towering skyscrapers, yellow taxi cabs parked, steam rising from manholes. Iconic American urban atmosphere, empty street, no people, 8K cinematic quality."
            },
            "Village/Countryside": {
                "lock": "American countryside, farmland, red barn, white picket fence, rolling hills",
                "english_prompt": "Photorealistic American countryside scene. Peaceful farmland with iconic red barn, white picket fence, golden wheat fields, rolling green hills. Heartland America atmosphere, serene landscape, no people, 8K cinematic quality."
            },
            "Restaurant/Cafe": {
                "lock": "American diner or coffee shop, booth seating, neon signs, classic Americana interior",
                "english_prompt": "Photorealistic American diner scene. Classic 50s-style diner with red leather booths, chrome counter stools, neon signs glowing, coffee and pie on counter. Nostalgic Americana atmosphere, empty establishment, no people, 8K cinematic quality."
            },
            "Office": {
                "lock": "modern American corporate office, glass walls, ergonomic desks, Silicon Valley style workspace",
                "english_prompt": "Photorealistic American office scene. Modern open-plan workspace with glass partitions, standing desks, tech startup aesthetic. Contemporary corporate America, empty office space, no people, 8K cinematic quality."
            },
            "Night Scene": {
                "lock": "American city at night, neon lights, downtown nightlife district, urban noir atmosphere",
                "english_prompt": "Photorealistic American city night scene. Downtown at night with neon signs, streetlights reflecting on wet pavement, late-night diners glowing. Cinematic noir atmosphere, empty street, no people, 8K quality."
            },
            "Rainy Scene": {
                "lock": "rainy American city, wet streets, yellow taxis, reflections on pavement",
                "english_prompt": "Photorealistic rainy New York scene. Rain falling on city streets, yellow taxis with headlights, reflections in puddles, glistening wet pavement. Melancholic urban beauty, empty street, no people, 8K cinematic quality."
            },
        }

        # Tạo locations từ phân tích SRT (kết hợp mô tả trích xuất)
        # ID format đơn giản: loc1, loc2, loc3...
        flashback_locs = [
            {"id": "loc1", "name": "Storytelling Room",  # loc1 cho narrator
             "lock": LOCATION_LOCK,
             "english_prompt": f"Photorealistic scene. {LOCATION_LOCK}. Cinematic lighting, 8K quality."}
        ]

        detected_locs = analysis.get("locations", [])
        for i, loc_type in enumerate(detected_locs[:5]):  # Max 5 detected locations
            loc_data = location_prompts.get(loc_type, {
                "lock": f"{loc_type.lower()}, atmospheric setting, cinematic mood",
                "english_prompt": f"Photorealistic {loc_type.lower()} scene. Atmospheric setting with beautiful lighting, cinematic composition. 8K quality."
            })
            # Thêm mô tả trích xuất từ SRT nếu có
            english_prompt = loc_data["english_prompt"]
            if i < len(extracted_loc_descs):
                srt_loc_desc = extracted_loc_descs[i]
                english_prompt = f"{english_prompt} Story setting: {srt_loc_desc}."
            flashback_locs.append({
                "id": f"loc{i+2}",  # loc2, loc3, loc4... (loc1 đã dùng cho narrator)
                "name": loc_type,
                "lock": loc_data["lock"],
                "english_prompt": english_prompt
            })

        # Nếu không đủ 6 locations, thêm generic (với mô tả từ SRT nếu có)
        generic_locs = ["Home Interior", "City", "Park/Garden", "Night Scene", "Village/Countryside"]
        while len(flashback_locs) < 6:
            next_id = len(flashback_locs) + 1  # loc2, loc3, loc4...
            idx = len(flashback_locs) - 1
            loc_type = generic_locs[idx % len(generic_locs)]
            if loc_type not in [l["name"] for l in flashback_locs]:
                loc_data = location_prompts.get(loc_type)
                english_prompt = loc_data["english_prompt"]
                # Thêm mô tả từ SRT nếu còn
                if idx < len(extracted_loc_descs):
                    srt_loc_desc = extracted_loc_descs[idx]
                    english_prompt = f"{english_prompt} Story setting: {srt_loc_desc}."
                flashback_locs.append({
                    "id": f"loc{next_id}",  # Đơn giản: loc2, loc3, loc4...
                    "name": loc_type,
                    "lock": loc_data["lock"],
                    "english_prompt": english_prompt
                })
        all_loc_refs = []
        backup_locs = []

        for fl in flashback_locs:
            # Tạo Character object cho location (role=location) → vào characters sheet
            loc_as_char = Character(
                id=fl["id"],
                name=fl["name"],
                role="location",  # Đánh dấu là location
                vietnamese_prompt=fl["name"],
                english_prompt=fl["english_prompt"],
                character_lock=fl["lock"],
                image_file=f"{fl['id']}.png",
                status="pending"
            )
            workbook.add_character(loc_as_char)
            all_loc_refs.append(f"{fl['id']}.png")

            # Backup data
            backup_locs.append({
                "id": fl["id"], "name": fl["name"],
                "location_lock": fl["lock"],
                "image_file": f"{fl['id']}.png"
            })

        workbook.save_backup_locations(backup_locs)
        for fl in flashback_locs:
            self.logger.info(f"[FALLBACK] [v] {fl['id']}: {fl['name']}")
        self.logger.info(f"[FALLBACK] [v] Tổng {len(flashback_locs)} locations từ phân tích SRT")

        # === BƯỚC 6: Nhóm SRT thành scenes ===
        scenes_data = group_srt_into_scenes(
            srt_entries,
            min_duration=self.min_scene_duration,
            max_duration=self.max_scene_duration
        )
        total_scenes = len(scenes_data)
        self.logger.info(f"[FALLBACK] [v] Chia thành {total_scenes} scenes từ SRT")

        # === BƯỚC 7: Xác định scenes nào là Narrator (30%) ===
        # Logic: Mỗi 10 scenes có 3 Narrator ở vị trí 1, 4, 7 (tức index 0, 3, 6)
        # Tổng quát: scene là Narrator nếu (scene_id - 1) % 10 in [0, 3, 6]
        def is_narrator_scene(scene_id: int) -> bool:
            pos_in_group = (scene_id - 1) % 10
            return pos_in_group in [0, 3, 6]  # 30%: positions 1, 4, 7 in each 10

        # === BƯỚC 8: Định nghĩa góc máy CINEMATIC ===
        # Phong cách: Hollywood blockbuster + Art house cinema
        # Mỗi góc = (shot, lens, composition, lighting, atmosphere, emotion)

        # NARRATOR: Intimate storytelling - like interview in documentary
        narrator_angles = [
            ("Intimate close-up", "85mm f/1.4 anamorphic",
             "face filling frame, eyes precisely at upper third intersection, shallow depth",
             "soft Rembrandt lighting with warm tungsten key from 45 degrees, gentle hair light",
             "dust particles floating in light beam, warm amber atmosphere, evening indoor",
             "contemplative, nostalgic, vulnerable authenticity"),

            ("Medium portrait", "50mm f/1.4",
             "head and shoulders, subject slightly left of center, negative space for thoughts",
             "butterfly lighting with soft fill, subtle rim light separating from background",
             "soft bokeh background with practical warm lamps, cozy living room depth",
             "thoughtful, wise, gentle storytelling presence"),

            ("Environmental medium", "35mm f/2",
             "upper body with hands visible, books and memories in soft background",
             "motivated window light creating natural gradient, soft shadows on face",
             "golden hour warmth spilling through curtains, floating dust motes",
             "grounded, authentic, lived-in atmosphere"),

            ("Wide establishing", "28mm f/2.8",
             "full seated figure in context, rule of thirds, leading lines from furniture",
             "practical lamp as key, ambient fill from unseen window, deep shadows",
             "rich interior atmosphere, warm pools of light, shadow play on walls",
             "intimate sanctuary, safe space for memories"),

            ("Profile contemplative", "85mm f/1.8",
             "pure profile silhouette edge, looking toward soft light source",
             "strong rim light from behind, minimal fill preserving mystery",
             "ethereal backlight glow, lens flare kiss, atmospheric haze",
             "reflective, deep in memory, bittersweet longing"),

            ("Low angle reverent", "35mm f/2",
             "slight upward angle, subject appears wise and authoritative",
             "dramatic side key with strong shadow, warm practical fill",
             "smoke or dust catching light beams, cinematic atmosphere",
             "respected elder, keeper of stories, quiet strength"),
        ]

        # FLASHBACK: Epic cinematic storytelling - blockbuster visual impact
        flashback_angles = [
            ("Epic wide master", "24mm anamorphic",
             "vast establishing shot, tiny figures in grand landscape, rule of thirds",
             "golden hour magic light, sun low on horizon, long dramatic shadows",
             "atmospheric haze, god rays through clouds, epic scale",
             "awe-inspiring, destiny awaits, journey begins"),

            ("Dramatic medium", "35mm f/1.4",
             "subject off-center with purposeful negative space, tension composition",
             "harsh chiaroscuro, single strong source, half face in shadow",
             "rain or mist in air catching light, moody urban or nature setting",
             "conflict, determination, inner struggle"),

            ("Emotional extreme close-up", "100mm f/2",
             "eyes only, crystal sharp iris detail, soul-window intimacy",
             "soft wraparound beauty light, perfect catch lights, no harsh shadows",
             "tear glistening, emotion raw, ultra shallow depth",
             "vulnerability, truth moment, heart breaking or healing"),

            ("Dutch tension", "28mm f/2",
             "15-degree tilt, unstable world, subject fighting against frame",
             "harsh directional light, deep noir shadows, anxiety inducing",
             "urban night, neon reflections, rain-slicked surfaces",
             "unease, world off-balance, crisis point"),

            ("Silhouette epic", "24mm f/2.8",
             "pure black figure against blazing sky or bright window",
             "extreme backlight, no fill, graphic bold shape",
             "sunrise/sunset fire colors, or heavenly white light",
             "transformation, hope emerging, destiny moment"),

            ("Intimate two-shot", "50mm f/1.8",
             "two figures in meaningful proximity, connection visible",
             "soft motivated light, faces both visible, emotional bridge",
             "warm interior or soft exterior, relationship atmosphere",
             "connection, love, understanding, shared moment"),

            ("Bird's eye fate", "24mm f/2.8",
             "god's view looking down, subject small in grand pattern",
             "soft even light revealing texture, no harsh shadows",
             "geometric patterns, nature fractals, life's grand design",
             "fate, insignificance, part of something greater"),

            ("Hero low angle", "24mm f/1.4",
             "powerful upward shot, subject towering, sky behind",
             "dramatic rim light, lens flare intentional, heroic lighting",
             "clouds moving, sunset fire, wind-blown elements",
             "triumph, power, overcoming, inspirational"),

            ("Memory insert", "100mm macro f/2.8",
             "hands holding meaningful object, or detail that tells story",
             "soft pool of light on subject, darkness around, spotlight effect",
             "dust on old photograph, texture of worn letter, meaningful prop",
             "nostalgia, memory trigger, emotional anchor"),

            ("Reflection poetry", "35mm f/2",
             "subject seen through window or mirror, layered reality",
             "mixed sources, reflection and real competing, dreamy blend",
             "rain on glass, condensation, imperfect reflection",
             "introspection, duality, past and present merging"),

            ("Chase/action wide", "18mm f/2.8",
             "dynamic diagonal composition, motion frozen, energy captured",
             "available light, gritty realistic, documentary feel",
             "motion blur on edges, sharp subject, speed and urgency",
             "tension, escape, pursuit, heart-pounding moment"),

            ("Peaceful resolution", "50mm f/2",
             "balanced symmetrical frame, subject at peace, harmony",
             "soft golden light, no harsh elements, gentle embracing light",
             "nature beauty, soft wind, leaves or petals floating",
             "acceptance, peace, resolution, quiet joy"),
        ]

        # Build ALL references string cho Flashback
        all_refs = all_char_refs + all_loc_refs
        all_refs_str = ", ".join(all_refs)

        # === BƯỚC 9: Tạo scenes ===
        director_plan_data = []
        narrator_count = 0
        flashback_count = 0

        for idx, scene in enumerate(scenes_data):
            scene_id = idx + 1
            srt_text = scene.get("text", "")[:400]  # Tăng lên 400 chars cho context
            srt_start = scene.get("srt_start", "00:00:00,000")
            srt_end = scene.get("srt_end", "00:00:05,000")
            duration = scene.get("duration_seconds", 5)

            if is_narrator_scene(scene_id):
                # === NARRATOR SCENE (30%) ===
                # Chỉ dùng người kể (nvc) + bối cảnh cố định (loc1)
                narrator_count += 1
                angle_idx = narrator_count % len(narrator_angles)
                shot_type, lens, composition, lighting, atmosphere, emotion = narrator_angles[angle_idx]

                # References CỐ ĐỊNH cho Narrator - an toàn, nhất quán
                # Format giống API: characters_used = comma-separated, reference_files = JSON
                characters_used = "nvc"  # Comma-separated (like API)
                location_used = "loc1"
                reference_files = '["nvc.png", "loc1.png"]'  # JSON array (like API)

                fallback_prompt = (
                    f"{shot_type}, {lens}. {composition}. "
                    f"{lighting}. {atmosphere}. "
                    f"{CHARACTER_LOCK}, {COSTUME_LOCK}. "
                    f"{LOCATION_LOCK}. "
                    f"Mood: {emotion}. "
                    f"Photorealistic, 8K cinematic quality, film grain, shallow depth of field. "
                    f"(nvc.png, loc1.png)"
                )

            else:
                # === FLASHBACK SCENE (70%) ===
                flashback_count += 1
                angle_idx = flashback_count % len(flashback_angles)
                shot_type, lens, composition, lighting, atmosphere, emotion = flashback_angles[angle_idx]

                # Format giống API: characters_used = comma-separated, reference_files = JSON
                all_char_ids = ["nvc"] + [c["id"] for c in flashback_chars]
                characters_used = ", ".join(all_char_ids)  # Comma-separated (like API)
                location_used = "loc2"  # Flashback dùng loc2 (loc1 là narrator)

                # Build reference_files từ characters_used và location_used (KHÔNG dùng all_refs)
                scene_refs = [f"{cid}.png" for cid in all_char_ids] + [f"{location_used}.png"]
                reference_files = json.dumps(scene_refs)  # Chỉ refs của scene này

                fallback_prompt = (
                    f"{shot_type}, {lens}. {composition}. "
                    f"{lighting}. {atmosphere}. "
                    f"Mood: {emotion}. "
                    f"Visual story: \"{srt_text}\". "
                    f"Photorealistic, 8K cinematic quality, film grain, anamorphic lens flare. "
                    f"({all_refs_str})"
                )

            # Tạo Scene object
            scene_obj = Scene(
                scene_id=scene_id,
                srt_start=srt_start,
                srt_end=srt_end,
                duration=duration,
                planned_duration=duration,
                srt_text=srt_text,
                img_prompt=fallback_prompt,
                video_prompt=fallback_prompt,
                status_img="pending",
                status_vid="pending",
                characters_used=characters_used,
                location_used=location_used,
                reference_files=reference_files
            )
            workbook.add_scene(scene_obj)

            # Thêm vào director_plan
            director_plan_data.append({
                "scene_id": scene_id,
                "srt_start": srt_start,
                "srt_end": srt_end,
                "duration": duration,
                "text": srt_text,
                "characters_used": characters_used,
                "location_used": location_used,
                "reference_files": reference_files,
                "img_prompt": fallback_prompt,
                "status": "backup"
            })

        # === BƯỚC 10: Lưu director_plan ===
        workbook.save_director_plan(director_plan_data)

        # Lưu Excel
        workbook.save()

        narrator_pct = round(narrator_count / total_scenes * 100) if total_scenes > 0 else 0
        flashback_pct = round(flashback_count / total_scenes * 100) if total_scenes > 0 else 0

        self.logger.info(f"[FALLBACK] [v] Excel hoàn thành:")
        self.logger.info(f"[FALLBACK]   - Narrator: {narrator_count} scenes ({narrator_pct}%) - fixed character/location")
        self.logger.info(f"[FALLBACK]   - Flashback: {flashback_count} scenes ({flashback_pct}%) - có SRT content")
        self.logger.info(f"[FALLBACK]   - ALL scenes có {len(all_refs)} references (Flow tự chọn)")
        self.logger.info(f"[FALLBACK]   - Duration từ SRT timestamps (không mặc định 5s)")

        return True
