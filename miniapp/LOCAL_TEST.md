# 本地联调测试指南（微信开发者工具）

> 目标：在你自己电脑上跑通「选图 → 上传 → 合成 PDF → 预览」全链路，**不需要域名、不需要 HTTPS、不需要改 `.env`**。

## TL;DR（三步）

```powershell
# 1) 启动后端（会打印模拟器 / 真机两种可用地址）
cd "D:\01 Project\02练习项目\AIPDF"
powershell -ExecutionPolicy Bypass -File .\scripts\dev-server.ps1
```

2) 开发者工具 → 导入项目 → 目录选 **`AIPDF\miniapp`**（不是 `AIPDF`）
3) 详情 → 本地设置 → ✅ 勾选 **「不校验合法域名、web-view（业务域名）、TLS 版本以及 HTTPS 证书」**

跑完这三步就能点【生成 PDF 并预览】了。下面是细节与排错。

---

## 1. 启动后端

```powershell
cd "D:\01 Project\02练习项目\AIPDF"
powershell -ExecutionPolicy Bypass -File .\scripts\dev-server.ps1
```

脚本会打印并启动：

```
  项目目录 : D:\01 Project\02练习项目\AIPDF
  监听端口 : 0.0.0.0:8000  (模拟器 + 真机都能连)

  【微信开发者工具 · 模拟器】→ miniapp/pages/index/index.js 里 ENV = "local"
     BASE_URL = http://127.0.0.1:8000
     健康检查 : http://127.0.0.1:8000/health
     接口文档 : http://127.0.0.1:8000/docs

  【真机预览 / 真机调试】→ ENV = "local-device"，LAN_IP 填下面这个
     LAN_IP  = '10.198.6.14'
```

不启动服务、只看地址信息：加 `-NoServe`。

### 先确认后端本身没问题（强烈建议）

在浏览器里打开 `http://127.0.0.1:8000/docs` → 展开 `POST /api/v1/convert-to-pdf` → Try it out → 拖两张图 → Execute。

- 能返回 `pdf_url` 并且点开能看到 PDF → 后端 OK，后面出问题一定是小程序侧
- 这一步能把「后端问题」和「前端问题」彻底分开，省很多时间

也可以直接看健康检查：

```powershell
Invoke-RestMethod http://127.0.0.1:8000/health
# status : ok
# pdf_engine : pymupdf       ← 实际生效的引擎
```

---

## 2. 导入小程序项目

1. 打开微信开发者工具 → 项目列表左上角 **`+`** → **导入项目**
2. **目录**：`D:\01 Project\02练习项目\AIPDF\miniapp` ⚠️ 选到 `miniapp` 这一层，选错会提示找不到 `app.json`
3. **AppID**：点「测试号」即可（`project.config.json` 里预置的就是 `touristappid`）
4. **后端服务**：选「不使用云服务」
5. 语言：JavaScript

导入后模拟器里应该看到：

- 顶部紫色渐变卡：`AI 拍纸立得 - 图片转 PDF`
- 卡片下面一行小字：`调试模式 · 后端 http://127.0.0.1:8000` ← **看到这行说明配置对了**
- 中间「已选图片」空状态、排版模式、底部两个按钮

---

## 3. ★ 勾选「不校验合法域名」（不勾 100% 失败）

**详情 → 本地设置 → 勾选「不校验合法域名、web-view（业务域名）、TLS 版本以及 HTTPS 证书」**

开发者工具默认禁止小程序请求 `http://127.0.0.1`，不勾选会直接报：

```
request:fail url not in domain list
downloadFile:fail url not in domain list
```

> `miniapp/project.config.json` 里已经写了 `"urlCheck": false`，理论上等价，
> 但不同版本的工具不一定同步这个开关，**手动勾一次最稳**。
> 上线前记得把它改回 `true`（见第 8 节）。

---

## 4. 跑一遍完整流程

| 步骤 | 操作 | 期望现象 |
| --- | --- | --- |
| 1 | 点【添加图片 / 拍照】 | 弹出选择：从「图片库」选 **2~3 张**（模拟器里"拍照"是模拟的，也能跑通） |
| 2 | —— | 缩略图出现，左下角有 **序号 1/2/3**（就是 PDF 里的页序） |
| 3 | 点缩略图 | 全屏预览，可左右滑动 |
| 4 | 点缩略图右上角 `✕` | 该图被移除，后面序号自动前移 |
| 5 | 切换「每图一页 / 长图分页」 | 选中项变蓝（长截图选后者会自动切成多页） |
| 6 | 点【生成 PDF 并预览】 | 按钮变灰并依次显示：`上传中 1/3` → `上传中 2/3` → `正在合成…` → `正在下载…` |
| 7 | —— | 弹出 PDF 预览窗，右上角 `···` 里有「转发」「保存到手机」 |
| 8 | —— | 顶部 toast：`已生成 3 页` |

### 同时观察后端控制台

应看到（顺序即上传顺序）：

```
POST /api/v1/upload-image        200 OK      ← 每张图一次
POST /api/v1/upload-image        200 OK
POST /api/v1/upload-image        200 OK
POST /api/v1/convert-to-pdf-by-ids 200 OK    ← 只有一次，合并成一个 PDF
GET  /static/pdfs/2026xxxx_xxxx.pdf 200 OK   ← 模拟 wx.downloadFile
```

### 检查落盘

```powershell
Get-ChildItem static\pdfs\*.pdf          # 新生成的 PDF 在这里
Get-Content .\var\uploads\*.jpg -ErrorAction SilentlyContinue   # 应为空：合成成功后暂存原图已自动清理
```

顺手验证一下真·A4：

```powershell
python -c "import fitz;d=fitz.open(sorted(__import__('glob').glob('static/pdfs/*.pdf'))[-1]);print(d.page_count,'pages',round(d[0].rect.width,2),'x',round(d[0].rect.height,2),'pt')"
# 3 pages 595.28 x 841.89 pt      ← 标准 A4
```

---

## 5. 出问题时看哪里

| 位置 | 用途 |
| --- | --- |
| 开发者工具 **调试器 → Network** | 看每次 `wx.uploadFile` / `wx.request` / `wx.downloadFile` 的状态码与响应体 |
| 开发者工具 **调试器 → Console** | 页面 `console.error('[AI拍纸立得] 生成失败', code, err)` 会打在这里 |
| 后端控制台 | uvicorn 访问日志，谁调了哪个接口一目了然 |
| `http://127.0.0.1:8000/docs` | 绕开小程序直接压接口，定位是前端还是后端 |
| 页面弹窗 | 后端错误码已映射成中文，例如「存在不支持的图片格式，请用 JPG/PNG」 |

---

## 6. 真机预览 / 真机调试（可选）

模拟器过了再上真机，因为这里多两个坑：**网络可达性** 和 **防火墙**。

1. 手机与电脑连 **同一个 Wi-Fi**（注意路由器别开「AP 隔离」/ 访客网络）
2. 改 `miniapp/pages/index/index.js`：

   ```js
   const ENV = 'local-device';
   const LAN_IP = '10.198.6.14';   // 换成 dev-server.ps1 打印的那个
   ```

3. 放行防火墙（管理员 PowerShell，只需一次）：

   ```powershell
   New-NetFirewallRule -DisplayName "AIPDF dev 8000" -Direction Inbound -Protocol TCP -LocalPort 8000 -Action Allow
   ```

   或者首次运行 `python main.py` 弹窗时点「允许访问」，**务必勾上「专用网络」**。

4. **先用手机浏览器访问 `http://10.198.6.14:8000/health`**，能看到 `{"status":"ok",...}` 才继续；打不开就先解决 Wi-Fi/防火墙，别去折腾小程序
5. 开发者工具点【预览】→ 手机扫码 → 打开小程序 → 右上角 `···` → **打开调试**
   （开了调试模式会出现 vConsole，同时才会跳过域名校验）
6. 若真机报 `url not in domain list`，就是第 5 步的「打开调试」没做

> 真机上 `pdf_url` 会自动变成 `http://10.198.6.14:8000/static/pdfs/xxx.pdf`（后端按请求 Host 推导），
> 所以手机能正常下载，**不需要配 `PUBLIC_BASE_URL`**。

---

## 7. 常见报错对照表

| 现象 | 原因 | 解决 |
| --- | --- | --- |
| `request:fail url not in domain list` | 没勾「不校验合法域名」 | 第 3 节 |
| `request:fail timeout` / 弹窗「连不上后端」 | 后端没启动、或端口不是 8000 | 浏览器开 `/health`；确认 `BASE_URL` 与脚本打印的一致 |
| 真机连不上（模拟器正常） | 不同网段 / 防火墙 / AP 隔离 | 第 6 节第 4 步 |
| 生成失败 `UNSUPPORTED_TYPE` | 选了 HEIC/WebP，或改了后缀的假图 | 换 JPG/PNG；HEIC 支持见 README 第 9 节 |
| 生成失败 `FILE_TOO_LARGE` | 单张超过 `MAX_FILE_SIZE_MB`（默认 15MB） | 少选几张，或在 `.env` 调大 |
| 生成失败 `REQUEST_TOO_LARGE` | 总大小超 `MAX_REQUEST_BODY_MB`（默认 120MB） | 减少张数 |
| 生成失败 `IMAGE_NOT_FOUND` | 上传后超过 30 分钟才点合成（暂存过期） | 重新选图再生成 |
| 点【添加图片】没反应 | 首次使用需授权相册/相机 | 弹窗点「允许」；或工具菜单「清缓存 → 清除授权数据」后重试 |
| PDF 打开是空白/黑图 | 图片解码异常 | 换张图复现并保留原始文件 |
| 合成的页序和预期不符 | 长按拖拽排序未实现 | 目前按选择顺序；删掉重选即可（需要拖拽排序告诉我） |

---

## 8. 测试完、上线前要改回来的地方

- [ ] `miniapp/pages/index/index.js`：`ENV = 'prod'`，并把 `PROD_BASE_URL` 换成真实域名
- [ ] `miniapp/app.js`：`ENV` 与 `BASE_URLS.prod` 同步改掉
- [ ] `miniapp/project.config.json`：`"urlCheck": false` → `true`
- [ ] 后端 `AIPDF/.env`：`PUBLIC_BASE_URL=https://yourdomain.com`
- [ ] 小程序后台「开发管理 → 开发设置 → 服务器域名」：把域名加入 `request` 与 `downloadFile` 白名单
- [ ] 后台 CORS 收紧：`CORS_ALLOW_ORIGINS`（小程序不受 CORS 限制，但 H5 端需要）
