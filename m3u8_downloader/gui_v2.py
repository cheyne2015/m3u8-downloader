"""基于 PySide6 的新版任务管理界面。"""

from __future__ import annotations

from dataclasses import replace
import os
from pathlib import Path
import sys
from urllib.parse import urlsplit

from PySide6.QtCore import QSize, Qt, QTimer, Signal
from PySide6.QtGui import QFont, QIcon, QIntValidator
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QFileDialog,
    QFormLayout,
    QFrame,
    QGroupBox,
    QHeaderView,
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
    QScrollArea,
    QSizePolicy,
    QSplitter,
    QStackedWidget,
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
QPushButton:pressed { background: #1e242b; padding-top: 10px; padding-bottom: 8px; }
QPushButton:disabled { background: #171b20; color: #59616c; }
QPushButton:checked { background: #29323e; color: #65a6ff; }
#newTask { background: #3478f6; color: white; font-weight: 600; text-align: center; padding: 9px 18px; }
#newTask:hover { background: #4385fa; }
#newTask:pressed { background: #2868db; }
#newTask:disabled { background: #263852; color: #76869b; }
QLineEdit, QTextEdit, QPlainTextEdit, QComboBox { background: #1b2026; border: 1px solid #303740; border-radius: 7px; padding: 7px; }
QLineEdit:focus, QTextEdit:focus { border-color: #4b8cf7; }
QSpinBox { background: #1b2026; color: #e8ebef; border: 1px solid #303740; border-radius: 6px; padding: 5px 28px 5px 7px; min-height: 18px; }
QSpinBox::up-button, QSpinBox::down-button { subcontrol-origin: border; width: 22px; background: #252b33; border-left: 1px solid #3a424d; }
QSpinBox::up-button { subcontrol-position: top right; border-top-right-radius: 6px; }
QSpinBox::down-button { subcontrol-position: bottom right; border-bottom-right-radius: 6px; }
QSpinBox::up-button:hover, QSpinBox::down-button:hover { background: #313945; }
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
#taskCard, #taskCard QLabel { background: transparent; }
QProgressBar { background: #2b3139; border: 0; border-radius: 3px; }
QProgressBar::chunk { background: #3d8bfd; border-radius: 3px; }
#logHeader { background: #181c21; border-top: 1px solid #2a3038; padding: 5px 10px; }
QDialog { background: #15191e; }
#detailPanel { background: #171b20; border-left: 1px solid #292e35; }
#feedback { background: #26313d; border: 1px solid #405164; border-radius: 6px; padding: 5px 10px; }
"""

_LIGHT_STYLE = """
QWidget { background: #f5f7fa; color: #20242a; font-family: "Microsoft YaHei UI"; font-size: 13px; }
QMainWindow { background: #eef1f5; }
#sidebar { background: #ffffff; border-right: 1px solid #d8dde5; }
#sidebarTitle, #muted { color: #687383; }
QPushButton { border: 0; border-radius: 7px; padding: 9px 12px; text-align: left; }
QPushButton:hover { background: #e8edf4; }
QPushButton:pressed { background: #d9e1eb; padding-top: 10px; padding-bottom: 8px; }
QPushButton:disabled { background: #eef1f5; color: #a1a9b4; }
QPushButton:checked { background: #e1ebfa; color: #216bd6; }
#newTask { background: #3478f6; color: white; font-weight: 600; text-align: center; }
#newTask:hover { background: #4385fa; }
#newTask:pressed { background: #2868db; }
#newTask:disabled { background: #b8c9e5; color: #eef3fa; }
QLineEdit, QTextEdit, QPlainTextEdit, QComboBox, QSpinBox, QTableWidget {
  background: #ffffff; border: 1px solid #cfd6df; border-radius: 7px; padding: 7px;
}
QSpinBox { color: #20242a; padding: 5px 28px 5px 7px; min-height: 18px; }
QSpinBox::up-button, QSpinBox::down-button { subcontrol-origin: border; width: 22px; background: #edf1f6; border-left: 1px solid #c5cdd8; }
QSpinBox::up-button { subcontrol-position: top right; border-top-right-radius: 6px; }
QSpinBox::down-button { subcontrol-position: bottom right; border-bottom-right-radius: 6px; }
QSpinBox::up-button:hover, QSpinBox::down-button:hover { background: #dfe7f1; }
QListWidget { border: 0; background: transparent; outline: none; }
QListWidget::item { border-bottom: 1px solid #dde2e9; }
QListWidget::item:selected { background: #e7eef8; }
QTabWidget::pane { border: 0; border-top: 1px solid #d5dbe3; }
QTabBar::tab { padding: 10px 16px; color: #56616f; }
QTabBar::tab:selected { color: #216bd6; border-bottom: 2px solid #3478f6; }
QSplitter::handle { background: #d8dde5; }
#logHeader { background: #ffffff; border-top: 1px solid #d8dde5; padding: 5px 10px; }
QDialog { background: #f5f7fa; }
#detailPanel { background: #ffffff; border-left: 1px solid #d8dde5; }
#taskCard, #taskCard QLabel { background: transparent; }
QProgressBar { background: #dce2ea; border: 0; border-radius: 3px; }
QProgressBar::chunk { background: #3478f6; border-radius: 3px; }
#feedback { background: #e7eef8; border: 1px solid #b8c8dc; border-radius: 6px; padding: 5px 10px; }
"""


def _asset_path(name: str) -> Path:
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent.parent))
    if getattr(sys, "frozen", False):
        return base / "m3u8_downloader" / "assets" / name
    return Path(__file__).resolve().parent / "assets" / name


def _theme_style(theme: str) -> str:
    light = theme == "light"
    suffix = "light" if light else "dark"
    up = _asset_path(f"spin-up-{suffix}.png").as_posix()
    down = _asset_path(f"spin-down-{suffix}.png").as_posix()
    return (_LIGHT_STYLE if light else _STYLE) + f"""
QSpinBox::up-arrow {{ image: url(\"{up}\"); width: 12px; height: 8px; }}
QSpinBox::down-arrow {{ image: url(\"{down}\"); width: 12px; height: 8px; }}
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
    if task.extraction_status is ExtractionStatus.COMPLETED and download is None:
        return "未找到可下载链接"
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


def _format_bytes(value: float) -> str:
    value = max(0.0, float(value))
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if value < 1024 or unit == "TB":
            return f"{value:.0f} {unit}" if unit == "B" else f"{value:.1f} {unit}"
        value /= 1024
    return "0 B"


def _format_duration(seconds: float) -> str:
    seconds = max(0, int(seconds))
    hours, remainder = divmod(seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    return f"{hours:d}:{minutes:02d}:{seconds:02d}" if hours else f"{minutes:d}:{seconds:02d}"


def _set_button_enabled(button: QPushButton, enabled: bool) -> None:
    button.setEnabled(enabled)
    button.setCursor(
        Qt.CursorShape.PointingHandCursor if enabled else Qt.CursorShape.ArrowCursor
    )


def _install_button_cursors(root: QWidget) -> None:
    for button in root.findChildren(QPushButton):
        _set_button_enabled(button, button.isEnabled())


class TaskCard(QWidget):
    def __init__(self, task: Task, items=None, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("taskCard")
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
            elif any(item.progress_percent for item in selected):
                progress_value = int(sum(
                    100.0 if item.status.value in {"completed", "skipped"}
                    else item.progress_percent
                    for item in selected
                ) / len(selected))
            else:
                handled = sum(item.status.value in {"completed", "skipped"} for item in selected)
                progress_value = int(handled * 100 / len(selected))
            progress = QProgressBar()
            progress.setRange(0, 100)
            progress.setValue(progress_value)
            progress.setTextVisible(False)
            progress.setFixedHeight(8)
            layout.addWidget(progress)
            running = next((item for item in selected if item.status.value == "downloading"), None)
            total_size = sum(item.total_bytes or item.estimated_bytes or 0 for item in selected)
            details = []
            if total_size:
                details.append(f"{_format_bytes(done)} / {_format_bytes(total_size)}")
            if running is not None:
                details.append(running.label or f"下载项 {running.output_index:02d}")
                if running.speed_bps:
                    details.append(f"{_format_bytes(running.speed_bps)}/秒")
                if running.eta_seconds:
                    details.append(f"剩余 {_format_duration(running.eta_seconds)}")
            if details:
                meta = QLabel("  ·  ".join(details))
                meta.setObjectName("muted")
                layout.addWidget(meta)
        elif task.extraction_status in {ExtractionStatus.WAITING, ExtractionStatus.RUNNING}:
            progress = QProgressBar()
            progress.setRange(0, 0)
            progress.setTextVisible(False)
            progress.setFixedHeight(8)
            layout.addWidget(progress)
        layout.addWidget(path)


class NewTaskDialog(QDialog):
    tasks_created = Signal(list)
    existing_task_requested = Signal(str)

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
        self.speed_limit_edit.setPlaceholderText("字节/秒；留空使用全局设置")
        self.speed_limit_edit.setValidator(QIntValidator(0, 1_000_000_000, self))
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
        _install_button_cursors(self)

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
        except DuplicateSourceError as error:
            box = QMessageBox(self)
            box.setWindowTitle("链接已存在")
            box.setText("任务列表中已有相同链接。")
            locate = box.addButton("查看原任务", QMessageBox.ButtonRole.ActionRole)
            duplicate = box.addButton("仍创建副本", QMessageBox.ButtonRole.AcceptRole)
            box.addButton("取消", QMessageBox.ButtonRole.RejectRole)
            box.exec()
            if box.clickedButton() is locate:
                self.existing_task_requested.emit(error.existing_task_ids[0])
                self.reject()
                return
            if box.clickedButton() is not duplicate:
                return
            tasks = self._service.create_tasks(request, allow_duplicates=True)
        if tasks:
            self.tasks_created.emit(tasks)
            self.accept()


class TaskSettingsDialog(QDialog):
    def __init__(self, task: Task, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("任务设置")
        self.resize(480, 420)
        self._original = task.settings
        root = QVBoxLayout(self)
        form = QFormLayout()
        self.threshold = QSpinBox(); self.threshold.setRange(1, 20)
        self.threshold.setValue(task.settings.auto_download_threshold)
        self.threads = QSpinBox(); self.threads.setRange(1, 32)
        self.threads.setValue(task.settings.segment_threads)
        self.request_retries = QSpinBox(); self.request_retries.setRange(0, 10)
        self.request_retries.setValue(task.settings.request_retries)
        self.task_retries = QSpinBox(); self.task_retries.setRange(0, 5)
        self.task_retries.setValue(task.settings.task_retries)
        self.retry_delay = QSpinBox(); self.retry_delay.setRange(1, 3600)
        self.retry_delay.setValue(task.settings.retry_delay_seconds); self.retry_delay.setSuffix(" 秒")
        self.timeout = QSpinBox(); self.timeout.setRange(1, 3600)
        self.timeout.setValue(task.settings.timeout_seconds); self.timeout.setSuffix(" 秒")
        self.speed = QSpinBox(); self.speed.setRange(0, 102400)
        self.speed.setValue(task.settings.speed_limit // 1024 // 1024)
        self.speed.setSuffix(" MB/秒（0 为不限速）")
        self.proxy = QLineEdit(task.settings.proxy)
        self.referer = QLineEdit(task.settings.referer)
        self.user_agent = QLineEdit(task.settings.user_agent)
        self.cookie = QLineEdit(); self.cookie.setEchoMode(QLineEdit.EchoMode.Password)
        self.cookie.setPlaceholderText("留空则保持原登录信息")
        for label, widget in [
            ("自动下载候选阈值", self.threshold), ("分片线程", self.threads),
            ("网络请求重试", self.request_retries), ("任务级重试", self.task_retries),
            ("重试等待", self.retry_delay), ("请求超时", self.timeout),
            ("本任务限速", self.speed), ("代理", self.proxy),
            ("来源地址", self.referer), ("浏览器标识", self.user_agent),
            ("登录信息", self.cookie),
        ]:
            form.addRow(label, widget)
        root.addLayout(form)
        actions = QHBoxLayout(); actions.addStretch()
        cancel = QPushButton("取消"); cancel.clicked.connect(self.reject)
        save = QPushButton("保存"); save.setObjectName("newTask"); save.clicked.connect(self.accept)
        actions.addWidget(cancel); actions.addWidget(save); root.addLayout(actions)
        _install_button_cursors(self)

    def settings(self) -> TaskSettings:
        protected_cookie = self._original.protected_cookie
        if self.cookie.text():
            protected_cookie = protect_secret(self.cookie.text())
        return replace(
            self._original,
            auto_download_threshold=self.threshold.value(),
            segment_threads=self.threads.value(),
            request_retries=self.request_retries.value(),
            task_retries=self.task_retries.value(),
            retry_delay_seconds=self.retry_delay.value(),
            timeout_seconds=self.timeout.value(),
            speed_limit=self.speed.value() * 1024 * 1024,
            proxy=self.proxy.text().strip(), referer=self.referer.text().strip(),
            user_agent=self.user_agent.text().strip(), protected_cookie=protected_cookie,
        )


class MainWindow(QMainWindow):
    def __init__(self, service: TaskService, parent=None) -> None:
        super().__init__(parent)
        self._service = service
        self._force_exit = False
        self._pending_item_checks: dict[str, set[str]] = {}
        self._updating_item_table = False
        self.new_task_dialog: NewTaskDialog | None = None
        self.setWindowTitle("m3u8 下载器")
        icon = QIcon(str(_asset_path("m3u8-downloader.ico")))
        self.setWindowIcon(icon)
        QApplication.instance().setWindowIcon(icon)
        self.resize(1280, 790)
        self.setMinimumSize(980, 640)
        QApplication.instance().setFont(QFont("Microsoft YaHei UI", 10))
        self.setStyleSheet(_STYLE)
        self._build_ui()
        self._build_feedback_area()
        _install_button_cursors(self)
        self._apply_theme(self._service.load_app_settings().theme)
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
        self.main_vertical_splitter = vertical
        horizontal = QSplitter(Qt.Orientation.Horizontal)
        horizontal.addWidget(self._build_task_area())
        horizontal.addWidget(self._build_detail_area())
        horizontal.setSizes([620, 530])
        horizontal.setCollapsible(0, False)
        vertical.addWidget(horizontal)
        vertical.addWidget(self._build_log_area())
        vertical.setSizes([530, 230])
        vertical.setStretchFactor(0, 1)
        vertical.setStretchFactor(1, 0)
        vertical.setCollapsible(1, False)
        outer.addWidget(vertical, 1)

    @staticmethod
    def _nav_button(text: str, checked: bool = False) -> QPushButton:
        button = QPushButton(text)
        button.setCheckable(True)
        button.setChecked(checked)
        return button

    def _build_feedback_area(self) -> None:
        self.feedback_label = QLabel()
        self.feedback_label.setObjectName("feedback")
        self.feedback_label.hide()
        self.statusBar().addPermanentWidget(self.feedback_label)
        self._feedback_timer = QTimer(self)
        self._feedback_timer.setSingleShot(True)
        self._feedback_timer.timeout.connect(self.feedback_label.hide)

    def _show_feedback(self, message: str, duration_ms: int = 2400) -> None:
        self.feedback_label.setText(message)
        self.feedback_label.show()
        self._feedback_timer.start(duration_ms)

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
        self.stop_extraction_button = QPushButton("停止提取")
        _set_button_enabled(self.stop_extraction_button, False)
        self.stop_extraction_button.clicked.connect(self._toggle_current_extraction)
        self.new_task_button = QPushButton("＋ 新建链接")
        self.new_task_button.setObjectName("newTask")
        self.new_task_button.clicked.connect(self.open_new_task_dialog)
        toolbar.addWidget(self.search_edit, 1)
        toolbar.addWidget(self.pause_task_button)
        toolbar.addWidget(self.resume_task_button)
        toolbar.addWidget(self.stop_extraction_button)
        toolbar.addWidget(self.new_task_button)
        layout.addLayout(toolbar)
        self.quick_download_panel = QFrame()
        self.quick_download_panel.setObjectName("quickDownloadPanel")
        quick_layout = QHBoxLayout(self.quick_download_panel)
        quick_layout.setContentsMargins(10, 8, 10, 8)
        quick_layout.setSpacing(8)
        quick_layout.addWidget(QLabel("快速下载"))
        self.quick_address_edit = QLineEdit()
        self.quick_address_edit.setPlaceholderText("粘贴网页链接或 m3u8 链接，按回车快速创建任务")
        self.quick_address_edit.returnPressed.connect(self._quick_start)
        self.quick_start_button = QPushButton("快速开始")
        self.quick_start_button.setObjectName("newTask")
        self.quick_start_button.clicked.connect(self._quick_start)
        quick_layout.addWidget(self.quick_address_edit, 1)
        quick_layout.addWidget(self.quick_start_button)
        layout.addWidget(self.quick_download_panel)
        filter_row = QHBoxLayout()
        self.status_filter_combo = QComboBox()
        for text, value in [
            ("全部状态", "all"), ("提取中", "extracting"), ("下载中", "downloading"),
            ("待选择", "selection"), ("已暂停", "paused"), ("失败", "failed"),
        ]:
            self.status_filter_combo.addItem(text, value)
        self.status_filter_combo.currentIndexChanged.connect(self.refresh_tasks)
        self.completed_sort_combo = QComboBox()
        self.completed_sort_combo.addItem("按完成时间", "time")
        self.completed_sort_combo.addItem("按名称", "name")
        self.completed_sort_combo.addItem("按总大小", "size")
        self.completed_sort_combo.currentIndexChanged.connect(self.refresh_tasks)
        self.completed_sort_combo.hide()
        filter_row.addWidget(self.status_filter_combo)
        filter_row.addWidget(self.completed_sort_combo)
        filter_row.addStretch()
        layout.addLayout(filter_row)
        self.page_title = QLabel("下载中")
        self.page_title.setStyleSheet("font-size: 22px; font-weight: 600; padding: 12px 2px 6px;")
        layout.addWidget(self.page_title)
        self.pages = QStackedWidget()
        self.pages.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Ignored
        )
        self.pages.setMinimumHeight(0)
        self.task_list = QListWidget()
        self.task_list.itemSelectionChanged.connect(
            lambda: self._sync_task_detail_from_selection(self.task_list)
        )
        self.task_list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.task_list.customContextMenuRequested.connect(
            lambda position: self._show_task_menu(self.task_list, position)
        )
        self.completed_list = QListWidget()
        self.completed_list.itemSelectionChanged.connect(
            lambda: self._sync_task_detail_from_selection(self.completed_list)
        )
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
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
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
        self.global_speed_spin = QSpinBox()
        self.global_speed_spin.setRange(0, 102400)
        self.global_speed_spin.setSuffix(" MB/秒（0 为不限速）")
        concurrency_form.addRow("同时下载父任务数", self.download_limit_spin)
        concurrency_form.addRow("同时提取网页数", self.extraction_limit_spin)
        concurrency_form.addRow("每个 m3u8 分片线程", self.segment_threads_spin)
        concurrency_form.addRow("新任务默认限速", self.global_speed_spin)

        behavior = QGroupBox("自动处理")
        behavior_form = QFormLayout(behavior)
        self.threshold_spin = QSpinBox()
        self.threshold_spin.setRange(1, 20)
        self.notification_check = QCheckBox("父任务全部完成时显示 Windows 通知")
        self.close_to_tray_check = QCheckBox("关闭窗口时最小化到托盘")
        self.request_retries_spin = QSpinBox()
        self.request_retries_spin.setRange(0, 10)
        self.task_retries_spin = QSpinBox()
        self.task_retries_spin.setRange(0, 5)
        self.retry_delay_spin = QSpinBox()
        self.retry_delay_spin.setRange(1, 3600)
        self.retry_delay_spin.setSuffix(" 秒")
        behavior_form.addRow("自动下载候选阈值", self.threshold_spin)
        behavior_form.addRow("单次网络请求重试", self.request_retries_spin)
        behavior_form.addRow("任务级自动重试", self.task_retries_spin)
        behavior_form.addRow("任务重试等待", self.retry_delay_spin)
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
        scroll.setWidget(page)
        self.settings_scroll = scroll
        return scroll

    def _load_settings_controls(self) -> None:
        settings = self._service.load_app_settings()
        self.download_limit_spin.setValue(settings.download_task_limit)
        self.extraction_limit_spin.setValue(settings.extraction_task_limit)
        self.threshold_spin.setValue(settings.auto_download_threshold)
        self.segment_threads_spin.setValue(settings.segment_threads)
        self.global_speed_spin.setValue(settings.global_speed_limit // 1024 // 1024)
        self.request_retries_spin.setValue(settings.request_retries)
        self.task_retries_spin.setValue(settings.task_retries)
        self.retry_delay_spin.setValue(settings.retry_delay_seconds)
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
            global_speed_limit=self.global_speed_spin.value() * 1024 * 1024,
            request_retries=self.request_retries_spin.value(),
            task_retries=self.task_retries_spin.value(),
            retry_delay_seconds=self.retry_delay_spin.value(),
            completion_notification=self.notification_check.isChecked(),
            close_to_tray=self.close_to_tray_check.isChecked(),
            log_retention_days=self.log_days_spin.value(),
            theme=self.theme_combo.currentData(),
        )
        self._service.save_app_settings(settings)
        speed_callback = getattr(self, "global_speed_limit_changed", None)
        if speed_callback is not None:
            speed_callback(settings.global_speed_limit)
        self._apply_theme(settings.theme)
        self.log_view.appendPlainText("设置已保存")
        self._show_feedback("设置已保存")

    def _apply_theme(self, theme: str) -> None:
        if theme == "system":
            theme = (
                "dark" if QApplication.styleHints().colorScheme() is Qt.ColorScheme.Dark
                else "light"
            )
        self.setStyleSheet(_theme_style(theme))

    def _build_detail_area(self) -> QWidget:
        panel = QFrame()
        panel.setObjectName("detailPanel")
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(16, 16, 16, 12)
        self.detail_title = QLabel("任务详情")
        self.detail_title.setStyleSheet("font-size: 18px; font-weight: 600;")
        self.detail_tabs = QTabWidget()
        self.item_table = QTableWidget(0, 4)
        self.item_table.setHorizontalHeaderLabels(["名称", "大小/时长", "状态", "进度"])
        self.item_table.verticalHeader().hide()
        self.item_table.horizontalHeader().setStretchLastSection(False)
        self.item_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.item_table.setColumnWidth(1, 105)
        self.item_table.setColumnWidth(2, 72)
        self.item_table.setColumnWidth(3, 190)
        self.item_table.setShowGrid(False)
        self.item_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.item_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.item_table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.item_table.customContextMenuRequested.connect(self._show_item_menu)
        self.item_table.itemChanged.connect(self._remember_item_check)
        self.info_view = QPlainTextEdit()
        self.info_view.setReadOnly(True)
        self.detail_tabs.addTab(self.item_table, "下载项")
        self.detail_tabs.addTab(self.info_view, "详细信息")
        self.download_selected_button = QPushButton("下载选中项")
        self.download_selected_button.setObjectName("newTask")
        _set_button_enabled(self.download_selected_button, False)
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
        header = QFrame()
        header.setObjectName("logHeader")
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(10, 4, 10, 4)
        header_layout.addWidget(QLabel("运行日志"))
        self.log_scope_combo = QComboBox()
        self.log_scope_combo.addItem("当前任务", "current")
        self.log_scope_combo.addItem("全部任务", "all")
        self.log_level_combo = QComboBox()
        self.log_level_combo.addItem("全部级别", "all")
        for level in ["信息", "警告", "错误"]:
            self.log_level_combo.addItem(level, level)
        self.log_scope_combo.currentIndexChanged.connect(lambda: self._refresh_logs())
        self.log_level_combo.currentIndexChanged.connect(lambda: self._refresh_logs())
        header_layout.addWidget(self.log_scope_combo)
        header_layout.addWidget(self.log_level_combo)
        header_layout.addStretch()
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
        self.quick_download_panel.setVisible(index != 2)
        self.status_filter_combo.setVisible(index == 0)
        self.completed_sort_combo.setVisible(index == 1)
        self.refresh_tasks()
        if index == 2:
            self._clear_task_detail()

    def open_new_task_dialog(self) -> None:
        last_directory = self._last_save_directory()
        self.new_task_dialog = NewTaskDialog(self._service, last_directory, self)
        self.new_task_dialog.tasks_created.connect(self._tasks_created)
        self.new_task_dialog.existing_task_requested.connect(self._locate_existing_task)
        self.new_task_dialog.open()

    def _last_save_directory(self) -> str:
        tasks = self._service.list_tasks()
        return tasks[-1].save_directory if tasks else str(Path.home() / "Downloads")

    def _quick_start(self) -> None:
        address = self.quick_address_edit.text().strip()
        if not address:
            self.quick_address_edit.setFocus()
            self._show_feedback("请输入网页链接或 m3u8 链接")
            return
        normalized = address if "://" in address else "https://" + address
        parts = urlsplit(normalized)
        host = parts.hostname or ""
        if (
            parts.scheme.lower() not in {"http", "https"}
            or not parts.netloc
            or any(character.isspace() for character in parts.netloc)
            or (host != "localhost" and "." not in host)
        ):
            self.quick_address_edit.setFocus()
            self._show_feedback("请输入有效的网页链接或 m3u8 链接")
            return
        app_settings = self._service.load_app_settings()
        settings = TaskSettings(
            auto_download_threshold=app_settings.auto_download_threshold,
            segment_threads=app_settings.segment_threads,
            request_retries=app_settings.request_retries,
            task_retries=app_settings.task_retries,
            retry_delay_seconds=app_settings.retry_delay_seconds,
        )
        try:
            tasks = self._service.create_tasks(CreateTaskRequest(
                addresses=address,
                save_directory=self._last_save_directory(),
                settings=settings,
            ))
        except DuplicateSourceError:
            self._show_feedback("链接已存在，可在任务列表中查看")
            return
        if not tasks:
            self._show_feedback("没有可创建的链接")
            return
        self.quick_address_edit.clear()
        self._tasks_created(tasks)
        self._switch_view(0)
        self._show_task_by_id(tasks[-1].id)
        self._show_feedback("任务已添加，正在开始处理")

    def _locate_existing_task(self, task_id: str) -> None:
        task = self._service.get_task(task_id)
        self._switch_view(1 if task.download_status is DownloadStatus.COMPLETED else 0)
        self._show_task_by_id(task_id)

    def _tasks_created(self, tasks: list[Task]) -> None:
        for task in tasks:
            self._service.add_log(task.id, "信息", "任务", "任务已创建")
        self.refresh_tasks()
        self._refresh_logs()
        if tasks:
            self._show_feedback(f"已创建 {len(tasks)} 个任务")

    def refresh_tasks(self) -> None:
        selected_id = ""
        current_list = self.completed_list if self.pages.currentIndex() == 1 else self.task_list
        current = current_list.currentItem()
        if current is not None:
            selected_id = current.data(Qt.ItemDataRole.UserRole).id
        query = self.search_edit.text().strip().lower() if hasattr(self, "search_edit") else ""
        tasks = self._service.list_tasks()
        active_tasks = [task for task in tasks if task.download_status is not DownloadStatus.COMPLETED]
        active_tasks.sort(key=lambda task: (task.queue_position, task.created_at, task.id))
        completed_tasks = [task for task in tasks if task.download_status is DownloadStatus.COMPLETED]
        completed_sort = self.completed_sort_combo.currentData()
        if completed_sort == "name":
            completed_tasks.sort(key=lambda task: task.name.casefold())
        elif completed_sort == "size":
            completed_tasks.sort(
                key=lambda task: sum(
                    item.total_bytes or item.estimated_bytes or 0
                    for item in self._service.list_items(task.id)
                ), reverse=True,
            )
        else:
            completed_tasks.sort(
                key=lambda task: task.completed_at or task.updated_at, reverse=True
            )
        tasks = active_tasks + completed_tasks
        self.task_list.clear()
        self.completed_list.clear()
        for task in tasks:
            task_items = self._service.list_items(task.id)
            searchable = "\n".join([
                task.name, task.source_url, task.save_directory,
                *(item.source_url for item in task_items),
                *(item.output_path for item in task_items),
            ]).lower()
            if query and query not in searchable:
                continue
            if task.download_status is not DownloadStatus.COMPLETED and not self._matches_status_filter(task):
                continue
            target = (
                self.completed_list
                if task.download_status is DownloadStatus.COMPLETED
                else self.task_list
            )
            item = QListWidgetItem()
            item.setData(Qt.ItemDataRole.UserRole, task)
            card_height = 124 if task_items else (
                94 if task.extraction_status in {
                    ExtractionStatus.WAITING, ExtractionStatus.RUNNING,
                } else 76
            )
            item.setSizeHint(QSize(0, card_height))
            target.addItem(item)
            target.setItemWidget(item, TaskCard(task, task_items))
            if task.id == selected_id and target is current_list:
                target.setCurrentItem(item)

        if self.pages.currentIndex() == 2:
            self._clear_task_detail()
        else:
            active_list = self.completed_list if self.pages.currentIndex() == 1 else self.task_list
            self._sync_task_detail_from_selection(active_list)

    def _sync_task_detail_from_selection(self, task_list: QListWidget) -> None:
        active_list = self.completed_list if self.pages.currentIndex() == 1 else self.task_list
        if self.pages.currentIndex() == 2 or task_list is not active_list:
            return
        selected = task_list.selectedItems()
        if selected:
            self._show_task_detail(selected[0], None)
        else:
            self._clear_task_detail()

    def _clear_task_detail(self) -> None:
        self._detail_task_id = ""
        self.detail_title.clear()
        self._updating_item_table = True
        self.item_table.setRowCount(0)
        self._updating_item_table = False
        self.info_view.clear()
        self.stop_extraction_button.setText("停止提取")
        _set_button_enabled(self.stop_extraction_button, False)
        _set_button_enabled(self.download_selected_button, False)

    def _show_task_detail(self, current: QListWidgetItem | None, _previous) -> None:
        if current is None:
            self._clear_task_detail()
            return
        task: Task = current.data(Qt.ItemDataRole.UserRole)
        self._detail_task_id = task.id
        extraction_active = task.extraction_status in {
            ExtractionStatus.WAITING, ExtractionStatus.RUNNING,
        }
        extraction_restartable = (
            task.source_kind.value == "web_page"
            and task.download_status is not DownloadStatus.COMPLETED
            and task.extraction_status in {
                ExtractionStatus.PAUSED, ExtractionStatus.FAILED, ExtractionStatus.COMPLETED,
            }
        )
        self.stop_extraction_button.setText(
            "停止提取" if extraction_active else "重新提取"
        )
        _set_button_enabled(
            self.stop_extraction_button, extraction_active or extraction_restartable
        )
        self.detail_title.setText(task.name)
        items = self._service.list_items(task.id)
        if task.id not in self._pending_item_checks:
            self._pending_item_checks[task.id] = {
                item.id for item in items if item.status.value == "waiting"
            }
        elif task.selection_mode.value == "auto":
            self._pending_item_checks[task.id].update(
                item.id for item in items if item.status.value == "waiting"
            )
        pending_checks = self._pending_item_checks[task.id]
        _set_button_enabled(self.download_selected_button, bool(items))
        best_size_by_duration = {}
        for candidate in items:
            if candidate.valid and candidate.duration_seconds and candidate.duration_seconds > 0:
                second = int(candidate.duration_seconds)
                best_size_by_duration[second] = max(
                    best_size_by_duration.get(second, 0),
                    candidate.estimated_bytes or 0,
                )
        self._updating_item_table = True
        self.item_table.setRowCount(len(items))
        for row, item in enumerate(items):
            display_name = item.label or f"下载项 {item.output_index:02d}"
            estimates = []
            if item.total_bytes:
                estimates.append(_format_bytes(item.total_bytes))
            elif item.estimated_bytes is not None:
                estimates.append(f"约 {_format_bytes(item.estimated_bytes)}")
            if item.duration_seconds is not None:
                estimates.append(_format_duration(item.duration_seconds))
            estimate = " · ".join(estimates) or "正在估算"
            total = item.total_bytes or item.estimated_bytes or 0
            progress_value = int(item.progress_percent)
            if not progress_value and total:
                progress_value = int(item.downloaded_bytes * 100 / total)
            if item.status.value == "completed":
                progress_value = 100
            duplicate_duration = (
                not item.valid
                and item.duration_seconds is not None
                and item.duration_seconds > 0
                and int(item.duration_seconds) in best_size_by_duration
                and best_size_by_duration[int(item.duration_seconds)] >= (item.estimated_bytes or 0)
            )
            values = [
                display_name,
                estimate,
                (
                    "文件缺失"
                    if item.status.value == "completed" and item.output_path
                    and not Path(item.output_path).is_file()
                    else "同时间已保留更大项"
                    if duplicate_duration
                    else _ITEM_STATUS_TEXT[item.status.value]
                ),
                (
                    f"{max(0, min(100, progress_value))}%"
                    + (f" · {_format_bytes(item.speed_bps)}/秒" if item.speed_bps else "")
                    + (f" · 剩余 {_format_duration(item.eta_seconds)}" if item.eta_seconds else "")
                ),
            ]
            for column, value in enumerate(values):
                cell = QTableWidgetItem(value)
                cell.setToolTip(item.source_url)
                if column == 0:
                    cell.setData(Qt.ItemDataRole.UserRole, item.id)
                    if item.valid and item.status.value in {"unselected", "waiting"}:
                        cell.setFlags(cell.flags() | Qt.ItemFlag.ItemIsUserCheckable)
                        cell.setCheckState(
                            Qt.CheckState.Checked
                            if item.id in pending_checks
                            else Qt.CheckState.Unchecked
                        )
                    else:
                        cell.setFlags(cell.flags() & ~Qt.ItemFlag.ItemIsUserCheckable)
                self.item_table.setItem(row, column, cell)
        self._updating_item_table = False
        self.info_view.setPlainText(
            f"状态：{_task_status(task)}\n"
            f"来源：{task.source_url}\n"
            f"保存位置：{task.save_directory}\n"
            f"创建时间：{task.created_at:%Y-%m-%d %H:%M:%S}"
        )
        self._refresh_logs(task.id)

    def _remember_item_check(self, cell: QTableWidgetItem) -> None:
        if self._updating_item_table or cell.column() != 0:
            return
        task_id = getattr(self, "_detail_task_id", "")
        item_id = cell.data(Qt.ItemDataRole.UserRole)
        if not task_id or not item_id:
            return
        checked = self._pending_item_checks.setdefault(task_id, set())
        if cell.checkState() is Qt.CheckState.Checked:
            checked.add(item_id)
        else:
            checked.discard(item_id)

    def _matches_status_filter(self, task: Task) -> bool:
        if not hasattr(self, "status_filter_combo"):
            return True
        value = self.status_filter_combo.currentData()
        if value == "all":
            return True
        if value == "extracting":
            return task.extraction_status in {ExtractionStatus.WAITING, ExtractionStatus.RUNNING}
        if value == "downloading":
            return task.download_status in {DownloadStatus.WAITING, DownloadStatus.RUNNING, DownloadStatus.MERGING}
        if value == "selection":
            return task.download_status is DownloadStatus.PENDING_SELECTION
        if value == "paused":
            return task.extraction_status is ExtractionStatus.PAUSED or task.download_status is DownloadStatus.PAUSED
        return task.extraction_status is ExtractionStatus.FAILED or task.download_status is DownloadStatus.PARTIAL_FAILURE

    def _download_selected_items(self) -> None:
        task_id = getattr(self, "_detail_task_id", "")
        if not task_id:
            self._show_feedback("请先选择一个下载任务")
            return
        selected = []
        for row in range(self.item_table.rowCount()):
            cell = self.item_table.item(row, 0)
            if cell.checkState() is Qt.CheckState.Checked:
                selected.append(cell.data(Qt.ItemDataRole.UserRole))
        if not selected:
            self._show_feedback("请先勾选要下载的项目")
            return
        self._service.select_items_for_download(task_id, selected)
        self._pending_item_checks[task_id] = set(selected)
        self.refresh_tasks()
        for row in range(self.task_list.count()):
            item = self.task_list.item(row)
            if item.data(Qt.ItemDataRole.UserRole).id == task_id:
                self.task_list.setCurrentItem(item)
                break
        self._service.add_log(task_id, "信息", "任务", f"已选择 {len(selected)} 个下载项")
        self._refresh_logs(task_id)
        self._show_feedback(f"已添加 {len(selected)} 个下载项")

    def _pause_current_task(self) -> None:
        item = self.task_list.currentItem()
        if item is None:
            self._show_feedback("请先选择一个下载任务")
            return
        task_id = item.data(Qt.ItemDataRole.UserRole).id
        controller = getattr(self, "background_controller", None)
        if controller is None:
            self._service.pause_task(task_id)
        else:
            controller.pause_task(task_id)
        self._after_task_action(task_id, "任务已暂停")

    def _resume_current_task(self) -> None:
        item = self.task_list.currentItem()
        if item is None:
            self._show_feedback("请先选择一个下载任务")
            return
        task_id = item.data(Qt.ItemDataRole.UserRole).id
        controller = getattr(self, "background_controller", None)
        if controller is None:
            self._service.resume_task(task_id)
        else:
            controller.resume_task(task_id)
        self._after_task_action(task_id, "任务已继续")

    def _toggle_current_extraction(self) -> None:
        item = self.task_list.currentItem()
        if item is None:
            self._show_feedback("请先选择一个下载任务")
            return
        task = self._service.get_task(item.data(Qt.ItemDataRole.UserRole).id)
        if task.extraction_status in {ExtractionStatus.WAITING, ExtractionStatus.RUNNING}:
            self._stop_current_extraction()
        else:
            self._restart_current_extraction(task.id)

    def _stop_current_extraction(self) -> None:
        item = self.task_list.currentItem()
        if item is None:
            self._show_feedback("请先选择一个下载任务")
            return
        task_id = item.data(Qt.ItemDataRole.UserRole).id
        controller = getattr(self, "background_controller", None)
        if controller is None:
            self._service.stop_extraction(task_id)
        else:
            controller.stop_extraction(task_id)
        self._service.add_log(task_id, "信息", "提取", "用户停止提取，已应用当前候选")
        self.refresh_tasks()
        self._refresh_logs(task_id)
        self._show_feedback("提取已停止，可随时重新提取")

    def _restart_current_extraction(self, task_id: str) -> None:
        self.stop_extraction_button.setText("正在启动…")
        _set_button_enabled(self.stop_extraction_button, False)
        controller = getattr(self, "background_controller", None)
        if controller is None:
            self._service.retry_extraction(task_id)
            started = True
        else:
            started = controller.retry_extraction(task_id)
        if not started:
            self.stop_extraction_button.setText("等待重新提取…")
            _set_button_enabled(self.stop_extraction_button, False)
            self._service.add_log(
                task_id, "信息", "提取", "正在等待旧提取线程结束，随后自动重新提取"
            )
            self._refresh_logs(task_id)
            self._show_feedback("正在结束旧提取，随后自动重新开始")
            return
        self._pending_item_checks.pop(task_id, None)
        self._service.add_log(task_id, "信息", "提取", "用户重新开始网页提取")
        self.refresh_tasks()
        self._show_task_by_id(task_id)
        self._refresh_logs(task_id)
        self._show_feedback("网页提取已重新开始")

    def _refresh_logs(self, task_id: str | None = None) -> None:
        selected_task_id = task_id or getattr(self, "_detail_task_id", "")
        show_all = (
            hasattr(self, "log_scope_combo")
            and self.log_scope_combo.currentData() == "all"
        )
        if show_all:
            selected_task_id = None
        level = None
        if hasattr(self, "log_level_combo") and self.log_level_combo.currentData() != "all":
            level = self.log_level_combo.currentData()
        entries = self._service.list_logs(task_id=selected_task_id or None, level=level)
        task_names = {task.id: task.name for task in self._service.list_tasks()}
        self.log_view.setPlainText("\n".join(
            f"{entry.created_at:%H:%M:%S}  [{entry.level}] "
            f"[{entry.category}]  "
            f"{f'[{task_names.get(entry.task_id, entry.task_id)}]  ' if show_all else ''}"
            f"{entry.message}"
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
            if task.extraction_status in {ExtractionStatus.WAITING, ExtractionStatus.RUNNING}:
                menu.addAction("停止提取", self._stop_current_extraction)
            if task.source_kind.value == "web_page" and task.extraction_status in {
                ExtractionStatus.FAILED, ExtractionStatus.COMPLETED,
            }:
                menu.addAction("重新提取", lambda: self._retry_extraction(task.id))
            if task.download_status is DownloadStatus.PARTIAL_FAILURE:
                menu.addAction("重试失败项", lambda: self._retry_failed_items(task.id))
            menu.addAction("任务设置", lambda: self._edit_task_settings(task))
            queue_menu = menu.addMenu("调整队列")
            queue_menu.addAction("优先下载 / 移到最前", lambda: self._move_task(task.id, "front"))
            queue_menu.addAction("上移", lambda: self._move_task(task.id, "up"))
            queue_menu.addAction("下移", lambda: self._move_task(task.id, "down"))
            queue_menu.addAction("移到最后", lambda: self._move_task(task.id, "back"))
            menu.addSeparator()
        else:
            menu.addAction("重新下载", lambda: self._redownload_task(task.id))
            completed_items = [
                candidate for candidate in self._service.list_items(task.id)
                if candidate.status.value == "completed"
            ]
            menu.addAction("查看下载项", lambda: self.detail_tabs.setCurrentIndex(0))
            if len(completed_items) == 1:
                completed_item = completed_items[0]
                if completed_item.output_path and Path(completed_item.output_path).is_file():
                    menu.addAction("打开文件", lambda: self._open_item_file(completed_item))
                    menu.addAction("定位文件", lambda: self._locate_item_file(completed_item))
                    menu.addAction(
                        "重命名文件",
                        lambda: self._rename_item_file(task.id, completed_item),
                    )
        menu.addAction("查看详情", lambda: self.detail_tabs.setCurrentIndex(1))
        menu.addAction("打开保存位置", lambda: self._open_task_directory(task))
        menu.addAction("重命名任务", lambda: self._rename_task(task))
        menu.addAction("复制原始链接", lambda: QApplication.clipboard().setText(task.source_url))
        menu.addSeparator()
        menu.addAction("删除任务", lambda: self._delete_task(task, False))
        menu.addAction("彻底删除文件", lambda: self._delete_task(task, True))
        menu.exec(task_list.mapToGlobal(position))

    def _edit_task_settings(self, task: Task) -> None:
        dialog = TaskSettingsDialog(task, self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self._service.update_task_settings(task.id, dialog.settings())
            self._after_task_action(task.id, "任务设置已保存")

    def _show_item_menu(self, position) -> None:
        row = self.item_table.rowAt(position.y())
        task_id = getattr(self, "_detail_task_id", "")
        if row < 0 or not task_id:
            return
        self.item_table.selectRow(row)
        item_id = self.item_table.item(row, 0).data(Qt.ItemDataRole.UserRole)
        item = self._service.get_item(task_id, item_id)
        menu = QMenu(self)
        if item.status.value in {"unselected", "paused"}:
            menu.addAction("下载 / 继续", lambda: self._resume_or_queue_item(task_id, item.id))
        if item.status.value in {"waiting", "downloading", "retry_wait"}:
            menu.addAction("暂停", lambda: self._pause_item(task_id, item.id))
            menu.addAction("跳过", lambda: self._skip_item(task_id, item.id))
        if item.status.value in {"failed", "skipped"}:
            menu.addAction("重试", lambda: self._retry_item(task_id, item.id))
        if item.status.value == "completed":
            if item.output_path and Path(item.output_path).is_file():
                menu.addAction("打开文件", lambda: self._open_item_file(item))
                menu.addAction("定位文件", lambda: self._locate_item_file(item))
            else:
                menu.addAction("重新关联文件", lambda: self._relink_item_file(task_id, item))
            menu.addAction("重命名文件", lambda: self._rename_item_file(task_id, item))
            menu.addAction("重新下载", lambda: self._redownload_item(task_id, item.id))
        menu.addAction("复制 m3u8 链接", lambda: QApplication.clipboard().setText(item.source_url))
        menu.exec(self.item_table.viewport().mapToGlobal(position))

    def _queue_item(self, task_id: str, item_id: str) -> None:
        selected = [
            item.id for item in self._service.list_items(task_id)
            if item.status.value == "waiting"
        ]
        if item_id not in selected:
            selected.append(item_id)
        self._service.select_items_for_download(task_id, selected)
        self._after_task_action(task_id, "下载项已加入队列")

    def _resume_or_queue_item(self, task_id: str, item_id: str) -> None:
        item = self._service.get_item(task_id, item_id)
        if item.status.value == "paused":
            controller = getattr(self, "background_controller", None)
            if controller is None:
                self._service.resume_item(task_id, item_id)
            else:
                controller.resume_item(task_id, item_id)
            self._after_task_action(task_id, "下载项已继续")
        else:
            self._queue_item(task_id, item_id)

    def _pause_item(self, task_id: str, item_id: str) -> None:
        controller = getattr(self, "background_controller", None)
        if controller is None:
            self._service.pause_item(task_id, item_id)
        else:
            controller.pause_item(task_id, item_id)
        self._after_task_action(task_id, "下载项已暂停")

    def _skip_item(self, task_id: str, item_id: str) -> None:
        controller = getattr(self, "background_controller", None)
        current = self._service.get_item(task_id, item_id)
        if controller is not None and current.status.value == "downloading":
            controller.pause_item(task_id, item_id)
        task = self._service.skip_item(task_id, item_id)
        self._after_task_action(task_id, "下载项已跳过")
        if task.download_status is DownloadStatus.COMPLETED:
            notifier = getattr(self, "notify_parent_completed", None)
            if notifier is not None:
                notifier(task.id, task.name)

    def _retry_item(self, task_id: str, item_id: str) -> None:
        self._service.retry_item(task_id, item_id)
        self._after_task_action(task_id, "下载项已重新加入队列")

    def _retry_failed_items(self, task_id: str) -> None:
        self._service.retry_failed_items(task_id)
        self._after_task_action(task_id, "失败项已重新加入队列")

    def _retry_extraction(self, task_id: str) -> None:
        self._restart_current_extraction(task_id)

    def _redownload_item(self, task_id: str, item_id: str) -> None:
        copied = self._service.redownload_item(task_id, item_id)
        self._after_task_action(copied.id, "已创建重新下载任务")
        self._switch_view(0)

    def _redownload_task(self, task_id: str) -> None:
        copied = self._service.redownload_task(task_id)
        self._after_task_action(copied.id, "已创建重新下载任务")
        self._switch_view(0)

    def _rename_item_file(self, task_id: str, item) -> None:
        current_name = Path(item.output_path).stem if item.output_path else item.label
        name, accepted = QInputDialog.getText(
            self, "重命名文件", "磁盘文件名称", text=current_name
        )
        if accepted and name.strip():
            try:
                self._service.rename_output_file(task_id, item.id, name)
            except (OSError, ValueError) as exc:
                QMessageBox.warning(self, "无法重命名", str(exc))
                return
            self._after_task_action(task_id, f"磁盘文件已重命名为 {name.strip()}")

    def _relink_item_file(self, task_id: str, item) -> None:
        selected, _filter = QFileDialog.getOpenFileName(
            self, "重新关联已下载文件", str(Path(item.output_path).parent),
            "视频文件 (*.mp4 *.mkv *.ts);;所有文件 (*)",
        )
        if selected:
            self._service.relink_output_file(task_id, item.id, selected)
            self._after_task_action(task_id, "已重新关联磁盘文件")

    @staticmethod
    def _open_item_file(item) -> None:
        path = Path(item.output_path)
        if path.is_file():
            os.startfile(str(path))

    @staticmethod
    def _locate_item_file(item) -> None:
        path = Path(item.output_path)
        if path.is_file():
            os.spawnl(os.P_NOWAIT, os.path.join(os.environ.get("WINDIR", "C:\\Windows"), "explorer.exe"),
                      "explorer.exe", "/select,", str(path))

    def _after_task_action(self, task_id: str, message: str) -> None:
        self._service.add_log(task_id, "信息", "任务", message)
        self.refresh_tasks()
        if self._service.list_tasks():
            try:
                self._show_task_by_id(task_id)
            except KeyError:
                pass
        self._refresh_logs(task_id)
        self._show_feedback(message)

    def _show_task_by_id(self, task_id: str) -> None:
        for task_list in (self.task_list, self.completed_list):
            for row in range(task_list.count()):
                candidate = task_list.item(row)
                if candidate.data(Qt.ItemDataRole.UserRole).id == task_id:
                    task_list.setCurrentItem(candidate)
                    return
        raise KeyError(task_id)

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
        self._show_feedback("任务顺序已调整")

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
            controller.delete_task(task.id, delete_outputs=delete_outputs)
        else:
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
    downloader_adapter = ExistingDownloaderAdapter(
        global_speed_limit=service.load_app_settings().global_speed_limit
    )
    controller = TaskBackgroundController(
        service,
        extraction_coordinator=TaskCoordinator(
            service, extractor=ExistingExtractorAdapter()
        ),
        download_coordinator=DownloadCoordinator(
            service,
            downloader=downloader_adapter,
        ),
        parent=window,
    )
    controller.changed.connect(window.refresh_tasks)
    controller.log.connect(lambda _task_id, _message: window._refresh_logs())
    window.background_controller = controller
    window.global_speed_limit_changed = downloader_adapter.set_global_speed_limit
    tray = QSystemTrayIcon(window.windowIcon(), window)
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
    window.notify_parent_completed = lambda _task_id, name: tray.showMessage(
        "下载完成", f"{name} 已全部完成", QSystemTrayIcon.MessageIcon.Information, 5000
    ) if service.load_app_settings().completion_notification else None
    guard.activate_requested.connect(window.show_from_tray)
    window.tray_icon = tray
    window.single_instance_guard = guard
    tray.show()
    window.show()
    controller.start()
    return app.exec()
