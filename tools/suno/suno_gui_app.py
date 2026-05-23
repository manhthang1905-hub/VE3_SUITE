#!/usr/bin/env python3
import shutil
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

from flask import Flask, jsonify, redirect, render_template, request, url_for
from werkzeug.utils import secure_filename

from suno_browser_worker import BrowserSunoWorker
from token_manager import TokenManager


HERE = Path(__file__).parent
SUITE_ROOT = HERE.resolve().parents[1]
UPLOAD_DIR = HERE / "uploads"
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

SRT_TO_EXCEL = SUITE_ROOT / "tools" / "srt-to-excel"
VE3_TOOL = SUITE_ROOT / "tools" / "ve3"


def ensure_excel_manager():
    for base in [SRT_TO_EXCEL, VE3_TOOL]:
        mod = base / "modules" / "excel_manager.py"
        if mod.exists():
            if str(base) not in sys.path:
                sys.path.insert(0, str(base))
            from modules.excel_manager import PromptWorkbook  # type: ignore

            return PromptWorkbook
    raise RuntimeError("Cannot find excel_manager.py")


PromptWorkbook = ensure_excel_manager()

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 50 * 1024 * 1024


@dataclass
class AppState:
    current_excel: Optional[Path] = None
    output_dir: Optional[Path] = None
    tracks: List[Dict[str, str]] = field(default_factory=list)
    running: bool = False
    stop_requested: bool = False
    current_index: int = 0
    total: int = 0
    last_error: str = ""
    log_lines: List[str] = field(default_factory=list)
    results: List[Dict[str, str]] = field(default_factory=list)
    thread: Optional[threading.Thread] = None

    def log(self, msg: str) -> None:
        stamp = time.strftime("%H:%M:%S")
        self.log_lines.append(f"{stamp} {msg}")
        self.log_lines = self.log_lines[-200:]


STATE = AppState()
LOCK = threading.Lock()


def read_music_tracks(excel_path: Path) -> List[Dict[str, str]]:
    wb = PromptWorkbook(excel_path)
    wb.load_or_create()
    return wb.get_music_tracks()


def default_output_dir(excel_path: Path) -> Path:
    return excel_path.parent / f"{excel_path.stem}_suno_music"


def update_track_status(excel_path: Path, music_id: str, **kwargs) -> None:
    wb = PromptWorkbook(excel_path)
    wb.load_or_create()
    wb.update_music_track(music_id, **kwargs)


def run_batch(excel_path: Path, output_dir: Path, skip_existing: bool) -> None:
    with LOCK:
        STATE.running = True
        STATE.stop_requested = False
        STATE.last_error = ""
        STATE.results = []
        STATE.current_index = 0
        STATE.total = len(STATE.tracks)
        STATE.log(f"Start batch: {excel_path.name}")

    try:
        with TokenManager() as tm:
            worker = BrowserSunoWorker(tm._page)

            for idx, track in enumerate(STATE.tracks, start=1):
                with LOCK:
                    if STATE.stop_requested:
                        STATE.log("Stop requested by user")
                        break
                    STATE.current_index = idx

                music_id = str(track.get("music_id", "")).strip()
                prompt = (track.get("suno_prompt") or "").strip()
                title = (track.get("title") or f"Track {music_id}").strip()
                status = (track.get("status") or "").strip().lower()
                output_path = output_dir / f"{music_id}.mp3"

                if not music_id or not prompt:
                    with LOCK:
                        STATE.results.append(
                            {
                                "music_id": music_id or f"row-{idx}",
                                "status": "skip",
                                "result": "missing music_id or suno_prompt",
                                "path": str(output_path),
                            }
                        )
                        STATE.log(f"[SKIP] {music_id}: missing prompt")
                    continue

                if skip_existing and status == "done" and output_path.exists():
                    with LOCK:
                        STATE.results.append(
                            {
                                "music_id": music_id,
                                "status": "skip",
                                "result": "already done",
                                "path": str(output_path),
                            }
                        )
                        STATE.log(f"[SKIP] {music_id}: already done")
                    continue

                update_track_status(excel_path, music_id, status="generating")
                with LOCK:
                    STATE.log(f"[{idx}/{STATE.total}] Generating {music_id} - {title}")

                ok = False
                result = ""
                try:
                    ok, result = worker.generate_and_download(
                        prompt=prompt,
                        output_path=output_path,
                        timeout=420,
                        pick="best",
                    )
                except Exception as e:
                    result = str(e)

                if ok:
                    update_track_status(excel_path, music_id, status="done", suno_url=result)
                    with LOCK:
                        STATE.results.append(
                            {
                                "music_id": music_id,
                                "status": "done",
                                "result": result,
                                "path": str(output_path),
                            }
                        )
                        STATE.log(f"[OK] {music_id} -> {output_path.name}")
                else:
                    update_track_status(excel_path, music_id, status="error")
                    with LOCK:
                        STATE.results.append(
                            {
                                "music_id": music_id,
                                "status": "error",
                                "result": result,
                                "path": str(output_path),
                            }
                        )
                        STATE.last_error = result
                        STATE.log(f"[FAIL] {music_id}: {result}")
                    break

                time.sleep(8)

    except Exception as e:
        with LOCK:
            STATE.last_error = str(e)
            STATE.log(f"[FATAL] {e}")
    finally:
        with LOCK:
            STATE.running = False
            STATE.thread = None
            STATE.log("Batch stopped")


@app.route("/", methods=["GET"])
def index():
    with LOCK:
        excel_path = str(STATE.current_excel) if STATE.current_excel else ""
        output_path = str(STATE.output_dir) if STATE.output_dir else ""
        tracks = list(STATE.tracks)
        running = STATE.running
        logs = list(STATE.log_lines)
        results = list(STATE.results)
        current_index = STATE.current_index
        total = STATE.total
        last_error = STATE.last_error
    return render_template(
        "index.html",
        excel_path=excel_path,
        output_path=output_path,
        tracks=tracks,
        running=running,
        logs=logs,
        results=results,
        current_index=current_index,
        total=total,
        last_error=last_error,
    )


@app.post("/upload")
def upload_excel():
    file = request.files.get("excel_file")
    if not file or not file.filename:
        return redirect(url_for("index"))

    filename = secure_filename(file.filename)
    if not filename.lower().endswith(".xlsx"):
        return redirect(url_for("index"))

    dest = UPLOAD_DIR / filename
    file.save(dest)

    with LOCK:
        STATE.current_excel = dest
        STATE.output_dir = default_output_dir(dest)
        STATE.tracks = read_music_tracks(dest)
        STATE.log(f"Loaded Excel: {dest.name} ({len(STATE.tracks)} tracks)")

    return redirect(url_for("index"))


@app.post("/load-path")
def load_excel_path():
    excel_path_raw = (request.form.get("excel_path") or "").strip()
    if not excel_path_raw:
        return redirect(url_for("index"))

    excel_path = Path(excel_path_raw)
    if not excel_path.exists():
        with LOCK:
            STATE.log(f"Excel not found: {excel_path}")
        return redirect(url_for("index"))

    with LOCK:
        STATE.current_excel = excel_path
        STATE.output_dir = default_output_dir(excel_path)
        STATE.tracks = read_music_tracks(excel_path)
        STATE.log(f"Loaded Excel: {excel_path.name} ({len(STATE.tracks)} tracks)")

    return redirect(url_for("index"))


@app.post("/start")
def start_batch():
    with LOCK:
        if STATE.running or not STATE.current_excel or not STATE.output_dir:
            return redirect(url_for("index"))
        STATE.output_dir.mkdir(parents=True, exist_ok=True)
        skip_existing = request.form.get("skip_existing") == "on"
        thread = threading.Thread(
            target=run_batch,
            args=(STATE.current_excel, STATE.output_dir, skip_existing),
            daemon=True,
        )
        STATE.thread = thread
        thread.start()
    return redirect(url_for("index"))


@app.post("/stop")
def stop_batch():
    with LOCK:
        STATE.stop_requested = True
        STATE.log("Stop requested")
    return redirect(url_for("index"))


@app.get("/status")
def status():
    with LOCK:
        payload = {
            "running": STATE.running,
            "current_index": STATE.current_index,
            "total": STATE.total,
            "last_error": STATE.last_error,
            "logs": STATE.log_lines[-40:],
            "results": STATE.results,
            "excel_path": str(STATE.current_excel) if STATE.current_excel else "",
            "output_dir": str(STATE.output_dir) if STATE.output_dir else "",
        }
    return jsonify(payload)


@app.post("/refresh")
def refresh_tracks():
    with LOCK:
        excel_path = STATE.current_excel
    if excel_path and excel_path.exists():
        with LOCK:
            STATE.tracks = read_music_tracks(excel_path)
            STATE.log("Refreshed music sheet")
    return redirect(url_for("index"))


@app.post("/copy-upload")
def copy_upload():
    src_raw = (request.form.get("source_path") or "").strip()
    if not src_raw:
        return redirect(url_for("index"))
    src = Path(src_raw)
    if not src.exists():
        with LOCK:
            STATE.log(f"Source file not found: {src}")
        return redirect(url_for("index"))
    dest = UPLOAD_DIR / src.name
    shutil.copy2(src, dest)
    with LOCK:
        STATE.current_excel = dest
        STATE.output_dir = default_output_dir(dest)
        STATE.tracks = read_music_tracks(dest)
        STATE.log(f"Copied and loaded Excel: {dest.name}")
    return redirect(url_for("index"))


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5010, debug=False)
