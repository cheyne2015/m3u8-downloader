"""提取与任务状态之间的协调层。"""

from __future__ import annotations

import threading
import zlib

from .models import Candidate
from .service import TaskService
from ..content_verifier import ContentDuplicateVerifier


class TaskCoordinator:
    def __init__(
        self, service: TaskService, *, extractor, retry_wait=None,
        content_verifier=None,
    ) -> None:
        self._service = service
        self._extractor = extractor
        self._retry_wait = retry_wait or (lambda event, delay: event.wait(delay))
        self._content_verifier = content_verifier or ContentDuplicateVerifier()

    @staticmethod
    def _is_retryable(error: Exception) -> bool:
        message = str(error).casefold()
        permanent = (
            "缺少 playwright", "缺少浏览器", "浏览器内核", "浏览器不可用",
            "地址格式", "链接格式", "不支持的地址", "用户停止",
        )
        return not any(token in message for token in permanent)

    @staticmethod
    def _retry_delay(task_id: str, retry_number: int) -> float:
        base = (3.0, 8.0)[retry_number - 1]
        spread_ms = (3000, 6000)[retry_number - 1]
        jitter = zlib.crc32(f"{task_id}:{retry_number}".encode("utf-8")) % spread_ms
        return base + jitter / 1000.0

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
            for attempt_index in range(3):
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
                    if any(item.valid for item in self._service.list_items(task_id)):
                        self._service.finish_extraction(task_id)
                        self._service.record_content_identity(task_id)
                        if task.settings.allow_content_duplicate:
                            self._service.add_log(
                                task_id, "信息", "任务",
                                "重新下载任务已跳过重复内容拦截",
                            )
                            return self._service.finish_parent_if_handled(task_id)
                        duplicate = self._service.find_content_duplicate(task_id)
                        if duplicate is not None:
                            match = self._content_verifier.matches(
                                duplicate,
                                self._service.list_items(duplicate.id),
                                self._service.get_task(task_id),
                                self._service.list_items(task_id),
                            )
                            if match is False:
                                self._service.add_log(
                                    task_id, "信息", "任务",
                                    "媒体抽样不同，已排除疑似重复并继续下载",
                                )
                                return self._service.finish_parent_if_handled(task_id)
                            self._service.add_log(
                                task_id, "警告", "任务",
                                (
                                    f"媒体抽样一致，发现疑似重复内容，已转为待处理；"
                                    f"已有任务：{duplicate.name}"
                                    if match is True else
                                    f"媒体抽样不可用，按媒体结构发现疑似重复内容，已转为待处理；"
                                    f"已有任务：{duplicate.name}"
                                ),
                            )
                            return self._service.hold_for_content_duplicate(
                                task_id, duplicate.id,
                            )
                        return self._service.finish_parent_if_handled(task_id)
                    last_error = RuntimeError("未找到可下载的 m3u8 链接")
                except Exception as exc:
                    last_error = exc
                    if stop_event.is_set():
                        return self._service.get_task(task_id)

                retry_number = attempt_index + 1
                if attempt_index < 2 and self._is_retryable(last_error):
                    delay = self._retry_delay(task_id, retry_number)
                    self._service.add_log(
                        task_id, "警告", "提取",
                        f"{attempt_name}未完成：{last_error}；"
                        f"将在 {delay:.1f} 秒后进行第 {retry_number} 次自动重试",
                    )
                    if self._retry_wait(stop_event, delay) or stop_event.is_set():
                        return self._service.get_task(task_id)
                    continue
                break

            if deep and mode == "smart":
                self._service.add_log(
                    task_id, "警告", "提取",
                    f"深度模式未完成：{last_error}；自动尝试普通模式",
                )
                continue
            message = str(last_error or "提取失败")
            if mode == "smart":
                message = (
                    "智能模式提取失败（已依次尝试深度模式和普通模式）。"
                    "该网页可能不使用 m3u8，或需要登录、Referer、Cookie。"
                )
            self._service.fail_extraction(task_id, message)
            raise RuntimeError(message) from last_error
        message = str(last_error or "提取失败")
        if mode == "smart":
            message = "智能模式提取失败（已依次尝试深度模式和普通模式）"
        self._service.fail_extraction(task_id, message)
        raise RuntimeError(message)
