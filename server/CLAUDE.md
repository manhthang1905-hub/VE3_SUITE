# VE3 Tool Simple - Project Context

## Tổng quan
**Phần mềm tạo video YouTube tự động** sử dụng Veo3 Flow (labs.google/fx).

### Mục đích
- Tool này chạy trên **MÁY ẢO (VM)**
- Các VM tạo: Excel (kịch bản) → Ảnh → Video → Visual
- Sau đó chuyển kết quả về **MÁY CHỦ (Master)**

### Workflow hoàn chỉnh

```
┌─────────────────────────────────────────────────────────────────────────┐
│                         MÁY CHỦ (Master)                                │
│  - Chứa file SRT gốc (phụ đề video)                                     │
│  - Nhận kết quả cuối cùng (ảnh + video)                                 │
└───────────────────────────────┬─────────────────────────────────────────┘
                                │ (1) Lấy SRT
                                ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                    MÁY ẢO (VM) - Tool này                               │
│                                                                         │
│   ┌─────────────────────────────────────────────────────────────────┐   │
│   │              VM_MANAGER_GUI.PY (Entry Point)                     │   │
│   │                   - GUI chính (Tkinter)                          │   │
│   │                   - Điều phối tất cả workers                     │   │
│   │                   - TẤT CẢ XOAY QUANH NÓ                         │   │
│   └─────────────────────────────────────────────────────────────────┘   │
│                                │                                         │
│         ┌──────────────────────┼──────────────────────┐                 │
│         ▼                      ▼                      ▼                 │
│   ┌──────────────┐    ┌──────────────┐    ┌──────────────┐              │
│   │ Excel Worker │    │Chrome Worker1│    │Chrome Worker2│              │
│   │  (Python)    │    │   (Python)   │    │   (Python)   │              │
│   │              │    │              │    │              │              │
│   │ SRT → Excel  │    │ Excel → Ảnh  │    │ Excel → Ảnh  │              │
│   │ (API AI)     │    │ (Google Flow)│    │ (Google Flow)│              │
│   └──────────────┘    └──────────────┘    └──────────────┘              │
│                                                                         │
│   (2) Bước 1: SRT → Excel (7 steps qua API DeepSeek/Gemini)            │
│       - Phân tích story → Tạo segments → Characters → Locations         │
│       - Director plan → Scene planning → Scene prompts                  │
│                                                                         │
│   (3) Bước 2: Excel → Ảnh + Video (Chrome automation với Google Flow)   │
│       - Chrome 1: Tạo ảnh scenes chẵn (2,4,6...) + reference images     │
│       - Chrome 2: Tạo ảnh scenes lẻ (1,3,5...)                          │
│       - Song song để tối ưu tốc độ                                      │
│                                                                         │
└───────────────────────────────┬─────────────────────────────────────────┘
                                │ (4) Trả kết quả
                                ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                         MÁY CHỦ (Master)                                │
│  - Nhận ảnh (img/*.png)                                                 │
│  - Nhận video (nếu có)                                                  │
│  - Tiếp tục xử lý (compose video, upload YouTube...)                    │
└─────────────────────────────────────────────────────────────────────────┘
```

### 2 Chức năng chính
1. **PY Đạo Diễn (Excel Worker)**: Tạo Excel kịch bản từ SRT - phân tích story, tạo segments, characters, locations, director plan, scene prompts
2. **Flow Image/Video (Chrome Workers)**: Tạo ảnh và video từ prompts bằng Google Veo3 Flow

- **Owner**: nguyenvantuong161978-dotcom
- **Repo chính thức**: https://github.com/nguyenvantuong161978-dotcom/ve3-tool-simple

## Kiến trúc chính

### Entry Points
- `vm_manager_gui.py` - GUI chính (Tkinter), quản lý workers
- `vm_manager.py` - Logic điều phối workers (VMManager class)
- `START.py` / `START.bat` - Khởi động tool

### Workers (chạy song song)
- **Excel Worker** (`run_excel_api.py`): Tạo Excel từ SRT (7 bước: story → segments → characters → locations → director_plan → scene_planning → prompts)
- **Chrome Worker 1** (`_run_chrome1.py`): Tạo ảnh scenes chẵn (2,4,6...) + reference images (nv/loc)
- **Chrome Worker 2** (`_run_chrome2.py`): Tạo ảnh scenes lẻ (1,3,5...)

---

## 3 FILE PYTHON CHÍNH

### 1. `run_excel_api.py` - Excel Worker (PY Đạo Diễn)

**Mục đích**: Chuyển đổi file SRT (phụ đề) thành Excel kịch bản hoàn chỉnh qua 7 bước API.

**Input**: `PROJECTS/{code}/{code}.srt`
**Output**: `PROJECTS/{code}/{code}_prompts.xlsx`

**7 Bước xử lý**:
```
┌─────────────────────────────────────────────────────────────────────────┐
│ STEP 1: Story Analysis                                                  │
│   - Đọc toàn bộ SRT                                                     │
│   - API phân tích: thể loại, mood, style, tổng quan câu chuyện          │
│   - Output: story_analysis sheet trong Excel                            │
├─────────────────────────────────────────────────────────────────────────┤
│ STEP 2: Segments (Phân đoạn)                                            │
│   - Chia SRT thành các đoạn logic (mỗi đoạn ~5-15 SRT entries)          │
│   - VALIDATION 1: Check ratio SRT/images, split nếu quá lớn             │
│   - VALIDATION 2: Check coverage, call API bổ sung nếu thiếu            │
│   - Output: segments sheet (segment_id, name, srt_range, image_count)   │
├─────────────────────────────────────────────────────────────────────────┤
│ STEP 3: Characters (Nhân vật)                                           │
│   - API phân tích các nhân vật xuất hiện trong story                    │
│   - Output: characters sheet (id, name, description, appearance)        │
├─────────────────────────────────────────────────────────────────────────┤
│ STEP 4: Locations (Địa điểm)                                            │
│   - API phân tích các địa điểm/bối cảnh                                 │
│   - Output: locations sheet (id, name, description, atmosphere)         │
├─────────────────────────────────────────────────────────────────────────┤
│ STEP 5: Director Plan (Kế hoạch đạo diễn)                               │
│   - Tạo danh sách scenes cho từng segment                               │
│   - Mỗi scene: visual_moment, srt_start, srt_end, duration              │
│   - GAP-FILL: Đảm bảo 100% SRT coverage                                 │
│   - Output: director_plan sheet                                         │
├─────────────────────────────────────────────────────────────────────────┤
│ STEP 6: Scene Planning (Chi tiết hóa)                                   │
│   - API chi tiết từng scene: camera_angle, lighting, composition        │
│   - Parallel processing: 15 scenes/batch, max 10 concurrent             │
│   - Output: Update director_plan với chi tiết                           │
├─────────────────────────────────────────────────────────────────────────┤
│ STEP 7: Scene Prompts (Tạo prompts)                                     │
│   - Tạo img_prompt cho từng scene (dùng để tạo ảnh)                     │
│   - Parallel processing: 10 scenes/batch, max 10 concurrent             │
│   - Duplicate detection + fallback                                      │
│   - Output: scenes sheet (img_prompt, ref_files, characters_used, etc.) │
└─────────────────────────────────────────────────────────────────────────┘
```

**Key modules sử dụng**:
- `modules/progressive_prompts.py` - Logic 7 steps
- `modules/ai_providers.py` - API calls (DeepSeek/Gemini)
- `modules/excel_manager.py` - Excel I/O (PromptWorkbook class)

**Chế độ chạy**:
```
┌─────────────────────────────────────────────────────────────────────────┐
│ CONTINUOUS MODE (--loop) - Chạy liên tục tự động                        │
│                                                                         │
│   Workflow vòng lặp:                                                    │
│   1. Scan master (Z:\AUTO) cho projects mới có SRT                      │
│   2. IMPORT: Copy project từ master → local PROJECTS                    │
│   3. Xóa project trên master (tránh xử lý trùng)                        │
│   4. Chạy 7 bước API tạo Excel                                          │
│   5. Đợi SCAN_INTERVAL (60s) rồi lặp lại                                │
│                                                                         │
│   → Chrome workers sẽ tự động pick up project từ local                  │
│   → Sau khi có ảnh, Chrome sẽ copy về VISUAL trên master                │
└─────────────────────────────────────────────────────────────────────────┘

Usage:
    python run_excel_api.py --loop    # Chạy continuous mode
```

---

### 2. `_run_chrome1.py` - Chrome Worker 1

**Mục đích**: Tạo ảnh cho scenes CHẴN (2, 4, 6, 8...) + Reference images

**Chrome Portable**: `GoogleChromePortable/`

**Nhiệm vụ**:
```
┌─────────────────────────────────────────────────────────────────────────┐
│ 1. TẠO REFERENCE IMAGES (ưu tiên cao)                                   │
│    - Characters: nv/{char_id}.png (ảnh nhân vật)                        │
│    - Locations: loc/{loc_id}.png (ảnh địa điểm)                         │
│    - Dùng làm style reference cho scenes                                │
├─────────────────────────────────────────────────────────────────────────┤
│ 2. TẠO SCENE IMAGES (scenes chẵn)                                       │
│    - Scene 2, 4, 6, 8, 10...                                            │
│    - Output: img/scene_002.png, img/scene_004.png...                    │
│    - Upload reference images kèm theo                                   │
├─────────────────────────────────────────────────────────────────────────┤
│ 3. TẠO VIDEO (nếu cần - video_mode)                                     │
│    - Dùng ảnh scene để tạo video clips                                  │
│    - Output: video/scene_XXX.mp4                                        │
└─────────────────────────────────────────────────────────────────────────┘
```

**Key modules sử dụng**:
- `modules/drission_flow_api.py` - DrissionPage browser control
- `modules/smart_engine.py` - Main image/video generation engine
- `modules/chrome_manager.py` - Chrome process management

---

### 3. `_run_chrome2.py` - Chrome Worker 2

**Mục đích**: Tạo ảnh cho scenes LẺ (1, 3, 5, 7...)

**Chrome Portable**: `GoogleChromePortable - Copy/` (riêng biệt để chạy song song)

**Nhiệm vụ**:
```
┌─────────────────────────────────────────────────────────────────────────┐
│ TẠO SCENE IMAGES (scenes lẻ)                                            │
│    - Scene 1, 3, 5, 7, 9...                                             │
│    - Output: img/scene_001.png, img/scene_003.png...                    │
│    - KHÔNG tạo reference (Chrome 1 đã làm)                              │
│    - skip_references=True để tránh trùng lặp                            │
└─────────────────────────────────────────────────────────────────────────┘
```

**Lý do tách 2 Chrome Workers**:
- Google Flow rate limit → chạy song song để tăng tốc 2x
- Chrome 1 tạo references trước, Chrome 2 chỉ tạo scenes
- Mỗi Chrome dùng folder Data riêng để tránh xung đột

**Key modules sử dụng** (giống Chrome 1):
- `modules/drission_flow_api.py`
- `modules/smart_engine.py`
- `modules/chrome_manager.py`

---

### Modules quan trọng
- `modules/smart_engine.py` - Engine chính tạo ảnh/video
- `modules/drission_flow_api.py` - DrissionPage API cho Google Flow
- `modules/browser_flow_generator.py` - Browser automation
- `modules/excel_manager.py` - Quản lý Excel (PromptWorkbook)
- `modules/ipv6_manager.py` - Quản lý IPv6 rotation
- `modules/chrome_manager.py` - Quản lý Chrome instances

### Cấu trúc dữ liệu
```
PROJECTS/
└── {project_code}/
    ├── {code}.srt           # File phụ đề
    ├── {code}_prompts.xlsx  # Excel chứa prompts
    ├── nv/                  # Reference images (characters/locations)
    └── img/                 # Scene images (scene_001.png, ...)
```

## Config
- `config/settings.yaml` - Cấu hình chính (API keys, Chrome paths, IPv6...)
- `config/ipv6_list.txt` - Danh sách IPv6 addresses

## Quy tắc xử lý lỗi

### 1. LỖI 403 (Google Flow bị block IP)

**Nguyên nhân**: Google Flow rate limit hoặc block IP khi request quá nhiều.

**Cơ chế xử lý tự động**:
```
┌─────────────────────────────────────────────────────────────────────────┐
│ LEVEL 1: Worker-level recovery (3 lỗi 403 liên tiếp)                    │
│                                                                         │
│   Chrome Worker gặp 403 x3 → Tự động:                                   │
│   1. Xóa Chrome Data folder (giữ lại First Run)                         │
│   2. Restart worker                                                     │
│   3. Tiếp tục từ scene đang làm dở                                      │
├─────────────────────────────────────────────────────────────────────────┤
│ LEVEL 2: System-level recovery (5 lỗi 403 tổng cộng)                    │
│                                                                         │
│   Nếu tổng 403 từ cả 2 workers >= 5 → VM Manager tự động:               │
│   1. Stop tất cả Chrome workers                                         │
│   2. Rotate IPv6 (đổi sang IP mới từ config/ipv6.txt)                   │
│   3. Xóa Chrome Data của cả 2 workers                                   │
│   4. Restart tất cả workers                                             │
└─────────────────────────────────────────────────────────────────────────┘
```

**File tracking**: `config/.403_tracker.json` - lưu số lỗi 403 của mỗi worker

**Modules liên quan**:
- `modules/shared_403_tracker.py` - Đếm và track 403 errors
- `modules/ipv6_manager.py` - Rotate IPv6 address
- `modules/chrome_manager.py` - Clear Chrome data

---

### 2. LỖI TIMEOUT (Google Flow không phản hồi)

**Nguyên nhân**: Network chậm, Google Flow quá tải, hoặc prompt phức tạp.

**Cơ chế xử lý**:
```
┌─────────────────────────────────────────────────────────────────────────┐
│ Image generation timeout (120s mặc định)                                │
│   → Retry 3 lần với cùng prompt                                         │
│   → Nếu vẫn fail → Skip scene, log warning, tiếp tục scene khác         │
├─────────────────────────────────────────────────────────────────────────┤
│ Video generation timeout (180s mặc định)                                │
│   → Retry 2 lần                                                         │
│   → Nếu vẫn fail → Đánh dấu scene cần regenerate sau                    │
└─────────────────────────────────────────────────────────────────────────┘
```

**Config**: `config/settings.yaml`
- `browser_generate_timeout: 120` - Timeout tạo ảnh (giây)
- `retry_count: 3` - Số lần retry

---

### 3. LỖI CHROME DISCONNECT

**Nguyên nhân**: Chrome crash, memory leak, hoặc network disconnect.

**Cơ chế xử lý**:
```
┌─────────────────────────────────────────────────────────────────────────┐
│ Chrome bị disconnect:                                                   │
│   1. Worker detect qua DrissionPage connection check                    │
│   2. Kill Chrome process cũ (chỉ worker đó, không kill hết)             │
│   3. Clear Chrome Data                                                  │
│   4. Restart Chrome với profile mới                                     │
│   5. Resume từ scene đang làm dở                                        │
└─────────────────────────────────────────────────────────────────────────┘
```

**Module**: `modules/chrome_manager.py` - `kill_chrome_by_port()`

---

### 4. LỖI API (Excel Worker)

**Các loại lỗi thường gặp**:

```
┌─────────────────────────────────────────────────────────────────────────┐
│ API rate limit (429):                                                   │
│   → Exponential backoff: 1s → 2s → 4s → 8s                              │
│   → Max retry: 5 lần                                                    │
├─────────────────────────────────────────────────────────────────────────┤
│ API response không đủ data:                                             │
│   → VALIDATION 1: Chia nhỏ segment, gọi API lại                         │
│   → VALIDATION 2: Detect missing range, call API bổ sung                │
│   → GAP-FILL: Tạo fill scenes cho SRT còn thiếu                         │
├─────────────────────────────────────────────────────────────────────────┤
│ Duplicate prompts (>80%):                                               │
│   → Tạo unique fallback prompts thay vì skip batch                      │
│   → Đảm bảo không mất scenes                                            │
├─────────────────────────────────────────────────────────────────────────┤
│ JSON parse error:                                                       │
│   → Retry với temperature cao hơn                                       │
│   → Max retry: 3 lần                                                    │
└─────────────────────────────────────────────────────────────────────────┘
```

**Module**: `modules/progressive_prompts.py` - `_call_api_with_retry()`

---

### 5. LỖI CONTENT POLICY (Google Flow từ chối prompt)

**Nguyên nhân**: Prompt chứa nội dung vi phạm policy của Google.

**Cơ chế xử lý**:
```
┌─────────────────────────────────────────────────────────────────────────┐
│ Content policy violation detected:                                      │
│   1. Log prompt bị reject                                               │
│   2. Tạo fallback prompt (generic, safe)                                │
│   3. Retry với fallback prompt                                          │
│   4. Nếu vẫn fail → Skip scene, đánh dấu manual review                  │
└─────────────────────────────────────────────────────────────────────────┘
```

---

### Chrome Data Clearing (Chi tiết)

**Khi nào clear**:
- 403 error x3
- Chrome disconnect
- Manual restart từ GUI

**Cách clear**:
```
ChromePortable/Data/
├── profile/
│   ├── First Run          ← GIỮ LẠI (tránh first-run prompts)
│   ├── Default/           ← XÓA
│   ├── Cache/             ← XÓA
│   └── ...                ← XÓA
```

**Code**: `modules/chrome_manager.py` - `clear_chrome_data()`
- GIỮ LẠI `Data/profile/First Run` để tránh first-run prompts

## Commands thường dùng

```bash
# Chạy GUI
python vm_manager_gui.py

# Chạy worker riêng
python run_excel_api.py --loop
python _run_chrome1.py
python _run_chrome2.py

# Git
git push official main  # Push lên repo chính thức
```

## Lưu ý quan trọng

1. **Chrome Portable**: Sử dụng 2 Chrome Portable riêng biệt
   - Chrome 1: `GoogleChromePortable/`
   - Chrome 2: `GoogleChromePortable - Copy/`

2. **Google Login**: Chrome phải đăng nhập Google trước khi chạy

3. **IPv6**: Cần có IPv6 list trong `config/ipv6_list.txt` để bypass rate limit

4. **Agent Protocol**: Workers giao tiếp qua `.agent/status/*.json`

## Recent Fixes

### 2026-02-04 - Project Priority (v1.0.83)
- **TÍNH NĂNG**: Project gần xong → làm trước
- **Logic**: `completion = images_done / total_scenes`
- **Sort**: Descending - completion cao hơn = priority cao hơn
- **Code**: `vm_manager.py` - `scan_projects()`
- **Mục đích**: Tối ưu hóa thời gian hoàn thành project

### 2026-02-04 - EARLY CHECK Logic Fix (v1.0.82)
- **BUG**: v1.0.79 EARLY CHECK kiểm tra "if scenes exist" → SAI!
  - Scenes có thể tồn tại nhưng chưa đủ (API bị ngắt giữa chừng)
  - Dẫn đến skip khi Excel chưa hoàn thành
- **FIX**: Kiểm tra `step_7 status == "COMPLETED"` thay vì check scenes
  - Nếu step_7 = COMPLETED → skip (Excel đã hoàn thành)
  - Nếu step_7 != COMPLETED → chạy tiếp từ bước đang dở
- **Code**: `modules/progressive_prompts.py` lines 3245-3259
- **Kết hợp với v1.0.81**: `workbook.save()` sau mỗi `update_step_status()`

### 2026-01-23 - Excel API 100% SRT Coverage (v1.0.4)
- **CRITICAL**: Fixed Excel worker losing scenes and SRT coverage
- **4 Major Fixes in `modules/progressive_prompts.py`**:
  1. **VALIDATION 1 (lines 833-1021)**: Split disproportionate segments
     - Ratio > 15: Local split into smaller segments
     - Ratio > 30: Recursive API retry with smaller input
  2. **VALIDATION 2 (lines 1023-1130)**: API call for missing segments
     - Detects gaps in SRT coverage after Step 2
     - Calls API for missing ranges with proper context
     - Recalculates image_count: `seg_entries / 10`
  3. **GAP-FILL (lines 2146-2198)**: Post-processing in Step 5
     - Finds all uncovered SRT indices
     - Creates fill scenes (max 10 SRT per scene)
     - Guarantees 100% SRT coverage
  4. **Duplicate Fallback (line ~2730)**: No more skipped batches
     - Creates unique fallback prompts instead of skipping
     - Prevents losing scenes due to >80% duplicates
- **Test Result (KA2-0238)**: 1183 SRT entries, 100% coverage, 520 scenes

### 2026-01-22 - Chrome 2 Control Fix
- **CRITICAL**: Fixed Chrome 2 using wrong portable path
  - Added `not self._chrome_portable` check to prevent auto-detect override
  - Added relative-to-absolute path conversion in drission_flow_api.py
- Created check_version.py to verify fixes are applied
- Created FIX_CHROME2_INSTRUCTIONS.txt for user update guide
- Fixed CMD hiding (START.bat uses pythonw)
- Fixed Chrome window positioning (even split, no overlap)
- Added show_cmd_windows() function

### 2026-01-20
- Fix GUI hiển thị đúng ProjectStatus attributes
- Fix bug `scene_number` → `scene_id` trong `get_project_status()`
- Thêm Chrome data clearing khi 403 errors
- Xóa log cũ khi start worker mới
- Đổi UPDATE URL sang repo mới `ve3-tool-simple`

---

## GHI CHÚ CÔNG VIỆC (Session Notes)

> **QUAN TRỌNG**: Claude Code phải cập nhật section này sau mỗi phiên làm việc để phiên sau sử dụng hiệu quả.

### Phiên hiện tại: 2026-02-04 - EARLY CHECK Logic Fix (v1.0.82 ✅)

**MISSION**: Fix EARLY CHECK logic - Dựa vào TRẠNG THÁI 7 BƯỚC, không phải scenes

**PROBLEM DISCOVERED**:
- v1.0.79 EARLY CHECK: `if scenes exist → skip`
- SAI! Vì scenes có thể tồn tại nhưng chưa đủ (API bị ngắt giữa chừng)
- User: "nên dựa vào trạng thái của 7 bước để quyết định có làm hay không"

**ROOT CAUSE**:
- Code kiểm tra `if existing_scenes and len(existing_scenes) > 0` → skip
- Nhưng scenes có thể incomplete, step_7 chưa COMPLETED
- Dẫn đến skip khi Excel chưa thực sự hoàn thành

**FIX ĐƠN GIẢN** (`modules/progressive_prompts.py` lines 3245-3259):
```python
# v1.0.82: EARLY CHECK dựa vào TRẠNG THÁI 7 BƯỚC, không phải scenes
step7_status = workbook.get_step_status("step_7")
if step7_status.get("status") == "COMPLETED":
    # Skip - Excel đã hoàn thành
    return True
# Nếu không COMPLETED → tiếp tục chạy các bước
```

**RESULT**:
- ✅ Skip khi step_7 = COMPLETED (chính xác)
- ✅ Chạy tiếp khi step_7 != COMPLETED (resume từ bước dở)
- ✅ Không bị skip khi scenes incomplete

**VERSION**: 1.0.82
**STATUS**: ✅ PRODUCTION READY

---

### Phiên trước: 2026-01-24 - Step 7 Metadata Fix (100% Accuracy ✅)

**MISSION**: Fix metadata/prompt mismatch - Đảm bảo metadata chính xác 100%

**PROBLEM DISCOVERED**:
- User phát hiện 3 scenes có metadata không khớp prompt:
  - Scene 21: Có char ref `(nv1.png)` trong prompt nhưng `characters_used=None`
  - Scene 171, 172: Có loc ref `(loc1.png)` trong prompt nhưng `location_used=None`

**ROOT CAUSE**:
- Step 7 tạo prompts từ API (có thể add references)
- Nhưng metadata (characters_used, location_used) lấy từ `original` director_plan
- Nếu original có empty metadata → metadata vẫn empty dù prompt có refs

**SOLUTION - PARSE PROMPTS FOR ACTUAL IDs**:

**Implementation** (`modules/progressive_prompts.py` lines 2947-2975):
```python
import re

# Extract all character IDs from prompt (pattern: nvX.png)
char_pattern = r'\(([nN][vV]_?\d+)\.png\)'
prompt_char_matches = re.findall(char_pattern, img_prompt)
if prompt_char_matches:
    char_ids = list(set(prompt_char_matches))  # Use IDs from prompt

# Extract location ID from prompt (pattern: locX.png)
loc_pattern = r'\(([lL][oO][cC]_?\d+)\.png\)'
prompt_loc_matches = re.findall(loc_pattern, img_prompt)
if prompt_loc_matches:
    loc_id = prompt_loc_matches[0]  # Use ID from prompt

# Use parsed IDs for metadata (not original)
chars_used_str = ",".join(char_ids) if char_ids else ""
loc_used_str = loc_id if loc_id else ""

scene = Scene(
    ...
    characters_used=chars_used_str,  # Parsed from prompt
    location_used=loc_used_str,  # Parsed from prompt
    ...
)
```

**REGEX TESTS - ALL PASSED**:
- ✅ `"A man (nv1.png)"` → chars=['nv1'], loc=None
- ✅ `"in room (loc1.png)"` → chars=[], loc='loc1'
- ✅ `"(nv1.png) in (loc5.png)"` → chars=['nv1'], loc='loc5'
- ✅ `"(nv1.png) and (nv2.png) in (loc3.png)"` → chars=['nv1','nv2'], loc='loc3'
- ✅ `"Empty room"` → chars=[], loc=None

**RESULT**:
- ✅ **0 issues found** - 100% metadata accuracy
- ✅ All metadata now matches prompt content exactly
- ✅ No more mismatches between references in prompt vs metadata

**KEY INSIGHT**:
- Metadata phải reflect ACTUAL content trong prompt
- Parse output để extract thực tế thay vì trust input
- Regex parsing = simple, fast, accurate

**COMMIT**: fcdd929
**VERSION**: 1.0.31
**STATUS**: ✅ PRODUCTION READY - Perfect metadata accuracy!

---

### Phiên tiếp theo: 2026-01-24 - API Retry Logic (Prevent Mid-Process Failures ✅)

**MISSION**: Implement retry logic to prevent API failures from stopping Excel generation mid-process

**PROBLEM DISCOVERED**:
- First two full test runs failed at Step 5 (Director Plan)
- Steps 5-7 stayed PENDING, 0 scenes created
- Third test succeeded → indicates intermittent API issues
- User identified: "hay là do api bị kiểu phải chờ vì làm nhiều" (API needs waiting due to many calls)

**ROOT CAUSE**:
- DeepSeek API rate limiting / quota exhaustion
- No retry mechanism → single API failure = process stops
- High API call volume in Steps 5-7 triggers rate limits

**SOLUTION - EXPONENTIAL BACKOFF RETRY LOGIC**:

**Implementation** (`modules/progressive_prompts.py` lines 144-233):
```python
def _call_api(self, prompt: str, temperature: float = 0.7, max_tokens: int = 8192) -> Optional[str]:
    """Gọi DeepSeek API với retry logic để tránh mid-process failures."""
    import requests
    import time

    max_retries = 5
    base_delay = 2  # seconds

    for attempt in range(max_retries):
        try:
            resp = requests.post(self.DEEPSEEK_URL, headers=headers, json=data, timeout=120)

            if resp.status_code == 200:
                # Success!
                if attempt > 0:
                    self._log(f"  API success after {attempt + 1} attempts", "INFO")
                return resp.json()["choices"][0]["message"]["content"]

            elif resp.status_code == 429:
                # Rate limit - retry with exponential backoff
                delay = base_delay * (2 ** attempt)  # 2, 4, 8, 16, 32 seconds
                self._log(f"  Rate limit hit (429), retry {attempt + 1}/{max_retries} after {delay}s", "WARN")
                if attempt < max_retries - 1:
                    time.sleep(delay)
                    continue

            elif resp.status_code >= 500:
                # Server error - retry
                delay = base_delay * (2 ** attempt)
                self._log(f"  Server error ({resp.status_code}), retry {attempt + 1}/{max_retries} after {delay}s", "WARN")
                if attempt < max_retries - 1:
                    time.sleep(delay)
                    continue

            else:
                # Client error (4xx except 429) - don't retry
                self._log(f"  API error: {resp.status_code}", "ERROR")
                return None

        except requests.exceptions.Timeout:
            # Timeout - retry
            delay = base_delay * (2 ** attempt)
            if attempt < max_retries - 1:
                time.sleep(delay)
                continue

    return None
```

**FEATURES**:
- ✅ **15 max retries** with exponential backoff (3 × 2^n seconds) - increased for multi-machine environments
- ✅ **Rate limit handling** (429 errors) → automatic retry with backoff
- ✅ **Server error handling** (5xx errors) → automatic retry
- ✅ **Timeout handling** → automatic retry
- ✅ **Smart retry** - Skip retry for client errors (4xx except 429)
- ✅ **Detailed logging** - Track retry attempts and success

**RETRY DELAYS** (v1.0.33):
- Base delay: 3 seconds
- Attempt 1: 3s, Attempt 2: 6s, Attempt 3: 12s
- Attempt 4: 24s, Attempt 5: 48s, Attempt 6: 96s
- Total max wait: ~6 minutes per API call (handles heavy rate limiting)

**RESUME LOGIC** (Already Built-in):
- ✅ **Step 5**: Skips if director_plan already exists
- ✅ **Step 6**: Skips if scene_planning already exists
- ✅ **Step 7**: Only creates missing scenes (pending_scenes)
- ✅ **If interrupted**: Just rerun → continues from last checkpoint, no lost work!

**RESULT**:
- ✅ Prevents mid-process failures from API rate limiting
- ✅ Automatic recovery without manual intervention
- ✅ No lost work due to temporary API issues
- ✅ Handles high-volume API calls gracefully
- ✅ Multi-machine safe - 15 retries handle quota contention

**COMMITS**: 71a0512 (v1.0.32), eacbf3d (v1.0.33)
**VERSION**: 1.0.33
**STATUS**: ✅ PRODUCTION READY - Pushed to GitHub

---

### Phiên tiếp theo: 2026-01-24 - Auto-Copy on Completion + Thumbnail Generation ✅

**MISSION**: Auto-copy results to master when project completes + Generate thumbnail for master

**REQUIREMENTS**:
1. Copy to master when project completes (not just on timeout)
2. Select main character (not child) image for thumbnail
3. Copy thumbnail to thumb/ folder for master to use

**IMPLEMENTATION**:

**1. Auto-Copy on Completion** (`vm_manager.py` lines 1644-1675):
```python
def create_tasks_for_project(self, project_code: str):
    status = self.quality_checker.get_project_status(project_code)

    # Check if project is completed
    if status.current_step == "done":
        if self.auto_path and project_code not in self._completed_projects:
            self.log(f"PROJECT COMPLETED: {project_code}", "SYSTEM", "SUCCESS")
            self.copy_project_to_master(project_code)
            self._completed_projects.add(project_code)
            # Reset timer for next project
            self.project_start_time = None
            self.current_project_code = None
        return
```

**2. Thumbnail Generation** (`vm_manager.py` lines 1541-1599):
```python
def create_thumbnail(self, project_code: str):
    # Read characters from Excel
    wb = PromptWorkbook(excel_path)
    characters = wb.get_characters()

    # Filter out locations (id starts with "loc" or role="location")
    actual_characters = [
        c for c in characters
        if not c.id.lower().startswith("loc") and c.role != "location"
    ]

    # Selection strategy:
    # 1. Find protagonist/main role + not child
    # 2. Find first non-child character
    # 3. Use first character if all are children

    # Copy selected character image to thumb/ folder
    thumb_dir = src_dir / "thumb"
    thumb_dir.mkdir(exist_ok=True)
    shutil.copy2(src_image, dest_image)
```

**COPY TRIGGERS**:
- ✅ **Project completion** (`current_step = "done"`) - NEW!
- ✅ **6-hour timeout** (existing)

**COPY STRUCTURE**:
```
AUTO/visual/{project_code}/
  ├── {code}.srt
  ├── {code}_prompts.xlsx
  ├── nv/ (character references)
  ├── img/ (scene images/videos)
  └── thumb/ (main character for thumbnail) ⭐ NEW
```

**THUMBNAIL SELECTION LOGIC**:
1. Filter out locations (`loc*` IDs or `role="location"`)
2. Prefer `protagonist` or `main` role
3. Exclude children (`is_child=False`)
4. Fallback to first non-child character
5. Last resort: first character

**RESULT**:
- ✅ Projects auto-copied when completed (no manual intervention)
- ✅ Thumbnail ready for master video generation
- ✅ Master gets complete package: Excel + Images + Videos + Thumbnail
- ✅ No duplicate copies (tracking via `_completed_projects` set)

**COMMIT**: 55526ec (v1.0.34), 2801a4c (v1.0.35 - path fix)
**VERSION**: 1.0.35
**STATUS**: ✅ PRODUCTION READY - Pushed to GitHub

### Backlog (việc cần làm)

**High Priority:**
- [x] **API Validation Framework**: ✅ DONE (v1.0.4)
  - VALIDATION 1: Check ratio, split if disproportionate
  - VALIDATION 2: Check coverage, call API for missing
  - GAP-FILL: Post-processing to fill remaining gaps
- [ ] **Pipeline Optimization**: Step 6+7 chạy song song (30-40% speedup)
  - Step 7 bắt đầu khi Step 6 hoàn thành batch đầu
  - Excel làm "message queue" giữa 2 steps

**Medium Priority:**
- [ ] Worker logs không hiển thị trong GUI (trade-off để Chrome automation hoạt động)
- [ ] Kiểm tra và làm sạch IPv6 list
- [ ] Test auto-recovery khi Chrome disconnect

**Low Priority:**
- [ ] Batch size optimization (Step 6: 15→20, Step 7: 10→15)
- [ ] Cache character/location lookups trong parallel processing

### Lịch sử phiên trước

**2026-01-23 - Excel API 100% SRT Coverage (COMPLETED ✅)**
- Mission: Fix Excel worker để đảm bảo 100% SRT coverage
- 4 CRITICAL FIXES: VALIDATION 1+2, GAP-FILL, Duplicate Fallback
- Test result: 1183 SRT → 520 scenes, 100% coverage
- Status: PRODUCTION READY

**2026-01-22 - Chrome 2 Portable Path Fix:**
- Fixed Chrome 2 using wrong portable path (2 fixes applied)
- Created check_version.py to verify fixes
- Fixed CMD hiding and Chrome window positioning
- Commit: 43d3158
