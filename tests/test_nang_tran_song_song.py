# -*- coding: utf-8 -*-
"""Máy cũ phải được nâng trần song song, và KHÔNG được nâng nhầm.

════════════════════════════════════════════════════════════════════════════
VÌ SAO CÓ BÀI NÀY — 14/08/2026
════════════════════════════════════════════════════════════════════════════

Đo trên máy chủ thật, hai khách đang chạy::

    máy chủ mời mỗi khách : ~979 chỗ ảnh · ~288 chỗ video
    khách GIỮ cùng lúc    :     2 chỗ ảnh  (đỉnh 2 · trung bình 1,2–2,0)

Khai thác 0,2% phần ảnh. Ba worker `idle`, hàng chờ 1–2 giây, ảnh p50 32 giây —
máy chủ rảnh suốt, trong khi chủ dự án thấy *"1 phút 1 ảnh 1 video"* và không
biết lỗi ở máy chủ hay ở tool.

Nguyên nhân: ba con số song song sống trong `settings.yaml`, file bị chặn bởi
`PROTECTED_PATHS` **và** `GIT_PROTECTED_FILES` **và** `.gitignore` — nên không
bản cập nhật nào chạm được. `setdefault` cũng vô dụng: nó chỉ điền khi khoá
VẮNG MẶT, còn máy cũ có khoá với giá trị nhỏ thời Chrome.

Bài này chạy HÀM THẬT chứ không đọc mã bằng AST. Đọc mã bắt được "ai đổi hằng
số", nhưng thứ dễ sai ở đây là LUẬT nâng — mà luật thì chỉ có chạy mới lộ.

════════════════════════════════════════════════════════════════════════════
BA CÁCH SAI, VÀ CÁI NÀO CŨNG TỆ HƠN LÀ KHÔNG LÀM GÌ
════════════════════════════════════════════════════════════════════════════

1. Nâng cả số `0`. `0` nghĩa "theo trần ĐỘNG của máy chủ" — đúng yêu cầu của
   chủ dự án ("server xử lý được bao nhiêu thì cứ dùng bấy nhiêu, đừng làm
   cứng"). Biến nó thành 40 là thay trần động bằng trần cứng, tức đi lùi.
2. HẠ số đã cao hơn sàn. Người vận hành có thể đã đo và chọn; ghi đè xuống là
   phá việc của họ mà không ai báo.
3. Chạy lại mỗi lần mở tool. Ai cố ý hạ trần sẽ bị ép ngược mỗi lần khởi động,
   và sẽ không hiểu vì sao cấu hình của mình "tự sửa lại".
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

GOC = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(GOC / "tools" / "ve3"))

ve3_gui = pytest.importorskip("ve3_gui", reason="cần tkinter/customtkinter")


@pytest.fixture()
def goi(tmp_path, monkeypatch):
    """Trả hàm `goi(cfg) -> cfg` chạy phép nâng trần trên một cấu hình giả.

    Đổi `VE3_DIR` sang thư mục tạm: phép nâng có chép lưu `settings.yaml`, và
    một bài kiểm KHÔNG được đẻ file vào cấu hình thật của người đang dùng máy.
    """
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "settings.yaml").write_text("cu: 1\n", encoding="utf-8")
    monkeypatch.setattr(ve3_gui, "VE3_DIR", tmp_path)
    # `ghi_log_file` ghi vào `logs/ve3-<ngày>.log` THẬT của máy đang chạy. Không
    # chặn thì mỗi lần chạy bộ kiểm lại nhét mấy chục dòng "[NÂNG TRẦN] ... bản
    # lưu: C:\\...\\pytest-of-...\\" vào sổ vận hành — và người đi tìm nguyên
    # nhân một sự cố thật sẽ đọc phải dấu vết của một bài kiểm. Đã xảy ra ngày
    # 14/08/2026, đúng trong lúc đang lần tìm chỗ nghẽn của tool.
    monkeypatch.setattr(ve3_gui, "ghi_log_file", lambda *a, **k: None)

    def _goi(cfg):
        class _Gia:
            config_data = cfg
            _duong_di_cho_bao: list = []

            def _save_config(self):
                pass

        gia = _Gia()
        ve3_gui.VE3App._nang_tran_song_song_may_cu(gia)
        return gia.config_data

    return _goi


def test_may_cu_thoi_chrome_duoc_nang(goi) -> None:
    """Đúng hình dạng đã đo được ở khách: giữ 2 chỗ."""
    ra = goi({"max_concurrent": 2, "shopapi_video_concurrency": 2,
              "shopapi_ma_song_song": 2})
    assert ra["max_concurrent"] == ve3_gui.SAN_ANH_MOI_MA
    assert ra["shopapi_video_concurrency"] == ve3_gui.SAN_VIDEO_MOI_MA
    assert ra["shopapi_ma_song_song"] == ve3_gui.SAN_MA_SONG_SONG
    assert ra["da_nang_tran_song_song"] == ve3_gui.NANG_TRAN_PHIEN


def test_so_khong_la_lua_chon_co_y_khong_duoc_dung_vao(goi) -> None:
    """`0` = đi theo trần động của máy chủ. Đây là cấu hình của máy ĐANG CHẠY NHANH."""
    ra = goi({"max_concurrent": 0, "shopapi_video_concurrency": 0,
              "shopapi_ma_song_song": 8})
    assert ra["max_concurrent"] == 0, "nâng `0` là thay trần động bằng trần cứng"
    assert ra["shopapi_video_concurrency"] == 0
    assert ra["shopapi_ma_song_song"] == 8


def test_khong_bao_gio_ha_so_da_cao_hon_san(goi) -> None:
    ra = goi({"max_concurrent": 200, "shopapi_video_concurrency": 64,
              "shopapi_ma_song_song": 32})
    assert ra["max_concurrent"] == 200
    assert ra["shopapi_video_concurrency"] == 64
    assert ra["shopapi_ma_song_song"] == 32


def test_khoa_vang_mat_thi_de_setdefault_lo(goi) -> None:
    """Không tự điền khoá thiếu — `_load_config` đã có `setdefault` cho việc đó.

    Điền ở đây là có hai chỗ cùng quyết định một con số, đúng loại bản sao đã
    làm hỏng cả ngày 14/08/2026 ở phía máy chủ (`video_poll_max` có hai mặc
    định, bản sửa nằm ở bản không có hiệu lực).
    """
    ra = goi({})
    assert "max_concurrent" not in ra
    assert "shopapi_video_concurrency" not in ra
    assert "shopapi_ma_song_song" not in ra


def test_chay_dung_mot_lan_cho_moi_phien_ban(goi) -> None:
    """Đã nâng rồi mà người dùng cố ý hạ xuống → KHÔNG ép lại."""
    ra = goi({"max_concurrent": 3,
              "da_nang_tran_song_song": ve3_gui.NANG_TRAN_PHIEN})
    assert ra["max_concurrent"] == 3, "lần mở sau không được ép lại lựa chọn của người dùng"


def test_phien_ban_cu_hon_thi_van_nang_lai(goi) -> None:
    """Máy đã qua bản nâng CŨ vẫn phải nhận bản nâng MỚI.

    Đây đúng chỗ mà cờ một-bit `True` đã trả giá ở `CHUYEN_API_PHIEN`: bản 528
    chuyển hụt rồi đóng cờ, bản 529 sửa đúng nhưng vừa vào đã gặp cờ và quay ra.
    """
    ra = goi({"max_concurrent": 2, "da_nang_tran_song_song": 0})
    assert ra["max_concurrent"] == ve3_gui.SAN_ANH_MOI_MA


def test_gia_tri_la_thi_de_yen_khong_doan_ho(goi) -> None:
    ra = goi({"max_concurrent": "nhieu"})
    assert ra["max_concurrent"] == "nhieu"


def test_san_khop_voi_cau_hinh_da_chay_that() -> None:
    """`8 mã × 40 ảnh = 320 chỗ` và `× 16 video = 128 chỗ`.

    Cố ý KHÔNG dùng 0 ("theo máy chủ") cho máy cũ: máy chủ mời ~979 chỗ ảnh, và
    `8 × 979` là con số đã giết nhà máy 9 lần ngày 12/08/2026.
    """
    assert ve3_gui.SAN_MA_SONG_SONG * ve3_gui.SAN_ANH_MOI_MA == 320
    assert ve3_gui.SAN_MA_SONG_SONG * ve3_gui.SAN_VIDEO_MOI_MA == 128
