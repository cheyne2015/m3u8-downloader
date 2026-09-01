"""Tests for m3u8_downloader.cli module."""

import sys
from unittest.mock import MagicMock, patch

import pytest

from m3u8_downloader.cli import _parse_pick, create_parser, main
from m3u8_downloader.extractor import (
    Candidate,
    DeepModeUnavailableError,
    NoCandidateFoundError,
)


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


# ---------------------------------------------------------------------------
# 网页抽取相关参数与流程（T04）
# ---------------------------------------------------------------------------

class TestFromPageArgs:
    """Tests for the new --from-page / --deep / --pick / etc. arguments."""

    def test_from_page_flag(self):
        args = create_parser().parse_args(["https://x/page", "--from-page"])
        assert args.from_page is True
        assert args.deep is False

    def test_deep_flag_parsed(self):
        args = create_parser().parse_args(["https://x/page", "--deep"])
        assert args.deep is True
        assert args.from_page is False  # main() 内部才将其置为 True

    def test_from_page_options_combined(self):
        args = create_parser().parse_args([
            "https://x/page",
            "--from-page",
            "--pick", "1,3",
            "--list-only",
            "--no-estimate",
            "--extract-workers", "12",
        ])
        assert args.from_page is True
        assert args.pick == "1,3"
        assert args.list_only is True
        assert args.no_estimate is True
        assert args.extract_workers == 12

    def test_extract_workers_clamped_default(self):
        args = create_parser().parse_args(["https://x/page", "--from-page"])
        assert args.extract_workers == 8


class TestParsePick:
    """Tests for _parse_pick boundary handling."""

    def test_all(self):
        assert _parse_pick("all", 3) == [1, 2, 3]

    def test_list(self):
        assert _parse_pick("1,3", 3) == [1, 3]

    def test_range(self):
        assert _parse_pick("1-3", 3) == [1, 2, 3]

    def test_mixed(self):
        assert _parse_pick("1,2-3", 3) == [1, 2, 3]

    def test_dedup(self):
        assert _parse_pick("2,2,1", 3) == [1, 2]

    def test_empty_spec_falls_back_to_all(self):
        assert _parse_pick("", 3) == [1, 2, 3]

    def test_out_of_range_single_raises(self):
        with pytest.raises(ValueError):
            _parse_pick("4", 3)

    def test_out_of_range_interval_raises(self):
        with pytest.raises(ValueError):
            _parse_pick("1-5", 3)

    def test_invalid_token_raises(self):
        with pytest.raises(ValueError):
            _parse_pick("abc", 3)


class TestRunFromPage:
    """Tests for --from-page main flow with mocked extractor/downloader."""

    def _cands(self):
        return [
            Candidate(url="https://x/a.m3u8", title="A"),
            Candidate(url="https://x/b.m3u8", title="B"),
        ]

    def test_list_only_does_not_download(self, capsys):
        cands = self._cands()
        with patch(
            "m3u8_downloader.extractor.extract_m3u8_from_page", return_value=cands
        ), patch("m3u8_downloader.downloader.M3U8Downloader") as DL, patch(
            "sys.argv",
            ["m3u8-dl", "https://x/page", "--from-page", "--list-only"],
        ):
            main()  # --list-only 正常返回（退出码 0），不抛异常
        DL.assert_not_called()
        out = capsys.readouterr().out
        assert "https://x/a.m3u8" in out
        assert "2 个候选" in out

    def test_pick_downloads_selected(self, capsys):
        cands = self._cands()
        fake_dl = MagicMock()
        with patch(
            "m3u8_downloader.extractor.extract_m3u8_from_page", return_value=cands
        ), patch(
            "m3u8_downloader.downloader.M3U8Downloader", return_value=fake_dl
        ), patch(
            "m3u8_downloader.utils.is_ffmpeg_available", return_value=False
        ), patch(
            "sys.argv",
            ["m3u8-dl", "https://x/page", "--from-page", "--pick", "1,2", "-o", "v.mp4"],
        ):
            main()  # 全部成功时正常返回，不抛异常
        assert fake_dl.download.call_count == 2

    def test_non_tty_without_pick_exits_2(self):
        cands = self._cands()
        with patch(
            "m3u8_downloader.extractor.extract_m3u8_from_page", return_value=cands
        ), patch("sys.stdin.isatty", return_value=False), patch(
            "sys.argv", ["m3u8-dl", "https://x/page", "--from-page"]
        ):
            with pytest.raises(SystemExit) as ei:
                main()
        assert ei.value.code == 2

    def test_no_candidate_found_exits_1(self):
        with patch(
            "m3u8_downloader.extractor.extract_m3u8_from_page",
            side_effect=NoCandidateFoundError("无候选"),
        ), patch("sys.argv", ["m3u8-dl", "https://x/page", "--from-page"]):
            with pytest.raises(SystemExit) as ei:
                main()
        assert ei.value.code == 1

    def test_deep_unavailable_exits_1(self):
        with patch(
            "m3u8_downloader.extractor.extract_m3u8_from_page",
            side_effect=DeepModeUnavailableError("需要 playwright"),
        ), patch("sys.argv", ["m3u8-dl", "https://x/page", "--deep"]):
            with pytest.raises(SystemExit) as ei:
                main()
        assert ei.value.code == 1
