"""父任务内部串行下载协调。"""

import threading
from pathlib import Path

from .models import ItemStatus
from .output import OutputPlanner
from .disk_space import DiskSpaceGuard


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
            waiting = [
                item for item in self._service.list_items(task_id)
                if item.status is ItemStatus.WAITING
            ]
            if not waiting:
                break
            item = self._service.start_item(task_id, waiting[0].id)
            task = self._service.get_task(task_id)
            try:
                output = self._downloader.download(
                    task,
                    item,
                    Path(item.output_path),
                    stop_event=stop_event,
                    on_progress=lambda progress, item_id=item.id: self._service.update_item_progress(
                        task_id, item_id, progress
                    ),
                    on_log=on_log,
                )
                self._service.complete_item(task_id, item.id, output)
            except Exception as exc:
                if stop_event.is_set():
                    self._service.pause_task(task_id)
                    break
                self._service.fail_item(task_id, item.id, str(exc))
                break
        return self._service.finish_parent_if_handled(task_id)
