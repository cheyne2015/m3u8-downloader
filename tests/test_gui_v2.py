"""新版主界面的用户可见行为测试。"""

from dataclasses import replace

import pytest

from PySide6.QtCore import QPoint, QItemSelectionModel, Qt, QTimer
from PySide6.QtGui import QPalette, QTextCursor
from PySide6.QtWidgets import (
    QApplication, QAbstractItemView, QCheckBox, QDialog, QLabel, QMessageBox,
)

from m3u8_downloader.gui_v2 import MainWindow, TaskSettingsDialog, _task_status
from m3u8_downloader.temp_files import TempScan
from m3u8_downloader.update_checker import ReleaseInfo
from m3u8_downloader.tasking import (
    Candidate,
    CreateTaskRequest,
    DownloadStatus,
    ExtractionStatus,
    ItemStatus,
    SQLiteTaskRepository,
    TaskService,
    TaskSettings,
)


def test_site_features_are_visible_and_site_learning_is_automatic(qtbot, tmp_path):
    service = TaskService(SQLiteTaskRepository(tmp_path / "tasks.db"))
    task = service.create_tasks(CreateTaskRequest(
        addresses="https://video.example/watch/1", save_directory=str(tmp_path),
    ))[0]
    service.save_site_profile(
        task.source_url, replace(task.settings, extraction_mode="deep", timeout_seconds=45)
    )
    service.record_site_extraction(
        task.id, success=True, effective_mode="deep", elapsed_seconds=2,
        candidate_count=2,
    )

    window = MainWindow(service)
    qtbot.addWidget(window); window._force_exit = True
    assert window.site_profile_table.rowCount() == 1
    assert window.site_profile_table.item(0, 0).text() == "video.example"
    assert window.site_compatibility_table.rowCount() == 1
    assert "CPU" in window.statistics_label.text()

    dialog = TaskSettingsDialog(task)
    qtbot.addWidget(dialog)
    assert not hasattr(dialog, "save_site_profile_check")
    assert dialog.extraction_mode.count() == 3


def test_main_window_first_size_fits_screen_and_uses_desktop_upper_bound(qtbot, tmp_path):
    window = MainWindow(TaskService(SQLiteTaskRepository(tmp_path / "tasks.db")))
    qtbot.addWidget(window)
    window._force_exit = True

    available = QApplication.primaryScreen().availableGeometry()
    assert 980 <= window.width() <= 1440
    assert 640 <= window.height() <= 900
    if available.width() >= 980:
        assert window.width() <= int(available.width() * 0.9)
    if available.height() >= 640:
        assert window.height() <= int(available.height() * 0.9)


def test_navigation_shows_total_parent_task_counts_independent_of_filter(qtbot, tmp_path):
    ids = iter(["waiting", "failed", "completed"])
    service = TaskService(SQLiteTaskRepository(tmp_path / "tasks.db"), id_factory=ids.__next__)
    waiting, failed, completed = service.create_tasks(CreateTaskRequest(
        addresses=(
            "https://site.example/waiting\n"
            "https://site.example/failed\n"
            "https://cdn.example/completed.m3u8"
        ),
        save_directory=str(tmp_path),
    ))
    service.fail_extraction(failed.id, "测试失败")
    completed_item = service.list_items(completed.id)[0]
    output = tmp_path / "completed.mp4"
    output.write_bytes(b"done")
    service.complete_item(completed.id, completed_item.id, output)
    service.finish_parent_if_handled(completed.id)

    window = MainWindow(service)
    qtbot.addWidget(window)
    window._force_exit = True

    assert window.downloading_button.countText() == "2"
    assert window.completed_button.countText() == "1"
    window.status_filter_combo.setCurrentIndex(window.status_filter_combo.findData("failed"))
    window.search_edit.setText("不存在的任务")
    assert window.task_list.count() == 0
    assert window.downloading_button.countText() == "2"
    assert window.completed_button.countText() == "1"


def test_settings_expose_temp_management_and_update_check(qtbot, tmp_path):
    service = TaskService(SQLiteTaskRepository(tmp_path / "tasks.db"))
    window = MainWindow(service)
    qtbot.addWidget(window)
    window._force_exit = True

    assert window.temp_status_label.text() == "尚未扫描"
    assert window.scan_temp_button.text() == "扫描"
    assert window.clean_temp_button.text() == "安全清理"
    assert window.clean_temp_button.isEnabled() is False
    assert window.update_status_label.text().startswith("当前版本：")
    assert window.auto_update_check.isChecked() is True
    assert window.check_update_button.text() == "检查更新"
    window.auto_update_check.setChecked(False)
    qtbot.mouseClick(window.save_settings_button, Qt.MouseButton.LeftButton)
    assert service.load_app_settings().check_updates_on_startup is False


def test_temp_scan_result_reports_cleanable_and_protected_sizes(qtbot, tmp_path):
    window = MainWindow(TaskService(SQLiteTaskRepository(tmp_path / "tasks.db")))
    qtbot.addWidget(window)
    window._force_exit = True

    window._temp_scan_finished(TempScan(
        total_bytes=3072,
        cleanable_bytes=2048,
        protected_bytes=1024,
        file_count=3,
        cleanable_jobs=(tmp_path / ".tmp" / ("job-" + "a" * 16 + "-" + "b" * 8),),
    ), "")

    assert "3.0 KB" in window.temp_status_label.text()
    assert "可安全清理 2.0 KB" in window.temp_status_label.text()
    assert "续传保留 1.0 KB" in window.temp_status_label.text()
    assert window.clean_temp_button.isEnabled() is True


def test_successful_update_check_records_time_and_reports_current_version(
    qtbot, tmp_path, monkeypatch,
):
    service = TaskService(SQLiteTaskRepository(tmp_path / "tasks.db"))
    window = MainWindow(service)
    qtbot.addWidget(window)
    window._force_exit = True
    messages = []
    monkeypatch.setattr(
        QMessageBox, "information",
        lambda _parent, title, message: messages.append((title, message)),
    )

    window._update_check_finished(ReleaseInfo(
        version="2.0.2",
        url="https://github.com/cheyne2015/m3u8-downloader/releases/tag/v2.0.2",
    ), "", True)

    assert service.load_app_settings().last_update_check_at
    assert window.update_status_label.text().endswith("（已是最新）")
    assert messages == [("检查更新", "当前已经是最新版本。")]


def test_navigation_count_color_follows_button_state_in_both_themes(qtbot, tmp_path):
    window = MainWindow(TaskService(SQLiteTaskRepository(tmp_path / "tasks.db")))
    qtbot.addWidget(window)
    window._force_exit = True

    window._apply_theme("dark")
    assert "#65a6ff" in window.downloading_button._count_label.styleSheet()
    assert "#e8ebef" in window.completed_button._count_label.styleSheet()

    window.downloading_button.setChecked(False)
    window.completed_button.setChecked(True)
    assert "#e8ebef" in window.downloading_button._count_label.styleSheet()
    assert "#65a6ff" in window.completed_button._count_label.styleSheet()

    window._apply_theme("light")
    assert "#20242a" in window.downloading_button._count_label.styleSheet()
    assert "#216bd6" in window.completed_button._count_label.styleSheet()


def test_active_task_cards_keep_same_height_across_extraction_and_download_states(
    qtbot, tmp_path,
):
    ids = iter(["waiting", "running", "failed", "downloading"])
    service = TaskService(SQLiteTaskRepository(tmp_path / "tasks.db"), id_factory=ids.__next__)
    waiting, running, failed, downloading = service.create_tasks(CreateTaskRequest(
        addresses=(
            "https://site.example/waiting\n"
            "https://site.example/running\n"
            "https://site.example/failed\n"
            "https://cdn.example/downloading.m3u8"
        ),
        save_directory=str(tmp_path),
    ))
    service.start_extraction(running.id)
    service.fail_extraction(failed.id, "测试失败")
    service.start_item(downloading.id, service.list_items(downloading.id)[0].id)

    window = MainWindow(service)
    qtbot.addWidget(window)
    window._force_exit = True

    heights = [window.task_list.item(row).sizeHint().height() for row in range(4)]
    assert heights == [88, 88, 88, 88]


def test_toolbar_reextracts_every_selected_failed_parent(qtbot, tmp_path):
    ids = iter(["one", "two", "other"])
    service = TaskService(SQLiteTaskRepository(tmp_path / "tasks.db"), id_factory=ids.__next__)
    one, two, other = service.create_tasks(CreateTaskRequest(
        addresses=(
            "https://site.example/one\n"
            "https://site.example/two\n"
            "https://site.example/other"
        ),
        save_directory=str(tmp_path),
    ))
    service.fail_extraction(one.id, "失败一")
    service.fail_extraction(two.id, "失败二")
    service.fail_extraction(other.id, "失败三")
    window = MainWindow(service)
    qtbot.addWidget(window)
    window.show()
    window._force_exit = True

    window.task_list.item(0).setSelected(True)
    window.task_list.item(1).setSelected(True)
    window.task_list.setCurrentItem(
        window.task_list.item(1), QItemSelectionModel.SelectionFlag.NoUpdate,
    )
    assert window.stop_extraction_button.text() == "重新提取"
    qtbot.mouseClick(window.stop_extraction_button, Qt.MouseButton.LeftButton)

    assert service.get_task(one.id).extraction_status is ExtractionStatus.WAITING
    assert service.get_task(two.id).extraction_status is ExtractionStatus.WAITING
    assert service.get_task(other.id).extraction_status is ExtractionStatus.FAILED


def test_toolbar_pause_and_resume_apply_to_every_selected_parent(qtbot, tmp_path):
    ids = iter(["one", "two", "other"])
    service = TaskService(SQLiteTaskRepository(tmp_path / "tasks.db"), id_factory=ids.__next__)
    one, two, other = service.create_tasks(CreateTaskRequest(
        addresses=(
            "https://cdn.example/one.m3u8\n"
            "https://cdn.example/two.m3u8\n"
            "https://cdn.example/other.m3u8"
        ),
        save_directory=str(tmp_path),
    ))
    window = MainWindow(service)
    qtbot.addWidget(window)
    window.show()
    window._force_exit = True
    window.task_list.item(0).setSelected(True)
    window.task_list.item(1).setSelected(True)
    window.task_list.setCurrentItem(
        window.task_list.item(1), QItemSelectionModel.SelectionFlag.NoUpdate,
    )

    qtbot.mouseClick(window.pause_task_button, Qt.MouseButton.LeftButton)
    assert service.get_task(one.id).download_status is DownloadStatus.PAUSED
    assert service.get_task(two.id).download_status is DownloadStatus.PAUSED
    assert service.get_task(other.id).download_status is DownloadStatus.WAITING

    qtbot.mouseClick(window.resume_task_button, Qt.MouseButton.LeftButton)
    assert service.get_task(one.id).download_status is DownloadStatus.WAITING
    assert service.get_task(two.id).download_status is DownloadStatus.WAITING


def test_multi_selected_failed_tasks_offer_batch_reextract_settings_and_queue(
    qtbot, tmp_path, monkeypatch,
):
    ids = iter(["one", "two"])
    service = TaskService(SQLiteTaskRepository(tmp_path / "tasks.db"), id_factory=ids.__next__)
    one, two = service.create_tasks(CreateTaskRequest(
        addresses="https://site.example/one\nhttps://site.example/two",
        save_directory=str(tmp_path),
    ))
    service.fail_extraction(one.id, "失败一")
    service.fail_extraction(two.id, "失败二")
    window = MainWindow(service)
    qtbot.addWidget(window)
    window.show()
    window._force_exit = True
    for row in range(2):
        window.task_list.item(row).setSelected(True)

    captured = {"labels": [], "callbacks": {}}

    class MenuStub:
        def __init__(self, *_args):
            pass

        def addSeparator(self):
            pass

        def addAction(self, label, callback=None):
            captured["labels"].append(label)
            if callback is not None:
                captured["callbacks"][label] = callback

        def addMenu(self, label):
            captured["labels"].append(label)
            return self

        def exec(self, *_args):
            pass

    monkeypatch.setattr("m3u8_downloader.gui_v2.QMenu", MenuStub)
    window._show_task_menu(
        window.task_list, window.task_list.visualItemRect(window.task_list.item(0)).center(),
    )

    assert "重新提取" in captured["labels"]
    assert "任务设置" in captured["labels"]
    assert "调整队列" in captured["labels"]
    captured["callbacks"]["重新提取"]()
    assert service.get_task(one.id).extraction_status is ExtractionStatus.WAITING
    assert service.get_task(two.id).extraction_status is ExtractionStatus.WAITING


def test_failed_fast_redownload_offers_retry_and_reextract(
    qtbot, tmp_path, monkeypatch,
):
    ids = iter(["original", "copy"])
    service = TaskService(
        SQLiteTaskRepository(tmp_path / "tasks.db"), id_factory=ids.__next__,
    )
    original = service.create_tasks(CreateTaskRequest(
        addresses="https://site.example/watch/42", save_directory=str(tmp_path),
    ))[0]
    service.start_extraction(original.id)
    service.add_candidates(original.id, [Candidate(
        "https://cdn.example/video.m3u8?expires=old"
    )])
    service.finish_extraction(original.id)
    copied = service.redownload_task(original.id)
    copied_item = service.list_items(copied.id)[0]
    service.fail_item(copied.id, copied_item.id, "媒体内容无法播放")

    window = MainWindow(service)
    qtbot.addWidget(window)
    window.show()
    window._force_exit = True
    callbacks = {}

    class MenuStub:
        def __init__(self, *_args): pass
        def addSeparator(self): pass
        def addAction(self, label, callback=None): callbacks[label] = callback
        def addMenu(self, label): callbacks[label] = None; return self
        def exec(self, *_args): pass

    monkeypatch.setattr("m3u8_downloader.gui_v2.QMenu", MenuStub)
    copied_row = next(
        row for row in range(window.task_list.count())
        if window.task_list.item(row).data(Qt.ItemDataRole.UserRole).id == copied.id
    )
    window.task_list.setCurrentRow(copied_row)
    window._show_task_menu(
        window.task_list,
        window.task_list.visualItemRect(window.task_list.item(copied_row)).center(),
    )

    assert "重试失败项" in callbacks
    assert "重新提取" in callbacks
    callbacks["重新提取"]()
    assert service.get_task(copied.id).extraction_status is ExtractionStatus.WAITING
    assert service.list_items(copied.id) == []


def test_batch_task_settings_apply_to_every_selected_parent(qtbot, tmp_path, monkeypatch):
    ids = iter(["one", "two"])
    service = TaskService(SQLiteTaskRepository(tmp_path / "tasks.db"), id_factory=ids.__next__)
    one, two = service.create_tasks(CreateTaskRequest(
        addresses="https://site.example/one\nhttps://site.example/two",
        save_directory=str(tmp_path),
    ))
    service.update_task_settings(
        one.id, replace(one.settings, allow_content_duplicate=True),
    )

    class DialogStub:
        def __init__(self, *_args):
            pass

        def setWindowTitle(self, _title):
            pass

        def exec(self):
            return QDialog.DialogCode.Accepted

        def settings(self):
            return TaskSettings(segment_threads=17, timeout_seconds=75)

    monkeypatch.setattr("m3u8_downloader.gui_v2.TaskSettingsDialog", DialogStub)
    window = MainWindow(service)
    qtbot.addWidget(window)
    window._force_exit = True
    window._edit_task_settings_batch([one, two], one)

    assert service.get_task(one.id).settings.segment_threads == 17
    assert service.get_task(two.id).settings.segment_threads == 17
    assert service.get_task(one.id).settings.timeout_seconds == 75
    assert service.get_task(two.id).settings.timeout_seconds == 75
    assert service.get_task(one.id).settings.allow_content_duplicate is True
    assert service.get_task(two.id).settings.allow_content_duplicate is False


def test_multi_selected_completed_tasks_offer_batch_redownload_and_validation(
    qtbot, tmp_path, monkeypatch,
):
    ids = iter(["one", "two"])
    service = TaskService(SQLiteTaskRepository(tmp_path / "tasks.db"), id_factory=ids.__next__)
    tasks = service.create_tasks(CreateTaskRequest(
        addresses="https://cdn.example/one.m3u8\nhttps://cdn.example/two.m3u8",
        save_directory=str(tmp_path),
    ))
    for task in tasks:
        item = service.list_items(task.id)[0]
        output = tmp_path / f"{task.id}.mp4"
        output.write_bytes(b"done")
        service.complete_item(task.id, item.id, output)
        service.finish_parent_if_handled(task.id)
    window = MainWindow(service)
    qtbot.addWidget(window)
    window.show()
    window._force_exit = True
    window._switch_view(1)
    for row in range(2):
        window.completed_list.item(row).setSelected(True)
    labels = []

    class MenuStub:
        def __init__(self, *_args): pass
        def addSeparator(self): pass
        def addAction(self, label, callback=None): labels.append(label)
        def addMenu(self, label): labels.append(label); return self
        def exec(self, *_args): pass

    monkeypatch.setattr("m3u8_downloader.gui_v2.QMenu", MenuStub)
    window._show_task_menu(
        window.completed_list,
        window.completed_list.visualItemRect(window.completed_list.item(0)).center(),
    )

    assert "重新下载" in labels
    assert "校验并修复" in labels
    assert "删除任务" in labels
    assert "彻底删除文件" in labels


def test_completed_web_task_offers_fast_redownload_and_fresh_reextract(
    qtbot, tmp_path, monkeypatch,
):
    service = TaskService(SQLiteTaskRepository(tmp_path / "tasks.db"))
    task = service.create_tasks(CreateTaskRequest(
        addresses="https://site.example/watch/42", save_directory=str(tmp_path),
    ))[0]
    service.start_extraction(task.id)
    items = service.add_candidates(task.id, [Candidate(
        "https://cdn.example/video.m3u8?expires=old"
    )])
    service.finish_extraction(task.id)
    output = tmp_path / "video.mp4"
    output.write_bytes(b"done")
    service.complete_item(task.id, items[0].id, output)
    service.finish_parent_if_handled(task.id)
    window = MainWindow(service)
    qtbot.addWidget(window)
    window.show()
    window._force_exit = True
    window._switch_view(1)
    labels = []

    class MenuStub:
        def __init__(self, *_args): pass
        def addSeparator(self): pass
        def addAction(self, label, callback=None): labels.append(label)
        def addMenu(self, label): labels.append(label); return self
        def exec(self, *_args): pass

    monkeypatch.setattr("m3u8_downloader.gui_v2.QMenu", MenuStub)
    window._show_task_menu(
        window.completed_list,
        window.completed_list.visualItemRect(window.completed_list.item(0)).center(),
    )

    assert "重新下载" in labels
    assert "重新提取" in labels


def test_main_window_restores_the_last_user_size(qtbot, tmp_path):
    database = tmp_path / "tasks.db"
    first = MainWindow(TaskService(SQLiteTaskRepository(database)))
    qtbot.addWidget(first)
    first.show()
    first.resize(1320, 820)
    first._force_exit = True
    first.close()

    restored = MainWindow(TaskService(SQLiteTaskRepository(database)))
    qtbot.addWidget(restored)
    restored._force_exit = True

    assert restored.size().width() == 1320
    assert restored.size().height() == 820


def test_downloading_page_title_shows_global_runtime_status(qtbot, tmp_path):
    service = TaskService(SQLiteTaskRepository(tmp_path / "tasks.db"))
    window = MainWindow(service)
    qtbot.addWidget(window)
    window.show()
    window._force_exit = True

    assert window.page_title.text() == "无任务"

    extracting = service.create_tasks(CreateTaskRequest(
        addresses="https://site.example/watch/42", save_directory=str(tmp_path),
    ))[0]
    window.refresh_tasks()
    assert window.page_title.text() == "提取中"

    service.delete_task(extracting.id, delete_outputs=False)
    downloading = service.create_tasks(CreateTaskRequest(
        addresses="https://cdn.example/video.m3u8", save_directory=str(tmp_path),
    ))[0]
    item = service.list_items(downloading.id)[0]
    window.refresh_tasks()
    assert window.page_title.text() == "等待中"

    service.pause_task(downloading.id)
    window.refresh_tasks()
    assert window.page_title.text() == "暂停中"

    second = service.create_tasks(CreateTaskRequest(
        addresses="https://cdn.example/second.m3u8", save_directory=str(tmp_path),
    ))[0]
    second_item = service.list_items(second.id)[0]
    window.refresh_tasks()
    assert window.page_title.text() == "等待中"
    service.pause_task(second.id)
    window.refresh_tasks()
    assert window.page_title.text() == "暂停中"

    service.resume_task(downloading.id)
    service.resume_task(second.id)
    service.start_item(downloading.id, item.id)
    service.start_item(second.id, second_item.id)
    service.update_item_progress(downloading.id, item.id, {"speed": 2 * 1024 * 1024})
    service.update_item_progress(second.id, second_item.id, {"speed": 1024 * 1024})
    window.refresh_tasks()
    assert window.page_title.text() == "3.0 MB/秒"

    service.delete_task(second.id, delete_outputs=False)
    service.fail_item(downloading.id, item.id, "测试失败")
    window.refresh_tasks()
    assert window.page_title.text() == "有失败任务"

    output = tmp_path / "video.mp4"
    output.write_bytes(b"video")
    service.complete_item(downloading.id, item.id, output)
    service.finish_parent_if_handled(downloading.id)
    window.refresh_tasks()
    assert window.page_title.text() == "无任务"

    qtbot.mouseClick(window.completed_button, Qt.MouseButton.LeftButton)
    assert window.page_title.text() == "已完成"
    qtbot.mouseClick(window.settings_button, Qt.MouseButton.LeftButton)
    assert window.page_title.text() == "设置"


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


def test_delete_task_accepts_the_integer_result_returned_by_pyside(
    qtbot, tmp_path,
):
    service = TaskService(SQLiteTaskRepository(tmp_path / "tasks.db"), id_factory=lambda: "task")
    task = service.create_tasks(CreateTaskRequest(
        addresses="https://cdn.example/video.m3u8",
        save_directory=str(tmp_path),
    ))[0]
    window = MainWindow(service)
    qtbot.addWidget(window)
    window._force_exit = True
    shown = []

    def confirm_real_dialog():
        box = QApplication.activeModalWidget()
        shown.append(box)
        box.button(QMessageBox.StandardButton.Yes).click()

    QTimer.singleShot(0, confirm_real_dialog)

    window._delete_task(task, delete_outputs=False)

    assert isinstance(shown[0], QMessageBox)
    assert service.list_tasks() == []


def test_delete_task_confirmation_can_be_suppressed_for_only_one_window_session(
    qtbot, tmp_path, monkeypatch,
):
    ids = iter(["first", "second", "after-reopen"])
    service = TaskService(SQLiteTaskRepository(tmp_path / "tasks.db"), id_factory=ids.__next__)
    first, second = service.create_tasks(CreateTaskRequest(
        addresses="https://cdn.example/first.m3u8\nhttps://cdn.example/second.m3u8",
        save_directory=str(tmp_path),
    ))
    prompts = []

    def accept_and_disable(box):
        prompts.append(box.windowTitle())
        assert box.checkBox() is not None
        assert box.checkBox().text() == "本次不再询问"
        box.checkBox().setChecked(True)
        return QMessageBox.StandardButton.Yes.value

    monkeypatch.setattr(QMessageBox, "exec", accept_and_disable)
    monkeypatch.setattr(
        QMessageBox,
        "question",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("删除确认必须提供“不再确认”复选框")
        ),
    )
    window = MainWindow(service)
    qtbot.addWidget(window)
    window._force_exit = True

    window._delete_task(first, delete_outputs=False)
    window._delete_task(second, delete_outputs=False)

    assert prompts == ["删除任务"]
    after_reopen = service.create_tasks(CreateTaskRequest(
        addresses="https://cdn.example/after-reopen.m3u8",
        save_directory=str(tmp_path),
    ))[0]
    reopened = MainWindow(service)
    qtbot.addWidget(reopened)
    reopened._force_exit = True
    reopened._delete_task(after_reopen, delete_outputs=False)
    assert prompts == ["删除任务", "删除任务"]


def test_permanent_delete_has_independent_session_confirmation_and_removes_files(
    qtbot, tmp_path, monkeypatch,
):
    ids = iter(["record", "file-one", "file-two", "file-after-reopen"])
    service = TaskService(SQLiteTaskRepository(tmp_path / "tasks.db"), id_factory=ids.__next__)

    def create_completed(name):
        task = service.create_tasks(CreateTaskRequest(
            addresses=f"https://cdn.example/{name}.m3u8",
            save_directory=str(tmp_path),
        ))[0]
        item = service.list_items(task.id)[0]
        output = tmp_path / f"{name}.mp4"
        output.write_bytes(name.encode("utf-8"))
        service.complete_item(task.id, item.id, output)
        service.finish_parent_if_handled(task.id)
        return service.get_task(task.id), output

    record_only = service.create_tasks(CreateTaskRequest(
        addresses="https://cdn.example/record.m3u8",
        save_directory=str(tmp_path),
    ))[0]
    file_one, output_one = create_completed("file-one")
    file_two, output_two = create_completed("file-two")
    prompts = []

    def accept_and_disable(box):
        prompts.append(box.windowTitle())
        box.checkBox().setChecked(True)
        return QMessageBox.StandardButton.Yes.value

    monkeypatch.setattr(QMessageBox, "exec", accept_and_disable)
    window = MainWindow(service)
    qtbot.addWidget(window)
    window._force_exit = True

    window._delete_task(record_only, delete_outputs=False)
    window._delete_task(file_one, delete_outputs=True)
    window._delete_task(file_two, delete_outputs=True)

    assert prompts == ["删除任务", "彻底删除文件"]
    assert not output_one.exists()
    assert not output_two.exists()

    after_reopen, output_after_reopen = create_completed("file-after-reopen")
    reopened = MainWindow(service)
    qtbot.addWidget(reopened)
    reopened._force_exit = True
    reopened._delete_task(after_reopen, delete_outputs=True)
    assert prompts == ["删除任务", "彻底删除文件", "彻底删除文件"]
    assert not output_after_reopen.exists()


def test_parent_lists_support_multi_selection_and_batch_delete_uses_one_prompt(
    qtbot, tmp_path, monkeypatch,
):
    ids = iter(["one", "two", "three"])
    service = TaskService(SQLiteTaskRepository(tmp_path / "tasks.db"), id_factory=ids.__next__)
    tasks = service.create_tasks(CreateTaskRequest(
        addresses=(
            "https://cdn.example/one.m3u8\n"
            "https://cdn.example/two.m3u8\n"
            "https://cdn.example/three.m3u8"
        ),
        save_directory=str(tmp_path),
    ))
    prompts = []

    def accept(box):
        prompts.append(box.text())
        return QMessageBox.StandardButton.Yes.value

    monkeypatch.setattr(QMessageBox, "exec", accept)
    window = MainWindow(service)
    qtbot.addWidget(window)
    window.show()
    window._force_exit = True

    assert window.task_list.selectionMode() is QAbstractItemView.SelectionMode.ExtendedSelection
    assert window.completed_list.selectionMode() is QAbstractItemView.SelectionMode.ExtendedSelection
    window._delete_tasks(tasks[:2], delete_outputs=False)

    assert len(prompts) == 1
    assert "2 个任务" in prompts[0]
    assert [task.id for task in service.list_tasks()] == ["three"]


def test_right_clicking_any_selected_parent_keeps_multi_selection_for_batch_actions(
    qtbot, tmp_path, monkeypatch,
):
    ids = iter(["one", "two", "three"])
    service = TaskService(SQLiteTaskRepository(tmp_path / "tasks.db"), id_factory=ids.__next__)
    service.create_tasks(CreateTaskRequest(
        addresses=(
            "https://cdn.example/one.m3u8\n"
            "https://cdn.example/two.m3u8\n"
            "https://cdn.example/three.m3u8"
        ),
        save_directory=str(tmp_path),
    ))
    window = MainWindow(service)
    qtbot.addWidget(window)
    window.show()
    window._force_exit = True
    first = window.task_list.item(0)
    second = window.task_list.item(1)
    first_position = window.task_list.visualItemRect(first).center()
    second_position = window.task_list.visualItemRect(second).center()
    qtbot.mouseClick(
        window.task_list.viewport(), Qt.MouseButton.LeftButton, pos=first_position,
    )
    qtbot.mouseClick(
        window.task_list.viewport(), Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.ControlModifier, second_position,
    )
    assert len(window.task_list.selectedItems()) == 2
    position = first_position
    assert window.task_list.itemAt(position) is first
    class MenuStub:
        def __init__(self, *_args):
            pass

        def addSeparator(self):
            pass

        def addAction(self, *_args):
            pass

        def addMenu(self, *_args):
            return self

        def exec(self, *_args):
            pass

    monkeypatch.setattr("m3u8_downloader.gui_v2.QMenu", MenuStub)
    qtbot.mouseClick(
        window.task_list.viewport(), Qt.MouseButton.RightButton, pos=position,
    )

    assert {
        item.data(Qt.ItemDataRole.UserRole).id for item in window.task_list.selectedItems()
    } == {"one", "two"}


def test_download_item_column_widths_persist_after_reopening(qtbot, tmp_path):
    database = tmp_path / "tasks.db"
    first = MainWindow(TaskService(SQLiteTaskRepository(database)))
    qtbot.addWidget(first)
    first.resize(1280, 790)
    first.show()
    first._force_exit = True
    header = first.item_table.horizontalHeader()
    requested = [260, 145, 96, 205]
    for column, width in enumerate(requested):
        header.resizeSection(column, width)
    qtbot.wait(300)
    expected = [header.sectionSize(column) for column in range(4)]
    first.close()

    restored = MainWindow(TaskService(SQLiteTaskRepository(database)))
    qtbot.addWidget(restored)
    restored.show()
    restored._force_exit = True

    assert [
        restored.item_table.horizontalHeader().sectionSize(column) for column in range(4)
    ] == expected


def test_double_clicking_download_item_opens_its_existing_file(qtbot, tmp_path, monkeypatch):
    service = TaskService(SQLiteTaskRepository(tmp_path / "tasks.db"), id_factory=lambda: "task")
    task = service.create_tasks(CreateTaskRequest(
        addresses="https://cdn.example/video.m3u8", save_directory=str(tmp_path),
    ))[0]
    item = service.list_items(task.id)[0]
    output = tmp_path / "video.mp4"
    output.write_bytes(b"video")
    service.complete_item(task.id, item.id, output)
    service.finish_parent_if_handled(task.id)
    opened = []
    monkeypatch.setattr("m3u8_downloader.gui_v2.os.startfile", opened.append)
    window = MainWindow(service)
    qtbot.addWidget(window)
    window.show()
    window._force_exit = True
    qtbot.mouseClick(window.completed_button, Qt.MouseButton.LeftButton)
    window.completed_list.setCurrentRow(0)

    cell = window.item_table.item(0, 0)
    window.item_table.itemDoubleClicked.emit(cell)

    assert opened == [str(output)]


def test_new_task_dialog_explains_missing_required_fields(qtbot, tmp_path):
    window = MainWindow(TaskService(SQLiteTaskRepository(tmp_path / "tasks.db")))
    qtbot.addWidget(window)
    window.show()
    window._force_exit = True
    window.open_new_task_dialog()
    dialog = window.new_task_dialog

    qtbot.mouseClick(dialog.start_button, Qt.MouseButton.LeftButton)
    assert dialog.validation_label.isVisible()
    assert "链接" in dialog.validation_label.text()

    dialog.address_edit.setPlainText("https://site.example/watch/42")
    dialog.directory_edit.clear()
    qtbot.mouseClick(dialog.start_button, Qt.MouseButton.LeftButton)
    assert dialog.validation_label.isVisible()
    assert "保存目录" in dialog.validation_label.text()

    dialog.reject()


def test_reopened_window_starts_with_empty_log_view_and_follows_new_logs(
    qtbot, tmp_path,
):
    database = tmp_path / "tasks.db"
    service = TaskService(SQLiteTaskRepository(database))
    for index in range(80):
        service.add_log("previous-task", "信息", "下载", f"上次运行日志 {index:02d}")

    reopened = MainWindow(TaskService(SQLiteTaskRepository(database)))
    qtbot.addWidget(reopened)
    reopened.show()
    reopened._force_exit = True
    QApplication.processEvents()

    scroll_bar = reopened.log_view.verticalScrollBar()
    assert reopened.log_view.toPlainText() == ""

    for index in range(80):
        reopened._service.add_log(
            "current-task", "信息", "下载", f"本次运行日志 {index:02d}"
        )
    reopened._refresh_logs()
    QApplication.processEvents()

    assert "上次运行日志" not in reopened.log_view.toPlainText()
    assert reopened.log_view.toPlainText().endswith("本次运行日志 79")
    assert scroll_bar.maximum() > 0
    assert scroll_bar.value() == scroll_bar.maximum()

    opened_again = MainWindow(TaskService(SQLiteTaskRepository(database)))
    qtbot.addWidget(opened_again)
    opened_again.show()
    opened_again._force_exit = True
    QApplication.processEvents()

    assert opened_again.log_view.toPlainText() == ""


def test_clear_log_button_only_hides_logs_from_the_current_process(qtbot, tmp_path):
    service = TaskService(SQLiteTaskRepository(tmp_path / "tasks.db"))
    task = service.create_tasks(CreateTaskRequest(
        addresses="https://cdn.example/video.m3u8", save_directory=str(tmp_path),
    ))[0]
    window = MainWindow(service)
    qtbot.addWidget(window)
    window.show()
    window._force_exit = True
    service.add_log(task.id, "信息", "下载", "清空前日志")
    window._refresh_logs()
    assert "清空前日志" in window.log_view.toPlainText()

    qtbot.mouseClick(window.clear_logs_button, Qt.MouseButton.LeftButton)

    assert window.log_view.toPlainText() == ""
    assert any(
        entry.message == "清空前日志" for entry in service.list_logs(task_id=task.id)
    )
    service.add_log(task.id, "信息", "下载", "清空后日志")
    window._refresh_logs()
    assert "清空前日志" not in window.log_view.toPlainText()
    assert "清空后日志" in window.log_view.toPlainText()


def test_log_refresh_appends_without_replacing_document_or_disturbing_user_selection(
    qtbot, tmp_path,
):
    service = TaskService(SQLiteTaskRepository(tmp_path / "tasks.db"))
    window = MainWindow(service)
    qtbot.addWidget(window)
    window.show()
    window._force_exit = True
    for index in range(80):
        service.add_log("task", "信息", "下载", f"可选择日志 {index:02d}")
    window.log_scope_combo.setCurrentIndex(1)
    QApplication.processEvents()

    document = window.log_view.document()
    cursor = window.log_view.textCursor()
    start = window.log_view.toPlainText().index("可选择日志 00")
    cursor.setPosition(start)
    cursor.setPosition(start + len("可选择日志 00"), QTextCursor.MoveMode.KeepAnchor)
    window.log_view.setTextCursor(cursor)
    scroll_bar = window.log_view.verticalScrollBar()
    scroll_bar.setValue(0)

    service.add_log("task", "信息", "下载", "新增日志")
    window._refresh_logs()
    QApplication.processEvents()

    assert window.log_view.document() is document
    assert window.log_view.textCursor().selectedText() == "可选择日志 00"
    assert scroll_bar.value() == 0
    assert window.log_view.toPlainText().endswith("新增日志")


def test_log_refresh_over_five_hundred_lines_still_preserves_selection(qtbot, tmp_path):
    service = TaskService(SQLiteTaskRepository(tmp_path / "tasks.db"))
    window = MainWindow(service)
    qtbot.addWidget(window)
    window._force_exit = True
    for index in range(500):
        service.add_log("task", "信息", "下载", f"长日志 {index:03d}")
    window._refresh_logs()
    cursor = window.log_view.textCursor()
    start = window.log_view.toPlainText().index("长日志 250")
    cursor.setPosition(start)
    cursor.setPosition(start + len("长日志 250"), QTextCursor.MoveMode.KeepAnchor)
    window.log_view.setTextCursor(cursor)

    service.add_log("task", "信息", "下载", "第 501 条日志")
    window._refresh_logs()

    assert window.log_view.textCursor().selectedText() == "长日志 250"
    assert window.log_view.toPlainText().endswith("第 501 条日志")


def test_log_filters_never_reveal_entries_from_a_previous_process(
    qtbot, tmp_path,
):
    database = tmp_path / "tasks.db"
    service = TaskService(SQLiteTaskRepository(database), id_factory=lambda: "task")
    service.create_tasks(CreateTaskRequest(
        addresses="https://cdn.example/video.m3u8",
        save_directory=str(tmp_path),
    ))
    service.add_log("task", "错误", "下载", "上次运行错误")
    window = MainWindow(TaskService(SQLiteTaskRepository(database)))
    qtbot.addWidget(window)
    window.show()
    window._force_exit = True

    window._service.add_log("task", "信息", "下载", "本次任务日志")
    window._service.add_log("other-task", "信息", "下载", "本次其他日志")
    window.task_list.setCurrentRow(0)

    assert "本次任务日志" in window.log_view.toPlainText()
    assert "本次其他日志" not in window.log_view.toPlainText()
    assert "上次运行错误" not in window.log_view.toPlainText()

    window.log_scope_combo.setCurrentIndex(1)
    assert "本次任务日志" in window.log_view.toPlainText()
    assert "本次其他日志" in window.log_view.toPlainText()
    assert "上次运行错误" not in window.log_view.toPlainText()

    window.log_level_combo.setCurrentText("错误")
    assert window.log_view.toPlainText() == ""


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

    card = window.task_list.itemWidget(window.task_list.item(0))
    qtbot.mouseClick(card, Qt.MouseButton.LeftButton, pos=card.rect().center())
    assert window.detail_title.text() == task.name
    assert window.item_table.rowCount() == 1
    assert task.source_url in window.info_view.toPlainText()

    window.task_list.clearSelection()
    assert window.detail_title.text() == ""
    assert window.item_table.rowCount() == 0
    assert window.info_view.toPlainText() == ""
    assert window._detail_task_id == ""


def test_task_detail_width_stays_equal_when_switching_between_task_names(qtbot, tmp_path):
    repository = SQLiteTaskRepository(tmp_path / "tasks.db")
    service = TaskService(
        repository, id_factory=iter(["short", "long"]).__next__,
    )
    tasks = service.create_tasks(CreateTaskRequest(
        addresses="https://cdn.example/short.m3u8\nhttps://cdn.example/long.m3u8",
        save_directory=str(tmp_path),
    ))
    service.rename_task(tasks[0].id, "短标题")
    service.rename_task(tasks[1].id, "很长的任务标题" * 80)
    window = MainWindow(service)
    qtbot.addWidget(window)
    window.resize(1280, 790)
    window.show()
    window._force_exit = True
    qtbot.waitUntil(lambda: sum(window.main_horizontal_splitter.sizes()) > 1000)

    initial_sizes = window.main_horizontal_splitter.sizes()
    assert abs(initial_sizes[0] - initial_sizes[1]) <= 1

    window.task_list.setCurrentRow(0)
    short_sizes = window.main_horizontal_splitter.sizes()
    window.task_list.setCurrentRow(1)
    long_sizes = window.main_horizontal_splitter.sizes()

    assert short_sizes == initial_sizes
    assert long_sizes == initial_sizes


def test_parent_task_card_is_compact_and_keeps_only_progress_size_and_speed(qtbot, tmp_path):
    service = TaskService(SQLiteTaskRepository(tmp_path / "tasks.db"), id_factory=lambda: "task")
    task = service.create_tasks(CreateTaskRequest(
        addresses="https://cdn.example/video.m3u8",
        save_directory=str(tmp_path / "不应显示的保存位置"),
    ))[0]
    service.rename_task(task.id, "父任务名称")
    item = service.list_items(task.id)[0]
    service.start_item(task.id, item.id)
    service.update_item_progress(task.id, item.id, {
        "downloaded": 256,
        "byte_total": 1024,
        "speed": 128,
        "eta": 999,
    })
    window = MainWindow(service)
    qtbot.addWidget(window)
    window.show()
    window._force_exit = True
    card = window.task_list.itemWidget(window.task_list.item(0))
    labels = card.findChildren(QLabel)
    texts = [label.text() for label in labels]
    name = next(label for label in labels if label.objectName() == "taskName")
    status = next(label for label in labels if label.objectName() == "status")

    assert window.task_list.item(0).sizeHint().height() <= 96
    assert abs(name.geometry().center().y() - status.geometry().center().y()) <= 2
    assert status.geometry().left() > name.geometry().left()
    assert not any("不应显示的保存位置" in text for text in texts)
    assert not any("剩余" in text or text == "video" for text in texts)
    assert any("256 B / 1.0 KB" in text and "128 B/秒" in text for text in texts)


def test_task_detail_width_is_restored_after_user_moves_splitter(qtbot, tmp_path):
    repository = SQLiteTaskRepository(tmp_path / "tasks.db")
    service = TaskService(repository)
    first_window = MainWindow(service)
    qtbot.addWidget(first_window)
    first_window.resize(1280, 790)
    first_window.show()
    first_window._force_exit = True
    qtbot.waitUntil(lambda: sum(first_window.main_horizontal_splitter.sizes()) > 1000)

    first_window.main_horizontal_splitter.moveSplitter(700, 1)
    saved_sizes = first_window.main_horizontal_splitter.sizes()
    first_window.close()

    restored_window = MainWindow(TaskService(SQLiteTaskRepository(tmp_path / "tasks.db")))
    qtbot.addWidget(restored_window)
    restored_window.resize(1280, 790)
    restored_window.show()
    restored_window._force_exit = True
    qtbot.waitUntil(lambda: sum(restored_window.main_horizontal_splitter.sizes()) > 1000)

    assert restored_window.main_horizontal_splitter.sizes() == saved_sizes


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


def test_blank_click_and_escape_cancel_highlight_at_the_right_scope(qtbot, tmp_path):
    service = TaskService(SQLiteTaskRepository(tmp_path / "tasks.db"), id_factory=lambda: "parent")
    service.create_tasks(CreateTaskRequest(
        addresses="https://cdn.example/video.m3u8",
        save_directory=str(tmp_path),
    ))
    window = MainWindow(service)
    qtbot.addWidget(window)
    window.resize(1280, 790)
    window.show()
    window._force_exit = True
    window.task_list.setCurrentRow(0)

    checked = window.item_table.item(0, 0)
    assert checked.checkState() is Qt.CheckState.Checked
    window.item_table.selectRow(0)
    qtbot.mouseClick(
        window.item_table.viewport(), Qt.MouseButton.LeftButton,
        pos=QPoint(window.item_table.viewport().width() - 3,
                   window.item_table.viewport().height() - 3),
    )
    assert window.item_table.selectedItems() == []
    assert checked.checkState() is Qt.CheckState.Checked
    assert window.detail_title.text() != ""

    window.item_table.selectRow(0)
    window.item_table.setFocus()
    qtbot.keyPress(window.item_table, Qt.Key.Key_Escape)
    assert window.item_table.selectedItems() == []
    assert checked.checkState() is Qt.CheckState.Checked

    window.task_list.setFocus()
    qtbot.keyPress(window.task_list, Qt.Key.Key_Escape)
    assert window.task_list.selectedItems() == []
    assert window.detail_title.text() == ""

    window.task_list.setCurrentRow(0)
    qtbot.mouseClick(window.settings_button, Qt.MouseButton.LeftButton)
    qtbot.mouseClick(window.downloading_button, Qt.MouseButton.LeftButton)
    assert window.task_list.currentItem() is None
    assert window.task_list.selectedItems() == []
    assert window.detail_title.text() == ""


def test_action_buttons_follow_parent_and_child_selection_state(qtbot, tmp_path):
    service = TaskService(SQLiteTaskRepository(tmp_path / "tasks.db"), id_factory=lambda: "parent")
    service.create_tasks(CreateTaskRequest(
        addresses="https://cdn.example/video.m3u8",
        save_directory=str(tmp_path),
    ))
    window = MainWindow(service)
    qtbot.addWidget(window)
    window.show()
    window._force_exit = True

    for button in [
        window.pause_task_button,
        window.resume_task_button,
        window.stop_extraction_button,
        window.download_selected_button,
    ]:
        assert not button.isEnabled()
        assert "选择" in button.toolTip()

    window.task_list.setCurrentRow(0)
    assert window.pause_task_button.isEnabled()
    assert not window.resume_task_button.isEnabled()
    assert not window.stop_extraction_button.isEnabled()
    assert window.download_selected_button.isEnabled()

    window.item_table.item(0, 0).setCheckState(Qt.CheckState.Unchecked)
    assert not window.download_selected_button.isEnabled()
    assert "勾选" in window.download_selected_button.toolTip()

    qtbot.mouseClick(window.pause_task_button, Qt.MouseButton.LeftButton)
    assert not window.pause_task_button.isEnabled()
    assert window.resume_task_button.isEnabled()


def test_select_all_tracks_none_partial_and_all_candidate_checks(qtbot, tmp_path):
    service = TaskService(SQLiteTaskRepository(tmp_path / "tasks.db"), id_factory=lambda: "parent")
    task = service.create_tasks(CreateTaskRequest(
        addresses="https://site.example/watch/42",
        save_directory=str(tmp_path),
    ))[0]
    service.add_candidates(task.id, [
        Candidate(f"https://cdn.example/{index}.m3u8") for index in range(4)
    ])
    service.finish_extraction(task.id)
    window = MainWindow(service)
    qtbot.addWidget(window)
    window.show()
    window._force_exit = True
    window.task_list.setCurrentRow(0)

    assert window.select_all_checkbox.isEnabled()
    assert window.select_all_checkbox.checkState() is Qt.CheckState.Unchecked

    qtbot.mouseClick(window.select_all_checkbox, Qt.MouseButton.LeftButton)
    assert all(
        window.item_table.item(row, 0).checkState() is Qt.CheckState.Checked
        for row in range(window.item_table.rowCount())
    )
    assert window.select_all_checkbox.checkState() is Qt.CheckState.Checked
    assert window.download_selected_button.isEnabled()

    window.item_table.item(0, 0).setCheckState(Qt.CheckState.Unchecked)
    assert window.select_all_checkbox.checkState() is Qt.CheckState.PartiallyChecked

    qtbot.mouseClick(window.select_all_checkbox, Qt.MouseButton.LeftButton)
    assert window.select_all_checkbox.checkState() is Qt.CheckState.Checked
    qtbot.mouseClick(window.select_all_checkbox, Qt.MouseButton.LeftButton)
    assert window.select_all_checkbox.checkState() is Qt.CheckState.Unchecked
    assert all(
        window.item_table.item(row, 0).checkState() is Qt.CheckState.Unchecked
        for row in range(window.item_table.rowCount())
    )
    assert not window.download_selected_button.isEnabled()


def test_empty_candidate_panel_explains_that_extraction_is_still_running(qtbot, tmp_path):
    service = TaskService(SQLiteTaskRepository(tmp_path / "tasks.db"))
    service.create_tasks(CreateTaskRequest(
        addresses="https://site.example/watch/42",
        save_directory=str(tmp_path),
    ))
    window = MainWindow(service)
    qtbot.addWidget(window)
    window.show()
    window._force_exit = True
    window.task_list.setCurrentRow(0)

    assert "正在提取" in window.select_all_checkbox.toolTip()
    assert "正在提取" in window.download_selected_button.toolTip()


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


def test_quick_bar_pastes_before_start_and_both_inputs_have_clear_buttons(qtbot, tmp_path):
    window = MainWindow(TaskService(SQLiteTaskRepository(tmp_path / "tasks.db")))
    qtbot.addWidget(window)
    window.show()
    window._force_exit = True

    assert window.search_edit.isClearButtonEnabled()
    assert window.quick_address_edit.isClearButtonEnabled()
    assert not any(
        label.text() == "快速下载"
        for label in window.quick_download_panel.findChildren(QLabel)
    )
    QApplication.clipboard().setText("https://site.example/watch/pasted")
    window.quick_address_edit.setText("将被替换")
    qtbot.mouseClick(window.quick_paste_button, Qt.MouseButton.LeftButton)

    assert window.quick_address_edit.text() == "https://site.example/watch/pasted"
    assert window.quick_paste_button.geometry().left() < window.quick_start_button.geometry().left()
    assert "已粘贴" in window.feedback_label.text()


def test_quick_download_and_search_inputs_are_left_aligned(qtbot, tmp_path):
    window = MainWindow(TaskService(SQLiteTaskRepository(tmp_path / "tasks.db")))
    qtbot.addWidget(window)
    window.show()
    window._force_exit = True

    search_left = window.search_edit.mapTo(window, QPoint(0, 0)).x()
    quick_left = window.quick_address_edit.mapTo(window, QPoint(0, 0)).x()

    assert quick_left == search_left


def test_close_rule_settings_persist_all_three_modes(qtbot, tmp_path):
    repository = SQLiteTaskRepository(tmp_path / "tasks.db")
    window = MainWindow(TaskService(repository))
    qtbot.addWidget(window)
    window._force_exit = True

    assert window.close_rule_enabled_check.isChecked() is False
    assert [window.close_rule_combo.itemData(index) for index in range(3)] == [
        "tray", "exit", "smart",
    ]
    window.close_rule_enabled_check.setChecked(True)
    window.close_rule_combo.setCurrentIndex(window.close_rule_combo.findData("smart"))
    qtbot.mouseClick(window.save_settings_button, Qt.MouseButton.LeftButton)

    saved = repository.load_app_settings()
    assert saved.close_rule_enabled is True
    assert saved.close_rule == "smart"


class CloseEventStub:
    def __init__(self):
        self.accepted = None

    def accept(self):
        self.accepted = True

    def ignore(self):
        self.accepted = False


def test_prompt_yes_with_remember_uses_tray_without_asking_again(qtbot, tmp_path, monkeypatch):
    window = MainWindow(TaskService(SQLiteTaskRepository(tmp_path / "tasks.db")))
    qtbot.addWidget(window)
    window.show()
    prompted = []
    monkeypatch.setattr(
        window, "_ask_close_action",
        lambda _default: prompted.append(True) or ("tray", True),
    )

    first = CloseEventStub()
    window.closeEvent(first)
    second = CloseEventStub()
    window.closeEvent(second)

    assert first.accepted is False
    assert second.accepted is False
    assert prompted == [True]
    assert window.isHidden()
    window._force_exit = True


def test_prompt_window_close_cancels_and_asks_again_next_time(qtbot, tmp_path, monkeypatch):
    window = MainWindow(TaskService(SQLiteTaskRepository(tmp_path / "tasks.db")))
    qtbot.addWidget(window)
    window.show()
    prompted = []
    monkeypatch.setattr(
        window, "_ask_close_action",
        lambda _default: prompted.append(True) or (None, False),
    )

    window.closeEvent(CloseEventStub())
    window.closeEvent(CloseEventStub())

    assert prompted == [True, True]
    window._force_exit = True


def test_close_prompt_has_one_tray_checkbox_and_concise_session_text(
    qtbot, tmp_path, monkeypatch,
):
    window = MainWindow(TaskService(SQLiteTaskRepository(tmp_path / "tasks.db")))
    qtbot.addWidget(window)
    captured = {}

    def cancel(box):
        captured["text"] = box.text()
        captured["information"] = box.informativeText()
        captured["minimum_size"] = (box.minimumWidth(), box.minimumHeight())
        captured["message_minimum_width"] = box.findChild(
            QLabel, "qt_msgbox_label"
        ).minimumWidth()
        captured["checkboxes"] = [
            checkbox.text() for checkbox in box.findChildren(QCheckBox)
        ]
        captured["checked"] = box.checkBox().isChecked()
        captured["buttons"] = {
            box.button(QMessageBox.StandardButton.Yes).text(),
            box.button(QMessageBox.StandardButton.No).text(),
        }
        captured["hidden_cancel"] = box.button(
            QMessageBox.StandardButton.Cancel
        ).isHidden()
        return QMessageBox.StandardButton.Cancel

    monkeypatch.setattr(QMessageBox, "exec", cancel)

    assert window._ask_close_action(True) == (None, False)
    assert captured == {
        "text": "是否最小化到托盘？",
        "information": "",
        "minimum_size": (360, 190),
        "message_minimum_width": 260,
        "checkboxes": ["本次不再询问"],
        "checked": True,
        "buttons": {"是", "否"},
        "hidden_cancel": True,
    }
    window._force_exit = True


@pytest.mark.parametrize(("button", "expected"), [
    (QMessageBox.StandardButton.Yes, "tray"),
    (QMessageBox.StandardButton.No, "exit"),
])
def test_close_prompt_maps_yes_to_tray_and_no_to_exit(
    qtbot, tmp_path, monkeypatch, button, expected,
):
    window = MainWindow(TaskService(SQLiteTaskRepository(tmp_path / "tasks.db")))
    qtbot.addWidget(window)

    def choose(box):
        box.checkBox().setChecked(True)
        return button

    monkeypatch.setattr(QMessageBox, "exec", choose)

    assert window._ask_close_action(False) == (expected, True)
    window._force_exit = True


def test_delete_confirmation_uses_concise_session_text(qtbot, tmp_path, monkeypatch):
    window = MainWindow(TaskService(SQLiteTaskRepository(tmp_path / "tasks.db")))
    qtbot.addWidget(window)
    captured = []

    def cancel(box):
        captured.append(box.checkBox().text())
        return QMessageBox.StandardButton.Cancel

    monkeypatch.setattr(QMessageBox, "exec", cancel)

    assert window._confirm_task_deletion("删除任务", "确认删除？", False) is False
    assert captured == ["本次不再询问"]
    window._force_exit = True


class CloseControllerStub:
    def __init__(self):
        self.paused = 0
        self.stopped = 0

    def pause_all(self):
        self.paused += 1

    def stop(self):
        self.stopped += 1


def test_prompt_no_pauses_active_tasks_and_exits(
    qtbot, tmp_path, monkeypatch,
):
    service = TaskService(SQLiteTaskRepository(tmp_path / "tasks.db"))
    service.create_tasks(CreateTaskRequest(
        addresses="https://cdn.example/video.m3u8", save_directory=str(tmp_path),
    ))
    window = MainWindow(service)
    qtbot.addWidget(window)
    controller = CloseControllerStub()
    window.background_controller = controller
    monkeypatch.setattr(window, "_ask_close_action", lambda _default: ("exit", False))
    event = CloseEventStub()

    window.closeEvent(event)

    assert event.accepted is True
    assert controller.paused == 1
    assert controller.stopped == 1


@pytest.mark.parametrize(("rule", "active", "expected_action"), [
    ("tray", False, "tray"),
    ("exit", True, "exit"),
    ("smart", True, "tray"),
    ("smart", False, "exit"),
])
def test_enabled_close_rule_executes_without_prompt(
    qtbot, tmp_path, monkeypatch, rule, active, expected_action,
):
    service = TaskService(SQLiteTaskRepository(tmp_path / "tasks.db"))
    if active:
        service.create_tasks(CreateTaskRequest(
            addresses="https://cdn.example/video.m3u8", save_directory=str(tmp_path),
        ))
    service.save_app_settings(replace(
        service.load_app_settings(), close_rule_enabled=True, close_rule=rule,
    ))
    window = MainWindow(service)
    qtbot.addWidget(window)
    applied = []
    monkeypatch.setattr(
        window, "_ask_close_action",
        lambda _default: pytest.fail("启用关闭规则后不应弹窗"),
    )
    monkeypatch.setattr(
        window, "_apply_close_action",
        lambda action, _event, is_active: applied.append((action, is_active)),
    )

    window.closeEvent(CloseEventStub())

    assert applied == [(expected_action, active)]
    window._force_exit = True


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


def test_quick_start_duplicate_locates_latest_task_and_offers_same_choices(
    qtbot, tmp_path, monkeypatch,
):
    repository = SQLiteTaskRepository(tmp_path / "tasks.db")
    service = TaskService(
        repository,
        id_factory=iter(["older", "newer", "unused"]).__next__,
    )
    address = "https://cdn.example/video.m3u8"
    service.create_tasks(CreateTaskRequest(
        addresses=address, save_directory=str(tmp_path),
    ))
    newer = service.create_tasks(CreateTaskRequest(
        addresses=address, save_directory=str(tmp_path),
    ), allow_duplicates=True)[0]
    item = service.list_items(newer.id)[0]
    output = tmp_path / "newer.mp4"
    output.write_bytes(b"video")
    service.complete_item(newer.id, item.id, output)
    service.finish_parent_if_handled(newer.id)
    window = MainWindow(service)
    qtbot.addWidget(window)
    window.show()
    window._force_exit = True
    window.search_edit.setText("会隐藏所有任务")
    window.status_filter_combo.setCurrentIndex(
        window.status_filter_combo.findData("failed")
    )
    observed = []

    def view_existing(box):
        current = window.completed_list.currentItem()
        observed.append({
            "title": box.windowTitle(),
            "page": window.pages.currentIndex(),
            "task_id": current.data(Qt.ItemDataRole.UserRole).id if current else "",
            "search": window.search_edit.text(),
            "status": window.status_filter_combo.currentData(),
            "buttons": {button.text() for button in box.buttons()},
        })
        next(button for button in box.buttons() if button.text() == "查看原任务").click()
        return 0

    monkeypatch.setattr(QMessageBox, "exec", view_existing)
    window.quick_address_edit.setText(address)
    qtbot.mouseClick(window.quick_start_button, Qt.MouseButton.LeftButton)

    assert len(service.list_tasks()) == 2
    assert window.quick_address_edit.text() == address
    assert observed == [{
        "title": "链接已存在",
        "page": 1,
        "task_id": "newer",
        "search": "",
        "status": "all",
        "buttons": {"查看原任务", "仍创建副本", "取消"},
    }]
    assert "已定位" in window.feedback_label.text()


def test_new_task_dialog_duplicate_copy_selects_the_new_task(
    qtbot, tmp_path, monkeypatch,
):
    repository = SQLiteTaskRepository(tmp_path / "tasks.db")
    service = TaskService(
        repository,
        id_factory=iter(["older", "newer", "copy"]).__next__,
    )
    address = "https://cdn.example/video.m3u8"
    request = CreateTaskRequest(addresses=address, save_directory=str(tmp_path))
    service.create_tasks(request)
    service.create_tasks(request, allow_duplicates=True)
    window = MainWindow(service)
    qtbot.addWidget(window)
    window.show()
    window._force_exit = True
    located_before_choice = []

    def create_copy(box):
        current = window.task_list.currentItem()
        located_before_choice.append(
            current.data(Qt.ItemDataRole.UserRole).id if current else ""
        )
        next(button for button in box.buttons() if button.text() == "仍创建副本").click()
        return 0

    monkeypatch.setattr(QMessageBox, "exec", create_copy)
    qtbot.mouseClick(window.new_task_button, Qt.MouseButton.LeftButton)
    dialog = window.new_task_dialog
    dialog.address_edit.setPlainText(address)
    dialog.directory_edit.setText(str(tmp_path))
    qtbot.mouseClick(dialog.start_button, Qt.MouseButton.LeftButton)

    assert located_before_choice == ["newer"]
    assert [task.id for task in service.list_tasks()] == ["older", "newer", "copy"]
    assert window.pages.currentIndex() == 0
    assert window.task_list.currentItem().data(Qt.ItemDataRole.UserRole).id == "copy"


def test_content_duplicate_prompt_can_resume_the_paused_task(
    qtbot, tmp_path, monkeypatch,
):
    service = TaskService(
        SQLiteTaskRepository(tmp_path / "tasks.db"),
        id_factory=iter(["existing", "new"]).__next__,
    )
    existing = service.create_tasks(CreateTaskRequest(
        addresses="https://first.example/watch/1", save_directory=str(tmp_path),
    ))[0]
    new = service.create_tasks(CreateTaskRequest(
        addresses="https://second.example/watch/2", save_directory=str(tmp_path),
    ))[0]
    service.add_candidates(new.id, [Candidate(
        "https://cdn.example/video.m3u8", duration_seconds=60, segment_count=12,
    )])
    service.finish_extraction(new.id)
    service.hold_for_content_duplicate(new.id, existing.id)
    observed = []

    def continue_download(box):
        observed.append({
            "title": box.windowTitle(),
            "buttons": {button.text() for button in box.buttons()},
        })
        next(button for button in box.buttons() if button.text() == "仍然下载").click()
        return 0

    monkeypatch.setattr(QMessageBox, "exec", continue_download)
    window = MainWindow(service)
    qtbot.addWidget(window)
    window._force_exit = True
    qtbot.waitUntil(lambda: bool(observed))

    assert observed == [{
        "title": "发现疑似重复内容",
        "buttons": {"定位已有任务", "仍然下载", "取消"},
    }]
    assert service.get_task(new.id).download_status is DownloadStatus.WAITING
    assert service.get_task(new.id).last_error == ""


def test_content_duplicate_is_shown_as_pending_review(qtbot, tmp_path, monkeypatch):
    service = TaskService(
        SQLiteTaskRepository(tmp_path / "tasks.db"),
        id_factory=iter(["existing", "new"]).__next__,
    )
    existing, new = service.create_tasks(CreateTaskRequest(
        addresses="https://first.example/1\nhttps://second.example/2",
        save_directory=str(tmp_path),
    ))
    service.hold_for_content_duplicate(new.id, existing.id)
    monkeypatch.setattr(QMessageBox, "exec", lambda _box: 0)

    window = MainWindow(service)
    qtbot.addWidget(window)
    window._force_exit = True

    held = service.get_task(new.id)
    assert held.download_status is DownloadStatus.PENDING_REVIEW
    assert _task_status(held) == "待处理"
    legacy_held = replace(held, download_status=DownloadStatus.PENDING_SELECTION)
    assert _task_status(legacy_held) == "待处理"


def test_content_duplicate_cancel_deletes_new_task(qtbot, tmp_path, monkeypatch):
    service = TaskService(
        SQLiteTaskRepository(tmp_path / "tasks.db"),
        id_factory=iter(["existing", "new"]).__next__,
    )
    existing, new = service.create_tasks(CreateTaskRequest(
        addresses="https://first.example/1\nhttps://second.example/2",
        save_directory=str(tmp_path),
    ))
    service.hold_for_content_duplicate(new.id, existing.id)

    def cancel(box):
        next(button for button in box.buttons() if button.text() == "取消").click()
        return 0

    monkeypatch.setattr(QMessageBox, "exec", cancel)
    window = MainWindow(service)
    qtbot.addWidget(window)
    window._force_exit = True
    qtbot.waitUntil(lambda: len(service.list_tasks()) == 1)

    assert [task.id for task in service.list_tasks()] == [existing.id]


def test_content_duplicate_locate_keeps_new_task_pending_review(
    qtbot, tmp_path, monkeypatch,
):
    service = TaskService(
        SQLiteTaskRepository(tmp_path / "tasks.db"),
        id_factory=iter(["existing", "new"]).__next__,
    )
    existing, new = service.create_tasks(CreateTaskRequest(
        addresses="https://first.example/1\nhttps://second.example/2",
        save_directory=str(tmp_path),
    ))
    service.hold_for_content_duplicate(new.id, existing.id)

    def locate(box):
        next(button for button in box.buttons() if button.text() == "定位已有任务").click()
        return 0

    monkeypatch.setattr(QMessageBox, "exec", locate)
    window = MainWindow(service)
    qtbot.addWidget(window)
    window._force_exit = True
    qtbot.waitUntil(lambda: window.task_list.currentItem() is not None)

    assert service.get_task(new.id).download_status is DownloadStatus.PENDING_REVIEW
    assert window.task_list.currentItem().data(Qt.ItemDataRole.UserRole).id == existing.id


def test_completed_task_detail_can_start_background_validation_and_repair(
    qtbot, tmp_path,
):
    service = TaskService(SQLiteTaskRepository(tmp_path / "tasks.db"))
    task = service.create_tasks(CreateTaskRequest(
        addresses="https://cdn.example/video.m3u8", save_directory=str(tmp_path),
    ))[0]
    item = service.list_items(task.id)[0]
    output = tmp_path / "video.mp4"
    output.write_bytes(b"video")
    service.complete_item(task.id, item.id, output)
    service.finish_parent_if_handled(task.id)

    class RepairController:
        def __init__(self):
            self.calls = []

        def repair_task(self, task_id, item_ids):
            self.calls.append((task_id, item_ids))
            return True

    controller = RepairController()
    window = MainWindow(service)
    qtbot.addWidget(window)
    window.background_controller = controller
    window._force_exit = True
    qtbot.mouseClick(window.completed_button, Qt.MouseButton.LeftButton)
    window.completed_list.setCurrentRow(0)

    assert window.repair_task_button.isEnabled()
    qtbot.mouseClick(window.repair_task_button, Qt.MouseButton.LeftButton)

    assert controller.calls == [(task.id, [item.id])]
    assert "正在后台校验" in window.feedback_label.text()


def test_completed_list_keeps_scroll_position_during_refresh(qtbot, tmp_path):
    repository = SQLiteTaskRepository(tmp_path / "tasks.db")
    service = TaskService(repository)
    for index in range(12):
        task = service.create_tasks(CreateTaskRequest(
            addresses=f"https://cdn.example/video-{index}.m3u8",
            save_directory=str(tmp_path),
        ))[0]
        item = service.list_items(task.id)[0]
        output = tmp_path / f"video-{index}.mp4"
        output.write_bytes(b"video")
        service.complete_item(task.id, item.id, output)
        service.finish_parent_if_handled(task.id)

    window = MainWindow(service)
    qtbot.addWidget(window)
    window.show()
    window._force_exit = True
    qtbot.mouseClick(window.completed_button, Qt.MouseButton.LeftButton)
    QApplication.processEvents()
    scroll_bar = window.completed_list.verticalScrollBar()
    assert scroll_bar.maximum() > 0
    scroll_bar.setValue(scroll_bar.maximum())
    previous = scroll_bar.value()

    window.refresh_tasks()
    QApplication.processEvents()

    assert scroll_bar.value() == previous


def test_task_refresh_keeps_existing_cards_multi_selection_and_current_detail(
    qtbot, tmp_path,
):
    ids = iter(["one", "two", "three"])
    service = TaskService(SQLiteTaskRepository(tmp_path / "tasks.db"), id_factory=ids.__next__)
    service.create_tasks(CreateTaskRequest(
        addresses=(
            "https://cdn.example/one.m3u8\n"
            "https://cdn.example/two.m3u8\n"
            "https://cdn.example/three.m3u8"
        ),
        save_directory=str(tmp_path),
    ))
    window = MainWindow(service)
    qtbot.addWidget(window)
    window.show()
    window._force_exit = True
    first = window.task_list.item(0)
    second = window.task_list.item(1)
    first.setSelected(True)
    second.setSelected(True)
    window.task_list.setCurrentItem(second)
    widgets = {
        window.task_list.item(row).data(Qt.ItemDataRole.UserRole).id:
            window.task_list.itemWidget(window.task_list.item(row))
        for row in range(window.task_list.count())
    }

    window.refresh_tasks()

    assert {
        item.data(Qt.ItemDataRole.UserRole).id for item in window.task_list.selectedItems()
    } == {"one", "two"}
    assert window.task_list.currentItem().data(Qt.ItemDataRole.UserRole).id == "two"
    assert window._detail_task_id == "two"
    for row in range(window.task_list.count()):
        item = window.task_list.item(row)
        task_id = item.data(Qt.ItemDataRole.UserRole).id
        assert window.task_list.itemWidget(item) is widgets[task_id]


def test_unchanged_task_refresh_preserves_detail_text_selection(qtbot, tmp_path):
    service = TaskService(SQLiteTaskRepository(tmp_path / "tasks.db"), id_factory=lambda: "task")
    task = service.create_tasks(CreateTaskRequest(
        addresses="https://cdn.example/video.m3u8", save_directory=str(tmp_path),
    ))[0]
    window = MainWindow(service)
    qtbot.addWidget(window)
    window.show()
    window._force_exit = True
    window.task_list.setCurrentRow(0)
    text = window.info_view.toPlainText()
    start = text.index(task.source_url)
    cursor = window.info_view.textCursor()
    cursor.setPosition(start)
    cursor.setPosition(start + len(task.source_url), QTextCursor.MoveMode.KeepAnchor)
    window.info_view.setTextCursor(cursor)

    window.refresh_tasks()

    assert window.info_view.textCursor().selectedText() == task.source_url


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
    assert not window.pause_task_button.isEnabled()
    assert "选择" in window.pause_task_button.toolTip()

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
    assert window.extraction_mode_combo.currentData() == "smart"
    assert window.notification_check.isChecked() is False

    window.download_limit_spin.setValue(5)
    window.threshold_spin.setValue(8)
    window.extraction_mode_combo.setCurrentIndex(
        window.extraction_mode_combo.findData("deep")
    )
    window.notification_check.setChecked(True)
    qtbot.mouseClick(window.save_settings_button, Qt.MouseButton.LeftButton)

    saved = repository.load_app_settings()
    assert saved.download_task_limit == 5
    assert saved.auto_download_threshold == 8
    assert saved.extraction_mode == "deep"
    assert saved.completion_notification is True
    window._force_exit = True


def test_global_extraction_mode_is_used_by_quick_start_and_new_task_dialog(qtbot, tmp_path):
    repository = SQLiteTaskRepository(tmp_path / "tasks.db")
    service = TaskService(repository, id_factory=lambda: "quick")
    service.save_app_settings(replace(
        service.load_app_settings(), extraction_mode="normal",
    ))
    window = MainWindow(service)
    qtbot.addWidget(window)
    window.show()
    window._force_exit = True

    window.open_new_task_dialog()
    assert window.new_task_dialog.extraction_mode.currentData() == "normal"
    window.new_task_dialog.reject()

    window.quick_address_edit.setText("https://site.example/watch/quick")
    qtbot.mouseClick(window.quick_start_button, Qt.MouseButton.LeftButton)

    assert service.get_task("quick").settings.extraction_mode == "normal"


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
