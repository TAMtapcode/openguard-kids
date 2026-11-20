# Kiến trúc Open Guardian Kids

```
 Máy con (Windows 10/11)                                         Máy chủ (FastAPI)               Phụ huynh
┌────────────────────────────────────────────┐                ┌──────────────────────┐      ┌─────────────────┐
│ OGK-Service (LocalSystem)                  │  HTTPS+pin     │ REST /api/v1/*       │      │ Trình duyệt     │
│  ├ Engine: tick 1s, đếm giờ monotonic      │ ─────────────▶ │  enrollment, token,  │      │ Dashboard       │
│  ├ Enforcer: ok → grace 60s → locked       │  heartbeat 60s │  heartbeat, policy,  │ ◀──▶ │ (Jinja2 + JS,   │
│  ├ ProcessGuard (psutil + SHA-256)         │ ◀───────────── │  events/sync,        │      │  responsive)    │
│  ├ DNS proxy 127.0.0.1:53 (dnslib)         │  policy+HMAC   │  requests, audit     │      └─────────────────┘
│  ├ SQLite WAL: chính sách, bộ đếm, hàng đợi│      WSS       │ WS /ws/agent/{id}    │
│  └ IPC loopback :49152 + token ◀────┐      │ ◀═════════════ │  LOCK_NOW / ADD_TIME │      SQLite WAL / PostgreSQL
│ OGK-Tray (quyền User) ──────────────┘      │  lệnh khẩn <5s │ Parent API /api/parent│     (Argon2id, AES-GCM, audit)
│  khay, popup, Khu vườn số, Đổi giờ, nhật ký│                └──────────────────────┘
└────────────────────────────────────────────┘
```

## 1. Phân tách đặc quyền
- **Service** (LocalSystem) giữ mọi thứ cần quyền: kết thúc tiến trình, DNS proxy, khóa phiên, bí mật thiết bị. Trẻ (User thường) không dừng/sửa được.
- **Tray** (User) chỉ hiển thị và gửi *telemetry phiên người dùng* (idle, màn hình khóa, tên tiến trình tiền cảnh) cho Service, vì `GetLastInputInfo` và `GetForegroundWindow` chỉ đúng trong phiên của trẻ, Service ở phiên 0 không đọc được.
- **IPC**: HTTP loopback `127.0.0.1:49152` + header `X-OGK-Token`. Token ngẫu nhiên nằm ở `ProgramData\OpenGuardKids\run` với ACL: *Users chỉ đọc, chỉ SYSTEM/Administrators ghi* nên tiến trình thường không thay được token. IPC chỉ cho phép hành động mức "trẻ" (xin giờ, đổi giờ, xem trạng thái).
- Khóa máy: KHÔNG ngắt/khóa phiên Windows (bản cũ dùng `WTSDisconnectSession` mỗi 5 giây, có thể làm máy kẹt ở màn hình đăng nhập tới mức phải vào Safe Mode). Khay hiển thị màn hình khóa toàn màn hình trong phiên của trẻ, luôn có nút Đăng xuất/Tắt máy và nút xin thêm giờ; tự đóng khi hết khóa. "Khóa ngay" của bố mẹ luôn có thời hạn (mặc định 60 phút, tối đa 4 giờ) nên máy không bao giờ bị kẹt khóa mãi; thời gian sau màn hình khóa không bị trừ vào hạn mức.

## 2. Giao thức
| Phương thức | Đường dẫn | Ghi chú |
|---|---|---|
| POST | `/api/v1/enrollment` | Mã 8 ký tự (bỏ I/O/0/1), dùng 1 lần, hiệu lực 10 phút, giới hạn 10 lần/phút/IP. Trả `device_id`, access JWT 15 phút, refresh token, `policy_key` |
| POST | `/api/v1/token/refresh` | Xoay vòng refresh token (cái cũ vô hiệu). Thiết bị bị gỡ → 401 |
| POST | `/api/v1/heartbeat` | 60s/lần: `{policy_version, day, used_today_sec, remaining_sec, group_usage, state, ui_ok}` → `{server_time, policy_version, commands[]}` |
| GET | `/api/v1/policy` | `{policy, sig}`; `sig = HMAC-SHA256(policy_key, 
canonical_json(policy))`, trong `policy` có `device_id`, `version` |
| POST | `/api/v1/events/sync` | Lô ≤500 sự kiện; khử trùng bằng `idem_key` (UUIDv4) → `{accepted, duplicates, rejected}` |
| POST | `/api/v1/requests/{add-time\|false-block}` | Yêu cầu của trẻ; chống spam 1/phút |
| GET | `/api/v1/audit`, `/api/v1/requests` | Trẻ xem nhật ký luật và phản hồi của phụ huynh |
| WS | `/ws/agent/{device_id}` | Header `Authorization: Bearer`; server đẩy `{"id","action":"LOCK_NOW"\|"UNLOCK"\|"ADD_TIME","minutes"}`; agent ack `{"ack": id}` |

Lệnh được lưu bền (`commands`); nếu WS không sẵn sàng thì giao qua heartbeat. Agent khử trùng theo `id` nên lệnh đến cả hai đường chỉ áp dụng một lần. `LOCK_NOW` hết hạn sau 10 phút nếu chưa giao, `ADD_TIME` sau 24 giờ.

## 3. Quyết định thiết kế (và lý do)
1. **Đồng hồ**: giờ tin cậy = neo vào giờ máy chủ, sau đó chỉ tiến theo `time.monotonic()`. Hạn mức *và* lịch tuần dùng giờ này, nên chỉnh giờ Windows vô hiệu. Sau khi khởi động lại không bao giờ tin giờ lùi hơn mốc đã lưu. Độ lệch >120 giây ghi sự kiện `quota_warning` với `subject=clock_tamper:<giây>s` (giữ nguyên lược đồ sự kiện), và báo cho trẻ.
2. **Đếm giờ**: mỗi nhịp tối đa 5 giây (máy ngủ 1 giờ không bị tính 1 giờ); chỉ đếm khi có người dùng đăng nhập, màn hình không khóa, không "idle" quá 5 phút. Bộ đếm lưu SQLite mỗi 10 giây, khởi động lại không đặt lại hạn mức.
3. **Ân hạn**: khi hạn mức/lịch hết lúc đang dùng → 60 giây ân hạn (popup đếm ngược để lưu bài) rồi khóa. Mở máy khi đã hết giờ/ngoài khung giờ → khóa ngay, không ân hạn. Cảnh báo còn 10/5/1 phút bắn **một lần** mỗi mốc; nếu luật đổi làm tụt qua nhiều mốc thì chỉ hiện mốc gấp nhất, lời nhắn dùng số phút thực còn lại.
4. **Ngân sách theo nhóm**: bộ đếm riêng cho từng nhóm. Nhóm của một khoảnh khắc = nhóm của ứng dụng tiền cảnh ∪ nhóm của tên miền được truy vấn trong 120 giây gần nhất (chỉ khi trẻ đang thao tác). Hết ngân sách → chặn tiến trình và tên miền của nhóm đó, nhóm khác không ảnh hưởng. Học tập không giới hạn.
5. **Đổi giờ học lấy giờ chơi**: phút học (`g:education`) × `earn_ratio`, trần `daily_cap_min`. Đổi cộng vào cả hạn mức tổng và ngân sách nhóm. Không cần phụ huynh duyệt, nhưng mọi lần đổi đều là sự kiện `override_granted`. ("Cộng giờ" thủ công của phụ huynh chỉ cộng hạn mức tổng.)
6. **Thời lượng tên miền chỉ là ước lượng**: DNS không cho biết thời gian xem. Hai truy vấn cùng tên miền cách nhau <120 giây được coi là xem liên tục; truy vấn nền lúc trẻ không thao tác bị bỏ qua. Báo cáo ghi rõ đây là ước lượng.
7. **Không có khay thì agent dừng chặn (NT1)**: Service tự mở lại Tray bằng `CreateProcessAsUser`; trong 90 giây đó vẫn tính giờ (tắt khay không thành cách né giờ). Quá hạn → trạng thái `suspended`: ngừng chặn/khóa, báo `ui_ok=false` lên dashboard để phụ huynh biết. Đây là đánh đổi có chủ ý giữa "không ẩn" và "không thể bị tắt".
8. **Phát hiện đổi tên tệp**: băm SHA-256 tệp thực thi (cache theo đường dẫn+mtime+kích thước), khớp *tên hoặc băm*. Không bao giờ kết thúc: PID ≤4, chính agent, tiến trình hệ thống thiết yếu (`explorer.exe`, `svchost.exe`, `lsass.exe`…).
9. **DNS**: chặn bằng `0.0.0.0` (A) / `::` (AAAA), khớp cả tên miền gốc lẫn subdomain; SafeSearch bằng CNAME (`forcesafesearch.google.com`, `strict.bing.
com`, `restrictmoderate.youtube.com`, `safe.duckduckgo.com`); truy vấn hợp lệ chuyển *nguyên byte* tới upstream, có dự phòng TCP khi bị cắt. Chặn máy chủ DoH và đặt chính sách `DnsOverHttpsMode=off` (công khai, xem ở `chrome://policy`) cho Chrome/Edge.

## 4. Smart Fail-Safe
| Tình huống | Nhóm | Hành vi |
|---|---|---|
| Mất mạng ≤ N ngày (mặc định 7) | tất cả | Dùng bản luật đã ký, lưu cục bộ; hạn mức, lịch, ngân sách vẫn áp dụng |
| Mất mạng > N ngày | Giáo dục, miễn chặn (trường, 111, y tế) | **Fail-open**: luôn cho phép |
| Mất mạng > N ngày | Giải trí, game, mạng xã hội, không phù hợp | **Fail-closed**: chặn, hộp thoại giải thích "chưa kết nối quá N ngày" |
| Mất mạng > N ngày | Chưa phân loại | Theo bản luật đã lưu |

*Lý do*: nếu fail-open toàn bộ, ngắt Wi-Fi của router 1 tuần là cách né luật dễ nhất; nếu fail-closed toàn bộ, trẻ mất cổng học tập và đường dây cứu hộ do lỗi hạ tầng, gây hại hơn cả điều ta muốn ngăn. N cấu hình được ở dashboard.

## 5. Mô hình mối đe dọa
| Mối đe dọa | Biện pháp | Còn lại |
|---|---|---|
| Trẻ chỉnh đồng hồ | Giờ monotonic + neo máy chủ + mốc đã lưu | Chỉnh *tiến* khi offline và khởi động lại có thể đổi "ngày"; đồng bộ lại sẽ sửa |
| Đổi tên file | SHA-256 | Biên dịch lại / sửa file đổi băm → dùng thêm luật theo tên |
| Dừng/sửa service | Chạy LocalSystem, tự khởi động lại, ACL | Trẻ có quyền Administrator thì vô hiệu mọi giải pháp; cần tài khoản trẻ là User thường |
| Giả mạo máy chủ (MITM) | TLS 1.2+ + ghim chứng chỉ; chính sách ký HMAC, chống phát lại bằng `version` | — |
| Phát lại/sửa chính sách | HMAC theo thiết bị, `device_id` trong chính sách, từ chối version cũ | — |
| Vượt DNS (DoH, DNS cứng, VPN, proxy) | Chặn endpoint DoH, chính sách trình duyệt, chặn theo tiến trình | Ứng dụng tự mang DNS/VPN vẫn né được DNS; ngăn bằng luật tiến trình |
| IDOR / CSRF / brute-force | Lọc sở hữu ở tầng truy vấn, token CSRF mỗi phiên, khóa tài khoản 5 lần/15 phút, giới hạn IP, Argon2id | — |
| Rò dữ liệu | AES-GCM trường nhạy cảm, bí mật không nằm trong mã, CSP chặt, cookie HttpOnly/SameSite | — |

## 6. Cài đặt và ghép đôi thân thiện (installer/)
Mục tiêu: người không rành kỹ thuật cài được trong 5 phút, **không cần gõ lệnh, không cần biết chứng chỉ/IP/mã ghim**.

- **Một tệp**: `OGK-Setup.exe` (PyInstaller `--onefile --uac-admin`) nhúng sẵn `ogk.exe` (dịch vụ + khay + máy chủ + công cụ). Mọi chương trình đóng gói đi qua một điểm vào `scripts/launch_ogk.py` (`agent-service`, `server-service`, `tray`, `uninstall`, `agent …`).
- **Hai chế độ**: máy bố mẹ (máy chủ + bảng điều khiển), máy con (agent). Máy con có thể dùng `monitored_users` (mục “Nâng cao”) để chỉ tính giờ/chặn tài khoản Windows của con.
- **Mã cài đặt 16 ký tự** `ABCD-2345-XXXX-XXXX` = mã ghép đôi 8 ký tự (đúng như API) + 8 ký tự là 40 bit đầu của SHA-256 chứng chỉ máy chủ. Máy con *tự lấy* chứng chỉ từ địa chỉ đã tìm thấy, đối chiếu với 8 ký tự cuối rồi mới ghim và gửi mã ghép đôi. Kẻ giả mạo phải tạo chứng chỉ trùng 40 bit (~10¹² lần thử) trong 10 phút hiệu lực nên không khả thi, và mã ghép đôi không bao giờ gửi cho máy chưa được xác thực.
- **Tự tìm máy chủ**: máy chủ trả lời gói UDP broadcast `OGK-DISCOVER/1` ở cổng 48600 bằng `{tên máy, url}` (không có bí mật). Người dùng ch
ỉ cần nhập mã. Có “Nâng cao” để nhập địa chỉ tay.
- **Tự động hoá**: sinh chứng chỉ (một lần, giữ nguyên để mã ghim không đổi), tin cậy chứng chỉ trên máy bố mẹ (`certutil`), mở tường lửa chỉ ở profile *Private* (TCP 8443, UDP 48600), đăng ký dịch vụ tự khởi động + tự khởi động lại, lối tắt, mục trong *Apps & features*, chép bộ cài vào `downloads/` để phụ huynh tải về máy con từ bảng điều khiển.
- **An toàn khi lỗi**: kiểm tra quyền Admin và xác thực máy chủ *trước* khi đụng vào hệ thống; lỗi giữa chừng hoàn tác (xóa dịch vụ + thư mục vừa chép); mọi thông báo lỗi là tiếng Việt kèm cách xử lý.
- **Kiểm thử**: `System` là lớp thao tác hệ thống; test dùng `FakeSystem` (ghi lại `sc/netsh/certutil/registry`, và `sc start OpenGuardKidsServer` khởi động server thật trong luồng) nên toàn bộ quy trình cài, ghép đôi, hoàn tác, gỡ được chạy end-to-end. Giao diện tkinter được điều khiển và chụp ảnh dưới Xvfb. Lưu ý kỹ thuật: biến Tk phải được giữ tham chiếu, nếu bị thu gom rác ở luồng nền Tcl sẽ `abort()` cả tiến trình.

