# Open Guardian Kids (OGK)

Hệ thống **đồng hành và quản lý thời gian dùng máy tính của trẻ em**: minh bạch tuyệt đối, không theo dõi lén. Chỉ ghi *thời lượng, tên ứng dụng, tên miền*; không chụp màn hình, không keylogger, không đọc tin nhắn. Xem [PRIVACY.md](PRIVACY.md).

| Phân hệ | Thư mục | Công nghệ |
|---|---|---|
| Máy con: Service nền (LocalSystem) + Tray UI (User) | `agent/` | Python, psutil, dnslib, pywin32, tkinter + pystray |
| Máy chủ trung tâm + WebSocket lệnh khẩn | `server/` | FastAPI, SQLAlchemy, SQLite WAL (hoặc PostgreSQL) |
| Bảng điều khiển phụ huynh + trang xem của trẻ | `web/` | React (Vite), responsive; chạy ngay trên máy bố mẹ hoặc deploy lên Netlify |
| Dùng chung (lược đồ, chữ ký HMAC) | `common/` | |
| Bộ phân loại ≥200 tên miền, 6 nhóm + bộ dữ liệu kiểm thử | `data/` | CSV |

## 🧑‍👩‍👧 Dành cho phụ huynh: cài đặt trong 5 phút, không cần gõ lệnh

1. Mở **`OGK-Setup.exe`** trên máy của bố mẹ → chọn **“Máy của bố mẹ”** → *Cài đặt*.
2. Mở “Bảng điều khiển OGK” → tạo tài khoản → thêm con → bấm **“Tạo mã cài đặt”** (mã dạng `ABCD-2345-XXXX-XXXX`).
3. Mở `OGK-Setup.exe` trên máy của con → chọn **“Máy của con”** → dán mã → *Cài đặt*.

Hướng dẫn đầy đủ kèm xử lý sự cố: **[HUONG_DAN_CAI_DAT.md](HUONG_DAN_CAI_DAT.md)**.

Vì sao dễ: máy con **tự tìm** máy bố mẹ trong mạng; chứng chỉ bảo mật, tường lửa (chỉ mạng nhà), dịch vụ, lối tắt đều **tự động**; mã cài đặt đã **kèm xác thực chứng chỉ** (không phải sao chép mã ghim 64 ký tự); gỡ cài đặt ở **Settings → Apps** như phần mềm bình thường.

### Lấy `OGK-Setup.exe` ở đâu?
Đây là tệp do lập trình viên build một lần, rồi gửi cho các gia đình:
- **Không cần cài gì:** đẩy mã lên GitHub → tab **Actions → “Build Windows installer” → Run workflow** → tải `OGK-Setup` ở mục *Artifacts* (hoặc ở *Releases* nếu gắn tag `v1.0.0`).
- **Hoặc build ở máy Windows:** `.\scripts\build_windows.ps1` → `dist\OGK-Setup.exe`.

> Tệp chưa có chữ ký số nên Windows SmartScreen sẽ hỏi một lần (More info → Run anyway). Muốn bỏ cảnh báo này khi phát hành rộng cần mua chứng chỉ ký mã (code signing).

## 👩‍💻 Dành cho lập trình viên

```bash
pip install -r requirements-dev.txt
python -m server --data-dir ./ogk-data            # HTTPS :8443, tự tạo chứng chỉ + khóa + CSDL, tự được máy con tìm thấy
python scripts/seed_demo.py --server https://127.0.0.1:8443   # (tuỳ chọn) dữ liệu mẫu
```
Mở https://127.0.0.1:8443/register, tạo con, “Tạo mã cài đặt”, rồi trên “máy con” (cùng máy cũng được):
```bash
python -m agent enroll --server https://127.0.0.1:8443 --pin <SHA-256 chứng chỉ> --code ABCD2345 --home ./agent-home
python -m agent run --dev --home ./agent-home      # DNS proxy cổng 5353, không đổi DNS hệ thống
```
(Hoặc `python -m installer --silent --mode child --code ABCD-2345-XXXX-XXXX --accept` trên Windows để chạy đúng đường cài đặt thật.) Chạy thử nhanh không TLS: `python -m server --insecure-http --port 8000`.

**Cài im lặng cho IT**: `OGK-Setup.exe --silent --mode parent` · `--mode child --code <mã> [--server <ip>] [--users bin,an] --accept` · `--mode uninstall [--keep-data]`.

**Cấu trúc**: `installer/` (wizard Tk, lõi cài đặt, ghép đôi), `agent/` (service + tray), `server/`, `common/`, `scripts/launch_ogk.py` (điểm vào duy nhất của `ogk.exe`), `scripts/build_windows.ps1`, `.github/workflows/`.

## Kiểm thử

```bash
xvfb-run -a pytest           # ~110 test, ~1 phút (bỏ `xvfb-run -a` trên Windows/Mac: test giao diện cài đặt sẽ tự bỏ qua nếu không có màn hình)
pytest tests/test_e2e_demos.py -v -s   # D1–D5 end-to-end
```
Xem [docs/DEMO.md](docs/DEMO.md) cho kịch bản D1–D5 và [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) cho giao thức, quyết định thiết kế, mô hình mối đe dọa.

## Giới hạn đã biết (đọc trước khi demo)

1. **Phần chỉ chạy trên Windows chưa được chạy thử thật.** Môi trường phát triển là Linux. Logic lõi (luật, đồng hồ, DNS, tiến trình, đồng bộ, WebSocket, ghép đôi, tìm máy chủ trong LAN, toàn bộ quy trình cài/gỡ) được kiểm thử tự động; giao diện cài đặt được chạy bằng tkinter thật dưới Xvfb. Còn các phần sau **chưa được thực thi trên Windows**: `agent/winapi.py` (`WindowsPlatform`: idle, khóa phiên, DPAPI, đổi DNS, ACL), `agent/service.py` và `server/service.py`, `agent/tray_ui.py`, và các lệnh hệ thống mà trình cài đặt gọi (`sc.exe`, `netsh`, `certutil`, registry, UAC). Bản build PyInstaller (`scripts/build_windows.ps1`) cũng chưa được chạy; có thể cần bổ sung `--hidden-import` nếu thiếu module. Cách font/emoji hiển thị trên Windows có thể khác ảnh chụp trên Linux. Hãy chạy danh sách kiểm tra dưới đây trên máy Windows thật.
2. Bộ phân loại là **bộ khởi đầu** (224 mục, tên miền công khai). Cần rà soát/bổ sung; nhóm "không phù hợp lứa tuổi" hiện chỉ có cờ bạc/hẹn hò/chat ẩn danh, không có nội dung người lớn. Hãy nạp thêm từ danh sách chặn có uy tín.
3. Thời lượng theo **tên miền là ước lượng** (xem ARCHITECTURE §3.6). Thời lượng theo ứng dụng chính xác hơn.
4. Chặn web dựa trên DNS: ứng dụng tự mang DNS/DoH/VPN vẫn né được; DoH của Firefox chưa có chính sách đi kèm. Bù bằng luật chặn tiến trình.
5. Trẻ có quyền Administrator thì vô hiệu mọi biện pháp (dừng dịch vụ, đổi DNS). Cần tài khoản User thường.
6. Chỉ hỗ trợ IPv4 cho DNS proxy; TLS tối thiểu theo mặc định của Python/OpenSSL trên máy chủ (cần ≥ 1.2, kiểm tra bằng `openssl s_client`).
7. Không có đăng nhập 2 bước (TOTP) và **chưa có “quên mật khẩu”** cho phụ huynh (có khóa tài khoản, giới hạn IP, Argon2id, CSRF). Phụ huynh quên mật khẩu hiện phải nhờ người có kỹ thuật đặt lại trực tiếp trong CSDL.
8. Chứng chỉ của máy chủ do chính máy bố mẹ tạo nên trình duyệt trên điện thoại sẽ cảnh báo một lần. Muốn hết cảnh báo cần tên miền thật và chứng chỉ công khai, ngoài phạm vi bản này.
9. Máy chủ chạy trên máy bố mẹ: tắt máy bố mẹ thì không xem/ra lệnh được (máy con vẫn áp dụng luật đã lưu). Chưa hỗ trợ dùng ngoài mạng nhà.

### Danh sách kiểm tra trên Windows thật
- [ ] Build `OGK-Setup.exe` thành công (GitHub Actions hoặc `build_windows.ps1`); mở ra thấy UAC rồi trình cài đặt; SmartScreen chỉ hỏi một lần.
- [ ] **Máy bố mẹ**: “Cài đặt” chạy hết; dịch vụ `OpenGuardKidsServer` Running; lối tắt “Bảng điều khiển OGK” mở được **không cảnh báo chứng chỉ**; điện thoại cùng Wi-Fi mở được địa chỉ LAN (sau cảnh báo một lần).
- [ ] **Máy con**: dán mã → tìm thấy máy bố mẹ tự động → cài xong; dịch vụ `OpenGuardKids` Running; đăng nhập tài khoản con thấy biểu tượng lá xanh; “Open Guardian Kids” có trong Settings → Apps.
- [ ] **Một máy dùng chung**: chỉ tài khoản Windows của con bị tính giờ/khóa; tài khoản bố mẹ không bị ảnh hưởng, kể cả khi con vừa bị khóa.
- [ ] `nslookup tiktok.com` trả `0.0.0.0` khi nhóm mạng xã hội bị chặn; hết hạn mức → popup đếm ngược 60 giây → màn hình khóa toàn màn hình (vẫn có nút Đăng xuất/Tắt máy, tự mở khi được cộng giờ).
- [ ] Tắt `ogk.exe` (tray) trong Task Manager → service mở lại khay trong ≤10 giây; kill service bằng tài khoản thường bị từ chối, bằng Admin thì tự khởi động lại sau 5 giây.
- [ ] Gỡ ở Settings → Apps: dịch vụ biến mất, DNS về như cũ (`Get-DnsClientServerAddress`), lối tắt và tường lửa được xóa.


## Giao diện web (React) và Netlify

- Phát triển: `cd web && npm ci && npm run dev` (chuyển `/api` tới máy chủ OGK, mặc định `https://127.0.0.1:8443`, đổi bằng biến `OGK_API`).
- Build: `npm run build` → `web/dist`. Máy chủ FastAPI tự phục vụ thư mục này, bộ cài Windows nhúng sẵn.
- Netlify: kết nối repo, Netlify đọc `netlify.toml` (base `web`, publish `dist`, `VITE_REMOTE=1`). Lần đầu mở trang, bố mẹ nhập địa chỉ máy bố mẹ
  (ví dụ `192.168.1.10`), mở địa chỉ đó một lần để chấp nhận chứng chỉ, rồi đăng nhập. Trang Netlify gọi API bằng Bearer token, không dùng cookie khác nguồn;
  máy chủ chỉ cho phép CORS từ `*.netlify.app` (đổi bằng biến môi trường `OGK_CORS_ORIGINS`, là một regex). Dữ liệu vẫn nằm trên máy bố mẹ,
  nên trình duyệt phải ở cùng mạng với máy đó.
