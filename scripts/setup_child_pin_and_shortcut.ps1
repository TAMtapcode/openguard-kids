<# Đặt hoặc đổi mã PIN tắt nhanh của phụ huynh & tạo shortcut "Bật Open Guardian Kids" trên Desktop máy con.
   Chạy PowerShell bằng quyền Administrator trên máy con:
     .\scripts\setup_child_pin_and_shortcut.ps1 -Pin "123456"
#>
param(
    [Parameter(Mandatory=$true)]
    [string]$Pin
)

$ErrorActionPreference = "Stop"
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$env:PYTHONUTF8 = "1"

$exe = "C:\Program Files\OpenGuardKids\ogk.exe"
if (-not (Test-Path $exe)) {
    Write-Host "Khong tim thay $exe. Hay cai dat may con truoc." -ForegroundColor Red
    exit 1
}

& $exe set-parent-pin $Pin

$ws = New-Object -ComObject WScript.Shell
$desktop = [Environment]::GetFolderPath("CommonDesktopDirectory")
$lnk = Join-Path $desktop "Bật Open Guardian Kids.lnk"
$sc = $ws.CreateShortcut($lnk)
$sc.TargetPath = $exe
$sc.Arguments = "child-resume"
$sc.WorkingDirectory = "C:\Program Files\OpenGuardKids"
$sc.IconLocation = "$exe,0"
$sc.Description = "Bật lại Open Guardian Kids"
$sc.Save()

Write-Host "[OK] Da dat ma PIN phu huynh va tao shortcut 'Bật Open Guardian Kids' ngoai Desktop!" -ForegroundColor Green
