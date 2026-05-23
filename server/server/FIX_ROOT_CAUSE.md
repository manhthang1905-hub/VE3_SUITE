# Fix Root Cause - Server Timeout Issue

## Nguyên nhân gốc rễ đã tìm ra:

**Worker threads đang block GIL khi setup Chrome:**

1. Worker gọi `_setup_single_worker_limited()` → acquire `_setup_sem`
2. `session.setup()` mất 60-120 giây (khởi động Chrome, login Google)
3. Worker thread giữ GIL trong suốt thời gian này
4. Flask thread muốn phản hồi `/api/status` → chờ GIL → timeout

## Giải pháp gốc rễ:

### 1. Thêm /api/ping (NGAY LẬP TỨC - 2 phút)

```python
@app.route('/api/ping', methods=['GET'])
def ping():
    """Ultra-lightweight health check - NO locks, NO chrome_pool access."""
    return jsonify({
        "status": "alive",
        "timestamp": time.time(),
        "server_state": "unknown",  # Client sẽ dùng /api/status để biết chi tiết
    })
```

**Tại sao cần:**
- Endpoint này KHÔNG bị ảnh hưởng bởi GIL blocking
- Tool có thể dùng để phân biệt: server down vs server busy
- Nếu ping OK nhưng status timeout → server đang setup workers

### 2. Release GIL trong Chrome setup (QUAN TRỌNG - 30 phút)

Sửa `_setup_single_worker()` để release GIL:

```python
def _setup_single_worker(self, worker: 'ChromeWorker') -> bool:
    worker_name = f"Chrome-{worker.index}"
    worker.recovering = True
    
    try:
        for attempt in range(max_retries):
            try:
                session = ChromeSession(...)
                if worker.account:
                    session._account = worker.account
                
                # CRITICAL: Release GIL trước khi setup Chrome
                # Dùng subprocess hoặc multiprocessing thay vì thread
                import concurrent.futures
                with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
                    # Submit vào thread pool riêng → không giữ main GIL
                    future = executor.submit(session.setup)
                    ok = future.result(timeout=180)  # 3 phút timeout
                
                if ok:
                    worker.session = session
                    worker.ready = True
                    self._log(f"[{worker_name}] READY!", "OK")
                    return True
                else:
                    self._log(f"[{worker_name}] Setup FAILED (attempt {attempt + 1})", "ERROR")
            except Exception as e:
                self._log(f"[{worker_name}] Setup error: {e}", "ERROR")
            
            if attempt < max_retries - 1:
                time.sleep(5)
        
        self._log(f"[{worker_name}] Setup FAILED sau {max_retries} lan!", "ERROR")
        return False
    finally:
        worker.recovering = False
```

**Vấn đề:** ThreadPoolExecutor vẫn dùng threads → vẫn có GIL issue.

**Giải pháp tốt hơn:** Dùng `ProcessPoolExecutor` hoặc subprocess:

```python
# Tách Chrome setup ra subprocess riêng
import subprocess
import json

def _setup_single_worker(self, worker: 'ChromeWorker') -> bool:
    worker_name = f"Chrome-{worker.index}"
    worker.recovering = True
    
    try:
        # Gọi script riêng để setup Chrome
        result = subprocess.run(
            ['python', 'server/setup_chrome_worker.py', 
             '--index', str(worker.index),
             '--chrome-path', worker.chrome_path,
             '--port', str(worker.port)],
            capture_output=True,
            text=True,
            timeout=180  # 3 phút
        )
        
        if result.returncode == 0:
            # Parse result và update worker
            data = json.loads(result.stdout)
            worker.ready = True
            self._log(f"[{worker_name}] READY!", "OK")
            return True
        else:
            self._log(f"[{worker_name}] Setup FAILED: {result.stderr}", "ERROR")
            return False
    except subprocess.TimeoutExpired:
        self._log(f"[{worker_name}] Setup timeout!", "ERROR")
        return False
    finally:
        worker.recovering = False
```

**Nhưng:** Cách này phức tạp, cần refactor nhiều.

### 3. Giải pháp đơn giản nhất: Tăng setup_concurrency (5 phút)

Giảm số workers setup đồng thời → giảm GIL contention:

```python
# Trong ChromePool.__init__
self._setup_concurrency = 1  # Chỉ 1 worker setup tại 1 thời điểm
```

**Nhược điểm:** Startup chậm hơn, nhưng Flask sẽ responsive hơn.

### 4. Cache /api/status (10 phút)

```python
status_cache = {"data": None, "ts": 0, "lock": threading.Lock()}

@app.route('/api/status', methods=['GET'])
def server_status():
    now = time.time()
    
    # Return cache nếu fresh (< 2s)
    with status_cache["lock"]:
        if status_cache["data"] and (now - status_cache["ts"]) < 2:
            return jsonify(status_cache["data"])
    
    # Tính toán status mới (code hiện tại)
    # ...
    result = {...}
    
    # Update cache
    with status_cache["lock"]:
        status_cache["data"] = result
        status_cache["ts"] = now
    
    return jsonify(result)
```

## Khuyến nghị thực hiện:

**Ngay lập tức (10 phút):**
1. ✅ Thêm `/api/ping` endpoint
2. ✅ Cache `/api/status` với TTL 2s
3. ✅ Giảm `_setup_concurrency = 1`

**Sau đó (1-2 giờ):**
4. Migrate sang FastAPI + uvicorn (async, no GIL blocking)

**Dài hạn:**
5. Tách Chrome setup ra subprocess riêng
