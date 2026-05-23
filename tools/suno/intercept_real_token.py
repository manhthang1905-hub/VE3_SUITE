#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
intercept_real_token.py
=======================
Kết nối Chrome Portable đang mở → lắng nghe network request → bắt Bearer token
từ request thật tới studio-api-prod.suno.com.

Cách dùng:
1. Chạy:  start_chrome_suno.bat   (mở Chrome Portable với debug port 9333)
2. Trong Chrome: đăng nhập Suno, vào suno.com/create
3. Chạy script này:  python intercept_real_token.py
4. Trong Chrome: click Advanced → nhập bất kỳ prompt → click Create
5. Script tự bắt token → lưu suno_token.txt → done
"""

import sys, time
from pathlib import Path

TOKEN_FILE  = Path(__file__).parent / "suno_token.txt"
DEBUG_PORT  = 9333
TARGET_HOST = "studio-api-prod.suno.com"
TIMEOUT     = 120   # giây chờ user click Create


def connect_chrome(port: int):
    """Connect DrissionPage tới Chrome đang listen trên port."""
    from DrissionPage import ChromiumPage
    try:
        page = ChromiumPage(port)
        return page
    except Exception:
        try:
            page = ChromiumPage(f"127.0.0.1:{port}")
            return page
        except Exception as e:
            print(f"[ERROR] Cannot connect to Chrome on port {port}: {e}")
            return None


def intercept_token(port: int = DEBUG_PORT, timeout: int = TIMEOUT):
    """
    Lắng nghe network → bắt Authorization header từ request tới Suno API.
    Trả về token string hoặc None.
    """
    page = connect_chrome(port)
    if not page:
        return None

    print(f"[OK] Connected to Chrome. URL: {page.url}")

    # Đảm bảo đang ở trang Suno
    if "suno.com" not in (page.url or ""):
        print(f"[INFO] Navigating to suno.com/create...")
        page.get("https://suno.com/create")
        time.sleep(4)

    print()
    print("=" * 55)
    print("  READY — Đang lắng nghe network requests...")
    print("  Trong Chrome: click Advanced → nhập prompt → Create")
    print("=" * 55)
    print()

    # Bắt đầu listen network packets
    page.listen.start(TARGET_HOST)

    token = None
    start = time.time()

    while time.time() - start < timeout:
        elapsed = int(time.time() - start)
        remaining = timeout - elapsed

        # Poll packets đã nhận
        packet = page.listen.wait(timeout=3)
        if packet is None:
            print(f"  Chờ... ({remaining}s còn lại) — hãy click Create trong Chrome")
            continue

        # Kiểm tra URL có phải Suno API không
        url = getattr(packet, 'url', '') or ''
        if TARGET_HOST not in url:
            continue

        # Lấy Authorization header
        auth = None
        try:
            req = packet.request
            headers = {}
            if hasattr(req, 'headers'):
                headers = req.headers or {}
            # DrissionPage v4: headers là dict hoặc object
            if isinstance(headers, dict):
                auth = (headers.get('Authorization') or
                        headers.get('authorization'))
            else:
                auth = (getattr(headers, 'Authorization', None) or
                        getattr(headers, 'authorization', None))
        except Exception:
            pass

        if auth and auth.startswith("Bearer ") and len(auth) > 100:
            token = auth.replace("Bearer ", "").strip()
            print(f"\n[✓] Token captured from: {url[:60]}...")
            print(f"    Token: {token[:40]}...")
            break
        else:
            print(f"  [request] {url[:60]} (no auth)")

    page.listen.stop()
    return token


def main():
    print("=" * 55)
    print("  Suno Token Interceptor")
    print("  Bắt token từ real network request")
    print("=" * 55)
    print()
    print(f"Kết nối Chrome Portable trên port {DEBUG_PORT}...")
    print(f"(Nếu chưa mở: chạy start_chrome_suno.bat trước)")
    print()

    token = intercept_token()

    if token:
        TOKEN_FILE.write_text(token, encoding='utf-8')
        print(f"\n[SUCCESS] Token saved: {TOKEN_FILE}")
        print(f"Preview: {token[:60]}...")
        print()
        print("Giờ chạy worker:")
        print('python suno_worker.py --excel "../../PROJECTS/KA5-xxxx/KA5-xxxx_prompts.xlsx"')
    else:
        print("\n[FAIL] Không bắt được token.")
        print("Kiểm tra:")
        print("  1. Chrome Portable mở bằng start_chrome_suno.bat?")
        print("  2. Đã đăng nhập Suno chưa?")
        print("  3. Đã click Create chưa?")

    return token


if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).parent))
    main()
