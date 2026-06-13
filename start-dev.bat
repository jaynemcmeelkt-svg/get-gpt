@echo off
setlocal EnableExtensions
cd /d "%~dp0"
title GET-GPT One Click Dev Startup

set BACKEND_PORT=18000
set FRONTEND_PORT=15173

echo ===================================================
echo        GET-GPT 一键前后端开发启动脚本
echo ===================================================
echo.
echo [INFO] 后端端口: %BACKEND_PORT%
echo [INFO] 前端端口: %FRONTEND_PORT%
echo.

call :check_port %BACKEND_PORT% "FastAPI 后端"
if errorlevel 1 goto port_error
call :check_port %FRONTEND_PORT% "Vite 前端"
if errorlevel 1 goto port_error

where python >nul 2>nul
if %errorlevel% neq 0 (
    echo [ERROR] 未找到 Python，请先安装 Python 并加入 PATH。
    pause
    exit /b 1
)

where npm >nul 2>nul
if %errorlevel% neq 0 (
    echo [ERROR] 未找到 npm，请先安装 Node.js。
    pause
    exit /b 1
)

if exist "venv\Scripts\activate.bat" (
    echo [INFO] 激活 venv...
    call venv\Scripts\activate.bat
)
if exist ".venv\Scripts\activate.bat" if not exist "venv\Scripts\activate.bat" (
    echo [INFO] 激活 .venv...
    call .venv\Scripts\activate.bat
)

echo.
echo [INFO] 检查后端依赖...
python -m pip install fastapi uvicorn httpx camoufox curl_cffi pydantic
if %errorlevel% neq 0 (
    echo [ERROR] 后端依赖安装失败。
    pause
    exit /b 1
)

echo.
echo [INFO] 检查前端依赖...
cd /d "%~dp0web"
if not exist "node_modules" (
    npm install
    if %errorlevel% neq 0 (
        echo [ERROR] 前端依赖安装失败。
        pause
        exit /b 1
    )
)

echo.
echo [INFO] 启动 FastAPI 后端：http://127.0.0.1:%BACKEND_PORT%
start "GET-GPT Backend %BACKEND_PORT%" cmd /k "cd /d %~dp0 && python -m sms_flow_project.main"

echo [INFO] 启动 Vite 前端热更新：http://127.0.0.1:%FRONTEND_PORT%
start "GET-GPT Frontend %FRONTEND_PORT%" cmd /k "cd /d %~dp0web && npm run dev -- --host 127.0.0.1 --port %FRONTEND_PORT% --strictPort"

echo.
echo ===================================================
echo [OK] 前后端启动命令已发出。
echo.
echo 开发热更新入口： http://127.0.0.1:%FRONTEND_PORT%
echo 后端 API 文档：   http://127.0.0.1:%BACKEND_PORT%/docs
echo.
echo 如果新窗口报错，请看对应窗口里的错误文本。
echo 关闭服务：分别在两个新打开的窗口里按 Ctrl+C。
echo ===================================================
echo.
pause
exit /b 0

:check_port
set PORT=%~1
set NAME=%~2
for /f "tokens=5" %%a in ('netstat -ano ^| findstr /R /C:":%PORT% .*LISTENING"') do (
    echo [ERROR] %NAME% 端口 %PORT% 已被占用，PID=%%a
    echo [ERROR] 请先关闭占用该端口的程序，或修改本脚本中的端口号。
    exit /b 1
)
exit /b 0

:port_error
echo.
echo [ERROR] 端口检测失败，已停止启动，避免打开后黑屏。
pause
exit /b 1
