# m3u8 下载器 v2 候选版打包

在项目根目录使用系统 Python 3.13：

```powershell
py -3.13 -m pip install -r requirements.txt
py -3.13 -m PyInstaller --distpath dist-v2 --workpath build-v2 build-v2.spec
```

输出目录为 `dist-v2\m3u8-downloader-v2`。正式候选包只包含图形程序
`m3u8-dl-v2.exe`，并在 `_internal` 目录携带已验证的 `ffmpeg.exe`。程序会优先使用
这个随包版本。深度提取继续通过随包 `deep_worker.py` 调用系统 Python 与 Playwright 浏览器资源。

打包后至少验证：

1. `_internal\ffmpeg.exe -version` 可正常返回版本。
2. 双击 `m3u8-dl-v2.exe` 后主窗口可见，重复启动只激活已有窗口。
3. 新建直链任务后可下载、合并并进入“已完成”。
4. 新建网页任务后智能模式可深度提取，并在失败时回退普通模式。
5. 关闭后重新打开，未完成任务恢复为“已暂停”。
