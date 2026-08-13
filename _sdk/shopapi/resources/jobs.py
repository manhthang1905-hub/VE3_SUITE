"""`client.jobs` — theo dõi, liệt kê, huỷ và chờ job (CONTRACT.md §2.2)."""

from __future__ import annotations

import asyncio
import json as _json
import time
from typing import TYPE_CHECKING, Any, AsyncIterator, Callable, Dict, Iterator, Optional

from .._constants import TERMINAL_JOB_STATUSES
from .._exceptions import JobTimeoutError, build_job_failed_error
from .._models import Model
from .._pagination import next_cursor, page_items
from .._polling import DEFAULT_WAIT_TIMEOUT, poll_delays
from .._validation import validate_job_id

if TYPE_CHECKING:  # pragma: no cover
    from .._client import AsyncShopAPI, ShopAPI

__all__ = ["Jobs", "AsyncJobs"]

#: Callback nhận đối tượng job mỗi lần hỏi lại.
ProgressCallback = Callable[[Model], Any]


def _list_params(
    status: Optional[str],
    type: Optional[str],  # noqa: A002 — trùng tên tham số của API
    limit: Optional[int],
    cursor: Optional[str],
    from_: Optional[str],
    to: Optional[str],
) -> Dict[str, Any]:
    return {
        "status": status,
        "type": type,
        "limit": limit,
        "cursor": cursor,
        # `from` là từ khoá của Python nên tham số đặt tên `from_`.
        "from": from_,
        "to": to,
    }


def _timeout_error(job: Optional[Model], job_id: str, waited: float) -> JobTimeoutError:
    status = None
    if job is not None:
        status = job.get("status")
    tail = " Trạng thái cuối cùng đọc được: {0}.".format(status) if status else ""
    return JobTimeoutError(
        "Đã chờ {0:.0f} giây mà job {1} vẫn chưa xong.{2} Job vẫn đang chạy trên máy chủ — "
        "bạn dùng `client.jobs.retrieve(\"{1}\")` để tra lại sau, hoặc khai `webhook_url` "
        "để được báo ngay khi xong.".format(waited, job_id, tail),
        job=job,
        job_id=job_id,
        waited_seconds=waited,
    )


def _parse_event(raw: str, event_name: Optional[str]) -> Optional[Model]:
    try:
        payload = _json.loads(raw)
    except ValueError:
        return None
    if not isinstance(payload, dict):
        return None
    if event_name and "event" not in payload:
        payload["event"] = event_name
    return Model(payload)


def _is_stream_finished(event: Model) -> bool:
    if event.get("event") in ("job.succeeded", "job.failed"):
        return True
    return event.get("status") in TERMINAL_JOB_STATUSES


class Jobs:
    """Bản đồng bộ."""

    def __init__(self, client: "ShopAPI") -> None:
        self._client = client

    def retrieve(self, job_id: str) -> Model:
        """`GET /v1/jobs/{id}`."""
        job_id = validate_job_id(job_id)
        return self._client.request("GET", "/v1/jobs/" + job_id)

    def list(
        self,
        *,
        status: Optional[str] = None,
        type: Optional[str] = None,  # noqa: A002
        limit: Optional[int] = 20,
        cursor: Optional[str] = None,
        from_: Optional[str] = None,
        to: Optional[str] = None,
    ) -> Model:
        """`GET /v1/jobs` — trả về `{ object, data, has_more, next_cursor }`."""
        return self._client.request(
            "GET", "/v1/jobs", params=_list_params(status, type, limit, cursor, from_, to)
        )

    def iterate(
        self,
        *,
        status: Optional[str] = None,
        type: Optional[str] = None,  # noqa: A002
        limit: Optional[int] = 20,
        cursor: Optional[str] = None,
        from_: Optional[str] = None,
        to: Optional[str] = None,
    ) -> Iterator[Model]:
        """Duyệt **mọi** job qua nhiều trang, tự đi theo `next_cursor`.

        ```python
        for job in client.jobs.iterate(status="succeeded"):
            print(job.id, job.cost)
        ```

        Chỉ gọi trang tiếp theo khi bạn thật sự duyệt tới đó, nên `break` sớm là
        dừng luôn, không tốn thêm request nào.
        """
        while True:
            page = self.list(
                status=status, type=type, limit=limit, cursor=cursor, from_=from_, to=to
            )
            for item in page_items(page):
                yield item
            cursor = next_cursor(page)
            if cursor is None:
                return

    def cancel(self, job_id: str) -> Model:
        """`POST /v1/jobs/{id}/cancel` — tiền tạm giữ được hoàn lại đầy đủ."""
        job_id = validate_job_id(job_id)
        return self._client.request("POST", "/v1/jobs/" + job_id + "/cancel")

    def wait(
        self,
        job_id: str,
        *,
        timeout: float = DEFAULT_WAIT_TIMEOUT,
        poll_interval: Optional[float] = None,
        on_progress: Optional[ProgressCallback] = None,
        raise_on_failure: bool = True,
        estimated_seconds: Optional[float] = None,
    ) -> Model:
        """Chờ tới khi job kết thúc — SDK_SPEC §6.

        Ném `JobTimeoutError` khi quá `timeout` (kèm job cuối cùng đọc được), và
        `JobFailedError` khi job `failed` / `cancelled` / `rejected`.
        """
        job_id = validate_job_id(job_id)
        started = time.monotonic()
        delays = poll_delays(estimated_seconds, poll_interval)
        job: Optional[Model] = None

        while True:
            elapsed = time.monotonic() - started
            remaining = timeout - elapsed
            if remaining <= 0:
                raise _timeout_error(job, job_id, elapsed)
            delay = min(next(delays), remaining)
            if delay > 0:
                time.sleep(delay)

            job = self.retrieve(job_id)
            if on_progress is not None:
                on_progress(job)
            if job.get("status") in TERMINAL_JOB_STATUSES:
                break
            if time.monotonic() - started >= timeout:
                raise _timeout_error(job, job_id, time.monotonic() - started)

        if job.get("status") != "succeeded" and raise_on_failure:
            raise build_job_failed_error(job)
        return job

    def stream(self, job_id: str, *, timeout: Optional[float] = None) -> Iterator[Model]:
        """`GET /v1/jobs/{id}/events` (SSE) — sinh từng sự kiện tiến độ.

        Tự dừng khi gặp `job.succeeded` / `job.failed`.
        """
        job_id = validate_job_id(job_id)
        for sse in self._client.stream_request(
            "GET", "/v1/jobs/" + job_id + "/events", timeout=timeout
        ):
            if not sse.data or sse.data == "[DONE]":
                continue
            event = _parse_event(sse.data, sse.event)
            if event is None:
                continue
            yield event
            if _is_stream_finished(event):
                return


class AsyncJobs:
    """Bản bất đồng bộ — cùng bề mặt, chỉ thêm `await`."""

    def __init__(self, client: "AsyncShopAPI") -> None:
        self._client = client

    async def retrieve(self, job_id: str) -> Model:
        """`GET /v1/jobs/{id}`."""
        job_id = validate_job_id(job_id)
        return await self._client.request("GET", "/v1/jobs/" + job_id)

    async def list(
        self,
        *,
        status: Optional[str] = None,
        type: Optional[str] = None,  # noqa: A002
        limit: Optional[int] = 20,
        cursor: Optional[str] = None,
        from_: Optional[str] = None,
        to: Optional[str] = None,
    ) -> Model:
        """`GET /v1/jobs`."""
        return await self._client.request(
            "GET", "/v1/jobs", params=_list_params(status, type, limit, cursor, from_, to)
        )

    async def iterate(
        self,
        *,
        status: Optional[str] = None,
        type: Optional[str] = None,  # noqa: A002
        limit: Optional[int] = 20,
        cursor: Optional[str] = None,
        from_: Optional[str] = None,
        to: Optional[str] = None,
    ) -> AsyncIterator[Model]:
        """Duyệt mọi job qua nhiều trang.

        ```python
        async for job in client.jobs.iterate(status="succeeded"):
            print(job.id)
        ```
        """
        while True:
            page = await self.list(
                status=status, type=type, limit=limit, cursor=cursor, from_=from_, to=to
            )
            for item in page_items(page):
                yield item
            cursor = next_cursor(page)
            if cursor is None:
                return

    async def cancel(self, job_id: str) -> Model:
        """`POST /v1/jobs/{id}/cancel`."""
        job_id = validate_job_id(job_id)
        return await self._client.request("POST", "/v1/jobs/" + job_id + "/cancel")

    async def wait(
        self,
        job_id: str,
        *,
        timeout: float = DEFAULT_WAIT_TIMEOUT,
        poll_interval: Optional[float] = None,
        on_progress: Optional[ProgressCallback] = None,
        raise_on_failure: bool = True,
        estimated_seconds: Optional[float] = None,
    ) -> Model:
        """Chờ tới khi job kết thúc — SDK_SPEC §6."""
        job_id = validate_job_id(job_id)
        started = time.monotonic()
        delays = poll_delays(estimated_seconds, poll_interval)
        job: Optional[Model] = None

        while True:
            elapsed = time.monotonic() - started
            remaining = timeout - elapsed
            if remaining <= 0:
                raise _timeout_error(job, job_id, elapsed)
            delay = min(next(delays), remaining)
            if delay > 0:
                await asyncio.sleep(delay)

            job = await self.retrieve(job_id)
            if on_progress is not None:
                result = on_progress(job)
                if asyncio.iscoroutine(result):
                    await result
            if job.get("status") in TERMINAL_JOB_STATUSES:
                break
            if time.monotonic() - started >= timeout:
                raise _timeout_error(job, job_id, time.monotonic() - started)

        if job.get("status") != "succeeded" and raise_on_failure:
            raise build_job_failed_error(job)
        return job

    async def stream(self, job_id: str, *, timeout: Optional[float] = None) -> AsyncIterator[Model]:
        """`GET /v1/jobs/{id}/events` (SSE) — `async for event in client.jobs.stream(id)`."""
        job_id = validate_job_id(job_id)
        async for sse in self._client.stream_request(
            "GET", "/v1/jobs/" + job_id + "/events", timeout=timeout
        ):
            if not sse.data or sse.data == "[DONE]":
                continue
            event = _parse_event(sse.data, sse.event)
            if event is None:
                continue
            yield event
            if _is_stream_finished(event):
                return
