"""Tự dò nhịp — máy trạng thái AIMD cho phía KHÁCH.

═══════════════════════════════════════════════════════════════════════════════
 VÌ SAO KHÔNG PHẢI "ĐỌC MỘT CON SỐ RỒI BẮN ĐÚNG BẤY NHIÊU"
═══════════════════════════════════════════════════════════════════════════════

Bản trước của SDK làm đúng thế: gọi ``GET /v1/me``, đọc
``limits.concurrent_jobs[loại]``, bắn đúng chừng ấy job. Nghe hợp lý, nhưng nó
vẫn là **một con số cứng**, chỉ là được tính lại thường xuyên hơn. Ba chỗ nó sai:

  1. **Trần của máy chủ là mức KHÔNG ĐƯỢC VƯỢT, không phải mức PHẢI CHẠY.**
     Máy chủ chia sức chứa còn trống cho số khách đang chờ — nó biết nhà máy
     rộng bao nhiêu, nhưng nó KHÔNG biết đường mạng của bạn, không biết engine
     phía sau đang bị Google bóp, không biết máy bạn mở nổi mấy luồng. Bắn đúng
     bằng trần là tin rằng máy chủ biết mọi thứ.

  2. **Con số ấy đã cũ ngay khi bạn đọc xong.** Giữa lúc đọc trần và lúc job
     thứ n được nhận, mười khách khác có thể vừa xếp hàng. Cách duy nhất biết
     "vừa nãy có quá tay không" là **nhìn kết quả của chính mình**.

  3. **Nó không có đường lùi.** Ăn ``429`` xong, lô sau vẫn bắn đúng con số cũ,
     rồi lại ăn ``429``.

Thứ đúng là **tự dò**: chạy trơn thì tăng dần, gặp nghẽn thì lùi lại, và trần
máy chủ chỉ là mức chặn trên. Đây chính là chống tắc nghẽn kiểu TCP (AIMD —
*additive increase, multiplicative decrease*), và **chính nhà máy đã dùng đúng
cơ chế đó ở bên trong** (``AdaptiveLimiter`` trong engine Veo3: 6 lượt mượt thì
+1, dính throttle thì chia đôi). Lớp này mang cơ chế ấy ra cho phía khách.

═══════════════════════════════════════════════════════════════════════════════
 VÌ SAO "TĂNG CỘNG, GIẢM NHÂN" MÀ KHÔNG PHẢI NGƯỢC LẠI
═══════════════════════════════════════════════════════════════════════════════

Đây là bài học đã học được của ngành mạng máy tính, đừng nghĩ lại từ đầu:

  • **Tăng nhanh (nhân đôi) thì cả trăm tool cùng vọt lên, cùng ăn ``429``,
    cùng lùi, rồi cùng vọt lên lại.** Hệ dao động mãi, không ai hội tụ, và tổng
    thông lượng thấp hơn hẳn so với khi mọi người đi chậm.
  • **Giảm chậm (trừ 1) thì lùi không kịp.** Nhà máy đã nghẽn mà bạn còn 20 job
    bay vào, mỗi lô chỉ bớt 1 — bạn giữ nguyên tình trạng nghẽn thêm 20 lô nữa.

Tăng cộng + giảm nhân là cặp DUY NHẤT hội tụ về chia đều và ổn định khi có
nhiều bên cùng tranh một tài nguyên.

═══════════════════════════════════════════════════════════════════════════════
 BỐN TÍN HIỆU VÀO, VÀ VÌ SAO CHỌN ĐÚNG BỐN CÁI ĐÓ
═══════════════════════════════════════════════════════════════════════════════

``dat_tran(n)``       ← ``GET /v1/me`` → trần CỨNG, không bao giờ vượt. Đọc mỗi
                        lô, không phải mỗi request (nhóm đọc trạng thái có hạn
                        mức riêng — hỏi mỗi request là tự bắn vào chân mình).

``xong(cho_hang_doi)`` ← một job chạy xong êm. Đây là tín hiệu TĂNG.
                        ``cho_hang_doi`` là số giây job nằm ở ``queued`` trước
                        khi chuyển sang ``running``.

``bi_chan(cho)``      ← ``429``. Tín hiệu GIẢM MẠNH: chia đôi.

``nha_may_dung(cho)`` ← ``503 engine_unavailable``. Không phải "chậm lại" mà là
                        "dừng hẳn": nhà máy loại đó không có máy xử lý nào
                        online, gửi thêm là vô nghĩa. Chờ, rồi **thăm dò lại
                        bằng đúng MỘT job**.

⚠ VÌ SAO ĐO **THỜI GIAN NẰM HÀNG CHỜ** CHỨ KHÔNG ĐO TỔNG THỜI GIAN JOB

Cách hiển nhiên là đo tổng thời gian một job, thấy nó vọt lên thì lùi. Cách đó
SAI ở đây, và sai nặng: một job đọc 30.000 chữ mất lâu hơn job đọc 300 chữ cả
trăm lần, mà chẳng liên quan gì tới tắc nghẽn. Dùng nó làm tín hiệu thì tool sẽ
tự bóp mình về sàn ngay khi khách gửi một văn bản dài.

Thời gian nằm ở ``queued`` thì khác: nó gần như KHÔNG phụ thuộc nội dung, chỉ
phụ thuộc "nhà máy có chỗ trống ngay không". Đó đúng là định nghĩa của nghẽn.

Nền so sánh là **giá trị nhỏ nhất trong** ``nho_mau`` **mẫu gần nhất**, không
phải nhỏ nhất mọi thời đại: một lần may mắn 0,2 giây không được phép trở thành
thước đo vĩnh viễn cho cả buổi chiều đông khách.
"""

from __future__ import annotations

import math
import threading
import time
from collections import deque
from datetime import datetime
from typing import Any, Deque, Optional

__all__ = ["NhipDo", "cho_hang_doi_cua"]


def cho_hang_doi_cua(job: Any) -> Optional[float]:
    """Số giây một job đã nằm ở ``queued``, đọc từ chính đối tượng job.

    ``started_at − created_at``. Cả hai mốc do **máy chủ** đặt (``startedAt``
    được ghi đúng lúc worker nhận job), nên phép trừ này không dính lệch đồng hồ
    máy khách và không tốn thêm một lời gọi nào.

    Trả ``None`` khi job chưa từng chạy (bị từ chối ngay ở cửa) hoặc máy chủ
    không trả mốc — nơi gọi khi đó bỏ qua tín hiệu độ trễ, chứ không đoán bừa.
    """
    try:
        tao = job["created_at"]
        chay = job["started_at"]
    except (KeyError, TypeError):
        return None
    if not isinstance(tao, str) or not isinstance(chay, str):
        return None
    try:
        t0 = datetime.fromisoformat(tao.replace("Z", "+00:00"))
        t1 = datetime.fromisoformat(chay.replace("Z", "+00:00"))
    except ValueError:
        return None
    return max(0.0, (t1 - t0).total_seconds())

#: Nhịp khởi đầu. Bắt đầu từ 1 là chọn có chủ đích, không phải cho tiện.
#:
#: Một tool vừa khởi động thì chưa biết gì về tình trạng nhà máy lúc này, và
#: hàng trăm tool cùng khởi động buổi sáng mà mỗi cái vọt thẳng lên trần thì
#: chúng dựng ra đúng cơn nghẽn mà chúng định tránh. Bắt đầu từ 1 rồi +1 mỗi lô
#: mượt: đổi vài chục giây đầu lấy việc không bao giờ tự gây nghẽn.
NHIP_DAU = 1

#: Sàn. Không bao giờ tự bóp về 0 rồi đứng im — trừ khi máy chủ nói thẳng là nhà
#: máy đang dừng (``nha_may_dung``), và cả lúc đó vẫn còn đường thăm dò trở lại.
SAN = 1

#: Chờ hàng đợi lâu gấp ngần này lần mức nền thì coi là nghẽn.
#:
#: 4 lần là mức thoả hiệp đo được: nhỏ hơn (2 lần) thì nhiễu bình thường của
#: hàng chờ cũng kích, tool lùi vô cớ; lớn hơn (8 lần) thì lúc nhận ra đã nghẽn
#: sâu rồi.
NGUONG_TRE = 4.0

#: Cộng thêm ngần này giây trước khi so — chống chia cho số bé.
#:
#: Không có nó thì một mẫu nền 0,2 giây biến mọi lần chờ quá 0,8 giây thành
#: "nghẽn", trong khi 0,8 giây là hoàn toàn bình thường.
TRE_TOI_THIEU = 5.0

#: Số mẫu chờ gần nhất giữ lại để tính mức nền.
NHO_MAU = 20

#: Chờ mặc định khi máy chủ báo nhà máy dừng mà không kèm ``Retry-After``.
CHO_KHI_DUNG = 30.0


class NhipDo:
    """Máy trạng thái AIMD. **Thuần tuý, không tự gọi mạng, không tự ngủ.**

    Cố ý không có I/O bên trong: nơi gọi quyết định lúc nào ngủ và ngủ bao lâu.
    Nhờ vậy bộ test dựng lại được cả một buổi chiều đông khách trong vài
    mili-giây, thay vì phải chờ thật.

    Dùng thẳng (khi bạn không dùng ``client.chay_ca_me``)::

        nhip = NhipDo()

        while con_viec:
            nhip.dat_tran(client.tran_song_song("image"))   # đọc trần MỖI LÔ

            cho = nhip.cho_bao_lau()
            if cho > 0:
                time.sleep(cho)          # nhà máy đang dừng — chờ, đừng bỏ việc
                continue

            lo = con_viec[: nhip.cho_phep()]
            ...
            nhip.xong(cho_hang_doi=giay_nam_o_queued)   # mỗi job xong êm
    """

    def __init__(
        self,
        *,
        san: int = SAN,
        bat_dau: int = NHIP_DAU,
        tran: Optional[int] = None,
        nguong_tre: float = NGUONG_TRE,
        tre_toi_thieu: float = TRE_TOI_THIEU,
        nho_mau: int = NHO_MAU,
        _dong_ho=time.monotonic,
    ) -> None:
        self._san = max(1, int(san))
        self._nhip = float(max(self._san, int(bat_dau)))
        #: Còn trong pha LEO NHANH — chưa gặp tín hiệu nghẽn nào. Xem `xong()`.
        self._leo_nhanh = True
        self._tran: Optional[int] = None if tran is None else max(0, int(tran))
        self._nguong_tre = float(nguong_tre)
        self._tre_toi_thieu = float(tre_toi_thieu)
        self._mau_cho: Deque[float] = deque(maxlen=max(1, int(nho_mau)))
        self._chuoi = 0
        self._dung_toi = 0.0
        self._tham_do = False
        self._dong_ho = _dong_ho
        self._khoa = threading.Lock()

    # ── Đọc trạng thái ───────────────────────────────────────────────────────

    @property
    def nhip(self) -> float:
        """Nhịp thô hiện tại, CHƯA cắt theo trần máy chủ. Chủ yếu để soi/ghi log."""
        with self._khoa:
            return self._nhip

    @property
    def tran(self) -> Optional[int]:
        """Trần máy chủ đang biết. ``None`` = chưa hỏi lần nào."""
        with self._khoa:
            return self._tran

    def cho_phep(self) -> int:
        """Được bắn bao nhiêu job NGAY BÂY GIỜ.

        ``0`` nghĩa là đang trong quãng dừng — nơi gọi phải ngủ ``cho_bao_lau()``
        giây rồi hỏi lại, **không** phải bỏ việc của khách.
        """
        with self._khoa:
            return self._cho_phep()

    def cho_bao_lau(self) -> float:
        """Còn phải chờ mấy giây nữa mới được gửi tiếp. ``0.0`` = gửi được ngay."""
        with self._khoa:
            return max(0.0, self._dung_toi - self._dong_ho())

    def mo_ta(self) -> str:
        """Một dòng tiếng Việt cho log — đọc là biết vòng dò đang ở đâu."""
        with self._khoa:
            tran = "chưa hỏi" if self._tran is None else str(self._tran)
            cho = max(0.0, self._dung_toi - self._dong_ho())
            trang_thai = (
                f"đang dừng, còn {cho:.0f}s"
                if cho > 0
                else ("thăm dò bằng 1 job" if self._tham_do else "đang chạy")
            )
            return (
                f"nhịp {self._nhip:.1f} · cho phép {self._cho_phep()} · "
                f"trần máy chủ {tran} · chuỗi mượt {self._chuoi} · {trang_thai}"
            )

    # ── Tín hiệu vào ─────────────────────────────────────────────────────────

    def dat_tran(self, tran: Optional[int]) -> None:
        """Ghi nhận trần mới đọc từ ``GET /v1/me``.

        Trần là mức chặn trên TUYỆT ĐỐI: vượt là bị máy chủ từ chối, nên nhịp bị
        kéo xuống ngay chứ không chờ tới lô sau. Trần **tăng** thì nhịp KHÔNG tự
        nhảy theo — nhà máy rộng ra không có nghĩa là đường của bạn cũng rộng ra;
        vẫn phải dò lên từng bước một.

        ``tran = 0`` là máy chủ nói thẳng "nhà máy loại này đang dừng".
        """
        if tran is None:
            return
        tran = int(tran)
        with self._khoa:
            if tran <= 0:
                self._tran = 0
                self._nha_may_dung(CHO_KHI_DUNG)
                return
            self._tran = tran
            if self._nhip > tran:
                self._nhip = float(tran)

    def xong(self, cho_hang_doi: Optional[float] = None) -> None:
        """Một job vừa xong êm — tín hiệu TĂNG.

        ``cho_hang_doi``: số giây job nằm ở ``queued`` trước khi sang ``running``.
        Bỏ trống cũng chạy được (vòng dò vẫn tăng/giảm theo ``429``/``503``), chỉ
        là mất tín hiệu nghẽn sớm — tool sẽ chỉ nhận ra khi đã ăn ``429``.

        Tăng **+1 mỗi lô mượt**, chứ không phải +1 mỗi job: một "lô" ở đây là
        ``ceil(nhịp)`` job liên tiếp không có dấu hiệu nghẽn nào. Đây đúng là
        luật tăng của TCP (mỗi vòng cửa sổ thì +1), và nó khiến tốc độ dò lên tỉ
        lệ nghịch với nhịp hiện tại — càng chạy nhanh càng thận trọng.
        """
        with self._khoa:
            if self._tham_do:
                # Job thăm dò về đích: nhà máy sống lại. Vào lại từ SÀN, không
                # phải từ chỗ đã ngã — nhà máy vừa mới đứng dậy.
                #
                # ⚠ RA SỚM Ở ĐÂY, đừng để rơi xuống phần tăng bên dưới. Job thăm
                # dò là một lô cỡ 1, nên luật "trọn một lô mượt thì +1" sẽ đẩy
                # nhịp lên 2 ngay lập tức — tức là một nhà máy vừa mới đứng dậy
                # đã bị gấp đôi tải chỉ vì một job duy nhất về đích. Việc thăm dò
                # trả lời câu hỏi "nhà máy sống chưa", không phải "chạy được mấy
                # job"; câu sau để lô bình thường kế tiếp trả lời.
                self._tham_do = False
                self._nhip = float(self._san)
                self._chuoi = 0
                return

            if cho_hang_doi is not None:
                cho_hang_doi = max(0.0, float(cho_hang_doi))
                if self._tre_vot(cho_hang_doi):
                    # Nghẽn nhìn thấy TRƯỚC khi máy chủ phải nói ``429``.
                    self._mau_cho.append(cho_hang_doi)
                    self._giam()
                    return
                self._mau_cho.append(cho_hang_doi)

            # ═══ LEO NHANH TRƯỚC, LEO CHẬM SAU (slow-start) — 13/08/2026 ═══
            #
            # Luật cộng-dồn bên dưới cần ``_nhip`` lần xong mới lên được 1, tức
            # để tới nhịp N phải có 1+2+…+(N-1) job xong::
            #
            #     tới nhịp  40  ->      780 job
            #     tới nhịp 120  ->    7.140 job
            #     tới nhịp 979  ->  478.731 job
            #
            # Trên máy thật mỗi ảnh mất ~60 giây và một cú ``429`` chia đôi
            # nhịp. Nên nhịp **không bao giờ** tới trần: đo 13/08/2026 nó bò
            # quanh 1–20 suốt buổi trong khi máy chủ công bố trần 979, và người
            # dùng thấy 0,8 ảnh/phút trên một hàng chờ 441 ảnh. Nâng trần bao
            # nhiêu cũng vô nghĩa nếu vòng dò không leo tới đó nổi.
            #
            # Đây đúng bài toán TCP đã giải: **slow-start** — leo nhanh cho tới
            # khi chạm nghẽn LẦN ĐẦU, rồi mới chuyển sang dò từng bước quanh
            # mép vừa tìm được. Bản này dè dặt hơn TCP thật (cộng 1 mỗi job
            # xong thay vì nhân đôi), nên tới nhịp 120 cần 120 job thay vì
            # 7.140 — nhanh gấp 60 lần mà không bắn vọt.
            #
            # ``_giam()`` tắt pha này vĩnh viễn cho cả mẻ: mọi tín hiệu nghẽn
            # (429, hoặc trễ hàng chờ vọt) đều là "đã tìm thấy mép".
            if self._leo_nhanh:
                moi = self._nhip + 1.0
                if self._tran is not None:
                    moi = min(moi, float(max(self._tran, self._san)))
                self._nhip = moi
                self._chuoi = 0
                return

            self._chuoi += 1
            if self._chuoi >= max(1, math.ceil(self._nhip)):
                self._chuoi = 0
                moi = self._nhip + 1.0
                if self._tran is not None:
                    moi = min(moi, float(max(self._tran, self._san)))
                self._nhip = moi

    def bi_chan(self, cho: Optional[float] = None) -> None:
        """Ăn ``429`` — tín hiệu GIẢM MẠNH: **chia đôi**, không phải trừ 1.

        ``cho`` là giá trị header ``Retry-After``. Tầng HTTP của SDK đã tự ngủ
        đúng chừng ấy rồi thử lại, nên ở đây **không** đặt quãng dừng — làm thế
        là ngủ hai lần cho cùng một cú ``429``. Việc của lớp này chỉ là nhớ rằng
        vừa có nghẽn và hạ nhịp xuống.
        """
        with self._khoa:
            self._giam()

    def nha_may_dung(self, cho: Optional[float] = None) -> None:
        """``503 engine_unavailable`` — nhà máy loại này KHÔNG có máy xử lý nào.

        Khác hẳn ``429``. ``429`` là "bạn nhanh quá", chia đôi là đủ. Cái này là
        "không còn ai làm việc cả" — chia đôi thành 8 luồng gõ cửa một nhà máy
        đóng vẫn là 8 lượt vô nghĩa. Nên: **về 0, chờ, rồi thăm dò lại bằng đúng
        một job**. Job thăm dò về đích thì mới mở lại, và mở từ sàn.

        Bạn **không** bị trừ đồng nào cho những job bị từ chối ở cửa như thế này.
        """
        with self._khoa:
            self._nha_may_dung(CHO_KHI_DUNG if cho is None else max(0.0, float(cho)))

    def ghi_nhan_tu_choi(
        self,
        status_code: int,
        code: Optional[str] = None,
        retry_after: Optional[float] = None,
    ) -> None:
        """Cửa vào chung cho tầng HTTP: một phản hồi lỗi vừa về.

        Tồn tại vì tầng HTTP của SDK **tự thử lại** ``429``/``503`` — nếu lượt
        thử lại thành công thì vòng dò sẽ không bao giờ nhìn thấy cú nghẽn đó, và
        nó sẽ vui vẻ tăng nhịp giữa lúc nhà máy đang ngộp. Móc này cho nó thấy.
        """
        if status_code == 429:
            self.bi_chan(retry_after)
        elif status_code == 503 and code == "engine_unavailable":
            self.nha_may_dung(retry_after)

    # ── Bên trong ────────────────────────────────────────────────────────────

    def _cho_phep(self) -> int:
        if self._dong_ho() < self._dung_toi:
            return 0
        if self._tham_do:
            return 1
        n = int(self._nhip)
        if self._tran is not None:
            n = min(n, self._tran)
        return max(self._san, n)

    def _giam(self) -> None:
        # Chạm nghẽn = đã tìm thấy mép. Hết pha leo nhanh cho tới hết mẻ; từ
        # đây dò từng bước quanh mức máy chủ chịu được. Xem .
        self._leo_nhanh = False
        self._nhip = max(float(self._san), self._nhip / 2.0)
        self._chuoi = 0

    def _nha_may_dung(self, cho: float) -> None:
        self._nhip = float(self._san)
        self._chuoi = 0
        self._dung_toi = self._dong_ho() + cho
        self._tham_do = True

    def _tre_vot(self, cho_hang_doi: float) -> bool:
        if not self._mau_cho:
            return False
        nen = min(self._mau_cho)
        return cho_hang_doi > nen * self._nguong_tre + self._tre_toi_thieu
