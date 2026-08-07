"""Chốt chặn: KHÔNG khoá API nào được nằm trong kho mã nguồn.

VÌ SAO CÓ FILE NÀY
------------------
Kho này từng chứa khoá DeepSeek SỐNG ở ba chỗ cùng lúc:
`server/config/settings.yaml`, `tools/srt-to-excel/config/settings.yaml`, và gõ
thẳng vào `ve3_gui.py`. Cả ba đều đã bị `git add` lên lịch sử.

Chỗ gõ trong `ve3_gui.py` là tệ nhất: `cfg` còn được ghi ra
`.excel_runtime_config.yaml` trong TỪNG thư mục project, nên một khoá gõ ở đó tự
nhân bản ra mọi project trên đĩa. Kiểm `old/` lúc dọn thấy đúng như vậy — hàng
chục bản sao của cùng một khoá.

Khoá `sk_live_` của shopapi (có ví tiền thật) cố ý KHÔNG đi đường đó: nó nằm ở
`%APPDATA%\\ShopAPI\\ve3-suite\\khoa.txt`, ngoài cây mã. Bài kiểm này giữ cho
đường đó không bị ai "tiện tay" phá.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

SUITE_ROOT = Path(__file__).resolve().parents[1]

#: Chuỗi trông như khoá API thật. Đủ dài để không đụng biến/ví dụ ngắn.
_KHOA = re.compile(r"\b(sk-[A-Za-z0-9]{20,}|sk_live_[A-Za-z0-9]{16,})")

#: Chỉ soi những đuôi file người ta hay lỡ tay dán khoá vào.
_DUOI = {".py", ".yaml", ".yml", ".json", ".bat", ".ps1", ".sh", ".md", ".txt"}

#: NỢ ĐÃ BIẾT — khoá VOV còn sót, chủ dự án chưa yêu cầu dọn.
#:
#: Ghi bằng TIỀN TỐ NGẮN, tuyệt đối không dán khoá đầy đủ vào đây: làm thế là tự
#: tay đưa khoá trở lại đúng kho mã mà bài kiểm này đang canh.
#:
#: Dọn xong thì XOÁ dòng dưới đây đi — danh sách này chỉ nên ngắn lại.
_NO_DA_BIET = ("sk-6m5lfO",)

#: Dấu hiệu khoá GIẢ dựng cho bài kiểm — bắt chúng là báo động giả.
#:
#: Cố ý đòi chữ RÕ NGHĨA chứ không phải mẩu ngắn: `"GIA"` hay `"TEST"` trần rất
#: dễ trùng vào giữa một khoá thật (khoá là chuỗi base62 ngẫu nhiên), mà một khoá
#: thật lọt lưới vì trùng ba chữ cái đúng là cái hỏng ta không được phép có.
_DAU_HIEU_GIA = ("KHOAGIA", "FAKE", "DUMMY", "EXAMPLE", "PLACEHOLDER", "KHONGDUNGDUOC")


def _file_trong_kho():
    """Mọi file ĐANG ĐƯỢC GIT THEO DÕI — thứ thật sự phát tán ra ngoài.

    Cố ý bỏ qua file chưa track (`old/`, thư mục chạy thử): chúng chỉ nằm trên
    máy này, không đi theo `git push`, và bài kiểm không phải việc dọn đĩa.
    """
    ra = subprocess.run(["git", "ls-files", "-z"], cwd=SUITE_ROOT,
                        capture_output=True, text=True, timeout=60)
    if ra.returncode != 0:
        pytest.skip("khong chay duoc git ls-files")
    for ten in ra.stdout.split("\0"):
        if not ten:
            continue
        p = SUITE_ROOT / ten
        if p.suffix.lower() in _DUOI and p.is_file():
            yield ten, p


def _khoa_lo(text):
    """Mọi khoá trong `text`, trừ nợ cũ đã ghi nhận và khoá giả của bài kiểm."""
    ra = []
    for m in _KHOA.finditer(text):
        k = m.group(1)
        if k.startswith(_NO_DA_BIET):
            continue
        if any(dh in k.upper() for dh in _DAU_HIEU_GIA):
            continue
        ra.append(k)
    return ra


def test_khong_con_khoa_deepseek_nao_trong_kho_ma():
    """Ba khoá đã dọn ngày 07/08/2026 — canh riêng để không ai vô tình đưa lại."""
    da_don = ("sk-e3a4138d", "sk-a138987932", "sk-de9ca9b3", "sk-HzpmHV")
    pham = []
    for ten, p in _file_trong_kho():
        noi_dung = p.read_text(encoding="utf-8", errors="replace")
        for tien_to in da_don:
            if tien_to in noi_dung:
                pham.append(f"{ten}: {tien_to}…")
    assert not pham, "Khoa DeepSeek da don lai xuat hien:\n  " + "\n  ".join(pham)


def test_khong_khoa_moi_nao_bi_go_vao_kho_ma():
    pham = []
    for ten, p in _file_trong_kho():
        # Chính file này chứa mẫu regex + tiền tố, đương nhiên phải bỏ qua.
        if Path(ten).name == Path(__file__).name:
            continue
        for k in _khoa_lo(p.read_text(encoding="utf-8", errors="replace")):
            pham.append(f"{ten}: {k[:10]}…")
    assert not pham, (
        "Co khoa API nam trong kho ma nguon:\n  " + "\n  ".join(pham)
        + "\n\nKhoa phai nam NGOAI cay ma. Voi shopapi: dung nut 'Luu khoa' o "
          "trang Cai dat (ghi vao %APPDATA%\\ShopAPI\\ve3-suite\\khoa.txt), "
          "hoac dat bien moi truong SHOPAPI_KEY."
    )


def test_khoa_shopapi_khong_bao_gio_doc_tu_settings_yaml():
    """`settings.yaml` bị chép sang `.ve3_run_config.json` + `.excel_runtime_config.yaml`
    trong từng project — khoá đặt ở đó là tự nhân bản ra khắp đĩa."""
    import sys
    sys.path.insert(0, str(SUITE_ROOT / "veo3top_engine"))
    import shopapi_common as sc

    khoa, nguon = sc.doc_khoa({"SHOPAPI_KEY": "", "APPDATA": str(SUITE_ROOT / "khong-co")})
    assert khoa == "", f"khong duoc tim thay khoa nao, nhung ra: {nguon}"
