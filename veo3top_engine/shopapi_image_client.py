"""Tạo ảnh bằng **API shopapi.vn** thay cho nhà máy Chrome.

Đặt cạnh `image_factory_client.py` và cố ý bắt chước chữ ký của nó
(`generate_image(...) -> (ok, info, err)`) để nhánh mới trong `ve3_worker.py`
trông giống hệt nhánh cũ — người đọc mã sau này không phải học thêm khuôn mới.

Khác biệt duy nhất về tham số: `image_inputs` của nhà máy cũ là các khối
`rawImageBytes` (Flow API nuốt bytes trực tiếp), còn ở đây ảnh tham chiếu phải là
**URL công khai**, nên hàm nhận đường dẫn máy / bytes rồi TỰ UPLOAD trước.
"""

from __future__ import annotations

import hashlib
import os
import re
import threading
import time

try:                       # chạy trong tool: import cùng thư mục veo3top_engine
    import shopapi_common as _sc
    import shopapi_batch as _mb
except ImportError:        # chạy như một gói: import tương đối
    from . import shopapi_common as _sc  # type: ignore
    from . import shopapi_batch as _mb   # type: ignore

__all__ = ["generate_image", "chuan_bi_reference_urls", "duong_dan_phu"]

#: Ảnh của tool luôn là PNG (`img/X.png`, `nv/X.png`) — dùng làm đuôi mặc định.
#: Đuôi file khi caller không nói rõ.
#:
#: ⚠ ĐUÔI DO TA ĐẶT, KHÔNG ĐỌC TỪ URL — VÀ CŨNG KHÔNG THEO `output.format`.
#:
#: Cả dây chuyền ràng buộc `vid/X.mp4` phải có `img/X.png` cùng tên gốc, nên đổi
#: đuôi theo định dạng máy chủ trả về sẽ làm đứt liên kết đó. Máy chủ hay trả
#: `jpeg` cho ảnh; ta vẫn lưu tên `.png` và **đó là cố ý**: bước tải video lên
#: nhận dạng bằng magic bytes (JPEG được chấp nhận), nên tên file không quan
#: trọng, còn tên gốc thì có.
#:
#: Ghi chú đổi giao file 14/08/2026 có cảnh báo "đừng đọc đuôi từ URL" — ta chưa
#: bao giờ làm thế. Cần đuôi đúng theo định dạng thật thì đã có
#: `shopapi_common.duoi_cua_output`.
_DUOI_MAC_DINH = ".png"


def _ten_file_ref(i, item):
    """Tên gợi ý gửi kèm khi upload bytes, để máy chủ đặt tên tải về cho đẹp."""
    if isinstance(item, (str, os.PathLike)):
        return os.path.basename(str(item)) or "ref{0}.png".format(i)
    return "ref{0}.png".format(i)


# ═══ ẢNH THAM CHIẾU: MỖI TẤM TẢI ĐÚNG MỘT LẦN — SỰ CỐ 03/10/2026 ═══
#
# Khách gunc94 chết cứng ở "Vượt hạn mức lưu trữ tạm" (trần máy chủ 500 MB /
# 2.000 tệp, tệp sống 2 giờ kể từ lần dùng). Soi máy chủ: 346 tệp tải lên trong
# 2 giờ nhưng chỉ 5 cỡ byte khác nhau — tức 5 ảnh nhân vật, mỗi job ảnh tải lại
# từ đầu. Bản hàm này ở engine máy chủ có bộ nhớ (`_NHO_REF`, sự cố 13/08) nhưng
# bộ nhớ ấy không có trong bản chép sang đây.
#
# Nhớ theo NỘI DUNG (băm byte) chứ không theo đường dẫn: cùng một ảnh đọc vào
# RAM ở hai chỗ vẫn là một khoá. Link hết hạn thì xin link mới cho CHÍNH tệp đã
# tải (`GET /v1/uploads/{id}`, không tốn chỗ kho); máy chủ đã xoá tệp mới tải lại.
_NHO_REF = {}            # khoá nội dung -> [url, lúc lấy link, lúc dùng gần nhất]
_KHOA_NHO = threading.Lock()
_KHOA_TUNG_ANH = {}

#: Tin link đã nhớ trong ngần này giây. Link ký sẵn của kho sống tới `expires_at`
#: (2 giờ kể từ lần dùng); 45 phút chừa biên rộng, quá thì hỏi lại máy chủ.
_HAN_LINK = 45 * 60.0
#: Tệp dùng trong ngần này giây là "đang dùng" — dọn kho không đụng tới.
_DANG_DUNG = 15 * 60.0
#: Kho đầy: xoá tối đa ngần này tệp cũ của chính tool này rồi thử lại một lần.
_SO_DON_MOI_LAN = 40
_DAU_HET_KHO = ("hạn mức lưu trữ", "storage quota", "quota exceeded",
                "file tải lên (tối đa")

_RE_MA = re.compile(r"/(upl_[A-Za-z0-9]+)")


def _ma_upl(url):
    m = _RE_MA.search(str(url or ""))
    return m.group(1) if m else None


def _khoa_ref(item):
    """Khoá theo NỘI DUNG ảnh. `None` = không đọc được, cứ tải như cũ."""
    try:
        if isinstance(item, (bytes, bytearray)):
            return hashlib.sha1(bytes(item)).hexdigest()
        with open(os.fspath(item), "rb") as f:
            return hashlib.sha1(f.read()).hexdigest()
    except Exception:  # noqa: BLE001
        return None


def _khoa_cua(khoa):
    with _KHOA_NHO:
        k = _KHOA_TUNG_ANH.get(khoa)
        if k is None:
            k = _KHOA_TUNG_ANH[khoa] = threading.Lock()
        return k


def _link_moi(client, url_cu):
    """Link MỚI cho tệp đã tải (không tải lại). Tệp không còn thì trả ""."""
    ma = _ma_upl(url_cu)
    lay = getattr(getattr(client, "uploads", None), "retrieve", None)
    if not ma or lay is None:
        return ""
    try:
        tra = lay(ma)
    except Exception:  # noqa: BLE001 — 404 / mạng: tải bản mới
        return ""
    url = tra.get("url") if isinstance(tra, dict) else getattr(tra, "url", None)
    return str(url) if url and str(url).lower().startswith("https://") else ""


def _la_het_kho(exc):
    chu = str(exc).lower()
    return any(d in chu for d in _DAU_HET_KHO)


def don_kho_tam(client, toi_da=_SO_DON_MOI_LAN, log=print):
    """Kho tạm đầy → xoá tệp CỦA TOOL NÀY đã thôi dùng, cũ nhất trước.

    Chừa tệp dùng trong `_DANG_DUNG` giây (ảnh nhân vật của job đang chạy).
    Trả số tệp đã xoá được.
    """
    bay_gio = time.time()
    with _KHOA_NHO:
        ung = sorted(((v[2], k, v[0]) for k, v in _NHO_REF.items()
                      if bay_gio - v[2] >= _DANG_DUNG), key=lambda x: x[0])
    da = 0
    for _luc, khoa, url in ung[:max(0, int(toi_da))]:
        ma = _ma_upl(url)
        if not ma:
            continue
        try:
            client.uploads.delete(ma)
            da += 1
        except Exception:  # noqa: BLE001 — tệp có thể đã hết hạn; vẫn bỏ khỏi sổ
            pass
        with _KHOA_NHO:
            _NHO_REF.pop(khoa, None)
    if da:
        log("    [shopapi-img] kho tam day -> da xoa {0} anh cu khong con dung".format(da),
            "WARN")
    return da


def _tai_that(client, i, item, log):
    try:
        url = client.uploads.upload_file(item, filename=_ten_file_ref(i, item))
    except Exception as exc:  # noqa: BLE001
        if not _la_het_kho(exc) or don_kho_tam(client, log=log) == 0:
            raise
        url = client.uploads.upload_file(item, filename=_ten_file_ref(i, item))
    # Để lại một bản ngay trên đĩa máy này: worker veo3 chạy cùng máy, nên
    # nó khỏi phải tải tấm ảnh vừa đi Singapore quay ngược về. Xem
    # `shopapi_common.luu_ban_cuc_bo` để biết số đo.
    _sc.luu_ban_cuc_bo(item, url)
    return url


def _tai_mot(client, i, item, log):
    """Một ảnh tham chiếu → URL: dùng lại link đã có, chỉ tải khi chưa có."""
    khoa = _khoa_ref(item)
    if khoa is None:
        return _tai_that(client, i, item, log)
    with _khoa_cua(khoa):
        with _KHOA_NHO:
            cu = _NHO_REF.get(khoa)
        if cu is not None:
            if time.time() - cu[1] < _HAN_LINK:
                with _KHOA_NHO:
                    cu[2] = time.time()
                return cu[0]
            moi = _link_moi(client, cu[0])
            if moi:
                with _KHOA_NHO:
                    _NHO_REF[khoa] = [moi, time.time(), time.time()]
                return moi
        url = _tai_that(client, i, item, log)
        with _KHOA_NHO:
            _NHO_REF[khoa] = [url, time.time(), time.time()]
        return url


def xoa_nho():
    """Quên mọi link đã nhớ — CHỈ cho bài kiểm."""
    with _KHOA_NHO:
        _NHO_REF.clear()
        _KHOA_TUNG_ANH.clear()


def chuan_bi_reference_urls(client, reference_images, log=print):
    """Đổi danh sách ảnh tham chiếu thành danh sách **URL công khai**.

    Nhận lẫn lộn ba kiểu và xử lý từng kiểu:

    * chuỗi bắt đầu bằng `http` → đã là URL, dùng thẳng (không upload lại)
    * đường dẫn máy (`str`/`Path`) → upload
    * `bytes` (ảnh nhân vật đã đọc sẵn vào RAM) → upload

    ⚠ Máy chủ chỉ nhận **tối đa 10** ảnh tham chiếu. Quá thì hàm **cắt bớt và
    ghi cảnh báo TO**, không gửi mù: gửi 12 cái là 400 cho cả job, mất luôn ảnh
    thứ 11-12 lẫn 10 cái hợp lệ. Cắt còn 10 vẫn ra ảnh dùng được.
    """
    if not reference_images:
        return []

    items = list(reference_images)
    if len(items) > _sc.MAX_REFERENCE_IMAGES:
        log("    [shopapi-img] CANH BAO: co {0} anh tham chieu nhung API chi nhan toi da "
            "{1} -> CAT BOT {2} anh cuoi. Anh ra co the lech nhan vat."
            .format(len(items), _sc.MAX_REFERENCE_IMAGES,
                    len(items) - _sc.MAX_REFERENCE_IMAGES), "WARN")
        items = items[:_sc.MAX_REFERENCE_IMAGES]

    urls = []
    for i, item in enumerate(items):
        if isinstance(item, str) and item.lower().startswith(("http://", "https://")):
            urls.append(item)
            continue
        # Đường dẫn máy KHÔNG gửi thẳng lên được: máy chủ không nhìn thấy ổ D của
        # bạn. Phải upload để đổi lấy URL công khai trước.
        # Mỗi tấm MỘT lần cho cả mẻ, không phải mỗi job một lần (`_tai_mot`).
        urls.append(_tai_mot(client, i, item, log))
    return urls


def duong_dan_phu(out_path, i):
    """Đường dẫn cho ảnh thứ `i` (0-based) của một job `n>1`.

    Ảnh **đầu** giữ ĐÚNG tên caller yêu cầu, các ảnh sau thêm hậu tố `_2`, `_3`…
    Ràng buộc `vid/X.mp4` phải có `img/X.png` cùng stem phụ thuộc vào điều này,
    nên quy tắc đặt tên được tách ra một chỗ duy nhất thay vì viết lại inline.
    """
    if i == 0:
        return str(out_path)
    duoi = os.path.splitext(str(out_path))[1] or _DUOI_MAC_DINH
    goc = os.path.splitext(str(out_path))[0]
    return "{0}_{1}{2}".format(goc, i + 1, duoi)


def _nem_neu_nghen(exc, nem):
    """Đổi `429`/`503` thành :class:`shopapi_common.BiNghen` — nhưng CHỈ khi được phép.

    VÌ SAO CÓ CÔNG TẮC `nem`: hai chỗ gọi cần hai hành vi trái ngược nhau.

    * Trong một mẻ (`shopapi_batch.chay_ca_me`) → phải ném, để vòng dò nhịp thấy
      cú nghẽn, hạ nhịp, và **trả việc về hàng chờ chứ không tính là hỏng**.
    * Gọi lẻ một phát → tuyệt đối không được ném: hợp đồng của hàm này là trả
      `(ok, info, err)`, ném ra là làm sập nơi gọi vốn không biết `BiNghen`.
    """
    if not nem:
        return
    nghen = _sc.phan_loai_nghen(exc)
    if nghen is not None:
        raise nghen


def generate_image(prompt, out_path, aspect=None, seed=None, reference_images=None,
                   n=1, timeout=900, log=print, api_key=None, client=None,
                   nem_khi_nghen=False, out_paths=None):
    """Gửi 1 job ảnh tới API shopapi. Trả `(success, info, error)`.

    Hàm **tự ghi file ra đĩa** tại `out_path` (và `out_path` thứ 2, 3… khi
    `n>1`); giá trị trả về không bao giờ chứa bytes ảnh.

    `info` chứa `media_name` (= mã job, để ghi vào Excel làm dấu vết),
    `bytes`, `job_id`, `cost`, `extra_paths`.

    GỘP NHIỀU ẢNH CÙNG PROMPT VÀO **MỘT** JOB
    -----------------------------------------
    `n` tới `MAX_ANH_MOT_JOB` (8) ảnh trong một job. Đây là món hời rẻ nhất của
    cả nhánh API: k ảnh cùng prompt gộp lại chỉ chiếm **một** chỗ trong trần
    song song và **một** lần xếp hàng, thay vì k chỗ và k lần. Trần song song là
    tài nguyên khan hiếm nhất ở đây, nên tiêu 1 thay vì k là nhân công suất lên.

    `out_paths` (danh sách) = "gộp `len(out_paths)` ảnh vào 1 job, ảnh thứ i ghi
    vào `out_paths[i]`" — dùng khi nhiều scene khác nhau tình cờ **cùng một
    prompt**, mỗi scene cần file mang tên riêng của nó. Truyền `out_paths` thì
    `n` và `out_path` bị bỏ qua.

    `client` chỉ để **kiểm thử** tiêm client giả — chạy thật thì để `None`.
    `nem_khi_nghen` xem :func:`_nem_neu_nghen`.
    """
    # `out_paths` thắng: nó nói rõ cả SỐ LƯỢNG lẫn CHỖ GHI của từng ảnh.
    danh_sach_dich = [str(p) for p in (out_paths or []) if str(p or "").strip()]
    if danh_sach_dich:
        so_anh = len(danh_sach_dich)
        out_path = danh_sach_dich[0]
    else:
        so_anh = max(1, int(n or 1))

    if so_anh > _sc.MAX_ANH_MOT_JOB:
        # Gửi n>8 là 400 cho CẢ job -> mất luôn 8 ảnh hợp lệ. Cắt còn 8 và báo TO.
        log("    [shopapi-img] CANH BAO: xin {0} anh mot job nhung API chi nhan toi da {1} "
            "-> CAT CON {1}. Phan con lai phai chia thanh job khac."
            .format(so_anh, _sc.MAX_ANH_MOT_JOB), "WARN")
        so_anh = _sc.MAX_ANH_MOT_JOB
        danh_sach_dich = danh_sach_dich[:so_anh]

    if client is None:
        try:
            client = _sc.tao_client(api_key=api_key, timeout=max(60.0, float(timeout)))
        except Exception as exc:
            return False, {}, "shopapi-img: {0}".format(_sc.mo_ta_loi(exc))

    ty_le = _sc.ty_le_api(aspect)

    try:
        urls = chuan_bi_reference_urls(client, reference_images, log=log)
    except Exception as exc:
        # Upload cũng đi qua cùng cái cổng nên cũng ăn 429/503 như job.
        _nem_neu_nghen(exc, nem_khi_nghen)
        return False, {}, "shopapi-img: upload anh tham chieu that bai: {0}".format(
            _sc.mo_ta_loi(exc))

    def _bao_tien_do(job):
        """Đẩy tiến độ lên GUI qua `self.log` (worker đã bọc thành `@@LOG|`)."""
        try:
            log("    [shopapi-img] {0} {1}%".format(
                job["status"], int(job.get("progress") or 0)))
        except Exception:
            pass

    def _bao_hang_cho(vi_tri, uoc_giay):
        """Máy chủ vừa nhận job -> nói ngay hàng dài bao nhiêu cho cổng của mẻ.

        `queue_position` / `estimated_seconds` đi kèm sẵn trong phản hồi `202`,
        không tốn thêm lời gọi nào. Bỏ qua chúng chính là lỗi đã làm hỏng 27 job
        ngày 07/08/2026 — 66 job bắn vào một nhà máy tiêu hoá ~16, 33 job chết
        vì hết hạn NGAY TRONG HÀNG CHỜ, kho tài khoản KHÔNG hề cạn.
        Gọi ngoài mẻ thì `bao_hang_cho` không làm gì cả.
        """
        _mb.bao_hang_cho(vi_tri, uoc_giay)
        if vi_tri is not None:
            log("    [shopapi-img] may chu nhan job | dung thu {0} trong hang"
                "{1}".format(vi_tri,
                             ", uoc {0:.0f}s toi luot".format(uoc_giay)
                             if uoc_giay is not None else ""))
        if uoc_giay is not None and float(uoc_giay) > float(timeout):
            log("    [shopapi-img] CANH BAO: may chu uoc {0:.0f}s moi toi luot nhung tool "
                "chi cho duoc {1:.0f}s -> job nay co the het han NGAY TRONG HANG CHO"
                .format(float(uoc_giay), float(timeout)), "WARN")

    try:
        job = _sc.tao_va_cho(
            client, "images",
            timeout=float(timeout),
            on_progress=_bao_tien_do,
            on_hang_cho=_bao_hang_cho,
            prompt=prompt,
            n=so_anh,
            aspect_ratio=ty_le,
            seed=seed,
            reference_images=urls or None,
        )
    except Exception as exc:
        # `429`/`resource_exhausted` -> ca me lui nhip, khong rieng luong nay.
        if _sc.phan_loai_nghen(exc) is not None:
            _mb.bao_nghen(getattr(exc, "retry_after", None))
        _nem_neu_nghen(exc, nem_khi_nghen)
        return False, {}, "shopapi-img: {0}".format(_sc.mo_ta_loi(exc))

    # ⚠ `n>1`: TOÀN BỘ ảnh nằm ở `outputs`, `output` chỉ là cái đầu tiên.
    outputs = _sc.lay_outputs(job)
    if not outputs:
        return False, {}, "shopapi-img: job {0} bao thanh cong nhung khong co file ket qua".format(
            _lay(job, "id"))

    ma_job = _lay(job, "id")
    extra_paths = []
    da_ghi = []
    try:
        for i, output in enumerate(outputs):
            url = _sc.url_cua_output(output)
            if not url:
                continue
            if danh_sach_dich:
                # Gộp nhiều scene cùng prompt: mỗi ảnh về đúng file của scene nó.
                # Máy chủ trả dư (không nên xảy ra) thì phần dư dùng quy tắc _2/_3.
                dich = danh_sach_dich[i] if i < len(danh_sach_dich) \
                    else duong_dan_phu(danh_sach_dich[-1], i - len(danh_sach_dich) + 1)
            else:
                dich = duong_dan_phu(out_path, i)
            # ⚠ TẢI QUA `/download`, ĐỪNG BÁM `output.url`.
            #
            # Từ 14/08/2026 `output.url` trỏ thẳng sang Google và chỉ sống ~6
            # giờ. Ở đây tải ngay nên phần lớn lần vẫn kịp — nhưng một mẻ lớn
            # nghẽn hàng chờ, hoặc một lượt thử lại, là quá đủ để link chết.
            # `tai_ket_qua` đi đường `/download` (không hết hạn), rồi mới lùi về
            # `output.url`, rồi mới xin link tươi.
            _sc.tai_ket_qua(client, ma_job, dich, index=i, timeout=float(timeout),
                            url_du_phong=url, log=log)
            da_ghi.append(dich)
            if i > 0:
                extra_paths.append(dich)
    except Exception as exc:
        _nem_neu_nghen(exc, nem_khi_nghen)
        return False, {}, "shopapi-img: tai ket qua ve dia that bai: {0}".format(
            _sc.mo_ta_loi(exc))

    if danh_sach_dich and len(da_ghi) < len(danh_sach_dich):
        # Xin k ảnh mà máy chủ chỉ trả về ít hơn: các scene thiếu file PHẢI được
        # biết là chưa xong, nếu không chúng bị đánh dấu "done" mà không có ảnh.
        return False, {"paths": da_ghi}, (
            "shopapi-img: xin {0} anh mot job nhung chi nhan duoc {1} -> so con lai "
            "phai tao lai".format(len(danh_sach_dich), len(da_ghi)))

    try:
        so_byte = os.path.getsize(str(out_path))
    except OSError:
        so_byte = 0

    info = {
        "backend": "shopapi",
        # Tool ghi `media_id` vào Excel để dò lại ảnh. API không có media_id kiểu
        # Flow, nên dùng mã job — vừa không rỗng (khỏi bị coi là "thiếu
        # reference"), vừa tra cứu được ở bảng điều khiển.
        "media_name": str(_lay(job, "id") or ""),
        "job_id": str(_lay(job, "id") or ""),
        "bytes": so_byte,
        "cost": str(_lay(job, "cost") or ""),
        "n": so_anh,
        "aspect": ty_le,
        "refs": len(urls),
        "extra_paths": extra_paths,
        # Đường dẫn TỪNG ảnh theo đúng thứ tự máy chủ trả — nơi gọi cần nó để
        # ghép ảnh thứ i về đúng scene thứ i khi gộp nhiều scene vào một job.
        "paths": da_ghi,
    }
    return True, info, ""


def _lay(obj, ten):
    """Đọc trường của `Model`/`dict` mà không nổ khi thiếu."""
    return _sc._lay_truong(obj, ten)
