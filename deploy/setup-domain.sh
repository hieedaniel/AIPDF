#!/usr/bin/env bash
# ==============================================================================
# AI 拍纸立得 —— 域名绑定 / Nginx 反向代理 / HTTPS 一键脚本
#
# 作用：把一个已解析到本机的域名，映射到本机 127.0.0.1:${HOST_PORT} 的 aipdf 容器，
#       并自动申请 Let's Encrypt 证书 + 配置 80→443 跳转 + 回填 PUBLIC_BASE_URL。
#
# 用法（在服务器上执行，需要 root）：
#   chmod +x setup-domain.sh
#   sudo ./setup-domain.sh aipdf.seveninfo.cn --email you@example.com
#
# 常用变体：
#   sudo ./setup-domain.sh aipdf.seveninfo.cn -m you@example.com --http-only   # 只配 HTTP，不签证书
#   sudo ./setup-domain.sh aipdf.seveninfo.cn -m you@example.com --no-redirect # 不强制 80→443
#   sudo ./setup-domain.sh aipdf.seveninfo.cn -m you@example.com --force       # DNS 未生效也继续（走 CDN 时用）
#   sudo ./setup-domain.sh --status        # 查看当前域名/证书/健康状态
#   sudo ./setup-domain.sh --renew         # 立即续期证书
#   sudo ./setup-domain.sh --check-dns     # 只检查解析与端口
#
# 可用环境变量覆盖：
#   DATA_DIR=/opt/aipdf        # 数据目录（静态目录、env 文件所在）
#   HOST_PORT=19530            # 容器在宿主机监听的端口
#   BIND_ADDR=127.0.0.1        # 容器绑定地址
#   NGINX_CONF=/etc/nginx/conf.d/aipdf.conf
#   CONTAINER=aipdf            # 容器名（用于重启）
#   DEPLOY_SCRIPT=./docker-deploy1.sh   # 重新创建容器用的部署脚本（自动探测）
#
# 注意：修改 --env-file 后必须「重新创建」容器才生效，docker restart 不会重读 env，
#       所以脚本最后会调用部署脚本重建容器。
# ==============================================================================
set -euo pipefail

# ------------------------------- 配置区 ---------------------------------------
DATA_DIR="${DATA_DIR:-/opt/aipdf}"
HOST_PORT="${HOST_PORT:-19530}"
BIND_ADDR="${BIND_ADDR:-127.0.0.1}"
CONTAINER="${CONTAINER:-aipdf}"
STATIC_DIR="${STATIC_DIR:-$DATA_DIR/static}"
ENV_FILE="${ENV_FILE:-$DATA_DIR/aipdf.env}"
NGINX_CONF="${NGINX_CONF:-/etc/nginx/conf.d/aipdf.conf}"
DEPLOY_SCRIPT="${DEPLOY_SCRIPT:-}"

DOMAIN=""
EMAIL=""
HTTP_ONLY=0
REDIRECT=1
FORCE=0
MODE="setup"
EXPECT_IP=""

# ------------------------------- 小工具 ---------------------------------------
c_red()   { printf '\033[31m%s\033[0m\n' "$*"; }
c_green() { printf '\033[32m%s\033[0m\n' "$*"; }
c_blue()  { printf '\033[36m%s\033[0m\n' "$*"; }
c_yellow(){ printf '\033[33m%s\033[0m\n' "$*"; }
die()     { c_red "✗ $*"; exit 1; }

usage() {
  sed -n '2,30p' "$0" | sed 's/^# \{0,1\}//'
  exit 0
}

# ------------------------------- 参数解析 -------------------------------------
while [ $# -gt 0 ]; do
  case "$1" in
    -h|--help)      usage ;;
    -m|--email)     EMAIL="${2:-}"; shift 2 ;;
    --email=*)      EMAIL="${1#*=}"; shift ;;
    --http-only)    HTTP_ONLY=1; shift ;;
    --no-redirect)  REDIRECT=0; shift ;;
    --force)        FORCE=1; shift ;;
    --expect-ip)    EXPECT_IP="${2:-}"; shift 2 ;;
    --expect-ip=*)  EXPECT_IP="${1#*=}"; shift ;;
    --status)       MODE="status"; shift ;;
    --renew)        MODE="renew"; shift ;;
    --check-dns)    MODE="check-dns"; shift ;;
    -*)             die "未知参数：$1（用 --help 查看帮助）" ;;
    *)              [ -z "$DOMAIN" ] || die "只接受一个域名参数"; DOMAIN="$1"; shift ;;
  esac
done

require_root() { [ "$(id -u)" = "0" ] || die "需要 root：请用 sudo $0 ..."; }

public_ip() {
  local ip
  # 阿里云元数据（最准，不走公网）
  ip="$(curl -fsS --max-time 3 http://100.100.100.200/latest/meta-data/eipv4 2>/dev/null || true)"
  [ -n "$ip" ] && { printf '%s' "$ip"; return; }
  ip="$(curl -fsS --max-time 5 https://api.ipify.org 2>/dev/null || true)"
  [ -n "$ip" ] && { printf '%s' "$ip"; return; }
  ip="$(curl -fsS --max-time 5 https://ifconfig.me 2>/dev/null || true)"
  [ -n "$ip" ] && { printf '%s' "$ip"; return; }
  hostname -I 2>/dev/null | awk '{print $1}'
}

resolve_ip() {
  local d="$1" ip=""
  if command -v dig >/dev/null 2>&1; then
    ip="$(dig +short A "$d" @8.8.8.8 2>/dev/null | grep -E '^[0-9]+\.' | tail -n1 || true)"
    [ -n "$ip" ] && { printf '%s' "$ip"; return; }
    ip="$(dig +short A "$d" 2>/dev/null | grep -E '^[0-9]+\.' | tail -n1 || true)"
  fi
  if [ -z "$ip" ] && command -v getent >/dev/null 2>&1; then
    ip="$(getent ahostsv4 "$d" 2>/dev/null | awk '{print $1}' | tail -n1 || true)"
  fi
  if [ -z "$ip" ]; then
    ip="$(python3 -c "import socket,sys;print(socket.gethostbyname(sys.argv[1]))" "$d" 2>/dev/null || true)"
  fi
  printf '%s' "$ip"
}

nginx_bin() { command -v nginx || echo /usr/sbin/nginx; }

install_pkgs() {
  c_blue "→ 检查并安装 nginx / certbot / dnsutils（已装则跳过）"
  if command -v apt-get >/dev/null 2>&1; then
    export DEBIAN_FRONTEND=noninteractive
    apt-get update -qq
    apt-get install -y -qq nginx certbot python3-certbot-nginx dnsutils curl
  elif command -v dnf >/dev/null 2>&1; then
    dnf install -y nginx certbot python3-certbot-nginx bind-utils curl
  elif command -v yum >/dev/null 2>&1; then
    yum install -y nginx certbot python3-certbot-nginx bind-utils curl
  else
    die "无法识别的包管理器，请手动安装 nginx + certbot + python3-certbot-nginx"
  fi
  systemctl enable --now nginx >/dev/null 2>&1 || true
}

# 反代是否已能拿到后端 200（绕过 DNS，直接对 127.0.0.1 带 Host 头请求）
backend_proxy_ok() {
  local d="$1" code
  code="$(curl -s -o /dev/null -w '%{http_code}' --max-time 5 -H "Host: $d" "http://127.0.0.1/health" || true)"
  [ "$code" = "200" ]
}

check_dns_and_ports() {
  local d="$1" real expect
  real="$(resolve_ip "$d")"
  expect="${EXPECT_IP:-$(public_ip)}"

  echo "  域名         : $d"
  echo "  解析结果     : ${real:-（未解析）}"
  echo "  本机公网 IP  : ${expect:-（未取到）}"

  if [ -z "$real" ]; then
    c_yellow "⚠ 域名尚未解析。请到 DNS 控制台添加 A 记录："
    echo "    主机记录 aipdf  →  记录值 ${expect:-<你的公网IP>}  →  类型 A"
    [ "$FORCE" = "1" ] || return 1
    return 0
  fi
  if [ -n "$EXPECT_IP" ] && [ "$real" != "$EXPECT_IP" ]; then
    c_yellow "⚠ 解析到 $real，与 --expect-ip $EXPECT_IP 不一致（CDN 场景可忽略）"
    [ "$FORCE" = "1" ] || return 1
    return 0
  fi
  if [ -n "$expect" ] && [ "$real" != "$expect" ] && [ -z "$EXPECT_IP" ]; then
    c_yellow "⚠ 解析到 $real，与本机公网 IP $expect 不一致。"
    c_yellow "  若走 CDN/负载均衡属正常；否则请先修正 DNS。"
    [ "$FORCE" = "1" ] || return 1
    return 0
  fi

  # 本机端口可达性
  if command -v ss >/dev/null 2>&1 && ss -lnt 2>/dev/null | grep -qE "[:.]${HOST_PORT}[[:space:]]"; then
    c_green "✓ 本机 ${BIND_ADDR}:${HOST_PORT} 正在监听"
  else
    c_yellow "⚠ 本机 ${HOST_PORT} 端口未监听，容器可能未启动（仍继续）"
  fi
  c_green "✓ DNS 检查通过"
}

dns_preflight() {
  check_dns_and_ports "$1" || die "DNS 检查未通过（确认 DNS/安全组，或加 --force 强行继续）"
  echo
  c_yellow "提示：请确认云厂商「安全组」已放行 80/tcp 与 443/tcp；国内服务器域名需完成 ICP 备案。"
}

write_nginx_conf() {
  local d="$1" conf="$NGINX_CONF"

  mkdir -p "$(dirname "$conf")" "$STATIC_DIR/pdfs"
  [ -f "$conf" ] && cp -a "$conf" "${conf}.bak.$(date +%Y%m%d%H%M%S)" && c_blue "→ 已备份原配置：${conf}.bak.*"

  cat > "$conf" <<'NGINXEOF'
# AI 拍纸立得 —— 反向代理（由 deploy/setup-domain.sh 生成）
# 重新生成：sudo ./setup-domain.sh __DOMAIN__ -m you@example.com
upstream aipdf_backend {
    server 127.0.0.1:__PORT__;
    keepalive 32;
}

server {
    listen 80;
    listen [::]:80;
    server_name __DOMAIN__;

    client_max_body_size 150m;      # 必须 >= 后端 MAX_REQUEST_BODY_MB（默认 120）
    client_body_timeout  120s;

    # 静态 PDF 由 Nginx 直出，不经过 Python
    location /static/ {
        alias __STATIC_DIR__/;
        expires 1h;
        add_header Cache-Control "public, max-age=3600";
        add_header X-Content-Type-Options nosniff;
        try_files $uri =404;
    }

    # 业务接口
    location / {
        proxy_pass http://aipdf_backend;
        proxy_http_version 1.1;
        proxy_set_header Connection "";

        proxy_set_header Host              $host;
        proxy_set_header X-Real-IP         $remote_addr;
        proxy_set_header X-Forwarded-For   $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;

        # 多图合成耗时与图片数量成正比，超时给足
        proxy_connect_timeout 10s;
        proxy_send_timeout    120s;
        proxy_read_timeout    120s;

        # 图片直传后端，不落 Nginx 磁盘
        proxy_request_buffering off;
        client_body_buffer_size 1m;
    }

    access_log /var/log/nginx/aipdf.access.log;
    error_log  /var/log/nginx/aipdf.error.log warn;
}
NGINXEOF

  sed -i "s|__DOMAIN__|${d}|g; s|__PORT__|${HOST_PORT}|g; s|__STATIC_DIR__|${STATIC_DIR}|g" "$conf"

  # conf.d 未被 include 的发行版（少数）提示一下
  if ! grep -qE 'conf\.d/\*\.conf' /etc/nginx/nginx.conf 2>/dev/null; then
    c_yellow "⚠ /etc/nginx/nginx.conf 未 include conf.d/*.conf，请手动确认配置会被加载"
  fi

  c_blue "→ 校验 Nginx 配置"
  "$(nginx_bin)" -t || die "nginx -t 失败，请检查 $conf"
}

reload_nginx() {
  systemctl reload nginx 2>/dev/null || "$(nginx_bin)" -s reload
  c_green "✓ Nginx 已重载"
}

enable_http2_if_supported() {
  local conf="$1" ver
  ver="$("$(nginx_bin)" -v 2>&1 | sed -n 's|.*nginx/\([0-9.]*\).*|\1|p')"
  [ -n "$ver" ] || return 0
  # 1.25.1+ 支持独立的 "http2 on;" 指令；更老的版本用 "listen ... http2"
  if [ "$(printf '%s\n1.25.1\n' "$ver" | sort -V | head -n1)" = "1.25.1" ]; then
    cp -a "$conf" "${conf}.pre-http2"
    awk '
      /^[[:space:]]*listen[[:space:]]+443[[:space:]]+ssl[[:space:];]/ && !done { print; print "    http2 on;"; done=1; next }
      { print }
    ' "${conf}.pre-http2" > "$conf"
    if "$(nginx_bin)" -t >/dev/null 2>&1; then
      c_green "✓ 已启用 HTTP/2"
    else
      mv "${conf}.pre-http2" "$conf"
      c_yellow "⚠ HTTP/2 启用失败，已回滚（不影响访问）"
    fi
    rm -f "${conf}.pre-http2"
  fi
}

issue_cert() {
  local d="$1" args
  args=(-d "$d" --nginx --non-interactive --agree-tos --no-eff-email --keep-until-expiring)
  [ "$REDIRECT" = "1" ] && args+=(--redirect)
  if [ -n "$EMAIL" ]; then
    args+=(-m "$EMAIL")
  else
    c_yellow "⚠ 未提供 --email，改用 --register-unsafely-without-email（收不到到期提醒）"
    args+=(--register-unsafely-without-email)
  fi

  c_blue "→ 申请/复用 Let's Encrypt 证书：$d"
  if certbot "${args[@]}"; then
    c_green "✓ 证书就绪"
  else
    c_red "✗ 证书申请失败。常见原因："
    echo "   1. DNS 未生效 / 解析到了别的机器 → ./setup-domain.sh --check-dns"
    echo "   2. 云厂商安全组未放行 80 端口（HTTP-01 校验必须走 80）"
    echo "   3. 域名走了 Cloudflare 等 CDN 且开了代理 → 先改成 DNS only 再签一次"
    echo "   4. 国内服务器域名未备案 → 80 端口被拦截"
    echo "   5. 已有其它 80 端口的 server 抢了 default_server"
    return 1
  fi
  enable_http2_if_supported "$NGINX_CONF"
  reload_nginx
}

update_env_and_recreate() {
  local d="$1"
  if [ ! -f "$ENV_FILE" ]; then
    c_yellow "⚠ 未找到 $ENV_FILE，跳过 PUBLIC_BASE_URL 回填（容器可能用的是其它 --env-file）"
    return 0
  fi

  c_blue "→ 回填 PUBLIC_BASE_URL=https://$d（$ENV_FILE）"
  cp -a "$ENV_FILE" "${ENV_FILE}.bak.$(date +%Y%m%d%H%M%S)"
  if grep -q '^PUBLIC_BASE_URL=' "$ENV_FILE"; then
    sed -i "s|^PUBLIC_BASE_URL=.*|PUBLIC_BASE_URL=https://${d}|" "$ENV_FILE"
  else
    printf 'PUBLIC_BASE_URL=https://%s\n' "$d" >> "$ENV_FILE"
  fi
  grep -n '^PUBLIC_BASE_URL=' "$ENV_FILE"

  local script="$DEPLOY_SCRIPT"
  if [ -z "$script" ]; then
    for cand in \
      "$(dirname "$(readlink -f "$0")")/docker-deploy.sh" \
      "$(dirname "$(readlink -f "$0")")/docker-deploy1.sh" \
      "$PWD/docker-deploy.sh" "$PWD/docker-deploy1.sh" \
      "$DATA_DIR/docker-deploy.sh" "$DATA_DIR/docker-deploy1.sh"; do
      [ -f "$cand" ] && { script="$cand"; break; }
    done
  fi
  if [ -z "$script" ]; then
    script="$(ls -1 "$(dirname "$(readlink -f "$0")")"/docker-deploy*.sh "$PWD"/docker-deploy*.sh 2>/dev/null | head -n1 || true)"
  fi

  if [ -n "$script" ] && [ -f "$script" ]; then
    c_blue "→ 用 $script 重建容器（env 改动必须重建，restart 不生效）"
    bash "$script" --deploy
  else
    c_yellow "⚠ 没找到 docker-deploy*.sh，无法自动重建容器。请手动执行以让 PUBLIC_BASE_URL 生效："
    echo "    docker rm -f $CONTAINER && ./docker-deploy.sh"
    return 0
  fi
}

verify() {
  local d="$1" ok=1 code
  echo
  c_blue "=== 验收 ==="

  code="$(curl -s -o /dev/null -w '%{http_code}' --max-time 5 "http://127.0.0.1:${HOST_PORT}/health" || true)"
  printf '  后端直连    : http://127.0.0.1:%s/health -> %s\n' "$HOST_PORT" "$code"
  [ "$code" = "200" ] || { c_red "  ✗ 后端健康检查异常，先看：docker logs --tail=100 $CONTAINER"; ok=0; }

  if [ "$HTTP_ONLY" = "1" ]; then
    code="$(curl -s -o /dev/null -w '%{http_code}' --max-time 5 "http://${d}/health" || true)"
    printf '  HTTP 域名   : http://%s/health -> %s\n' "$d" "$code"
    [ "$code" = "200" ] || { c_yellow "  ⚠ HTTP 访问异常（DNS 未生效或安全组未开 80）"; ok=0; }
  else
    code="$(curl -s -o /dev/null -w '%{http_code}' --max-time 5 "https://${d}/health" || true)"
    printf '  HTTPS 域名  : https://%s/health -> %s\n' "$d" "$code"
    [ "$code" = "200" ] || { c_yellow "  ⚠ HTTPS 访问异常"; ok=0; }

    code="$(curl -s -o /dev/null -w '%{http_code}' --max-time 5 "http://${d}/health" || true)"
    printf '  HTTP 跳转   : http://%s/health -> %s（301/308 为正常）\n' "$d" "$code"

    if [ -f "$ENV_FILE" ]; then
      printf '  PUBLIC_BASE_URL : %s\n' "$(grep -m1 '^PUBLIC_BASE_URL=' "$ENV_FILE" | cut -d= -f2-)"
      grep -q "PUBLIC_BASE_URL=https://${d}\$" "$ENV_FILE" \
        || { c_yellow "  ⚠ PUBLIC_BASE_URL 与域名不一致，PDF 下载链接会错"; ok=0; }
    fi
  fi

  echo
  if [ "$ok" = "1" ]; then
    c_green "✓ 全部检查通过，域名已绑定：https://${d}"
  else
    c_yellow "⚠ 部分检查未通过，按上面提示排查"
    return 1
  fi
}

do_setup() {
  [ -n "$DOMAIN" ] || { usage; }
  # 规范化：去协议、去路径、去端口
  DOMAIN="${DOMAIN#http://}"; DOMAIN="${DOMAIN#https://}"; DOMAIN="${DOMAIN%%/*}"; DOMAIN="${DOMAIN%%:*}"

  require_root
  install_pkgs
  echo
  c_blue "=== AI 拍纸立得 域名绑定：$DOMAIN ==="
  echo "  容器端口   : ${BIND_ADDR}:${HOST_PORT}"
  echo "  数据目录   : $DATA_DIR"
  echo "  静态目录   : $STATIC_DIR"
  echo "  Nginx 配置 : $NGINX_CONF"
  echo

  dns_preflight "$DOMAIN"

  write_nginx_conf "$DOMAIN"
  reload_nginx

  if backend_proxy_ok "$DOMAIN"; then
    c_green "✓ Nginx 已能反代到后端（/health 200）"
  else
    c_yellow "⚠ 用 Host: $DOMAIN 请求本机 80 未拿到 200，先排查容器与 Nginx 日志"
    echo "    docker logs --tail=100 $CONTAINER"
    echo "    tail -n 50 /var/log/nginx/aipdf.error.log"
  fi

  if [ "$HTTP_ONLY" = "1" ]; then
    c_yellow "→ --http-only：跳过 HTTPS 证书签发"
  else
    issue_cert "$DOMAIN" || true
  fi

  update_env_and_recreate "$DOMAIN" || true
  verify "$DOMAIN" || true

  echo
  c_blue "常用排查命令："
  echo "  $0 --status"
  echo "  tail -f /var/log/nginx/aipdf.error.log"
  echo "  docker logs -f --tail=200 $CONTAINER"
  echo "  certbot certificates"
}

do_status() {
  require_root
  c_blue "=== Nginx ==="
  systemctl is-active nginx 2>/dev/null || true
  "$(nginx_bin)" -T 2>/dev/null | grep -nE 'server_name|listen|proxy_pass|ssl_certificate ' | grep -v '^\s*#' || true

  echo
  c_blue "=== 容器 ==="
  if command -v docker >/dev/null 2>&1; then
    docker ps --filter "name=^${CONTAINER}$" --format 'table {{.Names}}\t{{.Status}}\t{{.Ports}}' || true
  else
    echo "（未安装 docker）"
  fi

  echo
  c_blue "=== 证书 ==="
  certbot certificates 2>/dev/null | grep -E 'Certificate Name|Domains|Expiry' || echo "（暂无证书）"

  echo
  c_blue "=== 健康检查 ==="
  local _health
  _health="$(curl -s --max-time 5 "http://127.0.0.1:${HOST_PORT}/health" || true)"
  echo "$_health"
  case "$_health" in
    *'"warnings":[]'*) c_green "✓ 自检通过（PUBLIC_BASE_URL 等关键配置无告警）" ;;
    *warnings*)
      c_yellow "⚠ 自检有告警，见上面 warnings 字段（最常见的还是 PUBLIC_BASE_URL）" ;;
  esac
  if [ -f "$ENV_FILE" ]; then
    printf 'PUBLIC_BASE_URL=%s\n' "$(grep -m1 '^PUBLIC_BASE_URL=' "$ENV_FILE" | cut -d= -f2-)"
  fi

  # 从配置里提取域名，顺带测一次 HTTPS
  local d
  d="$(grep -m1 -E '^[[:space:]]*server_name[[:space:]]' "$NGINX_CONF" 2>/dev/null | awk '{print $2}' | tr -d ';' || true)"
  if [ -n "$d" ]; then
    echo
    echo "  域名: $d"
    printf '  https://%s/health -> %s\n' "$d" "$(curl -s -o /dev/null -w '%{http_code}' --max-time 6 "https://${d}/health" || true)"
  fi
}

do_renew() {
  require_root
  c_blue "→ 续期证书"
  certbot renew --quiet
  reload_nginx
  c_green "✓ 续期流程执行完毕（未到期会跳过）"
  echo "  自检：certbot renew --dry-run"
}

do_check_dns() {
  [ -n "$DOMAIN" ] || die "--check-dns 需要带上域名，例如：$0 --check-dns aipdf.seveninfo.cn"
  require_root
  check_dns_and_ports "$DOMAIN" || true
  echo
  echo "  80 端口外部可达性可从本机自测（需已配 Nginx）："
  printf '  http://%s/health -> %s\n' "$DOMAIN" "$(curl -s -o /dev/null -w '%{http_code}' --max-time 6 "http://${DOMAIN}/health" || true)"
}

case "$MODE" in
  setup)     do_setup ;;
  status)    do_status ;;
  renew)     do_renew ;;
  check-dns) do_check_dns ;;
esac
