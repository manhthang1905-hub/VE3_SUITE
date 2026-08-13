"""Helper tiền — micro-VND (CONTRACT.md §0, PRICING.md §1).

`1 VND = 1_000_000 µVND`. API luôn truyền tiền qua JSON dưới dạng **chuỗi**.

Module này TUYỆT ĐỐI không dùng `float` để tính toán: `int` của Python là số
nguyên lớn tuỳ ý nên giữ được chính xác tuyệt đối với mọi số tiền.

Module này không import bất kỳ module nào khác của SDK (tránh vòng lặp import),
nên lỗi đầu vào được báo bằng `ValueError` / `TypeError` chuẩn của Python.
"""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal
from typing import Union

__all__ = [
    "MICRO_PER_VND",
    "micro_to_vnd",
    "micro_to_vnd_exact",
    "vnd_to_micro",
    "format_vnd",
    "format_micro_vnd",
    "group_thousands",
    "add_micro",
    "sub_micro",
    "compare_micro",
]

#: 1 đồng = 1.000.000 micro-đồng.
MICRO_PER_VND: int = 1_000_000

#: Kiểu đầu vào chấp nhận cho một số tiền micro-VND.
MicroVnd = Union[str, int]

#: Ký hiệu đồng Việt Nam.
VND_SYMBOL = "₫"


def _parse_micro(value: MicroVnd) -> int:
    """Đổi một số tiền micro-VND về `int`, từ chối `float` để không mất chính xác."""
    if isinstance(value, bool):
        raise TypeError("Số tiền micro-VND không nhận kiểu bool.")
    if isinstance(value, int):
        return value
    if isinstance(value, Decimal):
        if value != value.to_integral_value():
            raise ValueError("Số tiền micro-VND phải là số nguyên, không có phần thập phân.")
        return int(value)
    if isinstance(value, float):
        raise TypeError(
            "Số tiền micro-VND không được truyền bằng float vì sẽ mất chính xác. "
            'Bạn truyền chuỗi (ví dụ "957000000") hoặc int giúp mình.'
        )
    if isinstance(value, str):
        text = value.strip()
        if not text:
            raise ValueError("Số tiền micro-VND đang rỗng.")
        sign = 1
        digits = text
        if digits[0] in "+-":
            sign = -1 if digits[0] == "-" else 1
            digits = digits[1:]
        if not digits.isdigit():
            raise ValueError(
                'Số tiền micro-VND phải là chuỗi chữ số nguyên, ví dụ "957000000". '
                "Giá trị nhận được: " + repr(value)
            )
        return sign * int(digits)
    raise TypeError("Không đọc được số tiền micro-VND từ kiểu " + type(value).__name__ + ".")


def micro_to_vnd(micro: MicroVnd) -> int:
    """`"957000000"` → `957` (số đồng, làm tròn xuống).

    Dùng chia lấy nguyên trên `int` nên không bao giờ mất chính xác, kể cả với
    số hàng nghìn tỷ.
    """
    return _parse_micro(micro) // MICRO_PER_VND


def micro_to_vnd_exact(micro: MicroVnd) -> Decimal:
    """Như :func:`micro_to_vnd` nhưng giữ cả phần lẻ, trả về `Decimal`.

    Dùng khi bạn cần cộng dồn nhiều số tiền nhỏ (ví dụ 3,333333₫/giây audio)
    mà không muốn sai số tích luỹ.
    """
    return Decimal(_parse_micro(micro)) / Decimal(MICRO_PER_VND)


def vnd_to_micro(vnd: Union[str, int, float, Decimal]) -> str:
    """`957` (đồng) → `"957000000"` (micro-VND, dạng chuỗi).

    Chấp nhận `float` cho tiện lợi (ví dụ `1.5`) nhưng bên trong đổi qua
    `Decimal` để không dính sai số nhị phân.
    """
    if isinstance(vnd, bool):
        raise TypeError("Số tiền đồng không nhận kiểu bool.")
    if isinstance(vnd, int):
        amount = Decimal(vnd)
    elif isinstance(vnd, Decimal):
        amount = vnd
    elif isinstance(vnd, float):
        amount = Decimal(repr(vnd))
    elif isinstance(vnd, str):
        try:
            amount = Decimal(vnd.strip())
        except Exception as exc:  # noqa: BLE001 — đổi thành lỗi tiếng Việt
            raise ValueError("Không đọc được số tiền đồng từ chuỗi " + repr(vnd) + ".") from exc
    else:
        raise TypeError("Không đọc được số tiền đồng từ kiểu " + type(vnd).__name__ + ".")

    micro = (amount * Decimal(MICRO_PER_VND)).quantize(Decimal(1), rounding=ROUND_HALF_UP)
    return str(int(micro))


def group_thousands(number: int) -> str:
    """`2500000` → `"2.500.000"`.

    Tự tách nhóm nghìn bằng dấu chấm theo chuẩn Việt Nam. KHÔNG dùng `locale`
    của hệ thống vì máy khách hàng thường không cài sẵn `vi_VN`.
    """
    negative = number < 0
    digits = str(abs(int(number)))
    parts = []
    while len(digits) > 3:
        parts.append(digits[-3:])
        digits = digits[:-3]
    parts.append(digits)
    text = ".".join(reversed(parts))
    return "-" + text if negative else text


def format_vnd(micro: MicroVnd, *, with_symbol: bool = True) -> str:
    """`"957000000"` → `"957₫"`, `"2500000000000"` → `"2.500.000₫"`.

    Đầu vào là **micro-VND**, đúng như mọi số tiền API trả về. Làm tròn nửa lên
    đến đồng khi hiển thị.
    """
    value = _parse_micro(micro)
    negative = value < 0
    whole, remainder = divmod(abs(value), MICRO_PER_VND)
    if remainder * 2 >= MICRO_PER_VND:
        whole += 1
    if negative:
        whole = -whole
    text = group_thousands(whole)
    return text + VND_SYMBOL if with_symbol else text


#: Bí danh cho :func:`format_vnd` — trùng tên với bản Node/PHP.
format_micro_vnd = format_vnd


def add_micro(*values: MicroVnd) -> str:
    """Cộng nhiều số tiền micro-VND, trả về chuỗi."""
    total = 0
    for value in values:
        total += _parse_micro(value)
    return str(total)


def sub_micro(a: MicroVnd, b: MicroVnd) -> str:
    """`a - b` trên micro-VND, trả về chuỗi."""
    return str(_parse_micro(a) - _parse_micro(b))


def compare_micro(a: MicroVnd, b: MicroVnd) -> int:
    """Trả `-1`, `0`, `1` — so sánh chính xác tuyệt đối, không qua float."""
    x = _parse_micro(a)
    y = _parse_micro(b)
    if x < y:
        return -1
    if x > y:
        return 1
    return 0
