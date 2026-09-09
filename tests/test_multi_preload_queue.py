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

    def test_preload_extract_error_is_skipped_and_queue_clears(self, headless):
        """P1-A：预载页提取抛异常 → 条目被 finalize 为 error → 跳过后队列清空。

        回归背景：worker 异常路径此前只发 extract_done(error)，不发
        preloaded_extract，导致队头条目永久停在 extracting、整链卡死。
        """
        gui = headless
        _configure_tree(gui)
        # 下载中 B 入队后提取异常 → worker 现发 preloaded_extract(ERROR)
        gui._downloading = True
        gui._preload_queue.append(
            PreloadQueueEntry(page_url="https://x/bad", state="extracting")
        )
        with patch.object(gui, "_log"):
            gui._handle_message(
                "preloaded_extract",
                PreloadResult([], "", "", "https://x/bad", PreloadState.ERROR),
            )
        assert gui._preload_queue[0].state == "error"
        gui._downloading = False
        with patch("m3u8_downloader.gui.messagebox.showinfo"):
            gui._on_download_done("success")
        assert gui._preload_queue == [], "失败页应被跳过，队列清空而非卡死"

    def test_preload_extract_error_continues_to_next_head(self, headless):
        """P1-A：队头预载失败(finalize 为 error)后，再预载的成功页能继续处理。

        模拟真实时序：B 是当时唯一在提取的尾部，失败后固化为 error；
        之后用户才预载 C 成功 → 下载结束自动跳过 B 并处理 C。
        """
        gui = headless
        _configure_tree(gui)
        _arm_auto(gui)
        gui._downloading = True
        # 1) 下载中预载 B 提取失败 → finalize 为 error
        gui._preload_queue.append(
            PreloadQueueEntry(page_url="https://x/bad", state="extracting")
        )
        with patch.object(gui, "_log"):
            gui._handle_message(
                "preloaded_extract",
                PreloadResult([], "", "", "https://x/bad", PreloadState.ERROR),
            )
        assert gui._preload_queue[0].state == "error"
        # 2) 随后用户预载 C 成功（追加在队尾）
        gui._preload_queue.append(
            PreloadQueueEntry(page_url="https://x/ok", state="extracting")
        )
        with patch.object(gui, "_log"):
            gui._handle_message(
                "preloaded_extract",
                PreloadResult(
                    [Candidate(url="https://cdn/ok.m3u8", title="OK")],
                    "OK", "OK - full", "https://x/ok", PreloadState.SUCCESS,
                ),
            )
        assert [e.state for e in gui._preload_queue] == ["error", "success"]
        # 3) 当前下载完成 → 跳过 error 头，自动展示并下载成功页 C
        gui._downloading = False
        fake_start = _fake_download_start(gui)
        with patch("m3u8_downloader.gui.messagebox.showinfo"):
            with patch.object(gui, "_download_selected", side_effect=fake_start):
                gui._on_download_done("success")
        assert gui._downloading is True
        assert gui._candidate_page_url == "https://x/ok"
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
            # 重启恢复：提取线程无法跨进程存活，extracting 归一化为可跳过的 error
            assert gui_b._preload_queue[1].state == "error"

    def test_reload_normalizes_extracting_and_does_not_stall(self, tmp_path, monkeypatch):
        """P1-B：重启恢复的 extracting 条目归一化为 error，续跑不卡链。"""
        with _headless_env(tmp_path, monkeypatch) as build:
            gui_a = build()
            gui_a._preload_queue.append(
                PreloadQueueEntry(page_url="https://x/b", state="extracting")
            )
            gui_a._save_preload_queue()

            # 重启：工作线程已死，extracting 应被归一化为可跳过的终态
            gui_b = build()
            assert [e.state for e in gui_b._preload_queue] == ["error"]
            _configure_tree(gui_b)
            # 手动下载成功后续跑：error 头被跳过、队列清空，不永久等待
            with patch("m3u8_downloader.gui.messagebox.showinfo"):
                gui_b._on_download_done("success")
            assert gui_b._preload_queue == []

    def test_corrupt_queue_file_degrades_to_empty(self, tmp_path, monkeypatch):
        with _headless_env(tmp_path, monkeypatch) as build:
            qfile = gui_mod.PRELOAD_QUEUE_FILE
            qfile.write_text("{not json", encoding="utf-8")
            gui = build()
            assert gui._preload_queue == []


def _manual_entry(page_url, m3u8_url, title):
    """构造一个「用户手动加入队列尾」的条目（手动优先，轮到即无条件下载）。"""
    candidate = Candidate(url=m3u8_url, title=title, duration=60.0, estimated_size=1024)
    return PreloadQueueEntry(
        page_url=page_url,
        state="success",
        candidates=[candidate],
        filename_title=title,
        page_title=title,
        manual=True,
        manual_urls=[m3u8_url],
    )


def _configure_current_page(gui, page_url, m3u8_url, title="A", filename="A.mp4"):
    """把 GUI 置于「当前正在展示某网页候选」状态（可被真实 _download_selected 处理）。"""
    _configure_tree(gui)
    gui._candidates = [
        Candidate(url=m3u8_url, title=title, duration=60.0, estimated_size=1024)
    ]
    gui._candidate_items = {m3u8_url: ("row-a", 0)}
    gui._candidate_page_url = page_url
    gui._page_title = title
    gui._filename_var = FakeVar(filename)
    gui._dir_var = FakeVar("C:/tmp/out")
    gui._tree.selection.return_value = ["row-a"]
    gui._tree.item.return_value = (
        1, "≈ 1MB", "01:00", "2 Mbps", "media", "普通", title, m3u8_url,
    )


class TestP2StopFreezesAutoChain:
    """P2：点「停止下载」后冻结自动连播链，直到用户再次手动点下载才解除。"""

    def test_stop_sets_freeze_flag(self, headless):
        gui = headless
        _configure_tree(gui)
        gui._preload_queue.append(
            _success_entry("https://x/b", "https://cdn/b.m3u8", "B")
        )
        with patch("m3u8_downloader.gui.messagebox.showinfo"):
            gui._on_download_done("stopped")
        assert gui._auto_chain_stopped is True
        assert len(gui._preload_queue) == 1, "停止后队列保留"

    def test_stop_with_empty_queue_does_not_freeze(self, headless):
        gui = headless
        with patch("m3u8_downloader.gui.messagebox.showinfo"):
            gui._on_download_done("stopped")
        assert gui._auto_chain_stopped is False

    def test_inflight_preload_completion_after_stop_does_not_auto_download(self, headless):
        """P2 核心：停止后「下载结束才完成」的预载页不得自动选中/自动下载。

        回归背景：preloaded_extract 的 not-downloading 分支此前会调
        _on_extract_done(success)→_auto_select_and_download，让停止后的链复活。
        """
        gui = headless
        _configure_tree(gui)
        _arm_auto(gui)
        gui._preload_queue.append(
            PreloadQueueEntry(page_url="https://x/b", state="extracting")
        )
        # 下载 A 期间预载 B；用户点停止 → A 结束
        with patch("m3u8_downloader.gui.messagebox.showinfo"):
            gui._on_download_done("stopped")
        assert gui._auto_chain_stopped is True

        # B 在下载结束后才提取完成（此时不在下载）
        gui._extracting = True
        with patch.object(gui, "_log"):
            gui._handle_message(
                "preloaded_extract",
                PreloadResult(
                    [Candidate(url="https://cdn/b.m3u8", title="B")],
                    "B", "B - full", "https://x/b", PreloadState.SUCCESS,
                ),
            )
        # 条目已固化 success，但绝不能自动开始下载 B
        assert gui._preload_queue[0].state == "success"
        assert gui._downloading is False
        assert gui._auto_chain_stopped is True
        # 即使再尝试推进队列也保持冻结
        with patch.object(gui, "_download_selected") as dl:
            gui._advance_preload_queue_when_idle()
        dl.assert_not_called()
        assert len(gui._preload_queue) == 1

    def test_manual_download_clears_freeze_and_resumes(self, headless):
        """用户手动点「下载选中」清除冻结标志，之后队列可正常续跑。"""
        gui = headless
        _configure_tree(gui)
        _arm_auto(gui)
        gui._auto_chain_stopped = True
        # 无选中行 → _download_selected 清标志后早退
        gui._tree.selection.return_value = []
        gui._download_selected()
        assert gui._auto_chain_stopped is False

        # 清除后推进队列可恢复（队头成功页被展示并下载）
        gui._preload_queue.append(
            _success_entry("https://x/b", "https://cdn/b.m3u8", "B")
        )
        fake_start = _fake_download_start(gui)
        with patch.object(gui, "_download_selected", side_effect=fake_start):
            gui._advance_preload_queue_when_idle()
        assert gui._downloading is True
        assert gui._preload_queue == []
        assert gui._candidate_page_url == "https://x/b"


class TestManualUnifiedQueueTail:
    """新澄清：手动触发的下载加入统一队列尾（先处理队头），队列空时直接下载。"""

    def test_manual_download_joins_tail_when_queue_nonempty(self, headless):
        """遗留队列 [B] + 手动下载当前页 A → A 追加为 manual 队尾 [B, A]。"""
        gui = headless
        gui._preload_queue.append(
            _success_entry("https://x/b", "https://cdn/b.m3u8", "B")
        )
        _configure_current_page(gui, "https://x/A", "https://cdn/a.m3u8", "A")
        _arm_auto(gui)
        with patch.object(gui, "_advance_preload_queue_when_idle") as advance:
            with patch.object(gui, "_run_next_job") as rnj:
                with patch.object(gui, "_log"):
                    gui._download_selected()
        # A 不直接下载：加入队尾，由 advance 先处理队头 B
        assert [e.page_url for e in gui._preload_queue] == [
            "https://x/b", "https://x/A",
        ]
        tail = gui._preload_queue[-1]
        assert tail.manual is True
        assert tail.manual_urls == ["https://cdn/a.m3u8"]
        assert advance.call_count == 1
        rnj.assert_not_called()
        assert gui._pending_jobs == []
        assert gui._session_manual_downloaded is True
        assert "https://cdn/a.m3u8" in gui._manual_downloaded_urls

    def test_manual_download_direct_when_queue_empty(self, headless):
        """队列为空 → 手动下载当前页直接开始（不建队列）。"""
        gui = headless
        _configure_current_page(gui, "https://x/A", "https://cdn/a.m3u8", "A")
        _arm_auto(gui)
        with patch.object(gui, "_run_next_job") as rnj:
            with patch.object(gui, "_log"):
                gui._download_selected()
        rnj.assert_called_once()
        assert gui._preload_queue == []
        assert len(gui._pending_jobs) == 1
        assert gui._pending_jobs[0].url == "https://cdn/a.m3u8"

    def test_manual_download_direct_when_head_is_current_page(self, headless):
        """队头正是当前展示页 → 直接续跑队头（不重复入队）。"""
        gui = headless
        gui._preload_queue.append(
            _success_entry("https://x/A", "https://cdn/a.m3u8", "A")
        )
        _configure_current_page(gui, "https://x/A", "https://cdn/a.m3u8", "A")
        _arm_auto(gui)
        with patch.object(gui, "_run_next_job") as rnj:
            with patch.object(gui, "_log"):
                gui._download_selected()
        rnj.assert_called_once()
        assert [e.page_url for e in gui._preload_queue] == ["https://x/A"]
        assert gui._preload_queue[-1].manual is False

    def test_chain_processes_ready_head_before_manual_tail(self, headless):
        """顺序：手动 A 入队后 → 先处理已就绪队头 B，B 完成后再下载 A。"""
        gui = headless
        _configure_tree(gui)
        _arm_auto(gui)
        gui._preload_queue.append(
            _success_entry("https://x/b", "https://cdn/b.m3u8", "B")
        )
        gui._preload_queue.append(
            _manual_entry("https://x/A", "https://cdn/a.m3u8", "A")
        )
        fake_start = _fake_download_start(gui)
        # B 完成 → 自动推进：先展示并下载队头 B
        with patch("m3u8_downloader.gui.messagebox.showinfo"):
            with patch.object(gui, "_download_selected", side_effect=fake_start):
                gui._on_download_done("success")
        assert gui._downloading is True
        assert gui._candidate_page_url == "https://x/b"
        assert [e.page_url for e in gui._preload_queue] == ["https://x/A"]
        # B 下载完成 → 推进到 manual 队尾 A
        with patch("m3u8_downloader.gui.messagebox.showinfo"):
            with patch.object(gui, "_download_selected", side_effect=fake_start):
                gui._on_download_done("success")
        assert gui._downloading is True
        assert gui._candidate_page_url == "https://x/A"
        assert gui._preload_queue == []

    def test_manual_tail_downloads_even_when_auto_gates_closed(self, headless):
        """手动入队条目轮到时不设自动下载门控：即使未勾选也直接下载。"""
        gui = headless
        _configure_tree(gui)
        gui._auto_download_var = FakeVar(False)   # 自动下载门关闭
        gui._session_manual_downloaded = False     # 会话尚未手动下载过
        gui._preload_queue.append(
            _manual_entry("https://x/A", "https://cdn/a.m3u8", "A")
        )
        fake_start = _fake_download_start(gui)
        with patch("m3u8_downloader.gui.messagebox.showinfo"):
            with patch.object(gui, "_download_selected", side_effect=fake_start) as dl:
                gui._on_download_done("success")
        dl.assert_called_once()
        assert gui._downloading is True
        assert gui._preload_queue == []

    def test_auto_head_with_gates_closed_is_not_auto_downloaded(self, headless):
        """对照：普通自动条目门关闭时不下载（保留队头），只有 manual 条目绕过门控。"""
        gui = headless
        _configure_tree(gui)
        gui._auto_download_var = FakeVar(False)
        gui._session_manual_downloaded = False
        gui._preload_queue.append(
            _success_entry("https://x/b", "https://cdn/b.m3u8", "B")
        )
        fake_start = _fake_download_start(gui)
        with patch("m3u8_downloader.gui.messagebox.showinfo"):
            with patch.object(gui, "_download_selected", side_effect=fake_start) as dl:
                gui._on_download_done("success")
        dl.assert_not_called()
        assert gui._downloading is False
        assert len(gui._preload_queue) == 1


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
    pump_until(root, lambda: str(_find_button(root, "停止提取").cget("state")) == "disabled")
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
