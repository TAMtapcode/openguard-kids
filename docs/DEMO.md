# Kịch bản nghiệm thu D1–D5

Mỗi kịch bản có **test tự động** (chạy server thật + Agent thật, đồng hồ giả để không phải đợi) và **hướng dẫn thủ công** trên máy Windows.

| # | Yêu cầu | Test tự động | Kết quả đo được |
|---|---|---|---|
| D1 | Hạn mức 3 phút → popup hết giờ → tự khóa | `test_D1_quota_3_minutes_warns_then_popup_then_auto_lock` | Cảnh báo "còn 1 phút" → hết 180s: popup + ân hạn → 240s: khóa |
| D2 | Đổi tên `game.exe` thành `winword.exe` vẫn bị kết thúc | `test_D2_renamed_exe_detected_by_sha256…` (tiến trình thật) | Bị kết thúc trong 1 nhịp quét, popup nêu "Phụ huynh đã chặn" |
| D3 | Lùi đồng hồ Windows 2 giờ | `test_D3_windows_clock_back_2h…` | Thời gian dùng vẫn đúng ±1s, có sự kiện `clock_tamper:-7200s` |
| D4 | Rút mạng → vẫn chặn → cắm lại không trùng lặp | `test_D4_offline_rules_still_enforced…` | DNS chặn offline, khóa offline, đồng bộ lại 0 bản ghi trùng |
| D5 | Nút khóa trên web → máy con khóa < 5s | `test_D5_parent_lock_button…` | ~0,01 giây qua WebSocket (cùng máy); ngưỡng đạt: < 5 giây |

```
pytest tests/test_e2e_demos.py -v -s
```

## Thủ công trên Windows
1. **D1**: Dashboard → *Luật & lịch* → Ngày thường = 3 phút, bật lịch "Mở cả tuần" → Lưu. Dùng máy 3 phút liên tục (đừng để idle quá 5 phút) → thấy popup "Hết giờ rồi!" đếm ngược 60 giây rồi máy về màn hình đăng nhập.
2. **D2**: Lấy SHA-256 của một ứng dụng thử (chọn tệp ngay trên dashboard, mục *Ứng dụng*; hoặc `python -m agent hash C:\...\notepad.exe`) → thêm vào "Ứng dụng chặn" → Lưu. Sao chép `notepad.exe` thành `winword.exe` ở thư mục khác và chạy → cửa sổ đóng ngay, hiện hộp thoại giải thích.
3. **D3**: Settings → Time & Language → tắt "Set time automatically" → lùi 2 giờ. Hạn mức còn lại vẫn giảm đều; popup "Đồng hồ máy bị chỉnh lệch" hiện; dashboard → Nhật ký/Báo cáo có sự kiện.
4. **D4**: Rút cáp mạng. Mở `tiktok.com` → vẫn bị chặn. Cắm lại → trong ≤60s trạng thái trên dashboard về "Đang bật", báo cáo không trùng.
5. **D5**: Bấm "Khóa máy ngay" trên dashboard (điện thoại) và bấm đồng hồ → máy khóa trong vài giây.
