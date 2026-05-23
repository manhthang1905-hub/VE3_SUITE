# Cải thiện Server Performance - Fix Timeout Issue

## Vấn đề hiện tại:
- Server mất 230+ giây để phản hồi `/api/status`
- Flask `threaded=True` không đủ khi worker threads block GIL
- Chrome operations (CPU/IO intensive) làm Flask không thể phản hồi HTTP

## Giải pháp 1: Thêm /api/ping endpoint (NHANH - ƯU TIÊN)

Thêm endpoint siêu nhẹ không cần lock, không cần chrome_pool:

```python
@app.route('/api/ping', methods=['GET'])
def ping():
    """Lightweight health check - không cần lock, không cần chrome_pool."""
    return jsonify({
        "status": "alive",
        "timestamp": time.time()
    })
```

**Lợi ích:**
- Tool có thể dùng `/api/ping` để check server còn sống
- Nếu ping OK nhưng status timeout → server busy, không phải down
- Không cần sửa logic phức tạp

## Giải pháp 2: Cache server status (TRUNG BÌNH)

Tránh tính toán lại mỗi request:

```python
# Thêm vào đầu file
status_cache = {"data": None, "timestamp": 0}
status_cache_lock = threading.Lock()
STATUS_CACHE_TTL = 2  # Cache 2 giây

@app.route('/api/status', methods=['GET'])
def server_status():
    now = time.time()
    
    # Return cache nếu còn fresh
    with status_cache_lock:
        if status_cache["data"] and (now - status_cache["timestamp"]) < STATUS_CACHE_TTL:
            return jsonify(status_cache["data"])
    
    # Tính toán status mới
    uptime = now - stats['start_time']
    # ... (code hiện tại)
    
    result = {
        "status": "running",
        # ... (response hiện tại)
    }
    
    # Lưu cache
    with status_cache_lock:
        status_cache["data"] = result
        status_cache["timestamp"] = now
    
    return jsonify(result)
```

## Giải pháp 3: Chuyển sang ASGI (DÀI HẠN)

Thay Flask bằng FastAPI + uvicorn:

```python
# Thay vì Flask
from fastapi import FastAPI
import uvicorn

app = FastAPI()

@app.get("/api/status")
async def server_status():
    # Async không block GIL
    ...

if __name__ == '__main__':
    uvicorn.run(app, host='0.0.0.0', port=5000, workers=4)
```

**Lợi ích:**
- True async - không bị GIL blocking
- Multiple workers - scale tốt hơn
- Nhưng cần refactor nhiều code

## Giải pháp 4: Tối ưu locks trong /api/status

Giảm thời gian giữ lock:

```python
@app.route('/api/status', methods=['GET'])
def server_status():
    uptime = time.time() - stats['start_time']
    
    # Snapshot nhanh - giữ lock ngắn nhất
    with queue_lock:
        queue_size = len(task_queue)
    
    # Đọc chrome_pool KHÔNG cần lock (chỉ đọc attributes)
    total_workers = len(chrome_pool.workers) if chrome_pool else 0
    
    # Tính toán bên ngoài lock
    ready_workers = 0
    available_workers = 0
    recovering_workers = 0
    processing = []
    
    if chrome_pool:
        for w in chrome_pool.workers:
            # Đọc nhanh, không gọi methods phức tạp
            if w.ready:
                ready_workers += 1
                if not w.busy:
                    available_workers += 1
            if w.recovering:
                recovering_workers += 1
            if w.busy and w.current_task_id:
                processing.append({
                    "worker": w.index,
                    "task": w.current_task_id[:8] + "...",
                })
    
    # ... rest of code
```

## Khuyến nghị:

**Làm ngay:**
1. ✅ Thêm `/api/ping` endpoint (5 phút)
2. ✅ Cache `/api/status` với TTL 2s (10 phút)

**Làm sau:**
3. Tối ưu locks (30 phút)
4. Migrate sang FastAPI (2-3 giờ)

**Cập nhật tool client:**
- Thử `/api/ping` trước, nếu OK nhưng `/api/status` timeout → server busy
- Tăng timeout từ 6s lên 10s (tạm thời)
