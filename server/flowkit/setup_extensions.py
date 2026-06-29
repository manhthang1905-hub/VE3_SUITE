"""
Setup script — registers FlowKit extensions in Chrome Preferences.

Run this ONCE before first use (or after clearing Chrome data).
Must run with Chrome CLOSED.

Usage:
    python setup_extensions.py
"""
import json
import os
import sys
import time
from pathlib import Path

import yaml

BASE_DIR = Path(__file__).parent
CONFIG_PATH = Path(os.environ.get("FLOWKIT_CONFIG") or (BASE_DIR / "config.yaml"))

if not CONFIG_PATH.exists():
    print(f"[FATAL] config.yaml not found: {CONFIG_PATH}")
    sys.exit(1)
with open(CONFIG_PATH) as f:
    CONFIG = yaml.safe_load(f)


def get_extension_id_from_path(ext_path: str) -> str:
    """Generate deterministic extension ID from path (Chrome's algorithm).

    Chrome converts path to lowercase, then maps first 32 chars of hex-encoded
    path to a-p alphabet. For unpacked extensions, it uses the path hash.
    We'll use a simpler approach: just register with a known ID pattern.
    """
    import hashlib
    path_bytes = str(ext_path).lower().encode('utf-8')
    digest = hashlib.sha256(path_bytes).hexdigest()[:32]
    # Convert hex to Chrome's a-p encoding
    ext_id = ""
    for ch in digest:
        ext_id += chr(ord('a') + int(ch, 16))
    return ext_id


def register_extension(prefs_path: Path, ext_dir: Path, ext_name: str):
    """Register extension in Chrome's Preferences file."""
    if not prefs_path.exists():
        print(f"  [WARN] Preferences not found: {prefs_path}")
        print(f"  Creating minimal Preferences file...")
        prefs_path.parent.mkdir(parents=True, exist_ok=True)
        prefs = {}
    else:
        with open(prefs_path, 'r', encoding='utf-8') as f:
            prefs = json.load(f)

    # Ensure extensions.settings exists
    if "extensions" not in prefs:
        prefs["extensions"] = {}
    if "settings" not in prefs["extensions"]:
        prefs["extensions"]["settings"] = {}

    ext_dir_str = str(ext_dir.resolve()).replace("\\", "/")
    ext_id = get_extension_id_from_path(ext_dir_str)

    # Check if already registered
    if ext_id in prefs["extensions"]["settings"]:
        existing = prefs["extensions"]["settings"][ext_id]
        if existing.get("path") == ext_dir_str:
            print(f"  Already registered: {ext_name} ({ext_id[:8]}...)")
            return ext_id

    # Read manifest for version
    manifest_path = ext_dir / "manifest.json"
    if manifest_path.exists():
        with open(manifest_path) as f:
            manifest = json.load(f)
        version = manifest.get("version", "1.0.0")
    else:
        version = "1.0.0"

    # Register extension entry
    prefs["extensions"]["settings"][ext_id] = {
        "active_permissions": {
            "api": ["alarms", "browsingData", "cookies", "declarativeNetRequest",
                    "management", "scripting", "sidePanel", "storage", "tabs", "webRequest"],
            "explicit_host": [
                "https://labs.google/*",
                "https://aisandbox-pa.googleapis.com/*",
                f"http://127.0.0.1:{ext_name.split('_')[-1] if '_' in ext_name else '8100'}/*",
            ],
        },
        "commands": {},
        "content_settings": [],
        "creation_flags": 1,
        "events": [],
        "from_webstore": False,
        "granted_permissions": {
            "api": ["alarms", "browsingData", "cookies", "declarativeNetRequest",
                    "management", "scripting", "sidePanel", "storage", "tabs", "webRequest"],
            "explicit_host": [
                "https://labs.google/*",
                "https://aisandbox-pa.googleapis.com/*",
                f"http://127.0.0.1:{ext_name.split('_')[-1] if '_' in ext_name else '8100'}/*",
            ],
        },
        "incognito_content_settings": [],
        "incognito_preferences": {},
        "install_time": str(int(time.time() * 1000000)),  # microseconds since epoch
        "location": 4,  # LOAD (unpacked)
        "manifest": {
            "manifest_version": 3,
            "name": ext_name,
            "version": version,
        },
        "path": ext_dir_str,
        "state": 1,  # ENABLED
        "was_installed_by_default": False,
        "was_installed_by_oem": False,
    }

    with open(prefs_path, 'w', encoding='utf-8') as f:
        json.dump(prefs, f, indent=2)

    print(f"  Registered: {ext_name} -> {ext_id[:12]}...")
    return ext_id


def main():
    print("=" * 50)
    print("  FlowKit Extension Setup")
    print("=" * 50)

    instances = CONFIG.get("instances", [])
    for inst in instances:
        if not inst.get("enabled", True):
            continue

        name = inst["name"]
        ext_dir = BASE_DIR / inst["extension_dir"]
        profile_dir = BASE_DIR / inst["profile_dir"]
        prefs_path = profile_dir / "Default" / "Preferences"

        print(f"\n[{name}]")
        print(f"  Extension: {ext_dir}")
        print(f"  Profile: {profile_dir}")

        if not ext_dir.exists():
            print(f"  [ERROR] Extension dir not found!")
            continue

        # Also update Secure Preferences if it exists
        secure_prefs_path = profile_dir / "Default" / "Secure Preferences"

        ext_id = register_extension(prefs_path, ext_dir, name)

        # Disable extension verification (so Chrome doesn't reject our registration)
        if prefs_path.exists():
            with open(prefs_path, 'r', encoding='utf-8') as f:
                prefs = json.load(f)
            if "extensions" not in prefs:
                prefs["extensions"] = {}
            prefs["extensions"]["alerts"] = {"initialized": True}
            # Disable install verification
            if "profile" not in prefs:
                prefs["profile"] = {}
            prefs["profile"]["extensions_install_verification"] = False
            with open(prefs_path, 'w', encoding='utf-8') as f:
                json.dump(prefs, f, indent=2)

    print("\n" + "=" * 50)
    print("  Setup complete!")
    print("  Now start Chrome to activate extensions.")
    print("  NOTE: You may need to enable the extension in")
    print("  chrome://extensions if it shows as 'disabled'")
    print("=" * 50)


if __name__ == "__main__":
    main()
