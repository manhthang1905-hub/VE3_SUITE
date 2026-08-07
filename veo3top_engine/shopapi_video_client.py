"""Dựng video bằng **API shopapi.vn** thay cho nhà máy Chrome/Ultra.

Đặt cạnh `video_factory_client.py` và bắt chước chữ ký của nó
(`generate(image_path, prompt, out_path, ...) -> (ok, info, err)`) để nhánh mới
trong `ve3_worker.py` trông giống hệt nhánh cũ.

⛔ BẪY CHẾT NGƯỜI — THỜI LƯỢNG PHẢI KHỚP ENGINE
-----------------------------------------------
`engine` và `duration` là ánh xạ 1–1, không phải hai lựa chọn độc lập:

    veo3     ⇢ CHỈ nhận duration = 8
    seedance ⇢ CHỈ nhận duration = 10

Gửi sai là **422 `unsupported_parameter`** cho TỪNG job — một mã 200 scene là
200 lần lỗi liên tiếp. Tool VE3 là dây chuyền Veo3 (clip 8 giây khớp với nhịp
cắt của kịch bản), nên module này gửi cứng `engine="veo3", duration=8` và
KHÔNG mở tham số đó ra giao diện.

`engine="auto"` KHÔNG phải cơ chế dự phòng — nó chỉ chọn máy rảnh **trong cùng
một engine** ứng với `duration`. Engine đó chết thì job chết theo, nên chọn
`auto` cũng không cứu được gì mà lại làm mờ chuyện đang chạy máy nào.
"""

from __future__ import annotations

import os

try:                       # chạy trong tool: import cùng thư mục veo3top_engine
    import shopapi_common as _sc
except ImportError:        # chạy như một gói: import tương đối
    from . import shopapi_common as _sc  # type: ignore

__all__ = ["generate", "ENGINE", "DURATION"]

#: Engine của dây chuyền này. Đổi giá trị mà quên đổi :data:`DURATION` là 422.
ENGINE = "veo3"

#: Thời lượng DUY NHẤT mà Veo3 chấp nhận. Xem khối cảnh báo ở đầu file.
DURATION = 8


def _nem_neu_nghen(exc, nem):
    """`429`/`503` → :class:`shopapi_common.BiNghen`, nhưng CHỈ khi được phép.

    Giống hệt bản ở `shopapi_image_client`: trong một mẻ thì phải ném để vòng dò
    nhịp trả việc về hàng chờ; gọi lẻ thì tuyệt đối không, vì hợp đồng của hàm
    này là trả `(ok, info, err)` cho `_submit_video`.
    """
    if not nem:
        return
    nghen = _sc.phan_loai_nghen(exc)
    if nghen is not None:
        raise nghen


def generate(image_path, prompt, out_path, aspect=None, seed=None, timeout=1600,
             log=print, api_key=None, client=None, nem_khi_nghen=False):
    """Gửi 1 job video tới API shopapi. Trả `(success, info, error)` — KHỚP `_submit_video`.

    Hàm **tự ghi file ra đĩa** tại `out_path`; giá trị trả về không chứa bytes.

    `image_path` là ảnh scene trên máy → được upload thành URL công khai rồi gửi
    kèm `image_url` (chế độ ảnh-thành-video). Không có ảnh thì vẫn chạy được ở
    chế độ chữ-thành-video, nhưng dây chuyền VE3 luôn có ảnh nên đó là trường
    hợp bất thường và được ghi log.

    `seed` giữ trong chữ ký cho khớp `video_factory_client.generate`, nhưng API
    video hiện KHÔNG nhận `seed` — truyền vào chỉ được ghi log, không gửi lên.

    `client` chỉ để **kiểm thử** tiêm client giả — chạy thật thì để `None`.
    """
    if client is None:
        try:
            client = _sc.tao_client(api_key=api_key, timeout=max(60.0, float(timeout)))
        except Exception as exc:
            return False, {}, "shopapi-vid: {0}".format(_sc.mo_ta_loi(exc))

    if seed is not None:
        log("    [shopapi-vid] bo qua seed={0}: API video khong nhan tham so nay".format(seed))

    image_url = None
    if image_path:
        if isinstance(image_path, str) and image_path.lower().startswith(("http://", "https://")):
            image_url = image_path
        else:
            if not os.path.exists(str(image_path)):
                return False, {}, "shopapi-vid: khong thay anh scene {0}".format(image_path)
            try:
                # Máy chủ không nhìn thấy ổ đĩa của bạn -> phải upload lấy URL.
                image_url = client.uploads.upload_file(
                    str(image_path), filename=os.path.basename(str(image_path)))
            except Exception as exc:
                # Upload đi qua cùng cái cổng nên cũng ăn 429/503 như job.
                _nem_neu_nghen(exc, nem_khi_nghen)
                return False, {}, "shopapi-vid: upload anh scene that bai: {0}".format(
                    _sc.mo_ta_loi(exc))
    else:
        log("    [shopapi-vid] KHONG co anh scene -> chay che do chu-thanh-video", "WARN")

    ty_le = _sc.ty_le_api(aspect)

    def _bao_tien_do(job):
        try:
            log("    [shopapi-vid] {0} {1}%".format(
                job["status"], int(job.get("progress") or 0)))
        except Exception:
            pass

    try:
        job = client.videos.create_and_wait(
            prompt=prompt,
            engine=ENGINE,       # ⛔ đi đôi với DURATION - xem đầu file
            duration=DURATION,   # ⛔ veo3 CHI nhan 8; seedance CHI nhan 10
            aspect_ratio=ty_le,
            image_url=image_url,
            timeout=float(timeout),
            on_progress=_bao_tien_do,
        )
    except Exception as exc:
        _nem_neu_nghen(exc, nem_khi_nghen)
        return False, {}, "shopapi-vid: {0}".format(_sc.mo_ta_loi(exc))

    outputs = _sc.lay_outputs(job)
    url = _sc.url_cua_output(outputs[0]) if outputs else ""
    if not url:
        return False, {}, "shopapi-vid: job {0} bao xong nhung khong co file ket qua".format(
            _sc._lay_truong(job, "id"))

    try:
        # Link sống 7 ngày -> tải NGAY về đĩa, không lưu URL lại dùng tuần sau.
        _sc.tai_ve(url, str(out_path), timeout=float(timeout))
    except Exception as exc:
        return False, {}, "shopapi-vid: tai video ve dia that bai: {0}".format(
            _sc.mo_ta_loi(exc))

    try:
        so_byte = os.path.getsize(str(out_path))
    except OSError:
        so_byte = 0

    info = {
        "backend": "shopapi",
        "job_id": str(_sc._lay_truong(job, "id") or ""),
        "engine": ENGINE,
        "duration": DURATION,
        "aspect": ty_le,
        "bytes": so_byte,
        "cost": str(_sc._lay_truong(job, "cost") or ""),
        "has_image": bool(image_url),
    }
    return True, info, ""
