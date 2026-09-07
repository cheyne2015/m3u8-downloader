"""Tests for m3u8_downloader.gui module."""

import inspect
import json
import os
import queue
import threading
from contextlib import ExitStack
from unittest.mock import MagicMock, call, patch

import pytest

from m3u8_downloader.gui import (
    DownloadJob, M3U8DownloaderGUI, PageTitleUpdate, PreloadResult, PreloadState, run_gui,
)


# ---------------------------------------------------------------------------
# Helpers: Mock Tkinter so tests run headless
# ---------------------------------------------------------------------------


def _make_mock_root():
    """Create a mock Tk root with enough interface for M3U8DownloaderGUI.__init__."""
    root = MagicMock()
    root.tk = MagicMock()
    root.after = MagicMock()
    root.clipboard_get = MagicMock(return_value="")
    return root


def _tk_patches():
    """Return a list of patch objects for all tkinter widgets used by gui.py."""
    return [
        patch("tkinter.StringVar", lambda **kw: MagicMock(get=MagicMock(return_value=""), set=MagicMock())),
        patch("tkinter.IntVar", lambda **kw: MagicMock(get=MagicMock(return_value=kw.get("value", 0)))),
        patch("tkinter.BooleanVar", lambda **kw: MagicMock(get=MagicMock(return_value=kw.get("value", False)))),
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


@pytest.fixture
def gui_instance():
    """Fixture that provides a M3U8DownloaderGUI instance with mocked Tkinter."""
    with ExitStack() as stack:
        for p in _tk_patches():
            stack.enter_context(p)
        root = _make_mock_root()
        gui = M3U8DownloaderGUI(root)
        yield gui


# ---------------------------------------------------------------------------
# M3U8DownloaderGUI instantiation
# ---------------------------------------------------------------------------


class TestM3U8DownloaderGUIInstantiation:
    """Tests that M3U8DownloaderGUI can be instantiated with mocked Tkinter."""

    def test_gui_class_exists(self):
        """M3U8DownloaderGUI class is importable."""
        assert M3U8DownloaderGUI is not None

    def test_gui_init_with_mock_tk(self, gui_instance):
        """M3U8DownloaderGUI can be initialized with a mock root window."""
        assert gui_instance is not None
        assert gui_instance._root is not None
        assert gui_instance._downloading is False
        assert isinstance(gui_instance._stop_flag, threading.Event)
        assert isinstance(gui_instance._message_queue, queue.Queue)

    def test_gui_downloading_initial_state(self, gui_instance):
        """After init, downloading flag should be False."""
        assert gui_instance._downloading is False

    def test_gui_stop_flag_cleared_initially(self, gui_instance):
        """After init, stop flag should be cleared."""
        assert gui_instance._stop_flag.is_set() is False

    def test_gui_download_thread_none_initially(self, gui_instance):
        """After init, download thread should be None."""
        assert gui_instance._download_thread is None


# ---------------------------------------------------------------------------
# run_gui function
# ---------------------------------------------------------------------------


class TestRunGui:
    """Tests for the run_gui() entry point function."""

    def test_run_gui_exists(self):
        """run_gui function exists and is callable."""
        assert callable(run_gui)

    def test_run_gui_creates_tk_root(self):
        """run_gui creates a tk.Tk() root window."""
        with patch("m3u8_downloader.gui.tk") as mock_tk:
            mock_root = MagicMock()
            mock_tk.Tk.return_value = mock_root
            mock_root.mainloop = MagicMock()

            with patch.object(M3U8DownloaderGUI, "_build_ui"):
                with patch.object(M3U8DownloaderGUI, "_poll_queue"):
                    run_gui()
                    mock_tk.Tk.assert_called_once()
                    mock_root.mainloop.assert_called_once()


# ---------------------------------------------------------------------------
# GUI default values
# ---------------------------------------------------------------------------


class TestGUIDefaultValues:
    """Tests that GUI default parameter values match CLI defaults."""

    def test_default_workers_var_exists(self, gui_instance):
        """Default workers variable should exist after init."""
        assert gui_instance._workers_var is not None

    def test_default_filename_var_exists(self, gui_instance):
        """Default filename variable should exist after init."""
        assert gui_instance._filename_var is not None

    def test_default_dir_var_exists(self, gui_instance):
        """Default directory variable should exist after init."""
        assert gui_instance._dir_var is not None

    def test_default_retries_var_exists(self, gui_instance):
        """Default retries variable should exist after init."""
        assert gui_instance._retries_var is not None

    def test_default_timeout_var_exists(self, gui_instance):
        """Default timeout variable should exist after init."""
        assert gui_instance._timeout_var is not None

    def test_default_use_ffmpeg_var_exists(self, gui_instance):
        """Default use_ffmpeg variable should exist after init."""
        assert gui_instance._use_ffmpeg_var is not None


# ---------------------------------------------------------------------------
# CLI --gui argument
# ---------------------------------------------------------------------------


class TestGuiArgument:
    """Tests for --gui argument in CLI parser."""

    def test_gui_flag_default_false(self):
        """--gui defaults to False."""
        from m3u8_downloader.cli import create_parser
        parser = create_parser()
        args = parser.parse_args(["https://example.com/index.m3u8"])
        assert args.gui is False

    def test_gui_flag_set_true(self):
        """--gui flag can be set to True."""
        from m3u8_downloader.cli import create_parser
        parser = create_parser()
        args = parser.parse_args(["--gui"])
        assert args.gui is True

    def test_gui_with_url(self):
        """--gui can be combined with a URL."""
        from m3u8_downloader.cli import create_parser
        parser = create_parser()
        args = parser.parse_args(["https://example.com/index.m3u8", "--gui"])
        assert args.gui is True
        assert args.url == "https://example.com/index.m3u8"


# ---------------------------------------------------------------------------
# GUI message queue mechanism
# ---------------------------------------------------------------------------


class TestMessageQueue:
    """Tests for the message queue mechanism in M3U8DownloaderGUI."""

    def test_queue_message_puts_to_queue(self, gui_instance):
        """_queue_message puts a tuple (msg_type, data) into the message queue."""
        gui_instance._queue_message("log", "test message")
        msg_type, data = gui_instance._message_queue.get_nowait()
        assert msg_type == "log"
        assert data == "test message"

    def test_queue_message_multiple(self, gui_instance):
        """Multiple messages are queued in order."""
        gui_instance._queue_message("log", "msg1")
        gui_instance._queue_message("progress", {"percent": 50})
        gui_instance._queue_message("done", "success")

        msg1_type, msg1_data = gui_instance._message_queue.get_nowait()
        msg2_type, msg2_data = gui_instance._message_queue.get_nowait()
        msg3_type, msg3_data = gui_instance._message_queue.get_nowait()

        assert msg1_type == "log" and msg1_data == "msg1"
        assert msg2_type == "progress" and msg2_data == {"percent": 50}
        assert msg3_type == "done" and msg3_data == "success"

    def test_poll_queue_processes_messages(self, gui_instance):
        """_poll_queue drains messages from the queue."""
        gui_instance._queue_message("log", "test")

        with patch.object(gui_instance, "_handle_message") as mock_handle:
            gui_instance._poll_queue()
            mock_handle.assert_called_once_with("log", "test")

        # Verify after() was called to schedule next poll
        gui_instance._root.after.assert_called()

    def test_handle_message_log(self, gui_instance):
        """_handle_message dispatches 'log' to _log method."""
        with patch.object(gui_instance, "_log") as mock_log:
            gui_instance._handle_message("log", "test log")
            mock_log.assert_called_once_with("test log")

    def test_handle_message_progress(self, gui_instance):
        """_handle_message dispatches 'progress' to _update_progress method."""
        progress_data = {"percent": 50, "completed": 5, "total": 10}
        with patch.object(gui_instance, "_update_progress") as mock_progress:
            gui_instance._handle_message("progress", progress_data)
            mock_progress.assert_called_once_with(progress_data)

    def test_handle_message_done(self, gui_instance):
        """_handle_message dispatches 'done' to _on_download_done method."""
        with patch.object(gui_instance, "_on_download_done") as mock_done:
            gui_instance._handle_message("done", "success")
            mock_done.assert_called_once_with("success")

    def test_handle_message_log_none_data(self, gui_instance):
        """_handle_message converts None data to empty string for log."""
        with patch.object(gui_instance, "_log") as mock_log:
            gui_instance._handle_message("log", None)
            mock_log.assert_called_once_with("")


# ---------------------------------------------------------------------------
# GUI download start/stop
# ---------------------------------------------------------------------------


class TestDownloadStartStop:
    """Tests for start/stop download behavior."""

    def test_start_download_empty_url_logs_error(self, gui_instance):
        """Starting download with empty URL logs an error."""
        gui_instance._url_var.get = MagicMock(return_value="")
        with patch.object(gui_instance, "_log") as mock_log:
            gui_instance._start_download()
            mock_log.assert_called_once_with("错误：请输入 m3u8 地址")

    def test_start_download_invalid_url_logs_error(self, gui_instance):
        """Starting download with non-http URL logs an error."""
        gui_instance._url_var.get = MagicMock(return_value="not-a-url")
        with patch.object(gui_instance, "_log") as mock_log:
            gui_instance._start_download()
            mock_log.assert_called_once_with("错误：URL 必须以 http:// 或 https:// 开头")

    def test_start_download_empty_filename_logs_error(self, gui_instance):
        """Starting download with empty filename logs an error."""
        gui_instance._url_var.get = MagicMock(return_value="https://example.com/index.m3u8")
        gui_instance._filename_var.get = MagicMock(return_value="")
        with patch.object(gui_instance, "_log") as mock_log:
            gui_instance._start_download()
            mock_log.assert_called_once_with("错误：请输入文件名称")

    def test_start_download_valid_url_starts_thread(self, gui_instance):
        """Starting download with valid URL starts a download thread."""
        previous_stop_event = gui_instance._stop_flag
        previous_stop_event.set()
        gui_instance._url_var.get = MagicMock(return_value="https://example.com/index.m3u8")
        gui_instance._filename_var.get = MagicMock(return_value="video.mp4")
        gui_instance._dir_var.get = MagicMock(return_value="C:\\tmp")
        gui_instance._workers_var.get = MagicMock(return_value=8)
        gui_instance._retries_var.get = MagicMock(return_value=3)
        gui_instance._timeout_var.get = MagicMock(return_value=30)
        gui_instance._use_ffmpeg_var.get = MagicMock(return_value=True)
        gui_instance._tmpdir_var.get = MagicMock(return_value="")

        # Patch _download_worker to avoid actual download
        with patch.object(gui_instance, "_download_worker"):
            gui_instance._start_download()
            assert gui_instance._downloading is True
            assert gui_instance._download_thread is not None
            assert gui_instance._stop_flag is not previous_stop_event
            assert not gui_instance._stop_flag.is_set()
            assert previous_stop_event.is_set()

    def test_stop_download_sets_stop_flag(self, gui_instance):
        """Stopping download sets the stop flag and logs."""
        gui_instance._downloading = True
        gui_instance._active_downloader = MagicMock()
        with patch.object(gui_instance, "_log") as mock_log:
            gui_instance._stop_download()
            assert gui_instance._stop_flag.is_set()
            gui_instance._active_downloader.cancel.assert_called_once_with()
            mock_log.assert_called_once_with("正在停止下载...")

    def test_stop_download_does_nothing_when_not_downloading(self, gui_instance):
        """Stopping download when not downloading does nothing."""
        gui_instance._downloading = False
        gui_instance._stop_download()
        assert not gui_instance._stop_flag.is_set()

    def test_on_download_done_success(self, gui_instance):
        """_on_download_done with 'success' sets progress to 100."""
        with patch("tkinter.messagebox.showinfo"):
            gui_instance._on_download_done("success")
            assert gui_instance._downloading is False
            gui_instance._progress_var.set.assert_called_with(100)
            gui_instance._status_var.set.assert_called_with("下载完成")

    def test_on_download_done_stopped(self, gui_instance):
        """_on_download_done with 'stopped' updates status."""
        gui_instance._on_download_done("stopped")
        assert gui_instance._downloading is False
        gui_instance._status_var.set.assert_called_with("下载已停止")

    def test_on_download_done_error(self, gui_instance):
        """_on_download_done with 'error' updates status."""
        gui_instance._on_download_done("error")
        assert gui_instance._downloading is False
        gui_instance._status_var.set.assert_called_with("下载失败")

    def test_download_worker_uses_core_callbacks_and_records_success(self, gui_instance):
        """GUI 应复用核心下载流程，避免维护第二套解析/下载/合并实现。"""
        fake_downloader = MagicMock()
        fake_downloader.download.return_value = "D:/out/video.mp4"
        gui_instance._resolve_proxy = MagicMock(return_value=("", True))

        with patch("m3u8_downloader.gui.is_ffmpeg_available", return_value=True), patch(
            "m3u8_downloader.gui.M3U8Downloader", return_value=fake_downloader
        ) as downloader_class, patch(
            "m3u8_downloader.history.record_download"
        ) as record_download:
            gui_instance._download_worker(
                "https://x/video.m3u8", "D:/out/video.mp4", 8, 3, 30, True, ""
            )

        kwargs = downloader_class.call_args.kwargs
        assert kwargs["stop_event"] is gui_instance._stop_flag
        kwargs["log_callback"]("核心日志")
        kwargs["progress_callback"]({"percent": 50})
        messages = list(gui_instance._message_queue.queue)
        assert ("log", "核心日志") in messages
        assert ("progress", {"percent": 50}) in messages
        assert ("done", "success") in messages
        fake_downloader.download.assert_called_once_with()
        record_download.assert_called_once_with("https://x/video.m3u8")


# ---------------------------------------------------------------------------
# GUI imports and module structure
# ---------------------------------------------------------------------------


class TestGUIModuleStructure:
    """Tests for module-level structure and imports."""

    def test_gui_module_imports_m3u8_downloader(self):
        """gui.py imports from m3u8_downloader.downloader."""
        import m3u8_downloader.gui as gui_mod
        assert hasattr(gui_mod, "M3U8DownloaderGUI")
        assert hasattr(gui_mod, "run_gui")

    def test_gui_module_has_version_import(self):
        """gui.py imports __version__ from the package."""
        from m3u8_downloader import __version__
        from m3u8_downloader.gui import M3U8DownloaderGUI
        assert __version__ is not None

    def test_gui_uses_utils_functions(self):
        """gui.py uses utility functions from utils module."""
        import m3u8_downloader.gui as gui_mod
        source = inspect.getsource(gui_mod)
        assert "format_duration" in source
        assert "format_file_size" in source
        assert "format_speed" in source
        assert "is_ffmpeg_available" in source


# ---------------------------------------------------------------------------
# __main__.py behavior
# ---------------------------------------------------------------------------


class TestMainModule:
    """Tests for __main__.py entry point."""

    def test_main_module_imports_cli(self):
        """__main__.py imports from cli module."""
        import m3u8_downloader.__main__ as main_mod
        source = inspect.getsource(main_mod)
        assert "cli" in source

    def test_main_module_gui_check(self):
        """__main__.py checks for --gui in sys.argv."""
        import m3u8_downloader.__main__ as main_mod
        source = inspect.getsource(main_mod)
        assert "--gui" in source

    def test_main_module_non_gui_calls_main(self):
        """__main__.py calls main() when --gui is not present."""
        import m3u8_downloader.__main__ as main_mod
        source = inspect.getsource(main_mod)
        assert "main()" in source


# ---------------------------------------------------------------------------
# Helpers and fixture: keep the real ~/.m3u8-downloader config untouched
# ---------------------------------------------------------------------------


def _write_config(config_path, payload):
    """Write raw text or a JSON-serialisable payload to the isolated config file."""
    config_path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(payload, str):
        config_path.write_text(payload, encoding="utf-8")
    else:
        config_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


@pytest.fixture
def isolated_gui(tmp_path):
    """GUI instance whose preference file is redirected into tmp_path.

    ``GUI_CONFIG_PATH`` is patched for the whole lifetime of the instance
    (including ``_build_ui`` -> ``_load_config``), so the real
    ``~/.m3u8-downloader/gui_config.json`` is never read nor written.

    Yields:
        A tuple of ``(gui, config_path)``.
    """
    config_path = tmp_path / "prefs" / "gui_config.json"
    with ExitStack() as stack:
        for p in _tk_patches():
            stack.enter_context(p)
        stack.enter_context(patch("m3u8_downloader.gui.GUI_CONFIG_PATH", config_path))
        root = _make_mock_root()
        gui = M3U8DownloaderGUI(root)
        yield gui, config_path


# ---------------------------------------------------------------------------
# "打开" button: _open_dir()
# ---------------------------------------------------------------------------


class TestOpenDir:
    """Tests for M3U8DownloaderGUI._open_dir() cross-platform behaviour."""

    def test_open_dir_empty_path_logs_hint_and_does_nothing(self, isolated_gui):
        """Empty save directory logs a hint and never launches a file manager."""
        gui, _ = isolated_gui
        gui._dir_var.get = MagicMock(return_value="   ")

        with patch("os.startfile", create=True) as mock_startfile, \
                patch("m3u8_downloader.gui.subprocess.run") as mock_run, \
                patch.object(gui, "_log") as mock_log:
            gui._open_dir()

        mock_log.assert_called_once_with("提示：保存目录为空或不存在，无法打开")
        mock_startfile.assert_not_called()
        mock_run.assert_not_called()

    def test_open_dir_missing_directory_logs_hint_and_does_nothing(self, isolated_gui, tmp_path):
        """Non-existent save directory logs a hint and never launches a file manager."""
        gui, _ = isolated_gui
        gui._dir_var.get = MagicMock(return_value=str(tmp_path / "no-such-dir"))

        with patch("os.path.isdir", return_value=False), \
                patch("os.startfile", create=True) as mock_startfile, \
                patch("m3u8_downloader.gui.subprocess.run") as mock_run, \
                patch.object(gui, "_log") as mock_log:
            gui._open_dir()

        mock_log.assert_called_once_with("提示：保存目录为空或不存在，无法打开")
        mock_startfile.assert_not_called()
        mock_run.assert_not_called()

    def test_open_dir_windows_calls_startfile(self, isolated_gui, tmp_path):
        """On win32 the directory is opened with os.startfile(path)."""
        gui, _ = isolated_gui
        target = str(tmp_path)
        gui._dir_var.get = MagicMock(return_value=target)

        with patch("sys.platform", "win32"), \
                patch("os.startfile", create=True) as mock_startfile, \
                patch("m3u8_downloader.gui.subprocess.run") as mock_run, \
                patch.object(gui, "_log") as mock_log:
            gui._open_dir()

        mock_startfile.assert_called_once_with(target)
        mock_run.assert_not_called()
        mock_log.assert_not_called()

    def test_open_dir_darwin_calls_open(self, isolated_gui, tmp_path):
        """On darwin the directory is opened with subprocess.run(["open", path])."""
        gui, _ = isolated_gui
        target = str(tmp_path)
        gui._dir_var.get = MagicMock(return_value=target)

        with patch("sys.platform", "darwin"), \
                patch("os.startfile", create=True) as mock_startfile, \
                patch("m3u8_downloader.gui.subprocess.run") as mock_run, \
                patch.object(gui, "_log") as mock_log:
            gui._open_dir()

        mock_run.assert_called_once_with(["open", target], check=False)
        mock_startfile.assert_not_called()
        mock_log.assert_not_called()

    def test_open_dir_linux_calls_xdg_open(self, isolated_gui, tmp_path):
        """On other platforms the directory is opened with xdg-open."""
        gui, _ = isolated_gui
        target = str(tmp_path)
        gui._dir_var.get = MagicMock(return_value=target)

        with patch("sys.platform", "linux"), \
                patch("os.startfile", create=True) as mock_startfile, \
                patch("m3u8_downloader.gui.subprocess.run") as mock_run, \
                patch.object(gui, "_log") as mock_log:
            gui._open_dir()

        mock_run.assert_called_once_with(["xdg-open", target], check=False)
        mock_startfile.assert_not_called()
        mock_log.assert_not_called()

    def test_open_dir_failure_is_logged_and_not_raised(self, isolated_gui, tmp_path):
        """An exception from the open action is logged, never propagated to Tk."""
        gui, _ = isolated_gui
        gui._dir_var.get = MagicMock(return_value=str(tmp_path))

        with patch("sys.platform", "win32"), \
                patch("os.startfile", create=True, side_effect=OSError("拒绝访问")), \
                patch.object(gui, "_log") as mock_log:
            gui._open_dir()  # must not raise

        mock_log.assert_called_once()
        message = mock_log.call_args[0][0]
        assert "打开目录失败" in message
        assert "拒绝访问" in message


# ---------------------------------------------------------------------------
# 记住保存位置: _load_config()
# ---------------------------------------------------------------------------


class TestLoadDirPreference:
    """Tests for M3U8DownloaderGUI._load_config() degradation paths."""

    def test_load_missing_config_degrades_gracefully(self, isolated_gui):
        """Missing config file: no directory filled, checkbox stays unchecked."""
        gui, config_path = isolated_gui
        assert not config_path.exists()

        gui._dir_var.set.reset_mock()
        gui._remember_dir_var.set.reset_mock()
        gui._load_config()  # must not raise

        gui._dir_var.set.assert_not_called()
        gui._remember_dir_var.set.assert_not_called()

    def test_load_corrupt_json_degrades_gracefully(self, isolated_gui):
        """Broken JSON content: safe degradation, no exception surfaced."""
        gui, config_path = isolated_gui
        _write_config(config_path, "{ not valid json at all ")

        gui._dir_var.set.reset_mock()
        gui._remember_dir_var.set.reset_mock()
        gui._load_config()  # must not raise

        gui._dir_var.set.assert_not_called()
        gui._remember_dir_var.set.assert_not_called()

    def test_load_non_dict_top_level_degrades_gracefully(self, isolated_gui):
        """Valid JSON that is not an object (e.g. a list): safe degradation."""
        gui, config_path = isolated_gui
        _write_config(config_path, [1, 2, 3])

        gui._dir_var.set.reset_mock()
        gui._remember_dir_var.set.reset_mock()
        gui._load_config()  # must not raise

        gui._dir_var.set.assert_not_called()
        gui._remember_dir_var.set.assert_not_called()

    def test_load_restores_valid_directory(self, isolated_gui, tmp_path):
        """remember_dir=True with an existing last_dir fills entry and checks box."""
        gui, config_path = isolated_gui
        target = tmp_path / "saved_videos"
        target.mkdir()
        _write_config(config_path, {"remember_dir": True, "last_dir": str(target)})

        gui._dir_var.set.reset_mock()
        gui._remember_dir_var.set.reset_mock()
        gui._load_config()

        gui._dir_var.set.assert_called_once_with(str(target))
        gui._remember_dir_var.set.assert_called_once_with(True)

    def test_load_skips_when_saved_directory_is_gone(self, isolated_gui, tmp_path):
        """remember_dir=True but last_dir no longer exists: degrade to unchecked."""
        gui, config_path = isolated_gui
        _write_config(
            config_path,
            {"remember_dir": True, "last_dir": str(tmp_path / "deleted")},
        )

        gui._dir_var.set.reset_mock()
        gui._remember_dir_var.set.reset_mock()
        gui._load_config()

        gui._dir_var.set.assert_not_called()
        gui._remember_dir_var.set.assert_called_once_with(False)

    def test_load_ignores_when_remember_dir_false(self, isolated_gui, tmp_path):
        """remember_dir=False never fills the directory even if last_dir is valid."""
        gui, config_path = isolated_gui
        target = tmp_path / "saved_videos"
        target.mkdir()
        _write_config(config_path, {"remember_dir": False, "last_dir": str(target)})

        gui._dir_var.set.reset_mock()
        gui._remember_dir_var.set.reset_mock()
        gui._load_config()

        gui._dir_var.set.assert_not_called()
        gui._remember_dir_var.set.assert_called_once_with(False)

    def test_load_ignores_empty_last_dir(self, isolated_gui):
        """remember_dir=True with an empty last_dir: degrade to unchecked."""
        gui, config_path = isolated_gui
        _write_config(config_path, {"remember_dir": True, "last_dir": ""})

        gui._dir_var.set.reset_mock()
        gui._remember_dir_var.set.reset_mock()
        gui._load_config()

        gui._dir_var.set.assert_not_called()
        gui._remember_dir_var.set.assert_called_once_with(False)


# ---------------------------------------------------------------------------
# 记住保存位置: _save_config()
# ---------------------------------------------------------------------------


class TestSaveDirPreference:
    """Tests for M3U8DownloaderGUI._save_config() persistence."""

    def test_save_checked_writes_dir_preference(self, isolated_gui, tmp_path):
        """Checked: writes remember_dir=True together with the current directory."""
        gui, config_path = isolated_gui
        target = str(tmp_path / "videos")
        gui._remember_dir_var.get = MagicMock(return_value=True)
        gui._dir_var.get = MagicMock(return_value=target)

        gui._save_config()

        assert config_path.exists()
        data = json.loads(config_path.read_text(encoding="utf-8"))
        assert data["remember_dir"] is True
        assert data["last_dir"] == target

    def test_save_creates_parent_directory(self, isolated_gui):
        """The parent config directory is created when it does not exist yet."""
        gui, config_path = isolated_gui
        assert not config_path.parent.exists()
        gui._remember_dir_var.get = MagicMock(return_value=True)
        gui._dir_var.get = MagicMock(return_value="D:\\downloads")

        gui._save_config()

        assert config_path.parent.is_dir()
        assert config_path.exists()

    def test_save_unchecked_clears_last_dir(self, isolated_gui, tmp_path):
        """Unchecked: writes remember_dir=False and clears last_dir."""
        gui, config_path = isolated_gui
        _write_config(config_path, {"remember_dir": True, "last_dir": "C:\\old"})
        gui._remember_dir_var.get = MagicMock(return_value=False)
        gui._dir_var.get = MagicMock(return_value=str(tmp_path / "videos"))

        gui._save_config()

        data = json.loads(config_path.read_text(encoding="utf-8"))
        assert data["remember_dir"] is False
        assert data["last_dir"] == ""

    def test_save_swallows_write_error(self, isolated_gui):
        """An OSError while writing is logged only, never propagated to Tk."""
        gui, _ = isolated_gui
        gui._remember_dir_var.get = MagicMock(return_value=True)
        gui._dir_var.get = MagicMock(return_value="D:\\downloads")

        with patch("builtins.open", side_effect=OSError("磁盘不可写")):
            with patch.object(gui, "_log") as mock_log:
                gui._save_config()  # must not raise

        mock_log.assert_called_once()
        assert "写入失败" in mock_log.call_args[0][0]

    def test_save_then_load_round_trip(self, isolated_gui, tmp_path):
        """A directory saved on one run is restored on the next run."""
        gui, config_path = isolated_gui
        target = tmp_path / "round_trip"
        target.mkdir()
        gui._remember_dir_var.get = MagicMock(return_value=True)
        gui._dir_var.get = MagicMock(return_value=str(target))

        gui._save_config()

        gui._dir_var.set.reset_mock()
        gui._remember_dir_var.set.reset_mock()
        gui._load_config()

        gui._dir_var.set.assert_called_once_with(str(target))
        gui._remember_dir_var.set.assert_called_once_with(True)


# ---------------------------------------------------------------------------
# "记住保存位置" wiring: _browse_dir sync and _build_ui hooks
# ---------------------------------------------------------------------------


class TestDirPreferenceWiring:
    """Tests that the new preference feature is wired into the existing UI flow."""

    def test_browse_dir_persists_when_remember_checked(self, isolated_gui, tmp_path):
        """Picking a directory while checked persists it immediately."""
        gui, config_path = isolated_gui
        chosen = str(tmp_path / "chosen")
        gui._remember_dir_var.get = MagicMock(return_value=True)
        gui._dir_var.get = MagicMock(return_value=chosen)

        with patch(
            "m3u8_downloader.gui.filedialog.askdirectory", return_value=chosen
        ) as mock_ask:
            gui._browse_dir()

        mock_ask.assert_called_once()
        gui._dir_var.set.assert_any_call(chosen)
        data = json.loads(config_path.read_text(encoding="utf-8"))
        assert data["remember_dir"] is True
        assert data["last_dir"] == chosen

    def test_browse_dir_does_not_persist_when_unchecked(self, isolated_gui, tmp_path):
        """Picking a directory while unchecked must not create a config file."""
        gui, config_path = isolated_gui
        chosen = str(tmp_path / "chosen")
        gui._remember_dir_var.get = MagicMock(return_value=False)
        gui._dir_var.get = MagicMock(return_value=chosen)

        with patch("m3u8_downloader.gui.filedialog.askdirectory", return_value=chosen):
            gui._browse_dir()

        gui._dir_var.set.assert_any_call(chosen)
        assert not config_path.exists()

    def test_browse_dir_cancelled_does_not_persist(self, isolated_gui):
        """Cancelling the directory dialog keeps the preference file untouched."""
        gui, config_path = isolated_gui
        gui._remember_dir_var.get = MagicMock(return_value=True)

        with patch("m3u8_downloader.gui.filedialog.askdirectory", return_value=""):
            gui._browse_dir()

        assert not config_path.exists()

    def test_build_ui_loads_dir_preference_at_startup(self, tmp_path):
        """_build_ui restores the saved preference as its last step."""
        with ExitStack() as stack:
            for p in _tk_patches():
                stack.enter_context(p)
            stack.enter_context(
                patch(
                    "m3u8_downloader.gui.GUI_CONFIG_PATH", tmp_path / "gui_config.json"
                )
            )
            with patch.object(M3U8DownloaderGUI, "_load_config") as mock_load:
                M3U8DownloaderGUI(_make_mock_root())

        mock_load.assert_called_once()

    def test_build_ui_wires_open_button(self, tmp_path):
        """The 打开 button is created and bound to _open_dir()."""
        with ExitStack() as stack:
            for p in _tk_patches():
                stack.enter_context(p)
            button_cls = stack.enter_context(
                patch("tkinter.ttk.Button", return_value=MagicMock())
            )
            stack.enter_context(
                patch(
                    "m3u8_downloader.gui.GUI_CONFIG_PATH", tmp_path / "gui_config.json"
                )
            )
            gui = M3U8DownloaderGUI(_make_mock_root())

        open_calls = [
            c for c in button_cls.call_args_list if c.kwargs.get("text") == "打开"
        ]
        assert len(open_calls) == 1, "未找到“打开”按钮"
        assert open_calls[0].kwargs["command"] == gui._open_dir

    def test_build_ui_wires_remember_dir_checkbutton(self, tmp_path):
        """The 记住保存位置 checkbox is bound to the preference var and callback."""
        with ExitStack() as stack:
            for p in _tk_patches():
                stack.enter_context(p)
            check_cls = stack.enter_context(
                patch("tkinter.ttk.Checkbutton", return_value=MagicMock())
            )
            stack.enter_context(
                patch(
                    "m3u8_downloader.gui.GUI_CONFIG_PATH", tmp_path / "gui_config.json"
                )
            )
            gui = M3U8DownloaderGUI(_make_mock_root())

        remember_calls = [
            c for c in check_cls.call_args_list
            if "记住保存位置" in str(c.kwargs.get("text", ""))
        ]
        assert len(remember_calls) == 1, "未找到“记住保存位置”勾选框"
        assert remember_calls[0].kwargs["command"] == gui._save_config
        assert remember_calls[0].kwargs["variable"] is gui._remember_dir_var

    def test_remember_dir_var_defaults_to_false(self, isolated_gui):
        """The preference checkbox starts unchecked by default."""
        gui, _ = isolated_gui
        assert gui._remember_dir_var is not None
        assert gui._remember_dir_var.get() in (False, 0)

    def test_gui_config_path_is_a_path_object(self):
        """GUI_CONFIG_PATH is exposed as a pathlib.Path pointing at the JSON file."""
        from m3u8_downloader.gui import GUI_CONFIG_PATH
        assert isinstance(GUI_CONFIG_PATH, os.PathLike)
        assert GUI_CONFIG_PATH.name == "gui_config.json"
        assert str(GUI_CONFIG_PATH).endswith("gui_config.json")


# ---------------------------------------------------------------------------
# 网页抽取与多选下载（T05）
# ---------------------------------------------------------------------------

import tkinter as tk  # noqa: E402  (tk.NORMAL 常量用于断言)

from m3u8_downloader.extractor import Candidate  # noqa: E402


class TestWebExtractAndMultiDownload:
    """Tests for the new web-extraction Treeview and multi-select download queue."""

    def test_fill_tree_populates_and_enables_button(self, gui_instance):
        gui_instance._tree.get_children.return_value = []
        cands = [
            Candidate(url="https://x/a.m3u8", title="A"),
            Candidate(url="https://x/b.m3u8", title="B", is_master=True),
        ]
        gui_instance._fill_tree(cands)
        assert gui_instance._tree.insert.call_count == 2
        gui_instance._download_selected_btn.configure.assert_called_with(
            state=tk.NORMAL
        )

    def test_extract_worker_fills_tree_via_queue(self, gui_instance):
        cands = [Candidate(url="https://x/a.m3u8", title="A")]
        with patch(
            "m3u8_downloader.extractor.extract_m3u8_from_page_with_title",
            return_value=(cands, "标题A"),
        ):
            gui_instance._tree.get_children.return_value = []
            gui_instance._extract_worker("https://x/page", False)

        msgs = []
        while True:
            try:
                msgs.append(gui_instance._message_queue.get_nowait())
            except queue.Empty:
                break
        assert "candidate_update" in [m[0] for m in msgs]
        assert "extract_done" in [m[0] for m in msgs]
        for t, d in msgs:
            gui_instance._handle_message(t, d)
        assert gui_instance._tree.insert.call_count == 1

    def test_ordinary_extract_keeps_start_time_non_preload_behavior(self, gui_instance):
        """空闲时启动的普通提取，不应因稍后开始下载而被改判成预载。"""
        cands = [Candidate(url="https://x/new.m3u8", title="new")]
        gui_instance._downloading = True
        with patch(
            "m3u8_downloader.extractor.extract_m3u8_from_page_with_title",
            return_value=(cands, "新标题"),
        ):
            gui_instance._extract_worker("https://x/page", False, preload=False)
        messages = []
        while not gui_instance._message_queue.empty():
            messages.append(gui_instance._message_queue.get_nowait())
        assert any(kind == "candidate_update" for kind, _ in messages)
        assert not any(kind == "preloaded_extract" for kind, _ in messages)

    def test_deep_preload_does_not_pass_noop_candidate_callback(self, gui_instance):
        """预载只靠显式 preload 状态，不用空回调暗示执行模式。"""
        with patch(
            "m3u8_downloader.extractor.extract_m3u8_from_page_with_title",
            return_value=([], "下一集"),
        ) as extract:
            gui_instance._extract_worker("https://x/page", True, preload=True)
        assert "on_candidate" not in extract.call_args.kwargs

    def test_late_page_title_updates_active_and_queued_downloads(self, gui_instance):
        """深度候选先出现并开始下载后，稍后取得的完整网页标题仍应回填。"""
        page_url = "https://x/page"
        gui_instance._candidate_page_url = page_url
        gui_instance._current_source_page_url = page_url
        gui_instance._downloading = True
        gui_instance._pending_jobs = [
            DownloadJob("https://x/b.m3u8", "/tmp/b.mp4", "旧标题", page_url)
        ]

        gui_instance._handle_message(
            "page_title", PageTitleUpdate(page_url, "剧集标题 - 视频站")
        )
        gui_instance._suggest_filename("剧集标题")

        assert gui_instance._page_title == "剧集标题 - 视频站"
        gui_instance._current_title_var.set.assert_called_with("当前标题：剧集标题 - 视频站")
        assert gui_instance._pending_jobs[0].title == "剧集标题 - 视频站"

    def test_extract_worker_survives_exception(self, gui_instance):
        with patch(
            "m3u8_downloader.extractor.extract_m3u8_from_page_with_title",
            side_effect=RuntimeError("boom"),
        ):
            gui_instance._extract_worker("https://x/page", False)
        msgs = []
        while True:
            try:
                msgs.append(gui_instance._message_queue.get_nowait())
            except queue.Empty:
                break
        # 异常不应让 GUI 崩溃：仍收到 extract_done(error)
        assert ("extract_done", "error") in msgs

    def test_download_selected_builds_jobs(self, gui_instance):
        gui_instance._candidates = [
            Candidate(url="https://x/a.m3u8"),
            Candidate(url="https://x/b.m3u8"),
        ]
        gui_instance._filename_var.get.return_value = "v.mp4"
        gui_instance._page_title = "网页标题"
        gui_instance._dir_var.get.return_value = "/tmp/out"
        gui_instance._tree.get_children.return_value = []
        gui_instance._tree.selection.return_value = ["i1", "i2"]
        gui_instance._tree.item.side_effect = lambda item, *a, **k: {
            "i1": (1, "≈ 1MB", "01:00", "2 Mbps", "media", "普通", "A", "https://x/a.m3u8"),
            "i2": (2, "≈ 2MB", "02:00", "4 Mbps", "media", "普通", "B", "https://x/b.m3u8"),
        }[item]
        with patch.object(gui_instance, "_run_next_job") as rnr:
            gui_instance._download_selected()
        assert len(gui_instance._pending_jobs) == 2
        assert gui_instance._pending_jobs[0].url == "https://x/a.m3u8"
        assert gui_instance._pending_jobs[0].output_path.endswith("v_1.mp4")
        assert gui_instance._pending_jobs[0].title == "网页标题"
        assert gui_instance._pending_jobs[1].output_path.endswith("v_2.mp4")
        rnr.assert_called_once()

    def test_download_selected_creates_folder_when_enabled(self, gui_instance):
        """多选 ≥2 且勾选「创建文件夹」时，文件归拢到「提取名」文件夹内."""
        gui_instance._candidates = [
            Candidate(url="https://x/a.m3u8"),
            Candidate(url="https://x/b.m3u8"),
        ]
        gui_instance._filename_var.get.return_value = "网页标题.mp4"
        gui_instance._page_title = "网页标题 - 视频站"
        gui_instance._dir_var.get.return_value = "/tmp/out"
        gui_instance._create_folder_var.get.return_value = True
        gui_instance._tree.get_children.return_value = []
        gui_instance._tree.selection.return_value = ["i1", "i2"]
        gui_instance._tree.item.side_effect = lambda item, *a, **k: {
            "i1": (1, "≈ 1MB", "01:00", "2 Mbps", "media", "普通", "A", "https://x/a.m3u8"),
            "i2": (2, "≈ 2MB", "02:00", "4 Mbps", "media", "普通", "B", "https://x/b.m3u8"),
        }[item]
        with patch("m3u8_downloader.gui.os.makedirs") as mkdir:
            with patch.object(gui_instance, "_run_next_job") as rnr:
                gui_instance._download_selected()
        mkdir.assert_called_once()
        assert len(gui_instance._pending_jobs) == 2
        p0 = gui_instance._pending_jobs[0].output_path.replace("\\", "/")
        p1 = gui_instance._pending_jobs[1].output_path.replace("\\", "/")
        assert p0.startswith("/tmp/out/网页标题/")
        assert p0.endswith("网页标题_1.mp4")
        assert p1.endswith("网页标题_2.mp4")
        rnr.assert_called_once()

    def test_download_selected_no_folder_when_disabled_or_single(self, gui_instance):
        """未勾选「创建文件夹」时，文件直接落在保存目录（不建文件夹）."""
        gui_instance._candidates = [
            Candidate(url="https://x/a.m3u8"),
            Candidate(url="https://x/b.m3u8"),
        ]
        gui_instance._filename_var.get.return_value = "网页标题.mp4"
        gui_instance._page_title = "网页标题 - 视频站"
        gui_instance._dir_var.get.return_value = "/tmp/out"
        gui_instance._create_folder_var.get.return_value = False
        gui_instance._tree.get_children.return_value = []
        gui_instance._tree.selection.return_value = ["i1", "i2"]
        gui_instance._tree.item.side_effect = lambda item, *a, **k: {
            "i1": (1, "≈ 1MB", "01:00", "2 Mbps", "media", "普通", "A", "https://x/a.m3u8"),
            "i2": (2, "≈ 2MB", "02:00", "4 Mbps", "media", "普通", "B", "https://x/b.m3u8"),
        }[item]
        with patch("m3u8_downloader.gui.os.makedirs") as mkdir:
            with patch.object(gui_instance, "_run_next_job") as rnr:
                gui_instance._download_selected()
        mkdir.assert_not_called()
        p0 = gui_instance._pending_jobs[0].output_path.replace("\\", "/")
        assert p0.startswith("/tmp/out/") and "/网页标题/" not in p0
        assert p0.endswith("网页标题_1.mp4")

    def test_download_selected_ignores_empty_selection(self, gui_instance):
        gui_instance._tree.selection.return_value = []
        with patch.object(gui_instance, "_run_next_job") as rnr:
            gui_instance._download_selected()
        assert gui_instance._pending_jobs == []
        rnr.assert_not_called()

    def test_on_download_done_continues_queue(self, gui_instance):
        gui_instance._pending_jobs = [DownloadJob("https://x/c.m3u8", "/tmp/c.mp4", "C")]
        with patch.object(gui_instance, "_run_next_job") as rnr:
            gui_instance._on_download_done("success")
        rnr.assert_called_once()
        # 还有后续任务时不应恢复「开始下载」按钮
        gui_instance._start_btn.configure.assert_not_called()

    def test_on_download_done_restores_when_no_queue(self, gui_instance):
        gui_instance._pending_jobs = []
        gui_instance._candidates = [Candidate(url="https://x/a.m3u8")]
        gui_instance._on_download_done("success")
        gui_instance._start_btn.configure.assert_called_with(state=tk.NORMAL)
        gui_instance._download_selected_btn.configure.assert_called_with(
            state=tk.NORMAL
        )

    def test_double_click_fills_url(self, gui_instance):
        gui_instance._tree.selection.return_value = ["i1"]
        gui_instance._tree.item.return_value = (
            1, "x", "x", "x", "media", "普通", "A", "https://x/a.m3u8"
        )
        gui_instance._on_tree_double_click(None)
        gui_instance._url_var.set.assert_called_with("https://x/a.m3u8")

    def test_flush_pending_extract_displays_pending(self, gui_instance):
        """下载完成后应显示挂起的预加载提取结果（候选 + 标题自动命名）."""
        cands = [Candidate(url="https://x/b.m3u8")]
        gui_instance._pending_extract = [
            PreloadResult(cands, "预加载标题", "预加载标题 - 完整", "https://x/next")
        ]
        gui_instance._candidates = []
        gui_instance._tree.get_children.return_value = []
        result = gui_instance._flush_pending_extract()
        assert result is True, "有预填标题时应返回 True"
        assert gui_instance._pending_extract == []
        assert gui_instance._tree.insert.called, "应填充候选列表"
        gui_instance._filename_var.set.assert_called_with("预加载标题")

    def test_flush_pending_extract_empty_returns_false(self, gui_instance):
        """无挂起预加载结果时返回 False，不触碰文件名栏."""
        gui_instance._pending_extract = []
        result = gui_instance._flush_pending_extract()
        assert result is False

    def test_on_download_done_clears_filename_when_no_prefill(self, gui_instance):
        """无预填时，下载完成后应清空文件名称栏."""
        gui_instance._pending_jobs = []
        gui_instance._pending_extract = []
        gui_instance._candidates = []
        gui_instance._filename_var.get.return_value = "旧文件名.mp4"
        gui_instance._on_download_done("success")
        gui_instance._filename_var.set.assert_called_with("")

    def test_on_download_done_keeps_prefill_title(self, gui_instance):
        """有预填标题时，下载完成后应保留标题（不清空）."""
        cands = [Candidate(url="https://x/b.m3u8")]
        gui_instance._pending_jobs = []
        gui_instance._pending_extract = [
            PreloadResult(
                cands, "预加载标题", "完整标题", "https://x/next", PreloadState.SUCCESS
            )
        ]
        gui_instance._candidates = []
        gui_instance._tree.get_children.return_value = []
        gui_instance._on_download_done("success")
        # 预填标题应被填入
        gui_instance._filename_var.set.assert_any_call("预加载标题")
        # 不应被清空为空串
        set_calls = [c.args[0] for c in gui_instance._filename_var.set.call_args_list]
        assert "" not in set_calls

    def test_download_selected_enables_stop_button(self, gui_instance):
        """「下载选中」启动串行下载后，停止按钮应可用."""
        gui_instance._candidates = [
            Candidate(url="https://x/a.m3u8"),
        ]
        gui_instance._filename_var.get.return_value = "v.mp4"
        gui_instance._dir_var.get.return_value = "/tmp/out"
        gui_instance._tree.selection.return_value = ["i1"]
        gui_instance._tree.item.return_value = (
            1, "≈ 1MB", "01:00", "2 Mbps", "media", "普通", "A", "https://x/a.m3u8"
        )
        with patch.object(gui_instance, "_run_next_job"):
            gui_instance._download_selected()
        gui_instance._stop_btn.configure.assert_any_call(state=tk.NORMAL)

    def test_resolve_output_path_collision_auto_rename(self, gui_instance, tmp_path):
        """同名文件且选「否（自动改名）」→ 返回不冲突的新路径."""
        import os as _os
        existing = tmp_path / "foo.mp4"
        existing.write_bytes(b"x")
        with patch("m3u8_downloader.gui.messagebox.askyesnocancel", return_value=False):
            result = gui_instance._resolve_output_path_collision(str(existing))
        assert result is not None
        assert result != str(existing)
        assert not _os.path.exists(result)
        assert result.endswith(".mp4")

    def test_tree_toggle_select_adds_when_not_selected(self, gui_instance):
        """未选中的行 → 单击后应被选中."""
        gui_instance._tree.selection.return_value = []
        gui_instance._tree_toggle_select("i1")
        gui_instance._tree.selection_add.assert_called_with("i1")
        gui_instance._tree.selection_remove.assert_not_called()

    def test_tree_toggle_select_removes_when_selected(self, gui_instance):
        """已选中的行 → 单击后应被取消选中."""
        gui_instance._tree.selection.return_value = ["i1", "i2"]
        gui_instance._tree_toggle_select("i1")
        gui_instance._tree.selection_remove.assert_called_with("i1")
        gui_instance._tree.selection_add.assert_not_called()

    def test_tree_single_click_schedules_toggle(self, gui_instance):
        """无修饰键单击 → 应延迟调度 toggle，且不返回 None（即 break 阻止默认行为）."""
        gui_instance._root.after.reset_mock()
        evt = MagicMock()
        evt.state = 0
        evt.y = 100
        gui_instance._tree.identify_row.return_value = "i1"
        gui_instance._root.after.return_value = "after_id"
        result = gui_instance._on_tree_single_click(evt)
        gui_instance._root.after.assert_called_once()
        assert result == "break"

    def test_tree_single_click_ctrl_delegates_to_default(self, gui_instance):
        """Ctrl/Shift 修饰键单击 → 应放行给 Treeview 默认行为（返回 None）."""
        gui_instance._root.after.reset_mock()
        evt = MagicMock()
        evt.state = 0x0004  # Ctrl
        evt.y = 100
        result = gui_instance._on_tree_single_click(evt)
        assert result is None
        gui_instance._root.after.assert_not_called()

    def test_tree_single_click_empty_row_no_toggle(self, gui_instance):
        """点击空白行（无 row）→ 不调度 toggle."""
        gui_instance._root.after.reset_mock()
        evt = MagicMock()
        evt.state = 0
        evt.y = 100
        gui_instance._tree.identify_row.return_value = ""
        gui_instance._on_tree_single_click(evt)
        gui_instance._root.after.assert_not_called()
