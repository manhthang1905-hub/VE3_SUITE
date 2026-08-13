"""Lật trang tự động cho các endpoint trả về danh sách — CONTRACT.md §2.

Mọi endpoint phân trang của ShopAPI trả về cùng một khuôn:

```json
{ "object": "list", "data": [...], "has_more": true, "next_cursor": "job_..." }
```

Quy tắc dừng giống hệt SDK Node: đi tiếp **chỉ khi** `has_more` đúng **và**
`next_cursor` là chuỗi khác rỗng. Thiếu một trong hai thì dừng — nhờ vậy một
phản hồi thiếu trường hoặc `next_cursor: null` không bao giờ làm vòng lặp
chạy mãi.
"""

from __future__ import annotations

from typing import Any, List, Optional

from ._models import Model

__all__ = ["page_items", "next_cursor"]


def page_items(page: Model) -> List[Any]:
    """Lấy danh sách phần tử của một trang, mỗi phần tử đã bọc thành `Model`."""
    data = page.get("data")
    if not isinstance(data, list):
        return []
    return [Model(item) if isinstance(item, dict) else item for item in data]


def next_cursor(page: Model) -> Optional[str]:
    """Con trỏ trang kế tiếp, hoặc `None` khi đã hết."""
    if not page.get("has_more"):
        return None
    cursor = page.get("next_cursor")
    if isinstance(cursor, str) and cursor:
        return cursor
    return None
