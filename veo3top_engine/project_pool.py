"""
project_pool — XOAY VÒNG + TỰ CHỮA project cho 1 account (DÙNG CHUNG ảnh & video).

Tư duy 24/7 (chống gãy dài hạn):
- 1 account có NHIỀU project (đã kiểm ~20). KHÔNG dùng mãi 1 project -> tránh 1 project bị đốt làm account "chết oan".
- Xoay vòng qua các project TỐT mỗi job.
- Project lỗi CẤP-PROJECT thật (PERMISSION_DENIED/NOT_FOUND/project invalid) -> loại khỏi rotation (mark_bad).
  KHÔNG loại khi 'unusual' (đó là lỗi TOKEN reCAPTCHA ~25% bình thường, không phải lỗi project).
- Hết project tốt -> TẠO project mới (create_project) tự động; nếu tạo lỗi -> reset bad (flag có thể tạm) dùng lại.
- Refresh danh sách định kỳ từ COOKIE (không mở chrome) -> nhặt project mới, bỏ project đã xoá.
- Log rõ mỗi lần loại/tạo -> vận hành thấy ngay "chỉ cần đổi project", không sửa lung tung.
"""
import time, threading
import flow_client as fc

REFRESH_SECS = 1800    # refresh danh sách project mỗi 30'


class ProjectRotator:
    def __init__(self):
        self._projects = []
        self._idx = 0
        self._ts = 0.0
        self._bad = set()
        self._lock = threading.Lock()

    def _refresh(self, cookie):
        pl = fc.list_projects(cookie)
        if pl:
            self._projects = pl
            self._ts = time.time()
            self._bad &= set(pl)   # bỏ 'bad' không còn trong list

    def next(self, cookie, fallback=None, log=lambda *_: None):
        """Trả projectId tiếp theo (xoay vòng project TỐT). Tự refresh/tạo/reset. fallback nếu bí."""
        now = time.time()
        with self._lock:
            if cookie and (not self._projects or now - self._ts > REFRESH_SECS):
                try: self._refresh(cookie)
                except Exception: pass
            good = [p for p in self._projects if p not in self._bad]
            if not good:
                # hết project tốt -> tạo mới (self-heal, không kẹt)
                if cookie:
                    try:
                        np = fc.create_project(cookie)
                    except Exception:
                        np = None
                    if np:
                        self._projects.append(np); good = [np]
                        log(f"♻️ tạo project MỚI {str(np)[:8]} (hết project tốt)")
                if not good and self._projects:
                    # tạo lỗi -> flag có thể TẠM -> reset bad, dùng lại (còn hơn kẹt)
                    self._bad.clear(); good = list(self._projects)
            if not good:
                return fallback
            p = good[self._idx % len(good)]
            self._idx += 1
            return p

    def mark_bad(self, pid, log=lambda *_: None):
        """Loại project lỗi CẤP-PROJECT khỏi rotation. Chỉ gọi khi CHẮC lỗi project (không phải token)."""
        if not pid:
            return
        with self._lock:
            if pid not in self._bad:
                self._bad.add(pid)
                log(f"⚠️ project {str(pid)[:8]} lỗi cấp-project -> loại khỏi rotation")

    def stats(self):
        with self._lock:
            return {"total": len(self._projects), "bad": len(self._bad), "good": len(self._projects) - len(self._bad)}


def is_project_error(kind, data):
    """Phân biệt lỗi CẤP-PROJECT (đổi project cứu được) với lỗi token/khác.
    TRUE khi 403 PERMISSION_DENIED / NOT_FOUND / INVALID_ARGUMENT về project. reCAPTCHA unusual/quota -> FALSE."""
    if kind in ("ok", "unusual", "recaptcha_quota", "ratelimit", "auth"):
        return False
    s = str(data or "")
    return ("PERMISSION_DENIED" in s or "NOT_FOUND" in s
            or "project" in s.lower() and ("INVALID" in s or "not found" in s.lower()))
