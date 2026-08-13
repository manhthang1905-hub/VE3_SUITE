"""Kiểm tra chữ ký webhook — SDK_SPEC §8, CONTRACT.md §5.

Header ShopAPI gửi kèm mỗi webhook:

```
X-ShopAPI-Signature: t=1785312000,v1=<hmac_sha256(t + "." + rawBody, secret)>
X-ShopAPI-Event: job.succeeded
```

CẢNH BÁO QUAN TRỌNG NHẤT: `raw_body` phải là **thân request THÔ** (chuỗi hoặc
bytes đúng như nhận được trên dây). Nếu bạn parse JSON rồi `json.dumps` lại thì
thứ tự khoá và khoảng trắng đổi, chữ ký sẽ luôn sai.

FastAPI:      `raw = await request.body()`
Flask:        `raw = request.get_data()`
Django:       `raw = request.body`
"""

from __future__ import annotations

import hashlib
import hmac
import json
import time
from typing import TYPE_CHECKING, Any, Dict, Optional, Tuple, Union

from ._constants import EVENT_HEADER, SIGNATURE_HEADER
from ._exceptions import ShopAPIError, SignatureVerificationError
from ._models import Model

if TYPE_CHECKING:  # pragma: no cover
    from ._client import AsyncShopAPI, ShopAPI

__all__ = [
    "verify",
    "construct_event",
    "parse_signature_header",
    "build_signature_header",
    "Webhooks",
    "AsyncWebhooks",
    "SIGNATURE_HEADER",
    "EVENT_HEADER",
    "DEFAULT_TOLERANCE_SECONDS",
]

#: Cửa sổ chấp nhận mặc định (giây) — chống tấn công phát lại.
DEFAULT_TOLERANCE_SECONDS = 300


def _to_bytes(value: Union[str, bytes, bytearray]) -> bytes:
    if isinstance(value, (bytes, bytearray)):
        return bytes(value)
    if isinstance(value, str):
        return value.encode("utf-8")
    raise SignatureVerificationError(
        "`raw_body` phải là chuỗi hoặc bytes THÔ của request. Bạn đang truyền {0} — "
        "nếu đã parse JSON thì hãy lấy lại thân thô: FastAPI `await request.body()`, "
        "Flask `request.get_data()`, Django `request.body`.".format(type(value).__name__)
    )


def parse_signature_header(header: str) -> Tuple[int, str]:
    """Tách `t=<unix>,v1=<hex>` thành `(timestamp, chữ ký)`.

    Sai định dạng → `SignatureVerificationError`.
    """
    if not isinstance(header, str) or not header.strip():
        raise SignatureVerificationError(
            "Thiếu header {0}. Bạn kiểm tra lại xem webhook có đi qua proxy nào "
            "làm rơi header không.".format(SIGNATURE_HEADER)
        )

    timestamp: Optional[str] = None
    signature: Optional[str] = None
    for part in header.split(","):
        part = part.strip()
        if "=" not in part:
            continue
        key, value = part.split("=", 1)
        key = key.strip()
        if key == "t":
            timestamp = value.strip()
        elif key == "v1":
            signature = value.strip()

    if timestamp is None or signature is None:
        raise SignatureVerificationError(
            "Header {0} sai định dạng. Đúng phải là `t=<unix>,v1=<hex>`, "
            "bạn đang nhận: {1}".format(SIGNATURE_HEADER, header)
        )
    try:
        parsed_timestamp = int(timestamp)
    except ValueError:
        raise SignatureVerificationError(
            "Mốc thời gian `t` trong header {0} không phải số Unix: {1}".format(
                SIGNATURE_HEADER, timestamp
            )
        ) from None
    if not signature:
        raise SignatureVerificationError(
            "Chữ ký `v1` trong header {0} đang rỗng.".format(SIGNATURE_HEADER)
        )
    return parsed_timestamp, signature


def _expected_signature(timestamp: int, raw_body: bytes, secret: str) -> str:
    signed_payload = str(timestamp).encode("ascii") + b"." + raw_body
    return hmac.new(secret.encode("utf-8"), signed_payload, hashlib.sha256).hexdigest()


def verify(
    raw_body: Union[str, bytes, bytearray],
    signature_header: str,
    secret: str,
    tolerance_seconds: int = DEFAULT_TOLERANCE_SECONDS,
) -> bool:
    """Kiểm tra chữ ký webhook. Trả `True`, hoặc ném `SignatureVerificationError`.

    Các bước đúng theo SDK_SPEC §8:

    1. Tách header `t=<unix>,v1=<hex>`.
    2. Dựng `signed_payload = t + "." + raw_body` (raw_body là bytes THÔ).
    3. Tính `hmac_sha256(signed_payload, secret)` dạng hex.
    4. So sánh bằng `hmac.compare_digest` (thời gian hằng số, chống dò từng byte).
    5. Lệch thời gian quá `tolerance_seconds` → từ chối (chống phát lại).
    """
    if not secret or not isinstance(secret, str):
        raise ShopAPIError(
            "Thiếu webhook secret. Bạn truyền `secret=...`, đặt biến môi trường "
            "SHOPAPI_WEBHOOK_SECRET, hoặc khai `webhook_secret=` lúc khởi tạo client. "
            "Secret lấy ở mục Webhook trong bảng điều khiển (chỉ hiện đúng một lần)."
        )

    body = _to_bytes(raw_body)
    timestamp, signature = parse_signature_header(signature_header)

    drift = abs(time.time() - timestamp)
    if tolerance_seconds is not None and tolerance_seconds >= 0 and drift > tolerance_seconds:
        raise SignatureVerificationError(
            "Webhook quá cũ: lệch {0:.0f} giây so với hiện tại (cho phép {1} giây). "
            "Có thể đây là một request phát lại, hoặc đồng hồ máy chủ của bạn bị sai giờ. "
            "SDK từ chối để bảo vệ bạn.".format(drift, tolerance_seconds)
        )

    expected = _expected_signature(timestamp, body, secret)
    if not hmac.compare_digest(expected, signature):
        raise SignatureVerificationError(
            "Chữ ký webhook không khớp. Nguyên nhân hay gặp nhất: bạn đã parse JSON rồi "
            "serialize lại trước khi kiểm tra. Hãy dùng thân request THÔ — FastAPI "
            "`await request.body()`, Flask `request.get_data()`, Django `request.body`. "
            "Nguyên nhân thứ hai: dùng nhầm secret của môi trường khác."
        )
    return True


def construct_event(
    raw_body: Union[str, bytes, bytearray],
    signature_header: str,
    secret: str,
    tolerance_seconds: int = DEFAULT_TOLERANCE_SECONDS,
) -> Model:
    """Kiểm tra chữ ký rồi trả về payload đã parse.

    Dùng hàm này thay cho `json.loads` để không bao giờ quên bước kiểm tra.
    """
    verify(raw_body, signature_header, secret, tolerance_seconds)
    body = _to_bytes(raw_body)
    try:
        payload: Any = json.loads(body.decode("utf-8"))
    except (ValueError, UnicodeDecodeError) as exc:
        raise SignatureVerificationError(
            "Chữ ký hợp lệ nhưng thân webhook không phải JSON đọc được. "
            "Bạn kiểm tra lại xem có middleware nào sửa thân request không."
        ) from exc
    if not isinstance(payload, dict):
        payload = {"data": payload}
    return Model(payload)


class _BaseWebhooks:
    """Phần dùng chung — tự lấy secret từ client khi bạn không truyền."""

    def __init__(self, client: Any) -> None:
        self._client = client

    def _secret(self, secret: Optional[str]) -> str:
        resolved = secret or getattr(self._client, "webhook_secret", None)
        if not resolved:
            raise ShopAPIError(
                "Chưa có webhook secret. Bạn khởi tạo client với "
                "`ShopAPI(api_key=..., webhook_secret=\"whsec_...\")`, hoặc đặt biến môi "
                "trường SHOPAPI_WEBHOOK_SECRET, hoặc truyền thẳng `secret=` vào lời gọi này."
            )
        return str(resolved)

    def verify(
        self,
        raw_body: Union[str, bytes, bytearray],
        signature_header: str,
        secret: Optional[str] = None,
        tolerance_seconds: int = DEFAULT_TOLERANCE_SECONDS,
    ) -> bool:
        """Như :func:`shopapi.webhooks.verify` nhưng lấy sẵn secret của client."""
        return verify(raw_body, signature_header, self._secret(secret), tolerance_seconds)

    def construct_event(
        self,
        raw_body: Union[str, bytes, bytearray],
        signature_header: str,
        secret: Optional[str] = None,
        tolerance_seconds: int = DEFAULT_TOLERANCE_SECONDS,
    ) -> Model:
        """Kiểm tra chữ ký rồi trả về payload đã parse."""
        return construct_event(raw_body, signature_header, self._secret(secret), tolerance_seconds)


class Webhooks(_BaseWebhooks):
    """`client.webhooks` của `ShopAPI`.

    Kiểm tra chữ ký là việc thuần CPU nên không có bản `await` — hai client dùng
    chung đúng một bề mặt.
    """

    def __init__(self, client: "ShopAPI") -> None:
        super().__init__(client)


class AsyncWebhooks(_BaseWebhooks):
    """`client.webhooks` của `AsyncShopAPI` — cùng phương thức, không cần `await`."""

    def __init__(self, client: "AsyncShopAPI") -> None:
        super().__init__(client)


def build_signature_header(
    raw_body: Union[str, bytes, bytearray], secret: str, timestamp: Optional[int] = None
) -> str:
    """Dựng header chữ ký — dùng để tự test endpoint webhook của bạn.

    ```python
    header = shopapi.webhooks.build_signature_header(body, "whsec_test")
    ```
    """
    stamp = int(time.time()) if timestamp is None else int(timestamp)
    signature = _expected_signature(stamp, _to_bytes(raw_body), secret)
    return "t={0},v1={1}".format(stamp, signature)


def _headers_for(event: str, header: str) -> Dict[str, str]:  # pragma: no cover - tiện ích
    return {SIGNATURE_HEADER: header, EVENT_HEADER: event}
