"""`client.stats` — số liệu công khai (`GET /v1/stats`, CONTRACT.md §2.5).

Không cần API key. Server đệm 5 phút và chỉ trả số thật.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from .._models import Model

if TYPE_CHECKING:  # pragma: no cover
    from .._client import AsyncShopAPI, ShopAPI

__all__ = ["Stats", "AsyncStats"]


class Stats:
    """Bản đồng bộ."""

    def __init__(self, client: "ShopAPI") -> None:
        self._client = client

    def retrieve(self) -> Model:
        """`GET /v1/stats` → `{ jobs_completed, avg_seconds, uptime_percent, updated_at }`."""
        return self._client.request("GET", "/v1/stats", auth=False)


class AsyncStats:
    """Bản bất đồng bộ."""

    def __init__(self, client: "AsyncShopAPI") -> None:
        self._client = client

    async def retrieve(self) -> Model:
        """`GET /v1/stats`."""
        return await self._client.request("GET", "/v1/stats", auth=False)
