#!/usr/bin/env python3
"""
IPv6 Pool Manager GUI - Giao dien quan ly IPv6 pool.
=====================================================

Chay:
    python -m ipv6.ipv6_gui

Tu dong:
    - Ket noi MikroTik khi mo
    - Start API server khi ket noi thanh cong
    - Refresh trang thai moi 5 giay
"""

import sys
import json
import time
import threading
from pathlib import Path

import tkinter as tk
from tkinter import ttk, messagebox, scrolledtext

# Add parent dir to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from ipv6.mikrotik_api import MikroTikAPI
from ipv6.ipv6_pool import IPv6Pool
from ipv6.ipv6_server import start_api_server, stop_api_server, is_running as api_is_running, set_pool
from ipv6 import create_pool

CONFIG_FILE = Path(__file__).parent / "config_test.json"

# === DARK THEME ===
BG = "#1e1e2e"
BG2 = "#2a2a3a"
BG_CARD = "#313144"
FG = "#e0e0e0"
FG_DIM = "#888"
GREEN = "#4caf50"
RED = "#e53935"
BLUE = "#42a5f5"
ORANGE = "#ff9800"
YELLOW = "#fdd835"


class IPv6PoolGUI:
    def __init__(self, root):
        self.root = root
        self.root.title("IPv6 Pool Manager")
        self.root.geometry("900x780")
        self.root.resizable(True, True)
        self.root.configure(bg=BG)

        self.pool = None
        self.config = {}
        self.connected = False
        self._api_started = False

        self._build_ui()
        # Auto: load config → connect → start API
        self.root.after(300, self._auto_start)

    # =========================================================================
    # UI
    # =========================================================================

    def _build_ui(self):
        # === HEADER: Status bar ===
        header = tk.Frame(self.root, bg=BG2, pady=8)
        header.pack(fill="x")

        self.lbl_title = tk.Label(header, text="IPv6 POOL", font=("Segoe UI", 14, "bold"),
                                  bg=BG2, fg=FG)
        self.lbl_title.pack(side="left", padx=12)

        # Connection + API status indicators
        self.lbl_conn = tk.Label(header, text=" ROUTER: ... ", font=("Consolas", 10, "bold"),
                                 bg="#555", fg="white", padx=8)
        self.lbl_conn.pack(side="left", padx=4)

        self.lbl_api = tk.Label(header, text=" API: ... ", font=("Consolas", 10, "bold"),
                                bg="#555", fg="white", padx=8)
        self.lbl_api.pack(side="left", padx=4)

        # Config button (nhỏ, góc phải)
        tk.Button(header, text="Config", font=("Consolas", 9),
                  bg=BG_CARD, fg=FG, relief="flat", padx=8,
                  command=self._show_config).pack(side="right", padx=8)

        # === BIG STATS (4 ô lớn) ===
        stats = tk.Frame(self.root, bg=BG)
        stats.pack(fill="x", padx=8, pady=6)

        self.stat_widgets = {}
        stat_defs = [
            ("available", "SAN SANG", GREEN),
            ("in_use", "DANG DUNG", BLUE),
            ("burned", "DA BURN", RED),
            ("total_pool", "TONG POOL", FG_DIM),
        ]
        for key, label, color in stat_defs:
            f = tk.Frame(stats, bg=BG_CARD, padx=16, pady=8)
            f.pack(side="left", fill="x", expand=True, padx=4)
            tk.Label(f, text=label, font=("Consolas", 9), bg=BG_CARD, fg=FG_DIM).pack()
            num_lbl = tk.Label(f, text="0", font=("Segoe UI", 24, "bold"), bg=BG_CARD, fg=color)
            num_lbl.pack()
            self.stat_widgets[key] = num_lbl

        # === API STATS (1 dòng) ===
        api_stats_frame = tk.Frame(self.root, bg=BG2, pady=4)
        api_stats_frame.pack(fill="x", padx=8, pady=(0, 4))

        tk.Label(api_stats_frame, text="API:", font=("Consolas", 9, "bold"),
                 bg=BG2, fg=FG_DIM).pack(side="left", padx=(8, 4))

        self.api_stat_labels = {}
        for key, label, color in [
            ("get", "Lay", GREEN), ("rotate", "Doi", ORANGE),
            ("burn", "Burn", RED), ("release", "Tra", BLUE),
        ]:
            tk.Label(api_stats_frame, text=f"{label}:", font=("Consolas", 9),
                     bg=BG2, fg=FG_DIM).pack(side="left", padx=(8, 0))
            lbl = tk.Label(api_stats_frame, text="0", font=("Consolas", 10, "bold"),
                           bg=BG2, fg=color)
            lbl.pack(side="left", padx=(2, 0))
            self.api_stat_labels[key] = lbl

        # === WORKERS PANEL (may nao dang dung IP nao) ===
        workers_frame = tk.Frame(self.root, bg=BG_CARD, pady=4, padx=8)
        workers_frame.pack(fill="x", padx=8, pady=(0, 4))

        tk.Label(workers_frame, text="WORKERS:", font=("Consolas", 9, "bold"),
                 bg=BG_CARD, fg=FG_DIM).pack(side="left", padx=(0, 8))
        self.workers_container = tk.Frame(workers_frame, bg=BG_CARD)
        self.workers_container.pack(side="left", fill="x", expand=True)
        self.lbl_no_worker = tk.Label(self.workers_container, text="Chua co worker nao ket noi",
                                       font=("Consolas", 9), bg=BG_CARD, fg=FG_DIM)
        self.lbl_no_worker.pack(side="left")

        # === IP LIST (Treeview) ===
        list_frame = tk.Frame(self.root, bg=BG)
        list_frame.pack(fill="both", expand=True, padx=8, pady=4)

        # Treeview with dark style
        style = ttk.Style()
        style.theme_use("default")
        style.configure("Dark.Treeview", background=BG_CARD, foreground=FG,
                         fieldbackground=BG_CARD, font=("Consolas", 9))
        style.configure("Dark.Treeview.Heading", background=BG2, foreground=FG,
                         font=("Segoe UI", 9, "bold"))
        style.map("Dark.Treeview", background=[("selected", BLUE)])

        cols = ("address", "subnet", "gateway", "status", "uses", "last_used")
        self.tree = ttk.Treeview(list_frame, columns=cols, show="headings",
                                 height=14, style="Dark.Treeview")
        self.tree.heading("address", text="IPv6 Address")
        self.tree.heading("subnet", text="Subnet")
        self.tree.heading("gateway", text="Gateway")
        self.tree.heading("status", text="Trang thai")
        self.tree.heading("uses", text="Dung")
        self.tree.heading("last_used", text="Lan cuoi")

        self.tree.column("address", width=300)
        self.tree.column("subnet", width=60, anchor="center")
        self.tree.column("gateway", width=180)
        self.tree.column("status", width=90, anchor="center")
        self.tree.column("uses", width=50, anchor="center")
        self.tree.column("last_used", width=120, anchor="center")

        scrollbar = ttk.Scrollbar(list_frame, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=scrollbar.set)

        self.tree.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")

        # Tag colors
        self.tree.tag_configure("available", background="#1b3a1b")
        self.tree.tag_configure("in_use", background="#1b2a3a")
        self.tree.tag_configure("burned", background="#3a1b1b")

        # === BUTTONS (đơn giản, 1 dòng) ===
        btn_frame = tk.Frame(self.root, bg=BG, pady=4)
        btn_frame.pack(fill="x", padx=8)

        btn_style = {"font": ("Segoe UI", 10, "bold"), "relief": "flat",
                     "padx": 14, "pady": 6, "cursor": "hand2"}

        tk.Button(btn_frame, text="DOI TAT CA IP", bg=ORANGE, fg="white",
                  command=self._on_rotate_all, **btn_style).pack(side="left", padx=4)
        tk.Button(btn_frame, text="RESET POOL", bg=RED, fg="white",
                  command=self._on_reset, **btn_style).pack(side="left", padx=4)
        tk.Button(btn_frame, text="Them IP", bg=BG_CARD, fg=FG,
                  command=self._on_refill, **btn_style).pack(side="left", padx=4)

        # Right side: IP-specific actions
        tk.Button(btn_frame, text="Test IP", bg=BG_CARD, fg=FG,
                  command=self._on_test_ip, **btn_style).pack(side="right", padx=4)
        tk.Button(btn_frame, text="Burn IP", bg=BG_CARD, fg=RED,
                  command=self._on_burn_ip, **btn_style).pack(side="right", padx=4)
        tk.Button(btn_frame, text="Doi IP", bg=BG_CARD, fg=ORANGE,
                  command=self._on_rotate_ip, **btn_style).pack(side="right", padx=4)

        # === LOG ===
        log_frame = tk.Frame(self.root, bg=BG)
        log_frame.pack(fill="x", padx=8, pady=(4, 8))

        self.log_text = scrolledtext.ScrolledText(log_frame, height=5,
                                                   font=("Consolas", 9),
                                                   bg=BG_CARD, fg=FG,
                                                   insertbackground=FG,
                                                   selectbackground=BLUE)
        self.log_text.pack(fill="x")
        self.log_text.configure(state="disabled")

    # =========================================================================
    # AUTO START
    # =========================================================================

    def _auto_start(self):
        """Auto: load config → connect → start API."""
        if not CONFIG_FILE.exists():
            self._log("Chua co config! An 'Config' de nhap thong tin router.")
            self._update_conn_status(False, "Chua config")
            return

        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                self.config = json.load(f)
            self._log("Config loaded")
        except Exception as e:
            self._log(f"Config error: {e}")
            self._update_conn_status(False, "Config loi")
            return

        # Auto connect
        self._update_conn_status(None, "Dang ket noi...")
        threading.Thread(target=self._do_auto_connect, daemon=True).start()

    def _do_auto_connect(self):
        """Connect + init pool + start API (background thread)."""
        try:
            self.pool = create_pool(self.config, log_func=self._log)
            ok = self.pool.api.test_connection()

            if ok:
                self.connected = True
                host = self.config.get("mikrotik", {}).get("host", "?")
                self.pool.init()
                self.root.after(0, lambda: self._update_conn_status(True, host))
                self.root.after(0, self._refresh_ui)
                self._log(f"Ket noi OK: {host}")

                # Auto start API
                self.root.after(500, self._auto_start_api)
            else:
                self.connected = False
                self.root.after(0, lambda: self._update_conn_status(False, "That bai"))
                self._log("Ket noi router that bai!")
        except Exception as e:
            self.connected = False
            self.root.after(0, lambda: self._update_conn_status(False, "Loi"))
            self._log(f"Loi ket noi: {e}")

    def _auto_start_api(self):
        """Auto start API server."""
        if not self.connected or not self.pool:
            return
        if api_is_running():
            # v1.0.680: Cap nhat log_func cho server dang chay
            set_pool(self.pool, self._log)
            self._update_api_status(True)
            self._start_auto_refresh()
            self._log("API Server dang chay - da cap nhat log")
            return

        port = self.config.get("mikrotik", {}).get("api_port", 8765)
        ok = start_api_server(self.pool, host="0.0.0.0", port=port, log_func=self._log)
        if ok:
            self._update_api_status(True, port)
            self._log(f"API Server started: port {port}")
            self._start_auto_refresh()
        else:
            self._update_api_status(False)
            self._log("API Server start that bai!")

    def _start_auto_refresh(self):
        """Refresh stats moi 5 giay - v1.0.630: thread-safe, khong block GUI."""
        def _tick():
            if self.pool:
                # v1.0.630: Lay data trong thread rieng de tranh block GUI khi pool._lock bi giu
                def _fetch_and_update():
                    try:
                        status = self.pool.get_status()
                        pool_entries = list(self.pool.pool)  # Copy nhanh
                        from ipv6.ipv6_server import get_api_stats
                        api_stats = get_api_stats()
                        # Update UI tren main thread
                        self.root.after(0, lambda: self._update_ui_with_data(status, pool_entries, api_stats))
                    except Exception:
                        pass
                threading.Thread(target=_fetch_and_update, daemon=True).start()
            self.root.after(5000, _tick)
        self.root.after(5000, _tick)

    # =========================================================================
    # STATUS INDICATORS
    # =========================================================================

    def _update_conn_status(self, ok, text=""):
        if ok is None:
            self.lbl_conn.config(text=f" ROUTER: {text} ", bg="#666")
        elif ok:
            self.lbl_conn.config(text=f" ROUTER: {text} ", bg=GREEN)
        else:
            self.lbl_conn.config(text=f" ROUTER: {text} ", bg=RED)

    def _update_api_status(self, ok, port=None):
        if ok:
            import socket
            try:
                s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                s.connect(("192.168.88.1", 80))
                local_ip = s.getsockname()[0]
                s.close()
            except Exception:
                local_ip = "localhost"
            port = port or 8765
            self.lbl_api.config(text=f" API: {local_ip}:{port} ", bg=GREEN)
            self._api_started = True
        else:
            self.lbl_api.config(text=" API: OFF ", bg=RED)
            self._api_started = False

    # =========================================================================
    # ACTIONS
    # =========================================================================

    def _on_rotate_all(self):
        if not self._check_connected():
            return
        total = self.pool.api.subnet_end - self.pool.api.subnet_start + 1
        ok = messagebox.askyesno("Xac nhan",
            f"Doi TAT CA {total} IP?\n"
            "Xoa IP cu + tao IP moi (subnet random).\n"
            "Mat khoang 1-2 phut.")
        if not ok:
            return

        def do():
            self._log(f"Dang doi tat ca IP...")
            added = self.pool.rotate_all()
            self._log(f"Da doi xong: {added} IP moi")
            self.root.after(0, self._refresh_ui)

        threading.Thread(target=do, daemon=True).start()

    def _on_reset(self):
        if not self._check_connected():
            return
        ok = messagebox.askyesno("Xac nhan",
            "Reset pool tracking?\n"
            "IP giu tren router, chi xoa burned/in_use tracking.\n"
            "Pool bat dau lai tu dau.")
        if not ok:
            return

        self.pool.reset_pool()
        self.pool._load_all_from_router()
        self._refresh_ui()
        self._log("Da reset pool")

    def _on_refill(self):
        if not self._check_connected():
            return
        def do():
            added = self.pool._refill(count=5)
            self._log(f"Da them {added} IP vao pool")
            self.root.after(0, self._refresh_ui)
        threading.Thread(target=do, daemon=True).start()

    def _on_burn_ip(self):
        if not self._check_connected():
            return
        entry = self._get_selected_entry()
        if not entry:
            return
        def do():
            self.pool.burn_ip(entry["address"], reason="manual_burn")
            self.root.after(0, self._refresh_ui)
        threading.Thread(target=do, daemon=True).start()

    def _on_rotate_ip(self):
        if not self._check_connected():
            return
        entry = self._get_selected_entry()
        if not entry:
            return
        if entry["status"] not in ("in_use", "available"):
            messagebox.showinfo("Thong bao", "Chon IP 'San sang' hoac 'Dang dung' de doi")
            return
        def do():
            new_ip = self.pool.rotate_ip(entry["address"], reason="manual_rotate")
            if new_ip:
                self._log(f"Doi: → {new_ip[0]}")
            else:
                self._log("Doi that bai - het IP!")
            self.root.after(0, self._refresh_ui)
        threading.Thread(target=do, daemon=True).start()

    def _on_test_ip(self):
        if not self._check_connected():
            return
        entry = self._get_selected_entry()
        if not entry:
            return
        addr = entry["address"]
        self._log(f"Test {addr}...")
        def do():
            ok = self.pool.api.test_ipv6_connectivity(addr)
            if ok:
                self._log(f"TEST OK: {addr}")
            else:
                self._log(f"TEST FAIL: {addr}")
        threading.Thread(target=do, daemon=True).start()

    def _show_config(self):
        """Dialog chinh config router."""
        win = tk.Toplevel(self.root)
        win.title("Config Router")
        win.geometry("400x300")
        win.configure(bg=BG)
        win.transient(self.root)

        mk = self.config.get("mikrotik", {})
        fields = [
            ("Host:", "host", mk.get("host", "192.168.88.1")),
            ("User:", "username", mk.get("username", "admin")),
            ("Password:", "password", mk.get("password", "")),
            ("Interface:", "interface", mk.get("interface", "bridge")),
            ("Prefix:", "prefix", mk.get("prefix", "")),
            ("Subnet start:", "subnet_start", str(mk.get("subnet_start", 102))),
            ("Subnet end:", "subnet_end", str(mk.get("subnet_end", 255))),
            ("API port:", "api_port", str(mk.get("api_port", 8765))),
        ]

        vars_ = {}
        for i, (label, key, val) in enumerate(fields):
            tk.Label(win, text=label, font=("Consolas", 10), bg=BG, fg=FG,
                     anchor="w", width=14).grid(row=i, column=0, padx=8, pady=2, sticky="w")
            v = tk.StringVar(value=val)
            show = "*" if key == "password" else ""
            tk.Entry(win, textvariable=v, width=28, bg=BG2, fg=FG,
                     insertbackground=FG, font=("Consolas", 10),
                     show=show).grid(row=i, column=1, padx=8, pady=2)
            vars_[key] = v

        def save():
            cfg = {"mikrotik": {}}
            for key, v in vars_.items():
                val = v.get()
                if key in ("subnet_start", "subnet_end", "api_port"):
                    val = int(val)
                cfg["mikrotik"][key] = val
            try:
                with open(CONFIG_FILE, "w", encoding="utf-8") as f:
                    json.dump(cfg, f, indent=2)
                self.config = cfg
                self._log("Config saved! Restart de ap dung.")
                win.destroy()
            except Exception as e:
                self._log(f"Save error: {e}")

        tk.Button(win, text="LUU + DONG", bg=GREEN, fg="white",
                  font=("Segoe UI", 10, "bold"), relief="flat",
                  padx=20, command=save).grid(row=len(fields), column=0,
                  columnspan=2, pady=12)

    # =========================================================================
    # HELPERS
    # =========================================================================

    def _check_connected(self) -> bool:
        if not self.connected or not self.pool:
            messagebox.showwarning("Chua ket noi", "Hay ket noi MikroTik truoc!\nAn 'Config' de nhap thong tin.")
            return False
        return True

    def _get_selected_entry(self):
        sel = self.tree.selection()
        if not sel:
            messagebox.showinfo("Thong bao", "Chon 1 IP trong danh sach truoc!")
            return None
        item = self.tree.item(sel[0])
        addr = item["values"][0]
        for entry in self.pool.pool:
            if entry["address"] == addr:
                return entry
        return None

    def _update_ui_with_data(self, status, pool_entries, api_stats):
        """v1.0.630: Cap nhat UI voi data da fetch san (thread-safe, chi chay tren main thread)."""
        try:
            # Stats
            self.stat_widgets["available"].config(text=str(status["available"]))
            self.stat_widgets["in_use"].config(text=str(status["in_use"]))
            self.stat_widgets["burned"].config(text=str(status.get("burned_total", 0)))
            self.stat_widgets["total_pool"].config(text=str(status.get("range_total", 0)))

            # API Stats
            if api_stats:
                self.api_stat_labels["get"].config(text=str(api_stats.get("total_get", 0)))
                self.api_stat_labels["rotate"].config(text=str(api_stats.get("total_rotate", 0)))
                self.api_stat_labels["burn"].config(text=str(api_stats.get("total_burn", 0)))
                self.api_stat_labels["release"].config(text=str(api_stats.get("total_release", 0)))

                # Workers panel
                workers = api_stats.get("workers", {})
                for w in self.workers_container.winfo_children():
                    w.destroy()

                if workers:
                    for wname, wdata in workers.items():
                        ip = wdata.get("current_ip", "")
                        ip_short = ip[-22:] if len(ip) > 22 else ip
                        gets = wdata.get("get", 0)
                        rots = wdata.get("rotate", 0)
                        burns = wdata.get("burn", 0)

                        wf = tk.Frame(self.workers_container, bg=BG2, padx=6, pady=2)
                        wf.pack(side="left", padx=3)
                        tk.Label(wf, text=wname, font=("Consolas", 9, "bold"),
                                 bg=BG2, fg=YELLOW).pack(side="left")
                        tk.Label(wf, text=f" {ip_short}", font=("Consolas", 8),
                                 bg=BG2, fg=GREEN).pack(side="left")
                        tk.Label(wf, text=f" G:{gets} R:{rots} B:{burns}",
                                 font=("Consolas", 8), bg=BG2, fg=FG_DIM).pack(side="left")
                else:
                    tk.Label(self.workers_container, text="Chua co worker nao ket noi",
                             font=("Consolas", 9), bg=BG_CARD, fg=FG_DIM).pack(side="left")

            # API status indicator
            if api_is_running():
                if not self._api_started:
                    self._update_api_status(True)
            else:
                if self._api_started:
                    self._update_api_status(False)

            # Tree
            self.tree.delete(*self.tree.get_children())
            for entry in pool_entries:
                addr = entry.get("address", "?")
                subnet = entry.get("subnet_hex", "?")
                gateway = entry.get("gateway", "")
                st = entry.get("status", "?")
                uses = entry.get("use_count", 0)
                last = ""
                if entry.get("used_at"):
                    last = time.strftime("%H:%M:%S %d/%m", time.localtime(entry["used_at"]))
                elif entry.get("created_at"):
                    last = time.strftime("%H:%M:%S %d/%m", time.localtime(entry["created_at"]))

                st_display = {"available": "San sang", "in_use": "Dang dung", "burned": "Da burn"}.get(st, st)
                tag = st if st in ("available", "in_use", "burned") else ""
                self.tree.insert("", "end", values=(addr, subnet, gateway, st_display, uses, last), tags=(tag,))
        except Exception:
            pass

    def _refresh_ui(self):
        """Cap nhat giao dien (goi truc tiep - dung cho action callbacks)."""
        if not self.pool:
            return

        try:
            status = self.pool.get_status()
            pool_entries = list(self.pool.pool)
            from ipv6.ipv6_server import get_api_stats
            api_stats = get_api_stats()
            self._update_ui_with_data(status, pool_entries, api_stats)
        except Exception:
            pass

    def _log(self, msg):
        """Ghi log (thread-safe)."""
        def do():
            self.log_text.configure(state="normal")
            timestamp = time.strftime("%H:%M:%S")
            clean_msg = msg.replace("[POOL] ", "").replace("[MikroTik] ", "")
            self.log_text.insert("end", f"[{timestamp}] {clean_msg}\n")
            self.log_text.see("end")
            lines = int(self.log_text.index("end-1c").split(".")[0])
            if lines > 200:
                self.log_text.delete("1.0", f"{lines - 200}.0")
            self.log_text.configure(state="disabled")

        if threading.current_thread() is threading.main_thread():
            do()
        else:
            self.root.after(0, do)


def _kill_old_instances():
    """
    v1.0.681: Tat process Pool GUI cu con chay an.
    Neu khong tat → process cu giu port 8765 → GUI moi khong start API duoc
    → log khong hien, hoac dung server cu voi log_func = print().
    """
    import os
    import subprocess

    my_pid = os.getpid()
    try:
        # Tim tat ca python process dang chay ipv6_gui hoac ipv6_server
        result = subprocess.run(
            ["wmic", "process", "where",
             "name like '%python%'",
             "get", "processid,commandline"],
            capture_output=True, text=True, timeout=10
        )
        for line in result.stdout.splitlines():
            line_lower = line.lower()
            if "ipv6_gui" in line_lower or "ipv6.ipv6_gui" in line_lower:
                # Extract PID (so cuoi cung tren dong)
                parts = line.strip().split()
                if parts:
                    try:
                        pid = int(parts[-1])
                        if pid != my_pid:
                            os.kill(pid, 9)
                            print(f"[CLEANUP] Killed old ipv6_gui process: PID {pid}")
                    except (ValueError, OSError):
                        pass
    except Exception as e:
        print(f"[CLEANUP] Skip: {e}")


def main():
    _kill_old_instances()
    root = tk.Tk()
    app = IPv6PoolGUI(root)
    root.mainloop()


if __name__ == "__main__":
    main()
