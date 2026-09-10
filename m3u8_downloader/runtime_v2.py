"""新版应用的数据目录与服务装配。"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from .tasking import SQLiteTaskRepository, TaskService


def resolve_data_directory(executable_path, local_app_data) -> Path:
    executable = Path(executable_path).resolve()
    if (executable.parent / "portable.flag").is_file():
        return executable.parent / "data"
    return Path(local_app_data).resolve() / "m3u8-downloader"


def current_data_directory() -> Path:
    if getattr(sys, "frozen", False):
        executable = Path(sys.executable)
    else:
        executable = Path(__file__).resolve().parent.parent / "m3u8-dl.exe"
    local = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local")
    directory = resolve_data_directory(executable, local)
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "logs").mkdir(exist_ok=True)
    return directory


def build_task_service() -> TaskService:
    database = current_data_directory() / "tasks-v2.db"
    service = TaskService(SQLiteTaskRepository(database))
    service.restore_tasks_after_restart()
    service.purge_expired_logs()
    return service
