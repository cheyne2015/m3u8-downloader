# m3u8-dl 优化交接说明

## 接手范围

- 项目目录：`C:\Users\cheyn\WorkBuddy\2026-06-15-13-47-25\m3u8-downloader`
- 优化前基线：`35baa0b4c4d185a49caa80d42eca23b545c0f2a8`
- 当前结果：`ee76ce5b441cb092c5d68f49e87b25c1db660ba3`
- 完整审阅范围：`35baa0b..ee76ce5`
- 当前工作区应保持干净。
- 本轮只优化和验证源码，没有打包或替换正式版 EXE。
- 交付的 ZIP 是完整源码快照，不是差异补丁；解压后可直接按 `BUILD_EXE.md` 打包。

## 用户已经确认的产品要求

1. 网页提取速度以深度模式为主。
2. 下载速度和稳定性需要同步优化。
3. 日志必须始终显示，暂时不要日志折叠，因为用户通过日志确认所有状态。
4. 下载期间允许预载下一网页，但预载链接和标题都不能提前回填；必须等当前下载结束后一起显示。
5. 标题可能晚于候选链接到达，晚到标题必须更新当前任务和排队任务。
6. 优化界面体验、批量功能和代码可维护性。

## 已完成的主要优化

### 深度网页提取

- 深度 worker 改为流式输出候选链接，主进程不必等待整页扫描结束才看到第一个候选。
- 深度模式会选择实际可运行且安装了 Playwright 的 Python 解释器。
- 扫描停止与下载停止相互独立。
- 处理扫描取消、迟到消息和预载结果的时序问题。
- 可控网页基准中，首个候选平均从 `4.210 s` 提前到 `0.659 s`，约提前 `84.4%`。
- 证据见 `docs/deep-streaming-20260905.md`。

### 预载、标题和界面

- 当前下载结束前，预载链接和标题都保持隐藏；结束后一起回填。
- 晚到的完整网页标题会更新当前任务和队列中的相应任务。
- 增加下载所选、全选、清空、复制链接和候选数量显示。
- 当前下载区域显示完整标题和实际保存文件名。
- 日志保持常驻，没有加入折叠功能。
- GUI 删除重复的下载流水线，统一调用核心 `M3U8Downloader.download()`。

### 下载速度与稳定性

- 片段先写入 `.part`，校验完整后使用 `os.replace` 原子发布。
- 支持 Range 断点续传；校验 `206 Content-Range` 和最终总长度。
- 正确处理完整 `.part` 的 `416`；本地残片长度不符时删除后从零下载。
- `retries=0` 仍执行首次请求；播放列表、密钥和片段统一使用重试语义。
- 播放列表和 AES 密钥请求也支持重试与停止。
- 下载块由 8 KiB 调整为 64 KiB；本地 64 MiB 微基准中的客户端循环和落盘开销下降约 `67.3%`。
- 每个下载线程使用独立 Session，连接池规模与并发数匹配。
- 缓存按播放列表、sequence、密钥方法、密钥 URI、IV 和输出目标隔离；manifest 只保存指纹，不落盘签名 URL。
- 相同缓存任务使用任务锁。停止后立即重试时，新任务会等待旧 worker 完全退出，避免并发修改 `.part` 或 `.ts`。
- GUI 停止操作每次使用独立 Event，后续任务不会清空旧任务的停止信号。
- 后台清理线程无法启动时会同步清理，确保 Session 和缓存锁不会泄漏。
- AES-128 未显式提供 IV 时，使用片段 sequence 的 128 位大端值作为默认 IV。
- 证据见 `docs/download-reliability-20260906.md`。

## 提交记录

1. `f810fe2 feat: stream deep extraction and clarify preload status`
2. `802fbd1 fix: harden extraction cancellation and download state`
3. `ad588db fix: preserve late titles and session behavior`
4. `9d6c201 fix: select a usable deep worker interpreter`
5. `3c3566c feat: make segment downloads resumable and reliable`
6. `50dcd0e fix: harden cancellation and cache isolation`
7. `b0ed9ac fix: preserve network timeout during cancellation`
8. `a144a3f fix: isolate cancellation across download runs`
9. `8b11cdf fix: serialize access to resumable cache`
10. `ee76ce5 fix: release cache lock when cleanup cannot start`

## 验证结果

- 完整测试：`358 passed, 1 skipped`
- 测试命令：`py -3.13 -m pytest -q -W error`
- 编译检查：`py -3.13 -m compileall -q m3u8_downloader tests`
- 差异检查：`git diff --check`
- 安装元数据：`py -3.13 setup.py --version` 输出 `1.5.3`
- CLI 冒烟：`py -3.13 -m m3u8_downloader --help`
- GUI 导入：`py -3.13 -c "from m3u8_downloader.gui import M3U8DownloaderGUI; print('GUI import OK')"`
- 两路最终代码审查均无剩余发现。

## 接手时建议执行

1. 先运行 `git status --short`，确认没有未提交修改。
2. 阅读本文件、`BUILD_EXE.md` 以及两份 `docs` 验证报告。
3. 使用 `git diff 35baa0b..ee76ce5` 查看全部优化，或逐个阅读上面的 10 个提交。
4. 运行完整测试，不要只验证单个模块。
5. 保留日志常驻以及预载链接、标题同时延迟回填的产品约束。
6. 若需要发布，先在隔离目录构建候选 EXE 并做 GUI、深度模式和真实下载冒烟测试；不要直接覆盖用户当前正式版。

## 可直接发送给项目 AI 的提示词

> 请接手完整优化源码包。解压后先完整阅读项目根目录的 `HANDOFF_TO_PROJECT_AI.md`、`BUILD_EXE.md`、`docs/deep-streaming-20260905.md` 和 `docs/download-reliability-20260906.md`，再检查 `35baa0b..ee76ce5` 的提交差异。当前程序代码已经通过 `358 passed, 1 skipped` 和两路最终代码审查。请先按 `BUILD_EXE.md` 运行完整测试，再用 `build.spec` 生成 GUI 与 CLI EXE，并在隔离目录完成启动、深度提取、普通提取、下载停止、预载和日志显示冒烟测试。请保留以下用户要求：日志始终显示；深度提取优先；下载期间的预载链接和标题必须等当前下载结束后一起回填；晚到标题必须更新当前及排队任务。不要直接覆盖用户现有正式版 EXE。
