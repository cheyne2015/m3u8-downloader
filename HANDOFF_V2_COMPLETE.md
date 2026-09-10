# m3u8 下载器 v2 完整交接

## 交付位置

- 完整源码工作树：`C:\Users\cheyn\WorkBuddy\2026-06-15-13-47-25\m3u8-downloader-v2`
- Git 分支：`rebuild/thunder-gui-v2`
- 产品规格：`docs\v2_product_spec.md`
- 架构说明：`docs\v2_architecture.md`
- 打包配置：`build-v2.spec`
- 打包说明：`BUILD_V2.md`

原工作树没有被覆盖，也不迁移旧版任务数据。v2 默认把数据库放在
`%LOCALAPPDATA%\m3u8-downloader\tasks-v2.db`；程序目录存在 `portable.flag` 时改用
程序目录下的 `data` 文件夹。

## 已实现的用户流程

程序采用 PySide6，主界面分为“下载中”“已完成”“设置”，右侧为父任务的下载项和
详细信息，底部日志始终可见。新建窗口支持网页链接、直接 m3u8 和每行一条的批量链接，
默认沿用上次保存目录。

网页任务默认执行智能深度优先提取。候选流式加入右侧列表；深度路线失败或没有结果时
回退普通路线。用户可在提取尚未结束时选择现有候选开始下载，之后发现的候选保持未选；
用户点击“停止提取”后，当前候选立即成为最终结果并应用任务自己的自动下载阈值。

父任务并发、网页提取并发和分片线程分别控制。父任务内部按顺序下载子项。下载支持
暂停、继续、跳过、失败重试、30 秒延迟的任务级自动重试、断点续传、磁盘空间保护、
单项或整任务重新下载，以及父任务全部完成后的可选 Windows 通知。任务级重试等待不占
父任务下载槽。

单 m3u8 输出为“任务名称.mp4”。多 m3u8 使用网页原标题建立文件夹，文件为
“任务名称_01.mp4、任务名称_02.mp4……”；重名自动添加 `(1)`。父任务重命名只改变任务
名称和尚未开始项目的规划文件名，“重命名文件”才会修改磁盘文件。

设置包含父任务并发 1～6（默认 3）、网页提取并发 1～6（默认 3）、自动下载阈值
1～20（默认 3）、分片线程、全局共享限速、请求重试、任务重试、日志保留、完成通知、
关闭到托盘和深色/浅色/跟随系统主题。每个任务可保存自己的代理、来源地址、浏览器标识、
登录信息、限速和重试参数；登录信息使用 Windows 当前用户 DPAPI 加密。

## 关键模块

- `m3u8_downloader\gui_v2.py`：主窗口、任务卡、新建窗口、设置、右键菜单和托盘。
- `m3u8_downloader\background_v2.py`：Qt 线程池与独立提取/下载调度。
- `m3u8_downloader\tasking\service.py`：父子任务状态转换和用户操作。
- `m3u8_downloader\tasking\repository.py`：SQLite 建表、迁移和事务写入。
- `m3u8_downloader\tasking\coordinator.py`：智能提取及回退。
- `m3u8_downloader\tasking\download_coordinator.py`：子项串行、重试与空间保护。
- `m3u8_downloader\downloader_adapter_v2.py`：旧下载核心、任务限速及全局公平限速适配。
- `m3u8_downloader\runtime_v2.py`：安装版/便携版数据目录。
- `m3u8_downloader\windows_v2.py`：单实例。
- `m3u8_downloader\secrets_v2.py`：DPAPI。
- `m3u8_downloader\ffmpeg_v2.py`：随包 ffmpeg 优先解析。

## 验证命令

先安装 `requirements.txt`，再执行：

```powershell
py -3.13 -m pytest tests/test_merger.py tests/test_utils.py tests/test_ffmpeg_v2.py tests/test_task_service.py tests/test_task_scheduler.py tests/test_task_coordinator.py tests/test_download_coordinator.py tests/test_output_planner.py tests/test_extractor_adapter.py tests/test_downloader_adapter.py tests/test_app_settings.py tests/test_runtime_v2.py tests/test_background_controller.py tests/test_gui_v2.py tests/test_task_logs.py tests/test_task_deletion.py tests/test_disk_space.py tests/test_secret_protection.py tests/test_single_instance.py -q

py -3.13 -m pytest -p no:pytest-qt tests/test_page_history.py tests/test_gui.py tests/test_multi_preload_queue.py tests/test_qa_auto_download.py tests/_qa_page_history_align_indep.py -q
```

第二条命令明确关闭 `pytest-qt`，因为旧版 Tk 测试和 Qt 测试插件同时加载会互相影响。

## 打包

```powershell
py -3.13 -m PyInstaller -y --distpath dist-v2 --workpath build-v2 build-v2.spec
```

成品目录是 `dist-v2\m3u8-downloader-v2`，入口是 `m3u8-dl-v2.exe`。打包配置会把
`ffmpeg.exe`、`deep_worker.py`、Playwright 所需 Python 模块和 Qt 运行库收入目录。
深度模式仍要求目标电脑安装与 Python Playwright 版本相匹配的 Chromium 浏览器资源。

打包后应验证入口可启动、重复启动只保留一个进程、`_internal\ffmpeg.exe -version`
可以执行、便携模式能创建 `data\tasks-v2.db`，再用一个实际网页和一个直接 m3u8 做端到端
下载。没有实际网站样本时，本地测试只能证明状态、调度和界面链路，不能替代目标网站验证。
