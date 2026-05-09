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
echo [1/5] 清理旧产物...
if exist "build" rmdir /s /q "build"
if exist "dist" rmdir /s /q "dist"
if exist "%NAME%.spec" del /f /q "%NAME%.spec"
echo 清理完成

REM 2) 检查 venv
echo.
echo [2/5] 检查虚拟环境...
if not exist "venv\Scripts\python.exe" (
    echo 未找到 venv\Scripts\python.exe，请先运行:
    echo   python -m venv venv
    echo   venv\Scripts\pip install -r requirements.txt
    pause
    popd
    exit /b 1
)
call "venv\Scripts\activate.bat"
echo 虚拟环境: venv

REM 3) 确保 PyInstaller 已安装
echo.
echo [3/5] 检查 PyInstaller...
python -m pip show pyinstaller >nul 2>nul
if errorlevel 1 (
    echo 安装 PyInstaller...
    python -m pip install -U pyinstaller
)
echo PyInstaller 就绪

REM 4) 打包
echo.
echo [4/5] 开始打包...

python -m PyInstaller ^
  --noconsole ^
  --onefile ^
  --clean ^
  --name "%NAME%" ^
  --distpath "dist" ^
  --workpath "build\work" ^
  --specpath "build\spec" ^
  --add-data "templates;templates" ^
  --add-data "static;static" ^
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
  --hidden-import=pkg_resources ^
  --hidden-import=pkgutil ^
  "%PY_FILE%"

if errorlevel 1 (
    echo.
    echo 打包失败！常见原因：
    echo   1. 依赖未安装 - 运行 venv\Scripts\pip install -r requirements.txt
    echo   2. 模块缺失 - 根据报错添加 --hidden-import
    pause
    popd
    exit /b 1
)

echo.
echo [5/5] 打包成功，清理中间产物...
if exist "build" rmdir /s /q "build"
for /d /r %%D in (__pycache__) do @if exist "%%D" rmdir /s /q "%%D"

echo.
echo ============================================================
echo 输出文件: dist\%NAME%.exe
echo.
echo 使用说明:
echo   1. 将 dist\%NAME%.exe 复制到目标机器
echo   2. 首次运行会自动在同目录生成 .env 文件
echo   3. data\ 目录（数据库）和 .env 文件需保留在 exe 旁边
echo ============================================================
echo.

popd
pause
endlocal
