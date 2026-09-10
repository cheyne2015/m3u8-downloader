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
from .coordinator import TaskCoordinator
from .output import OutputPlanner
from .download_coordinator import DownloadCoordinator

__all__ = [
    "AppSettings",
    "Candidate",
    "CreateTaskRequest",
    "DownloadItem",
    "DownloadCoordinator",
    "DownloadStatus",
    "ExtractionStatus",
    "ItemStatus",
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
