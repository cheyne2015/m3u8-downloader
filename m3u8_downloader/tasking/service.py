"""父任务应用服务。"""

import os
import re
import shutil
import threading
import uuid
from collections import defaultdict
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
        self._task_locks = defaultdict(threading.RLock)

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
        direct_items = [DownloadItem(
            id=self._item_id_factory(),
            task_id=task.id,
            source_url=task.source_url,
            label=task.name,
            output_index=1,
            status=ItemStatus.WAITING,
        ) for task in tasks if task.source_kind is SourceKind.DIRECT_M3U8]
        self._repository.add_bundle(tasks, direct_items)
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
        with self._task_locks[task_id]:
            task = self._repository.get_task(task_id)
            if task.extraction_status in {
                ExtractionStatus.COMPLETED,
                ExtractionStatus.FAILED,
                ExtractionStatus.NOT_REQUIRED,
            }:
                return []
            return self._add_candidates_locked(task_id, candidates)

    def _add_candidates_locked(
        self, task_id: str, candidates: List[Candidate]
    ) -> List[DownloadItem]:
        existing = self._repository.list_items(task_id)
        known_by_url = {item.source_url: item for item in existing}
        known_urls = set(known_by_url)
        next_index = max((item.output_index for item in existing), default=0) + 1
        added: List[DownloadItem] = []
        for candidate in candidates:
            url = candidate.url.strip()
            if not url:
                continue
            if url in known_urls:
                current = known_by_url[url]
                improved = replace(
                    current,
                    label=candidate.label or current.label,
                    estimated_bytes=(
                        candidate.estimated_bytes
                        if candidate.estimated_bytes is not None else current.estimated_bytes
                    ),
                    duration_seconds=(
                        candidate.duration_seconds
                        if candidate.duration_seconds is not None else current.duration_seconds
                    ),
                    valid=current.valid or candidate.valid,
                )
                if improved != current:
                    self._repository.save_items([improved])
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
        self._clear_unstarted_output_paths(task_id)
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
        self._clear_unstarted_output_paths(task_id)
        return task

    def _clear_unstarted_output_paths(self, task_id: str) -> None:
        items = self._repository.list_items(task_id)
        changed = [
            replace(item, output_path="")
            for item in items
            if item.output_path
            and item.downloaded_bytes == 0
            and item.status in {ItemStatus.UNSELECTED, ItemStatus.WAITING}
        ]
        self._repository.save_items(changed)

    def update_task_settings(self, task_id: str, settings) -> Task:
        task = replace(
            self._repository.get_task(task_id),
            settings=settings,
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

    def retry_extraction(self, task_id: str) -> Task:
        task = self._repository.get_task(task_id)
        if task.source_kind is SourceKind.DIRECT_M3U8:
            raise ValueError("直接 m3u8 任务不需要提取")
        self._repository.delete_items(task_id)
        task = replace(
            task,
            extraction_status=ExtractionStatus.WAITING,
            download_status=DownloadStatus.NOT_READY,
            last_error="",
            retry_count=0,
            retry_at=None,
            completed_at=None,
            updated_at=self._clock(),
        )
        self._repository.save_many([task])
        return task

    def stop_extraction(self, task_id: str) -> Task:
        """用户主动停止提取，并把当前候选立即视为最终结果。"""
        task = self._repository.get_task(task_id)
        if task.extraction_status in {
            ExtractionStatus.NOT_REQUIRED,
            ExtractionStatus.COMPLETED,
            ExtractionStatus.FAILED,
        }:
            return task
        self.finish_extraction(task_id)
        return self.finish_parent_if_handled(task_id)

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
            downloaded_bytes=max(0, int(progress.get(
                "downloaded", progress.get("total_bytes", item.downloaded_bytes)
            ))),
            total_bytes=max(0, int(progress.get("byte_total", item.total_bytes))),
            progress_percent=max(0.0, min(100.0, float(progress.get(
                "percent", item.progress_percent
            )))),
            speed_bps=max(0.0, float(progress.get("speed", item.speed_bps))),
            eta_seconds=max(0.0, float(progress.get("eta", item.eta_seconds))),
        )
        self._repository.update_item_progress(item)

    def complete_item(self, task_id: str, item_id: str, output_path) -> DownloadItem:
        size = os.path.getsize(output_path) if os.path.isfile(output_path) else 0
        item = replace(
            self.get_item(task_id, item_id),
            status=ItemStatus.COMPLETED,
            output_path=str(output_path),
            downloaded_bytes=size,
            total_bytes=size,
            progress_percent=100.0,
            speed_bps=0.0,
            eta_seconds=0.0,
        )
        self._repository.save_items([item])
        task = self._repository.get_task(task_id)
        if task.retry_count or task.retry_at is not None or task.last_error:
            self._repository.save_many([replace(
                task, retry_count=0, retry_at=None, last_error="", updated_at=self._clock()
            )])
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

    def retry_item(self, task_id: str, item_id: str) -> Task:
        item = self.get_item(task_id, item_id)
        if item.status not in {ItemStatus.FAILED, ItemStatus.SKIPPED}:
            raise ValueError("只有失败或已跳过的下载项可以重试")
        self._repository.save_items([replace(
            item,
            status=ItemStatus.WAITING,
            downloaded_bytes=0,
            total_bytes=0,
            progress_percent=0.0,
            speed_bps=0.0,
            eta_seconds=0.0,
        )])
        task = replace(
            self._repository.get_task(task_id),
            download_status=DownloadStatus.WAITING,
            last_error="",
            retry_count=0,
            retry_at=None,
            updated_at=self._clock(),
        )
        self._repository.save_many([task])
        return task

    def retry_failed_items(self, task_id: str) -> Task:
        failed = [
            item for item in self._repository.list_items(task_id)
            if item.status is ItemStatus.FAILED
        ]
        if not failed:
            return self._repository.get_task(task_id)
        self._repository.save_items([
            replace(
                item, status=ItemStatus.WAITING, downloaded_bytes=0, total_bytes=0,
                progress_percent=0.0, speed_bps=0.0, eta_seconds=0.0,
            )
            for item in failed
        ])
        task = replace(
            self._repository.get_task(task_id),
            download_status=DownloadStatus.WAITING,
            last_error="",
            retry_count=0,
            retry_at=None,
            updated_at=self._clock(),
        )
        self._repository.save_many([task])
        return task

    def schedule_item_retry(self, task_id: str, item_id: str, error: str) -> Task:
        task = self._repository.get_task(task_id)
        if task.retry_count >= task.settings.task_retries:
            return self.fail_item(task_id, item_id, error)
        item = replace(self.get_item(task_id, item_id), status=ItemStatus.RETRY_WAIT)
        self._repository.save_items([item])
        task = replace(
            task,
            download_status=DownloadStatus.RETRY_WAIT,
            last_error=str(error),
            retry_count=task.retry_count + 1,
            retry_at=self._clock() + timedelta(seconds=task.settings.retry_delay_seconds),
            updated_at=self._clock(),
        )
        self._repository.save_many([task])
        return task

    def promote_due_retries(self) -> List[Task]:
        now = self._clock()
        promoted: List[Task] = []
        for task in self._repository.list_tasks():
            if (
                task.download_status is not DownloadStatus.RETRY_WAIT
                or task.retry_at is None
                or task.retry_at > now
            ):
                continue
            items = self._repository.list_items(task.id)
            self._repository.save_items([
                replace(item, status=ItemStatus.WAITING)
                if item.status is ItemStatus.RETRY_WAIT else item
                for item in items
            ])
            task = replace(
                task,
                download_status=DownloadStatus.WAITING,
                retry_at=None,
                updated_at=now,
            )
            self._repository.save_many([task])
            promoted.append(task)
        return promoted

    def redownload_item(self, task_id: str, item_id: str) -> Task:
        """为一个下载项创建独立父任务，原任务及文件保持不变。"""
        source_task = self._repository.get_task(task_id)
        source_item = self.get_item(task_id, item_id)
        now = self._clock()
        task = Task(
            id=self._id_factory(),
            source_url=source_item.source_url,
            source_kind=SourceKind.DIRECT_M3U8,
            name=source_task.name,
            save_directory=source_task.save_directory,
            extraction_status=ExtractionStatus.NOT_REQUIRED,
            download_status=DownloadStatus.WAITING,
            queue_position=self._repository.next_queue_position(),
            created_at=now,
            updated_at=now,
            original_title=source_task.original_title,
            name_edited=True,
            settings=source_task.settings,
        )
        item = DownloadItem(
            id=self._item_id_factory(),
            task_id=task.id,
            source_url=source_item.source_url,
            label=source_item.label,
            output_index=1,
            status=ItemStatus.WAITING,
            estimated_bytes=source_item.estimated_bytes,
            duration_seconds=source_item.duration_seconds,
            valid=source_item.valid,
        )
        self._repository.add_bundle([task], [item])
        return task

    def redownload_task(self, task_id: str) -> Task:
        """复制父任务中曾选择的下载项，生成一个新的待下载父任务。"""
        source_task = self._repository.get_task(task_id)
        source_items = [
            item for item in self._repository.list_items(task_id)
            if item.status is not ItemStatus.UNSELECTED
        ]
        if not source_items:
            raise ValueError("任务中没有可重新下载的项目")
        now = self._clock()
        task = replace(
            source_task,
            id=self._id_factory(),
            extraction_status=ExtractionStatus.NOT_REQUIRED,
            download_status=DownloadStatus.WAITING,
            queue_position=self._repository.next_queue_position(),
            created_at=now,
            updated_at=now,
            last_error="",
            selection_mode=SelectionMode.MANUAL,
            completed_at=None,
        )
        items = [replace(
            item,
            id=self._item_id_factory(),
            task_id=task.id,
            status=ItemStatus.WAITING,
            output_path="",
            downloaded_bytes=0,
            total_bytes=0,
            progress_percent=0.0,
            speed_bps=0.0,
            eta_seconds=0.0,
        ) for item in source_items]
        self._repository.add_bundle([task], items)
        return task

    def rename_output_file(self, task_id: str, item_id: str, name: str) -> DownloadItem:
        item = self.get_item(task_id, item_id)
        if not item.output_path:
            raise ValueError("该下载项还没有磁盘文件")
        source = Path(item.output_path)
        if not source.is_file():
            raise FileNotFoundError("磁盘文件不存在")
        safe_name = _INVALID_FILE_CHARS.sub("_", name).strip(" ._")
        if not safe_name:
            raise ValueError("文件名不能为空")
        suffix = source.suffix or ".mp4"
        if safe_name.lower().endswith(suffix.lower()):
            safe_name = safe_name[:-len(suffix)].rstrip(" .")
        target = source.with_name(safe_name + suffix)
        if target != source and target.exists():
            index = 1
            while True:
                candidate = source.with_name(f"{safe_name} ({index}){suffix}")
                if not candidate.exists():
                    target = candidate
                    break
                index += 1
        source.rename(target)
        item = replace(item, output_path=str(target))
        self._repository.save_items([item])
        return item

    def relink_output_file(self, task_id: str, item_id: str, path) -> DownloadItem:
        target = Path(path)
        if not target.is_file():
            raise FileNotFoundError("选择的文件不存在")
        item = replace(
            self.get_item(task_id, item_id),
            output_path=str(target),
            downloaded_bytes=target.stat().st_size,
            total_bytes=target.stat().st_size,
            progress_percent=100.0,
        )
        self._repository.save_items([item])
        return item

    def skip_item(self, task_id: str, item_id: str) -> Task:
        item = self.get_item(task_id, item_id)
        if item.status is ItemStatus.COMPLETED:
            raise ValueError("已完成的下载项不能跳过")
        self._repository.save_items([replace(item, status=ItemStatus.SKIPPED)])
        return self.finish_parent_if_handled(task_id)

    def pause_item(self, task_id: str, item_id: str) -> Task:
        item = self.get_item(task_id, item_id)
        if item.status not in {ItemStatus.WAITING, ItemStatus.DOWNLOADING, ItemStatus.RETRY_WAIT}:
            raise ValueError("该下载项当前不能暂停")
        self._repository.save_items([replace(item, status=ItemStatus.PAUSED)])
        items = self._repository.list_items(task_id)
        task = replace(
            self._repository.get_task(task_id),
            download_status=(
                DownloadStatus.WAITING
                if any(candidate.status is ItemStatus.WAITING for candidate in items)
                else DownloadStatus.PAUSED
            ),
            updated_at=self._clock(),
        )
        self._repository.save_many([task])
        return task

    def resume_item(self, task_id: str, item_id: str) -> Task:
        item = self.get_item(task_id, item_id)
        if item.status is not ItemStatus.PAUSED:
            raise ValueError("只有已暂停的下载项可以继续")
        self._repository.save_items([replace(item, status=ItemStatus.WAITING)])
        task = replace(
            self._repository.get_task(task_id),
            download_status=DownloadStatus.WAITING,
            updated_at=self._clock(),
        )
        self._repository.save_many([task])
        return task

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
        all_tasks = self._repository.list_tasks()
        completed = [
            task for task in all_tasks
            if task.download_status is DownloadStatus.COMPLETED
        ]
        tasks = [
            task for task in all_tasks
            if task.download_status is not DownloadStatus.COMPLETED
        ]
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
        reordered = tasks + completed
        reordered = [
            replace(item, queue_position=position, updated_at=now)
            for position, item in enumerate(reordered, start=1)
        ]
        self._repository.save_many(reordered)
        return reordered

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
        now = self._clock()
        task = replace(
            task,
            download_status=status,
            completed_at=(
                task.completed_at or now
                if status is DownloadStatus.COMPLETED else None
            ),
            updated_at=now,
        )
        self._repository.save_many([task])
        return task

    def select_items_for_download(self, task_id: str, item_ids: List[str]) -> Task:
        """由用户接管选择；已经开始或结束的下载项不会被改写。"""
        task = self._repository.get_task(task_id)
        selected = set(item_ids)
        selectable = {ItemStatus.UNSELECTED, ItemStatus.WAITING}
        items = self._keep_largest_candidate_per_duration(
            self._repository.list_items(task_id)
        )
        selected.intersection_update(item.id for item in items if item.valid)
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
        with self._task_locks[task_id]:
            return self._finish_extraction_locked(task_id)

    def _finish_extraction_locked(self, task_id: str) -> Task:
        task = self._repository.get_task(task_id)
        items = self._repository.list_items(task_id)
        items = self._keep_largest_candidate_per_duration(items)
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

    def _keep_largest_candidate_per_duration(self, items: List[DownloadItem]) -> List[DownloadItem]:
        """相同显示时长只保留体积最大的候选参与选择和阈值计算。"""
        best_by_second: dict[int, DownloadItem] = {}
        for item in items:
            if not item.valid or not item.duration_seconds or item.duration_seconds <= 0:
                continue
            displayed_second = max(0, int(item.duration_seconds))
            current = best_by_second.get(displayed_second)
            if current is None or (item.estimated_bytes or 0) > (current.estimated_bytes or 0):
                best_by_second[displayed_second] = item
        retained_ids = {item.id for item in best_by_second.values()}
        grouped_seconds = set(best_by_second)
        filtered = [
            replace(item, valid=False, status=ItemStatus.UNSELECTED)
            if (
                item.valid
                and item.status in {ItemStatus.UNSELECTED, ItemStatus.WAITING}
                and item.duration_seconds is not None
                and item.duration_seconds > 0
                and max(0, int(item.duration_seconds)) in grouped_seconds
                and item.id not in retained_ids
            )
            else item
            for item in items
        ]
        self._repository.save_items(filtered)
        return filtered

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
