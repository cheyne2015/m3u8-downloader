"""新版父子任务管理公共接口。"""

from .models import (
    AppSettings,
    Candidate,
    CreateTaskRequest,
    DownloadItem,
    DownloadStatus,
    ExtractionStatus,
    ItemStatus,
    SelectionMode,
    SourceKind,
    Task,
    TaskSettings,
)
from .repository import SQLiteTaskRepository
from .scheduler import SchedulePlan, TaskScheduler
from .service import TaskService

__all__ = [
    "AppSettings",
    "Candidate",
    "CreateTaskRequest",
    "DownloadItem",
    "DownloadStatus",
    "ExtractionStatus",
    "ItemStatus",
    "SelectionMode",
    "SourceKind",
    "SQLiteTaskRepository",
    "SchedulePlan",
    "Task",
    "TaskService",
    "TaskScheduler",
    "TaskSettings",
]
