"""父任务内部串行下载协调。"""

import threading
from pathlib import Path

from .models import ItemStatus
from .output import OutputPlanner


class DownloadCoordinator:
    def __init__(self, service, *, downloader, output_planner=None) -> None:
        self._service = service
        self._downloader = downloader
        self._planner = output_planner or OutputPlanner()

    def run_parent(self, task_id: str, stop_event=None, on_log=lambda _message: None):
        stop_event = stop_event or threading.Event()
        self._service.prepare_output_paths(task_id, self._planner)
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
                self._service.fail_item(task_id, item.id, str(exc))
                break
        return self._service.finish_parent_if_handled(task_id)

