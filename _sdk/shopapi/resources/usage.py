"""`client.usage` — thống kê chi tiêu (`GET /v1/usage`, CONTRACT.md §2.3)."""

from __future__ import annotations

from typing import TYPE_CHECKING, Optional

from .._models import Model

if TYPE_CHECKING:  # pragma: no cover
    from .._client import AsyncShopAPI, ShopAPI

__all__ = ["Usage", "AsyncUsage"]


class Usage:
    """Bản đồng bộ."""

    def __init__(self, client: "ShopAPI") -> None:
        self._client = client

    def retrieve(
        self,
        *,
        from_: Optional[str] = None,
        to: Optional[str] = None,
        group_by: str = "day",
    ) -> Model:
        """`GET /v1/usage?from=&to=&group_by=day|type`.

        `from_` có dấu gạch dưới vì `from` là từ khoá của Python.
        """
        return self._client.request(
            "GET", "/v1/usage", params={"from": from_, "to": to, "group_by": group_by}
        )


class AsyncUsage:
    """Bản bất đồng bộ."""

    def __init__(self, client: "AsyncShopAPI") -> None:
        self._client = client

    async def retrieve(
        self,
        *,
        from_: Optional[str] = None,
        to: Optional[str] = None,
        group_by: str = "day",
    ) -> Model:
        """`GET /v1/usage`."""
        return await self._client.request(
            "GET", "/v1/usage", params={"from": from_, "to": to, "group_by": group_by}
        )
