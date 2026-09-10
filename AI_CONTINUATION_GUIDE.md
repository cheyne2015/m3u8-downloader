# m3u8-downloader 接手指南（给下一个 AI）

> 目的：让另一个 AI 在**完全看不到本次对话**的前提下，快速读懂源码、理解全部功能、安全接手继续开发。
> 当前版本：**v1.6.0** ｜ 整理日期：2026-09-10

⚠️ **请先注意**：根目录另有一份 `HANDOFF_TO_PROJECT_AI.md`，那是 **v1.5.3 时代的旧交接文档**（基线 `35baa0b`），
内容已大幅过时（未包含多页预载队列、下载记录面板、本次的 m3u8↔文件对齐修复等）。
**以本文为准**，旧文档仅作历史参考。

---

## 0. 环境第一件事（不知道这个会直接卡死）

| 项 | 正确做法 | 错误做法 & 后果 |
|---|---|---|
| Python 解释器 | **必须用系统 `py -3.13`** | 托管 Python 3.13.12 **没有 tkinter** → GUI 相关全部 `ImportError` |
| 解释器路径 | `C:\Users\cheyn\AppData\Local\Programs\Python\Python313` | — |
| 跑测试 | `py -3.13 -m pytest tests/test_xxx.py -q` | 直接 `python` / `pytest` 会解析到托管版 |
| 全量测试 | **分文件跑** | 全量 `pytest -q` 在 Windows 偶发 `fatal exception 0x80000003`（downloader 线程 + GC 的 flaky，非代码 bug） |
| 打包 | `rm -rf build dist` 然后 `py -3.13 _run_build.py` | **不要加 `--clean`**：PyInstaller 删缓存会被安全删除钩子拦截报 OSError |

---

## 1. 项目是什么

一个 **m3u8 视频下载器**（Python + Tkinter），支持从网页提取 m3u8 直链、并发下载 TS 片段、合并为 MP4。

**三种运行形态**
- CLI：`py -3.13 -m m3u8_downloader ...`（`cli.py`）
- GUI：`py -3.13 -m m3u8_downloader`（无参数默认启动 GUI，`__main__.py`）
- 打包 EXE（PyInstaller，双产物）：
  - `dist/m3u8-dl.exe` —— GUI 版，`runw.exe` 引导，**窗口模式无黑窗**
  - `dist/m3u8-dl-cli.exe` —— CLI 版，控制台模式

**两种网页提取模式**
- **普通模式**：requests 拉 HTML + BeautifulSoup 正则扫 m3u8
- **深度模式**：Playwright 无头浏览器流式抓取（`extractor.py` 调度，`deep_worker.py` 执行）

---

## 2. 源码模块地图

项目根目录：`C:\Users\cheyn\WorkBuddy\2026-06-15-13-47-25\m3u8-downloader`
（`F:\gadgets\m3u8-downloader` 是**同源部署副本，无 `.git`**；改代码只在 C 盘，覆盖 F 盘由用户统一处理）

| 文件 | 行数 | 职责 |
|---|---|---|
| `m3u8_downloader/gui.py` | 2629 | **最复杂**。Tkinter GUI 全部逻辑：候选列表、预载队列、下载记录面板、自动选中/下载 |
| `m3u8_downloader/extractor.py` | 1591 | 网页提取。普通模式解析 + 深度模式调度（选解释器、起子进程、流式收候选） |
| `m3u8_downloader/downloader.py` | 785 | 下载核心：并发、断点续传（Range/206/416）、重试、AES-128 解密、`.part` 原子发布 |
| `m3u8_downloader/estimator.py` | 473 | 时长 / 体积估算（候选排序"选最大的"依赖它） |
| `m3u8_downloader/cli.py` | 445 | 命令行参数与流程 |
| `m3u8_downloader/page_history.py` | 429 | **网页下载记录**（本次重点改动）。持久化 `page_history.json` |
| `m3u8_downloader/deep_worker.py` | 410 | 深度模式 worker 子进程，用本机 playwright 抓页，JSON 打到 stdout |
| `m3u8_downloader/utils.py` | 408 | 文件名规范化（`normalize_mp4_filename`）、标题分段（`extract_title_segment`）等 |
| `m3u8_downloader/parser.py` | 334 | m3u8 播放列表解析 |
| `m3u8_downloader/merger.py` | 280 | TS 片段合并为 MP4（调用 ffmpeg） |
| `m3u8_downloader/history.py` | 74 | 已下载 m3u8 直链**去重**（跨会话"重复链接提醒"） |
| `m3u8_downloader/__main__.py` | 14 | 无参数 → 默认启动 GUI |
| `m3u8_downloader/gui_launcher.py` | 6 | GUI 启动入口 |
| `m3u8_downloader/__init__.py` | 3 | 仅 `__version__`（**当前 1.6.0**） |

测试：`tests/` 下 14 个 `test_*.py`（pytest 收集）+ 2 个 `_qa_*.py`（**`_qa_` 前缀故意不被收集**，是独立验证脚本，可手动 `pytest tests/_qa_xxx.py` 跑）。
设计文档：`docs/`（`system_design.md`、两份验证报告、两份 mermaid 图）。

---

## 3. 核心数据流

```
网页 URL
  │  提取（普通 HTML+bs4 / 深度 playwright 子进程）
  ▼
候选 m3u8 列表（estimator 估算时长/大小）
  │  自动选中（候选数=1 直接选；多个且时长全相同 → 选 estimated_size 最大）
  ▼
M3U8Downloader.download()  ← GUI 与 CLI 共用同一条流水线
  │  parser 解析播放列表 → 并发拉 TS 片段（.part + os.replace 原子发布）
  │  AES-128 解密（无显式 IV 时用 sequence 的 128 位大端值）
  ▼
merger 合并为 MP4（ffmpeg）
  │
  ▼
写入记录：history.py（去重）+ page_history.py（网页维度下载记录）
```

### 深度模式的关键架构约束（改前必读）
PyInstaller **冻结环境下 `FrozenImporter` 接管模块查找**，往 `sys.path` 注入系统 site-packages 无效，
EXE 内 `import playwright` 必然失败。
→ 因此深度模式**不在 EXE 内 import playwright**，而是 EXE 调用**系统 Python（`py -3.13`）**
执行随包分发的 `deep_worker.py`，worker 抓完把 URL 列表以 JSON 打到 stdout，EXE 解析 JSON。
`build.spec` 刻意**排除** Playwright/Chromium。

---

## 4. GUI 三个核心机制（最容易改错的地方）

### A. 多页连续预载队列
- 下载中可连续预载多页，每次点「提取网页」**追加**进队列，不顶掉上一个。
- **预载的标题/候选/文件名必须暂存，等当前下载完成（用户点确认）后才回填显示**——这是设计，不是 bug。
- 预载提取期间用独立在飞标志 `_preload_extracting`（此时 `_extracting` 为 False）。
  ⚠️ 任何判断「是否正在提取」的代码都要**同时看这两个标志**，否则预载期间该功能失效。

### B. 「提取网页」按钮不变灰
- 用户明确要求：下载中预载 + 空闲首次/单次提取，**按钮始终保持 `tk.NORMAL`**。
- 进行中反馈由「停止提取」按钮（enabled）+ 状态文本/日志承担。
- ⚠️ **测试不可用「提取网页」按钮态判断任何阶段**，改用「停止提取」`disabled`（=提取完成）或状态文本 / `app._pending_extract_result`。

### C. 下载记录面板（`_show_page_history`）
- **一行 = 一个网页**（key = `page_url`）。用户明确要求**不拆行**。
- 该页历次下载累积在 `downloads` 列表，主面板只显示**最近一次**的 m3u8。
- **双击某行 / 点「查看全部下载」** → 弹出 `_show_page_downloads_detail` 详情窗，
  列出该页**每一次**下载（下载时间 / m3u8 直链 / 输出文件），选中某行「打开位置」定位**该次**的文件。
- ⚠️ **m3u8 与「打开位置」的文件必须同源**：同一行要用**同一个** `latest_download` 条目
  同时提供 m3u8 和 output_path，否则会出现「显示 m3u8 是 B、打开的却是 A 的文件」。

---

## 5. 持久化数据（`~/.m3u8-downloader/`）

| 文件 | 内容 | 备注 |
|---|---|---|
| `page_history.json` | 网页维度下载记录 | 上限 500 页，损坏/缺失时安全降级为空，**绝不影响下载主流程** |
| `download_history.json` | 已下载 m3u8 直链（去 query/fragment 后去重） | 供"重复链接提醒" |
| `gui_config.json` | `remember_dir` / `last_dir` | 「记住保存位置」勾选；文件损坏/顶层非 dict/目录失效 → 均降级为"不记住" |
| `preload_queue.json` | 预载队列 | ⚠️ 测试必须隔离此文件，真实磁盘残留会污染 `_preload_queue` 导致假失败 |

### `page_history.json` 记录结构（重点）
```jsonc
{
  "page_url": "网页 URL（记录键，同一页只留一条）",
  "status": "extracted | downloaded | failed",
  "timestamp": "最近一次活动时间",
  "title": "网页标题（取不到回退显示 URL）",
  "output_path": "记录级兜底路径（最近一次成功下载）",
  "downloads": [
    {"m3u8_url": "...", "timestamp": "YYYY-MM-DD HH:MM:SS", "output_path": "该次下载的文件"}
  ],
  "m3u8_url": "冗余兼容字段 = 全部 m3u8 换行拼接（旧版本用）"
}
```
**两条不变量（破坏了就会错位）**
1. `downloads` **必须维持「旧→新」顺序**：重复下载同一 m3u8 时要 `pop` 后 `append` 到**末尾**（只就地改时间戳会打乱顺序）。
2. 「最近一次」= **`timestamp` 最大**的条目，**不能**硬取 `downloads[-1]`（见 `latest_download()`）。

---

## 6. 用户已确认的产品规则（硬约束，不要擅自改）

1. **日志始终显示，不折叠** —— 用户通过日志确认所有状态。
2. **下载期间的预载链接和标题不得提前回填**，必须等当前下载结束后一起显示。
3. **晚到的标题**必须更新当前任务**和**排队任务。
4. **标题自动填充文件名**：`utils.extract_title_segment()` 按 ` - `（缺则单 `-`）取首个非空段；
   v1.5.4 起已移除 `_filename_touched` 标志。
5. **文件名只保留单一 `.mp4` 后缀**（`utils.normalize_mp4_filename`）。
6. **发版原则**：小功能累积，**只在用户明确要求发版时统一合并为一个版本发布**，
   不每个小功能都单独建 Release / 上传 EXE。
7. **深度模式优先**（网页提取速度以深度模式为主）。

---

## 7. 开发工作流（命令速查）

```bash
# 测试（分文件跑，避免全量 flaky）
py -3.13 -m pytest tests/test_gui.py tests/test_page_history.py -q

# 打包（先手动清，不要 --clean）
rm -rf build dist && py -3.13 _run_build.py

# 校验版本
./dist/m3u8-dl-cli.exe --version
```

**Git 远端**：`origin`=GitHub / `gitee` / `cnb`（三平台同源）

```bash
# 推送（GCM 在无 TTY 下会静默失败，必须用 credential.helper=store）
env -u HTTPS_PROXY -u HTTP_PROXY -u ALL_PROXY \
  GIT_TERMINAL_PROMPT=0 git -c credential.helper=store push gitee main
```

**发版**（用户要求时）：改 `__init__.py` 的 `__version__` → 重新打包 → 推送 → 三平台 Release + 双 EXE。

---

## 8. 已知坑 & 陷阱（血泪清单，务必先读）

| # | 坑 | 正确做法 |
|---|---|---|
| 1 | **真实 Tk 测试会弹出真实窗口并挂死**，`desktop_gui` fixture 尤其危险 | 用 `_tk_patches()` / `_tk_patches_with_toplevel()`（后者额外 patch `tkinter.Toplevel`）；**别在用户用电脑时跑真实窗口测试** |
| 2 | 托管 Python 无 tkinter | 一律 `py -3.13` |
| 3 | PyInstaller `--clean` 被安全删除钩子拦截 | 手动 `rm -rf build dist` |
| 4 | 覆盖 F 盘 EXE 报 `Device busy` / `Permission denied` | F 盘正跑着 `m3u8-dl.exe`。先 `tasklist \| grep m3u8` 定位 PID，`taskkill /F /PID` 后再复制 |
| 5 | **PowerShell 工具 stdout 被吞**（只回 exit code） | 排查进程/锁统一用 **Bash + `tasklist`/`taskkill`** |
| 6 | 双击绑定顺序 | `<Double-1>` 必须**先于** `<<TreeviewSelect>>` 绑定：既有测试以「最后一次 bind」取选中处理器并无形参调用，在后会 TypeError |
| 7 | `downloads` 顺序 & `latest_download` | 见第 5 节两条不变量 |
| 8 | 旧 `page_history.json` 条目无 `output_path` | 降级空串 → 回退记录级路径。历史错位行要等该页**再下载一次**才写入条目级精确路径（数据限制，非代码 bug） |
| 9 | GitHub 网络不可达（SSL `unexpected eof`） | Gitee / CNB 正常；GitHub 等网络恢复再补推 |
| 10 | 调 Gitee/CNB API 报 SOCKS 错误 | 前置 `env -u HTTPS_PROXY -u HTTP_PROXY -u ALL_PROXY` |
| 11 | CNB Release 上传字段名 | 是 **`asset_name` + `size`**，不是 `file_name`/`file_size` |
| 12 | 行尾 CRLF/LF | 换 `.git` 后若全文件标 modified 而 `git diff` 为空 → `git config --unset core.autocrlf` + `git reset --hard HEAD` |
| 13 | 测试 fixture 隔离 | `PRELOAD_QUEUE_FILE` / `PAGE_HISTORY_FILE` 都要重定向到 tmp，否则真实磁盘数据污染导致假失败 |

---

## 9. 当前状态（2026-09-10）

**最近提交（都在 main）**
| commit | 说明 |
|---|---|
| `4a38ca5` | feat(gui)：下载记录支持点开查看该网页全部下载（每次下载一行） |
| `7468553` | fix(page-history)：修复 m3u8 与「打开位置」文件不一一对应 |
| `9f771fb` | chore：版本号 → 1.6.0 |
| `82c9978` | fix(gui)：空闲首次提取「提取网页」按钮不再变灰 |
| `90efaac` | fix(gui)：下载选中路径补记 `output_path`，修复「打开位置」定位不到文件 |

**同步状态**
- Gitee ✅ / CNB ✅ 已推到 `4a38ca5`
- GitHub ❌ 仍不可达，待网络恢复补推
- F 盘 ✅ 已用**重新打包的 EXE** 覆盖（含 7468553 + 4a38ca5），双 EXE 哈希与本地一致

**测试基线**
- `tests/test_page_history.py` + `tests/test_gui.py` → **175 passed**
- `tests/test_multi_preload_queue.py` + `tests/test_qa_auto_download.py` → **81 passed**
- QA 独立验证 `tests/_qa_page_history_align_indep.py` → **25 passed**（全 headless，不弹窗）

**待用户决定**
- ⚠️ **Release 附件版本不一致**：Gitee/CNB 上已发布的 v1.6.0 附件是 9/9 的旧构建，
  **不含** `7468553` + `4a38ca5`。三选一：① 保持现状等下次发版 ② 重传附件覆盖 v1.6.0 ③ 升 1.6.1 发新版。

---

## 10. 可直接复制给下一个 AI 的提示词

> 请接手 m3u8-downloader 项目（路径 `C:\Users\cheyn\WorkBuddy\2026-06-15-13-47-25\m3u8-downloader`，当前 v1.6.0）。
>
> **第一步请完整阅读**：根目录 `AI_CONTINUATION_GUIDE.md`（必读，含环境、模块地图、产品硬约束、13 条已知坑）、
> `README.md`、`BUILD_EXE.md`、`docs/system_design.md`。
> 注意根目录另有一份 `HANDOFF_TO_PROJECT_AI.md` 是 v1.5.3 时代的旧文档，**已过时，不要以它为准**。
>
> **环境强制要求**：必须用系统 Python `py -3.13`（托管 Python 无 tkinter，GUI 会 ImportError）；
> 测试**分文件跑**（全量在 Windows 偶发 flaky）；打包用 `rm -rf build dist && py -3.13 _run_build.py`（不要加 `--clean`）。
>
> **改代码前先跑一次基线确认环境正常**：`py -3.13 -m pytest tests/test_page_history.py tests/test_gui.py -q`（应为 175 passed）。
>
> **不可违反的用户硬约束**：日志常驻不折叠；下载期间预载的链接/标题必须等当前下载结束后一起回填；
> 「提取网页」按钮任何情况不变灰；下载记录一行=一个网页（不拆行，双击查看该页全部下载）；
> m3u8 与「打开位置」文件必须同源；发版只在用户明确要求时进行。
>
> 然后告诉我你的任务，我会明确说明要改什么。
