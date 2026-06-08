# 上傳並部署 QueryTube 至 Synology NAS
# 用法：
#   .\scripts\deploy-to-synology.ps1 -NasHost 192.168.1.107 -NasUser Acer
#   .\scripts\deploy-to-synology.ps1 -NasHost nas.local -NasUser admin -NasPath /volume1/docker/QueryTube

param(
    [Parameter(Mandatory = $true)]
    [string]$NasHost,

    [Parameter(Mandatory = $true)]
    [string]$NasUser,

    [string]$NasPath = "/volume1/docker/QueryTube",

    [int]$Port = 22
)

$ErrorActionPreference = "Stop"
$Root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$TarPath = Join-Path $Root "dist\QueryTube-nas.tar.gz"
$PackageScript = Join-Path $Root "scripts\package-for-nas.ps1"

if (-not (Test-Path $TarPath)) {
    Write-Host "找不到部署套件，先打包..." -ForegroundColor Yellow
    & $PackageScript
}

$RemoteTar = "$NasPath/QueryTube-nas.tar.gz"
$SshTarget = "${NasUser}@${NasHost}"

Write-Host "=== 1/5 建立 NAS 目錄 ===" -ForegroundColor Cyan
ssh -p $Port $SshTarget "mkdir -p '$NasPath'"

Write-Host "=== 2/5 上傳部署套件 ===" -ForegroundColor Cyan
scp -P $Port $TarPath "${SshTarget}:${RemoteTar}"

Write-Host "=== 3/5 解壓並設定權限（保留既有 data 與 .env）===" -ForegroundColor Cyan
$RemoteCmd = @"
cd '$NasPath' && \
BACKUP_DIR=`$(mktemp -d) && \
[ -f data/querytube.db ] && cp -a data/querytube.db "`$BACKUP_DIR/" && \
[ -f .env ] && cp -a .env "`$BACKUP_DIR/" && \
tar -xzf QueryTube-nas.tar.gz && \
rm -f QueryTube-nas.tar.gz && \
[ -f "`$BACKUP_DIR/querytube.db" ] && mkdir -p data && cp -a "`$BACKUP_DIR/querytube.db" data/ && \
[ -f "`$BACKUP_DIR/.env" ] && cp -a "`$BACKUP_DIR/.env" .env && \
rm -rf "`$BACKUP_DIR" && \
mkdir -p data temp && \
chmod +x scripts/deploy-nas.sh
"@
ssh -p $Port $SshTarget $RemoteCmd

$EnvCheckCmd = "test -f '$NasPath/.env' && echo yes || echo no"
$NasHasEnv = (ssh -p $Port $SshTarget $EnvCheckCmd).Trim()
$LocalEnv = Join-Path $Root ".env"
if ($NasHasEnv -ne "yes") {
    if (Test-Path $LocalEnv) {
        Write-Host "NAS 尚無 .env，上傳本機設定（僅首次部署）..." -ForegroundColor Yellow
        scp -P $Port $LocalEnv "${SshTarget}:${NasPath}/.env"
    } else {
        Write-Warning "NAS 與本機皆無 .env，請手動建立後再部署。"
    }
}

Write-Host "=== 4/5 停止舊版 whisper 容器（若存在）===" -ForegroundColor Cyan
$CleanupCmd = @"
DOCKER_BIN=''
for bin in /var/packages/ContainerManager/target/usr/bin/docker /var/packages/Docker/target/usr/bin/docker /usr/local/bin/docker /usr/bin/docker; do
  if [ -x \"\$bin\" ]; then DOCKER_BIN=\"\$bin\"; break; fi
done
if [ -n \"\$DOCKER_BIN\" ]; then
  sudo \"\$DOCKER_BIN\" rm -f querytube_whisper 2>/dev/null || true
  sudo \"\$DOCKER_BIN\" image rm onerahmet/openai-whisper-asr-webservice:latest 2>/dev/null || true
fi
"@
ssh -p $Port $SshTarget $CleanupCmd

Write-Host "=== 5/5 建置並啟動容器 ===" -ForegroundColor Cyan
$DeployCmd = @"
cd '$NasPath' && \
sudo bash scripts/deploy-nas.sh
"@
ssh -p $Port $SshTarget $DeployCmd

Write-Host ""
Write-Host "Deploy complete. View logs:" -ForegroundColor Green
Write-Host ("  ssh -p {0} {1} `"cd {2}; sudo docker compose logs --tail 30 querytube`"" -f $Port, $SshTarget, $NasPath) -ForegroundColor White
