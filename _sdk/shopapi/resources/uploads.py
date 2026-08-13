"""`client.uploads` — đưa ảnh của bạn lên kho ShopAPI (`/v1/uploads`).

Ảnh tham chiếu (`reference_images` của job ảnh, `image_url` của job video) nhận
một **URL**, còn ảnh của bạn thì nằm **trên máy bạn**. Nhóm tài nguyên này là cây
cầu giữa hai chỗ đó — để bạn khỏi phải đi đăng ảnh lên một máy chủ người lạ chỉ
để dùng được API của chính mình.

Cần nhớ đúng một dòng:

```python
url = client.uploads.upload_file("anh.png")
job = client.images.create(prompt="...", reference_images=[url])
```

`upload_file` gói trọn ba bước của hợp đồng:

    1. `POST /v1/uploads`              → xin vé PUT ký sẵn
    2. `PUT <upload_url>`              → gửi byte thô THẲNG lên kho, không qua API
    3. `POST /v1/uploads/{id}/confirm` → máy chủ soi file thật rồi mới phát URL

Muốn tự điều khiển từng bước thì gọi `create` / `confirm` / `retrieve` / `delete`.

═══ GIỚI HẠN (máy chủ, không phải SDK bịa ra) ═══

* Chỉ nhận `image/png`, `image/jpeg`, `image/webp`. Máy chủ quyết định bằng
  **magic bytes** — đuôi file và `content_type` bạn khai chỉ là lời khai, đổi tên
  `.jpg` thành `.png` không biến JPEG thành PNG.
* Mỗi file tối đa **10 MB**.
* Mỗi khách giữ tối đa **200 file còn sống** và **500 MB** tổng.
* File sống **24 giờ**; file chưa job nào dùng tới bị dọn sau **6 giờ**; vé đã cấp
  mà không PUT gì bị dọn sau **1 giờ**.
* **MIỄN PHÍ** — tải ảnh lên không trừ một đồng nào. Nó là đầu vào của một job đã
  tính tiền, thu thêm ở đây là thu hai lần cho cùng một việc.

Lỗi hay gặp: `400 invalid_request` (`param` là `content_type` hoặc `size_bytes`)
và `404 not_found` (file đã hết hạn và bị dọn).
"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING, Any, Dict, Mapping, Optional, Tuple, Union

import httpx

from .._exceptions import (
    APIConnectionError,
    APITimeoutError,
    InvalidRequestError,
    ShopAPIError,
)
from .._models import Model

if TYPE_CHECKING:  # pragma: no cover
    from .._client import AsyncShopAPI, ShopAPI

__all__ = [
    "Uploads",
    "AsyncUploads",
    "MAX_UPLOAD_BYTES",
    "ALLOWED_UPLOAD_MIMES",
    "sniff_image_mime",
]

#: Trần dung lượng MỘT file — khớp `maxInputUploadBytes` của máy chủ
#: (`apps/api/src/modules/storage/storage.service.ts`).
#:
#: Kiểm ở phía client là để bạn biết ngay, chứ không phải để thay máy chủ: đẩy
#: 40 MB lên đường truyền rồi mới nhận `400` là mấy chục giây vứt đi.
MAX_UPLOAD_BYTES = 10 * 1024 * 1024

#: Ba định dạng ảnh tham chiếu được nhận — `ALLOWED_INPUT_IMAGE_MIMES` của máy chủ.
ALLOWED_UPLOAD_MIMES: Tuple[str, ...] = ("image/png", "image/jpeg", "image/webp")

#: Số byte đầu file cần đọc để nhận dạng định dạng.
#:
#: ⚠ Phải là 12, không phải 8. PNG cần 8 byte và JPEG cần 3, nhưng WebP là một
#: khối RIFF: 4 byte `RIFF`, 4 byte độ dài, rồi mới tới `WEBP` ở byte thứ 8..11.
#: Đọc 8 byte thì mọi ảnh WebP đều "không nhận dạng được".
MAGIC_PROBE_BYTES = 12

#: Đuôi file → MIME. **Chỉ dùng để viết thông báo lỗi cho dễ hiểu**, không bao giờ
#: dùng để quyết định `content_type` gửi lên (xem `_guess_content_type`).
_MIME_BY_EXT: Dict[str, str] = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".jpe": "image/jpeg",
    ".webp": "image/webp",
}

#: Thứ `upload_file` nhận: đường dẫn, đối tượng kiểu `pathlib.Path`, hoặc byte thô.
FileSource = Union[str, "os.PathLike[str]", bytes, bytearray]


# ── Nhận dạng định dạng ───────────────────────────────────────────────────────


def sniff_image_mime(head: bytes) -> Optional[str]:
    """Đoán MIME từ **magic bytes**, trả `None` khi không khớp định dạng nào.

    Chép đúng ba chữ ký mà máy chủ chấp nhận (`common/security/magic-bytes.ts`),
    nên câu trả lời của SDK và của máy chủ luôn giống nhau.
    """
    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if head.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    # RIFF....WEBP — 4 byte giữa là độ dài khối, bỏ qua.
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return "image/webp"
    return None


def _mb(value: int) -> str:
    return "{0:.1f}".format(value / 1024 / 1024)


def _assert_size(size_bytes: Any, label: Optional[str] = None) -> int:
    """Chặn file quá to / rỗng **trước** khi tốn một vòng mạng nào."""
    ten = ' "{0}"'.format(label) if label else ""
    if isinstance(size_bytes, bool) or not isinstance(size_bytes, int):
        raise InvalidRequestError(
            "`size_bytes` phải là số nguyên byte, bạn đang truyền {0}.".format(
                type(size_bytes).__name__
            ),
            status=400,
            code="invalid_request",
            param="size_bytes",
        )
    if size_bytes <= 0:
        raise InvalidRequestError(
            "File{0} rỗng (0 byte) nên không dùng làm ảnh tham chiếu được.".format(ten),
            status=400,
            code="invalid_request",
            param="size_bytes",
        )
    if size_bytes > MAX_UPLOAD_BYTES:
        raise InvalidRequestError(
            "File{0} nặng {1} MB, vượt trần {2} MB mỗi file. Bạn nén lại hoặc giảm kích "
            "thước ảnh rồi thử lại giúp mình — ảnh tham chiếu không cần độ phân giải cao, "
            "engine sẽ tự co lại.".format(ten, _mb(size_bytes), _mb(MAX_UPLOAD_BYTES)),
            status=400,
            code="invalid_request",
            param="size_bytes",
        )
    return size_bytes


def _guess_content_type(head: bytes, filename: Optional[str]) -> str:
    """Suy `content_type` từ NỘI DUNG file, không phải từ đuôi tên.

    Đuôi file sai là lỗi hay gặp nhất khi tải ảnh lên: người ta đổi tên `.jpg`
    thành `.png` cho gọn thư mục, hoặc lưu ảnh từ trình duyệt ra với đuôi tuỳ
    hứng. Máy chủ soi magic bytes rồi mới phát URL, nên tin vào đuôi file ở đây
    chỉ đẩy lỗi lùi lại tới bước xác nhận — sau khi đã đẩy xong cả file lên kho.
    """
    detected = sniff_image_mime(head)
    if detected is not None:
        return detected

    ext = os.path.splitext(filename or "")[1].lower()
    claimed = _MIME_BY_EXT.get(ext)
    if claimed is not None:
        raise InvalidRequestError(
            'Đuôi file là "{0}" nhưng nội dung bên trong không phải PNG, JPEG hay WebP. '
            "Máy chủ nhận dạng ảnh bằng magic bytes chứ không nhìn tên file, nên file này "
            "chắc chắn bị từ chối. Bạn mở lại file kiểm tra giúp mình — rất hay gặp trường "
            "hợp file thật ra là HTML báo lỗi tải về, hoặc file tải dở.".format(ext),
            status=400,
            code="invalid_request",
            param="content_type",
        )
    raise InvalidRequestError(
        "Không nhận dạng được định dạng ảnh. Ảnh tham chiếu chỉ nhận: {0}. Nếu bạn chắc "
        'chắn file hợp lệ, truyền thẳng `content_type="image/png"` để bỏ qua bước đoán '
        "này.".format(", ".join(ALLOWED_UPLOAD_MIMES)),
        status=400,
        code="invalid_request",
        param="content_type",
    )


def _validate_content_type(content_type: Any) -> str:
    if not isinstance(content_type, str) or not content_type.strip():
        raise InvalidRequestError(
            "`content_type` phải là chuỗi, ví dụ \"image/png\".",
            status=400,
            code="invalid_request",
            param="content_type",
        )
    # Máy chủ cũng cắt phần `; charset=...` và hạ chữ thường trước khi so.
    value = content_type.split(";")[0].strip().lower()
    if value not in ALLOWED_UPLOAD_MIMES:
        raise InvalidRequestError(
            'Định dạng "{0}" không được hỗ trợ. Ảnh tham chiếu nhận: {1}.'.format(
                value, ", ".join(ALLOWED_UPLOAD_MIMES)
            ),
            status=400,
            code="invalid_request",
            param="content_type",
        )
    return value


def _validate_upload_id(upload_id: Any) -> str:
    """Kiểm mã file.

    Để ngay tại đây thay vì `_validation.py`: nó chỉ phục vụ đúng module này, và
    `_validation.py` là tệp dùng chung mà mọi nhóm tài nguyên đều nhập vào.
    """
    if not isinstance(upload_id, str) or not upload_id.strip():
        raise InvalidRequestError(
            '`upload_id` phải là mã file, ví dụ "upl_x7k2m9p4qr8s". '
            "Bạn lấy nó từ `upload.id` khi gọi `client.uploads.create(...)`.",
            status=400,
            code="invalid_request",
            param="upload_id",
        )
    return upload_id.strip()


# ── Đọc nguồn dữ liệu ─────────────────────────────────────────────────────────


def _prepare(
    file: FileSource,
    filename: Optional[str],
    content_type: Optional[str],
) -> Tuple[bytes, Optional[str], str]:
    """Đọc file (hoặc byte thô) → `(data, filename, content_type)`.

    Kích thước được kiểm **trước khi đọc** (bằng `os.path.getsize`), nên một file
    3 GB gõ nhầm cũng không bao giờ bị nạp vào RAM.
    """
    if isinstance(file, (bytes, bytearray)):
        data = bytes(file)
        _assert_size(len(data), filename)
    else:
        path = os.fspath(file)
        if not os.path.isfile(path):
            raise InvalidRequestError(
                'Không tìm thấy file "{0}". Bạn kiểm tra lại đường dẫn giúp mình.'.format(path),
                status=400,
                code="invalid_request",
                param="file",
            )
        # Hỏi kích thước TRƯỚC, đọc SAU.
        _assert_size(os.path.getsize(path), os.path.basename(path))
        with open(path, "rb") as handle:
            data = handle.read()
        # Đọc xong đo lại: file có thể vừa bị ghi đè giữa hai lệnh, mà `size_bytes`
        # khai sai một byte là chữ ký PUT không khớp.
        _assert_size(len(data), os.path.basename(path))
        if filename is None:
            filename = os.path.basename(path)

    resolved_type = (
        _validate_content_type(content_type)
        if content_type is not None
        else _guess_content_type(data[:MAGIC_PROBE_BYTES], filename)
    )
    return data, filename, resolved_type


# ── Bước 2: PUT thẳng lên kho ─────────────────────────────────────────────────


def _storage_headers(required_headers: Any) -> Dict[str, str]:
    """Lấy ĐÚNG các header máy chủ yêu cầu, không thêm thứ gì.

    ⚠⚠ TUYỆT ĐỐI KHÔNG kèm `Authorization` của ShopAPI vào request này.

    `upload_url` trỏ tới **kho lưu trữ**, một host khác hẳn `api.shopapi.vn`. Gửi
    `Bearer sk_live_...` sang đó là đưa khoá API của khách cho một bên thứ ba,
    ghi vào log truy cập của họ, vĩnh viễn. Kho không cần khoá đó: quyền ghi nằm
    trong chữ ký ở query string của chính URL, hết hạn sau 15 phút.

    Đây cũng là lý do bước PUT dùng một `httpx.Client` riêng chứ không mượn
    `client._http`: httpx trộn header mặc định của client vào mọi request, nên
    nếu ai đó truyền `http_client=` có sẵn `Authorization` (hoàn toàn hợp lệ với
    httpx), khoá sẽ rò ra mà không ai thấy. Một client rời thì không có gì để rò.
    """
    # `Model` cũng là một `Mapping` nên nhánh này nhận cả dict lẫn Model.
    if not isinstance(required_headers, Mapping):
        return {}
    return {str(key): str(value) for key, value in required_headers.items()}


def _storage_failed(response: httpx.Response) -> ShopAPIError:
    detail = ""
    try:
        text = (response.text or "").strip()
        if text:
            detail = " Kho trả lời: " + text[:300]
    except Exception:  # pragma: no cover — thân phản hồi hỏng
        detail = ""
    return ShopAPIError(
        "Kho lưu trữ không nhận file (HTTP {0}). Vé tải lên chỉ sống 15 phút và đã ghim "
        "sẵn `Content-Type` cùng `Content-Length` bạn khai lúc xin vé — lệch một trong hai "
        "là chữ ký không khớp. Bạn gọi lại `upload_file(...)` từ đầu giúp mình.{1}".format(
            response.status_code, detail
        )
    )


def _storage_timeout(exc: BaseException) -> APITimeoutError:
    return APITimeoutError(
        "Hết giờ khi đang đẩy file lên kho lưu trữ. File lớn trên mạng chậm thì bạn tăng "
        "`timeout` lúc khởi tạo client, ví dụ `ShopAPI(timeout=300)`.",
        cause=exc,
    )


def _storage_connection_error(exc: BaseException) -> APIConnectionError:
    return APIConnectionError(
        "Không nối được tới kho lưu trữ để đẩy file lên. Bạn kiểm tra mạng, tường lửa hoặc "
        "proxy rồi thử lại giúp mình.",
        cause=exc,
    )


class Uploads:
    """Bản đồng bộ."""

    def __init__(self, client: "ShopAPI") -> None:
        self._client = client

    # ── Tiện lợi: một lời gọi, xong ──────────────────────────────────────────

    def upload_file(
        self,
        file: FileSource,
        *,
        filename: Optional[str] = None,
        content_type: Optional[str] = None,
    ) -> str:
        """Tải một ảnh lên và trả về **URL** dùng được ngay.

        ```python
        url = client.uploads.upload_file("anh.png")
        job = client.images.create_and_wait(
            prompt="giữ nguyên bố cục, đổi nền thành bãi biển",
            reference_images=[url],
        )
        ```

        Nhận đường dẫn (`str`), đối tượng `pathlib.Path`, hoặc byte thô — byte thô
        thì nên kèm `filename` để máy chủ đặt tên tải về cho đẹp:

        ```python
        url = client.uploads.upload_file(anh_bytes, filename="san-pham.jpg")
        ```

        `content_type` để trống thì SDK tự đoán bằng **magic bytes** của chính
        file. Truyền tay chỉ khi bạn chắc chắn hơn SDK.

        Miễn phí, và URL trả về sống 24 giờ.
        """
        data, resolved_filename, resolved_type = _prepare(file, filename, content_type)

        ticket = self.create(
            content_type=resolved_type,
            size_bytes=len(data),
            filename=resolved_filename,
        )
        self._put(ticket, data)
        ready = self.confirm(ticket["id"])

        url = ready.get("url")
        if not url:
            raise ShopAPIError(
                "Máy chủ đã nhận file {0} nhưng chưa phát URL (trạng thái: {1}). Bạn thử gọi "
                "`client.uploads.confirm(\"{0}\")` lại giúp mình.".format(
                    ticket["id"], ready.get("status")
                )
            )
        return str(url)

    def _put(self, ticket: Model, data: bytes) -> None:
        """Bước 2 — đẩy byte thô THẲNG lên kho, không một byte nào qua API.

        Xem `_storage_headers` để hiểu vì sao ở đây không có `Authorization` và vì
        sao phải dùng một `httpx.Client` riêng.
        """
        headers = _storage_headers(ticket.get("required_headers"))
        # `follow_redirects=False`: chuyển hướng ở bước này nghĩa là gửi lại toàn bộ
        # thân file (và cả chữ ký) sang một host ta chưa hề định gửi tới.
        try:
            with httpx.Client(
                timeout=httpx.Timeout(self._client.timeout), follow_redirects=False
            ) as storage:
                response = storage.request(
                    "PUT", str(ticket["upload_url"]), content=data, headers=headers
                )
        except httpx.TimeoutException as exc:
            raise _storage_timeout(exc) from exc
        except httpx.HTTPError as exc:
            raise _storage_connection_error(exc) from exc

        if response.status_code >= 400:
            raise _storage_failed(response)

    # ── Ba bước rời ──────────────────────────────────────────────────────────

    def create(
        self,
        *,
        content_type: str,
        size_bytes: int,
        filename: Optional[str] = None,
        idempotency_key: Optional[str] = None,
        extra_body: Optional[Mapping[str, Any]] = None,
    ) -> Model:
        """`POST /v1/uploads` — xin một vé PUT ký sẵn.

        Trả về `upload_url`, `required_headers` và `upload_url_expires_in` (giây).
        Vé chưa dùng bị dọn sau 1 giờ.

        Hầu hết mọi người nên dùng `upload_file(...)` thay cho ba bước rời này.
        """
        body: Dict[str, Any] = {
            "content_type": _validate_content_type(content_type),
            "size_bytes": _assert_size(size_bytes, filename),
        }
        if filename is not None:
            body["filename"] = str(filename)
        if extra_body:
            body.update(extra_body)
        return self._client.request(
            "POST",
            "/v1/uploads",
            json=body,
            idempotent=True,
            idempotency_key=idempotency_key,
        )

    def confirm(self, upload_id: str) -> Model:
        """`POST /v1/uploads/{id}/confirm` — máy chủ đo file thật rồi phát `url`.

        Đây là nơi magic bytes được soi. Bỏ qua bước này thì không có URL nào để
        đưa vào `reference_images`. Gọi lại lần hai không hỏng gì.
        """
        upload_id = _validate_upload_id(upload_id)
        return self._client.request("POST", "/v1/uploads/" + upload_id + "/confirm")

    def retrieve(self, upload_id: str) -> Model:
        """`GET /v1/uploads/{id}` — `url` là `None` khi trạng thái còn `pending`."""
        upload_id = _validate_upload_id(upload_id)
        return self._client.request("GET", "/v1/uploads/" + upload_id)

    def delete(self, upload_id: str) -> Model:
        """`DELETE /v1/uploads/{id}` — xoá sớm để lấy lại hạn mức 200 file / 500 MB.

        Không bắt buộc: file tự hết hạn sau 24 giờ.
        """
        upload_id = _validate_upload_id(upload_id)
        return self._client.request("DELETE", "/v1/uploads/" + upload_id)


class AsyncUploads:
    """Bản bất đồng bộ — cùng bề mặt, chỉ thêm `await`."""

    def __init__(self, client: "AsyncShopAPI") -> None:
        self._client = client

    # ── Tiện lợi: một lời gọi, xong ──────────────────────────────────────────

    async def upload_file(
        self,
        file: FileSource,
        *,
        filename: Optional[str] = None,
        content_type: Optional[str] = None,
    ) -> str:
        """Tải một ảnh lên và trả về **URL** dùng được ngay.

        ```python
        url = await client.uploads.upload_file("anh.png")
        ```
        """
        data, resolved_filename, resolved_type = _prepare(file, filename, content_type)

        ticket = await self.create(
            content_type=resolved_type,
            size_bytes=len(data),
            filename=resolved_filename,
        )
        await self._put(ticket, data)
        ready = await self.confirm(ticket["id"])

        url = ready.get("url")
        if not url:
            raise ShopAPIError(
                "Máy chủ đã nhận file {0} nhưng chưa phát URL (trạng thái: {1}). Bạn thử gọi "
                "`client.uploads.confirm(\"{0}\")` lại giúp mình.".format(
                    ticket["id"], ready.get("status")
                )
            )
        return str(url)

    async def _put(self, ticket: Model, data: bytes) -> None:
        """Bước 2 — đẩy byte thô THẲNG lên kho.

        ⚠ Không kèm `Authorization`, và dùng `httpx.AsyncClient` riêng — lý do đầy
        đủ ở `_storage_headers`.
        """
        headers = _storage_headers(ticket.get("required_headers"))
        try:
            async with httpx.AsyncClient(
                timeout=httpx.Timeout(self._client.timeout), follow_redirects=False
            ) as storage:
                response = await storage.request(
                    "PUT", str(ticket["upload_url"]), content=data, headers=headers
                )
        except httpx.TimeoutException as exc:
            raise _storage_timeout(exc) from exc
        except httpx.HTTPError as exc:
            raise _storage_connection_error(exc) from exc

        if response.status_code >= 400:
            raise _storage_failed(response)

    # ── Ba bước rời ──────────────────────────────────────────────────────────

    async def create(
        self,
        *,
        content_type: str,
        size_bytes: int,
        filename: Optional[str] = None,
        idempotency_key: Optional[str] = None,
        extra_body: Optional[Mapping[str, Any]] = None,
    ) -> Model:
        """`POST /v1/uploads` — xin một vé PUT ký sẵn."""
        body: Dict[str, Any] = {
            "content_type": _validate_content_type(content_type),
            "size_bytes": _assert_size(size_bytes, filename),
        }
        if filename is not None:
            body["filename"] = str(filename)
        if extra_body:
            body.update(extra_body)
        return await self._client.request(
            "POST",
            "/v1/uploads",
            json=body,
            idempotent=True,
            idempotency_key=idempotency_key,
        )

    async def confirm(self, upload_id: str) -> Model:
        """`POST /v1/uploads/{id}/confirm` — máy chủ đo file thật rồi phát `url`."""
        upload_id = _validate_upload_id(upload_id)
        return await self._client.request("POST", "/v1/uploads/" + upload_id + "/confirm")

    async def retrieve(self, upload_id: str) -> Model:
        """`GET /v1/uploads/{id}` — `url` là `None` khi trạng thái còn `pending`."""
        upload_id = _validate_upload_id(upload_id)
        return await self._client.request("GET", "/v1/uploads/" + upload_id)

    async def delete(self, upload_id: str) -> Model:
        """`DELETE /v1/uploads/{id}` — xoá sớm để lấy lại hạn mức."""
        upload_id = _validate_upload_id(upload_id)
        return await self._client.request("DELETE", "/v1/uploads/" + upload_id)
