"""父任务应用服务。"""

import os
import re
import shutil
import uuid
from dataclasses import replace
from datetime import datetime, timedelta
from pathlib import Path, PurePosixPath
from typing import Callable, List
from urllib.parse import unquote, urlsplit, urlunsplit

from .models import (
    AppSettings,
    Candidate,
    CreateTaskRequest,
    DeletionPreview,
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


class DuplicateSourceError(ValueError):
    def __init__(self, existing_task_ids) -> None:
        self.existing_task_ids = tuple(existing_task_ids)
        super().__init__("链接已存在")


class TaskService:
    """接收用户命令并通过仓库提交完整的任务变化。"""

    def __init__(
        self,
        repository: SQLiteTaskRepository,
        *,
        clock: Callable[[], datetime] = datetime.now,
        id_factory: Callable[[], str] = lambda: str(uuid.uuid4()),
        item_id_factory: Callable[[], str] = lambda: str(uuid.uuid4()),
    ) -> None:
        self._repository = repository
        self._clock = clock
        self._id_factory = id_factory
        self._item_id_factory = item_id_factory

    def create_tasks(
        self, request: CreateTaskRequest, *, allow_duplicates: bool = False
    ) -> List[Task]:
        addresses = self._normalize_addresses(request.addresses)
        if not allow_duplicates:
            wanted = set(addresses)
            duplicates = [
                task.id for task in self._repository.list_tasks()
                if task.source_url in wanted
            ]
            if duplicates:
                raise DuplicateSourceError(duplicates)
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
        direct_items = [DownloadItem(
            id=self._item_id_factory(),
            task_id=task.id,
            source_url=task.source_url,
            label=task.name,
            output_index=1,
            status=ItemStatus.WAITING,
        ) for task in tasks if task.source_kind is SourceKind.DIRECT_M3U8]
        self._repository.add_items(direct_items)
        return tasks

    def list_tasks(self) -> List[Task]:
        return self._repository.list_tasks()

    def list_items(self, task_id: str) -> List[DownloadItem]:
        return self._repository.list_items(task_id)

    def get_item(self, task_id: str, item_id: str) -> DownloadItem:
        for item in self._repository.list_items(task_id):
            if item.id == item_id:
                return item
        raise KeyError(item_id)

    def get_task(self, task_id: str) -> Task:
        return self._repository.get_task(task_id)

    def load_app_settings(self) -> AppSettings:
        return self._repository.load_app_settings()

    def save_app_settings(self, settings: AppSettings) -> None:
        self._repository.save_app_settings(settings)

    def add_log(self, task_id: str, level: str, category: str, message: str) -> None:
        self._repository.append_log(
            task_id, level, category, str(message), self._clock()
        )

    def list_logs(self, *, task_id: str | None = None, level: str | None = None):
        return self._repository.list_logs(task_id=task_id, level=level)

    def purge_expired_logs(self) -> int:
        days = self.load_app_settings().log_retention_days
        return self._repository.purge_logs_before(self._clock() - timedelta(days=days))

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
                id=self._item_id_factory(), task_id=task_id, source_url=url,
                label=candidate.label, output_index=next_index,
                status=ItemStatus.UNSELECTED,
                estimated_bytes=candidate.estimated_bytes,
                duration_seconds=candidate.duration_seconds,
                valid=candidate.valid,
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

    def apply_page_title(self, task_id: str, title: str) -> Task:
        title = title.strip()
        task = self._repository.get_task(task_id)
        if not title:
            return task
        task = replace(
            task,
            original_title=title,
            name=(task.name if task.name_edited else title),
            updated_at=self._clock(),
        )
        self._repository.save_many([task])
        return task

    def rename_task(self, task_id: str, name: str) -> Task:
        name = _INVALID_FILE_CHARS.sub("_", name).strip(" ._")
        if not name:
            raise ValueError("任务名称不能为空")
        task = replace(
            self._repository.get_task(task_id),
            name=name,
            name_edited=True,
            updated_at=self._clock(),
        )
        self._repository.save_many([task])
        return task

    def fail_extraction(self, task_id: str, error: str) -> Task:
        task = replace(
            self._repository.get_task(task_id),
            extraction_status=ExtractionStatus.FAILED,
            last_error=str(error),
            updated_at=self._clock(),
        )
        self._repository.save_many([task])
        return task

    def prepare_output_paths(self, task_id: str, planner) -> List[DownloadItem]:
        task = self._repository.get_task(task_id)
        items = self._repository.list_items(task_id)
        paths = planner.plan(task, items)
        items = [
            replace(item, output_path=str(paths[item.id])) if not item.output_path else item
            for item in items
        ]
        self._repository.save_items(items)
        return items

    def start_item(self, task_id: str, item_id: str) -> DownloadItem:
        item = replace(self.get_item(task_id, item_id), status=ItemStatus.DOWNLOADING)
        self._repository.save_items([item])
        task = replace(
            self._repository.get_task(task_id),
            download_status=DownloadStatus.RUNNING,
            updated_at=self._clock(),
        )
        self._repository.save_many([task])
        return item

    def update_item_progress(self, task_id: str, item_id: str, progress: dict) -> None:
        item = self.get_item(task_id, item_id)
        item = replace(
            item,
            downloaded_bytes=max(0, int(progress.get("downloaded", item.downloaded_bytes))),
            total_bytes=max(0, int(progress.get("total", item.total_bytes))),
        )
        self._repository.save_items([item])

    def complete_item(self, task_id: str, item_id: str, output_path) -> DownloadItem:
        size = os.path.getsize(output_path) if os.path.isfile(output_path) else 0
        item = replace(
            self.get_item(task_id, item_id),
            status=ItemStatus.COMPLETED,
            output_path=str(output_path),
            downloaded_bytes=size,
            total_bytes=size,
        )
        self._repository.save_items([item])
        return item

    def fail_item(self, task_id: str, item_id: str, error: str) -> Task:
        item = replace(self.get_item(task_id, item_id), status=ItemStatus.FAILED)
        self._repository.save_items([item])
        task = replace(
            self._repository.get_task(task_id),
            download_status=DownloadStatus.PARTIAL_FAILURE,
            last_error=str(error),
            updated_at=self._clock(),
        )
        self._repository.save_many([task])
        return task

    def skip_item(self, task_id: str, item_id: str) -> Task:
        item = self.get_item(task_id, item_id)
        if item.status is ItemStatus.COMPLETED:
            raise ValueError("已完成的下载项不能跳过")
        self._repository.save_items([replace(item, status=ItemStatus.SKIPPED)])
        return self.finish_parent_if_handled(task_id)

    def pause_task(self, task_id: str) -> Task:
        task = self._repository.get_task(task_id)
        items = self._repository.list_items(task_id)
        pausable = {ItemStatus.WAITING, ItemStatus.DOWNLOADING, ItemStatus.RETRY_WAIT}
        items = [
            replace(item, status=ItemStatus.PAUSED) if item.status in pausable else item
            for item in items
        ]
        self._repository.save_items(items)
        extraction_status = (
            ExtractionStatus.PAUSED
            if task.extraction_status in {ExtractionStatus.WAITING, ExtractionStatus.RUNNING}
            else task.extraction_status
        )
        has_unfinished_download = any(item.status is ItemStatus.PAUSED for item in items)
        task = replace(
            task,
            extraction_status=extraction_status,
            download_status=(DownloadStatus.PAUSED if has_unfinished_download else task.download_status),
            updated_at=self._clock(),
        )
        self._repository.save_many([task])
        return task

    def resume_task(self, task_id: str) -> Task:
        task = self._repository.get_task(task_id)
        items = self._repository.list_items(task_id)
        items = [
            replace(item, status=ItemStatus.WAITING)
            if item.status is ItemStatus.PAUSED else item
            for item in items
        ]
        self._repository.save_items(items)
        extraction_status = (
            ExtractionStatus.WAITING
            if task.extraction_status is ExtractionStatus.PAUSED
            else task.extraction_status
        )
        has_waiting = any(item.status is ItemStatus.WAITING for item in items)
        download_status = DownloadStatus.WAITING if has_waiting else task.download_status
        task = replace(
            task,
            extraction_status=extraction_status,
            download_status=download_status,
            updated_at=self._clock(),
        )
        self._repository.save_many([task])
        return task

    def delete_task(self, task_id: str, *, delete_outputs: bool) -> DeletionPreview:
        preview = self.preview_deletion(task_id)
        task = self._repository.get_task(task_id)
        for path in preview.output_files:
            partial = Path(str(path) + ".part")
            if partial.is_file():
                partial.unlink()
            if delete_outputs and path.is_file():
                path.unlink()
        cache = Path(task.save_directory) / ".m3u8-cache" / task.id
        if cache.is_dir() and cache.parent.name == ".m3u8-cache":
            shutil.rmtree(cache)
        self._repository.delete_task(task_id)
        return preview

    def preview_deletion(self, task_id: str) -> DeletionPreview:
        output_files = tuple(
            Path(item.output_path)
            for item in self._repository.list_items(task_id)
            if item.output_path
        )
        total_bytes = sum(path.stat().st_size for path in output_files if path.is_file())
        return DeletionPreview(task_id, output_files, total_bytes)

    def pause_all(self) -> None:
        for task in self._repository.list_tasks():
            if task.download_status is not DownloadStatus.COMPLETED:
                self.pause_task(task.id)

    def resume_all(self) -> None:
        for task in self._repository.list_tasks():
            if (
                task.extraction_status is ExtractionStatus.PAUSED
                or task.download_status is DownloadStatus.PAUSED
            ):
                self.resume_task(task.id)

    def block_for_space(self, task_id: str, free_bytes: int, required_bytes: int) -> Task:
        self.pause_task(task_id)
        task = replace(
            self._repository.get_task(task_id),
            last_error=f"磁盘空间不足：可用 {free_bytes} 字节，需要 {required_bytes} 字节",
            updated_at=self._clock(),
        )
        self._repository.save_many([task])
        return task

    def move_task(self, task_id: str, direction: str) -> List[Task]:
        tasks = self._repository.list_tasks()
        index = next((i for i, task in enumerate(tasks) if task.id == task_id), None)
        if index is None:
            raise KeyError(task_id)
        task = tasks.pop(index)
        if direction == "front":
            target = 0
        elif direction == "back":
            target = len(tasks)
        elif direction == "up":
            target = max(0, index - 1)
        elif direction == "down":
            target = min(len(tasks), index + 1)
        else:
            raise ValueError("未知队列移动方式")
        tasks.insert(target, task)
        now = self._clock()
        tasks = [
            replace(item, queue_position=position, updated_at=now)
            for position, item in enumerate(tasks, start=1)
        ]
        self._repository.save_many(tasks)
        return tasks

    def finish_parent_if_handled(self, task_id: str) -> Task:
        task = self._repository.get_task(task_id)
        selected = [
            item for item in self._repository.list_items(task_id)
            if item.status is not ItemStatus.UNSELECTED
        ]
        if any(item.status is ItemStatus.FAILED for item in selected):
            status = DownloadStatus.PARTIAL_FAILURE
        elif selected and all(
            item.status in {ItemStatus.COMPLETED, ItemStatus.SKIPPED} for item in selected
        ):
            status = (
                DownloadStatus.COMPLETED
                if task.extraction_status in {
                    ExtractionStatus.COMPLETED, ExtractionStatus.NOT_REQUIRED,
                    ExtractionStatus.FAILED,
                }
                else DownloadStatus.RUNNING
            )
        else:
            status = task.download_status
        task = replace(task, download_status=status, updated_at=self._clock())
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
        valid_items = [item for item in items if item.valid]
        if (
            task.selection_mode is SelectionMode.AUTO
            and valid_items
            and len(valid_items) <= task.settings.auto_download_threshold
        ):
            items = [
                replace(item, status=ItemStatus.WAITING)
                if item.valid else item
                for item in items
            ]
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
