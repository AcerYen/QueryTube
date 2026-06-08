# 打包 QueryTube 部署檔（上傳至 NAS 用）
# 用法：.\scripts\package-for-nas.ps1

$ErrorActionPreference = "Stop"
$Root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$OutDir = Join-Path $Root "dist"
$ZipPath = Join-Path $OutDir "QueryTube-nas.zip"
$TarPath = Join-Path $OutDir "QueryTube-nas.tar.gz"
$MakeZip = Join-Path $Root "scripts\make-nas-zip.py"

New-Item -ItemType Directory -Force -Path $OutDir | Out-Null
if (Test-Path $ZipPath) { Remove-Item $ZipPath -Force }
if (Test-Path $TarPath) { Remove-Item $TarPath -Force }

$Staging = Join-Path $OutDir "QueryTube-staging"
if (Test-Path $Staging) { Remove-Item $Staging -Recurse -Force }
New-Item -ItemType Directory -Force -Path $Staging | Out-Null

# 不包含 data/、.env：更新部署時不應覆蓋 NAS 上既有用戶與設定
$Include = @(
    ".env.example",
    "docker-compose.yml",
    "Dockerfile",
    "requirements.txt",
    ".dockerignore",
    "app",
    "config",
    "scripts\deploy-nas.sh"
)

foreach ($item in $Include) {
    $src = Join-Path $Root $item
    if (-not (Test-Path $src)) {
        Write-Warning "Skip missing item: $item"
        continue
    }
    $dest = Join-Path $Staging $item
    $destParent = Split-Path $dest -Parent
    if (-not (Test-Path $destParent)) {
        New-Item -ItemType Directory -Force -Path $destParent | Out-Null
    }
    if ((Get-Item $src).PSIsContainer) {
        Copy-Item $src $dest -Recurse -Force
    } else {
        Copy-Item $src $dest -Force
    }
}

Get-ChildItem -Path $Staging -Recurse -Directory -Filter "__pycache__" | Remove-Item -Recurse -Force
Get-ChildItem -Path $Staging -Recurse -File -Filter "*.pyc" | Remove-Item -Force

tar -czf $TarPath -C $Staging .
python $MakeZip $Staging $ZipPath
Remove-Item $Staging -Recurse -Force

Write-Host ""
Write-Host "NAS packages created:" -ForegroundColor Green
Write-Host "  TAR.GZ (recommended): $TarPath"
Write-Host "  ZIP: $ZipPath"
Write-Host ""
Write-Host "On Synology:" -ForegroundColor Cyan
Write-Host "  tar -xzf QueryTube-nas.tar.gz -C QueryTube"
Write-Host ""
Write-Host "Note: package excludes data/ and .env — NAS user data is preserved on update." -ForegroundColor Yellow
