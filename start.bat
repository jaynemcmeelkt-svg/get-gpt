@echo off
setlocal EnableExtensions
cd /d "%~dp0"
title GET-GPT Control Console

set BACKEND_PORT=18000
set FRONTEND_PORT=15173
set MODE=%1
if "%MODE%"=="" set MODE=dev

echo ===================================================
echo          GET-GPT Control Console Startup
echo ===================================================
echo.
echo [INFO] Mode: %MODE%
echo [INFO] Backend Port: %BACKEND_PORT%
echo [INFO] Frontend Port: %FRONTEND_PORT%
echo.

where python >nul 2>nul
if %errorlevel% neq 0 (
    echo [ERROR] Python not found. Please install Python and add it to PATH.
    pause
    exit /b 1
)

if exist "venv\Scripts\activate.bat" (
    echo [INFO] Activating venv...
    call venv\Scripts\activate.bat
)
if exist ".venv\Scripts\activate.bat" if not exist "venv\Scripts\activate.bat" (
    echo [INFO] Activating .venv...
    call .venv\Scripts\activate.bat
)

echo [INFO] Checking Python dependencies...
python -m pip install fastapi uvicorn httpx camoufox curl_cffi pydantic
if %errorlevel% neq 0 goto error_exit

if /i "%MODE%"=="dev" goto dev_mode
if /i "%MODE%"=="prod" goto prod_mode
if /i "%MODE%"=="build" goto build_mode

echo [ERROR] Unknown mode: %MODE%
echo Usage:
echo   start.bat dev    - backend + Vite hot reload frontend
echo   start.bat prod   - build frontend, then serve with FastAPI
echo   start.bat build  - build frontend only
pause
exit /b 1

:dev_mode
call :check_port %BACKEND_PORT% "FastAPI Backend"
if errorlevel 1 goto port_error
call :check_port %FRONTEND_PORT% "Vite Frontend"
if errorlevel 1 goto port_error

where npm >nul 2>nul
if %errorlevel% neq 0 (
    echo [ERROR] npm not found. Please install Node.js.
    pause
    exit /b 1
)

echo [INFO] Installing frontend dependencies...
cd /d "%~dp0web"
if not exist "node_modules" npm install
if %errorlevel% neq 0 goto error_exit

echo [INFO] Starting backend at http://127.0.0.1:%BACKEND_PORT%
start "GET-GPT Backend %BACKEND_PORT%" cmd /k "cd /d %~dp0 && python -m sms_flow_project.main"

echo [INFO] Starting Vite frontend at http://127.0.0.1:%FRONTEND_PORT%
start "GET-GPT Frontend %FRONTEND_PORT%" cmd /k "cd /d %~dp0web && npm run dev -- --host 127.0.0.1 --port %FRONTEND_PORT% --strictPort"

echo.
echo [OK] Development mode started.
echo [OPEN] http://127.0.0.1:%FRONTEND_PORT%
echo.
pause
exit /b 0

:prod_mode
call :check_port %BACKEND_PORT% "FastAPI Backend"
if errorlevel 1 goto port_error
call :build_frontend
if %errorlevel% neq 0 goto error_exit
cd /d "%~dp0"
echo [INFO] Starting production service...
echo [OPEN] http://127.0.0.1:%BACKEND_PORT%
echo.
python -m sms_flow_project.main
goto end

:build_mode
call :build_frontend
goto end

:build_frontend
where npm >nul 2>nul
if %errorlevel% neq 0 (
    echo [ERROR] npm not found. Please install Node.js.
    exit /b 1
)
cd /d "%~dp0web"
echo [INFO] Installing frontend dependencies...
if not exist "node_modules" npm install
if %errorlevel% neq 0 exit /b 1
echo [INFO] Building Vite frontend...
npm run build
exit /b %errorlevel%

:check_port
set PORT=%~1
set NAME=%~2
for /f "tokens=5" %%a in ('netstat -ano ^| findstr /R /C:":%PORT% .*LISTENING"') do (
    echo [ERROR] %NAME% port %PORT% is already in use. PID=%%a
    echo [ERROR] Stop that process or change BACKEND_PORT/FRONTEND_PORT in this script.
    exit /b 1
)
exit /b 0

:port_error
echo.
echo [ERROR] Port check failed. Startup stopped to avoid blank/black screen.
pause
exit /b 1

:error_exit
echo.
echo [ERROR] Startup failed.
pause
exit /b 1

:end
if %errorlevel% neq 0 (
    echo.
    echo [ERROR] Service exited with error.
    pause
)
