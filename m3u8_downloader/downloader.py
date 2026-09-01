"""核心下载逻辑模块：多线程并发下载、断点续传、HTTP 重试、加密密钥下载."""

import os
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import List, Optional, Tuple

import requests

from m3u8_downloader.parser import M3U8Playlist, M3U8Segment, M3U8Key
from m3u8_downloader.utils import (
    ProgressBar,
    create_http_session,
    format_file_size,
    format_speed,
    format_duration,
)


# 默认重试配置
DEFAULT_MAX_RETRIES = 3
DEFAULT_RETRY_DELAY = 1.0  # 秒
DEFAULT_BACKOFF_FACTOR = 2.0


def _download_with_retry(
    session: requests.Session,
    url: str,
    output_path: str,
    max_retries: int = DEFAULT_MAX_RETRIES,
    retry_delay: float = DEFAULT_RETRY_DELAY,
    backoff_factor: float = DEFAULT_BACKOFF_FACTOR,
    timeout: int = 30,
) -> Tuple[bool, int]:
    """带重试机制的文件下载.

    Args:
        session: HTTP Session.
        url: 下载 URL.
        output_path: 输出文件路径.
        max_retries: 最大重试次数.
        retry_delay: 初始重试延迟（秒）.
        backoff_factor: 退避因子.
        timeout: 请求超时（秒）.

    Returns:
        (是否成功, 文件大小字节数).
    """
    last_error: Optional[Exception] = None

    for attempt in range(max_retries):
        try:
            response = session.get(url, timeout=timeout, stream=True)
            response.raise_for_status()

            # 确保输出目录存在
            os.makedirs(os.path.dirname(output_path), exist_ok=True)

            with open(output_path, "wb") as f:
                for chunk in response.iter_content(chunk_size=8192):
                    if chunk:
                        f.write(chunk)

            file_size = os.path.getsize(output_path)
            return True, file_size

        except (requests.RequestException, OSError) as e:
            last_error = e
            if attempt < max_retries - 1:
                delay = retry_delay * (backoff_factor ** attempt)
                time.sleep(delay)

    # 所有重试都失败
    return False, 0


def _download_key(
    session: requests.Session,
    key: M3U8Key,
    timeout: int = 30,
) -> None:
    """下载 AES-128 解密密钥.

    Args:
        session: HTTP Session.
        key: M3U8Key 对象（uri 字段将被下载并填充 key 字段）.
        timeout: 请求超时（秒）.

    Raises:
        RuntimeError: 如果密钥下载失败.
    """
    if key.method == "NONE" or not key.uri:
        return

    try:
        response = session.get(key.uri, timeout=timeout)
        response.raise_for_status()
        key.key = response.content
    except requests.RequestException as e:
        raise RuntimeError(f"下载解密密钥失败 ({key.uri}): {e}")


def _download_segment_task(
    session: requests.Session,
    segment: M3U8Segment,
    output_path: str,
    max_retries: int = DEFAULT_MAX_RETRIES,
    timeout: int = 30,
) -> Tuple[int, bool, int]:
    """下载单个 TS 片段（供线程池调用）.

    Args:
        session: HTTP Session.
        segment: M3U8Segment 对象.
        output_path: 输出文件路径.
        max_retries: 最大重试次数.
        timeout: 请求超时（秒）.

    Returns:
        (片段序号, 是否成功, 文件大小字节数).
    """
    # 断点续传：如果文件已存在且非空，跳过
    if os.path.exists(output_path) and os.path.getsize(output_path) > 0:
        return segment.sequence, True, os.path.getsize(output_path)

    success, size = _download_with_retry(
        session=session,
        url=segment.url,
        output_path=output_path,
        max_retries=max_retries,
        timeout=timeout,
    )
    return segment.sequence, success, size


class M3U8Downloader:
    """m3u8 下载器核心类.

    支持：
    - 多线程并发下载 TS 片段
    - 断点续传（已下载片段跳过）
    - HTTP 重试机制（指数退避）
    - AES-128 加密流下载与解密
    - 实时进度显示
    """

    def __init__(
        self,
        url: str,
        output: str = "output.mp4",
        workers: int = 8,
        tmp_dir: str = "",
        use_ffmpeg: bool = True,
        max_retries: int = DEFAULT_MAX_RETRIES,
        timeout: int = 30,
        no_proxy: bool = False,
        proxy: Optional[str] = None,
    ) -> None:
        """初始化下载器.

        Args:
            url: m3u8 URL.
            output: 输出文件路径.
            workers: 并发下载线程数.
            tmp_dir: 临时文件目录，默认为输出文件同目录下的 .tmp 子目录.
            use_ffmpeg: 是否使用 ffmpeg 合并转码.
            max_retries: 下载最大重试次数.
            timeout: HTTP 请求超时时间（秒）.
            no_proxy: 为 True 时所有请求直连、跳过系统代理环境变量.
            proxy: 手动指定代理地址（如 ``127.0.0.1:7897``）；与 no_proxy 互斥，
                同时传入时 no_proxy 优先（直连）。
        """
        self._url = url
        self._output = output
        self._workers = workers
        self._max_retries = max_retries
        self._timeout = timeout
        self._use_ffmpeg = use_ffmpeg
        self._session = create_http_session(
            timeout=timeout, no_proxy=no_proxy, proxy=proxy
        )

        # 设置临时目录
        if tmp_dir:
            self._tmp_dir = tmp_dir
        else:
            output_dir = os.path.dirname(os.path.abspath(output))
            self._tmp_dir = os.path.join(output_dir, ".tmp")

    def _fetch_m3u8_content(self, url: str) -> str:
        """获取 m3u8 文件内容.

        Args:
            url: m3u8 URL.

        Returns:
            m3u8 文件内容字符串.

        Raises:
            RuntimeError: 如果获取失败.
        """
        try:
            response = self._session.get(url, timeout=self._timeout)
            response.raise_for_status()
            return response.text
        except requests.RequestException as e:
            raise RuntimeError(f"获取 m3u8 文件失败 ({url}): {e}")

    def _resolve_playlist(self) -> M3U8Playlist:
        """解析 m3u8 播放列表，如果是 master playlist 则选择最高码率流.

        Returns:
            解析后的 M3U8Playlist（media playlist）.

        Raises:
            RuntimeError: 如果解析或选择流失败.
        """
        from m3u8_downloader.parser import M3U8Parser, select_best_stream

        # 第一次解析
        content = self._fetch_m3u8_content(self._url)
        parser = M3U8Parser(content, self._url)
        playlist = parser.parse()

        if playlist.is_master:
            # 选择最高码率流
            best_stream = select_best_stream(playlist)
            print(f"检测到多码率列表，选择最高码率: "
                  f"{best_stream.bandwidth} bps"
                  f"{f' ({best_stream.resolution})' if best_stream.resolution else ''}")

            # 获取选中流的 m3u8 内容并再次解析
            content = self._fetch_m3u8_content(best_stream.url)
            parser = M3U8Parser(content, best_stream.url)
            playlist = parser.parse()

        if not playlist.segments:
            raise RuntimeError("m3u8 播放列表中没有找到任何 TS 片段")

        return playlist

    def _download_keys(self, playlist: M3U8Playlist) -> None:
        """下载所有需要的解密密钥.

        Args:
            playlist: M3U8Playlist 对象.
        """
        seen_keys: dict = {}
        for segment in playlist.segments:
            if segment.key and segment.key.uri and segment.key.key is None:
                key_uri = segment.key.uri
                if key_uri in seen_keys:
                    # 复用已下载的密钥
                    segment.key.key = seen_keys[key_uri]
                else:
                    print(f"下载解密密钥: {key_uri}")
                    _download_key(self._session, segment.key, self._timeout)
                    seen_keys[key_uri] = segment.key.key

    def _download_segments(self, playlist: M3U8Playlist) -> List[str]:
        """并发下载所有 TS 片段.

        Args:
            playlist: M3U8Playlist 对象.

        Returns:
            下载成功的文件路径列表（按片段顺序排列）.

        Raises:
            RuntimeError: 如果有片段下载失败.
        """
        os.makedirs(self._tmp_dir, exist_ok=True)

        total = len(playlist.segments)
        print(f"共 {total} 个 TS 片段，使用 {self._workers} 线程并发下载")

        # 构建下载任务
        tasks: List[Tuple[M3U8Segment, str]] = []
        for i, segment in enumerate(playlist.segments):
            seg_filename = f"seg_{i:05d}.ts"
            seg_path = os.path.join(self._tmp_dir, seg_filename)
            tasks.append((segment, seg_path))

        # 统计变量
        success_count = 0
        fail_count = 0
        total_bytes = 0
        start_time = time.time()

        progress = ProgressBar(total=total, desc="下载进度", unit="个")

        # 使用线程池并发下载
        with ThreadPoolExecutor(max_workers=self._workers) as executor:
            futures = {}
            for segment, seg_path in tasks:
                future = executor.submit(
                    _download_segment_task,
                    self._session,
                    segment,
                    seg_path,
                    self._max_retries,
                    self._timeout,
                )
                futures[future] = (segment, seg_path)

            for future in as_completed(futures):
                segment, seg_path = futures[future]
                try:
                    seq, success, size = future.result()
                    if success:
                        success_count += 1
                        total_bytes += size
                    else:
                        fail_count += 1
                        print(f"\n片段 {seg_path} 下载失败")
                except Exception as e:
                    fail_count += 1
                    print(f"\n片段 {seg_path} 下载异常: {e}")

                progress.update(1)

        progress.close()

        elapsed = time.time() - start_time
        avg_speed = total_bytes / elapsed if elapsed > 0 else 0

        print(f"下载完成: 成功 {success_count}/{total}, "
              f"总大小 {format_file_size(total_bytes)}, "
              f"平均速度 {format_speed(avg_speed)}, "
              f"耗时 {format_duration(elapsed)}")

        if fail_count > 0:
            raise RuntimeError(f"有 {fail_count} 个片段下载失败")

        # 按顺序返回路径列表
        segment_paths = [seg_path for _, seg_path in tasks]
        return segment_paths

    def _cleanup_tmp(self) -> None:
        """清理临时目录."""
        if os.path.exists(self._tmp_dir):
            try:
                import shutil
                shutil.rmtree(self._tmp_dir)
                print("临时文件已清理")
            except OSError as e:
                print(f"清理临时文件失败: {e}")

    def download(self) -> str:
        """执行完整的下载流程.

        Returns:
            最终输出文件路径.

        Raises:
            RuntimeError: 如果下载过程中出现不可恢复的错误.
        """
        start_time = time.time()

        # 1. 解析 m3u8 播放列表
        print(f"正在解析 m3u8: {self._url}")
        playlist = self._resolve_playlist()
        print(f"解析完成: {len(playlist.segments)} 个片段, "
              f"总时长 {format_duration(playlist.total_duration)}"
              f"{', 加密流' if playlist.has_encryption else ''}")

        # 2. 下载解密密钥（如果有加密）
        if playlist.has_encryption:
            self._download_keys(playlist)

        # 3. 并发下载 TS 片段
        segment_paths = self._download_segments(playlist)

        # 4. 解密片段（如果需要）
        if playlist.has_encryption:
            from m3u8_downloader.merger import decrypt_segments
            print("正在解密 TS 片段...")
            segment_paths = decrypt_segments(playlist.segments, segment_paths)

        # 5. 合并并转换为 MP4
        from m3u8_downloader.merger import merge_segments_to_mp4
        output_path = merge_segments_to_mp4(
            segment_paths=segment_paths,
            output_path=self._output,
            use_ffmpeg=self._use_ffmpeg,
        )

        # 6. 清理临时文件
        self._cleanup_tmp()

        # 7. 输出统计信息
        elapsed = time.time() - start_time
        output_size = os.path.getsize(output_path) if os.path.exists(output_path) else 0
        print(f"\n下载完成!")
        print(f"输出文件: {os.path.abspath(output_path)}")
        print(f"文件大小: {format_file_size(output_size)}")
        print(f"总耗时: {format_duration(elapsed)}")

        return output_path
