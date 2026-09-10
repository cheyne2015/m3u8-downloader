"""新版父子任务管理公共接口。"""

from .models import (
    AppSettings,
    Candidate,
    CreateTaskRequest,
    DeletionPreview,
    DownloadItem,
    DownloadStatus,
    ExtractionStatus,
    ItemStatus,
    LogEntry,
    SelectionMode,
    SourceKind,
    Task,
    TaskSettings,
)
from .repository import SQLiteTaskRepository
from .scheduler import SchedulePlan, TaskScheduler
from .service import DuplicateSourceError, TaskService
from .coordinator import TaskCoordinator
from .output import OutputPlanner
from .download_coordinator import DownloadCoordinator
from .disk_space import DiskSpaceDecision, DiskSpaceGuard

__all__ = [
    "AppSettings",
    "Candidate",
    "CreateTaskRequest",
    "DeletionPreview",
    "DownloadItem",
    "DownloadCoordinator",
    "DownloadStatus",
    "DuplicateSourceError",
    "DiskSpaceDecision",
    "DiskSpaceGuard",
    "ExtractionStatus",
    "ItemStatus",
    "LogEntry",
    "OutputPlanner",
    "SelectionMode",
    "SourceKind",
    "SQLiteTaskRepository",
    "SchedulePlan",
    "Task",
    "TaskCoordinator",
    "TaskService",
    "TaskScheduler",
    "TaskSettings",
]
