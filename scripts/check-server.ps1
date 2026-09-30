<#
.SYNOPSIS
    本地自检脚本：不开微信开发者工具，直接对着【已部署的服务器】跑一遍完整链路。

.DESCRIPTION
    依次验证：
      1) HTTPS /health 是否正常（顺带看 http→https 是否跳转）
      2) 自动生成测试图片
      3) 分张上传 /api/v1/upload-image（小程序实际走的路径）
      4) 合并 /api/v1/convert-to-pdf-by-ids + 一次多文件 /api/v1/convert-to-pdf 对照
      5) 下载 PDF，校验文件头、页数、以及 pdf_url 的域名

    只有这里全绿了，再去微信开发者工具里联调；否则一定是网络 / 域名 / 后端问题。

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File .\scripts\check-server.ps1
    powershell -ExecutionPolicy Bypass -File .\scripts\check-server.ps1 -BaseUrl https://aipdf.seveninfo.cn
    powershell -ExecutionPolicy Bypass -File .\scripts\check-server.ps1 -Count 3 -Keep
#>
param(
    [string]$BaseUrl = 'https://aipdf.seveninfo.cn',
    [int]$Count = 2,
    [switch]$Keep
)

$ErrorActionPreference = 'Stop'

# Windows PowerShell 5.1 默认可能只有 TLS 1.0，会导致 HTTPS 直接失败
try {
    [Net.ServicePointManager]::SecurityProtocol = `
        [Net.SecurityProtocolType]::Tls12 -bor [Net.SecurityProtocolType]::Tls13
} catch {
    [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
}

$BaseUrl = $BaseUrl.TrimEnd('/')
if ($BaseUrl -notmatch '^https?://') { $BaseUrl = "https://$BaseUrl" }

$script:Failed = 0
$script:Work = Join-Path $env:TEMP ("aipdf-check-" + [guid]::NewGuid().ToString('N').Substring(0, 8))

function W-Ok($m)   { Write-Host "[OK] $m" -ForegroundColor Green }
function W-Bad($m)  { Write-Host "[XX] $m" -ForegroundColor Red; $script:Failed++ }
function W-Info($m) { Write-Host "     $m" -ForegroundColor DarkGray }
function W-Warn($m) { Write-Host "[!]  $m" -ForegroundColor Yellow }
function W-Step($m) { Write-Host ""; Write-Host "=== $m ===" -ForegroundColor Cyan }

# 取出 500 响应体（Invoke-RestMethod 在非 2xx 时会抛异常，正文里才有业务错误码）
function Get-ErrBody($err) {
    try {
        $resp = $err.Exception.Response
        if (-not $resp) { return '' }
        $reader = New-Object System.IO.StreamReader($resp.GetResponseStream())
        return $reader.ReadToEnd()
    } catch { return '' }
}

# 根据服务端返回的错误码/正文给出可操作的排查指引
function Show-ServerHint($body) {
    if ($body -match 'STORAGE_NOT_WRITABLE' -or $body -match 'Permission denied' -or $body -match 'PermissionError') {
        W-Info '原因：容器内非 root 用户（uid 10001）对挂载目录无写权限。服务器上执行：'
        W-Info '  chown -R 10001:10001 /opt/aipdf/static /opt/aipdf/var && docker restart aipdf'
        W-Info '新版部署脚本自带修复：./docker-deploy.sh --fix-perms'
    } elseif ($body -match 'INTERNAL_ERROR') {
        W-Info '服务器返回 500。先看日志定位：docker logs --tail=100 aipdf'
        W-Info '常见于挂载目录不可写，执行：./docker-deploy.sh --fix-perms'
    } elseif ($body -match 'PDF_BUILD_FAILED') {
        W-Info 'PDF 生成失败：常见于磁盘写满、挂载目录只读、或图片解码异常'
        W-Info '检查：df -h /opt && ls -ld /opt/aipdf/static/pdfs'
    }
}

function Invoke-Check {
    # ------------------------------------------------------------ 1) 健康检查
    W-Step "1/5  健康检查"
    try {
        $health = Invoke-RestMethod -Uri "$BaseUrl/health" -TimeoutSec 15
        W-Ok "GET $BaseUrl/health"
        W-Info ($health | ConvertTo-Json -Compress)
        if ($health.status -ne 'ok') { W-Warn "status 不是 ok，请确认后端版本" }
        if ($health.pdf_engine) { W-Info ("PDF 引擎 : {0}" -f $health.pdf_engine) }
        if ($health.PSObject.Properties.Name -contains 'public_base_url') {
            if ("$($health.public_base_url)" -ne '') {
                W-Info ("公网地址 : {0}" -f $health.public_base_url)
            } else {
                W-Warn '后端未配置 PUBLIC_BASE_URL（按请求 Host 推导），生成的 pdf_url 可能不可访问'
            }
        } else {
            W-Info '该镜像的 /health 还未上报 public_base_url（旧版本），下面用 pdf_url 反推校验'
        }
        # 服务端自检告警：BUILD 后新增的 warnings 字段，是“能跑但功能不对”的探测器
        if ($health.PSObject.Properties.Name -contains 'warnings' -and $health.warnings.Count -gt 0) {
            $script:Failed++
            W-Bad "后端自检发现 $($health.warnings.Count) 项配置问题："
            $health.warnings | ForEach-Object { W-Info "  - $_" }
            W-Info '服务器上修复：./docker-deploy.sh --set-domain 你的域名   （或 --fix-perms）'
        }
    } catch {
        W-Bad "健康检查失败：$($_.Exception.Message)"
        W-Info "先在浏览器打开 $BaseUrl/health 看看；打不开就别继续了。"
        return 1
    }

    if ($BaseUrl -like 'https://*') {
        $hostOnly = ([uri]$BaseUrl).Host
        $code = (& curl.exe -s -o NUL -w '%{http_code}' --max-time 10 "http://$hostOnly/health" 2>$null)
        $code = ([string]$code).Trim()
        if ($code -eq '301' -or $code -eq '308') {
            W-Ok "http://$hostOnly/health → $code（强制跳转 HTTPS）"
        } elseif ($code -eq '200') {
            W-Warn "http://$hostOnly/health 直接返回 200，建议在 Nginx 加 80→443 跳转"
        } else {
            W-Warn "http://$hostOnly/health 返回 '$code'（可能 80 未开放或被安全组拦截）"
        }
    }

    # ------------------------------------------------------------ 2) 造测试图
    W-Step "2/5  生成 $Count 张测试图片"
    Add-Type -AssemblyName System.Drawing
    $images = @()
    for ($i = 1; $i -le $Count; $i++) {
        $bmp = New-Object System.Drawing.Bitmap 1200, 1600
        $g = [System.Drawing.Graphics]::FromImage($bmp)
        $g.Clear([System.Drawing.Color]::White)
        $g.TextRenderingHint = [System.Drawing.Text.TextRenderingHint]::AntiAlias
        $bigFont = New-Object System.Drawing.Font('Arial', 260, [System.Drawing.FontStyle]::Bold)
        $smallFont = New-Object System.Drawing.Font('Arial', 60, [System.Drawing.FontStyle]::Regular)
        $pen = New-Object System.Drawing.Pen([System.Drawing.Color]::LightGray, 8)
        $g.DrawString("$i", $bigFont, [System.Drawing.Brushes]::Black, 80, 120)
        $g.DrawString("aipdf check $i", $smallFont, [System.Drawing.Brushes]::Gray, 90, 640)
        $g.DrawRectangle($pen, 40, 40, 1120, 1520)
        $g.Dispose(); $bigFont.Dispose(); $smallFont.Dispose(); $pen.Dispose()
        $path = Join-Path $script:Work "test-$i.jpg"
        $bmp.Save($path, [System.Drawing.Imaging.ImageFormat]::Jpeg)
        $bmp.Dispose()
        $images += $path
        W-Ok ("test-{0}.jpg  {1:N0} KB" -f $i, ((Get-Item $path).Length / 1KB))
    }
    W-Info "工作目录：$script:Work"

    # ------------------------------------------- 3) 分张上传（小程序实际路径）
    W-Step "3/5  分张上传 /api/v1/upload-image（wx.uploadFile 走的路径）"
    $imageIds = @()
    $idx = 0
    foreach ($img in $images) {
        $idx++
        $raw = & curl.exe -sS --max-time 60 -X POST "$BaseUrl/api/v1/upload-image" -F "file=@$img"
        try { $up = ($raw -join '') | ConvertFrom-Json } catch { $up = $null }
        if ($up -and $up.code -eq 0 -and $up.image_id) {
            $imageIds += $up.image_id
            W-Ok ("第 {0} 张 → image_id={1}（{2:N0} KB）" -f $idx, $up.image_id, ($up.size_bytes / 1KB))
        } else {
            W-Bad "第 $idx 张上传失败，原始响应："
            W-Info ([string]($raw -join ' '))
            if (([string]($raw -join '')) -match 'INTERNAL_ERROR|PDF_BUILD_FAILED|STORAGE_NOT_WRITABLE') {
                Show-ServerHint ([string]($raw -join ''))
            }
        }
    }
    if ($imageIds.Count -ne $images.Count) {
        W-Bad "upload-image 未全部成功，后续步骤跳过"
        return $script:Failed
    }

    # ---------------------------------------------------------- 4) 合并 & 对照
    W-Step "4/5  合并 /api/v1/convert-to-pdf-by-ids"
    $jsonBody = @{
        image_ids = $imageIds
        page_mode = 'fit'
        pdf_title = 'check-server'
    } | ConvertTo-Json -Compress
    try {
        $conv = Invoke-RestMethod -Uri "$BaseUrl/api/v1/convert-to-pdf-by-ids" -Method Post `
            -ContentType 'application/json; charset=utf-8' `
            -Body ([System.Text.Encoding]::UTF8.GetBytes($jsonBody)) -TimeoutSec 180
        W-Ok ("code={0}  page_count={1}  source_count={2}  size={3:N0} KB" -f `
                $conv.code, $conv.page_count, $conv.source_count, ($conv.size_bytes / 1KB))
        W-Info ("pdf_url = {0}" -f $conv.pdf_url)

        $targetHost = ([uri]$BaseUrl).Host
        $urlHost = ([uri]$conv.pdf_url).Host
        if ($urlHost -eq $targetHost) {
            W-Ok "pdf_url 域名与后端一致（PUBLIC_BASE_URL 配置正确）"
        } else {
            W-Bad "pdf_url 指向 $urlHost，与 $targetHost 不一致 → 改服务器 aipdf.env 的 PUBLIC_BASE_URL 后重建容器"
        }
    } catch {
        $body = Get-ErrBody $_
        W-Bad "合并失败：$($_.Exception.Message)"
        if ($body) { W-Info ("服务端正文：" + $body) }
        Show-ServerHint $body
        return $script:Failed
    }
    if ($conv.code -ne 0) {
        W-Bad "合并返回业务错误：$($conv.code) / $($conv.message)"
    } elseif ($conv.page_count -ne $images.Count) {
        W-Warn "page_count=$($conv.page_count)，与上传张数 $($images.Count) 不一致"
    }

    $multiArgs = @('-sS', '--max-time', '180', '-X', 'POST', "$BaseUrl/api/v1/convert-to-pdf")
    foreach ($img in $images) { $multiArgs += @('-F', "files=@$img") }
    $multiArgs += @('-F', 'page_mode=fit', '-F', 'pdf_title=check-multi')
    $rawMulti = & curl.exe @multiArgs
    try { $multi = ($rawMulti -join '') | ConvertFrom-Json } catch { $multi = $null }
    if ($multi -and $multi.code -eq 0 -and $multi.page_count -eq $images.Count) {
        W-Ok ("/convert-to-pdf 一次传多文件也正常（page_count={0}）" -f $multi.page_count)
    } else {
        W-Warn "/convert-to-pdf 多文件对照测试异常：$([string]($rawMulti -join ' '))"
    }

    # ------------------------------------------------------------ 5) 下载校验
    W-Step "5/5  下载 PDF 并校验"
    $pdfPath = Join-Path $script:Work 'result.pdf'

    # 先看 pdf_url 域名对不对 —— 这是“合成成功但小程序报 downloadFile:fail”的根因
    $urlHost = $null
    try { $urlHost = ([uri]$conv.pdf_url).Host } catch { }
    $baseHost = ([uri]$BaseUrl).Host
    if ($urlHost -and $urlHost -ne $baseHost) {
        W-Bad "pdf_url 的域名是 '$urlHost'，跟你访问的 '$baseHost' 不一致"
        W-Info '小程序会去下载 '$urlHost' 下的文件 → downloadFile:fail timeout / url not in domain list'
        W-Info '原因：服务器 aipdf.env 里 PUBLIC_BASE_URL 没改（还是占位域名）。修复：'
        W-Info '  cd /opt/aipdf && ./docker-deploy.sh --set-domain aipdf.seveninfo.cn'
    }

    try {
        Invoke-WebRequest -Uri $conv.pdf_url -OutFile $pdfPath -TimeoutSec 180 -UseBasicParsing
        $len = (Get-Item $pdfPath).Length
        $bytes = [System.IO.File]::ReadAllBytes($pdfPath)
        $magic = [System.Text.Encoding]::ASCII.GetString($bytes, 0, 5)
        if ($magic -eq '%PDF-') {
            W-Ok ("下载成功 {0:N0} KB，文件头 %PDF- ✓" -f ($len / 1KB))
        } else {
            W-Bad "下载到的文件头是 '$magic'，不是 PDF（可能被 CDN / 网关改写）"
            W-Info "若正文是 HTML，检查 Nginx 的 /static/ alias 是否指向了正确目录"
        }
        $text = [System.Text.Encoding]::GetEncoding(28591).GetString($bytes)
        $pageObjs = ([regex]::Matches($text, '/Type\s*/Page[^s]')).Count
        if ($pageObjs -gt 0) {
            W-Info ("PDF 内 /Type /Page 计数 = {0}（应与 page_count={1} 一致）" -f $pageObjs, $conv.page_count)
        }
    } catch {
        W-Bad "下载 PDF 失败：$($_.Exception.Message)"
        W-Info "从 $BaseUrl 主动去下 $($conv.pdf_url)：（这是小程序做的事，失败原因基本一致）"
        W-Info "  curl.exe -o test.pdf '$($conv.pdf_url)'"
        W-Info "服务器上排查：ls -l /opt/aipdf/static/pdfs && tail -n 50 /var/log/nginx/aipdf.error.log"
    }

    return $script:Failed
}

Write-Host ""
Write-Host "AI 拍纸立得 · 服务器链路自检" -ForegroundColor Cyan
Write-Host "目标地址 : $BaseUrl"
Write-Host ("时间     : {0}" -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'))

New-Item -ItemType Directory -Path $script:Work -Force | Out-Null

try {
    $script:Failed = Invoke-Check
} finally {
    if ($Keep) {
        Write-Host ""
        Write-Host "测试文件保留在：$script:Work" -ForegroundColor Yellow
    } else {
        Remove-Item -LiteralPath $script:Work -Recurse -Force -ErrorAction SilentlyContinue
    }
}

Write-Host ""
Write-Host "=============================== 汇总 ===============================" -ForegroundColor Cyan
if ($script:Failed -eq 0) {
    Write-Host " 全部通过 ✓  服务器可以直接给小程序用了" -ForegroundColor Green    Write-Host ""
    Write-Host " 下一步（微信开发者工具）：" -ForegroundColor Cyan
    Write-Host "   miniapp/pages/index/index.js 里把 ENV 改成 'server'"
    Write-Host "   详情 → 本地设置 → 勾选「不校验合法域名…」（或先把域名加进后台白名单）"
} else {
    Write-Host " 有 $script:Failed 项未通过，按上面 [XX] / [!] 的提示处理" -ForegroundColor Red
}
Write-Host " 服务器端日志：docker logs -f --tail=200 aipdf" -ForegroundColor DarkGray
Write-Host "===================================================================" -ForegroundColor Cyan

exit ([int]$script:Failed)
