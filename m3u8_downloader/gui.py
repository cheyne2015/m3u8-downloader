"""Tkinter GUI 界面模块：提供图形化下载操作界面."""

import json
import os
import queue
import subprocess
import sys
import threading
import time
import tkinter as tk
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
        self._root.geometry("900x800")
        self._root.minsize(760, 620)

        # 下载状态变量
        self._downloading: bool = False
        self._stop_flag: threading.Event = threading.Event()
        self._download_thread: Optional[threading.Thread] = None
        self._message_queue: queue.Queue = queue.Queue()

        # 网页抽取 / 多选下载状态
        self._candidates: list = []
        self._pending_jobs: list = []
        self._extracting: bool = False

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

        # 直连/跳过代理复选框
        self._no_proxy_var = tk.BooleanVar(value=False)
        no_proxy_check = ttk.Checkbutton(
            param_frame, text="直连/跳过代理", variable=self._no_proxy_var
        )
        no_proxy_check.grid(row=4, column=0, columnspan=2, sticky=tk.W, pady=(5, 0))

        row += 1

        # ===== 网页提取结果区 =====
        extract_frame = ttk.LabelFrame(main_frame, text="网页提取结果", padding=8)
        extract_frame.grid(row=row, column=0, columnspan=3, sticky=tk.EW, pady=(0, 10))
        extract_frame.columnconfigure(0, weight=1)
        extract_frame.rowconfigure(0, weight=1)

        self._tree = ttk.Treeview(
            extract_frame,
            columns=("no", "size", "duration", "bandwidth", "type", "title", "url"),
            show="headings",
            selectmode="extended",
            height=8,
        )
        self._tree.heading("no", text="#")
        self._tree.heading("size", text="估计大小")
        self._tree.heading("duration", text="时长")
        self._tree.heading("bandwidth", text="码率")
        self._tree.heading("type", text="类型")
        self._tree.heading("title", text="标题")
        self._tree.heading("url", text="链接")
        self._tree.column("no", width=40, anchor=tk.CENTER)
        self._tree.column("size", width=110)
        self._tree.column("duration", width=90)
        self._tree.column("bandwidth", width=100)
        self._tree.column("type", width=70)
        self._tree.column("title", width=120)
        self._tree.column("url", width=300, stretch=True)

        tree_scroll = ttk.Scrollbar(
            extract_frame, orient=tk.VERTICAL, command=self._tree.yview
        )
        self._tree.configure(yscrollcommand=tree_scroll.set)
        self._tree.grid(row=0, column=0, sticky=tk.NSEW)
        tree_scroll.grid(row=0, column=1, sticky=tk.NS)
        self._tree.bind("<Double-1>", self._on_tree_double_click)

        self._download_selected_btn = ttk.Button(
            extract_frame,
            text="下载选中",
            command=self._download_selected,
            state=tk.DISABLED,
            width=15,
        )
        self._download_selected_btn.grid(row=1, column=0, sticky=tk.W, pady=(5, 0))

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

        row += 1

        # ===== 日志显示区 =====
        log_frame = ttk.LabelFrame(main_frame, text="日志", padding=8)
        log_frame.grid(row=row, column=0, columnspan=3, sticky=tk.NSEW, pady=(0, 0))
        log_frame.columnconfigure(0, weight=1)
        log_frame.rowconfigure(0, weight=1)

        # 使最后一行占据剩余空间
        main_frame.rowconfigure(row, weight=1)

        self._log_text = tk.Text(
            log_frame, height=10, wrap=tk.WORD, state=tk.DISABLED, font=("Consolas", 9)
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

        output_path = os.path.join(save_dir, filename)
        # 规范化：保证最终保存文件后缀为 .mp4 且仅有一个 .mp4
        output_path = normalize_mp4_filename(output_path)
        # 把规范化后的名称回填到输入框，让用户清楚实际会保存成什么文件
        self._filename_var.set(os.path.basename(output_path))
        self._log(f"保存文件: {output_path}")

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

        # 启动下载线程
        self._download_thread = threading.Thread(
            target=self._download_worker,
            args=(url, output_path, workers, retries, timeout, use_ffmpeg, tmp_dir),
            daemon=True,
        )
        self._download_thread.start()

    def _stop_download(self) -> None:
        """点击停止下载按钮的回调."""
        if self._downloading:
            self._stop_flag.set()
            self._log("正在停止下载...")
            self._status_var.set("正在停止...")

    def _download_worker(
        self,
        url: str,
        output_path: str,
        workers: int,
        retries: int,
        timeout: int,
        use_ffmpeg: bool,
        tmp_dir: str,
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
        """
        try:
            # 检查 ffmpeg
            if use_ffmpeg and not is_ffmpeg_available():
                self._queue_message("log", "未检测到 ffmpeg，将使用 TS 二进制拼接方式")
                use_ffmpeg = False

            self._queue_message("log", f"正在解析 m3u8: {url}")

            # 创建下载器实例
            downloader = M3U8Downloader(
                url=url,
                output=output_path,
                workers=workers,
                tmp_dir=tmp_dir,
                use_ffmpeg=use_ffmpeg,
                max_retries=retries,
                timeout=timeout,
                no_proxy=self._no_proxy_var.get(),
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
        self._start_btn.configure(state=tk.NORMAL)
        self._stop_btn.configure(state=tk.DISABLED)
        self._extract_btn.configure(state=tk.NORMAL)
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

    # ===== 网页抽取与多选下载 =====

    def _start_extract(self) -> None:
        """点击「提取网页」按钮的回调：起 daemon 线程抽取页内 m3u8."""
        if self._extracting or self._downloading:
            return
        page_url = self._url_var.get().strip()
        if not page_url:
            self._log("错误：请输入网页地址")
            return
        if not page_url.startswith(("http://", "https://")):
            page_url = "https://" + page_url
            self._url_var.set(page_url)

        self._extracting = True
        self._extract_btn.configure(state=tk.DISABLED)
        self._download_selected_btn.configure(state=tk.DISABLED)
        self._clear_tree()
        self._log("正在抽取网页中的 m3u8 ...")
        deep = bool(self._deep_var.get())
        no_proxy = bool(self._no_proxy_var.get())
        threading.Thread(
            target=self._extract_worker, args=(page_url, deep, no_proxy), daemon=True
        ).start()

    def _extract_worker(self, page_url: str, deep: bool, no_proxy: bool = False) -> None:
        """抽取工作线程：调用 extractor，通过队列回传候选/完成消息.

        Args:
            page_url: 网页绝对 URL.
            deep: 是否深度模式.
            no_proxy: 为 True 时所有请求直连、跳过系统代理环境变量.
        """
        from m3u8_downloader.extractor import extract_m3u8_from_page

        try:
            candidates = extract_m3u8_from_page(
                page_url, deep=deep, estimate=True, no_proxy=no_proxy
            )
            self._queue_message("candidates", candidates)
            self._queue_message("extract_done", "success")
        except Exception as e:  # 任何异常都不让 GUI 崩溃
            self._queue_message("log", f"抽取失败：{e}")
            self._queue_message("extract_done", "error")

    def _clear_tree(self) -> None:
        """清空候选列表 Treeview."""
        for item in self._tree.get_children():
            self._tree.delete(item)

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
            self._tree.insert(
                "",
                tk.END,
                values=(
                    i,
                    c.display_size(),
                    c.display_duration(),
                    c.display_bandwidth(),
                    ctype,
                    title,
                    c.url,
                ),
            )
        if candidates:
            self._download_selected_btn.configure(state=tk.NORMAL)

    def _on_extract_done(self, result: str) -> None:
        """抽取完成的 UI 收尾.

        Args:
            result: 抽取结果（"success" / "empty" / "error"）.
        """
        self._extracting = False
        self._extract_btn.configure(state=tk.NORMAL)
        self._download_selected_btn.configure(
            state=tk.NORMAL if self._candidates else tk.DISABLED
        )
        if result == "success":
            self._status_var.set("抽取完成")
            self._log("以上大小均为估计值")
        elif result == "empty":
            self._status_var.set("未找到候选")
            self._log("提示：未从该网页找到任何 m3u8，可尝试勾选「深度模式」")
        else:
            self._status_var.set("抽取失败")

    def _on_tree_double_click(self, event) -> None:
        """双击候选行：把该行链接回填到地址框（单一下载快捷路径）."""
        sel = self._tree.selection()
        if not sel:
            return
        values = self._tree.item(sel[0], "values")
        if values and len(values) > 6:
            url = values[6]
            self._url_var.set(url)
            self._log(f"已填入链接：{url}")

    def _download_selected(self) -> None:
        """点击「下载选中」：把多选行组装为串行下载任务队列."""
        if self._downloading or self._extracting:
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
            if not values or len(values) < 7:
                continue
            url = values[6]
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
        self._start_btn.configure(state=tk.DISABLED)
        self._extract_btn.configure(state=tk.DISABLED)
        self._download_selected_btn.configure(state=tk.DISABLED)
        self._run_next_job()

    def _run_next_job(self) -> None:
        """从串行队列弹出下一个任务并启动下载线程."""
        if not self._pending_jobs:
            return
        url, output_path = self._pending_jobs.pop(0)
        self._log(f"下载: {url}")
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

        self._download_thread = threading.Thread(
            target=self._download_worker,
            args=(url, output_path, workers, retries, timeout, use_ffmpeg, tmp_dir),
            daemon=True,
        )
        self._download_thread.start()


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
    root.mainloop()
