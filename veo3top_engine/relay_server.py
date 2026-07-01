"""
relay_server — RELAY GENERATE qua IP TƯƠI (giống server veo3top 165.99.14.17 = VPS VN).
Deploy lên 1 VPS VN rẻ (LienVPS/AZVPS...). CHỈ forward request generate tới Google từ IP VPS
(chưa bị đốt quota reCAPTCHA) -> reCAPTCHA qua. Mint/upload/poll/download vẫn ở máy.

Chạy trên VPS:  python relay_server.py --port 8890 --key <SECRET>
Client gọi:     POST http://<vps-ip>:8890/relay  header X-Relay-Key: <SECRET>
                body {"url":..., "headers":{...}, "data":"..."}  -> {"status":int,"text":str}

Bảo mật: chỉ forward tới *.googleapis.com / labs.google (không thành open-proxy) + cần đúng key.
Cài trên VPS:   pip install curl_cffi flask   (hoặc chỉ flask, fallback requests)
"""
import os, sys, json, argparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

try:
    from curl_cffi import requests as _http
    _IMPERSONATE = {"impersonate": "chrome"}
except Exception:
    import requests as _http
    _IMPERSONATE = {}

KEY = os.environ.get("VEO3TOP_RELAY_KEY", "")
_ALLOW = ("googleapis.com", "labs.google")   # chỉ cho forward tới Google/Flow


def _forward(url, headers, data, timeout=120):
    if not any(a in url for a in _ALLOW):
        return 400, json.dumps({"error": "url not allowed"})
    try:
        r = _http.post(url, headers=headers or {}, data=(data or "").encode("utf-8") if isinstance(data, str) else data,
                       timeout=timeout, **_IMPERSONATE)
        return r.status_code, r.text
    except Exception as e:
        return 599, f"relay error: {type(e).__name__}: {e}"


class _H(BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def _send(self, code, obj):
        b = json.dumps(obj).encode("utf-8")
        self.send_response(code); self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(b))); self.end_headers(); self.wfile.write(b)

    def do_GET(self):
        if self.path == "/health":
            # trả IP tươi của VPS (để client biết relay sống + IP gì)
            try:
                ip = _http.get("https://api.ipify.org", timeout=8, **_IMPERSONATE).text
            except Exception:
                ip = "?"
            self._send(200, {"ok": True, "egress_ip": ip})
        else:
            self._send(404, {"error": "nf"})

    def do_POST(self):
        if KEY and self.headers.get("X-Relay-Key", "") != KEY:
            self._send(403, {"error": "bad key"}); return
        n = int(self.headers.get("Content-Length", 0) or 0)
        try:
            j = json.loads(self.rfile.read(n).decode("utf-8") or "{}")
        except Exception:
            self._send(400, {"error": "bad json"}); return
        if self.path == "/relay":
            status, text = _forward(j.get("url", ""), j.get("headers"), j.get("data"), j.get("timeout", 120))
            self._send(200, {"status": status, "text": text})
        else:
            self._send(404, {"error": "nf"})


def main():
    global KEY
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8890)
    ap.add_argument("--key", default=KEY)
    ap.add_argument("--host", default="0.0.0.0")
    args = ap.parse_args()
    KEY = args.key
    srv = ThreadingHTTPServer((args.host, args.port), _H)
    print(f"[relay] nghe {args.host}:{args.port} (key={'set' if KEY else 'NONE-warn'}), http={_http.__name__}", flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
