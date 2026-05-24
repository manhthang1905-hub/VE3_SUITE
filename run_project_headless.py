#!/usr/bin/env python3
"""Run one VE3 suite project from MP3/SRT to Excel to images/videos."""

import os
import re
import sys
import time
import traceback
from pathlib import Path

import yaml


SUITE_ROOT = Path(__file__).resolve().parent
SRT_TOOL_DIR = SUITE_ROOT / "tools" / "srt-to-excel"
VE3_TOOL_DIR = SUITE_ROOT / "tools" / "ve3"


def log(message, level="INFO"):
    stamp = time.strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{stamp}] {level:<7} {message}", flush=True)


def load_yaml(path):
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


TOPIC_MAPPING = {
    "story": ("story", "small"),
    "truyen": ("story", "small"),
    "truyện": ("story", "small"),
    "psychology": ("psychology", "full"),
    "tam ly": ("psychology", "full"),
    "tâm lý": ("psychology", "full"),
    "finance": ("finance", "full"),
    "tai chinh": ("finance", "full"),
    "tài chính": ("finance", "full"),
    "success": ("success", "full"),
    "phat trien ban than": ("success", "full"),
    "phát triển bản thân": ("success", "full"),
}

# Infer topic from project code prefix as last-resort fallback
CODE_PREFIX_TOPIC = {
    "TL": "psychology",
    "TH": "finance",
    "MT": "success",
    "KA": "story",
    "TA": "story",
}


def infer_topic_from_code(code: str) -> str:
    """Return topic string based on project code prefix, or empty string."""
    import re
    m = re.match(r"^([A-Za-z]+)", str(code or ""))
    if m:
        prefix = m.group(1).upper()
        return CODE_PREFIX_TOPIC.get(prefix, "")
    return ""


# Cache for NGUON sheet data (loaded once per process)
_nguon_sheet_cache = None


def _load_nguon_sheet() -> list:
    """Load sheet NGUON from Google Sheets using service account credentials."""
    import socket
    # Force IPv4 to avoid IPv6 timeout issues
    _orig_getaddrinfo = socket.getaddrinfo
    def _ipv4_only(*args, **kwargs):
        return [r for r in _orig_getaddrinfo(*args, **kwargs) if r[0] == socket.AF_INET]
    socket.getaddrinfo = _ipv4_only

    config_dir = SUITE_ROOT / "config"
    config_file = config_dir / "config.json"
    if not config_file.exists():
        socket.getaddrinfo = _orig_getaddrinfo
        return []
    try:
        import json as _json
        cfg = _json.loads(config_file.read_text(encoding="utf-8"))
        sa_path = cfg.get("SERVICE_ACCOUNT_JSON") or cfg.get("CREDENTIAL_PATH") or "creds.json"
        spreadsheet_name = cfg.get("SPREADSHEET_NAME")
        if not spreadsheet_name:
            socket.getaddrinfo = _orig_getaddrinfo
            return []
        sa_file = Path(sa_path)
        if not sa_file.exists():
            sa_file = config_dir / sa_path
        if not sa_file.exists():
            socket.getaddrinfo = _orig_getaddrinfo
            return []
        import gspread
        from google.oauth2.service_account import Credentials
        scopes = [
            "https://www.googleapis.com/auth/spreadsheets.readonly",
            "https://www.googleapis.com/auth/drive.readonly",
        ]
        creds = Credentials.from_service_account_file(str(sa_file), scopes=scopes)
        gc = gspread.authorize(creds)
        ws = gc.open(spreadsheet_name).worksheet("NGUON")
        data = ws.get_all_values()
        log(f"Loaded sheet NGUON: {len(data)} rows")
        return data
    except Exception as e:
        log(f"Cannot load sheet NGUON: {e}", "WARN")
        return []
    finally:
        socket.getaddrinfo = _orig_getaddrinfo


def lookup_topic_from_nguon_sheet(code: str) -> str:
    """Lookup topic for project code from Google Sheet NGUON (Col G=code, Col S=topic).
    
    Uses a 15s timeout to avoid blocking the pipeline if the API is slow.
    """
    import concurrent.futures
    global _nguon_sheet_cache

    def _do_lookup():
        global _nguon_sheet_cache
        if _nguon_sheet_cache is None:
            _nguon_sheet_cache = _load_nguon_sheet()
        if not _nguon_sheet_cache:
            return ""
        code_upper = code.upper()
        for row in _nguon_sheet_cache:
            if len(row) > 18:
                cell_g = str(row[6]).strip().upper()
                if cell_g == code_upper:
                    topic = str(row[18]).strip()
                    if topic:
                        log(f"Sheet NGUON: {code} -> topic='{topic}'")
                        return topic
        log(f"Sheet NGUON: no topic found for {code}", "WARN")
        return ""

    executor = concurrent.futures.ThreadPoolExecutor(max_workers=1)
    try:
        fut = executor.submit(_do_lookup)
        return fut.result(timeout=15)
    except concurrent.futures.TimeoutError:
        log(f"Sheet NGUON: timeout (15s) looking up {code} - fallback to prefix", "WARN")
        executor.shutdown(wait=False)
        return ""
    except Exception as e:
        log(f"Error looking up topic from sheet: {e}", "WARN")
        return ""


def lookup_reference_channel_from_nguon_sheet(code: str) -> str:
    """Lookup reference channel from NGUON (Col G=code, Col L=channel) with retries."""
    import concurrent.futures
    global _nguon_sheet_cache

    def _do_lookup():
        global _nguon_sheet_cache
        if _nguon_sheet_cache is None or not _nguon_sheet_cache:
            for attempt in range(1, 4):
                _nguon_sheet_cache = _load_nguon_sheet()
                if _nguon_sheet_cache:
                    break
                log(f"Sheet NGUON: retry {attempt}/3 for reference_channel {code}", "WARN")
                time.sleep(1.5 * attempt)
        rows = _nguon_sheet_cache or []
        if not rows:
            return ""
        code_upper = str(code or "").strip().upper()
        headers = [str(x or "").strip().lower() for x in (rows[0] if rows else [])]
        channel_header_terms = {
            "reference_channel", "reference channel", "kenh", "kênh",
            "channel", "ma kenh", "mã kênh", "channel code",
        }
        channel_cols = [idx for idx, name in enumerate(headers) if name in channel_header_terms]
        def valid_channel(text):
            text = str(text or "").strip()
            if not text:
                return ""
            for ref_dir in ["psychology", "finance", "success"]:
                root = SRT_TOOL_DIR / "reference_characters" / ref_dir
                if (root / text / "nv1.png").exists() or (root / text / "style.yaml").exists():
                    return text
            return ""

        for row in rows[1:] if rows else []:
            if len(row) <= 6 or str(row[6]).strip().upper() != code_upper:
                continue
            # NGUON fixed mapping: Col G = content code, Col L = channel/reference_channel.
            if len(row) > 11:
                found = valid_channel(row[11])
                if found:
                    log(f"Sheet NGUON: {code} -> reference_channel='{found}' (Col L)")
                    return found
            for idx in channel_cols:
                if idx < len(row):
                    found = valid_channel(row[idx])
                    if found:
                        log(f"Sheet NGUON: {code} -> reference_channel='{found}'")
                        return found
            for cell in row:
                found = valid_channel(cell)
                if found:
                    log(f"Sheet NGUON: {code} -> reference_channel='{found}'")
                    return found
            return ""
        return ""

    executor = concurrent.futures.ThreadPoolExecutor(max_workers=1)
    try:
        return executor.submit(_do_lookup).result(timeout=15)
    except concurrent.futures.TimeoutError:
        log(f"Sheet NGUON: timeout (15s) looking up reference_channel for {code}", "WARN")
        executor.shutdown(wait=False)
        return ""
    except Exception as e:
        log(f"Error looking up reference_channel from sheet: {e}", "WARN")
        return ""


def _project_nguon_metadata_path(project_dir: Path) -> Path:
    return Path(project_dir) / ".nguon_runtime_metadata.yaml"


def read_project_nguon_metadata_cache(project_dir: Path, code: str) -> dict:
    path = _project_nguon_metadata_path(project_dir)
    data = load_yaml(path)
    if not isinstance(data, dict):
        return {}
    if str(data.get("project_code", "")).strip().upper() != str(code or "").strip().upper():
        return {}
    reference_channel = str(data.get("reference_channel", "") or "").strip()
    if reference_channel:
        ch_upper = reference_channel.upper()
        ref_dir_name = "finance" if ch_upper.startswith("TH") else ("success" if ch_upper.startswith("MT") else "psychology")
        root = SRT_TOOL_DIR / "reference_characters" / ref_dir_name / reference_channel
        if not ((root / "nv1.png").exists() or (root / "style.yaml").exists()):
            return {}
    return data


def write_project_nguon_metadata_cache(project_dir: Path, data: dict) -> None:
    if not data:
        return
    path = _project_nguon_metadata_path(project_dir)
    try:
        with path.open("w", encoding="utf-8") as f:
            yaml.dump(data, f, default_flow_style=False, allow_unicode=True, sort_keys=False)
    except Exception as e:
        log(f"Cannot write NGUON metadata cache {path}: {e}", "WARN")


def load_project_nguon_metadata(project_dir: Path, code: str) -> dict:
    cached = read_project_nguon_metadata_cache(project_dir, code)
    if cached:
        log(f"Using cached NGUON metadata: {_project_nguon_metadata_path(project_dir)}")
        return cached

    topic = lookup_topic_from_nguon_sheet(code)
    sheet_reference_channel = lookup_reference_channel_from_nguon_sheet(code)
    if not topic and _nguon_sheet_cache:
        topic = lookup_topic_from_nguon_sheet(code)
    reference_channel = resolve_psychology_reference_channel(sheet_reference_channel or "", code)
    ref_dir = {"finance": "finance", "success": "success"}.get(topic, "psychology")
    ref = SRT_TOOL_DIR / "reference_characters" / ref_dir / reference_channel / "nv1.png"
    if not ref.exists():
        for try_dir in ["psychology", "finance", "success"]:
            try_ref = SRT_TOOL_DIR / "reference_characters" / try_dir / reference_channel / "nv1.png"
            if try_ref.exists():
                ref = try_ref
                break
    data = {
        "project_code": code,
        "topic": topic or "",
        "reference_channel": reference_channel,
        "psychology_reference_image": str(ref) if ref.exists() else "",
        "source": "NGUON" if (topic or sheet_reference_channel) else "fallback",
        "fetched_at": int(time.time()),
    }
    if topic or sheet_reference_channel:
        write_project_nguon_metadata_cache(project_dir, data)
    return data


def normalize_topic(value: str) -> str:
    import unicodedata

    text = str(value or "").strip().lower()
    text = unicodedata.normalize("NFKD", text)
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = text.replace("_", " ").replace("-", " ")
    return " ".join(text.split())


def read_claimed_metadata(project_dir: Path) -> dict:
    claimed = project_dir / "_CLAIMED"
    if not claimed.exists():
        return {}
    try:
        lines = claimed.read_text(encoding="utf-8").splitlines()
    except UnicodeDecodeError:
        lines = claimed.read_text(encoding="utf-8-sig", errors="ignore").splitlines()
    meta = {}
    if len(lines) >= 5 and lines[4].strip():
        meta["raw_topic"] = lines[4].strip()
    if len(lines) >= 6 and lines[5].strip():
        meta["character_template"] = lines[5].strip()
    return meta


def resolve_psychology_reference_channel(value: str, project_code: str = "") -> str:
    """Resolve project codes like TL1-0002 → TL1-T2 or TH1-0003 → TH1-T3."""
    candidates = []
    for item in [value, project_code]:
        item = str(item or "").strip()
        if item and item not in candidates:
            candidates.append(item)
        m = re.match(r"^([A-Za-z]+\d+)-0*(\d+)$", item, flags=re.IGNORECASE)
        if m:
            mapped = f"{m.group(1).upper()}-T{int(m.group(2))}"
            if mapped not in candidates:
                candidates.append(mapped)
    for ref_dir in ["psychology", "finance"]:
        root = SRT_TOOL_DIR / "reference_characters" / ref_dir
        for candidate in candidates:
            if (root / candidate / "nv1.png").exists() or (root / candidate / "style.yaml").exists():
                return candidate
    return candidates[0] if candidates else ""


def apply_project_runtime_metadata(cfg: dict, project_dir: Path, code: str) -> dict:
    cfg = dict(cfg or {})
    meta = read_claimed_metadata(project_dir)
    nguon_meta = load_project_nguon_metadata(project_dir, code)
    # Priority: _CLAIMED > Sheet NGUON > code prefix > config (may be stale) > default
    raw_topic = (
        meta.get("raw_topic")
        or nguon_meta.get("topic")
        or infer_topic_from_code(code)
        or cfg.get("topic")
        or "story"
    )
    mapped = TOPIC_MAPPING.get(normalize_topic(raw_topic))
    if mapped:
        cfg["topic"], cfg["excel_mode"] = mapped
    elif raw_topic:
        cfg["topic"] = str(raw_topic).strip()
    if meta.get("character_template"):
        cfg["character_template"] = meta["character_template"]
    cfg.setdefault("project_code", code)
    cfg["reference_channel"] = resolve_psychology_reference_channel(nguon_meta.get("reference_channel") or cfg.get("reference_channel") or "", code)
    if cfg.get("topic") in ("psychology", "finance", "success"):
        ref_dir = {"finance": "finance", "success": "success"}.get(cfg.get("topic"), "psychology")
        ref = Path(str(nguon_meta.get("psychology_reference_image") or ""))
        if not ref.exists():
            ref = SRT_TOOL_DIR / "reference_characters" / ref_dir / str(cfg.get("reference_channel") or code) / "nv1.png"
        if ref.exists():
            cfg["psychology_reference_image"] = str(ref)
    return cfg


def load_excel_runtime_config(override_path: Path | None = None):
    master_cfg = load_yaml(SRT_TOOL_DIR / "config" / "settings.yaml")
    cfg = dict(master_cfg)
    if override_path:
        cfg.update(load_yaml(override_path))
    # API keys luôn lấy từ settings.yaml (source of truth) - không dùng key cũ cached trong project
    for key in ("deepseek_api_key", "deepseek_api_keys", "vov_direct_api_key", "claude_pool_api_key"):
        if key in master_cfg and master_cfg[key]:
            cfg[key] = master_cfg[key]
    return cfg


def is_file_stable(path: Path, checks: int = 3, delay: float = 1.0) -> bool:
    if not path.exists() or not path.is_file():
        return False
    last = None
    for _ in range(checks):
        try:
            st = path.stat()
            cur = (st.st_size, int(st.st_mtime))
        except OSError:
            return False
        if last is not None and cur != last:
            return False
        last = cur
        time.sleep(delay)
        if not path.exists():
            return False
    return True


def excel_is_usable(project_dir, code):
    excel_path = project_dir / f"{code}_prompts.xlsx"
    lock_path = excel_path.with_suffix(".xlsx.lock")
    temp_path = excel_path.with_suffix(".xlsx.tmp")
    if lock_path.exists() or temp_path.exists():
        return False
    if not excel_path.exists():
        return False

    sys.path.insert(0, str(SRT_TOOL_DIR))
    try:
        from modules.excel_manager import PromptWorkbook

        wb = PromptWorkbook(str(excel_path))
        wb.load_or_create()
        scenes = wb.get_scenes()
        if not scenes or not any((getattr(s, "img_prompt", "") or "").strip() for s in scenes):
            return False
        try:
            summary = wb.get_processing_summary()
            if summary and summary.get("completion_pct", 0) < 100:
                return False
        except Exception:
            pass
        return True
    except Exception:
        return False


def run_srt(project_dir, code, config_override: Path | None = None):
    srt_path = project_dir / f"{code}.srt"
    if srt_path.exists():
        log(f"SRT da co: {srt_path.name} -- skip")
        return srt_path

    audio_files = []
    for ext in ("*.mp3", "*.wav", "*.m4a", "*.flac", "*.ogg", "*.aac"):
        audio_files.extend(project_dir.glob(ext))
    audio_files = sorted([p for p in audio_files if p.is_file()], key=lambda p: p.name.lower())
    if not audio_files:
        raise FileNotFoundError(f"Khong tim thay audio trong {project_dir}")

    # Wait for copied audio file to become stable before transcribing.
    audio_path = None
    for p in audio_files:
        if is_file_stable(p, checks=2, delay=0.8):
            audio_path = p
            break
    if audio_path is None:
        raise TimeoutError(f"Audio chua on dinh (co the dang copy): {project_dir}")
    sys.path.insert(0, str(SRT_TOOL_DIR))
    from modules.voice_to_srt import VoiceToSrt

    srt_cfg = load_excel_runtime_config(config_override)
    model = srt_cfg.get("whisper_model", "base")
    language = srt_cfg.get("whisper_language", "en")
    log(f"MP3 -> SRT: {audio_path.name}, model={model}, language={language}")
    converter = VoiceToSrt(model_name=model, language=language)
    converter.transcribe(audio_path, srt_path)
    log(f"SRT tao xong: {srt_path.name}", "SUCCESS")
    return srt_path


def run_excel(project_dir, code, config_override: Path | None = None):
    excel_path = project_dir / f"{code}_prompts.xlsx"
    if excel_is_usable(project_dir, code):
        log(f"Excel da co: {excel_path.name} -- skip")
        return excel_path
    if excel_path.exists():
        log(f"Excel co san nhung chua usable: {excel_path.name} -- se tao tiep")

    sys.path.insert(0, str(SRT_TOOL_DIR))
    from modules.progressive_prompts import ProgressivePromptsGenerator

    cfg = apply_project_runtime_metadata(load_excel_runtime_config(config_override), project_dir, code)
    log(f"SRT -> Excel: bat dau ProgressivePromptsGenerator topic={cfg.get('topic', 'story')}")
    generator = ProgressivePromptsGenerator(config=cfg)
    ok = generator.run_all_steps(
        project_dir=project_dir,
        code=code,
        log_callback=lambda msg, level="INFO": log(f"  {msg}", level),
    )
    if not ok or not excel_path.exists() or not excel_is_usable(project_dir, code):
        raise RuntimeError("Tao Excel that bai")
    log(f"Excel tao xong: {excel_path.name}", "SUCCESS")
    return excel_path


def run_ve3(project_dir):
    token = os.environ.get("VE3_FLOW_TOKEN", "").strip()
    project_id = os.environ.get("VE3_FLOW_PROJECT_ID", "").strip()
    server_url = os.environ.get("VE3_SERVER_URL", "").strip()
    backend = os.environ.get("VE3_GENERATION_BACKEND", "").strip().lower() or "server"
    if backend not in {"server", "nanopic"}:
        backend = "server"
    if backend == "server":
        if not token:
            raise RuntimeError("Missing VE3_FLOW_TOKEN")
        if not project_id:
            raise RuntimeError("Missing VE3_FLOW_PROJECT_ID")
        if not server_url:
            raise RuntimeError("Missing VE3_SERVER_URL")

    # Avoid importing srt-to-excel's modules package into VE3.
    for key in list(sys.modules):
        if key == "modules" or key.startswith("modules."):
            del sys.modules[key]
    sys.path = [p for p in sys.path if str(SRT_TOOL_DIR) not in p]
    sys.path.insert(0, str(VE3_TOOL_DIR))

    from ve3_worker import VE3Worker

    cfg = load_yaml(VE3_TOOL_DIR / "config" / "settings.yaml")
    cfg.update({
        "generation_backend": backend,
        "generation_mode": backend,
        "retry_count": int(cfg.get("retry_count", 3) or 3),
        "max_concurrent": int(cfg.get("max_concurrent", 3) or 3),
    })
    if backend == "server":
        cfg.update({
            "local_server_enabled": True,
            "local_server_url": server_url,
            "local_server_list": [
                {"name": "target", "url": server_url, "enabled": True},
            ],
            "flow_bearer_token": token,
            "flow_project_id": project_id,
        })
    cfg = apply_project_runtime_metadata(cfg, project_dir, project_dir.name)

    log(f"VE3 worker: server={server_url}, project_dir={project_dir}, topic={cfg.get('topic', 'story')}")
    worker = VE3Worker(
        project_dir=str(project_dir),
        config=cfg,
        log_func=lambda msg, level="INFO": log(msg, level),
        progress_func=lambda phase, current, total, detail="": log(f"{phase}: {current}/{total} {detail}".strip()),
    )
    result = worker.run()
    log(f"VE3 result: {result}", "SUCCESS" if result.get("success") else "ERROR")
    if not result.get("success"):
        raise RuntimeError(f"VE3 worker failed: {result}")


def main():
    if len(sys.argv) < 2:
        print("Usage: python run_project_headless.py [--srt-excel-only|--excel-only|--ve3-only] <project_dir>")
        return 2

    mode = "all"
    config_override = None
    args = sys.argv[1:]
    while args and args[0].startswith("--"):
        if args[0] == "--config":
            if len(args) < 2:
                print("Missing value for --config")
                return 2
            config_override = Path(args[1]).resolve()
            args = args[2:]
            continue
        mode = args[0][2:]
        args = args[1:]
    if not args:
        print("Missing project_dir")
        return 2

    project_dir = Path(args[0]).resolve()
    code = project_dir.name
    log(f"RUN PROJECT: {code}")
    log(f"PROJECT DIR: {project_dir}")

    if not project_dir.exists() or not project_dir.is_dir():
        raise FileNotFoundError(f"Project dir khong ton tai: {project_dir}")

    endpoint_done = project_dir / ".endpoint_done.lock"
    if endpoint_done.exists():
        raise RuntimeError(f"Project da endpoint xong, bo qua headless run: {project_dir}")
    visual_dir = Path(r"D:\AUTO\visual") / code
    if visual_dir.exists() and visual_dir.is_dir():
        raise RuntimeError(f"Project da export sang visual, bo qua headless run: {project_dir}")

    if config_override and not config_override.exists():
        raise FileNotFoundError(f"Config override khong ton tai: {config_override}")

    if mode in ("all", "srt-excel-only"):
        run_srt(project_dir, code, config_override=config_override)
        run_excel(project_dir, code, config_override=config_override)
    elif mode == "excel-only":
        run_excel(project_dir, code, config_override=config_override)
    elif mode == "ve3-only":
        run_ve3(project_dir)
    else:
        raise RuntimeError(f"Unknown mode: {mode}")

    if mode == "all":
        run_ve3(project_dir)

    log("ALL DONE", "SUCCESS")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        log(f"FATAL: {exc}", "ERROR")
        traceback.print_exc()
        raise SystemExit(1)
