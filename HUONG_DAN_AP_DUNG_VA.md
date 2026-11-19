# Hướng dẫn áp dụng gói bản vá cho máy bố mẹ (`ogk_V3`)

## Danh sách tệp trong gói bản vá
1. `server/tray_icon.py`: Nâng cấp icon khay trên máy bố mẹ để hiển thị rõ trạng thái server, mở nhanh bảng điều khiển (`https://localhost:8443`), xem thông tin minh bạch và tắt an toàn.
2. `installer/core.py`: Cập nhật `install_server` tự động dừng/xóa dịch vụ Agent cũ (nếu máy từng cài nhầm chế độ máy con) và khôi phục DNS, DoH, Task Manager trước khi khởi động Server.
3. `scripts/restore_parent_clean.ps1`: Script PowerShell (chạy quyền Administrator) giúp khôi phục ngay mạng & hệ thống nếu máy bố mẹ đang bị kẹt DNS từ lần test trước.

## Cách áp dụng
1. Giải nén gói `.zip` này và chép đè thư mục `server/`, `installer/`, `scripts/` vào thư mục gốc dự án `ogk_V3`.
2. Nếu máy bố mẹ hiện đang bị khóa DNS/DoH, mở PowerShell bằng quyền **Administrator** và chạy:
   ```powershell
   Set-ExecutionPolicy -Scope Process Bypass -Force
   .\scripts\restore_parent_clean.ps1
   ```
3. Build lại bộ cài Windows (`.\scriptsuild_windows.ps1`) hoặc chạy trực tiếp server trên máy bố mẹ.
