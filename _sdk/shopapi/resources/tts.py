"""`client.tts` — chuyển văn bản thành giọng nói (`POST /v1/tts`)."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Dict, Mapping, Optional

from .._constants import DEFAULT_VOICE_ID
from .._models import Model
from .._polling import DEFAULT_WAIT_TIMEOUT
from .._validation import (
    validate_audio_format,
    validate_speed,
    validate_text,
    validate_voice_id,
    validate_webhook_url,
)
from .jobs import ProgressCallback

if TYPE_CHECKING:  # pragma: no cover
    from .._client import AsyncShopAPI, ShopAPI

__all__ = ["Tts", "AsyncTts"]


def build_body(
    *,
    text: str,
    voice_id: str,
    speed: Optional[float],
    format: str,  # noqa: A002 — trùng tên trường của API
    webhook_url: Optional[str],
    extra_body: Optional[Mapping[str, Any]],
) -> Dict[str, Any]:
    """Kiểm tra phía client rồi dựng thân request — SDK_SPEC §3.

    `speed` giữ lại trong chữ ký nhưng LUÔN bị từ chối (xem `validate_speed`).
    Xoá hẳn tham số thì code khách viết từ trước gãy bằng `TypeError` — một câu
    lỗi chẳng nói được vì sao. Giữ lại và ném lỗi có lời giải thích thì họ đọc
    một lần là biết phải bỏ gì.
    """
    if speed is not None:
        validate_speed(speed)  # luôn ném — có lời giải thích đầy đủ
    body: Dict[str, Any] = {
        "text": validate_text(text),
        "voice_id": validate_voice_id(voice_id),
        "format": validate_audio_format(format),
    }
    if webhook_url is not None:
        body["webhook_url"] = validate_webhook_url(webhook_url)
    if extra_body:
        body.update(extra_body)
    return body


class Tts:
    """Bản đồng bộ."""

    def __init__(self, client: "ShopAPI") -> None:
        self._client = client

    def create(
        self,
        *,
        text: str,
        voice_id: str = DEFAULT_VOICE_ID,
        speed: Optional[float] = None,
        format: str = "mp3",  # noqa: A002
        webhook_url: Optional[str] = None,
        idempotency_key: Optional[str] = None,
        extra_body: Optional[Mapping[str, Any]] = None,
    ) -> Model:
        """Tạo job đọc văn bản. Trả về ngay (`202`), job chạy nền.

        Giá 200₫ mỗi phút audio thật. Xem danh sách giọng ở `shopapi.VOICE_CATALOG`.
        """
        body = build_body(
            text=text,
            voice_id=voice_id,
            speed=speed,
            format=format,
            webhook_url=webhook_url,
            extra_body=extra_body,
        )
        return self._client.request(
            "POST", "/v1/tts", json=body, idempotent=True, idempotency_key=idempotency_key
        )

    def create_and_wait(
        self,
        *,
        text: str,
        voice_id: str = DEFAULT_VOICE_ID,
        speed: Optional[float] = None,
        format: str = "mp3",  # noqa: A002
        webhook_url: Optional[str] = None,
        idempotency_key: Optional[str] = None,
        extra_body: Optional[Mapping[str, Any]] = None,
        timeout: float = DEFAULT_WAIT_TIMEOUT,
        poll_interval: Optional[float] = None,
        on_progress: Optional[ProgressCallback] = None,
        raise_on_failure: bool = True,
    ) -> Model:
        """Tạo job rồi chờ tới khi có kết quả. Kết quả nằm ở `job.output.url`."""
        job = self.create(
            text=text,
            voice_id=voice_id,
            speed=speed,
            format=format,
            webhook_url=webhook_url,
            idempotency_key=idempotency_key,
            extra_body=extra_body,
        )
        return self._client.jobs.wait(
            job["id"],
            timeout=timeout,
            poll_interval=poll_interval,
            on_progress=on_progress,
            raise_on_failure=raise_on_failure,
            estimated_seconds=job.get("estimated_seconds"),
        )


class AsyncTts:
    """Bản bất đồng bộ — cùng bề mặt, chỉ thêm `await`."""

    def __init__(self, client: "AsyncShopAPI") -> None:
        self._client = client

    async def create(
        self,
        *,
        text: str,
        voice_id: str = DEFAULT_VOICE_ID,
        speed: Optional[float] = None,
        format: str = "mp3",  # noqa: A002
        webhook_url: Optional[str] = None,
        idempotency_key: Optional[str] = None,
        extra_body: Optional[Mapping[str, Any]] = None,
    ) -> Model:
        """Tạo job đọc văn bản."""
        body = build_body(
            text=text,
            voice_id=voice_id,
            speed=speed,
            format=format,
            webhook_url=webhook_url,
            extra_body=extra_body,
        )
        return await self._client.request(
            "POST", "/v1/tts", json=body, idempotent=True, idempotency_key=idempotency_key
        )

    async def create_and_wait(
        self,
        *,
        text: str,
        voice_id: str = DEFAULT_VOICE_ID,
        speed: Optional[float] = None,
        format: str = "mp3",  # noqa: A002
        webhook_url: Optional[str] = None,
        idempotency_key: Optional[str] = None,
        extra_body: Optional[Mapping[str, Any]] = None,
        timeout: float = DEFAULT_WAIT_TIMEOUT,
        poll_interval: Optional[float] = None,
        on_progress: Optional[ProgressCallback] = None,
        raise_on_failure: bool = True,
    ) -> Model:
        """Tạo job rồi chờ tới khi có kết quả."""
        job = await self.create(
            text=text,
            voice_id=voice_id,
            speed=speed,
            format=format,
            webhook_url=webhook_url,
            idempotency_key=idempotency_key,
            extra_body=extra_body,
        )
        return await self._client.jobs.wait(
            job["id"],
            timeout=timeout,
            poll_interval=poll_interval,
            on_progress=on_progress,
            raise_on_failure=raise_on_failure,
            estimated_seconds=job.get("estimated_seconds"),
        )
