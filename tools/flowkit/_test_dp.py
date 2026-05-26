import sys, os, time, json, urllib.request
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "ve3"))
os.chdir(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "ve3"))
from DrissionPage import ChromiumOptions, ChromiumPage

opts = ChromiumOptions()
opts.set_browser_path(r"D:\VE3_SUITE\GoogleChromePortable - Copy (4)\App\Chrome-bin\chrome.exe")
opts.set_user_data_path(r"D:\VE3_SUITE\GoogleChromePortable - Copy (4)\Data\profile")
opts.set_local_port(9822)
opts.set_argument("--no-first-run")
opts.set_argument(r"--load-extension=D:\VE3_SUITE\flowkit_extensions\ext_8103")
page = ChromiumPage(opts)
page.get("https://labs.google/fx/tools/flow")
print("URL:", str(page.url)[:60])
time.sleep(15)
r = urllib.request.urlopen("http://127.0.0.1:8103/health", timeout=3)
d = json.loads(r.read())
ext = d.get("extension_connected", False)
connects = d.get("ws", {}).get("connects", 0)
print(f"ext={ext} connects={connects}")
if ext:
    print(">>> CONNECTED! <<<")
