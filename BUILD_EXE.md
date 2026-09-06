# 从完整优化源码打包 EXE

## 包内内容

源码包是完整项目快照，不是补丁。它包含：

- `m3u8_downloader/`：全部优化后的程序代码
- `tests/`：完整回归测试
- `build.spec`：同时生成 CLI 和 GUI 两个 EXE 的 PyInstaller 配置
- `build.bat`：Windows 一键打包脚本
- `requirements.txt`：运行和打包依赖
- `requirements-deep.txt`：深度网页提取所需的外部 Playwright 环境
- `HANDOFF_TO_PROJECT_AI.md`：全部改动、提交和产品约束
- `docs/`：性能与稳定性验证证据

## 建议打包流程

在源码包解压目录打开 PowerShell：

```powershell
py -3.13 -m pip install -r requirements.txt
py -3.13 -m pytest -q -W error
py -3.13 -m PyInstaller build.spec --clean --noconfirm
```

或者双击 `build.bat`。

成功后生成：

- `dist\m3u8-dl.exe`：GUI 版本，无控制台窗口
- `dist\m3u8-dl-cli.exe`：命令行版本

## 深度模式环境

当前 `build.spec` 有意不把 Playwright 和 Chromium 塞进 EXE。深度模式通过随包附带的 `deep_worker.py` 调用本机可用的 Python。打包机器或使用机器需要另外执行：

```powershell
py -3.13 -m pip install -r requirements-deep.txt
py -3.13 -m playwright install chromium
```

打包后应至少验证：

```powershell
.\dist\m3u8-dl-cli.exe --version
.\dist\m3u8-dl-cli.exe --help
```

然后启动 `dist\m3u8-dl.exe`，检查普通提取、深度提取、开始/停止、预载和日志显示。不要直接覆盖用户现有正式版；先在隔离目录验证候选 EXE。
