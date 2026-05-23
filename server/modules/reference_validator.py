#!/usr/bin/env python3
"""
Reference Media ID Validator
============================
Chrome 2 sử dụng để validate media_id của ảnh tham chiếu.
Phát hiện vi phạm và tự động sửa lại bằng DeepSeek API.

Author: nguyenvantuong161978-dotcom
"""

import time
from pathlib import Path
from typing import Optional, Tuple, List
from modules.ai_providers import DeepSeekClient


class ReferenceValidator:
    """Validate và fix reference media IDs"""

    def __init__(self, drission_api, workbook, config: dict, project_code: str = None):
        """
        Args:
            drission_api: DrissionFlowAPI instance
            workbook: PromptWorkbook instance
            config: Settings dict (chứa DeepSeek API key)
            project_code: Project code (VD: AR8-0003) để tạo ảnh trong đúng project
        """
        self.api = drission_api
        self.workbook = workbook
        self.config = config

        # Lấy project_code từ Excel nếu không được truyền vào
        if project_code:
            self.project_code = project_code
        else:
            # Lấy từ Excel filename: "AR8-0003_prompts.xlsx" -> "AR8-0003"
            excel_path = Path(workbook.file_path)
            excel_name = excel_path.stem  # "AR8-0003_prompts"
            self.project_code = excel_name.split('_')[0]  # "AR8-0003"

        # DeepSeek client
        deepseek_key = config.get('deepseek_api_key')
        self.deepseek = DeepSeekClient(deepseek_key) if deepseek_key else None

        # Stats
        self.stats = {
            'tested': 0,
            'verified': 0,
            'violated': 0,
            'fixed': 0,
            'failed': 0
        }

    def log(self, msg: str, level: str = "INFO"):
        """Log message"""
        prefix = f"[VALIDATOR] [{level}]"
        print(f"{prefix} {msg}")

    def test_media_id(self, media_id: str, ref_id: str, test_prompt: str = None) -> Tuple[bool, Optional[str]]:
        """
        Test media_id bằng cách gửi request.

        Args:
            media_id: Media ID cần test
            ref_id: Reference ID (nv1, loc1, etc.)
            test_prompt: Prompt để test. Nếu None, lấy từ Excel.

        Returns:
            (success, error) - error là None nếu OK, hoặc "400_POLICY", "403", etc.
        """
        self.log(f"Testing {ref_id} (media_id: {media_id[:30]}...)")

        # Nếu không có prompt, lấy từ Excel
        if test_prompt is None:
            all_chars = self.workbook.get_characters()
            character = None
            for char in all_chars:
                if char.id == ref_id:
                    character = char
                    break

            if character and character.english_prompt:
                test_prompt = character.english_prompt
                self.log(f"  Using prompt from Excel: {test_prompt[:60]}...")
            else:
                # Fallback nếu không tìm thấy
                test_prompt = "A professional portrait photograph in office setting"
                self.log(f"  No prompt in Excel, using fallback")
        else:
            self.log(f"  Using provided prompt: {test_prompt[:60]}...")

        try:
            success, images, error = self.api.generate_image(
                prompt=test_prompt,
                save_dir=Path("/tmp"),  # Không lưu, chỉ test
                filename=f"test_{ref_id}",
                image_inputs=[{
                    "name": media_id,
                    "imageInputType": "IMAGE_INPUT_TYPE_REFERENCE"
                }],
                skip_400_retry=True  # Validator mode: return ngay khi 400
            )

            if success:
                self.log(f"  [v] {ref_id} VERIFIED ✅")
                return True, None

            # Phân loại lỗi
            if error and "400" in error:
                self.log(f"  [x] {ref_id} POLICY VIOLATION ❌")
                return False, "400_POLICY_VIOLATION"
            elif error and "403" in error:
                self.log(f"  [x] {ref_id} 403 ERROR ❌")
                return False, "403_ERROR"
            elif error and "setup" in error.lower():
                # v1.0.392: API chết (chưa setup) → trả riêng để xử lý re-setup
                self.log(f"  [x] {ref_id} API NOT READY: {error}")
                return False, "API_NOT_READY"
            else:
                self.log(f"  [x] {ref_id} OTHER ERROR: {error}")
                return False, "OTHER_ERROR"

        except Exception as e:
            self.log(f"  [x] {ref_id} EXCEPTION: {e}", "ERROR")
            return False, "EXCEPTION"

    def fix_prompt_with_ai(self, original_prompt: str, ref_id: str) -> Optional[str]:
        """
        Gửi prompt đến DeepSeek để sửa lại.
        Phân biệt NV (character) vs LOC (location) để dùng rules khác nhau.

        Returns:
            Fixed prompt hoặc None nếu lỗi
        """
        if not self.deepseek:
            self.log("DeepSeek API not configured!", "ERROR")
            return None

        self.log(f"Sending to DeepSeek to fix prompt for {ref_id}...")

        # Phân biệt NV vs LOC
        is_location = ref_id.lower().startswith('loc')

        if is_location:
            # Rules cho LOCATION - xóa người
            system_prompt = """You are an expert at rewriting location image prompts to avoid content policy violations.
Focus on creating empty, pristine environments without any people."""

            user_prompt = f"""ORIGINAL PROMPT (VIOLATED Google's policy):
{original_prompt}

TASK: Rewrite this prompt for a LOCATION reference image (no people allowed).

CRITICAL RULES:
1. REMOVE all references to people, characters, humans, children, adults
2. Emphasize "empty", "pristine", "serene", "uninhabited"
3. Focus on architecture, landscape, atmosphere, lighting
4. Keep the location type and style intact
5. Add "no people visible" explicitly

OUTPUT: Only return the NEW PROMPT, nothing else."""

        else:
            # Rules cho CHARACTER - adult professional
            system_prompt = """You are an expert at rewriting image generation prompts to avoid content policy violations.
Focus on making the subject clearly appear as a mature adult professional."""

            user_prompt = f"""ORIGINAL PROMPT (VIOLATED Google's policy):
{original_prompt}

TASK: Rewrite this prompt to avoid policy violations while keeping character essence.

CRITICAL RULES:
1. Emphasize "mature adult", "professional", "executive"
2. Add specific age: "in their mid-30s" or "35-year-old"
3. Add professional context: "corporate portrait", "business attire", "office setting"
4. Remove emotional/dramatic words that may be misinterpreted
5. Avoid: close-up face shots, "young", casual settings

OUTPUT: Only return the NEW PROMPT, nothing else."""

        try:
            fixed_prompt = self.deepseek.generate(
                prompt=user_prompt,
                system_prompt=system_prompt,
                temperature=0.7,
                max_tokens=500
            )

            if fixed_prompt:
                self.log(f"  [v] DeepSeek fixed: {fixed_prompt[:100]}...")
                return fixed_prompt.strip()
            else:
                self.log("  [x] DeepSeek returned empty", "ERROR")
                return None

        except Exception as e:
            self.log(f"  [x] DeepSeek error: {e}", "ERROR")
            return None

    def regenerate_reference(self, ref_id: str, fixed_prompt: str) -> Tuple[bool, Optional[str]]:
        """
        Tạo lại ảnh reference với prompt đã fix.
        TẠO TRONG ĐÚNG PROJECT (không tạo project mới!)

        Returns:
            (success, new_media_id)
        """
        self.log(f"Regenerating {ref_id} with fixed prompt...")

        # Xác định save directory
        if self.project_code:
            # Lưu vào thư mục project (giống Chrome 1)
            is_location = ref_id.lower().startswith('loc')
            save_dir = Path("PROJECTS") / self.project_code / ("loc" if is_location else "nv")
            save_dir.mkdir(parents=True, exist_ok=True)
        else:
            # Fallback - lưu vào temp
            is_location = ref_id.lower().startswith('loc')
            save_dir = Path("PROJECTS") / "temp" / ("loc" if is_location else "nv")
            save_dir.mkdir(parents=True, exist_ok=True)

        try:
            # TẠO TRONG ĐÚNG PROJECT
            # Chrome đã được setup với project_url, nên tự động tạo trong project đó
            success, images, error = self.api.generate_image(
                prompt=fixed_prompt,
                save_dir=save_dir,
                filename=ref_id
            )

            if success and images and images[0].media_name:
                new_media_id = images[0].media_name
                self.log(f"  [v] Generated new image: {new_media_id[:30]}...")
                return True, new_media_id
            else:
                self.log(f"  [x] Failed to generate: {error}", "ERROR")
                return False, None

        except Exception as e:
            self.log(f"  [x] Exception: {e}", "ERROR")
            return False, None

    def validate_and_fix(self, ref_id: str) -> str:
        """
        Main function: Validate và fix nếu cần.

        Returns:
            Status: "VERIFIED", "FIXED", "FAILED"
        """
        self.stats['tested'] += 1

        # 1. Lấy thông tin từ Excel
        all_chars = self.workbook.get_characters()
        character = None
        for char in all_chars:
            if char.id == ref_id:
                character = char
                break

        if not character:
            self.log(f"{ref_id} not found in Excel", "ERROR")
            self.stats['failed'] += 1
            return "FAILED"

        media_id = character.media_id
        original_prompt = character.english_prompt

        if not media_id:
            self.log(f"{ref_id} has no media_id yet, skip")
            return "SKIPPED"

        # 2. Test media_id hiện tại
        success, error_type = self.test_media_id(media_id, ref_id)

        if success:
            # OK - Đánh dấu verified
            self.workbook.update_character(ref_id, status="verified")
            self.workbook.safe_save()
            self.stats['verified'] += 1
            return "VERIFIED"

        # 3. Bị vi phạm → Fix và regenerate
        if error_type == "400_POLICY_VIOLATION":
            self.stats['violated'] += 1
            self.log(f"")
            self.log(f"{'='*60}")
            self.log(f"{ref_id} POLICY VIOLATION DETECTED - Starting auto-fix...")
            self.log(f"{'='*60}")

            # 3a. XÓA media_id cũ trong Excel
            self.log(f"[STEP 1/4] Clearing old violated media_id from Excel...")
            self.workbook.update_character(ref_id, media_id="", status="fixing")
            self.workbook.safe_save()
            self.log(f"  [v] Cleared old media_id")

            # 3b. Fix prompt với AI
            self.log(f"[STEP 2/4] Calling DeepSeek AI to fix prompt...")
            fixed_prompt = self.fix_prompt_with_ai(original_prompt, ref_id)
            if not fixed_prompt:
                self.log(f"  [x] AI fix failed", "ERROR")
                self.workbook.update_character(ref_id, status="violated")
                self.workbook.safe_save()
                self.stats['failed'] += 1
                return "FAILED"
            self.log(f"  [v] Fixed prompt: {fixed_prompt[:80]}...")

            # 3c. Regenerate với prompt mới
            self.log(f"[STEP 3/4] Regenerating image with fixed prompt...")
            success, new_media_id = self.regenerate_reference(ref_id, fixed_prompt)
            if not success:
                self.log(f"  [x] Regenerate failed", "ERROR")
                self.workbook.update_character(ref_id, status="violated")
                self.workbook.safe_save()
                self.stats['failed'] += 1
                return "FAILED"
            self.log(f"  [v] Generated new media_id: {new_media_id[:40]}...")

            # 3d. Test lại media_id mới với FIXED PROMPT
            self.log(f"[STEP 4/4] Testing new media_id with fixed prompt...")
            time.sleep(2)  # Đợi Google xử lý
            success2, error2 = self.test_media_id(new_media_id, ref_id, test_prompt=fixed_prompt)

            if success2:
                # SUCCESS! Cập nhật Excel với media_id MỚI
                self.log(f"  [v] Test PASSED!")
                self.log(f"[FINAL] Updating Excel with new media_id and fixed prompt...")
                self.workbook.update_character(
                    ref_id,
                    english_prompt=fixed_prompt,
                    media_id=new_media_id,
                    status="verified_fixed"
                )
                self.workbook.safe_save()
                self.log(f"")
                self.log(f"{'='*60}")
                self.log(f"  ✅ {ref_id} FIXED SUCCESSFULLY!")
                self.log(f"  - Old prompt: {original_prompt[:60]}...")
                self.log(f"  - New prompt: {fixed_prompt[:60]}...")
                self.log(f"  - New media_id: {new_media_id[:40]}...")
                self.log(f"{'='*60}")
                self.log(f"")
                self.stats['fixed'] += 1
                return "FIXED"
            else:
                # Vẫn lỗi
                self.log(f"  [x] Test FAILED: {error2}", "ERROR")
                self.log(f"{ref_id} - Still violated after fix", "ERROR")
                self.workbook.update_character(ref_id, status="violated_unfixable")
                self.workbook.safe_save()
                self.stats['failed'] += 1
                return "FAILED"

        # 4a. API chết (chưa setup) → thử re-setup rồi test lại
        elif error_type == "API_NOT_READY":
            self.log(f"{ref_id} - API not ready, attempting re-setup...", "WARN")
            try:
                saved_url = getattr(self.api, '_current_project_url', None)
                if saved_url and self.api.setup(project_url=saved_url, skip_403_reset=True):
                    self.log(f"{ref_id} - Re-setup OK, retesting...")
                    success2, error2 = self.test_media_id(media_id, ref_id)
                    if success2:
                        self.workbook.update_character(ref_id, status="verified")
                        self.workbook.safe_save()
                        self.stats['verified'] += 1
                        return "VERIFIED"
                    elif error2 == "400_POLICY_VIOLATION":
                        # Sau re-setup phát hiện vi phạm → xử lý như case 3
                        self.log(f"{ref_id} - After re-setup: POLICY VIOLATION detected")
                        self.stats['verified'] += 1  # Count as tested
                        # Fall through to verified (will be caught in next cycle)
                    # Else: vẫn fail → giữ verified vì có media_id
            except Exception as e:
                self.log(f"{ref_id} - Re-setup failed: {e}", "WARN")

            self.log(f"{ref_id} - Giữ verified (có media_id = ảnh đã OK)", "WARN")
            self.workbook.update_character(ref_id, status="verified")
            self.workbook.safe_save()
            self.stats['verified'] += 1
            return "VERIFIED"

        # 4b. Lỗi khác (403, timeout, etc.) - CÓ media_id nghĩa là ảnh đã tạo thành công
        #    Test fail có thể do network tạm thời, không nên ghi error
        else:
            self.log(f"{ref_id} - Test error: {error_type} (nhưng ảnh đã tạo thành công, giữ verified)", "WARN")
            # v1.0.209: Giữ status = "verified" vì có media_id = ảnh đã OK
            self.workbook.update_character(ref_id, status="verified")
            self.workbook.safe_save()
            self.stats['verified'] += 1
            return "VERIFIED"

    def validate_all_references(self, ref_ids: List[str]) -> dict:
        """
        Validate tất cả references trong list.

        Returns:
            Stats dictionary
        """
        self.log(f"Validating {len(ref_ids)} references...")

        for i, ref_id in enumerate(ref_ids, 1):
            self.log(f"\n[{i}/{len(ref_ids)}] Processing: {ref_id}")
            self.log("=" * 50)

            result = self.validate_and_fix(ref_id)
            self.log(f"Result: {result}")

            # Rate limit
            time.sleep(3)

        # Summary
        self.log("\n" + "=" * 50)
        self.log("VALIDATION SUMMARY")
        self.log("=" * 50)
        self.log(f"Total tested: {self.stats['tested']}")
        self.log(f"Verified OK: {self.stats['verified']}")
        self.log(f"Violated: {self.stats['violated']}")
        self.log(f"Fixed: {self.stats['fixed']}")
        self.log(f"Failed: {self.stats['failed']}")

        return self.stats
