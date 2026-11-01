# m3u8 下载器正式版打包

在项目根目录使用系统 Python 3.13：

```powershell
py -3.13 -m pip install -r requirements.txt
py -3.13 -m PyInstaller --distpath dist-v2 --workpath build-v2 build-v2.spec
```

输出目录为 `dist-v2\m3u8-downloader`。正式包只包含图形程序
`m3u8-dl.exe`，并在 `_internal` 目录携带已验证的 `ffmpeg.exe`。程序会优先使用
这个随包版本。正式深度提取使用独立的 `deep-runtime/deep-worker.exe`，组件包含 Python、Playwright 和 Chromium 无界面内核，不依赖用户系统安装的 Python 或浏览器。主程序与深度组件分开打包，解压到同一位置。

构建深度组件时，将 `M3U8_PLAYWRIGHT_BROWSERS_PATH` 设为包含 `chromium_headless_shell-*` 的目录，再运行：

```powershell
py -3.13 -m PyInstaller --noconfirm --distpath dist-deep --workpath build-deep build-deep-runtime.spec
```

发布目录结构：

```text
m3u8-downloader/
  m3u8-dl.exe
  _internal/
  deep-runtime/
    deep-worker.exe
    runtime.json
    browsers/
```

打包后至少验证：

1. `_internal\ffmpeg.exe -version` 可正常返回版本。
2. 双击 `m3u8-dl.exe` 后主窗口可见，重复启动只激活已有窗口。
3. 新建直链任务后可下载、合并并进入“已完成”。
4. 新建网页任务后智能模式可深度提取，并在失败时回退普通模式。
5. 关闭后重新打开，未完成任务恢复为“已暂停”。
