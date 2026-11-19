<# Bật/tắt nhanh khóa Task Manager & dịch vụ OGK khi đang test (chạy PowerShell bằng quyền Administrator)
   Ví dụ:
     .\scripts\dev_toggle_taskmgr.ps1 -Unlock   # Mở Task Manager & tạm dừng dịch vụ để test
     .\scripts\dev_toggle_taskmgr.ps1 -Lock     # Bật lại dịch vụ & khóa Task Manager cho tài khoản con
#>
param(
    [switch]$Unlock,
    [switch]$Lock
)

if ($Unlock) {
    sc.exe stop OpenGuardKids | Out-Null
    taskkill /F /IM ogk.exe 2>$null | Out-Null
    Get-ChildItem Registry::HKEY_USERS | ForEach-Object {
        $p = "$($_.PSPath)\Software\Microsoft\Windows\CurrentVersion\Policies\System"
        if (Test-Path $p) {
            Remove-ItemProperty -Path $p -Name "DisableTaskMgr" -ErrorAction SilentlyContinue
        }
    }
    Write-Host "[OK] Da tam dung OpenGuardKids va mo khoa Task Manager de test." -ForegroundColor Green
} elseif ($Lock) {
    sc.exe start OpenGuardKids | Out-Null
    Write-Host "[OK] Da bat lai dich vu OpenGuardKids (tu dong khoa Task Manager voi tai khoan con)." -ForegroundColor Cyan
} else {
    Write-Host "Dung tham so -Unlock hoac -Lock."
}
