# SETUP SMB SHARE - Chay voi quyen Administrator
# Ket noi o Z: toi may chu qua mang LAN (thay the RDP tsclient)
#
# Cach dung:
#   1. Click phai vao file nay
#   2. Chon "Run with PowerShell" (hoac mo PowerShell Admin roi chay)
#
# Luu y: Thay doi IP va mat khau neu can

$ServerIP = "192.168.88.14"
$SharePath = "\\$ServerIP\D"
$DriveLetter = "Z:"
$Username = "$ServerIP\smbuser"
$Password = "159753"

Write-Host "========================================" -ForegroundColor Cyan
Write-Host "  SETUP SMB SHARE - KET NOI O MANG" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan
Write-Host ""

# Xoa credential cu neu co
Write-Host "[1/3] Xoa credential cu..." -ForegroundColor Yellow
cmdkey /delete:$ServerIP 2>$null

# Xoa mapping cu neu co
Write-Host "[2/3] Xoa drive mapping cu..." -ForegroundColor Yellow
net use $DriveLetter /delete /y 2>$null

# Tao mapping moi
Write-Host "[3/3] Tao drive mapping moi..." -ForegroundColor Yellow
$result = net use $DriveLetter $SharePath /user:$Username $Password /persistent:yes 2>&1

if ($LASTEXITCODE -eq 0) {
    Write-Host ""
    Write-Host "========================================" -ForegroundColor Green
    Write-Host "  THANH CONG!" -ForegroundColor Green
    Write-Host "========================================" -ForegroundColor Green
    Write-Host ""
    Write-Host "O $DriveLetter da duoc ket noi toi $SharePath" -ForegroundColor Green
    Write-Host ""

    # Kiem tra thu muc AUTO
    $autoPath = "$DriveLetter\AUTO\ve3-tool-simple\PROJECTS"
    if (Test-Path $autoPath) {
        Write-Host "Thu muc PROJECTS: $autoPath [OK]" -ForegroundColor Green
        Write-Host ""
        Write-Host "Cac project hien co:" -ForegroundColor Cyan
        Get-ChildItem $autoPath -Directory | ForEach-Object { Write-Host "  - $($_.Name)" }
    } else {
        Write-Host "[WARN] Khong tim thay thu muc: $autoPath" -ForegroundColor Yellow
        Write-Host "Kiem tra lai duong dan tren may chu" -ForegroundColor Yellow
    }
} else {
    Write-Host ""
    Write-Host "========================================" -ForegroundColor Red
    Write-Host "  LOI KET NOI!" -ForegroundColor Red
    Write-Host "========================================" -ForegroundColor Red
    Write-Host ""
    Write-Host "Chi tiet loi:" -ForegroundColor Red
    Write-Host $result -ForegroundColor Red
    Write-Host ""
    Write-Host "Kiem tra:" -ForegroundColor Yellow
    Write-Host "  1. May chu $ServerIP co dang chay khong?" -ForegroundColor Yellow
    Write-Host "  2. Thu muc D: da share chua?" -ForegroundColor Yellow
    Write-Host "  3. User smbuser co quyen truy cap khong?" -ForegroundColor Yellow
    Write-Host "  4. Firewall co chan port 445 khong?" -ForegroundColor Yellow
}

Write-Host ""
Write-Host "Nhan phim bat ky de dong..." -ForegroundColor Gray
$null = $Host.UI.RawUI.ReadKey("NoEcho,IncludeKeyDown")
