"""Kiểm phần CHẠY SONG SONG của `ve3_worker.py` cho nhánh API shopapi.

Ba nhóm việc, mỗi nhóm là một cách hỏng khác hẳn nhau:

1. **Dòng giao thức ra GUI không được cắt nát.** Đây là bài quan trọng nhất của
   cả tệp: tiến độ đi lên GUI bằng stdout có tiền tố (`@@LOG|`, `@@PROG|`,
   `@@ITEM|`, `@@RESULT|`) và GUI tách bằng `split("|")` theo TỪNG DÒNG. Từ khi
   các pha chạy hàng chục luồng, ba hàm ghi đó bị gọi đồng thời; không khoá thì
   một dòng bị chẻ đôi và GUI đọc ra rác — tệ nhất là thanh tiến độ đứng im
   trong khi job vẫn chạy ngon lành. Lỗi kiểu này chỉ hiện lúc đông việc nên rất
   khó lần ra, phải chặn bằng bài kiểm.
2. **Số luồng lấy từ máy chủ**, chặn trên bởi giới hạn người dùng đặt.
3. **`_chay_me` điều hướng đúng nhánh**, và đường cũ không bị đụng tới.
"""

from __future__ import annotations

import re
import threading
import time

import pytest

from conftest import KHOA_GIA, FakeClient, job_anh, job_video

import ve3_worker
from ve3_worker import VE3Worker


CAU_HINH_API = {
    "generation_backend": "shopapi",
    "veo3top_image_mode": "shopapi",
    "flow_aspect_ratio": "landscape",
    "retry_count": 1,
    "max_concurrent": 3,
}


@pytest.fixture
def co_khoa(monkeypatch, sc):
    monkeypatch.setattr(sc, "doc_khoa", lambda env=None: (KHOA_GIA, "kho khoa gia (bai kiem)"))
    return KHOA_GIA


def _worker(tmp_path, nhat_ky, cau_hinh=None):
    du_an = tmp_path / "PROJECT"
    du_an.mkdir(exist_ok=True)
    cfg = dict(CAU_HINH_API)
    if cau_hinh:
        cfg.update(cau_hinh)
    return VE3Worker(project_dir=str(du_an), config=cfg, log_func=nhat_ky)


# ═════════════════════════════════════════════════════════════════════════════
#  1. Dòng giao thức ra GUI: nhiều luồng ghi cùng lúc vẫn TRỌN VẸN
# ═════════════════════════════════════════════════════════════════════════════


class DongAcY:
    """`sys.stdout` cố ý ĐỘC ÁC: chẻ mỗi lần ghi làm hai và nhường luồng ở giữa.

    VÌ SAO PHẢI ĐỘC ÁC: với một `StringIO` bình thường, `write()` một chuỗi ngắn
    hầu như luôn lọt trọn vẹn nhờ GIL, nên bài kiểm sẽ XANH kể cả khi mã hoàn
    toàn không khoá — tức là một bài kiểm vô dụng, tệ hơn cả không có. Lớp này
    dựng lại đúng thứ mà `sys.stdout` thật làm khi bộ đệm đầy giữa chừng: một
    lần ghi thành hai mảnh, và luồng khác được chen vào giữa hai mảnh đó.

    Có khoá → hai mảnh luôn dính liền nhau. Không khoá → dòng bị cắt nát.
    """

    def __init__(self):
        self.manh = []          # list.append là nguyên tử, không cần khoá riêng

    def write(self, s):
        giua = max(1, len(s) // 2)
        self.manh.append(s[:giua])
        time.sleep(0)           # nhường luồng ĐÚNG giữa một dòng
        self.manh.append(s[giua:])
        return len(s)

    def flush(self):
        pass

    def chu(self):
        return "".join(self.manh)


def _ban_nhieu_luong(ham, so_luong):
    """Gọi `ham(i)` từ `so_luong` luồng, tất cả cùng xuất phát một lúc."""
    cong = threading.Barrier(so_luong)

    def _chay(i):
        cong.wait()             # ép mọi luồng đâm vào stdout ĐÚNG cùng thời điểm
        ham(i)

    luong = [threading.Thread(target=_chay, args=(i,)) for i in range(so_luong)]
    for t in luong:
        t.start()
    for t in luong:
        t.join()


def test_dong_PROG_tu_nhieu_luong_KHONG_bi_cat_nat(monkeypatch):
    ra = DongAcY()
    monkeypatch.setattr(ve3_worker.sys, "stdout", ra)

    _ban_nhieu_luong(
        lambda i: ve3_worker._structured_progress("scenes", i, 40, "scene_{0}".format(i)),
        40)

    dong = [d for d in ra.chu().split("\n") if d]
    assert len(dong) == 40, "mat hoac de them dong: {0}".format(len(dong))
    khuon = re.compile(r"^@@PROG\|scenes\|\d+\|40\|scene_\d+$")
    xau = [d for d in dong if not khuon.match(d)]
    assert xau == [], "co dong bi cat nat, GUI se parse ra rac: {0}".format(xau[:3])
    # Và không mất bản ghi nào: đủ 40 số khác nhau.
    assert len({int(d.split("|")[2]) for d in dong}) == 40


def test_dong_ITEM_tu_nhieu_luong_KHONG_bi_cat_nat(monkeypatch):
    ra = DongAcY()
    monkeypatch.setattr(ve3_worker.sys, "stdout", ra)

    _ban_nhieu_luong(
        lambda i: ve3_worker._structured_item("scene", i, "done", "img/S{0}.png".format(i),
                                              {"elapsed": i, "backend": "shopapi"}),
        40)

    dong = [d for d in ra.chu().split("\n") if d]
    assert len(dong) == 40
    khuon = re.compile(r"^@@ITEM\|scene\|\d+\|done\|img/S\d+\.png\|\{.*\}$")
    xau = [d for d in dong if not khuon.match(d)]
    assert xau == [], "dong @@ITEM bi cat nat: {0}".format(xau[:3])


def test_ba_loai_dong_ghi_XEN_KE_nhau_van_tron_ven(monkeypatch):
    """Thực tế là cả ba loại dòng cùng ra một lúc từ các pha khác nhau."""
    ra = DongAcY()
    monkeypatch.setattr(ve3_worker.sys, "stdout", ra)

    def _mot_luong(i):
        ve3_worker._structured_log("scene {0} dang chay".format(i))
        ve3_worker._structured_progress("scenes", i, 30, "scene_{0}".format(i))
        ve3_worker._structured_item("scene", i, "done", None, {"i": i})

    _ban_nhieu_luong(_mot_luong, 30)

    dong = [d for d in ra.chu().split("\n") if d]
    assert len(dong) == 90
    assert all(d.startswith(("@@LOG|", "@@PROG|", "@@ITEM|")) for d in dong), \
        "co dong khong bat dau bang tien to -> chac chan da bi cat nat"
    assert sum(1 for d in dong if d.startswith("@@PROG|")) == 30
    assert sum(1 for d in dong if d.startswith("@@ITEM|")) == 30


def test_noi_dung_co_xuong_dong_khong_de_ra_dong_gia(monkeypatch):
    """Một dòng giao thức phải là ĐÚNG một dòng, dù dữ liệu có ký tự xuống dòng."""
    ra = DongAcY()
    monkeypatch.setattr(ve3_worker.sys, "stdout", ra)

    ve3_worker._structured_log("loi\nnhieu dong\r\nnua")
    ve3_worker._structured_progress("scenes", 1, 2, "chi\ntiet")
    ve3_worker._structured_item("scene", 1, "done", None, {"loi": "hong\nnang"})

    dong = [d for d in ra.chu().split("\n") if d]
    assert len(dong) == 3, "du lieu co \\n ma de ra dong gia: {0}".format(dong)


# ═════════════════════════════════════════════════════════════════════════════
#  2. Số luồng: lấy từ máy chủ, chặn trên bởi giới hạn người dùng
# ═════════════════════════════════════════════════════════════════════════════


def test_so_luong_lay_tu_v1_me_chu_khong_go_cung(tmp_path, nhat_ky, co_khoa, monkeypatch, sc):
    monkeypatch.setattr(sc, "tran_song_song",
                        lambda loai, api_key=None, mac_dinh=1, client=None: 11)
    w = _worker(tmp_path, nhat_ky, {"max_concurrent": 0})
    assert w._shopapi_luong("image") == 11
    assert w._shopapi_luong("video") == 11


def test_so_luong_bi_chan_tren_boi_max_concurrent_cua_tool(tmp_path, nhat_ky, co_khoa,
                                                           monkeypatch, sc):
    """Máy chủ rộng 40 nhưng người dùng đặt 3 thì phải là 3 — máy họ, tiền họ."""
    monkeypatch.setattr(sc, "tran_song_song",
                        lambda loai, api_key=None, mac_dinh=1, client=None: 40)
    w = _worker(tmp_path, nhat_ky, {"max_concurrent": 3})
    assert w.max_concurrent == 3
    assert w._shopapi_luong("image", w.max_concurrent) == 3


def test_anh_va_video_hoi_TRAN_RIENG_cua_tung_loai(tmp_path, nhat_ky, co_khoa,
                                                   monkeypatch, sc):
    """Trần ảnh và trần video là hai con số khác nhau — dùng chung là sai cả hai."""
    monkeypatch.setattr(sc, "tran_song_song",
                        lambda loai, api_key=None, mac_dinh=1, client=None:
                        {"image": 20, "video": 5}[loai])
    w = _worker(tmp_path, nhat_ky, {"max_concurrent": 0})
    assert w._shopapi_luong("image") == 20
    assert w._shopapi_luong("video") == 5


def test_khong_ghim_thi_tran_nguoi_dung_la_tran_CUNG_khong_phai_1(tmp_path, nhat_ky, co_khoa):
    """Không ghim = "để máy chủ quyết", chứ không phải "chạy 1 job"."""
    w = _worker(tmp_path, nhat_ky, {"max_concurrent": 0})
    assert w.max_concurrent == 128


def test_khoi_dong_KHONG_hoi_may_chu_nua(tmp_path, nhat_ky, co_khoa, monkeypatch, sc):
    """Hỏi một lần lúc khởi động rồi giữ nguyên cả lượt chạy chính là tự bóp mình."""
    def _khong_duoc_goi(*a, **kw):
        raise AssertionError("dung /v1/me luc khoi dong -> con so se dong cung ca luot chay")

    monkeypatch.setattr(sc, "tran_song_song", _khong_duoc_goi)
    w = _worker(tmp_path, nhat_ky, {"max_concurrent": 0})
    assert w.max_concurrent >= 1


# ═════════════════════════════════════════════════════════════════════════════
#  3. `_chay_me`: điều hướng đúng nhánh, đường cũ không bị đụng
# ═════════════════════════════════════════════════════════════════════════════


def test_chay_me_chay_HET_viec_va_dem_dung(tmp_path, nhat_ky, co_khoa, monkeypatch, sc):
    monkeypatch.setattr(sc, "tran_song_song",
                        lambda loai, api_key=None, mac_dinh=1, client=None: 4)
    w = _worker(tmp_path, nhat_ky)
    ket = {"completed": 0, "failed": 0}

    w._chay_me("image", list(range(9)), lambda v: v % 3 != 0, w.max_concurrent, ket)

    assert ket == {"completed": 6, "failed": 3}


def test_chay_me_song_song_THAT_chu_khong_phai_tuan_tu(tmp_path, nhat_ky, co_khoa,
                                                       monkeypatch, sc):
    """Bài này bắt đúng cái bệnh đang chữa: lúc nào cũng chỉ 1 job trên máy chủ."""
    monkeypatch.setattr(sc, "tran_song_song",
                        lambda loai, api_key=None, mac_dinh=1, client=None: 8)
    w = _worker(tmp_path, nhat_ky, {"max_concurrent": 8})

    dang_chay = {"n": 0}
    dinh = {"n": 0}
    khoa = threading.Lock()

    def _chay(v):
        with khoa:
            dang_chay["n"] += 1
            dinh["n"] = max(dinh["n"], dang_chay["n"])
        time.sleep(0.02)
        with khoa:
            dang_chay["n"] -= 1
        return True

    w._chay_me("image", list(range(24)), _chay, w.max_concurrent, {"completed": 0, "failed": 0})

    assert dinh["n"] > 1, "van chay tuan tu - dung cai benh dang phai chua"


def test_chay_me_giu_NGUYEN_duong_cu_khi_chua_bat_nhanh_API(tmp_path, nhat_ky,
                                                            monkeypatch, sc):
    """Chưa có khoá → lùi về đường cũ, và đường cũ KHÔNG được hỏi `/v1/me` lần nào."""
    monkeypatch.setattr(sc, "doc_khoa", lambda env=None: ("", ""))

    def _khong_duoc_goi(*a, **kw):
        raise AssertionError("duong cu khong duoc dung toi API shopapi")

    monkeypatch.setattr(sc, "tran_song_song", _khong_duoc_goi)
    w = _worker(tmp_path, nhat_ky)
    assert w.use_shopapi_for_image is False

    ket = {"completed": 0, "failed": 0}
    w._chay_me("image", list(range(5)), lambda v: True, 2, ket)
    assert ket == {"completed": 5, "failed": 0}


def test_chay_me_hong_thi_LUI_VE_duong_cu_chu_khong_bo_ca_pha(tmp_path, nhat_ky, co_khoa,
                                                              monkeypatch, sc):
    w = _worker(tmp_path, nhat_ky)

    def _no(*a, **kw):
        raise RuntimeError("module bien mat")

    monkeypatch.setattr(w, "_chay_me_shopapi", _no)
    ket = {"completed": 0, "failed": 0}
    w._chay_me("image", list(range(4)), lambda v: True, 2, ket)

    assert ket == {"completed": 4, "failed": 0}, "mat ca pha chi vi mot module la qua dat"
    assert any(lv == "ERROR" and "lui ve chay tuan tu" in m for lv, m in nhat_ky.dong)


# ═════════════════════════════════════════════════════════════════════════════
#  4. 429/503 chỉ được NÉM khi đang ở trong một mẻ
# ═════════════════════════════════════════════════════════════════════════════


class _LoiNhanh(Exception):
    """Đứng thay `RateLimitError` của SDK — phân loại đi theo TÊN LỚP."""


_LoiNhanh.__name__ = "RateLimitError"


def test_goi_LE_thi_429_KHONG_duoc_nem_ra_ngoai(tmp_path, nhat_ky, co_khoa, monkeypatch, sc):
    """Hợp đồng `_submit_image` là trả đúng 4 phần tử — ném ra là làm sập nơi gọi."""
    monkeypatch.setattr(sc, "tao_client", lambda **kw: FakeClient(image_loi=_LoiNhanh("cham lai")))
    w = _worker(tmp_path, nhat_ky)

    ket_qua = w._submit_image("x", w.img_dir / "A.png")

    assert len(ket_qua) == 4
    assert ket_qua[0] is False and "429" in ket_qua[3]


def test_trong_me_thi_429_duoc_nem_de_TRA_VIEC_VE_HANG_CHO(tmp_path, nhat_ky, co_khoa,
                                                           monkeypatch, sc):
    """Trong mẻ, `429` phải thành `BiNghen` — nếu không, việc bị đếm nhầm là hỏng."""
    monkeypatch.setattr(sc, "tran_song_song",
                        lambda loai, api_key=None, mac_dinh=1, client=None: 4)
    w = _worker(tmp_path, nhat_ky)

    so_lan = {"n": 0}

    def _client(**kw):
        so_lan["n"] += 1
        if so_lan["n"] == 1:
            return FakeClient(image_loi=_LoiNhanh("cham lai"))
        return FakeClient(image_job=job_anh(n=1))

    monkeypatch.setattr(sc, "tao_client", _client)
    monkeypatch.setattr(sc, "tai_ve", lambda url, dest, timeout=600.0: str(dest))

    ket = {"completed": 0, "failed": 0}
    w._chay_me("image", [w.img_dir / "A.png"],
               lambda p: w._submit_image("x", p)[0], w.max_concurrent, ket)

    assert so_lan["n"] == 2, "viec bi 429 phai duoc CHAY LAI, khong phai bo"
    assert ket == {"completed": 1, "failed": 0}, "429 KHONG duoc dem la that bai"


def test_video_trong_me_cung_nem_429(tmp_path, nhat_ky, co_khoa, monkeypatch, sc):
    monkeypatch.setattr(sc, "tran_song_song",
                        lambda loai, api_key=None, mac_dinh=1, client=None: 4)
    w = _worker(tmp_path, nhat_ky)
    w.img_dir.mkdir(parents=True, exist_ok=True)
    (w.img_dir / "S1.png").write_bytes(b"\x89PNG")

    so_lan = {"n": 0}

    def _client(**kw):
        so_lan["n"] += 1
        if so_lan["n"] == 1:
            return FakeClient(video_loi=_LoiNhanh("cham lai"))
        return FakeClient(video_job=job_video())

    monkeypatch.setattr(sc, "tao_client", _client)
    monkeypatch.setattr(sc, "tai_ve", lambda url, dest, timeout=600.0: str(dest))

    ket = {"completed": 0, "failed": 0}
    w._chay_me("video", [w.vid_dir / "S1.mp4"],
               lambda p: w._submit_video("x", p, "")[0], 4, ket)

    assert so_lan["n"] == 2
    assert ket == {"completed": 1, "failed": 0}
