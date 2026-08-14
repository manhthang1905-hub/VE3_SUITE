"""Kiểm phần dùng chung: tỉ lệ khung, đọc `outputs`, kho khoá, dịch lỗi."""

from __future__ import annotations

import os

import pytest

from conftest import KHOA_GIA, job_anh


# ── Tỉ lệ khung ──────────────────────────────────────────────────────────────


@pytest.mark.parametrize("dau_vao,mong_doi", [
    ("IMAGE_ASPECT_RATIO_PORTRAIT", "9:16"),
    ("IMAGE_ASPECT_RATIO_LANDSCAPE", "16:9"),
    ("IMAGE_ASPECT_RATIO_SQUARE", "1:1"),
    ("VIDEO_ASPECT_RATIO_PORTRAIT", "9:16"),
    ("VIDEO_ASPECT_RATIO_LANDSCAPE", "16:9"),
    ("portrait", "9:16"),
    ("landscape", "16:9"),
    ("square", "1:1"),
    ("9:16", "9:16"),
    ("4:3", "4:3"),
])
def test_ty_le_api_quy_ve_chuoi_may_chu_hieu(sc, dau_vao, mong_doi):
    assert sc.ty_le_api(dau_vao) == mong_doi


def test_ty_le_api_khong_nhan_ra_thi_lay_mac_dinh_chu_khong_nem_loi(sc):
    # Sai tỉ lệ chỉ xấu khung hình; ném lỗi là hỏng cả job đã trả tiền.
    assert sc.ty_le_api("mot-thu-la-hoac") == "16:9"
    assert sc.ty_le_api(None) == "16:9"


def test_ty_le_api_doc_duoc_enum_cua_google_flow_api(sc):
    from modules.google_flow_api import AspectRatio, VideoAspectRatio
    assert sc.ty_le_api(AspectRatio.PORTRAIT) == "9:16"
    assert sc.ty_le_api(VideoAspectRatio.LANDSCAPE) == "16:9"


# ── outputs vs output ────────────────────────────────────────────────────────


def test_lay_outputs_n_bang_1_doc_tu_output(sc):
    job = job_anh(n=1)
    assert "outputs" not in job          # máy chủ KHÔNG gửi outputs khi n=1
    assert len(sc.lay_outputs(job)) == 1


def test_lay_outputs_n_lon_hon_1_phai_doc_tu_outputs_khong_phai_output(sc):
    """BẪY: `output` chỉ là ảnh ĐẦU TIÊN. Đọc nhầm là mất n-1 ảnh đã trả tiền."""
    job = job_anh(n=4)
    ket_qua = sc.lay_outputs(job)
    assert len(ket_qua) == 4
    urls = [sc.url_cua_output(o) for o in ket_qua]
    assert urls == ["https://cdn.example.invalid/anh{0}.png".format(i) for i in (1, 2, 3, 4)]


def test_lay_outputs_job_rong_tra_danh_sach_rong(sc):
    assert sc.lay_outputs(None) == []
    assert sc.lay_outputs({"id": "x", "status": "failed"}) == []


# ── Kho khoá ─────────────────────────────────────────────────────────────────


def test_doc_khoa_uu_tien_bien_moi_truong(sc):
    key, nguon = sc.doc_khoa(env={"SHOPAPI_KEY": KHOA_GIA})
    assert key == KHOA_GIA
    assert "SHOPAPI_KEY" in nguon
    assert KHOA_GIA not in nguon          # nguồn KHÔNG được lộ chính khoá


def test_doc_khoa_doc_file_tro_boi_bien_moi_truong(sc, tmp_path):
    f = tmp_path / "khoa.txt"
    f.write_text(KHOA_GIA + "\n", encoding="utf-8")
    key, nguon = sc.doc_khoa(env={"SHOPAPI_KEY_FILE": str(f)})
    assert key == KHOA_GIA
    assert KHOA_GIA not in nguon


def test_luu_khoa_roi_doc_lai_duoc_va_nam_ngoai_kho_ma(sc, tmp_path):
    env = {"APPDATA": str(tmp_path), "XDG_CONFIG_HOME": str(tmp_path)}
    duong_dan = sc.luu_khoa(KHOA_GIA, env=env)
    assert os.path.exists(duong_dan)
    assert str(tmp_path) in duong_dan     # nằm trong thư mục người dùng, không phải kho mã
    key, nguon = sc.doc_khoa(env=env)
    assert key == KHOA_GIA
    assert nguon == "kho khoa rieng cua may nay"
    sc.quen_khoa(env=env)
    assert sc.doc_khoa(env=env) == ("", "")


def test_khong_co_khoa_o_dau_ca_thi_tra_rong(sc, tmp_path):
    assert sc.doc_khoa(env={"APPDATA": str(tmp_path), "XDG_CONFIG_HOME": str(tmp_path)}) == ("", "")


def test_che_khoa_va_redact_khong_de_lo_khoa(sc):
    che = sc.che_khoa(KHOA_GIA)
    assert KHOA_GIA not in che
    # Giữ TIỀN TỐ để người đọc log biết đang nói tới khoá nào, nhưng đừng ghim
    # cứng `sk_live_`: khoá giả cố ý mang tiền tố khác để GitHub Push Protection
    # thôi nhận nhầm là khoá Stripe thật (xem `conftest.KHOA_GIA`).
    assert che.startswith(KHOA_GIA.split("_")[0] + "_"), "che mat ca tien to thi log kho doc"
    dong_log = "loi khi goi voi key {0} tren /v1/images".format(KHOA_GIA)
    assert KHOA_GIA not in sc.redact(dong_log)
    # Khoá THẬT dạng `sk_live_…` cũng phải bị che — đó mới là thứ đáng lo.
    that = "sk_live_" + "A" * 32
    assert that not in sc.redact("bearer " + that)


# ── Dịch lỗi sang tiếng Việt ─────────────────────────────────────────────────


def _loi_gia(ten_lop, thong_diep, **thuoc_tinh):
    """Dựng ngoại lệ MANG ĐÚNG TÊN LỚP của SDK mà không cần cài SDK."""
    lop = type(ten_lop, (Exception,), {})
    exc = lop(thong_diep)
    exc.message = thong_diep
    for k, v in thuoc_tinh.items():
        setattr(exc, k, v)
    return exc


def test_het_tien_noi_ro_chua_bi_tru_dong_nao(sc):
    msg = sc.mo_ta_loi(_loi_gia("InsufficientBalanceError", "So du khong du. Can 500d."))
    assert "HET TIEN" in msg
    assert "CHUA bi tru" in msg


def test_sai_khoa_chi_duong_tao_khoa_moi(sc):
    msg = sc.mo_ta_loi(_loi_gia("AuthenticationError", "Key nay sai hoac da bi thu hoi."))
    assert "KHOA API HONG" in msg
    assert "api-keys" in msg


def test_429_noi_ro_so_giay_can_cho(sc):
    msg = sc.mo_ta_loi(_loi_gia("RateLimitError", "Ban gui hoi nhanh.", retry_after=12.0))
    assert "429" in msg
    assert "12 giay" in msg


def test_503_noi_ro_job_chua_tao_va_khong_bi_tru_tien(sc):
    msg = sc.mo_ta_loi(_loi_gia("EngineUnavailableError", "Cum xu ly tam ban."))
    assert "503" in msg
    assert "KHONG bi tru tien" in msg


def test_422_nhac_dung_bay_8_giay_va_10_giay(sc):
    msg = sc.mo_ta_loi(_loi_gia("UnsupportedParameterError", "duration khong hop le"))
    assert "422" in msg
    assert "8 giay" in msg and "10 giay" in msg


def test_loi_la_hoac_van_ra_mot_dong_doc_duoc_khong_nuot(sc):
    msg = sc.mo_ta_loi(ValueError("cai gi do la lam"))
    assert "ValueError" in msg
    assert "cai gi do la lam" in msg


def test_thong_diep_loi_bi_che_khoa_truoc_khi_ghi_log(sc):
    """Máy chủ đôi khi nhắc lại tham số gửi lên -> không thể tin là nó sạch sẵn."""
    msg = sc.mo_ta_loi(_loi_gia("AuthenticationError", "key {0} bi thu hoi".format(KHOA_GIA)))
    assert KHOA_GIA not in msg


# ── Tải file ─────────────────────────────────────────────────────────────────


def test_tai_ve_ghi_ra_dung_duong_dan_va_khong_de_lai_file_part(sc, tmp_path, monkeypatch):
    """Không dùng mạng: giả `httpx` bằng một client cục bộ."""
    class _Resp:
        status_code = 200
        headers = {"Content-Length": "5"}

        def iter_bytes(self, n):
            yield b"12345"

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    class _Http:
        def __init__(self, **kw):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def stream(self, method, url):
            return _Resp()

    import httpx
    monkeypatch.setattr(httpx, "Client", _Http)
    dich = tmp_path / "sau" / "nua" / "a.png"
    sc.tai_ve("https://x.invalid/a.png", str(dich))
    assert dich.exists() and dich.read_bytes() == b"12345"
    assert not (tmp_path / "sau" / "nua" / "a.png.part").exists()


# ── Bộ lọc nội dung chặn prompt -> phải bật đường VIẾT LẠI ───────────────────
#
# Hai bài dưới đây giữ MỘT hợp đồng bắc qua hai file: `mo_ta_loi` (bên
# `veo3top_engine`) phải nhả ra mã máy đọc, và `_is_policy_violation_error` (bên
# `tools/ve3`) phải bắt được nó. Đứt một đầu là cả bộ máy viết lại prompt 3 vòng
# nằm im, cảnh bị chặn đếm thành "hỏng" và người dùng mất cảnh không rõ lý do.
#
# ĐÃ DÍNH THẬT (job_rqv2tbpk3fytiykdwzgp2zna): máy chủ trả
# `code="content_rejected"` kèm `message` là VĂN XUÔI TIẾNG VIỆT, nên không một
# marker tiếng Anh nào ("policy", "unsafe", "violat"…) khớp được.


def test_job_bi_chan_giu_lai_ma_may_doc_chu_khong_chi_van_xuoi(sc):
    """`mo_ta_loi` phải kèm `job.error.code`, vì `message` là tiếng Việt đổi lúc nào không hay."""
    van_xuoi_that = (
        "Mô tả (prompt) của bạn bị chặn, nhưng nhà cung cấp mô hình cho biết VIẾT "
        "LẠI MÔ TẢ là dùng được — nội dung không bị cấm hẳn."
    )
    msg = sc.mo_ta_loi(_loi_gia("JobFailedError", van_xuoi_that, code="content_rejected"))
    assert "content_rejected" in msg
    assert van_xuoi_that in msg, "van xuoi cua may chu van phai con de nguoi doc hieu"


def test_job_hong_khong_co_ma_van_ra_mot_dong_doc_duoc(sc):
    """Thiếu `code` thì vẫn phải ra câu tử tế, tuyệt đối không nổ `AttributeError`."""
    msg = sc.mo_ta_loi(_loi_gia("JobFailedError", "cum xu ly chet giua chung"))
    assert "JOB HONG" in msg
    assert "cum xu ly chet giua chung" in msg


@pytest.mark.parametrize("ten_lop, thuoc_tinh", [
    ("JobFailedError", {"code": "content_rejected"}),
    ("ContentRejectedError", {}),
])
def test_worker_nhan_ra_day_la_loi_policy_de_bat_duong_viet_lai(sc, ten_lop, thuoc_tinh):
    """Hợp đồng bắc cầu: chuỗi `mo_ta_loi` nhả ra PHẢI lọt cửa `_is_policy_violation_error`."""
    from ve3_worker import VE3Worker

    msg = sc.mo_ta_loi(_loi_gia(ten_lop, "Mô tả của bạn bị chặn.", **thuoc_tinh))
    # Gọi như hàm thuần: nó chỉ soi chuỗi, không đụng gì tới trạng thái worker.
    assert VE3Worker._is_policy_violation_error(None, msg) is True


# ── Tải kết quả: PHẢI thử lại ────────────────────────────────────────────────
#
# ĐÃ MẤT ẢNH ĐÃ TRẢ TIỀN VÌ THIẾU (07/08/2026). Nguyên văn:
#
#   shopapi-img: tai ket qua ve dia that bai:
#   RemoteProtocolError: peer closed connection without sending complete message
#
# Job da `succeeded`, anh da sinh ra, tien da tru — roi kho luu tru dut ket noi
# giua luc tai. Ban cu thu DUNG MOT LAN nen mat sach ca 64 giay cho lan so tien
# do vi mot cu hiccup mang vai mili giay. Day la kieu hong te nhat trong mot me
# lon: no xay ra SAU KHI moi viec kho da xong.


class _LuongGia:
    """Giả một lượt tải: có thể đứt giữa chừng, hoặc trả mã lỗi."""

    def __init__(self, dem, dut_toi_lan, ma=200):
        self.dem = dem
        self.dut_toi_lan = dut_toi_lan
        self.status_code = ma

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def iter_bytes(self, n):
        yield b"PHAN-DAU"
        if self.dem["n"] <= self.dut_toi_lan:
            raise RuntimeError("peer closed connection without sending complete message")
        yield b"-PHAN-CUOI"


def _httpx_gia(dem, dut_toi_lan=0, ma=200):
    class _Client:
        def __init__(self, **k):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def stream(self, method, url):
            dem["n"] += 1
            return _LuongGia(dem, dut_toi_lan, ma)

    import types
    return types.SimpleNamespace(Client=_Client)


def _tai(sc, url, dest, httpx_gia, so_lan=4):
    """Chạy đúng vòng thử lại của `tai_ve` nhưng tiêm httpx giả (không ra mạng)."""
    loi = None
    for lan in range(so_lan):
        try:
            return sc._tai_ve_mot_lan(url, str(dest) + ".part", str(dest), 1.0, httpx_gia)
        except sc._KhongTaiLai:
            raise
        except Exception as e:
            loi = e
    raise loi


def test_dut_giua_chung_thi_tai_lai_chu_khong_mat_anh_da_tra_tien(sc, tmp_path):
    dem = {"n": 0}
    dich = tmp_path / "anh.png"

    _tai(sc, "http://kho/anh.png", dich, _httpx_gia(dem, dut_toi_lan=2))

    assert dich.exists(), "phai cuu duoc anh sau khi kho luu tru dut ket noi"
    assert dich.read_bytes() == b"PHAN-DAU-PHAN-CUOI", "file phai DU, khong duoc cut dau"
    assert dem["n"] == 3, "phai thu lai den khi duoc, khong bo cuoc o lan dau"


def test_khong_bao_gio_de_lai_file_do_dang(sc, tmp_path):
    """Nơi gọi chỉ kiểm `exists()` — một `.part` sót lại trông y hệt đã tải xong."""
    dem = {"n": 0}
    dich = tmp_path / "anh.png"

    with pytest.raises(Exception):
        _tai(sc, "http://kho/anh.png", dich, _httpx_gia(dem, dut_toi_lan=99), so_lan=2)

    assert not dich.exists()
    assert not (tmp_path / "anh.png.part").exists(), "phai don .part khi hong"


def test_404_thi_dung_ngay_khong_tai_lai(sc, tmp_path):
    """Link hết hạn/sai thì tải lại cũng vẫn thế — chỉ tổ chậm."""
    dem = {"n": 0}
    with pytest.raises(sc._KhongTaiLai):
        _tai(sc, "http://kho/mat-roi.png", tmp_path / "x.png", _httpx_gia(dem, ma=404))
    assert dem["n"] == 1


def test_5xx_thi_van_tai_lai(sc, tmp_path):
    """Kho lưu trữ trục trặc tạm — khác hẳn link hỏng."""
    dem = {"n": 0}
    with pytest.raises(Exception):
        _tai(sc, "http://kho/anh.png", tmp_path / "x.png", _httpx_gia(dem, ma=503), so_lan=3)
    assert dem["n"] == 3


# ── SDK phải ĐI KÈM repo ─────────────────────────────────────────────────────


def test_SDK_di_kem_trong_repo():
    """`shopapi` CHƯA lên PyPI — `pip install shopapi` không có tác dụng.

    Máy khác chỉ nhận được SDK nếu nó nằm sẵn trong repo. Ngày 14/08/2026 repo
    KHÔNG kèm `_sdk/`, nên máy này chạy được chỉ vì `sdk_search_paths` có một
    đường dẫn tuyệt đối tới kho mã shopapi (ổ D, thư mục `New folder/shopapi`) —
    thứ duy nhất máy chủ dự án mới có. Mọi máy khác báo "thiếu SDK" và tool
    không gửi nổi một job nào.
    """
    from pathlib import Path
    goc = Path(__file__).resolve().parents[1]
    assert (goc / "_sdk" / "shopapi" / "__init__.py").is_file(), (
        "repo khong kem SDK -> may khac cap nhat xong van khong chay duoc")


def test_SDK_tim_duoc_KHONG_can_duong_dan_rieng_cua_may_nay(sc):
    """Bản kèm repo phải được ưu tiên TRƯỚC đường dẫn tuyệt đối của máy chủ dự án."""
    import os
    from pathlib import Path
    goc = Path(__file__).resolve().parents[1]
    duong = sc.sdk_search_paths()
    co = [p for p in duong if os.path.isdir(os.path.join(p, "shopapi"))]
    assert co, "khong tim thay SDK o bat ky dau"
    kem = str(goc / "_sdk")
    assert any(os.path.normcase(p) == os.path.normcase(kem) for p in co), \
        "ban kem repo khong nam trong danh sach tim thay"
    # Bản kèm repo phải đứng TRƯỚC mọi đường dẫn trỏ ra ngoài thư mục tool.
    ngoai = [p for p in co if not os.path.normcase(p).startswith(os.path.normcase(str(goc)))]
    if ngoai:
        assert co.index(kem) < min(co.index(p) for p in ngoai), \
            "duong dan rieng cua may nay thang ban kem repo -> may khac van thieu SDK"


# ── Ngân sách luồng: hạ được thì phải LÊN lại được ───────────────────────────


def test_ngan_sach_ha_roi_TU_LANH_sau_khi_het_han(sc, tmp_path, monkeypatch):
    """Bẫy một chiều: hạ thì dễ, lên thì không có đường.

    Đã dính thật 15/08/2026. Bộ kiểm thử mô phỏng "máy hết luồng" chạy trên thư
    mục nhịp sống CHUNG (lúc đó chưa cách ly), gọi `ha_ngan_sach_luong` mấy
    lượt, và ghim ngân sách của cả máy xuống sàn 24. Sau đó MỌI lần chạy thật
    khởi động ở 24 job thay vì 979 — không một dòng nào nói vì sao. Một cú nghẹn
    thoáng qua không được phép định đoạt phần còn lại của đời máy.
    """
    import os, time
    d = str(tmp_path)
    monkeypatch.setenv("SHOPAPI_NHIP_DIR", d)
    goc = sc.ngan_sach_luong(thu_muc=d)
    ha = sc.ha_ngan_sach_luong(thu_muc=d)
    assert ha == max(sc.NGAN_SACH_LUONG_SAN, goc // 2)
    assert sc.ngan_sach_luong(thu_muc=d) == ha, "vua ha xong ma khong nho"
    # Đẩy dấu thời gian ra quá hạn -> phải quay về mức mặc định để thăm dò lại.
    f = os.path.join(d, "ngan-sach-luong")
    cu = time.time() - sc.NGAN_SACH_HA_TTL - 60
    os.utime(f, (cu, cu))
    assert sc.ngan_sach_luong(thu_muc=d) == goc, "het han roi ma van ghim o muc da ha"


def test_ngan_sach_khong_bao_gio_xuong_duoi_SAN(sc, tmp_path, monkeypatch):
    monkeypatch.setenv("SHOPAPI_NHIP_DIR", str(tmp_path))
    for _ in range(20):
        sc.ha_ngan_sach_luong(thu_muc=str(tmp_path))
    assert sc.ngan_sach_luong(thu_muc=str(tmp_path)) == sc.NGAN_SACH_LUONG_SAN


def test_ngan_sach_khong_vuot_muc_MAC_DINH(sc, tmp_path, monkeypatch):
    """File rác ghi 999999 không được biến thành 999999 luồng."""
    import os
    monkeypatch.setenv("SHOPAPI_NHIP_DIR", str(tmp_path))
    with open(os.path.join(str(tmp_path), "ngan-sach-luong"), "w", encoding="utf-8") as f:
        f.write("999999")
    assert sc.ngan_sach_luong(thu_muc=str(tmp_path)) <= sc.NGAN_SACH_LUONG_MAC_DINH


def test_dem_ban_BO_QUA_file_ngan_sach(sc, tmp_path, monkeypatch):
    """File ngân sách nằm chung thư mục — đếm nhầm nó là một chỗ ngồi là chia sai trần."""
    monkeypatch.setenv("SHOPAPI_NHIP_DIR", str(tmp_path))
    sc.ha_ngan_sach_luong(thu_muc=str(tmp_path))
    assert sc.dem_ban_dang_chay("", thu_muc=str(tmp_path)) == 1
    with sc.NhipSong("image", thu_muc=str(tmp_path)):
        assert sc.dem_ban_dang_chay("image", thu_muc=str(tmp_path)) == 1
        assert sc.dem_ban_dang_chay("video", thu_muc=str(tmp_path)) == 1   # san la 1


def test_nhip_song_NGUOI_DA_CHET_khong_con_tinh(sc, tmp_path, monkeypatch):
    """Tiến trình chết đột ngột để lại file — nguội đi thì tự hết tính, khỏi dọn."""
    import os, time
    monkeypatch.setenv("SHOPAPI_NHIP_DIR", str(tmp_path))
    n = sc.NhipSong("image", thu_muc=str(tmp_path))
    assert sc.dem_ban_dang_chay("image", thu_muc=str(tmp_path)) == 1
    cu = time.time() - sc.NHIP_SONG_HAN - 10
    os.utime(n.duong_dan, (cu, cu))
    assert sc.dem_ban_dang_chay("image", thu_muc=str(tmp_path)) == 1, "san phai la 1"
    n2 = sc.NhipSong("image", thu_muc=str(tmp_path))
    assert sc.dem_ban_dang_chay("image", thu_muc=str(tmp_path)) == 1, \
        "file nguoi da chet van bi tinh -> chia tran cho ca ma khong con chay"
    n2.dong()


# ── `/v1/me` dùng CHUNG: 24 mã, một lời hỏi ──────────────────────────────────
#
# Mỗi mã là một tiến trình riêng và mỗi tiến trình tự hỏi `/v1/me`. Hai mươi
# tư mã cùng hỏi, cộng với chính tải job đang gửi, là đủ để hạn mức 1.000
# request/phút nuốt hết những lời hỏi trạng thái đó.
#
# Log 17:43–17:44 ngày 15/08/2026: gần như MỌI mã đều ghi `khong hoi duoc GET
# /v1/me` rồi rơi về trần mù 32. Mười mã × 32 = 320 chỗ xin trong khi máy chủ
# cấp 345 và đang chia cho từng ấy tiến trình — nên `429` liên tục, AIMD chia
# đôi mãi (`nhip 4.5 cho phep 4`), cả dây chuyền bò.
#
# Nghịch lý: tool hỏi trạng thái nhiều tới mức không còn đọc nổi trạng thái.


class _MeGia:
    def __init__(self, tran=100):
        self.so_lan = 0
        self._tran = tran

    def __call__(self, api_key=None, client=None, timeout=20.0):
        self.so_lan += 1
        return {"limits": {"concurrent_jobs": {"image": self._tran, "video": self._tran},
                           "concurrent_jobs_detail": {
                               "image": {"limit": self._tran, "hard_cap": 1536},
                               "video": {"limit": self._tran, "hard_cap": 832}}}}


def test_doc_v1_me_chung_chi_hoi_MOT_LAN_trong_TTL(sc, tmp_path, monkeypatch):
    monkeypatch.setenv("SHOPAPI_NHIP_DIR", str(tmp_path))
    gia = _MeGia()
    monkeypatch.setattr(sc, "doc_v1_me", gia)
    for _ in range(24):
        me = sc.doc_v1_me_chung(thu_muc=str(tmp_path))
        assert me["limits"]["concurrent_jobs"]["video"] == 100
    assert gia.so_lan == 1, "hoi {0} lan thay vi 1".format(gia.so_lan)


def test_het_TTL_thi_hoi_lai(sc, tmp_path, monkeypatch):
    import os
    monkeypatch.setenv("SHOPAPI_NHIP_DIR", str(tmp_path))
    gia = _MeGia()
    monkeypatch.setattr(sc, "doc_v1_me", gia)
    sc.doc_v1_me_chung(thu_muc=str(tmp_path))
    f = os.path.join(str(tmp_path), "v1-me.json")
    cu = os.path.getmtime(f) - sc.ME_CHUNG_TTL - 5
    os.utime(f, (cu, cu))
    sc.doc_v1_me_chung(thu_muc=str(tmp_path))
    assert gia.so_lan == 2


def test_24_TIEN_TRINH_hoi_CUNG_LUC_van_chi_MOT_loi_goi(sc, tmp_path, monkeypatch):
    """Lúc khởi động cả đàn cùng trượt bộ đệm — đó mới là cảnh thật.

    Đo trước khi có chốt: 24 luồng hỏi cùng lúc trên bộ đệm rỗng vẫn tốn đủ 24
    lời gọi, vì chưa đứa nào kịp ghi. Đúng cảnh 24 mã bật lên trong mươi giây.
    """
    import threading
    monkeypatch.setenv("SHOPAPI_NHIP_DIR", str(tmp_path))
    gia = _MeGia()

    def _cham(api_key=None, client=None, timeout=20.0):
        import time as _t
        _t.sleep(0.4)          # một lời hỏi thật mất vài trăm mili-giây
        return gia(api_key=api_key, client=client, timeout=timeout)

    monkeypatch.setattr(sc, "doc_v1_me", _cham)
    ra = []
    ts = [threading.Thread(target=lambda: ra.append(sc.doc_v1_me_chung(thu_muc=str(tmp_path))))
          for _ in range(24)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    assert gia.so_lan == 1, "hoi {0} lan — chot di hoi khong an".format(gia.so_lan)
    assert all(m and m["limits"]["concurrent_jobs"]["video"] == 100 for m in ra), \
        "co tien trinh khong doc duoc tran sau khi cho"


def test_hoi_hut_thi_DUNG_BAN_CU_qua_han_chu_khong_tra_rong(sc, tmp_path, monkeypatch):
    """Trần cũ vài chục giây vẫn sát hơn hẳn con số mù."""
    import os
    monkeypatch.setenv("SHOPAPI_NHIP_DIR", str(tmp_path))
    monkeypatch.setattr(sc, "doc_v1_me", _MeGia(tran=77))
    sc.doc_v1_me_chung(thu_muc=str(tmp_path))
    f = os.path.join(str(tmp_path), "v1-me.json")
    cu = os.path.getmtime(f) - sc.ME_CHUNG_TTL - 60
    os.utime(f, (cu, cu))
    monkeypatch.setattr(sc, "doc_v1_me", lambda **k: {})     # mạng hỏng
    me = sc.doc_v1_me_chung(thu_muc=str(tmp_path))
    assert me and me["limits"]["concurrent_jobs"]["video"] == 77


def test_tran_song_song_DI_QUA_ban_dung_chung(sc, tmp_path, monkeypatch):
    """Gọi thẳng `client.tran_song_song` là mỗi tiến trình một lời hỏi."""
    monkeypatch.setenv("SHOPAPI_NHIP_DIR", str(tmp_path))
    gia = _MeGia(tran=55)
    monkeypatch.setattr(sc, "doc_v1_me", gia)
    for _ in range(10):
        assert sc.tran_song_song("video") == 55
        assert sc.tran_song_song("image") == 55
    assert gia.so_lan == 1, "20 luot hoi tran ton {0} loi goi".format(gia.so_lan)
