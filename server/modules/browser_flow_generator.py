"""
VE3 Tool - Browser Flow Generator Module
========================================
Tich hop browser automation voi Excel workflow.

Workflow:
1. Doc Excel prompts (PromptWorkbook)
2. Lay cac scenes chua tao anh (status_img != 'done')
3. Mo trinh duyet, inject JS
4. Goi VE3.run() voi [{sceneId, prompt}]
5. Di chuyen file tu Downloads -> project/img/
6. Cap nhat Excel (img_path, status_img = 'done')

Usage:
    from modules.browser_flow_generator import BrowserFlowGenerator

    gen = BrowserFlowGenerator("PROJECTS/KA1-0001")
    gen.generate_all_images()
"""

import os
import sys

# Fix Windows encoding issues - must be at module level
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
import time
import json
import shutil
import glob
import base64
import threading
from pathlib import Path
from typing import Optional, List, Dict, Any, Tuple
from datetime import datetime

# Import PromptWorkbook
from modules.excel_manager import PromptWorkbook, Scene
from modules.utils import get_logger, load_settings

# Browser driver imports - PREFER SELENIUM (more stable)
DRIVER_TYPE = None

try:
    from selenium import webdriver
    from selenium.webdriver.common.by import By
    from selenium.webdriver.chrome.options import Options
    from selenium.webdriver.chrome.service import Service
    from selenium.webdriver.support.ui import WebDriverWait
    from selenium.webdriver.support import expected_conditions as EC
    from selenium.common.exceptions import (
        TimeoutException,
        WebDriverException,
        JavascriptException
    )
    DRIVER_TYPE = "selenium"
except ImportError:
    DRIVER_TYPE = None

SELENIUM_AVAILABLE = DRIVER_TYPE is not None


class BrowserFlowGenerator:
    """
    Tao anh tu Excel bang browser automation.

    Su dung JavaScript injection de dieu khien Google Flow.
    Tu dong di chuyen file tu Downloads va cap nhat Excel.
    """

    FLOW_URL = "https://labs.google/fx/vi/tools/flow"

    def __init__(
        self,
        project_path: str,
        profile_name: str = "main",
        headless: bool = False,
        verbose: bool = True,
        config_path: str = "config/settings.yaml",
        worker_id: int = 0,
        total_workers: int = 1,
        chrome_portable: str = None
    ):
        """
        Khoi tao BrowserFlowGenerator.

        Args:
            project_path: Duong dan den thu muc project (PROJECTS/KA1-0001)
            profile_name: Ten Chrome profile (luu trong chrome_profiles/)
            headless: Chay an (khong hien UI) - nen False lan dau de dang nhap
            verbose: In log chi tiet
            config_path: Duong dan file config
            worker_id: Worker ID for parallel processing (affects proxy, Chrome port)
            total_workers: Total number of workers (for window layout)
            chrome_portable: Chrome Portable path (overrides config)
        """
        if not SELENIUM_AVAILABLE:
            raise ImportError(
                "Selenium chua duoc cai dat. "
                "Chay: pip install selenium undetected-chromedriver"
            )

        self.project_path = Path(project_path)
        self.profile_name = profile_name
        self.headless = headless
        self.verbose = verbose
        self.worker_id = worker_id  # For parallel processing
        self.total_workers = total_workers  # For window layout

        # Load config
        self.config = {}
        config_file = Path(config_path)
        if config_file.exists():
            self.config = load_settings(config_file)  # Pass Path object, not string

        # Override chrome_portable if passed (for Chrome 2 parallel mode)
        if chrome_portable:
            self.config['chrome_portable'] = chrome_portable
            self._chrome_portable_override = True
        else:
            self._chrome_portable_override = False

        # Paths
        self.img_path = self.project_path / "img"
        self.nv_path = self.project_path / "nv"

        # Check flat vs nested structure for prompts
        # Flat: Excel trực tiếp trong project folder
        # Nested: Excel trong prompts/ subfolder
        if list(self.project_path.glob("*_prompts.xlsx")):
            self.prompts_path = self.project_path  # Flat structure
            self.use_flat_structure = True
        else:
            self.prompts_path = self.project_path / "prompts"  # Nested structure
            self.use_flat_structure = False

        # Tao thu muc neu chua co (chi img va nv, KHONG tao prompts cho flat structure)
        self.img_path.mkdir(parents=True, exist_ok=True)
        self.nv_path.mkdir(parents=True, exist_ok=True)

        # Chrome profile
        base_dir = Path(__file__).parent.parent
        profiles_dir = self.config.get("browser_profiles_dir", "./chrome_profiles")
        if not os.path.isabs(profiles_dir):
            profiles_dir = base_dir / profiles_dir
        self.profile_dir = Path(profiles_dir) / profile_name
        self.profile_dir.mkdir(parents=True, exist_ok=True)

        # Downloads folder - noi browser tai anh ve
        self.downloads_dir = Path.home() / "Downloads"

        # Project code (dung cho ten file)
        self.project_code = self.project_path.name  # VD: KA1-0001

        # Driver
        self.driver = None
        self._js_injected = False
        self._project_url = ""  # Luu project URL de giu nguyen phien lam viec

        # Logger
        self.logger = get_logger("browser_flow")

        # Stats
        self.stats = {
            "total": 0,
            "success": 0,
            "failed": 0,
            "skipped": 0,
        }

    def _log(self, message: str, level: str = "info") -> None:
        """Print log message."""
        if self.verbose:
            timestamp = datetime.now().strftime("%H:%M:%S")
            icons = {
                "info": "[INFO]",
                "success": "[OK]",
                "error": "[ERROR]",
                "warn": "[WARN]",
            }
            full_msg = f"[{timestamp}] {icons.get(level, '')} {message}"
            # Handle encoding issues on Windows console
            try:
                print(full_msg)
            except UnicodeEncodeError:
                print(full_msg.encode('ascii', 'replace').decode('ascii'))

    def _get_profile_path(self) -> Optional[str]:
        """
        Lấy Chrome profile path cho worker này.
        Mỗi worker cần profile riêng để chạy song song.
        """
        base_dir = Path(__file__).parent.parent
        profiles_dir = base_dir / "chrome_profiles"

        # === PARALLEL MODE: Mỗi worker dùng profile khác nhau ===
        if profiles_dir.exists():
            # Lấy danh sách profiles có sẵn (đã đăng nhập từ GUI)
            available_profiles = sorted([
                p for p in profiles_dir.iterdir()
                if p.is_dir() and not p.name.startswith('.')
            ])

            if available_profiles:
                # Worker 0 → profile[0], Worker 1 → profile[1], ...
                worker_id = getattr(self, 'worker_id', 0) or 0
                profile_idx = worker_id % len(available_profiles)
                selected_profile = available_profiles[profile_idx]
                self._log(f"[Worker {worker_id}] Dùng profile: {selected_profile.name}")
                return str(selected_profile)

        # Fallback: từ settings.yaml
        chrome_profile = self.config.get('chrome_profile', '')
        if chrome_profile:
            profile_path = Path(chrome_profile)
            if profile_path.exists():
                return str(profile_path)
            abs_path = Path.cwd() / chrome_profile
            if abs_path.exists():
                return str(abs_path)

        # Fallback: dùng tool profile
        if hasattr(self, 'profile_dir') and self.profile_dir:
            return str(self.profile_dir)

        return None

    def _find_excel_file(self) -> Optional[Path]:
        """Tim file Excel prompts trong project."""
        # Tim trong prompts/
        for pattern in ["*_prompts.xlsx", "*.xlsx"]:
            files = list(self.prompts_path.glob(pattern))
            if files:
                return files[0]

        # Tim trong project root
        for pattern in ["*_prompts.xlsx", "*.xlsx"]:
            files = list(self.project_path.glob(pattern))
            if files:
                return files[0]

        return None

    def _get_js_script(self) -> str:
        """Doc file JavaScript automation."""
        script_path = Path(__file__).parent.parent / "scripts" / "ve3_browser_automation.js"

        if script_path.exists():
            with open(script_path, "r", encoding="utf-8") as f:
                return f.read()

        raise FileNotFoundError(f"JS script khong tim thay: {script_path}")

    def _create_driver(self):
        """
        Tao Chrome WebDriver - PARALLEL SAFE.
        - Moi instance dung port rieng (khong xung dot)
        - Dung working profile rieng (giu nguyen settings nhu download permission)
        - Mac dinh headless (chay an)
        """
        import random

        # Download prefs
        prefs = {
            "download.default_directory": str(self.downloads_dir),
            "download.prompt_for_download": False,
            "download.directory_upgrade": True,
            "safebrowsing.enabled": True,
        }

        self._log(f"Headless: {self.headless}")
        self._log("Su dung Selenium WebDriver (Parallel Safe)")

        try:
            options = Options()

            # Tim Chrome binary - ƯU TIÊN chrome_portable từ config
            chrome_portable = self.config.get('chrome_portable', '')
            chrome_portable_user_data = None

            if chrome_portable:
                chrome_dir = Path(chrome_portable).parent

                # Dùng trực tiếp chrome_portable (launcher tự chạy đúng Chrome)
                options.binary_location = chrome_portable
                self._log(f"Chrome: {chrome_portable}")

                # Tìm User Data của Chrome portable
                # GoogleChromePortable/Data/profile hoặc GoogleChromePortable/User Data
                for data_path in [chrome_dir / "Data" / "profile", chrome_dir / "User Data"]:
                    if data_path.exists():
                        chrome_portable_user_data = data_path
                        break
            else:
                # Fallback: Tim Chrome system
                chrome_paths = [
                    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
                    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
                    "/usr/bin/google-chrome",
                    "/usr/bin/chromium-browser",
                ]
                for chrome_path in chrome_paths:
                    if os.path.exists(chrome_path):
                        options.binary_location = chrome_path
                        self._log(f"Chrome: {chrome_path}")
                        break

            # Xử lý User Data
            if chrome_portable_user_data:
                # Dùng User Data của Chrome portable (đã đăng nhập sẵn)
                self._log(f"User Data: {chrome_portable_user_data}")
                options.add_argument(f"--user-data-dir={chrome_portable_user_data}")
                self._working_profile = str(chrome_portable_user_data)
            else:
                # Tao working profile rieng (khong phai temp, giu nguyen settings)
                # Vi tri: ~/.ve3_chrome_profiles/{profile_name}
                self.profile_dir.mkdir(parents=True, exist_ok=True)
                working_profile_base = Path.home() / ".ve3_chrome_profiles"
                working_profile_base.mkdir(parents=True, exist_ok=True)
                working_profile = working_profile_base / self.profile_name

                self._log(f"Profile goc: {self.profile_dir}")
                self._log(f"Working profile: {working_profile}")

                # LUÔN sync cookies/login data từ profile gốc (đảm bảo dùng đúng account đã đăng nhập)
                import shutil
                if not working_profile.exists():
                    working_profile.mkdir(parents=True, exist_ok=True)

                # Sync các file quan trọng (cookies, login data) mỗi lần chạy
                critical_files = [
                    "Cookies", "Login Data", "Web Data",
                    "Network/Cookies", "Network/TransportSecurity"
                ]
                critical_dirs = ["Default", "Network"]

                if any(self.profile_dir.iterdir()):
                    # Sync critical dirs first
                    for dir_name in critical_dirs:
                        src_dir = self.profile_dir / dir_name
                        if src_dir.exists() and src_dir.is_dir():
                            dest_dir = working_profile / dir_name
                            dest_dir.mkdir(parents=True, exist_ok=True)
                            for item in src_dir.iterdir():
                                try:
                                    dest = dest_dir / item.name
                                    if item.is_file():
                                        shutil.copy2(item, dest)
                                except Exception:
                                    pass  # Skip locked files

                    # Sync root level files
                    for item in self.profile_dir.iterdir():
                        try:
                            dest = working_profile / item.name
                            if item.is_file():
                                shutil.copy2(item, dest)
                            elif item.is_dir() and item.name not in critical_dirs:
                                if not dest.exists():
                                    shutil.copytree(item, dest, dirs_exist_ok=True)
                        except Exception:
                            pass  # Skip locked files

                    self._log(f"Da sync profile data tu profile goc")

                self._working_profile = str(working_profile)  # Luu de reference
                options.add_argument(f"--user-data-dir={working_profile}")

            # PARALLEL SAFE: Moi instance dung port rieng
            debug_port = random.randint(9222, 9999)
            options.add_argument(f"--remote-debugging-port={debug_port}")

            # Headless mac dinh (chay an, toi uu cho auto)
            if self.headless:
                options.add_argument("--headless=new")
                options.add_argument("--disable-gpu")

            # Cac options cho automation
            options.add_argument("--no-sandbox")
            options.add_argument("--disable-dev-shm-usage")
            options.add_argument("--disable-blink-features=AutomationControlled")
            options.add_argument("--disable-extensions")
            options.add_argument("--disable-infobars")
            options.add_argument("--window-size=1920,1080")
            options.add_experimental_option("excludeSwitches", ["enable-automation", "enable-logging"])
            options.add_experimental_option("useAutomationExtension", False)
            options.add_experimental_option("prefs", prefs)

            self._log(f"Dang khoi dong Chrome (port {debug_port})...")
            driver = webdriver.Chrome(options=options)

            # An webdriver flag
            driver.execute_script(
                "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
            )

            self._log("Chrome da san sang!", "success")

            return driver

        except Exception as e:
            self._log(f"Loi khoi dong Chrome: {e}", "error")
            import traceback
            traceback.print_exc()
            raise

    def start_browser(self, use_cached_project: bool = True) -> bool:
        """
        Khoi dong trinh duyet va navigate den Google Flow.

        Args:
            use_cached_project: Neu True, se vao project cu (tu cache) de giu media_name valid

        QUAN TRONG: Khi tao img, phai vao DUNG project da tao nv
        de media_name cua nv con valid cho reference.

        Returns:
            True neu thanh cong
        """
        self._log("Khoi dong trinh duyet...")

        try:
            self.driver = self._create_driver()
            self._log("Da khoi dong Chrome", "success")

            # Tang timeout cho async script (mac dinh 30s, can nhieu hon cho upload/generate)
            self.driver.set_script_timeout(300)  # 5 phut
            self._log("Set script timeout: 300s")

            # QUAN TRONG: Kiem tra cached project URL
            # Neu co, vao project cu de giu media_name valid
            target_url = self.FLOW_URL
            cached_url = getattr(self, '_cached_project_url', None)

            if use_cached_project and cached_url:
                self._log(f"[REUSE PROJECT] Vao project cu de giu media_name valid")
                self._log(f"  -> URL: {cached_url[:60]}...")
                target_url = cached_url
            else:
                self._log(f"Navigate den: {self.FLOW_URL}")

            self.driver.get(target_url)

            # Cho page load
            time.sleep(5)

            return True

        except Exception as e:
            self._log(f"Loi khoi dong: {e}", "error")
            return False

    def stop_browser(self) -> None:
        """Dong trinh duyet (giu nguyen working profile de luu settings)."""
        if self.driver:
            self._log("Dong trinh duyet...")
            try:
                self.driver.quit()
            except:
                pass
            self.driver = None
            self._js_injected = False
            # Khong xoa working profile - giu nguyen de luu settings (download permission, etc.)

    def wait_for_login(self, timeout: int = 300) -> bool:
        """
        Cho nguoi dung dang nhap.

        Args:
            timeout: Thoi gian cho (giay)

        Returns:
            True neu da dang nhap
        """
        self._log(f"Cho dang nhap (timeout: {timeout}s)...")
        self._log("Vui long dang nhap Google account tren trinh duyet")

        start_time = time.time()

        while time.time() - start_time < timeout:
            try:
                textarea = self.driver.find_element(By.CSS_SELECTOR, "textarea")
                if textarea:
                    self._log("Da phat hien dang nhap thanh cong!", "success")
                    return True
            except:
                pass

            time.sleep(2)

        self._log("Timeout - chua dang nhap", "error")
        return False

    def _inject_js(self) -> bool:
        """Inject JavaScript automation script."""
        if self._js_injected:
            return True

        try:
            self._log("Buoc 1: Inject JavaScript script...")
            js_code = self._get_js_script()
            self.driver.execute_script(js_code)

            # Init VE3 voi project name
            self._log("Buoc 2: Init VE3...")
            self.driver.execute_script(f'VE3.init("{self.project_code}")')

            # Setup UI: Click "Du an moi" + Chon "Tao hinh anh"
            self._log("Buoc 3: Setup UI (Du an moi + Tao hinh anh)...")
            setup_result = self.driver.execute_async_script("""
                const callback = arguments[arguments.length - 1];
                (async () => {
                    try {
                        await VE3.setup();
                        callback({success: true});
                    } catch(e) {
                        callback({success: false, error: e.message});
                    }
                })();
            """)

            if setup_result and setup_result.get('success'):
                self._log("Setup UI thanh cong!", "success")
                # Luu project URL de giu nguyen phien
                self._project_url = self._get_project_url_from_js()
                if self._project_url:
                    self._log(f"Project URL: {self._project_url}", "info")
                    # === QUAN TRONG: Extract project_id va luu vao config ===
                    # De API mode co the dung chung project
                    if '/project/' in self._project_url:
                        try:
                            project_id = self._project_url.split('/project/')[1].split('/')[0].split('?')[0]
                            if project_id:
                                self.config['flow_project_id'] = project_id
                                self._log(f"  -> Saved project_id: {project_id[:8]}...", "info")
                        except Exception as e:
                            self._log(f"  -> Could not extract project_id: {e}", "warn")
            else:
                error = setup_result.get('error', 'Unknown') if setup_result else 'No response'
                self._log(f"Setup UI that bai: {error}", "warn")

            self._js_injected = True
            self._log("Da san sang tao anh!", "success")
            return True

        except Exception as e:
            self._log(f"Loi inject JS: {e}", "error")
            import traceback
            traceback.print_exc()
            return False

    # =========================================================================
    # MEDIA NAMES CACHE - Luu media_name de reference
    # =========================================================================

    def _get_media_cache_path(self) -> Path:
        """Duong dan file cache media_names (flat hoặc nested)."""
        if getattr(self, 'use_flat_structure', False):
            return self.project_path / ".media_cache.json"
        return self.project_path / "prompts" / ".media_cache.json"

    def _load_media_cache(self) -> Dict[str, Any]:
        """
        Load media_names tu cache file.

        Format moi: {
            "_project_url": "https://...",
            "_project_id": "xxx",
            "_bearer_token": "ya29.xxx",
            "_token_time": 1234567890.0,
            "nvc": {mediaName: str, seed: int|null},
            ...
        }
        Backward compatible voi format cu: {id: str}
        """
        cache_path = self._get_media_cache_path()
        if cache_path.exists():
            try:
                with open(cache_path, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    # Extract project info
                    project_url = data.pop('_project_url', None)
                    project_id = data.pop('_project_id', None)
                    bearer_token = data.pop('_bearer_token', None)
                    token_time = data.pop('_token_time', None)

                    if project_url:
                        self._cached_project_url = project_url
                        self._log(f"[CACHE] Project URL: {project_url[:50]}...")
                    if project_id:
                        self._cached_project_id = project_id
                        self.config['flow_project_id'] = project_id  # Set vào config
                        self._log(f"[CACHE] Project ID: {project_id[:20]}...")

                    # LUON dung cached token truoc - neu API tra ve 401 thi moi refresh
                    # Khong dua vao thoi gian vi khong dang tin cay
                    if bearer_token:
                        import time
                        self._cached_bearer_token = bearer_token
                        self._cached_token_time = token_time or time.time()
                        self.config['flow_bearer_token'] = bearer_token
                        if token_time:
                            age_minutes = (time.time() - token_time) / 60
                            self._log(f"[CACHE] Token loaded ({age_minutes:.1f} phút) - TRY FIRST, refresh if API fails")
                        else:
                            self._log(f"[CACHE] Token loaded - TRY FIRST, refresh if API fails")

                    self._log(f"[CACHE] Loaded {len(data)} media_names")
                    return data
            except Exception as e:
                self._log(f"Error loading cache: {e}", "warn")
        return {}

    def _save_media_cache(self, media_names: Dict[str, Any]) -> None:
        """
        Luu media_names vao cache file.

        Format: {
            "_project_url": "https://...",
            "_project_id": "xxx",
            "_bearer_token": "ya29.xxx",
            "_token_time": 1234567890.0,
            "nvc": {mediaName: str, seed: int|null},
            ...
        }

        QUAN TRONG: Luu project_url VA token de khi tao img co the:
        1. Vao dung project (giu media_name valid)
        2. Reuse token (khong can mo Chrome lai)
        """
        import time
        cache_path = self._get_media_cache_path()
        try:
            cache_path.parent.mkdir(parents=True, exist_ok=True)

            # Add project info to cache
            cache_data = dict(media_names)  # Copy to avoid modifying original

            # Get project URL from current session or config
            project_url = getattr(self, '_project_url', None)
            project_id = self.config.get('flow_project_id', '')
            bearer_token = self.config.get('flow_bearer_token', '')

            if project_url:
                cache_data['_project_url'] = project_url
                self._log(f"[CACHE] Saving project_url: {project_url[:50]}...")
            if project_id:
                cache_data['_project_id'] = project_id
                self._log(f"[CACHE] Saving project_id: {project_id[:20]}...")

            # QUAN TRONG: Luu token de reuse (khong can mo Chrome lai)
            if bearer_token:
                cache_data['_bearer_token'] = bearer_token
                cache_data['_token_time'] = time.time()  # Luu thoi diem lay token
                self._log(f"[CACHE] Saving bearer_token: {bearer_token[:30]}... (de reuse)")

            # Lưu thêm recaptcha_token và x_browser_validation cho I2V
            recaptcha_token = self.config.get('flow_recaptcha_token', '')
            x_browser_val = self.config.get('flow_x_browser_validation', '')
            if recaptcha_token:
                cache_data['_recaptcha_token'] = recaptcha_token
                self._log(f"[CACHE] Saving recaptcha_token (cho I2V)")
            if x_browser_val:
                cache_data['_x_browser_validation'] = x_browser_val

            # Lưu Chrome profile path để VIDEO worker có thể mở đúng Chrome
            chrome_profile = self.config.get('chrome_profile_path', '') or str(getattr(self, 'chrome_profile', ''))
            if chrome_profile:
                cache_data['_chrome_profile_path'] = chrome_profile
                self._log(f"[CACHE] Saving chrome_profile_path: {chrome_profile}")

            with open(cache_path, 'w', encoding='utf-8') as f:
                json.dump(cache_data, f, indent=2)
            self._log(f"[CACHE] Saved {len(media_names)} media_names + token + project")
        except Exception as e:
            self._log(f"Loi save cache: {e}", "warn")

    def _get_media_names_from_js(self) -> Dict[str, Any]:
        """
        Lay tat ca media_names tu JS.

        Returns:
            Dict voi format: {id: {mediaName: str, seed: int|null}}
        """
        if not self.driver:
            return {}
        try:
            return self.driver.execute_script("return VE3.getMediaNames();") or {}
        except:
            return {}

    def _get_project_url_from_js(self) -> str:
        """Lay project URL tu JS."""
        if not self.driver:
            return ""
        try:
            return self.driver.execute_script("return VE3.getProjectUrl();") or ""
        except:
            return ""

    def _load_media_names_to_js(self, media_names: Dict[str, Any]) -> None:
        """
        Load media_names vao JS tu cache.

        Supports ca format cu (string) va moi ({mediaName, seed}).
        JS se xu ly chinh xac dua vao format.
        """
        if not self.driver or not media_names:
            return
        try:
            self.driver.execute_script(f"VE3.setMediaNames({json.dumps(media_names)});")
        except Exception as e:
            self._log(f"Loi load media_names to JS: {e}", "warn")

    def _is_child_character(self, char_id: str) -> bool:
        """
        Check if a character ID represents a child (under 15 years old).
        Children cannot use reference images due to API policy.

        Child patterns:
        - nvc1 (exactly) = narrator as child
        - nv1c (exactly) = character 1 as child
        - *_child, *-child = any character with child suffix
        - *child* (but not just containing 'child' in middle of word)
        """
        if not char_id:
            return False

        char_id_clean = char_id.replace('.png', '').replace('.jpg', '').replace('.jpeg', '').replace('.webp', '').lower().strip()

        # Exact child character IDs (narrator/character as child)
        exact_child_ids = ['nvc1', 'nv1c', 'nvc_child', 'nv1_child', 'child']

        if char_id_clean in exact_child_ids:
            self._log(f"[CHILD] {char_id} matched exact child ID: {char_id_clean}", "info")
            return True

        # Suffix patterns for child characters
        if char_id_clean.endswith('_child') or char_id_clean.endswith('-child'):
            self._log(f"[CHILD] {char_id} matched child suffix pattern", "info")
            return True

        # Pattern: nvc followed by single digit 1 (nvc1 but not nvc10, nvc11, etc.)
        # This is for narrator-child-version-1
        import re
        if re.match(r'^nvc1$', char_id_clean):  # Exactly nvc1
            self._log(f"[CHILD] {char_id} matched nvc1 pattern", "info")
            return True

        return False

    def _filter_children_from_refs(self, ref_files: List[str]) -> List[str]:
        """
        Filter out child characters from reference_files list.
        Children under 15 should not be uploaded as reference.
        """
        if not ref_files:
            return []

        filtered = []
        for ref in ref_files:
            if self._is_child_character(ref):
                self._log(f"[FILTER] Bo qua tre em: {ref}", "info")
                continue
            filtered.append(ref)

        return filtered

    def _upload_reference_images(self, reference_files: List[str]) -> bool:
        """
        Upload cac anh reference truoc khi tao scene.

        Args:
            reference_files: List ten file (vd: ["nvc.png", "nv1.png"])

        Returns:
            True neu upload thanh cong
        """
        if not reference_files or not self.driver:
            return True

        self._log(f"[UPLOAD] Input reference_files: {reference_files}", "info")
        self._log(f"[UPLOAD] nv_path: {self.nv_path}", "info")

        # Filter out children under 15
        filtered_refs = self._filter_children_from_refs(reference_files)
        if not filtered_refs:
            self._log("[UPLOAD] Khong con anh nao sau khi filter tre em", "info")
            return True

        self._log(f"[UPLOAD] After filter children: {filtered_refs}", "info")

        images_to_upload = []

        for ref_file in filtered_refs:
            # Xac dinh duong dan file - ref_file co the la "nvc.png" hoac "nvc"
            ref_id = ref_file.replace('.png', '').replace('.jpg', '').replace('.jpeg', '').replace('.webp', '')

            # Tim file voi nhieu extension
            file_path = None
            filename = None

            # Thu cac extension khac nhau
            extensions = ['.png', '.jpg', '.jpeg', '.webp', '']
            search_dirs = [self.nv_path, self.project_path / "img"]

            for search_dir in search_dirs:
                if file_path:
                    break
                for ext in extensions:
                    test_path = search_dir / f"{ref_id}{ext}"
                    if test_path.exists():
                        file_path = test_path
                        filename = f"{ref_id}{ext}" if ext else ref_id
                        self._log(f"[UPLOAD] Found: {test_path}", "info")
                        break

            if not file_path:
                self._log(f"[UPLOAD] Khong tim thay file: {ref_id} (searched in nv/ and img/)", "warn")
                # List available files in nv/ for debugging
                if self.nv_path.exists():
                    available = list(self.nv_path.glob("*.*"))
                    self._log(f"[UPLOAD] Files in nv/: {[f.name for f in available[:10]]}", "info")
                continue

            # Doc file va convert sang base64
            try:
                with open(file_path, 'rb') as f:
                    image_data = f.read()
                    base64_data = base64.b64encode(image_data).decode('utf-8')
                    images_to_upload.append({
                        'base64': base64_data,
                        'filename': filename
                    })
                    self._log(f"[UPLOAD] Doc file: {filename} ({len(image_data)/1024:.1f} KB)")
            except Exception as e:
                self._log(f"[UPLOAD] Loi doc file {filename}: {e}", "error")
                continue

        if not images_to_upload:
            self._log("[UPLOAD] Khong co anh nao de upload", "warn")
            return True

        # Upload qua JS
        try:
            self._log(f"[UPLOAD] Goi JS VE3.uploadReferences() voi {len(images_to_upload)} files...", "info")
            images_json = json.dumps(images_to_upload)

            # Timeout dai hon cho upload nhieu file
            timeout_ms = 60000 + (len(images_to_upload) * 30000)  # 60s + 30s per file
            self._log(f"[UPLOAD] Timeout: {timeout_ms/1000:.0f}s", "info")

            result = self.driver.execute_async_script(f"""
                const callback = arguments[arguments.length - 1];
                const timeout = setTimeout(() => {{
                    callback({{ success: false, error: 'JS timeout' }});
                }}, {timeout_ms});

                try {{
                    VE3.uploadReferences({images_json}).then(success => {{
                        clearTimeout(timeout);
                        callback({{ success: success, message: 'Upload completed' }});
                    }}).catch(e => {{
                        clearTimeout(timeout);
                        callback({{ success: false, error: e.message || 'Upload exception' }});
                    }});
                }} catch (e) {{
                    clearTimeout(timeout);
                    callback({{ success: false, error: 'JS error: ' + e.message }});
                }}
            """)

            self._log(f"[UPLOAD] JS result: {result}", "info")

            if result and result.get('success'):
                success_count = result.get('successCount', 0)
                total_count = result.get('totalCount', len(images_to_upload))
                self._log(f"[UPLOAD] Da upload {success_count}/{total_count} anh reference", "success")
                return True
            else:
                # Log chi tiet loi tu JS
                errors = result.get('errors', []) if result else []
                if errors:
                    for err in errors:
                        self._log(f"[UPLOAD] - {err.get('file', '?')}: {err.get('error', 'Unknown')}", "error")
                else:
                    error = result.get('error', 'Unknown') if result else 'No response from JS'
                    self._log(f"[UPLOAD] Loi upload: {error}", "error")
                # Tiep tuc du co loi - khong block generation
                return False

        except Exception as e:
            self._log(f"[UPLOAD] Python Exception: {e}", "error")
            import traceback
            self._log(f"[UPLOAD] Traceback: {traceback.format_exc()}", "error")
            return False

    def _find_downloaded_files(self, pattern: str, wait_timeout: int = 30) -> List[Path]:
        """
        Tim file vua download trong Downloads folder.

        Args:
            pattern: Pattern de match (vd: KA1-0001_scene_*)
            wait_timeout: Thoi gian cho file xuat hien

        Returns:
            List cac file tim duoc
        """
        start_time = time.time()
        search_pattern = str(self.downloads_dir / pattern)

        while time.time() - start_time < wait_timeout:
            files = glob.glob(search_pattern)
            # Loai bo file .crdownload (dang tai)
            files = [f for f in files if not f.endswith('.crdownload')]

            if files:
                return [Path(f) for f in files]

            time.sleep(0.5)

        return []

    def _select_best_image(self, files: List[Path], is_character: bool = False) -> Tuple[Path, float]:
        """
        Chon anh tot nhat tu nhieu files.
        Su dung ImageEvaluator de danh gia chat luong anh (sharpness, brightness, contrast, faces).
        Fallback: dung file size neu khong co opencv.

        Args:
            files: List cac file anh
            is_character: Co phai anh nhan vat (nvc/nv*/loc*) - uu tien face detection

        Returns:
            Tuple[Path den file tot nhat, score]
        """
        if len(files) == 1:
            # Van danh gia de biet score
            try:
                from modules.image_evaluator import ImageEvaluator
                evaluator = ImageEvaluator(verbose=False)
                _, score = evaluator.evaluate(files[0], is_character)
                return files[0], score.total_score
            except ImportError:
                return files[0], 100.0  # Assume good if can't evaluate

        # Thu dung ImageEvaluator (tot hon)
        try:
            from modules.image_evaluator import ImageEvaluator
            evaluator = ImageEvaluator(verbose=False)
            best_path, best_score = evaluator.select_best(files, is_character)

            self._log(f"Chon anh tot nhat: {best_path.name} (score={best_score.total_score}, grade={best_score.grade})")

            # Log comparison
            if len(files) > 1:
                scores_str = []
                for f in files:
                    _, score = evaluator.evaluate(f, is_character)
                    scores_str.append(f"{f.name}={score.total_score}")
                self._log(f"  So sanh: {', '.join(scores_str)}")

            return best_path, best_score.total_score

        except ImportError:
            self._log("ImageEvaluator khong co, dung file size", "warn")

        # Fallback: Lay file size cua moi file
        file_sizes = []
        for f in files:
            try:
                size = f.stat().st_size
                file_sizes.append((f, size))
            except:
                file_sizes.append((f, 0))

        # Sort theo size giam dan (lon nhat = tot nhat)
        file_sizes.sort(key=lambda x: x[1], reverse=True)

        best_file = file_sizes[0][0]
        best_size = file_sizes[0][1]

        self._log(f"Chon anh tot nhat: {best_file.name} ({best_size/1024:.1f}KB)")

        # Log comparison
        if len(file_sizes) > 1:
            sizes_str = ", ".join([f"{f.name}={s/1024:.1f}KB" for f, s in file_sizes])
            self._log(f"  So sanh: {sizes_str}")

        return best_file, 70.0  # Assume decent score for fallback

    def _move_downloaded_images(
        self,
        scene_id: str,
        min_score: float = 50.0
    ) -> Tuple[Optional[Path], float, bool]:
        """
        Di chuyen anh vua download tu Downloads vao project/img/ hoac nv/.
        Neu co 2 anh, chon anh tot nhat bang ImageEvaluator.
        Tra ve score de biet co can tao lai khong.

        Args:
            scene_id: ID cua scene (1, 2, ... hoac nvc, nv1, loc1...)
            min_score: Diem toi thieu de pass (0-100)

        Returns:
            Tuple[Path da di chuyen, score, needs_regeneration]
        """
        # Pattern: {project_code}_{scene_id}*.png
        pattern = f"{self.project_code}_{scene_id}*.png"

        files = self._find_downloaded_files(pattern, wait_timeout=120)

        if not files:
            self._log(f"Khong tim thay file: {pattern}", "warn")
            return None, 0.0, True

        # Xac dinh co phai nhan vat/dia diem khong (uu tien face detection)
        scene_id_str = str(scene_id)
        is_character = scene_id_str.startswith('nv') or scene_id_str.startswith('loc')

        # QUAN TRONG: Chon anh tot nhat va danh gia chat luong
        best_file, score = self._select_best_image(files, is_character)

        # Check neu can tao lai
        needs_regeneration = score < min_score
        if needs_regeneration:
            self._log(f"Anh {scene_id} chua dat chuan: {score:.1f} < {min_score}", "warn")

        # Xac dinh thu muc dich: nv/ cho nvc/nv*/loc*, img/ cho scenes
        if is_character:
            dst_dir = self.nv_path
        else:
            dst_dir = self.img_path

        dst_dir.mkdir(parents=True, exist_ok=True)
        dst_file = dst_dir / f"{scene_id}.png"

        try:
            shutil.move(str(best_file), str(dst_file))
            self._log(f"Da di chuyen: {best_file.name} -> {dst_file} (score={score:.1f})", "success")

            # Xoa cac file con lai (khong can nua)
            for f in files:
                if f != best_file and f.exists():
                    try:
                        os.remove(f)
                        self._log(f"  Xoa file du: {f.name}")
                    except:
                        pass

            return dst_file, score, needs_regeneration

        except Exception as e:
            self._log(f"Loi di chuyen file: {e}", "error")
            return None, 0.0, True

    def generate_scene_images(
        self,
        excel_path: Optional[Path] = None,
        start_scene: int = 1,
        end_scene: Optional[int] = None,
        overwrite: bool = False
    ) -> Dict[str, Any]:
        """
        Tao anh cho cac scenes trong Excel.

        Args:
            excel_path: Duong dan file Excel (tu tim neu khong chi dinh)
            start_scene: Scene bat dau (1-indexed)
            end_scene: Scene ket thuc (None = tat ca)
            overwrite: Ghi de anh da co

        Returns:
            Dict voi ket qua
        """
        self._log("=" * 60)
        self._log("BROWSER FLOW GENERATOR - TAO ANH TU EXCEL")
        self._log("=" * 60)

        # Tim file Excel
        if excel_path is None:
            excel_path = self._find_excel_file()

        if excel_path is None or not excel_path.exists():
            return {"success": False, "error": "Khong tim thay file Excel"}

        self._log(f"Excel: {excel_path}")
        self._log(f"Project: {self.project_code}")

        # Load Excel
        workbook = PromptWorkbook(excel_path)
        workbook.load_or_create()

        # Lay cac scene can tao anh
        all_scenes = workbook.get_scenes()
        scenes_to_process = []

        for scene in all_scenes:
            # Filter theo range
            if scene.scene_id < start_scene:
                continue
            if end_scene is not None and scene.scene_id > end_scene:
                break

            # Skip neu khong co prompt
            if not scene.img_prompt:
                continue

            # Skip neu da done va khong overwrite
            if scene.status_img == "done" and not overwrite:
                self.stats["skipped"] += 1
                continue

            scenes_to_process.append(scene)

        if not scenes_to_process:
            self._log("Khong co scene nao can tao anh", "warn")
            return {"success": True, "message": "No scenes to process"}

        self._log(f"Se tao {len(scenes_to_process)} anh")
        self.stats["total"] = len(scenes_to_process)

        # QUAN TRONG: Load cache TRUOC khi start browser
        # De lay _cached_project_url va vao dung project (giu media_name valid)
        cached_media_names = self._load_media_cache()
        has_cached_project = hasattr(self, '_cached_project_url') and self._cached_project_url

        if cached_media_names:
            self._log(f"[CACHE] Loaded {len(cached_media_names)} media references (nv/loc)")
            if has_cached_project:
                self._log(f"[CACHE] Co project URL -> se reuse de giu media_name valid")
        else:
            self._log("Khong co media cache - scenes se khong co reference", "warn")

        # Khoi dong browser - vao dung project neu co cache
        if not self.driver:
            if not self.start_browser(use_cached_project=has_cached_project):
                return {"success": False, "error": "Khong khoi dong duoc browser"}

            # Cho dang nhap
            if not self.wait_for_login(timeout=120):
                self.stop_browser()
                return {"success": False, "error": "Chua dang nhap"}

        # Inject JS
        if not self._inject_js():
            return {"success": False, "error": "Khong inject duoc JS"}

        # Load media_names vao JS sau khi inject
        if cached_media_names:
            self._load_media_names_to_js(cached_media_names)
            self._log(f"Loaded media references vao JS")

        # Chuan bi prompts cho VE3.run()
        # QUAN TRONG: Dung numeric ID (1, 2, 3) de khop voi SmartEngine video composer
        prompts_data = []
        for scene in scenes_to_process:
            # Normalize scene_id: 1.0 -> "1", 2.0 -> "2"
            scene_id = str(int(float(scene.scene_id))) if scene.scene_id else "0"
            prompts_data.append({
                "sceneId": scene_id,
                "prompt": scene.img_prompt
            })

        self._log(f"\nBat dau tao {len(prompts_data)} anh...")

        # DEBUG: List files trong thu muc nv/
        self._log("\n" + "=" * 60)
        self._log("[DEBUG] FILES TRONG THU MUC NV/:")
        self._log(f"  nv_path: {self.nv_path}")
        if self.nv_path.exists():
            nv_files = list(self.nv_path.glob("*.*"))
            self._log(f"  Found {len(nv_files)} files:")
            for f in nv_files:
                self._log(f"    - {f.name} ({f.stat().st_size / 1024:.1f} KB)")
        else:
            self._log(f"  [ERROR] Thu muc nv/ KHONG TON TAI!")
        self._log("=" * 60)

        # DEBUG: Hien thi cac scene de kiem tra reference_files
        self._log("\n" + "=" * 60)
        self._log("[DEBUG] KIEM TRA REFERENCE_FILES TU EXCEL:")
        self._log("=" * 60)
        for idx, s in enumerate(scenes_to_process[:5]):  # Hien thi 5 scene dau
            ref_raw = getattr(s, 'reference_files', None)
            self._log(f"  Scene {s.scene_id}: reference_files = '{ref_raw}'")
        self._log("=" * 60 + "\n")

        # Goi VE3.run() - xu ly tung prompt mot de cap nhat Excel theo thoi gian thuc
        for i, item in enumerate(prompts_data):
            scene = scenes_to_process[i]
            scene_id = item["sceneId"]
            prompt = item["prompt"]

            # Lay reference_files tu scene (JSON string hoac list)
            reference_files = []
            ref_str = getattr(scene, 'reference_files', '') or ''

            self._log(f"\n[DEBUG] Scene {scene_id} raw reference_files: '{ref_str}' (type={type(ref_str).__name__})")

            if ref_str:
                try:
                    # Thu parse JSON truoc
                    if ref_str.startswith('['):
                        parsed = json.loads(ref_str)
                        reference_files = parsed if isinstance(parsed, list) else [parsed]
                        self._log(f"[DEBUG] Parsed JSON: {reference_files}")
                    else:
                        # Khong phai JSON, split by comma
                        reference_files = [f.strip() for f in str(ref_str).split(',') if f.strip()]
                        self._log(f"[DEBUG] Split by comma: {reference_files}")
                except Exception as e:
                    self._log(f"[DEBUG] Parse error: {e}, trying split...")
                    reference_files = [f.strip() for f in str(ref_str).split(',') if f.strip()]

            self._log(f"\n[{i+1}/{len(prompts_data)}] Scene {scene_id}")
            self._log(f"Prompt ({len(prompt)} chars): {prompt[:100]}...")
            self._log(f"[REF] Final reference_files: {reference_files}")

            # VERIFY: Check if prompt has filename annotations (case-insensitive)
            has_annotations = False
            prompt_lower = prompt.lower()
            for ref in reference_files:
                ref_lower = ref.lower()
                ref_name = ref.replace('.png', '').replace('.jpg', '')
                ref_name_lower = ref_name.lower()
                if f"({ref_lower})" in prompt_lower or f"({ref_name_lower}.png)" in prompt_lower:
                    has_annotations = True
                    break
            if "(reference:" in prompt_lower:
                has_annotations = True

            if reference_files:
                if has_annotations:
                    self._log(f"[ANNOTATION] [v] Prompt DA CO annotations", "success")
                else:
                    self._log(f"[ANNOTATION] [WARN] Prompt CHUA CO annotations - them vao cuoi...", "warn")
                    # Them annotation neu chua co
                    refs_str = ", ".join(reference_files)
                    prompt = prompt.rstrip('. ') + f" (reference: {refs_str})."
                    self._log(f"[ANNOTATION] Prompt sau khi them: ...{prompt[-80:]}", "info")

            try:
                # QUAN TRONG: Upload reference images TRUOC KHI tao anh
                if reference_files:
                    self._log(f"[UPLOAD] Dang upload {len(reference_files)} anh reference...")
                    upload_success = self._upload_reference_images(reference_files)
                    if upload_success:
                        self._log(f"[UPLOAD] Upload thanh cong!", "success")
                    else:
                        self._log("[UPLOAD] Upload that bai, tiep tuc khong co reference", "warn")

                # Goi VE3.run() cho 1 prompt (voi reference_files)
                ref_files_json = json.dumps(reference_files)
                result = self.driver.execute_async_script(f"""
                    const callback = arguments[arguments.length - 1];
                    const timeout = setTimeout(() => {{
                        callback({{ success: false, error: 'Timeout 120s' }});
                    }}, 120000);

                    VE3.run([{{
                        sceneId: "{scene_id}",
                        prompt: `{self._escape_js_string(prompt)}`,
                        referenceFiles: {ref_files_json}
                    }}]).then(r => {{
                        clearTimeout(timeout);
                        callback({{ success: true, result: r }});
                    }}).catch(e => {{
                        clearTimeout(timeout);
                        callback({{ success: false, error: e.message }});
                    }});
                """)

                if result and result.get("success"):
                    # Di chuyen file - scene_id la numeric ("1", "2", ...)
                    img_file, score, needs_regen = self._move_downloaded_images(scene_id)

                    if img_file:
                        # Cap nhat Excel - dung numeric ID
                        relative_path = f"img/{scene_id}.png"
                        workbook.update_scene(
                            scene.scene_id,  # scene.scene_id la int
                            img_path=relative_path,
                            status_img="done" if not needs_regen else "low_quality"
                        )
                        workbook.safe_save()

                        if needs_regen:
                            self._log(f"Anh {scene_id} chua dat chuan (score={score:.1f}), can tao lai", "warn")
                            self.stats["low_quality"] = self.stats.get("low_quality", 0) + 1
                        else:
                            self._log(f"Da cap nhat Excel: {scene_id} = done (score={score:.1f})", "success")
                        self.stats["success"] += 1
                    else:
                        workbook.update_scene(scene.scene_id, status_img="error")
                        workbook.safe_save()
                        self.stats["failed"] += 1
                else:
                    error = result.get("error", "Unknown") if result else "No response"
                    self._log(f"Loi: {error}", "error")
                    workbook.update_scene(scene.scene_id, status_img="error")
                    workbook.safe_save()
                    self.stats["failed"] += 1

                # Delay giua cac prompt
                if i < len(prompts_data) - 1:
                    time.sleep(2)

            except Exception as e:
                self._log(f"Exception: {e}", "error")
                workbook.update_scene(scene.scene_id, status_img="error")
                workbook.safe_save()
                self.stats["failed"] += 1

        # Summary
        self._log("\n" + "=" * 60)
        self._log("HOAN THANH")
        self._log("=" * 60)
        self._log(f"Tong: {self.stats['total']}")
        self._log(f"Thanh cong: {self.stats['success']}")
        self._log(f"That bai: {self.stats['failed']}")
        self._log(f"Bo qua: {self.stats['skipped']}")

        return {
            "success": True,
            "stats": self.stats.copy()
        }

    def _process_single_prompt(self, prompt_data: Dict, index: int, total: int) -> Tuple[bool, Optional[Path], float, str]:
        """
        Xu ly mot prompt don le.

        Returns:
            Tuple[success, image_path, score, prompt_json]
        """
        pid = str(prompt_data.get('id', index + 1))
        prompt = prompt_data.get('prompt', '')

        # Lay reference_files tu prompt_data (JSON string hoac list)
        reference_files = []
        ref_str = prompt_data.get('reference_files', '')
        if ref_str:
            try:
                parsed = json.loads(ref_str) if isinstance(ref_str, str) else ref_str
                reference_files = parsed if isinstance(parsed, list) else [parsed]
            except:
                reference_files = [f.strip() for f in str(ref_str).split(',') if f.strip()]

        self._log(f"\n[{index+1}/{total}] ID: {pid}")
        self._log(f"Prompt ({len(prompt)} chars): {prompt[:100]}...")
        if reference_files:
            self._log(f"[REF] reference_files: {reference_files}")
        else:
            self._log(f"[REF] [WARN] NO REFERENCES - Excel cot 'reference_files' trong!")
            self._log(f"[REF] raw value from Excel: '{ref_str}'")

        if not prompt:
            self._log("Skip - prompt rong", "warn")
            return False, None, 0.0

        try:
            # UPLOAD REFERENCE IMAGES TRUOC KHI TAO ANH
            if reference_files:
                self._log(f"[UPLOAD] Dang upload {len(reference_files)} anh reference...")
                upload_success = self._upload_reference_images(reference_files)
                if not upload_success:
                    self._log("[UPLOAD] Upload that bai, tiep tuc khong co reference", "warn")

            # Goi VE3.run() cho 1 prompt (voi reference_files)
            ref_files_json = json.dumps(reference_files)
            result = self.driver.execute_async_script(f"""
                const callback = arguments[arguments.length - 1];
                const timeout = setTimeout(() => {{
                    callback({{ success: false, error: 'Timeout 120s' }});
                }}, 120000);

                VE3.run([{{
                    sceneId: "{pid}",
                    prompt: `{self._escape_js_string(prompt)}`,
                    referenceFiles: {ref_files_json}
                }}]).then(r => {{
                    clearTimeout(timeout);
                    callback({{ success: true, result: r }});
                }}).catch(e => {{
                    clearTimeout(timeout);
                    callback({{ success: false, error: e.message }});
                }});
            """)

            if result and result.get("success"):
                # Lay prompt_json va images tu result
                js_result = result.get("result", {})
                prompt_json = js_result.get("prompt_json", "") if isinstance(js_result, dict) else ""
                js_images = js_result.get("images", []) if isinstance(js_result, dict) else []

                self._log(f"[DEBUG] prompt_json: {prompt_json[:100] if prompt_json else '(empty)'}...")
                self._log(f"[DEBUG] JS returned {len(js_images)} images with mediaNames")

                # Di chuyen file tu Downloads (timeout 2 phut)
                img_file, score, needs_regen = self._move_downloaded_images(pid)

                if img_file:
                    # TIM MEDIA_NAME VA SEED DUNG CHO ANH DA CHON
                    selected_media_name = ""
                    selected_seed = None
                    if js_images:
                        # Neu chi co 1 anh, lay mediaName va seed cua no
                        if len(js_images) == 1:
                            selected_media_name = js_images[0].get("mediaName", "")
                            selected_seed = js_images[0].get("seed")
                            self._log(f"[MEDIA] 1 image -> mediaName: {selected_media_name[:50] if selected_media_name else 'NONE'}, seed={selected_seed}")
                        else:
                            # Neu co nhieu anh, lay anh dau tien (thuong la best)
                            # TODO: Match filename de lay dung anh duoc chon
                            selected_media_name = js_images[0].get("mediaName", "")
                            selected_seed = js_images[0].get("seed")
                            self._log(f"[MEDIA] {len(js_images)} images, selected first -> mediaName: {selected_media_name[:50] if selected_media_name else 'NONE'}, seed={selected_seed}")

                    # Set mediaName + seed vao JS STATE de reference sau
                    if selected_media_name and self.driver:
                        try:
                            # Pass ca mediaName va seed
                            seed_arg = f", {selected_seed}" if selected_seed else ", null"
                            self.driver.execute_script(
                                f"VE3.setMediaName('{pid}', '{selected_media_name}'{seed_arg});"
                            )
                            self._log(f"[MEDIA] Saved mediaInfo for {pid}: mediaName + seed={selected_seed}")
                        except Exception as e:
                            self._log(f"[MEDIA] Warning: Could not set mediaName: {e}", "warn")

                    if needs_regen:
                        self._log(f"OK - Da tao anh nhung chua dat chuan (score={score:.1f})", "warn")
                    else:
                        self._log(f"OK - Da tao va luu anh (score={score:.1f})", "success")
                    return True, img_file, score, prompt_json
                else:
                    self._log(f"Khong tim thay file download sau 2 phut", "warn")
                    return False, None, 0.0, prompt_json
            else:
                error = result.get("error", "Unknown") if result else "No response"
                self._log(f"Loi: {error}", "error")
                return False, None, 0.0, ""

        except Exception as e:
            self._log(f"Exception: {e}", "error")
            return False, None, 0.0, ""

    def _restart_browser_and_setup(self) -> bool:
        """
        Khoi dong lai browser va setup (dung khi setup that bai).
        Neu da co project URL, navigate ve do thay vi tao project moi.

        Returns:
            True neu thanh cong
        """
        self._log("Khoi dong lai browser...", "warn")

        # Luu project URL truoc khi dong browser
        saved_project_url = self._project_url

        # Dong browser cu
        self.stop_browser()
        self._js_injected = False

        # Doi 3 giay
        time.sleep(3)

        # Khoi dong lai
        if not self.start_browser():
            return False

        if not self.wait_for_login(timeout=120):
            self.stop_browser()
            return False

        # Neu co project URL cu, navigate ve do thay vi setup moi
        if saved_project_url and '/project/' in saved_project_url:
            self._log(f"Navigate ve project cu: {saved_project_url}", "info")
            self.driver.get(saved_project_url)
            time.sleep(5)  # Cho page load

            # Inject JS nhung KHONG goi VE3.setup()
            if not self._inject_js_without_setup():
                return False

            # Restore project URL
            self._project_url = saved_project_url
        else:
            # Inject JS va setup moi
            if not self._inject_js():
                return False

        return True

    def _inject_js_without_setup(self) -> bool:
        """Inject JS ma khong goi VE3.setup() - dung khi navigate ve project cu."""
        if self._js_injected:
            return True

        try:
            self._log("Inject JavaScript (khong setup)...")
            js_code = self._get_js_script()
            self.driver.execute_script(js_code)

            # Init VE3 voi project name
            self.driver.execute_script(f'VE3.init("{self.project_code}")')

            # Danh dau da setup (vi da co project)
            self.driver.execute_script("VE3.markSetupDone();")

            # Load media_names tu cache
            cached_media_names = self._load_media_cache()
            if cached_media_names:
                self._load_media_names_to_js(cached_media_names)

            self._js_injected = True
            self._log("Da san sang tao anh (project cu)!", "success")
            return True

        except Exception as e:
            self._log(f"Loi inject JS: {e}", "error")
            return False

    def generate_from_prompts(
        self,
        prompts: List[Dict],
        excel_path: Optional[Path] = None,
        max_setup_retries: int = 3
    ) -> Dict[str, Any]:
        """
        Tao anh tu danh sach prompts da load san (tu smart_engine._load_prompts).
        Method nay nhan prompts truc tiep thay vi doc lai tu Excel.

        Features:
        - Neu prompt dau tien that bai (co the do setup loi), tu dong restart browser va thu lai (toi da 3 lan)
        - Theo doi cac prompt that bai va retry o cuoi
        - Timeout 2 phut cho moi anh

        Args:
            prompts: List cac dict co dang {'id': '1', 'prompt': '...', 'output_path': '...'}
            excel_path: Duong dan Excel (de cap nhat status)
            max_setup_retries: So lan retry toi da neu setup that bai (default: 3)

        Returns:
            Dict voi ket qua
        """
        self._log("=" * 60)
        self._log("BROWSER FLOW GENERATOR - TAO ANH TU PROMPTS")
        self._log("=" * 60)

        if not prompts:
            return {"success": False, "error": "Khong co prompts"}

        self._log(f"Tong: {len(prompts)} prompts")
        self._log(f"Project: {self.project_code}")

        # Reset stats
        self.stats = {"total": len(prompts), "success": 0, "failed": 0, "skipped": 0, "low_quality": 0}

        # Load Excel workbook de cap nhat prompt_json
        workbook = None
        if excel_path and Path(excel_path).exists():
            try:
                workbook = PromptWorkbook(excel_path)
                workbook.load_or_create()
                self._log(f"[Excel] Loaded: {excel_path}")
            except Exception as e:
                self._log(f"[Excel] Warning: Khong load duoc Excel: {e}", "warn")

        # QUAN TRONG: Load cache TRUOC khi start browser
        # De lay _cached_project_url va vao dung project (giu media_name valid)
        cached_media_names = self._load_media_cache()
        has_cached_project = hasattr(self, '_cached_project_url') and self._cached_project_url

        if cached_media_names:
            self._log(f"[CACHE] Loaded {len(cached_media_names)} media_names")
            if has_cached_project:
                self._log(f"[CACHE] Co project URL -> se reuse de giu media_name valid")
        else:
            self._log("[CACHE] [WARN] EMPTY - Characters (nv/loc) chua duoc tao!", "warn")

        # Khoi dong browser - vao dung project neu co cache
        if not self.driver:
            if not self.start_browser(use_cached_project=has_cached_project):
                return {"success": False, "error": "Khong khoi dong duoc browser"}

            if not self.wait_for_login(timeout=120):
                self.stop_browser()
                return {"success": False, "error": "Chua dang nhap"}

        # Inject JS
        if not self._inject_js():
            return {"success": False, "error": "Khong inject duoc JS"}

        # Load media_names vao JS sau khi inject
        if cached_media_names:
            for key, val in cached_media_names.items():
                if isinstance(val, dict):
                    mn = val.get('mediaName', '')
                    seed = val.get('seed')
                    self._log(f"  {key} -> mediaName:{mn[:40] if mn else 'None'}..., seed:{seed}")
                else:
                    self._log(f"  {key} -> {val[:50] if val else 'None'}...")
            self._load_media_names_to_js(cached_media_names)

        self._log(f"\nBat dau tao {len(prompts)} anh...")

        # DEBUG: Hien thi prompt dau tien
        if prompts:
            p = prompts[0]
            self._log(f"[DEBUG] Prompt dau tien: id={p.get('id')}")
            self._log(f"[DEBUG] prompt = '{str(p.get('prompt', ''))[:100]}'")

        # Track failed prompts de retry sau
        failed_prompts = []  # List of (prompt_data, original_index)

        # === XU LY PROMPT DAU TIEN VOI SETUP RETRY ===
        first_prompt_success = False
        setup_attempts = 0

        while not first_prompt_success and setup_attempts < max_setup_retries:
            setup_attempts += 1

            if setup_attempts > 1:
                self._log(f"\n=== SETUP RETRY {setup_attempts}/{max_setup_retries} ===", "warn")
                if not self._restart_browser_and_setup():
                    self._log(f"Khong the khoi dong lai browser", "error")
                    continue

            # Thu prompt dau tien
            success, img_file, score, prompt_json = self._process_single_prompt(prompts[0], 0, len(prompts))

            if success:
                first_prompt_success = True
                self.stats["success"] += 1
                if score < 50.0:
                    self.stats["low_quality"] += 1
                # Luu prompt_json vao prompt_data
                if prompt_json:
                    prompts[0]['prompt_json'] = prompt_json
                # Cap nhat Excel (prompt_json, img_path)
                if workbook:
                    try:
                        pid = prompts[0].get('id', '1')
                        # Chi cap nhat cho scenes (so), khong phai nv/loc
                        if pid.isdigit():
                            scene_id = int(pid)
                            relative_path = f"img/{pid}.png" if img_file else ""
                            workbook.update_scene(
                                scene_id,
                                img_path=relative_path,
                                status_img="done" if score >= 50.0 else "low_quality",
                                prompt_json=prompt_json
                            )
                            workbook.safe_save()
                            self._log(f"[Excel] Updated scene {scene_id}: prompt_json saved")
                    except Exception as e:
                        self._log(f"[Excel] Warning: {e}", "warn")
            else:
                self._log(f"Prompt dau tien that bai (lan {setup_attempts})", "error")

        if not first_prompt_success:
            # Da thu het so lan retry, ghi nhan that bai
            self._log(f"Prompt dau tien that bai sau {max_setup_retries} lan thu", "error")
            failed_prompts.append((prompts[0], 0))
            self.stats["failed"] += 1

        # === XU LY CAC PROMPT CON LAI ===
        for i, prompt_data in enumerate(prompts[1:], start=1):
            pid = str(prompt_data.get('id', i + 1))
            prompt = prompt_data.get('prompt', '')

            if not prompt:
                self._log(f"\n[{i+1}/{len(prompts)}] ID: {pid} - Skip (prompt rong)", "warn")
                self.stats["skipped"] += 1
                continue

            success, img_file, score, prompt_json = self._process_single_prompt(prompt_data, i, len(prompts))

            if success:
                self.stats["success"] += 1
                if score < 50.0:
                    self.stats["low_quality"] += 1
                # Luu prompt_json vao prompt_data
                if prompt_json:
                    prompt_data['prompt_json'] = prompt_json
                # Cap nhat Excel (prompt_json, img_path)
                if workbook:
                    try:
                        # Chi cap nhat cho scenes (so), khong phai nv/loc
                        if pid.isdigit():
                            scene_id = int(pid)
                            relative_path = f"img/{pid}.png" if img_file else ""
                            workbook.update_scene(
                                scene_id,
                                img_path=relative_path,
                                status_img="done" if score >= 50.0 else "low_quality",
                                prompt_json=prompt_json
                            )
                            workbook.safe_save()
                            self._log(f"[Excel] Updated scene {scene_id}: prompt_json saved")
                    except Exception as e:
                        self._log(f"[Excel] Warning: {e}", "warn")
            else:
                failed_prompts.append((prompt_data, i))
                self.stats["failed"] += 1

            # Delay giua cac prompt
            if i < len(prompts) - 1:
                time.sleep(2)

        # === RETRY FAILED PROMPTS (2 VÒNG) ===
        # v1.0.209: Tăng retry lên 2 vòng để tăng tỉ lệ thành công reference images
        max_retry_rounds = 2
        for retry_round in range(max_retry_rounds):
            if not failed_prompts:
                break

            self._log("\n" + "=" * 60)
            self._log(f"RETRY VÒNG {retry_round + 1}/{max_retry_rounds}: {len(failed_prompts)} ảnh thất bại")
            self._log("=" * 60)

            still_failed = []
            retry_success = 0

            for prompt_data, original_index in failed_prompts:
                pid = str(prompt_data.get('id', original_index + 1))
                self._log(f"\nRetry ID: {pid} (vòng {retry_round + 1})")

                success, img_file, score, prompt_json = self._process_single_prompt(
                    prompt_data, original_index, len(prompts)
                )

                if success:
                    retry_success += 1
                    self.stats["success"] += 1
                    self.stats["failed"] -= 1  # Giam failed vi da thanh cong
                    if score < 50.0:
                        self.stats["low_quality"] += 1
                    # Luu prompt_json vao prompt_data
                    if prompt_json:
                        prompt_data['prompt_json'] = prompt_json
                    # Cap nhat Excel (prompt_json, img_path)
                    if workbook:
                        try:
                            # Chi cap nhat cho scenes (so), khong phai nv/loc
                            if pid.isdigit():
                                scene_id = int(pid)
                                relative_path = f"img/{pid}.png" if img_file else ""
                                workbook.update_scene(
                                    scene_id,
                                    img_path=relative_path,
                                    status_img="done" if score >= 50.0 else "low_quality",
                                    prompt_json=prompt_json
                                )
                                workbook.safe_save()
                                self._log(f"[Excel] Updated scene {scene_id}: prompt_json saved (retry)")
                        except Exception as e:
                            self._log(f"[Excel] Warning: {e}", "warn")
                else:
                    # Vẫn thất bại - thêm vào list cho vòng retry tiếp theo
                    still_failed.append((prompt_data, original_index))

                # Delay
                time.sleep(2)

            self._log(f"\nRetry vòng {retry_round + 1}: {retry_success}/{len(failed_prompts)} thành công")

            # Cập nhật failed_prompts cho vòng retry tiếp theo
            failed_prompts = still_failed

        # Luu media_names tu JS vao cache (cho cac lan chay sau)
        js_media_names = self._get_media_names_from_js()
        if js_media_names:
            # Merge voi cached (uu tien moi)
            all_media_names = {**cached_media_names, **js_media_names}
            self._save_media_cache(all_media_names)

        # Summary
        self._log("\n" + "=" * 60)
        self._log("HOAN THANH")
        self._log("=" * 60)
        self._log(f"Tong: {self.stats['total']}")
        self._log(f"Thanh cong: {self.stats['success']}")
        self._log(f"That bai: {self.stats['failed']}")
        self._log(f"Bo qua: {self.stats['skipped']}")
        if self.stats.get('low_quality', 0) > 0:
            self._log(f"Chat luong thap: {self.stats['low_quality']}")
        if js_media_names:
            self._log(f"Media names saved: {len(js_media_names)}")

        return {
            "success": True,
            "stats": self.stats.copy()
        }

    def generate_character_images(
        self,
        excel_path: Optional[Path] = None,
        overwrite: bool = False
    ) -> Dict[str, Any]:
        """
        Tao anh cho cac nhan vat trong Excel.

        Args:
            excel_path: Duong dan file Excel
            overwrite: Ghi de anh da co

        Returns:
            Dict voi ket qua
        """
        self._log("=" * 60)
        self._log("TAO ANH NHAN VAT")
        self._log("=" * 60)

        # Tim file Excel
        if excel_path is None:
            excel_path = self._find_excel_file()

        if excel_path is None or not excel_path.exists():
            return {"success": False, "error": "Khong tim thay file Excel"}

        # Load Excel
        workbook = PromptWorkbook(excel_path)
        workbook.load_or_create()

        # Lay cac nhan vat can tao anh
        characters = workbook.get_characters()
        chars_to_process = []

        for char in characters:
            if not char.english_prompt:
                continue

            # Skip children (status="skip" or english_prompt="DO_NOT_GENERATE" or is_child=True)
            if char.status == "skip" or char.english_prompt == "DO_NOT_GENERATE" or getattr(char, 'is_child', False):
                self._log(f"  [SKIP]  {char.id}: Child character, skipping (will use inline description)")
                continue

            if char.status == "done" and not overwrite:
                continue

            chars_to_process.append(char)

        if not chars_to_process:
            self._log("Khong co nhan vat nao can tao anh", "warn")
            return {"success": True, "message": "No characters to process"}

        self._log(f"Se tao {len(chars_to_process)} anh nhan vat")

        # Khoi dong browser neu chua
        if not self.driver:
            if not self.start_browser():
                return {"success": False, "error": "Khong khoi dong duoc browser"}

            if not self.wait_for_login(timeout=120):
                self.stop_browser()
                return {"success": False, "error": "Chua dang nhap"}

        if not self._inject_js():
            return {"success": False, "error": "Khong inject duoc JS"}

        # Load media cache de co the reference characters da tao truoc do
        cached_media_names = self._load_media_cache()
        if cached_media_names:
            self._load_media_names_to_js(cached_media_names)

        success_count = 0
        failed_count = 0

        for i, char in enumerate(chars_to_process):
            char_id = char.id or f"char_{i+1}"
            prompt = char.english_prompt

            self._log(f"\n[{i+1}/{len(chars_to_process)}] Nhan vat: {char_id}")

            try:
                result = self.driver.execute_async_script(f"""
                    const callback = arguments[arguments.length - 1];
                    VE3.run([{{
                        sceneId: "{char_id}",
                        prompt: `{self._escape_js_string(prompt)}`
                    }}]).then(r => {{
                        callback({{ success: true, result: r }});
                    }}).catch(e => {{
                        callback({{ success: false, error: e.message }});
                    }});
                """)

                if result and result.get("success"):
                    # Di chuyen file vao nv/
                    pattern = f"{self.project_code}_{char_id}*.png"
                    files = self._find_downloaded_files(pattern, wait_timeout=120)

                    if files:
                        dst_file = self.nv_path / f"{char_id}.png"
                        shutil.move(str(files[0]), str(dst_file))

                        workbook.update_character(char_id, status="done", image_file=f"{char_id}.png")
                        workbook.safe_save()

                        self._log(f"Da luu: {dst_file}", "success")
                        success_count += 1
                    else:
                        workbook.update_character(char_id, status="error")
                        workbook.safe_save()
                        failed_count += 1
                else:
                    failed_count += 1

                if i < len(chars_to_process) - 1:
                    time.sleep(2)

            except Exception as e:
                self._log(f"Loi: {e}", "error")
                failed_count += 1

        self._log(f"\nNhan vat: {success_count} thanh cong, {failed_count} that bai")

        # IMPORTANT: Luu mediaNames vao cache sau khi tao xong characters
        # De khi tao scenes co the reference den characters
        try:
            media_names = self._get_media_names_from_js()
            if media_names:
                self._save_media_cache(media_names)
                self._log(f"Da luu {len(media_names)} media_names cho reference", "success")
        except Exception as e:
            self._log(f"Loi luu media cache: {e}", "warn")

        return {
            "success": True,
            "characters_success": success_count,
            "characters_failed": failed_count
        }

    def _get_generation_mode(self) -> str:
        """
        Lay generation mode tu config: 'chrome' hoac 'api'.
        Mac dinh: 'api' (user preference).
        """
        return self.config.get('generation_mode', 'api')

    def _auto_extract_token(self, force_refresh: bool = False) -> Optional[str]:
        """
        Tu dong lay bearer token tu Chrome bang profile hien tai.
        Su dung ChromeAutoToken de mo Chrome, navigate den Flow va capture token.

        Args:
            force_refresh: True = bo qua cache, luon lay token moi (dung khi API tra 401)

        QUAN TRONG:
        - KHONG check thoi gian token
        - Chi refresh khi force_refresh=True (API tra 401)
        - Neu co cached project_url -> mo truc tiep project do -> skip 'Tao du an moi'

        Returns:
            Bearer token (ya29.xxx) hoac None neu that bai
        """
        import time

        # =====================================================================
        # LOAD CACHE TRUOC - de co token va project_url
        # =====================================================================
        if not hasattr(self, '_cached_project_url') or not self._cached_project_url:
            self._log("Loading media cache de lay token va project URL...")
            self._load_media_cache()

        # =====================================================================
        # REUSE CACHED TOKEN (tru khi force_refresh)
        # Chi refresh khi API tra loi 401 -> caller goi force_refresh=True
        # =====================================================================
        if not force_refresh:
            cached_token = self.config.get('flow_bearer_token', '')

            if cached_token:
                # Luon dung token da co - khong can biet tuoi
                # Neu het han, API se tra 401 -> caller goi force_refresh=True
                cached_token_time = getattr(self, '_cached_token_time', 0)
                if cached_token_time:
                    age_minutes = (time.time() - cached_token_time) / 60
                    self._log(f"=== REUSE CACHED TOKEN (tuoi: {age_minutes:.1f} phut) ===")
                else:
                    self._log(f"=== REUSE CACHED TOKEN ===")
                self._log(f"Token: {cached_token[:20]}...{cached_token[-10:]}")
                return cached_token
        else:
            self._log("=== FORCE REFRESH TOKEN (API tra 401) ===")
            # Clear cached token
            self.config['flow_bearer_token'] = ''

        self._log("=== TU DONG LAY BEARER TOKEN (mo Chrome) ===")

        # QUAN TRONG: Lay token LUON phai chay Chrome HIEN THI (khong an)
        # Vi Google Flow detect headless mode va block!
        # Headless chi dung cho viec TAO ANH, khong dung cho LAY TOKEN.
        use_headless = False  # LUON False khi lay token
        self._log("[WARN] Lay token: Chrome se HIEN THI (Google block headless)")

        # Chon extractor phu hop:
        TokenExtractor = None
        extractor_name = None
        if use_headless:
            try:
                from modules.chrome_token_extractor import ChromeTokenExtractor
                TokenExtractor = ChromeTokenExtractor
                extractor_name = "ChromeTokenExtractor"
                self._log("Su dung ChromeTokenExtractor (Selenium - headless)")
            except ImportError as e:
                self._log(f"ChromeTokenExtractor khong kha dung: {e}", "warn")
                self._log("Fallback sang PyAutoGUI...", "warn")
            except Exception as e:
                self._log(f"Loi import ChromeTokenExtractor: {e}", "error")

        if TokenExtractor is None:
            try:
                from modules.auto_token import ChromeAutoToken
                TokenExtractor = ChromeAutoToken
                extractor_name = "ChromeAutoToken"
                self._log("Su dung ChromeAutoToken (PyAutoGUI - can cua so)")
            except ImportError as e:
                self._log(f"Khong import duoc token extractor: {e}", "error")
                return None
            except Exception as e:
                self._log(f"Loi import ChromeAutoToken: {e}", "error")
                return None

        # Lay chrome_path tu config
        # UU TIEN: chrome_portable (VM dung ChromePortable, khong co system Chrome)
        chrome_path = self.config.get('chrome_portable', '')
        if chrome_path:
            # Resolve relative path (vd: ./GoogleChromePortable/GoogleChromePortable.exe)
            chrome_p = Path(chrome_path)
            if not chrome_p.is_absolute():
                root_dir_cp = Path(__file__).parent.parent
                chrome_p = root_dir_cp / chrome_path
            if chrome_p.exists():
                chrome_path = str(chrome_p)
                self._log(f"Su dung ChromePortable: {chrome_path}")
            else:
                self._log(f"[WARN] ChromePortable khong ton tai: {chrome_p}", "warn")
                chrome_path = ''  # Fallback sang system Chrome

        if not chrome_path:
            chrome_path = self.config.get('chrome_path', '')
        if not chrome_path:
            # Default paths
            import platform
            if platform.system() == 'Windows':
                chrome_path = r"C:\Program Files\Google\Chrome\Application\chrome.exe"
            else:
                chrome_path = "/usr/bin/google-chrome"

        # Lay profile path
        # Uu tien: 0. ChromePortable Data/profile (neu dung ChromePortable)
        #          1. chrome_profiles/ directory (GUI tao)
        #          2. chrome_profiles tu accounts.json
        #          3. chrome_profile tu settings.yaml (fallback)
        #          4. browser_profiles_dir/profile_name (fallback cuoi)
        chrome_profile = ''
        root_dir = Path(__file__).parent.parent

        # 0. UU TIEN CAO NHAT: ChromePortable Data/profile
        chrome_portable_cfg = self.config.get('chrome_portable', '')
        if chrome_portable_cfg:
            cp_path = Path(chrome_portable_cfg)
            if not cp_path.is_absolute():
                cp_path = root_dir / chrome_portable_cfg
            # ChromePortable/Data/profile la user data dir
            cp_data_profile = cp_path.parent / "Data" / "profile"
            if cp_data_profile.exists():
                chrome_profile = str(cp_data_profile)
                self._log(f"Got chrome_profile from ChromePortable: {chrome_profile}")

        # 1. chrome_profiles/ directory (GUI tao) - chi dung neu chua co ChromePortable profile
        if not chrome_profile:
            profiles_dir = root_dir / "chrome_profiles"
            if profiles_dir.exists():
                for profile_path in sorted(profiles_dir.iterdir()):
                    if profile_path.is_dir() and not profile_path.name.startswith('.'):
                        chrome_profile = str(profile_path)
                        self._log(f"Got chrome_profile from chrome_profiles/ dir: {chrome_profile}")
                        break

        # 2. Fallback: accounts.json
        if not chrome_profile:
            try:
                accounts_file = root_dir / "config" / "accounts.json"
                if accounts_file.exists():
                    import json
                    with open(accounts_file, 'r', encoding='utf-8') as f:
                        accounts = json.load(f)
                    profiles = accounts.get('chrome_profiles', [])
                    for p in profiles:
                        path = p if isinstance(p, str) else p.get('path', '')
                        if path and not path.startswith('THAY_BANG') and Path(path).exists():
                            chrome_profile = path
                            self._log(f"Got chrome_profile from accounts.json: {chrome_profile}")
                            break
            except Exception as e:
                self._log(f"[DEBUG] Cannot read accounts.json: {e}")

        # 3. Fallback: settings.yaml
        if not chrome_profile:
            chrome_profile = self.config.get('chrome_profile', '')

        self._log(f"[DEBUG] chrome_profile: '{chrome_profile}'")

        if chrome_profile:
            chrome_profile_path = Path(chrome_profile)
            self._log(f"[DEBUG] chrome_profile exists: {chrome_profile_path.exists()}")

            if chrome_profile_path.exists():
                # Dung profile tu settings.yaml
                profile_path = str(chrome_profile_path)
                self._log(f"Su dung Chrome profile tu settings: {profile_path}")
            else:
                # Path khong ton tai - thu resolve relative path
                base_dir = Path(__file__).parent.parent
                abs_chrome_profile = base_dir / chrome_profile
                self._log(f"[DEBUG] Trying absolute: {abs_chrome_profile}")

                if abs_chrome_profile.exists():
                    profile_path = str(abs_chrome_profile)
                    self._log(f"Su dung Chrome profile (resolved): {profile_path}")
                else:
                    # Van khong ton tai - dung profile_dir da setup
                    profile_path = str(self.profile_dir)
                    self._log(f"[WARN] chrome_profile path khong ton tai, dung: {profile_path}")
        else:
            # Khong co chrome_profile - dung profile_dir da setup
            profile_path = str(self.profile_dir)
            self._log(f"Su dung tool profile: {profile_path}")

        self._log(f"Chrome: {chrome_path}")
        self._log(f"Profile: {profile_path}")
        self._log(f"Headless: {'ON' if use_headless else 'OFF'}")

        try:
            # Callback de log
            def log_callback(msg, level="info"):
                self._log(f"[TokenExtract] {msg}", level)

            # QUAN TRONG: Reuse project URL/ID da co de:
            # 1. Share media_ids giua nv va img
            # 2. Skip buoc "Tao du an moi" -> lay token nhanh hon
            existing_project_url = getattr(self, '_cached_project_url', None)
            existing_project_id = self.config.get('flow_project_id', '')

            if existing_project_url:
                self._log(f"  -> Reuse project URL: {existing_project_url[:50]}...")
            elif existing_project_id:
                self._log(f"  -> Reuse project_id: {existing_project_id[:20]}...")
            else:
                self._log(f"  -> Chua co project -> se tao moi")

            # Tao extractor va goi extract_token
            # ChromeTokenExtractor (Selenium) va ChromeAutoToken (PyAutoGUI) co interface khac nhau
            self._log(f"Creating extractor: {extractor_name}...")

            if extractor_name == "ChromeTokenExtractor":
                # Selenium-based: headless OK, nhung khong co project_id/url param
                self._log("Khoi tao Selenium extractor...")
                extractor = TokenExtractor(
                    chrome_path=chrome_path,
                    profile_path=profile_path,
                    headless=use_headless,
                    timeout=90
                )
                self._log("Goi extract_token (Selenium)...")
                token, proj_id, error = extractor.extract_token(callback=log_callback)
            else:
                # PyAutoGUI-based: can cua so, nhung co project reuse
                self._log("Khoi tao PyAutoGUI extractor...")
                extractor = TokenExtractor(
                    chrome_path=chrome_path,
                    profile_path=profile_path,
                    headless=use_headless
                )
                self._log("Goi extract_token (PyAutoGUI)...")
                token, proj_id, error = extractor.extract_token(
                    project_id=existing_project_id,
                    project_url=existing_project_url,
                    callback=log_callback
                )

            if token:
                self._log(f"OK - Da lay duoc token: {token[:20]}...{token[-10:]}", "success")
                # Luu project_id va token vao config
                if proj_id:
                    self.config['flow_project_id'] = proj_id
                    self._log(f"  -> Project ID: {proj_id[:20]}...")

                # Luu token vao config va cache
                import time
                self.config['flow_bearer_token'] = token
                self._cached_bearer_token = token
                self._cached_token_time = time.time()

                # Save vao cache file de reuse lan sau
                try:
                    cached_media_names = self._load_media_cache() or {}
                    self._save_media_cache(cached_media_names)
                    self._log("  -> Token da luu vao cache")
                except Exception as e:
                    self._log(f"  -> Luu cache that bai: {e}")

                # v1.0.522: Luu token vao Excel de server mode doc duoc
                try:
                    excel_path = self._find_excel_file()
                    if excel_path and excel_path.exists():
                        from modules.excel_manager import PromptWorkbook
                        _wb = PromptWorkbook(excel_path)
                        _wb.load_or_create()
                        _wb.set_config_value('flow_bearer_token', token)
                        if proj_id:
                            _wb.set_config_value('flow_project_id', proj_id)
                        _wb.safe_save()
                        self._log("  -> Token da luu vao Excel config (cho server mode)")
                except Exception as e:
                    self._log(f"  -> Luu token vao Excel fail: {e}", "warn")

                return token
            else:
                self._log(f"FAIL - Khong lay duoc token: {error}", "error")
                return None

        except Exception as e:
            self._log(f"Exception khi lay token: {e}", "error")
            import traceback
            traceback.print_exc()
            return None

    def _capture_headers_via_chrome(self) -> Optional[Dict[str, str]]:
        """
        Capture headers (x-browser-validation, authorization, etc) tu Chrome bang Selenium + CDP.
        TAO 1 ANH TEST de trigger API request va capture headers.

        Returns:
            Dict chua headers hoac None neu that bai
        """
        self._log("=== CAPTURE HEADERS TU CHROME (SELENIUM) ===")

        try:
            from modules.chrome_headers_extractor import ChromeHeadersExtractor
        except ImportError as e:
            self._log(f"Khong import duoc ChromeHeadersExtractor: {e}", "error")
            return None

        # Lay profile path - UU TIEN accounts.json (GUI) truoc settings.yaml
        chrome_profile = ''

        # 1. UU TIEN accounts.json (user them profile qua GUI)
        try:
            accounts_file = Path(__file__).parent.parent / "config" / "accounts.json"
            if accounts_file.exists():
                import json
                with open(accounts_file, 'r', encoding='utf-8') as f:
                    accounts = json.load(f)
                profiles = accounts.get('chrome_profiles', [])
                for p in profiles:
                    path = p if isinstance(p, str) else p.get('path', '')
                    if path and not path.startswith('THAY_BANG') and Path(path).exists():
                        chrome_profile = path
                        self._log(f"Got chrome_profile from accounts.json (GUI): {chrome_profile}")
                        break
        except:
            pass

        # 2. Fallback: settings.yaml
        if not chrome_profile:
            chrome_profile = self.config.get('chrome_profile', '')

        if chrome_profile:
            chrome_profile_path = Path(chrome_profile)
            if chrome_profile_path.exists():
                profile_path = str(chrome_profile_path)
                self._log(f"Su dung Chrome profile: {profile_path}")
            else:
                base_dir = Path(__file__).parent.parent
                abs_chrome_profile = base_dir / chrome_profile
                if abs_chrome_profile.exists():
                    profile_path = str(abs_chrome_profile)
                    self._log(f"Su dung Chrome profile (resolved): {profile_path}")
                else:
                    profile_path = str(self.profile_dir)
                    self._log(f"[WARN] chrome_profile khong ton tai, dung: {profile_path}")
        else:
            profile_path = str(self.profile_dir)
            self._log(f"Su dung tool profile: {profile_path}")

        self._log(f"Profile: {profile_path}")

        try:
            extractor = ChromeHeadersExtractor(
                chrome_profile_path=profile_path,
                headless=False,
                verbose=True
            )

            # Start browser
            if not extractor.start_browser():
                self._log("Khong khoi dong duoc Chrome", "error")
                return None

            # Navigate to Flow
            if not extractor.navigate_to_flow():
                self._log("Khong navigate duoc den Flow", "error")
                extractor.stop_browser()
                return None

            # Wait for page load (12s like auto_token.py)
            self._log("Doi trang load (12s)...")
            import time
            time.sleep(12)

            # Trigger API request and capture headers
            self._log("Trigger API request de capture headers...")
            captured = extractor.trigger_api_and_capture()

            if captured.is_valid():
                self._log(f"[OK] x-browser-validation: {captured.x_browser_validation[:40]}...", "success")
                self._log(f"[OK] Authorization: {captured.authorization[:50]}...", "success")

                # Extract token and project_id
                if captured.authorization.startswith("Bearer "):
                    token = captured.authorization[7:]
                    self.config['flow_bearer_token'] = token

                    # Extract project_id from URL if available
                    # (will be captured in next request)

                # Convert to dict for API use
                headers_dict = captured.to_dict()

                # Store for later use
                self.config['captured_headers'] = headers_dict

                extractor.stop_browser()
                return headers_dict
            else:
                self._log("Khong capture duoc headers day du", "error")
                self._log(f"  authorization: {bool(captured.authorization)}")
                self._log(f"  x-browser-validation: {bool(captured.x_browser_validation)}")

                # Fallback: try capture from network logs
                self._log("Thu capture tu network logs...")
                captured = extractor.capture_headers_from_network(timeout=30)

                if captured.is_valid():
                    headers_dict = captured.to_dict()
                    self.config['captured_headers'] = headers_dict
                    extractor.stop_browser()
                    return headers_dict

                extractor.stop_browser()
                return None

        except Exception as e:
            self._log(f"Exception khi capture headers: {e}", "error")
            import traceback
            traceback.print_exc()
            return None

    def generate_images_auto(
        self,
        excel_path: Optional[Path] = None,
        start_scene: int = 1,
        end_scene: Optional[int] = None,
        overwrite: bool = False,
        bearer_token: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Tao anh tu dong - chon mode dua tren config.

        Mode duoc cau hinh trong settings.yaml (generation_mode: 'chrome' hoac 'api')

        Args:
            excel_path: Duong dan file Excel
            start_scene: Scene bat dau
            end_scene: Scene ket thuc
            overwrite: Ghi de anh da co
            bearer_token: Bearer token (chi can cho API mode)

        Returns:
            Dict voi ket qua
        """
        mode = self._get_generation_mode()
        self._log(f"[AUTO] Generation mode: {mode.upper()}")

        # Check proxy support for API mode
        proxy_api_token = self.config.get('proxy_api_token', '')

        if mode == 'api':
            if proxy_api_token:
                self._log("[AUTO] API mode voi proxy support - su dung proxy de bypass captcha")
                return self.generate_scene_images_api(
                    excel_path=excel_path,
                    start_scene=start_scene,
                    end_scene=end_scene,
                    overwrite=overwrite
                )
            else:
                self._log("[AUTO] API mode khong co proxy token, chuyen sang Chrome mode...")

        # Fall back to Chrome mode
        return self.generate_scene_images(
            excel_path=excel_path,
            start_scene=start_scene,
            end_scene=end_scene,
            overwrite=overwrite
        )

    def generate_from_prompts_auto(
        self,
        prompts: List[Dict],
        excel_path: Optional[Path] = None,
        bearer_token: Optional[str] = None,
        max_setup_retries: int = 3
    ) -> Dict[str, Any]:
        """
        Tao anh tu prompts - tu dong chon mode dua tren config.

        Args:
            prompts: List prompts [{'id': '1', 'prompt': '...'}]
            excel_path: Duong dan Excel
            bearer_token: Bearer token (chi can cho API mode)
            max_setup_retries: So lan retry setup (chi cho Chrome mode)

        Returns:
            Dict voi ket qua
        """
        mode = self._get_generation_mode()
        self._log(f"[AUTO] Generation mode: {mode.upper()}")

        # Check proxy support for API mode
        proxy_api_token = self.config.get('proxy_api_token', '')
        local_server_enabled = self.config.get('local_server_enabled', False)
        # v1.0.516: Ho tro nhieu server - uu tien list, fallback single URL
        server_list = self.config.get('local_server_list', [])
        local_server_url = self.config.get('local_server_url', '')
        if not local_server_url and server_list:
            first = server_list[0]
            local_server_url = first.get('url', first) if isinstance(first, dict) else str(first)
        has_local_server = local_server_enabled and (local_server_url or server_list)

        # v1.0.513: api+server mode - chay API truoc, tu dong chuyen server khi 403 lien tuc
        if mode == 'api+server':
            self._log("[AUTO] API+SERVER mode: Bat dau bang API (Chrome local), chuyen Server neu 403 lien tuc")
            # v1.0.519: Buoc 1: FORCE chay API (DrissionPage) - TAT local_server de khong gui qua server
            old_server_enabled_api = self.config.get('local_server_enabled', False)
            self.config['local_server_enabled'] = False
            try:
                api_result = self.generate_from_prompts_api(
                    prompts=prompts,
                    excel_path=excel_path,
                    bearer_token=bearer_token
                )
            finally:
                self.config['local_server_enabled'] = old_server_enabled_api
            api_stats = api_result.get("stats", {})
            api_success = api_stats.get("success", 0)
            api_failed = api_stats.get("failed", 0)

            # Kiem tra co nen chuyen sang server khong
            # Drission mode: success = anh TAO MOI OK, skipped = anh da co, failed = that bai
            # Dieu kien chuyen: co anh failed VA KHONG tao duoc anh MOI nao (success == 0)
            should_switch = False
            if api_failed > 0 and api_success == 0:
                # 403 lien tuc: co anh failed nhung KHONG tao duoc anh MOI nao
                should_switch = True
                self._log(f"[API+SERVER] API khong tao duoc anh moi nao ({api_failed} fail, {api_stats.get('skipped', 0)} skip) → CHUYEN SERVER")

            # v1.0.516: Validate server URL hoac server list
            local_server_url = (local_server_url or '').strip()
            has_server = (local_server_url and local_server_url.startswith('http')) or bool(server_list)
            if should_switch and has_server:
                self._log(f"[API+SERVER] === CHUYEN SANG SERVER MODE ({len(server_list) or 1} servers) ===")
                # Luu va khoi phuc config an toan bang try/finally
                old_server_enabled = self.config.get('local_server_enabled', False)
                old_server_url = self.config.get('local_server_url', '')
                old_server_list = self.config.get('local_server_list', [])
                server_result = None
                try:
                    self.config['local_server_enabled'] = True
                    self.config['local_server_url'] = local_server_url
                    if server_list:
                        self.config['local_server_list'] = server_list

                    # Server tu skip anh da co tren disk (check output_path.exists())
                    server_result = self.generate_from_prompts_api(
                        prompts=prompts,
                        excel_path=excel_path,
                        bearer_token=bearer_token
                    )
                except Exception as e:
                    self._log(f"[API+SERVER] Server call FAILED: {e}", "ERROR")
                    server_result = {"success": False, "stats": {"success": 0, "failed": api_failed, "skipped": 0}}
                finally:
                    # LUON khoi phuc config du co loi
                    self.config['local_server_enabled'] = old_server_enabled
                    self.config['local_server_url'] = old_server_url
                    self.config['local_server_list'] = old_server_list

                server_stats = server_result.get("stats", {})
                # LUU Y: Switch chi xay ra khi api_success == 0 (API khong tao anh MOI nao)
                # server_stats.success = anh SERVER tao MOI
                # server_stats.skipped = file da co tren disk (tu run truoc hoac parallel worker)
                # server_stats.failed = anh ca server cung khong lam duoc
                # → Tong co file = success + skipped (= tat ca prompt co anh tren disk)
                # → Tong failed = failed (= prompt khong co anh)
                server_new = server_stats.get("success", 0)
                server_existed = server_stats.get("skipped", 0)
                server_failed = server_stats.get("failed", 0)
                combined_stats = {
                    "success": server_new + server_existed,
                    "failed": server_failed,
                    "skipped": 0,
                }
                self._log(f"[API+SERVER] Ket qua: Server tao moi {server_new}, da co san {server_existed}, "
                          f"that bai {server_failed} | Tong: {combined_stats['success']} OK, {server_failed} fail")
                return {"success": True, "stats": combined_stats}
            elif should_switch:
                self._log("[API+SERVER] Muon chuyen Server nhung chua cau hinh Server URL/list!", "WARN")

            return api_result

        # v1.0.523: Server mode - gui thang qua server (khong can local_server_enabled checkbox)
        if mode == 'server':
            # Chi can co URL, khong can checkbox local_server_enabled
            if local_server_url or server_list:
                self._log(f"[AUTO] SERVER mode: Gui anh qua server ({local_server_url})")
                # Save original config
                old_se = self.config.get('local_server_enabled', False)
                old_url = self.config.get('local_server_url', '')
                old_list = self.config.get('local_server_list', [])
                # Force server config
                self.config['local_server_enabled'] = True
                if local_server_url:
                    self.config['local_server_url'] = local_server_url
                if server_list:
                    self.config['local_server_list'] = server_list
                try:
                    return self.generate_from_prompts_api(
                        prompts=prompts,
                        excel_path=excel_path,
                        bearer_token=bearer_token
                    )
                finally:
                    # Always restore original config
                    self.config['local_server_enabled'] = old_se
                    self.config['local_server_url'] = old_url
                    self.config['local_server_list'] = old_list
            else:
                self._log("[AUTO] SERVER mode nhung chua cau hinh Server URLs!", "WARN")
                self._log("[AUTO] Hay nhap Server URLs trong Settings va bam Luu")
                return {"success": False, "error": "Chua cau hinh Server URLs"}

        if mode == 'api':
            # v1.0.535: Mode API = LUON dung Chrome local (DrissionPage)
            # Server URLs chi dung cho mode 'server' va 'api+server'
            # Tam tat local_server de KHONG gui qua server
            old_ls_enabled = self.config.get('local_server_enabled', False)
            self.config['local_server_enabled'] = False
            try:
                if proxy_api_token:
                    self._log("[AUTO] API mode voi proxy support - su dung proxy de bypass captcha")
                    return self.generate_from_prompts_api(
                        prompts=prompts,
                        excel_path=excel_path,
                        bearer_token=bearer_token
                    )
                else:
                    self._log("[AUTO] API mode - dung Chrome local (DrissionPage)")
                    return self.generate_from_prompts_api(
                        prompts=prompts,
                        excel_path=excel_path,
                        bearer_token=bearer_token
                    )
            finally:
                self.config['local_server_enabled'] = old_ls_enabled

        # Fall back to Chrome mode
        return self.generate_from_prompts(
            prompts=prompts,
            excel_path=excel_path,
            max_setup_retries=max_setup_retries
        )

    def generate_all(
        self,
        characters: bool = True,
        scenes: bool = True,
        start_scene: int = 1,
        end_scene: Optional[int] = None,
        overwrite: bool = False,
        bearer_token: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Tao tat ca anh (nhan vat + scenes).
        Tu dong chon mode dua tren config (generation_mode).

        Args:
            characters: Tao anh nhan vat
            scenes: Tao anh scenes
            start_scene: Scene bat dau
            end_scene: Scene ket thuc
            overwrite: Ghi de
            bearer_token: Bearer token (chi can cho API mode)

        Returns:
            Dict voi ket qua
        """
        results = {
            "characters": {},
            "scenes": {},
        }

        mode = self._get_generation_mode()
        self._log(f"[GENERATE_ALL] Mode: {mode.upper()}")

        try:
            # AUTO-UPDATE: Them filename annotations vao prompts (neu chua co)
            # Giup Flow match uploaded reference images voi prompt
            excel_path = self._find_excel_file()
            if scenes and excel_path and excel_path.exists():
                self._log("[AUTO] Kiem tra va cap nhat filename annotations trong prompts...", "info")
                try:
                    from modules.prompts_generator import PromptsGenerator
                    pg = PromptsGenerator()
                    updated = pg.update_excel_prompts_with_annotations(str(excel_path))
                    if updated:
                        self._log("[AUTO] Da cap nhat prompts voi filename annotations", "success")
                    else:
                        self._log("[AUTO] Khong can cap nhat annotations (da co san hoac khong co ref_files)", "info")
                except ImportError:
                    self._log("[AUTO] Khong the import PromptsGenerator, bo qua auto-update", "warn")
                except Exception as e:
                    self._log(f"[AUTO] Loi khi cap nhat annotations: {e}", "warn")

            if characters:
                # Characters luon dung Chrome mode (can UI de handle consent)
                results["characters"] = self.generate_character_images(overwrite=overwrite)

            if scenes:
                # Scenes su dung mode tu config
                results["scenes"] = self.generate_images_auto(
                    start_scene=start_scene,
                    end_scene=end_scene,
                    overwrite=overwrite,
                    bearer_token=bearer_token
                )
        finally:
            # Dong browser khi xong (chi can cho Chrome mode)
            if mode == 'chrome':
                self.stop_browser()

        return results

    def _escape_js_string(self, s: str) -> str:
        """Escape string cho JavaScript."""
        return (s
            .replace("\\", "\\\\")
            .replace("`", "\\`")
            .replace("$", "\\$")
            .replace("\n", "\\n")
            .replace("\r", "\\r")
            .replace("\t", "\\t"))

    # =========================================================================
    # API MODE - Direct API call without browser
    # =========================================================================

    def generate_scene_images_api(
        self,
        excel_path: Optional[Path] = None,
        start_scene: int = 1,
        end_scene: Optional[int] = None,
        overwrite: bool = False,
        bearer_token: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Tao anh bang API mode - goi truc tiep batchGenerateImages API.

        Uu diem:
        - Nhanh hon Chrome mode (khong can khoi dong browser)
        - On dinh hon (khong bi loi UI)

        Nhuoc diem:
        - Can bearer token (het han sau ~1h)
        - Khong tu dong xu ly consent dialogs

        Args:
            excel_path: Duong dan file Excel
            start_scene: Scene bat dau (1-indexed)
            end_scene: Scene ket thuc (None = tat ca)
            overwrite: Ghi de anh da co
            bearer_token: Bearer token (bat buoc)

        Returns:
            Dict voi ket qua
        """
        self._log("=" * 60)
        self._log("API MODE - TAO ANH BANG DIRECT API CALL")
        self._log("=" * 60)

        # Import GoogleFlowAPI
        try:
            from modules.google_flow_api import GoogleFlowAPI, AspectRatio
        except ImportError as e:
            return {"success": False, "error": f"Khong import duoc GoogleFlowAPI: {e}"}

        # Check proxy support
        proxy_api_token = self.config.get('proxy_api_token', '')
        use_proxy = bool(proxy_api_token)

        # Check local server mode (thay the nanoai.pics bang server rieng)
        local_server_enabled = self.config.get('local_server_enabled', False)
        local_server_url = self.config.get('local_server_url', '')
        if local_server_enabled and local_server_url:
            use_proxy = True  # Local server dung chung logic voi proxy

        if use_proxy:
            if local_server_enabled and local_server_url:
                self._log(f"Local server mode: {local_server_url}")
            else:
                self._log("Proxy API token co san - se su dung proxy de bypass captcha")

        # Check bearer token
        if not bearer_token:
            # Thu lay tu config
            bearer_token = self.config.get('flow_bearer_token', '')

        # Neu van chua co token, capture tu Chrome (PyAutoGUI)
        if not bearer_token:
            self._log("Khong co bearer token, thu tu dong lay...")
            bearer_token = self._auto_extract_token()

        if not bearer_token:
            return {
                "success": False,
                "error": "Can bearer token cho API mode. Chua co token va khong the tu dong lay."
            }

        # Tim file Excel
        if excel_path is None:
            excel_path = self._find_excel_file()

        if excel_path is None or not excel_path.exists():
            return {"success": False, "error": "Khong tim thay file Excel"}

        self._log(f"Excel: {excel_path}")

        # Use extracted flow_project_id if available, otherwise fallback to project_code
        flow_project_id = self.config.get('flow_project_id', self.project_code)
        self._log(f"Project ID: {flow_project_id}")
        self._log(f"Token: {bearer_token[:20]}...{bearer_token[-10:]}")
        if local_server_enabled and local_server_url:
            self._log(f"Mode: LOCAL SERVER ({local_server_url})")
        elif use_proxy:
            self._log(f"Mode: PROXY (nanoai.pics)")
        else:
            self._log(f"Mode: DIRECT API (khong proxy)")

        # Tao API client
        api = GoogleFlowAPI(
            bearer_token=bearer_token,
            project_id=flow_project_id,
            timeout=self.config.get('flow_timeout', 120),
            verbose=self.verbose,
            proxy_api_token=proxy_api_token,
            use_proxy=use_proxy,
            local_server_url=local_server_url if local_server_enabled else None
        )

        # Map aspect ratio
        ar_setting = self.config.get('flow_aspect_ratio', 'landscape')
        ar_map = {
            'landscape': AspectRatio.LANDSCAPE,
            'portrait': AspectRatio.PORTRAIT,
            'square': AspectRatio.SQUARE,
        }
        aspect_ratio = ar_map.get(ar_setting, AspectRatio.LANDSCAPE)

        # Load Excel
        workbook = PromptWorkbook(excel_path)
        workbook.load_or_create()

        # Lay cac scene can tao anh
        all_scenes = workbook.get_scenes()
        scenes_to_process = []

        for scene in all_scenes:
            if scene.scene_id < start_scene:
                continue
            if end_scene is not None and scene.scene_id > end_scene:
                break
            if not scene.img_prompt:
                continue
            if scene.status_img == "done" and not overwrite:
                self.stats["skipped"] += 1
                continue
            scenes_to_process.append(scene)

        if not scenes_to_process:
            self._log("Khong co scene nao can tao anh", "warn")
            return {"success": True, "message": "No scenes to process"}

        self._log(f"Se tao {len(scenes_to_process)} anh bang API")
        self.stats["total"] = len(scenes_to_process)

        # Load media cache cho reference
        cached_media_names = self._load_media_cache()
        if cached_media_names:
            self._log(f"Loaded {len(cached_media_names)} media references")

        # Process tung scene
        for i, scene in enumerate(scenes_to_process):
            # Normalize: 1.0 -> "1"
            scene_id = str(int(float(scene.scene_id))) if scene.scene_id else "0"
            prompt = scene.img_prompt

            self._log(f"\n[{i+1}/{len(scenes_to_process)}] Scene {scene_id}")
            self._log(f"Prompt ({len(prompt)} chars): {prompt[:100]}...")

            try:
                # Build image inputs from references
                image_inputs = []
                ref_str = getattr(scene, 'reference_files', '') or ''
                if ref_str:
                    try:
                        ref_files = json.loads(ref_str) if ref_str.startswith('[') else [f.strip() for f in ref_str.split(',') if f.strip()]
                    except:
                        ref_files = [f.strip() for f in str(ref_str).split(',') if f.strip()]

                    for ref_file in ref_files:
                        ref_id = ref_file.replace('.png', '').replace('.jpg', '')
                        # Check cache for media_name
                        if ref_id in cached_media_names:
                            media_info = cached_media_names[ref_id]
                            media_name = media_info.get('mediaName') if isinstance(media_info, dict) else media_info
                            if media_name:
                                from modules.google_flow_api import ImageInput, ImageInputType
                                image_inputs.append(ImageInput(
                                    name=media_name,
                                    input_type=ImageInputType.REFERENCE
                                ))
                                self._log(f"[REF] Using cached media: {ref_id}")

                # Generate image - co retry khi token het han
                success, images, error = api.generate_images(
                    prompt=prompt,
                    count=self.config.get('flow_image_count', 1),  # Default 1 ảnh
                    aspect_ratio=aspect_ratio,
                    image_inputs=[inp.to_dict() for inp in image_inputs] if image_inputs else None
                )

                # Check token expired (401) - auto refresh and retry
                if not success and error:
                    error_lower = str(error).lower()
                    is_token_expired = '401' in error_lower or 'expired' in error_lower or 'authentication' in error_lower or 'unauthenticated' in error_lower
                    if is_token_expired:
                        self._log(f"Token het han (401)! Dang lay token moi bang DrissionPage...", "warn")
                        new_token = self._extract_token_via_drission(excel_path)  # v1.0.626: DrissionPage thay PyAutoGUI
                        if new_token:
                            # Update API instance with new token
                            api.bearer_token = new_token
                            bearer_token = new_token
                            self._log(f"Token moi OK - dang retry scene {scene_id}...")

                            # Retry generation
                            success, images, error = api.generate_images(
                                prompt=prompt,
                                count=self.config.get('flow_image_count', 1),  # Default 1 ảnh
                                aspect_ratio=aspect_ratio,
                                image_inputs=[inp.to_dict() for inp in image_inputs] if image_inputs else None
                            )
                        else:
                            self._log(f"Khong the refresh token!", "error")

                if success and images:
                    # Xác định thư mục lưu: nv*/loc* -> nv/, còn lại -> img/
                    scene_id_str = str(scene_id)
                    if scene_id_str.lower().startswith('nv') or scene_id_str.lower().startswith('loc'):
                        save_dir = self.nv_path
                        relative_folder = "nv"
                    else:
                        save_dir = self.img_path
                        relative_folder = "img"

                    output_file = save_dir / f"{scene_id}.png"

                    downloaded = api.download_image(
                        images[0],  # Take first image
                        save_dir,
                        scene_id
                    )

                    if downloaded:
                        # Update Excel
                        relative_path = f"{relative_folder}/{scene_id}.png"
                        workbook.update_scene(
                            scene.scene_id,
                            img_path=relative_path,
                            status_img="done"
                        )
                        workbook.safe_save()

                        # Save media_name to cache
                        if images[0].media_name:
                            cached_media_names[scene_id] = {
                                'mediaName': images[0].media_name,
                                'seed': images[0].seed
                            }

                        self._log(f"OK - Da tao va luu anh: {downloaded}", "success")
                        self.stats["success"] += 1
                    else:
                        self._log("Loi download anh", "error")
                        workbook.update_scene(scene.scene_id, status_img="error")
                        workbook.safe_save()
                        self.stats["failed"] += 1
                else:
                    self._log(f"Loi: {error}", "error")
                    workbook.update_scene(scene.scene_id, status_img="error")
                    workbook.safe_save()
                    self.stats["failed"] += 1

                # Delay giua cac prompt
                delay = self.config.get('flow_delay', 3.0)
                if i < len(scenes_to_process) - 1:
                    time.sleep(delay)

            except Exception as e:
                self._log(f"Exception: {e}", "error")
                import traceback
                traceback.print_exc()
                workbook.update_scene(scene.scene_id, status_img="error")
                workbook.safe_save()
                self.stats["failed"] += 1

        # Save updated media cache
        if cached_media_names:
            self._save_media_cache(cached_media_names)

        # Summary
        self._log("\n" + "=" * 60)
        self._log("HOAN THANH (API MODE)")
        self._log("=" * 60)
        self._log(f"Tong: {self.stats['total']}")
        self._log(f"Thanh cong: {self.stats['success']}")
        self._log(f"That bai: {self.stats['failed']}")
        self._log(f"Bo qua: {self.stats['skipped']}")

        return {
            "success": True,
            "stats": self.stats.copy()
        }

    def generate_scene_videos_api(
        self,
        excel_path: Optional[Path] = None,
        start_scene: int = 1,
        end_scene: Optional[int] = None,
        max_videos: int = 10,
        bearer_token: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Tao video cho scenes bang API mode (proxy).

        Args:
            excel_path: Duong dan file Excel
            start_scene: Scene bat dau (1-indexed)
            end_scene: Scene ket thuc (None = tat ca)
            max_videos: So video toi da can tao (default 10)
            bearer_token: Bearer token (bat buoc)

        Returns:
            Dict voi ket qua
        """
        self._log("=" * 60)
        self._log("API MODE - TAO VIDEO CHO SCENES")
        self._log("=" * 60)

        # Import GoogleFlowAPI
        try:
            from modules.google_flow_api import GoogleFlowAPI, VideoAspectRatio, VideoModel
        except ImportError as e:
            return {"success": False, "error": f"Khong import duoc GoogleFlowAPI: {e}"}

        # Check proxy support
        proxy_api_token = self.config.get('proxy_api_token', '')
        use_proxy = bool(proxy_api_token)

        # Check local server mode
        local_server_enabled = self.config.get('local_server_enabled', False)
        local_server_url = self.config.get('local_server_url', '')
        if local_server_enabled and local_server_url:
            use_proxy = True

        if not use_proxy:
            return {"success": False, "error": "Can proxy_api_token hoac local_server de tao video"}

        if local_server_enabled and local_server_url:
            self._log(f"Local server mode: {local_server_url}")
        else:
            self._log("Proxy API token co san - su dung proxy de tao video")

        # Check bearer token
        if not bearer_token:
            bearer_token = self.config.get('flow_bearer_token', '')

        if not bearer_token:
            self._log("Khong co bearer token, thu tu dong lay...")
            bearer_token = self._auto_extract_token()

        if not bearer_token:
            return {
                "success": False,
                "error": "Can bearer token cho API mode"
            }

        # Tim file Excel
        if excel_path is None:
            excel_path = self._find_excel_file()

        if excel_path is None or not excel_path.exists():
            return {"success": False, "error": "Khong tim thay file Excel"}

        self._log(f"Excel: {excel_path}")

        # Use extracted flow_project_id if available
        flow_project_id = self.config.get('flow_project_id', self.project_code)
        self._log(f"Project ID: {flow_project_id}")
        self._log(f"Token: {bearer_token[:20]}...{bearer_token[-10:]}")
        self._log(f"Max videos: {max_videos}")

        # Tao API client
        api = GoogleFlowAPI(
            bearer_token=bearer_token,
            project_id=flow_project_id,
            timeout=self.config.get('flow_timeout', 300),  # Longer timeout for video
            verbose=self.verbose,
            proxy_api_token=proxy_api_token,
            use_proxy=True,
            local_server_url=local_server_url if local_server_enabled else None
        )

        # Map aspect ratio
        ar_setting = self.config.get('flow_aspect_ratio', 'landscape')
        ar_map = {
            'landscape': VideoAspectRatio.LANDSCAPE,
            'portrait': VideoAspectRatio.PORTRAIT,
            'square': VideoAspectRatio.SQUARE,
        }
        aspect_ratio = ar_map.get(ar_setting, VideoAspectRatio.LANDSCAPE)

        # Load Excel
        workbook = PromptWorkbook(excel_path)
        workbook.load_or_create()

        # Lay cac scene can tao video
        all_scenes = workbook.get_scenes()
        scenes_to_process = []

        for scene in all_scenes:
            if scene.scene_id < start_scene:
                continue
            if end_scene is not None and scene.scene_id > end_scene:
                break
            # Chi tao video cho scenes da co anh
            if not scene.img_path:
                continue
            # Bo qua scenes da tao video
            if scene.status_vid == "done":
                self.stats["skipped"] += 1
                continue
            # Lay video_prompt hoac img_prompt
            video_prompt = scene.video_prompt or scene.img_prompt
            if not video_prompt:
                continue
            scenes_to_process.append((scene, video_prompt))
            # Gioi han so video
            if len(scenes_to_process) >= max_videos:
                break

        if not scenes_to_process:
            self._log("Khong co scene nao can tao video", "warn")
            return {"success": True, "message": "No scenes to process"}

        self._log(f"Se tao {len(scenes_to_process)} video bang API")
        self.stats["total"] = len(scenes_to_process)

        # Output folder
        output_folder = excel_path.parent / "img"
        output_folder.mkdir(parents=True, exist_ok=True)

        # Generate videos
        for i, (scene, video_prompt) in enumerate(scenes_to_process):
            # Normalize: 1.0 -> "1"
            scene_id = str(int(float(scene.scene_id))) if scene.scene_id else "0"
            self._log(f"\n[{i+1}/{len(scenes_to_process)}] Scene: {scene_id}")
            self._log(f"Prompt ({len(video_prompt)} chars): {video_prompt[:80]}...")

            try:
                # Generate video
                success, result, error = api.generate_video(
                    prompt=video_prompt,
                    aspect_ratio=aspect_ratio,
                    model=VideoModel.VEO3_FAST,
                    scene_id=scene_id
                )

                if success and result.video_url:
                    # Download video
                    video_filename = f"scene_{scene_id}"
                    downloaded = api.download_video(result, output_folder, video_filename)

                    if downloaded:
                        # Update Excel - chi luu filename, khong luu full path
                        relative_path = downloaded.name
                        workbook.update_scene(
                            scene.scene_id,
                            video_path=relative_path,
                            status_vid="done"
                        )
                        workbook.safe_save()

                        self._log(f"OK - Da tao va luu video: {downloaded}", "success")
                        self.stats["success"] += 1
                    else:
                        self._log("Loi download video", "error")
                        workbook.update_scene(scene.scene_id, status_vid="error")
                        workbook.safe_save()
                        self.stats["failed"] += 1
                else:
                    self._log(f"Loi: {error}", "error")
                    workbook.update_scene(scene.scene_id, status_vid="error")
                    workbook.safe_save()
                    self.stats["failed"] += 1

                # Delay giua cac video (video mat nhieu thoi gian hon)
                delay = self.config.get('video_delay', 5.0)
                if i < len(scenes_to_process) - 1:
                    time.sleep(delay)

            except Exception as e:
                self._log(f"Exception: {e}", "error")
                import traceback
                traceback.print_exc()
                workbook.update_scene(scene.scene_id, status_vid="error")
                workbook.safe_save()
                self.stats["failed"] += 1

        # Summary
        self._log("\n" + "=" * 60)
        self._log("HOAN THANH VIDEO (API MODE)")
        self._log("=" * 60)
        self._log(f"Tong: {self.stats['total']}")
        self._log(f"Thanh cong: {self.stats['success']}")
        self._log(f"That bai: {self.stats['failed']}")
        self._log(f"Bo qua: {self.stats['skipped']}")

        return {
            "success": True,
            "stats": self.stats.copy()
        }

    def generate_videos_from_excel(
        self,
        excel_path: Optional[Path] = None,
        max_videos: int = 10
    ) -> Dict[str, Any]:
        """
        Tạo video từ Excel - GIỐNG HỆT FLOW TẠO ẢNH.
        Mở Chrome → Chuyển mode video → Tạo video → Đóng Chrome.

        Args:
            excel_path: Đường dẫn file Excel
            max_videos: Số video tối đa cần tạo

        Returns:
            Dict với kết quả {success, failed, stats}
        """
        self._log("=" * 60)
        self._log("TẠO VIDEO TỪ ẢNH (Chrome UI - giống tạo ảnh)")
        self._log("=" * 60)

        # Reset stats
        self.stats = {"total": 0, "success": 0, "failed": 0, "skipped": 0}

        # Import DrissionFlowAPI
        try:
            from modules.drission_flow_api import DrissionFlowAPI
        except ImportError as e:
            return {"success": False, "error": f"Không import được DrissionFlowAPI: {e}"}

        # Load Excel
        from modules.excel_manager import PromptWorkbook
        if not excel_path:
            excel_path = self._find_excel_file()
        if not excel_path:
            return {"success": False, "error": "Không tìm thấy file Excel"}

        workbook = PromptWorkbook(excel_path)
        workbook.load_or_create()

        # === ĐỌC MEDIA_ID TỪ CACHE (giống tạo ảnh đọc cached_media_names) ===
        cache_path = Path(excel_path).parent / ".media_cache.json"
        cached_media_names = {}
        if cache_path.exists():
            try:
                import json
                with open(cache_path, 'r', encoding='utf-8') as f:
                    cache_data = json.load(f)
                for key, value in cache_data.items():
                    if key.startswith('_'):
                        continue  # Skip metadata
                    if isinstance(value, dict):
                        cached_media_names[key] = value.get('mediaName', '')
                    elif isinstance(value, str):
                        cached_media_names[key] = value
                self._log(f"[CACHE] Loaded {len(cached_media_names)} media_ids từ cache")
            except Exception as e:
                self._log(f"[CACHE] Error: {e}", "warn")

        # Lấy scenes cần tạo video (có media_id trong cache, chưa có video)
        scenes_for_video = []
        for scene in workbook.get_scenes():
            # Normalize: 1.0 -> "1"
            scene_id = str(int(float(scene.scene_id))) if hasattr(scene, 'scene_id') and scene.scene_id else ''
            if not scene_id or not scene_id.isdigit():
                continue

            # Lấy media_id từ CACHE (giống tạo ảnh)
            media_id = cached_media_names.get(scene_id, '')
            if not media_id:
                # Fallback: thử Excel
                media_id = getattr(scene, 'media_id', '') or ''

            video_path = getattr(scene, 'video_path', '') or ''
            status_vid = getattr(scene, 'status_vid', '') or ''

            if not media_id:
                continue
            if video_path or status_vid == 'done':
                continue

            video_prompt = getattr(scene, 'video_prompt', '') or 'Subtle cinematic motion'
            scenes_for_video.append({
                'scene_id': scene_id,
                'media_id': media_id,
                'video_prompt': video_prompt
            })

            if len(scenes_for_video) >= max_videos:
                break

        if not scenes_for_video:
            self._log(f"Không có scene nào cần tạo video (cache có {len(cached_media_names)} items)")
            return {"success": True, "message": "No scenes to process"}

        self._log(f"Sẽ tạo {len(scenes_for_video)} video")
        self.stats["total"] = len(scenes_for_video)

        # === ĐỌC PROJECT_URL TỪ EXCEL (giống tạo ảnh) ===
        # Để vào đúng project đã tạo ảnh, giữ media_id valid
        saved_project_url = None
        try:
            import openpyxl
            wb = openpyxl.load_workbook(excel_path, data_only=True)
            if 'Config' in wb.sheetnames:
                ws = wb['Config']
                for row in ws.iter_rows(min_row=2, max_col=2):
                    key = row[0].value
                    val = row[1].value
                    if key == 'flow_project_url' and val and '/project/' in str(val):
                        saved_project_url = str(val)
                        self._log(f"[FOUND] Project URL từ Excel: {saved_project_url[:50]}...")
                        break
                    elif key == 'flow_project_id' and val and not saved_project_url:
                        saved_project_url = f"https://labs.google/fx/vi/tools/flow/project/{val}"
                        self._log(f"[FOUND] Project ID từ Excel: {val[:20]}...")
            wb.close()
        except Exception as e:
            self._log(f"Đọc Excel config error: {e}", "warn")

        # Fallback: đọc từ cache
        if not saved_project_url:
            cache_path = Path(excel_path).parent / ".media_cache.json"
            if cache_path.exists():
                try:
                    import json
                    with open(cache_path, 'r', encoding='utf-8') as f:
                        cache_data = json.load(f)
                    cached_url = cache_data.get('_project_url', '')
                    cached_id = cache_data.get('_project_id', '')
                    if cached_url and '/project/' in cached_url:
                        saved_project_url = cached_url
                        self._log(f"[FOUND] Project URL từ cache: {saved_project_url[:50]}...")
                    elif cached_id:
                        saved_project_url = f"https://labs.google/fx/vi/tools/flow/project/{cached_id}"
                except:
                    pass

        if not saved_project_url:
            return {"success": False, "error": "Không tìm thấy project URL. Hãy tạo ảnh trước!"}

        # Webshare config
        ws_cfg = self.config.get('webshare_proxy', {})

        # Tạo DrissionFlowAPI (giống tạo ảnh)
        self._log("Khởi tạo Chrome (hiển thị)...")
        drission_api = DrissionFlowAPI(
            headless=False,  # Chrome hiển thị
            verbose=True,
            log_callback=lambda msg, lvl: self._log(msg, lvl),
            webshare_enabled=ws_cfg.get('enabled', False),
            machine_id=ws_cfg.get('machine_id', 1),
            worker_id=self.worker_id,
            total_workers=self.total_workers,  # Chia màn hình
            chrome_portable=self.config.get('chrome_portable', '')
        )

        # Setup Chrome - VÀO ĐÚNG PROJECT ĐÃ TẠO ẢNH
        self._log(f"Vào project: {saved_project_url[:60]}...")
        if not drission_api.setup(project_url=saved_project_url):
            return {"success": False, "error": "Không setup được Chrome"}

        self._log("[v] Chrome sẵn sàng - bắt đầu tạo video...")

        # Output folder
        output_dir = Path(self.project_path) / "img"
        output_dir.mkdir(parents=True, exist_ok=True)

        # Tạo video cho từng scene
        video_success = 0
        video_failed = 0

        for i, scene_info in enumerate(scenes_for_video):
            scene_id = scene_info['scene_id']
            media_id = scene_info['media_id']
            video_prompt = scene_info['video_prompt']

            self._log(f"\n[{i+1}/{len(scenes_for_video)}] Scene {scene_id}...")

            try:
                video_file = output_dir / f"{scene_id}.mp4"

                # Gọi generate_video_t2v_mode - Chuyển sang "Từ văn bản sang video"
                # Chrome gửi T2V request → Interceptor convert sang I2V với media_id
                success, result_path, error = drission_api.generate_video_t2v_mode(
                    media_id=media_id,
                    prompt=video_prompt,
                    video_model="veo_3_0_r2v_fast",
                    save_path=video_file
                )

                if success:
                    self._log(f"   [v] OK: {video_file.name}")
                    video_success += 1
                    self.stats["success"] += 1

                    # Di chuyển ảnh vào img_backup/ sau khi tạo video thành công
                    # (Giữ lại để hiển thị thumbnail trong GUI)
                    image_file = output_dir / f"{scene_id}.png"
                    if image_file.exists():
                        try:
                            # Tạo folder backup nếu chưa có
                            backup_dir = output_dir.parent / "img_backup"
                            backup_dir.mkdir(exist_ok=True)

                            # Di chuyển ảnh vào backup
                            backup_file = backup_dir / image_file.name
                            image_file.rename(backup_file)
                            self._log(f"   [MOVE] Moved image to backup: {image_file.name}")
                        except Exception as e:
                            self._log(f"   [WARN] Cannot move image: {e}", "warn")

                    # Update Excel
                    workbook.update_scene(int(scene_id), video_path=video_file.name, status_vid='done')
                    workbook.safe_save()
                else:
                    self._log(f"   [x] Failed: {error}", "warn")
                    video_failed += 1
                    self.stats["failed"] += 1

            except Exception as e:
                self._log(f"   [x] Error: {e}", "error")
                video_failed += 1
                self.stats["failed"] += 1

            # Delay giữa các video
            time.sleep(3)

        # v1.0.163: Bỏ switch_to_image_mode() - giao diện mới không cần
        # drission_api.switch_to_image_mode()

        # Close Chrome
        drission_api.close()

        # Summary
        self._log("\n" + "=" * 60)
        self._log("HOÀN THÀNH VIDEO (Chrome UI)")
        self._log("=" * 60)
        self._log(f"Tổng: {self.stats['total']}")
        self._log(f"Thành công: {self.stats['success']}")
        self._log(f"Thất bại: {self.stats['failed']}")

        return {
            "success": video_success,
            "failed": video_failed,
            "stats": self.stats.copy()
        }

    def _extract_token_via_drission(self, excel_path: Optional[Path] = None) -> Optional[str]:
        """
        Lay bearer token bang DrissionPage (khong can DevTools/F12).
        Mo ChromePortable da login → vao project URL tu Excel → inject interceptor
        → gui 1 prompt → capture token → luu vao Excel.

        Returns:
            Bearer token (ya29.xxx) hoac None
        """
        import time

        self._log("=== LAY TOKEN BANG DRISSION PAGE ===")

        try:
            from modules.drission_flow_api import DrissionFlowAPI, JS_INTERCEPTOR
        except ImportError as e:
            self._log(f"Khong import duoc DrissionFlowAPI: {e}", "error")
            return None

        # Chrome Portable path - dung dung chrome cua worker nay
        chrome_portable = self.config.get('chrome_portable', '')
        if not chrome_portable:
            self._log("Khong co chrome_portable trong config!", "error")
            return None

        root_dir = Path(__file__).parent.parent
        cp_path = Path(chrome_portable)
        if not cp_path.is_absolute():
            cp_path = root_dir / chrome_portable
        if not cp_path.exists():
            self._log(f"ChromePortable khong ton tai: {cp_path}", "error")
            return None

        self._log(f"ChromePortable: {cp_path}")
        self._log(f"Worker ID: {self.worker_id}")

        # Project URL tu Excel
        project_url = ''
        if excel_path and excel_path.exists():
            try:
                workbook = PromptWorkbook(excel_path)
                workbook.load_or_create()
                project_url = workbook.get_config_value('flow_project_url')
                if project_url:
                    self._log(f"Project URL tu Excel: {project_url[:60]}...")
            except Exception as e:
                self._log(f"Khong doc duoc Excel config: {e}", "warn")

        # Tao DrissionFlowAPI - dung worker_id de tranh conflict port
        drission = DrissionFlowAPI(
            chrome_portable=str(cp_path),
            worker_id=self.worker_id,
            total_workers=self.total_workers,
            headless=False,  # Can hien thi de Google khong block
            verbose=self.verbose,
            log_callback=lambda msg, level="info": self._log(f"[Drission] {msg}", level),
            webshare_enabled=False,  # Khong can proxy cho viec lay token
        )

        try:
            # Setup Chrome - inject interceptor
            self._log("Mo Chrome va inject interceptor...")
            setup_ok = drission.setup(
                wait_for_project=True,
                timeout=60,
                warm_up=False,
                project_url=project_url or None,
            )

            if not setup_ok:
                self._log("DrissionPage setup that bai!", "error")
                return None

            # Gui 1 prompt test de capture token
            self._log("Gui prompt test de capture token...")
            test_prompt = "a simple red circle on white background"
            capture_ok = drission._capture_tokens(test_prompt, timeout=30)

            # Cho local server, CHI CAN bearer token (server tu tao recaptcha)
            # _capture_tokens() doi ca recaptcha nen co the fail
            # → doc window._tk truc tiep
            token = None
            if capture_ok and drission.bearer_token:
                token = drission.bearer_token
            else:
                # Thu doc _tk truc tiep tu JS
                self._log("_capture_tokens fail (thieu recaptcha), thu doc _tk truc tiep...")
                try:
                    tk = drission.driver.run_js("return window._tk")
                    if tk:
                        token = f"Bearer {tk}"
                        self._log(f"[OK] Lay _tk truc tiep thanh cong!")
                except Exception as e:
                    self._log(f"Khong doc duoc _tk: {e}", "error")

            if not token:
                self._log("Khong capture duoc token!", "error")
                return None

            if token.startswith("Bearer "):
                token = token[7:]

            self._log(f"[OK] Token: {token[:20]}...{token[-10:]}")

            # Lay project URL va ID
            captured_project_url = drission.captured_url or ''
            if not captured_project_url:
                try:
                    captured_project_url = drission.driver.url
                except:
                    pass
            captured_project_id = drission.project_id or ''
            # Fallback: doc _pj truc tiep
            if not captured_project_id:
                try:
                    pj = drission.driver.run_js("return window._pj")
                    if pj:
                        captured_project_id = pj
                except:
                    pass

            self._log(f"Project ID: {captured_project_id}")
            self._log(f"Project URL: {captured_project_url[:60]}..." if captured_project_url else "Project URL: N/A")

            # Luu vao Excel config
            if excel_path and excel_path.exists():
                try:
                    workbook = PromptWorkbook(excel_path)
                    workbook.load_or_create()
                    workbook.set_config_value('flow_bearer_token', token)
                    if captured_project_url:
                        workbook.set_config_value('flow_project_url', captured_project_url)
                    if captured_project_id:
                        workbook.set_config_value('flow_project_id', captured_project_id)
                    workbook.save()
                    self._log("[OK] Token da luu vao Excel config!")
                except Exception as e:
                    self._log(f"Luu Excel that bai: {e}", "warn")

            # Luu vao settings config (de reuse trong session)
            self.config['flow_bearer_token'] = token
            if captured_project_id:
                self.config['flow_project_id'] = captured_project_id
            self._cached_bearer_token = token
            self._cached_token_time = time.time()

            return token

        except Exception as e:
            self._log(f"Loi extract token: {e}", "error")
            import traceback
            traceback.print_exc()
            return None

        finally:
            # Dong Chrome
            try:
                drission.close()
                self._log("Chrome dong thanh cong")
            except:
                pass

    def _generate_via_local_server(
        self,
        prompts: List[Dict],
        excel_path: Optional[Path] = None,
        bearer_token: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Tao anh qua Local Proxy Server(s).
        Ho tro NHIEU server song song (N server = N anh dong thoi).
        VM tu dong chon server it viec nhat (least-queue).
        """
        import time
        from concurrent.futures import ThreadPoolExecutor, as_completed

        try:
            from modules.google_flow_api import GoogleFlowAPI, AspectRatio
            from modules.server_pool import ServerPool
        except ImportError as e:
            return {"success": False, "error": f"Khong import duoc module: {e}",
                    "stats": {"success": 0, "failed": 0, "skipped": 0}}

        # Excel (can truoc de doc token tu Excel config)
        if excel_path is None:
            excel_path = self._find_excel_file()
        if excel_path is None or not excel_path.exists():
            return {"success": False, "error": "Khong tim thay file Excel",
                    "stats": {"success": 0, "failed": 0, "skipped": 0}}

        # Bearer token - uu tien: param > config > Excel > DrissionPage
        if not bearer_token:
            bearer_token = self.config.get('flow_bearer_token', '')
        if not bearer_token and excel_path:
            try:
                wb_tmp = PromptWorkbook(excel_path)
                wb_tmp.load_or_create()
                bearer_token = wb_tmp.get_config_value('flow_bearer_token')
                if bearer_token:
                    self._log(f"Bearer token tu Excel config: {bearer_token[:20]}...")
                    self.config['flow_bearer_token'] = bearer_token
                # Doc luon project_id va project_url tu Excel (tranh phai tao anh test)
                excel_project_id = wb_tmp.get_config_value('flow_project_id')
                if excel_project_id and not self.config.get('flow_project_id'):
                    self.config['flow_project_id'] = excel_project_id
                    self._log(f"Project ID tu Excel config: {excel_project_id}")
                excel_project_url = wb_tmp.get_config_value('flow_project_url')
                if excel_project_url and not self.config.get('flow_project_url'):
                    self.config['flow_project_url'] = excel_project_url
                    self._log(f"Project URL tu Excel config: {excel_project_url[:60]}...")
            except:
                pass
        if not bearer_token:
            # Chrome 2 (worker_id > 0): doi Chrome 1 extract token truoc
            if self.worker_id > 0:
                self._log(f"[Worker {self.worker_id}] Doi Chrome 1 lay token (check Excel moi 10s)...")
                for wait_i in range(18):
                    time.sleep(10)
                    try:
                        wb_tmp2 = PromptWorkbook(excel_path)
                        wb_tmp2.load_or_create()
                        bearer_token = wb_tmp2.get_config_value('flow_bearer_token')
                        if bearer_token:
                            self._log(f"[Worker {self.worker_id}] Token tu Excel: {bearer_token[:20]}...")
                            break
                    except:
                        pass
                    if (wait_i + 1) % 6 == 0:
                        self._log(f"[Worker {self.worker_id}] Van doi token... ({(wait_i+1)*10}s)")
            if not bearer_token:
                self._log("Khong co bearer token, dung DrissionPage de lay...")
                bearer_token = self._extract_token_via_drission(excel_path)
        if not bearer_token:
            return {"success": False, "error": "Can bearer token. VM phai dang nhap Google truoc.",
                    "stats": {"success": 0, "failed": 0, "skipped": 0}}

        # Server Pool - nhieu server song song
        pool = ServerPool(self.config, log_callback=lambda msg, lvl="info": self._log(f"[POOL] {msg}", lvl))
        pool.refresh_all()
        num_servers = pool.available_count()

        if num_servers == 0:
            # v1.0.548: Server mode - cho den khi co server
            _gen_mode = self.config.get('generation_mode', '')
            if _gen_mode in ('server', 'api+server'):
                self._log("[POOL] Chua co server nao san sang - CHO (server mode)...")
                _wait_count = 0
                while num_servers == 0:
                    _wait_count += 1
                    time.sleep(30)
                    pool.refresh_all()
                    num_servers = pool.available_count()
                    if _wait_count % 10 == 0:
                        self._log(f"[POOL] Van cho server... ({_wait_count * 30}s)")
                self._log(f"[POOL] Da co {num_servers} server san sang!")
            else:
                return {"success": False, "error": "Khong co server nao kha dung!",
                        "stats": {"success": 0, "failed": 0, "skipped": 0}}

        # Project ID va config chung
        flow_project_id = self.config.get('flow_project_id', self.project_code)
        flow_timeout = self.config.get('flow_timeout', 120)
        ar_setting = self.config.get('flow_aspect_ratio', 'landscape')
        ar_map = {'landscape': AspectRatio.LANDSCAPE, 'portrait': AspectRatio.PORTRAIT, 'square': AspectRatio.SQUARE}
        aspect_ratio = ar_map.get(ar_setting, AspectRatio.LANDSCAPE)

        self._log(f"Project ID: {flow_project_id}")
        self._log(f"Token: {bearer_token[:20]}...{bearer_token[-10:]}")
        self._log(f"Servers: {num_servers} kha dung")
        self._log(f"Prompts: {len(prompts)}")
        self._log(f"Mode: {'SONG SONG' if num_servers > 1 else 'TUAN TU'} ({num_servers} server)")

        # Load Excel + lock cho thread-safe writes
        workbook = PromptWorkbook(excel_path)
        workbook.load_or_create()
        excel_lock = threading.Lock()

        # Load media_ids tu Excel (nv1 -> media_id, loc1 -> media_id)
        media_ids = {}
        try:
            media_ids = workbook.get_media_ids()
            if media_ids:
                self._log(f"[REF] Loaded {len(media_ids)} media_ids for references")
        except Exception:
            pass
        media_ids_lower = {k.lower(): v for k, v in media_ids.items()}

        success_count = 0
        failed_count = 0
        skipped_count = 0  # v1.0.514: Tach rieng skip khoi success
        count_lock = threading.Lock()
        proj_dir = Path(self.project_path)

        # Filter prompts can xu ly
        pending_prompts = []
        for i, prompt_data in enumerate(prompts):
            prompt_id = prompt_data.get('id', str(i + 1))
            prompt_text = prompt_data.get('prompt', '')
            if not prompt_text:
                continue

            # Xac dinh output path
            if prompt_id.startswith('nv') or prompt_id.startswith('loc'):
                output_path = proj_dir / "nv" / f"{prompt_id}.png"
            elif prompt_id.startswith('thumb'):
                output_path = proj_dir / "thumb" / f"{prompt_id}.png"
            else:
                output_path = proj_dir / "img" / f"{prompt_id}.png"

            output_path.parent.mkdir(parents=True, exist_ok=True)

            if output_path.exists():
                # v1.0.526: Nếu là ref (nv/loc) có file nhưng KHÔNG có media_id → vẫn tạo lại để lấy media_id
                is_ref = prompt_id.startswith('nv') or prompt_id.startswith('loc')
                has_media_id = prompt_id.lower() in media_ids_lower
                if is_ref and not has_media_id:
                    self._log(f"[REF] {prompt_id}: Có ảnh nhưng KHÔNG có media_id → tạo lại qua server")
                    # Không skip - thêm vào pending để lấy media_id
                else:
                    with count_lock:
                        skipped_count += 1
                    continue

            pending_prompts.append({
                'id': prompt_id,
                'prompt': prompt_text,
                'output_path': output_path,
                'index': i,
                'reference_files': prompt_data.get('reference_files', ''),
            })

        self._log(f"[SKIP] Da co {success_count} anh | Con {len(pending_prompts)} can tao")

        if not pending_prompts:
            return {"success": True, "total": len(prompts), "generated": 0, "failed": 0,
                    "stats": {"success": 0, "failed": 0, "skipped": skipped_count}}

        # v1.0.548: Server mode = KHONG BAO GIO bo cuoc
        _is_server_mode = self.config.get('generation_mode', '') in ('server', 'api+server')

        # Ham xu ly 1 prompt tren 1 server
        def _process_one(prompt_info):
            nonlocal success_count, failed_count, bearer_token
            pid = prompt_info['id']
            ptxt = prompt_info['prompt']
            opath = prompt_info['output_path']
            idx = prompt_info['index']
            ref_files_raw = prompt_info.get('reference_files', '')

            # Build image_inputs tu reference_files + media_ids
            image_inputs = []
            if ref_files_raw:
                try:
                    import json as _json
                    ref_list = _json.loads(ref_files_raw) if isinstance(ref_files_raw, str) else ref_files_raw
                    if isinstance(ref_list, list):
                        for ref in ref_list:
                            ref_id = Path(ref).stem.lower()  # "nv/nv1.png" → "nv1"
                            if ref_id in media_ids_lower:
                                image_inputs.append({
                                    "name": media_ids_lower[ref_id],
                                    "imageInputType": "IMAGE_INPUT_TYPE_REFERENCE"
                                })
                except Exception:
                    pass

            # v1.0.548: Server mode = cho VO HAN, khong bao gio bo cuoc
            server = pool.pick_best_server()
            if not server:
                if _is_server_mode:
                    # Server mode: cho den khi co server (retry moi 30s)
                    self._log(f"  [{idx+1}] {pid}: Cho server san sang (server mode - cho vo han)...")
                    _wait_round = 0
                    while not server:
                        _wait_round += 1
                        server = pool.wait_for_server(max_wait=60)
                        if not server:
                            if _wait_round % 5 == 0:
                                pool.refresh_all()  # Refresh lai trang thai servers
                                self._log(f"  [{idx+1}] {pid}: Van cho server... ({_wait_round * 60}s)")
                            time.sleep(5)
                else:
                    self._log(f"  [{idx+1}] {pid}: Cho server san sang...")
                    server = pool.wait_for_server(max_wait=300)
                    if not server:
                        self._log(f"  [{idx+1}] {pid}: Het thoi gian cho server!", "error")
                        with count_lock:
                            failed_count += 1
                        return False

            # Tao API client cho server nay
            api = GoogleFlowAPI(
                bearer_token=bearer_token,
                project_id=flow_project_id,
                timeout=flow_timeout,
                verbose=False,
                proxy_api_token='',
                use_proxy=True,
                local_server_url=server.url,
            )

            ref_info = f" +{len(image_inputs)}ref" if image_inputs else ""
            self._log(f"  [{idx+1}/{len(prompts)}] {pid} → {server.name} (q={server.queue_size}){ref_info}")

            try:
                ok, images, error = api.generate_images(
                    prompt=ptxt,
                    count=1,
                    aspect_ratio=aspect_ratio,
                    image_inputs=image_inputs if image_inputs else None,
                )

                if ok and images:
                    # Luu anh - images[0] la GeneratedImage object
                    gen_img = images[0]
                    saved = False
                    if hasattr(gen_img, 'base64_data') and gen_img.base64_data:
                        import base64
                        with open(opath, 'wb') as f:
                            f.write(base64.b64decode(gen_img.base64_data))
                        saved = True
                    elif hasattr(gen_img, 'url') and gen_img.url:
                        import requests as req
                        try:
                            r = req.get(gen_img.url, timeout=30)
                            if r.status_code == 200:
                                with open(opath, 'wb') as f:
                                    f.write(r.content)
                                saved = True
                        except Exception as dl_err:
                            self._log(f"  [{idx+1}] {pid} Download error: {dl_err}", "warn")
                    elif isinstance(gen_img, bytes):
                        with open(opath, 'wb') as f:
                            f.write(gen_img)
                        saved = True
                    elif isinstance(gen_img, str):
                        import base64
                        with open(opath, 'wb') as f:
                            f.write(base64.b64decode(gen_img))
                        saved = True

                    if not saved:
                        self._log(f"  [{idx+1}] {pid} [FAIL] Khong luu duoc anh!", "error")
                        with count_lock:
                            failed_count += 1
                        return False

                    pool.mark_success(server)
                    # Lay media_name tu response (de luu vao Excel cho I2V)
                    _media_name = getattr(gen_img, 'media_name', None) if gen_img else None
                    self._log(f"  [{idx+1}] {pid} [OK] → {opath.name} ({server.name})" +
                              (f" media_id={_media_name[:40]}..." if _media_name else ""))

                    # v1.0.527: RELOAD Excel moi lan save - tranh ghi de data tu Chrome 2
                    with excel_lock:
                        _saved_ok = False
                        for _sa in range(10):
                            try:
                                _wb = PromptWorkbook(excel_path)
                                _wb.load_or_create()
                                if pid.startswith('nv') or pid.startswith('loc'):
                                    _wb.update_character(pid, status="done", image_file=str(opath),
                                                         media_id=_media_name)
                                elif not pid.startswith('thumb'):
                                    _wb.update_scene(int(pid), status_img="done", img_path=str(opath),
                                                     media_id=_media_name)
                                if _wb.safe_save():
                                    _saved_ok = True
                                    break
                                else:
                                    _wb.save()
                                    _saved_ok = True
                                    break
                            except Exception as e:
                                if _sa < 9:
                                    self._log(f"  [{idx+1}] Excel save retry {_sa+1}/10: {e}", "warn")
                                    time.sleep(3)
                                else:
                                    self._log(f"  [{idx+1}] Excel save FAILED after 10 attempts: {e}", "warn")
                        # Pending write fallback
                        if not _saved_ok:
                            try:
                                if pid.startswith('nv') or pid.startswith('loc'):
                                    _wb._save_pending_write('character', char_id=pid,
                                                            media_id=_media_name, status="done")
                                elif not pid.startswith('thumb'):
                                    _wb._save_pending_write('scene', scene_id=pid,
                                                            img_path=str(opath), status_img="done",
                                                            media_id=_media_name)
                                self._log(f"  [{idx+1}] [PENDING] Saved pending write for {pid}")
                            except:
                                pass
                        # Log media_id for reference tracking
                        if _media_name and (pid.startswith('nv') or pid.startswith('loc')):
                            self._log(f"  [{idx+1}] [REF] {pid} media_id saved")

                    with count_lock:
                        success_count += 1
                    return True
                else:
                    # v1.0.530: Phan loai loi chinh xac hon
                    _err_str = str(error).lower()
                    # v1.0.537: Refs (nv/loc) la idempotent → AN TOAN retry khi TIMEOUT
                    _is_ref_prompt = pid.startswith('nv') or pid.startswith('loc')
                    # Connection error = CHAC CHAN khong gui duoc (server chet)
                    _is_connect_err = any(k in _err_str for k in [
                        'proxy network error', 'connection refused', 'connection error',
                    ])
                    # Submit timeout = KHONG BIET da gui duoc chua → KHONG retry (tranh duplicate)
                    _is_timeout = 'proxy request timeout' in _err_str
                    # v1.0.549: Token expired (401) → refresh token roi retry
                    _is_401 = '401' in _err_str or 'authentication' in _err_str or 'unauthenticated' in _err_str

                    if _is_401:
                        # v1.0.626: Token het han → refresh bang DrissionPage (khong dung PyAutoGUI)
                        pool.mark_task_failed(server, str(error))
                        self._log(f"  [{idx+1}] {pid} [401] Token het han! Dang lay token moi bang DrissionPage...", "warn")
                        try:
                            new_token = self._extract_token_via_drission(excel_path)
                            if new_token:
                                bearer_token = new_token
                                self._log(f"  [{idx+1}] {pid} [401] Token moi OK - retry...")
                                # Retry voi token moi tren server khac
                                server3 = pool.pick_best_server()
                                if not server3:
                                    server3 = pool.wait_for_server(max_wait=120)
                                if server3:
                                    api3 = GoogleFlowAPI(
                                        bearer_token=new_token, project_id=flow_project_id,
                                        timeout=flow_timeout, verbose=False,
                                        proxy_api_token='', use_proxy=True,
                                        local_server_url=server3.url,
                                    )
                                    ok3, images3, err3 = api3.generate_images(
                                        prompt=ptxt, count=1, aspect_ratio=aspect_ratio,
                                        image_inputs=image_inputs if image_inputs else None
                                    )
                                    if ok3 and images3:
                                        gen_img3 = images3[0]
                                        saved3 = False
                                        if hasattr(gen_img3, 'base64_data') and gen_img3.base64_data:
                                            import base64
                                            with open(opath, 'wb') as f:
                                                f.write(base64.b64decode(gen_img3.base64_data))
                                            saved3 = True
                                        elif hasattr(gen_img3, 'url') and gen_img3.url:
                                            import requests as req
                                            try:
                                                r = req.get(gen_img3.url, timeout=30)
                                                if r.status_code == 200:
                                                    with open(opath, 'wb') as f:
                                                        f.write(r.content)
                                                    saved3 = True
                                            except:
                                                pass
                                        if saved3:
                                            pool.mark_success(server3)
                                            _media_name3 = getattr(gen_img3, 'media_name', None)
                                            self._log(f"  [{idx+1}] {pid} [OK] 401→refresh→retry OK → {opath.name}")
                                            with excel_lock:
                                                try:
                                                    _wb3 = PromptWorkbook(excel_path)
                                                    _wb3.load_or_create()
                                                    if pid.startswith('nv') or pid.startswith('loc'):
                                                        _wb3.update_character(pid, status="done", image_file=str(opath),
                                                                              media_id=_media_name3)
                                                    elif not pid.startswith('thumb'):
                                                        _wb3.update_scene(int(pid), status_img="done", img_path=str(opath),
                                                                          media_id=_media_name3)
                                                    _wb3.safe_save() or _wb3.save()
                                                except:
                                                    pass
                                            with count_lock:
                                                success_count += 1
                                            return True
                                    else:
                                        pool.mark_task_failed(server3, str(err3))
                                        self._log(f"  [{idx+1}] {pid} [401 RETRY FAIL] {err3}")
                            else:
                                self._log(f"  [{idx+1}] {pid} [401] Khong refresh duoc token!", "error")
                        except Exception as e401:
                            self._log(f"  [{idx+1}] {pid} [401] Refresh error: {e401}", "error")
                        # Fall through - de retry logic ben duoi xu ly tiep
                    elif _is_timeout:
                        pool.mark_task_failed(server, str(error))
                        # v1.0.541: Track timeout de deprioritize server cham
                        server.recent_timeout_count = getattr(server, 'recent_timeout_count', 0) + 1
                        # v1.0.537: Refs idempotent → retry, Scenes → KHONG retry (tranh duplicate)
                        if _is_ref_prompt:
                            self._log(f"  [{idx+1}] {pid} [TIMEOUT] {server.name}: Ref timeout → retry server khac (ref idempotent)")
                            # Fall through to retry logic below (same as CONNECT/TASK error)
                        elif _is_server_mode:
                            # v1.0.548: Server mode - KHONG bo cuoc, de cycle sau retry
                            self._log(f"  [{idx+1}] {pid} [TIMEOUT] {server.name}: Submit timeout - DOI CYCLE SAU retry (server mode)")
                            # Khong tang failed_count - prompt se duoc retry o vong scan tiep theo
                            return False
                        else:
                            self._log(f"  [{idx+1}] {pid} [TIMEOUT] {server.name}: Submit timeout - KHONG retry (tranh duplicate)")
                            # KHONG retry - task co the da nam trong queue server
                            with count_lock:
                                failed_count += 1
                            return False
                    elif _is_connect_err:
                        pool.mark_submit_failed(server, str(error))
                        self._log(f"  [{idx+1}] {pid} [CONNECT FAIL] {server.name}: {error}")
                    else:
                        pool.mark_task_failed(server, str(error))
                        self._log(f"  [{idx+1}] {pid} [TASK FAIL] {server.name}: {error}")

                    # v1.0.550: Server mode retry nhieu hon + cho phep retry cung server
                    _tried_urls = {server.url}  # Da thu server nay roi
                    # v1.0.550: Task failed (khong phai timeout) → cho retry cung server (sau delay)
                    _is_task_failed = not _is_timeout  # ContextLostError, 403, etc
                    if _is_ref_prompt:
                        _max_retries = len(pool.servers)  # Refs thu tat ca servers
                    elif _is_server_mode:
                        _max_retries = max(len(pool.servers), 3)  # v1.0.548: Server mode retry nhieu hon
                    else:
                        _max_retries = 1
                    _retry_success = False

                    for _retry_i in range(_max_retries):
                        server2 = pool.pick_best_server()
                        if not server2:
                            _wait_max = 300 if _is_server_mode else 120  # v1.0.548
                            server2 = pool.wait_for_server(max_wait=_wait_max)
                        # v1.0.550: Cho phep retry cung server khi:
                        # - Refs (idempotent) ở lần cuối
                        # - Server mode + task failed (khong phai timeout) ở lần cuối
                        _allow_same = (_is_ref_prompt or (_is_server_mode and _is_task_failed)) and _retry_i == _max_retries - 1
                        if not server2:
                            break
                        if server2.url in _tried_urls and not _allow_same:
                            # Da thu server nay roi, tim server khac
                            pool.release_server(server2)
                            continue
                        if server2.url in _tried_urls:
                            self._log(f"  [{idx+1}] {pid} [RETRY {_retry_i+1}] Doi 10s truoc khi retry cung server...")
                            time.sleep(10)
                        self._log(f"  [{idx+1}] {pid} [RETRY {_retry_i+1}/{_max_retries}] → {server2.name}")
                        _tried_urls.add(server2.url)
                        api2 = GoogleFlowAPI(
                            bearer_token=bearer_token, project_id=flow_project_id,
                            timeout=flow_timeout, verbose=False,
                            proxy_api_token='', use_proxy=True,
                            local_server_url=server2.url,
                        )
                        ok2, images2, err2 = api2.generate_images(prompt=ptxt, count=1, aspect_ratio=aspect_ratio, image_inputs=image_inputs if image_inputs else None)
                        if not ok2 or not images2:
                            pool.mark_task_failed(server2, str(err2))  # release + track
                            self._log(f"  [{idx+1}] {pid} [RETRY FAIL] {server2.name}: {err2}")
                            continue  # v1.0.542: Thu server tiep theo trong loop
                        if ok2 and images2:
                            gen_img2 = images2[0]
                            saved2 = False
                            if hasattr(gen_img2, 'base64_data') and gen_img2.base64_data:
                                import base64
                                with open(opath, 'wb') as f:
                                    f.write(base64.b64decode(gen_img2.base64_data))
                                saved2 = True
                            elif hasattr(gen_img2, 'url') and gen_img2.url:
                                import requests as req
                                try:
                                    r = req.get(gen_img2.url, timeout=30)
                                    if r.status_code == 200:
                                        with open(opath, 'wb') as f:
                                            f.write(r.content)
                                        saved2 = True
                                except:
                                    pass
                            elif isinstance(gen_img2, bytes):
                                with open(opath, 'wb') as f:
                                    f.write(gen_img2)
                                saved2 = True
                            elif isinstance(gen_img2, str):
                                import base64
                                with open(opath, 'wb') as f:
                                    f.write(base64.b64decode(gen_img2))
                                saved2 = True
                            if not saved2:
                                pool.release_server(server2)
                                with count_lock:
                                    failed_count += 1
                                return False
                            pool.mark_success(server2)
                            _media_name2 = getattr(gen_img2, 'media_name', None) if gen_img2 else None
                            self._log(f"  [{idx+1}] {pid} [OK] retry → {opath.name} ({server2.name})" +
                                      (f" media_id={_media_name2[:40]}..." if _media_name2 else ""))
                            # v1.0.527: RELOAD Excel moi lan save - tranh ghi de
                            with excel_lock:
                                _saved_ok2 = False
                                for _sa2 in range(10):
                                    try:
                                        _wb2 = PromptWorkbook(excel_path)
                                        _wb2.load_or_create()
                                        if pid.startswith('nv') or pid.startswith('loc'):
                                            _wb2.update_character(pid, status="done", image_file=str(opath),
                                                                  media_id=_media_name2)
                                        elif not pid.startswith('thumb'):
                                            _wb2.update_scene(int(pid), status_img="done", img_path=str(opath),
                                                              media_id=_media_name2)
                                        if _wb2.safe_save():
                                            _saved_ok2 = True
                                            break
                                        else:
                                            _wb2.save()
                                            _saved_ok2 = True
                                            break
                                    except Exception as e:
                                        if _sa2 < 9:
                                            self._log(f"  [{idx+1}] Excel retry save {_sa2+1}/10: {e}", "warn")
                                            time.sleep(3)
                                        else:
                                            self._log(f"  [{idx+1}] Excel retry FAILED: {e}", "warn")
                                if not _saved_ok2:
                                    try:
                                        _wb2 = PromptWorkbook(excel_path)
                                        _wb2.load_or_create()
                                        if pid.startswith('nv') or pid.startswith('loc'):
                                            _wb2._save_pending_write('character', char_id=pid,
                                                                     media_id=_media_name2, status="done")
                                        elif not pid.startswith('thumb'):
                                            _wb2._save_pending_write('scene', scene_id=pid,
                                                                     img_path=str(opath), status_img="done",
                                                                     media_id=_media_name2)
                                        self._log(f"  [{idx+1}] [PENDING] Saved pending write for {pid}")
                                    except:
                                        pass
                            with count_lock:
                                success_count += 1
                            return True

                    if not _is_server_mode:
                        with count_lock:
                            failed_count += 1
                    else:
                        # v1.0.548: Server mode - khong tang failed, de cycle sau retry
                        self._log(f"  [{idx+1}] {pid} [RETRY EXHAUSTED] Server mode - prompt se duoc retry cycle sau")
                    return False

            except Exception as e:
                # v1.0.528: Exception = thuong la connection error
                pool.mark_submit_failed(server, str(e))
                self._log(f"  [{idx+1}] {pid} [ERROR] {server.name}: {e}", "error")
                if not _is_server_mode:
                    with count_lock:
                        failed_count += 1
                else:
                    self._log(f"  [{idx+1}] {pid} [ERROR] Server mode - prompt se duoc retry cycle sau")
                return False

        # Chay song song - so worker = so server kha dung
        max_workers = min(num_servers, len(pending_prompts))
        self._log(f"[START] {len(pending_prompts)} prompts x {max_workers} workers")

        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = {executor.submit(_process_one, p): p for p in pending_prompts}
            for future in as_completed(futures):
                try:
                    future.result()
                except Exception as e:
                    self._log(f"[ERROR] Thread exception: {e}", "error")

        # Log stats
        pool_stats = pool.get_stats()
        for s in pool_stats.get('servers', []):
            self._log(f"  Server {s['name']}: {s['completed']} OK, {s['failed']} fail")

        self._log(f"[LOCAL SERVER] Xong: {success_count} OK, {failed_count} fail, {skipped_count} skip")
        return {
            "success": True,
            "total": len(prompts),
            "generated": success_count,
            "failed": failed_count,
            # v1.0.514: "stats" dong bo voi drission mode (success = TAO MOI, skipped = rieng)
            "stats": {
                "success": success_count,
                "failed": failed_count,
                "skipped": skipped_count,
            },
        }

    def generate_from_prompts_api(
        self,
        prompts: List[Dict],
        excel_path: Optional[Path] = None,
        bearer_token: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Tao anh tu danh sach prompts bang API mode.

        Args:
            prompts: List prompts [{'id': '1', 'prompt': '...'}]
            excel_path: Duong dan Excel (de cap nhat status)
            bearer_token: Bearer token (bat buoc)

        Returns:
            Dict voi ket qua
        """
        self._log("=" * 60)
        self._log("API MODE - TAO ANH TU PROMPTS")
        self._log("=" * 60)

        # === PARALLEL CHROME: Filter chẵn/lẻ TRƯỚC KHI chọn mode ===
        worker_id = getattr(self, 'worker_id', 0) or 0
        total_workers = getattr(self, 'total_workers', 1) or 1

        if total_workers <= 1:
            parallel_chrome = os.environ.get('PARALLEL_CHROME', '') or str(self.config.get('parallel_chrome', ''))
            if parallel_chrome and '/' in parallel_chrome:
                try:
                    parts = parallel_chrome.split('/')
                    worker_id = int(parts[0])
                    total_workers = int(parts[1])
                except:
                    pass

        if total_workers > 1:
            all_are_references = all(
                str(p.get('id', '')).lower().startswith('nv') or str(p.get('id', '')).lower().startswith('loc')
                for p in prompts
            ) if prompts else False

            if not all_are_references:
                filtered = []
                for i, prompt_data in enumerate(prompts):
                    pid = str(prompt_data.get('id', i + 1))
                    is_ref = pid.lower().startswith('nv') or pid.lower().startswith('loc')
                    if is_ref:
                        if worker_id == 0:
                            filtered.append(prompt_data)
                    else:
                        try:
                            scene_num = int(pid)
                            if (scene_num % total_workers) == (worker_id % total_workers):
                                filtered.append(prompt_data)
                        except ValueError:
                            filtered.append(prompt_data)

                self._log(f"[PARALLEL] Chrome {worker_id}/{total_workers} - {len(filtered)}/{len(prompts)} prompts")
                prompts = filtered

        # === LOCAL SERVER MODE: Dùng GoogleFlowAPI thay DrissionPage ===
        local_server_enabled = self.config.get('local_server_enabled', False)
        local_server_url = self.config.get('local_server_url', '')
        if local_server_enabled and local_server_url:
            self._log(f"[LOCAL SERVER] Gui anh qua server: {local_server_url}")
            return self._generate_via_local_server(prompts, excel_path, bearer_token)

        # === DRISSION MODE ONLY ===
        # Sử dụng DrissionPage + Interceptor để tạo ảnh
        # Cho phép prompts=[] nếu có video_count > 0 (chỉ tạo video, không tạo ảnh)
        video_count_setting = self.config.get('video_count', 0)
        try:
            video_count = 999999 if video_count_setting == 'full' else int(video_count_setting)
        except:
            video_count = 0

        if not prompts and video_count <= 0:
            return {"success": False, "error": "Khong co prompts va video_count=0",
                    "stats": {"success": 0, "failed": 0, "skipped": 0}}

        try:
            from modules.drission_flow_api import DrissionFlowAPI
        except ImportError as e:
            return {"success": False, "stats": {"success": 0, "failed": len(prompts), "skipped": 0},
                    "error": f"Khong import duoc DrissionFlowAPI: {e}. Cài đặt: pip install DrissionPage"}

        # Webshare proxy config (nested dict from GUI settings)
        # IPv6 proxy đã bị bỏ - chỉ dùng Webshare
        webshare_cfg = self.config.get('webshare_proxy', {})
        webshare_api_key = webshare_cfg.get('api_key', '')
        webshare_proxy_file = webshare_cfg.get('proxy_file', 'config/proxies.txt')  # Default file
        use_webshare = webshare_cfg.get('enabled', True)  # Default ON - Webshare enabled by default

        # === PROXY MODE CONFIG ===
        # "direct" = Direct Proxy List (100 IP cố định)
        # "rotating" = Rotating Residential (IP tự động đổi)
        proxy_mode = webshare_cfg.get('proxy_mode', 'direct')
        rotating_host = webshare_cfg.get('rotating_host', 'p.webshare.io')
        rotating_port = webshare_cfg.get('rotating_port', 80)
        # Ưu tiên base_username, fallback sang username cũ
        rotating_username = webshare_cfg.get('rotating_base_username') or webshare_cfg.get('rotating_username', 'jhvbehdf-residential')
        rotating_password = webshare_cfg.get('rotating_password', 'cf1bi3yvq0t1')
        machine_id = webshare_cfg.get('machine_id', 1)  # Máy số mấy (1-99)

        # Khởi tạo Webshare Proxy Manager nếu enabled
        if use_webshare:

            try:
                from webshare_proxy import init_proxy_manager, get_proxy_manager

                # === ROTATING RESIDENTIAL MODE ===
                if proxy_mode == "rotating":
                    self._log(f"[GLOBE] ROTATING RESIDENTIAL mode")
                    self._log(f"   → {rotating_host}:{rotating_port}")
                    self._log(f"   → User: {rotating_username}")
                    self._log(f"   → Mỗi request sẽ tự động đổi IP!")

                    manager = init_proxy_manager(
                        username=rotating_username,
                        password=rotating_password,
                        rotating_endpoint=True,
                        rotating_host=rotating_host,
                        rotating_port=rotating_port
                    )
                else:
                    # === DIRECT PROXY LIST MODE ===
                    # Load proxy list từ file hoặc API
                    if webshare_proxy_file:
                        self._log(f"Loading proxies from: {webshare_proxy_file}")
                    else:
                        # Kiểm tra file mặc định
                        default_proxy_file = Path("config/proxies.txt")
                        if default_proxy_file.exists():
                            webshare_proxy_file = str(default_proxy_file)
                            self._log(f"Found default proxy file: {webshare_proxy_file}")

                    manager = init_proxy_manager(
                        api_key=webshare_api_key,
                        proxy_file=webshare_proxy_file if webshare_proxy_file else None
                    )

                # Verify initialization
                if manager.is_rotating_mode():
                    self._log(f"[v] Rotating Endpoint ready")
                elif manager.proxies:
                    self._log(f"[v] Loaded {len(manager.proxies)} proxies")
                    self._log(f"  Current: {manager.current_proxy.endpoint}")
                else:
                    self._log("[WARN] No proxies loaded - chạy không có proxy", "WARN")
                    use_webshare = False

            except Exception as e:
                self._log(f"[WARN] Webshare init error: {e} - chạy không có proxy", "WARN")
                use_webshare = False

        # === ĐỌC CONFIG TỪ EXCEL/CACHE TRƯỚC (để biết profile nào đã dùng) ===
        saved_project_url = None
        saved_chrome_profile = None  # Chrome profile đã dùng cho dự án này
        self._log(f"[DEBUG] excel_path = {excel_path}")

        # 1. Thử đọc từ Excel sheet 'config'
        if excel_path and Path(excel_path).exists():
            self._log(f"[DEBUG] Excel tồn tại, kiểm tra sheet 'config'...")
            try:
                import openpyxl
                wb = openpyxl.load_workbook(excel_path)
                self._log(f"[DEBUG] Sheets có: {wb.sheetnames}")
                if 'config' in wb.sheetnames:
                    ws = wb['config']
                    config_keys_found = []

                    # DEBUG: Print first few rows to see actual data
                    self._log(f"[DEBUG] Config sheet max_row={ws.max_row}, max_col={ws.max_column}")
                    for row_idx in range(1, min(6, ws.max_row + 1)):
                        row_vals = [ws.cell(row=row_idx, column=c).value for c in range(1, min(5, ws.max_column + 1))]
                        self._log(f"[DEBUG] Row {row_idx}: {row_vals}")

                    # Scan all columns to find key-value pairs (not just A,B)
                    for row in ws.iter_rows(min_row=1, max_row=30, values_only=True):
                        if not row:
                            continue

                        # Try to find project_url anywhere in the row
                        for i, cell_val in enumerate(row):
                            if cell_val and isinstance(cell_val, str):
                                cell_str = str(cell_val).strip()
                                # Direct URL match
                                if '/project/' in cell_str and cell_str.startswith('http'):
                                    saved_project_url = cell_str
                                    self._log(f"[FOUND] Tìm thấy project URL: {saved_project_url[:60]}...")
                                    break

                        if saved_project_url:
                            break

                        # Standard key-value format (column A = key, column B = value)
                        if len(row) >= 2 and row[0]:
                            key = str(row[0] or '').strip().lower()
                            val = str(row[1] or '').strip() if row[1] else ''
                            config_keys_found.append(key)
                            if key == 'flow_project_url' and val and '/project/' in val:
                                saved_project_url = val
                                self._log(f"[FOUND] Tìm thấy project URL từ Excel: {saved_project_url[:50]}...")
                            elif key == 'flow_project_id' and val and not saved_project_url:
                                # Nếu chỉ có project_id, tạo URL
                                saved_project_url = f"https://labs.google/fx/vi/tools/flow/project/{val}"
                                self._log(f"[FOUND] Tìm thấy project_id từ Excel: {val[:20]}...")
                            elif key == 'chrome_profile_path' and val:
                                # Đọc Chrome profile đã dùng cho dự án này
                                if Path(val).exists():
                                    saved_chrome_profile = val
                                    self._log(f"[FOUND] Tìm thấy Chrome profile từ Excel: {val}")

                    if not saved_project_url:
                        self._log(f"[DEBUG] Config keys: {config_keys_found}")
                else:
                    self._log(f"[DEBUG] Không có sheet 'config' trong Excel")
                wb.close()
            except Exception as e:
                self._log(f"[WARN] Không đọc được config từ Excel: {e}", "warn")

        # Chọn profile: ưu tiên saved profile từ Excel, fallback về default
        if saved_chrome_profile:
            profile_to_use = saved_chrome_profile
            self._log(f"[SYNC] Dùng Chrome profile đã lưu: {profile_to_use}")
        else:
            profile_to_use = self._get_profile_path() or "./chrome_profile"
            self._log(f"[DIR] Dùng Chrome profile mặc định: {profile_to_use}")

        # Đọc setting headless từ config (default: True = chạy ẩn)
        # Dùng chung setting 'browser_headless' với Selenium mode
        drission_headless = self.config.get('browser_headless', True)

        # Chrome portable - ưu tiên cao nhất
        chrome_portable = self.config.get('chrome_portable', '')

        drission_api = DrissionFlowAPI(
            profile_dir=profile_to_use,
            verbose=self.verbose,
            log_callback=self._log,
            webshare_enabled=use_webshare,
            worker_id=self.worker_id,  # Parallel mode - mỗi worker có proxy riêng
            total_workers=self.total_workers,  # Chia màn hình theo số workers
            headless=drission_headless,  # Chạy Chrome ẩn (default: True)
            machine_id=machine_id,  # Máy số mấy - tránh trùng session giữa các máy
            chrome_portable=chrome_portable  # Chrome portable đã đăng nhập
        )

        self._log("[START] DrissionPage + Interceptor")
        if use_webshare:
            manager = get_proxy_manager()
            if manager.is_rotating_mode():
                self._log(f"   Proxy: [SYNC] ROTATING ENDPOINT (auto IP change)")
            else:
                self._log(f"   Proxy: Webshare Pool ({len(manager.proxies)} proxies)")
        else:
            self._log("   Proxy: OFF (không có proxy)")

        # 2. Fallback: Thử đọc từ cache file
        if not saved_project_url and excel_path:
            cache_path = Path(excel_path).parent / ".media_cache.json"
            self._log(f"[DEBUG] Cache path: {cache_path}")
            if cache_path.exists():
                try:
                    import json
                    with open(cache_path, 'r', encoding='utf-8') as f:
                        cache_data = json.load(f)
                    self._log(f"[DEBUG] Cache keys: {list(cache_data.keys())[:10]}")
                    cached_url = cache_data.get('_project_url', '')
                    cached_id = cache_data.get('_project_id', '')
                    if cached_url and '/project/' in cached_url:
                        saved_project_url = cached_url
                        self._log(f"[FOUND] Tìm thấy project URL từ cache: {saved_project_url[:50]}...")
                    elif cached_id:
                        saved_project_url = f"https://labs.google/fx/vi/tools/flow/project/{cached_id}"
                        self._log(f"[FOUND] Tìm thấy project_id từ cache: {cached_id[:20]}...")
                except Exception as e:
                    self._log(f"[WARN] Không đọc được cache: {e}", "warn")
            else:
                self._log(f"[DEBUG] Cache file không tồn tại")

        if saved_project_url:
            self._log(f"[SYNC] Sẽ vào lại project cũ để giữ media_id...")
        else:
            self._log(f"[NOTE] Sẽ tạo project mới...")

        # Setup Chrome và đợi user chọn project (với retry + IP rotation)
        MAX_SETUP_RETRIES = 3
        setup_success = False

        for setup_attempt in range(MAX_SETUP_RETRIES):
            if drission_api.setup(project_url=saved_project_url):
                setup_success = True
                # CHỈ reset về Pro khi đây là project MỚI (không có saved_project_url)
                # Nếu đang retry cùng project thì giữ nguyên model (có thể đang dùng fallback)
                if not saved_project_url:
                    drission_api.reset_to_pro_model()

                # === LƯU PROJECT URL VÀO EXCEL NGAY SAU KHI SETUP ===
                # Đảm bảo 1 voice = 1 project link (không bị mất nếu fail giữa chừng)
                new_project_url = getattr(drission_api, '_current_project_url', '')
                if new_project_url and '/project/' in new_project_url and excel_path:
                    try:
                        import openpyxl
                        wb = openpyxl.load_workbook(excel_path)
                        if 'config' not in wb.sheetnames:
                            ws = wb.create_sheet('config')
                            # Thêm header row
                            ws['A1'] = 'key'
                            ws['B1'] = 'value'
                        else:
                            ws = wb['config']
                            # Đảm bảo có header
                            if not ws['A1'].value:
                                ws['A1'] = 'key'
                                ws['B1'] = 'value'

                        # Tìm hoặc thêm row cho flow_project_url
                        found = False
                        for row_num in range(2, min(ws.max_row + 1, 50)):  # Giới hạn 50 rows
                            cell_val = ws.cell(row=row_num, column=1).value
                            if cell_val and str(cell_val).strip().lower() == 'flow_project_url':
                                ws.cell(row=row_num, column=2, value=new_project_url)
                                found = True
                                break
                        if not found:
                            # Tìm row trống đầu tiên (sau header)
                            next_row = 2
                            for r in range(2, 50):
                                if not ws.cell(row=r, column=1).value:
                                    next_row = r
                                    break
                                next_row = r + 1
                            ws.cell(row=next_row, column=1, value='flow_project_url')
                            ws.cell(row=next_row, column=2, value=new_project_url)

                        # Cũng lưu chrome_profile_path
                        profile_path = str(drission_api.profile_dir) if hasattr(drission_api, 'profile_dir') else ''
                        if profile_path:
                            found = False
                            for row_num in range(2, min(ws.max_row + 1, 50)):
                                cell_val = ws.cell(row=row_num, column=1).value
                                if cell_val and str(cell_val).strip().lower() == 'chrome_profile_path':
                                    ws.cell(row=row_num, column=2, value=profile_path)
                                    found = True
                                    break
                            if not found:
                                # Tìm row trống đầu tiên
                                next_row = 2
                                for r in range(2, 50):
                                    if not ws.cell(row=r, column=1).value:
                                        next_row = r
                                        break
                                    next_row = r + 1
                                ws.cell(row=next_row, column=1, value='chrome_profile_path')
                                ws.cell(row=next_row, column=2, value=profile_path)

                        wb.save(excel_path)
                        wb.close()
                        self._log(f"[v] Lưu project URL vào Excel: {new_project_url[:50]}...")
                    except Exception as e:
                        self._log(f"[WARN] Không lưu được project URL: {e}", "warn")

                break
            else:
                self._log(f"[FAIL] Setup failed (attempt {setup_attempt + 1}/{MAX_SETUP_RETRIES})", "error")

                if setup_attempt < MAX_SETUP_RETRIES - 1:
                    # Rotate IP và restart Chrome
                    self._log("[SYNC] Đang rotate IP và restart Chrome...", "warn")
                    if use_webshare:
                        try:
                            manager = get_proxy_manager()
                            success, msg = manager.rotate_worker_proxy(self.worker_id, "setup_timeout")
                            self._log(f"   → {msg}")
                            if success and drission_api.restart_chrome():
                                self._log("[v] Chrome restarted với IP mới")
                                import time
                                time.sleep(3)
                                continue
                        except Exception as e:
                            self._log(f"   → Rotate error: {e}", "warn")

        if not setup_success:
            self._log("[FAIL] DrissionFlowAPI setup failed sau tất cả retries!", "error")
            return {"success": False, "error": "DrissionFlowAPI setup failed",
                    "stats": {"success": 0, "failed": len(prompts), "skipped": 0}}

        self._log(f"Tong: {len(prompts)} prompts")

        # Store reference
        self._drission_api = drission_api

        # Đăng ký với ChromeManager để giám sát
        try:
            from modules.chrome_manager import get_chrome_manager
            chrome_mgr = get_chrome_manager()
            chrome_mgr.set_log_callback(self._log)
            chrome_mgr.register_chrome(
                worker_id=self.worker_id,
                drission_api=drission_api,
                project_url=saved_project_url or getattr(drission_api, '_current_project_url', '')
            )
            self._log(f"[Manager] Đã đăng ký Chrome {self.worker_id} với ChromeManager")
        except Exception as e:
            self._log(f"[Manager] Không đăng ký được với ChromeManager: {e}", "warn")

        # Generate images - use self.img_path as output directory
        return self._generate_images_drission_mode(prompts, self.img_path, excel_path)

    def _generate_images_drission_mode(
        self,
        prompts: List[Dict[str, Any]],
        output_dir: Path,
        excel_path: Optional[Path] = None
    ) -> Dict[str, Any]:
        """
        Generate images sử dụng DrissionFlowAPI (DrissionPage + Interceptor).

        Flow:
        1. DrissionFlowAPI đã setup sẵn Chrome + interceptor
        2. Mỗi prompt: gửi qua Chrome để capture tokens → gọi API → save ảnh
        3. Lưu vào output_dir và cập nhật Excel nếu có

        Args:
            prompts: Danh sách prompts
            output_dir: Thư mục lưu ảnh
            excel_path: Path đến Excel file (optional)

        Returns:
            Dict với stats
        """
        from modules.excel_manager import PromptWorkbook

        self._log("=" * 60)
        self._log("DRISSION MODE - Generate Images")
        self._log("=" * 60)

        drission_api = getattr(self, '_drission_api', None)
        if not drission_api:
            return {"success": False, "error": "DrissionFlowAPI chưa được khởi tạo",
                    "stats": {"success": 0, "failed": len(prompts), "skipped": 0}}

        # Reset stats
        self.stats = {"total": len(prompts), "success": 0, "failed": 0, "skipped": 0}
        _token_saved_to_excel = False  # v1.0.523: Flag de chi save token 1 lan

        # Track failed prompts để retry sau
        failed_prompts = []  # List[Tuple[prompt_data, index, error]]

        # v1.0.216: Hardcode empty - không đọc từ config
        # Giữ nguyên model Chrome chọn, không can thiệp
        force_model = ""
        try:
            worker_id = f"chrome_{self.worker_id + 1}"  # chrome_1 hoặc chrome_2
            model_file = Path(__file__).parent.parent / ".agent" / "status" / f"{worker_id}_model.txt"
            if model_file.exists():
                file_content = model_file.read_text().strip()
                if file_content:
                    # Nếu là số (index), chọn model trên giao diện
                    if file_content.isdigit():
                        model_index = int(file_content)
                        self._log(f"[MODEL] Chuyển sang model index {model_index}...")
                        if hasattr(self.api, 'select_model_by_index'):
                            self.api.select_model_by_index(model_index)
                        force_model = ""  # Không cần force trong API, Chrome đã chọn
                    else:
                        # Nếu là tên model, dùng force_model như cũ
                        force_model = file_content
                        self._log(f"[MODEL] Đọc từ file: {force_model}")
        except Exception as e:
            self._log(f"[MODEL] Error reading model file: {e}", "warn")
        if force_model:
            self._log(f"[MODEL] Force model: {force_model}")

        # Load Excel workbook
        workbook = None
        if excel_path and Path(excel_path).exists():
            try:
                workbook = PromptWorkbook(excel_path)
                workbook.load_or_create()
            except Exception as e:
                self._log(f"Warning: Khong load duoc Excel: {e}", "warn")

        # === LOAD MEDIA_IDs từ Excel (thay vì cache file) ===
        excel_media_ids = {}
        if workbook:
            try:
                excel_media_ids = workbook.get_media_ids()
                if excel_media_ids:
                    self._log(f"[EXCEL] Loaded {len(excel_media_ids)} media_ids: {list(excel_media_ids.keys())}")
                else:
                    self._log("[EXCEL] [WARN] KHÔNG CÓ MEDIA_ID TRONG EXCEL - ảnh nv/loc sẽ được tạo lại", "warn")
            except Exception as e:
                self._log(f"Warning: Cannot load media_ids from Excel: {e}", "warn")

        # v1.0.383: LUÔN load cache, merge với Excel (Excel ưu tiên)
        # Trước: chỉ load cache khi Excel rỗng → mất media_id đã save vào cache khi Excel bị lock
        cached_media_names = self._load_media_cache()
        if cached_media_names:
            # Tìm IDs trong cache mà Excel không có
            cache_only = set(k.lower() for k in cached_media_names) - set(k.lower() for k in excel_media_ids)
            if cache_only:
                self._log(f"[CACHE] Bổ sung {len(cache_only)} media_ids từ cache: {sorted(cache_only)}")
                # v1.0.647: Sync cache → Excel - điền media_id từ cache vào Excel
                # Để Excel luôn đầy đủ dữ liệu cho các bước sau (smart_engine STOP check)
                if workbook:
                    _synced = 0
                    for _cid in sorted(cache_only):
                        # Tìm key gốc (case-sensitive) trong cache
                        _orig_key = next((k for k in cached_media_names if k.lower() == _cid), None)
                        if not _orig_key:
                            continue
                        _media_info = cached_media_names[_orig_key]
                        _media_name = _media_info.get('mediaName') if isinstance(_media_info, dict) else _media_info
                        if _media_name:
                            try:
                                if workbook.update_character(_orig_key, media_id=str(_media_name)):
                                    _synced += 1
                                    excel_media_ids[_orig_key] = str(_media_name)
                            except Exception:
                                pass
                    if _synced > 0:
                        try:
                            workbook.save()
                            self._log(f"[CACHE→EXCEL] Synced {_synced} media_ids vào Excel")
                        except Exception as _se:
                            self._log(f"[CACHE→EXCEL] Save failed: {_se}", "warn")
            else:
                self._log(f"[CACHE] Loaded {len(cached_media_names)} cached media refs (all in Excel already)")

        # Ensure output dir exists
        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)

        # Log danh sách prompts sẽ xử lý
        prompt_ids = [str(p.get('id', idx+1)) for idx, p in enumerate(prompts)]
        ref_ids = [pid for pid in prompt_ids if pid.lower().startswith('nv') or pid.lower().startswith('loc')]
        if ref_ids:
            self._log(f"[INFO] Reference images (nv/loc): {ref_ids}")

        # === PARALLEL CHROME: chia scene cho nhiều Chrome ===
        # Ưu tiên 1: self.worker_id và self.total_workers (từ constructor - Chrome 2 mode)
        # Ưu tiên 2: PARALLEL_CHROME env/config (format "1/2", "2/4", etc.)
        worker_id = getattr(self, 'worker_id', 0) or 0
        total_workers = getattr(self, 'total_workers', 1) or 1

        # Fallback to PARALLEL_CHROME env/config nếu chưa set từ constructor
        if total_workers <= 1:
            parallel_chrome = os.environ.get('PARALLEL_CHROME', '') or str(self.config.get('parallel_chrome', ''))
            if parallel_chrome and '/' in parallel_chrome:
                try:
                    parts = parallel_chrome.split('/')
                    worker_id = int(parts[0])
                    total_workers = int(parts[1])
                except:
                    pass

        # === CHECK NẾU ĐANG TẠO REFERENCES RIÊNG LẺ ===
        # Nếu TẤT CẢ prompts là references (nv/loc) → không áp dụng parallel skip
        # Vì step 1 cần tạo TẤT CẢ references trước khi step 2 tạo scenes
        all_are_references = all(
            str(p.get('id', '')).lower().startswith('nv') or str(p.get('id', '')).lower().startswith('loc')
            for p in prompts
        ) if prompts else False

        if total_workers > 1:
            if all_are_references:
                self._log(f"[PARALLEL] Chrome {worker_id}/{total_workers} - TẠO REFERENCES: xử lý TẤT CẢ (không skip)")
            else:
                self._log(f"[PARALLEL] Chrome {worker_id}/{total_workers} - Worker sẽ xử lý scenes theo modulo")

        for i, prompt_data in enumerate(prompts):
            pid = str(prompt_data.get('id', i + 1))
            prompt = prompt_data.get('prompt', '')

            if not prompt:
                self._log(f"[{i+1}/{len(prompts)}] ID: {pid} - Skip (prompt rong)", "warn")
                self.stats["skipped"] += 1
                continue

            # Skip DO_NOT_GENERATE markers
            if prompt.strip().upper() == "DO_NOT_GENERATE":
                self._log(f"[{i+1}/{len(prompts)}] ID: {pid} - Skip (DO_NOT_GENERATE)")
                self.stats["skipped"] += 1
                continue

            # === PARALLEL: Skip nếu không thuộc worker này ===
            # QUAN TRỌNG: Không áp dụng parallel skip khi đang tạo references riêng lẻ
            if total_workers > 1 and not all_are_references:
                is_ref = pid.lower().startswith('nv') or pid.lower().startswith('loc')

                if is_ref:
                    # Reference images: chỉ worker 1 xử lý (khi chạy cùng scenes)
                    if worker_id != 1:
                        self.stats["skipped"] += 1
                        continue
                else:
                    # Scene số: chia theo modulo
                    try:
                        scene_num = int(pid)
                        # scene_num % total_workers == (worker_id - 1)
                        # VD: worker 1/4 làm scenes 1,5,9... (mod 4 = 1)
                        #     worker 2/4 làm scenes 2,6,10... (mod 4 = 2)
                        if (scene_num % total_workers) != (worker_id % total_workers):
                            self.stats["skipped"] += 1
                            continue
                    except ValueError:
                        pass

            # Xác định là ảnh tham chiếu (nv*/loc*) hay ảnh scene
            is_reference_image = pid.lower().startswith('nv') or pid.lower().startswith('loc')

            # === PARALLEL MODE: Reload media_ids từ Excel trước mỗi scene ===
            # Chrome 2 cần lấy media_ids mới từ Chrome 1 (đang chạy song song)
            if not is_reference_image and total_workers > 1:
                try:
                    if workbook:
                        fresh_media_ids = workbook.get_media_ids()
                        if fresh_media_ids:
                            # Chỉ log nếu có media_ids mới
                            new_ids = set(fresh_media_ids.keys()) - set(excel_media_ids.keys())
                            if new_ids:
                                self._log(f"   [PARALLEL] Reload Excel: +{len(new_ids)} media_ids mới: {list(new_ids)}")
                            excel_media_ids = fresh_media_ids
                            all_media_ids = {**cached_media_names, **excel_media_ids}
                except Exception as e:
                    self._log(f"   [PARALLEL] Lỗi reload media_ids: {e}", "warn")

            # Xác định thư mục lưu: nv*/loc* -> nv_path (tham chiếu), còn lại -> img_path
            if is_reference_image:
                save_dir = self.nv_path
            else:
                save_dir = output_dir

            # Check if image already exists
            output_file = save_dir / f"{pid}.png"
            if output_file.exists():
                # === CHECK MEDIA_ID FOR REFERENCE IMAGES ===
                # Nếu là ảnh nv*/loc* nhưng KHÔNG có media_id → xóa và tạo lại
                # Normalize key để so sánh (case-insensitive)
                pid_lower = pid.lower()
                # Check cả Excel VÀ cache (phòng trường hợp Excel bị lock không save được)
                all_check = {**cached_media_names, **excel_media_ids}
                has_media_id = any(k.lower() == pid_lower for k in all_check.keys())

                if is_reference_image and not has_media_id:
                    self._log(f"[{i+1}/{len(prompts)}] ID: {pid} - [WARN] ANH TON TAI NHUNG KHONG CO MEDIA_ID")
                    self._log(f"   → Se tao lai de lay media_id (giu file cu - overwrite neu thanh cong)")
                    # SAFETY v1.0.293: KHÔNG xóa file cũ - nếu generate thành công sẽ overwrite,
                    # nếu thất bại vẫn giữ được file gốc. Tránh mất dữ liệu khi tạo lại thất bại.
                    # Tiếp tục generate (không skip)
                else:
                    self._log(f"[{i+1}/{len(prompts)}] ID: {pid} - Skip (da co anh)")
                    self.stats["skipped"] += 1
                    continue

            self._log(f"[{i+1}/{len(prompts)}] ID: {pid}")
            self._log(f"   Prompt: {prompt[:60]}...")

            # === BUILD IMAGE_INPUTS từ reference_files và media_ids ===
            image_inputs = []
            # is_reference_image đã được định nghĩa ở trên

            # Merge Excel và cache media_ids (Excel ưu tiên)
            all_media_ids = {**cached_media_names, **excel_media_ids}

            if not is_reference_image:
                # Parse reference_files từ prompt_data
                ref_str = prompt_data.get('reference_files', '')
                ref_files = []

                self._log(f"   [REF] Raw reference_files: '{ref_str}'")

                if ref_str:
                    try:
                        parsed = json.loads(ref_str) if ref_str.startswith('[') else None
                        if isinstance(parsed, list):
                            ref_files = parsed
                        else:
                            ref_files = [f.strip() for f in str(ref_str).split(',') if f.strip()]
                    except:
                        ref_files = [f.strip() for f in str(ref_str).split(',') if f.strip()]

                # Fallback: nếu không có reference, dùng nvc.png mặc định
                if not ref_files:
                    ref_files = ["nvc.png"]
                    self._log(f"   [REF] No reference, using default nvc.png")

                self._log(f"   [REF] Parsed ref_files: {ref_files}")
                self._log(f"   [REF] Available media_ids: {list(all_media_ids.keys())}")

                # Build image_inputs từ media_ids (Excel hoặc cache)
                # QUAN TRỌNG: Dùng "imageInputType" (không phải "inputType") với giá trị đầy đủ
                # Build case-insensitive lookup dicts
                excel_media_ids_lower = {k.lower(): v for k, v in excel_media_ids.items()}
                cached_media_names_lower = {k.lower(): v for k, v in cached_media_names.items()}

                missing_refs = []
                for ref_file in ref_files:
                    ref_id = ref_file.replace('.png', '').replace('.jpg', '')
                    ref_id_lower = ref_id.lower()

                    # Thử Excel media_id trước (case-insensitive)
                    if ref_id_lower in excel_media_ids_lower:
                        media_id = excel_media_ids_lower[ref_id_lower]
                        image_inputs.append({
                            "name": media_id,
                            "imageInputType": "IMAGE_INPUT_TYPE_REFERENCE"
                        })
                        self._log(f"   [REF] [v] {ref_id} → {media_id[:30]}... (Excel)")
                    elif ref_id_lower in cached_media_names_lower:
                        # Fallback to cache (case-insensitive)
                        media_info = cached_media_names_lower[ref_id_lower]
                        media_name = media_info.get('mediaName') if isinstance(media_info, dict) else media_info
                        if media_name:
                            image_inputs.append({
                                "name": media_name,
                                "imageInputType": "IMAGE_INPUT_TYPE_REFERENCE"
                            })
                            self._log(f"   [REF] [v] {ref_id} → {media_name[:30]}... (cache)")
                    else:
                        missing_refs.append(ref_id)
                        self._log(f"   [REF] [x] {ref_id} - KHÔNG CÓ MEDIA_ID!", "warn")

                if missing_refs:
                    self._log(f"   [REF] [WARN] THIẾU {len(missing_refs)} media_id: {missing_refs}", "warn")
                    self._log(f"   [REF] → Cần tạo ảnh nv/loc trước để có media_id!", "warn")

                if image_inputs:
                    self._log(f"   [REF] Total: {len(image_inputs)}/{len(ref_files)} reference images")
                    for idx, img_inp in enumerate(image_inputs):
                        self._log(f"   [REF] #{idx+1}: {img_inp.get('name', 'N/A')[:40]}...")
                else:
                    self._log(f"   [REF] [WARN] KHÔNG CÓ REFERENCE NÀO! Ảnh sẽ tạo không có tham chiếu", "warn")

            try:
                # Generate image using DrissionFlowAPI with reference images
                # force_model: đảm bảo dùng model chất lượng cao (Nano Banana Pro)
                success, images, error = drission_api.generate_image(
                    prompt=prompt,
                    save_dir=save_dir,
                    filename=pid,
                    image_inputs=image_inputs if image_inputs else None,
                    force_model=force_model
                )

                if success and images:
                    self._log(f"   [v] Thành công! Saved {len(images)} image(s)")
                    self.stats["success"] += 1
                    consecutive_403 = 0  # Reset counter on success

                    # v1.0.523: Luu bearer token + project_id vao Excel sau anh DAU TIEN thanh cong
                    # Dung flag _token_saved de chi save 1 lan (khong phu thuoc success count)
                    if not _token_saved_to_excel and workbook and drission_api:
                        try:
                            _cur_token = getattr(drission_api, 'bearer_token', None) or ''
                            if isinstance(_cur_token, str) and _cur_token.startswith('Bearer '):
                                _cur_token = _cur_token[7:]
                            _cur_pid = getattr(drission_api, 'project_id', None) or self.config.get('flow_project_id', '')
                            _cur_url = getattr(drission_api, 'captured_url', None) or ''
                            if _cur_token:
                                workbook.set_config_value('flow_bearer_token', _cur_token)
                                self._log(f"   [TOKEN] Luu bearer token vao Excel: {_cur_token[:20]}...")
                            if _cur_pid:
                                workbook.set_config_value('flow_project_id', _cur_pid)
                            if _cur_url:
                                workbook.set_config_value('flow_project_url', _cur_url)
                            if _cur_token or _cur_pid:
                                workbook.safe_save()
                                self._log(f"   [TOKEN] Token + project_id da luu vao Excel (server mode co the dung)")
                            _token_saved_to_excel = True
                        except Exception as _te:
                            self._log(f"   [TOKEN] Luu token vao Excel fail: {_te}", "warn")

                    # Đánh dấu thành công với ChromeManager
                    try:
                        from modules.chrome_manager import get_chrome_manager
                        get_chrome_manager().mark_success(self.worker_id)
                    except:
                        pass

                    # Update Excel if available - SAVE MEDIA_ID for SCENE images
                    # v1.0.441: Retry 10 lần đảm bảo ghi được Excel
                    if workbook and not is_reference_image:
                        _scene_saved = False
                        for _sa in range(10):
                            try:
                                workbook.update_scene(
                                    int(pid),
                                    img_path=str(images[0].local_path) if images[0].local_path else None,
                                    status_img="done",
                                    media_id=images[0].media_name if images[0].media_name else None
                                )
                                if workbook.safe_save():
                                    _scene_saved = True
                                    if images[0].media_name:
                                        self._log(f"   [EXCEL] Saved scene {pid}: media_id={images[0].media_name[:40]}...")
                                    elif pid.isdigit():
                                        self._log(f"   [WARN] Scene {pid}: API không trả về media_name (I2V sẽ không hoạt động)", "warn")
                                    break
                            except Exception as e:
                                if _sa < 9:
                                    self._log(f"   [EXCEL] Save scene {pid} retry {_sa+1}/10: {e}", "warn")
                                    time.sleep(3)
                                    try:
                                        workbook = PromptWorkbook(excel_path)
                                        workbook.load_or_create()
                                    except:
                                        pass
                                else:
                                    self._log(f"   [EXCEL] FAILED save scene {pid} after 3 attempts: {e}", "error")
                        # v1.0.441: Fallback - lưu pending write nếu save thất bại
                        if not _scene_saved:
                            try:
                                workbook._save_pending_write(
                                    'scene',
                                    scene_id=pid,
                                    img_path=str(images[0].local_path) if images[0].local_path else None,
                                    status_img="done",
                                    media_id=images[0].media_name if images[0].media_name else None
                                )
                                self._log(f"   [PENDING] Saved pending write for scene {pid}")
                            except Exception as pe:
                                self._log(f"   [PENDING] Cannot save pending: {pe}", "error")

                    # === SAVE MEDIA_ID to Excel for nv/loc images ===
                    # Cả nv* và loc* đều nằm trong sheet "characters"
                    if images[0].media_name and is_reference_image:
                        media_id_saved = False
                        if workbook:
                            # v1.0.383: Retry 10 lần khi Excel bị lock (WinError 32)
                            for _save_attempt in range(10):
                                try:
                                    # v1.0.210: Thêm status="done" khi có media_id
                                    if workbook.update_character(pid, media_id=images[0].media_name, status="done"):
                                        workbook.safe_save()
                                        self._log(f"   [EXCEL] Saved media_id + status=done for {pid}: {images[0].media_name[:40]}...")
                                        excel_media_ids[pid] = images[0].media_name
                                        media_id_saved = True
                                        break
                                    else:
                                        self._log(f"   [EXCEL] {pid} not found in characters sheet", "warn")
                                        break  # Không retry nếu character không tồn tại
                                except Exception as e:
                                    if _save_attempt < 9:
                                        self._log(f"   [EXCEL] Save retry {_save_attempt+1}/10: {e}", "warn")
                                        time.sleep(2)  # Đợi file unlock
                                        # Reload workbook để lấy file handle mới
                                        try:
                                            workbook = PromptWorkbook(excel_path)
                                            workbook.load_or_create()
                                        except:
                                            pass
                                    else:
                                        self._log(f"   [EXCEL] Cannot save media_id sau 3 lần: {e}", "warn")
                            # v1.0.441: Pending write fallback cho character/location
                            if not media_id_saved:
                                try:
                                    workbook._save_pending_write(
                                        'character',
                                        char_id=pid,
                                        media_id=images[0].media_name,
                                        status="done"
                                    )
                                    self._log(f"   [PENDING] Saved pending write for {pid}")
                                except:
                                    pass

                        # LUÔN save vào cache (backup) - không chỉ khi Excel fail
                        try:
                            cached_media_names[pid] = {'mediaName': images[0].media_name}
                            self._save_media_cache(cached_media_names)
                            if not media_id_saved:
                                self._log(f"   [CACHE] Fallback - saved media_id for {pid}")
                                excel_media_ids[pid] = images[0].media_name
                        except Exception as e:
                            self._log(f"   [CACHE] Cannot save media_id: {e}", "warn")
                    elif images[0].media_name:
                        self._log(f"   Media name: {images[0].media_name[:40]}...")
                else:
                    self._log(f"   [x] Thất bại: {error}", "error")
                    self.stats["failed"] += 1

                    # Đánh dấu lỗi với ChromeManager để giám sát
                    try:
                        from modules.chrome_manager import get_chrome_manager, ChromeRestartExhaustedException
                        chrome_mgr = get_chrome_manager()
                        chrome_mgr.mark_error(self.worker_id, str(error))

                        # Nếu Chrome bị đánh dấu ERROR, để Manager restart
                        if not chrome_mgr.is_healthy(self.worker_id):
                            self._log(f"[Manager] Chrome {self.worker_id} bị lỗi nhiều, đang restart...")
                            if chrome_mgr.restart_chrome(self.worker_id):
                                self._log(f"[Manager] [v] Chrome {self.worker_id} đã restart")
                                # Cập nhật reference
                                worker = chrome_mgr.workers.get(self.worker_id)
                                if worker and worker.drission_api:
                                    drission_api = worker.drission_api
                            else:
                                self._log(f"[Manager] [x] Restart Chrome {self.worker_id} thất bại", "warn")
                    except ChromeRestartExhaustedException as exhaust_e:
                        # Chrome đã restart quá nhiều lần - DỪNG WORKER HOÀN TOÀN
                        self._log(f"[FATAL] {exhaust_e}", "error")
                        self._log(f"[FATAL] Worker {self.worker_id} DỪNG - cần khởi động lại từ GUI!", "error")
                        raise  # Re-raise để dừng worker
                    except Exception as mgr_e:
                        pass  # Silent fail - fallback to original logic

                    # Check for token expiry - thử refresh và retry
                    if error and "401" in str(error):
                        self._log("[WARN] Bearer token hết hạn - thử restart Chrome...", "warn")
                        try:
                            if drission_api.restart_chrome():
                                self._log(f"→ Retry prompt: {pid}...", "info")
                                success2, images2, error2 = drission_api.generate_image(
                                    prompt=prompt,
                                    save_dir=save_dir,
                                    filename=pid,
                                    image_inputs=image_inputs if image_inputs else None
                                )
                                if success2 and images2:
                                    self._log(f"   [v] Retry thành công!")
                                    self.stats["success"] += 1
                                    self.stats["failed"] -= 1
                                    consecutive_errors = 0
                                    continue
                                else:
                                    self._log(f"   [x] Retry vẫn thất bại - skip scene {pid}, tiếp tục...", "warn")
                                    # Không break, để RETRY PHASE xử lý sau
                            else:
                                self._log(f"[x] Không restart được Chrome - skip scene {pid}", "warn")
                                # Không break, tiếp tục với scene khác
                        except Exception as e:
                            self._log(f"[x] Refresh token error: {e} - skip scene {pid}", "warn")
                            # Không break, tiếp tục với scene khác

                    # Check for 429 - Quota exceeded, cần đổi proxy/tài khoản
                    if error and "429" in str(error):
                        self._log(f"[WARN] Lỗi 429 (Quota) - Restart Chrome + đổi proxy...", "warn")
                        try:
                            if drission_api.restart_chrome():
                                self._log(f"→ Retry prompt: {pid}...", "info")
                                success2, images2, error2 = drission_api.generate_image(
                                    prompt=prompt,
                                    save_dir=save_dir,
                                    filename=pid,
                                    image_inputs=image_inputs if image_inputs else None
                                )
                                if success2 and images2:
                                    self._log(f"   [v] Retry thành công!")
                                    self.stats["success"] += 1
                                    self.stats["failed"] -= 1
                                    consecutive_errors = 0
                                    continue
                                else:
                                    self._log(f"   [x] Retry vẫn thất bại: {error2}", "warn")
                            else:
                                self._log("[x] Không restart được Chrome", "error")
                        except Exception as e:
                            self._log(f"[x] Restart error: {e}", "error")

                    # Check for POLICY_VIOLATION - Skip prompt immediately (đã retry trong drission_flow_api)
                    if error and "POLICY_VIOLATION" in str(error):
                        self._log(f"[WARN] POLICY VIOLATION - Prompt vi phạm nội dung! SKIP {pid}", "warn")
                        self.stats["skipped"] = self.stats.get("skipped", 0) + 1
                        self.stats["failed"] -= 1  # Undo fail count (đã đánh dấu failed trước đó)

                        # GHI VÀO EXCEL: status_img = "skip" để không retry lại
                        if not is_reference_image and workbook:
                            try:
                                workbook.update_scene(int(pid), status_img="skip")
                                workbook.safe_save()
                                self._log(f"   [EXCEL] Đánh dấu scene {pid} SKIP (policy violation)", "info")
                            except Exception as e:
                                self._log(f"   [EXCEL] Lỗi update Excel: {e}", "warn")

                        # v1.0.306: Tạo .SKIP file để vm_manager tính vào images_done
                        if not is_reference_image and pid.isdigit():
                            try:
                                skip_file = output_dir / f"{pid}.SKIP"
                                skip_file.touch()
                                self._log(f"   [SKIP] Tạo {skip_file.name} (policy violation)", "info")
                            except Exception as e:
                                self._log(f"   [SKIP] Không tạo được .SKIP: {e}", "warn")

                        continue  # Skip to next prompt

                    # Check for 400 - Invalid argument (reference image expired or invalid prompt)
                    if error and "400" in str(error):
                        self._log(f"[WARN] Lỗi 400 - Restart Chrome + retry không có reference...", "warn")
                        try:
                            # Restart Chrome trước khi retry
                            if drission_api.restart_chrome():
                                # Retry without reference images
                                success2, images2, error2 = drission_api.generate_image(
                                    prompt=prompt,
                                    save_dir=save_dir,
                                    filename=pid,
                                    image_inputs=None  # No reference images
                                )
                                if success2 and images2:
                                    self._log(f"   [v] Retry (no ref) thành công!")
                                    self.stats["success"] += 1
                                    self.stats["failed"] -= 1  # Undo fail count
                                    consecutive_errors = 0
                                    continue  # Move to next prompt
                                else:
                                    self._log(f"   [x] Retry (no ref) thất bại: {error2}", "warn")
                        except Exception as e:
                            self._log(f"   [x] Retry exception: {e}", "error")

                    # v1.0.195: KHÔNG restart Chrome ở đây vì drission_flow_api đã xử lý 403 rồi
                    # generate_image() đã restart Chrome + switch model khi gặp 403
                    # Restart lại ở đây sẽ gây double restart và reset 403 counter
                    if error and "403" in str(error) and False:  # DISABLED - drission_flow_api đã xử lý
                        self._log(f"[WARN] Lỗi 403 - Restart Chrome với proxy mới...", "warn")
                        try:
                            # Restart Chrome (clear blocked IPs + restart với proxy)
                            if drission_api.restart_chrome():
                                # Retry current prompt with new Chrome session
                                self._log(f"→ Retry prompt: {pid}...", "info")
                                success2, images2, error2 = drission_api.generate_image(
                                    prompt=prompt,
                                    save_dir=save_dir,
                                    filename=pid,
                                    image_inputs=image_inputs if image_inputs else None
                                )
                                if success2 and images2:
                                    self._log(f"   [v] Retry thành công! Saved {len(images2)} image(s)")
                                    self.stats["success"] += 1
                                    self.stats["failed"] -= 1  # Undo the fail count
                                    # Save media_id for scene images (v1.0.441: retry)
                                    if images2[0].media_name and not is_reference_image:
                                        for _sa in range(10):
                                            try:
                                                if workbook:
                                                    workbook.update_scene(int(pid), media_id=images2[0].media_name)
                                                    if workbook.safe_save():
                                                        self._log(f"   [EXCEL] Saved media_id for scene {pid}")
                                                        break
                                                else:
                                                    break
                                            except Exception as _se:
                                                if _sa < 9:
                                                    time.sleep(2)
                                                    try:
                                                        workbook = PromptWorkbook(excel_path)
                                                        workbook.load_or_create()
                                                    except:
                                                        pass
                                    # Save media_id for nv/loc images (all in characters sheet)
                                    if images2[0].media_name and is_reference_image:
                                        media_id_saved = False
                                        if workbook:
                                            try:
                                                # v1.0.210: Thêm status="done" khi có media_id
                                                if workbook.update_character(pid, media_id=images2[0].media_name, status="done"):
                                                    workbook.safe_save()
                                                    self._log(f"   [EXCEL] Saved media_id + status=done for {pid}")
                                                    excel_media_ids[pid] = images2[0].media_name
                                                    media_id_saved = True
                                            except:
                                                pass
                                        # Fallback to cache
                                        if not media_id_saved:
                                            try:
                                                cached_media_names[pid] = {'mediaName': images2[0].media_name}
                                                self._save_media_cache(cached_media_names)
                                                excel_media_ids[pid] = images2[0].media_name
                                            except:
                                                pass
                                    elif images2[0].media_name:
                                        self._log(f"   Media name: {images2[0].media_name[:40]}...")
                                else:
                                    self._log(f"   [x] Retry vẫn thất bại - skip scene {pid}", "warn")
                                    self.stats["failed"] = self.stats.get("failed", 0) + 1

                                    # GHI VÀO EXCEL: Đánh dấu scene này skip vì 403 retry thất bại
                                    if not is_reference_image and workbook:
                                        try:
                                            workbook.update_scene(int(pid), status_img="error_403")
                                            workbook.safe_save()
                                            self._log(f"   [EXCEL] Đánh dấu scene {pid} ERROR_403 (403 retry failed)", "info")
                                        except Exception as e:
                                            self._log(f"   [EXCEL] Lỗi update Excel: {e}", "warn")
                            else:
                                self._log(f"[x] Không restart được Chrome - skip scene {pid}", "warn")
                                # Không break, tiếp tục với scene khác

                                # GHI VÀO EXCEL: Đánh dấu scene này skip vì không restart được Chrome
                                if not is_reference_image and workbook:
                                    try:
                                        workbook.update_scene(int(pid), status_img="error_403")
                                        workbook.safe_save()
                                        self._log(f"   [EXCEL] Đánh dấu scene {pid} ERROR_403 (Chrome restart failed)", "info")
                                    except Exception as e:
                                        self._log(f"   [EXCEL] Lỗi update Excel: {e}", "warn")
                        except Exception as e:
                            self._log(f"[x] Restart error: {e} - skip scene {pid}", "warn")
                            # Không break, tiếp tục với scene khác

                            # GHI VÀO EXCEL: Đánh dấu scene này skip vì restart lỗi
                            if not is_reference_image and workbook:
                                try:
                                    workbook.update_scene(int(pid), status_img="error_403")
                                    workbook.safe_save()
                                    self._log(f"   [EXCEL] Đánh dấu scene {pid} ERROR_403 (restart exception)", "info")
                                except Exception as e2:
                                    self._log(f"   [EXCEL] Lỗi update Excel: {e2}", "warn")

            except Exception as e:
                error_str = str(e)
                self._log(f"   [x] Exception:\n{error_str}", "error")

                # v1.0.191: Xử lý "connection disconnected" - restart Chrome và retry
                if "disconnected" in error_str.lower() or "connection" in error_str.lower():
                    self._log("   [RECOVER] Chrome bị disconnect, restart và retry...", "warn")
                    try:
                        # Restart Chrome
                        if self.api:
                            self.api._kill_chrome()
                            time.sleep(2)
                            saved_url = getattr(self.api, '_current_project_url', None)
                            if self.api.setup(project_url=saved_url):
                                self._log("   [RECOVER] [v] Chrome restarted!", "info")
                                # Không tăng failed count, sẽ retry ở phase sau
                                continue
                    except Exception as recover_err:
                        self._log(f"   [RECOVER] Restart failed: {recover_err}", "error")

                self.stats["failed"] += 1

            # Rate limit
            time.sleep(1)

        # Save workbook (trước retry phase)
        if workbook:
            try:
                workbook.safe_save()
            except:
                pass

        # === RETRY PHASE: Tìm và retry những ảnh còn thiếu ===
        if self.stats["failed"] > 0:
            self._log("\n" + "=" * 60)
            self._log("RETRY PHASE - Tìm ảnh còn thiếu")
            self._log("=" * 60)

            # Tìm những prompts chưa có ảnh (và chưa bị skip/error)
            missing_prompts = []
            for prompt_data in prompts:
                pid = str(prompt_data.get('id', ''))
                if not pid:
                    continue

                is_reference = pid.lower().startswith('nv') or pid.lower().startswith('loc')
                save_dir = self.nv_path if is_reference else output_dir
                output_file = save_dir / f"{pid}.png"

                # Skip nếu file đã tồn tại
                if output_file.exists():
                    continue

                # Skip nếu đã đánh dấu skip/error trong Excel
                if workbook and not is_reference:
                    try:
                        scene = workbook.get_scene(int(pid))
                        if scene:
                            status = getattr(scene, 'status_img', '') or ''
                            # Bỏ qua scenes đã skip hoặc có error
                            if status in ['skip', 'error_403', 'error']:
                                self._log(f"[RETRY] Skip scene {pid} - status={status}", "info")
                                continue
                    except:
                        pass

                missing_prompts.append(prompt_data)

            if missing_prompts:
                self._log(f"Tìm thấy {len(missing_prompts)} ảnh thiếu, đang retry...")

                # Retry up to 3 rounds
                MAX_RETRY_ROUNDS = 3
                for retry_round in range(MAX_RETRY_ROUNDS):
                    if not missing_prompts:
                        break

                    self._log(f"\n--- Retry Round {retry_round + 1}/{MAX_RETRY_ROUNDS} ---")

                    # Kiểm tra và restart Chrome nếu cần trước khi retry
                    if drission_api and not drission_api._ready:
                        self._log("[RETRY] API chưa sẵn sàng, restart Chrome...")
                        try:
                            if drission_api.restart_chrome():
                                self._log("[RETRY] [v] Chrome restarted")
                            else:
                                self._log("[RETRY] [x] Không restart được Chrome, bỏ qua retry phase", "warn")
                                break
                        except Exception as e:
                            self._log(f"[RETRY] [x] Restart error: {e}", "warn")
                            break

                    still_missing = []

                    for prompt_data in missing_prompts:
                        pid = str(prompt_data.get('id', ''))
                        prompt = prompt_data.get('prompt', '')
                        is_reference = pid.lower().startswith('nv') or pid.lower().startswith('loc')
                        save_dir = self.nv_path if is_reference else output_dir

                        self._log(f"[RETRY] {pid}...")

                        try:
                            # Build reference images if needed
                            image_inputs = None
                            refs = prompt_data.get('references', [])
                            if refs and not is_reference:
                                image_inputs = []
                                for ref_id in refs:
                                    ref_key = ref_id.lower()
                                    media_id = None
                                    for k, v in excel_media_ids.items():
                                        if k.lower() == ref_key:
                                            media_id = v
                                            break
                                    if not media_id:
                                        for k, v in cached_media_names.items():
                                            if k.lower() == ref_key:
                                                media_id = v.get('mediaName', v) if isinstance(v, dict) else v
                                                break
                                    if media_id:
                                        image_inputs.append({
                                            "name": media_id,
                                            "imageInputType": "IMAGE_INPUT_TYPE_REFERENCE"
                                        })

                            success, images, error = drission_api.generate_image(
                                prompt=prompt,
                                save_dir=save_dir,
                                filename=pid,
                                image_inputs=image_inputs
                            )

                            if success and images:
                                self._log(f"   [v] Retry OK: {pid}")
                                self.stats["success"] += 1
                                self.stats["failed"] -= 1
                            else:
                                self._log(f"   [x] Retry fail: {error}", "warn")
                                still_missing.append(prompt_data)

                        except Exception as e:
                            self._log(f"   [x] Retry error: {e}", "error")
                            still_missing.append(prompt_data)

                        time.sleep(2)

                    missing_prompts = still_missing

                    # Wait before next round
                    if missing_prompts and retry_round < MAX_RETRY_ROUNDS - 1:
                        wait_time = 5 * (retry_round + 1)
                        self._log(f"Còn {len(missing_prompts)} ảnh thiếu, đợi {wait_time}s...")
                        time.sleep(wait_time)

                if missing_prompts:
                    self._log(f"[WARN] Vẫn còn {len(missing_prompts)} ảnh không tạo được sau {MAX_RETRY_ROUNDS} rounds", "warn")

                    # === FULL RESTART: Tắt Chrome hoàn toàn, mở lại và thử lại ===
                    # Giống như người dùng làm thủ công: tắt đi bật lại
                    MAX_FULL_RESTARTS = 2  # Tối đa 2 lần full restart
                    for restart_count in range(MAX_FULL_RESTARTS):
                        if not missing_prompts:
                            break

                        self._log(f"\n{'='*60}")
                        self._log(f"[SYNC] FULL RESTART {restart_count + 1}/{MAX_FULL_RESTARTS} - Tắt Chrome, mở lại")
                        self._log(f"{'='*60}")

                        try:
                            # 1. Đóng Chrome hoàn toàn
                            self._log("   → Đóng Chrome...")
                            drission_api.close()
                            time.sleep(3)

                            # 2. Mở lại Chrome và setup
                            self._log("   → Mở lại Chrome...")
                            saved_url = getattr(drission_api, '_current_project_url', None)
                            if drission_api.setup(project_url=saved_url):
                                self._log("   [v] Chrome đã sẵn sàng!")

                                # v1.0.163: Reset model flag để chọn lại khi generate
                                drission_api._model_selected = False
                                self._log("   → Model flag reset")

                                # 4. Thử lại các ảnh thiếu
                                still_missing = []
                                for prompt_data in missing_prompts:
                                    pid = str(prompt_data.get('id', ''))
                                    prompt = prompt_data.get('prompt', '')
                                    is_reference = pid.lower().startswith('nv') or pid.lower().startswith('loc')
                                    save_dir = self.nv_path if is_reference else output_dir

                                    self._log(f"   [FULL-RETRY] {pid}...")

                                    try:
                                        success, images, error = drission_api.generate_image(
                                            prompt=prompt,
                                            save_dir=save_dir,
                                            filename=pid,
                                            force_model=force_model
                                        )

                                        if success and images:
                                            self._log(f"      [v] OK!")
                                            self.stats["success"] += 1
                                            self.stats["failed"] -= 1

                                            # Save media_id nếu có
                                            # v1.0.210: Thêm status="done" khi có media_id
                                            if images[0].media_name and is_reference:
                                                try:
                                                    if workbook and workbook.update_character(pid, media_id=images[0].media_name, status="done"):
                                                        workbook.safe_save()
                                                        excel_media_ids[pid] = images[0].media_name
                                                except:
                                                    pass
                                        else:
                                            self._log(f"      [x] {error}", "warn")
                                            still_missing.append(prompt_data)

                                    except Exception as e:
                                        self._log(f"      [x] Error: {e}", "error")
                                        still_missing.append(prompt_data)

                                    time.sleep(2)

                                missing_prompts = still_missing

                                if not missing_prompts:
                                    self._log("   [v] Tất cả ảnh đã hoàn thành sau full restart!")
                                    break
                            else:
                                self._log("   [x] Không thể mở lại Chrome", "error")

                        except Exception as e:
                            self._log(f"   [x] Full restart error: {e}", "error")

                        # Đợi trước khi thử lại
                        if missing_prompts and restart_count < MAX_FULL_RESTARTS - 1:
                            wait = 10 * (restart_count + 1)
                            self._log(f"   Còn {len(missing_prompts)} ảnh, đợi {wait}s...")
                            time.sleep(wait)

                    if missing_prompts:
                        self._log(f"[WARN] Vẫn còn {len(missing_prompts)} ảnh sau {MAX_FULL_RESTARTS} full restart", "warn")
            else:
                self._log("Tất cả ảnh đã có, không cần retry")

        # === LƯU TOKEN VÀO EXCEL + CACHE CHO VIDEO WORKER ===
        # Quan trọng: Video worker cần token để tạo I2V
        try:
            if drission_api.bearer_token and drission_api.project_id:
                bearer = drission_api.bearer_token
                if bearer.startswith("Bearer "):
                    bearer = bearer[7:]  # Remove "Bearer " prefix
                project_id = drission_api.project_id
                # Lấy recaptcha_token nếu có (quan trọng cho I2V!)
                recaptcha = getattr(drission_api, 'recaptcha_token', '') or ''
                x_browser_val = getattr(drission_api, 'x_browser_validation', '') or ''

                # 1. Lưu vào config để _save_media_cache có thể đọc
                self.config['flow_bearer_token'] = bearer
                self.config['flow_project_id'] = project_id
                self.config['flow_recaptcha_token'] = recaptcha
                self.config['flow_x_browser_validation'] = x_browser_val

                # 2. Lưu vào Excel (sheet config) để tái sử dụng
                if workbook:
                    try:
                        import openpyxl
                        wb = openpyxl.load_workbook(excel_path)

                        # Tạo hoặc lấy sheet 'config'
                        if 'config' not in wb.sheetnames:
                            ws = wb.create_sheet('config')
                            ws['A1'] = 'key'
                            ws['B1'] = 'value'
                        else:
                            ws = wb['config']
                            # Đảm bảo có header nếu sheet đã tồn tại nhưng thiếu
                            if not ws['A1'].value or str(ws['A1'].value).strip().lower() != 'key':
                                ws['A1'] = 'key'
                                ws['B1'] = 'value'

                        # Lấy project URL để lưu (cho lần chạy tiếp theo vào đúng project)
                        project_url = getattr(drission_api, '_current_project_url', '')
                        if not project_url and project_id:
                            project_url = f"https://labs.google/fx/vi/tools/flow/project/{project_id}"

                        # Lưu Chrome profile path để resume đúng profile
                        chrome_profile_path = str(drission_api.profile_dir) if hasattr(drission_api, 'profile_dir') else ''

                        # Lưu các config - đầy đủ để tái sử dụng cho I2V
                        config_items = {
                            'flow_project_id': project_id,
                            'flow_project_url': project_url,  # URL để vào lại project cũ
                            'flow_bearer_token': bearer,  # Full token để video worker dùng
                            'flow_recaptcha_token': recaptcha,  # Quan trọng cho I2V!
                            'flow_x_browser_validation': x_browser_val,  # Auth header
                            'token_time': str(int(time.time())),
                            'chrome_profile_path': chrome_profile_path  # Profile để resume đúng Chrome
                        }

                        for key, value in config_items.items():
                            # Tìm row có key này để update
                            found = False
                            for row_num in range(2, ws.max_row + 1):
                                cell_key = ws.cell(row=row_num, column=1).value
                                if cell_key and str(cell_key).strip().lower() == key.lower():
                                    ws.cell(row=row_num, column=2, value=value)
                                    found = True
                                    break
                            if not found:
                                # Tìm row trống đầu tiên (sau header)
                                next_row = 2
                                for r in range(2, 50):
                                    if not ws.cell(row=r, column=1).value:
                                        next_row = r
                                        break
                                    next_row = r + 1
                                ws.cell(row=next_row, column=1, value=key)
                                ws.cell(row=next_row, column=2, value=value)

                        wb.save(excel_path)
                        wb.close()
                        self._log(f"[EXCEL] Saved project_id + token to Excel")
                    except Exception as e:
                        self._log(f"[EXCEL] Warning: Cannot save to Excel: {e}", "warn")

                # 3. Lưu full token vào media cache (để video worker dùng)
                self._save_media_cache(cached_media_names)
                self._log(f"[CACHE] Saved full token for video worker")
        except Exception as e:
            self._log(f"[CACHE] Warning: Cannot save token: {e}", "warn")

        # === I2V: TẠO VIDEO TỪ ẢNH (CÙNG SESSION CHROME) ===
        video_count_setting = self.config.get('video_count', 0)
        try:
            if video_count_setting == 'full':
                video_count = 999999  # Tất cả
            else:
                video_count = int(video_count_setting)
        except:
            video_count = 0

        # === KIỂM TRA: Ảnh của WORKER NÀY đã xong chưa? ===
        # v1.0.389: Chỉ check scenes thuộc worker này (không đợi Chrome kia)
        all_images_done = True
        pending_scenes = []
        if video_count > 0 and workbook:
            try:
                for scene in workbook.get_scenes():
                    # Normalize: 1.0 -> "1"
                    scene_id = str(int(float(scene.scene_id))) if hasattr(scene, 'scene_id') and scene.scene_id else ''
                    if not scene_id or not scene_id.isdigit():
                        continue  # Bỏ qua nv/loc

                    # v1.0.389: Chỉ check scenes của worker này
                    try:
                        scene_num = int(scene_id)
                        if total_workers > 1 and (scene_num % total_workers) != (worker_id % total_workers):
                            continue  # Scene của Chrome kia, bỏ qua
                    except ValueError:
                        continue

                    # Kiểm tra status_img
                    status_img = getattr(scene, 'status_img', '') or ''
                    prompt = getattr(scene, 'img_prompt', '') or ''

                    # Nếu có prompt và chưa done (bỏ qua skip/error) → chưa xong
                    # Skip/error không cần chờ vì không thể tạo được
                    if prompt and prompt.strip().upper() != 'DO_NOT_GENERATE':
                        if status_img == 'done':
                            pass  # OK
                        elif status_img in ['skip', 'error_403', 'error']:
                            pass  # Bỏ qua, không cần chờ
                        else:
                            # v1.0.441: RECONCILIATION - check file ảnh thực trên disk
                            # Excel có thể bị lock nên status chưa ghi, nhưng ảnh đã có
                            img_path = Path(excel_path).parent / "img" / f"scene_{scene_id.zfill(3)}.png"
                            img_path2 = Path(excel_path).parent / "img" / f"{scene_id}.png"
                            if img_path.exists() or img_path2.exists():
                                # Ảnh đã có trên disk, chỉ Excel chưa ghi → coi như done
                                # Thử ghi lại vào Excel
                                try:
                                    actual_path = img_path if img_path.exists() else img_path2
                                    workbook.update_scene(int(scene_id), img_path=f"img/{actual_path.name}", status_img="done")
                                    workbook.safe_save()
                                    self._log(f"   [RECONCILE] Scene {scene_id}: ảnh có trên disk, đã sync Excel")
                                except:
                                    pass  # Không ghi được cũng OK, vẫn coi là done
                            else:
                                # Thật sự chưa có ảnh
                                all_images_done = False
                                pending_scenes.append(scene_id)
            except Exception as e:
                self._log(f"[I2V] Warning: Cannot check pending scenes: {e}", "warn")
                all_images_done = False  # Nếu lỗi, coi như chưa xong → KHÔNG tạo video

        if not all_images_done:
            self._log(f"[I2V] [WAIT] SKIP - Còn {len(pending_scenes)} scene (worker {worker_id}) chưa có ảnh: {pending_scenes[:10]}...")
            self._log(f"[I2V] Video sẽ được tạo sau khi ảnh của worker này hoàn thành")
        elif video_count > 0 and drission_api._ready:
            self._log("")
            self._log("=" * 60)
            self._log(f"[I2V] TẠO VIDEO TỪ ẢNH (cùng session)")
            self._log("=" * 60)

            # Lấy danh sách scenes cần tạo video (có media_id, chưa có video)
            scenes_for_video = []
            scenes_without_media_id = []
            if workbook:
                try:
                    all_scenes = workbook.get_scenes()
                    self._log(f"[I2V] Loaded {len(all_scenes)} scenes from Excel")

                    for scene in all_scenes:
                        # Chỉ lấy scene (không phải character/location)
                        # Normalize: 1.0 -> "1"
                        scene_id = str(int(float(scene.scene_id))) if hasattr(scene, 'scene_id') and scene.scene_id else ''
                        if not scene_id or not scene_id.isdigit():
                            continue

                        # v1.0.389: Chỉ tạo video cho scenes của worker này
                        try:
                            scene_num = int(scene_id)
                            if total_workers > 1 and (scene_num % total_workers) != (worker_id % total_workers):
                                continue
                        except ValueError:
                            continue

                        # Lấy media_id từ CACHE trước (giống tạo ảnh), fallback Excel
                        media_id = cached_media_names.get(scene_id, {})
                        if isinstance(media_id, dict):
                            media_id = media_id.get('mediaName', '')
                        if not media_id:
                            media_id = getattr(scene, 'media_id', '') or ''

                        video_path = getattr(scene, 'video_path', '') or ''
                        status_vid = getattr(scene, 'status_vid', '') or ''
                        video_note = getattr(scene, 'video_note', '') or ''  # Ghi chú video

                        if not media_id:
                            scenes_without_media_id.append(scene_id)
                        elif not video_path and status_vid != 'done':
                            # CHECK video_note: Nếu là "SKIP" thì bỏ qua scene này
                            if video_note == "SKIP":
                                continue  # BỎ QUA scene này (BASIC mode, segment > 1)

                            video_prompt = getattr(scene, 'video_prompt', '') or 'Subtle cinematic motion'
                            scenes_for_video.append({
                                'scene_id': scene_id,
                                'media_id': media_id,
                                'video_prompt': video_prompt
                            })

                    if scenes_without_media_id:
                        self._log(f"[I2V] [WARN] {len(scenes_without_media_id)} scenes KHÔNG CÓ media_id: {scenes_without_media_id[:5]}{'...' if len(scenes_without_media_id) > 5 else ''}", "warn")
                except Exception as e:
                    self._log(f"[I2V] Error loading scenes: {e}", "warn")

            # Fallback: Lấy từ cached_media_names
            if not scenes_for_video and cached_media_names:
                for pid, media_info in cached_media_names.items():
                    if pid.isdigit():  # Chỉ scenes (số)
                        media_id = media_info.get('mediaName', media_info) if isinstance(media_info, dict) else media_info
                        if media_id:
                            scenes_for_video.append({
                                'scene_id': pid,
                                'media_id': media_id,
                                'video_prompt': 'Subtle cinematic motion, slow camera movement'
                            })

            # Giới hạn số lượng
            scenes_for_video = scenes_for_video[:video_count]

            if scenes_for_video:
                self._log(f"[I2V] Tạo video cho {len(scenes_for_video)} ảnh...")
                video_success = 0
                video_failed = 0

                for i, scene_info in enumerate(scenes_for_video):
                    scene_id = scene_info['scene_id']
                    media_id = scene_info['media_id']
                    video_prompt = scene_info['video_prompt']

                    self._log(f"[I2V] [{i+1}/{len(scenes_for_video)}] Scene {scene_id}...")

                    try:
                        # Dùng generate_video_t2v_mode() - Chuyển sang "Từ văn bản sang video"
                        # Chrome gửi T2V request → Interceptor convert sang I2V với media_id
                        video_dir = output_dir  # img/ folder
                        video_file = video_dir / f"{scene_id}.mp4"

                        success, result_path, error = drission_api.generate_video_t2v_mode(
                            media_id=media_id,
                            prompt=video_prompt,
                            video_model="veo_3_0_r2v_fast",
                            save_path=video_file  # Tự download luôn
                        )

                        if success:
                            self._log(f"   [v] OK: {video_file.name}")
                            video_success += 1

                            # Di chuyển ảnh vào img_backup/ sau khi tạo video thành công
                            # (Giữ lại để hiển thị thumbnail trong GUI)
                            image_file = video_dir / f"{scene_id}.png"
                            if image_file.exists():
                                try:
                                    # Tạo folder backup nếu chưa có
                                    backup_dir = video_dir.parent / "img_backup"
                                    backup_dir.mkdir(exist_ok=True)

                                    # Di chuyển ảnh vào backup
                                    backup_file = backup_dir / image_file.name
                                    image_file.rename(backup_file)
                                    self._log(f"   [MOVE] Moved image to backup: {image_file.name}")
                                except Exception as e:
                                    self._log(f"   [WARN] Cannot move image: {e}", "warn")

                            # Update Excel
                            if workbook:
                                workbook.update_scene(int(scene_id), video_path=video_file.name, status_vid='done')
                                workbook.safe_save()
                        else:
                            self._log(f"   [x] Failed: {error}", "warn")
                            video_failed += 1

                    except Exception as e:
                        self._log(f"   [x] Error: {e}", "error")
                        video_failed += 1

                    # Delay giữa các video
                    time.sleep(3)

                # v1.0.163: Bỏ switch_to_image_mode() - giao diện mới không cần
                # drission_api.switch_to_image_mode()

                self._log(f"[I2V] Hoàn tất: {video_success} OK, {video_failed} failed")
            else:
                self._log(f"[I2V] Không có ảnh nào cần tạo video")
        elif video_count > 0:
            self._log(f"[I2V] Bỏ qua - DrissionAPI chưa sẵn sàng")

        # Cleanup (sau I2V)
        try:
            drission_api.close()
        except:
            pass

        # Save workbook final
        if workbook:
            try:
                workbook.safe_save()
            except:
                pass

        # Summary
        self._log("=" * 60)
        self._log("HOAN THANH (DRISSION MODE)")
        self._log("=" * 60)
        self._log(f"Tong: {self.stats['total']}")
        self._log(f"Thanh cong: {self.stats['success']}")
        self._log(f"That bai: {self.stats['failed']}")
        self._log(f"Bo qua: {self.stats['skipped']}")

        return {
            "success": True,
            "stats": self.stats.copy()
        }

    def __enter__(self):
        """Context manager entry."""
        self.start_browser()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """Context manager exit."""
        self.stop_browser()


# =============================================================================
# HELPER FUNCTIONS
# =============================================================================

def create_browser_flow_generator(
    project_path: str,
    profile_name: str = "main",
    headless: bool = False,
    verbose: bool = True
) -> BrowserFlowGenerator:
    """
    Factory function de tao BrowserFlowGenerator.

    Args:
        project_path: Duong dan project
        profile_name: Ten Chrome profile
        headless: Chay an
        verbose: In log

    Returns:
        BrowserFlowGenerator instance
    """
    return BrowserFlowGenerator(
        project_path=project_path,
        profile_name=profile_name,
        headless=headless,
        verbose=verbose
    )


def generate_images_from_excel(
    project_path: str,
    profile_name: str = "main",
    headless: bool = False,
    characters: bool = True,
    scenes: bool = True,
    start_scene: int = 1,
    end_scene: Optional[int] = None,
    overwrite: bool = False
) -> Dict[str, Any]:
    """
    Ham tien ich de tao anh tu Excel.

    Args:
        project_path: Duong dan project (PROJECTS/KA1-0001)
        profile_name: Ten Chrome profile
        headless: Chay an
        characters: Tao anh nhan vat
        scenes: Tao anh scenes
        start_scene: Scene bat dau
        end_scene: Scene ket thuc
        overwrite: Ghi de anh cu

    Returns:
        Dict voi ket qua
    """
    generator = BrowserFlowGenerator(
        project_path=project_path,
        profile_name=profile_name,
        headless=headless,
        verbose=True
    )

    return generator.generate_all(
        characters=characters,
        scenes=scenes,
        start_scene=start_scene,
        end_scene=end_scene,
        overwrite=overwrite
    )


# =============================================================================
# CLI
# =============================================================================

if __name__ == "__main__":
    print("""
+============================================================+
|      BROWSER FLOW GENERATOR - VE3 TOOL                     |
+============================================================+
|  Tao anh tu Excel bang browser automation                  |
|                                                            |
|  Usage:                                                    |
|    python browser_flow_generator.py <project_path>         |
|                                                            |
|  Options:                                                  |
|    --profile <name>    Chrome profile name (default: main) |
|    --headless          Chay an (khong hien UI)             |
|    --characters        Chi tao anh nhan vat                |
|    --scenes            Chi tao anh scenes                  |
|    --start N           Bat dau tu scene N                  |
|    --end N             Ket thuc o scene N                  |
|    --overwrite         Ghi de anh da co                    |
+============================================================+
""")

    if not SELENIUM_AVAILABLE:
        print("Error: Selenium chua duoc cai dat")
        print("Chay: pip install selenium undetected-chromedriver")
        sys.exit(1)

    if len(sys.argv) < 2:
        print("Vui long cung cap duong dan project")
        print("Vi du: python browser_flow_generator.py ./PROJECTS/KA1-0001")
        sys.exit(1)

    project_path = sys.argv[1]

    # Parse options
    profile_name = "main"
    headless = "--headless" in sys.argv
    do_characters = "--characters" in sys.argv or "--scenes" not in sys.argv
    do_scenes = "--scenes" in sys.argv or "--characters" not in sys.argv
    overwrite = "--overwrite" in sys.argv
    start_scene = 1
    end_scene = None

    for i, arg in enumerate(sys.argv):
        if arg == "--profile" and i + 1 < len(sys.argv):
            profile_name = sys.argv[i + 1]
        if arg == "--start" and i + 1 < len(sys.argv):
            start_scene = int(sys.argv[i + 1])
        if arg == "--end" and i + 1 < len(sys.argv):
            end_scene = int(sys.argv[i + 1])

    # Run
    results = generate_images_from_excel(
        project_path=project_path,
        profile_name=profile_name,
        headless=headless,
        characters=do_characters,
        scenes=do_scenes,
        start_scene=start_scene,
        end_scene=end_scene,
        overwrite=overwrite
    )

    # Exit code
    scenes_failed = results.get("scenes", {}).get("stats", {}).get("failed", 0)
    chars_failed = results.get("characters", {}).get("characters_failed", 0)

    sys.exit(0 if (scenes_failed + chars_failed) == 0 else 1)
