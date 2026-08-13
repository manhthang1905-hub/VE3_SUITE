"""`client.ledger` — sổ cái ví (`GET /v1/ledger`, CONTRACT.md §2.3)."""

from __future__ import annotations

from typing import TYPE_CHECKING, AsyncIterator, Iterator, Optional

from .._models import Model
from .._pagination import next_cursor, page_items

if TYPE_CHECKING:  # pragma: no cover
    from .._client import AsyncShopAPI, ShopAPI

__all__ = ["Ledger", "AsyncLedger"]


class Ledger:
    """Bản đồng bộ."""

    def __init__(self, client: "ShopAPI") -> None:
        self._client = client

    def list(self, *, limit: Optional[int] = 50, cursor: Optional[str] = None) -> Model:
        """`GET /v1/ledger?limit=&cursor=`.

        `amount` âm là trừ, dương là cộng. Các loại bút toán: TOPUP, BONUS,
        SIGNUP_CREDIT, HOLD, HOLD_RELEASE, CAPTURE, REFUND, PACKAGE_PURCHASE, ADJUSTMENT.
        """
        return self._client.request("GET", "/v1/ledger", params={"limit": limit, "cursor": cursor})

    def iterate(
        self, *, limit: Optional[int] = 50, cursor: Optional[str] = None
    ) -> Iterator[Model]:
        """Duyệt **mọi** bút toán qua nhiều trang, tự đi theo `next_cursor`.

        Đây là cách đối soát cả tháng mà không phải tự viết vòng lặp phân trang:

        ```python
        total = 0
        for entry in client.ledger.iterate():
            total += int(entry.amount)
        ```
        """
        while True:
            page = self.list(limit=limit, cursor=cursor)
            for item in page_items(page):
                yield item
            cursor = next_cursor(page)
            if cursor is None:
                return


class AsyncLedger:
    """Bản bất đồng bộ."""

    def __init__(self, client: "AsyncShopAPI") -> None:
        self._client = client

    async def list(self, *, limit: Optional[int] = 50, cursor: Optional[str] = None) -> Model:
        """`GET /v1/ledger`."""
        return await self._client.request(
            "GET", "/v1/ledger", params={"limit": limit, "cursor": cursor}
        )

    async def iterate(
        self, *, limit: Optional[int] = 50, cursor: Optional[str] = None
    ) -> AsyncIterator[Model]:
        """Duyệt mọi bút toán qua nhiều trang."""
        while True:
            page = await self.list(limit=limit, cursor=cursor)
            for item in page_items(page):
                yield item
            cursor = next_cursor(page)
            if cursor is None:
                return
