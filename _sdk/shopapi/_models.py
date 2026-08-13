"""Kiểu trả về nhẹ — không phụ thuộc pydantic.

`Model` bọc `dict` mà API trả về và cho phép truy cập theo **cả hai** cách:

```python
audio.output.url      # thuộc tính, lồng nhau bao nhiêu tầng cũng được
audio["output"]["url"]  # như dict
```

Lý do không dùng dataclass cứng cho từng phản hồi: API còn thêm trường mới theo
thời gian, mà SDK cũ không được vỡ khi gặp trường lạ.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, Iterator, List, Mapping, Optional

try:  # Python 3.9 đặt các ABC ở collections.abc
    from collections.abc import Mapping as _MappingABC
except ImportError:  # pragma: no cover
    from typing import Mapping as _MappingABC  # type: ignore[assignment]

__all__ = ["Model", "RateLimit"]


class RateLimit:
    """Giới hạn tần suất đọc từ header của MỌI phản hồi — CONTRACT.md §8."""

    __slots__ = ("limit", "remaining", "reset")

    def __init__(
        self, limit: Optional[int] = None, remaining: Optional[int] = None, reset: Optional[int] = None
    ) -> None:
        #: Số request tối đa mỗi phút của hạng tài khoản.
        self.limit: Optional[int] = limit
        #: Số request còn lại trong cửa sổ hiện tại.
        self.remaining: Optional[int] = remaining
        #: Mốc thời gian Unix khi cửa sổ được đặt lại.
        self.reset: Optional[int] = reset

    @property
    def reset_at(self) -> Optional[datetime]:
        """`reset` đổi sang `datetime` UTC cho dễ đọc."""
        if self.reset is None:
            return None
        return datetime.fromtimestamp(self.reset, tz=timezone.utc)

    @classmethod
    def from_headers(cls, headers: Mapping[str, str]) -> Optional["RateLimit"]:
        """Đọc `X-RateLimit-*`. Trả `None` nếu phản hồi không có header nào."""

        def _int(name: str) -> Optional[int]:
            raw = headers.get(name)
            if raw is None:
                return None
            try:
                return int(str(raw).strip())
            except (TypeError, ValueError):
                return None

        limit = _int("X-RateLimit-Limit")
        remaining = _int("X-RateLimit-Remaining")
        reset = _int("X-RateLimit-Reset")
        if limit is None and remaining is None and reset is None:
            return None
        return cls(limit=limit, remaining=remaining, reset=reset)

    def __repr__(self) -> str:
        return "RateLimit(limit={0!r}, remaining={1!r}, reset={2!r})".format(
            self.limit, self.remaining, self.reset
        )

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, RateLimit):
            return NotImplemented
        return (self.limit, self.remaining, self.reset) == (other.limit, other.remaining, other.reset)


def _wrap(value: Any) -> Any:
    """Bọc dict thành `Model`, list thành list đã bọc, còn lại giữ nguyên."""
    if isinstance(value, Model):
        return value
    if isinstance(value, _MappingABC):
        return Model(value)
    if isinstance(value, (list, tuple)):
        return [_wrap(item) for item in value]
    return value


def _unwrap(value: Any) -> Any:
    if isinstance(value, Model):
        return value.to_dict()
    if isinstance(value, _MappingABC):
        return {str(k): _unwrap(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_unwrap(item) for item in value]
    return value


class Model(_MappingABC):  # type: ignore[misc]
    """Bọc một đối tượng JSON của API.

    Vừa là `Mapping` (nên `dict(model)`, `"key" in model`, `model.get(...)` đều
    chạy), vừa cho truy cập bằng thuộc tính lồng nhau.
    """

    def __init__(self, data: Optional[Mapping[str, Any]] = None) -> None:
        object.__setattr__(self, "_data", dict(data or {}))
        object.__setattr__(self, "_rate_limit", None)
        object.__setattr__(self, "_request_id", None)
        object.__setattr__(self, "_status_code", None)

    # ── Mapping ───────────────────────────────────────────────────────────────

    def __getitem__(self, key: str) -> Any:
        return _wrap(self._data[key])

    def __iter__(self) -> Iterator[str]:
        return iter(self._data)

    def __len__(self) -> int:
        return len(self._data)

    def __contains__(self, key: object) -> bool:
        return key in self._data

    # ── Truy cập bằng thuộc tính ──────────────────────────────────────────────

    def __getattr__(self, name: str) -> Any:
        # `__getattr__` chỉ được gọi khi tra cứu thường thất bại, nên phương thức
        # thật của lớp luôn thắng. Chặn tên bắt đầu bằng "_" để không đệ quy vô hạn
        # lúc copy/pickle.
        if name.startswith("_"):
            raise AttributeError(name)
        try:
            data: Dict[str, Any] = object.__getattribute__(self, "_data")
        except AttributeError:  # pragma: no cover — chỉ xảy ra khi unpickle dở dang
            raise AttributeError(name) from None
        if name in data:
            return _wrap(data[name])
        available = ", ".join(sorted(data)) or "(không có trường nào)"
        raise AttributeError(
            "Đối tượng không có trường '{0}'. Các trường hiện có: {1}".format(name, available)
        )

    def __setattr__(self, name: str, value: Any) -> None:
        if name.startswith("_"):
            object.__setattr__(self, name, value)
            return
        raise AttributeError(
            "Đối tượng trả về từ ShopAPI là chỉ đọc. Bạn dùng `model.to_dict()` "
            "rồi sửa trên bản sao giúp mình."
        )

    # ── Tiện ích ──────────────────────────────────────────────────────────────

    def to_dict(self) -> Dict[str, Any]:
        """Trả về bản sao dữ liệu thô (dict lồng dict), an toàn để sửa."""
        return {key: _unwrap(value) for key, value in self._data.items()}

    @property
    def rate_limit(self) -> Optional[RateLimit]:
        """Giới hạn tần suất đọc từ header của chính phản hồi này."""
        return object.__getattribute__(self, "_rate_limit")

    @property
    def request_id(self) -> Optional[str]:
        """`request_id` của phản hồi — gửi cho hỗ trợ khi cần tra cứu."""
        return object.__getattribute__(self, "_request_id")

    @property
    def status_code(self) -> Optional[int]:
        """Mã HTTP của phản hồi đã tạo ra đối tượng này."""
        return object.__getattribute__(self, "_status_code")

    def _attach(
        self,
        *,
        rate_limit: Optional[RateLimit] = None,
        request_id: Optional[str] = None,
        status_code: Optional[int] = None,
    ) -> "Model":
        object.__setattr__(self, "_rate_limit", rate_limit)
        object.__setattr__(self, "_request_id", request_id)
        object.__setattr__(self, "_status_code", status_code)
        return self

    def __eq__(self, other: object) -> bool:
        if isinstance(other, Model):
            return self._data == other._data
        if isinstance(other, _MappingABC):
            return self.to_dict() == _unwrap(other)
        return NotImplemented

    def __ne__(self, other: object) -> bool:
        result = self.__eq__(other)
        if result is NotImplemented:
            return result
        return not result

    __hash__ = None  # type: ignore[assignment]

    def __repr__(self) -> str:
        return "Model({0!r})".format(self._data)

    def __dir__(self) -> List[str]:
        return sorted(set(list(super().__dir__()) + [k for k in self._data if isinstance(k, str)]))
