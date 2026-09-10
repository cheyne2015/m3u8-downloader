"""提取与任务状态之间的协调层。"""

from __future__ import annotations

import threading

from .models import Candidate
from .service import TaskService


class TaskCoordinator:
    def __init__(self, service: TaskService, *, extractor) -> None:
        self._service = service
        self._extractor = extractor

    def run_extraction(self, task_id: str, stop_event=None):
        """同步执行一次提取；图形界面负责把本方法放入工作线程。"""
        stop_event = stop_event or threading.Event()
        task = self._service.start_extraction(task_id)

        def on_candidate(candidate: Candidate) -> None:
            self._service.add_candidates(task_id, [candidate])

        def on_title(title: str) -> None:
            self._service.apply_page_title(task_id, title)

        mode = task.settings.extraction_mode
        attempts = [True, False] if mode == "smart" else [mode == "deep"]
        last_error = None
        for deep in attempts:
            try:
                candidates = self._extractor.extract(
                    task,
                    deep=deep,
                    on_candidate=on_candidate,
                    on_title=on_title,
                    stop_event=stop_event,
                )
                self._service.add_candidates(task_id, list(candidates or []))
                if stop_event.is_set():
                    return self._service.get_task(task_id)
                self._service.finish_extraction(task_id)
                return self._service.finish_parent_if_handled(task_id)
            except Exception as exc:
                last_error = exc
                if deep and mode == "smart":
                    continue
                self._service.fail_extraction(task_id, str(exc))
                raise
        self._service.fail_extraction(task_id, str(last_error or "提取失败"))
        raise RuntimeError(str(last_error or "提取失败"))
