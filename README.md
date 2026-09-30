# AI 拍纸立得 · 后端服务

微信小程序「AI 拍纸立得」的 Python FastAPI 后端：把用户拍摄的多张图片，**按上传顺序**合并成一个
**A4 尺寸**的 PDF，落盘到静态目录并返回公网可访问链接。

- 图像处理：Pillow（方向纠正 / 去透明 / 缩放 / 转码）
- PDF 合成：PyMuPDF（默认，`fitz`），缺失时自动回退 ReportLab
- 运行环境：Linux + Python 3.10+，自带 CORS、统一异常处理、过期文件清理

---

## 1. 项目结构

```
AIPDF/
├── main.py                       # 服务入口：FastAPI 实例、CORS、静态挂载、异常处理、lifespan
├── requirements.txt
├── pyproject.toml                # ruff / pytest 配置
├── .env.example                  # 配置模板（复制为 .env）
├── Dockerfile
├── app/
│   ├── config.py                 # 全部配置（env / .env 覆盖）
│   ├── core/
│   │   └── exceptions.py         # ApiError + 全局异常处理器（统一错误 JSON）
│   ├── schemas/
│   │   └── pdf.py                # 出入参模型（含 base64 / 分张上传）
│   ├── routers/
│   │   └── convert.py            # 四个业务接口，见下方“接口”章节
│   ├── services/
│   │   ├── image_utils.py        # 文件头嗅探、EXIF 纠正、缩码、切页、base64 解码
│   │   ├── pdf_builder.py        # PyMuPDF / ReportLab 双引擎，A4 等比居中排版
│   │   ├── pdf_service.py        # 业务编排（异步读流 + 线程池做 CPU 密集活）
│   │   └── storage.py            # 随机文件名、公网 URL 拼接、过期清理
│   └── background/
│       └── cleanup.py            # 定时清理过期 PDF 与暂存原图
├── static/
│   └── pdfs/                     # PDF 输出目录（对外 URL 前缀 /static/pdfs/）
├── var/
│   └── uploads/                  # 分张上传的暂存原图（**不对外公开**，合成后即删）
├── miniapp/                      # 微信小程序原生页面（可直接导入微信开发者工具）
│   ├── app.js / app.json / app.wxss / sitemap.json / project.config.json
│   ├── LOCAL_TEST.md             # 本地联调测试指南（模拟器 / 真机）
│   └── pages/index/index.{wxml,wxss,js,json}
├── start_backend.bat             # 双击即用：环境自检 + 端口检查 + 启动后端（Windows）
├── scripts/
│   ├── dev-server.ps1            # PowerShell 版启动器（仅启动 + 打印地址）
│   ├── check_env.py              # 环境自检（Python 版本 / 依赖 / PDF 引擎）
│   └── net_info.ps1              # 探测局域网 IP、端口占用 PID
├── .github/workflows/
│   └── docker-publish.yml        # CI：lint + 测试 → 构建镜像推送到 ghcr.io
├── deploy/
│   ├── docker-deploy.sh          # ★ 服务器一键拉取镜像并部署/更新（手动拷到服务器）
│   ├── docker-compose.yml        # docker compose 部署（可选替代方案）
│   ├── aipdf.env.example         # 服务器端 .env 模板（Docker 用）
│   ├── nginx-docker.conf.example # Docker 部署配套的 Nginx 反代配置
│   ├── aipdf.service             # systemd 单元（裸机部署用）
│   └── nginx.conf.example        # Nginx 反向代理示例（裸机部署用）
├── examples/
│   └── miniapp-api.js            # 小程序调用示例（与 miniapp/ 页面同源的简化版）
└── tests/                        # 41 个测试：接口、静态访问、双引擎、两阶段上传、清理、安全
```

---

## 2. 快速开始

### Windows 一键启动：双击 `start_backend.bat`

直接双击运行（也可以在命令行执行），它会依次完成：

1. 检查 `python` 是否在 PATH 中
2. 检查依赖与 PDF 引擎，缺依赖自动 `pip install -r requirements.txt`
3. 检查 8000 端口：被占用时列出 PID，询问是否强制结束旧进程（`taskkill /T`）
4. 识别本机局域网 IP，打印【模拟器】与【真机调试】两种可用地址
5. 以 `--reload` 启动后端，并在 5 秒后自动打开 `http://127.0.0.1:8000/docs`

```bat
type start_backend.bat ^| more        :: 看一眼内容
start_backend.bat                    :: 启动（默认自动开浏览器）
start_backend.bat -nobrowser         :: 启动但不开浏览器
```

> 停止服务：在窗口里按 `Ctrl+C`。

其他等价的启动方式：

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\dev-server.ps1   # PowerShell 版（不带端口检查）
python main.py                                                       # 最裸的方式
```

### 本地开发

```bash
cd AIPDF
python -m venv .venv
source .venv/bin/activate           # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env                # 按需修改
python main.py                      # http://127.0.0.1:8000/docs
```

### Linux 服务器（生产）

```bash
# 1. 安装依赖（PyMuPDF 在多数平台都有 wheel，无需编译）
python3 -m venv /opt/aipdf/.venv
/opt/aipdf/.venv/bin/pip install -r /opt/aipdf/requirements.txt

# 2. 配置
cp .env.example .env && vim .env     # 至少设置 PUBLIC_BASE_URL 与 CORS_ALLOW_ORIGINS

# 3. 启动（生产建议 2 个 worker + Nginx 反代）
uvicorn main:app --host 127.0.0.1 --port 8000 --workers 2 \
    --proxy-headers --forwarded-allow-ips='127.0.0.1'
```

systemd 与 Nginx 示例见 `deploy/`。

---

## 3. 接口

### 3.1 `POST /api/v1/convert-to-pdf`（multipart，推荐）

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `files` | File[] | 是* | 图片，可重复该字段，**按传入顺序排版** |
| `file` | File | 是* | 单文件入口，兼容只传一个文件的场景 |
| `page_mode` | str | 否 | `fit`（默认，每张图一页）/ `split`（长截图按 A4 比例自动分页） |
| `pdf_title` | str | 否 | PDF 元数据标题，同时用作文件名前缀 |

\* `files` 与 `file` 至少提供一个。

```bash
curl -X POST https://yourdomain.com/api/v1/convert-to-pdf \
  -F "files=@1.jpg" -F "files=@2.jpg" -F "files=@3.png" \
  -F "page_mode=fit" -F "pdf_title=scan"
```

```json
{
  "code": 0,
  "message": "ok",
  "pdf_url": "https://yourdomain.com/static/pdfs/20250101_scan_4f3a9b21c8d04e77.pdf",
  "file_name": "20250101_scan_4f3a9b21c8d04e77.pdf",
  "page_count": 3,
  "size_bytes": 524288,
  "source_count": 3,
  "expires_at": "2025-01-02T08:00:00Z"
}
```

### 3.2 `POST /api/v1/convert-to-pdf-base64`（JSON，小程序友好）

`wx.uploadFile` 一次只能传一个文件，用 JSON 传 base64 数组最省事：

```json
{
  "images": [
    { "file_name": "1.jpg", "data": "data:image/jpeg;base64,/9j/4AAQ..." },
    { "file_name": "2.jpg", "data": "/9j/4AAQ..." }
  ],
  "page_mode": "fit",
  "pdf_title": "scan"
}
```

返回结构与 3.1 完全一致。

### 3.3 `POST /api/v1/upload-image` + `POST /api/v1/convert-to-pdf-by-ids`（分张上传，小程序首选）

微信小程序 `wx.uploadFile` **一次只能携带一个文件**，所以多图必须分两步：

```bash
# 第一步：按顺序逐张上传，拿到 image_id
curl -X POST https://yourdomain.com/api/v1/upload-image -F "file=@1.jpg"
# → {"code":0,"image_id":"9fc631e4...","size_bytes":183920,"expires_at":"..."}

# 第二步：按期望的排版顺序提交 id 数组，一次合成
curl -X POST https://yourdomain.com/api/v1/convert-to-pdf-by-ids \
  -H 'content-type: application/json' \
  -d '{"image_ids":["9fc631e4...","3210872e..."],"page_mode":"fit","pdf_title":"scan"}'
```

优势：请求体小（不膨胀 base64）、单张可任意大、顺序由数组决定；
上传的原图先规范化（EXIF/转 RGB/JPEG）暂存到 `var/uploads/`（**不在 static 下，外部无法访问**），
合并成功后立即删除，失败则保留 30 分钟供重试。

### 3.4 其他

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/health` | 健康检查，返回实际生效的 PDF 引擎 |
| GET | `/docs` | Swagger UI（可直接在浏览器里拖图片调试） |
| GET | `/static/pdfs/<file>.pdf` | 静态文件服务，外部可直接访问生成的 PDF |

### 错误响应（统一结构）

```json
{ "code": "FILE_TOO_LARGE", "message": "图片 1.jpg 超过单张上限 15MB", "detail": null }
```

| HTTP | code | 触发场景 |
| --- | --- | --- |
| 400 | `NO_FILES` | 没有上传任何文件 |
| 400 | `TOO_MANY_FILES` | 超过 `MAX_FILE_COUNT` |
| 400 | `INVALID_PARAM` | `page_mode` 非法 / 参数校验失败（422 同码） |
| 400 | `INVALID_IMAGE_ID` | 分张上传的 `image_id` 格式非法 |
| 404 | `IMAGE_NOT_FOUND` | `image_id` 不存在或已过期，需重新上传 |
| 400 | `IMAGE_DECODE_FAILED` | 文件损坏、base64 非法 |
| 400 | `IMAGE_TOO_SMALL` | 分辨率低于 `MIN_IMAGE_SIDE` |
| 413 | `FILE_TOO_LARGE` / `TOTAL_TOO_LARGE` / `REQUEST_TOO_LARGE` | 单张 / 总量 / 请求体超限 |
| 415 | `UNSUPPORTED_TYPE` | 伪装成图片的非图片文件（按文件头识别） |
| 500 | `PDF_BUILD_FAILED` / `INTERNAL_ERROR` | 合成失败 / 未捕获异常 |

---

## 4. 配置项（`.env` 或环境变量）

| 变量 | 默认值 | 说明 |
| --- | --- | --- |
| `DEBUG` | `false` | 开启后热重载 + DEBUG 日志 |
| `PUBLIC_BASE_URL` | 空 | **生产必填**，如 `https://yourdomain.com`；留空则按请求 Host 推导 |
| `STATIC_DIR` | `./static` | 静态根目录 |
| `PDF_SUBDIR` | `pdfs` | PDF 存放子目录 |
| `VAR_DIR` | `./var` | 运行时目录（暂存上传原图，不对外暴露） |
| `UPLOAD_SUBDIR` | `uploads` | 暂存子目录 |
| `UPLOAD_TTL_MINUTES` | `30` | 未合并的暂存原图保留时长 |
| `PDF_TTL_HOURS` | `24` | 文件保留时长 |
| `CLEANUP_INTERVAL_MINUTES` | `60` | 后台清理间隔 |
| `MAX_FILE_SIZE_MB` | `15` | 单张图片上限 |
| `MAX_TOTAL_SIZE_MB` | `80` | 单次总量上限 |
| `MAX_REQUEST_BODY_MB` | `120` | 请求体上限（读取前按 Content-Length 拦截） |
| `MAX_FILE_COUNT` | `20` | 单次最多张数 |
| `MAX_IMAGE_SIDE` | `4096` | 长边超过则等比缩小（控内存/体积，不影响 A4 打印清晰度） |
| `MIN_IMAGE_SIDE` | `16` | 小于该边长视为无效图片 |
| `PDF_ENGINE` | `auto` | `auto` / `pymupdf` / `reportlab` |
| `PAGE_MODE` | `fit` | 默认排版模式 |
| `PAGE_MARGIN_MM` | `0` | 页面留白（毫米），打印友好可设 `5` |
| `JPEG_QUALITY` | `90` | 转码质量 1–95 |
| `CORS_ALLOW_ORIGINS` | `*` | 逗号分隔白名单；小程序请求无 Origin，此项主要给 H5/后台用 |
| `CORS_ALLOW_CREDENTIALS` | `false` | 与 `*` 互斥（代码已自动规避） |

---

## 5. 小程序接入

### 5.1 直接使用现成页面

`miniapp/` 是一套可直接运行的微信小程序原生代码（UI + 逻辑都已写好）：

```
miniapp/
├── app.js / app.json / app.wxss / sitemap.json / project.config.json
└── pages/index/index.wxml|wxss|js|json
```

三步上线：

1. 用微信开发者工具「导入项目」，目录选 `miniapp/`，AppID 填自己的（当前是 `touristappid` 测试号）。
2. 把 `pages/index/index.js` 顶部的 `BASE_URL` 改成你的域名。
3. 把域名加入「开发管理 → 开发设置 → 服务器域名」的 `request`、`uploadFile`、`downloadFile` 三类合法域名；
   本地调试可在工具里勾选“不校验合法域名”（`project.config.json` 中已设 `urlCheck: false`，**上线前改回 true**）。

页面包含：顶部标题、3 列缩略图网格（点图预览、给角标看顺序、✕ 删除）、
排版模式切换（每图一页 / 长图分页）、底部【添加图片 / 拍照】【生成 PDF 并预览】两个按钮，
生成过程用 `wx.showLoading({mask:true})` 逐步提示“上传中 2/3 → 正在合成 → 正在下载”，
最终 `wx.openDocument({fileType:'pdf', showMenu:true})` 预览，右上角可直接保存/转发。

### 5.2 关于「循环 wx.uploadFile」

`wx.uploadFile` 一次请求只能带一个文件，直接循环打向 `/convert-to-pdf` 会得到 N 份 PDF，
因此页面默认采用**分张上传 + 一次合并**（`STRATEGY = 'uploadfile'`）：

```
for (每张图)  →  wx.uploadFile /api/v1/upload-image  →  收好 image_id
一次请求      →  POST /api/v1/convert-to-pdf-by-ids  { image_ids: [...] }  →  pdf_url
```

若后端还没部署这两个接口，把 `pages/index/index.js` 里的 `STRATEGY` 改成 `'base64'`
即变成一次性 JSON 请求（图片总大小建议控制在 5–10MB 内）。

### 5.3 本地联调（不用域名 / 不用 HTTPS）

桌面端调试看 **[miniapp/LOCAL_TEST.md](miniapp/LOCAL_TEST.md)**，两步就够：

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\dev-server.ps1   # 起后端，并打印模拟器/真机可用地址
```

再用微信开发者工具导入 `miniapp/` 目录，并勾选
**「详情 → 本地设置 → 不校验合法域名、web-view（业务域名）、TLS 版本以及 HTTPS 证书」**。

`miniapp/pages/index/index.js` 顶部有环境开关，三选一：

```js
const ENV = 'local';          // 开发者工具模拟器（http://127.0.0.1:8000）
const ENV = 'local-device';   // 真机预览，需同时把 LAN_IP 改成电脑的局域网 IP
const ENV = 'prod';           // 上线：HTTPS 域名 + 小程序后台白名单
```

本地不需要创建 `.env`：`PUBLIC_BASE_URL` 未配置时后端会按请求的 Host 推导，
所以模拟器拿到 `http://127.0.0.1:8000/...`、真机拿到 `http://<局域网IP>:8000/...`，两边都能正常下载。

### 5.4 其他要点

1. **必须 HTTPS**，并在「微信公众平台 → 开发管理 → 开发设置 → 服务器域名」把域名加入
   `request 合法域名`、`uploadFile 合法域名` 与 `downloadFile 合法域名`（本项目用 `wx.uploadFile` 分张上传，
**漏配 `uploadFile` 会报 `uploadFile:fail url not in domain list`**）。
2. 极简版调用示例（一个文件看完）见 `examples/miniapp-api.js`。
3. 上传前压缩很重要：`wx.compressImage` 把每张图控在 1MB 左右，再传才稳。

---

## 6. 部署

### 6.1 GitHub Actions 自动构建镜像（推荐）

推送到 `main` 分支后，`.github/workflows/docker-publish.yml` 会自动：

```
lint + pytest  →  docker build  →  推送 ghcr.io/hieedaniel/aipdf  →  启动容器冒烟测试 /health
```

产出的镜像标签：

| 标签 | 触发条件 | 说明 |
| --- | --- | --- |
| `latest` | 推送 `main` | 生产用这个 |
| `sha-xxxxxxx` | 每次 push | 按 commit 精确定位，方便回滚 |
| `v1.2.3` / `1.2` | 推送 `v*` tag | 版本发布 |

镜像地址：`ghcr.io/hieedaniel/aipdf:latest`（仓库名会统一转小写）。

> 首次构建后，如果服务器 `docker pull` 报 `denied`，去 GitHub → 头像 → **Your packages** → 找到 `aipdf` →
> **Package settings → Change visibility → Public**；或者用带 `read:packages` 的
> Personal Access Token 登录：`echo $TOKEN | docker login ghcr.io -u hieedaniel --password-stdin`。

### 6.2 服务器部署（Docker 拉镜像）

把 `deploy/docker-deploy.sh` 拷到服务器（单文件即可，不需要整套源码）：

```bash
# 在服务器上
chmod +x docker-deploy.sh
sudo ./docker-deploy.sh                       # 首次部署（生成 /opt/aipdf/aipdf.env）
sudo vim /opt/aipdf/aipdf.env                 # 改 PUBLIC_BASE_URL 为真实 HTTPS 域名
sudo ./docker-deploy.sh                       # 改完配置重启生效

sudo ./docker-deploy.sh --status              # 查状态 + 健康检查
sudo ./docker-deploy.sh --logs                # 看日志
sudo ./docker-deploy.sh --uninstall           # 卸载（数据保留）
```

脚本做的事：建目录 → 生成默认 `.env` → 拉镜像 → 替换容器 → 等 `/health` 就绪 → 清理旧镜像。
容器以 `127.0.0.1:8000` 暴露，配套 Nginx 配置见 `deploy/nginx-docker.conf.example`。

想用 compose 的话：`cp deploy/docker-compose.yml deploy/aipdf.env.example /opt/aipdf/` 后
`mv .env.example .env && docker compose up -d`。

### 6.3 Nginx（关键片段）

```nginx
server {
    listen 443 ssl http2;
    server_name yourdomain.com;
    client_max_body_size 150m;          # 必须 ≥ MAX_REQUEST_BODY_MB

    location /static/ {                  # 静态 PDF 直出，不走 Python
        alias /opt/aipdf/static/;
        add_header Cache-Control "public, max-age=3600";
    }

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_read_timeout 120s;
    }
}
```

完整示例见 `deploy/nginx.conf.example`，systemd 见 `deploy/aipdf.service`。

### 6.4 Docker（本地构建）

```bash
docker build -t aipdf .
docker run -d --name aipdf -p 8000:8000 \
  -e PUBLIC_BASE_URL=https://yourdomain.com \
  -v /data/aipdf/static:/app/static \
  -v /data/aipdf/var:/app/var \
  aipdf
```

---

## 7. 实现要点 / 已处理的坑

**排版**

- 页面固定 A4 纵向（595.28pt × 841.89pt），图片等比缩放后**居中**，绝不拉伸变形。
- `split` 模式按「可打印区域宽高比」切片，超长截图（如聊天记录、网页长图）会自然分成多页 A4，而不是被压缩成一条细线。
- 透明 PNG 先用白底合成再转 JPEG，避免出现黑底。

**健壮性**

- 不信任 `content_type` 和文件名：按**文件头**识别真实格式，`x.txt` 改名成 `x.jpg` 会被 415 拒绝。
- 分块读取上传流并即时校验大小，超大文件不会读满内存。
- 输出先写 `<name>.pdf.part` 再原子 `os.replace`，不会出现"下载到半个 PDF"。
- 文件名 = `日期_slug_uuid16.pdf`，slug 经过白名单清洗，天然防路径穿越。
- 单张图片按顺序处理，内存占用与图片张数无关；CPU 密集步骤放线程池，不阻塞事件循环。
- 定时任务清理过期文件与残留 `.part`，避免磁盘被写满。

**PDF 引擎**

- 优先 `import pymupdf`（PyMuPDF ≥ 1.24.3 推荐写法），旧版本自动回退到 `import fitz`，不产生弃用告警。
- `PDF_ENGINE=auto` 时若 PyMuPDF 不可用，会打日志并自动切到 ReportLab，服务不会挂。

**想加 AI 增强？**

在 `app/services/image_utils.py::decode_image()` 返回前插入你的处理（去阴影、透视矫正、超分、
OCR 去水印等），只要返回一个 RGB 的 `PIL.Image` 即可，后续合成逻辑无需改动。

---

## 8. 测试

```bash
python -m pytest            # 41 passed
python -m ruff check .      # All checks passed!
```

覆盖：多图顺序与页数、单文件字段、静态文件访问、A4 尺寸校验、长图分页、双引擎输出、
base64 入口（含 data URL 前缀）、分张上传→合并全流程、`image_id` 不存在/路径穿越/非图片、
暂存目录不可公开访问、暂存图 TTL 清理、CORS 预检、请求体/单文件/张数/总大小限流、
非图片与损坏图片、过期清理。

---

## 9. 常见问题

**Q：`pip install PyMuPDF` 失败？**
设 `PDF_ENGINE=reportlab`（已在 requirements 中），功能完全等价，只是体积略大。

**Q：iPhone 的 HEIC 图片报 415？**
打开 `requirements.txt` 里的 `pillow-heif`，并在 `app/services/image_utils.py` 顶部加
`from pillow_heif import register_heif_opener; register_heif_opener()`。

**Q：返回的 `pdf_url` 是 `http://127.0.0.1:8000/...`？**
没配 `PUBLIC_BASE_URL`，且反代没传 `X-Forwarded-Proto`。显式配置域名最稳，也能避免 Host 头伪造。

**Q：小程序提示"不在以下 request 合法域名列表中"？**
域名要 HTTPS、已备案、并加入小程序后台白名单；本机调试可在开发者工具里勾选"不校验合法域名"。

**Q：`var/uploads/` 堆积了原图？**
那些是上传后没触发合并的临时文件，后台任务每小时按 `UPLOAD_TTL_MINUTES`（默认 30 分钟）清理；
它不在 `static/` 下，外部无法下载。手动清理：直接删掉 `var/uploads/*.jpg`。

**Q：PDF 文件会无限增长吗？**
不会。默认 24 小时 TTL，后台任务每小时清理一次（`PDF_TTL_HOURS` / `CLEANUP_INTERVAL_MINUTES`）。
生产环境建议再把 `static/pdfs` 挂到独立数据盘或用对象存储替换 `storage.py` 的落盘逻辑。
