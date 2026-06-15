@echo off
chcp 65001 >nul 2>&1
echo ========================================
echo   m3u8-downloader 安装脚本
echo ========================================
echo.

:: 检查 Python 是否安装
echo [1/4] 检查 Python 环境...
python --version >nul 2>&1
if %errorlevel% neq 0 (
    echo 错误: 未检测到 Python！
    echo 请先安装 Python 3.8+ : https://www.python.org/downloads/
    echo 安装时请勾选 "Add Python to PATH"
    pause
    exit /b 1
)

for /f "tokens=2 delims= " %%v in ('python --version 2^>^&1') do set PYTHON_VER=%%v
echo 检测到 Python %PYTHON_VER%

:: 检查 pip
echo.
echo [2/4] 检查 pip...
pip --version >nul 2>&1
if %errorlevel% neq 0 (
    echo 错误: pip 不可用！
    echo 请重新安装 Python 并确保勾选 pip
    pause
    exit /b 1
)
echo pip 可用

:: 安装依赖
echo.
echo [3/4] 安装 Python 依赖...
pip install -r requirements.txt
if %errorlevel% neq 0 (
    echo 错误: 依赖安装失败！
    echo 请检查网络连接或尝试使用国内镜像:
    echo   pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple
    pause
    exit /b 1
)
echo 依赖安装完成

:: 以开发模式安装本工具（添加 m3u8-dl 到 PATH）
pip install -e .
if %errorlevel% neq 0 (
    echo 警告: 开发模式安装失败，你可能需要手动运行:
    echo   pip install -e .
) else (
    echo m3u8-dl 命令已安装到 PATH
)

:: 检查 ffmpeg
echo.
echo [4/4] 检查 ffmpeg...
ffmpeg -version >nul 2>&1
if %errorlevel% neq 0 (
    echo 警告: 未检测到 ffmpeg！
    echo.
    echo 不安装 ffmpeg 也可以使用本工具（使用 TS 二进制拼接方式），
    echo 但安装 ffmpeg 可以获得更好的转码质量。
    echo.
    echo 安装方法:
    echo   1. 下载 ffmpeg: https://ffmpeg.org/download.html
    echo   2. 解压到任意目录
    echo   3. 将 ffmpeg 的 bin 目录添加到系统 PATH
    echo   4. 重新打开命令行窗口
) else (
    echo 检测到 ffmpeg 可用
)

echo.
echo ========================================
echo   安装完成！
echo ========================================
echo.
echo 使用方法:
echo   m3u8-dl https://example.com/index.m3u8 -o video.mp4
echo   m3u8-dl https://example.com/index.m3u8 -o video.mp4 -w 16
echo   m3u8-dl --help
echo.
pause
