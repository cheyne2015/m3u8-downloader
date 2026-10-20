"""部分下载恢复：真实 HTTP、缓存、解密及 ffmpeg 导出。"""

from collections import Counter
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import subprocess
import threading

import pytest

from m3u8_downloader.downloader import M3U8Downloader, DownloadCancelled
from m3u8_downloader.ffmpeg_v2 import resolve_ffmpeg_executable
from m3u8_downloader.merger import validate_media_file


@pytest.fixture
def partial_site():
    state = {"failed": {1}, "requests": Counter(), "segment": b"segment-data", "encrypted": False}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            state["requests"][self.path] += 1
            if self.path == '/index.m3u8':
                body = b'#EXTM3U\n#EXT-X-TARGETDURATION:1\n#EXT-X-MEDIA-SEQUENCE:7\n'
                if state['encrypted']:
                    body += b'#EXT-X-KEY:METHOD=AES-128,URI="key"\n'
                if state.get('init'):
                    body += b'#EXT-X-MAP:URI="init.mp4"\n'
                body += b''.join(b'#EXTINF:1,\n' + str(i).encode() + b'.ts\n' for i in range(3))
                body += b'#EXT-X-ENDLIST\n'
            elif self.path == '/key':
                body = b'0123456789abcdef'
            elif self.path == '/init.mp4':
                body = state['init']
            else:
                index = int(self.path[1:-3])
                if index in state['failed']:
                    self.send_error(404)
                    return
                body = state.get('segments', {}).get(index, state['segment'])
                if state['encrypted']:
                    from Crypto.Cipher import AES
                    from Crypto.Util.Padding import pad
                    body = AES.new(b'0123456789abcdef', AES.MODE_CBC,
                                   (7 + index).to_bytes(16, 'big')).encrypt(pad(body, 16))
            self.send_response(200)
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    state['url'] = f'http://127.0.0.1:{server.server_port}/index.m3u8'
    yield state
    server.shutdown()
    server.server_close()
    thread.join()


@pytest.fixture
def media_segment():
    return subprocess.run(
        [resolve_ffmpeg_executable(), '-v', 'error', '-f', 'lavfi', '-i',
         'color=c=blue:s=64x64:r=10', '-t', '1', '-c:v', 'mpeg2video', '-f', 'mpegts', 'pipe:1'],
        capture_output=True, check=True,
    ).stdout


def test_failed_download_can_preview_cached_segments_without_network(partial_site, tmp_path):
    output = tmp_path / 'video.mp4'
    downloader = M3U8Downloader(partial_site['url'], str(output), max_retries=0, no_proxy=True)
    with pytest.raises(RuntimeError, match='片段'):
        downloader.download()
    requests_before = dict(partial_site['requests'])

    preview = M3U8Downloader(partial_site['url'], str(output), no_proxy=True).inspect_partial()

    assert preview.total_segments == 3
    assert preview.available_segments == 2
    assert preview.missing_duration == 1
    assert preview.available_duration == 2
    assert preview.missing_indices == (1,)
    assert dict(partial_site['requests']) == requests_before
    assert not output.exists()


def test_partial_export_is_playable_and_retry_only_fetches_missing_segment(partial_site, tmp_path, media_segment):
    sample = media_segment
    partial_site['segment'] = sample
    output = tmp_path / 'video.mp4'
    with pytest.raises(RuntimeError, match='片段'):
        M3U8Downloader(partial_site['url'], str(output), max_retries=0, no_proxy=True).download()
    before = dict(partial_site['requests'])
    output.write_bytes(b'preserve original')

    exported = Path(M3U8Downloader(partial_site['url'], str(output), no_proxy=True).export_partial())

    assert exported.name == 'video_内容不完整.mp4'
    validate_media_file(str(exported))
    assert output.read_bytes() == b'preserve original'
    assert dict(partial_site['requests']) == before
    cache = list((tmp_path / '.tmp').glob('job-*'))[0]
    assert (cache / 'seg_00000.ts').read_bytes() == sample
    assert (cache / 'seg_00002.ts').read_bytes() == sample
    partial_site['failed'].clear()
    M3U8Downloader(partial_site['url'], str(output), max_retries=0, no_proxy=True).download()
    assert partial_site['requests']['/0.ts'] == 1
    assert partial_site['requests']['/2.ts'] == 1
    assert partial_site['requests']['/1.ts'] == before['/1.ts'] + 1
    validate_media_file(str(output))
    assert exported.is_file()


def test_encrypted_partial_export_preserves_sequence_and_cached_keys(partial_site, tmp_path, media_segment):
    partial_site.update(segment=media_segment, encrypted=True)
    output = tmp_path / 'encrypted.mp4'
    with pytest.raises(RuntimeError, match='片段'):
        M3U8Downloader(partial_site['url'], str(output), max_retries=0, no_proxy=True).download()
    before = dict(partial_site['requests'])
    preview = M3U8Downloader(partial_site['url'], str(output), no_proxy=True).inspect_partial()
    assert [preview.playlist.segments[index].sequence for index in preview.available_indices] == [7, 9]
    exported = M3U8Downloader(partial_site['url'], str(output), no_proxy=True).export_partial(preview)
    validate_media_file(exported)
    assert dict(partial_site['requests']) == before
    assert b'0123456789abcdef' not in (preview.cache_directory / '.partial-recovery.json').read_bytes()
    assert not output.exists()


def test_failed_and_cancelled_exports_keep_cache_and_leave_no_output(partial_site, tmp_path):
    output = tmp_path / 'video.mp4'
    with pytest.raises(RuntimeError, match='片段'):
        M3U8Downloader(partial_site['url'], str(output), max_retries=0, no_proxy=True).download()
    preview = M3U8Downloader(partial_site['url'], str(output)).inspect_partial()
    stop = threading.Event()
    stop.set()
    with pytest.raises(DownloadCancelled):
        M3U8Downloader(partial_site['url'], str(output), stop_event=stop).export_partial(preview)
    with pytest.raises(RuntimeError, match='合并失败'):
        M3U8Downloader(partial_site['url'], str(output)).export_partial(preview)
    assert not list(tmp_path.glob('*.mp4'))
    assert not list(tmp_path.glob('.partial-*'))
    assert (preview.cache_directory / 'seg_00000.ts').read_bytes() == b'segment-data'


def test_cache_change_and_unfinished_segments_cannot_bypass_confirmation(partial_site, tmp_path):
    output = tmp_path / 'video.mp4'
    with pytest.raises(RuntimeError, match='片段'):
        M3U8Downloader(partial_site['url'], str(output), max_retries=0, no_proxy=True).download()
    preview = M3U8Downloader(partial_site['url'], str(output)).inspect_partial()
    (preview.cache_directory / 'seg_00002.ts').unlink()
    (preview.cache_directory / 'seg_00001.ts.part').write_bytes(b'unfinished')
    with pytest.raises(RuntimeError, match='缓存已变化'):
        M3U8Downloader(partial_site['url'], str(output)).export_partial(preview)
    fresh = M3U8Downloader(partial_site['url'], str(output)).inspect_partial()
    assert fresh.available_segments == 1
    assert fresh.missing_indices == (1, 2)
    (preview.cache_directory / 'seg_00000.ts').unlink()
    with pytest.raises(RuntimeError, match='没有可合并'):
        M3U8Downloader(partial_site['url'], str(output)).inspect_partial()


def test_export_stop_cancels_ffmpeg_and_preserves_cache(partial_site, tmp_path, media_segment, monkeypatch):
    output = tmp_path / 'cancel.mp4'
    partial_site['segment'] = media_segment
    with pytest.raises(RuntimeError):
        M3U8Downloader(partial_site['url'], str(output), max_retries=0, no_proxy=True).download()
    stop = threading.Event()

    class StalledFFmpeg:
        killed = False

        def communicate(self, timeout=None):
            if self.killed:
                return b'', b''
            stop.set()
            raise subprocess.TimeoutExpired('ffmpeg', timeout)

        def poll(self):
            return -1 if self.killed else None

        def kill(self):
            self.killed = True

    process = StalledFFmpeg()
    monkeypatch.setattr('m3u8_downloader.partial_download.subprocess.Popen', lambda *args, **kwargs: process)
    with pytest.raises(DownloadCancelled):
        M3U8Downloader(partial_site['url'], str(output), stop_event=stop).export_partial()
    assert process.killed
    assert not list(tmp_path.glob('*.mp4'))
    assert not list(tmp_path.glob('.partial-*'))
    assert len(list((tmp_path / '.tmp').rglob('seg_*.ts'))) == 2


def test_legacy_cache_can_be_adopted_without_redownloading_segments(partial_site, tmp_path):
    output = tmp_path / 'legacy.mp4'
    with pytest.raises(RuntimeError, match='片段'):
        M3U8Downloader(partial_site['url'], str(output), max_retries=0, no_proxy=True).download()
    snapshot = next((tmp_path / '.tmp').rglob('.partial-recovery.json'))
    snapshot.unlink()
    before = dict(partial_site['requests'])
    preview = M3U8Downloader(partial_site['url'], str(output), no_proxy=True).inspect_partial()
    assert preview.available_segments == 2
    assert partial_site['requests']['/index.m3u8'] == before['/index.m3u8'] + 1
    assert partial_site['requests']['/0.ts'] == before['/0.ts']
    assert partial_site['requests']['/2.ts'] == before['/2.ts']
    assert snapshot.is_file()


def test_refreshed_media_address_can_be_recovered_by_original_item_address(partial_site, tmp_path):
    output = tmp_path / 'refreshed.mp4'
    original = 'https://example.test/original.m3u8'
    with pytest.raises(RuntimeError, match='片段'):
        M3U8Downloader(partial_site['url'], str(output), cache_source_url=original,
                       max_retries=0, no_proxy=True).download()
    before = dict(partial_site['requests'])
    preview = M3U8Downloader(original, str(output), no_proxy=True).inspect_partial()
    assert preview.available_segments == 2
    assert dict(partial_site['requests']) == before


def test_fragmented_mp4_exports_required_initialization_and_numbers_repeats(partial_site, tmp_path):
    media = tmp_path / 'media'
    media.mkdir()
    subprocess.run(
        [resolve_ffmpeg_executable(), '-v', 'error', '-f', 'lavfi', '-i',
         'color=c=green:s=64x64:r=10', '-t', '3', '-c:v', 'libx264', '-g', '10',
         '-sc_threshold', '0', '-f', 'hls', '-hls_time', '1', '-hls_segment_type', 'fmp4',
         '-hls_fmp4_init_filename', str(media / 'init.mp4'),
         '-hls_segment_filename', str(media / '%d.m4s'), str(media / 'index.m3u8')],
        check=True, capture_output=True,
    )
    partial_site.update(init=(media / 'init.mp4').read_bytes(),
                        segments={i: (media / f'{i}.m4s').read_bytes() for i in range(3)})
    output = tmp_path / 'fragmented.mp4'
    with pytest.raises(RuntimeError, match='片段'):
        M3U8Downloader(partial_site['url'], str(output), max_retries=0, no_proxy=True).download()
    for number in range(2):
        exported = Path(M3U8Downloader(partial_site['url'], str(output), no_proxy=True).export_partial())
        validate_media_file(str(exported))
        assert exported.name == ('fragmented_内容不完整.mp4' if number == 0 else 'fragmented_内容不完整_02.mp4')
    assert partial_site['requests']['/init.mp4'] == 1
    assert not output.exists()


@pytest.mark.parametrize('confirm', [False, True], ids=['cancel', 'export'])
def test_gui_partial_export_requires_confirmation_and_keeps_failed_status(
    partial_site, tmp_path, media_segment, qtbot, confirm,
):
    from PySide6.QtCore import QTimer, Qt
    from PySide6.QtWidgets import QApplication, QMenu, QMessageBox
    from m3u8_downloader.gui_v2 import MainWindow
    from m3u8_downloader.background_v2 import TaskBackgroundController
    from m3u8_downloader.downloader_adapter_v2 import ExistingDownloaderAdapter
    from m3u8_downloader.tasking import (
        TaskService, SQLiteTaskRepository, CreateTaskRequest, OutputPlanner,
        DownloadCoordinator, ItemStatus, DownloadStatus,
    )

    partial_site['segment'] = media_segment
    service = TaskService(SQLiteTaskRepository(tmp_path / 'tasks.db'))
    task = service.create_tasks(CreateTaskRequest(addresses=partial_site['url'], save_directory=str(tmp_path)))[0]
    service.prepare_output_paths(task.id, OutputPlanner())
    item = service.list_items(task.id)[0]
    with pytest.raises(RuntimeError):
        M3U8Downloader(item.source_url, item.output_path, max_retries=0, no_proxy=True).download()
    service.fail_item(task.id, item.id, '分片下载失败')
    window = MainWindow(service)
    window._force_exit = True
    qtbot.addWidget(window)
    window.show()
    window.task_list.setCurrentRow(0)
    controller = TaskBackgroundController(
        service, extraction_coordinator=object(),
        download_coordinator=DownloadCoordinator(service, downloader=ExistingDownloaderAdapter()),
        parent=window,
    )
    window.background_controller = controller
    seen = {'done': False, 'menu': [], 'confirmation': ''}

    def interact():
        menu = QApplication.activePopupWidget()
        if isinstance(menu, QMenu):
            seen['menu'] = [action.text() for action in menu.actions()]
            more = next(action.menu() for action in menu.actions() if action.text() == '更多操作')
            action = next(action for action in more.actions() if action.text() == '合并已下载片段')
            menu.close()
            action.trigger()
        box = QApplication.activeModalWidget()
        if isinstance(box, QMessageBox) and box.windowTitle() == '合并已下载片段':
            seen['confirmation'] = box.text()
            assert box.defaultButton() == box.button(QMessageBox.StandardButton.No)
            box.button(QMessageBox.StandardButton.Yes if confirm else QMessageBox.StandardButton.No).click()
            if not confirm:
                seen['done'] = True
        elif isinstance(box, QMessageBox) and box.windowTitle() == '不完整文件已导出':
            assert '内容不完整' in box.text()
            box.button(QMessageBox.StandardButton.Ok).click()
            seen['done'] = True

    timer = QTimer(window)
    timer.timeout.connect(interact)
    timer.start(10)
    try:
        cell = window.item_table.item(0, 0)
        assert cell is not None
        window.item_table.customContextMenuRequested.emit(window.item_table.visualItemRect(cell).center())
        qtbot.waitUntil(lambda: seen['done'], timeout=15000)
    finally:
        timer.stop()
        controller.stop()
    assert '重试' in seen['menu']
    assert '2 / 3' in seen['confirmation']
    assert '00:01' in seen['confirmation']
    assert service.get_item(task.id, item.id).status is ItemStatus.FAILED
    assert service.get_task(task.id).download_status is DownloadStatus.PARTIAL_FAILURE
    assert len(list(tmp_path.glob('*内容不完整*.mp4'))) == int(confirm)
    assert len(list((tmp_path / '.tmp').rglob('seg_*.ts'))) == 2
