"""核心下载逻辑模块：多线程并发下载、断点续传、HTTP 重试、加密密钥下载."""

import copy
import hashlib
import json
import os
import re
import threading
import time
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from typing import Callable, List, Optional, Tuple

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
DOWNLOAD_CHUNK_SIZE = 64 * 1024
CANCEL_POLL_INTERVAL = 0.1
CACHE_MANIFEST_NAME = ".m3u8-download.json"


class DownloadCancelled(RuntimeError):
    """下载被调用方主动停止。"""


def _wait_before_retry(
    delay: float, stop_event: Optional[threading.Event],
) -> None:
    if stop_event is not None:
        if stop_event.wait(delay):
            raise DownloadCancelled("用户停止")
    else:
        time.sleep(delay)


def _fetch_small_with_retry(
    session: requests.Session,
    url: str,
    *,
    timeout: int,
    max_retries: int,
    stop_event: Optional[threading.Event] = None,
) -> bytes:
    """下载播放列表或密钥等小资源，并对瞬时网络错误重试。"""
    attempts = max(0, int(max_retries)) + 1
    last_error: Optional[Exception] = None
    for attempt in range(attempts):
        if stop_event is not None and stop_event.is_set():
            raise DownloadCancelled("用户停止")
        response = None
        try:
            response = session.get(url, timeout=timeout)
            response.raise_for_status()
            return response.content
        except requests.RequestException as exc:
            last_error = exc
            if stop_event is not None and stop_event.is_set():
                raise DownloadCancelled("用户停止") from exc
            if attempt < attempts - 1:
                _wait_before_retry(DEFAULT_RETRY_DELAY * (DEFAULT_BACKOFF_FACTOR ** attempt),
                                   stop_event)
        finally:
            if response is not None:
                try:
                    response.close()
                except Exception:
                    pass
    assert last_error is not None
    raise last_error


def _download_with_retry(
    session: requests.Session,
    url: str,
    output_path: str,
    max_retries: int = DEFAULT_MAX_RETRIES,
    retry_delay: float = DEFAULT_RETRY_DELAY,
    backoff_factor: float = DEFAULT_BACKOFF_FACTOR,
    timeout: int = 30,
    stop_event: Optional[threading.Event] = None,
) -> Tuple[bool, int]:
    """带重试机制的文件下载.

    Args:
        session: HTTP Session.
        url: 下载 URL.
        output_path: 输出文件路径.
        max_retries: 首次请求失败后的最大重试次数.
        retry_delay: 初始重试延迟（秒）.
        backoff_factor: 退避因子.
        timeout: 请求超时（秒）.

    Returns:
        (是否成功, 文件大小字节数).
    """
    output_dir = os.path.dirname(os.path.abspath(output_path))
    os.makedirs(output_dir, exist_ok=True)
    part_path = output_path + ".part"
    attempts = max(0, int(max_retries)) + 1

    for attempt in range(attempts):
        if stop_event is not None and stop_event.is_set():
            raise DownloadCancelled("用户停止")
        response = None
        try:
            offset = os.path.getsize(part_path) if os.path.exists(part_path) else 0
            headers = {"Range": f"bytes={offset}-"} if offset else {}
            response = session.get(url, timeout=timeout, stream=True, headers=headers)
            if stop_event is not None and stop_event.is_set():
                raise DownloadCancelled("用户停止")

            if response.status_code == 416 and offset:
                unsatisfied = re.fullmatch(
                    r"bytes \*/(\d+)", response.headers.get("Content-Range", "").strip(),
                    re.IGNORECASE,
                )
                if unsatisfied and int(unsatisfied.group(1)) == offset:
                    os.replace(part_path, output_path)
                    return True, offset
                if unsatisfied:
                    try:
                        os.remove(part_path)
                    except FileNotFoundError:
                        pass
                    raise requests.ConnectionError(
                        f"本地续传大小 {offset} 与服务器总长度 {unsatisfied.group(1)} 不符"
                    )
            response.raise_for_status()

            content_range = response.headers.get("Content-Range", "")
            range_match = re.fullmatch(
                r"bytes (\d+)-(\d+)/(\d+|\*)", content_range.strip(), re.IGNORECASE,
            ) if response.status_code == 206 else None
            if response.status_code == 206:
                valid_start = int(range_match.group(1)) if range_match else -1
                if valid_start != offset:
                    try:
                        os.remove(part_path)
                    except FileNotFoundError:
                        pass
                    raise requests.ConnectionError(
                        f"续传范围不匹配：请求从 {offset} 开始，响应为 {content_range or '空'}"
                    )
            append = bool(offset and response.status_code == 206)
            mode = "ab" if append else "wb"
            response_bytes = 0
            with open(part_path, mode) as f:
                for chunk in response.iter_content(chunk_size=DOWNLOAD_CHUNK_SIZE):
                    if stop_event is not None and stop_event.is_set():
                        raise DownloadCancelled("用户停止")
                    if chunk:
                        f.write(chunk)
                        response_bytes += len(chunk)

            declared_length = response.headers.get("Content-Length")
            content_encoding = response.headers.get("Content-Encoding", "identity").lower()
            try:
                expected_bytes = int(declared_length) if declared_length else None
            except (TypeError, ValueError):
                expected_bytes = None
            if (
                expected_bytes is not None
                and content_encoding in ("", "identity")
                and response_bytes != expected_bytes
            ):
                raise requests.ConnectionError(
                    f"响应体不完整：期望 {expected_bytes} 字节，实际 {response_bytes} 字节"
                )
            if range_match and range_match.group(3) != "*":
                total_size = int(range_match.group(3))
                actual_size = os.path.getsize(part_path)
                if actual_size != total_size:
                    raise requests.ConnectionError(
                        f"续传后文件不完整：期望 {total_size} 字节，实际 {actual_size} 字节"
                    )
            os.replace(part_path, output_path)
            file_size = os.path.getsize(output_path)
            return True, file_size
        except DownloadCancelled:
            raise
        except (requests.RequestException, OSError) as e:
            if stop_event is not None and stop_event.is_set():
                raise DownloadCancelled("用户停止") from e
            if attempt < attempts - 1:
                delay = retry_delay * (backoff_factor ** attempt)
                _wait_before_retry(delay, stop_event)
        finally:
            if response is not None:
                try:
                    response.close()
                except Exception:
                    pass

    # 所有重试都失败
    return False, 0


def _download_key(
    session: requests.Session,
    key: M3U8Key,
    timeout: int = 30,
    max_retries: int = DEFAULT_MAX_RETRIES,
    stop_event: Optional[threading.Event] = None,
) -> None:
    """下载 AES-128 解密密钥.

    Args:
        session: HTTP Session.
        key: M3U8Key 对象（uri 字段将被下载并填充 key 字段）.
        timeout: 请求超时（秒）.
        max_retries: 首次请求失败后的最大重试次数.
        stop_event: 可选停止信号，可中断请求间的退避等待.

    Raises:
        RuntimeError: 如果密钥下载失败.
    """
    if key.method == "NONE" or not key.uri:
        return

    try:
        key.key = _fetch_small_with_retry(
            session, key.uri, timeout=timeout, max_retries=max_retries,
            stop_event=stop_event,
        )
    except requests.RequestException as e:
        raise RuntimeError(f"下载解密密钥失败 ({key.uri}): {e}")


def _download_segment_task(
    session: requests.Session,
    segment: M3U8Segment,
    output_path: str,
    max_retries: int = DEFAULT_MAX_RETRIES,
    timeout: int = 30,
    stop_event: Optional[threading.Event] = None,
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
        stop_event=stop_event,
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
        stop_event: Optional[threading.Event] = None,
        progress_callback: Optional[Callable[[dict], None]] = None,
        log_callback: Optional[Callable[[str], None]] = None,
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
        self._stop_event = stop_event or threading.Event()
        self._progress_callback = progress_callback
        self._log_callback = log_callback
        self._worker_local = threading.local()
        self._worker_sessions: set = set()
        self._worker_sessions_lock = threading.Lock()
        self._session = create_http_session(
            timeout=timeout, no_proxy=no_proxy, proxy=proxy, pool_maxsize=max(1, workers)
        )

        # 设置临时目录
        if tmp_dir:
            self._tmp_root = os.path.abspath(tmp_dir)
        else:
            output_dir = os.path.dirname(os.path.abspath(output))
            self._tmp_root = os.path.join(output_dir, ".tmp")
        self._tmp_dir = self._tmp_root

    def _log(self, message: str) -> None:
        if self._log_callback is not None:
            self._log_callback(message)
        else:
            print(message)

    def _check_stopped(self) -> None:
        if self._stop_event.is_set():
            raise DownloadCancelled("用户停止")

    def _new_worker_session(self) -> requests.Session:
        """为线程池中的每个线程创建独立 Session，并复制认证请求状态。"""
        session = requests.Session()
        session.headers.update(self._session.headers)
        session.cookies.update(self._session.cookies)
        session.auth = self._session.auth
        session.proxies.update(self._session.proxies)
        session.verify = self._session.verify
        session.cert = self._session.cert
        session.trust_env = self._session.trust_env
        session.max_redirects = self._session.max_redirects
        session.params.update(self._session.params)
        session.hooks = copy.deepcopy(self._session.hooks)
        for prefix in ("http://", "https://"):
            session.mount(prefix, requests.adapters.HTTPAdapter(
                pool_connections=1, pool_maxsize=1, pool_block=True, max_retries=0,
            ))
        with self._worker_sessions_lock:
            self._worker_sessions.add(session)
        return session

    def _get_worker_session(self) -> requests.Session:
        session = getattr(self._worker_local, "session", None)
        if session is None:
            session = self._new_worker_session()
            self._worker_local.session = session
        return session

    def _close_worker_sessions(self) -> None:
        with self._worker_sessions_lock:
            sessions = list(self._worker_sessions)
            self._worker_sessions.clear()
        for session in sessions:
            try:
                session.close()
            except Exception:
                pass

    def _drain_cancelled_executor(self, executor: ThreadPoolExecutor) -> None:
        """在后台等待已开始的网络读取结束，再释放线程 Session。"""
        executor.shutdown(wait=True, cancel_futures=True)
        self._close_worker_sessions()

    def cancel(self) -> None:
        """请求停止；在途读取会在短读超时后观察到该状态。"""
        self._stop_event.set()

    @staticmethod
    def _playlist_identity(playlist: M3U8Playlist) -> dict:
        segments = []
        for segment in playlist.segments:
            key = segment.key
            segments.append({
                "url": segment.url,
                "sequence": segment.sequence,
                "key_method": key.method if key else "NONE",
                "key_uri": key.uri if key else None,
                "key_iv": key.iv.hex() if key and key.iv is not None else None,
            })
        return {"version": 2, "segments": segments}

    def _prepare_segment_cache(self, playlist: M3U8Playlist) -> None:
        """只复用与当前播放列表完全匹配的已完成片段。"""
        identity = self._playlist_identity(playlist)
        encoded = json.dumps(identity, sort_keys=True, separators=(",", ":")).encode("utf-8")
        fingerprint = hashlib.sha256(encoded).hexdigest()
        output_fingerprint = hashlib.sha256(
            os.path.abspath(self._output).lower().encode("utf-8")
        ).hexdigest()[:8]
        self._tmp_dir = os.path.join(
            self._tmp_root, f"job-{fingerprint[:16]}-{output_fingerprint}",
        )
        os.makedirs(self._tmp_dir, exist_ok=True)
        manifest_path = os.path.join(self._tmp_dir, CACHE_MANIFEST_NAME)
        expected = {"version": 2, "fingerprint": fingerprint}
        current = None
        try:
            with open(manifest_path, "r", encoding="utf-8") as stream:
                current = json.load(stream)
        except (OSError, ValueError, TypeError):
            pass

        if current != expected:
            for name in os.listdir(self._tmp_dir):
                if name.startswith("seg_") and (
                    name.endswith(".ts")
                    or name.endswith(".ts.part")
                    or name.endswith(".ts.dec")
                ):
                    try:
                        os.remove(os.path.join(self._tmp_dir, name))
                    except FileNotFoundError:
                        pass
            temp_manifest = manifest_path + ".tmp"
            with open(temp_manifest, "w", encoding="utf-8") as stream:
                json.dump(expected, stream, ensure_ascii=False, separators=(",", ":"))
            os.replace(temp_manifest, manifest_path)

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
            content = _fetch_small_with_retry(
                self._session, url, timeout=self._timeout,
                max_retries=self._max_retries, stop_event=self._stop_event,
            )
            return content.decode("utf-8-sig", errors="replace")
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
            self._log(f"检测到多码率列表，选择最高码率: "
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
            self._check_stopped()
            if segment.key and segment.key.uri and segment.key.key is None:
                key_uri = segment.key.uri
                if key_uri in seen_keys:
                    # 复用已下载的密钥
                    segment.key.key = seen_keys[key_uri]
                else:
                    self._log(f"下载解密密钥: {key_uri}")
                    _download_key(
                        self._session, segment.key, self._timeout,
                        self._max_retries, self._stop_event,
                    )
                    seen_keys[key_uri] = segment.key.key

    def _download_one_segment(
        self, segment: M3U8Segment, seg_path: str,
    ) -> Tuple[int, bool, int]:
        return _download_segment_task(
            self._get_worker_session(), segment, seg_path,
            self._max_retries, self._timeout, self._stop_event,
        )

    def _download_segments(self, playlist: M3U8Playlist) -> List[str]:
        """并发下载所有 TS 片段.

        Args:
            playlist: M3U8Playlist 对象.

        Returns:
            下载成功的文件路径列表（按片段顺序排列）.

        Raises:
            RuntimeError: 如果有片段下载失败.
        """
        self._check_stopped()
        self._prepare_segment_cache(playlist)

        total = len(playlist.segments)
        self._log(f"共 {total} 个 TS 片段，使用 {self._workers} 线程并发下载")

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

        progress = ProgressBar(
            total=total, desc="下载进度", unit="个",
            disable=self._progress_callback is not None,
        )

        executor = ThreadPoolExecutor(max_workers=self._workers)
        futures = {}
        try:
            for segment, seg_path in tasks:
                future = executor.submit(self._download_one_segment, segment, seg_path)
                futures[future] = (segment, seg_path)

            pending_futures = set(futures)
            while pending_futures:
                self._check_stopped()
                completed_futures, pending_futures = wait(
                    pending_futures,
                    timeout=CANCEL_POLL_INTERVAL,
                    return_when=FIRST_COMPLETED,
                )
                for future in completed_futures:
                    _segment, seg_path = futures[future]
                    try:
                        _seq, success, size = future.result()
                        if success:
                            success_count += 1
                            total_bytes += size
                        else:
                            fail_count += 1
                            self._log(f"片段 {seg_path} 下载失败")
                    except DownloadCancelled:
                        raise
                    except Exception as e:
                        fail_count += 1
                        self._log(f"片段 {seg_path} 下载异常: {e}")

                    progress.update(1)
                    completed = success_count + fail_count
                    elapsed_now = time.time() - start_time
                    speed_now = total_bytes / elapsed_now if elapsed_now > 0 else 0
                    eta = (
                        elapsed_now / completed * (total - completed)
                        if completed > 0 else 0
                    )
                    if self._progress_callback is not None:
                        self._progress_callback({
                            "percent": completed / total * 100 if total else 100,
                            "completed": completed,
                            "total": total,
                            "speed": speed_now,
                            "eta": eta,
                            "total_bytes": total_bytes,
                        })
        except DownloadCancelled:
            for pending in futures:
                pending.cancel()
            self.cancel()
            threading.Thread(
                target=self._drain_cancelled_executor,
                args=(executor,),
                daemon=True,
                name="m3u8-download-cleanup",
            ).start()
            raise
        except Exception:
            executor.shutdown(wait=True, cancel_futures=True)
            raise
        else:
            executor.shutdown(wait=True)
        finally:
            progress.close()
            if not self._stop_event.is_set():
                self._close_worker_sessions()

        elapsed = time.time() - start_time
        avg_speed = total_bytes / elapsed if elapsed > 0 else 0

        self._log(f"下载完成: 成功 {success_count}/{total}, "
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
                try:
                    os.rmdir(self._tmp_root)
                except OSError:
                    pass
                self._log("临时文件已清理")
            except OSError as e:
                self._log(f"清理临时文件失败: {e}")

    def download(self) -> str:
        """执行完整的下载流程.

        Returns:
            最终输出文件路径.

        Raises:
            RuntimeError: 如果下载过程中出现不可恢复的错误.
        """
        start_time = time.time()
        try:
            self._check_stopped()
            self._log(f"正在解析 m3u8: {self._url}")
            playlist = self._resolve_playlist()
            self._log(f"解析完成: {len(playlist.segments)} 个片段, "
                      f"总时长 {format_duration(playlist.total_duration)}"
                      f"{', 加密流' if playlist.has_encryption else ''}")

            self._check_stopped()
            if playlist.has_encryption:
                self._download_keys(playlist)

            segment_paths = self._download_segments(playlist)

            self._check_stopped()
            if playlist.has_encryption:
                from m3u8_downloader.merger import decrypt_segments
                self._log("正在解密 TS 片段...")
                segment_paths = decrypt_segments(playlist.segments, segment_paths)

            self._check_stopped()
            from m3u8_downloader.merger import merge_segments_to_mp4
            self._log("正在合并 TS 片段...")
            output_path = merge_segments_to_mp4(
                segment_paths=segment_paths,
                output_path=self._output,
                use_ffmpeg=self._use_ffmpeg,
            )

            self._cleanup_tmp()

            elapsed = time.time() - start_time
            output_size = os.path.getsize(output_path) if os.path.exists(output_path) else 0
            self._log("")
            self._log("下载完成！")
            self._log(f"输出文件: {os.path.abspath(output_path)}")
            self._log(f"文件大小: {format_file_size(output_size)}")
            self._log(f"总耗时: {format_duration(elapsed)}")
            return output_path
        finally:
            if not self._stop_event.is_set():
                self._close_worker_sessions()
            self._session.close()
