<#
.SYNOPSIS
    本地开发启动脚本：自动识别局域网 IP，打印模拟器/真机可用的地址，然后启动后端。

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File .\scripts\dev-server.ps1
    powershell -ExecutionPolicy Bypass -File .\scripts\dev-server.ps1 -NoServe   # 只看信息不启动
#>
param(
    [int]$Port = 8000,
    [switch]$NoServe
)

$ErrorActionPreference = 'Stop'

# 项目根目录（本脚本位于 <root>/scripts/）
$root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
Set-Location $root

Write-Host ''
Write-Host '========================================================' -ForegroundColor Cyan
Write-Host '  AI 拍纸立得 · 本地开发后端' -ForegroundColor Cyan
Write-Host '========================================================' -ForegroundColor Cyan
Write-Host ("  项目目录 : {0}" -f $root)
Write-Host ("  监听端口 : 0.0.0.0:{0}  (模拟器 + 真机都能连)" -f $Port)

# ---------- 1) 探测局域网 IP ----------
$lanIp = $null
try {
    $lanIp = Get-NetIPAddress -AddressFamily IPv4 -ErrorAction Stop |
        Where-Object { $_.IPAddress -notlike '127.*' -and $_.IPAddress -notlike '169.254.*' } |
        Sort-Object @{ Expression = {
                if ($_.IPAddress -like '192.168.*') { 0 }
                elseif ($_.IPAddress -like '10.*') { 1 }
                elseif ($_.IPAddress -like '172.1[6-9].*' -or $_.IPAddress -like '172.2[0-9].*' -or $_.IPAddress -like '172.3[01].*') { 2 }
                else { 3 }
            } } |
        Select-Object -First 1 -ExpandProperty IPAddress
} catch {
    $lanIp = $null
}
if (-not $lanIp) {
    try {
        $lanIp = [System.Net.Dns]::GetHostAddresses([System.Net.Dns]::GetHostName()) |
            Where-Object { $_.AddressFamily -eq 'InterNetwork' -and $_.IPAddressToString -notlike '127.*' } |
            Select-Object -First 1 -ExpandProperty IPAddressToString
    } catch { $lanIp = $null }
}

Write-Host ''
Write-Host '  【微信开发者工具 · 模拟器】→ miniapp/pages/index/index.js 里 ENV = "local"' -ForegroundColor Green
Write-Host ("     BASE_URL = http://127.0.0.1:{0}" -f $Port)
Write-Host ("     健康检查 : http://127.0.0.1:{0}/health" -f $Port)
Write-Host ("     接口文档 : http://127.0.0.1:{0}/docs" -f $Port)

Write-Host ''
if ($lanIp) {
    Write-Host '  【真机预览 / 真机调试】→ ENV = "local-device"，LAN_IP 填下面这个' -ForegroundColor Yellow
    Write-Host ("     LAN_IP  = '{0}'" -f $lanIp) -ForegroundColor Yellow
    Write-Host ("     手机浏览器打开 http://{0}:{1}/health 能返回 JSON 才算通" -f $lanIp, $Port)
    Write-Host '     注意：手机与电脑必须同一个 Wi-Fi，且 Windows 防火墙需放行 python.exe' -ForegroundColor DarkGray
} else {
    Write-Host '  【真机调试】未能识别局域网 IP，请手动执行 ipconfig 查看' -ForegroundColor Yellow
}
Write-Host '========================================================' -ForegroundColor Cyan
Write-Host ''

if ($NoServe) { return }

# ---------- 2) 检查端口占用 ----------
$busy = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
if ($busy) {
    Write-Host ("[!] 端口 {0} 已被占用（PID {1}），可能是上一次的后端还在跑。" -f $Port, ($busy.OwningProcess -join ',')) -ForegroundColor Red
    Write-Host ("    结束它： Stop-Process -Id {0} -Force" -f ($busy.OwningProcess -join ',')) -ForegroundColor Red
    return
}

# ---------- 3) 确保依赖就绪 ----------
$py = Get-Command python -ErrorAction SilentlyContinue
if (-not $py) {
    Write-Host '[x] 找不到 python，请先安装并加入 PATH' -ForegroundColor Red
    return
}
python -c "import fastapi, uvicorn, PIL, pydantic_settings" 2>$null
if ($LASTEXITCODE -ne 0) {
    Write-Host '[!] 依赖不完整，正在执行 pip install -r requirements.txt ...' -ForegroundColor Yellow
    python -m pip install -r requirements.txt
}

Write-Host '[√] 启动中（Ctrl+C 停止，改代码会自动重载）...' -ForegroundColor Green
Write-Host ''
python -m uvicorn main:app --host 0.0.0.0 --port $Port --reload
