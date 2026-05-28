"""
FlowKit Server GUI — Setup, monitor, and manage FlowKit instances.

Run: python flowkit_gui.py
  or: START_FLOWKIT_GUI.bat
"""
import sys
import os
import json
import shutil
import subprocess
import threading
import time
import tkinter as tk
from tkinter import ttk, scrolledtext
from pathlib import Path

sys.stdout.reconfigure(encoding='utf-8', errors='replace')

BASE_DIR = Path(__file__).parent
TOOL_DIR = BASE_DIR.parent  # server/ (or Documents/ on standalone VM)
SUITE_ROOT = TOOL_DIR.parent  # VE3_SUITE root (or user home on standalone VM)
_IS_STANDALONE = not (TOOL_DIR / "google_login.py").exists() and (BASE_DIR / "google_login.py").exists()
LOG_DIR = BASE_DIR / "logs"
sys.path.insert(0, str(BASE_DIR))
if not _IS_STANDALONE:
    sys.path.insert(0, str(TOOL_DIR))

GITHUB_REPO = "manhthang1905-hub/VE3_SUITE"
GITHUB_ZIP_URL = f"https://github.com/{GITHUB_REPO}/archive/refs/heads/main.zip"
GITHUB_GIT_URL = f"https://github.com/{GITHUB_REPO}.git"

# Colors (dark theme — matches server_gui.py)
BG = '#0f172a'
BG2 = '#1e293b'
FG = '#e2e8f0'
FG2 = '#94a3b8'
BLUE = '#38bdf8'
GREEN = '#22c55e'
ORANGE = '#f97316'
RED = '#ef4444'
YELLOW = '#eab308'
BORDER = '#334155'

SETTINGS_FILE = BASE_DIR / "config" / "flowkit_gui.json"

PROTECTED_PATHS = {
    "chrome_profiles",
    "config/settings.yaml",
    "config/flowkit_gui.json",
    "config/flow_accounts.yaml",
    ".claude",
}


def _get_auto_version() -> str:
    if not _IS_STANDALONE:
        try:
            result = subprocess.run(
                ["git", "rev-list", "--count", "HEAD"],
                cwd=str(SUITE_ROOT), capture_output=True, text=True, timeout=5,
            )
            if result.returncode == 0:
                ver = f"1.0.{result.stdout.strip()}"
                try:
                    (BASE_DIR / "VERSION.txt").write_text(ver + "\n", encoding="utf-8")
                except Exception:
                    pass
                return ver
        except Exception:
            pass
    for vf in (BASE_DIR / "VERSION.txt", BASE_DIR / "VERSION"):
        try:
            if vf.exists():
                txt = vf.read_text(encoding="utf-8-sig").split("\n")[0].strip()
                if txt:
                    return txt
        except Exception:
            pass
    return "?"


class FlowKitGUI(tk.Tk):
    def __init__(self):
        super().__init__()
        self._version = _get_auto_version()
        self._remote_version = ""
        self.title(f"FlowKit Server v{self._version}")
        gui_w = 700
        try:
            gui_h = self.winfo_screenheight() - 40
        except Exception:
            gui_h = 1040
        self.geometry(f"{gui_w}x{gui_h}+0+0")
        self.configure(bg=BG)
        self.resizable(True, True)
        self.minsize(600, 500)

        self._started = False
        self._processes = []
        self._log_handles = []
        self._logs = []
        self._workers = []
        self._stats = {}
        self._poll_thread = None

        self.protocol("WM_DELETE_WINDOW", self._on_close)

        self._notebook = ttk.Notebook(self)
        self._notebook.pack(fill='both', expand=True)

        style = ttk.Style()
        style.theme_use('clam')
        style.configure('TNotebook', background=BG, borderwidth=0)
        style.configure('TNotebook.Tab', background=BG2, foreground=FG,
                        padding=[10, 4], font=('Segoe UI', 9))
        style.map('TNotebook.Tab',
                  background=[('selected', BLUE)],
                  foreground=[('selected', '#000')])

        self._setup_frame = tk.Frame(self._notebook, bg=BG)
        self._monitor_frame = tk.Frame(self._notebook, bg=BG)
        self._notebook.add(self._setup_frame, text="  Setup  ")
        self._notebook.add(self._monitor_frame, text="  Monitor  ")

        self._build_setup_page()
        self._build_monitor_page()
        self._load_settings()
        self._detect_chromes()

    # ============================================================
    # Setup Page
    # ============================================================
    def _build_setup_page(self):
        p = self._setup_frame

        # Header row: title + version + update button
        hdr = tk.Frame(p, bg=BG)
        hdr.pack(fill='x', padx=10, pady=(8, 4))
        tk.Label(hdr, text="FlowKit Server", font=('Segoe UI', 14, 'bold'),
                 fg=BLUE, bg=BG).pack(side='left')
        self._version_label = tk.Label(hdr, text=f"v{self._version}", font=('Consolas', 9),
                 fg=FG2, bg=BG)
        self._version_label.pack(side='left', padx=(6, 0))

        self._update_btn = tk.Button(
            hdr, text="Check Update", command=self._on_check_update,
            bg='#0984e3', fg='#fff', font=('Segoe UI', 9, 'bold'),
            relief='flat', cursor='hand2', padx=10)
        self._update_btn.pack(side='right')

        # ── Gateway + 403 Rotation (same row) ──
        row_top = tk.Frame(p, bg=BG)
        row_top.pack(fill='x', padx=10, pady=2)

        gw = tk.LabelFrame(row_top, text=" Gateway ", bg=BG2, fg=FG2,
                           font=('Segoe UI', 8, 'bold'), bd=1, relief='groove')
        gw.pack(side='left', fill='x', expand=True, padx=(0, 4))
        gw_row = tk.Frame(gw, bg=BG2)
        gw_row.pack(fill='x', padx=6, pady=4)
        tk.Label(gw_row, text="Port:", bg=BG2, fg=FG, font=('Segoe UI', 9)).pack(side='left')
        self._gateway_port = tk.Entry(gw_row, width=7, bg=BG, fg=FG, insertbackground=FG,
                                      font=('Consolas', 10), bd=1, relief='solid')
        self._gateway_port.pack(side='left', padx=4)
        self._gateway_port.insert(0, "5100")

        rot = tk.LabelFrame(row_top, text=" 403 Rotation ", bg=BG2, fg=FG2,
                            font=('Segoe UI', 8, 'bold'), bd=1, relief='groove')
        rot.pack(side='left', fill='x', expand=True, padx=(4, 0))
        rot_row = tk.Frame(rot, bg=BG2)
        rot_row.pack(fill='x', padx=6, pady=4)
        tk.Label(rot_row, text="Max:", bg=BG2, fg=FG, font=('Segoe UI', 9)).pack(side='left')
        self._max_403 = tk.Entry(rot_row, width=4, bg=BG, fg=FG, insertbackground=FG,
                                 font=('Consolas', 10), bd=1, relief='solid')
        self._max_403.pack(side='left', padx=4)
        self._max_403.insert(0, "3")
        tk.Label(rot_row, text="Cool:", bg=BG2, fg=FG, font=('Segoe UI', 9)).pack(side='left', padx=(8, 0))
        self._cooldown = tk.Entry(rot_row, width=5, bg=BG, fg=FG, insertbackground=FG,
                                  font=('Consolas', 10), bd=1, relief='solid')
        self._cooldown.pack(side='left', padx=4)
        self._cooldown.insert(0, "300")

        # ── IPv6 Pool ──
        f = tk.LabelFrame(p, text=" IPv6 Pool ", bg=BG2, fg=FG2,
                          font=('Segoe UI', 8, 'bold'), bd=1, relief='groove')
        f.pack(fill='x', padx=10, pady=2)
        ipv6_row = tk.Frame(f, bg=BG2)
        ipv6_row.pack(fill='x', padx=6, pady=4)
        self._ipv6_enabled = tk.BooleanVar(value=False)
        tk.Checkbutton(ipv6_row, text="Enable", variable=self._ipv6_enabled,
                       bg=BG2, fg=FG, selectcolor=BG, activebackground=BG2,
                       font=('Segoe UI', 9)).pack(side='left')
        tk.Label(ipv6_row, text="URL:", bg=BG2, fg=FG, font=('Segoe UI', 9)).pack(side='left', padx=(8, 0))
        self._pool_url = tk.Entry(ipv6_row, width=30, bg=BG, fg=FG, insertbackground=FG,
                                  font=('Consolas', 9), bd=1, relief='solid')
        self._pool_url.pack(side='left', padx=4, fill='x', expand=True)
        self._pool_url.insert(0, "http://192.168.88.146:8765")
        btn = tk.Button(ipv6_row, text="TEST", command=self._test_pool, bg='#334155',
                        fg=FG, font=('Segoe UI', 8, 'bold'), relief='flat', cursor='hand2')
        btn.pack(side='left', padx=4)
        self._pool_status = tk.Label(f, text="", bg=BG2, fg=FG2, font=('Segoe UI', 8))
        self._pool_status.pack(padx=6, pady=(0, 2), anchor='w')

        # ── Chrome Instances ──
        f = tk.LabelFrame(p, text=" Chrome ", bg=BG2, fg=FG2,
                          font=('Segoe UI', 8, 'bold'), bd=1, relief='groove')
        f.pack(fill='x', padx=10, pady=2)
        self._chrome_info = tk.Label(f, text="Dang quet...", bg=BG2, fg=FG,
                                     font=('Segoe UI', 9))
        self._chrome_info.pack(padx=6, pady=3, anchor='w')

        # ── Google Accounts ──
        f = tk.LabelFrame(p, text=" Tai khoan Google (email|password|2fa_secret) ",
                          bg=BG2, fg=FG2, font=('Segoe UI', 8, 'bold'), bd=1, relief='groove')
        f.pack(fill='x', padx=10, pady=2)
        self._accounts_text = scrolledtext.ScrolledText(
            f, width=60, height=4, bg=BG, fg=FG, insertbackground=FG,
            font=('Consolas', 9), bd=1, relief='solid', wrap='none')
        self._accounts_text.pack(padx=6, pady=4, fill='x')

        # ── Buttons ──
        btn_frame = tk.Frame(p, bg=BG)
        btn_frame.pack(fill='x', padx=10, pady=(6, 8))

        self._start_btn = tk.Button(
            btn_frame, text="START FLOWKIT", command=self._on_start,
            bg=GREEN, fg='#000', font=('Segoe UI', 12, 'bold'),
            relief='flat', cursor='hand2', height=1)
        self._start_btn.pack(fill='x', pady=2)

        self._stop_btn = tk.Button(
            btn_frame, text="STOP", command=self._on_stop,
            bg=RED, fg='#fff', font=('Segoe UI', 10, 'bold'),
            relief='flat', cursor='hand2', state='disabled')
        self._stop_btn.pack(fill='x', pady=2)

    # ============================================================
    # Monitor Page
    # ============================================================
    def _build_monitor_page(self):
        p = self._monitor_frame

        # Stats row
        stats_f = tk.Frame(p, bg=BG)
        stats_f.pack(fill='x', padx=8, pady=4)

        self._stat_labels = {}
        for col, (key, label, color) in enumerate([
            ('available', 'Available', GREEN),
            ('completed', 'Done', BLUE),
            ('failed', 'Failed', RED),
            ('cooling', 'Cooling', ORANGE),
        ]):
            box = tk.Frame(stats_f, bg=BG2, bd=1, relief='solid')
            box.pack(side='left', fill='x', expand=True, padx=2)
            val = tk.Label(box, text="0", font=('Segoe UI', 18, 'bold'),
                           fg=color, bg=BG2)
            val.pack(pady=(4, 0))
            tk.Label(box, text=label, font=('Segoe UI', 8), fg=FG2, bg=BG2).pack(pady=(0, 4))
            self._stat_labels[key] = val

        # Workers grid
        wf = tk.LabelFrame(p, text=" Workers ", bg=BG2, fg=FG2,
                           font=('Segoe UI', 8, 'bold'), bd=1, relief='groove')
        wf.pack(fill='x', padx=8, pady=2)
        self._workers_frame = tk.Frame(wf, bg=BG2)
        self._workers_frame.pack(fill='x', padx=4, pady=4)
        self._worker_cards = {}

        # Logs
        lf = tk.LabelFrame(p, text=" Logs ", bg=BG2, fg=FG2,
                           font=('Segoe UI', 8, 'bold'), bd=1, relief='groove')
        lf.pack(fill='both', expand=True, padx=8, pady=2)
        self._log_text = scrolledtext.ScrolledText(
            lf, bg=BG, fg=FG2, font=('Consolas', 8),
            bd=0, wrap='word', state='disabled')
        self._log_text.pack(fill='both', expand=True, padx=2, pady=2)
        self._log_text.tag_config('OK', foreground=GREEN)
        self._log_text.tag_config('WARN', foreground=YELLOW)
        self._log_text.tag_config('ERROR', foreground=RED)
        self._log_text.tag_config('INFO', foreground=FG2)

    # ============================================================
    # Chrome Detection
    # ============================================================
    def _detect_chromes(self):
        """Find Chrome Portable copies in flowkit directory."""
        self._chrome_dirs = []
        for d in sorted(BASE_DIR.iterdir()):
            if d.is_dir() and 'GoogleChromePortable' in d.name:
                portable = d / 'GoogleChromePortable.exe'
                if portable.exists():
                    self._chrome_dirs.append(d)
        count = len(self._chrome_dirs)
        names = ', '.join(d.name for d in self._chrome_dirs[:5])
        if count > 5:
            names += f'... (+{count - 5})'
        self._chrome_info.config(text=f"Tim thay {count} Chrome: {names}" if count else "Khong tim thay Chrome Portable!")

    # ============================================================
    # Settings
    # ============================================================
    def _load_settings(self):
        if not SETTINGS_FILE.exists():
            return
        try:
            data = json.loads(SETTINGS_FILE.read_text(encoding='utf-8'))
            self._gateway_port.delete(0, 'end')
            self._gateway_port.insert(0, str(data.get('gateway_port', 5100)))
            self._ipv6_enabled.set(data.get('ipv6_enabled', False))
            self._pool_url.delete(0, 'end')
            self._pool_url.insert(0, data.get('pool_url', 'http://192.168.88.146:8765'))
            self._max_403.delete(0, 'end')
            self._max_403.insert(0, str(data.get('max_403', 3)))
            self._cooldown.delete(0, 'end')
            self._cooldown.insert(0, str(data.get('cooldown', 300)))
            accounts = data.get('accounts', '')
            if accounts:
                self._accounts_text.delete('1.0', 'end')
                self._accounts_text.insert('1.0', accounts)
        except Exception:
            pass

    def _save_settings(self):
        SETTINGS_FILE.parent.mkdir(parents=True, exist_ok=True)
        data = {
            'gateway_port': int(self._gateway_port.get() or 5100),
            'ipv6_enabled': self._ipv6_enabled.get(),
            'pool_url': self._pool_url.get().strip(),
            'max_403': int(self._max_403.get() or 3),
            'cooldown': int(self._cooldown.get() or 300),
            'accounts': self._accounts_text.get('1.0', 'end').strip(),
        }
        SETTINGS_FILE.write_text(json.dumps(data, indent=2), encoding='utf-8')

    # ============================================================
    # IPv6 Pool Test
    # ============================================================
    def _test_pool(self):
        url = self._pool_url.get().strip()
        self._pool_status.config(text="Dang ket noi...", fg=YELLOW)
        self.update()

        def _do():
            try:
                from ipv6_pool_client import IPv6PoolClient
                client = IPv6PoolClient(url, timeout=5)
                status = client.get_status()
                if status:
                    avail = status.get('available', '?')
                    total = status.get('total', '?')
                    self._pool_status.config(text=f"OK - Available: {avail}/{total}", fg=GREEN)
                else:
                    self._pool_status.config(text="Khong ket noi duoc!", fg=RED)
            except Exception as e:
                self._pool_status.config(text=f"Loi: {e}", fg=RED)

        threading.Thread(target=_do, daemon=True).start()

    # ============================================================
    # Start / Stop
    # ============================================================
    def _on_start(self):
        if self._started:
            return
        self._save_settings()
        self._started = True
        self._start_btn.config(state='disabled', bg='#475569')
        self._stop_btn.config(state='normal')
        self._notebook.select(self._monitor_frame)

        self._log("Starting FlowKit Server...", "INFO")

        # Update config.yaml with GUI settings
        self._update_config()

        # Parse accounts
        accounts = self._parse_accounts()

        threading.Thread(target=self._start_all, args=(accounts,), daemon=True).start()

    def _update_config(self):
        """Update config.yaml with current GUI settings."""
        import yaml
        config_path = BASE_DIR / "config.yaml"
        with open(config_path) as f:
            config = yaml.safe_load(f)

        config['gateway_port'] = int(self._gateway_port.get() or 5100)
        config['rotation']['max_consecutive_403'] = int(self._max_403.get() or 3)
        config['rotation']['cooldown_seconds'] = int(self._cooldown.get() or 300)

        ipv6_cfg = config.get('ipv6', {})
        ipv6_cfg['enabled'] = self._ipv6_enabled.get()
        ipv6_cfg['pool_url'] = self._pool_url.get().strip()
        config['ipv6'] = ipv6_cfg

        # Update instance count based on detected Chromes
        instances = []
        for i, chrome_dir in enumerate(self._chrome_dirs):
            instances.append({
                'name': f'flowkit-{i + 1}',
                'chrome_path': f'{chrome_dir.name}/App/Chrome-bin/chrome.exe',
                'profile_dir': f'{chrome_dir.name}/Data/profile',
                'extension_dir': f'flowkit_extensions/ext_{8100 + i}',
                'api_port': 8100 + i,
                'ws_port': 9222 + i,
                'enabled': True,
            })
        config['instances'] = instances

        with open(config_path, 'w') as f:
            yaml.dump(config, f, default_flow_style=False, allow_unicode=True)

    def _parse_accounts(self) -> list:
        """Parse accounts from text field. Format: email|password|2fa_secret"""
        raw = self._accounts_text.get('1.0', 'end').strip()
        accounts = []
        for line in raw.split('\n'):
            line = line.strip()
            if not line or line.startswith('#'):
                continue
            parts = line.split('|')
            if len(parts) >= 2:
                acc = {
                    'email': parts[0].strip(),
                    'password': parts[1].strip(),
                    'totp_secret': parts[2].strip() if len(parts) > 2 else '',
                }
                accounts.append(acc)
        return accounts

    def _start_all(self, accounts: list):
        """Start Chrome instances + agents + gateway in background."""
        import yaml
        config_path = BASE_DIR / "config.yaml"
        with open(config_path) as f:
            config = yaml.safe_load(f)

        instances = config.get('instances', [])
        gateway_port = config.get('gateway_port', 5100)
        use_ipv6 = config.get('ipv6', {}).get('enabled', False)
        pool_url = config.get('ipv6', {}).get('pool_url', '')

        LOG_DIR.mkdir(exist_ok=True)

        # IPv6 Pool Client
        pool_client = None
        if use_ipv6 and pool_url:
            try:
                from ipv6_pool_client import IPv6PoolClient
                pool_client = IPv6PoolClient(pool_url, log_func=lambda m: self._log(m, "INFO"))
                if pool_client.ping():
                    self._log(f"IPv6 Pool connected: {pool_url}", "OK")
                else:
                    self._log(f"IPv6 Pool unreachable: {pool_url}", "WARN")
                    pool_client = None
            except Exception as e:
                self._log(f"IPv6 Pool error: {e}", "ERROR")

        # Get IPv6 addresses and start per-instance SOCKS5 proxies
        ipv6_map = {}
        proxy_port_map = {}
        PROXY_BASE_PORT = 1081
        if pool_client:
            self._log("Getting IPv6 addresses from pool...", "INFO")
            for i, inst in enumerate(instances):
                if not inst.get('enabled', True) or i >= len(self._chrome_dirs):
                    continue
                result = pool_client.get_ip(worker=f"flowkit_{inst['name']}")
                if result:
                    ipv6_map[i] = {
                        'ip': result['ip'],
                        'gateway': result.get('gateway', ''),
                    }
                    self._log(f"[{inst['name']}] Got IPv6: {result['ip']}", "OK")

            if ipv6_map:
                self._log("Setting up SOCKS5 proxies for each IPv6...", "INFO")
                self._setup_ipv6_proxies(ipv6_map, instances, PROXY_BASE_PORT, proxy_port_map)

        # Phase 1: Login accounts (skip if no accounts entered)
        if accounts:
            self._log(f"Phase 1: Login {len(accounts)} accounts into {len(instances)} Chrome copies...", "INFO")
            for i, inst in enumerate(instances):
                if not inst.get('enabled', True) or i >= len(self._chrome_dirs):
                    continue
                chrome_dir = self._chrome_dirs[i]
                account = accounts[i % len(accounts)]
                proxy_arg = f"socks5://127.0.0.1:{proxy_port_map[i]}" if i in proxy_port_map else ""
                self._log(f"[{inst['name']}] Login: {account['email']}...", "INFO")
                self._do_chrome_login(chrome_dir, account, inst, proxy_arg)
        else:
            self._log("Phase 1: Skipped login (no accounts configured)", "INFO")

        # Phase 2: Start agents
        self._log("Phase 2: Starting FlowKit agents...", "INFO")
        for i, inst in enumerate(instances):
            if not inst.get('enabled', True) or i >= len(self._chrome_dirs):
                continue
            self._start_agent(inst)
            time.sleep(1)

        time.sleep(3)

        # Phase 3: Start Chrome with extensions
        self._log("Phase 3: Starting Chrome instances...", "INFO")
        for i, inst in enumerate(instances):
            if not inst.get('enabled', True) or i >= len(self._chrome_dirs):
                continue
            proxy_arg = f"socks5://127.0.0.1:{proxy_port_map[i]}" if i in proxy_port_map else ""
            self._start_chrome(self._chrome_dirs[i], inst, proxy_arg)
            time.sleep(2)

        time.sleep(5)

        # Phase 4: Start gateway
        self._log("Phase 4: Starting Gateway...", "INFO")
        self._start_gateway(gateway_port)

        time.sleep(3)
        self._log(f"FlowKit Server READY on port {gateway_port}", "OK")

        # Start monitoring
        self._poll_thread = threading.Thread(target=self._poll_loop, daemon=True)
        self._poll_thread.start()

    def _do_chrome_login(self, chrome_dir: Path, account: dict, inst: dict, proxy_arg: str = ""):
        """Login Google account into Chrome profile using google_login.py."""
        try:
            login_module = BASE_DIR / "google_login.py"
            if not login_module.exists():
                login_module = TOOL_DIR / "google_login.py"
            if not login_module.exists():
                self._log(f"[{inst['name']}] google_login.py not found, skip login", "WARN")
                return

            sys.path.insert(0, str(login_module.parent))
            from google_login import login_google_chrome

            portable = str(chrome_dir / "GoogleChromePortable.exe")
            worker_id = inst['api_port'] - 8100

            account_info = {
                'id': account['email'],
                'password': account['password'],
                'totp_secret': account.get('totp_secret', ''),
            }

            success = login_google_chrome(
                account_info=account_info,
                chrome_portable=portable,
                worker_id=worker_id,
                proxy_arg=proxy_arg,
            )

            if success:
                self._log(f"[{inst['name']}] Login OK: {account['email']}", "OK")
            else:
                self._log(f"[{inst['name']}] Login FAILED: {account['email']}", "ERROR")

        except Exception as e:
            self._log(f"[{inst['name']}] Login error: {e}", "ERROR")

    def _start_chrome(self, chrome_dir: Path, inst: dict, proxy_arg: str = ""):
        """Start Chrome with extension, navigate to Flow page to activate extension."""
        portable = chrome_dir / "GoogleChromePortable.exe"
        ext_dir = BASE_DIR / inst['extension_dir']

        # Clear stale service worker cache to ensure fresh extension loading
        profile_dir = chrome_dir / "Data" / "profile"
        sw_cache = profile_dir / "Default" / "Service Worker"
        if sw_cache.exists():
            try:
                shutil.rmtree(sw_cache)
            except Exception:
                pass

        args = [
            str(portable),
            f"--load-extension={ext_dir}",
            "--disable-background-timer-throttling",
            "--disable-renderer-backgrounding",
            "--disable-backgrounding-occluded-windows",
        ]
        if proxy_arg:
            args.append(f"--proxy-server={proxy_arg}")
        # Open directly to Flow page so extension can capture flow key
        args.append("https://labs.google/fx/tools/flow")

        proc = subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                creationflags=subprocess.CREATE_NEW_PROCESS_GROUP)
        self._processes.append(proc)
        log_extra = f" proxy={proxy_arg}" if proxy_arg else ""
        self._log(f"[{inst['name']}] Chrome started (PID {proc.pid}){log_extra}", "INFO")

    def _setup_ipv6_proxies(self, ipv6_map: dict, instances: list, base_port: int, proxy_port_map: dict):
        """Add IPv6 addresses to interface and start a SOCKS5 proxy per instance.

        ipv6_map: {index: {'ip': '2001:...', 'gateway': '2001:...:1'}}
        """
        try:
            from modules.ipv6_proxy import IPv6SocksProxy
        except ImportError:
            try:
                from ipv6_proxy import IPv6SocksProxy
            except ImportError:
                self._log("ipv6_proxy module not found, cannot start SOCKS5 proxies", "ERROR")
                return

        iface = "Ethernet"
        self._ipv6_proxies = []
        first_gateway = None

        for i, info in ipv6_map.items():
            ipv6 = info['ip']
            gateway = info.get('gateway', '') or self._compute_ipv6_gateway(ipv6)
            name = instances[i]['name'] if i < len(instances) else f"flowkit-{i}"
            port = base_port + i
            if not first_gateway and gateway:
                first_gateway = gateway

            # Add IPv6 to Windows network interface
            cmd = f'netsh interface ipv6 add address "{iface}" {ipv6}'
            try:
                subprocess.run(cmd, shell=True, capture_output=True, timeout=10)
                self._log(f"[{name}] Added {ipv6} to {iface}", "INFO")
            except Exception as e:
                self._log(f"[{name}] netsh add address failed: {e}", "WARN")

            # Start SOCKS5 proxy bound to this IPv6
            proxy = IPv6SocksProxy(
                listen_port=port,
                ipv6_address=ipv6,
                log_func=lambda m, _n=name: self._log(f"[{_n}] {m}", "INFO")
            )
            if proxy.start():
                proxy_port_map[i] = port
                self._ipv6_proxies.append(proxy)
                self._log(f"[{name}] SOCKS5 proxy on 127.0.0.1:{port} → {ipv6}", "OK")
            else:
                self._log(f"[{name}] Failed to start SOCKS5 proxy on port {port}", "ERROR")

        # Add on-link route + default route (once, using first gateway)
        if first_gateway:
            try:
                parts = first_gateway.split(':')
                prefix = ':'.join(parts[:4]) + '::/64'
                subprocess.run(
                    f'netsh interface ipv6 add route {prefix} "{iface}"',
                    shell=True, capture_output=True, timeout=10
                )
                subprocess.run(
                    f'netsh interface ipv6 add route ::/0 "{iface}" {first_gateway}',
                    shell=True, capture_output=True, timeout=10
                )
                self._log(f"Default route ::/0 via {first_gateway}", "OK")
            except Exception as e:
                self._log(f"Route setup error: {e}", "WARN")

        # IPv6 prefix policy: prefer IPv6 over IPv4
        try:
            subprocess.run('netsh interface ipv6 set prefixpolicy ::1/128 50 0', shell=True, capture_output=True, timeout=5)
            subprocess.run('netsh interface ipv6 set prefixpolicy ::/0 40 1', shell=True, capture_output=True, timeout=5)
            subprocess.run('netsh interface ipv6 set prefixpolicy ::ffff:0:0/96 10 4', shell=True, capture_output=True, timeout=5)
        except Exception:
            pass

        # Firewall: allow ICMPv6 NDP
        try:
            subprocess.run('netsh advfirewall firewall add rule name="ICMPv6-NDP-In" dir=in action=allow protocol=icmpv6',
                           shell=True, capture_output=True, timeout=5)
            subprocess.run('netsh advfirewall firewall add rule name="ICMPv6-NDP-Out" dir=out action=allow protocol=icmpv6',
                           shell=True, capture_output=True, timeout=5)
        except Exception:
            pass

        if proxy_port_map:
            self._log(f"Waiting 5s for IPv6 NDP to settle...", "INFO")
            time.sleep(5)

    def _compute_ipv6_gateway(self, ipv6: str) -> str:
        """Compute gateway address from IPv6 (::1 of the /64 prefix)."""
        try:
            import ipaddress
            addr = ipaddress.IPv6Address(ipv6)
            network = ipaddress.IPv6Network(f"{addr}/64", strict=False)
            return str(network.network_address + 1)
        except Exception:
            return ""

    def _start_agent(self, inst: dict):
        """Start FlowKit agent for an instance."""
        env = os.environ.copy()
        env['API_PORT'] = str(inst['api_port'])
        env['WS_PORT'] = str(inst['ws_port'])
        env['INSTANCE_NAME'] = inst['name']

        log_file = LOG_DIR / f"{inst['name']}.log"
        fh = open(log_file, 'a', encoding='utf-8')
        self._log_handles.append(fh)

        proc = subprocess.Popen(
            [sys.executable, "-m", "uvicorn", "agent.main:app",
             "--host", "127.0.0.1", "--port", str(inst['api_port']),
             "--log-level", "info"],
            env=env, cwd=str(BASE_DIR),
            stdout=fh, stderr=fh,
            creationflags=subprocess.CREATE_NEW_PROCESS_GROUP,
        )
        self._processes.append(proc)
        self._log(f"[{inst['name']}] Agent started: port {inst['api_port']} (PID {proc.pid})", "INFO")

    def _start_gateway(self, port: int):
        """Start the gateway."""
        log_file = LOG_DIR / "gateway.log"
        fh = open(log_file, 'a', encoding='utf-8')
        self._log_handles.append(fh)

        proc = subprocess.Popen(
            [sys.executable, "-m", "uvicorn", "gateway:app",
             "--host", "0.0.0.0", "--port", str(port),
             "--log-level", "info"],
            cwd=str(BASE_DIR),
            stdout=fh, stderr=fh,
            creationflags=subprocess.CREATE_NEW_PROCESS_GROUP,
        )
        self._processes.append(proc)
        self._log(f"Gateway started: port {port} (PID {proc.pid})", "INFO")

    def _on_stop(self):
        """Stop all processes."""
        self._log("Stopping FlowKit Server...", "WARN")
        self._started = False

        # Kill processes
        for proc in self._processes:
            try:
                proc.kill()
            except Exception:
                pass
        self._processes.clear()

        # Close log file handles
        for fh in self._log_handles:
            try:
                fh.close()
            except Exception:
                pass
        self._log_handles.clear()

        # Kill Chrome Portable
        try:
            subprocess.run(
                ['wmic', 'process', 'where',
                 "name='chrome.exe' and CommandLine like '%GoogleChromePortable%'",
                 'call', 'terminate'],
                capture_output=True, text=True, timeout=10,
                creationflags=0x08000000,
            )
        except Exception:
            pass

        self._start_btn.config(state='normal', bg=GREEN)
        self._stop_btn.config(state='disabled')
        self._log("FlowKit Server stopped.", "WARN")

    # ============================================================
    # Monitoring
    # ============================================================
    def _poll_loop(self):
        """Poll gateway /api/instances every 3s."""
        import requests as req
        port = int(self._gateway_port.get() or 5100)
        while self._started:
            try:
                r = req.get(f"http://127.0.0.1:{port}/api/instances", timeout=3)
                data = r.json()
                self._workers = data.get('instances', [])

                r2 = req.get(f"http://127.0.0.1:{port}/api/status", timeout=3)
                self._stats = r2.json()

                self.after(0, self._update_monitor)
            except Exception:
                pass
            time.sleep(3)

    def _update_monitor(self):
        """Update monitor UI from polled data."""
        # Stats
        self._stat_labels['available'].config(text=str(self._stats.get('instances_available', 0)))
        self._stat_labels['completed'].config(text=str(self._stats.get('total_completed', 0)))
        self._stat_labels['failed'].config(text=str(self._stats.get('total_failed', 0)))
        self._stat_labels['cooling'].config(text=str(self._stats.get('instances_cooling', 0)))

        # Workers
        for widget in self._workers_frame.winfo_children():
            widget.destroy()

        for i, w in enumerate(self._workers):
            card = tk.Frame(self._workers_frame, bg=BG, bd=1, relief='solid',
                            highlightbackground=BORDER)
            card.pack(side='left', fill='y', padx=4, pady=4, expand=True)

            # Status color
            if w.get('cooling'):
                border_color = ORANGE
                status_text = f"COOLING ({w.get('cooling_remaining', 0)}s)"
                status_fg = ORANGE
            elif w.get('available'):
                border_color = GREEN
                status_text = "READY"
                status_fg = GREEN
            elif w.get('healthy'):
                border_color = YELLOW
                status_text = "BUSY" if w.get('processing', 0) > 0 else "WAIT"
                status_fg = YELLOW
            else:
                border_color = RED
                status_text = "DOWN"
                status_fg = RED

            card.config(highlightbackground=border_color)

            tk.Label(card, text=w.get('name', '?'), font=('Segoe UI', 11, 'bold'),
                     fg=FG, bg=BG).pack(padx=8, pady=(6, 2))
            tk.Label(card, text=status_text, font=('Segoe UI', 9, 'bold'),
                     fg=status_fg, bg=BG).pack()

            ext = "Ext: OK" if w.get('extension_connected') else "Ext: --"
            ext_fg = GREEN if w.get('extension_connected') else RED
            tk.Label(card, text=ext, font=('Consolas', 9), fg=ext_fg, bg=BG).pack()

            key = "Key: OK" if w.get('flow_key_present') else "Key: --"
            key_fg = GREEN if w.get('flow_key_present') else FG2
            tk.Label(card, text=key, font=('Consolas', 9), fg=key_fg, bg=BG).pack()

            tk.Label(card, text=f"403: {w.get('consecutive_403', 0)}",
                     font=('Consolas', 9), fg=FG2, bg=BG).pack()
            tk.Label(card, text=f"Done: {w.get('total_completed', 0)} | Fail: {w.get('total_failed', 0)}",
                     font=('Consolas', 9), fg=FG2, bg=BG).pack(pady=(0, 6))

    # ============================================================
    # Logging
    # ============================================================
    def _log(self, msg: str, level: str = "INFO"):
        ts = time.strftime("%H:%M:%S")
        line = f"[{ts}] [{level}] {msg}"
        print(line)
        self._logs.append(line)

        def _append():
            self._log_text.config(state='normal')
            self._log_text.insert('end', line + '\n', level)
            self._log_text.see('end')
            self._log_text.config(state='disabled')

        try:
            self.after(0, _append)
        except Exception:
            pass

    # ============================================================
    # Update
    # ============================================================

    def _get_remote_version(self) -> str:
        """Get remote version from GitHub (commit count or VERSION file)."""
        import json as _json
        from urllib.request import urlopen, Request
        api_url = f"https://api.github.com/repos/{GITHUB_REPO}/commits?sha=main&per_page=1"
        try:
            req = Request(api_url, headers={"User-Agent": "FlowKit-Updater"})
            with urlopen(req, timeout=10) as resp:
                link = resp.headers.get("Link", "")
                if 'rel="last"' in link:
                    import re
                    m = re.search(r'[&?]page=(\d+)>;\s*rel="last"', link)
                    if m:
                        return f"1.0.{m.group(1)}"
        except Exception:
            pass
        try:
            url = f"https://raw.githubusercontent.com/{GITHUB_REPO}/main/VERSION"
            req = Request(url, headers={"User-Agent": "FlowKit-Updater"})
            with urlopen(req, timeout=10) as resp:
                return resp.read().decode("utf-8").strip()
        except Exception:
            return ""

    def _on_check_update(self):
        self._update_btn.config(text="Checking...", state="disabled", fg='#FFA500')
        threading.Thread(target=self._check_update_thread, daemon=True).start()

    def _check_update_thread(self):
        try:
            local = self._version
            remote = self._get_remote_version()
            self._remote_version = remote
            if not remote:
                self.after(0, lambda: self._update_btn.config(
                    text="Loi ket noi", state="normal", bg=RED, fg='#fff'))
                self.after(5000, lambda: self._update_btn.config(
                    text="Check Update", bg='#0984e3', fg='#fff'))
                return

            has_update = False
            try:
                local_n = int(local.rsplit(".", 1)[-1])
                remote_n = int(remote.rsplit(".", 1)[-1])
                has_update = remote_n > local_n
            except (ValueError, IndexError):
                has_update = remote != local

            if has_update:
                self.after(0, lambda: self._update_btn.config(
                    text=f"Update v{remote}", state="normal",
                    bg='#2E7D32', fg='#fff',
                    command=self._run_update))
                self.after(0, lambda: self._version_label.config(
                    text=f"v{local}  >>  v{remote}", fg='#FFA500'))
            else:
                self.after(0, lambda: self._update_btn.config(
                    text="Da moi nhat", state="normal", bg=GREEN, fg='#fff'))
                self.after(5000, lambda: self._update_btn.config(
                    text="Check Update", bg='#0984e3', fg='#fff'))
        except Exception as e:
            self.after(0, lambda: self._update_btn.config(
                text="Loi", state="normal", bg=RED, fg='#fff'))
            print(f"Check update error: {e}")
            self.after(5000, lambda: self._update_btn.config(
                text="Check Update", bg='#0984e3', fg='#fff'))

    def _run_update(self):
        import urllib.request
        import zipfile

        def do_update():
            self._update_btn.config(state="disabled", text="Dang cap nhat...", bg='#666', fg='#fff')

            try:
                git_available = False
                try:
                    result = subprocess.run(
                        ["git", "--version"],
                        capture_output=True, text=True, timeout=10,
                    )
                    git_available = (result.returncode == 0)
                except Exception:
                    pass

                git_root = SUITE_ROOT if not _IS_STANDALONE else BASE_DIR
                if git_available:
                    result = subprocess.run(
                        ["git", "remote", "get-url", "origin"],
                        cwd=str(git_root), capture_output=True, text=True, timeout=10,
                    )
                    if result.returncode != 0:
                        subprocess.run(
                            ["git", "remote", "add", "origin", GITHUB_GIT_URL],
                            cwd=str(git_root), capture_output=True, timeout=10,
                        )
                    elif GITHUB_GIT_URL not in result.stdout.strip():
                        subprocess.run(
                            ["git", "remote", "set-url", "origin", GITHUB_GIT_URL],
                            cwd=str(git_root), capture_output=True, timeout=10,
                        )

                    for cmd in [
                        ["git", "fetch", "origin", "main"],
                        ["git", "checkout", "main"],
                        ["git", "reset", "--hard", "origin/main"],
                    ]:
                        subprocess.run(cmd, cwd=str(git_root), capture_output=True, text=True, timeout=120)
                else:
                    import ssl
                    ssl_context = ssl.create_default_context()
                    ssl_context.check_hostname = False
                    ssl_context.verify_mode = ssl.CERT_NONE

                    zip_path = BASE_DIR / "update_temp.zip"
                    extract_dir = BASE_DIR / "update_temp"

                    cache_buster = f"?t={int(time.time())}"
                    with urllib.request.urlopen(GITHUB_ZIP_URL + cache_buster, context=ssl_context) as response:
                        with open(str(zip_path), 'wb') as out_file:
                            out_file.write(response.read())

                    with zipfile.ZipFile(str(zip_path), 'r') as zip_ref:
                        zip_ref.extractall(str(extract_dir))

                    src_flowkit = extract_dir / "VE3_SUITE-main" / "server" / "flowkit"

                    if src_flowkit.exists():
                        for py_file in src_flowkit.glob("*.py"):
                            shutil.copy2(str(py_file), str(BASE_DIR / py_file.name))
                        for bat_file in src_flowkit.glob("*.bat"):
                            shutil.copy2(str(bat_file), str(BASE_DIR / bat_file.name))

                        src_agent = src_flowkit / "agent"
                        dst_agent = BASE_DIR / "agent"
                        if src_agent.exists():
                            dst_agent.mkdir(exist_ok=True)
                            for py_file in src_agent.rglob("*.py"):
                                rel = py_file.relative_to(src_agent)
                                dst = dst_agent / rel
                                dst.parent.mkdir(parents=True, exist_ok=True)
                                shutil.copy2(str(py_file), str(dst))

                        src_ext = src_flowkit / "flowkit_extensions"
                        dst_ext = BASE_DIR / "flowkit_extensions"
                        if src_ext.exists():
                            for ext_dir in src_ext.iterdir():
                                if ext_dir.is_dir():
                                    dst_sub = dst_ext / ext_dir.name
                                    if dst_sub.exists():
                                        shutil.rmtree(str(dst_sub))
                                    shutil.copytree(str(ext_dir), str(dst_sub))

                    src_server_root = extract_dir / "VE3_SUITE-main" / "server"
                    for shared_file in ("google_login.py", "requirements.txt"):
                        src = src_server_root / shared_file
                        if src.exists():
                            shutil.copy2(str(src), str(BASE_DIR / shared_file))
                    src_ipv6 = src_server_root / "modules" / "ipv6_proxy.py"
                    if src_ipv6.exists():
                        shutil.copy2(str(src_ipv6), str(BASE_DIR / "ipv6_proxy.py"))

                    if zip_path.exists():
                        zip_path.unlink()
                    if extract_dir.exists():
                        shutil.rmtree(str(extract_dir))

                remote_ver = getattr(self, '_remote_version', '') or self._get_remote_version()
                if remote_ver:
                    try:
                        (BASE_DIR / "VERSION.txt").write_text(remote_ver + "\n", encoding="utf-8")
                    except Exception:
                        pass
                    if not _IS_STANDALONE:
                        try:
                            (SUITE_ROOT / "VERSION").write_text(remote_ver + "\n", encoding="utf-8")
                        except Exception:
                            pass

                new_ver = _get_auto_version()
                self._version = new_ver
                self.after(0, lambda: self._version_label.config(
                    text=f"v{new_ver}", fg=GREEN))
                self.after(0, lambda: self._update_btn.config(
                    text=f"v{new_ver} OK! Restart...", bg='#00ff88', fg='#000'))
                self.after(0, lambda: self.title(f"FlowKit Server v{new_ver}"))
                self.after(2000, lambda: os.execv(sys.executable, [sys.executable] + sys.argv))

            except Exception as e:
                self._update_btn.config(text="LOI", bg=RED, fg='#fff')
                print(f"Update error: {e}")
                from tkinter import messagebox
                messagebox.showerror("Loi cap nhat", f"Loi: {e}\n\nThu tai thu cong:\n{GITHUB_ZIP_URL}")
            finally:
                self.after(5000, lambda: self._update_btn.config(
                    state="normal", text="Check Update", bg='#0984e3', fg='#fff',
                    command=self._on_check_update))

        threading.Thread(target=do_update, daemon=True).start()

    # ============================================================
    # Cleanup
    # ============================================================
    def _on_close(self):
        if self._started:
            self._on_stop()
        self.destroy()


def main():
    app = FlowKitGUI()
    app.mainloop()


if __name__ == "__main__":
    main()
