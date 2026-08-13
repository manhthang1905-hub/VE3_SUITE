"""Phân tích khung Server-Sent Events — SDK_SPEC §9.

Đúng theo đặc tả SSE của WHATWG:

* gộp nhiều dòng `data:` thành một chuỗi, nối bằng `\\n`
* bỏ qua dòng bắt đầu bằng `:` (comment / keep-alive)
* một dòng trống kết thúc và phát ra sự kiện
* bỏ đúng MỘT dấu cách sau dấu hai chấm
"""

from __future__ import annotations

import json
from typing import Any, List, Optional

__all__ = ["SSEEvent", "SSEDecoder"]


class SSEEvent:
    """Một sự kiện SSE đã ráp xong."""

    __slots__ = ("event", "data", "id", "retry")

    def __init__(
        self,
        *,
        event: Optional[str] = None,
        data: str = "",
        id: Optional[str] = None,  # noqa: A002 — tên trường của SSE
        retry: Optional[int] = None,
    ) -> None:
        #: Tên sự kiện, ví dụ `job.progress`. `None` khi server không gửi `event:`.
        self.event: Optional[str] = event
        #: Phần thân đã gộp của các dòng `data:`.
        self.data: str = data
        self.id: Optional[str] = id
        self.retry: Optional[int] = retry

    def json(self) -> Any:
        """Đọc `data` thành JSON. Ném `ValueError` nếu không phải JSON."""
        return json.loads(self.data)

    def __repr__(self) -> str:
        return "SSEEvent(event={0!r}, data={1!r})".format(self.event, self.data)

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, SSEEvent):
            return NotImplemented
        return (self.event, self.data, self.id, self.retry) == (
            other.event,
            other.data,
            other.id,
            other.retry,
        )


class SSEDecoder:
    """Bộ phân tích SSE theo từng dòng.

    Dùng chung cho cả `httpx.Client` lẫn `httpx.AsyncClient` — chỉ cần bơm từng
    dòng vào `feed_line`.
    """

    def __init__(self) -> None:
        self._data: List[str] = []
        self._event: Optional[str] = None
        self._id: Optional[str] = None
        self._retry: Optional[int] = None

    def _reset(self) -> None:
        self._data = []
        self._event = None
        self._id = None
        self._retry = None

    def feed_line(self, line: str) -> Optional[SSEEvent]:
        """Nạp một dòng (không kèm ký tự xuống dòng).

        Trả về `SSEEvent` khi gặp dòng trống kết thúc một sự kiện, ngược lại `None`.
        """
        if line.endswith("\r"):
            line = line[:-1]
        if line.endswith("\n"):
            line = line[:-1]
        if line.endswith("\r"):
            line = line[:-1]

        if line == "":
            return self._dispatch()

        # Dòng comment / keep-alive: bỏ qua hoàn toàn.
        if line.startswith(":"):
            return None

        if ":" in line:
            field, value = line.split(":", 1)
            if value.startswith(" "):
                value = value[1:]
        else:
            field, value = line, ""

        if field == "data":
            self._data.append(value)
        elif field == "event":
            self._event = value
        elif field == "id":
            # Đặc tả cấm ký tự NUL trong id.
            if "\x00" not in value:
                self._id = value
        elif field == "retry":
            try:
                self._retry = int(value)
            except ValueError:
                pass
        # Field lạ thì bỏ qua, đúng đặc tả.
        return None

    def _dispatch(self) -> Optional[SSEEvent]:
        if not self._data and self._event is None:
            # Dòng trống thừa giữa hai sự kiện.
            self._reset()
            return None
        event = SSEEvent(
            event=self._event, data="\n".join(self._data), id=self._id, retry=self._retry
        )
        self._reset()
        return event

    def flush(self) -> Optional[SSEEvent]:
        """Phát nốt sự kiện dở dang khi luồng đóng mà thiếu dòng trống cuối."""
        return self._dispatch()
