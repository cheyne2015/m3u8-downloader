"""真实 HTTP 分片尚未完成时也应上报实时下载速度。"""
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from m3u8_downloader.downloader import M3U8Downloader
from m3u8_downloader.parser import M3U8Playlist, M3U8Segment


def test_speed_updates_before_segment_finishes_and_cache_has_no_network_speed(tmp_path, qtbot):
    from m3u8_downloader.gui_v2 import MainWindow
    from m3u8_downloader.tasking import CreateTaskRequest, SQLiteTaskRepository, TaskService
    release = threading.Event()
    receiving = threading.Event()
    idle = threading.Event()
    requests = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            requests.append(self.path)
            self.send_response(200)
            self.send_header('Content-Length', '131072')
            self.end_headers()
            self.wfile.write(b'a' * 65536)
            self.wfile.flush()
            release.wait(timeout=10)
            self.wfile.write(b'b' * 65536)

        def log_message(self, *_args):
            pass

    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()
    updates = []
    errors = []
    service = TaskService(SQLiteTaskRepository(tmp_path / 'tasks.db'))
    task = service.create_tasks(CreateTaskRequest(addresses='https://example.test/index.m3u8',
        save_directory=str(tmp_path)))[0]
    item = service.list_items(task.id)[0]
    service.start_item(task.id, item.id)
    window = MainWindow(service)
    window._force_exit = True
    qtbot.addWidget(window)
    def report(progress):
        updates.append(progress)
        service.update_item_progress(task.id, item.id, progress)
        if progress['completed'] == 0 and progress['total_bytes'] == 65536 and progress['speed'] > 0:
            receiving.set()
        if receiving.is_set() and progress['completed'] == 0 and progress['speed'] == 0:
            idle.set()

    url = f'http://127.0.0.1:{server.server_port}/0.ts'
    downloader = M3U8Downloader(url, str(tmp_path / 'video.mp4'),
        tmp_dir=str(tmp_path / 'cache'), progress_callback=report, max_retries=0)
    playlist = M3U8Playlist(segments=[M3U8Segment(url)])
    def run():
        try:
            downloader._download_segments(playlist)
        except Exception as exc:
            errors.append(exc)
        finally:
            downloader._release_cache_job_lock()

    worker = threading.Thread(target=run)
    worker.start()
    try:
        observed = receiving.wait(timeout=2)
        window.refresh_tasks()
        live_title = window.page_title.text()
        live_statistics = window.statistics_label.text()
        stalled = idle.wait(timeout=2)
        window.refresh_tasks()
        idle_title = window.page_title.text()
        idle_statistics = window.statistics_label.text()
    finally:
        release.set()
        worker.join(timeout=5)
        server.shutdown()
        server.server_close()
        server_thread.join(timeout=3)
    assert not worker.is_alive()
    assert not errors
    assert observed, '整个分片完成前没有收到速度更新'
    assert '/秒' in live_title and not live_title.startswith('0 ')
    assert not live_statistics.startswith('速度 0 ')
    assert stalled, '网络停止传输时没有及时报告零速度'
    assert idle_title.startswith('0 ') and idle_statistics.startswith('速度 0 ')
    assert updates[-1]['total_bytes'] == 131072
    updates.clear()
    downloader._download_segments(playlist)
    downloader._release_cache_job_lock()
    assert requests == ['/0.ts']
    assert updates[-1]['total_bytes'] == 131072
    assert all(progress['speed'] == 0 for progress in updates)
