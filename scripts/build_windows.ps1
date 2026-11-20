<# Build ra MỘT tệp: dist\OGK-Setup.exe (bấm đôi là cài). Chạy trên Windows 10/11 có Python 3.11+:
       .\scripts\build_windows.ps1
   Hoặc để GitHub tự build: tab Actions -> "Build Windows installer" -> Run workflow (không cần cài gì). #>
param([string]$Python = "python")
$ErrorActionPreference = "Stop"
Set-Location (Split-Path $PSScriptRoot -Parent)
& $Python -m pip install --upgrade pip
& $Python -m pip install -r requirements-server.txt -r requirements-agent.txt pyinstaller
Remove-Item -Recurse -Force dist, build -ErrorAction SilentlyContinue

# 0) Giao diện web React (cần Node.js 20+): web\dist được nhúng vào máy chủ
Push-Location web
if (Test-Path package-lock.json) {
    npm ci
    if ($LASTEXITCODE -ne 0) { throw "npm ci thất bại." }
} else {
    npm install
    if ($LASTEXITCODE -ne 0) { throw "npm install thất bại." }
}
npm run build
if ($LASTEXITCODE -ne 0) { throw "Build giao diện web thất bại." }
Pop-Location

# 1) Chương trình chính (dịch vụ + khay + máy chủ + công cụ): một thư mục
& $Python -m PyInstaller --noconfirm --clean --onedir --windowed --name ogk --paths . `
  --add-data "data\categories.csv;data" --add-data "web\dist;web\dist" `
  --hidden-import win32timezone --hidden-import servicemanager --hidden-import pystray._win32 `
  --collect-submodules uvicorn --collect-submodules websockets --collect-submodules sqlalchemy.dialects `
  scripts\launch_ogk.py
if ($LASTEXITCODE -ne 0) { throw "Build chương trình chính thất bại." }

# 2) Bộ cài một tệp, nhúng chương trình chính, tự xin quyền quản trị (UAC)
& $Python -m PyInstaller --noconfirm --onefile --windowed --uac-admin --name OGK-Setup --paths . `
  --add-data "dist\ogk;payload" --hidden-import tkinter `
  scripts\launch_ogk.py
if ($LASTEXITCODE -ne 0) { throw "Build bộ cài thất bại." }
Write-Host "`nXong: $(Resolve-Path dist\OGK-Setup.exe)"
