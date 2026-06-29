"""Test: _kill_chrome_for_dir tree kills ALL child processes."""
import sys, time, subprocess
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
sys.path.insert(0, r'd:\VE3_SUITE\server\flowkit')
from pathlib import Path
from modules.flow_extension_auth import _ExtensionInstanceManager

def chrome_count():
    r = subprocess.run(['tasklist', '/FI', 'IMAGENAME eq chrome.exe', '/FO', 'CSV'],
                       capture_output=True, text=True, timeout=5)
    return sum(1 for l in r.stdout.splitlines() if 'chrome' in l.lower())

# Start Chrome Copy (1)
print("Starting Chrome Copy (1)...")
p = subprocess.Popen(
    [r'D:\VE3_SUITE\GoogleChromePortable - Copy (1)\GoogleChromePortable.exe',
     '--no-first-run', '--no-default-browser-check'],
    creationflags=0x08000000)
time.sleep(6)

before = chrome_count()
print(f"Before kill: {before} chrome processes")

# Kill with tree kill
print("Killing...")
_ExtensionInstanceManager._kill_chrome_for_dir(Path(r'D:\VE3_SUITE\GoogleChromePortable - Copy (1)'))
time.sleep(3)

after = chrome_count()
print(f"After kill: {after} chrome processes")
print(f"RESULT: {'PASS' if after == 0 else 'FAIL'}")
