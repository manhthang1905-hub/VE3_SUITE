"""
WARP manager — "Fake DNS Google free local".
Chạy Cloudflare WARP ở chế độ SOCKS5 proxy local (127.0.0.1:40000) và rotate IP.

Phát hiện quan trọng (xem VEO3TOP_MECHANISM_NOTES.md):
- `warp-cli disconnect/connect` KHÔNG đổi IP (kẹt cùng /24).
- Phải `registration delete` + `registration new` để đổi pool IP.
- Một số IP bị aisandbox-pa block cứng (generate trả 403 HTML "Sorry").
  Test IP đúng phải dùng bearer thật (credits?key=... + Bearer => 200). KHÔNG dựa
  vào credits-no-bearer (luôn trả "Sorry" kể cả IP tốt).
"""
import subprocess, time, requests

WARP_CLI = r"C:\Program Files\Cloudflare\Cloudflare WARP\warp-cli.exe"
PROXY_PORT = 40000
PROXIES = {"https": f"socks5h://127.0.0.1:{PROXY_PORT}", "http": f"socks5h://127.0.0.1:{PROXY_PORT}"}
KEY = "AIzaSyBtrm0o5ab1c-Ec8ZuLcGt3oJAA5VWt3pY"


def _cli(*args, timeout=30):
    return subprocess.run([WARP_CLI, "--accept-tos", *args],
                          capture_output=True, text=True, timeout=timeout).stdout.strip()


def ensure_proxy_mode():
    """Đảm bảo WARP đang ở mode proxy port 40000 + connected."""
    _cli("mode", "proxy")
    _cli("proxy", "port", str(PROXY_PORT))
    _cli("connect")
    time.sleep(4)
    return current_ip()


def current_ip():
    try:
        return requests.get("https://api.ipify.org", proxies=PROXIES, timeout=20).text.strip()
    except Exception:
        return None


def rotate_ip():
    """Đổi IP exit bằng RE-REGISTER (đã kiểm chứng: cycle được pool nhỏ của colo gần nhất,
    vd 104.28.237.239 <-> 237.241 <-> 205.239). WARP dùng anycast nên từ 1 máy chỉ có pool
    ~3-4 IP; re-register phân tán tải qua các IP đó (tránh 1 IP bị 429 TOO_MUCH_TRAFFIC).
    Trả về IP mới (có thể trùng nếu xui)."""
    cur = current_ip()
    for _ in range(3):
        _cli("disconnect"); time.sleep(1)
        _cli("registration", "delete"); time.sleep(1)
        _cli("registration", "new"); time.sleep(2)
        _cli("mode", "proxy"); _cli("proxy", "port", str(PROXY_PORT))
        _cli("connect"); time.sleep(5)
        ip = current_ip()
        if ip and ip != cur:
            return ip
    return current_ip()


def ip_is_good(bearer: str) -> bool:
    """IP tốt nếu aisandbox-pa nhận request có bearer (200), không trả 'Sorry'."""
    try:
        r = requests.get(f"https://aisandbox-pa.googleapis.com/v1/credits?key={KEY}",
                         headers={"Authorization": f"Bearer {bearer}",
                                  "Origin": "https://labs.google", "Referer": "https://labs.google/"},
                         proxies=PROXIES, timeout=20)
        return r.status_code == 200
    except Exception:
        return False


def ensure_good_ip(bearer: str, max_rotations: int = 8) -> str | None:
    """Rotate đến khi có IP aisandbox-pa chấp nhận. Trả về IP tốt hoặc None."""
    ensure_proxy_mode()
    for _ in range(max_rotations):
        if ip_is_good(bearer):
            return current_ip()
        rotate_ip()
    return current_ip() if ip_is_good(bearer) else None


if __name__ == "__main__":
    print("proxy mode IP:", ensure_proxy_mode())
    print("rotate ->", rotate_ip())
