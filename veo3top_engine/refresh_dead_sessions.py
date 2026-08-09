"""
refresh_dead_sessions.py — Refresh NextAuth sessions for accounts with EXPIRED Flow tokens.

Google SSO is still ALIVE for all accounts (verified: /fx/api/auth/session returns access_token
for all 9). Only the NextAuth session-token has expired (~30-day lifetime). Fix: open Chrome with
the account's profile -> clear labs.google cookies -> reload Flow -> SSO silently re-authenticates
-> new NextAuth session. Then save new cookies back to _auth_cache.

Usage:
    cd D:\veo3_engine_shopapi\veo3top_engine
    python refresh_dead_sessions.py [--cache-dir PATH] [--chrome-base PATH]
"""
import os, sys, json, time, argparse, glob
_HERE = os.path.dirname(os.path.abspath(__file__))

# Add VE3_SUITE engine to path for imports
_ENGINE = os.environ.get("VEO3TOP_ENGINE_DIR", os.path.join(os.path.dirname(_HERE), "VE3_SUITE", "veo3top_engine"))
for p in [_HERE, _ENGINE, os.path.join(os.path.dirname(os.path.dirname(_HERE)), "VE3_SUITE", "veo3top_engine")]:
    if p not in sys.path and os.path.isdir(p):
        sys.path.insert(0, p)

from cdp_chrome import ChromeCDP
import flow_client as fc

BASE_PORT = 19200


def find_chrome_exe(chrome_base):
    """Find chrome.exe in GoogleChromePortable or system."""
    for pat in [os.path.join(chrome_base, "GoogleChromePortable", "App", "Chrome-bin", "*", "chrome.exe"),
                os.path.join(chrome_base, "GoogleChromePortable*", "App", "Chrome-bin", "*", "chrome.exe")]:
        hits = glob.glob(pat)
        if hits:
            return hits[0]
    for p in [r"C:\Program Files\Google\Chrome\Application\chrome.exe",
              r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe"]:
        if os.path.exists(p):
            return p
    return None


def find_profile_for_email(chrome_base, email):
    """Find the Chrome profile directory that has this account logged in."""
    # Strategy: search GoogleChromePortable - Copy (N)/Data/profile for each N
    # Check the profile's cookies or Preferences for the email
    for d in sorted(glob.glob(os.path.join(chrome_base, "GoogleChromePortable*"))):
        profile = os.path.join(d, "Data", "profile")
        if not os.path.isdir(profile):
            continue
        # Check if this profile has a cookie file with labs.google cookies
        cookie_db = os.path.join(profile, "Default", "Cookies")
        if not os.path.exists(cookie_db):
            cookie_db = os.path.join(profile, "Cookies")
        # Quick check: look for email in Preferences or Local State
        for pref_file in [os.path.join(profile, "Default", "Preferences"),
                          os.path.join(profile, "Preferences")]:
            if os.path.exists(pref_file):
                try:
                    txt = open(pref_file, encoding="utf-8", errors="ignore").read(50000)
                    if email.split("@")[0] in txt:
                        chrome_exe_path = glob.glob(os.path.join(d, "App", "Chrome-bin", "*", "chrome.exe"))
                        return profile, chrome_exe_path[0] if chrome_exe_path else None, d
                except Exception:
                    pass
    return None, None, None


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--cache-dir", default=os.path.join(_HERE, "_auth_cache"))
    parser.add_argument("--chrome-base", default=os.path.dirname(_HERE))
    args = parser.parse_args()

    cache_dir = args.cache_dir
    chrome_base = args.chrome_base

    print(f"Cache dir: {cache_dir}")
    print(f"Chrome base: {chrome_base}")

    # Load all accounts from auth_cache
    accounts = []
    for f in sorted(os.listdir(cache_dir)):
        if not f.endswith(".json") or f.startswith("."):
            continue
        try:
            d = json.load(open(os.path.join(cache_dir, f), encoding="utf-8"))
        except Exception:
            continue
        email = d.get("email")
        cookie = d.get("cookie", "")
        if not email or not cookie:
            continue
        accounts.append({"email": email, "cookie": cookie, "file": f, "data": d})

    print(f"\n=== CHECKING {len(accounts)} ACCOUNTS ===\n")

    dead = []
    for a in accounts:
        lv = fc.cookie_liveness(a["cookie"], timeout=10)
        status = "ALIVE" if lv == "alive" else lv.upper()
        print(f"  {a['email']:35s}  {status}")
        if lv != "alive":
            dead.append(a)

    if not dead:
        print("\nAll accounts alive! Nothing to do.")
        return

    print(f"\n{len(dead)} accounts need refresh.\n")

    # For each dead account, find its Chrome profile and refresh
    chrome_exe = find_chrome_exe(chrome_base)
    if not chrome_exe:
        print("ERROR: No chrome.exe found!")
        return
    print(f"Chrome exe: {chrome_exe}\n")

    success = 0
    for i, a in enumerate(dead):
        email = a["email"]
        profile_dir, profile_chrome, portable_dir = find_profile_for_email(chrome_base, email)

        if not profile_dir:
            print(f"[{i+1}/{len(dead)}] {email}: NO PROFILE FOUND - skip")
            continue

        use_chrome = profile_chrome or chrome_exe
        port = BASE_PORT + i
        print(f"[{i+1}/{len(dead)}] {email}")
        print(f"  profile: {profile_dir}")
        print(f"  chrome:  {use_chrome}")
        print(f"  port:    {port}")

        cdp = ChromeCDP(use_chrome, profile_dir, port, offscreen=False,
                        log=lambda *args: print("    CDP:", *args))
        try:
            if not cdp.connect(launch_timeout=30):
                print(f"  FAIL: Chrome did not connect")
                cdp.close(kill=True)
                time.sleep(2)
                continue

            # Check current email in session
            current_email = cdp.email()
            print(f"  Session email: {current_email}")

            if current_email and current_email.lower() != email.lower():
                print(f"  WRONG ACCOUNT in this profile! Expected {email}, got {current_email}")
                cdp.close(kill=True)
                time.sleep(2)
                continue

            print(f"  Refreshing session (clearing labs.google cookies + reload)...")
            ok = cdp.refresh_session(wait=45)

            if ok:
                # Get new cookies from Chrome
                raw_cookies = cdp.cookies()
                cookie_str = "; ".join(f"{c['name']}={c['value']}" for c in raw_cookies
                                       if "labs.google" in c.get("domain", ""))
                bearer = cdp.bearer()
                project = getattr(cdp, '_refreshed_pid', None) or cdp.first_project_id()

                if cookie_str and bearer:
                    # Save to auth_cache
                    cache_file = os.path.join(cache_dir, a["file"])
                    data = {"bearer": bearer, "ts": time.time(), "cookie": cookie_str,
                            "project": project, "email": email}
                    with open(cache_file, "w", encoding="utf-8") as f:
                        json.dump(data, f, indent=1)

                    print(f"  SUCCESS! bearer={bearer[:30]}... project={project}")
                    success += 1
                else:
                    print(f"  PARTIAL: refresh ok but cookie={len(cookie_str)} bearer={bool(bearer)}")
            else:
                # Try reading session anyway - maybe it worked but create_project failed
                new_email = cdp.email()
                new_bearer = cdp.bearer()
                if new_bearer:
                    raw_cookies = cdp.cookies()
                    cookie_str = "; ".join(f"{c['name']}={c['value']}" for c in raw_cookies
                                           if "labs.google" in c.get("domain", ""))
                    project = cdp.first_project_id() or cdp.create_project()
                    if cookie_str:
                        data = {"bearer": new_bearer, "ts": time.time(), "cookie": cookie_str,
                                "project": project, "email": email}
                        cache_file = os.path.join(cache_dir, a["file"])
                        with open(cache_file, "w", encoding="utf-8") as f:
                            json.dump(data, f, indent=1)
                        print(f"  RECOVERED! bearer={new_bearer[:30]}...")
                        success += 1
                    else:
                        print(f"  FAIL: has bearer but no cookies")
                else:
                    print(f"  FAIL: refresh_session returned False, no bearer")
        except Exception as e:
            print(f"  ERROR: {e}")
            import traceback; traceback.print_exc()
        finally:
            cdp.close(kill=True)
            time.sleep(3)

    print(f"\n=== DONE: {success}/{len(dead)} refreshed successfully ===")
    if success > 0:
        print("Restart veo3 worker to pick up new sessions.")


if __name__ == "__main__":
    main()
