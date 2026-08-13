"""`client.videos` — tạo video (`POST /v1/videos/generations`)."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Dict, Mapping, Optional

from .._models import Model
from .._polling import DEFAULT_WAIT_TIMEOUT
from .._validation import (
    default_video_duration,
    validate_aspect_ratio,
    validate_engine,
    validate_prompt,
    validate_video_duration,
    validate_webhook_url,
)
from .jobs import ProgressCallback

if TYPE_CHECKING:  # pragma: no cover
    from .._client import AsyncShopAPI, ShopAPI

__all__ = ["Videos", "AsyncVideos"]


def build_body(
    *,
    prompt: str,
    engine: str,
    duration: Optional[int],
    aspect_ratio: str,
    image_url: Optional[str],
    webhook_url: Optional[str],
    extra_body: Optional[Mapping[str, Any]],
) -> Dict[str, Any]:
    """Kiểm tra phía client rồi dựng thân request — SDK_SPEC §3.

    `duration` phải hợp lệ với `engine`: veo3 chỉ 8 giây, seedance chỉ 10, auto
    nhận cả hai. Sai là chặn ngay, không tốn một vòng mạng.

    ⚠ `duration=None` thì SDK suy theo engine chứ KHÔNG lấy cứng 8 giây. Mặc
    định cứng 8 làm ``videos.create(prompt=..., engine="seedance")`` **luôn luôn
    lỗi** — người dùng chỉ đích danh engine mình muốn rồi bị chính SDK từ chối,
    và phải tự đoán ra rằng còn thiếu ``duration=10``.
    """
    checked_engine = validate_engine(engine)
    chosen_duration = default_video_duration(checked_engine) if duration is None else duration
    body: Dict[str, Any] = {
        "prompt": validate_prompt(prompt),
        "engine": checked_engine,
        "duration": validate_video_duration(checked_engine, chosen_duration),
        "aspect_ratio": validate_aspect_ratio(aspect_ratio),
    }
    if image_url is not None:
        body["image_url"] = image_url
    if webhook_url is not None:
        body["webhook_url"] = validate_webhook_url(webhook_url)
    if extra_body:
        body.update(extra_body)
    return body


class Videos:
    """Bản đồng bộ."""

    def __init__(self, client: "ShopAPI") -> None:
        self._client = client

    def create(
        self,
        *,
        prompt: str,
        engine: str = "auto",
        duration: Optional[int] = None,
        aspect_ratio: str = "16:9",
        image_url: Optional[str] = None,
        webhook_url: Optional[str] = None,
        idempotency_key: Optional[str] = None,
        extra_body: Optional[Mapping[str, Any]] = None,
    ) -> Model:
        """Tạo job dựng video.

        Giá theo engine: Veo3 500₫ (clip 8 giây), Seedance 1.000₫ (clip 10 giây)
        — xem :data:`shopapi.VIDEO_UNIT_PRICE_MICRO`.

        Nên để `engine="auto"`, nhưng KHÔNG phải vì nó chống được engine hỏng.
        `duration` quyết định engine theo ánh xạ 1–1 (8 giây → Veo3, 10 giây →
        Seedance), nên `auto` không phải lớp dự phòng: engine của thời lượng đó
        chết thì job chết theo. Cái `auto` làm là cho bạn vào việc sớm hơn, giữ
        theo mức đắt nhất rồi hoàn phần thừa.
        `duration=None` thì SDK điền theo engine (veo3/auto → 8, seedance → 10).
        Có `image_url` thì chuyển sang chế độ ảnh-thành-video.
        """
        body = build_body(
            prompt=prompt,
            engine=engine,
            duration=duration,
            aspect_ratio=aspect_ratio,
            image_url=image_url,
            webhook_url=webhook_url,
            extra_body=extra_body,
        )
        return self._client.request(
            "POST",
            "/v1/videos/generations",
            json=body,
            idempotent=True,
            idempotency_key=idempotency_key,
        )

    def create_and_wait(
        self,
        *,
        prompt: str,
        engine: str = "auto",
        duration: Optional[int] = None,
        aspect_ratio: str = "16:9",
        image_url: Optional[str] = None,
        webhook_url: Optional[str] = None,
        idempotency_key: Optional[str] = None,
        extra_body: Optional[Mapping[str, Any]] = None,
        timeout: float = DEFAULT_WAIT_TIMEOUT,
        poll_interval: Optional[float] = None,
        on_progress: Optional[ProgressCallback] = None,
        raise_on_failure: bool = True,
    ) -> Model:
        """Tạo job rồi chờ xong. Video điển hình mất ~95 giây, lúc tải cao có thể 3–5 phút."""
        job = self.create(
            prompt=prompt,
            engine=engine,
            duration=duration,
            aspect_ratio=aspect_ratio,
            image_url=image_url,
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


class AsyncVideos:
    """Bản bất đồng bộ — cùng bề mặt, chỉ thêm `await`."""

    def __init__(self, client: "AsyncShopAPI") -> None:
        self._client = client

    async def create(
        self,
        *,
        prompt: str,
        engine: str = "auto",
        duration: Optional[int] = None,
        aspect_ratio: str = "16:9",
        image_url: Optional[str] = None,
        webhook_url: Optional[str] = None,
        idempotency_key: Optional[str] = None,
        extra_body: Optional[Mapping[str, Any]] = None,
    ) -> Model:
        """Tạo job dựng video."""
        body = build_body(
            prompt=prompt,
            engine=engine,
            duration=duration,
            aspect_ratio=aspect_ratio,
            image_url=image_url,
            webhook_url=webhook_url,
            extra_body=extra_body,
        )
        return await self._client.request(
            "POST",
            "/v1/videos/generations",
            json=body,
            idempotent=True,
            idempotency_key=idempotency_key,
        )

    async def create_and_wait(
        self,
        *,
        prompt: str,
        engine: str = "auto",
        duration: Optional[int] = None,
        aspect_ratio: str = "16:9",
        image_url: Optional[str] = None,
        webhook_url: Optional[str] = None,
        idempotency_key: Optional[str] = None,
        extra_body: Optional[Mapping[str, Any]] = None,
        timeout: float = DEFAULT_WAIT_TIMEOUT,
        poll_interval: Optional[float] = None,
        on_progress: Optional[ProgressCallback] = None,
        raise_on_failure: bool = True,
    ) -> Model:
        """Tạo job rồi chờ xong."""
        job = await self.create(
            prompt=prompt,
            engine=engine,
            duration=duration,
            aspect_ratio=aspect_ratio,
            image_url=image_url,
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
