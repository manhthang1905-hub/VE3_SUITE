"""Install FlowKit extension into Chrome Copy (4) using Selenium CDP."""
import os, time, json
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service

chrome_bin = r"D:\VE3_SUITE\GoogleChromePortable - Copy (4)\App\Chrome-bin\chrome.exe"
user_data = r"D:\VE3_SUITE\GoogleChromePortable - Copy (4)\Data\profile"
ext_dir = r"D:\VE3_SUITE\flowkit_extensions\ext_8103"

# Kill existing Chrome Copy (4)
os.system('taskkill /F /FI "IMAGENAME eq chrome.exe" >nul 2>&1')
time.sleep(3)

opts = Options()
opts.binary_location = chrome_bin
opts.add_argument(f"--user-data-dir={user_data}")
opts.add_argument(f"--load-extension={ext_dir}")
opts.add_argument("--no-first-run")

print("Starting Chrome via Selenium...")
driver = webdriver.Chrome(options=opts)

# Navigate to Flow (triggers content_scripts → wakes service worker)
driver.get("https://labs.google/fx/tools/flow")
time.sleep(10)
print(f"URL: {driver.current_url[:60]}")

# Check agent
import urllib.request
try:
    r = urllib.request.urlopen("http://127.0.0.1:8103/health", timeout=3)
    d = json.loads(r.read())
    ext = d.get("extension_connected", False)
    connects = d.get("ws", {}).get("connects", 0)
    print(f"Agent: ext={ext} connects={connects}")
    if ext:
        print(">>> EXTENSION CONNECTED! <<<")
except Exception as e:
    print(f"Agent check: {e}")

# Keep Chrome open
print("\nChrome open. Press Ctrl+C to close.")
try:
    while True:
        time.sleep(5)
except KeyboardInterrupt:
    pass
