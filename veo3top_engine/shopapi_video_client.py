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
import re

try:                       # chạy trong tool: import cùng thư mục veo3top_engine
    import shopapi_common as _sc
    import shopapi_batch as _mb
except ImportError:        # chạy như một gói: import tương đối
    from . import shopapi_common as _sc  # type: ignore
    from . import shopapi_batch as _mb   # type: ignore

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


#: Mã file trong URL kho: `.../2026/08/11/upl_rh0kp0npms36fw99a7spkldj.png?X-Amz-...`
_RE_MA_UPLOAD = re.compile(r"/(upl_[A-Za-z0-9]+)")


def _ma_upload(url):
    """Moi mã `upl_...` ra khỏi URL kho. `None` nếu URL không phải của kho ta.

    `client.uploads.upload_file` chỉ trả về URL, không trả mã — mà muốn gọi
    `DELETE /v1/uploads/{id}` thì phải có mã. Đọc từ URL là đường duy nhất không
    phải chép lại ruột SDK (create -> _put -> confirm), và nó an toàn theo hướng
    đúng: không khớp thì trả `None` và ta chỉ đơn giản không dọn.

    Neo vào dấu `/` phía trước để không nhặt nhầm chuỗi `upl_` nằm trong tham số
    chữ ký của URL.
    """
    m = _RE_MA_UPLOAD.search(str(url or ""))
    return m.group(1) if m else None


def _don_upload(client, upload_id, log):
    """Xoá ảnh khung đầu khỏi kho sau khi video đã về đĩa.

    ═══ VÌ SAO PHẢI DỌN, VÀ VÌ SAO CHỈ DỌN LÚC ĐÃ XONG ═══

    Mỗi job video upload MỘT ảnh khung đầu. CONTRACT.md §6 đặt hai trần cho kho
    của một khách:

        số file còn sống : 200
        tổng dung lượng  : 500 MB

    File đã có job dùng sống **2 giờ kể từ lần dùng gần nhất**, và client trước
    nay không xoá cái nào. Đo ngày 11/08/2026: 456 video/giờ × 2 giờ = ~900 file
    còn sống trên trần 200. Chưa nổ vì mới chạy 41 phút, nhưng nó chắc chắn nổ,
    và nó nổ theo kiểu khó đoán nhất: job mới bị từ chối ở khâu upload, tức là
    hỏng ở một chỗ chẳng liên quan gì tới nội dung hay tới nhà máy.

    Contract nói thẳng đường chữa: *"Muốn giải phóng hạn mức sớm hơn thì gọi
    `DELETE /v1/uploads/{id}` — không bắt buộc, nhưng nhanh hơn ngồi chờ hết
    hạn."*

    CHỈ DỌN KHI VIDEO ĐÃ VỀ ĐĨA. Job hỏng thì `chay_ca_me` trả nó về hàng chờ và
    chạy lại — xoá ảnh lúc đó là biến một lần thử lại bình thường thành hỏng
    vĩnh viễn ("khong thay anh scene").

    KHÔNG BAO GIỜ làm hỏng job vì dọn không được. Video đã nằm trên đĩa và khách
    đã trả tiền; một lời gọi dọn trượt chỉ có nghĩa là file đó chờ hết 2 giờ như
    trước, đúng bằng hành vi cũ.

    ⚠ Ảnh THAM CHIẾU của đường tạo ẢNH thì KHÔNG dọn kiểu này: một ảnh nhân vật
    được dùng lại cho hàng chục scene, và hạn của nó trượt theo mỗi lần dùng.
    Xoá sau scene đầu là cắt chân những scene sau.
    """
    if not upload_id:
        return
    try:
        client.uploads.delete(upload_id)
    except Exception as exc:  # noqa: BLE001 — dọn trượt KHÔNG phải lỗi của job
        log("    [shopapi-vid] khong don duoc anh khung dau {0} ({1}) — no se tu het "
            "han sau 2 gio, khong anh huong video vua tai ve"
            .format(upload_id, _sc.mo_ta_loi(exc)), "WARN")


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
    #: Mã file vừa upload cho RIÊNG job này — để dọn sau khi video về đĩa.
    #: `None` khi khách tự đưa URL sẵn (không phải của ta, không được xoá).
    upload_id = None
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
            upload_id = _ma_upload(image_url)
    else:
        log("    [shopapi-vid] KHONG co anh scene -> chay che do chu-thanh-video", "WARN")

    ty_le = _sc.ty_le_api(aspect)

    def _bao_tien_do(job):
        try:
            log("    [shopapi-vid] {0} {1}%".format(
                job["status"], int(job.get("progress") or 0)))
        except Exception:
            pass

    def _bao_hang_cho(vi_tri, uoc_giay):
        """Máy chủ vừa nhận job -> nói ngay hàng dài bao nhiêu cho cổng của mẻ.

        Hai con số này (`queue_position`, `estimated_seconds`) đi kèm sẵn trong
        phản hồi `202`, không tốn thêm lời gọi nào. Không đọc chúng chính là lỗi
        đã làm hỏng 27 job ngày 07/08/2026 (66 job bắn vào nhà máy tiêu hoá ~16;
        33 job chết vì hết hạn NGAY TRONG HÀNG CHỜ, kho tài khoản còn nguyên).
        Gọi ngoài mẻ thì `bao_hang_cho` không làm gì cả.
        """
        _mb.bao_hang_cho(vi_tri, uoc_giay)
        if vi_tri is not None:
            log("    [shopapi-vid] may chu nhan job | dung thu {0} trong hang"
                "{1}".format(vi_tri,
                             ", uoc {0:.0f}s toi luot".format(uoc_giay)
                             if uoc_giay is not None else ""))
        if uoc_giay is not None and float(uoc_giay) > float(timeout):
            # Đúng cảnh 14 job "vuot qua thoi gian cho" hom 07/08 - noi TO.
            log("    [shopapi-vid] CANH BAO: may chu uoc {0:.0f}s moi toi luot nhung tool "
                "chi cho duoc {1:.0f}s -> job nay co the het han NGAY TRONG HANG CHO"
                .format(float(uoc_giay), float(timeout)), "WARN")

    try:
        job = _sc.tao_va_cho(
            client, "videos",
            timeout=float(timeout),
            on_progress=_bao_tien_do,
            on_hang_cho=_bao_hang_cho,
            prompt=prompt,
            engine=ENGINE,       # ⛔ đi đôi với DURATION - xem đầu file
            duration=DURATION,   # ⛔ veo3 CHI nhan 8; seedance CHI nhan 10
            aspect_ratio=ty_le,
            image_url=image_url,
        )
    except Exception as exc:
        # `429`/`resource_exhausted` -> ca me lui nhip, khong rieng luong nay.
        if _sc.phan_loai_nghen(exc) is not None:
            _mb.bao_nghen(getattr(exc, "retry_after", None))
        _nem_neu_nghen(exc, nem_khi_nghen)
        return False, {}, "shopapi-vid: {0}".format(_sc.mo_ta_loi(exc))

    outputs = _sc.lay_outputs(job)
    url = _sc.url_cua_output(outputs[0]) if outputs else ""
    if not url:
        return False, {}, "shopapi-vid: job {0} bao xong nhung khong co file ket qua".format(
            _sc._lay_truong(job, "id"))

    try:
        # ⚠ TẢI QUA `/download` — xem chú thích cùng chỗ ở `shopapi_image_client`.
        # Video nằm ở cuối một hàng chờ có lúc dài 90+ giây, nên nó dính hạn 6
        # giờ của `output.url` sớm hơn ảnh.
        _sc.tai_ket_qua(client, _sc._lay_truong(job, "id"), str(out_path),
                        timeout=float(timeout), url_du_phong=url, log=log)
    except Exception as exc:
        return False, {}, "shopapi-vid: tai video ve dia that bai: {0}".format(
            _sc.mo_ta_loi(exc))

    try:
        so_byte = os.path.getsize(str(out_path))
    except OSError:
        so_byte = 0

    # VIDEO ĐÃ VỀ ĐĨA -> ảnh khung đầu hết việc. Dọn ngay, xem `_don_upload`.
    _don_upload(client, upload_id, log)

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
