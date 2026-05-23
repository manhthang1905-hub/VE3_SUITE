#!/usr/bin/env python3
"""
Settings Sync - Dong bo settings tu Master xuong VM
====================================================

Master dat file settings vao Z:\AUTO\_vm_settings\
VM tu dong doc va merge vao local config/settings.yaml

Cau truc:
    Z:\AUTO\_vm_settings\
        _global.yaml     ← ap dung cho TAT CA VM
        KA4-T3.yaml      ← ap dung rieng cho VM KA4-T3 (override global)

Do uu tien (thap → cao):
    1. Local settings.yaml (mac dinh)
    2. _global.yaml (master dat cho tat ca VM)
    3. {vm_id}.yaml (master dat rieng cho VM nay)

Chi merge cac key CO TRONG file master. Key khong co se giu nguyen local.
"""

import yaml
import time
import shutil
from pathlib import Path
from typing import Dict, Any, Optional, Callable, List

# Settings duoc phep sync tu master (whitelist)
# Tranh master vo tinh ghi de sensitive keys (api keys, paths...)
SYNCABLE_KEYS = [
    'generation_mode',
    'chrome_model_index',
    'topic',
    'excel_mode',
    'video_mode',
    'video_model',
    'flow_aspect_ratio',
    'video_aspect_ratio',
    'browser_headless',
    'browser_generate_timeout',
    'browser_delay_between_prompts',
    'retry_count',
    'max_scenes_per_account',
    'early_video_start',
    'flow_delay',
    'flow_image_count',
    'flow_timeout',
    'force_model',
    'ken_burns_intensity',
    'max_parallel_api',
    'max_scene_duration',
    'min_scene_duration',
    'parallel_browsers',
    'parallel_chrome',
    'video_compose_mode',
    'video_count',
    'video_generation_mode',
    'video_paygate_tier',
    'wait_timeout',
    'local_server_enabled',
    'local_server_url',
    'local_server_list',
    'distributed_mode',
    'use_proxy',
    # IPv6 rotation (nested dict)
    'ipv6_rotation',
    # MikroTik pool (nested dict)
    'mikrotik',
    # Webshare proxy (nested dict)
    'webshare_proxy',
]

# Folder name tren master: control/settings/ (cung cap voi commands, status)
SETTINGS_FOLDER = "control/settings"


class SettingsSync:
    """Dong bo settings tu Master xuong VM."""

    def __init__(
        self,
        vm_id: str,
        local_config_path: Path,
        log_func: Callable = print,
    ):
        """
        Args:
            vm_id: Ma may (VD: KA4-T3, AR8-T1)
            local_config_path: Duong dan den config/settings.yaml
            log_func: Ham log
        """
        self.vm_id = vm_id
        self.local_config_path = Path(local_config_path)
        self.log = log_func
        self._last_sync_time = 0
        self._last_global_mtime = 0
        self._last_vm_mtime = 0

    def sync_from_master(self, auto_path: Path) -> Dict[str, Any]:
        """
        Doc settings tu master va merge vao local.

        Args:
            auto_path: Duong dan AUTO (VD: Z:\\AUTO)

        Returns:
            Dict cac key da thay doi {"key": {"old": ..., "new": ...}}
            Rong neu khong co thay doi
        """
        if not auto_path:
            return {}

        # Structure: Z:\AUTO\ve3-tool-simple\control\settings\
        settings_dir = Path(auto_path) / "ve3-tool-simple" / SETTINGS_FOLDER

        if not settings_dir.exists():
            return {}

        # Doc file master settings
        global_file = settings_dir / "_global.yaml"
        vm_file = settings_dir / f"{self.vm_id}.yaml"

        master_settings = {}

        # Layer 1: Global settings (cho tat ca VM)
        if global_file.exists():
            try:
                mtime = global_file.stat().st_mtime
                with open(global_file, 'r', encoding='utf-8') as f:
                    global_cfg = yaml.safe_load(f) or {}
                master_settings.update(global_cfg)
            except Exception as e:
                self.log(f"[SYNC] Loi doc _global.yaml: {e}")

        # Layer 2: Per-VM settings (override global)
        if vm_file.exists():
            try:
                mtime = vm_file.stat().st_mtime
                with open(vm_file, 'r', encoding='utf-8') as f:
                    vm_cfg = yaml.safe_load(f) or {}
                master_settings.update(vm_cfg)
            except Exception as e:
                self.log(f"[SYNC] Loi doc {self.vm_id}.yaml: {e}")

        if not master_settings:
            return {}

        # Loc chi cac key duoc phep sync
        filtered = {}
        for key in SYNCABLE_KEYS:
            if key in master_settings:
                filtered[key] = master_settings[key]

        if not filtered:
            return {}

        # Doc local settings hien tai
        local_cfg = {}
        if self.local_config_path.exists():
            try:
                with open(self.local_config_path, 'r', encoding='utf-8') as f:
                    local_cfg = yaml.safe_load(f) or {}
            except Exception:
                local_cfg = {}

        # Tim cac key thay doi
        changes = {}
        for key, new_val in filtered.items():
            old_val = local_cfg.get(key)
            if old_val != new_val:
                changes[key] = {"old": old_val, "new": new_val}
                local_cfg[key] = new_val

        if not changes:
            return {}

        # Ghi lai local settings
        try:
            with open(self.local_config_path, 'w', encoding='utf-8') as f:
                yaml.dump(local_cfg, f, default_flow_style=False, allow_unicode=True)

            self.log(f"[SYNC] Da cap nhat {len(changes)} settings tu master:")
            for key, change in changes.items():
                self.log(f"[SYNC]   {key}: {change['old']} → {change['new']}")

            self._last_sync_time = time.time()
            return changes

        except Exception as e:
            self.log(f"[SYNC] Loi ghi settings.yaml: {e}")
            return {}

    def check_and_sync(self, auto_path: Path, min_interval: int = 60) -> Dict[str, Any]:
        """
        Check va sync neu co thay doi (rate-limited).

        Args:
            auto_path: Duong dan AUTO
            min_interval: Thoi gian toi thieu giua 2 lan sync (giay)

        Returns:
            Dict cac key da thay doi
        """
        now = time.time()
        if now - self._last_sync_time < min_interval:
            return {}

        if not auto_path:
            return {}

        # Structure: Z:\AUTO\ve3-tool-simple\control\settings\
        settings_dir = Path(auto_path) / "ve3-tool-simple" / SETTINGS_FOLDER
        if not settings_dir.exists():
            self._last_sync_time = now
            return {}

        # Check file modification time de tranh doc khi khong can
        global_file = settings_dir / "_global.yaml"
        vm_file = settings_dir / f"{self.vm_id}.yaml"

        global_mtime = global_file.stat().st_mtime if global_file.exists() else 0
        vm_mtime = vm_file.stat().st_mtime if vm_file.exists() else 0

        if global_mtime == self._last_global_mtime and vm_mtime == self._last_vm_mtime:
            self._last_sync_time = now
            return {}

        self._last_global_mtime = global_mtime
        self._last_vm_mtime = vm_mtime

        return self.sync_from_master(auto_path)

    @staticmethod
    def create_master_settings(
        auto_path: Path,
        settings: Dict[str, Any],
        vm_id: str = None,
        log_func: Callable = print,
    ) -> bool:
        """
        Tao file settings tren master (goi tu Master GUI).

        Args:
            auto_path: Duong dan AUTO (VD: Z:\\AUTO)
            settings: Dict settings can set
            vm_id: None = global (tat ca VM), "KA4-T3" = rieng VM do
            log_func: Ham log

        Returns:
            True neu thanh cong
        """
        # Structure: Z:\AUTO\ve3-tool-simple\control\settings\
        settings_dir = Path(auto_path) / "ve3-tool-simple" / SETTINGS_FOLDER
        settings_dir.mkdir(parents=True, exist_ok=True)

        if vm_id:
            target_file = settings_dir / f"{vm_id}.yaml"
            label = f"VM {vm_id}"
        else:
            target_file = settings_dir / "_global.yaml"
            label = "GLOBAL (tat ca VM)"

        try:
            # Loc chi cac key hop le
            filtered = {k: v for k, v in settings.items() if k in SYNCABLE_KEYS}
            if not filtered:
                log_func(f"[SYNC] Khong co key hop le de sync!")
                return False

            # Merge voi file hien tai (khong ghi de key cu)
            existing = {}
            if target_file.exists():
                try:
                    with open(target_file, 'r', encoding='utf-8') as f:
                        existing = yaml.safe_load(f) or {}
                except Exception:
                    pass

            merged = {**existing, **filtered}

            with open(target_file, 'w', encoding='utf-8') as f:
                yaml.dump(merged, f, default_flow_style=False, allow_unicode=True)

            log_func(f"[SYNC] Da ghi {len(filtered)} settings cho {label}:")
            for key, val in filtered.items():
                log_func(f"[SYNC]   {key}: {val}")
            return True

        except Exception as e:
            log_func(f"[SYNC] Loi ghi master settings: {e}")
            return False

    @staticmethod
    def list_vm_settings(auto_path: Path) -> Dict[str, Dict]:
        """
        Liet ke tat ca VM settings tren master.

        Returns:
            {"_global": {...}, "KA4-T3": {...}, ...}
        """
        result = {}
        # Structure: Z:\AUTO\ve3-tool-simple\control\settings\
        settings_dir = Path(auto_path) / "ve3-tool-simple" / SETTINGS_FOLDER
        if not settings_dir.exists():
            return result

        for f in settings_dir.glob("*.yaml"):
            name = f.stem  # "_global" or "KA4-T3"
            try:
                with open(f, 'r', encoding='utf-8') as fh:
                    result[name] = yaml.safe_load(fh) or {}
            except Exception:
                pass

        return result

    @staticmethod
    def delete_vm_settings(auto_path: Path, vm_id: str = None) -> bool:
        """
        Xoa settings cua 1 VM hoac global.

        Args:
            vm_id: None = xoa global, "KA4-T3" = xoa rieng VM do
        """
        # Structure: Z:\AUTO\ve3-tool-simple\control\settings\
        settings_dir = Path(auto_path) / "ve3-tool-simple" / SETTINGS_FOLDER
        if vm_id:
            target = settings_dir / f"{vm_id}.yaml"
        else:
            target = settings_dir / "_global.yaml"

        if target.exists():
            target.unlink()
            return True
        return False
