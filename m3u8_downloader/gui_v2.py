"""基于 PySide6 的新版任务管理界面。"""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
import os
from pathlib import Path
import sys
from urllib.parse import urlsplit

from PySide6.QtCore import (
    QByteArray, QItemSelectionModel, QObject, QRunnable, QSignalBlocker, QSize,
    Qt, QThreadPool, QTimer, QUrl, Signal,
)
from PySide6.QtGui import QDesktopServices, QFont, QIcon, QIntValidator, QTextCursor
from PySide6.QtWidgets import (
    QAbstractItemView,
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
    ItemStatus,
    Task,
    TaskService,
    TaskSettings,
)
from .secrets_v2 import protect_secret
from . import __version__
from .temp_files import TempFileManager, TempScan
from .update_checker import UpdateChecker, is_newer_version, should_check_for_updates
from .resource_stats import RuntimeStatisticsTracker


_STYLE = """
QWidget { background: #111418; color: #e8ebef; font-family: "Microsoft YaHei UI"; font-size: 13px; }
QLabel, QCheckBox, QRadioButton { background: transparent; }
QMessageBox { background: #15191e; }
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
QListWidget::item:hover { background: #1c2229; }
QListWidget::item:selected { background: #222a34; }
QTableWidget::item:hover { background: #202832; }
QTableWidget::item:selected { background: #29466b; color: #ffffff; }
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
#validation { color: #ff8585; padding: 3px 2px; }
"""

_LIGHT_STYLE = """
QWidget { background: #f5f7fa; color: #20242a; font-family: "Microsoft YaHei UI"; font-size: 13px; }
QLabel, QCheckBox, QRadioButton { background: transparent; }
QMessageBox { background: #f5f7fa; }
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
QListWidget::item:hover { background: #f0f4f9; }
QListWidget::item:selected { background: #e7eef8; }
QTableWidget::item:hover { background: #eef4fc; }
QTableWidget::item:selected { background: #d7e7fb; color: #173f73; }
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
#validation { color: #c62828; padding: 3px 2px; }
"""


class _BackgroundCallSignals(QObject):
    finished = Signal(object, str)


class _BackgroundCall(QRunnable):
    def __init__(self, operation) -> None:
        super().__init__()
        self.operation = operation
        self.signals = _BackgroundCallSignals()

    def run(self) -> None:
        try:
            result = self.operation()
            error = ""
        except Exception as exc:
            result = None
            error = str(exc)
        self.signals.finished.emit(result, error)


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
    if task.last_error.startswith("疑似重复内容:"):
        return "待处理"
    extraction = {
        ExtractionStatus.WAITING: "等待提取",
        ExtractionStatus.RUNNING: "提取中",
        ExtractionStatus.PAUSED: "提取已暂停",
        ExtractionStatus.FAILED: "提取失败",
    }.get(task.extraction_status)
    download = {
        DownloadStatus.NOT_READY: None,
        DownloadStatus.PENDING_SELECTION: "待选择",
        DownloadStatus.PENDING_REVIEW: "待处理",
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


def _set_button_enabled(button: QPushButton, enabled: bool, tooltip: str = "") -> None:
    button.setEnabled(enabled)
    button.setToolTip(tooltip)
    button.setAttribute(Qt.WidgetAttribute.WA_AlwaysShowToolTips, True)
    button.setCursor(
        Qt.CursorShape.PointingHandCursor if enabled else Qt.CursorShape.ArrowCursor
    )


def _install_button_cursors(root: QWidget) -> None:
    for button in root.findChildren(QPushButton):
        _set_button_enabled(button, button.isEnabled())
    for checkbox in root.findChildren(QCheckBox):
        checkbox.setCursor(
            Qt.CursorShape.PointingHandCursor
            if checkbox.isEnabled() else Qt.CursorShape.ArrowCursor
        )


class NavigationButton(QPushButton):
    """导航按钮右侧显示分类任务总数，按钮文字仍保留给无障碍接口。"""

    def __init__(self, text: str, parent: QWidget | None = None) -> None:
        super().__init__(text, parent)
        self._count_label = QLabel("0", self)
        self._count_label.setObjectName("navCount")
        self._count_label.setAlignment(
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
        )
        self._count_label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self._normal_count_color = "#e8ebef"
        self._checked_count_color = "#65a6ff"
        self.toggled.connect(self._update_count_color)
        self._update_count_color()

    def setCount(self, count: int) -> None:
        self._count_label.setText(str(max(0, int(count))))

    def countText(self) -> str:
        return self._count_label.text()

    def setCountColors(self, normal: str, checked: str) -> None:
        self._normal_count_color = normal
        self._checked_count_color = checked
        self._update_count_color()

    def _update_count_color(self, _checked: bool | None = None) -> None:
        color = (
            self._checked_count_color if self.isChecked()
            else self._normal_count_color
        )
        self._count_label.setStyleSheet(
            f"background: transparent; color: {color};"
        )

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._count_label.setGeometry(max(0, self.width() - 52), 0, 38, self.height())


class DeselectableListWidget(QListWidget):
    """空白点击或 Esc 取消主任务选择。"""

    def mousePressEvent(self, event) -> None:
        clicked = self.itemAt(event.position().toPoint())
        blank = clicked is None
        self._preserve_right_click_selection = False
        if (
            event.button() is Qt.MouseButton.RightButton
            and clicked is not None
            and clicked.isSelected()
        ):
            self._preserve_right_click_selection = True
            self.selectionModel().setCurrentIndex(
                self.indexFromItem(clicked), QItemSelectionModel.SelectionFlag.NoUpdate,
            )
            event.accept()
            return
        super().mousePressEvent(event)
        if blank:
            self.clearSelection()
            self.setCurrentItem(None)

    def mouseReleaseEvent(self, event) -> None:
        if (
            event.button() is Qt.MouseButton.RightButton
            and getattr(self, "_preserve_right_click_selection", False)
        ):
            self._preserve_right_click_selection = False
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def keyPressEvent(self, event) -> None:
        if event.key() == Qt.Key.Key_Escape:
            self.clearSelection()
            self.setCurrentItem(None)
            event.accept()
            return
        super().keyPressEvent(event)


class DeselectableTableWidget(QTableWidget):
    """只取消下载项行高亮，不改变复选框。"""

    def mousePressEvent(self, event) -> None:
        blank = self.itemAt(event.position().toPoint()) is None
        super().mousePressEvent(event)
        if blank:
            self.clearSelection()

    def keyPressEvent(self, event) -> None:
        if event.key() == Qt.Key.Key_Escape:
            self.clearSelection()
            event.accept()
            return
        super().keyPressEvent(event)


class MasterCheckBox(QCheckBox):
    """部分选中时点击会补齐全选，已全选时点击会全部取消。"""

    def nextCheckState(self) -> None:
        self.setCheckState(
            Qt.CheckState.Unchecked
            if self.checkState() is Qt.CheckState.Checked
            else Qt.CheckState.Checked
        )


class TaskCard(QWidget):
    def __init__(self, task: Task, items=None, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("taskCard")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 7, 12, 7)
        layout.setSpacing(4)
        title_row = QHBoxLayout()
        title_row.setContentsMargins(0, 0, 0, 0)
        title_row.setSpacing(10)
        name = QLabel(task.name)
        name.setObjectName("taskName")
        name.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.status_label = QLabel(_task_status(task))
        self.status_label.setObjectName("status")
        self.status_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        title_row.addWidget(name, 1)
        title_row.addWidget(self.status_label)
        layout.addLayout(title_row)
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
            total_size = sum(item.total_bytes or item.estimated_bytes or 0 for item in selected)
            details = []
            if total_size:
                details.append(f"{_format_bytes(done)} / {_format_bytes(total_size)}")
            speed = sum(
                item.speed_bps for item in selected
                if item.status.value == "downloading"
            )
            if speed:
                details.append(f"{_format_bytes(speed)}/秒")
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


def _ask_duplicate_source_action(parent: QWidget) -> str:
    box = QMessageBox(parent)
    box.setWindowTitle("链接已存在")
    box.setText("任务列表中已有相同链接。")
    locate = box.addButton("查看原任务", QMessageBox.ButtonRole.ActionRole)
    duplicate = box.addButton("仍创建副本", QMessageBox.ButtonRole.AcceptRole)
    box.addButton("取消", QMessageBox.ButtonRole.RejectRole)
    box.exec()
    if box.clickedButton() is locate:
        return "locate"
    if box.clickedButton() is duplicate:
        return "duplicate"
    return "cancel"


def _ask_content_duplicate_action(parent: QWidget, existing_name: str) -> str:
    box = QMessageBox(parent)
    box.setWindowTitle("发现疑似重复内容")
    box.setIcon(QMessageBox.Icon.Warning)
    box.setText(f"该任务与已有任务“{existing_name}”的标题和媒体结构高度相似。")
    box.setInformativeText("任务已转为待处理，请选择如何处理。")
    locate = box.addButton("定位已有任务", QMessageBox.ButtonRole.ActionRole)
    download = box.addButton("仍然下载", QMessageBox.ButtonRole.AcceptRole)
    cancel = box.addButton("取消", QMessageBox.ButtonRole.RejectRole)
    box.exec()
    if box.clickedButton() is locate:
        return "locate"
    if box.clickedButton() is download:
        return "download"
    if box.clickedButton() is cancel:
        return "delete"
    return "dismiss"


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
        self.extraction_mode.addItem("智能模式（深度优先，失败后普通）", "smart")
        self.extraction_mode.addItem("仅深度模式", "deep")
        self.extraction_mode.addItem("仅普通模式", "normal")
        default_mode = self._service.load_app_settings().extraction_mode
        self.extraction_mode.setCurrentIndex(
            max(0, self.extraction_mode.findData(default_mode))
        )
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

        self.validation_label = QLabel()
        self.validation_label.setObjectName("validation")
        self.validation_label.hide()
        root.addWidget(self.validation_label)

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
        self.validation_label.hide()
        if not self.address_edit.toPlainText().strip():
            self.validation_label.setText("请输入网页链接或 m3u8 链接")
            self.validation_label.show()
            self.address_edit.setFocus()
            return
        if not self.directory_edit.text().strip():
            self.validation_label.setText("请选择保存目录")
            self.validation_label.show()
            self.directory_edit.setFocus()
            return
        app_settings = self._service.load_app_settings()
        task_settings = TaskSettings(
            auto_download_threshold=app_settings.auto_download_threshold,
            extraction_mode=self.extraction_mode.currentData(),
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
            self.existing_task_requested.emit(error.existing_task_ids[0])
            action = _ask_duplicate_source_action(self)
            if action == "locate":
                self.reject()
                return
            if action != "duplicate":
                return
            tasks = self._service.create_tasks(request, allow_duplicates=True)
        if tasks:
            self.tasks_created.emit(tasks)
            self.accept()


class TaskSettingsDialog(QDialog):
    def __init__(self, task: Task, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("任务设置")
        self.resize(500, 500)
        self._original = task.settings
        self._task = task
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
        self.extraction_mode = QComboBox()
        self.extraction_mode.addItem("智能模式（深度优先）", "smart")
        self.extraction_mode.addItem("仅深度模式", "deep")
        self.extraction_mode.addItem("仅普通模式", "normal")
        self.extraction_mode.setCurrentIndex(max(
            0, self.extraction_mode.findData(task.settings.extraction_mode)
        ))
        self.speed = QSpinBox(); self.speed.setRange(0, 102400)
        self.speed.setValue(task.settings.speed_limit // 1024 // 1024)
        self.speed.setSuffix(" MB/秒（0 为不限速）")
        self.proxy = QLineEdit(task.settings.proxy)
        self.referer = QLineEdit(task.settings.referer)
        self.user_agent = QLineEdit(task.settings.user_agent)
        self.cookie = QLineEdit(); self.cookie.setEchoMode(QLineEdit.EchoMode.Password)
        self.cookie.setPlaceholderText("留空则保持原登录信息")
        for label, widget in [
            ("网页提取模式", self.extraction_mode),
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
            extraction_mode=self.extraction_mode.currentData(),
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
        self._log_session_start_id = self._service.latest_log_id()
        self._log_render_key = None
        self._log_rendered_ids: list[int] = []
        self._skip_delete_task_confirmation = False
        self._skip_permanent_delete_confirmation = False
        self._session_close_action: str | None = None
        self._pending_item_checks: dict[str, set[str]] = {}
        self._shown_content_duplicates: set[str] = set()
        self._updating_item_table = False
        self._detail_render_state = None
        self._temp_manager = TempFileManager()
        self._last_temp_scan = TempScan()
        self._update_checker = UpdateChecker()
        self._stats_tracker = RuntimeStatisticsTracker()
        self._background_calls: set[_BackgroundCall] = set()
        self.new_task_dialog: NewTaskDialog | None = None
        self.setWindowTitle("m3u8 下载器")
        icon = QIcon(str(_asset_path("m3u8-downloader.ico")))
        self.setWindowIcon(icon)
        QApplication.instance().setWindowIcon(icon)
        self.setMinimumSize(980, 640)
        self._window_size_save_timer = QTimer(self)
        self._window_size_save_timer.setSingleShot(True)
        self._window_size_save_timer.timeout.connect(self._persist_window_size)
        self._pending_window_size: tuple[int, int] | None = None
        app_settings = self._service.load_app_settings()
        if app_settings.window_width and app_settings.window_height:
            self.resize(app_settings.window_width, app_settings.window_height)
        else:
            screen = QApplication.primaryScreen()
            available = screen.availableGeometry() if screen is not None else None
            width = min(1440, int(available.width() * 0.9)) if available else 1440
            height = min(900, int(available.height() * 0.9)) if available else 900
            self.resize(max(980, width), max(640, height))
        QApplication.instance().setFont(QFont("Microsoft YaHei UI", 10))
        self.setStyleSheet(_STYLE)
        self._build_ui()
        self._item_header_save_timer = QTimer(self)
        self._item_header_save_timer.setSingleShot(True)
        self._item_header_save_timer.timeout.connect(self._persist_item_table_header_state)
        self._pending_item_table_header_state: str | None = None
        if app_settings.item_table_header_state:
            self.item_table.horizontalHeader().restoreState(QByteArray.fromBase64(
                app_settings.item_table_header_state.encode("ascii")
            ))
        self.item_table.horizontalHeader().sectionResized.connect(
            self._schedule_item_table_header_save
        )
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
        self.downloading_button = self._nav_button("下载中", True, counted=True)
        self.completed_button = self._nav_button("已完成", counted=True)
        self.settings_button = self._nav_button("⚙  设置")
        self.settings_button.setText("设置")
        self.downloading_button.clicked.connect(lambda: self._switch_view(0))
        self.completed_button.clicked.connect(lambda: self._switch_view(1))
        self.settings_button.clicked.connect(lambda: self._switch_view(2))
        side.addWidget(brand)
        side.addWidget(self.downloading_button)
        side.addWidget(self.completed_button)
        side.addStretch()
        side.addWidget(self.settings_button)
        outer.addWidget(sidebar)

        vertical = QSplitter(Qt.Orientation.Vertical)
        self.main_vertical_splitter = vertical
        horizontal = QSplitter(Qt.Orientation.Horizontal)
        self.main_horizontal_splitter = horizontal
        horizontal.addWidget(self._build_task_area())
        horizontal.addWidget(self._build_detail_area())
        app_settings = self._service.load_app_settings()
        if app_settings.task_panel_width and app_settings.detail_panel_width:
            horizontal.setSizes([
                app_settings.task_panel_width,
                app_settings.detail_panel_width,
            ])
        else:
            horizontal.setSizes([1000, 1000])
        horizontal.setStretchFactor(0, 1)
        horizontal.setStretchFactor(1, 1)
        horizontal.setCollapsible(0, False)
        horizontal.setCollapsible(1, False)
        self._splitter_save_timer = QTimer(self)
        self._splitter_save_timer.setSingleShot(True)
        self._splitter_save_timer.timeout.connect(self._persist_horizontal_splitter_sizes)
        self._pending_horizontal_splitter_sizes: tuple[int, int] | None = None
        horizontal.splitterMoved.connect(self._schedule_horizontal_splitter_save)
        vertical.addWidget(horizontal)
        vertical.addWidget(self._build_log_area())
        vertical.setSizes([530, 230])
        vertical.setStretchFactor(0, 1)
        vertical.setStretchFactor(1, 0)
        vertical.setCollapsible(1, False)
        outer.addWidget(vertical, 1)

    @staticmethod
    def _nav_button(
        text: str, checked: bool = False, *, counted: bool = False,
    ) -> QPushButton:
        button = NavigationButton(text) if counted else QPushButton(text)
        button.setCheckable(True)
        button.setChecked(checked)
        return button

    def _build_feedback_area(self) -> None:
        self.statistics_label = QLabel(
            "速度 0 B/秒 · 本次 0 B · 下载 0 · 提取 0 · CPU 0% · 内存 0 B"
        )
        self.statistics_label.setObjectName("muted")
        self.statusBar().addWidget(self.statistics_label, 1)
        self.feedback_label = QLabel()
        self.feedback_label.setObjectName("feedback")
        self.feedback_label.hide()
        self.statusBar().addPermanentWidget(self.feedback_label)
        self._feedback_timer = QTimer(self)
        self._feedback_timer.setSingleShot(True)
        self._feedback_timer.timeout.connect(self.feedback_label.hide)

    def _schedule_horizontal_splitter_save(self, _position: int, _index: int) -> None:
        task_width, detail_width = self.main_horizontal_splitter.sizes()
        if task_width <= 0 or detail_width <= 0:
            return
        self._pending_horizontal_splitter_sizes = (task_width, detail_width)
        self._splitter_save_timer.start(250)

    def _persist_horizontal_splitter_sizes(self) -> None:
        if self._pending_horizontal_splitter_sizes is None:
            return
        task_width, detail_width = self._pending_horizontal_splitter_sizes
        self._pending_horizontal_splitter_sizes = None
        settings = self._service.load_app_settings()
        if (
            settings.task_panel_width == task_width
            and settings.detail_panel_width == detail_width
        ):
            return
        self._service.save_app_settings(replace(
            settings,
            task_panel_width=task_width,
            detail_panel_width=detail_width,
        ))

    def _schedule_item_table_header_save(
        self, _logical_index: int, _old_size: int, _new_size: int,
    ) -> None:
        state = self.item_table.horizontalHeader().saveState().toBase64()
        self._pending_item_table_header_state = bytes(state).decode("ascii")
        self._item_header_save_timer.start(250)

    def _persist_item_table_header_state(self) -> None:
        state = self._pending_item_table_header_state
        if state is None:
            return
        self._pending_item_table_header_state = None
        settings = self._service.load_app_settings()
        if settings.item_table_header_state == state:
            return
        self._service.save_app_settings(replace(
            settings, item_table_header_state=state,
        ))

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
        self.search_edit.setClearButtonEnabled(True)
        self.search_edit.textChanged.connect(self.refresh_tasks)
        self.pause_task_button = QPushButton("暂停")
        _set_button_enabled(self.pause_task_button, False, "请先选择一个主任务")
        self.pause_task_button.clicked.connect(self._pause_current_task)
        self.resume_task_button = QPushButton("继续")
        _set_button_enabled(self.resume_task_button, False, "请先选择一个主任务")
        self.resume_task_button.clicked.connect(self._resume_current_task)
        self.stop_extraction_button = QPushButton("停止提取")
        _set_button_enabled(self.stop_extraction_button, False, "请先选择一个主任务")
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
        quick_layout.setContentsMargins(0, 8, 0, 8)
        quick_layout.setSpacing(8)
        self.quick_address_edit = QLineEdit()
        self.quick_address_edit.setPlaceholderText("粘贴网页链接或 m3u8 链接，按回车快速创建任务")
        self.quick_address_edit.setClearButtonEnabled(True)
        self.quick_address_edit.returnPressed.connect(self._quick_start)
        self.quick_paste_button = QPushButton("粘贴")
        self.quick_paste_button.clicked.connect(self._paste_quick_address)
        self.quick_start_button = QPushButton("快速开始")
        self.quick_start_button.setObjectName("newTask")
        self.quick_start_button.clicked.connect(self._quick_start)
        quick_layout.addWidget(self.quick_address_edit, 1)
        quick_layout.addWidget(self.quick_paste_button)
        quick_layout.addWidget(self.quick_start_button)
        layout.addWidget(self.quick_download_panel)
        filter_row = QHBoxLayout()
        self.status_filter_combo = QComboBox()
        for text, value in [
            ("全部状态", "all"), ("提取中", "extracting"), ("下载中", "downloading"),
            ("待选择", "selection"), ("待处理", "review"),
            ("已暂停", "paused"), ("失败", "failed"),
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
        self.task_list = DeselectableListWidget()
        self.task_list.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.task_list.itemSelectionChanged.connect(
            lambda: self._sync_task_detail_from_selection(self.task_list)
        )
        self.task_list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.task_list.customContextMenuRequested.connect(
            lambda position: self._show_task_menu(self.task_list, position)
        )
        self.completed_list = DeselectableListWidget()
        self.completed_list.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
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
        self.extraction_mode_combo = QComboBox()
        self.extraction_mode_combo.addItem(
            "智能模式（深度优先，失败后普通）", "smart",
        )
        self.extraction_mode_combo.addItem("仅深度模式", "deep")
        self.extraction_mode_combo.addItem("仅普通模式", "normal")
        self.threshold_spin = QSpinBox()
        self.threshold_spin.setRange(1, 20)
        self.notification_check = QCheckBox("父任务全部完成时显示 Windows 通知")
        self.close_rule_enabled_check = QCheckBox("启用关闭规则")
        self.close_rule_combo = QComboBox()
        self.close_rule_combo.addItem("最小化到托盘", "tray")
        self.close_rule_combo.addItem("直接关闭", "exit")
        self.close_rule_combo.addItem(
            "有任务时最小化到托盘，无任务时直接关闭", "smart",
        )
        close_rule_row = QWidget()
        close_rule_layout = QHBoxLayout(close_rule_row)
        close_rule_layout.setContentsMargins(0, 0, 0, 0)
        close_rule_layout.setSpacing(10)
        close_rule_layout.addWidget(self.close_rule_enabled_check)
        close_rule_layout.addWidget(self.close_rule_combo, 1)
        self.request_retries_spin = QSpinBox()
        self.request_retries_spin.setRange(0, 10)
        self.task_retries_spin = QSpinBox()
        self.task_retries_spin.setRange(0, 5)
        self.retry_delay_spin = QSpinBox()
        self.retry_delay_spin.setRange(1, 3600)
        self.retry_delay_spin.setSuffix(" 秒")
        behavior_form.addRow("网页提取模式", self.extraction_mode_combo)
        behavior_form.addRow("自动下载候选阈值", self.threshold_spin)
        behavior_form.addRow("单次网络请求重试", self.request_retries_spin)
        behavior_form.addRow("任务级自动重试", self.task_retries_spin)
        behavior_form.addRow("任务重试等待", self.retry_delay_spin)
        behavior_form.addRow("", self.notification_check)
        behavior_form.addRow("关闭窗口", close_rule_row)

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

        site_profiles = QGroupBox("按网站保存提取配置")
        site_profiles_layout = QVBoxLayout(site_profiles)
        self.site_profile_table = QTableWidget(0, 4)
        self.site_profile_table.setHorizontalHeaderLabels(["网站", "模式", "超时", "代理"])
        self.site_profile_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.site_profile_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.site_profile_table.horizontalHeader().setStretchLastSection(True)
        self.site_profile_table.setMaximumHeight(170)
        delete_profile = QPushButton("删除选中配置")
        delete_profile.clicked.connect(self._delete_selected_site_profile)
        site_profiles_layout.addWidget(QLabel("网页提取成功后自动学习，同站新任务自动复用。"))
        site_profiles_layout.addWidget(self.site_profile_table)
        site_profiles_layout.addWidget(delete_profile, 0, Qt.AlignmentFlag.AlignRight)

        compatibility = QGroupBox("站点兼容性记录")
        compatibility_layout = QVBoxLayout(compatibility)
        self.site_compatibility_table = QTableWidget(0, 6)
        self.site_compatibility_table.setHorizontalHeaderLabels(
            ["网站", "成功率", "次数", "最近模式", "候选", "平均耗时"]
        )
        self.site_compatibility_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.site_compatibility_table.horizontalHeader().setStretchLastSection(True)
        self.site_compatibility_table.setMaximumHeight(190)
        clear_compatibility = QPushButton("清空兼容性记录")
        clear_compatibility.clicked.connect(self._clear_site_compatibility)
        compatibility_layout.addWidget(self.site_compatibility_table)
        compatibility_layout.addWidget(clear_compatibility, 0, Qt.AlignmentFlag.AlignRight)

        temporary = QGroupBox("临时文件管理")
        temporary_layout = QVBoxLayout(temporary)
        self.temp_status_label = QLabel("尚未扫描")
        self.temp_status_label.setWordWrap(True)
        temp_actions = QHBoxLayout()
        self.scan_temp_button = QPushButton("扫描")
        self.clean_temp_button = QPushButton("安全清理")
        self.clean_temp_button.setEnabled(False)
        self.scan_temp_button.clicked.connect(self._scan_temp_files)
        self.clean_temp_button.clicked.connect(self._clean_temp_files)
        temp_actions.addWidget(self.scan_temp_button)
        temp_actions.addWidget(self.clean_temp_button)
        temp_actions.addStretch()
        temporary_layout.addWidget(self.temp_status_label)
        temporary_layout.addLayout(temp_actions)

        update = QGroupBox("软件更新")
        update_layout = QVBoxLayout(update)
        self.update_status_label = QLabel(f"当前版本：{__version__}")
        self.auto_update_check = QCheckBox("启动时自动检查新版本")
        self.check_update_button = QPushButton("检查更新")
        self.check_update_button.clicked.connect(
            lambda: self._start_update_check(manual=True)
        )
        update_actions = QHBoxLayout()
        update_actions.addWidget(self.check_update_button)
        update_actions.addStretch()
        update_layout.addWidget(self.update_status_label)
        update_layout.addWidget(self.auto_update_check)
        update_layout.addLayout(update_actions)

        self.save_settings_button = QPushButton("保存设置")
        self.save_settings_button.setObjectName("newTask")
        self.save_settings_button.clicked.connect(self._save_settings)
        root.addWidget(concurrency)
        root.addWidget(behavior)
        root.addWidget(appearance)
        root.addWidget(site_profiles)
        root.addWidget(compatibility)
        root.addWidget(temporary)
        root.addWidget(update)
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
        mode_index = self.extraction_mode_combo.findData(settings.extraction_mode)
        self.extraction_mode_combo.setCurrentIndex(max(0, mode_index))
        self.notification_check.setChecked(settings.completion_notification)
        self.close_rule_enabled_check.setChecked(settings.close_rule_enabled)
        close_rule_index = self.close_rule_combo.findData(settings.close_rule)
        self.close_rule_combo.setCurrentIndex(max(0, close_rule_index))
        self.log_days_spin.setValue(settings.log_retention_days)
        index = self.theme_combo.findData(settings.theme)
        self.theme_combo.setCurrentIndex(max(0, index))
        self.auto_update_check.setChecked(settings.check_updates_on_startup)
        self._refresh_site_tables()

    def _refresh_site_tables(self) -> None:
        if not hasattr(self, "site_profile_table"):
            return
        modes = {"smart": "智能", "deep": "深度", "normal": "普通"}
        profiles = self._service.list_site_profiles()
        self.site_profile_table.setRowCount(len(profiles))
        for row, profile in enumerate(profiles):
            values = [
                profile.hostname,
                (
                    f"智能（优先{modes.get(profile.preferred_extraction_mode, '深度')}）"
                    if profile.extraction_mode == "smart" else
                    modes.get(profile.extraction_mode, profile.extraction_mode)
                ),
                f"{profile.timeout_seconds} 秒", profile.proxy or "跟随系统",
            ]
            for column, value in enumerate(values):
                self.site_profile_table.setItem(row, column, QTableWidgetItem(value))
        records = self._service.list_site_compatibility()
        self.site_compatibility_table.setRowCount(len(records))
        for row, record in enumerate(records):
            values = [
                record.hostname, f"{record.success_rate * 100:.0f}%",
                str(record.attempts), modes.get(record.last_mode, record.last_mode),
                str(record.last_candidate_count), f"{record.average_elapsed_seconds:.1f} 秒",
            ]
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                if column == 0:
                    item.setToolTip(record.last_error or record.last_result)
                self.site_compatibility_table.setItem(row, column, item)

    def _delete_selected_site_profile(self) -> None:
        row = self.site_profile_table.currentRow()
        if row < 0:
            self._show_feedback("请先选择一个网站配置")
            return
        hostname = self.site_profile_table.item(row, 0).text()
        self._service.delete_site_profile(hostname)
        self._refresh_site_tables()
        self._show_feedback(f"已删除 {hostname} 的提取配置")

    def _clear_site_compatibility(self) -> None:
        self._service.clear_site_compatibility()
        self._refresh_site_tables()
        self._show_feedback("兼容性记录已清空")

    def _save_settings(self) -> None:
        settings = replace(
            self._service.load_app_settings(),
            download_task_limit=self.download_limit_spin.value(),
            extraction_task_limit=self.extraction_limit_spin.value(),
            auto_download_threshold=self.threshold_spin.value(),
            extraction_mode=self.extraction_mode_combo.currentData(),
            segment_threads=self.segment_threads_spin.value(),
            global_speed_limit=self.global_speed_spin.value() * 1024 * 1024,
            request_retries=self.request_retries_spin.value(),
            task_retries=self.task_retries_spin.value(),
            retry_delay_seconds=self.retry_delay_spin.value(),
            completion_notification=self.notification_check.isChecked(),
            close_rule_enabled=self.close_rule_enabled_check.isChecked(),
            close_rule=self.close_rule_combo.currentData(),
            log_retention_days=self.log_days_spin.value(),
            theme=self.theme_combo.currentData(),
            check_updates_on_startup=self.auto_update_check.isChecked(),
        )
        self._service.save_app_settings(settings)
        speed_callback = getattr(self, "global_speed_limit_changed", None)
        if speed_callback is not None:
            speed_callback(settings.global_speed_limit)
        self._apply_theme(settings.theme)
        self.log_view.appendPlainText("设置已保存")
        self._show_feedback("设置已保存")

    def _run_in_background(self, operation, callback) -> None:
        worker = _BackgroundCall(operation)
        self._background_calls.add(worker)

        def finished(result, error, worker=worker):
            self._background_calls.discard(worker)
            callback(result, error)

        worker.signals.finished.connect(finished)
        QThreadPool.globalInstance().start(worker)

    def _temp_context(self):
        tasks = self._service.list_tasks()
        return tasks, {task.id: self._service.list_items(task.id) for task in tasks}

    def _scan_temp_files(self) -> None:
        self.scan_temp_button.setEnabled(False)
        self.clean_temp_button.setEnabled(False)
        self.temp_status_label.setText("正在扫描……")
        tasks, items = self._temp_context()
        self._run_in_background(
            lambda: self._temp_manager.scan(tasks, items),
            self._temp_scan_finished,
        )

    def _temp_scan_finished(self, result, error: str) -> None:
        self.scan_temp_button.setEnabled(True)
        if error:
            self.temp_status_label.setText(f"扫描失败：{error}")
            self._show_feedback("临时文件扫描失败")
            return
        self._last_temp_scan = result
        self.temp_status_label.setText(
            f"共 {_format_bytes(result.total_bytes)}（{result.file_count} 个文件），"
            f"可安全清理 {_format_bytes(result.cleanable_bytes)}；"
            f"续传保留 {_format_bytes(result.protected_bytes)}"
        )
        self.clean_temp_button.setEnabled(bool(result.cleanable_jobs))
        self._show_feedback("临时文件扫描完成")

    def _clean_temp_files(self) -> None:
        scan = self._last_temp_scan
        if not scan.cleanable_jobs:
            self._show_feedback("没有可安全清理的临时文件")
            return
        box = QMessageBox(self)
        box.setWindowTitle("清理临时文件")
        box.setText(f"清理 {_format_bytes(scan.cleanable_bytes)} 临时文件？")
        box.setInformativeText("未完成任务的断点续传文件会保留。")
        box.setStandardButtons(
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel
        )
        box.button(QMessageBox.StandardButton.Yes).setText("清理")
        box.button(QMessageBox.StandardButton.Cancel).setText("取消")
        box.setDefaultButton(QMessageBox.StandardButton.Cancel)
        if box.exec() != QMessageBox.StandardButton.Yes:
            return
        self.scan_temp_button.setEnabled(False)
        self.clean_temp_button.setEnabled(False)
        self.temp_status_label.setText("正在清理……")
        tasks, items = self._temp_context()

        def clean_current_snapshot():
            current = self._temp_manager.scan(tasks, items)
            return self._temp_manager.cleanup(current)

        self._run_in_background(clean_current_snapshot, self._temp_cleanup_finished)

    def _temp_cleanup_finished(self, result, error: str) -> None:
        if error:
            self.scan_temp_button.setEnabled(True)
            self.temp_status_label.setText(f"清理失败：{error}")
            self._show_feedback("临时文件清理失败")
            return
        self._show_feedback(
            f"已清理 {_format_bytes(result.removed_bytes)}，"
            f"{result.failed_jobs} 项未能删除"
            if result.failed_jobs else
            f"已清理 {_format_bytes(result.removed_bytes)}"
        )
        self._scan_temp_files()

    def maybe_check_for_updates(self) -> None:
        settings = self._service.load_app_settings()
        if (
            settings.check_updates_on_startup
            and should_check_for_updates(settings.last_update_check_at)
        ):
            self._start_update_check(manual=False)

    def _start_update_check(self, *, manual: bool) -> None:
        if not self.check_update_button.isEnabled():
            return
        self.check_update_button.setEnabled(False)
        self.update_status_label.setText("正在检查新版本……")
        self._run_in_background(
            self._update_checker.fetch_latest,
            lambda result, error: self._update_check_finished(result, error, manual),
        )

    def _update_check_finished(self, release, error: str, manual: bool) -> None:
        self.check_update_button.setEnabled(True)
        if error:
            self.update_status_label.setText(f"当前版本：{__version__}（检查失败）")
            if manual:
                QMessageBox.warning(self, "检查更新", error)
            else:
                self.log_view.appendPlainText(f"[警告] [更新] {error}")
            return

        settings = self._service.load_app_settings()
        self._service.save_app_settings(replace(
            settings,
            last_update_check_at=datetime.now(timezone.utc).isoformat(),
        ))
        if is_newer_version(release.version, __version__):
            self.update_status_label.setText(
                f"发现新版本：{release.version}（当前 {__version__}）"
            )
            box = QMessageBox(self)
            box.setWindowTitle("发现新版本")
            box.setText(f"发现 m3u8 下载器 {release.version}")
            box.setInformativeText(f"当前版本：{__version__}")
            open_release = box.addButton("打开下载页面", QMessageBox.ButtonRole.AcceptRole)
            box.addButton("稍后", QMessageBox.ButtonRole.RejectRole)
            box.exec()
            if box.clickedButton() is open_release:
                QDesktopServices.openUrl(QUrl(release.url))
        else:
            self.update_status_label.setText(f"当前版本：{__version__}（已是最新）")
            if manual:
                QMessageBox.information(self, "检查更新", "当前已经是最新版本。")

    def _apply_theme(self, theme: str) -> None:
        if theme == "system":
            theme = (
                "dark" if QApplication.styleHints().colorScheme() is Qt.ColorScheme.Dark
                else "light"
            )
        self.setStyleSheet(_theme_style(theme))
        normal, checked = (
            ("#e8ebef", "#65a6ff") if theme == "dark"
            else ("#20242a", "#216bd6")
        )
        for button in (self.downloading_button, self.completed_button):
            button.setCountColors(normal, checked)

    def _build_detail_area(self) -> QWidget:
        panel = QFrame()
        panel.setObjectName("detailPanel")
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(16, 16, 16, 12)
        self.detail_title = QLabel("任务详情")
        self.detail_title.setStyleSheet("font-size: 18px; font-weight: 600;")
        self.detail_title.setMinimumWidth(0)
        self.detail_title.setSizePolicy(
            QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred,
        )
        self.detail_tabs = QTabWidget()
        self.item_table = DeselectableTableWidget(0, 4)
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
        self.item_table.itemDoubleClicked.connect(self._open_item_from_table)
        self.item_table.itemChanged.connect(self._remember_item_check)
        self.info_view = QPlainTextEdit()
        self.info_view.setReadOnly(True)
        self.detail_tabs.addTab(self.item_table, "下载项")
        self.detail_tabs.addTab(self.info_view, "详细信息")
        self.download_selected_button = QPushButton("下载选中项")
        self.download_selected_button.setObjectName("newTask")
        _set_button_enabled(self.download_selected_button, False, "请先选择一个主任务")
        self.download_selected_button.clicked.connect(self._download_selected_items)
        self.repair_task_button = QPushButton("校验并修复")
        _set_button_enabled(self.repair_task_button, False, "请先选择已完成任务")
        self.repair_task_button.clicked.connect(self._validate_and_repair_current_task)
        self.select_all_checkbox = MasterCheckBox("全选")
        self.select_all_checkbox.setTristate(True)
        self.select_all_checkbox.setEnabled(False)
        self.select_all_checkbox.setToolTip("请先选择一个主任务")
        self.select_all_checkbox.stateChanged.connect(self._toggle_all_candidate_checks)
        action_row = QHBoxLayout()
        action_row.addWidget(self.select_all_checkbox)
        action_row.addStretch()
        action_row.addWidget(self.repair_task_button)
        action_row.addWidget(self.download_selected_button)
        layout.addWidget(self.detail_title)
        layout.addWidget(self.detail_tabs, 1)
        layout.addLayout(action_row)
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
        self.clear_logs_button = QPushButton("清空日志")
        self.clear_logs_button.clicked.connect(self._clear_current_session_logs)
        header_layout.addWidget(self.clear_logs_button)
        self.log_view = QPlainTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setPlaceholderText("任务状态、提取结果和下载过程会显示在这里")
        layout.addWidget(header)
        layout.addWidget(self.log_view, 1)
        return panel

    def _switch_view(self, index: int) -> None:
        if self.pages.currentIndex() != index:
            for task_list in (self.task_list, self.completed_list):
                task_list.clearSelection()
                task_list.setCurrentItem(None)
            self.item_table.clearSelection()
            self._clear_task_detail()
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
            self._refresh_site_tables()
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

    def _paste_quick_address(self) -> None:
        value = QApplication.clipboard().text().strip()
        self.quick_address_edit.setText(value)
        self.quick_address_edit.setFocus()
        self._show_feedback("链接已粘贴" if value else "剪贴板中没有文字")

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
            extraction_mode=app_settings.extraction_mode,
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
        except DuplicateSourceError as error:
            self._locate_existing_task(error.existing_task_ids[0])
            action = _ask_duplicate_source_action(self)
            if action != "duplicate":
                return
            tasks = self._service.create_tasks(CreateTaskRequest(
                addresses=address,
                save_directory=self._last_save_directory(),
                settings=settings,
            ), allow_duplicates=True)
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
        self.search_edit.clear()
        self.status_filter_combo.setCurrentIndex(0)
        self._switch_view(1 if task.download_status is DownloadStatus.COMPLETED else 0)
        self._show_task_by_id(task_id)
        self._show_feedback("已定位到最近创建的相同链接任务")

    def _tasks_created(self, tasks: list[Task]) -> None:
        for task in tasks:
            self._service.add_log(task.id, "信息", "任务", "任务已创建")
        self.refresh_tasks()
        self._refresh_logs()
        if tasks:
            self._switch_view(0)
            self._show_task_by_id(tasks[-1].id)
            self._show_feedback(f"已创建 {len(tasks)} 个任务")

    def refresh_tasks(self) -> None:
        selected_ids = {
            task_list: {
                item.data(Qt.ItemDataRole.UserRole).id
                for item in task_list.selectedItems()
            }
            for task_list in (self.task_list, self.completed_list)
        }
        current_ids = {
            task_list: (
                task_list.currentItem().data(Qt.ItemDataRole.UserRole).id
                if task_list.currentItem() is not None else ""
            )
            for task_list in (self.task_list, self.completed_list)
        }
        scroll_positions = {
            task_list: task_list.verticalScrollBar().value()
            for task_list in (self.task_list, self.completed_list)
        }
        query = self.search_edit.text().strip().lower() if hasattr(self, "search_edit") else ""
        tasks = self._service.list_tasks()
        task_items_by_id = {
            task.id: self._service.list_items(task.id) for task in tasks
        }
        all_items = [item for items in task_items_by_id.values() for item in items]
        statistics = self._stats_tracker.update(
            total_downloaded_bytes=sum(item.downloaded_bytes for item in all_items),
            speed_bps=sum(
                item.speed_bps for item in all_items
                if item.status is ItemStatus.DOWNLOADING
            ),
            active_downloads=sum(
                task.download_status is DownloadStatus.RUNNING for task in tasks
            ),
            active_extractions=sum(
                task.extraction_status is ExtractionStatus.RUNNING for task in tasks
            ),
        )
        self.statistics_label.setText(
            f"速度 {_format_bytes(statistics.speed_bps)}/秒 · "
            f"本次 {_format_bytes(statistics.session_downloaded_bytes)} · "
            f"下载 {statistics.active_downloads} · 提取 {statistics.active_extractions} · "
            f"CPU {statistics.cpu_percent:.1f}% · 内存 {_format_bytes(statistics.memory_bytes)}"
        )
        active_tasks = [task for task in tasks if task.download_status is not DownloadStatus.COMPLETED]
        active_tasks.sort(key=lambda task: (task.queue_position, task.created_at, task.id))
        completed_tasks = [task for task in tasks if task.download_status is DownloadStatus.COMPLETED]
        self.downloading_button.setCount(len(active_tasks))
        self.completed_button.setCount(len(completed_tasks))
        completed_sort = self.completed_sort_combo.currentData()
        if completed_sort == "name":
            completed_tasks.sort(key=lambda task: task.name.casefold())
        elif completed_sort == "size":
            completed_tasks.sort(
                key=lambda task: sum(
                    item.total_bytes or item.estimated_bytes or 0
                    for item in task_items_by_id[task.id]
                ), reverse=True,
            )
        else:
            completed_tasks.sort(
                key=lambda task: task.completed_at or task.updated_at, reverse=True
            )
        displayed = {self.task_list: [], self.completed_list: []}
        for task in active_tasks + completed_tasks:
            task_items = task_items_by_id[task.id]
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
            displayed[target].append((task, task_items))

        for task_list in (self.task_list, self.completed_list):
            self._reconcile_task_list(
                task_list,
                displayed[task_list],
                selected_ids[task_list],
                current_ids[task_list],
            )

        if self.pages.currentIndex() == 2:
            self._clear_task_detail()
        else:
            active_list = self.completed_list if self.pages.currentIndex() == 1 else self.task_list
            self._sync_task_detail_from_selection(active_list)
        for task_list, position in scroll_positions.items():
            task_list.verticalScrollBar().setValue(position)
        if self.pages.currentIndex() == 0:
            self.page_title.setText(
                self._global_download_status(active_tasks, task_items_by_id)
            )
        self._schedule_content_duplicate_reviews(active_tasks)

    def _schedule_content_duplicate_reviews(self, tasks: list[Task]) -> None:
        for task in tasks:
            if (
                task.last_error.startswith("疑似重复内容:")
                and task.id not in self._shown_content_duplicates
            ):
                self._shown_content_duplicates.add(task.id)
                QTimer.singleShot(
                    0, lambda task_id=task.id: self._review_content_duplicate(task_id)
                )

    def _review_content_duplicate(self, task_id: str) -> str:
        try:
            task = self._service.get_task(task_id)
        except KeyError:
            return "cancel"
        if not task.last_error.startswith("疑似重复内容:"):
            return "download"
        existing_task_id = task.last_error.split(":", 1)[1]
        try:
            existing = self._service.get_task(existing_task_id)
        except KeyError:
            self._service.allow_content_duplicate(task_id)
            self.refresh_tasks()
            return "download"
        action = _ask_content_duplicate_action(self, existing.name)
        if action == "locate":
            self._locate_existing_task(existing.id)
        elif action == "download":
            self._service.allow_content_duplicate(task_id)
            self._service.add_log(task_id, "信息", "任务", "用户确认仍然下载疑似重复内容")
            self.refresh_tasks()
            self._show_task_by_id(task_id)
            self._show_feedback("任务已恢复下载")
        elif action == "delete":
            controller = getattr(self, "background_controller", None)
            if controller is not None:
                controller.delete_task(task_id, delete_outputs=False)
            else:
                self._service.delete_task(task_id, delete_outputs=False)
            self.refresh_tasks()
            self._refresh_logs()
            self._show_feedback("已删除新的疑似重复任务")
        else:
            self._show_feedback("任务保持待处理，稍后可在任务详情中继续")
        return action

    @staticmethod
    def _task_card_height(task: Task, task_items: list) -> int:
        return 88

    def _reconcile_task_list(
        self,
        task_list: QListWidget,
        desired: list[tuple[Task, list]],
        selected_ids: set[str],
        current_id: str,
    ) -> None:
        desired_ids = [task.id for task, _items in desired]
        existing_ids = [
            task_list.item(row).data(Qt.ItemDataRole.UserRole).id
            for row in range(task_list.count())
        ]
        blocker = QSignalBlocker(task_list)
        if existing_ids != desired_ids:
            task_list.clear()
            for task, task_items in desired:
                item = QListWidgetItem()
                item.setData(Qt.ItemDataRole.UserRole, task)
                item.setSizeHint(QSize(0, self._task_card_height(task, task_items)))
                item._render_state = (task, tuple(task_items))
                task_list.addItem(item)
                task_list.setItemWidget(item, TaskCard(task, task_items))
        else:
            for row, (task, task_items) in enumerate(desired):
                item = task_list.item(row)
                state = (task, tuple(task_items))
                item.setData(Qt.ItemDataRole.UserRole, task)
                item.setSizeHint(QSize(0, self._task_card_height(task, task_items)))
                if getattr(item, "_render_state", None) != state:
                    item._render_state = state
                    task_list.setItemWidget(item, TaskCard(task, task_items))
        for row in range(task_list.count()):
            item = task_list.item(row)
            task_id = item.data(Qt.ItemDataRole.UserRole).id
            item.setSelected(task_id in selected_ids)
            if task_id == current_id:
                task_list.selectionModel().setCurrentIndex(
                    task_list.indexFromItem(item),
                    QItemSelectionModel.SelectionFlag.NoUpdate,
                )
        del blocker

    @staticmethod
    def _global_download_status(tasks: list[Task], task_items_by_id: dict[str, list]) -> str:
        if not tasks:
            return "无任务"
        items = [item for task in tasks for item in task_items_by_id[task.id]]
        if (
            any(task.download_status is DownloadStatus.RUNNING for task in tasks)
            or any(item.status is ItemStatus.DOWNLOADING for item in items)
        ):
            speed = sum(
                item.speed_bps
                for item in items
                if item.status is ItemStatus.DOWNLOADING
            )
            return f"{_format_bytes(speed)}/秒"
        if any(
            task.extraction_status in {ExtractionStatus.WAITING, ExtractionStatus.RUNNING}
            for task in tasks
        ):
            return "提取中"
        waiting_statuses = {
            DownloadStatus.PENDING_SELECTION,
            DownloadStatus.PENDING_REVIEW,
            DownloadStatus.WAITING,
            DownloadStatus.MERGING,
            DownloadStatus.RETRY_WAIT,
        }
        if any(task.download_status in waiting_statuses for task in tasks):
            return "等待中"
        if all(
            task.download_status is DownloadStatus.PAUSED
            or task.extraction_status is ExtractionStatus.PAUSED
            for task in tasks
        ):
            return "暂停中"
        if any(
            task.download_status is DownloadStatus.PARTIAL_FAILURE
            or task.extraction_status is ExtractionStatus.FAILED
            for task in tasks
        ):
            return "有失败任务"
        return "等待中"

    def _sync_task_detail_from_selection(self, task_list: QListWidget) -> None:
        active_list = self.completed_list if self.pages.currentIndex() == 1 else self.task_list
        if self.pages.currentIndex() == 2 or task_list is not active_list:
            return
        selected = task_list.selectedItems()
        if selected:
            current = task_list.currentItem()
            self._show_task_detail(
                current if current is not None and current.isSelected() else selected[-1],
                None,
            )
            self._refresh_task_toolbar([
                item.data(Qt.ItemDataRole.UserRole) for item in selected
            ])
        else:
            self._clear_task_detail()

    def _selected_active_tasks(self) -> list[Task]:
        if self.pages.currentIndex() != 0:
            return []
        return [
            item.data(Qt.ItemDataRole.UserRole) for item in self.task_list.selectedItems()
        ]

    def _refresh_task_toolbar(self, tasks: list[Task]) -> None:
        can_pause = any(
            task.extraction_status in {ExtractionStatus.WAITING, ExtractionStatus.RUNNING}
            or task.download_status in {
                DownloadStatus.WAITING, DownloadStatus.RUNNING,
                DownloadStatus.MERGING, DownloadStatus.RETRY_WAIT,
            }
            for task in tasks
        )
        can_resume = any(
            task.extraction_status is ExtractionStatus.PAUSED
            or task.download_status is DownloadStatus.PAUSED
            for task in tasks
        )
        active_extractions = [
            task for task in tasks
            if task.source_kind.value == "web_page"
            and task.extraction_status in {ExtractionStatus.WAITING, ExtractionStatus.RUNNING}
        ]
        restartable = [
            task for task in tasks
            if task.source_kind.value == "web_page"
            and task.download_status is not DownloadStatus.COMPLETED
            and (
                task.extraction_status in {
                    ExtractionStatus.PAUSED, ExtractionStatus.FAILED,
                    ExtractionStatus.COMPLETED,
                }
                or (
                    task.extraction_status is ExtractionStatus.NOT_REQUIRED
                    and task.download_status is DownloadStatus.PARTIAL_FAILURE
                )
            )
        ]
        _set_button_enabled(
            self.pause_task_button, can_pause,
            f"暂停选中的 {len(tasks)} 个任务" if can_pause else "所选任务没有可暂停的工作",
        )
        _set_button_enabled(
            self.resume_task_button, can_resume,
            f"继续选中的 {len(tasks)} 个任务" if can_resume else "所选任务没有已暂停的工作",
        )
        if active_extractions:
            self.stop_extraction_button.setText("停止提取")
            _set_button_enabled(
                self.stop_extraction_button, True,
                f"停止所选任务中 {len(active_extractions)} 个正在进行的网页提取",
            )
        else:
            self.stop_extraction_button.setText("重新提取")
            _set_button_enabled(
                self.stop_extraction_button, bool(restartable),
                f"重新提取所选的 {len(restartable)} 个网页任务"
                if restartable else "所选任务不支持重新提取",
            )

    def _clear_task_detail(self) -> None:
        self._detail_task_id = ""
        self._detail_render_state = None
        self.detail_title.clear()
        self._updating_item_table = True
        self.item_table.setRowCount(0)
        self._updating_item_table = False
        self.info_view.clear()
        with QSignalBlocker(self.select_all_checkbox):
            self.select_all_checkbox.setCheckState(Qt.CheckState.Unchecked)
        self.select_all_checkbox.setEnabled(False)
        self.select_all_checkbox.setCursor(Qt.CursorShape.ArrowCursor)
        self.select_all_checkbox.setToolTip("请先选择一个主任务")
        self.stop_extraction_button.setText("停止提取")
        reason = "请先选择一个主任务"
        _set_button_enabled(self.pause_task_button, False, reason)
        _set_button_enabled(self.resume_task_button, False, reason)
        _set_button_enabled(self.stop_extraction_button, False, reason)
        _set_button_enabled(self.download_selected_button, False, reason)
        _set_button_enabled(self.repair_task_button, False, reason)

    def _show_task_detail(self, current: QListWidgetItem | None, _previous) -> None:
        if current is None:
            self._clear_task_detail()
            return
        task: Task = current.data(Qt.ItemDataRole.UserRole)
        self._detail_task_id = task.id
        items = self._service.list_items(task.id)
        render_state = (task, tuple(items))
        if self._detail_render_state == render_state:
            self._refresh_logs(task.id)
            return
        can_pause = (
            task.extraction_status in {ExtractionStatus.WAITING, ExtractionStatus.RUNNING}
            or any(item.status.value in {"waiting", "downloading", "retry_wait"} for item in items)
        )
        can_resume = (
            task.extraction_status is ExtractionStatus.PAUSED
            or any(item.status.value == "paused" for item in items)
        )
        _set_button_enabled(
            self.pause_task_button, can_pause,
            "暂停当前任务" if can_pause else "当前任务没有可暂停的工作",
        )
        _set_button_enabled(
            self.resume_task_button, can_resume,
            "继续当前任务" if can_resume else "当前任务没有已暂停的工作",
        )
        extraction_active = task.extraction_status in {
            ExtractionStatus.WAITING, ExtractionStatus.RUNNING,
        }
        extraction_restartable = (
            task.source_kind.value == "web_page"
            and task.download_status is not DownloadStatus.COMPLETED
            and (
                task.extraction_status in {
                    ExtractionStatus.PAUSED, ExtractionStatus.FAILED,
                    ExtractionStatus.COMPLETED,
                }
                or (
                    task.extraction_status is ExtractionStatus.NOT_REQUIRED
                    and task.download_status is DownloadStatus.PARTIAL_FAILURE
                )
            )
        )
        self.stop_extraction_button.setText(
            "停止提取" if extraction_active else "重新提取"
        )
        _set_button_enabled(
            self.stop_extraction_button, extraction_active or extraction_restartable,
            (
                "停止当前网页提取" if extraction_active
                else "重新提取网页" if extraction_restartable
                else "当前任务不支持停止或重新提取"
            ),
        )
        completed_items = [
            item for item in items
            if item.status.value == "completed" and item.output_path
        ]
        _set_button_enabled(
            self.repair_task_button,
            task.download_status is DownloadStatus.COMPLETED and bool(completed_items),
            (
                "校验已完成文件，损坏时安全重新下载"
                if completed_items else "当前任务没有可校验的已完成文件"
            ),
        )
        self.detail_title.setText(task.name)
        if task.id not in self._pending_item_checks:
            self._pending_item_checks[task.id] = {
                item.id for item in items if item.status.value == "waiting"
            }
        elif task.selection_mode.value == "auto":
            self._pending_item_checks[task.id].update(
                item.id for item in items if item.status.value == "waiting"
            )
        pending_checks = self._pending_item_checks[task.id]
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
        self._refresh_selection_controls()
        info_text = (
            f"状态：{_task_status(task)}\n"
            f"来源：{task.source_url}\n"
            f"保存位置：{task.save_directory}\n"
            f"创建时间：{task.created_at:%Y-%m-%d %H:%M:%S}"
        )
        if self.info_view.toPlainText() != info_text:
            self.info_view.setPlainText(info_text)
        self._detail_render_state = render_state
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
        self._refresh_selection_controls()

    def _selectable_cells(self) -> list[QTableWidgetItem]:
        return [
            cell for row in range(self.item_table.rowCount())
            if (cell := self.item_table.item(row, 0)) is not None
            and bool(cell.flags() & Qt.ItemFlag.ItemIsUserCheckable)
        ]

    def _refresh_selection_controls(self) -> None:
        selectable = self._selectable_cells()
        checked_count = sum(
            cell.checkState() is Qt.CheckState.Checked for cell in selectable
        )
        if selectable and checked_count == len(selectable):
            master_state = Qt.CheckState.Checked
        elif checked_count:
            master_state = Qt.CheckState.PartiallyChecked
        else:
            master_state = Qt.CheckState.Unchecked
        with QSignalBlocker(self.select_all_checkbox):
            self.select_all_checkbox.setCheckState(master_state)
        task_selected = bool(getattr(self, "_detail_task_id", ""))
        can_select = task_selected and bool(selectable)
        extracting_without_candidates = False
        if task_selected and not selectable:
            task = self._service.get_task(self._detail_task_id)
            extracting_without_candidates = task.extraction_status in {
                ExtractionStatus.WAITING, ExtractionStatus.RUNNING,
            }
        self.select_all_checkbox.setEnabled(can_select)
        self.select_all_checkbox.setCursor(
            Qt.CursorShape.PointingHandCursor if can_select else Qt.CursorShape.ArrowCursor
        )
        self.select_all_checkbox.setToolTip(
            "选择或取消全部可下载项目"
            if can_select else "正在提取网页，发现下载项后会显示在这里"
            if extracting_without_candidates else "当前任务没有可选择的下载项"
            if task_selected else "请先选择一个主任务"
        )
        has_checked = checked_count > 0
        if not task_selected:
            tooltip = "请先选择一个主任务"
        elif extracting_without_candidates:
            tooltip = "正在提取网页，发现下载项后即可选择下载"
        elif not has_checked:
            tooltip = "请先勾选要下载的项目"
        else:
            tooltip = "下载当前勾选的项目"
        _set_button_enabled(self.download_selected_button, task_selected and has_checked, tooltip)

    def _toggle_all_candidate_checks(self, state: int) -> None:
        checked = Qt.CheckState(state) is Qt.CheckState.Checked
        task_id = getattr(self, "_detail_task_id", "")
        pending = self._pending_item_checks.setdefault(task_id, set()) if task_id else set()
        self._updating_item_table = True
        for cell in self._selectable_cells():
            cell.setCheckState(
                Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked
            )
            item_id = cell.data(Qt.ItemDataRole.UserRole)
            if checked:
                pending.add(item_id)
            else:
                pending.discard(item_id)
        self._updating_item_table = False
        self._refresh_selection_controls()

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
            return (
                task.download_status is DownloadStatus.PENDING_SELECTION
                and not task.last_error.startswith("疑似重复内容:")
            )
        if value == "review":
            return (
                task.download_status is DownloadStatus.PENDING_REVIEW
                or task.last_error.startswith("疑似重复内容:")
            )
        if value == "paused":
            return task.extraction_status is ExtractionStatus.PAUSED or task.download_status is DownloadStatus.PAUSED
        return task.extraction_status is ExtractionStatus.FAILED or task.download_status is DownloadStatus.PARTIAL_FAILURE

    def _download_selected_items(self) -> None:
        task_id = getattr(self, "_detail_task_id", "")
        if not task_id:
            self._show_feedback("请先选择一个下载任务")
            return
        task = self._service.get_task(task_id)
        if task.last_error.startswith("疑似重复内容:"):
            if self._review_content_duplicate(task_id) != "download":
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
        tasks = self._selected_active_tasks()
        if not tasks:
            self._show_feedback("请先选择一个下载任务")
            return
        controller = getattr(self, "background_controller", None)
        affected = 0
        for task in tasks:
            if not (
                task.extraction_status in {ExtractionStatus.WAITING, ExtractionStatus.RUNNING}
                or task.download_status in {
                    DownloadStatus.WAITING, DownloadStatus.RUNNING,
                    DownloadStatus.MERGING, DownloadStatus.RETRY_WAIT,
                }
            ):
                continue
            (self._service.pause_task if controller is None else controller.pause_task)(task.id)
            self._service.add_log(task.id, "信息", "任务", "任务已暂停")
            affected += 1
        self.refresh_tasks()
        self._refresh_logs()
        self._show_feedback(f"已暂停 {affected} 个任务")

    def _resume_current_task(self) -> None:
        tasks = self._selected_active_tasks()
        if not tasks:
            self._show_feedback("请先选择一个下载任务")
            return
        controller = getattr(self, "background_controller", None)
        affected = 0
        for task in tasks:
            if not (
                task.extraction_status is ExtractionStatus.PAUSED
                or task.download_status is DownloadStatus.PAUSED
            ):
                continue
            (self._service.resume_task if controller is None else controller.resume_task)(task.id)
            self._service.add_log(task.id, "信息", "任务", "任务已继续")
            affected += 1
        self.refresh_tasks()
        self._refresh_logs()
        self._show_feedback(f"已继续 {affected} 个任务")

    def _toggle_current_extraction(self) -> None:
        tasks = self._selected_active_tasks()
        if not tasks:
            self._show_feedback("请先选择一个下载任务")
            return
        active = [
            task for task in tasks
            if task.source_kind.value == "web_page"
            and task.extraction_status in {ExtractionStatus.WAITING, ExtractionStatus.RUNNING}
        ]
        if active:
            self._stop_extractions(active)
        else:
            restartable = [
                task for task in tasks
                if task.source_kind.value == "web_page"
                and task.download_status is not DownloadStatus.COMPLETED
                and (
                    task.extraction_status in {
                        ExtractionStatus.PAUSED, ExtractionStatus.FAILED,
                        ExtractionStatus.COMPLETED,
                    }
                    or (
                        task.extraction_status is ExtractionStatus.NOT_REQUIRED
                        and task.download_status is DownloadStatus.PARTIAL_FAILURE
                    )
                )
            ]
            self._restart_extractions(restartable)

    def _stop_current_extraction(self) -> None:
        tasks = [
            task for task in self._selected_active_tasks()
            if task.extraction_status in {ExtractionStatus.WAITING, ExtractionStatus.RUNNING}
        ]
        if not tasks:
            self._show_feedback("请先选择一个下载任务")
            return
        self._stop_extractions(tasks)

    def _stop_extractions(self, tasks: list[Task]) -> None:
        controller = getattr(self, "background_controller", None)
        for task in tasks:
            (self._service.stop_extraction if controller is None else controller.stop_extraction)(
                task.id
            )
            self._service.add_log(
                task.id, "信息", "提取", "用户停止提取，已应用当前候选"
            )
        self.refresh_tasks()
        self._refresh_logs()
        self._show_feedback(f"已停止 {len(tasks)} 个网页提取，可随时重新提取")

    def _restart_current_extraction(self, task_id: str) -> None:
        self._restart_extractions([self._service.get_task(task_id)])

    def _restart_extractions(self, tasks: list[Task]) -> None:
        if not tasks:
            self._show_feedback("所选任务不支持重新提取")
            return
        self.stop_extraction_button.setText("正在启动…")
        _set_button_enabled(self.stop_extraction_button, False)
        controller = getattr(self, "background_controller", None)
        delayed = 0
        for task in tasks:
            if controller is None:
                self._service.retry_extraction(task.id)
                started = True
            else:
                started = controller.retry_extraction(task.id)
            if not started:
                delayed += 1
                self._service.add_log(
                    task.id, "信息", "提取", "正在等待旧提取线程结束，随后自动重新提取"
                )
            else:
                self._pending_item_checks.pop(task.id, None)
                self._service.add_log(task.id, "信息", "提取", "用户重新开始网页提取")
        self.refresh_tasks()
        self._refresh_logs()
        if delayed:
            self._service.add_log(
                tasks[-1].id, "信息", "提取",
                f"{delayed} 个任务正在等待旧提取线程结束",
            )
        self._show_feedback(
            f"已重新开始 {len(tasks)} 个网页提取"
            if not delayed else
            f"已提交 {len(tasks)} 个网页提取，其中 {delayed} 个正在等待旧线程结束"
        )

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
        entries = self._service.list_logs(
            task_id=selected_task_id or None,
            level=level,
            after_id=self._log_session_start_id,
        )
        task_names = {task.id: task.name for task in self._service.list_tasks()}
        render_key = (selected_task_id or None, level, show_all, self._log_session_start_id)
        lines = [
            f"{entry.created_at:%H:%M:%S}  [{entry.level}] "
            f"[{entry.category}]  "
            f"{f'[{task_names.get(entry.task_id, entry.task_id)}]  ' if show_all else ''}"
            f"{entry.message}"
            for entry in entries
        ]
        entry_ids = [entry.id for entry in entries]
        prefix_matches = (
            render_key == self._log_render_key
            and entry_ids[:len(self._log_rendered_ids)] == self._log_rendered_ids
        )
        if not prefix_matches:
            self.log_view.setPlainText("\n".join(lines))
            self._log_render_key = render_key
            self._log_rendered_ids = entry_ids
            self._scroll_logs_to_latest()
            return
        new_lines = lines[len(self._log_rendered_ids):]
        if not new_lines:
            return
        scroll_bar = self.log_view.verticalScrollBar()
        old_scroll = scroll_bar.value()
        was_at_bottom = old_scroll >= scroll_bar.maximum() - 2
        selected_cursor = self.log_view.textCursor()
        anchor = selected_cursor.anchor()
        position = selected_cursor.position()
        append_cursor = QTextCursor(self.log_view.document())
        append_cursor.movePosition(QTextCursor.MoveOperation.End)
        if self.log_view.document().characterCount() > 1:
            append_cursor.insertBlock()
        append_cursor.insertText("\n".join(new_lines))
        selected_cursor.setPosition(anchor)
        selected_cursor.setPosition(position, QTextCursor.MoveMode.KeepAnchor)
        self.log_view.setTextCursor(selected_cursor)
        self._log_rendered_ids = entry_ids
        if was_at_bottom:
            self._scroll_logs_to_latest()
        else:
            scroll_bar.setValue(old_scroll)

    def _clear_current_session_logs(self) -> None:
        self._log_session_start_id = self._service.latest_log_id()
        self._log_render_key = None
        self._log_rendered_ids = []
        self._refresh_logs()
        self._show_feedback("已清空本次运行显示的日志")

    def _scroll_logs_to_latest(self) -> None:
        scroll_bar = self.log_view.verticalScrollBar()
        scroll_bar.setValue(scroll_bar.maximum())

    def show_from_tray(self) -> None:
        self.showNormal()
        self.raise_()
        self.activateWindow()

    def request_exit(self) -> None:
        self._persist_window_size()
        self._persist_horizontal_splitter_sizes()
        self._persist_item_table_header_state()
        self._force_exit = True
        controller = getattr(self, "background_controller", None)
        if controller is not None:
            controller.stop()
        QApplication.quit()

    def closeEvent(self, event) -> None:
        self._persist_window_size()
        self._persist_horizontal_splitter_sizes()
        self._persist_item_table_header_state()
        if self._force_exit:
            event.accept()
            return
        settings = self._service.load_app_settings()
        active = any(
            task.download_status is not DownloadStatus.COMPLETED
            for task in self._service.list_tasks()
        )
        if self._session_close_action in {"tray", "exit"}:
            self._apply_close_action(self._session_close_action, event, active)
            return
        if settings.close_rule_enabled:
            self._apply_close_action(
                self._close_action_for_rule(settings.close_rule, active), event, active,
            )
            return
        default_action = self._close_action_for_rule(settings.close_rule, active)
        action, remember = self._ask_close_action(default_action == "tray")
        if action is None:
            event.ignore()
            return
        if remember:
            self._session_close_action = action
        self._apply_close_action(action, event, active)

    @staticmethod
    def _close_action_for_rule(rule: str, active: bool) -> str:
        if rule == "smart":
            return "tray" if active else "exit"
        return rule

    def _ask_close_action(self, default_to_tray: bool) -> tuple[str | None, bool]:
        box = QMessageBox(self)
        box.setWindowTitle("关闭程序")
        box.setText("是否最小化到托盘？")
        box.setMinimumSize(360, 190)
        message_label = box.findChild(QLabel, "qt_msgbox_label")
        if message_label is not None:
            message_label.setMinimumWidth(260)
        box.setStandardButtons(
            QMessageBox.StandardButton.Yes
            | QMessageBox.StandardButton.No
            | QMessageBox.StandardButton.Cancel
        )
        yes_button = box.button(QMessageBox.StandardButton.Yes)
        no_button = box.button(QMessageBox.StandardButton.No)
        cancel_button = box.button(QMessageBox.StandardButton.Cancel)
        yes_button.setText("是")
        no_button.setText("否")
        cancel_button.hide()
        box.setDefaultButton(yes_button if default_to_tray else no_button)
        box.setEscapeButton(cancel_button)
        remember_check = QCheckBox("本次不再询问", box)
        remember_check.setChecked(True)
        box.setCheckBox(remember_check)
        result = box.exec()
        if result == QMessageBox.StandardButton.Yes:
            return "tray", remember_check.isChecked()
        if result == QMessageBox.StandardButton.No:
            return "exit", remember_check.isChecked()
        return None, False

    def _apply_close_action(self, action: str, event, active: bool) -> None:
        if action == "tray":
            self.hide()
            event.ignore()
            return
        controller = getattr(self, "background_controller", None)
        if active:
            if controller is not None:
                controller.pause_all()
            else:
                self._service.pause_all()
        if controller is not None:
            controller.stop()
        self._force_exit = True
        event.accept()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        if self.isMaximized() or self.isFullScreen():
            return
        if not hasattr(self, "_window_size_save_timer"):
            return
        size = event.size()
        self._pending_window_size = (size.width(), size.height())
        self._window_size_save_timer.start(250)

    def _persist_window_size(self) -> None:
        if self._pending_window_size is None:
            return
        width, height = self._pending_window_size
        self._pending_window_size = None
        settings = self._service.load_app_settings()
        if settings.window_width == width and settings.window_height == height:
            return
        self._service.save_app_settings(replace(
            settings, window_width=width, window_height=height,
        ))

    def _show_task_menu(self, task_list: QListWidget, position) -> None:
        item = task_list.itemAt(position)
        if item is None:
            return
        if not item.isSelected():
            task_list.clearSelection()
            item.setSelected(True)
        task_list.selectionModel().setCurrentIndex(
            task_list.indexFromItem(item), QItemSelectionModel.SelectionFlag.NoUpdate,
        )
        task = item.data(Qt.ItemDataRole.UserRole)
        selected_tasks = [
            selected.data(Qt.ItemDataRole.UserRole)
            for selected in task_list.selectedItems()
        ]
        menu = QMenu(self)
        if len(selected_tasks) > 1 and all(
            selected.download_status is not DownloadStatus.COMPLETED
            for selected in selected_tasks
        ):
            pausable = [
                selected for selected in selected_tasks
                if selected.extraction_status in {
                    ExtractionStatus.WAITING, ExtractionStatus.RUNNING,
                }
                or selected.download_status in {
                    DownloadStatus.WAITING, DownloadStatus.RUNNING,
                    DownloadStatus.MERGING, DownloadStatus.RETRY_WAIT,
                }
            ]
            resumable = [
                selected for selected in selected_tasks
                if selected.extraction_status is ExtractionStatus.PAUSED
                or selected.download_status is DownloadStatus.PAUSED
            ]
            extracting = [
                selected for selected in selected_tasks
                if selected.source_kind.value == "web_page"
                and selected.extraction_status in {
                    ExtractionStatus.WAITING, ExtractionStatus.RUNNING,
                }
            ]
            restartable = [
                selected for selected in selected_tasks
                if selected.source_kind.value == "web_page"
                and (
                    selected.extraction_status in {
                        ExtractionStatus.PAUSED, ExtractionStatus.FAILED,
                        ExtractionStatus.COMPLETED,
                    }
                    or (
                        selected.extraction_status is ExtractionStatus.NOT_REQUIRED
                        and selected.download_status is DownloadStatus.PARTIAL_FAILURE
                    )
                )
            ]
            retryable = [
                selected for selected in selected_tasks
                if selected.download_status is DownloadStatus.PARTIAL_FAILURE
            ]
            if pausable:
                menu.addAction("暂停", self._pause_current_task)
            if resumable:
                menu.addAction("继续", self._resume_current_task)
            if extracting:
                menu.addAction("停止提取", lambda: self._stop_extractions(extracting))
            if restartable:
                menu.addAction("重新提取", lambda: self._restart_extractions(restartable))
            if retryable:
                menu.addAction(
                    "重试失败项",
                    lambda: self._retry_failed_tasks(retryable),
                )
            menu.addAction(
                "任务设置", lambda: self._edit_task_settings_batch(selected_tasks, task)
            )
            queue_menu = menu.addMenu("调整队列")
            queue_menu.addAction(
                "优先下载 / 移到最前",
                lambda: self._move_tasks(selected_tasks, "front"),
            )
            queue_menu.addAction("上移", lambda: self._move_tasks(selected_tasks, "up"))
            queue_menu.addAction("下移", lambda: self._move_tasks(selected_tasks, "down"))
            queue_menu.addAction(
                "移到最后", lambda: self._move_tasks(selected_tasks, "back")
            )
            menu.addSeparator()
        elif len(selected_tasks) > 1:
            menu.addAction(
                "重新下载", lambda: self._redownload_tasks(selected_tasks)
            )
            web_tasks = [
                selected for selected in selected_tasks
                if selected.source_kind.value == "web_page"
            ]
            if web_tasks:
                menu.addAction(
                    "重新提取", lambda: self._reextract_task_copies(web_tasks)
                )
            menu.addAction(
                "校验并修复", lambda: self._validate_and_repair_tasks(selected_tasks)
            )
            menu.addSeparator()
        if len(selected_tasks) == 1 and task.download_status is not DownloadStatus.COMPLETED:
            menu.addAction("继续", self._resume_current_task)
            menu.addAction("暂停", self._pause_current_task)
            if task.extraction_status in {ExtractionStatus.WAITING, ExtractionStatus.RUNNING}:
                menu.addAction("停止提取", self._stop_current_extraction)
            if task.source_kind.value == "web_page" and (
                task.extraction_status in {
                    ExtractionStatus.FAILED, ExtractionStatus.COMPLETED,
                }
                or (
                    task.extraction_status is ExtractionStatus.NOT_REQUIRED
                    and task.download_status is DownloadStatus.PARTIAL_FAILURE
                )
            ):
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
        elif len(selected_tasks) == 1:
            menu.addAction("重新下载", lambda: self._redownload_task(task.id))
            if task.source_kind.value == "web_page":
                menu.addAction("重新提取", lambda: self._reextract_task_copy(task.id))
            completed_items = [
                candidate for candidate in self._service.list_items(task.id)
                if candidate.status.value == "completed"
            ]
            menu.addAction("查看下载项", lambda: self.detail_tabs.setCurrentIndex(0))
            menu.addAction(
                "校验并修复", lambda: self._validate_and_repair_task(task.id)
            )
            if len(completed_items) == 1:
                completed_item = completed_items[0]
                if completed_item.output_path and Path(completed_item.output_path).is_file():
                    menu.addAction("打开文件", lambda: self._open_item_file(completed_item))
                    menu.addAction("定位文件", lambda: self._locate_item_file(completed_item))
                    menu.addAction(
                        "重命名文件",
                        lambda: self._rename_item_file(task.id, completed_item),
                    )
        if len(selected_tasks) == 1:
            menu.addAction("查看详情", lambda: self.detail_tabs.setCurrentIndex(1))
            menu.addAction("打开保存位置", lambda: self._open_task_directory(task))
            menu.addAction("重命名任务", lambda: self._rename_task(task))
            menu.addAction(
                "复制原始链接",
                lambda: self._copy_text(task.source_url, "原始链接已复制"),
            )
        menu.addSeparator()
        menu.addAction("删除任务", lambda: self._delete_tasks(selected_tasks, False))
        menu.addAction("彻底删除文件", lambda: self._delete_tasks(selected_tasks, True))
        menu.exec(task_list.mapToGlobal(position))

    def _edit_task_settings(self, task: Task) -> None:
        dialog = TaskSettingsDialog(task, self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self._service.update_task_settings(task.id, dialog.settings())
            self._after_task_action(task.id, "任务设置已保存")

    def _edit_task_settings_batch(self, tasks: list[Task], template: Task) -> None:
        dialog = TaskSettingsDialog(template, self)
        dialog.setWindowTitle(f"批量任务设置（{len(tasks)} 个任务）")
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        settings = dialog.settings()
        for task in tasks:
            current_settings = self._service.get_task(task.id).settings
            task_settings = replace(
                settings,
                allow_content_duplicate=current_settings.allow_content_duplicate,
            )
            if hasattr(dialog, "cookie") and not dialog.cookie.text():
                task_settings = replace(
                    task_settings, protected_cookie=current_settings.protected_cookie,
                )
            self._service.update_task_settings(task.id, task_settings)
            self._service.add_log(task.id, "信息", "任务", "批量任务设置已保存")
        self._refresh_site_tables()
        self.refresh_tasks()
        self._refresh_logs()
        self._show_feedback(f"已更新 {len(tasks)} 个任务的设置")

    def _move_tasks(self, tasks: list[Task], direction: str) -> None:
        self._service.move_tasks([task.id for task in tasks], direction)
        self.refresh_tasks()
        self._show_feedback(f"已调整 {len(tasks)} 个任务的队列位置")

    def _retry_failed_tasks(self, tasks: list[Task]) -> None:
        for task in tasks:
            self._service.retry_failed_items(task.id)
            self._service.add_log(task.id, "信息", "任务", "失败项已重新加入队列")
        self.refresh_tasks()
        self._refresh_logs()
        self._show_feedback(f"已重试 {len(tasks)} 个任务的失败项")

    def _validate_and_repair_tasks(self, tasks: list[Task]) -> None:
        started = 0
        controller = getattr(self, "background_controller", None)
        if controller is None:
            self._show_feedback("后台下载服务尚未启动")
            return
        for task in tasks:
            targets = [
                item.id for item in self._service.list_items(task.id)
                if item.status.value == "completed" and item.output_path
            ]
            if targets and controller.repair_task(task.id, targets):
                self._service.add_log(
                    task.id, "信息", "修复", f"开始校验 {len(targets)} 个已完成文件",
                )
                started += 1
        self._refresh_logs()
        self._show_feedback(f"已开始校验 {started} 个任务")

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
            menu.addAction(
                "校验并修复",
                lambda: self._validate_and_repair_task(task_id, [item.id]),
            )
            menu.addAction("重新下载", lambda: self._redownload_item(task_id, item.id))
        menu.addAction(
            "复制 m3u8 链接",
            lambda: self._copy_text(item.source_url, "m3u8 链接已复制"),
        )
        menu.exec(self.item_table.viewport().mapToGlobal(position))

    def _open_item_from_table(self, cell: QTableWidgetItem) -> None:
        task_id = getattr(self, "_detail_task_id", "")
        if not task_id:
            return
        id_cell = self.item_table.item(cell.row(), 0)
        if id_cell is None:
            return
        item = self._service.get_item(
            task_id, id_cell.data(Qt.ItemDataRole.UserRole),
        )
        if not item.output_path:
            self._show_feedback("该下载项尚无可打开文件")
            return
        self._open_item_file(item)

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

    def _validate_and_repair_current_task(self) -> None:
        task_id = getattr(self, "_detail_task_id", "")
        if not task_id:
            self._show_feedback("请先选择一个已完成任务")
            return
        self._validate_and_repair_task(task_id)

    def _validate_and_repair_task(
        self, task_id: str, item_ids: list[str] | None = None,
    ) -> None:
        completed = [
            item.id for item in self._service.list_items(task_id)
            if item.status.value == "completed" and item.output_path
        ]
        targets = item_ids or completed
        if not targets:
            self._show_feedback("当前任务没有可校验的文件")
            return
        controller = getattr(self, "background_controller", None)
        if controller is None:
            self._show_feedback("后台下载服务尚未启动")
            return
        if controller.repair_task(task_id, targets):
            self._service.add_log(
                task_id, "信息", "修复", f"开始校验 {len(targets)} 个已完成文件",
            )
            self._refresh_logs(task_id)
            self._show_feedback(f"正在后台校验 {len(targets)} 个文件")
        else:
            self._show_feedback("该任务正在校验或修复")

    def _redownload_task(self, task_id: str) -> None:
        copied = self._service.redownload_task(task_id)
        self._after_task_action(copied.id, "已创建重新下载任务")
        self._switch_view(0)

    def _redownload_tasks(self, tasks: list[Task]) -> None:
        copies = [self._service.redownload_task(task.id) for task in tasks]
        for copied in copies:
            self._service.add_log(copied.id, "信息", "任务", "已创建重新下载任务")
        self._switch_view(0)
        self._show_feedback(f"已创建 {len(copies)} 个重新下载任务")

    def _reextract_task_copy(self, task_id: str) -> None:
        copied = self._service.reextract_task(task_id)
        self._service.add_log(copied.id, "信息", "提取", "已创建重新提取任务")
        self.refresh_tasks()
        self._switch_view(0)
        self._show_task_by_id(copied.id)
        self._show_feedback("已创建重新提取任务")

    def _reextract_task_copies(self, tasks: list[Task]) -> None:
        copies = [self._service.reextract_task(task.id) for task in tasks]
        for copied in copies:
            self._service.add_log(
                copied.id, "信息", "提取", "已创建重新提取任务",
            )
        self.refresh_tasks()
        self._switch_view(0)
        self._show_feedback(f"已创建 {len(copies)} 个重新提取任务")

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

    def _copy_text(self, value: str, message: str) -> None:
        QApplication.clipboard().setText(value)
        self._show_feedback(message)

    def _open_item_file(self, item) -> None:
        path = Path(item.output_path)
        if path.is_file():
            os.startfile(str(path))
            self._show_feedback("已打开文件")
        else:
            self._show_feedback("文件不存在，请重新关联文件")

    def _locate_item_file(self, item) -> None:
        path = Path(item.output_path)
        if path.is_file():
            os.spawnl(os.P_NOWAIT, os.path.join(os.environ.get("WINDIR", "C:\\Windows"), "explorer.exe"),
                      "explorer.exe", "/select,", str(path))
            self._show_feedback("已在文件管理器中定位")
        else:
            self._show_feedback("文件不存在，请重新关联文件")

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
                    task_list.scrollToItem(candidate)
                    return
        raise KeyError(task_id)

    def _open_task_directory(self, task: Task) -> None:
        directory = Path(task.save_directory)
        if directory.is_dir():
            os.startfile(str(directory))
            self._show_feedback("已打开保存位置")
        else:
            self._show_feedback("保存目录不存在")

    def _rename_task(self, task: Task) -> None:
        name, accepted = QInputDialog.getText(
            self, "重命名任务", "任务名称", text=task.name
        )
        if accepted and name.strip():
            self._service.rename_task(task.id, name)
            self._after_task_action(task.id, f"任务已重命名为 {name.strip()}")

    def _move_task(self, task_id: str, direction: str) -> None:
        self._service.move_task(task_id, direction)
        self.refresh_tasks()
        self._show_feedback("任务顺序已调整")

    def _delete_task(self, task: Task, delete_outputs: bool) -> None:
        self._delete_tasks([task], delete_outputs)

    def _delete_tasks(self, tasks: list[Task], delete_outputs: bool) -> None:
        if not tasks:
            return
        previews = [self._service.preview_deletion(task.id) for task in tasks]
        output_files = tuple(dict.fromkeys(
            path for preview in previews for path in preview.output_files
        ))
        total_bytes = sum(path.stat().st_size for path in output_files if path.is_file())
        task_count = len(tasks)
        if delete_outputs:
            file_lines = "\n".join(str(path) for path in output_files) or "没有已记录的输出文件"
            size_mb = total_bytes / 1024 / 1024
            message = (
                f"将彻底删除 {task_count} 个任务、{len(output_files)} 个文件"
                f"（共 {size_mb:.2f} MB）：\n\n"
                f"{file_lines}\n\n此操作无法撤销。"
            )
            title = "彻底删除文件"
        else:
            message = f"删除 {task_count} 个任务记录？已完成的输出文件会保留。"
            title = "删除任务"
        if not self._confirm_task_deletion(title, message, delete_outputs):
            return
        controller = getattr(self, "background_controller", None)
        for task in tasks:
            if controller is not None:
                controller.delete_task(task.id, delete_outputs=delete_outputs)
            else:
                self._service.delete_task(task.id, delete_outputs=delete_outputs)
        self.refresh_tasks()
        self._refresh_logs()
        self._show_feedback(
            f"已彻底删除 {task_count} 个任务及其文件"
            if delete_outputs else f"已删除 {task_count} 个任务，文件已保留"
        )

    def _confirm_task_deletion(
        self, title: str, message: str, delete_outputs: bool,
    ) -> bool:
        skip_attribute = (
            "_skip_permanent_delete_confirmation"
            if delete_outputs else "_skip_delete_task_confirmation"
        )
        if getattr(self, skip_attribute):
            return True
        box = QMessageBox(self)
        box.setIcon(
            QMessageBox.Icon.Critical if delete_outputs else QMessageBox.Icon.Warning
        )
        box.setWindowTitle(title)
        box.setText(message)
        box.setStandardButtons(
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel
        )
        box.setDefaultButton(QMessageBox.StandardButton.Cancel)
        box.button(QMessageBox.StandardButton.Yes).setText(
            "彻底删除" if delete_outputs else "删除任务"
        )
        box.button(QMessageBox.StandardButton.Cancel).setText("取消")
        skip_confirmation = QCheckBox("本次不再询问", box)
        box.setCheckBox(skip_confirmation)
        if box.exec() != QMessageBox.StandardButton.Yes:
            return False
        if skip_confirmation.isChecked():
            setattr(self, skip_attribute, True)
        return True


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
    QTimer.singleShot(1200, window.maybe_check_for_updates)
    controller.start()
    return app.exec()
