@echo off
setlocal
cd /d "%~dp0"

set PORT=8501
set URL=http://localhost:%PORT%

echo ==========================================================
echo    電腦硬體 CP 值配置推薦 Demo
echo ==========================================================
echo.

REM ---------- 1. 檢查 Python ----------
python --version >nul 2>&1
if errorlevel 1 (
    echo [錯誤] 找不到 Python。
    echo.
    echo 請先安裝 Python 3.9 以上版本：
    echo     https://www.python.org/downloads/
    echo 安裝時務必勾選「Add Python to PATH」，裝完後重新執行這個檔案。
    echo.
    pause
    exit /b 1
)
for /f "delims=" %%v in ('python --version') do echo [1/3] 找到 %%v

REM ---------- 2. 檢查專案檔案 ----------
if not exist "server\main.py" (
    echo [錯誤] 找不到 server\main.py。
    echo 請確認這個批次檔放在專案資料夾的最上層。
    echo.
    pause
    exit /b 1
)
if not exist "web\dist\index.html" (
    echo [錯誤] 找不到建置好的前端 web\dist\index.html。
    echo 請在裝有 Node.js 的電腦上，於 web 資料夾執行：
    echo     npm install
    echo     npm run build
    echo 再把整個專案資料夾複製過來。
    echo.
    pause
    exit /b 1
)

REM ---------- 3. 檢查相依套件 ----------
python -c "import fastapi, uvicorn, pandas" >nul 2>&1
if errorlevel 1 (
    echo [2/3] 缺少必要套件，開始安裝...
    echo       第一次執行需要幾分鐘，請不要關閉視窗。
    echo.
    python -m pip install -r requirements.txt
    if errorlevel 1 (
        echo.
        echo [錯誤] 套件安裝失敗。請檢查網路連線後再試一次。
        echo.
        pause
        exit /b 1
    )
    echo.
    echo       安裝完成。
) else (
    echo [2/3] 相依套件齊全。
)

REM LLM 功能用的 anthropic 套件是選用的：裝不起來（離線、Python 3.9）只會停用 LLM，不影響推薦
python -c "import importlib.util, sys; sys.exit(sys.version_info >= (3, 10) and importlib.util.find_spec('anthropic') is None)" >nul 2>&1
if errorlevel 1 (
    echo       選用套件 anthropic 未安裝，嘗試安裝（LLM 功能需要）...
    python -m pip install -q --retries 0 --timeout 5 "anthropic>=1.11" >nul 2>&1
    if errorlevel 1 echo       安裝失敗，LLM 功能停用；推薦功能不受影響。
)

REM ---------- 4. 啟動 ----------
echo [3/3] 啟動中，稍候會自動開啟瀏覽器：%URL%
echo.
echo       要結束請按 Ctrl+C，或直接關閉這個視窗。
echo ==========================================================
echo.

REM 背景等 3 秒讓伺服器就緒，再開瀏覽器。
start "" /b powershell -NoProfile -Command "Start-Sleep 3; Start-Process '%URL%'"

REM FastAPI 同時送出 API 與建置好的前端（web\dist），只需要這一個程序
python -m uvicorn server.main:app --port %PORT%

if errorlevel 1 (
    echo.
    echo [錯誤] 啟動失敗。最常見的原因是連接埠 %PORT% 已被佔用，
    echo        可能是上一次的視窗還開著，關掉它再試一次。
)

echo.
echo 已結束。
pause
