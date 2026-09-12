"""新版主界面的用户可见行为测试。"""

from dataclasses import replace

from PySide6.QtCore import QPoint, Qt, QTimer
from PySide6.QtGui import QPalette
from PySide6.QtWidgets import QApplication, QMessageBox

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
        assert box.checkBox().text() == "本次运行不再确认"
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
