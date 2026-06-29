"""Test setup_chrome login only."""
import sys, time
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
sys.path.insert(0, r'd:\VE3_SUITE\server\flowkit')
from pathlib import Path
from chrome_setup import setup_chrome
from launcher import _write_chrome_prefs

chrome_dir = Path(r'D:\VE3_SUITE\GoogleChromePortable - Copy (1)')
ext_dir = Path(r'D:\VE3_SUITE\flowkit_extensions\ext_8100')

for lk in ('SingletonLock', 'SingletonCookie', 'SingletonSocket'):
    try:
        (chrome_dir / 'Data' / 'profile' / lk).unlink()
    except Exception:
        pass

_write_chrome_prefs(chrome_dir)

def log(msg):
    ts = time.strftime("%H:%M:%S")
    print(f"[{ts}] {msg}")
    sys.stdout.flush()

log("=== Test setup_chrome sv1 ===")
ok = setup_chrome(
    chrome_dir=chrome_dir, ext_dir=ext_dir, port=19200,
    account={
        'id': 'dendalion41@gmail.com',
        'password': 'Damuocmo212@',
        'totp_secret': 'ag7b 35ne sdsx na3z xgkj kydl 3bmx 3fir',
    },
    proxy_arg='',
    log_func=log,
    instance_name='sv1',
)
log(f"Result: {'OK' if ok else 'FAIL'}")
