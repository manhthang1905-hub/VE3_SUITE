"""Lịch hỏi lại thích nghi khi chờ job — SDK_SPEC §6.

Tách riêng khỏi `_client.py` để `resources/jobs.py` dùng được mà không tạo vòng
lặp import.
"""

from __future__ import annotations

from typing import Iterator, Optional

__all__ = ["poll_delays", "DEFAULT_WAIT_TIMEOUT", "MAX_POLL_INTERVAL"]

#: Mặc định chờ tối đa 600 giây — SDK_SPEC §6.
DEFAULT_WAIT_TIMEOUT = 600.0
#: Không bao giờ hỏi lại thưa hơn 5 giây một lần.
MAX_POLL_INTERVAL = 5.0


def poll_delays(
    estimated_seconds: Optional[float] = None, poll_interval: Optional[float] = None
) -> Iterator[float]:
    """Sinh ra khoảng nghỉ trước mỗi lần hỏi lại trạng thái job.

    1. Job vừa tạo có `estimated_seconds` → ngủ `min(estimated_seconds * 0.5, 5)`
       giây trước lần hỏi đầu. Không có thì ngủ 1 giây.
    2. Sau đó mỗi vòng `interval = min(interval * 1.5, 5s)`, bắt đầu từ `1s`.

    Truyền `poll_interval` để cố định khoảng cách — hữu ích khi bạn tự điều khiển
    nhịp hỏi hoặc khi viết test.
    """
    if poll_interval is not None:
        fixed = max(float(poll_interval), 0.0)
        while True:
            yield fixed

    first = 1.0
    if estimated_seconds is not None:
        try:
            first = min(float(estimated_seconds) * 0.5, MAX_POLL_INTERVAL)
        except (TypeError, ValueError):
            first = 1.0
    yield max(first, 0.0)

    interval = 1.0
    while True:
        yield interval
        interval = min(interval * 1.5, MAX_POLL_INTERVAL)
