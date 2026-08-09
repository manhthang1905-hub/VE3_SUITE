import sys, os, json, time, glob
sys.path.insert(0, '.')
from cdp_chrome import ChromeCDP

CHROME_BASE = 'D:/VE3_SUITE'
CACHE_DIR = 'D:/veo3_engine_shopapi/veo3top_engine/_auth_cache'

email = 'antonioundadi@gmail.com'
copy_n = 10
chrome_dir = os.path.join(CHROME_BASE, f'GoogleChromePortable - Copy ({copy_n})')
chrome_exe = glob.glob(os.path.join(chrome_dir, 'App', 'Chrome-bin', 'chrome.exe'))[0]
profile_dir = os.path.join(chrome_dir, 'Data', 'profile')

print(f'Testing {email}')
cdp = ChromeCDP(chrome_exe, profile_dir, 19500, offscreen=False, log=lambda *a: print('  CDP:', *a))
if not cdp.connect(launch_timeout=30):
    print('CONNECT FAILED'); cdp.close(kill=True); sys.exit(1)

print('Connected! warm_flow...')
ok = cdp.warm_flow(attempts=5)
print(f'warm_flow: {ok}')
e = cdp.email()
b = cdp.bearer()
print(f'email={e} bearer={bool(b)}')

if b:
    cookies = cdp.cookies()
    cookie_str = '; '.join(f"{c['name']}={c['value']}" for c in cookies if 'labs.google' in c.get('domain', ''))
    project = cdp.first_project_id()
    print(f'cookie_len={len(cookie_str)} project={project}')
    if cookie_str:
        fname = 'antonioundadi_gmail.com.json'
        data = {'bearer': b, 'ts': time.time(), 'cookie': cookie_str, 'project': project, 'email': email}
        with open(os.path.join(CACHE_DIR, fname), 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=1)
        print(f'SAVED!')
else:
    print('No bearer -> checking SSO')
    cdp.cmd('Page.navigate', url='https://accounts.google.com')
    time.sleep(5)
    print(f'title: {cdp.ev("document.title")}')
    print(f'url: {cdp.ev("window.location.href")}')

cdp.close(kill=True)
