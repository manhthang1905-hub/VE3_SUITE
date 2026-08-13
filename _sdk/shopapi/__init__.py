"""SDK Python chính thức cho ShopAPI — giọng nói, ảnh và video tiếng Việt.

```python
import os
from shopapi import ShopAPI

client = ShopAPI(api_key=os.environ["SHOPAPI_KEY"])
audio = client.tts.create_and_wait(text="Xin chào")
print(audio.output.url)
```

Bản bất đồng bộ có ĐÚNG cùng bề mặt, chỉ thêm `await`:

```python
from shopapi import AsyncShopAPI

async with AsyncShopAPI() as client:
    audio = await client.tts.create_and_wait(text="Xin chào")
```
"""

from __future__ import annotations

from . import webhooks
from ._client import AsyncShopAPI, BaseClient, ShopAPI, parse_retry_after
from ._nhip_do import NhipDo, cho_hang_doi_cua
from ._constants import (
    ASPECT_RATIOS,
    AUDIO_FORMATS,
    DEFAULT_BASE_URL,
    DEFAULT_VIDEO_DURATION_BY_ENGINE,
    DEFAULT_VOICE_ID,
    JOB_STATUSES,
    MAX_IMAGES_PER_JOB,
    MAX_PROMPT_LENGTH,
    MAX_REFERENCE_IMAGES,
    MAX_TEXT_LENGTH,
    MAX_TOPUP_VND,
    MIN_TOPUP_VND,
    OUTPUT_RETENTION_DAYS,
    CONCURRENCY_HARD_CAP,
    RATE_LIMITS,
    RATE_LIMIT_TIERS,
    RETAIL_PRICE_VND,
    SERVICE_TYPES,
    TERMINAL_JOB_STATUSES,
    TYPICAL_SECONDS,
    UNIT_PRICE_MICRO,
    VIDEO_DURATIONS_BY_ENGINE,
    VIDEO_UNIT_PRICE_MICRO,
    VIDEO_ENGINES,
    VOICE_CATALOG,
    WEBHOOK_EVENTS,
)
from ._exceptions import (
    AccountSuspendedError,
    APIConnectionError,
    APIStatusError,
    APITimeoutError,
    AuthenticationError,
    ConflictError,
    ContentRejectedError,
    EngineUnavailableError,
    IdempotencyConflictError,
    InsufficientBalanceError,
    InternalServerError,
    InvalidRequestError,
    JobFailedError,
    JobTimeoutError,
    NotFoundError,
    PermissionDeniedError,
    RateLimitError,
    ServiceUnavailableError,
    ShopAPIError,
    SignatureVerificationError,
    UnsupportedParameterError,
)
from ._models import Model, RateLimit
from ._money import (
    MICRO_PER_VND,
    add_micro,
    compare_micro,
    format_micro_vnd,
    format_vnd,
    group_thousands,
    micro_to_vnd,
    micro_to_vnd_exact,
    sub_micro,
    vnd_to_micro,
)
from ._polling import poll_delays
from ._sse import SSEDecoder, SSEEvent
from ._version import USER_AGENT, __version__

__all__ = [
    # Client
    "ShopAPI",
    "AsyncShopAPI",
    "BaseClient",
    "NhipDo",
    "cho_hang_doi_cua",
    # Kiểu trả về
    "Model",
    "RateLimit",
    "SSEEvent",
    "SSEDecoder",
    # Ngoại lệ
    "ShopAPIError",
    "APIConnectionError",
    "APITimeoutError",
    "APIStatusError",
    "InvalidRequestError",
    "AuthenticationError",
    "InsufficientBalanceError",
    "ContentRejectedError",
    "PermissionDeniedError",
    "AccountSuspendedError",
    "NotFoundError",
    "ConflictError",
    "IdempotencyConflictError",
    "UnsupportedParameterError",
    "RateLimitError",
    "EngineUnavailableError",
    "ServiceUnavailableError",
    "InternalServerError",
    "JobFailedError",
    "JobTimeoutError",
    "SignatureVerificationError",
    # Tiền
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
    # Webhook
    "webhooks",
    # Hằng số hợp đồng
    "DEFAULT_BASE_URL",
    "ASPECT_RATIOS",
    "AUDIO_FORMATS",
    "VIDEO_ENGINES",
    "VIDEO_DURATIONS_BY_ENGINE",
    "SERVICE_TYPES",
    "JOB_STATUSES",
    "TERMINAL_JOB_STATUSES",
    "WEBHOOK_EVENTS",
    "VOICE_CATALOG",
    "DEFAULT_VOICE_ID",
    "MAX_TEXT_LENGTH",
    "MAX_IMAGES_PER_JOB",
    "MAX_REFERENCE_IMAGES",
    "RATE_LIMIT_TIERS",
    "CONCURRENCY_HARD_CAP",
    "RATE_LIMITS",
    "UNIT_PRICE_MICRO",
    "VIDEO_UNIT_PRICE_MICRO",
    "DEFAULT_VIDEO_DURATION_BY_ENGINE",
    "MAX_PROMPT_LENGTH",
    "RETAIL_PRICE_VND",
    "MIN_TOPUP_VND",
    "MAX_TOPUP_VND",
    "OUTPUT_RETENTION_DAYS",
    "TYPICAL_SECONDS",
    # Tiện ích
    "poll_delays",
    "parse_retry_after",
    "USER_AGENT",
    "__version__",
]
