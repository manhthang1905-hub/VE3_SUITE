#!/usr/bin/env python3
"""VE3 Studio  compact, professional GUI."""

import sys, os, shutil, threading, time as _time, json, subprocess, re, unicodedata
# ⚠ CẢ HAI TÊN, CỐ Ý. File dùng `_time.…` ở chỗ này và `time.…` ở chỗ khác —
# đếm được **56 lời gọi `time.time()` trần** trong khi module chỉ import
# `time as _time`. Mỗi lời gọi đó là một `NameError` nằm chờ.
#
# Chúng sống sót lâu vì nằm trong nhánh hiếm chạy, hoặc bị `try/except` rộng
# nuốt gọn — ghi mốc `endpoint_done`, `manual_done`, dọn marker quá hạn... hỏng
# lặng lẽ chứ không kêu. Ngày 13/08/2026 một cái mới nổ ra ở `_ghi_so_luot_trang`
# và làm bài kiểm đỏ; 55 cái còn lại vẫn đang nằm im.
#
# Thêm tên `time` rẻ hơn hẳn việc sửa 56 chỗ và cầu cho không sót chỗ nào.
import time
from collections import deque
import tkinter as tk
from tkinter import filedialog, messagebox
from pathlib import Path
from datetime import datetime
from typing import Dict

_RE_PROJECT_CODE = re.compile(r'\[([A-Z0-9]+-\d+)\]|\b([A-Z0-9]+-\d+):')
_RE_QUEUE_DEBUG = re.compile(r'^\[DEBUG\]\s+[A-Z0-9]+-\d+:')

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

#: Số MÃ chạy cùng lúc khi đi toàn API, khi `settings.yaml` không nói gì.
#:
#: 8, không phải 3. Con số này NHÂN với trần MỖI MÃ mới ra tải thật đặt lên nhà
#: máy, nên phải đọc kèm `max_concurrent` (ảnh) và `shopapi_video_concurrency`
#: (video). Đo `GET /v1/me` ngày 11/08/2026 — máy chủ cấp 691 chỗ ảnh, 374 chỗ
#: video — nên bộ ba khớp trần là:
#:
#:     8 mã × 88 ảnh   = 704 ≈ 691 chỗ ảnh
#:     8 mã × 48 video = 384 ≈ 374 chỗ video
#:
#: Ba con số đó là MỘT bộ. Vặn cái này lên mà quên hai cái kia thì hoặc vượt
#: trần (phần dư nằm hàng chờ, không nhanh thêm được gì), hoặc mỗi mã ôm 384
#: luồng và máy này chết trước máy chủ.
#:
#: Đừng để nó thấp: mỗi mã chạy các pha TUẦN TỰ (ảnh xong hết mới tới video),
#: nên một mã đang dựng video là một mã không tạo ảnh nào. Số mã song song
#: chính là thứ lấp những quãng đó.
SHOPAPI_MA_SONG_SONG_MAC_DINH = 8

#: Tên thư mục kho lưu: `TL3-0413_20260813_165010` = mã + ngày + giờ.
_RE_DUOI_KHO = re.compile(r"_\d{8}_\d{6}$")


def _ma_goc(ten_thu_muc):
    """`TL3-0413_20260813_165010` -> `TL3-0413`. Tên thường thì giữ nguyên.

    Dùng để KHỬ TRÙNG khi đếm sản lượng: một mã đã xong tồn tại ở CẢ `PROJECTS`
    lẫn `old/`. Bản trước cắt bằng `rsplit("_", 1)` — chỉ rụng được `_165010`,
    còn lại `TL3-0413_20260813`, không khớp với `TL3-0413` bên kia. Đo
    13/08/2026: **24 mã** nằm ở cả hai nơi và đều bị đếm hai lần.
    """
    return _RE_DUOI_KHO.sub("", str(ten_thu_muc))


#: Nơi ghi nhật ký ra ĐĨA. Xem `ghi_log_file`.
LOG_DIR = SUITE_ROOT / "logs"
_log_file_lock = threading.Lock()
_log_file_handle = [None, ""]   # [file đang mở, ngày của nó]


def ghi_log_file(msg, level="INFO", channel=None):
    """Chép mỗi dòng nhật ký ra `logs/ve3-YYYYMMDD.log`.

    ═══ VÌ SAO PHẢI CÓ, VÀ VÌ SAO NÓ TỐN CẢ MỘT BUỔI ═══

    Trước 11/08/2026 VE3 KHÔNG ghi nhật ký ra file nào. Mọi dòng chỉ chảy vào
    khung log trên cửa sổ — cửa sổ đóng là mất sạch, cửa sổ chết thì mất luôn cả
    lý do nó chết.

    Cái giá đo được: chiều 11/08, tool chạy rồi dừng hẳn (7 mã giữ lock, 0 job,
    0 file trong 90 giây). Cả GUI lẫn 8 worker đều đã thoát. Không một dòng nào
    còn lại để biết vì sao — nên việc chẩn đoán phải suy từ log của MÁY CHỦ, tức
    là đoán từ dấu vết gián tiếp.

    Một tool chạy hàng giờ không người trông mà không để lại vết là một tool
    không gỡ lỗi được. Đây là món nợ, không phải tính năng thêm.
    """
    try:
        from datetime import datetime as _dt
        now = _dt.now()
        ngay = now.strftime("%Y%m%d")
        with _log_file_lock:
            if _log_file_handle[1] != ngay:
                if _log_file_handle[0] is not None:
                    try: _log_file_handle[0].close()
                    except Exception: pass
                LOG_DIR.mkdir(parents=True, exist_ok=True)
                _log_file_handle[0] = open(LOG_DIR / f"ve3-{ngay}.log", "a",
                                           encoding="utf-8", errors="replace")
                _log_file_handle[1] = ngay
            f = _log_file_handle[0]
            f.write("{0} {1:<7} {2}{3}\n".format(
                now.strftime("%H:%M:%S"), str(level)[:7],
                "[{0}] ".format(channel) if channel else "", msg))
            f.flush()
    except Exception:
        # Ghi log KHÔNG BAO GIỜ được làm chết thứ nó đang ghi lại.
        pass

#: Nhớ câu trả lời "máy này đã có khoá shopapi chưa" trong ngần này giây.
#:
#: `shopapi_common.doc_khoa()` ĐỌC FILE mỗi lần gọi, không nhớ gì. Từ 11/08/2026
#: `_chi_dung_shopapi` được hỏi ở nhiều chỗ nóng hơn hẳn trước — mỗi lần dựng
#: pair, và mỗi mã một lần trong vòng hàng chờ (75 mã, 5 giây một vòng). Không
#: nhớ thì đó là hàng chục lượt giải mã kho khoá mỗi giây, ngay trong luồng vẽ
#: giao diện.
#:
#: 5 giây: khoá API không đổi giữa hai nhịp hàng chờ, mà cũng không ai phải chờ
#: quá 5 giây để tool nhận ra vừa dán khoá mới.
_KHOA_TTL = 5.0
_khoa_cache = {"den": 0.0, "co": False}


def quen_khoa_shopapi():
    """Vứt câu trả lời đã nhớ — gọi sau khi lưu/xoá khoá, và trong bài kiểm.

    Có nó thì người vừa dán khoá xong không phải chờ hết `_KHOA_TTL`, và bài
    kiểm không bị dính câu trả lời của bài chạy trước.
    """
    _khoa_cache["den"] = 0.0


#: Hai bảng chọn backend trên Settings. Ở CẤP MODULE, cố ý.
#:
#: ⚠ HAI BỘ TỪ VỰNG NÀY KHÔNG GIỐNG NHAU, và đó là cái bẫy đã cắn thật.
#: Video dùng `veo3top_b_pool`; ẢNH dùng `pool`. Video dùng `server`; ảnh dùng
#: `""`. Ai nhìn lướt sẽ tưởng chung một danh sách.
#:
#: Ngày 14/08/2026 tôi viết `_chuyen_may_cu_sang_api` với MỘT danh sách gõ tay
#: — toàn giá trị của video. Máy thứ hai lên đúng bản 528, video được chuyển
#: sang shopapi, còn `veo3top_image_mode: pool` thì không khớp phần tử nào nên
#: đứng yên. `cau_hinh_toan_api` đòi CẢ HAI, nên nó trả False và giao diện vẫn
#: nguyên `TRẠM ẢNH`/`TRẠM VIDEO` — sửa xong mà y như chưa sửa.
#:
#: Nên giờ danh sách "backend cũ" được SUY RA từ chính hai bảng này (mọi thứ
#: khác `shopapi`), không ai gõ tay nữa. Thêm backend mới vào bảng là phần
#: chuyển đường tự biết.
BACKEND_VIDEO = {
    "API shopapi": "shopapi", "Server": "server", "NanoPic": "nanopic",
    "FlowKit": "flowkit", "Combined": "combined", "Veo3top": "veo3top",
    "Veo3top-B": "veo3top_b", "Veo3top-B-Ultra": "veo3top_b_ultra",
    "Veo3top-B-Pool (nha may chung)": "veo3top_b_pool",
}
BACKEND_ANH = {
    "API shopapi (anh)": "shopapi", "Mac dinh": "", "Veo3top-B (anh)": "blank",
    "Veo3top-B-Ultra (anh)": "account", "Veo3top-B-Pool (anh)": "pool",
}
#: Mọi backend KHÔNG phải shopapi = đường Chrome/VM cũ.
BACKEND_VIDEO_CU = frozenset(v for v in BACKEND_VIDEO.values() if v != "shopapi")
BACKEND_ANH_CU = frozenset(v for v in BACKEND_ANH.values() if v != "shopapi")

#: Phiên bản của phép chuyển máy cũ sang API. TĂNG SỐ NÀY mỗi khi sửa
#: `_chuyen_may_cu_sang_api` theo hướng máy đã chuyển hụt cần được chuyển lại.
#:
#:   1 — bản đầu (14/08/2026). Chuyển hụt: một danh sách backend gõ tay, toàn
#:       giá trị của VIDEO, nên `veo3top_image_mode: pool` không khớp gì và
#:       đứng nguyên. Máy dừng ở `ảnh=pool · video=shopapi`.
#:   2 — suy danh sách ra từ `BACKEND_VIDEO`/`BACKEND_ANH`, hai bộ từ vựng
#:       riêng. Chuyển lại những máy đã dính bản 1.
CHUYEN_API_PHIEN = 2

#: ═══ SÀN SONG SONG CHO MÁY CŨ — 14/08/2026 ═══
#:
#: Chuyển được sang API rồi vẫn có thể chạy chậm như chưa chuyển, vì ba con số
#: dưới đây nằm trong `settings.yaml` — file bị `PROTECTED_PATHS` **và**
#: `GIT_PROTECTED_FILES` **và** `.gitignore` chặn, đúng ba lớp đã ngăn phép
#: chuyển backend. Máy cũ giữ giá trị thời chạy Chrome, khi một "mã" chỉ dám
#: giữ vài chỗ vì mỗi chỗ là một cửa sổ Chrome thật.
#:
#: Đo trên máy chủ thật 14/08/2026, hai khách đang chạy::
#:
#:      máy chủ mời mỗi khách : ~979 chỗ ảnh · ~288 chỗ video
#:      khách GIỮ cùng lúc    :     2 chỗ ảnh (đỉnh 2, trung bình 1,2–2,0)
#:
#: Tức khai thác **0,2%** phần ảnh. Ba worker `idle`, hàng chờ 1–2 giây, ảnh
#: p50 32 giây — máy chủ rảnh suốt trong khi chủ dự án thấy "1 phút 1 ảnh".
#: Con số 2 đó chính là mặc định thời Chrome còn sót lại.
#:
#: Sàn dưới đây KHÔNG phải số bịa: `8 mã × 40 ảnh = 320 chỗ`, `× 16 video = 128
#: chỗ` là cấu hình ĐÃ CHẠY THẬT (xem `_load_config`). Cố ý không dùng 0 ("theo
#: trần máy chủ") cho máy cũ: 8 mã × 979 chỗ là con số đã giết nhà máy 9 lần
#: ngày 12/08/2026.
SAN_ANH_MOI_MA = 40
SAN_VIDEO_MOI_MA = 16
SAN_MA_SONG_SONG = 8

#: Phiên bản của phép NÂNG TRẦN. Tăng khi sửa `_nang_tran_song_song_may_cu`
#: theo hướng máy đã nâng hụt cần nâng lại. Dùng số chứ không dùng cờ `True`,
#: vì đúng lý do đã trả giá ở `CHUYEN_API_PHIEN`: cờ một-bit không phân biệt
#: nổi "đã chạy bằng bản hỏng" với "đã chạy bằng bản đúng".
NANG_TRAN_PHIEN = 1


def _phien_da_chuyen(cfg):
    """Cấu hình này đã qua phép chuyển ở phiên bản nào? Chưa qua → 0.

    Đọc được cả cờ `True` của bản đầu — nó tương đương phiên bản 1.
    """
    v = cfg.get("da_chuyen_sang_shopapi")
    if v is True:
        return 1
    try:
        return int(v)
    except (TypeError, ValueError):
        return 0


def cau_hinh_toan_api(cfg):
    """Cấu hình CHỌN đi API cho cả ảnh lẫn video? (KHÔNG hỏi tới khoá)

    Tách khỏi `che_do_toan_api` vì hai câu hỏi khác nhau và cần trả lời khác
    nhau: *"người dùng muốn đi đường nào"* và *"đi được chưa"*.

    Gộp hai câu đó làm một thì máy chưa dán khoá sẽ lặng lẽ hiện giao diện của
    đường Chrome — `TRẠM ẢNH · acc Pro`, `TRẠM VIDEO · acc Ultra`, tám ô đứng
    `-` — trong khi cấu hình đã là API. Người dùng không có cách nào biết thứ
    duy nhất còn thiếu là dán khoá. Đã xảy ra thật trên máy thứ hai ngày
    14/08/2026: code mới đã về, số phiên bản đúng, mà màn hình vẫn y như cũ.
    """
    if (cfg.get("veo3top_image_mode") or "").strip().lower() != "shopapi":
        return False
    video = (cfg.get("generation_backend") or cfg.get("generation_mode") or "").strip().lower()
    return video == "shopapi"


def che_do_toan_api(cfg):
    """CẢ ảnh LẪN video đều đi API shopapi, và máy đã có khoá?

    ⚠ HÀM NÀY Ở CẤP MODULE, CỐ Ý. Bản trước nó là phương thức của `VE3App`, và
    `SettingsPage` gọi `self._chi_dung_shopapi(...)` — một lớp KHÁC, không có
    phương thức đó. Lời gọi ném `AttributeError`, `try/except` bao quanh nuốt
    gọn, và giao diện lặng lẽ coi như "không đi API": tám núm chết vẫn sáng như
    thường, nhãn vẫn nói dối, không một dòng lỗi nào.

    Đó đúng là kiểu hỏng mà cả phiên 11/08/2026 đi chữa — thứ sai mà không có
    triệu chứng. Để hàm ở cấp module thì không lớp nào gọi hụt được nữa.
    """
    return cau_hinh_toan_api(cfg) and _co_khoa_shopapi()


def _co_khoa_shopapi():
    """Máy này đã có khoá API shopapi chưa? (nhớ trong `_KHOA_TTL` giây)"""
    now = _time.time()
    if now < _khoa_cache["den"]:
        return _khoa_cache["co"]
    try:
        import sys as _sys
        _engine = str(SUITE_ROOT / "veo3top_engine")
        if _engine not in _sys.path:
            _sys.path.insert(0, _engine)
        import shopapi_common as _sc
        co = bool((_sc.doc_khoa() or ("", ""))[0])
    except Exception:
        # Thiếu module / kho khoá hỏng -> coi như CHƯA có khoá, giữ nguyên cổng
        # cũ. Thà bắt khai token còn hơn thả cho chạy rồi chết giữa lượt.
        co = False
    _khoa_cache["den"] = now + _KHOA_TTL
    _khoa_cache["co"] = co
    return co
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
#: Màu ô "vấn đề" của hai bảng trạm. Ở CẤP MODULE vì hai nơi dùng chung:
#: `_poll_pool_health._work_body` (đường pool) và `_so_lieu_api_len_tram`
#: (đường API) — cái sau là phương thức riêng, không thấy biến cục bộ của cái
#: trước. Định nghĩa hai lần là hai bảng màu lệch nhau lúc nào không hay.
GREEN, ORANGE, RED, GRAY = "#0A7", "#FF8C00", "#D22", T3
TW, TH = 110, 74     # thumb
SW = 175              # sidebar width
POOL_POLL_MS = int(os.environ.get("VE3_POOL_POLL_MS", "15000") or "15000")   # nhịp poll /health tiles (15s) - giãn cho đỡ đơ

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
        self._mk_projects()      # 1. Danh sch m (kem hang tiles so lieu pool ngay duoi thong ke)
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
        c.grid_rowconfigure(4, weight=1)  # projects_list expandable (row 4 sau overview + tiles pool)

        # Overview section - matching tool color scheme
        overview = ctk.CTkFrame(c, fg_color=CD, corner_radius=8, border_width=1, border_color=BD)
        overview.grid(row=1, column=0, padx=8, pady=(4,4), sticky="ew")
        overview.grid_columnconfigure((0,1,2,3,4,5,6,7), weight=1)

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

        # Anh hom nay (do luong khai thac tao anh - dem file .png tao trong ngay)
        f7 = ctk.CTkFrame(overview, fg_color="#EAF4FF", corner_radius=6, border_width=1, border_color=BD)
        f7.grid(row=0, column=6, padx=4, pady=8, sticky="ew")
        ctk.CTkLabel(f7, text="ẢNH HÔM NAY", font=("",9,"bold"), text_color=T2).pack(pady=(6,2))
        self.lbl_overview_img_today = ctk.CTkLabel(f7, text="0", font=("",20,"bold"), text_color="#0A7")
        self.lbl_overview_img_today.pack(pady=(0,6))

        # Video hom nay (do luong khai thac tao video - dem file .mp4 tao trong ngay)
        f8 = ctk.CTkFrame(overview, fg_color="#FFF0E6", corner_radius=6, border_width=1, border_color=BD)
        f8.grid(row=0, column=7, padx=4, pady=8, sticky="ew")
        ctk.CTkLabel(f8, text="VIDEO HÔM NAY", font=("",9,"bold"), text_color=T2).pack(pady=(6,2))
        self.lbl_overview_vid_today = ctk.CTkLabel(f8, text="0", font=("",20,"bold"), text_color="#E60")
        self.lbl_overview_vid_today.pack(pady=(0,6))

        # ===== SỐ LIỆU POOL — 2 TRẠM: TRÁI = ẢNH (acc Pro), PHẢI = VIDEO (acc Ultra). Mỗi trạm 8 ô chỉ số, tự cập nhật =====
        ov2 = ctk.CTkFrame(c, fg_color=CD, corner_radius=8, border_width=0)
        ov2.grid(row=2, column=0, padx=8, pady=(0,2), sticky="ew")
        ov2.grid_columnconfigure((0, 1), weight=1, uniform="pool")
        self.pool_tiles = {}
        #: Nhãn CHÚ THÍCH của từng ô + nhãn tiêu đề mỗi bảng. Giữ tham chiếu vì
        #: chúng phải ĐỔI THEO CHẾ ĐỘ: đi API shopapi thì "ĐÃ LOGIN", "CÁCH LY
        #: 429", "SUBMIT/ACC"... không còn nghĩa gì — không account nào login,
        #: không Chrome nào chạy. Xem `_dat_nhan_tram`.
        self.pool_caps = {}
        self.pool_titles = {}

        def _mk_pool_panel(col, title, accent, headbg, cells, issue_key):
            panel = ctk.CTkFrame(ov2, fg_color=CD, corner_radius=8, border_width=1, border_color=accent)
            panel.grid(row=0, column=col, padx=(0, 4) if col == 0 else (4, 0), pady=4, sticky="nsew")
            for cc in range(4):
                panel.grid_columnconfigure(cc, weight=1, uniform="cell")
            head = ctk.CTkFrame(panel, fg_color=headbg, corner_radius=6)
            head.grid(row=0, column=0, columnspan=4, sticky="ew", padx=4, pady=(4, 2))
            tl = ctk.CTkLabel(head, text=title, font=("", 12, "bold"), text_color=accent)
            tl.pack(side="left", padx=8, pady=3)
            self.pool_titles[issue_key] = tl
            for i, (key, label) in enumerate(cells):
                r, cc = 1 + i // 4, i % 4
                cell = ctk.CTkFrame(panel, fg_color="#F7F8FA", corner_radius=6, border_width=1, border_color=BD2)
                cell.grid(row=r, column=cc, padx=3, pady=3, sticky="nsew")
                cap = ctk.CTkLabel(cell, text=label, font=("", 8, "bold"), text_color=T3)
                cap.pack(pady=(5, 0))
                lb = ctk.CTkLabel(cell, text="-", font=("", 15, "bold"), text_color=accent)
                lb.pack(pady=(0, 5))
                self.pool_tiles[key] = lb
                self.pool_caps[key] = cap
            # THANH VẤN ĐỀ: vì sao ảnh/video/giờ đang chậm -> để fix ngay
            issue_r = 1 + (len(cells) + 3) // 4
            issue = ctk.CTkLabel(panel, text="…", font=("", 10, "bold"), text_color=T3,
                                 anchor="w", justify="left", wraplength=430)
            issue.grid(row=issue_r, column=0, columnspan=4, sticky="ew", padx=8, pady=(1, 6))
            self.pool_tiles[issue_key] = issue

        _mk_pool_panel(0, "🖼️ TRẠM ẢNH · acc Pro", "#1a73e8", "#EAF4FF", [
            ("img_login", "ĐÃ LOGIN"), ("img_run", "ĐANG CHẠY"), ("img_q429", "CÁCH LY 429"), ("img_nghi", "NGHỈ KHÁC"),
            ("img_dead", "CHẾT"), ("img_rate", "ẢNH / GIỜ"), ("img_done", "TỔNG ẢNH"), ("img_codes", "MÃ × LUỒNG")],
            "img_issue")
        _mk_pool_panel(1, "🎬 TRẠM VIDEO · acc Ultra", "#E60", "#FFF0E6", [
            ("vid_acc", "KHAI THÁC"), ("vid_rest", "CÁCH LY 429"), ("vid_heal", "ĐANG CHỮA"), ("vid_queue", "HÀNG ĐỢI"),
            ("vid_submit", "SUBMIT/ACC"), ("vid_rate", "VIDEO / GIỜ"), ("vid_done", "TỔNG VIDEO"), ("vid_codes", "MÃ CHẠY")],
            "vid_issue")

        self._dat_nhan_tram()

        self.pool_status_lbl = ctk.CTkLabel(c, text="Đang đọc pool...", font=("",10), text_color=T2, anchor="w", justify="left")
        self.pool_status_lbl.grid(row=3, column=0, padx=12, pady=(0,4), sticky="w")

        self.projects_list = ctk.CTkScrollableFrame(c, height=320, fg_color="#F3F5F7", corner_radius=6, border_width=1, border_color=BD2)   # thấp gọn -> log hiện luôn không phải kéo (list tự cuộn trong nó)
        self.projects_list.grid(row=4, column=0, padx=8, pady=(0,8), sticky="nsew")
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

        # Tab LOG nhà máy ẢNH + VIDEO (tail file log service -> biết pool đang làm gì)
        self._pool_log_files = {}
        for _name, _fn in [("Pool ANH", ".veo3top_imgpool.log"), ("Pool VIDEO", ".veo3top_pool.log")]:
            _tab = self.log_tabs.add(_name)
            _tab.grid_columnconfigure(0, weight=1); _tab.grid_rowconfigure(0, weight=1)
            _box = ctk.CTkTextbox(_tab, font=("Consolas",10), fg_color="#1A1A1A", text_color="#9F9", corner_radius=4, wrap="word")
            _box.grid(row=0, column=0, sticky="nsew", padx=0, pady=0); _box.configure(state="disabled")
            _p = os.path.join(SUITE_ROOT, _fn)
            # BẮT ĐẦU TỪ CUỐI FILE: last_size = kích thước hiện tại -> panel CHỈ hiện log MỚI (ghi ra sau khi mở tool),
            # KHÔNG kéo lại log cũ của lần chạy trước (user: "log mới khi mới chạy tool").
            try: _sz0 = os.path.getsize(_p) if os.path.exists(_p) else 0
            except Exception: _sz0 = 0
            self._pool_log_files[_name] = [_p, _box, _sz0]  # [path, box, last_size]

        # Số liệu pool hiện ở TILES dưới Projects (_mk_pool_stats) -> chỉ cần khởi động vòng poll.
        self._poll_pool_health()

        self._tail_pool_logs()

        # Dictionary to store log boxes for each project code
        self.log_project_boxes = {}

        # Default to VE3 tab at startup.
        try:
            self.log_tabs.set("VE3")
        except Exception:
            pass
        self.log_tabs.grid_remove()

    def _tail_pool_logs(self):
        """Đọc thêm phần mới của file log pool ẢNH/VIDEO -> hiện live (biết pool đang làm gì). Chạy mỗi 2s."""
        try:
            for name, item in getattr(self, "_pool_log_files", {}).items():
                path, box, last = item
                try:
                    if not os.path.exists(path):
                        continue
                    sz = os.path.getsize(path)
                    if sz < last:      # file bị xoá/ghi đè -> đọc lại từ đầu
                        last = 0
                    if sz > last:
                        with open(path, "r", encoding="utf-8", errors="replace") as f:
                            f.seek(last); new = f.read()
                        item[2] = sz
                        if new.strip():
                            box.configure(state="normal")
                            box.insert("end", new)
                            # cắt bớt giữ ~500 dòng cuối
                            try:
                                n = int(box.index("end-1c").split(".")[0])
                                if n > 500:
                                    box.delete("1.0", f"{n-500}.0")
                            except Exception:
                                pass
                            box.see("end"); box.configure(state="disabled")
                except Exception:
                    pass
        finally:
            try: self.after(2000, self._tail_pool_logs)
            except Exception: pass

    def _poll_pool_health(self):
        """Poll /health pool ẢNH (8789) + VIDEO (8788) -> cập nhật TILES + dòng trạng thái. Mỗi 5s.
        FETCH CHẠY NỀN (thread): urlopen tới 2s x2 + _compute_pool_capacity -> KHÔNG được chặn main thread (gây ĐƠ GUI).
        Widget chỉ cập nhật qua self.after(0, ...) trên main thread."""
        import threading

        # GUARD: thread poll trước chưa xong (pool chậm) -> KHÔNG spawn thêm (chống chồng chất thread) -> lên lịch lại
        if getattr(self, "_pool_poll_busy", False):
            try: self.after(POOL_POLL_MS, self._poll_pool_health)
            except Exception: pass
            return
        self._pool_poll_busy = True

        def _work_body():
            import urllib.request, json as _json

            # ═══ ĐI API shopapi -> ĐỌC `/v1/me`, KHÔNG ĐỌC `/health` CỦA POOL ═══
            #
            # Hai nhà máy pool (8789/8788) chỉ tồn tại ở đường Chrome. Ở chế độ
            # API chúng không được bật, nên vòng poll cũ chỉ nhận `None` và cả
            # 16 ô đứng `-` — người vận hành nhìn vào một bảng chết trong khi
            # máy chủ đang có đủ số để trả lời mọi câu hỏi.
            try:
                if self._dat_nhan_tram():
                    vals, st = self._so_lieu_api_len_tram()
                    st.append("⟳ {0}".format(_time.strftime("%H:%M:%S")))
                    try:
                        self.after(0, lambda vv=vals, s="     ".join(st):
                                   self._apply_pool_health(vv, s))
                    except Exception:
                        pass
                    return
            except Exception as e:
                try:
                    self.after(0, lambda m=str(e), t=type(e).__name__:
                               self._apply_pool_health(
                                   {}, "API shopapi: khong doc duoc /v1/me ({0}: {1})".format(t, m)))
                except Exception:
                    pass
                return

            def _get(port):
                try:
                    r = urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=4)
                    return _json.loads(r.read().decode("utf-8", "replace"))
                except Exception:
                    return None

            vals = {}     # key tile -> text (áp trên main thread)
            status = []
            # nhà máy tự tính (số mã + luồng) — cũng đọc /health nên chạy trong thread luôn
            try:
                cap = self.app._compute_pool_capacity()
                cfgd = getattr(self.app, "config_data", {}) or {}
                _ic = int(cfgd.get("max_concurrent_image_codes", 0) or 0)
                _vc = int(cfgd.get("max_concurrent_video_codes", 0) or 0)
                img_codes = _ic if _ic > 0 else cap.get("img_codes", "-")
                vid_codes = _vc if _vc > 0 else cap.get("vid_codes", "-")
            except Exception:
                cap = {}; img_codes = "-"; vid_codes = "-"

            # GREEN/ORANGE/RED/GRAY nay o CAP MODULE (xem dau file): bang mau cua
            # o "van de" con duoc `_so_lieu_api_len_tram` dung, ma ham do nam
            # ngoai `_work_body` nen khong thay duoc bien cuc bo.
            # ---------- ẢNH ----------
            h = _get(8789)
            if h:
                self._img_hfail = 0   # /health OK -> reset đếm fail
                try:
                    act = h.get("active") or []
                    mw = {}
                    for a in act:
                        if not isinstance(a, dict): continue
                        for m, n in (a.get("model_wins") or {}).items():
                            mw[m] = mw.get(m, 0) + n
                    top_model = max(mw.items(), key=lambda x: x[1])[0] if mw else "-"
                    _tot = int(h.get('candidates', 0) or 0); _rest = int(h.get('known_resting', 0) or 0); _dead = int(h.get('known_dead', 0) or 0)
                    _rate = h.get('image_per_hour', 0); _qb = h.get("quota_blocked"); _done = h.get('done', 0)
                    _run = len(act)                                       # ĐANG CHẠY: account trong slot ngay bây giờ
                    _cfg = int(h.get('configured', _tot) or _tot)
                    _login_done = int(h.get('logged_in', _tot) or _tot)   # số ĐÃ login (live)
                    _login_gap = max(0, _cfg - _login_done)
                    # TÁCH lý do nghỉ: 429 THẬT vs nghỉ khác (xoay tua/reCAPTCHA). Pool cũ chưa có -> gom vào 'nghỉ khác'.
                    _rq = h.get('resting_quota', None)
                    if _rq is None:
                        _q429 = 0; _nghi = _rest      # pool cũ: KHÔNG dám nhận 429 -> để 'nghỉ khác'
                    else:
                        _q429 = int(_rq or 0); _nghi = int(h.get('resting_cap', 0) or 0) + int(h.get('resting_other', 0) or 0)
                    vals["img_login"] = f"{_login_done}/{_cfg}"
                    vals["img_run"] = _run
                    vals["img_q429"] = _q429
                    vals["img_nghi"] = _nghi
                    vals["img_dead"] = _dead
                    vals["img_rate"] = _rate
                    vals["img_done"] = _done
                    vals["img_codes"] = f"{img_codes}×{cap.get('img_per', '?')}"
                    # CHẨN ĐOÁN: vì sao ảnh/giờ chậm
                    if _qb:
                        _iss = ("⛔ Quota reCAPTCHA ảnh CẠN toàn cục — ngừng đốt, tự chạy lại khi Google mở", RED)
                    elif _cfg and _q429 >= max(3, _cfg * 0.5):
                        _iss = (f"⚠️ {_q429}/{_cfg} acc hết quota THẬT cả 3 model (nghỉ) — chờ hồi / dùng Ultra", ORANGE)
                    elif _cfg and _dead >= max(3, _cfg * 0.3):
                        _iss = (f"⚠️ {_dead}/{_cfg} acc chết (cookie hết hạn) — đang login lại (5 chrome/lúc)", ORANGE)
                    elif _login_gap > 0:
                        _iss = (f"🔐 đã login {_login_done}/{_cfg} acc — nhà máy TỰ login thêm {_login_gap} (5 chrome/lúc) để tối đa hóa", ORANGE)
                    elif _run == 0 and _nghi > 0:
                        _iss = (f"⏳ {_nghi} acc đang nghỉ ngắn (xoay tua/reCAPTCHA) — sẽ chạy lại; thêm acc để liên tục", GRAY)
                    elif not _rate:
                        _iss = ("⏳ Đang khởi động / chờ job ảnh (chưa ra ảnh)", GRAY)
                    else:
                        _iss = (f"✅ Chạy tốt — {_run} acc đang chạy, {_rate} ảnh/giờ, model {top_model}", GREEN)
                    vals["img_issue"] = _iss
                    status.append(f"🖼️ Pool ảnh: ra {_done} | chạy {_run} | 429thật {_q429} | model {top_model} | quota {'⛔ cạn' if _qb else '✅ còn'}")
                except Exception:
                    status.append("🖼️ Pool ảnh: ⚠️ /health dữ liệu lạ")
            else:
                self._img_hfail = getattr(self, "_img_hfail", 0) + 1
                if self._img_hfail >= 3:   # 3 lần liên tiếp (~45s) không phản hồi -> mới coi là tắt (hiện ⏸)
                    for k in ("img_login", "img_run", "img_q429", "img_nghi", "img_dead", "img_rate", "img_done", "img_codes"): vals[k] = "⏸"
                    vals["img_issue"] = ("⏸ Pool ảnh chưa chạy (chưa tới phase ảnh / pool tắt)", GRAY)
                    status.append("🖼️ Pool ảnh: ⏸ chưa chạy")
                else:                       # fail lẻ tẻ -> GIỮ số cũ (không nháy về ⏸)
                    status.append(f"🖼️ Pool ảnh: /health chậm, giữ số cũ (lần {self._img_hfail}/3)")
            # ---------- VIDEO ----------
            v = _get(8788)
            if v:
                self._vid_hfail = 0   # /health OK -> reset đếm fail
                try:
                    accs = [a for a in (v.get("accounts") or []) if isinstance(a, dict)]
                    resting = [a for a in accs if (a.get("resting_in") or 0) > 1]
                    q429 = sum(1 for a in resting if "quota" in str(a.get("last_kind", "")).lower())
                    _heal = max(0, len(resting) - q429)   # nghỉ nhưng KHÔNG do quota -> đang chữa login/khác
                    _thr = sum(1 for a in accs if "throttle" in str(a.get("last_kind", "")).lower())
                    _vtot = len(accs); _vwork = max(0, _vtot - len(resting))
                    _lims = [int(a.get("submit_limit", 0) or 0) for a in accs if a.get("submit_limit")]
                    _submit_avg = round(sum(_lims) / len(_lims), 1) if _lims else "-"
                    _vq = int(v.get('queue', 0) or 0); _vrate = v.get('video_per_hour', 0); _vdone = v.get('done', 0)
                    vals["vid_acc"] = f"{_vwork}/{_vtot}"
                    vals["vid_rest"] = q429
                    vals["vid_heal"] = _heal
                    vals["vid_queue"] = _vq
                    vals["vid_submit"] = _submit_avg
                    vals["vid_rate"] = _vrate
                    vals["vid_done"] = _vdone
                    vals["vid_codes"] = f"{vid_codes} mã"
                    # CHẨN ĐOÁN: vì sao video/giờ chậm
                    if _vwork == 0:
                        _iss = (f"⛔ 0 Ultra khai thác — {q429} cách ly 429 + {_heal} đang chữa", RED)
                    elif q429 >= 1:
                        _iss = (f"⛔ {q429}/{_vtot} Ultra hết quota (nghỉ 6h) — còn {_vwork} chạy, video/giờ giảm", ORANGE)
                    elif _vq == 0 and not _vrate:
                        _iss = ("⏳ Hàng đợi trống — CHỜ trạm ảnh xong mới có video (không phải lỗi)", GRAY)
                    elif _thr >= max(2, _vtot * 0.3):
                        _iss = (f"⚠️ {_thr} acc bị throttle (bắn nhanh quá) — submit tự giảm (AIMD), sẽ hồi", ORANGE)
                    elif _heal >= 1:
                        _iss = (f"⚠️ {_heal} acc đang chữa login (401/cookie) — nền, không chặn", ORANGE)
                    else:
                        _iss = (f"✅ Chạy tốt — {_vwork}/{_vtot} Ultra, {_vrate} video/giờ, hàng đợi {_vq}", GREEN)
                    vals["vid_issue"] = _iss
                    status.append(f"🎬 Pool video: ra {_vdone} | hàng đợi {_vq}")
                except Exception:
                    status.append("🎬 Pool video: ⚠️ /health dữ liệu lạ")
            else:
                self._vid_hfail = getattr(self, "_vid_hfail", 0) + 1
                if self._vid_hfail >= 3:   # 3 lần liên tiếp không phản hồi -> mới ⏸
                    for k in ("vid_acc", "vid_rest", "vid_heal", "vid_queue", "vid_submit", "vid_rate", "vid_done", "vid_codes"): vals[k] = "⏸"
                    vals["vid_issue"] = ("⏸ Pool video chưa chạy (chưa tới phase video)", GRAY)
                    status.append("🎬 Pool video: ⏸ chưa chạy")
                else:                       # fail lẻ tẻ -> GIỮ số cũ
                    status.append(f"🎬 Pool video: /health chậm, giữ số cũ (lần {self._vid_hfail}/3)")
            status.append(f"⟳ {_time.strftime('%H:%M:%S')}")
            # ÁP KẾT QUẢ trên MAIN THREAD (widget chỉ đụng từ main thread). Reset busy + reschedule -> ở _work finally.
            try: self.after(0, lambda vv=vals, st="     ".join(status): self._apply_pool_health(vv, st))
            except Exception: pass

        def _work():
            # SELF-HEAL: dù _work_body CRASH (pool chưa sẵn lúc startup, /health lạ...) VẪN reset busy + reschedule
            # -> poll KHÔNG chết vĩnh viễn (trước đây crash -> _pool_poll_busy kẹt True -> tiles đứng '-' mãi).
            try:
                _work_body()
            except Exception:
                pass
            finally:
                self._pool_poll_busy = False
                try: self.after(POOL_POLL_MS, self._poll_pool_health)
                except Exception: pass

        threading.Thread(target=_work, daemon=True, name="pool-health").start()

    #: Nhãn 8 ô mỗi trạm ở CHẾ ĐỘ API shopapi.
    #:
    #: ═══ CHỈ ĐO THỨ CỦA MÌNH, KHÔNG BÊ SỐ NỘI BỘ CỦA MÁY CHỦ LÊN ═══
    #:
    #: Bộ nhãn cũ ("ĐÃ LOGIN", "CÁCH LY 429", "ĐANG CHỮA", "SUBMIT/ACC") đo sức
    #: khoẻ kho Chrome/Gmail của đường pool — đi API thì cả tám ô đứng `-`.
    #:
    #: Bản thay thế ĐẦU TIÊN cũng sai, chỉ theo kiểu khác: nó bày ra `TÀI KHOẢN
    #: 94/96`, `MÁY XỬ LÝ 1`, `SỨC CHỨA 1088`. Đó là kho và đội máy **của máy
    #: chủ** — VE3 không sở hữu, không điều khiển, và biết cũng không làm được
    #: gì. Bày số của người khác lên bảng điều khiển của mình thì bảng đầy số mà
    #: vẫn không trả lời được câu duy nhất cần trả lời: *ta đang làm được bao
    #: nhiêu, và cái gì đang chặn ta?*
    #:
    #: Tám ô dưới đây chỉ gồm hai loại:
    #:   * TRẠNG THÁI CỦA TA — mã đang chạy, ta xin bao nhiêu, job CỦA TA đang
    #:     chạy/xếp hàng, sản lượng thật đếm từ file trên đĩa, việc còn lại, ETA;
    #:   * RANH GIỚI HỢP ĐỒNG — trần máy chủ cấp cho ta, thứ ta phải tôn trọng.
    #:
    #: Bốn ô đầu xếp theo đúng chuỗi nhân quả để đọc một dòng là ra thủ phạm:
    #: **mã đang chạy → ta xin → thật sự chạy → xếp hàng**. Mã ít thì xin ít; xin
    #: nhiều mà chạy ít và không xếp hàng thì tool không gửi được; xếp hàng dài
    #: thì nhà máy ngộp. Ba nguyên nhân khác hẳn nhau, trước đây nhìn từ ngoài
    #: giống hệt nhau.
    NHAN_TRAM_API = {
        # HÀNG 1 — CHUỖI CUNG, đọc trái sang phải là ra thủ phạm.
        "img_login": "MÃ Ở PHA NÀY",  "img_run": "XIN / TRẦN",
        "img_q429": "JOB ĐANG CHẠY",  "img_nghi": "JOB XẾP HÀNG",
        # HÀNG 2 — KẾT QUẢ: đang ra bao nhiêu, hỏng bao nhiêu, còn bao nhiêu, bao giờ xong.
        "img_dead": "ẢNH / PHÚT",     "img_rate": "HỎNG (100 JOB)",
        "img_done": "CÒN LẠI",        "img_codes": "XONG SAU",
        "vid_acc": "MÃ Ở PHA NÀY",    "vid_rest": "XIN / TRẦN",
        "vid_heal": "JOB ĐANG CHẠY",  "vid_queue": "JOB XẾP HÀNG",
        "vid_submit": "VIDEO / PHÚT", "vid_rate": "HỎNG (100 JOB)",
        "vid_done": "CÒN LẠI",        "vid_codes": "XONG SAU",
    }

    #: Nhãn gốc (đường Chrome/pool) — để quay về khi đổi chế độ.
    NHAN_TRAM_POOL = {
        "img_login": "ĐÃ LOGIN", "img_run": "ĐANG CHẠY", "img_q429": "CÁCH LY 429",
        "img_nghi": "NGHỈ KHÁC", "img_dead": "CHẾT", "img_rate": "ẢNH / GIỜ",
        "img_done": "TỔNG ẢNH", "img_codes": "MÃ × LUỒNG",
        "vid_acc": "KHAI THÁC", "vid_rest": "CÁCH LY 429", "vid_heal": "ĐANG CHỮA",
        "vid_queue": "HÀNG ĐỢI", "vid_submit": "SUBMIT/ACC", "vid_rate": "VIDEO / GIỜ",
        "vid_done": "TỔNG VIDEO", "vid_codes": "MÃ CHẠY",
    }

    def _dat_nhan_tram(self):
        """Đặt nhãn hai bảng trạm theo CHẾ ĐỘ đang chạy. Gọi lại khi đổi chế độ.

        BA trạng thái, không phải hai:

          * cấu hình API + CÓ khoá  -> bảng API, chạy bình thường
          * cấu hình API + THIẾU khoá -> vẫn bảng API, nhưng nói thẳng là thiếu
            khoá. Bản trước rơi thẳng về bảng pool, và người dùng nhìn thấy
            `TRẠM ẢNH · acc Pro` với tám ô `-` mà không có manh mối nào rằng thứ
            duy nhất còn thiếu là dán khoá. Máy thứ hai ngày 14/08/2026 đứng
            đúng ở đây: code mới đã về, số phiên bản đúng, màn hình y như cũ.
          * cấu hình đường Chrome  -> bảng pool, đúng như trước.
        """
        cfg = getattr(self.app, "config_data", {}) or {}
        try:
            chon_api = cau_hinh_toan_api(cfg)
            co_khoa = _co_khoa_shopapi() if chon_api else False
        except Exception:
            chon_api = co_khoa = False
        di_api = chon_api and co_khoa
        self._thieu_khoa_api = chon_api and not co_khoa
        # Chọn API mà thiếu khoá thì VẪN dùng nhãn API — bảng phải khớp thứ người
        # dùng đã chọn, rồi mới nói vì sao chưa chạy được.
        bo = self.NHAN_TRAM_API if chon_api else self.NHAN_TRAM_POOL
        for k, cap in (getattr(self, "pool_caps", {}) or {}).items():
            if k in bo:
                try: cap.configure(text=bo[k])
                except Exception: pass
        if self._thieu_khoa_api:
            tieu_de = {"img_issue": "🖼️ ẢNH · API shopapi — CHƯA CÓ KHOÁ",
                       "vid_issue": "🎬 VIDEO · API shopapi — CHƯA CÓ KHOÁ"}
        elif chon_api:
            tieu_de = {"img_issue": "🖼️ ẢNH · API shopapi",
                       "vid_issue": "🎬 VIDEO · API shopapi"}
        else:
            tieu_de = {"img_issue": "🖼️ TRẠM ẢNH · acc Pro",
                       "vid_issue": "🎬 TRẠM VIDEO · acc Ultra"}
        for k, lb in (getattr(self, "pool_titles", {}) or {}).items():
            if k in tieu_de:
                try: lb.configure(text=tieu_de[k])
                except Exception: pass

        # Thiếu khoá: hai ô "vấn đề" nói thẳng phải làm gì. Không có dòng này thì
        # bảng chỉ đứng `-` và người dùng đoán mò.
        if self._thieu_khoa_api:
            cau = ("⛔ CHƯA DÁN KHOÁ API. Cấu hình đã chọn đi API shopapi cho cả ảnh lẫn "
                   "video, nhưng máy này chưa có khoá nên không gửi được job nào. "
                   "Vào Settings → ô 'Khoá API shopapi' → dán khoá → Lưu khoá.")
            try:
                self.after(0, lambda: self._apply_pool_health(
                    {k: "-" for k in (self.pool_caps or {})}
                    | {"img_issue": (cau, RED), "vid_issue": (cau, RED)},
                    "Chưa có khoá API shopapi — xem Settings"))
            except Exception:
                pass
        return di_api

    def app_client_jobs_list(self, so=100):
        """`so` job gần nhất của tài khoản — nguồn của ô TỈ LỆ HỎNG.

        MỘT lời gọi cho cả hai loại job, chạy trong luồng nền của vòng poll.
        Hỏng thì trả rỗng: ô hỏng hiện `-`, phần còn lại của bảng vẫn đủ dùng.
        """
        try:
            import sys as _sys
            _engine = str(SUITE_ROOT / "veo3top_engine")
            if _engine not in _sys.path:
                _sys.path.insert(0, _engine)
            import shopapi_common as _sc
            c = _sc.tao_client(timeout=15.0, max_retries=1)
            r = c.jobs.list(limit=int(so))
            return r.get("data") if isinstance(r, dict) else (getattr(r, "data", None) or [])
        except Exception:
            return []

    def _so_lieu_api_len_tram(self):
        """Đọc `GET /v1/me` rồi dựng `vals` cho 16 ô + hai dòng chẩn đoán.

        Chạy trong LUỒNG NỀN của `_poll_pool_health` — không đụng widget ở đây.
        """
        import sys as _sys
        _engine = str(SUITE_ROOT / "veo3top_engine")
        if _engine not in _sys.path:
            _sys.path.insert(0, _engine)
        import shopapi_common as _sc
        me = _sc.doc_v1_me() or {}
        det = ((me.get("limits") or {}).get("concurrent_jobs_detail") or {})

        cfg = getattr(self.app, "config_data", {}) or {}
        def _n(khoa, loai):
            """Trần của TOOL cho một loại job. `0`/thiếu = không ghim.

            Không ghim thì trần thật là trần ĐỘNG của máy chủ (`/v1/me`), nên
            bảng phải hiện đúng con số ấy. Bản trước ép `max(1, ...)` rồi rơi
            về mặc định 24/16, tức là bảng nói dối: tool đang theo máy chủ mà
            bảng vẽ ra một cái trần không ai áp.
            """
            try:
                v = int(cfg.get(khoa) or 0)
            except (TypeError, ValueError):
                v = 0
            if v > 0:
                return v
            try:
                return int((det.get(loai) or {}).get("limit") or 0) or None
            except (TypeError, ValueError):
                return None
        tran_ma = {"image": _n("max_concurrent", "image"),
                   "video": _n("shopapi_video_concurrency", "video")}

        # ⚠ TỈ LỆ HỎNG lấy từ 100 job gần nhất của MÁY CHỦ, không đếm ở đây.
        # Hôm 12/08/2026 video hỏng 54% mà bảng không hề nói — người vận hành
        # nhìn thấy "đang chạy" và tưởng mọi thứ ổn, trong khi hơn nửa số job
        # đốt thời gian rồi quay về hàng chờ. Một lời gọi cho cả hai loại.
        hong = {}
        try:
            r = self.app_client_jobs_list()
            dem = {}
            for j in (r or []):
                d = j if isinstance(j, dict) else j.to_dict()
                loai = d.get("type")
                if loai not in ("image", "video"):
                    continue
                t, h = dem.get(loai, (0, 0))
                if d.get("status") == "failed":
                    h += 1
                if d.get("status") in ("succeeded", "failed"):
                    t += 1
                dem[loai] = (t, h)
            for loai, (t, h) in dem.items():
                hong[loai] = (h, t)
        except Exception:
            pass

        # SẢN LƯỢNG THẬT: đếm file trên đĩa trong 60 phút vừa rồi. Đây là sản
        # phẩm đã về tay, khác hẳn "máy chủ báo job succeeded".
        try:
            anh_gio, vid_gio = self.app._count_production_today(tu_giay=_time.time() - 3600)
        except Exception:
            anh_gio = vid_gio = None
        # VIỆC CÒN LẠI + MÃ ĐANG Ở PHA NÀO — trạng thái của chính ta.
        try:
            con_anh, con_vid, ma_anh, ma_vid = self.app._con_lai_va_ma_theo_pha()
        except Exception:
            con_anh = con_vid = ma_anh = ma_vid = None

        rieng = {"image": (anh_gio, con_anh, ma_anh), "video": (vid_gio, con_vid, ma_vid)}

        vals, status = {}, []
        for loai, khoa in (("image", ("img_login", "img_run", "img_q429", "img_nghi",
                                      "img_dead", "img_rate", "img_done", "img_codes", "img_issue")),
                           ("video", ("vid_acc", "vid_rest", "vid_heal", "vid_queue",
                                      "vid_submit", "vid_rate", "vid_done", "vid_codes", "vid_issue"))):
            d = det.get(loai) or {}
            gio, con, ma = rieng[loai]
            tran = int(d.get("limit") or 0)
            chay = int(d.get("running") or 0)
            cho = int(d.get("queued") or 0)
            # ⚠ XIN = (MÃ ĐANG Ở PHA NÀY) × trần mỗi mã, KHÔNG phải (số mã cấu
            # hình) × trần. Bản trước lấy con số cấu hình nên bảng khai "xin 320"
            # trong khi chỉ 1 mã ở pha ảnh — tức là xin thật 40. Con số phóng đại
            # đó còn kéo theo dòng chẩn đoán sai: "xin 320 mà 0 job chạy" nghe
            # như tool gãy, thật ra là chưa mã nào tới pha này.
            xin = (ma or 0) * tran_ma[loai]

            vals[khoa[0]] = "-" if ma is None else str(ma)
            vals[khoa[1]] = "{0}/{1}".format(xin, tran) if d else "-"
            vals[khoa[2]] = str(chay) if d else "-"
            vals[khoa[3]] = str(cho) if d else "-"
            vals[khoa[4]] = "-" if gio is None else "{0:.1f}".format(gio / 60.0)
            _h = hong.get(loai)
            vals[khoa[5]] = ("-" if not _h or not _h[1]
                             else "{0:.0f}%".format(100.0 * _h[0] / _h[1]))
            vals[khoa[6]] = "-" if con is None else str(con)
            # DỰ KIẾN XONG: việc còn lại chia tốc độ đang chạy. Không có tốc độ
            # thì nói thẳng "chưa đo được" thay vì bịa một con số đẹp.
            if con is None or not gio:
                vals[khoa[7]] = "—"
            elif con <= 0:
                vals[khoa[7]] = "xong"
            else:
                _gio = con / float(gio)
                vals[khoa[7]] = ("{0:.0f} phút".format(_gio * 60) if _gio < 1.5
                                 else "{0:.1f} giờ".format(_gio))

            # ═══ CHẨN ĐOÁN THEO CHUỖI NHÂN QUẢ: mã → xin → chạy → xếp hàng ═══
            if not d:
                vals[khoa[-1]] = ("⏸ Chưa đọc được số của máy chủ cho loại này", GRAY)
            elif tran <= 0:
                vals[khoa[-1]] = ("⛔ Nhà máy {0} ĐANG DỪNG (trần 0) — gửi lúc này chắc chắn "
                                  "503, KHÔNG bị trừ tiền. Chờ máy chủ mở lại.".format(loai), RED)
            elif con is not None and con <= 0:
                vals[khoa[-1]] = ("✅ Hết việc loại này — không còn gì để làm", GREEN)
            elif not ma and (chay or cho):
                # `MÃ ĐANG CHẠY` đếm worker của CỬA SỔ NÀY; `/v1/me` đếm job của
                # CẢ TÀI KHOẢN. Lệch nhau = có bản VE3 khác đang chạy cùng khoá,
                # hoặc job của lượt trước chưa xong. Nói thẳng ra, đừng để người
                # đọc phải tự giải thích hai con số chọi nhau.
                vals[khoa[-1]] = ("ℹ️ Cửa sổ này không chạy mã nào, nhưng tài khoản có {0} job "
                                  "chạy / {1} chờ — một bản VE3 khác (hoặc lượt trước) đang "
                                  "dùng chung khoá.".format(chay, cho), GRAY)
            elif not ma:
                vals[khoa[-1]] = ("⏳ KHÔNG mã nào đang ở pha này — chưa tới lượt, hoặc hàng "
                                  "chờ đã dừng. Bấm RUN nếu muốn chạy tiếp.", GRAY)
            elif chay == 0 and cho == 0:
                vals[khoa[-1]] = ("⛔ {0} mã đang chạy mà KHÔNG job nào tới máy chủ — tool "
                                  "không gửi được. Xem logs/ve3-*.log.".format(ma), RED)
            elif cho > max(4, tran * 0.25):
                vals[khoa[-1]] = ("⚠️ {0} job xếp hàng trên trần {1} — nhà máy đang ngộp, gửi "
                                  "thêm chỉ nằm chờ chứ không nhanh hơn".format(cho, tran), ORANGE)
            elif _h and _h[1] >= 10 and _h[0] / _h[1] >= 0.3:
                # Hỏng nhiều là thứ dễ bị bỏ qua nhất: bảng vẫn "đang chạy",
                # job vẫn nhúc nhích, mà quá nửa công sức rơi xuống đất.
                vals[khoa[-1]] = ("⚠️ HỎNG {0}/{1} job gần đây ({2:.0%}) — đang đốt thời gian. "
                                  "Xem 'VÌ SAO HỎNG' bằng: python -m tools.do_san_luong"
                                  .format(_h[0], _h[1], _h[0] / _h[1]), ORANGE)
            elif chay < xin * 0.5:
                vals[khoa[-1]] = ("⚠️ Xin {0} mà chỉ {1} job chạy (hàng chờ {2}) — nghẽn ở PHÍA "
                                  "TOOL, không phải ở máy chủ. Xem logs/ve3-*.log."
                                  .format(xin, chay, cho), ORANGE)
            elif xin < tran * 0.5:
                vals[khoa[-1]] = ("⚠️ Còn dư chỗ: {0} mã × {1} = xin {2} trên {3} được cấp "
                                  "({4:.0%}) — nâng 'Mã song song' hoặc 'Ảnh/mã' trong Cài đặt."
                                  .format(ma, tran_ma[loai], xin, tran, xin / max(tran, 1)), ORANGE)
            else:
                vals[khoa[-1]] = ("✅ {0} mã · {1} chạy / {2} chờ · {3:.1f}/phút · còn {4}"
                                  .format(ma, chay, cho, (gio or 0) / 60.0,
                                          con if con is not None else "?"), GREEN)
            status.append("{0}: chạy {1} · chờ {2} · trần {3}".format(loai, chay, cho, tran))
        return vals, status

    def _apply_pool_health(self, vals, status_text):
        """Áp số liệu pool lên tiles + dòng trạng thái. CHẠY TRÊN MAIN THREAD (gọi qua self.after)."""
        tiles = getattr(self, "pool_tiles", {})
        for k, val in vals.items():
            lb = tiles.get(k)
            if lb is None:
                continue
            try:
                if isinstance(val, tuple):   # ô VẤN ĐỀ: (text, color)
                    lb.configure(text=str(val[0]), text_color=val[1])
                else:
                    lb.configure(text=str(val))
            except Exception:
                pass
        try: self.pool_status_lbl.configure(text=status_text)
        except Exception: pass

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
                pass
        box._ve3_log_line_count = current
        # Throttle scroll — only scroll if not scrolled recently
        now = _time.time()
        last_scroll = getattr(box, "_last_scroll_time", 0)
        if now - last_scroll > 0.3:
            box.see("end")
            box._last_scroll_time = now
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
        self.log_pending.pop(code, None)
        if code in self.log_project_boxes:
            try:
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

    def refresh_projects_overview(self, rows, archived_today=0, images_today=0, videos_today=0):
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

            _archived_today = archived_today

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
                elif state == "DONE":
                    done_today += 1

            done_today += _archived_today

            self.lbl_overview_total.configure(text=str(len(rows)))
            self.lbl_overview_done_today.configure(text=str(done_today))
            self.lbl_overview_excel_run.configure(text=str(excel_run))
            self.lbl_overview_ve3_run.configure(text=str(ve3_run))
            self.lbl_overview_excel_wait.configure(text=str(excel_wait))
            self.lbl_overview_ve3_wait.configure(text=str(ve3_wait))
        except Exception as e:
            pass

        # Chi so KHAI THAC hom nay (do luong thuc te: dem file anh/video tao trong ngay)
        try:
            self.lbl_overview_img_today.configure(text=str(int(images_today)))
            self.lbl_overview_vid_today.configure(text=str(int(videos_today)))
        except Exception:
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
            def _srv_num(name):
                import re as _r
                m = _r.search(r'(\d+)', str(name or ""))
                return int(m.group(1)) if m else 999

            def sort_priority(r):
                state = r["state"]
                next_step = str(r.get("next", ""))
                is_ve3_run = state == "RUN" and r.get("ve3_running")
                is_excel_run = state == "RUN" and r.get("excel_running") and not r.get("ve3_running")
                srv_n = _srv_num(r.get("server_name"))

                if is_ve3_run:
                    return (0, srv_n)
                elif is_excel_run:
                    return (1, srv_n)
                elif state == "RUN":
                    return (2, srv_n)
                elif state == "WAIT" and "VE3" in next_step:
                    return (3, r["code"])
                elif state == "WAIT" and "Excel" in next_step:
                    return (4, r["code"])
                elif state == "WAIT":
                    return (5, r["code"])
                elif state == "BLOCK":
                    return (6, r["code"])
                elif state == "DONE":
                    return (7, r["code"])
                else:
                    return (8, r["code"])

            ordered = sorted(rows, key=lambda r: sort_priority(r))
        except Exception as e:
            ordered = rows

        # Render projects list — reuse existing widgets, only update text/colors
        if not hasattr(self, "_project_row_widgets"):
            self._project_row_widgets = []
        if not hasattr(self, "_project_widget_map"):
            self._project_widget_map = {}

        old_codes_set = set(w["code"] for w in self._project_row_widgets)
        new_codes_set = set(r["code"] for r in ordered)

        if not ordered and not self._project_row_widgets:
            ctk.CTkLabel(self.projects_list, text="No project in PROJECTS.", font=("",10), text_color=T3).grid(row=0, column=0, padx=8, pady=8, sticky="w")
            return

        removed = old_codes_set - new_codes_set
        added = new_codes_set - old_codes_set

        if removed:
            for code in removed:
                w = self._project_widget_map.pop(code, None)
                if w:
                    w["frame"].destroy()
            self._project_row_widgets = [w for w in self._project_row_widgets if w["code"] not in removed]

        def _make_row(code, grid_row):
            bg = "#FFFFFF" if grid_row % 2 == 0 else "#FBFCFD"
            row = ctk.CTkFrame(self.projects_list, fg_color=bg, corner_radius=6, border_width=1, border_color="#E6EAEE")
            row.grid(row=grid_row, column=0, padx=6, pady=(0,4), sticky="ew")
            row.grid_columnconfigure(0, weight=0, minsize=85)
            row.grid_columnconfigure(1, weight=0, minsize=80)
            row.grid_columnconfigure(2, weight=1, minsize=220)
            row.grid_columnconfigure(3, weight=1, minsize=180)
            for col in range(4, 8):
                row.grid_columnconfigure(col, weight=0)
            row.grid_propagate(False)
            row.configure(height=40)
            lbl_code = ctk.CTkLabel(row, text=code, font=("Consolas", 11, "bold"), text_color=T1)
            lbl_code.grid(row=0, column=0, padx=(10,8), pady=6, sticky="w")
            lbl_step = ctk.CTkLabel(row, text="", font=("", 10, "bold"), text_color=T1, anchor="w")
            lbl_step.grid(row=0, column=1, padx=(0,10), pady=6, sticky="ew")
            lbl_pair = ctk.CTkLabel(row, text="", font=("Consolas", 9), text_color=T3, anchor="w")
            lbl_pair.grid(row=0, column=2, padx=(0,10), pady=6, sticky="ew")
            lbl_prog = ctk.CTkLabel(row, text="", font=("Consolas", 10), text_color=T2, anchor="w")
            lbl_prog.grid(row=0, column=3, padx=(0,10), pady=6, sticky="ew")
            lbl_media = ctk.CTkLabel(row, text="", font=("Consolas", 10, "bold"), text_color=T2, anchor="w")
            lbl_media.grid(row=0, column=4, padx=(0,10), pady=6, sticky="ew")
            lbl_state = ctk.CTkLabel(row, text="", corner_radius=9, font=("Consolas", 10, "bold"), width=64, height=22)
            lbl_state.grid(row=0, column=5, padx=(0,8), pady=6, sticky="w")
            btn_done = ctk.CTkButton(row, text="Xong", width=64, height=22, corner_radius=4, font=("",10))
            btn_done.grid(row=0, column=6, padx=(0,4), pady=6, sticky="e")
            btn_reset = ctk.CTkButton(row, text="Reset", width=50, height=22, corner_radius=4,
                                      fg_color="#EF5350", hover_color="#D32F2F", text_color="#FFFFFF", font=("",10))
            btn_reset.grid(row=0, column=7, padx=(0,10), pady=6, sticky="e")
            return {
                "code": code, "frame": row,
                "lbl_step": lbl_step, "lbl_pair": lbl_pair, "lbl_prog": lbl_prog,
                "lbl_media": lbl_media, "lbl_state": lbl_state,
                "btn_done": btn_done, "btn_reset": btn_reset,
            }

        if added:
            for code in added:
                we = _make_row(code, len(self._project_row_widgets))
                self._project_row_widgets.append(we)
                self._project_widget_map[code] = we

        new_codes = [r["code"] for r in ordered]
        old_codes = [w["code"] for w in self._project_row_widgets]
        if old_codes != new_codes:
            reordered = []
            for i, code in enumerate(new_codes):
                w = self._project_widget_map.get(code)
                if not w:
                    continue
                w["frame"].grid(row=i, column=0, padx=6, pady=(0, 4), sticky="ew")
                bg = "#FFFFFF" if i % 2 == 0 else "#FBFCFD"
                w["frame"].configure(fg_color=bg)
                reordered.append(w)
            self._project_row_widgets = reordered

        state_colors = {
            "RUN": ("#E8F2FF", RN), "WAIT": ("#FFF4DD", "#C47F00"),
            "DONE": ("#E8F7ED", OK), "BLOCK": ("#FDECEC", ER),
        }
        for i, r in enumerate(ordered):
            if i >= len(self._project_row_widgets):
                break
            w = self._project_row_widgets[i]
            prev = w.get("_prev", {})
            st = state_label(r)
            badge_bg, badge_fg = state_colors.get(st, ("#F2F2F2", T2))
            border = "#D9E7FF" if r["state"] == "RUN" else "#E6EAEE"
            step_t = step_label(r)
            pair_t = pair_label(r)
            prog_t = progress_short(r)
            media_t = media_label(r)
            media_c = RN if str(r.get("latest_media_kind", "")) == "VID" else T2
            manual_done = bool(r.get("manual_done"))
            is_running = bool(r.get("excel_running") or r.get("ve3_running"))
            p_path = r.get("path", "")
            cur = (border, step_t, pair_t, prog_t, media_t, media_c, st, badge_bg, badge_fg, manual_done, is_running, p_path)
            if cur == prev.get("sig"):
                continue
            if border != prev.get("border"):
                w["frame"].configure(border_color=border)
            if step_t != prev.get("step"):
                w["lbl_step"].configure(text=step_t)
            if pair_t != prev.get("pair"):
                w["lbl_pair"].configure(text=pair_t)
            if prog_t != prev.get("prog"):
                w["lbl_prog"].configure(text=prog_t)
            if (media_t, media_c) != (prev.get("media"), prev.get("media_c")):
                w["lbl_media"].configure(text=media_t, text_color=media_c)
            if (st, badge_bg, badge_fg) != (prev.get("st"), prev.get("bg"), prev.get("fg")):
                w["lbl_state"].configure(text=st, fg_color=badge_bg, text_color=badge_fg)
            path_changed = p_path != prev.get("p_path")
            if manual_done != prev.get("done") or path_changed:
                if manual_done:
                    w["btn_done"].configure(text="Da nhan", fg_color="#1F8E4D", hover_color="#1F8E4D",
                                            text_color="#FFFFFF", state="disabled", command=None)
                else:
                    cmd = lambda p=r["path"]: self.app.toggle_project_manual_done(Path(p), mark_done=True)
                    w["btn_done"].configure(text="Xong", fg_color="#F4C542", hover_color="#E5B52F",
                                            text_color="#1F1F1F", state="normal", command=cmd)
            if is_running != prev.get("running") or path_changed:
                w["btn_reset"].configure(
                    state="disabled" if is_running else "normal",
                    fg_color="#BDBDBD" if is_running else "#EF5350",
                    command=lambda p=r["path"], c=r["code"]: self.app.clean_project_excel(Path(p), c),
                )
            w["_prev"] = {
                "sig": cur, "border": border, "step": step_t, "pair": pair_t,
                "prog": prog_t, "media": media_t, "media_c": media_c,
                "st": st, "bg": badge_bg, "fg": badge_fg,
                "done": manual_done, "running": is_running, "p_path": p_path,
            }

    def _sanitize_log_text(self, msg):
        """Fast log sanitization — remove control chars, keep printable."""
        text = str(msg).replace("\r\n", "\n").replace("\r", "\n")
        text = text.encode("ascii", "replace").decode("ascii")
        text = "".join(ch for ch in text if ch >= " " or ch in "\n\t")
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
        return None

    def _format_log_targets(self, msg, level="INFO", channel="ve3"):
        ts = datetime.now().strftime("%H:%M:%S")
        ic = {"SUCCESS":"[OK]", "ERROR":"[X]", "WARN":"[!]"}.get(level, "[ ]")
        raw_msg = str(msg)
        safe_msg = self._sanitize_log_text(raw_msg)
        line = f"[{ts}] {ic} {safe_msg}\n"

        code_match = _RE_PROJECT_CODE.search(raw_msg)
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
            is_queue_debug = bool(_RE_QUEUE_DEBUG.search(raw_msg))
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
        self.excel_engine_options = {
            "API (DeepSeek/VOV) - nhieu buoc": "api",
            "Claude Code CLI (Claude Max)": "claude_cli",
        }
        self.excel_engine_labels = {v: k for k, v in self.excel_engine_options.items()}
        # Transport for the Claude CLI engine: local claude.exe vs OpenAI-compatible API (VOV).
        self.claude_backend_options = {
            # shopapi dung CHUNG khoa sk_live_ voi anh/video -> khong khoa rieng,
            # khong claude.exe, khong dinh toi OAuth cua extension VS Code.
            "API shopapi (sonnet-5)": "api_shop",
            "API shopapi -> CLI": "api_shop_cli",
            "CLI (claude.exe)": "cli",
            "API (VOV)": "api",
            "API -> CLI (fallback)": "api_cli",
            "API DeepSeek (v4 pro)": "api_ds",
            "API DeepSeek -> CLI": "api_ds_cli",
        }
        self.claude_backend_labels = {v: k for k, v in self.claude_backend_options.items()}
        # "API shopapi" = goi thang api.shopapi.vn (MAC DINH cua ban nay, khong mo Chrome).
        self.generation_backend_options = dict(BACKEND_VIDEO)
        self.generation_backend_labels = {v: k for k, v in self.generation_backend_options.items()}
        # Backend TAO ANH (ban thang Flow API giong video). "" = dung backend anh cu (server/local token).
        self.image_backend_options = dict(BACKEND_ANH)
        self.image_backend_labels = {v: k for k, v in self.image_backend_options.items()}
        self.grid_columnconfigure(0, weight=1)

        # Server pairs
        sc = ctk.CTkFrame(self, fg_color=CD, corner_radius=8, border_width=1, border_color=BD)
        sc.grid(row=0, column=0, sticky="ew", padx=10, pady=(10,4))
        sc.grid_columnconfigure(0, weight=1)
        #: Cả khối server/gmail/chrome — GẤP LẠI khi đi API (xem `_gap_khoi_chrome`).
        self.frame_server = sc
        #: Một dòng thay chỗ khối đã gấp, kèm nút mở lại.
        self.thanh_gap_chrome = ctk.CTkFrame(self, fg_color="#F7F8FA", corner_radius=8,
                                             border_width=1, border_color=BD2)
        self.thanh_gap_chrome.grid_columnconfigure(0, weight=1)
        self.lbl_gap_chrome = ctk.CTkLabel(
            self.thanh_gap_chrome, anchor="w", justify="left", font=("",10), text_color=T2,
            text=("Server + Gmail + Chrome — DA GAP. Dang chay API shopapi nen khong "
                  "server/gmail/chrome nao duoc dung toi."))
        self.lbl_gap_chrome.grid(row=0, column=0, padx=12, pady=8, sticky="w")
        ctk.CTkButton(self.thanh_gap_chrome, text="Hien khoi Chrome", width=130, height=26,
                      corner_radius=5, fg_color=SB2, hover_color=SB3, text_color="#FFF",
                      font=("",10), command=self._mo_lai_khoi_chrome).grid(
            row=0, column=1, padx=(0,10), pady=6, sticky="e")
        self.thanh_gap_chrome.grid_remove()
        #: Người dùng đã bấm "Hiện" -> tôn trọng, đừng gấp lại sau lưng họ.
        self._ep_hien_chrome = False
        ctk.CTkLabel(sc, text="Server + Gmail + Chrome", font=("",13,"bold"), text_color=T1).grid(row=0, column=0, padx=10, pady=(8,4), sticky="w", columnspan=6)
        top = ctk.CTkFrame(sc, fg_color="transparent")
        top.grid(row=1, column=0, padx=10, pady=(0,4), sticky="ew", columnspan=6)
        top.grid_columnconfigure(1, weight=1)
        ctk.CTkLabel(top, text="Moi dong la 1 cap dung truc tiep: server + gmail|password|totp + chrome. Project da bind cap nao thi giu nguyen cap do.", font=("",10), text_color=T3).grid(row=0, column=0, sticky="w", columnspan=2)
        self.sw_flow_auto = ctk.CTkSwitch(top, text="Auto auth", progress_color=OK, button_color="#FFF", button_hover_color="#EEE")
        self.sw_flow_auto.grid(row=0, column=2, padx=(4,0))
        ctk.CTkLabel(top, text="Mode:", font=("",10), text_color=T2).grid(row=0, column=3, padx=(8,2))
        self.opt_flow_auth_mode = ctk.CTkOptionMenu(top, values=["Chrome UI", "Extension"], width=100, height=24, font=("",10))
        self.opt_flow_auth_mode.grid(row=0, column=4, padx=(0,4))
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
        # Parallel jobs = so anh 1 ma gui song song: TU DONG theo (so chrome pool / so ma song song) -> luon lam day pool.
        # KHONG con chinh tay (tranh dat sai lam chrome ngoi khong hoac over-thread). Tool tu tinh theo thuc te.
        ctk.CTkLabel(gc, text="Parallel jobs:", font=("",11), text_color=T2).grid(row=1, column=0, padx=(10,6), sticky="e")
        # Câu này NÓI DỐI ở chế độ API: không có chrome nào, và số mã do
        # `shopapi_ma_song_song` quyết. `_cap_nhat_num_theo_che_do` sửa lại.
        self.lbl_parallel_jobs = ctk.CTkLabel(gc, text="TU DONG (theo so chrome / so ma)", font=("",10,"italic"), text_color=T3)
        self.lbl_parallel_jobs.grid(row=1, column=1, sticky="w")
        ctk.CTkLabel(gc, text="Retry:", font=("",11), text_color=T2).grid(row=2, column=0, padx=(10,6), sticky="e")
        self.ent_retry = ctk.CTkEntry(gc, width=60, height=28, corner_radius=4, font=("",11), fg_color=EN, border_color=BD)
        self.ent_retry.grid(row=2, column=1, sticky="w")
        ctk.CTkLabel(gc, text="Aspect ratio:", font=("",11), text_color=T2).grid(row=3, column=0, padx=(10,6), sticky="e")
        self.opt_ar = ctk.CTkOptionMenu(gc, values=["landscape","portrait","square"], width=120, height=28, corner_radius=4, fg_color=EN, button_color=BD, text_color=T1, font=("",11))
        self.opt_ar.grid(row=3, column=1, sticky="w", pady=(0,4))
        ctk.CTkLabel(gc, text="Generation:", font=("",11), text_color=T2).grid(row=4, column=0, padx=(10,6), sticky="e")
        self.opt_generation_backend = ctk.CTkOptionMenu(gc, values=list(self.generation_backend_options.keys()), width=120, height=28, corner_radius=4, fg_color=EN, button_color=BD, text_color=T1, font=("",11))
        self.opt_generation_backend.grid(row=4, column=1, sticky="w", pady=(0,4))
        # FLOW2: tao anh bang token Pro local tren tung server (dot quota account local, khong dung token Ultra)
        self.sw_use_local_token = ctk.CTkSwitch(gc, text="Tao anh bang token local (Pro)", progress_color=OK, button_color="#FFF", button_hover_color="#EEE")
        self.sw_use_local_token.grid(row=4, column=2, padx=(10,0), sticky="w")
        # Backend TAO ANH: ban thang Flow API (giong video Veo3top-B). "Mac dinh" = giu backend anh cu.
        ctk.CTkLabel(gc, text="Tao anh:", text_color=T1, font=("",11)).grid(row=5, column=0, padx=10, sticky="w")
        self.opt_image_backend = ctk.CTkOptionMenu(gc, values=list(self.image_backend_options.keys()), width=160, height=28, corner_radius=4, fg_color=EN, button_color=BD, text_color=T1, font=("",11))
        self.opt_image_backend.grid(row=5, column=1, columnspan=2, sticky="w", pady=(0,4))
        self.sw_music_workspace = ctk.CTkSwitch(gc, text="Music Chrome mo lech man hinh", progress_color=OK, button_color="#FFF", button_hover_color="#EEE")
        self.sw_music_workspace.grid(row=6, column=0, columnspan=3, padx=10, pady=(0,8), sticky="w")
        # AN chrome anh + video (mac dinh BAT = offscreen; gom ca login vi loi login la IP, da fix bang 4G/WARP).
        self.sw_img_hide = ctk.CTkSwitch(gc, text="An TAT CA Chrome cua tool - ke ca login (mac dinh bat; tat = hien het)", progress_color=OK, button_color="#FFF", button_hover_color="#EEE")
        self.sw_img_hide.grid(row=7, column=0, columnspan=3, padx=10, pady=(0,8), sticky="w")
        # LOGIN EGRESS: 4G proxy (moi dong 1 cai: host:port) -> IP tu doi, retry xoay vong -> khoi 'Something went wrong'.
        ctk.CTkLabel(gc, text="4G login (host:port):", font=("",11), text_color=T2).grid(row=8, column=0, padx=(10,6), sticky="ne")
        self.txt_login_4g = ctk.CTkTextbox(gc, height=48, corner_radius=4, font=("Consolas",9), fg_color=EN, border_color=BD, border_width=1, wrap="none")
        self.txt_login_4g.grid(row=8, column=1, columnspan=2, sticky="ew", padx=(0,10), pady=(0,4))
        self.sw_login_warp = ctk.CTkSwitch(gc, text="Dung WARP cho login (Cloudflare, IP khac IP may)", progress_color=OK, button_color="#FFF", button_hover_color="#EEE")
        self.sw_login_warp.grid(row=9, column=0, columnspan=2, padx=10, pady=(0,8), sticky="w")
        self.btn_warp_setup = ctk.CTkButton(gc, text="⬇ Setup WARP", width=110, height=26, corner_radius=5,
                                            fg_color="#F38020", hover_color="#D96D10", text_color="#FFF", font=("",10,"bold"),
                                            command=self._setup_warp)
        self.btn_warp_setup.grid(row=9, column=2, padx=(0,10), pady=(0,8), sticky="e")
        # NUM TINH CHINH NHA MAY ANH (toi uu toc do, chong 403). Tat ca so nguyen.
        knob = ctk.CTkFrame(gc, fg_color="transparent")
        knob.grid(row=10, column=0, columnspan=3, padx=10, pady=(0,6), sticky="w")
        def _mk_knob(col, label, width=52, row=0):
            ctk.CTkLabel(knob, text=label, font=("",10), text_color=T2).grid(row=row, column=col*2, padx=(0 if col==0 else 10, 4), pady=(0,3), sticky="e")
            e = ctk.CTkEntry(knob, width=width, height=28, corner_radius=4, font=("",11), fg_color=EN, border_color=BD, justify="center")
            e.grid(row=row, column=col*2+1, pady=(0,3), sticky="w")
            return e
        # HANG 1: account pool anh | so MA moi tram (0=TU TINH theo nhan su) | cach ly 429
        self.ent_img_accounts = _mk_knob(0, "Account pool anh:", width=44)
        self.ent_img_codes    = _mk_knob(1, "Ma ANH (0=auto):", width=40)
        self.ent_vid_codes    = _mk_knob(2, "Ma VIDEO (0=auto):", width=40)
        self.ent_iso_hours    = _mk_knob(3, "Nghi 429 (gio):", width=40)
        # HANG 2 (TUNE THEO MAY): token chrome anh/video + recycle + luong video
        self.ent_img_tokchrome = _mk_knob(0, "Token chrome anh:", width=44, row=1)
        self.ent_img_recycle   = _mk_knob(1, "Recycle token:", width=44, row=1)
        self.ent_vid_tokchrome = _mk_knob(2, "Token chrome video:", width=44, row=1)
        self.ent_vid_workers   = _mk_knob(3, "Luong/acc video:", width=44, row=1)
        # CHU THICH y nghia (chinh theo may -> restart de ap) — dat trong frame knob, hang 2
        self.lbl_chu_thich_pool = ctk.CTkLabel(
            knob, justify="left", font=("",9), text_color=T2, anchor="w", wraplength=360,
            text=("Ma ANH/VIDEO = so ma song song moi TRAM; 0 = TU TINH theo nhan su dang khai thac (xem tab '📊 So lieu'). "
                  "Account pool anh = tran so account nap. Token chrome = so chrome-trang de token (CPU la tran ~6-8). "
                  "Recycle = N token/chrome roi lam moi. Luong/acc video = submit/account ultra. Restart de ap."))
        self.lbl_chu_thich_pool.grid(row=2, column=0, columnspan=8, padx=(0,4), pady=(4,0), sticky="w")

        # ═══ NÚM CỦA CHẾ ĐỘ API shopapi ═══════════════════════════════════════
        #
        # Tám núm ở trên đều là núm của đường CHROME/POOL: account pool, token
        # chrome, recycle, luồng/account ultra... Đi API shopapi thì KHÔNG cái
        # nào trong đó được đọc — không có Chrome nào mở, không có account Flow
        # nào nạp.
        #
        # Ba con số THẬT SỰ quyết định thông lượng ở chế độ API lại không có
        # mặt trên giao diện, nên muốn chỉnh phải mở `settings.yaml` bằng tay.
        # Ngày 11/08/2026 đó chính là cách chúng bị bỏ ở mức làm tool chạy 1%
        # công suất suốt nhiều giờ mà không ai thấy gì bất thường: giao diện
        # đầy núm, và không núm nào nối tới thứ đang bóp cổ.
        api = ctk.CTkFrame(knob, fg_color=CD, corner_radius=6, border_width=1, border_color=AC)
        api.grid(row=3, column=0, columnspan=8, padx=(0,4), pady=(8,2), sticky="ew")
        self.frame_api_knob = api
        ctk.CTkLabel(api, text="API shopapi — 3 so quyet dinh thong luong",
                     font=("",11,"bold"), text_color=AC).grid(
            row=0, column=0, columnspan=8, padx=8, pady=(6,2), sticky="w")

        def _mk_api(col, label, width=52):
            ctk.CTkLabel(api, text=label, font=("",10), text_color=T2).grid(
                row=1, column=col*2, padx=(8 if col==0 else 10, 4), pady=(0,4), sticky="e")
            e = ctk.CTkEntry(api, width=width, height=28, corner_radius=4, font=("",11),
                             fg_color=EN, border_color=BD, justify="center")
            e.grid(row=1, column=col*2+1, pady=(0,4), sticky="w")
            return e

        self.ent_api_ma      = _mk_api(0, "Ma song song:", width=46)
        self.ent_api_anh     = _mk_api(1, "Anh / ma:", width=46)
        self.ent_api_video   = _mk_api(2, "Video / ma:", width=46)
        ctk.CTkButton(api, text="Doc tran may chu", width=130, height=26, corner_radius=5,
                      fg_color=AC, hover_color=AC2, text_color="#FFF", font=("",10,"bold"),
                      command=self._doc_tran_may_chu).grid(
            row=1, column=6, columnspan=2, padx=(10,8), pady=(0,4), sticky="w")
        self.lbl_api_tran = ctk.CTkLabel(
            api, justify="left", font=("",9), text_color=T2, anchor="w", wraplength=520,
            text=("BA SO NAY LA MOT BO: (Ma song song) x (Anh/ma) = tong job anh dat len nha may, "
                  "tuong tu cho video. Vuot tran may chu thi phan du nam hang cho chu khong nhanh them. "
                  "Bam 'Doc tran may chu' de xem minh dang duoc cap bao nhieu."))
        self.lbl_api_tran.grid(row=2, column=0, columnspan=8, padx=8, pady=(2,8), sticky="w")
        # (Bỏ nút 'Auto Setup theo máy': số MÃ ảnh/video TỰ LINH HOẠT ở runtime — _compute_pool_capacity đọc /health
        #  live -> nhân sự thực bao nhiêu làm bấy nhiêu (Mã=0=auto). Núm token chrome có default + token factory đã
        #  demand-driven idle nên khỏi tinh chỉnh tay. Muốn chỉnh vẫn sửa núm trực tiếp được.)
        ctk.CTkButton(gc, text="Save settings", width=120, height=30, fg_color=AC, hover_color=AC2, text_color="#FFF", font=("",11,"bold"), corner_radius=6, command=self._save).grid(row=11, column=0, columnspan=3, padx=10, pady=(4,4))
        self.lbl_saved = ctk.CTkLabel(gc, text="", font=("",9), text_color=OK)
        self.lbl_saved.grid(row=12, column=0, columnspan=3, padx=10, pady=(0,4))
        # QUẢN LÝ account nhà máy ảnh/video (GUI riêng: trạng thái good/bad/dead + Prepare/Probe/Check/Reset)
        ctk.CTkButton(gc, text="Quan ly account anh/video", width=200, height=30, fg_color=RN,
                      hover_color="#1565C0", text_color="#FFF", font=("",11,"bold"), corner_radius=6,
                      command=app._open_pool_manager).grid(row=13, column=0, columnspan=3, padx=10, pady=(0,10))

        # ===== KHOA API shopapi.vn ==================================================
        # Khoa KHONG luu vao settings.yaml: file do nam trong kho ma va con duoc chep
        # sang worker qua .ve3_run_config.json trong thu muc project -> hai duong ro ri.
        # Nut "Luu khoa" ghi vao %APPDATA%\ShopAPI\ve3-suite\khoa.txt (ngoai kho ma).
        key_box = ctk.CTkFrame(gc, fg_color="transparent")
        key_box.grid(row=14, column=0, columnspan=3, padx=10, pady=(0,8), sticky="ew")
        key_box.grid_columnconfigure(1, weight=1)
        ctk.CTkLabel(key_box, text="Khoa API shopapi:", font=("",11), text_color=T2).grid(row=0, column=0, padx=(0,6), sticky="e")
        self.ent_shopapi_key = ctk.CTkEntry(key_box, placeholder_text="sk_live_...", height=28, corner_radius=4,
                                            font=("Consolas",10), fg_color=EN, border_color=BD, show="*")
        self.ent_shopapi_key.grid(row=0, column=1, sticky="ew", padx=(0,4))
        ctk.CTkButton(key_box, text="Luu khoa", width=80, height=28, corner_radius=4, fg_color=OK,
                      hover_color=OK2, text_color="#FFF", font=("",10),
                      command=self._shopapi_save_key).grid(row=0, column=2, padx=(0,4))
        ctk.CTkButton(key_box, text="Kiem khoa", width=90, height=28, corner_radius=4, fg_color=RN,
                      hover_color="#1565C0", text_color="#FFF", font=("",10),
                      command=self._shopapi_check_key).grid(row=0, column=3, padx=(0,4))
        ctk.CTkButton(key_box, text="Xoa khoa", width=90, height=28, corner_radius=4, fg_color=EN,
                      hover_color=BD, text_color=T2, font=("",10),
                      command=self._shopapi_forget_key).grid(row=0, column=4)
        self.lbl_shopapi_key = ctk.CTkLabel(key_box, text="", font=("",10), text_color=T3, anchor="w", wraplength=560, justify="left")
        self.lbl_shopapi_key.grid(row=1, column=0, columnspan=5, sticky="w", pady=(3,0))

        # Excel AI
        ai = ctk.CTkFrame(self, fg_color=CD, corner_radius=8, border_width=1, border_color=BD)
        ai.grid(row=2, column=0, sticky="ew", padx=10, pady=4)
        ai.grid_columnconfigure(1, weight=1)
        ctk.CTkLabel(ai, text="Excel AI", font=("",13,"bold"), text_color=T1).grid(row=0, column=0, padx=10, pady=(8,6), sticky="w")
        eng_box = ctk.CTkFrame(ai, fg_color="transparent")
        eng_box.grid(row=0, column=1, padx=(0,10), pady=(8,6), sticky="e")
        ctk.CTkLabel(eng_box, text="Engine:", font=("",11,"bold"), text_color=T1).pack(side="left", padx=(0,6))
        self.opt_excel_engine = ctk.CTkOptionMenu(eng_box, values=list(self.excel_engine_options.keys()), width=220, height=28, corner_radius=4, fg_color=EN, button_color=BD, text_color=T1, font=("",11))
        self.opt_excel_engine.pack(side="left")
        # Claude CLI transport: CLI (claude.exe, Claude Max) or API (VOV, no CLI needed)
        ctk.CTkLabel(eng_box, text="Backend:", font=("",10), text_color=T2).pack(side="left", padx=(10,4))
        self.opt_claude_backend = ctk.CTkOptionMenu(eng_box, values=list(self.claude_backend_options.keys()), width=130, height=28, corner_radius=4, fg_color=EN, button_color=BD, text_color=T1, font=("",11))
        self.opt_claude_backend.pack(side="left")
        # So luong code chay song song trong queue Excel (claude_cli_max_parallel)
        ctk.CTkLabel(eng_box, text="Luồng:", font=("",10), text_color=T2).pack(side="left", padx=(10,4))
        self.ent_claude_parallel = ctk.CTkEntry(eng_box, width=46, height=28, corner_radius=4, font=("",11), fg_color=EN, border_color=BD, justify="center")
        self.ent_claude_parallel.pack(side="left")
        # Claude CLI review pass toggle (tat = nhanh gap doi, bat = ra soat ky hon)
        self.sw_claude_review = ctk.CTkSwitch(eng_box, text="Review", progress_color=OK, button_color="#FFF", button_hover_color="#EEE", font=("",10))
        self.sw_claude_review.pack(side="left", padx=(10,0))

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

        ctk.CTkLabel(ai, text="DeepSeek keys:", font=("",11), text_color=T2).grid(row=3, column=0, padx=(10,6), sticky="ne")
        ds_key_frame = ctk.CTkFrame(ai, fg_color="transparent")
        ds_key_frame.grid(row=3, column=1, sticky="ew", padx=(0,10), pady=2)
        ds_key_frame.columnconfigure(0, weight=1)
        self.txt_deepseek_keys = ctk.CTkTextbox(ds_key_frame, height=60, corner_radius=4, font=("Consolas",9), fg_color=EN, border_color=BD, border_width=1)
        self.txt_deepseek_keys.grid(row=0, column=0, sticky="ew")
        ds_btn_frame = ctk.CTkFrame(ds_key_frame, fg_color="transparent")
        ds_btn_frame.grid(row=0, column=1, padx=(4,0), sticky="n")
        self.btn_ds_balance = ctk.CTkButton(ds_btn_frame, text="Check $", width=60, height=28, corner_radius=4, font=("",10), command=self._check_deepseek_balance)
        self.btn_ds_balance.pack(pady=(0,2))
        self.lbl_ds_total = ctk.CTkLabel(ds_btn_frame, text="", font=("",9,"bold"), text_color=OK)
        self.lbl_ds_total.pack()
        # Hidden compat entries
        self.ent_deepseek_key = type('D',(),{'get':lambda s: '','delete':lambda s,*a:None,'insert':lambda s,*a:None})()
        self.ent_deepseek_keys = type('D',(),{'get':lambda s: '','delete':lambda s,*a:None,'insert':lambda s,*a:None})()

        # Claude proxy (bought "Claude card") — route claude.exe (Backend=CLI) through
        # a custom Anthropic endpoint instead of personal Max. URL prefilled; each
        # machine enters its OWN key (saved locally, not committed to git).
        ctk.CTkLabel(ai, text="Claude Proxy:", font=("",11), text_color=T2).grid(row=4, column=0, padx=(10,6), sticky="e")
        prox = ctk.CTkFrame(ai, fg_color="transparent")
        prox.grid(row=4, column=1, sticky="ew", padx=(0,10), pady=2)
        prox.columnconfigure(1, weight=1)
        self.ent_claude_proxy_url = ctk.CTkEntry(prox, height=28, corner_radius=4, font=("Consolas",10), fg_color=EN, border_color=BD, placeholder_text="https://vip.digishop.work")
        self.ent_claude_proxy_url.grid(row=0, column=0, columnspan=2, sticky="ew", padx=(0,4))
        self.btn_proxy_test = ctk.CTkButton(prox, text="Test", width=56, height=28, corner_radius=4, fg_color=RN, hover_color="#1565C0", text_color="#FFF", font=("",10,"bold"), command=self._check_claude_proxy)
        self.btn_proxy_test.grid(row=0, column=2, padx=(4,0))
        # NHIEU KEY digishop: MOI DONG 1 KEY -> xoay vong chia tai giua cac chunk song song (de trong = dung Max ca nhan)
        self.txt_claude_proxy_keys = ctk.CTkTextbox(prox, height=52, corner_radius=4, font=("Consolas",9), fg_color=EN, border_color=BD, border_width=1, wrap="none")
        self.txt_claude_proxy_keys.grid(row=1, column=0, columnspan=3, sticky="ew", pady=(3,0))
        self.lbl_proxy_status = ctk.CTkLabel(prox, text="moi dong 1 key (sk-...) -> xoay vong nhieu key card", font=("",9), text_color=T3)
        self.lbl_proxy_status.grid(row=2, column=0, columnspan=3, sticky="w", pady=(2,0))

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
        self.ent_claude_pool_chain.grid(row=14, column=1, sticky="ew", padx=(0,10), pady=2)
        ctk.CTkButton(ai, text="Save AI settings", width=120, height=28, fg_color=AC, hover_color=AC2, text_color="#FFF", font=("",11,"bold"), corner_radius=6, command=self._save).grid(row=15, column=0, columnspan=2, padx=10, pady=(6,10))

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
            # Account-only rows (for example ShopAPI/Flow workers) legitimately
            # have no local server URL.  Do not let the settings page crash the
            # whole application while rendering those rows.
            url = str(s.get("url", "") or "") if isinstance(s, dict) else str(s or "")
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

    def _parse_deepseek_keys(self):
        """Parse keys from textbox — supports tab, comma, newline, space separated."""
        raw = self.txt_deepseek_keys.get("1.0", "end").strip()
        keys = []
        import re
        for part in re.split(r'[\t,\n\s]+', raw):
            part = part.strip()
            if part.startswith("sk-") and len(part) > 10:
                keys.append(part)
        return keys

    def _check_claude_proxy(self):
        """Kiem tra key proxy Claude: quota (con bao nhieu token, han dung) + ping
        chat that su de chac chan chay duoc truoc khi chay chinh thuc."""
        keys = [ln.strip() for ln in self.txt_claude_proxy_keys.get("1.0", "end").splitlines() if ln.strip()]
        url = (self.ent_claude_proxy_url.get().strip().rstrip("/") or "https://vip.digishop.work")
        if not keys:
            self.lbl_proxy_status.configure(text="⚠ Chua nhap key", text_color=ER)
            return
        self.btn_proxy_test.configure(text="...", state="disabled")
        self.lbl_proxy_status.configure(text=f"Dang kiem tra {len(keys)} key...", text_color=T3)

        def _do():
            import urllib.request, json as _json
            ORANGE = "#FF8C00"
            # 1) quota TUNG key (biet key nao het han / con bao nhieu)
            ok_n = exp_n = drained_n = err_n = 0
            total_rem = 0
            first_ok_key = ""
            for k in keys:
                try:
                    req = urllib.request.Request("https://token-quota.digishop.work",
                                                 headers={"Authorization": f"Bearer {k}"})
                    with urllib.request.urlopen(req, timeout=12) as resp:
                        q = (_json.loads(resp.read()) or {}).get("quota", {})
                    try: rem = int(q.get("remaining", 0) or 0)
                    except Exception: rem = 0
                    if q.get("is_expired"):
                        exp_n += 1
                    elif rem <= 0:
                        drained_n += 1          # con han NGAY nhung HET TOKEN (remaining<=0) -> proxy tra 401; se TU HOI
                    else:
                        ok_n += 1; total_rem += rem
                        if not first_ok_key: first_ok_key = k
                except Exception:
                    err_n += 1
            quota_txt = f"{ok_n}/{len(keys)} key song | tong con {total_rem:,} token"
            if drained_n: quota_txt += f" | {drained_n} het token (cho hoi)"
            if exp_n: quota_txt += f" | {exp_n} het han"
            if err_n: quota_txt += f" | {err_n} loi"
            # 2) ping chat that bang 1 key con song -> chac chan proxy chay
            ping_key = first_ok_key or keys[0]
            head, color = "", ER
            try:
                body = _json.dumps({"model": "claude-sonnet-4-6", "max_tokens": 20,
                                    "messages": [{"role": "user", "content": "PONG"}]}).encode()
                req = urllib.request.Request(f"{url}/v1/messages", data=body, method="POST",
                                             headers={"x-api-key": ping_key, "anthropic-version": "2023-06-01",
                                                      "Content-Type": "application/json"})
                with urllib.request.urlopen(req, timeout=40) as resp:
                    d = _json.loads(resp.read())
                head, color = ("✓ PROXY OK", OK) if d.get("content") else ("⚠ Tra ve rong", ORANGE)
            except urllib.error.HTTPError as he:
                code = he.code
                if code in (429, 500, 502, 503, 504, 529):
                    head, color = f"⚠ SERVER QUA TAI/LOI ({code}) — key OK, thu lai sau", ORANGE
                elif code == 401:
                    head, color = "✗ KEY SAI (401)", ER
                elif code == 403:
                    head, color = "✗ HET QUOTA / BI CAM (403)", ER
                else:
                    head, color = f"✗ Loi HTTP {code}", ER
            except Exception as e:
                head, color = f"✗ Khong ket noi duoc proxy ({str(e)[:30]})", ER
            if ok_n == 0 and color == OK:
                color = ORANGE   # ping OK nhung khong key nao con quota -> canh bao
            msg = f"{head} | {quota_txt}"
            self.after(0, lambda: self.lbl_proxy_status.configure(text=msg, text_color=color))
            self.after(0, lambda: self.btn_proxy_test.configure(text="Test", state="normal"))

        import threading
        threading.Thread(target=_do, daemon=True).start()

    def _check_deepseek_balance(self):
        keys = self._parse_deepseek_keys()
        if not keys:
            self.btn_ds_balance.configure(text="No keys!")
            self.after(2000, lambda: self.btn_ds_balance.configure(text="Check $"))
            return
        self.btn_ds_balance.configure(text=f"Checking {len(keys)}...", state="disabled")
        self.lbl_ds_total.configure(text="")

        def _do():
            import urllib.request, json as _json
            results = []
            grand_total = 0.0
            for key in keys:
                try:
                    req = urllib.request.Request(
                        "https://api.deepseek.com/user/balance",
                        headers={"Authorization": f"Bearer {key}", "Accept": "application/json"},
                    )
                    with urllib.request.urlopen(req, timeout=10) as resp:
                        data = _json.loads(resp.read())
                    info = data.get("balance_infos", [{}])
                    if info:
                        bal = float(info[0].get("total_balance", 0))
                        grand_total += bal
                        results.append((key, bal))
                    else:
                        results.append((key, -1))
                except Exception:
                    results.append((key, -1))

            # Update textbox with balance info
            def _update():
                self.txt_deepseek_keys.delete("1.0", "end")
                lines = []
                for key, bal in results:
                    if bal >= 0:
                        lines.append(f"{key}\t${bal:.2f}")
                    else:
                        lines.append(f"{key}\tERROR")
                self.txt_deepseek_keys.insert("1.0", "\n".join(lines))
                self.btn_ds_balance.configure(text="Check $", state="normal")
                self.lbl_ds_total.configure(text=f"Total: ${grand_total:.2f}")
            self.after(0, _update)

        import threading
        threading.Thread(target=_do, daemon=True).start()

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

    # ===== KHOA API shopapi.vn =================================================

    def _shopapi_common(self):
        """Nap module veo3top_engine/shopapi_common.py. Loi -> None (khong lam sap GUI)."""
        try:
            engine_dir = str(SUITE_ROOT / "veo3top_engine")
            if engine_dir not in sys.path:
                sys.path.insert(0, engine_dir)
            import shopapi_common
            return shopapi_common
        except Exception:
            return None

    def _shopapi_refresh_key_label(self):
        """Hien nguon khoa + khoa DA CHE. Khong bao gio in khoa day du ra man hinh."""
        try:
            sc = self._shopapi_common()
            if sc is None:
                self.lbl_shopapi_key.configure(
                    text="Khong nap duoc module shopapi (thieu veo3top_engine/shopapi_common.py).",
                    text_color="#E17055")
                return
            key, source = sc.doc_khoa()
            if key:
                self.lbl_shopapi_key.configure(
                    text="Dang dung khoa {0} - nguon: {1}".format(sc.che_khoa(key), source),
                    text_color=T3)
            else:
                self.lbl_shopapi_key.configure(
                    text="CHUA CO KHOA. Chon backend 'API shopapi' ma khong co khoa thi tool "
                         "TU LUI VE duong cu (server/veo3top). Lay khoa o shopapi.vn/dashboard/api-keys.",
                    text_color="#E17055")
        except Exception:
            pass

    def _shopapi_save_key(self):
        """Ghi khoa vao kho khoa cua may (%APPDATA%\\ShopAPI\\ve3-suite\\khoa.txt)."""
        sc = self._shopapi_common()
        if sc is None:
            messagebox.showerror("Loi", "Khong nap duoc module shopapi_common.")
            return
        key = (self.ent_shopapi_key.get() or "").strip()
        if not key:
            messagebox.showwarning("Loi", "O khoa dang trong.")
            return
        try:
            path = sc.luu_khoa(key)
        except Exception as e:
            messagebox.showerror("Loi", "Khong ghi duoc khoa: {0}".format(e))
            return
        # Xoa khoi o nhap ngay sau khi luu: khong de khoa nam tren man hinh.
        self.ent_shopapi_key.delete(0, "end")
        self._shopapi_refresh_key_label()
        messagebox.showinfo("Da luu", "Da luu khoa vao:\n{0}\n\n(Nam NGOAI kho ma nguon)".format(path))

    def _shopapi_forget_key(self):
        """Xoá hẳn khoá khỏi máy này — có hỏi lại, và KHÔNG nói dối khi xoá hụt.

        Bản trước gọi `quen_khoa()` rồi im. `quen_khoa()` chỉ xoá **kho khoá
        riêng của máy**; khoá đặt bằng biến môi trường `SHOPAPI_KEY` /
        `SHOPAPI_API_KEY`, hoặc file trỏ bởi `SHOPAPI_KEY_FILE`, đứng TRƯỚC kho
        khoá trong `doc_khoa()` nên vẫn còn nguyên. Người dùng bấm "xoá", màn
        hình vẫn hiện y khoá cũ, không một lời giải thích — nhìn như nút hỏng.

        Đây là thứ tệ hơn hỏng: người ta tưởng đã gỡ khoá khỏi máy mà thật ra
        chưa. Nên giờ xoá xong phải HỎI LẠI `doc_khoa()`, còn khoá thì chỉ đích
        danh chỗ nó nằm và cách gỡ.
        """
        sc = self._shopapi_common()
        if sc is None:
            return
        key_cu, nguon_cu = "", ""
        try:
            key_cu, nguon_cu = sc.doc_khoa()
        except Exception:
            pass
        if not key_cu:
            messagebox.showinfo("Khong co gi de xoa", "May nay chua co khoa API nao.")
            self._shopapi_refresh_key_label()
            return
        if not messagebox.askyesno(
                "Xoa khoa API?",
                "Xoa khoa {0} khoi may nay?\n\nNguon: {1}\n\n"
                "Sau khi xoa, tool KHONG gui duoc job nao qua api.shopapi.vn cho toi khi "
                "dan khoa moi.\n\nKhoa tren tai khoan shopapi.vn KHONG bi anh huong — muon "
                "vo hieu hoa han thi vao shopapi.vn/dashboard/api-keys de thu hoi.".format(
                    sc.che_khoa(key_cu), nguon_cu)):
            return
        try:
            sc.quen_khoa()
        except Exception as e:
            messagebox.showerror("Loi", "Khong xoa duoc kho khoa: {0}".format(e))
            return
        self.ent_shopapi_key.delete(0, "end")
        self._shopapi_refresh_key_label()

        con, nguon_con = "", ""
        try:
            con, nguon_con = sc.doc_khoa()
        except Exception:
            pass
        if not con:
            messagebox.showinfo("Da xoa", "Da xoa khoa khoi may nay.")
            return
        # Xoá hụt: khoá đến từ môi trường, GUI không với tới được.
        messagebox.showwarning(
            "Da xoa kho khoa, NHUNG VAN CON KHOA",
            "Da xoa kho khoa rieng cua may, nhung tool VAN doc duoc mot khoa khac:\n\n"
            "    {0}\n    nguon: {1}\n\n"
            "Nguon nay nam NGOAI tam voi cua giao dien. Go bang tay:\n\n"
            "  - Bien moi truong: mo 'Sua bien moi truong' cua Windows, xoa SHOPAPI_KEY "
            "va SHOPAPI_API_KEY, roi MO LAI tool.\n"
            "  - Hoac neu la SHOPAPI_KEY_FILE: xoa bien do (hoac xoa file no tro toi).".format(
                sc.che_khoa(con), nguon_con))

    def _shopapi_check_key(self):
        """GET /v1/balance -> hien so du. Chay o luong rieng: dung goi mang tren luong GUI."""
        sc = self._shopapi_common()
        if sc is None:
            messagebox.showerror("Loi", "Khong nap duoc module shopapi_common.")
            return
        key = (self.ent_shopapi_key.get() or "").strip() or None   # trong -> dung khoa da luu
        self.lbl_shopapi_key.configure(text="Dang kiem khoa...", text_color=T3)

        def _run():
            ok, msg = sc.kiem_khoa(api_key=key)
            def _show():
                # Giu nguyen ket qua tren man hinh (KHONG refresh de len): so du moi la
                # cai nguoi dung bam nut de xem.
                self.lbl_shopapi_key.configure(text=msg, text_color=(OK if ok else "#E17055"))
            self.after(0, _show)
        threading.Thread(target=_run, daemon=True).start()

    def load_config(self, cfg):
        self._render()
        self.ent_retry.delete(0, "end")
        self.ent_retry.insert(0, str(cfg.get("retry_count", 3)))
        self.opt_ar.set(cfg.get("flow_aspect_ratio", "landscape"))
        backend_value = (cfg.get("generation_backend") or cfg.get("generation_mode") or "server").strip().lower()
        self.opt_generation_backend.set(self.generation_backend_labels.get(backend_value, "Server"))
        provider_value = (cfg.get("excel_ai_provider", "") or "deepseek").strip() or "deepseek"
        self.opt_excel_ai_provider.set(self.excel_ai_provider_labels.get(provider_value, "DeepSeek"))
        engine_value = (cfg.get("excel_engine", "") or "api").strip().lower() or "api"
        self.opt_excel_engine.set(self.excel_engine_labels.get(engine_value, self.excel_engine_labels["api"]))
        backend_v = (cfg.get("claude_cli_backend", "") or "cli").strip().lower() or "cli"
        self.opt_claude_backend.set(self.claude_backend_labels.get(backend_v, self.claude_backend_labels["cli"]))
        self.ent_claude_parallel.delete(0, "end")
        self.ent_claude_parallel.insert(0, str(cfg.get("claude_cli_max_parallel", 5) or 5))
        if bool(cfg.get("claude_cli_review", True)):
            self.sw_claude_review.select()
        else:
            self.sw_claude_review.deselect()
        self.ent_deepseek_slots.delete(0, "end")
        self.ent_deepseek_slots.insert(0, str(cfg.get("deepseek_parallel_slots", cfg.get("excel_workers", 4)) or 4))
        self.ent_vov_slots.delete(0, "end")
        self.ent_vov_slots.insert(0, str(cfg.get("vov_direct_parallel_slots", 0) or 0))
        self._on_excel_ai_provider_change(self.opt_excel_ai_provider.get())
        # Load all DeepSeek keys into textbox
        all_keys = []
        main_key = str(cfg.get("deepseek_api_key", "") or "").strip()
        if main_key:
            all_keys.append(main_key)
        for k in (cfg.get("deepseek_api_keys", []) or []):
            k = str(k).strip()
            if k and k not in all_keys:
                all_keys.append(k)
        self.txt_deepseek_keys.delete("1.0", "end")
        if all_keys:
            self.txt_deepseek_keys.insert("1.0", "\n".join(all_keys))
        self.ent_deepseek_model.delete(0, "end")
        self.ent_deepseek_model.insert(0, str(cfg.get("deepseek_model", "") or "deepseek-v4-pro"))
        self.ent_claude_proxy_url.delete(0, "end")
        self.ent_claude_proxy_url.insert(0, str(cfg.get("claude_cli_anthropic_base_url", "") or "https://vip.digishop.work"))
        # NHIEU KEY digishop: gom claude_cli_anthropic_keys (list) + key don cu -> textbox moi dong 1 key
        _ck = []
        _main_ck = str(cfg.get("claude_cli_anthropic_key", "") or "").strip()
        if _main_ck: _ck.append(_main_ck)
        for k in (cfg.get("claude_cli_anthropic_keys", []) or []):
            k = str(k).strip()
            if k and k not in _ck: _ck.append(k)
        self.txt_claude_proxy_keys.delete("1.0", "end")
        if _ck: self.txt_claude_proxy_keys.insert("1.0", "\n".join(_ck))
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
        auth_mode = cfg.get("flow_auth_mode", "chrome")
        self.opt_flow_auth_mode.set("Extension" if auth_mode == "extension" else "Chrome UI")
        if cfg.get("music_workspace_mode_enabled", True):
            self.sw_music_workspace.select()
        else:
            self.sw_music_workspace.deselect()
        if cfg.get("use_local_token_for_image", False):
            self.sw_use_local_token.select()
        else:
            self.sw_use_local_token.deselect()
        if cfg.get("image_hide_chrome", True):   # mặc định ẩn
            self.sw_img_hide.select()
        else:
            self.sw_img_hide.deselect()
        # LOGIN egress: 4G proxies (list) + WARP
        try:
            _g4 = cfg.get("login_4g_proxies") or []
            if isinstance(_g4, str): _g4 = [x.strip() for x in _g4.replace("\n", ",").split(",") if x.strip()]
            self.txt_login_4g.delete("1.0", "end")
            if _g4: self.txt_login_4g.insert("1.0", "\n".join(str(x).strip() for x in _g4))
            if cfg.get("login_use_warp", True): self.sw_login_warp.select()
            else: self.sw_login_warp.deselect()
        except Exception:
            pass
        try:
            for _ent, _key, _def in ((self.ent_img_accounts, "image_pool_accounts", 24),
                                     (self.ent_img_tokchrome, "image_token_chromes", 6),
                                     (self.ent_img_recycle, "image_token_recycle", 10),
                                     (self.ent_vid_tokchrome, "video_token_chromes", 3),
                                     (self.ent_vid_workers, "video_workers_per_account", 7),
                                     (self.ent_iso_hours, "pool_isolation_hours", 6),
                                     (self.ent_img_codes, "max_concurrent_image_codes", 0),
                                     (self.ent_vid_codes, "max_concurrent_video_codes", 0),
                                     (self.ent_api_ma, "shopapi_ma_song_song",
                                      SHOPAPI_MA_SONG_SONG_MAC_DINH),
                                     (self.ent_api_anh, "max_concurrent", 24),
                                     (self.ent_api_video, "shopapi_video_concurrency", 16)):
                _ent.delete(0, "end"); _ent.insert(0, str(cfg.get(_key, _def)))
        except Exception:
            pass
        img_mode = str(cfg.get("veo3top_image_mode") or "").strip().lower()
        if img_mode in ("ultra", "veo3top_b_ultra"):
            img_mode = "account"
        self.opt_image_backend.set(self.image_backend_labels.get(img_mode if img_mode in ("blank", "account", "pool", "shopapi") else "", "Mac dinh"))
        self._shopapi_refresh_key_label()
        # Đổi chế độ ở HAI ô riêng (ảnh và video) mà chỉ một trong hai là API
        # thì vẫn còn Chrome — nên phải hỏi lại cả hai mỗi lần một ô đổi.
        for _opt in (self.opt_generation_backend, self.opt_image_backend):
            try:
                _opt.configure(command=lambda _v=None: self._doi_che_do_sinh())
            except Exception:
                pass
        self._cap_nhat_num_theo_che_do()

    def _doi_che_do_sinh(self):
        """Người dùng vừa đổi ô 'Generation' hoặc 'Tao anh' — làm mới nhóm núm.

        `_chi_dung_shopapi` đọc `self.config_data`, mà cấu hình chỉ được ghi lúc
        bấm Save. Nên phải soi thẳng hai ô đang chọn, nếu không giao diện chỉ
        đổi sau khi lưu — tức là đúng lúc người dùng không còn nhìn nó nữa.
        """
        try:
            tam = dict(getattr(self.app, "config_data", {}) or {})
            tam["generation_backend"] = self.generation_backend_options.get(
                self.opt_generation_backend.get().strip(), "server")
            tam["veo3top_image_mode"] = self.image_backend_options.get(
                self.opt_image_backend.get().strip(), "")
            self._cfg_dang_xem = tam
            self._cap_nhat_num_theo_che_do()
            # Hai bảng trạm ở trang Overview cũng phải đổi nhãn theo.
            try:
                self.app.pages["home"]._dat_nhan_tram()
            except Exception:
                pass
        except Exception:
            pass

    def _doc_tran_may_chu(self):
        """Hỏi `GET /v1/me` rồi hiện trần THẬT cạnh ba núm, kèm phép nhân.

        Đặt hai vế cạnh nhau là cách duy nhất thấy được mình đang xin bao nhiêu
        so với được cấp bao nhiêu. Ngày 11/08/2026 máy chủ cấp 691 chỗ ảnh trong
        khi tool đặt lên đúng 5,6 — không màn hình nào nói ra chuyện đó, vì
        không màn hình nào đặt hai con số cạnh nhau.

        Chạy ở luồng nền: một lời gọi mạng trong luồng Tk là cửa sổ đứng hình.
        """
        self.lbl_api_tran.configure(text="Dang hoi GET /v1/me...", text_color=T2)

        def _hoi():
            try:
                import sys as _sys
                _engine = str(SUITE_ROOT / "veo3top_engine")
                if _engine not in _sys.path:
                    _sys.path.insert(0, _engine)
                import shopapi_common as _sc
                tran = {loai: int(_sc.tran_song_song(loai, mac_dinh=-1))
                        for loai in ("image", "video")}
            except Exception as e:
                self.after(0, lambda: self.lbl_api_tran.configure(
                    text="Khong hoi duoc /v1/me: {0}: {1}".format(type(e).__name__, e),
                    text_color=ER))
                return

            def _so(ent, mac_dinh):
                try:
                    return max(1, int((ent.get() or "").strip() or mac_dinh))
                except (TypeError, ValueError):
                    return mac_dinh

            ma = _so(self.ent_api_ma, SHOPAPI_MA_SONG_SONG_MAC_DINH)
            xin = {"image": ma * _so(self.ent_api_anh, 24),
                   "video": ma * _so(self.ent_api_video, 16)}
            dong = []
            cang = False
            for loai in ("image", "video"):
                t = tran.get(loai, -1)
                if t < 0:
                    dong.append("{0}: khong doc duoc".format(loai))
                    continue
                if t == 0:
                    dong.append("{0}: nha may DANG DUNG (tran 0)".format(loai))
                    cang = True
                    continue
                dong.append("{0}: dat len {1} / may chu cap {2} ({3:.0%})".format(
                    loai, xin[loai], t, xin[loai] / t))
                if xin[loai] > t:
                    cang = True
            them = ("  ->  Dang XIN QUA tran: phan du nam hang cho, khong nhanh them."
                    if cang else "  ->  Trong tran.")
            self.after(0, lambda: self.lbl_api_tran.configure(
                text="{0} x (anh {1} / video {2}) = {3}{4}".format(
                    ma, _so(self.ent_api_anh, 24), _so(self.ent_api_video, 16),
                    " · ".join(dong), them),
                text_color=(ER if cang else OK)))

        threading.Thread(target=_hoi, daemon=True).start()

    def _cap_nhat_num_theo_che_do(self):
        """Làm MỜ nhóm núm không được đọc ở chế độ đang chạy.

        Đi API shopapi thì tám núm pool (account pool, token chrome, recycle,
        luồng/account ultra, nghỉ 429, mã ảnh/video) KHÔNG có đường nào chạm tới
        engine: không Chrome nào mở, không account Flow nào nạp. Để chúng sáng
        như thường là mời người dùng ngồi tinh chỉnh những con số không nối vào
        đâu — và đó đúng là chuyện đã xảy ra suốt sáng 11/08/2026.

        Làm mờ chứ KHÔNG ẩn: người dùng còn quay lại đường Chrome, và giá trị cũ
        phải nhìn thấy được để biết mình sẽ quay về đâu.
        """
        # Dùng hàm CẤP MODULE. Bản trước gọi `self._chi_dung_shopapi(...)` — một
        # phương thức của `VE3App`, không phải của lớp này — nên nó ném
        # `AttributeError`, bị `except` nuốt, và giao diện lặng lẽ coi như không
        # đi API: núm chết vẫn sáng, nhãn vẫn nói dối. Xem `che_do_toan_api`.
        di_api = che_do_toan_api(getattr(self, "_cfg_dang_xem", None)
                                 or getattr(self.app, "config_data", {}) or {})
        num_pool = (self.ent_img_accounts, self.ent_img_tokchrome, self.ent_img_recycle,
                    self.ent_vid_tokchrome, self.ent_vid_workers, self.ent_iso_hours,
                    self.ent_img_codes, self.ent_vid_codes)
        for ent in num_pool:
            try:
                ent.configure(text_color=(T3 if di_api else T1),
                              border_color=(BD if di_api else BD))
            except Exception:
                pass
        try:
            self.lbl_chu_thich_pool.configure(
                text_color=(T3 if di_api else T2),
                text=(("[KHONG DUNG O CHE DO API] Tam num tren la cua duong Chrome/pool. "
                       "Dang chay API shopapi -> khong num nao trong so do duoc doc. "
                       "Chinh o khung 'API shopapi' ben duoi.")
                      if di_api else self.lbl_chu_thich_pool.cget("text")))
        except Exception:
            pass
        try:
            self.lbl_parallel_jobs.configure(
                text=("TU DONG (may chu quyet moi lo — xem khung 'API shopapi')"
                      if di_api else "TU DONG (theo so chrome / so ma)"))
        except Exception:
            pass
        # Ba mục chỉ phục vụ ĐĂNG NHẬP Chrome: proxy 4G, WARP, token local Pro.
        # Đi API thì không có lượt đăng nhập nào để mà chọn đường ra mạng.
        for w in (getattr(self, "txt_login_4g", None), getattr(self, "sw_login_warp", None),
                  getattr(self, "btn_warp_setup", None), getattr(self, "sw_use_local_token", None)):
            try:
                w.configure(state=("disabled" if di_api else "normal"))
            except Exception:
                pass
        self._gap_khoi_chrome(di_api)

    def _gap_khoi_chrome(self, di_api):
        """Gấp khối "Server + Gmail + Chrome" khi đi API, mở lại khi về đường cũ.

        Khối này là mười dòng server kèm gmail và đường dẫn chrome.exe — nó
        chiếm nửa trang Cài đặt. Ở chế độ API **không dòng nào được đọc**: không
        server nào được gọi, không Chrome nào mở, không tài khoản Flow nào nạp.
        Để nguyên là bắt người dùng cuộn qua nửa trang thông tin chết mới tới
        được ba con số thật sự điều khiển được thứ gì.

        GẤP chứ không XOÁ, và có nút mở lại: cấu hình vẫn còn nguyên trong
        `settings.yaml`, người dùng còn quay về đường Chrome bất cứ lúc nào —
        giấu hẳn thì họ tưởng mất.
        """
        try:
            if di_api and not getattr(self, "_ep_hien_chrome", False):
                self.frame_server.grid_remove()
                self.thanh_gap_chrome.grid(row=0, column=0, sticky="ew", padx=10, pady=(10,4))
            else:
                self.thanh_gap_chrome.grid_remove()
                self.frame_server.grid(row=0, column=0, sticky="ew", padx=10, pady=(10,4))
        except Exception:
            pass

    def _mo_lai_khoi_chrome(self):
        self._ep_hien_chrome = True
        self._gap_khoi_chrome(False)

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

    def _setup_warp(self):
        """Cài + đăng ký Cloudflare WARP (máy chưa có) -> bật Local proxy :40000 để dùng cho login.
        Tải MSI (nếu chưa cài) -> cài im lặng (cần admin) -> registration new -> mode proxy -> connect."""
        def _set(msg, color=T3):
            try: self.after(0, lambda: self.lbl_saved.configure(text=msg, text_color=color))
            except Exception: pass
        def _do():
            import subprocess, os, tempfile, urllib.request, socket
            WARP = r"C:\Program Files\Cloudflare\Cloudflare WARP\warp-cli.exe"
            try:
                self.after(0, lambda: self.btn_warp_setup.configure(text="...", state="disabled"))
                # 1) chưa cài -> TẢI + CÀI
                if not os.path.exists(WARP):
                    _set("WARP: đang tải Cloudflare WARP...", T3)
                    msi = os.path.join(tempfile.gettempdir(), "Cloudflare_WARP.msi")
                    _rq = urllib.request.Request("https://1111-releases.cloudflareclient.com/win/latest",
                                                 headers={"User-Agent": "Mozilla/5.0"})   # thiếu UA -> 403
                    with urllib.request.urlopen(_rq, timeout=120) as _resp, open(msi, "wb") as _f:
                        _f.write(_resp.read())
                    _set("WARP: đang cài (bấm YES nếu hỏi quyền admin)...", T3)
                    subprocess.run(["msiexec", "/i", msi, "/qn", "/norestart"], timeout=300)
                if not os.path.exists(WARP):
                    _set("❌ Cài WARP thất bại (thử tải tay: 1.1.1.1)", ER); return
                # 2) đăng ký (free) + bật proxy mode :40000 + connect
                _set("WARP: đăng ký + bật Local proxy :40000...", T3)
                for a in (["registration", "new"], ["mode", "proxy"], ["proxy", "port", "40000"], ["connect"]):
                    subprocess.run([WARP, "--accept-tos", *a], capture_output=True, text=True, timeout=40)
                import time as _t; _t.sleep(4)
                # 3) kiểm port 40000 mở chưa
                s = socket.socket(); s.settimeout(3)
                ok = (s.connect_ex(("127.0.0.1", 40000)) == 0); s.close()
                if ok:
                    _set("✅ WARP sẵn sàng (Local proxy :40000) — bật switch + Save để dùng cho login", OK)
                    self.after(0, lambda: self.sw_login_warp.select())
                else:
                    _set("⚠️ WARP cài xong nhưng chưa nghe :40000 — mở app Cloudflare WARP, chọn Mode=Local proxy", "#FF8C00")
            except Exception as e:
                _set(f"❌ WARP lỗi: {str(e)[:60]}", ER)
            finally:
                self.after(0, lambda: self.btn_warp_setup.configure(text="⬇ Setup WARP", state="normal"))
        import threading
        threading.Thread(target=_do, daemon=True).start()

    def _auto_setup(self):
        """Đọc tài nguyên máy (CPU/RAM) + đếm số account ảnh/video -> tính thông số TỐI ƯU -> tự điền vào các ô.
        Bypass = curl thuần (nhẹ) nên ảnh dùng HẾT account; token chrome chỉ là fallback -> để thấp theo CPU."""
        import os, sys
        # 1) tài nguyên máy
        try:
            import psutil
            cores = psutil.cpu_count(logical=True) or 8
            ram_gb = psutil.virtual_memory().total / 1e9
        except Exception:
            cores, ram_gb = (os.cpu_count() or 8), 16.0
        # 2) đếm account ẢNH (sqlite accounts.db: account có password+totp — nguồn thật image_pool_browser dùng)
        #    + VIDEO (pool_accounts.load_pool_accounts). Đọc trực tiếp -> KHÔNG import module nặng (cdp_chrome...).
        n_img, n_vid = 96, 10
        suite = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        try:
            import sqlite3
            dbp = os.path.join(suite, "accounts", "accounts.db")
            if os.path.isfile(dbp):
                _c = sqlite3.connect(dbp)
                _n = _c.execute("SELECT COUNT(DISTINCT lower(email)) FROM accounts "
                                "WHERE password!='' AND totp_secret!=''").fetchone()[0]
                _c.close()
                if _n:
                    n_img = int(_n)
        except Exception:
            pass
        try:
            eng = os.path.join(suite, "veo3top_engine")
            if eng not in sys.path:
                sys.path.insert(0, eng)
            import pool_accounts as _pa
            _vi = _pa.load_pool_accounts(); n_vid = len(_vi) if _vi else n_vid
        except Exception:
            pass
        # 3) TÍNH TỐI ƯU
        img_slots = max(1, min(n_img, 100))                              # ảnh dùng hết account (bypass nhẹ)
        codes = max(2, min(max(4, img_slots // 10),                      # ~10 job ảnh/mã để fill pool
                           int(ram_gb // 3),                             # RAM (~0.3GB/mã)
                           max(4, cores),                                # CPU
                           20))
        tok_img = max(2, min(cores // 2, 6))                             # token chrome FALLBACK (CPU là trần)
        tok_vid = max(2, min(cores // 3, 4))
        recycle = 10
        vid_workers = 7                                                  # đã chứng minh gần tối ưu (render-bound)
        giveup = 2
        # 4) ĐIỀN vào ô
        def _set(ent, val):
            try:
                ent.delete(0, "end"); ent.insert(0, str(val))
            except Exception:
                pass
        _set(self.ent_img_accounts, img_slots)
        _set(self.ent_img_codes, 0)     # 0 = TU TINH theo nhan su (khong dien tay)
        _set(self.ent_vid_codes, 0)
        _set(self.ent_img_tokchrome, tok_img)
        _set(self.ent_img_recycle, recycle)
        _set(self.ent_vid_tokchrome, tok_vid)
        _set(self.ent_vid_workers, vid_workers)
        try:
            self.lbl_saved.configure(
                text=f"⚙ Auto: {cores} core · {ram_gb:.0f}GB · {n_img} acc ảnh · {n_vid} acc video → "
                     f"slot ảnh {img_slots}, {codes} mã song song. Bấm Save + restart để áp.",
                text_color="#00B894")
        except Exception:
            pass

    def _save(self):
        cfg = self.app.config_data
        # max_concurrent (Parallel jobs) giờ TỰ ĐỘNG (worker tính theo pool/số mã) -> không lưu từ GUI nữa
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
        cfg["flow_auth_mode"] = "extension" if self.opt_flow_auth_mode.get() == "Extension" else "chrome"
        cfg["music_workspace_mode_enabled"] = bool(self.sw_music_workspace.get())
        cfg["use_local_token_for_image"] = bool(self.sw_use_local_token.get())
        cfg["image_hide_chrome"] = bool(self.sw_img_hide.get())
        cfg["login_4g_proxies"] = [ln.strip() for ln in self.txt_login_4g.get("1.0", "end").splitlines() if ln.strip()]
        cfg["login_use_warp"] = bool(self.sw_login_warp.get())
        def _knob(ent, key, default, lo, hi):
            try:
                cfg[key] = max(lo, min(hi, int((ent.get() or str(default)).strip())))
            except Exception:
                cfg[key] = default
        _knob(self.ent_img_accounts, "image_pool_accounts", 24, 1, 100)
        _knob(self.ent_img_tokchrome, "image_token_chromes", 6, 1, 16)   # token chrome anh (CPU la tran)
        _knob(self.ent_img_recycle,   "image_token_recycle", 10, 2, 50)  # recycle token
        _knob(self.ent_vid_tokchrome, "video_token_chromes", 3, 1, 16)   # token chrome video
        _knob(self.ent_vid_workers,   "video_workers_per_account", 7, 1, 30)  # luong/account ultra
        _knob(self.ent_iso_hours,     "pool_isolation_hours", 6, 1, 48)   # cach ly account 429 het quota ngay (anh+video), gio
        _knob(self.ent_img_codes,     "max_concurrent_image_codes", 0, 0, 50)   # tram ANH: 0 = TU TINH; >0 co dinh
        _knob(self.ent_vid_codes,     "max_concurrent_video_codes", 0, 0, 20)   # tram VIDEO: 0 = TU TINH; >0 co dinh
        # ═══ 0 = THEO MÁY CHỦ. ĐỪNG GÕ CỨNG TÀI NGUYÊN — 13/08/2026 ═══
        #
        # Chủ dự án: *"server xử lý được bao nhiêu thì cứ dùng bấy nhiêu, đừng
        # có làm cứng, mà nên có logic thông minh để khai thác tối đa server"*.
        #
        # Bản trước kẹp `lo=1`, nên KHÔNG THỂ gõ 0 — mà 0 mới là giá trị có
        # nghĩa "không ghim, đi theo trần động của máy chủ" (`ve3_worker`
        # ._chay_me_shopapi đã hiểu đúng như vậy từ lâu). Trần trên thì ghim
        # 384/64, hai con số đã cũ: máy chủ hôm nay công bố `hard_cap` ảnh
        # 1536, video 832, và trần ĐỘNG là 979 / 374.
        #
        # Hậu quả đo được: tool ghim ở 40, máy chủ mở 979, người dùng thấy
        # **0,8 ảnh/phút** trên hàng chờ 441 ảnh — 7,4 giờ cho việc lẽ ra xong
        # trong khoảng mười phút.
        #
        # Nay: 0 là MẶC ĐỊNH và là giá trị nhỏ nhất. Muốn tự ghim thì vẫn ghim
        # được, nhưng đó phải là lựa chọn có ý thức chứ không phải mặc định.
        # Trần trên để rộng — `_hoi_tran` đã kẹp bằng `min(trần động máy chủ,
        # hard_cap, trần tool)` ở mỗi lô, nên máy chủ mới là người chặn thật.
        _knob(self.ent_api_ma,    "shopapi_ma_song_song",      SHOPAPI_MA_SONG_SONG_MAC_DINH, 1, 64)
        _knob(self.ent_api_anh,   "max_concurrent",            0, 0, 4096)
        _knob(self.ent_api_video, "shopapi_video_concurrency", 0, 0, 4096)
        cfg["veo3top_image_mode"] = self.image_backend_options.get(self.opt_image_backend.get().strip(), "")
        selected_provider_label = self.opt_excel_ai_provider.get().strip() or "DeepSeek"
        cfg["excel_ai_provider"] = self.excel_ai_provider_options.get(selected_provider_label, "deepseek")
        selected_engine_label = self.opt_excel_engine.get().strip()
        cfg["excel_engine"] = self.excel_engine_options.get(selected_engine_label, "api")
        cfg["claude_cli_backend"] = self.claude_backend_options.get(self.opt_claude_backend.get().strip(), "cli")
        try:
            cfg["claude_cli_max_parallel"] = max(1, int(self.ent_claude_parallel.get().strip() or "5"))
        except Exception:
            cfg["claude_cli_max_parallel"] = 5
        cfg["claude_cli_review"] = bool(self.sw_claude_review.get())
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
        ds_keys = self._parse_deepseek_keys()
        cfg["deepseek_api_key"] = ds_keys[0] if ds_keys else ""
        cfg["deepseek_api_keys"] = ds_keys
        cfg["deepseek_model"] = self.ent_deepseek_model.get().strip() or "deepseek-v4-pro"
        cfg["claude_cli_anthropic_base_url"] = self.ent_claude_proxy_url.get().strip()
        # NHIEU KEY digishop: moi dong 1 key -> list. Key don cu = key dau (tuong thich nguoc engine cu).
        _ck_keys = []
        for ln in self.txt_claude_proxy_keys.get("1.0", "end").splitlines():
            ln = ln.strip()
            if ln and ln not in _ck_keys: _ck_keys.append(ln)
        cfg["claude_cli_anthropic_key"] = _ck_keys[0] if _ck_keys else ""
        cfg["claude_cli_anthropic_keys"] = _ck_keys
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
        self.queue_ve3_stage = {}      # {code: "image"|"video"|"all"} - trạm đang chạy mã (tách concurrency ảnh/video)
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
        self._project_state_cache_ttl = 30.0
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
        self._load_config(); self._build()
        # Main-thread watchdog: detect and log when event loop is blocked
        self._watchdog_last_tick = _time.time()
        self._watchdog_main_thread_id = threading.get_ident()
        self._start_watchdog()
        # Cleanup phien truoc (Chrome/agent con sot lai)
        self._kill_extension_instances()
        self._kill_veo3top_chromes()   # don chrome token-factory veo3top sot lai tu phien truoc
        self.after(400, self._boot)

    def _start_watchdog(self):
        def _watchdog_loop():
            _wd_log_path = str(Path(os.environ.get("TEMP", ".")) / "ve3_watchdog.log")
            _wd_last_report = 0.0
            while not getattr(self, "_closing", False):
                _time.sleep(5)
                elapsed = _time.time() - self._watchdog_last_tick
                if elapsed > 5.0:
                    now = _time.time()
                    if elapsed > 60 and now - _wd_last_report < 300:
                        continue
                    _wd_last_report = now
                    import sys, traceback as _tb
                    frames = sys._current_frames()
                    main_frame = frames.get(self._watchdog_main_thread_id)
                    if main_frame:
                        stack = "".join(_tb.format_stack(main_frame))
                    else:
                        stack = "(no main thread frame)"
                    try:
                        log_size = Path(_wd_log_path).stat().st_size if Path(_wd_log_path).exists() else 0
                        if log_size > 10 * 1024 * 1024:
                            Path(_wd_log_path).write_text("", encoding="utf-8")
                    except Exception:
                        pass
                    with open(_wd_log_path, "a", encoding="utf-8") as f:
                        f.write(f"\n{'='*60}\n")
                        f.write(f"[WATCHDOG] Main thread blocked {elapsed:.1f}s at {_time.strftime('%H:%M:%S')}\n")
                        f.write(f"Threads: {threading.active_count()}\n")
                        f.write(f"Main thread stack:\n{stack}\n")
                        for tid, frame in frames.items():
                            if tid == self._watchdog_main_thread_id:
                                continue
                            tname = "?"
                            for t in threading.enumerate():
                                if t.ident == tid:
                                    tname = t.name
                                    break
                            f.write(f"\n--- Thread {tid} ({tname}) ---\n")
                            f.write("".join(_tb.format_stack(frame)))
        threading.Thread(target=_watchdog_loop, daemon=True, name="watchdog").start()
        self._watchdog_tick()

    def _watchdog_tick(self):
        self._watchdog_last_tick = _time.time()
        if not getattr(self, "_closing", False):
            old = getattr(self, "_watchdog_timer_id", None)
            if old is not None:
                self.after_cancel(old)
            self._watchdog_timer_id = self.after(2000, self._watchdog_tick)

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
        # ═══ NÓI RA RẰNG MÌNH ĐANG ĐÓNG, VÀ AI BẢO ĐÓNG ═══
        #
        # Hàm này giết sạch subprocess rồi `destroy()` mà KHÔNG ghi một dòng
        # nào. Dấu vết nó để lại y hệt một cú crash: worker thoát `exit code=1`
        # đồng loạt, cửa sổ biến mất, không traceback, không sự kiện lỗi Windows.
        #
        # Ngày 11/08/2026 mất hai lượt chẩn đoán vì đúng chỗ này: tool "chết"
        # hai lần, và cả hai lần đều là CỬA SỔ BỊ ĐÓNG chứ không phải hỏng hóc —
        # nhưng không có cách nào phân biệt. Một dòng log tách được hai chuyện
        # khác hẳn nhau: "ai đó đóng tool" và "tool tự chết".
        try:
            _n = len([p for p in (self.queue_ve3_procs or {}).values()
                      if p is not None and p.poll() is None])
        except Exception:
            _n = -1
        self._log("=== ĐÓNG CỬA SỔ (WM_DELETE_WINDOW) — sẽ giết {0} worker đang chạy. "
                  "Nếu bạn KHÔNG bấm đóng thì có thứ khác đã bấm hộ (script auto-click, "
                  "Alt+F4, hoặc lệnh tắt máy).".format(_n), "WARN", "ve3")
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
            # Kill extension Chrome + agents (y het FlowKit _on_stop)
            self._kill_extension_instances()
            # Kill chrome token-factory cua veo3top option (theo PID da dang ky) — ke ca orphan/blank-mode
            self._kill_veo3top_chromes()
        finally:
            try:
                self.destroy()
            except Exception:
                pass

    def _kill_pool_services(self):
        """Kill service NHÀ MÁY CHUNG (video port 8788 + ảnh 8789) khi tắt/restart — taskkill /T kill luôn
        chrome token con của service. Xoá PID + lock file."""
        import subprocess as _sp
        CF = 0x08000000
        for pidf, lockf in ((".veo3top_pool.pid", ".veo3top_pool_start.lock"),
                            (".veo3top_imgpool.pid", ".veo3top_imgpool_start.lock")):
            try:
                p = SUITE_ROOT / pidf
                pid = (p.read_text() or "").strip() if p.exists() else ""
                if pid.isdigit() and int(pid) > 4:
                    _sp.run(['taskkill', '/F', '/T', '/PID', pid], capture_output=True, timeout=8, creationflags=CF)
            except Exception:
                pass
            for fn in (pidf, lockf):
                try:
                    (SUITE_ROOT / fn).unlink(missing_ok=True)
                except Exception:
                    pass
        # Kill chrome của nhà máy ảnh (profile pool_img_profiles) + tiến trình GUI quản lý (image_pool_gui)
        # -> không để chrome/GUI orphan khi tắt tool.
        try:
            _sp.run(['powershell', '-NoProfile', '-Command',
                     # TREE-KILL (/T) chrome nhà máy ảnh+video -> KHÔNG mồ côi children thành zombie (chrome rác).
                     # Match CẢ profile account (pool_img/vid_profiles) LẪN token chrome (veo3tok_97x = 970 video/974 ảnh)
                     # + warm profile -> chrome reparent (tách python) vẫn bị dọn theo cmdline (fix 5 zombie token).
                     "Get-CimInstance Win32_Process -Filter \"Name='chrome.exe'\" | "
                     "Where-Object { $_.CommandLine -match 'pool_img_profiles|pool_vid_profiles|veo3tok_97|veo3top_warm_profiles' } | "
                     "ForEach-Object { taskkill /F /T /PID $_.ProcessId 2>$null }; "
                     # kill LUÔN service pool còn sót theo cmdline (phòng khi PID file mất -> service orphan respawn chrome).
                     # THÊM video_factory.py/image_factory.py = service MẶC ĐỊNH (không phải *_pool_browser) -> fix python zombie.
                     "Get-CimInstance Win32_Process | Where-Object { $_.Name -match '^pythonw?\\.exe$' -and $_.CommandLine -and "
                     "($_.CommandLine -match 'image_pool_browser|video_pool_browser|video_factory\\.py|image_factory\\.py') } | "
                     "ForEach-Object { taskkill /F /T /PID $_.ProcessId 2>$null }; "
                     # dọn chrome ZOMBIE (đã chết, không ExecutablePath)
                     "Get-CimInstance Win32_Process -Filter \"Name='chrome.exe'\" | Where-Object { -not $_.ExecutablePath } | "
                     "ForEach-Object { Stop-Process -Id $_.ProcessId -Force -EA SilentlyContinue }; "
                     "Get-CimInstance Win32_Process | Where-Object { $_.Name -match '^pythonw?\\.exe$' -and "
                     "$_.CommandLine -and $_.CommandLine.ToLowerInvariant().Contains('image_pool_gui') } | "
                     "ForEach-Object { Stop-Process -Id $_.ProcessId -Force -EA SilentlyContinue }"],
                    capture_output=True, timeout=25, creationflags=CF)
        except Exception:
            pass

    def _open_pool_manager(self):
        """Mở GUI quản lý account nhà máy ảnh/video (image_pool_gui) — cửa sổ riêng."""
        import subprocess as _sp, sys as _sys
        gui = SUITE_ROOT / "veo3top_engine" / "image_pool_gui.py"
        try:
            _sp.Popen([_sys.executable, str(gui)], cwd=str(SUITE_ROOT / "veo3top_engine"))
        except Exception as e:
            try:
                from tkinter import messagebox as _mb; _mb.showerror("Lỗi", f"Không mở được quản lý account: {e}")
            except Exception:
                pass

    def _kill_veo3top_chromes(self):
        """Kill chrome token-factory cua veo3top (PID da dang ky trong .veo3top_pids\\*.pid).
        Bat duoc ca chrome bi orphan khoi subprocess VA blank-mode (system chrome, khong co 'GoogleChromePortable').
        Loc IMAGENAME=chrome.exe de an toan neu PID bi tai su dung."""
        import subprocess as _sp
        CF = 0x08000000
        self._kill_pool_services()   # dọn service nhà máy chung (video 8788 + ảnh 8789) + chrome con
        pid_dir = SUITE_ROOT / ".veo3top_pids"
        if not pid_dir.exists():
            return
        for f in pid_dir.glob("*.pid"):
            try:
                pid = (f.read_text() or "").strip()
                if pid.isdigit() and int(pid) > 4:
                    _sp.run(['taskkill', '/F', '/T', '/FI', f'PID eq {pid}', '/FI', 'IMAGENAME eq chrome.exe'],
                            capture_output=True, timeout=5, creationflags=CF)
            except Exception:
                pass
            try:
                f.unlink()
            except Exception:
                pass
        # Quét sạch: giết MỌI chrome debug-port 9600-9899 (bắt cả orphan .pid cũ/thiếu).
        # An toàn vì hàm này chỉ chạy lúc START/STOP/RESTART — không có mã nào cần giữ.
        try:
            sweep = r'''
$ErrorActionPreference='SilentlyContinue'
$cim = Get-CimInstance Win32_Process -Filter "name='chrome.exe'"; if(-not $cim){ exit }
$cp=@($cim.ProcessId)
$listen = Get-NetTCPConnection -State Listen | ?{ $cp -contains $_.OwningProcess -and $_.LocalPort -ge 9600 -and $_.LocalPort -le 9899 }
$tok=@($listen | Select-Object -ExpandProperty OwningProcess -Unique)
$kill=New-Object System.Collections.Generic.HashSet[int]; $q=New-Object System.Collections.Queue
foreach($r in $tok){ if($kill.Add([int]$r)){ $q.Enqueue([int]$r) } }
while($q.Count -gt 0){ $p=$q.Dequeue(); foreach($c in ($cim|?{[int]$_.ParentProcessId -eq $p})){ $ci=[int]$c.ProcessId; if($kill.Add($ci)){ $q.Enqueue($ci) } } }
foreach($k in $kill){ taskkill /F /T /PID $k 2>$null | Out-Null }
'''
            _sp.run(['powershell', '-NoProfile', '-Command', sweep],
                    capture_output=True, timeout=30, creationflags=CF)
        except Exception:
            pass
        # Dọn profile temp veo3tok_* (chrome đã bị giết -> profile free). An toàn ở start/stop/restart.
        try:
            import os as _os, tempfile as _tf, shutil as _sh, glob as _gl
            for d in _gl.glob(_os.path.join(_tf.gettempdir(), "veo3tok_*")):
                if _os.path.isdir(d):
                    _sh.rmtree(d, ignore_errors=True)
        except Exception:
            pass

    def _reap_orphan_chromes(self):
        """Định kỳ (chạy elevated) giết chrome token-factory MỒ CÔI = worker cha ĐÃ CHẾT.
        Worker chết bẩn -> chrome elevated còn sống -> phình dần (nguyên nhân 'nhiều chrome').
        CHỈ giết chrome debug-port 9600-9899 có cha chết -> KHÔNG đụng mã đang chạy (cha còn sống).
        Trả số chrome đã reap (để log)."""
        import subprocess as _sp
        CF = 0x08000000
        ps = r'''
$ErrorActionPreference='SilentlyContinue'
$alive=@{}; Get-Process | %{ $alive[[int]$_.Id]=$true }
$cim = Get-CimInstance Win32_Process -Filter "name='chrome.exe'"
if(-not $cim){ Write-Output 0; exit }
$cp = @($cim.ProcessId)
$listen = Get-NetTCPConnection -State Listen | ?{ $cp -contains $_.OwningProcess -and $_.LocalPort -ge 9600 -and $_.LocalPort -le 9899 }
$tok = @($listen | Select-Object -ExpandProperty OwningProcess -Unique)
$roots = $cim | ?{ $tok -contains [int]$_.ProcessId -and -not $alive[[int]$_.ParentProcessId] }
if(-not $roots){ Write-Output 0; exit }
$kill = New-Object System.Collections.Generic.HashSet[int]
$q = New-Object System.Collections.Queue
foreach($r in $roots){ $id=[int]$r.ProcessId; if($kill.Add($id)){ $q.Enqueue($id) } }
while($q.Count -gt 0){ $p=$q.Dequeue(); foreach($c in ($cim|?{[int]$_.ParentProcessId -eq $p})){ $ci=[int]$c.ProcessId; if($kill.Add($ci)){ $q.Enqueue($ci) } } }
foreach($k in $kill){ taskkill /F /T /PID $k 2>$null | Out-Null }
Write-Output $kill.Count
'''
        try:
            out = _sp.run(['powershell', '-NoProfile', '-Command', ps],
                          capture_output=True, text=True, timeout=30, creationflags=CF).stdout or ""
            n = int((out.strip().splitlines() or ["0"])[-1] or "0")
            if n:
                try: self._log(f"[reaper] don {n} chrome token mo coi (worker cha da chet)")
                except Exception: pass
            return n
        except Exception:
            return 0

    def _kill_extension_instances(self):
        """Kill all Chrome + agent + cleanup lock files."""
        import subprocess as _sp
        CF = 0x08000000
        for f in SUITE_ROOT.glob(".ext_start_*.lock"):
            try:
                f.unlink()
            except Exception:
                pass
        try:
            (SUITE_ROOT / ".extension_startup.lock").unlink(missing_ok=True)
        except Exception:
            pass
        # Kill all GoogleChromePortable processes
        try:
            _sp.run(['taskkill', '/F', '/IM', 'GoogleChromePortable.exe'],
                    capture_output=True, timeout=5, creationflags=CF)
        except Exception:
            pass
        try:
            _sp.run(['wmic', 'process', 'where',
                     "name='chrome.exe' and CommandLine like '%GoogleChromePortable%'",
                     'call', 'terminate'],
                    capture_output=True, timeout=10, creationflags=CF)
        except Exception:
            pass
        # Kill agents: any process listening on ports 8100-8129
        killed_pids = set()
        try:
            script = (
                "Get-NetTCPConnection -State Listen -ErrorAction SilentlyContinue | "
                "Where-Object { $_.LocalPort -ge 8100 -and $_.LocalPort -le 8129 } | "
                "Select-Object -ExpandProperty OwningProcess -Unique"
            )
            proc = _sp.run(['powershell', '-NoProfile', '-Command', script],
                          capture_output=True, text=True, timeout=10, creationflags=CF)
            for line in (proc.stdout or "").splitlines():
                pid = line.strip()
                if pid.isdigit() and int(pid) > 4:
                    killed_pids.add(pid)
                    _sp.run(['taskkill', '/F', '/T', '/PID', pid],
                            capture_output=True, timeout=5, creationflags=CF)
        except Exception:
            pass
        # Fallback: also kill by command line pattern
        markers = ("flowkit\\\\agent", "flowkit/agent", "agent\\\\main.py", "agent/main.py")
        try:
            proc = _sp.run(['wmic', 'process', 'where',
                           'name="python.exe" or name="pythonw.exe"',
                           'get', 'ProcessId,CommandLine', '/FORMAT:CSV'],
                          capture_output=True, text=True, timeout=8, creationflags=CF)
            for line in (proc.stdout or "").splitlines():
                s = line.strip()
                if not s or not any(m in s.lower() for m in markers):
                    continue
                parts = s.rsplit(',', 1)
                if len(parts) == 2 and parts[1].strip().isdigit() and parts[1].strip() not in killed_pids:
                    _sp.run(['taskkill', '/F', '/T', '/PID', parts[1].strip()],
                            capture_output=True, timeout=5, creationflags=CF)
        except Exception:
            pass

    #: Ngân sách lời gọi HỎI TRẠNG THÁI cho CẢ MÁY, request/giây.
    #:
    #: Hạn mức tài khoản là 1.000 request/phút ≈ 16,6/giây cho MỌI lời gọi. Dành
    #: 10 cho hỏi thăm, còn lại cho `create`, upload ảnh tham chiếu, và mấy lời
    #: gọi trạng thái lẻ. Con số này chia đều cho các tiến trình mã.
    NGAN_SACH_HOI_CA_MAY = 10.0

    def _chia_ngan_sach_hoi_tham(self):
        """Chia ngân sách hỏi trạng thái cho từng tiến trình mã, qua biến môi trường.

        ═══ VÌ SAO PHẢI CHIA, VÀ VÌ SAO NÓ QUYẾT ĐỊNH THÔNG LƯỢNG ═══

        Đo ngày 12/08/2026: đẩy 300 job ảnh chỉ bằng `POST`, máy chủ dựng **134
        job đồng thời**, rút **222 ảnh/phút**. Cùng ngày, đo qua `create_and_wait`
        lại ra "nhà máy bão hoà ở 40 chỗ". Khác biệt duy nhất: cách sau **hỏi thăm
        từng job**, và 100 job × hỏi mỗi 1–5 giây là vài nghìn request/phút trên
        hạn mức 1.000. Tool tự đâm vào trần của chính nó.

        Nên trần thật của dây chuyền không phải sức chứa nhà máy, mà là ngân sách
        lời gọi. `shopapi_common.NhipHoiTham` giãn nhịp hỏi theo số job đang bay
        để giữ trong ngân sách; hàm này nói cho mỗi tiến trình biết phần của nó.

        Truyền qua `os.environ` vì worker là TIẾN TRÌNH CON — nó thừa kế môi
        trường lúc được tạo, và `Popen` ở đây không truyền `env=` nên đặt vào
        `os.environ` là tới nơi.
        """
        try:
            so_ma = max(1, int(self.config_data.get("shopapi_ma_song_song",
                                                    SHOPAPI_MA_SONG_SONG_MAC_DINH) or 1))
        except (TypeError, ValueError):
            so_ma = SHOPAPI_MA_SONG_SONG_MAC_DINH
        phan = max(0.3, self.NGAN_SACH_HOI_CA_MAY / so_ma)
        os.environ["SHOPAPI_NGAN_SACH_HOI"] = "{0:.2f}".format(phan)

    def _load_config(self):
        try:
            import yaml
            p = VE3_DIR / "config" / "settings.yaml"
            if p.exists():
                with open(p,"r",encoding="utf-8") as f: self.config_data = yaml.safe_load(f) or {}
        except: self.config_data = {}
        # ===== MAC DINH MOI: di qua API shopapi.vn ==============================
        # Chi dat khi cau hinh CHUA CO Y KIEN. Nguoi dung cu da chon backend khac thi
        # GIU NGUYEN - doi len backend cua ho la doi cho tien di duong khac ma khong hoi.
        # Phai kiem CA generation_backend LAN generation_mode: may cu chi co
        # generation_mode, dat generation_backend='shopapi' vao do la de len y cu.
        if not self.config_data.get("generation_backend") and not self.config_data.get("generation_mode"):
            self.config_data["generation_backend"] = "shopapi"
            self.config_data["generation_mode"] = "shopapi"
        if "veo3top_image_mode" not in self.config_data:
            self.config_data["veo3top_image_mode"] = "shopapi"
        # ═══ TRẦN SONG SONG CỦA CHẾ ĐỘ API — PHẢI CÓ MẶC ĐỊNH TRONG CODE ═══
        #
        # `tools/ve3/config/settings.yaml` nằm trong `updater.PROTECTED_PATHS` và
        # KHÔNG được theo dõi trong git. Nghĩa là máy khác cập nhật sẽ không bao
        # giờ nhận ba con số này qua file cấu hình — chúng phải sống trong code.
        #
        # Thiếu mặc định thì worker rơi về TRẦN CỨNG của loại job: ảnh 384,
        # video 64. Nhân với 8 mã là **3.072 chỗ ảnh** đặt lên nhà máy — đúng
        # con số đã giết nhà máy ngày 12/08/2026 (nó khai 3.072 luồng rồi tiến
        # trình biến mất, 9 lần trong ngày).
        #
        # Số dưới đây là số ĐÃ CHẠY THẬT: 8 mã × 40 ảnh = 320, × 16 video = 128.
        # Giờ đỉnh 13/08/2026 ra 396 ảnh + 483 video = 879 sản phẩm. Máy chủ đo
        # được dựng 134 job đồng thời ở 222 ảnh/phút, nên 320 còn dư đầu cho
        # AIMD dò lên/xuống mà không chạm trần nhà máy.
        self.config_data.setdefault("max_concurrent", 40)              # ảnh / mã
        self.config_data.setdefault("shopapi_video_concurrency", 16)   # video / mã
        self.config_data.setdefault("shopapi_ma_song_song", SHOPAPI_MA_SONG_SONG_MAC_DINH)
        self._chia_ngan_sach_hoi_tham()
        self.config_data.setdefault("music_workspace_mode_enabled", True)
        self.config_data.setdefault("image_hide_chrome", True)   # mặc định ẩn chrome tạo ảnh
        self.config_data.setdefault("image_pool_accounts", 24)   # slot ảnh (account song song, cookie-based nhẹ)
        self.config_data.setdefault("image_swap_giveup", 2)      # swap mấy lượt thì cách ly account đốt
        self.config_data.setdefault("max_concurrent_codes", 0)   # số mã (video) chạy song song; 0 = không giới hạn
        self.config_data.setdefault("pool_isolation_hours", 6)   # cách ly account 429 (hết quota ngày) nghỉ N giờ - ảnh+video
        self.config_data.setdefault("max_concurrent_image_codes", 0)   # trạm ẢNH: 0 = TỰ TÍNH theo nhân sự; >0 = cố định
        self.config_data.setdefault("max_concurrent_video_codes", 0)   # trạm VIDEO: 0 = TỰ TÍNH theo Ultra; >0 = cố định
        self.config_data.setdefault("image_token_chromes", 6)    # chrome-trắng đẻ token ảnh (CPU là trần)
        self.config_data.setdefault("image_token_recycle", 10)   # đẻ N token/chrome rồi làm mới
        self.config_data.setdefault("video_token_chromes", 3)    # chrome đẻ token video (async cần ít)
        self.config_data.setdefault("video_workers_per_account", 7)  # luồng submit/account ultra
        self._chuyen_may_cu_sang_api()
        # PHẢI chạy SAU phép chuyển: máy chưa sang API thì trần song song của
        # chế độ API chưa có nghĩa gì, và nâng trước là ghi vào cấu hình một
        # con số cho đường mà máy đó chưa đi.
        self._nang_tran_song_song_may_cu()
        self._bao_duong_di()

    def _bao_duong_di(self):
        """Khai to ngay lúc mở: ảnh đi đường nào, video đi đường nào, có khoá chưa.

        ⚠ ĐỪNG BỎ DÒNG NÀY. Ngày 14/08/2026 một máy thứ hai cập nhật lên đúng
        bản mới, `_sdk/` đã về, code shopapi đã có mặt — mà vẫn chạy pool Chrome
        suốt đêm. Không ai biết, vì `settings.yaml` nằm trong
        `updater.PROTECTED_PATHS` **và** bị gitignore (nó giữ gmail|mật khẩu|totp
        của cả kho, nên bảo vệ là đúng) — cập nhật code KHÔNG BAO GIỜ đổi được
        chế độ. Máy đó vẫn để `generation_backend: veo3top_b_pool` từ trước.

        Cái ác không phải là nó chạy pool — cấu hình bảo vậy thì chạy vậy là
        đúng. Cái ác là **không có một dòng nào nói nó đang chạy pool**. Phải
        đọc ngược log đến chữ `PHASE 4: Tao Video tu anh` mới suy ra được. Một
        dòng ở đây trả lời xong câu đó trong một giây.
        """
        cfg = self.config_data
        anh = (cfg.get("veo3top_image_mode") or "").strip().lower() or "(chua dat)"
        vid = (cfg.get("generation_backend") or cfg.get("generation_mode") or "").strip().lower() or "(chua dat)"
        if cau_hinh_toan_api(cfg):
            if _co_khoa_shopapi():
                dong = "[ĐƯỜNG ĐI] ảnh=API shopapi · video=API shopapi · khoá=CÓ → chạy qua api.shopapi.vn"
            else:
                dong = ("[ĐƯỜNG ĐI] ảnh=API shopapi · video=API shopapi · khoá=THIẾU → "
                        "KHÔNG GỬI ĐƯỢC JOB NÀO. Settings → 'Khoá API shopapi' → dán khoá → Lưu khoá.")
        else:
            dong = ("[ĐƯỜNG ĐI] ảnh={0} · video={1} → chạy đường Chrome/pool cũ, KHÔNG dùng API shopapi. "
                    "Muốn đi API: Settings → hai ô backend → chọn 'API shopapi' cho CẢ HAI → Lưu.".format(anh, vid))
        # `_load_config()` chạy TRƯỚC `_build()` — chưa có ô log nào để ghi vào.
        # Xếp hàng, `_boot` sẽ nhả ra khi giao diện đã dựng xong.
        self._duong_di_cho_bao = getattr(self, "_duong_di_cho_bao", [])
        self._duong_di_cho_bao.append(dong)
        ghi_log_file(dong, "INFO")

    def _chuyen_may_cu_sang_api(self):
        """Máy cũ vẫn để backend Chrome/pool → chuyển sang API shopapi MỘT LẦN.

        ═══ VÌ SAO PHẢI CÓ, DÙ NÓ GHI ĐÈ LỰA CHỌN CŨ ═══

        `tools/ve3/config/settings.yaml` nằm trong `updater.PROTECTED_PATHS`
        **và** `GIT_PROTECTED_FILES` **và** `.gitignore`. Ba lớp, và đều đúng —
        file đó giữ gmail|mật khẩu|totp của cả kho account. Hệ quả: **không có
        đường nào để một bản cập nhật đổi được chế độ chạy.** Code mới về đủ,
        `_sdk/` về đủ, số phiên bản nhảy lên 527, mà máy vẫn chạy y như cũ.

        Đã xảy ra thật đêm 14/08/2026. Máy thứ hai cập nhật xong chạy suốt đêm
        `PHASE 4: Tao Video tu anh` qua pool Chrome, `sv5/...`, `media_id`, rồi
        20 cảnh `FAIL (129.6s) [error: retry lt sau]` một lượt — trong khi cả
        đợt việc chuyển sang shopapi đã nằm sẵn trên đĩa nó.

        Bảo người dùng tự vào Settings bật hai ô là chữa được đúng một máy. Còn
        20 máy nữa thì mỗi máy một lần đi bảo, và máy nào quên thì đốt account
        Chrome cả đêm mà không ai biết. Nên việc chuyển phải nằm trong code.

        ═══ BỐN CÁI PHANH ═══

        1. **Chạy đúng một lần cho mỗi PHIÊN BẢN chuyển.** Cấu hình ghi
           `da_chuyen_sang_shopapi = <số phiên bản>`, so với
           :data:`CHUYEN_API_PHIEN`. Ai cố ý quay về pool sau đó sẽ KHÔNG bị ép
           lại — nhưng một bản vá SỬA CHÍNH PHẦN CHUYỂN thì vẫn chạy lại được.

           Bản đầu ghi cờ `True`, và cái cờ đó tự khoá lấy bản vá của chính nó:
           528 chuyển hụt (được video, sót ảnh vì lẫn hai bộ từ vựng) rồi đóng
           cờ; 529 sửa đúng logic nhưng vừa vào hàm đã gặp cờ và quay ra. Máy
           đó nằm chết ở `ảnh=pool · video=shopapi` — nửa vời, và nửa đó đủ để
           `cau_hinh_toan_api` trả False nên vẫn chạy Chrome cho cả hai khâu.
           Cờ một-bit không phân biệt nổi "đã chuyển bằng bản hỏng" với "đã
           chuyển bằng bản đúng"; con số thì phân biệt được.
        2. **Chỉ chuyển từ backend cũ đã biết** — :data:`BACKEND_VIDEO_CU` cho
           video, :data:`BACKEND_ANH_CU` cho ảnh. HAI danh sách, vì hai bộ từ
           vựng khác nhau: video là `veo3top_b_pool`, ảnh là `pool`. Bản đầu
           dùng chung một danh sách gõ tay (toàn giá trị video) nên ảnh không
           bao giờ được chuyển, `cau_hinh_toan_api` đòi cả hai nên vẫn trả
           False, và máy 528 vẫn hiện `TRẠM ẢNH`/`TRẠM VIDEO` y như chưa sửa.
           Giá trị lạ thì để yên — không đoán hộ.
        3. **Chép lưu trước khi ghi**, kèm dấu thời gian, cạnh file gốc.
        4. **Nói to.** In ra log và ghi vào `logs/`, kèm đường dẫn bản lưu và
           cách quay về.
        """
        cfg = self.config_data
        if _phien_da_chuyen(cfg) >= CHUYEN_API_PHIEN:
            return
        vid = (cfg.get("generation_backend") or cfg.get("generation_mode") or "").strip().lower()
        anh = (cfg.get("veo3top_image_mode") or "").strip().lower()
        doi_vid = vid in BACKEND_VIDEO_CU
        doi_anh = anh in BACKEND_ANH_CU
        if not (doi_vid or doi_anh):
            cfg["da_chuyen_sang_shopapi"] = CHUYEN_API_PHIEN   # đã ở API sẵn — đóng cửa
            return

        luu = ""
        try:
            goc = VE3_DIR / "config" / "settings.yaml"
            if goc.exists():
                ban = goc.with_name("settings.yaml.truoc-api-{0}".format(
                    _time.strftime("%Y%m%d_%H%M%S")))
                ban.write_bytes(goc.read_bytes())
                luu = str(ban)
        except Exception as e:
            ghi_log_file("Khong chep luu duoc settings.yaml truoc khi chuyen: {0}".format(e), "WARN")

        cu = "ảnh={0} · video={1}".format(anh or "(chua dat)", vid or "(chua dat)")
        if doi_vid:
            cfg["generation_backend"] = "shopapi"
            cfg["generation_mode"] = "shopapi"
        if doi_anh:
            cfg["veo3top_image_mode"] = "shopapi"
        cfg["da_chuyen_sang_shopapi"] = CHUYEN_API_PHIEN
        try:
            self._save_config()
        except Exception as e:
            ghi_log_file("Khong ghi duoc settings.yaml sau khi chuyen: {0}".format(e), "ERROR")

        self._duong_di_cho_bao = getattr(self, "_duong_di_cho_bao", [])
        for d in ("[CHUYỂN ĐƯỜNG] Máy này đang để backend cũ ({0}) → đã chuyển sang API shopapi "
                  "cho cả ảnh lẫn video. Chỉ chuyển MỘT LẦN.".format(cu),
                  "[CHUYỂN ĐƯỜNG] Bản lưu cấu hình cũ: {0}".format(luu or "(không chép được)"),
                  "[CHUYỂN ĐƯỜNG] Muốn quay lại pool Chrome: Settings → hai ô backend → chọn lại → Lưu."):
            self._duong_di_cho_bao.append(d)
            ghi_log_file(d, "WARN")

    def _nang_tran_song_song_may_cu(self):
        """Máy cũ còn giữ trần song song thời Chrome → nâng lên sàn API MỘT LẦN.

        ═══ VÌ SAO PHẢI NẰM TRONG CODE ═══

        Giống hệt `_chuyen_may_cu_sang_api`: ba con số này sống trong
        `settings.yaml`, mà file đó bị chặn bởi `PROTECTED_PATHS` **và**
        `GIT_PROTECTED_FILES` **và** `.gitignore`. Không có bản cập nhật nào
        chạm được vào chúng. `setdefault` trong `_load_config` cũng không cứu
        được: nó chỉ điền khi khoá VẮNG MẶT, còn máy cũ thì khoá có mặt với
        giá trị nhỏ — nên nó lặng lẽ không làm gì.

        Hậu quả đo được 14/08/2026: khách giữ **2 chỗ ảnh** trong khi máy chủ
        mời ~979, ba worker `idle`, và chủ dự án thấy "1 phút 1 ảnh". Không có
        gì trong log báo điều đó — mọi job đều xong, mọi ảnh đều ra.

        ═══ BỐN CÁI PHANH (cùng khuôn với phép chuyển backend) ═══

        1. **Một lần cho mỗi PHIÊN BẢN**, so `da_nang_tran_song_song` với
           :data:`NANG_TRAN_PHIEN`. Ai cố ý hạ trần sau đó sẽ không bị ép lại.
        2. **CHỈ NÂNG, không bao giờ hạ.** Số đã ≥ sàn thì để yên — người vận
           hành có thể đã đo và chọn cao hơn, và ghi đè xuống là phá việc của họ.
        3. **`0` là lựa chọn CÓ Ý, không phải số cũ.** `0` nghĩa "theo trần động
           của máy chủ" — đúng cái chủ dự án yêu cầu ("server xử lý được bao
           nhiêu thì cứ dùng bấy nhiêu, đừng có làm cứng"). Nâng nó thành 40 là
           biến một trần ĐỘNG thành trần CỨNG, tức đi lùi.
        4. **Chép lưu trước khi ghi, và nói to** — kèm đường dẫn bản lưu.
        """
        cfg = self.config_data
        try:
            da = int(cfg.get("da_nang_tran_song_song") or 0)
        except (TypeError, ValueError):
            da = 0
        if da >= NANG_TRAN_PHIEN:
            return

        def _nang(khoa, san):
            """Trả `(cũ, mới)` nếu cần nâng, `None` nếu để yên."""
            if khoa not in cfg:
                return None                      # vắng mặt → `setdefault` lo
            try:
                v = int(cfg.get(khoa) or 0)
            except (TypeError, ValueError):
                return None                      # giá trị lạ → không đoán hộ
            if v == 0 or v >= san:
                return None                      # phanh 2 và 3
            return (v, san)

        doi = {}
        for khoa, san in (("max_concurrent", SAN_ANH_MOI_MA),
                          ("shopapi_video_concurrency", SAN_VIDEO_MOI_MA),
                          ("shopapi_ma_song_song", SAN_MA_SONG_SONG)):
            kq = _nang(khoa, san)
            if kq is not None:
                doi[khoa] = kq

        if not doi:
            cfg["da_nang_tran_song_song"] = NANG_TRAN_PHIEN   # đã đủ trần — đóng cửa
            return

        luu = ""
        try:
            goc = VE3_DIR / "config" / "settings.yaml"
            if goc.exists():
                ban = goc.with_name("settings.yaml.truoc-nang-tran-{0}".format(
                    _time.strftime("%Y%m%d_%H%M%S")))
                ban.write_bytes(goc.read_bytes())
                luu = str(ban)
        except Exception as e:
            ghi_log_file("Khong chep luu duoc settings.yaml truoc khi nang tran: {0}".format(e),
                         "WARN")

        for khoa, (_cu, moi) in doi.items():
            cfg[khoa] = moi
        cfg["da_nang_tran_song_song"] = NANG_TRAN_PHIEN
        try:
            self._save_config()
        except Exception as e:
            ghi_log_file("Khong ghi duoc settings.yaml sau khi nang tran: {0}".format(e), "ERROR")

        mo_ta = " · ".join("{0} {1}→{2}".format(k, c, m) for k, (c, m) in sorted(doi.items()))
        self._duong_di_cho_bao = getattr(self, "_duong_di_cho_bao", [])
        for d in ("[NÂNG TRẦN] Máy này còn giữ trần song song thời Chrome ({0}). Đo ngày "
                  "14/08/2026: khách chỉ giữ 2 chỗ ảnh trong khi máy chủ mời ~979.".format(mo_ta),
                  "[NÂNG TRẦN] Bản lưu cấu hình cũ: {0}".format(luu or "(không chép được)"),
                  "[NÂNG TRẦN] Muốn đổi lại: Settings → ba ô song song → chọn lại → Lưu. "
                  "Đặt 0 nghĩa là đi theo trần động của máy chủ."):
            self._duong_di_cho_bao.append(d)
            ghi_log_file(d, "WARN")

    def _save_config(self):
        try:
            import yaml
            with open(VE3_DIR/"config"/"settings.yaml","w",encoding="utf-8") as f:
                yaml.dump(self.config_data, f, default_flow_style=False, allow_unicode=True)
        except: pass
        # Sync API keys to srt-to-excel (headless Excel process reads from there)
        try:
            import yaml
            srt_cfg_path = VE3_DIR.parent / "srt-to-excel" / "config" / "settings.yaml"
            if srt_cfg_path.exists():
                srt_cfg = yaml.safe_load(srt_cfg_path.read_text(encoding="utf-8")) or {}
            else:
                srt_cfg = {}
            for key in ("deepseek_api_key", "deepseek_api_keys", "deepseek_model",
                        "deepseek_thinking_type", "vov_direct_base_url", "vov_direct_api_key",
                        "vov_direct_model", "vov_direct_model_chain",
                        "claude_pool_base_url", "claude_pool_api_key", "claude_pool_model",
                        "claude_pool_model_chain"):
                if key in self.config_data:
                    srt_cfg[key] = self.config_data[key]
            srt_cfg_path.parent.mkdir(parents=True, exist_ok=True)
            with open(srt_cfg_path, "w", encoding="utf-8") as f:
                yaml.dump(srt_cfg, f, default_flow_style=False, allow_unicode=True)
        except: pass
        # Sync API keys to server/config/settings.yaml
        try:
            import yaml
            srv_cfg_path = VE3_DIR.parent.parent / "server" / "config" / "settings.yaml"
            if srv_cfg_path.exists():
                srv_cfg = yaml.safe_load(srv_cfg_path.read_text(encoding="utf-8")) or {}
                for key in ("deepseek_api_key", "deepseek_api_keys", "deepseek_model",
                            "vov_direct_base_url", "vov_direct_api_key", "vov_direct_model"):
                    if key in self.config_data:
                        srv_cfg[key] = self.config_data[key]
                with open(srv_cfg_path, "w", encoding="utf-8") as f:
                    yaml.dump(srv_cfg, f, default_flow_style=False, allow_unicode=True)
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

    def _excel_di_shopapi(self, cfg):
        """Bước Excel có đi thẳng LLM của shopapi không?

        Khi đúng, `excel_ai_provider` (DeepSeek / VOV / Claude Pool) KHÔNG được
        dùng tới một lần nào: `ClaudeCliEngine` tự gọi `api.shopapi.vn` bằng
        khoá `sk_live_`. Đòi khoá của mấy nguồn kia lúc này là chặn oan.
        """
        if str(cfg.get("excel_engine", "") or "").strip().lower() not in ("claude_cli", "claude", "cli"):
            return False
        if str(cfg.get("claude_cli_backend", "") or "").strip().lower() not in ("api_shop", "api_shop_cli"):
            return False
        if str(cfg.get("shopapi_api_key", "") or "").strip():
            return True
        try:
            import sys as _sys
            _engine = str(SUITE_ROOT / "veo3top_engine")
            if _engine not in _sys.path:
                _sys.path.insert(0, _engine)
            import shopapi_common as _sc
            return bool((_sc.doc_khoa() or ("", ""))[0])
        except Exception:
            return False

    def _validate_excel_ai_config(self, cfg=None):
        cfg = cfg or self.config_data
        # ⚠ Cổng này ra đời TRƯỚC nhánh shopapi nên nó chỉ biết ba nguồn cũ, và
        # nhánh rơi cuối cùng là "Can DeepSeek API key". Excel đi shopapi mà vẫn
        # bị đòi khoá DeepSeek là chặn oan — càng oan sau khi đã dọn sạch khoá
        # DeepSeek khỏi kho mã.
        #
        # `self.config_data` KHÔNG chứa `excel_engine`/`claude_cli_backend` (hai
        # khoá đó chỉ sinh ra trong `_build_excel_runtime_config`), nên phải hỏi
        # đúng cấu hình runtime thì mới thấy được sự thật.
        if cfg is self.config_data:
            try:
                cfg = self._build_excel_runtime_config()
            except Exception:
                pass
        if self._excel_di_shopapi(cfg):
            return True, ""
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
        """Load sheet NGUON from Google Sheets with retry (network hay loi)."""
        import socket
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
            # Retry 5 lan (network hay timeout/loi ket noi — Google API hay loi)
            max_attempts = 5
            for attempt in range(1, max_attempts + 1):
                try:
                    gc = gspread.authorize(creds)
                    ws = gc.open(spreadsheet_name).worksheet("NGUON")
                    data = ws.get_all_values()
                    self._log(f"[TOPIC] Loaded sheet NGUON: {len(data)} rows")
                    return data
                except Exception as e:
                    if attempt < max_attempts:
                        wait = min(5 * attempt, 30)
                        self._log(f"[TOPIC] Sheet NGUON load failed ({attempt}/{max_attempts}): {e} — retry in {wait}s", "WARN")
                        _time.sleep(wait)
                    else:
                        self._log(f"[TOPIC] Sheet NGUON load FAILED after {max_attempts} attempts: {e}", "ERROR")
            return []
        except Exception as e:
            self._log(f"[TOPIC] Cannot load sheet NGUON: {e}", "WARN")
            return []
        finally:
            socket.getaddrinfo = _orig_getaddrinfo

    def _lookup_topic_from_nguon_sheet(self, code):
        """Lookup topic from Google Sheet NGUON (Col G=code, Col S=topic). 15s timeout."""
        import concurrent.futures

        def _do():
            if not self.__class__._nguon_sheet_cache:
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
            return executor.submit(_do).result(timeout=120)
        except concurrent.futures.TimeoutError:
            self._log(f"[TOPIC] Sheet NGUON timeout (120s) for {code}", "WARN")
            return ""
        except Exception:
            return ""
        finally:
            executor.shutdown(wait=False)

    def _lookup_reference_channel_from_nguon_sheet(self, code):
        """Lookup reference channel from NGUON (Col G=code, Col L=channel) with retries."""
        import concurrent.futures

        def _do():
            if not self.__class__._nguon_sheet_cache:
                self.__class__._nguon_sheet_cache = self._load_nguon_sheet()
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
            ref_base = SUITE_ROOT / "tools" / "srt-to-excel" / "reference_characters"

            def valid_channel(text):
                text = str(text or "").strip()
                if not text:
                    return ""
                for topic_dir in ("psychology", "finance", "success"):
                    root = ref_base / topic_dir
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
            return executor.submit(_do).result(timeout=120)
        except concurrent.futures.TimeoutError:
            self._log(f"[TOPIC] Sheet NGUON timeout (120s) for reference_channel {code}", "WARN")
            return ""
        except Exception:
            return ""
        finally:
            executor.shutdown(wait=False)

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
        # ⚠ "claude_cli" là tên LỊCH SỬ của engine một-lần-gọi, KHÔNG có nghĩa là
        # phải chạy claude.exe. Đường vận chuyển do `claude_cli_backend` quyết
        # định, và mặc định của bản này là `api_shop` (HTTP tới api.shopapi.vn).
        # Nên cặp mặc định dưới đây = "Excel đi API shopapi, không mở claude.exe".
        cfg.setdefault("excel_engine", "claude_cli")
        cfg.setdefault("claude_cli_model", "claude-sonnet-4-6")
        cfg.setdefault("claude_cli_min_scene", 3)
        cfg.setdefault("claude_cli_max_scene", 8)
        cfg.setdefault("claude_cli_review", True)
        cfg.setdefault("claude_cli_timeout_seconds", 1800)
        # Chunks of one long video run in parallel (each its own claude.exe) with
        # per-chunk retry, so a long video finishes ~3x faster without orphaning.
        cfg.setdefault("claude_cli_chunk_parallel", 3)
        cfg.setdefault("claude_cli_chunk_retries", 2)
        # Đường vận chuyển của engine Excel. MẶC ĐỊNH `api_shop` = HTTP thẳng tới
        # api.shopapi.vn bằng CÙNG khoá `sk_live_` đang trả tiền ảnh/video.
        # Không mở claude.exe, nên cũng không đụng OAuth của extension VS Code.
        cfg.setdefault("claude_cli_backend", "api_shop")
        cfg.setdefault("claude_cli_api_model", "claude-sonnet-4-6")
        cfg.setdefault("claude_cli_max_parallel", 5)
        # Nhánh DeepSeek (`api_ds` / `api_ds_cli`) — KHÔNG còn dùng, giữ lại đường
        # chạy phòng khi cần, nhưng KHÔNG kèm khoá.
        #
        # ⚠ ĐỪNG GÕ KHOÁ THẬT VÀO ĐÂY NỮA. Chỗ này từng chứa một khoá DeepSeek
        # sống, và `cfg` còn được ghi ra `.excel_runtime_config.yaml` trong từng
        # thư mục project — nên một khoá gõ ở đây tự nhân bản ra mọi project trên
        # đĩa, ngoài chuyện nằm sẵn trong kho mã. Ai cần DeepSeek thì tự điền vào
        # settings.yaml của máy mình.
        cfg.setdefault("claude_cli_ds_base_url", "https://api.deepseek.com/v1")
        cfg.setdefault("claude_cli_ds_api_key", "")
        cfg.setdefault("claude_cli_ds_model", "deepseek-v4-pro")
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

        # Tai Excel co san vao PROJECTS -> queue tu tao anh + video
        ctk.CTkButton(sb, text="Tải Excel", width=SW-16, height=28,
                      fg_color=RN, hover_color="#1565C0", text_color="#FFF",
                      font=("",10,"bold"), corner_radius=6,
                      command=self._import_excel).grid(row=8, column=0, padx=8, pady=(0,4))

        # Version + Update button
        self._version_label = ctk.CTkLabel(sb, text=f"v{self._get_local_version()}", font=("",9), text_color="#555")
        self._version_label.grid(row=9, column=0, padx=8, pady=(4,0))

        self._update_btn = ctk.CTkButton(sb, text="Update", width=SW-16, height=26,
                                         fg_color=SB2, hover_color="#2E7D32", text_color="#888",
                                         font=("",10), corner_radius=6,
                                         command=self._on_check_update)
        self._update_btn.grid(row=10, column=0, padx=8, pady=(2,4))

        # Settings button at bottom
        cfg_btn = ctk.CTkButton(sb, text="Settings", width=SW-16, height=28,
                                fg_color="transparent", hover_color=SB3, text_color="#777",
                                font=("",11), corner_radius=6, anchor="w",
                                command=lambda: self.show("cfg"))
        cfg_btn.grid(row=11, column=0, padx=8, pady=(0,14))
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
                ver = f"1.0.{result.stdout.strip()}"
                try:
                    (SUITE_ROOT / "VERSION").write_text(ver, encoding="utf-8")
                except Exception:
                    pass
                return ver
        except Exception:
            pass
        try:
            ver = (SUITE_ROOT / "VERSION").read_text(encoding="utf-8").strip()
            if ver:
                return ver
        except Exception:
            pass
        return "?.?.?"

    def _on_check_update(self):
        # Cancel auto-start queue countdown
        self._auto_start_countdown = 0
        self._update_btn.configure(text="Checking...", state="disabled", text_color="#FFA500")
        import threading
        threading.Thread(target=self._check_and_update_thread, daemon=True).start()

    def _check_and_update_thread(self):
        """Check + update in one click."""
        try:
            import sys
            sys.path.insert(0, str(SUITE_ROOT))
            from updater import check_update, download_and_apply
            info = check_update()
            if info.get("error"):
                err = info["error"][:30]
                self.after(0, lambda: self._update_btn.configure(text=f"Lỗi: {err}", state="normal", text_color="#FF4444"))
                self.after(5000, lambda: self._update_btn.configure(text="Update", text_color="#888"))
                return
            if not info["available"]:
                self.after(0, lambda: self._update_btn.configure(text="Mới nhất ✓", state="normal", text_color="#43e97b"))
                self.after(5000, lambda: self._update_btn.configure(text="Update", text_color="#888", fg_color=SB2))
                return
            # Has update → download immediately
            remote = info["remote"]
            self.after(0, lambda: self._version_label.configure(
                text=f"v{info['local']}  →  v{remote}", text_color="#FFA500"))
            self.after(0, lambda: self._update_btn.configure(text="Dang tai...", text_color="#FFA500"))
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
                self.after(5000, lambda: self._update_btn.configure(text="Update", text_color="#888", fg_color=SB2))
        except Exception as e:
            err_msg = str(e)[:30]
            self.after(0, lambda: self._update_btn.configure(text=f"Lỗi: {err_msg}", state="normal", text_color="#FF4444"))
            self.after(3000, lambda: self._update_btn.configure(text="Update", text_color="#888", fg_color=SB2))

    def _boot(self):
        # Nhả hàng đợi của `_bao_duong_di` / `_chuyen_may_cu_sang_api`: hai hàm
        # đó chạy trong `_load_config`, tức là TRƯỚC `_build()`, lúc chưa có ô
        # log nào tồn tại để ghi vào.
        for _d in getattr(self, "_duong_di_cho_bao", []):
            self._log(_d, "WARN" if "CHUYỂN ĐƯỜNG" in _d or "THIẾU" in _d or "pool cũ" in _d else "INFO", "ve3")
        self._duong_di_cho_bao = []
        cleared = self._clear_all_queue_markers()
        self._refresh_manual_done_codes()
        if cleared:
            self._log(f"[QUEUE] Da don {cleared} lock cu khi khoi dong.", "WARN", "ve3")
        # Fetch server status in background to avoid blocking GUI
        threading.Thread(target=self._refresh_server_status_sync, daemon=True).start()
        self.pages["home"].load_server_config()
        self.pages["cfg"]._render()
        self._refresh_project_views()
        # Force immediate refresh for projects list on startup
        self.after(500, self._refresh_project_views)
        self.after(3000, self._process_monitor_tick)
        # Auto-start queue after 60s
        self._auto_start_countdown = 30
        self._auto_restart_at = 0
        self._pending_restart_id = None
        self._log("[QUEUE] Tu dong chay sau 30s... (bam STOP hoac Update de huy)", "INFO")
        self.after(1000, self._auto_start_tick)

    def _auto_start_tick(self):
        if not hasattr(self, '_auto_start_countdown') or self._auto_start_countdown <= 0:
            return
        if self.queue_running:
            return
        self._auto_start_countdown -= 1
        if self._auto_start_countdown <= 0:
            self._log("[QUEUE] Tu dong bat dau!", "OK")
            self.toggle_queue_worker()
            # Schedule auto-restart after 12h
            self._schedule_auto_restart()
        else:
            if self._auto_start_countdown % 10 == 0:
                self._log(f"[QUEUE] Tu dong chay sau {self._auto_start_countdown}s...", "INFO")
            self.after(1000, self._auto_start_tick)

    def _schedule_auto_restart(self):
        """Schedule full GUI restart after 12h to reset all errors."""
        hours = 12
        self._auto_restart_at = _time.time() + hours * 3600
        if self._pending_restart_id:
            self.after_cancel(self._pending_restart_id)
        self._pending_restart_id = self.after(hours * 3600 * 1000, self._do_auto_restart)
        self._log(f"[QUEUE] Auto-restart sau {hours}h", "INFO")

    def _do_auto_restart(self):
        """Kill everything (like closing GUI) → restart GUI process."""
        self._pending_restart_id = None
        self._auto_restart_at = 0
        self._log("=== AUTO-RESTART 12h — kill all + restart GUI ===", "WARN")

        def _cleanup_and_restart():
            try:
                self.queue_stop_requested = True
                self.music_stop_requested = True
                if self.worker:
                    try:
                        self.worker.stop()
                    except Exception:
                        pass
                with self.queue_lock:
                    all_procs = list(self.queue_ve3_procs.values()) + list(self.queue_music_procs.values())
                for proc in all_procs:
                    if proc and proc.poll() is None:
                        self._kill_pid_tree(proc.pid)
                self._kill_own_child_processes()
                self._kill_extension_instances()
                self._kill_veo3top_chromes()
            except Exception as e:
                self._log(f"[RESTART] cleanup error: {e}", "ERROR")
            _time.sleep(15)
            self._exec_restart()

        threading.Thread(target=_cleanup_and_restart, daemon=True).start()

    def _exec_restart(self):
        """Start new GUI process then exit. Safe on Windows (no process chaining)."""
        import sys, subprocess
        try:
            subprocess.Popen([sys.executable] + sys.argv,
                             creationflags=subprocess.CREATE_NEW_PROCESS_GROUP)
        except Exception as e:
            self._log(f"[RESTART] failed: {e}", "ERROR")
        os._exit(0)

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
            old_pm_timer = getattr(self, "_process_monitor_timer_id", None)
            if old_pm_timer is not None:
                self.after_cancel(old_pm_timer)
            self._process_monitor_timer_id = self.after(
                getattr(self, "_process_monitor_interval_ms", 60000), self._process_monitor_tick)

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
        # reap chrome token mồ côi (worker cha chết) — throttle 120s, chạy trong chính vòng monitor (elevated)
        try:
            _now = _time.time()
            if _now - getattr(self, "_last_reap_ts", 0) >= 120:
                self._last_reap_ts = _now
                self._reap_orphan_chromes()
        except Exception:
            pass
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
        if getattr(self, "_closing", False):
            return
        with self._project_refresh_lock:
            if self._project_refresh_thread and self._project_refresh_thread.is_alive():
                self._project_refresh_pending = True
                return
            self._project_refresh_pending = False
            self._project_refresh_thread = threading.Thread(target=self._refresh_project_views_worker, daemon=True)
            self._project_refresh_thread.start()
        self._periodic_cleanup()
        # Cancel previous timer to prevent accumulation over hours of operation
        old_timer = getattr(self, "_project_refresh_timer_id", None)
        if old_timer is not None:
            self.after_cancel(old_timer)
        self._project_refresh_timer_id = self.after(60000, self._refresh_project_views)

    def _periodic_cleanup(self):
        """Prune dead processes, stale caches, and leaked references. Runs every 60s."""
        # 1. Prune child_procs (dead Popen objects)
        with self.child_proc_lock:
            before = len(self.child_procs)
            self.child_procs = [x for x in self.child_procs
                                if x.get("proc") and x["proc"].poll() is None]
            pruned = before - len(self.child_procs)

        # 2. Prune queue_ve3_procs (finished VE3 subprocesses)
        with self.queue_lock:
            dead_ve3 = [code for code, p in (self.queue_ve3_procs or {}).items()
                        if not p or p.poll() is not None]
            for code in dead_ve3:
                self.queue_ve3_procs.pop(code, None)
            dead_music = [code for code, p in (self.queue_music_procs or {}).items()
                          if not p or p.poll() is not None]
            for code in dead_music:
                self.queue_music_procs.pop(code, None)
            # Prune VE3 task threads
            dead_tasks = [code for code, t in (self.queue_ve3_tasks or {}).items()
                          if not t or not t.is_alive()]
            for code in dead_tasks:
                self.queue_ve3_tasks.pop(code, None)
                self.queue_active_ve3.discard(code)
                self.queue_ve3_stage.pop(code, None)   # dọn stage khi task chết (tránh stale chặn concurrency)

        # 3. Prune caches (keep max 200 entries, remove oldest)
        for cache_name in ('_project_state_cache', '_project_binding_cache', '_ve3_priority_cache', 'project_progress_cache'):
            cache = getattr(self, cache_name, None)
            if cache and len(cache) > 200:
                keys = sorted(cache.keys(), key=lambda k: cache[k].get("ts", 0) if isinstance(cache[k], dict) else 0)
                for k in keys[:len(cache) - 150]:
                    cache.pop(k, None)

        # 4. Prune log timestamp dicts
        now = _time.time()
        for ts_dict_name in ('source_wait_log_ts', 've3_skip_log_ts'):
            ts_dict = getattr(self, ts_dict_name, None)
            if ts_dict and len(ts_dict) > 100:
                stale = [k for k, v in ts_dict.items() if now - v > 300]
                for k in stale:
                    ts_dict.pop(k, None)

        # 5. Prune HomePage._ui_progress_cache (grows per unique project code)
        try:
            home = self.pages.get("home")
            if home and hasattr(home, "_ui_progress_cache"):
                upc = home._ui_progress_cache
                if len(upc) > 300:
                    for k in list(upc.keys())[:len(upc) - 200]:
                        upc.pop(k, None)
        except Exception:
            pass

        # 6. Prune _channel_cache (static, grows per unique code)
        if len(self._channel_cache) > 500:
            self._channel_cache.clear()

        # 7. Prune queue_pair_last_used (grows per pair usage, never cleared)
        plu = getattr(self, "queue_pair_last_used", None)
        if plu and len(plu) > 200:
            sorted_keys = sorted(plu, key=plu.get)
            for k in sorted_keys[:len(plu) - 100]:
                plu.pop(k, None)

        # 8. Prune _progress_slot_codes (HomePage, can grow with new projects)
        try:
            home = self.pages.get("home")
            if home and hasattr(home, "_progress_slot_codes"):
                psc = home._progress_slot_codes
                if len(psc) > 200:
                    home._progress_slot_codes = psc[-150:]
        except Exception:
            pass

        # 9. Prune stale queue_ve3_workers (threads that died without cleanup)
        workers = getattr(self, "queue_ve3_workers", None)
        if workers:
            stale_w = [k for k, t in workers.items()
                       if not t or not t.is_alive()]
            for k in stale_w:
                workers.pop(k, None)

    def _count_archived_today(self):
        """Count projects in old/ modified today. Called from background thread."""
        count = 0
        try:
            today_start = _time.mktime(_time.localtime()[:3] + (0,0,0,0,0,-1))
            if ARCHIVE_DIR.exists():
                for d in ARCHIVE_DIR.iterdir():
                    if d.is_dir():
                        try:
                            if d.stat().st_mtime >= today_start:
                                count += 1
                        except Exception:
                            pass
        except Exception:
            pass
        return count

    def _con_lai_va_ma_theo_pha(self):
        """`(còn ảnh, còn video, số mã ở pha ẢNH, số mã ở pha VIDEO)`.

        Bốn con số này là TRẠNG THÁI CỦA CHÍNH TA — không hỏi máy chủ câu nào.

        "Mã ở pha nào" là thứ giải thích được điều mà mọi chỉ số khác không nói
        nổi: ngày 11/08/2026 máy chủ cấp 676 chỗ ảnh, cấu hình xin 192, mà chỉ 24
        job chạy — đúng bằng trần của MỘT mã. Nhìn `/v1/me` mãi cũng không ra,
        vì máy chủ không biết ta có mấy mã. Đặt con số này cạnh ba con số kia thì
        câu trả lời hiện ra ngay: 1 mã × 24 = 24.

        Đếm theo ĐÚNG luật của worker: một mã còn thiếu ảnh thì nó ở pha ẢNH;
        ảnh xong hết mà còn thiếu video thì ở pha VIDEO (các pha chạy tuần tự).

        ⚠ NGUỒN "MÃ NÀO ĐANG CHẠY" LÀ `queue_ve3_procs`, KHÔNG PHẢI FILE LOCK.
        `_boot()` xoá sạch `.queue_*.lock` mỗi lần một bản GUI khởi động, nên chỉ
        cần mở thêm một cửa sổ VE3 thứ hai là bảng của bản đang chạy tụt hết về
        0 — đúng chuyện đã thấy khi chụp màn hình. Danh sách tiến trình con là
        thứ CHỈ bản này biết và không ai xoá hộ được.

        Chạy ở background thread — không đụng widget.
        """
        con_anh = con_vid = 0
        ma_anh = ma_vid = 0
        dang_chay = set()
        try:
            with self.queue_lock:
                for ma, proc in list((self.queue_ve3_procs or {}).items()):
                    if proc is not None and proc.poll() is None:
                        dang_chay.add(str(ma))
        except Exception:
            pass
        try:
            for pd in PROJECTS_DIR.iterdir():
                if not pd.is_dir():
                    continue
                tt = pd / ".progress_totals.json"
                if not tt.exists():
                    continue
                try:
                    t = json.loads(tt.read_text(encoding="utf-8"))
                except Exception:
                    continue
                tong_s = int(t.get("scene_total") or 0)
                tong_v = int(t.get("video_total") or 0)
                if tong_s <= 0 and tong_v <= 0:
                    continue
                # ⚠ ĐẾM Ở `img_backup` KHI CÓ. Sau khi finalize, ảnh đã thành
                # video bị XOÁ khỏi `img/`, nên đếm `img/` là đếm hụt và ô "CÒN
                # LẠI" phình lên. Đo ngày 11/08/2026: đếm `img/` ra 4.556 ảnh
                # còn thiếu, sự thật là 1.889 — sai 2,4 lần, và sai theo hướng
                # làm người vận hành tưởng còn cả núi việc chưa làm.
                # `_count_production_today` đã chọn đúng nguồn này từ trước.
                nguon_anh = (pd / "img_backup") if (pd / "img_backup").exists() else (pd / "img")
                co_a = len(list(nguon_anh.glob("*.png")) + list(nguon_anh.glob("*.jpg"))) \
                    if nguon_anh.exists() else 0
                co_v = len(list((pd / "vid").glob("*.mp4"))) if (pd / "vid").exists() else 0
                thieu_a = max(0, tong_s - min(co_a, tong_s))
                thieu_v = max(0, tong_v - min(co_v, tong_v))
                con_anh += thieu_a
                con_vid += thieu_v
                # Chỉ tính mã có worker ĐANG SỐNG của chính bản GUI này.
                if pd.name not in dang_chay:
                    continue
                if thieu_a > 0:
                    ma_anh += 1
                elif thieu_v > 0:
                    ma_vid += 1
        except Exception:
            pass
        return con_anh, con_vid, ma_anh, ma_vid

    def _count_production_today(self, tu_giay=None):
        """Do luong THUC TE: dem so ANH (.png/.jpg) va VIDEO (.mp4) co mtime tu `tu_giay`
        tro di, tren PROJECTS + old/ (dedup theo code de tranh dem trung).

        `tu_giay=None` -> tu 00:00 hom nay (hanh vi cu, dung cho o "HOM NAY").
        Truyen moc khac de do TOC DO: vi du `time.time()-3600` ra so anh/video lam
        duoc trong mot gio vua roi.

        VI SAO DEM FILE CHU KHONG DOC SO CUA MAY CHU: file tren dia la san pham
        CUOI CUNG da ve tay minh. May chu bao "job succeeded" ma file chua tai ve
        xong thi chua co gi de dung — 738 job ngay 11/08/2026 la dung canh do.
        Chay o background thread."""
        today_start = (float(tu_giay) if tu_giay is not None
                       else _time.mktime(_time.localtime()[:3] + (0, 0, 0, 0, 0, -1)))
        imgs = 0
        vids = 0
        seen = set()
        for base in (PROJECTS_DIR, ARCHIVE_DIR):
            try:
                if not base.exists():
                    continue
                for pd in base.iterdir():
                    if not pd.is_dir():
                        continue
                    # ⚠ KHO LƯU ĐẶT TÊN `TL3-0413_20260813_165010` — HAI đuôi,
                    # không phải một. `rsplit("_", 1)` chỉ cắt được `_165010`,
                    # để lại `TL3-0413_20260813` — khác hẳn `TL3-0413` bên
                    # PROJECTS, nên phép chống-đếm-trùng KHÔNG khớp và mã nằm ở
                    # cả hai nơi bị đếm hai lần.
                    #
                    # Đo 13/08/2026: 24 mã có mặt ở CẢ `PROJECTS` lẫn `old/`.
                    code = _ma_goc(pd.name) if base is ARCHIVE_DIR else pd.name
                    if code in seen:
                        continue
                    got = False
                    # ẢNH hôm nay: đếm ở `img_backup` (ảnh GỐC đầy đủ — `copy2`
                    # giữ nguyên mtime gốc). Sau finalize, `img/` đã xoá png nào
                    # đã thành video, nên đếm `img/` là đếm hụt. Dự án chưa
                    # finalize (chưa có `img_backup`) thì lùi về `img/` — an
                    # toàn, vì `_finalize_img` chép sang backup TRƯỚC rồi mới
                    # xoá, không có khe nào ảnh biến mất khỏi cả hai chỗ.
                    #
                    # ⚠ BA THƯ MỤC, KHÔNG PHẢI MỘT. Ảnh cảnh chỉ là phần lớn
                    # nhất, không phải toàn bộ sản lượng ảnh:
                    #
                    #   img_backup/   ảnh CẢNH   — mỗi cảnh một ảnh
                    #   nv/           ảnh NHÂN VẬT/BỐI CẢNH — PHASE 1 sinh từ
                    #                 prompt tham chiếu (`_repair_reference_media_id`)
                    #   thumb/        THUMBNAIL — `_generate_thumbnail` sinh từ
                    #                 prompt trong sheet thumbnail
                    #
                    # Cả ba đều là job ảnh gửi lên máy chủ và đều tính tiền. Đo
                    # 14/08/2026 trên 24 mã: 2.367 cảnh + 24 nhân vật + 72
                    # thumbnail = 2.463. Bỏ hai cái sau là báo hụt 96 ảnh
                    # (3,9%) — và hụt đúng ở phần đắt: ảnh nhân vật phải qua
                    # vòng kiểm/sửa media_id nên tốn hơn ảnh cảnh.
                    #
                    # `thumb/` còn chứa `{MÃ}.png` do
                    # `_fallback_copy_thumbnail_from_character` CHÉP từ `nv/`
                    # khi không có prompt — chép chứ không sinh, nên đếm nó là
                    # đếm hai lần một tấm ảnh. Chỉ lấy `thumb_*`.
                    nguon_anh = (
                        ((pd / "img_backup") if (pd / "img_backup").exists() else (pd / "img"), ""),
                        (pd / "nv", ""),
                        (pd / "thumb", "thumb_"),
                    )
                    for thu_muc, tien_to in nguon_anh:
                        if not thu_muc.exists():
                            continue
                        try:
                            if thu_muc.stat().st_mtime < today_start:
                                continue   # prune: không có file mới hôm nay
                        except OSError:
                            continue
                        for f in list(thu_muc.glob(tien_to + "*.png")) + \
                                 list(thu_muc.glob(tien_to + "*.jpg")):
                            try:
                                if f.stat().st_mtime >= today_start:
                                    imgs += 1; got = True
                            except OSError:
                                pass
                    # VIDEO hôm nay: mp4 nằm ở `vid/` VÀ ở `img/` (bước I2V ghi
                    # đè `img/{n}.png` thành `img/{n}.mp4`).
                    #
                    # ⚠ HAI THƯ MỤC, MỘT SẢN PHẨM — PHẢI KHỬ TRÙNG THEO TÊN FILE.
                    # Đo 13/08/2026 trên TL3-0413: `img/1.mp4` và `vid/1.mp4`
                    # cùng 3.089.785 byte, hash khớp — 49/49 file trùng tên. Đếm
                    # cả hai là nhân đôi sản lượng video, và con số phóng đại đó
                    # đi thẳng lên ô "VIDEO HÔM NAY" của giao diện.
                    da_dem = set()
                    for sub in (pd / "vid", pd / "img"):
                        if not sub.exists():
                            continue
                        try:
                            if sub.stat().st_mtime < today_start:
                                continue   # prune: khong co file moi hom nay
                        except OSError:
                            continue
                        for f in sub.glob("*.mp4"):
                            if f.name in da_dem:
                                continue
                            try:
                                if f.stat().st_mtime >= today_start:
                                    da_dem.add(f.name)
                                    vids += 1; got = True
                            except OSError:
                                pass
                    if got:
                        seen.add(code)
            except Exception:
                pass
        return imgs, vids

    def _refresh_project_views_worker(self):
        rows = []
        err = None
        archived_today = 0
        try:
            PROJECTS_DIR.mkdir(parents=True, exist_ok=True)
            projects = [p for p in PROJECTS_DIR.iterdir() if p.is_dir()]
            projects.sort(key=lambda p: p.stat().st_mtime, reverse=True)
            all_pairs = self._get_server_pairs(only_available=False)
            pair_by_server = {p["server_name"]: p for p in all_pairs}
            for pd in projects:
                if self._is_project_exported_to_visual(pd):
                    continue
                rows.append(self._project_row(pd, pair_by_server=pair_by_server))
            state_order = {"RUN": 0, "WAIT": 1, "DONE": 2, "BLOCK": 3}
            rows.sort(key=lambda r: (state_order.get(r.get("state", "BLOCK"), 9), r.get("code", "")))
            archived_today = self._count_archived_today()
            imgs_today, vids_today = self._count_production_today()
        except Exception as exc:
            err = exc
            imgs_today = vids_today = 0
        self.after(0, lambda rows=rows, err=err, at=archived_today, it=imgs_today, vt=vids_today:
                   self._apply_project_views(rows, err, at, it, vt))

    def _apply_project_views(self, rows, err=None, archived_today=0, images_today=0, videos_today=0):
        if err is not None:
            self._log(f"Khng qut c PROJECTS: {err}", "WARN")
        try:
            self.pages["home"].refresh_projects_overview(rows, archived_today=archived_today,
                                                         images_today=images_today, videos_today=videos_today)
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

        # Reset = người dùng nói "thử lại mã này". Gỡ luôn cờ ĐỖ LẠI, nếu không
        # thì reset xong hàng chờ vẫn bỏ qua nó và không ai hiểu vì sao.
        try:
            (getattr(self, "_luot_trang", None) or {}).pop(code, None)
        except Exception:
            pass

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
                try:
                    if hasattr(wb, 'workbook') and wb.workbook:
                        wb.workbook.close()
                except Exception:
                    pass

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

    def _project_row(self, pd, pair_by_server=None):
        code = pd.name
        manual_done = self._is_project_manually_done(pd)
        has_audio = any(pd.glob(ext) for ext in ("*.mp3", "*.wav", "*.m4a", "*.flac", "*.ogg", "*.aac"))
        srt = pd / f"{code}.srt"
        ep = self._project_excel_path(pd)
        binding = self._load_project_pair_binding(pd)
        if pair_by_server is None:
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
            if state == "DONE":
                self._repair_endpoint_done_marker(pd, reason="auto_done")
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
                    try:
                        if hasattr(wb, 'workbook') and wb.workbook:
                            wb.workbook.close()
                    except Exception:
                        pass
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

        if state == "DONE":
            self._repair_endpoint_done_marker(pd, reason="auto_done")

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

    def _so_ma_song_song_shopapi(self):
        """Bao nhiêu MÃ chạy cùng lúc khi đi toàn API."""
        mac_dinh = SHOPAPI_MA_SONG_SONG_MAC_DINH
        try:
            return max(1, int(self.config_data.get("shopapi_ma_song_song", mac_dinh) or mac_dinh))
        except (TypeError, ValueError):
            return mac_dinh

    def _pair_ao_shopapi(self):
        """Vài "chỗ làm" ẢO để hàng chờ có cái mà phát việc khi chạy toàn API.

        ⚠ VÌ SAO PHẢI CÓ — HÀNG CHỜ ĐỨNG IM KHÔNG BÁO GÌ
        -------------------------------------------------
        Vòng lặp hàng chờ chỉ giao việc cho một "pair" (server + tài khoản
        Flow). Đi toàn API thì KHÔNG có server nào, cũng KHÔNG có tài khoản nào
        — nên `_get_server_pairs` trả về rỗng, `free_pairs` rỗng, và vòng lặp
        quay vòng mãi mà không bao giờ phát một việc nào. Không lỗi, không cảnh
        báo, chỉ là không có gì xảy ra: kiểu hỏng khó đoán nhất.

        Pair ảo giữ nguyên cách đếm "mã nào đang chạy" của hàng chờ, chỉ bỏ đi
        phần server/tài khoản vốn không tồn tại. Số lượng chính là số MÃ chạy
        song song — song song ở mức job thì đã có trần của máy chủ lo.
        """
        return [{
            "pair_id": "shopapi-{0}".format(i + 1),
            "server_name": "API shopapi",
            "server_url": "",
            "server_config": {},
            "flow_account_name": "",
            "flow_account": None,
            "enabled": True,
            "available": True,
            "queue_size": 0,
            "ao_shopapi": True,      # `_build_project_pair_cfg` đọc cờ này
        } for i in range(self._so_ma_song_song_shopapi())]

    def _get_server_pairs(self, only_available=False):
        # ĐI TOÀN API -> chỗ làm ẢO, BẤT KỂ trong cấu hình còn bao nhiêu server.
        #
        # ⚠ TRƯỚC 11/08/2026 dòng này còn đòi `local_server_list` PHẢI RỖNG. Ai
        # đã từng chạy đường Chrome thì danh sách server vẫn nằm đó (10 dòng),
        # nên điều kiện không bao giờ đúng và cả nhánh này chết. Hậu quả: chạy
        # toàn API mà số mã song song vẫn bị ghim bằng số Chrome portable —
        # những cái mà chế độ API KHÔNG hề mở, KHÔNG hề dùng, và người dùng
        # không có lý do gì để nghĩ là còn liên quan.
        #
        # `shopapi_ma_song_song` cũng chết theo: nó chỉ được đọc trong
        # `_pair_ao_shopapi`. Vặn con số đó không có tác dụng gì cả — không lỗi,
        # không cảnh báo, chỉ là không có gì thay đổi.
        #
        # Xoá server khỏi cấu hình KHÔNG phải cách chữa: người dùng còn muốn
        # quay lại đường Chrome. Cái quyết định phải là CHẾ ĐỘ ĐANG CHẠY.
        if self._chi_dung_shopapi(self.config_data):
            return self._pair_ao_shopapi()
        account_map = self._get_flow_account_map()
        status_map = {str(s.get("url", "")).rstrip("/"): s for s in (self.server_status_cache or [])}
        pairs = []
        _now = _time.time()
        # POOL MODE: server không tạo ảnh/video -> ĐỪNG log "Server unavailable"/"Built pairs" (noise gây hiểu lầm đang rà server)
        _pm = str(self.config_data.get("generation_backend", "") or "").strip() == "veo3top_b_pool"
        _should_log_pairs = (not _pm) and getattr(self, "_server_pair_debug_enabled", False) and (_now - getattr(self, "_server_pair_debug_last_ts", 0)) >= 30
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

    def _binding_yaml_path(self, project_dir):
        return Path(project_dir) / ".ve3_binding.yaml"

    def _read_binding_yaml(self, project_dir):
        try:
            import yaml
            p = self._binding_yaml_path(project_dir)
            if p.exists():
                return yaml.safe_load(p.read_text(encoding="utf-8")) or {}
        except Exception:
            pass
        return {}

    def _write_binding_yaml(self, project_dir, data):
        try:
            import yaml
            p = self._binding_yaml_path(project_dir)
            p.write_text(yaml.dump(data, default_flow_style=False, allow_unicode=True), encoding="utf-8")
        except Exception:
            pass

    def _load_project_pair_binding(self, project_dir):
        # 1. Try YAML cache first (always available, never locked)
        yaml_data = self._read_binding_yaml(project_dir)
        if yaml_data.get("bound_server_name"):
            return yaml_data

        # 2. Try Excel
        ep = self._project_excel_path(project_dir)
        if not ep.exists():
            return yaml_data or {}
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
            try:
                if hasattr(wb, 'workbook') and wb.workbook:
                    wb.workbook.close()
            except Exception:
                pass
            if cache_sig is not None:
                self._project_binding_cache[cache_key] = {"sig": cache_sig, "data": dict(data)}
            if data.get("bound_server_name"):
                self._write_binding_yaml(project_dir, data)
            return data
        except Exception:
            return yaml_data or {}

    def _save_project_pair_binding(self, project_dir, pair):
        data = {
            "flow_project_id": pair.get("flow_project_id", ""),
            "flow_account_name": pair["flow_account_name"],
            "bound_account_name": pair["flow_account_name"],
            "bound_server_name": pair["server_name"],
            "bound_server_url": pair["server_url"],
        }
        self._write_binding_yaml(project_dir, data)

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
            try:
                if hasattr(wb, 'workbook') and wb.workbook:
                    wb.workbook.close()
            except Exception:
                pass
        except Exception as exc:
            self._log(f"[QUEUE] {project_dir.name}: khong ghi duoc binding vao Excel ({exc}), YAML da luu", "WARN", "ve3")

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
        # ═══ ĐI TOÀN API: RÀNG BUỘC SERVER/ACCOUNT CŨ KHÔNG CÒN NGHĨA GÌ ═══
        #
        # `.ve3_binding.yaml` của mọi mã cũ đều ghi `bound_server_name: sv9` kiểu
        # vậy — dấu vết từ hồi chạy đường Chrome. Chế độ API không có sv nào,
        # không có tài khoản Flow nào; giữ ràng buộc đó thì nhánh `if bound_server`
        # bên dưới tìm không ra, ghi "missing from config. Waiting (will not
        # reassign)" rồi trả `None`, và **mã đó không bao giờ chạy nữa**.
        #
        # Đây là cái bẫy đi kèm việc bật chỗ làm ảo (xem `_get_server_pairs`):
        # gỡ một nút thắt mà quên chỗ này là đổi "chạy chậm" lấy "đứng hẳn" — 75
        # mã đang có binding trỏ vào sv1..sv10.
        #
        # KHÔNG xoá file binding: người dùng còn quay lại đường Chrome, và
        # `flow_project_id` trong đó vẫn còn giá trị. Chỉ BỎ QUA nó ở chế độ này.
        if self._chi_dung_shopapi(self.config_data):
            if not free_pairs:
                return None
            return sorted(
                free_pairs,
                key=lambda p: (self.queue_pair_last_used.get(p["pair_id"], 0), p["pair_id"]),
            )[0]

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
        if pair.get("ao_shopapi"):
            # Chỗ làm ẢO: không có server nào để trỏ tới. Phải để danh sách RỖNG
            # chứ đừng nhét một mục url="" vào — `_init_server_pool` thấy danh
            # sách khác rỗng là dựng ServerPool cho một server không tồn tại.
            cfg["local_server_list"] = []
            cfg["local_server_url"] = ""
            cfg["flow_bearer_token"] = ""
            cfg["flow_project_id"] = ""
            cfg["flow_project_url"] = ""
            return cfg
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
                    # Kill the WHOLE tree (python headless + its claude.exe child)
                    # so the Claude CLI process never orphans on stop.
                    self._kill_pid_tree(proc.pid)
                    break
                if log_cb:
                    log_cb(line.rstrip(), "INFO")
            code = proc.wait()
            if code != 0 and not self.queue_stop_requested:
                raise RuntimeError(f"Excel engine failed with exit code {code}")
        finally:
            # Defensive: if the process is still alive (timeout/crash), kill its tree.
            try:
                if proc.poll() is None:
                    self._kill_pid_tree(proc.pid)
            except Exception:
                pass
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
            old_wb = getattr(self, "wb", None)
            if old_wb:
                try:
                    if hasattr(old_wb, 'workbook') and old_wb.workbook:
                        old_wb.workbook.close()
                except Exception:
                    pass
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

    def _chi_dung_shopapi(self, cfg):
        """CẢ ảnh LẪN video đều đi API shopapi, và máy ĐÃ CÓ khoá?

        ⚠ PHẢI KHỚP TỪNG CHỮ với `ve3_worker._shopapi_only`, đừng để hai bên
        lệch nhau. Cổng `_build_cfg` chạy TRƯỚC khi worker khởi động, nên nếu
        cổng khắt khe hơn worker thì cấu hình hợp lệ vẫn bị chặn ngay ở cửa —
        đúng chuyện đã xảy ra: chọn shopapi cho cả hai khâu mà vẫn bị đòi
        "Can token hop le", trong khi worker thừa biết là không cần.

        Đòi CẢ HAI khâu, không phải một: còn một khâu đi đường cũ là còn mở
        Chrome, mà đường cũ thì vẫn cần auth thật.

        Đòi CÓ KHOÁ nữa: thiếu khoá thì worker tự lùi về đường cũ, và đường cũ
        cần auth — bỏ qua cổng lúc đó là để nó chết sâu hơn ở giữa lượt chạy.
        """
        # MỘT bản cài đặt duy nhất, ở cấp module (xem `che_do_toan_api`). Trước
        # đây phép kiểm này nằm hẳn trong đây, nên `SettingsPage` — lớp khác —
        # gọi không tới và phải tự đoán lấy. Hai bản đoán là hai câu trả lời
        # khác nhau cho cùng một câu hỏi.
        return che_do_toan_api(cfg)

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
        if not t and not auto_auth_ready and not self._chi_dung_shopapi(c):
            messagebox.showwarning(
                "Flow Auth",
                "Can token hop le hoac it nhat 1 pair co du gmail bundle va chrome path "
                "trong Cai dat.\n\n"
                "(Neu muon chay HOAN TOAN qua API shopapi thi dat CA hai: backend video = "
                "'API shopapi' VA backend anh = 'API shopapi (anh)', roi luu khoa sk_live_ "
                "o trang Cai dat — luc do khong can token Flow nua.)")
            return None
        if t and not t.startswith("ya29."):
            messagebox.showwarning("Token","Token phi bt u ya29."); return None
        c["flow_bearer_token"] = t
        c["flow_project_id"] = str(c.get("flow_project_id", "") or "").strip()
        c["flow_project_url"] = c.get("flow_project_url", "") or ""
        if self.project_dir:
            c.update(self._project_topic_runtime_config(Path(self.project_dir), c))
        # max_concurrent (Parallel jobs) TỰ ĐỘNG trong worker (theo pool/số mã) -> không set từ GUI nữa
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
                # BUG CU: cho nay unpack 3 phan tu tu _submit_image von tra 4 -> ValueError
                # moi lan bam Regen. Sua bang cach nhan du 4 (KHONG doi kieu tra ve cua
                # _submit_image: ca chuc cho khac dang phu thuoc vao dung 4 phan tu do).
                t0 = _time.time(); ok, med, si, err = w._submit_image(prompt, ip)
                el = round(_time.time()-t0,1); ex = {"elapsed":el, **si}
                if ok:
                    self.wb.update_character(cid, status="done", media_id=med or "", reference_media_checked=False); self.wb.safe_save()
                    self.after(0, lambda: self._reload_wb())
                    self.after(0, lambda: self.pages["gen"].update_char(cid, "done", ex))
                    self.after(0, lambda: self._log(f"{cid} done ({el}s)","SUCCESS"))
                else:
                    self.after(0, lambda: self.pages["gen"].update_char(cid, "error", ex))
                    self.after(0, lambda: self._log(f"{cid} failed: {err[:300]}","ERROR"))
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
                # BUG CU (giong regen_character): unpack 3 tu ham tra 4 -> ValueError.
                t0 = _time.time(); ok, med, si, err = w._submit_image(prompt, ip, refs)
                el = round(_time.time()-t0,1); ex = {"elapsed":el, **si}
                if ok:
                    self.wb.update_scene(sid, status_img="done", media_id=med or ""); self.wb.safe_save()
                    self.after(0, lambda: self._reload_wb())
                    self.after(0, lambda: self.pages["gen"].update_scene(sid, "done", ex))
                    self.after(0, lambda: self._log(f"Scene {sid} done ({el}s)","SUCCESS"))
                else:
                    self.wb.update_scene(sid, status_img="error"); self.wb.safe_save()
                    self.after(0, lambda: self.pages["gen"].update_scene(sid, "error", ex))
                    self.after(0, lambda: self._log(f"Scene {sid} failed: {err[:300]}","ERROR"))
            except Exception as e:
                self.after(0, lambda: self.pages["gen"].update_scene(sid, "error"))
                self.after(0, lambda: self._log(f"Error: {e}","ERROR"))
        threading.Thread(target=_r, daemon=True).start()

    def _reload_wb(self):
        """Reload workbook t file  ly data mi nht (media_id, status...)."""
        if self.excel_path and self.excel_path.exists():
            from modules.excel_manager import PromptWorkbook
            old_wb = getattr(self, "wb", None)
            if old_wb:
                try:
                    if hasattr(old_wb, 'workbook') and old_wb.workbook:
                        old_wb.workbook.close()
                except Exception:
                    pass
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
        # Đi toàn API shopapi thì KHÔNG có bước nào chạm tới server Chrome.
        # Xem chú thích ở `_chi_dung_shopapi`.
        if (not cfg.get("local_server_url") and not cfg.get("local_server_list")
                and not self._chi_dung_shopapi(cfg)):
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
        old_tick = getattr(self, "_tick_timer_id", None)
        if old_tick is not None:
            self.after_cancel(old_tick)
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
            try:
                if hasattr(wb, 'workbook') and wb.workbook:
                    wb.workbook.close()
            except Exception:
                pass
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
            try:
                if hasattr(wb, 'workbook') and wb.workbook:
                    wb.workbook.close()
            except Exception:
                pass
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
            # Cancel auto-restart if user manually stops
            if self._pending_restart_id:
                self.after_cancel(self._pending_restart_id)
                self._pending_restart_id = None
                self._auto_restart_at = 0
            self.btn_go.configure(text="Stopping...", fg_color="#555")
            self.pages["home"].btn_run_center.configure(text="Stopping...", fg_color="#555")
            self._log("[QUEUE] Dang yeu cau dung sau task hien tai...", "WARN")
            self._log("[QUEUE/EXCEL] Dang yeu cau dung worker Excel sau task hien tai...", "WARN", "excel")
            return

        cfg = self._build_cfg()
        if not cfg:
            return
        chi_shopapi = self._chi_dung_shopapi(cfg)
        if (not cfg.get("local_server_url") and not cfg.get("local_server_list")
                and not chi_shopapi):
            messagebox.showwarning("Thieu server", "Them server trong Cai dat truoc!")
            return
        online_count = sum(1 for s in self.server_status_cache if s.get("available"))
        if self.server_status_cache and online_count == 0:
            threading.Thread(target=self._refresh_server_status_sync, daemon=True).start()
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
        # Schedule auto-restart 12h (reset all errors periodically)
        if not self._pending_restart_id:
            self._schedule_auto_restart()
        with self.queue_lock:
            self.queue_active_excel.clear()
            self.queue_active_ve3.clear()
            self.queue_ve3_stage.clear()
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
        # Extension mode: workers tu mo Chrome khi can (on-demand), khong start tat ca luc dau
        self.queue_excel_thread = threading.Thread(target=self._queue_excel_loop, daemon=True)
        self.queue_ve3_thread = threading.Thread(target=self._queue_ve3_loop, args=(cfg,), daemon=True)
        self.queue_excel_thread.start()
        self.queue_ve3_thread.start()

    def _start_extension_instances(self, cfg):
        """Start ALL extension instances song song — y het FlowKit GUI _start_all."""
        try:
            import sys as _sys
            _sys.path.insert(0, str(VE3_DIR.parent / "flowkit"))
            _sys.path.insert(0, str(SUITE_ROOT / "server" / "flowkit"))
            from modules.flow_extension_auth import FlowExtensionAuth
            servers = cfg.get("local_server_list", [])
            if not servers:
                self.extension_ready_event.set()
                return
            FlowExtensionAuth.start_all_instances(
                servers, str(SUITE_ROOT),
                log_func=lambda m: self._log(m, "INFO", "ve3"),
                ready_event=self.extension_ready_event,
            )
            self._log("[QUEUE] Extension instances started.", "SUCCESS", "ve3")
        except Exception as e:
            self._log(f"[QUEUE] Extension start error: {e}", "ERROR", "ve3")
            self.extension_ready_event.set()

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

        scan_msg = (
            f"[DEBUG] _queue_projects: scanned {total_dirs} dirs, "
            f"returned {len(projects)} projects | "
            f"skipped: manual_done={skipped_reasons['manual_done']}, "
            f"manual_skip={skipped_reasons['manual_skip']}, "
            f"endpoint_complete={skipped_reasons['endpoint_complete']}, "
            f"endpoint_hold={skipped_reasons['endpoint_hold']}"
        )
        self._log(scan_msg, "INFO")
        self._log(scan_msg, "INFO", "excel")

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
        """Ưu tiên mã đang làm dở trước, sau đó round-robin theo series (TL1/TL2/TL3) cho mã mới."""
        from collections import OrderedDict
        import re as _re
        in_progress = []
        new_projects = []
        for pd in projects:
            if self._is_project_in_progress(pd):
                in_progress.append(pd)
            else:
                new_projects.append(pd)
        in_progress.sort(key=priority_key_func)
        # Group by series prefix (TL1, TL2, TL3, TH1, ...) for even spread across series
        series_groups = OrderedDict()
        for pd in new_projects:
            code = Path(pd).name
            m = _re.match(r"^([A-Za-z]+\d+)-", code, flags=_re.IGNORECASE)
            series = m.group(1).upper() if m else "unknown"
            series_groups.setdefault(series, []).append(pd)
        for series in series_groups:
            series_groups[series] = sorted(series_groups[series], key=priority_key_func)
        round_robin = []
        while any(series_groups.values()):
            for series in list(series_groups.keys()):
                if series_groups[series]:
                    round_robin.append(series_groups[series].pop(0))
                else:
                    del series_groups[series]
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

        # FAST-SKIP: Excel đang bị GHI (Excel worker) -> ĐỪNG đọc chậm (openpyxl treo ~30s/lần × 3 = ~90s, CHẶN cả
        # vòng lặp dispatcher). Trả cache cũ (nếu có) hoặc None -> dispatcher bỏ qua NHANH, đọc lại vòng sau khi ghi xong.
        if self._excel_is_locked(ep):
            cached = self._project_state_cache.get(cache_key)
            return cached.get("data") if cached else None

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
                try:
                    thumbnails = wb.get_thumbnails() if hasattr(wb, 'get_thumbnails') else []
                except Exception:
                    thumbnails = []
                try:
                    if hasattr(wb, 'workbook') and wb.workbook:
                        wb.workbook.close()
                except Exception:
                    pass
                data = {"scenes": scenes_all, "stats": stats, "summary": summary, "thumbnails": thumbnails}
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
            has_done_lock = self._endpoint_done_marker(project_dir).exists()
            if has_done_lock:
                return True
            has_visual = self._is_project_exported_to_visual(project_dir)
            has_archive = self._has_project_archive(project_dir)
            complete = has_visual or has_archive
            if complete:
                reasons = []
                if has_visual: reasons.append(f"visual")
                if has_archive: reasons.append(f"archive")
                self._log(f"[DEBUG] {project_dir.name}: endpoint_complete ({', '.join(reasons)})", "INFO", "excel")
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
        # KHOA OS (mo file de GHI) LA CHUAN DUY NHAT. Marker ~$/.lock/.tmp CO THE STALE (sot lai tu lan
        # crash truoc) -> KHONG duoc tin marker MOT MINH: file mo ghi duoc nhung tool tuong bi khoa ->
        # KET VINH VIEN ca 5 ma (worker skip read + dispatcher skip). Da tung gap tren may khac.
        if not excel_path.exists():
            # File chinh CHUA CO -> co the dang duoc tao do dang (tmp/lock) => coi nhu ban, cho.
            lock_path = excel_path.with_suffix(".xlsx.lock")
            temp_path = excel_path.with_suffix(".xlsx.tmp")
            return lock_path.exists() or temp_path.exists()
        # File TON TAI: thu mo GHI. Mo duoc = KHONG bi OS khoa (Excel da dong, khong process nao giu handle)
        # -> marker con sot lai chi la STALE, KHONG tinh la khoa. Mo khong duoc = OS khoa THAT (Excel dang
        # mo / process khac dang giu) -> khoa.
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
            # Check thumbnail — excel chua xong neu thumbnail chua co
            thumbnails = state.get("thumbnails") or []
            if not thumbnails:
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
            try:
                if hasattr(wb, 'workbook') and wb.workbook:
                    wb.workbook.close()
            except Exception:
                pass
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
        # Gioi han so ma Excel chay dong thoi — doc lai moi vong de cap nhat khi user thay doi setting
        try:
            while not self.queue_stop_requested:
                _engine = str(self.config_data.get("excel_engine", "api") or "api").strip().lower()
                if _engine in ("claude_cli", "claude", "cli"):
                    # Claude Code CLI uses ONE Claude Max account — keep concurrency
                    # low (default 2) so many parallel claude.exe don't hit rate limits.
                    max_excel_concurrent = self.config_data.get("claude_cli_max_parallel", 5)
                else:
                    max_excel_concurrent = self.config_data.get("excel_workers", 2)
                try:
                    max_excel_concurrent = int(max_excel_concurrent)
                except Exception:
                    max_excel_concurrent = 2
                max_excel_concurrent = max(1, max_excel_concurrent)

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
                self._log(f"[DEBUG] Excel queue returned {len(excel_queue)} projects, max_concurrent={max_excel_concurrent}, active={active_count}", "INFO", "excel")

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

    def _project_pending_img_vid(self, project_dir):
        """Trả (pending_img, pending_vid): còn scene chưa có FILE ảnh / video (status không skip/failed).
        'error' = lỗi tài nguyên tạm thời -> VẪN pending (retry lượt sau, backoff marker).
        'failed'/'skip' = terminal -> KHÔNG pending. Raise nếu Excel không đọc được (caller xử lý)."""
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
                has_img = ((img_dir / f"{sid}.png").exists() or (img_dir / f"{sid}.jpg").exists()
                           or (img_dir / f"{sid}.mp4").exists())
                if (not has_img) and st_img not in ("skip", "failed"):
                    pending_img = True
            video_prompt = str(getattr(s, "video_prompt", "") or "").strip()
            if video_prompt:
                st_vid = str(getattr(s, "status_vid", "") or "").strip().lower()
                has_vid = ((vid_dir / f"{sid}.mp4").exists() or (img_dir / f"{sid}.mp4").exists())
                # video I2V cần ẢNH NGUỒN. Ảnh còn đang làm -> tính qua pending_img (chưa tới lượt video).
                # Ảnh 'failed'/không có file -> video KHÔNG làm được -> KHÔNG coi là pending (tránh kẹt vô tận).
                has_img_file = ((img_dir / f"{sid}.png").exists() or (img_dir / f"{sid}.jpg").exists())
                if (not has_vid) and has_img_file and st_vid not in ("skip", "failed"):
                    pending_vid = True
            if pending_img and pending_vid:
                break   # cả 2 đã xác định -> đủ (needs_image/needs_video đều dùng được)
        return pending_img, pending_vid

    def _project_has_pending_ve3_units(self, project_dir):
        """True when project still has actionable VE3 work (img/video)."""
        ep = self._project_excel_path(project_dir)
        if not ep.exists():
            return False
        try:
            pi, pv = self._project_pending_img_vid(project_dir)
            return bool(pi or pv)
        except Exception as exc:
            self._log(f"[QUEUE/VE3] {project_dir.name}: khng c c Excel {exc}", "WARN", "ve3")
            # Safe default: if cannot verify, treat as still pending to avoid premature endpoint.
            return True

    def _project_needs_image(self, project_dir):
        """TRẠM ẢNH: còn scene chưa có ảnh -> cần chạy worker image-only."""
        ep = self._project_excel_path(project_dir)
        if not ep.exists():
            return False
        try:
            pi, _pv = self._project_pending_img_vid(project_dir)
            return bool(pi)
        except Exception:
            return False   # không chắc -> KHÔNG claim ở trạm ảnh (an toàn)

    def _project_needs_video(self, project_dir):
        """TRẠM VIDEO: ẢNH XONG HẾT (không còn pending_img) + còn video chưa xong -> chạy worker video-only.
        Điều kiện 'ảnh đủ hẳn' (not pending_img) đảm bảo I2V có ảnh + tránh race giữa 2 trạm."""
        ep = self._project_excel_path(project_dir)
        if not ep.exists():
            return False
        try:
            pi, pv = self._project_pending_img_vid(project_dir)
            return bool(pv and not pi)
        except Exception:
            return False

    def _compute_pool_capacity(self):
        """NHÀ MÁY THÔNG MINH: đọc /health 2 pool -> đếm 'nhân sự' đang KHAI THÁC thực tế -> TỰ TÍNH số MÃ mỗi trạm
        + số LUỒNG mỗi mã (linh hoạt: ít account -> ít mã; nhiều account -> nhiều mã). Cache 8s. Fallback config nếu pool tắt."""
        now = _time.time()
        c = getattr(self, "_pool_cap_cache", None)
        if c and (now - c.get("ts", 0)) < 8:
            return c
        import urllib.request, json as _json

        def _get(port):
            try:
                r = urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=1.5)
                return _json.loads(r.read().decode("utf-8", "replace"))
            except Exception:
                return None

        cfgd = getattr(self, "config_data", {}) or {}
        # ẢNH (8789): capacity = account KHAI THÁC ĐƯỢC (tổng - cách ly 429 - chết)
        h = _get(8789)
        if h:
            img_total = int(h.get("candidates", 0) or 0)
            img_cap = max(0, img_total - int(h.get("known_resting", 0) or 0) - int(h.get("known_dead", 0) or 0))
            img_up = True
        else:
            img_total = int(cfgd.get("image_pool_accounts", 96) or 96); img_cap = img_total; img_up = False
        # VIDEO (8788): capacity = Ultra không đang nghỉ
        v = _get(8788)
        if v:
            vaccs = v.get("accounts") or []
            vid_total = len(vaccs)
            vid_cap = sum(1 for a in vaccs if (a.get("resting_in") or 0) <= 1)
            vid_up = True
        else:
            vid_total = 10; vid_cap = 10; vid_up = False
        # TỰ TÍNH: PER = số nhân sự / 1 mã. Ảnh 12 acc/mã (mỗi mã ~12 luồng, đầy pool). Video 4 Ultra/mã.
        IMG_PER = 12; VID_PER = 4
        base_img = img_cap if img_cap > 0 else img_total
        base_vid = vid_cap if vid_cap > 0 else vid_total
        img_codes = max(1, min(20, round(base_img / IMG_PER)))
        vid_codes = max(1, min(8, round(base_vid / VID_PER)))
        img_per = max(1, -(-base_img // img_codes))   # luồng/mã ảnh = ceil(capacity/codes)
        # VIDEO: luồng/mã = ĐẦY POOL. Pool video có (acc SỐNG × video_workers_per_account) slot. Chia đều cho vid_codes
        # -> tổng luồng các mã video = full pool -> KHÔNG worker nào ngồi không (pool AIMD tự giảm nếu throttle).
        _vwork = int(cfgd.get("video_workers_per_account", 7) or 7)
        vid_slots = max(1, base_vid) * _vwork
        vid_per = max(1, -(-vid_slots // vid_codes))   # ceil(slots/codes)
        res = {"ts": now, "img_up": img_up, "vid_up": vid_up,
               "img_total": img_total, "img_cap": img_cap, "img_codes": img_codes, "img_per": img_per,
               "vid_total": vid_total, "vid_cap": vid_cap, "vid_codes": vid_codes, "vid_per": vid_per}
        self._pool_cap_cache = res
        return res

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

    def _run_single_project_ve3(self, pd, pair, cfg, mode="all"):
        """Run VE3 worker + music as subprocesses. Kill = instant stop.
        mode: 'all' (ảnh+video), 'image-only' (trạm ảnh), 'video-only' (trạm video)."""
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
            # Inject nguon metadata (reference_channel, psychology_reference_image)
            nguon_meta = self._load_project_nguon_metadata(pd, code)
            if nguon_meta.get("reference_channel"):
                pair_cfg["reference_channel"] = nguon_meta["reference_channel"]
            if nguon_meta.get("psychology_reference_image"):
                pair_cfg["psychology_reference_image"] = nguon_meta["psychology_reference_image"]
            if nguon_meta.get("topic"):
                pair_cfg.setdefault("topic", nguon_meta["topic"])
            # TRUYỀN số LUỒNG/mã TỰ TÍNH (theo pool thực) -> worker gửi đúng số job song song để ĐẦY pool.
            # TRẠM ẢNH -> img_per (theo pool ảnh); TRẠM VIDEO -> vid_per (theo pool video 10 acc × luồng/acc).
            # (Trước đây video-only KHÔNG set -> worker rơi vào fallback dùng image_pool_accounts = sai pool -> pool video đói.)
            if mode in ("image-only", "video-only"):
                try:
                    _cap = self._compute_pool_capacity()
                    _key = "img_per" if mode == "image-only" else "vid_per"
                    _rmc = int(_cap.get(_key, 0) or 0)
                    if _rmc > 0:
                        pair_cfg["run_max_concurrent"] = _rmc
                except Exception:
                    pass
            config_file = pd / ".ve3_run_config.json"
            with open(config_file, "w", encoding="utf-8") as f:
                json.dump(pair_cfg, f, ensure_ascii=False, indent=2)

            excel_path = self._project_excel_path(pd)
            self._save_project_pair_binding(pd, pair)

            # Queue VE3 mode finishes by image/video only; music is not launched here.
            # Launch VE3 worker subprocess
            worker_script = str(VE3_DIR / "ve3_worker.py")
            ve3_proc = subprocess.Popen(
                [sys.executable, worker_script, str(pd), "--config", str(config_file), "--mode", str(mode)],
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
                else:
                    # ═══ DÒNG KHÔNG CÓ TIỀN TỐ `@@` LÀ LỜI TRĂNG TRỐI ═══
                    #
                    # `stderr` được gộp vào `stdout` ngay ở `Popen`, nên mọi
                    # traceback Python, mọi `MemoryError`, mọi lời than của thư
                    # viện đều đi qua đúng đường này — và không cái nào bắt đầu
                    # bằng `@@LOG|`.
                    #
                    # Bản trước để chúng rơi khỏi chuỗi `elif` mà không làm gì.
                    # Nghĩa là worker chết vì lý do gì cũng KHÔNG ai biết: người
                    # dùng chỉ thấy mã dừng, log sạch bong, không một dấu vết.
                    # Chiều 11/08/2026 mất cả buổi vì đúng chỗ này.
                    self._log("[{0}] {1}".format(code, line), "ERROR", "ve3")

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
            self._ghi_so_luot_trang(code, res)

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
                self.queue_ve3_stage.pop(code, None)
            # TRẠM ẢNH (image-only): xong ẢNH thì KHÔNG chốt endpoint (video CHƯA làm; res.success của ảnh=True sẽ
            # chốt OAN). Vòng loop kế sẽ thấy mã cần VIDEO -> trạm video làm video + finalize + chốt endpoint.
            if mode == "image-only":
                self._log(f"[QUEUE] {code}: [ẢNH] xong ảnh -> chuyển trạm VIDEO (chưa chốt endpoint)", "INFO", "ve3")
            # Endpoint check - skip if already completed (e.g. by _manual_complete_project)
            elif pd.exists() and not self._is_project_endpoint_complete(pd):
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

    #: Bao nhiêu lượt chạy TRẮNG liên tiếp thì đỗ mã lại.
    #:
    #: "Trắng" = worker chạy xong mà KHÔNG ra được sản phẩm nào (`completed == 0`)
    #: trong khi vẫn có việc hỏng. Ba lượt là đủ để phân biệt sự cố thoáng qua
    #: (nhà máy vừa restart, mạng chập) với thứ hỏng vĩnh viễn.
    LUOT_TRANG_TOI_DA = 3

    def _ghi_so_luot_trang(self, code, res):
        """Đếm số lượt chạy TRẮNG liên tiếp của một mã, để còn đỗ nó lại.

        ═══ VÌ SAO CẦN, VÀ NÓ ĐÃ ĂN MẤT BAO NHIÊU ═══

        Hàng chờ chỉ nhìn `success`. Một mã làm xong 61/62 đơn vị mà đơn vị thứ
        62 hỏng vĩnh viễn thì `success = False`, và hàng chờ bật lại. Worker mới
        chạy 4 giây, hỏng đúng đơn vị đó, thoát. Rồi lại bật.

        Đo log ngày 12/08/2026: **126 lượt bật worker cho 21 mã** — TL3-0401 một
        mình 24 lượt, mỗi lượt 4 giây và ra 0 sản phẩm:

            09:03:06  co loi {total: 1, completed: 0, failed: 1}
            09:05:54  co loi {total: 1, completed: 0, failed: 1}
            09:08:41  ...

        Cái giá không nằm ở 4 giây đó. Mỗi lượt bật **chiếm một chỗ pair**, mà
        chỉ có 8 chỗ — nên đám mã zombie quay vòng chiếm hết, và mã còn việc thật
        đứng ngoài. Log cùng ngày có **1.426 dòng `skip no_free_pair`**, trong khi
        sản lượng 10 phút cuối là 0 ảnh, 0 video.

        Đỗ lại KHÔNG phải bỏ: mã vẫn nguyên trong danh sách, việc vẫn nguyên
        trong Excel. Nó chỉ thôi tranh chỗ với mã còn làm được việc, cho tới khi
        người dùng bấm Reset hoặc mở lại tool.
        """
        try:
            xong = int((res or {}).get("completed") or 0)
            hong = int((res or {}).get("failed") or 0)
        except (TypeError, ValueError):
            xong = hong = 0
        kho = getattr(self, "_luot_trang", None)
        if kho is None:
            kho = self._luot_trang = {}
        if xong > 0 or hong <= 0:
            kho.pop(code, None)          # có ra hàng -> quên hết lượt trắng cũ
            return
        n = kho.get(code, 0) + 1
        kho[code] = n
        if n >= self.LUOT_TRANG_TOI_DA:
            moc = getattr(self, "_do_lai_luc", None)
            if moc is None:
                moc = self._do_lai_luc = {}
            moc[code] = time.time()
            self._log(
                f"[QUEUE/VE3] {code}: ĐỖ LẠI — {n} lượt chạy liên tiếp KHÔNG ra sản phẩm nào "
                f"(việc còn lại hỏng vĩnh viễn). Mã vẫn giữ nguyên, việc vẫn nguyên trong "
                f"Excel; nó chỉ thôi tranh chỗ pair với mã còn làm được. Bấm Reset ở dòng đó "
                f"để chạy lại.", "WARN", "ve3")
        else:
            self._log(f"[QUEUE/VE3] {code}: lượt trắng {n}/{self.LUOT_TRANG_TOI_DA} "
                      f"(chạy xong mà 0 sản phẩm)", "WARN", "ve3")

    #: Đỗ lại HẾT HẠN sau ngần này giây.
    #:
    #: ═══ VÌ SAO ĐỖ LẠI KHÔNG ĐƯỢC LÀ VĨNH VIỄN — 13/08/2026 ═══
    #:
    #: `_ghi_so_luot_trang` đếm "lượt chạy không ra sản phẩm" để đỗ những mã có
    #: việc hỏng vĩnh viễn. Đúng ý định, nhưng nó KHÔNG phân biệt được hai thứ
    #: hoàn toàn khác nhau:
    #:
    #:   * mã hỏng thật  -> đỗ là đúng, chạy lại bao nhiêu cũng vậy
    #:   * HẠ TẦNG ngả   -> nhà máy chết vài phút, mọi mã đều trắng
    #:
    #: Đo hôm nay: nhà máy ảnh bị giết oan mỗi 5 phút (lỗi ở supervisor, đã
    #: sửa). Trong quãng đó **15/16 mã** ăn đủ 3 lượt trắng và bị đỗ. Hạ tầng
    #: khoẻ lại thì không mã nào tự chạy — server rảnh với trần 979 job ảnh,
    #: còn tool thì đứng im, và người vận hành thấy "0,8 ảnh/phút".
    #:
    #: Một sự cố 10 phút biến thành đứng máy vô hạn, chỉ gỡ được bằng cách mở
    #: lại tool. Đó là cái giá quá đắt cho một phép đếm không biết mình đang
    #: đếm gì.
    #:
    #: 15 phút: đủ dài để mã hỏng thật không quay lại tranh chỗ liên tục, đủ
    #: ngắn để một sự cố hạ tầng tự lành mà không cần ai đụng vào.
    HAN_DO_LAI_GIAY = 15 * 60

    def _ma_bi_do_lai(self, code):
        kho = getattr(self, "_luot_trang", {}) or {}
        if kho.get(code, 0) < self.LUOT_TRANG_TOI_DA:
            return False
        moc = (getattr(self, "_do_lai_luc", {}) or {}).get(code)
        if moc is None:
            return True
        if time.time() - moc < self.HAN_DO_LAI_GIAY:
            return True
        # Hết hạn đỗ -> xoá sổ, cho chạy lại MỘT lượt để tự chứng minh.
        # Còn hỏng thật thì nó lại đủ 3 lượt trắng và đỗ tiếp, tốn đúng 3 lượt
        # mỗi 15 phút — rẻ hơn nhiều so với đứng im cả buổi.
        try:
            kho.pop(code, None)
            (getattr(self, "_do_lai_luc", {}) or {}).pop(code, None)
            self._log(f"[QUEUE/VE3] {code}: hết hạn đỗ lại ({self.HAN_DO_LAI_GIAY//60} phút) "
                      f"-> cho chạy lại để tự chứng minh. Hạ tầng ngả rồi khoẻ thì mã tự "
                      f"quay lại, không phải mở lại tool.", "INFO", "ve3")
        except Exception:
            pass
        return False

    def _queue_ve3_loop(self, cfg):
        try:
            while not self.queue_stop_requested:
                did_work = False
                # POOL MODE (veo3top_b_pool): ảnh + video ĐỀU đi qua pool (image_factory 8789 + video pool), KHÔNG cần
                # server sv# tạo gì. -> BỎ rà soát server cũ (ping accepting/chrome_ready, chờ bound server) để mã VÀO
                # LÀM NGAY với account enabled. (Trước đây gate server làm treo tới 5 phút dù pool sẵn sàng.)
                _pool_mode = str(cfg.get("generation_backend", "") or "").strip() == "veo3top_b_pool"
                # API shopapi: y hệt lý do trên — không có server sv# nào tạo gì,
                # nên rà trạng thái server chỉ tổ trễ mã vào làm. Pair ở đây là
                # chỗ làm ẢO (xem `_pair_ao_shopapi`), luôn `available`.
                if self._chi_dung_shopapi(cfg):
                    pairs = [p for p in self._get_server_pairs(only_available=False) if p.get("enabled")]
                elif _pool_mode:
                    pairs = [p for p in self._get_server_pairs(only_available=False) if p.get("enabled")]
                else:
                    if (_time.time() - float(getattr(self, "server_status_cache_ts", 0.0) or 0.0)) >= 10:
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
                    # Mã đã ĐỖ LẠI: chạy mấy lượt liền không ra sản phẩm nào.
                    # Bỏ qua ở đây là chỗ duy nhất chặn được nó tranh pair —
                    # xem `_ghi_so_luot_trang`.
                    if self._ma_bi_do_lai(pd.name):
                        self._queue_ve3_skip_log(pd.name, "da_do_lai",
                                                 "chay nhieu luot khong ra san pham")
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
                        # File mo GHI duoc (khong bi OS khoa) nhung con marker ~$/.lock/.tmp -> STALE (sot lai tu
                        # crash) -> DON de khoi ket va khoi ban Excel. Worker Excel khong chay -> an toan xoa.
                        if ep.exists():
                            for _stale in (ep.with_name(f"~${ep.name}"),
                                           ep.with_suffix(".xlsx.lock"), ep.with_suffix(".xlsx.tmp")):
                                try:
                                    if _stale.exists():
                                        _stale.unlink()
                                        self._log(f"[QUEUE/EXCEL] {pd.name}: xoa marker khoa STALE {_stale.name} (file mo duoc)", "WARN", "excel")
                                except Exception:
                                    pass

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
                    # === TÁCH 2 TRẠM (chỉ backend pool): mã cần ẢNH -> image-only (nhả slot sớm, làm đầy pool 96);
                    #     ảnh XONG cần VIDEO -> video-only (finalize). Backend khác -> 'all' (nguyên khối như cũ). ===
                    _run_mode = "all"; _stage = "all"
                    if _pool_mode:
                        if self._project_needs_image(pd):
                            _run_mode = "image-only"; _stage = "image"
                        elif self._project_needs_video(pd):
                            _run_mode = "video-only"; _stage = "video"
                        else:
                            self._queue_ve3_skip_log(pd.name, "no_stage")
                            continue
                    # GIỚI HẠN SỐ MÃ SONG SONG — TÁCH theo trạm: trạm ẢNH nhiều (đầy pool 96), trạm VIDEO ít (10 Ultra).
                    # max_concurrent_codes (mode all) = 0 -> không giới hạn. Đếm active THEO ĐÚNG trạm (queue_ve3_stage).
                    if _stage == "image":
                        _ic = int(cfg.get("max_concurrent_image_codes", 0) or 0)
                        _maxcodes = _ic if _ic > 0 else self._compute_pool_capacity()["img_codes"]   # 0 = TỰ TÍNH theo nhân sự
                    elif _stage == "video":
                        _vc = int(cfg.get("max_concurrent_video_codes", 0) or 0)
                        _maxcodes = _vc if _vc > 0 else self._compute_pool_capacity()["vid_codes"]
                    else:
                        _maxcodes = int(cfg.get("max_concurrent_codes", 0) or 0)
                    if _maxcodes > 0:
                        with self.queue_lock:
                            _active = sum(1 for _c, _t in self.queue_ve3_tasks.items()
                                          if _t.is_alive() and self.queue_ve3_stage.get(_c, "all") == _stage)
                        if _active >= _maxcodes and pd.name not in self.queue_active_ve3:
                            self._queue_ve3_skip_log(pd.name, "max_codes", f"{_stage} {_active}/{_maxcodes} mã")
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
                        self.queue_ve3_stage[pd.name] = _stage   # trạm đang chạy mã (image/video/all) -> đếm concurrency đúng trạm
                        self.queue_active_pairs[pair["pair_id"]] = pd.name
                        self.queue_pair_use_seq += 1
                        self.queue_pair_last_used[pair["pair_id"]] = self.queue_pair_use_seq
                    self._write_queue_marker(pd, "ve3", f"VE3 [{_stage}] using pair {pair['server_name']} / {pair['flow_account_name']}")
                    task = threading.Thread(target=self._run_single_project_ve3, args=(pd, pair, cfg, _run_mode), daemon=True)
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
        if getattr(self, "_closing", False):
            return
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
                self.queue_ve3_stage.clear()
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
        if self._t0 and self.btn_st.cget("state")!="disabled" and not getattr(self, "_closing", False):
            self.lbl_tm.configure(text=_ts(_time.time()-self._t0))
            old = getattr(self, "_tick_timer_id", None)
            if old is not None:
                self.after_cancel(old)
            self._tick_timer_id = self.after(1000, self._tick)

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

    def _import_excel(self):
        """Tai 1 Excel co san (<code>_prompts.xlsx) vao PROJECTS de tao anh + video.
        Copy ca thu muc nv/ (anh nv1) di kem; queue se tu nhan dien va chay."""
        import shutil
        path = filedialog.askopenfilename(
            title="Chon Excel co san (*_prompts.xlsx)",
            filetypes=[("Excel prompts", "*_prompts.xlsx"), ("Excel", "*.xlsx"), ("All", "*.*")],
        )
        if not path:
            return
        try:
            src_xlsx = Path(path)
            code = src_xlsx.stem
            if code.endswith("_prompts"):
                code = code[:-len("_prompts")]
            code = code.strip()
            if not code:
                messagebox.showerror("Tải Excel", "Không lấy được mã từ tên file.")
                return
            src_dir = src_xlsx.parent
            dst_dir = PROJECTS_DIR / code
            # Neu chon dung file da nam trong PROJECTS/<code> thi khong can copy
            if src_dir.resolve() == dst_dir.resolve():
                self._log(f"[IMPORT] {code} da o trong PROJECTS — san sang chay.", "SUCCESS", "ve3")
            else:
                dst_dir.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src_xlsx, dst_dir / f"{code}_prompts.xlsx")
                copied = ["Excel"]
                # nv/ (anh tham chieu) — bat buoc de tao anh
                if (src_dir / "nv").exists():
                    shutil.copytree(src_dir / "nv", dst_dir / "nv", dirs_exist_ok=True)
                    copied.append("nv/")
                # cac file phu (khong bat buoc cho generation)
                for extra in (f"{code}.srt", ".nguon_runtime_metadata.yaml"):
                    if (src_dir / extra).exists():
                        shutil.copy2(src_dir / extra, dst_dir / extra)
                self._log(f"[IMPORT] Đã nạp {code} vào PROJECTS/{code} ({', '.join(copied)})", "SUCCESS", "ve3")
            # Neu chua co nv/nv1.png, doc LINK trong cot image_file (sheet characters)
            # va copy anh do ve nv/nv1.png — dung khi user dien duong dan anh vao Excel.
            nv_png = dst_dir / "nv" / "nv1.png"
            if not nv_png.exists():
                try:
                    from modules.excel_manager import PromptWorkbook
                    _wb = PromptWorkbook(str(dst_dir / f"{code}_prompts.xlsx")); _wb.load_or_create()
                    for _ch in _wb.get_characters():
                        if str(_ch.id).strip().lower() != "nv1":
                            continue
                        link = str(getattr(_ch, "image_file", "") or "").strip()
                        if link and link not in (".", ".."):
                            cand = Path(link)
                            if not cand.is_absolute():
                                cand = src_dir / link
                            if cand.exists() and cand.is_file():
                                nv_png.parent.mkdir(parents=True, exist_ok=True)
                                shutil.copy2(cand, nv_png)
                                self._log(f"[IMPORT] Copy anh nhan vat tu link: {cand} -> nv/nv1.png", "SUCCESS", "ve3")
                        break
                except Exception as _e:
                    self._log(f"[IMPORT] Khong copy duoc anh tu image_file: {_e}", "WARN", "ve3")
            has_nv = (dst_dir / "nv" / "nv1.png").exists()
            msg = (f"Đã nạp mã {code} vào PROJECTS.\n"
                   f"{'✓' if has_nv else '⚠ THIẾU'} ảnh nv/nv1.png\n\n"
                   f"Bấm RUN để tạo ảnh + video.")
            if has_nv:
                messagebox.showinfo("Tải Excel", msg)
            else:
                messagebox.showwarning("Tải Excel",
                                       msg + "\n\n(Nếu image_file trong Excel là đường dẫn đầy đủ tới ảnh thì vẫn chạy được.)")
        except Exception as e:
            messagebox.showerror("Tải Excel", f"Lỗi nạp Excel: {e}")
            self._log(f"[IMPORT] Loi nap Excel: {e}", "ERROR", "ve3")

    def _log(self, m, l="INFO", channel=None):
        ghi_log_file(m, l, channel)
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

        # Deduplicate consecutive identical messages
        deduped = []
        repeat_count = 0
        last_key = None
        for m, l, channel in batch:
            msg_str = str(m).strip()
            code_m = _RE_PROJECT_CODE.search(msg_str)
            code_id = (code_m.group(1) or code_m.group(2)) if code_m else ""
            key = (msg_str, l, channel, code_id)
            if key == last_key:
                repeat_count += 1
            else:
                if repeat_count > 0:
                    deduped.append((f"  (...repeated {repeat_count}x)", "WARN", deduped[-1][2] if deduped else "ve3"))
                repeat_count = 0
                last_key = key
                deduped.append((m, l, channel))
        if repeat_count > 0:
            deduped.append((f"  (...repeated {repeat_count}x)", "WARN", deduped[-1][2] if deduped else "ve3"))

        # Limit batch size to prevent GUI freeze
        if len(deduped) > 60:
            deduped = deduped[-60:]

        records = []
        for m, l, channel in deduped:
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


