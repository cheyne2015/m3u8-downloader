"""本地 HLS 清单检查与封装；不联网、不修改输入、不覆盖已有输出。"""

from dataclasses import dataclass
import os
from pathlib import Path
import subprocess
import tempfile
import threading
import time
from urllib.parse import urlsplit
from urllib.request import url2pathname
import uuid

from .parser import M3U8Parser
from .ffmpeg_v2 import resolve_ffmpeg_executable
from .downloader import DownloadCancelled


@dataclass(frozen=True)
class LocalMergePreview:
    playlist: Path
    segments: tuple[Path, ...]
    resources: tuple[Path, ...]
    duration_seconds: float
    encrypted: bool
    fingerprints: tuple[tuple[int, int], ...]


def inspect_local_playlist(path) -> LocalMergePreview:
    path = Path(path).resolve()
    if not path.is_file():
        raise ValueError('请选择存在的本地 m3u8 清单')
    playlist = M3U8Parser(path.read_text(encoding='utf-8-sig'), path.as_uri()).parse()
    if playlist.is_master or not playlist.segments:
        raise ValueError('请选择直接包含本地分片的 m3u8 清单')
    root = path.parent
    def local(uri):
        parsed = urlsplit(uri)
        if parsed.scheme != 'file' or parsed.netloc not in ('', 'localhost'):
            raise ValueError('清单包含非本地资源，请先完整下载到本地')
        result = Path(url2pathname(parsed.path)).resolve()
        if not result.is_relative_to(root):
            raise ValueError('本地清单引用的文件必须位于清单所在目录内')
        if not result.is_file() or result.stat().st_size == 0:
            raise ValueError(f'缺少本地分片或密钥：{result.name}')
        return result
    segments = []
    resources = [path]
    for segment in playlist.segments:
        resource = local(segment.url)
        segments.append(resource)
        resources.append(resource)
        if segment.key and segment.key.method != 'NONE':
            if segment.key.method != 'AES-128' or not segment.key.uri:
                raise ValueError('本地合并仅支持未加密和 AES-128 清单')
            key = local(segment.key.uri)
            if key.stat().st_size != 16:
                raise ValueError(f'AES-128 密钥长度不正确：{key.name}')
            resources.append(key)
        if segment.init_section:
            resources.append(local(segment.init_section.url))
    resources = tuple(dict.fromkeys(resources))
    return LocalMergePreview(path, tuple(segments), resources, playlist.total_duration,
        playlist.has_encryption, tuple((p.stat().st_size, p.stat().st_mtime_ns) for p in resources))


def suggest_output_path(playlist_path) -> Path:
    playlist = Path(playlist_path)
    base = playlist.parent / (playlist.parent.name + '.mp4')
    candidate = base
    suffix = 1
    while candidate.exists():
        candidate = base.with_name(f'{base.stem} ({suffix}).mp4')
        suffix += 1
    return candidate


def merge_local_playlist(playlist_path, output_path, *, stop_event=None) -> Path:
    stop_event = stop_event or threading.Event()
    preview = inspect_local_playlist(playlist_path)
    output = Path(output_path).resolve()
    if output.suffix.lower() != '.mp4':
        raise ValueError('输出文件必须使用 .mp4 扩展名')
    if output.exists():
        raise FileExistsError('输出文件已存在，请使用其他名称')
    if stop_event.is_set():
        raise DownloadCancelled('已取消本地合并')
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_name(f'.{output.stem}.merge-{uuid.uuid4().hex}.mp4')
    try:
        _run_ffmpeg([
            '-n', '-protocol_whitelist', 'file,crypto,data', '-allowed_extensions', 'ALL',
            '-i', str(preview.playlist), '-map', '0:v:0?', '-map', '0:a:0?',
            '-c', 'copy', '-movflags', '+faststart', str(temporary),
        ], stop_event)
        if inspect_local_playlist(preview.playlist) != preview:
            raise RuntimeError('合并过程中源文件发生变化，请等下载结束后重试')
        _run_ffmpeg([
            '-xerror', '-i', str(temporary), '-t', '1', '-map', '0:v:0?',
            '-map', '0:a:0?', '-f', 'null', os.devnull,
        ], stop_event, timeout=120)
        # Windows rename 不覆盖现有文件，且支持没有硬链接的磁盘格式。
        if os.name == 'nt':
            os.rename(temporary, output)
        else:
            os.link(temporary, output)
        return output
    finally:
        temporary.unlink(missing_ok=True)


def _run_ffmpeg(arguments, stop_event, *, timeout=None):
    process = None
    try:
        if stop_event.is_set():
            raise DownloadCancelled('已取消本地合并')
        with tempfile.TemporaryFile() as stderr:
            process = subprocess.Popen([
                resolve_ffmpeg_executable(), '-hide_banner', '-nostdin', '-v', 'error',
                *arguments,
            ], stdout=subprocess.DEVNULL, stderr=stderr,
                creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            started = time.monotonic()
            while process.poll() is None:
                if stop_event.wait(.1):
                    raise DownloadCancelled('已取消本地合并')
                if timeout is not None and time.monotonic() - started > timeout:
                    raise RuntimeError('本地合并文件校验超时')
            if stop_event.is_set():
                raise DownloadCancelled('已取消本地合并')
            if process.returncode:
                stderr.seek(0, os.SEEK_END)
                stderr.seek(max(0, stderr.tell() - 3500))
                detail = stderr.read().decode('utf-8', errors='replace')
                raise RuntimeError(f'本地合并或校验失败：{detail.strip()}')
    finally:
        if process is not None and process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=3)
