"""
image_pool_gui — GUI QUAN LY account nha may (2 TAB: ANH va VIDEO). Nhan tieng Viet khong dau.
Moi tab: bang trang thai rieng (state file rieng) + Prepare(song song) / Probe / Kiem tra / Reset /
Check cookie / Them-Sua TK. Chay: python image_pool_gui.py  (hoac nut trong ve3 settings).
"""
import os, sys, threading, time, subprocess
_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)
import tkinter as tk
from tkinter import ttk, messagebox
import image_pool_manager as M
import image_pool_browser as P

try:
    import requests
except Exception:
    requests = None

_COLOR = {"good": "#d7f7d7", "ready": "#d7e8ff", "bad": "#ffe2c2", "dead": "#ffd6d6", "unknown": "#f0f0f0"}
_STATES = ["Tat ca", "good", "ready", "bad", "dead", "unknown"]


def kill_pool_chromes():
    """Kill MOI chrome nha may (profile anh + video) khi dong GUI -> khong orphan."""
    try:
        subprocess.run(["powershell", "-NoProfile", "-Command",
                        "Get-CimInstance Win32_Process -Filter \"Name='chrome.exe'\" | "
                        "Where-Object { $_.CommandLine -match 'pool_img_profiles|pool_vid_profiles' } | "
                        "ForEach-Object { Stop-Process -Id $_.ProcessId -Force -EA SilentlyContinue }"],
                       capture_output=True, timeout=20, creationflags=0x08000000)
    except Exception:
        pass


class PoolTab(tk.Frame):
    def __init__(self, parent, name, sf, port, is_video=False, acct_loader=None, profile_base=None,
                 check_port=9550, upsert_fn=None, logfile=None):
        super().__init__(parent)
        self.name = name; self.sf = sf; self.port = port; self.is_video = is_video
        self.acct_loader = acct_loader; self.profile_base = profile_base; self.check_port = check_port
        self.upsert_fn = upsert_fn or M.db_upsert_account
        self.logfile = logfile; self._logpos = None
        self._busy = False; self._rows_cache = []

        r1 = tk.Frame(self); r1.pack(fill="x", padx=8, pady=(6, 2))
        tk.Button(r1, text="Lam moi", command=self.refresh, width=8).pack(side="left")
        tk.Button(r1, text="Check cookie (nhanh)", command=self.do_checkcookies).pack(side="left", padx=4)
        tk.Label(r1, text="  Tim:").pack(side="left")
        self.search = tk.Entry(r1, width=22); self.search.pack(side="left")
        self.search.bind("<KeyRelease>", lambda e: self._render())
        tk.Label(r1, text="  Loc:").pack(side="left")
        self.filter_state = tk.StringVar(value="Tat ca")
        tk.OptionMenu(r1, self.filter_state, *_STATES, command=lambda e: self._render()).pack(side="left")
        tk.Button(r1, text="Them TK", command=self.do_add).pack(side="left", padx=(12, 2))
        tk.Button(r1, text="Sua TK (chon)", command=self.do_edit).pack(side="left", padx=2)

        r2 = tk.Frame(self); r2.pack(fill="x", padx=8, pady=2)
        tk.Label(r2, text="Chuan bi = login HET account thieu cookie  |  Luong:").pack(side="left")
        self.conc = tk.Entry(r2, width=4); self.conc.insert(0, str(M.PREP_CONCURRENCY)); self.conc.pack(side="left")
        self.hide_var = tk.IntVar(value=1)
        tk.Checkbutton(r2, text="An Chrome", variable=self.hide_var, command=self._apply_hide).pack(side="left", padx=6)
        tk.Button(r2, text="Chuan bi", command=self.do_prepare, width=9).pack(side="left", padx=3)
        if not is_video:
            tk.Button(r2, text="Chuan bi + Probe", command=lambda: self.do_prepare(True)).pack(side="left", padx=3)
            tk.Button(r2, text="Probe (chon)", command=self.do_probe).pack(side="left", padx=6)
        tk.Button(r2, text="Kiem tra - mo Chrome (chon)", command=self.do_check).pack(side="left", padx=3)
        tk.Button(r2, text="Reset (chon)", command=self.do_reset).pack(side="left", padx=6)

        self.summary = tk.Label(self, text="", anchor="w", font=("Consolas", 10)); self.summary.pack(fill="x", padx=8)

        cols = ("email", "state", "cookie", "reason", "project", "cooldown", "last", "creds")
        self.tree = ttk.Treeview(self, columns=cols, show="headings", selectmode="extended")
        w = {"email": 280, "state": 78, "cookie": 90, "reason": 200, "project": 84, "cooldown": 70, "last": 100, "creds": 70}
        heads = {"email": "EMAIL", "state": "TRANG THAI", "cookie": "COOKIE", "reason": "LY DO",
                 "project": "PROJECT", "cooldown": "NGHI", "last": "LAN CUOI", "creds": "PASS/TOTP"}
        for c in cols:
            self.tree.heading(c, text=heads[c]); self.tree.column(c, width=w[c], anchor="w")
        for st, col in _COLOR.items():
            self.tree.tag_configure(st, background=col)
        self.tree.pack(fill="both", expand=True, padx=8, pady=4)
        self.tree.bind("<Double-1>", lambda e: self.do_edit())

        tk.Label(self, text="Log thao tac (prepare/check):", anchor="w").pack(fill="x", padx=8)
        self.logbox = tk.Text(self, height=5, font=("Consolas", 9)); self.logbox.pack(fill="x", padx=8, pady=(0, 4))
        tk.Label(self, text=f"Log DICH VU (service {port} - login/warm/generate/loi):", anchor="w").pack(fill="x", padx=8)
        self.svbox = tk.Text(self, height=8, font=("Consolas", 9), bg="#101418", fg="#a8e6a8")
        self.svbox.pack(fill="x", padx=8, pady=(0, 8))

        self._apply_hide(); self.refresh(); self._auto(); self._tail_log()

    def _tail_log(self):
        """Doc log service (file) va do vao o Log DICH VU -> theo doi pool live."""
        try:
            if self.logfile and os.path.exists(self.logfile):
                sz = os.path.getsize(self.logfile)
                if self._logpos is None:
                    self._logpos = max(0, sz - 6000)   # lan dau: show ~6KB cuoi
                if sz < self._logpos:
                    self._logpos = 0                    # file bi ghi lai (rotate) -> doc lai
                if sz > self._logpos:
                    with open(self.logfile, "r", encoding="utf-8", errors="replace") as f:
                        f.seek(self._logpos); chunk = f.read(); self._logpos = f.tell()
                    if chunk:
                        self.svbox.insert("end", chunk); self.svbox.see("end")
                        # cat bot neu qua dai
                        if int(self.svbox.index("end-1c").split(".")[0]) > 500:
                            self.svbox.delete("1.0", "200.0")
        except Exception:
            pass
        self.after(2000, self._tail_log)

    def log(self, m):
        try: self.after(0, self._log_main, str(m))
        except Exception: pass

    def _log_main(self, m):
        self.logbox.insert("end", f"{time.strftime('%H:%M:%S')} {m}\n"); self.logbox.see("end")

    def _health(self):
        now = time.time()
        if now - getattr(self, "_health_ts", 0) < 5:
            return getattr(self, "_health_cache", None)
        h = None
        if requests is not None:
            try: h = requests.get(f"http://127.0.0.1:{self.port}/health", timeout=1).json()
            except Exception: h = None
        self._health_cache = h; self._health_ts = now
        return h

    def refresh(self):
        self._rows_cache = M.status_rows(self.sf, self.acct_loader)
        self._render()

    def _render(self):
        q = self.search.get().strip().lower(); fs = self.filter_state.get()
        self.tree.delete(*self.tree.get_children()); shown = 0
        for r in self._rows_cache:
            if q and q not in r["email"].lower(): continue
            if fs != "Tat ca" and r["state"] != fs: continue
            creds = ("pass" if r["has_pass"] else "-") + "/" + ("totp" if r["has_totp"] else "-")
            cd = f"{r['cooldown_min']}p" if r["cooldown_min"] else ""
            self.tree.insert("", "end", iid=r["email"],
                             values=(r["email"], r["state"], r.get("cookie", ""), r["reason"],
                                     r["project"], cd, r["last"], creds), tags=(r["state"],))
            shown += 1
        cnt = M.status_summary(self.sf, self.acct_loader); h = self._health(); hs = ""
        if h:
            hs = (f"  |  SERVICE {self.port}: slots={h.get('slots')} queue={h.get('queue')} "
                  f"done={h.get('done')} fail={h.get('fail')}")
        self.summary.config(text=f"[{self.name}] Hien: {shown}  |  TONG: "
                            + ", ".join(f"{k}={v}" for k, v in sorted(cnt.items())) + hs)

    def _auto(self):
        if not self._busy:
            try: self.refresh()
            except Exception: pass
        self.after(8000, self._auto)

    def _run_bg(self, fn, name):
        if self._busy:
            messagebox.showinfo("Dang ban", "Dang chay tac vu khac, cho xong."); return
        self._busy = True; self.log(f">> {name}...")
        def _w():
            try: fn()
            except Exception as e: self.log(f"[x] {name} loi: {e}")
            finally:
                self._busy = False; self.log(f"[v] {name} xong."); self.after(0, self.refresh)
        threading.Thread(target=_w, daemon=True).start()

    def _sel(self): return list(self.tree.selection())

    def _find_acc(self, email):
        """Tim account trong NGUON cua tab nay (anh=169 / video=10) -> dung creds dung pool."""
        loader = self.acct_loader or P.load_gmail_accounts
        for a in loader():
            if a["email"].lower() == email.lower(): return a
        return M._find(email)

    def _apply_hide(self):
        os.environ["VEO3TOP_HIDE_CHROME"] = "1" if self.hide_var.get() else "0"

    def do_checkcookies(self):
        self._run_bg(lambda: M.checkcookies_parallel(log=self.log, on_done=None, sf=self.sf,
                     acct_loader=self.acct_loader), "Check cookie")

    def do_prepare(self, probe=False):
        try: conc = max(1, int(self.conc.get()))
        except Exception: conc = M.PREP_CONCURRENCY
        self._apply_hide()
        def _f():
            todo = M.select_todo(None, self.sf, self.acct_loader)   # None = HET account thieu cookie (khong gioi han)
            self.log(f"chuan bi {len(todo)} account thieu cookie, {min(conc, len(todo) or 1)} luong "
                     f"(probe={probe}, an={'co' if self.hide_var.get() else 'khong'})")
            M.prepare_parallel(todo, probe=probe, concurrency=conc, log=lambda m: None, sf=self.sf,
                               profile_base=self.profile_base,
                               on_done=lambda e, r: (self.log(f"  {e} -> {r}"), self.after(0, self.refresh)))
        self._run_bg(_f, f"Chuan bi ALL (//{conc})" + ("+Probe" if probe else ""))

    def do_probe(self):
        emails = self._sel()
        if not emails: messagebox.showinfo("Chon account", "Chon account trong bang."); return
        def _f():
            for e in emails:
                a = self._find_acc(e)
                if a: self.log(f"  probe {e} -> {M._prepare_one(0, a['email'], a['password'], a['totp'], probe=True, sf=self.sf, profile_base=self.profile_base)}")
                self.after(0, self.refresh)
        self._run_bg(_f, f"Probe {len(emails)}")

    def do_check(self):
        emails = self._sel()
        if not emails: messagebox.showinfo("Chon account", "Chon 1 account de mo Chrome."); return
        e = emails[0]; a = self._find_acc(e)
        if not a: return
        def _f():
            from cdp_chrome import ChromeCDP
            acc = P.Account(0, a["email"], a["password"], a["totp"], profile_base=self.profile_base)
            acc._start_ipv6(); proxy = acc.ipv6.proxy_url() if acc.ipv6 else None
            os.makedirs(acc.profile, exist_ok=True)
            self.log(f"mo Chrome (HIEN) {e} - dang nhap/kiem tra thu cong roi tat cua so Chrome do")
            c = ChromeCDP(P._CHROME, acc.profile, self.check_port, offscreen=False, log=lambda *_: None, proxy=proxy)
            c.connect(launch_timeout=45)
            while True:
                time.sleep(3)
                if not c._page_ws(): break
            try: acc.ipv6.stop()
            except Exception: pass
        self._run_bg(_f, f"Kiem tra {e}")

    def do_reset(self):
        emails = self._sel()
        if not emails: return
        s = M._load_state(self.sf)
        for e in emails: s.pop(e, None)
        M._save_state(s, self.sf); self.log(f"reset {len(emails)} account"); self.refresh()

    def do_add(self): self._edit_dialog(None)

    def do_edit(self):
        emails = self._sel()
        if not emails: messagebox.showinfo("Chon account", "Chon 1 account de sua."); return
        self._edit_dialog(emails[0])

    def _edit_dialog(self, email):
        pw = tt = ""
        if email:
            a = self._find_acc(email)   # doc creds dung nguon cua tab (anh=DB / video=vid_accounts.txt)
            if a: pw = a.get("password", ""); tt = a.get("totp", "")
        win = tk.Toplevel(self.winfo_toplevel()); win.title("Sua TK" if email else "Them TK moi"); win.geometry("470x210")
        win.transient(self.winfo_toplevel()); win.grab_set()
        tk.Label(win, text="Email:").grid(row=0, column=0, sticky="e", padx=6, pady=6)
        e_email = tk.Entry(win, width=44); e_email.grid(row=0, column=1, padx=6)
        if email: e_email.insert(0, email); e_email.config(state="readonly")
        tk.Label(win, text="Password:").grid(row=1, column=0, sticky="e", padx=6, pady=6)
        e_pw = tk.Entry(win, width=44); e_pw.insert(0, pw); e_pw.grid(row=1, column=1, padx=6)
        tk.Label(win, text="TOTP secret:").grid(row=2, column=0, sticky="e", padx=6, pady=6)
        e_tt = tk.Entry(win, width=44); e_tt.insert(0, tt); e_tt.grid(row=2, column=1, padx=6)
        tk.Label(win, text="(Hoac dan 1 dong: email|password|totp vao o Email)", fg="#888").grid(row=3, column=1, sticky="w", padx=6)
        def _save():
            em = e_email.get().strip(); p = e_pw.get().strip(); t = e_tt.get().strip()
            if "|" in em and not email:
                parts = em.split("|"); em = parts[0].strip()
                if len(parts) > 1 and not p: p = parts[1].strip()
                if len(parts) > 2 and not t: t = parts[2].strip()
            ok, msg = self.upsert_fn(em, p, t)
            self.log(f"{em}: {msg if ok else 'LOI: ' + msg}")
            if ok: win.destroy(); self.refresh()
            else: messagebox.showerror("Loi", msg)
        tk.Button(win, text="Luu", width=12, command=_save).grid(row=4, column=1, sticky="e", padx=6, pady=10)


def main():
    root = tk.Tk()
    root.title("VE3 - Quan ly account (ANH / VIDEO)")
    root.geometry("1140x700")
    nb = ttk.Notebook(root); nb.pack(fill="both", expand=True)
    vid_state = getattr(M, "VID_STATE", os.path.join(P.SUITE, ".veo3top_vidpool_state.json"))
    import video_pool_browser as VB
    tab_img = PoolTab(nb, "ANH", M.IMG_STATE, 8789, is_video=False,
                      acct_loader=P.load_gmail_accounts, profile_base=P.POOL_PROFILES,
                      check_port=9550, upsert_fn=M.db_upsert_account,
                      logfile=os.path.join(P.SUITE, ".veo3top_imgpool.log"))
    tab_vid = PoolTab(nb, "VIDEO", vid_state, 8790, is_video=True,
                      acct_loader=VB.load_video_accounts, profile_base=P.POOL_VID_PROFILES,
                      check_port=9551, upsert_fn=VB.upsert_video_account,
                      logfile=os.path.join(P.SUITE, ".veo3top_pool.log"))
    nb.add(tab_img, text="  Tai khoan ANH  ")
    nb.add(tab_vid, text="  Tai khoan VIDEO  ")
    def _on_close():
        try: kill_pool_chromes()
        except Exception: pass
        try: root.destroy()
        except Exception: pass
    root.protocol("WM_DELETE_WINDOW", _on_close)
    root.mainloop()


if __name__ == "__main__":
    main()
