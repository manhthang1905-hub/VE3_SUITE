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
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

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


def _tao_nhip():
    """`NhipDo` của SDK khi có; không có thì bản dự phòng ở trên.

    Ưu tiên bản của SDK vì nó còn nhìn được **thời gian job nằm hàng chờ** —
    tín hiệu nghẽn nhìn thấy TRƯỚC khi phải ăn `429`.
    """
    try:
        _sc.bootstrap_sdk()
        from shopapi._nhip_do import NhipDo
        return NhipDo()
    except Exception:
        return _NhipDoDuPhong()


# ── Hỏi trần ─────────────────────────────────────────────────────────────────


def _hoi_tran(loai, tran_tool=None, client=None, api_key=None, log=print):
    """Một lời hỏi `/v1/me`, đã chặn trên. Trả `0` khi nhà máy đang dừng.

    Ba mức chặn, theo đúng thứ tự nghiêm ngặt dần:

    1. trần ĐỘNG của máy chủ — nguồn sự thật, đọc lại mỗi lô;
    2. trần CỨNG của loại job — chốt chặn phòng khi mức động trả về số vô lý;
    3. trần của TOOL — giới hạn người dùng đã đặt, không được phá dù máy chủ
       có rộng đến đâu (máy họ, mạng họ, tiền họ).

    Hỏi không được thì trả `1`: đoán thấp còn hơn đứng im, và cũng còn hơn đoán
    cao rồi đập vào máy chủ bằng một con số bịa.
    """
    try:
        # `tran_song_song` đã tự nuốt lỗi mạng, nhưng vẫn bọc thêm một lớp: hàm
        # này chạy trong vòng lặp của cả mẻ, một ngoại lệ lọt ra là chết cả mẻ vì
        # một lời hỏi trạng thái — cái giá quá đắt cho thứ chỉ để đoán số luồng.
        tran = int(_sc.tran_song_song(loai, api_key=api_key, client=client, mac_dinh=-1))
    except Exception:
        tran = -1

    if tran < 0:
        log("API shopapi: khong hoi duoc GET /v1/me cho '{0}' -> tam chay 1 job "
            "(doan thap con hon dung im)".format(loai), "WARN")
        tran = 1
    if tran == 0:
        return 0

    n = min(tran, _sc.tran_cung(loai))
    if tran_tool:
        try:
            gioi_han = int(tran_tool)
        except (TypeError, ValueError):
            gioi_han = 0
        if gioi_han > 0:
            n = min(n, gioi_han)
    return max(1, n)


def so_luong_song_song(loai, tran_tool=None, client=None, api_key=None, log=print,
                       ngu=time.sleep, cho_khi_dung=CHO_KHI_DUNG,
                       cho_toi_da=CHO_TOI_DA, dung_lai=None):
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
        tran = _hoi_tran(loai, tran_tool, client=client, api_key=api_key, log=log)
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


def _boc(chay_mot, viec, gia_tri_khi_hong, log, cong=None):
    """Chạy MỘT việc sao cho **không có gì lọt ra ngoài** làm chết cả mẻ.

    Trả `("nghen", BiNghen)` hoặc `("xong", giá_trị)`. Không bao giờ ném.

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
        return "xong", gia_tri_khi_hong
    finally:
        _cuc_bo.trong_me = False
        _cuc_bo.cong = None


def chay_ca_me(viec, chay_mot, loai, tran_tool=None, client=None, api_key=None,
               log=print, ngu=time.sleep, dung_lai=None,
               cho_khi_dung=CHO_KHI_DUNG, cho_toi_da=CHO_TOI_DA,
               gia_tri_khi_hong=False, nhip=None, cong=None, han_giay=0.0):
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
    nhip = nhip if nhip is not None else _tao_nhip()
    cong = cong if cong is not None else CongHangCho(han_giay=han_giay)
    da_cho = 0.0
    lo_thu = 0

    while con_lai:
        if dung_lai is not None and dung_lai():
            log("API shopapi: nhan lenh DUNG -> bo {0} viec con lai cua me {1}"
                .format(len(con_lai), loai), "WARN")
            break

        # ⚠ NGỦ TRƯỚC, HỎI SAU. Đang trong quãng dừng thì `GET /v1/me` không đổi
        # được gì cả — trần chỉ mở lại khi có máy xử lý báo danh, chứ không phải
        # vì ta hỏi nhiều hơn. Hỏi trong lúc chờ là tự đốt hạn mức đọc trạng thái.
        # ⚠ HAI quãng chờ, không phải một: `nhip` chờ vì nhà máy dừng, `cong` chờ
        # vì hàng đang dài / vừa ăn 429. Lấy cái LỚN HƠN — cả hai đều là "đừng
        # gửi bây giờ", và bỏ qua một cái là bỏ qua đúng lý do quan trọng hơn.
        cho = max(float(nhip.cho_bao_lau()), float(cong.cho_bao_lau()))
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
        tran = _hoi_tran(loai, tran_tool, client=client, api_key=api_key, log=log)
        nhip.dat_tran(tran)
        cong.dat_tran(tran)
        # ⚠ CỔNG CẮT SAU NHỊP. `nhip.cho_phep()` nói "chạy nhanh tới đâu";
        # `cong.giu_cho()` nói "hàng chờ còn nuốt được mấy job nữa". Lô thật là
        # phần giao của hai câu đó — và `giu_cho` GIỮ CHỖ luôn, nên không có khe
        # nào cho hai luồng cùng thấy "còn chỗ" rồi cùng gửi.
        n = int(nhip.cho_phep())
        # ⚠ CẮT THEO SỐ VIỆC CÒN LẠI **TRƯỚC KHI** GIỮ CHỖ, đừng làm ngược.
        # `lo = con_lai[:n]` tự cắt bớt, nên xin 6 chỗ khi chỉ còn 5 việc là giữ
        # 6 mà chỉ trả 5 — mỗi lô cuối rò một chỗ, và sau vài mẻ nối tiếp thì
        # cổng khoá cứng vì tưởng còn job đang bay. Bài
        # `test_cong_nha_HET_cho_du_lo_an_429` bắt được đúng lỗi này.
        n = min(n, len(con_lai))
        if n > 0:
            n = cong.giu_cho(n)
        if n <= 0:
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
        log("API shopapi: me {0} lo {1} -> ban {2} job CUNG LUC ({3} viec con lai) | {4}"
            .format(loai, lo_thu, len(lo), len(con_lai), cong.mo_ta()))

        tra_lai = []
        try:
            with ThreadPoolExecutor(max_workers=len(lo)) as pool:
                tuong_lai = {
                    pool.submit(_boc, chay_mot, viec[i], gia_tri_khi_hong, log, cong): i
                    for i in lo
                }
                for tl in as_completed(tuong_lai):
                    i = tuong_lai[tl]
                    trang_thai, gia_tri = tl.result()
                    if trang_thai == "nghen":
                        # Bị từ chối NGAY Ở CỬA: chưa tốn tiền, việc chưa mất.
                        tra_lai.append(i)
                        if gia_tri.ma == 429:
                            nhip.bi_chan(gia_tri.cho)
                            cong.bi_nghen(gia_tri.cho)
                        else:
                            nhip.nha_may_dung(gia_tri.cho)
                            cong.nha_may_dung(gia_tri.cho)
                        continue
                    ket_qua[i] = gia_tri
                    nhip.xong()
        finally:
            # ⚠ TRẢ ĐÚNG SỐ CHỖ ĐÃ GIỮ, và trả trong `finally`. Rò chỗ ở đây là
            # cổng chỉ tăng mà không giảm -> tới lúc nào đó khoá cứng cả mẻ mà
            # không một dòng nào nói vì sao. Trả THỪA cũng hỏng: cổng tưởng trời
            # trống rồi bắn quá tay trở lại. `len(lo)` là con số duy nhất đúng.
            cong.tra_cho(len(lo))

        if tra_lai:
            log("API shopapi: me {0} - {1} viec bi tu choi o cua ({2}) -> TRA VE DAU HANG CHO, "
                "KHONG mat viec, KHONG bi tru tien"
                .format(loai, len(tra_lai), nhip.mo_ta()), "WARN")
        # Về ĐẦU hàng chờ để việc bị hoãn không tụt xuống cuối mẻ rồi chờ mãi.
        con_lai = sorted(tra_lai) + con_lai

    return [ket_qua.get(i) for i in range(tong)]
