import threading
from pathlib import Path

import pytest
import requests

from m3u8_downloader.downloader import M3U8Downloader
from m3u8_downloader.parser import M3U8Playlist, M3U8Segment
from m3u8_downloader.tasking.download_coordinator import DownloadCoordinator


def test_incomplete_segment_remains_retryable_after_batch_error(tmp_path):
    downloader = M3U8Downloader('https://example.test/index.m3u8',
        str(tmp_path / 'video.mp4'), tmp_dir=str(tmp_path / 'cache'), max_retries=0)
    playlist = M3U8Playlist(segments=[M3U8Segment('https://example.test/0.ts')])
    def truncated(*args):
        raise requests.exceptions.ChunkedEncodingError('IncompleteRead: body interrupted')
    downloader._download_one_segment = truncated
    with pytest.raises(RuntimeError) as caught:
        downloader._download_segments(playlist)
    downloader._release_cache_job_lock()
    assert DownloadCoordinator._is_retryable(caught.value)


def test_permanent_segment_error_does_not_become_retryable(tmp_path):
    downloader = M3U8Downloader('https://example.test/index.m3u8',
        str(tmp_path / 'video.mp4'), tmp_dir=str(tmp_path / 'cache'), max_retries=0)
    playlist = M3U8Playlist(segments=[M3U8Segment('https://example.test/0.ts')])
    def denied(*args):
        response = requests.Response()
        response.status_code = 403
        raise requests.HTTPError('HTTPSConnectionPool: HTTP 403', response=response)
    downloader._download_one_segment = denied
    with pytest.raises(RuntimeError) as caught:
        downloader._download_segments(playlist)
    downloader._release_cache_job_lock()
    assert not DownloadCoordinator._is_retryable(caught.value)


def test_truncated_http_segment_retries_only_missing_data(tmp_path):
    from collections import Counter
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    from m3u8_downloader.downloader import SegmentDownloadError
    counts = Counter()

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            counts[self.path] += 1
            self.send_response(200)
            self.send_header('Content-Length', '65536')
            self.end_headers()
            self.wfile.write(b'x' * (10 if self.path == '/1.ts' and counts[self.path] == 1 else 65536))
            self.close_connection = True

        def log_message(self, *_args):
            pass

    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f'http://127.0.0.1:{server.server_port}'
    playlist = M3U8Playlist(segments=[M3U8Segment(f'{base}/{i}.ts') for i in range(2)])
    downloader = M3U8Downloader(base + '/index.m3u8', str(tmp_path / 'video.mp4'),
        tmp_dir=str(tmp_path / 'cache'), max_retries=0)
    try:
        with pytest.raises(SegmentDownloadError) as caught:
            downloader._download_segments(playlist)
        assert caught.value.retryable
        downloader._release_cache_job_lock()
        paths = downloader._download_segments(playlist)
        assert all(Path(path).stat().st_size == 65536 for path in paths)
        assert counts == {'/0.ts': 1, '/1.ts': 2}
    finally:
        downloader._release_cache_job_lock()
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)


def test_segment_network_failure_uses_existing_parent_retry_budget(tmp_path):
    from datetime import datetime, timedelta
    from m3u8_downloader.downloader import SegmentDownloadError
    from m3u8_downloader.tasking import (
        Candidate, CreateTaskRequest, DownloadStatus, SQLiteTaskRepository, TaskService, TaskSettings,
    )
    now = [datetime(2026, 11, 5, 12)]
    service = TaskService(SQLiteTaskRepository(tmp_path / 'tasks.db'), clock=lambda: now[0])
    task = service.create_tasks(CreateTaskRequest(addresses='https://example.test/watch',
        save_directory=str(tmp_path), settings=TaskSettings(request_retries=0, task_retries=1,
        retry_delay_seconds=30)))[0]
    service.add_candidates(task.id, [Candidate('https://example.test/video.m3u8')])
    service.finish_extraction(task.id)

    class Downloader:
        attempts = 0
        def download(self, task, item, output, **kwargs):
            self.attempts += 1
            if self.attempts == 1:
                raise SegmentDownloadError(1, retryable=True)
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_bytes(b'finished')
            return output

    downloader = Downloader()
    coordinator = DownloadCoordinator(service, downloader=downloader)
    assert coordinator.run_parent(task.id).download_status is DownloadStatus.RETRY_WAIT
    assert downloader.attempts == 1
    now[0] += timedelta(seconds=30)
    service.promote_due_retries()
    assert coordinator.run_parent(task.id).download_status is DownloadStatus.COMPLETED
    assert downloader.attempts == 2
