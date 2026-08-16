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
1. **Link kết quả HẾT HẠN NHANH** → phải tải ngay về ổ cứng, tuyệt đối không
   lưu URL rồi dùng lại. Từ 14/08/2026 ảnh/video do Google giữ
   (`flow-content.google`) và link chỉ sống ~6 giờ thay vì nhiều ngày. ĐỪNG gõ
   cứng con số nào — dùng :func:`tai_ket_qua`, nó đi qua đường
   `GET /v1/jobs/{id}/download` vốn KHÔNG hết hạn.
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
import threading
import time

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
    "tai_ket_qua",
    "duoi_cua_output",
    "mo_ta_loi",
    "kiem_khoa",
    "tran_song_song",
    "tran_cung",
    "doc_v1_me_chung",
    "ThuHoachChung",
    "thu_hoach_cua",
    "dung_thu_hoach_chung",
    "tran_cung_may_chu",
    "dung_sse",
    "TRAN_KET_NOI",
    "NGAN_SACH_LUONG_MAC_DINH",
    "don_nhip_song_cu",
    "phan_luong_cua_toi",
    "ha_ngan_sach_luong",
    "ngan_sach_luong",
    "thu_muc_nhip_song",
    "dem_ban_dang_chay",
    "NhipSong",
    "doc_tran_chi_tiet",
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

    def __init__(self, ma, cho=None, goc=None, ly_do=""):
        self.ma = int(ma)
        #: `code` máy chủ gửi kèm. Ba cửa khác nhau cùng cho ra `429`, và cách
        #: chữa của chúng ngược nhau — xem :func:`phan_loai_nghen`.
        self.ly_do = str(ly_do or "")
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
        http_client=_http_rong(timeout),
    )


#: Trần kết nối HTTP mở cùng lúc trong MỘT tiến trình.
#:
#: ⚠ `httpx.Client()` mặc định chặn ở `max_connections=100`. Với đường SSE thì
#: mỗi job đang chờ GIỮ một kết nối suốt vòng đời nó, nên con số mặc định đó
#: chính là trần số job chạy song song — 100, bất kể máy chủ đang mời 979. Và
#: nó chặn IM LẶNG: job thứ 101 không lỗi, chỉ nằm đợi trong hàng của httpx.
TRAN_KET_NOI = 1200


def _http_rong(timeout):
    """`httpx.Client` đã nới trần kết nối. `None` nếu thiếu httpx (SDK sẽ tự dựng)."""
    try:
        import httpx
        return httpx.Client(
            timeout=httpx.Timeout(float(timeout)),
            follow_redirects=True,
            limits=httpx.Limits(max_connections=TRAN_KET_NOI,
                                max_keepalive_connections=max(64, TRAN_KET_NOI // 4)),
        )
    except Exception:
        return None


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


#: Ngân sách lời gọi HỎI TRẠNG THÁI, tính bằng request/giây cho TIẾN TRÌNH NÀY.
#:
#: Hạn mức của tài khoản là 1.000 request/phút (~16,6/giây) cho MỌI lời gọi.
#: Hỏi thăm job chỉ được ăn một phần, phần còn lại dành cho `create`, `upload`,
#: tải file. Mà VE3 chạy NHIỀU tiến trình mã song song, mỗi tiến trình một ngân
#: sách riêng — nên GUI chia ngân sách rồi truyền xuống qua biến môi trường.
#:
#: Không đặt biến -> 3 req/giây, đủ an toàn cho tới 5 tiến trình mã.
NGAN_SACH_HOI_MOI_GIAY = float(os.environ.get("SHOPAPI_NGAN_SACH_HOI") or 3.0)

#: Chặn dưới/trên của nhịp hỏi. Dưới 1s là phí request; trên 30s thì job xong
#: rồi mà nửa phút sau mới biết, tự thêm độ trễ vào dây chuyền.
#: Chặn dưới 1s: hỏi dày hơn là phí request mà không biết sớm hơn bao nhiêu.
#: Chặn trên 60s: quá đó thì job xong cả phút rồi mới biết, và độ trễ đó cộng
#: thẳng vào thời gian của cả mẻ.
#:
#: ⚠ Trần 60 chứ không phải 30. Ở 300 job đang bay với ngân sách 1,25 req/giây,
#: nhịp đúng phải là 240 giây — kẹp ở 30 thì tổng lại vọt lên 10 req/giây cho
#: MỘT tiến trình, nhân 8 tiến trình là phá trần rate-limit, tức là dựng lại
#: đúng cái bẫy vừa gỡ. Kẹp rộng hơn để lời hứa "giữ trong ngân sách" là thật.
HOI_TOI_THIEU, HOI_TOI_DA = 1.0, 60.0


class NhipHoiTham:
    """Giãn nhịp hỏi trạng thái theo SỐ JOB ĐANG BAY.

    ═══ ĐÂY LÀ TRẦN THẬT CỦA CẢ DÂY CHUYỀN, KHÔNG PHẢI SỨC CHỨA NHÀ MÁY ═══

    Đo ngày 12/08/2026: đẩy 300 job ảnh chỉ bằng `POST` (không chờ), máy chủ
    dựng **134 job đồng thời** và rút 37 job trong 10 giây — **222 ảnh/phút**.
    Cùng ngày, phép đo qua `create_and_wait` lại kết luận "nhà máy bão hoà ở 40
    chỗ, p50 vọt từ 36 lên 186 giây".

    Cả hai đều đúng, và khác nhau ở đúng một chỗ: `create_and_wait` **hỏi thăm
    từng job một**. 100 job × hỏi mỗi 1–5 giây = vài nghìn request/phút, trong
    khi hạn mức là 1.000. Client tự đâm vào trần rate-limit của chính nó, rồi
    ghi độ trễ ấy vào sổ như thể nhà máy chậm.

    Nói cách khác: thứ chặn thông lượng KHÔNG phải sức chứa nhà máy, mà là NGÂN
    SÁCH LỜI GỌI của chính tool. Và nó là thứ chia được: càng nhiều job bay thì
    mỗi job hỏi thưa ra, tổng số request giữ nguyên.

    Đánh đổi có thật và phải nói rõ: hỏi thưa thì biết job xong muộn hơn, trung
    bình chậm thêm nửa nhịp. Ở 134 job bay, nhịp ~22 giây nên trung bình muộn 11
    giây trên một job 36 giây. Đổi 30% độ trễ MỘT job lấy 3 lần số job chạy được
    cùng lúc — lãi gấp mấy lần.
    """

    def __init__(self, moi_giay=None):
        self._moi_giay = max(0.2, float(
            NGAN_SACH_HOI_MOI_GIAY if moi_giay is None else moi_giay))
        self._bay = 0
        self._khoa = threading.Lock()

    def vao(self):
        with self._khoa:
            self._bay += 1

    def ra(self):
        with self._khoa:
            self._bay = max(0, self._bay - 1)

    @property
    def dang_bay(self):
        with self._khoa:
            return self._bay

    def nhip(self):
        """Mỗi job nên hỏi lại sau bao nhiêu giây, NGAY BÂY GIỜ."""
        with self._khoa:
            n = max(1, self._bay)
        return max(HOI_TOI_THIEU, min(HOI_TOI_DA, n / self._moi_giay))


#: Một bộ điều nhịp cho cả tiến trình — mọi mẻ dùng chung một ngân sách.
NHIP_HOI = NhipHoiTham()


# ── Thu hoạch CHUNG: một lời hỏi cho cả trăm job ─────────────────────────────
#
# ═══ VÌ SAO ĐÂY LÀ TRẦN THẬT, VÀ VÌ SAO NỚI NÓ MỚI LÀ CÁCH KHAI THÁC ═══
#
# Hạn mức của tài khoản là **1.000 request/phút**. Đó là ngân sách cứng, và
# thông lượng tối đa = ngân sách ÷ số lời gọi mỗi ảnh. Nên muốn ra nhiều ảnh
# hơn thì không phải nới trần — mà phải làm mỗi ảnh RẺ ĐI.
#
#   đường hỏi thăm  : 1 POST + N lần GET (N tăng theo độ dài job)  -> ~5,7
#   đường SSE       : 1 POST + 1 SSE + 1 GET kết quả               -> 3,0
#   đường này       : 1 POST + (một lời hỏi CHUNG chia cho cả trăm job) -> ~1,05
#
# Đo 15/08/2026: `GET /v1/jobs?status=succeeded&limit=100` trả về **100 job KÈM
# LUÔN `output`** trong một lời gọi. Nghĩa là không cần hỏi từng job nữa, cũng
# không cần một kết nối SSE cho mỗi job.
#
#   850 req/phút ÷ 3,0  =  283 job/phút  =  2.830 ảnh / 10 phút
#   850 req/phút ÷ 1,05 =  810 job/phút  =  8.100 ảnh / 10 phút
#
# Cùng một hạn mức, gấp gần ba lần hàng ra. Đây là chỗ duy nhất còn nới được mà
# không phải xin máy chủ nâng gì cả.

#: Nhịp hỏi của luồng thu hoạch chung (giây).
#:
#: ═══════════════════════════════════════════════════════════════════════════
#:  3,0 → 15,0 NGÀY 16/08/2026: KHỐI TRÊN KIA ĐANG TỐI ƯU NHẦM ĐƠN VỊ
#: ═══════════════════════════════════════════════════════════════════════════
#:
#: Cả khối chú thích ở trên tính bằng **số lời gọi**, vì tin rằng ngân sách cứng
#: là 1.000 request/phút. Phép tính ấy đúng, và nó đã kéo chi phí từ 5,7 xuống
#: 1,05 lời gọi mỗi ảnh — một cải tiến thật.
#:
#: Nhưng ngân sách request KHÔNG phải nút thắt. Đo trên máy chủ thật hôm nay:
#:
#:     GET /v1/jobs   3.146 lần / 5 phút  =  10 request/giây
#:     tốn 780 giây xử lý trong cửa sổ 300 giây  =  ~2,6 trên 4 lõi CPU
#:
#: Một lời gọi `?limit=100` trả về 100 job **kèm toàn bộ output** — thứ khiến nó
#: rẻ về SỐ LƯỢNG lại đắt về CPU: ~257 ms mỗi lần, so với ~1 ms khi hỏi một job
#: lẻ. Ta đã đổi 5 lời gọi rẻ lấy 1 lời gọi đắt gấp hai trăm lần.
#:
#: ═══ CÁI GIÁ, TRẢ BẰNG TIỀN CỦA CHÍNH KHÁCH ═══
#:
#: Máy chủ cạn CPU (load average 10 trên 4 vCPU) → tiến trình Node không giành
#: được lượt chạy → giao dịch quyết toán hết giờ:
#:
#:     POST /internal/v1/jobs/{id}/complete
#:     471 lỗi 500 / 124 thành công trong 10 phút  =  79% HỎNG
#:
#: Tức vòng thu hoạch này đang phá hỏng khâu **kết sổ tiền** của chính những job
#: nó đang chờ. Tiết kiệm request để rồi mỗi job phải quyết toán ba lần.
#:
#: ═══ VÌ SAO 15 GIÂY VẪN LÀ THỪA NHANH ═══
#:
#: Chủ dự án: *"1 ảnh nhanh nhất cũng 30 giây, video cũng phải 2 phút, nên việc
#: hỏi có thể để lâu hơn"*. Hỏi mỗi 3 giây cho một việc mất 30 giây là hỏi mười
#: lần để nhận một câu trả lời — chín lần trong đó chắc chắn là "chưa xong".
#:
#: 15 giây: chậm nhất cũng chỉ trả kết quả muộn hơn 15 giây so với lúc job thật
#: sự xong, trên một việc vốn mất 30–120 giây. Đổi lại, tải hỏi giảm 5 lần.
NHIP_THU_HOACH = 15.0

#: Về tay không thì giãn dần ra tới ngần này (giây).
#:
#: Vòng thu hoạch vẫn chạy trong lúc mọi job còn đang dựng, và lúc đó mỗi lời
#: hỏi chắc chắn về tay không. Job video mất 2 phút, nên hai phút đầu có tới tám
#: lượt hỏi vô ích ở nhịp 15 giây. Giãn ra khi về tay không, siết lại NGAY khi
#: nhặt được: hàng đang ra đều thì vẫn nhanh, hàng đang dựng thì im.
NHIP_THU_HOACH_TOI_DA = 60.0

#: Mỗi lượt về tay không thì nhân nhịp lên ngần này lần.
NHIP_THU_HOACH_GIAN = 1.5

#: Mỗi lời hỏi lấy tối đa ngần này job. Trần của `GET /v1/jobs`.
LO_THU_HOACH = 100

#: Nhiều nhất bao nhiêu trang mỗi vòng — chặn để một hàng chờ khổng lồ không
#: biến vòng thu hoạch thành cơn bão request đúng thứ nó sinh ra để dập.
TRANG_TOI_DA = 8

_KET_THUC = ("succeeded", "failed", "cancelled", "rejected")


class ThuHoachChung:
    """Một luồng hỏi thăm CHUNG cho mọi job đang bay của một client.

    Thay cho "mỗi job một kết nối SSE + một lời hỏi kết quả". Job đăng ký rồi
    ngồi chờ trên `threading.Event`; luồng nền hỏi `GET /v1/jobs` theo lô và
    đánh thức đúng những job đã xong.

    Chi phí mỗi job tiệm cận **một** lời gọi (chỉ còn `POST` lúc tạo), vì lời
    hỏi thu hoạch được chia cho cả trăm job cùng lúc.
    """

    def __init__(self, client, nhip=None, lo=None):
        self._client = client
        self._nhip = float(NHIP_THU_HOACH if nhip is None else nhip)
        self._lo = int(LO_THU_HOACH if lo is None else lo)
        self._khoa = threading.Lock()
        #: `job_id -> [Event, job đã xong hoặc None]`
        self._cho = {}
        self._luong = None
        #: Số lời gọi thu hoạch đã tốn và số job đã nhặt — để đo chi phí thật.
        self.so_loi_goi = 0
        self.so_nhat_duoc = 0

    # ── Bề mặt cho người gọi ────────────────────────────────────────────────

    def dang_ky(self, job_id):
        ô = [threading.Event(), None]
        with self._khoa:
            self._cho[job_id] = ô
            if self._luong is None or not self._luong.is_alive():
                self._luong = threading.Thread(target=self._vong, daemon=True,
                                               name="shopapi-thu-hoach")
                self._luong.start()
        return ô

    def cho(self, job_id, timeout):
        """Chờ job xong. Trả job dict, hoặc `None` khi hết hạn."""
        ô = self.dang_ky(job_id)
        try:
            if not ô[0].wait(timeout=max(0.0, float(timeout))):
                return None
            return ô[1]
        finally:
            with self._khoa:
                self._cho.pop(job_id, None)

    # ── Luồng nền ───────────────────────────────────────────────────────────

    def _vong(self):
        # ═══ GIÃN NHỊP KHI VỀ TAY KHÔNG — 16/08/2026 ═══
        #
        # Vòng này chạy cả trong lúc mọi job còn đang dựng, và lúc đó mỗi lời hỏi
        # chắc chắn không nhặt được gì. Job video mất 2 phút: ở nhịp cố định đó
        # là tám lượt hỏi vô ích trước lượt đầu tiên có ích.
        #
        # Giãn khi về tay không, siết lại NGAY khi nhặt được — nên hàng đang ra
        # đều thì vẫn chạy ở nhịp nhanh nhất, còn hàng đang dựng thì im. Cùng
        # một luật mà `tu_dieu_luong` bên worker đang dùng, chỉ khác chiều.
        nhip = self._nhip
        while True:
            time.sleep(nhip)
            with self._khoa:
                if not self._cho:
                    return                      # hết việc thì luồng tự nghỉ
                con_cho = set(self._cho)
            truoc = self.so_nhat_duoc
            try:
                self._mot_vong(con_cho)
            except Exception:
                # Một lượt hỏi hỏng KHÔNG được giết luồng: job đang chờ sẽ treo
                # tới hết hạn mà không ai biết vì sao.
                pass
            if self.so_nhat_duoc > truoc:
                nhip = self._nhip               # có hàng -> về nhịp nhanh nhất
            else:
                nhip = min(NHIP_THU_HOACH_TOI_DA, nhip * NHIP_THU_HOACH_GIAN)

    def _mot_vong(self, con_cho):
        cursor = None
        for _ in range(TRANG_TOI_DA):
            if not con_cho:
                return
            r = self._client.jobs.list(limit=self._lo, cursor=cursor)
            self.so_loi_goi += 1
            data = _lay(r, "data") or []
            for j in data:
                ma = _lay(j, "id")
                if ma not in con_cho:
                    continue
                if _lay(j, "status") not in _KET_THUC:
                    con_cho.discard(ma)         # đã thấy, còn chạy -> thôi tìm
                    continue
                job = j if isinstance(j, dict) else (
                    j.to_dict() if hasattr(j, "to_dict") else dict(j._data))
                con_cho.discard(ma)
                self.so_nhat_duoc += 1
                with self._khoa:
                    ô = self._cho.get(ma)
                if ô is not None:
                    ô[1] = job
                    ô[0].set()
            if not _lay(r, "has_more"):
                return
            cursor = _lay(r, "next_cursor")
            if not cursor:
                return


#: Một bộ thu hoạch cho mỗi client — job của cùng một client gộp chung lời hỏi.
_thu_hoach = {}
_thu_hoach_khoa = threading.Lock()


def thu_hoach_cua(client):
    with _thu_hoach_khoa:
        bo = _thu_hoach.get(id(client))
        if bo is None or bo._client is not client:
            bo = ThuHoachChung(client)
            _thu_hoach[id(client)] = bo
        return bo


def dung_thu_hoach_chung():
    """Có gộp lời hỏi không? Tắt bằng `SHOPAPI_THU_HOACH_CHUNG=0`."""
    return (os.environ.get("SHOPAPI_THU_HOACH_CHUNG") or "1").strip()         not in ("0", "false", "False")


def dung_sse():
    """Có đi đường SSE không? Tắt bằng `SHOPAPI_SSE=0`."""
    return (os.environ.get("SHOPAPI_SSE") or "1").strip() not in ("0", "false", "False")


def _cho_bang_sse(client, job_id, timeout, on_progress=None):
    """Chờ job qua `GET /v1/jobs/{id}/events` — MỘT kết nối, KHÔNG hỏi thăm lần nào.

    ═══ VÌ SAO ĐÂY LÀ CHỖ THÁO NÚT THẮT ═══

    Trần thật của dây chuyền không phải sức chứa nhà máy mà là **1.000
    request/phút** của cả tài khoản. Với đường hỏi thăm, một job tốn 1 `POST` +
    N lần `GET` trong suốt vòng đời — N tăng theo thời gian job chạy. Đo
    12/08/2026: 100 job × hỏi mỗi 1–5 giây = vài nghìn request/phút, tool tự đâm
    vào trần của chính nó và "nhà máy bão hoà ở 40 chỗ" — trong khi cùng ngày,
    đẩy thuần `POST` cho ra 134 job đồng thời.

    `NhipHoiTham` chữa bằng cách giãn nhịp hỏi, nhưng đó là chia lại một cái
    bánh quá nhỏ: 600 job đang bay trên ngân sách 10 lời gọi/giây nghĩa là mỗi
    job được hỏi 60 giây một lần, tức là biết tin job xong muộn cả phút.

    SSE bỏ hẳn cái bánh đó. Đo thật 15/08/2026 trên `api.shopapi.vn`: một job
    ảnh trả 11 sự kiện tiến độ trong 30,7 giây qua **một** kết nối, và báo
    `succeeded` ngay giây nó xong. Số request mỗi job trở thành HẰNG SỐ — không
    còn phụ thuộc job chạy bao lâu:

        POST tạo job  +  1 kết nối SSE  +  1 GET lấy kết quả  =  3

    Sự kiện cuối KHÔNG mang `output`/`outputs` (đã đo: chỉ có `status`,
    `progress`, `job_id`, `at`), nên vẫn phải hỏi một lần để lấy đường tải.

    Ném y hợp đồng của `jobs.wait`: `JobFailedError` khi job hỏng,
    `JobTimeoutError` khi quá hạn.
    """
    bootstrap_sdk()
    from shopapi._exceptions import JobFailedError, JobTimeoutError

    t0 = time.monotonic()
    tt_cuoi = None
    for ev in client.jobs.stream(job_id, timeout=timeout):
        tt_cuoi = ev.get("status") or tt_cuoi
        if on_progress is not None:
            try:
                on_progress(ev)
            except Exception:       # noqa: BLE001 — báo tiến độ hỏng không giết job
                pass
    if tt_cuoi is None:
        # Dòng đóng mà chưa nói trạng thái nào -> không kết luận gì, để nơi gọi
        # lùi về đường hỏi thăm. Đoán bừa ở đây là báo hỏng cho job có thể đang chạy.
        raise RuntimeError("dong SSE dong ma chua bao trang thai")
    j = client.jobs.retrieve(job_id)
    job = j if isinstance(j, dict) else j.to_dict()
    tt = job.get("status") or tt_cuoi
    if tt == "succeeded":
        return job
    if tt in ("failed", "cancelled", "rejected"):
        raise JobFailedError(
            "Job {0} ket thuc voi trang thai '{1}'.".format(job_id, tt), job=job)
    raise JobTimeoutError(
        "Dong SSE dong ma job {0} van '{1}'.".format(job_id, tt),
        job=job, job_id=job_id, waited_seconds=time.monotonic() - t0)


def _cho_job_xong(client, job_id, timeout, on_progress=None, uoc_giay=None):
    """Chờ job kết thúc, TRA LẠI NHỊP HỎI MỖI VÒNG.

    Vì sao không dùng `client.jobs.wait`: nó nhận `poll_interval` MỘT LẦN rồi
    chốt cứng cho cả vòng đời job. Job mở lúc dây chuyền còn vắng sẽ giữ nhịp 1
    giây suốt, kể cả khi sau đó có 300 job cùng bay — tức là đúng cơn bão request
    mà `NhipHoiTham` sinh ra để dập.

    Giữ nguyên hợp đồng của `jobs.wait`: trả job đã xong, ném `JobFailedError`
    khi hỏng, `JobTimeoutError` khi quá hạn.
    """
    bootstrap_sdk()
    from shopapi._exceptions import JobFailedError, JobTimeoutError

    # ═══ BA ĐƯỜNG, RẺ TRƯỚC ĐẮT SAU ═══
    #
    # Ngân sách 1.000 request/phút là trần cứng, nên thông lượng = ngân sách ÷
    # số lời gọi mỗi ảnh. Đường nào rẻ hơn thì ra được nhiều hàng hơn với đúng
    # cùng một hạn mức:
    #
    #   thu hoạch chung : ~1,05 lời gọi/ảnh  ->  ~810 job/phút
    #   SSE             :  3,0               ->   283 job/phút
    #   hỏi thăm        :  5,7               ->   149 job/phút
    #
    # Hai đường sau giữ nguyên làm lưới: job đã trả tiền thì tuyệt đối không
    # được bỏ chỉ vì một cách chờ không dựng nổi.
    if dung_thu_hoach_chung() and hasattr(getattr(client, "jobs", None), "list"):
        try:
            job = thu_hoach_cua(client).cho(job_id, timeout)
            if job is not None:
                tt = job.get("status")
                if tt == "succeeded":
                    return job
                raise JobFailedError(
                    "Job {0} ket thuc voi trang thai '{1}'.".format(job_id, tt), job=job)
            # `None` = hết hạn chờ. KHÔNG ném ở đây: có thể luồng thu hoạch vừa
            # chết hoặc job chưa kịp lên danh sách. Để hai đường dưới thử tiếp,
            # chúng còn `timeout` của chính chúng.
        except JobFailedError:
            raise
        except Exception:
            pass

    if dung_sse() and hasattr(getattr(client, "jobs", None), "stream"):
        try:
            return _cho_bang_sse(client, job_id, timeout, on_progress=on_progress)
        except (JobFailedError, JobTimeoutError):
            raise          # kết luận THẬT về job — đừng chờ lại lần nữa
        except Exception:
            pass           # đường truyền hỏng -> lùi về hỏi thăm

    KET_THUC = ("succeeded", "failed", "cancelled", "rejected")
    #: Bao nhiêu lượt đọc trạng thái hỏng LIÊN TIẾP thì thôi coi là "mạng chập".
    #:
    #: ⚠ Vòng này từng bọc `retrieve` trong `except: continue` trần. Một lỗi
    #: VĨNH VIỄN — client không có `retrieve`, khoá hết quyền, endpoint đổi tên —
    #: bị nuốt y như một cú mạng chập, và vòng lặp quay tới hết `timeout` rồi mới
    #: báo "job chưa xong". Sai chỗ nào cũng ra đúng một triệu chứng: treo.
    HONG_LIEN_TIEP_TOI_DA = 5
    t0 = time.monotonic()
    job = None
    hong = 0
    NHIP_HOI.vao()
    try:
        # Lần hỏi đầu bám `estimated_seconds` như SDK: hỏi sớm hơn thì chắc chắn
        # vẫn `queued`, chỉ tốn một lời gọi.
        dau = min(float(uoc_giay or 0) * 0.5, 5.0) if uoc_giay else 1.0
        time.sleep(max(0.0, min(dau, timeout)))
        while True:
            con = timeout - (time.monotonic() - t0)
            if con <= 0:
                raise JobTimeoutError(
                    "Cho qua {0:.0f}s ma job {1} chua xong.".format(timeout, job_id),
                    job=job, job_id=job_id, waited_seconds=time.monotonic() - t0)
            try:
                j = client.jobs.retrieve(job_id)
                job = j if isinstance(j, dict) else j.to_dict()
                hong = 0
            except Exception as e:
                # Lỗi đọc trạng thái KHÔNG phải lỗi job — hỏi lại vòng sau.
                # Nhưng hỏng LIÊN TIẾP thì đó không còn là mạng chập nữa: ném ra
                # để người gọi lùi về đường của SDK, thay vì quay vòng câm.
                hong += 1
                if hong >= HONG_LIEN_TIEP_TOI_DA:
                    raise
                time.sleep(min(NHIP_HOI.nhip(), max(0.0, con)))
                continue
            tt = job.get("status")
            if on_progress is not None:
                try:
                    on_progress(job)
                except Exception:       # noqa: BLE001 — báo tiến độ hỏng không giết job
                    pass
            if tt in KET_THUC:
                if tt == "succeeded":
                    return job
                raise JobFailedError(
                    "Job {0} ket thuc voi trang thai '{1}'.".format(job_id, tt), job=job)
            time.sleep(min(NHIP_HOI.nhip(), max(0.0, con)))
    finally:
        NHIP_HOI.ra()


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
    _jobs = getattr(client, "jobs", None)
    cho = getattr(_jobs, "wait", None)
    # `retrieve` là thứ vòng chờ RIÊNG cần (xem `_cho_job_xong`). Không có nó thì
    # đi đường của SDK ngay từ đầu — đừng để phát hiện muộn ở giữa vòng lặp.
    doc = getattr(_jobs, "retrieve", None)
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
    # Vòng chờ RIÊNG, tra lại nhịp hỏi mỗi vòng — xem `_cho_job_xong` và khối
    # `why` dài ở `NhipHoiTham`. `jobs.wait` của SDK chốt nhịp một lần rồi giữ
    # nguyên, và chính chỗ đó biến 134 job đang bay thành một cơn bão request.
    ma = _lay_truong(job, "id")
    if doc is not None:
        try:
            return _cho_job_xong(client, ma, timeout, on_progress=on_progress,
                                 uoc_giay=uoc_giay)
        except (AttributeError, ImportError):
            pass   # SDK đời cũ / client thiếu thứ gì đó -> đường dưới
    return cho(ma, timeout=timeout, on_progress=on_progress,
               estimated_seconds=uoc_giay)


# ── Tải file kết quả ──────────────────────────────────────────────────────────


#: Số lần thử tải một file kết quả. Xem chú thích trong :func:`tai_ve`.
TAI_VE_SO_LAN = 4


def tai_ve(url, dest_path, timeout=600.0, so_lan=None, ngu=None, headers=None):
    """Tải `url` về `dest_path`, trả lại đường dẫn thật đã ghi.

    ⚠ HÀM NÀY TẢI MỘT URL ĐÃ CÓ. Muốn tải kết quả của một job thì gọi
    :func:`tai_ket_qua` — nó biết đường `/download` không hết hạn và biết xin
    link mới khi link cũ chết. Link trong `output.url` hết hạn nhanh (từ
    14/08/2026: ~6 giờ, do Google giữ file), nên giữ lại URL để dùng sau là mất
    trắng thứ đã trả tiền.

    Ghi ra `.part` rồi mới đổi tên → không bao giờ để lại file dở dang trông như
    đã tải xong (caller chỉ kiểm `output_path.exists()`).

    ⚠ PHẢI THỬ LẠI — ĐÃ MẤT ẢNH ĐÃ TRẢ TIỀN VÌ THIẾU (07/08/2026)
    -------------------------------------------------------------
    Đo thật, nguyên văn::

        shopapi-img: tai ket qua ve dia that bai:
        RemoteProtocolError: peer closed connection without sending complete message

    Job đã **succeeded**, ảnh đã sinh ra, tiền đã trừ — rồi kho lưu trữ đứt kết
    nối giữa lúc tải. Bản cũ chỉ thử ĐÚNG MỘT LẦN nên ném lỗi luôn, và cả 64
    giây chờ lẫn số tiền đó bay sạch vì một cú hiccup mạng vài mili giây.

    Đây là kiểu hỏng tệ nhất trong một mẻ lớn: mỗi lần trượt là mất tiền thật,
    và nó xảy ra ở chỗ **sau khi mọi việc khó đã xong**.

    Chỉ thử lại lỗi MẠNG và `5xx`. `404` thì thôi — link hết hạn hoặc sai, gọi
    lại chỉ tổ chậm.
    """
    folder = os.path.dirname(os.path.abspath(str(dest_path)))
    if folder:
        os.makedirs(folder, exist_ok=True)

    temp_path = str(dest_path) + ".part"
    try:
        import httpx
    except ImportError:
        httpx = None

    lan_toi_da = int(so_lan or TAI_VE_SO_LAN)
    cho = ngu or time.sleep
    loi_cuoi = None
    for lan in range(lan_toi_da):
        try:
            return _tai_ve_mot_lan(url, temp_path, dest_path, timeout, httpx, headers)
        except _KhongTaiLai:
            raise
        except Exception as exc:
            loi_cuoi = exc
            if lan < lan_toi_da - 1:
                cho(min(8.0, 2.0 ** lan))
    raise loi_cuoi


def tai_ket_qua(client, job_id, dich, index=0, timeout=600.0, url_du_phong="",
                so_lan=None, ngu=None, log=None):
    """Tải file kết quả của một job về `dich`. Trả đường dẫn thật đã ghi.

    ═══ VÌ SAO KHÔNG TẢI THẲNG `output.url` NỮA ═══

    Từ 14/08/2026 ShopAPI thôi giữ bản sao ảnh/video: `output.url` trỏ thẳng
    sang Google (`flow-content.google`), và nó **chỉ sống khoảng 6 giờ** thay vì
    nhiều ngày. Tải ngay thì không sao; nhưng bất cứ chỗ nào giữ URL lại — hàng
    chờ nghẽn, mẻ chạy dài, một lần thử lại sau đêm — đều nhận về trang lỗi của
    Google cho thứ đã trả tiền.

    Nên hàm này đi ba tầng, rẻ và bền trước:

      1. `GET /v1/jobs/{id}/download` — địa chỉ này **KHÔNG hết hạn**, sống
         chừng nào job còn. Mỗi lần gọi máy chủ tự lái sang một đường tải tươi.
         Cần `Authorization: Bearer …` và **phải đi theo chuyển hướng** (nó trả
         `302`; không đi theo thì nhận đúng một thân RỖNG và tưởng API hỏng).
      2. `url_du_phong` — chính `output.url` vừa đọc được. Dùng khi đường trên
         không dựng nổi (khoá thiếu, endpoint lạ).
      3. Hỏi lại `GET /v1/jobs/{id}` để xin link TƯƠI rồi tải. Job còn thì luôn
         xin lại được — đây là đường thoát khi link cũ vừa chết.

    ⚠ ĐỪNG GÕ CỨNG THỜI HẠN NÀO vào mã, và đừng cache `output.url`. Cache
    `job_id` — nó không hết hạn.
    """
    def _noi(m):
        if log:
            try:
                log(m, "WARN")
            except Exception:
                pass

    loi = []
    base = str(getattr(client, "base_url", "") or DEFAULT_BASE_URL).rstrip("/")
    khoa = str(getattr(client, "api_key", "") or "")

    if job_id and khoa:
        duong = "{0}/v1/jobs/{1}/download".format(base, job_id)
        if int(index or 0) > 0:
            duong += "?index={0}".format(int(index))
        try:
            return tai_ve(duong, dich, timeout=timeout, so_lan=so_lan, ngu=ngu,
                          headers={"Authorization": "Bearer {0}".format(khoa)})
        except Exception as exc:
            loi.append("/download: {0}".format(mo_ta_loi(exc)))
            _noi("tai qua /download hong ({0}) -> thu output.url".format(type(exc).__name__))

    if url_du_phong:
        try:
            return tai_ve(url_du_phong, dich, timeout=timeout, so_lan=so_lan, ngu=ngu)
        except Exception as exc:
            loi.append("output.url: {0}".format(mo_ta_loi(exc)))
            _noi("link cu het han hoac hong -> xin link moi")

    # Tầng cuối: xin link tươi. `GET /v1/jobs/{id}` khiến máy chủ tự đi lấy
    # đường tải mới, nên đây là cách chữa đúng cho "link vừa hết hạn".
    if job_id and client is not None:
        try:
            j = client.jobs.retrieve(job_id)
            job = j if isinstance(j, dict) else (
                j.to_dict() if hasattr(j, "to_dict") else dict(j._data))
            outs = lay_outputs(job)
            i = int(index or 0)
            u = url_cua_output(outs[i]) if i < len(outs) else ""
            if u:
                return tai_ve(u, dich, timeout=timeout, so_lan=so_lan, ngu=ngu)
            loi.append("xin lai: job khong con output[{0}]".format(i))
        except Exception as exc:
            loi.append("xin lai: {0}".format(mo_ta_loi(exc)))

    raise IOError("Khong tai duoc ket qua job {0} (index {1}). Da thu: {2}"
                  .format(job_id or "?", index, " | ".join(loi) or "khong co duong nao"))


def duoi_cua_output(output, mac_dinh=".png"):
    """Đuôi file ĐÚNG cho một output, đọc từ trường `format`.

    ⚠ ĐỪNG ĐOÁN TỪ URL. Link Google không có đuôi file — cắt chuỗi ra chỉ được
    một mớ tham số chữ ký. Máy chủ đã tính sẵn `format` (`mp4`, `jpeg`, `png`…),
    dùng nó.
    """
    v = str(_lay(output, "format") or "").strip().lower().lstrip(".")
    if not v:
        return mac_dinh
    return "." + {"jpg": "jpg", "jpeg": "jpg"}.get(v, v)


class _KhongTaiLai(IOError):
    """Kho lưu trữ trả `4xx` — tải lại cũng vẫn thế (link hết hạn / sai)."""


def _tai_ve_mot_lan(url, temp_path, dest_path, timeout, httpx, headers=None):
    """Đúng MỘT lượt tải. Dọn `.part` khi hỏng để lần sau bắt đầu sạch."""
    try:
        if httpx is not None:
            with httpx.Client(timeout=timeout, follow_redirects=True) as http:
                with http.stream("GET", url, headers=headers or None) as response:
                    if response.status_code >= 400:
                        loi = ("Kho luu tru tra ma {0} khi tai ket qua (link co the da "
                               "het han).".format(response.status_code))
                        # 4xx: link het han/sai -> tai lai cung the. 5xx thi thu lai.
                        raise (_KhongTaiLai(loi) if response.status_code < 500 else IOError(loi))
                    with open(temp_path, "wb") as handle:
                        for chunk in response.iter_bytes(_CHUNK):
                            handle.write(chunk)
        else:
            import requests  # dự phòng: requests đã có sẵn trong requirements.txt
            response = requests.get(url, stream=True, timeout=timeout,
                                    headers=headers or None, allow_redirects=True)
            if response.status_code >= 400:
                loi = "Kho luu tru tra ma {0} khi tai ket qua.".format(response.status_code)
                raise (_KhongTaiLai(loi) if response.status_code < 500 else IOError(loi))
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
        # BA CỬA KHÁC NHAU CÙNG CHO RA `429`, và cách chữa ngược nhau. Máy chủ
        # xác nhận 15/08/2026:
        #
        #   `queue_full`      hàng chờ RIÊNG của khách đầy -> chờ hàng vơi;
        #                     hạ nhịp GỬI không giúp gì, hàng vẫn đầy chừng ấy.
        #   rate limit        vượt `requests_per_minute` -> đúng là gửi quá
        #                     nhanh, phải ghìm nhịp rót.
        #
        # Gộp làm một thì một nửa số lần ta kéo sai cần.
        return BiNghen(429, getattr(exc, "retry_after", None), exc,
                       ly_do=str(getattr(exc, "code", "") or "rate_limit"))
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
        # là đúng cả về tiền lẫn về nhịp.
        #
        # ⚠ NHƯNG NÓ KHÔNG PHẢI "GỬI QUÁ NHANH". Máy chủ nói rõ 15/08/2026:
        # `resource_exhausted` là mã CỦA WORKER, không phải của cửa vào API —
        # job đã được NHẬN rồi, nhà máy mới hết chỗ giữa chừng. Nghĩa là hệ
        # thống phía sau đang quá tải, chứ không phải client rót nhanh quá.
        #
        # Ghìm nhịp rót để chữa nó là kéo nhầm cần: rót chậm lại không làm nhà
        # máy rộng ra. Vẫn giữ mã `429` (nhà máy còn sống, khác `503`), nhưng
        # `ly_do` để nhánh xử lý biết mà đừng đụng vào nhịp gửi.
        return BiNghen(429, getattr(exc, "retry_after", None), exc,
                       ly_do="resource_exhausted")
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


def doc_v1_me(api_key=None, client=None, timeout=20.0):
    """Cả phản hồi `GET /v1/me`, dạng dict. `{}` khi hỏi không được.

    `tran_song_song` chỉ trả về MỘT con số cho MỘT loại job, nên nơi nào cần
    nhiều hơn thế phải gọi nó nhiều lần — mỗi lần một vòng HTTP, và các con số
    thu về không còn thuộc cùng một thời điểm.

    Bảng chỉ số trên giao diện cần đủ bộ cùng lúc: `limit`, `running`, `queued`,
    `capacity`, `accounts_usable`, `workers_online`, và câu `reason` mà máy chủ
    đã viết sẵn bằng tiếng Việt. Một lời gọi lấy hết, và mọi con số nhất quán.

    KHÔNG ném: nơi gọi là vòng poll của giao diện, một lỗi mạng không được phép
    làm chết nó.
    """
    try:
        if client is None:
            client = tao_client(api_key=api_key, timeout=float(timeout), max_retries=1)
        me = client.request("GET", "/v1/me")
        return me if isinstance(me, dict) else dict(me)
    except Exception:
        return {}


def doc_tran_chi_tiet(loai, api_key=None, client=None):
    """`(trần_đang_cấp, trần_cứng)` của một loại job, đọc trong MỘT lời gọi.

    ═══ VÌ SAO KHÔNG DÙNG HẰNG SỐ TRẦN CỨNG NỮA ═══

    :data:`TRAN_CUNG_MAC_DINH` và `shopapi._constants.CONCURRENCY_HARD_CAP` đều
    là BẢN CHÉP của một con số sống bên máy chủ, và cả hai đã cũ. Đo lúc 03:0x
    ngày 15/08/2026, `GET /v1/me` nói:

        image : limit  979   hard_cap 1536      (bản chép:  384)
        video : limit  374   hard_cap  832      (bản chép:   64)

    `_hoi_tran` lấy `min(limit, trần_cứng)`, nên bản chép cũ đang cắt video từ
    374 xuống **64** — bóp 5,8 lần — và ảnh từ 979 xuống 384. Máy chủ mở rộng
    nhà máy mà tool không hề ăn theo, đúng thứ ghi chú ở `TRAN_CUNG_MAC_DINH`
    đã cảnh báo sẽ xảy ra ("bản chép này sẽ cũ đi") — chỉ có điều nó cảnh báo
    nhầm chỗ: SDK cũng chỉ là một bản chép nữa, cũ y như thế.

    Máy chủ trả `hard_cap` ngay trong `/v1/me`. Đọc thẳng từ đó thì không còn
    bản chép nào để cũ. Hằng số chỉ còn là lưới cuối khi hỏi không được.
    """
    # ⚠ TRẦN ĐANG CẤP đi qua ĐÚNG MỘT CỬA: :func:`tran_song_song`. Đọc thẳng
    # `/v1/me` ở đây nữa là dựng cửa thứ hai cho cùng một con số — hai đường thì
    # sớm muộn lệch nhau, và mọi thứ đã cắm vào cửa cũ (kể cả bài kiểm) im lặng
    # thành vô hiệu.
    tran = int(tran_song_song(loai, api_key=api_key, mac_dinh=-1, client=client))
    return tran, tran_cung_may_chu(loai, api_key=api_key, client=client)


#: `hard_cap` đổi hoạ hoằn (chỉ khi máy chủ dựng thêm nhà máy), nên nhớ lâu.
_TRAN_CUNG_TTL = 300.0
_tran_cung_nho = {}
_tran_cung_khoa = threading.Lock()


def tran_cung_may_chu(loai, api_key=None, client=None, bay_gio=None):
    """Trần CỨNG do máy chủ tự khai trong `/v1/me`, có nhớ tạm.

    Lùi về hằng số chép sẵn khi hỏi không được — nhưng chỉ khi CHƯA đọc được
    lần nào. Đo 15/08/2026: máy chủ khai ảnh 1.536 / video 832, còn hằng số
    trong SDK vẫn là 384 / 64. Lấy nhầm bản chép là bóp video 5,8 lần.
    """
    gio = float(time.time() if bay_gio is None else bay_gio)
    with _tran_cung_khoa:
        cu = _tran_cung_nho.get(loai)
        if cu and (gio - cu[1]) < _TRAN_CUNG_TTL:
            return cu[0]
    me = doc_v1_me_chung(api_key=api_key, client=client, timeout=30.0)
    v = _so(_lay(_lay(_lay(_lay(me, "limits"), "concurrent_jobs_detail"), loai),
                 "hard_cap"), 0)
    if v <= 0:
        with _tran_cung_khoa:
            cu = _tran_cung_nho.get(loai)
        return cu[0] if cu else int(tran_cung(loai))
    with _tran_cung_khoa:
        _tran_cung_nho[loai] = (v, gio)
    return v


def doc_dang_chay(loai, api_key=None, client=None):
    """Số job loại này đang chạy của CẢ TÀI KHOẢN. `None` = hỏi không được.

    Bao gồm job của MỌI máy đang dùng chung khoá, không chỉ máy này.
    """
    me = doc_v1_me_chung(api_key=api_key, client=client, timeout=15.0)
    if not me:
        return None
    chi_tiet = _lay(_lay(_lay(me, "limits"), "concurrent_jobs_detail"), loai)
    if chi_tiet is None:
        return None
    v = _lay(chi_tiet, "running")
    if v is None:
        return None
    return max(0, _so(v, 0))


def nguoi_khac_dang_chay(loai, dang_bay_cua_toi=0, api_key=None, client=None):
    """Bao nhiêu job loại này đang chạy mà KHÔNG phải của mẻ này. `None` = không rõ.

    ═══ `concurrent_jobs` LÀ TỔNG, KHÔNG PHẢI SỐ CHỖ CÒN TRỐNG ═══

    Máy chủ xác nhận 15/08/2026: `limits.concurrent_jobs.<loại>` lấy thẳng từ
    `tran.<loại>.per_user` — **tổng** số job một khách được chạy song song.
    Client phải tự trừ số đang chạy mới ra số gửi thêm được.

    Tool trước đây coi nó là số chỗ trống. Với MỘT máy thì sai số còn nuốt được;
    với HAI máy chung một khoá thì hỏng hẳn: mỗi máy đếm tiến trình của riêng
    mình (`NhipSong` ghi file trong thư mục tạm CỦA MÁY ĐÓ), nên cả hai đều
    tưởng mình sở hữu trọn hạn mức. Tổng gửi ra gấp đôi hạn mức, và `429` là
    chuyện chắc chắn. Đo 12:00–12:28 ngày 15/08/2026: 399 cú `429` trong 28 phút
    trên đúng một khoá đang chạy ở hai máy.

    `running` của máy chủ đã đếm CẢ HAI MÁY, nên trừ nó đi là hết chồng chéo —
    không cần máy này biết gì về máy kia.

    ⚠ TRỪ PHẦN *NGƯỜI KHÁC*, KHÔNG TRỪ CẢ `running`. `running` đã bao gồm job
    của chính mình. Trừ trọn thì mỗi lần mình gửi thêm, trần của mình lại tụt
    đúng chừng ấy — tự bóp mình về 0. Lấy `running - của_mình` ra "phần người
    khác" thì con số đứng yên khi mình gửi thêm, đúng như nó phải thế.
    """
    tong = doc_dang_chay(loai, api_key=api_key, client=client)
    if tong is None:
        return None
    khac = max(0, tong - max(0, int(dang_bay_cua_toi)))
    return khac


def suc_khoe_nha_may(loai, api_key=None, client=None):
    """Một dòng về nhà máy loại này, để log nói được LỖI CỦA AI.

    ═══ VÌ SAO CẦN ═══

    Log 12:57–13:06 ngày 15/08/2026: gửi 582 job, máy chủ NHẬN 106 (xếp hàng
    tới vị trí 107), và trong chín phút **không một job nào xong** — video bình
    thường chỉ 60–90 giây. Đọc log thì chỉ thấy `429 / resource_exhausted` lặp
    lại, không có lấy một con số nào nói nhà máy đang ra sao.

    Không có dòng này thì mọi lượt chạy kém đều trông giống nhau, và câu hỏi
    "tool hay nhà máy" phải trả lời bằng phỏng đoán. `workers_online = 0` là
    dấu vân tay của một nhà máy chết (xem ghi chú `shopapi-nha-may-chet-duoi-tai`)
    và nó nằm sẵn trong `/v1/me` — chỉ là chưa ai in ra.

    Máy chủ còn viết sẵn `reason` bằng tiếng Việt cho đúng việc này.
    """
    me = doc_v1_me_chung(api_key=api_key, client=client, timeout=15.0)
    chi_tiet = _lay(_lay(_lay(me, "limits"), "concurrent_jobs_detail"), loai)
    if chi_tiet is None:
        return "khong doc duoc GET /v1/me"
    phan = []
    for khoa, nhan in (("workers_online", "tho online"), ("running", "dang chay"),
                       ("queued", "xep hang"), ("limit", "han muc"),
                       ("capacity", "suc chua"), ("accounts_usable", "tai khoan dung duoc")):
        v = _lay(chi_tiet, khoa)
        if v is not None:
            phan.append("{0} {1}".format(nhan, _so(v, 0)))
    ly_do = _lay(chi_tiet, "reason")
    if ly_do:
        phan.append("may chu noi: {0}".format(ly_do))
    return " | ".join(phan) if phan else "khong co so lieu nha may"


def con_tho_khong(loai, api_key=None, client=None):
    """Nhà máy loại này còn thợ online không? `None` = hỏi không được.

    ═══ VÌ SAO CẦN PHÂN BIỆT ═══

    `503 engine_unavailable` có HAI nghĩa hoàn toàn khác nhau, mà tên mã lỗi
    thì chỉ có một:

    * **Nhà máy chết hẳn** — `workers_online = 0`. Gửi thêm là phí, phải dừng.
    * **Thợ còn đủ nhưng lúc này không ai rảnh** — chen chúc nhất thời.

    Đối xử giống nhau thì rất đắt. `NhipDo.nha_may_dung()` kéo nhịp về SÀN (1),
    đóng băng 30 giây, rồi thăm dò lại bằng đúng 1 job — và luật leo là +1 mỗi
    lô mượt. Với job video ~500 giây một lô, bò từ 1 về lại 40 mất hàng giờ.
    Một cú nghẹt thoáng qua đổi lấy cả buổi chiều chạy ở nhịp 1.

    Đo thật 11:03–11:05 ngày 15/08/2026: chín tiến trình video, mỗi cái ăn một
    `503` rồi tụt từ `tran may chu 124` xuống `nhip 1.0`, trong khi CÙNG LÚC
    hai mã khác vẫn được nhận job và xếp hàng thứ 27. Nhà máy rõ ràng còn sống.

    Còn thợ thì `429` mới là cách hiểu đúng: chia đôi rồi bò lên lại, không
    đóng băng. Xem chỗ dùng ở `shopapi_batch`.

    Đọc qua `doc_v1_me_chung` nên nhiều tiến trình chỉ tốn MỘT lời gọi trong
    mỗi 8 giây — rẻ hơn nhiều so với cái giá của một lần đoán sai.
    """
    me = doc_v1_me_chung(api_key=api_key, client=client, timeout=15.0)
    if not me:
        return None
    chi_tiet = _lay(_lay(_lay(me, "limits"), "concurrent_jobs_detail"), loai)
    if chi_tiet is None:
        return None
    tho = _lay(chi_tiet, "workers_online")
    if tho is None:
        return None
    return _so(tho, 0) > 0


def _lay(o, khoa):
    """Đọc một khoá từ dict HOẶC từ `Model` của SDK. `None` khi không có."""
    if o is None:
        return None
    if isinstance(o, dict):
        return o.get(khoa)
    try:
        return o[khoa]
    except Exception:
        return getattr(o, khoa, None)


def _so(v, mac_dinh):
    try:
        return int(v)
    except (TypeError, ValueError):
        return mac_dinh


# ── Nhịp sống: mấy tiến trình đang tranh nhau cùng một loại job ──────────────
#
# Mỗi mã chạy trong MỘT TIẾN TRÌNH RIÊNG, và trần máy chủ (`limit`) là của CẢ
# TÀI KHOẢN chứ không phải của một tiến trình. Không có chỗ nào để tám tiến
# trình bàn với nhau, nên bản trước chia bằng một con số cứng trong
# `settings.yaml` — `max_concurrent: 40`, `shopapi_video_concurrency: 16`.
#
# Con số cứng sai theo cả hai chiều, và luôn sai:
#
#   * đặt thấp  -> tám mã ăn 320/979 chỗ ảnh, bỏ phí hai phần ba nhà máy;
#   * đặt cao   -> lúc chỉ MỘT mã chạy thì nó xin trọn phần của tám mã.
#
# Người dùng thấy đúng cái đó: "xin 290 mà chỉ 22 job chạy thật", và "video còn
# dư chỗ, 1 mã × 16".
#
# Cách chữa không cần ai bàn với ai: mỗi tiến trình đang chạy một mẻ để lại một
# file trong thư mục chung, chạm lại vài chục giây một lần. Đếm file còn tươi
# ra ĐÚNG số tiến trình đang tranh nhau loại job đó — sống thật, không phải con
# số trong cấu hình. Tiến trình chết đột ngột thì file của nó nguội đi và tự
# hết tính, khỏi cần dọn.
#
# Đếm RIÊNG theo loại job là điều quan trọng: lúc bảy mã đang làm ảnh và một mã
# làm video, mã video phải được trọn 374 chỗ video chứ không phải một phần tám.

#: File nhịp sống cũ hơn ngần này giây = tiến trình đã chết, không tính nữa.
NHIP_SONG_HAN = 90.0

#: Chạm lại file nhịp sống thưa nhất ngần này giây một lần.
NHIP_SONG_NHIP = 20.0


def thu_muc_nhip_song():
    """Thư mục chung để các tiến trình mã điểm danh. Tạo sẵn nếu chưa có."""
    d = (os.environ.get("SHOPAPI_NHIP_DIR") or "").strip()
    if not d:
        import tempfile
        d = os.path.join(tempfile.gettempdir(), "shopapi-nhip")
    try:
        os.makedirs(d, exist_ok=True)
    except OSError:
        pass
    return d


class NhipSong:
    """Ghi danh "tiến trình này đang chạy một mẻ `loai`" cho tới khi `dong()`.

    Dùng như context manager. Hỏng ở đây KHÔNG được làm chết mẻ chạy: thiếu
    điểm danh thì cùng lắm chia trần bảo thủ hơn, còn ném lỗi là mất cả mẻ.
    """

    def __init__(self, loai, thu_muc=None, pid=None):
        self.loai = str(loai)
        self.thu_muc = thu_muc or thu_muc_nhip_song()
        self.pid = int(pid if pid is not None else os.getpid())
        # `id(self)` để một tiến trình chạy hai mẻ cùng loại vẫn ra hai chỗ ngồi.
        self.duong_dan = os.path.join(
            self.thu_muc, "{0}-{1}-{2}".format(self.loai, self.pid, id(self)))
        self._lan_cuoi = 0.0
        self.con_viec = None
        self.diem_danh(ep=True)

    def diem_danh(self, ep=False, con_viec=None):
        """Chạm lại file. Gọi thoải mái — tự thưa ra `NHIP_SONG_NHIP` giây/lần.

        `con_viec` là SỐ JOB TIẾN TRÌNH NÀY CÒN PHẢI LÀM. Ghi ra để anh em chia
        trần theo việc chứ không chia theo đầu người — xem :func:`chia_theo_viec`.
        """
        gio = time.time()
        if con_viec is not None:
            try:
                self.con_viec = max(0, int(con_viec))
            except (TypeError, ValueError):
                pass
        if not ep and (gio - self._lan_cuoi) < NHIP_SONG_NHIP:
            return
        try:
            with open(self.duong_dan, "w", encoding="utf-8") as f:
                f.write(str(int(gio)))
                if self.con_viec is not None:
                    f.write("\n{0}".format(int(self.con_viec)))
            self._lan_cuoi = gio
        except OSError:
            pass

    def dong(self):
        try:
            os.remove(self.duong_dan)
        except OSError:
            pass

    def __enter__(self):
        return self

    def __exit__(self, *a):
        self.dong()
        return False


def dem_ban_dang_chay(loai, thu_muc=None, han=None, bay_gio=None):
    """Bao nhiêu tiến trình đang chạy mẻ `loai` NGAY LÚC NÀY. Tối thiểu 1.

    Trả 1 khi không đếm được — chia cho 1 là không chia, tức là lùi về hành vi
    "một mình một chợ". Thà xin rộng rồi để `429`/hàng chờ kéo xuống, còn hơn
    chia cho một con số bịa rồi tự bóp.
    """
    thu_muc = thu_muc or thu_muc_nhip_song()
    han = float(NHIP_SONG_HAN if han is None else han)
    gio = float(time.time() if bay_gio is None else bay_gio)
    # `loai=""` -> đếm MỌI loại (dùng cho ngân sách luồng của cả máy).
    tien_to = (str(loai) + "-") if loai else ""
    n = 0
    try:
        for ten in os.listdir(thu_muc):
            if ten == _TEN_FILE_NGAN_SACH:
                continue          # file ngân sách, không phải một chỗ ngồi
            if tien_to and not ten.startswith(tien_to):
                continue
            try:
                if (gio - os.path.getmtime(os.path.join(thu_muc, ten))) <= han:
                    n += 1
            except OSError:
                pass
    except OSError:
        return 1
    return max(1, n)


def doc_viec_dang_cho(loai, thu_muc=None, han=None, bay_gio=None):
    """Số việc còn lại của TỪNG tiến trình đang chạy mẻ `loai`.

    Trả về danh sách; phần tử là `int` khi tiến trình đó có khai, `None` khi
    không (bản cũ chỉ ghi mốc giờ). Danh sách rỗng = không đọc được gì.
    """
    thu_muc = thu_muc or thu_muc_nhip_song()
    han = float(NHIP_SONG_HAN if han is None else han)
    gio = float(time.time() if bay_gio is None else bay_gio)
    tien_to = (str(loai) + "-") if loai else ""
    ds = []
    try:
        ten_ds = os.listdir(thu_muc)
    except OSError:
        return ds
    for ten in ten_ds:
        if ten == _TEN_FILE_NGAN_SACH:
            continue
        if tien_to and not ten.startswith(tien_to):
            continue
        duong = os.path.join(thu_muc, ten)
        try:
            if (gio - os.path.getmtime(duong)) > han:
                continue
            with open(duong, "r", encoding="utf-8") as f:
                dong = f.read().split("\n")
        except OSError:
            continue
        if len(dong) >= 2 and dong[1].strip():
            try:
                ds.append(max(0, int(dong[1].strip())))
                continue
            except ValueError:
                pass
        ds.append(None)
    return ds


def chia_theo_viec(loai, tran, viec_cua_toi, thu_muc=None, han=None, bay_gio=None):
    """Suất của tiến trình này trong `tran`, chia THEO VIỆC chứ không theo đầu người.

    ═══ VÌ SAO KHÔNG CHIA ĐỀU ═══

    Chia đều cho số tiến trình nghe công bằng, nhưng nó phát chỗ cho những
    tiến trình không có việc mà tiêu, rồi bỏ đói đúng tiến trình đang ôm cả
    đống. Đo thật 11:04 ngày 15/08/2026, chín tiến trình video, trần 374:

        TH1-0199   1 video    được 41 chỗ  -> dùng 1,  phí 40
        TH1-0304   1 video    được 41 chỗ  -> dùng 1,  phí 40
        TH1-0200   1 video    được 41 chỗ  -> dùng 1,  phí 40
        TH1-0126   3 video    được 41 chỗ  -> dùng 3,  phí 38
        TH2-0007   4 video    được 41 chỗ  -> dùng 4,  phí 37
        TH2-0008  12 video    được 41 chỗ  -> dùng 12, phí 29
        TH2-0056  17 video    được 41 chỗ  -> dùng 17, phí 24
        TH1-0100  45 video    được 41 chỗ  -> KHÔNG ĐỦ
        TH2-0033  52 video    được 41 chỗ  -> KHÔNG ĐỦ, 11 việc nằm chờ

    Tổng việc thật 136, tổng chỗ phát ra 369. Hai tiến trình có việc thì thiếu
    chỗ, bảy tiến trình gần như rỗng thì giữ 233 chỗ không dùng tới.

    Chia theo việc thì TH2-0033 được 52/136 × 374 = 143 chỗ, thừa sức nuốt cả
    52 việc trong MỘT lô thay vì bốn lô.

    ═══ LÙI VỀ AN TOÀN ═══

    Tiến trình nào không khai số việc (bản cũ) được tính bằng ĐÚNG số việc của
    mình. Nên khi cả máy còn chạy bản cũ, tổng = N × việc-của-tôi và công thức
    rút gọn về `tran / N` — đúng bằng cách chia đều trước đây, không lệch một
    đơn vị. Nâng cấp dần từng tiến trình cũng không ai bị thiệt.
    """
    try:
        cua_toi = max(1, int(viec_cua_toi))
    except (TypeError, ValueError):
        return None
    ds = doc_viec_dang_cho(loai, thu_muc=thu_muc, han=han, bay_gio=bay_gio)
    if not ds:
        return None
    tong = sum(cua_toi if v is None else max(1, v) for v in ds)
    if tong <= 0:
        return None
    return max(1, int(int(tran) * cua_toi // tong))


# ── Ngân sách LUỒNG của cả máy ───────────────────────────────────────────────
#
# Trần máy chủ không phải trần duy nhất. Thiết kế hiện tại mở MỘT LUỒNG cho mỗi
# job đang bay (`pool.submit(_boc, ...)` — gửi, chờ, tải file, ghi Excel), nên
# số job chạy cùng lúc trên máy này bị chặn bởi số luồng Windows chịu mở.
#
# Đã chạm trần thật: 8 tiến trình mã × 88 luồng = 704 luồng thì
# `ThreadPoolExecutor.submit` bắt đầu ném `RuntimeError: can't start new
# thread`. Nên ngân sách mặc định để dưới mức đó, và nó là ngân sách CỦA CẢ
# MÁY — chia cho số tiến trình đang chạy, chứ không phải mỗi tiến trình một suất.
#
# Con số này TỰ HẠ khi máy nói không: tiến trình nào ăn `RuntimeError` sẽ chia
# đôi ngân sách và ghi vào file chung, nên mọi tiến trình anh em cùng biết mà
# lùi — thay vì từng đứa một tự đâm vào tường rồi tự rút ra.

#: Tổng số job được phép bay cùng lúc trên MỘT MÁY, cộng cả ảnh lẫn video.
#:
#: ⚠ CON SỐ NÀY ĐÃ ĐO, KHÔNG PHẢI ĐOÁN. Ghi chú cũ nói Windows từ chối mở luồng
#: quanh 704, nên bản đầu để 600. Đo lại ngày 15/08/2026 trên chính máy này:
#: **3.000 luồng mở hết, không chạm trần, bộ nhớ không đáng kể**. Luồng chờ
#: mạng gần như không tốn gì — sự cố 704 hôm đó là chuyện khác, không phải trần
#: luồng của hệ điều hành.
#:
#: Trần thật giờ là hai thứ khác, và cả hai đều cao hơn 600 nhiều:
#:
#:   * máy chủ đang mời 979 chỗ ảnh + 374 chỗ video = 1.353;
#:   * `httpx` chặn 100 kết nối/tiến trình -> đã nới, xem :data:`TRAN_KET_NOI`.
#:
#: Để 1.400 là vừa đủ ôm trọn phần máy chủ mời mà vẫn còn chốt chặn. Nó TỰ HẠ
#: khi máy thật sự nói không (xem :func:`ha_ngan_sach_luong`), nên đặt rộng
#: không phải là đánh cược — đặt hẹp mới là bỏ phí chắc chắn.
NGAN_SACH_LUONG_MAC_DINH = 1400

#: Không bao giờ hạ ngân sách xuống dưới mức này — hạ nữa là tool đứng im.
NGAN_SACH_LUONG_SAN = 24

_TEN_FILE_NGAN_SACH = "ngan-sach-luong"


#: Mức đã hạ chỉ có giá trị trong ngần này giây, sau đó tự lành lại.
#:
#: ⚠ NGÂN SÁCH CHỈ BIẾT ĐI XUỐNG LÀ MỘT CÁI BẪY MỘT CHIỀU. Đã dính thật ngày
#: 15/08/2026: bộ kiểm thử mô phỏng "máy hết luồng" chạy trên thư mục nhịp sống
#: CHUNG (lúc đó chưa cách ly), gọi `ha_ngan_sach_luong` mấy lượt, và ghim ngân
#: sách của cả máy xuống sàn **24**. Sau đó mọi lần chạy THẬT đều khởi động ở 24
#: job thay vì 979 — không một dòng nào nói vì sao, và không có đường nào tự lên
#: lại. Một cú nghẹn thoáng qua (hay một lần chạy kiểm) không được phép định
#: đoạt phần còn lại của đời máy.
NGAN_SACH_HA_TTL = 1800.0


def ngan_sach_luong(thu_muc=None, bay_gio=None):
    """Ngân sách luồng của cả máy hiện tại.

    Ưu tiên mức đã hạ — nó là bằng chứng máy này thật sự không mở nổi nhiều hơn
    thế. Nhưng bằng chứng đó CÓ HẠN DÙNG (:data:`NGAN_SACH_HA_TTL`): hết hạn thì
    quay về mức mặc định và thăm dò lại. Máy vẫn chật thì lần nghẹn sau lại hạ,
    mất đúng một lô; còn máy đã rảnh thì tool lấy lại được cả nhà máy.
    """
    thu_muc = thu_muc or thu_muc_nhip_song()
    mac_dinh = max(NGAN_SACH_LUONG_SAN,
                   _so(os.environ.get("SHOPAPI_NGAN_SACH_LUONG"), NGAN_SACH_LUONG_MAC_DINH))
    d = os.path.join(thu_muc, _TEN_FILE_NGAN_SACH)
    try:
        gio = float(time.time() if bay_gio is None else bay_gio)
        if (gio - os.path.getmtime(d)) > NGAN_SACH_HA_TTL:
            return mac_dinh          # bằng chứng đã cũ -> thăm dò lại
        with open(d, "r", encoding="utf-8") as f:
            v = int((f.read() or "0").strip())
        if v > 0:
            return max(NGAN_SACH_LUONG_SAN, min(v, mac_dinh))
    except (OSError, ValueError):
        pass
    return mac_dinh


def ha_ngan_sach_luong(thu_muc=None):
    """Máy vừa từ chối mở luồng → chia đôi ngân sách CHUNG. Trả mức mới.

    Ghi ra file để mọi tiến trình anh em cùng lùi. Một đứa đâm vào tường là cả
    nhà biết, thay vì bảy đứa còn lại lần lượt đâm lại đúng chỗ đó.
    """
    thu_muc = thu_muc or thu_muc_nhip_song()
    moi = max(NGAN_SACH_LUONG_SAN, ngan_sach_luong(thu_muc) // 2)
    # Ghi lại mốc thời gian: mức hạ có hạn dùng, xem `NGAN_SACH_HA_TTL`.
    try:
        with open(os.path.join(thu_muc, _TEN_FILE_NGAN_SACH), "w", encoding="utf-8") as f:
            f.write(str(moi))
    except OSError:
        pass
    return moi


def phan_luong_cua_toi(thu_muc=None, so_ban=None):
    """Suất luồng của TIẾN TRÌNH NÀY = ngân sách cả máy ÷ số tiến trình đang chạy.

    Đếm bạn theo MỌI loại job, không riêng loại mình đang làm: luồng là tài
    nguyên chung của máy, mã đang dựng video cũng ăn vào đúng cái ngân sách mà
    mã đang dựng ảnh đang dùng.
    """
    thu_muc = thu_muc or thu_muc_nhip_song()
    n = so_ban if so_ban else dem_ban_dang_chay("", thu_muc=thu_muc)
    return max(1, ngan_sach_luong(thu_muc) // max(1, int(n)))


def don_nhip_song_cu(thu_muc=None, han=None):
    """Xoá file nhịp sống đã nguội. Gọi lúc khởi động cho thư mục khỏi phình."""
    thu_muc = thu_muc or thu_muc_nhip_song()
    han = float(NHIP_SONG_HAN if han is None else han) * 4
    gio = time.time()
    try:
        for ten in os.listdir(thu_muc):
            d = os.path.join(thu_muc, ten)
            try:
                if (gio - os.path.getmtime(d)) > han:
                    os.remove(d)
            except OSError:
                pass
    except OSError:
        pass


#: `/v1/me` đọc được thì dùng chung cho MỌI tiến trình mã trên máy này, trong
#: ngần này giây. Trần máy chủ đổi chậm (theo sức chứa nhà máy), nên vài giây
#: cũ không hại gì — mà lợi thì lớn.
ME_CHUNG_TTL = 8.0

_TEN_FILE_ME = "v1-me.json"


def doc_v1_me_chung(api_key=None, client=None, timeout=20.0, thu_muc=None, bay_gio=None):
    """`/v1/me` dùng CHUNG cho mọi tiến trình mã trên máy này.

    ═══ VÌ SAO CẦN, VÀ NÓ ĐANG LÀM HỎNG CÁI GÌ ═══

    Mỗi mã là một tiến trình riêng, và mỗi tiến trình tự hỏi `/v1/me` để biết
    trần. Hai mươi tư mã cùng hỏi, cộng với chính tải job đang gửi, là đủ để
    hạn mức 1.000 request/phút nuốt hết những lời hỏi trạng thái đó.

    Hậu quả nhìn thấy trong log 17:43–17:44 ngày 15/08/2026: gần như MỌI mã đều
    ghi `khong hoi duoc GET /v1/me`, rồi rơi về trần mù 32. Mười mã × 32 = 320
    chỗ xin, trong khi máy chủ chỉ cấp 345 và đang chia cho từng ấy tiến trình
    — nên `429` liên tục, AIMD chia đôi mãi (`nhip 4.5 cho phep 4`), và cả dây
    chuyền bò.

    Nghịch lý: tool hỏi trạng thái nhiều tới mức không còn đọc nổi trạng thái.

    Một lời hỏi, dùng chung qua file, xoá hẳn nghịch lý đó: 24 tiến trình tốn
    một lời gọi mỗi 8 giây thay vì 24 lời gọi.

    Ghi bằng file tạm rồi đổi tên: tiến trình khác đọc giữa chừng không thấy
    file vỡ đôi.
    """
    thu_muc = thu_muc or thu_muc_nhip_song()
    d = os.path.join(thu_muc, _TEN_FILE_ME)
    gio = float(time.time() if bay_gio is None else bay_gio)
    try:
        if (gio - os.path.getmtime(d)) <= ME_CHUNG_TTL:
            import json as _json
            with open(d, "r", encoding="utf-8") as f:
                me = _json.load(f)
            if isinstance(me, dict) and me:
                return me
    except (OSError, ValueError):
        pass

    # ⚠ CHỐT ĐI HỎI — nếu không thì lúc khởi động cả đàn cùng trượt và cùng gọi.
    #
    # Đo thật: 24 tiến trình hỏi CÙNG LÚC trên bộ đệm rỗng vẫn tốn đủ 24 lời
    # gọi, vì chưa đứa nào kịp ghi. Đúng cảnh 17:43 ngày 15/08/2026 — 24 mã bật
    # lên trong mươi giây và cùng đâm vào `/v1/me`.
    #
    # Chốt bằng `O_EXCL`: đứa tạo được file chốt thì đi hỏi, những đứa còn lại
    # chờ một nhịp ngắn rồi đọc lại bộ đệm. Chốt cũng có hạn dùng, để một tiến
    # trình chết giữa chừng không khoá cả nhà.
    _chot = d + ".chot"
    _cua_toi = False
    try:
        if (gio - os.path.getmtime(_chot)) > max(30.0, ME_CHUNG_TTL * 3):
            os.remove(_chot)            # chốt nguội = chủ nó đã chết
    except OSError:
        pass
    try:
        os.close(os.open(_chot, os.O_CREAT | os.O_EXCL | os.O_WRONLY))
        _cua_toi = True
    except OSError:
        # Đứa khác đang hỏi: chờ ngắn rồi đọc lại. Chờ hụt cũng không sao —
        # rơi xuống dưới và tự hỏi, cùng lắm thừa một lời gọi.
        for _ in range(20):
            time.sleep(0.25)
            try:
                if (time.time() - os.path.getmtime(d)) <= ME_CHUNG_TTL:
                    import json as _json
                    with open(d, "r", encoding="utf-8") as f:
                        me = _json.load(f)
                    if isinstance(me, dict) and me:
                        return me
            except (OSError, ValueError):
                pass

    try:
        me = doc_v1_me(api_key=api_key, client=client, timeout=timeout)
    finally:
        if _cua_toi:
            try:
                os.remove(_chot)
            except OSError:
                pass
    if not me:
        # Hỏi hụt: thà dùng bản cũ QUÁ HẠN còn hơn không có gì. Trần cũ vài chục
        # giây vẫn sát hơn hẳn con số mù.
        try:
            import json as _json
            with open(d, "r", encoding="utf-8") as f:
                cu_roi = _json.load(f)
            if isinstance(cu_roi, dict) and cu_roi:
                return cu_roi
        except (OSError, ValueError):
            pass
        return {}
    try:
        import json as _json
        tam = d + ".{0}.tmp".format(os.getpid())
        with open(tam, "w", encoding="utf-8") as f:
            _json.dump(_thuan_json(me), f)
        os.replace(tam, d)
    except (OSError, ValueError, TypeError):
        pass
    return me


def _thuan_json(o):
    """Đổi `Model` của SDK thành dict/list thuần để `json.dump` nuốt được."""
    if isinstance(o, dict):
        return {k: _thuan_json(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [_thuan_json(v) for v in o]
    d = getattr(o, "_data", None)
    if isinstance(d, dict):
        return {k: _thuan_json(v) for k, v in d.items()}
    return o


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
    # ĐI QUA BẢN DÙNG CHUNG. Gọi thẳng `client.tran_song_song` là mỗi tiến
    # trình một lời hỏi — 24 mã thì 24 lời hỏi, và chúng tự bóp nghẹt nhau.
    try:
        me = doc_v1_me_chung(api_key=api_key, client=client, timeout=30.0)
        v = _lay(_lay(_lay(me, "limits"), "concurrent_jobs"), loai)
        if v is not None:
            return int(v)
    except Exception:
        pass
    return mac_dinh
