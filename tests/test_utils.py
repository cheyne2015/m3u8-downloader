"""Tests for m3u8_downloader.utils module."""

import pytest

from m3u8_downloader.utils import (
    format_file_size,
    format_duration,
    format_speed,
    ProgressBar,
    create_http_session,
    is_ffmpeg_available,
)


# ---------------------------------------------------------------------------
# format_file_size
# ---------------------------------------------------------------------------

class TestFormatFileSize:
    """Tests for format_file_size."""

    def test_bytes(self):
        assert format_file_size(0) == "0.00 B"
        assert format_file_size(512) == "512.00 B"

    def test_kilobytes(self):
        assert format_file_size(1024) == "1.00 KB"
        assert format_file_size(1536) == "1.50 KB"

    def test_megabytes(self):
        assert format_file_size(1048576) == "1.00 MB"
        assert format_file_size(1572864) == "1.50 MB"

    def test_gigabytes(self):
        assert format_file_size(1073741824) == "1.00 GB"

    def test_terabytes(self):
        assert format_file_size(1099511627776) == "1.00 TB"

    def test_negative_returns_zero(self):
        assert format_file_size(-1) == "0 B"

    def test_large_value_capped_at_tb(self):
        # Should not go beyond TB
        result = format_file_size(1099511627776 * 1000)
        assert "TB" in result

    def test_float_input(self):
        result = format_file_size(1.5 * 1024)
        assert "KB" in result


# ---------------------------------------------------------------------------
# format_duration
# ---------------------------------------------------------------------------

class TestFormatDuration:
    """Tests for format_duration."""

    def test_zero(self):
        assert format_duration(0) == "00:00"

    def test_seconds_only(self):
        assert format_duration(45) == "00:45"

    def test_minutes_and_seconds(self):
        assert format_duration(125) == "02:05"

    def test_hours_minutes_seconds(self):
        assert format_duration(3723) == "01:02:03"

    def test_large_hours(self):
        assert format_duration(36000) == "10:00:00"

    def test_negative_becomes_zero(self):
        assert format_duration(-5) == "00:00"

    def test_float_seconds(self):
        assert format_duration(90.7) == "01:30"

    def test_exact_hour(self):
        assert format_duration(3600) == "01:00:00"


# ---------------------------------------------------------------------------
# format_speed
# ---------------------------------------------------------------------------

class TestFormatSpeed:
    """Tests for format_speed."""

    def test_bytes_per_sec(self):
        result = format_speed(1024)
        assert result == "1.00 KB/s"

    def test_megabytes_per_sec(self):
        result = format_speed(1048576)
        assert result == "1.00 MB/s"

    def test_zero_speed(self):
        result = format_speed(0)
        assert result == "0.00 B/s"


# ---------------------------------------------------------------------------
# ProgressBar
# ---------------------------------------------------------------------------

class TestProgressBar:
    """Tests for ProgressBar (basic functionality, no visual output)."""

    def test_init_defaults(self):
        pb = ProgressBar(total=10, disable=True)
        assert pb._total == 10
        assert pb._count == 0

    def test_update_increments(self):
        pb = ProgressBar(total=10, disable=True)
        pb.update(3)
        assert pb._count == 3

    def test_close_no_error(self):
        pb = ProgressBar(total=10, disable=True)
        pb.close()  # Should not raise

    def test_disabled_no_output(self, capsys):
        pb = ProgressBar(total=10, disable=True)
        pb.update(5)
        pb.close()
        captured = capsys.readouterr()
        assert captured.err == ""  # No output when disabled

    def test_full_cycle(self):
        pb = ProgressBar(total=5, disable=True)
        for _ in range(5):
            pb.update(1)
        assert pb._count == 5
        pb.close()


# ---------------------------------------------------------------------------
# create_http_session
# ---------------------------------------------------------------------------

class TestCreateHttpSession:
    """Tests for create_http_session."""

    def test_returns_session(self):
        import requests
        session = create_http_session()
        assert isinstance(session, requests.Session)

    def test_default_user_agent(self):
        session = create_http_session()
        assert "Mozilla" in session.headers.get("User-Agent", "")

    def test_custom_headers(self):
        session = create_http_session(headers={"X-Custom": "test"})
        assert session.headers.get("X-Custom") == "test"

    def test_timeout_stored(self):
        session = create_http_session(timeout=60)
        assert session._default_timeout == 60


# ---------------------------------------------------------------------------
# is_ffmpeg_available
# ---------------------------------------------------------------------------

class TestIsFfmpegAvailable:
    """Tests for is_ffmpeg_available."""

    def test_returns_bool(self):
        result = is_ffmpeg_available()
        assert isinstance(result, bool)
