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
class AppSettings:
    download_task_limit: int = 3
    extraction_task_limit: int = 3
    auto_download_threshold: int = 3
    segment_threads: int = 8
    request_retries: int = 3
    task_retries: int = 1
    retry_delay_seconds: int = 30
    global_speed_limit: int = 0
    theme: str = "dark"
    log_retention_days: int = 30
    completion_notification: bool = False
    close_to_tray: bool = False

    def __post_init__(self) -> None:
        ranges = {
            "download_task_limit": (1, 6),
            "extraction_task_limit": (1, 6),
            "auto_download_threshold": (1, 20),
            "segment_threads": (1, 32),
            "request_retries": (0, 10),
            "task_retries": (0, 5),
            "retry_delay_seconds": (1, 3600),
            "global_speed_limit": (0, 10**12),
            "log_retention_days": (1, 3650),
        }
        for name, (minimum, maximum) in ranges.items():
            value = getattr(self, name)
            if not minimum <= value <= maximum:
                raise ValueError(f"{name} 必须在 {minimum}～{maximum} 之间")
        if self.theme not in {"dark", "light", "system"}:
            raise ValueError("theme 必须是 dark、light 或 system")


@dataclass(frozen=True)
class TaskSettings:
    auto_download_threshold: int = 3
    extraction_mode: str = "smart"
    proxy: str = ""
    referer: str = ""
    user_agent: str = ""
    protected_cookie: str = ""
    speed_limit: int = 0
    segment_threads: int = 8
    request_retries: int = 3
    task_retries: int = 1
    retry_delay_seconds: int = 30
    timeout_seconds: int = 30

    def __post_init__(self) -> None:
        ranges = {
            "auto_download_threshold": (1, 20),
            "speed_limit": (0, 10**12),
            "segment_threads": (1, 32),
            "request_retries": (0, 10),
            "task_retries": (0, 5),
            "retry_delay_seconds": (1, 3600),
            "timeout_seconds": (1, 3600),
        }
        for name, (minimum, maximum) in ranges.items():
            value = getattr(self, name)
            if not minimum <= value <= maximum:
                raise ValueError(f"{name} 必须在 {minimum}～{maximum} 之间")
        if self.extraction_mode not in {"smart", "deep", "normal"}:
            raise ValueError("extraction_mode 必须是 smart、deep 或 normal")


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
    original_title: str = ""
    name_edited: bool = False
    last_error: str = ""
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
    valid: bool = True


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
    valid: bool = True
    output_path: str = ""
    downloaded_bytes: int = 0
    total_bytes: int = 0
