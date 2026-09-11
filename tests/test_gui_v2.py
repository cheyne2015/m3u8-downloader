"""新版主界面的用户可见行为测试。"""

from dataclasses import replace

from PySide6.QtCore import Qt
from PySide6.QtGui import QPalette

from m3u8_downloader.gui_v2 import MainWindow
from m3u8_downloader.tasking import (
    Candidate,
    CreateTaskRequest,
    DownloadStatus,
    ExtractionStatus,
    ItemStatus,
    SQLiteTaskRepository,
    TaskService,
)


def test_new_link_dialog_adds_tasks_to_downloading_view(qtbot, tmp_path):
    repository = SQLiteTaskRepository(tmp_path / "tasks.db")
    service = TaskService(repository, id_factory=iter(["page", "direct"]).__next__)
    window = MainWindow(service)
    qtbot.addWidget(window)
    window.show()

    assert window.downloading_button.text() == "下载中"
    assert window.completed_button.text() == "已完成"
    assert window.settings_button.text() == "设置"
    assert window.log_panel.minimumHeight() > 0

    qtbot.mouseClick(window.new_task_button, Qt.MouseButton.LeftButton)
    dialog = window.new_task_dialog
    dialog.address_edit.setPlainText(
        "https://site.example/watch/42\nhttps://cdn.example/video.m3u8"
    )
    dialog.directory_edit.setText(str(tmp_path / "downloads"))
    qtbot.mouseClick(dialog.start_button, Qt.MouseButton.LeftButton)

    assert window.task_list.count() == 2
    assert window.task_list.item(0).data(Qt.ItemDataRole.UserRole).name == "正在获取标题"
    assert "等待提取" in window.task_list.itemWidget(window.task_list.item(0)).status_label.text()
    assert window.task_list.item(1).data(Qt.ItemDataRole.UserRole).name == "video"
    assert "等待下载" in window.task_list.itemWidget(window.task_list.item(1)).status_label.text()
    window.task_list.setCurrentRow(1)
    assert window.item_table.rowCount() == 1
    assert window.item_table.item(0, 0).text() == "video"
    assert window.item_table.item(0, 2).text() == "等待下载"
    window._force_exit = True


def test_task_detail_is_empty_until_a_parent_task_is_selected(qtbot, tmp_path):
    service = TaskService(SQLiteTaskRepository(tmp_path / "tasks.db"), id_factory=lambda: "parent")
    task = service.create_tasks(CreateTaskRequest(
        addresses="https://cdn.example/video.m3u8",
        save_directory=str(tmp_path),
    ))[0]
    window = MainWindow(service)
    qtbot.addWidget(window)
    window.show()
    window._force_exit = True

    assert window.task_list.currentItem() is None
    assert window.detail_title.text() == ""
    assert window.item_table.rowCount() == 0
    assert window.info_view.toPlainText() == ""

    window.task_list.setCurrentRow(0)
    assert window.detail_title.text() == task.name
    assert window.item_table.rowCount() == 1
    assert task.source_url in window.info_view.toPlainText()

    window.task_list.clearSelection()
    assert window.detail_title.text() == ""
    assert window.item_table.rowCount() == 0
    assert window.info_view.toPlainText() == ""
    assert window._detail_task_id == ""


def test_detail_clears_when_selected_task_moves_to_completed_view(qtbot, tmp_path):
    service = TaskService(SQLiteTaskRepository(tmp_path / "tasks.db"), id_factory=lambda: "parent")
    task = service.create_tasks(CreateTaskRequest(
        addresses="https://cdn.example/video.m3u8",
        save_directory=str(tmp_path),
    ))[0]
    item = service.list_items(task.id)[0]
    window = MainWindow(service)
    qtbot.addWidget(window)
    window.show()
    window._force_exit = True
    window.task_list.setCurrentRow(0)

    output = tmp_path / "video.mp4"
    output.write_bytes(b"video")
    service.complete_item(task.id, item.id, output)
    service.finish_parent_if_handled(task.id)
    window.refresh_tasks()

    assert window.task_list.currentItem() is None
    assert window.completed_list.currentItem() is None
    assert window.completed_list.count() == 1
    assert window.detail_title.text() == ""
    assert window.item_table.rowCount() == 0
    assert window.info_view.toPlainText() == ""
    assert window._detail_task_id == ""


def test_quick_start_from_completed_view_creates_and_selects_task(qtbot, tmp_path):
    repository = SQLiteTaskRepository(tmp_path / "tasks.db")
    identifiers = iter(["seed", "quick"])
    service = TaskService(repository, id_factory=identifiers.__next__)
    service.create_tasks(CreateTaskRequest(
        addresses="https://cdn.example/seed.m3u8",
        save_directory=str(tmp_path / "last-downloads"),
    ))
    window = MainWindow(service)
    qtbot.addWidget(window)
    window.show()
    window._force_exit = True

    qtbot.mouseClick(window.completed_button, Qt.MouseButton.LeftButton)
    assert window.quick_download_panel.isVisible()
    window.quick_address_edit.setText("https://site.example/watch/quick")
    qtbot.mouseClick(window.quick_start_button, Qt.MouseButton.LeftButton)

    assert window.pages.currentIndex() == 0
    assert window.quick_address_edit.text() == ""
    assert service.get_task("quick").save_directory == str(tmp_path / "last-downloads")
    assert window.task_list.currentItem().data(Qt.ItemDataRole.UserRole).id == "quick"


def test_quick_start_keeps_invalid_address_and_shows_feedback(qtbot, tmp_path):
    window = MainWindow(TaskService(SQLiteTaskRepository(tmp_path / "tasks.db")))
    qtbot.addWidget(window)
    window.show()
    window._force_exit = True
    window.quick_address_edit.setText("这不是链接")

    qtbot.mouseClick(window.quick_start_button, Qt.MouseButton.LeftButton)

    assert window.quick_address_edit.text() == "这不是链接"
    assert window.task_list.count() == 0
    assert window.feedback_label.isVisible()
    assert "有效" in window.feedback_label.text()


def test_quick_start_supports_enter_and_preserves_duplicate_address(qtbot, tmp_path):
    repository = SQLiteTaskRepository(tmp_path / "tasks.db")
    service = TaskService(repository, id_factory=iter(["first", "unused"]).__next__)
    window = MainWindow(service)
    qtbot.addWidget(window)
    window.show()
    window._force_exit = True
    address = "https://site.example/watch/enter"
    window.quick_address_edit.setText(address)

    qtbot.keyPress(window.quick_address_edit, Qt.Key.Key_Return)

    assert service.get_task("first").source_url == address
    assert window.quick_address_edit.text() == ""
    window.quick_address_edit.setText(address)
    qtbot.mouseClick(window.quick_start_button, Qt.MouseButton.LeftButton)
    assert len(service.list_tasks()) == 1
    assert window.quick_address_edit.text() == address
    assert "已存在" in window.feedback_label.text()


def test_stopped_extraction_button_can_restart_with_fresh_candidates(qtbot, tmp_path):
    repository = SQLiteTaskRepository(tmp_path / "tasks.db")
    service = TaskService(repository, id_factory=lambda: "page")
    task = service.create_tasks(CreateTaskRequest(
        addresses="https://site.example/watch/42",
        save_directory=str(tmp_path),
    ))[0]
    service.start_extraction(task.id)
    service.add_candidates(task.id, [Candidate("https://cdn.example/old.m3u8")])
    window = MainWindow(service)
    qtbot.addWidget(window)
    window.show()
    window._force_exit = True
    window.task_list.setCurrentRow(0)

    assert window.stop_extraction_button.text() == "停止提取"
    qtbot.mouseClick(window.stop_extraction_button, Qt.MouseButton.LeftButton)
    assert window.stop_extraction_button.text() == "重新提取"
    assert window.stop_extraction_button.isEnabled()

    qtbot.mouseClick(window.stop_extraction_button, Qt.MouseButton.LeftButton)

    assert service.get_task(task.id).extraction_status is ExtractionStatus.WAITING
    assert service.list_items(task.id) == []
    assert "重新开始" in window.feedback_label.text()


def test_log_panel_starts_taller_without_changing_minimum(qtbot, tmp_path):
    window = MainWindow(TaskService(SQLiteTaskRepository(tmp_path / "tasks.db")))
    qtbot.addWidget(window)
    window.show()
    window._force_exit = True
    qtbot.wait(20)

    assert window.log_panel.minimumHeight() == 105
    assert window.log_panel.height() >= 210


def test_light_settings_automatic_controls_are_readable(qtbot, tmp_path):
    repository = SQLiteTaskRepository(tmp_path / "tasks.db")
    service = TaskService(repository)
    settings = service.load_app_settings()
    service.save_app_settings(replace(settings, theme="light"))
    window = MainWindow(service)
    qtbot.addWidget(window)
    window.show()
    window._force_exit = True
    qtbot.mouseClick(window.settings_button, Qt.MouseButton.LeftButton)
    qtbot.wait(20)

    for spin in [
        window.threshold_spin,
        window.request_retries_spin,
        window.task_retries_spin,
        window.retry_delay_spin,
    ]:
        assert spin.height() >= 28
        text = spin.palette().color(QPalette.ColorRole.Text)
        base = spin.palette().color(QPalette.ColorRole.Base)
        assert abs(text.lightness() - base.lightness()) >= 80


def test_buttons_expose_pointer_disabled_and_action_feedback(qtbot, tmp_path):
    window = MainWindow(TaskService(SQLiteTaskRepository(tmp_path / "tasks.db")))
    qtbot.addWidget(window)
    window.show()
    window._force_exit = True

    assert window.new_task_button.cursor().shape() is Qt.CursorShape.PointingHandCursor
    assert window.stop_extraction_button.cursor().shape() is Qt.CursorShape.ArrowCursor

    qtbot.mouseClick(window.pause_task_button, Qt.MouseButton.LeftButton)
    assert window.feedback_label.text() == "请先选择一个下载任务"

    qtbot.mouseClick(window.settings_button, Qt.MouseButton.LeftButton)
    qtbot.mouseClick(window.save_settings_button, Qt.MouseButton.LeftButton)

    assert window.feedback_label.isVisible()
    assert window.feedback_label.text() == "设置已保存"


def test_window_uses_multi_size_web_extraction_icon(qtbot, tmp_path):
    window = MainWindow(TaskService(SQLiteTaskRepository(tmp_path / "tasks.db")))
    qtbot.addWidget(window)
    window._force_exit = True

    assert not window.windowIcon().isNull()
    sizes = {(size.width(), size.height()) for size in window.windowIcon().availableSizes()}
    assert {(16, 16), (32, 32), (256, 256)} <= sizes


def test_same_duration_smaller_candidate_shows_why_it_is_not_selectable(qtbot, tmp_path):
    repository = SQLiteTaskRepository(tmp_path / "tasks.db")
    service = TaskService(repository, id_factory=lambda: "page")
    task = service.create_tasks(CreateTaskRequest(
        addresses="https://site.example/watch/42", save_directory=str(tmp_path)
    ))[0]
    service.add_candidates(task.id, [
        Candidate("https://cdn.example/small.m3u8", estimated_bytes=100, duration_seconds=60),
        Candidate("https://cdn.example/large.m3u8", estimated_bytes=900, duration_seconds=60),
    ])
    service.finish_extraction(task.id)
    window = MainWindow(service)
    qtbot.addWidget(window)
    window.show()
    window._force_exit = True
    window.task_list.setCurrentRow(0)

    assert window.item_table.item(0, 2).text() == "同时间已保留更大项"
    assert not (
        window.item_table.item(0, 0).flags() & Qt.ItemFlag.ItemIsUserCheckable
    )
    assert window.item_table.item(1, 0).checkState() is Qt.CheckState.Checked


def test_settings_page_saves_concurrency_threshold_and_notification(qtbot, tmp_path):
    repository = SQLiteTaskRepository(tmp_path / "tasks.db")
    window = MainWindow(TaskService(repository))
    qtbot.addWidget(window)
    window.show()

    qtbot.mouseClick(window.settings_button, Qt.MouseButton.LeftButton)
    assert window.download_limit_spin.value() == 3
    assert window.extraction_limit_spin.value() == 3
    assert window.threshold_spin.value() == 3
    assert window.segment_threads_spin.value() == 8
    assert window.notification_check.isChecked() is False

    window.download_limit_spin.setValue(5)
    window.threshold_spin.setValue(8)
    window.notification_check.setChecked(True)
    qtbot.mouseClick(window.save_settings_button, Qt.MouseButton.LeftButton)

    saved = repository.load_app_settings()
    assert saved.download_task_limit == 5
    assert saved.auto_download_threshold == 8
    assert saved.completion_notification is True
    window._force_exit = True


def test_pending_task_can_queue_checked_download_items(qtbot, tmp_path):
    repository = SQLiteTaskRepository(tmp_path / "tasks.db")
    service = TaskService(repository, id_factory=lambda: "page")
    task = service.create_tasks(CreateTaskRequest(
        addresses="https://site.example/watch/42", save_directory=str(tmp_path)
    ))[0]
    service.add_candidates(task.id, [
        Candidate(f"https://cdn.example/{index}.m3u8", label=f"线路 {index}")
        for index in range(1, 5)
    ])
    service.finish_extraction(task.id)
    window = MainWindow(service)
    qtbot.addWidget(window)
    window.show()
    window.task_list.setCurrentRow(0)

    window.item_table.item(0, 0).setCheckState(Qt.CheckState.Checked)
    window.item_table.item(2, 0).setCheckState(Qt.CheckState.Checked)
    qtbot.mouseClick(window.download_selected_button, Qt.MouseButton.LeftButton)

    assert service.get_task(task.id).download_status is DownloadStatus.WAITING
    assert [item.status for item in service.list_items(task.id)] == [
        ItemStatus.WAITING,
        ItemStatus.UNSELECTED,
        ItemStatus.WAITING,
        ItemStatus.UNSELECTED,
    ]
    window._force_exit = True


def test_live_refresh_preserves_unsubmitted_candidate_checks(qtbot, tmp_path):
    repository = SQLiteTaskRepository(tmp_path / "tasks.db")
    service = TaskService(repository, id_factory=lambda: "page")
    task = service.create_tasks(CreateTaskRequest(
        addresses="https://site.example/watch/42", save_directory=str(tmp_path)
    ))[0]
    service.add_candidates(task.id, [
        Candidate(f"https://cdn.example/{index}.m3u8", valid=index != 4)
        for index in range(1, 5)
    ])
    service.finish_extraction(task.id)
    window = MainWindow(service)
    qtbot.addWidget(window)
    window.show()
    window.task_list.setCurrentRow(0)

    window.item_table.item(1, 0).setCheckState(Qt.CheckState.Checked)
    window.refresh_tasks()

    window._force_exit = True
    assert window.item_table.item(1, 0).checkState() is Qt.CheckState.Checked
    assert not (window.item_table.item(3, 0).flags() & Qt.ItemFlag.ItemIsUserCheckable)
