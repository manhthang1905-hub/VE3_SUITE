# VE3 Suite Web Admin

Web dashboard thu nghiem de quan ly `PROJECTS`, cau hinh chinh, va chay pipeline thong qua `run_project_headless.py`.

## Muc tieu

- Khong thay the GUI cu.
- Thu nghiem mot lop dieu phoi web de giam cam giac do giao dien.
- Co the xoa toan bo thu muc nay neu khong phu hop.

## Chuc nang hien tai

- Danh sach project trong `PROJECTS`
- Tong hop nhanh audio, srt, excel, image, video
- Chay job theo mode:
  - `all`
  - `srt-excel-only`
  - `excel-only`
  - `ve3-only`
- Xem log job gan nhat
- Xem va sua mot so config chinh cua `tools/ve3/config/settings.yaml`
- Xem va sua mot so config chinh cua `tools/srt-to-excel/config/settings.yaml`
- Ping cac server trong `local_server_list`

## Chay

```bat
tools\web-admin\RUN_WEB_ADMIN.bat
```

Hoac:

```bat
python tools\web-admin\app.py
```

Mo:

`http://127.0.0.1:5070`

## Ghi chu

- Day la Flask development server.
- Ban web nay khong dong vao GUI cu.
- Job stop tren Windows la soft-stop, phu thuoc subprocess va cac tool con cua pipeline.
