#!/usr/bin/env python3
"""VE3 Studio  compact, professional GUI."""

import sys, os, shutil, threading, time as _time, json, subprocess, re, unicodedata
from collections import deque
import tkinter as tk
from tkinter import filedialog, messagebox
from pathlib import Path
from datetime import datetime
from typing import Dict

VE3_DIR = Path(__file__).parent
SUITE_ROOT = VE3_DIR.parents[1] if VE3_DIR.parent.name.lower() == "tools" else VE3_DIR
PROJECTS_DIR = SUITE_ROOT / "PROJECTS"
ARCHIVE_DIR = Path(r"D:\VE3_SUITE\old")
EDIT_VISUAL_DIR = Path(r"D:\AUTO\visual")
HEADLESS_RUNNER = SUITE_ROOT / "run_project_headless.py"
sys.path.insert(0, str(VE3_DIR))
SUNO_DIR = SUITE_ROOT / "tools" / "suno"
SUNO_CHROME = SUNO_DIR / "GoogleChromePortable" / "GoogleChromePortable.exe"
SUNO_WINDOW_SIZE = "1600,1200"
SUNO_WINDOW_POSITION_OFFSCREEN = "3200,40"
SUNO_WINDOW_POSITION_VISIBLE = "120,40"
if SUNO_DIR.exists():
    sys.path.insert(0, str(SUNO_DIR))

try:
    import customtkinter as ctk
except ImportError:
    os.system(f'"{sys.executable}" -m pip install customtkinter')
    import customtkinter as ctk
from PIL import Image

ctk.set_appearance_mode("light")

#  palette 
AC = "#C00"           # accent red
AC2 = "#A00"
SB = "#1E1E1E"        # sidebar
SB2 = "#2D2D2D"
SB3 = "#3A3A3A"
BG = "#FAFAFA"
CD = "#FFF"           # card
BD = "#DDD"           # border
BD2 = "#EEE"
EN = "#F5F5F5"        # entry bg
T1 = "#111"           # text primary
T2 = "#555"
T3 = "#999"
OK = "#1B8"           # green
OK2 = "#169"
ER = "#D22"
RN = "#17C"           # running blue
TW, TH = 110, 74     # thumb
SW = 175              # sidebar width

BADGES = {
    "pending": (T3, "#F0F0F0",  "i"),
    "running": ("#0D6EFD", "#E7F1FF", "ang to"),
    "done":    ("#198754", "#D1E7DD", "Xong"),
    "error":   ("#DC3545", "#F8D7DA", "Li"),
    "skip":    (T3, "#F0F0F0",  "B qua"),
}

def _thumb(p, w=TW, h=TH):
    try:
        if p and Path(p).exists():
            i = Image.open(str(p))
            return ctk.CTkImage(light_image=i, dark_image=i, size=(w, h))
    except Exception: pass
    return None

def _ph(w=TW, h=TH):
    i = Image.new("RGB", (w*2, h*2), "#E8E8E8")
    return ctk.CTkImage(light_image=i, dark_image=i, size=(w, h))

def _ts(s):
    if s is None: return ""
    s = int(s)
    return f"{s//60}:{s%60:02d}" if s >= 60 else f"{s}s"

def _media_age(ts):
    if not ts:
        return "-"
    delta = max(0, int(_time.time() - float(ts)))
    if delta < 60:
        return "<1m"
    minutes = delta // 60
    if minutes < 60:
        return f"{minutes}m"
    hours = minutes // 60
    mins = minutes % 60
    if hours < 24:
        return f"{hours}h{mins}m" if mins else f"{hours}h"
    days = hours // 24
    return f"{days}d"

#  badge 
class Badge(ctk.CTkLabel):
    def __init__(self, master, st="pending", **k):
        fg, bg, tx = BADGES.get(st, BADGES["pending"])
        super().__init__(master, text=tx, text_color=fg, fg_color=bg,
                         corner_radius=8, font=("", 10, "bold"), padx=7, pady=1, **k)
    def set(self, st):
        fg, bg, tx = BADGES.get(st, BADGES["pending"])
        self.configure(text=tx, text_color=fg, fg_color=bg)

#  character card 
class CharCard(ctk.CTkFrame):
    def __init__(self, master, d, nv, on_regen=None, on_view=None, **k):
        super().__init__(master, fg_color=CD, corner_radius=8,
                         border_width=1, border_color=BD2, height=90, **k)
        self.cid = d["id"]; self.nv = nv
        self.on_regen = on_regen; self.on_view = on_view
        self.grid_columnconfigure(1, weight=1)
        self.grid_propagate(False)

        # img
        self.img = ctk.CTkLabel(self, text="", width=TW, height=TH,
                                 fg_color="#ECECEC", corner_radius=4, cursor="hand2")
        self.img.grid(row=0, column=0, rowspan=2, padx=(6,4), pady=6)
        self.img.bind("<Button-1>", lambda e: self._view())
        self._load_img()

        # row 0: idnamerole  badge  time  server  regen
        r0 = ctk.CTkFrame(self, fg_color="transparent")
        r0.grid(row=0, column=1, sticky="ew", padx=(0,6), pady=(6,0))
        r0.grid_columnconfigure(0, weight=1)

        role = d.get("role",""); name = d.get("name","")
        t = self.cid
        if name: t += f"  {name}"
        if role: t += f"  {role}"
        ctk.CTkLabel(r0, text=t, font=("",12,"bold"), text_color=T1,
                     anchor="w").grid(row=0, column=0, sticky="w")

        st = (d.get("status") or "pending").lower()
        self.badge = Badge(r0, st if st in BADGES else "pending")
        self.badge.grid(row=0, column=1, padx=3)

        self.lbl_t = ctk.CTkLabel(r0, text="", font=("",9), text_color=T3)
        self.lbl_t.grid(row=0, column=2, padx=2)
        self.lbl_s = ctk.CTkLabel(r0, text="", font=("",9), text_color=T3)
        self.lbl_s.grid(row=0, column=3, padx=2)

        ctk.CTkButton(r0, text="To li", width=54, height=22, corner_radius=4,
                      fg_color="#EBEBEB", hover_color="#DDD", text_color=T2,
                      font=("",10), command=self._regen).grid(row=0, column=4, padx=(3,0))

        # row 1: prompt
        self.pb = ctk.CTkTextbox(self, height=36, font=("",11), fg_color=EN,
                                  border_color=BD2, border_width=1, corner_radius=4, wrap="word")
        self.pb.grid(row=1, column=1, sticky="ew", padx=(0,6), pady=(2,6))
        p = d.get("english_prompt") or d.get("vietnamese_prompt") or ""
        if p: self.pb.insert("1.0", p)

    def _regen(self):
        if self.on_regen: self.on_regen(self.cid, self.get_prompt())
    def _view(self):
        p = self.nv / f"{self.cid}.png"
        if p.exists() and self.on_view: self.on_view(p, self.cid)
    def _load_img(self):
        p = self.nv / f"{self.cid}.png"
        t = _thumb(p)
        if t: self.img.configure(image=t, text="", fg_color="transparent"); self.img._r = t
        else:
            ph = _ph(); self.img.configure(image=ph, text="", fg_color="#ECECEC"); self.img._r = ph
    def set_status(self, st, ex=None):
        self.badge.set(st)
        c = {"running": RN, "done": OK, "error": ER}.get(st)
        self.configure(border_color=c or BD2, border_width=2 if c else 1)
        if st == "done": self._load_img()
        ex = ex or {}
        if "elapsed" in ex: self.lbl_t.configure(text=_ts(ex["elapsed"]))
        if "server" in ex: self.lbl_s.configure(text=f'{ex["server"]}(q={ex.get("queue","?")})')
        if "queue_pos" in ex and ex["queue_pos"] is not None:
            self.lbl_s.configure(text=f'pos={ex["queue_pos"]}')
        if st == "running" and "elapsed" not in ex and "queue_pos" not in ex:
            self.lbl_t.configure(text="...")
    def get_prompt(self):
        return self.pb.get("1.0", "end-1c").strip()

#  scene card 
class SceneCard(ctk.CTkFrame):
    def __init__(self, master, d, idir, on_regen=None, on_regen_vid=None, on_view=None, **k):
        super().__init__(master, fg_color=CD, corner_radius=8,
                         border_width=1, border_color=BD2, height=110, **k)
        self.sid = d["scene_id"]; self.idir = idir
        self.on_regen = on_regen; self.on_regen_vid = on_regen_vid; self.on_view = on_view
        self.grid_columnconfigure(1, weight=1)
        self.grid_propagate(False)

        # nh preview
        self.img = ctk.CTkLabel(self, text="", width=TW, height=TH,
                                 fg_color="#ECECEC", corner_radius=4, cursor="hand2")
        self.img.grid(row=0, column=0, rowspan=3, padx=(6,4), pady=6)
        self.img.bind("<Button-1>", lambda e: self._view())
        self._load_img()

        # Row 0: Scene ID + SRT + badge nh + thi gian + server + nt to li nh
        r0 = ctk.CTkFrame(self, fg_color="transparent")
        r0.grid(row=0, column=1, sticky="ew", padx=(0,6), pady=(6,0))
        r0.grid_columnconfigure(1, weight=1)

        ctk.CTkLabel(r0, text=f"S{self.sid:03d}", font=("",12,"bold"),
                     text_color=T1).grid(row=0, column=0, sticky="w")
        srt = d.get("srt_text","")
        if srt:
            ctk.CTkLabel(r0, text=srt[:45]+("" if len(srt)>45 else ""),
                         font=("",10), text_color=T3, anchor="w"
                         ).grid(row=0, column=1, sticky="w", padx=4)

        st = (d.get("status_img") or "pending").lower()
        self.badge = Badge(r0, st if st in BADGES else "pending")
        self.badge.grid(row=0, column=2, padx=2)

        self.lbl_t = ctk.CTkLabel(r0, text="", font=("",9), text_color=T3)
        self.lbl_t.grid(row=0, column=3, padx=2)
        self.lbl_s = ctk.CTkLabel(r0, text="", font=("",9), text_color=T3)
        self.lbl_s.grid(row=0, column=4, padx=2)

        ctk.CTkButton(r0, text="Regen", width=54, height=22, corner_radius=4,
                      fg_color="#EBEBEB", hover_color="#DDD", text_color=T2,
                      font=("",10), command=self._regen).grid(row=0, column=5, padx=(2,0))

        # Row 0b: Video badge + nt to video
        stv = (d.get("status_vid") or "pending").lower()
        self.badge_vid = Badge(r0, stv if stv in BADGES else "pending")
        self.badge_vid.grid(row=0, column=6, padx=2)

        ctk.CTkLabel(r0, text="vid", font=("",8), text_color=T3).grid(row=0, column=7)

        self.lbl_tv = ctk.CTkLabel(r0, text="", font=("",9), text_color=T3)
        self.lbl_tv.grid(row=0, column=8, padx=1)

        ctk.CTkButton(r0, text="To video", width=62, height=22, corner_radius=4,
                      fg_color="#E0E7FF", hover_color="#C7D2FE", text_color="#3730A3",
                      font=("",10), command=self._regen_vid).grid(row=0, column=9, padx=(2,0))

        # Row 1: Prompt nh
        r1 = ctk.CTkFrame(self, fg_color="transparent")
        r1.grid(row=1, column=1, sticky="ew", padx=(0,6), pady=(2,1))
        r1.grid_columnconfigure(0, weight=1)

        self.pb = ctk.CTkTextbox(r1, height=30, font=("",10), fg_color=EN,
                                  border_color=BD2, border_width=1, corner_radius=4, wrap="word")
        self.pb.grid(row=0, column=0, sticky="ew")
        p = d.get("img_prompt","")
        if p: self.pb.insert("1.0", p)

        # Row 2: Video prompt (editable) + refs
        r2 = ctk.CTkFrame(self, fg_color="transparent")
        r2.grid(row=2, column=1, sticky="ew", padx=(0,6), pady=(1,6))
        r2.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(r2, text="Video:", font=("",9), text_color=T3).grid(row=0, column=0, sticky="w")
        self.vp = ctk.CTkTextbox(r2, height=22, font=("",10), fg_color="#F0F0FF",
                                  border_color="#D0D0E8", border_width=1, corner_radius=4, wrap="word")
        self.vp.grid(row=1, column=0, sticky="ew")
        vp = d.get("video_prompt","") or ""
        if vp: self.vp.insert("1.0", vp)

    def _regen(self):
        if self.on_regen: self.on_regen(self.sid, self.get_prompt())
    def _regen_vid(self):
        if self.on_regen_vid: self.on_regen_vid(self.sid, self.get_video_prompt())
    def _view(self):
        p = self.idir / f"scene_{self.sid:03d}.png"
        if p.exists() and self.on_view: self.on_view(p, f"Scene {self.sid:03d}")
    def _load_img(self):
        p = self.idir / f"scene_{self.sid:03d}.png"
        t = _thumb(p)
        if t: self.img.configure(image=t, text="", fg_color="transparent"); self.img._r = t
        else:
            ph = _ph(); self.img.configure(image=ph, text="", fg_color="#ECECEC"); self.img._r = ph
    def set_status(self, st, ex=None):
        ex = ex or {}
        is_vid = ex.get("phase") == "video"
        if is_vid:
            self.badge_vid.set(st)
            if "elapsed" in ex: self.lbl_tv.configure(text=_ts(ex["elapsed"]))
            if st == "running" and "elapsed" not in ex: self.lbl_tv.configure(text="...")
        else:
            self.badge.set(st)
            c = {"running": RN, "done": OK, "error": ER}.get(st)
            self.configure(border_color=c or BD2, border_width=2 if c else 1)
            if st == "done": self._load_img()
            if "elapsed" in ex: self.lbl_t.configure(text=_ts(ex["elapsed"]))
            if "server" in ex: self.lbl_s.configure(text=f'{ex["server"]}(q={ex.get("queue","?")})')
            if "queue_pos" in ex and ex["queue_pos"] is not None:
                self.lbl_s.configure(text=f'pos={ex["queue_pos"]}')
            if st == "running" and "elapsed" not in ex and "queue_pos" not in ex:
                self.lbl_t.configure(text="...")
    def get_prompt(self):
        return self.pb.get("1.0", "end-1c").strip()
    def get_video_prompt(self):
        return self.vp.get("1.0", "end-1c").strip()

#  image viewer 
class ImageViewer(ctk.CTkToplevel):
    def __init__(self, master, path, title=""):
        super().__init__(master)
        self.title(title or Path(path).name)
        self.geometry("820x620"); self.configure(fg_color="#111")
        self.transient(master); self.grab_set()
        try:
            i = Image.open(str(path))
            r = min(800/i.width, 600/i.height)
            ci = ctk.CTkImage(light_image=i, dark_image=i, size=(int(i.width*r), int(i.height*r)))
            l = ctk.CTkLabel(self, image=ci, text=""); l.pack(expand=True); l._r = ci
        except Exception as e:
            ctk.CTkLabel(self, text=str(e), text_color="#FFF").pack(expand=True)

#  HOME PAGE 
class HomePage(ctk.CTkScrollableFrame):
    def __init__(self, master, app, **k):
        super().__init__(master, fg_color=BG, **k)
        self.app = app
        # Stable UI state: keep project-card slot order and last good progress values.
        self._progress_slot_codes = []
        self._ui_progress_cache = {}
        self.grid_columnconfigure(0, weight=1)
        self._mk_projects()      # 1. Danh sch m
        self._mk_queue_state()   # 2. Tin
        self._mk_log()           # 3. Nht k
        self._mk_process_monitor()
        self._mk_server()
        self._mk_progress()

    def _card(self, row, title):
        c = ctk.CTkFrame(self, fg_color=CD, corner_radius=8, border_width=1, border_color=BD)
        c.grid(row=row, column=0, sticky="ew", padx=10, pady=1)
        c.grid_columnconfigure(0, weight=1)
        if title:
            ctk.CTkLabel(c, text=title, font=("",11,"bold"), text_color=T1, anchor="w").grid(row=0, column=0, padx=10, pady=(4,2), sticky="w", columnspan=4)
        return c

    def _mk_queue_state(self):
        # Queue Status card removed - not needed anymore
        # Create dummy objects for legacy code compatibility
        class DummyWidget:
            def configure(self, **kwargs): pass
            def set(self, value): pass
            def grid(self, **kwargs): pass
            def grid_remove(self): pass

        dummy = DummyWidget()
        self.progress_wrap = ctk.CTkFrame(self, fg_color="transparent")
        self.progress_cards = []

        self.lbl_active_project_left = dummy
        self.pb_refs_left = dummy
        self.lbl_refs_left = dummy
        self.pb_scenes_left = dummy
        self.lbl_scenes_left = dummy
        self.pb_vids_left = dummy
        self.lbl_vids_left = dummy
        self.pb_music_left = dummy
        self.lbl_music_left = dummy

        self.lbl_active_project_right = dummy
        self.pb_refs_right = dummy
        self.lbl_refs_right = dummy
        self.pb_scenes_right = dummy
        self.lbl_scenes_right = dummy
        self.pb_vids_right = dummy
        self.lbl_vids_right = dummy
        self.pb_music_right = dummy
        self.lbl_music_right = dummy

        self.pb_refs = dummy
        self.lbl_refs = dummy
        self.pb_scenes = dummy
        self.lbl_scenes = dummy
        self.pb_vids = dummy
        self.lbl_vids = dummy
        self.pb_music = dummy
        self.lbl_music = dummy
        self.lbl_active_project = dummy

        self.lbl_cur = ctk.CTkLabel(self, text="", font=("",8), text_color=T3)

        # Keep compatibility labels (hidden)
        self.lbl_total_projects_metric = ctk.CTkLabel(self, text="0")
        self.lbl_running_metric = ctk.CTkLabel(self, text="0")
        self.lbl_waiting_metric = ctk.CTkLabel(self, text="0")
        self.lbl_done_metric = ctk.CTkLabel(self, text="0")
        self.lbl_queue_mode = ctk.CTkLabel(self, text="", font=("",11, "bold"), text_color=T1)
        self.lbl_queue_focus = ctk.CTkLabel(self, text="", font=("",11), text_color=RN)
        self.lbl_queue_summary = ctk.CTkLabel(self, text="", text_color=T2, font=("",10))
        self.lbl_queue_projects = self.lbl_queue_summary
        self.lbl_queue_pairs = self.lbl_queue_summary
        self.lbl_next_excel = self.lbl_queue_summary
        self.lbl_next_ve3 = self.lbl_queue_summary
        self.lbl_need_fix = self.lbl_queue_summary

        self.btn_run_center = ctk.CTkButton(
            self,
            text="RUN",
            height=42,
            fg_color="#2E7D32",
            hover_color="#1B5E20",
            text_color="#FFFFFF",
            font=("",18,"bold"),
            corner_radius=10,
            command=self.app.toggle_queue_worker,
        )
        self.btn_run_center.grid_remove()

    def _set_active_project_label(self, code):
        # Progress cards removed - this method is now a no-op
        pass

    def _make_progress_card(self, idx):
        # Progress cards removed - return empty dict for compatibility
        return {}

    def _layout_progress_cards(self, visible_count):
        # Progress cards removed - this method is now a no-op
        pass

    def _ensure_progress_cards(self, count):
        # Progress cards removed - this method is now a no-op
        pass

    def _mk_projects(self):
        c = self._card(0, "Projects")
        c.grid_columnconfigure(0, weight=1)
        c.grid_rowconfigure(2, weight=1)  # Make projects_list expandable

        # Overview section - matching tool color scheme
        overview = ctk.CTkFrame(c, fg_color=CD, corner_radius=8, border_width=1, border_color=BD)
        overview.grid(row=1, column=0, padx=8, pady=(4,4), sticky="ew")
        overview.grid_columnconfigure((0,1,2,3,4,5), weight=1)

        # Total projects - Blue theme
        f1 = ctk.CTkFrame(overview, fg_color="#F5F5F5", corner_radius=6, border_width=1, border_color=BD)
        f1.grid(row=0, column=0, padx=4, pady=8, sticky="ew")
        ctk.CTkLabel(f1, text="TỔNG", font=("",9,"bold"), text_color=T2).pack(pady=(6,2))
        self.lbl_overview_total = ctk.CTkLabel(f1, text="0", font=("",20,"bold"), text_color=T1)
        self.lbl_overview_total.pack(pady=(0,6))

        # Completed today - Green theme
        f2 = ctk.CTkFrame(overview, fg_color="#F5F5F5", corner_radius=6, border_width=1, border_color=BD)
        f2.grid(row=0, column=1, padx=4, pady=8, sticky="ew")
        ctk.CTkLabel(f2, text="XONG HÔM NAY", font=("",9,"bold"), text_color=T2).pack(pady=(6,2))
        self.lbl_overview_done_today = ctk.CTkLabel(f2, text="0", font=("",20,"bold"), text_color=OK)
        self.lbl_overview_done_today.pack(pady=(0,6))

        # Running Excel - Orange theme
        f3 = ctk.CTkFrame(overview, fg_color="#F5F5F5", corner_radius=6, border_width=1, border_color=BD)
        f3.grid(row=0, column=2, padx=4, pady=8, sticky="ew")
        ctk.CTkLabel(f3, text="ĐANG EXCEL", font=("",9,"bold"), text_color=T2).pack(pady=(6,2))
        self.lbl_overview_excel_run = ctk.CTkLabel(f3, text="0", font=("",20,"bold"), text_color="#F90")
        self.lbl_overview_excel_run.pack(pady=(0,6))

        # Running VE3 - Running blue theme
        f4 = ctk.CTkFrame(overview, fg_color="#F5F5F5", corner_radius=6, border_width=1, border_color=BD)
        f4.grid(row=0, column=3, padx=4, pady=8, sticky="ew")
        ctk.CTkLabel(f4, text="ĐANG VE3", font=("",9,"bold"), text_color=T2).pack(pady=(6,2))
        self.lbl_overview_ve3_run = ctk.CTkLabel(f4, text="0", font=("",20,"bold"), text_color=RN)
        self.lbl_overview_ve3_run.pack(pady=(0,6))

        # Waiting Excel - Gray theme
        f5 = ctk.CTkFrame(overview, fg_color="#F5F5F5", corner_radius=6, border_width=1, border_color=BD)
        f5.grid(row=0, column=4, padx=4, pady=8, sticky="ew")
        ctk.CTkLabel(f5, text="CHỜ EXCEL", font=("",9,"bold"), text_color=T2).pack(pady=(6,2))
        self.lbl_overview_excel_wait = ctk.CTkLabel(f5, text="0", font=("",18,"bold"), text_color=T2)
        self.lbl_overview_excel_wait.pack(pady=(0,6))

        # Waiting VE3 - Gray theme
        f6 = ctk.CTkFrame(overview, fg_color="#F5F5F5", corner_radius=6, border_width=1, border_color=BD)
        f6.grid(row=0, column=5, padx=4, pady=8, sticky="ew")
        ctk.CTkLabel(f6, text="CHỜ VE3", font=("",9,"bold"), text_color=T2).pack(pady=(6,2))
        self.lbl_overview_ve3_wait = ctk.CTkLabel(f6, text="0", font=("",18,"bold"), text_color=T2)
        self.lbl_overview_ve3_wait.pack(pady=(0,6))

        self.projects_list = ctk.CTkScrollableFrame(c, height=600, fg_color="#F3F5F7", corner_radius=6, border_width=1, border_color=BD2)
        self.projects_list.grid(row=2, column=0, padx=8, pady=(0,8), sticky="nsew")
        self.projects_list.grid_columnconfigure(0, weight=1)
        self.projects_box = None

    def _mk_server(self):
        # Hidden compatibility labels used by queue code paths.
        self.lbl_running_pair = ctk.CTkLabel(self, text="-", font=("Consolas",11,"bold"), text_color=RN)
        self.lbl_sync = ctk.CTkLabel(self, text="", font=("",9), text_color=T3, justify="left")
        self.lbl_pair_ready = ctk.CTkLabel(self, text="", font=("Consolas",11,"bold"), text_color=OK)
        self.lbl_pair_bound = self.lbl_running_pair

    def load_server_config(self):
        pass

    def update_server_status(self, infos):
        pass

    def _mk_progress(self):
        # This method is now merged into _mk_queue_state
        pass

    def _mk_log(self):
        c = self._card(2, "Logs")
        # Increase height to allow 2 rows of tabs
        self.log_tabs = ctk.CTkTabview(c, fg_color="transparent", segmented_button_fg_color="#DDD", segmented_button_selected_color=RN, segmented_button_selected_hover_color="#1565C0", text_color=T1, height=240)
        self.log_tabs.grid(row=1, column=0, padx=10, pady=(0,3), sticky="nsew")

        # Tabs now use shortened names (e.g., "0116" instead of "TL1-0116")
        # Increase height to allow wrapping to 2 rows
        try:
            self.log_tabs._segmented_button.configure(font=("", 9), height=48)
        except Exception:
            pass

        self.logs_visible = False
        self.log_pending = {}
        self.log_max_pending_per_tab = 800
        self.log_max_lines_per_tab = 1200
        self._running_codes: set = set()
        self.btn_toggle_logs = ctk.CTkButton(
            c, text="Show Logs", width=90, height=22, corner_radius=4,
            fg_color="#EBEBEB", hover_color="#DDD", text_color=T2, font=("",10),
            command=self._toggle_logs_visibility
        )
        self.btn_toggle_logs.grid(row=0, column=0, padx=10, pady=(4,2), sticky="e")

        # Create VE3 tab
        tab_ve3 = self.log_tabs.add("VE3")
        tab_ve3.grid_columnconfigure(0, weight=1); tab_ve3.grid_rowconfigure(0, weight=1)
        self.log_ve3_box = ctk.CTkTextbox(tab_ve3, font=("Consolas",10), fg_color="#1A1A1A", text_color="#CCC", corner_radius=4, wrap="word")
        self.log_ve3_box.grid(row=0, column=0, sticky="nsew", padx=0, pady=0)
        self.log_ve3_box.configure(state="disabled")

        # Create Excel tab
        tab_excel = self.log_tabs.add("Excel")
        tab_excel.grid_columnconfigure(0, weight=1); tab_excel.grid_rowconfigure(0, weight=1)
        self.log_excel_box = ctk.CTkTextbox(tab_excel, font=("Consolas",10), fg_color="#1A1A1A", text_color="#CCC", corner_radius=4, wrap="word")
        self.log_excel_box.grid(row=0, column=0, sticky="nsew", padx=0, pady=0)
        self.log_excel_box.configure(state="disabled")

        # Dictionary to store log boxes for each project code
        self.log_project_boxes = {}

        # Default to VE3 tab at startup.
        try:
            self.log_tabs.set("VE3")
        except Exception:
            pass
        self.log_tabs.grid_remove()

    def _mk_process_monitor(self):
        c = self._card(3, "Process Monitor")
        c.grid_columnconfigure(0, weight=1)

        top = ctk.CTkFrame(c, fg_color="transparent")
        top.grid(row=0, column=0, padx=10, pady=(2,4), sticky="ew")
        top.grid_columnconfigure(0, weight=1)
        self.lbl_process_status = ctk.CTkLabel(top, text="Chua cap nhat", font=("",10), text_color=T3, anchor="w")
        self.lbl_process_status.grid(row=0, column=0, sticky="w")
        self.chk_process_auto = ctk.CTkCheckBox(
            top,
            text="Auto 60s",
            width=90,
            checkbox_width=16,
            checkbox_height=16,
            font=("",10),
            command=self.app.toggle_process_monitor_auto,
        )
        self.chk_process_auto.grid(row=0, column=1, padx=(6,4), sticky="e")
        self.chk_process_auto.select()
        self.btn_process_refresh = ctk.CTkButton(
            top,
            text="Cap nhat",
            width=72,
            height=24,
            fg_color="#EBEBEB",
            hover_color="#DDD",
            text_color=T2,
            font=("",10),
            command=self.app.refresh_process_monitor_now,
        )
        self.btn_process_refresh.grid(row=0, column=2, sticky="e")

        self.process_box = ctk.CTkTextbox(
            c,
            height=115,
            font=("Consolas",10),
            fg_color="#F8F9FA",
            text_color=T1,
            corner_radius=4,
            wrap="none",
        )
        self.process_box.grid(row=1, column=0, padx=10, pady=(0,8), sticky="ew")
        self.process_box.configure(state="disabled")

    def update_process_monitor(self, rows, ts=None, err=None):
        ts_text = datetime.fromtimestamp(ts or _time.time()).strftime("%H:%M:%S")
        if err:
            self.lbl_process_status.configure(text=f"Loi cap nhat {ts_text}: {err}", text_color=ER)
            return
        rows = rows or []
        self.lbl_process_status.configure(text=f"Cap nhat {ts_text} - {len(rows)} process", text_color=T3)
        lines = []
        lines.append(f"{'PROJECT':<10} {'TYPE':<11} {'PID':>6} {'PPID':>6} {'AGE':>8} CMD")
        lines.append("-" * 90)
        for row in rows:
            lines.append(
                f"{row.get('code','-'):<10} {row.get('kind','other'):<11} "
                f"{str(row.get('pid','')):>6} {str(row.get('ppid','')):>6} "
                f"{row.get('age','-'):>8} {row.get('cmd','')}"
            )
        if len(lines) == 2:
            lines.append("Khong thay process VE3/Suno dang chay.")
        self.process_box.configure(state="normal")
        self.process_box.delete("1.0", "end")
        self.process_box.insert("1.0", "\n".join(lines))
        self.process_box.configure(state="disabled")

    def _toggle_logs_visibility(self):
        self.logs_visible = not self.logs_visible
        if self.logs_visible:
            self.log_tabs.grid()
            self.btn_toggle_logs.configure(text="Hide Logs")
            self._flush_pending_logs()
        else:
            self.log_tabs.grid_remove()
            self.btn_toggle_logs.configure(text="Show Logs")

    def _append_log_line(self, box, line):
        self._append_log_text(box, line, line_count=line.count("\n") or 1)

    def _append_log_text(self, box, text, line_count=1):
        box.configure(state="normal")
        box.insert("end", text)
        current = int(getattr(box, "_ve3_log_line_count", 0) or 0) + int(line_count or 1)
        if current > self.log_max_lines_per_tab:
            remove = current - self.log_max_lines_per_tab
            try:
                box.delete("1.0", f"{remove + 1}.0")
                current = self.log_max_lines_per_tab
            except Exception:
                current = int(float(box.index("end-1c").split(".")[0]))
                if current > self.log_max_lines_per_tab:
                    try:
                        box.delete("1.0", f"{current - self.log_max_lines_per_tab + 1}.0")
                        current = self.log_max_lines_per_tab
                    except Exception:
                        pass
        box._ve3_log_line_count = current
        box.see("end")
        box.configure(state="disabled")

    def _flush_pending_logs(self):
        if not self.log_pending:
            return
        for key, lines in list(self.log_pending.items()):
            if not lines:
                continue
            if key == "Excel":
                box = self.log_excel_box
            elif key == "VE3":
                box = self.log_ve3_box
            elif key in self.log_project_boxes:
                box = self.log_project_boxes[key]
            else:
                # Project is no longer running — route buffered logs to VE3 tab
                box = self.log_ve3_box
            self._append_log_text(box, "".join(lines), line_count=len(lines))
        self.log_pending.clear()

    def get_or_create_project_log(self, code):
        """Get or create a log tab for a specific project code.
        Only creates a new tab if the project is currently in RUN state.
        Non-running projects fall back to the VE3 tab."""
        if code not in self.log_project_boxes:
            # Guard: only create a new tab for projects actively running
            running = getattr(self, "_running_codes", None)
            if running is not None and code not in running:
                return self.log_ve3_box
            tab_name = code.split('-')[-1] if '-' in code else code
            tab = self.log_tabs.add(tab_name)
            tab.grid_columnconfigure(0, weight=1)
            tab.grid_rowconfigure(0, weight=1)
            log_box = ctk.CTkTextbox(tab, font=("Consolas",10), fg_color="#1A1A1A", text_color="#CCC", corner_radius=4, wrap="word")
            log_box.grid(row=0, column=0, sticky="nsew", padx=0, pady=0)
            log_box.configure(state="disabled")
            self.log_project_boxes[code] = log_box
        return self.log_project_boxes[code]

    def remove_project_log(self, code):
        """Remove log tab for a project that finished"""
        if code in self.log_project_boxes:
            try:
                # Use shortened tab name to delete
                tab_name = code.split('-')[-1] if '-' in code else code
                self.log_tabs.delete(tab_name)
            except:
                pass
            del self.log_project_boxes[code]

    def set_config(self, cfg):
        pairs_all = self.app._get_server_pairs(only_available=False) if hasattr(self.app, "_get_server_pairs") else []
        pairs_ok = self.app._get_server_pairs(only_available=True) if hasattr(self.app, "_get_server_pairs") else []
        self.lbl_sync.configure(text=f"Pair san sang: {len(pairs_ok)}/{len(pairs_all)}", text_color=T3)

    def get_token(self):
        return str(self.app.config_data.get("flow_bearer_token", "") or "").strip()

    def get_project_id(self):
        return str(self.app.config_data.get("flow_project_id", "") or "").strip()

    def fill_from_excel(self, wb):
        bound_server = (wb.get_config_value("ve3_bound_server_name") or "").strip()
        bound_account = (wb.get_config_value("ve3_bound_account_name") or wb.get_config_value("flow_account_name") or "").strip()
        project_id = (wb.get_config_value("flow_project_id") or "").strip()
        self.lbl_pair_bound.configure(text=f"{bound_server or '-'} / {bound_account or '-'}")
        if project_id:
            self.lbl_sync.configure(text=f"Project hien tai: {project_id[:8]}... | Pair: {bound_server or '-'} / {bound_account or '-'}", text_color=OK)

    def sync_to_excel(self, wb):
        return

    def update_progress(self, phase, cur, tot):
        # Update left column (backward compatibility)
        if phase == "refs":
            self.pb_refs.set(cur/max(tot,1)); self.lbl_refs.configure(text=f"{cur}/{tot}")
            self.pb_refs_left.set(cur/max(tot,1)); self.lbl_refs_left.configure(text=f"{cur}/{tot}")
        elif phase == "scenes":
            self.pb_scenes.set(cur/max(tot,1)); self.lbl_scenes.configure(text=f"{cur}/{tot}")
            self.pb_scenes_left.set(cur/max(tot,1)); self.lbl_scenes_left.configure(text=f"{cur}/{tot}")
        elif phase == "videos":
            self.pb_vids.set(cur/max(tot,1)); self.lbl_vids.configure(text=f"{cur}/{tot}")
            self.pb_vids_left.set(cur/max(tot,1)); self.lbl_vids_left.configure(text=f"{cur}/{tot}")
        elif phase == "music":
            self.pb_music.set(cur/max(tot,1)); self.lbl_music.configure(text=f"{cur}/{tot}")
            self.pb_music_left.set(cur/max(tot,1)); self.lbl_music_left.configure(text=f"{cur}/{tot}")

    def refresh_projects_overview(self, rows):
        def normalize_code(value):
            return str(value or "").strip().upper()

        # Defensive dedupe by project code (avoid duplicate cards/progress for same code).
        dedup_rows = {}
        for r in rows:
            code = normalize_code(r.get("code", ""))
            if not code:
                continue
            if r.get("code") != code:
                r = dict(r)
                r["code"] = code
            prev = dedup_rows.get(code)
            if prev is None:
                dedup_rows[code] = r
                continue
            prev_state = str(prev.get("state", "") or "").upper()
            cur_state = str(r.get("state", "") or "").upper()
            # Prefer RUN row over others; otherwise keep first stable entry.
            if prev_state != "RUN" and cur_state == "RUN":
                dedup_rows[code] = r
        rows = list(dedup_rows.values())

        try:
            running = sum(1 for r in rows if r["state"] == "RUN")
            waiting = sum(1 for r in rows if r["state"] == "WAIT")
            done = sum(1 for r in rows if r["state"] == "DONE")
        except Exception as e:
            return

        try:
            queue_mode = "running" if getattr(self.app, "queue_running", False) else "idle"
            self.lbl_total_projects_metric.configure(text=str(len(rows)))
            self.lbl_running_metric.configure(text=str(running))
            self.lbl_waiting_metric.configure(text=str(waiting))
            self.lbl_done_metric.configure(text=str(done))
            self.lbl_queue_mode.configure(text=f"Status: {queue_mode}", text_color=OK if queue_mode == "running" else T1)
        except Exception as e:
            return

        # Update overview metrics
        try:
            excel_run = 0
            ve3_run = 0
            excel_wait = 0
            ve3_wait = 0
            done_today = 0

            today_start = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0).timestamp()

            def _as_project_path(row):
                raw = row.get("path") or row.get("dir")
                return Path(raw) if raw else None

            def _done_marker_today(row):
                project_dir = _as_project_path(row)
                if not project_dir:
                    return False
                for marker_name in (".endpoint_done.lock", ".manual_done.lock", ".done"):
                    marker = project_dir / marker_name
                    try:
                        if marker.exists() and marker.stat().st_mtime >= today_start:
                            return True
                    except Exception:
                        continue
                return False

            for r in rows:
                state = str(r.get("state", "") or "").upper()
                next_phase = str(r.get("next", "") or "")
                next_lower = next_phase.lower()
                excel_running = bool(r.get("excel_running"))
                ve3_running = bool(r.get("ve3_running"))
                excel_complete = bool(r.get("excel_complete"))
                needs_ve3 = bool(r.get("needs_ve3"))
                visuals_done = bool(r.get("visuals_done"))
                music_ready = bool(r.get("music_ready", True))
                has_excel = str(r.get("excel", "") or "") == "OK"
                has_source = str(r.get("source", "") or "") == "OK" or str(r.get("srt", "") or "") == "OK"

                if state == "RUN":
                    if excel_running or "excel" in next_lower:
                        excel_run += 1
                    else:
                        ve3_run += 1
                elif state == "WAIT":
                    if not has_excel or "build excel" in next_lower or (has_source and not excel_complete):
                        excel_wait += 1
                    elif needs_ve3 or not visuals_done or not music_ready or any(token in next_lower for token in ("ve3", "music", "pair", "fix")):
                        ve3_wait += 1
                elif state == "DONE" and _done_marker_today(r):
                    done_today += 1

            self.lbl_overview_total.configure(text=str(len(rows)))
            self.lbl_overview_done_today.configure(text=str(done_today))
            self.lbl_overview_excel_run.configure(text=str(excel_run))
            self.lbl_overview_ve3_run.configure(text=str(ve3_run))
            self.lbl_overview_excel_wait.configure(text=str(excel_wait))
            self.lbl_overview_ve3_wait.configure(text=str(ve3_wait))
        except Exception as e:
            pass

        # Update progress bars from active projects (RUN or WAIT), with stable slot order.
        try:
            active_rows = [r for r in rows if r["state"] in ("RUN", "WAIT")]
            active_by_code = {}
            for r in active_rows:
                code = normalize_code(r.get("code", ""))
                if not code:
                    continue
                if r.get("code") != code:
                    r = dict(r)
                    r["code"] = code
                active_by_code[code] = r
            active_codes = set(active_by_code.keys())
        except Exception as e:
            return

        if not self._progress_slot_codes:
            seen_codes = set()
            self._progress_slot_codes = []
            for r in active_rows:
                c = normalize_code(r.get("code", ""))
                if not c:
                    continue
                if c in seen_codes:
                    continue
                seen_codes.add(c)
                self._progress_slot_codes.append(c)
        else:
            seen_codes = set()
            filtered_codes = []
            for c in self._progress_slot_codes:
                if c in active_codes and c not in seen_codes:
                    filtered_codes.append(c)
                    seen_codes.add(c)
            self._progress_slot_codes = filtered_codes
            for r in active_rows:
                c = normalize_code(r.get("code", ""))
                if not c:
                    continue
                if c not in self._progress_slot_codes:
                    self._progress_slot_codes.append(c)
        display_rows = [active_by_code[c] for c in self._progress_slot_codes if c in active_by_code]

        self._ensure_progress_cards(len(display_rows))

        # Only create log tabs for projects that are currently running (RUN state)
        # Don't show tabs for WAIT projects even if they have Excel/VE3 next
        running_codes = {
            r['code'] for r in display_rows
            if r.get("state") == "RUN"
        }

        if not hasattr(self, '_last_active_codes'):
            self._last_active_codes = set()

        self._running_codes = running_codes

        if running_codes != self._last_active_codes:
            # Create log tabs for projects that started running
            for code in running_codes - self._last_active_codes:
                self.get_or_create_project_log(code)

            # Remove tabs for projects that finished or stopped running
            for code in self._last_active_codes - running_codes:
                self.remove_project_log(code)

            self._last_active_codes = running_codes

        def parse_progress(s):
            if isinstance(s, str) and '/' in s:
                try:
                    parts = s.split('/')
                    cur = int(str(parts[0]).strip())
                    tot = int(str(parts[1]).strip())
                    return max(0, cur), max(0, tot)
                except Exception:
                    return 0, 0
            return 0, 0

        def stable_metric(code, key, raw):
            cur, tot = parse_progress(raw)
            cache = self._ui_progress_cache.setdefault(code, {})
            prev_cur, prev_tot = cache.get(key, (0, 0))

            # If current read is invalid/missing, keep last valid UI value.
            if tot <= 0 and prev_tot > 0:
                return prev_cur, prev_tot

            # When total stays same, keep non-decreasing progress to avoid flicker.
            if tot > 0 and prev_tot == tot:
                cur = max(cur, prev_cur)

            if tot > 0:
                cache[key] = (cur, tot)
            return cur, tot

        # Progress cards removed from UI, skip update

        running_rows = [r for r in rows if r["state"] == "RUN"]
        current_row = running_rows[0] if running_rows else None
        current_text = f"{current_row['code']} ({current_row['next']})" if current_row else "-"
        self.lbl_queue_focus.configure(text=f"Running: {current_text}")

        next_excel_rows = [r for r in rows if r["state"] == "WAIT" and "Excel" in str(r.get("next", ""))]
        next_ve3_rows = [r for r in rows if r["state"] == "WAIT" and "VE3" in str(r.get("next", ""))]
        fix_rows = [
            r for r in rows
            if r["state"] == "BLOCK"
            or "Fix" in str(r.get("next", ""))
            or "no scenes" in str(r.get("next", "")).lower()
            or r.get("pair_state") in ("MISS", "UNBOUND")
        ]
        summary_parts = []
        if next_excel_rows:
            summary_parts.append(f"Excel: {next_excel_rows[0]['code']}")
        if next_ve3_rows:
            summary_parts.append(f"VE3: {next_ve3_rows[0]['code']}")
        if fix_rows:
            summary_parts.append(f"Need Fix: {fix_rows[0]['code']}")
        self.lbl_queue_summary.configure(text=" | ".join(summary_parts))

        def step_label(r):
            nxt = str(r.get("next", "") or "")
            if "Excel" in nxt:
                return "Build Excel"
            if "VE3" in nxt:
                return "Generate image/video"
            if r["state"] == "DONE":
                return "Completed"
            if r["state"] == "RUN":
                return "Running"
            return nxt or "-"

        def progress_label(r):
            if r["scenes"] <= 0:
                return "SCN 0"
            return f"IMG {r['img_progress']} | VID {r['vid_progress']} | MUS {r['music_progress']}"

        def pair_label(r):
            server = str(r.get("server_name", "") or "-")
            account = str(r.get("account_name", "") or "-")
            return f"{server} / {account}"

        def progress_short(r):
            if r["scenes"] <= 0:
                return "0 scene"
            return f"I {r['img_progress']}  V {r['vid_progress']}  M {r['music_progress']}"

        def media_label(r):
            kind = str(r.get("latest_media_kind", "") or "")
            age = str(r.get("latest_media_age", "") or "-")
            if not kind or age == "-":
                return "-"
            return f"{kind} {age}"

        def state_label(r):
            if r["state"] == "RUN":
                return "RUN"
            if r["state"] == "DONE":
                return "DONE"
            if r["state"] == "BLOCK":
                return "BLOCK"
            return "WAIT"

        try:
            # Sort by priority: projects closest to completion first
            # Priority order:
            # 0 = RUN (currently running)
            # 1 = WAIT + VE3 (ready to run VE3, Excel already done)
            # 2 = WAIT + Excel (ready to run Excel)
            # 3 = WAIT + has server/account (paired, has work done)
            # 4 = BLOCK (blocked)
            # 5 = DONE (completed)
            # 6 = WAIT + no pair (not started yet)
            def sort_priority(r):
                state = r["state"]
                next_step = str(r.get("next", ""))
                has_server = bool(r.get("server_name") and r.get("server_name") != "-")
                has_account = bool(r.get("account_name") and r.get("account_name") != "-")
                has_pair = has_server and has_account

                if state == "RUN":
                    return 0  # Running - highest priority
                elif state == "WAIT" and "VE3" in next_step:
                    return 1  # Ready for VE3 - Excel done
                elif state == "WAIT" and "Excel" in next_step:
                    return 2  # Ready for Excel
                elif state == "WAIT" and has_pair:
                    return 3  # Has server/account pair - work in progress
                elif state == "BLOCK":
                    return 4  # Blocked
                elif state == "DONE":
                    return 5  # Completed
                else:
                    return 6  # Not started yet

            ordered = sorted(rows, key=lambda r: (sort_priority(r), r["code"]))
        except Exception as e:
            ordered = rows

        # Render projects list
        for w in self.projects_list.winfo_children():
            w.destroy()
        if not ordered:
            ctk.CTkLabel(self.projects_list, text="No project in PROJECTS.", font=("",10), text_color=T3).grid(row=0, column=0, padx=8, pady=8, sticky="w")
            return

        for i, r in enumerate(ordered):
            bg = "#FFFFFF" if i % 2 == 0 else "#FBFCFD"
            border = "#D9E7FF" if r["state"] == "RUN" else "#E6EAEE"
            row = ctk.CTkFrame(
                self.projects_list,
                fg_color=bg,
                corner_radius=6,
                border_width=1,
                border_color=border,
            )
            row.grid(row=i, column=0, padx=6, pady=(0,4), sticky="ew")
            for col, weight in enumerate((0, 2, 2, 2, 1, 0, 0, 0)):
                row.grid_columnconfigure(col, weight=weight)
            row.grid_propagate(False)
            row.configure(height=40)

            state = state_label(r)
            state_colors = {
                "RUN": ("#E8F2FF", RN),
                "WAIT": ("#FFF4DD", "#C47F00"),
                "DONE": ("#E8F7ED", OK),
                "BLOCK": ("#FDECEC", ER),
            }
            badge_bg, badge_fg = state_colors.get(state, ("#F2F2F2", T2))

            ctk.CTkLabel(
                row,
                text=r["code"],
                font=("Consolas", 11, "bold"),
                text_color=T1,
            ).grid(row=0, column=0, padx=(10,8), pady=6, sticky="w")
            ctk.CTkLabel(
                row,
                text=step_label(r),
                font=("", 10, "bold"),
                text_color=T1,
                anchor="w",
            ).grid(row=0, column=1, padx=(0,10), pady=6, sticky="ew")
            ctk.CTkLabel(
                row,
                text=pair_label(r),
                font=("", 10),
                text_color=T3,
                anchor="w",
            ).grid(row=0, column=2, padx=(0,10), pady=6, sticky="ew")
            ctk.CTkLabel(
                row,
                text=progress_short(r),
                font=("Consolas", 10),
                text_color=T2,
                anchor="w",
            ).grid(row=0, column=3, padx=(0,10), pady=6, sticky="ew")
            ctk.CTkLabel(
                row,
                text=media_label(r),
                font=("Consolas", 10, "bold"),
                text_color=RN if str(r.get("latest_media_kind", "")) == "VID" else T2,
                anchor="w",
            ).grid(row=0, column=4, padx=(0,10), pady=6, sticky="ew")
            ctk.CTkLabel(
                row,
                text=state,
                fg_color=badge_bg,
                text_color=badge_fg,
                corner_radius=9,
                font=("Consolas", 10, "bold"),
                width=64,
                height=22,
            ).grid(row=0, column=5, padx=(0,8), pady=6, sticky="w")
            manual_done = bool(r.get("manual_done"))
            if manual_done:
                btn_text = "Da nhan"
                btn_fg = "#1F8E4D"
                btn_hover = "#1F8E4D"
                btn_text_color = "#FFFFFF"
                btn_state = "disabled"
                btn_command = None
            else:
                # Chua bam: vang de nhin ro canh bao lenh thu cong.
                btn_text = "Xong"
                btn_fg = "#F4C542"
                btn_hover = "#E5B52F"
                btn_text_color = "#1F1F1F"
                btn_state = "normal"
                btn_command = lambda p=r["path"]: self.app.toggle_project_manual_done(Path(p), mark_done=True)
            ctk.CTkButton(
                row,
                text=btn_text,
                width=64,
                height=22,
                corner_radius=4,
                fg_color=btn_fg,
                hover_color=btn_hover,
                text_color=btn_text_color,
                font=("",10),
                state=btn_state,
                command=btn_command,
            ).grid(row=0, column=6, padx=(0,4), pady=6, sticky="e")
            is_running = bool(r.get("excel_running") or r.get("ve3_running"))
            reset_state = "disabled" if is_running else "normal"
            ctk.CTkButton(
                row,
                text="Reset",
                width=50,
                height=22,
                corner_radius=4,
                fg_color="#EF5350" if not is_running else "#BDBDBD",
                hover_color="#D32F2F",
                text_color="#FFFFFF",
                font=("",10),
                state=reset_state,
                command=lambda p=r["path"], c=r["code"]: self.app.clean_project_excel(Path(p), c),
            ).grid(row=0, column=7, padx=(0,10), pady=6, sticky="e")

    def _sanitize_log_text(self, msg):
        """Normalize logs to plain ASCII to avoid font/encoding glitches."""
        text = str(msg).replace("\r\n", "\n").replace("\r", "\n")
        # Remove Vietnamese diacritics and other combining marks.
        text = unicodedata.normalize("NFKD", text)
        text = "".join(ch for ch in text if not unicodedata.combining(ch))
        # Keep UI-safe ASCII only.
        text = text.encode("ascii", "ignore").decode("ascii")
        # Remove control chars except newline/tab.
        text = "".join(ch for ch in text if ch == "\n" or ch == "\t" or ord(ch) >= 32)
        # Compact whitespace per line.
        text = "\n".join(" ".join(line.split()) for line in text.split("\n"))
        return text.strip()

    def log(self, msg, level="INFO", channel="ve3"):
        self.log_many([(msg, level, channel)])

    def log_many(self, records):
        buckets = {}
        for msg, level, channel in records:
            target_keys, line = self._format_log_targets(msg, level, channel)
            for target_key in target_keys:
                buckets.setdefault(target_key, []).append(line)

        for target_key, lines in buckets.items():
            if not getattr(self, "logs_visible", True):
                arr = self.log_pending.setdefault(target_key, [])
                arr.extend(lines)
                if len(arr) > self.log_max_pending_per_tab:
                    del arr[:len(arr) - self.log_max_pending_per_tab]
                continue

            if target_key == "Excel":
                box = self.log_excel_box
            elif target_key == "VE3":
                box = self.log_ve3_box
            else:
                box = self.get_or_create_project_log(target_key)
            self._append_log_text(box, "".join(lines), line_count=len(lines))

    def _active_project_log_channel(self, code):
        app = getattr(self, "app", None)
        if app is None:
            return None
        try:
            with app.queue_lock:
                if code in getattr(app, "queue_active_ve3", set()):
                    return "ve3"
                if code in getattr(app, "queue_active_excel", set()):
                    return "excel"
        except Exception:
            pass
        try:
            projects_dir = getattr(app, "projects_dir", PROJECTS_DIR)
            project_dir = projects_dir / code
            if app._queue_marker(project_dir, "ve3").exists():
                return "ve3"
            if app._queue_marker(project_dir, "excel").exists():
                return "excel"
        except Exception:
            pass
        return None

    def _format_log_targets(self, msg, level="INFO", channel="ve3"):
        ts = datetime.now().strftime("%H:%M:%S")
        ic = {"SUCCESS":"[OK]", "ERROR":"[X]", "WARN":"[!]"}.get(level, "[ ]")
        raw_msg = str(msg)
        safe_msg = self._sanitize_log_text(raw_msg)
        line = f"[{ts}] {ic} {safe_msg}\n"

        # Extract project code from message like "[KA5-0080] ..." or "[QUEUE/VE3] KA5-0080: ..."
        code_match = re.search(r'\[([A-Z0-9]+-\d+)\]|\b([A-Z0-9]+-\d+):', raw_msg)
        code_key = (code_match.group(1) or code_match.group(2)) if code_match else None

        # Determine primary channel tab.
        ch = str(channel or "").strip().lower()
        if ch == "excel":
            primary_key = "Excel"
        elif ch == "ve3":
            primary_key = "VE3"
        else:
            text = raw_msg
            if "[QUEUE/EXCEL]" in text or "MP3/SRT -> Excel" in text or "SRT -> Excel" in text:
                ch = "excel"
                primary_key = "Excel"
            else:
                ch = "ve3"
                primary_key = "VE3"

        # Project tabs show only the active phase for that project.
        # Queue scanner DEBUG lines are global scheduler chatter, not project task logs.
        if code_key:
            is_queue_debug = bool(re.search(r'^\[DEBUG\]\s+[A-Z0-9]+-\d+:', raw_msg))
            active_channel = self._active_project_log_channel(code_key)
            target_keys = [code_key] if active_channel and ch == active_channel and not is_queue_debug else [primary_key]
        else:
            target_keys = [primary_key]

        return target_keys, line


class GeneratePage(ctk.CTkFrame):
    PAGE_SIZE = 20

    def __init__(self, master, app, **k):
        super().__init__(master, fg_color=BG, **k)
        self.app = app
        self.cc: Dict[str, CharCard] = {}
        self.sc: Dict[int, SceneCard] = {}
        self._all_scenes = []
        self._idir = None
        self._page = 0
        self.project_paths = {}

        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(6, weight=1)

        top = ctk.CTkFrame(self, fg_color="transparent")
        top.grid(row=0, column=0, sticky="ew", padx=10, pady=(10, 4))
        top.grid_columnconfigure(1, weight=1)
        ctk.CTkLabel(top, text="Project", font=("", 12, "bold"), text_color=T1).grid(row=0, column=0, sticky="w")
        self.project_menu = ctk.CTkOptionMenu(top, values=["No project"], width=260, command=lambda _v: self._load_selected_project())
        self.project_menu.grid(row=0, column=1, sticky="w", padx=(8, 8))
        ctk.CTkButton(top, text="Load", width=60, height=28, fg_color=RN, hover_color="#1565C0", text_color="#FFF",
                      font=("",10), command=self._load_selected_project).grid(row=0, column=2, padx=(0, 6))
        self.project_hint = ctk.CTkLabel(top, text="", font=("", 10), text_color=T3, anchor="w")
        self.project_hint.grid(row=1, column=0, columnspan=3, sticky="ew", pady=(4, 0))

        ch = ctk.CTkFrame(self, fg_color="transparent")
        ch.grid(row=1, column=0, sticky="ew", padx=10, pady=(2, 0))
        ch.grid_columnconfigure(0, weight=1)
        self.lbl_c = ctk.CTkLabel(ch, text="Characters (0)", font=("",13,"bold"), text_color=T1)
        self.lbl_c.grid(row=0, column=0, sticky="w")
        ctk.CTkButton(ch, text="Luu", width=50, height=24, fg_color=AC, hover_color=AC2,
                      text_color="#FFF", corner_radius=4, font=("",10),
                      command=app.save_characters).grid(row=0, column=1)

        self.cs = ctk.CTkScrollableFrame(self, fg_color=BG)
        self.cs.grid(row=2, column=0, sticky="nsew", padx=6, pady=(2, 6))
        self.cs.grid_columnconfigure(0, weight=1)

        sh = ctk.CTkFrame(self, fg_color="transparent")
        sh.grid(row=4, column=0, sticky="ew", padx=10, pady=(2,0))
        sh.grid_columnconfigure(0, weight=1)
        self.lbl_s = ctk.CTkLabel(sh, text="Canh (0)", font=("",13,"bold"), text_color=T1)
        self.lbl_s.grid(row=0, column=0, sticky="w")
        ctk.CTkButton(sh, text="Luu", width=50, height=24, fg_color=AC, hover_color=AC2,
                      text_color="#FFF", corner_radius=4, font=("",10),
                      command=app.save_scenes).grid(row=0, column=1)

        pg = ctk.CTkFrame(self, fg_color="transparent")
        pg.grid(row=5, column=0, sticky="ew", padx=10, pady=(2,0))
        self.btn_prev = ctk.CTkButton(pg, text="< Truoc", width=72, height=24,
                                       fg_color=SB2, hover_color=SB3, text_color="#AAA",
                                       corner_radius=4, font=("",10), command=self._prev_page)
        self.btn_prev.pack(side="left", padx=(0,4))
        self.lbl_page = ctk.CTkLabel(pg, text="Trang 1/1  (0 canh)", font=("",10), text_color=T2)
        self.lbl_page.pack(side="left", padx=4)
        self.btn_next = ctk.CTkButton(pg, text="Tiep >", width=72, height=24,
                                       fg_color=SB2, hover_color=SB3, text_color="#AAA",
                                       corner_radius=4, font=("",10), command=self._next_page)
        self.btn_next.pack(side="left", padx=4)
        ctk.CTkLabel(pg, text="  Den trang:", font=("",10), text_color=T3).pack(side="left")
        self.ent_jump = ctk.CTkEntry(pg, width=40, height=24, font=("",10),
                                      fg_color=EN, border_color=BD, corner_radius=4)
        self.ent_jump.pack(side="left", padx=(2,2))
        ctk.CTkButton(pg, text="->", width=28, height=24, fg_color=SB2, hover_color=SB3,
                      text_color="#AAA", corner_radius=4, font=("",10),
                      command=self._jump_page).pack(side="left")

        self.ss = ctk.CTkScrollableFrame(self, fg_color=BG)
        self.ss.grid(row=6, column=0, sticky="nsew", padx=6, pady=(2,6))
        self.ss.grid_columnconfigure(0, weight=1)

    def update_project_list(self, rows):
        ready = [r for r in rows if r.get("excel") == "OK"]
        values = [r["code"] for r in ready] or ["No project"]
        new_paths = {r["code"]: r["path"] for r in ready}
        values_key = tuple(values)
        hint_text = f"{len(ready)} ma co Excel. AUTO QUEUE se tu chay; Load chi de xem/sua prompt."

        if getattr(self, "_last_project_menu_values", None) != values_key:
            self.project_menu.configure(values=values)
            self._last_project_menu_values = values_key
        self.project_paths = new_paths
        current = self.project_menu.get()
        if current not in values:
            self.project_menu.set(values[0])
        if getattr(self, "_last_project_hint_text", None) != hint_text:
            self.project_hint.configure(text=hint_text)
            self._last_project_hint_text = hint_text

    def _load_selected_project(self):
        code = self.project_menu.get()
        path = self.project_paths.get(code)
        if not path:
            return
        pd = Path(path)
        ep = pd / f"{pd.name}_prompts.xlsx"
        if not ep.exists():
            excels = [p for p in pd.glob("*_prompts.xlsx") if not p.name.startswith("~")]
            ep = excels[0] if excels else ep
        if ep.exists():
            self.app._load_excel(ep)

    def _total_pages(self):
        return max(1, (len(self._all_scenes) + self.PAGE_SIZE - 1) // self.PAGE_SIZE)

    def _prev_page(self):
        if self._page > 0:
            self._page -= 1
            self._render_page()

    def _next_page(self):
        if self._page < self._total_pages() - 1:
            self._page += 1
            self._render_page()

    def _jump_page(self):
        try:
            p = int(self.ent_jump.get().strip()) - 1
            p = max(0, min(p, self._total_pages() - 1))
            self._page = p
            self._render_page()
        except Exception:
            pass

    def _render_page(self):
        for w in self.ss.winfo_children():
            w.destroy()
        self.sc.clear()

        start = self._page * self.PAGE_SIZE
        page_data = self._all_scenes[start:start + self.PAGE_SIZE]
        for i, d in enumerate(page_data):
            c = SceneCard(self.ss, d, self._idir,
                          on_regen=self.app.regen_scene,
                          on_regen_vid=self.app.regen_video,
                          on_view=self.app.view_image)
            c.grid(row=i, column=0, sticky="ew", pady=2, padx=2)
            self.sc[d["scene_id"]] = c

        tp = self._total_pages()
        total = len(self._all_scenes)
        left = start + 1 if total else 0
        right = min(start + self.PAGE_SIZE, total) if total else 0
        self.lbl_page.configure(text=f"Trang {self._page+1}/{tp}  ({left}-{right}/{total} canh)")
        self.btn_prev.configure(state="normal" if self._page > 0 else "disabled")
        self.btn_next.configure(state="normal" if self._page < tp - 1 else "disabled")

    def load_chars(self, data, nv):
        for w in self.cs.winfo_children():
            w.destroy()
        self.cc.clear()
        for i, d in enumerate(data):
            c = CharCard(self.cs, d, nv, on_regen=self.app.regen_character, on_view=self.app.view_image)
            c.grid(row=i, column=0, sticky="ew", pady=2, padx=2)
            self.cc[d["id"]] = c
        self.lbl_c.configure(text=f"Characters ({len(data)})")

    def load_scenes(self, data, idir):
        self._all_scenes = data
        self._idir = idir
        self._page = 0
        n = len([s for s in data if s.get("img_prompt")])
        self.lbl_s.configure(text=f"Canh ({n}/{len(data)})")
        self._render_page()

    def update_char(self, cid, st, ex=None):
        if cid in self.cc:
            self.cc[cid].set_status(st, ex)

    def update_scene(self, sid, st, ex=None):
        sid = int(sid) if isinstance(sid, str) else sid
        for d in self._all_scenes:
            if d["scene_id"] == sid:
                if st == "done":
                    d["status_img"] = "done"
                elif st == "error":
                    d["status_img"] = "error"
                break
        if sid in self.sc:
            self.sc[sid].set_status(st, ex)


class SettingsPage(ctk.CTkScrollableFrame):
    def __init__(self, master, app, **k):
        super().__init__(master, fg_color=BG, **k)
        self.app = app
        self.excel_ai_provider_options = {
            "DeepSeek": "deepseek",
            "DeepSeek + VOV": "deepseek_vov",
            "VOV Direct + GPT Fallback": "vov_direct",
            "Pool Creative Fallback": "claude_pool",
        }
        self.excel_ai_provider_labels = {v: k for k, v in self.excel_ai_provider_options.items()}
        self.generation_backend_options = {"Server": "server", "NanoPic": "nanopic", "FlowKit": "flowkit", "Combined": "combined"}
        self.generation_backend_labels = {v: k for k, v in self.generation_backend_options.items()}
        self.grid_columnconfigure(0, weight=1)

        # Server pairs
        sc = ctk.CTkFrame(self, fg_color=CD, corner_radius=8, border_width=1, border_color=BD)
        sc.grid(row=0, column=0, sticky="ew", padx=10, pady=(10,4))
        sc.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(sc, text="Server + Gmail + Chrome", font=("",13,"bold"), text_color=T1).grid(row=0, column=0, padx=10, pady=(8,4), sticky="w", columnspan=6)
        top = ctk.CTkFrame(sc, fg_color="transparent")
        top.grid(row=1, column=0, padx=10, pady=(0,4), sticky="ew", columnspan=6)
        top.grid_columnconfigure(1, weight=1)
        ctk.CTkLabel(top, text="Moi dong la 1 cap dung truc tiep: server + gmail|password|totp + chrome. Project da bind cap nao thi giu nguyen cap do.", font=("",10), text_color=T3).grid(row=0, column=0, sticky="w", columnspan=2)
        self.sw_flow_auto = ctk.CTkSwitch(top, text="Auto auth", progress_color=OK, button_color="#FFF", button_hover_color="#EEE")
        self.sw_flow_auto.grid(row=0, column=2, padx=(4,0))
        ar = ctk.CTkFrame(sc, fg_color="transparent")
        ar.grid(row=2, column=0, padx=10, pady=(0,4), sticky="ew", columnspan=6)
        ar.grid_columnconfigure(0, weight=1)
        ar.grid_columnconfigure(1, weight=1)
        ar.grid_columnconfigure(2, weight=1)
        ar.grid_columnconfigure(3, weight=1)
        self.ent_nm = ctk.CTkEntry(ar, placeholder_text="Pair name", width=100, height=28, corner_radius=4, font=("",10), fg_color=EN, border_color=BD)
        self.ent_nm.grid(row=0, column=0, padx=(0,3))
        self.ent_url = ctk.CTkEntry(ar, placeholder_text="http://192.168.x.x:5000", height=28, corner_radius=4, font=("Consolas",10), fg_color=EN, border_color=BD)
        self.ent_url.grid(row=0, column=1, sticky="ew", padx=(0,3))
        self.ent_bundle = ctk.CTkEntry(ar, placeholder_text="email@gmail.com|password|totp_secret", height=28, corner_radius=4, font=("Consolas",10), fg_color=EN, border_color=BD)
        self.ent_bundle.grid(row=0, column=2, sticky="ew", padx=(0,3))
        self.ent_chrome = ctk.CTkEntry(ar, placeholder_text=str(SUITE_ROOT / "GoogleChromePortable" / "GoogleChromePortable.exe"), height=28, corner_radius=4, font=("Consolas",10), fg_color=EN, border_color=BD)
        self.ent_chrome.grid(row=0, column=3, sticky="ew", padx=(0,3))
        self.ent_topics = ctk.CTkEntry(ar, placeholder_text="story, psychology (trong=tat ca)", width=160, height=28, corner_radius=4, font=("",10), fg_color=EN, border_color=BD)
        self.ent_topics.grid(row=0, column=4, padx=(0,3))
        ctk.CTkButton(ar, text="+", width=28, height=28, corner_radius=4, fg_color=OK, hover_color=OK2, text_color="#FFF", font=("",13,"bold"), command=self._add).grid(row=0, column=5, padx=(0,3))
        ctk.CTkButton(ar, text="Test", width=60, height=28, corner_radius=4, fg_color=RN, hover_color="#1565C0", text_color="#FFF", font=("",10), command=app.test_all_servers).grid(row=0, column=6)
        self.sv_frame = ctk.CTkFrame(sc, fg_color="transparent")
        self.sv_frame.grid(row=3, column=0, padx=10, pady=(2,8), sticky="ew", columnspan=6)
        self.sv_frame.grid_columnconfigure(1, weight=1)
        self.sv_rows = []

        # Runtime
        gc = ctk.CTkFrame(self, fg_color=CD, corner_radius=8, border_width=1, border_color=BD)
        gc.grid(row=1, column=0, sticky="ew", padx=10, pady=4)
        gc.grid_columnconfigure(1, weight=1)
        ctk.CTkLabel(gc, text="Runtime", font=("",13,"bold"), text_color=T1).grid(row=0, column=0, padx=10, pady=(8,6), sticky="w", columnspan=3)
        ctk.CTkLabel(gc, text="Parallel jobs:", font=("",11), text_color=T2).grid(row=1, column=0, padx=(10,6), sticky="e")
        self.ent_conc = ctk.CTkEntry(gc, width=60, height=28, corner_radius=4, font=("",11), fg_color=EN, border_color=BD)
        self.ent_conc.grid(row=1, column=1, sticky="w")
        ctk.CTkLabel(gc, text="Retry:", font=("",11), text_color=T2).grid(row=2, column=0, padx=(10,6), sticky="e")
        self.ent_retry = ctk.CTkEntry(gc, width=60, height=28, corner_radius=4, font=("",11), fg_color=EN, border_color=BD)
        self.ent_retry.grid(row=2, column=1, sticky="w")
        ctk.CTkLabel(gc, text="Aspect ratio:", font=("",11), text_color=T2).grid(row=3, column=0, padx=(10,6), sticky="e")
        self.opt_ar = ctk.CTkOptionMenu(gc, values=["landscape","portrait","square"], width=120, height=28, corner_radius=4, fg_color=EN, button_color=BD, text_color=T1, font=("",11))
        self.opt_ar.grid(row=3, column=1, sticky="w", pady=(0,4))
        ctk.CTkLabel(gc, text="Generation:", font=("",11), text_color=T2).grid(row=4, column=0, padx=(10,6), sticky="e")
        self.opt_generation_backend = ctk.CTkOptionMenu(gc, values=list(self.generation_backend_options.keys()), width=120, height=28, corner_radius=4, fg_color=EN, button_color=BD, text_color=T1, font=("",11))
        self.opt_generation_backend.grid(row=4, column=1, sticky="w", pady=(0,4))
        self.sw_music_workspace = ctk.CTkSwitch(gc, text="Music Chrome mo lech man hinh", progress_color=OK, button_color="#FFF", button_hover_color="#EEE")
        self.sw_music_workspace.grid(row=5, column=0, columnspan=3, padx=10, pady=(0,8), sticky="w")
        ctk.CTkButton(gc, text="Save settings", width=120, height=30, fg_color=AC, hover_color=AC2, text_color="#FFF", font=("",11,"bold"), corner_radius=6, command=self._save).grid(row=6, column=0, columnspan=3, padx=10, pady=(4,10))
        self.lbl_saved = ctk.CTkLabel(gc, text="", font=("",9), text_color=OK)
        self.lbl_saved.grid(row=7, column=0, columnspan=3, padx=10, pady=(0,6))

        # Excel AI
        ai = ctk.CTkFrame(self, fg_color=CD, corner_radius=8, border_width=1, border_color=BD)
        ai.grid(row=2, column=0, sticky="ew", padx=10, pady=4)
        ai.grid_columnconfigure(1, weight=1)
        ctk.CTkLabel(ai, text="Excel AI", font=("",13,"bold"), text_color=T1).grid(row=0, column=0, padx=10, pady=(8,6), sticky="w", columnspan=3)

        ctk.CTkLabel(ai, text="Provider:", font=("",11), text_color=T2).grid(row=1, column=0, padx=(10,6), sticky="e")
        self.opt_excel_ai_provider = ctk.CTkOptionMenu(ai, values=list(self.excel_ai_provider_options.keys()), width=180, height=28, corner_radius=4, fg_color=EN, button_color=BD, text_color=T1, font=("",11), command=self._on_excel_ai_provider_change)
        self.opt_excel_ai_provider.grid(row=1, column=1, sticky="w", pady=(0,4))

        slots = ctk.CTkFrame(ai, fg_color="transparent")
        slots.grid(row=2, column=1, sticky="w", padx=(0,10), pady=2)
        ctk.CTkLabel(ai, text="Excel/API slots:", font=("",11), text_color=T2).grid(row=2, column=0, padx=(10,6), sticky="e")
        ctk.CTkLabel(slots, text="DeepSeek", font=("",10), text_color=T2).grid(row=0, column=0, padx=(0,4))
        self.ent_deepseek_slots = ctk.CTkEntry(slots, width=48, height=26, corner_radius=4, font=("",10), fg_color=EN, border_color=BD)
        self.ent_deepseek_slots.grid(row=0, column=1, padx=(0,10))
        ctk.CTkLabel(slots, text="VOV", font=("",10), text_color=T2).grid(row=0, column=2, padx=(0,4))
        self.ent_vov_slots = ctk.CTkEntry(slots, width=48, height=26, corner_radius=4, font=("",10), fg_color=EN, border_color=BD)
        self.ent_vov_slots.grid(row=0, column=3)

        ctk.CTkLabel(ai, text="DeepSeek key:", font=("",11), text_color=T2).grid(row=3, column=0, padx=(10,6), sticky="e")
        self.ent_deepseek_key = ctk.CTkEntry(ai, height=28, corner_radius=4, font=("Consolas",10), fg_color=EN, border_color=BD)
        self.ent_deepseek_key.grid(row=3, column=1, sticky="ew", padx=(0,10), pady=2)

        ctk.CTkLabel(ai, text="DeepSeek extra keys:", font=("",11), text_color=T2).grid(row=4, column=0, padx=(10,6), sticky="e")
        self.ent_deepseek_keys = ctk.CTkEntry(ai, height=28, corner_radius=4, font=("Consolas",10), fg_color=EN, border_color=BD, placeholder_text="comma,separated,keys")
        self.ent_deepseek_keys.grid(row=4, column=1, sticky="ew", padx=(0,10), pady=2)

        ctk.CTkLabel(ai, text="DeepSeek model:", font=("",11), text_color=T2).grid(row=5, column=0, padx=(10,6), sticky="e")
        self.ent_deepseek_model = ctk.CTkEntry(ai, height=28, corner_radius=4, font=("Consolas",10), fg_color=EN, border_color=BD)
        self.ent_deepseek_model.grid(row=5, column=1, sticky="ew", padx=(0,10), pady=2)

        ctk.CTkLabel(ai, text="Thinking type:", font=("",11), text_color=T2).grid(row=6, column=0, padx=(10,6), sticky="e")
        self.ent_deepseek_thinking = ctk.CTkEntry(ai, height=28, corner_radius=4, font=("Consolas",10), fg_color=EN, border_color=BD)
        self.ent_deepseek_thinking.grid(row=6, column=1, sticky="ew", padx=(0,10), pady=2)

        ctk.CTkLabel(ai, text="VOV URL:", font=("",11), text_color=T2).grid(row=7, column=0, padx=(10,6), sticky="e")
        self.ent_vov_direct_url = ctk.CTkEntry(ai, height=28, corner_radius=4, font=("Consolas",10), fg_color=EN, border_color=BD, placeholder_text="https://routerapi.vovantin.online/v1")
        self.ent_vov_direct_url.grid(row=7, column=1, sticky="ew", padx=(0,10), pady=2)

        ctk.CTkLabel(ai, text="VOV key:", font=("",11), text_color=T2).grid(row=8, column=0, padx=(10,6), sticky="e")
        self.ent_vov_direct_key = ctk.CTkEntry(ai, height=28, corner_radius=4, font=("Consolas",10), fg_color=EN, border_color=BD)
        self.ent_vov_direct_key.grid(row=8, column=1, sticky="ew", padx=(0,10), pady=2)

        ctk.CTkLabel(ai, text="VOV model:", font=("",11), text_color=T2).grid(row=9, column=0, padx=(10,6), sticky="e")
        self.ent_vov_direct_model = ctk.CTkEntry(ai, height=28, corner_radius=4, font=("Consolas",10), fg_color=EN, border_color=BD)
        self.ent_vov_direct_model.grid(row=9, column=1, sticky="ew", padx=(0,10), pady=2)

        ctk.CTkLabel(ai, text="VOV model chain:", font=("",11), text_color=T2).grid(row=10, column=0, padx=(10,6), sticky="e")
        self.ent_vov_direct_chain = ctk.CTkEntry(ai, height=28, corner_radius=4, font=("Consolas",10), fg_color=EN, border_color=BD, placeholder_text="claude-opus-4-6, claude-sonnet-4-6")
        self.ent_vov_direct_chain.grid(row=10, column=1, sticky="ew", padx=(0,10), pady=2)

        ctk.CTkLabel(ai, text="Claude Pool URL:", font=("",11), text_color=T2).grid(row=11, column=0, padx=(10,6), sticky="e")
        self.ent_claude_pool_url = ctk.CTkEntry(ai, height=28, corner_radius=4, font=("Consolas",10), fg_color=EN, border_color=BD, placeholder_text="http://127.0.0.1:8318")
        self.ent_claude_pool_url.grid(row=11, column=1, sticky="ew", padx=(0,10), pady=2)

        ctk.CTkLabel(ai, text="Claude Pool key:", font=("",11), text_color=T2).grid(row=12, column=0, padx=(10,6), sticky="e")
        self.ent_claude_pool_key = ctk.CTkEntry(ai, height=28, corner_radius=4, font=("Consolas",10), fg_color=EN, border_color=BD)
        self.ent_claude_pool_key.grid(row=12, column=1, sticky="ew", padx=(0,10), pady=2)

        ctk.CTkLabel(ai, text="Claude Pool model:", font=("",11), text_color=T2).grid(row=13, column=0, padx=(10,6), sticky="e")
        self.ent_claude_pool_model = ctk.CTkEntry(ai, height=28, corner_radius=4, font=("Consolas",10), fg_color=EN, border_color=BD)
        self.ent_claude_pool_model.grid(row=13, column=1, sticky="ew", padx=(0,10), pady=2)

        ctk.CTkLabel(ai, text="Pool model chain:", font=("",11), text_color=T2).grid(row=14, column=0, padx=(10,6), sticky="e")
        self.ent_claude_pool_chain = ctk.CTkEntry(ai, height=28, corner_radius=4, font=("Consolas",10), fg_color=EN, border_color=BD, placeholder_text="gpt-5.4, gpt-5.2, gpt-5.3-codex, gemini-3-flash-agent, gemini-3.1-pro-high")
        self.ent_claude_pool_chain.grid(row=14, column=1, sticky="ew", padx=(0,10), pady=(2,10))

        # NanoPic
        np = ctk.CTkFrame(self, fg_color=CD, corner_radius=8, border_width=1, border_color=BD)
        np.grid(row=3, column=0, sticky="ew", padx=10, pady=4)
        np.grid_columnconfigure(1, weight=1)
        ctk.CTkLabel(np, text="NanoPic", font=("",13,"bold"), text_color=T1).grid(row=0, column=0, padx=10, pady=(8,6), sticky="w", columnspan=3)
        ctk.CTkLabel(np, text="Base URL:", font=("",11), text_color=T2).grid(row=1, column=0, padx=(10,6), sticky="e")
        self.ent_nanopic_base_url = ctk.CTkEntry(np, height=28, corner_radius=4, font=("Consolas",10), fg_color=EN, border_color=BD)
        self.ent_nanopic_base_url.grid(row=1, column=1, sticky="ew", padx=(0,10), pady=2)
        ctk.CTkLabel(np, text="Nano token:", font=("",11), text_color=T2).grid(row=2, column=0, padx=(10,6), sticky="e")
        self.ent_nanopic_token = ctk.CTkEntry(np, height=28, corner_radius=4, font=("Consolas",10), fg_color=EN, border_color=BD, show="*")
        self.ent_nanopic_token.grid(row=2, column=1, sticky="ew", padx=(0,10), pady=2)
        ctk.CTkLabel(np, text="Access token:", font=("",11), text_color=T2).grid(row=3, column=0, padx=(10,6), sticky="e")
        self.ent_nanopic_access_token = ctk.CTkEntry(np, height=28, corner_radius=4, font=("Consolas",10), fg_color=EN, border_color=BD, show="*")
        self.ent_nanopic_access_token.grid(row=3, column=1, sticky="ew", padx=(0,10), pady=2)
        ctk.CTkLabel(np, text="Video cookie:", font=("",11), text_color=T2).grid(row=4, column=0, padx=(10,6), sticky="e")
        self.ent_nanopic_video_cookie = ctk.CTkEntry(np, height=28, corner_radius=4, font=("Consolas",10), fg_color=EN, border_color=BD, show="*")
        self.ent_nanopic_video_cookie.grid(row=4, column=1, sticky="ew", padx=(0,10), pady=2)
        ctk.CTkLabel(np, text="Image model:", font=("",11), text_color=T2).grid(row=5, column=0, padx=(10,6), sticky="e")
        self.ent_nanopic_image_model = ctk.CTkEntry(np, height=28, corner_radius=4, font=("Consolas",10), fg_color=EN, border_color=BD)
        self.ent_nanopic_image_model.grid(row=5, column=1, sticky="ew", padx=(0,10), pady=2)
        ctk.CTkLabel(np, text="Video model:", font=("",11), text_color=T2).grid(row=6, column=0, padx=(10,6), sticky="e")
        self.ent_nanopic_video_model = ctk.CTkEntry(np, height=28, corner_radius=4, font=("Consolas",10), fg_color=EN, border_color=BD)
        self.ent_nanopic_video_model.grid(row=6, column=1, sticky="ew", padx=(0,10), pady=2)
        ctk.CTkLabel(np, text="Video type:", font=("",11), text_color=T2).grid(row=7, column=0, padx=(10,6), sticky="e")
        self.ent_nanopic_video_type = ctk.CTkEntry(np, height=28, corner_radius=4, font=("Consolas",10), fg_color=EN, border_color=BD)
        self.ent_nanopic_video_type.grid(row=7, column=1, sticky="ew", padx=(0,10), pady=2)
        self.sw_nanopic_flow_proxy = ctk.CTkSwitch(np, text="Use /api/fix/create-flow", progress_color=OK, button_color="#FFF", button_hover_color="#EEE")
        self.sw_nanopic_flow_proxy.grid(row=8, column=1, sticky="w", padx=(0,10), pady=2)
        ctk.CTkLabel(np, text="Flow auth token:", font=("",11), text_color=T2).grid(row=9, column=0, padx=(10,6), sticky="e")
        self.ent_nanopic_flow_auth_token = ctk.CTkEntry(np, height=28, corner_radius=4, font=("Consolas",10), fg_color=EN, border_color=BD, show="*")
        self.ent_nanopic_flow_auth_token.grid(row=9, column=1, sticky="ew", padx=(0,10), pady=2)
        ctk.CTkLabel(np, text="Flow base URL:", font=("",11), text_color=T2).grid(row=10, column=0, padx=(10,6), sticky="e")
        self.ent_nanopic_flow_base_url = ctk.CTkEntry(np, height=28, corner_radius=4, font=("Consolas",10), fg_color=EN, border_color=BD)
        self.ent_nanopic_flow_base_url.grid(row=10, column=1, sticky="ew", padx=(0,10), pady=2)
        ctk.CTkLabel(np, text="Flow project ID:", font=("",11), text_color=T2).grid(row=11, column=0, padx=(10,6), sticky="e")
        self.ent_nanopic_flow_project_id = ctk.CTkEntry(np, height=28, corner_radius=4, font=("Consolas",10), fg_color=EN, border_color=BD)
        self.ent_nanopic_flow_project_id.grid(row=11, column=1, sticky="ew", padx=(0,10), pady=(2,10))

    def _add(self):
        url = self.ent_url.get().strip()
        if not url:
            return
        if not url.startswith("http"):
            url = "http://" + url
        nm = self.ent_nm.get().strip() or f"Sv-{len(self.sv_rows)+1}"
        bundle = self.ent_bundle.get().strip()
        chrome_path = self.ent_chrome.get().strip() or str(SUITE_ROOT / "GoogleChromePortable" / "GoogleChromePortable.exe")
        topics = self.ent_topics.get().strip()
        cfg = self.app.config_data
        if "local_server_list" not in cfg:
            old = cfg.get("local_server_url", "")
            cfg["local_server_list"] = []
            if old:
                cfg["local_server_list"].append({"url": old, "name": "Sv-1", "enabled": True, "flow_account_name": ""})
        cfg["local_server_list"].append({
            "url": url,
            "name": nm,
            "enabled": True,
            "flow_account_bundle": bundle,
            "chrome_path": chrome_path,
            "allowed_topics": topics,
        })
        cfg["local_server_url"] = url
        self.app._save_config()
        self.ent_url.delete(0, "end")
        self.ent_nm.delete(0, "end")
        self.ent_bundle.delete(0, "end")
        self.ent_chrome.delete(0, "end")
        self.ent_topics.delete(0, "end")
        self._render()
        self.app.test_all_servers()

    def _rm(self, i):
        sl = self.app.config_data.get("local_server_list", [])
        if 0 <= i < len(sl):
            sl.pop(i)
            self.app._save_config()
            self._render()

    def _toggle(self, i):
        sl = self.app.config_data.get("local_server_list", [])
        if 0 <= i < len(sl):
            sl[i]["enabled"] = not sl[i].get("enabled", True)
            self.app._save_config()
            self._render()

    def _edit(self, i):
        sl = self.app.config_data.get("local_server_list", [])
        if not (0 <= i < len(sl)) or not isinstance(sl[i], dict):
            return
        server = dict(sl[i])
        win = ctk.CTkToplevel(self)
        win.title(f"Edit server {server.get('name', i + 1)}")
        win.geometry("760x300")
        win.transient(self.winfo_toplevel())
        win.grab_set()
        win.grid_columnconfigure(1, weight=1)

        fields = [
            ("Name", "name", server.get("name", f"Sv-{i+1}")),
            ("URL", "url", server.get("url", "")),
            ("Gmail bundle", "flow_account_bundle", server.get("flow_account_bundle", "")),
            ("Chrome", "chrome_path", server.get("chrome_path", "")),
            ("Topics", "allowed_topics", server.get("allowed_topics", "")),
        ]
        entries = {}
        for row, (label, key, value) in enumerate(fields):
            ctk.CTkLabel(win, text=label + ":", font=("",11), text_color=T2).grid(row=row, column=0, padx=(12,8), pady=6, sticky="e")
            entry = ctk.CTkEntry(win, height=28, corner_radius=4, font=("Consolas",10) if key in {"url", "flow_account_bundle", "chrome_path"} else ("",10), fg_color=EN, border_color=BD)
            entry.grid(row=row, column=1, padx=(0,12), pady=6, sticky="ew")
            entry.insert(0, str(value or ""))
            entries[key] = entry

        ctk.CTkLabel(
            win,
            text='Topics: "story, psychology" = both; empty = all; also accepts "truyen, tam ly".',
            font=("",10),
            text_color=T3,
        ).grid(row=len(fields), column=1, padx=(0,12), pady=(0,8), sticky="w")

        btns = ctk.CTkFrame(win, fg_color="transparent")
        btns.grid(row=len(fields)+1, column=0, columnspan=2, pady=(4,12), sticky="e", padx=12)

        def save():
            updated = dict(server)
            for key, entry in entries.items():
                updated[key] = entry.get().strip()
            if updated.get("url") and not str(updated["url"]).startswith("http"):
                updated["url"] = "http://" + str(updated["url"])
            updated["enabled"] = bool(server.get("enabled", True))
            sl[i] = updated
            self.app.config_data["local_server_list"] = sl
            self.app.config_data["local_server_url"] = updated.get("url", self.app.config_data.get("local_server_url", ""))
            self.app._save_config()
            win.destroy()
            self._render()
            self.app.test_all_servers()

        ctk.CTkButton(btns, text="Cancel", width=80, height=28, corner_radius=4, fg_color="#EEE", hover_color=BD, text_color=T2, command=win.destroy).grid(row=0, column=0, padx=4)
        ctk.CTkButton(btns, text="Save", width=80, height=28, corner_radius=4, fg_color=OK, hover_color=OK2, text_color="#FFF", command=save).grid(row=0, column=1, padx=4)

    def _render(self):
        for w in self.sv_frame.winfo_children():
            w.destroy()
        self.sv_rows.clear()
        sl = self.app.config_data.get("local_server_list", [])
        if not sl:
            u = self.app.config_data.get("local_server_url", "")
            if u:
                sl = [{"url": u, "name": "Sv-1", "enabled": True}]
        if not sl:
            ctk.CTkLabel(self.sv_frame, text="-- no server pairs --", font=("",10), text_color=T3).grid(row=0, column=0, columnspan=6, pady=2)
            return
        for i, s in enumerate(sl):
            url = s["url"] if isinstance(s, dict) else s
            nm = s.get("name", f"Sv-{i+1}") if isinstance(s, dict) else f"Sv-{i+1}"
            en = s.get("enabled", True) if isinstance(s, dict) else True
            chrome_name = Path(str(s.get("chrome_path", "") or "")).name if isinstance(s, dict) else "-"
            account_name = self.app._pair_account_name(s) if isinstance(s, dict) else ""
            topics_display = str(s.get("allowed_topics", "") or "").strip() if isinstance(s, dict) else ""
            dot = ctk.CTkLabel(self.sv_frame, text="" if en else "", text_color=T3, font=("",10))
            dot.grid(row=i, column=0, padx=(0,2))
            ctk.CTkLabel(self.sv_frame, text=nm, font=("",11,"bold"), text_color=T1 if en else T3).grid(row=i, column=1, sticky="w")
            ctk.CTkLabel(self.sv_frame, text=url, font=("Consolas",9), text_color=T3 if en else "#CCC").grid(row=i, column=2, sticky="w", padx=4)
            ctk.CTkLabel(self.sv_frame, text=account_name or "(no gmail)", font=("Consolas",9), text_color=T3 if account_name else ER).grid(row=i, column=3, sticky="w", padx=4)
            ctk.CTkLabel(self.sv_frame, text=chrome_name or "-", font=("Consolas",9), text_color=T3 if chrome_name else "#CCC").grid(row=i, column=4, sticky="w", padx=4)
            ctk.CTkLabel(self.sv_frame, text=topics_display or "*", font=("",9), text_color="#8BC34A" if not topics_display else "#FFB74D").grid(row=i, column=5, sticky="w", padx=4)
            info = ctk.CTkLabel(self.sv_frame, text="", font=("",9), text_color=T3)
            info.grid(row=i, column=6, padx=4)
            ctk.CTkButton(self.sv_frame, text="Edit", width=34, height=18, corner_radius=3, fg_color="#EEE", hover_color=BD, text_color=T2, font=("",8,"bold"), command=lambda x=i: self._edit(x)).grid(row=i, column=7, padx=1)
            ctk.CTkButton(self.sv_frame, text="ON" if en else "OFF", width=34, height=18, corner_radius=3, fg_color=OK if en else "#BBB", hover_color=BD, text_color="#FFF", font=("",8,"bold"), command=lambda x=i: self._toggle(x)).grid(row=i, column=8, padx=1)
            ctk.CTkButton(self.sv_frame, text="x", width=20, height=18, corner_radius=3, fg_color="#F5D5D5", hover_color=ER, text_color=ER, font=("",9,"bold"), command=lambda x=i: self._rm(x)).grid(row=i, column=9, padx=(1,0))
            self.sv_rows.append({"dot": dot, "info": info, "url": url})

    def update_server_status(self, infos):
        m = {s["url"].rstrip("/"): s for s in infos}
        for r in self.sv_rows:
            si = m.get(r["url"].rstrip("/"))
            if si:
                if si.get("available"):
                    r["dot"].configure(text="", text_color=OK)
                    state = str(si.get("server_state", "ready") or "ready")
                    proc = si.get("processing_count", 0)
                    r["info"].configure(text=f'q={si.get("queue_size", "?")} p={proc} {state}', text_color=OK)
                else:
                    state = str(si.get("server_state", "offline") or "offline")
                    r["dot"].configure(text="", text_color=ER)
                    r["info"].configure(text=state, text_color=ER)

    def _on_excel_ai_provider_change(self, value=None):
        provider = self.excel_ai_provider_options.get((value or self.opt_excel_ai_provider.get()).strip(), "deepseek")
        if provider == "deepseek":
            self.ent_vov_slots.delete(0, "end")
            self.ent_vov_slots.insert(0, "0")
            self.ent_vov_slots.configure(state="disabled")
        else:
            self.ent_vov_slots.configure(state="normal")
            if provider == "deepseek_vov" and not (self.ent_vov_slots.get().strip() or "0").isdigit():
                self.ent_vov_slots.delete(0, "end")
                self.ent_vov_slots.insert(0, "2")
            elif provider == "deepseek_vov" and int(self.ent_vov_slots.get().strip() or "0") <= 0:
                self.ent_vov_slots.delete(0, "end")
                self.ent_vov_slots.insert(0, "2")

    def load_config(self, cfg):
        self._render()
        self.ent_conc.delete(0, "end")
        self.ent_conc.insert(0, str(cfg.get("max_concurrent", 1)))
        self.ent_retry.delete(0, "end")
        self.ent_retry.insert(0, str(cfg.get("retry_count", 3)))
        self.opt_ar.set(cfg.get("flow_aspect_ratio", "landscape"))
        backend_value = (cfg.get("generation_backend") or cfg.get("generation_mode") or "server").strip().lower()
        self.opt_generation_backend.set(self.generation_backend_labels.get(backend_value, "Server"))
        provider_value = (cfg.get("excel_ai_provider", "") or "deepseek").strip() or "deepseek"
        self.opt_excel_ai_provider.set(self.excel_ai_provider_labels.get(provider_value, "DeepSeek"))
        self.ent_deepseek_slots.delete(0, "end")
        self.ent_deepseek_slots.insert(0, str(cfg.get("deepseek_parallel_slots", cfg.get("excel_workers", 4)) or 4))
        self.ent_vov_slots.delete(0, "end")
        self.ent_vov_slots.insert(0, str(cfg.get("vov_direct_parallel_slots", 0) or 0))
        self._on_excel_ai_provider_change(self.opt_excel_ai_provider.get())
        self.ent_deepseek_key.delete(0, "end")
        self.ent_deepseek_key.insert(0, str(cfg.get("deepseek_api_key", "") or ""))
        self.ent_deepseek_keys.delete(0, "end")
        self.ent_deepseek_keys.insert(0, ", ".join(cfg.get("deepseek_api_keys", []) or []))
        self.ent_deepseek_model.delete(0, "end")
        self.ent_deepseek_model.insert(0, str(cfg.get("deepseek_model", "") or "deepseek-v4-pro"))
        self.ent_deepseek_thinking.delete(0, "end")
        self.ent_deepseek_thinking.insert(0, str(cfg.get("deepseek_thinking_type", "") or "disabled"))
        self.ent_vov_direct_url.delete(0, "end")
        self.ent_vov_direct_url.insert(0, str(cfg.get("vov_direct_base_url", "") or "https://routerapi.vovantin.online/v1"))
        self.ent_vov_direct_key.delete(0, "end")
        self.ent_vov_direct_key.insert(0, str(cfg.get("vov_direct_api_key", "") or "sk-6m5lfOmA6GdmbkZfWKXNYLtB6ouLfyfvf06obd7g3kZKdljB"))
        self.ent_vov_direct_model.delete(0, "end")
        self.ent_vov_direct_model.insert(0, str(cfg.get("vov_direct_model", "") or "claude-opus-4-6"))
        self.ent_vov_direct_chain.delete(0, "end")
        self.ent_vov_direct_chain.insert(0, ", ".join(cfg.get("vov_direct_model_chain", []) or [
            "claude-opus-4-6", "claude-sonnet-4-6"
        ]))
        self.ent_claude_pool_url.delete(0, "end")
        self.ent_claude_pool_url.insert(0, str(cfg.get("claude_pool_base_url", "") or "http://127.0.0.1:8318"))
        self.ent_claude_pool_key.delete(0, "end")
        self.ent_claude_pool_key.insert(0, str(cfg.get("claude_pool_api_key", "") or "sk_cliproxy_local"))
        self.ent_claude_pool_model.delete(0, "end")
        self.ent_claude_pool_model.insert(0, str(cfg.get("claude_pool_model", "") or "gpt-5.4"))
        self.ent_claude_pool_chain.delete(0, "end")
        self.ent_claude_pool_chain.insert(0, ", ".join(cfg.get("claude_pool_model_chain", []) or [
            "gpt-5.4", "gpt-5.2", "gpt-5.3-codex", "gemini-3-flash-agent", "gemini-3.1-pro-high"
        ]))
        self.ent_nanopic_base_url.delete(0, "end")
        self.ent_nanopic_base_url.insert(0, str(cfg.get("nanopic_base_url", "") or "https://flow-api.nanoai.pics/api/v2"))
        self.ent_nanopic_token.delete(0, "end")
        self.ent_nanopic_token.insert(0, str(cfg.get("nanopic_token", "") or ""))
        self.ent_nanopic_access_token.delete(0, "end")
        self.ent_nanopic_access_token.insert(0, str(cfg.get("nanopic_access_token", "") or ""))
        self.ent_nanopic_video_cookie.delete(0, "end")
        self.ent_nanopic_video_cookie.insert(0, str(cfg.get("nanopic_video_cookie", "") or ""))
        self.ent_nanopic_image_model.delete(0, "end")
        self.ent_nanopic_image_model.insert(0, str(cfg.get("nanopic_image_model", "") or "NARWHAL"))
        self.ent_nanopic_video_model.delete(0, "end")
        self.ent_nanopic_video_model.insert(0, str(cfg.get("nanopic_video_model", "") or "VEO_3_FAST_LOWER"))
        self.ent_nanopic_video_type.delete(0, "end")
        self.ent_nanopic_video_type.insert(0, str(cfg.get("nanopic_video_type", "") or "frame"))
        if cfg.get("nanopic_use_flow_proxy", False):
            self.sw_nanopic_flow_proxy.select()
        else:
            self.sw_nanopic_flow_proxy.deselect()
        self.ent_nanopic_flow_auth_token.delete(0, "end")
        self.ent_nanopic_flow_auth_token.insert(0, str(cfg.get("nanopic_flow_auth_token", "") or ""))
        self.ent_nanopic_flow_base_url.delete(0, "end")
        self.ent_nanopic_flow_base_url.insert(0, str(cfg.get("nanopic_flow_base_url", "") or "https://aisandbox-pa.googleapis.com"))
        self.ent_nanopic_flow_project_id.delete(0, "end")
        self.ent_nanopic_flow_project_id.insert(0, str(cfg.get("nanopic_flow_project_id", "") or cfg.get("project_id", "") or ""))
        if cfg.get("flow_auth_auto_enabled", True):
            self.sw_flow_auto.select()
        else:
            self.sw_flow_auto.deselect()
        if cfg.get("music_workspace_mode_enabled", True):
            self.sw_music_workspace.select()
        else:
            self.sw_music_workspace.deselect()

    def _auto_flowkit_server_list(self) -> list:
        """Auto-generate flowkit_server_list from Chrome Portable copies."""
        import glob as _glob
        suite_root = Path(__file__).resolve().parent.parent.parent
        pattern = str(suite_root / "GoogleChromePortable - Copy (*)")
        dirs = sorted(_glob.glob(pattern))
        servers = []
        for i, d in enumerate(dirs):
            chrome_bin = Path(d) / "App" / "Chrome-bin" / "chrome.exe"
            if chrome_bin.is_file():
                servers.append({
                    "url": f"http://127.0.0.1:{8100 + i}",
                    "name": f"flowkit-{i + 1}",
                    "enabled": True,
                })
        return servers

    def _save(self):
        cfg = self.app.config_data
        try:
            cfg["max_concurrent"] = max(1, int(self.ent_conc.get().strip() or "1"))
        except:
            cfg["max_concurrent"] = 1
        try:
            cfg["retry_count"] = max(1, int(self.ent_retry.get().strip() or "3"))
        except:
            cfg["retry_count"] = 3
        cfg["flow_aspect_ratio"] = self.opt_ar.get()
        selected_backend_label = self.opt_generation_backend.get().strip() or "Server"
        cfg["generation_backend"] = self.generation_backend_options.get(selected_backend_label, "server")
        cfg["generation_mode"] = cfg["generation_backend"]
        if cfg["generation_backend"] in ("flowkit", "combined") and not cfg.get("flowkit_server_list"):
            cfg["flowkit_server_list"] = self._auto_flowkit_server_list()
        cfg["flow_auth_auto_enabled"] = bool(self.sw_flow_auto.get())
        cfg["music_workspace_mode_enabled"] = bool(self.sw_music_workspace.get())
        selected_provider_label = self.opt_excel_ai_provider.get().strip() or "DeepSeek"
        cfg["excel_ai_provider"] = self.excel_ai_provider_options.get(selected_provider_label, "deepseek")
        try:
            deepseek_slots = max(1, int(self.ent_deepseek_slots.get().strip() or "4"))
        except:
            deepseek_slots = 4
        try:
            vov_slots = max(0, int(self.ent_vov_slots.get().strip() or "0"))
        except:
            vov_slots = 0
        if cfg["excel_ai_provider"] == "deepseek":
            vov_slots = 0
        elif cfg["excel_ai_provider"] == "deepseek_vov" and vov_slots <= 0:
            vov_slots = 2
        total_slots = deepseek_slots + vov_slots
        cfg["deepseek_parallel_slots"] = deepseek_slots
        cfg["vov_direct_parallel_slots"] = vov_slots
        cfg["excel_workers"] = total_slots
        cfg["max_parallel_api"] = total_slots
        cfg["deepseek_api_key"] = self.ent_deepseek_key.get().strip()
        cfg["deepseek_api_keys"] = [x.strip() for x in self.ent_deepseek_keys.get().split(",") if x.strip()]
        cfg["deepseek_model"] = self.ent_deepseek_model.get().strip() or "deepseek-v4-pro"
        cfg["deepseek_thinking_type"] = self.ent_deepseek_thinking.get().strip() or "disabled"
        cfg["vov_direct_base_url"] = self.ent_vov_direct_url.get().strip() or "https://routerapi.vovantin.online/v1"
        cfg["vov_direct_api_key"] = self.ent_vov_direct_key.get().strip() or "sk-6m5lfOmA6GdmbkZfWKXNYLtB6ouLfyfvf06obd7g3kZKdljB"
        cfg["vov_direct_model"] = self.ent_vov_direct_model.get().strip() or "claude-opus-4-6"
        cfg["vov_direct_model_chain"] = [x.strip() for x in self.ent_vov_direct_chain.get().split(",") if x.strip()] or [
            "claude-opus-4-6", "claude-sonnet-4-6"
        ]
        cfg["claude_pool_base_url"] = self.ent_claude_pool_url.get().strip() or "http://127.0.0.1:8318"
        cfg["claude_pool_api_key"] = self.ent_claude_pool_key.get().strip() or "sk_cliproxy_local"
        cfg["claude_pool_model"] = self.ent_claude_pool_model.get().strip() or "gpt-5.4"
        cfg["claude_pool_model_chain"] = [x.strip() for x in self.ent_claude_pool_chain.get().split(",") if x.strip()] or [
            "gpt-5.4", "gpt-5.2", "gpt-5.3-codex", "gemini-3-flash-agent", "gemini-3.1-pro-high"
        ]
        cfg["nanopic_base_url"] = self.ent_nanopic_base_url.get().strip() or "https://flow-api.nanoai.pics/api/v2"
        cfg["nanopic_token"] = self.ent_nanopic_token.get().strip()
        cfg["nanopic_access_token"] = self.ent_nanopic_access_token.get().strip()
        cfg["nanopic_video_cookie"] = self.ent_nanopic_video_cookie.get().strip()
        cfg["nanopic_image_model"] = self.ent_nanopic_image_model.get().strip() or "NARWHAL"
        cfg["nanopic_video_model"] = self.ent_nanopic_video_model.get().strip() or "VEO_3_FAST_LOWER"
        cfg["nanopic_video_type"] = self.ent_nanopic_video_type.get().strip() or "frame"
        cfg["nanopic_use_flow_proxy"] = bool(self.sw_nanopic_flow_proxy.get())
        cfg["nanopic_flow_auth_token"] = self.ent_nanopic_flow_auth_token.get().strip()
        cfg["nanopic_flow_base_url"] = self.ent_nanopic_flow_base_url.get().strip() or "https://aisandbox-pa.googleapis.com"
        cfg["nanopic_flow_project_id"] = self.ent_nanopic_flow_project_id.get().strip() or cfg.get("project_id", "")
        self.app._save_config()
        self.lbl_saved.configure(text="Saved")
        self.after(2000, lambda: self.lbl_saved.configure(text=""))


class VE3App(ctk.CTk):
    def __init__(self):
        super().__init__()
        self.title("VE3 Studio"); self.geometry("1200x820"); self.minsize(900,600)
        self.config_data = {}; self.worker = None; self.worker_thread = None
        self.music_thread = None; self.music_stop_requested = False
        self.music_lock = threading.Lock()
        self.child_procs = []
        self.child_proc_lock = threading.Lock()
        self._closing = False
        self.server_status_cache = []
        self.server_status_cache_ts = 0.0
        self.queue_running = False; self.queue_stop_requested = False
        self.queue_excel_thread = None; self.queue_ve3_thread = None
        self.queue_active_excel = set(); self.queue_active_ve3 = set()
        self.queue_active_pairs = {}
        self.queue_pair_use_seq = 0
        self.queue_pair_last_used = {}
        self.queue_excel_tasks = {}
        self.queue_ve3_tasks = {}
        self.queue_ve3_workers = {}
        self.queue_ve3_procs = {}      # {code: subprocess.Popen} - VE3 worker subprocesses
        self.queue_music_procs = {}    # {code: subprocess.Popen} - Music worker subprocesses
        self.queue_progress_owner_code = None
        self.queue_progress_owner_pair = "-"
        self.endpoint_active_codes = set()
        self.manual_done_codes = set()
        self.project_progress_cache = {}
        self.source_wait_log_ts = {}
        self.ve3_skip_log_ts = {}
        self.queue_lock = threading.Lock()
        self._log_queue = deque(maxlen=3000)
        self._log_queue_lock = threading.Lock()
        self._log_flush_scheduled = False
        self._progress_update_cache = {}
        self._progress_update_lock = threading.Lock()
        self._progress_flush_scheduled = False
        self._project_refresh_thread = None
        self._project_refresh_pending = False
        self._project_refresh_lock = threading.Lock()
        self._project_binding_cache = {}
        self._project_state_cache = {}
        self._project_state_cache_ttl = 10.0
        self._ve3_priority_cache = {}
        self._ve3_priority_cache_ttl = 20.0
        self._process_monitor_thread = None
        self._process_monitor_lock = threading.Lock()
        self._process_monitor_auto = True
        self._process_monitor_interval_ms = 60000
        self._server_pair_debug_enabled = True  # Enable server/account pair diagnostics
        self._server_pair_debug_last_ts = 0.0  # Rate limit debug logs
        self.excel_path = None; self.project_dir = None; self.wb = None
        self._t0 = None
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self._load_config(); self._build(); self.after(400, self._boot)

    def _clear_all_queue_markers(self):
        try:
            PROJECTS_DIR.mkdir(parents=True, exist_ok=True)
            cleared = 0
            for pd in PROJECTS_DIR.iterdir():
                if not pd.is_dir():
                    continue
                for marker in pd.glob(".queue_*.lock"):
                    try:
                        marker.unlink()
                        cleared += 1
                    except Exception:
                        pass
            return cleared
        except Exception:
            return 0

    def _refresh_manual_done_codes(self):
        """Rebuild in-memory set of manually completed project codes."""
        try:
            # Rebuild from disk each time to avoid stale in-memory codes.
            codes = set()
            PROJECTS_DIR.mkdir(parents=True, exist_ok=True)
            for pd in PROJECTS_DIR.iterdir():
                if not pd.is_dir():
                    continue
                if (pd / ".manual_done.lock").exists() or (pd / ".manual_skip.lock").exists():
                    codes.add(pd.name)
            self.manual_done_codes = codes
        except Exception:
            pass

    def _track_process(self, proc, label=""):
        if not proc:
            return
        if isinstance(proc, dict):
            with self.child_proc_lock:
                self.child_procs.append({"proc": proc.get("proc"), "label": label, "pid": proc.get("pid")})
            return
        with self.child_proc_lock:
            self.child_procs.append({"proc": proc, "label": label, "pid": getattr(proc, "pid", None)})

    def _untrack_process(self, proc):
        with self.child_proc_lock:
            self.child_procs = [x for x in self.child_procs if x.get("proc") is not proc]

    def _kill_pid_tree(self, pid):
        if not pid:
            return
        try:
            subprocess.run(
                ["taskkill", "/PID", str(pid), "/T", "/F"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=10,
                check=False,
            )
        except Exception:
            pass

    def _kill_own_child_processes(self):
        # Kill direct tracked children first.
        with self.child_proc_lock:
            tracked = list(self.child_procs)
            self.child_procs.clear()
        for item in tracked:
            self._kill_pid_tree(item.get("pid"))

        # Kill any remaining descendants of the current app only.
        parent_pid = os.getpid()
        script = rf"""
$ErrorActionPreference = 'SilentlyContinue'
$parent = {parent_pid}
$all = Get-CimInstance Win32_Process | Select-Object ProcessId,ParentProcessId
$children = @()
function Add-Children([int]$ppid) {{
  $direct = $all | Where-Object {{ $_.ParentProcessId -eq $ppid }}
  foreach ($p in $direct) {{
    $children += [int]$p.ProcessId
    Add-Children ([int]$p.ProcessId)
  }}
}}
Add-Children $parent
$children = $children | Sort-Object -Unique
foreach ($pid in $children) {{
  try {{ Stop-Process -Id $pid -Force }} catch {{ }}
}}
"""
        try:
            subprocess.run(
                ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", script],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=15,
                check=False,
            )
        except Exception:
            pass

    def _on_close(self):
        if self._closing:
            return
        self._closing = True
        try:
            self.queue_stop_requested = True
            self.music_stop_requested = True
            if self.worker:
                try:
                    self.worker.stop()
                except Exception:
                    pass
            # Kill all VE3 subprocesses
            with self.queue_lock:
                all_procs = list(self.queue_ve3_procs.values()) + list(self.queue_music_procs.values())
            for proc in all_procs:
                if proc and proc.poll() is None:
                    self._kill_pid_tree(proc.pid)
            self._kill_own_child_processes()
        finally:
            try:
                self.destroy()
            except Exception:
                pass

    def _load_config(self):
        try:
            import yaml
            p = VE3_DIR / "config" / "settings.yaml"
            if p.exists():
                with open(p,"r",encoding="utf-8") as f: self.config_data = yaml.safe_load(f) or {}
        except: self.config_data = {}
        self.config_data.setdefault("music_workspace_mode_enabled", True)

    def _save_config(self):
        try:
            import yaml
            with open(VE3_DIR/"config"/"settings.yaml","w",encoding="utf-8") as f:
                yaml.dump(self.config_data, f, default_flow_style=False, allow_unicode=True)
        except: pass

    def _music_workspace_mode_enabled(self):
        return bool(self.config_data.get("music_workspace_mode_enabled", True))

    def _music_window_position(self):
        if self._music_workspace_mode_enabled():
            return SUNO_WINDOW_POSITION_OFFSCREEN
        return SUNO_WINDOW_POSITION_VISIBLE

    def _resolve_excel_ai_provider(self, cfg=None):
        cfg = cfg or self.config_data
        provider = str(cfg.get("excel_ai_provider", "") or "").strip().lower()
        if provider in ("deepseek", "deepseek_vov", "claude_pool", "vov_direct"):
            return provider
        return "deepseek"

    def _validate_excel_ai_config(self, cfg=None):
        cfg = cfg or self.config_data
        provider = self._resolve_excel_ai_provider(cfg)
        if provider == "vov_direct":
            base_url = str(cfg.get("vov_direct_base_url", "") or "").strip()
            api_key = str(cfg.get("vov_direct_api_key", "") or "").strip()
            model = str(cfg.get("vov_direct_model", "") or "").strip()
            if not base_url:
                return False, "Can VOV base URL trong Cai dat."
            if not api_key:
                return False, "Can VOV API key trong Cai dat."
            if not model:
                return False, "Can VOV model trong Cai dat."
            return True, ""
        if provider == "claude_pool":
            base_url = str(cfg.get("claude_pool_base_url", "") or "").strip()
            api_key = str(cfg.get("claude_pool_api_key", "") or "").strip()
            model = str(cfg.get("claude_pool_model", "") or "").strip()
            if not base_url:
                return False, "Can Claude Pool base URL trong Cai dat."
            if not api_key:
                return False, "Can Claude Pool API key trong Cai dat."
            if not model:
                return False, "Can Claude Pool model trong Cai dat."
            return True, ""

        one_key = str(cfg.get("deepseek_api_key", "") or "").strip()
        many_keys = [str(x).strip() for x in (cfg.get("deepseek_api_keys", []) or []) if str(x).strip()]
        if not one_key and not many_keys:
            return False, "Can DeepSeek API key trong Cai dat."
        return True, ""

    def _normalize_project_topic(self, value):
        import unicodedata
        text = str(value or "").strip().lower()
        text = unicodedata.normalize("NFKD", text)
        text = "".join(ch for ch in text if not unicodedata.combining(ch))
        text = text.replace("_", " ").replace("-", " ")
        return " ".join(text.split())

    def _read_claimed_runtime_metadata(self, project_dir):
        claimed = Path(project_dir) / "_CLAIMED"
        if not claimed.exists():
            return {}
        try:
            lines = claimed.read_text(encoding="utf-8").splitlines()
        except UnicodeDecodeError:
            lines = claimed.read_text(encoding="utf-8-sig", errors="ignore").splitlines()
        data = {}
        if len(lines) >= 5 and lines[4].strip():
            data["raw_topic"] = lines[4].strip()
        if len(lines) >= 6 and lines[5].strip():
            data["character_template"] = lines[5].strip()
        return data

    _CODE_PREFIX_TOPIC = {
        "TL": "psychology",
        "TH": "finance",
        "MT": "success",
        "KA": "story",
        "TA": "story",
    }
    _nguon_sheet_cache = None

    def _infer_topic_from_code(self, code):
        """Return topic string based on project code prefix, or empty string."""
        import re
        m = re.match(r"^([A-Za-z]+)", str(code or ""))
        if m:
            prefix = m.group(1).upper()
            return self._CODE_PREFIX_TOPIC.get(prefix, "")
        return ""

    def _load_nguon_sheet(self):
        """Load sheet NGUON from Google Sheets (cached, loaded once)."""
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
            self._log(f"[TOPIC] Loaded sheet NGUON: {len(data)} rows")
            return data
        except Exception as e:
            self._log(f"[TOPIC] Cannot load sheet NGUON: {e}", "WARN")
            return []
        finally:
            socket.getaddrinfo = _orig_getaddrinfo

    def _lookup_topic_from_nguon_sheet(self, code):
        """Lookup topic from Google Sheet NGUON (Col G=code, Col S=topic). 15s timeout."""
        import concurrent.futures

        def _do():
            if self.__class__._nguon_sheet_cache is None:
                self.__class__._nguon_sheet_cache = self._load_nguon_sheet()
            if not self.__class__._nguon_sheet_cache:
                return ""
            code_upper = code.upper()
            for row in self.__class__._nguon_sheet_cache:
                if len(row) > 18:
                    cell_g = str(row[6]).strip().upper()
                    if cell_g == code_upper:
                        topic = str(row[18]).strip()
                        if topic:
                            return topic
            return ""

        executor = concurrent.futures.ThreadPoolExecutor(max_workers=1)
        try:
            return executor.submit(_do).result(timeout=15)
        except concurrent.futures.TimeoutError:
            self._log(f"[TOPIC] Sheet NGUON timeout (15s) for {code}", "WARN")
            executor.shutdown(wait=False)
            return ""
        except Exception:
            return ""

    def _lookup_reference_channel_from_nguon_sheet(self, code):
        """Lookup reference channel from NGUON (Col G=code, Col L=channel) with retries."""
        import concurrent.futures

        def _do():
            if self.__class__._nguon_sheet_cache is None or not self.__class__._nguon_sheet_cache:
                for attempt in range(1, 4):
                    self.__class__._nguon_sheet_cache = self._load_nguon_sheet()
                    if self.__class__._nguon_sheet_cache:
                        break
                    self._log(f"[TOPIC] Sheet NGUON retry {attempt}/3 for reference_channel {code}", "WARN")
                    _time.sleep(1.5 * attempt)
            rows = self.__class__._nguon_sheet_cache or []
            if not rows:
                return ""
            code_upper = str(code or "").strip().upper()
            headers = [str(x or "").strip().lower() for x in (rows[0] if rows else [])]
            channel_header_terms = {
                "reference_channel", "reference channel", "kenh", "kênh",
                "channel", "ma kenh", "mã kênh", "channel code",
            }
            channel_cols = [idx for idx, name in enumerate(headers) if name in channel_header_terms]
            root = SUITE_ROOT / "tools" / "srt-to-excel" / "reference_characters" / "psychology"

            def valid_channel(text):
                text = str(text or "").strip()
                if not text:
                    return ""
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
                        return found
                for idx in channel_cols:
                    if idx < len(row):
                        found = valid_channel(row[idx])
                        if found:
                            return found
                for cell in row:
                    found = valid_channel(cell)
                    if found:
                        return found
                return ""
            return ""

        executor = concurrent.futures.ThreadPoolExecutor(max_workers=1)
        try:
            return executor.submit(_do).result(timeout=15)
        except concurrent.futures.TimeoutError:
            self._log(f"[TOPIC] Sheet NGUON timeout (15s) for reference_channel {code}", "WARN")
            executor.shutdown(wait=False)
            return ""
        except Exception:
            return ""

    def _project_nguon_metadata_path(self, project_dir):
        return Path(project_dir) / ".nguon_runtime_metadata.yaml"

    def _read_project_nguon_metadata_cache(self, project_dir, code):
        path = self._project_nguon_metadata_path(project_dir)
        if not path.exists():
            return {}
        try:
            import yaml
            data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        except Exception:
            return {}
        if not isinstance(data, dict):
            return {}
        if str(data.get("project_code", "")).strip().upper() != str(code or "").strip().upper():
            return {}
        reference_channel = str(data.get("reference_channel", "") or "").strip()
        if reference_channel:
            root = SUITE_ROOT / "tools" / "srt-to-excel" / "reference_characters" / "psychology" / reference_channel
            if not ((root / "nv1.png").exists() or (root / "style.yaml").exists()):
                return {}
        return data

    def _write_project_nguon_metadata_cache(self, project_dir, data):
        if not data:
            return
        path = self._project_nguon_metadata_path(project_dir)
        try:
            import yaml
            with open(path, "w", encoding="utf-8") as f:
                yaml.dump(data, f, default_flow_style=False, allow_unicode=True, sort_keys=False)
        except Exception as e:
            self._log(f"[TOPIC] Cannot write NGUON metadata cache {path}: {e}", "WARN")

    def _load_project_nguon_metadata(self, project_dir, code):
        cached = self._read_project_nguon_metadata_cache(project_dir, code)
        if cached:
            self._log(f"[TOPIC] Using cached NGUON metadata: {self._project_nguon_metadata_path(project_dir)}")
            return cached
        topic = self._lookup_topic_from_nguon_sheet(code)
        sheet_reference_channel = self._lookup_reference_channel_from_nguon_sheet(code)
        if not topic and self.__class__._nguon_sheet_cache:
            topic = self._lookup_topic_from_nguon_sheet(code)
        reference_channel = self._resolve_psychology_reference_channel(sheet_reference_channel or "", code)
        ref_dir = {"finance": "finance", "success": "success"}.get(topic, "psychology")
        ref = SUITE_ROOT / "tools" / "srt-to-excel" / "reference_characters" / ref_dir / reference_channel / "nv1.png"
        if not ref.exists():
            for try_dir in ["psychology", "finance", "success"]:
                try_ref = SUITE_ROOT / "tools" / "srt-to-excel" / "reference_characters" / try_dir / reference_channel / "nv1.png"
                if try_ref.exists():
                    ref = try_ref
                    break
        data = {
            "project_code": code,
            "topic": topic or "",
            "reference_channel": reference_channel,
            "psychology_reference_image": str(ref) if ref.exists() else "",
            "source": "NGUON" if (topic or sheet_reference_channel) else "fallback",
            "fetched_at": int(_time.time()),
        }
        if topic or sheet_reference_channel:
            self._write_project_nguon_metadata_cache(project_dir, data)
        return data

    def _project_topic_runtime_config(self, project_dir, base_cfg=None):
        cfg = dict(base_cfg or {})
        meta = self._read_claimed_runtime_metadata(project_dir)
        topic_map = {
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
        code = Path(project_dir).name
        nguon_meta = self._load_project_nguon_metadata(project_dir, code)
        # Priority: _CLAIMED > Sheet NGUON > code prefix > config (may be stale) > default
        raw_topic = (
            meta.get("raw_topic")
            or nguon_meta.get("topic")
            or self._infer_topic_from_code(code)
            or cfg.get("topic")
            or "story"
        )
        mapped = topic_map.get(self._normalize_project_topic(raw_topic))
        reference_channel = self._resolve_psychology_reference_channel(
            nguon_meta.get("reference_channel") or cfg.get("reference_channel") or meta.get("character_template") or "",
            code,
        )
        out = {"project_code": Path(project_dir).name, "reference_channel": reference_channel}
        if mapped:
            out["topic"], out["excel_mode"] = mapped
        elif raw_topic:
            out["topic"] = str(raw_topic).strip()
        if meta.get("character_template"):
            out["character_template"] = meta["character_template"]
        if out.get("topic") in ("psychology", "finance", "success"):
            ref = Path(str(nguon_meta.get("psychology_reference_image") or ""))
            ref_dir = {"finance": "finance", "success": "success"}.get(out.get("topic"), "psychology")
            if not ref.exists():
                ref = SUITE_ROOT / "tools" / "srt-to-excel" / "reference_characters" / ref_dir / out["reference_channel"] / "nv1.png"
            if ref.exists():
                out["psychology_reference_image"] = str(ref)
        return out

    def _resolve_psychology_reference_channel(self, value="", project_code=""):
        """Resolve project codes like TL1-0002 → TL1-T2 or TH1-0003 → TH1-T3."""
        import re

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
        ref_base = SUITE_ROOT / "tools" / "srt-to-excel" / "reference_characters"
        for ref_dir in ["psychology", "finance", "success"]:
            root = ref_base / ref_dir
            for candidate in candidates:
                if (root / candidate / "nv1.png").exists() or (root / candidate / "style.yaml").exists():
                    return candidate
        return candidates[0] if candidates else ""

    def _build_excel_runtime_config(self, project_dir=None):
        cfg = dict(self.config_data or {})
        if project_dir:
            cfg.update(self._project_topic_runtime_config(Path(project_dir), cfg))
        cfg.setdefault("excel_ai_provider", self._resolve_excel_ai_provider(cfg))
        cfg.setdefault("deepseek_model", "deepseek-v4-pro")
        cfg.setdefault("deepseek_thinking_type", "disabled")
        cfg.setdefault("vov_direct_base_url", "https://routerapi.vovantin.online/v1")
        cfg.setdefault("vov_direct_api_key", "sk-6m5lfOmA6GdmbkZfWKXNYLtB6ouLfyfvf06obd7g3kZKdljB")
        cfg.setdefault("vov_direct_model", "claude-opus-4-6")
        cfg.setdefault("vov_direct_model_chain", ["claude-opus-4-6", "claude-sonnet-4-6"])
        cfg.setdefault("claude_pool_base_url", "http://127.0.0.1:8318")
        cfg.setdefault("claude_pool_api_key", "sk_cliproxy_local")
        cfg.setdefault("claude_pool_model", "gpt-5.4")
        cfg.setdefault("claude_pool_model_chain", ["gpt-5.4", "gpt-5.2", "gpt-5.3-codex", "gemini-3-flash-agent", "gemini-3.1-pro-high"])
        cfg.setdefault("excel_workers", 6)
        cfg.setdefault("max_parallel_api", 6)
        cfg.setdefault("deepseek_parallel_slots", 4)
        cfg.setdefault("vov_direct_parallel_slots", 2)
        cfg.setdefault("project_root", "../../PROJECTS")
        return cfg

    def _build(self):
        self.grid_columnconfigure(1, weight=1); self.grid_rowconfigure(0, weight=1)

        # sidebar
        sb = ctk.CTkFrame(self, width=SW, fg_color=SB, corner_radius=0)
        sb.grid(row=0, column=0, sticky="ns"); sb.grid_rowconfigure(3, weight=1); sb.grid_propagate(False)

        lf = ctk.CTkFrame(sb, fg_color="transparent")
        lf.grid(row=0, column=0, padx=12, pady=(16,20))
        ctk.CTkLabel(lf, text="", font=("",18,"bold"), text_color=AC).pack(side="left")
        ctk.CTkLabel(lf, text=" VE3", font=("",16,"bold"), text_color="#FFF").pack(side="left")

        self.nav = {}
        for i, (k, t) in enumerate([("home","Overview"), ("gen","Generate")]):
            b = ctk.CTkButton(sb, text=t, width=SW-16, height=34, fg_color="transparent",
                              hover_color=SB3, text_color="#999", anchor="w", corner_radius=6,
                              font=("",12), command=lambda x=k: self.show(x))
            b.grid(row=i+1, column=0, padx=8, pady=1); self.nav[k] = b

        self.btn_go = ctk.CTkButton(sb, text="RUN", width=SW-16, height=58,
                                     fg_color="#2E7D32", hover_color="#1B5E20", text_color="#FFFFFF",
                                     font=("",20,"bold"), corner_radius=10, command=self.toggle_queue_worker)
        self.btn_go.grid(row=4, column=0, padx=8, pady=(6,3))

        self.btn_st = ctk.CTkButton(sb, text="STOP", width=SW-16, height=46,
                                     fg_color="#555", hover_color="#333", text_color="#999",
                                     font=("",16,"bold"), corner_radius=8,
                                     command=self.stop_worker, state="disabled")
        self.btn_st.grid(row=5, column=0, padx=8, pady=(0,3))
        # Hide secondary STOP button; keep object for backward-compatible state checks.
        self.btn_st.grid_remove()

        self.lbl_tm = ctk.CTkLabel(sb, text="", font=("",10), text_color="#666")
        self.lbl_tm.grid(row=6, column=0, padx=8)

        ctk.CTkButton(sb, text="Open Folder", width=SW-16, height=28,
                      fg_color=SB2, hover_color=SB3, text_color="#888",
                      font=("",10), corner_radius=6,
                      command=self.open_folder).grid(row=7, column=0, padx=8, pady=(2,4))

        # Version + Update button
        self._version_label = ctk.CTkLabel(sb, text=f"v{self._get_local_version()}", font=("",9), text_color="#555")
        self._version_label.grid(row=8, column=0, padx=8, pady=(4,0))

        self._update_btn = ctk.CTkButton(sb, text="Check Update", width=SW-16, height=26,
                                         fg_color=SB2, hover_color="#2E7D32", text_color="#888",
                                         font=("",10), corner_radius=6,
                                         command=self._on_check_update)
        self._update_btn.grid(row=9, column=0, padx=8, pady=(2,4))

        # Settings button at bottom
        cfg_btn = ctk.CTkButton(sb, text="Settings", width=SW-16, height=28,
                                fg_color="transparent", hover_color=SB3, text_color="#777",
                                font=("",11), corner_radius=6, anchor="w",
                                command=lambda: self.show("cfg"))
        cfg_btn.grid(row=10, column=0, padx=8, pady=(0,14))
        self.nav["cfg"] = cfg_btn

        # main
        self.mf = ctk.CTkFrame(self, fg_color=BG, corner_radius=0)
        self.mf.grid(row=0, column=1, sticky="nsew")
        self.mf.grid_columnconfigure(0, weight=1); self.mf.grid_rowconfigure(0, weight=1)

        self.pages = {
            "home": HomePage(self.mf, self),
            "gen": GeneratePage(self.mf, self),
            "cfg": SettingsPage(self.mf, self),
        }
        self.pages["home"].set_config(self.config_data)
        self.pages["cfg"].load_config(self.config_data)
        self.show("home")

    def show(self, k):
        for p in self.pages.values(): p.grid_forget()
        self.pages[k].grid(row=0, column=0, sticky="nsew")
        for n, b in self.nav.items():
            if n==k: b.configure(fg_color=AC, text_color="#FFF", hover_color=AC2)
            else: b.configure(fg_color="transparent", text_color="#999", hover_color=SB3)

    def _get_local_version(self) -> str:
        try:
            import subprocess
            result = subprocess.run(
                ["git", "rev-list", "--count", "HEAD"],
                cwd=str(SUITE_ROOT), capture_output=True, text=True, timeout=5,
            )
            if result.returncode == 0:
                return f"1.0.{result.stdout.strip()}"
        except Exception:
            pass
        try:
            return (SUITE_ROOT / "VERSION").read_text(encoding="utf-8").strip()
        except Exception:
            return "?.?.?"

    def _on_check_update(self):
        self._update_btn.configure(text="Checking...", state="disabled", text_color="#FFA500")
        import threading
        threading.Thread(target=self._check_update_thread, daemon=True).start()

    def _check_update_thread(self):
        try:
            import sys
            sys.path.insert(0, str(SUITE_ROOT))
            from updater import check_update, download_and_apply
            info = check_update()
            if info.get("error"):
                err = info["error"][:30]
                self.after(0, lambda: self._update_btn.configure(text=f"Lỗi: {err}", state="normal", text_color="#FF4444"))
                self.after(5000, lambda: self._update_btn.configure(text="Check Update", text_color="#888"))
            elif info["available"]:
                remote = info["remote"]
                self.after(0, lambda: self._update_btn.configure(
                    text=f"Update v{remote}", state="normal",
                    fg_color="#2E7D32", text_color="#FFF",
                    command=lambda: self._do_update()))
                self.after(0, lambda: self._version_label.configure(
                    text=f"v{info['local']}  →  v{remote}", text_color="#FFA500"))
            else:
                self.after(0, lambda: self._update_btn.configure(text="Mới nhất ✓", state="normal", text_color="#43e97b"))
                self.after(5000, lambda: self._update_btn.configure(text="Check Update", text_color="#888", fg_color=SB2))
        except Exception as e:
            err_msg = str(e)[:30]
            self.after(0, lambda: self._update_btn.configure(text=f"Lỗi: {err_msg}", state="normal", text_color="#FF4444"))
            self.after(5000, lambda: self._update_btn.configure(text="Check Update", text_color="#888"))

    def _do_update(self):
        self._update_btn.configure(text="Đang tải...", state="disabled", text_color="#FFA500")
        import threading
        threading.Thread(target=self._update_thread, daemon=True).start()

    def _update_thread(self):
        try:
            import sys
            sys.path.insert(0, str(SUITE_ROOT))
            from updater import download_and_apply
            def _progress(msg):
                self.after(0, lambda m=msg: self._update_btn.configure(text=m[:20] + "..."))
            result = download_and_apply(progress_callback=_progress)
            if result["success"]:
                ver = result["version"]
                self.after(0, lambda: self._update_btn.configure(
                    text=f"v{ver} OK! Restart", state="normal", fg_color="#2E7D32", text_color="#FFF"))
                self.after(0, lambda: self._version_label.configure(text=f"v{ver}"))
            else:
                self.after(0, lambda: self._update_btn.configure(
                    text="Update lỗi", state="normal", text_color="#FF4444"))
                self.after(5000, lambda: self._update_btn.configure(text="Check Update", text_color="#888", fg_color=SB2))
        except Exception:
            self.after(0, lambda: self._update_btn.configure(text="Lỗi", state="normal", text_color="#FF4444"))
            self.after(3000, lambda: self._update_btn.configure(text="Check Update", text_color="#888", fg_color=SB2))

    def _boot(self):
        cleared = self._clear_all_queue_markers()
        self._refresh_manual_done_codes()
        if cleared:
            self._log(f"[QUEUE] Da don {cleared} lock cu khi khoi dong.", "WARN", "ve3")
        # Fetch server status synchronously BEFORE loading config to ensure accurate pair counts
        self._refresh_server_status_sync()
        self.pages["home"].load_server_config()
        self.pages["cfg"]._render()
        self._refresh_project_views()
        # Force immediate refresh for projects list on startup
        self.after(500, self._refresh_project_views)
        self.after(3000, self._process_monitor_tick)

    def refresh_process_monitor_now(self):
        self._start_process_monitor_refresh(manual=True)

    def toggle_process_monitor_auto(self):
        try:
            self._process_monitor_auto = bool(self.pages["home"].chk_process_auto.get())
        except Exception:
            self._process_monitor_auto = not bool(getattr(self, "_process_monitor_auto", True))
        if self._process_monitor_auto:
            self.after(1000, self._process_monitor_tick)

    def _process_monitor_tick(self):
        if getattr(self, "_closing", False):
            return
        if getattr(self, "_process_monitor_auto", True):
            self._start_process_monitor_refresh(manual=False)
            self.after(getattr(self, "_process_monitor_interval_ms", 60000), self._process_monitor_tick)

    def _start_process_monitor_refresh(self, manual=False):
        with self._process_monitor_lock:
            if self._process_monitor_thread and self._process_monitor_thread.is_alive():
                if manual:
                    try:
                        self.pages["home"].lbl_process_status.configure(text="Dang cap nhat, vui long cho...", text_color=T3)
                    except Exception:
                        pass
                return
            self._process_monitor_thread = threading.Thread(target=self._refresh_process_monitor_worker, daemon=True)
            self._process_monitor_thread.start()
        if manual:
            try:
                self.pages["home"].lbl_process_status.configure(text="Dang cap nhat...", text_color=T3)
            except Exception:
                pass

    def _refresh_process_monitor_worker(self):
        rows = []
        err = None
        try:
            rows = self._collect_ve3_process_rows()
        except Exception as exc:
            err = exc
        self.after(0, lambda rows=rows, err=err, ts=_time.time(): self._apply_process_monitor_rows(rows, ts, err))

    def _apply_process_monitor_rows(self, rows, ts, err=None):
        try:
            self.pages["home"].update_process_monitor(rows, ts, err)
        except Exception:
            pass
        with self._process_monitor_lock:
            self._process_monitor_thread = None

    def _collect_ve3_process_rows(self):
        script = r'''
$ErrorActionPreference = 'SilentlyContinue'
$patterns = @('D:\VE3_SUITE','ve3_worker.py','music_subprocess.py','run_project_headless.py','GoogleChromePortable')
Get-CimInstance Win32_Process |
  Where-Object {
    $cmd = [string]$_.CommandLine
    foreach ($p in $patterns) { if ($cmd -like "*$p*") { return $true } }
    return $false
  } |
  Select-Object ProcessId,ParentProcessId,Name,CommandLine,CreationDate |
  ConvertTo-Json -Compress
'''
        cp = subprocess.run(
            ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", script],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            encoding="utf-8",
            errors="replace",
            timeout=12,
            check=False,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        raw = (cp.stdout or "").strip()
        if not raw:
            return []
        data = json.loads(raw)
        if isinstance(data, dict):
            data = [data]
        now = _time.time()
        rows = []
        for item in data or []:
            cmd = str(item.get("CommandLine") or "")
            name = str(item.get("Name") or "")
            if "_collect_ve3_process_rows" in cmd:
                continue
            code = "-"
            m = re.search(r"(TL\d+-\d+|KA\d+-\d+|TA\d+-\d+)", cmd, flags=re.IGNORECASE)
            if m:
                code = m.group(1).upper()
            kind = "other"
            low = cmd.lower()
            if "ve3_worker.py" in low:
                kind = "VE3"
            elif "music_subprocess.py" in low:
                kind = "Music"
            elif "run_project_headless.py" in low:
                kind = "Excel"
            elif "googlechromeportable" in low or "tools\\suno" in low:
                kind = "ChromeSuno"
            elif "ve3_gui.py" in low:
                kind = "GUI"
            age = "-"
            created = item.get("CreationDate")
            if created:
                try:
                    dt = datetime.strptime(str(created).split(".")[0], "%Y%m%d%H%M%S")
                    age = _ts(max(0, now - dt.timestamp()))
                except Exception:
                    pass
            short_cmd = " ".join(cmd.split())
            if len(short_cmd) > 95:
                short_cmd = short_cmd[:92] + "..."
            rows.append({
                "pid": item.get("ProcessId", ""),
                "ppid": item.get("ParentProcessId", ""),
                "name": name,
                "kind": kind,
                "code": code,
                "age": age,
                "cmd": short_cmd,
            })
        order = {"GUI": 0, "Excel": 1, "VE3": 2, "Music": 3, "ChromeSuno": 4, "other": 9}
        rows.sort(key=lambda r: (r.get("code", "-"), order.get(r.get("kind", "other"), 9), int(r.get("pid") or 0)))
        return rows

    def _refresh_project_views(self):
        with self._project_refresh_lock:
            if self._project_refresh_thread and self._project_refresh_thread.is_alive():
                self._project_refresh_pending = True
                return
            self._project_refresh_pending = False
            self._project_refresh_thread = threading.Thread(target=self._refresh_project_views_worker, daemon=True)
            self._project_refresh_thread.start()
        # Lower refresh frequency to reduce IO and progress jitter.
        self.after(60000, self._refresh_project_views)

    def _refresh_project_views_worker(self):
        rows = []
        err = None
        try:
            PROJECTS_DIR.mkdir(parents=True, exist_ok=True)
            projects = [p for p in PROJECTS_DIR.iterdir() if p.is_dir()]
            projects.sort(key=lambda p: p.stat().st_mtime, reverse=True)
            for pd in projects:
                if self._is_project_exported_to_visual(pd):
                    continue
                rows.append(self._project_row(pd))
            state_order = {"RUN": 0, "WAIT": 1, "DONE": 2, "BLOCK": 3}
            rows.sort(key=lambda r: (state_order.get(r.get("state", "BLOCK"), 9), r.get("code", "")))
        except Exception as exc:
            err = exc
        self.after(0, lambda rows=rows, err=err: self._apply_project_views(rows, err))

    def _apply_project_views(self, rows, err=None):
        if err is not None:
            self._log(f"Khng qut c PROJECTS: {err}", "WARN")
        try:
            self.pages["home"].refresh_projects_overview(rows)
            self.pages["gen"].update_project_list(rows)
        except Exception as e:
            import traceback
            traceback.print_exc()
        with self._project_refresh_lock:
            rerun = self._project_refresh_pending
            self._project_refresh_pending = False
            if not rerun:
                self._project_refresh_thread = None
        if rerun:
            self._refresh_project_views()

    def toggle_project_manual_done(self, project_dir, mark_done=True):
        project_dir = Path(project_dir)
        code = project_dir.name
        if not mark_done:
            self._log(f"[QUEUE] {code}: che do bo xong da tat (Xong la lenh 1 chieu)", "WARN", "ve3")
            return
        if self._is_project_endpoint_complete(project_dir):
            self._log(f"[QUEUE] {code}: endpoint da xong, bo qua XONG thu cong", "WARN", "ve3")
            return

        self.manual_done_codes.add(code)
        self._set_project_manually_done(project_dir, True)
        self._log(f"[QUEUE] {code}: danh dau XONG thu cong (1 chieu), se xu ly nhu xong that", "WARN", "ve3")
        threading.Thread(
            target=self._manual_complete_project,
            args=(project_dir, code),
            daemon=True
        ).start()

        self._refresh_project_views()

    def clean_project_excel(self, project_dir, code):
        """Reset Excel data for a project: clear server/account/token/status/paths."""
        project_dir = Path(project_dir)
        ep = self._project_excel_path(project_dir)
        if not ep.exists():
            self._log(f"[RESET] {code}: khong tim thay Excel, bo qua", "WARN", "ve3")
            return

        with self.queue_lock:
            if code in self.queue_active_ve3 or code in self.queue_active_excel:
                self._log(f"[RESET] {code}: dang chay, khong the reset", "ERROR", "ve3")
                return

        from tkinter import messagebox
        if not messagebox.askyesno("Xac nhan Reset",
                f"Reset ma {code}?\n\n"
                f"Se xoa: server, account, token,\n"
                f"trang thai anh/video, duong dan, media_id.\n\n"
                f"Giu nguyen: prompts, SRT, nhan vat, boi canh.\n\n"
                f"Tiep tuc?",
                parent=self):
            return

        def _do_clean():
            try:
                from modules.excel_manager import PromptWorkbook
                CONFIG_CLEAR = {
                    "ve3_bound_server_name", "ve3_bound_server_url",
                    "ve3_bound_account_name", "flow_account_name",
                    "flow_bearer_token", "flow_project_id",
                    "flow_project_url", "flow_token_updated_at",
                }
                SCENES_RESET = {"img_path": "", "video_path": "", "media_id": "",
                                "status_img": "pending", "status_vid": "pending"}
                CHARS_RESET = {"status": "pending", "media_id": "", "reference_media_checked": ""}
                THUMB_RESET = {"img_path": "", "img_path_portrait": "",
                               "status_img": "pending", "status_portrait": "pending"}
                LOCS_RESET = {"status": "pending", "media_id": ""}

                wb = PromptWorkbook(str(ep))
                wb.load_or_create()
                changes = 0

                if "config" in wb.workbook.sheetnames:
                    ws = wb.workbook["config"]
                    for row_idx in range(2, ws.max_row + 1):
                        k = ws.cell(row=row_idx, column=1).value
                        if k and str(k).strip() in CONFIG_CLEAR:
                            v = ws.cell(row=row_idx, column=2).value
                            if v and str(v).strip():
                                ws.cell(row=row_idx, column=2, value="")
                                changes += 1

                def _reset_sheet(sheet_name, reset_map):
                    nonlocal changes
                    if sheet_name not in wb.workbook.sheetnames:
                        return
                    ws = wb.workbook[sheet_name]
                    hdrs = {str(c.value): c.column for c in ws[1] if c.value}
                    for row_idx in range(2, ws.max_row + 1):
                        if ws.cell(row=row_idx, column=1).value is None:
                            continue
                        for col_name, new_val in reset_map.items():
                            if col_name not in hdrs:
                                continue
                            col_idx = hdrs[col_name]
                            old = ws.cell(row=row_idx, column=col_idx).value
                            if new_val == "pending":
                                need = old is None or str(old).strip() not in ("pending", "")
                            else:
                                need = old is not None and str(old).strip() != ""
                            if need:
                                ws.cell(row=row_idx, column=col_idx, value=new_val)
                                changes += 1

                _reset_sheet("scenes", SCENES_RESET)
                _reset_sheet("characters", CHARS_RESET)
                _reset_sheet("thumbnail", THUMB_RESET)
                _reset_sheet("locations", LOCS_RESET)

                if changes > 0:
                    wb.save()

                for pattern in ["*.xlsx.lock", "*.xlsx.tmp", "*.xlsx.bak",
                                ".pending_writes_*", ".flowkit_quota_wait",
                                ".progress_totals.json"]:
                    for f in project_dir.glob(pattern):
                        try:
                            f.unlink()
                        except Exception:
                            pass

                if code in self.project_progress_cache:
                    del self.project_progress_cache[code]
                cache_key = str(project_dir)
                if cache_key in self._project_binding_cache:
                    del self._project_binding_cache[cache_key]

                self._log(f"[RESET] {code}: da xoa sach ({changes} thay doi)", "SUCCESS", "ve3")
                self.after(0, self._refresh_project_views)

            except Exception as e:
                self._log(f"[RESET] {code}: LOI - {e}", "ERROR", "ve3")

        threading.Thread(target=_do_clean, daemon=True).start()

    def _manual_complete_project(self, project_dir, code, timeout_sec=30):
        """Kill subprocess tree -> finalize -> endpoint. No race conditions."""
        self._log(f"[QUEUE] {code}: killing all subprocesses...", "WARN", "ve3")

        # Kill VE3 subprocess
        with self.queue_lock:
            ve3_proc = self.queue_ve3_procs.get(code)
            music_proc = self.queue_music_procs.get(code)

        if ve3_proc and ve3_proc.poll() is None:
            self._log(f"[QUEUE] {code}: killing VE3 worker PID={ve3_proc.pid}", "WARN", "ve3")
            self._kill_pid_tree(ve3_proc.pid)
            try:
                ve3_proc.wait(timeout=10)
            except Exception:
                pass

        # Kill music subprocess
        if music_proc and music_proc.poll() is None:
            self._log(f"[QUEUE] {code}: killing music worker PID={music_proc.pid}", "WARN", "ve3")
            self._kill_pid_tree(music_proc.pid)
            try:
                music_proc.wait(timeout=10)
            except Exception:
                pass

        # Also stop any thread-based workers (legacy/fallback)
        try:
            if self.project_dir and Path(self.project_dir) == project_dir and self.worker:
                self.worker.stop()
        except Exception:
            pass
        with self.queue_lock:
            workers = [
                w for w in self.queue_ve3_workers.values()
                if getattr(w, "project_dir", None) and Path(getattr(w, "project_dir")).name == code
            ]
        for w in workers:
            try:
                w.stop()
            except Exception:
                pass
        self.music_stop_requested = True

        # Wait briefly for task thread to finish cleanup
        start = _time.time()
        while _time.time() - start < timeout_sec:
            if not project_dir.exists():
                self._log(f"[QUEUE] {code}: endpoint da hoan tat (thu muc da xoa)", "SUCCESS", "ve3")
                return
            with self.queue_lock:
                task = self.queue_ve3_tasks.get(code)
                task_alive = bool(task and task.is_alive())
                active = code in self.queue_active_ve3 or task_alive
            if not active:
                break
            _time.sleep(0.5)

        if not project_dir.exists():
            return

        # Check if endpoint was already done by the _run_single_project_ve3 finally block
        if self._is_project_endpoint_complete(project_dir):
            self._log(f"[QUEUE] {code}: endpoint da duoc xu ly boi worker thread, khong can lam lai", "SUCCESS", "ve3")
            return

        self._log(f"[QUEUE] {code}: subprocesses killed, bat dau finalize + endpoint", "WARN", "ve3")
        finalize_ok = self._finalize_project_outputs(project_dir)
        if finalize_ok:
            moved_ok = self._complete_project_endpoint(project_dir, reason="manual_done")
            if moved_ok:
                self._log(f"[QUEUE] {code}: da copy old + visual (giu nguyen PROJECTS)", "SUCCESS", "ve3")
            else:
                self._log(f"[QUEUE] {code}: endpoint loi, giu marker manual_done", "ERROR", "ve3")
        else:
            self._log(f"[QUEUE] {code}: finalize loi, giu marker manual_done", "ERROR", "ve3")

    def _unique_archive_dest(self, code):
        ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)
        base = ARCHIVE_DIR / code
        if not base.exists():
            return base
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        return ARCHIVE_DIR / f"{code}_{stamp}"

    def _complete_project_endpoint(self, project_dir, reason="success"):
        """Finalize one project endpoint: archive + copy to edit, keep source in PROJECTS."""
        hold_marker = None
        code = None
        try:
            project_dir = Path(project_dir)
            if not project_dir.exists():
                return True
            code = project_dir.name
            hold_marker = self._endpoint_hold_marker(project_dir)
            done_marker = self._endpoint_done_marker(project_dir)
            with self.queue_lock:
                if code in self.endpoint_active_codes:
                    self._log(
                        f"[QUEUE] {code}: bo qua endpoint ({reason}) vi endpoint dang duoc xu ly",
                        "WARN",
                        "ve3",
                    )
                    return False
                self.endpoint_active_codes.add(code)
            if done_marker.exists():
                self._log(
                    f"[QUEUE] {code}: bo qua endpoint ({reason}) vi da co marker endpoint_done",
                    "INFO",
                    "ve3",
                )
                return True
            edit_dst = EDIT_VISUAL_DIR / code
            if self._has_project_archive(project_dir):
                try:
                    done_marker.write_text(
                        f"endpoint_done {time.time()} {reason} old_exists=1 repaired=1",
                        encoding="utf-8",
                    )
                except Exception:
                    pass
                self._log(
                    f"[QUEUE] {code}: phat hien da co old/{code}, tao lai marker endpoint_done va bo qua chay lai",
                    "WARN",
                    "ve3",
                )
                return True
            if edit_dst.exists():
                try:
                    done_marker.write_text(
                        f"endpoint_done {time.time()} {reason} visual={code} repaired=1",
                        encoding="utf-8",
                    )
                except Exception:
                    pass
                self._log(
                    f"[QUEUE] {code}: phat hien da co AUTO/visual/{code}, tao lai marker endpoint_done va bo qua chay lai",
                    "WARN",
                    "ve3",
                )
                return True
            with self.queue_lock:
                task_excel = self.queue_excel_tasks.get(code)
                task_ve3 = self.queue_ve3_tasks.get(code)
                ve3_proc = self.queue_ve3_procs.get(code)
                busy = (
                    code in self.queue_active_excel or
                    code in self.queue_active_ve3 or
                    bool(task_excel and task_excel.is_alive()) or
                    bool(task_ve3 and task_ve3.is_alive()) or
                    bool(ve3_proc and ve3_proc.poll() is None)
                )
            # Allow manual_done and kill-based stops to skip busy check
            # (subprocess was killed, task thread is in cleanup)
            if busy and reason not in ("manual_done", "manual_done_after_stop"):
                raise RuntimeError(f"project van con worker/queue dang chay: {code}")
            elif busy:
                self._log(f"[QUEUE] {code}: endpoint ({reason}) skip busy check vi subprocess da bi kill", "WARN", "ve3")
            ep = self._project_excel_path(project_dir)
            if ep.exists():
                if not self._wait_excel_ready_for_endpoint(
                    ep,
                    reason=reason,
                    timeout_sec=90 if reason in ("manual_done", "manual_done_after_stop") else 12,
                ):
                    if self._excel_is_locked(ep):
                        raise RuntimeError(f"excel chua on dinh (locked): {ep.name}")
                    raise RuntimeError(f"excel chua on dinh (mtime/size dang doi): {ep.name}")
            try:
                hold_marker.write_text(f"endpoint_hold {time.time()} {reason}", encoding="utf-8")
            except Exception:
                pass
            # IMPORTANT:
            # Keep .manual_done.lock until endpoint fully succeeds.
            # If endpoint fails mid-way and manual marker is removed too early,
            # queue will pick this project again and restart it.
            for marker in list(project_dir.glob(".queue_*.lock")):
                try:
                    if marker.exists():
                        marker.unlink()
                except Exception:
                    pass
            archive_dst = self._unique_archive_dest(code)
            ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)
            EDIT_VISUAL_DIR.mkdir(parents=True, exist_ok=True)

            shutil.copytree(str(project_dir), str(archive_dst))
            if edit_dst.exists():
                shutil.rmtree(edit_dst, ignore_errors=True)
            shutil.copytree(str(project_dir), str(edit_dst))
            # Cleanup markers in destination copies (not needed downstream).
            for copied_dir in (archive_dst, edit_dst):
                for marker in list(copied_dir.glob(".queue_*.lock")) + [
                    copied_dir / ".manual_done.lock",
                    copied_dir / ".manual_skip.lock",
                    copied_dir / ".endpoint_done.lock",
                    copied_dir / ".endpoint_hold.lock",
                ]:
                    try:
                        if marker.exists():
                            marker.unlink()
                    except Exception:
                        pass
            try:
                done_marker.write_text(
                    f"endpoint_done {time.time()} {reason} old={archive_dst.name} visual={code}",
                    encoding="utf-8",
                )
            except Exception:
                pass

            try:
                self.pages["home"].remove_project_log(code)
            except Exception:
                pass
            self._log(
                f"[QUEUE] {code}: hoan tat endpoint ({reason}) -> old/{archive_dst.name} va AUTO/visual/{code} (giu source PROJECTS/{code})",
                "SUCCESS",
                "ve3",
            )
            try:
                self.after(0, self._refresh_project_views)
            except Exception:
                pass
            return True
        except Exception as exc:
            self._log(f"[QUEUE] {Path(project_dir).name}: endpoint loi {exc}", "ERROR", "ve3")
            return False
        finally:
            if code is not None:
                with self.queue_lock:
                    self.endpoint_active_codes.discard(code)
            if hold_marker is not None:
                try:
                    if hold_marker.exists():
                        hold_marker.unlink()
                except Exception:
                    pass

    def _finalize_project_outputs(self, project_dir):
        """Finalize output files for one project before manual completion."""
        try:
            img_dir = project_dir / "img"
            vid_dir = project_dir / "vid"
            backup_dir = project_dir / "img_backup"

            if not img_dir.exists():
                self._log(f"[QUEUE] {project_dir.name}: finalize bo qua, img/ khong ton tai", "WARN", "ve3")
                return False

            backup_dir.mkdir(parents=True, exist_ok=True)

            for p in list(img_dir.glob("*.png")) + list(img_dir.glob("*.jpg")):
                dst = backup_dir / p.name
                if not dst.exists():
                    shutil.copy2(p, dst)

            vid_mp4s = {m.stem: m for m in vid_dir.glob("*.mp4")} if vid_dir.exists() else {}
            bak_pngs = {p.stem: p for p in backup_dir.glob("*.png")}

            copied_mp4 = 0
            copied_png = 0
            for sid in set(vid_mp4s) | set(bak_pngs):
                if sid in vid_mp4s:
                    dst = img_dir / f"{sid}.mp4"
                    if not dst.exists():
                        shutil.copy2(vid_mp4s[sid], dst)
                        copied_mp4 += 1
                    for ext in (".png", ".jpg"):
                        old = img_dir / f"{sid}{ext}"
                        if old.exists():
                            old.unlink()
                else:
                    dst = img_dir / f"{sid}.png"
                    if not dst.exists() and sid in bak_pngs:
                        shutil.copy2(bak_pngs[sid], dst)
                        copied_png += 1

            total = len(list(img_dir.iterdir()))
            self._log(f"[QUEUE] {project_dir.name}: finalize {copied_mp4} mp4 + {copied_png} png -> img/ (tong {total} files)", "INFO", "ve3")
            return True
        except Exception as exc:
            self._log(f"[QUEUE] {project_dir.name}: finalize loi {exc}", "WARN", "ve3")
            return False

    def _project_row(self, pd):
        code = pd.name
        manual_done = self._is_project_manually_done(pd)
        has_audio = any(list(pd.glob(ext)) for ext in ("*.mp3", "*.wav", "*.m4a", "*.flac", "*.ogg", "*.aac"))
        srt = pd / f"{code}.srt"
        ep = self._project_excel_path(pd)
        binding = self._load_project_pair_binding(pd)
        all_pairs = self._get_server_pairs(only_available=False)
        pair_by_server = {p["server_name"]: p for p in all_pairs}
        flow_project_id = binding.get("flow_project_id", "")
        server_name = binding.get("bound_server_name", "") or "-"
        account_name = binding.get("bound_account_name", "") or binding.get("flow_account_name", "") or "-"
        pair_state = "AUTO"
        if server_name != "-":
            pair = pair_by_server.get(server_name)
            if pair and pair.get("flow_account_name") == (account_name if account_name != "-" else pair.get("flow_account_name")):
                pair_state = "READY" if pair.get("available") else "WAIT"
            else:
                pair_state = "MISS"
        elif flow_project_id:
            pair_state = "UNBOUND"

        state = "BLOCK"
        next_step = "Missing source"
        scenes = 0
        char_progress = "-"
        img_progress = "-"
        vid_progress = "-"
        music_progress = "-"

        excel_running = self._queue_marker(pd, "excel").exists()
        ve3_running = self._queue_marker(pd, "ve3").exists()

        if not ep.exists():
            latest_media = {
                "latest_media_ts": 0.0,
                "latest_media_age": "-",
                "latest_media_name": "",
                "latest_media_kind": "",
            }
            for folder, kind, patterns in (
                (pd / "img", "IMG", ("*.png", "*.jpg", "*.jpeg", "*.webp", "*.mp4")),
                (pd / "vid", "VID", ("*.mp4",)),
            ):
                if not folder.exists():
                    continue
                for pattern in patterns:
                    for f in folder.glob(pattern):
                        try:
                            if f.is_file() and float(f.stat().st_mtime) > latest_media["latest_media_ts"]:
                                latest_media = {
                                    "latest_media_ts": float(f.stat().st_mtime),
                                    "latest_media_age": _media_age(f.stat().st_mtime),
                                    "latest_media_name": f.name,
                                    "latest_media_kind": kind,
                                }
                        except Exception:
                            continue
            state = "WAIT" if (has_audio or srt.exists()) else "BLOCK"
            next_step = "Build Excel" if (has_audio or srt.exists()) else "Missing MP3/SRT"
            if excel_running:
                state = "RUN"
                next_step = "Excel"
            if manual_done and state != "RUN":
                state = "DONE"
                next_step = "Manually done"
            return {
                "code": code,
                "path": str(pd),
                "manual_done": manual_done,
                "source": "OK" if has_audio else "-",
                "srt": "OK" if srt.exists() else "-",
                "excel": "OK" if ep.exists() else "-",
                "pair_state": pair_state,
                "server_name": server_name,
                "account_name": account_name,
                "state": state,
                "next": next_step,
                "scenes": scenes,
                "char_progress": char_progress,
                "img_progress": img_progress,
                "vid_progress": vid_progress,
                "music_progress": music_progress,
                "excel_running": excel_running,
                "ve3_running": ve3_running,
                "excel_complete": False,
                "needs_ve3": False,
                "visuals_done": False,
                "music_ready": False,
                **latest_media,
            }

        cache = self.project_progress_cache.get(code, {})

        def _latest_media_info():
            candidates = []
            for folder, kind, patterns in (
                (pd / "img", "IMG", ("*.png", "*.jpg", "*.jpeg", "*.webp", "*.mp4")),
                (pd / "vid", "VID", ("*.mp4",)),
            ):
                if not folder.exists():
                    continue
                for pattern in patterns:
                    for f in folder.glob(pattern):
                        try:
                            if f.is_file():
                                candidates.append((float(f.stat().st_mtime), kind, f.name))
                        except Exception:
                            continue
            if not candidates:
                return {"latest_media_ts": 0.0, "latest_media_age": "-", "latest_media_name": "", "latest_media_kind": ""}
            ts, kind, name = max(candidates, key=lambda item: item[0])
            return {"latest_media_ts": ts, "latest_media_age": _media_age(ts), "latest_media_name": name, "latest_media_kind": kind}

        latest_media = _latest_media_info()

        def _numeric_stems(files):
            stems = set()
            for f in files:
                try:
                    st = str(f.stem).strip()
                    if st.isdigit():
                        stems.add(int(st))
                except Exception:
                    continue
            return stems

        # Folder-based done counters (fast, lock-safe).
        img_dir = pd / "img"
        vid_dir = pd / "vid"
        nv_dir = pd / "nv"
        music_dir = pd / "music"

        img_stems = _numeric_stems(list(img_dir.glob("*.png")) + list(img_dir.glob("*.jpg")) + list(img_dir.glob("*.mp4"))) if img_dir.exists() else set()
        vid_stems = _numeric_stems(list(vid_dir.glob("*.mp4"))) if vid_dir.exists() else set()
        vid_done_stems = set(vid_stems) | {s for s in img_stems if (img_dir / f"{s}.mp4").exists()}

        images_done = len(img_stems)
        videos_done = len(vid_done_stems)

        # Totals: read from Excel exactly once per project, then lock forever.
        totals_locked = bool(cache.get("totals_locked", False))
        locked_scene_total = int(cache.get("locked_scene_total", 0) or 0)
        locked_video_total = int(cache.get("locked_video_total", 0) or 0)
        totals_file = pd / ".progress_totals.json"
        stale_totals = False

        # Prefer persisted totals so app restarts do not re-read Excel repeatedly.
        if (not totals_locked) and totals_file.exists():
            try:
                payload = json.loads(totals_file.read_text(encoding="utf-8"))
                locked_scene_total = int(payload.get("scene_total", 0) or 0)
                locked_video_total = int(payload.get("video_total", 0) or 0)
                totals_locked = bool(locked_scene_total > 0 or locked_video_total > 0)
                excel_mtime_saved = float(payload.get("excel_mtime", 0) or 0)
                excel_mtime_now = float(ep.stat().st_mtime) if ep.exists() else 0.0
                # If Excel changed after totals were captured, totals may be stale.
                if excel_mtime_now > excel_mtime_saved + 0.5:
                    stale_totals = True
            except Exception:
                pass

        # Capture once when not locked; or re-capture when previous lock is stale.
        if (not totals_locked or stale_totals) and ep.exists():
            max_retries = 3
            retry_delay = 2
            excel_read_success = False

            for attempt in range(max_retries):
                if self._excel_is_locked(ep):
                    if attempt < max_retries - 1:
                        self._log(f"[{code}] Excel locked (attempt {attempt + 1}/{max_retries}), waiting {retry_delay}s...")
                        _time.sleep(retry_delay)
                        continue
                    else:
                        self._log(f"[{code}] Excel still locked after {max_retries} attempts, skipping read")
                        break

                try:
                    from modules.excel_manager import PromptWorkbook
                    wb = PromptWorkbook(str(ep)); wb.load_or_create()
                    scenes_all = wb.get_scenes() or []
                    scene_total_now = sum(
                        1 for s in scenes_all
                        if str(getattr(s, "img_prompt", "") or "").strip()
                    )
                    video_total_now = sum(
                        1 for s in scenes_all
                        if str(getattr(s, "video_prompt", "") or "").strip()
                    )
                    # Never shrink totals in UI; allow correction upward from stale lock.
                    locked_scene_total = max(locked_scene_total, scene_total_now)
                    locked_video_total = max(locked_video_total, video_total_now)
                    totals_locked = bool(locked_scene_total > 0 or locked_video_total > 0)

                    if attempt > 0:
                        self._log(f"[{code}] Excel read succeeded on attempt {attempt + 1}")

                    try:
                        totals_file.write_text(
                            json.dumps(
                                {
                                    "scene_total": locked_scene_total,
                                    "video_total": locked_video_total,
                                    "excel_mtime": float(ep.stat().st_mtime),
                                },
                                ensure_ascii=False,
                            ),
                            encoding="utf-8",
                        )
                    except Exception:
                        pass

                    excel_read_success = True
                    break

                except Exception as e:
                    if attempt < max_retries - 1:
                        self._log(f"[{code}] Excel read failed (attempt {attempt + 1}/{max_retries}): {e}")
                        _time.sleep(retry_delay)
                    else:
                        self._log(f"[{code}] Excel read FAILED after {max_retries} attempts: {e}")
                        break

        # Before totals are locked, keep prior cached totals (avoid wrong jumps).
        scenes = locked_scene_total if totals_locked else int(cache.get("scenes", 0) or 0)
        video_total = locked_video_total if totals_locked else int(cache.get("video_total", 0) or 0)

        # Characters/music totals are unknown without Excel, so derive from folders and cache.
        chars_done = len(list(nv_dir.glob("*.png"))) if nv_dir.exists() else 0
        total_chars = max(chars_done, int(cache.get("total_chars", 0) or 0))
        music_done = len(list(music_dir.glob("*.mp3"))) if music_dir.exists() else 0
        music_total = max(music_done, int(cache.get("music_total", 0) or 0))

        # Non-decreasing stabilization for done counters (avoid flicker/jumps).
        if scenes > 0:
            images_done = max(images_done, int(cache.get("images_done", 0) or 0))
        if video_total > 0:
            videos_done = max(videos_done, int(cache.get("videos_done", 0) or 0))
        if total_chars > 0 and int(cache.get("total_chars", 0) or 0) == total_chars:
            chars_done = max(chars_done, int(cache.get("chars_done", 0) or 0))
        if music_total > 0 and int(cache.get("music_total", 0) or 0) == music_total:
            music_done = max(music_done, int(cache.get("music_done", 0) or 0))

        # Clamp done <= total for clean UI.
        if scenes > 0:
            images_done = min(images_done, scenes)
        if video_total > 0:
            videos_done = min(videos_done, video_total)

        img_progress = f"{images_done}/{scenes}" if scenes else "-"
        vid_progress = f"{videos_done}/{video_total}" if video_total else "-"
        char_progress = f"{chars_done}/{total_chars}" if total_chars else "-"
        music_progress = f"{music_done}/{music_total}" if music_total else "-"

        self.project_progress_cache[code] = {
            "totals_locked": totals_locked,
            "locked_scene_total": locked_scene_total,
            "locked_video_total": locked_video_total,
            "scenes": scenes,
            "images_done": images_done,
            "videos_done": videos_done,
            "video_total": video_total,
            "total_chars": total_chars,
            "chars_done": chars_done,
            "music_total": music_total,
            "music_done": music_done,
        }

        visuals_done = bool(scenes > 0 and images_done >= scenes and videos_done >= video_total)
        music_ready = bool(music_total == 0 or music_done >= music_total)
        excel_complete = bool(ep.exists() and not excel_running and scenes > 0)
        needs_ve3 = bool(excel_complete and not ve3_running and not manual_done and (not visuals_done or not music_ready))

        if excel_running:
            state = "RUN"
            next_step = "Excel"
        elif ve3_running:
            state = "RUN"
            if server_name != "-" or account_name != "-":
                next_step = f"{server_name}/{account_name}"
            if scenes and images_done >= scenes and videos_done >= video_total and music_total > 0 and music_done < music_total:
                next_step = "Music"
            elif next_step == f"{server_name}/{account_name}":
                pass
            else:
                next_step = "VE3/Music"
        elif scenes <= 0:
            state = "WAIT"
            next_step = "Waiting source data"
        else:
            if visuals_done and music_ready:
                state = "DONE"
                next_step = "-"
            elif visuals_done and not music_ready:
                state = "WAIT"
                next_step = "Music"
            else:
                state = "WAIT"
                if pair_state == "WAIT":
                    next_step = "Waiting pair"
                elif pair_state in ("MISS", "UNBOUND"):
                    next_step = "Fix pair"
                else:
                    next_step = "VE3"

        if manual_done and state != "RUN":
            state = "DONE"
            next_step = "Manually done"

        return {
            "code": code,
            "path": str(pd),
            "manual_done": manual_done,
            "source": "OK" if has_audio else "-",
            "srt": "OK" if srt.exists() else "-",
            "excel": "OK" if ep.exists() else "-",
            "pair_state": pair_state,
            "server_name": server_name,
            "account_name": account_name,
            "state": state,
            "next": next_step,
            "scenes": scenes,
            "char_progress": char_progress,
            "img_progress": img_progress,
            "vid_progress": vid_progress,
            "music_progress": music_progress,
            "excel_running": excel_running,
            "ve3_running": ve3_running,
            "excel_complete": excel_complete,
            "needs_ve3": needs_ve3,
            "visuals_done": visuals_done,
            "music_ready": music_ready,
            **latest_media,
        }

    def _get_svs(self):
        out = []
        sl = self.config_data.get("local_server_list",[])
        if sl:
            for s in sl:
                if isinstance(s,str): out.append({"url":s,"name":s})
                elif isinstance(s,dict) and s.get("enabled",True): out.append(s)
        else:
            u = self.config_data.get("local_server_url","")
            if u: out.append({"url":u,"name":"Sv-1"})
        return out

    def _split_account_bundle(self, bundle_text):
        raw = str(bundle_text or "").strip()
        if not raw:
            return ("", "", "")
        parts = raw.split("|")
        email = parts[0].strip() if len(parts) > 0 else ""
        password = parts[1].strip() if len(parts) > 1 else ""
        totp = "|".join(parts[2:]).strip() if len(parts) > 2 else ""
        return (email, password, totp)

    def _pair_account_name(self, row, idx=0):
        if not isinstance(row, dict):
            return ""
        explicit = str(row.get("flow_account_name", "") or "").strip()
        if explicit:
            return explicit
        email, _, _ = self._split_account_bundle(row.get("flow_account_bundle", ""))
        if email:
            return email
        return f"pair-{idx+1}"

    def _pair_account_from_row(self, row, idx=0):
        if not isinstance(row, dict):
            return None
        email, password, totp = self._split_account_bundle(row.get("flow_account_bundle", ""))
        chrome_path = str(row.get("chrome_path", "") or "").strip()
        if not email or not password or not chrome_path:
            return None
        name = self._pair_account_name(row, idx)
        profile_name = "".join(ch if ch.isalnum() or ch in ("-", "_", ".") else "_" for ch in name) or f"pair_{idx+1}"
        profile_dir = str(row.get("profile_dir", "") or (SUITE_ROOT / "chrome_profiles" / profile_name))
        return {
            "name": name,
            "email": email,
            "password": password,
            "totp_secret": totp,
            "chrome_path": chrome_path,
            "profile_dir": profile_dir,
            "enabled": bool(row.get("enabled", True)),
        }

    def _get_flow_account_map(self):
        out = {}
        for row in self.config_data.get("flow_accounts", []) or []:
            if not isinstance(row, dict):
                continue
            name = (row.get("name") or row.get("email") or "").strip()
            if not name:
                continue
            out[name] = row
        for idx, row in enumerate(self.config_data.get("local_server_list", []) or []):
            if not isinstance(row, dict):
                continue
            account = self._pair_account_from_row(row, idx)
            if account:
                out[account["name"]] = account
        _now = _time.time()
        _should_log_pairs = getattr(self, "_server_pair_debug_enabled", False) and (_now - getattr(self, "_server_pair_debug_last_ts", 0)) >= 30
        if _should_log_pairs:
            try:
                self._log(f"[DEBUG] Account map has {len(out)} entries: {', '.join(out.keys()) or '-'}", "INFO", "ve3")
            except Exception:
                pass
        return out

    def _get_server_pairs(self, only_available=False):
        account_map = self._get_flow_account_map()
        status_map = {str(s.get("url", "")).rstrip("/"): s for s in (self.server_status_cache or [])}
        pairs = []
        _now = _time.time()
        _should_log_pairs = getattr(self, "_server_pair_debug_enabled", False) and (_now - getattr(self, "_server_pair_debug_last_ts", 0)) >= 30
        for idx, row in enumerate(self.config_data.get("local_server_list", []) or []):
            if isinstance(row, str):
                row = {"url": row, "name": f"Sv-{idx+1}", "enabled": True, "flow_account_name": ""}
            if not isinstance(row, dict):
                continue
            url = str(row.get("url", "") or "").strip().rstrip("/")
            name = str(row.get("name", "") or url or f"Sv-{idx+1}").strip()
            enabled = bool(row.get("enabled", True))
            account_name = str(row.get("flow_account_name", "") or self._pair_account_name(row, idx) or "").strip()
            account = account_map.get(account_name)
            status = status_map.get(url, {})
            status_accepts = self._status_accepts_tasks(status)
            available = bool(enabled and account and status_accepts)
            if _should_log_pairs and not available:
                reasons = []
                if not enabled:
                    reasons.append("disabled")
                if not account:
                    reasons.append(f"account '{account_name}' not found")
                if not status_accepts:
                    state = status.get("server_state", "unknown") if isinstance(status, dict) else "missing"
                    chrome_ready = status.get("chrome_ready", 0) if isinstance(status, dict) else 0
                    accepting = status.get("accepting_tasks", None) if isinstance(status, dict) else None
                    reasons.append(f"status not accepting (state={state}, chrome_ready={chrome_ready}, accepting={accepting})")
                try:
                    self._log(f"[DEBUG] Server {name} unavailable: {', '.join(reasons)}", "WARN", "ve3")
                except Exception:
                    pass
            pair = {
                "pair_id": name,
                "server_name": name,
                "server_url": url,
                "server_config": row,
                "flow_account_name": account_name,
                "flow_account": account,
                "enabled": enabled,
                "available": available,
                "queue_size": int(status.get("queue_size", 0) or 0),
            }
            if only_available and not available:
                continue
            pairs.append(pair)
        if _should_log_pairs:
            try:
                self._log(f"[DEBUG] Built {len(pairs)} pairs, {sum(1 for p in pairs if p['available'])} available", "INFO", "ve3")
            except Exception:
                pass
            self._server_pair_debug_last_ts = _now
        return pairs

    def _status_accepts_tasks(self, status):
        if not isinstance(status, dict):
            return False
        state = str(status.get("server_state", "") or "").strip().lower()
        if state in ("offline", "error", "failed", "stopped", "crashed"):
            return False
        chrome_ready = int(status.get("chrome_ready", 0) or 0)
        accepting_raw = status.get("accepting_tasks", None)
        if isinstance(accepting_raw, bool):
            accepting = accepting_raw
        elif accepting_raw is None:
            accepting = None
        else:
            accepting = str(accepting_raw).strip().lower() in ("1", "true", "yes", "on")
        if accepting is True:
            return True
        if state in ("ready", "busy", "recovering"):
            return True
        if chrome_ready > 0:
            return True
        return bool(accepting)

    def _server_status_entry(self, name, url, data=None, available=False):
        data = data or {}
        server_state = str(data.get("server_state", "unknown") or "unknown")
        chrome_ready = int(data.get("chrome_ready", 0) or 0)
        chrome_count = int(data.get("chrome_count", 0) or 0)
        queue_size = int(data.get("queue_size", 0) or 0)
        processing_count = int(data.get("processing_count", 0) or 0)
        accepting_tasks = bool(data.get("accepting_tasks", chrome_ready > 0))
        return {
            "name": name,
            "url": url,
            "available": bool(available),
            "accepting_tasks": accepting_tasks,
            "queue_size": queue_size,
            "processing_count": processing_count,
            "chrome_ready": chrome_ready,
            "chrome_count": chrome_count,
            "server_state": server_state,
        }

    def _load_project_pair_binding(self, project_dir):
        ep = self._project_excel_path(project_dir)
        if not ep.exists():
            return {}
        try:
            st = ep.stat()
            cache_key = str(ep)
            cache_sig = (float(st.st_mtime), int(st.st_size))
            cached = self._project_binding_cache.get(cache_key)
            if cached and cached.get("sig") == cache_sig:
                return dict(cached.get("data") or {})
        except Exception:
            cache_key = str(ep)
            cache_sig = None
        try:
            from modules.excel_manager import PromptWorkbook
            wb = PromptWorkbook(str(ep)); wb.load_or_create()
            data = {
                "flow_project_id": (wb.get_config_value("flow_project_id") or "").strip(),
                "flow_account_name": (wb.get_config_value("flow_account_name") or "").strip(),
                "bound_account_name": (wb.get_config_value("ve3_bound_account_name") or "").strip(),
                "bound_server_name": (wb.get_config_value("ve3_bound_server_name") or "").strip(),
                "bound_server_url": (wb.get_config_value("ve3_bound_server_url") or "").strip(),
            }
            if cache_sig is not None:
                self._project_binding_cache[cache_key] = {"sig": cache_sig, "data": dict(data)}
            return data
        except Exception:
            return {}

    def _save_project_pair_binding(self, project_dir, pair):
        ep = self._project_excel_path(project_dir)
        if not ep.exists():
            return
        try:
            from modules.excel_manager import PromptWorkbook
            wb = PromptWorkbook(str(ep)); wb.load_or_create()
            changed = False
            current_server_name = (wb.get_config_value("ve3_bound_server_name") or "").strip()
            current_server_url = (wb.get_config_value("ve3_bound_server_url") or "").strip()
            current_account_name = (wb.get_config_value("ve3_bound_account_name") or "").strip()
            current_flow_account = (wb.get_config_value("flow_account_name") or "").strip()
            if current_server_name != pair["server_name"]:
                wb.set_config_value("ve3_bound_server_name", pair["server_name"]); changed = True
            if current_server_url != pair["server_url"]:
                wb.set_config_value("ve3_bound_server_url", pair["server_url"]); changed = True
            if current_account_name != pair["flow_account_name"]:
                wb.set_config_value("ve3_bound_account_name", pair["flow_account_name"]); changed = True
            if not current_flow_account:
                wb.set_config_value("flow_account_name", pair["flow_account_name"]); changed = True
            if changed:
                wb.safe_save()
        except Exception as exc:
            self._log(f"[QUEUE] {project_dir.name}: khong ghi duoc binding server/account ({exc})", "WARN", "ve3")

    def _get_project_topic(self, project_dir):
        """Lay topic da normalize tu _CLAIMED file hoac Sheet NGUON."""
        meta = self._read_claimed_runtime_metadata(project_dir)
        raw = meta.get("raw_topic", "")
        if not raw:
            # Fallback 1: cached/project NGUON metadata, then Google Sheet if cache is absent.
            code = Path(project_dir).name
            raw = self._load_project_nguon_metadata(project_dir, code).get("topic", "")
        if not raw:
            # Fallback 2: infer from project code prefix (TL→psychology, KA→story)
            raw = self._infer_topic_from_code(Path(project_dir).name)
        if not raw:
            return ""
        normalized = self._normalize_project_topic(raw)
        topic_map = {"truyen": "story", "truyen ngan": "story",
                     "tam ly": "psychology",
                     "tai chinh": "finance",
                     "phat trien ban than": "success",
                     "psychology": "psychology", "finance": "finance", "success": "success", "story": "story"}
        return topic_map.get(normalized, normalized)

    def _filter_pairs_by_topic(self, pairs, project_topic):
        """Loc danh sach server pairs theo topic cua project.
        Server co allowed_topics trong = nhan tat ca.
        Server co allowed_topics set = chi nhan topic phu hop."""
        if not project_topic:
            return pairs
        topic_map = {"truyen": "story", "truyen ngan": "story",
                     "tam ly": "psychology",
                     "tai chinh": "finance",
                     "phat trien ban than": "success",
                     "psychology": "psychology", "finance": "finance", "success": "success", "story": "story"}
        filtered = []
        for p in pairs:
            allowed = str(p.get("server_config", {}).get("allowed_topics", "") or "").strip()
            if not allowed:
                filtered.append(p)  # Trong = nhan tat ca
                continue
            allowed_list = [self._normalize_project_topic(t.strip()) for t in allowed.split(",") if t.strip()]
            allowed_normalized = [topic_map.get(t, t) for t in allowed_list]
            if project_topic in allowed_normalized:
                filtered.append(p)
        return filtered

    def _choose_pair_for_project(self, project_dir, free_pairs):
        # === Filter pairs by project topic ===
        project_topic = self._get_project_topic(project_dir)
        if project_topic:
            topic_filtered = self._filter_pairs_by_topic(free_pairs, project_topic)
            if not topic_filtered and free_pairs:
                self._log(f"[QUEUE/VE3] {project_dir.name}: topic={project_topic} has no matching server (allowed_topics). Skip.", "WARN", "ve3")
                return None
            free_pairs = topic_filtered

        def _best_pair(pairs):
            if not pairs:
                return None
            return sorted(pairs, key=lambda p: (p["queue_size"], self.queue_pair_last_used.get(p["pair_id"], 0), p["server_name"]))[0]

        binding = self._load_project_pair_binding(project_dir)
        by_server = {p["server_name"]: p for p in free_pairs}
        by_account = {}
        for p in free_pairs:
            by_account.setdefault(p["flow_account_name"], []).append(p)

        bound_server = binding.get("bound_server_name", "")
        bound_account = binding.get("bound_account_name", "") or binding.get("flow_account_name", "")
        flow_project_id = binding.get("flow_project_id", "")

        if bound_server:
            pair = by_server.get(bound_server)
            if pair and (not bound_account or pair["flow_account_name"] == bound_account):
                return pair
            all_pairs = self._get_server_pairs(only_available=False)
            bound_pair = next((p for p in all_pairs if p["server_name"] == bound_server), None)
            if bound_pair:
                self._log(f"[QUEUE/VE3] {project_dir.name}: bound server {bound_server}/{bound_account or '?'} is not ready. Waiting (will not reassign).", "WARN", "ve3")
            else:
                self._log(f"[QUEUE/VE3] {project_dir.name}: bound server {bound_server}/{bound_account or '?'} is missing from config. Waiting (will not reassign).", "WARN", "ve3")
            return None

        if bound_account:
            candidates = by_account.get(bound_account, [])
            if candidates:
                return _best_pair(candidates)
            self._log(f"[QUEUE/VE3] {project_dir.name}: bound account {bound_account} has no ready server. Waiting (will not reassign).", "WARN", "ve3")
            return None

        if flow_project_id and len(free_pairs) > 1:
            pair = _best_pair(free_pairs)
            if pair:
                self._log(f"[QUEUE/VE3] {project_dir.name}: project_id exists but binding is missing; auto-selected stable pair {pair['server_name']}/{pair['flow_account_name']}", "WARN", "ve3")
                return pair

        return _best_pair(free_pairs)

    def _build_project_pair_cfg(self, base_cfg, pair):
        cfg = dict(base_cfg)
        server_cfg = dict(pair["server_config"])
        server_cfg["flow_account_name"] = pair["flow_account_name"]
        cfg["local_server_list"] = [server_cfg]
        cfg["local_server_url"] = pair["server_url"]
        cfg["flow_auth_default_account"] = pair["flow_account_name"]
        # Queue mode must not inherit stale global Flow auth/project from settings.
        # Each project should use its own workbook-bound project/token, or create a new one.
        cfg["flow_bearer_token"] = ""
        cfg["flow_project_id"] = ""
        cfg["flow_project_url"] = ""
        if pair.get("flow_account"):
            cfg["flow_accounts"] = [dict(pair["flow_account"])]
        return cfg

    def test_all_servers(self):
        svs = self._get_svs()
        if not svs: return
        def _t():
            import requests; res = []
            for s in svs:
                u = s["url"].rstrip("/"); nm = s.get("name",u)
                try:
                    r = requests.get(f"{u}/api/status", timeout=8)
                    if r.status_code==200:
                        d = r.json()
                        res.append(self._server_status_entry(nm, u, d, available=self._status_accepts_tasks(d)))
                    else:
                        try:
                            ping = requests.get(f"{u}/api/ping", timeout=1.5)
                            if ping.status_code == 200:
                                res.append(self._server_status_entry(nm, u, {"server_state": "ping_alive", "accepting_tasks": True, "chrome_ready": 1, "chrome_count": 1}, available=True))
                            else:
                                res.append(self._server_status_entry(nm, u, available=False))
                        except Exception:
                            res.append(self._server_status_entry(nm, u, available=False))
                except Exception:
                    try:
                        ping = requests.get(f"{u}/api/ping", timeout=1.5)
                        if ping.status_code == 200:
                            res.append(self._server_status_entry(nm, u, {"server_state": "ping_alive", "accepting_tasks": True, "chrome_ready": 1, "chrome_count": 1}, available=True))
                        else:
                            res.append(self._server_status_entry(nm, u, available=False))
                    except Exception:
                        res.append(self._server_status_entry(nm, u, available=False))
            self.server_status_cache = res
            self.server_status_cache_ts = _time.time()
            self.after(0, lambda: self.pages["home"].update_server_status(res))
            self.after(0, lambda: self.pages["cfg"].update_server_status(res))
            ok = sum(1 for r in res if r["available"])
            self.after(0, lambda: self._log(f"Servers: {ok}/{len(res)} online", "SUCCESS" if ok else "WARN"))
        threading.Thread(target=_t, daemon=True).start()

    def _refresh_server_status_sync(self):
        svs = self._get_svs()
        if not svs:
            return []
        import requests
        from concurrent.futures import ThreadPoolExecutor, as_completed

        def _check_server(s):
            u = str(s.get("url", "") or "").rstrip("/")
            nm = s.get("name", u)
            if not u:
                return None
            try:
                r = requests.get(f"{u}/api/status", timeout=5)
                if r.status_code == 200:
                    d = r.json()
                    return self._server_status_entry(nm, u, d, available=self._status_accepts_tasks(d))
                return self._server_status_entry(nm, u, available=False)
            except Exception:
                try:
                    ping = requests.get(f"{u}/api/ping", timeout=1.5)
                    if ping.status_code == 200:
                        return self._server_status_entry(nm, u, {"server_state": "ping_alive", "accepting_tasks": True, "chrome_ready": 1, "chrome_count": 1}, available=True)
                except Exception:
                    pass
                return self._server_status_entry(nm, u, available=False)

        res = []
        max_workers = max(1, min(8, len(svs)))
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = [executor.submit(_check_server, s) for s in svs]
            try:
                for future in as_completed(futures, timeout=15):
                    try:
                        item = future.result()
                        if item:
                            res.append(item)
                    except Exception:
                        pass
            except Exception:
                # Timeout or other error - collect whatever completed
                for future in futures:
                    if future.done():
                        try:
                            item = future.result(timeout=0)
                            if item:
                                res.append(item)
                        except Exception:
                            pass

        seen_urls = {str(r.get("url", "") or "").rstrip("/") for r in res}
        for s in svs:
            u = str(s.get("url", "") or "").rstrip("/")
            if u and u not in seen_urls:
                res.append(self._server_status_entry(s.get("name", u), u, available=False))

        self.server_status_cache = res
        self.server_status_cache_ts = _time.time()
        self.after(0, lambda: self.pages["home"].update_server_status(res))
        self.after(0, lambda: self.pages["cfg"].update_server_status(res))
        return res

    #  file 
    def _run_excel_engine_subprocess(self, project_dir, mode="srt-excel-only", log_cb=None):
        """Run MP3/SRT -> Excel with the bundled srt-to-excel engine in a separate process."""
        if not HEADLESS_RUNNER.exists():
            raise FileNotFoundError(f"Khong tim thay {HEADLESS_RUNNER}")
        project_dir = Path(project_dir)
        if not project_dir.exists() or not project_dir.is_dir():
            raise FileNotFoundError(f"Project khong con trong PROJECTS: {project_dir}")
        if self._is_project_endpoint_complete(project_dir):
            raise RuntimeError(f"Project da endpoint, bo qua Excel subprocess: {project_dir.name}")
        runtime_cfg_path = project_dir / ".excel_runtime_config.yaml"
        try:
            import yaml
            with open(runtime_cfg_path, "w", encoding="utf-8") as f:
                yaml.dump(self._build_excel_runtime_config(project_dir), f, default_flow_style=False, allow_unicode=True, sort_keys=False)
        except Exception as e:
            raise RuntimeError(f"Khong ghi duoc runtime Excel config: {e}")
        cmd = [sys.executable, str(HEADLESS_RUNNER), "--config", str(runtime_cfg_path), f"--{mode}", str(project_dir)]
        proc = subprocess.Popen(
            cmd,
            cwd=str(SUITE_ROOT),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
        )
        self._track_process(proc, f"excel:{Path(project_dir).name}:{mode}")
        if log_cb:
            log_cb(f"[EXCEL] Spawn headless pid={proc.pid} mode={mode}", "INFO")
        assert proc.stdout is not None
        try:
            for line in proc.stdout:
                if self.queue_stop_requested:
                    proc.terminate()
                    break
                if log_cb:
                    log_cb(line.rstrip(), "INFO")
            code = proc.wait()
            if code != 0 and not self.queue_stop_requested:
                raise RuntimeError(f"Excel engine failed with exit code {code}")
        finally:
            self._untrack_process(proc)

    def upload_excel(self):
        p = filedialog.askopenfilename(title="Excel", filetypes=[("Excel","*.xlsx"),("All","*.*")])
        if p: self._load_excel(Path(p))

    def create_from_mp3(self):
        """Pipeline day du: MP3 -> SRT (Whisper) -> Excel (provider da chon) -> load vao GUI."""
        p = filedialog.askopenfilename(
            title="Chon file MP3/am thanh",
            filetypes=[("Audio", "*.mp3 *.wav *.m4a *.flac *.ogg *.aac"), ("All", "*.*")])
        if not p: return

        ok_ai, ai_msg = self._validate_excel_ai_config()
        if not ok_ai:
            messagebox.showwarning("Thieu cau hinh", ai_msg); return

        sp = Path(p)
        code = sp.stem
        pd = PROJECTS_DIR / code
        pd.mkdir(parents=True, exist_ok=True)

        dest_mp3 = pd / sp.name
        if str(sp) != str(dest_mp3): shutil.copy2(str(sp), str(dest_mp3))

        srt_path = pd / f"{code}.srt"
        ep = pd / f"{code}_prompts.xlsx"

        whisper_model = self.config_data.get("whisper_model", "large-v3")
        whisper_lang  = self.config_data.get("whisper_language", "auto")

        self._log(f"[MP3>>] Pipeline bat dau: {sp.name}")
        self._log(f"[MP3>>] Project: PROJECTS/{code}/")

        def _log_cb(msg, level="INFO"):
            self.after(0, lambda m=msg, l=level: self._log(f"  {m}", l))

        def _pipeline():
            try:
                self.after(0, lambda: self._log("[1/2] MP3/SRT -> Excel bang engine srt-to-excel moi..."))
                self._run_excel_engine_subprocess(pd, mode="srt-excel-only", log_cb=_log_cb)
                if not ep.exists():
                    self.after(0, lambda: self._log(f"Excel chua duoc tao: {ep}", "ERROR"))
                    return
                self.after(0, lambda: self._load_excel(ep))
                self.after(0, lambda: self._log("[2/2] Excel da load! Bam CHAY de tao anh + video.", "SUCCESS"))
                return

                #  Buoc 1: MP3 -> SRT via Whisper 
                self.after(0, lambda: self._log("[1/3] MP3 -> SRT (Whisper)..."))
                try:
                    sys.path.insert(0, str(VE3_DIR))
                    from mp3_to_srt import segments_to_srt, install_if_missing
                    install_if_missing("openai-whisper", "whisper")
                    install_if_missing("torch")
                    import whisper as _whisper
                except Exception as e:
                    self.after(0, lambda: self._log(f"  Cai Whisper that bai: {e}", "ERROR"))
                    return

                self.after(0, lambda: self._log(f"  Load model Whisper: {whisper_model}"))
                model = _whisper.load_model(whisper_model)

                opts = {"task": "transcribe", "verbose": False, "word_timestamps": True}
                if whisper_lang and whisper_lang.lower() not in ("auto", ""):
                    opts["language"] = whisper_lang

                self.after(0, lambda: self._log(f"  Transcribing {sp.name}..."))
                result = model.transcribe(str(dest_mp3), **opts)
                segments = result.get("segments", [])
                detected = result.get("language", "?")
                self.after(0, lambda: self._log(f"  Language: {detected}, {len(segments)} segments"))

                srt_content = segments_to_srt(segments)
                srt_path.write_text(srt_content, encoding="utf-8")
                cue_count = srt_content.count("\n\n") + 1
                self.after(0, lambda: self._log(f"  SRT: {cue_count} cau -> {srt_path.name}", "SUCCESS"))

                provider_name = self._resolve_excel_ai_provider().replace("_", " ").title()
                self.after(0, lambda p=provider_name: self._log(f"[2/3] SRT -> Excel ({p})..."))
                self._run_excel_engine_subprocess(pd, mode="excel-only", log_cb=_log_cb)
                ok = ep.exists()

                if not ok:
                    self.after(0, lambda: self._log("[2/3] Excel co b uoc that bai  kiem tra log", "WARN"))
                    return
                if not ep.exists():
                    self.after(0, lambda: self._log(f"[2/3] Excel chua duoc tao: {ep}", "ERROR"))
                    return

                self.after(0, lambda: self._log(f"  Excel: {ep.name}", "SUCCESS"))

                #  Buoc 3: Load vao GUI, san sang chay worker 
                self.after(0, lambda: self._load_excel(ep))
                self.after(0, lambda: self._log("[3/3] Excel da load! Bam >> CHAY de tao anh + video.", "SUCCESS"))

            except Exception as e:
                import traceback
                self.after(0, lambda: self._log(f"Pipeline error: {e}", "ERROR"))
                _log_cb(traceback.format_exc(), "ERROR")

        threading.Thread(target=_pipeline, daemon=True).start()

    def create_from_srt(self):

        p = filedialog.askopenfilename(title="SRT", filetypes=[("SRT","*.srt"),("All","*.*")])
        if not p: return
        ok_ai, ai_msg = self._validate_excel_ai_config()
        if not ok_ai:
            messagebox.showwarning("Thieu cau hinh", ai_msg); return
        sp = Path(p); code = sp.stem
        pd = PROJECTS_DIR/code; pd.mkdir(parents=True, exist_ok=True)
        dest = pd/sp.name
        if str(sp) != str(dest): shutil.copy2(str(sp), str(dest))
        self._log(f"SRT  Excel: {sp.name} (code={code})")
        ep = pd/f"{code}_prompts.xlsx"

        def _log_cb(msg, level="INFO"):
            self.after(0, lambda m=msg, l=level: self._log(m, l))

        def _r():
            try:
                self._run_excel_engine_subprocess(pd, mode="excel-only", log_cb=_log_cb)
                ok = ep.exists()
                if ok and ep.exists():
                    self.after(0, lambda: self._load_excel(ep))
                elif not ok:
                    self.after(0, lambda: self._log("SRT  Excel: mt s step tht bi, kim tra log", "WARN"))
                else:
                    self.after(0, lambda: self._log(f"Excel cha c to: {ep}", "ERROR"))
            except Exception as e:
                import traceback
                self.after(0, lambda: self._log(f"SRT Error: {e}", "ERROR"))
                _log_cb(traceback.format_exc(), "ERROR")
        threading.Thread(target=_r, daemon=True).start()


    def download_template(self):
        src = VE3_DIR/"templates"/"template.xlsx"
        if not src.exists():
            from create_template import create_template; create_template(str(src))
        d = filedialog.asksaveasfilename(title="Save",defaultextension=".xlsx",initialfile="template.xlsx",
                                          filetypes=[("Excel","*.xlsx")])
        if d: shutil.copy2(str(src),d); messagebox.showinfo("OK",f"Saved: {d}")

    def _load_excel(self, path):
        try:
            from modules.excel_manager import PromptWorkbook
            code = path.stem.replace("_prompts","")
            pd = PROJECTS_DIR/code; pd.mkdir(parents=True,exist_ok=True)
            dest = pd/path.name
            if str(path.resolve())!=str(dest.resolve()): shutil.copy2(str(path),str(dest))
            wb = PromptWorkbook(str(dest)); wb.load_or_create()
            self.wb = wb; self.excel_path = dest; self.project_dir = pd
            nv = pd/"nv"; img = pd/"img"; nv.mkdir(exist_ok=True); img.mkdir(exist_ok=True)
            self.pages["home"].fill_from_excel(wb)

            chars = wb.get_characters()
            cd = [c.to_dict() if hasattr(c,'to_dict') else {"id":c.id,"name":c.name,"role":c.role,
                  "english_prompt":c.english_prompt,"vietnamese_prompt":getattr(c,'vietnamese_prompt',''),
                  "status":c.status,"is_child":c.is_child,"media_id":getattr(c,'media_id','')} for c in chars]
            scenes = wb.get_scenes()
            sd = [{"scene_id":s.scene_id,"srt_text":getattr(s,'srt_text',''),"img_prompt":s.img_prompt,
                   "video_prompt":getattr(s,'video_prompt','') or '',
                   "characters_used":getattr(s,'characters_used',''),"location_used":getattr(s,'location_used',''),
                   "reference_files":getattr(s,'reference_files',''),
                   "status_img":getattr(s,'status_img',''),"status_vid":getattr(s,'status_vid','')} for s in scenes]

            self.pages["gen"].load_chars(cd, nv)
            self.pages["gen"].load_scenes(sd, img)
            nc = len(cd); ns = len([s for s in sd if s.get("img_prompt")])
            self.pages["home"].lbl_queue_summary.configure(text=f"{path.name}    {nc} chars    {ns} scenes", text_color=T1)
            self._log(f"Loaded {path.name}  {nc} chars, {ns} scenes","SUCCESS")
        except Exception as e:
            self._log(f"Excel error: {e}","ERROR"); messagebox.showerror("Li",str(e))

    #  save 
    def save_characters(self):
        if not self.wb: return
        for cid, c in self.pages["gen"].cc.items(): self.wb.update_character(cid, english_prompt=c.get_prompt())
        self.pages["home"].sync_to_excel(self.wb); self.wb.safe_save()
        self._log(f"Saved {len(self.pages['gen'].cc)} characters","SUCCESS")

    def save_scenes(self):
        if not self.wb: return
        for sid, c in self.pages["gen"].sc.items():
            self.wb.update_scene(sid, img_prompt=c.get_prompt(), video_prompt=c.get_video_prompt())
        self.pages["home"].sync_to_excel(self.wb); self.wb.safe_save()
        self._log(f"Saved {len(self.pages['gen'].sc)} scenes","SUCCESS")

    def view_image(self, p, t=""):
        if p and Path(p).exists(): ImageViewer(self, p, t)

    #  token 
    def _build_cfg(self):
        c = dict(self.config_data)
        t = str(c.get("flow_bearer_token", "") or "").strip()
        accounts = c.get("flow_accounts", []) or []
        pair_accounts = [self._pair_account_from_row(row, idx) for idx, row in enumerate(c.get("local_server_list", []) or [])]
        all_accounts = [a for a in accounts if isinstance(a, dict)] + [a for a in pair_accounts if a]
        auto_auth_ready = bool(
            c.get("flow_auth_auto_enabled", True)
            and any(
                (a or {}).get("enabled", True)
                and (a or {}).get("email")
                and (a or {}).get("password")
                and (a or {}).get("chrome_path")
                and (a or {}).get("profile_dir")
                for a in all_accounts
            )
        )
        if not t and not auto_auth_ready:
            messagebox.showwarning("Flow Auth", "Can token hop le hoac it nhat 1 pair co du gmail bundle va chrome path trong Cai dat.")
            return None
        if t and not t.startswith("ya29."):
            messagebox.showwarning("Token","Token phi bt u ya29."); return None
        c["flow_bearer_token"] = t
        c["flow_project_id"] = str(c.get("flow_project_id", "") or "").strip()
        c["flow_project_url"] = c.get("flow_project_url", "") or ""
        if self.project_dir:
            c.update(self._project_topic_runtime_config(Path(self.project_dir), c))
        # Load concurrent setting
        try: c["max_concurrent"] = max(1, int(self.pages["cfg"].ent_conc.get().strip() or "1"))
        except: c["max_concurrent"] = 1
        return c

    #  regen 
    def regen_character(self, cid, prompt):
        if not self.project_dir or not self.wb: messagebox.showwarning("Li","Cha c project!"); return
        if not prompt: messagebox.showwarning("Li","Prompt trng!"); return
        cfg = self._build_cfg()
        if not cfg: return
        self.wb.update_character(cid, english_prompt=prompt, reference_media_checked=False); self.wb.safe_save()
        self.pages["gen"].update_char(cid, "running"); self._log(f"Regen {cid}...")
        ip = self.project_dir/"nv"/f"{cid}.png"
        def _r():
            from ve3_worker import VE3Worker
            try:
                w = VE3Worker(project_dir=str(self.project_dir), config=cfg,
                              log_func=lambda m,l="INFO": self.after(0, lambda: self._log(m,l)))
                t0 = _time.time(); ok, med, si = w._submit_image(prompt, ip)
                el = round(_time.time()-t0,1); ex = {"elapsed":el, **si}
                if ok:
                    self.wb.update_character(cid, status="done", media_id=med or "", reference_media_checked=False); self.wb.safe_save()
                    self.after(0, lambda: self._reload_wb())
                    self.after(0, lambda: self.pages["gen"].update_char(cid, "done", ex))
                    self.after(0, lambda: self._log(f"{cid} done ({el}s)","SUCCESS"))
                else:
                    self.after(0, lambda: self.pages["gen"].update_char(cid, "error", ex))
                    self.after(0, lambda: self._log(f"{cid} failed","ERROR"))
            except Exception as e:
                self.after(0, lambda: self.pages["gen"].update_char(cid, "error"))
                self.after(0, lambda: self._log(f"Error: {e}","ERROR"))
        threading.Thread(target=_r, daemon=True).start()

    def regen_scene(self, sid, prompt):
        if not self.project_dir or not self.wb: messagebox.showwarning("Li","Cha c project!"); return
        if not prompt: messagebox.showwarning("Li","Prompt trng!"); return
        cfg = self._build_cfg()
        if not cfg: return
        self.wb.update_scene(sid, img_prompt=prompt); self.wb.safe_save()
        self.pages["gen"].update_scene(sid, "running"); self._log(f"Regen scene {sid}...")
        ip = self.project_dir/"img"/f"{sid}.png"
        def _r():
            from ve3_worker import VE3Worker
            try:
                w = VE3Worker(project_dir=str(self.project_dir), config=cfg,
                              log_func=lambda m,l="INFO": self.after(0, lambda: self._log(m,l)))
                mids = w._load_media_ids(self.wb); scenes = self.wb.get_scenes()
                child_ids = {c.id for c in self.wb.get_characters() if getattr(c, "is_child", False)}
                so = next((s for s in scenes if s.scene_id==sid), None)
                refs = []
                missing_refs = []
                if so:
                    refs, expected_refs, missing_refs = w._build_references(
                        so, mids, with_details=True, ignored_ids=child_ids
                    )
                    if expected_refs and missing_refs:
                        raise RuntimeError(f"Scene {sid} thieu references: {', ' .join(missing_refs[:6])}")
                t0 = _time.time(); ok, med, si = w._submit_image(prompt, ip, refs)
                el = round(_time.time()-t0,1); ex = {"elapsed":el, **si}
                if ok:
                    self.wb.update_scene(sid, status_img="done", media_id=med or ""); self.wb.safe_save()
                    self.after(0, lambda: self._reload_wb())
                    self.after(0, lambda: self.pages["gen"].update_scene(sid, "done", ex))
                    self.after(0, lambda: self._log(f"Scene {sid} done ({el}s)","SUCCESS"))
                else:
                    self.wb.update_scene(sid, status_img="error"); self.wb.safe_save()
                    self.after(0, lambda: self.pages["gen"].update_scene(sid, "error", ex))
                    self.after(0, lambda: self._log(f"Scene {sid} failed","ERROR"))
            except Exception as e:
                self.after(0, lambda: self.pages["gen"].update_scene(sid, "error"))
                self.after(0, lambda: self._log(f"Error: {e}","ERROR"))
        threading.Thread(target=_r, daemon=True).start()

    def _reload_wb(self):
        """Reload workbook t file  ly data mi nht (media_id, status...)."""
        if self.excel_path and self.excel_path.exists():
            from modules.excel_manager import PromptWorkbook
            self.wb = PromptWorkbook(str(self.excel_path))
            self.wb.load_or_create()

    def _music_columns(self):
        return [
            "music_id", "start_time", "duration", "title", "suno_prompt",
            "style_tags", "mood", "scene_range", "suno_url", "status",
        ]

    def _get_music_tracks_compat(self, wb):
        if hasattr(wb, "get_music_tracks"):
            return wb.get_music_tracks()

        sheet_name = getattr(wb, "MUSIC_SHEET", "music")
        workbook = getattr(wb, "workbook", None)
        if not workbook or sheet_name not in workbook.sheetnames:
            return []

        ws = workbook[sheet_name]
        headers = [cell.value for cell in ws[1]]
        columns = self._music_columns()
        header_map = {str(h): i for i, h in enumerate(headers) if h}
        if not header_map:
            return []

        tracks = []
        for row in ws.iter_rows(min_row=2, values_only=True):
            music_id_idx = header_map.get("music_id")
            if music_id_idx is None or music_id_idx >= len(row) or row[music_id_idx] is None:
                continue
            track = {}
            for name in columns:
                idx = header_map.get(name)
                val = row[idx] if idx is not None and idx < len(row) else None
                track[name] = str(val) if val is not None else ""
            tracks.append(track)
        return tracks

    def _update_music_track_compat(self, wb, music_id, **kwargs):
        if hasattr(wb, "update_music_track"):
            return wb.update_music_track(music_id, **kwargs)

        sheet_name = getattr(wb, "MUSIC_SHEET", "music")
        workbook = getattr(wb, "workbook", None)
        if not workbook or sheet_name not in workbook.sheetnames:
            return False

        ws = workbook[sheet_name]
        headers = [cell.value for cell in ws[1]]
        header_map = {str(h): i + 1 for i, h in enumerate(headers) if h}
        if "music_id" not in header_map:
            return False

        for row_idx in range(2, ws.max_row + 1):
            cell_val = ws.cell(row=row_idx, column=header_map["music_id"]).value
            if cell_val is not None and str(cell_val) == str(music_id):
                for key, value in kwargs.items():
                    col_idx = header_map.get(key)
                    if col_idx:
                        ws.cell(row=row_idx, column=col_idx, value=value)
                if hasattr(wb, "safe_save"):
                    wb.safe_save()
                elif hasattr(wb, "save"):
                    wb.save()
                return True
        return False

    def regen_video(self, sid, video_prompt):
        """To li video cho 1 scene (Image-to-Video)."""
        if not self.project_dir or not self.wb:
            messagebox.showwarning("Li","Cha c project!"); return
        if not video_prompt:
            messagebox.showwarning("Li","Video prompt trng!"); return
        cfg = self._build_cfg()
        if not cfg: return

        # Reload workbook  ly media_id mi nht
        self._reload_wb()

        # Ly media_id ca nh scene
        scenes = self.wb.get_scenes()
        scene_obj = next((s for s in scenes if s.scene_id == sid), None)
        if not scene_obj:
            messagebox.showwarning("Li", f"Khng tm thy scene {sid}"); return
        media_id = getattr(scene_obj, 'media_id', '') or ''
        if not media_id:
            messagebox.showwarning("Li",
                f"Scene {sid} cha c media_id.\n"
                "Cn to nh trc (bm 'To nh') ri mi to video c."); return

        self.wb.update_scene(sid, video_prompt=video_prompt); self.wb.safe_save()
        self.pages["gen"].update_scene(sid, "running", {"phase": "video"})
        self._log(f"To video scene {sid}...")

        vid_path = self.project_dir / "vid" / f"{sid}.mp4"

        def _r():
            from ve3_worker import VE3Worker
            try:
                w = VE3Worker(project_dir=str(self.project_dir), config=cfg,
                              log_func=lambda m,l="INFO": self.after(0, lambda: self._log(m,l)))
                t0 = _time.time()
                ok, si = w._submit_video(video_prompt, vid_path, media_id)
                el = round(_time.time()-t0, 1)
                ex = {"elapsed": el, "phase": "video", **si}
                if ok:
                    self.wb.update_scene(sid, status_vid="done", video_path=str(vid_path))
                    self.wb.safe_save()
                    self.after(0, lambda: self.pages["gen"].update_scene(sid, "done", ex))
                    self.after(0, lambda: self._log(f"Video scene {sid} xong ({el}s)","SUCCESS"))
                else:
                    self.wb.update_scene(sid, status_vid="error"); self.wb.safe_save()
                    self.after(0, lambda: self.pages["gen"].update_scene(sid, "error", ex))
                    self.after(0, lambda: self._log(f"Video scene {sid} li","ERROR"))
            except Exception as e:
                self.after(0, lambda: self.pages["gen"].update_scene(sid, "error", {"phase":"video"}))
                self.after(0, lambda: self._log(f"Li: {e}","ERROR"))
        threading.Thread(target=_r, daemon=True).start()

    #  full auto-pipeline 
    def start_worker(self):
        """1 nut bam: chon file (neu chua co) -> SRT -> Excel -> Anh -> Video."""
        # Neu chua co project: tu dong mo dialog chon file
        if not self.project_dir:
            p = filedialog.askopenfilename(
                title="Chn mt file trong m cn chy th cng (khng cn nu dng AUTO QUEUE)",
                filetypes=[
                    ("MP3 / Audio", "*.mp3 *.wav *.m4a *.flac *.ogg *.aac"),
                    ("Excel", "*.xlsx"),
                    ("Tat ca", "*.*"),
                ])
            if not p: return
            fp = Path(p)
            if fp.suffix.lower() == ".xlsx":
                # Tai Excel truc tiep
                self._load_excel(fp)
                if not self.project_dir: return
            else:
                # MP3 / audio: tao project folder
                code = fp.stem
                pd_new = PROJECTS_DIR / code
                pd_new.mkdir(parents=True, exist_ok=True)
                dest = pd_new / fp.name
                if str(fp) != str(dest): shutil.copy2(str(fp), str(dest))
                self.project_dir = pd_new
                self._log(f"Project: PROJECTS/{code}/")


        cfg = self._build_cfg()
        if not cfg: return
        if not cfg.get("local_server_url") and not cfg.get("local_server_list"):
            messagebox.showwarning("Thieu server", "Them server trong Cai dat truoc!"); return

        self.config_data.update({
            "flow_bearer_token": cfg["flow_bearer_token"],
            "flow_project_id":   cfg["flow_project_id"]})
        self._save_config()

        h = self.pages["home"]
        h.pb_refs.set(0); h.pb_scenes.set(0); h.pb_vids.set(0); h.pb_music.set(0)
        h.lbl_refs.configure(text="0/0"); h.lbl_scenes.configure(text="0/0")
        h.lbl_vids.configure(text="0/0"); h.lbl_music.configure(text="0/0")
        h.lbl_active_project.configure(text="Ma dang chay: -")
        h.lbl_cur.configure(text="")
        for box in (h.log_excel_box, h.log_ve3_box):
            box.configure(state="normal"); box.delete("1.0","end"); box.configure(state="disabled")

        self.btn_go.configure(state="disabled", fg_color="#555", text_color="#999")
        self.btn_st.configure(state="normal", fg_color="#D32F2F", text_color="#FFFFFF")
        self._t0 = _time.time(); self._tick()

        pd = self.project_dir
        code = pd.name
        srt_path = pd / f"{code}.srt"
        ep = pd / f"{code}_prompts.xlsx"
        # Tim mp3 bat ky trong project folder
        mp3_files = list(pd.glob("*.mp3")) + list(pd.glob("*.wav")) + \
                    list(pd.glob("*.m4a")) + list(pd.glob("*.flac"))

        whisper_model = self.config_data.get("whisper_model", "large-v3")
        whisper_lang  = self.config_data.get("whisper_language", "auto")

        def _log(msg, level="INFO"):
            self.after(0, lambda m=msg, l=level: self._log(m, l))

        def _auto_pipeline():
            try:
                if not ep.exists():
                    _log("[1/3] MP3/SRT -> Excel bang engine srt-to-excel moi...")
                    self._run_excel_engine_subprocess(
                        pd,
                        mode="srt-excel-only",
                        log_cb=lambda m,l="INFO": _log(f"  {m}", l),
                    )
                    if ep.exists():
                        self.after(0, lambda: self._load_excel(ep))
                        _time.sleep(1.5)

                #  Buoc 1: MP3 -> SRT (neu chua co) 
                if srt_path.exists():
                    _log(f"[1/3] SRT da co: {srt_path.name} -- skip")
                elif mp3_files:
                    mp3 = mp3_files[0]
                    _log(f"[1/3] MP3 -> SRT: {mp3.name}")
                    try:
                        srt_tool_dir = SUITE_ROOT / "tools" / "srt-to-excel"
                        if str(srt_tool_dir) not in sys.path:
                            sys.path.insert(0, str(srt_tool_dir))
                        from modules.voice_to_srt import VoiceToSrt
                        _lang = None if not whisper_lang or whisper_lang.lower() in ("auto", "") else whisper_lang
                        _log(f"  VoiceToSrt model: {whisper_model} | language: {_lang or 'auto'}")
                        converter = VoiceToSrt(model_name=whisper_model, language=_lang)
                        result = converter.transcribe(mp3, srt_path)
                        cues = len(result.get("segments", []) or [])
                        detected = result.get("language", "?")
                        _log(f"  Detected language: {detected}", "INFO")
                        _log(f"  SRT -> {srt_path.name} ({cues} segments)", "SUCCESS")
                    except Exception as e:
                        _log(f"  Whisper error: {e}", "ERROR"); return
                else:
                    _log("[1/3] Khong co MP3 va SRT -- bo qua buoc Whisper", "WARN")

                #  Buoc 2: SRT -> Excel (neu chua co) 
                if self._project_excel_complete(pd):
                    _log(f"[2/3] Excel usable: {ep.name} -- skip")
                    # Van load lai de dam bao GUI co data
                    if not self.excel_path or str(self.excel_path) != str(ep):
                        self.after(0, lambda: self._load_excel(ep))
                        _time.sleep(1.5)  # Cho load xong
                elif srt_path.exists():
                    if ep.exists():
                        _log(f"[2/3] Excel co san nhung chua usable: {ep.name} -- tao tiep")
                    provider_name = self._resolve_excel_ai_provider().replace("_", " ").title()
                    _log(f"[2/3] SRT -> Excel ({provider_name})...")
                    ok_ai, ai_msg = self._validate_excel_ai_config()
                    if not ok_ai:
                        _log(f"  {ai_msg} Bo qua buoc tao Excel.", "WARN")
                    else:
                        try:
                            self._run_excel_engine_subprocess(
                                pd,
                                mode="excel-only",
                                log_cb=lambda m,l="INFO": _log(f"  {m}", l),
                            )
                            ok = ep.exists()
                            if ok and ep.exists():
                                self.after(0, lambda: self._load_excel(ep))
                                _log(f"  Excel: {ep.name}", "SUCCESS")
                                _time.sleep(1.5)
                            else:
                                _log("  Excel generation that bai!", "ERROR"); return
                        except Exception as e:
                            _log(f"  Excel AI error: {e}", "ERROR"); return
                else:
                    _log("[2/3] Khong co SRT -- bo qua tao Excel", "WARN")

                #  Buoc 3: Chay worker tao anh + video 
                if not self.excel_path or not self.excel_path.exists():
                    _log("[3/3] Chua co Excel -- khong the tao anh!", "ERROR"); return

                _log("[3/3] Bat dau tao anh + video...")
                if self.wb:
                    self.save_characters(); self.save_scenes()

                from ve3_worker import VE3Worker
                self.worker = VE3Worker(
                    project_dir=str(self.project_dir), config=cfg,
                    log_func=lambda m,l="INFO": self.after(0, lambda: self._log(m,l)),
                    progress_func=lambda *a,**kw: self.after(0, lambda: self._prog(*a,**kw)),
                    on_item_status=lambda *a,**kw: self.after(0, lambda: self._item(*a,**kw)))
                res = self.worker.run()
                self.after(0, lambda: self._done(res))

            except Exception as e:
                import traceback
                _log(f"Pipeline error: {e}", "ERROR")
                _log(traceback.format_exc(), "ERROR")
                self.after(0, lambda: self._done({"success": False, "completed": 0,
                                                   "total": 0, "errors": [str(e)]}))

        self.worker_thread = threading.Thread(target=_auto_pipeline, daemon=True)
        self.worker_thread.start()
        self.start_music_worker()
        self._log("Pipeline bat dau!"); self.show("home")

    def start_music_worker(self):
        if self.music_thread and self.music_thread.is_alive():
            self._log("Job tao nhac dang chay", "WARN"); return
        if not self.excel_path or not self.excel_path.exists() or not self.project_dir:
            messagebox.showwarning("Loi", "Can load project Excel truoc!"); return
        if not SUNO_DIR.exists():
            messagebox.showwarning("Loi", f"Khong tim thay thu muc Suno: {SUNO_DIR}"); return

        self._reload_wb()
        music_dir = self.project_dir / "music"
        music_dir.mkdir(parents=True, exist_ok=True)
        self.music_stop_requested = False

        if self.wb:
            self.pages["home"].sync_to_excel(self.wb)
            self.wb.safe_save()

        excel_path = Path(self.excel_path)
        project_dir = Path(self.project_dir)
        if not self._music_has_pending(excel_path, project_dir):
            self._log(f"[MUSIC] {project_dir.name}: da du mp3, bo qua mo browser", "INFO", "ve3")
            return
        self.music_thread = threading.Thread(
            target=lambda: self._run_music_for_project(project_dir, excel_path, update_ui=True),
            daemon=True,
        )
        self.music_thread.start()
        self.show("home")

    def _launch_suno_browser(self):
        chrome_exe = SUNO_CHROME
        if not chrome_exe.exists():
            return None
        if self._is_suno_browser_ready():
            self._log("[MUSIC] Reuse Suno browser dang chay tren port 9444", "INFO", "ve3")
            return None
        self._log(f"[MUSIC] Mo Suno browser: {chrome_exe}", "INFO", "ve3")
        self._cleanup_existing_suno_chrome()
        window_position = self._music_window_position()
        proc = subprocess.Popen([
            str(chrome_exe),
            "--remote-debugging-port=9444",
            "--no-first-run",
            "--new-window",
            f"--window-size={SUNO_WINDOW_SIZE}",
            f"--window-position={window_position}",
            "https://suno.com/create",
        ], cwd=str(chrome_exe.parent))
        self._track_process(proc, "suno-browser")
        for _ in range(16):
            _time.sleep(0.5)
            if self._is_suno_browser_ready():
                break
        return proc

    def _is_suno_browser_ready(self, timeout=2.0):
        try:
            import urllib.request
            with urllib.request.urlopen("http://127.0.0.1:9444/json/version", timeout=timeout) as resp:
                return resp.status == 200
        except Exception:
            return False

    def _cleanup_existing_suno_chrome(self):
        try:
            ps = r"""
Get-CimInstance Win32_Process |
  Where-Object {
    ($_.Name -eq 'chrome.exe' -or $_.Name -eq 'GoogleChromePortable.exe') -and
    $_.CommandLine -and
    $_.CommandLine -like '*tools\suno\GoogleChromePortable*' -and
    $_.CommandLine -like '*remote-debugging-port=9444*'
  } |
  ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
"""
            subprocess.run(
                ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", ps],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
                timeout=20,
            )
        except Exception:
            pass

    def _music_global_lock_path(self):
        return VE3_DIR / ".music_global.lock"

    def _pid_alive(self, pid):
        if not pid or int(pid) <= 0:
            return False
        if os.name == "nt":
            try:
                import ctypes
                from ctypes import wintypes

                PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
                STILL_ACTIVE = 259
                kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
                handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, int(pid))
                if not handle:
                    return False
                try:
                    exit_code = wintypes.DWORD()
                    if not kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code)):
                        return False
                    return exit_code.value == STILL_ACTIVE
                finally:
                    kernel32.CloseHandle(handle)
            except Exception:
                return False
        try:
            os.kill(int(pid), 0)
            return True
        except Exception:
            return False

    def _acquire_music_global_lock(self, project_code, wait_log_sec=20):
        lock_path = self._music_global_lock_path()
        owner_pid = os.getpid()
        started = _time.time()
        next_log_at = 0.0
        while True:
            try:
                lock_path.parent.mkdir(parents=True, exist_ok=True)
                fd = os.open(str(lock_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                with os.fdopen(fd, "w", encoding="utf-8") as f:
                    f.write(f"{owner_pid}|{project_code}|{int(started)}")
                return owner_pid
            except FileExistsError:
                try:
                    content = lock_path.read_text(encoding="utf-8", errors="ignore").strip()
                    parts = content.split("|")
                    lock_pid = int(parts[0]) if parts and parts[0].isdigit() else 0
                    lock_code = parts[1] if len(parts) > 1 else "?"
                except Exception:
                    lock_pid = 0
                    lock_code = "?"

                if lock_pid and not self._pid_alive(lock_pid):
                    try:
                        lock_path.unlink()
                        self._log(f"[MUSIC] Xoa stale global music lock cua PID={lock_pid} ({lock_code})", "WARN", "ve3")
                        continue
                    except Exception:
                        pass

                now = _time.time()
                if now >= next_log_at:
                    waited = int(now - started)
                    self._log(
                        f"[MUSIC] Dang cho global music lock ({waited}s). "
                        f"Project dang giu: {lock_code} PID={lock_pid or '?'}",
                        "INFO",
                        "ve3",
                    )
                    next_log_at = now + wait_log_sec
                _time.sleep(2)

    def _release_music_global_lock(self, owner_pid):
        lock_path = self._music_global_lock_path()
        try:
            if not lock_path.exists():
                return
            content = lock_path.read_text(encoding="utf-8", errors="ignore").strip()
            current_owner = int(content.split("|", 1)[0]) if content else 0
            if current_owner == int(owner_pid):
                lock_path.unlink()
        except Exception:
            pass

    def _music_has_pending(self, excel_path, project_dir):
        try:
            from modules.excel_manager import PromptWorkbook
            wb = PromptWorkbook(str(excel_path)); wb.load_or_create()
            tracks = self._get_music_tracks_compat(wb)
            if not tracks:
                return False
            for track in tracks:
                music_id = str(track.get("music_id", "")).strip()
                prompt = str(track.get("suno_prompt", "")).strip()
                if not music_id or not prompt:
                    continue
                out_mp3 = Path(project_dir) / "music" / f"{music_id}.mp3"
                if not out_mp3.exists():
                    return True
            return False
        except Exception:
            return False

    def _run_music_for_project(self, project_dir, excel_path, update_ui=False):
        lock_owner_pid = None
        chrome_proc = None
        try:
            from modules.excel_manager import PromptWorkbook
            from token_manager import TokenManager
            from suno_browser_worker import BrowserSunoWorker

            project_dir = Path(project_dir)
            excel_path = Path(excel_path)
            lock_owner_pid = self._acquire_music_global_lock(project_dir.name)
            self._log(f"[MUSIC] Da giu global music lock cho {project_dir.name}", "SUCCESS", "ve3")
            wb = PromptWorkbook(str(excel_path)); wb.load_or_create()
            tracks = self._get_music_tracks_compat(wb)
            pending = []
            for track in tracks:
                music_id = str(track.get("music_id", "")).strip()
                prompt = str(track.get("suno_prompt", "")).strip()
                if not music_id or not prompt:
                    continue
                out_mp3 = project_dir / "music" / f"{music_id}.mp3"
                if not out_mp3.exists():
                    pending.append(track)
            if not pending:
                self._log(f"[MUSIC] {project_dir.name}: da du mp3, khong can tao nhac", "INFO", "ve3")
                return True

            self._log(f"[MUSIC] {project_dir.name}: bt u to nhc ({len(pending)} tracks)", "INFO", "ve3")
            if update_ui:
                self.after(0, lambda: self.pages["home"].lbl_cur.configure(text=f" Music 0/{len(pending)}"))

            try:
                chrome_proc = self._launch_suno_browser()
                if chrome_proc:
                    _time.sleep(8)
                else:
                    self._log(f"[MUSIC] Khng tm thy browser: {SUNO_CHROME}", "WARN", "ve3")
            except Exception as e:
                self._log(f"[MUSIC] Khng m c browser: {e}", "WARN", "ve3")

            self._log("[MUSIC] Kt ni browser Suno...", "INFO", "ve3")

            with TokenManager(auto_launch=False) as tm:
                if not getattr(tm, "_page", None):
                    raise RuntimeError("Could not connect to Suno browser session on port 9444")
                self._log("[MUSIC]  kt ni browser Suno", "SUCCESS", "ve3")
                worker = BrowserSunoWorker(tm._page)
                done = 0
                for idx, track in enumerate(pending, start=1):
                    if self.music_stop_requested:
                        self._log("[MUSIC]  dng theo yu cu", "WARN", "ve3")
                        break

                    music_id = str(track.get("music_id", "")).strip()
                    title = (track.get("title") or f"Track {music_id}").strip()
                    prompt = (track.get("suno_prompt") or "").strip()
                    status = (track.get("status") or "").strip().lower()
                    out_mp3 = project_dir / "music" / f"{music_id}.mp3"
                    out_mp3.parent.mkdir(parents=True, exist_ok=True)

                    if out_mp3.exists():
                        done += 1
                        if status != "done":
                            self._update_music_track_compat(wb, music_id, status="done")
                        self._log(f"[MUSIC] {project_dir.name}: skip {music_id},  c mp3", "INFO", "ve3")
                        if update_ui:
                            self.after(0, lambda d=done: self.pages["home"].lbl_cur.configure(text=f" Music {d}/{len(pending)}"))
                        continue

                    self._update_music_track_compat(wb, music_id, status="generating")
                    self._log(f"[MUSIC {idx}/{len(pending)}] {project_dir.name}: {music_id} - {title}", "INFO", "ve3")
                    if update_ui:
                        self.after(0, lambda i=idx: self.pages["home"].lbl_cur.configure(text=f" Music {i-1}/{len(pending)}"))

                    ok = False
                    result = ""
                    for attempt in range(1, 4):
                        try:
                            ok, result = worker.generate_and_download(
                                prompt=prompt,
                                output_path=out_mp3,
                                timeout=420,
                                pick="best",
                            )
                        except Exception as e:
                            ok = False
                            result = str(e)

                        if ok:
                            break

                        result_text = str(result or "")
                        # Retry vi browser restart cho mi loi li
                        if attempt < 3:
                            self._log(
                                f"[MUSIC] {project_dir.name}: track {music_id} fail ({result_text[:80]}), restart browser & retry (ln {attempt}/3)",
                                "WARN",
                                "ve3",
                            )
                            try:
                                # ng browser c
                                try:
                                    tm.stop()
                                except Exception:
                                    pass
                                if chrome_proc and chrome_proc.poll() is None:
                                    self._kill_pid_tree(chrome_proc.pid)
                                    _time.sleep(3)
                                _time.sleep(2)

                                # M browser mi
                                chrome_proc = self._launch_suno_browser()
                                if chrome_proc:
                                    _time.sleep(8)
                                else:
                                    self._log(f"[MUSIC] Khng m li c browser", "WARN", "ve3")

                                # Reconnect
                                if not tm.start() or not getattr(tm, "_page", None):
                                    result = f"{result_text} | reconnect failed"
                                    break
                                worker = BrowserSunoWorker(tm._page)
                                _time.sleep(2)
                                self._log(f"[MUSIC] Browser restarted, retry track {music_id}...", "INFO", "ve3")
                            except Exception as e:
                                result = f"{result_text} | restart failed: {e}"
                                break
                            continue

                        break

                    if ok:
                        done += 1
                        self._update_music_track_compat(wb, music_id, status="done", suno_url=result)
                        self._log(f"[MUSIC OK] {project_dir.name}: {music_id} -> {out_mp3.name}", "SUCCESS", "ve3")
                        if update_ui:
                            self.after(0, lambda d=done: self.pages["home"].lbl_cur.configure(text=f" Music {d}/{len(pending)}"))
                    else:
                        self._update_music_track_compat(wb, music_id, status="error")
                        self._log(f"[MUSIC FAIL] {project_dir.name}: {music_id}: {result}", "ERROR", "ve3")
                        # Continue with next track instead of returning False

                    _time.sleep(8)

            if update_ui:
                self.after(0, self._reload_wb)
                self.after(0, lambda: self._load_excel(excel_path))
                self.after(0, lambda: self.pages["home"].lbl_active_project.configure(text="Ma dang chay: -"))
                self.after(0, lambda: self.pages["home"].lbl_cur.configure(text=""))
            self._log(f"[MUSIC] {project_dir.name}: hon tt", "SUCCESS", "ve3")
            return True

        except Exception as e:
            import traceback
            self._log(f"[MUSIC] {Path(project_dir).name}: error {e}", "ERROR", "ve3")
            self._log(traceback.format_exc(), "ERROR", "ve3")
            return False
        finally:
            if chrome_proc and chrome_proc.poll() is None:
                self._kill_pid_tree(chrome_proc.pid)
            if lock_owner_pid is not None:
                self._release_music_global_lock(lock_owner_pid)
            if update_ui:
                self.music_thread = None


    def stop_worker(self):
        if self.worker:
            self.worker.stop(); self._log("Stopping","WARN")
        # Kill all subprocesses
        with self.queue_lock:
            all_procs = list(self.queue_ve3_procs.values()) + list(self.queue_music_procs.values())
        for proc in all_procs:
            if proc and proc.poll() is None:
                self._kill_pid_tree(proc.pid)
                self._log(f"Killed subprocess PID={proc.pid}", "WARN")
        if self.music_thread and self.music_thread.is_alive():
            self.music_stop_requested = True
            self._log("Music stopping", "WARN")

    def toggle_queue_worker(self):
        if self.queue_running:
            self.queue_stop_requested = True
            self.btn_go.configure(text="Stopping...", fg_color="#555")
            self.pages["home"].btn_run_center.configure(text="Stopping...", fg_color="#555")
            self._log("[QUEUE] Dang yeu cau dung sau task hien tai...", "WARN")
            self._log("[QUEUE/EXCEL] Dang yeu cau dung worker Excel sau task hien tai...", "WARN", "excel")
            return

        cfg = self._build_cfg()
        if not cfg:
            return
        if not cfg.get("local_server_url") and not cfg.get("local_server_list"):
            messagebox.showwarning("Thieu server", "Them server trong Cai dat truoc!")
            return
        online_count = sum(1 for s in self.server_status_cache if s.get("available"))
        if self.server_status_cache and online_count == 0:
            self._refresh_server_status_sync()
            online_count = sum(1 for s in self.server_status_cache if s.get("available"))
        if self.server_status_cache and online_count == 0:
            self._log("[QUEUE] Chua co server nao online — se tu dong chay khi server san sang.", "WARN", "ve3")
        configured_pairs = self._get_server_pairs(only_available=False)
        available_pairs = self._get_server_pairs(only_available=True)
        if configured_pairs and not available_pairs:
            self._log("[QUEUE] Khong co pair server/account nao san sang. Kiem tra local_server_list.flow_account_name va trang thai server.", "WARN", "ve3")

        self.config_data.update({
            "flow_bearer_token": cfg["flow_bearer_token"],
            "flow_project_id": cfg["flow_project_id"],
        })
        self._save_config()
        cleared = self._clear_all_queue_markers()
        if cleared:
            self._log(f"[QUEUE] Da don {cleared} lock cu truoc khi start.", "WARN", "ve3")
        self.queue_running = True
        self.queue_stop_requested = False
        with self.queue_lock:
            self.queue_active_excel.clear()
            self.queue_active_ve3.clear()
            self.queue_active_pairs.clear()
            self.queue_excel_tasks.clear()
            self.queue_ve3_tasks.clear()
            self.queue_ve3_workers.clear()
            self.queue_progress_owner_code = None
            self.queue_progress_owner_pair = "-"
        self.btn_go.configure(text="STOP", fg_color="#D32F2F", hover_color="#9A0007")
        self.pages["home"].btn_run_center.configure(text="STOP", fg_color="#D32F2F", hover_color="#9A0007")
        self._log(f"[QUEUE] Bt u: Excel worker + VE3 dispatcher. Pair sn sng {len(available_pairs)}/{len(configured_pairs)}.", "SUCCESS", "ve3")
        self._log("[QUEUE/EXCEL] Worker Excel da khoi dong, se quet PROJECTS va tiep tuc cac ma chua co Excel.", "SUCCESS", "excel")
        self.queue_excel_thread = threading.Thread(target=self._queue_excel_loop, daemon=True)
        self.queue_ve3_thread = threading.Thread(target=self._queue_ve3_loop, args=(cfg,), daemon=True)
        self.queue_excel_thread.start()
        self.queue_ve3_thread.start()

    def _queue_projects(self):
        PROJECTS_DIR.mkdir(parents=True, exist_ok=True)
        self._refresh_manual_done_codes()
        projects = []
        total_dirs = 0
        skipped_reasons = {"manual_done": 0, "manual_skip": 0, "endpoint_complete": 0, "endpoint_hold": 0}

        for p in PROJECTS_DIR.iterdir():
            if not p.is_dir():
                continue
            total_dirs += 1

            # Check skip reasons
            if p.name in self.manual_done_codes or (p / ".manual_done.lock").exists():
                skipped_reasons["manual_done"] += 1
                continue
            if (p / ".manual_skip.lock").exists():
                skipped_reasons["manual_skip"] += 1
                continue
            if self._is_project_endpoint_complete(p):
                skipped_reasons["endpoint_complete"] += 1
                continue
            if (p / ".endpoint_hold.lock").exists():
                skipped_reasons["endpoint_hold"] += 1
                continue

            projects.append(p)

        self._log(
            f"[DEBUG] _queue_projects: scanned {total_dirs} dirs, "
            f"returned {len(projects)} projects | "
            f"skipped: manual_done={skipped_reasons['manual_done']}, "
            f"manual_skip={skipped_reasons['manual_skip']}, "
            f"endpoint_complete={skipped_reasons['endpoint_complete']}, "
            f"endpoint_hold={skipped_reasons['endpoint_hold']}",
            "INFO"
        )

        return sorted(projects, key=lambda p: p.name)

    def _excel_priority_key(self, project_dir):
        code = project_dir.name.lower()
        srt_path = project_dir / f"{project_dir.name}.srt"
        has_srt = srt_path.exists()
        has_excel = self._project_excel_path(project_dir).exists()
        has_img = (project_dir / "img").exists()
        mtime_hint = 0
        try:
            mtime_hint = -project_dir.stat().st_mtime
        except Exception:
            pass
        if has_img:
            resume_priority = 0
        elif has_excel:
            resume_priority = 1
        else:
            resume_priority = 2
        return (resume_priority, 0 if has_excel else 1, 0 if has_srt else 1, mtime_hint, code)

    def _ve3_priority_key(self, project_dir):
        code = project_dir.name.lower()
        if not self._project_excel_path(project_dir).exists():
            return (999, code)
        img_dir = project_dir / "img"
        img_count = 0
        if img_dir.exists():
            try:
                img_count = sum(1 for _ in img_dir.iterdir())
            except Exception:
                pass
        return (-img_count, code)

    _channel_cache = {}
    _in_progress_cache = {}
    _cache_ts = 0

    def _get_project_channel(self, project_dir) -> str:
        """Lấy channel từ project code (không đọc file)."""
        import re
        code = Path(project_dir).name
        if code in self._channel_cache:
            return self._channel_cache[code]
        m = re.match(r"^([A-Za-z]+\d+)-0*(\d+)$", code, flags=re.IGNORECASE)
        ch = f"{m.group(1).upper()}-T{int(m.group(2))}" if m else "unknown"
        self._channel_cache[code] = ch
        return ch

    def _is_project_in_progress(self, project_dir) -> bool:
        """Project đang làm dở = có ít nhất 1 ảnh trong img/."""
        pd = Path(project_dir)
        now = _time.time()
        if now - self._cache_ts > 60:
            self._in_progress_cache.clear()
            self._cache_ts = now
        code = pd.name
        if code in self._in_progress_cache:
            return self._in_progress_cache[code]
        img_dir = pd / "img"
        result = img_dir.exists() and bool(next(img_dir.glob("*.png"), None))
        self._in_progress_cache[code] = result
        return result

    def _interleave_by_channel(self, projects, priority_key_func):
        """Ưu tiên mã đang làm dở trước, sau đó round-robin theo kênh cho mã mới."""
        from collections import OrderedDict
        in_progress = []
        new_projects = []
        for pd in projects:
            if self._is_project_in_progress(pd):
                in_progress.append(pd)
            else:
                new_projects.append(pd)
        in_progress.sort(key=priority_key_func)
        channel_groups = OrderedDict()
        for pd in new_projects:
            ch = self._get_project_channel(pd)
            channel_groups.setdefault(ch, []).append(pd)
        for ch in channel_groups:
            channel_groups[ch] = sorted(channel_groups[ch], key=priority_key_func)
        round_robin = []
        while any(channel_groups.values()):
            for ch in list(channel_groups.keys()):
                if channel_groups[ch]:
                    round_robin.append(channel_groups[ch].pop(0))
                else:
                    del channel_groups[ch]
        return in_progress + round_robin

    def _queue_projects_excel(self):
        projects = self._queue_projects()
        return self._interleave_by_channel(projects, self._excel_priority_key)

    def _queue_projects_ve3(self):
        projects = self._queue_projects()
        return self._interleave_by_channel(projects, self._ve3_priority_key)

    def _project_excel_path(self, project_dir):
        code = project_dir.name
        ep = project_dir / f"{code}_prompts.xlsx"
        if ep.exists():
            return ep
        excels = [p for p in project_dir.glob("*_prompts.xlsx") if not p.name.startswith("~")]
        return excels[0] if excels else ep

    def _get_project_state_cached(self, project_dir):
        ep = self._project_excel_path(project_dir)
        if not ep.exists():
            return None
        cache_key = str(ep)
        now = _time.time()
        try:
            st = ep.stat()
            cache_sig = (float(st.st_mtime), int(st.st_size))
            cached = self._project_state_cache.get(cache_key)
            if cached and cached.get("sig") == cache_sig and now - float(cached.get("ts", 0.0) or 0.0) < self._project_state_cache_ttl:
                return cached.get("data")
        except Exception:
            cache_sig = None

        # Retry logic for Excel reading (handle file locking from Excel Worker)
        max_retries = 3
        retry_delay = 2
        code = project_dir.name if hasattr(project_dir, 'name') else str(project_dir)

        for attempt in range(max_retries):
            try:
                from modules.excel_manager import PromptWorkbook
                wb = PromptWorkbook(str(ep)); wb.load_or_create()
                scenes_all = wb.get_scenes() or []
                try:
                    stats = wb.get_stats()
                except Exception:
                    stats = {}
                try:
                    summary = wb.get_processing_summary()
                except Exception:
                    summary = None
                data = {"wb": wb, "scenes": scenes_all, "stats": stats, "summary": summary}
                if cache_sig is not None:
                    self._project_state_cache[cache_key] = {"sig": cache_sig, "ts": now, "data": data}

                if attempt > 0:
                    self._log(f"[{code}] Excel state read succeeded on attempt {attempt + 1}", "WARN")

                return data
            except Exception as exc:
                if attempt < max_retries - 1:
                    self._log(f"[{code}] Excel state read failed (attempt {attempt + 1}/{max_retries}): {exc}", "WARN")
                    _time.sleep(retry_delay)
                else:
                    self._log(f"[{code}] Excel state read FAILED after {max_retries} attempts: {exc}", "ERROR")
                    return {"error": exc}

    def _queue_marker(self, project_dir, name):
        return project_dir / f".queue_{name}.lock"

    def _manual_done_marker(self, project_dir):
        return project_dir / ".manual_done.lock"

    def _manual_skip_marker(self, project_dir):
        return project_dir / ".manual_skip.lock"

    def _endpoint_hold_marker(self, project_dir):
        return project_dir / ".endpoint_hold.lock"

    def _endpoint_done_marker(self, project_dir):
        return project_dir / ".endpoint_done.lock"

    def _is_project_exported_to_visual(self, project_dir):
        try:
            return (EDIT_VISUAL_DIR / Path(project_dir).name).is_dir()
        except Exception:
            return False

    def _has_project_archive(self, project_dir):
        try:
            code = Path(project_dir).name
            if (ARCHIVE_DIR / code).is_dir():
                return True
            return any(p.is_dir() for p in ARCHIVE_DIR.glob(f"{code}_*"))
        except Exception:
            return False

    def _repair_endpoint_done_marker(self, project_dir, reason="repaired"):
        try:
            project_dir = Path(project_dir)
            if not project_dir.exists():
                return
            marker = self._endpoint_done_marker(project_dir)
            if not marker.exists():
                marker.write_text(f"endpoint_done {time.time()} {reason}", encoding="utf-8")
        except Exception:
            pass

    def _is_project_endpoint_complete(self, project_dir):
        try:
            complete = (
                self._endpoint_done_marker(project_dir).exists()
                or self._is_project_exported_to_visual(project_dir)
                or self._has_project_archive(project_dir)
            )
            if complete:
                self._repair_endpoint_done_marker(project_dir, reason="repaired_from_endpoint_artifact")
            return complete
        except Exception:
            return False

    def _is_project_manually_done(self, project_dir):
        try:
            return self._manual_done_marker(project_dir).exists() or self._manual_skip_marker(project_dir).exists()
        except Exception:
            return False

    def _set_project_manually_done(self, project_dir, done=True):
        marker = self._manual_done_marker(project_dir)
        skip_marker = self._manual_skip_marker(project_dir)
        try:
            if done:
                marker.write_text(f"manual_done {time.time()}", encoding="utf-8")
                skip_marker.write_text(f"manual_skip {time.time()}", encoding="utf-8")
            else:
                if marker.exists():
                    marker.unlink()
                if skip_marker.exists():
                    skip_marker.unlink()
        except Exception:
            pass
        return marker

    def _write_queue_marker(self, project_dir, name, text=""):
        marker = self._queue_marker(project_dir, name)
        try:
            marker.write_text(text or f"{name} {time.time()}", encoding="utf-8")
        except Exception:
            pass
        return marker

    def _clear_queue_marker(self, project_dir, name):
        marker = self._queue_marker(project_dir, name)
        try:
            if marker.exists():
                marker.unlink()
        except Exception:
            pass

    def _excel_is_locked(self, excel_path):
        if not excel_path.exists():
            lock_path = excel_path.with_suffix(".xlsx.lock")
            temp_path = excel_path.with_suffix(".xlsx.tmp")
            return lock_path.exists() or temp_path.exists()
        lock_path = excel_path.with_name(f"~${excel_path.name}")
        internal_lock = excel_path.with_suffix(".xlsx.lock")
        temp_path = excel_path.with_suffix(".xlsx.tmp")
        if internal_lock.exists() or temp_path.exists():
            return True
        if lock_path.exists():
            return True
        try:
            with open(excel_path, "a+b"):
                return False
        except OSError:
            return True

    def _excel_main_file_is_valid(self, excel_path):
        if not excel_path.exists() or not excel_path.is_file():
            return False
        try:
            if excel_path.stat().st_size < 1000:
                return False
            from zipfile import ZipFile
            with ZipFile(str(excel_path), "r") as zf:
                return zf.testzip() is None
        except Exception:
            return False

    def _process_id_is_alive(self, pid):
        try:
            pid = int(str(pid).strip())
        except Exception:
            return False
        if pid <= 0:
            return False
        try:
            result = subprocess.run(
                ["tasklist", "/FI", f"PID eq {pid}", "/NH"],
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True,
                timeout=5,
                check=False,
            )
            return str(pid) in (result.stdout or "")
        except Exception:
            return True

    def _cleanup_stale_excel_sidecars_after_kill(self, excel_path, min_age_sec=10):
        """Unblock endpoint after forced kill while preserving possible temp output."""
        if not self._excel_main_file_is_valid(excel_path):
            return False
        now = _time.time()
        changed = False
        lock_path = excel_path.with_suffix(".xlsx.lock")
        temp_path = excel_path.with_suffix(".xlsx.tmp")
        for sidecar in (lock_path, temp_path):
            if not sidecar.exists():
                continue
            try:
                age = now - sidecar.stat().st_mtime
            except OSError:
                continue
            if age < min_age_sec:
                continue
            if sidecar == lock_path:
                try:
                    pid_text = sidecar.read_text(encoding="utf-8", errors="ignore").strip()
                except Exception:
                    pid_text = ""
                if pid_text and self._process_id_is_alive(pid_text):
                    continue
                try:
                    sidecar.unlink()
                    changed = True
                except Exception:
                    pass
            else:
                try:
                    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                    sidecar.rename(sidecar.with_name(f"{sidecar.name}.stale_{stamp}"))
                    changed = True
                except Exception:
                    pass
        return changed

    def _wait_excel_ready_for_endpoint(self, excel_path, reason="success", timeout_sec=12):
        deadline = _time.time() + max(1, timeout_sec)
        manual_reason = reason in ("manual_done", "manual_done_after_stop")
        cleaned = False
        while _time.time() < deadline:
            if self._wait_excel_stable(excel_path, checks=2, delay=0.8):
                return True
            if manual_reason and not cleaned and self._cleanup_stale_excel_sidecars_after_kill(excel_path):
                cleaned = True
                self._log(
                    f"[QUEUE] {excel_path.parent.name}: don lock/tmp Excel cu sau kill subprocess",
                    "WARN",
                    "ve3",
                )
                continue
            _time.sleep(1.0)
        if manual_reason and self._cleanup_stale_excel_sidecars_after_kill(excel_path, min_age_sec=0):
            self._log(
                f"[QUEUE] {excel_path.parent.name}: ep endpoint manual_done, bo qua lock/tmp noi bo da stale",
                "WARN",
                "ve3",
            )
            return self._wait_excel_stable(excel_path, checks=2, delay=0.8)
        return False

    def _wait_excel_stable(self, excel_path, checks=3, delay=2.0):
        if not excel_path.exists() or self._excel_is_locked(excel_path):
            return False
        last = None
        for _ in range(checks):
            try:
                stat = excel_path.stat()
                current = (stat.st_size, int(stat.st_mtime))
            except OSError:
                return False
            if last is not None and current != last:
                return False
            last = current
            _time.sleep(delay)
            if self._excel_is_locked(excel_path):
                return False
        return True

    def _is_file_stable(self, path, checks=3, delay=1.0):
        """A file is stable when size+mtime stay unchanged across checks."""
        path = Path(path)
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
            _time.sleep(delay)
            if not path.exists():
                return False
        return True

    def _collect_audio_files(self, project_dir):
        files = []
        for ext in ("*.mp3", "*.wav", "*.m4a", "*.flac", "*.ogg", "*.aac"):
            files.extend(project_dir.glob(ext))
        # Prefer deterministic order.
        return sorted([f for f in files if f.is_file()], key=lambda p: p.name.lower())

    def _project_has_srt(self, project_dir, stable=False):
        srt = project_dir / f"{project_dir.name}.srt"
        if not srt.exists():
            return False
        return self._is_file_stable(srt, checks=2, delay=0.8) if stable else True

    def _project_has_source(self, project_dir, stable=False):
        if self._project_has_srt(project_dir, stable=stable):
            return True
        return self._project_has_audio(project_dir, stable=stable)

    def _project_has_audio(self, project_dir, stable=False):
        audio_files = self._collect_audio_files(project_dir)
        if not audio_files:
            return False
        if not stable:
            return True
        return any(self._is_file_stable(p, checks=2, delay=0.8) for p in audio_files)

    def _project_excel_complete(self, project_dir):
        code = project_dir.name
        ep = self._project_excel_path(project_dir)
        if not ep.exists():
            return False
        if self._queue_marker(project_dir, "excel").exists():
            return False
        if self._excel_is_locked(ep):
            return False
        if not self._wait_excel_stable(ep, checks=2, delay=1.5):
            return False
        try:
            state = self._get_project_state_cached(project_dir)
            if not state or state.get("error"):
                return False
            scenes = state.get("scenes") or []
            if not scenes or not any((s.img_prompt or "").strip() for s in scenes):
                return False
            summary = state.get("summary")
            if summary and summary.get("completion_pct", 0) < 100:
                return False
            return True
        except Exception as exc:
            return False

    def _project_resume_candidate(self, project_dir):
        ep = self._project_excel_path(project_dir)
        if not ep.exists():
            return False
        try:
            from modules.excel_manager import PromptWorkbook
            wb = PromptWorkbook(str(ep)); wb.load_or_create()
            stats = wb.get_stats()
            scenes = int(stats.get("scenes_with_prompts", 0) or stats.get("total_scenes", 0) or 0)
            if scenes <= 0:
                return False
            images_done = int(stats.get("images_done", 0) or 0)
            videos_done = int(stats.get("videos_done", 0) or 0)
            music_pending = self._music_has_pending(ep, project_dir)
            return images_done < scenes or videos_done < scenes or music_pending
        except Exception:
            return True

    def _prune_excel_task_registry(self):
        """Drop finished Excel task threads to prevent stale capacity blocking."""
        with self.queue_lock:
            stale = [code for code, t in (self.queue_excel_tasks or {}).items() if not t or not t.is_alive()]
            for code in stale:
                self.queue_excel_tasks.pop(code, None)
                # Keep active set in sync in case a worker died before finally-cleanup.
                self.queue_active_excel.discard(code)

    def _run_single_project_excel(self, pd, mode):
        """Xá»­ lÃ½ 1 mÃ£ Excel trong thread riÃªng (cháº¡y Ä‘á»™c láº­p)"""
        code = pd.name  # Capture tÃªn mÃ£ ngay tá»« Ä‘áº§u
        try:
            self.after(0, lambda c=code:
                self.pages["home"].lbl_active_project.configure(text=f"Ma dang chay: {c}"))

            step_label = "SRT -> Excel" if mode == "excel-only" else "MP3/SRT -> Excel"
            self._log(f"[QUEUE/EXCEL] {code}: {step_label}", "INFO", "excel")

            # Cháº¡y subprocess vá»›i log riÃªng cho mÃ£ nÃ y
            self._run_excel_engine_subprocess(
                pd,
                mode=mode,
                log_cb=lambda m, l="INFO", c=code:
                    self._log(f"[{c}] {m}", l, "excel")
            )

            self._log(f"[QUEUE/EXCEL] {code}: xong", "SUCCESS", "excel")

            # Invalidate cache state so VE3 loop can detect completion immediately
            ep = self._project_excel_path(pd)
            if ep.exists():
                cache_key = str(ep)
                self._project_state_cache.pop(cache_key, None)
                self._log(f"[QUEUE/EXCEL] {code}: invalidated state cache", "INFO", "excel")

        except Exception as exc:
            import traceback
            self._log(f"[QUEUE/EXCEL] {code}: loi {exc}", "ERROR", "excel")
            self._log(f"[QUEUE/EXCEL] {code}: traceback: {traceback.format_exc()}", "ERROR", "excel")

        finally:
            # Cleanup khi xong
            self._clear_queue_marker(pd, "excel")
            self._log(f"[QUEUE/EXCEL] {code}: cleared excel lock marker", "INFO", "excel")
            with self.queue_lock:
                self.queue_active_excel.discard(code)
                self.queue_excel_tasks.pop(code, None)

    def _queue_excel_loop(self):
        # Gioi han so ma Excel chay dong thoi
        max_excel_concurrent = self.config_data.get("excel_workers", 2)
        try:
            max_excel_concurrent = int(max_excel_concurrent)
        except Exception:
            max_excel_concurrent = 2
        max_excel_concurrent = max(1, max_excel_concurrent)

        try:
            while not self.queue_stop_requested:
                self._prune_excel_task_registry()
                did_work = False
                total_projects = 0
                busy_projects = 0
                has_excel_projects = 0
                no_source_projects = 0
                blocked_by_ve3_projects = 0
                pending_projects = 0
                retry_resume_soon = False

                # Äáº¿m sá»‘ task Ä‘ang cháº¡y
                with self.queue_lock:
                    active_count = len(self.queue_excel_tasks)

                excel_queue = self._queue_projects_excel()
                self._log(f"[DEBUG] Excel queue returned {len(excel_queue)} projects", "INFO", "excel")

                # Debug: log first 5 project codes in queue
                if excel_queue:
                    sample_codes = [pd.name for pd in excel_queue[:5]]
                    self._log(f"[DEBUG] First 5 in queue: {', '.join(sample_codes)}", "INFO", "excel")

                for pd in excel_queue:
                    if self.queue_stop_requested:
                        break

                    total_projects += 1

                    # Clean up stale tasks
                    with self.queue_lock:
                        stale = [code for code, t in (self.queue_excel_tasks or {}).items() if not t or not t.is_alive()]
                        for code in stale:
                            self.queue_excel_tasks.pop(code, None)
                            self.queue_active_excel.discard(code)

                    for stale_name in ("excel", "ve3"):
                        marker = self._queue_marker(pd, stale_name)
                        try:
                            if marker.exists() and time.time() - marker.stat().st_mtime > 24 * 3600:
                                marker.unlink()
                        except Exception:
                            pass

                    ep = self._project_excel_path(pd)
                    if self._project_excel_complete(pd):
                        has_excel_projects += 1
                        if total_projects <= 5:  # Log first 5 for debugging
                            self._log(f"[DEBUG] {pd.name}: SKIP - already has complete Excel", "INFO", "excel")
                        continue

                    # Check vÃ  Ä‘Ã¡nh dáº¥u atomic Ä‘á»ƒ trÃ¡nh duplicate
                    with self.queue_lock:
                        if pd.name in self.queue_active_excel or pd.name in self.queue_active_ve3:
                            busy_projects += 1
                            if total_projects <= 5:  # Log first 5 for debugging
                                self._log(f"[DEBUG] {pd.name}: SKIP - busy (excel={pd.name in self.queue_active_excel}, ve3={pd.name in self.queue_active_ve3})", "INFO", "excel")
                            continue
                        # ÄÃ¡nh dáº¥u ngay Ä‘á»ƒ thread khÃ¡c khÃ´ng pick

                        # Check da du so luong task chua - CHI BREAK KHI MA NAY CAN EXCEL
                        if len(self.queue_excel_tasks) >= max_excel_concurrent:
                            break
                        self.queue_active_excel.add(pd.name)

                    # Tá»« Ä‘Ã¢y trá»Ÿ Ä‘i, mÃ£ nÃ y Ä‘Ã£ Ä‘Æ°á»£c claim bá»Ÿi thread nÃ y
                    ep = self._project_excel_path(pd)

                    # Validate: náº¿u khÃ´ng pass cÃ¡c check, release claim vÃ  skip
                    should_skip = False
                    skip_reason = ""

                    if not self._project_has_source(pd, stable=True):
                        # If source exists but still being copied, wait for next cycle.
                        has_raw_source = self._project_has_source(pd, stable=False)
                        if has_raw_source:
                            now = _time.time()
                            last_log = self.source_wait_log_ts.get(pd.name, 0)
                            if now - last_log >= 10:
                                self._log(f"[QUEUE/EXCEL] {pd.name}: dang cho file source copy xong (chua on dinh)...", "WARN", "excel")
                                self.source_wait_log_ts[pd.name] = now
                            pending_projects += 1
                        else:
                            no_source_projects += 1
                            if total_projects <= 5:  # Log first 5 for debugging
                                self._log(f"[DEBUG] {pd.name}: SKIP - no source file", "INFO", "excel")
                        should_skip = True
                        skip_reason = "no_stable_source"
                    elif self._project_resume_candidate(pd) and ep.exists() and (self._excel_is_locked(ep) or not self._wait_excel_stable(ep, checks=2, delay=1.5)):
                        pending_projects += 1
                        retry_resume_soon = True
                        self._log(
                            f"[QUEUE] {pd.name}: ma dang do, uu tien retry Excel som thay vi nhay sang ma moi",
                            "WARN",
                        )
                        should_skip = True
                        skip_reason = "excel_locked_or_unstable"
                    elif self._queue_marker(pd, "ve3").exists():
                        blocked_by_ve3_projects += 1
                        if total_projects <= 5:  # Log first 5 for debugging
                            self._log(f"[DEBUG] {pd.name}: SKIP - VE3 lock exists", "INFO", "excel")
                        should_skip = True
                        skip_reason = "blocked_by_ve3"

                    # Release claim náº¿u skip
                    if should_skip:
                        with self.queue_lock:
                            self.queue_active_excel.discard(pd.name)
                        continue

                    self.source_wait_log_ts.pop(pd.name, None)

                    # XÃ¡c Ä‘á»‹nh mode
                    has_srt = self._project_has_srt(pd, stable=True)
                    has_audio = self._project_has_audio(pd, stable=True)
                    if has_srt:
                        mode = "excel-only"
                    elif has_audio:
                        mode = "srt-excel-only"
                    else:
                        # Lost source unexpectedly: don't run headless in wrong mode.
                        self._log(f"[QUEUE/EXCEL] {pd.name}: thieu ca SRT va audio, bo qua va cho lan quet sau", "WARN", "excel")
                        with self.queue_lock:
                            self.queue_active_excel.discard(pd.name)
                        continue

                    # MÃ£ nÃ y Ä‘Ã£ pass táº¥t cáº£ validation, spawn task
                    pending_projects += 1
                    did_work = True
                    self._log(f"[DEBUG] {pd.name}: PASSED all checks, spawning Excel task (mode: {mode})", "INFO", "excel")
                    self._write_queue_marker(pd, "excel", "Excel worker is creating this workbook")

                    # Táº O THREAD RIÃŠNG CHO MÃƒ NÃ€Y (khÃ´ng chá»)
                    task = threading.Thread(
                        target=self._run_single_project_excel,
                        args=(pd, mode),
                        daemon=True
                    )
                    with self.queue_lock:
                        self.queue_excel_tasks[pd.name] = task
                    task.start()

                if not did_work:
                    with self.queue_lock:
                        active_excel = len(self.queue_excel_tasks)
                        active_codes = sorted((self.queue_excel_tasks or {}).keys())
                    if active_excel >= max_excel_concurrent:
                        self._log(
                            f"[QUEUE/EXCEL] Dang ban: {active_excel}/{max_excel_concurrent} worker "
                            f"(dang chay: {', '.join(active_codes) if active_codes else '-'})",
                            "INFO",
                            "excel",
                        )
                        _time.sleep(5 if retry_resume_soon else 10)
                        continue
                    self._log(
                        "[QUEUE/EXCEL] Khong co ma can tao Excel. "
                        f"Quet {total_projects} ma | co Excel {has_excel_projects} | "
                        f"cho nguon {no_source_projects} | dang ban {busy_projects} | "
                        f"ve3 dang giu {blocked_by_ve3_projects} | pending {pending_projects}",
                        "INFO",
                        "excel",
                    )
                    _time.sleep(5 if retry_resume_soon else 30)
                else:
                    _time.sleep(2)
        finally:
            self._queue_thread_finished()

    def _project_needs_ve3(self, project_dir):
        if self._is_project_endpoint_complete(project_dir):
            return False
        if self._endpoint_hold_marker(project_dir).exists():
            return False
        if self._is_project_manually_done(project_dir):
            return False
        if not self._project_excel_complete(project_dir):
            return False
        ep = self._project_excel_path(project_dir)
        if not ep.exists():
            return False
        if self._queue_marker(project_dir, "excel").exists() or self._queue_marker(project_dir, "ve3").exists():
            return False
        if self._excel_is_locked(ep):
            return False
        return self._project_has_pending_ve3_units(project_dir)

    def _project_has_pending_ve3_units(self, project_dir):
        """True when project still has actionable VE3 work (img/video)."""
        ep = self._project_excel_path(project_dir)
        if not ep.exists():
            return False
        try:
            state = self._get_project_state_cached(project_dir)
            if not state or state.get("error"):
                raise RuntimeError(state.get("error") if state else "Excel state unavailable")
            scenes_all = state.get("scenes") or []
            img_dir = project_dir / "img"
            vid_dir = project_dir / "vid"

            pending_img = False
            pending_vid = False

            for s in scenes_all:
                sid = int(getattr(s, "scene_id", 0) or 0)
                if sid <= 0:
                    continue

                img_prompt = str(getattr(s, "img_prompt", "") or "").strip()
                if img_prompt:
                    st_img = str(getattr(s, "status_img", "") or "").strip().lower()
                    has_img = (
                        (img_dir / f"{sid}.png").exists()
                        or (img_dir / f"{sid}.jpg").exists()
                        or (img_dir / f"{sid}.mp4").exists()
                    )
                    if (not has_img) and st_img not in ("skip", "error"):
                        pending_img = True

                video_prompt = str(getattr(s, "video_prompt", "") or "").strip()
                if video_prompt:
                    st_vid = str(getattr(s, "status_vid", "") or "").strip().lower()
                    has_vid = (
                        (vid_dir / f"{sid}.mp4").exists()
                        or (img_dir / f"{sid}.mp4").exists()
                    )
                    if (not has_vid) and st_vid not in ("skip", "error"):
                        pending_vid = True

                if pending_img or pending_vid:
                    break

            return bool(pending_img or pending_vid)
        except Exception as exc:
            self._log(f"[QUEUE/VE3] {project_dir.name}: khng c c Excel {exc}", "WARN", "ve3")
            # Safe default: if cannot verify, treat as still pending to avoid premature endpoint.
            return True

    def _project_ready_for_endpoint_by_files(self, project_dir):
        """Ground-truth completion check from folders, not Excel status flags."""
        ep = self._project_excel_path(project_dir)
        if not ep.exists():
            return False
        try:
            state = self._get_project_state_cached(project_dir)
            if not state or state.get("error"):
                return False
            scenes_all = state.get("scenes") or []

            scene_ids_img = [
                int(s.scene_id) for s in scenes_all
                if str(getattr(s, "img_prompt", "") or "").strip()
            ]
            scene_ids_vid = [
                int(s.scene_id) for s in scenes_all
                if str(getattr(s, "video_prompt", "") or "").strip()
            ]
            if not scene_ids_img:
                return False

            img_dir = project_dir / "img"
            vid_dir = project_dir / "vid"

            for sid in scene_ids_img:
                if not ((img_dir / f"{sid}.png").exists() or
                        (img_dir / f"{sid}.jpg").exists() or
                        (img_dir / f"{sid}.mp4").exists()):
                    return False

            for sid in scene_ids_vid:
                if not ((vid_dir / f"{sid}.mp4").exists() or
                        (img_dir / f"{sid}.mp4").exists()):
                    return False

            return True
        except Exception:
            return False

    def _run_music_for_project_serial(self, project_dir, excel_path):
        with self.music_lock:
            self._run_music_for_project(project_dir, excel_path, update_ui=True)

    def _queue_claim_progress_owner(self, code, pair_text="-", force=False):
        with self.queue_lock:
            if force or not self.queue_progress_owner_code:
                self.queue_progress_owner_code = code
                self.queue_progress_owner_pair = pair_text or "-"
            return self.queue_progress_owner_code == code

    def _queue_update_progress_ui(self, code, pair_text, ph, cur, tot, det=""):
        if threading.current_thread() is not threading.main_thread():
            with self._progress_update_lock:
                self._progress_update_cache[(code, ph)] = (code, pair_text, ph, cur, tot, det)
                if not self._progress_flush_scheduled:
                    self._progress_flush_scheduled = True
                    self.after(120, self._flush_queue_progress_updates)
            return
        if not self._queue_claim_progress_owner(code, pair_text=pair_text):
            return
        self.pages["home"].lbl_active_project.configure(text=f"Ma dang chay: {code}")
        self.pages["home"].lbl_running_pair.configure(text=pair_text or "-")
        self._prog(ph, cur, tot, det)

    def _flush_queue_progress_updates(self):
        with self._progress_update_lock:
            updates = list(self._progress_update_cache.values())
            self._progress_update_cache.clear()
            self._progress_flush_scheduled = False
        for code, pair_text, ph, cur, tot, det in updates:
            self._queue_update_progress_ui(code, pair_text, ph, cur, tot, det)

    def _queue_release_progress_owner(self, code):
        if threading.current_thread() is not threading.main_thread():
            self.after(0, lambda: self._queue_release_progress_owner(code))
            return
        next_owner = None
        with self.queue_lock:
            if self.queue_progress_owner_code != code:
                return
            candidates = sorted(c for c in (self.queue_active_ve3 or set()) if c != code)
            if candidates:
                next_owner = candidates[0]
                self.queue_progress_owner_code = next_owner
                self.queue_progress_owner_pair = "-"
            else:
                self.queue_progress_owner_code = None
                self.queue_progress_owner_pair = "-"
        if next_owner:
            self.pages["home"].lbl_active_project.configure(text=f"Ma dang chay: {next_owner}")
            self.pages["home"].lbl_running_pair.configure(text="-")
        else:
            self.pages["home"].lbl_active_project.configure(text="Ma dang chay: -")
            self.pages["home"].lbl_running_pair.configure(text="-")

    def _run_single_project_ve3(self, pd, pair, cfg):
        """Run VE3 worker + music as subprocesses. Kill = instant stop."""
        code = pd.name
        ve3_proc = None
        music_proc = None
        res = None
        try:
            pair_text = f"{pair['server_name']} / {pair['flow_account_name']}"
            if self._queue_claim_progress_owner(code, pair_text=pair_text):
                self.after(0, lambda c=code, pt=pair_text: [
                    self.pages["home"].lbl_active_project.configure(text=f"Ma dang chay: {c}"),
                    self.pages["home"].lbl_running_pair.configure(text=pt),
                ])
            self._log(
                f"[QUEUE/VE3] {code}: bat dau tao anh/video (subprocess) tren {pair_text}",
                "INFO", "ve3",
            )

            # Write config JSON for subprocess
            pair_cfg = self._build_project_pair_cfg(cfg, pair)
            config_file = pd / ".ve3_run_config.json"
            with open(config_file, "w", encoding="utf-8") as f:
                json.dump(pair_cfg, f, ensure_ascii=False, indent=2)

            excel_path = self._project_excel_path(pd)
            self._save_project_pair_binding(pd, pair)

            # Queue VE3 mode finishes by image/video only; music is not launched here.
            # Launch VE3 worker subprocess
            worker_script = str(VE3_DIR / "ve3_worker.py")
            ve3_proc = subprocess.Popen(
                [sys.executable, worker_script, str(pd), "--config", str(config_file)],
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                encoding="utf-8", errors="replace",
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            with self.queue_lock:
                self.queue_ve3_procs[code] = ve3_proc
            self._track_process({"pid": ve3_proc.pid}, f"ve3-{code}")
            self._log(f"[QUEUE/VE3] {code}: worker subprocess PID={ve3_proc.pid}", "INFO", "ve3")

            # Read stdout for structured output
            for line in ve3_proc.stdout:
                line = line.rstrip()
                if not line:
                    continue
                if line.startswith("@@LOG|"):
                    parts = line.split("|", 2)
                    if len(parts) >= 3:
                        self._log(f"[{code}] {parts[2]}", parts[1], "ve3")
                elif line.startswith("@@PROG|"):
                    parts = line.split("|", 4)
                    if len(parts) >= 4:
                        try:
                            ph, cur, tot = parts[1], int(parts[2]), int(parts[3])
                            det = parts[4] if len(parts) > 4 else ""
                            self._queue_update_progress_ui(code, pair_text, ph, cur, tot, det)
                        except ValueError:
                            pass
                elif line.startswith("@@ITEM|"):
                    parts = line.split("|", 5)
                    if len(parts) >= 4:
                        tp, item_id, st = parts[1], parts[2], parts[3]
                        path = parts[4] if len(parts) > 4 else None
                        try:
                            ex = json.loads(parts[5]) if len(parts) > 5 else None
                        except Exception:
                            ex = None
                        self.after(0, lambda tp=tp, iid=item_id, s=st, p=path, e=ex: self._item(tp, iid, s, p, e))
                elif line.startswith("@@RESULT|"):
                    try:
                        res = json.loads(line.split("|", 1)[1])
                    except Exception:
                        pass

            # Wait for process to fully exit
            ve3_proc.wait(timeout=30)

            if res and res.get("success"):
                self._log(f"[QUEUE/VE3] {code}: xong {res.get('completed')}/{res.get('total')}", "SUCCESS", "ve3")
            elif res:
                self._log(f"[QUEUE/VE3] {code}: co loi {res}", "ERROR", "ve3")
            else:
                exit_code = ve3_proc.returncode
                self._log(f"[QUEUE/VE3] {code}: subprocess exit code={exit_code}", "WARN", "ve3")
                res = {"success": exit_code == 0, "completed": 0, "total": 0}

        except Exception as exc:
            import traceback
            self._log(f"[QUEUE/VE3] {code}: loi {exc}", "ERROR", "ve3")
            self._log(traceback.format_exc(), "ERROR", "ve3")
        finally:
            # Kill music subprocess if still running (don't wait)
            if music_proc and music_proc.poll() is None:
                try:
                    self._kill_pid_tree(music_proc.pid)
                    self._log(f"[QUEUE/VE3] {code}: killed music subprocess (VE3 done)", "INFO", "ve3")
                except Exception:
                    pass
            # Cleanup config file
            try:
                config_file = pd / ".ve3_run_config.json"
                if config_file.exists():
                    config_file.unlink()
            except Exception:
                pass
            # Release locks
            self._queue_release_progress_owner(code)
            self._clear_queue_marker(pd, "ve3")
            with self.queue_lock:
                self.queue_active_ve3.discard(code)
                self.queue_active_pairs.pop(pair["pair_id"], None)
                self.queue_ve3_tasks.pop(code, None)
                self.queue_ve3_workers.pop(code, None)
                self.queue_ve3_procs.pop(code, None)
                self.queue_music_procs.pop(code, None)
            # Endpoint check - skip if already completed (e.g. by _manual_complete_project)
            if pd.exists() and not self._is_project_endpoint_complete(pd):
                endpoint_reason = None
                if res and res.get("success"):
                    endpoint_reason = "queue_success"
                elif self._is_project_manually_done(pd):
                    endpoint_reason = "manual_done_after_stop"
                elif self._project_ready_for_endpoint_by_files(pd):
                    endpoint_reason = "ready_by_files"
                elif not self._project_has_pending_ve3_units(pd):
                    endpoint_reason = "queue_no_pending"
                if endpoint_reason:
                    self._log(f"[QUEUE] {code}: da du dieu kien ket thuc ({endpoint_reason}) -> finalize + endpoint", "WARN", "ve3")
                    finalize_ok = self._finalize_project_outputs(pd)
                    if finalize_ok:
                        self._complete_project_endpoint(pd, reason=endpoint_reason)
                    else:
                        self._log(f"[QUEUE] {code}: bo qua endpoint vi finalize that bai", "ERROR", "ve3")
            elif pd.exists() and self._is_project_endpoint_complete(pd):
                self._log(f"[QUEUE] {code}: endpoint da duoc xu ly truoc do, bo qua", "INFO", "ve3")

    def _queue_ve3_skip_log(self, code, reason, detail="", interval=30):
        key = f"{code}:{reason}:{detail}"
        now = _time.time()
        last = float(self.ve3_skip_log_ts.get(key, 0.0) or 0.0)
        if now - last < interval:
            return
        self.ve3_skip_log_ts[key] = now
        suffix = f" ({detail})" if detail else ""
        self._log(f"[QUEUE/VE3] {code}: skip {reason}{suffix}", "INFO", "ve3")

    def _queue_ve3_loop(self, cfg):
        try:
            while not self.queue_stop_requested:
                did_work = False
                if (_time.time() - float(getattr(self, "server_status_cache_ts", 0.0) or 0.0)) >= 3:
                    self._refresh_server_status_sync()
                pairs = self._get_server_pairs(only_available=True)
                with self.queue_lock:
                    busy_pair_ids = set(self.queue_active_pairs.keys())
                free_pairs = [p for p in pairs if p["pair_id"] not in busy_pair_ids]

                for pd in self._queue_projects_ve3():
                    if self.queue_stop_requested:
                        break
                    for stale_name in ("excel", "ve3"):
                        marker = self._queue_marker(pd, stale_name)
                        try:
                            if marker.exists() and time.time() - marker.stat().st_mtime > 24 * 3600:
                                marker.unlink()
                        except Exception:
                            pass
                    if self._is_project_endpoint_complete(pd):
                        continue
                    if self._is_project_manually_done(pd):
                        continue
                    with self.queue_lock:
                        existing_task = self.queue_ve3_tasks.get(pd.name)
                        if existing_task and existing_task.is_alive():
                            self._queue_ve3_skip_log(pd.name, "active_task")
                            continue
                        # Also check subprocess alive
                        existing_proc = self.queue_ve3_procs.get(pd.name)
                        if existing_proc and existing_proc.poll() is None:
                            self._queue_ve3_skip_log(pd.name, "active_subprocess")
                            continue
                        if pd.name in self.queue_active_excel or pd.name in self.queue_active_ve3:
                            active_reason = "excel_active" if pd.name in self.queue_active_excel else "ve3_active"
                            self._queue_ve3_skip_log(pd.name, active_reason)
                            continue
                    # Check quota wait marker — skip project if FlowKit quota exhausted
                    quota_marker = pd / ".flowkit_quota_wait"
                    if quota_marker.exists():
                        try:
                            qdata = json.loads(quota_marker.read_text(encoding="utf-8"))
                            resume_ts = qdata.get("resume_ts", 0)
                            if time.time() < resume_ts:
                                remaining = int(resume_ts - time.time())
                                self._queue_ve3_skip_log(pd.name, "quota_wait", f"{remaining}s left")
                                continue
                            quota_marker.unlink()
                            self._log(f"[QUEUE] {pd.name}: quota wait expired, resuming", "INFO", "ve3")
                        except Exception:
                            quota_marker.unlink(missing_ok=True)

                    if self._project_ready_for_endpoint_by_files(pd):
                        self._log(f"[QUEUE] {pd.name}: file da du, tu dong chot endpoint", "WARN", "ve3")
                        finalize_ok = self._finalize_project_outputs(pd)
                        if finalize_ok:
                            self._complete_project_endpoint(pd, reason="ready_by_files_loop")
                        else:
                            self._log(f"[QUEUE] {pd.name}: file da du nhung finalize loi", "ERROR", "ve3")
                        did_work = True
                        continue
                    if self._project_excel_complete(pd) and not self._project_has_pending_ve3_units(pd):
                        self._log(f"[QUEUE] {pd.name}: khong con pending, tu dong chot endpoint", "WARN", "ve3")
                        finalize_ok = self._finalize_project_outputs(pd)
                        if finalize_ok:
                            self._complete_project_endpoint(pd, reason="queue_no_pending_loop")
                        else:
                            self._log(f"[QUEUE] {pd.name}: khong pending nhung finalize loi", "ERROR", "ve3")
                        did_work = True
                        continue
                    # Retry logic: clear stale Excel lock if Excel Worker is not running
                    with self.queue_lock:
                        excel_task_running = pd.name in self.queue_active_excel

                    if not excel_task_running:
                        # Excel Worker not running - clear any stale locks
                        excel_lock_marker = self._queue_marker(pd, "excel")
                        if excel_lock_marker.exists():
                            self._log(f"[QUEUE/EXCEL] {pd.name}: detected stale excel lock marker, clearing", "WARN", "excel")
                            self._clear_queue_marker(pd, "excel")

                        # Also check if Excel file is locked - if so, wait a bit for Windows to release it
                        ep = self._project_excel_path(pd)
                        if ep.exists() and self._excel_is_locked(ep):
                            self._log(f"[QUEUE/EXCEL] {pd.name}: Excel file still locked after worker finished, waiting for release", "WARN", "excel")
                            self._queue_ve3_skip_log(pd.name, "excel_locked")
                            # Don't block the loop - just skip this iteration and check again in 30s
                            continue

                    needs_ve3 = self._project_needs_ve3(pd)
                    if not needs_ve3:
                        excel_complete = self._project_excel_complete(pd)
                        if not excel_complete:
                            detail = "missing_or_incomplete_excel"
                        else:
                            has_pending = self._project_has_pending_ve3_units(pd)
                            detail = "no_pending_units" if not has_pending else "blocked_by_lock_or_hold"
                        self._queue_ve3_skip_log(pd.name, "not_ready", detail)
                        continue
                    if not free_pairs:
                        self._queue_ve3_skip_log(pd.name, "no_free_pair")
                        continue
                    pair = self._choose_pair_for_project(pd, free_pairs)
                    if not pair:
                        self._queue_ve3_skip_log(pd.name, "no_matching_pair")
                        continue
                    did_work = True
                    with self.queue_lock:
                        existing_task = self.queue_ve3_tasks.get(pd.name)
                        if existing_task and existing_task.is_alive():
                            continue
                        existing_proc = self.queue_ve3_procs.get(pd.name)
                        if existing_proc and existing_proc.poll() is None:
                            continue
                        self.queue_active_ve3.add(pd.name)
                        self.queue_active_pairs[pair["pair_id"]] = pd.name
                        self.queue_pair_use_seq += 1
                        self.queue_pair_last_used[pair["pair_id"]] = self.queue_pair_use_seq
                    self._write_queue_marker(pd, "ve3", f"VE3 worker using pair {pair['server_name']} / {pair['flow_account_name']}")
                    task = threading.Thread(target=self._run_single_project_ve3, args=(pd, pair, cfg), daemon=True)
                    with self.queue_lock:
                        self.queue_ve3_tasks[pd.name] = task
                    task.start()
                    free_pairs = [p for p in free_pairs if p["pair_id"] != pair["pair_id"]]

                with self.queue_lock:
                    active_count = len(self.queue_ve3_tasks)
                if not did_work:
                    _time.sleep(5)
        finally:
            self._queue_thread_finished()

    def _queue_thread_finished(self):
        if threading.current_thread() is not threading.main_thread():
            self.after(1000, self._queue_thread_finished)
            return
        excel_task_threads = list((self.queue_excel_tasks or {}).values())
        ve3_task_threads = list((self.queue_ve3_tasks or {}).values())
        ve3_procs = list((self.queue_ve3_procs or {}).values())
        music_procs = list((self.queue_music_procs or {}).values())
        procs_alive = any(p and p.poll() is None for p in (*ve3_procs, *music_procs))
        alive = procs_alive or any(t and t.is_alive() for t in (self.queue_excel_thread, self.queue_ve3_thread, *excel_task_threads, *ve3_task_threads))
        if not alive:
            self.queue_running = False
            self.queue_stop_requested = False
            with self.queue_lock:
                self.queue_active_excel.clear()
                self.queue_active_ve3.clear()
                self.queue_active_pairs.clear()
                self.queue_excel_tasks.clear()
                self.queue_ve3_tasks.clear()
                self.queue_ve3_workers.clear()
                self.queue_ve3_procs.clear()
                self.queue_music_procs.clear()
                self.queue_progress_owner_code = None
                self.queue_progress_owner_pair = "-"
            self.btn_go.configure(text="RUN", fg_color="#2E7D32", hover_color="#1B5E20")
            self.pages["home"].btn_run_center.configure(text="RUN", fg_color="#2E7D32", hover_color="#1B5E20")
            self.pages["home"].lbl_active_project.configure(text="Ma dang chay: -")
            self.pages["home"].lbl_running_pair.configure(text="-")
            self._log("[QUEUE] Da dung.", "WARN")

    def _tick(self):
        if self._t0 and self.btn_st.cget("state")!="disabled":
            self.lbl_tm.configure(text=_ts(_time.time()-self._t0)); self.after(1000, self._tick)

    def _prog(self, ph, cur, tot, det=""):
        self.pages["home"].update_progress(ph, cur, tot)
        if det: self.pages["home"].lbl_cur.configure(text=f" {det}")

    def _item(self, tp, id, st, path=None, ex=None):
        g = self.pages["gen"]
        if tp=="char": g.update_char(id, st, ex)
        elif tp=="scene": g.update_scene(id, st, ex)

    def _done(self, r):
        # Nt CHY sng xanh li, nt DNG m
        self.btn_go.configure(state="normal", fg_color="#2E7D32", text_color="#FFFFFF")
        self.btn_st.configure(state="disabled", fg_color="#555", text_color="#999")
        self.pages["home"].lbl_cur.configure(text="")
        # Reload workbook  GUI c media_id mi nht
        self._reload_wb()
        tt = f" ({_ts(_time.time()-self._t0)})" if self._t0 else ""
        self._t0 = None
        if r.get("success"):
            self._log(f"Done: {r['completed']}/{r['total']}{tt}","SUCCESS")
            if self.project_dir and Path(self.project_dir).exists():
                self._complete_project_endpoint(Path(self.project_dir), reason="manual_run_success")
        else:
            e = "; ".join(r.get("errors",[])); self._log(f"End: {r['completed']}/{r['total']}{tt} {e}","ERROR" if e else "WARN")
            if self.project_dir and Path(self.project_dir).exists() and self._is_project_manually_done(Path(self.project_dir)):
                finalize_ok = self._finalize_project_outputs(Path(self.project_dir))
                if finalize_ok:
                    self._complete_project_endpoint(Path(self.project_dir), reason="manual_done_after_stop")
                else:
                    self._log(f"[QUEUE] {Path(self.project_dir).name}: bo qua endpoint vi finalize that bai", "ERROR", "ve3")

    def open_folder(self):
        t = self.project_dir if self.project_dir and self.project_dir.exists() else PROJECTS_DIR
        t.mkdir(parents=True,exist_ok=True); os.startfile(str(t))

    def _log(self, m, l="INFO", channel=None):
        with self._log_queue_lock:
            self._log_queue.append((m, l, channel))
            if self._log_flush_scheduled:
                return
            self._log_flush_scheduled = True
        delay = 80 if threading.current_thread() is threading.main_thread() else 120
        self.after(delay, self._flush_log_queue)

    def _flush_log_queue(self):
        with self._log_queue_lock:
            batch = list(self._log_queue)
            self._log_queue.clear()
            self._log_flush_scheduled = False
        if not batch:
            return
        records = []
        for m, l, channel in batch:
            if channel is None:
                text = str(m)
                if "[QUEUE/EXCEL]" in text or "MP3/SRT -> Excel" in text or "SRT -> Excel" in text or "ProgressivePromptsGenerator" in text:
                    channel = "excel"
                else:
                    channel = "ve3"
            records.append((m, l, channel))
        try:
            self.pages["home"].log_many(records)
        except Exception:
            pass

def main():
    # n ca s console trn Windows
    try:
        import ctypes
        ctypes.windll.user32.ShowWindow(ctypes.windll.kernel32.GetConsoleWindow(), 0)
    except Exception:
        pass
    app = VE3App(); app.mainloop()

if __name__ == "__main__":
    main()


