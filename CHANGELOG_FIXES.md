# Các lỗi đã sửa (đợt rà soát này)

1. `agent/dns_proxy.py`: `forward()` bỏ qua **mọi** upstream loopback (kể cả `127.0.0.53`, resolver cục bộ khác cổng) -> nay chỉ bỏ qua chính proxy (cùng cổng) để tránh vòng lặp.
2. `agent/config.py`: docstring chứa `\O` -> `SyntaxWarning` (sẽ thành lỗi ở Python mới); đã chuyển thành raw string.
3. `server/tray_icon.py`: `import pystray` đầu file làm sập khi import trên máy không có GUI/tray; nay import muộn trong `run_with_tray_icon`.
4. `server/routers/agent_api.py`: mã ghép đôi nay được "chiếm" nguyên tử (UPDATE ... WHERE used=0) chống 2 thiết bị cùng dùng 1 mã; so sánh refresh token bằng `hmac.compare_digest`; WebSocket không còn sập khi nhận tin nhắn sai định dạng.
5. `server/routers/web.py`: đăng ký trùng tài khoản đồng thời không còn lỗi 500; thông báo khóa tài khoản ghi đúng số phút cấu hình.
6. `tests/test_agent_core.py`: test DNS không còn phụ thuộc Internet thật (`fallback_upstreams=()`).
7. Dọn import thừa `time` trong `server/services.py`.

Kết quả: 121 passed, 1 skipped.
