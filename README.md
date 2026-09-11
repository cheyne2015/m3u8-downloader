# M3U8 下载器 v2

面向 Windows 的图形化网页视频与 M3U8 下载工具。v2 使用 PySide6 重建任务界面，把一个网页或一条 M3U8 直链作为父任务管理，并把网页中提取到的每条 M3U8 作为独立下载项处理。

它适合需要批量提取网页视频、选择清晰度、稳定续传以及长期管理下载记录的场景。

## 下载

从 [GitHub Releases](https://github.com/cheyne2015/m3u8-downloader/releases/latest) 下载 Windows x64 压缩包，解压后运行：

```text
m3u8-downloader-v2\m3u8-dl-v2.exe
```

发布版是文件夹程序，`_internal` 中包含 Qt 运行库、程序资源和 ffmpeg。请保留整个文件夹，不要只复制 EXE。

## 主要功能

- 支持网页链接和 M3U8 直链。
- 智能提取默认深度模式优先，失败或没有结果时自动回退普通解析。
- 提取结果流式出现，无需等待整个网页扫描结束。
- 可以在网页仍在提取时选择已有候选并开始下载，提取继续在后台完成。
- 多个父任务并发下载，每个父任务内部按顺序下载子项。
- M3U8 分片并发、AES-128 解密、断点续传、HTTP Range 和失败重试。
- 优先使用发布包内的 ffmpeg 合并为 MP4。
- 下载中、已完成、任务详情、搜索、状态筛选和完整右键菜单。
- 深色、浅色和跟随 Windows 三种主题。
- SQLite 持久化任务、下载项、队列顺序、设置和日志。
- Windows 单实例、系统托盘及可选的下载完成通知。

## 使用流程

### 快速创建任务

在主界面的“快速下载”栏粘贴网页或 M3U8 地址，按回车或点击“快速开始”。程序使用上一次保存目录和当前默认设置创建任务。

需要批量链接、指定保存目录或单独设置代理时，点击“新建链接”：

1. 每行粘贴一个网页或 M3U8 地址。
2. 选择保存目录。
3. 根据需要展开高级选项。
4. 点击“开始”。

M3U8 直链会直接进入下载队列；网页链接会先进入提取队列。

### 网页候选处理

提取自然结束或用户主动停止提取后，程序按照任务自己的自动下载阈值处理候选：

- 有效候选数量不超过阈值：全部自动进入下载队列。
- 有效候选数量超过阈值：任务进入“待选择”，由用户勾选。
- 默认阈值为 3，可在设置中调整为 1～20。
- 预估时长显示为相同整秒的候选只保留预估体积最大的一个；其余项不下载，也不计入阈值。
- 时长未知的候选不会被错误合并。

“停止提取”只结束网页扫描，并立即应用当前候选；停止后可以点击“重新提取”，旧候选会安全清理后重新扫描。

### 选择与操作

- 主任务列表点击空白处或按 `Esc` 可取消选择。
- 右侧下载项表格点击空白处或按 `Esc` 只取消行高亮，不改变下载勾选。
- “全选”支持未选、部分选中和全部选中三种状态，只影响尚未开始且可选择的项目。
- 暂停、继续、停止提取和下载选中项会根据任务状态自动启用或禁用。
- 任务完成并移入“已完成”后，旧选择和右侧详情会自动清空。

## 下载调度

默认设置：

| 设置 | 默认值 | 可选范围 |
|---|---:|---:|
| 同时下载父任务 | 3 | 1～6 |
| 同时提取网页 | 3 | 1～6 |
| 自动下载候选阈值 | 3 | 1～20 |
| 每个 M3U8 分片线程 | 8 | 1～32 |
| 单次网络请求重试 | 3 | 0～10 |
| 任务级自动重试 | 1 | 0～5 |
| 任务重试等待 | 30 秒 | 1～3600 秒 |

父任务并发、网页提取并发和分片线程相互独立。任务级重试等待、待选择和暂停任务不会占用下载槽位。

下载过程中会显示总体进度、当前速度、已下载大小、总大小和剩余时间。磁盘空间不足时，程序会阻止新任务启动或安全暂停当前任务。

## 文件命名

- 单个 M3U8：`任务名称.mp4`
- 多个 M3U8：创建网页标题文件夹，子文件为 `任务名称_01.mp4`、`任务名称_02.mp4`……
- 文件重名时自动追加 `(1)`、`(2)`……
- 下载前重命名父任务会更新尚未开始项的规划文件名。
- 下载后重命名父任务只改变界面名称；“重命名文件”才会修改磁盘文件。

## 深度网页提取

普通解析会扫描 HTML、媒体标签和页面脚本。SPA 或运行时生成地址的网站需要 Playwright 浏览器监听网络请求。

当前发布包为了控制体积，不内置 Playwright 和 Chromium。需要深度提取的电脑请安装 Python 3.13，然后执行：

```powershell
py -3.13 -m pip install playwright
py -3.13 -m playwright install chromium
```

程序会自动调用系统 Python 执行发布包内的 `deep_worker.py`。不可用时，界面会区分缺少 Python、Playwright 或浏览器内核，并在智能模式下尝试普通解析。

## 数据目录

默认数据目录：

```text
%LOCALAPPDATA%\m3u8-downloader\
```

其中 `tasks-v2.db` 保存任务、下载项、队列、设置和日志。v2 不迁移也不修改旧版 JSON 数据。

需要便携模式时，在 `m3u8-dl-v2.exe` 同级目录创建空文件：

```text
portable.flag
```

程序随后把数据写入程序目录下的 `data` 文件夹。

程序异常退出或系统断电后，未完成任务下次启动统一恢复为“已暂停”，不会自动联网。

## 从源码运行

环境要求：Windows 10/11、Python 3.13。

```powershell
git clone https://github.com/cheyne2015/m3u8-downloader.git
cd m3u8-downloader
py -3.13 -m pip install -r requirements.txt
py -3.13 -m m3u8_downloader.gui_launcher
```

启用深度模式：

```powershell
py -3.13 -m pip install -r requirements-deep.txt
py -3.13 -m playwright install chromium
```

## 测试

```powershell
py -3.13 -m pytest -q
```

v2.0.0 发布前验证结果为 `607 passed`，覆盖任务服务、SQLite、调度、网页提取、下载与续传、文件处理、Windows 集成和 PySide6 界面交互。

## 打包 Windows 程序

确保系统可以找到 ffmpeg，然后执行：

```powershell
py -3.13 -m PyInstaller build-v2.spec --clean --noconfirm
```

输出位置：

```text
dist\m3u8-downloader-v2\m3u8-dl-v2.exe
```

构建配置会收集 PySide6、ffmpeg、深度提取脚本和应用图标。发布前应从最终目录启动 EXE，并验证 SQLite 初始化、ffmpeg、Playwright 浏览器和实际网页提取。

## 项目结构

```text
m3u8_downloader/
├── gui_v2.py                   # PySide6 主界面与交互
├── background_v2.py            # 提取与下载后台调度
├── runtime_v2.py               # 数据目录和服务装配
├── extractor.py                # 普通/深度网页提取入口
├── deep_worker.py              # 冻结程序使用的深度提取进程
├── downloader.py               # 分片下载、续传和重试
├── downloader_adapter_v2.py    # v2 下载任务适配
├── ffmpeg_v2.py                # 随包 ffmpeg 定位
├── windows_v2.py               # Windows 单实例
└── tasking/
    ├── models.py               # 父任务与子下载项模型
    ├── repository.py           # SQLite 仓库
    ├── service.py              # 任务操作与状态转换
    ├── coordinator.py          # 智能提取和回退
    └── download_coordinator.py # 下载队列、重试与空间保护
```

更完整的行为约束见 [v2 产品规格](docs/v2_product_spec.md)，架构说明见 [v2 架构](docs/v2_architecture.md)。

## 使用说明

请只下载你有权访问和保存的内容，并遵守内容来源网站的服务条款及所在地法律。
