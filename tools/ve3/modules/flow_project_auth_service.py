from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Callable, Dict, Optional

try:
    import yaml
except Exception:  # pragma: no cover
    yaml = None

from .flow_auto_token_ref import FlowAutoTokenRef


LogFunc = Callable[[str, str], None]


@dataclass
class FlowAccount:
    name: str
    chrome_path: str
    profile_path: str = ""
    enabled: bool = True
    email: str = ""
    notes: str = ""


@dataclass
class FlowProjectSession:
    account_name: str = ""
    project_code: str = ""
    project_id: str = ""
    project_url: str = ""
    token: str = ""
    acquired_at: float = 0.0
    last_error: str = ""

    @property
    def usable(self) -> bool:
        return bool(self.project_id and self.token)


class FlowProjectAuthService:
    """
    New standalone auth service for VE3_SUITE.

    Supported cases:
    - New project code: use the right account, create new Flow project, capture full project_id + token.
    - Existing project code: open the old Flow project in the same Chrome profile, refresh token only.
    """

    def __init__(
        self,
        suite_root: Path | str,
        config_path: Optional[Path | str] = None,
        cache_path: Optional[Path | str] = None,
        log_func: Optional[LogFunc] = None,
    ):
        self.suite_root = Path(suite_root)
        self.config_path = Path(config_path) if config_path else self.suite_root / "tools" / "ve3" / "config" / "flow_accounts.yaml"
        self.cache_path = Path(cache_path) if cache_path else self.suite_root / "config" / "flow_project_auth_cache.json"
        self.log_func = log_func or (lambda msg, level="INFO": None)
        self.cache_path.parent.mkdir(parents=True, exist_ok=True)

    def log(self, msg: str, level: str = "INFO") -> None:
        self.log_func(msg, level)

    def load_accounts(self) -> list[FlowAccount]:
        if yaml is None:
            raise RuntimeError("Missing PyYAML")
        if not self.config_path.exists():
            return []
        data = yaml.safe_load(self.config_path.read_text(encoding="utf-8")) or {}
        accounts = []
        for item in data.get("accounts", []):
            try:
                acc = FlowAccount(**item)
            except TypeError:
                continue
            if acc.enabled:
                accounts.append(acc)
        return accounts

    def load_cache(self) -> Dict[str, Dict]:
        if not self.cache_path.exists():
            return {"projects": {}}
        try:
            data = json.loads(self.cache_path.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                return {"projects": {}}
            data.setdefault("projects", {})
            return data
        except Exception:
            return {"projects": {}}

    def save_cache(self, data: Dict[str, Dict]) -> None:
        self.cache_path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")

    def get_session(self, project_code: str) -> FlowProjectSession:
        raw = self.load_cache().get("projects", {}).get(project_code, {}) or {}
        return FlowProjectSession(**{k: raw.get(k, getattr(FlowProjectSession(), k)) for k in FlowProjectSession.__dataclass_fields__.keys()})

    def save_session(self, session: FlowProjectSession) -> None:
        cache = self.load_cache()
        cache.setdefault("projects", {})[session.project_code] = asdict(session)
        self.save_cache(cache)

    def pick_account(self, account_name: str = "") -> Optional[FlowAccount]:
        accounts = self.load_accounts()
        if not accounts:
            return None
        if account_name:
            for account in accounts:
                if account.name == account_name:
                    return account
        return accounts[0]

    def acquire_for_new_project(self, project_code: str, account_name: str = "", prompt: str = "") -> FlowProjectSession:
        account = self.pick_account(account_name)
        if not account:
            return FlowProjectSession(project_code=project_code, last_error="No enabled Flow account configured")

        self.log(f"[AUTH] New project auth for {project_code} using account {account.name}", "INFO")
        extractor = FlowAutoTokenRef(
            chrome_path=account.chrome_path,
            profile_path=account.profile_path,
            callback=lambda msg: self.log(msg, "INFO"),
        )
        token, project_id, error = extractor.extract_token(prompt=prompt)
        session = FlowProjectSession(
            account_name=account.name,
            project_code=project_code,
            project_id=str(project_id or ""),
            project_url=f"https://labs.google/fx/vi/tools/flow/project/{project_id}" if project_id else "",
            token=str(token or ""),
            acquired_at=time.time(),
            last_error=str(error or ""),
        )
        self.save_session(session)
        return session

    def refresh_for_existing_project(self, project_code: str, prompt: str = "") -> FlowProjectSession:
        session = self.get_session(project_code)
        if not session.account_name:
            session.last_error = "Project has no cached Flow account"
            return session
        account = self.pick_account(session.account_name)
        if not account:
            session.last_error = f"Configured Flow account not found: {session.account_name}"
            return session

        self.log(f"[AUTH] Refresh token for {project_code} via account {account.name}", "INFO")
        extractor = FlowAutoTokenRef(
            chrome_path=account.chrome_path,
            profile_path=account.profile_path,
            callback=lambda msg: self.log(msg, "INFO"),
        )
        token, project_id, error = extractor.extract_token(
            project_id=session.project_id,
            project_url=session.project_url,
            prompt=prompt,
        )
        refreshed = FlowProjectSession(
            account_name=account.name,
            project_code=project_code,
            project_id=str(project_id or session.project_id),
            project_url=f"https://labs.google/fx/vi/tools/flow/project/{project_id or session.project_id}" if (project_id or session.project_id) else "",
            token=str(token or ""),
            acquired_at=time.time(),
            last_error=str(error or ""),
        )
        self.save_session(refreshed)
        return refreshed
