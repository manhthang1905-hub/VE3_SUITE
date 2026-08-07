"""Bộ kiểm HAI NHÀ MÁY (`image_factory`, `video_factory`) ở phần PHIÊN ĐĂNG NHẬP.

Ghim ba thứ mà sự cố 07/08/2026 dạy:

  1. `/health` không còn con số nào mang hai nghĩa (`logged_in` đã bị xoá hẳn —
     nó là BỘ ĐẾM TIẾN TRÌNH KIỂM, đo được bò từ 8 lên 41 trong 30 phút, mà cái
     tên khiến người đọc hiểu thành "số account đăng nhập được").
  2. `Account.auth()` có BƯỚC 3 "kéo phiên từ kho server về" — 07/08 đo được 96
     tài khoản `ready` trên server nhưng chỉ 65 bản ghi cache trên máy, và 31
     tài khoản còn lại bị gõ lại mật khẩu cho một phiên nằm cách đó một lời gọi.
  3. `rest(1800)` cố định đã bị bỏ khỏi đường kiểm phiên.

KHÔNG chạm mạng, KHÔNG mở Chrome, KHÔNG đăng nhập.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

import image_factory as imf
import kho_phien as kp
import phien_kiem as pk
import video_factory as vif

ENGINE = Path(__file__).resolve().parents[1] / "veo3top_engine"


class KhoGiaDia:
    """Thay `AuthCache` — không chạm thư mục `_auth_cache` thật."""

    def __init__(self, data=None):
        self.data = dict(data or {})
        self.da_ghi = []

    def _load(self, email):
        return self.data.get(email)

    def _save(self, email, d):
        self.data[email] = d
        self.da_ghi.append(email)

    def _fresh(self, d):
        return bool(d and d.get("bearer"))

    def _refresh_from_cookie(self, email, d):
        nd = dict(d)
        nd["bearer"] = "bearer-moi"
        return nd


@pytest.fixture(params=[imf, vif], ids=["anh", "video"])
def nha_may(request, monkeypatch):
    """Một nhà máy CHƯA `start()` — không luồng thợ, không Chrome, không mạng."""
    mod = request.param
    lop = imf.ImageFactory if mod is imf else vif.VideoFactory
    nm = lop(log=lambda *_: None)
    nm.cache = KhoGiaDia()
    yield mod, nm
    nm.han_muc_chua.stop()
    try:
        nm.recovery.stop()
    except Exception:
        pass


def _tk(mod, nm, email="a@gmail.com"):
    return mod.Account(email.split("@")[0], email, "", nm.cache, "")


# ── 1. /health nói thật ────────────────────────────────────────────────────

def test_health_khong_con_khoa_logged_in(nha_may):
    mod, nm = nha_may
    h = nm.health()
    assert "logged_in" not in h, "khoá này đã đánh lừa cả người vận hành lẫn chủ dự án"
    assert "not_logged_in" not in h


def test_health_tach_ro_bon_nhom(nha_may):
    mod, nm = nha_may
    nm.accounts = [_tk(mod, nm, f"tk{i}@gmail.com") for i in range(5)]
    nm.accounts[0].trang_thai_phien = pk.SONG
    nm.accounts[1].trang_thai_phien = pk.SONG
    nm.accounts[2].trang_thai_phien = pk.CHET_CHAC_CHAN
    h = nm.health()
    assert h["phien_song"] == 2
    assert h["phien_chet_chac_chan"] == 1
    assert h["phien_chua_kiem"] == 2
    assert h["phien_da_kiem"] == 3
    assert h["phien_dung_duoc"] == 4          # sống + chưa rõ = số account được giao việc
    assert isinstance(h["accounts"], list) and len(h["accounts"]) == 5


def test_health_phan_biet_chua_kiem_voi_da_kiem_va_chet(nha_may):
    """Hai tình huống hoàn toàn khác nhau KHÔNG được ra cùng một con số."""
    mod, nm = nha_may
    nm.accounts = [_tk(mod, nm, "chua@gmail.com")]
    chua = nm.health()
    nm.accounts[0].trang_thai_phien = pk.CHET_CHAC_CHAN
    chet = nm.health()
    assert (chua["phien_chua_kiem"], chua["phien_chet_chac_chan"]) == (1, 0)
    assert (chet["phien_chua_kiem"], chet["phien_chet_chac_chan"]) == (0, 1)
    assert chua["phien_dung_duoc"] != chet["phien_dung_duoc"]


def test_account_moi_dung_la_CHUA_RO_chu_khong_phai_chet(nha_may):
    """Lúc vừa dựng object thì chưa ai kiểm gì cả."""
    mod, nm = nha_may
    a = _tk(mod, nm)
    assert a.trang_thai_phien == pk.KHONG_KIEM_DUOC
    assert a.phien_ok is False        # "chưa chứng minh được là sống"


def test_phien_ok_van_ghi_duoc_kieu_cu(nha_may):
    mod, nm = nha_may
    a = _tk(mod, nm)
    a.phien_ok = True
    assert a.trang_thai_phien == pk.SONG
    a.phien_ok = False
    assert a.trang_thai_phien == pk.CHET_CHAC_CHAN


def test_canh_bao_lech_dem_ca_account_chua_ro(nha_may):
    """Account chưa rõ VẪN chạy việc -> đếm nó vào "không dùng được" là dựng lại
    đúng con số dối trá cũ (8/96)."""
    mod, nm = nha_may
    nm.accounts = [_tk(mod, nm, f"tk{i}@gmail.com") for i in range(50)]
    nm.n_kho_san_sang = 50
    nm.started_ts = 0.0               # qua thời gian ân hạn
    assert nm.canh_bao_lech() == ""


# ── 2. Account.auth() có BƯỚC 3 ────────────────────────────────────────────

def test_thieu_cache_thi_auth_di_hoi_kho_server(nha_may, monkeypatch):
    mod, nm = nha_may
    a = _tk(mod, nm)
    goi = []

    def keo_gia(email, cache, kho=None, log=lambda *_: None):
        goi.append(email)
        d = {"bearer": None, "ts": 0, "cookie": "CK", "project": "P1", "email": email}
        cache._save(email, d)
        return d, kp.CO_PHIEN

    monkeypatch.setattr(mod.kp, "keo_phien_ve", keo_gia)
    ket = a.auth()
    assert goi == [a.email], "thiếu cache mà KHÔNG hỏi kho = đi thẳng tới gõ mật khẩu"
    assert ket and ket.get("cookie") == "CK" and ket.get("bearer")
    assert nm.cache.da_ghi == [a.email], "kéo về phải GHI XUỐNG cache, lần sau khỏi hỏi"


def test_da_co_cookie_thi_KHONG_hoi_kho_server(nha_may, monkeypatch):
    """Bước 2 rẻ hơn bước 3 — đừng nhảy cóc theo chiều ngược lại."""
    mod, nm = nha_may
    nm.cache.data["a@gmail.com"] = {"cookie": "ck-cu", "project": "p-cu"}
    a = _tk(mod, nm)
    goi = []
    monkeypatch.setattr(mod.kp, "keo_phien_ve",
                        lambda *ar, **kw: (goi.append(1), (None, kp.KHONG_HOI_DUOC))[1])
    assert a.auth().get("cookie") == "ck-cu"
    assert goi == []


def test_kho_khong_co_gi_thi_auth_tra_None(nha_may, monkeypatch):
    mod, nm = nha_may
    a = _tk(mod, nm)
    monkeypatch.setattr(mod.kp, "keo_phien_ve", lambda *ar, **kw: (None, kp.KHONG_CO_PHIEN))
    assert a.auth() is None


# ── 3. Không còn `rest(1800)` trên đường kiểm phiên ────────────────────────

def _ma_thuc(ten):
    """Nội dung file, ĐÃ BỎ chú thích và chuỗi.

    Cần thiết vì các bản vá này cố ý TRÍCH NGUYÊN mã cũ vào chú thích để người
    sau hiểu vì sao không được quay lại — bài kiểm phải soi mã chạy được, chứ
    không phải soi phần đang giải thích lịch sử.
    """
    import io as _io
    import tokenize
    src = (ENGINE / ten).read_text(encoding="utf-8")
    ra = []
    for tok in tokenize.generate_tokens(_io.StringIO(src).readline):
        if tok.type in (tokenize.COMMENT, tokenize.STRING, tokenize.NL, tokenize.NEWLINE):
            continue
        ra.append(tok.string)
    return " ".join(ra)


@pytest.mark.parametrize("ten", ["image_factory.py", "video_factory.py"])
def test_khong_con_hinh_phat_1800_giay_co_dinh(ten):
    """Mức nghỉ phải NGẮN và TĂNG DẦN theo số lần hỏng liên tiếp
    (`phien_kiem.nghi_tang_dan`). 30 phút cố định sai theo cả hai chiều: quá nặng
    cho account chỉ chập một nhịp, mà chẳng đủ cho account hỏng thật."""
    ma = _ma_thuc(ten)
    assert not re.search(r"\.\s*rest\s*\(\s*1800\s*\)", ma), \
        f"{ten}: `rest(1800)` đã bị bỏ khỏi đường kiểm phiên, đừng thêm lại"


@pytest.mark.parametrize("ten", ["image_factory.py", "video_factory.py"])
def test_khong_con_except_Exception_ok_False(ten):
    """LỖI 1 của 07/08/2026: gom mạng chập vào cùng rổ với "phiên chết"."""
    ma = _ma_thuc(ten)
    assert not re.search(r"except\s+Exception\s*:\s*ok\s*=\s*False", ma), \
        f"{ten}: đừng quy mọi ngoại lệ về 'phiên chết'"


@pytest.mark.parametrize("ten", ["image_factory.py", "video_factory.py"])
def test_ca_hai_nha_may_dung_CHUNG_mot_bo_luat(ten):
    """Hai bản chép tay của cùng một luật = sửa một bên, quên bên kia."""
    src = (ENGINE / ten).read_text(encoding="utf-8")
    assert "pk.kiem_roster(" in src
    assert "import phien_kiem as pk" in src
