import os, sys, winreg, ctypes
try:
    import winshell
    from win32com.client import Dispatch
except ImportError:
    pass

def clean_uninstall():
    # 1. Xóa Registry Autorun
    try:
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Run", 0, winreg.KEY_SET_VALUE)
        winreg.DeleteValue(key, "OpenGuardKids")
        winreg.CloseKey(key)
    except Exception:
        pass
    try:
        key = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"Software\Microsoft\Windows\CurrentVersion\Run", 0, winreg.KEY_SET_VALUE)
        winreg.DeleteValue(key, "OpenGuardKidsTray")
        winreg.CloseKey(key)
    except Exception:
        pass

    # 2. Xóa Shortcut Desktop
    try:
        desktop = winshell.desktop()
        shortcut_path = os.path.join(desktop, "Open Guardian Kids.lnk")
        if os.path.exists(shortcut_path):
            os.remove(shortcut_path)
    except Exception:
        pass

    # 3. Gỡ Windows Service
    os.system("sc stop OpenGuardKids")
    os.system("sc delete OpenGuardKids")

    # 4. Tự diệt tiến trình
    ctypes.windll.user32.MessageBoxW(0, "Đã xóa toàn bộ dữ liệu và gỡ cài đặt Open Guardian Kids thành công!", "Tự hủy", 0x40)
    os.system("taskkill /F /IM pythonw.exe /T")

if __name__ == '__main__':
    clean_uninstall()
