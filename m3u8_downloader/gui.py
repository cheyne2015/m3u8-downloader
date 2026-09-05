"""Tkinter GUI 界面模块：提供图形化下载操作界面."""

import json
import os
import queue
import subprocess
import sys
import threading
import time
import tkinter as tk
from dataclasses import replace
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from typing import Optional

from m3u8_downloader import __version__
from m3u8_downloader.downloader import M3U8Downloader
from m3u8_downloader.extractor import is_deep_mode_available
from m3u8_downloader.utils import (
    build_output_path,
    format_duration,
    format_file_size,
    format_speed,
    is_ffmpeg_available,
    normalize_mp4_filename,
)

# GUI 偏好配置文件路径：存放"记住保存位置"等界面偏好
GUI_CONFIG_PATH: Path = Path(os.path.expanduser("~/.m3u8-downloader/gui_config.json"))


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
        self._root.minsize(760, 760)

        # 下载状态变量
        self._downloading: bool = False
        self._stop_flag: threading.Event = threading.Event()
        self._download_thread: Optional[threading.Thread] = None
        self._message_queue: queue.Queue = queue.Queue()

        # 网页抽取 / 多选下载状态
        self._candidates: list = []
        self._pending_jobs: list = []
        self._extracting: bool = False
        self._extract_stop_flag = threading.Event()
        self._candidate_items: dict = {}
        # 下载中预加载的提取结果（挂起）：[(candidates, title_seg, title), ...]
        self._pending_extract: list = []

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
        # 用户手动改过文件名后，不再用网页标题自动覆盖
        self._filename_touched = False
        self._filename_entry.bind("<Key>", self._on_filename_typed)
        self._filename_entry.bind("<<Paste>>", self._on_filename_typed)

        row += 1

        # 记住保存位置（下次启动自动填充）
        self._remember_dir_var = tk.BooleanVar(value=False)
        remember_dir_check = ttk.Checkbutton(
            main_frame,
            text="记住保存位置（下次启动自动填充）",
            variable=self._remember_dir_var,
            command=self._save_dir_preference,
        )
        remember_dir_check.grid(row=row, column=1, columnspan=2, sticky=tk.W, pady=(0, 5))

        row += 1

        # ===== 参数设置区 =====
        param_frame = ttk.LabelFrame(main_frame, text="参数设置", padding=8)
        param_frame.grid(row=row, column=0, columnspan=3, sticky=tk.EW, pady=(0, 10))
        param_frame.columnconfigure(1, weight=1)
        param_frame.columnconfigure(3, weight=1)

        # 并发线程数
        ttk.Label(param_frame, text="并发线程数：").grid(row=0, column=0, sticky=tk.W, padx=(0, 5))
        self._workers_var = tk.IntVar(value=8)
        workers_spin = ttk.Spinbox(
            param_frame, from_=1, to=64, textvariable=self._workers_var, width=8
        )
        workers_spin.grid(row=0, column=1, sticky=tk.W, padx=(0, 20))

        # 重试次数
        ttk.Label(param_frame, text="重试次数：").grid(row=0, column=2, sticky=tk.W, padx=(0, 5))
        self._retries_var = tk.IntVar(value=3)
        retries_spin = ttk.Spinbox(
            param_frame, from_=0, to=100, textvariable=self._retries_var, width=8
        )
        retries_spin.grid(row=0, column=3, sticky=tk.W)

        # 超时时间
        ttk.Label(param_frame, text="超时时间(秒)：").grid(row=1, column=0, sticky=tk.W, padx=(0, 5), pady=(5, 0))
        self._timeout_var = tk.IntVar(value=30)
        timeout_spin = ttk.Spinbox(
            param_frame, from_=5, to=300, textvariable=self._timeout_var, width=8
        )
        timeout_spin.grid(row=1, column=1, sticky=tk.W, padx=(0, 20), pady=(5, 0))

        # 使用 ffmpeg 合并
        self._use_ffmpeg_var = tk.BooleanVar(value=True)
        ffmpeg_check = ttk.Checkbutton(
            param_frame, text="使用 ffmpeg 合并转码", variable=self._use_ffmpeg_var
        )
        ffmpeg_check.grid(row=1, column=2, columnspan=2, sticky=tk.W, pady=(5, 0))

        # 临时目录
        ttk.Label(param_frame, text="临时目录：").grid(row=2, column=0, sticky=tk.W, padx=(0, 5), pady=(5, 0))
        tmp_frame = ttk.Frame(param_frame)
        tmp_frame.grid(row=2, column=1, columnspan=3, sticky=tk.EW, pady=(5, 0))
        tmp_frame.columnconfigure(0, weight=1)

        self._tmpdir_var = tk.StringVar(value="")
        self._tmpdir_entry = ttk.Entry(tmp_frame, textvariable=self._tmpdir_var)
        self._tmpdir_entry.grid(row=0, column=0, sticky=tk.EW, padx=(0, 5))

        tmp_browse_btn = ttk.Button(
            tmp_frame, text="浏览", command=self._browse_tmpdir, width=6
        )
        tmp_browse_btn.grid(row=0, column=1)

        # 深度模式（无头浏览器）复选框
        self._deep_var = tk.BooleanVar(value=False)
        deep_check = ttk.Checkbutton(
            param_frame, text="深度模式（需 playwright）", variable=self._deep_var
        )
        deep_check.grid(row=3, column=0, columnspan=2, sticky=tk.W, pady=(5, 0))
        if not is_deep_mode_available():
            deep_check.configure(state=tk.DISABLED)

        # 使用代理复选框（默认不勾选 = 直连，不通过任何代理）
        self._use_proxy_var = tk.BooleanVar(value=False)
        use_proxy_check = ttk.Checkbutton(
            param_frame, text="使用代理", variable=self._use_proxy_var
        )
        use_proxy_check.grid(row=4, column=0, columnspan=2, sticky=tk.W, pady=(5, 0))

        # 手动代理地址（本地 clash 默认 127.0.0.1:7897；勾选「使用代理」后生效）
        ttk.Label(param_frame, text="代理地址：").grid(
            row=5, column=0, sticky=tk.W, padx=(0, 5), pady=(5, 0)
        )
        self._proxy_var = tk.StringVar(value="127.0.0.1:7897")
        proxy_entry = ttk.Entry(param_frame, textvariable=self._proxy_var, width=24)
        proxy_entry.grid(row=5, column=1, columnspan=3, sticky=tk.W, pady=(5, 0))

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

        # UI 构建完成、变量均已创建后，恢复"记住保存位置"偏好
        self._load_dir_preference()

        # 深度模式依赖缺失时给出安装提示（此时日志区已就绪，可安全 _log）
        if not is_deep_mode_available():
            self._log(
                "提示：未安装 playwright，深度模式不可用；"
                "安装：pip install playwright && playwright install chromium"
            )

    # ===== UI 回调方法 =====

    def _paste_url(self) -> None:
        """从剪贴板粘贴 URL 到输入框."""
        try:
            clipboard_text = self._root.clipboard_get()
            self._url_var.set(clipboard_text.strip())
        except tk.TclError:
            pass  # 剪贴板为空或不可访问

    def _on_filename_typed(self, event=None) -> None:
        """文件名输入框有手动输入/粘贴时标记，停止自动覆盖."""
        if not self._filename_touched:
            self._filename_touched = True
            self._log("已手动指定文件名，后续抽取不再自动覆盖")

    def _browse_dir(self) -> None:
        """浏览选择保存目录."""
        selected = filedialog.askdirectory(initialdir=self._dir_var.get())
        if selected:
            self._dir_var.set(selected)
            # 已勾选"记住保存位置"时，同步持久化新选择的目录
            if self._remember_dir_var.get():
                self._save_dir_preference()

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

    def _load_dir_preference(self) -> None:
        """读取"记住保存位置"偏好，必要时填充保存目录.

        配置文件为 GUI_CONFIG_PATH（~/.m3u8-downloader/gui_config.json）。
        仅当 remember_dir 为 True 且 last_dir 是已存在的目录时，才填充保存目录
        并勾选复选框；文件不存在、JSON 损坏或缺少键时，安全降级为"不记住"，
        绝不让 GUI 启动失败。
        """
        try:
            with open(GUI_CONFIG_PATH, "r", encoding="utf-8") as f:
                config = json.load(f)
            if not isinstance(config, dict):
                raise ValueError("配置内容不是 JSON 对象")

            remember_dir = bool(config.get("remember_dir", False))
            last_dir = str(config.get("last_dir", "") or "").strip()
            restored = remember_dir and bool(last_dir) and os.path.isdir(last_dir)
        except Exception:
            # 配置不存在 / JSON 损坏 / 结构异常：安全降级为"不记住"
            self._remember_dir_var.set(False)
            return

        if restored:
            self._dir_var.set(last_dir)
            self._remember_dir_var.set(True)
        else:
            self._remember_dir_var.set(False)

    def _save_dir_preference(self) -> None:
        """将"记住保存位置"偏好与当前保存目录写入配置文件.

        勾选时记录 remember_dir=True 及当前目录；取消勾选时 remember_dir=False
        且清空 last_dir，保证下次启动不会自动填充。写入失败仅在日志区提示。
        """
        remember_dir = bool(self._remember_dir_var.get())
        current_dir = self._dir_var.get().strip()
        config = {
            "remember_dir": remember_dir,
            "last_dir": current_dir if remember_dir else "",
        }

        try:
            GUI_CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
            with open(GUI_CONFIG_PATH, "w", encoding="utf-8") as f:
                json.dump(config, f, ensure_ascii=False, indent=2)
        except Exception as e:
            self._log(f"提示：保存位置偏好写入失败：{e}")

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
        self._stop_flag.clear()
        self._start_btn.configure(state=tk.DISABLED)
        self._stop_btn.configure(state=tk.NORMAL)

        # 重置进度
        self._progress_var.set(0)
        self._status_var.set("正在下载...")
        self._set_current_download_info(output_path)
        self._log("下载进行中可预载下一网页，链接和标题将在当前下载结束后一起回填")

        # 启动下载线程
        self._download_thread = threading.Thread(
            target=self._download_worker,
            args=(url, output_path, workers, retries, timeout, use_ffmpeg, tmp_dir),
            daemon=True,
        )
        self._download_thread.start()

    def _stop_download(self) -> None:
        """停止下载，不影响独立进行的网页扫描。"""
        if self._downloading:
            self._stop_flag.set()
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

    def _set_current_download_info(self, output_path: str) -> None:
        """在固定状态区显示当前任务，避免与预载网页混淆。"""
        filename = os.path.basename(output_path)
        title = os.path.splitext(filename)[0] or "—"
        self._current_title_var.set(f"当前标题：{title}")
        self._current_output_var.set(f"保存文件：{filename or '—'}")

    def _clear_current_download_info(self) -> None:
        self._current_title_var.set("当前标题：—")
        self._current_output_var.set("保存文件：—")

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
            proxy: 手动代理地址（如 ``127.0.0.1:7897``）；为空则不使用.
        """
        try:
            # 检查 ffmpeg
            if use_ffmpeg and not is_ffmpeg_available():
                self._queue_message("log", "未检测到 ffmpeg，将使用 TS 二进制拼接方式")
                use_ffmpeg = False

            self._queue_message("log", f"正在解析 m3u8: {url}")

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
            )

            # 使用自定义的下载流程以便回调进度
            self._run_download_with_progress(downloader)

        except RuntimeError as e:
            if self._stop_flag.is_set():
                self._queue_message("log", "下载已停止")
                self._queue_message("done", "stopped")
            else:
                self._queue_message("log", f"错误：{e}")
                self._queue_message("done", "error")
        except Exception as e:
            if self._stop_flag.is_set():
                self._queue_message("log", "下载已停止")
                self._queue_message("done", "stopped")
            else:
                self._queue_message("log", f"未知错误：{e}")
                self._queue_message("done", "error")

    def _run_download_with_progress(self, downloader: M3U8Downloader) -> None:
        """执行带进度回调的下载流程.

        复用 M3U8Downloader 内部方法，但通过自定义回调更新 GUI 进度。

        Args:
            downloader: M3U8Downloader 实例.

        Raises:
            RuntimeError: 下载失败或被停止.
        """
        import time as _time

        from m3u8_downloader.parser import M3U8Parser, select_best_stream

        start_time = _time.time()

        # 1. 解析 m3u8 播放列表
        if self._stop_flag.is_set():
            raise RuntimeError("用户停止")

        content = downloader._fetch_m3u8_content(downloader._url)
        parser = M3U8Parser(content, downloader._url)
        playlist = parser.parse()

        if playlist.is_master:
            best_stream = select_best_stream(playlist)
            self._queue_message(
                "log",
                f"检测到多码率列表，选择最高码率: {best_stream.bandwidth} bps"
                f"{f' ({best_stream.resolution})' if best_stream.resolution else ''}",
            )
            content = downloader._fetch_m3u8_content(best_stream.url)
            parser = M3U8Parser(content, best_stream.url)
            playlist = parser.parse()

        if not playlist.segments:
            raise RuntimeError("m3u8 播放列表中没有找到任何 TS 片段")

        total_segments = len(playlist.segments)
        self._queue_message(
            "log",
            f"解析完成: {total_segments} 个片段, 总时长 {format_duration(playlist.total_duration)}"
            f"{', 加密流' if playlist.has_encryption else ''}",
        )

        # 2. 下载解密密钥
        if self._stop_flag.is_set():
            raise RuntimeError("用户停止")

        if playlist.has_encryption:
            downloader._download_keys(playlist)

        # 3. 并发下载 TS 片段（自定义逻辑以更新进度）
        if self._stop_flag.is_set():
            raise RuntimeError("用户停止")

        from concurrent.futures import ThreadPoolExecutor, as_completed

        from m3u8_downloader.downloader import _download_segment_task

        os.makedirs(downloader._tmp_dir, exist_ok=True)
        self._queue_message("log", f"共 {total_segments} 个 TS 片段，使用 {downloader._workers} 线程并发下载")

        tasks = []
        for i, segment in enumerate(playlist.segments):
            seg_filename = f"seg_{i:05d}.ts"
            seg_path = os.path.join(downloader._tmp_dir, seg_filename)
            tasks.append((segment, seg_path))

        success_count = 0
        fail_count = 0
        total_bytes = 0
        download_start = _time.time()

        with ThreadPoolExecutor(max_workers=downloader._workers) as executor:
            futures = {}
            for segment, seg_path in tasks:
                future = executor.submit(
                    _download_segment_task,
                    downloader._session,
                    segment,
                    seg_path,
                    downloader._max_retries,
                    downloader._timeout,
                )
                futures[future] = (segment, seg_path)

            for future in as_completed(futures):
                if self._stop_flag.is_set():
                    executor.shutdown(wait=False, cancel_futures=True)
                    raise RuntimeError("用户停止")

                segment, seg_path = futures[future]
                try:
                    seq, success, size = future.result()
                    if success:
                        success_count += 1
                        total_bytes += size
                    else:
                        fail_count += 1
                        self._queue_message("log", f"片段 {seg_path} 下载失败")
                except Exception as e:
                    fail_count += 1
                    self._queue_message("log", f"片段 {seg_path} 下载异常: {e}")

                # 更新进度
                completed = success_count + fail_count
                percent = completed / total_segments * 100
                elapsed = _time.time() - download_start
                speed = total_bytes / elapsed if elapsed > 0 else 0
                eta = (total_segments - completed) / speed * (total_bytes / completed) if completed > 0 and speed > 0 else 0
                # 更简洁的 ETA 计算
                if completed > 0 and elapsed > 0:
                    avg_per_seg = elapsed / completed
                    remaining_segments = total_segments - completed
                    eta = avg_per_seg * remaining_segments
                else:
                    eta = 0

                self._queue_message("progress", {
                    "percent": percent,
                    "completed": completed,
                    "total": total_segments,
                    "speed": speed,
                    "eta": eta,
                    "total_bytes": total_bytes,
                })

        if fail_count > 0:
            raise RuntimeError(f"有 {fail_count} 个片段下载失败")

        # 4. 解密片段
        if self._stop_flag.is_set():
            raise RuntimeError("用户停止")

        segment_paths = [seg_path for _, seg_path in tasks]

        if playlist.has_encryption:
            from m3u8_downloader.merger import decrypt_segments
            self._queue_message("log", "正在解密 TS 片段...")
            segment_paths = decrypt_segments(playlist.segments, segment_paths)

        # 5. 合并为 MP4
        if self._stop_flag.is_set():
            raise RuntimeError("用户停止")

        from m3u8_downloader.merger import merge_segments_to_mp4
        self._queue_message("log", "正在合并 TS 片段...")
        output_path = merge_segments_to_mp4(
            segment_paths=segment_paths,
            output_path=downloader._output,
            use_ffmpeg=downloader._use_ffmpeg,
        )

        # 6. 清理临时文件
        downloader._cleanup_tmp()

        # 7. 完成
        elapsed = _time.time() - start_time
        output_size = os.path.getsize(output_path) if os.path.exists(output_path) else 0
        self._queue_message("log", "")
        self._queue_message("log", "下载完成！")
        self._queue_message("log", f"输出文件: {os.path.abspath(output_path)}")
        self._queue_message("log", f"文件大小: {format_file_size(output_size)}")
        self._queue_message("log", f"总耗时: {format_duration(elapsed)}")
        # 记录到下载历史（去 query 后），供「重复链接提醒」跨会话去重
        from m3u8_downloader.history import record_download
        record_download(downloader._url)
        self._queue_message("done", "success")

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
            self._upsert_candidate(data)
        elif msg_type == "preloaded_extract":
            candidates, segment, title, result = data
            # 结果和标题在主线程一起交接，避免下载完成与工作线程暂存结果竞态。
            if self._downloading:
                self._pending_extract[:] = [(candidates, segment, title)]
                if result == "success":
                    self._preload_status_var.set(
                        f"预载：成功，找到 {len(candidates)} 条，等待当前下载结束"
                    )
                else:
                    self._preload_status_var.set("预载：已停止，保留已找到结果")
                self._log("网页预载完成，链接和标题等待当前下载结束后一起回填")
                self._on_extract_done("pending" if result == "success" else result)
            else:
                # 下载先结束、预载后完成时，直接显示这组配套结果。
                self._pending_extract.clear()
                self._fill_tree(candidates)
                if segment:
                    self._suggest_filename(segment)
                self._preload_status_var.set(f"预载：已载入 {len(candidates)} 条结果")
                self._on_extract_done(result)
        elif msg_type == "preload_status":
            self._preload_status_var.set(str(data))
        elif msg_type == "suggest_filename":
            # 抽取成功后用网页标题段落自动填充输出文件名（仅当用户未改过默认名）
            if not self._downloading:
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
            messagebox.showinfo("提示", "下载完成！")
        elif result == "stopped":
            self._status_var.set("下载已停止")
        elif result == "error":
            self._status_var.set("下载失败")

        # 下载（含串行队列）全部结束后，显示挂起的预加载提取结果
        has_prefill = self._flush_pending_extract()

        # 下载完成后的文件名栏收尾：无预填标题 → 清空文件名栏；同时重置手动标志，
        # 让下一轮抽取能正常自动命名。（有预填时标题已由 _flush_pending_extract 填入）
        if not has_prefill:
            self._filename_var.set("")
        self._filename_touched = False

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
        deep = bool(self._deep_var.get())
        preload = self._downloading
        if preload:
            self._preload_status_var.set("预载：正在提取下一网页…")
        if not preload:
            self._clear_tree()
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
            # 普通复用已抓 HTML），避免二次抓取慢/拿不到。
            candidates, title = extract_m3u8_from_page_with_title(
                page_url,
                deep=deep,
                estimate=True,
                no_proxy=no_proxy,
                proxy=proxy,
                stop_event=self._extract_stop_flag,
                **({"on_candidate": (lambda c: None) if preload else
                   (lambda c: self._queue_message("candidate_update", replace(c)))}
                   if deep else {}),
            )
            seg = extract_title_segment(title) if title else ""

            if preload:
                result = "stopped" if self._extract_stop_flag.is_set() else "success"
                self._queue_message("preloaded_extract", (candidates, seg, title, result))
            elif deep:
                # 按 URL 更新原行，不清空列表、不重新排序，保留选择和滚动位置。
                for candidate in candidates:
                    self._queue_message("candidate_update", replace(candidate))
                if seg:
                    self._queue_message("suggest_filename", seg)
                result = "stopped" if self._extract_stop_flag.is_set() else "success"
                self._queue_message("extract_done", result)
            elif self._downloading:
                # 网页预加载：下载进行中，挂起结果，等下载+合成完成后再显示
                self._pending_extract.append((candidates, seg, title))
                self._queue_message("log", "网页提取完成，等待当前下载结束后显示结果")
                self._queue_message("extract_done", "pending")
            else:
                self._queue_message("candidates", candidates)
                # 仅在有标题段落时自动命名；不再单独打印标题提取日志，避免与命名日志重复
                if seg:
                    self._queue_message("suggest_filename", seg)
                self._queue_message("extract_done", "success")
        except Exception as e:  # 任何异常都不让 GUI 崩溃
            if self._extract_stop_flag.is_set():
                if preload:
                    self._queue_message("preload_status", "预载：已停止")
                self._queue_message("log", "提取已停止")
                self._queue_message("extract_done", "stopped")
            else:
                if preload:
                    self._queue_message("preload_status", "预载：失败，请查看日志")
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

        仅在用户未手动修改过文件名时生效，避免覆盖用户已输入的自定义名。

        Args:
            base_name: 网页标题截取后的文件名基底（不含扩展名），如 ``仙界法务部 第55集 (2026)``.
        """
        if self._filename_touched:
            return
        if not base_name:
            return
        self._filename_var.set(base_name)
        self._log(f"已按网页标题自动命名：{base_name}")

    def _on_extract_done(self, result: str) -> None:
        """抽取完成的 UI 收尾.

        Args:
            result: 抽取结果（"success" / "pending" / "empty" / "error" / "stopped"）.
        """
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
            self._log("提取已停止，保留已有结果" if result == "stopped" else "网页提取结束，下载继续")
            return
        if result == "success":
            self._status_var.set("抽取完成")
            mode = getattr(self, "_extract_mode", "普通")
            self._log(f"本次提取模式：{mode}；以上大小均为估计值")
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

    def _flush_pending_extract(self) -> bool:
        """下载全部完成后，显示最近一次挂起的「预加载」提取结果.

        Returns:
            True 表示有预填标题已填入文件名栏；False 表示无预填（可清空文件名栏）.
        """
        if not self._pending_extract:
            return False
        candidates, seg, _ = self._pending_extract[-1]
        self._pending_extract.clear()
        self._fill_tree(candidates)
        self._preload_status_var.set(f"预载：已载入 {len(candidates)} 条结果")
        if seg:
            # 下载完成后进入新一轮，重置手动标志，让预填标题稳定填入文件名栏
            self._filename_touched = False
            self._suggest_filename(seg)
            self._log("已显示预加载网页的提取结果")
            self._status_var.set("抽取完成")
            return True
        self._log("已显示预加载网页的提取结果")
        self._status_var.set("抽取完成")
        return False

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
        """点击「下载选中」：把多选行组装为串行下载任务队列."""
        if self._downloading:
            return
        sel = self._tree.selection()
        if not sel:
            self._log("提示：请先在列表中选择要下载的 m3u8")
            return

        base_name = self._filename_var.get().strip() or "output.mp4"
        save_dir = self._dir_var.get().strip()
        total = len(sel)

        jobs = []
        for item in sel:
            values = self._tree.item(item, "values")
            if not values or len(values) < 8:
                continue
            url = values[7]
            orig_no = int(values[0])
            output_name = build_output_path(base_name, orig_no, total)
            output_path = normalize_mp4_filename(
                os.path.join(save_dir, output_name)
            )
            jobs.append((url, output_path))

        if not jobs:
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
            url, output_path = self._pending_jobs.pop(0)
            self._log(f"下载: {url}")

            # 重复链接提醒（已下载过则询问是否仍要下载）
            if not self._confirm_duplicate(url):
                self._log(f"已跳过重复链接: {url}")
                continue  # 跳过重复任务，继续下一个

            # 同名文件覆盖提醒（覆盖 / 自动改名 / 取消）
            output_path = self._resolve_output_path_collision(output_path)
            if output_path is None:
                self._log(f"已取消下载: {url}")
                continue  # 跳过被取消的任务，继续下一个

            self._log(f"保存为: {output_path}")

            workers = self._workers_var.get()
            retries = self._retries_var.get()
            timeout = self._timeout_var.get()
            use_ffmpeg = self._use_ffmpeg_var.get()
            tmp_dir = self._tmpdir_var.get().strip()

            self._downloading = True
            self._stop_flag.clear()
            self._progress_var.set(0)
            self._status_var.set("正在下载...")
            self._set_current_download_info(output_path)
            self._stop_btn.configure(state=tk.NORMAL)

            self._download_thread = threading.Thread(
                target=self._download_worker,
                args=(url, output_path, workers, retries, timeout, use_ffmpeg, tmp_dir),
                daemon=True,
            )
            self._download_thread.start()
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
    root.minsize(760, 760)
    root.mainloop()
