"""Kiểm tra phía client — SDK_SPEC §3.

Chặn các lỗi hiển nhiên TRƯỚC khi tốn một vòng mạng, ném `InvalidRequestError`
với thông điệp tiếng Việt nói rõ giá trị nào sai và giá trị nào được chấp nhận.
"""

from __future__ import annotations

import re
from typing import Any, Optional, Sequence

from ._constants import (
    ASPECT_RATIOS,
    AUDIO_FORMATS,
    DEFAULT_VIDEO_DURATION_BY_ENGINE,
    MAX_IMAGES_PER_JOB,
    MAX_PROMPT_LENGTH,
    MAX_REFERENCE_IMAGES,
    MAX_SPEED,
    MAX_TEXT_LENGTH,
    MIN_SPEED,
    VIDEO_DURATIONS_BY_ENGINE,
    VIDEO_ENGINES,
    VOICE_CATALOG,
)
from ._exceptions import InvalidRequestError
from ._money import group_thousands

#: Khuôn `voice_id` thật của ElevenLabs — đúng 20 ký tự chữ và số.
#: Phải khớp `ELEVENLABS_VOICE_ID` ở `apps/api/src/modules/jobs/job.schemas.ts`;
#: lệch nhau nghĩa là SDK từ chối thứ máy chủ nhận, hoặc ngược lại.
_ELEVENLABS_VOICE_ID = re.compile(r"^[A-Za-z0-9]{20}$")

__all__ = [
    "validate_text",
    "validate_prompt",
    "validate_speed",
    "validate_audio_format",
    "validate_voice_id",
    "validate_image_count",
    "validate_aspect_ratio",
    "validate_reference_images",
    "validate_engine",
    "default_video_duration",
    "validate_video_duration",
    "validate_webhook_url",
    "validate_job_id",
]


def _fail(message: str, param: str) -> "InvalidRequestError":
    return InvalidRequestError(message, status=400, code="invalid_request", param=param)


def _quote_list(values: Sequence[Any]) -> str:
    return ", ".join(str(v) for v in values)


def validate_text(text: Any, *, param: str = "text") -> str:
    """1..100.000 ký tự — CONTRACT.md §2.1."""
    if not isinstance(text, str):
        raise _fail(
            "Trường `{0}` phải là chuỗi, bạn đang truyền {1}.".format(param, type(text).__name__), param
        )
    if not text.strip():
        raise _fail("Bạn cần nhập nội dung cần đọc — `{0}` đang rỗng.".format(param), param)
    if len(text) > MAX_TEXT_LENGTH:
        raise _fail(
            "Nội dung tối đa {0} ký tự, bạn đang gửi {1} ký tự. "
            "Bạn hãy chia thành nhiều job nhỏ hơn giúp mình.".format(
                group_thousands(MAX_TEXT_LENGTH), group_thousands(len(text))
            ),
            param,
        )
    return text


def validate_prompt(prompt: Any, *, param: str = "prompt") -> str:
    """1..8.000 ký tự — đúng bằng trần của máy chủ, không chặt hơn.

    Chặn chặt hơn máy chủ là tự tay từ chối một yêu cầu hoàn toàn hợp lệ: khách
    gõ 6.000 ký tự, SDK báo lỗi, còn cURL thì chạy ngon.
    """
    if not isinstance(prompt, str):
        raise _fail(
            "Trường `{0}` phải là chuỗi, bạn đang truyền {1}.".format(param, type(prompt).__name__), param
        )
    if not prompt.strip():
        raise _fail("Bạn cần mô tả thứ muốn tạo — `{0}` đang rỗng.".format(param), param)
    if len(prompt) > MAX_PROMPT_LENGTH:
        raise _fail(
            "Mô tả tối đa {0} ký tự, bạn đang gửi {1} ký tự. Bạn rút gọn giúp mình.".format(
                MAX_PROMPT_LENGTH, len(prompt)
            ),
            param,
        )
    return prompt


def validate_speed(speed: Any, *, param: str = "speed") -> float:
    """LUÔN từ chối — API đã bỏ tham số này ngày 06/08/2026.

    Model giọng đọc đang chạy (`eleven_v3`) bỏ qua hoàn toàn `voice_settings.
    speed`. Đo thật, cùng câu cùng giọng, quét hết khoảng từng hợp lệ:

        0.5 → 7,549s   │   1.0 → 6,922s   │   2.0 → 7,079s

    Dao động ±5% là nhiễu, không phải tác dụng. Trước đây SDK vẫn nhận và gửi
    lên, còn máy chủ thì CHIA TIỀN cho nó — ba job cùng một câu ở ba tốc độ trả
    về ba file gần như giống hệt nhau mà giá chênh nhau 17%.

    Ném lỗi NGAY TẠI MÁY KHÁCH thay vì để máy chủ trả 400: nhanh hơn một vòng
    mạng, và câu lỗi nói được đúng dòng nào trong code họ cần sửa.
    """
    raise _fail(
        "Model giọng đọc hiện tại (eleven_v3) không hỗ trợ chỉnh tốc độ, nên "
        "shopapi không nhận tham số `{0}` nữa — bỏ nó ra khỏi lời gọi là chạy "
        "được. Muốn đọc nhanh/chậm thì xử lý ở phía bạn sau khi nhận file "
        "audio.".format(param),
        param,
    )


def validate_audio_format(fmt: Any, *, param: str = "format") -> str:
    if fmt not in AUDIO_FORMATS:
        raise _fail(
            "Định dạng audio `{0}` chưa được hỗ trợ. Bạn chọn một trong: {1}.".format(
                fmt, _quote_list(AUDIO_FORMATS)
            ),
            param,
        )
    return str(fmt)


def validate_voice_id(voice_id: Any, *, param: str = "voice_id") -> str:
    """Kiểm khuôn `voice_id` NGAY TẠI MÁY KHÁCH, trước khi tốn một lượt mạng.

    Nhận hai dạng (giống hệt máy chủ — xem `ELEVENLABS_VOICE_ID` trong
    `apps/api/src/modules/jobs/job.schemas.ts`):
      1. mã ngắn của shopapi, ví dụ ``vi_female_01``;
      2. ``voice_id`` thật của ElevenLabs — đúng 20 ký tự chữ và số.

    Kiểm ở đây không thay thế máy chủ, nó chỉ báo lỗi SỚM HƠN: người viết code
    biết mình gõ sai ngay lúc chạy thử, thay vì sau một vòng gọi mạng.
    """
    if not isinstance(voice_id, str) or not voice_id.strip():
        raise _fail(
            "`{0}` phải là chuỗi không rỗng.".format(param), param,
        )
    ma = voice_id.strip()
    hop_le = (any(v["id"] == ma for v in VOICE_CATALOG)
              or _ELEVENLABS_VOICE_ID.match(ma) is not None)
    if not hop_le:
        raise _fail(
            "`{0}` không đúng khuôn. Dùng một trong hai cách: "
            "(1) mã có sẵn của shopapi — xem `shopapi.VOICE_CATALOG`; hoặc "
            "(2) voice_id thật của ElevenLabs, đúng 20 ký tự chữ và số "
            "(ví dụ \"NOpBlnGInO9m6vDvFkFC\"). Lấy id tại "
            "elevenlabs.io/app/voice-library: mở giọng bạn thích, bấm nút ba "
            "chấm rồi chọn \"Copy voice ID\".".format(param),
            param,
        )
    return ma


def validate_image_count(n: Any, *, param: str = "n") -> int:
    """1..8 — mỗi ảnh tính tiền riêng."""
    if isinstance(n, bool) or not isinstance(n, int):
        raise _fail("Số lượng ảnh `{0}` phải là số nguyên từ 1 đến {1}.".format(param, MAX_IMAGES_PER_JOB), param)
    if n < 1 or n > MAX_IMAGES_PER_JOB:
        raise _fail(
            "Mỗi lần tạo được từ 1 đến {0} ảnh, bạn đang yêu cầu {1}. "
            "Mỗi ảnh tính tiền riêng nên bạn cân nhắc giúp mình.".format(MAX_IMAGES_PER_JOB, n),
            param,
        )
    return n


def validate_aspect_ratio(ratio: Any, *, param: str = "aspect_ratio") -> str:
    if ratio not in ASPECT_RATIOS:
        raise _fail(
            "Tỉ lệ khung hình `{0}` chưa được hỗ trợ. Bạn chọn một trong: {1}.".format(
                ratio, _quote_list(ASPECT_RATIOS)
            ),
            param,
        )
    return str(ratio)


def validate_reference_images(images: Any, *, param: str = "reference_images") -> list:
    """Tối đa ``MAX_REFERENCE_IMAGES`` (10) ảnh tham chiếu."""
    if isinstance(images, (str, bytes)) or not isinstance(images, (list, tuple)):
        raise _fail("`{0}` phải là danh sách đường dẫn ảnh.".format(param), param)
    items = list(images)
    if len(items) > MAX_REFERENCE_IMAGES:
        raise _fail(
            "Tối đa {0} ảnh tham chiếu, bạn đang gửi {1}. Bạn bớt bớt giúp mình.".format(
                MAX_REFERENCE_IMAGES, len(items)
            ),
            param,
        )
    for item in items:
        if not isinstance(item, str) or not item.strip():
            raise _fail("Mỗi ảnh tham chiếu phải là một đường dẫn https hợp lệ.", param)
    return items


def validate_engine(engine: Any, *, param: str = "engine") -> str:
    if engine not in VIDEO_ENGINES:
        raise _fail(
            "Engine `{0}` không tồn tại. Bạn chọn một trong: {1}. "
            'Nên để "auto" để hệ thống tự chọn máy rảnh nhất.'.format(engine, _quote_list(VIDEO_ENGINES)),
            param,
        )
    return str(engine)


def default_video_duration(engine: str) -> int:
    """Thời lượng mặc định của một engine, khi khách không truyền `duration`.

    ⚠ KHÔNG được mặc định cứng 8 giây cho mọi engine: Seedance chỉ bán clip 10
    giây, nên ``videos.create(prompt=..., engine="seedance")`` với mặc định 8 là
    **luôn luôn lỗi**.
    """
    return DEFAULT_VIDEO_DURATION_BY_ENGINE.get(engine, 8)


def validate_video_duration(engine: str, duration: Any, *, param: str = "duration") -> int:
    """veo3 chỉ 8 giây; seedance chỉ 10; auto nhận 8 hoặc 10.

    Clip 5 giây đã NGỪNG BÁN (mỗi clip dù dài ngắn đều tiêu một lượt trong hạn
    mức 2 video/gmail/ngày), nên không nhắc tới số 5 ở bất kỳ đâu nữa — vừa từ
    chối số 5 vừa mời khách chọn số 5 là mâu thuẫn khách nhìn thấy ngay.
    """
    if isinstance(duration, bool) or not isinstance(duration, int):
        raise _fail("Thời lượng video `{0}` phải là số nguyên (giây).".format(param), param)
    allowed = VIDEO_DURATIONS_BY_ENGINE.get(engine)
    if allowed is None:
        raise _fail(
            "Engine `{0}` không tồn tại. Bạn chọn một trong: {1}.".format(engine, _quote_list(VIDEO_ENGINES)),
            "engine",
        )
    if duration not in allowed:
        raise _fail(
            "Engine {0} chỉ nhận video {1} giây, bạn đang chọn {2} giây. Bạn chọn lại giúp mình.".format(
                engine, " hoặc ".join(str(v) for v in allowed), duration
            ),
            param,
        )
    return duration


def validate_webhook_url(url: Any, *, param: str = "webhook_url") -> str:
    if not isinstance(url, str) or not url.strip():
        raise _fail("`{0}` phải là một đường dẫn https hợp lệ.".format(param), param)
    if not (url.startswith("https://") or url.startswith("http://localhost")):
        raise _fail(
            "`{0}` phải dùng https (chỉ http://localhost được phép khi phát triển). "
            "Bạn đang truyền: {1}".format(param, url),
            param,
        )
    return url


def validate_job_id(job_id: Any, *, param: str = "job_id") -> str:
    if not isinstance(job_id, str) or not job_id.strip():
        raise _fail(
            "`{0}` phải là mã job, ví dụ \"job_x7k2m9p4qr8s\". "
            "Bạn lấy nó từ `job.id` khi tạo job.".format(param),
            param,
        )
    return job_id.strip()


def optional(value: Optional[Any], validator: Any, **kwargs: Any) -> Optional[Any]:
    """Chạy `validator` chỉ khi giá trị khác `None`."""
    if value is None:
        return None
    return validator(value, **kwargs)
