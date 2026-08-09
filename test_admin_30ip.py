# CHAY AS ADMIN: python test_admin_30ip.py
# Test IP POOL TUOI (bind can admin) + CHROME TUOI moi lan + VERIFY egress IPv6 khong lo IP may.
import sys, json, os, time, tempfile, shutil
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_HERE, "veo3top_engine"))
sys.path.insert(0, os.path.join(_HERE, "server", "modules"))
sys.path.insert(0, os.path.join(_HERE, "server", "flowkit"))
os.environ["VEO3TOP_CLEAN_FLAGS"] = "1"
import flow_client as fc
import token_factory as tf
import ipv6_transport as ip6t
from cdp_chrome import ChromeCDP
from auth_cache import AuthCache

try:
    import ctypes; admin = ctypes.windll.shell32.IsUserAnAdmin()
except Exception: admin = False
print(f"[admin] {'CO ADMIN - OK' if admin else '!!! CHUA ADMIN - chay lai: chuot phai PowerShell -> Run as administrator !!!'}", flush=True)

N = 15
ACC = "ellazokarenkaren"
chrome_exe = tf._system_chrome()
cache = AuthCache(log=lambda *_: None)
d = json.load(open(os.path.join("veo3top_engine", "_auth_cache", f"{ACC}_gmail.com.json"), encoding="utf-8"))
auth = cache._refresh_from_cookie(f"{ACC}@gmail.com", d)
bearer, cookie, project = auth["bearer"], auth.get("cookie"), auth["project"]
print(f"[auth] {ACC} project={project[:8]}\n", flush=True)

res = {}; ips = set()
for i in range(N):
    # TRANSPORT worker MOI = IP POOL TUOI UNIQUE; as admin -> bind that
    tr = ip6t.IPv6Transport(f"a30_{i}_{int(time.time())}", port=9930 + (i % 30), log=lambda *_: None)
    if not tr.start():
        print(f"  {i+1}: transport fail"); continue
    ips.add(str(tr.ip))
    # CHROME TUOI (fresh connection = mint dung IP tuoi, khong keep-alive cu)
    prof = os.path.join(tempfile.gettempdir(), f"a30_{i}"); shutil.rmtree(prof, ignore_errors=True)
    cdp = ChromeCDP(chrome_exe, prof, 9850 + (i % 30), offscreen=False, log=lambda *_: None, proxy=tr.proxy_url())
    if not (cdp.connect(launch_timeout=40) and cdp.wait_ready(45)):
        print(f"  {i+1}: chrome fail ip=...{str(tr.ip)[-10:]}")
        try: cdp.close(kill=True); tr.stop()
        except: pass
        continue
    # VERIFY egress THAT = IPv6 gan (khong lo IP may 14.224.x)
    try:
        eg = cdp.ev("(async()=>{try{let r=await fetch('https://ipv6.icanhazip.com',{cache:'no-store'});return(await r.text()).trim()}catch(e){return 'ERR'}})()")
    except Exception: eg = "?"
    ips14 = "LO-IP-MAY!" if "14.224" in str(eg) else ("KHOP-IPv6" if (eg and str(tr.ip)[-14:] in str(eg)) else f"KHAC:{str(eg)[:22]}")
    tok = cdp.mint_token("VIDEO_GENERATION")
    if tok:
        kind, _ = fc.generate(bearer, fc.build_payload("A calm ocean wave.", project, tok, seed=i+1, aspect="VIDEO_ASPECT_RATIO_LANDSCAPE"),
                              url=fc.GEN_T2V, cookie=cookie, proxy=tr.proxy_url(), timeout=60)
        res[kind] = res.get(kind, 0) + 1
        mk = " <== OK!!!" if kind == "ok" else ""
        print(f"  {i+1}/{N}: {kind} (ok={res.get('ok',0)}) egress={ips14} ip=...{str(tr.ip)[-10:]}{mk}", flush=True)
    else:
        res["no_tok"] = res.get("no_tok", 0) + 1
        print(f"  {i+1}/{N}: no_token egress={ips14}", flush=True)
    try: cdp.close(kill=True); tr.stop()
    except: pass

ok = res.get("ok", 0)
print(f"\n### {N} IP POOL TUOI (ADMIN, {len(ips)} IP unique) + chrome tuoi: {ok}/{N} = {ok*100//N}% ###", flush=True)
print(f"    {res}", flush=True)
