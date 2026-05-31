from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from .excel_manager import PromptWorkbook
from .flow_reference_bridge import (
    FlowReferenceBridge,
    FlowReferenceConfig,
)


LogFunc = Callable[[str, str], None]


@dataclass
class FlowAccountRuntime:
    name: str
    email: str
    password: str
    totp_secret: str = ""
    profile_dir: str = ""
    chrome_path: str = ""
    enabled: bool = True


class FlowRuntimeAuthService:
    """Runtime auth service for VE3 worker/UI.

    Supports 2 cases:
    - New project: no project_id on workbook -> create a new Flow project and capture token.
    - Existing project: project_id already known but token expired -> reopen old project and refresh token.
    """

    def __init__(self, suite_root: str | Path, settings: Dict, log_func: Optional[LogFunc] = None):
        self.suite_root = Path(suite_root)
        self.settings = settings
        self.log_func = log_func or (lambda msg, level="INFO": None)

    def log(self, msg: str, level: str = "INFO") -> None:
        self.log_func(msg, level)

    def is_enabled(self) -> bool:
        return bool(self.settings.get("flow_auth_auto_enabled", True))

    def chrome_path(self) -> str:
        p = str(self.settings.get("flow_auth_chrome_path", "") or "").strip()
        if p:
            return p
        return str(self.suite_root / "GoogleChromePortable" / "GoogleChromePortable.exe")

    def get_accounts(self) -> List[FlowAccountRuntime]:
        rows = self.settings.get("flow_accounts", []) or []
        accounts: List[FlowAccountRuntime] = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            email = str(row.get("email", "") or "").strip()
            password = str(row.get("password", "") or "").strip()
            if not email or not password:
                continue
            accounts.append(
                FlowAccountRuntime(
                    name=str(row.get("name", "") or email).strip(),
                    email=email,
                    password=password,
                    totp_secret=str(row.get("totp_secret", "") or "").strip(),
                    profile_dir=str(row.get("profile_dir", "") or "").strip(),
                    chrome_path=str(row.get("chrome_path", "") or "").strip(),
                    enabled=bool(row.get("enabled", True)),
                )
            )
        return [a for a in accounts if a.enabled]

    def pick_account(self, preferred_name: str = "") -> Optional[FlowAccountRuntime]:
        accounts = self.get_accounts()
        if not accounts:
            return None
        if preferred_name:
            for account in accounts:
                if account.name == preferred_name:
                    return account
        default_name = str(self.settings.get("flow_auth_default_account", "") or "").strip()
        if default_name:
            for account in accounts:
                if account.name == default_name:
                    return account
        return accounts[0]

    def _auth_worker_slot(self, account: FlowAccountRuntime, project_dir: str | Path) -> int:
        """
        Give each auth flow a stable, widely separated port slot.

        google_login.py uses: 9222 + worker_id
        bridge helper uses:   9322 + worker_id
        warmup helper uses:   9422 + worker_id

        We keep worker_id as a bounded offset instead of raw 0 so concurrent
        projects/accounts do not all collide on the same debug ports.
        """
        key = f"{Path(project_dir).resolve()}|{account.name}|{account.email}".encode("utf-8", errors="ignore")
        digest = hashlib.sha1(key).digest()
        slot = int.from_bytes(digest[:2], "big") % 2000
        return 1000 + slot

    def _bridge_for_account(self, account: FlowAccountRuntime, project_dir: str | Path) -> FlowReferenceBridge:
        return FlowReferenceBridge(
            suite_root=self.suite_root,
            config=FlowReferenceConfig(
                chrome_path=account.chrome_path or self.chrome_path(),
                profile_dir=account.profile_dir,
                email=account.email,
                password=account.password,
                totp_secret=account.totp_secret,
                worker_id=self._auth_worker_slot(account, project_dir),
            ),
            log_func=self.log,
        )

    @staticmethod
    def _auth_state_path(project_dir: str | Path) -> Path:
        return Path(project_dir) / ".flow_auth_state.json"

    def load_cached_auth(self, project_dir: str | Path) -> Dict[str, str]:
        path = self._auth_state_path(project_dir)
        if not path.exists():
            return {}
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except Exception as e:
            self.log(f"[AUTH] cannot read auth backup {path.name}: {e}", "WARN")
            return {}
        if not isinstance(raw, dict):
            return {}
        project_id = str(raw.get("flow_project_id", "") or "").strip()
        project_url = self._normalize_project_url(project_id, str(raw.get("flow_project_url", "") or "").strip())
        token = str(raw.get("flow_bearer_token", "") or "").strip()
        if token.lower().startswith("bearer "):
            token = token[7:].strip()
        return {
            "token": token,
            "project_id": project_id,
            "project_url": project_url,
            "account_name": str(raw.get("flow_account_name", "") or "").strip(),
            "token_updated_at": str(raw.get("flow_token_updated_at", "") or "").strip(),
        }

    def persist_cached_auth(
        self,
        project_dir: str | Path,
        *,
        token: str = "",
        project_id: str = "",
        project_url: str = "",
        account_name: str = "",
        token_updated_at: str = "",
    ) -> bool:
        path = self._auth_state_path(project_dir)
        path.parent.mkdir(parents=True, exist_ok=True)
        token = (token or "").strip()
        if token.lower().startswith("bearer "):
            token = token[7:].strip()
        project_id = (project_id or "").strip()
        project_url = self._normalize_project_url(project_id, project_url or "")
        payload = {
            "version": 1,
            "flow_bearer_token": token,
            "flow_project_id": project_id,
            "flow_project_url": project_url,
            "flow_account_name": (account_name or "").strip(),
            "flow_token_updated_at": str(token_updated_at or int(time.time())),
            "saved_at": int(time.time()),
        }
        temp_path = path.with_suffix(path.suffix + ".tmp")
        try:
            temp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
            temp_path.replace(path)
            return True
        except Exception as e:
            self.log(f"[AUTH] cannot persist auth backup {path.name}: {e}", "WARN")
            try:
                if temp_path.exists():
                    temp_path.unlink()
            except Exception:
                pass
            return False

    def _resolve_existing_auth(self, project_dir: str | Path, wb: PromptWorkbook) -> Dict[str, str]:
        cached = self.load_cached_auth(project_dir)
        wb_token = (wb.get_config_value("flow_bearer_token") or "").strip()
        if wb_token.lower().startswith("bearer "):
            wb_token = wb_token[7:].strip()
        wb_pid = (wb.get_config_value("flow_project_id") or "").strip()
        wb_url = (wb.get_config_value("flow_project_url") or "").strip()
        wb_account = (wb.get_config_value("flow_account_name") or "").strip()
        project_id = wb_pid or cached.get("project_id", "")
        project_url = self._normalize_project_url(project_id, wb_url or cached.get("project_url", ""))
        token = wb_token or cached.get("token", "")
        account_name = wb_account or cached.get("account_name", "")
        return {
            "token": token,
            "project_id": project_id,
            "project_url": project_url,
            "account_name": account_name,
            "source": "workbook" if (wb_pid or wb_url or wb_account or wb_token) else ("backup" if cached else ""),
        }

    def _restore_auth_to_workbook(self, wb: PromptWorkbook, auth: Dict[str, str]) -> None:
        project_id = (auth.get("project_id", "") or "").strip()
        project_url = (auth.get("project_url", "") or "").strip()
        token = (auth.get("token", "") or "").strip()
        account_name = (auth.get("account_name", "") or "").strip()
        changed = False
        if project_id and (wb.get_config_value("flow_project_id") or "").strip() != project_id:
            wb.set_config_value("flow_project_id", project_id)
            changed = True
        if project_url and (wb.get_config_value("flow_project_url") or "").strip() != project_url:
            wb.set_config_value("flow_project_url", project_url)
            changed = True
        if token and (wb.get_config_value("flow_bearer_token") or "").strip() != token:
            wb.set_config_value("flow_bearer_token", token)
            changed = True
        if account_name and (wb.get_config_value("flow_account_name") or "").strip() != account_name:
            wb.set_config_value("flow_account_name", account_name)
            changed = True
        if changed and not wb.safe_save(max_retries=8):
            self.log("[AUTH] workbook restore from auth backup was deferred because Excel is busy", "WARN")

    def _detect_project_guard(self, project_dir: str | Path, wb: PromptWorkbook) -> Dict[str, Any]:
        workbook_assets = wb.detect_existing_flow_assets()
        fs_counts = {"refs": 0, "images": 0, "videos": 0, "thumbs": 0}
        project_dir = Path(project_dir)
        path_specs = (
            ("refs", project_dir / "nv", ("*.png", "*.jpg", "*.jpeg", "*.webp")),
            ("images", project_dir / "img", ("*.png", "*.jpg", "*.jpeg", "*.webp")),
            ("videos", project_dir / "vid", ("*.mp4", "*.mov", "*.mkv", "*.webm")),
            ("thumbs", project_dir / "thumb", ("*.png", "*.jpg", "*.jpeg", "*.webp")),
        )
        for stat_key, folder, patterns in path_specs:
            if not folder.exists():
                continue
            count = 0
            for pattern in patterns:
                count += sum(1 for _ in folder.glob(pattern))
            fs_counts[stat_key] = count
        has_generated = any(v for k, v in fs_counts.items() if k != "refs")
        return {
            "blocked": bool(workbook_assets.get("has_assets") or has_generated),
            "workbook": workbook_assets,
            "files": fs_counts,
        }

    def _write_auth_back(
        self,
        project_dir: str | Path,
        wb: PromptWorkbook,
        token: str,
        project_id: str,
        project_url: str,
        account_name: str,
    ) -> None:
        token_updated_at = str(int(time.time()))
        self.persist_cached_auth(
            project_dir,
            token=token,
            project_id=project_id,
            project_url=project_url,
            account_name=account_name,
            token_updated_at=token_updated_at,
        )
        existing_project_id = (wb.get_config_value("flow_project_id") or "").strip()
        reset_stats = wb.reset_flow_media_cache_if_project_changed(existing_project_id, project_id)
        if reset_stats.get("changed"):
            self.log(
                "[AUTH] reset workbook cache after project change "
                f"(chars={reset_stats['characters_reset']}, scenes={reset_stats['scenes_reset']}, thumbs={reset_stats['thumbnails_reset']})",
                "WARN",
            )
        wb.set_config_value("flow_bearer_token", token)
        wb.set_config_value("flow_project_id", project_id)
        wb.set_config_value("flow_project_url", project_url)
        wb.set_config_value("flow_account_name", account_name)
        wb.set_config_value("flow_token_updated_at", token_updated_at)
        if not wb.safe_save(max_retries=8):
            self.log("[AUTH] workbook save deferred; auth backup sidecar is still up to date", "WARN")

    @staticmethod
    def _normalize_project_url(project_id: str, project_url: str) -> str:
        project_id = (project_id or "").strip()
        project_url = (project_url or "").strip()
        if project_id:
            return f"https://labs.google/fx/vi/tools/flow/project/{project_id}"
        return project_url

    def ensure_auth(
        self,
        project_dir: str | Path,
        wb: PromptWorkbook,
        force_refresh: bool = False,
        keep_chrome_open: bool = False,
        server_name: str = "",
    ) -> Dict[str, str]:
        if not self.is_enabled():
            return {"ok": "", "error": "auto auth disabled"}

        project_dir = Path(project_dir)
        existing_auth = self._resolve_existing_auth(project_dir, wb)
        existing_pid = existing_auth.get("project_id", "")
        existing_url = existing_auth.get("project_url", "")
        account_name = existing_auth.get("account_name", "")
        if existing_auth.get("source") == "backup" and (existing_pid or existing_url):
            self.log(f"[AUTH] {project_dir.name}: restoring project identity from auth backup", "WARN")
            self._restore_auth_to_workbook(wb, existing_auth)
        if not existing_pid and not existing_url and not keep_chrome_open:
            guard = self._detect_project_guard(project_dir, wb)
            if guard["blocked"]:
                workbook_stats = guard["workbook"]
                file_stats = guard["files"]
                return {
                    "ok": "",
                    "error": (
                        "project guard: ma nay da co du lieu/media dang do, tu choi tao project moi "
                        f"(wb: char_media={workbook_stats['character_media']}, scene_media={workbook_stats['scene_media']}, "
                        f"scene_img={workbook_stats['scene_images']}, scene_vid={workbook_stats['scene_videos']}, "
                        f"thumb={workbook_stats['thumbnail_images']}; "
                        f"files: refs={file_stats['refs']}, img={file_stats['images']}, vid={file_stats['videos']}, thumb={file_stats['thumbs']}). "
                        "Can khoi phuc flow_project_id/project_url tu backup thay vi tao project moi."
                    ),
                }

        account = self.pick_account(account_name)
        if not account:
            return {"ok": "", "error": "no enabled flow accounts configured"}

        if force_refresh and (existing_pid or existing_url):
            self.log(
                f"[AUTH] {project_dir.name}: "
                "refresh token via new throwaway project (keep old project identity)",
                "INFO",
            )
        else:
            self.log(
                f"[AUTH] {project_dir.name}: "
                + ("refresh token from existing project" if (existing_pid or existing_url) else "create new project + get token"),
                "INFO",
            )

        # Mode: extension (FlowKit agent API) or chrome (UI automation)
        auth_mode = str(self.settings.get("flow_auth_mode", "chrome")).strip().lower()
        if auth_mode == "extension":
            return self._ensure_auth_via_extension(project_dir, wb, existing_pid, existing_url, account,
                                                   server_name=server_name, force_refresh=force_refresh)

        # Mode: chrome (original UI automation)
        bridge = self._bridge_for_account(account, project_dir)
        if force_refresh and (existing_pid or existing_url):
            result = bridge.acquire_token(
                existing_project_id="",
                existing_project_url="",
                keep_chrome_open=keep_chrome_open,
            )
        else:
            result = bridge.acquire_token(
                existing_project_id=existing_pid,
                existing_project_url=existing_url,
                keep_chrome_open=keep_chrome_open,
            )

        if not result.ok:
            return {
                "ok": "",
                "error": result.error or "flow auth failed",
                "token": "",
                "project_id": "",
                "project_url": "",
                "account_name": account.name,
            }

        token = result.token.strip()
        if force_refresh and (existing_pid or existing_url):
            pid = existing_pid
            purl = existing_url or self._normalize_project_url(pid, "")
        else:
            pid = result.project_id.strip()
            purl = self._normalize_project_url(pid, result.project_url.strip())
        self._write_auth_back(project_dir, wb, token, pid, purl, account.name)
        self.log(f"[AUTH] {project_dir.name}: token ready for project {pid[:8]}...", "SUCCESS")
        return {
            "ok": "1",
            "error": "",
            "token": token,
            "project_id": pid,
            "project_url": purl,
            "account_name": account.name,
        }

    def _ensure_auth_via_extension(self, project_dir, wb, existing_pid, existing_url, account,
                                     server_name: str = "", force_refresh: bool = False):
        """Get token/project via FlowKit extension API (no UI automation)."""
        try:
            from .flow_extension_auth import FlowExtensionAuth
        except ImportError:
            from modules.flow_extension_auth import FlowExtensionAuth

        servers = self.settings.get("local_server_list", [])

        # Find server index — by server_name from worker (correct), NOT by account (wrong)
        server_index = None
        if server_name:
            for i, srv in enumerate(servers):
                if srv.get("name", "") == server_name:
                    server_index = i
                    break
        if server_index is None:
            # Fallback: match by account
            for i, srv in enumerate(servers):
                srv_account = srv.get("flow_account_name", "")
                bundle = srv.get("flow_account_bundle", "")
                if account and (
                    (srv_account and srv_account == account.name) or
                    (account.email and account.email in bundle)
                ):
                    server_index = i
                    server_name = srv.get("name", f"sv{i+1}")
                    break
        if server_index is None:
            server_index = 0
            server_name = servers[0].get("name", "sv1") if servers else "sv1"

        api_port = 8100 + server_index

        # Check port directly
        agent_url = FlowExtensionAuth.get_agent_url_by_index(server_index)
        if not agent_url:
            self.log(f"[AUTH] Agent port {api_port} ({server_name}) not ready — starting all...", "INFO")
            FlowExtensionAuth.start_all_instances(servers, str(self.suite_root),
                                                   log_func=lambda m: self.log(m, "INFO"))
            # Wait for THIS port to become ready
            for _ in range(60):
                agent_url = FlowExtensionAuth.get_agent_url_by_index(server_index)
                if agent_url:
                    break
                time.sleep(2)
            if not agent_url:
                self.log(f"[AUTH] Agent port {api_port} still not ready for {server_name}", "WARN")
                return {"ok": "", "error": f"extension agent not ready for {server_name}"}

        ext_auth = FlowExtensionAuth(
            agent_url,
            log_func=lambda m: self.log(m, "INFO"),
            suite_root=str(self.suite_root),
        )

        # force_refresh: kill Chrome → setup_chrome lai → extension capture token moi
        # Y het luc ban dau: check login, re-login neu can, vao Flow, extension lay token
        if force_refresh:
            self.log(f"[AUTH] {server_name}: token expired — re-setup (y het luc dau)", "INFO")
            try:
                from .flow_extension_auth import _ExtensionInstanceManager
            except ImportError:
                from modules.flow_extension_auth import _ExtensionInstanceManager

            chrome_dir = Path(servers[server_index].get("chrome_path", "")).parent
            api_port = 8100 + server_index
            debug_port = 19200 + server_index

            # Kill Chrome cua instance nay
            _ExtensionInstanceManager._kill_chrome_for_dir(chrome_dir)
            time.sleep(2)

            # Re-run full start cho tat ca (PID lock xu ly, chi start instance chua ready)
            FlowExtensionAuth.start_all_instances(servers, str(self.suite_root),
                                                   log_func=lambda m: self.log(m, "INFO"))

            # Wait for agent ready again
            for _ in range(60):
                agent_url = FlowExtensionAuth.get_agent_url_by_index(server_index)
                if agent_url:
                    break
                time.sleep(2)
            if agent_url:
                ext_auth = FlowExtensionAuth(agent_url, log_func=lambda m: self.log(m, "INFO"),
                                             suite_root=str(self.suite_root))

        token = ext_auth.get_token()
        if not token:
            time.sleep(5)
            token = ext_auth.get_token()
        if not token:
            return {"ok": "", "error": "extension: no token"}

        pid = existing_pid
        if not pid:
            pid = ext_auth.get_project_id()
        if not pid:
            pid = ext_auth.ensure_project()
        if not pid:
            return {"ok": "", "error": "extension: no project"}

        purl = self._normalize_project_url(pid, "")
        self._write_auth_back(project_dir, wb, token, pid, purl, account.name if account else "")
        self.log(f"[AUTH] {project_dir.name}: token via extension for project {pid[:8]}...", "SUCCESS")
        return {
            "ok": "1",
            "error": "",
            "token": token,
            "project_id": pid,
            "project_url": purl,
            "account_name": account.name if account else "",
        }
