#!/usr/bin/env python3
"""
MP3 → SRT Converter
Chuyển file âm thanh thành phụ đề SRT thông minh dùng Whisper AI
"""

import sys
import os
import re
import threading
import tkinter as tk
from tkinter import filedialog, messagebox
from pathlib import Path
import subprocess


# ─── Cài thư viện nếu chưa có ────────────────────────────────────────────────
def install_if_missing(pkg, import_name=None):
    import_name = import_name or pkg
    try:
        __import__(import_name)
    except ImportError:
        subprocess.check_call([sys.executable, "-m", "pip", "install", pkg, "-q"])


# ─── Hằng số ─────────────────────────────────────────────────────────────────
BG         = "#0f0f13"
SURFACE    = "#1a1a24"
SURFACE2   = "#22222f"
ACCENT     = "#7c6af7"
ACCENT2    = "#a78bfa"
TEXT       = "#e8e6f0"
TEXT_MID   = "#9391a8"
TEXT_DIM   = "#4a4863"
SUCCESS    = "#34d399"
WARNING    = "#fbbf24"
DANGER     = "#f87171"
FONT_MAIN  = ("Segoe UI", 10)
FONT_BOLD  = ("Segoe UI", 10, "bold")
FONT_TITLE = ("Segoe UI", 22, "bold")
FONT_MONO  = ("Consolas", 9)

WHISPER_MODELS = ["tiny", "base", "base.en", "small", "small.en", "medium", "medium.en", "large", "large-v2", "large-v3"]
LANGUAGES = [
    ("Tự động phát hiện", None),
    ("Tiếng Việt", "vi"),
    ("Tiếng Anh", "en"),
    ("Tiếng Nhật", "ja"),
    ("Tiếng Hàn", "ko"),
    ("Tiếng Trung", "zh"),
    ("Tiếng Pháp", "fr"),
    ("Tiếng Tây Ban Nha", "es"),
    ("Tiếng Đức", "de"),
    ("Tiếng Bồ Đào Nha", "pt"),
    ("Tiếng Ý", "it"),
    ("Tiếng Thổ Nhĩ Kỳ", "tr"),
]

MAX_CHARS_PER_LINE = 42   # ký tự tối đa mỗi dòng sub
MAX_LINES          = 2    # số dòng tối đa mỗi cue
MAX_DURATION_SEC   = 5.0  # giây tối đa mỗi cue (capcut-like)
MIN_DURATION_SEC   = 0.7  # giây tối thiểu mỗi cue
MAX_WORDS_PER_CUE  = 12   # số từ tối đa mỗi cue


# ─── Tiện ích SRT ─────────────────────────────────────────────────────────────
def fmt_time(seconds: float) -> str:
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = int(seconds % 60)
    ms = int(round((seconds - int(seconds)) * 1000))
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


BREAK_AFTER = re.compile(
    r"(?<=[.!?…。！？])\s+|"           # Kết thúc câu
    r"(?<=,)\s+(?=\w{3,})|"            # Sau dấu phẩy (từ dài)
    r"(?<=;)\s+|"                       # Sau dấu chấm phẩy
    r"\s+(?=(?:nhưng|và|hoặc|vì|khi|nên|mà|thì|mà|để|bởi|tuy|dù|"
    r"but|and|or|because|when|so|yet|although|while|since|though|"
    r"however|moreover|therefore|meanwhile)\s)",  # Liên từ
    re.IGNORECASE
)


def smart_split_text(text: str, max_chars: int = MAX_CHARS_PER_LINE) -> list[str]:
    """Tách văn bản thành các dòng sub hợp lý."""
    text = text.strip()
    if not text:
        return []

    # Thử đặt vừa 1 dòng
    if len(text) <= max_chars:
        return [text]

    # Tách tại ranh giới ngữ nghĩa tự nhiên
    parts = re.split(BREAK_AFTER, text)

    lines, current = [], ""
    for part in parts:
        part = part.strip()
        if not part:
            continue
        candidate = (current + " " + part).strip() if current else part
        if len(candidate) <= max_chars:
            current = candidate
        else:
            if current:
                lines.append(current)
            # Nếu part vẫn quá dài → cắt từng từ
            if len(part) > max_chars:
                words, tmp = part.split(), ""
                for w in words:
                    trial = (tmp + " " + w).strip() if tmp else w
                    if len(trial) <= max_chars:
                        tmp = trial
                    else:
                        if tmp:
                            lines.append(tmp)
                        tmp = w
                current = tmp
            else:
                current = part
    if current:
        lines.append(current)
    return lines if lines else [text[:max_chars]]


def segments_to_srt(segments) -> str:
    """Chuyển Whisper segments -> chuoi SRT thong minh, uu tien word timestamps."""
    srt_blocks = []

    def _normalize_word(raw_word):
        txt = (raw_word or "").strip()
        txt = re.sub(r"\s+", " ", txt)
        return txt

    def _emit_block(words_buf, start_t, end_t):
        if not words_buf:
            return
        text = " ".join(words_buf).strip()
        if not text:
            return
        lines = smart_split_text(text)
        block_text = "\n".join(lines[:MAX_LINES])
        srt_blocks.append({"start": start_t, "end": end_t, "text": block_text})

    for seg in segments:
        raw_text = seg.get("text", "").strip()
        if not raw_text:
            continue

        start_t = float(seg["start"])
        end_t   = float(seg["end"])
        duration = end_t - start_t

        words = seg.get("words") or []
        timed_words = []
        for w in words:
            txt = _normalize_word(w.get("word") or w.get("text") or "")
            if not txt:
                continue
            ws = float(w.get("start", start_t))
            we = float(w.get("end", ws))
            if we < ws:
                we = ws
            timed_words.append((txt, ws, we))

        # Preferred path: split using real word timestamps.
        if timed_words:
            buf_words = []
            cue_start = None
            cue_end = None

            i = 0
            while i < len(timed_words):
                txt, ws, we = timed_words[i]

                if cue_start is None:
                    cue_start = ws
                    cue_end = we
                    buf_words = [txt]
                    i += 1
                    continue

                trial_words = buf_words + [txt]
                trial_text = " ".join(trial_words)
                trial_lines = smart_split_text(trial_text)
                trial_dur = we - cue_start

                # Stop before overflow, emit current cue first.
                if len(trial_lines) > MAX_LINES or trial_dur > MAX_DURATION_SEC or len(trial_words) > MAX_WORDS_PER_CUE:
                    if cue_end - cue_start < MIN_DURATION_SEC and i < len(timed_words):
                        cue_end = max(cue_end, min(cue_start + MIN_DURATION_SEC, end_t))
                    _emit_block(buf_words, cue_start, cue_end)
                    cue_start = None
                    cue_end = None
                    buf_words = []
                    continue

                buf_words.append(txt)
                cue_end = we
                i += 1

                # Natural break at sentence punctuation when duration is sufficient.
                if re.search(r"[.!?…。！？]$", txt) and (cue_end - cue_start) >= MIN_DURATION_SEC:
                    _emit_block(buf_words, cue_start, cue_end)
                    cue_start = None
                    cue_end = None
                    buf_words = []

            if buf_words and cue_start is not None:
                final_end = cue_end if cue_end is not None else end_t
                final_end = min(end_t, max(final_end, cue_start + 0.05))
                _emit_block(buf_words, cue_start, final_end)
            continue

        # Fallback path: no word timestamps -> split by semantic lines and distribute by chars.
        all_lines = smart_split_text(raw_text)
        groups = []
        for i in range(0, max(1, len(all_lines)), MAX_LINES):
            groups.append(all_lines[i:i + MAX_LINES])
        if not groups:
            continue
        total_chars = sum(len(" ".join(g)) for g in groups) or 1
        cur_time = start_t
        for g in groups:
            block_text = "\n".join(g)
            block_chars = len(block_text) or 1
            block_dur = duration * (block_chars / total_chars)
            block_dur = max(MIN_DURATION_SEC, min(MAX_DURATION_SEC, block_dur))
            block_end = min(cur_time + block_dur, end_t)
            srt_blocks.append({"start": cur_time, "end": block_end, "text": block_text})
            cur_time = block_end

    if not srt_blocks:
        return ""

    merged = []
    for b in srt_blocks:
        if merged:
            prev = merged[-1]
            gap = b["start"] - prev["end"]
            dur_b = b["end"] - b["start"]
            words_b = len(b["text"].replace("\n", " ").split())
            is_tiny_tail = dur_b < 0.9 and words_b <= 3
            if is_tiny_tail and gap <= 0.18 and (b["end"] - prev["start"]) <= (MAX_DURATION_SEC + 0.6):
                joined = (prev["text"].replace("\n", " ") + " " + b["text"].replace("\n", " ")).strip()
                joined = re.sub(r"\s+([.,!?;:])", r"\1", joined)
                lines = smart_split_text(joined)
                prev["text"] = "\n".join(lines[:MAX_LINES])
                prev["end"] = max(prev["end"], b["end"])
                continue
        merged.append(dict(b))

    HOLD_SEC = 0.10
    MIN_GAP = 0.03
    for i in range(len(merged) - 1):
        cur = merged[i]
        nxt = merged[i + 1]
        desired_end = min(cur["end"] + HOLD_SEC, nxt["start"] - MIN_GAP)
        cur["end"] = max(cur["start"] + MIN_DURATION_SEC, desired_end)
        min_next_start = cur["end"] + MIN_GAP
        if nxt["start"] < min_next_start:
            shift = min_next_start - nxt["start"]
            nxt["start"] += shift
            nxt["end"] = max(nxt["end"] + shift, nxt["start"] + MIN_DURATION_SEC)

    for b in merged:
        b["end"] = max(b["end"], b["start"] + MIN_DURATION_SEC)

    out_blocks = []
    for idx, b in enumerate(merged, start=1):
        out_blocks.append(
            f"{idx}\n{fmt_time(b['start'])} --> {fmt_time(b['end'])}\n{b['text']}\n"
        )
    return "\n".join(out_blocks)


# ─── Giao diện ───────────────────────────────────────────────────────────────
class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("MP3 → SRT  |  Whisper AI")
        self.configure(bg=BG)
        self.resizable(True, True)
        self.minsize(620, 680)

        self.file_path   = tk.StringVar()
        self.out_path    = tk.StringVar()
        self.model_var   = tk.StringVar(value="base.en")
        self.lang_var    = tk.StringVar(value="Tiếng Anh")
        self.status_var  = tk.StringVar(value="Ready")
        self.progress    = tk.DoubleVar(value=0)
        self._running    = False

        self._build_ui()
        self.update_idletasks()
        w, h = 700, 740
        x = (self.winfo_screenwidth()  - w) // 2
        y = (self.winfo_screenheight() - h) // 2
        self.geometry(f"{w}x{h}+{x}+{y}")

    # ── Build ─────────────────────────────────────────────────────────────────
    def _build_ui(self):
        PX = 28

        # Header
        hdr = tk.Frame(self, bg=BG)
        hdr.pack(fill="x", padx=PX, pady=(28, 4))
        tk.Label(hdr, text="MP3 → SRT", font=FONT_TITLE,
                 fg=ACCENT2, bg=BG).pack(side="left")
        tk.Label(hdr, text="  phụ đề thông minh", font=("Segoe UI", 13),
                 fg=TEXT_MID, bg=BG).pack(side="left", pady=(6, 0))

        self._sep()

        # File input
        self._section("📂  File âm thanh")
        row = tk.Frame(self, bg=BG)
        row.pack(fill="x", padx=PX, pady=(4, 0))
        entry = tk.Entry(row, textvariable=self.file_path,
                         bg=SURFACE, fg=TEXT, insertbackground=TEXT,
                         relief="flat", font=FONT_MAIN,
                         highlightthickness=1, highlightbackground=TEXT_DIM,
                         highlightcolor=ACCENT)
        entry.pack(side="left", fill="x", expand=True, ipady=6, padx=(0, 8))
        self._btn(row, "Chọn file", self._pick_file, side="left")

        tk.Label(self, text="Hỗ trợ: MP3, WAV, M4A, FLAC, OGG, MP4, MKV, …",
                 font=("Segoe UI", 8), fg=TEXT_DIM, bg=BG).pack(anchor="w", padx=28, pady=(3, 0))

        self._sep()

        # Settings row
        self._section("⚙️  Cài đặt")
        cfg = tk.Frame(self, bg=BG)
        cfg.pack(fill="x", padx=PX, pady=(4, 0))

        # Model
        ml = tk.Frame(cfg, bg=BG)
        ml.pack(side="left", padx=(0, 24))
        tk.Label(ml, text="Mô hình Whisper", font=("Segoe UI", 9),
                 fg=TEXT_MID, bg=BG).pack(anchor="w")
        model_menu = tk.OptionMenu(ml, self.model_var, *WHISPER_MODELS)
        self._style_menu(model_menu)
        model_menu.pack(anchor="w", pady=(2, 0))

        # Language
        ll = tk.Frame(cfg, bg=BG)
        ll.pack(side="left")
        tk.Label(ll, text="Ngôn ngữ", font=("Segoe UI", 9),
                 fg=TEXT_MID, bg=BG).pack(anchor="w")
        lang_names = [l[0] for l in LANGUAGES]
        lang_menu  = tk.OptionMenu(ll, self.lang_var, *lang_names)
        self._style_menu(lang_menu)
        lang_menu.pack(anchor="w", pady=(2, 0))

        # Sub quality hint
        hints = tk.Frame(self, bg=SURFACE2, bd=0)
        hints.pack(fill="x", padx=28, pady=(14, 0))
        tk.Label(hints,
                 text="  💡  tiny/base = nhanh   •   small = cân bằng   •   medium/large = chính xác nhất",
                 font=("Segoe UI", 8), fg=TEXT_MID, bg=SURFACE2).pack(anchor="w", pady=6)

        self._sep()

        # Output
        self._section("💾  Lưu file SRT")
        orow = tk.Frame(self, bg=BG)
        orow.pack(fill="x", padx=PX, pady=(4, 0))
        oentry = tk.Entry(orow, textvariable=self.out_path,
                          bg=SURFACE, fg=TEXT, insertbackground=TEXT,
                          relief="flat", font=FONT_MAIN,
                          highlightthickness=1, highlightbackground=TEXT_DIM,
                          highlightcolor=ACCENT)
        oentry.pack(side="left", fill="x", expand=True, ipady=6, padx=(0, 8))
        self._btn(orow, "Chọn nơi lưu", self._pick_out, side="left")

        self._sep()

        # Convert button BEFORE log so it is always visible
        bot = tk.Frame(self, bg=BG)
        bot.pack(fill="x", padx=28, pady=(0, 8))
        tk.Label(bot, textvariable=self.status_var,
                 font=("Segoe UI", 9), fg=TEXT_MID, bg=BG).pack(side="left")
        self.convert_btn = tk.Button(
            bot, text="▶  Convert to SRT",
            font=("Segoe UI", 12, "bold"),
            bg=ACCENT, fg="white", activebackground=ACCENT2,
            activeforeground="white", relief="flat", cursor="hand2",
            padx=24, pady=9, command=self._start
        )
        self.convert_btn.pack(side="right")

        # Progress bar
        self.pbar_canvas = tk.Canvas(self, height=5, bg=SURFACE2,
                                     highlightthickness=0)
        self.pbar_canvas.pack(fill="x", padx=28, pady=(0, 10))
        self._pbar_fill = self.pbar_canvas.create_rectangle(
            0, 0, 0, 5, fill=ACCENT, outline=""
        )

        # Log expands to fill remaining space
        self._section("📋  Log")
        log_frame = tk.Frame(self, bg=SURFACE, highlightthickness=1,
                             highlightbackground=TEXT_DIM)
        log_frame.pack(fill="both", expand=True, padx=28, pady=(4, 14))
        self.log = tk.Text(log_frame, bg=SURFACE, fg=TEXT_MID,
                           font=FONT_MONO, relief="flat", wrap="word",
                           state="disabled", height=7)
        sb = tk.Scrollbar(log_frame, command=self.log.yview, bg=SURFACE,
                          troughcolor=SURFACE, width=10, relief="flat")
        self.log.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        self.log.pack(fill="both", expand=True, padx=6, pady=6)

    # ── Helpers ───────────────────────────────────────────────────────────────
    def _sep(self):
        tk.Frame(self, bg=TEXT_DIM, height=1).pack(fill="x", padx=28, pady=12)

    def _section(self, label):
        tk.Label(self, text=label, font=FONT_BOLD, fg=TEXT, bg=BG).pack(
            anchor="w", padx=28, pady=(0, 2))

    def _btn(self, parent, text, cmd, side="left"):
        b = tk.Button(parent, text=text, command=cmd,
                      bg=SURFACE2, fg=TEXT, activebackground=SURFACE,
                      activeforeground=ACCENT2, relief="flat", cursor="hand2",
                      font=("Segoe UI", 9), padx=12, pady=6)
        b.pack(side=side)
        return b

    def _style_menu(self, menu):
        menu.configure(bg=SURFACE2, fg=TEXT, activebackground=ACCENT,
                       activeforeground="white", relief="flat",
                       highlightthickness=0, font=FONT_MAIN, width=18,
                       indicatoron=True)
        menu["menu"].configure(bg=SURFACE2, fg=TEXT, activebackground=ACCENT,
                               activeforeground="white", font=FONT_MAIN)

    def _log(self, msg, color=None):
        self.log.configure(state="normal")
        tag = f"col_{len(self.log.tag_names())}"
        self.log.tag_config(tag, foreground=color or TEXT_MID)
        self.log.insert("end", msg + "\n", tag)
        self.log.see("end")
        self.log.configure(state="disabled")

    def _set_progress(self, pct):
        self.pbar_canvas.update_idletasks()
        w = self.pbar_canvas.winfo_width()
        x1 = int(w * pct / 100)
        self.pbar_canvas.coords(self._pbar_fill, 0, 0, x1, 4)

    # ── Dialogs ───────────────────────────────────────────────────────────────
    def _pick_file(self):
        path = filedialog.askopenfilename(
            title="Chọn file âm thanh / video",
            filetypes=[
                ("Âm thanh & Video",
                 "*.mp3 *.wav *.m4a *.flac *.ogg *.aac *.wma *.mp4 *.mkv *.avi *.mov"),
                ("Tất cả", "*.*")
            ]
        )
        if path:
            self.file_path.set(path)
            default_out = str(Path(path).with_suffix(".srt"))
            self.out_path.set(default_out)
            self._log(f"Selected: {Path(path).name}", TEXT)

    def _pick_out(self):
        suggested = self.out_path.get() or "output.srt"
        path = filedialog.asksaveasfilename(
            title="Lưu file SRT",
            defaultextension=".srt",
            initialfile=Path(suggested).name,
            filetypes=[("SubRip", "*.srt"), ("Tất cả", "*.*")]
        )
        if path:
            self.out_path.set(path)

    # ── Conversion ────────────────────────────────────────────────────────────
    def _start(self):
        if self._running:
            return

        src = self.file_path.get().strip()
        dst = self.out_path.get().strip()

        if not src:
            messagebox.showwarning("Thiếu file", "Please select an audio file.")
            return
        if not os.path.isfile(src):
            messagebox.showerror("File not found", f"File does not exist:\n{src}")
            return
        if not dst:
            messagebox.showwarning("Missing output", "Please choose an output path.")
            return

        self._running = True
        self.convert_btn.configure(state="disabled", text="⏳  Processing…")
        self.status_var.set("Preparing…")
        self._set_progress(0)

        lang_name = self.lang_var.get()
        lang_code = next((l[1] for l in LANGUAGES if l[0] == lang_name), None)

        t = threading.Thread(
            target=self._run,
            args=(src, dst, self.model_var.get(), lang_code),
            daemon=True
        )
        t.start()

    def _run(self, src, dst, model_name, lang):
        def log(msg, c=None):
            self.after(0, lambda: self._log(msg, c))

        def progress(pct, status):
            self.after(0, lambda: self._set_progress(pct))
            self.after(0, lambda: self.status_var.set(status))

        try:
            log("─" * 42, TEXT_DIM)
            log(f"File   : {Path(src).name}", TEXT)
            log(f"Model  : {model_name}   Language : {lang or 'auto'}", TEXT)

            # 1. Cài thư viện
            log("📦  Checking libraries…", WARNING)
            progress(5, "Installing libs…")
            install_if_missing("openai-whisper", "whisper")
            install_if_missing("torch")

            import whisper

            # 2. Tải mô hình
            log(f"⬇️   Tải mô hình '{model_name}'…", WARNING)
            progress(15, f"Loading model {model_name}…")
            model = whisper.load_model(model_name)
            log("✅  Model ready.", SUCCESS)

            # 3. Nhận dạng
            log("🎙️   Transcribing audio…", WARNING)
            progress(30, "Transcribing audio…")

            opts = dict(
                task="transcribe",
                word_timestamps=True,
                verbose=False,
            )
            if lang:
                opts["language"] = lang

            result = model.transcribe(src, **opts)
            segments = result.get("segments", [])
            detected = result.get("language", "?")
            log(f"🌐  Detected language: {detected}", TEXT)
            log(f"📝  Segments: {len(segments)}", TEXT)
            progress(75, "Processing subtitles…")

            # 4. Tạo SRT
            log("✂️   Smart sentence splitting…", WARNING)
            srt_content = segments_to_srt(segments)
            cue_count   = srt_content.count("\n\n") + 1

            # 5. Lưu
            Path(dst).parent.mkdir(parents=True, exist_ok=True)
            with open(dst, "w", encoding="utf-8") as f:
                f.write(srt_content)

            progress(100, "Done!")
            log(f"💾  Đã lưu: {dst}", SUCCESS)
            log(f"🎉  {cue_count} subtitle lines — finished!", SUCCESS)
            log("─" * 42, TEXT_DIM)
            self.after(0, lambda: messagebox.showinfo(
                "Success",
                f"Created {cue_count} subtitle lines!\n\n📄 {dst}"
            ))

        except Exception as e:
            log(f"❌  Lỗi: {e}", DANGER)
            progress(0, "Error – see log")
            self.after(0, lambda: messagebox.showerror("Error", str(e)))

        finally:
            self._running = False
            self.after(0, lambda: self.convert_btn.configure(
                state="normal", text="▶  Chuyển đổi"))


# ─── Entry point ──────────────────────────────────────────────────────────────
if __name__ == "__main__":
    app = App()
    app.mainloop()
