"""
Flow API client — bắn thẳng aisandbox-pa (Veo 3.1) qua WARP socks5.
Contract đã xác nhận chạy thật (xem VEO3TOP_MECHANISM_NOTES.md).

QUAN TRỌNG: dùng curl_cffi impersonate="chrome" để có TLS/HTTP fingerprint giống Chrome.
`requests`/urllib3 (JA3 bot) bị Google edge chặn cứng "403 Sorry" trên IP bị nghi ngờ;
curl_cffi-chrome thì qua (đã kiểm chứng trên IP .205.239). Đây là điều tool làm bằng native Schannel.
"""
import os, json, time
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
GEN_T2V = f"{BASE}/video:batchAsyncGenerateVideoText?key={KEY}"
GEN_I2V = f"{BASE}/video:batchAsyncGenerateVideoReferenceImages?key={KEY}"
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


def _headers(bearer):
    return {"Authorization": f"Bearer {bearer}", "Content-Type": "text/plain;charset=UTF-8",
            "Origin": "https://labs.google", "Referer": "https://labs.google/", "User-Agent": UA}


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
                  model=None, reference_media_id=None):
    if model is None:
        model = MODEL_I2V_LITE if reference_media_id else MODEL_T2V_LITE
    req = {"aspectRatio": aspect, "seed": seed, "textInput": {"prompt": prompt}, "videoModelKey": model}
    if reference_media_id:
        req["referenceImages"] = [{"imageUsageType": "IMAGE_USAGE_TYPE_ASSET", "mediaId": reference_media_id}]
    return {
        "clientContext": {
            "sessionId": f";{int(time.time()*1000)}", "projectId": project_id,
            "tool": "PINHOLE", "userPaygateTier": "PAYGATE_TIER_TWO",
            "recaptchaContext": {"applicationType": "RECAPTCHA_APPLICATION_TYPE_WEB", "token": recaptcha_token},
        },
        "requests": [req],
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
        if "RESOURCE_EXHAUSTED" in txt or "reCAPTCHA" in txt or "UNUSUAL_ACTIVITY" in txt:
            return "recaptcha_quota", None
        return "ratelimit", None
    try:
        reason = resp.json()["error"]["details"][0]["reason"]
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

def generate(bearer, payload, url=GEN_T2V, timeout=120, proxy=_USE_GEN_PROXY):
    # generate = gửi recaptcha token -> chỗ bị 429.
    # proxy: bỏ trống -> GEN_PROXY (mặc định IPv6). Truyền None -> DIRECT IPv4 máy. Truyền url -> WARP/khác.
    # -> cho phép EGRESS LADDER: thử ip máy(None) -> WARP(socks5://127.0.0.1:40000) -> IPv6 pool.
    p = GEN_PROXY if proxy is _USE_GEN_PROXY else proxy
    r = _post(url, _headers(bearer), json.dumps(payload), timeout=timeout, proxy=p)
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
    Trả (bearer, email) hoặc (None, None) nếu cookie hết hạn."""
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
            return j.get("access_token"), (j.get("user") or {}).get("email")
    except Exception:
        pass
    return None, None

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
                        aspect=IMG_ASPECT_LANDSCAPE, model=IMG_MODEL, image_inputs=None):
    """FORMAT MỚI (đã soi request thật): structuredPrompt + mediaGenerationContext.batchId + useNewMedia.
    image_inputs: list dict {imageInputType, name} (ref đã UPLOAD -> name; KHÔNG dùng rawImageBytes nữa)."""
    import uuid as _uuid
    ctx = {"recaptchaContext": {"token": recaptcha_token, "applicationType": "RECAPTCHA_APPLICATION_TYPE_WEB"},
           "projectId": project_id, "tool": "PINHOLE", "sessionId": f";{int(time.time()*1000)}"}
    req = {"clientContext": dict(ctx), "imageModelName": model, "imageAspectRatio": aspect,
           "structuredPrompt": {"parts": [{"text": prompt}]}, "seed": seed, "imageInputs": image_inputs or []}
    return {"clientContext": ctx,
            "mediaGenerationContext": {"batchId": str(_uuid.uuid4())},
            "useNewMedia": True, "requests": [req]}


def generate_image(bearer, project_id, payload, timeout=120, proxy=None):
    """POST batchGenerateImages qua IPv6 pool. Trả classify() giống video:
    ('ok',json)|('ratelimit',None)|('recaptcha_quota',None)|('unusual',None)|('ip_block',None)|('auth',None)|('other',txt).
    proxy=None -> dùng GEN_PROXY toàn cục (như video). Truyền proxy riêng để KHÔNG phụ thuộc/đụng
    GEN_PROXY của video khi chạy cùng process (image provider truyền transport riêng của nó)."""
    p = GEN_PROXY if proxy is None else proxy
    try:
        r = _post(image_gen_url(project_id), _headers(bearer), json.dumps(payload), timeout=timeout, proxy=p)
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
