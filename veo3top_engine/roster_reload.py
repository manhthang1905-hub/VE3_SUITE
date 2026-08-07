"""roster_reload — NẠP LẠI NÓNG danh sách tài khoản cho ba nhà máy (ảnh browser, ảnh curl, video).

═══════════════════════════════════════════════════════════════════════════════
VÌ SAO FILE NÀY TỒN TẠI — SỰ CỐ ĐO ĐƯỢC NGÀY 07/08/2026
═══════════════════════════════════════════════════════════════════════════════

Nhà máy ảnh (:8789) trả về đúng con số này lúc khách đang chạy job thật:

    slots: 64, candidates: 64, not_logged_in: 50, logged_in: 8, known_good: 45

Trong khi KHO tài khoản trong CSDL lúc đó là **96/96 sẵn sàng**.

Hậu quả đo được trong `workers/veo3/logs/worker-20260807.jsonl`:

    "Đang tạo ảnh 1/1 (627s)"   "(748s)"   "(769s)"

tức **600–780 giây một ảnh** thay vì ~90 giây. Một khách thật
(`gunc94@gmail.com`) có 4 job bò ở tốc độ đó. Chậm gấp ~8 lần, và KHÔNG có một
dòng lỗi nào — nhà máy vẫn báo "khoẻ", chỉ là nó đang chia việc cho 8 tài khoản
đăng nhập được thay vì 96.

NGUYÊN NHÂN: cả ba nhà máy nạp danh sách tài khoản **đúng một lần lúc khởi
động** rồi không bao giờ nạp lại (`ImagePoolBrowser.start`, `ImageFactory.start`,
`VideoFactory.start`). Nhà máy khởi động TRƯỚC lúc kho được nạp đầy, nên nó chạy
cả ca bằng cookie cũ đã chết.

VÌ SAO LỖI NÀY SỐNG DAI: cách duy nhất để nạp phiên mới là khởi động lại nhà
máy, mà khởi động lại là **giết mọi job đang chạy dở của khách**. Nên cái giá
để sửa nó luôn rơi vào khách, và ai cũng hoãn. File này bỏ cái giá đó đi.

═══════════════════════════════════════════════════════════════════════════════
RÀNG BUỘC SỐ MỘT: KHÔNG ĐƯỢC ĐỤNG TÀI KHOẢN ĐANG BẬN
═══════════════════════════════════════════════════════════════════════════════

`hop_nhat_roster` **giữ nguyên đối tượng tài khoản cũ** cho mọi email còn nằm
trong danh sách mới — không dựng lại. Dựng lại là vứt cookie/bearer/IPv6/chrome
đang mở và job đang chạy trên đó chết theo, tức là ta vừa xây lại đúng cái giá
mà cả file này sinh ra để tránh.

Và tài khoản **đang bận** thì không bao giờ bị gỡ ra, kể cả khi nó đã rời danh
sách mới. Nó được đếm vào `giu_lai_vi_ban` để lượt nạp sau dọn nốt — lúc đó nó
đã rảnh và việc gỡ không giết ai. Thà chậm một nhịp còn hơn giết một job.

Đây là mã THUẦN (không mạng, không Chrome, không tiến trình con) nên bộ kiểm
`D:\\VE3_SUITE\\tests\\test_nap_lai_nong.py` phủ được toàn bộ quyết định ở đây.
"""

import threading
import time

__all__ = [
    "khoa_email",
    "KetQuaHopNhat",
    "hop_nhat_roster",
    "canh_bao_kho_lech",
    "dung_bao_cao",
    "AN_HAN_GIAY",
    "TY_LE_BAO_DONG",
    "KHO_TOI_THIEU",
]

#: Nhà máy vừa bật thì `logged_in` đương nhiên thấp (tài khoản đang lần lượt vào
#: ca). Cảnh báo trong quãng đó chỉ tạo tiếng ồn rồi bị bỏ qua — mà một cảnh báo
#: bị bỏ qua thì tệ hơn không có, vì lần lệch THẬT cũng sẽ bị bỏ qua theo.
AN_HAN_GIAY = 300.0

#: Dưới ngưỡng này so với kho là BẤT THƯỜNG, không phải "đang ấm máy".
#: Số đo 07/08: 8/96 = 0.083. Đặt 0.5 để bắt được cả những ca nhẹ hơn nhiều
#: (48/96) mà vẫn không kêu oan khi vài tài khoản đang nghỉ hồi quota.
TY_LE_BAO_DONG = 0.5

#: Kho quá nhỏ thì tỷ lệ mất ý nghĩa (1/2 tài khoản = 0.5, chẳng nói lên gì).
KHO_TOI_THIEU = 4


def khoa_email(x):
    """Khoá so khớp tài khoản. Email KHÔNG phân biệt hoa thường và hay dính
    khoảng trắng thừa khi đi qua yaml/sqlite/JSON — so chuỗi thô là cách chắc
    chắn nhất để cùng một tài khoản bị đếm hai lần rồi mở hai phiên song song."""
    return str(x or "").strip().lower()


class KetQuaHopNhat:
    """Kết quả một lượt hợp nhất roster.

    * `danh_sach`      — danh sách tài khoản MỚI, đã sắp: giữ trước, thêm sau.
    * `giu`            — object CŨ được giữ nguyên (không dựng lại, không đụng).
    * `them`           — email vừa được thêm vào.
    * `bo`             — email vừa bị gỡ ra (chỉ gồm tài khoản ĐANG RẢNH).
    * `giu_lai_vi_ban` — email đáng lẽ bị gỡ nhưng ĐANG BẬN nên hoãn tới lượt sau.
    """

    __slots__ = ("danh_sach", "giu", "them", "bo", "giu_lai_vi_ban")

    def __init__(self, danh_sach, giu, them, bo, giu_lai_vi_ban):
        self.danh_sach = danh_sach
        self.giu = giu
        self.them = them
        self.bo = bo
        self.giu_lai_vi_ban = giu_lai_vi_ban

    def co_doi(self):
        return bool(self.them or self.bo)


def hop_nhat_roster(hien_tai, mong_muon, *, lay_khoa, lay_khoa_mong_muon, dang_ban, tao_moi):
    """Hợp nhất roster ĐANG CHẠY với roster MONG MUỐN, KHÔNG đụng tài khoản bận.

    Tham số đều là hàm để dùng chung được cho ba nhà máy có ba lớp `Account`
    khác nhau (và để bộ kiểm dựng được cảnh giả bằng object tầm thường):

        lay_khoa(obj)          -> khoá của một tài khoản ĐANG CHẠY
        lay_khoa_mong_muon(d)  -> khoá của một mục trong danh sách mới
        dang_ban(obj)          -> True nếu tài khoản đó đang chạy job của khách
        tao_moi(d)             -> dựng object tài khoản mới từ một mục

    BA LUẬT, theo đúng thứ tự ưu tiên:

    1. Email có ở CẢ HAI  -> GIỮ NGUYÊN object cũ. Không dựng lại. Đây là luật
       quan trọng nhất: object cũ đang giữ cookie/bearer/chrome/IPv6 và có thể
       đang chạy job.
    2. Email chỉ có ở danh sách MỚI -> `tao_moi`. Đây là phần cứu sự cố 07/08:
       tài khoản đăng nhập xong sau lúc nhà máy khởi động rốt cuộc cũng vào ca.
    3. Email chỉ có ở danh sách CŨ  -> gỡ, NHƯNG chỉ khi nó đang RẢNH. Đang bận
       thì giữ lại tới lượt nạp sau (xem chú thích đầu file).

    `tao_moi` ném ngoại lệ thì mục đó bị bỏ qua chứ không làm hỏng cả lượt nạp —
    một tài khoản dữ liệu rác không được phép chặn 95 tài khoản còn lại.
    """
    cu = {}
    for obj in hien_tai:
        k = khoa_email(lay_khoa(obj))
        if k and k not in cu:
            cu[k] = obj

    moi_theo_khoa = {}
    thu_tu = []
    for d in mong_muon:
        k = khoa_email(lay_khoa_mong_muon(d))
        if not k or k in moi_theo_khoa:
            continue
        moi_theo_khoa[k] = d
        thu_tu.append(k)

    giu, them, bo, giu_lai = [], [], [], []
    danh_sach = []

    # (1) + (3): duyệt roster CŨ trước để giữ đúng thứ tự đang chạy.
    for k, obj in cu.items():
        if k in moi_theo_khoa:
            giu.append(obj)
            danh_sach.append(obj)
        elif dang_ban(obj):
            giu_lai.append(k)
            danh_sach.append(obj)   # ĐANG BẬN -> ở lại, dọn ở lượt sau
        else:
            bo.append(k)

    # (2): thêm mới vào CUỐI — tài khoản đang chạy giữ nguyên vị trí/slot.
    for k in thu_tu:
        if k in cu:
            continue
        try:
            obj = tao_moi(moi_theo_khoa[k])
        except Exception:
            continue
        if obj is None:
            continue
        them.append(k)
        danh_sach.append(obj)

    return KetQuaHopNhat(danh_sach, giu, them, bo, giu_lai)


def canh_bao_kho_lech(logged_in, san_sang, *, uptime_s=None, an_han_giay=AN_HAN_GIAY,
                      ty_le=TY_LE_BAO_DONG, kho_toi_thieu=KHO_TOI_THIEU):
    """Kho có N tài khoản sẵn sàng mà nhà máy chỉ đăng nhập được M -> KÊU LÊN.

    Trả về câu cảnh báo (chuỗi) hoặc "" nếu bình thường.

    ═══ VÌ SAO PHẢI CÓ CẢNH BÁO NÀY ═══

    Ngày 07/08/2026 nhà máy chạy 8/96 tài khoản suốt cả ca. Mọi phép kiểm sức
    khoẻ đều xanh: cổng nghe, `/health` trả 200, `candidates > 0`, không một
    dòng lỗi. Thứ duy nhất sai là ảnh mất 600–780 giây thay vì ~90 — và cái đó
    thì không ai đo tự động.

    Một lỗi làm chậm 8 lần mà KHÔNG kêu sẽ sống lâu hơn hẳn một lỗi làm sập
    dịch vụ, vì lỗi làm sập thì có người sửa trong 10 phút. Nên chỗ này cố ý
    kêu to bằng cả log LẪN một khoá trong `/health`.
    """
    try:
        logged_in = int(logged_in or 0)
        san_sang = int(san_sang or 0)
    except (TypeError, ValueError):
        return ""
    if san_sang < kho_toi_thieu:
        return ""
    if uptime_s is not None and float(uptime_s) < an_han_giay:
        return ""       # còn đang ấm máy, chưa kết luận
    if logged_in >= san_sang * ty_le:
        return ""
    thieu = max(0, san_sang - logged_in)
    return (
        f"KHO LỆCH: chỉ {logged_in}/{san_sang} tài khoản đăng nhập được "
        f"(thiếu {thieu}). Nhà máy đang chia việc cho quá ít tài khoản -> ảnh sẽ "
        f"CHẬM GẤP NHIỀU LẦN mà không báo lỗi (07/08/2026 đo được 8/96 -> ảnh "
        f"600-780 giây thay vì ~90). Gọi POST /reload_accounts để nạp lại phiên "
        f"nóng, KHÔNG cần khởi động lại nhà máy."
    )


def dung_bao_cao(truoc, sau, kq, canh_bao=""):
    """Thân phản hồi của `POST /reload_accounts`.

    Người vận hành nhìn MỘT lần là biết có ăn thua không, nên bắt buộc có số
    liệu TRƯỚC và SAU chứ không chỉ "ok: true" — 07/08 mọi thứ đều "ok: true".
    """
    return {
        "ok": True,
        "truoc": dict(truoc or {}),
        "sau": dict(sau or {}),
        "them": len(kq.them),
        "bo": len(kq.bo),
        "them_email": list(kq.them),
        "bo_email": list(kq.bo),
        "giu_lai_vi_ban": len(kq.giu_lai_vi_ban),
        "giu_lai_vi_ban_email": list(kq.giu_lai_vi_ban),
        "canh_bao": canh_bao or "",
        "ts": int(time.time()),
    }


class KhoaNapLai:
    """Chỉ cho MỘT lượt nạp lại chạy tại một thời điểm.

    Hai lượt nạp chồng nhau sẽ cùng dựng object cho cùng một email rồi cùng ghi
    đè `self.accounts` — kết quả là một tài khoản có hai object, hai luồng thợ,
    hai phiên Chrome trên cùng một hồ sơ. Đó là loại lỗi chỉ hiện ra lúc đông
    khách, đúng lúc không ai muốn gỡ nó.
    """

    def __init__(self):
        self._lock = threading.Lock()

    def __enter__(self):
        self._lock.acquire()
        return self

    def __exit__(self, *a):
        self._lock.release()
        return False
