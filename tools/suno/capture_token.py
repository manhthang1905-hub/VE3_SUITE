#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
capture_token.py
Kết nối đến system Chrome (Profile 8) → lấy Clerk JWT token cho Suno API.
"""
import sys, time
from pathlib import Path

TOKEN_FILE   = Path(__file__).parent / "suno_token.txt"
SUNO_URL     = "https://suno.com/create"
CHROME_EXE   = r"C:\Program Files\Google\Chrome\Application\chrome.exe"
PROFILE_DIR  = r"C:\Users\trant\AppData\Local\Google\Chrome\User Data"
PROFILE_NAME = "Profile 8"
DEBUG_PORT   = 9333


def capture_via_drission() -> str | None:
    """
    Launch system Chrome với Profile 8 (tài khoản Suno đã login),
    poll cho đến khi Clerk.session sẵn sàng, rồi trả về JWT token.
    """
    import subprocess
    from DrissionPage import ChromiumPage

    # Đóng Chrome đang dùng port này (nếu có)
    try:
        import psutil
        for p in psutil.process_iter(['pid', 'cmdline']):
            cmdline = p.info.get('cmdline') or []
            if any(str(DEBUG_PORT) in str(a) for a in cmdline):
                p.kill()
                time.sleep(1)
    except Exception:
        pass

    print(f"[INFO] Starting system Chrome (Profile 8) on port {DEBUG_PORT}...")
    proc = subprocess.Popen([
        CHROME_EXE,
        f"--remote-debugging-port={DEBUG_PORT}",
        f"--user-data-dir={PROFILE_DIR}",
        f"--profile-directory={PROFILE_NAME}",
        "--no-first-run",
        "--no-default-browser-check",
        SUNO_URL,
    ])
    print(f"[INFO] Launched PID {proc.pid}, waiting 10s for Suno to load...")
    time.sleep(10)

    # Connect DrissionPage
    page = None
    for attempt in range(3):
        try:
            page = ChromiumPage(DEBUG_PORT)
            break
        except Exception as e:
            try:
                page = ChromiumPage(f"127.0.0.1:{DEBUG_PORT}")
                break
            except Exception as e2:
                print(f"[RETRY {attempt+1}] Connect failed: {e2}")
                time.sleep(3)

    if not page:
        print("[ERROR] Cannot connect to Chrome")
        proc.terminate()
        return None

    print(f"[INFO] Connected. URL: {page.url}")

    # Đảm bảo đang ở trang Suno
    if "suno.com" not in (page.url or ""):
        print(f"[INFO] Navigating to {SUNO_URL}...")
        page.get(SUNO_URL)
        time.sleep(5)

    # Poll cho đến khi Clerk.session sẵn sàng (max 45s)
    print("[INFO] Polling for Clerk.session to initialize...")
    token = None
    for attempt in range(15):   # 15 x 3s = 45s max
        time.sleep(3)
        try:
            result = page.run_js("""
                return new Promise((resolve, reject) => {
                    if (!window.Clerk)         { reject('no_clerk'); return; }
                    if (!window.Clerk.session) { reject('no_session'); return; }
                    window.Clerk.session.getToken()
                        .then(t => resolve(t))
                        .catch(e => reject(String(e)));
                });
            """, as_expr=False)
            if result and isinstance(result, str) and len(result) > 50:
                token = result.strip()
                print(f"[v] Token captured: {token[:40]}...")
                break
            else:
                print(f"  [try {attempt+1}] result={str(result)[:40]}")
        except Exception as e:
            err = str(e)
            if 'no_clerk' in err:
                print(f"  [try {attempt+1}] Clerk JS not loaded yet...")
            elif 'no_session' in err:
                print(f"  [try {attempt+1}] Session not ready (not logged in?)...")
            else:
                print(f"  [try {attempt+1}] {err[:80]}")

    # Fallback: lastActiveToken
    if not token:
        try:
            result = page.run_js("""
                var s = window.Clerk && window.Clerk.session;
                if (s && s.lastActiveToken) {
                    return s.lastActiveToken.getRawString
                        ? s.lastActiveToken.getRawString()
                        : (s.lastActiveToken.jwt || s.lastActiveToken.raw || null);
                }
                return null;
            """)
            if result and len(str(result)) > 50:
                token = str(result).strip()
                print(f"[v] Token via lastActiveToken: {token[:40]}...")
        except Exception as e:
            print(f"[lastActiveToken] {e}")

    page.quit()
    proc.terminate()
    return token


def main():
    print("=" * 60)
    print("Suno Token Capture — System Chrome Profile 8")
    print("=" * 60)

    token = capture_via_drission()

    if token:
        TOKEN_FILE.write_text(token, encoding='utf-8')
        print(f"\n[SUCCESS] Token saved to: {TOKEN_FILE}")
        print(f"Token preview: {token[:60]}...")
        print(f"\nGiờ chạy worker:")
        print('python suno_worker.py --excel "../../PROJECTS/KA5-xxxx/KA5-xxxx_prompts.xlsx"')
    else:
        print("\n[FAIL] Không lấy được token")
        print("\n>> MANUAL OPTION:")
        print("1. Mở Suno.com trong Chrome Profile 8")
        print("2. Bấm F12 → Console tab")
        print('3. window.Clerk.session.getToken().then(t => console.log("TOKEN:" + t))')
        print(f'4. Lưu vào file: {TOKEN_FILE}')


if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).parent))
    main()
