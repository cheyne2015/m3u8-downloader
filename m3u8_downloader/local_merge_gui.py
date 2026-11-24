"""本地分片合并窗口。"""
from pathlib import Path
import threading

from PySide6.QtCore import QUrl, Slot
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QApplication, QDialog, QFileDialog, QFormLayout, QHBoxLayout,
    QLabel, QLineEdit, QPushButton, QVBoxLayout,
)

from .local_merge import merge_local_playlist, suggest_output_path


class LocalMergeDialog(QDialog):
    def __init__(self, parent, runner):
        super().__init__(parent)
        self.setWindowTitle('本地合并')
        self.resize(620, 280)
        self._runner = runner
        self._stop = threading.Event()
        self._running = False
        QApplication.instance().aboutToQuit.connect(self.cancel_for_exit)
        layout = QVBoxLayout(self)
        note = QLabel('选择本地 m3u8 清单，按清单顺序合并分片。支持 AES-128；原文件保留。')
        note.setWordWrap(True)
        layout.addWidget(note)
        form = QFormLayout()
        self.playlist_edit = QLineEdit()
        self.output_edit = QLineEdit()
        self.browse_button = QPushButton('浏览…')
        self.save_button = QPushButton('选择…')
        for title, edit, button in (
            ('本地清单', self.playlist_edit, self.browse_button),
            ('输出文件', self.output_edit, self.save_button),
        ):
            row = QHBoxLayout()
            row.addWidget(edit)
            row.addWidget(button)
            form.addRow(title, row)
        layout.addLayout(form)
        self.status_label = QLabel('请选择清单；缺少分片或密钥时会提示。')
        self.status_label.setWordWrap(True)
        layout.addWidget(self.status_label)
        row = QHBoxLayout()
        self.open_button = QPushButton('打开输出目录')
        self.open_button.setEnabled(False)
        self.start_button = QPushButton('开始合并')
        self.cancel_button = QPushButton('关闭')
        row.addWidget(self.open_button)
        row.addStretch()
        row.addWidget(self.start_button)
        row.addWidget(self.cancel_button)
        layout.addLayout(row)
        self.browse_button.clicked.connect(self._browse)
        self.save_button.clicked.connect(self._save)
        self.start_button.clicked.connect(self._start)
        self.cancel_button.clicked.connect(self.reject)
        self.open_button.clicked.connect(lambda: QDesktopServices.openUrl(
            QUrl.fromLocalFile(str(self._result.parent))))

    def _browse(self):
        path, _ = QFileDialog.getOpenFileName(self, '选择本地清单', '', 'M3U8 清单 (*.m3u8)')
        if path:
            self.playlist_edit.setText(path)
            self.output_edit.setText(str(suggest_output_path(path)))

    def _save(self):
        path, _ = QFileDialog.getSaveFileName(self, '选择输出文件', self.output_edit.text(), 'MP4 视频 (*.mp4)')
        if path:
            self.output_edit.setText(path)

    def _start(self):
        playlist = self.playlist_edit.text().strip()
        output = self.output_edit.text().strip()
        if not playlist or not output:
            self.status_label.setText('请选择本地清单和输出文件。')
            return
        self._stop.clear()
        self._running = True
        self.open_button.setEnabled(False)
        for widget in (self.playlist_edit, self.output_edit, self.browse_button,
                       self.save_button, self.start_button):
            widget.setEnabled(False)
        self.cancel_button.setText('取消合并')
        self.status_label.setText('正在检查分片并合并，请稍候…')
        self._runner(lambda: merge_local_playlist(playlist, output, stop_event=self._stop), self._finished)

    def _finished(self, result, error):
        self._running = False
        for widget in (self.playlist_edit, self.output_edit, self.browse_button,
                       self.save_button, self.start_button):
            widget.setEnabled(True)
        self.cancel_button.setText('关闭')
        if error:
            self.status_label.setText(error)
        else:
            self._result = Path(result)
            self.status_label.setText(f'合并完成：{result}')
            self.open_button.setEnabled(True)

    def reject(self):
        if self._running:
            self._stop.set()
            self.status_label.setText('正在取消合并…')
            return
        super().reject()

    @Slot()
    def cancel_for_exit(self):
        self._stop.set()

    def closeEvent(self, event):
        if self._running:
            self.reject()
            event.ignore()
        else:
            super().closeEvent(event)
