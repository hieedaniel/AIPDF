#!/usr/bin/env bash
# ==============================================================================
# AI 拍纸立得 —— Linux 服务器一键部署 / 更新脚本（Docker 版）
#
# 用法（把本文件拷到服务器，例如 /root/docker-deploy.sh）：
#   chmod +x docker-deploy.sh
#   ./docker-deploy.sh                 # 拉取最新镜像并重启容器
#   ./docker-deploy.sh --uninstall     # 停止并删除容器（保留数据）
#   ./docker-deploy.sh --logs          # 跟踪日志
#   ./docker-deploy.sh --status        # 查看状态与健康检查
#
# 首次部署前按需修改下面「配置区」，或用环境变量覆盖：
#   IMAGE=ghcr.io/xxx/aipdf:v1.0.0 ./docker-deploy.sh
#   PUBLIC_BASE_URL=https://pdf.example.com ./docker-deploy.sh
#   HOST_PORT=19533 ./docker-deploy.sh          # 换宿主机端口（默认 19530）
#
# 关于端口：容器内固定监听 8000，但每个容器有独立网络命名空间，
# 不会和其它容器的 8000 冲突；真正会冲突的只有「宿主机端口」，默认已避开 8000。
# ==============================================================================
set -euo pipefail

# ------------------------------- 配置区 ---------------------------------------
IMAGE="${IMAGE:-ghcr.io/hieedaniel/aipdf:latest}"   # 镜像地址（仓库名统一小写）
CONTAINER="${CONTAINER:-aipdf}"                     # 容器名
DATA_DIR="${DATA_DIR:-/opt/aipdf}"                  # 宿主机数据目录（静态 PDF / 临时文件 / 配置）
HOST_PORT="${HOST_PORT:-19530}"                     # 宿主机端口（容器内固定 8000；避免与已有服务冲突）
BIND_ADDR="${BIND_ADDR:-127.0.0.1}"                 # 只监听本机，由 Nginx 反代；想直接暴露改成 0.0.0.0
PUBLIC_BASE_URL="${PUBLIC_BASE_URL:-}"              # 【必填】如 https://pdf.example.com
CORS_ALLOW_ORIGINS="${CORS_ALLOW_ORIGINS:-*}"       # H5 用；小程序不受 CORS 限制
TZ_VALUE="${TZ_VALUE:-Asia/Shanghai}"
# GHCR 私有包需要登录。公开包可留空。
# 生成 PAT：GitHub → Settings → Developer settings → Tokens(classic) 勾选 read:packages
GHCR_USER="${GHCR_USER:-}"
GHCR_TOKEN="${GHCR_TOKEN:-}"

ENV_FILE="$DATA_DIR/aipdf.env"
STATIC_DIR="$DATA_DIR/static"
VAR_DIR="$DATA_DIR/var"

# ------------------------------- 小工具 ---------------------------------------
c_red()   { printf '\033[31m%s\033[0m\n' "$*"; }
c_green() { printf '\033[32m%s\033[0m\n' "$*"; }
c_blue()  { printf '\033[36m%s\033[0m\n' "$*"; }

need_docker() {
  if ! command -v docker >/dev/null 2>&1; then
    c_red "✗ 未检测到 docker。请先安装："
    echo "    curl -fsSL https://get.docker.com | sh && systemctl enable --now docker"
    exit 1
  fi
  if ! docker info >/dev/null 2>&1; then
    c_red "✗ 无法连接 docker daemon（可能需要 sudo 或 docker 服务未启动）"
    exit 1
  fi
}

# 部署前预检宿主机端口，避免 docker run 时才发现 "port is already allocated"
check_port_conflict() {
  local conflicts
  conflicts="$(docker ps --format '{{.Names}} {{.Ports}}' | grep -F ":${HOST_PORT}->" || true)"
  # 重建时旧容器仍占着该端口，排除掉自己
  conflicts="$(printf '%s\n' "$conflicts" | grep -v -E "^${CONTAINER}[[:space:]]" || true)"
  if [ -n "$(printf '%s' "$conflicts" | tr -d '[:space:]')" ]; then
    c_red "✗ 宿主机端口 ${HOST_PORT} 已被其它容器占用："
    printf '%s\n' "$conflicts"
    echo
    echo "  换个端口重试，例如："
    echo "    HOST_PORT=19533 ./docker-deploy.sh"
    exit 1
  fi

  # 非 docker 进程占用（bind 也会失败）
  if command -v ss >/dev/null 2>&1 && ss -lnt 2>/dev/null | grep -qE "[:.]${HOST_PORT}[[:space:]]"; then
    c_red "✗ 宿主机端口 ${HOST_PORT} 已被非 Docker 进程占用："
    ss -lntp 2>/dev/null | grep -E "[:.]${HOST_PORT}[[:space:]]" || true
    echo
    echo "  换个端口重试，例如：HOST_PORT=19533 ./docker-deploy.sh"
    exit 1
  fi
}

# ------------------------------- 子命令 ---------------------------------------
case "${1:-}" in
  --logs)    docker logs -f --tail=200 "$CONTAINER"; exit 0 ;;
  --status)
    docker ps --filter "name=^${CONTAINER}$" --format 'table {{.Names}}\t{{.Status}}\t{{.Ports}}'
    echo "--- /health ---"
    curl -fsS "http://${BIND_ADDR}:${HOST_PORT}/health" || true
    echo
    exit 0 ;;
  --restart) docker restart "$CONTAINER"; exit 0 ;;
  --uninstall)
    c_blue "→ 停止并删除容器 $CONTAINER（数据保留在 $DATA_DIR）"
    docker rm -f "$CONTAINER" 2>/dev/null || true
    c_green "✓ 已卸载"
    exit 0 ;;
  ""|--deploy) ;;
  *) c_red "未知参数：$1（可用：--deploy/--logs/--status/--restart/--uninstall）"; exit 2 ;;
esac

# ------------------------------- 正式部署 -------------------------------------
need_docker
check_port_conflict

c_blue "=== AI 拍纸立得 部署开始 ==="
echo "  镜像     : $IMAGE"
echo "  容器名   : $CONTAINER"
echo "  数据目录 : $DATA_DIR"
echo "  监听     : ${BIND_ADDR}:${HOST_PORT} -> 容器内 8000"

# 1) 目录
mkdir -p "$STATIC_DIR/pdfs" "$VAR_DIR/uploads"

# 2) 配置文件（首次自动生成，已存在则保留，不覆盖你的修改）
if [ ! -f "$ENV_FILE" ]; then
  c_blue "→ 首次部署，生成默认配置 $ENV_FILE"
  cat > "$ENV_FILE" <<EOF
DEBUG=false
PDF_ENGINE=auto
PAGE_MODE=fit

# 生成的 PDF 对外公网地址前缀（必须与 Nginx 域名一致，否则小程序下载会失败）
PUBLIC_BASE_URL=${PUBLIC_BASE_URL:-https://yourdomain.com}

# 存储
STATIC_DIR=/app/static
PDF_SUBDIR=pdfs
VAR_DIR=/app/var
UPLOAD_SUBDIR=uploads
PDF_TTL_HOURS=24
UPLOAD_TTL_MINUTES=30
CLEANUP_INTERVAL_MINUTES=60

# 上传限制（与 Nginx client_max_body_size 保持一致）
MAX_FILE_SIZE_MB=15
MAX_TOTAL_SIZE_MB=80
MAX_REQUEST_BODY_MB=120
MAX_FILE_COUNT=20
MAX_IMAGE_SIDE=4096

# CORS（小程序请求无 Origin，此项主要给 H5 / 调试用）
CORS_ALLOW_ORIGINS=${CORS_ALLOW_ORIGINS}
CORS_ALLOW_CREDENTIALS=false
EOF
  chmod 600 "$ENV_FILE"
else
  c_blue "→ 复用已有配置 $ENV_FILE"
fi

if grep -q 'yourdomain.com' "$ENV_FILE"; then
  c_red "⚠ 提醒：$ENV_FILE 里 PUBLIC_BASE_URL 还是占位域名，请改成真实 HTTPS 域名后再上线。"
fi

# 3) 登录 GHCR（仅在提供了凭据时）
if [ -n "$GHCR_TOKEN" ]; then
  c_blue "→ 登录 GitHub Container Registry"
  echo "$GHCR_TOKEN" | docker login ghcr.io -u "$GHCR_USER" --password-stdin
fi

# 4) 拉取镜像
c_blue "→ 拉取镜像（首次可能较慢）"
if ! docker pull "$IMAGE"; then
  c_red "✗ 拉取失败。常见原因："
  echo "   1. 镜像是私有包 → 设置 GHCR_USER / GHCR_TOKEN 后重试"
  echo "   2. 镜像名不对（仓库名必须全小写）→ 当前：$IMAGE"
  echo "   3. 服务器网络无法访问 ghcr.io"
  exit 1
fi

# 5) 替换容器（先起新的再删旧的会端口冲突，这里直接滚动替换）
c_blue "→ 停止旧容器"
docker rm -f "$CONTAINER" >/dev/null 2>&1 || true

c_blue "→ 启动新容器"
docker run -d \
  --name "$CONTAINER" \
  --restart unless-stopped \
  -p "${BIND_ADDR}:${HOST_PORT}:8000" \
  --env-file "$ENV_FILE" \
  -e TZ="$TZ_VALUE" \
  -v "$STATIC_DIR:/app/static" \
  -v "$VAR_DIR:/app/var" \
  --health-cmd 'python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen(\"http://127.0.0.1:8000/health\", timeout=2).status==200 else 1)"' \
  --health-interval 30s \
  --health-timeout 5s \
  --health-retries 3 \
  --health-start-period 15s \
  --log-opt max-size=20m \
  --log-opt max-file=3 \
  "$IMAGE" >/dev/null

# 6) 等待健康
c_blue "→ 等待服务就绪"
for i in $(seq 1 30); do
  if curl -fsS "http://127.0.0.1:${HOST_PORT}/health" >/dev/null 2>&1; then
    c_green "✓ 服务已就绪（${i}s）"
    break
  fi
  if [ "$i" = "30" ]; then
    c_red "✗ 30 秒内 /health 未就绪，最近日志："
    docker logs --tail=60 "$CONTAINER" || true
    exit 1
  fi
  sleep 1
done

# 7) 清理旧镜像（保留最近使用的）
docker image prune -f >/dev/null 2>&1 || true

echo
c_green "=== 部署完成 ==="
echo "  健康检查 : http://127.0.0.1:${HOST_PORT}/health"
echo "  接口文档 : http://127.0.0.1:${HOST_PORT}/docs"
echo "  静态 PDF : $STATIC_DIR/pdfs"
echo
echo "  下一步（如果还没配 Nginx/HTTPS）："
echo "    sudo cp deploy/nginx-docker.conf.example /etc/nginx/conf.d/aipdf.conf"
echo "    sudo vim /etc/nginx/conf.d/aipdf.conf   # 改 server_name 与证书路径"
echo "    sudo nginx -t && sudo systemctl reload nginx"
echo
echo "  常用命令：./docker-deploy.sh --logs | --status | --restart | --uninstall"
