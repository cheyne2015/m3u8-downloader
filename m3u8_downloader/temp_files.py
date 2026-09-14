"""扫描并安全清理下载器自己创建的分片缓存。"""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
import re
import shutil
import os
from typing import Callable, Iterable, Mapping


_JOB_NAME = re.compile(r"^job-[0-9a-f]{16}-([0-9a-f]{8})$", re.IGNORECASE)
_FINISHED_ITEM_STATES = {"completed", "skipped"}


@dataclass(frozen=True)
class TempScan:
    total_bytes: int = 0
    cleanable_bytes: int = 0
    protected_bytes: int = 0
    file_count: int = 0
    cleanable_jobs: tuple[Path, ...] = ()


@dataclass(frozen=True)
class TempCleanupResult:
    removed_bytes: int = 0
    removed_jobs: int = 0
    failed_jobs: int = 0


def _output_suffix(path: str) -> str:
    absolute = str(Path(path).resolve()).lower()
    return sha256(absolute.encode("utf-8")).hexdigest()[:8]


def _directory_size(path: Path) -> tuple[int, int]:
    size = 0
    count = 0
    try:
        entries = list(path.iterdir())
    except OSError:
        return 0, 0
    for entry in entries:
        try:
            if entry.is_symlink():
                continue
            if entry.is_dir():
                child_size, child_count = _directory_size(entry)
                size += child_size
                count += child_count
            elif entry.is_file():
                size += entry.stat().st_size
                count += 1
        except OSError:
            continue
    return size, count


class TempFileManager:
    """只识别 ``.tmp/job-指纹-输出指纹``，避免误删用户文件。"""

    def __init__(self, *, active_job_paths: Callable[[], set[str]] | None = None) -> None:
        if active_job_paths is None:
            from .downloader import active_cache_job_paths
            active_job_paths = active_cache_job_paths
        self._active_job_paths = active_job_paths

    @staticmethod
    def _task_items(task, items_by_task: Mapping) -> Iterable:
        key = getattr(task, "id", id(task))
        return items_by_task.get(key, ())

    def scan(self, tasks: Iterable, items_by_task: Mapping) -> TempScan:
        tasks = list(tasks)
        protected_suffixes: set[str] = set()
        save_roots: set[Path] = set()
        explicit_temp_roots: set[Path] = set()

        for task in tasks:
            save_root = Path(task.save_directory).resolve()
            save_roots.add(save_root)
            explicit_temp_roots.add(save_root / ".tmp")
            for item in self._task_items(task, items_by_task):
                output_path = str(getattr(item, "output_path", "") or "")
                if not output_path:
                    continue
                output = Path(output_path).resolve()
                explicit_temp_roots.add(output.parent / ".tmp")
                state = getattr(getattr(item, "status", None), "value", "")
                if state not in _FINISHED_ITEM_STATES:
                    protected_suffixes.add(_output_suffix(output_path))

        temp_roots = set(explicit_temp_roots)
        for save_root in save_roots:
            try:
                for child in save_root.iterdir():
                    if child.is_dir() and not child.is_symlink():
                        temp_roots.add(child / ".tmp")
            except OSError:
                continue

        total = cleanable = protected = files = 0
        cleanable_jobs: list[Path] = []
        for temp_root in sorted(temp_roots, key=str):
            if not temp_root.is_dir() or temp_root.is_symlink():
                continue
            try:
                jobs = list(temp_root.iterdir())
            except OSError:
                continue
            for job in jobs:
                match = _JOB_NAME.fullmatch(job.name)
                if not match or not job.is_dir() or job.is_symlink():
                    continue
                size, count = _directory_size(job)
                total += size
                files += count
                if match.group(1).lower() in protected_suffixes:
                    protected += size
                else:
                    cleanable += size
                    cleanable_jobs.append(job)

        return TempScan(
            total_bytes=total,
            cleanable_bytes=cleanable,
            protected_bytes=protected,
            file_count=files,
            cleanable_jobs=tuple(sorted(cleanable_jobs, key=str)),
        )

    def cleanup(self, scan: TempScan) -> TempCleanupResult:
        removed_bytes = removed_jobs = failed_jobs = 0
        active_jobs = self._active_job_paths()
        for job in scan.cleanable_jobs:
            if (
                not _JOB_NAME.fullmatch(job.name)
                or job.parent.name != ".tmp"
                or job.is_symlink()
            ):
                failed_jobs += 1
                continue
            if os.path.normcase(os.path.abspath(job)) in active_jobs:
                continue
            size, _count = _directory_size(job)
            try:
                shutil.rmtree(job)
                removed_bytes += size
                removed_jobs += 1
                try:
                    job.parent.rmdir()
                except OSError:
                    pass
            except OSError:
                failed_jobs += 1
        return TempCleanupResult(removed_bytes, removed_jobs, failed_jobs)
