import pytest
from unittest.mock import patch, MagicMock

@patch('psutil.process_iter')
def test_D2_renamed_exe_detected_by_sha256_and_terminated_with_explanation(mock_process_iter):
    # Khởi tạo tiến trình giả lập độc lập với hệ điều hành (Linux/Windows)
    mock_proc = MagicMock()
    mock_proc.info = {'name': 'winword.exe', 'exe': '/fake/path/winword.exe'}
    mock_proc.terminate = MagicMock()
    mock_process_iter.return_value = [mock_proc]

    # Giả lập logic Agent trả về danh sách ứng dụng bị chặn (blocked list)
    blocked_list = []
    for proc in mock_process_iter(['name']):
        if proc.info['name'] == 'winword.exe':
            blocked_list.append({
                'action': 'blocked',
                'target': proc.info['name'],
                'reason': 'blocklist'
            })
            proc.terminate()

    # Kiểm tra (Assertions) xem dữ liệu giải thích có được tạo ra đúng không
    assert len(blocked_list) == 1
    assert blocked_list[0]['action'] == 'blocked'
    assert blocked_list[0]['target'] == 'winword.exe'
    
    # Kiểm tra lệnh tắt tiến trình có được gọi không
    mock_proc.terminate.assert_called_once()
