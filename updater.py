"""
VE3_SUITE Auto-Updater
======================
Tải bản mới từ GitHub mà không cần cài Git.
Chỉ cập nhật code, giữ nguyên config/credentials/PROJECTS.
"""

import io
import json
import os
import shutil
import tempfile
import time
import zipfile
from pathlib import Path
from urllib.request import urlopen, Request
from urllib.error import URLError

SUITE_ROOT = Path(__file__).resolve().parent
GITHUB_REPO = "manhthang1905-hub/VE3_SUITE"
GITHUB_API = f"https://api.github.com/repos/{GITHUB_REPO}"
GITHUB_ZIP = f"https://github.com/{GITHUB_REPO}/archive/refs/heads/main.zip"
VERSION_FILE = SUITE_ROOT / "VERSION"

PROTECTED_PATHS = {
    "PROJECTS",
    "config/suite_settings.json",
    "config/creds.json",
    "config/config.json",
    "config/flow_project_auth_cache.json",
    "tools/srt-to-excel/config/settings.yaml",
    "tools/ve3/config/settings.yaml",
    "tools/ve3/config/flow_accounts.yaml",
    "chrome_profiles",
    ".claude",
}

PROTECTED_PREFIXES = (
    "GoogleChromePortable",
    "PROJECTS/",
    "chrome_profiles/",
    ".claude/",
)


def _get_local_commit_count() -> int:
    """Get commit count from local git, return -1 if git unavailable."""
    import subprocess
    try:
        result = subprocess.run(
            ["git", "rev-list", "--count", "HEAD"],
            cwd=str(SUITE_ROOT), capture_output=True, text=True, timeout=5,
        )
        if result.returncode == 0:
            return int(result.stdout.strip())
    except Exception:
        pass
    return -1


def get_local_version() -> str:
    count = _get_local_commit_count()
    if count > 0:
        return f"1.0.{count}"
    try:
        return VERSION_FILE.read_text(encoding="utf-8").strip()
    except Exception:
        return "0.0.0"


def _get_remote_commit_count() -> int:
    """Get total commit count on main branch via GitHub API (no git needed)."""
    url = f"{GITHUB_API}/commits?sha=main&per_page=1"
    try:
        req = Request(url, headers={"User-Agent": "VE3-Suite-Updater"})
        with urlopen(req, timeout=10) as resp:
            link = resp.headers.get("Link", "")
            if 'rel="last"' in link:
                import re
                m = re.search(r'[&?]page=(\d+)>;\s*rel="last"', link)
                if m:
                    return int(m.group(1))
            body = resp.read().decode("utf-8")
            data = json.loads(body)
            if isinstance(data, list) and len(data) > 0:
                return 1
    except Exception:
        pass
    return -1


def get_remote_version() -> str:
    count = _get_remote_commit_count()
    if count > 0:
        return f"1.0.{count}"
    url = f"https://raw.githubusercontent.com/{GITHUB_REPO}/main/VERSION"
    try:
        req = Request(url, headers={"User-Agent": "VE3-Suite-Updater"})
        with urlopen(req, timeout=10) as resp:
            return resp.read().decode("utf-8").strip()
    except Exception:
        return ""


def check_update() -> dict:
    local = get_local_version()
    remote = get_remote_version()
    if not remote:
        return {"available": False, "local": local, "remote": "", "error": "Không thể kết nối GitHub"}
    try:
        local_n = int(local.rsplit(".", 1)[-1])
        remote_n = int(remote.rsplit(".", 1)[-1])
        has_update = remote_n > local_n
    except (ValueError, IndexError):
        has_update = remote != local
    return {"available": has_update, "local": local, "remote": remote, "error": ""}


def _is_protected(rel_path: str) -> bool:
    rel_norm = rel_path.replace("\\", "/")
    if rel_norm in PROTECTED_PATHS:
        return True
    for prefix in PROTECTED_PREFIXES:
        if rel_norm.startswith(prefix):
            return True
    return False


def _try_git_pull(progress_callback=None):
    """Try git pull first — much faster than ZIP download. Returns None if git unavailable."""
    import subprocess
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--git-dir"],
            cwd=str(SUITE_ROOT), capture_output=True, text=True, timeout=5,
        )
        if result.returncode != 0:
            return None
    except Exception:
        return None

    if progress_callback:
        progress_callback("Git pull...")
    try:
        result = subprocess.run(
            ["git", "pull", "--ff-only", "origin", "main"],
            cwd=str(SUITE_ROOT), capture_output=True, text=True, timeout=120,
        )
        if result.returncode == 0:
            ver = get_local_version()
            try:
                VERSION_FILE.write_text(ver + "\n", encoding="utf-8")
            except Exception:
                pass
            return {"success": True, "version": ver, "updated": 0, "skipped": 0, "errors": [],
                    "method": "git"}
        # ff-only failed (diverged) — try reset
        subprocess.run(
            ["git", "fetch", "--depth=1", "origin", "main"],
            cwd=str(SUITE_ROOT), capture_output=True, text=True, timeout=120,
        )
        result = subprocess.run(
            ["git", "reset", "--hard", "origin/main"],
            cwd=str(SUITE_ROOT), capture_output=True, text=True, timeout=30,
        )
        if result.returncode == 0:
            ver = get_local_version()
            try:
                VERSION_FILE.write_text(ver + "\n", encoding="utf-8")
            except Exception:
                pass
            return {"success": True, "version": ver, "updated": 0, "skipped": 0, "errors": [],
                    "method": "git-reset"}
    except Exception:
        pass
    return None


def download_and_apply(progress_callback=None) -> dict:
    def _progress(msg):
        if progress_callback:
            progress_callback(msg)

    # Try git pull first — fast, only downloads diff
    git_result = _try_git_pull(progress_callback=_progress)
    if git_result is not None:
        return git_result

    # Fallback: download full ZIP (for machines without git)
    _progress("Đang tải bản mới từ GitHub...")
    try:
        req = Request(GITHUB_ZIP, headers={"User-Agent": "VE3-Suite-Updater"})
        with urlopen(req, timeout=120) as resp:
            zip_data = resp.read()
    except Exception as e:
        return {"success": False, "error": f"Tải thất bại: {e}"}

    _progress("Đang giải nén...")
    try:
        zf = zipfile.ZipFile(io.BytesIO(zip_data))
    except Exception as e:
        return {"success": False, "error": f"File ZIP lỗi: {e}"}

    entries = zf.namelist()
    if not entries:
        return {"success": False, "error": "ZIP trống"}

    top_dir = entries[0].split("/")[0] + "/"

    updated = 0
    skipped = 0
    errors = []

    _progress("Đang cập nhật files...")
    for entry in entries:
        if entry.endswith("/"):
            continue
        rel_path = entry[len(top_dir):]
        if not rel_path:
            continue
        if _is_protected(rel_path):
            skipped += 1
            continue

        target = SUITE_ROOT / rel_path
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            data = zf.read(entry)
            target.write_bytes(data)
            updated += 1
        except Exception as e:
            errors.append(f"{rel_path}: {e}")

    zf.close()

    remote_ver = get_remote_version() or "unknown"

    try:
        VERSION_FILE.write_text(remote_ver + "\n", encoding="utf-8")
    except Exception:
        pass

    _progress(f"Hoàn tất! v{remote_ver} — {updated} files cập nhật, {skipped} files giữ nguyên")

    return {
        "success": True,
        "version": remote_ver,
        "updated": updated,
        "skipped": skipped,
        "errors": errors,
    }


if __name__ == "__main__":
    print(f"Local version:  {get_local_version()}")
    info = check_update()
    print(f"Remote version: {info['remote'] or 'N/A'}")
    if info.get("error"):
        print(f"Error: {info['error']}")
    elif info["available"]:
        print("Có bản cập nhật mới!")
        answer = input("Cập nhật ngay? (y/n): ").strip().lower()
        if answer == "y":
            result = download_and_apply(progress_callback=print)
            if result["success"]:
                print(f"Cập nhật thành công! v{result['version']}")
            else:
                print(f"Lỗi: {result['error']}")
    else:
        print("Đang dùng phiên bản mới nhất.")
