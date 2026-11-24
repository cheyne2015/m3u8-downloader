from dataclasses import replace

import pytest
from PySide6.QtCore import Qt

from m3u8_downloader.gui_v2 import MainWindow
from m3u8_downloader.tasking import (
    Candidate, CreateTaskRequest, DownloadStatus, ItemStatus,
    SQLiteTaskRepository, TaskService, TaskSettings,
)


@pytest.fixture
def batch_window(qtbot, tmp_path):
    service = TaskService(SQLiteTaskRepository(tmp_path / 'tasks.db'))
    tasks = service.create_tasks(CreateTaskRequest(
        addresses='https://example.test/one\nhttps://example.test/two',
        save_directory=str(tmp_path), settings=TaskSettings(auto_download_threshold=1),
    ))
    for task in tasks:
        service.add_candidates(task.id, [
            Candidate(f'https://cdn.test/{task.id}/{i}.m3u8', estimated_bytes=100+i,
                      duration_seconds=60+i) for i in range(3)
        ])
        service.finish_extraction(task.id)
    window = MainWindow(service)
    window._force_exit = True
    qtbot.addWidget(window)
    window.show()
    window.task_list.setCurrentRow(0)
    return window, service, tasks


def test_candidates_default_to_checked_and_manual_uncheck_survives_refresh(batch_window):
    window, service, tasks = batch_window
    assert [window.item_table.item(i, 0).checkState() for i in range(3)] == [Qt.CheckState.Checked]*3
    window.item_table.item(0, 0).setCheckState(Qt.CheckState.Unchecked)
    task_id = window._detail_task_id
    service.apply_page_title(task_id, '更新的网页标题')
    window.refresh_tasks()
    assert window.item_table.item(0, 0).checkState() is Qt.CheckState.Unchecked


def test_master_checkbox_and_download_apply_to_all_selected_parents(batch_window, qtbot):
    window, service, tasks = batch_window
    for row in range(window.task_list.count()):
        window.task_list.item(row).setSelected(True)
    qtbot.mouseClick(window.select_all_checkbox, Qt.MouseButton.LeftButton)
    for row in range(window.task_list.count()):
        window.task_list.selectionModel().setCurrentIndex(
            window.task_list.indexFromItem(window.task_list.item(row)),
            window.task_list.selectionModel().SelectionFlag.NoUpdate,
        )
        window.task_list.currentItemChanged.emit(window.task_list.item(row), None)
        assert all(window.item_table.item(i, 0).checkState() is Qt.CheckState.Unchecked for i in range(3))
    qtbot.mouseClick(window.select_all_checkbox, Qt.MouseButton.LeftButton)
    qtbot.mouseClick(window.download_selected_button, Qt.MouseButton.LeftButton)
    assert all(service.get_task(task.id).download_status is DownloadStatus.WAITING for task in tasks)
    assert all(item.status is ItemStatus.WAITING for task in tasks for item in service.list_items(task.id))


def test_unknown_rule_skips_only_when_both_estimates_are_missing(tmp_path):
    service = TaskService(SQLiteTaskRepository(tmp_path / 'tasks.db'))
    task = service.create_tasks(CreateTaskRequest(
        addresses='https://example.test/watch', save_directory=str(tmp_path),
        settings=TaskSettings(skip_unknown_estimates=True),
    ))[0]
    service.add_candidates(task.id, [
        Candidate('https://cdn.test/unknown.m3u8'),
        Candidate('https://cdn.test/size.m3u8', estimated_bytes=100),
        Candidate('https://cdn.test/duration.m3u8', duration_seconds=60),
    ])
    result = service.finish_extraction(task.id)
    items = service.list_items(task.id)
    assert [item.status for item in items] == [ItemStatus.SKIPPED, ItemStatus.WAITING, ItemStatus.WAITING]
    assert result.download_status is DownloadStatus.WAITING
    service.queue_items_for_download(task.id, [items[0].id], replace_selection=False)
    assert service.get_item(task.id, items[0].id).status is ItemStatus.WAITING


def test_live_speed_drops_to_zero_when_progress_stops():
    from m3u8_downloader.resource_stats import DownloadSpeedTracker
    from m3u8_downloader.tasking import DownloadItem
    now = [0.0]
    tracker = DownloadSpeedTracker(clock=lambda: now[0], idle_seconds=2)
    item = DownloadItem(id='item', task_id='task', source_url='https://cdn.test/a.m3u8',
                        label='', output_index=1, status=ItemStatus.DOWNLOADING,
                        downloaded_bytes=100, speed_bps=4096)
    assert tracker.update([item])['item'] == 4096
    now[0] = 2.1
    assert tracker.update([item])['item'] == 0
    item = replace(item, downloaded_bytes=200)
    assert tracker.update([item])['item'] == 4096
    assert tracker.update([replace(item, speed_bps=0)])['item'] == 0


def test_global_title_clears_stale_speed(batch_window, qtbot):
    window, service, tasks = batch_window
    item = service.list_items(tasks[0].id)[0]
    service.start_item(tasks[0].id, item.id)
    service.update_item_progress(tasks[0].id, item.id, {'downloaded': 100, 'speed': 4096})
    window.refresh_tasks()
    assert not window.page_title.text().startswith('0 ')
    qtbot.wait(2100)
    window.refresh_tasks()
    assert window.page_title.text().startswith('0 ')


def test_refresh_failed_links_preserves_completed_files_and_item_identity(tmp_path):
    from m3u8_downloader.tasking import TaskCoordinator
    service = TaskService(SQLiteTaskRepository(tmp_path / 'tasks.db'))
    task = service.create_tasks(CreateTaskRequest(
        addresses='https://example.test/watch', save_directory=str(tmp_path),
        settings=TaskSettings(extraction_mode='normal'),
    ))[0]
    service.add_candidates(task.id, [Candidate('https://cdn.test/a.m3u8?token=old'),
                                     Candidate('https://cdn.test/b.m3u8?token=old')])
    service.finish_extraction(task.id)
    completed, failed = service.list_items(task.id)
    output = tmp_path / 'completed.mp4'
    output.write_bytes(b'keep-existing-file')
    service.complete_item(task.id, completed.id, output)
    service.fail_item(task.id, failed.id, '链接失效')
    targets = service.prepare_failed_recovery(task.id)
    service = TaskService(SQLiteTaskRepository(tmp_path / 'tasks.db'))

    class Extractor:
        def extract(self, task, **kwargs):
            kwargs['on_title']('不应改变原任务名称')
            return [Candidate('https://cdn.test/a.m3u8?token=new'),
                    Candidate('https://cdn.test/b.m3u8?token=new'),
                    Candidate('https://cdn.test/unrelated.m3u8')]

    result = TaskCoordinator(service, extractor=Extractor()).run_extraction(task.id)
    items = service.list_items(task.id)
    assert len(items) == 2 and items[0] == service.get_item(task.id, completed.id)
    assert items[0].status is ItemStatus.COMPLETED and items[0].source_url.endswith('token=old')
    assert items[1].id == failed.id and items[1].source_url.endswith('b.m3u8?token=new')
    assert items[1].status is ItemStatus.WAITING
    assert output.read_bytes() == b'keep-existing-file'
    assert result.name == task.name and result.download_status is DownloadStatus.WAITING


def test_refresh_does_not_match_unknown_items_by_position(tmp_path):
    service = TaskService(SQLiteTaskRepository(tmp_path / 'tasks.db'))
    task = service.create_tasks(CreateTaskRequest(
        addresses='https://example.test/watch', save_directory=str(tmp_path),
    ))[0]
    service.add_candidates(task.id, [Candidate('https://cdn.test/a.m3u8'), Candidate('https://cdn.test/b.m3u8')])
    service.finish_extraction(task.id)
    for item in service.list_items(task.id):
        service.fail_item(task.id, item.id, '失败')
    targets = service.prepare_failed_recovery(task.id)
    service.apply_failed_recovery(task.id, [Candidate('https://new.test/x.m3u8'),
                                         Candidate('https://new.test/y.m3u8')], targets)
    assert all(item.status is ItemStatus.FAILED for item in service.list_items(task.id))
    assert len(service.list_items(task.id)) == 2


def test_refresh_without_new_candidates_does_not_reuse_old_successes(tmp_path):
    from m3u8_downloader.tasking import TaskCoordinator
    service = TaskService(SQLiteTaskRepository(tmp_path / 'tasks.db'))
    task = service.create_tasks(CreateTaskRequest(
        addresses='https://example.test/watch', save_directory=str(tmp_path),
        settings=TaskSettings(extraction_mode='normal'),
    ))[0]
    service.add_candidates(task.id, [Candidate('https://cdn.test/a.m3u8'), Candidate('https://cdn.test/b.m3u8')])
    service.finish_extraction(task.id)
    a, b = service.list_items(task.id)
    service.complete_item(task.id, a.id, tmp_path / 'done.mp4')
    service.fail_item(task.id, b.id, '失败')
    service.prepare_failed_recovery(task.id)
    class Empty:
        def extract(self, *args, **kwargs):
            return []
    with pytest.raises(RuntimeError):
        TaskCoordinator(service, extractor=Empty(), retry_wait=lambda *_: False).run_extraction(task.id)
    assert service.get_item(task.id, a.id).status is ItemStatus.COMPLETED
    assert service.get_item(task.id, b.id).status is ItemStatus.FAILED


def test_background_refresh_then_downloads_only_the_failed_item(qtbot, tmp_path):
    from m3u8_downloader.background_v2 import TaskBackgroundController
    from m3u8_downloader.tasking import TaskCoordinator, DownloadCoordinator
    service = TaskService(SQLiteTaskRepository(tmp_path / 'tasks.db'))
    task = service.create_tasks(CreateTaskRequest(
        addresses='https://example.test/watch', save_directory=str(tmp_path),
        settings=TaskSettings(extraction_mode='normal'),
    ))[0]
    service.add_candidates(task.id, [Candidate('https://cdn.test/a.m3u8?token=old'),
                                     Candidate('https://cdn.test/b.m3u8?token=old')])
    service.finish_extraction(task.id)
    a, b = service.list_items(task.id)
    output = tmp_path / 'keep.mp4'
    output.write_bytes(b'keep')
    service.complete_item(task.id, a.id, output)
    service.fail_item(task.id, b.id, '失败')
    class Extractor:
        def extract(self, *args, **kwargs):
            return [Candidate('https://cdn.test/a.m3u8?token=new'), Candidate('https://cdn.test/b.m3u8?token=new')]
    downloaded = []
    class Downloader:
        def download(self, task, item, path, **kwargs):
            downloaded.append(item.id)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b'new-file')
            return path
    controller = TaskBackgroundController(service,
        extraction_coordinator=TaskCoordinator(service, extractor=Extractor()),
        download_coordinator=DownloadCoordinator(service, downloader=Downloader()), poll_interval_ms=10)
    completed = []
    controller.parent_completed.connect(lambda *args: completed.append(args))
    try:
        controller.start()
        controller.refresh_failed_links(task.id)
        qtbot.waitUntil(lambda: bool(completed), timeout=5000)
        assert downloaded == [b.id]
        assert output.read_bytes() == b'keep'
        assert service.get_task(task.id).download_status is DownloadStatus.COMPLETED
    finally:
        controller.stop()
