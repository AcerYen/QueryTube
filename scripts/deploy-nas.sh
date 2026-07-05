#!/usr/bin/env bash
# QueryTube NAS 一鍵部署（Synology / QNAP / Linux）
set -euo pipefail

cd "$(dirname "$0")/.."

if [[ ! -f .env ]]; then
  echo "錯誤：找不到 .env，請先從本機複製設定檔。" >&2
  exit 1
fi

mkdir -p data temp

if [[ -f data/querytube.db ]]; then
  echo "保留既有資料庫：data/querytube.db"
fi

find_docker() {
  local candidates=(
    /usr/local/bin/docker
    /var/packages/ContainerManager/target/usr/bin/docker
    /var/packages/Docker/target/usr/bin/docker
    /usr/bin/docker
  )
  for bin in "${candidates[@]}"; do
    if [[ -x "$bin" ]]; then
      echo "$bin"
      return 0
    fi
  done
  if command -v docker >/dev/null 2>&1; then
    command -v docker
    return 0
  fi
  return 1
}

DOCKER_BIN="$(find_docker || true)"
if [[ -z "$DOCKER_BIN" ]]; then
  echo "錯誤：找不到 docker。請在 DSM 套件中心安裝 Container Manager。" >&2
  exit 1
fi

echo "使用 Docker: $DOCKER_BIN"
DOCKER=(sudo "$DOCKER_BIN")

echo "=== 停止舊版 whisper 容器（若存在）==="
"${DOCKER[@]}" rm -f querytube_whisper 2>/dev/null || true

echo "=== 建置並啟動 QueryTube ==="
COMPOSE_PROFILE=""
if grep -qE '^LINE_CHANNEL_SECRET=.+' .env 2>/dev/null \
  && grep -qE '^LINE_CHANNEL_ACCESS_TOKEN=.+' .env 2>/dev/null \
  && grep -qE '^NGROK_AUTHTOKEN=.+' .env 2>/dev/null; then
  echo "LINE + ngrok 已設定，使用 --profile line"
  COMPOSE_PROFILE="--profile line"
fi
"${DOCKER[@]}" compose ${COMPOSE_PROFILE} up -d --build --remove-orphans

echo ""
echo "=== 容器狀態 ==="
"${DOCKER[@]}" compose ps

echo ""
echo "=== 最近 log ==="
"${DOCKER[@]}" compose logs --tail 30 querytube
