# FlowKit Architecture — Phien ban hoat dong (commit 640e838)

> Ghi chu nay mo ta cach FlowKit hoat dong de co the quay lai khi gap loi.
> Neu lam sai logic, revert ve commit `640e838` la phien ban da test ok ca anh va video.

## Tong quan

FlowKit la he thong proxy API Google Flow thong qua Chrome Extension.
VE3 (may chu chinh) gui request → FlowKit server (may khac) → Chrome extension goi Google API.

```
VE3 (192.168.88.xxx)          FlowKit Server (192.168.88.145)
      |                              |
      |  POST /api/fix/create-image  |
      |  {flow_auth_token, body_json}|
      |----------------------------->|
      |                         Gateway (:5100)
      |                              |
      |                         Agent (:8100)
      |                              |
      |                         Extension (Chrome)
      |                              |
      |                         Google API
      |                         (aisandbox-pa.googleapis.com)
```

## Thanh phan

### 1. Gateway (`server/flowkit/gateway.py` — port 5100)

API duy nhat ma VE3 goi. Tuong thich voi API cu (`server/app.py`).

**Endpoints chinh:**
- `POST /api/fix/create-image-veo3` — Tao anh
- `POST /api/fix/create-video-veo3` — Tao video
- `GET  /api/fix/task-status?taskId=xxx` — Kiem tra trang thai
- `GET  /api/fix/video-file/{media_id}` — Tai file video (I2V workflow)
- `POST /api/upload-image` — Upload anh reference
- `GET  /api/gateway-status` — Trang thai gateway + instances

**Cach hoat dong:**
1. Nhan request tu VE3 voi `flow_auth_token` (bearer token) va `body_json`
2. Tao `task_id`, luu vao `tasks` dict
3. Chon instance kha dung (round-robin, uu tien it viec)
4. Chuyen request toi Agent (POST /api/generate-image hoac /api/generate-video)
5. Tra `taskId` cho VE3 ngay, xu ly async

**Video polling:**
- T2V (text-to-video): Tra ve `operations` → gateway poll qua `/api/poll-video` cua agent
- I2V (image-to-video): Tra ve `workflows` + `primaryMediaId` → gateway poll bang 2 cach:
  - Method 1: Direct Google API GET (dung bearer_token tu VE3)
  - Method 2: Qua extension `/api/check-media` (dung flowKey cua extension)
- Khi I2V tra ve video dang base64 (`encodedVideo`), gateway decode, luu file MP4,
  tra URL dang `http://127.0.0.1:5100/api/fix/video-file/{media_id}`

**QUAN TRONG — URL rewrite:**
Gateway tra URL dang `127.0.0.1`. VE3 o may khac khong truy cap duoc.
Fix nam o `google_flow_api.py` → `_fix_localhost_url()`: thay `127.0.0.1:PORT` thanh IP server thuc.

**403 handling:**
- Moi instance co `consecutive_403` counter
- Khi 403: `mark_403()` → tang counter → neu >= 3 lan → cooldown 300 giay
- Gateway thu instance khac (toi da 3 retries)
- Khi tat ca instance cooling: tra loi "All instances cooling"

**401 handling:**
- Gateway prefix `[401]` hoac `[429]` vao error message
- VE3 detect "401" trong error → refresh token → retry

### 2. Agent (`server/flowkit/agent/main.py` — port 8100+)

Moi Agent = 1 Chrome instance. Nhan request tu Gateway, chuyen cho Extension qua WebSocket.

**Endpoints:**
- `POST /api/generate-image` — Tao anh
- `POST /api/generate-video` — Tao video
- `POST /api/poll-video` — Poll trang thai video (T2V)
- `POST /api/check-media` — Kiem tra media (I2V)
- `POST /api/upload-image` — Upload anh
- `POST /api/reset-captcha` — Reset captcha (clear data extension)
- `GET  /health` — Trang thai agent

**Cach hoat dong:**
1. Nhan `bearer_token` + `body_json` tu gateway
2. Them `recaptchaContext` vao body neu chua co (`_ensure_recaptcha_context`)
3. Gui qua WebSocket cho extension: `{method: "api_request", params: {url, body, bearerToken, captchaAction}}`
4. Extension tra ket qua qua WebSocket
5. Agent tra ve cho gateway

### 3. Extension (`flowkit_extensions/ext_XXXX/background.js`)

Chrome Extension (Manifest V3 service worker). Lam 3 viec:
1. **Bat token**: Intercept `Authorization: Bearer ya29.*` tu Chrome requests → luu lam `flowKey`
2. **Giai captcha**: Inject content.js vao Flow tab → lay reCAPTCHA token
3. **Proxy API call**: Goi Google API tu browser context (bypass CORS, dung cookies Chrome)

**7 extension co cung code, chi khac port:**
| Extension | Agent WS Port | Agent API Port |
|-----------|---------------|----------------|
| ext_8100  | 9222          | 8100           |
| ext_8101  | 9223          | 8101           |
| ext_8102  | 9224          | 8102           |
| ext_8103  | 9225          | 8103           |
| ext_8104  | 9226          | 8104           |
| ext_8105  | 9227          | 8105           |
| ext_8106  | 9228          | 8106           |

**Token logic (DA FIX — commit 640e838):**
```javascript
// handleApiRequest():
const { url, method, headers, body, captchaAction, bearerToken } = params;
// ...
const activeFlowKey = bearerToken || flowKey;
// bearerToken = token VE3 gui qua (uu tien)
// flowKey = token extension bat tu Chrome (fallback)
```

**Truoc fix:** Extension luon dung `flowKey` rieng, bo qua `bearerToken` tu VE3.
**Sau fix:** Uu tien `bearerToken` tu VE3. Neu khong co thi dung `flowKey`.

**handleResetCaptcha:**
1. Clear cookies/cache/localStorage cho labs.google va aisandbox-pa.googleapis.com
2. Reload tat ca Flow tabs (bypassCache)
3. Doi 8 giay
4. Re-inject content.js
5. Re-capture token
6. Clear flowKey (force re-capture)

### 4. FlowClient (`server/flowkit/agent/services/flow_client.py`)

Quan ly WebSocket giua Agent va Extension.
- `set_extension(ws)` — Extension connect
- `handle_message(data)` — Xu ly message tu extension (token_captured, response, v.v.)
- `send(method, params, timeout)` — Gui request va doi response

## Flow chi tiet: Tao anh

```
VE3                     Gateway              Agent              Extension          Google
 |                        |                    |                    |                 |
 |  POST create-image     |                    |                    |                 |
 |  {flow_auth_token,     |                    |                    |                 |
 |   body_json, flow_url} |                    |                    |                 |
 |----------------------->|                    |                    |                 |
 |  {taskId: "abc"}       |                    |                    |                 |
 |<-----------------------|                    |                    |                 |
 |                        | POST generate-image|                    |                 |
 |                        | {bearer_token,     |                    |                 |
 |                        |  body_json}        |                    |                 |
 |                        |------------------>|                    |                 |
 |                        |                    | WS: api_request    |                 |
 |                        |                    | {bearerToken,      |                 |
 |                        |                    |  captchaAction}    |                 |
 |                        |                    |------------------>|                 |
 |                        |                    |                    | solveCaptcha()  |
 |                        |                    |                    | inject token    |
 |                        |                    |                    |                 |
 |                        |                    |                    | fetch(url, {    |
 |                        |                    |                    |   auth: Bearer  |
 |                        |                    |                    |   bearerToken}) |
 |                        |                    |                    |--------------->|
 |                        |                    |                    |   {media: [...]}|
 |                        |                    |                    |<---------------|
 |                        |                    | WS: {status, data} |                 |
 |                        |                    |<------------------|                 |
 |                        | {success, result}  |                    |                 |
 |                        |<------------------|                    |                 |
 |                        | tasks[abc]=done    |                    |                 |
 |                        |                    |                    |                 |
 |  GET task-status       |                    |                    |                 |
 |  ?taskId=abc           |                    |                    |                 |
 |----------------------->|                    |                    |                 |
 |  {success, result}     |                    |                    |                 |
 |<-----------------------|                    |                    |                 |
```

## Flow chi tiet: Tao video (I2V)

```
VE3 → Gateway → Agent → Extension → Google
                                      |
                                      | {workflows, primaryMediaId}
                                      |
Gateway bat dau poll:
  Loop moi 10 giay, timeout 420 giay:
    Method 1: GET truc tiep Google API (dung bearer_token VE3)
      GET /v1/media/{mediaId}?key=API_KEY
      Header: Authorization: Bearer {bearer_token_tu_VE3}
    
    Method 2 (fallback): Qua extension
      POST agent/api/check-media {media_id}
      Extension dung flowKey rieng de goi
    
    Khi co encodedVideo (base64):
      → Decode → luu MP4 → tra URL /api/fix/video-file/{mediaId}
    
    Khi co fifeUrl:
      → Tra URL truc tiep

VE3 nhan URL video:
  _fix_localhost_url() thay 127.0.0.1 → IP server thuc
  Download video tu URL da fix
```

## Token va Auth

**2 loai token:**
1. `bearer_token` (VE3 gui) = OAuth2 token `ya29.*` cua tai khoan VE3
2. `flowKey` (extension bat) = OAuth2 token `ya29.*` tu Chrome tren server

**Sau fix 640e838:** Extension uu tien `bearerToken` tu VE3.
Nghia la khi VE3 refresh token → server dung token moi ngay.

**Auth refresh flow (khi 401):**
1. Server tra [401] error
2. VE3 detect "401" trong error
3. VE3 goi `_refresh_flow_auth()` → `ensure_auth(force_refresh=True)`
4. `ensure_auth` tao project MOI (throwaway) de bat token moi
5. Giu nguyen project_id/url cu
6. Retry voi token moi

## Config (`server/flowkit/config.yaml`)

```yaml
gateway_port: 5100          # Port gateway
instances:                  # Danh sach Chrome instances
  - name: flowkit-1
    api_port: 8100          # Agent API port
    ws_port: 9222           # WebSocket cho extension
    chrome_path: ...        # Duong dan Chrome Portable
    profile_dir: ...        # Profile Chrome
    extension_dir: ...      # Thu muc extension
    enabled: true

rotation:
  max_consecutive_403: 3    # Bao nhieu lan 403 thi cooldown
  cooldown_seconds: 300     # Thoi gian cooldown (giay)
  max_retries_per_request: 3  # So lan retry toi da

rate_limit:
  cooldown_per_instance: 5  # Khoang cach giua cac request (giay)
  max_concurrent_per_instance: 1  # Toi da request dong thoi

timeouts:
  image_generation: 120     # Timeout tao anh (giay)
  video_submit: 60          # Timeout submit video
  video_poll: 420           # Timeout poll video (7 phut)
  video_poll_interval: 10   # Khoang cach poll (giay)
```

## Troubleshooting

### "Failed to fetch"
- Extension khong goi duoc Google API
- Check: Chrome tren server co internet khong?
- Thu: Reload extension trong chrome://extensions/
- Thu: Mo Flow tab → doi extension bat token moi
- Thu: Tat Chrome → mo lai

### 401 Token het han
- VE3 tu dong refresh token va retry (da fix)
- Neu van loi: Kiem tra VE3 co mo duoc Chrome de lay token moi khong

### 403 reCAPTCHA
- Gateway tu dong rotate instance + cooldown
- Hien tai CHUA co auto-reset captcha (can lam them)
- Thu cong: Reload extension → mo Flow tab → thu lai

### Video download that bai
- Kiem tra `_fix_localhost_url()` trong `google_flow_api.py`
- URL phai la IP server thuc, khong phai 127.0.0.1
- Check: `local_server_url` trong settings.yaml co dung IP khong

### I2V timeout
- I2V mat 60-450 giay tuy server
- Gateway dung dual polling (direct + extension)
- Neu chi 1 method hoat dong van ok

## Cau truc file

```
server/flowkit/
  gateway.py              # Gateway chinh (port 5100)
  config.yaml             # Cau hinh
  agent/
    main.py               # Agent API (port 8100+)
    services/
      flow_client.py      # WebSocket client → extension
  flowkit_extensions/
    ext_8100/             # Extension cho agent 1
      background.js       # Service worker chinh
      content.js          # Inject vao Flow tab
      injected.js         # Inject vao page context
      manifest.json       # Chrome extension manifest
      popup.html/js       # Popup UI
      rules.json          # Declarative net request rules
    ext_8101-8106/        # Giong ext_8100, khac port

flowkit_extensions/       # BAN GOC cua extension (may VE3)
  ext_8100-8106/          # Copy sang server khi deploy

tools/ve3/
  modules/
    google_flow_api.py    # Client goi FlowKit gateway
      _fix_localhost_url()  # Fix URL 127.0.0.1 → IP server
    flow_runtime_auth.py  # Auth management
    flow_reference_bridge.py  # Mo Chrome + lay token
  ve3_worker.py           # Worker chinh, 401 detection + retry
```

## Revert

Neu moi thu hong, quay lai phien ban nay:
```bash
git checkout 640e838
```

Hoac chi revert 1 file:
```bash
git checkout 640e838 -- server/flowkit/gateway.py
git checkout 640e838 -- flowkit_extensions/ext_8100/background.js
```
