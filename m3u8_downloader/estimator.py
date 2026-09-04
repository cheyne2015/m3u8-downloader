"""m3u8 大小估算模块：输入 m3u8 URL，输出体积/时长/码率估计值.

设计要点（见 docs/system_design.md §3.2）：

* 本模块是**纯计算层**，不认识 ``extractor.Candidate``，只吐 :class:`SizeEstimate`；
* 本模块**从不抛出业务异常**，任何失败都写进 :attr:`SizeEstimate.error`，
  以保证并发批处理 :func:`estimate_many` 不会被单个坏链接拖垮；
* 双通道估算：
  - master playlist：``BANDWIDTH × duration ÷ 8``（取最高码率变体，与
    :func:`m3u8_downloader.parser.select_best_stream` 的实际下载行为一致）；
  - media playlist：``HEAD`` 抽样前若干片段的 ``Content-Length`` × 片段数。
"""

from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from typing import Dict, List, Optional

import requests

from m3u8_downloader.parser import M3U8Parser, M3U8Playlist
from m3u8_downloader.utils import create_http_session

# ===== 模块常量（不要在别处散落魔数） =====
DEFAULT_HEAD_SAMPLES: int = 2        # media playlist 抽样片段数（折中：2 个片段兼顾速度与精度）
MAX_ESTIMATE_WORKERS: int = 16       # 并发硬上限
DEFAULT_ESTIMATE_WORKERS: int = 8    # 默认并发数
# 估算专用超时（秒）：估算只是「参考大小」，网络慢时应快速失败显示「未知」，
# 而不是像主下载那样等满 30s 拖慢提取结果呈现。
ESTIMATE_TIMEOUT: int = 10

# 估算方法枚举值
METHOD_BANDWIDTH: str = "bandwidth"
METHOD_SEGMENT_HEAD: str = "segment_head"
METHOD_UNKNOWN: str = "unknown"


@dataclass
class SizeEstimate:
    """单个 m3u8 链接的估算结果.

    Attributes:
        size_bytes: 估计字节数，``0`` 表示未知.
        duration: 时长（秒），``0.0`` 表示未知.
        bandwidth: 码率（bits per second），``0`` 表示未知.
        is_master: 是否是 master playlist（多码率列表）.
        variant_count: master playlist 的变体（码率）数量.
        segment_count: TS 片段数量.
        method: 估算方法，取值 ``"bandwidth"`` / ``"segment_head"`` / ``"unknown"``.
        error: 非空表示估算失败的原因（本模块不抛异常）.
        is_live: 疑似直播流（media playlist 缺少 ``#EXT-X-ENDLIST``）.
    """

    size_bytes: int = 0
    duration: float = 0.0
    bandwidth: int = 0
    is_master: bool = False
    variant_count: int = 0
    segment_count: int = 0
    method: str = METHOD_UNKNOWN
    error: str = ""
    is_live: bool = False

    @property
    def ok(self) -> bool:
        """估算是否成功（无错误且体积已知）.

        Returns:
            True 表示 ``error`` 为空且 ``size_bytes > 0``.
        """
        return not self.error and self.size_bytes > 0


def _fetch_text(
    session: requests.Session,
    url: str,
    timeout: int = 30,
) -> str:
    """拉取 playlist 文本内容.

    Args:
        session: HTTP 会话.
        url: playlist URL.
        timeout: 超时秒数.

    Returns:
        响应文本（可能为空字符串）.

    Raises:
        RuntimeError: HTTP 状态码非 2xx.
    """
    response = session.get(url, timeout=timeout)
    status = int(getattr(response, "status_code", 200) or 200)
    if status < 200 or status >= 300:
        raise RuntimeError(f"HTTP {status}")
    text = getattr(response, "text", "") or ""
    return str(text)


def _is_m3u8_text(text: str) -> bool:
    """判断文本是否是 m3u8 播放列表（首个非空行为 ``#EXTM3U``）.

    Args:
        text: 待判断的文本.

    Returns:
        True 表示是合法 m3u8 文本.
    """
    if not text:
        return False
    return text.lstrip().startswith("#EXTM3U")


def _content_length_of(response: object) -> int:
    """从响应中提取 ``Content-Length``.

    Args:
        response: requests 响应对象（或具备同样接口的对象）.

    Returns:
        字节数；无法获得时返回 ``0``.
    """
    status = int(getattr(response, "status_code", 200) or 200)
    if status >= 400:
        return 0
    headers = getattr(response, "headers", None) or {}
    raw = ""
    try:
        raw = headers.get("Content-Length", "") or headers.get("content-length", "")
    except AttributeError:
        return 0
    try:
        value = int(str(raw).strip())
    except (TypeError, ValueError):
        return 0
    return value if value > 0 else 0


def _head_content_length(
    session: requests.Session,
    url: str,
    timeout: int = 30,
) -> int:
    """探测单个片段的字节数：先 ``HEAD``，失败则回退 ``GET`` 读 header.

    某些 CDN 对 ``HEAD`` 返回 405 或不带 ``Content-Length``，此时用
    ``GET stream=True`` 拿到响应头后立刻 ``close()``，避免下载整段数据。

    Args:
        session: HTTP 会话.
        url: 片段 URL.
        timeout: 超时秒数.

    Returns:
        字节数；探测失败返回 ``0``.
    """
    if not url:
        return 0

    # 1. 优先 HEAD
    try:
        response = session.head(url, timeout=timeout, allow_redirects=True)
        length = _content_length_of(response)
        if length > 0:
            return length
    except Exception:
        pass

    # 2. 回退 GET + stream=True（只读 header）
    response = None
    try:
        response = session.get(url, timeout=timeout, stream=True)
        return _content_length_of(response)
    except Exception:
        return 0
    finally:
        if response is not None:
            try:
                response.close()
            except Exception:
                pass


def _estimate_media(
    playlist: M3U8Playlist,
    session: requests.Session,
    timeout: int = 30,
    head_samples: int = DEFAULT_HEAD_SAMPLES,
    has_endlist: bool = True,
) -> SizeEstimate:
    """基于片段 ``HEAD`` 抽样估算 media playlist 体积.

    Args:
        playlist: 已解析的 media playlist.
        session: HTTP 会话.
        timeout: 超时秒数.
        head_samples: 抽样片段数量（至少 1）.
        has_endlist: playlist 是否包含 ``#EXT-X-ENDLIST``（否 = 疑似直播）.

    Returns:
        SizeEstimate（失败时 ``error`` 非空，但时长/片段数仍尽量填充）.
    """
    estimate = SizeEstimate(
        is_master=False,
        duration=float(playlist.total_duration or 0.0),
        segment_count=len(playlist.segments),
        is_live=not has_endlist,
    )

    if estimate.segment_count == 0:
        estimate.error = "playlist 中没有任何 TS 片段"
        return estimate

    sample_count = max(1, int(head_samples or 1))
    sample_urls: List[str] = [
        segment.url for segment in playlist.segments[:sample_count] if segment.url
    ]

    lengths: List[int] = []
    for sample_url in sample_urls:
        length = _head_content_length(session, sample_url, timeout)
        if length > 0:
            lengths.append(length)

    if not lengths:
        estimate.method = METHOD_UNKNOWN
        estimate.error = "片段大小探测失败"
        return estimate

    average = sum(lengths) / float(len(lengths))
    estimate.size_bytes = int(average * estimate.segment_count)
    estimate.method = METHOD_SEGMENT_HEAD
    if estimate.duration > 0:
        estimate.bandwidth = int(estimate.size_bytes * 8 / estimate.duration)
    return estimate


def _estimate_master(
    playlist: M3U8Playlist,
    session: requests.Session,
    timeout: int = 30,
    head_samples: int = DEFAULT_HEAD_SAMPLES,
) -> SizeEstimate:
    """估算 master playlist 体积（取最高码率变体，与下载器实际行为一致）.

    Args:
        playlist: 已解析的 master playlist（``streams`` 已按码率降序）.
        session: HTTP 会话.
        timeout: 超时秒数.
        head_samples: ``BANDWIDTH=0`` 退化抽样时使用的片段数.

    Returns:
        SizeEstimate（失败时 ``error`` 非空）.
    """
    estimate = SizeEstimate(is_master=True, variant_count=len(playlist.streams))

    if not playlist.streams:
        estimate.error = "master playlist 中没有可用的码率流"
        return estimate

    best = playlist.streams[0]
    estimate.bandwidth = int(best.bandwidth or 0)

    if not best.url:
        estimate.error = "master playlist 中的码率流缺少 URL"
        return estimate

    try:
        media_text = _fetch_text(session, best.url, timeout)
    except Exception as exc:
        estimate.error = f"最高码率 playlist 拉取失败: {exc}"
        return estimate

    if not _is_m3u8_text(media_text):
        estimate.error = "最高码率 playlist 不是有效的 m3u8"
        return estimate

    media_playlist = M3U8Parser(media_text, best.url).parse()
    estimate.duration = float(media_playlist.total_duration or 0.0)
    estimate.segment_count = len(media_playlist.segments)
    estimate.is_live = "#EXT-X-ENDLIST" not in media_text

    # 正常通道：BANDWIDTH × duration ÷ 8
    if estimate.bandwidth > 0 and estimate.duration > 0:
        estimate.size_bytes = int(estimate.bandwidth * estimate.duration / 8)
        estimate.method = METHOD_BANDWIDTH
        return estimate

    # 退化通道：BANDWIDTH 缺失或时长未知 → 走片段 HEAD 抽样
    sampled = _estimate_media(
        media_playlist,
        session=session,
        timeout=timeout,
        head_samples=head_samples,
        has_endlist="#EXT-X-ENDLIST" in media_text,
    )
    estimate.size_bytes = sampled.size_bytes
    estimate.method = sampled.method
    estimate.duration = sampled.duration
    estimate.segment_count = sampled.segment_count
    estimate.error = sampled.error
    if estimate.bandwidth <= 0:
        estimate.bandwidth = sampled.bandwidth
    return estimate


def estimate_size(
    url: str,
    session: Optional[requests.Session] = None,
    timeout: int = 30,
    head_samples: int = DEFAULT_HEAD_SAMPLES,
) -> SizeEstimate:
    """估算单个 m3u8 链接的体积/时长/码率.

    **本函数永不抛出异常**，失败原因写入返回值的 ``error`` 字段。

    Args:
        url: m3u8 链接（master 或 media playlist）.
        session: 复用的 HTTP 会话；为 None 时内部自建并在结束时关闭.
        timeout: HTTP 超时秒数.
        head_samples: media playlist 抽样片段数.

    Returns:
        SizeEstimate 估算结果.
    """
    estimate = SizeEstimate()
    if not url or not str(url).strip():
        estimate.error = "m3u8 链接为空"
        return estimate

    target_url = str(url).strip()
    own_session = session is None
    if own_session:
        session = create_http_session(timeout)

    try:
        text = _fetch_text(session, target_url, timeout)
        if not _is_m3u8_text(text):
            estimate.error = "不是有效的 m3u8"
            return estimate

        playlist = M3U8Parser(text, target_url).parse()
        if playlist.is_master:
            return _estimate_master(
                playlist, session=session, timeout=timeout, head_samples=head_samples
            )
        return _estimate_media(
            playlist,
            session=session,
            timeout=timeout,
            head_samples=head_samples,
            has_endlist="#EXT-X-ENDLIST" in text,
        )
    except Exception as exc:
        estimate.error = str(exc) or type(exc).__name__
        return estimate
    finally:
        if own_session and session is not None:
            try:
                session.close()
            except Exception:
                pass


def _safe_estimate(
    url: str,
    session: requests.Session,
    timeout: int,
    head_samples: int,
) -> SizeEstimate:
    """``estimate_size`` 的兜底包装，保证并发任务绝不向外抛异常.

    Args:
        url: m3u8 链接.
        session: HTTP 会话.
        timeout: 超时秒数.
        head_samples: 抽样片段数.

    Returns:
        SizeEstimate（异常时 ``error`` 非空）.
    """
    try:
        return estimate_size(
            url, session=session, timeout=timeout, head_samples=head_samples
        )
    except Exception as exc:  # 理论不可达，纯防御
        return SizeEstimate(error=str(exc) or type(exc).__name__)


def estimate_many(
    urls: List[str],
    session: Optional[requests.Session] = None,
    timeout: int = 30,
    max_workers: int = DEFAULT_ESTIMATE_WORKERS,
) -> Dict[str, SizeEstimate]:
    """并发估算多个 m3u8 链接.

    单个 URL 的任意异常都会被兜住并转成带 ``error`` 的 :class:`SizeEstimate`，
    **批处理整体绝不失败**。

    Args:
        urls: m3u8 链接列表（重复项自动折叠）.
        session: 复用的 HTTP 会话；为 None 时内部自建并在结束时关闭.
        timeout: HTTP 超时秒数.
        max_workers: 并发数，内部会被钳制到 ``1..MAX_ESTIMATE_WORKERS``.

    Returns:
        ``{url: SizeEstimate}``，key 为输入的原样 URL.
    """
    results: Dict[str, SizeEstimate] = {}

    unique_urls: List[str] = []
    seen = set()
    for raw_url in urls or []:
        if not raw_url:
            continue
        if raw_url in seen:
            continue
        seen.add(raw_url)
        unique_urls.append(raw_url)

    if not unique_urls:
        return results

    own_session = session is None
    if own_session:
        session = create_http_session(timeout)

    workers = max(1, min(int(max_workers or 1), MAX_ESTIMATE_WORKERS, len(unique_urls)))

    try:
        if workers == 1:
            for target_url in unique_urls:
                results[target_url] = _safe_estimate(
                    target_url, session, timeout, DEFAULT_HEAD_SAMPLES
                )
            return results

        with ThreadPoolExecutor(max_workers=workers) as executor:
            futures = {
                executor.submit(
                    _safe_estimate, target_url, session, timeout, DEFAULT_HEAD_SAMPLES
                ): target_url
                for target_url in unique_urls
            }
            for future in as_completed(futures):
                target_url = futures[future]
                try:
                    results[target_url] = future.result()
                except Exception as exc:  # 理论不可达，纯防御
                    results[target_url] = SizeEstimate(
                        error=str(exc) or type(exc).__name__
                    )
        return results
    finally:
        if own_session and session is not None:
            try:
                session.close()
            except Exception:
                pass
