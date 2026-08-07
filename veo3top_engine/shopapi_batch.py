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
    "CHO_KHI_DUNG",
    "CHO_TOI_DA",
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

#: Cờ "luồng này đang chạy trong một mẻ" — xem :func:`trong_me`.
_cuc_bo = threading.local()


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


def _boc(chay_mot, viec, gia_tri_khi_hong, log):
    """Chạy MỘT việc sao cho **không có gì lọt ra ngoài** làm chết cả mẻ.

    Trả `("nghen", BiNghen)` hoặc `("xong", giá_trị)`. Không bao giờ ném.

    Đây là chỗ phân biệt hai loại thất bại hoàn toàn khác nhau:

    * :class:`shopapi_common.BiNghen` — việc CHƯA chạy, chưa mất tiền → trả về
      hàng chờ, hạ nhịp, chạy lại ở lô sau.
    * ngoại lệ khác — việc này hỏng thật → ghi lỗi cho MÌNH NÓ rồi đi tiếp. 199
      scene còn lại không có tội gì mà phải chết theo.
    """
    _cuc_bo.trong_me = True
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


def chay_ca_me(viec, chay_mot, loai, tran_tool=None, client=None, api_key=None,
               log=print, ngu=time.sleep, dung_lai=None,
               cho_khi_dung=CHO_KHI_DUNG, cho_toi_da=CHO_TOI_DA,
               gia_tri_khi_hong=False, nhip=None):
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
    """
    tong = len(viec)
    if tong == 0:
        return []

    ket_qua = {}
    con_lai = list(range(tong))
    nhip = nhip if nhip is not None else _tao_nhip()
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
        cho = float(nhip.cho_bao_lau())
        if cho > 0:
            if da_cho >= cho_toi_da:
                log("API shopapi: nha may {0} DUNG qua lau (da cho {1:.0f}s) -> bo {2} viec con lai. "
                    "Chung van con nguyen trong Excel, lan chay sau nhat lai duoc."
                    .format(loai, da_cho, len(con_lai)), "ERROR")
                break
            buoc = min(cho, cho_khi_dung)
            log("API shopapi: nha may {0} dang DUNG -> cho {1:.0f}s roi hoi lai "
                "(con {2} viec, da cho {3:.0f}/{4:.0f}s)"
                .format(loai, buoc, len(con_lai), da_cho, cho_toi_da), "WARN")
            ngu(buoc)
            da_cho += buoc
            continue

        # Trần máy chủ là mức CHẶN TRÊN, đọc lại MỖI LÔ (không phải mỗi job:
        # nhóm đọc trạng thái có hạn mức riêng). `dat_tran(0)` tự chuyển vòng dò
        # sang trạng thái "nhà máy đang dừng" -> vòng sau rơi vào nhánh ngủ trên.
        nhip.dat_tran(_hoi_tran(loai, tran_tool, client=client, api_key=api_key, log=log))
        n = int(nhip.cho_phep())
        if n <= 0:
            if nhip.cho_bao_lau() <= 0:
                # Không đời nào tới đây với `NhipDo` thật, nhưng nếu một bản nhịp
                # khác trả 0 mà không đặt quãng dừng thì vòng này sẽ quay tít.
                ngu(cho_khi_dung)
                da_cho += cho_khi_dung
            continue

        lo, con_lai = con_lai[:n], con_lai[n:]
        lo_thu += 1
        log("API shopapi: me {0} lo {1} -> ban {2} job CUNG LUC ({3} viec con lai)"
            .format(loai, lo_thu, len(lo), len(con_lai)))

        tra_lai = []
        with ThreadPoolExecutor(max_workers=len(lo)) as pool:
            tuong_lai = {pool.submit(_boc, chay_mot, viec[i], gia_tri_khi_hong, log): i
                         for i in lo}
            for tl in as_completed(tuong_lai):
                i = tuong_lai[tl]
                trang_thai, gia_tri = tl.result()
                if trang_thai == "nghen":
                    # Bị từ chối NGAY Ở CỬA: chưa tốn tiền, việc chưa mất.
                    tra_lai.append(i)
                    if gia_tri.ma == 429:
                        nhip.bi_chan(gia_tri.cho)
                    else:
                        nhip.nha_may_dung(gia_tri.cho)
                    continue
                ket_qua[i] = gia_tri
                nhip.xong()

        if tra_lai:
            log("API shopapi: me {0} - {1} viec bi tu choi o cua ({2}) -> TRA VE DAU HANG CHO, "
                "KHONG mat viec, KHONG bi tru tien"
                .format(loai, len(tra_lai), nhip.mo_ta()), "WARN")
        # Về ĐẦU hàng chờ để việc bị hoãn không tụt xuống cuối mẻ rồi chờ mãi.
        con_lai = sorted(tra_lai) + con_lai

    return [ket_qua.get(i) for i in range(tong)]
