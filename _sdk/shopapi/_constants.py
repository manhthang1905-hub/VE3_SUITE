"""Hằng số chép nguyên từ `packages/contracts/src/*.ts` — nguồn sự thật là
`docs/CONTRACT.md` và `docs/PRICING.md`.

Đổi bất kỳ giá trị nào ở đây là phá vỡ hợp đồng với API, phải báo trước.
"""

from __future__ import annotations

from typing import Dict, Tuple

__all__ = [
    "DEFAULT_BASE_URL",
    "BILLING_URL",
    "ASPECT_RATIOS",
    "AUDIO_FORMATS",
    "VIDEO_ENGINES",
    "VIDEO_DURATIONS_BY_ENGINE",
    "DEFAULT_VIDEO_DURATION_BY_ENGINE",
    "SERVICE_TYPES",
    "JOB_STATUSES",
    "TERMINAL_JOB_STATUSES",
    "JOB_EVENT_TYPES",
    "WEBHOOK_EVENTS",
    "DEFAULT_VOICE_ID",
    "VOICE_CATALOG",
    "MAX_TEXT_LENGTH",
    "MAX_PROMPT_LENGTH",
    "MAX_REFERENCE_IMAGES",
    "MAX_IMAGES_PER_JOB",
    "MIN_SPEED",
    "MAX_SPEED",
    "RATE_LIMIT_TIERS",
    "RATE_LIMITS",
    "SIGNATURE_HEADER",
    "EVENT_HEADER",
    "IDEMPOTENCY_HEADER",
    "RATE_LIMIT_HEADERS",
    "OUTPUT_RETENTION_DAYS",
    "UNIT_PRICE_MICRO",
    "VIDEO_UNIT_PRICE_MICRO",
    "RETAIL_PRICE_VND",
    "MIN_TOPUP_VND",
    "MAX_TOPUP_VND",
    "TYPICAL_SECONDS",
]

#: CONTRACT.md §0
DEFAULT_BASE_URL = "https://api.shopapi.vn"

#: Trang nạp tiền — nhắc trong thông điệp lỗi số dư.
BILLING_URL = "https://shopapi.vn/dashboard/billing"

# ── Tham số tạo job — CONTRACT.md §2.1 ────────────────────────────────────────

ASPECT_RATIOS: Tuple[str, ...] = ("16:9", "9:16", "1:1", "4:3", "3:4")
AUDIO_FORMATS: Tuple[str, ...] = ("mp3", "wav")
VIDEO_ENGINES: Tuple[str, ...] = ("auto", "veo3", "seedance")

#: Thời lượng video hợp lệ theo engine. `auto` nhận mọi thời lượng của cả hai.
VIDEO_DURATIONS_BY_ENGINE: Dict[str, Tuple[int, ...]] = {
    "veo3": (8,),
    # Chỉ bán clip 10 giây — xem docs/PRICING.md mục 2.
    "seedance": (10,),
    "auto": (8, 10),
}

#: Thời lượng mặc định khi bạn không truyền `duration`.
#:
#: ⚠ PHẢI suy theo engine. Mặc định cứng 8 giây cho mọi engine làm
#: ``client.videos.create(prompt=..., engine="seedance")`` **luôn luôn lỗi**, vì
#: Seedance chỉ bán clip 10 giây — người dùng chỉ đích danh engine mình muốn rồi
#: bị SDK từ chối, và phải tự đoán ra rằng còn thiếu ``duration=10``.
#:
#: Quy ước: lấy mức NGẮN NHẤT engine đó bán — rẻ nhất cho khách khi chưa nói rõ.
DEFAULT_VIDEO_DURATION_BY_ENGINE: Dict[str, int] = {
    "veo3": 8,
    "seedance": 10,
    "auto": 8,
}

SERVICE_TYPES: Tuple[str, ...] = ("tts", "image", "video")

# ── Trạng thái job — CONTRACT.md §2.2 ─────────────────────────────────────────

JOB_STATUSES: Tuple[str, ...] = (
    "queued",
    "running",
    "retrying",
    "succeeded",
    "failed",
    "cancelled",
    "rejected",
)

#: Trạng thái kết thúc, không đổi được nữa.
TERMINAL_JOB_STATUSES: Tuple[str, ...] = ("succeeded", "failed", "cancelled", "rejected")

JOB_EVENT_TYPES: Tuple[str, ...] = ("job.queued", "job.progress", "job.succeeded", "job.failed")

WEBHOOK_EVENTS: Tuple[str, ...] = ("job.succeeded", "job.failed", "job.progress")

# ── Giọng đọc — contracts/src/jobs.ts VOICE_CATALOG ───────────────────────────

DEFAULT_VOICE_ID = "vi_female_01"

#: Danh mục giọng đọc tiếng Việt. `region`: bac | trung | nam.
VOICE_CATALOG: Tuple[Dict[str, str], ...] = (
    {
        "id": "vi_female_01",
        "name": "Ngọc Anh",
        "gender": "female",
        "region": "bac",
        "description": "Nữ miền Bắc, trong trẻo — hợp đọc tin tức, thuyết minh",
    },
    {
        "id": "vi_female_02",
        "name": "Thu Hà",
        "gender": "female",
        "region": "bac",
        "description": "Nữ miền Bắc, trầm ấm — hợp kể chuyện, audiobook",
    },
    {
        "id": "vi_male_01",
        "name": "Minh Quân",
        "gender": "male",
        "region": "bac",
        "description": "Nam miền Bắc, chắc khoẻ — hợp quảng cáo, giới thiệu",
    },
    {
        "id": "vi_female_03",
        "name": "Mỹ Duyên",
        "gender": "female",
        "region": "nam",
        "description": "Nữ miền Nam, gần gũi — hợp review, TikTok",
    },
    {
        "id": "vi_male_02",
        "name": "Hoàng Nam",
        "gender": "male",
        "region": "nam",
        "description": "Nam miền Nam, thân thiện — hợp video bán hàng",
    },
    {
        "id": "vi_female_04",
        "name": "Diệu Linh",
        "gender": "female",
        "region": "trung",
        "description": "Nữ miền Trung, nhẹ nhàng — hợp nội dung du lịch",
    },
)

# ── Giới hạn đầu vào — CONTRACT.md §2.1 ───────────────────────────────────────

MAX_TEXT_LENGTH = 100_000

#: Số ký tự tối đa của ``prompt`` (ảnh và video).
#:
#: ⚠ Phải bằng ĐÚNG máy chủ (``imageSchema``/``videoSchema`` trong
#: ``apps/api/src/modules/jobs/job.schemas.ts``). Trước đây SDK chặn ở 5.000
#: trong khi máy chủ nhận 8.000: cùng một prompt 6.000 ký tự thì gọi bằng cURL
#: chạy ngon còn gọi bằng SDK báo lỗi — người ta kết luận SDK hỏng và gỡ đi.
MAX_PROMPT_LENGTH = 8_000
#: Số ảnh tham chiếu tối đa mỗi job ảnh.
#:
#: ⚠ Cùng lý do như ``MAX_PROMPT_LENGTH`` ngay trên: phải bằng ĐÚNG
#: ``MAX_REFERENCE_IMAGES`` của máy chủ trong
#: ``apps/api/src/modules/jobs/job.schemas.ts``. SDK chặn chặt hơn máy chủ là tự
#: tay từ chối một yêu cầu hoàn toàn hợp lệ; SDK dễ dãi hơn máy chủ thì khách
#: qua được SDK rồi ăn 400 từ máy chủ. Cả hai kiểu lệch đều khó lần vì mỗi bên
#: đều "đúng theo mã của mình" — nên có bài kiểm khoá hai con số này bằng nhau
#: ở ``tests/test_validation.py``.
#:
#: ⚠ File này có BẢN SAO y hệt ở ``tools/shopapi-studio/_sdk/shopapi/``. Sửa ở
#: đây thì phải chép sang đó, nếu không Studio và SDK sẽ chặn khác nhau.
MAX_REFERENCE_IMAGES = 10
MAX_IMAGES_PER_JOB = 8
MIN_SPEED = 0.5
MAX_SPEED = 2.0

# ── Giới hạn tần suất — CONTRACT.md §8 ────────────────────────────────────────

RATE_LIMIT_TIERS: Tuple[str, ...] = ("free", "starter", "pro", "business")

#: `jobs_per_day = None` nghĩa là không giới hạn.
#:
#: .. warning::
#:    KHÔNG có ``concurrent_jobs`` ở đây, và đó là chủ ý. Số job chạy song song
#:    **không phải hằng số theo hạng** mà là số đo của nhà máy tại thời điểm
#:    gọi: nó co giãn theo sức chứa còn trống và khác nhau ở từng loại job.
#:    Đọc con số thật ở ``GET /v1/me`` → ``limits["concurrent_jobs"][<loại>]``,
#:    đừng gõ cứng. Bảng cũ ở tệp này từng ghi ``business: 100 job cùng lúc``
#:    trong khi máy chủ chỉ cho 1.
#:
#:    Và **đừng bắn đúng bằng trần**: trần là mức KHÔNG ĐƯỢC VƯỢT, không phải
#:    mức PHẢI CHẠY. Máy chủ biết nhà máy còn mấy chỗ, nhưng không biết đường
#:    mạng của bạn — nên hãy tự dò: chạy trơn thì +1 mỗi lô, gặp ``429`` thì chia
#:    đôi, gặp ``503`` thì dừng rồi thăm dò lại bằng một job. Xem ``_nhip_do.py``.
#:
#:    Không muốn tự viết vòng lặp thì dùng ``client.chay_ca_me(loai, cong_viec)``
#:    — nó đã có sẵn cả vòng tự dò nhịp ở trên và không bao giờ bỏ việc của bạn.
RATE_LIMITS: Dict[str, Dict[str, object]] = {
    "free": {"requests_per_minute": 10, "jobs_per_day": 20, "max_queued": 5, "label": "Miễn phí"},
    "starter": {"requests_per_minute": 120, "jobs_per_day": None, "max_queued": 200, "label": "Khởi động"},
    "pro": {"requests_per_minute": 300, "jobs_per_day": None, "max_queued": 1_000, "label": "Chuyên nghiệp"},
    "business": {"requests_per_minute": 1_000, "jobs_per_day": None, "max_queued": 5_000, "label": "Doanh nghiệp"},
}

#: Trần CỨNG tuyệt đối của số job chạy song song, theo từng loại job — mức mà
#: trần động không bao giờ vượt qua. KHÔNG phải trần gặp hằng ngày.
CONCURRENCY_HARD_CAP: Dict[str, int] = {"tts": 16, "image": 384, "video": 64}

# ── Header — CONTRACT.md §2.1, §5, §8 ─────────────────────────────────────────

SIGNATURE_HEADER = "X-ShopAPI-Signature"
EVENT_HEADER = "X-ShopAPI-Event"
IDEMPOTENCY_HEADER = "Idempotency-Key"

RATE_LIMIT_HEADERS = {
    "limit": "X-RateLimit-Limit",
    "remaining": "X-RateLimit-Remaining",
    "reset": "X-RateLimit-Reset",
}

#: Link kết quả sống 7 ngày rồi bị xoá — CONTRACT.md §2.2.
OUTPUT_RETENTION_DAYS = 7

# ── Giá — PRICING.md §2 ───────────────────────────────────────────────────────

#: µVND cho mỗi đơn vị tính tiền.
#:
#: ⚠ Khoá ``video`` là mức THẤP NHẤT (Veo3). **Video có HAI giá.** Báo giá video
#: cho khách thì tra :data:`VIDEO_UNIT_PRICE_MICRO` theo engine, đừng dùng khoá
#: này — dùng nhầm là báo bằng nửa giá thật cho mỗi clip Seedance.
UNIT_PRICE_MICRO: Dict[str, str] = {
    "tts": "3333333",       # mỗi giây audio thật
    "image": "100000000",   # mỗi ảnh
    "video": "500000000",   # Veo3, clip 8 giây — KHÔNG phải giá chung của video
}

#: Giá video theo ENGINE (µVND mỗi video) — nguồn đúng khi cần báo giá.
#:
#: Seedance đắt gấp đôi vì giá vốn gấp đôi: dola.com chặn cứng 2 video/gmail/ngày
#: nên mỗi clip "ăn" nửa vòng đời một gmail, trong khi một gmail Veo3 chạy được
#: nhiều hơn hẳn.
VIDEO_UNIT_PRICE_MICRO: Dict[str, str] = {
    "veo3": "500000000",       # 500₫, clip 8 giây
    "seedance": "1000000000",  # 1.000₫, clip 10 giây
}

#: Giá niêm yết để hiển thị (đồng).
RETAIL_PRICE_VND: Dict[str, int] = {
    "voice_per_minute": 200,
    "image_per_image": 100,
    #: 500₫ / video Veo3 (clip 8 giây)
    "video_veo3": 500,
    #: 1.000₫ / video Seedance (clip 10 giây)
    "video_seedance": 1000,
    #: Giữ lại tên cũ, trỏ vào mức THẤP NHẤT, để code cũ không gãy đột ngột.
    #: Cần đúng giá thì đọc hai khoá ở trên.
    "video_per_video": 500,
}

#: PRICING.md §5 — không có tín dụng tặng lúc đăng ký; ví mới 0₫, nạp tối
#: thiểu 50.000₫ mới tạo được job.
MIN_TOPUP_VND = 50_000

#: Trần một lần nạp (đồng) — khớp ``MAX_TOPUP_MICROS`` của máy chủ.
#:
#: Cũng là lưới bắt lỗi đơn vị: ai lỡ gửi µVND theo thói quen
#: (``vnd_to_micro(500_000)`` = 500.000.000.000) sẽ vượt trần này gấp nghìn lần
#: và nhận đúng câu giải thích, thay vì một lỗi 400 khó hiểu từ máy chủ.
MAX_TOPUP_VND = 500_000_000

#: Thời gian xử lý điển hình (giây) — số trung bình từ `GET /v1/stats`.
TYPICAL_SECONDS: Dict[str, int] = {"tts": 42, "image": 18, "video": 95}
