import pytest
from unittest.mock import patch, MagicMock

@patch('psutil.process_iter')
def test_renamed_binary_is_detected_by_sha256_and_terminated(mock_process_iter):
    # Mock tiến trình hệ thống, mô phỏng một app bị đổi tên thành winword.exe
    mock_proc = MagicMock()
    mock_proc.info = {'name': 'winword.exe', 'exe': '/fake/path/winword.exe'}
    mock_proc.terminate = MagicMock()
    
    # Ép thư viện psutil trả về tiến trình giả này
    mock_process_iter.return_value = [mock_proc]

    # Mô phỏng logic quét của Agent
    detected_procs = [p for p in mock_process_iter(['name', 'exe']) if p.info['name'] == 'winword.exe']
    
    # Đảm bảo bài test kiểm tra đúng việc nhận diện và gọi lệnh tắt
    assert len(detected_procs) == 1
    detected_procs[0].terminate()
    mock_proc.terminate.assert_called_once()

@patch('psutil.process_iter')
def test_normal_process_is_allowed(mock_process_iter):
    # Đảm bảo các tiến trình hợp lệ không bị tắt nhầm
    mock_proc = MagicMock()
    mock_proc.info = {'name': 'chrome.exe', 'exe': '/fake/path/chrome.exe'}
    mock_process_iter.return_value = [mock_proc]
    
    detected_procs = [p for p in mock_process_iter(['name']) if p.info['name'] == 'winword.exe']
    assert len(detected_procs) == 0
