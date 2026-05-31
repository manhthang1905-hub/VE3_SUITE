"""
Flow Extension Auth — get token/project/media via FlowKit extension API.

Replaces Chrome UI automation (DrissionPage) with simple HTTP calls
to FlowKit agent running on localhost. Extension handles:
- Bearer token capture (from Flow page webRequest)
- reCAPTCHA solving (automatic)
- Project creation (click "New project")
- Image upload (API call through browser context)

Usage:
    auth = FlowExtensionAuth("http://127.0.0.1:8100")
    if auth.is_ready():
        token = auth.get_token()
        project_id = auth.get_project_id()
        media_id = auth.upload_image("path/to/image.png", token, project_id)
"""
import base64
import logging
import time
from pathlib import Path
from typing import Optional, Tuple

try:
    import httpx
except ImportError:
    import requests as httpx

logger = logging.getLogger(__name__)


class FlowExtensionAuth:
    """Get Flow auth data via FlowKit Chrome extension API."""

    def __init__(self, agent_url: str = "http://127.0.0.1:8100", log_func=None):
        self.agent_url = agent_url.rstrip("/")
        self._log = log_func or (lambda msg, *a: logger.info(msg))

    def is_ready(self, timeout: int = 5) -> bool:
        """Check if extension is connected and has token."""
        try:
            r = httpx.get(f"{self.agent_url}/health", timeout=timeout)
            if hasattr(r, 'json'):
                data = r.json()
            else:
                data = r.json()
            return data.get("extension_connected", False) and data.get("flow_key_present", False)
        except Exception:
            return False

    def wait_ready(self, timeout: int = 60) -> bool:
        """Wait for extension to be ready."""
        deadline = time.time() + timeout
        while time.time() < deadline:
            if self.is_ready():
                return True
            time.sleep(3)
        return False

    def get_token(self) -> Optional[str]:
        """Get bearer token from extension."""
        try:
            r = httpx.get(f"{self.agent_url}/api/get-token", timeout=10)
            data = r.json()
            if data.get("success"):
                token = data["token"]
                self._log(f"[ExtAuth] Token: {token[:25]}... ({len(token)} chars)")
                return token
            self._log(f"[ExtAuth] No token: {data.get('error', '?')}")
        except Exception as e:
            self._log(f"[ExtAuth] Token error: {e}")
        return None

    def get_project_id(self) -> Optional[str]:
        """Get current project ID from Flow tab URL."""
        try:
            r = httpx.post(f"{self.agent_url}/api/extract-project-id", json={}, timeout=15)
            data = r.json()
            if data.get("success"):
                pid = data.get("data", {}).get("projectId", "")
                if pid:
                    self._log(f"[ExtAuth] Project: {pid}")
                    return pid
            self._log(f"[ExtAuth] No project ID: {data.get('error', '?')}")
        except Exception as e:
            self._log(f"[ExtAuth] Project ID error: {e}")
        return None

    def ensure_project(self) -> Optional[str]:
        """Create new project if needed, return project ID."""
        try:
            r = httpx.post(f"{self.agent_url}/api/ensure-project", json={}, timeout=60)
            data = r.json()
            if data.get("success"):
                self._log(f"[ExtAuth] Project ensured: {data.get('data', {})}")
                time.sleep(2)
                return self.get_project_id()
            self._log(f"[ExtAuth] Ensure project failed: {data.get('error', '?')}")
        except Exception as e:
            self._log(f"[ExtAuth] Ensure project error: {e}")
        return None

    def upload_image(self, image_path: str, bearer_token: str, project_id: str) -> Optional[str]:
        """Upload image → get media_id."""
        image_path = Path(image_path)
        if not image_path.exists():
            self._log(f"[ExtAuth] Image not found: {image_path}")
            return None

        try:
            with open(image_path, "rb") as f:
                img_bytes = f.read()

            img_b64 = base64.b64encode(img_bytes).decode("utf-8")

            suffix = image_path.suffix.lower()
            mime_map = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".webp": "image/webp"}
            mime_type = mime_map.get(suffix, "image/png")

            r = httpx.post(f"{self.agent_url}/api/upload-image", json={
                "bearer_token": bearer_token,
                "image_base64": img_b64,
                "mime_type": mime_type,
                "project_id": project_id,
            }, timeout=60)
            data = r.json()

            if data.get("success"):
                media_name = data.get("media_name", "")
                if media_name:
                    self._log(f"[ExtAuth] Upload OK: {image_path.name} → {media_name}")
                    return media_name
                self._log(f"[ExtAuth] Upload OK but no media_name")
            else:
                self._log(f"[ExtAuth] Upload failed: {data.get('error', '?')}")
        except Exception as e:
            self._log(f"[ExtAuth] Upload error: {e}")
        return None

    def get_all(self) -> Tuple[Optional[str], Optional[str]]:
        """Get token + project_id in one call."""
        token = self.get_token()
        project_id = self.get_project_id()
        if not project_id and token:
            project_id = self.ensure_project()
        return token, project_id
