"""父任务内部串行下载协调。"""

import threading
import errno
import os
import uuid
from dataclasses import replace
from pathlib import Path

from .models import ItemStatus
from .output import OutputPlanner
from .disk_space import DiskSpaceGuard
from ..merger import validate_media_file


class _InsufficientDiskSpace(RuntimeError):
    def __init__(self, free_bytes: int, required_bytes: int) -> None:
        self.free_bytes = free_bytes
        self.required_bytes = required_bytes
        super().__init__("磁盘空间不足")


class DownloadCoordinator:
    def __init__(self, service, *, downloader, output_planner=None, disk_guard=None) -> None:
        self._service = service
        self._downloader = downloader
        self._planner = output_planner or OutputPlanner()
        self._disk_guard = disk_guard or DiskSpaceGuard()

    def run_parent(self, task_id: str, stop_event=None, on_log=lambda _message: None):
        stop_event = stop_event or threading.Event()
        self._service.prepare_output_paths(task_id, self._planner)
        task = self._service.get_task(task_id)
        waiting_items = [
            item for item in self._service.list_items(task_id)
            if item.status is ItemStatus.WAITING
        ]
        decision = self._disk_guard.check(task.save_directory, waiting_items)
        if not decision.can_start:
            return self._service.block_for_space(
                task_id, decision.free_bytes, decision.required_bytes
            )
        while not stop_event.is_set():
            self._service.prepare_output_paths(task_id, self._planner)
            waiting = [
                item for item in self._service.list_items(task_id)
                if item.status is ItemStatus.WAITING
            ]
            if not waiting:
                break
            item = self._service.start_item(task_id, waiting[0].id)
            task = self._service.get_task(task_id)
            try:
                def report_progress(progress, item_id=item.id):
                    self._service.update_item_progress(task_id, item_id, progress)
                    remaining = [
                        replace(
                            candidate,
                            estimated_bytes=max(
                                0, candidate.estimated_bytes - candidate.downloaded_bytes
                            ) if candidate.estimated_bytes is not None else None,
                        ) for candidate in self._service.list_items(task_id)
                        if candidate.status in {ItemStatus.WAITING, ItemStatus.DOWNLOADING}
                    ]
                    live_decision = self._disk_guard.check(task.save_directory, remaining)
                    if not live_decision.can_start:
                        raise _InsufficientDiskSpace(
                            live_decision.free_bytes, live_decision.required_bytes
                        )

                output = self._downloader.download(
                    task,
                    item,
                    Path(item.output_path),
                    stop_event=stop_event,
                    on_progress=report_progress,
                    on_log=on_log,
                )
                self._service.complete_item(task_id, item.id, output)
            except Exception as exc:
                if stop_event.is_set():
                    break
                if isinstance(exc, _InsufficientDiskSpace):
                    self._service.block_for_space(
                        task_id, exc.free_bytes, exc.required_bytes
                    )
                    on_log("磁盘空间不足，任务已自动暂停")
                    break
                if isinstance(exc, OSError) and getattr(exc, "errno", None) == errno.ENOSPC:
                    decision = self._disk_guard.check(task.save_directory, [])
                    self._service.block_for_space(
                        task_id, decision.free_bytes, decision.required_bytes
                    )
                    on_log("磁盘已写满，任务已自动暂停")
                    break
                backup = self._service.activate_same_duration_backup(
                    task_id, item.id,
                )
                if backup is not None:
                    on_log(
                        f"首选候选未完成：{exc}；已自动切换同时间备用链接 "
                        f"{backup.source_url}"
                    )
                    continue
                if self._is_retryable(exc):
                    retrying = self._service.schedule_item_retry(task_id, item.id, str(exc))
                    if retrying.download_status.value == "retry_wait":
                        on_log(
                            f"下载暂时失败，将在 {task.settings.retry_delay_seconds} 秒后自动重试"
                        )
                else:
                    self._service.fail_item(task_id, item.id, str(exc))
                break
        return self._service.finish_parent_if_handled(task_id)

    def validate_and_repair(
        self, task_id: str, item_id: str, *, stop_event=None,
        on_log=lambda _message: None,
    ) -> str:
        """校验已完成文件；损坏时在同目录重下并原子替换。"""
        stop_event = stop_event or threading.Event()
        task = self._service.get_task(task_id)
        item = self._service.get_item(task_id, item_id)
        output = Path(item.output_path)
        if not output.is_file():
            raise FileNotFoundError(f"找不到已完成文件：{output}")
        try:
            validate_media_file(output)
            on_log("媒体文件校验通过，无需修复")
            return "valid"
        except RuntimeError as exc:
            on_log(f"媒体文件校验失败：{exc}；正在重新下载到临时文件")

        temporary = output.with_name(
            f".{output.stem}.{uuid.uuid4().hex}.repair{output.suffix or '.mp4'}"
        )
        try:
            repaired = Path(self._downloader.download(
                task,
                item,
                temporary,
                stop_event=stop_event,
                on_progress=lambda _progress: None,
                on_log=on_log,
            ))
            validate_media_file(repaired)
            if stop_event.is_set():
                raise RuntimeError("用户取消修复")
            os.replace(repaired, output)
            self._service.complete_item(task_id, item_id, output)
            on_log("媒体文件已校验并修复")
            return "repaired"
        except Exception:
            for path in {temporary, Path(str(temporary) + ".part")}:
                try:
                    if path.is_file():
                        path.unlink()
                except OSError:
                    pass
            raise

    @staticmethod
    def _is_retryable(error: Exception) -> bool:
        if isinstance(error, (ConnectionError, TimeoutError)):
            return True
        message = str(error).casefold()
        return any(token in message for token in (
            "timeout", "timed out", "connection", "network", "断线", "网络",
            "500", "502", "503", "504",
        ))
