"""保留原序号的分片恢复信息，不改变完整下载的状态和缓存。"""

from dataclasses import dataclass
import base64
import hashlib
import json
import math
import os
from pathlib import Path
import subprocess
import time

from .parser import M3U8InitSection, M3U8Key, M3U8Playlist, M3U8Segment

SNAPSHOT_NAME = '.partial-recovery.json'


@dataclass(frozen=True)
class PartialDownloadPreview:
    cache_directory: Path
    playlist: M3U8Playlist
    available_indices: tuple[int, ...]
    missing_indices: tuple[int, ...]
    fingerprint: str

    @property
    def total_segments(self):
        return len(self.playlist.segments)

    @property
    def available_segments(self):
        return len(self.available_indices)

    def _duration(self, indices):
        values = [self.playlist.segments[i].duration for i in indices]
        return sum(values) if all(math.isfinite(value) and value > 0 for value in values) else None

    @property
    def missing_duration(self):
        return self._duration(self.missing_indices)

    @property
    def available_duration(self):
        return self._duration(self.available_indices)


def output_cache_suffix(output):
    return hashlib.sha256(os.path.abspath(output).lower().encode('utf-8')).hexdigest()[:8]


def playlist_fingerprint(playlist):
    from .downloader import M3U8Downloader
    identity = M3U8Downloader._playlist_identity(playlist)
    return hashlib.sha256(json.dumps(identity, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def save_recovery_snapshot(directory, playlist, source_url):
    segments = []
    for segment in playlist.segments:
        key = segment.key
        init = segment.init_section
        segments.append({
            'url': segment.url, 'duration': segment.duration, 'sequence': segment.sequence,
            'key': None if key is None else {
                'method': key.method, 'uri': key.uri,
                'iv': key.iv.hex() if key.iv is not None else None,
                'bytes': base64.b64encode(key.key).decode() if key.key is not None and os.name == 'nt' else None,
            },
            'init': None if init is None else {'url': init.url, 'byte_range': init.byte_range},
        })
    encoded = json.dumps(segments, separators=(',', ':'))
    if os.name == 'nt':
        from .secrets_v2 import protect_secret
        encoded = protect_secret(encoded)
    payload = {'version': 1, 'source_hash': hashlib.sha256(source_url.encode()).hexdigest(),
               'fingerprint': playlist_fingerprint(playlist), 'segments': encoded}
    path = Path(directory) / SNAPSHOT_NAME
    temporary = path.with_suffix('.json.part')
    temporary.write_text(json.dumps(payload), encoding='utf-8')
    os.replace(temporary, path)


def read_partial_preview(directory, playlist, fingerprint):
    from .downloader import CACHE_MANIFEST_NAME
    directory = Path(directory)
    marker = json.loads((directory / CACHE_MANIFEST_NAME).read_text(encoding='utf-8'))
    if marker != {'version': 2, 'fingerprint': fingerprint} or playlist_fingerprint(playlist) != fingerprint:
        raise RuntimeError('分片缓存与播放列表不匹配，不能合并')
    available, missing = [], []
    for index in range(len(playlist.segments)):
        path = directory / f'seg_{index:05d}.ts'
        (available if path.is_file() and not path.is_symlink() and path.stat().st_size > 0 else missing).append(index)
    if not available:
        raise RuntimeError('没有可合并的完整分片；未完成的 .part 文件不会参与合并')
    return PartialDownloadPreview(directory, playlist, tuple(available), tuple(missing), fingerprint)


def load_partial_preview(tmp_root, output, source_url):
    root = Path(tmp_root)
    source_hash = hashlib.sha256(source_url.encode()).hexdigest()
    directories = sorted(root.glob(f'job-*-{output_cache_suffix(output)}'),
                         key=lambda directory: directory.stat().st_mtime_ns, reverse=True)
    for directory in directories:
        if directory.is_symlink():
            continue
        path = directory / SNAPSHOT_NAME
        if not path.is_file():
            continue
        try:
            if path.stat().st_size > 16 * 1024 * 1024:
                raise ValueError('恢复信息过大')
            payload = json.loads(path.read_text(encoding='utf-8'))
            if payload.get('version') != 1:
                raise ValueError('恢复信息版本不受支持')
            if payload.get('source_hash') != source_hash:
                continue
            encoded = payload['segments']
            if encoded.startswith('dpapi:'):
                from .secrets_v2 import unprotect_secret
                encoded = unprotect_secret(encoded)
            segments = []
            for record in json.loads(encoded):
                key, init = record['key'], record['init']
                segments.append(M3U8Segment(
                    record['url'], duration=record['duration'], sequence=record['sequence'],
                    key=None if key is None else M3U8Key(
                        key['method'], key['uri'], bytes.fromhex(key['iv']) if key['iv'] is not None else None,
                        base64.b64decode(key['bytes']) if key['bytes'] is not None else None,
                    ),
                    init_section=None if init is None else M3U8InitSection(init['url'], init['byte_range']),
                ))
            playlist = M3U8Playlist(segments=segments, has_encryption=any(
                segment.key and segment.key.method != 'NONE' for segment in segments))
            return read_partial_preview(directory, playlist, payload['fingerprint'])
        except (OSError, ValueError, KeyError, TypeError) as exc:
            raise RuntimeError('缓存恢复信息无法读取，原始分片已保留') from exc
    return None


def merge_partial_media(paths, output, stop_event):
    """只读取现有片段，输出须经过 ffmpeg 封装与媒体校验。"""
    from .downloader import DownloadCancelled
    from .ffmpeg_v2 import resolve_ffmpeg_executable
    from .merger import validate_media_file

    temporary = Path(output).with_suffix('.ts')
    try:
        with temporary.open('xb') as target:
            for path in paths:
                with open(path, 'rb') as source:
                    while block := source.read(1024 * 1024):
                        if stop_event.is_set():
                            raise DownloadCancelled('用户停止导出')
                        target.write(block)
        command = [resolve_ffmpeg_executable(), '-v', 'error', '-y', '-i', str(temporary),
                   '-c', 'copy', '-bsf:a', 'aac_adtstoasc', str(output)]
        process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                   creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        deadline = time.monotonic() + 3600
        try:
            while True:
                if stop_event.is_set():
                    raise DownloadCancelled('用户停止导出')
                if time.monotonic() >= deadline:
                    raise RuntimeError('不完整文件合并超时，原始缓存已保留')
                try:
                    _, error = process.communicate(timeout=.1)
                    break
                except subprocess.TimeoutExpired:
                    continue
            if process.returncode:
                raise RuntimeError('不完整文件合并失败：' + error.decode('utf-8', errors='replace')[-1500:])
        finally:
            if process.poll() is None:
                process.kill()
                process.communicate()
        validate_media_file(str(output))
    finally:
        temporary.unlink(missing_ok=True)
