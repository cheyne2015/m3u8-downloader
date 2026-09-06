# m3u8-downloader

本地 m3u8 下载工具，支持 TS 片段并发下载与 MP4 转换。

## 功能特性

- 解析 m3u8 播放列表（master/media playlist）
- 多码率自动选择（默认选最高码率）
- 多线程并发下载 TS 片段（默认 8 并发）
- AES-128 加密流解密支持
- 安全断点续传（`.part` 原子片段 + HTTP Range，播放列表变化时自动隔离旧缓存）
- HTTP 重试机制（播放列表、密钥和片段均支持指数退避；停止时立即中断等待）
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
| `--from-page` | 把位置参数当作网页地址，先抽取页内 m3u8 再下载 | false |
| `--deep` | 使用无头浏览器深度抽取（隐含 --from-page，需 playwright） | false |
| `--pick` | 非交互选择序号：1,3 / 1-3 / 1,3-5 / all | 空（交互输入） |
| `--list-only` | 只列出候选与估计大小，不下载 | false |
| `--no-estimate` | 跳过大小估计（秒出列表） | false |
| `--extract-workers` | 抽取/估算并发数（1-16） | 8 |

## 从网页抽取 m3u8（CLI）

除了直接传 m3u8 直链，还可以**粘贴一个包含 m3u8 的网页 URL**，工具会列出页内所有 m3u8 链接（含估计大小/时长/码率），由你选择下载。

```bash
# 抽取并进入交互式选择
m3u8-dl https://site.com/play/123 --from-page

# 只看列表，不下载
m3u8-dl https://site.com/play/123 --from-page --list-only

# 非交互：直接选 1 和 3 下载
m3u8-dl https://site.com/play/123 --from-page --pick 1,3 -o video.mp4

# 全选
m3u8-dl https://site.com/play/123 --from-page --pick all

# 无头浏览器深度模式（SPA / 运行时拼 URL 的站点）
m3u8-dl https://site.com/play/123 --deep --pick all
```

选择序号语法（`--pick`）：

- `1,3` 选中第 1、3 个
- `1-3` 选中第 1 到第 3 个
- `1,3-5` 混合
- `all` 全选
- 不带 `--pick` 且为终端（TTY）时进入交互式输入；非交互环境（管道/CI）会提示用 `--pick` 并退出码 2

列表输出示例：

```
序号  估计大小      时长      码率        类型    来源  标题/URL
[1]   ≈ 1.21 GB   01:32:10  2.5 Mbps    master  html  1080P
      https://cdn.x/hls/1080/index.m3u8
[2]   ≈ 620.4 MB  01:32:08  1.2 Mbps    media   js    -
      https://cdn.x/hls/720/index.m3u8
[3]   未知         -         -           -       js    (不可达)
      https://cdn.x/hls/dead.m3u8
共 3 个候选（大小为估计值）
```

> ⚠️ 大小为**估计值**（基于码率×时长或抽样片段大小），实际文件可能偏差；不可达链接（403/超时）显示「未知」。

### 深度模式（可选，需 playwright）

默认是**静态解析**（拉 HTML + 扫描 `<source>/<video>/<a>` 标签与内外链 JS）。若页面是 SPA、m3u8 由播放器运行时生成，可开启深度模式（无头浏览器监听网络请求中的 .m3u8）：

```bash
pip install -r requirements-deep.txt
playwright install chromium
m3u8-dl https://site.com/play/123 --deep
```

深度模式**不进主依赖、不进 EXE**，缺失时给出明确的安装提示而不会崩溃。

> **EXE 的深度模式怎么跑通？** 两条路线自动择优，无需额外配置：
>
> 1. **进程内**：源码运行时当前 Python 装了 playwright，直接跑（最快）；
> 2. **子进程**：冻结 EXE 内无法 import 外部 site-packages，改为调用系统 Python
>    （`py -3.13` / `python`）执行随包分发几 KB 的 `deep_worker.py`，复用本机
>    playwright 与 Chromium 浏览器，因此 EXE 体积不受影响。
>
> 两者都不可用时，提示会区分具体原因（缺解释器 / 缺 playwright / 缺浏览器内核）。

## 依赖

- `requests` - HTTP 请求
- `pycryptodome` - AES-128 解密
- `tqdm` - 进度条显示
- `beautifulsoup4` - 网页 HTML 解析（静态抽取，仅主依赖，用内置 html.parser）
- `pyinstaller` - EXE 打包（仅打包时需要）

> 深度模式（无头浏览器）为**可选**依赖，见 `requirements-deep.txt`：
> `pip install -r requirements-deep.txt && playwright install chromium`

## GUI 图形界面

### 启动方式

```bash
# 方式一：双击 m3u8-dl.exe（GUI 版，无黑窗口，文件名固定）
m3u8-dl.exe

# 方式二：Python 模块方式（需 pip install -e .）
python -m m3u8_downloader --gui
```

### 界面功能

- **URL 输入**：地址框支持「m3u8 直链」或「网页地址」，支持一键粘贴
- **提取网页**：粘贴网页地址后点击「提取网页」，工具列出页内所有 m3u8（含估计大小），抽取在后台线程进行不卡界面
- **网页提取结果**：Treeview 展示序号 / 估计大小 / 时长 / 码率 / 类型 / 标题 / 链接；单击即可切换多选，并提供全选、取消选择、复制链接和数量统计
- **下载选中**：选中一行或多行后点击「下载选中」，多选时**串行**下载，文件名自动加 `_序号`
- **深度模式**：参数区可勾选「深度模式（需 playwright）」，未安装时该选项自动禁用
- **输出设置**：选择保存目录和文件名
- **打开目录**：点击「打开」按钮，在系统文件管理器中打开当前保存目录
- **记住保存位置**：勾选后记住当前保存目录，下次启动 GUI 自动填充（配置保存在 `~/.m3u8-downloader/gui_config.json`）
- **参数配置**：并发线程数、重试次数、超时时间、ffmpeg 选项、临时目录
- **预载状态**：下载期间可提取下一网页，链接和标题会在当前下载结束后一起显示
- **进度显示**：实时进度条、下载速度、剩余时间、当前完整标题和实际保存文件名
- **日志输出**：下载过程的详细日志始终显示
- **操作控制**：下载与网页提取分别停止；已经完整写入的片段可安全续传复用

### 注意事项

- 下载在子线程执行，不会阻塞界面
- 停止下载会中断当前任务
- 错误信息在日志区显示，不会弹出报错框

### 从网页提取 m3u8（GUI）

1. 在「地址（m3u8 / 网页）」框粘贴网页 URL；
2. 点击「提取网页」，稍候在「网页提取结果」列表中看到候选（大小均为估计值）；
3. 单击一行切换选择，可连续选择多行；双击可把该链接填回地址框直接下载，也可点「下载选中」批量下载；
4. 需要时可先勾选「深度模式」再点「提取网页」（需先安装 playwright，见上方 CLI 深度模式说明）。

## EXE 打包

### 一键打包（Windows）

双击运行 `build.bat`，脚本将自动：
1. 检查并安装 PyInstaller
2. 安装项目依赖
3. 执行打包生成 `dist/m3u8-dl.exe`（GUI）与 `dist/m3u8-dl-cli.exe`（CLI）

### 手动打包

```bash
# 安装 PyInstaller
pip install pyinstaller

# 执行打包
pyinstaller build.spec --clean --noconfirm
```

打包完成后，`dist/` 目录下生成 `m3u8-dl.exe`（图形界面版，无控制台窗口）与 `m3u8-dl-cli.exe`（命令行版，保留控制台进度输出）。

### EXE 使用

打包后 `dist/` 目录会生成两个 EXE：

- **`m3u8-dl.exe`** —— 图形界面版，**双击即可运行且不会弹出黑色终端窗口**（文件名固定为 m3u8-dl.exe，推荐日常 GUI 使用）。
- **`m3u8-dl-cli.exe`** —— 命令行版，保留控制台窗口用于实时进度输出（适合在终端/批处理中调用）。

```bash
# GUI 模式（双击 m3u8-dl.exe，无黑窗口）
m3u8-dl.exe

# 命令行模式（保留进度输出）
m3u8-dl-cli.exe https://example.com/index.m3u8 -o video.mp4
```

## 项目结构

```
m3u8-downloader/
├── m3u8_downloader/        # 核心包
│   ├── __init__.py         # 包初始化
│   ├── __main__.py         # 模块运行入口（CLI）
│   ├── gui_launcher.py      # GUI 专用启动入口（无控制台 EXE）
│   ├── cli.py              # CLI 入口
│   ├── gui.py              # GUI 图形界面
│   ├── extractor.py        # 网页 m3u8 抽取（HTML/JS 扫描 + 深度模式接口）
│   ├── estimator.py        # m3u8 大小/时长/码率估算（并发）
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
