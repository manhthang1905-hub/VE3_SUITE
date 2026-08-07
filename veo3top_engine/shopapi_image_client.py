"""Tạo ảnh bằng **API shopapi.vn** thay cho nhà máy Chrome.

Đặt cạnh `image_factory_client.py` và cố ý bắt chước chữ ký của nó
(`generate_image(...) -> (ok, info, err)`) để nhánh mới trong `ve3_worker.py`
trông giống hệt nhánh cũ — người đọc mã sau này không phải học thêm khuôn mới.

Khác biệt duy nhất về tham số: `image_inputs` của nhà máy cũ là các khối
`rawImageBytes` (Flow API nuốt bytes trực tiếp), còn ở đây ảnh tham chiếu phải là
**URL công khai**, nên hàm nhận đường dẫn máy / bytes rồi TỰ UPLOAD trước.
"""

from __future__ import annotations

import os

try:                       # chạy trong tool: import cùng thư mục veo3top_engine
    import shopapi_common as _sc
except ImportError:        # chạy như một gói: import tương đối
    from . import shopapi_common as _sc  # type: ignore

__all__ = ["generate_image", "chuan_bi_reference_urls", "duong_dan_phu"]

#: Ảnh của tool luôn là PNG (`img/X.png`, `nv/X.png`) — dùng làm đuôi mặc định.
_DUOI_MAC_DINH = ".png"


def _ten_file_ref(i, item):
    """Tên gợi ý gửi kèm khi upload bytes, để máy chủ đặt tên tải về cho đẹp."""
    if isinstance(item, (str, os.PathLike)):
        return os.path.basename(str(item)) or "ref{0}.png".format(i)
    return "ref{0}.png".format(i)


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
        urls.append(client.uploads.upload_file(item, filename=_ten_file_ref(i, item)))
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

    try:
        job = client.images.create_and_wait(
            prompt=prompt,
            n=so_anh,
            aspect_ratio=ty_le,
            seed=seed,
            reference_images=urls or None,
            timeout=float(timeout),
            on_progress=_bao_tien_do,
        )
    except Exception as exc:
        _nem_neu_nghen(exc, nem_khi_nghen)
        return False, {}, "shopapi-img: {0}".format(_sc.mo_ta_loi(exc))

    # ⚠ `n>1`: TOÀN BỘ ảnh nằm ở `outputs`, `output` chỉ là cái đầu tiên.
    outputs = _sc.lay_outputs(job)
    if not outputs:
        return False, {}, "shopapi-img: job {0} bao thanh cong nhung khong co file ket qua".format(
            _lay(job, "id"))

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
            # Link sống 7 ngày -> tải NGAY, không lưu URL lại dùng sau.
            _sc.tai_ve(url, dich, timeout=float(timeout))
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
