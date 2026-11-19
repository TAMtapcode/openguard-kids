```markdown
# Quyền riêng tư của Open Guardian Kids

OGK là công cụ **đồng hành**, không phải công cụ theo dõi. Tài liệu này là cam kết bằng văn bản và cũng là bảng kiểm tra cho mọi tính năng mới: nếu một tính năng cần thu thập thứ không nằm trong bảng "Có thu thập" thì không được thêm.

## Có thu thập (và vì sao)

| Dữ liệu | Mục đích | Lưu ở đâu | Thời hạn | Ai xem được |
|---|---|---|---|---|
| Tổng thời lượng dùng máy mỗi ngày | Cưỡng chế hạn mức, báo cáo 7 ngày | Máy con + máy chủ | 90 ngày | Phụ huynh, **và con** |
| Tên tiến trình đang ở tiền cảnh + thời lượng (vd `winword.exe`, 40 phút) | Ngân sách theo nhóm, Top 10 ứng dụng | Máy con + máy chủ | 90 ngày | Phụ huynh, **và con** |
| **Tên miền cấp hai** + thời lượng ước lượng (vd `khanacademy.org`) | Lọc web, Top 10 tên miền | Máy con + máy chủ | 90 ngày | Phụ huynh, **và con** |
| Sự kiện bị chặn / cảnh báo / khóa / yêu cầu của con (chỉ loại + tên mục) | Giải thích vì sao bị chặn, hộp thư yêu cầu | Máy con + máy chủ | 90 ngày | Phụ huynh, **và con** |
| Nhật ký đổi luật (ai, lúc nào, IP nào, giá trị cũ/mới) | Minh bạch, chống tranh cãi | Máy chủ | 365 ngày | Phụ huynh, **và con** |
| Nội dung tin nhắn con gửi trong "Xin thêm giờ" | Để phụ huynh đọc | Máy chủ, **mã hóa AES-GCM** | 90 ngày | Phụ huynh |
| Băm (SHA-256) dấu vân tay thiết bị | Nhận diện máy đã ghép đôi | Máy chủ | Đến khi gỡ máy | Hệ thống |

Lược đồ sự kiện duy nhất được máy chủ chấp nhận: `app_start`, `app_stop`, `domain_query`, `blocked_app`, `blocked_domain`, `quota_warning`, `locked`, `unlock_request`, `override_granted`. Máy chủ **từ chối** mọi sự kiện có loại khác hoặc có `subject` chứa `/ \ ? # @ = &` hay `://` (tức là URL đầy đủ).

## Không bao giờ thu thập

- Ảnh chụp màn hình, video màn hình, nội dung cửa sổ.
- Phím gõ (không có keylogger), nội dung clipboard.
- Nội dung tin nhắn, email, cuộc trò chuyện, mật khẩu.
- URL đầy đủ, đường dẫn, tham số tìm kiếm, từ khóa tìm kiếm (chỉ tên miền cấp hai).
- **Tiêu đề cửa sổ** (chỉ tên tiến trình).
- Vị trí, camera, micro, danh bạ, tệp cá nhân.

## Minh bạch với con

- Biểu tượng khay **luôn hiển thị**, không có chế độ ẩn. Nếu không hiển thị được biểu tượng, agent tự dừng chặn và báo phụ huynh (NT1).
- Mỗi lần chặn đều có hộp thoại nêu **mục bị chặn, nhóm, ai đặt luật** (NT4).
- Con xem được đúng báo cáo của phụ huynh và nhật ký đổi luật, trên máy (tab "Nhật ký luật") và trên web (tài khoản xem) (NT5).
- Con có thể xin thêm giờ, báo chặn nhầm, và tự đổi giờ học lấy giờ chơi (NT3).
- Danh mục **miễn chặn bất biến**: cổng trường học (`*.edu.vn`), `*.gov.vn`, nền tảng học trực tuyến quốc gia, y tế/cứu hộ và **Tổng đài quốc gia bảo vệ trẻ em 111**. Không chính sách nào ghi đè được; cũng không bao giờ bị chặn khi mất kết nối (NT6).

## Đồng ý, truy cập, xóa

- **Đồng ý**: phụ huynh phải tích xác nhận trước khi tạo mã ghép đôi; thời điểm đồng ý được ghi cùng thiết bị. Trình cài đặt (OGK-Setup) yêu cầu tích xác nhận trước khi cài trên máy của con. Đối với trẻ em, cần có sự đồng ý của cha mẹ/người giám hộ theo quy định về bảo vệ dữ liệu cá nhân hiện hành (ví dụ Nghị định 13/2023/NĐ-CP). *Đây là mô tả kỹ thuật, không phải tư vấn pháp lý.*
- **Tự động xóa** dữ liệu hoạ
t động sau 90 ngày (tác vụ nền 6 giờ/lần).
- **Xóa ngay**: nút "Xóa toàn bộ dữ liệu của con tôi" xóa sự kiện, thống kê, yêu cầu trên máy chủ và gửi lệnh xóa bộ nhớ đệm trên máy con. Riêng bộ đếm *tổng thời gian hôm nay* được giữ đến hết ngày để hạn mức không bị đặt lại.
- **Gỡ cài đặt công khai**: Settings → Apps → Open Guardian Kids (cần quyền quản trị) trả lại DNS gốc, gỡ chính sách trình duyệt và xóa dữ liệu (hoặc giữ lại nếu bạn chọn). Phụ huynh đọc phiên bản dễ hiểu ngay trên bảng điều khiển tại `/privacy`.

## Bảo vệ dữ liệu

TLS 1.2+ và ghim chứng chỉ giữa Agent và máy chủ; mật khẩu băm Argon2id; bí mật thiết bị mã hóa AES-GCM trong CSDL và bảo vệ bằng DPAPI trên máy con; chính sách ký HMAC-SHA256; khóa không nằm trong mã nguồn (biến môi trường `OGK_MASTER_KEY` hoặc tệp khóa quyền 600).

```
