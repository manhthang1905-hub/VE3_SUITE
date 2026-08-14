# -*- coding: utf-8 -*-
"""Nhớ nhịp đã dò được, qua các LẦN CHẠY khác nhau.

════════════════════════════════════════════════════════════════════════════
VÌ SAO CẦN — ĐO NGÀY 14/08/2026
════════════════════════════════════════════════════════════════════════════

`NhipDo` bắt đầu ở 1 và nhân đôi mỗi vòng, một vòng bằng một job::

    1 → 2 → 4 → 8 → 16 → 32 → 64

Với job video ~60 giây, chạm 64 mất **6 phút chạy liên tục**. Mà bên VE3_SUITE
mỗi "mã" là một TIẾN TRÌNH RIÊNG (`ve3_gui.py`: ``queue_ve3_procs = {code:
Popen}``), nên mỗi mã dựng một `NhipDo` mới bắt đầu lại từ 1. Mã xong trước 6
phút thì tiến trình chết mang theo cả bài học, và mã sau lại leo từ đầu.

Chủ dự án đọc được trên bảng của tool, 14/08/2026::

    ảnh : xin  4 → chạy 1 job
    video: xin 64 → chạy 2 job

Không phải cấu hình sai, không phải máy chủ yếu (đo cùng lúc: hàng chờ 1–2
giây, ba worker `idle`, máy chủ mời ~979 chỗ ảnh). Tool đang ở bậc 1–2 của một
cái thang 6 bậc, và cứ mỗi mã mới lại tụt về chân thang.

════════════════════════════════════════════════════════════════════════════
VÌ SAO KHÔNG ĐƠN GIẢN LÀ "BẮT ĐẦU CAO HƠN"
════════════════════════════════════════════════════════════════════════════

Vì con số đúng KHÔNG BIẾT TRƯỚC được: nó là năng lực nhà máy tại thời điểm đó,
và nhà máy thay đổi (tài khoản bị khoá, IP bị phanh, nhà máy khởi động lại).
Gõ cứng một số cao là quay lại đúng cái bệnh đã chữa cả ngày hôm nay. Ngày
12/08/2026 tool đòi 3.072 chỗ ảnh và **giết nhà máy 9 lần trong một ngày**.

Nhớ thì khác gõ cứng: con số đến từ phép ĐO của chính máy này, có hạn dùng, và
vẫn nằm dưới trần động của máy chủ.

════════════════════════════════════════════════════════════════════════════
BA CÁI PHANH
════════════════════════════════════════════════════════════════════════════

1. **Hạn dùng** (:data:`HAN_GIAY`). Nhịp nhớ từ hôm qua nói về một nhà máy
   khác. Quá hạn thì quên, leo lại từ đầu — chậm vài phút còn hơn dựng 64 job
   lên một nhà máy vừa mất 90 tài khoản.
2. **Lùi một bậc** (:data:`CHIA_KHI_NHO`). Vào lại ở MỘT NỬA nhịp đã nhớ, rồi
   để pha leo nhanh nhân đôi — chạm mức cũ sau ĐÚNG MỘT VÒNG thay vì sáu, mà
   vẫn không đặt ngay toàn bộ tải lên một nhà máy chưa kịp nói gì.
3. **Trần máy chủ vẫn là trên cùng.** `NhipDo.dat_tran()` đọc `/v1/me` mỗi lô
   và cắt; bộ nhớ này chỉ đề nghị chỗ BẮT ĐẦU, không có quyền vượt trần.

Hỏng ở đây phải im lặng và vô hại: không đọc/ghi được tệp thì trả `None` và
mọi thứ chạy y như trước khi có bộ nhớ. Một cái tệp hỏng không được phép làm
khách không tạo được ảnh.
"""
from __future__ import annotations

import json
import os
import tempfile
import time
from typing import Any, Dict, Optional

#: Nhịp nhớ cũ hơn ngần này giây thì bỏ. 15 phút: đủ dài để nối hai mã liên
#: tiếp (mã ngắn nhất đo được ~4 phút), đủ ngắn để một nhà máy vừa đổi trạng
#: thái không bị áp con số của trạng thái cũ.
HAN_GIAY = 900.0

#: Vào lại ở `nhớ / 2`. Xem phanh số 2.
CHIA_KHI_NHO = 2.0

#: Ghi thưa: nhịp đổi liên tục trong pha leo, mà mỗi lần ghi là một lần chạm
#: đĩa trên máy đang chạy hàng chục tiến trình. Chỉ ghi khi đã qua ngần này
#: giây HOẶC khi nhịp lập đỉnh mới đáng kể (xem `ghi()`).
GIAN_GHI_GIAY = 10.0

#: Biến môi trường đổi chỗ đặt tệp — bộ kiểm dùng, và người vận hành cũng đổi
#: được khi thư mục mặc định không ghi được.
BIEN_TEP = "SHOPAPI_NHIP_TEP"


def duong_dan_mac_dinh() -> str:
    """Chỗ đặt tệp nhớ. Ưu tiên biến môi trường, sau đó thư mục dữ liệu người dùng.

    Cố ý KHÔNG đặt cạnh mã nguồn: thư mục cài đặt có thể chỉ-đọc (tool chạy từ
    ổ mạng hoặc thư mục Program Files), và một bộ nhớ không ghi được thì im
    lặng vô dụng — đúng kiểu hỏng khó thấy mà cả ngày hôm nay đã dạy.
    """
    tay = os.environ.get(BIEN_TEP)
    if tay:
        return tay
    goc = (os.environ.get("LOCALAPPDATA")
           or os.environ.get("XDG_CACHE_HOME")
           or os.path.join(os.path.expanduser("~"), ".cache"))
    return os.path.join(goc, "shopapi", "nhip-do.json")


class BoNhoNhip:
    """Đọc/ghi nhịp theo khoá (thường là loại job: ``image``/``video``/``tts``).

    An toàn khi nhiều tiến trình cùng dùng: ghi bằng tệp tạm rồi `os.replace`
    (thao tác nguyên tử trên cùng ổ đĩa), và người ghi sau thắng. Không cần
    khoá liên tiến trình — mất một lần ghi chỉ làm mã sau leo chậm hơn vài
    giây, còn dựng khoá liên tiến trình lại thêm một chỗ có thể kẹt.
    """

    def __init__(self, duong_dan: Optional[str] = None,
                 *, han_giay: float = HAN_GIAY,
                 _dong_ho=time.time) -> None:
        self._duong_dan = duong_dan or duong_dan_mac_dinh()
        self._han = float(han_giay)
        self._dong_ho = _dong_ho
        self._ghi_luc = 0.0

    @property
    def duong_dan(self) -> str:
        return self._duong_dan

    def _doc_het(self) -> Dict[str, Any]:
        try:
            with open(self._duong_dan, "r", encoding="utf-8") as f:
                d = json.load(f)
            return d if isinstance(d, dict) else {}
        except (OSError, ValueError):
            # Không có tệp, hỏng JSON, hoặc một tiến trình khác đang ghi dở.
            # Tất cả đều nghĩa là "chưa biết gì" — không phải lỗi cần kêu.
            return {}

    def doc(self, khoa: str) -> Optional[float]:
        """Nhịp còn hạn cho khoá này, hoặc `None`."""
        muc = self._doc_het().get(str(khoa))
        if not isinstance(muc, dict):
            return None
        try:
            nhip = float(muc.get("nhip"))
            luc = float(muc.get("luc"))
        except (TypeError, ValueError):
            return None
        if nhip <= 0:
            return None
        tuoi = self._dong_ho() - luc
        if tuoi < 0 or tuoi > self._han:
            return None            # đồng hồ nhảy lùi, hoặc đã quá hạn
        return nhip

    def bat_dau_tu(self, khoa: str, san: float) -> Optional[float]:
        """Nhịp nên BẮT ĐẦU cho khoá này — đã lùi một bậc. `None` nếu chưa nhớ gì."""
        nho = self.doc(khoa)
        if nho is None:
            return None
        return max(float(san), nho / CHIA_KHI_NHO)

    def ghi(self, khoa: str, nhip: float, *, ep: bool = False) -> bool:
        """Ghi nhịp cho khoá. Trả `True` nếu thật sự chạm đĩa.

        `ep=True` bỏ qua giãn cách — dùng khi đóng mẻ, lúc con số đáng giữ nhất.
        """
        bay_gio = self._dong_ho()
        if not ep and (bay_gio - self._ghi_luc) < GIAN_GHI_GIAY:
            return False
        try:
            nhip = float(nhip)
        except (TypeError, ValueError):
            return False
        if nhip <= 0:
            return False

        d = self._doc_het()
        d[str(khoa)] = {"nhip": nhip, "luc": bay_gio}
        try:
            thu_muc = os.path.dirname(self._duong_dan)
            if thu_muc:
                os.makedirs(thu_muc, exist_ok=True)
            # Tệp tạm CÙNG thư mục: `os.replace` chỉ nguyên tử trong cùng ổ đĩa.
            fd, tam = tempfile.mkstemp(dir=thu_muc or ".", prefix=".nhip-", suffix=".tmp")
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as f:
                    json.dump(d, f)
                os.replace(tam, self._duong_dan)
            except BaseException:
                try:
                    os.unlink(tam)
                except OSError:
                    pass
                raise
        except (OSError, ValueError):
            # `OSError`: đĩa đầy, chỉ-đọc, bị khoá, đường dẫn quá dài.
            # `ValueError`: đường dẫn có ký tự cấm (ví dụ byte 0) — `os.makedirs`
            #   ném cái này chứ KHÔNG ném `OSError`, nên bắt thiếu là bộ nhớ làm
            #   gãy đúng cái việc nó sinh ra để tăng tốc. Bắt được nhờ
            #   `test_thu_muc_khong_ghi_duoc_thi_im`.
            return False           # chạy tiếp như chưa từng có bộ nhớ
        self._ghi_luc = bay_gio
        return True
