"""
pool_accounts — nạp DANH SÁCH 10 account ultra cho "nhà máy video chung" (veo3top-b-pool).
Nguồn cấu hình RÕ RÀNG: settings.yaml -> local_server_list (sv1..sv10, enabled).
Mỗi account: email (từ flow_account_bundle), chrome_path (Copy(N) — fallback nếu cookie chết).
Auth (bearer+cookie+project) lấy từ auth_cache (refresh TỪ COOKIE, không mở chrome).
"""
import os, sys
_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

# settings.yaml của ve3
_SETTINGS = os.path.join(os.path.dirname(_HERE), "tools", "ve3", "config", "settings.yaml")


def _parse_bundle(bundle):
    """flow_account_bundle = 'email|password|token' -> email (phần đầu)."""
    if not bundle:
        return None
    return str(bundle).split("|", 1)[0].strip() or None


def load_pool_accounts(settings_path=None):
    """Trả list dict {name, email, chrome_path} cho các server ultra enabled trong settings.yaml.
    KHÔNG chứa auth — auth lấy runtime qua auth_cache (refresh cookie)."""
    path = settings_path or _SETTINGS
    try:
        import yaml
    except Exception:
        yaml = None
    accounts = []
    seen = set()
    try:
        if yaml:
            with open(path, encoding="utf-8") as f:
                data = yaml.safe_load(f) or {}
            servers = data.get("local_server_list") or []
        else:
            servers = []
    except Exception:
        servers = []
    for s in servers:
        if not isinstance(s, dict) or not s.get("enabled", False):
            continue
        email = _parse_bundle(s.get("flow_account_bundle"))
        if not email or email.lower() in seen:
            continue
        seen.add(email.lower())
        accounts.append({
            "name": s.get("name") or email,
            "email": email,
            "chrome_path": s.get("chrome_path") or "",
        })
    return accounts


def load_image_pool_accounts(settings_path=None):
    """Danh sách account cho NHÀ MÁY ẢNH — TÁCH RIÊNG với video (mai sẽ là pro accounts khác).
    Ưu tiên section 'image_pool_accounts' trong settings.yaml (list {name,email,chrome_path}) HOẶC
    'image_server_list' (giống local_server_list). Chưa có -> DÙNG CHUNG pool video (như hiện tại).
    Cơ chế auth y hệt video: cookie -> refresh bearer (auth_cache), tái dùng nhiều lần."""
    path = settings_path or _SETTINGS
    try:
        import yaml
        with open(path, encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
    except Exception:
        data = {}
    # (a) list trực tiếp {name,email,chrome_path}
    direct = data.get("image_pool_accounts")
    if isinstance(direct, list) and direct:
        out = []
        for s in direct:
            if isinstance(s, dict) and s.get("email"):
                out.append({"name": s.get("name") or s["email"], "email": s["email"],
                            "chrome_path": s.get("chrome_path") or ""})
        if out:
            return out
    # (b) image_server_list (giống local_server_list: flow_account_bundle)
    servers = data.get("image_server_list")
    if isinstance(servers, list) and servers:
        out = []; seen = set()
        for s in servers:
            if not isinstance(s, dict) or not s.get("enabled", True):
                continue
            email = _parse_bundle(s.get("flow_account_bundle")) or s.get("email")
            if email and email.lower() not in seen:
                seen.add(email.lower())
                out.append({"name": s.get("name") or email, "email": email,
                            "chrome_path": s.get("chrome_path") or ""})
        if out:
            return out
    # (c) fallback: dùng chung pool video (trước mắt)
    return load_pool_accounts(settings_path)


if __name__ == "__main__":
    # Test: liệt kê pool + kiểm tra account nào đã có auth cache (cookie) sẵn sàng.
    import json
    accts = load_pool_accounts()
    print(f"Pool: {len(accts)} account ultra (settings.yaml local_server_list, enabled)")
    cache_dir = os.path.join(_HERE, "_auth_cache")
    import re
    def _safe(n): return re.sub(r"[^a-zA-Z0-9._-]", "_", str(n or ""))
    ready = 0
    for a in accts:
        p = os.path.join(cache_dir, _safe(a["email"]) + ".json")
        has_cookie = False; has_bearer = False
        if os.path.exists(p):
            try:
                d = json.load(open(p, encoding="utf-8"))
                has_cookie = bool(d.get("cookie")); has_bearer = bool(d.get("bearer"))
            except Exception:
                pass
        if has_cookie:
            ready += 1
        print(f"  {a['name']:5} {a['email']:32} cookie={'Y' if has_cookie else 'N'} bearer={'Y' if has_bearer else 'N'}")
    print(f"San sang (co cookie de refresh bearer): {ready}/{len(accts)}")
