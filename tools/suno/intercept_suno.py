#!/usr/bin/env python3
"""
Intercept ALL requests to studio-api-prod.suno.com
and save full details including POST body + response
"""
import subprocess, time, json
from pathlib import Path

PORTABLE_EXE = str(Path(__file__).parent / "GoogleChromePortable" / "GoogleChromePortable.exe")
DEBUG_PORT   = 9335
OUT_FILE     = Path(__file__).parent / "suno_api_details.json"

def main():
    from DrissionPage import ChromiumPage

    proc = subprocess.Popen([
        PORTABLE_EXE,
        f"--remote-debugging-port={DEBUG_PORT}",
        "--no-first-run",
        "https://suno.com/create"
    ])
    time.sleep(8)

    page = ChromiumPage(DEBUG_PORT)
    print(f"[INFO] Connected. URL: {page.url}")

    # Listen to studio-api-prod only
    page.listen.start(targets="studio-api-prod.suno.com")
    print("[INFO] Listening for 60s. Hãy thử click CREATE một bài nhạc trong browser!!!")
    print("="*60)

    captured = []
    start = time.time()
    while time.time() - start < 60:
        packet = page.listen.wait(timeout=4)
        if packet:
            url     = getattr(packet, 'url', '')
            method  = getattr(packet.request, 'method', 'GET') if hasattr(packet, 'request') else 'GET'
            req_hdrs = {}
            req_body = None
            resp_status = 0
            resp_body = ""

            if hasattr(packet, 'request') and packet.request:
                req_hdrs = dict(getattr(packet.request, 'headers', {}) or {})
                req_body = getattr(packet.request, 'body', None)
            if hasattr(packet, 'response') and packet.response:
                resp_status = getattr(packet.response, 'status', 0)
                try:
                    resp_body = packet.response.body[:500] if packet.response.body else ""
                except:
                    pass

            auth = req_hdrs.get('Authorization') or req_hdrs.get('authorization', '')

            info = {
                "url": url,
                "method": method,
                "auth_preview": auth[:80] if auth else "",
                "body": req_body,
                "resp_status": resp_status,
                "resp_preview": str(resp_body)[:300],
            }
            captured.append(info)

            print(f"[{method}] {url}")
            if req_body:
                print(f"  BODY: {str(req_body)[:150]}")
            print(f"  RESP {resp_status}: {str(resp_body)[:100]}")
            print()

    page.listen.stop()
    page.quit()
    proc.terminate()

    OUT_FILE.write_text(json.dumps(captured, indent=2, ensure_ascii=False), encoding='utf-8')
    print(f"\n[DONE] {len(captured)} requests saved → {OUT_FILE}")

if __name__ == "__main__":
    main()
