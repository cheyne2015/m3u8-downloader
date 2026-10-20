"""在真实 Qt 菜单上验证状态、分组和批量操作。"""

from pathlib import Path

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QMenu

from m3u8_downloader.gui_v2 import MainWindow
from m3u8_downloader.tasking import (
    Candidate, CreateTaskRequest, DownloadStatus, ExtractionStatus, ItemStatus, OutputPlanner,
    SQLiteTaskRepository, TaskService,
)


@pytest.fixture
def menu_window(qtbot, tmp_path):
    service = TaskService(SQLiteTaskRepository(tmp_path / 'tasks.db'))
    window = MainWindow(service)
    window._force_exit = True
    qtbot.addWidget(window)
    window.show()
    return window, service


def capture_menu(monkeypatch, action=None):
    captured = []

    class CapturedMenu(QMenu):
        def exec(self, *_args):
            captured.extend(self.actions())
            def entries(menu):
                actions = menu.actions()
                assert actions and not actions[0].isSeparator() and not actions[-1].isSeparator()
                assert not any(left.isSeparator() and right.isSeparator()
                               for left, right in zip(actions, actions[1:]))
                for entry in actions:
                    yield entry
                    if entry.menu() is not None:
                        yield from entries(entry.menu())
            all_entries = list(entries(self))
            if action:
                next(entry for entry in all_entries if entry.text() == action).trigger()

    monkeypatch.setattr('m3u8_downloader.gui_v2.QMenu', CapturedMenu)
    return captured


@pytest.mark.parametrize('state, present, absent', [
    ('waiting', {'暂停', '停止提取'}, {'继续', '重新提取'}),
    ('paused', {'继续', '重新提取'}, {'暂停', '停止提取'}),
    ('failed', {'重新提取'}, {'继续', '暂停', '停止提取'}),
])
def test_single_parent_menu_only_offers_applicable_controls(
    menu_window, tmp_path, monkeypatch, state, present, absent,
):
    window, service = menu_window
    task = service.create_tasks(CreateTaskRequest(
        addresses='https://example.test/watch', save_directory=str(tmp_path),
    ))[0]
    if state == 'paused':
        service.pause_task(task.id)
    elif state == 'failed':
        service.fail_extraction(task.id, '测试失败')
    window.refresh_tasks()
    actions = capture_menu(monkeypatch)
    window._show_task_menu(window.task_list, window.task_list.visualItemRect(
        window.task_list.item(0)).center())
    labels = {action.text() for action in actions}
    assert present <= labels
    assert not absent & labels
    assert {'更多操作', '删除任务'} <= labels
    assert not {'查看下载项', '查看详情', '任务设置', '调整队列'} & labels
    more = next(action.menu() for action in actions if action.text() == '更多操作')
    assert {'任务设置', '移到最前', '上移', '下移', '移到最后'} <= {
        action.text() for action in more.actions()
    }
    assert all(action.menu() is None for action in more.actions())
    delete_index = next(i for i, action in enumerate(actions) if action.text() == '删除任务')
    assert actions[delete_index - 1].isSeparator()


def test_multi_menu_preserves_selection_and_copies_all_links(
    menu_window, tmp_path, monkeypatch,
):
    window, service = menu_window
    tasks = service.create_tasks(CreateTaskRequest(
        addresses='https://example.test/one\nhttps://example.test/two',
        save_directory=str(tmp_path),
    ))
    for task in tasks:
        service.fail_extraction(task.id, '测试失败')
    window.refresh_tasks()
    for index in range(2):
        window.task_list.item(index).setSelected(True)
    actions = capture_menu(monkeypatch, '复制全部原始链接')
    window._show_task_menu(window.task_list, window.task_list.visualItemRect(
        window.task_list.item(0)).center())
    assert len(window.task_list.selectedItems()) == 2
    assert set(QApplication.clipboard().text().splitlines()) == {task.source_url for task in tasks}
    next(action for action in actions if action.text() == '重新提取').trigger()
    assert all(service.get_task(task.id).extraction_status is ExtractionStatus.WAITING for task in tasks)


def test_right_click_unselected_parent_targets_only_that_task(
    menu_window, tmp_path, monkeypatch,
):
    window, service = menu_window
    service.create_tasks(CreateTaskRequest(
        addresses='https://example.test/one\nhttps://example.test/two\nhttps://example.test/three',
        save_directory=str(tmp_path),
    ))
    window.refresh_tasks()
    for index in (0, 1):
        window.task_list.item(index).setSelected(True)
    capture_menu(monkeypatch)
    target = window.task_list.item(2)
    window._show_task_menu(window.task_list, window.task_list.visualItemRect(target).center())
    assert window.task_list.selectedItems() == [target]


def test_missing_completed_file_offers_relink_without_rename(
    menu_window, tmp_path, monkeypatch,
):
    window, service = menu_window
    task = service.create_tasks(CreateTaskRequest(
        addresses='https://example.test/media.m3u8', save_directory=str(tmp_path),
    ))[0]
    item = service.list_items(task.id)[0]
    service.complete_item(task.id, item.id, tmp_path / 'missing.mp4')
    service.finish_parent_if_handled(task.id)
    window.refresh_tasks()
    window._switch_view(1)
    window.completed_list.setCurrentRow(0)
    actions = capture_menu(monkeypatch)
    window._show_item_menu(window.item_table.visualItemRect(window.item_table.item(0, 0)).center())
    labels = {action.text() for action in actions}
    assert {'重新关联文件', '重新下载', '复制 m3u8 链接'} <= labels
    assert not {'打开文件', '定位文件', '重命名文件'} & labels


@pytest.mark.parametrize('state, control, advanced', [
    ('unselected', '下载', None),
    ('waiting', '暂停', '跳过'),
    ('downloading', '暂停', '跳过'),
    ('retry_wait', '暂停', '跳过'),
    ('paused', '继续', None),
    ('failed', '重试', '合并已下载片段'),
    ('skipped', '重试', None),
    ('completed', '重新下载', '校验并修复'),
])
def test_item_menu_controls_and_advanced_actions_are_separate(
    menu_window, tmp_path, monkeypatch, state, control, advanced,
):
    window, service = menu_window
    task = service.create_tasks(CreateTaskRequest(
        addresses='https://example.test/media.m3u8', save_directory=str(tmp_path),
    ))[0]
    item = service.prepare_output_paths(task.id, OutputPlanner())[0]
    if state == 'unselected':
        service.select_items_for_download(task.id, [])
    elif state == 'downloading':
        service.start_item(task.id, item.id)
    elif state == 'retry_wait':
        service.schedule_item_retry(task.id, item.id, '暂时失败')
    elif state == 'paused':
        service.pause_item(task.id, item.id)
    elif state == 'failed':
        service.fail_item(task.id, item.id, '测试失败')
    elif state == 'skipped':
        service.skip_item(task.id, item.id)
        service.finish_parent_if_handled(task.id)
    elif state == 'completed':
        output = tmp_path / 'complete.mp4'
        output.write_bytes(b'fixture')
        service.complete_item(task.id, item.id, output)
        service.finish_parent_if_handled(task.id)
    window.refresh_tasks()
    if service.get_task(task.id).download_status is DownloadStatus.COMPLETED:
        window._switch_view(1)
        window.completed_list.setCurrentRow(0)
    else:
        window.task_list.setCurrentRow(0)
    actions = capture_menu(monkeypatch)
    cell = window.item_table.item(0, 0)
    window.item_table.customContextMenuRequested.emit(window.item_table.visualItemRect(cell).center())
    labels = {entry.text() for entry in actions}
    assert actions[0].text() == control
    assert actions[-1].text() == '复制 m3u8 链接'
    assert not {'跳过', '合并已下载片段', '校验并修复'} & labels
    if advanced:
        more = next(entry.menu() for entry in actions if entry.text() == '更多操作')
        assert [entry.text() for entry in more.actions()] == [advanced]
    else:
        assert '更多操作' not in labels
    if state == 'completed':
        assert {'打开文件', '定位文件', '重命名文件'} <= labels


def test_completed_parent_leaves_file_operations_to_download_items(
    menu_window, tmp_path, monkeypatch,
):
    window, service = menu_window
    task = service.create_tasks(CreateTaskRequest(
        addresses='https://example.test/media.m3u8', save_directory=str(tmp_path),
    ))[0]
    item = service.list_items(task.id)[0]
    output = tmp_path / 'complete.mp4'
    output.write_bytes(b'fixture')
    service.complete_item(task.id, item.id, output)
    service.finish_parent_if_handled(task.id)
    window.refresh_tasks()
    window._switch_view(1)
    actions = capture_menu(monkeypatch)
    window.completed_list.customContextMenuRequested.emit(window.completed_list.visualItemRect(
        window.completed_list.item(0)).center())
    labels = {entry.text() for entry in actions}
    assert {'重新下载', '定位文件', '重命名任务', '复制原始链接', '更多操作'} <= labels
    assert not {'打开文件', '打开保存位置', '重命名文件', '合并已下载片段',
                '查看详情', '查看下载项', '校验并修复'} & labels
    more = next(entry.menu() for entry in actions if entry.text() == '更多操作')
    assert [entry.text() for entry in more.actions()] == ['校验并修复']


@pytest.mark.parametrize('action', ['暂停', '继续', '重新提取', '重试失败项'])
def test_mixed_selection_only_changes_eligible_selected_tasks(
    menu_window, tmp_path, monkeypatch, action,
):
    window, service = menu_window
    waiting, failed, paused, partial, outside = service.create_tasks(CreateTaskRequest(
        addresses='\n'.join([
            'https://example.test/waiting', 'https://example.test/failed',
            'https://example.test/paused.m3u8', 'https://example.test/partial.m3u8',
            'https://example.test/outside',
        ]), save_directory=str(tmp_path),
    ))
    service.fail_extraction(failed.id, '测试失败')
    service.pause_task(paused.id)
    partial_item = service.list_items(partial.id)[0]
    service.fail_item(partial.id, partial_item.id, '测试失败')
    window.refresh_tasks()
    for index in range(window.task_list.count()):
        selected = window.task_list.item(index)
        selected.setSelected(selected.data(Qt.ItemDataRole.UserRole).id != outside.id)
    capture_menu(monkeypatch, action)
    window.task_list.customContextMenuRequested.emit(window.task_list.visualItemRect(
        window.task_list.item(0)).center())
    assert service.get_task(outside.id).extraction_status is ExtractionStatus.WAITING
    if action == '暂停':
        assert service.get_task(waiting.id).extraction_status is ExtractionStatus.PAUSED
        assert service.get_task(failed.id).extraction_status is ExtractionStatus.FAILED
    elif action == '继续':
        assert service.get_task(paused.id).download_status is DownloadStatus.WAITING
        assert service.get_task(failed.id).extraction_status is ExtractionStatus.FAILED
    elif action == '重新提取':
        assert service.get_task(failed.id).extraction_status is ExtractionStatus.WAITING
        assert service.get_task(paused.id).download_status is DownloadStatus.PAUSED
    else:
        assert service.get_item(partial.id, partial_item.id).status is ItemStatus.WAITING
        assert service.get_task(failed.id).extraction_status is ExtractionStatus.FAILED


@pytest.mark.parametrize('action, expected', [
    ('移到最前', ['two', 'three', 'one', 'four']),
    ('上移', ['two', 'three', 'one', 'four']),
    ('下移', ['one', 'four', 'two', 'three']),
    ('移到最后', ['one', 'four', 'two', 'three']),
])
def test_flat_queue_actions_move_all_selected_tasks(
    qtbot, tmp_path, monkeypatch, action, expected,
):
    ids = iter(['one', 'two', 'three', 'four'])
    service = TaskService(SQLiteTaskRepository(tmp_path / 'queue.db'), id_factory=ids.__next__)
    service.create_tasks(CreateTaskRequest(
        addresses='\n'.join(f'https://example.test/{name}.m3u8'
                            for name in ['one', 'two', 'three', 'four']),
        save_directory=str(tmp_path),
    ))
    window = MainWindow(service)
    window._force_exit = True
    qtbot.addWidget(window)
    window.show()
    for index in (1, 2):
        window.task_list.item(index).setSelected(True)
    capture_menu(monkeypatch, action)
    window.task_list.customContextMenuRequested.emit(window.task_list.visualItemRect(
        window.task_list.item(1)).center())
    assert [task.id for task in service.list_tasks()] == expected


@pytest.mark.parametrize('case', ['single', 'multiple', 'partly_created', 'missing'])
def test_parent_locates_actual_output_or_falls_back_to_save_directory(
    menu_window, tmp_path, monkeypatch, case,
):
    window, service = menu_window
    task = service.create_tasks(CreateTaskRequest(
        addresses='https://example.test/watch', save_directory=str(tmp_path),
    ))[0]
    service.start_extraction(task.id)
    service.add_candidates(task.id, [Candidate('https://example.test/one.m3u8')]
                           + ([Candidate('https://example.test/two.m3u8')]
                              if case in {'multiple', 'partly_created'} else []))
    service.finish_extraction(task.id)
    items = service.prepare_output_paths(task.id, OutputPlanner())
    paths = [Path(item.output_path) for item in items]
    if case != 'missing':
        for path in (paths[:1] if case == 'partly_created' else paths):
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b'fixture')
    launches, opened = [], []
    monkeypatch.setattr('m3u8_downloader.gui_v2.os.spawnl', lambda *args: launches.append(args))
    monkeypatch.setattr('m3u8_downloader.gui_v2.os.startfile', lambda path: opened.append(path))
    window.refresh_tasks()
    capture_menu(monkeypatch, '定位文件')
    window.task_list.customContextMenuRequested.emit(window.task_list.visualItemRect(
        window.task_list.item(0)).center())
    if case == 'missing':
        assert launches == []
        assert opened == [str(tmp_path)]
    else:
        assert opened == []
        assert len(launches) == 1
        assert launches[0][-2:] == ('/select,', str(paths[0] if case == 'single' else paths[0].parent))
