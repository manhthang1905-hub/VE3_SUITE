"""Cây ngoại lệ của SDK — SDK_SPEC §4, CONTRACT.md §3.

```
ShopAPIError
├── APIConnectionError
│   └── APITimeoutError
├── APIStatusError
│   ├── InvalidRequestError          400  invalid_request
│   ├── AuthenticationError          401  invalid_api_key
│   ├── InsufficientBalanceError     402  insufficient_balance
│   ├── ContentRejectedError         403  content_rejected
│   ├── PermissionDeniedError        403  permission_denied
│   ├── AccountSuspendedError        403  account_suspended
│   ├── NotFoundError                404  not_found
│   ├── ConflictError                409  conflict
│   ├── IdempotencyConflictError     409  idempotency_conflict
│   ├── UnsupportedParameterError    422  unsupported_parameter
│   ├── RateLimitError               429  rate_limit_exceeded
│   ├── InternalServerError          500  internal_error
│   ├── EngineUnavailableError       503  engine_unavailable
│   └── ServiceUnavailableError      503  service_unavailable
├── JobFailedError
├── JobTimeoutError
└── SignatureVerificationError
```

Mọi thông điệp viết bằng tiếng Việt và **nói cho khách biết phải làm gì tiếp
theo** — chép đúng tinh thần `ERROR_COPY_VI` trong `contracts/src/errors.ts`.
"""

from __future__ import annotations

from typing import Any, Dict, Mapping, Optional

from ._constants import BILLING_URL
from ._money import format_vnd, sub_micro

__all__ = [
    "ShopAPIError",
    "APIConnectionError",
    "APITimeoutError",
    "APIStatusError",
    "InvalidRequestError",
    "AuthenticationError",
    "InsufficientBalanceError",
    "ContentRejectedError",
    "PermissionDeniedError",
    "AccountSuspendedError",
    "NotFoundError",
    "ConflictError",
    "IdempotencyConflictError",
    "UnsupportedParameterError",
    "RateLimitError",
    "EngineUnavailableError",
    "ServiceUnavailableError",
    "InternalServerError",
    "JobFailedError",
    "JobTimeoutError",
    "SignatureVerificationError",
    "ERROR_COPY_VI",
    "JOB_ERROR_COPY_VI",
    "RETRYABLE_STATUS_CODES",
    "MISSING_API_KEY_MESSAGE",
]

# ── Nội dung tiếng Việt cho từng mã lỗi (contracts/src/errors.ts) ─────────────

ERROR_COPY_VI: Dict[str, Dict[str, Any]] = {
    "invalid_request": {
        "title": "Yêu cầu chưa hợp lệ",
        "description": "Có tham số bị thiếu hoặc sai định dạng nên chúng tôi chưa xử lý được.",
        "action": "Bạn kiểm tra lại các ô đã nhập, hoặc xem mục tương ứng trong tài liệu API.",
        "retryable": False,
    },
    "invalid_api_key": {
        "title": "API key không dùng được",
        "description": "Key này sai, đã bị thu hồi, hoặc bạn dán thiếu một phần.",
        "action": "Bạn vào mục API key trong bảng điều khiển để tạo key mới rồi thay vào code.",
        "retryable": False,
    },
    "insufficient_balance": {
        "title": "Số dư không đủ",
        "description": "Tài khoản của bạn chưa đủ tiền để chạy yêu cầu này. Chúng tôi chưa trừ đồng nào.",
        "action": "Bạn nạp thêm tiền bằng mã QR ở mục Nạp tiền, tiền vào ví trong khoảng 10 giây.",
        "retryable": True,
    },
    "content_rejected": {
        "title": "Nội dung không được phép",
        "description": (
            "Nội dung bạn gửi vi phạm quy định sử dụng nên chúng tôi phải từ chối. "
            "Tiền đã được hoàn lại đầy đủ."
        ),
        "action": "Bạn sửa lại nội dung rồi gửi lại giúp mình.",
        "retryable": False,
    },
    "permission_denied": {
        "title": "Khoá API không đủ quyền",
        "description": (
            "Khoá này bị giới hạn phạm vi, dải IP hoặc hạn mức tháng nên không dùng được cho "
            "thao tác vừa rồi. Ví của bạn KHÔNG bị trừ tiền."
        ),
        "action": (
            "Bạn xem thuộc tính `reason` của lỗi để biết vướng ở đâu, rồi chỉnh khoá trong "
            "Bảng điều khiển → API keys. Nếu bạn không nhận ra địa chỉ IP hoặc mức chi tiêu đó, "
            "hãy thu hồi khoá ngay — nhiều khả năng khoá đã bị lộ."
        ),
        "retryable": False,
    },
    "account_suspended": {
        "title": "Tài khoản đang tạm khoá",
        "description": "Tài khoản của bạn đang tạm khoá nên không tạo được yêu cầu mới. Ví không bị trừ tiền.",
        "action": "Bạn liên hệ bộ phận hỗ trợ để được mở lại giúp mình.",
        "retryable": False,
    },
    "not_found": {
        "title": "Không tìm thấy",
        "description": "Mục bạn đang tìm không tồn tại hoặc không thuộc tài khoản này.",
        "action": "Bạn kiểm tra lại đường dẫn, hoặc quay về danh sách để chọn lại.",
        "retryable": False,
    },
    "conflict": {
        "title": "Trạng thái không cho phép thao tác này",
        "description": (
            "Yêu cầu xung đột với trạng thái hiện tại của dữ liệu, ví dụ huỷ một job đã chạy xong."
        ),
        "action": "Bạn đọc lại trạng thái mới nhất rồi quyết định bước tiếp theo.",
        "retryable": False,
    },
    "idempotency_conflict": {
        "title": "Trùng mã chống lặp",
        "description": (
            "Bạn dùng lại một Idempotency-Key cũ nhưng nội dung gửi lên đã khác. "
            "Chúng tôi không xử lý để tránh tạo trùng job."
        ),
        "action": "Bạn đổi sang một Idempotency-Key mới (khuyến nghị dùng uuid) rồi gửi lại.",
        "retryable": False,
    },
    "unsupported_parameter": {
        "title": "Tham số chưa được hỗ trợ",
        "description": "Giá trị bạn chọn không nằm trong danh sách engine này chấp nhận.",
        # Nêu ĐÚNG các mức đang bán: nói "Seedance nhận 5 hoặc 10 giây" trong khi
        # máy chủ chỉ nhận 10 là vừa từ chối số 5 vừa mời người ta chọn số 5.
        "action": "Ví dụ Veo3 chỉ nhận video 8 giây, Seedance chỉ nhận 10 giây. Bạn chọn lại giúp mình.",
        "retryable": False,
    },
    "rate_limit_exceeded": {
        "title": "Bạn gửi hơi nhanh",
        "description": "Số yêu cầu vượt quá giới hạn của hạng tài khoản hiện tại.",
        "action": "Bạn chờ vài giây rồi thử lại. Cần chạy nhiều hơn thì nâng hạng ở mục Bảng giá.",
        "retryable": True,
    },
    "engine_unavailable": {
        "title": "Hệ thống đang quá tải",
        "description": "Cụm xử lý tạm thời bận. Bạn không bị trừ tiền — toàn bộ đã được hoàn lại.",
        "action": 'Bạn thử lại sau khoảng một phút, hoặc để engine ở chế độ "auto" để hệ thống tự chọn máy rảnh.',
        "retryable": True,
    },
    "service_unavailable": {
        "title": "Dịch vụ tạm thời không sẵn sàng",
        "description": "Một thành phần phía chúng tôi đang bảo trì hoặc quá tải. Bạn không bị trừ tiền.",
        "action": "Bạn thử lại sau ít phút. SDK cũng đã tự thử lại giúp bạn vài lần.",
        "retryable": True,
    },
    "internal_error": {
        "title": "Lỗi từ phía chúng tôi",
        "description": "Có sự cố bên hệ thống. Nếu đã tạm giữ tiền thì sẽ được hoàn lại tự động.",
        "action": "Bạn thử lại giúp mình. Nếu vẫn lỗi, gửi mã request_id cho hỗ trợ để tra cứu nhanh.",
        "retryable": True,
    },
}

#: Mã lỗi cấp job (nằm trong `job.error.code`) — rộng hơn mã HTTP.
JOB_ERROR_COPY_VI: Dict[str, Dict[str, Any]] = {
    "engine_error": {
        "title": "Máy xử lý gặp lỗi",
        "description": "Engine chạy nhưng không ra kết quả hợp lệ.",
        "action": "Bạn tạo lại job — hệ thống sẽ tự chọn một máy khác.",
        "retryable": True,
    },
    "engine_unavailable": {
        "title": "Không còn máy rảnh",
        "description": "Cả cụm engine đang bận hoặc tạm nghỉ.",
        "action": 'Bạn thử lại sau ít phút, hoặc để engine ở chế độ "auto".',
        "retryable": True,
    },
    "content_rejected": {
        "title": "Nội dung bị từ chối",
        "description": "Nội dung vi phạm quy định nên job bị dừng.",
        "action": "Bạn sửa lại nội dung rồi tạo job mới.",
        "retryable": False,
    },
    "timeout": {
        "title": "Quá thời gian chờ",
        "description": "Job chạy lâu hơn mức cho phép nên bị dừng.",
        "action": "Với văn bản dài, bạn thử chia thành nhiều job nhỏ hơn.",
        "retryable": True,
    },
    "download_failed": {
        "title": "Không tải được kết quả",
        "description": "Engine có ra file nhưng hệ thống không lấy về được.",
        "action": "Bạn tạo lại job giúp mình.",
        "retryable": True,
    },
    "upload_failed": {
        "title": "Không lưu được kết quả",
        "description": "File tạo xong nhưng lưu trữ gặp sự cố.",
        "action": "Bạn tạo lại job giúp mình.",
        "retryable": True,
    },
    "account_exhausted": {
        "title": "Hết lượt trong ngày",
        "description": "Tài nguyên cho loại job này đã dùng hết hạn mức hôm nay.",
        "action": "Bạn thử lại sau 00:00, hoặc đổi sang engine khác.",
        "retryable": True,
    },
    "cancelled_by_user": {
        "title": "Bạn đã huỷ job này",
        "description": "Job dừng theo yêu cầu của bạn.",
        "action": "Bạn có thể tạo lại job bất cứ lúc nào.",
        "retryable": True,
    },
    "internal_error": {
        "title": "Lỗi hệ thống",
        "description": "Sự cố bên phía chúng tôi.",
        "action": "Bạn thử lại, nếu vẫn lỗi thì gửi mã job cho hỗ trợ.",
        "retryable": True,
    },
}

#: Mã HTTP được phép thử lại — SDK_SPEC §5.
RETRYABLE_STATUS_CODES = frozenset({408, 429, 500, 502, 503, 504})

MISSING_API_KEY_MESSAGE = (
    "Thiếu API key. Bạn truyền `ShopAPI(api_key=\"sk_live_...\")` hoặc đặt biến môi trường "
    "SHOPAPI_KEY (dự phòng SHOPAPI_API_KEY). "
    "Lấy key ở https://shopapi.vn/dashboard/keys — key chỉ hiện đúng một lần lúc tạo. "
    "Nếu bạn chỉ nhận webhook thì KHÔNG cần API key: việc kiểm tra chữ ký là phép tính "
    "cục bộ, chỉ cần webhook secret (SHOPAPI_WEBHOOK_SECRET)."
)


def _default_message(code: str) -> str:
    copy = ERROR_COPY_VI.get(code) or ERROR_COPY_VI["internal_error"]
    return "{0} {1}".format(copy["description"], copy["action"])


# ── Cây ngoại lệ ──────────────────────────────────────────────────────────────


class ShopAPIError(Exception):
    """Gốc của mọi lỗi ShopAPI. `.message` luôn là tiếng Việt."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message: str = message

    def __str__(self) -> str:
        return self.message


class APIConnectionError(ShopAPIError):
    """Không nối được tới máy chủ ShopAPI."""

    def __init__(self, message: Optional[str] = None, *, cause: Optional[BaseException] = None) -> None:
        super().__init__(
            message
            or (
                "Không kết nối được tới máy chủ ShopAPI. Bạn kiểm tra lại mạng, tường lửa "
                "hoặc proxy rồi thử lại giúp mình."
            )
        )
        #: Lỗi gốc của httpx, giữ lại để gỡ rối.
        self.cause: Optional[BaseException] = cause
        #: Lỗi mạng luôn thử lại được.
        self.retryable: bool = True


class APITimeoutError(APIConnectionError):
    """Máy chủ không trả lời kịp trong `timeout`."""

    def __init__(self, message: Optional[str] = None, *, cause: Optional[BaseException] = None) -> None:
        super().__init__(
            message
            or (
                "Máy chủ ShopAPI không trả lời kịp. Bạn thử lại, hoặc tăng `timeout` khi khởi tạo "
                "client nếu mạng của bạn chậm."
            ),
            cause=cause,
        )


class APIStatusError(ShopAPIError):
    """Máy chủ trả về mã lỗi HTTP (4xx / 5xx)."""

    #: Mã HTTP mặc định của lớp con.
    default_status: int = 500
    #: `error.code` mặc định của lớp con.
    default_code: str = "internal_error"
    #: `error.type` mặc định của lớp con.
    default_type: str = "api_error"

    def __init__(
        self,
        message: Optional[str] = None,
        *,
        status: Optional[int] = None,
        code: Optional[str] = None,
        type: Optional[str] = None,  # noqa: A002 — trùng tên trường của API
        param: Optional[str] = None,
        request_id: Optional[str] = None,
        headers: Optional[Mapping[str, str]] = None,
        body: Optional[Any] = None,
    ) -> None:
        self.status: int = status if status is not None else self.default_status
        self.code: str = code or self.default_code
        self.type: str = type or self.default_type
        self.param: Optional[str] = param
        self.request_id: Optional[str] = request_id
        self.headers: Dict[str, str] = dict(headers or {})
        self.body: Optional[Any] = body
        #: Theo bảng §5 của SDK_SPEC — KHÔNG theo `retryable` trong copy hiển thị.
        self.retryable: bool = self.status in RETRYABLE_STATUS_CODES
        super().__init__(message or _default_message(self.code))

    def __repr__(self) -> str:
        return "{0}(status={1}, code={2!r}, request_id={3!r})".format(
            type(self).__name__, self.status, self.code, self.request_id
        )


class InvalidRequestError(APIStatusError):
    """400 — thiếu tham số hoặc sai định dạng. Cũng dùng cho kiểm tra phía client."""

    default_status = 400
    default_code = "invalid_request"
    default_type = "invalid_request_error"


class AuthenticationError(APIStatusError):
    """401 — API key sai, đã bị thu hồi, hoặc **đã hết hạn**.

    Khoá có đặt `expires_in_days` sẽ hết hạn đúng hẹn. Khi đó máy chủ gửi kèm
    `.expired_at`, và nếu bạn vừa xoay khoá thì `.replaced_by` cho biết mã khoá
    thay thế để bạn đổi sang.
    """

    default_status = 401
    default_code = "invalid_api_key"
    default_type = "authentication_error"

    def __init__(
        self,
        message: Optional[str] = None,
        *,
        expired_at: Optional[str] = None,
        replaced_by: Optional[str] = None,
        **kwargs: Any,
    ) -> None:
        #: Thời điểm khoá hết hạn (ISO 8601), nếu lỗi là do hết hạn.
        self.expired_at: Optional[str] = expired_at
        #: Mã khoá thay thế do lần xoay khoá gần nhất sinh ra.
        self.replaced_by: Optional[str] = replaced_by
        super().__init__(message, **kwargs)

    @property
    def expired(self) -> bool:
        """Khoá hết hạn (khác với khoá sai hoặc bị thu hồi)."""
        return self.expired_at is not None


class InsufficientBalanceError(APIStatusError):
    """402 — không đủ số dư. Kèm `.required`, `.available`, `.shortfall` (µVND)."""

    default_status = 402
    default_code = "insufficient_balance"
    default_type = "billing_error"

    def __init__(
        self,
        message: Optional[str] = None,
        *,
        required: Optional[str] = None,
        available: Optional[str] = None,
        **kwargs: Any,
    ) -> None:
        #: µVND cần có để chạy yêu cầu này.
        self.required: Optional[str] = required
        #: µVND hiện có trong ví.
        self.available: Optional[str] = available
        #: µVND còn thiếu = required - available (không âm).
        self.shortfall: Optional[str] = None
        if required is not None and available is not None:
            diff = sub_micro(required, available)
            self.shortfall = diff if not diff.startswith("-") else "0"
            message = (
                "Số dư không đủ. Cần {0}, hiện có {1} — thiếu {2}. Bạn nạp thêm ở {3}".format(
                    format_vnd(required), format_vnd(available), format_vnd(self.shortfall), BILLING_URL
                )
            )
        super().__init__(message, **kwargs)


class ContentRejectedError(APIStatusError):
    """403 — nội dung vi phạm quy định. Tiền đã hoàn lại đầy đủ."""

    default_status = 403
    default_code = "content_rejected"
    default_type = "content_policy_error"


class PermissionDeniedError(APIStatusError):
    """403 — khoá API không đủ quyền cho thao tác này. **Ví không bị trừ tiền.**

    Ba nguyên nhân rất khác nhau dùng chung một mã, nên đọc `.reason` để phân biệt:

    * `"scope"` — khoá thiếu phạm vi: xem `.required_scope` và `.granted_scopes`
    * `"ip"` — gọi từ IP ngoài danh sách: xem `.client_ip` và `.allowed`
    * `"budget"` — chạm hạn mức tháng của khoá: xem `.key_monthly_limit`
      và `.key_spent_this_month`
    * `"unknown"` — máy chủ không gửi kèm trường phụ nào

    Nếu bạn không nhận ra địa chỉ IP hay mức chi tiêu đó thì **thu hồi khoá ngay** —
    nhiều khả năng khoá đã bị lộ.
    """

    default_status = 403
    default_code = "permission_denied"
    default_type = "permission_error"

    def __init__(
        self,
        message: Optional[str] = None,
        *,
        required_scope: Optional[str] = None,
        granted_scopes: Optional[Any] = None,
        client_ip: Optional[str] = None,
        allowed: Optional[Any] = None,
        key_monthly_limit: Optional[str] = None,
        key_spent_this_month: Optional[str] = None,
        **kwargs: Any,
    ) -> None:
        #: Phạm vi mà thao tác này đòi hỏi (khi `reason == "scope"`).
        self.required_scope: Optional[str] = required_scope
        #: Danh sách phạm vi khoá đang có.
        self.granted_scopes: Optional[Any] = granted_scopes
        #: Địa chỉ IP mà yêu cầu vừa rồi đi ra (khi `reason == "ip"`).
        self.client_ip: Optional[str] = client_ip
        #: Danh sách dải IP được phép.
        self.allowed: Optional[Any] = allowed
        #: Hạn mức tháng của riêng khoá này, µVND (khi `reason == "budget"`).
        self.key_monthly_limit: Optional[str] = key_monthly_limit
        #: Số µVND khoá này đã tiêu trong tháng.
        self.key_spent_this_month: Optional[str] = key_spent_this_month
        super().__init__(message, **kwargs)

    @property
    def reason(self) -> str:
        """Vì sao bị chặn: `"scope"`, `"ip"`, `"budget"` hoặc `"unknown"`."""
        if self.required_scope is not None or self.granted_scopes is not None:
            return "scope"
        if self.client_ip is not None or self.allowed is not None:
            return "ip"
        if self.key_monthly_limit is not None or self.key_spent_this_month is not None:
            return "budget"
        return "unknown"


class AccountSuspendedError(APIStatusError):
    """403 — tài khoản đang tạm khoá. Ví không bị trừ tiền; liên hệ hỗ trợ để mở lại."""

    default_status = 403
    default_code = "account_suspended"
    default_type = "permission_error"


class NotFoundError(APIStatusError):
    """404 — không tìm thấy."""

    default_status = 404
    default_code = "not_found"
    default_type = "invalid_request_error"


class ConflictError(APIStatusError):
    """409 — yêu cầu xung đột với trạng thái hiện tại (ví dụ huỷ job đã xong).

    Khác `IdempotencyConflictError`: lỗi đó nói về `Idempotency-Key`, lỗi này nói
    về trạng thái của chính dữ liệu.
    """

    default_status = 409
    default_code = "conflict"
    default_type = "invalid_request_error"


class IdempotencyConflictError(APIStatusError):
    """409 — cùng Idempotency-Key nhưng nội dung gửi lên đã khác."""

    default_status = 409
    default_code = "idempotency_conflict"
    default_type = "idempotency_error"


class UnsupportedParameterError(APIStatusError):
    """422 — giá trị tham số không được engine hỗ trợ."""

    default_status = 422
    default_code = "unsupported_parameter"
    default_type = "invalid_request_error"


class RateLimitError(APIStatusError):
    """429 — vượt giới hạn tần suất. `.retry_after` là số giây nên chờ."""

    default_status = 429
    default_code = "rate_limit_exceeded"
    default_type = "rate_limit_error"

    def __init__(
        self, message: Optional[str] = None, *, retry_after: Optional[float] = None, **kwargs: Any
    ) -> None:
        #: Số giây nên chờ trước khi thử lại, đọc từ header `Retry-After`.
        self.retry_after: Optional[float] = retry_after
        super().__init__(message, **kwargs)


class EngineUnavailableError(APIStatusError):
    """503 — cả cụm engine đang bận. Tiền đã được hoàn lại."""

    default_status = 503
    default_code = "engine_unavailable"
    default_type = "api_error"


class ServiceUnavailableError(APIStatusError):
    """503 — một thành phần của hệ thống đang bảo trì hoặc quá tải.

    Khác `EngineUnavailableError`: lỗi đó là cụm engine sinh nội dung bận, lỗi này
    là hạ tầng chung. Cả hai đều đáng thử lại và đều không bị trừ tiền.
    """

    default_status = 503
    default_code = "service_unavailable"
    default_type = "api_error"


class InternalServerError(APIStatusError):
    """500 — lỗi phía ShopAPI. Gửi `request_id` cho hỗ trợ để tra cứu."""

    default_status = 500
    default_code = "internal_error"
    default_type = "api_error"


class JobFailedError(ShopAPIError):
    """Job kết thúc ở `failed` / `cancelled` / `rejected`."""

    def __init__(self, message: str, *, job: Any = None) -> None:
        super().__init__(message)
        #: Đối tượng job cuối cùng đọc được — có `.id`, `.error`, `.refunded`.
        self.job: Any = job
        self.job_id: Optional[str] = _job_field(job, "id")
        self.status: Optional[str] = _job_field(job, "status")
        error = _job_field(job, "error") or {}
        self.code: Optional[str] = _job_field(error, "code")
        #: µVND đã hoàn về ví.
        self.refunded: Optional[str] = _job_field(job, "refunded")


class JobTimeoutError(ShopAPIError):
    """Chờ job quá `timeout` mà job vẫn chưa xong. Job vẫn đang chạy trên server."""

    def __init__(
        self,
        message: str,
        *,
        job: Any = None,
        job_id: Optional[str] = None,
        waited_seconds: float = 0.0,
    ) -> None:
        super().__init__(message)
        #: Job cuối cùng đọc được (có thể là `None` nếu chưa kịp hỏi lần nào).
        self.job: Any = job
        self.job_id: Optional[str] = job_id or _job_field(job, "id")
        #: Số giây đã chờ.
        self.waited_seconds: float = waited_seconds


class SignatureVerificationError(ShopAPIError):
    """Chữ ký webhook không hợp lệ — KHÔNG được xử lý payload này."""


# ── Dựng ngoại lệ từ phản hồi ─────────────────────────────────────────────────

#: Dự phòng khi máy chủ không gửi `error.code`. Ba mã HTTP (403, 409, 503) có
#: nhiều hơn một mã lỗi, nên `error.code` mới là nguồn phân biệt chính xác —
#: bảng này chỉ chọn trường hợp hay gặp nhất.
_STATUS_TO_CLASS: Dict[int, Any] = {
    400: InvalidRequestError,
    401: AuthenticationError,
    402: InsufficientBalanceError,
    403: ContentRejectedError,
    404: NotFoundError,
    409: IdempotencyConflictError,
    422: UnsupportedParameterError,
    429: RateLimitError,
    500: InternalServerError,
    503: EngineUnavailableError,
}

_CODE_TO_CLASS: Dict[str, Any] = {
    "invalid_request": InvalidRequestError,
    "invalid_api_key": AuthenticationError,
    "insufficient_balance": InsufficientBalanceError,
    "content_rejected": ContentRejectedError,
    "permission_denied": PermissionDeniedError,
    "account_suspended": AccountSuspendedError,
    "not_found": NotFoundError,
    "conflict": ConflictError,
    "idempotency_conflict": IdempotencyConflictError,
    "unsupported_parameter": UnsupportedParameterError,
    "rate_limit_exceeded": RateLimitError,
    "engine_unavailable": EngineUnavailableError,
    "service_unavailable": ServiceUnavailableError,
    "internal_error": InternalServerError,
}


def _str_or_none(value: Any) -> Optional[str]:
    """Giữ nguyên chuỗi; số thì đổi sang chuỗi (tiền luôn là chuỗi). Còn lại bỏ qua."""
    if isinstance(value, str):
        return value
    if isinstance(value, int) and not isinstance(value, bool):
        return str(value)
    return None


def _job_field(source: Any, key: str) -> Any:
    """Đọc một trường từ `Model` hoặc `dict` mà không nổ khi thiếu."""
    if source is None:
        return None
    try:
        return source[key]
    except (KeyError, IndexError, TypeError):
        return None


def build_status_error(
    *,
    status: int,
    body: Any = None,
    headers: Optional[Mapping[str, str]] = None,
    retry_after: Optional[float] = None,
    text: Optional[str] = None,
) -> APIStatusError:
    """Dựng đúng lớp ngoại lệ từ phản hồi lỗi của API.

    Ưu tiên `error.code` trong body; nếu body không đúng khuôn thì suy từ mã HTTP.
    Dùng `error.message` của server nếu có, ngược lại dùng bản dựng sẵn tiếng Việt.
    """
    error: Dict[str, Any] = {}
    if isinstance(body, Mapping):
        candidate = body.get("error")
        if isinstance(candidate, Mapping):
            error = dict(candidate)

    code = error.get("code")
    cls = _CODE_TO_CLASS.get(code) if isinstance(code, str) else None
    if cls is None:
        cls = _STATUS_TO_CLASS.get(status)
    if cls is None:
        cls = InvalidRequestError if 400 <= status < 500 else InternalServerError

    message = error.get("message")
    if not isinstance(message, str) or not message.strip():
        message = None
        if text and not isinstance(body, Mapping):
            snippet = text.strip()[:200]
            if snippet:
                message = "{0} (máy chủ trả về HTTP {1}: {2})".format(
                    _default_message(cls.default_code), status, snippet
                )

    kwargs: Dict[str, Any] = {
        "status": status,
        "code": code if isinstance(code, str) else None,
        "type": error.get("type") if isinstance(error.get("type"), str) else None,
        "param": error.get("param") if isinstance(error.get("param"), str) else None,
        "request_id": error.get("request_id") if isinstance(error.get("request_id"), str) else None,
        "headers": headers,
        "body": body,
    }

    if cls is InsufficientBalanceError:
        return InsufficientBalanceError(
            message,
            required=error.get("required") if error.get("required") is not None else None,
            available=error.get("available") if error.get("available") is not None else None,
            **kwargs,
        )
    if cls is RateLimitError:
        if retry_after is None and error.get("retry_after") is not None:
            try:
                retry_after = float(error["retry_after"])
            except (TypeError, ValueError):
                retry_after = None
        return RateLimitError(message, retry_after=retry_after, **kwargs)
    if cls is PermissionDeniedError:
        # Ba nguyên nhân dùng chung mã `permission_denied`; giữ nguyên trường phụ
        # để `.reason` phân biệt được. Xem `policyError()` phía máy chủ.
        return PermissionDeniedError(
            message,
            required_scope=_str_or_none(error.get("required_scope")),
            granted_scopes=error.get("granted_scopes"),
            client_ip=_str_or_none(error.get("client_ip")),
            allowed=error.get("allowed"),
            key_monthly_limit=_str_or_none(error.get("key_monthly_limit")),
            key_spent_this_month=_str_or_none(error.get("key_spent_this_month")),
            **kwargs,
        )
    if cls is AuthenticationError:
        return AuthenticationError(
            message,
            expired_at=_str_or_none(error.get("expired_at")),
            replaced_by=_str_or_none(error.get("replaced_by")),
            **kwargs,
        )
    return cls(message, **kwargs)


def build_job_failed_error(job: Any) -> JobFailedError:
    """Dựng `JobFailedError` với thông điệp nhắc rõ việc hoàn tiền."""
    job_id = _job_field(job, "id") or "?"
    status = _job_field(job, "status") or "failed"
    error = _job_field(job, "error") or {}
    code = _job_field(error, "code")
    server_message = _job_field(error, "message")
    refunded = _job_field(job, "refunded")

    if status == "cancelled":
        headline = "Job {0} đã bị huỷ.".format(job_id)
    elif status == "rejected":
        headline = "Job {0} bị từ chối vì nội dung vi phạm quy định.".format(job_id)
    else:
        headline = "Job {0} chạy không thành công.".format(job_id)

    parts = [headline]
    if isinstance(server_message, str) and server_message.strip():
        parts.append(server_message.strip())
    else:
        copy = JOB_ERROR_COPY_VI.get(code or "")
        if copy:
            parts.append(str(copy["description"]))

    # Job hỏng luôn hoàn 100% — CONTRACT.md §2.2. Nói rõ để khách yên tâm.
    refunded_micro = None
    if refunded is not None:
        try:
            refunded_micro = int(str(refunded))
        except (TypeError, ValueError):
            refunded_micro = None
    if refunded_micro is not None and refunded_micro > 0:
        parts.append("Bạn đã được hoàn tiền đầy đủ: {0} trở lại ví.".format(format_vnd(refunded)))
    else:
        parts.append("Job hỏng không bị tính tiền, mọi khoản tạm giữ đều được hoàn lại.")

    copy = JOB_ERROR_COPY_VI.get(code or "")
    parts.append(str(copy["action"]) if copy else "Bạn tạo lại job giúp mình.")

    return JobFailedError(" ".join(parts), job=job)
