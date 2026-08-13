"""Máy này đang ở bản nào, và vì sao nút Update im lặng?

Chạy TRÊN MÁY ĐANG LỖI:

    cd D:\\VE3_SUITE
    python kiem_ban_cap_nhat.py

═══════════════════════════════════════════════════════════════════════════════
 VÌ SAO CẦN

Số phiên bản của tool đến từ HAI hệ đếm khác nhau — số commit của git, và file
`VERSION`. Khi hai hệ đó lệch nhau, `check_update` hỏng theo hướng tệ nhất: nó
IM LẶNG kết luận "đã mới nhất" và không bao giờ cập nhật nữa.

Đo ngày 14/08/2026: `VERSION` ghi `1.0.526` trong khi nhánh `main` có 525
commit. Máy không có git đọc `526`, hỏi API ra `525`, thấy `525 > 526` là sai
nên nằm im vĩnh viễn — cả đợt việc chuyển sang API shopapi đã nằm trên GitHub
mà máy đó không nhận được gì.

Nhìn số phiên bản không phân biệt được "đã mới nhất" với "kẹt ở bản cũ". Công
cụ này soi CẢ HAI hệ đếm cùng lúc, cộng thêm bằng chứng THẬT là code shopapi có
mặt trong file hay không.
"""
from __future__ import annotations

import json
import pathlib
import subprocess
import sys
from pathlib import Path
from urllib.request import Request, urlopen

GOC = Path(__file__).resolve().parent
REPO = "manhthang1905-hub/VE3_SUITE"

#: Dấu vết của bản dùng API shopapi. Có mặt = code mới đã về máy.
#: Đây là bằng chứng THẬT, không phụ thuộc con số nào.
DAU_VET = {
    "tools/ve3/ve3_gui.py": [
        ("che_do_toan_api", "cổng nhận biết chế độ API"),
        ("NHAN_TRAM_API", "bảng chỉ số API trên trang Overview"),
        ("ghi_log_file", "ghi nhật ký ra logs/"),
        ("_ghi_so_luot_trang", "đỗ mã chạy trắng"),
    ],
    "veo3top_engine/shopapi_common.py": [
        ("NhipHoiTham", "giãn nhịp hỏi trạng thái theo số job đang bay"),
        ("doc_v1_me", "đọc trần máy chủ"),
    ],
    "veo3top_engine/shopapi_batch.py": [
        ("_thu_hoach", "bỏ hàng rào mỗi lô"),
        ("TRAN_TTL", "hạn hỏi lại /v1/me"),
    ],
    "veo3top_engine/shopapi_video_client.py": [
        ("_don_upload", "dọn ảnh khung đầu khỏi kho"),
    ],
}


def _chay(*lenh):
    try:
        r = subprocess.run(lenh, cwd=str(GOC), capture_output=True, text=True, timeout=10)
        return r.stdout.strip() if r.returncode == 0 else ""
    except Exception:
        return ""


def _api(duong, accept="application/json"):
    try:
        req = Request("https://api.github.com/repos/{0}{1}".format(REPO, duong),
                      headers={"User-Agent": "VE3-kiem-ban", "Accept": accept})
        with urlopen(req, timeout=15) as r:
            return r.read().decode("utf-8").strip()
    except Exception as e:
        return "LOI: {0}".format(e)


def main():
    print("=" * 68)
    print(" MAY NAY DANG O BAN NAO")
    print("=" * 68)

    sha_may = _chay("git", "rev-parse", "HEAD")
    dem_may = _chay("git", "rev-list", "--count", "HEAD")
    ban_file = ""
    try:
        ban_file = (GOC / "VERSION").read_text(encoding="utf-8").strip()
    except Exception:
        ban_file = "(khong doc duoc)"

    print("  git tren may       : {0}".format("CO" if sha_may else "KHONG (ban cai tu zip)"))
    print("  commit dang dung   : {0}".format(sha_may[:12] if sha_may else "-"))
    print("  so commit          : {0}".format(dem_may or "-"))
    print("  file VERSION       : {0}".format(ban_file))

    sha_git = _api("/commits/main", "application/vnd.github.sha")
    print("  commit tren GitHub : {0}".format(sha_git[:12] if not sha_git.startswith("LOI") else sha_git))

    print()
    print("=" * 68)
    print(" SDK shopapi — THU BAT BUOC PHAI CO")
    print("=" * 68)
    # SDK CHUA len PyPI: `pip install shopapi` KHONG co tac dung. No chi den
    # duoc may khac neu nam san trong repo o `_sdk/`. Thieu no thi tool bao
    # "thieu SDK" va khong gui noi mot job nao.
    kem = GOC / "_sdk" / "shopapi" / "__init__.py"
    print("  _sdk/shopapi ket repo : {0}".format("CO" if kem.is_file() else "THIEU"))
    try:
        sys.path.insert(0, str(GOC / "veo3top_engine"))
        import shopapi_common as _sc
        ok_sdk = _sc.bootstrap_sdk()
        print("  bootstrap_sdk()       : {0}".format(ok_sdk))
        if ok_sdk:
            import shopapi as _s
            print("  nap SDK tu            : {0}".format(pathlib.Path(_s.__file__).parent))
    except Exception as e:
        ok_sdk = False
        print("  bootstrap_sdk()       : LOI {0}: {1}".format(type(e).__name__, e))
    if not ok_sdk:
        thieu_sdk = True
    else:
        thieu_sdk = False

    print()
    print("=" * 68)
    print(" CODE SHOPAPI DA VE MAY CHUA (bang chung THAT, khong nhin so)")
    print("=" * 68)
    thieu = []
    for duong, moc in DAU_VET.items():
        p = GOC / duong
        if not p.exists():
            print("  [THIEU FILE] {0}".format(duong))
            thieu.append(duong)
            continue
        noi_dung = p.read_text(encoding="utf-8", errors="replace")
        for ten, y_nghia in moc:
            co = ten in noi_dung
            print("  [{0}] {1:26} {2}".format("OK   " if co else "THIEU", ten, y_nghia))
            if not co:
                thieu.append("{0}:{1}".format(duong, ten))

    print()
    print("=" * 68)
    if thieu_sdk:
        print(" KET LUAN: THIEU SDK shopapi. Tool khong gui duoc job nao.")
        print()
        print(" SDK CHUA len PyPI -> `pip install shopapi` VO DUNG. No phai nam trong repo.")
        print(" Cap nhat lai de lay thu muc `_sdk/`:")
        if sha_may:
            print("     git fetch origin main && git reset --hard origin/main")
        else:
            print("     tai https://github.com/{0}/archive/refs/heads/main.zip".format(REPO))
            print("     giai nen de len, GIU LAI: PROJECTS/, tools/ve3/config/")
    elif not thieu and sha_may and sha_git and not sha_git.startswith("LOI"):
        if sha_may == sha_git:
            print(" KET LUAN: DA CO BAN MOI NHAT. Neu giao dien chua doi -> DONG VA MO LAI tool.")
        else:
            print(" KET LUAN: code shopapi DA CO, nhung chua dung commit moi nhat.")
            print("           Chay:  git pull --ff-only origin main")
    elif thieu:
        print(" KET LUAN: MAY NAY CHUA CO CODE SHOPAPI. Nut Update dang khong an.")
        print()
        print(" CACH CHUA (chon MOT):")
        if sha_may:
            print("   A. Co git — chay hai lenh nay:")
            print("        git fetch origin main")
            print("        git reset --hard origin/main")
            print("      (config/PROJECTS deu duoc gitignore nen khong mat gi)")
        else:
            print("   A. Khong co git — tai lai ban moi:")
            print("        https://github.com/{0}/archive/refs/heads/main.zip".format(REPO))
            print("      Giai nen de len thu muc cu, GIU LAI: PROJECTS/, tools/ve3/config/")
        print("   B. Hoac ha so trong file VERSION xuong `1.0.0` roi bam Update.")
        print("      Nut Update chi so SO, nen ha so xuong la no chiu chay.")
    else:
        print(" KET LUAN: khong ket noi duoc GitHub de doi chieu. Kiem mang roi chay lai.")
    print("=" * 68)
    return 0 if not thieu else 1


if __name__ == "__main__":
    sys.exit(main())
