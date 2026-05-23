#!/usr/bin/env python3
"""
SRT to Excel Tool - GUI
========================
Giao diện đồ họa để:
  1. Load file SRT (hoặc MP3 → tự chuyển SRT qua Whisper)
  2. Tạo Excel có characters + scenes từ SRT
  3. Generate prompts bằng AI API (DeepSeek / Gemini / Groq)
  4. Hiển thị tiến trình và kết quả từng bước

Author : Antigravity
"""

import sys
import os

# Fix Windows encoding
if sys.platform == "win32":
    for s in (sys.stdout, sys.stderr):
        if s and hasattr(s, "reconfigure"):
            try:
                s.reconfigure(encoding="utf-8", errors="replace")
            except Exception:
                pass

TOOL_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, TOOL_DIR)

import tkinter as tk
from tkinter import ttk, filedialog, messagebox, scrolledtext
import threading
import queue
import json
import shutil
import yaml
from pathlib import Path
from datetime import datetime
from typing import Optional

# ─────────────────────────────────────────────
# COLORS / THEME
# ─────────────────────────────────────────────
BG       = "#0f1117"       # nền chính
PANEL    = "#1a1d27"       # nền panel
CARD     = "#22263a"       # nền card
ACCENT   = "#6c63ff"       # tím accent
ACCENT2  = "#00d4aa"       # xanh ngọc
WARN     = "#f5a623"       # cam cảnh báo
ERR      = "#ff4d4d"       # đỏ lỗi
OK       = "#43e97b"       # xanh lá ok
FG       = "#e8eaf6"       # chữ chính
FG2      = "#9e9e9e"       # chữ phụ
BORDER   = "#2e3350"       # viền

FONT_TITLE  = ("Segoe UI", 11, "bold")
FONT_BODY   = ("Segoe UI", 9)
FONT_SMALL  = ("Segoe UI", 8)
FONT_MONO   = ("Consolas", 9)
FONT_CODE   = ("Consolas", 8)


# ─────────────────────────────────────────────
# HELPER - format thời gian
# ─────────────────────────────────────────────
def fmt_time(sec: float) -> str:
    m, s = divmod(int(sec), 60)
    h, m = divmod(m, 60)
    if h:
        return f"{h:02d}:{m:02d}:{s:02d}"
    return f"{m:02d}:{s:02d}"


# ─────────────────────────────────────────────
# STYLED WIDGETS
# ─────────────────────────────────────────────
class StyledButton(tk.Button):
    def __init__(self, parent, text, command=None, color=ACCENT, **kw):
        super().__init__(
            parent,
            text=text,
            command=command,
            bg=color,
            fg="#ffffff",
            activebackground=color,
            activeforeground="#ffffff",
            relief="flat",
            bd=0,
            padx=14,
            pady=6,
            cursor="hand2",
            font=FONT_TITLE,
            **kw,
        )
        self.bind("<Enter>", lambda e: self.config(bg=self._lighten(color)))
        self.bind("<Leave>", lambda e: self.config(bg=color))

    @staticmethod
    def _lighten(hex_color: str) -> str:
        """Brighten a hex color slightly."""
        c = hex_color.lstrip("#")
        if len(c) == 3:
            c = "".join(ch * 2 for ch in c)
        if len(c) != 6:
            return hex_color
        rgb = tuple(min(255, int(c[i:i+2], 16) + 30) for i in (0, 2, 4))
        return "#{:02x}{:02x}{:02x}".format(*rgb)


class StatusBadge(tk.Label):
    COLORS = {
        "pending":  ("#555", "#aaa"),
        "running":  (WARN,  "#fff"),
        "done":     (OK,    "#000"),
        "error":    (ERR,   "#fff"),
        "skip":     (FG2,   "#000"),
    }

    def __init__(self, parent, status="pending", **kw):
        bg, fg = self.COLORS.get(status, ("#555", "#aaa"))
        super().__init__(
            parent,
            text=f" {status.upper()} ",
            bg=bg, fg=fg,
            font=FONT_SMALL,
            relief="flat",
            padx=4, pady=1,
            **kw,
        )

    def set_status(self, status: str):
        bg, fg = self.COLORS.get(status, ("#555", "#aaa"))
        self.config(text=f" {status.upper()} ", bg=bg, fg=fg)


class SeparatorH(tk.Frame):
    def __init__(self, parent, **kw):
        super().__init__(parent, bg=BORDER, height=1, **kw)


# ─────────────────────────────────────────────
# STEP ROW WIDGET
# ─────────────────────────────────────────────
class StepRow(tk.Frame):
    """Một dòng step hiển thị: số, tên, trạng thái, progress bar."""

    def __init__(self, parent, step_no: int, label: str, **kw):
        super().__init__(parent, bg=CARD, **kw)
        self.step_no = step_no

        # Số bước
        tk.Label(self, text=f"{step_no}", bg=ACCENT, fg="#fff",
                 font=FONT_TITLE, width=3, pady=6).pack(side="left")

        # Tên bước
        tk.Label(self, text=label, bg=CARD, fg=FG, font=FONT_BODY,
                 anchor="w", padx=8).pack(side="left", fill="x", expand=True)

        # Badge trạng thái
        self.badge = StatusBadge(self, "pending")
        self.badge.pack(side="right", padx=8)

        # Progress bar nhỏ
        self.pbar = ttk.Progressbar(self, orient="horizontal",
                                    length=120, mode="determinate")
        self.pbar.pack(side="right", padx=4)

    def set_status(self, status: str, pct: int = 0):
        self.badge.set_status(status)
        self.pbar["value"] = pct
        if status == "running":
            self.pbar.config(mode="indeterminate")
            self.pbar.start(12)
        else:
            self.pbar.stop()
            self.pbar.config(mode="determinate")
            self.pbar["value"] = 100 if status == "done" else pct


# ─────────────────────────────────────────────
# MAIN APP
# ─────────────────────────────────────────────
class SrtToExcelApp(tk.Tk):

    STEPS = [
        "Phan tich cau chuyen",
        "Chia segments",
        "Tao nhan vat",
        "Tao boi canh (locations)",
        "Ke hoach dao dien",
        "Y do nghe thuat",
        "Tao prompts tung scene",
    ]

    def __init__(self):
        super().__init__()

        self.title("SRT → Excel Tool")
        self.geometry("1040x720")
        self.minsize(900, 600)
        self.configure(bg=BG)
        self.resizable(True, True)

        # App state
        self.srt_path:     Optional[Path] = None
        self.project_dir:  Optional[Path] = None
        self.project_name: str = ""
        self.running = False
        self.log_queue: queue.Queue = queue.Queue()
        self.cfg: dict = {}

        # Load config
        self._load_config()

        # Build UI
        self._build_ui()

        # ttk style
        self._apply_ttk_style()

        # Poll log queue
        self._poll_log()

    # ── CONFIG ─────────────────────────────────
    def _load_config(self):
        cfg_path = Path(TOOL_DIR) / "config" / "settings.yaml"
        if cfg_path.exists():
            with open(cfg_path, "r", encoding="utf-8") as f:
                self.cfg = yaml.safe_load(f) or {}
        else:
            self.cfg = {}

    def _save_config(self):
        cfg_path = Path(TOOL_DIR) / "config" / "settings.yaml"
        cfg_path.parent.mkdir(parents=True, exist_ok=True)
        # Cập nhật từ UI
        self.cfg["deepseek_api_key"] = self.var_deepseek.get().strip()
        self.cfg["gemini_api_keys"] = [k.strip() for k in self.var_gemini.get().split(",") if k.strip()]
        self.cfg["groq_api_keys"] = [k.strip() for k in self.var_groq.get().split(",") if k.strip()]
        self.cfg["whisper_model"] = self.var_whisper_model.get()
        self.cfg["whisper_language"] = self.var_whisper_lang.get()
        self.cfg["min_scene_duration"] = int(self.var_min_dur.get() or 5)
        self.cfg["max_scene_duration"] = int(self.var_max_dur.get() or 8)
        with open(cfg_path, "w", encoding="utf-8") as f:
            yaml.dump(self.cfg, f, allow_unicode=True, sort_keys=False)

    # ── TTK STYLE ──────────────────────────────
    def _apply_ttk_style(self):
        s = ttk.Style(self)
        s.theme_use("default")
        s.configure("TProgressbar",
                     troughcolor=CARD,
                     background=ACCENT,
                     thickness=6)
        s.configure("Notebook.TNotebook", background=PANEL, borderwidth=0)
        s.configure("TNotebook.Tab",
                     background=PANEL, foreground=FG2,
                     padding=[14, 6], font=FONT_BODY)
        s.map("TNotebook.Tab",
              background=[("selected", CARD)],
              foreground=[("selected", FG)])

    # ── UI BUILD ───────────────────────────────
    def _build_ui(self):
        # ── Header ──
        hdr = tk.Frame(self, bg=PANEL, height=52)
        hdr.pack(fill="x")
        hdr.pack_propagate(False)

        tk.Label(hdr, text="⚡ SRT  →  EXCEL",
                 bg=PANEL, fg=ACCENT, font=("Segoe UI", 14, "bold"),
                 padx=20).pack(side="left", pady=10)
        tk.Label(hdr, text="Tạo prompts cinematographic từ file SRT",
                 bg=PANEL, fg=FG2, font=FONT_BODY).pack(side="left", pady=10)

        # Version badge
        tk.Label(hdr, text=" v1.0 ", bg=ACCENT2, fg="#000",
                 font=FONT_SMALL, padx=4).pack(side="right", padx=16, pady=16)

        SeparatorH(self).pack(fill="x")

        # ── Main content (notebook) ──
        self.nb = ttk.Notebook(self)
        self.nb.pack(fill="both", expand=True, padx=0, pady=0)

        tab1 = tk.Frame(self.nb, bg=BG)
        tab2 = tk.Frame(self.nb, bg=BG)
        tab3 = tk.Frame(self.nb, bg=BG)

        self.nb.add(tab1, text="  📂  Tải file & Chạy  ")
        self.nb.add(tab2, text="  ⚙️  Cài đặt  ")
        self.nb.add(tab3, text="  📊  Xem Excel  ")

        self._build_tab_run(tab1)
        self._build_tab_settings(tab2)
        self._build_tab_excel(tab3)

        # ── Status bar ──
        sbar = tk.Frame(self, bg=PANEL, height=28)
        sbar.pack(fill="x", side="bottom")
        sbar.pack_propagate(False)
        self.lbl_status = tk.Label(sbar, text="Sẵn sàng", bg=PANEL,
                                   fg=FG2, font=FONT_SMALL, padx=10)
        self.lbl_status.pack(side="left")

        self.lbl_time = tk.Label(sbar, text="", bg=PANEL,
                                 fg=FG2, font=FONT_SMALL, padx=10)
        self.lbl_time.pack(side="right")
        self._tick()

    # ─────────────────────────────────────────
    # TAB 1: RUN
    # ─────────────────────────────────────────
    def _build_tab_run(self, parent: tk.Frame):
        # Left pane: file picker + steps
        left = tk.Frame(parent, bg=BG, width=380)
        left.pack(side="left", fill="y", padx=0)
        left.pack_propagate(False)

        # Right pane: log
        right = tk.Frame(parent, bg=BG)
        right.pack(side="left", fill="both", expand=True)

        # ─ File picker ─
        pick_card = tk.Frame(left, bg=CARD, padx=16, pady=14)
        pick_card.pack(fill="x", padx=10, pady=(12, 6))

        tk.Label(pick_card, text="📁  File SRT / MP3 / WAV",
                 bg=CARD, fg=FG, font=FONT_TITLE).grid(row=0, column=0, columnspan=2, sticky="w")

        self.lbl_file = tk.Label(pick_card, text="Chưa chọn file...",
                                 bg=CARD, fg=FG2, font=FONT_SMALL,
                                 wraplength=280, anchor="w", justify="left")
        self.lbl_file.grid(row=1, column=0, columnspan=2, sticky="w", pady=(6, 8))

        StyledButton(pick_card, "Chọn SRT", command=self._pick_srt,
                     color=ACCENT).grid(row=2, column=0, sticky="ew", padx=(0, 4))
        StyledButton(pick_card, "Chọn MP3/WAV", command=self._pick_audio,
                     color="#3d5a99").grid(row=2, column=1, sticky="ew")

        pick_card.columnconfigure(0, weight=1)
        pick_card.columnconfigure(1, weight=1)

        # Project name
        pname_card = tk.Frame(left, bg=CARD, padx=16, pady=10)
        pname_card.pack(fill="x", padx=10, pady=4)

        tk.Label(pname_card, text="🏷  Tên project",
                 bg=CARD, fg=FG, font=FONT_TITLE).pack(anchor="w")
        self.var_project_name = tk.StringVar(value="")
        tk.Entry(pname_card, textvariable=self.var_project_name,
                 bg=PANEL, fg=FG, insertbackground=FG, relief="flat",
                 font=FONT_BODY).pack(fill="x", pady=(4, 0))

        # ─ Steps ─
        steps_lbl = tk.Frame(left, bg=BG)
        steps_lbl.pack(fill="x", padx=10, pady=(10, 4))
        tk.Label(steps_lbl, text="TIẾN TRÌNH (7 BƯỚC AI)",
                 bg=BG, fg=FG2, font=FONT_SMALL).pack(anchor="w")

        steps_frame = tk.Frame(left, bg=BG)
        steps_frame.pack(fill="x", padx=10)

        self.step_rows: list[StepRow] = []
        for i, label in enumerate(self.STEPS, 1):
            row = StepRow(steps_frame, i, label)
            row.pack(fill="x", pady=2)
            self.step_rows.append(row)

        # ─ Run button ─
        btn_frame = tk.Frame(left, bg=BG)
        btn_frame.pack(fill="x", padx=10, pady=12)

        self.btn_run = StyledButton(btn_frame, "▶  Tạo Excel + Prompts",
                                   command=self._start_pipeline,
                                   color=OK)
        self.btn_run.config(fg="#000")
        self.btn_run.pack(fill="x", pady=(0, 4))

        self.btn_stop = StyledButton(btn_frame, "⏹  Dừng",
                                    command=self._stop_pipeline,
                                    color=ERR)
        self.btn_stop.pack(fill="x")
        self.btn_stop.config(state="disabled")

        # ─ Overall progress ─
        prog_frame = tk.Frame(left, bg=BG)
        prog_frame.pack(fill="x", padx=10)
        tk.Label(prog_frame, text="Tổng tiến trình:", bg=BG,
                 fg=FG2, font=FONT_SMALL).pack(anchor="w")
        self.overall_bar = ttk.Progressbar(prog_frame, orient="horizontal",
                                           length=100, mode="determinate")
        self.overall_bar.pack(fill="x")

        # ─ LOG pane (right) ─
        log_hdr = tk.Frame(right, bg=PANEL, height=36)
        log_hdr.pack(fill="x")
        log_hdr.pack_propagate(False)

        tk.Label(log_hdr, text="📋  LOG", bg=PANEL, fg=FG,
                 font=FONT_TITLE, padx=12).pack(side="left")

        StyledButton(log_hdr, "Xóa log", command=self._clear_log,
                     color="#333").pack(side="right", padx=8, pady=4)

        StyledButton(log_hdr, "📂 Mở thư mục", command=self._open_folder,
                     color="#2c3a5e").pack(side="right", pady=4)

        self.log_text = scrolledtext.ScrolledText(
            right, bg="#0a0c12", fg="#c7f0c7",
            font=FONT_CODE, insertbackground=FG,
            state="disabled", relief="flat", bd=0,
            wrap="word",
        )
        self.log_text.pack(fill="both", expand=True)

        # Configure tags
        self.log_text.tag_config("ok",   foreground=OK)
        self.log_text.tag_config("warn", foreground=WARN)
        self.log_text.tag_config("err",  foreground=ERR)
        self.log_text.tag_config("info", foreground="#c7f0c7")
        self.log_text.tag_config("step", foreground=ACCENT)

    # ─────────────────────────────────────────
    # TAB 2: SETTINGS
    # ─────────────────────────────────────────
    def _build_tab_settings(self, parent: tk.Frame):
        canvas = tk.Canvas(parent, bg=BG, highlightthickness=0)
        scroll = ttk.Scrollbar(parent, orient="vertical", command=canvas.yview)
        canvas.configure(yscrollcommand=scroll.set)
        scroll.pack(side="right", fill="y")
        canvas.pack(side="left", fill="both", expand=True)

        inner = tk.Frame(canvas, bg=BG)
        win_id = canvas.create_window((0, 0), window=inner, anchor="nw")

        def _on_resize(e):
            canvas.itemconfig(win_id, width=e.width)
        canvas.bind("<Configure>", _on_resize)
        inner.bind("<Configure>", lambda e: canvas.configure(
            scrollregion=canvas.bbox("all")))

        # ─ API Keys card ─
        self._settings_card(inner, "🔑  API Keys", [
            ("DeepSeek API Key",   "var_deepseek",
             self.cfg.get("deepseek_api_key", "")),
            ("Gemini API Keys (phẩy nếu nhiều key)",   "var_gemini",
             ", ".join(self.cfg.get("gemini_api_keys", []))),
            ("Groq API Keys (phẩy nếu nhiều key)",     "var_groq",
             ", ".join(self.cfg.get("groq_api_keys", []))),
        ], row_start=0)

        # ─ Whisper card ─
        w_card = tk.Frame(inner, bg=CARD, padx=16, pady=14)
        w_card.pack(fill="x", padx=20, pady=8)
        tk.Label(w_card, text="🎤  Whisper (Audio → SRT)",
                 bg=CARD, fg=FG, font=FONT_TITLE).pack(anchor="w", pady=(0, 8))

        wrow = tk.Frame(w_card, bg=CARD)
        wrow.pack(fill="x")

        tk.Label(wrow, text="Model:", bg=CARD, fg=FG2, font=FONT_BODY,
                 width=20, anchor="w").grid(row=0, column=0, sticky="w")
        self.var_whisper_model = tk.StringVar(
            value=self.cfg.get("whisper_model", "large-v3"))
        ttk.Combobox(wrow, textvariable=self.var_whisper_model,
                     values=["tiny", "base", "small", "medium", "large", "large-v2", "large-v3", "distil-large-v3"],
                     state="readonly", width=16).grid(row=0, column=1, sticky="w")

        tk.Label(wrow, text="Ngôn ngữ:", bg=CARD, fg=FG2, font=FONT_BODY,
                 width=20, anchor="w").grid(row=1, column=0, sticky="w", pady=4)
        self.var_whisper_lang = tk.StringVar(
            value=self.cfg.get("whisper_language", "auto"))
        ttk.Combobox(wrow, textvariable=self.var_whisper_lang,
                     values=["auto", "en", "vi", "es", "fr", "de", "pt", "ja", "ko", "zh"],
                     state="readonly", width=16).grid(row=1, column=1, sticky="w")

        # ─ Scene duration card ─
        d_card = tk.Frame(inner, bg=CARD, padx=16, pady=14)
        d_card.pack(fill="x", padx=20, pady=8)
        tk.Label(d_card, text="⏱  Thời lượng scene",
                 bg=CARD, fg=FG, font=FONT_TITLE).pack(anchor="w", pady=(0, 8))

        drow = tk.Frame(d_card, bg=CARD)
        drow.pack(fill="x")

        tk.Label(drow, text="Tối thiểu (giây):", bg=CARD, fg=FG2,
                 font=FONT_BODY, width=20, anchor="w").grid(row=0, column=0, sticky="w")
        self.var_min_dur = tk.StringVar(
            value=str(self.cfg.get("min_scene_duration", 5)))
        tk.Entry(drow, textvariable=self.var_min_dur,
                 bg=PANEL, fg=FG, insertbackground=FG, relief="flat",
                 width=8).grid(row=0, column=1, sticky="w")

        tk.Label(drow, text="Tối đa (giây):", bg=CARD, fg=FG2,
                 font=FONT_BODY, width=20, anchor="w").grid(row=1, column=0, sticky="w", pady=4)
        self.var_max_dur = tk.StringVar(
            value=str(self.cfg.get("max_scene_duration", 8)))
        tk.Entry(drow, textvariable=self.var_max_dur,
                 bg=PANEL, fg=FG, insertbackground=FG, relief="flat",
                 width=8).grid(row=1, column=1, sticky="w")

        # Save button
        btn_save = StyledButton(inner, "💾  Lưu cài đặt",
                                command=self._on_save_settings,
                                color=ACCENT2)
        btn_save.config(fg="#000")
        btn_save.pack(padx=20, pady=16, anchor="w")

    def _settings_card(self, parent, title, fields, row_start=0):
        card = tk.Frame(parent, bg=CARD, padx=16, pady=14)
        card.pack(fill="x", padx=20, pady=8)
        tk.Label(card, text=title, bg=CARD, fg=FG, font=FONT_TITLE).pack(anchor="w", pady=(0, 8))

        for i, (label, varname, default) in enumerate(fields):
            tk.Label(card, text=label, bg=CARD, fg=FG2,
                     font=FONT_BODY).pack(anchor="w")
            var = tk.StringVar(value=default)
            setattr(self, varname, var)
            tk.Entry(card, textvariable=var,
                     bg=PANEL, fg=FG, insertbackground=FG,
                     relief="flat", font=FONT_BODY).pack(fill="x", pady=(2, 8))

    # ─────────────────────────────────────────
    # TAB 3: XEM EXCEL
    # ─────────────────────────────────────────
    def _build_tab_excel(self, parent: tk.Frame):
        toolbar = tk.Frame(parent, bg=PANEL, height=40)
        toolbar.pack(fill="x")
        toolbar.pack_propagate(False)

        StyledButton(toolbar, "🔄  Reload", command=self._reload_excel,
                     color=ACCENT).pack(side="left", padx=8, pady=6)

        StyledButton(toolbar, "📂  Mở file Excel", command=self._open_excel_file,
                     color="#2c3a5e").pack(side="left", pady=6)

        self.lbl_excel_path = tk.Label(toolbar, text="Chưa có file Excel",
                                       bg=PANEL, fg=FG2, font=FONT_SMALL)
        self.lbl_excel_path.pack(side="left", padx=12)

        # Tabs bên trong: Characters / Scenes
        inner_nb = ttk.Notebook(parent)
        inner_nb.pack(fill="both", expand=True)

        chars_tab = tk.Frame(inner_nb, bg=BG)
        scenes_tab = tk.Frame(inner_nb, bg=BG)
        inner_nb.add(chars_tab, text="  👤 Nhân vật  ")
        inner_nb.add(scenes_tab, text="  🎬 Scenes  ")

        # Characters tree
        self.tree_chars = self._build_tree(
            chars_tab,
            columns=["ID", "Tên", "Vai trò", "Trạng thái", "english_prompt"],
            widths=[60, 120, 80, 80, 500],
        )

        # Scenes tree
        self.tree_scenes = self._build_tree(
            scenes_tab,
            columns=["#", "Bắt đầu", "Kết thúc", "Độ dài", "Text", "Trạng thái"],
            widths=[40, 90, 90, 60, 240, 70],
        )

    def _build_tree(self, parent, columns, widths):
        frame = tk.Frame(parent, bg=BG)
        frame.pack(fill="both", expand=True, padx=4, pady=4)

        sb_y = ttk.Scrollbar(frame, orient="vertical")
        sb_y.pack(side="right", fill="y")
        sb_x = ttk.Scrollbar(frame, orient="horizontal")
        sb_x.pack(side="bottom", fill="x")

        tv = ttk.Treeview(frame, columns=columns, show="headings",
                          yscrollcommand=sb_y.set,
                          xscrollcommand=sb_x.set)
        sb_y.config(command=tv.yview)
        sb_x.config(command=tv.xview)

        for col, w in zip(columns, widths):
            tv.heading(col, text=col)
            tv.column(col, width=w, minwidth=40)

        style = ttk.Style()
        style.configure("Treeview",
                        background=CARD, foreground=FG,
                        fieldbackground=CARD, rowheight=22,
                        font=FONT_SMALL)
        style.configure("Treeview.Heading",
                        background=PANEL, foreground=FG,
                        font=FONT_BODY)
        style.map("Treeview", background=[("selected", ACCENT)])

        tv.pack(fill="both", expand=True)
        return tv

    # ─────────────────────────────────────────
    # ACTIONS
    # ─────────────────────────────────────────
    def _pick_srt(self):
        path = filedialog.askopenfilename(
            title="Chọn file SRT",
            filetypes=[("SRT Files", "*.srt"), ("All files", "*.*")],
        )
        if not path:
            return
        self.srt_path = Path(path)
        name = self.srt_path.stem
        self.var_project_name.set(name)
        self.lbl_file.config(text=str(self.srt_path), fg=ACCENT2)
        self._log(f"Đã chọn SRT: {self.srt_path.name}", "ok")
        self._setup_project_dir()

    def _pick_audio(self):
        path = filedialog.askopenfilename(
            title="Chọn file Audio",
            filetypes=[
                ("Audio Files", "*.mp3 *.wav *.m4a *.ogg"),
                ("All files", "*.*"),
            ],
        )
        if not path:
            return
        audio_path = Path(path)
        name = audio_path.stem
        self.var_project_name.set(name)
        self.lbl_file.config(text=str(audio_path), fg=WARN)
        self._log(f"Đã chọn audio: {audio_path.name}  (sẽ dùng Whisper để tạo SRT)", "warn")
        self._setup_project_dir(audio_path=audio_path)

    def _setup_project_dir(self, audio_path: Optional[Path] = None):
        """Chuẩn bị thư mục project."""
        name = self.var_project_name.get().strip() or (
            self.srt_path.stem if self.srt_path else "project"
        )
        self.project_name = name
        projects_root = Path(TOOL_DIR) / self.cfg.get("project_root", "./PROJECTS")
        self.project_dir = projects_root / name
        self.project_dir.mkdir(parents=True, exist_ok=True)

        # Copy SRT
        if self.srt_path and self.srt_path.exists():
            dst_srt = self.project_dir / f"{name}.srt"
            if not dst_srt.exists():
                shutil.copy2(self.srt_path, dst_srt)
            self.srt_path = dst_srt

        # Copy audio để chuyển SRT sau
        if audio_path:
            dst_audio = self.project_dir / audio_path.name
            if not dst_audio.exists():
                shutil.copy2(audio_path, dst_audio)
            # Lưu vào state để dùng khi chạy
            self._pending_audio = dst_audio
        else:
            self._pending_audio = None

        self._log(f"Thư mục project: {self.project_dir}", "info")

    def _start_pipeline(self):
        if self.running:
            return

        if not self.srt_path and not getattr(self, "_pending_audio", None):
            messagebox.showwarning("Chưa chọn file",
                                   "Vui lòng chọn file SRT hoặc file audio.")
            return

        # Lưu config trước
        self._save_config()
        self._load_config()

        # Reset UI
        self._reset_steps()
        self._clear_log()
        self.running = True
        self.btn_run.config(state="disabled")
        self.btn_stop.config(state="normal")
        self.overall_bar["value"] = 0

        # Chạy pipeline trong thread riêng
        t = threading.Thread(target=self._run_pipeline_thread, daemon=True)
        t.start()

    def _stop_pipeline(self):
        self.running = False
        self._log("⏹  Đã yêu cầu dừng...", "warn")
        self.btn_stop.config(state="disabled")

    def _run_pipeline_thread(self):
        """Pipeline chạy trong background thread."""
        try:
            name = self.var_project_name.get().strip() or "project"
            self.project_name = name

            # ── Step 0: Tạo SRT từ audio nếu cần ──────────────
            if getattr(self, "_pending_audio", None) and (
                not self.srt_path or not self.srt_path.exists()
            ):
                self._log("🎤  Bước 0: Chuyển audio → SRT (Whisper)...", "step")
                srt_out = self.project_dir / f"{name}.srt"
                try:
                    from modules.voice_to_srt import VoiceToSrt
                    _raw_lang = self.cfg.get("whisper_language", "auto")
                    # "auto" → None (Whisper auto-detect), else pass code directly
                    _lang = None if _raw_lang == "auto" else (_raw_lang or "auto")
                    v = VoiceToSrt(
                        model_name=self.cfg.get("whisper_model", "large-v3"),
                        language=_lang,
                    )
                    v.transcribe(self._pending_audio, srt_out)
                    self.srt_path = srt_out
                    self._log(f"✅  SRT tạo xong: {srt_out.name}", "ok")
                except Exception as e:
                    self._log(f"❌  Whisper lỗi: {e}", "err")
                    self._finish_pipeline(False)
                    return

            if not self.srt_path or not self.srt_path.exists():
                self._log("❌  Không tìm thấy file SRT!", "err")
                self._finish_pipeline(False)
                return

            # ── Steps 1–7: Progressive API ─────────────────────
            try:
                from modules.progressive_prompts import ProgressivePromptsGenerator
                gen = ProgressivePromptsGenerator(self.cfg)
            except Exception as e:
                self._log(f"❌  Không load được module: {e}", "err")
                # Thử fallback cơ bản
                self._run_fallback_pipeline()
                return

            def on_step(step_idx: int, status: str, msg: str = ""):
                """Callback từ ProgressivePromptsGenerator."""
                if 1 <= step_idx <= 7:
                    row = self.step_rows[step_idx - 1]
                    pct = {
                        "start":  0,
                        "running": 50,
                        "done":  100,
                        "error":   0,
                    }.get(status, 0)
                    self.after(0, lambda r=row, s=status, p=pct: r.set_status(s, p))
                    self.after(0, lambda: self.overall_bar.config(
                        value=int((step_idx - 1) / 7 * 100)))

                if msg:
                    tag = {"done": "ok", "error": "err", "running": "info"}.get(status, "info")
                    self._log(msg, tag)

            self._log(f"🚀  Bắt đầu pipeline cho project: {name}", "step")
            self._log(f"    SRT: {self.srt_path}", "info")
            self._log("─" * 60, "info")

            success = gen.run_all_steps(
                self.project_dir,
                name,
                log_callback=lambda msg, level="INFO": (
                    self._log(f"   {msg}",
                              {"SUCCESS": "ok", "WARN": "warn",
                               "ERROR": "err"}.get(level, "info"))
                ),
                step_callback=on_step if hasattr(gen, "run_all_steps") else None,
            )

            # Đánh dấu tất cả steps
            for i, row in enumerate(self.step_rows):
                self.after(0, lambda r=row: r.set_status("done"))
            self.after(0, lambda: self.overall_bar.config(value=100))

            if success:
                self._log("", "info")
                self._log("✅  Pipeline hoàn thành! Excel đã được tạo.", "ok")
                self._log(f"📂  {self.project_dir}", "ok")
                self.after(200, self._reload_excel)
            else:
                self._log("⚠️  Pipeline kết thúc với lỗi (xem log ở trên).", "warn")

        except Exception as ex:
            import traceback
            self._log(f"❌  Lỗi không mong đợi: {ex}", "err")
            self._log(traceback.format_exc(), "err")
        finally:
            self._finish_pipeline(True)

    def _run_fallback_pipeline(self):
        """Fallback: chỉ tạo Excel từ SRT mà không dùng AI."""
        self._log("⚡  Fallback mode: Tạo Excel cơ bản từ SRT...", "warn")
        try:
            from modules.utils import parse_srt_file, group_srt_into_scenes
            from modules.excel_manager import PromptWorkbook, Scene

            entries = parse_srt_file(self.srt_path)
            min_d = int(self.cfg.get("min_scene_duration", 5))
            max_d = int(self.cfg.get("max_scene_duration", 8))
            scenes_data = group_srt_into_scenes(entries, min_d, max_d)

            excel_path = self.project_dir / f"{self.project_name}_prompts.xlsx"
            wb = PromptWorkbook(excel_path)
            wb.load_or_create()

            for s in scenes_data:
                scene = Scene(
                    scene_id=s["scene_id"],
                    srt_start=s["srt_start"],
                    srt_end=s["srt_end"],
                    duration=s["duration_seconds"],
                    srt_text=s["text"],
                    img_prompt="[PENDING - Cần AI generate]",
                    status_img="pending",
                    status_vid="pending",
                )
                wb.add_scene(scene)

            wb.save()
            self._log(f"✅  Đã tạo Excel cơ bản: {excel_path.name}", "ok")
            self._log(f"   Tổng {len(scenes_data)} scenes từ {len(entries)} SRT entries", "ok")
            self.after(200, self._reload_excel)

        except Exception as e:
            self._log(f"❌  Fallback lỗi: {e}", "err")
        finally:
            self._finish_pipeline(True)

    def _finish_pipeline(self, ran: bool):
        self.running = False
        self.after(0, lambda: (
            self.btn_run.config(state="normal"),
            self.btn_stop.config(state="disabled"),
        ))

    # ─────────────────────────────────────────
    # EXCEL VIEWER
    # ─────────────────────────────────────────
    def _reload_excel(self):
        if not self.project_dir:
            return

        name = self.var_project_name.get().strip() or self.project_name
        excel_path = self.project_dir / f"{name}_prompts.xlsx"

        if not excel_path.exists():
            self._log("Chưa có file Excel để hiển thị.", "warn")
            return

        self.lbl_excel_path.config(text=str(excel_path), fg=ACCENT2)

        try:
            from modules.excel_manager import PromptWorkbook
            wb = PromptWorkbook(excel_path)
            wb.load_or_create()

            # Characters
            for row in self.tree_chars.get_children():
                self.tree_chars.delete(row)
            for ch in wb.get_characters():
                self.tree_chars.insert("", "end", values=(
                    ch.id, ch.name, ch.role, ch.status, ch.english_prompt[:80],
                ))

            # Scenes
            for row in self.tree_scenes.get_children():
                self.tree_scenes.delete(row)
            for sc in wb.get_scenes():
                self.tree_scenes.insert("", "end", values=(
                    sc.scene_id,
                    sc.srt_start,
                    sc.srt_end,
                    f"{sc.duration:.1f}s",
                    sc.srt_text[:60],
                    sc.status_img,
                ))

            self._log(f"📊  Đã tải Excel: {excel_path.name}", "ok")

            # Switch to excel tab
            self.nb.select(2)

        except Exception as e:
            self._log(f"❌  Lỗi đọc Excel: {e}", "err")

    def _open_excel_file(self):
        if not self.project_dir:
            return
        name = self.var_project_name.get().strip() or self.project_name
        excel_path = self.project_dir / f"{name}_prompts.xlsx"
        if excel_path.exists():
            os.startfile(str(excel_path))
        else:
            messagebox.showinfo("Không tìm thấy",
                                "Chưa có file Excel. Chạy pipeline trước.")

    def _open_folder(self):
        if self.project_dir and self.project_dir.exists():
            os.startfile(str(self.project_dir))

    # ─────────────────────────────────────────
    # LOG
    # ─────────────────────────────────────────
    def _log(self, msg: str, tag: str = "info"):
        """Đưa message vào queue để hiển thị an toàn từ bất kỳ thread nào."""
        ts = datetime.now().strftime("%H:%M:%S")
        self.log_queue.put((f"[{ts}]  {msg}\n", tag))

    def _poll_log(self):
        """Poll log queue mỗi 80ms."""
        try:
            while True:
                line, tag = self.log_queue.get_nowait()
                self.log_text.config(state="normal")
                self.log_text.insert("end", line, tag)
                self.log_text.see("end")
                self.log_text.config(state="disabled")
        except queue.Empty:
            pass
        self.after(80, self._poll_log)

    def _clear_log(self):
        self.log_text.config(state="normal")
        self.log_text.delete("1.0", "end")
        self.log_text.config(state="disabled")

    # ─────────────────────────────────────────
    # MISC
    # ─────────────────────────────────────────
    def _reset_steps(self):
        for row in self.step_rows:
            row.set_status("pending", 0)

    def _on_save_settings(self):
        self._save_config()
        self._log("✅  Đã lưu cài đặt.", "ok")
        messagebox.showinfo("Lưu thành công", "Cài đặt đã được lưu.")

    def _tick(self):
        """Cập nhật đồng hồ trên status bar."""
        now = datetime.now().strftime("%d/%m/%Y  %H:%M:%S")
        self.lbl_time.config(text=now)
        self.after(1000, self._tick)


# ─────────────────────────────────────────────
# ENTRY
# ─────────────────────────────────────────────
if __name__ == "__main__":
    app = SrtToExcelApp()
    app.mainloop()
