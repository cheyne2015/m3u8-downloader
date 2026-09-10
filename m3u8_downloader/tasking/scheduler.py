"""父任务并发调度决策。"""

from dataclasses import dataclass
from typing import Iterable

from .models import DownloadStatus, ExtractionStatus, Task


@dataclass(frozen=True)
class SchedulePlan:
    start_extraction: tuple[str, ...] = ()
    start_download: tuple[str, ...] = ()


class TaskScheduler:
    """根据持久化状态生成本轮启动计划，不直接执行网络操作。"""

    def plan(
        self,
        tasks: Iterable[Task],
        *,
        download_limit: int,
        extraction_limit: int,
    ) -> SchedulePlan:
        ordered = sorted(tasks, key=lambda task: (task.queue_position, task.created_at, task.id))
        active_extraction = sum(
            task.extraction_status is ExtractionStatus.RUNNING for task in ordered
        )
        active_download_states = {DownloadStatus.RUNNING, DownloadStatus.MERGING}
        active_download = sum(
            task.download_status in active_download_states for task in ordered
        )
        extraction_slots = max(0, extraction_limit - active_extraction)
        download_slots = max(0, download_limit - active_download)
        extraction = tuple(
            task.id for task in ordered
            if task.extraction_status is ExtractionStatus.WAITING
        )[:extraction_slots]
        download = tuple(
            task.id for task in ordered
            if task.download_status is DownloadStatus.WAITING
        )[:download_slots]
        return SchedulePlan(start_extraction=extraction, start_download=download)

