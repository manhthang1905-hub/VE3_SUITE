"""`client.balance` — số dư ví (`GET /v1/balance`, CONTRACT.md §2.3)."""

from __future__ import annotations

from typing import TYPE_CHECKING

from .._models import Model

if TYPE_CHECKING:  # pragma: no cover
    from .._client import AsyncShopAPI, ShopAPI

__all__ = ["Balance", "AsyncBalance"]


class Balance:
    """Bản đồng bộ."""

    def __init__(self, client: "ShopAPI") -> None:
        self._client = client

    def retrieve(self) -> Model:
        """`GET /v1/balance`.

        `wallet` là µVND tiền mặt (chuỗi), `entitlements` là gói đã mua — được trừ
        TRƯỚC ví tiền mặt. Dùng `shopapi.format_vnd(balance.wallet)` để hiển thị.
        """
        return self._client.request("GET", "/v1/balance")


class AsyncBalance:
    """Bản bất đồng bộ."""

    def __init__(self, client: "AsyncShopAPI") -> None:
        self._client = client

    async def retrieve(self) -> Model:
        """`GET /v1/balance`."""
        return await self._client.request("GET", "/v1/balance")
