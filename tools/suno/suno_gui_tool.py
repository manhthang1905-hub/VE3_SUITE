#!/usr/bin/env python3
import queue
import sys
import threading
import time
from pathlib import Path
from typing import Dict, List, Optional

import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from suno_browser_worker import BrowserSunoWorker
from token_manager import TokenManager


HERE = Path(__file__).resolve().parent
SUITE_ROOT = HERE.parents[1]
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


class SunoDesktopApp:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("Auto Suno GUI")
        self.root.geometry("1240x820")

        self.excel_path_var = tk.StringVar()
        self.output_dir_var = tk.StringVar()
        self.status_var = tk.StringVar(value="Idle")
        self.skip_existing_var = tk.BooleanVar(value=True)

        self.tracks: List[Dict[str, str]] = []
        self.worker_thread: Optional[threading.Thread] = None
        self.stop_requested = False
        self.ui_queue: queue.Queue = queue.Queue()

        self._build_ui()
        self._poll_ui_queue()

    def _build_ui(self) -> None:
        top = ttk.Frame(self.root, padding=12)
        top.pack(fill="x")

        ttk.Label(top, text="Excel").grid(row=0, column=0, sticky="w")
        ttk.Entry(top, textvariable=self.excel_path_var, width=95).grid(row=0, column=1, sticky="ew", padx=8)
        ttk.Button(top, text="Browse", command=self.browse_excel).grid(row=0, column=2, padx=4)
        ttk.Button(top, text="Load Music Sheet", command=self.load_excel).grid(row=0, column=3, padx=4)
        top.columnconfigure(1, weight=1)

        ttk.Label(top, text="Output").grid(row=1, column=0, sticky="w", pady=(8, 0))
        ttk.Entry(top, textvariable=self.output_dir_var, width=95).grid(row=1, column=1, sticky="ew", padx=8, pady=(8, 0))
        ttk.Button(top, text="Browse", command=self.browse_output).grid(row=1, column=2, padx=4, pady=(8, 0))
        ttk.Checkbutton(top, text="Skip existing mp3", variable=self.skip_existing_var).grid(row=1, column=3, sticky="w", pady=(8, 0))

        actions = ttk.Frame(self.root, padding=(12, 0))
        actions.pack(fill="x")
        self.start_btn = ttk.Button(actions, text="Start Batch", command=self.start_batch)
        self.start_btn.pack(side="left")
        self.stop_btn = ttk.Button(actions, text="Stop", command=self.stop_batch, state="disabled")
        self.stop_btn.pack(side="left", padx=8)
        ttk.Button(actions, text="Refresh Sheet", command=self.load_excel).pack(side="left")
        ttk.Label(actions, textvariable=self.status_var).pack(side="right")

        middle = ttk.Panedwindow(self.root, orient="vertical")
        middle.pack(fill="both", expand=True, padx=12, pady=12)

        tracks_frame = ttk.Labelframe(middle, text="Music Sheet Preview", padding=8)
        logs_frame = ttk.Labelframe(middle, text="Logs / Results", padding=8)
        middle.add(tracks_frame, weight=3)
        middle.add(logs_frame, weight=2)

        columns = ("music_id", "title", "start_time", "duration", "mood", "status", "prompt")
        self.tree = ttk.Treeview(tracks_frame, columns=columns, show="headings", height=18)
        widths = {
            "music_id": 70,
            "title": 170,
            "start_time": 100,
            "duration": 80,
            "mood": 120,
            "status": 90,
            "prompt": 580,
        }
        for col in columns:
            self.tree.heading(col, text=col)
            self.tree.column(col, width=widths[col], anchor="w")
        tree_scroll = ttk.Scrollbar(tracks_frame, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=tree_scroll.set)
        self.tree.pack(side="left", fill="both", expand=True)
        tree_scroll.pack(side="right", fill="y")

        self.log_text = tk.Text(logs_frame, height=14, wrap="word")
        log_scroll = ttk.Scrollbar(logs_frame, orient="vertical", command=self.log_text.yview)
        self.log_text.configure(yscrollcommand=log_scroll.set)
        self.log_text.pack(side="left", fill="both", expand=True)
        log_scroll.pack(side="right", fill="y")

    def browse_excel(self) -> None:
        path = filedialog.askopenfilename(
            title="Choose Excel file",
            filetypes=[("Excel files", "*.xlsx")],
        )
        if path:
            self.excel_path_var.set(path)
            if not self.output_dir_var.get().strip():
                excel_path = Path(path)
                self.output_dir_var.set(str(excel_path.parent / f"{excel_path.stem}_suno_music"))

    def browse_output(self) -> None:
        path = filedialog.askdirectory(title="Choose output directory")
        if path:
            self.output_dir_var.set(path)

    def log(self, message: str) -> None:
        stamp = time.strftime("%H:%M:%S")
        self.log_text.insert("end", f"{stamp} {message}\n")
        self.log_text.see("end")

    def load_excel(self) -> None:
        excel_raw = self.excel_path_var.get().strip()
        if not excel_raw:
            messagebox.showwarning("Missing Excel", "Choose an Excel file first.")
            return

        excel_path = Path(excel_raw)
        if not excel_path.exists():
            messagebox.showerror("File Not Found", str(excel_path))
            return

        try:
            wb = PromptWorkbook(excel_path)
            wb.load_or_create()
            self.tracks = wb.get_music_tracks()
        except Exception as e:
            messagebox.showerror("Load Failed", str(e))
            return

        if not self.output_dir_var.get().strip():
            self.output_dir_var.set(str(excel_path.parent / f"{excel_path.stem}_suno_music"))

        self.refresh_tree()
        self.status_var.set(f"Loaded {len(self.tracks)} tracks")
        self.log(f"Loaded {excel_path.name}: {len(self.tracks)} music tracks")

    def refresh_tree(self) -> None:
        for item in self.tree.get_children():
            self.tree.delete(item)

        for track in self.tracks:
            self.tree.insert(
                "",
                "end",
                values=(
                    track.get("music_id", ""),
                    track.get("title", ""),
                    track.get("start_time", ""),
                    track.get("duration", ""),
                    track.get("mood", ""),
                    track.get("status", ""),
                    track.get("suno_prompt", "")[:160],
                ),
            )

    def set_running(self, running: bool) -> None:
        self.start_btn.configure(state="disabled" if running else "normal")
        self.stop_btn.configure(state="normal" if running else "disabled")

    def update_track_status(self, excel_path: Path, music_id: str, **kwargs) -> None:
        wb = PromptWorkbook(excel_path)
        wb.load_or_create()
        wb.update_music_track(music_id, **kwargs)

    def start_batch(self) -> None:
        if self.worker_thread and self.worker_thread.is_alive():
            return

        excel_raw = self.excel_path_var.get().strip()
        output_raw = self.output_dir_var.get().strip()
        if not excel_raw or not output_raw:
            messagebox.showwarning("Missing Paths", "Choose Excel and output directory first.")
            return

        excel_path = Path(excel_raw)
        output_dir = Path(output_raw)
        output_dir.mkdir(parents=True, exist_ok=True)
        self.stop_requested = False
        self.set_running(True)
        self.status_var.set("Running")

        args = (excel_path, output_dir, bool(self.skip_existing_var.get()))
        self.worker_thread = threading.Thread(target=self._run_batch_worker, args=args, daemon=True)
        self.worker_thread.start()

    def stop_batch(self) -> None:
        self.stop_requested = True
        self.log("Stop requested")
        self.status_var.set("Stopping...")

    def _queue(self, kind: str, payload) -> None:
        self.ui_queue.put((kind, payload))

    def _run_batch_worker(self, excel_path: Path, output_dir: Path, skip_existing: bool) -> None:
        try:
            self._queue("log", f"Start batch with {excel_path.name}")
            wb = PromptWorkbook(excel_path)
            wb.load_or_create()
            tracks = wb.get_music_tracks()
            self._queue("tracks", tracks)

            with TokenManager() as tm:
                worker = BrowserSunoWorker(tm._page)

                for idx, track in enumerate(tracks, start=1):
                    if self.stop_requested:
                        self._queue("log", "Batch stopped by user")
                        break

                    music_id = str(track.get("music_id", "")).strip()
                    prompt = (track.get("suno_prompt") or "").strip()
                    title = (track.get("title") or f"Track {music_id}").strip()
                    status = (track.get("status") or "").strip().lower()
                    output_path = output_dir / f"{music_id}.mp3"

                    if not music_id or not prompt:
                        self._queue("log", f"[SKIP] row {idx}: missing music_id or prompt")
                        continue

                    if skip_existing and output_path.exists() and status == "done":
                        self._queue("log", f"[SKIP] {music_id}: already done")
                        continue

                    self.update_track_status(excel_path, music_id, status="generating")
                    self._queue("log", f"[{idx}/{len(tracks)}] Generating {music_id} - {title}")

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
                        self.update_track_status(excel_path, music_id, status="done", suno_url=result)
                        self._queue("log", f"[OK] {music_id} -> {output_path.name}")
                    else:
                        self.update_track_status(excel_path, music_id, status="error")
                        self._queue("log", f"[FAIL] {music_id}: {result}")
                        break

                    wb = PromptWorkbook(excel_path)
                    wb.load_or_create()
                    self._queue("tracks", wb.get_music_tracks())
                    time.sleep(8)

        except Exception as e:
            self._queue("log", f"[FATAL] {e}")
        finally:
            self._queue("done", None)

    def _poll_ui_queue(self) -> None:
        try:
            while True:
                kind, payload = self.ui_queue.get_nowait()
                if kind == "log":
                    self.log(payload)
                elif kind == "tracks":
                    self.tracks = payload
                    self.refresh_tree()
                elif kind == "done":
                    self.set_running(False)
                    self.status_var.set("Idle")
                    self.load_excel()
        except queue.Empty:
            pass
        self.root.after(250, self._poll_ui_queue)


def main() -> None:
    root = tk.Tk()
    app = SunoDesktopApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
