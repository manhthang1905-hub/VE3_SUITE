# FlowKit Architecture — Phien ban 403 Recovery (commit 0c37a8d+)

> Ghi chu nay mo ta cach FlowKit hoat dong de co the quay lai khi gap loi.
> Phien ban stable truoc: commit `640e838` (chua co 403 recovery).
> Phien ban hien tai: commit `0c37a8d` (403 recovery + fingerprint + retry-on-404).

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
- `POST /api/fix/upload-image` — Upload anh reference
- `POST /api/fix/reset-captcha` — Trigger 403 recovery cho tat ca instance dang cooling
- `GET  /api/status` — Trang thai gateway + instances
- `GET  /api/instances` — Chi tiet tung instance
- `GET  /health` — Health check (VE3-compatible)
- `GET  /api/recovery-status` — Trang thai recovery manager
- `POST /api/trigger-recovery/{name}` — Trigger recovery thu cong
- `POST /api/reset-instance/{name}` — Reset cooldown/recovery

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
  - Method 2 (fallback): Qua extension `/api/check-media` (dung flowKey cua extension)
- Khi I2V tra ve video dang base64 (`encodedVideo`), gateway decode, luu file MP4,
  tra URL dang `http://127.0.0.1:5100/api/fix/video-file/{media_id}`

**QUAN TRONG — URL rewrite:**
Gateway tra URL dang `127.0.0.1`. VE3 o may khac khong truy cap duoc.
Fix nam o `google_flow_api.py` → `_fix_localhost_url()`: thay `127.0.0.1:PORT` thanh IP server thuc.

**403 handling + Recovery (MOI — commit 0c37a8d):**
- Moi instance co `consecutive_403` counter
- Khi 403: `mark_403()` → tang counter → neu >= 3 lan → cooldown 300 giay
- Gateway thu instance khac (toi da 3 retries cho image)
- Khi instance vao cooldown → **tu dong trigger RecoveryManager**
- Recovery chay background, escalation 3 cap (xem muc "403 Recovery System" ben duoi)
- Khi recovery thanh cong → clear cooldown, instance kha dung lai

**Retry-on-404 (MOI — commit 0c37a8d):**
- Video task bi 404 (Google eventual consistency): gateway retry toi da 2 lan, moi lan doi 5 giay
- Xay ra khi upload reference image xong nhung Google chua propagate

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

### 3. Extension (`flowkit_extensions/ext_XXXX/`)

Chrome Extension (Manifest V3 service worker). Lam 4 viec:
1. **Bat token**: Intercept `Authorization: Bearer ya29.*` tu Chrome requests → luu lam `flowKey`
2. **Giai captcha**: Inject content.js vao Flow tab → lay reCAPTCHA token
3. **Proxy API call**: Goi Google API tu browser context (bypass CORS, dung cookies Chrome)
4. **Fingerprint injection**: `fp_inject.js` chay truoc tat ca scripts, spoof browser fingerprint (MOI)

**Content scripts (thu tu load):**
1. `fp_inject.js` — `document_start`, `world: MAIN` → spoof WebGL, Canvas, Hardware, Screen, Audio
2. `content.js` — `document_start` → inject `injected.js`, bat events tu page

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

### 4. FlowClient (`server/flowkit/agent/services/flow_client.py`)

Quan ly WebSocket giua Agent va Extension.
- `set_extension(ws)` — Extension connect
- `handle_message(data)` — Xu ly message tu extension (token_captured, response, v.v.)
- `send(method, params, timeout)` — Gui request va doi response

### 5. RecoveryManager (`server/flowkit/recovery_manager.py` — MOI)

Quan ly tu dong recovery khi instance bi 403 lien tiep.

**3 cap escalation:**

| Level | Hanh dong | Mo ta |
|-------|-----------|-------|
| 1 | Reset Captcha | Goi agent `/api/reset-captcha` → clear cookies/cache, reload Flow tab, re-capture token |
| 2 | Rotate IPv6 | Lay IPv6 moi tu pool (MikroTik) + restart Chrome voi proxy moi |
| 3 | Full Restart | Kill Chrome + tao fingerprint moi + restart Chrome (+ IPv6 moi neu co) |

**Cau hinh (config.yaml > recovery):**
```yaml
recovery:
  level1_max_attempts: 2      # So lan thu Level 1 truoc khi len Level 2
  level2_max_attempts: 2      # So lan thu Level 2 truoc khi len Level 3
  level3_max_attempts: 3      # So lan thu Level 3 truoc khi bao "can manual"
  min_recovery_interval: 30   # Khoang cach toi thieu giua 2 lan recovery (giay)
  extension_reconnect_timeout: 30  # Timeout doi extension ket noi lai (giay)
  chrome_restart_delay: 5     # Doi sau khi restart Chrome (giay)
```

**Flow:**
```
Instance bi 403 x3 → cooldown → trigger_recovery()
    |
    v
Level 1: Reset captcha via agent
    ├─ Thanh cong → clear cooldown, instance kha dung lai
    └─ That bai → Level 2
            |
            v
      Level 2: Rotate IPv6 + restart Chrome
            ├─ Thanh cong → clear cooldown
            └─ That bai → Level 3
                    |
                    v
              Level 3: Kill Chrome + fingerprint moi + restart
                    ├─ Thanh cong → clear cooldown
                    └─ That bai → "Can xu ly thu cong"
```

**Callbacks:**
- `on_instance_success(name)` — Reset recovery state khi request thanh cong
- `on_cooldown_clear` — Gateway callback de clear cooldown cua instance

### 6. Fingerprint System (`server/flowkit/fingerprint_data.py` — MOI)

Tao browser fingerprint unique cho moi Chrome instance.

**53.6 trieu fingerprint khac nhau tu:**
- 65 GPU models (NVIDIA, AMD, Intel)
- 15 screen resolutions (1920x1080 → 3840x2160)
- 11 CPU core counts (4 → 64)
- 5 memory options (4GB → 32GB)
- 1000 canvas noise variations

**File:**
- `fingerprint_data.py` — `build_fingerprint_js(seed)` tao code JS, `get_unique_seed()` tao seed unique
- `fp_inject.js` — File JS duoc tao ra, inject vao moi trang Google Flow

**Spoofing:**
- WebGL: Vendor + Renderer (GPU name)
- Canvas: Noise tren pixel data (unique per seed)
- Hardware: `navigator.hardwareConcurrency`, `navigator.deviceMemory`
- Screen: `screen.width/height/availWidth/availHeight`
- Audio: `AnalyserNode.getFloatFrequencyData` offset

**Khi nao tao fingerprint:**
- Moi lan Chrome khoi dong (`launcher.py > start_chrome()`)
- Moi lan Chrome restart do recovery (`launcher.py > restart_chrome()`)
- Fingerprint duoc ghi vao `extension_dir/fp_inject.js`

### 7. Launcher (`server/flowkit/launcher.py`)

Khoi dong va quan ly Chrome + Agent + Gateway.

**Chuc nang:**
- `generate_fingerprint(ext_dir, name)` — Tao fingerprint JS va ghi vao extension
- `start_chrome(instance, new_fingerprint)` — Khoi dong Chrome Portable voi extension
- `kill_chrome(instance_name)` — Kill Chrome (3 phuong phap: tracked PID, wmic by extension dir, wmic by profile dir)
- `restart_chrome(instance, new_ipv6)` — Kill + fingerprint moi + start lai
- `start_agent(instance)` — Khoi dong Agent (uvicorn)
- `start_gateway()` — Khoi dong Gateway (uvicorn)

**Kill Chrome:** GoogleChromePortable.exe la wrapper, exit ngay sau khi launch chrome.exe.
Nen phai dung `wmic` de tim chrome.exe thuc theo:
1. Extension dir trong command line
2. Chrome profile dir trong command line

### 8. IPv6 Pool Client (`server/flowkit/ipv6_pool_client.py`)

Ket noi toi IPv6 pool server (MikroTik router) de lay/rotate IPv6.

**API:** `http://192.168.88.146:8765`
- `GET /api/get_ip` — Lay IPv6 moi
- `POST /api/rotate_ip` — Rotate (release cu + lay moi)
- `POST /api/burn_ip` — Danh dau IP bi ban
- `POST /api/release_ip` — Tra IP ve pool

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

## Flow chi tiet: 403 Recovery

```
Request bi 403
      |
      v
mark_403() → consecutive_403++
      |
      v  (>= 3 lan)
cooldown 300s + trigger_recovery()
      |
      v
RecoveryManager._run_recovery()
      |
      v
Level 1: POST agent/api/reset-captcha
      |   → Clear cookies/cache
      |   → Reload Flow tabs
      |   → Re-capture token
      |   → Check extension reconnected
      |
      ├─ OK → clear_cooldown() → Instance kha dung lai
      └─ FAIL
            |
            v
      Level 2: IPv6 Pool rotate_ip()
            |   → Lay IPv6 moi tu MikroTik
            |   → launcher.restart_chrome(new_ipv6)
            |   → Kill Chrome (wmic)
            |   → Generate fingerprint moi
            |   → Start Chrome voi proxy IPv6 moi
            |   → Doi extension reconnect
            |
            ├─ OK → clear_cooldown()
            └─ FAIL
                  |
                  v
            Level 3: Full Chrome restart
                  |   → Kill Chrome (wmic)
                  |   → Generate fingerprint moi
                  |   → Start Chrome (khong IPv6 neu Level 2 that bai)
                  |   → Doi extension reconnect
                  |
                  ├─ OK → clear_cooldown()
                  └─ FAIL → Log "Can xu ly thu cong"
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

recovery:                   # MOI — 403 recovery config
  level1_max_attempts: 2    # So lan thu reset captcha
  level2_max_attempts: 2    # So lan thu rotate IPv6
  level3_max_attempts: 3    # So lan thu full restart
  min_recovery_interval: 30 # Khoang cach toi thieu giua 2 recovery (giay)
  extension_reconnect_timeout: 30  # Timeout doi extension ket noi lai
  chrome_restart_delay: 5   # Doi sau khi restart Chrome

rate_limit:
  cooldown_per_instance: 5  # Khoang cach giua cac request (giay)
  max_concurrent_per_instance: 1  # Toi da request dong thoi

ipv6:                       # IPv6 pool config
  enabled: true
  pool_url: http://192.168.88.146:8765
  prefix_length: 56
  socks_port: 1080

timeouts:
  image_generation: 120     # Timeout tao anh (giay)
  video_submit: 60          # Timeout submit video
  video_poll: 420           # Timeout poll video (7 phut)
  video_poll_interval: 10   # Khoang cach poll (giay)
```

## Setup may moi

Chay `SETUP_NEW_MACHINE.bat` (Run as Admin) de:
1. Check Python 3.10+
2. Install dependencies (requirements.txt)
3. Check config.yaml
4. Check Chrome Portable instances
5. Setup extensions
6. Mo firewall ports: 5100 (gateway), 8100-8106 (agents), ICMP ping

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
- **RecoveryManager tu dong xu ly** (Level 1 → 2 → 3)
- Check trang thai: GET /api/recovery-status
- Trigger thu cong: POST /api/trigger-recovery/{name}
- Reset: POST /api/reset-instance/{name}
- VE3 goi: POST /api/fix/reset-captcha (trigger recovery cho tat ca instance cooling)

### 404 khi tao video (I2V)
- Google eventual consistency: upload anh reference xong nhung chua propagate
- Gateway tu dong retry 2 lan, moi lan doi 5 giay
- Thuong tu khoi (khong can lam gi)

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
  recovery_manager.py     # 403 Recovery (3 cap escalation) — MOI
  fingerprint_data.py     # Browser fingerprint generator — MOI
  launcher.py             # Chrome + Agent + Gateway launcher
  ipv6_pool_client.py     # IPv6 pool client (MikroTik)
  config.yaml             # Cau hinh
  flowkit_gui.py          # GUI quan ly FlowKit
  setup_extensions.py     # Dang ky extension vao Chrome Preferences
  SETUP_NEW_MACHINE.bat   # Setup may moi (firewall, deps) — MOI
  START_FLOWKIT.bat        # Khoi dong FlowKit (CLI)
  START_FLOWKIT_GUI.bat    # Khoi dong FlowKit (GUI)
  agent/
    main.py               # Agent API (port 8100+)
    services/
      flow_client.py      # WebSocket client → extension
  flowkit_extensions/
    ext_8100/             # Extension cho agent 1
      background.js       # Service worker chinh
      content.js          # Inject vao Flow tab
      injected.js         # Inject vao page context
      fp_inject.js        # Fingerprint spoof (auto-generated) — MOI
      manifest.json       # Chrome extension manifest
      popup.html/js       # Popup UI
      side_panel.html/js  # Side panel dashboard
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

Neu 403 recovery lam hong gi, quay lai phien ban stable:
```bash
git checkout 640e838
```

Hoac chi revert 1 file:
```bash
git checkout 640e838 -- server/flowkit/gateway.py
git checkout 640e838 -- server/flowkit/launcher.py
```

## Changelog

### 0c37a8d — 403 Recovery System + Fingerprint + Retry-on-404
- Them `recovery_manager.py`: 3 cap recovery tu dong (reset captcha → rotate IPv6 → restart Chrome)
- Them `fingerprint_data.py`: 53.6M fingerprint unique (WebGL, Canvas, Hardware, Screen, Audio)
- Sua `launcher.py`: fingerprint moi khi Chrome khoi dong, kill/restart Chrome bang wmic
- Sua `gateway.py`: tich hop RecoveryManager, endpoints moi (/health, /api/recovery-status, /api/fix/reset-captcha)
- Sua `gateway.py`: retry-on-404 cho video task (Google eventual consistency)
- Sua `config.yaml`: them section recovery
- Sua all `manifest.json`: them fp_inject.js content_script (MV3 MAIN world, document_start)

### 640e838 — Bearer Token Fix + 401 Detection
- Extension uu tien bearerToken tu VE3 thay vi flowKey rieng
- VE3 tu dong refresh token khi gap 401
- Gateway prefix [401]/[429] cho VE3 detect

### 08059e7 — Video Download Fix
- VE3 download video tu remote FlowKit servers
- _fix_localhost_url() thay 127.0.0.1 → IP server thuc
