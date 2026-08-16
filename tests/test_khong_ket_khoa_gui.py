"""Không được chạm Tk khi đang giữ `queue_lock` — đó là kẹt chéo hai khoá.

`self._log()` gọi `self.after()`, tức đi vào bộ thông dịch Tcl và khoá riêng
của nó. Một luồng nền ghi log trong khi giữ `queue_lock` sẽ đi tìm khoá Tcl
trong lúc đang cầm `queue_lock`. Luồng vẽ thì làm ngược chiều: đang ở trong Tcl
(vẽ thanh cuộn, `see`, `update_idletasks`) rồi gọi `_queue_claim_progress_owner`
hoặc `_periodic_cleanup` — hai hàm mà việc đầu tiên là `with self.queue_lock`.

    luồng nền : giữ queue_lock  ->  đợi khoá Tcl
    luồng vẽ  : giữ khoá Tcl    ->  đợi queue_lock

`queue_lock` là `threading.Lock`, không hạn chờ. Nên đây là CHẾT HẲN.

Bằng chứng — `%TEMP%\ve3_watchdog.log`, 1.006 lần chẹn luồng vẽ. Lọc riêng
những lần quá 300 giây (có lần **44.766 giây**), khung trong cùng chỉ có:

    156 lan  _queue_claim_progress_owner
     42 lan  _periodic_cleanup
     17 lan  threading.py wait
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

NGUON = Path(__file__).resolve().parents[1] / "tools" / "ve3" / "ve3_gui.py"

#: Mọi thứ đụng tới Tk, dù gián tiếp. `_queue_ve3_skip_log` gọi `_log`.
CHAM_TK = re.compile(
    r"self\._log\(|self\.after\(|self\._queue_ve3_skip_log\(|"
    r"self\.pages\[|\.configure\(|messagebox\.")


def _khoi_giu_khoa(dong):
    """Trả về [(số dòng, nội dung)] của mọi dòng nằm TRONG `with self.queue_lock`."""
    ra = []
    i = 0
    while i < len(dong):
        m = re.match(r"^(\s*)with self\.queue_lock:", dong[i])
        if not m:
            i += 1
            continue
        thut = len(m.group(1))
        j = i + 1
        while j < len(dong):
            ln = dong[j]
            if ln.strip() and (len(ln) - len(ln.lstrip())) <= thut:
                break
            ra.append((j + 1, ln))
            j += 1
        i = j
    return ra


@pytest.fixture(scope="module")
def dong():
    return NGUON.read_text(encoding="utf-8").splitlines(keepends=True)


def test_khong_ghi_log_trong_queue_lock(dong):
    """Đây là luật, không phải khuyến nghị: vi phạm là giao diện chết hẳn."""
    trong = _khoi_giu_khoa(dong)
    assert trong, "khong tim thay khoi nao giu queue_lock -> bai kiem hong"
    pham = [(n, ln.strip()) for n, ln in trong
            if CHAM_TK.search(ln) and not ln.lstrip().startswith("#")]
    assert not pham, (
        "cham Tk khi dang giu queue_lock -> ket cheo hai khoa:\n"
        + "\n".join("  dong {0}: {1}".format(n, t[:100]) for n, t in pham))


def test_hai_ham_cua_LUONG_VE_xin_khoa_CO_HAN(dong):
    """Luồng vẽ không bao giờ được chờ khoá vô hạn.

    Đây là lớp bảo hiểm cho luật ở bài trên: luật kia giữ cho vòng kẹt đừng
    hình thành, hạn chờ này giữ cho nó đừng CHẾT NGƯỜI nếu vẫn hình thành. Bỏ
    một lượt cập nhật tiến độ hay một lượt dọn dẹp thì không ai thấy; treo cửa
    sổ thì người dùng thấy ngay — và đó chính là thứ họ báo.
    """
    src = "".join(dong)
    for ten in ("_queue_claim_progress_owner", "_periodic_cleanup"):
        i = src.find("def {0}(".format(ten))
        assert i > 0, ten
        than = src[i:i + 1200]
        assert "_khoa_hang_cho_luong_ve" in than, (
            "{0} chay tren luong ve ma xin khoa VO HAN -> mot cu ket la treo "
            "han cua so".format(ten))
        assert "with self.queue_lock" not in than, (
            "{0} van con cho khoa vo han o dau do".format(ten))


def test_xin_khoa_co_han_thi_PHAI_NHA(dong):
    """`acquire(timeout=...)` không có `with` — quên `release` là kẹt vĩnh viễn.

    Đổi từ `with` sang `acquire/release` là đánh đổi: được hạn chờ, mất phần
    tự nhả. Nên mỗi chỗ xin phải có `finally: release`.
    """
    src = "".join(dong)
    assert src.count("_khoa_hang_cho_luong_ve()") >= 2
    # Mỗi lần xin phải có đúng một lần nhả đi kèm.
    assert src.count("self.queue_lock.release()") >= src.count("_khoa_hang_cho_luong_ve():"), (
        "so lan nha it hon so lan xin -> co duong ra nao khong nha khoa")


def test_con_giu_loi_giai_thich(dong):
    """Luật này vô nghĩa nếu người sau không biết vì sao có nó."""
    src = "".join(dong)
    assert "_bao_bo_qua" in src
    assert "44.766" in src, "mat con so do that -> luat thanh me tin"


# ── Dựng lại đúng vòng kẹt, bằng luồng thật ──────────────────────────────────


def _thu_vong_ket(luong_nen_cham_tcl, luong_ve_co_han):
    """Trả `True` nếu luồng vẽ thoát ra được trong 2 giây.

    Hai khoá đóng vai `queue_lock` và khoá bộ thông dịch Tcl. Dùng luồng nền
    (`daemon`) nên kể cả kẹt thật thì bộ kiểm vẫn kết thúc.
    """
    import threading
    import time

    khoa, tcl = threading.Lock(), threading.Lock()
    bat_dau, thoat = threading.Event(), threading.Event()

    def nen():
        bat_dau.wait(1.0)
        khoa.acquire()
        try:
            if luong_nen_cham_tcl:
                tcl.acquire()          # `_log` -> `after()` -> cần khoá Tcl
                tcl.release()
            time.sleep(1.5)            # giữ `queue_lock` một lúc
        finally:
            khoa.release()

    def ve():
        tcl.acquire()                  # đang vẽ: giữ khoá Tcl
        try:
            bat_dau.set()
            time.sleep(0.15)           # để luồng nền kịp ôm `queue_lock`
            if luong_ve_co_han:
                if khoa.acquire(timeout=0.3):
                    khoa.release()
            else:
                khoa.acquire()
                khoa.release()
            thoat.set()
        finally:
            tcl.release()

    threading.Thread(target=nen, daemon=True).start()
    threading.Thread(target=ve, daemon=True).start()
    return thoat.wait(2.0)


def test_dung_lai_duoc_vong_ket_cua_ban_cu():
    """Bản cũ: luồng nền chạm Tcl trong khoá + luồng vẽ chờ vô hạn = CHẾT.

    Đây là bằng chứng bằng mã chạy được, không phải suy luận: đúng hai điều
    kiện đó thì luồng vẽ không bao giờ thoát.
    """
    assert _thu_vong_ket(luong_nen_cham_tcl=True, luong_ve_co_han=False) is False, (
        "dung lai khong duoc vong ket -> bai kiem nay khong chung minh gi")


def test_bo_cham_Tcl_trong_khoa_la_het_ket():
    """Phép chữa CHÍNH: luồng nền thôi chạm Tcl khi đang giữ `queue_lock`."""
    assert _thu_vong_ket(luong_nen_cham_tcl=False, luong_ve_co_han=False) is True


def test_han_cho_cua_luong_ve_la_luoi_thu_hai():
    """Kể cả khi vòng kẹt hình thành lại, hạn chờ vẫn cứu được cửa sổ."""
    assert _thu_vong_ket(luong_nen_cham_tcl=True, luong_ve_co_han=True) is True
