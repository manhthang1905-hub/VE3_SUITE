"""Phần dùng chung cho hai nhánh "gọi API shopapi" (ảnh + video).

VÌ SAO CÓ FILE NÀY
------------------
`shopapi_image_client.py` và `shopapi_video_client.py` cùng cần đúng bốn thứ:
tìm SDK, tìm khoá API, đổi tên tỉ lệ khung, và tải file kết quả về đĩa. Viết hai
lần là hai lần sai khác nhau, nên gom vào đây.

File này **không** import `shopapi` ở mức module: máy chưa cài SDK vẫn phải
`import` được để tool chạy đường cũ bình thường (nhánh mới sẽ tự lùi).

BỐN CÁI BẪY ĐÃ GHI RÕ TRONG MÃ (đọc trước khi sửa)
--------------------------------------------------
1. **Link kết quả chỉ sống 7 ngày** (CONTRACT §2.2) → phải tải ngay về ổ cứng,
   tuyệt đối không lưu URL vào Excel rồi dùng lại tuần sau.
2. **`n>1` thì ảnh nằm ở `outputs`, KHÔNG phải `output`** — `output` chỉ là file
   đầu tiên (`job.mapper.ts`). Đọc nhầm là mất ảnh mà không báo lỗi.
3. **Veo3 chỉ nhận `duration: 8`, Seedance chỉ nhận `10`** → xem
   `shopapi_video_client.py`.
4. **`create_and_wait` GIẤU MẤT `queue_position` / `estimated_seconds`.** Phản
   hồi `202` của máy chủ có sẵn hai trường đó, nhưng `create_and_wait` nuốt
   luôn job trung gian và chỉ trả về job đã xong. Đó chính là lý do ngày
   07/08/2026 tool `dola-seedance-api` bắn 66 job vào một nhà máy tiêu hoá ~16
   và mất 27 job vì hết hạn NGAY TRONG HÀNG CHỜ (kho tài khoản còn nguyên).
   :func:`tao_va_cho` tách hai bước ra để đọc được hai con số ấy.
"""

from __future__ import annotations

import os
import re
import sys

__all__ = [
    "bootstrap_sdk",
    "sdk_search_paths",
    "duong_dan_kho_khoa",
    "doc_khoa",
    "luu_khoa",
    "quen_khoa",
    "che_khoa",
    "redact",
    "tao_client",
    "ty_le_api",
    "lay_outputs",
    "url_cua_output",
    "doc_hang_cho",
    "tao_va_cho",
    "tai_ve",
    "mo_ta_loi",
    "kiem_khoa",
    "tran_song_song",
    "tran_cung",
    "phan_loai_nghen",
    "BiNghen",
    "MA_NGHEN_CAP_JOB",
    "DEFAULT_BASE_URL",
    "KEY_ENV_NAMES",
    "KEY_FILE_ENV_NAME",
    "MAX_REFERENCE_IMAGES",
    "MAX_ANH_MOT_JOB",
    "TRAN_CUNG_MAC_DINH",
    "ShopAPIKhongCoSDK",
]

#: Base URL công khai — CONTRACT.md §0.
DEFAULT_BASE_URL = "https://api.shopapi.vn"

#: Biến môi trường chứa thẳng khoá. `SHOPAPI_KEY` đứng trước cho khớp SDK.
KEY_ENV_NAMES = ("SHOPAPI_KEY", "SHOPAPI_API_KEY")

#: Biến môi trường chứa ĐƯỜNG DẪN tới file khoá (khi khoá do hệ thống khác giữ).
KEY_FILE_ENV_NAME = "SHOPAPI_KEY_FILE"

#: Tên file khoá trong kho khoá riêng của máy.
KEY_FILENAME = "khoa.txt"

#: Thư mục con trong kho khoá — tách khỏi các tool shopapi khác trên cùng máy.
KEY_APP_DIRNAME = "ve3-suite"

#: Máy chủ chỉ nhận TỐI ĐA 10 ảnh tham chiếu mỗi job (hợp đồng API mục ảnh).
MAX_REFERENCE_IMAGES = 10

#: `POST /v1/images/generations` nhận `n` tối đa **8 ảnh MỘT job**.
#:
#: VÌ SAO PHẢI NHỚ CON SỐ NÀY: k ảnh cùng một prompt gộp thành 1 job `n=k` đi
#: nhanh hơn hẳn k job riêng — chỉ tốn MỘT chỗ trong trần song song thay vì k
#: chỗ, và chỉ MỘT lần xếp hàng thay vì k lần. Gửi `n>8` là 400 cho cả job.
MAX_ANH_MOT_JOB = 8

#: Trần CỨNG tuyệt đối của số job chạy song song theo từng loại — mức mà trần
#: động (`GET /v1/me`) không bao giờ vượt qua.
#:
#: Bản gốc ở `packages/queue.constants.ts` (`CONCURRENCY_HARD_CAP`), chép sang
#: Python ở `packages/sdk-python/src/shopapi/_constants.py`. Ở đây chỉ là bản
#: DỰ PHÒNG cho máy chưa cài SDK — :func:`tran_cung` luôn ưu tiên đọc từ SDK để
#: máy chủ nâng trần là tool ăn theo ngay, khỏi phải sửa hai chỗ.
#:
#: ⚠ BẢN CHÉP NÀY SẼ CŨ ĐI. Máy chủ nâng trần là con số ở đây lệch ngay (đã lệch
#: một lần: ảnh 128 trong khi SDK đã lên 384). Không sao — :func:`tran_cung` LUÔN
#: đọc SDK trước, nên bản chép chỉ dùng cho máy chưa cài SDK. Đừng viết bài kiểm
#: gõ cứng con số ở đây; hãy đối chiếu với SDK.
TRAN_CUNG_MAC_DINH = {"tts": 16, "image": 384, "video": 64}

#: Mã lỗi CẤP JOB nghĩa là **nhà máy hết chỗ**, không phải "việc này hỏng".
#:
#: Xem chú thích dài ở :func:`phan_loai_nghen` để biết vì sao nó phải được xếp
#: vào nhóm nghẽn thay vì nhóm hỏng.
MA_NGHEN_CAP_JOB = frozenset({"resource_exhausted"})

#: Che mọi chuỗi trông giống khoá khi ghi log. Thà che nhầm còn hơn để lộ.
_KEY_PATTERN = re.compile(r"\b((?:sk|wk)_[A-Za-z0-9]*_?[A-Za-z0-9\-]{6,})")

#: Đọc từng khối 256KB khi tải: đủ lớn để nhanh, đủ nhỏ để không ngốn RAM.
_CHUNK = 256 * 1024


class ShopAPIKhongCoSDK(ImportError):
    """Không tìm thấy gói `shopapi` trên máy này.

    Nhánh gọi API bắt lỗi này để **lùi về đường cũ** thay vì làm chết cả lượt chạy.
    """


class BiNghen(Exception):
    """Job bị máy chủ từ chối **ngay ở cửa** vì nghẽn: `429` hoặc `503`.

    VÌ SAO PHẢI TÁCH RA MỘT LOẠI LỖI RIÊNG
    --------------------------------------
    Mọi lỗi khác (prompt vi phạm, hết tiền, ảnh tham chiếu hỏng) đều có nghĩa là
    "việc này hỏng, ghi lỗi rồi đi tiếp". Riêng hai mã này nghĩa hoàn toàn khác:

    * **việc CHƯA mất** — job không hề được tạo, nên chỉ cần gửi lại là xong;
    * **CHƯA tốn một đồng nào** — không có gì để hoàn, cũng không có gì để lo;
    * **lỗi không nằm ở việc, mà ở NHỊP** — gửi lại y hệt lúc thưa khách là chạy.

    Trộn nó vào lỗi thường thì cả mẻ sẽ "hỏng" đúng vào lúc đông khách nhất, và
    người dùng phải chạy lại tay. Vì vậy `shopapi_batch.chay_ca_me` bắt riêng
    loại này để **trả việc về đầu hàng chờ** và hạ nhịp, thay vì đếm là thất bại.

    `ma` là `429` (gửi quá nhanh) hoặc `503` (nhà máy đang dừng) — hai cái xử lý
    khác nhau: 429 chia đôi nhịp, 503 dừng hẳn rồi thăm dò lại bằng đúng 1 job.
    """

    def __init__(self, ma, cho=None, goc=None):
        self.ma = int(ma)
        #: `Retry-After` máy chủ gửi kèm (giây), hoặc `None` khi không có.
        self.cho = cho
        #: Ngoại lệ gốc của SDK — giữ lại để ghi log cho đúng nguyên văn.
        self.goc = goc
        super().__init__(mo_ta_loi(goc) if goc is not None else "nghen {0}".format(ma))


# ── Tìm SDK ───────────────────────────────────────────────────────────────────


def sdk_search_paths(engine_dir=None):
    """Các thư mục có thể chứa gói `shopapi`, xếp theo thứ tự ưu tiên.

    Tách riêng khỏi :func:`bootstrap_sdk` để kiểm thử được mà không phải động vào
    `sys.path` thật của tiến trình đang chạy — y hệt cách
    `tools/dola-seedance-api/core/__init__.py` đang làm và đã chạy thật.
    """
    here = engine_dir or os.path.dirname(os.path.abspath(__file__))
    suite_root = os.path.dirname(here)
    paths = []
    # 1. Người dùng chỉ đích danh — luôn thắng.
    pointed = (os.environ.get("SHOPAPI_SDK_PATH") or "").strip()
    if pointed:
        paths.append(pointed)
    # 2. SDK đi kèm khi đóng gói gửi cho người dùng cuối. SDK CHƯA lên PyPI nên
    #    `pip install shopapi` KHÔNG có tác dụng — phải kèm theo hoặc trỏ tay.
    paths.append(os.path.join(here, "_sdk"))
    paths.append(os.path.join(suite_root, "_sdk"))
    # 3. Máy của chủ dự án: kho mã nguồn shopapi nằm cạnh (hoặc khác ổ) VE3_SUITE.
    paths.append(os.path.join(suite_root, "..", "shopapi", "packages", "sdk-python", "src"))
    paths.append(os.path.join("D:" + os.sep, "New folder", "shopapi",
                              "packages", "sdk-python", "src"))
    return [os.path.abspath(p) for p in paths]


def bootstrap_sdk(engine_dir=None):
    """Bảo đảm `import shopapi` chạy được, dù tool nằm ở đâu.

    Không tìm thấy thì KHÔNG ném lỗi ở đây — :func:`tao_client` mới là chỗ báo,
    để việc chỉ `import` module này không bao giờ làm sập tool.
    """
    try:
        import shopapi  # noqa: F401
        return True
    except ImportError:
        pass

    for candidate in sdk_search_paths(engine_dir):
        if os.path.isdir(os.path.join(candidate, "shopapi")):
            if candidate not in sys.path:
                sys.path.insert(0, candidate)
            return True
    return False


# ── Kho khoá (NGOÀI kho mã nguồn) ─────────────────────────────────────────────


def che_khoa(key):
    """`sk_live_abcdef…wxyz` → `sk_live_abcd…wxyz` để hiện lên màn hình.

    Đủ để nhận ra mình đang dùng khoá nào, không đủ để ai nhìn màn hình chép lại.
    """
    text = (key or "").strip()
    if not text:
        return "(chua co khoa)"
    if len(text) <= 12:
        return text[:4] + "…"
    return text[:12] + "…" + text[-4:]


def redact(message):
    """Xoá mọi thứ trông giống khoá khỏi một dòng chữ.

    Gọi ở **mọi** chỗ ghi log: thông điệp lỗi của máy chủ đôi khi nhắc lại tham
    số gửi lên nên không thể tin là nó sạch sẵn.
    """
    return _KEY_PATTERN.sub(lambda m: che_khoa(m.group(1)), str(message))


def duong_dan_kho_khoa(env=None):
    """Đường dẫn file khoá riêng của người dùng máy này.

    * Windows: `%APPDATA%\\ShopAPI\\ve3-suite\\khoa.txt`
    * Nơi khác: `~/.config/shopapi/ve3-suite/khoa.txt`

    Cố ý nằm **ngoài** thư mục kho mã nguồn: không có `git add -A` nào chạm tới.
    """
    source = os.environ if env is None else env
    if sys.platform.startswith("win"):
        root = source.get("APPDATA") or os.path.expanduser("~")
        return os.path.join(root, "ShopAPI", KEY_APP_DIRNAME, KEY_FILENAME)
    root = source.get("XDG_CONFIG_HOME") or os.path.join(os.path.expanduser("~"), ".config")
    return os.path.join(root, "shopapi", KEY_APP_DIRNAME, KEY_FILENAME)


def _doc_file_khoa(path):
    """Đọc khoá từ file chỉ chứa mỗi khoá. Hỏng/không có → chuỗi rỗng."""
    try:
        with open(path, "r", encoding="utf-8-sig") as handle:
            return handle.read().strip()
    except OSError:
        return ""


def doc_khoa(env=None):
    """Tìm khoá API. Trả `(khoá, nguồn)`; không có → `("", "")`.

    Thứ tự: biến môi trường `SHOPAPI_KEY` → `SHOPAPI_API_KEY` → file trỏ bởi
    `SHOPAPI_KEY_FILE` → kho khoá riêng của máy. `nguồn` là câu tiếng Việt hiện
    lên giao diện — **không bao giờ chứa chính khoá đó**.

    Khoá cố ý KHÔNG đọc từ `settings.yaml`: file cấu hình đó nằm trong kho mã và
    còn được chép sang tiến trình worker qua `.ve3_run_config.json` trong thư mục
    project — hai đường rò rỉ mà người dùng không hề biết.
    """
    source = dict(os.environ if env is None else env)

    for name in KEY_ENV_NAMES:
        value = (source.get(name) or "").strip()
        if value:
            return value, "bien moi truong {0}".format(name)

    pointed = (source.get(KEY_FILE_ENV_NAME) or "").strip()
    if pointed:
        value = _doc_file_khoa(pointed)
        if value:
            return value, "file tro boi {0}".format(KEY_FILE_ENV_NAME)

    value = _doc_file_khoa(duong_dan_kho_khoa(source))
    if value:
        return value, "kho khoa rieng cua may nay"

    return "", ""


def luu_khoa(key, env=None):
    """Ghi khoá vào kho khoá của máy, trả lại đường dẫn đã ghi.

    Ghi ra file tạm rồi mới đổi tên: mất điện giữa chừng không làm hỏng khoá cũ.
    """
    path = duong_dan_kho_khoa(env)
    folder = os.path.dirname(path)
    if folder:
        os.makedirs(folder, exist_ok=True)
    temp_path = path + ".tmp"
    with open(temp_path, "w", encoding="utf-8") as handle:
        handle.write((key or "").strip() + "\n")
    try:
        os.chmod(temp_path, 0o600)   # Linux/macOS: chỉ chủ file đọc được
    except OSError:                  # Windows không có khái niệm này
        pass
    os.replace(temp_path, path)
    return path


def quen_khoa(env=None):
    """Xoá khoá khỏi kho khoá — cho nút "Quen khoa" trên giao diện."""
    try:
        os.remove(duong_dan_kho_khoa(env))
    except OSError:
        pass


# ── Client ────────────────────────────────────────────────────────────────────


def tao_client(api_key=None, base_url=None, timeout=180.0, max_retries=3):
    """Dựng `ShopAPI` client. Ném :class:`ShopAPIKhongCoSDK` khi thiếu SDK.

    `timeout` để 180 giây (SDK mặc định 60): job ảnh/video là POST rồi poll, nên
    một lần chờ lâu vẫn là chuyện bình thường trên mạng nhà.

    SDK tự thử lại và tự **giữ nguyên `Idempotency-Key` qua các lần thử**, nên
    mất mạng giữa chừng không đẻ ra job thứ hai (và không trừ tiền hai lần).
    """
    if not bootstrap_sdk():
        raise ShopAPIKhongCoSDK(
            "Khong tim thay goi 'shopapi' (SDK chua len PyPI). Dat thu muc SDK vao "
            "veo3top_engine/_sdk/ hoac tro bien moi truong SHOPAPI_SDK_PATH toi "
            "packages/sdk-python/src."
        )
    from shopapi import ShopAPI  # import muộn: máy không có SDK vẫn chạy đường cũ

    key = (api_key or "").strip()
    if not key:
        key, _ = doc_khoa()
    return ShopAPI(
        api_key=key or None,
        base_url=base_url or None,
        timeout=timeout,
        max_retries=max_retries,
    )


# ── Tỉ lệ khung ───────────────────────────────────────────────────────────────

#: Bộ tỉ lệ máy chủ chấp nhận. Gửi ngoài bộ này là 400 invalid_request.
_TY_LE_HOP_LE = ("16:9", "9:16", "1:1", "4:3", "3:4")


def ty_le_api(aspect, mac_dinh="16:9"):
    """Đổi mọi cách gọi tỉ lệ trong tool sang chuỗi máy chủ hiểu.

    Tool nói bằng ba thứ tiếng khác nhau — `IMAGE_ASPECT_RATIO_PORTRAIT` (Flow
    API ảnh), `VIDEO_ASPECT_RATIO_LANDSCAPE` (Flow API video), và `"portrait"`
    (settings.yaml). Hàm này quy hết về `"9:16"` / `"16:9"` / `"1:1"`.

    Không nhận ra → trả `mac_dinh` chứ không ném lỗi: sai tỉ lệ chỉ xấu khung
    hình, còn ném lỗi là hỏng cả job.
    """
    if aspect is None:
        return mac_dinh
    # Enum của google_flow_api có .value/.name; chuỗi thì dùng thẳng.
    text = getattr(aspect, "value", None) or getattr(aspect, "name", None) or str(aspect)
    text = str(text).strip()
    if text in _TY_LE_HOP_LE:
        return text
    upper = text.upper()
    if "PORTRAIT" in upper:
        return "9:16"
    if "SQUARE" in upper:
        return "1:1"
    if "LANDSCAPE" in upper:
        return "16:9"
    return mac_dinh


# ── Đọc kết quả job ───────────────────────────────────────────────────────────


def _lay_truong(obj, ten):
    """Đọc một trường từ `Model` của SDK **hoặc** `dict` mà không nổ khi thiếu."""
    if obj is None:
        return None
    try:
        return obj[ten]
    except (KeyError, IndexError, TypeError):
        return getattr(obj, ten, None)


def lay_outputs(job):
    """Trả về DANH SÁCH output của job, đúng thứ tự máy chủ trả.

    ⚠ BẪY: với `n>1` máy chủ đặt toàn bộ ảnh ở `outputs`, còn `output` **chỉ là
    file đầu tiên** (`apps/api/src/modules/jobs/job.mapper.ts`). Chỉ đọc `output`
    là lẳng lặng mất `n-1` ảnh ĐÃ TRẢ TIỀN. Với `n==1` máy chủ KHÔNG gửi
    `outputs`, nên phải chấp nhận cả hai khuôn.
    """
    if job is None:
        return []
    many = _lay_truong(job, "outputs")
    if many:
        return list(many)
    one = _lay_truong(job, "output")
    return [one] if one else []


def url_cua_output(output):
    """Lấy `url` từ một phần tử output (là `Model`, `dict`, hoặc chuỗi URL trần)."""
    if output is None:
        return ""
    if isinstance(output, str):
        return output
    return str(_lay_truong(output, "url") or "")


# ── Đọc chỗ đứng trong hàng chờ ───────────────────────────────────────────────


def _so_hoac_none(gia_tri, so_nguyen):
    """Đọc một con số từ phản hồi máy chủ. Thiếu/hỏng kiểu → `None`, không đoán bừa.

    `None` khác hẳn `0`: `None` là "máy chủ không nói", `0` là "hàng rỗng, vào
    ngay". Coi "không biết" thành "rỗng" là cách êm ái nhất để tắt mất cái cổng
    nhịp vừa dựng lên.
    """
    if gia_tri is None:
        return None
    try:
        return int(gia_tri) if so_nguyen else float(gia_tri)
    except (TypeError, ValueError):
        return None


def doc_hang_cho(job):
    """`(queue_position, estimated_seconds)` từ phản hồi TẠO job (CONTRACT §2.1).

    Máy chủ trả sẵn hai trường này trong mọi phản hồi `202`::

        {"id": "job_…", "estimated_seconds": 45, "queue_position": 3}

    `queue_position` = đứng thứ mấy trong hàng; `estimated_seconds` = ước lượng
    bao lâu nữa tới lượt. Hai con số đó nói ra thứ mà trần song song KHÔNG nói:
    **hàng chờ trước mặt đang dài bao nhiêu**. Xem
    :class:`shopapi_batch.CongHangCho`.

    Nhận cả `Model` của SDK lẫn `dict` thuần, và **không bao giờ ném**: một
    trường thiếu không được phép làm hỏng một job đã trả tiền.
    """
    try:
        return (
            _so_hoac_none(_lay_truong(job, "queue_position"), True),
            _so_hoac_none(_lay_truong(job, "estimated_seconds"), False),
        )
    except Exception:                       # noqa: BLE001
        return (None, None)


def tao_va_cho(client, ten_tai_nguyen, timeout, on_progress=None, on_hang_cho=None,
               **tham_so):
    """`create` rồi `jobs.wait` — **tách hai bước để ĐỌC ĐƯỢC hàng chờ**.

    Làm đúng y việc mà `create_and_wait` của SDK làm bên trong (tạo job, rồi
    `client.jobs.wait(job["id"], …, estimated_seconds=job["estimated_seconds"])`),
    chỉ khác một điều: nó **đưa job trung gian ra** qua `on_hang_cho` trước khi
    bắt đầu chờ.

    VÌ SAO PHẢI TÁCH: `create_and_wait` chỉ trả về job ĐÃ XONG, nên
    `queue_position` và `estimated_seconds` — hai trường chỉ có trong phản hồi
    `202` lúc tạo — biến mất không dấu vết. Không đọc được chúng thì tool không
    có cách nào biết hàng chờ đang dài, và nó sẽ nhồi tiếp cho tới lúc job chết
    vì hết hạn trong hàng (sự cố 07/08/2026: 66 job, nhà máy tiêu hoá 16, mất
    27 job, kho tài khoản KHÔNG cạn).

    ⚠ CÓ ĐƯỜNG LÙI: client nào không có đủ `create` + `jobs.wait` (bản SDK cũ,
    hoặc client giả trong bài kiểm) thì quay về `create_and_wait` như trước.
    Mất tín hiệu hàng chờ thì tool chạy đúng như bản cũ — chậm hơn về mặt điều
    nhịp, nhưng **không bao giờ gãy**.
    """
    tai_nguyen = getattr(client, ten_tai_nguyen, None)
    if tai_nguyen is None:
        raise AttributeError("client khong co '{0}'".format(ten_tai_nguyen))

    tao = getattr(tai_nguyen, "create", None)
    cho = getattr(getattr(client, "jobs", None), "wait", None)
    if tao is None or cho is None:
        return tai_nguyen.create_and_wait(
            timeout=timeout, on_progress=on_progress, **tham_so)

    job = tao(**tham_so)
    vi_tri, uoc_giay = doc_hang_cho(job)
    if on_hang_cho is not None:
        try:
            on_hang_cho(vi_tri, uoc_giay)
        except Exception:                   # noqa: BLE001 — báo nhịp hỏng không giết job
            pass
    # `estimated_seconds` cũng là thứ SDK dùng để chọn nhịp hỏi trạng thái đầu
    # tiên (`_polling.poll_delays`) — truyền tiếp cho khớp `create_and_wait`.
    return cho(_lay_truong(job, "id"), timeout=timeout, on_progress=on_progress,
               estimated_seconds=uoc_giay)


# ── Tải file kết quả ──────────────────────────────────────────────────────────


def tai_ve(url, dest_path, timeout=600.0):
    """Tải `url` về `dest_path`, trả lại đường dẫn thật đã ghi.

    ⚠ Link kết quả của ShopAPI **sống 7 ngày** rồi bị xoá. Đó là lý do tool tải
    ngay tại đây thay vì ghi URL vào Excel: mã cũ mở lại sau một tuần sẽ thấy
    toàn 404, mà lúc đó tiền đã tiêu rồi.

    Ghi ra `.part` rồi mới đổi tên → không bao giờ để lại file dở dang trông như
    đã tải xong (caller chỉ kiểm `output_path.exists()`).
    """
    folder = os.path.dirname(os.path.abspath(str(dest_path)))
    if folder:
        os.makedirs(folder, exist_ok=True)

    temp_path = str(dest_path) + ".part"
    try:
        import httpx
    except ImportError:
        httpx = None

    try:
        if httpx is not None:
            with httpx.Client(timeout=timeout, follow_redirects=True) as http:
                with http.stream("GET", url) as response:
                    if response.status_code >= 400:
                        raise IOError(
                            "Kho luu tru tra ma {0} khi tai ket qua. Link ket qua chi "
                            "song 7 ngay.".format(response.status_code)
                        )
                    with open(temp_path, "wb") as handle:
                        for chunk in response.iter_bytes(_CHUNK):
                            handle.write(chunk)
        else:
            import requests  # dự phòng: requests đã có sẵn trong requirements.txt
            response = requests.get(url, stream=True, timeout=timeout)
            if response.status_code >= 400:
                raise IOError(
                    "Kho luu tru tra ma {0} khi tai ket qua.".format(response.status_code)
                )
            with open(temp_path, "wb") as handle:
                for chunk in response.iter_content(_CHUNK):
                    if chunk:
                        handle.write(chunk)
    except Exception:
        try:
            if os.path.exists(temp_path):
                os.remove(temp_path)
        except OSError:
            pass
        raise

    os.replace(temp_path, str(dest_path))
    return str(dest_path)


# ── Lỗi → tiếng Việt ──────────────────────────────────────────────────────────


def mo_ta_loi(exc):
    """Một dòng tiếng Việt cho log, nói rõ CÓ BỊ TRỪ TIỀN KHÔNG.

    Rẽ nhánh theo **tên lớp ngoại lệ**, không theo mã HTTP: một mã HTTP mang
    nhiều `code` khác nhau (403 có ba loại, 503 có hai) nên rẽ theo số là nhầm
    loại lỗi. Dùng tên lớp thay vì `isinstance` để hàm này chạy được cả khi SDK
    chưa nạp được (lúc đó `exc` chỉ là lỗi Python thường).
    """
    ten = type(exc).__name__
    goc = redact(getattr(exc, "message", None) or str(exc))

    if ten == "InsufficientBalanceError":
        return "HET TIEN: {0} (CHUA bi tru dong nao - nap tien roi chay lai)".format(goc)
    if ten == "AuthenticationError":
        return "KHOA API HONG: {0} (tao khoa moi o shopapi.vn/dashboard/api-keys)".format(goc)
    if ten == "RateLimitError":
        cho = getattr(exc, "retry_after", None)
        them = " Nen cho {0:.0f} giay roi thu lai.".format(float(cho)) if cho else ""
        return "GUI QUA NHANH (429): {0}.{1}".format(goc, them)
    if ten in ("EngineUnavailableError", "ServiceUnavailableError"):
        # 503 = job KHÔNG được tạo và KHÔNG trừ tiền — nói rõ để khỏi ai đi soi ví.
        return "MAY CHU QUA TAI (503): {0} (job CHUA duoc tao, KHONG bi tru tien)".format(goc)
    if ten == "UnsupportedParameterError":
        return ("THAM SO SAI (422): {0} (Veo3 CHI nhan video 8 giay, Seedance CHI nhan "
                "10 giay)".format(goc))
    if ten == "ContentRejectedError":
        # `[content_rejected]` là MÃ MÁY ĐỌC, cố ý nhét vào câu chữ — xem chú thích
        # ở nhánh `JobFailedError` bên dưới để biết vì sao không được bỏ đi.
        return ("NOI DUNG BI TU CHOI [content_rejected]: {0} (da hoan tien day du - "
                "sua prompt roi chay lai)".format(goc))
    if ten == "PermissionDeniedError":
        return "KHOA KHONG DU QUYEN ({0}): {1} (vi KHONG bi tru tien)".format(
            getattr(exc, "reason", "?"), goc)
    if ten in ("APITimeoutError", "APIConnectionError"):
        return "KHONG NOI DUOC MAY CHU: {0} (kiem tra mang/tuong lua)".format(goc)
    if ten == "JobFailedError":
        # ⚠ PHẢI kèm `job.error.code` vào câu chữ, đừng rút gọn cho đẹp.
        #
        # Job bị bộ lọc nội dung chặn về đây dưới dạng `JobFailedError` mang
        # `code="content_rejected"`, còn `message` thì là **văn xuôi tiếng Việt của
        # máy chủ** — chữ nghĩa có thể đổi bất cứ lúc nào mà không báo. Bên
        # `ve3_worker._is_policy_violation_error` phải nhận ra đây là lỗi policy để
        # bật đường VIẾT LẠI PROMPT 3 vòng; nó chỉ có mỗi chuỗi này để nhìn.
        #
        # Đã dính một lần: bỏ mã đi thì mọi lần bị chặn đều đếm là "cảnh hỏng", cả
        # bộ máy viết lại nằm im, người dùng mất cảnh mà không hiểu vì sao.
        ma = getattr(exc, "code", None)
        return "JOB HONG [{0}]: {1}".format(ma or "?", goc)
    if ten == "JobTimeoutError":
        return "CHO QUA LAU: {0} (job van dang chay tren server)".format(goc)
    if ten == "ShopAPIKhongCoSDK":
        return "THIEU SDK shopapi: {0}".format(goc)
    return "{0}: {1}".format(ten, goc)


def phan_loai_nghen(exc):
    """`exc` có phải nghẽn (429/503) không? Có → :class:`BiNghen`; không → `None`.

    Rẽ theo **tên lớp ngoại lệ** chứ không theo mã HTTP, cùng lý do như
    :func:`mo_ta_loi`: một mã HTTP mang nhiều `code` khác nhau, và hàm này còn
    phải chạy được cả khi SDK chưa nạp (lúc đó `exc` chỉ là lỗi Python thường).

    ⚠ `BiNghen` đi qua đây thì trả lại chính nó — nếu không, một lần bọc lại sẽ
    làm mất `Retry-After` và mã gốc.
    """
    if isinstance(exc, BiNghen):
        return exc
    ten = type(exc).__name__
    if ten == "RateLimitError":
        return BiNghen(429, getattr(exc, "retry_after", None), exc)
    if ten in ("EngineUnavailableError", "ServiceUnavailableError"):
        # 503: job KHÔNG được tạo, KHÔNG trừ tiền — chờ rồi gửi lại là đủ.
        return BiNghen(503, getattr(exc, "retry_after", None), exc)
    if ten == "JobFailedError" and str(getattr(exc, "code", "") or "") in MA_NGHEN_CAP_JOB:
        # ⚠ `resource_exhausted` TRÔNG NHƯ job hỏng nhưng nghĩa là NHÀ MÁY HẾT CHỖ.
        #
        # Worker phía máy chủ ném nó khi không còn máy/tài khoản rảnh
        # (`workers/shared/shopapi_worker/errors.py`), và nó về tới đây dưới dạng
        # `JobFailedError` — cùng lớp với "prompt bị chặn", "engine crash". Nếu
        # xếp nó vào "việc này hỏng" thì tool ghi lỗi rồi **bắn tiếp cái sau**,
        # tức là đạp ga đúng lúc máy chủ vừa nói hết chỗ.
        #
        # Job kiểu này **đã được hoàn 100% tiền**, nên trả việc về đầu hàng chờ
        # là đúng cả về tiền lẫn về nhịp. Cho mã `429` để vòng dò chia đôi nhịp
        # (không phải `503`: nhà máy vẫn sống, chỉ là đang chật).
        return BiNghen(429, getattr(exc, "retry_after", None), exc)
    return None


def tran_cung(loai):
    """Trần CỨNG tuyệt đối của một loại job — mức trần động không bao giờ vượt.

    Đọc từ SDK trước (`CONCURRENCY_HARD_CAP`) để máy chủ nâng trần là tool ăn
    theo ngay; thiếu SDK mới dùng bản chép :data:`TRAN_CUNG_MAC_DINH`.

    Đây **không** phải con số nên dùng làm số luồng: nó chỉ là chốt chặn để một
    lỗi đọc `/v1/me` (hoặc một người dùng gõ 999 vào ô cấu hình) không biến
    thành 999 luồng đập vào máy chủ.
    """
    try:
        bootstrap_sdk()
        from shopapi._constants import CONCURRENCY_HARD_CAP
        return int(CONCURRENCY_HARD_CAP[loai])
    except Exception:
        return int(TRAN_CUNG_MAC_DINH.get(loai, 1))


def kiem_khoa(api_key=None, base_url=None):
    """Kiểm khoá bằng `GET /v1/balance`. Trả `(ok, câu tiếng Việt)`.

    Dùng cho nút "Kiem khoa" ở trang Cài đặt. Gọi endpoint số dư vì nó vừa xác
    nhận khoá dùng được, vừa trả lời luôn câu hỏi thực sự trong đầu người dùng:
    "còn bao nhiêu tiền".
    """
    try:
        client = tao_client(api_key=api_key, base_url=base_url, timeout=30.0, max_retries=1)
    except Exception as exc:
        return False, mo_ta_loi(exc)

    try:
        balance = client.balance.retrieve()
    except Exception as exc:
        return False, mo_ta_loi(exc)

    try:
        from shopapi import format_vnd
        so_du = format_vnd(_lay_truong(balance, "wallet"))
    except Exception:
        so_du = str(_lay_truong(balance, "wallet"))
    return True, "Khoa dung. So du: {0}".format(so_du)


def tran_song_song(loai, api_key=None, mac_dinh=1, client=None):
    """`GET /v1/me` → số job loại `loai` (`image`/`video`) được chạy song song.

    KHÔNG gõ cứng con số này: máy chủ tính lại liên tục theo sức chứa nhà máy
    chia cho số khách đang chờ. `0` nghĩa là nhà máy loại đó **đang dừng** — gửi
    lúc này bị 503 ngay ở cửa và không trừ tiền. Hàm này trả nguyên `0` đó ra
    ngoài; quyết định "chờ rồi hỏi lại" là việc của
    :func:`shopapi_batch.so_luong_song_song`, không phải của hàm đọc số.

    Hỏi không được (mạng hỏng, thiếu SDK) thì trả `mac_dinh` chứ không làm chết
    lượt chạy: đoán thấp còn hơn đứng im.

    `client` để **dùng lại một client duy nhất** cho cả lượt chạy. Quan trọng
    hơn vẻ ngoài: mỗi `ShopAPI` mang MỘT vòng tự dò nhịp riêng, nên dựng client
    mới cho từng lời gọi là mỗi job dò lại từ đầu và không ai biết vừa có 429.
    """
    try:
        if client is None:
            client = tao_client(api_key=api_key, timeout=30.0, max_retries=1)
        return int(client.tran_song_song(loai))
    except Exception:
        return mac_dinh
