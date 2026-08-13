"""`client.images` — tạo ảnh (`POST /v1/images/generations`)."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Dict, Mapping, Optional, Sequence

from .._models import Model
from .._polling import DEFAULT_WAIT_TIMEOUT
from .._validation import (
    validate_aspect_ratio,
    validate_image_count,
    validate_prompt,
    validate_reference_images,
    validate_webhook_url,
)
from .jobs import ProgressCallback

if TYPE_CHECKING:  # pragma: no cover
    from .._client import AsyncShopAPI, ShopAPI

__all__ = ["Images", "AsyncImages"]


def build_body(
    *,
    prompt: str,
    n: int,
    aspect_ratio: str,
    seed: Optional[int],
    reference_images: Optional[Sequence[str]],
    webhook_url: Optional[str],
    extra_body: Optional[Mapping[str, Any]],
) -> Dict[str, Any]:
    """Kiểm tra phía client rồi dựng thân request — SDK_SPEC §3."""
    body: Dict[str, Any] = {
        "prompt": validate_prompt(prompt),
        "n": validate_image_count(n),
        "aspect_ratio": validate_aspect_ratio(aspect_ratio),
    }
    if seed is not None:
        body["seed"] = int(seed)
    if reference_images is not None:
        body["reference_images"] = validate_reference_images(reference_images)
    if webhook_url is not None:
        body["webhook_url"] = validate_webhook_url(webhook_url)
    if extra_body:
        body.update(extra_body)
    return body


class Images:
    """Bản đồng bộ."""

    def __init__(self, client: "ShopAPI") -> None:
        self._client = client

    def create(
        self,
        *,
        prompt: str,
        n: int = 1,
        aspect_ratio: str = "16:9",
        seed: Optional[int] = None,
        reference_images: Optional[Sequence[str]] = None,
        webhook_url: Optional[str] = None,
        idempotency_key: Optional[str] = None,
        extra_body: Optional[Mapping[str, Any]] = None,
    ) -> Model:
        """Tạo job sinh ảnh. Giá 100₫ mỗi ảnh — `n` ảnh tính tiền `n` lần."""
        body = build_body(
            prompt=prompt,
            n=n,
            aspect_ratio=aspect_ratio,
            seed=seed,
            reference_images=reference_images,
            webhook_url=webhook_url,
            extra_body=extra_body,
        )
        return self._client.request(
            "POST",
            "/v1/images/generations",
            json=body,
            idempotent=True,
            idempotency_key=idempotency_key,
        )

    def create_and_wait(
        self,
        *,
        prompt: str,
        n: int = 1,
        aspect_ratio: str = "16:9",
        seed: Optional[int] = None,
        reference_images: Optional[Sequence[str]] = None,
        webhook_url: Optional[str] = None,
        idempotency_key: Optional[str] = None,
        extra_body: Optional[Mapping[str, Any]] = None,
        timeout: float = DEFAULT_WAIT_TIMEOUT,
        poll_interval: Optional[float] = None,
        on_progress: Optional[ProgressCallback] = None,
        raise_on_failure: bool = True,
    ) -> Model:
        """Tạo job rồi chờ xong. Nhiều ảnh nằm ở `job.outputs`, ảnh đầu ở `job.output`."""
        job = self.create(
            prompt=prompt,
            n=n,
            aspect_ratio=aspect_ratio,
            seed=seed,
            reference_images=reference_images,
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


class AsyncImages:
    """Bản bất đồng bộ — cùng bề mặt, chỉ thêm `await`."""

    def __init__(self, client: "AsyncShopAPI") -> None:
        self._client = client

    async def create(
        self,
        *,
        prompt: str,
        n: int = 1,
        aspect_ratio: str = "16:9",
        seed: Optional[int] = None,
        reference_images: Optional[Sequence[str]] = None,
        webhook_url: Optional[str] = None,
        idempotency_key: Optional[str] = None,
        extra_body: Optional[Mapping[str, Any]] = None,
    ) -> Model:
        """Tạo job sinh ảnh."""
        body = build_body(
            prompt=prompt,
            n=n,
            aspect_ratio=aspect_ratio,
            seed=seed,
            reference_images=reference_images,
            webhook_url=webhook_url,
            extra_body=extra_body,
        )
        return await self._client.request(
            "POST",
            "/v1/images/generations",
            json=body,
            idempotent=True,
            idempotency_key=idempotency_key,
        )

    async def create_and_wait(
        self,
        *,
        prompt: str,
        n: int = 1,
        aspect_ratio: str = "16:9",
        seed: Optional[int] = None,
        reference_images: Optional[Sequence[str]] = None,
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
            n=n,
            aspect_ratio=aspect_ratio,
            seed=seed,
            reference_images=reference_images,
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
