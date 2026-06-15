"""Tests for m3u8_downloader.parser module."""

import pytest

from m3u8_downloader.parser import (
    M3U8Key,
    M3U8Segment,
    M3U8Stream,
    M3U8Playlist,
    M3U8Parser,
    select_best_stream,
)


# ---------------------------------------------------------------------------
# M3U8Parser – compute_base_url
# ---------------------------------------------------------------------------

class TestComputeBaseUrl:
    """Tests for M3U8Parser._compute_base_url."""

    def test_standard_url(self):
        url = "https://example.com/video/index.m3u8"
        result = M3U8Parser._compute_base_url(url)
        assert result == "https://example.com/video/"

    def test_root_path(self):
        url = "https://example.com/index.m3u8"
        result = M3U8Parser._compute_base_url(url)
        assert result == "https://example.com/"

    def test_nested_path(self):
        url = "https://cdn.example.com/a/b/c/playlist.m3u8"
        result = M3U8Parser._compute_base_url(url)
        assert result == "https://cdn.example.com/a/b/c/"

    def test_empty_url(self):
        result = M3U8Parser._compute_base_url("")
        assert result == ""

    def test_no_path(self):
        url = "https://example.com"
        result = M3U8Parser._compute_base_url(url)
        # path is empty string, "/" not in "", so base_path = "/"
        assert result == "https://example.com/"


# ---------------------------------------------------------------------------
# M3U8Parser – resolve_url
# ---------------------------------------------------------------------------

class TestResolveUrl:
    """Tests for M3U8Parser._resolve_url."""

    def test_absolute_url_unchanged(self):
        parser = M3U8Parser("#EXTM3U", "https://example.com/video/index.m3u8")
        result = parser._resolve_url("https://other.com/segment.ts")
        assert result == "https://other.com/segment.ts"

    def test_relative_url_resolved(self):
        parser = M3U8Parser("#EXTM3U", "https://example.com/video/index.m3u8")
        result = parser._resolve_url("seg001.ts")
        assert result == "https://example.com/video/seg001.ts"

    def test_relative_url_with_subdir(self):
        parser = M3U8Parser("#EXTM3U", "https://example.com/video/index.m3u8")
        result = parser._resolve_url("hd/seg001.ts")
        assert result == "https://example.com/video/hd/seg001.ts"

    def test_empty_url(self):
        parser = M3U8Parser("#EXTM3U", "https://example.com/video/index.m3u8")
        result = parser._resolve_url("")
        assert result == ""

    def test_no_base_url(self):
        parser = M3U8Parser("#EXTM3U", "")
        result = parser._resolve_url("seg.ts")
        # No base URL, no m3u8 URL => return as-is
        assert result == "seg.ts"

    def test_http_absolute_url(self):
        parser = M3U8Parser("#EXTM3U", "https://example.com/video/index.m3u8")
        result = parser._resolve_url("http://cdn.com/seg.ts")
        assert result == "http://cdn.com/seg.ts"


# ---------------------------------------------------------------------------
# M3U8Parser – parse_hex_iv
# ---------------------------------------------------------------------------

class TestParseHexIv:
    """Tests for M3U8Parser._parse_hex_iv."""

    def test_standard_16_byte_iv(self):
        iv = M3U8Parser._parse_hex_iv("0x1234567890abcdef1234567890abcdef")
        assert iv is not None
        assert len(iv) == 16
        assert iv == bytes.fromhex("1234567890abcdef1234567890abcdef")

    def test_short_iv_padded(self):
        iv = M3U8Parser._parse_hex_iv("0xff")
        assert iv is not None
        assert len(iv) == 16
        assert iv == b"\x00" * 15 + b"\xff"

    def test_0x_prefix_case_insensitive(self):
        iv = M3U8Parser._parse_hex_iv("0XABCD")
        assert iv is not None
        assert len(iv) == 16

    def test_empty_string(self):
        result = M3U8Parser._parse_hex_iv("")
        assert result is None

    def test_none_like_empty(self):
        result = M3U8Parser._parse_hex_iv("")
        assert result is None

    def test_invalid_hex(self):
        result = M3U8Parser._parse_hex_iv("0xZZZZ")
        assert result is None


# ---------------------------------------------------------------------------
# M3U8Parser – parse (media playlist)
# ---------------------------------------------------------------------------

class TestParseMediaPlaylist:
    """Tests for M3U8Parser.parse with media playlists."""

    SAMPLE_MEDIA_PLAYLIST = """#EXTM3U
#EXT-X-VERSION:3
#EXT-X-TARGETDURATION:10
#EXT-X-MEDIA-SEQUENCE:0
#EXTINF:9.9,
seg001.ts
#EXTINF:9.9,
seg002.ts
#EXTINF:5.1,
seg003.ts
#EXT-X-ENDLIST"""

    def test_parse_media_playlist_basic(self):
        parser = M3U8Parser(self.SAMPLE_MEDIA_PLAYLIST, "https://example.com/video/index.m3u8")
        playlist = parser.parse()

        assert playlist.is_master is False
        assert len(playlist.segments) == 3
        assert playlist.target_duration == 10.0

    def test_segment_urls_resolved(self):
        parser = M3U8Parser(self.SAMPLE_MEDIA_PLAYLIST, "https://example.com/video/index.m3u8")
        playlist = parser.parse()

        assert playlist.segments[0].url == "https://example.com/video/seg001.ts"
        assert playlist.segments[1].url == "https://example.com/video/seg002.ts"
        assert playlist.segments[2].url == "https://example.com/video/seg003.ts"

    def test_segment_durations(self):
        parser = M3U8Parser(self.SAMPLE_MEDIA_PLAYLIST, "https://example.com/video/index.m3u8")
        playlist = parser.parse()

        assert playlist.segments[0].duration == 9.9
        assert playlist.segments[1].duration == 9.9
        assert playlist.segments[2].duration == 5.1

    def test_total_duration(self):
        parser = M3U8Parser(self.SAMPLE_MEDIA_PLAYLIST, "https://example.com/video/index.m3u8")
        playlist = parser.parse()

        assert playlist.total_duration == pytest.approx(24.9)

    def test_segment_sequence_numbers(self):
        parser = M3U8Parser(self.SAMPLE_MEDIA_PLAYLIST, "https://example.com/video/index.m3u8")
        playlist = parser.parse()

        assert playlist.segments[0].sequence == 0
        assert playlist.segments[1].sequence == 1
        assert playlist.segments[2].sequence == 2

    def test_media_sequence_offset(self):
        content = """#EXTM3U
#EXT-X-VERSION:3
#EXT-X-TARGETDURATION:10
#EXT-X-MEDIA-SEQUENCE:5
#EXTINF:10.0,
seg005.ts
#EXTINF:10.0,
seg006.ts
#EXT-X-ENDLIST"""
        parser = M3U8Parser(content, "https://example.com/video/index.m3u8")
        playlist = parser.parse()

        assert playlist.segments[0].sequence == 5
        assert playlist.segments[1].sequence == 6

    def test_no_encryption_by_default(self):
        parser = M3U8Parser(self.SAMPLE_MEDIA_PLAYLIST, "https://example.com/video/index.m3u8")
        playlist = parser.parse()

        assert playlist.has_encryption is False
        assert playlist.segments[0].key is None

    def test_base_url_set(self):
        parser = M3U8Parser(self.SAMPLE_MEDIA_PLAYLIST, "https://example.com/video/index.m3u8")
        playlist = parser.parse()

        assert playlist.base_url == "https://example.com/video/"

    def test_invalid_content_no_extm3u(self):
        parser = M3U8Parser("This is not m3u8", "https://example.com/video/index.m3u8")
        playlist = parser.parse()

        assert playlist.is_master is False
        assert len(playlist.segments) == 0
        assert len(playlist.streams) == 0


# ---------------------------------------------------------------------------
# M3U8Parser – parse (encrypted media playlist)
# ---------------------------------------------------------------------------

class TestParseEncryptedPlaylist:
    """Tests for parsing AES-128 encrypted media playlists."""

    SAMPLE_ENCRYPTED_PLAYLIST = """#EXTM3U
#EXT-X-VERSION:3
#EXT-X-TARGETDURATION:10
#EXT-X-MEDIA-SEQUENCE:0
#EXT-X-KEY:METHOD=AES-128,URI="https://example.com/key.php",IV=0x1234567890abcdef1234567890abcdef
#EXTINF:10.0,
seg001.ts
#EXTINF:10.0,
seg002.ts
#EXT-X-ENDLIST"""

    def test_encryption_detected(self):
        parser = M3U8Parser(self.SAMPLE_ENCRYPTED_PLAYLIST, "https://example.com/video/index.m3u8")
        playlist = parser.parse()

        assert playlist.has_encryption is True

    def test_key_method(self):
        parser = M3U8Parser(self.SAMPLE_ENCRYPTED_PLAYLIST, "https://example.com/video/index.m3u8")
        playlist = parser.parse()

        assert playlist.segments[0].key is not None
        assert playlist.segments[0].key.method == "AES-128"

    def test_key_uri(self):
        parser = M3U8Parser(self.SAMPLE_ENCRYPTED_PLAYLIST, "https://example.com/video/index.m3u8")
        playlist = parser.parse()

        assert playlist.segments[0].key.uri == "https://example.com/key.php"

    def test_key_iv(self):
        parser = M3U8Parser(self.SAMPLE_ENCRYPTED_PLAYLIST, "https://example.com/video/index.m3u8")
        playlist = parser.parse()

        assert playlist.segments[0].key.iv is not None
        assert len(playlist.segments[0].key.iv) == 16
        assert playlist.segments[0].key.iv == bytes.fromhex("1234567890abcdef1234567890abcdef")

    def test_key_applied_to_subsequent_segments(self):
        parser = M3U8Parser(self.SAMPLE_ENCRYPTED_PLAYLIST, "https://example.com/video/index.m3u8")
        playlist = parser.parse()

        # Both segments should have the same key
        assert playlist.segments[1].key is not None
        assert playlist.segments[1].key.method == "AES-128"

    def test_key_uri_relative_resolved(self):
        content = """#EXTM3U
#EXT-X-VERSION:3
#EXT-X-TARGETDURATION:10
#EXT-X-KEY:METHOD=AES-128,URI="encryption.key"
#EXTINF:10.0,
seg001.ts
#EXT-X-ENDLIST"""
        parser = M3U8Parser(content, "https://example.com/video/index.m3u8")
        playlist = parser.parse()

        assert playlist.segments[0].key.uri == "https://example.com/video/encryption.key"

    def test_key_method_none_means_no_key(self):
        content = """#EXTM3U
#EXT-X-VERSION:3
#EXT-X-TARGETDURATION:10
#EXT-X-KEY:METHOD=NONE
#EXTINF:10.0,
seg001.ts
#EXT-X-ENDLIST"""
        parser = M3U8Parser(content, "https://example.com/video/index.m3u8")
        playlist = parser.parse()

        assert playlist.has_encryption is False
        # Key with method=NONE should not be attached to segments
        assert playlist.segments[0].key is None


# ---------------------------------------------------------------------------
# M3U8Parser – parse (master playlist)
# ---------------------------------------------------------------------------

class TestParseMasterPlaylist:
    """Tests for parsing master playlists with multiple streams."""

    SAMPLE_MASTER_PLAYLIST = """#EXTM3U
#EXT-X-STREAM-INF:BANDWIDTH=800000,RESOLUTION=640x360
360p/index.m3u8
#EXT-X-STREAM-INF:BANDWIDTH=1400000,RESOLUTION=842x480
480p/index.m3u8
#EXT-X-STREAM-INF:BANDWIDTH=2800000,RESOLUTION=1280x720
720p/index.m3u8
#EXT-X-STREAM-INF:BANDWIDTH=5000000,RESOLUTION=1920x1080,NAME="1080p"
1080p/index.m3u8"""

    def test_master_playlist_detected(self):
        parser = M3U8Parser(self.SAMPLE_MASTER_PLAYLIST, "https://example.com/video/master.m3u8")
        playlist = parser.parse()

        assert playlist.is_master is True

    def test_streams_parsed(self):
        parser = M3U8Parser(self.SAMPLE_MASTER_PLAYLIST, "https://example.com/video/master.m3u8")
        playlist = parser.parse()

        assert len(playlist.streams) == 4

    def test_streams_sorted_by_bandwidth_desc(self):
        parser = M3U8Parser(self.SAMPLE_MASTER_PLAYLIST, "https://example.com/video/master.m3u8")
        playlist = parser.parse()

        bandwidths = [s.bandwidth for s in playlist.streams]
        assert bandwidths == sorted(bandwidths, reverse=True)

    def test_stream_bandwidth(self):
        parser = M3U8Parser(self.SAMPLE_MASTER_PLAYLIST, "https://example.com/video/master.m3u8")
        playlist = parser.parse()

        assert playlist.streams[0].bandwidth == 5000000  # Highest first

    def test_stream_resolution(self):
        parser = M3U8Parser(self.SAMPLE_MASTER_PLAYLIST, "https://example.com/video/master.m3u8")
        playlist = parser.parse()

        assert playlist.streams[0].resolution == "1920x1080"

    def test_stream_name(self):
        parser = M3U8Parser(self.SAMPLE_MASTER_PLAYLIST, "https://example.com/video/master.m3u8")
        playlist = parser.parse()

        assert playlist.streams[0].name == "1080p"

    def test_stream_urls_resolved(self):
        parser = M3U8Parser(self.SAMPLE_MASTER_PLAYLIST, "https://example.com/video/master.m3u8")
        playlist = parser.parse()

        assert playlist.streams[0].url == "https://example.com/video/1080p/index.m3u8"

    def test_no_segments_in_master(self):
        parser = M3U8Parser(self.SAMPLE_MASTER_PLAYLIST, "https://example.com/video/master.m3u8")
        playlist = parser.parse()

        assert len(playlist.segments) == 0


# ---------------------------------------------------------------------------
# select_best_stream
# ---------------------------------------------------------------------------

class TestSelectBestStream:
    """Tests for select_best_stream function."""

    def test_selects_highest_bandwidth(self):
        playlist = M3U8Playlist(is_master=True)
        playlist.streams = [
            M3U8Stream(bandwidth=5000000, resolution="1920x1080", url="url_1080"),
            M3U8Stream(bandwidth=2800000, resolution="1280x720", url="url_720"),
            M3U8Stream(bandwidth=800000, resolution="640x360", url="url_360"),
        ]
        best = select_best_stream(playlist)
        assert best.bandwidth == 5000000

    def test_raises_for_non_master(self):
        playlist = M3U8Playlist(is_master=False)
        with pytest.raises(ValueError, match="不是 master playlist"):
            select_best_stream(playlist)

    def test_raises_for_empty_streams(self):
        playlist = M3U8Playlist(is_master=True)
        with pytest.raises(ValueError, match="没有可用的流"):
            select_best_stream(playlist)


# ---------------------------------------------------------------------------
# Dataclasses – default values
# ---------------------------------------------------------------------------

class TestDataclassDefaults:
    """Test default values for dataclasses."""

    def test_m3u8_key_defaults(self):
        key = M3U8Key()
        assert key.method == "NONE"
        assert key.uri is None
        assert key.iv is None
        assert key.key is None

    def test_m3u8_segment_defaults(self):
        seg = M3U8Segment(url="http://example.com/seg.ts")
        assert seg.url == "http://example.com/seg.ts"
        assert seg.duration == 0.0
        assert seg.key is None
        assert seg.sequence == 0

    def test_m3u8_stream_defaults(self):
        stream = M3U8Stream()
        assert stream.bandwidth == 0
        assert stream.resolution is None
        assert stream.url == ""
        assert stream.name == ""

    def test_m3u8_playlist_defaults(self):
        playlist = M3U8Playlist()
        assert playlist.is_master is False
        assert playlist.streams == []
        assert playlist.segments == []
        assert playlist.target_duration == 0.0
        assert playlist.total_duration == 0.0
        assert playlist.has_encryption is False
        assert playlist.base_url == ""
