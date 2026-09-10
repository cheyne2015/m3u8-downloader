"""Qt 后台任务调度与工作线程。"""

from __future__ import annotations

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
        self._timer = QTimer(self)
        self._timer.setInterval(max(10, poll_interval_ms))
        self._timer.timeout.connect(self.dispatch)

    def start(self) -> None:
        self._timer.start()
        self.dispatch()

    def stop(self) -> None:
        self._timer.stop()

    def dispatch(self) -> None:
        settings = self._service.load_app_settings()
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
            self._submit(
                "extract",
                task_id,
                lambda task_id=task_id: self._extraction.run_extraction(task_id),
            )
        for task_id in plan.start_download:
            if task_id in self._downloading:
                continue
            self._downloading.add(task_id)
            self._submit(
                "download",
                task_id,
                lambda task_id=task_id: self._download.run_parent(
                    task_id,
                    on_log=lambda message: self.log.emit(task_id, message),
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
        else:
            self._downloading.discard(task_id)
        self._workers = {
            worker for worker in self._workers
            if not (worker.kind == kind and worker.task_id == task_id)
        }
        if error:
            self.log.emit(task_id, f"操作失败：{error}")
        self.changed.emit()
        if self._timer.isActive():
            QTimer.singleShot(0, self.dispatch)

