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
            attempt_name = "深度模式" if deep else "普通模式"
            self._service.add_log(
                task_id, "信息", "提取", f"正在使用{attempt_name}提取网页",
            )
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
                has_valid = any(
                    item.valid for item in self._service.list_items(task_id)
                )
                if deep and mode == "smart" and not has_valid:
                    self._service.add_log(
                        task_id, "警告", "提取",
                        "深度模式未找到可下载的 m3u8 链接，自动尝试普通模式",
                    )
                    continue
                if not has_valid:
                    message = (
                        "智能模式未找到可下载的 m3u8 链接（已依次尝试深度模式和普通模式）"
                        if mode == "smart" else "未找到可下载的 m3u8 链接"
                    )
                    return self._service.fail_extraction(task_id, message)
                self._service.finish_extraction(task_id)
                return self._service.finish_parent_if_handled(task_id)
            except Exception as exc:
                last_error = exc
                if stop_event.is_set():
                    return self._service.get_task(task_id)
                if deep and mode == "smart":
                    self._service.add_log(
                        task_id, "警告", "提取",
                        f"深度模式未完成：{exc}；自动尝试普通模式",
                    )
                    continue
                message = str(exc)
                if mode == "smart":
                    message = (
                        "智能模式提取失败（已依次尝试深度模式和普通模式）。"
                        "该网页可能不使用 m3u8，或需要登录、Referer、Cookie。"
                    )
                self._service.fail_extraction(task_id, message)
                raise RuntimeError(message) from exc
        message = str(last_error or "提取失败")
        if mode == "smart":
            message = "智能模式提取失败（已依次尝试深度模式和普通模式）"
        self._service.fail_extraction(task_id, message)
        raise RuntimeError(message)
