"""
Flow API client — bắn thẳng aisandbox-pa (Veo 3.1) qua WARP socks5.
Contract đã xác nhận chạy thật (xem VEO3TOP_MECHANISM_NOTES.md).

QUAN TRỌNG: dùng curl_cffi impersonate="chrome" để có TLS/HTTP fingerprint giống Chrome.
`requests`/urllib3 (JA3 bot) bị Google edge chặn cứng "403 Sorry" trên IP bị nghi ngờ;
curl_cffi-chrome thì qua (đã kiểm chứng trên IP .205.239). Đây là điều tool làm bằng native Schannel.
"""
import os, json, time
import uuid as _uuid
from curl_cffi import requests as _cffi

IMPERSONATE = "chrome"   # fingerprint Chrome mới nhất curl_cffi hỗ trợ

def _kw(proxy):
    kw = {"impersonate": IMPERSONATE}
    p = PROXY if proxy is None else proxy
    if p:
        kw["proxy"] = p
    elif DIRECT_IFACE:
        kw["interface"] = DIRECT_IFACE   # ép IPv4 máy (máy ưu tiên IPv6 -> POST lớn upload bị MTU-fail)
    return kw

def _post(url, headers, data, timeout=120, proxy=None):
    return _cffi.post(url, headers=headers, data=data, timeout=timeout, **_kw(proxy))

def _get(url, headers=None, timeout=60, proxy=None):
    return _cffi.get(url, headers=headers or {}, timeout=timeout, **_kw(proxy))

KEY = "AIzaSyBtrm0o5ab1c-Ec8ZuLcGt3oJAA5VWt3pY"
BASE = "https://aisandbox-pa.googleapis.com/v1"
# FORMAT THẮNG (đã ra 200): submit KHÔNG ?key (auth bằng bearer+Cookie). ?key + thiếu Cookie -> reCAPTCHA UNUSUAL.
GEN_T2V = f"{BASE}/video:batchAsyncGenerateVideoText"
GEN_I2V = f"{BASE}/video:batchAsyncGenerateVideoReferenceImages"
CHECK   = f"{BASE}/video:batchCheckAsyncVideoGenerationStatus?key={KEY}"
PROXY = None                  # upload/poll/download/khác: DIRECT = IP máy (nhanh, không WARP)
DIRECT_IFACE = "0.0.0.0"      # ép IPv4 máy (máy ưu tiên IPv6 -> POST lớn upload bị MTU reset)
GEN_PROXY = None              # CHỈ generate (gửi token captcha = chỗ 429) đi IPv6 pool

def set_proxy(url):
    global PROXY
    PROXY = url

def set_gen_proxy(url):
    """Chỉ generate (recaptcha submit) đi qua đây (IPv6 pool) để tránh 429. None = direct."""
    global GEN_PROXY
    GEN_PROXY = url
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/148.0.0.0 Safari/537.36"

MODEL_T2V_LITE = "veo_3_1_t2v_lite_low_priority"
MODEL_T2V_QUALITY = "veo_3_1_t2v"
MODEL_I2V_LITE = "veo_3_1_r2v_lite_low_priority"

# --- android_bypass (giải mã từ tool tst DgtCore.dll) ---
# Endpoint aisandbox CHẤP NHẬN token placeholder "android_bypass" + applicationType=ANDROID mà KHÔNG cần
# reCAPTCHA thật. Đây là toàn bộ khác biệt 40%->100%: KHÔNG mint captcha WEB (nguồn gây UNUSUAL). Chỉ khi
# request bypass fail (403/unusual) mới fallback mint token WEB thật. Token đi 1 mình trong body, KHÔNG cookie.
BYPASS_TOKEN = "android_bypass"
APP_TYPE_WEB = "RECAPTCHA_APPLICATION_TYPE_WEB"
APP_TYPE_ANDROID = "RECAPTCHA_APPLICATION_TYPE_ANDROID"
UA_FF = "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:151.0) Gecko/20100101 Firefox/151.0"


def _headers_ff(bearer):
    """Headers KHỚP tool tst (DgtCore AddVeo3SandboxHeaders): Firefox 151 UA, KHÔNG cookie/x-client-data.
    Dùng cho submit android_bypass (token đi 1 mình, không dính session cookie)."""
    return {"Authorization": f"Bearer {bearer}", "Content-Type": "text/plain;charset=UTF-8",
            "Accept": "*/*", "Accept-Language": "en-US,en;q=0.9,vi;q=0.8",
            "Origin": "https://labs.google", "Referer": "https://labs.google/",
            "User-Agent": UA_FF, "Cache-Control": "no-cache", "Pragma": "no-cache",
            "Priority": "u=1, i", "Sec-Fetch-Dest": "empty", "Sec-Fetch-Mode": "cors",
            "Sec-Fetch-Site": "cross-site", "X-Browser-Channel": "stable"}


def _headers(bearer, cookie=None):
    # Khớp CHÍNH XÁC request Chrome đã ra 200. QUAN TRỌNG: KHÔNG ép User-Agent — để curl_cffi impersonate='chrome'
    # tự set UA + Sec-Ch-Ua + TLS NHẤT QUÁN cùng 1 phiên bản. Ép UA=148 trong khi TLS/hints=149 -> LỆCH -> reCAPTCHA
    # chấm UNUSUAL (đã đo: có UA ép -> 0%; bỏ UA ép + thêm client-hints -> qua). Cookie buộc request vào session thật.
    h = {"Authorization": f"Bearer {bearer}", "Content-Type": "text/plain;charset=UTF-8",
         "Accept": "*/*", "Accept-Language": "en-US,vi;q=0.9",
         "Origin": "https://labs.google", "Referer": "https://labs.google/",
         "Priority": "u=1, i", "X-Client-Data": "CLuQywE=",
         "Sec-Ch-Ua": '"Chromium";v="149", "Google Chrome";v="149", "Not_A Brand";v="24"',
         "Sec-Ch-Ua-Mobile": "?0", "Sec-Ch-Ua-Platform": '"Windows"',
         "Sec-Fetch-Dest": "empty", "Sec-Fetch-Mode": "cors", "Sec-Fetch-Site": "cross-site"}
    if cookie:
        h["Cookie"] = cookie
    return h


def upload_image(bearer, project_id, session_id, image_path, aspect=None, timeout=120):
    """Upload ảnh -> mediaId (Image-to-Video). Endpoint /v1/flow/uploadImage, body:
    {clientContext:{sessionId,projectId,tool:PINHOLE}, imageBytes:<b64>}. Trả (mediaId, err)."""
    import base64
    with open(image_path, "rb") as f:
        b64 = base64.b64encode(f.read()).decode()
    url = f"{BASE}/flow/uploadImage?key={KEY}"
    payload = {"clientContext": {"sessionId": session_id, "projectId": project_id, "tool": "PINHOLE"},
               "imageBytes": b64}
    r = _post(url, _headers(bearer), json.dumps(payload), timeout=timeout)
    if r.status_code not in (200, 201):
        return None, f"{r.status_code}:{r.text[:80]}"
    j = r.json()
    media = j.get("media") or {}
    name = media.get("name") if isinstance(media, dict) else (media[0].get("name") if media else None)
    return (name, None) if name else (None, f"no media name: {json.dumps(j)[:120]}")


def build_payload(prompt, project_id, recaptcha_token, seed, aspect="VIDEO_ASPECT_RATIO_PORTRAIT",
                  model=None, reference_media_id=None, app_type=APP_TYPE_WEB):
    if model is None:
        model = MODEL_I2V_LITE if reference_media_id else MODEL_T2V_LITE
    # FORMAT MỚI (đã soi request thật + ra 200): structuredPrompt + mediaGenerationContext + useV2ModelConfig.
    req = {"aspectRatio": aspect, "seed": seed,
           "textInput": {"structuredPrompt": {"parts": [{"text": prompt}]}},
           "videoModelKey": model, "metadata": {}}
    if reference_media_id:
        req["referenceImages"] = [{"imageUsageType": "IMAGE_USAGE_TYPE_ASSET", "mediaId": reference_media_id}]
    return {
        "mediaGenerationContext": {"batchId": str(_uuid.uuid4()), "audioFailurePreference": "BLOCK_SILENCED_VIDEOS"},
        "clientContext": {
            "sessionId": f";{int(time.time()*1000)}", "projectId": project_id,
            "tool": "PINHOLE", "userPaygateTier": "PAYGATE_TIER_TWO",
            "recaptchaContext": {"applicationType": app_type, "token": recaptcha_token},
        },
        "requests": [req],
        "useV2ModelConfig": True,
    }


def classify(resp):
    """-> ('ok',json) | ('unusual',None) | ('ip_block',None) | ('auth',None) | ('other',text)"""
    if resp.status_code == 200:
        try: return "ok", resp.json()
        except Exception: return "other", resp.text[:100]
    if resp.status_code == 401:
        return "auth", None
    txt = resp.text
    if "<html" in txt.lower() or "Sorry" in txt[:200]:
        return "ip_block", None
    # 429 phân biệt 2 loại (khác HẲN cách xử lý):
    #  - "reCAPTCHA evaluation failed" / RESOURCE_EXHAUSTED / UNUSUAL_ACTIVITY = reCAPTCHA ĐÁNH GIÁ TRƯỢT
    #    -> "recaptcha_quota". Đổi IP/nghỉ account VÔ ÍCH; phải RETRY TOKEN MỚI kiên nhẫn (như veo3top ~50 lần)
    #    tới khi 1 token qua được assessment. LƯU Ý: body loại này CŨNG chứa "TOO_MUCH_TRAFFIC" trong reason
    #    (PUBLIC_ERROR_UNUSUAL_ACTIVITY_TOO_MUCH_TRAFFIC) -> phải check reCAPTCHA/RESOURCE_EXHAUSTED TRƯỚC.
    #  - TOO_MUCH_TRAFFIC "trần" (không kèm reCAPTCHA) = rate-limit theo IP -> "ratelimit" (đổi egress cứu được).
    if resp.status_code == 429 or "TOO_MUCH_TRAFFIC" in txt or "RESOURCE_EXHAUSTED" in txt:
        # PHÂN BIỆT bằng REASON (mã 429/RESOURCE_EXHAUSTED giống nhau cho NHIỀU loại — phải đọc reason):
        #  - USER_REQUESTS_THROTTLED = GIỚI HẠN TỐC ĐỘ/BURST (account-wide, hồi VÀI GIÂY; đã đo ngừng burst 1-2'
        #    -> submit lại 200 ngay). -> "throttle": nghỉ NGẮN + giảm submit đồng thời, TUYỆT ĐỐI KHÔNG cách ly dài.
        #  - RESOURCE_EXHAUSTED khác (hết quota MODEL/ngày — per-model, model khác vẫn 200) -> "recaptcha_quota".
        if "USER_REQUESTS_THROTTLED" in txt:
            return "throttle", None
        if "RESOURCE_EXHAUSTED" in txt or "reCAPTCHA" in txt or "UNUSUAL_ACTIVITY" in txt:
            return "recaptcha_quota", None
        return "ratelimit", None
    try:
        reason = resp.json()["error"]["details"][0]["reason"]
        if "USER_REQUESTS_THROTTLED" in reason:
            return "throttle", None
        if reason == "PUBLIC_ERROR_UNUSUAL_ACTIVITY":
            return "unusual", None
        if "TOO_MUCH_TRAFFIC" in reason:
            return "ratelimit", None
        if "RESOURCE_EXHAUSTED" in reason:
            return "recaptcha_quota", None
        return "other", reason
    except Exception:
        return "other", txt[:100]


_USE_GEN_PROXY = object()   # sentinel: caller không truyền proxy -> dùng GEN_PROXY (hành vi cũ)

def generate(bearer, payload, url=GEN_T2V, timeout=120, proxy=_USE_GEN_PROXY, cookie=None, bypass=False):
    # generate = gửi recaptcha token -> chỗ bị 429.
    # proxy: bỏ trống -> GEN_PROXY (mặc định IPv6). Truyền None -> DIRECT IPv4 máy. Truyền url -> WARP/khác.
    # -> cho phép EGRESS LADDER: thử ip máy(None) -> WARP(socks5://127.0.0.1:40000) -> IPv6 pool.
    # cookie: BẮT BUỘC để reCAPTCHA điểm cao (đã đo: thiếu Cookie -> UNUSUAL dù token tốt).
    # bypass=True: android_bypass (payload token='android_bypass', app_type=ANDROID) -> headers Firefox tst,
    # KHÔNG cookie. Đường CHÍNH cho cả video (đã đo 13/13 submit 200).
    p = GEN_PROXY if proxy is _USE_GEN_PROXY else proxy
    hdrs = _headers_ff(bearer) if bypass else _headers(bearer, cookie)
    r = _post(url, hdrs, json.dumps(payload), timeout=timeout, proxy=p)
    return classify(r)


def operation_names(gen_resp):
    names = []
    for o in gen_resp.get("operations", []):
        n = (o.get("operation") or {}).get("name")
        if n: names.append(n)
    if not names:
        for m in gen_resp.get("media", []):
            if m.get("name"): names.append(m["name"])
    if not names:
        for w in gen_resp.get("workflows", []):
            mid = (w.get("metadata") or {}).get("primaryMediaId")
            if mid: names.append(mid)
    return names


def _find_url(o):
    if isinstance(o, str) and o.startswith("http") and (
        "videofx" in o or ".mp4" in o or "storage.googleapis" in o):
        return o
    if isinstance(o, dict):
        for v in o.values():
            u = _find_url(v)
            if u: return u
    if isinstance(o, list):
        for v in o:
            u = _find_url(v)
            if u: return u
    return None


def _find_status(o, acc=None):
    acc = acc if acc is not None else []
    if isinstance(o, dict):
        for k, v in o.items():
            if k == "status" and isinstance(v, str): acc.append(v)
            _find_status(v, acc)
    elif isinstance(o, list):
        for x in o: _find_status(x, acc)
    return acc


def poll_until_done(bearer, op_names, max_attempts=90, interval=8, timeout=60):
    """batchCheck CHỈ trả status (không URL). Return 'done' khi SUCCESSFUL — caller tải
    bằng media_url_and_download(media_id, ...)."""
    body = {"operations": [{"operation": {"name": n}} for n in op_names]}
    for attempt in range(max_attempts):
        try:
            r = _post(CHECK, _headers(bearer), json.dumps(body), timeout=timeout)
        except Exception:
            time.sleep(interval); continue
        if r.status_code == 401:
            return "auth", None
        if r.status_code != 200:
            time.sleep(interval); continue
        statuses = _find_status(r.json())
        if any("SUCCESSFUL" in s or "SUCCEEDED" in s or "COMPLETE" in s for s in statuses):
            return "done", op_names[0]
        if any("FAIL" in s for s in statuses):
            return "failed", None
        time.sleep(interval)
    return "timeout", None


def download(url, dst, timeout=180):
    data = _get(url, timeout=timeout).content
    with open(dst, "wb") as f:
        f.write(data)
    return len(data)


# --- Lấy URL + tải video qua labs.google (cookie auth, DIRECT, không WARP) ---
_KEEP = ("next-auth", "__Secure", "__Host", "_ga", "EMAIL", "email")

def labs_cookie_header(cdp_cookies):
    """Lọc cookie labs.google (tránh 400 'cookie too large' do gửi cả google.com)."""
    return "; ".join(
        f"{c['name']}={c['value']}" for c in cdp_cookies
        if "labs.google" in c.get("domain", "") and any(k in c["name"] for k in _KEEP)
    )


def bearer_from_cookie(cookie, timeout=25):
    """Refresh bearer TỪ cookie labs.google (KHÔNG mở chrome) — giống veo3top.
    GET /fx/api/auth/session với cookie -> access_token. Cookie sống lâu (tuần), bearer ~1h.
    Trả (bearer, email) hoặc (None, None) nếu cookie hết hạn.
    
    ═══ BẢN VÁ 2026-08-10: TỰ REFRESH KHI PHIÊN HẾT HẠN ═══
    
    Phát hiện: khi NextAuth session-token hết hạn (expires < now), endpoint VẪN trả
    200 + access_token (stale, 401 khi dùng). NHƯNG response kèm Set-Cookie chứa
    session-token MỚI. Gọi lại với token mới → bearer TƯƠI, expires +24h.
    
    Trước bản vá: expires < now → trả (None, None) → phien_kiem kết án CHẾT →
    recovery mở Chrome login → fail vì thiếu module → nghỉ 1h → toàn bộ factory chết.
    
    Sau bản vá: expires < now → đọc Set-Cookie → thay session-token → gọi lại →
    bearer mới → KHÔNG CẦN Chrome. Giống người dùng bấm F5 trên trình duyệt."""
    if not cookie:
        return None, None
    H = {"Cookie": cookie, "User-Agent": UA, "Referer": "https://labs.google/", "Accept": "application/json"}
    kw = {"impersonate": IMPERSONATE, "timeout": timeout}
    if DIRECT_IFACE:
        kw["interface"] = DIRECT_IFACE
    try:
        r = _cffi.get("https://labs.google/fx/api/auth/session", headers=H, **kw)
        if r.status_code == 200:
            j = r.json() or {}
            # BUG FIX (2026-07-05): endpoint trả 200 + access_token CẢ KHI cookie ĐÃ HẾT HẠN (field "expires"
            # ở quá khứ) -> token đó CHẾT (401 khi submit). Trước bỏ qua "expires" -> tưởng refresh thành công nhưng
            # bearer chết -> account coi là sống rồi 401 hàng loạt.
            exp = j.get("expires")
            if exp:
                try:
                    from datetime import datetime
                    if datetime.fromisoformat(str(exp).replace("Z", "+00:00")).timestamp() < time.time() + 120:
                        # ═══ MỚI: thử TỰ REFRESH bằng Set-Cookie thay vì bỏ cuộc ═══
                        # NextAuth trả Set-Cookie chứa session-token MỚI khi session hết hạn.
                        # Lấy token mới -> gọi lại -> bearer tươi. KHÔNG cần Chrome.
                        refreshed = _try_refresh_from_set_cookie(r, cookie, kw)
                        if refreshed:
                            return refreshed  # (bearer, email) hoặc (bearer, email, new_cookie)
                        return None, None
                except Exception:
                    pass
            return j.get("access_token"), (j.get("user") or {}).get("email")
    except Exception:
        pass
    return None, None


def _try_refresh_from_set_cookie(response, old_cookie, kw):
    """Đọc Set-Cookie từ response, tìm session-token mới, gọi lại lấy bearer tươi.
    Trả (bearer, email) nếu thành công, None nếu không."""
    import re as _re
    set_cookie = response.headers.get("set-cookie", "")
    if not set_cookie:
        return None
    m = _re.search(r"__Secure-next-auth\.session-token=([^;]+)", set_cookie)
    if not m:
        return None
    new_token = m.group(1)
    # Thay session-token cũ bằng mới trong cookie string
    new_cookie = _re.sub(
        r"__Secure-next-auth\.session-token=[^;]+",
        "__Secure-next-auth.session-token=" + new_token,
        old_cookie
    )
    # Gọi lại với cookie mới
    try:
        H2 = {"Cookie": new_cookie, "User-Agent": UA, "Referer": "https://labs.google/", "Accept": "application/json"}
        r2 = _cffi.get("https://labs.google/fx/api/auth/session", headers=H2, **kw)
        if r2.status_code == 200:
            j2 = r2.json() or {}
            bearer = j2.get("access_token")
            email = (j2.get("user") or {}).get("email")
            exp2 = j2.get("expires", "")
            if bearer and exp2:
                from datetime import datetime
                if datetime.fromisoformat(str(exp2).replace("Z", "+00:00")).timestamp() > time.time() + 120:
                    # Cập nhật lại Set-Cookie nếu có (token có thể refresh lần 2)
                    sc2 = r2.headers.get("set-cookie", "")
                    m2 = _re.search(r"__Secure-next-auth\.session-token=([^;]+)", sc2)
                    if m2:
                        new_cookie = _re.sub(
                            r"__Secure-next-auth\.session-token=[^;]+",
                            "__Secure-next-auth.session-token=" + m2.group(1),
                            new_cookie
                        )
                    # Lưu cookie mới vào global để auth_cache._refresh_from_cookie có thể cập nhật
                    _LAST_REFRESHED_COOKIE[email or ""] = new_cookie
                    return bearer, email
    except Exception:
        pass
    return None


# Cache cookie mới nhất sau khi refresh (để auth_cache cập nhật)
_LAST_REFRESHED_COOKIE = {}


def get_refreshed_cookie(email):
    """Lấy cookie đã được refresh gần nhất cho email (nếu có). Dùng bởi auth_cache."""
    return _LAST_REFRESHED_COOKIE.pop(email, None)



def cookie_liveness(cookie, timeout=25):
    """PHÂN LOẠI cookie (KHÔNG chỉ sống/chết) — dùng để KHỎI wipe/login OAN khi endpoint rate-limit.
    Trả:
      'alive'     = 200 + access_token + expires còn hạn (cookie SỐNG chắc chắn).
      'dead'      = 200 nhưng KHÔNG token / expires quá khứ (BẰNG CHỨNG cookie hết hạn thật).
      'transient' = lỗi mạng/timeout HOẶC HTTP != 200 (429 rate-limit, 5xx...) -> KHÔNG kết luận chết,
                    cookie có thể vẫn sống. TUYỆT ĐỐI KHÔNG wipe/login vì cái này (chống churn 96->77 khi
                    96 account cùng poll session -> Google 429 -> tưởng chết hàng loạt)."""
    if not cookie:
        return "dead"   # không có cookie = out thật
    H = {"Cookie": cookie, "User-Agent": UA, "Referer": "https://labs.google/", "Accept": "application/json"}
    kw = {"impersonate": IMPERSONATE, "timeout": timeout}
    if DIRECT_IFACE:
        kw["interface"] = DIRECT_IFACE
    try:
        r = _cffi.get("https://labs.google/fx/api/auth/session", headers=H, **kw)
    except Exception:
        return "transient"          # mạng/timeout -> KHÔNG kết luận
    if r.status_code != 200:
        return "transient"          # 429 rate-limit / 5xx -> KHÔNG kết luận (cookie có thể vẫn sống)
    try:
        j = r.json() or {}
    except Exception:
        return "transient"
    tok = j.get("access_token")
    if not tok:
        return "dead"               # 200 nhưng không token = phiên hết -> chết thật
    exp = j.get("expires")
    if exp:
        try:
            from datetime import datetime
            if datetime.fromisoformat(str(exp).replace("Z", "+00:00")).timestamp() < time.time() + 120:
                # BẢN VÁ 2026-08-10: Trước đây trả "dead" ngay — nhưng NextAuth thường trả
                # Set-Cookie chứa session-token MỚI. Nếu có -> cookie CÒN CỨU ĐƯỢC (bearer_from_cookie
                # sẽ tự refresh) -> trả "alive" thay vì "dead" để phien_kiem KHÔNG kết án oan.
                import re as _re
                sc = r.headers.get("set-cookie", "")
                if sc and _re.search(r"__Secure-next-auth\.session-token=", sc):
                    return "alive"  # phiên hết hạn nhưng CÓ THỂ tự refresh → không kết án
                return "dead"        # 200 + token nhưng expires quá khứ VÀ không refresh được = chết thật
        except Exception:
            pass
    return "alive"


def _labs_kw(timeout):
    kw = {"impersonate": IMPERSONATE, "timeout": timeout}
    if DIRECT_IFACE:
        kw["interface"] = DIRECT_IFACE
    return kw

def list_projects(cookie, timeout=25):
    """Danh sách projectId (PINHOLE) của account qua COOKIE labs.google — KHÔNG mở chrome.
    Dùng để ĐỔI/ROTATE project (1 account nhiều project -> tránh dùng mãi 1 project bị đốt)."""
    if not cookie:
        return []
    import urllib.parse as _upa
    inp = _upa.quote(json.dumps({"json": {"pageSize": 20, "toolName": "PINHOLE", "cursor": None},
                                 "meta": {"values": {"cursor": ["undefined"]}}}))
    url = "https://labs.google/fx/api/trpc/project.searchUserProjects?input=" + inp
    H = {"Cookie": cookie, "User-Agent": UA, "Referer": "https://labs.google/", "Accept": "application/json"}
    try:
        r = _cffi.get(url, headers=H, **_labs_kw(timeout))
        projs = (((r.json() or {}).get("result") or {}).get("data") or {}).get("json", {}).get("result", {}).get("projects", [])
        return [p["projectId"] for p in projs if p.get("projectId")]
    except Exception:
        return []

def create_project(cookie, title="auto", timeout=25):
    """Tạo project PINHOLE MỚI qua COOKIE labs.google — KHÔNG mở chrome. Trả projectId hoặc None."""
    if not cookie:
        return None
    url = "https://labs.google/fx/api/trpc/project.createProject"
    H = {"Cookie": cookie, "User-Agent": UA, "Referer": "https://labs.google/",
         "Content-Type": "application/json", "Accept": "application/json"}
    body = json.dumps({"json": {"projectTitle": title, "toolName": "PINHOLE"}})
    try:
        r = _cffi.post(url, headers=H, data=body, **_labs_kw(timeout))
        d = (((r.json() or {}).get("result") or {}).get("data") or {}).get("json") or {}
        return d.get("projectId") or (d.get("result") or {}).get("projectId")
    except Exception:
        return None

# =========================================================================
# ẢNH (flowMedia:batchGenerateImages) — bắn thẳng như video, SYNCHRONOUS (200 = có ảnh luôn).
# Contract đã kiểm chứng chạy thật 200 OK (2026-07-01):
#   POST /v1/projects/{projectId}/flowMedia:batchGenerateImages?key=KEY
#   recaptcha = clientContext.recaptchaContext.token, action='IMAGE_GENERATION' (KHÁC video 'VIDEO_GENERATION').
#   generate đi GEN_PROXY (IPv6, chỗ 429 TOO_MUCH_TRAFFIC); download fifeUrl DIRECT IPv4 (URL ký sẵn, không auth).
#   Response: media[0].image.generatedImage.fifeUrl (JPEG ký), media[0].name = mediaId (dùng làm reference).
# =========================================================================
# Model ảnh: GEM_PIX_2 (Nano Banana Pro, default) | NARWHAL (Nano Banana 2) | IMAGEN_3_5 (Imagen 4).
# LƯU Ý: 429 "reCAPTCHA evaluation failed" chặn Ở TẦNG reCAPTCHA (trước model) -> ĐỔI MODEL KHÔNG né
# được 429 đó (đã kiểm chứng: GEM_PIX_2 & NARWHAL đều 429; IMAGEN_3_5 ra 500). Env chỉ để thử nghiệm.
IMG_MODEL = os.environ.get("VEO3TOP_IMG_MODEL", "GEM_PIX_2") or "GEM_PIX_2"
IMG_ASPECT_LANDSCAPE = "IMAGE_ASPECT_RATIO_LANDSCAPE"
IMG_ASPECT_PORTRAIT  = "IMAGE_ASPECT_RATIO_PORTRAIT"
IMG_ASPECT_SQUARE    = "IMAGE_ASPECT_RATIO_SQUARE"


def image_gen_url(project_id):
    return f"{BASE}/projects/{project_id}/flowMedia:batchGenerateImages"   # format MỚI: KHÔNG ?key


def build_image_payload(prompt, project_id, recaptcha_token, seed,
                        aspect=IMG_ASPECT_LANDSCAPE, model=IMG_MODEL, image_inputs=None,
                        app_type=APP_TYPE_WEB):
    """FORMAT MỚI (đã soi request thật): structuredPrompt + mediaGenerationContext.batchId + useNewMedia.
    image_inputs: list dict {imageInputType, name} (ref đã UPLOAD -> name; KHÔNG dùng rawImageBytes nữa).
    app_type: WEB (token reCAPTCHA thật) | ANDROID (dùng với recaptcha_token='android_bypass' -> bỏ qua captcha)."""
    import uuid as _uuid
    ctx = {"recaptchaContext": {"token": recaptcha_token, "applicationType": app_type},
           "projectId": project_id, "tool": "PINHOLE", "sessionId": f";{int(time.time()*1000)}"}
    req = {"clientContext": dict(ctx), "imageModelName": model, "imageAspectRatio": aspect,
           "structuredPrompt": {"parts": [{"text": prompt}]}, "seed": seed, "imageInputs": image_inputs or []}
    return {"clientContext": ctx,
            "mediaGenerationContext": {"batchId": str(_uuid.uuid4())},
            "useNewMedia": True, "requests": [req]}


def generate_image(bearer, project_id, payload, timeout=120, proxy=None, cookie=None, bypass=False):
    """POST batchGenerateImages qua IPv6 pool. Trả classify() giống video:
    ('ok',json)|('ratelimit',None)|('recaptcha_quota',None)|('unusual',None)|('ip_block',None)|('auth',None)|('other',txt).
    proxy=None -> dùng GEN_PROXY toàn cục (như video). Truyền proxy riêng để KHÔNG phụ thuộc/đụng
    GEN_PROXY của video khi chạy cùng process (image provider truyền transport riêng của nó).
    cookie: như video — buộc request vào session -> reCAPTCHA điểm cao hơn (thử nghiệm mang logic video sang ảnh).
    bypass=True: submit android_bypass (payload token='android_bypass', app_type=ANDROID) -> headers Firefox tst,
    KHÔNG cookie (token đi 1 mình). Đây là đường CHÍNH (không đụng reCAPTCHA)."""
    p = GEN_PROXY if proxy is None else proxy
    hdrs = _headers_ff(bearer) if bypass else _headers(bearer, cookie)
    try:
        r = _post(image_gen_url(project_id), hdrs, json.dumps(payload), timeout=timeout, proxy=p)
    except Exception as e:
        # Lỗi mạng (proxy IPv6 rớt/abort, timeout...) -> báo retryable, KHÔNG cho crash scene.
        # Coi như per-IP hỏng để provider đổi IPv6 + thử token mới (giống ratelimit).
        return "ratelimit", None
    kind, data = classify(r)
    # Tinh chỉnh cho ẢNH: status RESOURCE_EXHAUSTED = HẠN MỨC đánh giá reCAPTCHA sitekey/action (TOÀN CỤC)
    # -> đổi IP VÔ ÍCH, phải CHỜ quota hồi. classify() trả "ratelimit" do body chứa TOO_MUCH_TRAFFIC ->
    # ép về "recaptcha_quota" để provider KHÔNG xoay IPv6 (đỡ đốt pool), chỉ back off + nghỉ chờ quota.
    if kind == "ratelimit" and r.status_code == 429:
        try:
            if r.json().get("error", {}).get("status") == "RESOURCE_EXHAUSTED":
                return "recaptcha_quota", None
        except Exception:
            pass
    return kind, data


def image_result(gen_resp):
    """Bóc (media_name, fife_url, encoded_b64, seed) từ response generate ảnh (200). Tất cả None nếu không có."""
    for m in gen_resp.get("media", []) or []:
        gi = (m.get("image") or {}).get("generatedImage") or {}
        fife = gi.get("fifeUrl")
        name = m.get("name") or gi.get("mediaId") or gi.get("mediaGenerationId")
        if fife or gi.get("encodedImage"):
            return name, fife, gi.get("encodedImage"), gi.get("seed")
    return None, None, None, None


def download_image_url(fife_url, dst, timeout=120):
    """Tải ảnh từ fifeUrl (flow-content.google, URL ký sẵn) — DIRECT IPv4, không auth. Trả số byte."""
    data = _get(fife_url, timeout=timeout).content
    d = os.path.dirname(dst)
    if d:
        os.makedirs(d, exist_ok=True)
    with open(dst, "wb") as f:
        f.write(data)
    return len(data)


def media_url_and_download(media_id, labs_cookie, dst, timeout=180):
    """getMediaUrlRedirect (cookie labs.google) -> 302 -> mp4. DIRECT (không WARP). Trả số byte."""
    H = {"Cookie": labs_cookie, "User-Agent": UA, "Referer": "https://labs.google/", "Accept": "*/*"}
    # DIRECT IPv4 máy (labs.google không bị IP-gate) — ép IPv4 tránh IPv6; fingerprint chrome
    kw = {"impersonate": IMPERSONATE, "timeout": timeout, "allow_redirects": True}
    if DIRECT_IFACE:
        kw["interface"] = DIRECT_IFACE
    r = _cffi.get(f"https://labs.google/fx/api/trpc/media.getMediaUrlRedirect?name={media_id}",
                  headers=H, **kw)
    if r.status_code == 200 and r.headers.get("content-type", "").startswith("video"):
        with open(dst, "wb") as f:
            f.write(r.content)
        return len(r.content), r.url
    raise RuntimeError(f"download failed {r.status_code}: {r.text[:120]}")
