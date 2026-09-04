"""Tests for m3u8_downloader.utils module."""

import os

import pytest

from m3u8_downloader.utils import (
    format_file_size,
    format_duration,
    format_speed,
    ProgressBar,
    create_http_session,
    is_ffmpeg_available,
    normalize_mp4_filename,
    extract_title_segment,
    build_output_path,
    sanitize_filename_component,
    _normalize_proxy,
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

    def test_no_proxy_sets_trust_env_false(self):
        # 开启 no_proxy 时，必须关闭 trust_env 才能真正绕过环境代理
        # （proxies={"http": None} 在 requests 2.34 不生效）
        session = create_http_session(no_proxy=True)
        assert session.trust_env is False

    def test_default_trust_env_true(self):
        # 默认应保留环境代理读取
        session = create_http_session()
        assert session.trust_env is True


# ---------------------------------------------------------------------------
# is_ffmpeg_available
# ---------------------------------------------------------------------------

class TestIsFfmpegAvailable:
    """Tests for is_ffmpeg_available."""

    def test_returns_bool(self):
        result = is_ffmpeg_available()
        assert isinstance(result, bool)


# ---------------------------------------------------------------------------
# normalize_mp4_filename
# ---------------------------------------------------------------------------

class TestNormalizeMp4Filename:
    """Tests for normalize_mp4_filename (保证后缀为单一 .mp4)."""

    def test_plain_name_gets_mp4(self):
        assert normalize_mp4_filename("video") == "video.mp4"

    def test_already_mp4_unchanged(self):
        assert normalize_mp4_filename("video.mp4") == "video.mp4"

    def test_double_mp4_collapsed(self):
        # 杜绝 .mp4.mp4
        assert normalize_mp4_filename("video.mp4.mp4") == "video.mp4"

    def test_triple_mp4_collapsed(self):
        assert normalize_mp4_filename("a.b.mp4.mp4.mp4") == "a.b.mp4"

    def test_other_extension_becomes_mp4(self):
        assert normalize_mp4_filename("clip.avi") == "clip.avi.mp4"

    def test_uppercase_mp4_normalized(self):
        assert normalize_mp4_filename("VID.MP4") == "VID.mp4"

    def test_mixed_case_mp4_mp4_collapsed(self):
        assert normalize_mp4_filename("x.Mp4.MP4") == "x.mp4"

    def test_dots_in_name_preserved(self):
        # 中间的点保留，只在末尾追加一个 .mp4
        assert normalize_mp4_filename("my.clip.v2") == "my.clip.v2.mp4"

    def test_only_dot_mp4_falls_back_to_default(self):
        # 用户只填了 ".mp4" -> 主名为空，回退为 output.mp4
        assert normalize_mp4_filename(".mp4") == "output.mp4"

    def test_empty_string_falls_back_to_default(self):
        assert normalize_mp4_filename("") == "output.mp4"

    def test_chinese_name(self):
        assert normalize_mp4_filename("我的视频") == "我的视频.mp4"

    def test_full_path_normalized(self):
        assert normalize_mp4_filename("C:/Movies/clip.mkv") == os.path.join("C:/Movies", "clip.mkv.mp4")

    def test_full_path_double_mp4_collapsed(self):
        assert normalize_mp4_filename("/tmp/out.mp4.mp4") == os.path.join("/tmp", "out.mp4")

    def test_invalid_windows_chars_sanitized(self):
        # 复现真实失败：网页标题含 ASCII '|' 会导致 [Errno 22] Invalid argument
        # 注：全角 '？'(U+FF1F) 在 Windows 文件名中合法，应予以保留
        raw = "空悲切是什么梗？福利姬小鱼自慰 竟被瓜友玩原神救己不救她认出 结果竟是亲姐姐！ | 51吃瓜网"
        out = normalize_mp4_filename(raw)
        assert out == "空悲切是什么梗？福利姬小鱼自慰 竟被瓜友玩原神救己不救她认出 结果竟是亲姐姐！ _ 51吃瓜网.mp4"
        # 不得残留任何非法字符（仅 ASCII 保留集需要清洗）
        for ch in '<|>:"/\\|?*':
            assert ch not in out

    def test_invalid_chars_in_path_base_sanitized(self):
        out = normalize_mp4_filename("F:/迅雷下载/a|b?.mp4")
        assert out == os.path.join("F:/迅雷下载", "a_b_.mp4")
        for ch in '<|>:"/\\|?*':
            assert ch not in os.path.basename(out)

    def test_idempotent(self):
        # 对结果再次规范化应保持不变
        once = normalize_mp4_filename("a.mp4.mp4")
        assert normalize_mp4_filename(once) == once == "a.mp4"


# ---------------------------------------------------------------------------
# extract_title_segment
# ---------------------------------------------------------------------------

class TestExtractTitleSegment:
    """Tests for extract_title_segment (截取第一个 '-' 之前的段落)."""

    def test_split_on_dash_with_spaces(self):
        title = "仙界法务部 第55集 (2026) - 动漫 - 在线免费观看 - 冷映"
        assert extract_title_segment(title) == "仙界法务部 第55集 (2026)"

    def test_split_on_bare_dash(self):
        assert extract_title_segment("Hello - World") == "Hello"
        assert extract_title_segment("A-B-C") == "A"

    def test_no_dash_returns_whole(self):
        assert extract_title_segment("完整标题无连字符") == "完整标题无连字符"

    def test_strips_whitespace(self):
        assert extract_title_segment("  trimmed  - suffix") == "trimmed"

    def test_empty_returns_empty(self):
        assert extract_title_segment("") == ""
        assert extract_title_segment("   ") == ""


# ---------------------------------------------------------------------------
# sanitize_filename_component
# ---------------------------------------------------------------------------

class TestSanitizeFilenameComponent:
    """Tests for sanitize_filename_component (清洗 Windows 非法文件名字符)."""

    def test_invalid_chars_replaced_with_underscore(self):
        cleaned = sanitize_filename_component('a<b>c:d"e/f\\g|h?i*j')
        assert cleaned == "a_b_c_d_e_f_g_h_i_j"

    def test_no_invalid_chars_unchanged(self):
        assert sanitize_filename_component("my_clip.v2") == "my_clip.v2"

    def test_trailing_dot_and_space_stripped(self):
        assert sanitize_filename_component("name. ") == "name"
        assert sanitize_filename_component("name...") == "name"

    def test_control_chars_removed(self):
        cleaned = sanitize_filename_component("a\tb\nc")
        assert cleaned == "abc"

    def test_empty_falls_back_to_output(self):
        assert sanitize_filename_component("") == "output"
        assert sanitize_filename_component("   ") == "output"

    def test_title_with_invalid_chars(self):
        title = "title | x?y"
        cleaned = sanitize_filename_component(title)
        for ch in '<|>:"/\\|?*':
            assert ch not in cleaned


# ---------------------------------------------------------------------------
# extract_title_segment (invalid chars)
# ---------------------------------------------------------------------------

class TestExtractTitleSegmentInvalidChars:
    """extract_title_segment 应同时清洗标题中的非法文件名字符."""

    def test_title_with_pipe_sanitized(self):
        title = "full title | sub - suffix"
        assert extract_title_segment(title) == "full title _ sub"

    def test_title_with_question_sanitized(self):
        title = "what is X? why - site"
        assert extract_title_segment(title) == "what is X_ why"


# ---------------------------------------------------------------------------
# build_output_path (invalid chars in multi-target)
# ---------------------------------------------------------------------------

class TestBuildOutputPathInvalidChars:
    """多目标下载时 build_output_path 也应清洗非法字符."""

    def test_invalid_chars_sanitized_multi(self):
        out = build_output_path("a|b?.mp4", 1, 3)
        assert out == "a_b__1.mp4"
        for ch in '<|>:"/\\|?*':
            assert ch not in out

    def test_invalid_chars_sanitized_single(self):
        out = build_output_path("a|b?.mp4", 1, 1)
        assert out == "a_b_.mp4"


# ---------------------------------------------------------------------------
# _normalize_proxy
# ---------------------------------------------------------------------------

class TestNormalizeProxy:
    """Tests for _normalize_proxy (补全协议头)."""

    def test_bare_host_port_gets_http(self):
        assert _normalize_proxy("127.0.0.1:7897") == "http://127.0.0.1:7897"

    def test_socks5_preserved(self):
        assert _normalize_proxy("socks5://127.0.0.1:7897") == "socks5://127.0.0.1:7897"

    def test_http_preserved(self):
        assert _normalize_proxy("http://user:pass@host:8080") == "http://user:pass@host:8080"

    def test_empty_returns_empty(self):
        assert _normalize_proxy("") == ""
        assert _normalize_proxy("   ") == ""
