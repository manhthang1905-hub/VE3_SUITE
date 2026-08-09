"""
fix_dead_sessions.py — Refresh 7 accounts với NextAuth session hết hạn quá lâu (>20h).
Google SSO vẫn sống trong Chrome profile → mở Chrome → xoá labs.google cookies →
reload Flow → SSO tự re-auth → NextAuth cấp session mới → lưu cookie mới vào auth_cache.

Usage:
    cd D:\VE3_SUITE\veo3top_engine
    python fix_dead_sessions.py
"""
import os, sys, json, time, re, glob
# Fix Windows console encoding (cp1252 can't handle Unicode)
if sys.stdout.encoding and sys.stdout.encoding.lower() not in ("utf-8", "utf8"):
    sys.stdout = open(sys.stdout.fileno(), mode='w', encoding='utf-8', errors='replace', closefd=False)
    sys.stderr = open(sys.stderr.fileno(), mode='w', encoding='utf-8', errors='replace', closefd=False)
_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)

from cdp_chrome import ChromeCDP
import flow_client as fc

CACHE_DIR = "D:/veo3_engine_shopapi/veo3top_engine/_auth_cache"
CHROME_BASE = "D:/VE3_SUITE"

# Mapping email -> Chrome Portable Copy number (from Preferences scan)
PROFILE_MAP = {
    "antonioundadi@gmail.com": 10,
    "ellazokarenkaren@gmail.com": 2,
    "latol0039@gmail.com": 7,
    "lf177254@gmail.com": 4,
    "lucaslira30y@gmail.com": 6,
    "ng5855653@gmail.com": 8,
    "reena.patel2024@gmail.com": 3,
    "santaninaasaali@gmail.com": 5,
    "lua789001@gmail.com": None,  # already alive, skip
}

BASE_PORT = 19300


def find_chrome_exe(copy_n):
    if copy_n is None or copy_n == 0:
        d = os.path.join(CHROME_BASE, "GoogleChromePortable")
    else:
        d = os.path.join(CHROME_BASE, f"GoogleChromePortable - Copy ({copy_n})")
    # Try both: with and without version subdirectory
    for pattern in [os.path.join(d, "App", "Chrome-bin", "chrome.exe"),
                    os.path.join(d, "App", "Chrome-bin", "*", "chrome.exe")]:
        hits = glob.glob(pattern)
        if hits:
            return hits[0]
    return None


def main():
    # Find dead accounts
    dead = []
    for f in sorted(os.listdir(CACHE_DIR)):
        if not f.endswith(".json") or f.startswith("."): continue
        d = json.load(open(os.path.join(CACHE_DIR, f), encoding="utf-8"))
        email = d.get("email", "")
        cookie = d.get("cookie", "")
        if not email or not cookie: continue
        
        lv = fc.cookie_liveness(cookie, timeout=10)
        if lv == "alive":
            # Double-check: try bearer
            b, e = fc.bearer_from_cookie(cookie, timeout=10)
            if b:
                print(f"  {email:35s}  ALIVE (bearer OK) - skip")
                continue
            # liveness=alive but bearer failed - still need refresh
        
        copy_n = PROFILE_MAP.get(email)
        if copy_n is None:
            print(f"  {email:35s}  ALIVE or no mapping - skip")
            continue
        dead.append({"email": email, "file": f, "copy_n": copy_n, "data": d})

    if not dead:
        print("\nAll accounts working! Nothing to do.")
        return

    print(f"\n{len(dead)} accounts need Chrome SSO refresh.\n")

    success = 0
    for i, a in enumerate(dead):
        email = a["email"]
        copy_n = a["copy_n"]
        chrome_exe = find_chrome_exe(copy_n)
        
        if copy_n == 0:
            profile_dir = os.path.join(CHROME_BASE, "GoogleChromePortable", "Data", "profile")
        else:
            profile_dir = os.path.join(CHROME_BASE, f"GoogleChromePortable - Copy ({copy_n})", "Data", "profile")
        
        if not chrome_exe or not os.path.isdir(profile_dir):
            print(f"[{i+1}/{len(dead)}] {email}: chrome={chrome_exe} profile={profile_dir} - MISSING")
            continue

        port = BASE_PORT + i
        print(f"[{i+1}/{len(dead)}] {email} (Copy {copy_n}, port {port})")

        cdp = ChromeCDP(chrome_exe, profile_dir, port, offscreen=False,
                        log=lambda *args, _e=email: print(f"    [{_e.split('@')[0]}]", *args))
        try:
            if not cdp.connect(launch_timeout=30):
                print(f"  FAIL: Chrome connect failed")
                cdp.close(kill=True)
                time.sleep(2)
                continue

            # Verify correct account
            sess_email = cdp.email()
            print(f"  Chrome session email: {sess_email}")

            print(f"  Calling refresh_session() (SSO re-auth)...")
            ok = cdp.refresh_session(wait=60)

            if ok:
                print(f"  refresh_session = True, extracting cookies...")
                raw_cookies = cdp.cookies()
                cookie_parts = []
                for c in raw_cookies:
                    dom = c.get("domain", "")
                    if "labs.google" in dom:
                        cookie_parts.append(f"{c['name']}={c['value']}")
                cookie_str = "; ".join(cookie_parts)
                bearer = cdp.bearer()
                project = getattr(cdp, '_refreshed_pid', None) or cdp.first_project_id()
                new_email = cdp.email()

                if cookie_str and bearer:
                    data = {"bearer": bearer, "ts": time.time(), "cookie": cookie_str,
                            "project": project, "email": email}
                    cache_file = os.path.join(CACHE_DIR, a["file"])
                    with open(cache_file, "w", encoding="utf-8") as f:
                        json.dump(data, f, indent=1)
                    print(f"  [OK] SUCCESS! expires ~24h, project={project}")
                    success += 1
                else:
                    print(f"  [WARN] PARTIAL: cookie={len(cookie_str)} bearer={bool(bearer)}")
            else:
                # Try extracting anyway
                bearer = cdp.bearer()
                if bearer:
                    raw_cookies = cdp.cookies()
                    cookie_parts = [f"{c['name']}={c['value']}" for c in raw_cookies if "labs.google" in c.get("domain", "")]
                    cookie_str = "; ".join(cookie_parts)
                    if cookie_str:
                        project = cdp.first_project_id() or cdp.create_project()
                        data = {"bearer": bearer, "ts": time.time(), "cookie": cookie_str,
                                "project": project, "email": email}
                        with open(os.path.join(CACHE_DIR, a["file"]), "w", encoding="utf-8") as f:
                            json.dump(data, f, indent=1)
                        print(f"  [OK] RECOVERED (refresh=False but bearer exists)! project={project}")
                        success += 1
                    else:
                        print(f"  [FAIL] bearer but no cookies")
                else:
                    print(f"  [FAIL] no bearer after refresh")
        except Exception as e:
            print(f"  [ERROR] {e}")
            import traceback; traceback.print_exc()
        finally:
            cdp.close(kill=True)
            time.sleep(3)

    print(f"\n{'='*60}")
    print(f"RESULT: {success}/{len(dead)} accounts refreshed")
    if success > 0:
        print("=> Restart veo3 worker to use new sessions")


if __name__ == "__main__":
    main()
