import sys,os,json,time,glob
sys.path.insert(0,'.')
from cdp_chrome import ChromeCDP
CACHE='D:/veo3_engine_shopapi/veo3top_engine/_auth_cache'
email='lucasff02731@gmail.com'
d=os.path.join('D:/VE3_SUITE','GoogleChromePortable - Copy (9)')
exe=glob.glob(os.path.join(d,'App','Chrome-bin','chrome.exe'))[0]
prof=os.path.join(d,'Data','profile')
print(f'Warming {email}')
cdp=ChromeCDP(exe,prof,19509,offscreen=False,log=lambda *a:None)
if not cdp.connect(launch_timeout=30):
    print('FAIL connect');cdp.close(kill=True);sys.exit(1)
ok=cdp.warm_flow(attempts=5)
b=cdp.bearer();e=cdp.email()
print(f'warm={ok} email={e} bearer={bool(b)}')
if b:
    cs=cdp.cookies()
    ck='; '.join(f"{c['name']}={c['value']}" for c in cs if 'labs.google' in c.get('domain',''))
    pj=cdp.first_project_id()
    data={'bearer':b,'ts':time.time(),'cookie':ck,'project':pj,'email':email}
    fn=os.path.join(CACHE,'lucasff02731_gmail.com.json')
    json.dump(data,open(fn,'w',encoding='utf-8'),indent=1)
    print(f'SAVED! project={pj}')
else:
    print('NO BEARER')
cdp.close(kill=True)
