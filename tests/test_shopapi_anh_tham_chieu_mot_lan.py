"""Ảnh tham chiếu tải MỘT lần cho cả mẻ (sự cố 03/10/2026).

Khách chết cứng ở "Vượt hạn mức lưu trữ tạm": 346 tệp trong 2 giờ, chỉ 5 ảnh
khác nhau — mỗi job ảnh tải lại toàn bộ ảnh nhân vật.
"""
from __future__ import annotations

import threading

import shopapi_image_client as sic
from conftest import FakeClient


def _nhat_ky(*_a, **_k):
    pass


def test_cung_anh_bytes_nhieu_job_chi_tai_mot_lan():
    client = FakeClient()
    nv = b"\x89PNG" + b"nhan-vat-1" * 100
    urls = [sic.chuan_bi_reference_urls(client, [nv], log=_nhat_ky)[0] for _ in range(50)]
    assert len(client.so.uploads) == 1
    assert len(set(urls)) == 1


def test_cung_anh_duong_dan_nhieu_job_chi_tai_mot_lan(tmp_path):
    client = FakeClient()
    p = tmp_path / "nv1.png"
    p.write_bytes(b"\x89PNG" + b"x" * 500)
    for _ in range(20):
        sic.chuan_bi_reference_urls(client, [str(p)], log=_nhat_ky)
    assert len(client.so.uploads) == 1


def test_nam_anh_khac_nhau_thi_tai_nam_lan():
    client = FakeClient()
    anh = [b"\x89PNG" + bytes([i]) * 300 for i in range(5)]
    for _ in range(30):
        sic.chuan_bi_reference_urls(client, anh[:3], log=_nhat_ky)
        sic.chuan_bi_reference_urls(client, anh[2:], log=_nhat_ky)
    assert len(client.so.uploads) == 5


def test_nhieu_luong_cung_luc_van_chi_tai_mot_lan():
    client = FakeClient()
    nv = b"\x89PNG" + b"chung" * 200
    luong = [threading.Thread(target=sic.chuan_bi_reference_urls, args=(client, [nv]),
                              kwargs={"log": _nhat_ky}) for _ in range(24)]
    for t in luong:
        t.start()
    for t in luong:
        t.join()
    assert len(client.so.uploads) == 1


def test_link_het_han_thi_xin_link_moi_khong_tai_lai(monkeypatch):
    client = FakeClient()
    hoi = []
    client.uploads.retrieve = lambda ma: (hoi.append(ma) or
                                          {"url": "https://cdn.example.invalid/x/{0}.png?moi=1".format(ma)})
    nv = b"\x89PNG" + b"het-han" * 50
    sic.chuan_bi_reference_urls(client, [nv], log=_nhat_ky)
    monkeypatch.setattr(sic, "_HAN_LINK", -1.0)
    url = sic.chuan_bi_reference_urls(client, [nv], log=_nhat_ky)[0]
    assert len(client.so.uploads) == 1 and len(hoi) == 1 and "moi=1" in url


def test_kho_day_thi_xoa_anh_cu_khong_dung_roi_tai_lai(monkeypatch):
    client = FakeClient()
    cu = b"\x89PNG" + b"cu" * 100
    sic.chuan_bi_reference_urls(client, [cu], log=_nhat_ky)
    monkeypatch.setattr(sic, "_DANG_DUNG", -1.0)       # ảnh cũ coi như thôi dùng
    goc = client.uploads.upload_file
    lan = [0]

    def day_mot_lan(file, filename=None, content_type=None):
        lan[0] += 1
        if lan[0] == 1:
            raise RuntimeError("Vượt hạn mức lưu trữ tạm: bạn đang giữ 499.1 MB")
        return goc(file, filename=filename, content_type=content_type)

    client.uploads.upload_file = day_mot_lan
    url = sic.chuan_bi_reference_urls(client, [b"\x89PNG" + b"moi" * 100], log=_nhat_ky)[0]
    assert url and client.uploads.da_xoa == ["upl_giabo1kytu"]
