"""新版任务系统的领域对象。"""

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum


class SourceKind(str, Enum):
    WEB_PAGE = "web_page"
    DIRECT_M3U8 = "direct_m3u8"


class ExtractionStatus(str, Enum):
    NOT_REQUIRED = "not_required"
    WAITING = "waiting"
    RUNNING = "running"
    PAUSED = "paused"
    COMPLETED = "completed"
    FAILED = "failed"


class DownloadStatus(str, Enum):
    NOT_READY = "not_ready"
    PENDING_SELECTION = "pending_selection"
    WAITING = "waiting"
    RUNNING = "running"
    MERGING = "merging"
    PAUSED = "paused"
    RETRY_WAIT = "retry_wait"
    PARTIAL_FAILURE = "partial_failure"
    COMPLETED = "completed"


class ItemStatus(str, Enum):
    UNSELECTED = "unselected"
    WAITING = "waiting"
    DOWNLOADING = "downloading"
    PAUSED = "paused"
    RETRY_WAIT = "retry_wait"
    FAILED = "failed"
    SKIPPED = "skipped"
    COMPLETED = "completed"


class SelectionMode(str, Enum):
    AUTO = "auto"
    MANUAL = "manual"


@dataclass(frozen=True)
class TaskSettings:
    download_task_limit: int = 3
    extraction_task_limit: int = 3
    auto_download_threshold: int = 3


@dataclass(frozen=True)
class Task:
    id: str
    source_url: str
    source_kind: SourceKind
    name: str
    save_directory: str
    extraction_status: ExtractionStatus
    download_status: DownloadStatus
    queue_position: int
    created_at: datetime
    updated_at: datetime
    selection_mode: SelectionMode = SelectionMode.AUTO
    settings: TaskSettings = field(default_factory=TaskSettings)


@dataclass(frozen=True)
class CreateTaskRequest:
    addresses: str
    save_directory: str
    settings: TaskSettings = field(default_factory=TaskSettings)


@dataclass(frozen=True)
class Candidate:
    url: str
    label: str = ""
    estimated_bytes: int | None = None
    duration_seconds: float | None = None


@dataclass(frozen=True)
class DownloadItem:
    id: str
    task_id: str
    source_url: str
    label: str
    output_index: int
    status: ItemStatus
    estimated_bytes: int | None = None
    duration_seconds: float | None = None
