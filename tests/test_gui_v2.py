"""新版主界面的用户可见行为测试。"""

from PySide6.QtCore import Qt

from m3u8_downloader.gui_v2 import MainWindow
from m3u8_downloader.tasking import (
    Candidate,
    CreateTaskRequest,
    DownloadStatus,
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
    assert window.task_list.item(0).text() == "正在获取标题"
    assert "等待提取" in window.task_list.itemWidget(window.task_list.item(0)).status_label.text()
    assert window.task_list.item(1).text() == "video"
    assert "等待下载" in window.task_list.itemWidget(window.task_list.item(1)).status_label.text()
    window.task_list.setCurrentRow(1)
    assert window.item_table.rowCount() == 1
    assert window.item_table.item(0, 0).text() == "video"
    assert window.item_table.item(0, 2).text() == "等待下载"
    window._force_exit = True


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
