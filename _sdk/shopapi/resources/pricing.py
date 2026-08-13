"""`client.pricing` — bảng giá công khai (CONTRACT.md §2.4).

Hai endpoint này KHÔNG cần API key nên SDK không gửi header `Authorization`.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Dict, Optional

from .._constants import SERVICE_TYPES
from .._exceptions import InvalidRequestError
from .._models import Model
from .._validation import validate_image_count

if TYPE_CHECKING:  # pragma: no cover
    from .._client import AsyncShopAPI, ShopAPI

__all__ = ["Pricing", "AsyncPricing"]


def build_estimate_body(
    *,
    type: str,  # noqa: A002 — trùng tên trường của API
    text_length: Optional[int],
    n: Optional[int],
    duration: Optional[int],
) -> Dict[str, Any]:
    """Dựng thân `POST /v1/pricing/estimate` và chặn tổ hợp tham số vô nghĩa."""
    if type not in SERVICE_TYPES:
        raise InvalidRequestError(
            "Loại dịch vụ `{0}` không tồn tại. Bạn chọn một trong: {1}.".format(
                type, ", ".join(SERVICE_TYPES)
            ),
            status=400,
            code="invalid_request",
            param="type",
        )
    if type == "tts":
        if not isinstance(text_length, int) or isinstance(text_length, bool) or text_length < 1:
            raise InvalidRequestError(
                "Ước tính giá TTS cần `text_length` là số ký tự (số nguyên ≥ 1). "
                "Bạn truyền `len(text)` giúp mình.",
                status=400,
                code="invalid_request",
                param="text_length",
            )
        return {"type": "tts", "text_length": text_length}
    if type == "image":
        return {"type": "image", "n": validate_image_count(1 if n is None else n)}
    return {"type": "video", "duration": 8 if duration is None else int(duration)}


class Pricing:
    """Bản đồng bộ."""

    def __init__(self, client: "ShopAPI") -> None:
        self._client = client

    def retrieve(self) -> Model:
        """`GET /v1/pricing` — không cần API key."""
        return self._client.request("GET", "/v1/pricing", auth=False)

    def estimate(
        self,
        *,
        type: str,  # noqa: A002
        text_length: Optional[int] = None,
        n: Optional[int] = None,
        duration: Optional[int] = None,
    ) -> Model:
        """`POST /v1/pricing/estimate` — không cần API key.

        `estimated_cost` là số tiền sẽ được TẠM GIỮ (luôn ≥ chi phí thật),
        `likely_cost` là chi phí thật dự kiến.
        """
        body = build_estimate_body(type=type, text_length=text_length, n=n, duration=duration)
        return self._client.request("POST", "/v1/pricing/estimate", json=body, auth=False)


class AsyncPricing:
    """Bản bất đồng bộ."""

    def __init__(self, client: "AsyncShopAPI") -> None:
        self._client = client

    async def retrieve(self) -> Model:
        """`GET /v1/pricing`."""
        return await self._client.request("GET", "/v1/pricing", auth=False)

    async def estimate(
        self,
        *,
        type: str,  # noqa: A002
        text_length: Optional[int] = None,
        n: Optional[int] = None,
        duration: Optional[int] = None,
    ) -> Model:
        """`POST /v1/pricing/estimate`."""
        body = build_estimate_body(type=type, text_length=text_length, n=n, duration=duration)
        return await self._client.request("POST", "/v1/pricing/estimate", json=body, auth=False)
