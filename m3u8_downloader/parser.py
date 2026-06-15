"""m3u8 解析器模块：解析 m3u8 播放列表、多码率选择、加密信息解析、URL 拼接."""

import re
from dataclasses import dataclass, field
from typing import List, Optional, Tuple
from urllib.parse import urljoin, urlparse


@dataclass
class M3U8Key:
    """EXT-X-KEY 加密信息."""

    method: str = "NONE"
    uri: Optional[str] = None
    iv: Optional[bytes] = None
    key: Optional[bytes] = None  # 下载后的 key 数据


@dataclass
class M3U8Segment:
    """TS 片段信息."""

    url: str
    duration: float = 0.0
    key: Optional[M3U8Key] = None
    sequence: int = 0


@dataclass
class M3U8Stream:
    """多码率流信息（master playlist 中的条目）."""

    bandwidth: int = 0
    resolution: Optional[str] = None
    url: str = ""
    name: str = ""


@dataclass
class M3U8Playlist:
    """m3u8 播放列表解析结果."""

    is_master: bool = False
    streams: List[M3U8Stream] = field(default_factory=list)
    segments: List[M3U8Segment] = field(default_factory=list)
    target_duration: float = 0.0
    total_duration: float = 0.0
    has_encryption: bool = False
    base_url: str = ""


class M3U8Parser:
    """m3u8 播放列表解析器.

    支持解析 master playlist（多码率）和 media playlist（TS 片段列表），
    支持 AES-128 加密信息解析，正确处理相对/绝对 URL。
    """

    def __init__(self, content: str, url: str = "") -> None:
        """初始化解析器.

        Args:
            content: m3u8 文件内容.
            url: m3u8 文件的 URL，用于解析相对路径.
        """
        self._content = content.strip()
        self._url = url
        self._base_url = self._compute_base_url(url)

    @staticmethod
    def _compute_base_url(url: str) -> str:
        """根据 m3u8 URL 计算 base URL（去掉最后一段路径）.

        Args:
            url: 完整的 m3u8 URL.

        Returns:
            base URL 字符串.
        """
        if not url:
            return ""
        parsed = urlparse(url)
        path = parsed.path
        if "/" in path:
            base_path = path[: path.rfind("/") + 1]
        else:
            base_path = "/"
        return f"{parsed.scheme}://{parsed.netloc}{base_path}"

    def _resolve_url(self, url: str) -> str:
        """将相对 URL 转换为绝对 URL.

        Args:
            url: 可能是相对路径的 URL.

        Returns:
            绝对 URL.
        """
        if not url:
            return ""
        # 已经是绝对 URL
        if url.startswith(("http://", "https://")):
            return url
        # 基于 base URL 拼接
        if self._base_url:
            return urljoin(self._base_url, url)
        # 基于 m3u8 URL 拼接
        if self._url:
            return urljoin(self._url, url)
        return url

    @staticmethod
    def _parse_hex_iv(iv_str: str) -> Optional[bytes]:
        """解析 IV 字符串为字节.

        Args:
            iv_str: IV 字符串，格式如 "0x1234567890abcdef1234567890abcdef".

        Returns:
            16 字节的 IV，或 None.
        """
        if not iv_str:
            return None
        iv_str = iv_str.strip()
        if iv_str.startswith("0x") or iv_str.startswith("0X"):
            iv_str = iv_str[2:]
        try:
            iv_bytes = bytes.fromhex(iv_str)
            # 补齐到 16 字节
            if len(iv_bytes) < 16:
                iv_bytes = b"\x00" * (16 - len(iv_bytes)) + iv_bytes
            return iv_bytes[:16]
        except ValueError:
            return None

    def _parse_ext_x_key(self, line: str) -> M3U8Key:
        """解析 #EXT-X-KEY 行.

        Args:
            line: #EXT-X-KEY 行内容.

        Returns:
            M3U8Key 对象.
        """
        key = M3U8Key()

        # 提取 METHOD
        method_match = re.search(r'METHOD=([A-Z0-9-]+)', line)
        if method_match:
            key.method = method_match.group(1)

        # 提取 URI
        uri_match = re.search(r'URI="([^"]+)"', line)
        if uri_match:
            key.uri = self._resolve_url(uri_match.group(1))

        # 提取 IV
        iv_match = re.search(r'IV=0x([0-9a-fA-F]+)', line, re.IGNORECASE)
        if iv_match:
            key.iv = self._parse_hex_iv(f"0x{iv_match.group(1)}")

        return key

    def _parse_stream_info(self, line: str, next_line: str) -> M3U8Stream:
        """解析 #EXT-X-STREAM-INF 行和对应的 URL.

        Args:
            line: #EXT-X-STREAM-INF 行内容.
            next_line: 下一行（URL 行）.

        Returns:
            M3U8Stream 对象.
        """
        stream = M3U8Stream()

        # 提取 BANDWIDTH
        bw_match = re.search(r'BANDWIDTH=(\d+)', line)
        if bw_match:
            stream.bandwidth = int(bw_match.group(1))

        # 提取 RESOLUTION
        res_match = re.search(r'RESOLUTION=(\d+x\d+)', line)
        if res_match:
            stream.resolution = res_match.group(1)

        # 提取 NAME
        name_match = re.search(r'NAME="([^"]*)"', line)
        if name_match:
            stream.name = name_match.group(1)

        # URL
        stream.url = self._resolve_url(next_line.strip())

        return stream

    def parse(self) -> M3U8Playlist:
        """解析 m3u8 内容.

        Returns:
            M3U8Playlist 对象.
        """
        playlist = M3U8Playlist(base_url=self._base_url)

        lines = self._content.split("\n")
        # 去除空行
        lines = [line.strip() for line in lines if line.strip()]

        # 检查是否是有效的 m3u8
        if not lines or lines[0] != "#EXTM3U":
            return playlist

        # 判断是 master playlist 还是 media playlist
        is_master = any(line.startswith("#EXT-X-STREAM-INF") for line in lines)
        playlist.is_master = is_master

        if is_master:
            self._parse_master_playlist(lines, playlist)
        else:
            self._parse_media_playlist(lines, playlist)

        return playlist

    def _parse_master_playlist(
        self, lines: List[str], playlist: M3U8Playlist
    ) -> None:
        """解析 master playlist（多码率列表）.

        Args:
            lines: m3u8 行列表.
            playlist: 待填充的 M3U8Playlist 对象.
        """
        i = 0
        while i < len(lines):
            line = lines[i]
            if line.startswith("#EXT-X-STREAM-INF"):
                # 找到下一行非注释行作为 URL
                url_line = ""
                j = i + 1
                while j < len(lines):
                    if not lines[j].startswith("#"):
                        url_line = lines[j]
                        break
                    j += 1
                if url_line:
                    stream = self._parse_stream_info(line, url_line)
                    playlist.streams.append(stream)
            i += 1

        # 按码率降序排列
        playlist.streams.sort(key=lambda s: s.bandwidth, reverse=True)

    def _parse_media_playlist(
        self, lines: List[str], playlist: M3U8Playlist
    ) -> None:
        """解析 media playlist（TS 片段列表）.

        Args:
            lines: m3u8 行列表.
            playlist: 待填充的 M3U8Playlist 对象.
        """
        current_key: Optional[M3U8Key] = None
        current_duration: float = 0.0
        media_sequence: int = 0
        total_duration: float = 0.0

        # 提取 MEDIA SEQUENCE
        for line in lines:
            seq_match = re.match(r'#EXT-X-MEDIA-SEQUENCE:(\d+)', line)
            if seq_match:
                media_sequence = int(seq_match.group(1))
                break

        segment_index = 0
        i = 0
        while i < len(lines):
            line = lines[i]

            if line.startswith("#EXT-X-TARGETDURATION"):
                td_match = re.match(r'#EXT-X-TARGETDURATION:(\d+)', line)
                if td_match:
                    playlist.target_duration = float(td_match.group(1))

            elif line.startswith("#EXT-X-KEY"):
                current_key = self._parse_ext_x_key(line)
                if current_key.method != "NONE":
                    playlist.has_encryption = True

            elif line.startswith("#EXTINF"):
                # 解析片段时长
                dur_match = re.match(r'#EXTINF:([\d.]+)', line)
                if dur_match:
                    current_duration = float(dur_match.group(1))

            elif line.startswith("#"):
                # 其他注释行，跳过
                pass

            else:
                # URL 行
                url = self._resolve_url(line)
                segment = M3U8Segment(
                    url=url,
                    duration=current_duration,
                    key=current_key if current_key and current_key.method != "NONE" else None,
                    sequence=media_sequence + segment_index,
                )
                playlist.segments.append(segment)
                total_duration += current_duration
                current_duration = 0.0
                segment_index += 1

            i += 1

        playlist.total_duration = total_duration


def select_best_stream(playlist: M3U8Playlist) -> M3U8Stream:
    """从 master playlist 中选择最高码率的流.

    Args:
        playlist: 解析后的 M3U8Playlist（master playlist）.

    Returns:
        码率最高的 M3U8Stream.

    Raises:
        ValueError: 如果 playlist 不是 master playlist 或没有可用流.
    """
    if not playlist.is_master:
        raise ValueError("不是 master playlist，无法选择流")
    if not playlist.streams:
        raise ValueError("master playlist 中没有可用的流")
    # 已按码率降序排列，返回第一个
    return playlist.streams[0]
