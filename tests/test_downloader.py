"""Downloader reliability, resume, cancellation, and progress tests."""

import json
import threading
import time
from pathlib import Path
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
import requests

from m3u8_downloader.downloader import (
    DownloadCancelled,
    M3U8Downloader,
    _download_key,
    _download_with_retry,
)
from m3u8_downloader.parser import M3U8Key, M3U8Playlist, M3U8Segment


class FakeResponse:
    def __init__(self, chunks, *, status=200, headers=None):
        self._chunks = list(chunks)
        self.status_code = status
        self.headers = headers or {}
        self.closed = False
        self.encoding = "utf-8"

    @property
    def content(self):
        return b"".join(chunk for chunk in self._chunks if isinstance(chunk, bytes))

    @property
    def text(self):
        return self.content.decode(self.encoding)

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"HTTP {self.status_code}")

    def iter_content(self, chunk_size):
        assert chunk_size >= 64 * 1024
        for chunk in self._chunks:
            if isinstance(chunk, Exception):
                raise chunk
            yield chunk

    def close(self):
        self.closed = True


class FakeSession:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


def test_zero_retries_still_performs_initial_request(tmp_path):
    response = FakeResponse([b"complete"], headers={"Content-Length": "8"})
    session = FakeSession([response])
    output = tmp_path / "seg.ts"

    assert _download_with_retry(session, "https://x/seg.ts", str(output), max_retries=0) == (
        True,
        8,
    )
    assert output.read_bytes() == b"complete"
    assert len(session.calls) == 1
    assert response.closed


def test_interrupted_segment_resumes_from_part_file(tmp_path):
    first = FakeResponse(
        [b"first-", requests.ConnectionError("cut")],
        headers={"Content-Length": "12"},
    )
    second = FakeResponse(
        [b"second"],
        status=206,
        headers={"Content-Length": "6", "Content-Range": "bytes 6-11/12"},
    )
    session = FakeSession([first, second])
    output = tmp_path / "seg.ts"

    success, size = _download_with_retry(
        session,
        "https://x/seg.ts",
        str(output),
        max_retries=1,
        retry_delay=0,
    )

    assert (success, size) == (True, 12)
    assert output.read_bytes() == b"first-second"
    assert not Path(str(output) + ".part").exists()
    assert session.calls[1][1]["headers"] == {"Range": "bytes=6-"}
    assert first.closed and second.closed


def test_wrong_content_range_is_rejected_and_restarted(tmp_path):
    output = tmp_path / "seg.ts"
    part = Path(str(output) + ".part")
    part.write_bytes(b"prefix")
    wrong_range = FakeResponse(
        [b"wrong-tail"], status=206,
        headers={"Content-Length": "10", "Content-Range": "bytes 3-12/13"},
    )
    full = FakeResponse([b"complete"], headers={"Content-Length": "8"})
    session = FakeSession([wrong_range, full])

    assert _download_with_retry(
        session, "https://x/seg.ts", str(output), max_retries=1, retry_delay=0,
    ) == (True, 8)
    assert output.read_bytes() == b"complete"
    assert session.calls[0][1]["headers"] == {"Range": "bytes=6-"}
    assert session.calls[1][1]["headers"] == {}


def test_complete_part_is_promoted_on_range_416(tmp_path):
    output = tmp_path / "seg.ts"
    part = Path(str(output) + ".part")
    part.write_bytes(b"already-complete")
    response = FakeResponse(
        [], status=416, headers={"Content-Range": "bytes */16"},
    )

    assert _download_with_retry(
        FakeSession([response]), "https://x/seg.ts", str(output), max_retries=0,
    ) == (True, 16)
    assert output.read_bytes() == b"already-complete"


def test_cancellation_interrupts_retry_backoff(tmp_path):
    stopped = threading.Event()

    class StopAfterFailure(FakeSession):
        def get(self, url, **kwargs):
            stopped.set()
            raise requests.ConnectionError("offline")

    with pytest.raises(DownloadCancelled):
        _download_with_retry(
            StopAfterFailure([]),
            "https://x/seg.ts",
            str(tmp_path / "seg.ts"),
            max_retries=3,
            retry_delay=30,
            stop_event=stopped,
        )


def test_playlist_and_key_requests_retry_transient_failures(tmp_path, monkeypatch):
    playlist_response = FakeResponse([b"#EXTM3U\n#EXTINF:1,\na.ts\n"])
    key_response = FakeResponse([b"0123456789abcdef"])
    monkeypatch.setattr("m3u8_downloader.downloader.time.sleep", lambda _seconds: None)

    downloader = M3U8Downloader(
        "https://x/index.m3u8", tmp_dir=str(tmp_path), max_retries=1
    )
    downloader._session = FakeSession([
        requests.ConnectionError("playlist transient"), playlist_response,
    ])
    assert downloader._fetch_m3u8_content("https://x/index.m3u8").startswith("#EXTM3U")

    key_session = FakeSession([requests.ConnectionError("key transient"), key_response])
    key = M3U8Key(method="AES-128", uri="https://x/key")
    _download_key(key_session, key, timeout=1, max_retries=1)
    assert key.key == b"0123456789abcdef"
    assert playlist_response.closed and key_response.closed


def _playlist(*urls):
    return M3U8Playlist(
        segments=[M3U8Segment(url=url, sequence=i) for i, url in enumerate(urls)],
        base_url="https://x/",
    )


def test_cache_manifest_reuses_only_matching_playlist(tmp_path):
    downloader = M3U8Downloader(
        "https://x/a.m3u8", output=str(tmp_path / "video.mp4"), tmp_dir=str(tmp_path)
    )
    stale = tmp_path / "seg_00000.ts"
    stale.write_bytes(b"old-video")

    downloader._prepare_segment_cache(_playlist("https://x/a.ts"))
    job_dir = Path(downloader._tmp_dir)
    assert job_dir.parent == tmp_path
    assert job_dir != tmp_path
    assert stale.exists(), "isolated jobs must not delete unrelated legacy files"

    cached = job_dir / "seg_00000.ts"
    cached.write_bytes(b"same-video")
    downloader._prepare_segment_cache(_playlist("https://x/a.ts"))
    assert cached.read_bytes() == b"same-video"

    changed = _playlist("https://x/a.ts")
    changed.segments[0].sequence = 99
    changed.segments[0].key = M3U8Key(
        method="AES-128", uri="https://x/key?secret=token", iv=b"\x01" * 16,
    )
    downloader._prepare_segment_cache(changed)
    changed_dir = Path(downloader._tmp_dir)
    assert changed_dir != job_dir
    assert not (changed_dir / "seg_00000.ts").exists()
    manifest_text = (changed_dir / ".m3u8-download.json").read_text(encoding="utf-8")
    manifest = json.loads(manifest_text)
    assert "fingerprint" in manifest
    assert "secret=token" not in manifest_text


def test_worker_threads_use_independent_sessions(monkeypatch, tmp_path):
    sessions = set()
    barrier = threading.Barrier(2)
    downloader = M3U8Downloader(
        "https://x/a.m3u8", output=str(tmp_path / "video.mp4"),
        tmp_dir=str(tmp_path / "cache"), workers=2,
    )

    def complete(session, segment, output_path, *_args, **_kwargs):
        sessions.add(id(session))
        barrier.wait(timeout=2)
        Path(output_path).write_bytes(b"x")
        return segment.sequence, True, 1

    monkeypatch.setattr("m3u8_downloader.downloader._download_segment_task", complete)
    downloader._download_segments(_playlist("https://x/0.ts", "https://x/1.ts"))
    assert len(sessions) == 2


def test_download_segments_reports_progress_callback(monkeypatch, tmp_path):
    updates = []
    downloader = M3U8Downloader(
        "https://x/a.m3u8",
        tmp_dir=str(tmp_path),
        workers=2,
        progress_callback=updates.append,
    )

    def complete(_session, segment, output_path, *_args, **_kwargs):
        Path(output_path).write_bytes(b"x" * (segment.sequence + 1))
        return segment.sequence, True, segment.sequence + 1

    monkeypatch.setattr("m3u8_downloader.downloader._download_segment_task", complete)
    paths = downloader._download_segments(_playlist("https://x/0.ts", "https://x/1.ts"))

    assert len(paths) == 2
    assert updates[-1]["percent"] == 100
    assert updates[-1]["completed"] == 2
    assert updates[-1]["total_bytes"] == 3


def test_real_http_download_recovers_mid_segment_disconnect(tmp_path):
    """真实 HTTP 中途断流后应 Range 续传，并生成逐字节正确的最终文件。"""
    chunks = [bytes([index + 1]) * (192 * 1024 + index) for index in range(4)]
    requests_seen = []
    first_cut_done = threading.Event()

    class QuietServer(ThreadingHTTPServer):
        def handle_error(self, request, client_address):
            pass

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *_args):
            pass

        def do_GET(self):
            if self.path == "/index.m3u8":
                body = (
                    "#EXTM3U\n"
                    + "".join(
                        f"#EXTINF:1,\n/seg-{index}.ts\n" for index in range(len(chunks))
                    )
                    + "#EXT-X-ENDLIST\n"
                ).encode()
                self.send_response(200)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return

            index = int(self.path.removeprefix("/seg-").removesuffix(".ts"))
            body = chunks[index]
            range_header = self.headers.get("Range", "")
            requests_seen.append((self.path, range_header))
            if index == 1 and not first_cut_done.is_set() and not range_header:
                first_cut_done.set()
                cut = len(body) * 2 // 3
                self.send_response(200)
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Connection", "close")
                self.end_headers()
                self.wfile.write(body[:cut])
                self.wfile.flush()
                self.close_connection = True
                return

            start = int(range_header[6:-1]) if range_header.startswith("bytes=") else 0
            payload = body[start:]
            self.send_response(206 if start else 200)
            if start:
                self.send_header("Content-Range", f"bytes {start}-{len(body) - 1}/{len(body)}")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

    server = QuietServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        base = f"http://127.0.0.1:{server.server_port}"
        output = tmp_path / "video.mp4"
        downloader = M3U8Downloader(
            f"{base}/index.m3u8",
            output=str(output),
            workers=4,
            max_retries=1,
            timeout=5,
            use_ffmpeg=False,
            no_proxy=True,
        )
        assert downloader.download() == str(output)
        assert output.read_bytes() == b"".join(chunks)
        assert any(path == "/seg-1.ts" and value.startswith("bytes=")
                   for path, value in requests_seen)
        assert not (tmp_path / ".tmp").exists()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_real_http_stop_keeps_only_resumable_part(tmp_path):
    """停止真实流式响应后不生成伪完成片段，并保留可续传的 .part。"""
    stopped = threading.Event()
    release_server = threading.Event()
    first_chunk_sent = threading.Event()
    payload = b"z" * (256 * 1024)

    class QuietServer(ThreadingHTTPServer):
        def handle_error(self, request, client_address):
            pass

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *_args):
            pass

        def do_GET(self):
            if self.path == "/index.m3u8":
                body = b"#EXTM3U\n#EXTINF:1,\n/slow.ts\n#EXT-X-ENDLIST\n"
                self.send_response(200)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            self.send_response(200)
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload[:64 * 1024])
            self.wfile.flush()
            first_chunk_sent.set()
            release_server.wait(5)
            self.wfile.write(payload[64 * 1024:128 * 1024])
            self.wfile.flush()

    server = QuietServer(("127.0.0.1", 0), Handler)
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()
    failure = []
    try:
        base = f"http://127.0.0.1:{server.server_port}"
        downloader = M3U8Downloader(
            f"{base}/index.m3u8", output=str(tmp_path / "video.mp4"),
            tmp_dir=str(tmp_path / "cache"), use_ffmpeg=False, no_proxy=True,
            stop_event=stopped, timeout=5,
        )

        def run():
            try:
                downloader.download()
            except Exception as exc:
                failure.append(exc)

        download_thread = threading.Thread(target=run)
        download_thread.start()
        assert first_chunk_sent.wait(5)
        part_path = Path(downloader._tmp_dir) / "seg_00000.ts.part"
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            if part_path.exists() and part_path.stat().st_size >= 64 * 1024:
                break
            time.sleep(0.01)
        assert part_path.stat().st_size >= 64 * 1024
        stopped.set()
        download_thread.join(timeout=2)
        assert not download_thread.is_alive()
        assert len(failure) == 1 and isinstance(failure[0], DownloadCancelled)
        assert not (Path(downloader._tmp_dir) / "seg_00000.ts").exists()
        assert part_path.exists()
        assert not (tmp_path / "video.mp4").exists()
    finally:
        stopped.set()
        release_server.set()
        server.shutdown()
        server.server_close()
        server_thread.join(timeout=5)
