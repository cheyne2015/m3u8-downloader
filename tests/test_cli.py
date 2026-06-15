"""Tests for m3u8_downloader.cli module."""

import sys
from unittest.mock import patch

import pytest

from m3u8_downloader.cli import create_parser, main


# ---------------------------------------------------------------------------
# create_parser / argument parsing
# ---------------------------------------------------------------------------

class TestCreateParser:
    """Tests for CLI argument parsing."""

    def test_url_optional_with_gui(self):
        """URL is optional when --gui is specified."""
        parser = create_parser()
        args = parser.parse_args(["--gui"])
        assert args.url is None
        assert args.gui is True

    def test_gui_flag_default(self):
        """--gui flag defaults to False."""
        parser = create_parser()
        args = parser.parse_args(["https://example.com/index.m3u8"])
        assert args.gui is False

    def test_url_positional(self):
        parser = create_parser()
        args = parser.parse_args(["https://example.com/index.m3u8"])
        assert args.url == "https://example.com/index.m3u8"

    def test_output_default(self):
        parser = create_parser()
        args = parser.parse_args(["https://example.com/index.m3u8"])
        assert args.output == "output.mp4"

    def test_output_custom(self):
        parser = create_parser()
        args = parser.parse_args(["https://example.com/index.m3u8", "-o", "video.mp4"])
        assert args.output == "video.mp4"

    def test_workers_default(self):
        parser = create_parser()
        args = parser.parse_args(["https://example.com/index.m3u8"])
        assert args.workers == 8

    def test_workers_custom(self):
        parser = create_parser()
        args = parser.parse_args(["https://example.com/index.m3u8", "-w", "16"])
        assert args.workers == 16

    def test_no_ffmpeg_flag(self):
        parser = create_parser()
        args = parser.parse_args(["https://example.com/index.m3u8", "--no-ffmpeg"])
        assert args.no_ffmpeg is True

    def test_no_ffmpeg_default(self):
        parser = create_parser()
        args = parser.parse_args(["https://example.com/index.m3u8"])
        assert args.no_ffmpeg is False

    def test_retries_default(self):
        parser = create_parser()
        args = parser.parse_args(["https://example.com/index.m3u8"])
        assert args.retries == 3

    def test_retries_custom(self):
        parser = create_parser()
        args = parser.parse_args(["https://example.com/index.m3u8", "--retries", "5"])
        assert args.retries == 5

    def test_timeout_default(self):
        parser = create_parser()
        args = parser.parse_args(["https://example.com/index.m3u8"])
        assert args.timeout == 30

    def test_timeout_custom(self):
        parser = create_parser()
        args = parser.parse_args(["https://example.com/index.m3u8", "--timeout", "60"])
        assert args.timeout == 60

    def test_tmp_dir_default(self):
        parser = create_parser()
        args = parser.parse_args(["https://example.com/index.m3u8"])
        assert args.tmp_dir == ""

    def test_tmp_dir_custom(self):
        parser = create_parser()
        args = parser.parse_args(["https://example.com/index.m3u8", "--tmp-dir", "/tmp/m3u8"])
        assert args.tmp_dir == "/tmp/m3u8"

    def test_all_options_combined(self):
        parser = create_parser()
        args = parser.parse_args([
            "https://example.com/index.m3u8",
            "-o", "my_video.mp4",
            "-w", "4",
            "--tmp-dir", "/tmp/work",
            "--no-ffmpeg",
            "--retries", "5",
            "--timeout", "60",
        ])
        assert args.url == "https://example.com/index.m3u8"
        assert args.output == "my_video.mp4"
        assert args.workers == 4
        assert args.tmp_dir == "/tmp/work"
        assert args.no_ffmpeg is True
        assert args.retries == 5
        assert args.timeout == 60


# ---------------------------------------------------------------------------
# main – validation and error handling
# ---------------------------------------------------------------------------

class TestMainValidation:
    """Tests for main() function input validation."""

    def test_invalid_url_no_http(self):
        """URL must start with http:// or https://."""
        with patch("sys.argv", ["m3u8-dl", "not-a-url"]):
            with pytest.raises(SystemExit) as exc_info:
                main()
            assert exc_info.value.code == 1

    def test_invalid_workers_zero(self):
        """Workers must be >= 1."""
        with patch("sys.argv", ["m3u8-dl", "https://example.com/index.m3u8", "-w", "0"]):
            with pytest.raises(SystemExit) as exc_info:
                main()
            assert exc_info.value.code == 1

    def test_invalid_retries_negative(self):
        """Retries must not be negative."""
        with patch("sys.argv", ["m3u8-dl", "https://example.com/index.m3u8", "--retries", "-1"]):
            with pytest.raises(SystemExit) as exc_info:
                main()
            assert exc_info.value.code == 1

    def test_version_flag(self):
        """--version should print version and exit."""
        with patch("sys.argv", ["m3u8-dl", "--version"]):
            with pytest.raises(SystemExit) as exc_info:
                main()
            assert exc_info.value.code == 0

    def test_missing_url_without_gui(self):
        """Missing URL without --gui should cause error."""
        with patch("sys.argv", ["m3u8-dl"]):
            with pytest.raises(SystemExit) as exc_info:
                main()
            assert exc_info.value.code != 0
