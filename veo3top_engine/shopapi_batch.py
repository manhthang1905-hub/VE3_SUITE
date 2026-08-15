"""Chạy CẢ MẺ job shopapi cùng lúc — số luồng do **máy chủ** quyết, không gõ cứng.

═══════════════════════════════════════════════════════════════════════════════
 VÌ SAO CÓ FILE NÀY
═══════════════════════════════════════════════════════════════════════════════

Nhánh API bản đầu gửi từng job một: gửi → chờ xong → gửi cái kế. Nghĩa là trên
máy chủ **lúc nào cũng chỉ có đúng 1 job của bạn**, trong khi trần thật thường
lớn hơn cả chục lần. Với 200 scene × ~90 giây/ảnh, chạy 1 job là 5 tiếng; chạy
đúng trần (giả sử 12) là ~25 phút. Chỗ ăn thời gian không nằm ở tốc độ của máy
chủ, mà nằm ở chỗ tool để nhà máy ngồi không.

Nhưng "cứ bắn thật nhiều" cũng sai, và sai theo kiểu tốn tiền:

* trần máy chủ là mức **KHÔNG ĐƯỢC VƯỢT**, không phải mức **PHẢI CHẠY**;
* con số ấy đã cũ ngay khi đọc xong — mười khách khác có thể vừa xếp hàng;
* bắn quá tay thì ăn `429`/`503`, và nếu bắt nhầm thành "job hỏng" thì cả mẻ
  chết oan đúng vào lúc đông khách nhất.

Nên module này làm ba việc, và chỉ ba việc:

1. **Hỏi trần thật** (`GET /v1/me`) trước MỖI lô, chặn trên bằng trần cứng và
   bằng giới hạn người dùng đã đặt trong tool.
2. **Tự dò nhịp** quanh trần đó (AIMD — mượt thì +1, `429` chia đôi, `503` dừng
   hẳn rồi thăm dò lại bằng đúng 1 job). Mượn thẳng `NhipDo` của SDK khi có.
3. **Không bao giờ đánh mất việc của khách**: job bị từ chối ở cửa quay về ĐẦU
   hàng chờ; job hỏng thật thì chỉ mình nó hỏng, không kéo cả mẻ.

═══════════════════════════════════════════════════════════════════════════════
 VÌ SAO KHÔNG DÙNG THẲNG `client.chay_ca_me` CỦA SDK
═══════════════════════════════════════════════════════════════════════════════

`ShopAPI.chay_ca_me` làm đúng phần điều nhịp, nhưng nó nhận **tham số job** và
trả **đối tượng job** — trong khi một "việc" của tool VE3 không phải một job:
nó là cả một khối gồm viết lại prompt khi dính policy, ghi Excel dưới khoá, tải
file về đúng `img/X.png`, và đẩy `@@PROG|`/`@@ITEM|` lên GUI. Nhét khối đó vào
`chay_ca_me` là không nhét được.

Và có một chỗ nữa quan trọng hơn: `chay_ca_me` gọi `future.result()` trần, nên
**một prompt bị từ chối nội dung sẽ ném lỗi ra ngoài và giết cả mẻ** — chấp
nhận được với một mẻ 5 câu TTS, không chấp nhận được với 200 scene chạy 4 tiếng.

Nên module này giữ nguyên cơ chế nhịp của SDK (`NhipDo`, không chép lại) nhưng
thay phần "chạy một job" bằng một hàm do nơi gọi đưa vào.
"""

from __future__ import annotations

import math
import os
import threading
import time
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor
from concurrent.futures import wait as futures_wait

try:                       # chạy trong tool: import cùng thư mục veo3top_engine
    import shopapi_common as _sc
except ImportError:        # chạy như một gói: import tương đối
    from . import shopapi_common as _sc  # type: ignore

__all__ = [
    "so_luong_song_song",
    "chay_ca_me",
    "trong_me",
    "bao_hang_cho",
    "bao_nghen",
    "CongHangCho",
    "CHO_KHI_DUNG",
    "CHO_TOI_DA",
    "HE_SO_DANG_BAY",
    "HE_SO_NGUONG_VI_TRI",
    "BIEN_AN_TOAN_HAN",
    "LUI_NHIP_GIAY",
]

#: Ngủ bao lâu mỗi lần trước khi hỏi lại, khi nhà máy đang dừng.
#:
#: 30 giây là con số của SDK (`_nhip_do.CHO_KHI_DUNG`). Ngắn hơn thì chỉ đốt hạn
#: mức đọc trạng thái — trần không mở ra vì ta hỏi nhiều hơn, nó chỉ mở khi có
#: máy xử lý báo danh.
CHO_KHI_DUNG = 30.0

#: Tổng thời gian CHỜ tối đa cho một mẻ trước khi bỏ cuộc.
#:
#: Phải có trần này, nếu không nhà máy dừng qua đêm là tool treo qua đêm mà
#: người dùng không biết đang chờ hay đã chết. Hết ngân sách thì bỏ số việc còn
#: lại kèm log TO — chúng vẫn còn nguyên trong Excel (status ≠ done) nên lượt
#: chạy sau nhặt lại được, không mất gì.
CHO_TOI_DA = 300.0

#: Được phép để **bao nhiêu job ĐANG BAY** so với trần chạy song song.
#:
#: "Đang bay" = đã gửi lên máy chủ, chưa nhận kết quả — gồm cả job đang chạy lẫn
#: job còn nằm xếp hàng. Trần song song KHÔNG nói gì về con số này.
#:
#: VÌ SAO 1,5: 1,0 thì cái ghế vừa trống phải chờ trọn một vòng mạng mới có
#: người ngồi (nhà máy ngồi không, mất công suất); 4,0 thì đúng bằng ca
#: 07/08/2026 (66 ÷ 16 ≈ 4,1) — đệm dày tới mức job xếp hàng lâu hơn cả hạn chờ
#: của chính nó rồi chết trong hàng. Nửa vòng là đủ lấp chỗ trống ngay lập tức
#: mà không dựng ra một hàng chờ chết người.
HE_SO_DANG_BAY = 1.5

#: `queue_position` vượt quá `trần × hệ số này` thì **NGỪNG GỬI HẲN**.
#:
#: Đứng thứ `trần + 1` nghĩa là phải chờ trọn một lượt dựng của cả xưởng mới tới
#: lượt. Nhồi thêm lúc đó không làm ai nhanh lên một giây nào.
HE_SO_NGUONG_VI_TRI = 1.0

#: Nhân `estimated_seconds` với biên này rồi mới so với hạn chờ của job.
#: Ước lượng của máy chủ là LẠC QUAN (chưa tính khách chen vào sau), nên còn
#: dưới 20% biên thì coi như đã hết.
BIEN_AN_TOAN_HAN = 1.2

#: Ăn `429` / `resource_exhausted` thì cả mẻ im ngần này giây trước khi gửi tiếp.
LUI_NHIP_GIAY = 15.0

#: Cờ "luồng này đang chạy trong một mẻ" — xem :func:`trong_me`.
#: Cũng giữ luôn cổng hàng chờ của mẻ đang chạy — xem :func:`bao_hang_cho`.
_cuc_bo = threading.local()


class CongHangCho:
    """Gác cổng: **giữ số job ĐANG BAY trong tầm máy chủ tiêu hoá được**.

    ═══ VÌ SAO CẦN THÊM LỚP NÀY, ĐÃ CÓ `NhipDo` RỒI ═══

    `NhipDo` (AIMD) là vòng dò **theo sau**: nó chỉ biết có nghẽn khi một job đã
    xong (đo thời gian nằm hàng chờ) hoặc khi đã ăn `429`. Ngày 07/08/2026 trên
    máy chủ thật, tín hiệu đó **không bao giờ tới**: 33 job nằm xếp hàng cho tới
    lúc hết hạn, chưa chạy một giây nào, nên không có job nào "xong êm" để báo
    độ trễ, và máy chủ cũng chẳng trả `429` — nó nhận job bình thường rồi để
    chúng chết già trong hàng. Tổng kết 20 phút: 66 job bắn ra, nhà máy tiêu
    hoá ~16, **27 job hỏng** (14 "vượt quá thời gian chờ" + 13 "không còn đủ
    thời gian để thử lại"), **kho tài khoản KHÔNG hề cạn**.

    Cổng này là tín hiệu **đi trước**: nó đọc `queue_position` /
    `estimated_seconds` mà máy chủ đã trả sẵn ngay lúc nhận job, nên biết hàng
    dài **trước khi** job đầu tiên kịp chết.

    Hai lớp bổ sung cho nhau, không thay thế nhau: `NhipDo` chỉnh *tốc độ*,
    cổng này chặn *số lượng đang bay*. Bỏ lớp nào cũng để lọt một kiểu hỏng.

    **Thuần tuý, không tự gọi mạng, không tự ngủ** — đồng hồ tiêm được, nên bài
    kiểm dựng lại nguyên buổi 07/08 trong vài mili-giây.
    """

    def __init__(self, tran=1, han_giay=0.0, he_so_dang_bay=None,
                 he_so_nguong_vi_tri=None, bien_an_toan=None, lui_nhip_giay=None,
                 cho_khi_dung=None, _dong_ho=time.monotonic):
        # ⚠ `None` = "đọc hằng số của module NGAY LÚC NÀY", cố ý không gán thẳng
        # hằng số làm giá trị mặc định của tham số. Mặc định của tham số được
        # chốt lúc `def` chạy, nên `monkeypatch.setattr(sb, "LUI_NHIP_GIAY", 0)`
        # sẽ **không có tác dụng** — và bài kiểm nào cần rút ngắn quãng lùi nhịp
        # sẽ lặng lẽ chờ thật 15 giây thay vì báo lỗi.
        #: Hạn chờ của MỘT job (giây). `0` = không so hạn.
        self._han_giay = max(0.0, float(han_giay or 0.0))
        self._he_so_bay = max(1.0, float(
            HE_SO_DANG_BAY if he_so_dang_bay is None else he_so_dang_bay))
        self._he_so_vi_tri = max(0.0, float(
            HE_SO_NGUONG_VI_TRI if he_so_nguong_vi_tri is None else he_so_nguong_vi_tri))
        self._bien = max(1.0, float(
            BIEN_AN_TOAN_HAN if bien_an_toan is None else bien_an_toan))
        self._lui_giay = max(0.0, float(
            LUI_NHIP_GIAY if lui_nhip_giay is None else lui_nhip_giay))
        self._cho_khi_dung = max(0.0, float(
            CHO_KHI_DUNG if cho_khi_dung is None else cho_khi_dung))
        self._dong_ho = _dong_ho

        self._tran = max(0, int(tran))
        self._dang_bay = 0
        self._vi_tri = None
        self._uoc_giay = None
        self._dung_toi = 0.0
        self._ly_do = ""
        self._khoa = threading.Lock()

    # ── Đọc trạng thái ───────────────────────────────────────────────────────

    @property
    def dang_bay(self):
        """Bao nhiêu job ĐÃ GỬI mà CHƯA xong — con số mà ca 07/08 nói tới."""
        with self._khoa:
            return self._dang_bay

    @property
    def vi_tri_hang_cho(self):
        """`queue_position` mới nhất máy chủ trả. `None` = chưa gửi job nào."""
        with self._khoa:
            return self._vi_tri

    def tran_dang_bay(self):
        """Trần số job đang bay = `ceil(trần × hệ số)`, tối thiểu 1."""
        with self._khoa:
            return self._tran_dang_bay()

    def cho_bao_lau(self):
        """Còn mấy giây nữa mới được gửi tiếp. `0.0` = gửi được ngay."""
        with self._khoa:
            return max(0.0, self._dung_toi - self._dong_ho())

    def ly_do(self):
        """Vì sao cổng đang đóng — câu để đẩy thẳng lên log/GUI."""
        with self._khoa:
            self._cho_phep()
            return self._ly_do

    def mo_ta(self):
        """Một dòng cho log. **Người dùng phải thấy là đang chờ, không phải treo.**"""
        with self._khoa:
            con = max(0.0, self._dung_toi - self._dong_ho())
            phan = ["dang bay {0}/{1}".format(self._dang_bay, self._tran_dang_bay()),
                    "tran may chu {0}".format(self._tran)]
            if self._vi_tri is not None:
                phan.append("dung thu {0} trong hang".format(self._vi_tri))
            if self._uoc_giay is not None:
                phan.append("uoc cho {0:.0f}s".format(self._uoc_giay))
            if con > 0:
                phan.append("dang lui nhip, con {0:.0f}s".format(con))
            return " | ".join(phan)

    # ── Tín hiệu vào ─────────────────────────────────────────────────────────

    def dat_tran(self, tran):
        """Trần mới đọc từ `GET /v1/me`. `0` = nhà máy loại đó đang dừng."""
        if tran is None:
            return
        with self._khoa:
            tran = max(0, int(tran))
            self._tran = tran
            if tran == 0:
                self._dung_toi = max(self._dung_toi,
                                     self._dong_ho() + self._cho_khi_dung)
                self._ly_do = (
                    "nha may DANG DUNG (tran song song = 0) -> cho {0:.0f}s roi hoi lai; "
                    "job gui luc nay bi tu choi o cua va KHONG bi tru tien"
                    .format(self._cho_khi_dung))

    def ghi_nhan_tao(self, vi_tri=None, uoc_giay=None):
        """Máy chủ vừa nhận một job — **đọc hai trường mà không tool nào chịu đọc**.

        Gọi ngay sau phản hồi `202`, trước cả lúc bắt đầu chờ kết quả: đó là
        khoảnh khắc duy nhất tool biết hàng chờ dài bao nhiêu mà không tốn thêm
        một lời gọi nào.

        Trường nào máy chủ không trả (`None`) thì **giữ nguyên con số đang
        biết** — không đoán bừa, và tuyệt đối không coi "không biết" là "rỗng".
        """
        with self._khoa:
            if vi_tri is not None:
                try:
                    self._vi_tri = max(0, int(vi_tri))
                except (TypeError, ValueError):
                    pass
            if uoc_giay is not None:
                try:
                    self._uoc_giay = max(0.0, float(uoc_giay))
                except (TypeError, ValueError):
                    pass

    def bi_nghen(self, cho=None):
        """`429` / `resource_exhausted` → **lùi nhịp cho cả mẻ**, không gửi lại ngay.

        Một luồng ăn `429` rồi tự nghỉ là chưa đủ: các luồng còn lại vẫn bắn
        tiếp thì máy chủ vẫn nhận đủ chừng ấy job ngay sau câu "tôi đang ngộp".
        """
        with self._khoa:
            giay = self._lui_giay if cho is None else max(0.0, float(cho))
            self._dung_toi = max(self._dung_toi, self._dong_ho() + giay)
            self._ly_do = (
                "may chu bao qua tai (429 / resource_exhausted) -> ca me nghi {0:.0f}s "
                "roi gui tiep, KHONG gui lai ngay".format(giay))

    def nha_may_dung(self, cho=None):
        """`503 engine_unavailable` — không còn máy xử lý nào online."""
        with self._khoa:
            giay = self._cho_khi_dung if cho is None else max(0.0, float(cho))
            self._tran = 0
            self._dung_toi = max(self._dung_toi, self._dong_ho() + giay)
            self._ly_do = (
                "nha may DANG DUNG (503) -> cho {0:.0f}s roi tham do lai bang dung 1 job; "
                "job bi tu choi o cua KHONG bi tru tien".format(giay))

    # ── Giữ chỗ / trả chỗ ────────────────────────────────────────────────────

    def giu_cho(self, xin=1):
        """Xin `xin` chỗ, trả về số chỗ **thật sự giữ được** (có thể 0).

        Kiểm tra và giữ chỗ nằm TRONG CÙNG một khoá: tách ra hai bước thì mọi
        luồng đều thấy "còn chỗ" rồi cùng gửi — đúng kiểu lỗi đẻ ra 66 job.
        """
        with self._khoa:
            duoc = min(max(0, int(xin)), self._cho_phep())
            self._dang_bay += duoc
            return duoc

    def tra_cho(self, so=1):
        """Một job rời khỏi bầu trời (xong, hỏng, hay bị trả về hàng chờ).

        Cũng **rút ngắn hàng chờ đang biết đi một**: job của ta ra khỏi hàng thì
        người phía sau nhích lên. Không có luật này thì một `queue_position` cũ
        khoá cổng vĩnh viễn (số mới chỉ có khi gửi job mới, mà job mới thì đang
        bị chính nó chặn) — vòng luẩn quẩn, tool treo.
        """
        with self._khoa:
            self._dang_bay = max(0, self._dang_bay - max(0, int(so)))
            if self._vi_tri is not None:
                self._vi_tri = max(0, self._vi_tri - max(0, int(so)))

    # ── Bên trong (gọi khi ĐANG giữ `_khoa`) ─────────────────────────────────

    def _tran_dang_bay(self):
        return max(1, int(math.ceil(max(1, self._tran) * self._he_so_bay)))

    def _cho_phep(self):
        con = self._dung_toi - self._dong_ho()
        if con > 0:
            # Quãng lùi nhịp THẮNG TẤT CẢ, kể cả chốt thăm dò bên dưới: máy chủ
            # vừa nói "đừng gửi nữa" thì trời trống cũng không phải cái cớ.
            if not self._ly_do:
                self._ly_do = "dang lui nhip, con {0:.0f}s".format(con)
            return 0

        n = self._cho_phep_thuong()
        if n > 0:
            return n

        # CHỐT CHỐNG TREO: cổng đóng mà trời TRỐNG thì cho qua đúng MỘT job.
        # Mọi con số ở trên đều đến từ lần gửi TRƯỚC, nên không job nào bay thì
        # không bao giờ có số mới — một `queue_position` xấu sẽ khoá cổng vĩnh
        # viễn. Job thăm dò mang về con số tươi và phá được vòng đó.
        # KHÔNG áp dụng khi `tran == 0`: lúc đó job thăm dò chắc chắn ăn 503.
        if self._dang_bay == 0 and self._tran > 0:
            return 1
        return 0

    def _cho_phep_thuong(self):
        self._ly_do = ""

        if self._tran <= 0:
            self._ly_do = ("nha may DANG DUNG (tran song song = 0) -> khong gui job "
                           "vao cho chac chan bi tu choi")
            return 0

        nguong = max(1, int(math.ceil(self._tran * self._he_so_vi_tri)))
        if self._vi_tri is not None and self._vi_tri > nguong:
            self._ly_do = (
                "HANG CHO DANG DAI (job vua gui dung thu {0}, nguong {1}) -> NGUNG gui "
                "them, doi bot. Nhoi tiep vao hang nay chi de job moi chet vi het han "
                "- su co 07/08/2026 mat 27 job dung kieu do".format(self._vi_tri, nguong))
            return 0

        if (self._han_giay > 0 and self._uoc_giay is not None
                and self._uoc_giay * self._bien > self._han_giay):
            self._ly_do = (
                "may chu uoc phai cho {0:.0f}s moi toi luot, ma han cho mot job chi co "
                "{1:.0f}s -> gui them luc nay la gui job di chet trong hang"
                .format(self._uoc_giay, self._han_giay))
            return 0

        con_cho = self._tran_dang_bay() - self._dang_bay
        if con_cho <= 0:
            self._ly_do = (
                "dang co {0} job bay tren may chu (tran {1} = tran song song {2} x {3:.1f}) "
                "-> cho bot roi gui tiep".format(
                    self._dang_bay, self._tran_dang_bay(), self._tran, self._he_so_bay))
            return 0
        return con_cho


def bao_hang_cho(vi_tri, uoc_giay):
    """Nhánh gửi job báo về "hàng đang dài bao nhiêu" — cho cổng của mẻ đang chạy.

    Cùng kiểu với :func:`trong_me`: cờ nằm ở `threading.local` nên mỗi luồng
    trong lô tự tìm đúng cổng của mẻ mình, và **gọi ngoài mẻ thì không làm gì
    cả** — `shopapi_image_client.generate_image` gọi lẻ một phát vẫn chạy y như
    cũ, không cần biết cổng là gì.
    """
    cong = getattr(_cuc_bo, "cong", None)
    if cong is not None:
        cong.ghi_nhan_tao(vi_tri, uoc_giay)


def bao_nghen(cho=None):
    """Nhánh gửi job báo về một cú `429` / `resource_exhausted`."""
    cong = getattr(_cuc_bo, "cong", None)
    if cong is not None:
        cong.bi_nghen(cho)


def trong_me():
    """Luồng hiện tại có đang chạy bên trong :func:`chay_ca_me` không?

    VÌ SAO CẦN: nhánh gửi job phải xử lý `429`/`503` theo HAI cách khác nhau.

    * Trong một mẻ → **ném** :class:`shopapi_common.BiNghen` để vòng dò nhịp bên
      ngoài thấy được cú nghẽn, hạ nhịp, và trả việc về hàng chờ.
    * Ngoài mẻ (ví dụ `_submit_image` gọi lẻ một phát) → **không được ném**:
      hợp đồng của `_submit_image` là trả đúng 4 phần tử, ném ra là làm sập chỗ
      gọi vốn chẳng biết `BiNghen` là gì.

    Cờ nằm ở `threading.local` vì mỗi luồng trong lô có câu trả lời riêng.
    """
    return bool(getattr(_cuc_bo, "trong_me", False))


# ── Nhịp ─────────────────────────────────────────────────────────────────────


class _NhipDoDuPhong:
    """Bản AIMD tối giản, chỉ dùng khi SDK trên máy chưa có `_nhip_do`.

    Cố ý viết ngắn và **cùng chữ ký** với `NhipDo` của SDK: bản thật mới là bản
    được chăm sóc (có cả tín hiệu độ trễ hàng chờ), bản này chỉ để tool cũ không
    chết. Không chép logic đo độ trễ sang đây — chép là hai bản trôi khác nhau.
    """

    def __init__(self, san=1, bat_dau=1):
        self._san = max(1, int(san))
        self._nhip = float(max(self._san, int(bat_dau)))
        self._tran = None
        self._chuoi = 0
        self._dung_toi = 0.0
        self._tham_do = False
        self._khoa = threading.Lock()

    def dat_tran(self, tran):
        if tran is None:
            return
        tran = int(tran)
        with self._khoa:
            if tran <= 0:
                self._tran = 0
                self._dung(CHO_KHI_DUNG)
                return
            self._tran = tran
            if self._nhip > tran:
                self._nhip = float(tran)

    def cho_phep(self):
        with self._khoa:
            if time.monotonic() < self._dung_toi:
                return 0
            if self._tham_do:
                return 1
            n = int(self._nhip)
            if self._tran is not None:
                n = min(n, self._tran)
            return max(self._san, n)

    def cho_bao_lau(self):
        with self._khoa:
            return max(0.0, self._dung_toi - time.monotonic())

    def xong(self, cho_hang_doi=None):
        with self._khoa:
            if self._tham_do:
                # Nhà máy vừa đứng dậy: vào lại từ SÀN, không phải từ chỗ đã ngã.
                self._tham_do = False
                self._nhip = float(self._san)
                self._chuoi = 0
                return
            self._chuoi += 1
            if self._chuoi >= max(1, int(self._nhip)):
                self._chuoi = 0
                moi = self._nhip + 1.0
                if self._tran is not None:
                    moi = min(moi, float(max(self._tran, self._san)))
                self._nhip = moi

    def bi_chan(self, cho=None):
        with self._khoa:
            self._nhip = max(float(self._san), self._nhip / 2.0)
            self._chuoi = 0

    def nha_may_dung(self, cho=None):
        with self._khoa:
            self._dung(CHO_KHI_DUNG if cho is None else max(0.0, float(cho)))

    def mo_ta(self):
        with self._khoa:
            return "nhip {0:.1f} (ban du phong)".format(self._nhip)

    def _dung(self, cho):
        self._nhip = float(self._san)
        self._chuoi = 0
        self._dung_toi = time.monotonic() + cho
        self._tham_do = True


def _tao_nhip(bat_dau=None):
    """`NhipDo` của SDK khi có; không có thì bản dự phòng ở trên.

    Ưu tiên bản của SDK vì nó còn nhìn được **thời gian job nằm hàng chờ** —
    tín hiệu nghẽn nhìn thấy TRƯỚC khi phải ăn `429`.

    ═══ VÌ SAO BẮT ĐẦU Ở TRẦN MÁY CHỦ, KHÔNG PHẢI Ở 1 ═══

    `NHIP_DAU = 1` của SDK có lý do chính đáng, và docstring của nó nói rõ:
    hàng trăm tool lạ cùng khởi động buổi sáng mà mỗi cái vọt thẳng lên trần
    thì chúng dựng ra đúng cơn nghẽn mà chúng định tránh. Đúng — CHO MỘT SDK
    CÔNG CỘNG.

    Ở đây thì sai, và sai rất đắt. Đo ngày 11/08/2026:

      * `GET /v1/me` cấp **691 chỗ ảnh / 374 chỗ video**, và nói nguyên văn:
        *"đang chừa 77 chỗ cho khách mới, và hiện chỉ có bạn đang dùng — gửi
        tối đa 691 job cùng lúc thì chúng chạy NGAY."*
      * Luật tăng là **+1 mỗi lô mượt**, nên muốn đạt nhịp N phải chạy hết
        N(N+1)/2 job. Một mã 87 scene bò tới nhịp 12 là hết việc; chạm 691 cần
        ~239.000 job liên tục — không bao giờ tới.
      * Kết quả đo trên chính lớp này: bình quân **6,7 job cùng lúc = 1% chỗ
        được cấp**. Số này khớp với 5,6 job/lúc đo ở log nhà máy.
      * Quy ra thời gian: 4.206 job còn tồn chạy hết trong **~33 tiếng** thay vì
        **~10 phút**.

    Máy chủ ĐÃ tính phần chia (sức chứa trừ dự phòng, chia cho số khách đang
    chờ) và ĐÃ chừa sẵn chỗ cho khách mới. Client dò lại từ 1 là đề phòng hai
    lần cho cùng một chuyện, mà lần thứ hai thì mù — nó không biết nhà máy rộng
    bao nhiêu, chỉ biết bò lên từng bước.

    Nên: **bắt đầu ở đúng con số máy chủ cấp**, rồi để AIMD làm việc nó giỏi
    nhất là ĐI XUỐNG khi thực tế phản đối (429 chia đôi, 503 dừng hẳn, hàng chờ
    dài thì hạ). Chặn trên vẫn nguyên vẹn: `_hoi_tran` đã kẹp qua cả trần cứng
    lẫn trần người dùng trước khi con số tới được đây.
    """
    try:
        _sc.bootstrap_sdk()
        from shopapi._nhip_do import NhipDo
        if bat_dau is None:
            return NhipDo()
        return NhipDo(bat_dau=max(1, int(bat_dau)))
    except Exception:
        return _NhipDoDuPhong()


# ── Hỏi trần ─────────────────────────────────────────────────────────────────


#: Chờ job xong lâu nhất ngần này giây rồi ngoi lên kiểm `dung_lai` một lượt.
#:
#: Đây là độ trễ tối đa từ lúc khách bấm Dừng tới lúc tool thôi gửi thêm. Ngắn
#: hơn thì vòng ngoài quay không; dài hơn thì nút Dừng trông như bị kẹt.
NHIP_KIEM_DUNG = 2.0

#: Giữ trần đã đọc ngần này giây trước khi hỏi `/v1/me` lại.
#:
#: ⚠ CON SỐ NÀY LÀ HÀNG RÀO CHỐNG TỰ BẮN VÀO CHÂN, ĐỪNG HẠ VỀ 0.
#:
#: Từ khi bỏ hàng rào mỗi lô (xem `_thu_hoach`), vòng lặp không còn quay 13 lần
#: cho một mẻ 88 việc mà quay gần một lần cho MỖI JOB xong — tức ~88 lượt hỏi
#: `/v1/me` thay vì 13, nhân với 8 tiến trình mã chạy song song.
#:
#: CONTRACT.md §8.2b liệt kê đúng chuyện này vào mục việc-đừng-làm: *"Hỏi GET
#: /v1/me trước mỗi request. Thừa, và tự đốt hạn mức đọc trạng thái."* Đốt hết
#: hạn mức thì `_hoi_tran` bắt đầu trả về mức đoán, và mức đoán kéo nhịp xuống —
#: tức là tối ưu thông lượng xong lại tự bóp thông lượng, qua một đường vòng mà
#: không có dòng log nào nối hai đầu lại.
#:
#: 15 giây: trần máy chủ đổi theo số khách đang chờ, không đổi theo từng giây.
TRAN_TTL = 15.0

#: Coi là "đang đứng sát trần" từ mức này trở lên. Dùng để đọc đúng nghĩa một
#: cú `429`: sát trần thì đó là TRẦN SAI, còn xa trần mới là GỬI QUÁ NHANH.
#: 0,85 vì số job đang bay dao động quanh trần chứ không đứng yên đúng ở đó.
SAT_TRAN = 0.85

#: Mép thật đo được sống bao lâu. Phải CÓ HẠN: một con số chỉ đi xuống là cái
#: bẫy đã sập một lần ở lớp này (ngân sách luồng ghim ở sàn rồi không tự gỡ).
#: Nhà máy rộng ra, hay khách khác nghỉ, thì mép cũ thành xiềng.
MEP_THAT_TTL = 120.0


#: Tự điều tiết BẬT sẵn. Tắt bằng `SHOPAPI_TU_DIEU_TIET=0` khi cần ghim cứng
#: số luồng để đo đạc — chứ không phải để chạy thật.
#: Mở bao nhiêu job khi CHƯA từng đọc được `/v1/me` lần nào.
#:
#: Không phải 1 (tự bóp cả mẻ vì một cú mạng chập), cũng không phải trần cứng
#: (đập vào nhà máy bằng một con số bịa). Đủ để mẻ chạy thật sự, và đủ nhỏ để
#: một lần AIMD chia đôi là về mức lịch sự.
TRAN_KHOI_DONG_MU = 32


def _tu_dieu_tiet_mac_dinh():
    return (os.environ.get("SHOPAPI_TU_DIEU_TIET") or "1").strip() not in ("0", "false", "False")


def _hoi_tran(loai, tran_tool=None, client=None, api_key=None, log=print,
              truoc_do=None, so_ban=None, tu_dieu_tiet=None, viec_con_lai=None,
              dang_bay=None):
    """Một lời hỏi `/v1/me`, đã chặn trên. Trả `0` khi nhà máy đang dừng.

    `truoc_do` là trần đọc được lần gần nhất. Hỏi không được thì trả LẠI con số
    đó thay vì tụt về `1` — xem khối cảnh báo trong thân hàm.

    Ba mức chặn, theo đúng thứ tự nghiêm ngặt dần:

    1. trần ĐỘNG của máy chủ — nguồn sự thật, đọc lại mỗi lô;
    2. trần CỨNG của loại job — chốt chặn phòng khi mức động trả về số vô lý;
    3. trần của TOOL — giới hạn người dùng đã đặt, không được phá dù máy chủ
       có rộng đến đâu (máy họ, mạng họ, tiền họ).

    Hỏi không được thì trả `1`: đoán thấp còn hơn đứng im, và cũng còn hơn đoán
    cao rồi đập vào máy chủ bằng một con số bịa.

    `tu_dieu_tiet` (mặc định BẬT) đổi vai của `tran_tool`: bật thì trần đến từ
    máy chủ chia cho số tiến trình đang chạy thật, và `tran_tool` không còn
    được dùng; tắt thì quay về hành vi cũ — `tran_tool` là trần cứng.
    """
    if tu_dieu_tiet is None:
        tu_dieu_tiet = _tu_dieu_tiet_mac_dinh()
    try:
        # `doc_tran_chi_tiet` đã tự nuốt lỗi mạng, nhưng vẫn bọc thêm một lớp:
        # hàm này chạy trong vòng lặp của cả mẻ, một ngoại lệ lọt ra là chết cả
        # mẻ vì một lời hỏi trạng thái — quá đắt cho thứ chỉ để đoán số luồng.
        #
        # ⚠ ĐỌC `hard_cap` TỪ MÁY CHỦ, ĐỪNG DÙNG BẢN CHÉP. Đo 15/08/2026:
        # `/v1/me` nói ảnh `hard_cap 1536`, video `832`; hằng số chép trong SDK
        # vẫn là `384` và `64`. `min(limit, trần_cứng)` với bản chép cũ đang bóp
        # video từ 374 xuống 64 — 5,8 lần — mà không một dòng log nào nói.
        tran, cung_sv = _sc.doc_tran_chi_tiet(loai, api_key=api_key, client=client)
        tran = int(tran)
    except Exception:
        tran, cung_sv = -1, 0

    if tran < 0:
        # ⚠ HỎNG MỘT LƯỢT ĐỌC KHÔNG PHẢI LÀ "NHÀ MÁY CHỈ CÒN MỘT CHỖ".
        #
        # Bản trước trả thẳng `1` với lý do "đoán thấp còn hơn đứng im". Lý do đó
        # đúng cho lượt đọc ĐẦU TIÊN, khi ta chưa biết gì. Từ lượt thứ hai trở đi
        # nó thành một cái bẫy: `NhipDo.dat_tran(1)` KÉO NHỊP XUỐNG 1 ngay lập
        # tức, và luật tăng +1 mỗi lô nghĩa là một cú mạng chập nửa giây đổi lấy
        # cả phần còn lại của mẻ chạy ở tốc độ bò.
        #
        # Trần vừa đọc được cách đây mươi giây là ước lượng tốt hơn hẳn số 1, và
        # nếu nhà máy thật sự đã hẹp lại thì `429`/`503` sẽ nói — đó mới là tín
        # hiệu đáng tin, vì nó đến từ chính lượt gửi thật.
        if truoc_do and int(truoc_do) > 0:
            log("API shopapi: khong hoi duoc GET /v1/me cho '{0}' -> GIU trần cũ {1} "
                "(tut ve 1 la tu bop minh vi mot cu mang chap)"
                .format(loai, int(truoc_do)), "WARN")
            return int(truoc_do)
        # ⚠ ĐỪNG LÙI VỀ 1. Bản trước làm thế với lý do "đoán thấp còn hơn đứng
        # im", và cái giá của nó lộ ra trong phép đo 10 phút ngày 15/08/2026:
        # lời hỏi `/v1/me` ĐẦU TIÊN mất 121 giây rồi hỏng (hạn mức 1.000
        # request/phút đang bão hoà vì tải của chính mình). Vòng dò khởi động ở
        # 1 job, rồi AIMD +1 mỗi lô bò lên 1 → 3 → 8 → 16 → 45. Cả mẻ chạy ở
        # nhịp bò và ra 88 ảnh trong 657 giây, trong khi nhà máy nhận 61 job
        # đồng thời cùng lúc đó.
        #
        # Một cú mạng chập KHÔNG phải bằng chứng nhà máy chỉ còn một chỗ. Mở
        # vừa phải rồi để `429`/`503` nói — chúng đến từ lượt gửi THẬT nên đáng
        # tin hơn hẳn một lời hỏi trạng thái không tới nơi. Và nếu nhà máy hẹp
        # thật thì AIMD chia đôi, mất đúng một lô.
        # ⚠ TRẢ THẲNG, ĐỪNG ĐỂ RƠI XUỐNG PHÉP CHIA BÊN DƯỚI.
        #
        # `TRAN_KHOI_DONG_MU` đã là suất CỦA MỘT TIẾN TRÌNH. Để nó chảy tiếp
        # xuống nhánh tự điều tiết là chia thêm lần nữa cho số mã đang sống —
        # bắt được trong log máy khác lúc 17:01:40 ngày 15/08/2026:
        #
        #     khong hoi duoc GET /v1/me cho 'video' -> tam chay 32 job
        #     me video lo 1 -> ban them 4 job | dang bay 4/6 | tran may chu 4
        #
        # 32 ÷ 7 mã đang chạy = 4. Một mã có 76 video chờ mà mở đúng 4 chỗ, giữa
        # lúc nhà máy đang cấp 53. Nhánh "giữ trần cũ" ngay bên trên đã trả
        # thẳng (`return`) vì đúng lý do này; nhánh mù thì quên.
        try:
            _mo = max(1, min(TRAN_KHOI_DONG_MU, int(_sc.phan_luong_cua_toi())))
        except Exception:
            _mo = TRAN_KHOI_DONG_MU
        log("API shopapi: khong hoi duoc GET /v1/me cho '{0}' -> tam chay {1} job "
            "(chua tung doc duoc lan nao; lui ve 1 la tu bop minh ca me)"
            .format(loai, _mo), "WARN")
        return _mo
    if tran == 0:
        return 0

    try:
        cung = int(cung_sv) if int(cung_sv) > 0 else int(_sc.tran_cung(loai))
    except (TypeError, ValueError):
        cung = int(_sc.tran_cung(loai))
    n = min(tran, cung)

    # ═══ TRỪ PHẦN NGƯỜI KHÁC ĐANG CHẠY ═══
    #
    # Máy chủ xác nhận 15/08/2026: `concurrent_jobs.<loại>` là TỔNG số job một
    # khách được chạy song song, KHÔNG phải số chỗ còn trống. Client phải tự trừ
    # số đang chạy.
    #
    # Bỏ qua bước này thì hai máy dùng chung một khoá đều tưởng mình sở hữu trọn
    # hạn mức — `NhipSong` chỉ đếm tiến trình của MÁY MÌNH. Tổng gửi ra gấp đôi,
    # và `429` là chuyện chắc chắn: 399 cú trong 28 phút, đo 12:00–12:28.
    #
    # `running` của máy chủ đã đếm cả hai máy, nên trừ nó là hết chồng chéo mà
    # máy này không cần biết gì về máy kia.
    try:
        _khac = _sc.nguoi_khac_dang_chay(loai, dang_bay_cua_toi=dang_bay or 0,
                                         api_key=api_key, client=client)
    except Exception:
        _khac = None
    if _khac:
        n = max(1, n - int(_khac))

    if tu_dieu_tiet:
        # ═══ CHIA PHẦN THAY VÌ GÕ CỨNG ═══
        #
        # `tran` là trần của CẢ TÀI KHOẢN, còn mỗi mã chạy trong một TIẾN TRÌNH
        # RIÊNG. Bản trước chia bằng một con số gõ tay trong `settings.yaml`
        # (`max_concurrent: 40`, `shopapi_video_concurrency: 16`), và con số gõ
        # tay thì sai theo cả hai chiều cùng lúc:
        #
        #   * tám mã đang chạy  -> 8×40 = 320 trên 979 chỗ ảnh, bỏ phí 2/3;
        #   * một mã đang chạy  -> 16 trên 374 chỗ video, bỏ phí 96%.
        #
        # Đúng hai triệu chứng người dùng thấy ngày 15/08/2026.
        #
        # Giờ chia bằng SỐ TIẾN TRÌNH ĐANG SỐNG THẬT (`NhipSong` đếm theo file,
        # xem `shopapi_common`), đếm RIÊNG từng loại job — nên lúc bảy mã làm
        # ảnh và một mã làm video, mã video được trọn 374 chỗ chứ không phải
        # một phần tám.
        #
        # Rồi cắt lần nữa bằng SUẤT LUỒNG của máy này. Trần máy chủ không phải
        # trần duy nhất: thiết kế mở một luồng cho mỗi job đang bay, và Windows
        # bắt đầu từ chối quanh 700 luồng. Ngân sách luồng là của cả máy, chia
        # cho mọi tiến trình bất kể loại job — luồng là tài nguyên chung.
        try:
            ban = int(so_ban) if so_ban else int(_sc.dem_ban_dang_chay(loai))
        except (TypeError, ValueError):
            ban = 1
        # Chia THEO VIỆC khi biết mình còn bao nhiêu việc; không biết thì chia
        # đều theo đầu người như cũ. Chia đều phát chỗ cho tiến trình rỗng rồi
        # bỏ đói tiến trình đang ôm cả đống — xem `_sc.chia_theo_viec`.
        phan_may_chu = None
        if viec_con_lai:
            try:
                phan_may_chu = _sc.chia_theo_viec(loai, n, viec_con_lai)
            except Exception:
                phan_may_chu = None
        if not phan_may_chu:
            phan_may_chu = max(1, n // max(1, ban))
        try:
            phan_luong = int(_sc.phan_luong_cua_toi())
        except Exception:
            phan_luong = phan_may_chu
        n = max(1, min(phan_may_chu, phan_luong))
    elif tran_tool:
        try:
            gioi_han = int(tran_tool)
        except (TypeError, ValueError):
            gioi_han = 0
        if gioi_han > 0:
            n = min(n, gioi_han)
    return max(1, n)


def so_luong_song_song(loai, tran_tool=None, client=None, api_key=None, log=print,
                       ngu=time.sleep, cho_khi_dung=CHO_KHI_DUNG,
                       cho_toi_da=CHO_TOI_DA, dung_lai=None, tu_dieu_tiet=None):
    """Bắn được bao nhiêu job loại `loai` cùng lúc NGAY BÂY GIỜ.

    Dùng cho chỗ chỉ cần **một con số** để dựng `ThreadPoolExecutor`, không cần
    cả vòng dò nhịp.

    ⚠ `0` từ máy chủ nghĩa là **nhà máy loại đó đang dừng**, KHÔNG phải "chạy 1
    job". Chạy 1 job lúc đó là ăn `503` chắc chắn, rồi tool báo lỗi cho một việc
    hoàn toàn không có lỗi. Nên ở đây: **chờ rồi hỏi lại**, và ghi log mỗi vòng
    để người dùng nhìn màn hình biết là đang chờ chứ không phải treo.

    Chờ quá `cho_toi_da` giây thì trả `0` để nơi gọi bỏ cuộc **có kiểm soát** —
    treo vô hạn là kiểu hỏng tệ nhất vì không ai biết nó đang hỏng.

    `ngu` tách ra thành tham số để bài kiểm dựng lại một buổi nhà máy dừng trong
    vài mili-giây thay vì phải chờ thật.
    """
    da_cho = 0.0
    while True:
        if dung_lai is not None and dung_lai():
            return 0
        tran = _hoi_tran(loai, tran_tool, client=client, api_key=api_key, log=log,
                         tu_dieu_tiet=tu_dieu_tiet)
        if tran > 0:
            return tran
        if da_cho >= cho_toi_da:
            log("API shopapi: nha may {0} DUNG qua lau (da cho {1:.0f}s) -> bo qua luot nay. "
                "Viec van con nguyen trong Excel, lan chay sau nhat lai duoc."
                .format(loai, da_cho), "ERROR")
            return 0
        log("API shopapi: nha may {0} DANG DUNG (tran song song = 0) -> CHO {1:.0f}s roi hoi lai "
            "(da cho {2:.0f}/{3:.0f}s). Job gui luc nay chac chan 503 va KHONG bi tru tien."
            .format(loai, cho_khi_dung, da_cho, cho_toi_da), "WARN")
        ngu(cho_khi_dung)
        da_cho += cho_khi_dung


# ── Chạy cả mẻ ───────────────────────────────────────────────────────────────


#: Hạn mức request/phút của cả TÀI KHOẢN (hợp đồng API). Chừa lại một phần cho
#: `/v1/me` và các lời hỏi khác, nên ngân sách gửi để dưới mức trần.
NGAN_SACH_REQ_PHUT = 850

#: Giá mỗi job, theo ĐƯỜNG CHỜ đang dùng. ĐO THẬT 15/08/2026, 40 ảnh mỗi lượt,
#: đếm cả `stream_request` (bộ đếm cũ bỏ sót nó nên báo 2,0 thay vì 3,0):
#:
#:   gộp lời hỏi : 1,27  (1 POST + một lời hỏi CHUNG chia cho cả trăm job)
#:   SSE         : 3,00  (1 POST + 1 SSE + 1 GET kết quả)
#:   hỏi từng job: 5,70  (1 POST + N lần GET, N tăng theo độ dài job)
#:
#: ⚠ PHẢI ĐI THEO ĐƯỜNG ĐANG DÙNG. Ghim cứng 3,0 trong khi chạy đường 1,27 là
#: tự bóp nhịp gửi xuống còn 42% — thùng token tưởng mỗi ảnh đắt gấp 2,4 lần
#: thực tế. Ngân sách request là trần THẬT của cả dây chuyền
#: (thông lượng = ngân sách ÷ giá mỗi ảnh), nên sai ở đây là mất thẳng sản lượng.
GIA_MOI_JOB = {"gop": 1.3, "sse": 3.0, "hoi": 5.7}


def req_moi_job():
    """Giá mỗi job của đường chờ đang bật."""
    try:
        if _sc.dung_thu_hoach_chung():
            return GIA_MOI_JOB["gop"]
        if _sc.dung_sse():
            return GIA_MOI_JOB["sse"]
    except Exception:
        pass
    return GIA_MOI_JOB["hoi"]


#: Giữ tên cũ cho chỗ nào còn đọc thẳng — nhưng ĐỪNG dùng nó để tính nhịp gửi.
REQ_MOI_JOB = GIA_MOI_JOB["sse"]

#: Được gửi bao nhiêu job liền một mạch trước khi phải rót đều.
#:
#: 979 là con số đã gây thảm hoạ; 5 (một giây rót) thì mẻ nhỏ nào cũng bị nhỏ
#: giọt vô cớ. 64 cho mọi mẻ cỡ thường đi thẳng trong một lượt, mà vẫn giữ cú
#: bùng đầu tiên trong tầm hạn mức.
TRAN_BUNG = 64


class ThungGui:
    """Ghìm TỐC ĐỘ GỬI job, tách hẳn khỏi SỐ JOB SONG SONG.

    ═══ HAI THỨ KHÁC NHAU, VÀ TÔI ĐÃ NHẦM ═══

    Trần máy chủ (`limit`) nói **bao nhiêu job được CHẠY cùng lúc**. Hạn mức
    `requests_per_minute` nói **bao nhiêu lời gọi được GỬI mỗi phút**. Mở đủ
    979 chỗ chạy không có nghĩa là được phép tạo 979 job trong một nhịp thở.

    Đo 15/08/2026, sau khi sửa cho vòng dò khởi động đúng ở 979: mẻ bắn một
    loạt và ăn **2.651 lần `429`**, 71 job chết vì quá hạn, và ra **0 ảnh**.
    Trần song song thì đúng, nhưng cách tiêu nó thì sai — cả ngân sách
    request của một phút bị đốt trong vài giây đầu, rồi mọi thứ kể cả
    `GET /v1/me` đều bị chặn ở cửa.

    Thùng token này rót đều: `ngân_sách_phút ÷ số_request_mỗi_job ÷ số_tiến
    _trình_đang_sống` job mỗi giây. Nó KHÔNG thay `nhip`/`cong` — chúng chặn
    *tổng số đang bay*, còn cái này chặn *nhịp rót vào*. Cần cả hai: một mẻ có
    thể vừa đúng trần song song vừa gửi quá nhanh, và ngược lại.
    """

    def __init__(self, so_ban=None, ngan_sach=None, dong_ho=time.monotonic):
        self._dong_ho = dong_ho
        self._lan_cuoi = dong_ho()
        # ⚠ ĐẦY SẴN, KHÔNG RỖNG. Thùng rỗng nghĩa là lô ĐẦU TIÊN của mọi mẻ chỉ
        # được một job — đúng cái nhịp bò mà cả đợt sửa này đi chữa.
        self._token = float(TRAN_BUNG)
        self._so_ban = so_ban
        self._ngan_sach = float(ngan_sach if ngan_sach is not None
                                else _so_moi_truong("SHOPAPI_NGAN_SACH_REQ", NGAN_SACH_REQ_PHUT))
        #: Trần trên của phép bò lên — không bò quá mức khởi điểm.
        self._tran_ngan_sach = self._ngan_sach

    def toc_do(self):
        """Bao nhiêu job được gửi mỗi giây, phần của TIẾN TRÌNH NÀY."""
        ban = self._so_ban
        if ban is None:
            try:
                ban = int(_sc.dem_ban_dang_chay(""))
            except Exception:
                ban = 1
        return max(0.2, self._ngan_sach / 60.0 / req_moi_job() / max(1, int(ban)))

    def xin(self, n):
        """Xin gửi `n` job. Trả số ĐƯỢC PHÉP gửi ngay bây giờ (có thể là 0)."""
        gio = self._dong_ho()
        self._token += (gio - self._lan_cuoi) * self.toc_do()
        self._lan_cuoi = gio
        # ⚠ TRẦN THÙNG = MỨC BÙNG CHO PHÉP. Thảm hoạ 15/08/2026 là bùng 979
        # job trong một nhịp thở (2.651 lần `429`, 0 ảnh ra). Nhưng chặn xuống
        # "một giây rót" thì mẻ nhỏ nào cũng phải nhỏ giọt vô cớ — một mẻ 30
        # scene lẽ ra đi trong một lượt lại kéo thành bảy giây.
        #
        # `TRAN_BUNG` là điểm giữa: đủ rộng để mọi mẻ cỡ thường đi thẳng, đủ hẹp
        # để không đốt sạch ngân sách một phút. Quá mức đó thì rót đều theo
        # `toc_do()`.
        self._token = min(self._token, max(float(TRAN_BUNG), self.toc_do()))
        cho = int(min(int(n), int(self._token)))
        if cho > 0:
            self._token -= cho
        return cho

    def cho_bao_lau(self):
        """Chưa đủ một token thì phải nghỉ bao lâu nữa."""
        thieu = max(0.0, 1.0 - self._token)
        return thieu / self.toc_do() if thieu > 0 else 0.0

    # ── Tự dò ngân sách, đừng tin con số tôi gõ ─────────────────────────────
    #
    # `NGAN_SACH_REQ_PHUT = 850` là suy ra từ hạn mức 1.000 ghi trong hợp đồng,
    # và nó SAI. Đo 15/08/2026 với đúng ngân sách đó: vẫn 464 lần `429` trong
    # 10 phút và **0 ảnh ra**. Hạn mức thật thấp hơn con số công bố, hoặc được
    # tính trên cửa sổ khác, hoặc còn tính cả thứ tôi không nhìn thấy.
    #
    # Không cần biết đáp án. Chỉ cần cư xử như TCP: đụng tường thì lùi một nửa,
    # đi êm thì bò lên. Vòng dò tự tìm ra con số đúng, và nó còn đi theo được
    # cả khi máy chủ đổi hạn mức mà không báo ai.

    #: Không bao giờ hạ ngân sách dưới mức này (req/phút) — hạ nữa là đứng im.
    SAN_NGAN_SACH = 30.0
    #: ...và cũng không hạ quá ngần này lần so với mức khởi điểm. Chia đôi mãi
    #: thì rơi xuống vùng mỗi token chờ vài giây, tức là tool đứng hình vì một
    #: chuỗi `429` mà đáng lẽ chỉ cần lùi vài bậc. Quá 32 lần thì vấn đề không
    #: còn là nhịp gửi nữa.
    HA_TOI_DA = 32.0
    #: Mỗi lô đi êm thì nới ngân sách thêm ngần này.
    NOI = 1.04

    def bi_chan(self):
        """Ăn `429` -> CHIA ĐÔI ngân sách gửi. Trả mức mới (req/phút)."""
        san = max(self.SAN_NGAN_SACH, self._tran_ngan_sach / self.HA_TOI_DA)
        self._ngan_sach = max(san, self._ngan_sach * 0.5)
        self._token = 0.0          # xả sạch token: đừng bắn tiếp ngay sau cú phanh
        return self._ngan_sach

    def tron_tru(self):
        """Một lô đi êm -> bò lên. Tăng nhân, chậm hơn hẳn lúc lùi."""
        self._ngan_sach = min(self._tran_ngan_sach, self._ngan_sach * self.NOI)


def _so_moi_truong(ten, mac_dinh):
    try:
        return float(os.environ.get(ten) or mac_dinh)
    except (TypeError, ValueError):
        return float(mac_dinh)


#: Bao lâu KHÔNG ăn nghẽn thì coi là nhà máy đã rảnh trở lại (giây).
YEN_LANG_GIAY = 90.0

#: Hồi lại tới mức nào so với trần máy chủ đang cấp. Nửa trần là mức TCP
#: kinh điển: đủ mạnh để lấy lại thông lượng, đủ khiêm tốn để nếu nhà máy vẫn
#: chật thì chỉ mất một cú chia đôi nữa.
TI_LE_HOI = 0.5


def can_hoi_nhip(nhip, tran, lan_nghen_cuoi, bay_gio):
    """Có nên NHẢY nhịp về gần trần thay vì bò +1 không?

    ═══ VÌ SAO AIMD HỎNG Ở ĐÂY, DÙ NÓ ĐÚNG VỚI TCP ═══

    Luật tăng của `NhipDo` là **+1 mỗi lô mượt**, một "lô" = `ceil(nhịp)` job
    liên tiếp không nghẽn. Đúng y TCP. Nhưng TCP có một thứ ta không có: gói tin
    về trong mili-giây. Ở đây một "gói" là **một video 70 giây**.

    Hệ quả đo được trong log 00:00–01:03 ngày 15/08/2026: nhà máy video chập
    chờn, ăn 111 lần `429` và 98 lần `503`. Mỗi cú chia đôi nhịp — đúng. Nhưng
    khi nhịp đã tụt về 1 thì mỗi lúc chỉ còn MỘT job đang bay, nên tốc độ hồi là
    +1 mỗi 70 giây. Leo từ 1 về 34 cần 1+2+…+34 ≈ 595 job xong, tức hàng giờ.

    Nhìn thẳng trong log:

        00:02  nhip=15.5      00:13  nhip=1.0      00:14  cho_phep=0
        00:36–00:48: MỌI lô đều "ban them 1 job"
        san luong: 00:00-00:19 ~15-35/phut  ->  00:20-00:49 ~4/phut

    Ba mươi phút chạy ở 4 video/phút trong khi máy chủ vẫn rao trần 12–17. Mất
    khoảng 330 video chỉ trong một giờ đó.

    Cái bẫy tự nuôi nó: nhịp thấp → ít job bay → ít job xong → tăng chậm → nhịp
    vẫn thấp. Càng nghẽn càng lâu hồi.

    Nên: giữ CHIA ĐÔI khi bị chặn (đó là phần đúng của AIMD), nhưng bỏ bò-lên
    khi hồi. Yên lặng đủ lâu mà nhịp vẫn thấp hơn hẳn trần thì NHẢY về nửa trần.
    """
    if not tran or tran <= 0:
        return False
    if (bay_gio - lan_nghen_cuoi) < YEN_LANG_GIAY:
        return False
    try:
        dang = int(nhip.cho_phep())
    except Exception:
        return False
    return dang < int(tran * TI_LE_HOI)


class DoHieuQua:
    """Trần theo SẢN LƯỢNG ĐO ĐƯỢC, không theo con số máy chủ rao.

    ═══ VÌ SAO TRẦN CỦA MÁY CHỦ CHƯA ĐỦ ĐỂ TIN ═══

    Đo 15/08/2026 lúc 03:2x, `GET /v1/me` nói nhà máy ảnh có `capacity 1088` và
    mời `limit 979` — nhưng cùng lúc đó `workers_online = 1`. Một tiến trình thợ
    khai 1.088 chỗ. Bắn 60 ảnh vào đấy: đỉnh 63 job đồng thời, mà **56 ảnh mất
    393 giây — 8,5 ảnh/phút**, và 2 job chết vì quá hạn 300 giây. Một ảnh chạy
    một mình hôm đó mất 30 giây.

    Nghĩa là quá một mức nào đó, gửi thêm KHÔNG ra thêm hàng: nó chỉ chia cùng
    một năng lực thật cho nhiều job hơn, mỗi job lâu hơn, và job ở đuôi chết vì
    hết hạn. Tệ hơn cả phí — nó làm HỎNG việc đã trả tiền.

    Và không có tín hiệu nào báo: `queued = 0`, không `429`, không `503`. Máy
    chủ nhận hết rồi chạy chậm. `NhipDo` chờ `429` để lùi, `CongHangCho` chờ
    hàng dài để lùi — cả hai đều mù trước kiểu nghẽn này.

    Thứ duy nhất nhìn thấy nó là **số job xong mỗi phút**. Lớp này đo đúng cái
    đó rồi leo đồi: còn tăng số job cùng lúc mà sản lượng còn lên thì cứ tăng;
    tăng mà sản lượng không lên nữa thì lùi về mức tốt nhất từng đo được.

    Đây chính là "server mà có thể khai thác thì cứ khai thác tối đa" — nhưng đo
    bằng hàng ra, chứ không bằng lời máy chủ tự khai.
    """

    #: Chốt sổ mỗi ngần này giây.
    #:
    #: ⚠ PHẢI DÀI HƠN TUỔI THỌ MỘT JOB, nếu không là đo nhiễu khởi động. Bản đầu
    #: để 45 giây trong khi một job ảnh mất 30–300 giây, và nó hỏng đúng như vậy
    #: trong phép đo 10 phút ngày 15/08/2026: vừa nâng lên 45 job cùng lúc,
    #: chúng còn đang bay chứ chưa cái nào xong, cửa sổ đọc ra "6 job/phút" rồi
    #: kết luận đã quá đỉnh và KHOÁ TRẦN XUỐNG 8. Tự bóp mình bằng chính cái
    #: đáng lẽ để nới ra.
    CUA_SO = 150.0
    #: Sản lượng phải kém hơn mức tốt nhất ngần này mới coi là "đã quá đỉnh".
    NGUONG_KEM = 0.75
    #: Phải thấy TỆ ngần này lượt LIÊN TIẾP mới được hạ. Một lượt là nhiễu.
    XAU_LIEN_TIEP = 2
    #: Mỗi lượt chốt, kỷ lục cũ phai đi ngần này — nhà máy đổi thì mốc cũ phải
    #: chịu buông, nếu không một phút vàng hồi nào đó khoá trần mãi mãi.
    PHAI = 0.9
    #: Không bao giờ hạ xuống dưới mức này — hạ nữa là đứng im.
    SAN = 4

    def __init__(self, cua_so=None, dong_ho=time.monotonic):
        self.cua_so = float(cua_so if cua_so is not None else self.CUA_SO)
        self._dong_ho = dong_ho
        self._moc = dong_ho()
        self._xong = 0
        #: Tổng số job-giây đã bay trong cửa sổ, để ra số job cùng lúc BÌNH QUÂN
        #: — dùng con số tức thời thì một lần nhặt hàng loạt làm lệch hẳn.
        self._tich = 0.0
        self._lan_do = dong_ho()
        #: `(sản lượng tốt nhất, số job cùng lúc lúc đó)`.
        self._tot_nhat = (0.0, 0)
        #: Mấy lượt liên tiếp thấy "đông hơn mà ra ít hơn".
        self._xau = 0
        #: Trần đang áp. `0` = chưa chặn gì.
        self.tran = 0

    def ghi_xong(self, so=1):
        self._xong += int(so)

    def nhip_tich(self, dang_bay):
        """Gọi mỗi vòng lặp: cộng dồn job-giây để tính bình quân."""
        gio = self._dong_ho()
        self._tich += max(0, int(dang_bay)) * (gio - self._lan_do)
        self._lan_do = gio

    def chot_so(self):
        """Hết cửa sổ thì chốt. Trả `(sản lượng/phút, số cùng lúc)` hoặc `None`."""
        gio = self._dong_ho()
        troi = gio - self._moc
        if troi < self.cua_so:
            return None
        san_luong = 60.0 * self._xong / troi
        cung_luc = int(round(self._tich / troi)) if troi > 0 else 0
        self._moc, self._xong, self._tich, self._lan_do = gio, 0, 0.0, gio

        tot, tai_muc = self._tot_nhat
        if san_luong > tot:
            # Còn leo được -> ghi mốc mới và MỞ trần ra (đừng giữ trần cũ).
            self._tot_nhat = (san_luong, max(cung_luc, self.SAN))
            self.tran = 0
            self._xau = 0
        elif (tai_muc and cung_luc > tai_muc * 1.15
              and san_luong < tot * self.NGUONG_KEM):
            # Đông hơn mà ra ít hơn. MỘT lượt như thế chưa nói lên gì: hàng vừa
            # nâng lên còn đang bay, chưa cái nào kịp xong. Chỉ hạ khi thấy lặp
            # lại — lúc đó mới là nhà máy nghẽn thật chứ không phải đang đầy ống.
            self._xau += 1
            if self._xau >= self.XAU_LIEN_TIEP:
                self.tran = max(self.SAN, tai_muc)
                self._xau = 0
        else:
            self._xau = 0
        # Kỷ lục phai dần: nhà máy hôm nay khác hôm qua, và một cửa sổ may mắn
        # không được phép làm mốc so sánh vĩnh viễn.
        self._tot_nhat = (self._tot_nhat[0] * self.PHAI, self._tot_nhat[1])
        return san_luong, cung_luc

    def cho_phep(self):
        """Trần theo sản lượng, hoặc `0` khi chưa có căn cứ để chặn."""
        return int(self.tran)


def _boc(chay_mot, viec, gia_tri_khi_hong, log, cong=None):
    """Chạy MỘT việc sao cho **không có gì lọt ra ngoài** làm chết cả mẻ.

    Trả `("nghen", BiNghen)`, `("xong", giá_trị)`, hoặc `("hong", giá_trị)`.
    Không bao giờ ném.

    ⚠ "XONG" VÀ "HỎNG" PHẢI TÁCH RA. Bản trước gộp cả hai thành `"xong"`, và
    `DoHieuQua` đếm luôn job hỏng là sản lượng. Phép đo 10 phút ngày 15/08/2026
    báo "262 job/phút" trong khi số ảnh thật ra được là **0** — 2.651 job dính
    `429`. Bộ leo đồi tưởng đang ở đỉnh phong độ và cứ thế nhồi thêm.

    Đây là chỗ phân biệt hai loại thất bại hoàn toàn khác nhau:

    * :class:`shopapi_common.BiNghen` — việc CHƯA chạy, chưa mất tiền → trả về
      hàng chờ, hạ nhịp, chạy lại ở lô sau.
    * ngoại lệ khác — việc này hỏng thật → ghi lỗi cho MÌNH NÓ rồi đi tiếp. 199
      scene còn lại không có tội gì mà phải chết theo.
    """
    _cuc_bo.trong_me = True
    # Cắm cổng của mẻ vào luồng này để `bao_hang_cho` / `bao_nghen` tìm thấy nó.
    _cuc_bo.cong = cong
    try:
        return "xong", chay_mot(viec)
    except _sc.BiNghen as nghen:
        return "nghen", nghen
    except BaseException as exc:   # noqa: BLE001 - co y bat het, xem docstring
        nghen = _sc.phan_loai_nghen(exc)
        if nghen is not None:
            # Nhánh gửi quên bật `nem_khi_nghen` thì vẫn nhận ra được ở đây.
            return "nghen", nghen
        try:
            log("    [shopapi-me] mot viec hong: {0} -> BO RIENG viec nay, me van chay tiep"
                .format(_sc.mo_ta_loi(exc)), "WARN")
        except Exception:
            pass
        return "hong", gia_tri_khi_hong
    finally:
        _cuc_bo.trong_me = False
        _cuc_bo.cong = None


def chay_ca_me(viec, chay_mot, loai, tran_tool=None, client=None, api_key=None,
               log=print, ngu=time.sleep, dung_lai=None,
               cho_khi_dung=CHO_KHI_DUNG, cho_toi_da=CHO_TOI_DA,
               gia_tri_khi_hong=False, nhip=None, cong=None, han_giay=0.0,
               tu_dieu_tiet=None):
    """Chạy cả mẻ `viec` bằng `chay_mot`, tự dò nhịp. Trả kết quả **ĐÚNG THỨ TỰ ĐƯA VÀO**.

    `chay_mot(mot_viec)` làm trọn một việc (gửi job, chờ, tải file, ghi Excel,
    đẩy tiến độ) và trả về giá trị tuỳ ý — thường là `True`/`False`/`None` cho
    khớp cách các pha trong `ve3_worker.py` đang đếm.

    Việc chưa chạy được (bị bỏ vì `dung_lai`, hoặc hết ngân sách chờ) có chỗ
    tương ứng trong danh sách trả về là `None`.

    VÌ SAO TRẢ ĐÚNG THỨ TỰ ĐƯA VÀO, dù xong lộn xộn: nơi gọi ghép kết quả với
    `scene_id` bằng **vị trí**. Trả theo thứ tự chạy xong là ghi nhầm kết quả
    scene này sang dòng scene khác — hỏng lặng lẽ, và chỉ lộ ra khi khách xem
    video thấy sai cảnh.

    NHỊP ĐI THEO LÔ, KHÔNG THEO TỪNG JOB: mỗi vòng lặp hỏi lại trần, lấy `n` việc
    đầu hàng chờ, **gửi hết `n` cái rồi mới chờ** — đó chính là chỗ tách "gửi"
    khỏi "chờ". Bản cũ gửi-rồi-chờ-xong-mới-gửi-cái-kế, nên `n` luôn bằng 1.

    `nhip` để tiêm vòng dò riêng (bài kiểm soi nhịp, hoặc dùng chung một vòng dò
    cho nhiều mẻ nối tiếp — vòng dò càng sống lâu càng bám sát nhà máy).

    ⚠ `cong` (:class:`CongHangCho`) LÀ LỚP CHẶN THỨ HAI, đừng gỡ vì thấy `nhip`
    đã có rồi. `nhip` chỉ biết có nghẽn **sau khi** một job xong êm (đo độ trễ
    hàng chờ) hoặc **sau khi** ăn `429`. Ngày 07/08/2026 cả hai tín hiệu đó đều
    không bao giờ tới: 33 job nằm xếp hàng cho tới lúc hết hạn nên chẳng job nào
    "xong êm" để báo, và máy chủ cũng không trả `429` — nó nhận job rồi để chúng
    chết già trong hàng. `cong` đọc `queue_position` / `estimated_seconds` ngay
    lúc máy chủ NHẬN job, nên nó thấy hàng dài **trước khi** có job nào chết.

    `han_giay` là hạn chờ của MỘT job (thường chính là `timeout` mà nơi gọi
    truyền cho `generate`). Có nó thì cổng chặn được cả trường hợp máy chủ nói
    thẳng "còn 1.500 giây nữa mới tới lượt" trong khi ta chỉ chờ được 1.200.
    """
    tong = len(viec)
    if tong == 0:
        return []

    ket_qua = {}
    con_lai = list(range(tong))
    #: Chỗ ngồi của tiến trình này trong bảng chia trần. Hỏng thì thôi, chạy
    #: tiếp — thiếu điểm danh cùng lắm là chia bảo thủ hơn.
    try:
        _nhip_song = _sc.NhipSong(loai)
    except Exception:
        _nhip_song = None
    #: Trần theo SẢN LƯỢNG ĐO ĐƯỢC — lớp chặn duy nhất nhìn thấy kiểu nghẽn
    #: "máy chủ nhận hết rồi chạy chậm" (không 429, không 503, `queued = 0`).
    hieu_qua = DoHieuQua()
    #: Lần cuối ăn `429`/`503`. Yên lặng đủ lâu thì nhịp được NHẢY về gần trần
    #: thay vì bò +1 — xem `can_hoi_nhip`.
    lan_nghen = [time.monotonic()]
    #: Ghìm NHỊP RÓT VÀO. `nhip`/`cong` chặn tổng số đang bay; cái này chặn tốc
    #: độ gửi, và thiếu nó thì một mẻ đúng trần song song vẫn đốt sạch hạn mức
    #: request của cả phút trong vài giây đầu.
    thung = ThungGui()
    #: Trần đọc gần nhất + lúc đọc. Vòng lặp hỏi qua `_doc_tran`, không hỏi thẳng.
    _tran_moi_nhat = [0, 0.0]
    #: Mép THẬT vừa đo được: số job đang bay lúc máy chủ nói `429`. `[mức, lúc]`.
    _mep_that = [0, 0.0]

    def _sat_tran():
        """Đang đứng sát trần chưa? Dùng để đọc đúng nghĩa một cú `429`."""
        tr = _tran_moi_nhat[0]
        if tr <= 0:
            return False
        return len(dang_bay) >= tr * SAT_TRAN

    def _ghi_mep_that(bay):
        """Nhớ mức vừa chạm `429`. Lấy mức THẤP NHẤT trong quãng còn hiệu lực."""
        muc = max(1, int(bay) - 1)
        gio = time.monotonic()
        if _mep_that[0] <= 0 or (gio - _mep_that[1]) > MEP_THAT_TTL:
            _mep_that[0] = muc
        else:
            _mep_that[0] = min(_mep_that[0], muc)
        _mep_that[1] = gio

    def _tran_mep_that():
        """Mép đã đo, hoặc `0` khi chưa đo / đã cũ.

        ⚠ PHẢI CÓ HẠN. Một con số chỉ đi xuống là cái bẫy đã sập một lần ở lớp
        này (ngân sách luồng bị ghim ở sàn 24 và không bao giờ tự gỡ). Nhà máy
        rộng ra, hay khách khác nghỉ, thì mép cũ thành xiềng.
        """
        if _mep_that[0] <= 0:
            return 0
        if (time.monotonic() - _mep_that[1]) > MEP_THAT_TTL:
            _mep_that[0] = 0
            return 0
        return _mep_that[0]

    def _doc_tran():
        """Trần hiện tại, hỏi lại `/v1/me` nhiều nhất mỗi `TRAN_TTL` giây.

        Xem khối cảnh báo ở `TRAN_TTL`: bỏ hàng rào làm vòng lặp quay gần một
        lần mỗi job xong, nên hỏi thẳng ở đây là hỏi ~88 lần một mẻ thay vì 13.
        """
        gio = time.monotonic()
        # Điểm danh mỗi lần ghé qua đây: `NhipSong` tự thưa ra 20 giây một lần,
        # nên gọi thoải mái. Tiến trình chết đột ngột thì file nguội đi và tự
        # hết tính sau 90 giây — không cần ai dọn.
        #
        # Khai luôn CÒN BAO NHIÊU VIỆC. Anh em đọc con số này để chia trần theo
        # việc chứ không chia đều đầu người — mã còn 1 video không giữ chỗ của
        # mã còn 52. Xem `_sc.chia_theo_viec`.
        _con = len(con_lai) + len(dang_bay)
        if _nhip_song is not None:
            _nhip_song.diem_danh(con_viec=_con)
        if _tran_moi_nhat[0] > 0 and (gio - _tran_moi_nhat[1]) < TRAN_TTL:
            return _tran_moi_nhat[0]
        t = _hoi_tran(loai, tran_tool, client=client, api_key=api_key, log=log,
                      truoc_do=_tran_moi_nhat[0], tu_dieu_tiet=tu_dieu_tiet,
                      viec_con_lai=_con, dang_bay=len(dang_bay))
        # `0` = nhà máy ĐANG DỪNG. KHÔNG nhớ nó: nhớ số 0 thì lượt sau đọc phải
        # số 0 đã cũ và tưởng nhà máy vẫn chết, trong khi nó có thể vừa sống lại.
        if t > 0:
            _tran_moi_nhat[0], _tran_moi_nhat[1] = t, gio
        return t

    #: Job đã gửi, chưa biết kết quả. Đây là thứ thay cho hàng rào cũ.
    #
    # ⚠ PHẢI KHAI TRƯỚC LỜI GỌI `_doc_tran()` NGAY DƯỚI. `_tran_hien_tai` đọc
    # `len(con_lai) + len(dang_bay)` để khai số việc còn lại; khai sau là
    # `NameError` ngay ở lô đầu của mọi mẻ.
    dang_bay = {}

    if nhip is None:
        # HỎI TRẦN TRƯỚC KHI DỰNG VÒNG DÒ. Máy chủ đã tính sẵn phần của ta (sức
        # chứa trừ dự phòng, chia cho số khách đang chờ), nên đó là điểm xuất
        # phát đúng — xem khối `why` dài trong `_tao_nhip`. Bắt đầu ở 1 rồi bò
        # lên là bỏ phí 99% chỗ được cấp, và mẻ nào cũng bò lại từ đầu.
        _tran_dau = _doc_tran()
        nhip = _tao_nhip(bat_dau=_tran_dau)
        if _tran_dau > 0:
            log("API shopapi: me {0} bat dau o {1} job cung luc (dung bang tran may chu "
                "dang cap; AIMD chi con viec HA XUONG khi thuc te phan doi)"
                .format(loai, _tran_dau))
    cong = cong if cong is not None else CongHangCho(han_giay=han_giay)
    da_cho = 0.0
    lo_thu = 0
    #: Một pool sống suốt cả mẻ. `ThreadPoolExecutor` dựng luồng LƯỜI nên đặt
    #: rộng bằng trần cứng không tốn gì: số luồng thật sự mở ra luôn bằng số job
    #: đang bay, mà số đó đã bị `nhip` + `cong` chặn ở trên rồi.
    # ⚠ ĐẶT BẰNG SUẤT LUỒNG, KHÔNG BẰNG TRẦN CỨNG. Bản trước lấy `tran_cung`
    # (nay đọc từ máy chủ là 1.536 cho ảnh) — dựng pool rộng đến thế là mời
    # `RuntimeError: can't start new thread`, vì luồng là tài nguyên CỦA CẢ MÁY
    # mà tám tiến trình mã cùng ăn. Suất luồng đã chia sẵn phần cho mỗi tiến
    # trình; `nhip` + `cong` còn cắt thêm ở trên.
    try:
        _tran_pool = max(1, int(_sc.phan_luong_cua_toi()))
    except Exception:
        _tran_pool = 64
    pool = ThreadPoolExecutor(max_workers=_tran_pool,
                              thread_name_prefix="shopapi-{0}".format(loai))

    def _thu_hoach(cho_it_nhat_mot):
        """Nhặt job đã xong, trả chỗ, báo nhịp. Trả danh sách việc cần gửi lại.

        ═══ VÌ SAO KHÔNG CÒN CHỜ HẾT CẢ LÔ ═══

        Bản trước dựng một `ThreadPoolExecutor` MỚI cho mỗi lô rồi `as_completed`
        cho tới hết — tức là **hàng rào**: job xong sớm nằm không đợi job chậm
        nhất, và cả lô chỉ nhanh bằng cái chậm nhất của nó. Với video p50 59
        giây mà đuôi tới 226 giây, mỗi lô mất gần bốn lần thời gian đáng phải mất.

        Tệ hơn: nhịp chỉ tăng SAU khi trọn một lô xong, nên hàng rào còn làm
        chậm cả tốc độ dò lên. Hai cái cộng lại là lý do một mẻ 87 scene chỉ đạt
        6,7 job cùng lúc trong khi máy chủ đang mời 691.

        Giờ: chờ job ĐẦU TIÊN xong là lấp chỗ ngay.
        """
        if not dang_bay:
            return []
        if cho_it_nhat_mot:
            # ⚠ CÓ HẠN CHỜ, không chờ trắng. `futures_wait` không hạn thì bấm
            # Dừng xong tool vẫn đứng đó tới lúc job chậm nhất xong — với video
            # đuôi 226 giây thì đó là gần bốn phút không phản hồi. Hết hạn mà
            # chưa có cái nào xong cũng không sao: vòng ngoài kiểm `dung_lai`
            # rồi quay lại đây.
            xong_roi, _ = futures_wait(list(dang_bay), timeout=NHIP_KIEM_DUNG,
                                       return_when=FIRST_COMPLETED)
        else:
            xong_roi = [f for f in list(dang_bay) if f.done()]
        lai = []
        for f in xong_roi:
            i = dang_bay.pop(f, None)
            if i is None:
                continue
            cong.tra_cho(1)   # trả ĐÚNG một chỗ, ngay khi job đó rời hàng
            try:
                trang_thai, gia_tri = f.result()
            except Exception as e:  # `_boc` đã nuốt hết, đây chỉ là lưới cuối
                log("API shopapi: job {0} nem ngoai le lot luoi ({1}: {2})"
                    .format(i, type(e).__name__, e), "ERROR")
                ket_qua[i] = gia_tri_khi_hong
                continue
            if trang_thai == "nghen":
                # Bị từ chối NGAY Ở CỬA: chưa tốn tiền, việc chưa mất.
                lai.append(i)
                lan_nghen[0] = time.monotonic()
                if gia_tri.ma == 429 and _sat_tran():
                    # ═══ `429` NGAY TẠI TRẦN LÀ TRẦN SAI, KHÔNG PHẢI NHỊP SAI ═══
                    #
                    # `nhip` là MỤC TIÊU SỐ JOB CÙNG LÚC (`n = nhip.cho_phep() -
                    # len(dang_bay)`). Chia đôi nó khi ta đang đứng đúng ở trần
                    # là trừng phạt nhầm thứ: số job đang bay đã bị trần chặn
                    # rồi, cú `429` chỉ nói trần đó cao hơn sự thật.
                    #
                    # Đo thật 12:16–12:17 ngày 15/08/2026, TH1-0328 làm ảnh:
                    #
                    #     12:16:29  lo 30 -> 57 dang bay / tran 58   (đang lấp đầy)
                    #     12:16:50  429 | nhip 29.0 cho phep 29
                    #     12:17:22  429 | nhip 1.0  cho phep 1
                    #
                    # Nửa phút, nhịp 29 -> 1. Mà đường về thì phải im tiếng 90
                    # giây liền (`can_hoi_nhip`) — điều gần như không xảy ra khi
                    # ta đang ngồi ngay mép trần. Cả mẻ 399 cú `429` trong 28
                    # phút, sản lượng còn 4 ảnh/phút.
                    #
                    # Việc đúng phải làm: GHI NHỚ mép thật vừa chạm, rồi chờ một
                    # chỗ trống. Nhịp giữ nguyên để lúc có chỗ là lấp được ngay.
                    _ghi_mep_that(len(dang_bay))
                    cong.bi_nghen(gia_tri.cho)
                    log("API shopapi: me {0} - 429 NGAY TAI TRAN ({1} dang bay, tran rao {2})"
                        " -> ghi mep that {3}, GIU nhip {4:.0f} de lap lai ngay khi co cho"
                        .format(loai, len(dang_bay), _tran_moi_nhat[0],
                                _tran_mep_that(), float(nhip.cho_phep())), "WARN")
                elif gia_tri.ma == 429:
                    nhip.bi_chan(gia_tri.cho)
                    cong.bi_nghen(gia_tri.cho)
                    # ⚠ CHỈ HẠ NHỊP RÓT KHI ĐÚNG LÀ RÓT QUÁ NHANH.
                    #
                    # Ba cửa cùng cho ra `429` và cách chữa ngược nhau (máy chủ
                    # xác nhận 15/08/2026):
                    #
                    #   rate limit          vượt `requests_per_minute` — đúng
                    #                       là rót nhanh quá, ghìm lại.
                    #   queue_full          hàng chờ RIÊNG của khách đầy — rót
                    #                       chậm lại không làm hàng vơi đi.
                    #   resource_exhausted  mã CỦA WORKER: job đã được nhận rồi
                    #                       nhà máy mới hết chỗ giữa chừng. Hệ
                    #                       thống phía sau quá tải, không phải
                    #                       lỗi nhịp rót.
                    #
                    # Hai cái sau mà đi ghìm nhịp rót là kéo nhầm cần, và giá
                    # phải trả rất thật: nhịp rót chỉ bò lại lên bằng những lô
                    # trơn tru, mà lô video thì 60–90 giây một cái.
                    if str(getattr(gia_tri, "ly_do", "")) not in (
                            "queue_full", "resource_exhausted"):
                        thung.bi_chan()
                elif _sc.con_tho_khong(loai, client=client) is True:
                    # `503` mà nhà máy VẪN CÒN THỢ = chen chúc nhất thời, không
                    # phải chết. Đối xử như `429`: chia đôi rồi bò lên lại.
                    #
                    # `nha_may_dung` kéo nhịp về SÀN (1) + đóng băng 30 giây +
                    # thăm dò bằng 1 job, và luật leo là +1 mỗi lô mượt. Với
                    # video ~500 giây một lô thì bò từ 1 về 40 mất hàng giờ.
                    #
                    # Đo 11:03–11:05 ngày 15/08/2026: chín tiến trình video, mỗi
                    # cái ăn một `503` rồi tụt từ `tran may chu 124` xuống
                    # `nhip 1.0 cho phep 0`, trong khi CÙNG LÚC hai mã khác vẫn
                    # được nhận và xếp hàng thứ 27. Cả chín đóng băng vì một cú
                    # nghẹt mà nhà máy chưa hề ngừng chạy.
                    nhip.bi_chan(gia_tri.cho)
                    cong.bi_nghen(gia_tri.cho)
                    thung.bi_chan()
                else:
                    # Hết thợ thật, hoặc hỏi không được -> giữ nguyên cách cũ:
                    # dừng hẳn. Hỏi không được thì chọn phía AN TOÀN, vì gửi vào
                    # một nhà máy đã chết là đốt lượt thử chứ không ra hàng.
                    nhip.nha_may_dung(gia_tri.cho)
                    cong.nha_may_dung(gia_tri.cho)
                continue
            ket_qua[i] = gia_tri
            nhip.xong()
            if trang_thai == "xong":
                hieu_qua.ghi_xong()   # CHỈ đếm hàng ra thật, không đếm job hỏng
                thung.tron_tru()      # đi êm thì bò lại lên
        return lai

    try:
      while con_lai or dang_bay:
        if dung_lai is not None and dung_lai():
            log("API shopapi: nhan lenh DUNG -> bo {0} viec con lai cua me {1}"
                .format(len(con_lai), loai), "WARN")
            break

        # ⚠ NHẶT KẾT QUẢ TRƯỚC MỌI THỨ KHÁC, kể cả trước khi xét có phải ngủ không.
        #
        # Job đang bay là job KHÁCH ĐÃ TRẢ TIỀN. Nếu nhà máy vừa báo dừng
        # (`503` từ một job khác) thì `cho > 0` và vòng lặp rơi vào nhánh ngủ bên
        # dưới — những job đang bay vẫn về đích trong lúc đó, nhưng không ai nhặt.
        # Ngủ đủ lâu thì `da_cho` chạm `cho_toi_da`, vòng lặp `break`, và kết quả
        # của chúng bị vứt: khách mất tiền cho ảnh đã dựng xong.
        #
        # Nhặt ở đây là KHÔNG CHỜ, nên nó không làm chậm nhánh nào cả.
        _lai_som = _thu_hoach(cho_it_nhat_mot=False)
        if _lai_som:
            con_lai = sorted(_lai_som) + con_lai

        # ⚠ NGỦ TRƯỚC, HỎI SAU. Đang trong quãng dừng thì `GET /v1/me` không đổi
        # được gì cả — trần chỉ mở lại khi có máy xử lý báo danh, chứ không phải
        # vì ta hỏi nhiều hơn. Hỏi trong lúc chờ là tự đốt hạn mức đọc trạng thái.
        # ⚠ HAI quãng chờ, không phải một: `nhip` chờ vì nhà máy dừng, `cong` chờ
        # vì hàng đang dài / vừa ăn 429. Lấy cái LỚN HƠN — cả hai đều là "đừng
        # gửi bây giờ", và bỏ qua một cái là bỏ qua đúng lý do quan trọng hơn.
        cho = max(float(nhip.cho_bao_lau()), float(cong.cho_bao_lau()))
        # Còn job đang bay thì KHÔNG ngủ trọn quãng: phải quay lại nhặt. Quãng
        # dừng là lệnh "đừng GỬI THÊM", không phải "đừng nhận hàng về".
        if cho > 0 and dang_bay:
            cho = min(cho, NHIP_KIEM_DUNG)
        if cho > 0:
            if da_cho >= cho_toi_da:
                log("API shopapi: nha may {0} DUNG qua lau (da cho {1:.0f}s) -> bo {2} viec con lai. "
                    "Chung van con nguyen trong Excel, lan chay sau nhat lai duoc."
                    .format(loai, da_cho, len(con_lai)), "ERROR")
                break
            buoc = min(cho, cho_khi_dung)
            log("API shopapi: me {0} dang CHO -> {1:.0f}s roi hoi lai "
                "(con {2} viec, da cho {3:.0f}/{4:.0f}s) | {5}"
                .format(loai, buoc, len(con_lai), da_cho, cho_toi_da,
                        cong.ly_do() or cong.mo_ta()), "WARN")
            ngu(buoc)
            da_cho += buoc
            continue

        # Trần máy chủ là mức CHẶN TRÊN, đọc lại MỖI LÔ (không phải mỗi job:
        # nhóm đọc trạng thái có hạn mức riêng). `dat_tran(0)` tự chuyển vòng dò
        # sang trạng thái "nhà máy đang dừng" -> vòng sau rơi vào nhánh ngủ trên.
        tran = _doc_tran()
        # ⚠ HỒI NHỊP TRƯỚC KHI ĐẶT TRẦN. Nhà máy đã yên lặng đủ lâu mà nhịp vẫn
        # thấp hơn hẳn trần được cấp -> nhảy về nửa trần thay vì bò +1 mỗi lô.
        # Xem khối `why` dài ở `can_hoi_nhip`: bò +1 với job 70 giây nghĩa là
        # ba mươi phút chạy ở một phần tư công suất.
        if can_hoi_nhip(nhip, tran, lan_nghen[0], time.monotonic()):
            _cu = int(nhip.cho_phep())
            _moi = max(1, int(tran * TI_LE_HOI))
            nhip = _tao_nhip(bat_dau=_moi)
            nhip.dat_tran(tran)
            lan_nghen[0] = time.monotonic()   # cho quãng yên lặng đếm lại
            log("API shopapi: me {0} - {1:.0f}s khong nghen ma nhip van {2}/{3} -> HOI VE {4}. "
                "(bo len +1 moi lo se mat hang gio voi job dai)"
                .format(loai, YEN_LANG_GIAY, _cu, tran, _moi))
        nhip.dat_tran(tran)
        cong.dat_tran(tran)
        # ⚠ CỔNG CẮT SAU NHỊP. `nhip.cho_phep()` nói "chạy nhanh tới đâu";
        # `cong.giu_cho()` nói "hàng chờ còn nuốt được mấy job nữa". Lô thật là
        # phần giao của hai câu đó — và `giu_cho` GIỮ CHỖ luôn, nên không có khe
        # nào cho hai luồng cùng thấy "còn chỗ" rồi cùng gửi.
        # ⚠ TRỪ ĐI SỐ JOB ĐANG BAY. `cho_phep()` là trần cho TỔNG số job đang
        # chạy, không phải cho mỗi lần gửi. Quên trừ là mỗi vòng lại xin thêm
        # trọn một trần nữa, và tool vượt trần máy chủ trong vài vòng.
        # Chốt sổ sản lượng: đông hơn mà ra ít hơn thì lùi về mức tốt nhất.
        hieu_qua.nhip_tich(len(dang_bay))
        _chot = hieu_qua.chot_so()
        if _chot is not None:
            _sl, _cl = _chot
            log("API shopapi: me {0} - do duoc {1:.0f} job/phut o {2} job cung luc{3}"
                .format(loai, _sl, _cl,
                        " -> HA tran xuong {0} (dong hon ma ra it hon)".format(hieu_qua.cho_phep())
                        if hieu_qua.cho_phep() else ""))
        n = max(0, int(nhip.cho_phep()) - len(dang_bay))
        _tran_hq = hieu_qua.cho_phep()
        if _tran_hq:
            n = min(n, max(0, _tran_hq - len(dang_bay)))
        # Mép THẬT vừa đo bằng một cú `429` đáng tin hơn con số máy chủ rao:
        # nó đến từ lượt gửi thật, ngay lúc này, và đã tính cả khách khác lẫn
        # các máy khác đang dùng chung khoá.
        _tran_mep = _tran_mep_that()
        if _tran_mep:
            n = min(n, max(0, _tran_mep - len(dang_bay)))
        # ⚠ CẮT THEO SỐ VIỆC CÒN LẠI **TRƯỚC KHI** GIỮ CHỖ, đừng làm ngược.
        # Xin 6 chỗ khi chỉ còn 5 việc là giữ 6 mà chỉ trả 5 — mỗi lượt cuối rò
        # một chỗ, và sau vài mẻ nối tiếp thì cổng khoá cứng vì tưởng còn job
        # đang bay. Bài `test_cong_nha_HET_cho_du_lo_an_429` bắt đúng lỗi này.
        n = min(n, len(con_lai))
        if n > 0:
            n = thung.xin(n)          # ghìm nhịp rót TRƯỚC khi giữ chỗ
        if n > 0:
            n = cong.giu_cho(n)
        if n <= 0:
            if dang_bay:
                # CÒN JOB ĐANG BAY thì không được ngủ: chỉ cần MỘT cái xong là
                # có chỗ trống ngay. Đây chính là chỗ thay cho hàng rào cũ.
                tra_lai = _thu_hoach(cho_it_nhat_mot=True)
                if tra_lai:
                    log("API shopapi: me {0} - {1} viec bi tu choi o cua ({2}) -> TRA VE "
                        "DAU HANG CHO, KHONG mat viec, KHONG bi tru tien"
                        .format(loai, len(tra_lai), nhip.mo_ta()), "WARN")
                    con_lai = sorted(tra_lai) + con_lai
                continue
            _cho_gui = thung.cho_bao_lau()
            if _cho_gui > 0:
                # Hết token gửi: nghỉ ĐÚNG tới lúc có token, không nghỉ trọn
                # quãng dài — hàng còn đang bay và phải quay lại nhặt.
                ngu(min(_cho_gui, NHIP_KIEM_DUNG))
                continue
            if nhip.cho_bao_lau() <= 0 and cong.cho_bao_lau() <= 0:
                # Cổng đóng vì hàng dài chứ không vì một quãng dừng có hạn -> phải
                # tự ngủ, nếu không vòng này quay tít và đốt CPU.
                log("API shopapi: me {0} -> NGUNG gui them ({1}); cho {2:.0f}s roi hoi lai "
                    "({3} viec con lai, da cho {4:.0f}/{5:.0f}s)"
                    .format(loai, cong.ly_do() or cong.mo_ta(), cho_khi_dung,
                            len(con_lai), da_cho, cho_toi_da), "WARN")
                if da_cho >= cho_toi_da:
                    log("API shopapi: hang cho {0} van dai qua lau -> bo {1} viec con lai. "
                        "Chung van con nguyen trong Excel, lan chay sau nhat lai duoc."
                        .format(loai, len(con_lai)), "ERROR")
                    break
                ngu(cho_khi_dung)
                da_cho += cho_khi_dung
            continue

        lo, con_lai = con_lai[:n], con_lai[n:]
        lo_thu += 1
        # ⚠ GỬI TỪNG CÁI VÀ CHỊU ĐƯỢC LỖI GIỮA CHỪNG.
        #
        # `ThreadPoolExecutor.submit` ném `RuntimeError` khi hệ điều hành không
        # tạo nổi luồng nữa. Ở nhịp cũ (12 luồng) chuyện đó không bao giờ xảy ra;
        # ở nhịp mới thì một máy chạy 8 tiến trình mã × 88 luồng là 704 luồng, và
        # đó là vùng mà Windows bắt đầu từ chối.
        #
        # Để lỗi bay thẳng ra thì việc đã bốc khỏi `con_lai` biến mất không dấu
        # vết: khách trả tiền cho một scene không bao giờ được gửi đi, và Excel
        # vẫn ghi "chưa làm" nên lần sau chạy lại mới ra. Trả nó về hàng chờ và
        # HẠ NHỊP mới đúng — máy đã hết luồng thì xin thêm chỗ cũng vô nghĩa.
        gui_duoc = 0
        for vi_tri, i in enumerate(lo):
            try:
                dang_bay[pool.submit(_boc, chay_mot, viec[i], gia_tri_khi_hong,
                                     log, cong)] = i
            except Exception as e:  # noqa: BLE001 — hết luồng / pool đã đóng
                con_lai = lo[vi_tri:] + con_lai
                cong.tra_cho(len(lo) - vi_tri)
                nhip.bi_chan()
                # ⚠ HẠ NGÂN SÁCH CHUNG, KHÔNG CHỈ HẠ NHỊP CỦA MÌNH. Máy vừa nói
                # "không mở thêm luồng được nữa" — đó là sự thật về CẢ MÁY, nên
                # bảy tiến trình anh em cũng phải biết. Không ghi ra chỗ chung
                # thì từng đứa lần lượt đâm vào đúng bức tường đó, mỗi lần mất
                # một lô và một nhịp.
                try:
                    moi_muc = _sc.ha_ngan_sach_luong()
                except Exception:
                    moi_muc = "?"
                log("API shopapi: me {0} khong mo them duoc luong ({1}: {2}) -> tra {3} "
                    "viec ve hang cho, HA NHIP, va ha ngan sach luong CA MAY xuong {4}."
                    .format(loai, type(e).__name__, e, len(lo) - vi_tri, moi_muc), "ERROR")
                break
            gui_duoc += 1
        log("API shopapi: me {0} lo {1} -> ban them {2} job ({3} dang bay, {4} viec con lai) | {5}"
            .format(loai, lo_thu, gui_duoc, len(dang_bay), len(con_lai), cong.mo_ta()))
        if not gui_duoc:
            # Không gửi nổi cái nào mà cũng không có gì đang bay -> phải nghỉ,
            # nếu không vòng này quay tít và đốt CPU đúng lúc máy đang ngộp.
            if not dang_bay:
                ngu(cho_khi_dung)
                da_cho += cho_khi_dung
            continue

        # Nhặt những cái ĐÃ xong (không chờ) để lấp chỗ ngay vòng sau. Chỉ khi
        # đã bắn hết việc mới chịu đứng lại chờ — lúc đó không còn gì để lấp.
        tra_lai = _thu_hoach(cho_it_nhat_mot=not con_lai)
        if tra_lai:
            log("API shopapi: me {0} - {1} viec bi tu choi o cua ({2}) -> TRA VE DAU HANG CHO, "
                "KHONG mat viec, KHONG bi tru tien"
                .format(loai, len(tra_lai), nhip.mo_ta()), "WARN")
            # Về ĐẦU hàng chờ để việc bị hoãn không tụt xuống cuối mẻ rồi chờ mãi.
            con_lai = sorted(tra_lai) + con_lai
    finally:
        # ⚠ TRẢ CHỖ CHO MỌI JOB CÒN BAY, kể cả khi thoát vì `dung_lai` hay lỗi.
        # Rò chỗ ở đây là cổng chỉ tăng không giảm -> mẻ SAU khoá cứng mà không
        # một dòng nào nói vì sao. `cong` sống lâu hơn một mẻ nên đây là thật.
        if dang_bay:
            cong.tra_cho(len(dang_bay))
        # Không chờ job dở: `dung_lai` nghĩa là khách bấm Dừng, đứng đợi thêm
        # vài phút nữa là đúng thứ họ vừa bảo đừng làm.
        pool.shutdown(wait=False)
        # Nhả chỗ ngồi NGAY, đừng đợi file nguội: mẻ vừa xong là phần trần của
        # nó phải về tay các mã còn đang chạy trong vòng vài giây, không phải
        # sau 90 giây.
        if _nhip_song is not None:
            _nhip_song.dong()

    return [ket_qua.get(i) for i in range(tong)]
