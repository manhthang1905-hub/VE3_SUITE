"""Móc chung cho bộ kiểm nhánh "gọi API shopapi".

NGUYÊN TẮC SỐ MỘT: **không chạm mạng thật, không tiêu tiền thật.**
Mọi bài kiểm ở đây dùng client GIẢ (`FakeClient`) và hàm tải GIẢ. Không có bài
nào được phép dựng `shopapi.ShopAPI` thật hay gọi `api.shopapi.vn` — một job ảnh
là 100₫, một job video là 500₫, chạy CI vài chục lần là mất tiền thật.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

SUITE_ROOT = Path(__file__).resolve().parents[1]
VE3_DIR = SUITE_ROOT / "tools" / "ve3"
ENGINE_DIR = SUITE_ROOT / "veo3top_engine"

for _p in (str(VE3_DIR), str(ENGINE_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)


# ── Khoá giả ─────────────────────────────────────────────────────────────────

#: Khoá GIẢ. Có tiền tố `sk_live_` để bài kiểm che khoá chạy đúng nhánh thật,
#: nhưng nó không tồn tại trên máy chủ nào.
KHOA_GIA = "sk_gia_KHOAGIAKHONGDUNGDUOC1234567890"


@pytest.fixture(autouse=True)
def chan_khoa_that(monkeypatch):
    """Xoá mọi khoá thật khỏi môi trường trước MỖI bài kiểm.

    Máy của chủ dự án có `SHOPAPI_KEY` thật trong môi trường. Không dọn thì một
    bài kiểm lỡ tay sẽ bắn job thật bằng tiền thật.
    """
    for name in ("SHOPAPI_KEY", "SHOPAPI_API_KEY", "SHOPAPI_KEY_FILE", "SHOPAPI_SDK_PATH"):
        monkeypatch.delenv(name, raising=False)
    # Kho khoá cũng phải trỏ vào thư mục rỗng, không phải %APPDATA% thật.
    yield


@pytest.fixture
def sc():
    """Module `shopapi_common` đã nạp."""
    import shopapi_common
    return shopapi_common


@pytest.fixture
def lui_nhip_0(monkeypatch):
    """Rút quãng lùi nhịp sau `429` về 0 — **chỉ cho bài KHÔNG đo quãng đó**.

    Từ bản sửa 07/08/2026, một cú `429` khoá cả mẻ lại 15 giây thật
    (`shopapi_batch.LUI_NHIP_GIAY`) để tool không gửi lại ngay lập tức. Đúng cho
    sản phẩm, nhưng bài nào chỉ muốn kiểm "429 được đếm là THỬ LẠI chứ không
    phải THẤT BẠI" thì nó chỉ tổ ngồi chờ 15 giây cho mỗi bài.

    ⚠ Đừng dùng fixture này cho bài đo chính quãng lùi nhịp — dùng là bài đó
    xanh mà chẳng kiểm gì. Quãng lùi nhịp được đóng đinh riêng ở
    `test_cong_hang_cho.py`.
    """
    import shopapi_batch
    monkeypatch.setattr(shopapi_batch, "LUI_NHIP_GIAY", 0.0)


# ── Client giả ───────────────────────────────────────────────────────────────


class GhiNhan:
    """Ghi lại mọi lời gọi để bài kiểm soi lại tham số đã gửi lên."""

    def __init__(self):
        self.image_calls = []
        self.video_calls = []
        self.uploads = []


class FakeUploads:
    def __init__(self, so: GhiNhan):
        self._so = so

    def upload_file(self, file, filename=None, content_type=None):
        self._so.uploads.append({"file": file, "filename": filename})
        return "https://cdn.example.invalid/upload/{0}".format(len(self._so.uploads))


class FakeImages:
    def __init__(self, so: GhiNhan, job=None, loi=None):
        self._so = so
        self._job = job
        self._loi = loi

    def create_and_wait(self, **kwargs):
        self._so.image_calls.append(kwargs)
        if self._loi is not None:
            raise self._loi
        on_progress = kwargs.get("on_progress")
        if on_progress is not None:
            on_progress({"status": "running", "progress": 50})
        return self._job


class FakeVideos:
    def __init__(self, so: GhiNhan, job=None, loi=None):
        self._so = so
        self._job = job
        self._loi = loi

    def create_and_wait(self, **kwargs):
        self._so.video_calls.append(kwargs)
        if self._loi is not None:
            raise self._loi
        on_progress = kwargs.get("on_progress")
        if on_progress is not None:
            on_progress({"status": "running", "progress": 90})
        return self._job


class FakeClient:
    """Đứng thay `shopapi.ShopAPI`. KHÔNG mở một kết nối mạng nào."""

    def __init__(self, *, image_job=None, video_job=None, image_loi=None, video_loi=None,
                 upload_loi=None):
        self.so = GhiNhan()
        self.uploads = FakeUploads(self.so)
        self.images = FakeImages(self.so, image_job, image_loi)
        self.videos = FakeVideos(self.so, video_job, video_loi)
        if upload_loi is not None:
            def _no(file, filename=None, content_type=None):
                raise upload_loi
            self.uploads.upload_file = _no


def job_anh(n=1, job_id="job_img_1"):
    """Khuôn job ảnh ĐÚNG như máy chủ trả (xem `job.mapper.ts`).

    ⚠ `n == 1`: máy chủ CHỈ gửi `output`, KHÔNG có khoá `outputs`.
       `n > 1` : gửi cả hai, `output` = phần tử đầu của `outputs`.
    """
    outs = [
        {"url": "https://cdn.example.invalid/anh{0}.png".format(i + 1),
         "expires_at": "2026-08-14T00:00:00Z", "size_bytes": 1234,
         "format": "png", "index": i}
        for i in range(n)
    ]
    job = {"id": job_id, "object": "job", "type": "image", "status": "succeeded",
           "progress": 100, "cost": "100000000", "output": outs[0]}
    if n > 1:
        job["outputs"] = outs
    return job


def job_video(job_id="job_vid_1"):
    out = {"url": "https://cdn.example.invalid/clip.mp4",
           "expires_at": "2026-08-14T00:00:00Z", "size_bytes": 999,
           "format": "mp4", "index": 0}
    return {"id": job_id, "object": "job", "type": "video", "status": "succeeded",
            "progress": 100, "cost": "500000000", "output": out}


@pytest.fixture
def tai_ve_gia(monkeypatch, sc):
    """Thay :func:`shopapi_common.tai_ve` bằng bản ghi file cục bộ.

    Bài kiểm vẫn kiểm được điều quan trọng nhất — **file có thật sự nằm trên
    đĩa ở đúng `output_path` không** — mà không cần một byte nào đi qua mạng.
    """
    da_tai = []

    def _gia(url, dest_path, timeout=600.0):
        da_tai.append((url, str(dest_path)))
        folder = os.path.dirname(os.path.abspath(str(dest_path)))
        if folder:
            os.makedirs(folder, exist_ok=True)
        with open(str(dest_path), "wb") as handle:
            handle.write(b"NOI-DUNG-GIA:" + url.encode("utf-8"))
        return str(dest_path)

    monkeypatch.setattr(sc, "tai_ve", _gia)
    return da_tai


@pytest.fixture
def nhat_ky():
    """Thu log của nhánh mới để bài kiểm khẳng định "có cảnh báo, không nuốt lỗi"."""
    dong = []

    def _log(msg, level="INFO"):
        dong.append((level, str(msg)))

    _log.dong = dong
    _log.text = lambda: "\n".join(m for _, m in dong)
    return _log
