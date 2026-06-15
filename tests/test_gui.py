"""Tests for m3u8_downloader.gui module."""

import inspect
import os
import queue
import threading
from contextlib import ExitStack
from unittest.mock import MagicMock, patch

import pytest

from m3u8_downloader.gui import M3U8DownloaderGUI, run_gui


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

    def test_stop_download_sets_stop_flag(self, gui_instance):
        """Stopping download sets the stop flag and logs."""
        gui_instance._downloading = True
        with patch.object(gui_instance, "_log") as mock_log:
            gui_instance._stop_download()
            assert gui_instance._stop_flag.is_set()
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
