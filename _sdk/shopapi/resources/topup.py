"""`client.topup` — nạp tiền bằng mã QR (CONTRACT.md §2.3)."""

from __future__ import annotations

from typing import TYPE_CHECKING, Union

from .._constants import MAX_TOPUP_VND, MIN_TOPUP_VND
from .._models import Model
from .._money import group_thousands

if TYPE_CHECKING:  # pragma: no cover
    from .._client import AsyncShopAPI, ShopAPI

__all__ = ["Topup", "AsyncTopup"]


def _amount_vnd(amount_vnd: Union[str, int]) -> int:
    """Chuẩn hoá số tiền nạp — đơn vị **ĐỒNG**, không phải µVND.

    ⚠ NGOẠI LỆ DUY NHẤT CỦA SDK: mọi số tiền khác (ví, sổ cái, chi phí job) đều
    là µVND dạng chuỗi. Riêng đây là đồng, vì SePay và sao kê ngân hàng đối soát
    theo đồng — giữ đồng ở đúng chỗ tiếp xúc với ngân hàng thì không phải quy đổi
    hai lượt và không có chỗ nào để lệch.

    Chặn ngay tại máy khách hai lỗi tốn kém nhất: gửi µVND theo thói quen, và
    gửi số dưới mức nạp tối thiểu.
    """
    if isinstance(amount_vnd, bool):  # bool là con của int trong Python
        raise ValueError("Số tiền nạp phải là số nguyên dương, đơn vị đồng.")

    if isinstance(amount_vnd, str):
        # Người ta hay copy nguyên "500.000" từ giao diện.
        raw = amount_vnd.strip().replace(".", "").replace(",", "").replace(" ", "")
        if not raw.isdigit():
            raise ValueError(
                "Số tiền nạp phải là số nguyên dương, đơn vị đồng. "
                "Ví dụ: client.topup.create_intent(500_000)."
            )
        value = int(raw)
    elif isinstance(amount_vnd, int):
        value = amount_vnd
    else:
        raise ValueError("Số tiền nạp phải là số nguyên dương, đơn vị đồng.")

    if value <= 0:
        raise ValueError(
            "Số tiền nạp phải là số nguyên dương, đơn vị đồng. "
            "Ví dụ: client.topup.create_intent(500_000)."
        )
    if value < MIN_TOPUP_VND:
        raise ValueError(
            "Số tiền nạp tối thiểu là {0}₫ — bạn đang gửi {1}₫. "
            "Lưu ý đơn vị ở đây là ĐỒNG, không phải micro-VND như các số tiền khác.".format(
                group_thousands(MIN_TOPUP_VND), group_thousands(value)
            )
        )
    if value > MAX_TOPUP_VND:
        raise ValueError(
            "Một lần nạp tối đa {0}₫ — bạn đang gửi {1}₫. "
            "Nếu bạn vừa dùng vnd_to_micro(...) thì bỏ đi: riêng create_intent nhận "
            "ĐỒNG, gửi micro-VND là số bị thổi lên gấp một triệu lần. "
            "Còn nếu bạn thật sự muốn nạp khoản lớn thì chia thành nhiều lần giúp mình.".format(
                group_thousands(MAX_TOPUP_VND), group_thousands(value)
            )
        )
    return value


class Topup:
    """Bản đồng bộ."""

    def __init__(self, client: "ShopAPI") -> None:
        self._client = client

    def create_intent(self, amount_vnd: Union[str, int]) -> Model:
        """`POST /v1/topup/intent` — trả về mã QR và nội dung chuyển khoản.

        ``amount_vnd`` tính bằng **ĐỒNG**: ``create_intent(500_000)`` là nạp năm
        trăm nghìn đồng. Đây là ngoại lệ duy nhất — mọi số tiền khác trong SDK là
        µVND. Xem ghi chú ở :func:`_amount_vnd`.

        ⚠ THAY ĐỔI PHÁ VỠ TƯƠNG THÍCH (xem CHANGELOG.md): tham số cũ tên
        ``amount_micro`` và nhận µVND. Ai đang gọi
        ``create_intent(vnd_to_micro(500_000))`` phải đổi thành
        ``create_intent(500_000)`` — nếu không, số tiền bị thổi lên gấp một triệu
        lần, vượt trần nạp và **máy chủ từ chối, không nạp được đồng nào**.

        Số tiền TRẢ VỀ (``amount``, ``bonus``, ``credited``) vẫn là µVND như
        thường lệ; dùng ``shopapi.format_vnd()`` để hiển thị.
        """
        return self._client.request(
            "POST",
            "/v1/topup/intent",
            json={"amount": _amount_vnd(amount_vnd)},
            idempotent=True,
        )

    def retrieve(self, txn_id: str) -> Model:
        """`GET /v1/topup/{txn_id}` — hỏi lại mỗi 3 giây để biết tiền đã vào chưa."""
        return self._client.request("GET", "/v1/topup/" + str(txn_id).strip())


class AsyncTopup:
    """Bản bất đồng bộ."""

    def __init__(self, client: "AsyncShopAPI") -> None:
        self._client = client

    async def create_intent(self, amount_vnd: Union[str, int]) -> Model:
        """`POST /v1/topup/intent` — ``amount_vnd`` tính bằng **ĐỒNG**.

        Cùng ghi chú với bản đồng bộ: đây là ngoại lệ duy nhất nhận đồng, và là
        thay đổi phá vỡ tương thích so với ``amount_micro`` trước đây.
        """
        return await self._client.request(
            "POST",
            "/v1/topup/intent",
            json={"amount": _amount_vnd(amount_vnd)},
            idempotent=True,
        )

    async def retrieve(self, txn_id: str) -> Model:
        """`GET /v1/topup/{txn_id}`."""
        return await self._client.request("GET", "/v1/topup/" + str(txn_id).strip())
