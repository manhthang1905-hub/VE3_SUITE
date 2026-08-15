"""Xoá dấu nhà cung cấp ở góc phải dưới ảnh, bằng phép ĐẢO ALPHA.

Dấu được dán lên theo phép trộn alpha chuẩn::

    anh_co_dau = alpha * mau_logo + (1 - alpha) * anh_goc

nên lấy lại ảnh gốc chỉ là đảo công thức::

    anh_goc = (anh_co_dau - alpha * mau_logo) / (1 - alpha)

**Không vá, không đoán, không dùng AI.** Trả lại đúng điểm ảnh gốc, kể cả vân
vải bên dưới — thứ mà cách vá bằng mô hình sẽ bôi mịn mất.

═══ VÌ SAO PHẢI XOÁ TRƯỚC KHI DỰNG VIDEO ═══

Đường dựng là Image-to-Video: ảnh scene là KHUNG ĐẦU của clip. Ảnh còn dấu thì
mọi khung của clip đều mang dấu, và lúc đó xoá khó gấp bội vì phải xử từng khung
hình. Nên chỗ đúng để cắm là ngay sau khi tải ảnh về.

═══ NGUỒN SỐ ĐO ═══

Hình dạng ngôi sao trong :data:`FILE_CHUAN` đo từ 9 ảnh thật ngày 15/08/2026
(kho `shopapi/tools/kho-github/mau-xoa-dau`). Tâm cách mép phải 97, cách mép dưới
98, cỡ 48–53 điểm ảnh, màu trắng.

═══ ĐỘ MỜ KHÔNG CỐ ĐỊNH — ĐÂY LÀ CHỖ DỄ LÀM HỎNG ẢNH ═══

Đo riêng từng ảnh trong 9 ảnh đó: tám ảnh nằm trong 0,26–0,34, riêng một ảnh chỉ
**0,08**. Dùng một mức cố định cho mọi ảnh thì ảnh lệch bị trừ quá tay, chỗ có
dấu biến thành một **ngôi sao ĐEN — còn xấu hơn để nguyên**.

Nên hàm này dò lại độ mờ cho từng ảnh, chọn mức nào làm viền ngôi sao phẳng nhất.

═══ VÀ MỘT CỬA NỮA ẢNH GỐC KHÔNG CÓ Ở ĐÂY ═══

Số đo trên lấy từ một nhà cung cấp, ở một cỡ ảnh. VE3 chạy nhiều tỉ lệ khung và
nhiều mẫu. Nếu vị trí/cỡ dấu khác đi, phép đảo sẽ **bôi bẩn một góc ảnh sạch**.

Nên hàm này chỉ nhận kết quả khi ĐO ĐƯỢC là nó cải thiện: viền ngôi sao phải
phẳng đi rõ rệt sau khi xoá. Không cải thiện thì trả lại ảnh nguyên vẹn. Thà bỏ
sót một cái dấu còn hơn phá một tấm ảnh đã trả tiền.
"""

from __future__ import annotations

import os

#: Viền phải phẳng đi ít nhất chừng này phần thì mới coi là có dấu thật.
#:
#: Đo trên ảnh dựng tay (xem `tests/test_xoa_dau_anh.py`): ảnh CÓ dấu thì tỉ lệ
#: viền/nền phẳng đi **97–99%**; ảnh SẠCH thì phép đảo làm nó xấu đi, tức số âm.
#: Hai bên cách nhau cả chục lần nên 0,5 nằm giữa một khoảng trống rất rộng —
#: không phải con số vặn cho vừa.
NGUONG_CAI_THIEN = 0.5

#: Khoảng độ mờ cho phép dò. Ngoài khoảng này thì hoặc không phải dấu, hoặc đảo
#: ra kết quả không tin được.
MUC_MO_MIN, MUC_MO_MAX, MUC_MO_BUOC = 0.08, 0.41, 0.02

FILE_CHUAN = os.path.join(os.path.dirname(os.path.abspath(__file__)), "dau_chuan.npz")

_chuan = {"da_nap": False}


def _nap_chuan():
    """Nạp bản đo một lần. Thiếu file/thiếu numpy thì tắt hẳn tính năng."""
    if _chuan["da_nap"]:
        return _chuan.get("ok", False)
    _chuan["da_nap"] = True
    try:
        import numpy as np
    except ImportError:
        _chuan["ok"] = False
        return False
    try:
        d = np.load(FILE_CHUAN)
        hinh = d["hinh"].astype(np.float64)
        gy, gx = np.gradient(hinh)
        vien = np.hypot(gx, gy) > 0.06
        _chuan.update({
            "np": np,
            "hinh": hinh,
            "vien": vien,
            # Nền = phần trong cửa sổ mà ngôi sao KHÔNG phủ tới. Chia cho nó thì
            # vân của chính tấm ảnh triệt tiêu, chỉ còn phần do cái dấu gây ra.
            "nen": (hinh < 0.02) & (~vien),
            "canh": int(d["canh"]),
            "le_phai": int(d["le_phai"]),
            "le_duoi": int(d["le_duoi"]),
            "bien": int(d["bien"]),
            "mau": float(d["mau"]),
            "ok": True,
        })
    except Exception:
        _chuan["ok"] = False
    return _chuan.get("ok", False)


def _diem_vien(vung, am=None):
    """Viền ngôi sao nổi lên GẤP MẤY LẦN nền ngay bên cạnh nó.

    `am = None` là đo ảnh chưa xoá.

    ⚠ PHẢI CHIA CHO NỀN, ĐỪNG ĐO VIỀN MỘT MÌNH. Bản đầu tôi viết chỉ đo độ gồ
    ghề dọc viền rồi so trước/sau. Nghe hợp lý, nhưng con số đó bị chính NỘI
    DUNG ảnh chi phối: ảnh nhiều vân thì gồ ghề ở đâu cũng cao, dấu chìm nghỉm,
    và phép so ra "cải thiện 3%" cho một tấm ảnh có dấu rành rành.

    Chia cho nền — vùng bên trong cửa sổ mà ngôi sao KHÔNG phủ tới — thì vân của
    ảnh triệt tiêu ở cả tử lẫn mẫu, chỉ còn lại phần do cái dấu gây ra. Đo trên
    ảnh dựng tay: có dấu thì tỉ lệ 22–80 lần, xoá đúng thì về 0,75; ảnh sạch thì
    tỉ lệ ~1 và phép đảo làm nó XẤU ĐI. Hai bên cách nhau cả chục lần.
    """
    np = _chuan["np"]
    if am is None:
        r = vung.mean(axis=2)
    else:
        a = np.clip(_chuan["hinh"] * am, 0.0, 0.93)[:, :, None]
        r = ((vung - a * _chuan["mau"]) / (1.0 - a)).mean(axis=2)
    gy, gx = np.gradient(r)
    g = np.hypot(gx, gy)
    nen = float(g[_chuan["nen"]].mean())
    return float(g[_chuan["vien"]].mean()) / max(1e-6, nen)


def xoa_dau(im, tra_chi_tiet=False):
    """Trả ảnh đã xoá dấu. Không chắc thì trả lại CHÍNH ảnh vào.

    `im` là `PIL.Image`. Không ghi đè gì; người gọi tự lưu.
    """
    khong = (im, {"da_xoa": False, "ly_do": "chua ro"}) if tra_chi_tiet else im
    if not _nap_chuan():
        return (im, {"da_xoa": False, "ly_do": "thieu numpy hoac ban do"}) \
            if tra_chi_tiet else im
    np = _chuan["np"]
    hinh = _chuan["hinh"]
    S = hinh.shape[0]

    A = np.asarray(im.convert("RGB"), dtype=np.float64)
    H, W = A.shape[:2]
    x0 = W - _chuan["le_phai"] - _chuan["canh"] - _chuan["bien"]
    y0 = H - _chuan["le_duoi"] - _chuan["canh"] - _chuan["bien"]
    if x0 < 0 or y0 < 0 or x0 + S > W or y0 + S > H:
        return (im, {"da_xoa": False, "ly_do": "anh nho hon vung dau"}) \
            if tra_chi_tiet else im

    vung = A[y0:y0 + S, x0:x0 + S, :]
    goc = _diem_vien(vung)
    if goc <= 0:
        return khong

    muc = np.arange(MUC_MO_MIN, MUC_MO_MAX, MUC_MO_BUOC)
    am = min(muc, key=lambda m: _diem_vien(vung, m))
    cai_thien = (goc - _diem_vien(vung, am)) / goc

    # ⚠ CỬA NÀY LÀ THỨ GIỮ CHO TA KHÔNG PHÁ ẢNH SẠCH.
    #
    # Số đo lấy từ một nhà cung cấp ở một cỡ ảnh. Ảnh khác cỡ, khác mẫu, hoặc
    # đã bị nén lại thì vùng đó chỉ là một góc ảnh bình thường — đảo alpha lên
    # nó là bôi một vệt sáng vào giữa nội dung thật.
    if cai_thien < NGUONG_CAI_THIEN:
        return (im, {"da_xoa": False, "ly_do": "khong thay dau",
                     "cai_thien": cai_thien}) if tra_chi_tiet else im

    from PIL import Image
    a = np.clip(hinh * am, 0.0, 0.93)[:, :, None]
    A[y0:y0 + S, x0:x0 + S, :] = np.clip(
        (vung - a * _chuan["mau"]) / (1.0 - a), 0, 255)
    ra = Image.fromarray(A.astype(np.uint8))
    if tra_chi_tiet:
        return ra, {"da_xoa": True, "do_mo": float(am), "cai_thien": cai_thien}
    return ra


#: Khoá ghi vào phần thông tin file PNG để biết ảnh đã xoá dấu rồi.
#:
#: ⚠ BẮT BUỘC PHẢI CÓ. Phép đảo alpha KHÔNG tự biết mình đã chạy: xoá hai lần
#: là trừ hai lần, và lần thứ hai để lại đúng cái ngôi sao ĐEN mà cả thiết kế
#: này đi tránh. Ảnh đi qua hai chỗ (lúc tải về, và lúc soát trước khi dựng
#: video) nên dấu này là thứ duy nhất nối hai chỗ đó lại.
KHOA_DA_XOA = "ve3_xoa_dau"


def da_xoa_roi(duong_dan):
    """Ảnh này đã qua xoá dấu chưa? Đọc không được thì coi như RỒI.

    Chọn phía an toàn: đoán "chưa" mà thật ra "rồi" thì xoá hai lần và hỏng ảnh;
    đoán "rồi" mà thật ra "chưa" thì cùng lắm còn cái dấu.
    """
    try:
        from PIL import Image
        with Image.open(duong_dan) as im:
            return KHOA_DA_XOA in (im.info or {})
    except Exception:
        return True


def xoa_dau_file(duong_dan, log=None):
    """Xoá dấu NGAY TRÊN file, và đóng dấu đã xử lý. Trả `True` khi có sửa.

    Nuốt mọi lỗi: đây là bước làm đẹp, không được phép làm hỏng một lượt chạy
    đã trả tiền.
    """
    try:
        if da_xoa_roi(duong_dan):
            return False
        from PIL import Image
        from PIL.PngImagePlugin import PngInfo
        with Image.open(duong_dan) as im:
            im.load()
            ra, tin = xoa_dau(im, tra_chi_tiet=True)
        thong_tin = PngInfo()
        thong_tin.add_text(KHOA_DA_XOA, "1" if tin.get("da_xoa") else "0")
        ra.save(duong_dan, format="PNG", pnginfo=thong_tin)
        if log and tin.get("da_xoa"):
            log("  xoa dau nha cung cap: do mo {0:.2f}, vien phang di {1:.0%}"
                .format(tin.get("do_mo", 0.0), tin.get("cai_thien", 0.0)))
        return bool(tin.get("da_xoa"))
    except Exception as e:  # noqa: BLE001 — buoc lam dep, khong duoc lam chet luot chay
        if log:
            log("  xoa dau: bo qua ({0}: {1})".format(type(e).__name__, e), "WARN")
        return False
