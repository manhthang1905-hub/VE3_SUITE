# BÁO CÁO: Chrome tích tụ / Zombie — Pool Ảnh (image_pool_browser.py)
Ngày: 2026-07-06

## 1. TRẠNG THÁI TỐT (đang chạy đúng)
- **android_bypass**: ảnh + video tạo được (curl thuần, không reCAPTCHA). Ảnh ~403 tấm/run, throughput đo được **2575 ảnh/giờ** (50 slot) → 96 slot sẽ hơn.
- **96 slot ĐÃ LÊN** sau khi sửa clamp `_cfg_accounts` (trước bị `min(50)` chặn cứng → nay `min(100)`).
- **Login bounded**: fix `with login_sem` bọc cả `_open_cdp`+warm (trước _open_cdp ngoài sem) → login chỉ mở ≤5 chrome/lúc. Đã verify chỉ 4 login chrome mở.
- Các nhánh reuse (370-407) + login (417-472) ĐỀU đóng chrome trên MỌI path (đã rà). Token factory recycle = đóng cũ trước mở mới. → **KHÔNG có bug "mở-không-đóng" trong vận hành bình thường.**

## 2. GỐC RỄ ZOMBIE (đã xác nhận: 33 chrome mồ côi, parent chết)
### Lỗ hổng A — /F kill bỏ qua handler dọn (CHÍNH)
- Pool đăng ký dọn chrome qua `atexit` + `signal(SIGTERM/SIGINT/SIGBREAK)` → `_kill_pool_chromes()`.
- Khi kill bằng **`taskkill /F`** (force) → handler KHÔNG chạy → chrome con mồ côi (re-parent về system) = zombie.
- Hôm nay restart pool nhiều lần bằng /F → tích 33 zombie.

### Lỗ hổng B — Sweeper không phân biệt orphan cùng dải port (chi tiết trong `_chrome_sweeper` line 806)
- Sweeper mỗi 90s kill: (1) chrome không ExecutablePath, HOẶC (2) chrome `pool_img_profiles` mà port debug KHÔNG thuộc slot đang chạy (9500..9500+n).
- **VẤN ĐỀ**: orphan từ pool CŨ vẫn dùng port 9500..9500+n (trùng dải slot pool MỚI) → sweeper tưởng "slot đang chạy" → **GIỮ LẠI** (không kill). Orphan còn ExecutablePath (chrome.exe vẫn sống) nên điều kiện (1) cũng trượt.
- → orphan cùng dải port KHÔNG bao giờ bị sweeper dọn.

### Lỗ hổng C — Sweeper 90s + burst onboarding
- 96 slot → 89 account onboard; ~40 account cookie chết phải reuse/login chrome (bounded 5) → burst ~15-25 chrome trong 2-3 phút đầu (transient, sẽ settle). Sweeper 90s dọn chậm.
- Token factory (bypass fallback) 1 lần fail → khởi động 4-5 chrome mint và GIỮ chạy (persistent), không tự tắt khi hết cần.

## 3. FIX ĐÃ LÀM (2026-07-06) — phương án dài hạn
1. ✅ **[A+B] Sweeper dọn orphan theo PARENT CHẾT (bất kể port)** — `_chrome_sweeper` giờ dùng psutil: kill main chrome `pool_img_profiles`/`veo3tok_974` có `ppid` KHÔNG còn sống (parent pool đã chết = orphan chắc chắn). Vá lỗ hổng port cũ. Interval **90→30s**. Giữ backstop PowerShell (zombie no-ExecutablePath + port ngoài slot). An toàn tuyệt đối Chrome cá nhân.
2. ✅ **[C] Token factory tự tắt khi IDLE** — `IMG_TOKFAC_IDLE_STOP=300s`: sweeper check, nếu >5' không bypass-fail nào cần WEB token → `_IMG_TOKFAC.stop()` (đóng 4-5 chrome mint). Bypass-fail sau tự bật lại.
3. ✅ **[C] Burst onboarding — ĐÃ CÓ SẴN cơ chế**: account login-fail → `DEAD_COOLDOWN=3h` + `BAD_COOLDOWN=1.5h`, **state persist** (.veo3top_imgpool_state.json) → restart trong 3h KHÔNG thử login lại account chết → không burst lặp. Login bounded 5 (đã fix semaphore). Burst chỉ 1 lần/3h, transient.
4. ✅ **[A] Start clean-slate** — `_kill_pool_chromes()` (line 768) kill MỌI `pool_img_profiles` lúc start (không điều kiện port) → dọn hết orphan khi khởi động. Singleton-guard chỉ exit khi có instance khác đang sống (sở hữu chrome) nên không skip nhầm.
5. ⚠️ **[OPERATIONAL] Đừng restart pool bằng `taskkill /F`** — /F bỏ qua handler dọn → orphan. Dùng `/PID` thường (cho SIGTERM handler chạy `_kill_pool_chromes`). Sweeper 30s giờ tự dọn nếu lỡ /F.

## 4. TRẠNG THÁI: các fix dài hạn ĐÃ IMPLEMENT. Cần RESTART pool để nạp code mới. Sau đó zombie tự dọn 30s/lần, token factory tự tắt khi idle, không còn tích chrome dần.

## 5. MONITOR 04:30-04:53 (leakmon.py) — GỐC RỄ "càng chạy càng nặng"
Theo dõi 60s/lần, kết luận:
- **KHÔNG có leak tích lũy**: chrome / thread (thr_img) / RAM đều tăng rồi QUAY XUỐNG (chrome DELTA về ÂM, thr_img 202→125, RAM 17.6→15.0G). Sweeper + login-bound + idle-stop hoạt động.
- **Thủ phạm THẬT = SPIKE token factory**: bypass ~3% trả `"other"` (transient mạng) → code CŨ fallback mở token factory (4 chrome mint + recycle churn) → **spike chrome 68, RAM chrome 5GB, CPU 100%**, rồi lắng, lặp lại → cảm giác "càng chạy càng nặng".
- **FIX (đã làm)**: trong `generate_external`, `"other"` → `return "retry"` (thử lại bypass, rẻ) — TUYỆT ĐỐI KHÔNG mở token factory. CHỈ `"unusual"` (reCAPTCHA reject thật, cực hiếm với bypass) mới fallback WEB token.
- Sau fix: token factory gần như không bao giờ chạy → hết spike chrome/RAM/CPU.

## 4. GHI CHÚ VẬN HÀNH
- Trong VẬN HÀNH BÌNH THƯỜNG (không restart /F): chrome bounded (login/reuse ≤5, token 4-5), tự đóng. Zombie chủ yếu do RESTART /F (thao tác của mình hôm nay).
- Đã dọn thủ công 33 zombie: 247 → 27 chrome, CPU 27%→21%.
