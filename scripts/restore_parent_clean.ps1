<# Khôi phục sạch mạng & hệ thống trên máy bố mẹ (chạy PowerShell với quyền Administrator) #>
Write-Host "Dang dung dich vu Agent (neu co)..." -ForegroundColor Yellow
sc.exe stop OpenGuardKids | Out-Null
sc.exe delete OpenGuardKids | Out-Null

Write-Host "Dang khoi phuc DNS ve tu dong (DHCP)..." -ForegroundColor Cyan
Get-NetAdapter | ForEach-Object {
    Set-DnsClientServerAddress -InterfaceIndex $_.ifIndex -ResetServerAddresses -ErrorAction SilentlyContinue
}
ipconfig /flushdns | Out-Null

Write-Host "Dang xoa cac luat tuong lua chan DNS cua OGK..." -ForegroundColor Cyan
Get-NetFirewallRule | Where-Object {
    $_.DisplayName -like "*OGK Block*" -or $_.Name -like "*OGK Block*" -or $_.Name -eq "OGK Allow Upstream DNS"
} | Remove-NetFirewallRule -ErrorAction SilentlyContinue

Write-Host "Dang go chinh sach DoH & mo khoa Task Manager..." -ForegroundColor Cyan
Remove-ItemProperty -Path "HKLM:\SOFTWARE\Policies\Google\Chrome" -Name "DnsOverHttpsMode" -ErrorAction SilentlyContinue
Remove-ItemProperty -Path "HKLM:\SOFTWARE\Policies\Microsoft\Edge" -Name "DnsOverHttpsMode" -ErrorAction SilentlyContinue
Remove-ItemProperty -Path "HKLM:\SOFTWARE\Policies\BraveSoftware\Brave" -Name "DnsOverHttpsMode" -ErrorAction SilentlyContinue
Remove-Item -Path "HKLM:\SOFTWARE\Policies\Mozilla\Firefox\DNSOverHTTPS" -Recurse -ErrorAction SilentlyContinue

Get-ChildItem Registry::HKEY_USERS | ForEach-Object {
    $p = "$($_.PSPath)\Software\Microsoft\Windows\CurrentVersion\Policies\System"
    if (Test-Path $p) {
        Remove-ItemProperty -Path $p -Name "DisableTaskMgr" -ErrorAction SilentlyContinue
    }
}

Write-Host "[OK] May bo me da duoc khoi phuc mang & he thong sach se!" -ForegroundColor Green
