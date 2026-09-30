<#
.SYNOPSIS
    网络信息探测 —— 供 start_backend.bat 调用。

.DESCRIPTION
    批处理里写 PowerShell 单行命令要同时处理 cmd 的引号、转义符和管道，
    极易出错，所以统一抽成独立脚本，.bat 只负责取一行输出。

.EXAMPLE
    powershell -NoProfile -File scripts\net_info.ps1 -Mode lanip
    10.198.6.14

.EXAMPLE
    powershell -NoProfile -File scripts\net_info.ps1 -Mode portpid -Port 8000
    12345
#>
param(
    [ValidateSet('lanip', 'portpid')]
    [string]$Mode = 'lanip',

    [int]$Port = 8000
)

$ErrorActionPreference = 'SilentlyContinue'

if ($Mode -eq 'lanip') {
    # 优先 192.168.x → 10.x → 172.16-31.x（排除回环与 APIPA 169.254.x）
    $ip = Get-NetIPAddress -AddressFamily IPv4 |
        Where-Object { $_.IPAddress -notlike '127.*' -and $_.IPAddress -notlike '169.254.*' } |
        Sort-Object @{ Expression = {
                if ($_.IPAddress -like '192.168.*') { 0 }
                elseif ($_.IPAddress -like '10.*') { 1 }
                elseif ($_.IPAddress -match '^172\.(1[6-9]|2[0-9]|3[01])\.') { 2 }
                else { 3 }
            } } |
        Select-Object -First 1 -ExpandProperty IPAddress

    # 退路：DNS 解析本机名（老系统没有 Get-NetIPAddress 时）
    if (-not $ip) {
        $ip = [System.Net.Dns]::GetHostAddresses([System.Net.Dns]::GetHostName()) |
            Where-Object { $_.AddressFamily -eq 'InterNetwork' -and $_.IPAddressToString -notlike '127.*' } |
            Select-Object -First 1 -ExpandProperty IPAddressToString
    }

    if ($ip) { Write-Output $ip }
    exit 0
}

# 指定端口上的监听进程 PID（可能多个），逗号分隔；没有则输出空
$pids = Get-NetTCPConnection -LocalPort $Port -State Listen |
    Select-Object -ExpandProperty OwningProcess -Unique
if ($pids) { Write-Output ($pids -join ',') }
exit 0
