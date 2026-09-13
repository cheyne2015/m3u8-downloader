"""父任务应用服务。"""

import os
import re
import shutil
import threading
import uuid
import hashlib
import json
import unicodedata
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
_PAGE_TITLE_SEPARATOR = re.compile(r"[-|｜]")


def _automatic_task_name(page_title: str) -> str:
    prefix = _PAGE_TITLE_SEPARATOR.split(page_title, maxsplit=1)[0].strip()
    return prefix or page_title


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
            duplicates = [
                task.id
                for task in self._repository.list_tasks_by_source_urls(addresses)
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

    def latest_log_id(self) -> int:
        return self._repository.latest_log_id()

    def list_logs(
        self, *, task_id: str | None = None, level: str | None = None,
        after_id: int | None = None,
    ):
        return self._repository.list_logs(
            task_id=task_id, level=level, after_id=after_id
        )

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
                    segment_count=candidate.segment_count or current.segment_count,
                    bandwidth=candidate.bandwidth or current.bandwidth,
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
                segment_count=candidate.segment_count,
                bandwidth=candidate.bandwidth,
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
            name=(task.name if task.name_edited else _automatic_task_name(title)),
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
        desired_paths = planner.plan(
            task, [replace(item, output_path="") for item in items]
        )
        multiple = len(desired_paths) > 1
        save_root = Path(task.save_directory).resolve()
        updated = []
        moved: list[tuple[Path, Path]] = []
        try:
            for item in items:
                target = desired_paths.get(item.id)
                if target is None:
                    updated.append(item)
                    continue
                current = Path(item.output_path) if item.output_path else None
                output_path = current or target
                should_replan = current is None or not current.exists()
                should_migrate = (
                    multiple
                    and current is not None
                    and current.is_file()
                    and current.resolve().parent == save_root
                    and item.status is not ItemStatus.DOWNLOADING
                )
                if should_migrate:
                    target.parent.mkdir(parents=True, exist_ok=True)
                    os.replace(current, target)
                    moved.append((target, current))
                    output_path = target
                elif should_replan and item.status is not ItemStatus.DOWNLOADING:
                    output_path = target
                updated.append(replace(item, output_path=str(output_path)))
            self._repository.save_items(updated)
        except Exception:
            for target, original in reversed(moved):
                if target.exists() and not original.exists():
                    original.parent.mkdir(parents=True, exist_ok=True)
                    os.replace(target, original)
            raise
        return updated

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

    def activate_same_duration_backup(
        self, task_id: str, failed_item_id: str,
    ) -> DownloadItem | None:
        """首选候选失败后，把同显示时长的下一候选接入同一个输出位置。"""
        items = self._repository.list_items(task_id)
        failed = next(item for item in items if item.id == failed_item_id)
        if not failed.duration_seconds or failed.duration_seconds <= 0:
            return None
        displayed_second = int(failed.duration_seconds)
        backups = sorted(
            (
                item for item in items
                if item.id != failed.id
                and item.status is ItemStatus.UNSELECTED
                and item.duration_backup
                and item.duration_seconds is not None
                and item.duration_seconds > 0
                and int(item.duration_seconds) == displayed_second
            ),
            key=lambda item: (item.estimated_bytes or 0, -item.output_index),
            reverse=True,
        )
        if not backups:
            return None
        backup = replace(
            backups[0],
            valid=True,
            status=ItemStatus.WAITING,
            output_path=failed.output_path,
            duration_backup=False,
        )
        failed = replace(
            failed,
            valid=False,
            status=ItemStatus.UNSELECTED,
            output_path="",
            speed_bps=0.0,
            eta_seconds=0.0,
            duration_backup=False,
        )
        self._repository.save_items([failed, backup])
        task = replace(
            self._repository.get_task(task_id),
            download_status=DownloadStatus.WAITING,
            last_error="",
            updated_at=self._clock(),
        )
        self._repository.save_many([task])
        return backup

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
        task = self._repository.get_task(task_id)
        if (
            task.source_kind is SourceKind.WEB_PAGE
            and task.extraction_status is ExtractionStatus.NOT_REQUIRED
            and re.search(r"\b(?:400|401|403|404|410)\b", str(error))
        ):
            self.add_log(
                task_id, "警告", "提取",
                "上次媒体地址已失效，正在自动重新提取原网页",
            )
            return self._restart_legacy_web_redownload(task_id)
        item = replace(self.get_item(task_id, item_id), status=ItemStatus.FAILED)
        self._repository.save_items([item])
        task = replace(
            task,
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
        task = self._repository.get_task(task_id)
        if (
            task.source_kind is SourceKind.WEB_PAGE
            and task.extraction_status is ExtractionStatus.NOT_REQUIRED
        ):
            return self._restart_legacy_web_redownload(task_id)
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
        task = self._repository.get_task(task_id)
        if (
            task.source_kind is SourceKind.WEB_PAGE
            and task.extraction_status is ExtractionStatus.NOT_REQUIRED
        ):
            return self._restart_legacy_web_redownload(task_id)
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
        """复制父任务中曾选择的下载项，生成一个快速重新下载任务。"""
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

    def reextract_task(self, task_id: str) -> Task:
        """从原网页创建独立的重新提取任务，保留原任务及磁盘文件。"""
        source_task = self._repository.get_task(task_id)
        if source_task.source_kind is SourceKind.DIRECT_M3U8:
            raise ValueError("直接 m3u8 任务没有原网页可重新提取")
        return self._create_web_redownload(source_task)

    def _create_web_redownload(self, source_task: Task) -> Task:
        """创建重新提取原网页的新任务，避免复用可能过期的签名地址。"""
        now = self._clock()
        task = replace(
            source_task,
            id=self._id_factory(),
            extraction_status=ExtractionStatus.WAITING,
            download_status=DownloadStatus.NOT_READY,
            queue_position=self._repository.next_queue_position(),
            created_at=now,
            updated_at=now,
            last_error="",
            retry_count=0,
            retry_at=None,
            completed_at=None,
            selection_mode=SelectionMode.AUTO,
            settings=replace(
                source_task.settings, allow_content_duplicate=True,
            ),
        )
        self._repository.add_bundle([task], [])
        return task

    def _restart_legacy_web_redownload(self, task_id: str) -> Task:
        """把旧地址失败的网页重下任务转回网页重新提取流程。"""
        task = self.retry_extraction(task_id)
        task = replace(
            task,
            selection_mode=SelectionMode.AUTO,
            settings=replace(task.settings, allow_content_duplicate=True),
        )
        self._repository.save_many([task])
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
        return self.move_tasks([task_id], direction)

    def move_tasks(self, task_ids: List[str], direction: str) -> List[Task]:
        """批量调整未完成父任务，并保持所选任务之间原有的相对顺序。"""
        all_tasks = self._repository.list_tasks()
        completed = [
            task for task in all_tasks
            if task.download_status is DownloadStatus.COMPLETED
        ]
        tasks = [
            task for task in all_tasks
            if task.download_status is not DownloadStatus.COMPLETED
        ]
        selected_ids = set(task_ids)
        if not selected_ids:
            return all_tasks
        existing_ids = {task.id for task in tasks}
        missing = selected_ids - existing_ids
        if missing:
            raise KeyError(next(iter(missing)))
        selected = [task for task in tasks if task.id in selected_ids]
        remaining = [task for task in tasks if task.id not in selected_ids]
        if direction == "front":
            tasks = selected + remaining
        elif direction == "back":
            tasks = remaining + selected
        elif direction == "up":
            for index in range(1, len(tasks)):
                if tasks[index].id in selected_ids and tasks[index - 1].id not in selected_ids:
                    tasks[index - 1], tasks[index] = tasks[index], tasks[index - 1]
        elif direction == "down":
            for index in range(len(tasks) - 2, -1, -1):
                if tasks[index].id in selected_ids and tasks[index + 1].id not in selected_ids:
                    tasks[index], tasks[index + 1] = tasks[index + 1], tasks[index]
        else:
            raise ValueError("未知队列移动方式")
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

    def record_content_identity(self, task_id: str) -> str:
        """保存网页标题与媒体结构组成的保守内容指纹。"""
        task = self._repository.get_task(task_id)
        identity_title = _automatic_task_name(task.original_title or task.name)
        normalized_title = "".join(
            character.casefold()
            for character in unicodedata.normalize("NFKC", identity_title)
            if character.isalnum()
        )
        media = sorted(
            (
                round(float(item.duration_seconds), 1),
                int(item.segment_count),
                int(item.bandwidth),
            )
            for item in self._repository.list_items(task_id)
            if item.valid
            and item.duration_seconds is not None
            and item.duration_seconds > 0
            and item.segment_count > 0
        )
        if not normalized_title or not media:
            return ""
        payload = json.dumps(
            {"title": normalized_title, "media": media},
            ensure_ascii=False,
            separators=(",", ":"),
        )
        fingerprint = hashlib.sha256(payload.encode("utf-8")).hexdigest()
        self._repository.save_content_identity(task_id, fingerprint, self._clock())
        return fingerprint

    def find_content_duplicate(self, task_id: str) -> Task | None:
        fingerprint = self._repository.get_content_identity(task_id)
        if not fingerprint:
            return None
        return self._repository.find_task_by_content_identity(
            fingerprint, excluding_task_id=task_id,
        )

    def hold_for_content_duplicate(self, task_id: str, existing_task_id: str) -> Task:
        items = self._repository.list_items(task_id)
        self._repository.save_items([
            replace(item, status=ItemStatus.UNSELECTED)
            if item.status is ItemStatus.WAITING else item
            for item in items
        ])
        task = replace(
            self._repository.get_task(task_id),
            download_status=DownloadStatus.PENDING_SELECTION,
            last_error=f"疑似重复内容:{existing_task_id}",
            updated_at=self._clock(),
        )
        self._repository.save_many([task])
        return task

    def allow_content_duplicate(self, task_id: str) -> Task:
        task = self._repository.get_task(task_id)
        if not task.last_error.startswith("疑似重复内容:"):
            return task
        items = self._repository.list_items(task_id)
        selected = [
            replace(item, status=ItemStatus.WAITING)
            if item.valid and item.status is ItemStatus.UNSELECTED else item
            for item in items
        ]
        self._repository.save_items(selected)
        task = replace(
            task,
            download_status=(
                DownloadStatus.WAITING
                if any(item.status is ItemStatus.WAITING for item in selected)
                else DownloadStatus.PENDING_SELECTION
            ),
            last_error="",
            updated_at=self._clock(),
        )
        self._repository.save_many([task])
        return task

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
            replace(
                item, valid=False, status=ItemStatus.UNSELECTED,
                duration_backup=True,
            )
            if (
                item.valid
                and item.status in {ItemStatus.UNSELECTED, ItemStatus.WAITING}
                and item.duration_seconds is not None
                and item.duration_seconds > 0
                and max(0, int(item.duration_seconds)) in grouped_seconds
                and item.id not in retained_ids
            )
            else replace(item, duration_backup=False)
            if item.id in retained_ids and item.duration_backup
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
