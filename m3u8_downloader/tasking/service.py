"""父任务应用服务。"""

import os
import re
import uuid
from dataclasses import replace
from datetime import datetime
from pathlib import PurePosixPath
from typing import Callable, List
from urllib.parse import unquote, urlsplit, urlunsplit

from .models import (
    Candidate,
    CreateTaskRequest,
    DownloadItem,
    DownloadStatus,
    ExtractionStatus,
    ItemStatus,
    SelectionMode,
    SourceKind,
    Task,
)
from .repository import SQLiteTaskRepository


_GENERIC_PLAYLIST_NAMES = {"index", "playlist", "master", "media"}
_INVALID_FILE_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


class TaskService:
    """接收用户命令并通过仓库提交完整的任务变化。"""

    def __init__(
        self,
        repository: SQLiteTaskRepository,
        *,
        clock: Callable[[], datetime] = datetime.now,
        id_factory: Callable[[], str] = lambda: str(uuid.uuid4()),
    ) -> None:
        self._repository = repository
        self._clock = clock
        self._id_factory = id_factory

    def create_tasks(self, request: CreateTaskRequest) -> List[Task]:
        addresses = self._normalize_addresses(request.addresses)
        save_directory = os.path.abspath(os.path.expanduser(request.save_directory))
        position = self._repository.next_queue_position()
        now = self._clock()
        tasks: List[Task] = []
        for offset, address in enumerate(addresses):
            source_kind = self._source_kind(address)
            direct = source_kind is SourceKind.DIRECT_M3U8
            tasks.append(Task(
                id=self._id_factory(),
                source_url=address,
                source_kind=source_kind,
                name=self._initial_name(address, source_kind),
                save_directory=save_directory,
                extraction_status=(
                    ExtractionStatus.NOT_REQUIRED if direct else ExtractionStatus.WAITING
                ),
                download_status=(DownloadStatus.WAITING if direct else DownloadStatus.NOT_READY),
                queue_position=position + offset,
                created_at=now,
                updated_at=now,
                settings=request.settings,
            ))
        self._repository.add_many(tasks)
        return tasks

    def restore_tasks_after_restart(self) -> List[Task]:
        """将中断时仍在执行的状态落盘为暂停，并返回恢复后的任务。"""
        tasks = self._repository.list_tasks()
        now = self._clock()
        restored: List[Task] = []
        changed: List[Task] = []
        active_download_states = {
            DownloadStatus.RUNNING,
            DownloadStatus.MERGING,
            DownloadStatus.RETRY_WAIT,
        }
        for task in tasks:
            extraction_status = task.extraction_status
            download_status = task.download_status
            if extraction_status is ExtractionStatus.RUNNING:
                extraction_status = ExtractionStatus.PAUSED
            if download_status in active_download_states:
                download_status = DownloadStatus.PAUSED
            if (extraction_status, download_status) != (
                task.extraction_status, task.download_status
            ):
                task = replace(
                    task,
                    extraction_status=extraction_status,
                    download_status=download_status,
                    updated_at=now,
                )
                changed.append(task)
            restored.append(task)
        self._repository.save_many(changed)
        return restored

    def add_candidates(self, task_id: str, candidates: List[Candidate]) -> List[DownloadItem]:
        """把流式发现的候选加入父任务；新候选默认不选择。"""
        existing = self._repository.list_items(task_id)
        known_urls = {item.source_url for item in existing}
        next_index = max((item.output_index for item in existing), default=0) + 1
        added: List[DownloadItem] = []
        for candidate in candidates:
            url = candidate.url.strip()
            if not url or url in known_urls:
                continue
            known_urls.add(url)
            added.append(DownloadItem(
                id=self._id_factory(), task_id=task_id, source_url=url,
                label=candidate.label, output_index=next_index,
                status=ItemStatus.UNSELECTED,
                estimated_bytes=candidate.estimated_bytes,
                duration_seconds=candidate.duration_seconds,
            ))
            next_index += 1
        self._repository.add_items(added)
        return added

    def start_extraction(self, task_id: str) -> Task:
        task = self._repository.get_task(task_id)
        task = replace(
            task,
            extraction_status=ExtractionStatus.RUNNING,
            updated_at=self._clock(),
        )
        self._repository.save_many([task])
        return task

    def select_items_for_download(self, task_id: str, item_ids: List[str]) -> Task:
        """由用户接管选择；已经开始或结束的下载项不会被改写。"""
        task = self._repository.get_task(task_id)
        selected = set(item_ids)
        selectable = {ItemStatus.UNSELECTED, ItemStatus.WAITING}
        items = self._repository.list_items(task_id)
        updated_items = [
            replace(
                item,
                status=(ItemStatus.WAITING if item.id in selected else ItemStatus.UNSELECTED),
            ) if item.status in selectable else item
            for item in items
        ]
        self._repository.save_items(updated_items)
        has_selected = any(item.status is ItemStatus.WAITING for item in updated_items)
        task = replace(
            task,
            selection_mode=SelectionMode.MANUAL,
            download_status=(
                DownloadStatus.WAITING if has_selected else DownloadStatus.PENDING_SELECTION
            ),
            updated_at=self._clock(),
        )
        self._repository.save_many([task])
        return task

    def finish_extraction(self, task_id: str) -> Task:
        """结束提取，并按任务阈值决定自动下载或等待用户选择。"""
        task = self._repository.get_task(task_id)
        items = self._repository.list_items(task_id)
        if (
            task.selection_mode is SelectionMode.AUTO
            and items
            and len(items) <= task.settings.auto_download_threshold
        ):
            items = [replace(item, status=ItemStatus.WAITING) for item in items]
            download_status = DownloadStatus.WAITING
            self._repository.save_items(items)
        elif task.selection_mode is SelectionMode.AUTO:
            download_status = (
                DownloadStatus.PENDING_SELECTION if items else DownloadStatus.NOT_READY
            )
        else:
            download_status = task.download_status
        task = replace(
            task,
            extraction_status=ExtractionStatus.COMPLETED,
            download_status=download_status,
            updated_at=self._clock(),
        )
        self._repository.save_many([task])
        return task

    @staticmethod
    def _normalize_addresses(raw: str) -> List[str]:
        addresses: List[str] = []
        seen = set()
        for line in str(raw or "").splitlines():
            address = line.strip()
            if not address:
                continue
            if "://" not in address:
                address = "https://" + address
            parts = urlsplit(address)
            normalized = urlunsplit((parts.scheme.lower(), parts.netloc.lower(),
                                     parts.path, parts.query, parts.fragment))
            if normalized not in seen:
                seen.add(normalized)
                addresses.append(normalized)
        return addresses

    @staticmethod
    def _source_kind(address: str) -> SourceKind:
        return (
            SourceKind.DIRECT_M3U8
            if urlsplit(address).path.lower().endswith(".m3u8")
            else SourceKind.WEB_PAGE
        )

    @staticmethod
    def _initial_name(address: str, source_kind: SourceKind) -> str:
        if source_kind is SourceKind.WEB_PAGE:
            return "正在获取标题"
        path = PurePosixPath(unquote(urlsplit(address).path))
        name = path.stem.strip()
        if name.lower() in _GENERIC_PLAYLIST_NAMES or not name:
            name = path.parent.name.strip()
        name = _INVALID_FILE_CHARS.sub("_", name).strip(" ._")
        return name or "新建任务"
