# veo3top_engine — Veo 3.1 video factory (Python replica của veo3top)

Replica cơ chế tool **Auto Veo3Top** đạt sản lượng cao, build từ reverse-engineer + quan sát tool chạy thật.
Đã test ra **video MP4 thật** (xem `out/`). Chi tiết cơ chế: `../VEO3TOP_MECHANISM_NOTES.md`.

## Cơ chế (đã kiểm chứng)
1. **Token**: Chrome (account ultra) chạy `--disable-blink-features=AutomationControlled` (giấu bot → reCAPTCHA score cao), mint `grecaptcha.enterprise.execute('6LdsFiUsAAAAAIjVDZcuLhaHiDn5nnHVXVRQGeMV',{action:'VIDEO_GENERATION'})` qua CDP. Chrome chạy **DIRECT** (token không ràng IP).
2. **Generate**: POST `aisandbox-pa.googleapis.com/v1/video:batchAsyncGenerateVideoText` bằng **`curl_cffi` impersonate="chrome"** (fingerprint TLS giống Chrome — bắt buộc, `requests` trần bị Google chặn "Sorry") qua **WARP socks5 :40000**.
3. **403 UNUSUAL = bình thường** → retry token mới. **Per-account throttle** (~5-15 req) → **rotate account**.
4. **Download**: `labs.google/fx/api/trpc/media.getMediaUrlRedirect?name=<mediaId>` (cookie account, DIRECT) → mp4. Chỉ có URL khi render xong ⇒ kiêm luôn poll.

## Yêu cầu
- Cloudflare WARP cài sẵn, chế độ proxy port 40000 (engine tự `warp-cli mode proxy/connect`).
- Python: `pip install curl_cffi websocket-client` (đã có).
- Account ultra: các `D:\VE3_SUITE\GoogleChromePortable - Copy (N)\GoogleChromePortable.exe` (đã login labs.google).

## Chạy
```bash
# Đa-account (khuyến nghị) — tự rotate qua 21 account khi throttle:
python pool_engine.py <số_video> <số_chrome_song_song>
python pool_engine.py 20 3

# Đơn account (debug):
python engine.py "<...\GoogleChromePortable.exe>" <debug_port> <số_video>
```
Video lưu ở `out/`.

## File
- `warp.py` — WARP proxy mode + (rotate IP, không dùng).
- `chrome_factory.py` — ChromeAccount: launch (anti-automation), mint token, bearer/cookie/project (CDP).
- `flow_client.py` — generate/poll/download bằng curl_cffi(chrome).
- `engine.py` — orchestrator 1 account.
- `pool_engine.py` — orchestrator đa-account rotation + poll/download song song.

## Kết quả test (2026-06-30, IP WARP 104.28.205.239)
- Engine 1-account: 2/2, 3/3 (account fresh) — real mp4 5-10MB.
- Pool đa-account: **6/6, 0 fail**, ~135 video/h với concurrency=2. Rotation throttle OK.
- Tổng 11 video MP4 hợp lệ trong `out/`.

## TÍCH HỢP VÀO FLOWKIT (option "Veo3top") — ĐÃ CHẠY
Option mới ở GUI, KHÔNG ảnh hưởng tool cũ (chỉ chạy khi chọn). Thay bước **tạo video** (đang qua server)
bằng phương pháp veo3top — mỗi project/1 account, ảnh đã upload sẵn (media_id trong Excel) → generate I2V local.
- `provider.py` → `Veo3topProvider`: tái dùng chrome account (bearer+cookie+mint token), `submit_video(prompt, media_id, out)`.
- `tools/ve3/ve3_worker.py`: whitelist backend `"veo3top"` + branch trong `_submit_video` → `_submit_video_veo3top` → `_get_veo3top_provider` (lấy `account.chrome_path`/`profile_dir` như lúc upload ảnh).
- `tools/ve3/ve3_gui.py`: thêm `"Veo3top": "veo3top"` vào `generation_backend_options`.
**Bật:** GUI → Generation backend → chọn **Veo3top** → Save settings. (Yêu cầu: WARP proxy 40000 + `curl_cffi`.)
**Đã test:** provider.start→upload→generate I2V→poll batchCheck→download = video thật 1.37MB/63s (scene20, TL1-0550).
Lỗi `exhausted (UNUSUAL)` = account throttle → bình thường (scene fail, chạy lại sau / account hồi). Có reconnect ws.

## 📌 TODO / LÀM SAU (tạm dừng theo yêu cầu 2026-06-30)

**Trạng thái hiện tại = "Cách A" (per-project = 1 chrome account) — ĐÃ CHẠY RA VIDEO:**
- Mỗi mã mở chrome account của nó (Copy N) → lấy bearer + cookie + mint token → generate I2V → download.
- Đã wire: `provider.py` + `_submit_video` branch + GUI option "Veo3top". Port CDP riêng `9850+CopyN` (chạy nhiều mã đồng thời không đụng). Retry bền bỉ 80 lần + cooldown 25s.
- Nhược: nặng RAM khi nhiều mã (N chrome account login cùng lúc).

**"Cách B" = LÀM GIỐNG VEO3TOP (tối ưu, làm sau):**
- 1 **chrome trắng CHUNG** (login 1 account bất kỳ) chỉ để đẻ token recaptcha → 1 pool token dùng cho mọi mã.
- Mỗi account chỉ cần **bearer + cookie lưu sẵn** (auto lấy 1 lần + tự refresh khi hết hạn / 401 / download fail).
- Nhẹ hơn nhiều khi chạy nhiều mã.

**⚠️ CÂU HỎI MỞ chặn cách B (PHẢI test đối chứng trước khi build):**
- Token recaptcha có **dùng chung cross-account** được không? (mint ở chrome account A → generate account B).
- Test nhanh 2026-06-30: tokenA(lucaslira30y)→generateB(latol0039) = **4/4 UNUSUAL** → CHƯA kết luận (có thể do account B throttle, không hẳn do cross-account).
- **Cần test đối chứng:** trên 2 account FRESH, so sánh A→A (same-account) vs A→B (cross). Nếu A→A pass mà A→B fail ⇒ token gắn session/account ⇒ **cách B KHÔNG share được 1 chrome** (phải mint per-account) ⇒ cách B chỉ tiết kiệm được phần cookie, không tiết kiệm chrome.
- Nếu A→B cũng pass ⇒ cách B khả thi đầy đủ (1 chrome trắng chung).
- Cũng cần kiểm: chrome trắng KHÔNG login có mint được token không (trang Flow có redirect login không) — veo3top captcha profile có vẻ cần login 1 account nào đó.

**Việc cần làm cho cách B (khi quyết làm):**
1. `token_factory.py`: 1 blank chrome (anti-automation) + thread mint token → pool (TTL ~90s) + recycle. Singleton dùng chung.
2. Auth cache per-account: lưu `{bearer,cookie,ts}` ra file; auto lấy bằng cách mở chrome account 1 lần (lúc tool đã mở để refresh token/upload thì chen vào lấy luôn cookie) + tự refresh khi 401/download fail. **Tự fix.**
3. `provider.submit_video` đổi sang: token = factory.get(), bearer/cookie = auth_cache.get(account). Bỏ chrome account thường trú.

## Scale lên ~1000 video/h
- Tăng `concurrency` (nhiều chrome account song song) — mỗi account ~25000 credit.
- 21 account rotate; account throttle sẽ hồi sau cooldown.
- Render async server-side (~60-90s) chạy song song; bottleneck là tốc độ submit + token mint + số account.
- Poll hiện dùng getMediaUrlRedirect-retry (đơn giản); scale lớn nên gom batch `batchCheckAsyncVideoGenerationStatus`.
