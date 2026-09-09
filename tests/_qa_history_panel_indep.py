"""QA 独立回归验证：下载记录面板三处改动（commit 0f818d4）。

与工程师自测（``tests/test_gui.py`` / ``tests/test_page_history.py``）**方法不同**，
本文件刻意走「真实」路径而非 mock：

* **真实文件系统**：``page_history`` 走真实 ``PAGE_HISTORY_FILE`` 读写（``json`` 落盘后
  再从磁盘读回断言），不 monkeypatch 模块函数；
* **真实 Tk**：面板用真 ``Toplevel`` + 真 ``Treeview``，断言真实表头、真实单元格值、
  真实按钮 ``state``，而不是 mock 出来的 widget 调用记录；
* **真实下载**：本地 HTTP 服务 + 真 ``threading.Thread`` + 真 ``M3U8Downloader``，
  文件真实落盘后校验 ``output_path`` 指向的文件确实存在；
* **真实 subprocess**：「打开位置」真的派生进程（spy 包裹真 ``subprocess``）。

产品契约（偏离即 bug）：
  1. 网页名列显示**网页标题**，不是文件名；
  2. m3u8 列只显示**一个**（最近一次）；
  3. 打开位置 = 打开文件夹**并选中该文件**。
"""

from __future__ import annotations

import json
import os
import subprocess
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace

import pytest

from m3u8_downloader import gui as gui_mod
from m3u8_downloader import page_history
from m3u8_downloader.gui import DownloadJob, M3U8DownloaderGUI


# ===========================================================================
# 基础设施
# ===========================================================================

SEGMENT = b"\x47" + b"\x00" * 187
M3U8_BODY = (
    b"#EXTM3U\n#EXT-X-TARGETDURATION:1\n"
    b"#EXTINF:1,\nseg1.ts\n#EXTINF:1,\nseg2.ts\n#EXT-X-ENDLIST\n"
)


@pytest.fixture(scope="module")
def tk_runtime():
    """模块级 Tk 根窗口（真 Tk，Windows 上可用）。"""
    import tkinter as tk

    root = tk.Tk()
    root.withdraw()
    yield root
    try:
        root.destroy()
    except Exception:
        pass


@pytest.fixture
def real_gui(tmp_path, monkeypatch, tk_runtime):
    """真 Tk GUI 实例 + 全部持久化文件隔离到 tmp_path。"""
    import tkinter as tk
    from m3u8_downloader import gui, history

    monkeypatch.setattr(gui, "GUI_CONFIG_PATH", tmp_path / "gui.json")
    monkeypatch.setattr(history, "HISTORY_FILE", str(tmp_path / "history.json"))
    monkeypatch.setattr(gui, "PRELOAD_QUEUE_FILE", tmp_path / "preload_queue.json")
    monkeypatch.setattr(
        page_history, "PAGE_HISTORY_FILE", str(tmp_path / "page_history.json")
    )
    # 下载完成会弹 messagebox 阻塞 mainloop，这里静音（仅静音，不改逻辑）。
    monkeypatch.setattr(gui.messagebox, "showinfo", lambda *a, **k: None)
    monkeypatch.setattr(gui.messagebox, "showerror", lambda *a, **k: None)

    root = tk.Toplevel(tk_runtime)
    root.withdraw()
    app = gui.M3U8DownloaderGUI(root)
    yield SimpleNamespace(
        root=root,
        app=app,
        tmp=tmp_path,
        ph_file=tmp_path / "page_history.json",
    )
    # ---- 清理：先取消 after 回调，再释放 Tk 变量，最后销毁窗口 ----
    try:
        for cb in root.tk.call("after", "info"):
            root.after_cancel(cb)
    except Exception:
        pass
    for name, value in list(vars(app).items()):
        if isinstance(value, tk.Variable):
            try:
                setattr(app, name, None)
            except Exception:
                pass
    value = None
    try:
        root.destroy()
    except Exception:
        pass


@pytest.fixture
def m3u8_server():
    """本地 HTTP 服务：``/a.m3u8``（2 个分片）与 ``/segN.ts``。"""
    started = threading.Event()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            started.set()
            if self.path.startswith("/seg"):
                body, kind = SEGMENT, "video/mp2t"
            else:
                body, kind = M3U8_BODY, "application/vnd.apple.mpegurl"
            self.send_response(200)
            self.send_header("Content-Type", kind)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            try:
                self.wfile.write(body)
            except (BrokenPipeError, ConnectionResetError):
                pass

        def do_HEAD(self):
            self.send_response(200)
            self.send_header("Content-Length", str(len(SEGMENT)))
            self.end_headers()

    class Quiet(ThreadingHTTPServer):
        def handle_error(self, *args):
            pass

    server = Quiet(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_port}/a.m3u8"
    server.shutdown()
    server.server_close()
    thread.join(timeout=10)


def pump_until(root, predicate, timeout=40):
    """真 mainloop 泵送到 predicate 为真（超时则断言失败）。"""
    deadline = time.monotonic() + timeout
    outcome = []

    def check():
        if predicate() or time.monotonic() >= deadline:
            outcome.append(bool(predicate()))
            root.quit()
            return
        root.after(10, check)

    root.after(0, check)
    root.mainloop()
    assert outcome == [True], "GUI 未在超时内到达预期状态"


def read_records(ph_file: Path):
    """从**磁盘**读回 page_history.json 的 records（真持久化验证）。"""
    if not ph_file.exists():
        return []
    return json.loads(ph_file.read_text(encoding="utf-8")).get("records", [])


def find_record(ph_file: Path, page_url: str):
    for r in read_records(ph_file):
        if r.get("page_url") == page_url:
            return r
    return None


def arm_extraction(app, page_url: str, title: str):
    """让 GUI 处于「刚提取完某页」的状态并调用**真实**收尾（写入 title）。"""
    app._current_extract_page_url = page_url
    app._extract_recorded = False
    app._page_title = title
    app._candidate_page_url = page_url
    app._on_extract_done("success", allow_auto_download=False)
    return app


def run_queue_download(env, m3u8_url: str, output_path: str, page_url: str, title=""):
    """走**真实串行队列**下载一个任务并等待线程结束。"""
    app = env.app
    app._dir_var.set(str(env.tmp))
    app._pending_jobs = [DownloadJob(m3u8_url, output_path, title, page_url)]
    app._run_next_job()
    # 真实下载线程结束 + 真实消息队列回调（_poll_queue 在 mainloop 中）
    pump_until(env.root, lambda: not app._downloading, timeout=60)
    if app._download_thread is not None:
        app._download_thread.join(timeout=30)
    pump_until(env.root, lambda: True, timeout=5)


# ===========================================================================
# A. 端到端真链路：真实下载后记录里的 title / output_path
# ===========================================================================


class TestEndToEndRealDownload:
    """真实 HTTP + 真实下载线程 + 真实磁盘落盘 + 真实 page_history.json。"""

    def test_queue_download_persists_title_and_real_output_path(
        self, real_gui, m3u8_server
    ):
        """契约：下载完成后记录同时有正确 title 与 output_path，且文件真实存在。"""
        env = real_gui
        app = env.app
        page_url = "https://example.com/watch/1"
        out = str(env.tmp / "e2e 视频.mp4")

        arm_extraction(app, page_url, "【QA】网页标题 E2E")
        run_queue_download(env, m3u8_server, out, page_url, title="【QA】网页标题 E2E")

        rec = find_record(env.ph_file, page_url)
        assert rec is not None, "下载完成后应存在该页记录"
        # 1) 网页名 = 网页标题
        assert rec["title"] == "【QA】网页标题 E2E", f"网页名应为网页标题，实际 {rec['title']!r}"
        # 2) 状态与最近 m3u8
        assert rec["status"] == "downloaded"
        assert page_history.latest_m3u8_url(rec) == m3u8_server
        # 3) output_path 指向真实存在的文件
        assert rec["output_path"], "output_path 不应为空（打开位置依赖它）"
        assert os.path.isfile(rec["output_path"]), (
            f"output_path 指向的文件在磁盘上不存在：{rec['output_path']!r}"
        )
        assert os.path.abspath(rec["output_path"]) == os.path.abspath(out)
        assert os.path.getsize(rec["output_path"]) == len(SEGMENT) * 2

    def test_manual_start_download_records_output_path(self, real_gui, m3u8_server):
        """对照：手动「开始下载」路径（_start_download）应正确写入 output_path。"""
        env = real_gui
        app = env.app
        page_url = "https://example.com/watch/manual"
        out_name = "manual.mp4"

        app._current_source_page_url = page_url
        app._url_var.set(m3u8_server)
        app._filename_var.set(out_name)
        app._dir_var.set(str(env.tmp))
        app._start_download()
        assert app._downloading, "点击开始下载后应处于下载中"
        pump_until(env.root, lambda: not app._downloading, timeout=60)
        if app._download_thread is not None:
            app._download_thread.join(timeout=30)
        pump_until(env.root, lambda: True, timeout=5)

        rec = find_record(env.ph_file, page_url)
        assert rec is not None
        assert rec["output_path"], "手动下载路径应写入 output_path"
        assert os.path.isfile(rec["output_path"])
        assert os.path.abspath(rec["output_path"]) == os.path.abspath(
            env.tmp / out_name
        )


# ===========================================================================
# B. 队列 / 自动下载路径是否写入 output_path（重点挑刺）
# ===========================================================================


class TestQueuePathOutputPath:
    """串行队列（下载选中 / 自动下载 / 连续下载）是否也写入 output_path。"""

    def test_run_next_job_arms_inflight_output_path(self, real_gui, m3u8_server):
        """``_run_next_job`` 必须像 ``_start_download`` 一样记住本次输出路径。

        ``_finalize_inflight_download_record`` 依赖 ``_inflight_download_output_path``
        才能把落盘位置写进记录；队列路径若不赋值，记录里就没有 output_path，
        「打开位置」按钮会永久 DISABLED。
        """
        env = real_gui
        app = env.app
        out = str(env.tmp / "queue.mp4")
        app._dir_var.set(str(env.tmp))
        app._pending_jobs = [
            DownloadJob(m3u8_server, out, "T", "https://example.com/q")
        ]
        app._run_next_job()
        try:
            assert app._inflight_download_output_path == out, (
                "队列下载未把 output_path 存入 _inflight_download_output_path；"
                f"实际 {app._inflight_download_output_path!r}，期望 {out!r}"
            )
        finally:
            # 收尾：停止并等待，避免残留线程
            if app._stop_flag is not None:
                app._stop_flag.set()
            if app._download_thread is not None:
                app._download_thread.join(timeout=30)

    def test_queue_download_writes_output_path_to_record(self, real_gui, m3u8_server):
        """队列下载成功后记录里必须有 output_path（与手动下载等价）。"""
        env = real_gui
        page_url = "https://example.com/auto/1"
        out = str(env.tmp / "auto 下载.mp4")
        arm_extraction(env.app, page_url, "自动下载页")
        run_queue_download(env, m3u8_server, out, page_url, title="自动下载页")

        rec = find_record(env.ph_file, page_url)
        assert rec is not None
        assert rec["status"] == "downloaded"
        assert rec["output_path"] == out, (
            f"队列（自动/连续）下载未写入 output_path：{rec['output_path']!r}"
        )
        assert os.path.isfile(out)


# ===========================================================================
# C. 串行队列不串味
# ===========================================================================


class TestSerialQueueNoBleed:
    @pytest.mark.parametrize("n", [3])
    def test_each_job_records_its_own_output_path(self, real_gui, m3u8_server, n):
        """连续 n 个任务：每条记录的 output_path 必须是自己的，不残留上一个。"""
        env = real_gui
        app = env.app
        app._dir_var.set(str(env.tmp))
        jobs = []
        for i in range(n):
            page = f"https://example.com/serial/{i}"
            out = str(env.tmp / f"serial-{i}.mp4")
            arm_extraction(app, page, f"第{i}集")
            jobs.append((page, out))
            app._pending_jobs.append(
                DownloadJob(m3u8_server, out, f"第{i}集", page)
            )
        app._run_next_job()
        pump_until(env.root, lambda: not app._pending_jobs and not app._downloading,
                   timeout=120)
        if app._download_thread is not None:
            app._download_thread.join(timeout=30)
        pump_until(env.root, lambda: True, timeout=5)

        for i, (page, out) in enumerate(jobs):
            rec = find_record(env.ph_file, page)
            assert rec is not None, f"第{i}个任务的记录缺失"
            assert rec["output_path"] == out, (
                f"第{i}个任务 output_path 串味：{rec['output_path']!r} != {out!r}"
            )
            assert os.path.isfile(out)

    def test_manual_then_queue_does_not_leak_previous_path(
        self, real_gui, m3u8_server
    ):
        """手动下载 A（有路径）后紧接着队列下载 B：B 不能继承 A 的旧路径。"""
        env = real_gui
        app = env.app
        page_a = "https://example.com/bleed/a"
        page_b = "https://example.com/bleed/b"
        out_a = str(env.tmp / "bleed-a.mp4")
        out_b = str(env.tmp / "bleed-b.mp4")

        app._current_source_page_url = page_a
        app._url_var.set(m3u8_server)
        app._filename_var.set("bleed-a.mp4")
        app._dir_var.set(str(env.tmp))
        app._start_download()
        pump_until(env.root, lambda: not app._downloading, timeout=60)
        if app._download_thread is not None:
            app._download_thread.join(timeout=30)
        pump_until(env.root, lambda: True, timeout=5)
        assert find_record(env.ph_file, page_a)["output_path"] == out_a

        arm_extraction(app, page_b, "B 页")
        run_queue_download(env, m3u8_server, out_b, page_b, title="B 页")
        rec_b = find_record(env.ph_file, page_b)
        assert rec_b["output_path"] not in ("", out_a), (
            f"B 页 output_path 异常（空或继承了 A 的路径）：{rec_b['output_path']!r}"
        )
        assert rec_b["output_path"] == out_b


# ===========================================================================
# D. 自动改名 / 覆盖后 output_path 与最终落盘文件一致
# ===========================================================================


class TestCollisionRenameConsistency:
    def test_auto_rename_records_renamed_path(self, real_gui, m3u8_server):
        """用户选「自动改名」：记录里的 output_path 必须是**改名后**的路径。"""
        env = real_gui
        app = env.app
        page_url = "https://example.com/rename/1"
        original = env.tmp / "rename.mp4"
        original.write_bytes(b"old")  # 制造同名冲突

        # 「否」= 自动改名
        app._monkey_answer = False
        with pytest.MonkeyPatch().context() as mp:
            mp.setattr(gui_mod.messagebox, "askyesnocancel", lambda *a, **k: False)
            arm_extraction(app, page_url, "改名页")
            app._dir_var.set(str(env.tmp))
            app._pending_jobs = [
                DownloadJob(
                    m3u8_server, str(original), "改名页", page_url
                )
            ]
            app._run_next_job()

        pump_until(env.root, lambda: not app._downloading, timeout=60)
        if app._download_thread is not None:
            app._download_thread.join(timeout=30)
        pump_until(env.root, lambda: True, timeout=5)

        rec = find_record(env.ph_file, page_url)
        assert rec is not None
        expected_renamed = str(env.tmp / "rename-1.mp4")
        assert rec["output_path"] == expected_renamed, (
            f"改名后记录的 output_path 应为改名后的路径，实际 {rec['output_path']!r}"
        )
        assert os.path.isfile(rec["output_path"]), (
            "记录的 output_path 必须指向磁盘上真实存在的文件（否则打开位置选不中）"
        )

    def test_overwrite_records_original_path(self, real_gui, m3u8_server):
        """用户选「覆盖」：记录里的 output_path 保持原路径，且文件真实被覆盖。"""
        env = real_gui
        app = env.app
        page_url = "https://example.com/overwrite/1"
        original = env.tmp / "overwrite.mp4"
        original.write_bytes(b"old")

        with pytest.MonkeyPatch().context() as mp:
            mp.setattr(gui_mod.messagebox, "askyesnocancel", lambda *a, **k: True)
            arm_extraction(app, page_url, "覆盖页")
            app._dir_var.set(str(env.tmp))
            app._pending_jobs = [
                DownloadJob(m3u8_server, str(original), "覆盖页", page_url)
            ]
            app._run_next_job()

        pump_until(env.root, lambda: not app._downloading, timeout=60)
        if app._download_thread is not None:
            app._download_thread.join(timeout=30)
        pump_until(env.root, lambda: True, timeout=5)

        rec = find_record(env.ph_file, page_url)
        assert rec is not None
        assert rec["output_path"] == str(original)
        assert os.path.getsize(original) == len(SEGMENT) * 2


# ===========================================================================
# E. 打开位置（_reveal_path_in_file_manager）健壮性 —— 真 subprocess
# ===========================================================================


class TestRevealPathInFileManager:
    """用 spy 包裹**真实** subprocess，验证命令与健壮性。"""

    @pytest.fixture
    def spy(self, monkeypatch):
        calls = []
        real_popen = subprocess.Popen
        real_run = subprocess.run

        def fake_popen(args, *a, **k):
            calls.append(("popen", list(args)))
            return real_popen(args, *a, **k)

        def fake_run(args, *a, **k):
            calls.append(("run", list(args)))
            return real_run(args, *a, **k)

        monkeypatch.setattr(gui_mod.subprocess, "Popen", fake_popen)
        monkeypatch.setattr(gui_mod.subprocess, "run", fake_run)
        return calls

    @pytest.mark.parametrize(
        "name",
        [
            "normal.mp4",
            "with space.mp4",
            "中文 文件名.mp4",
            "special-#$%&'().mp4",
            "中英混合 ABC 123.mp4",
        ],
    )
    def test_weird_paths_do_not_crash_and_are_passed_verbatim(
        self, real_gui, spy, tmp_path, name
    ):
        """空格 / 中文 / 特殊字符路径：不崩，且路径原样传给 explorer /select,。"""
        app = real_gui.app
        target = tmp_path / name
        target.write_bytes(b"x")
        assert app._reveal_path_in_file_manager(str(target)) is True
        assert spy, "应真实派生一次进程"
        kind, args = spy[-1]
        if os.name == "nt":
            assert kind == "popen"
            assert args[0] == "explorer"
            assert args[1] == "/select,"
            assert args[2] == os.path.normpath(str(target))
        assert name in os.path.basename(args[-1])

    def test_empty_and_none_return_false_without_spawning(self, real_gui, spy):
        """空串 / None：返回 False 且不派生任何进程（按钮点击应无动作）。"""
        app = real_gui.app
        assert app._reveal_path_in_file_manager("") is False
        assert app._reveal_path_in_file_manager("   ") is False
        assert app._reveal_path_in_file_manager(None) is False
        assert spy == [], "空路径不应派生进程"

    def test_missing_file_and_missing_dir_do_not_raise(self, real_gui, spy, tmp_path):
        """文件已被删除 / 所在目录不存在：不抛异常，返回 True（由资源管理器报错）。"""
        app = real_gui.app
        gone = tmp_path / "已删除.mp4"
        assert app._reveal_path_in_file_manager(str(gone)) is True
        missing_dir = tmp_path / "不存在的目录" / "x.mp4"
        assert app._reveal_path_in_file_manager(str(missing_dir)) is True
        assert len(spy) == 2

    def test_panel_button_disabled_without_selection(self, real_gui, monkeypatch):
        """无记录/未选中行时「打开位置」按钮必须 DISABLED 且点击不动作。"""
        env = real_gui
        opened = []
        monkeypatch.setattr(
            gui_mod.subprocess, "Popen", lambda *a, **k: opened.append(a)
        )
        env.app._show_page_history()
        win = _latest_toplevel(env)
        btn = _find_button(win, "打开位置")
        assert btn is not None, "面板应有「打开位置」按钮"
        assert str(btn.cget("state")) == "disabled"
        btn.invoke()
        assert opened == [], "DISABLED 状态下点击不应派生进程"

    def test_panel_button_enabled_and_reveals_selected_file(self, real_gui, monkeypatch, tmp_path):
        """选中一个有 output_path 的行 → 按钮可用 → 点击定位到该文件。"""
        env = real_gui
        target = tmp_path / "目标 文件.mp4"
        target.write_bytes(b"x")
        page_history.record_page_downloaded(
            "https://example.com/reveal/1",
            "https://cdn/a.m3u8",
            output_path=str(target),
        )
        opened = []
        monkeypatch.setattr(
            gui_mod.subprocess, "Popen", lambda *a, **k: opened.append(list(a))
        )
        env.app._show_page_history()
        win = _latest_toplevel(env)
        tree = _find_tree(win)
        item = tree.get_children()[0]
        tree.selection_set(item)
        win.update()
        btn = _find_button(win, "打开位置")
        assert str(btn.cget("state")) == "normal"
        btn.invoke()
        assert opened, "点击后应派生定位进程"
        assert os.path.normpath(str(target)) in opened[-1]

    def test_panel_button_disabled_for_record_without_output_path(
        self, real_gui, monkeypatch
    ):
        """该行无 output_path（仅提取过未下载）→ 按钮 DISABLED。"""
        env = real_gui
        page_history.record_page_extracted(
            "https://example.com/only-extracted/1", title="只提取过"
        )
        monkeypatch.setattr(gui_mod.subprocess, "Popen", lambda *a, **k: 1 / 0)
        env.app._show_page_history()
        win = _latest_toplevel(env)
        tree = _find_tree(win)
        tree.selection_set(tree.get_children()[0])
        win.update()
        btn = _find_button(win, "打开位置")
        assert str(btn.cget("state")) == "disabled"


# ===========================================================================
# F. 向后兼容：旧格式 page_history.json
# ===========================================================================


class TestBackwardCompatOldFormat:
    OLD = {
        "records": [
            {
                "page_url": "https://old.example.com/p1",
                "status": "downloaded",
                "timestamp": "2026-01-01 10:00:00",
                "downloads": [
                    {"m3u8_url": "https://cdn/1.m3u8", "timestamp": "2026-01-01 10:00:00"},
                    {"m3u8_url": "https://cdn/2.m3u8", "timestamp": "2026-01-02 10:00:00"},
                ],
                "m3u8_url": "https://cdn/1.m3u8\nhttps://cdn/2.m3u8",
            },
            {
                "page_url": "https://old.example.com/p2",
                "status": "extracted",
                "timestamp": "2025-12-31 09:00:00",
                "downloads": [],
                "m3u8_url": "",
            },
        ]
    }

    def test_old_file_reads_without_error_and_does_not_crash_panel(
        self, real_gui, monkeypatch
    ):
        """旧格式（无 title/output_path）：list_records 不报错，面板能打开。"""
        env = real_gui
        env.ph_file.write_text(
            json.dumps(self.OLD, ensure_ascii=False), encoding="utf-8"
        )
        records = page_history.list_records()
        assert len(records) == 2
        assert all(r["title"] == "" for r in records)
        assert all(r["output_path"] == "" for r in records)

        monkeypatch.setattr(gui_mod.subprocess, "Popen", lambda *a, **k: 1 / 0)
        env.app._show_page_history()
        win = _latest_toplevel(env)
        tree = _find_tree(win)
        values = {tree.item(i, "values")[3]: tree.item(i, "values") for i in tree.get_children()}
        # 网页名列（index 1）应回退显示网页 URL
        row1 = values["https://old.example.com/p1"]
        assert row1[1] == "https://old.example.com/p1", (
            f"旧记录无标题时网页名应回退为网页 URL，实际 {row1[1]!r}"
        )
        # m3u8 列只显示最近一个（2.m3u8），不含「 | 」拼接
        assert row1[4] == "https://cdn/2.m3u8", (
            f"m3u8 列应只显示最近一次下载的 m3u8，实际 {row1[4]!r}"
        )
        # 打开位置按钮禁用
        tree.selection_set(tree.get_children()[0])
        win.update()
        assert str(_find_button(win, "打开位置").cget("state")) == "disabled"

    def test_old_file_roundtrip_preserves_downloads(self, real_gui):
        """旧记录被新代码写回后，历史 m3u8 不丢失（记录本身仍保留全部）。"""
        env = real_gui
        env.ph_file.write_text(
            json.dumps(self.OLD, ensure_ascii=False), encoding="utf-8"
        )
        page_history.record_page_downloaded(
            "https://old.example.com/p1", "https://cdn/3.m3u8", output_path="X:/nope.mp4"
        )
        rec = find_record(env.ph_file, "https://old.example.com/p1")
        urls = [d["m3u8_url"] for d in rec["downloads"]]
        assert urls == [
            "https://cdn/1.m3u8",
            "https://cdn/2.m3u8",
            "https://cdn/3.m3u8",
        ], f"历史 m3u8 不应丢失：{urls}"
        assert page_history.latest_m3u8_url(rec) == "https://cdn/3.m3u8"


# ===========================================================================
# G. 停止 / 失败：不应写入 output_path
# ===========================================================================


class TestStoppedAndFailed:
    def test_failed_download_does_not_set_output_path(self, real_gui):
        """失败：状态 failed，output_path 不写入（保留空）。"""
        env = real_gui
        page_url = "https://example.com/fail/1"
        arm_extraction(env.app, page_url, "失败页")
        env.app._inflight_download_page_url = page_url
        env.app._inflight_download_url = "https://cdn/x.m3u8"
        env.app._inflight_download_output_path = str(env.tmp / "should-not-exist.mp4")
        env.app._finalize_inflight_download_record("error")

        rec = find_record(env.ph_file, page_url)
        assert rec["status"] == "failed"
        assert rec["output_path"] == "", (
            f"失败下载不应写入 output_path，实际 {rec['output_path']!r}"
        )

    def test_stopped_download_does_not_set_output_path(self, real_gui):
        """停止：状态保持已提取，output_path 不写入，且 in-flight 值被复位。"""
        env = real_gui
        page_url = "https://example.com/stop/1"
        arm_extraction(env.app, page_url, "停止页")
        env.app._inflight_download_page_url = page_url
        env.app._inflight_download_url = "https://cdn/x.m3u8"
        env.app._inflight_download_output_path = str(env.tmp / "stopped.mp4")
        env.app._finalize_inflight_download_record("stopped")

        rec = find_record(env.ph_file, page_url)
        assert rec["status"] == "extracted"
        assert rec["output_path"] == ""
        assert env.app._inflight_download_output_path == "", "停止后必须复位 in-flight 路径"

    def test_stop_mid_download_leaves_no_output_path(self, real_gui, m3u8_server):
        """真实下载中途停止：记录不写入 output_path。"""
        env = real_gui
        app = env.app
        page_url = "https://example.com/stop-mid/1"
        out = str(env.tmp / "stop-mid.mp4")
        arm_extraction(app, page_url, "中途停止页")
        app._dir_var.set(str(env.tmp))
        app._pending_jobs = [DownloadJob(m3u8_server, out, "中途停止页", page_url)]
        app._run_next_job()
        if app._stop_flag is not None:
            app._stop_flag.set()
        pump_until(env.root, lambda: not app._downloading, timeout=60)
        if app._download_thread is not None:
            app._download_thread.join(timeout=30)
        pump_until(env.root, lambda: True, timeout=5)

        rec = find_record(env.ph_file, page_url)
        assert rec is not None
        assert rec["output_path"] == "", (
            f"中途停止不应写入 output_path，实际 {rec['output_path']!r}"
        )


# ===========================================================================
# H. 面板产品契约：网页名 = 网页标题（非文件名）；m3u8 只有一个
# ===========================================================================


class TestPanelContract:
    def test_title_column_shows_page_title_not_filename(self, real_gui):
        """网页名列显示网页标题，即使文件名与标题完全不同。"""
        env = real_gui
        page_url = "https://example.com/contract/1"
        page_history.record_page_extracted(page_url, title="仙界法务部 第55集 (2026)")
        page_history.record_page_downloaded(
            page_url,
            "https://cdn/one.m3u8",
            output_path=str(env.tmp / "completely-different-name.mp4"),
        )
        env.app._show_page_history()
        win = _latest_toplevel(env)
        tree = _find_tree(win)
        # 表头顺序：时间 / 网页名 / 状态 / 网页 URL / m3u8
        assert tree.heading("title", "text") == "网页名"
        values = tree.item(tree.get_children()[0], "values")
        assert values[1] == "仙界法务部 第55集 (2026)"
        assert "completely-different-name" not in values[1], (
            "网页名列不能显示文件名"
        )

    def test_m3u8_column_shows_only_latest(self, real_gui):
        """同一页多次下载：m3u8 列只显示最近一次，不出现「 | 」拼接。"""
        env = real_gui
        page_url = "https://example.com/multi/1"
        page_history.record_page_extracted(page_url, title="多集页")
        for i in range(1, 4):
            page_history.record_page_downloaded(
                page_url, f"https://cdn/{i}.m3u8", output_path=str(env.tmp / f"{i}.mp4")
            )
        env.app._show_page_history()
        win = _latest_toplevel(env)
        tree = _find_tree(win)
        values = tree.item(tree.get_children()[0], "values")
        assert values[4] == "https://cdn/3.m3u8", (
            f"m3u8 列应只显示最近一次（3.m3u8），实际 {values[4]!r}"
        )
        assert " | " not in values[4]
        # 记录本身仍保留全部历史
        rec = find_record(env.ph_file, page_url)
        assert len(rec["downloads"]) == 3

    def test_latest_m3u8_url_unit_contract(self):
        """latest_m3u8_url：末项优先；无 downloads 时回退 m3u8_url 首行。"""
        assert page_history.latest_m3u8_url({"downloads": [
            {"m3u8_url": "a"}, {"m3u8_url": "b"},
        ]}) == "b"
        assert page_history.latest_m3u8_url({"m3u8_url": "x\ny\nz"}) == "x"
        assert page_history.latest_m3u8_url({}) == ""
        assert page_history.latest_m3u8_url(None) == ""
        assert page_history.latest_m3u8_url({"downloads": []}) == ""


# ===========================================================================
# 工具：在真 Toplevel 中定位控件
# ===========================================================================


def _walk(parent):
    for child in parent.winfo_children():
        yield child
        yield from _walk(child)


def _latest_toplevel(env):
    import tkinter as tk

    tops = [w for w in _walk(env.root) if isinstance(w, tk.Toplevel)]
    if tops:
        return tops[-1]
    return env.root


def _find_tree(win):
    from tkinter import ttk

    for w in _walk(win):
        if isinstance(w, ttk.Treeview):
            return w
    raise AssertionError("未找到 Treeview")


def _find_button(win, label):
    from tkinter import ttk

    for w in _walk(win):
        if isinstance(w, ttk.Button) and w.cget("text") == label:
            return w
    return None
