"""Xoá dấu nhà cung cấp: đảo alpha, và TUYỆT ĐỐI không phá ảnh sạch.

Ảnh trong bộ kiểm đều dựng tay bằng numpy — không tải ảnh nào, không gọi mạng,
không tốn một đồng nào.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

SUITE_ROOT = Path(__file__).resolve().parents[1]
if str(SUITE_ROOT / "veo3top_engine") not in sys.path:
    sys.path.insert(0, str(SUITE_ROOT / "veo3top_engine"))

np = pytest.importorskip("numpy")
PIL = pytest.importorskip("PIL")
from PIL import Image  # noqa: E402

import xoa_dau_anh as xd  # noqa: E402


def _nen_muot(W, H):
    """Ảnh nền mượt — giống ảnh thật hơn nhiễu ngẫu nhiên nhiều."""
    yy, xx = np.mgrid[0:H, 0:W]
    return np.stack([120 + 60 * np.sin(xx / 90.0),
                     100 + 50 * np.cos(yy / 70.0),
                     140 + 40 * np.sin((xx + yy) / 110.0)], axis=-1)


def _dan_dau(A, alpha):
    """Dán ngôi sao lên đúng chỗ nhà cung cấp dán, bằng đúng phép trộn alpha."""
    assert xd._nap_chuan()
    hinh = xd._chuan["hinh"]
    S = hinh.shape[0]
    H, W = A.shape[:2]
    x0 = W - xd._chuan["le_phai"] - xd._chuan["canh"] - xd._chuan["bien"]
    y0 = H - xd._chuan["le_duoi"] - xd._chuan["canh"] - xd._chuan["bien"]
    a = np.clip(hinh * alpha, 0.0, 0.93)[:, :, None]
    A[y0:y0 + S, x0:x0 + S, :] = a * xd._chuan["mau"] + (1 - a) * A[y0:y0 + S, x0:x0 + S, :]
    return A


def _anh(W=1376, H=768, alpha=0.0):
    goc = _nen_muot(W, H)
    co_dau = _dan_dau(goc.copy(), alpha) if alpha else goc.copy()
    return Image.fromarray(co_dau.astype(np.uint8)), goc


# ── Đảo alpha phải trả lại đúng ảnh gốc ──────────────────────────────────────


@pytest.mark.parametrize("alpha", [0.32, 0.30, 0.26, 0.08])
def test_tra_lai_dung_diem_anh_goc(alpha):
    """Đây là lý do chọn đảo alpha thay vì vá bằng AI: KHÔI PHỤC, không bịa.

    Cách vá bằng mô hình sẽ bôi mịn vân nền; phép đảo trả lại đúng pixel.
    """
    im, goc = _anh(alpha=alpha)
    ra, tin = xd.xoa_dau(im, tra_chi_tiet=True)
    assert tin["da_xoa"], tin
    sai = np.abs(np.asarray(ra, dtype=float) - goc).max()
    assert sai <= 4, "sai lech toi da {0} — dao alpha phai gan nhu tuyet doi".format(sai)


def test_tu_do_lai_DO_MO_cho_tung_anh():
    """Độ mờ KHÔNG cố định — đây là chỗ dễ làm hỏng ảnh nhất.

    Đo trên 9 ảnh thật 15/08/2026: tám ảnh trong 0,26–0,34, riêng một ảnh 0,08.
    Dùng một mức cố định thì ảnh lệch bị trừ quá tay và chỗ có dấu biến thành
    ngôi sao ĐEN — còn xấu hơn để nguyên.
    """
    for alpha in (0.32, 0.08):
        im, _goc = _anh(alpha=alpha)
        _ra, tin = xd.xoa_dau(im, tra_chi_tiet=True)
        assert abs(tin["do_mo"] - alpha) <= 0.02, (alpha, tin)


# ── Không được phá ảnh sạch ──────────────────────────────────────────────────


def test_anh_SACH_thi_KHONG_dung_toi():
    """Số đo lấy từ MỘT nhà cung cấp ở MỘT cỡ ảnh.

    Cỡ khác, mẫu khác, hay ảnh đã nén lại thì vùng đó chỉ là một góc ảnh bình
    thường — đảo alpha lên nó là bôi một vệt sáng vào giữa nội dung thật. Thà
    bỏ sót một cái dấu còn hơn phá một tấm ảnh đã trả tiền.
    """
    im, _goc = _anh(alpha=0.0)
    ra, tin = xd.xoa_dau(im, tra_chi_tiet=True)
    assert not tin["da_xoa"], tin
    assert np.array_equal(np.asarray(ra), np.asarray(im)), "da sua mot tam anh sach"


def test_anh_nho_hon_vung_dau_thi_bo_qua():
    """Ảnh bé hơn vùng dấu thì toạ độ âm — cắt bừa là ném lỗi giữa mẻ."""
    im = Image.fromarray(_nen_muot(80, 60).astype(np.uint8))
    ra, tin = xd.xoa_dau(im, tra_chi_tiet=True)
    assert not tin["da_xoa"] and np.array_equal(np.asarray(ra), np.asarray(im))


# ── Xoá hai lần là hỏng — phải có dấu đã xử lý ───────────────────────────────


def test_KHONG_xoa_hai_lan(tmp_path):
    """Phép đảo alpha không tự biết mình đã chạy.

    Ảnh đi qua HAI chỗ: lúc tải về, và lúc soát trước khi dựng video. Không có
    dấu nối hai chỗ đó thì lần thứ hai trừ tiếp và để lại đúng cái ngôi sao ĐEN
    mà cả thiết kế này đi tránh.
    """
    p = tmp_path / "9.png"
    im, goc = _anh(alpha=0.32)
    im.save(p)

    assert xd.xoa_dau_file(str(p)) is True, "lan dau phai xoa"
    sau_lan_1 = np.asarray(Image.open(p), dtype=float)

    assert xd.xoa_dau_file(str(p)) is False, "lan hai phai bo qua"
    sau_lan_2 = np.asarray(Image.open(p), dtype=float)

    assert np.array_equal(sau_lan_1, sau_lan_2), "lan hai da tru them -> ngoi sao DEN"
    assert np.abs(sau_lan_1 - goc).max() <= 4


def test_anh_sach_cung_duoc_dong_dau_de_khoi_do_lai(tmp_path):
    """Đóng dấu cả khi không xoá gì: lượt sau khỏi dò lại từ đầu."""
    p = tmp_path / "9.png"
    im, _ = _anh(alpha=0.0)
    im.save(p)
    assert xd.xoa_dau_file(str(p)) is False
    assert xd.da_xoa_roi(str(p)) is True


def test_doc_khong_duoc_thi_coi_nhu_DA_XOA(tmp_path):
    """Chọn phía an toàn: đoán "chưa" mà thật ra "rồi" là hỏng ảnh."""
    p = tmp_path / "khong-phai-anh.png"
    p.write_bytes(b"day khong phai anh")
    assert xd.da_xoa_roi(str(p)) is True


def test_file_hong_KHONG_lam_chet_luot_chay(tmp_path):
    """Bước làm đẹp không được phép làm hỏng một lượt chạy đã trả tiền."""
    p = tmp_path / "hong.png"
    p.write_bytes(b"rac")
    assert xd.xoa_dau_file(str(p)) is False
