"""Client đồng bộ và bất đồng bộ — SDK_SPEC §1, §5.

`ShopAPI` dùng `httpx.Client`, `AsyncShopAPI` dùng `httpx.AsyncClient`. Hai lớp
có ĐÚNG cùng bề mặt API, chỉ khác chỗ phải `await`.
"""

from __future__ import annotations

import asyncio
import email.utils
import os
import random
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from types import TracebackType
from typing import (
    Any,
    AsyncIterator,
    Dict,
    Iterator,
    List,
    Mapping,
    Optional,
    Sequence,
    Type,
    Union,
)

import httpx

from ._constants import DEFAULT_BASE_URL, IDEMPOTENCY_HEADER
from ._exceptions import (
    MISSING_API_KEY_MESSAGE,
    APIConnectionError,
    APIStatusError,
    APITimeoutError,
    EngineUnavailableError,
    RateLimitError,
    ShopAPIError,
    build_status_error,
)
from ._models import Model, RateLimit
from ._nhip_do import NhipDo, cho_hang_doi_cua
from ._polling import poll_delays
from ._sse import SSEDecoder, SSEEvent
from ._version import USER_AGENT
from .resources import (
    AsyncBalance,
    AsyncImages,
    AsyncJobs,
    AsyncLedger,
    AsyncPricing,
    AsyncStats,
    AsyncTopup,
    AsyncTts,
    AsyncUploads,
    AsyncUsage,
    AsyncVideos,
    Balance,
    Images,
    Jobs,
    Ledger,
    Pricing,
    Stats,
    Topup,
    Tts,
    Uploads,
    Usage,
    Videos,
)
from .webhooks import AsyncWebhooks, Webhooks

__all__ = [
    "ShopAPI",
    "AsyncShopAPI",
    "BaseClient",
    "NhipDo",
    "poll_delays",
    "parse_retry_after",
]

#: Mặc định 60 giây mỗi request — SDK_SPEC §1.
DEFAULT_TIMEOUT = 60.0
#: Số lần THỬ LẠI, không tính lần đầu.
DEFAULT_MAX_RETRIES = 3
#: Giãn cách: `delay = min(cap, base * 2^attempt) * jitter` — SDK_SPEC §5.
RETRY_BASE_DELAY = 0.5
RETRY_MAX_DELAY = 8.0
#: Tôn trọng `Retry-After` tuyệt đối nhưng chặn trên ở 60 giây để không treo tiến trình.
RETRY_AFTER_CAP = 60.0


def parse_retry_after(value: Optional[str], *, now: Optional[float] = None) -> Optional[float]:
    """Đọc header `Retry-After` — hỗ trợ cả số giây lẫn HTTP-date."""
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    try:
        seconds = float(text)
    except ValueError:
        try:
            when = email.utils.parsedate_to_datetime(text)
        except (TypeError, ValueError):
            return None
        if when is None:
            return None
        reference = now if now is not None else time.time()
        seconds = when.timestamp() - reference
    if seconds < 0:
        return 0.0
    return seconds


class BaseClient:
    """Phần dùng chung giữa client đồng bộ và bất đồng bộ."""

    def __init__(
        self,
        api_key: Optional[str] = None,
        *,
        base_url: Optional[str] = None,
        timeout: float = DEFAULT_TIMEOUT,
        max_retries: int = DEFAULT_MAX_RETRIES,
        webhook_secret: Optional[str] = None,
        default_headers: Optional[Mapping[str, str]] = None,
        _retry_base_delay: float = RETRY_BASE_DELAY,
        _retry_max_delay: float = RETRY_MAX_DELAY,
        _retry_jitter: bool = True,
    ) -> None:
        resolved_key = api_key
        if resolved_key is None or not str(resolved_key).strip():
            resolved_key = os.environ.get("SHOPAPI_KEY") or os.environ.get("SHOPAPI_API_KEY")

        # KHÔNG ném lỗi ở đây: kiểm tra chữ ký webhook là phép tính cục bộ, không
        # cần API key. Bắt khai key ngay lúc khởi tạo sẽ ép người chỉ nhận webhook
        # phải bịa một key giả. Lỗi được ném ở lời gọi HTTP đầu tiên cần xác thực.
        #: API key `sk_live_...`, hoặc `None` nếu chưa khai.
        self.api_key: Optional[str] = (
            str(resolved_key).strip() if resolved_key and str(resolved_key).strip() else None
        )

        resolved_base = base_url or os.environ.get("SHOPAPI_BASE_URL") or DEFAULT_BASE_URL
        #: Base URL đã bỏ dấu `/` cuối.
        self.base_url: str = str(resolved_base).rstrip("/")

        self.timeout: float = float(timeout)
        self.max_retries: int = max(int(max_retries), 0)
        #: Bí mật ký webhook, dùng cho `client.webhooks.verify(...)`.
        self.webhook_secret: Optional[str] = (
            webhook_secret if webhook_secret is not None else os.environ.get("SHOPAPI_WEBHOOK_SECRET")
        )
        self.default_headers: Dict[str, str] = dict(default_headers or {})

        self._retry_base_delay = float(_retry_base_delay)
        self._retry_max_delay = float(_retry_max_delay)
        self._retry_jitter = bool(_retry_jitter)

        #: Giới hạn tần suất của phản hồi gần nhất — CONTRACT.md §8.
        self.last_rate_limit: Optional[RateLimit] = None

        #: Vòng TỰ DÒ NHỊP dùng chung cho client này — xem `_nhip_do.py`.
        #:
        #: Đặt ở đây (chứ không phải bên trong `chay_ca_me`) vì nó cần **thấy mọi
        #: request**, kể cả những cú ``429`` mà tầng HTTP đã tự nuốt bằng cách
        #: ngủ rồi thử lại. Nếu limiter chỉ sống trong vòng lặp mẻ thì nó sẽ vui
        #: vẻ tăng nhịp giữa lúc nhà máy đang ngộp, vì mọi tín hiệu nghẽn đã bị
        #: lớp retry hấp thụ hết trước khi tới nơi.
        self.nhip_do = NhipDo()

        #: Vòng dò RIÊNG cho từng loại job, dựng khi cần — xem `_nhip_theo_loai`.
        self._nhip_loai: Dict[str, NhipDo] = {}

    def _nhip_theo_loai(self, loai: str) -> NhipDo:
        """Vòng dò của một loại job, có NHỚ nhịp qua các lần chạy.

        ═══ VÌ SAO TÁCH THEO LOẠI ═══

        Ảnh và video không cùng năng lực: đo ngày 14/08/2026 trên cùng cỗ máy,
        kho ảnh 95 tài khoản × 32 luồng = 3.040 chỗ, kho video 10 tài khoản ×
        32 = 320 chỗ. Một vòng dò dùng chung sẽ mang mép vừa tìm được ở video
        (nhỏ) áp cho ảnh, hoặc ngược lại — và cái sai theo hướng "ngược lại"
        thì đặt tải video gấp mười lần nhà máy chịu được.

        ═══ VÌ SAO PHẢI NHỚ ═══

        Bên VE3_SUITE mỗi mã là một TIẾN TRÌNH RIÊNG, nên không có gì sống đủ
        lâu để học. Xem khối chú thích đầu `_nho_nhip.py` để có số đo.
        """
        vong = self._nhip_loai.get(loai)
        if vong is None:
            vong = NhipDo(nho_khoa=loai)
            self._nhip_loai[loai] = vong
        return vong

    @property
    def has_api_key(self) -> bool:
        """Client đã có API key chưa.

        Dựng client chỉ để kiểm tra chữ ký webhook thì thuộc tính này là `False`
        mà vẫn dùng được `client.webhooks.verify(...)` bình thường.
        """
        return bool(self.api_key)

    # ── Dựng request ─────────────────────────────────────────────────────────

    def _url(self, path: str) -> str:
        if path.startswith("http://") or path.startswith("https://"):
            return path
        return self.base_url + ("" if path.startswith("/") else "/") + path

    def _build_headers(
        self,
        *,
        auth: bool = True,
        idempotency_key: Optional[str] = None,
        extra: Optional[Mapping[str, str]] = None,
        accept: str = "application/json",
    ) -> Dict[str, str]:
        headers: Dict[str, str] = {
            "Accept": accept,
            "User-Agent": USER_AGENT,
        }
        headers.update(self.default_headers)
        if auth:
            if not self.api_key:
                raise ShopAPIError(MISSING_API_KEY_MESSAGE)
            headers["Authorization"] = "Bearer " + self.api_key
        if idempotency_key:
            headers[IDEMPOTENCY_HEADER] = idempotency_key
        if extra:
            headers.update({str(k): str(v) for k, v in extra.items()})
        return headers

    @staticmethod
    def _clean_params(params: Optional[Mapping[str, Any]]) -> Optional[Dict[str, Any]]:
        """Bỏ tham số `None` để không gửi `?cursor=None` lên server."""
        if not params:
            return None
        cleaned = {k: v for k, v in params.items() if v is not None}
        return cleaned or None

    # ── Xử lý phản hồi ───────────────────────────────────────────────────────

    def _remember_rate_limit(self, response: httpx.Response) -> Optional[RateLimit]:
        rate_limit = RateLimit.from_headers(response.headers)
        if rate_limit is not None:
            self.last_rate_limit = rate_limit
        return rate_limit

    @staticmethod
    def _decode_json(response: httpx.Response) -> Any:
        if response.status_code == 204 or not response.content:
            return {}
        try:
            return response.json()
        except ValueError:
            return None

    def _to_model(self, response: httpx.Response) -> Model:
        rate_limit = self._remember_rate_limit(response)
        data = self._decode_json(response)
        if data is None:
            data = {"raw": response.text}
        elif not isinstance(data, Mapping):
            data = {"data": data}
        request_id = response.headers.get("X-Request-Id") or response.headers.get("x-request-id")
        if isinstance(data, Mapping) and isinstance(data.get("request_id"), str):
            request_id = data["request_id"]
        return Model(data)._attach(
            rate_limit=rate_limit, request_id=request_id, status_code=response.status_code
        )

    def _build_error(self, response: httpx.Response) -> APIStatusError:
        rate_limit = self._remember_rate_limit(response)
        body = self._decode_json(response)
        text = None
        if body is None:
            try:
                text = response.text
            except Exception:  # pragma: no cover — thân phản hồi hỏng
                text = None
        retry_after = parse_retry_after(response.headers.get("Retry-After"))
        error = build_status_error(
            status=response.status_code,
            body=body,
            headers=dict(response.headers),
            retry_after=retry_after,
            text=text,
        )
        if error.request_id is None:
            error.request_id = response.headers.get("X-Request-Id")
        # Phơi bày giới hạn tần suất ngay trên ngoại lệ để khách khỏi phải tự đọc header.
        error.rate_limit = rate_limit  # type: ignore[attr-defined]
        return error

    # ── Thử lại ──────────────────────────────────────────────────────────────

    def _retry_delay(self, attempt: int, retry_after: Optional[float] = None) -> float:
        """`delay = min(cap, base * 2^attempt) * jitter`, tôn trọng `Retry-After`."""
        if retry_after is not None:
            return min(max(retry_after, 0.0), RETRY_AFTER_CAP)
        delay = min(self._retry_max_delay, self._retry_base_delay * (2**attempt))
        if self._retry_jitter:
            delay *= random.uniform(0.75, 1.25)
        return max(delay, 0.0)

    def _should_retry(self, error: APIStatusError, attempt: int) -> bool:
        return bool(getattr(error, "retryable", False)) and attempt < self.max_retries

    def _bao_nhip_do(self, error: APIStatusError) -> None:
        """Cho vòng tự dò nhịp NHÌN THẤY một lời từ chối.

        Gọi ở **mọi** phản hồi lỗi, kể cả những cú sắp được tầng retry nuốt gọn.
        Đó chính là điểm: một cú ``429`` được thử lại thành công vẫn là bằng
        chứng rằng ta đang đi nhanh hơn mức nhà máy chịu được, và vòng dò phải
        biết để hạ nhịp — nếu không nó sẽ tăng tiếp và ăn ``429`` dày hơn.
        """
        self.nhip_do.ghi_nhan_tu_choi(
            # `APIStatusError` đặt tên trường là `status`, KHÔNG phải
            # `status_code` — đọc nhầm tên thì `getattr` trả `None` một cách im
            # lặng và cả vòng dò không bao giờ thấy một cú 429 nào. Đã mắc đúng
            # lỗi này một lần; bài kiểm đầu-cuối trong
            # `test_chay_ca_me_tu_do_nhip.py` là thứ bắt được.
            getattr(error, "status", 0) or 0,
            getattr(error, "code", None),
            getattr(error, "retry_after", None),
        )


class ShopAPI(BaseClient):
    """Client đồng bộ.

    ```python
    import os
    from shopapi import ShopAPI

    client = ShopAPI(api_key=os.environ["SHOPAPI_KEY"])
    audio = client.tts.create_and_wait(text="Xin chào")
    print(audio.output.url)
    ```
    """

    def __init__(
        self,
        api_key: Optional[str] = None,
        *,
        base_url: Optional[str] = None,
        timeout: float = DEFAULT_TIMEOUT,
        max_retries: int = DEFAULT_MAX_RETRIES,
        webhook_secret: Optional[str] = None,
        default_headers: Optional[Mapping[str, str]] = None,
        http_client: Optional[httpx.Client] = None,
        _retry_base_delay: float = RETRY_BASE_DELAY,
        _retry_max_delay: float = RETRY_MAX_DELAY,
        _retry_jitter: bool = True,
    ) -> None:
        super().__init__(
            api_key,
            base_url=base_url,
            timeout=timeout,
            max_retries=max_retries,
            webhook_secret=webhook_secret,
            default_headers=default_headers,
            _retry_base_delay=_retry_base_delay,
            _retry_max_delay=_retry_max_delay,
            _retry_jitter=_retry_jitter,
        )
        self._owns_http = http_client is None
        self._http: httpx.Client = http_client or httpx.Client(
            timeout=httpx.Timeout(self.timeout), follow_redirects=True
        )

        self.tts = Tts(self)
        self.images = Images(self)
        self.videos = Videos(self)
        self.jobs = Jobs(self)
        self.uploads = Uploads(self)
        self.balance = Balance(self)
        self.usage = Usage(self)
        self.ledger = Ledger(self)
        self.topup = Topup(self)
        self.pricing = Pricing(self)
        self.stats = Stats(self)
        self.webhooks = Webhooks(self)

    # ── Vòng đời ─────────────────────────────────────────────────────────────

    def close(self) -> None:
        """Đóng kết nối HTTP. Gọi khi bạn không dùng `with`."""
        if self._owns_http:
            self._http.close()

    def __enter__(self) -> "ShopAPI":
        return self

    def __exit__(
        self,
        exc_type: Optional[Type[BaseException]],
        exc: Optional[BaseException],
        tb: Optional[TracebackType],
    ) -> None:
        self.close()

    # ── Gửi request ──────────────────────────────────────────────────────────

    def request(
        self,
        method: str,
        path: str,
        *,
        params: Optional[Mapping[str, Any]] = None,
        json: Optional[Mapping[str, Any]] = None,
        headers: Optional[Mapping[str, str]] = None,
        auth: bool = True,
        idempotent: bool = False,
        idempotency_key: Optional[str] = None,
    ) -> Model:
        """Gửi một request có tự thử lại. Trả về `Model` bọc JSON phản hồi."""
        # Sinh Idempotency-Key MỘT LẦN rồi giữ nguyên qua mọi lần thử lại —
        # nhờ vậy thử lại không bao giờ tạo job trùng (SDK_SPEC §5).
        if idempotent and not idempotency_key:
            idempotency_key = str(uuid.uuid4())

        url = self._url(path)
        request_headers = self._build_headers(
            auth=auth, idempotency_key=idempotency_key, extra=headers
        )
        query = self._clean_params(params)

        attempt = 0
        while True:
            try:
                response = self._http.request(
                    method, url, params=query, json=json, headers=request_headers
                )
            except httpx.TimeoutException as exc:
                if attempt < self.max_retries:
                    time.sleep(self._retry_delay(attempt))
                    attempt += 1
                    continue
                raise APITimeoutError(cause=exc) from exc
            except httpx.HTTPError as exc:
                if attempt < self.max_retries:
                    time.sleep(self._retry_delay(attempt))
                    attempt += 1
                    continue
                raise APIConnectionError(cause=exc) from exc

            if response.status_code < 400:
                return self._to_model(response)

            error = self._build_error(response)
            self._bao_nhip_do(error)
            if self._should_retry(error, attempt):
                retry_after = getattr(error, "retry_after", None)
                time.sleep(self._retry_delay(attempt, retry_after))
                attempt += 1
                continue
            raise error

    # ── Chạy nhiều việc một lúc ──────────────────────────────────────────────

    def tran_song_song(self, loai: str) -> int:
        """Trần chạy song song HIỆN TẠI cho một loại job (``tts``/``image``/``video``).

        Con số này **không cố định**: máy chủ tính lại liên tục từ sức chứa còn
        trống của nhà máy chia cho số khách đang chờ. Vì vậy đừng gọi một lần rồi
        nhớ mãi — gọi lại sau mỗi lô.

        ``0`` nghĩa là nhà máy loại đó đang dừng; job gửi lúc này bị từ chối ngay
        ở cửa bằng ``503 engine_unavailable`` và **không trừ tiền**.
        """
        me = self.request("GET", "/v1/me")
        return int(me["limits"]["concurrent_jobs"][loai])

    def chay_ca_me(
        self,
        loai: str,
        cong_viec: Sequence[Mapping[str, Any]],
        *,
        cho_khi_dung: float = 30.0,
        nhip: Optional[NhipDo] = None,
    ) -> List[Model]:
        """Chạy cả mẻ job, **tự dò nhịp**. Trả kết quả ĐÚNG THỨ TỰ đưa vào.

        ═══ VÌ SAO KHÔNG PHẢI "ĐỌC TRẦN RỒI BẮN ĐÚNG BẤY NHIÊU" ═══

        Bản trước của hàm này làm đúng thế, và nó vẫn sai — vì trần máy chủ là
        mức **không được vượt**, không phải mức **phải chạy**. Máy chủ biết nhà
        máy còn mấy chỗ; nó không biết đường mạng của bạn, không biết engine phía
        sau đang bị bóp, không biết máy bạn mở nổi mấy luồng. Bắn đúng bằng trần
        là tin rằng máy chủ biết mọi thứ, và không có đường lùi khi đoán sai.

        Hàm này thay bằng vòng **tự dò nhịp** (`NhipDo`, xem `_nhip_do.py`):

          • chạy trơn        → **+1 mỗi lô** (tăng cộng, chậm và an toàn)
          • ``429``          → **chia đôi** (giảm nhân, lùi cho kịp)
          • độ trễ vọt lên   → chia đôi, không cần chờ tới lúc ăn ``429``
          • ``503``          → **về 0**, chờ, rồi thăm dò lại bằng đúng MỘT job
          • luôn ``min`` với trần máy chủ, và không bao giờ tụt dưới sàn 1

        Trần máy chủ được đọc lại **trước mỗi lô** (không phải mỗi request —
        nhóm đọc trạng thái có hạn mức riêng), và chỉ dùng làm mức chặn trên.

        ```python
        ket_qua = client.chay_ca_me("tts", [
            {"text": "Câu một"},
            {"text": "Câu hai"},
        ])
        for job in ket_qua:
            print(job.output.url)
        ```

        Truyền `nhip=NhipDo(...)` khi bạn muốn tự đặt sàn/trần khởi đầu, hoặc để
        dùng chung một vòng dò giữa nhiều mẻ chạy nối tiếp nhau — vòng dò càng
        sống lâu thì càng bám sát nhà máy.
        """
        tao = {"tts": self.tts, "image": self.images, "video": self.videos}[loai]

        # Vòng dò phải là vòng dò mà TẦNG HTTP đang báo cáo về (`_bao_nhip_do`),
        # nếu không thì mọi cú 429/503 sẽ rơi vào một cái khác và vòng đang điều
        # khiển mẻ này không bao giờ biết là đã có nghẽn — nó sẽ tăng nhịp đều
        # đặn giữa lúc nhà máy đang ngộp. Nên khi khách tiêm limiter riêng, ta
        # **đổi luôn limiter của client** trong suốt mẻ rồi trả lại như cũ.
        vong_cu = self.nhip_do
        # Khách tiêm limiter riêng thì tôn trọng tuyệt đối; không thì dùng vòng
        # dò CỦA LOẠI JOB NÀY — nó nhớ nhịp qua các lần chạy, thứ mà một tiến
        # trình sống vài phút không tự học nổi.
        self.nhip_do = nhip if nhip is not None else self._nhip_theo_loai(loai)
        vong = self.nhip_do
        try:
            return self._chay_ca_me(loai, cong_viec, tao, vong, cho_khi_dung)
        finally:
            self.nhip_do = vong_cu

    def _chay_ca_me(
        self,
        loai: str,
        cong_viec: Sequence[Mapping[str, Any]],
        tao: Any,
        vong: NhipDo,
        cho_khi_dung: float,
    ) -> List[Model]:
        con_lai = list(enumerate(cong_viec))
        ket_qua: Dict[int, Model] = {}

        while con_lai:
            # ⚠ NGỦ TRƯỚC, HỎI SAU. Đang trong quãng dừng thì `GET /v1/me` không
            # đổi được gì cả — trần chỉ mở lại khi có máy xử lý báo danh, chứ
            # không phải vì ta hỏi nhiều hơn. Hỏi trong lúc chờ là đốt hạn mức
            # đọc trạng thái của chính khách (đo được: một quãng chờ 30 giây
            # thành 579 lời gọi thừa).
            cho = vong.cho_bao_lau()
            if cho > 0:
                time.sleep(min(cho, cho_khi_dung))
                continue

            # Trần máy chủ: mức CHẶN TRÊN, đọc lại mỗi lô. `dat_tran(0)` tự
            # chuyển vòng dò sang trạng thái "nhà máy đang dừng" — vòng sau sẽ
            # rơi vào nhánh ngủ ở trên.
            try:
                vong.dat_tran(self.tran_song_song(loai))
            except EngineUnavailableError as exc:
                vong.nha_may_dung(getattr(exc, "retry_after", None))
                continue

            n = vong.cho_phep()
            if n <= 0:
                time.sleep(cho_khi_dung)
                continue

            lo, con_lai = con_lai[:n], con_lai[n:]
            with ThreadPoolExecutor(max_workers=n) as pool:
                tuong_lai = {
                    pool.submit(self._chay_mot_viec, tao, dict(tham_so)): i for i, tham_so in lo
                }
                tra_lai: List[tuple] = []
                for tl in as_completed(tuong_lai):
                    i = tuong_lai[tl]
                    job, cho_hang_doi = tl.result()
                    if job is None:
                        # Bị từ chối ở cửa (429/503 đã cạn lượt thử lại). Vòng dò
                        # đã ghi nhận qua `_bao_nhip_do`; việc của khách KHÔNG bị
                        # bỏ — nó quay lại hàng chờ và chạy ở lô sau, chậm hơn.
                        tra_lai.append((i, cong_viec[i]))
                        continue
                    ket_qua[i] = job
                    vong.xong(cho_hang_doi)

            # Đưa về ĐẦU hàng chờ để việc bị hoãn không tụt xuống cuối mẻ.
            con_lai = tra_lai + con_lai

        # Trả về đúng thứ tự khách đưa vào, không phải thứ tự job chạy xong.
        return [ket_qua[i] for i in range(len(cong_viec))]

    def _chay_mot_viec(self, tao: Any, tham_so: Mapping[str, Any]):
        """Chạy một job tới khi xong, kèm số giây nó đã nằm hàng chờ.

        Trả `(job, số_giây_nằm_hàng_chờ)`, hoặc `(None, None)` khi bị từ chối vì
        nghẽn — nơi gọi sẽ trả việc về hàng chờ chứ không đánh mất nó.
        """
        try:
            job = tao.create_and_wait(**dict(tham_so))
        except (RateLimitError, EngineUnavailableError):
            return None, None
        return job, cho_hang_doi_cua(job)

    def stream_request(
        self,
        method: str,
        path: str,
        *,
        params: Optional[Mapping[str, Any]] = None,
        headers: Optional[Mapping[str, str]] = None,
        auth: bool = True,
        timeout: Optional[float] = None,
    ) -> Iterator[SSEEvent]:
        """Mở một luồng SSE và sinh từng `SSEEvent`.

        Luồng SSE KHÔNG tự thử lại: đã nhận được một phần dữ liệu thì thử lại
        sẽ làm khách nhận trùng sự kiện.
        """
        url = self._url(path)
        request_headers = self._build_headers(
            auth=auth, extra=headers, accept="text/event-stream"
        )
        request_headers.setdefault("Cache-Control", "no-store")
        # Luồng SSE có thể im lặng lâu giữa hai sự kiện nên bỏ hạn đọc.
        stream_timeout = (
            httpx.Timeout(timeout) if timeout is not None else httpx.Timeout(self.timeout, read=None)
        )
        decoder = SSEDecoder()
        try:
            with self._http.stream(
                method,
                url,
                params=self._clean_params(params),
                headers=request_headers,
                timeout=stream_timeout,
            ) as response:
                if response.status_code >= 400:
                    response.read()
                    raise self._build_error(response)
                self._remember_rate_limit(response)
                for line in response.iter_lines():
                    event = decoder.feed_line(line)
                    if event is not None:
                        yield event
        except httpx.TimeoutException as exc:
            raise APITimeoutError(cause=exc) from exc
        except httpx.HTTPError as exc:
            raise APIConnectionError(cause=exc) from exc
        trailing = decoder.flush()
        if trailing is not None:
            yield trailing


class AsyncShopAPI(BaseClient):
    """Client bất đồng bộ — cùng bề mặt với `ShopAPI`, chỉ thêm `await`.

    ```python
    import asyncio, os
    from shopapi import AsyncShopAPI

    async def main():
        async with AsyncShopAPI(api_key=os.environ["SHOPAPI_KEY"]) as client:
            audio = await client.tts.create_and_wait(text="Xin chào")
            print(audio.output.url)

    asyncio.run(main())
    ```
    """

    def __init__(
        self,
        api_key: Optional[str] = None,
        *,
        base_url: Optional[str] = None,
        timeout: float = DEFAULT_TIMEOUT,
        max_retries: int = DEFAULT_MAX_RETRIES,
        webhook_secret: Optional[str] = None,
        default_headers: Optional[Mapping[str, str]] = None,
        http_client: Optional[httpx.AsyncClient] = None,
        _retry_base_delay: float = RETRY_BASE_DELAY,
        _retry_max_delay: float = RETRY_MAX_DELAY,
        _retry_jitter: bool = True,
    ) -> None:
        super().__init__(
            api_key,
            base_url=base_url,
            timeout=timeout,
            max_retries=max_retries,
            webhook_secret=webhook_secret,
            default_headers=default_headers,
            _retry_base_delay=_retry_base_delay,
            _retry_max_delay=_retry_max_delay,
            _retry_jitter=_retry_jitter,
        )
        self._owns_http = http_client is None
        self._http: httpx.AsyncClient = http_client or httpx.AsyncClient(
            timeout=httpx.Timeout(self.timeout), follow_redirects=True
        )

        self.tts = AsyncTts(self)
        self.images = AsyncImages(self)
        self.videos = AsyncVideos(self)
        self.jobs = AsyncJobs(self)
        self.uploads = AsyncUploads(self)
        self.balance = AsyncBalance(self)
        self.usage = AsyncUsage(self)
        self.ledger = AsyncLedger(self)
        self.topup = AsyncTopup(self)
        self.pricing = AsyncPricing(self)
        self.stats = AsyncStats(self)
        self.webhooks = AsyncWebhooks(self)

    # ── Vòng đời ─────────────────────────────────────────────────────────────

    async def close(self) -> None:
        """Đóng kết nối HTTP. Gọi khi bạn không dùng `async with`."""
        if self._owns_http:
            await self._http.aclose()

    #: Bí danh quen thuộc với người dùng httpx.
    aclose = close

    async def __aenter__(self) -> "AsyncShopAPI":
        return self

    async def __aexit__(
        self,
        exc_type: Optional[Type[BaseException]],
        exc: Optional[BaseException],
        tb: Optional[TracebackType],
    ) -> None:
        await self.close()

    # ── Gửi request ──────────────────────────────────────────────────────────

    async def request(
        self,
        method: str,
        path: str,
        *,
        params: Optional[Mapping[str, Any]] = None,
        json: Optional[Mapping[str, Any]] = None,
        headers: Optional[Mapping[str, str]] = None,
        auth: bool = True,
        idempotent: bool = False,
        idempotency_key: Optional[str] = None,
    ) -> Model:
        """Gửi một request có tự thử lại. Trả về `Model` bọc JSON phản hồi."""
        if idempotent and not idempotency_key:
            idempotency_key = str(uuid.uuid4())

        url = self._url(path)
        request_headers = self._build_headers(
            auth=auth, idempotency_key=idempotency_key, extra=headers
        )
        query = self._clean_params(params)

        attempt = 0
        while True:
            try:
                response = await self._http.request(
                    method, url, params=query, json=json, headers=request_headers
                )
            except httpx.TimeoutException as exc:
                if attempt < self.max_retries:
                    await asyncio.sleep(self._retry_delay(attempt))
                    attempt += 1
                    continue
                raise APITimeoutError(cause=exc) from exc
            except httpx.HTTPError as exc:
                if attempt < self.max_retries:
                    await asyncio.sleep(self._retry_delay(attempt))
                    attempt += 1
                    continue
                raise APIConnectionError(cause=exc) from exc

            if response.status_code < 400:
                return self._to_model(response)

            error = self._build_error(response)
            self._bao_nhip_do(error)
            if self._should_retry(error, attempt):
                retry_after = getattr(error, "retry_after", None)
                await asyncio.sleep(self._retry_delay(attempt, retry_after))
                attempt += 1
                continue
            raise error

    async def stream_request(
        self,
        method: str,
        path: str,
        *,
        params: Optional[Mapping[str, Any]] = None,
        headers: Optional[Mapping[str, str]] = None,
        auth: bool = True,
        timeout: Optional[float] = None,
    ) -> AsyncIterator[SSEEvent]:
        """Mở một luồng SSE bất đồng bộ và sinh từng `SSEEvent`."""
        url = self._url(path)
        request_headers = self._build_headers(
            auth=auth, extra=headers, accept="text/event-stream"
        )
        request_headers.setdefault("Cache-Control", "no-store")
        stream_timeout = (
            httpx.Timeout(timeout) if timeout is not None else httpx.Timeout(self.timeout, read=None)
        )
        decoder = SSEDecoder()
        try:
            async with self._http.stream(
                method,
                url,
                params=self._clean_params(params),
                headers=request_headers,
                timeout=stream_timeout,
            ) as response:
                if response.status_code >= 400:
                    await response.aread()
                    raise self._build_error(response)
                self._remember_rate_limit(response)
                async for line in response.aiter_lines():
                    event = decoder.feed_line(line)
                    if event is not None:
                        yield event
        except httpx.TimeoutException as exc:
            raise APITimeoutError(cause=exc) from exc
        except httpx.HTTPError as exc:
            raise APIConnectionError(cause=exc) from exc
        trailing = decoder.flush()
        if trailing is not None:
            yield trailing


#: Kiểu chung cho tài nguyên: nhận một trong hai client.
AnyClient = Union[ShopAPI, AsyncShopAPI]
