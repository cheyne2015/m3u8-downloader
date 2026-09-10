"""QA 独立回归验证：commit 7468553「下载记录 m3u8 ↔ 打开位置 一一对应」。

与工程师自测（``tests/test_page_history.py`` / ``tests/test_gui.py`` 新增用例）
刻意采用**不同方法**，避免同向盲区：

1. **原始磁盘断言**：写盘后直接用 ``json.load`` 读原始文件校验结构（条目级
   ``output_path``、``downloads`` 顺序、冗余 ``m3u8_url`` 拼接），再重载比对——
   证明「真的落盘」，而不是只在内存里对。
2. **可控时钟**：monkeypatch ``page_history._now`` 为可控时钟（严格递增 / 冻结），
   摆脱真实秒级时钟的不确定性，从而能**确定性地**构造「同一秒并列」。
3. **随机化不变量（fuzz）**：随机下载序列 + 独立 oracle，校验 ``downloads`` 恒为
   「按最后一次下载时间旧→新」，``latest_download`` 恒为最后下载的那条及其最新路径。
4. **真按钮 + 真 Popen**：面板用自建的「录制型」假控件渲染后，**真的调用**「打开
   位置」按钮的 command，让 ``_reveal_path_in_file_manager`` 原样执行（含
   ``os.path.normpath`` 与 win32 分支），只在 ``subprocess.Popen`` 处设 spy 捕获
   真正要定位的路径。工程师用例是直接替换掉 ``_reveal_path_in_file_manager``，
   那一段真实逻辑未被覆盖——这里补上。
5. **真实文件落盘**：候选文件真实写在 tmp 目录、内容带各自 m3u8 标记；断言「打开
   位置」选中的文件**内容**与该行显示的 m3u8 一致——从数据层面证明一一对应，
   而不是比对两个字符串变量。
6. **生产调用链**：通过 GUI 真实的 ``_finalize_inflight_download_record`` 入账，
   证明生产路径（而非仅公开 API）确实写入条目级 ``output_path``。

全程 headless（无真实 Tk 窗口，绝不弹出窗口）；``page_history`` / 预载队列均重定
向到 tmp，并用 autouse 守卫确保用户真实 ``~/.m3u8-downloader/page_history.json``
不被改动。

运行：``py -3.13 -m pytest tests/_qa_page_history_align_indep.py -q``
（文件名以 ``_qa_`` 开头，不进主套件，属一次性独立验证资产。）
"""

from __future__ import annotations

import hashlib
import json
import os
import random
import subprocess
from pathlib import Path

import pytest
import tkinter as tk
from tkinter import messagebox, ttk

from m3u8_downloader import gui as gui_mod
from m3u8_downloader import page_history
from m3u8_downloader.gui import M3U8DownloaderGUI

REAL_HISTORY_FILE = os.path.join(
    os.path.expanduser("~"), ".m3u8-downloader", "page_history.json"
)


# ===========================================================================
# 安全守卫：绝不污染用户真实记录文件
# ===========================================================================

def _fingerprint(path: str):
    """文件内容指纹（不存在时返回 None）."""
    try:
        with open(path, "rb") as f:
            return hashlib.sha256(f.read()).hexdigest()
    except OSError:
        return None


@pytest.fixture(autouse=True)
def guard_real_history_file():
    """每个用例前后比对用户真实 page_history.json 的哈希，变动即失败。"""
    before = _fingerprint(REAL_HISTORY_FILE)
    yield
    after = _fingerprint(REAL_HISTORY_FILE)
    assert before == after, "测试污染了用户真实的 page_history.json！"


# ===========================================================================
# 可控时钟 + 隔离的持久化文件
# ===========================================================================

@pytest.fixture
def history_file(tmp_path, monkeypatch):
    """把 page_history 的落盘目标重定向到 tmp（不影响真实用户文件）。"""
    target = tmp_path / "page_history.json"
    monkeypatch.setattr(page_history, "PAGE_HISTORY_FILE", str(target))
    return target


@pytest.fixture
def clock(monkeypatch):
    """严格递增的可控时钟（每次调用 +1 秒），消除真实时钟抖动。"""
    state = {"n": 0}

    def fake_now() -> str:
        state["n"] += 1
        s = state["n"]
        return "2026-01-01 %02d:%02d:%02d" % (s // 3600, (s % 3600) // 60, s % 60)

    monkeypatch.setattr(page_history, "_now", fake_now)
    return state


@pytest.fixture
def frozen_clock(monkeypatch):
    """冻结时钟：可手工设定当前时间，用于构造「同一秒并列」。"""
    box = {"value": "2026-01-01 00:00:07"}
    monkeypatch.setattr(page_history, "_now", lambda: box["value"])
    return box


def _history_path() -> Path:
    """当前（已重定向的）落盘文件路径，转 Path 便于直接写原始 JSON。"""
    return Path(page_history.PAGE_HISTORY_FILE)


def write_raw(path, records: list) -> None:
    """直接向落盘文件写入原始 JSON（模拟旧版本/脏数据/手工篡改）。"""
    path.write_text(json.dumps({"records": records}), encoding="utf-8")


def read_raw(path) -> list:
    """读取落盘文件的原始记录（不经 page_history，验证真实落盘内容）。"""
    return json.loads(path.read_text(encoding="utf-8"))["records"]


# ===========================================================================
# Headless 录制型 Tk 假件（自建，不复用工程师的 MagicMock 假件）
# ===========================================================================

class StubWidget:
    """任何属性/方法都安全 no-op 的控件占位."""

    def __init__(self, *args, **kwargs):
        self.kwargs = kwargs

    def __getattr__(self, name):
        return lambda *a, **kw: None


class RecordingTree(StubWidget):
    """记录真实 insert 行数据的 Treeview 假件."""

    instances: list = []

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.rows = {}          # iid -> values 元组
        self.order = []         # 插入顺序
        self.binds = {}
        self._selection = ()
        RecordingTree.instances.append(self)

    def insert(self, parent, index, iid=None, **kwargs):
        self.rows[iid] = tuple(kwargs.get("values", ()))
        self.order.append(iid)
        return iid

    def selection(self):
        return tuple(self._selection)

    def select(self, iid):
        self._selection = (iid,)

    def bind(self, sequence, callback=None):
        self.binds[sequence] = callback


class RecordingButton(StubWidget):
    """记录构造参数（text/command/state）并支持 configure 改状态的按钮假件."""

    instances: list = []

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.text = kwargs.get("text", "")
        self.command = kwargs.get("command")
        self.state = kwargs.get("state")
        RecordingButton.instances.append(self)

    def configure(self, **kwargs):
        if "state" in kwargs:
            self.state = kwargs["state"]


class Var:
    """真实的 tk 变量语义（get/set 存真值，不是 MagicMock）."""

    def __init__(self, master=None, value=None, **kwargs):
        self._value = value

    def set(self, value):
        self._value = value

    def get(self):
        return self._value


class StringVar(Var):
    def __init__(self, master=None, value="", **kwargs):
        super().__init__(master, value if value is not None else "")


class IntVar(Var):
    def __init__(self, master=None, value=0, **kwargs):
        super().__init__(master, value if value is not None else 0)


class BooleanVar(Var):
    def __init__(self, master=None, value=False, **kwargs):
        super().__init__(master, value if value is not None else False)


class DoubleVar(Var):
    def __init__(self, master=None, value=0.0, **kwargs):
        super().__init__(master, value if value is not None else 0.0)


class RootStub:
    """Tk 根窗口占位：无窗口、无 mainloop。"""

    def __init__(self):
        self.tk = StubWidget()
        self.after_calls = []

    def after(self, *args, **kwargs):
        self.after_calls.append(args)
        return ""

    def clipboard_get(self):
        return ""

    def __getattr__(self, name):
        return lambda *a, **kw: None


@pytest.fixture
def headless_tk(monkeypatch, tmp_path):
    """安装 headless 假件（不弹任何真实窗口）。"""
    RecordingTree.instances.clear()
    RecordingButton.instances.clear()
    monkeypatch.setattr(tk, "StringVar", StringVar)
    monkeypatch.setattr(tk, "IntVar", IntVar)
    monkeypatch.setattr(tk, "BooleanVar", BooleanVar)
    monkeypatch.setattr(tk, "DoubleVar", DoubleVar)
    monkeypatch.setattr(tk, "Text", StubWidget)
    monkeypatch.setattr(tk, "Toplevel", StubWidget)
    for name in ("Frame", "Label", "Entry", "Spinbox", "Checkbutton",
                 "Progressbar", "LabelFrame", "Scrollbar", "Combobox",
                 "Notebook", "Separator", "PanedWindow", "Style"):
        if hasattr(ttk, name):
            monkeypatch.setattr(ttk, name, StubWidget)
    monkeypatch.setattr(ttk, "Treeview", RecordingTree)
    monkeypatch.setattr(ttk, "Button", RecordingButton)
    monkeypatch.setattr(messagebox, "showinfo", lambda *a, **kw: None)
    monkeypatch.setattr(messagebox, "showerror", lambda *a, **kw: None)
    monkeypatch.setattr(messagebox, "askyesno", lambda *a, **kw: True)
    monkeypatch.setattr(messagebox, "askokcancel", lambda *a, **kw: True)
    # 预载队列同样隔离，避免读写用户真实文件
    monkeypatch.setattr(gui_mod, "PRELOAD_QUEUE_FILE", tmp_path / "preload_queue.json")


@pytest.fixture
def popen_spy(monkeypatch):
    """捕获真正的 subprocess.Popen 调用参数（不真的拉起资源管理器）。"""
    calls = []

    def fake_popen(args, *a, **kw):
        calls.append(list(args))
        return StubWidget()

    monkeypatch.setattr(subprocess, "Popen", fake_popen)
    return calls


@pytest.fixture
def panel_gui(headless_tk, history_file):
    """headless GUI 实例（页面历史与预载队列均已重定向到 tmp）。"""
    return M3U8DownloaderGUI(RootStub())


def open_row(gui, iid, popen_calls) -> str:
    """选中某行并真的点「打开位置」，返回实际交给 explorer 的文件路径。"""
    popen_calls.clear()
    tree = RecordingTree.instances[-1]
    tree.select(iid)
    # 真实触发选中事件（按钮可用性同步逻辑）
    callback = tree.binds.get("<<TreeviewSelect>>")
    if callback:
        callback()
    buttons = [b for b in RecordingButton.instances if b.text == "打开位置"]
    assert buttons, "面板没有渲染出「打开位置」按钮"
    buttons[-1].command()  # 真实执行 _open_location → _reveal_path_in_file_manager
    if not popen_calls:
        return ""
    args = popen_calls[-1]
    assert args[0] == "explorer" and args[1] == "/select,"
    return args[2]


def render_panel(gui):
    """渲染一次记录面板，返回 (tree, 按钮字典)。"""
    gui._show_page_history()
    tree = RecordingTree.instances[-1]
    return tree, {b.text: b for b in RecordingButton.instances}


def make_file(tmp_path, name: str, marker: str) -> str:
    """在 tmp 下创建真实文件，内容为该 m3u8 的标记（用于内容级对应性断言）。"""
    path = tmp_path / name
    path.write_text(marker, encoding="utf-8")
    return str(path)


# ===========================================================================
# 1. 端到端落盘往返（原始磁盘结构断言）
# ===========================================================================

class TestDiskRoundTrip:

    def test_a_b_then_a_round_trip_on_disk(self, history_file, clock):
        """A→B→再 A：落盘结构为 [B, A] 且 A 条目带第二次的文件路径。"""
        page_history.record_page_downloaded(
            "https://x/page", "https://cdn/x/a.m3u8", output_path="D:/out/a.mp4"
        )
        page_history.record_page_downloaded(
            "https://x/page", "https://cdn/x/b.m3u8", output_path="D:/out/b.mp4"
        )
        page_history.record_page_downloaded(
            "https://x/page", "https://cdn/x/a.m3u8", output_path="D:/out/a-2.mp4"
        )

        # —— 原始磁盘：不经 page_history，直接读文件 ——
        raw = read_raw(history_file)[0]
        assert [d["m3u8_url"] for d in raw["downloads"]] == [
            "https://cdn/x/b.m3u8", "https://cdn/x/a.m3u8",
        ]
        by_url = {d["m3u8_url"]: d for d in raw["downloads"]}
        assert by_url["https://cdn/x/a.m3u8"]["output_path"] == "D:/out/a-2.mp4"
        assert by_url["https://cdn/x/b.m3u8"]["output_path"] == "D:/out/b.mp4"
        # 条目时间戳必须严格递增（旧→新）
        stamps = [d["timestamp"] for d in raw["downloads"]]
        assert stamps == sorted(stamps) and len(set(stamps)) == 2
        # 冗余兼容字段仍包含全部历史（观察 D 不被破坏）
        assert set(raw["m3u8_url"].split("\n")) == {
            "https://cdn/x/a.m3u8", "https://cdn/x/b.m3u8",
        }

        # —— 重新从磁盘 load ——
        record = page_history.list_records()[0]
        latest = page_history.latest_download(record)
        assert latest["m3u8_url"] == "https://cdn/x/a.m3u8"
        assert latest["output_path"] == "D:/out/a-2.mp4"
        assert page_history.latest_m3u8_url(record) == latest["m3u8_url"]
        assert record["output_path"] == "D:/out/a-2.mp4"  # 记录级兜底同步

    def test_entry_path_survives_fresh_process_like_reload(self, history_file, clock):
        """模拟「关掉程序再打开」：只靠磁盘内容重建内存，条目路径仍在。"""
        page_history.record_page_downloaded(
            "https://x/page", "https://cdn/x/a.m3u8", output_path="D:/out/a.mp4"
        )
        page_history.record_page_downloaded(
            "https://x/page", "https://cdn/x/b.m3u8", output_path="D:/out/b.mp4"
        )
        # 只依赖磁盘重建（list_records 每次都重新 _load）
        reloaded = page_history.list_records()[0]
        paths = {d["m3u8_url"]: d["output_path"] for d in reloaded["downloads"]}
        assert paths == {
            "https://cdn/x/a.m3u8": "D:/out/a.mp4",
            "https://cdn/x/b.m3u8": "D:/out/b.mp4",
        }

    def test_real_user_file_never_touched(self, history_file, clock):
        """写入发生在 tmp 目标上，用户真实文件路径未被当作落盘目标。"""
        page_history.record_page_downloaded(
            "https://x/page", "https://cdn/x/a.m3u8", output_path="D:/out/a.mp4"
        )
        assert page_history.PAGE_HISTORY_FILE == str(history_file)
        assert os.path.abspath(page_history.PAGE_HISTORY_FILE) != os.path.abspath(
            REAL_HISTORY_FILE
        )
        assert history_file.exists()


# ===========================================================================
# 2. 排序与「同一秒并列」
# ===========================================================================

class TestOrderingAndTieBreak:

    def test_same_second_redownload_takes_the_later_listed_entry(
        self, history_file, frozen_clock
    ):
        """同一秒完成 A→B→再 A：靠后者（被移到末尾的 A）胜出，符合旧→新。"""
        frozen_clock["value"] = "2026-01-01 00:00:07"  # 三次都在同一秒
        page_history.record_page_downloaded(
            "https://x/page", "https://cdn/x/a.m3u8", output_path="D:/out/a.mp4"
        )
        page_history.record_page_downloaded(
            "https://x/page", "https://cdn/x/b.m3u8", output_path="D:/out/b.mp4"
        )
        page_history.record_page_downloaded(
            "https://x/page", "https://cdn/x/a.m3u8", output_path="D:/out/a-2.mp4"
        )
        record = page_history.list_records()[0]
        assert [d["m3u8_url"] for d in record["downloads"]] == [
            "https://cdn/x/b.m3u8", "https://cdn/x/a.m3u8",
        ]
        latest = page_history.latest_download(record)
        assert latest["m3u8_url"] == "https://cdn/x/a.m3u8"
        assert latest["output_path"] == "D:/out/a-2.mp4"

    def test_same_second_distinct_downloads_pick_last_one(
        self, history_file, frozen_clock
    ):
        """同一秒连下两个不同 m3u8：最近一次应为后下载的那个（B）。"""
        page_history.record_page_downloaded(
            "https://x/page", "https://cdn/x/a.m3u8", output_path="D:/out/a.mp4"
        )
        page_history.record_page_downloaded(
            "https://x/page", "https://cdn/x/b.m3u8", output_path="D:/out/b.mp4"
        )
        record = page_history.list_records()[0]
        assert page_history.latest_download(record)["m3u8_url"] == (
            "https://cdn/x/b.m3u8"
        )

    def test_later_timestamp_beats_list_position(self, history_file):
        """磁盘上「新时间戳在前、旧在后」的乱序数据：按时间戳判定，不看位置。"""
        write_raw(history_file, [{
            "page_url": "https://x/page",
            "status": "downloaded",
            "timestamp": "2026-01-05 10:00:00",
            "output_path": "D:/out/b.mp4",
            "downloads": [
                {"m3u8_url": "https://cdn/x/a.m3u8",
                 "timestamp": "2026-01-09 10:00:00",
                 "output_path": "D:/out/a-2.mp4"},
                {"m3u8_url": "https://cdn/x/b.m3u8",
                 "timestamp": "2026-01-02 10:00:00",
                 "output_path": "D:/out/b.mp4"},
            ],
        }])
        record = page_history.list_records()[0]
        latest = page_history.latest_download(record)
        # 末位是 b，但 a 的时间戳更新 → 必须是 a（这条同时证明修好了旧遗留数据）
        assert latest["m3u8_url"] == "https://cdn/x/a.m3u8"
        assert latest["output_path"] == "D:/out/a-2.mp4"
        assert page_history.latest_m3u8_url(record) == "https://cdn/x/a.m3u8"

    def test_all_blank_timestamps_pick_last_entry(self):
        """全部时间戳为空：不比较失败、不崩，取列表最后一个。"""
        record = {"downloads": [
            {"m3u8_url": "https://cdn/x/a.m3u8", "timestamp": ""},
            {"m3u8_url": "https://cdn/x/b.m3u8", "timestamp": None},
        ]}
        assert page_history.latest_download(record)["m3u8_url"] == (
            "https://cdn/x/b.m3u8"
        )

    def test_blank_timestamp_never_beats_a_real_one(self):
        """空时间戳不会盖掉有时间戳的条目（字符串比较的边界）。"""
        record = {"downloads": [
            {"m3u8_url": "https://cdn/x/old.m3u8", "timestamp": "2026-01-01 00:00:01"},
            {"m3u8_url": "https://cdn/x/nodate.m3u8", "timestamp": ""},
        ]}
        assert page_history.latest_download(record)["m3u8_url"] == (
            "https://cdn/x/old.m3u8"
        )

    def test_fuzz_downloads_keep_old_to_new_and_latest_matches(
        self, history_file, clock
    ):
        """随机序列 fuzz：downloads 恒为「最后下载时间旧→新」，latest 恒为最后一条。

        独立 oracle 在测试内重算期望值，不复用被测代码任何判定逻辑。
        """
        rng = random.Random(20260910)
        pool = ["https://cdn/x/a.m3u8", "https://cdn/x/b.m3u8", "https://cdn/x/c.m3u8"]
        path_seq = ["D:/out/p1.mp4", "D:/out/p2.mp4", "D:/out/p3.mp4", "D:/out/p4.mp4"]

        for _ in range(40):
            history_file.write_text("{}", encoding="utf-8")  # 每轮从空盘开始
            seq = [
                (rng.choice(pool), rng.choice(path_seq + [""]))
                for _ in range(rng.randint(2, 10))
            ]
            last_touch, last_path = {}, {}
            for i, (m3u8, path) in enumerate(seq):
                page_history.record_page_downloaded(
                    "https://x/page", m3u8, output_path=path
                )
                last_touch[m3u8] = i
                if path:
                    last_path[m3u8] = path
                elif m3u8 not in last_path:
                    last_path[m3u8] = ""

            expected_order = sorted(last_touch, key=lambda m: last_touch[m])
            expected_latest = expected_order[-1]
            expected_path = last_path[expected_latest]

            record = page_history.list_records()[0]
            assert [d["m3u8_url"] for d in record["downloads"]] == expected_order
            stamps = [d["timestamp"] for d in record["downloads"]]
            assert stamps == sorted(stamps)  # 旧→新不变量
            latest = page_history.latest_download(record)
            assert latest["m3u8_url"] == expected_latest
            assert latest["output_path"] == expected_path
            assert page_history.latest_m3u8_url(record) == expected_latest


# ===========================================================================
# 3. 旧格式 / 脏数据 / 边界
# ===========================================================================

class TestLegacyAndDirtyData:

    def test_legacy_record_with_only_m3u8_string(self, history_file, clock):
        """最旧格式（只有 m3u8_url 字符串、无 downloads）：迁移不崩、路径降级空串。"""
        write_raw(history_file, [{
            "page_url": "https://x/old",
            "status": "downloaded",
            "m3u8_url": "https://cdn/x/legacy.m3u8",
            "timestamp": "2026-01-01 00:00:00",
            "output_path": "D:/out/legacy.mp4",
        }])
        record = page_history.list_records()[0]
        assert len(record["downloads"]) == 1
        assert record["downloads"][0]["m3u8_url"] == "https://cdn/x/legacy.m3u8"
        assert record["downloads"][0]["output_path"] == ""   # 旧条目无路径 → 降级
        assert page_history.latest_m3u8_url(record) == "https://cdn/x/legacy.m3u8"
        latest = page_history.latest_download(record)
        assert latest["m3u8_url"] == "https://cdn/x/legacy.m3u8"
        assert latest["output_path"] == ""   # 展示层回退记录级路径
        # 旧记录继续下载：追加而非覆盖，且新条目带自己的路径
        page_history.record_page_downloaded(
            "https://x/old", "https://cdn/x/new.m3u8", output_path="D:/out/new.mp4"
        )
        after = page_history.list_records()[0]
        assert [d["m3u8_url"] for d in after["downloads"]] == [
            "https://cdn/x/legacy.m3u8", "https://cdn/x/new.m3u8",
        ]
        assert page_history.latest_download(after)["output_path"] == "D:/out/new.mp4"

    def test_dirty_downloads_entries_are_ignored(self, history_file):
        """downloads 混入非 dict / 空 m3u8 / 缺字段：不崩，脏项被丢弃。"""
        write_raw(history_file, [{
            "page_url": "https://x/page",
            "status": "downloaded",
            "timestamp": "2026-01-03 00:00:00",
            "output_path": "D:/out/keep.mp4",
            "downloads": [
                "i-am-a-string",
                42,
                None,
                {},
                {"m3u8_url": "   ", "timestamp": "2026-01-09 00:00:00"},
                {"timestamp": "2026-01-09 00:00:00"},
                {"m3u8_url": "https://cdn/x/good.m3u8",
                 "timestamp": "2026-01-02 00:00:00",
                 "output_path": "D:/out/good.mp4"},
                {"m3u8_url": "https://cdn/x/good.m3u8",  # 重复 → 去重
                 "timestamp": "2026-01-08 00:00:00"},
            ],
        }])
        record = page_history.list_records()[0]
        assert len(record["downloads"]) == 1
        latest = page_history.latest_download(record)
        assert latest["m3u8_url"] == "https://cdn/x/good.m3u8"
        assert latest["output_path"] == "D:/out/good.mp4"

    def test_downloads_field_not_a_list(self):
        """downloads 是字符串/None/数字时不崩：latest_download 返回 {}。"""
        for bad in ("oops", None, 123, {}, True):
            assert page_history.latest_download({"downloads": bad}) == {}
            assert page_history.latest_m3u8_url({"downloads": bad}) == ""

    def test_downloads_not_a_list_falls_back_to_legacy_field(self, history_file):
        """downloads 非列表时 _normalize 回退旧 m3u8_url 字段，不丢数据。"""
        write_raw(history_file, [{
            "page_url": "https://x/page",
            "status": "downloaded",
            "timestamp": "2026-01-01 00:00:00",
            "downloads": "corrupted",
            "m3u8_url": "https://cdn/x/fallback.m3u8",
        }])
        record = page_history.list_records()[0]
        assert [d["m3u8_url"] for d in record["downloads"]] == [
            "https://cdn/x/fallback.m3u8"
        ]
        assert page_history.latest_m3u8_url(record) == "https://cdn/x/fallback.m3u8"

    def test_blank_output_path_does_not_wipe_entry_path(self, history_file, clock):
        """重下时 output_path 为空 → 条目已有路径保留，不被抹成空。"""
        page_history.record_page_downloaded(
            "https://x/page", "https://cdn/x/a.m3u8", output_path="D:/out/a.mp4"
        )
        page_history.record_page_downloaded("https://x/page", "https://cdn/x/a.m3u8")
        record = page_history.list_records()[0]
        assert len(record["downloads"]) == 1
        assert record["downloads"][0]["output_path"] == "D:/out/a.mp4"
        assert page_history.latest_download(record)["output_path"] == "D:/out/a.mp4"
        # 之后再带路径重下 → 正常更新
        page_history.record_page_downloaded(
            "https://x/page", "https://cdn/x/a.m3u8", output_path="D:/out/a-2.mp4"
        )
        assert page_history.latest_download(
            page_history.list_records()[0]
        )["output_path"] == "D:/out/a-2.mp4"

    def test_blank_output_path_first_time_leaves_empty(self, history_file, clock):
        """首次下载就没拿到路径：条目与记录级均为空串，不产生假路径。"""
        page_history.record_page_downloaded(
            "https://x/page", "https://cdn/x/a.m3u8", output_path=""
        )
        raw = read_raw(history_file)[0]
        assert raw["downloads"][0]["output_path"] == ""
        assert raw["output_path"] == ""

    def test_latest_download_returns_a_copy(self, history_file, clock):
        """返回副本：调用方乱改返回值不污染原记录（含磁盘）。"""
        page_history.record_page_downloaded(
            "https://x/page", "https://cdn/x/a.m3u8", output_path="D:/out/a.mp4"
        )
        record = page_history.list_records()[0]
        latest = page_history.latest_download(record)
        latest["m3u8_url"] = "HACKED"
        latest["output_path"] = "HACKED"
        latest["new_key"] = "HACKED"
        # 内存记录未污染
        assert page_history.latest_download(record)["m3u8_url"] == (
            "https://cdn/x/a.m3u8"
        )
        # 磁盘未污染
        assert read_raw(history_file)[0]["downloads"][0]["output_path"] == "D:/out/a.mp4"
        # 重新 load 也未污染
        assert page_history.latest_download(
            page_history.list_records()[0]
        )["output_path"] == "D:/out/a.mp4"

    def test_latest_download_invalid_arguments(self):
        """非法入参安全返回 {}（不抛异常）。"""
        for bad in (None, "", "x", 0, [], (), object()):
            assert page_history.latest_download(bad) == {}
        assert page_history.latest_download({}) == {}
        assert page_history.latest_download({"downloads": []}) == {}
        assert page_history.latest_download({"downloads": [None, 1, "x"]}) == {}


# ===========================================================================
# 4. GUI 行内对应性（真按钮 + 真 reveal + Popen spy + 真实文件）
# ===========================================================================

class TestGuiRowCorrespondence:

    def test_row_m3u8_matches_the_file_explorer_would_select(
        self, panel_gui, popen_spy, tmp_path, clock
    ):
        """A→B→再 A：行显示 A，explorer 选中的文件内容必须就是 A 第二次的文件。"""
        file_a1 = make_file(tmp_path, "a1.mp4", "FILE:A:1")
        file_b = make_file(tmp_path, "b.mp4", "FILE:B:1")
        file_a2 = make_file(tmp_path, "a2.mp4", "FILE:A:2")
        page_history.record_page_downloaded(
            "https://x/page", "https://cdn/x/a.m3u8", output_path=file_a1
        )
        page_history.record_page_downloaded(
            "https://x/page", "https://cdn/x/b.m3u8", output_path=file_b
        )
        page_history.record_page_downloaded(
            "https://x/page", "https://cdn/x/a.m3u8", output_path=file_a2
        )

        tree, _ = render_panel(panel_gui)
        row = tree.rows["r0"]
        displayed_m3u8 = row[4]
        assert displayed_m3u8 == "https://cdn/x/a.m3u8"

        opened = open_row(panel_gui, "r0", popen_spy)
        assert os.path.exists(opened), "「打开位置」定位的文件必须真实存在"
        # 内容级对应：打开的文件正是显示的那条 m3u8 下载出来的文件
        with open(opened, encoding="utf-8") as f:
            assert f.read() == "FILE:A:2"
        assert opened != file_b  # 绝不能是 B 的文件（旧 bug 的错位表现）

    def test_every_row_opens_its_own_latest_file(self, panel_gui, popen_spy, tmp_path, clock):
        """多行：逐行选中，打开的文件内容都与本行显示的 m3u8 一致。"""
        page1_a = make_file(tmp_path, "p1a.mp4", "P1:A")
        page1_b = make_file(tmp_path, "p1b.mp4", "P1:B")
        page2_c = make_file(tmp_path, "p2c.mp4", "P2:C")
        page_history.record_page_downloaded(
            "https://x/p1", "https://cdn/1/a.m3u8", output_path=page1_a
        )
        page_history.record_page_downloaded(
            "https://x/p2", "https://cdn/2/c.m3u8", output_path=page2_c
        )
        page_history.record_page_downloaded(
            "https://x/p1", "https://cdn/1/b.m3u8", output_path=page1_b
        )

        tree, _ = render_panel(panel_gui)
        assert len(tree.rows) == 2
        # 最近活动的页排在最前：p1（刚下载 b）
        assert tree.rows["r0"][3] == "https://x/p1"
        assert tree.rows["r1"][3] == "https://x/p2"

        for iid, expected_marker in (("r0", "P1:B"), ("r1", "P2:C")):
            displayed = tree.rows[iid][4]
            opened = open_row(panel_gui, iid, popen_spy)
            with open(opened, encoding="utf-8") as f:
                content = f.read()
            assert content == expected_marker, (
                f"行 {iid} 显示 {displayed}，却打开了 {content} 的文件"
            )

    def test_legacy_entry_falls_back_to_record_path(self, panel_gui, popen_spy, tmp_path):
        """旧条目无条目级路径 → 回退记录级路径；回退结果仍与本行 m3u8 对应。"""
        legacy_file = make_file(tmp_path, "legacy.mp4", "LEGACY:A")
        write_raw(_history_path(), [{
            "page_url": "https://x/old",
            "status": "downloaded",
            "timestamp": "2026-01-01 00:00:00",
            "output_path": legacy_file,
            "downloads": [
                {"m3u8_url": "https://cdn/x/a.m3u8", "timestamp": "2026-01-01 00:00:00"},
            ],
        }])
        tree, _ = render_panel(panel_gui)
        assert tree.rows["r0"][4] == "https://cdn/x/a.m3u8"
        opened = open_row(panel_gui, "r0", popen_spy)
        with open(opened, encoding="utf-8") as f:
            assert f.read() == "LEGACY:A"

    def test_open_button_disabled_without_path(self, panel_gui, popen_spy):
        """该行没有可定位路径时：按钮禁用且真的不会拉起 explorer。"""
        page_history.record_page_downloaded(
            "https://x/page", "https://cdn/x/a.m3u8", output_path=""
        )
        tree, buttons = render_panel(panel_gui)
        assert buttons["打开位置"].state == tk.DISABLED
        opened = open_row(panel_gui, "r0", popen_spy)
        assert opened == ""
        assert popen_spy == []  # 没有发生任何外部进程调用

    def test_open_button_enabled_when_path_known(self, panel_gui, popen_spy, tmp_path):
        """有路径时按钮可用（选中事件驱动）。"""
        target = make_file(tmp_path, "ok.mp4", "OK")
        page_history.record_page_downloaded(
            "https://x/page", "https://cdn/x/a.m3u8", output_path=target
        )
        tree, buttons = render_panel(panel_gui)
        tree.select("r0")
        tree.binds["<<TreeviewSelect>>"]()
        assert buttons["打开位置"].state == tk.NORMAL

    def test_residual_case_empty_path_falls_back_to_previous_file(
        self, panel_gui, popen_spy, tmp_path, clock
    ):
        """残留场景（供评估）：最近一次下载没拿到路径时，打开的是上一次的文件。

        构造：A(有路径) → B(无路径)。B 条目路径为空 → 回退记录级（仍是 A 的文件），
        于是「显示 B、打开 A 的文件」。此处断言**实际行为**不崩、可定位，并记录该
        固有限制（生产路径 output_path 必非空，触发概率极低）。
        """
        file_a = make_file(tmp_path, "only_a.mp4", "ONLY:A")
        page_history.record_page_downloaded(
            "https://x/page", "https://cdn/x/a.m3u8", output_path=file_a
        )
        page_history.record_page_downloaded("https://x/page", "https://cdn/x/b.m3u8")
        tree, _ = render_panel(panel_gui)
        assert tree.rows["r0"][4] == "https://cdn/x/b.m3u8"  # 显示 B（最新一次）
        opened = open_row(panel_gui, "r0", popen_spy)
        with open(opened, encoding="utf-8") as f:
            assert f.read() == "ONLY:A"  # 打开的仍是 A 的文件（兜底）


# ===========================================================================
# 5. 生产调用链：GUI 真实的下载收尾入账
# ===========================================================================

class TestProductionWiring:

    def test_finalize_inflight_download_writes_entry_level_path(
        self, panel_gui, history_file, clock
    ):
        """走 GUI 真实收尾方法（不是直接调 API）：条目级路径确实落盘。"""
        def finish(page_url, m3u8, path):
            panel_gui._inflight_download_page_url = page_url
            panel_gui._inflight_download_url = m3u8
            panel_gui._inflight_download_output_path = path
            panel_gui._finalize_inflight_download_record("success")

        finish("https://x/page", "https://cdn/x/a.m3u8", "D:/out/a.mp4")
        finish("https://x/page", "https://cdn/x/b.m3u8", "D:/out/b.mp4")
        finish("https://x/page", "https://cdn/x/a.m3u8", "D:/out/a-2.mp4")

        raw = read_raw(history_file)[0]
        by_url = {d["m3u8_url"]: d for d in raw["downloads"]}
        assert by_url["https://cdn/x/a.m3u8"]["output_path"] == "D:/out/a-2.mp4"
        assert by_url["https://cdn/x/b.m3u8"]["output_path"] == "D:/out/b.mp4"
        record = page_history.list_records()[0]
        latest = page_history.latest_download(record)
        assert latest["m3u8_url"] == "https://cdn/x/a.m3u8"
        assert latest["output_path"] == "D:/out/a-2.mp4"

    def test_finalize_then_panel_row_is_consistent(
        self, panel_gui, popen_spy, tmp_path, clock
    ):
        """生产入账后开面板：行 m3u8 与 explorer 选中文件一致。"""
        file_a1 = make_file(tmp_path, "w_a1.mp4", "W:A:1")
        file_b = make_file(tmp_path, "w_b.mp4", "W:B")
        file_a2 = make_file(tmp_path, "w_a2.mp4", "W:A:2")

        def finish(m3u8, path):
            panel_gui._inflight_download_page_url = "https://x/page"
            panel_gui._inflight_download_url = m3u8
            panel_gui._inflight_download_output_path = path
            panel_gui._finalize_inflight_download_record("success")

        finish("https://cdn/x/a.m3u8", file_a1)
        finish("https://cdn/x/b.m3u8", file_b)
        finish("https://cdn/x/a.m3u8", file_a2)

        tree, _ = render_panel(panel_gui)
        assert tree.rows["r0"][4] == "https://cdn/x/a.m3u8"
        opened = open_row(panel_gui, "r0", popen_spy)
        with open(opened, encoding="utf-8") as f:
            assert f.read() == "W:A:2"
