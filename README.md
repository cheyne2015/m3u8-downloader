# m3u8-downloader

本地 m3u8 下载工具，支持 TS 片段并发下载与 MP4 转换。

## 功能特性

- 解析 m3u8 播放列表（master/media playlist）
- 多码率自动选择（默认选最高码率）
- 多线程并发下载 TS 片段（默认 8 并发）
- AES-128 加密流解密支持
- 断点续传（已下载片段自动跳过）
- HTTP 重试机制（指数退避）
- 实时进度显示（进度条 + 速度 + ETA）
- 优先使用 ffmpeg 合并转码为 MP4
- ffmpeg 不可用时自动降级为 TS 二进制拼接
- 中文友好提示信息

## 安装

### 方式一：一键安装（Windows）

双击运行 `install.bat`，脚本将自动：
1. 检查 Python 环境
2. 安装 Python 依赖
3. 将 `m3u8-dl` 命令添加到 PATH
4. 检查 ffmpeg 是否可用

### 方式二：手动安装

```bash
# 安装依赖
pip install -r requirements.txt

# 以开发模式安装（添加 m3u8-dl 到 PATH）
pip install -e .
```

### ffmpeg（可选但推荐）

安装 ffmpeg 可获得更好的转码质量：
1. 下载：https://ffmpeg.org/download.html
2. 解压到任意目录
3. 将 `bin` 目录添加到系统 PATH

## 使用方法

### 基本用法

```bash
m3u8-dl https://example.com/index.m3u8
```

### 指定输出文件

```bash
m3u8-dl https://example.com/index.m3u8 -o video.mp4
```

### 调整并发数

```bash
m3u8-dl https://example.com/index.m3u8 -o video.mp4 -w 16
```

### 指定临时目录

```bash
m3u8-dl https://example.com/index.m3u8 -o video.mp4 --tmp-dir ./temp
```

### 不使用 ffmpeg

```bash
m3u8-dl https://example.com/index.m3u8 -o video.mp4 --no-ffmpeg
```

### 查看帮助

```bash
m3u8-dl --help
```

## 命令行参数

| 参数 | 说明 | 默认值 |
|------|------|--------|
| `url` | m3u8 播放列表 URL | - |
| `-o, --output` | 输出文件路径 | output.mp4 |
| `-w, --workers` | 并发下载线程数 | 8 |
| `--tmp-dir` | 临时文件目录 | 输出目录/.tmp |
| `--no-ffmpeg` | 不使用 ffmpeg 合并转码 | false |
| `--retries` | 下载失败重试次数 | 3 |
| `--timeout` | HTTP 请求超时（秒） | 30 |
| `-v, --version` | 显示版本号 | - |
| `--gui` | 启动图形界面 | false |

## 依赖

- `requests` - HTTP 请求
- `pycryptodome` - AES-128 解密
- `tqdm` - 进度条显示
- `pyinstaller` - EXE 打包（仅打包时需要）

## GUI 图形界面

### 启动方式

```bash
# 方式一：命令行参数
m3u8-dl --gui

# 方式二：直接启动 GUI 入口
m3u8-dl-gui

# 方式三：模块方式运行
python -m m3u8_downloader --gui
```

### 界面功能

- **URL 输入**：输入 m3u8 地址，支持一键粘贴
- **输出设置**：选择保存目录和文件名
- **打开目录**：点击「打开」按钮，在系统文件管理器中打开当前保存目录
- **记住保存位置**：勾选后记住当前保存目录，下次启动 GUI 自动填充（配置保存在 `~/.m3u8-downloader/gui_config.json`）
- **参数配置**：并发线程数、重试次数、超时时间、ffmpeg 选项、临时目录
- **进度显示**：实时进度条、下载速度、剩余时间
- **日志输出**：下载过程的详细日志
- **操作控制**：开始/停止下载

### 注意事项

- 下载在子线程执行，不会阻塞界面
- 停止下载会中断当前任务
- 错误信息在日志区显示，不会弹出报错框

## EXE 打包

### 一键打包（Windows）

双击运行 `build.bat`，脚本将自动：
1. 检查并安装 PyInstaller
2. 安装项目依赖
3. 执行打包生成 `dist/m3u8-dl.exe`

### 手动打包

```bash
# 安装 PyInstaller
pip install pyinstaller

# 执行打包
pyinstaller build.spec --clean --noconfirm
```

打包完成后，EXE 文件位于 `dist/m3u8-dl.exe`。

### EXE 使用

```bash
# 命令行模式
m3u8-dl.exe https://example.com/index.m3u8 -o video.mp4

# GUI 模式
m3u8-dl.exe --gui
```

## 项目结构

```
m3u8-downloader/
├── m3u8_downloader/        # 核心包
│   ├── __init__.py         # 包初始化
│   ├── __main__.py         # 模块运行入口
│   ├── cli.py              # CLI 入口
│   ├── gui.py              # GUI 图形界面
│   ├── downloader.py       # 核心下载逻辑
│   ├── parser.py           # m3u8 解析器
│   ├── merger.py           # TS 合并 + MP4 转换
│   └── utils.py            # 工具函数
├── tests/                  # 测试目录
├── setup.py                # 安装配置
├── requirements.txt        # 依赖
├── build.spec              # PyInstaller 打包配置
├── build.bat               # Windows 一键打包脚本
├── install.bat             # Windows 一键安装脚本
└── README.md               # 使用说明
```
