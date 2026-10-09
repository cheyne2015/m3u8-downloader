"""通用深度提取识别率回归：真实浏览器、独立本地页面。"""

import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from m3u8_downloader.extractor import extract_m3u8_from_page_with_title


@pytest.fixture
def discovery_page():
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            if self.path.startswith('/failed-'):
                self.connection.close()
                return
            if self.path == '/requests':
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
            self.send_header('Content-Type', 'text/html; charset=utf-8')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f'http://127.0.0.1:{server.server_port}'
    server.shutdown()
    server.server_close()
    thread.join()


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
