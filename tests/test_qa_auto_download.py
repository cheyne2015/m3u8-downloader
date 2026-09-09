"""QA 独立回归验证：自动选中 / 自动下载 / 连续下载（commit 2d18b81）。

与工程师自测（``tests/test_gui.py``）互补：本文件由 QA 独立设计，从行为层复核
产品契约，重点覆盖

* 自动选中的边界（并列取第一个、全 0 体积、``-`` 出现在不同位置、全 ``-``）；
* 自动下载的 4 个门控条件的组合（勾选 / 会话手动标志 / 提取结果 / 手动优先）；
* 预载交接（``_pending_extract_result``）的落库与清空；
* 连续下载弹窗控制；
* 两个新配置键的默认值、**真实往返**（存盘后另起实例重载恢复）与缺键回退；
* **真实 GUI**：自动下载开启时，预载的「列表 / 标题切换」是否仍然正确
  （对应工程师在 ``test_preload_swaps_list_and_title_only_after_download``
  里关闭自动下载的适配是否掩盖冲突）。
"""

from __future__ import annotations

import json
import tempfile
from contextlib import ExitStack, contextmanager
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from m3u8_downloader.extractor import Candidate
from m3u8_downloader.gui import M3U8DownloaderGUI


# ---------------------------------------------------------------------------
# 基础设施：无头 mock Tk（QA 自实现，不复用工程师测试里的 helper）
# ---------------------------------------------------------------------------


class FakeVar:
    """可断言的 Tk 变量替身：记录 set 调用，便于验证「文件名是否被清空」。"""

    def __init__(self, value=""):
        self.value = value
        self.set_calls: list = []

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
        # 注意：BooleanVar 必须是「可写可读」的替身，否则 _load_config 的 set()
        # 不会反映到 get()，持久化往返就无法从值上验证（只能验证 set 被调用）。
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
def headless_gui(config_path=None):
    """构造一个 tkinter 被 mock 的 GUI 实例；可选重定向配置文件路径。"""
    with ExitStack() as stack:
        for p in _tk_patches():
            stack.enter_context(p)
        if config_path is not None:
            stack.enter_context(patch("m3u8_downloader.gui.GUI_CONFIG_PATH", config_path))
        # 隔离预载队列持久化文件：避免读取本机真实 ~/.m3u8-downloader/preload_queue.json
        # 造成 _preload_queue 非空，使 _on_download_done 走自动连播分支而跳过单页交接逻辑。
        qfile = Path(tempfile.mkdtemp()) / "preload_queue.json"
        stack.enter_context(patch("m3u8_downloader.gui.PRELOAD_QUEUE_FILE", qfile))
        root = MagicMock()
        root.tk = MagicMock()
        root.after = MagicMock()
        root.clipboard_get = MagicMock(return_value="")
        yield M3U8DownloaderGUI(root)


def seed(gui, candidates):
    """模拟一次已完成的提取：候选与其 Treeview 行均已就位。"""
    gui._candidates = list(candidates)
    gui._candidate_items = {c.url: (f"row-{i}", i) for i, c in enumerate(candidates)}
    return gui


def arm(gui, candidates=None, *, auto=True, manual=True):
    """把 GUI 置于「自动下载各前置条件均满足」的状态。"""
    seed(gui, candidates or [Candidate(url="https://x/a.m3u8", title="A", duration=1.0)])
    gui._auto_download_var = FakeVar(auto)
    gui._session_manual_downloaded = manual
    return gui


# ===========================================================================
# A. 自动选中规则
# ===========================================================================


class TestQaAutoSelectRules:

    def test_tie_on_size_picks_first_in_list(self):
        """体积并列时取列表中的第一个（``max`` 的稳定性必须被验证）。"""
        with headless_gui() as gui:
            seed(gui, [
                Candidate(url="https://x/1.m3u8", duration=60.0, estimated_size=500),
                Candidate(url="https://x/2.m3u8", duration=60.0, estimated_size=500),
                Candidate(url="https://x/3.m3u8", duration=60.0, estimated_size=100),
            ])
            picked = gui._pick_auto_candidate()
            assert picked is not None
            assert picked[0].url == "https://x/1.m3u8"

    def test_all_sizes_zero_picks_first(self):
        """全部体积未知（0）且时长相同 → 仍取第一个（并列取第一个）。"""
        with headless_gui() as gui:
            seed(gui, [
                Candidate(url="https://x/1.m3u8", duration=60.0, estimated_size=0),
                Candidate(url="https://x/2.m3u8", duration=60.0, estimated_size=0),
            ])
            picked = gui._pick_auto_candidate()
            assert picked is not None and picked[0].url == "https://x/1.m3u8"

    @pytest.mark.parametrize("unknown_at", [0, 1, 2])
    def test_unknown_duration_anywhere_blocks(self, unknown_at):
        """``-`` 出现在任意位置都不满足「全部相同」→ 不自动选中。"""
        cands = [
            Candidate(url="https://x/1.m3u8", duration=60.0, estimated_size=100),
            Candidate(url="https://x/2.m3u8", duration=60.0, estimated_size=200),
            Candidate(url="https://x/3.m3u8", duration=60.0, estimated_size=300),
        ]
        cands[unknown_at] = Candidate(
            url=f"https://x/u{unknown_at}.m3u8", duration=0.0, estimated_size=999,
        )
        with headless_gui() as gui:
            seed(gui, cands)
            assert gui._pick_auto_candidate() is None

    def test_all_unknown_durations_block(self):
        """全部时长未知 → 不自动选中（已确认的保守解读）。"""
        with headless_gui() as gui:
            seed(gui, [
                Candidate(url="https://x/1.m3u8", duration=0.0, estimated_size=100),
                Candidate(url="https://x/2.m3u8", duration=0.0, estimated_size=900),
            ])
            assert gui._pick_auto_candidate() is None
            assert gui._auto_select_candidate() is None

    def test_single_candidate_with_unknown_duration_is_selected(self):
        """唯一候选即使时长未知也应自动选中（规则 1 优先于规则 3）。"""
        with headless_gui() as gui:
            seed(gui, [Candidate(url="https://x/only.m3u8", duration=0.0)])
            picked = gui._pick_auto_candidate()
            assert picked is not None
            assert picked[0].url == "https://x/only.m3u8"
            assert picked[1] == "唯一结果"

    def test_duration_compares_display_string_not_float(self):
        """比较的是 ``display_duration()`` 字符串：60.0 与 60.4 视为相同。"""
        with headless_gui() as gui:
            seed(gui, [
                Candidate(url="https://x/1.m3u8", duration=60.0, estimated_size=100),
                Candidate(url="https://x/2.m3u8", duration=60.4, estimated_size=900),
            ])
            assert gui._candidates[0].display_duration() == gui._candidates[1].display_duration()
            picked = gui._pick_auto_candidate()
            assert picked is not None and picked[0].url == "https://x/2.m3u8"

    def test_mixed_durations_block_selection_and_download(self):
        """混合时长 → 既不自动选中，也不自动下载。"""
        with headless_gui() as gui:
            arm(gui, [
                Candidate(url="https://x/1.m3u8", duration=60.0, estimated_size=100),
                Candidate(url="https://x/2.m3u8", duration=60.0, estimated_size=900),
                Candidate(url="https://x/3.m3u8", duration=120.0, estimated_size=900),
            ])
            with patch.object(gui, "_download_selected") as dl:
                gui._auto_select_and_download()
            dl.assert_not_called()
            gui._tree.selection_set.assert_not_called()

    def test_empty_candidate_list_is_safe(self):
        """空候选列表 → 不抛异常、不选中。"""
        with headless_gui() as gui:
            gui._candidates = []
            gui._candidate_items = {}
            assert gui._pick_auto_candidate() is None
            assert gui._auto_select_candidate() is None

    def test_missing_tree_row_returns_none(self):
        """候选存在但缺少对应列表行 → 不选中（防御性，不应抛异常）。"""
        with headless_gui() as gui:
            seed(gui, [Candidate(url="https://x/1.m3u8")])
            gui._candidate_items = {}
            assert gui._auto_select_candidate() is None


# ===========================================================================
# B. 自动下载门控
# ===========================================================================


class TestQaAutoDownloadGating:

    def test_all_gates_open_starts_download(self):
        """四个条件同时满足 → 自动下载启动。"""
        with headless_gui() as gui:
            arm(gui)
            with patch.object(gui, "_download_selected") as dl, patch.object(gui, "_log"):
                gui._on_extract_done("success")
            dl.assert_called_once_with()

    @pytest.mark.parametrize("result", ["stopped", "error", "empty", "pending"])
    def test_non_success_extract_never_downloads(self, result):
        """非 success 的提取结果一律不自动下载。"""
        with headless_gui() as gui:
            arm(gui)
            with patch.object(gui, "_download_selected") as dl:
                gui._on_extract_done(result)
            dl.assert_not_called()

    @pytest.mark.parametrize(
        "auto,manual,expect",
        [
            (False, True, False),   # 未勾选「自动下载」
            (True, False, False),   # 本次会话未手动下载过
            (False, False, False),  # 两者都不满足
            (True, True, True),     # 都满足
        ],
    )
    def test_gate_combinations(self, auto, manual, expect):
        """勾选框 × 会话手动标志 的四种组合。"""
        with headless_gui() as gui:
            arm(gui, auto=auto, manual=manual)
            with patch.object(gui, "_download_selected") as dl, patch.object(gui, "_log"):
                gui._auto_select_and_download()
            assert dl.called is expect
            # 无论是否下载，自动选中都应发生（门控只影响下载）
            gui._tree.selection_set.assert_called_once_with("row-0")

    def test_manual_first_skips_already_manually_downloaded_url(self):
        """手动优先：已手动触发过的链接不再被自动下载，但仍会选中。"""
        with headless_gui() as gui:
            arm(gui)
            gui._manual_downloaded_urls = {"https://x/a.m3u8"}
            with patch.object(gui, "_download_selected") as dl:
                gui._auto_select_and_download()
            dl.assert_not_called()
            gui._tree.selection_set.assert_called_once_with("row-0")

    def test_manual_first_only_blocks_the_matching_url(self):
        """手动优先只拦截匹配链接；选中另一个未手动下载过的链接时仍自动下载。"""
        with headless_gui() as gui:
            arm(gui, [Candidate(url="https://x/b.m3u8", title="B", duration=1.0)])
            gui._manual_downloaded_urls = {"https://x/a.m3u8"}
            with patch.object(gui, "_download_selected") as dl, patch.object(gui, "_log"):
                gui._auto_select_and_download()
            dl.assert_called_once_with()

    def test_no_auto_processing_while_downloading(self):
        """下载进行中 → 既不自动选中也不自动下载（预载未交接）。"""
        with headless_gui() as gui:
            arm(gui)
            gui._downloading = True
            with patch.object(gui, "_download_selected") as dl:
                gui._auto_select_and_download()
            dl.assert_not_called()
            gui._tree.selection_set.assert_not_called()

    def test_extract_success_while_downloading_only_stages_result(self):
        """下载中完成提取：只暂存结果，不立即下载。"""
        with headless_gui() as gui:
            arm(gui)
            gui._downloading = True
            with patch.object(gui, "_download_selected") as dl:
                gui._on_extract_done("success")
            dl.assert_not_called()
            assert gui._pending_extract_result == "success"

    @pytest.mark.parametrize("stopped_result", ["stopped", "error", "empty"])
    def test_extract_stopped_while_downloading_does_not_stage_success(self, stopped_result):
        """下载中「停止提取」/失败/空结果 → 暂存值不是 success，下载完成后不接力。"""
        with headless_gui() as gui:
            arm(gui)
            gui._downloading = True
            gui._on_extract_done(stopped_result)
            assert gui._pending_extract_result != "success"


# ===========================================================================
# C. 预载交接（_pending_extract_result）
# ===========================================================================


class TestQaPreloadHandoff:

    def test_success_handoff_triggers_chain(self):
        """A 正常完成 + B 预载成功 → 自动接力下载 B。"""
        with headless_gui() as gui:
            arm(gui, [Candidate(url="https://x/b.m3u8", title="B", duration=1.0)])
            gui._pending_extract_result = "success"
            with patch.object(gui, "_download_selected") as dl, patch.object(gui, "_log"):
                gui._on_download_done("success")
            dl.assert_called_once_with()

    @pytest.mark.parametrize(
        "download_result,pending,expect",
        [
            ("success", "success", True),
            ("success", "stopped", False),
            ("success", "error", False),
            ("success", "", False),
            ("stopped", "success", False),
            ("error", "success", False),
        ],
    )
    def test_handoff_matrix(self, download_result, pending, expect):
        """A 的下载结果 × B 的提取结果 的完整组合矩阵。"""
        with headless_gui() as gui:
            arm(gui, [Candidate(url="https://x/b.m3u8", title="B", duration=1.0)])
            gui._pending_extract_result = pending
            with patch.object(gui, "_download_selected") as dl, patch.object(gui, "_log"):
                gui._on_download_done(download_result)
            assert dl.called is expect

    def test_pending_result_always_cleared(self):
        """无论是否接力，``_pending_extract_result`` 都必须被清空，避免重复接力。"""
        with headless_gui() as gui:
            arm(gui, [Candidate(url="https://x/b.m3u8", title="B", duration=1.0)])
            gui._pending_extract_result = "success"
            with patch.object(gui, "_download_selected"), patch.object(gui, "_log"):
                gui._on_download_done("success")
                assert gui._pending_extract_result == ""
                gui._on_download_done("success")
            assert gui._pending_extract_result == ""

    def test_filename_not_cleared_when_chain_starts(self):
        """接力自动下载启动后，文件名栏不应被清空（否则会写成 output.mp4）。"""
        with headless_gui() as gui:
            arm(gui, [Candidate(url="https://x/b.m3u8", title="B", duration=1.0)])
            gui._pending_extract_result = "success"
            gui._filename_var = FakeVar("Next episode")

            def fake_download_selected():
                gui._downloading = True

            with patch.object(gui, "_download_selected", side_effect=fake_download_selected):
                with patch.object(gui, "_log"):
                    gui._on_download_done("success")
            assert "" not in gui._filename_var.set_calls, "文件名栏被错误清空"
            assert gui._filename_var.get() == "Next episode"


# ===========================================================================
# D. 连续下载
# ===========================================================================


class TestQaContinuousDownload:

    @pytest.mark.parametrize("checked,popup", [(True, False), (False, True)])
    def test_popup_control(self, checked, popup):
        """勾选「连续下载」→ 跳过弹窗；未勾选 → 仍弹「下载完成！」。"""
        with headless_gui() as gui:
            gui._continuous_download_var = FakeVar(checked)
            with patch("m3u8_downloader.gui.messagebox.showinfo") as info:
                gui._on_download_done("success")
            assert info.called is popup
            if popup:
                info.assert_called_once_with("提示", "下载完成！")

    @pytest.mark.parametrize("result", ["stopped", "error"])
    def test_no_popup_on_non_success_even_unchecked(self, result):
        """非成功结果本来就不弹窗，与勾选状态无关。"""
        with headless_gui() as gui:
            gui._continuous_download_var = FakeVar(False)
            with patch("m3u8_downloader.gui.messagebox.showinfo") as info:
                gui._on_download_done(result)
            info.assert_not_called()


# ===========================================================================
# E. 两个新配置键：默认值 / 保存 / 往返 / 缺键回退
# ===========================================================================


class TestQaPreferencePersistence:

    def test_defaults(self, tmp_path):
        """默认：auto_download=True、continuous_download=False。"""
        with headless_gui(tmp_path / "p" / "cfg.json") as gui:
            assert bool(gui._auto_download_var.get()) is True
            assert bool(gui._continuous_download_var.get()) is False

    def test_round_trip_through_a_fresh_instance(self, tmp_path):
        """真实往返：实例 A 保存 → 另起实例 B 重载，两个键都能恢复。"""
        cfg = tmp_path / "p" / "cfg.json"
        with headless_gui(cfg) as gui_a:
            gui_a._auto_download_var = FakeVar(False)
            gui_a._continuous_download_var = FakeVar(True)
            gui_a._save_config()
            raw = json.loads(cfg.read_text(encoding="utf-8"))
            assert raw["auto_download"] is False
            assert raw["continuous_download"] is True

        with headless_gui(cfg) as gui_b:
            assert gui_b._load_config() is None
            assert bool(gui_b._auto_download_var.get()) is False
            assert bool(gui_b._continuous_download_var.get()) is True

    def test_round_trip_other_direction(self, tmp_path):
        """反向往返：auto=True / continuous=False 也能正确恢复（不被默认值掩盖）。"""
        cfg = tmp_path / "p" / "cfg.json"
        with headless_gui(cfg) as gui_a:
            gui_a._auto_download_var = FakeVar(True)
            gui_a._continuous_download_var = FakeVar(False)
            gui_a._save_config()
        with headless_gui(cfg) as gui_b:
            gui_b._load_config()
            assert bool(gui_b._auto_download_var.get()) is True
            assert bool(gui_b._continuous_download_var.get()) is False

    def test_legacy_config_without_new_keys_uses_defaults(self, tmp_path):
        """旧配置缺键 → 回退默认（自动下载开、连续下载关）。"""
        cfg = tmp_path / "p" / "cfg.json"
        cfg.parent.mkdir(parents=True, exist_ok=True)
        cfg.write_text(json.dumps({"workers": 8}), encoding="utf-8")
        with headless_gui(cfg) as gui:
            gui._load_config()
            assert bool(gui._auto_download_var.get()) is True
            assert bool(gui._continuous_download_var.get()) is False

    def test_corrupt_config_falls_back_to_defaults(self, tmp_path):
        """配置文件损坏 → 不应崩溃，回退默认值。"""
        cfg = tmp_path / "p" / "cfg.json"
        cfg.parent.mkdir(parents=True, exist_ok=True)
        cfg.write_text("{not json", encoding="utf-8")
        with headless_gui(cfg) as gui:
            gui._load_config()  # 不应抛异常
            assert bool(gui._auto_download_var.get()) is True
            assert bool(gui._continuous_download_var.get()) is False


# ===========================================================================
# F. 真实 GUI 集成（复用 test_deep_streaming 的浏览器/服务器 fixture）
# ===========================================================================

from tests.test_deep_streaming import (  # noqa: E402
    desktop_gui, pump_until, start_deep_scan, tk_runtime, video_page, visible_text,
    widgets,
)


def _gui_button(root, label):
    from tkinter import ttk
    return next(w for w in widgets(root) if isinstance(w, ttk.Button) and w.cget("text") == label)


def _configure_dir_and_ffmpeg(root, tmp_path):
    from tkinter import ttk
    entries = [w for w in widgets(root) if isinstance(w, ttk.Entry)]
    entries[1].delete(0, "end")
    entries[1].insert(0, str(tmp_path))
    ffmpeg = next(
        w for w in widgets(root)
        if isinstance(w, ttk.Checkbutton) and "ffmpeg" in str(w.cget("text"))
    )
    root.setvar(ffmpeg.cget("variable"), False)
    return entries


def test_qa_auto_download_on_does_not_break_preload_list_and_title_swap(
    video_page, desktop_gui, tmp_path, monkeypatch,
):
    """【重点】自动下载保持开启时，预载的列表/标题切换仍然正确，且 B 被自动下载。

    这是针对工程师在 ``test_preload_swaps_list_and_title_only_after_download``
    中用 ``app._auto_download_var.set(False)`` 做隔离的复核：确认该隔离只是
    「避免链式下载干扰原断言」，而没有掩盖「预载切换」与「自动下载」的冲突。
    """
    from tkinter import messagebox, ttk

    root, app = desktop_gui
    monkeypatch.setattr(messagebox, "showinfo", lambda *a, **kw: None)
    entries = _configure_dir_and_ffmpeg(root, tmp_path)
    tree = next(w for w in widgets(root) if isinstance(w, ttk.Treeview))

    # 前置：新会话默认「自动下载」是勾选的
    assert bool(app._auto_download_var.get()) is True

    start_deep_scan(root, video_page)
    pump_until(root, lambda: str(_gui_button(root, "停止提取").cget("state")) == "disabled")
    assert "共 2 条，已选 1 条" in visible_text(root), "自动选中未生效"

    selected = tree.get_children()[0]
    tree.selection_set(selected)
    entries[2].delete(0, "end")
    entries[2].insert(0, "current.mp4")

    video_page.release_download.clear()
    try:
        _gui_button(root, "下载选中").invoke()
        pump_until(root, video_page.download_started.is_set)
        # 下载 A 期间预载下一集 B
        start_deep_scan(root, video_page + "?next", deep=False)
        pump_until(root, lambda: "预载：成功，找到 1 条，等待当前下载结束" in visible_text(root))
        # 关键：下载未结束前，列表与文件名都不应被预载改动
        assert entries[2].get() == "current.mp4"
        assert len(tree.get_children()) == 2
    finally:
        video_page.release_download.set()

    # A 完成后：B 被自动下载（无需再点任何按钮）
    pump_until(root, lambda: (tmp_path / "Next episode.mp4").exists(), timeout=30)

    # 列表已切换为 B 的候选，且标题/文件名切换正确
    assert [tree.item(i, "values")[-1] for i in tree.get_children()] == [video_page + "next.m3u8"]
    assert entries[2].get() == "Next episode"
    assert "共 1 条，已选 1 条" in visible_text(root)
    # A 也正常落盘（用的是 A 的文件名，未被预载标题覆盖）
    assert (tmp_path / "current.mp4").read_bytes() == video_page.segment
    assert (tmp_path / "Next episode.mp4").read_bytes() == video_page.segment


def test_qa_no_auto_download_before_any_manual_download(
    video_page, desktop_gui, tmp_path, monkeypatch,
):
    """本次会话从未手动下载过 → 提取完成后只自动选中，不产生任何文件。"""
    from tkinter import messagebox, ttk

    root, app = desktop_gui
    monkeypatch.setattr(messagebox, "showinfo", lambda *a, **kw: None)
    _configure_dir_and_ffmpeg(root, tmp_path)
    tree = next(w for w in widgets(root) if isinstance(w, ttk.Treeview))

    start_deep_scan(root, video_page)
    pump_until(root, lambda: str(_gui_button(root, "停止提取").cget("state")) == "disabled")
    assert "共 2 条，已选 1 条" in visible_text(root)
    assert app._session_manual_downloaded is False

    for _ in range(15):
        root.update()
    assert not list(tmp_path.glob("*.mp4")), "未手动下载过却自动下载了"


def test_qa_stop_extract_during_preload_blocks_chain(
    video_page, desktop_gui, tmp_path, monkeypatch,
):
    """预载期间点「停止提取」→ A 下载完成后不自动开始 B。"""
    from tkinter import messagebox, ttk

    root, app = desktop_gui
    monkeypatch.setattr(messagebox, "showinfo", lambda *a, **kw: None)
    _configure_dir_and_ffmpeg(root, tmp_path)
    tree = next(w for w in widgets(root) if isinstance(w, ttk.Treeview))

    start_deep_scan(root, video_page)
    pump_until(root, lambda: str(_gui_button(root, "停止提取").cget("state")) == "disabled")
    tree.selection_set(tree.get_children()[0])

    video_page.release_download.clear()
    video_page.release_navigation.clear()
    try:
        _gui_button(root, "下载选中").invoke()
        pump_until(root, video_page.download_started.is_set)
        # 预载一个导航会卡住的网页，在其未完成时点「停止提取」
        start_deep_scan(root, video_page + "?hang", deep=True)
        pump_until(root, video_page.navigation_started.is_set, timeout=20)
        _gui_button(root, "停止提取").invoke()
        pump_until(
            root,
            lambda: app._pending_extract_result == "stopped",
            timeout=15,
        )
        # 下载中「停止提取」→ 暂存为 stopped，A 完成后不应接力
        assert app._pending_extract_result == "stopped"
    finally:
        video_page.release_navigation.set()
        video_page.release_download.set()

    pump_until(root, lambda: str(_gui_button(root, "停止下载").cget("state")) == "disabled")
    for _ in range(15):
        root.update()
    assert not (tmp_path / "Next episode.mp4").exists(), "预载被停止却仍自动下载了 B"
    assert (tmp_path / "Streaming test.mp4").exists(), "A 本身应正常落盘"


def test_qa_continuous_download_checkbox_suppresses_popup(
    video_page, desktop_gui, tmp_path, monkeypatch,
):
    """真实 GUI：勾选「连续下载」后成功下载不再弹「下载完成！」。"""
    from tkinter import messagebox, ttk

    popups = []
    monkeypatch.setattr(messagebox, "showinfo", lambda *a, **kw: popups.append(a))
    # 屏蔽原生模态框（同名文件 / 重复链接），否则无人点击会永久阻塞
    monkeypatch.setattr(messagebox, "askyesnocancel", lambda *a, **kw: True)
    monkeypatch.setattr(messagebox, "askyesno", lambda *a, **kw: True)

    root, app = desktop_gui
    _configure_dir_and_ffmpeg(root, tmp_path)
    tree = next(w for w in widgets(root) if isinstance(w, ttk.Treeview))

    start_deep_scan(root, video_page)
    pump_until(root, lambda: str(_gui_button(root, "停止提取").cget("state")) == "disabled")
    rows = tree.get_children()
    assert len(rows) == 2

    # 未勾选：第一次下载完成应弹「下载完成！」
    assert bool(app._continuous_download_var.get()) is False
    tree.selection_set(rows[0])
    _gui_button(root, "下载选中").invoke()
    pump_until(root, lambda: str(_gui_button(root, "停止下载").cget("state")) == "disabled")
    assert popups, "未勾选「连续下载」却没有弹出「下载完成！」"

    # 勾选后再下一次：不弹窗。关掉自动下载，避免其它路径干扰本断言
    app._auto_download_var.set(False)
    app._continuous_download_var.set(True)
    popups.clear()
    tree.selection_set(rows[1])
    _gui_button(root, "下载选中").invoke()
    pump_until(root, lambda: str(_gui_button(root, "停止下载").cget("state")) == "disabled")
    assert not popups, "勾选「连续下载」后仍然弹窗"
