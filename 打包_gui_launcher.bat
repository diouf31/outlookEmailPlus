@echo off
setlocal enabledelayedexpansion
chcp 65001 >nul

set "ROOT=%~dp0"
pushd "%ROOT%"

echo.
echo ===========老王打包工具，联系方式VX:17100666866,QQ:3579333333===========
echo.

set "PY_FILE=gui_launcher.py"
set "NAME=gui_launcher"

REM 1) 清理旧产物
echo [1/5] Cleaning old build artifacts...
if exist "build" rmdir /s /q "build"
if exist "dist" rmdir /s /q "dist"
if exist "%NAME%.spec" del /f /q "%NAME%.spec"
echo Clean done.

REM 2) 检查 / 创建 venv
echo.
echo [2/5] Checking venv...
if not exist "venv\Scripts\python.exe" (
    echo venv not found, creating...
    where python >nul 2>nul
    if errorlevel 1 (
        echo ERROR: python not found in PATH.
        pause
        popd
        exit /b 1
    )
    python -m venv venv
    if errorlevel 1 (
        echo ERROR: failed to create venv.
        pause
        popd
        exit /b 1
    )
    call "venv\Scripts\activate.bat"
    echo Installing requirements.txt ...
    python -m pip install -U pip
    python -m pip install -r requirements.txt
    if errorlevel 1 (
        echo ERROR: pip install failed.
        pause
        popd
        exit /b 1
    )
) else (
    call "venv\Scripts\activate.bat"
)
echo Using venv: venv

REM 3) 确保 PyInstaller 已安装
echo.
echo [3/5] Checking PyInstaller...
python -m pip show pyinstaller >nul 2>nul
if errorlevel 1 (
    echo Installing PyInstaller...
    python -m pip install -U pyinstaller
)
echo PyInstaller ready.

REM 4) 打包（add-data 必须用绝对路径，避免相对 specpath 解析失败）
echo.
echo [4/5] Building with PyInstaller...

python -m PyInstaller ^
  --noconsole ^
  --onefile ^
  --clean ^
  --name "%NAME%" ^
  --distpath "%ROOT%dist" ^
  --workpath "%ROOT%build\work" ^
  --specpath "%ROOT%build\spec" ^
  --add-data "%ROOT%templates;templates" ^
  --add-data "%ROOT%static;static" ^
  --hidden-import=outlook_web ^
  --hidden-import=outlook_web.app ^
  --hidden-import=outlook_web.db ^
  --hidden-import=outlook_web.config ^
  --hidden-import=outlook_web.errors ^
  --hidden-import=outlook_web.audit ^
  --collect-submodules=outlook_web ^
  --collect-submodules=flask ^
  --collect-submodules=werkzeug ^
  --collect-submodules=jinja2 ^
  --collect-submodules=flask_wtf ^
  --collect-submodules=flask_cors ^
  --collect-submodules=apscheduler ^
  --collect-submodules=cryptography ^
  --collect-submodules=bcrypt ^
  --collect-all=certifi ^
  --hidden-import=pkgutil ^
  "%ROOT%%PY_FILE%"

if errorlevel 1 (
    echo.
    echo Build FAILED. Common causes:
    echo   1. Missing deps - run: venv\Scripts\pip install -r requirements.txt
    echo   2. Missing module - add --hidden-import based on error
    pause
    popd
    exit /b 1
)

echo.
echo [5/5] Build OK, cleaning temp files...
if exist "build" rmdir /s /q "build"
for /d /r %%D in (__pycache__) do @if exist "%%D" rmdir /s /q "%%D"

echo.
echo ============================================================
echo Output: dist\%NAME%.exe
echo.
echo Usage:
echo   1. Copy dist\%NAME%.exe to target PC
echo   2. First run auto-creates .env beside the exe
echo   3. Keep data\ and .env next to the exe
echo ============================================================
echo.

popd
pause
endlocal
