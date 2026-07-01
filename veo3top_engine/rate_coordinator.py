"""
Bộ điều phối nhịp bắn generate CHUNG toàn máy (chia sẻ giữa TẤT CẢ mã/subprocess).
Vấn đề: N mã × M luồng bắn generate độc lập -> đập bão hoà rate reCAPTCHA/aisandbox chung -> tụt dần.
Cách veo3top: 1 process ghìm nhịp bắn chung. Mình đa-tiến-trình nên cần token-bucket CHUNG (file-based).

Mỗi lần generate (kể cả retry) gọi acquire() -> lấy 1 "vé" từ bucket chung. Bucket refill R vé/giây
=> tổng nhịp bắn toàn máy <= R/giây, không phụ thuộc số mã/luồng -> không tự vỡ rate.

LƯU Ý: chỉ điều phối MÁY NÀY. 1 account bị 3 máy khai thác -> đặt R vừa phải (phần của máy này).
Chỉnh qua env VEO3TOP_GEN_RATE (vé/giây toàn máy). 0 = tắt điều phối.
"""
import os, time, json

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))   # D:\VE3_SUITE
_DIR = os.path.join(_ROOT, ".veo3top_rate")

def _load_rate():
    """Ưu tiên: env VEO3TOP_GEN_RATE > file .veo3top_rate\\RATE.txt > mặc định 5. (0 = tắt điều phối)"""
    v = os.environ.get("VEO3TOP_GEN_RATE")
    if v:
        try: return float(v)
        except Exception: pass
    try:
        return float(open(os.path.join(_DIR, "RATE.txt")).read().strip())
    except Exception:
        return 5.0

RATE = _load_rate()   # vé/giây TOÀN MÁY (mọi mã chung). Sửa file .veo3top_rate\RATE.txt rồi restart để đổi.


def _acquire_lock(lockf, timeout=3.0):
    end = time.time() + timeout
    while time.time() < end:
        try:
            fd = os.open(lockf, os.O_CREAT | os.O_EXCL | os.O_RDWR)
            os.close(fd)
            return True
        except FileExistsError:
            try:
                if time.time() - os.path.getmtime(lockf) > 5:   # dọn lock chết (process crash)
                    os.remove(lockf); continue
            except OSError:
                pass
            time.sleep(0.01)
        except OSError:
            time.sleep(0.01)
    return False


def _release(lockf):
    try:
        os.remove(lockf)
    except OSError:
        pass


def acquire(rate=None, key="gen", max_wait=30.0):
    """Chờ lấy 1 vé từ token-bucket CHUNG toàn máy. Fail-open sau max_wait (không kẹt cứng)."""
    rate = RATE if rate is None else rate
    if not rate or rate <= 0:
        return
    try:
        os.makedirs(_DIR, exist_ok=True)
    except OSError:
        pass
    burst = max(2.0, rate * 2)
    statef = os.path.join(_DIR, key + ".json")
    lockf = statef + ".lock"
    deadline = time.time() + max_wait
    while time.time() < deadline:
        if not _acquire_lock(lockf):
            time.sleep(0.05); continue
        wait = 0.1
        try:
            try:
                st = json.load(open(statef))
            except Exception:
                st = {"tokens": burst, "ts": time.time()}
            now = time.time()
            st["tokens"] = min(burst, st.get("tokens", burst) + (now - st.get("ts", now)) * rate)
            st["ts"] = now
            if st["tokens"] >= 1.0:
                st["tokens"] -= 1.0
                try:
                    json.dump(st, open(statef, "w"))
                except Exception:
                    pass
                _release(lockf)
                return
            wait = (1.0 - st["tokens"]) / rate
            try:
                json.dump(st, open(statef, "w"))
            except Exception:
                pass
        finally:
            _release(lockf)
        time.sleep(min(max(wait, 0.02), 0.5))
    return  # fail-open
