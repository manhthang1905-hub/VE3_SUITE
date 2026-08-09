import sys, os, json, time, glob
if sys.stdout.encoding and sys.stdout.encoding.lower() not in ("utf-8", "utf8"):
    sys.stdout = open(sys.stdout.fileno(), mode='w', encoding='utf-8', errors='replace', closefd=False)
    sys.stderr = open(sys.stderr.fileno(), mode='w', encoding='utf-8', errors='replace', closefd=False)
sys.path.insert(0, '.')
from cdp_chrome import ChromeCDP

CHROME_BASE = 'D:/VE3_SUITE'
CACHE_DIR = 'D:/veo3_engine_shopapi/veo3top_engine/_auth_cache'
PROFILE_MAP = {
    'antonioundadi@gmail.com': 10,
    'ellazokarenkaren@gmail.com': 2,
    'latol0039@gmail.com': 7,
    'lucaslira30y@gmail.com': 6,
    'ng5855653@gmail.com': 8,
    'reena.patel2024@gmail.com': 3,
    'santaninaasaali@gmail.com': 5,
}

success = 0
for email, copy_n in PROFILE_MAP.items():
    chrome_dir = os.path.join(CHROME_BASE, f'GoogleChromePortable - Copy ({copy_n})')
    hits = glob.glob(os.path.join(chrome_dir, 'App', 'Chrome-bin', 'chrome.exe'))
    if not hits:
        print(f'[SKIP] {email}: no chrome.exe'); continue
    chrome_exe = hits[0]
    profile_dir = os.path.join(chrome_dir, 'Data', 'profile')
    port = 19500 + copy_n

    print(f'[{email}] Copy {copy_n}, port {port}')
    cdp = ChromeCDP(chrome_exe, profile_dir, port, offscreen=False, log=lambda *a, _e=email: None)
    try:
        if not cdp.connect(launch_timeout=30):
            print(f'  CONNECT FAILED'); cdp.close(kill=True); time.sleep(2); continue

        ok = cdp.warm_flow(attempts=5)
        b = cdp.bearer()
        e = cdp.email()

        if b:
            cookies = cdp.cookies()
            cookie_str = '; '.join(f"{c['name']}={c['value']}" for c in cookies if 'labs.google' in c.get('domain', ''))
            project = cdp.first_project_id()
            if cookie_str:
                fname = email.replace('@', '_').replace('.', '_') + '.json'
                for f in os.listdir(CACHE_DIR):
                    if email.split('@')[0] in f and f.endswith('.json'):
                        fname = f; break
                data = {'bearer': b, 'ts': time.time(), 'cookie': cookie_str, 'project': project, 'email': email}
                with open(os.path.join(CACHE_DIR, fname), 'w', encoding='utf-8') as fh:
                    json.dump(data, fh, indent=1)
                print(f'  OK! project={project}')
                success += 1
            else:
                print(f'  bearer OK but no cookies')
        else:
            print(f'  NO BEARER (SSO dead?)')
    except Exception as ex:
        print(f'  ERROR: {ex}')
    finally:
        cdp.close(kill=True)
        time.sleep(3)

print(f'\nDONE: {success}/{len(PROFILE_MAP)} refreshed')
