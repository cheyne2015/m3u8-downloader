"""Qt 后台任务调度与工作线程。"""

from __future__ import annotations

import threading

from PySide6.QtCore import QObject, QRunnable, QThreadPool, QTimer, Signal

from .tasking import TaskScheduler


class _WorkerSignals(QObject):
    finished = Signal(str, str, str)
    log = Signal(str, str)


class _Worker(QRunnable):
    def __init__(self, kind: str, task_id: str, operation) -> None:
        super().__init__()
        self.kind = kind
        self.task_id = task_id
        self.operation = operation
        self.signals = _WorkerSignals()

    def run(self) -> None:
        error = ""
        try:
            self.operation()
        except Exception as exc:
            error = str(exc)
        self.signals.finished.emit(self.kind, self.task_id, error)


class TaskBackgroundController(QObject):
    changed = Signal()
    log = Signal(str, str)
    parent_completed = Signal(str, str)

    def __init__(
        self,
        service,
        *,
        extraction_coordinator,
        download_coordinator,
        poll_interval_ms: int = 500,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self._service = service
        self._extraction = extraction_coordinator
        self._download = download_coordinator
        self._scheduler = TaskScheduler()
        self._pool = QThreadPool(self)
        self._pool.setMaxThreadCount(12)
        self._extracting: set[str] = set()
        self._downloading: set[str] = set()
        self._workers: set[_Worker] = set()
        self._extract_events: dict[str, threading.Event] = {}
        self._download_events: dict[str, threading.Event] = {}
        self._completion_emitted: set[str] = set()
        self._pending_deletions: dict[str, bool] = {}
        self._timer = QTimer(self)
        self._timer.setInterval(max(10, poll_interval_ms))
        self._timer.timeout.connect(self.dispatch)

    def start(self) -> None:
        self._timer.start()
        self.dispatch()

    def stop(self) -> None:
        self._timer.stop()
        for event in [*self._extract_events.values(), *self._download_events.values()]:
            event.set()

    def pause_task(self, task_id: str) -> None:
        if task_id in self._extract_events:
            self._extract_events[task_id].set()
        if task_id in self._download_events:
            self._download_events[task_id].set()
        self._service.pause_task(task_id)
        self.changed.emit()

    def resume_task(self, task_id: str) -> None:
        self._service.resume_task(task_id)
        self.changed.emit()
        self.dispatch()

    def pause_item(self, task_id: str, item_id: str) -> None:
        item = self._service.get_item(task_id, item_id)
        event = self._download_events.get(task_id)
        if event is not None and item.status.value == "downloading":
            event.set()
        self._service.pause_item(task_id, item_id)
        self.changed.emit()

    def resume_item(self, task_id: str, item_id: str) -> None:
        self._service.resume_item(task_id, item_id)
        self.changed.emit()
        self.dispatch()

    def delete_task(self, task_id: str, *, delete_outputs: bool) -> None:
        extract_event = self._extract_events.get(task_id)
        download_event = self._download_events.get(task_id)
        if extract_event is not None:
            setattr(extract_event, "delete_requested", True)
            extract_event.set()
        if download_event is not None:
            setattr(download_event, "delete_requested", True)
            download_event.set()
        if task_id in self._extracting or task_id in self._downloading:
            self._pending_deletions[task_id] = delete_outputs
        else:
            self._service.delete_task(task_id, delete_outputs=delete_outputs)
        self.changed.emit()

    def stop_extraction(self, task_id: str) -> None:
        event = self._extract_events.get(task_id)
        if event is not None:
            event.set()
        task = self._service.stop_extraction(task_id)
        if (
            task.download_status.value == "completed"
            and task.id not in self._completion_emitted
        ):
            self._completion_emitted.add(task.id)
            self.parent_completed.emit(task.id, task.name)
        self.changed.emit()
        self.dispatch()

    def dispatch(self) -> None:
        if self._extracting or self._downloading:
            self.changed.emit()
        settings = self._service.load_app_settings()
        for task in self._service.promote_due_retries():
            self._record_log(task.id, "下载", "自动重试等待结束，任务已重新排队")
        tasks = self._service.list_tasks()
        plan = self._scheduler.plan(
            tasks,
            download_limit=settings.download_task_limit,
            extraction_limit=settings.extraction_task_limit,
        )
        for task_id in plan.start_extraction:
            if task_id in self._extracting:
                continue
            self._extracting.add(task_id)
            event = threading.Event()
            self._extract_events[task_id] = event
            self._submit(
                "extract",
                task_id,
                lambda task_id=task_id, event=event: self._extraction.run_extraction(
                    task_id, stop_event=event
                ),
            )
        for task_id in plan.start_download:
            if task_id in self._downloading:
                continue
            self._downloading.add(task_id)
            event = threading.Event()
            self._download_events[task_id] = event
            self._submit(
                "download",
                task_id,
                lambda task_id=task_id, event=event: self._download.run_parent(
                    task_id,
                    stop_event=event,
                    on_log=lambda message, task_id=task_id: self._record_log(
                        task_id, "下载", message
                    ),
                ),
            )

    def _submit(self, kind: str, task_id: str, operation) -> None:
        worker = _Worker(kind, task_id, operation)
        worker.signals.finished.connect(self._finished)
        self._workers.add(worker)
        self._pool.start(worker)

    def _finished(self, kind: str, task_id: str, error: str) -> None:
        if kind == "extract":
            self._extracting.discard(task_id)
            self._extract_events.pop(task_id, None)
        else:
            self._downloading.discard(task_id)
            self._download_events.pop(task_id, None)
        self._workers = {
            worker for worker in self._workers
            if not (worker.kind == kind and worker.task_id == task_id)
        }
        if (
            task_id in self._pending_deletions
            and task_id not in self._extracting
            and task_id not in self._downloading
        ):
            delete_outputs = self._pending_deletions.pop(task_id)
            self._service.delete_task(task_id, delete_outputs=delete_outputs)
            self.changed.emit()
            return
        if error:
            self._service.add_log(task_id, "错误", kind, error)
            self.log.emit(task_id, f"操作失败：{error}")
        else:
            task = self._service.get_task(task_id)
            if (
                task.download_status.value == "completed"
                and task.id not in self._completion_emitted
            ):
                self._completion_emitted.add(task.id)
                self.parent_completed.emit(task.id, task.name)
        self.changed.emit()
        if self._timer.isActive():
            QTimer.singleShot(0, self.dispatch)

    def _record_log(self, task_id: str, category: str, message: str) -> None:
        self._service.add_log(task_id, "信息", category, message)
        self.log.emit(task_id, message)

    def pause_all(self) -> None:
        for task in self._service.list_tasks():
            self.pause_task(task.id)

    def resume_all(self) -> None:
        self._service.resume_all()
        self.changed.emit()
        self.dispatch()
