"""通用深度提取识别率回归：真实浏览器、独立本地页面。"""

import threading
import json
import subprocess
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from m3u8_downloader.extractor import extract_m3u8_from_page_with_title


@pytest.fixture
def discovery_page():
    release_slow_response = threading.Event()
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            if self.path.startswith('/api/'):
                if self.path == '/api/typed-slow':
                    self.send_response(200)
                    self.send_header('Content-Type', 'application/vnd.apple.mpegurl')
                    self.send_header('Content-Length', '23')
                    self.end_headers()
                    self.wfile.flush()
                    release_slow_response.wait(10)
                    return
                playlist = self.path in ('/api/typed?token=keep', '/api/plain')
                body = b'#EXTM3U\n#EXT-X-ENDLIST\n' if playlist else b'ordinary text mentioning #EXTM3U'
                if self.path == '/api/oversize':
                    body = b'#EXTM3U\n' + b'x' * (256 * 1024)
                if self.path == '/api/invalid-marker':
                    body = b'#EXTM3Ufake\n'
                self.send_response(404 if self.path == '/api/error' else 200)
                self.send_header('Content-Type', 'application/vnd.apple.mpegurl' if 'typed?' in self.path else 'text/plain')
                self.send_header('Content-Length', str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            if self.path.startswith('/failed-'):
                self.connection.close()
                return
            if self.path == '/guarded':
                body = b'''<title>Guarded responses</title><script>
                for (const name of ['plain', 'oversize', 'invalid-marker', 'error'])
                  fetch('/api/' + name);
                </script>'''
            elif self.path == '/slow-headers':
                body = b'<title>Slow body</title><script>fetch("/api/typed-slow")</script>'
            elif self.path == '/style-dependent':
                body = b'''<title>Styled player</title><script>
                const style = document.createElement('link');
                style.rel = 'stylesheet'; style.href = '/player.css';
                style.onload = () => fetch('/api/plain');
                document.head.appendChild(style);
                </script>'''
            elif self.path == '/player.css':
                body = b'video { display: block; }'
            elif self.path == '/interfaces':
                body = b'''<title>Playlist interfaces</title><script>
                for (const name of ['typed?token=keep', 'plain', 'ordinary'])
                  fetch('/api/' + name);
                </script>'''
            elif self.path == '/requests':
                body = b'''<title>Four players</title><script>
                for (let i = 1; i <= 4; i++) {
                  const url = '/failed-' + i + '.m3u8';
                  if (i % 2) fetch(url).catch(() => {});
                  else { const xhr = new XMLHttpRequest(); xhr.open('GET', url); xhr.send(); }
                }
                </script>'''
            elif self.path == '/frames':
                body = b'''<title>Embedded players</title>
                <iframe src="/nested/one"></iframe><iframe src="/nested/two"></iframe>'''
            elif self.path.startswith('/nested/'):
                body = b'''<script type="application/json">{"source":"relative.m3u8"}</script>'''
            elif self.path == '/players':
                body = b'''<title>Lazy players</title><video></video><video></video>
                <video></video><video></video><script>
                document.querySelectorAll('video').forEach((video, index) => {
                  video.play = () => {
                    setTimeout(() => fetch('/playlist-' + (index + 1) + '.' + 'm3u8'), 700);
                    return Promise.resolve();
                  };
                });
                </script>'''
            else:
                body = b'<title>Empty</title>'
            self.send_response(200)
            self.send_header('Content-Type', 'text/css' if self.path == '/player.css' else 'text/html; charset=utf-8')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f'http://127.0.0.1:{server.server_port}'
    release_slow_response.set()
    server.shutdown()
    server.server_close()
    thread.join()


@pytest.mark.parametrize('use_worker', [False, True], ids=['inprocess', 'service'])
def test_extensionless_playlists_are_discovered_without_ordinary_text(discovery_page, use_worker):
    streamed = []
    candidates, _ = extract_m3u8_from_page_with_title(
        discovery_page + '/interfaces', deep=True, estimate=False, no_proxy=True,
        stop_event=threading.Event() if use_worker else None,
        on_candidate=lambda candidate: streamed.append(candidate.url),
    )
    expected = {discovery_page + '/api/typed?token=keep', discovery_page + '/api/plain'}
    assert {candidate.url for candidate in candidates} == expected
    assert set(streamed) == expected


def test_standalone_worker_discovers_extensionless_playlists(discovery_page):
    result = subprocess.run(
        [sys.executable, '-m', 'm3u8_downloader.deep_worker', '--url',
         discovery_page + '/interfaces', '--stream'],
        capture_output=True, encoding='utf-8', timeout=30,
    )
    assert result.returncode == 0, result.stderr
    events = [json.loads(line) for line in result.stdout.splitlines()]
    expected = {discovery_page + '/api/typed?token=keep', discovery_page + '/api/plain'}
    assert set(events[-1]['urls']) == expected
    assert {event['url'] for event in events if event.get('event') == 'candidate'} == expected


@pytest.mark.parametrize('use_worker', [False, True], ids=['inprocess', 'service'])
def test_player_initialized_after_stylesheet_load_is_discovered(discovery_page, use_worker):
    candidates, _ = extract_m3u8_from_page_with_title(
        discovery_page + '/style-dependent', deep=True, estimate=False, no_proxy=True,
        stop_event=threading.Event() if use_worker else None,
    )
    assert [candidate.url for candidate in candidates] == [discovery_page + '/api/plain']


def test_typed_playlist_is_reported_before_body_finishes_and_can_stop(discovery_page):
    stop = threading.Event()
    streamed = []

    def discovered(candidate):
        streamed.append(candidate.url)
        stop.set()

    candidates, _ = extract_m3u8_from_page_with_title(
        discovery_page + '/slow-headers', deep=True, estimate=False, no_proxy=True,
        stop_event=stop, on_candidate=discovered,
    )
    assert streamed == [discovery_page + '/api/typed-slow']
    assert [candidate.url for candidate in candidates] == streamed


@pytest.mark.parametrize('use_worker', [False, True], ids=['inprocess', 'service'])
def test_content_detection_ignores_large_invalid_and_failed_responses(discovery_page, use_worker):
    candidates, _ = extract_m3u8_from_page_with_title(
        discovery_page + '/guarded', deep=True, estimate=False, no_proxy=True,
        stop_event=threading.Event() if use_worker else None,
    )
    assert [candidate.url for candidate in candidates] == [discovery_page + '/api/plain']


@pytest.mark.parametrize('use_worker', [False, True], ids=['inprocess', 'service'])
def test_four_requested_playlists_are_discovered_without_responses(discovery_page, use_worker):
    candidates, title = extract_m3u8_from_page_with_title(
        discovery_page + '/requests', deep=True, estimate=False, no_proxy=True,
        stop_event=threading.Event() if use_worker else None,
    )
    assert {candidate.url for candidate in candidates} == {
        discovery_page + '/failed-1.m3u8', discovery_page + '/failed-2.m3u8',
        discovery_page + '/failed-3.m3u8', discovery_page + '/failed-4.m3u8',
    }
    assert title == 'Four players'


@pytest.mark.parametrize('use_worker', [False, True], ids=['inprocess', 'service'])
def test_embedded_page_playlist_is_resolved_against_its_own_address(discovery_page, use_worker):
    candidates, _ = extract_m3u8_from_page_with_title(
        discovery_page + '/frames', deep=True, estimate=False, no_proxy=True,
        stop_event=threading.Event() if use_worker else None,
    )
    assert [candidate.url for candidate in candidates] == [
        discovery_page + '/nested/relative.m3u8',
    ]


@pytest.mark.parametrize('use_worker', [False, True], ids=['inprocess', 'service'])
def test_all_four_lazy_players_are_activated_and_delayed_links_discovered(discovery_page, use_worker):
    candidates, _ = extract_m3u8_from_page_with_title(
        discovery_page + '/players', deep=True, estimate=False, no_proxy=True,
        stop_event=threading.Event() if use_worker else None,
    )
    assert {candidate.url for candidate in candidates} == {
        discovery_page + '/playlist-1.m3u8', discovery_page + '/playlist-2.m3u8',
        discovery_page + '/playlist-3.m3u8', discovery_page + '/playlist-4.m3u8',
    }
