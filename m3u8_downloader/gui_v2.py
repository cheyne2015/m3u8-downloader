"""基于 PySide6 的新版任务管理界面。"""

from __future__ import annotations

from dataclasses import replace
import os
from pathlib import Path

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtGui import QAction, QColor, QFont
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QFileDialog,
    QFormLayout,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QInputDialog,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMenu,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QSplitter,
    QStackedWidget,
    QStyle,
    QSpinBox,
    QTabWidget,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QSystemTrayIcon,
    QVBoxLayout,
    QWidget,
)

from .tasking import (
    CreateTaskRequest,
    DownloadStatus,
    DuplicateSourceError,
    ExtractionStatus,
    Task,
    TaskService,
    TaskSettings,
)
from .secrets_v2 import protect_secret


_STYLE = """
QWidget { background: #111418; color: #e8ebef; font-family: "Microsoft YaHei UI"; font-size: 13px; }
QMainWindow { background: #0d1014; }
#sidebar { background: #171b20; border-right: 1px solid #292e35; }
#sidebarTitle { color: #8c96a3; font-size: 12px; padding: 8px 10px; }
QPushButton { border: 0; border-radius: 7px; padding: 9px 12px; text-align: left; }
QPushButton:hover { background: #272d35; }
QPushButton:checked { background: #29323e; color: #65a6ff; }
#newTask { background: #3478f6; color: white; font-weight: 600; text-align: center; padding: 9px 18px; }
#newTask:hover { background: #4385fa; }
QLineEdit, QTextEdit, QPlainTextEdit, QComboBox { background: #1b2026; border: 1px solid #303740; border-radius: 7px; padding: 7px; }
QLineEdit:focus, QTextEdit:focus { border-color: #4b8cf7; }
QListWidget { border: 0; background: transparent; outline: none; }
QListWidget::item { border-bottom: 1px solid #242a31; }
QListWidget::item:selected { background: #222a34; }
QTabWidget::pane { border: 0; border-top: 1px solid #2a3038; }
QTabBar::tab { padding: 10px 16px; color: #aeb6c1; }
QTabBar::tab:selected { color: #67a7ff; border-bottom: 2px solid #4b8cf7; }
QSplitter::handle { background: #2a3038; }
#muted { color: #8c96a3; }
#taskName { font-weight: 600; font-size: 14px; }
#status { color: #75aefc; }
#logHeader { background: #181c21; border-top: 1px solid #2a3038; padding: 5px 10px; }
QDialog { background: #15191e; }
"""


def _task_status(task: Task) -> str:
    extraction = {
        ExtractionStatus.WAITING: "等待提取",
        ExtractionStatus.RUNNING: "提取中",
        ExtractionStatus.PAUSED: "提取已暂停",
        ExtractionStatus.FAILED: "提取失败",
    }.get(task.extraction_status)
    download = {
        DownloadStatus.NOT_READY: None,
        DownloadStatus.PENDING_SELECTION: "待选择",
        DownloadStatus.WAITING: "等待下载",
        DownloadStatus.RUNNING: "下载中",
        DownloadStatus.MERGING: "合并中",
        DownloadStatus.PAUSED: "已暂停",
        DownloadStatus.RETRY_WAIT: "重试等待",
        DownloadStatus.PARTIAL_FAILURE: "部分失败",
        DownloadStatus.COMPLETED: "已完成",
    }.get(task.download_status)
    if download and extraction == "提取中":
        return f"{download} · 仍在提取"
    return download or extraction or "准备中"


_ITEM_STATUS_TEXT = {
    "unselected": "未选择",
    "waiting": "等待下载",
    "downloading": "下载中",
    "paused": "已暂停",
    "retry_wait": "重试等待",
    "failed": "失败",
    "skipped": "已跳过",
    "completed": "已完成",
}


class TaskCard(QWidget):
    def __init__(self, task: Task, items=None, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 10, 14, 10)
        layout.setSpacing(4)
        name = QLabel(task.name)
        name.setObjectName("taskName")
        self.status_label = QLabel(_task_status(task))
        self.status_label.setObjectName("status")
        path = QLabel(task.save_directory)
        path.setObjectName("muted")
        path.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(name)
        layout.addWidget(self.status_label)
        selected = [item for item in (items or []) if item.status.value != "unselected"]
        if selected:
            total = sum(item.total_bytes or item.estimated_bytes or 0 for item in selected)
            done = sum(
                (item.total_bytes or item.estimated_bytes or 0)
                if item.status.value in {"completed", "skipped"}
                else item.downloaded_bytes
                for item in selected
            )
            if total:
                progress_value = max(0, min(100, int(done * 100 / total)))
            else:
                handled = sum(item.status.value in {"completed", "skipped"} for item in selected)
                progress_value = int(handled * 100 / len(selected))
            progress = QProgressBar()
            progress.setRange(0, 100)
            progress.setValue(progress_value)
            progress.setTextVisible(True)
            progress.setFixedHeight(8)
            layout.addWidget(progress)
        layout.addWidget(path)


class NewTaskDialog(QDialog):
    tasks_created = Signal(list)

    def __init__(self, service: TaskService, last_directory: str, parent=None) -> None:
        super().__init__(parent)
        self._service = service
        self.setWindowTitle("新建下载任务")
        self.resize(620, 490)
        root = QVBoxLayout(self)
        title = QLabel("新建链接")
        title.setStyleSheet("font-size: 20px; font-weight: 600;")
        hint = QLabel("粘贴网页链接或 m3u8 链接；批量创建时每行一条")
        hint.setObjectName("muted")
        self.address_edit = QTextEdit()
        self.address_edit.setPlaceholderText("https://example.com/video\nhttps://example.com/index.m3u8")
        self.address_edit.setMinimumHeight(115)
        root.addWidget(title)
        root.addWidget(hint)
        root.addWidget(self.address_edit)

        directory_row = QHBoxLayout()
        self.directory_edit = QLineEdit(last_directory)
        self.directory_edit.setPlaceholderText("选择保存目录")
        browse = QPushButton("浏览…")
        browse.clicked.connect(self._browse_directory)
        directory_row.addWidget(self.directory_edit, 1)
        directory_row.addWidget(browse)
        root.addWidget(QLabel("保存目录"))
        root.addLayout(directory_row)

        self.advanced_button = QPushButton("高级选项  ▸")
        self.advanced_button.setCheckable(True)
        self.advanced_button.toggled.connect(self._toggle_advanced)
        root.addWidget(self.advanced_button)
        self.advanced_panel = QWidget()
        form = QFormLayout(self.advanced_panel)
        self.extraction_mode = QComboBox()
        self.extraction_mode.addItems(["智能模式（深度优先）", "仅深度模式", "普通模式"])
        self.proxy_edit = QLineEdit()
        self.referer_edit = QLineEdit()
        self.user_agent_edit = QLineEdit()
        self.cookie_edit = QLineEdit()
        self.cookie_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.speed_limit_edit = QLineEdit()
        self.speed_limit_edit.setPlaceholderText("不限速")
        form.addRow("提取模式", self.extraction_mode)
        form.addRow("代理", self.proxy_edit)
        form.addRow("来源地址", self.referer_edit)
        form.addRow("浏览器标识", self.user_agent_edit)
        form.addRow("登录信息", self.cookie_edit)
        form.addRow("本任务限速", self.speed_limit_edit)
        self.advanced_panel.hide()
        root.addWidget(self.advanced_panel)

        actions = QHBoxLayout()
        actions.addStretch()
        cancel = QPushButton("取消")
        cancel.clicked.connect(self.reject)
        self.start_button = QPushButton("开始")
        self.start_button.setObjectName("newTask")
        self.start_button.clicked.connect(self._create)
        actions.addWidget(cancel)
        actions.addWidget(self.start_button)
        root.addLayout(actions)

    def _toggle_advanced(self, visible: bool) -> None:
        self.advanced_panel.setVisible(visible)
        self.advanced_button.setText("高级选项  ▾" if visible else "高级选项  ▸")

    def _browse_directory(self) -> None:
        selected = QFileDialog.getExistingDirectory(
            self, "选择保存目录", self.directory_edit.text()
        )
        if selected:
            self.directory_edit.setText(selected)

    def _create(self) -> None:
        if not self.address_edit.toPlainText().strip():
            self.address_edit.setFocus()
            return
        if not self.directory_edit.text().strip():
            self.directory_edit.setFocus()
            return
        app_settings = self._service.load_app_settings()
        extraction_modes = ["smart", "deep", "normal"]
        task_settings = TaskSettings(
            auto_download_threshold=app_settings.auto_download_threshold,
            extraction_mode=extraction_modes[self.extraction_mode.currentIndex()],
            proxy=self.proxy_edit.text().strip(),
            referer=self.referer_edit.text().strip(),
            user_agent=self.user_agent_edit.text().strip(),
            protected_cookie=protect_secret(self.cookie_edit.text()),
            speed_limit=int(self.speed_limit_edit.text() or 0),
            segment_threads=app_settings.segment_threads,
            request_retries=app_settings.request_retries,
            task_retries=app_settings.task_retries,
            retry_delay_seconds=app_settings.retry_delay_seconds,
        )
        request = CreateTaskRequest(
            addresses=self.address_edit.toPlainText(),
            save_directory=self.directory_edit.text(),
            settings=task_settings,
        )
        try:
            tasks = self._service.create_tasks(request)
        except DuplicateSourceError:
            answer = QMessageBox.question(
                self,
                "链接已存在",
                "任务列表中已有相同链接。是否仍创建一个副本？",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if answer is not QMessageBox.StandardButton.Yes:
                return
            tasks = self._service.create_tasks(request, allow_duplicates=True)
        if tasks:
            self.tasks_created.emit(tasks)
            self.accept()


class MainWindow(QMainWindow):
    def __init__(self, service: TaskService, parent=None) -> None:
        super().__init__(parent)
        self._service = service
        self._force_exit = False
        self.new_task_dialog: NewTaskDialog | None = None
        self.setWindowTitle("m3u8 下载器")
        self.resize(1280, 790)
        self.setMinimumSize(980, 640)
        QApplication.instance().setFont(QFont("Microsoft YaHei UI", 10))
        self.setStyleSheet(_STYLE)
        self._build_ui()
        self.refresh_tasks()
        self._refresh_logs()

    def _build_ui(self) -> None:
        root = QWidget()
        self.setCentralWidget(root)
        outer = QHBoxLayout(root)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        sidebar = QFrame()
        sidebar.setObjectName("sidebar")
        sidebar.setFixedWidth(172)
        side = QVBoxLayout(sidebar)
        side.setContentsMargins(12, 18, 12, 14)
        brand = QLabel("M3U8 下载器")
        brand.setStyleSheet("font-size: 17px; font-weight: 700; padding: 8px;")
        section = QLabel("任务")
        section.setObjectName("sidebarTitle")
        self.downloading_button = self._nav_button("↓  下载中", True)
        self.completed_button = self._nav_button("✓  已完成")
        self.settings_button = self._nav_button("⚙  设置")
        self.downloading_button.setText("下载中")
        self.completed_button.setText("已完成")
        self.settings_button.setText("设置")
        self.downloading_button.clicked.connect(lambda: self._switch_view(0))
        self.completed_button.clicked.connect(lambda: self._switch_view(1))
        self.settings_button.clicked.connect(lambda: self._switch_view(2))
        side.addWidget(brand)
        side.addWidget(section)
        side.addWidget(self.downloading_button)
        side.addWidget(self.completed_button)
        side.addStretch()
        side.addWidget(self.settings_button)
        outer.addWidget(sidebar)

        vertical = QSplitter(Qt.Orientation.Vertical)
        horizontal = QSplitter(Qt.Orientation.Horizontal)
        horizontal.addWidget(self._build_task_area())
        horizontal.addWidget(self._build_detail_area())
        horizontal.setSizes([700, 400])
        horizontal.setCollapsible(0, False)
        vertical.addWidget(horizontal)
        vertical.addWidget(self._build_log_area())
        vertical.setSizes([610, 150])
        vertical.setCollapsible(1, False)
        outer.addWidget(vertical, 1)

    @staticmethod
    def _nav_button(text: str, checked: bool = False) -> QPushButton:
        button = QPushButton(text)
        button.setCheckable(True)
        button.setChecked(checked)
        return button

    def _build_task_area(self) -> QWidget:
        panel = QWidget()
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(22, 18, 18, 10)
        toolbar = QHBoxLayout()
        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText("搜索任务、链接或文件")
        self.search_edit.textChanged.connect(self.refresh_tasks)
        self.pause_task_button = QPushButton("暂停")
        self.pause_task_button.clicked.connect(self._pause_current_task)
        self.resume_task_button = QPushButton("继续")
        self.resume_task_button.clicked.connect(self._resume_current_task)
        self.new_task_button = QPushButton("＋ 新建链接")
        self.new_task_button.setObjectName("newTask")
        self.new_task_button.clicked.connect(self.open_new_task_dialog)
        toolbar.addWidget(self.search_edit, 1)
        toolbar.addWidget(self.pause_task_button)
        toolbar.addWidget(self.resume_task_button)
        toolbar.addWidget(self.new_task_button)
        layout.addLayout(toolbar)
        self.page_title = QLabel("下载中")
        self.page_title.setStyleSheet("font-size: 22px; font-weight: 600; padding: 12px 2px 6px;")
        layout.addWidget(self.page_title)
        self.pages = QStackedWidget()
        self.task_list = QListWidget()
        self.task_list.currentItemChanged.connect(self._show_task_detail)
        self.task_list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.task_list.customContextMenuRequested.connect(
            lambda position: self._show_task_menu(self.task_list, position)
        )
        self.completed_list = QListWidget()
        self.completed_list.currentItemChanged.connect(self._show_task_detail)
        self.completed_list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.completed_list.customContextMenuRequested.connect(
            lambda position: self._show_task_menu(self.completed_list, position)
        )
        settings_page = self._build_settings_page()
        self.pages.addWidget(self.task_list)
        self.pages.addWidget(self.completed_list)
        self.pages.addWidget(settings_page)
        layout.addWidget(self.pages, 1)
        return panel

    def _build_settings_page(self) -> QWidget:
        page = QWidget()
        root = QVBoxLayout(page)
        root.setContentsMargins(8, 8, 24, 24)
        root.setSpacing(12)

        concurrency = QGroupBox("任务并发")
        concurrency_form = QFormLayout(concurrency)
        self.download_limit_spin = QSpinBox()
        self.download_limit_spin.setRange(1, 6)
        self.extraction_limit_spin = QSpinBox()
        self.extraction_limit_spin.setRange(1, 6)
        self.segment_threads_spin = QSpinBox()
        self.segment_threads_spin.setRange(1, 32)
        concurrency_form.addRow("同时下载父任务数", self.download_limit_spin)
        concurrency_form.addRow("同时提取网页数", self.extraction_limit_spin)
        concurrency_form.addRow("每个 m3u8 分片线程", self.segment_threads_spin)

        behavior = QGroupBox("自动处理")
        behavior_form = QFormLayout(behavior)
        self.threshold_spin = QSpinBox()
        self.threshold_spin.setRange(1, 20)
        self.notification_check = QCheckBox("父任务全部完成时显示 Windows 通知")
        self.close_to_tray_check = QCheckBox("关闭窗口时最小化到托盘")
        behavior_form.addRow("自动下载候选阈值", self.threshold_spin)
        behavior_form.addRow("", self.notification_check)
        behavior_form.addRow("", self.close_to_tray_check)

        appearance = QGroupBox("界面与日志")
        appearance_form = QFormLayout(appearance)
        self.theme_combo = QComboBox()
        self.theme_combo.addItem("深色", "dark")
        self.theme_combo.addItem("浅色", "light")
        self.theme_combo.addItem("跟随系统", "system")
        self.log_days_spin = QSpinBox()
        self.log_days_spin.setRange(1, 3650)
        self.log_days_spin.setSuffix(" 天")
        appearance_form.addRow("主题", self.theme_combo)
        appearance_form.addRow("日志保留", self.log_days_spin)

        self.save_settings_button = QPushButton("保存设置")
        self.save_settings_button.setObjectName("newTask")
        self.save_settings_button.clicked.connect(self._save_settings)
        root.addWidget(concurrency)
        root.addWidget(behavior)
        root.addWidget(appearance)
        root.addStretch()
        root.addWidget(self.save_settings_button, 0, Qt.AlignmentFlag.AlignRight)
        self._load_settings_controls()
        return page

    def _load_settings_controls(self) -> None:
        settings = self._service.load_app_settings()
        self.download_limit_spin.setValue(settings.download_task_limit)
        self.extraction_limit_spin.setValue(settings.extraction_task_limit)
        self.threshold_spin.setValue(settings.auto_download_threshold)
        self.segment_threads_spin.setValue(settings.segment_threads)
        self.notification_check.setChecked(settings.completion_notification)
        self.close_to_tray_check.setChecked(settings.close_to_tray)
        self.log_days_spin.setValue(settings.log_retention_days)
        index = self.theme_combo.findData(settings.theme)
        self.theme_combo.setCurrentIndex(max(0, index))

    def _save_settings(self) -> None:
        settings = replace(
            self._service.load_app_settings(),
            download_task_limit=self.download_limit_spin.value(),
            extraction_task_limit=self.extraction_limit_spin.value(),
            auto_download_threshold=self.threshold_spin.value(),
            segment_threads=self.segment_threads_spin.value(),
            completion_notification=self.notification_check.isChecked(),
            close_to_tray=self.close_to_tray_check.isChecked(),
            log_retention_days=self.log_days_spin.value(),
            theme=self.theme_combo.currentData(),
        )
        self._service.save_app_settings(settings)
        self.log_view.appendPlainText("设置已保存")

    def _build_detail_area(self) -> QWidget:
        panel = QFrame()
        panel.setStyleSheet("QFrame { background: #171b20; border-left: 1px solid #292e35; }")
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(16, 16, 16, 12)
        self.detail_title = QLabel("任务详情")
        self.detail_title.setStyleSheet("font-size: 18px; font-weight: 600;")
        self.detail_tabs = QTabWidget()
        self.item_table = QTableWidget(0, 4)
        self.item_table.setHorizontalHeaderLabels(["名称", "大小/时长", "状态", "进度"])
        self.item_table.verticalHeader().hide()
        self.item_table.horizontalHeader().setStretchLastSection(True)
        self.item_table.setShowGrid(False)
        self.item_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.info_view = QPlainTextEdit()
        self.info_view.setReadOnly(True)
        self.detail_tabs.addTab(self.item_table, "下载项")
        self.detail_tabs.addTab(self.info_view, "详细信息")
        self.download_selected_button = QPushButton("下载选中项")
        self.download_selected_button.setObjectName("newTask")
        self.download_selected_button.setEnabled(False)
        self.download_selected_button.clicked.connect(self._download_selected_items)
        layout.addWidget(self.detail_title)
        layout.addWidget(self.detail_tabs, 1)
        layout.addWidget(self.download_selected_button, 0, Qt.AlignmentFlag.AlignRight)
        return panel

    def _build_log_area(self) -> QWidget:
        panel = QWidget()
        self.log_panel = panel
        panel.setMinimumHeight(105)
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(0, 0, 0, 0)
        header = QLabel("运行日志    全部任务  ·  全部级别")
        header.setObjectName("logHeader")
        self.log_view = QPlainTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setPlaceholderText("任务状态、提取结果和下载过程会显示在这里")
        layout.addWidget(header)
        layout.addWidget(self.log_view, 1)
        return panel

    def _switch_view(self, index: int) -> None:
        buttons = [self.downloading_button, self.completed_button, self.settings_button]
        for button_index, button in enumerate(buttons):
            button.setChecked(button_index == index)
        self.pages.setCurrentIndex(index)
        self.page_title.setText(["下载中", "已完成", "设置"][index])
        self.refresh_tasks()

    def open_new_task_dialog(self) -> None:
        tasks = self._service.list_tasks()
        last_directory = tasks[-1].save_directory if tasks else str(Path.home() / "Downloads")
        self.new_task_dialog = NewTaskDialog(self._service, last_directory, self)
        self.new_task_dialog.tasks_created.connect(self._tasks_created)
        self.new_task_dialog.open()

    def _tasks_created(self, tasks: list[Task]) -> None:
        for task in tasks:
            self._service.add_log(task.id, "信息", "任务", "任务已创建")
        self.refresh_tasks()
        self._refresh_logs()

    def refresh_tasks(self) -> None:
        selected_id = ""
        current_list = self.completed_list if self.pages.currentIndex() == 1 else self.task_list
        current = current_list.currentItem()
        if current is not None:
            selected_id = current.data(Qt.ItemDataRole.UserRole).id
        query = self.search_edit.text().strip().lower() if hasattr(self, "search_edit") else ""
        tasks = self._service.list_tasks()
        self.task_list.clear()
        self.completed_list.clear()
        for task in tasks:
            if query and query not in task.name.lower() and query not in task.source_url.lower():
                continue
            target = (
                self.completed_list
                if task.download_status is DownloadStatus.COMPLETED
                else self.task_list
            )
            item = QListWidgetItem(task.name)
            item.setData(Qt.ItemDataRole.UserRole, task)
            task_items = self._service.list_items(task.id)
            item.setSizeHint(QSize(0, 88 if task_items else 72))
            target.addItem(item)
            target.setItemWidget(item, TaskCard(task, task_items))
            if task.id == selected_id:
                target.setCurrentItem(item)

    def _show_task_detail(self, current: QListWidgetItem | None, _previous) -> None:
        if current is None:
            return
        task: Task = current.data(Qt.ItemDataRole.UserRole)
        self._detail_task_id = task.id
        self.detail_title.setText(task.name)
        items = self._service.list_items(task.id)
        self.download_selected_button.setEnabled(bool(items))
        self.item_table.setRowCount(len(items))
        for row, item in enumerate(items):
            display_name = item.label or f"下载项 {item.output_index:02d}"
            if item.estimated_bytes is not None:
                estimate = f"约 {item.estimated_bytes / 1024 / 1024:.1f} MB"
            elif item.duration_seconds is not None:
                estimate = f"约 {item.duration_seconds:.0f} 秒"
            else:
                estimate = "正在估算"
            total = item.total_bytes or item.estimated_bytes or 0
            progress_value = int(item.downloaded_bytes * 100 / total) if total else 0
            if item.status.value == "completed":
                progress_value = 100
            values = [
                display_name,
                estimate,
                _ITEM_STATUS_TEXT[item.status.value],
                f"{max(0, min(100, progress_value))}%",
            ]
            for column, value in enumerate(values):
                cell = QTableWidgetItem(value)
                cell.setToolTip(item.source_url)
                if column == 0:
                    cell.setData(Qt.ItemDataRole.UserRole, item.id)
                    if item.status.value in {"unselected", "waiting"}:
                        cell.setFlags(cell.flags() | Qt.ItemFlag.ItemIsUserCheckable)
                        cell.setCheckState(
                            Qt.CheckState.Checked
                            if item.status.value == "waiting"
                            else Qt.CheckState.Unchecked
                        )
                self.item_table.setItem(row, column, cell)
        self.info_view.setPlainText(
            f"状态：{_task_status(task)}\n"
            f"来源：{task.source_url}\n"
            f"保存位置：{task.save_directory}\n"
            f"创建时间：{task.created_at:%Y-%m-%d %H:%M:%S}"
        )
        self._refresh_logs(task.id)

    def _download_selected_items(self) -> None:
        task_id = getattr(self, "_detail_task_id", "")
        if not task_id:
            return
        selected = []
        for row in range(self.item_table.rowCount()):
            cell = self.item_table.item(row, 0)
            if cell.checkState() is Qt.CheckState.Checked:
                selected.append(cell.data(Qt.ItemDataRole.UserRole))
        self._service.select_items_for_download(task_id, selected)
        self.refresh_tasks()
        for row in range(self.task_list.count()):
            item = self.task_list.item(row)
            if item.data(Qt.ItemDataRole.UserRole).id == task_id:
                self.task_list.setCurrentItem(item)
                break
        self._service.add_log(task_id, "信息", "任务", f"已选择 {len(selected)} 个下载项")
        self._refresh_logs(task_id)

    def _pause_current_task(self) -> None:
        item = self.task_list.currentItem()
        if item is None:
            return
        task_id = item.data(Qt.ItemDataRole.UserRole).id
        controller = getattr(self, "background_controller", None)
        if controller is None:
            self._service.pause_task(task_id)
        else:
            controller.pause_task(task_id)
        self._service.add_log(task_id, "信息", "任务", "任务已暂停")
        self.refresh_tasks()

    def _resume_current_task(self) -> None:
        item = self.task_list.currentItem()
        if item is None:
            return
        task_id = item.data(Qt.ItemDataRole.UserRole).id
        controller = getattr(self, "background_controller", None)
        if controller is None:
            self._service.resume_task(task_id)
        else:
            controller.resume_task(task_id)
        self._service.add_log(task_id, "信息", "任务", "任务已继续")
        self.refresh_tasks()

    def _refresh_logs(self, task_id: str | None = None) -> None:
        entries = self._service.list_logs(task_id=task_id)
        self.log_view.setPlainText("\n".join(
            f"{entry.created_at:%H:%M:%S}  [{entry.level}] "
            f"[{entry.category}]  {entry.message}"
            for entry in entries[-500:]
        ))

    def show_from_tray(self) -> None:
        self.showNormal()
        self.raise_()
        self.activateWindow()

    def request_exit(self) -> None:
        self._force_exit = True
        controller = getattr(self, "background_controller", None)
        if controller is not None:
            controller.stop()
        QApplication.quit()

    def closeEvent(self, event) -> None:
        if self._force_exit:
            event.accept()
            return
        settings = self._service.load_app_settings()
        active = any(
            task.download_status is not DownloadStatus.COMPLETED
            for task in self._service.list_tasks()
        )
        if settings.close_to_tray:
            self.hide()
            event.ignore()
            return
        if not active:
            event.accept()
            return
        box = QMessageBox(self)
        box.setWindowTitle("仍有任务未完成")
        box.setText("请选择关闭方式")
        tray_button = box.addButton("最小化到托盘", QMessageBox.ButtonRole.AcceptRole)
        exit_button = box.addButton("暂停全部并退出", QMessageBox.ButtonRole.DestructiveRole)
        cancel_button = box.addButton("取消", QMessageBox.ButtonRole.RejectRole)
        box.exec()
        clicked = box.clickedButton()
        if clicked is tray_button:
            self.hide()
            event.ignore()
        elif clicked is exit_button:
            controller = getattr(self, "background_controller", None)
            if controller is not None:
                controller.pause_all()
                controller.stop()
            else:
                self._service.pause_all()
            self._force_exit = True
            event.accept()
        else:
            event.ignore()

    def _show_task_menu(self, task_list: QListWidget, position) -> None:
        item = task_list.itemAt(position)
        if item is None:
            return
        task_list.setCurrentItem(item)
        task = item.data(Qt.ItemDataRole.UserRole)
        menu = QMenu(self)
        if task.download_status is not DownloadStatus.COMPLETED:
            menu.addAction("继续", self._resume_current_task)
            menu.addAction("暂停", self._pause_current_task)
            queue_menu = menu.addMenu("调整队列")
            queue_menu.addAction("优先下载 / 移到最前", lambda: self._move_task(task.id, "front"))
            queue_menu.addAction("上移", lambda: self._move_task(task.id, "up"))
            queue_menu.addAction("下移", lambda: self._move_task(task.id, "down"))
            queue_menu.addAction("移到最后", lambda: self._move_task(task.id, "back"))
            menu.addSeparator()
        menu.addAction("查看详情", lambda: self.detail_tabs.setCurrentIndex(1))
        menu.addAction("打开保存位置", lambda: self._open_task_directory(task))
        menu.addAction("重命名任务", lambda: self._rename_task(task))
        menu.addAction("复制原始链接", lambda: QApplication.clipboard().setText(task.source_url))
        menu.addSeparator()
        menu.addAction("删除任务", lambda: self._delete_task(task, False))
        menu.addAction("彻底删除文件", lambda: self._delete_task(task, True))
        menu.exec(task_list.mapToGlobal(position))

    @staticmethod
    def _open_task_directory(task: Task) -> None:
        directory = Path(task.save_directory)
        if directory.is_dir():
            os.startfile(str(directory))

    def _rename_task(self, task: Task) -> None:
        name, accepted = QInputDialog.getText(
            self, "重命名任务", "任务名称", text=task.name
        )
        if accepted and name.strip():
            self._service.rename_task(task.id, name)
            self._service.add_log(task.id, "信息", "任务", f"任务已重命名为 {name.strip()}")
            self.refresh_tasks()

    def _move_task(self, task_id: str, direction: str) -> None:
        self._service.move_task(task_id, direction)
        self.refresh_tasks()

    def _delete_task(self, task: Task, delete_outputs: bool) -> None:
        preview = self._service.preview_deletion(task.id)
        if delete_outputs:
            file_lines = "\n".join(str(path) for path in preview.output_files) or "没有已记录的输出文件"
            size_mb = preview.total_bytes / 1024 / 1024
            message = (
                f"将删除任务记录和以下文件（共 {size_mb:.2f} MB）：\n\n"
                f"{file_lines}\n\n此操作无法撤销。"
            )
            title = "彻底删除文件"
        else:
            message = "删除任务记录？已完成的输出文件会保留。"
            title = "删除任务"
        if QMessageBox.question(
            self, title, message,
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        ) is not QMessageBox.StandardButton.Yes:
            return
        controller = getattr(self, "background_controller", None)
        if controller is not None:
            controller.pause_task(task.id)
        self._service.delete_task(task.id, delete_outputs=delete_outputs)
        self.refresh_tasks()
        self._refresh_logs()


def run_gui_v2(service: TaskService) -> int:
    from .background_v2 import TaskBackgroundController
    from .downloader_adapter_v2 import ExistingDownloaderAdapter
    from .extractor_adapter_v2 import ExistingExtractorAdapter
    from .tasking import DownloadCoordinator, TaskCoordinator
    from .windows_v2 import SingleInstanceGuard

    app = QApplication.instance() or QApplication([])
    guard = SingleInstanceGuard("m3u8-downloader-v2-single-instance", app)
    if not guard.acquire():
        return 0
    window = MainWindow(service)
    controller = TaskBackgroundController(
        service,
        extraction_coordinator=TaskCoordinator(
            service, extractor=ExistingExtractorAdapter()
        ),
        download_coordinator=DownloadCoordinator(
            service, downloader=ExistingDownloaderAdapter()
        ),
        parent=window,
    )
    controller.changed.connect(window.refresh_tasks)
    controller.log.connect(
        lambda task_id, _message: window._refresh_logs(task_id)
    )
    window.background_controller = controller
    tray = QSystemTrayIcon(
        window.style().standardIcon(QStyle.StandardPixmap.SP_ArrowDown), window
    )
    tray.setToolTip("m3u8 下载器")
    tray_menu = QMenu(window)
    tray_menu.addAction("显示主窗口", window.show_from_tray)
    tray_menu.addAction("暂停全部", controller.pause_all)
    tray_menu.addAction("继续全部", controller.resume_all)
    tray_menu.addSeparator()
    tray_menu.addAction("退出", window.request_exit)
    tray.setContextMenu(tray_menu)
    tray.activated.connect(lambda _reason: window.show_from_tray())
    controller.parent_completed.connect(
        lambda _task_id, name: tray.showMessage(
            "下载完成", f"{name} 已全部完成", QSystemTrayIcon.MessageIcon.Information, 5000
        ) if service.load_app_settings().completion_notification else None
    )
    guard.activate_requested.connect(window.show_from_tray)
    window.tray_icon = tray
    window.single_instance_guard = guard
    tray.show()
    window.show()
    controller.start()
    return app.exec()
