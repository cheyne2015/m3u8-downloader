@echo off
chcp 65001 >nul 2>&1
echo ==========================================
echo   m3u8-downloader EXE 打包脚本
echo ==========================================
echo.

:: 检查 Python 是否可用
python --version >nul 2>&1
if errorlevel 1 (
    echo [错误] 未检测到 Python，请先安装 Python 3.8+
    pause
    exit /b 1
)

:: 检查 PyInstaller 是否安装
python -c "import PyInstaller" >nul 2>&1
if errorlevel 1 (
    echo [信息] PyInstaller 未安装，正在自动安装...
    pip install pyinstaller>=6.0.0
    if errorlevel 1 (
        echo [错误] PyInstaller 安装失败
        pause
        exit /b 1
    )
)

:: 检查项目依赖
echo [信息] 检查项目依赖...
pip install -r requirements.txt --quiet

:: 执行打包
echo.
echo [信息] 开始打包...
pyinstaller build.spec --clean --noconfirm

if errorlevel 1 (
    echo.
    echo [错误] 打包失败！请检查错误信息。
    pause
    exit /b 1
)

echo.
echo ==========================================
echo   打包完成！
echo   输出文件位置: dist\m3u8-dl.exe
echo ==========================================
echo.
echo 使用方法：
echo   m3u8-dl.exe https://example.com/index.m3u8 -o video.mp4
echo   m3u8-dl.exe --gui
echo.
pause
