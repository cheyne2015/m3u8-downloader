"""Tkinter GUI 界面模块：提供图形化下载操作界面."""

import json
import os
import queue
import subprocess
import sys
import threading
import time
import tkinter as tk
from dataclasses import dataclass, field, replace
from enum import Enum
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from typing import Optional

from m3u8_downloader import __version__
from m3u8_downloader.downloader import M3U8Downloader
from m3u8_downloader.extractor import Candidate, is_deep_mode_available
from m3u8_downloader.utils import (
    build_output_path,
    extract_title_segment,
    format_duration,
    format_file_size,
    format_speed,
    is_ffmpeg_available,
    normalize_mp4_filename,
    sanitize_filename_component,
)

# GUI 偏好配置文件路径：存放"记住保存位置"等界面偏好
GUI_CONFIG_PATH: Path = Path(os.path.expanduser("~/.m3u8-downloader/gui_config.json"))
# 待处理预载队列持久化文件：与下载历史分开存储（无上限，关工具保留可续连播）
PRELOAD_QUEUE_FILE: Path = Path(
    os.path.expanduser("~/.m3u8-downloader/preload_queue.json")
)


@dataclass(frozen=True)
class DownloadJob:
    url: str
    output_path: str
    title: str
    source_page_url: str = ""


@dataclass(frozen=True)
class PageTitleUpdate:
    page_url: str
    title: str


class PreloadState(str, Enum):
    SUCCESS = "success"
    STOPPED = "stopped"
    ERROR = "error"


@dataclass(frozen=True)
class PreloadResult:
    candidates: list
    filename_title: str
    page_title: str
    page_url: str
    state: PreloadState = PreloadState.SUCCESS


@dataclass
class PreloadQueueEntry:
    """待处理预载队列中的一个页面条目.

    下载中每点击一次「提取网页」即入队（state="extracting"）；提取完成后
    更新为 success / stopped / error 并固化候选、标题等结果，供自动连播时
    逐个展示与下载。条目被处理（轮到下载）时从队列移除。

    Attributes:
        page_url: 网页 URL（入队键）.
        state: "extracting" | "success" | "stopped" | "error".
        candidates: 该页提取出的候选列表（流式累积，完成时用最终结果覆盖）.
        filename_title: 预载期间流式暂存的标题段（文件名用）.
        page_title: 网页完整标题.
        m3u8_url: 该页实际下载的 m3u8 直链（下载后回填，供记录/回看）.
        manual: True = 用户手动点「下载选中」加入队列尾（手动优先：轮到时不设
            自动下载门控，直接下载）；False = 下载中预载自动入队（走自动规则）.
        manual_urls: 手动入队时用户实际选中的 m3u8 链接（轮到该页时精确保留选择）.
    """

    page_url: str
    state: str = "extracting"
    candidates: list = field(default_factory=list)
    filename_title: str = ""
    page_title: str = ""
    m3u8_url: str = ""
    manual: bool = False
    manual_urls: list = field(default_factory=list)

    def to_dict(self) -> dict:
        """转成可 JSON 持久化的字典（候选字段均为标量）."""
        return {
            "page_url": self.page_url,
            "state": self.state,
            "filename_title": self.filename_title,
            "page_title": self.page_title,
            "m3u8_url": self.m3u8_url,
            "manual": bool(self.manual),
            "manual_urls": [str(u) for u in self.manual_urls],
            "candidates": [
                {
                    "url": c.url,
                    "title": c.title,
                    "source": c.source,
                    "is_master": bool(c.is_master),
                    "estimated_size": int(getattr(c, "estimated_size", 0) or 0),
                    "duration": float(getattr(c, "duration", 0.0) or 0.0),
                    "bandwidth": int(getattr(c, "bandwidth", 0) or 0),
                    "segment_count": int(getattr(c, "segment_count", 0) or 0),
                    "estimate_method": str(getattr(c, "estimate_method", "unknown")),
                    "estimate_error": str(getattr(c, "estimate_error", "")),
                    "reachable": bool(getattr(c, "reachable", True)),
                }
                for c in self.candidates
            ],
        }

    @classmethod
    def from_dict(cls, data: dict) -> "PreloadQueueEntry":
        """从持久化字典恢复条目（损坏字段安全降级）."""
        candidates: list = []
        try:
            for c in data.get("candidates", []) or []:
                if not isinstance(c, dict) or not str(c.get("url", "")).strip():
                    continue
                candidates.append(Candidate(**{
                    "url": str(c.get("url", "")),
                    "title": str(c.get("title", "") or ""),
                    "source": str(c.get("source", "html")),
                    "is_master": bool(c.get("is_master", False)),
                    "estimated_size": int(c.get("estimated_size", 0) or 0),
                    "duration": float(c.get("duration", 0.0) or 0.0),
                    "bandwidth": int(c.get("bandwidth", 0) or 0),
                    "segment_count": int(c.get("segment_count", 0) or 0),
                    "estimate_method": str(c.get("estimate_method", "unknown")),
                    "estimate_error": str(c.get("estimate_error", "") or ""),
                    "reachable": bool(c.get("reachable", True)),
                }))
        except (TypeError, ValueError):
            candidates = []
        return cls(
            page_url=str(data.get("page_url", "")),
            state=str(data.get("state", "extracting")),
            candidates=candidates,
            filename_title=str(data.get("filename_title", "") or ""),
            page_title=str(data.get("page_title", "") or ""),
            m3u8_url=str(data.get("m3u8_url", "") or ""),
            manual=bool(data.get("manual", False)),
            manual_urls=[
                str(u) for u in (data.get("manual_urls") or [])
            ],
        )


class M3U8DownloaderGUI:
    """m3u8 下载器 GUI 主窗口.

    使用 Tkinter 构建图形界面，复用 M3U8Downloader 核心下载逻辑。
    下载在子线程中执行，通过队列和 after() 方法更新 UI。
    """

    def __init__(self, root: tk.Tk) -> None:
        """初始化 GUI 窗口.

        Args:
            root: Tkinter 根窗口.
        """
        self._root = root
        self._root.title("m3u8 下载工具")
        self._root.geometry("980x900")
        # 最小尺寸与默认几何保持一致：保证所有内容（尤其是「设置」区 4 列 grid ≈ 960px 宽）
        # 完整显示，避免窗口缩小时 Spinbox 看似飘到右端（其实是 reqwidth>minsize 被压缩）。
        self._root.minsize(980, 700)

        # 下载状态变量
        self._downloading: bool = False
        self._stop_flag: threading.Event = threading.Event()
        self._download_thread: Optional[threading.Thread] = None
        self._active_downloader: Optional[M3U8Downloader] = None
        self._message_queue: queue.Queue = queue.Queue()

        # 网页抽取 / 多选下载状态
        self._candidates: list = []
        self._pending_jobs: list = []
        self._extracting: bool = False
        self._extract_stop_flag = threading.Event()
        self._candidate_items: dict = {}
        self._page_title: str = ""
        self._candidate_page_url: str = ""
        self._current_source_page_url: str = ""
        # 只保留最近一次完成的预载，链接与标题作为一个对象交接。
        self._pending_extract: list = []
        # 预载期间流式暂存的候选（下载中暂存，下载结束后逐条流式显示）。
        self._pending_preload_candidates: list = []
        # 候选列表是否已为当前提取清空（预载 B 在下载结束后需先清空 A 再显示 B）。
        self._preload_list_cleared: bool = True
        # 预载期间暂存的标题段（下载中暂存，下载结束后立即填充文件名）。
        self._pending_preload_title: str = ""

        # ===== 自动选中 / 自动下载（会话级状态，不持久化） =====
        # 本次会话（打开工具后）用户是否已手动下载过至少一次：「自动下载」的前置条件。
        self._session_manual_downloaded: bool = False
        # 本次会话已手动触发下载的链接；「手动优先」——自动下载不重复处理这些链接。
        self._manual_downloaded_urls: set = set()
        # 预载场景：下载中完成的提取结果（"success" / "stopped" / "error"；空串表示无）。
        # 结果暂存于此，等当前下载结束后再判定是否自动选中 / 自动下载。
        self._pending_extract_result: str = ""

        # ===== 多页连续预载队列（下载中反复预载的待处理页） =====
        # 条目 = 已入队的预载页（含 extracting/success/stopped/error 状态与结果暂存）。
        # 队列持久化到 PRELOAD_QUEUE_FILE，关工具保留，下次打开可续连播。
        self._preload_queue: list = self._load_preload_queue()
        # 正在执行的提取对应的网页 URL（用于把结果记入网页下载记录）。
        self._current_extract_page_url: str = ""
        # 本次提取是否已记入网页下载记录（避免一次提取重复入账）。
        self._extract_recorded: bool = False
        # 「待处理 N 个」按钮的已同步状态缓存（避免重复 configure 空转）。
        self._queue_indicator_count: int = 0
        self._queue_indicator_state: str = tk.DISABLED
        # 记录当前下载任务关联的网页（page_url -> 下载 m3u8 url）；用于功能二记录。
        self._inflight_download_page_url: str = ""
        self._inflight_download_url: str = ""
        # 本次下载是否来自待处理队列头（用于「队列页下载失败→跳过继续」语义）。
        self._active_download_is_queue_page: bool = False
        # P2：用户点「停止下载」后置位，抑制自动连播链（含 in-flight 预载完成后
        # 的自动下载）；用户再次手动点「下载选中 / 开始下载」时清除（会话级，不持久化）。
        self._auto_chain_stopped: bool = False

        # 构建 UI
        self._build_ui()

        # 启动消息轮询
        self._poll_queue()

    def _build_ui(self) -> None:
        """构建所有 UI 组件."""
        # 主容器，支持缩放
        main_frame = ttk.Frame(self._root, padding=10)
        main_frame.pack(fill=tk.BOTH, expand=True)

        # 配置网格列权重，使组件可随窗口缩放
        main_frame.columnconfigure(1, weight=1)

        row = 0

        # ===== URL 输入区 =====
        url_label = ttk.Label(main_frame, text="地址（m3u8 / 网页）：")
        url_label.grid(row=row, column=0, sticky=tk.W, pady=(0, 5))

        url_frame = ttk.Frame(main_frame)
        url_frame.grid(row=row, column=1, columnspan=2, sticky=tk.EW, pady=(0, 5))
        url_frame.columnconfigure(0, weight=1)

        self._url_var = tk.StringVar()
        self._url_entry = ttk.Entry(url_frame, textvariable=self._url_var)
        self._url_entry.grid(row=0, column=0, sticky=tk.EW, padx=(0, 5))

        paste_btn = ttk.Button(url_frame, text="粘贴", command=self._paste_url, width=6)
        paste_btn.grid(row=0, column=1)

        self._extract_btn = ttk.Button(
            url_frame, text="提取网页", command=self._start_extract, width=10
        )
        self._extract_btn.grid(row=0, column=2, padx=(5, 0))
        self._stop_extract_btn = ttk.Button(
            url_frame, text="停止提取", command=self._stop_extract,
            width=10, state=tk.DISABLED,
        )
        self._stop_extract_btn.grid(row=0, column=3, padx=(5, 0))

        row += 1

        # ===== 输出设置区 =====
        # 保存目录
        dir_label = ttk.Label(main_frame, text="保存目录：")
        dir_label.grid(row=row, column=0, sticky=tk.W, pady=(0, 5))

        dir_frame = ttk.Frame(main_frame)
        dir_frame.grid(row=row, column=1, columnspan=2, sticky=tk.EW, pady=(0, 5))
        dir_frame.columnconfigure(0, weight=1)

        self._dir_var = tk.StringVar(value=os.path.abspath("."))
        self._dir_entry = ttk.Entry(dir_frame, textvariable=self._dir_var)
        self._dir_entry.grid(row=0, column=0, sticky=tk.EW, padx=(0, 5))

        dir_browse_btn = ttk.Button(dir_frame, text="浏览", command=self._browse_dir, width=6)
        dir_browse_btn.grid(row=0, column=1)

        # 在系统文件管理器中打开当前保存目录
        dir_open_btn = ttk.Button(dir_frame, text="打开", command=self._open_dir, width=6)
        dir_open_btn.grid(row=0, column=2, padx=(5, 0))

        row += 1

        # 文件名
        name_label = ttk.Label(main_frame, text="文件名称：")
        name_label.grid(row=row, column=0, sticky=tk.W, pady=(0, 5))

        self._filename_var = tk.StringVar(value="output.mp4")
        self._filename_entry = ttk.Entry(main_frame, textvariable=self._filename_var)
        self._filename_entry.grid(row=row, column=1, columnspan=2, sticky=tk.EW, pady=(0, 5))

        row += 1

        # 记住保存位置（下次启动自动填充）
        self._remember_dir_var = tk.BooleanVar(value=False)
        remember_dir_check = ttk.Checkbutton(
            main_frame,
            text="记住保存位置（下次启动自动填充）",
            variable=self._remember_dir_var,
            command=self._save_config,
        )
        remember_dir_check.grid(row=row, column=1, columnspan=2, sticky=tk.W, pady=(0, 5))

        row += 1

        # ===== 设置区（参数 + 行为合并） =====
        # 每行「label + 控件」用独立水平 Frame 紧贴排布，再放入 3 列 grid（col0=左栏 / col1=间隔 / col2=右栏）。
        # 关键：各行不再共享同一套 grid 列宽，避免短 label 的控件被同列的超长勾选框/输入框挤到右侧
        #（旧版把「深度模式（需 playwright）」与「代理地址」和短 label 挤在同一列，导致 Spinbox 漂到右端）。
        param_frame = ttk.LabelFrame(main_frame, text="设置", padding=8)
        param_frame.grid(row=row, column=0, columnspan=3, sticky=tk.EW, pady=(0, 10))
        param_frame.columnconfigure(0, weight=1)
        param_frame.columnconfigure(1, minsize=30)
        param_frame.columnconfigure(2, weight=1)

        # 并发线程数（左栏 row0）
        self._workers_var = tk.IntVar(value=8)
        _row0l = ttk.Frame(param_frame)
        workers_spin = ttk.Spinbox(_row0l, from_=1, to=64, textvariable=self._workers_var, width=8)
        ttk.Label(_row0l, text="并发线程数：").pack(side=tk.LEFT)
        workers_spin.pack(side=tk.LEFT, padx=(6, 0))
        _row0l.grid(row=0, column=0, sticky=tk.W)

        # 重试次数（右栏 row0）
        self._retries_var = tk.IntVar(value=3)
        _row0r = ttk.Frame(param_frame)
        retries_spin = ttk.Spinbox(_row0r, from_=0, to=100, textvariable=self._retries_var, width=8)
        ttk.Label(_row0r, text="重试次数：").pack(side=tk.LEFT)
        retries_spin.pack(side=tk.LEFT, padx=(6, 0))
        _row0r.grid(row=0, column=2, sticky=tk.W)

        # 超时时间(秒)（左栏 row1）
        self._timeout_var = tk.IntVar(value=30)
        _row1l = ttk.Frame(param_frame)
        timeout_spin = ttk.Spinbox(_row1l, from_=5, to=300, textvariable=self._timeout_var, width=8)
        ttk.Label(_row1l, text="超时时间(秒)：").pack(side=tk.LEFT)
        timeout_spin.pack(side=tk.LEFT, padx=(6, 0))
        _row1l.grid(row=1, column=0, sticky=tk.W, pady=(5, 0))

        # 使用 ffmpeg 合并转码（右栏 row1）
        self._use_ffmpeg_var = tk.BooleanVar(value=True)
        _row1r = ttk.Frame(param_frame)
        ffmpeg_check = ttk.Checkbutton(_row1r, text="使用 ffmpeg 合并转码", variable=self._use_ffmpeg_var)
        ffmpeg_check.pack(side=tk.LEFT)
        _row1r.grid(row=1, column=2, sticky=tk.W, pady=(5, 0))

        # 临时目录（整行 row2，跨三列，entry 撑宽）
        self._tmpdir_var = tk.StringVar(value="")
        _tmp_row = ttk.Frame(param_frame)
        ttk.Label(_tmp_row, text="临时目录：").pack(side=tk.LEFT)
        _tmp_inner = ttk.Frame(_tmp_row)
        self._tmpdir_entry = ttk.Entry(_tmp_inner, textvariable=self._tmpdir_var)
        self._tmpdir_entry.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 5))
        tmp_browse_btn = ttk.Button(_tmp_inner, text="浏览", command=self._browse_tmpdir, width=6)
        tmp_browse_btn.pack(side=tk.LEFT)
        _tmp_inner.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(6, 0))
        _tmp_row.grid(row=2, column=0, columnspan=3, sticky=tk.EW, pady=(5, 0))

        # 深度模式（需 playwright）（左栏 row3）
        self._deep_var = tk.BooleanVar(value=False)
        _row3l = ttk.Frame(param_frame)
        deep_check = ttk.Checkbutton(_row3l, text="深度模式（需 playwright）", variable=self._deep_var)
        deep_check.pack(side=tk.LEFT)
        _row3l.grid(row=3, column=0, sticky=tk.W, pady=(5, 0))
        if not is_deep_mode_available():
            deep_check.configure(state=tk.DISABLED)

        # 自动下载（右栏 row3）
        self._auto_download_var = tk.BooleanVar(value=True)
        _row3r = ttk.Frame(param_frame)
        auto_download_check = ttk.Checkbutton(_row3r, text="自动下载", variable=self._auto_download_var, command=self._save_config)
        auto_download_check.pack(side=tk.LEFT)
        _row3r.grid(row=3, column=2, sticky=tk.W, pady=(5, 0))

        # 使用代理（左栏 row4）
        self._use_proxy_var = tk.BooleanVar(value=False)
        _row4l = ttk.Frame(param_frame)
        use_proxy_check = ttk.Checkbutton(_row4l, text="使用代理", variable=self._use_proxy_var)
        use_proxy_check.pack(side=tk.LEFT)
        _row4l.grid(row=4, column=0, sticky=tk.W, pady=(5, 0))

        # 连续下载（右栏 row4）
        self._continuous_download_var = tk.BooleanVar(value=False)
        _row4r = ttk.Frame(param_frame)
        continuous_download_check = ttk.Checkbutton(_row4r, text="连续下载", variable=self._continuous_download_var, command=self._save_config)
        continuous_download_check.pack(side=tk.LEFT)
        _row4r.grid(row=4, column=2, sticky=tk.W, pady=(5, 0))

        # 代理地址（左栏 row5）
        self._proxy_var = tk.StringVar(value="127.0.0.1:7897")
        _row5l = ttk.Frame(param_frame)
        ttk.Label(_row5l, text="代理地址：").pack(side=tk.LEFT)
        proxy_entry = ttk.Entry(_row5l, textvariable=self._proxy_var, width=24)
        proxy_entry.pack(side=tk.LEFT, padx=(6, 0))
        _row5l.grid(row=5, column=0, sticky=tk.W, pady=(5, 0))

        # 多文件时创建文件夹（右栏 row5）
        self._create_folder_var = tk.BooleanVar(value=False)
        _row5r = ttk.Frame(param_frame)
        create_folder_check = ttk.Checkbutton(_row5r, text="多文件时创建文件夹", variable=self._create_folder_var)
        create_folder_check.pack(side=tk.LEFT)
        _row5r.grid(row=5, column=2, sticky=tk.W, pady=(5, 0))

        row += 1


        # ===== 网页提取结果区 =====
        extract_frame = ttk.LabelFrame(main_frame, text="网页提取结果", padding=8)
        extract_frame.grid(row=row, column=0, columnspan=3, sticky=tk.NSEW, pady=(0, 10))
        extract_frame.columnconfigure(0, weight=1)
        extract_frame.rowconfigure(0, weight=1)
        # 与日志行共享垂直剩余空间（权重 1：3，日志优先填满）
        main_frame.rowconfigure(row, weight=1)

        self._tree = ttk.Treeview(
            extract_frame,
            columns=("no", "size", "duration", "bandwidth", "type", "mode", "title", "url"),
            show="headings",
            selectmode="extended",
            height=8,
        )
        self._tree.heading("no", text="#")
        self._tree.heading("size", text="估计大小")
        self._tree.heading("duration", text="时长")
        self._tree.heading("bandwidth", text="码率")
        self._tree.heading("type", text="类型")
        self._tree.heading("mode", text="模式")
        self._tree.heading("title", text="标题")
        self._tree.heading("url", text="链接")
        self._tree.column("no", width=40, anchor=tk.CENTER)
        self._tree.column("size", width=110)
        self._tree.column("duration", width=90)
        self._tree.column("bandwidth", width=100)
        self._tree.column("type", width=70)
        self._tree.column("mode", width=70, anchor=tk.CENTER)
        self._tree.column("title", width=120)
        self._tree.column("url", width=300, stretch=True)

        tree_scroll = ttk.Scrollbar(
            extract_frame, orient=tk.VERTICAL, command=self._tree.yview
        )
        self._tree.configure(yscrollcommand=tree_scroll.set)
        self._tree.grid(row=0, column=0, sticky=tk.NSEW)
        tree_scroll.grid(row=0, column=1, sticky=tk.NS)
        # 单击切换多选：点一下选中、再点一下取消，且不影响其他已选行（无需 Ctrl/Shift）
        self._tree.bind("<Button-1>", self._on_tree_single_click)
        self._tree.bind("<Double-1>", self._on_tree_double_click)
        self._tree.bind("<<TreeviewSelect>>", self._on_tree_selection_changed)
        self._tree_click_after_id = None  # 区分单击/双击的延迟定时器

        result_bar = ttk.Frame(extract_frame)
        result_bar.grid(row=1, column=0, columnspan=2, sticky=tk.EW, pady=(5, 0))
        result_bar.columnconfigure(5, weight=1)

        self._download_selected_btn = ttk.Button(
            result_bar,
            text="下载选中",
            command=self._download_selected,
            state=tk.DISABLED,
            width=12,
        )
        self._download_selected_btn.grid(row=0, column=0, padx=(0, 5))
        self._select_all_btn = ttk.Button(
            result_bar, text="全选", command=self._select_all_candidates,
            state=tk.DISABLED, width=7,
        )
        self._select_all_btn.grid(row=0, column=1, padx=(0, 5))
        self._clear_selection_btn = ttk.Button(
            result_bar, text="取消选择", command=self._clear_candidate_selection,
            state=tk.DISABLED, width=9,
        )
        self._clear_selection_btn.grid(row=0, column=2, padx=(0, 5))
        self._copy_links_btn = ttk.Button(
            result_bar, text="复制链接", command=self._copy_selected_links,
            state=tk.DISABLED, width=9,
        )
        self._copy_links_btn.grid(row=0, column=3, padx=(0, 10))
        self._selection_summary_var = tk.StringVar(value="共 0 条，已选 0 条")
        ttk.Label(result_bar, textvariable=self._selection_summary_var).grid(
            row=0, column=4, sticky=tk.W,
        )

        # 待处理预载队列计数按钮：点击弹出各待处理页 URL 列表
        self._queue_count_var = tk.StringVar(value="待处理 0 个")
        self._queue_count_btn = ttk.Button(
            result_bar,
            textvariable=self._queue_count_var,
            command=self._show_preload_queue_popup,
            width=12,
            state=tk.DISABLED,
        )
        self._queue_count_btn.grid(row=0, column=5, padx=(10, 5))

        # 下载记录按钮：弹出独立只读的网页下载记录面板
        self._page_history_btn = ttk.Button(
            result_bar, text="下载记录", command=self._show_page_history, width=9
        )
        self._page_history_btn.grid(row=0, column=6)

        self._preload_status_var = tk.StringVar(value="预载：未开始")
        ttk.Label(extract_frame, textvariable=self._preload_status_var).grid(
            row=2, column=0, columnspan=2, sticky=tk.W, pady=(5, 0),
        )

        row += 1

        # ===== 操作按钮区 =====
        btn_frame = ttk.Frame(main_frame)
        btn_frame.grid(row=row, column=0, columnspan=3, pady=(0, 10))

        self._start_btn = ttk.Button(
            btn_frame, text="开始下载", command=self._start_download, width=15
        )
        self._start_btn.pack(side=tk.LEFT, padx=(0, 10))

        self._stop_btn = ttk.Button(
            btn_frame, text="停止下载", command=self._stop_download, width=15, state=tk.DISABLED
        )
        self._stop_btn.pack(side=tk.LEFT)

        row += 1

        # ===== 进度显示区 =====
        progress_frame = ttk.LabelFrame(main_frame, text="下载进度", padding=8)
        progress_frame.grid(row=row, column=0, columnspan=3, sticky=tk.EW, pady=(0, 10))
        progress_frame.columnconfigure(0, weight=1)

        # 总进度条
        self._progress_var = tk.DoubleVar(value=0)
        self._progress_bar = ttk.Progressbar(
            progress_frame, variable=self._progress_var, maximum=100, mode="determinate"
        )
        self._progress_bar.grid(row=0, column=0, sticky=tk.EW, pady=(0, 5))

        # 状态文本
        self._status_var = tk.StringVar(value="就绪")
        status_label = ttk.Label(progress_frame, textvariable=self._status_var, anchor=tk.W)
        status_label.grid(row=1, column=0, sticky=tk.EW)

        self._current_title_var = tk.StringVar(value="当前标题：—")
        self._current_output_var = tk.StringVar(value="保存文件：—")
        ttk.Label(progress_frame, textvariable=self._current_title_var, anchor=tk.W).grid(
            row=2, column=0, sticky=tk.EW, pady=(5, 0),
        )
        ttk.Label(progress_frame, textvariable=self._current_output_var, anchor=tk.W).grid(
            row=3, column=0, sticky=tk.EW,
        )

        row += 1

        # ===== 日志显示区 =====
        log_frame = ttk.LabelFrame(main_frame, text="日志", padding=8)
        log_frame.grid(row=row, column=0, columnspan=3, sticky=tk.NSEW, pady=(0, 0))
        log_frame.columnconfigure(0, weight=1)
        log_frame.rowconfigure(0, weight=1)

        # 日志行优先占满剩余空间（权重 3：1 vs extract 行）
        main_frame.rowconfigure(row, weight=3)

        self._log_text = tk.Text(
            log_frame, height=24, wrap=tk.WORD, state=tk.DISABLED, font=("Consolas", 9)
        )
        log_scrollbar = ttk.Scrollbar(log_frame, orient=tk.VERTICAL, command=self._log_text.yview)
        self._log_text.configure(yscrollcommand=log_scrollbar.set)

        self._log_text.grid(row=0, column=0, sticky=tk.NSEW)
        log_scrollbar.grid(row=0, column=1, sticky=tk.NS)

        # UI 构建完成、变量均已创建后，恢复上次的配置（保存目录 + 参数设置）
        self._load_config()

        # 窗口关闭前保存配置
        self._root.protocol("WM_DELETE_WINDOW", self._on_close)

        # 深度模式依赖缺失时给出安装提示（此时日志区已就绪，可安全 _log）
        if not is_deep_mode_available():
            self._log(
                "提示：未安装 playwright，深度模式不可用；"
                "安装：pip install playwright && playwright install chromium"
            )

        # 队列计数按钮就绪后，同步当前（含启动时从持久化恢复的）队列长度
        self._update_queue_indicator()

    # ===== UI 回调方法 =====

    def _paste_url(self) -> None:
        """从剪贴板粘贴 URL 到输入框."""
        try:
            clipboard_text = self._root.clipboard_get()
            self._url_var.set(clipboard_text.strip())
        except tk.TclError:
            pass  # 剪贴板为空或不可访问

    def _browse_dir(self) -> None:
        """浏览选择保存目录."""
        selected = filedialog.askdirectory(initialdir=self._dir_var.get())
        if selected:
            self._dir_var.set(selected)
            # 已勾选"记住保存位置"时，同步持久化新选择的目录
            if self._remember_dir_var.get():
                self._save_config()

    def _open_dir(self) -> None:
        """在系统文件管理器中打开当前保存目录.

        目录为空或不存在时给出日志提示；打开失败时记录异常但不影响 GUI 运行。
        """
        path = self._dir_var.get().strip()
        if not path or not os.path.isdir(path):
            self._log("提示：保存目录为空或不存在，无法打开")
            return

        try:
            if sys.platform == "win32":
                os.startfile(path)  # type: ignore[attr-defined]  # Windows 专有接口
            elif sys.platform == "darwin":
                subprocess.run(["open", path], check=False)
            else:
                subprocess.run(["xdg-open", path], check=False)
        except Exception as e:
            self._log(f"打开目录失败：{e}")

    def _load_config(self) -> None:
        """启动时读取所有 GUI 配置（保存目录 + 参数设置）.

        配置文件为 GUI_CONFIG_PATH（~/.m3u8-downloader/gui_config.json）。
        文件不存在、JSON 损坏或结构异常时安全降级为默认值，绝不让 GUI 启动失败。
        """
        try:
            with open(GUI_CONFIG_PATH, "r", encoding="utf-8") as f:
                config = json.load(f)
            if not isinstance(config, dict):
                raise ValueError("配置内容不是 JSON 对象")
        except Exception:
            return  # 配置不存在/损坏：全部用默认值

        # 保存目录（仅当 remember_dir 为 True 且 last_dir 仍存在时恢复）
        remember_dir = bool(config.get("remember_dir", False))
        last_dir = str(config.get("last_dir", "") or "").strip()
        if remember_dir and last_dir and os.path.isdir(last_dir):
            self._dir_var.set(last_dir)
            self._remember_dir_var.set(True)
        else:
            self._remember_dir_var.set(False)

        # 参数设置
        self._deep_var.set(bool(config.get("deep", False)))
        self._use_proxy_var.set(bool(config.get("use_proxy", False)))
        self._proxy_var.set(str(config.get("proxy", "") or "127.0.0.1:7897"))
        self._use_ffmpeg_var.set(bool(config.get("use_ffmpeg", True)))
        self._tmpdir_var.set(str(config.get("tmpdir", "") or ""))
        self._create_folder_var.set(bool(config.get("create_folder", False)))
        self._auto_download_var.set(bool(config.get("auto_download", True)))
        self._continuous_download_var.set(bool(config.get("continuous_download", False)))

        for key, var, default in (
            ("workers", self._workers_var, 8),
            ("retries", self._retries_var, 3),
            ("timeout", self._timeout_var, 30),
        ):
            try:
                var.set(int(config.get(key, default)))
            except (TypeError, ValueError):
                pass

    def _save_config(self) -> None:
        """将当前所有 GUI 配置写入配置文件.

        在「记住位置」勾选变化、浏览选目录、以及窗口关闭时调用，
        保证下次启动恢复相同的配置。写入失败仅在日志区提示。
        """
        config = {
            "remember_dir": bool(self._remember_dir_var.get()),
            "last_dir": self._dir_var.get().strip() if self._remember_dir_var.get() else "",
            "deep": bool(self._deep_var.get()),
            "use_proxy": bool(self._use_proxy_var.get()),
            "proxy": self._proxy_var.get().strip(),
            "workers": int(self._workers_var.get()),
            "retries": int(self._retries_var.get()),
            "timeout": int(self._timeout_var.get()),
            "use_ffmpeg": bool(self._use_ffmpeg_var.get()),
            "tmpdir": self._tmpdir_var.get().strip(),
            "create_folder": bool(self._create_folder_var.get()),
            "auto_download": bool(self._auto_download_var.get()),
            "continuous_download": bool(self._continuous_download_var.get()),
        }
        try:
            GUI_CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
            with open(GUI_CONFIG_PATH, "w", encoding="utf-8") as f:
                json.dump(config, f, ensure_ascii=False, indent=2)
        except Exception as e:
            self._log(f"提示：配置写入失败：{e}")

    def _on_close(self) -> None:
        """窗口关闭前保存配置，再销毁窗口."""
        self._save_config()
        self._root.destroy()

    def _browse_tmpdir(self) -> None:
        """浏览选择临时目录."""
        initial = self._tmpdir_var.get() or self._dir_var.get()
        selected = filedialog.askdirectory(initialdir=initial)
        if selected:
            self._tmpdir_var.set(selected)

    def _auto_rename_path(self, output_path: str) -> str:
        """自动改名：``foo.mp4`` → ``foo-1.mp4`` → ``foo-2.mp4``，直到不冲突."""
        base, ext = os.path.splitext(output_path)
        for i in range(1, 1000):
            candidate = f"{base}-{i}{ext}"
            if not os.path.exists(candidate):
                return candidate
        # 极端情况：-1..-999 全被占，退回带时间戳的名字兜底
        import time as _time
        return f"{base}-{int(_time.time())}{ext}"

    def _resolve_output_path_collision(self, output_path: str) -> "Optional[str]":
        """同名文件覆盖提醒：返回最终保存路径（可能自动改名），取消时返回 None.

        仅在 UI 线程调用（弹出 messagebox 阻塞询问）。
        """
        if not os.path.exists(output_path):
            return output_path
        result = messagebox.askyesnocancel(
            "文件已存在",
            f"保存路径已存在同名文件：\n{output_path}\n\n"
            "「是」= 覆盖\n「否」= 自动改名（追加 -1、-2 后缀）\n「取消」= 放弃本次下载",
        )
        if result is None:  # 取消
            return None
        if result:  # 是 = 覆盖
            return output_path
        # 否 = 自动改名
        return self._auto_rename_path(output_path)

    def _confirm_duplicate(self, url: str) -> bool:
        """重复链接提醒：URL 已下载过则弹窗询问是否仍要下载，返回是否继续.

        仅在 UI 线程调用（弹 messagebox 阻塞询问）。
        """
        from m3u8_downloader.history import is_downloaded

        if not is_downloaded(url):
            return True
        return bool(messagebox.askyesno(
            "重复链接",
            f"该链接已下载过：\n{url}\n\n是否仍要下载？\n「是」= 继续下载\n「否」= 跳过",
        ))

    def _start_download(self) -> None:
        """点击开始下载按钮的回调."""
        # P2：手动触发下载 → 解除「停止后抑制自动连播」（无论是否真正开始）。
        self._auto_chain_stopped = False
        # 参数校验
        url = self._url_var.get().strip()
        if not url:
            self._log("错误：请输入 m3u8 地址")
            return
        if not url.startswith(("http://", "https://")):
            self._log("错误：URL 必须以 http:// 或 https:// 开头")
            return

        filename = self._filename_var.get().strip()
        if not filename:
            self._log("错误：请输入文件名称")
            return

        save_dir = self._dir_var.get().strip()
        if not save_dir:
            self._log("错误：请选择保存目录")
            return

        # 重复链接提醒（已下载过则询问是否仍要下载）
        if not self._confirm_duplicate(url):
            self._log("已跳过重复链接")
            return

        output_path = os.path.join(save_dir, filename)
        # 规范化：保证最终保存文件后缀为 .mp4 且仅有一个 .mp4
        output_path = normalize_mp4_filename(output_path)
        # 把规范化后的名称回填到输入框，让用户清楚实际会保存成什么文件
        self._filename_var.set(os.path.basename(output_path))
        self._log(f"保存文件: {output_path}")

        # 同名文件覆盖提醒（覆盖 / 自动改名 / 取消）
        output_path = self._resolve_output_path_collision(output_path)
        if output_path is None:
            self._log("已取消下载")
            return
        if os.path.basename(output_path) != self._filename_var.get():
            # 自动改名后，回填新文件名让用户看到最终保存名
            self._filename_var.set(os.path.basename(output_path))
            self._log(f"已自动改名，保存文件: {output_path}")

        workers = self._workers_var.get()
        retries = self._retries_var.get()
        timeout = self._timeout_var.get()
        use_ffmpeg = self._use_ffmpeg_var.get()
        tmp_dir = self._tmpdir_var.get().strip()

        # 切换按钮状态
        self._downloading = True
        self._stop_flag = threading.Event()
        download_stop_event = self._stop_flag
        self._start_btn.configure(state=tk.DISABLED)
        self._stop_btn.configure(state=tk.NORMAL)

        # 重置进度
        self._progress_var.set(0)
        self._status_var.set("正在下载...")
        candidate_urls = {candidate.url for candidate in self._candidates}
        display_title = self._page_title if url in candidate_urls and self._page_title else ""
        self._current_source_page_url = self._candidate_page_url if url in candidate_urls else ""
        self._set_current_download_info(output_path, display_title)
        self._log("下载进行中可预载下一网页，链接和标题将在当前下载结束后一起回填")

        # 功能二：记住本次下载对应的网页（仅当下载的是从网页提取出的 m3u8）。
        self._inflight_download_url = url
        self._inflight_download_page_url = self._current_source_page_url

        # 启动下载线程
        self._download_thread = threading.Thread(
            target=self._download_worker,
            args=(url, output_path, workers, retries, timeout, use_ffmpeg, tmp_dir,
                  download_stop_event),
            daemon=True,
        )
        self._download_thread.start()

        # 会话级标记：本次会话已手动下载过（「自动下载」的前置条件，不持久化）。
        # 同时记录链接，「手动优先」——自动下载不再重复处理用户已手动触发的链接。
        self._session_manual_downloaded = True
        self._manual_downloaded_urls.add(url)
        # 手动直链下载若正对应队列头：视为该页「轮到下载」，从待处理队列移除。
        self._active_download_is_queue_page = self._drop_queue_head_if_page(
            self._current_source_page_url
        )

    def _stop_download(self) -> None:
        """停止下载，不影响独立进行的网页扫描。"""
        if self._downloading:
            self._stop_flag.set()
            if self._active_downloader is not None:
                self._active_downloader.cancel()
            self._pending_jobs.clear()
            self._log("正在停止下载...")
            self._status_var.set("正在停止下载...")

    def _stop_extract(self) -> None:
        """停止扫描，保留已有候选，不设置下载停止信号。"""
        if self._extracting:
            self._extract_stop_flag.set()
            self._stop_extract_btn.configure(state=tk.DISABLED)
            if self._downloading:
                self._preload_status_var.set("预载：正在停止，已有结果将保留")
            self._log("正在停止提取，已找到的结果将保留")

    def _set_current_download_info(self, output_path: str, title: str = "") -> None:
        """在固定状态区显示当前任务，避免与预载网页混淆。"""
        filename = os.path.basename(output_path)
        display_title = title or os.path.splitext(filename)[0] or "—"
        self._current_title_var.set(f"当前标题：{display_title}")
        self._current_output_var.set(f"保存文件：{filename or '—'}")

    def _clear_current_download_info(self) -> None:
        self._current_title_var.set("当前标题：—")
        self._current_output_var.set("保存文件：—")
        self._current_source_page_url = ""

    def _apply_page_title(self, page_url: str, title: str) -> None:
        """记录完整网页标题，并补写从早到候选启动的下载任务。"""
        if not title or page_url != self._candidate_page_url:
            return
        self._page_title = title
        self._pending_jobs = [
            replace(job, title=title) if job.source_page_url == page_url else job
            for job in self._pending_jobs
        ]
        if self._downloading and self._current_source_page_url == page_url:
            self._current_title_var.set(f"当前标题：{title}")

    def _resolve_proxy(self) -> "tuple[str, bool]":
        """根据 UI 解析代理配置.

        默认直连（未勾选「使用代理」）：返回 ``("", True)`` —— 不使用任何代理，
        且跳过系统代理环境变量。勾选「使用代理」后返回用户输入的代理地址。
        """
        if not self._use_proxy_var.get():
            return "", True
        return self._proxy_var.get().strip(), False

    def _download_worker(
        self,
        url: str,
        output_path: str,
        workers: int,
        retries: int,
        timeout: int,
        use_ffmpeg: bool,
        tmp_dir: str,
        stop_event: Optional[threading.Event] = None,
        proxy: str = "",
    ) -> None:
        """下载工作线程函数.

        在子线程中执行下载逻辑，通过队列发送消息更新 UI。

        Args:
            url: m3u8 地址.
            output_path: 输出文件路径.
            workers: 并发线程数.
            retries: 重试次数.
            timeout: 超时时间.
            use_ffmpeg: 是否使用 ffmpeg.
            tmp_dir: 临时目录.
            stop_event: 本次下载独占的停止信号.
            proxy: 手动代理地址（如 ``127.0.0.1:7897``）；为空则不使用.
        """
        download_stop_event = stop_event or self._stop_flag
        downloader = None
        try:
            # 检查 ffmpeg
            if use_ffmpeg and not is_ffmpeg_available():
                self._queue_message("log", "未检测到 ffmpeg，将使用 TS 二进制拼接方式")
                use_ffmpeg = False

            # 创建下载器实例（默认直连；勾选「使用代理」才走代理）
            proxy, no_proxy = self._resolve_proxy()
            downloader = M3U8Downloader(
                url=url,
                output=output_path,
                workers=workers,
                tmp_dir=tmp_dir,
                use_ffmpeg=use_ffmpeg,
                max_retries=retries,
                timeout=timeout,
                no_proxy=no_proxy,
                proxy=proxy,
                stop_event=download_stop_event,
                progress_callback=lambda data: self._queue_message("progress", data),
                log_callback=lambda message: self._queue_message("log", message),
            )
            self._active_downloader = downloader

            downloader.download()
            from m3u8_downloader.history import record_download
            record_download(url)
            self._queue_message("done", "success")

        except RuntimeError as e:
            if download_stop_event.is_set():
                self._queue_message("log", "下载已停止")
                self._queue_message("done", "stopped")
            else:
                self._queue_message("log", f"错误：{e}")
                self._queue_message("done", "error")
        except Exception as e:
            if download_stop_event.is_set():
                self._queue_message("log", "下载已停止")
                self._queue_message("done", "stopped")
            else:
                self._queue_message("log", f"未知错误：{e}")
                self._queue_message("done", "error")
        finally:
            if self._active_downloader is downloader:
                self._active_downloader = None

    # ===== 消息队列与 UI 更新 =====

    def _queue_message(self, msg_type: str, data: object = None) -> None:
        """向消息队列发送消息.

        Args:
            msg_type: 消息类型（"log", "progress", "done"）.
            data: 消息数据.
        """
        self._message_queue.put((msg_type, data))

    def _poll_queue(self) -> None:
        """轮询消息队列，处理 UI 更新."""
        try:
            while True:
                msg_type, data = self._message_queue.get_nowait()
                self._handle_message(msg_type, data)
        except queue.Empty:
            pass
        # 继续轮询（每 100ms）
        self._root.after(100, self._poll_queue)

    def _handle_message(self, msg_type: str, data: object) -> None:
        """处理队列消息，更新 UI.

        Args:
            msg_type: 消息类型.
            data: 消息数据.
        """
        if msg_type == "log":
            self._log(str(data) if data is not None else "")
        elif msg_type == "progress":
            self._update_progress(data)
        elif msg_type == "done":
            self._on_download_done(str(data))
        elif msg_type == "candidates":
            self._fill_tree(data if isinstance(data, list) else [])
        elif msg_type == "candidate_update":
            if self._downloading and self._extracting:
                # 下载中预载：候选暂存到「正在提取的队列条目」，不显示。
                # 若该提取不是入队预载（如空闲提取中途开始下载），退化为旧暂存缓冲。
                entry = self._current_preload_entry()
                if entry is not None:
                    entry.candidates.append(data)
                else:
                    self._pending_preload_candidates.append(data)
            elif self._downloading:
                # 下载中但提取标志已复位：兜底暂存，下载结束后流式显示。
                self._pending_preload_candidates.append(data)
            else:
                # 下载结束后（或空闲正常提取）：先清空上一页候选（切换），再逐条显示。
                if not self._preload_list_cleared:
                    self._clear_tree()
                    self._preload_list_cleared = True
                self._upsert_candidate(data)
                entry = self._current_preload_entry()
                if entry is not None:
                    # 预载页在下载结束后才继续流式出候选：同步累积进条目结果。
                    entry.candidates.append(data)
        elif msg_type == "preloaded_extract":
            preload_result = data
            # 先把结果固化到对应的待处理队列条目（若存在），再走旧的单页交接逻辑。
            self._finalize_preload_entry(preload_result)
            # 结果和标题在主线程一起交接，避免下载完成与工作线程暂存结果竞态。
            if self._downloading:
                self._pending_extract[:] = [preload_result]
                if preload_result.state == PreloadState.SUCCESS:
                    self._preload_status_var.set(
                        f"预载：成功，找到 {len(preload_result.candidates)} 条，等待当前下载结束"
                    )
                else:
                    state_text = (
                        "预载：已停止，保留已找到结果"
                        if preload_result.state == PreloadState.STOPPED
                        else "预载：失败，保留已找到结果"
                    )
                    self._preload_status_var.set(state_text)
                self._log("网页预载完成，链接和标题等待当前下载结束后一起回填")
                self._on_extract_done(
                    "pending" if preload_result.state == PreloadState.SUCCESS else preload_result.state
                )
            else:
                # 下载先结束、预载后完成时：候选已通过 candidate_update 流式显示，
                # 这里只回填标题与状态，不再一次性 _fill_tree。
                self._pending_extract.clear()
                self._candidate_page_url = preload_result.page_url
                self._apply_page_title(preload_result.page_url, preload_result.page_title)
                if preload_result.filename_title:
                    self._suggest_filename(preload_result.filename_title)
                self._preload_status_var.set(
                    f"预载：已载入 {len(preload_result.candidates)} 条结果"
                )
                # P2：若用户刚点过「停止下载」，本次「下载结束后才完成」的预载页
                # 不得再自动选中/自动下载（链保持冻结，等用户手动续跑）。
                self._on_extract_done(
                    preload_result.state,
                    allow_auto_download=not self._auto_chain_stopped,
                )
                # 下载已结束、预载补完：尝试接着自动处理队列（含本条后续页）。
                self._continue_queue_after_idle_completion(preload_result)
        elif msg_type == "preload_status":
            self._preload_status_var.set(str(data))
        elif msg_type == "page_title" and isinstance(data, PageTitleUpdate):
            # 预载中（下载中或下载后补完）的完整标题：写入对应队列条目。
            entry = self._current_preload_entry()
            if entry is not None and entry.page_url == data.page_url:
                entry.page_title = data.title
                self._save_preload_queue()
            self._apply_page_title(data.page_url, data.title)
        elif msg_type == "suggest_filename":
            # 标题流式回传：下载中预载则暂存，下载结束后立即填充文件名。
            if self._downloading:
                entry = self._current_preload_entry()
                if entry is not None:
                    entry.filename_title = str(data)
                self._pending_preload_title = str(data)
            else:
                self._suggest_filename(str(data))
        elif msg_type == "extract_done":
            self._on_extract_done(str(data))

    def _log(self, message: str) -> None:
        """向日志区追加一行文本.

        Args:
            message: 日志消息.
        """
        self._log_text.configure(state=tk.NORMAL)
        if message:
            self._log_text.insert(tk.END, message + "\n")
        else:
            self._log_text.insert(tk.END, "\n")
        self._log_text.see(tk.END)
        self._log_text.configure(state=tk.DISABLED)

    def _update_progress(self, data: dict) -> None:
        """更新进度条和状态文本.

        Args:
            data: 进度数据字典，包含 percent, completed, total, speed, eta, total_bytes.
        """
        percent = data.get("percent", 0)
        completed = data.get("completed", 0)
        total = data.get("total", 1)
        speed = data.get("speed", 0)
        eta = data.get("eta", 0)
        total_bytes = data.get("total_bytes", 0)

        self._progress_var.set(percent)
        self._status_var.set(
            f"正在下载 {completed}/{total} | "
            f"速度 {format_speed(speed)} | "
            f"已下载 {format_file_size(total_bytes)} | "
            f"剩余时间 {format_duration(eta)}"
        )

    def _on_download_done(self, result: str) -> None:
        """下载完成回调.

        Args:
            result: 完成状态（"success", "error", "stopped"）.
        """
        # 串行下载队列：还有后续任务则继续，不恢复按钮
        if self._pending_jobs:
            # 功能二：每完成一个任务都先把结果记入对应网页的下载记录。
            self._finalize_inflight_download_record(result)
            self._log("")
            self._run_next_job()
            return

        self._downloading = False
        self._clear_current_download_info()
        self._start_btn.configure(state=tk.NORMAL)
        self._stop_btn.configure(state=tk.DISABLED)
        self._extract_btn.configure(state=tk.DISABLED if self._extracting else tk.NORMAL)
        self._download_selected_btn.configure(
            state=tk.NORMAL if self._candidates else tk.DISABLED
        )

        if result == "success":
            self._progress_var.set(100)
            self._status_var.set("下载完成")
            # 「连续下载」勾选时自动确认「下载完成！」弹窗，无需用户点「确定」
            if not self._continuous_download_var.get():
                messagebox.showinfo("提示", "下载完成！")
        elif result == "stopped":
            self._status_var.set("下载已停止")
            # P2：点「停止下载」后冻结自动连播链（队列保留，等用户手动续跑）。
            if self._preload_queue:
                self._auto_chain_stopped = True
                self._log(
                    "已停止自动连播；待处理队列已保留，"
                    "手动点「下载选中 / 开始下载」续跑"
                )
        elif result == "error":
            self._status_var.set("下载失败")

        # 功能二：把本次下载结果记入对应网页的下载记录（仅当由网页发起时）。
        self._finalize_inflight_download_record(result)
        # 记录本次下载是否来自待处理队列头，随后复位（仅对单次下载生效）。
        was_queue_download = self._active_download_is_queue_page
        self._active_download_is_queue_page = False

        # ===== 多页连续预载队列路径 =====
        # 队列非空时，下载结束后自动衔接下一个待处理页；「停止下载」中断整条链
        # （队列保留，续跑由用户再次点「下载选中 / 开始下载」触发）。
        if self._preload_queue:
            # 成功始终衔接；队列页自身下载失败也跳过继续；手动页失败不自动起链。
            if result != "stopped" and (result == "success" or was_queue_download):
                self._advance_preload_queue_when_idle()
            # 文件名栏收尾：无下载继续、无进行中预载、且队列已清空时才清空。
            if (not self._downloading and not self._extracting
                    and not self._preload_queue):
                self._filename_var.set("")
            return

        # ===== 旧单页预载交接（无待处理队列时保持既有行为） =====

        # 下载全部结束后：立即填充下载期间流式暂存的预载标题（文件名）。
        # 点确认后，标题（on_title 流式回传暂存的）立即填入文件名栏。
        if self._pending_preload_title:
            self._suggest_filename(self._pending_preload_title)
            self._pending_preload_title = ""

        # 兜底回填预载标题（若 on_title 未流式回传，例如标题晚到）。
        has_prefill = self._flush_pending_extract()

        # 再逐条流式显示下载期间暂存的预载候选：先清空 A（切换），再逐条显示 B。
        if self._pending_preload_candidates:
            if not self._preload_list_cleared:
                self._clear_tree()
                self._preload_list_cleared = True
            for candidate in self._pending_preload_candidates:
                self._upsert_candidate(candidate)
            self._pending_preload_candidates.clear()

        # 预载场景：下载 A 完成后，预载的 B 若已提取完毕（result == "success"），
        # 按规则自动选中并自动下载 B，实现「连续下载」。
        pending_result = self._pending_extract_result
        self._pending_extract_result = ""
        if result == "success" and pending_result == "success":
            self._auto_select_and_download()

        # 下载完成后的文件名栏收尾：无预填标题、无进行中的预载提取、且未自动开始
        # 新下载时才清空。预载仍在提取中（_extracting=True）时不清空，等预载完成后
        # 填充，避免空白中间态；已自动开始下一次下载时也不清空，避免抹掉当前文件名。
        # 注意：清空/用户修改都不影响「提取填充文件名」——后者始终无条件优先。
        if not has_prefill and not self._extracting and not self._downloading:
            self._filename_var.set("")

    # ===== 网页抽取与多选下载 =====

    def _start_extract(self) -> None:
        """点击「提取网页」按钮的回调：起 daemon 线程抽取页内 m3u8.

        下载中预载的链接和标题在当前下载结束后一起回填；空闲时深度结果实时显示。
        """
        if self._extracting:
            return
        page_url = self._url_var.get().strip()
        if not page_url:
            self._log("错误：请输入网页地址")
            return
        if not page_url.startswith(("http://", "https://")):
            page_url = "https://" + page_url
            self._url_var.set(page_url)

        self._extracting = True
        self._extract_stop_flag.clear()
        self._extract_btn.configure(state=tk.DISABLED)
        self._stop_extract_btn.configure(state=tk.NORMAL)
        self._download_selected_btn.configure(state=tk.DISABLED)
        self._current_extract_page_url = page_url
        self._extract_recorded = False
        deep = bool(self._deep_var.get())
        preload = self._downloading
        if preload:
            self._preload_status_var.set("预载：正在提取下一网页…")
            # 预载：保留 A 候选，下载结束后再清空并切换到 B 候选。
            self._preload_list_cleared = False
            # 多页连续预载：每次下载中点击「提取网页」都追加为一个待处理队列条目，
            # 而不是顶掉上一个；同时清空上一轮暂存缓冲，避免候选/标题串页。
            self._preload_queue.append(PreloadQueueEntry(page_url=page_url))
            self._pending_preload_candidates.clear()
            self._pending_preload_title = ""
            self._save_preload_queue()
            self._update_queue_indicator()
        if not preload:
            self._page_title = ""
            self._candidate_page_url = page_url
            self._clear_tree()
            # 正常提取：已清空候选列表，候选到达即直接显示。
            self._preload_list_cleared = True
        self._log("正在抽取网页中的 m3u8 ...")
        proxy, no_proxy = self._resolve_proxy()
        threading.Thread(
            target=self._extract_worker,
            args=(page_url, deep, no_proxy, proxy, preload),
            daemon=True,
        ).start()

    def _extract_worker(
        self, page_url: str, deep: bool, no_proxy: bool = False, proxy: str = "",
        preload: bool = False,
    ) -> None:
        """抽取工作线程：调用 extractor，通过队列回传候选/完成消息.

        Args:
            page_url: 网页绝对 URL.
            deep: 是否深度模式.
            no_proxy: 为 True 时所有请求直连、跳过系统代理环境变量.
            proxy: 手动代理地址（如 ``127.0.0.1:7897``）；为空则不使用.
            preload: 启动提取时是否已有下载；固定本次行为，不随下载完成时机改变。
        """
        from m3u8_downloader.extractor import extract_m3u8_from_page_with_title
        from m3u8_downloader.utils import extract_title_segment

        mode_label = "深度模式（无头浏览器）" if deep else "普通模式（HTML + JS 静态扫描）"
        self._extract_mode = "深度" if deep else "普通"
        self._queue_message("log", f"提取模式：{mode_label}")

        try:
            # 一次拿到候选 + 标题；标题零额外请求（深度走 page.title，
            # 普通复用已抓 HTML）。on_title 流式回传标题：预载时下载中暂存，
            # 下载结束后立即填充文件名；正常提取直接填充。
            streamed_title = {"value": ""}

            def on_title_cb(t: str) -> None:
                if not t or streamed_title["value"]:
                    return
                streamed_title["value"] = t
                seg_early = extract_title_segment(t)
                self._queue_message("page_title", PageTitleUpdate(page_url, t))
                if seg_early:
                    self._queue_message("suggest_filename", seg_early)

            candidates, title = extract_m3u8_from_page_with_title(
                page_url,
                deep=deep,
                estimate=True,
                no_proxy=no_proxy,
                proxy=proxy,
                stop_event=self._extract_stop_flag,
                on_candidate=(lambda c: self._queue_message("candidate_update", replace(c))),
                on_title=on_title_cb,
            )
            seg = extract_title_segment(title) if title else ""

            if preload:
                result = (PreloadState.STOPPED if self._extract_stop_flag.is_set()
                          else PreloadState.SUCCESS)
                self._queue_message(
                    "preloaded_extract", PreloadResult(candidates, seg, title, page_url, result)
                )
            else:
                # 普通与深度模式统一：按 URL 更新原行，不清空列表、不重新排序，
                # 保留选择和滚动位置。流式阶段已显示部分候选，这里补齐并更新估计值。
                for candidate in candidates:
                    self._queue_message("candidate_update", replace(candidate))
                # 标题已在流式阶段回传；若流式未触发（标题为空或极端时序），此处兜底补发。
                if title and not streamed_title["value"]:
                    self._queue_message("page_title", PageTitleUpdate(page_url, title))
                if seg and not streamed_title["value"]:
                    self._queue_message("suggest_filename", seg)
                result = "stopped" if self._extract_stop_flag.is_set() else "success"
                self._queue_message("extract_done", result)
        except Exception as e:  # 任何异常都不让 GUI 崩溃
            if preload:
                # 预载异常也必须把对应队列条目 finalize 为终态（stopped / error），
                # 否则条目会永远停在 "extracting"，自动连播链在队头永久卡死。
                if self._extract_stop_flag.is_set():
                    self._queue_message("preload_status", "预载：已停止")
                    self._queue_message("log", "提取已停止")
                    state = PreloadState.STOPPED
                else:
                    self._queue_message("preload_status", "预载：失败，请查看日志")
                    self._queue_message("log", f"抽取失败：{e}")
                    state = PreloadState.ERROR
                self._queue_message(
                    "preloaded_extract",
                    PreloadResult([], "", "", page_url, state),
                )
            elif self._extract_stop_flag.is_set():
                self._queue_message("log", "提取已停止")
                self._queue_message("extract_done", "stopped")
            else:
                self._queue_message("log", f"抽取失败：{e}")
                self._queue_message("extract_done", "error")

    def _clear_tree(self) -> None:
        """清空候选列表 Treeview."""
        self._candidates = []
        self._candidate_items.clear()
        for item in self._tree.get_children():
            self._tree.delete(item)
        self._update_result_actions()

    def _upsert_candidate(self, candidate) -> None:
        """同一 URL 更新已有行，新 URL 追加；仅在 Tk 主线程调用。"""
        existing = self._candidate_items.get(candidate.url)
        if existing:
            item, index = existing
            self._candidates[index] = candidate
        else:
            index = len(self._candidates)
            self._candidates.append(candidate)
            item = self._tree.insert("", tk.END)
            self._candidate_items[candidate.url] = (item, index)
        ctype = "master" if candidate.is_master else ("media" if candidate.reachable else "-")
        self._tree.item(item, values=(
            index + 1, candidate.display_size(), candidate.display_duration(),
            candidate.display_bandwidth(), ctype, candidate.display_mode(),
            candidate.title or ("-" if candidate.reachable else "(不可达)"), candidate.url,
        ))
        self._update_result_actions()
        self._download_selected_btn.configure(state=tk.DISABLED if self._downloading else tk.NORMAL)
        if self._extracting and not self._downloading:
            self._status_var.set(f"已找到 {len(self._candidates)} 个结果，可选择下载；正在继续提取…")

    def _fill_tree(self, candidates: list) -> None:
        """把候选列表填入 Treeview.

        Args:
            candidates: :func:`extract_m3u8_from_page` 返回的候选列表.
        """
        self._clear_tree()
        self._candidates = list(candidates)
        for i, c in enumerate(candidates, 1):
            ctype = "master" if c.is_master else ("-" if not c.reachable else "media")
            title = c.title or ("(不可达)" if not c.reachable else "-")
            item = self._tree.insert(
                "",
                tk.END,
                values=(
                    i,
                    c.display_size(),
                    c.display_duration(),
                    c.display_bandwidth(),
                    ctype,
                    c.display_mode(),
                    title,
                    c.url,
                ),
            )
            self._candidate_items[c.url] = (item, i - 1)
        self._update_result_actions()
        if candidates:
            self._download_selected_btn.configure(state=tk.NORMAL)

    def _suggest_filename(self, base_name: str) -> None:
        """抽取成功后用网页标题段落自动填充输出文件名.

        始终用网页标题自动填充，不设「手动命名」保护：用户会在填充后再修改文件名。

        Args:
            base_name: 网页标题截取后的文件名基底（不含扩展名），如 ``仙界法务部 第55集 (2026)``.
        """
        if not base_name:
            return
        self._filename_var.set(base_name)
        self._log(f"已按网页标题自动命名：{base_name}")

    def _on_extract_done(self, result: str, *, allow_auto_download: bool = True) -> None:
        """抽取完成的 UI 收尾.

        Args:
            result: 抽取结果（"success" / "pending" / "empty" / "error" / "stopped"）.
            allow_auto_download: 是否允许 success 后自动选中并自动下载。P2 停止后
                下载已结束才完成的预载页会传 False（链冻结，等用户手动续跑）.
        """
        # 功能二：把本次「提取网页」事件记入网页下载记录（含下载中预载的页面）。
        # 用 _extract_recorded 防止一次提取被多次收尾重复入账。
        if self._current_extract_page_url and not self._extract_recorded:
            self._extract_recorded = True
            try:
                from m3u8_downloader import page_history
                page_history.record_page_extracted(self._current_extract_page_url)
            except Exception:
                pass  # 记录失败不影响提取主流程

        self._extracting = False
        self._extract_btn.configure(state=tk.NORMAL)
        self._stop_extract_btn.configure(state=tk.DISABLED)
        self._download_selected_btn.configure(
            state=tk.NORMAL if (self._candidates and not self._downloading) else tk.DISABLED
        )
        # 非下载状态（未在下载）时，抽取结束应还原停止按钮为禁用
        if not self._downloading:
            self._stop_btn.configure(state=tk.DISABLED)
        else:
            # 预载场景：结果先暂存，等当前下载结束后（_on_download_done）再判定
            # 自动选中 / 自动下载；"pending" 表示预载已成功完成。
            self._pending_extract_result = "success" if result == "pending" else result
            self._log("提取已停止，保留已有结果" if result == "stopped" else "网页提取结束，下载继续")
            return
        if result == "success":
            self._status_var.set("抽取完成")
            mode = getattr(self, "_extract_mode", "普通")
            self._log(f"本次提取模式：{mode}；以上大小均为估计值")
            # 提取正常完成：按规则自动选中，满足条件时自动下载
            if allow_auto_download:
                self._auto_select_and_download()
        elif result == "pending":
            # 下载中预加载：不覆盖「正在下载」状态，等下载完成后由 _flush_pending_extract 显示
            pass
        elif result == "empty":
            self._status_var.set("未找到候选")
            self._log("提示：未从该网页找到任何 m3u8，可尝试勾选「深度模式」")
        elif result == "stopped":
            self._status_var.set("提取已停止")
        else:
            self._status_var.set("抽取失败")

    # ===== 自动选中 / 自动下载 =====

    def _pick_auto_candidate(self) -> "Optional[tuple[object, str]]":
        """按规则挑出应自动选中的候选，不满足条件时返回 None.

        规则（与产品确认一致）：
            1. 候选数为 1 → 选中该候选；
            2. 候选数 > 1 且所有候选的 ``display_duration()`` 字符串完全相同 →
               选中 ``estimated_size`` 最大的那个；
            3. 时长未知（``display_duration()`` 返回 ``"-"``）视为与任何其他时长都
               不同 → 不满足「全部相同」→ 不自动选中；
            4. 混合时长 / 全部时长都不同 → 不自动选中。

        Returns:
            ``(候选对象, 选中原因)``；无合适候选时返回 None.
        """
        candidates = list(self._candidates)
        if not candidates:
            return None

        if len(candidates) == 1:
            return candidates[0], "唯一结果"

        # 任一候选时长未知即视为「与任何其他时长都不同」，不满足「全部相同」
        if any(c.display_duration() == "-" for c in candidates):
            return None
        if len({c.display_duration() for c in candidates}) != 1:
            return None
        # 时长全部相同：取估计体积最大的候选（体积相同则取列表中第一个）
        best = max(candidates, key=lambda c: int(getattr(c, "estimated_size", 0) or 0))
        return best, "时长相同中最大"

    def _auto_select_candidate(self) -> "Optional[object]":
        """把 :meth:`_pick_auto_candidate` 挑中的候选在列表中选中并写日志.

        Returns:
            被自动选中的候选对象；无合适候选（或缺少对应列表行）时返回 None.
        """
        picked = self._pick_auto_candidate()
        if picked is None:
            return None
        candidate, reason = picked

        item_info = self._candidate_items.get(candidate.url)
        if item_info is None:
            return None
        self._tree.selection_set(item_info[0])
        self._update_result_actions()

        label = candidate.title or candidate.url
        self._log(f"已自动选中：{label}（{reason}）")
        return candidate

    def _auto_select_and_download(self) -> None:
        """提取正常完成后的自动处理：先自动选中，满足条件时再自动下载.

        自动下载需同时满足（与产品确认一致）：
            1. 「自动下载」勾选框已勾选（默认勾选，持久化）；
            2. 本次会话用户已手动下载过至少一次（会话级标记，不持久化）；
            3. 本次提取正常完成（调用方保证 result == "success"）；
            4. 手动优先：自动下载不重复处理用户已手动触发过的链接。

        下载复用「下载选中」的入口 :meth:`_download_selected`，与手动点击行为一致
        （同名文件仍走现有的「覆盖 / 自动改名 / 取消」提醒）。
        """
        # 下载进行中（含预载未交接）时不做任何自动处理
        if self._downloading:
            return

        candidate = self._auto_select_candidate()
        if candidate is None:
            return

        if not bool(self._auto_download_var.get()):
            return
        if not self._session_manual_downloaded:
            self._log("提示：本次会话尚未手动下载过，未自动下载（需先手动下载一次）")
            return
        if candidate.url in self._manual_downloaded_urls:
            return  # 手动优先：已手动触发过的链接不再自动下载

        label = candidate.title or candidate.url
        self._log(f"自动下载已启动：{label}")
        self._download_selected()

    def _flush_pending_extract(self) -> bool:
        """下载全部完成后，显示最近一次挂起的「预加载」提取结果.

        Returns:
            True 表示有预填标题已填入文件名栏；False 表示无预填（可清空文件名栏）.
        """
        if not self._pending_extract:
            return False
        preload_result = self._pending_extract[-1]
        self._pending_extract.clear()
        # 候选已通过 candidate_update 流式显示，这里只回填标题（文件名）与状态。
        self._candidate_page_url = preload_result.page_url
        self._apply_page_title(preload_result.page_url, preload_result.page_title)
        self._preload_status_var.set(
            f"预载：已载入 {len(preload_result.candidates)} 条结果"
        )
        if preload_result.filename_title:
            self._suggest_filename(preload_result.filename_title)
            self._log("已显示预加载网页的提取结果")
            self._status_var.set("抽取完成")
            return True
        self._log("已显示预加载网页的提取结果")
        self._status_var.set("抽取完成")
        return False

    # ===== 多页连续预载队列（下载中预载、自动连播、持久化） =====

    def _load_preload_queue(self) -> list:
        """启动时从 PRELOAD_QUEUE_FILE 恢复待处理队列.

        恢复的 ``"extracting"`` 条目会被归一化为可跳过的 ``"error"`` 终态：
        提取线程无法跨进程存活，重启后不存在任何能把它 finalize 的 worker，
        若不归一化，续跑会在队头永久等待一个永不完结的条目。

        Returns:
            恢复出的 ``PreloadQueueEntry`` 列表；文件缺失/损坏/结构异常时为空.
        """
        try:
            with open(PRELOAD_QUEUE_FILE, "r", encoding="utf-8") as f:
                raw = json.load(f)
            entries = raw.get("entries", []) if isinstance(raw, dict) else []
            queue = []
            for e in entries:
                if not isinstance(e, dict):
                    continue
                entry = PreloadQueueEntry.from_dict(e)
                if not entry.page_url:
                    continue
                if entry.state == "extracting":
                    # 重启恢复：不存在存活的工作线程，置为终态避免卡链。
                    entry.state = "error"
                queue.append(entry)
            return queue
        except (OSError, ValueError):
            return []

    def _save_preload_queue(self) -> None:
        """持久化待处理队列（无上限；写入失败静默，不影响主流程）."""
        try:
            PRELOAD_QUEUE_FILE.parent.mkdir(parents=True, exist_ok=True)
            with open(PRELOAD_QUEUE_FILE, "w", encoding="utf-8") as f:
                json.dump(
                    {"entries": [e.to_dict() for e in self._preload_queue]},
                    f, ensure_ascii=False,
                )
        except Exception:
            pass

    def _update_queue_indicator(self) -> None:
        """同步「待处理 N 个」按钮的计数文本与可用状态（仅在变化时更新）."""
        count = len(self._preload_queue)
        want_state = tk.NORMAL if count else tk.DISABLED
        if (count != self._queue_indicator_count
                or want_state != self._queue_indicator_state):
            self._queue_count_var.set(f"待处理 {count} 个")
            if getattr(self, "_queue_count_btn", None) is not None:
                self._queue_count_btn.configure(state=want_state)
            self._queue_indicator_count = count
            self._queue_indicator_state = want_state

    def _current_preload_entry(self) -> "Optional[PreloadQueueEntry]":
        """返回当前正在提取（尚未完成）的队列条目（队列尾）；没有则返回 None."""
        if not self._preload_queue:
            return None
        tail = self._preload_queue[-1]
        return tail if tail.state == "extracting" else None

    def _finalize_preload_entry(self, result: PreloadResult) -> None:
        """预载提取完成：把结果固化到对应的队列条目（缺失时防御性补建）.

        Args:
            result: 提取工作线程回传的预载结果.
        """
        entry = self._current_preload_entry()
        if entry is None or entry.page_url != result.page_url:
            entry = PreloadQueueEntry(page_url=result.page_url)
            self._preload_queue.append(entry)
        if result.state == PreloadState.SUCCESS:
            entry.state = "success"
        elif result.state == PreloadState.STOPPED:
            entry.state = "stopped"
        else:
            entry.state = "error"
        if result.candidates:
            entry.candidates = list(result.candidates)
        if result.filename_title:
            entry.filename_title = result.filename_title
        if result.page_title:
            entry.page_title = result.page_title
        self._save_preload_queue()
        self._update_queue_indicator()

    def _drop_queue_head_if_page(self, page_url: str) -> bool:
        """队列头正对应 ``page_url`` 时，视为该页「轮到下载」并从队列移除.

        成功 / 失败 / 用户手动再次下载都算已处理；停止下载只中断后续自动链，
        尚未轮到（队列里更靠后）的条目保留。

        Args:
            page_url: 正在发起下载的网页 URL.

        Returns:
            True 表示本次下载确实移除了队列头（即下载的是待处理页）.
        """
        if not self._preload_queue:
            return False
        head = self._preload_queue[0]
        if head.state != "extracting" and head.page_url == page_url:
            self._preload_queue.pop(0)
            self._save_preload_queue()
            self._update_queue_indicator()
            if not self._preload_queue:
                # 队列清空：清理旧单页交接镜像，避免污染之后的无队列交接逻辑。
                self._pending_extract.clear()
                self._pending_extract_result = ""
                self._pending_preload_candidates.clear()
            return True
        return False

    def _present_queue_entry(self, entry: PreloadQueueEntry) -> None:
        """把队列条目作为「当前正在处理的页」展示（列表只显示当前页）.

        清空上一页候选、逐条填入该页候选，并回填网页标题 / 文件名。
        """
        self._preload_list_cleared = True
        self._clear_tree()
        for candidate in entry.candidates:
            self._upsert_candidate(candidate)
        self._candidate_page_url = entry.page_url
        if entry.page_title:
            self._page_title = entry.page_title
            self._apply_page_title(entry.page_url, entry.page_title)
        if entry.filename_title:
            self._suggest_filename(entry.filename_title)
        self._preload_status_var.set(
            f"预载：已载入 {len(entry.candidates)} 条结果"
        )
        self._status_var.set("抽取完成" if entry.state == "success" else "网页结果已显示")
        self._log(f"已显示待处理页结果：{entry.page_url}")

    def _auto_trigger_download(self) -> bool:
        """按现有自动下载规则尝试自动下载当前页.

        Returns:
            True 表示真正启动了下载（队列头已随之移除）；False 表示未启动.
        """
        self._auto_select_and_download()
        return bool(self._downloading)

    def _select_candidate_rows(self, urls) -> None:
        """按 m3u8 URL 选中候选列表中的对应行（手动入队条目轮到时的精确续下）.

        Args:
            urls: 需要选中的 m3u8 链接列表（缺失的行自动忽略）.
        """
        items = []
        for url in urls or []:
            info = self._candidate_items.get(url)
            if info is not None:
                items.append(info[0])
        if items:
            self._tree.selection_set(items)
            self._update_result_actions()

    def _advance_preload_queue_when_idle(self) -> None:
        """空闲时依次处理待处理队列：逐个展示并（满足规则时）自动下载.

        仅在无下载、无提取时调用。队列头仍在提取中则等待其完成后继续；
        stopped / error 条目视为不可连播，跳过并继续下一个（不中断整链）。
        success 条目被展示后按自动下载规则决定是否立即下载：
        - 规则满足 → 下载开始（该页已在下载启动时移出队列，链继续）；
        - 规则不满足（未勾选自动下载 / 会话尚未手动下载过 / 无自动候选）
          → 保留在队列，等用户手动点「下载选中 / 开始下载」续跑。

        P2：用户点「停止下载」后 ``_auto_chain_stopped`` 为 True，此处直接返回，
        整条链冻结直到用户再次手动触发下载清除该标志。
        手动入队条目（``manual=True``）优先：轮到它时不设自动下载门控，直接下载。
        """
        if self._auto_chain_stopped:
            return
        if self._downloading or self._extracting:
            return
        while self._preload_queue:
            head = self._preload_queue[0]
            if head.state == "extracting":
                return  # 等该页预载完成后再继续
            if head.state != "success":
                self._preload_queue.pop(0)
                self._save_preload_queue()
                self._update_queue_indicator()
                self._log(f"已跳过待处理页：{head.page_url}（{head.state}）")
                continue
            self._present_queue_entry(head)
            if head.manual:
                # 手动加入的条目：手动优先，不受「自动下载」勾选/会话手动标志限制。
                self._session_manual_downloaded = True
                for url in head.manual_urls:
                    self._manual_downloaded_urls.add(url)
                self._select_candidate_rows(head.manual_urls)
                if not self._tree.selection():
                    # 防御：手动选择的链接已不在候选里时退化为自动选中。
                    self._auto_select_candidate()
                self._log(f"开始下载手动加入队列的页：{head.page_url}")
                self._download_selected()
                return  # head 已在下载启动时从队列移除
            if self._auto_trigger_download():
                return  # 已开始下载该页；head 在下载启动时已从队列移除
            return  # 规则不满足/无自动候选：head 保留队列，等用户手动续跑
        # 队列清空后的收尾
        self._pending_extract.clear()
        self._pending_extract_result = ""
        self._pending_preload_candidates.clear()

    def _continue_queue_after_idle_completion(self, preload_result: PreloadResult) -> None:
        """下载先结束、预载后完成时的队列簿记.

        预载页在下载结束后才完成：候选已流式显示，``_on_extract_done(success)``
        已尝试自动选中/下载。这里只负责收尾：
        - 已开始下载（``_downloading=True``）→ head 已在下载启动时移除；
        - success 但未开始下载 → 页面已展示，head 保留等待手动续跑；
        - stopped / error → 该页不可连播，移除并继续队列下一个.

        Args:
            preload_result: 下载结束后才完成的预载结果.
        """
        if self._downloading:
            return
        # P2：停止后整个链冻结；失败/停止页也等手动续跑时再跳过，不自动推进。
        if self._auto_chain_stopped:
            return
        if preload_result.state == PreloadState.SUCCESS:
            return
        self._drop_queue_head_if_page(preload_result.page_url)
        self._advance_preload_queue_when_idle()

    # ===== 功能二：网页下载记录（独立只读，与队列分开） =====

    def _finalize_inflight_download_record(self, result: str) -> None:
        """下载结束：把结果记入该下载所属网页的下载记录.

        Args:
            result: 下载完成状态（"success" / "error" / "stopped"）.
        """
        page_url = self._inflight_download_page_url
        m3u8_url = self._inflight_download_url
        self._inflight_download_page_url = ""
        self._inflight_download_url = ""
        if not page_url:
            return
        try:
            from m3u8_downloader import page_history
            if result == "success":
                page_history.record_page_downloaded(page_url, m3u8_url)
            elif result == "error":
                page_history.record_page_failed(page_url)
            # stopped（用户手动停止）不改写状态，保留为「已提取」。
        except Exception:
            pass

    def _show_preload_queue_popup(self) -> None:
        """点击「待处理 N 个」：弹出窗口列出各待处理页 URL."""
        if not self._preload_queue:
            return
        win = tk.Toplevel(self._root)
        win.title(f"待处理预载队列（{len(self._preload_queue)} 个）")
        win.geometry("640x360")
        frame = ttk.Frame(win, padding=8)
        frame.pack(fill=tk.BOTH, expand=True)
        ttk.Label(frame, text="以下页面将按顺序在当前下载结束后自动续播：").pack(
            anchor=tk.W, pady=(0, 5)
        )
        body = ttk.Frame(frame)
        body.pack(fill=tk.BOTH, expand=True)
        body.columnconfigure(0, weight=1)
        body.rowconfigure(0, weight=1)
        text = tk.Text(body, wrap=tk.WORD, state=tk.DISABLED)
        text.grid(row=0, column=0, sticky=tk.NSEW)
        scroll = ttk.Scrollbar(body, orient=tk.VERTICAL, command=text.yview)
        text.configure(yscrollcommand=scroll.set)
        scroll.grid(row=0, column=1, sticky=tk.NS)
        state_names = {
            "extracting": "提取中",
            "success": "已就绪",
            "stopped": "已停止",
            "error": "失败",
        }
        text.configure(state=tk.NORMAL)
        for i, entry in enumerate(self._preload_queue, 1):
            label = state_names.get(entry.state, entry.state)
            text.insert(tk.END, f"{i}. [{label}] {entry.page_url}\n")
        text.configure(state=tk.DISABLED)
        ttk.Button(frame, text="关闭", command=win.destroy).pack(anchor=tk.E, pady=(6, 0))

    def _show_page_history(self) -> None:
        """打开独立只读的「网页下载记录」面板（新→旧，最多 500 条）."""
        from m3u8_downloader import page_history
        try:
            records = page_history.list_records()
        except Exception:
            records = []
        win = tk.Toplevel(self._root)
        win.title("网页下载记录")
        win.geometry("820x460")
        frame = ttk.Frame(win, padding=8)
        frame.pack(fill=tk.BOTH, expand=True)
        body = ttk.Frame(frame)
        body.pack(fill=tk.BOTH, expand=True)
        body.columnconfigure(0, weight=1)
        body.rowconfigure(0, weight=1)
        tree = ttk.Treeview(
            body,
            columns=("time", "status", "url", "m3u8"),
            show="headings",
        )
        tree.heading("time", text="时间")
        tree.heading("status", text="状态")
        tree.heading("url", text="网页 URL")
        tree.heading("m3u8", text="实际下载的 m3u8")
        tree.column("time", width=150, anchor=tk.W)
        tree.column("status", width=90, anchor=tk.CENTER)
        tree.column("url", width=300)
        tree.column("m3u8", width=300)
        tree.grid(row=0, column=0, sticky=tk.NSEW)
        scroll = ttk.Scrollbar(body, orient=tk.VERTICAL, command=tree.yview)
        tree.configure(yscrollcommand=scroll.set)
        scroll.grid(row=0, column=1, sticky=tk.NS)
        status_names = {
            "downloaded": "已下载",
            "failed": "下载失败",
            "extracted": "已提取",
        }
        for record in records:
            status = status_names.get(
                str(record.get("status", "")), str(record.get("status", ""))
            )
            # 同一页多次下载过的 m3u8 以换行累积在 m3u8_url 字段；树状单元格换行
            # 显示不佳，展示时用「 | 」连接便于阅读（记录本身仍完整保留）。
            m3u8_display = str(record.get("m3u8_url", "") or "").replace("\n", " | ")
            tree.insert(
                "", tk.END,
                values=(
                    record.get("timestamp", ""),
                    status,
                    record.get("page_url", ""),
                    m3u8_display,
                ),
            )
        if not records:
            ttk.Label(frame, text="暂无记录。提取网页后，页面会显示在这里。").pack(
                anchor=tk.W, pady=(6, 0)
            )
        ttk.Button(frame, text="关闭", command=win.destroy).pack(anchor=tk.E, pady=(6, 0))

    def _on_tree_selection_changed(self, _event=None) -> None:
        self._update_result_actions()

    def _update_result_actions(self) -> None:
        """同步候选计数和选择工具状态。"""
        total = len(self._tree.get_children())
        selected = len(self._tree.selection())
        self._selection_summary_var.set(f"共 {total} 条，已选 {selected} 条")
        self._select_all_btn.configure(state=tk.NORMAL if total else tk.DISABLED)
        selected_state = tk.NORMAL if selected else tk.DISABLED
        self._clear_selection_btn.configure(state=selected_state)
        self._copy_links_btn.configure(state=selected_state)

    def _select_all_candidates(self) -> None:
        items = self._tree.get_children()
        if items:
            self._tree.selection_set(items)
        self._update_result_actions()

    def _clear_candidate_selection(self) -> None:
        self._tree.selection_remove(self._tree.selection())
        self._update_result_actions()

    def _copy_selected_links(self) -> None:
        links = []
        for item in self._tree.selection():
            values = self._tree.item(item, "values")
            if values and len(values) > 7:
                links.append(str(values[7]))
        if not links:
            self._log("提示：请先选择要复制的链接")
            return
        self._root.clipboard_clear()
        self._root.clipboard_append("\n".join(links))
        self._root.update_idletasks()
        self._log(f"已复制 {len(links)} 条链接")

    def _on_tree_single_click(self, event) -> None:
        """单击候选行：切换该行选中状态，不影响其他已选行（无需 Ctrl/Shift）.

        用 after 延迟 200ms 区分单击与双击：双击时取消本次单击处理，
        只走 _on_tree_double_click（回填链接），避免双击触发两次 toggle。

        保留 Ctrl+单击 / Shift+单击 的标准行为：有修饰键时放行给 Treeview
        默认处理（Ctrl=切换单行、Shift=范围选择），仅无修饰键时接管为 toggle。
        """
        # Ctrl(0x0004) / Shift(0x0001) 按下时，交给 Treeview 默认选择行为
        if event.state & 0x0005:
            return None

        # 取消上一次未执行的单击定时器
        if self._tree_click_after_id is not None:
            try:
                self._root.after_cancel(self._tree_click_after_id)
            except Exception:
                pass
            self._tree_click_after_id = None

        row_id = self._tree.identify_row(event.y)
        if not row_id:
            return "break"
        self._tree_click_after_id = self._root.after(
            200, lambda: self._tree_toggle_select(row_id)
        )
        # 阻止 Treeview 默认的「单击选中并取消其他行」行为，由我们接管切换逻辑
        return "break"

    def _tree_toggle_select(self, row_id: str) -> None:
        """切换某行的选中状态（已选则取消、未选则选中），不影响其他行."""
        self._tree_click_after_id = None
        if row_id in self._tree.selection():
            self._tree.selection_remove(row_id)
        else:
            self._tree.selection_add(row_id)
        self._update_result_actions()

    def _on_tree_double_click(self, event) -> None:
        """双击候选行：把该行链接回填到地址框（单一下载快捷路径）."""
        # 取消待执行的单击切换，避免双击时 toggle 两次
        if self._tree_click_after_id is not None:
            try:
                self._root.after_cancel(self._tree_click_after_id)
            except Exception:
                pass
            self._tree_click_after_id = None
        sel = self._tree.selection()
        if not sel:
            return
        values = self._tree.item(sel[0], "values")
        if values and len(values) > 7:
            url = values[7]
            self._url_var.set(url)
            self._log(f"已填入链接：{url}")

    def _download_selected(self) -> None:
        """点击「下载选中」：把多选行组装为串行下载任务队列.

        新澄清（统一队列语义）：队列非空且队头是另一「已就绪」页时，本次手动下载
        不直接开始，而是把当前页作为 ``manual`` 条目追加到队尾（先处理队列头）；
        队列为空或队头正是当前页时才直接（续跑队头）开始下载。
        """
        # P2：手动触发下载 → 解除「停止后抑制自动连播」（无论是否真正开始）。
        self._auto_chain_stopped = False
        if self._downloading:
            return
        sel = self._tree.selection()
        if not sel:
            self._log("提示：请先在列表中选择要下载的 m3u8")
            return

        base_name = self._filename_var.get().strip() or "output.mp4"
        save_dir = self._dir_var.get().strip()
        total = len(sel)

        # 多文件（≥2）且勾选「创建文件夹」：以提取名作为文件夹名归拢文件
        target_dir = save_dir
        if total >= 2 and bool(self._create_folder_var.get()):
            folder_base = (
                extract_title_segment(self._page_title)
                or os.path.splitext(base_name)[0].strip()
            )
            folder_name = sanitize_filename_component(folder_base)
            if folder_name:
                candidate_dir = os.path.join(save_dir, folder_name)
                try:
                    os.makedirs(candidate_dir, exist_ok=True)
                    target_dir = candidate_dir
                    self._log(f"已创建文件夹：{candidate_dir}")
                except Exception as e:
                    self._log(f"提示：创建文件夹失败，文件将保存到保存目录：{e}")

        jobs = []
        for item in sel:
            values = self._tree.item(item, "values")
            if not values or len(values) < 8:
                continue
            url = values[7]
            orig_no = int(values[0])
            row_title = str(values[6]) if values[6] not in ("", "-") else ""
            output_name = build_output_path(base_name, orig_no, total)
            output_path = normalize_mp4_filename(
                os.path.join(target_dir, output_name)
            )
            jobs.append(DownloadJob(
                url, output_path, self._page_title or row_title, self._candidate_page_url,
            ))

        if not jobs:
            return

        # 会话级标记：本次会话已手动下载过（「自动下载」的前置条件，不持久化）。
        # 同时记录链接，「手动优先」——自动下载不再重复处理用户已手动触发的链接。
        self._session_manual_downloaded = True
        for job in jobs:
            self._manual_downloaded_urls.add(job.url)

        # ===== 新澄清：队列非空且队头是另一「已就绪」页 → 手动下载加入队尾 =====
        page_url = (self._candidate_page_url or "").strip()
        head = self._preload_queue[0] if self._preload_queue else None
        head_is_ready = head is not None and head.state != "extracting"
        if page_url and head_is_ready and head.page_url != page_url:
            # 当前页不直接下载：作为「手动优先」条目追加队尾，再按序处理队头。
            manual_entry = PreloadQueueEntry(
                page_url=page_url,
                state="success",
                candidates=list(self._candidates),
                filename_title=os.path.splitext(base_name)[0].strip() or "output",
                page_title=self._page_title,
                manual=True,
                manual_urls=[job.url for job in jobs],
            )
            self._preload_queue.append(manual_entry)
            self._save_preload_queue()
            self._update_queue_indicator()
            self._log(
                f"已将当前页加入待处理队列尾（共 {len(self._preload_queue)} 个），"
                "先处理队列头"
            )
            self._advance_preload_queue_when_idle()
            return

        self._pending_jobs = jobs
        self._log(f"已加入 {len(jobs)} 个下载任务，开始串行下载")
        self._log("下载进行中可预载下一网页，链接和标题将在当前下载结束后一起回填")
        self._start_btn.configure(state=tk.DISABLED)
        self._stop_btn.configure(state=tk.NORMAL)
        # 下载进行中保留「提取网页」可用，实现网页预加载（结果挂起，下载完成后显示）
        self._download_selected_btn.configure(state=tk.DISABLED)
        self._run_next_job()

    def _run_next_job(self) -> None:
        """从串行队列弹出下一个任务并启动下载线程.

        同名文件被「取消」的任务会被跳过并继续弹下一个；若队列全部被取消
        （未启动任何下载），则恢复按钮状态。
        """
        while self._pending_jobs:
            job = self._pending_jobs.pop(0)
            self._log(f"下载: {job.url}")

            # 重复链接提醒（已下载过则询问是否仍要下载）
            if not self._confirm_duplicate(job.url):
                self._log(f"已跳过重复链接: {job.url}")
                continue  # 跳过重复任务，继续下一个

            # 同名文件覆盖提醒（覆盖 / 自动改名 / 取消）
            output_path = self._resolve_output_path_collision(job.output_path)
            if output_path is None:
                self._log(f"已取消下载: {job.url}")
                continue  # 跳过被取消的任务，继续下一个

            self._log(f"保存为: {output_path}")

            workers = self._workers_var.get()
            retries = self._retries_var.get()
            timeout = self._timeout_var.get()
            use_ffmpeg = self._use_ffmpeg_var.get()
            tmp_dir = self._tmpdir_var.get().strip()

            self._downloading = True
            self._stop_flag = threading.Event()
            download_stop_event = self._stop_flag
            self._progress_var.set(0)
            self._status_var.set("正在下载...")
            self._current_source_page_url = job.source_page_url
            self._set_current_download_info(output_path, job.title)
            self._stop_btn.configure(state=tk.NORMAL)

            # 功能二：记住本次下载对应的网页（记录下载成功/失败用）。
            self._inflight_download_url = job.url
            self._inflight_download_page_url = job.source_page_url

            self._download_thread = threading.Thread(
                target=self._download_worker,
                args=(job.url, output_path, workers, retries, timeout, use_ffmpeg,
                      tmp_dir, download_stop_event),
                daemon=True,
            )
            self._download_thread.start()
            # 若本次下载的正是待处理队列头页面：视为「轮到下载」，从队列移除。
            self._active_download_is_queue_page = self._drop_queue_head_if_page(
                job.source_page_url
            )
            return  # 已启动一个任务，等 done 回调再弹下一个

        # 队列空（全部取消或本就没有任务）：未启动任何下载，恢复按钮
        self._downloading = False
        self._clear_current_download_info()
        self._start_btn.configure(state=tk.NORMAL)
        self._extract_btn.configure(state=tk.DISABLED if self._extracting else tk.NORMAL)
        self._download_selected_btn.configure(
            state=tk.NORMAL if self._candidates else tk.DISABLED
        )
        self._stop_btn.configure(state=tk.DISABLED)
        self._status_var.set("已取消全部下载")


def run_gui() -> None:
    """启动 GUI 界面."""
    root = tk.Tk()
    # 设置高 DPI 感知（Windows）
    try:
        from ctypes import windll
        windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        pass
    _app = M3U8DownloaderGUI(root)
    root.geometry("980x900")
    root.minsize(980, 700)
    root.mainloop()
