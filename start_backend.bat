@echo off
setlocal enabledelayedexpansion
title AI 拍纸立得 - 后端服务
cd /d "%~dp0"

set "PORT=8000"
set "HOST=0.0.0.0"
set "OPEN_BROWSER=1"
if /i "%~1"=="-nobrowser" set "OPEN_BROWSER=0"

echo ============================================================
echo   AI 拍纸立得 - 图片转 PDF  ^|  本地后端服务
echo ============================================================
echo   项目目录 : %CD%
echo   监听地址 : %HOST%:%PORT%
echo.

rem ================= 1. 检查 Python =================
where python >nul 2>nul
if errorlevel 1 (
  echo [x] 找不到 python 命令
  echo     请先安装 Python 3.9+，安装时务必勾选 Add python.exe to PATH
  echo     下载地址: https://www.python.org/downloads/
  echo.
  pause
  exit /b 1
)

rem ================= 2. 检查依赖与 PDF 引擎 =================
set "PYVER=?"
set "ENGINE=?"
set "DEPS=?"
set "MISSING="
for /f "usebackq tokens=1,* delims==" %%a in (`python "scripts\check_env.py"`) do set "%%a=%%b"

if /i not "!DEPS!"=="ok" (
  echo [警告] 缺少依赖: !MISSING!
  echo     正在安装 requirements.txt ...
  echo.
  python -m pip install -r requirements.txt
  if errorlevel 1 (
    echo.
    echo [x] 依赖安装失败。请检查网络，或改用国内镜像:
    echo     python -m pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple
    echo.
    pause
    exit /b 1
  )
  echo.
  python "scripts\check_env.py" >nul 2>nul
  if errorlevel 1 (
    echo [x] 依赖装完仍不可用，请手动执行: python scripts\check_env.py
    echo.
    pause
    exit /b 1
  )
)

echo [OK] Python !PYVER!
echo [OK] 依赖就绪
if "!ENGINE!"=="none" (
  echo [x] 既没有 PyMuPDF 也没有 ReportLab，无法生成 PDF
  echo     python -m pip install PyMuPDF
  echo.
  pause
  exit /b 1
)
echo [OK] PDF 引擎: !ENGINE!

rem ================= 3. 检查端口占用 =================
set "BUSYPID="
for /f "usebackq delims=" %%i in (`powershell -NoProfile -ExecutionPolicy Bypass -File "scripts\net_info.ps1" -Mode portpid -Port %PORT%`) do set "BUSYPID=%%i"
if not "!BUSYPID!"=="" (
  echo.
  echo [警告] 端口 %PORT% 已被占用，占用进程 PID = !BUSYPID!
  echo     通常是上一次的后端还没退出。
  echo.
  choice /c YN /n /m "是否强制结束该进程并继续? [Y=是 / N=退出] "
  if errorlevel 2 (
    echo.
    echo 已退出。你可以直接访问已在运行的后端:
    echo     http://127.0.0.1:%PORT%/docs
    echo.
    pause
    exit /b 0
  )
  taskkill /PID !BUSYPID! /F /T >nul 2>nul
  if errorlevel 1 (
    echo.
    echo [x] 结束进程失败，请以管理员身份重试，或手动执行:
    echo     taskkill /PID !BUSYPID! /F /T
    echo.
    pause
    exit /b 1
  )
  echo [OK] 已结束 PID !BUSYPID!
  timeout /t 2 >nul
)

rem ================= 4. 识别局域网 IP（真机调试用） =================
set "LANIP="
for /f "usebackq delims=" %%i in (`powershell -NoProfile -ExecutionPolicy Bypass -File "scripts\net_info.ps1" -Mode lanip`) do set "LANIP=%%i"

echo.
echo ------------------------------------------------------------
echo  【微信开发者工具 - 模拟器】 ENV = 'local'，无需改动
echo     后端地址  : http://127.0.0.1:%PORT%
echo     健康检查  : http://127.0.0.1:%PORT%/health
echo     接口文档  : http://127.0.0.1:%PORT%/docs
echo.
if not "!LANIP!"=="" (
  echo  【真机预览 / 真机调试】 ENV = 'local-device'
  echo     把 miniapp\pages\index\index.js 里的 LAN_IP 改成:
  echo         const LAN_IP = '!LANIP!';
  echo     手机浏览器先打开 http://!LANIP!:%PORT%/health，能返回 JSON 才算通
  echo     手机与电脑需同一 Wi-Fi，且防火墙需放行 python.exe
) else (
  echo  【真机调试】未识别到局域网 IP，请执行 ipconfig 手动查看
)
echo ------------------------------------------------------------
echo  PDF 输出目录: static\pdfs\    暂存目录: var\uploads\
echo  按 Ctrl+C 停止服务
echo ============================================================
echo.

rem ================= 5. 启动服务 =================
if "%OPEN_BROWSER%"=="1" start "" /min cmd /c "timeout /t 5 >nul & start http://127.0.0.1:%PORT%/docs"

python -m uvicorn main:app --host %HOST% --port %PORT% --reload

echo.
echo 服务已停止。
if not "!LANIP!"=="" echo 真机调试记录: LAN_IP = '!LANIP!'
pause
