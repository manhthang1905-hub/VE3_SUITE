#!/usr/bin/env python3
from __future__ import annotations

import os
import signal
import subprocess
import sys
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

import requests
import yaml
from flask import Flask, jsonify, render_template, request


HERE = Path(__file__).resolve().parent
SUITE_ROOT = HERE.parents[1]
PROJECTS_DIR = SUITE_ROOT / "PROJECTS"
RUNNER = SUITE_ROOT / "run_project_headless.py"
VE3_DIR = SUITE_ROOT / "tools" / "ve3"
SRT_DIR = SUITE_ROOT / "tools" / "srt-to-excel"
VE3_SETTINGS_PATH = VE3_DIR / "config" / "settings.yaml"
SRT_SETTINGS_PATH = SRT_DIR / "config" / "settings.yaml"

def ensure_excel_manager():
    for base in (SRT_DIR, VE3_DIR):
        mod = base / "modules" / "excel_manager.py"
        if mod.exists():
            if str(base) not in sys.path:
                sys.path.insert(0, str(base))
            try:
                from modules.excel_manager import PromptWorkbook  # type: ignore

                return PromptWorkbook
            except Exception:
                continue
    return None


PromptWorkbook = ensure_excel_manager()


app = Flask(__name__, template_folder="templates", static_folder="static")


def now_ts() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S")


def load_yaml(path: Path) -> Dict[str, Any]:
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as fh:
        return yaml.safe_load(fh) or {}


def save_yaml(path: Path, data: Dict[str, Any]) -> None:
    with path.open("w", encoding="utf-8") as fh:
        yaml.safe_dump(data, fh, allow_unicode=True, sort_keys=False)


def tail_text(path: Path, max_lines: int = 120) -> List[str]:
    if not path.exists() or not path.is_file():
        return []
    try:
        with path.open("r", encoding="utf-8", errors="replace") as fh:
            lines = fh.readlines()
        return [line.rstrip("\r\n") for line in lines[-max_lines:]]
    except Exception as exc:
        return [f"[{now_ts()}] ERROR read log failed: {exc}"]


def normalize_project_summary(project_dir: Path) -> Dict[str, Any]:
    code = project_dir.name
    excel_path = project_dir / f"{code}_prompts.xlsx"
    srt_path = project_dir / f"{code}.srt"
    audio_path = next((p for p in sorted(project_dir.iterdir()) if p.suffix.lower() in {".mp3", ".wav", ".m4a", ".flac", ".ogg", ".aac"}), None) if project_dir.exists() else None
    nv_count = len(list((project_dir / "nv").glob("*.png"))) if (project_dir / "nv").exists() else 0
    img_count = len(list((project_dir / "img").glob("*.png"))) if (project_dir / "img").exists() else 0
    vid_count = len(list((project_dir / "vid").glob("*.mp4"))) if (project_dir / "vid").exists() else 0
    summary: Dict[str, Any] = {
        "code": code,
        "path": str(project_dir),
        "has_audio": bool(audio_path),
        "has_srt": srt_path.exists(),
        "has_excel": excel_path.exists(),
        "audio_file": audio_path.name if audio_path else "",
        "srt_file": srt_path.name if srt_path.exists() else "",
        "excel_file": excel_path.name if excel_path.exists() else "",
        "thumb_file": "",
        "counts": {
            "references": nv_count,
            "images": img_count,
            "videos": vid_count,
            "scenes_total": 0,
            "scenes_done": 0,
            "videos_done": 0,
            "characters_total": 0,
            "characters_done": 0,
            "music_total": 0,
            "music_done": 0,
        },
        "updated_at": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(project_dir.stat().st_mtime)),
    }
    thumb_dir = project_dir / "thumb"
    thumb_exts = {".jpg", ".jpeg", ".png", ".webp"}
    thumbs = sorted((p for p in thumb_dir.iterdir() if p.is_file() and p.suffix.lower() in thumb_exts)) if thumb_dir.exists() else []
    if thumbs:
        summary["thumb_file"] = str(thumbs[0].relative_to(project_dir))

    if PromptWorkbook and excel_path.exists():
        try:
            wb = PromptWorkbook(excel_path)
            wb.load_or_create()
            characters = wb.get_characters() or []
            scenes = wb.get_scenes() or []
            if characters:
                summary["counts"]["characters_total"] = len(characters)
                summary["counts"]["characters_done"] = sum(1 for c in characters if (getattr(c, "status", "") or "").lower() == "done")
            if scenes:
                summary["counts"]["scenes_total"] = len(scenes)
                summary["counts"]["scenes_done"] = sum(1 for s in scenes if (getattr(s, "status_img", "") or "").lower() == "done")
                summary["counts"]["videos_done"] = sum(1 for s in scenes if (getattr(s, "status_vid", "") or "").lower() == "done")
            if hasattr(wb, "get_music_tracks"):
                tracks = wb.get_music_tracks() or []
                tracks = [t for t in tracks if str(t.get("music_id", "")).strip() and str(t.get("suno_prompt", "")).strip()]
                summary["counts"]["music_total"] = len(tracks)
                summary["counts"]["music_done"] = sum(
                    1
                    for t in tracks
                    if (project_dir / "music" / f"{str(t.get('music_id', '')).strip()}.mp3").exists()
                    or str(t.get("status", "")).strip().lower() == "done"
                )
        except Exception:
            pass

    # Disk-based fallback: always count from filesystem for accuracy
    nv_dir = project_dir / "nv"
    img_dir = project_dir / "img"
    vid_dir = project_dir / "vid"
    music_dir = project_dir / "music"

    nv_files = sorted(nv_dir.glob("*.png")) if nv_dir.exists() else []
    img_files = sorted(img_dir.glob("*.png")) if img_dir.exists() else []
    vid_files = sorted(vid_dir.glob("*.mp4")) if vid_dir.exists() else []
    music_files = sorted(music_dir.glob("*.mp3")) if music_dir.exists() else []

    # Characters: count nv*.png files (excluding loc_*)
    char_files = [f for f in nv_files if f.stem.startswith("nv")]
    loc_files = [f for f in nv_files if f.stem.startswith("loc")]

    # Use disk counts as fallback when Excel returns 0
    c = summary["counts"]
    if c["characters_total"] == 0:
        c["characters_total"] = len(char_files) + len(loc_files)
        c["characters_done"] = len(char_files) + len(loc_files)  # files exist = done
    if c["scenes_total"] == 0 and img_files:
        c["scenes_total"] = len(img_files)
        c["scenes_done"] = len(img_files)
    # Always use disk count for images/videos as authoritative
    c["images"] = len(img_files)
    c["videos"] = len(vid_files)
    c["references"] = len(nv_files)
    if c["videos_done"] == 0 and vid_files:
        c["videos_done"] = len(vid_files)
    if c["music_total"] == 0 and music_files:
        c["music_total"] = len(music_files)
        c["music_done"] = len(music_files)

    return summary


def list_projects() -> List[Dict[str, Any]]:
    if not PROJECTS_DIR.exists():
        return []
    items = [normalize_project_summary(p) for p in sorted(PROJECTS_DIR.iterdir()) if p.is_dir()]
    items.sort(key=lambda item: item["updated_at"], reverse=True)
    return items


def server_status_rows() -> List[Dict[str, Any]]:
    cfg = load_yaml(VE3_SETTINGS_PATH)
    rows = []
    for server in cfg.get("local_server_list", []) or []:
        url = str(server.get("url", "") or "").strip()
        enabled = bool(server.get("enabled", False))
        row = {
            "name": server.get("name", ""),
            "url": url,
            "enabled": enabled,
            "online": False,
            "latency_ms": None,
            "error": "",
        }
        if not url:
            row["error"] = "missing url"
            rows.append(row)
            continue
        started = time.perf_counter()
        try:
            res = requests.get(f"{url}/api/status", timeout=3)
            row["online"] = res.ok
            row["latency_ms"] = int((time.perf_counter() - started) * 1000)
            if not res.ok:
                row["error"] = f"http {res.status_code}"
        except Exception as exc:
            row["error"] = str(exc)
        rows.append(row)
    return rows


@dataclass
class JobState:
    id: str
    project_code: str
    mode: str
    created_at: str
    status: str = "queued"
    started_at: str = ""
    finished_at: str = ""
    return_code: Optional[int] = None
    process: Optional[subprocess.Popen] = None
    log_lines: List[str] = field(default_factory=list)
    stop_requested: bool = False

    def push(self, message: str) -> None:
        line = f"[{now_ts()}] {message}"
        self.log_lines.append(line)
        self.log_lines = self.log_lines[-400:]


class RuntimeState:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.jobs: Dict[str, JobState] = {}
        self.active_job_id: Optional[str] = None

    def create_job(self, project_code: str, mode: str) -> JobState:
        job = JobState(
            id=uuid.uuid4().hex[:12],
            project_code=project_code,
            mode=mode,
            created_at=now_ts(),
        )
        with self.lock:
            self.jobs[job.id] = job
            self.active_job_id = job.id
        return job

    def get_job(self, job_id: str) -> Optional[JobState]:
        with self.lock:
            return self.jobs.get(job_id)

    def active_job(self) -> Optional[JobState]:
        with self.lock:
            job = self.jobs.get(self.active_job_id) if self.active_job_id else None
            if job and job.status in {"queued", "running"}:
                return job
            return None

    def clear_active(self, job_id: str) -> None:
        with self.lock:
            if self.active_job_id == job_id:
                self.active_job_id = None

    def snapshot(self) -> List[Dict[str, Any]]:
        with self.lock:
            jobs = [job for job in self.jobs.values() if job.status in {"queued", "running"}]
        jobs.sort(key=lambda item: item.created_at, reverse=True)
        return [serialize_job(job) for job in jobs]


STATE = RuntimeState()


def serialize_job(job: JobState) -> Dict[str, Any]:
    return {
        "id": job.id,
        "project_code": job.project_code,
        "mode": job.mode,
        "created_at": job.created_at,
        "started_at": job.started_at,
        "finished_at": job.finished_at,
        "status": job.status,
        "return_code": job.return_code,
        "stop_requested": job.stop_requested,
        "log_lines": job.log_lines[-160:],
    }


def run_job(job: JobState) -> None:
    project_dir = PROJECTS_DIR / job.project_code
    cmd = [sys.executable, str(RUNNER)]
    if job.mode != "all":
        cmd.append(f"--{job.mode}")
    cmd.append(str(project_dir))

    env = os.environ.copy()
    ve3_cfg = load_yaml(VE3_SETTINGS_PATH)
    env["PYTHONUTF8"] = "1"
    env["VE3_FLOW_TOKEN"] = str(ve3_cfg.get("flow_bearer_token", "") or "")
    env["VE3_FLOW_PROJECT_ID"] = str(ve3_cfg.get("flow_project_id", "") or "")
    env["VE3_SERVER_URL"] = str(ve3_cfg.get("local_server_url", "") or "")

    job.status = "running"
    job.started_at = now_ts()
    job.push(f"START {job.project_code} mode={job.mode}")
    try:
        creationflags = subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0
        proc = subprocess.Popen(
            cmd,
            cwd=str(SUITE_ROOT),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=env,
            creationflags=creationflags,
        )
        job.process = proc
        assert proc.stdout is not None
        for line in proc.stdout:
            job.push(line.rstrip("\r\n"))
        rc = proc.wait()
        job.return_code = rc
        job.status = "done" if rc == 0 else ("stopped" if job.stop_requested else "error")
        job.finished_at = now_ts()
        job.push(f"FINISH rc={rc}")
    except Exception as exc:
        job.return_code = -1
        job.status = "error"
        job.finished_at = now_ts()
        job.push(f"FATAL {exc}")
    finally:
        job.process = None
        if job.status not in {"queued", "running"}:
            STATE.clear_active(job.id)


@app.get("/")
def index():
    return render_template("index.html")


@app.get("/api/overview")
def api_overview():
    projects = list_projects()
    active = STATE.active_job()
    return jsonify(
        {
            "project_count": len(projects),
            "projects_with_excel": sum(1 for p in projects if p["has_excel"]),
            "projects_with_video": sum(1 for p in projects if p["counts"]["videos"] > 0),
            "active_job": serialize_job(active) if active else None,
        }
    )


@app.get("/api/ping")
def api_ping():
    return jsonify({"ok": True, "ts": now_ts()})


@app.get("/api/projects")
def api_projects():
    return jsonify({"items": list_projects()})


@app.get("/api/projects/<code>")
def api_project_detail(code: str):
    project_dir = PROJECTS_DIR / code
    if not project_dir.exists():
        return jsonify({"error": "project not found"}), 404
    detail = normalize_project_summary(project_dir)
    detail["tail_logs"] = tail_text(SUITE_ROOT / "logs" / f"{code}_pipeline.out.log")
    return jsonify(detail)


@app.get("/api/jobs")
def api_jobs():
    return jsonify({"items": STATE.snapshot()})


@app.post("/api/jobs")
def api_create_job():
    data = request.get_json(silent=True) or {}
    project_code = str(data.get("project_code", "") or "").strip()
    mode = str(data.get("mode", "all") or "all").strip()
    if not project_code:
        return jsonify({"error": "project_code is required"}), 400
    if mode not in {"all", "srt-excel-only", "excel-only", "ve3-only"}:
        return jsonify({"error": "invalid mode"}), 400
    if not (PROJECTS_DIR / project_code).exists():
        return jsonify({"error": "project not found"}), 404
    active = STATE.active_job()
    if active and active.status == "running":
        return jsonify({"error": "another job is running", "active_job": serialize_job(active)}), 409

    job = STATE.create_job(project_code, mode)
    thread = threading.Thread(target=run_job, args=(job,), daemon=True)
    thread.start()
    return jsonify({"job": serialize_job(job)})


@app.post("/api/jobs/<job_id>/stop")
def api_stop_job(job_id: str):
    job = STATE.get_job(job_id)
    if not job:
        return jsonify({"error": "job not found"}), 404
    proc = job.process
    if not proc or job.status != "running":
        return jsonify({"job": serialize_job(job)})
    job.stop_requested = True
    job.push("STOP requested")
    try:
        if os.name == "nt":
            proc.send_signal(signal.CTRL_BREAK_EVENT)  # type: ignore[attr-defined]
        else:
            proc.terminate()
    except Exception:
        try:
            proc.terminate()
        except Exception:
            pass
    return jsonify({"job": serialize_job(job)})


@app.get("/api/config")
def api_get_config():
    ve3_cfg = load_yaml(VE3_SETTINGS_PATH)
    srt_cfg = load_yaml(SRT_SETTINGS_PATH)
    return jsonify(
        {
            "ve3": {
                "pipeline_workers": ve3_cfg.get("pipeline_workers", 1),
                "excel_workers": ve3_cfg.get("excel_workers", 1),
                "max_concurrent": ve3_cfg.get("max_concurrent", 1),
                "retry_count": ve3_cfg.get("retry_count", 1),
                "flow_aspect_ratio": ve3_cfg.get("flow_aspect_ratio", "landscape"),
                "generation_backend": ve3_cfg.get("generation_backend", "server"),
                "local_server_url": ve3_cfg.get("local_server_url", ""),
                "flow_auth_auto_enabled": bool(ve3_cfg.get("flow_auth_auto_enabled", False)),
                "servers": ve3_cfg.get("local_server_list", []),
            },
            "srt": {
                "whisper_model": srt_cfg.get("whisper_model", "base"),
                "whisper_language": srt_cfg.get("whisper_language", "en"),
                "max_parallel_api": srt_cfg.get("max_parallel_api", 6),
                "min_scene_duration": srt_cfg.get("min_scene_duration", 5),
                "max_scene_duration": srt_cfg.get("max_scene_duration", 8),
            },
        }
    )


@app.post("/api/config")
def api_save_config():
    payload = request.get_json(silent=True) or {}
    ve3_in = payload.get("ve3", {}) or {}
    srt_in = payload.get("srt", {}) or {}

    ve3_cfg = load_yaml(VE3_SETTINGS_PATH)
    srt_cfg = load_yaml(SRT_SETTINGS_PATH)

    for key in ("pipeline_workers", "excel_workers", "max_concurrent", "retry_count"):
        if key in ve3_in:
            ve3_cfg[key] = int(ve3_in[key])
    for key in ("flow_aspect_ratio", "generation_backend", "local_server_url"):
        if key in ve3_in:
            ve3_cfg[key] = str(ve3_in[key])
    if "flow_auth_auto_enabled" in ve3_in:
        ve3_cfg["flow_auth_auto_enabled"] = bool(ve3_in["flow_auth_auto_enabled"])

    incoming_servers = ve3_in.get("servers")
    if isinstance(incoming_servers, list):
        for existing, update in zip(ve3_cfg.get("local_server_list", []), incoming_servers):
            if "enabled" in update:
                existing["enabled"] = bool(update["enabled"])
            if "url" in update:
                existing["url"] = str(update["url"])
            if "name" in update:
                existing["name"] = str(update["name"])
        ve3_cfg["local_server_list"] = ve3_cfg.get("local_server_list", [])

    for key in ("whisper_model", "whisper_language"):
        if key in srt_in:
            srt_cfg[key] = str(srt_in[key])
    for key in ("max_parallel_api", "min_scene_duration", "max_scene_duration"):
        if key in srt_in:
            srt_cfg[key] = int(srt_in[key])

    save_yaml(VE3_SETTINGS_PATH, ve3_cfg)
    save_yaml(SRT_SETTINGS_PATH, srt_cfg)
    return api_get_config()


@app.get("/api/servers")
def api_servers():
    return jsonify({"items": server_status_rows()})


@app.get("/api/projects/<code>/characters")
def api_project_characters(code: str):
    """Get character list with prompts and status."""
    project_dir = PROJECTS_DIR / code
    if not project_dir.exists():
        return jsonify({"error": "project not found"}), 404
    excel_path = project_dir / f"{code}_prompts.xlsx"
    if not excel_path.exists():
        return jsonify({"items": [], "total": 0})
    if not PromptWorkbook:
        return jsonify({"error": "PromptWorkbook not available"}), 500
    try:
        wb = PromptWorkbook(excel_path)
        wb.load_or_create()
        chars = wb.get_characters() or []
        nv_dir = project_dir / "nv"
        items = []
        for c in chars:
            cid = getattr(c, "id", "")
            items.append({
                "id": cid,
                "name": getattr(c, "name", ""),
                "role": getattr(c, "role", ""),
                "status": (getattr(c, "status", "") or "pending").lower(),
                "english_prompt": getattr(c, "english_prompt", "") or "",
                "vietnamese_prompt": getattr(c, "vietnamese_prompt", "") or "",
                "has_image": (nv_dir / f"{cid}.png").exists() if cid else False,
                "image_url": f"/api/projects/{code}/image/nv/{cid}.png" if cid and (nv_dir / f"{cid}.png").exists() else None,
            })
        # Disk fallback: if Excel returned nothing, scan nv/ folder
        if not items:
            nv_dir = project_dir / "nv"
            if nv_dir.exists():
                for f in sorted(nv_dir.glob("*.png")):
                    cid = f.stem
                    items.append({
                        "id": cid,
                        "name": cid,
                        "role": "character" if cid.startswith("nv") else "location",
                        "status": "done",
                        "english_prompt": "",
                        "vietnamese_prompt": "",
                        "has_image": True,
                        "image_url": f"/api/projects/{code}/image/nv/{cid}.png",
                    })
        return jsonify({"items": items, "total": len(items)})
    except Exception as e:
        # Final fallback: scan disk even on exception
        nv_dir = project_dir / "nv"
        items = []
        if nv_dir.exists():
            for f in sorted(nv_dir.glob("*.png")):
                cid = f.stem
                items.append({
                    "id": cid, "name": cid,
                    "role": "character" if cid.startswith("nv") else "location",
                    "status": "done", "english_prompt": "", "vietnamese_prompt": "",
                    "has_image": True,
                    "image_url": f"/api/projects/{code}/image/nv/{cid}.png",
                })
        if items:
            return jsonify({"items": items, "total": len(items)})
        return jsonify({"error": str(e)}), 500


@app.get("/api/projects/<code>/scenes")
def api_project_scenes(code: str):
    """Get scene list with prompts, status, and pagination."""
    project_dir = PROJECTS_DIR / code
    if not project_dir.exists():
        return jsonify({"error": "project not found"}), 404
    excel_path = project_dir / f"{code}_prompts.xlsx"
    if not excel_path.exists():
        return jsonify({"items": [], "total": 0, "page": 1, "pages": 0})
    if not PromptWorkbook:
        return jsonify({"error": "PromptWorkbook not available"}), 500
    try:
        wb = PromptWorkbook(excel_path)
        wb.load_or_create()
        scenes = wb.get_scenes() or []
        page = int(request.args.get("page", 1))
        size = int(request.args.get("size", 20))

        # Disk fallback when Excel returns empty
        if not scenes:
            img_dir = project_dir / "img"
            vid_dir = project_dir / "vid"
            if img_dir.exists():
                img_files = sorted(img_dir.glob("scene_*.png"))
                total = len(img_files)
                pages = max(1, (total + size - 1) // size)
                page = max(1, min(page, pages))
                start = (page - 1) * size
                items = []
                for f in img_files[start:start + size]:
                    try:
                        sid = int(f.stem.replace("scene_", ""))
                    except ValueError:
                        continue
                    has_vid = (vid_dir / f"scene_{sid:03d}.mp4").exists() if vid_dir.exists() else False
                    items.append({
                        "scene_id": sid, "srt_text": "", "img_prompt": "", "video_prompt": "",
                        "status_img": "done", "status_vid": "done" if has_vid else "pending",
                        "duration": 0, "has_image": True,
                        "image_url": f"/api/projects/{code}/image/img/scene_{sid:03d}.png",
                    })
                return jsonify({"items": items, "total": total, "page": page, "pages": pages, "size": size})

        total = len(scenes)
        pages = max(1, (total + size - 1) // size)
        page = max(1, min(page, pages))
        start = (page - 1) * size
        page_scenes = scenes[start:start + size]
        img_dir = project_dir / "img"
        items = []
        for s in page_scenes:
            sid = getattr(s, "scene_id", 0)
            items.append({
                "scene_id": sid,
                "srt_text": getattr(s, "srt_text", "") or "",
                "img_prompt": getattr(s, "img_prompt", "") or "",
                "video_prompt": getattr(s, "video_prompt", "") or "",
                "status_img": (getattr(s, "status_img", "") or "pending").lower(),
                "status_vid": (getattr(s, "status_vid", "") or "pending").lower(),
                "duration": getattr(s, "duration", 0) or 0,
                "has_image": (img_dir / f"scene_{sid:03d}.png").exists(),
                "image_url": f"/api/projects/{code}/image/img/scene_{sid:03d}.png" if (img_dir / f"scene_{sid:03d}.png").exists() else None,
            })
        return jsonify({"items": items, "total": total, "page": page, "pages": pages, "size": size})
    except Exception as e:
        # Final fallback: scan disk
        img_dir = project_dir / "img"
        vid_dir = project_dir / "vid"
        page = int(request.args.get("page", 1))
        size = int(request.args.get("size", 20))
        if img_dir.exists():
            img_files = sorted(img_dir.glob("scene_*.png"))
            total = len(img_files)
            pages = max(1, (total + size - 1) // size)
            page = max(1, min(page, pages))
            start = (page - 1) * size
            items = []
            for f in img_files[start:start + size]:
                try:
                    sid = int(f.stem.replace("scene_", ""))
                except ValueError:
                    continue
                has_vid = (vid_dir / f"scene_{sid:03d}.mp4").exists() if vid_dir.exists() else False
                items.append({
                    "scene_id": sid, "srt_text": "", "img_prompt": "", "video_prompt": "",
                    "status_img": "done", "status_vid": "done" if has_vid else "pending",
                    "duration": 0, "has_image": True,
                    "image_url": f"/api/projects/{code}/image/img/scene_{sid:03d}.png",
                })
            if items:
                return jsonify({"items": items, "total": total, "page": page, "pages": pages, "size": size})
        return jsonify({"error": str(e)}), 500


@app.get("/api/projects/<code>/image/<path:subpath>")
def api_project_image(code: str, subpath: str):
    """Serve project images (nv/characters, img/scenes)."""
    from flask import send_from_directory
    project_dir = PROJECTS_DIR / code
    file_path = project_dir / subpath
    if not file_path.exists() or not file_path.is_file():
        return "", 404
    # Security: ensure path is within project dir
    try:
        file_path.resolve().relative_to(project_dir.resolve())
    except ValueError:
        return "", 403
    return send_from_directory(str(file_path.parent), file_path.name)


if __name__ == "__main__":
    host = os.environ.get("WEB_ADMIN_HOST", "127.0.0.1")
    port = int(os.environ.get("WEB_ADMIN_PORT", "5070"))
    app.run(host=host, port=port, debug=False)
