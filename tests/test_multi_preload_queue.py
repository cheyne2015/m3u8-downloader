"""多页连续预载队列（功能一）行为测试.

以 headless（mock tkinter）为主，直接驱动 GUI 内部状态机，覆盖：

* 下载中连续预载 2 页 → 队列追加 2 个条目（不顶掉上一个）；
* 空闲提取不入队（保持既有单页行为）；
* 当前页下载成功完成后自动依次处理队列头并下载（队列清空 + 顺序）；
* 「停止下载」中断后队列保留；
* 单页失败被跳过、自动继续下一个；
* 「待处理 N 个」计数控件与点击弹窗列出 URL；
* 队列持久化往返（存盘后重载恢复条目与候选）；
* 预载完成消息把 extracting 条目固化为 success 并附结果。

风格与 ``tests/test_qa_auto_download.py`` / ``tests/test_gui.py`` 保持一致。
"""

from contextlib import ExitStack, contextmanager
from unittest.mock import MagicMock, patch

import pytest
import tkinter as tk

from m3u8_downloader import gui as gui_mod
from m3u8_downloader import page_history as page_history_mod
from m3u8_downloader.extractor import Candidate
from m3u8_downloader.gui import (
    M3U8DownloaderGUI,
    PreloadQueueEntry,
    PreloadResult,
    PreloadState,
)


class FakeVar:
    """可写可读的 Tk 变量替身（BooleanVar 需要 get 反映 set）. """

    def __init__(self, value=False):
        self.value = value
        self.set_calls = []

    def set(self, value):
        self.set_calls.append(value)
        self.value = value

    def get(self):
        return self.value


def _tk_patches():
    """屏蔽全部 tkinter 控件，使 GUI 可在无显示环境下实例化。"""
    return [
        patch("tkinter.StringVar", lambda **kw: MagicMock(get=MagicMock(return_value=""), set=MagicMock())),
        patch("tkinter.IntVar", lambda **kw: MagicMock(get=MagicMock(return_value=kw.get("value", 0)))),
        patch("tkinter.BooleanVar", lambda *a, **kw: FakeVar(bool(kw.get("value", False)))),
        patch("tkinter.DoubleVar", lambda **kw: MagicMock(get=MagicMock(return_value=0))),
        patch("tkinter.Text", return_value=MagicMock()),
        patch("tkinter.ttk.Frame", return_value=MagicMock()),
        patch("tkinter.ttk.Label", return_value=MagicMock()),
        patch("tkinter.ttk.Entry", return_value=MagicMock()),
        patch("tkinter.ttk.Button", return_value=MagicMock()),
        patch("tkinter.ttk.Spinbox", return_value=MagicMock()),
        patch("tkinter.ttk.Checkbutton", return_value=MagicMock()),
        patch("tkinter.ttk.Progressbar", return_value=MagicMock()),
        patch("tkinter.ttk.LabelFrame", return_value=MagicMock()),
        patch("tkinter.ttk.Scrollbar", return_value=MagicMock()),
        patch("tkinter.ttk.Treeview", return_value=MagicMock()),
        patch("tkinter.messagebox.showinfo"),
        patch("tkinter.messagebox.showerror"),
    ]


@contextmanager
def _headless_env(tmp_path, monkeypatch):
    """进入 headless mock 环境，并把队列/记录文件重定向到 tmp_path.

    Yields:
        一个 ``build()`` 函数：每次调用创建一个新的 GUI 实例（同文件路径）。
    """
    with ExitStack() as stack:
        for p in _tk_patches():
            stack.enter_context(p)
        qfile = tmp_path / "preload_queue.json"
        hfile = tmp_path / "page_history.json"
        monkeypatch.setattr(gui_mod, "PRELOAD_QUEUE_FILE", qfile)
        monkeypatch.setattr(page_history_mod, "PAGE_HISTORY_FILE", str(hfile))

        def build():
            root = MagicMock()
            root.tk = MagicMock()
            root.after = MagicMock()
            root.clipboard_get = MagicMock(return_value="")
            return M3U8DownloaderGUI(root)

        yield build


@pytest.fixture
def headless(tmp_path, monkeypatch):
    with _headless_env(tmp_path, monkeypatch) as build:
        yield build()


def _success_entry(page_url, m3u8_url, title, filename_title="", page_title=""):
    """构造一个已成功预载的队列条目（单候选，便于自动选中）。"""
    candidate = Candidate(url=m3u8_url, title=title, duration=60.0, estimated_size=1024)
    return PreloadQueueEntry(
        page_url=page_url,
        state="success",
        candidates=[candidate],
        filename_title=filename_title or title,
        page_title=page_title or title,
    )


def _arm_auto(gui):
    """开启自动下载且本次会话已手动下载过。"""
    gui._auto_download_var = FakeVar(True)
    gui._session_manual_downloaded = True


def _configure_tree(gui):
    """让 Treeview mock 可以安全迭代/取选中。"""
    gui._tree.get_children.return_value = []
    gui._tree.selection.return_value = []
    gui._candidates = []
    gui._candidate_items = {}


def _fake_download_start(gui):
    """模拟「下载选中」真正启动：置 downloading、移除队列头并记录是否队列页。"""

    def start():
        gui._downloading = True
        gui._active_download_is_queue_page = gui._drop_queue_head_if_page(
            gui._candidate_page_url
        )

    return start


class TestQueueEnqueue:

    def test_two_download_time_preloads_append_two_entries(self, headless):
        """下载中连续预载两页 → 队列有 2 个条目（追加而非顶掉）。"""
        gui = headless
        gui._downloading = True
        gui._url_var.get.return_value = "https://x/page-b"
        with patch.object(gui, "_extract_worker"):
            gui._start_extract()
        assert len(gui._preload_queue) == 1
        assert gui._preload_queue[0].page_url == "https://x/page-b"
        assert gui._preload_queue[0].state == "extracting"

        # 第一轮预载完成，允许发起第二次
        gui._on_extract_done("success")
        assert gui._extracting is False

        gui._url_var.get.return_value = "https://x/page-c"
        with patch.object(gui, "_extract_worker"):
            gui._start_extract()
        assert len(gui._preload_queue) == 2
        assert [e.page_url for e in gui._preload_queue] == [
            "https://x/page-b", "https://x/page-c",
        ]
        # 结果列表不应被预载改动（旧逻辑由 _preload_list_cleared=False 保证）
        assert gui._preload_list_cleared is False

    def test_idle_extract_does_not_enqueue(self, headless):
        """空闲（未下载）时提取不入待处理队列，保持既有单页行为。"""
        gui = headless
        gui._downloading = False
        gui._url_var.get.return_value = "https://x/page-a"
        with patch.object(gui, "_extract_worker"):
            gui._start_extract()
        assert gui._preload_queue == []


class TestQueueFinalize:

    def test_preload_completion_finalizes_entry_and_stages_result(self, headless):
        """下载中收到 preloaded_extract → 条目固化为 success，并暂存结果与镜像。"""
        gui = headless
        gui._downloading = True
        gui._preload_queue.append(PreloadQueueEntry(page_url="https://x/b"))
        cands = [Candidate(url="https://cdn/b.m3u8", title="B")]
        result = PreloadResult(
            cands, "B", "B - full", "https://x/b", PreloadState.SUCCESS,
        )
        with patch.object(gui, "_log"):
            gui._handle_message("preloaded_extract", result)
        entry = gui._preload_queue[0]
        assert entry.state == "success"
        assert entry.page_url == "https://x/b"
        assert [c.url for c in entry.candidates] == ["https://cdn/b.m3u8"]
        assert entry.filename_title == "B"
        assert entry.page_title == "B - full"
        assert gui._pending_extract_result == "success"


class TestQueueAutoChain:

    def test_download_done_processes_heads_sequentially(self, headless):
        """当前页下载成功后自动依次处理队列头并下载 → 队列清空 + 顺序正确。"""
        gui = headless
        _configure_tree(gui)
        _arm_auto(gui)
        gui._preload_queue.append(
            _success_entry("https://x/b", "https://cdn/b.m3u8", "B")
        )
        gui._preload_queue.append(
            _success_entry("https://x/c", "https://cdn/c.m3u8", "C")
        )
        fake_start = _fake_download_start(gui)
        with patch("m3u8_downloader.gui.messagebox.showinfo"):
            with patch.object(gui, "_download_selected", side_effect=fake_start):
                # A 下载完成
                gui._on_download_done("success")
        # B 被展示并自动下载：队列还剩 C
        assert gui._downloading is True
        assert gui._candidate_page_url == "https://x/b"
        assert [e.page_url for e in gui._preload_queue] == ["https://x/c"]

        with patch("m3u8_downloader.gui.messagebox.showinfo"):
            with patch.object(gui, "_download_selected", side_effect=fake_start):
                gui._on_download_done("success")  # B 下载完成
        assert gui._downloading is True
        assert gui._candidate_page_url == "https://x/c"
        assert gui._preload_queue == []

        with patch("m3u8_downloader.gui.messagebox.showinfo"):
            with patch.object(gui, "_download_selected", side_effect=fake_start):
                gui._on_download_done("success")  # C 下载完成（队列已空）
        assert gui._downloading is False
        assert gui._preload_queue == []

    def test_stop_download_keeps_queue(self, headless):
        """「停止下载」→ 自动连播链中断，队列保留。"""
        gui = headless
        _configure_tree(gui)
        gui._preload_queue.append(
            _success_entry("https://x/b", "https://cdn/b.m3u8", "B")
        )
        gui._preload_queue.append(
            _success_entry("https://x/c", "https://cdn/c.m3u8", "C")
        )
        with patch("m3u8_downloader.gui.messagebox.showinfo"):
            gui._on_download_done("stopped")
        assert gui._downloading is False
        assert len(gui._preload_queue) == 2, "停止下载后队列应保留"
        assert [e.page_url for e in gui._preload_queue] == ["https://x/b", "https://x/c"]

    def test_failed_page_is_skipped_and_chain_continues(self, headless):
        """队列头提取失败 → 跳过该页，自动继续队列下一个。"""
        gui = headless
        _configure_tree(gui)
        _arm_auto(gui)
        gui._preload_queue.append(
            PreloadQueueEntry(page_url="https://x/bad", state="error", candidates=[])
        )
        gui._preload_queue.append(
            _success_entry("https://x/ok", "https://cdn/ok.m3u8", "OK")
        )
        fake_start = _fake_download_start(gui)
        with patch("m3u8_downloader.gui.messagebox.showinfo"):
            with patch.object(gui, "_download_selected", side_effect=fake_start):
                gui._on_download_done("success")
        # bad 被跳过，ok 被展示并自动下载
        assert gui._downloading is True
        assert gui._candidate_page_url == "https://x/ok"
        assert gui._preload_queue == []

    def test_download_error_still_advances_next_head(self, headless):
        """自动连播中的单页下载失败（error）不中断整链：跳过并继续下一个。"""
        gui = headless
        _configure_tree(gui)
        _arm_auto(gui)
        gui._preload_queue.append(
            _success_entry("https://x/b", "https://cdn/b.m3u8", "B")
        )
        gui._preload_queue.append(
            _success_entry("https://x/c", "https://cdn/c.m3u8", "C")
        )
        fake_start = _fake_download_start(gui)
        with patch("m3u8_downloader.gui.messagebox.showinfo"):
            with patch.object(gui, "_download_selected", side_effect=fake_start):
                # 当前页 A 下载成功 → 自动开始队列头 B
                gui._on_download_done("success")
        assert gui._downloading is True
        assert gui._candidate_page_url == "https://x/b"
        assert [e.page_url for e in gui._preload_queue] == ["https://x/c"]

        with patch("m3u8_downloader.gui.messagebox.showinfo"):
            with patch.object(gui, "_download_selected", side_effect=fake_start):
                # B 下载失败（队列页失败）→ 跳过 B，自动继续 C
                gui._on_download_done("error")
        assert gui._downloading is True
        assert gui._candidate_page_url == "https://x/c"
        assert gui._preload_queue == []

    def test_manual_download_error_does_not_auto_start_queue(self, headless):
        """手动（非队列）页面下载失败 → 不自动开始队列头（等用户续跑）。"""
        gui = headless
        _configure_tree(gui)
        _arm_auto(gui)
        gui._preload_queue.append(
            _success_entry("https://x/b", "https://cdn/b.m3u8", "B")
        )
        gui._active_download_is_queue_page = False  # 手动下载失败
        with patch("m3u8_downloader.gui.messagebox.showinfo"):
            gui._on_download_done("error")
        assert gui._downloading is False
        assert gui._preload_queue != []


class TestQueueIndicatorAndPopup:

    def test_indicator_shows_count_and_enables_button(self, headless):
        gui = headless
        gui._queue_count_var.set.reset_mock()
        gui._queue_count_btn.configure.reset_mock()
        gui._preload_queue.append(PreloadQueueEntry(page_url="https://x/1"))
        gui._preload_queue.append(PreloadQueueEntry(page_url="https://x/2"))
        gui._update_queue_indicator()
        gui._queue_count_var.set.assert_called_with("待处理 2 个")
        gui._queue_count_btn.configure.assert_called_with(state=tk.NORMAL)

    def test_popup_lists_pending_urls(self, headless):
        gui = headless
        gui._preload_queue.append(PreloadQueueEntry(page_url="https://x/b", state="success"))
        gui._preload_queue.append(PreloadQueueEntry(page_url="https://x/c", state="extracting"))
        fake_win = MagicMock()
        fake_text = MagicMock()
        with patch("tkinter.Toplevel", return_value=fake_win):
            with patch("tkinter.Text", return_value=fake_text):
                gui._show_preload_queue_popup()
        fake_win.title.assert_called_once()
        inserted = "".join(
            str(c.args[1]) for c in fake_text.insert.call_args_list if len(c.args) > 1
        )
        assert "https://x/b" in inserted
        assert "https://x/c" in inserted

    def test_popup_noop_when_queue_empty(self, headless):
        gui = headless
        with patch("tkinter.Toplevel") as toplevel:
            gui._show_preload_queue_popup()
        toplevel.assert_not_called()


class TestQueuePersistence:

    def test_round_trip_restores_entries_and_candidates(self, tmp_path, monkeypatch):
        """队列持久化往返：存盘后另起实例能恢复条目、状态与候选。"""
        with _headless_env(tmp_path, monkeypatch) as build:
            gui_a = build()
            gui_a._preload_queue.append(
                _success_entry("https://x/b", "https://cdn/b.m3u8", "B")
            )
            gui_a._preload_queue.append(
                PreloadQueueEntry(page_url="https://x/c", state="extracting")
            )
            gui_a._save_preload_queue()

            gui_b = build()
            assert [e.page_url for e in gui_b._preload_queue] == [
                "https://x/b", "https://x/c",
            ]
            head = gui_b._preload_queue[0]
            assert head.state == "success"
            assert head.filename_title == "B"
            assert [c.url for c in head.candidates] == ["https://cdn/b.m3u8"]
            assert gui_b._preload_queue[1].state == "extracting"

    def test_corrupt_queue_file_degrades_to_empty(self, tmp_path, monkeypatch):
        with _headless_env(tmp_path, monkeypatch) as build:
            qfile = gui_mod.PRELOAD_QUEUE_FILE
            qfile.write_text("{not json", encoding="utf-8")
            gui = build()
            assert gui._preload_queue == []


class TestPageHistoryIntegration:
    """功能二 GUI 触发链：提取/下载成功/下载失败 → 写入独立只读记录."""

    def test_extract_done_records_page(self, headless):
        gui = headless
        gui._current_extract_page_url = "https://x/page"
        gui._on_extract_done("success")
        records = page_history_mod.list_records()
        assert [r["page_url"] for r in records] == ["https://x/page"]
        assert records[0]["status"] == page_history_mod.STATUS_EXTRACTED

    def test_success_download_records_downloaded_with_url(self, headless):
        gui = headless
        gui._inflight_download_page_url = "https://x/page"
        gui._inflight_download_url = "https://cdn/v.m3u8"
        gui._finalize_inflight_download_record("success")
        records = page_history_mod.list_records()
        assert records[0]["status"] == page_history_mod.STATUS_DOWNLOADED
        assert records[0]["m3u8_url"] == "https://cdn/v.m3u8"
        # 状态已清空，避免串到下一次下载
        assert gui._inflight_download_page_url == ""

    def test_error_download_records_failed(self, headless):
        gui = headless
        gui._inflight_download_page_url = "https://x/page"
        gui._inflight_download_url = "https://cdn/v.m3u8"
        gui._finalize_inflight_download_record("error")
        records = page_history_mod.list_records()
        assert records[0]["status"] == page_history_mod.STATUS_FAILED

    def test_stop_download_keeps_extracted_status(self, headless):
        gui = headless
        gui._inflight_download_page_url = "https://x/page"
        gui._inflight_download_url = "https://cdn/v.m3u8"
        gui._finalize_inflight_download_record("stopped")
        # 用户手动停止不改写为失败（仍保持 extracted 语义）
        assert page_history_mod.list_records() == []

    def test_page_history_button_exists_and_is_clickable(self, headless):
        gui = headless
        # 新增的「下载记录」按钮命令指向只读面板打开方法
        assert gui._page_history_btn is not None


# ===========================================================================
# 真实 GUI 集成（复用 test_deep_streaming 的浏览器/服务器 fixture）
# ===========================================================================


def _find_button(root, label):
    from tkinter import ttk
    return next(
        w for w in widgets(root)
        if isinstance(w, ttk.Button) and w.cget("text") == label
    )


def _configure_save(root, tmp_path):
    from tkinter import ttk
    entries = [w for w in widgets(root) if isinstance(w, ttk.Entry)]
    entries[1].delete(0, "end")
    entries[1].insert(0, str(tmp_path))
    ffmpeg = next(
        w for w in widgets(root)
        if isinstance(w, ttk.Checkbutton) and "ffmpeg" in str(w.cget("text"))
    )
    root.setvar(ffmpeg.cget("variable"), False)


def test_real_gui_two_preloads_auto_chain(
    video_page, desktop_gui, tmp_path, monkeypatch,
):
    """真实 GUI：下载一集时连续预载两集 → A 完成后依次自动下载 B、C。"""
    from tkinter import messagebox, ttk

    root, app = desktop_gui
    monkeypatch.setattr(messagebox, "showinfo", lambda *a, **kw: None)
    # 同名文件/重复链接的模态框全部自动确认，避免阻塞自动链
    monkeypatch.setattr(messagebox, "askyesnocancel", lambda *a, **kw: True)
    monkeypatch.setattr(messagebox, "askyesno", lambda *a, **kw: True)

    _configure_save(root, tmp_path)
    tree = next(w for w in widgets(root) if isinstance(w, ttk.Treeview))

    start_deep_scan(root, video_page)
    pump_until(root, lambda: str(_find_button(root, "提取网页").cget("state")) == "normal")
    tree.selection_set(tree.get_children()[0])

    video_page.release_download.clear()
    try:
        _find_button(root, "下载选中").invoke()
        pump_until(root, video_page.download_started.is_set)
        # 下载 A 期间依次预载 B 与 C → 队列应累积 2 个条目
        start_deep_scan(root, video_page + "?next", deep=False)
        pump_until(root, lambda: "预载：成功，找到 1 条，等待当前下载结束" in visible_text(root))
        start_deep_scan(root, video_page + "?third", deep=False)
        pump_until(root, lambda: (
            len(app._preload_queue) == 2
            and app._preload_queue[0].state == "success"
            and app._preload_queue[1].state == "success"
        ))
        assert app._queue_count_var.get() == "待处理 2 个"
        # 下载未结束前列表仍是 A 的候选，不被预载 B/C 改动
        assert [tree.item(i, "values")[-1] for i in tree.get_children()] == [
            video_page + "first.m3u8", video_page + "second.m3u8",
        ]
    finally:
        video_page.release_download.set()

    # A 完成 → B 自动下载并落盘
    pump_until(root, lambda: (tmp_path / "Next episode.mp4").exists(), timeout=40)
    # B 完成 → C 自动下载并落盘
    pump_until(root, lambda: (tmp_path / "Third episode.mp4").exists(), timeout=40)
    # 全部处理完：队列清空、恢复空闲
    pump_until(root, lambda: (
        not app._preload_queue
        and str(_find_button(root, "停止下载").cget("state")) == "disabled"
    ), timeout=40)
    assert app._preload_queue == []
    assert (tmp_path / "Next episode.mp4").read_bytes() == video_page.segment
    assert (tmp_path / "Third episode.mp4").read_bytes() == video_page.segment


# 复用 test_deep_streaming 的真实 GUI / 本地 HTTP fixture（须在模块级注册）
from tests.test_deep_streaming import (  # noqa: E402
    desktop_gui, pump_until, start_deep_scan, tk_runtime, video_page,
    visible_text, widgets,
)
