# Hướng dẫn cài đặt Open Guardian Kids (dành cho phụ huynh)

Bạn **không cần biết kỹ thuật**. Cả quá trình khoảng 5 phút và chỉ cần tệp **`OGK-Setup.exe`**.

## Trước khi bắt đầu
- Máy của bố mẹ và máy của con **cùng nối một Wi-Fi / mạng dây ở nhà**.
- Bạn có quyền quản trị (Administrator) trên cả hai máy. Windows sẽ hỏi “Bạn có cho phép…?”, hãy bấm **Yes**.
- Tài khoản Windows của con nên là loại **thường (Standard)**, không phải Administrator, để con không tự tắt được ứng dụng.


---

## Bước 1. Cài bảng điều khiển trên máy của bố mẹ
1. Mở **OGK-Setup.exe** → chọn **“Máy của bố mẹ”** → bấm **Cài đặt**.
2. Đợi khoảng một phút. Xong sẽ có nút **“Mở bảng điều khiển”**, và lối tắt **“Bảng điều khiển OGK”** ngoài màn hình nền.

## Bước 2. Tạo tài khoản và hồ sơ của con
1. Bấm **“Mở bảng điều khiển”** → **Đăng ký** (email hoặc tên đăng nhập + mật khẩu từ 10 ký tự).
2. Ở khung **Thêm hồ sơ cho con**, nhập tên và tuổi → **Thêm con**.
3. Bấm **“Luật, báo cáo và yêu cầu”** của con → cuộn tới mục **“Thêm máy tính của con”**.
4. Tích ô đồng ý → bấm **“Tạo mã cài đặt”** → bấm **“Sao chép”**. Mã có dạng `ABCD-2345-XXXX-XXXX`, dùng một lần, có hiệu lực 10 phút.

## Bước 3. Cài trên máy của con
1. Đưa **OGK-Setup.exe** sang máy của con: tải từ đường dẫn ghi ở bước 1 của mục “Thêm máy tính của con”, hoặc chép bằng USB.
2. Mở **OGK-Setup.exe** → chọn **“Máy của con”**.
3. **Dán mã** vào ô (nếu bạn đã bấm “Sao chép”, mã tự điền sẵn) → tích ô đồng ý → **Cài đặt**.

Xong! Chiếc lá xanh sẽ hiện ở góc phải thanh taskbar khi con đăng nhập. Bạn quản lý mọi thứ từ bảng điều khiển, kể cả trên điện thoại.

---

## Xem bảng điều khiển trên điện thoại
Mở địa chỉ ghi ở màn hình cuối của Bước 1 (dạng `https://192.168.x.x:8443`) bằng trình duyệt điện thoại **đang dùng cùng Wi-Fi**. Lần đầu trình duyệt báo “không bảo mật”. Đó là bình thường vì chứng chỉ do chính máy bạn tạo ra: chọn **Nâng cao → Tiếp tục**. Bạn có thể thêm lối tắt ra màn hình chính của điện thoại.

> Máy bố mẹ cần **bật** thì điện thoại mới xem được và lệnh “Khóa ngay/Cộng giờ” mới đến máy con. Nếu máy bố mẹ tắt, máy của con vẫn áp dụng đúng các luật đã lưu.

## Gỡ cài đặt
Vào **Settings → Apps → Open Guardian Kids → Uninstall** (cần quyền quản trị). Cài đặt mạng của máy được trả lại như ban đầu. Không có cách gỡ “giấu” nào: con cũng biết ứng dụng đang chạy.

---

## Gặp trục trặc?
| Hiện tượng | Cách xử lý |
|---|---|
| Windows báo **“Windows protected your PC”** (SmartScreen) khi mở OGK-Setup.exe | Bấm **More info → Run anyway**. Thông báo này hiện với mọi phần mềm chưa mua chữ ký số. |
| “**Không tìm thấy máy của bố mẹ** trong mạng” | Kiểm tra máy bố mẹ đang bật và cùng Wi-Fi. Trong trình cài đặt bấm **Nâng cao** và nhập địa chỉ máy bố mẹ (xem ở màn hình cuối Bước 1). |
| “Máy tại … **không khớp** với mã cài đặt” | Mã sai hoặc không phải của máy này. Tạo mã mới ở bảng điều khiển và nhập lại. Tránh nhầm chữ O với số 0 (mã không chứa chữ I, O, số 0 và 1). |
| “Mã … **đã hết hạn**” | Mã chỉ dùng một lần trong 10 phút. Bấm **Tạo mã cài đặt** để lấy mã mới. |
|
 Điện thoại không mở được địa chỉ | Chắc chắn điện thoại cùng Wi-Fi (không dùng 4G). Nếu Windows hỏi về tường lửa, hãy cho phép trên mạng **Private**. |
| Trình duyệt luôn cảnh báo “không bảo mật” | Bình thường (xem phần điện thoại). Trên máy bố mẹ thì không còn cảnh báo vì trình cài đặt đã tin cậy chứng chỉ. |
| Máy con báo “Biểu tượng khay không hiển thị” trên bảng điều khiển | Con đăng xuất rồi đăng nhập lại. Nếu vẫn vậy, khởi động lại máy con. |
| Quên mật khẩu bảng điều khiển | Hiện chưa có chức năng tự khôi phục. Hãy nhờ người hỗ trợ kỹ thuật (xem README, mục “Giới hạn đã biết”). |

