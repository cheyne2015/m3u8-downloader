"""SQLite任务仓库。"""

import json
import sqlite3
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Iterable, List

from .models import (
    AppSettings,
    DownloadStatus,
    DownloadItem,
    ExtractionStatus,
    ItemStatus,
    LogEntry,
    SelectionMode,
    SourceKind,
    Task,
    TaskSettings,
)


class SQLiteTaskRepository:
    """以SQLite持久化父任务，写入操作使用单事务。"""

    def __init__(self, database_path) -> None:
        self.database_path = Path(database_path)
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(str(self.database_path))
        connection.row_factory = sqlite3.Row
        return connection

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.execute("""
                CREATE TABLE IF NOT EXISTS tasks (
                    id TEXT PRIMARY KEY,
                    source_url TEXT NOT NULL,
                    source_kind TEXT NOT NULL,
                    name TEXT NOT NULL,
                    save_directory TEXT NOT NULL,
                    extraction_status TEXT NOT NULL,
                    download_status TEXT NOT NULL,
                    queue_position INTEGER NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    original_title TEXT NOT NULL DEFAULT '',
                    name_edited INTEGER NOT NULL DEFAULT 0,
                    last_error TEXT NOT NULL DEFAULT '',
                    retry_count INTEGER NOT NULL DEFAULT 0,
                    retry_at TEXT,
                    completed_at TEXT,
                    selection_mode TEXT NOT NULL DEFAULT 'auto',
                    settings_json TEXT NOT NULL
                )
            """)
            self._ensure_task_column(connection, "original_title", "TEXT NOT NULL DEFAULT ''")
            self._ensure_task_column(connection, "name_edited", "INTEGER NOT NULL DEFAULT 0")
            self._ensure_task_column(connection, "last_error", "TEXT NOT NULL DEFAULT ''")
            self._ensure_task_column(connection, "retry_count", "INTEGER NOT NULL DEFAULT 0")
            self._ensure_task_column(connection, "retry_at", "TEXT")
            self._ensure_task_column(connection, "completed_at", "TEXT")
            connection.execute("""
                CREATE TABLE IF NOT EXISTS app_settings (
                    singleton INTEGER PRIMARY KEY CHECK(singleton = 1),
                    settings_json TEXT NOT NULL
                )
            """)
            connection.execute("""
                CREATE TABLE IF NOT EXISTS download_items (
                    id TEXT PRIMARY KEY,
                    task_id TEXT NOT NULL,
                    source_url TEXT NOT NULL,
                    label TEXT NOT NULL,
                    output_index INTEGER NOT NULL,
                    status TEXT NOT NULL,
                    estimated_bytes INTEGER,
                    duration_seconds REAL,
                    valid INTEGER NOT NULL DEFAULT 1,
                    output_path TEXT NOT NULL DEFAULT '',
                    downloaded_bytes INTEGER NOT NULL DEFAULT 0,
                    total_bytes INTEGER NOT NULL DEFAULT 0,
                    UNIQUE(task_id, source_url),
                    FOREIGN KEY(task_id) REFERENCES tasks(id) ON DELETE CASCADE
                )
            """)
            item_columns = {row[1] for row in connection.execute("PRAGMA table_info(download_items)")}
            if "valid" not in item_columns:
                connection.execute(
                    "ALTER TABLE download_items ADD COLUMN valid INTEGER NOT NULL DEFAULT 1"
                )
            self._ensure_item_column(connection, "output_path", "TEXT NOT NULL DEFAULT ''")
            self._ensure_item_column(connection, "downloaded_bytes", "INTEGER NOT NULL DEFAULT 0")
            self._ensure_item_column(connection, "total_bytes", "INTEGER NOT NULL DEFAULT 0")
            self._ensure_item_column(connection, "progress_percent", "REAL NOT NULL DEFAULT 0")
            self._ensure_item_column(connection, "speed_bps", "REAL NOT NULL DEFAULT 0")
            self._ensure_item_column(connection, "eta_seconds", "REAL NOT NULL DEFAULT 0")
            connection.execute("""
                CREATE TABLE IF NOT EXISTS task_logs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    task_id TEXT NOT NULL,
                    level TEXT NOT NULL,
                    category TEXT NOT NULL,
                    message TEXT NOT NULL,
                    created_at TEXT NOT NULL
                )
            """)
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_task_logs_task_time "
                "ON task_logs(task_id, created_at)"
            )

    @staticmethod
    def _ensure_task_column(connection: sqlite3.Connection, name: str, definition: str) -> None:
        columns = {row[1] for row in connection.execute("PRAGMA table_info(tasks)")}
        if name not in columns:
            connection.execute(f"ALTER TABLE tasks ADD COLUMN {name} {definition}")

    @staticmethod
    def _ensure_item_column(connection: sqlite3.Connection, name: str, definition: str) -> None:
        columns = {row[1] for row in connection.execute("PRAGMA table_info(download_items)")}
        if name not in columns:
            connection.execute(f"ALTER TABLE download_items ADD COLUMN {name} {definition}")

    def next_queue_position(self) -> int:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT COALESCE(MAX(queue_position), 0) + 1 AS value FROM tasks"
            ).fetchone()
        return int(row["value"])

    def load_app_settings(self) -> AppSettings:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT settings_json FROM app_settings WHERE singleton = 1"
            ).fetchone()
        return AppSettings() if row is None else AppSettings(**json.loads(row["settings_json"]))

    def save_app_settings(self, settings: AppSettings) -> None:
        payload = json.dumps(asdict(settings), ensure_ascii=False, separators=(",", ":"))
        with self._connect() as connection:
            connection.execute("""
                INSERT INTO app_settings(singleton, settings_json) VALUES(1, ?)
                ON CONFLICT(singleton) DO UPDATE SET settings_json = excluded.settings_json
            """, (payload,))

    def add_many(self, tasks: Iterable[Task]) -> None:
        rows = [(
            task.id,
            task.source_url,
            task.source_kind.value,
            task.name,
            task.save_directory,
            task.extraction_status.value,
            task.download_status.value,
            task.queue_position,
            task.created_at.isoformat(),
            task.updated_at.isoformat(),
            task.original_title,
            int(task.name_edited),
            task.last_error,
            task.retry_count,
            task.retry_at.isoformat() if task.retry_at else None,
            task.completed_at.isoformat() if task.completed_at else None,
            task.selection_mode.value,
            json.dumps(asdict(task.settings), ensure_ascii=False, separators=(",", ":")),
        ) for task in tasks]
        if not rows:
            return
        with self._connect() as connection:
            connection.executemany("""
                INSERT INTO tasks (
                    id, source_url, source_kind, name, save_directory,
                    extraction_status, download_status, queue_position,
                    created_at, updated_at, original_title, name_edited, last_error,
                    retry_count, retry_at, completed_at,
                    selection_mode, settings_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, rows)

    def add_bundle(self, tasks: Iterable[Task], items: Iterable[DownloadItem]) -> None:
        """在一个事务中创建父任务和首批下载项。"""
        tasks = list(tasks)
        items = list(items)
        task_rows = [(
            task.id, task.source_url, task.source_kind.value, task.name,
            task.save_directory, task.extraction_status.value,
            task.download_status.value, task.queue_position,
            task.created_at.isoformat(), task.updated_at.isoformat(),
            task.original_title, int(task.name_edited), task.last_error,
            task.retry_count, task.retry_at.isoformat() if task.retry_at else None,
            task.completed_at.isoformat() if task.completed_at else None,
            task.selection_mode.value,
            json.dumps(asdict(task.settings), ensure_ascii=False, separators=(",", ":")),
        ) for task in tasks]
        item_rows = [(
            item.id, item.task_id, item.source_url, item.label,
            item.output_index, item.status.value, item.estimated_bytes,
            item.duration_seconds, int(item.valid), item.output_path,
            item.downloaded_bytes, item.total_bytes, item.progress_percent,
            item.speed_bps, item.eta_seconds,
        ) for item in items]
        with self._connect() as connection:
            if task_rows:
                connection.executemany("""
                    INSERT INTO tasks (
                        id, source_url, source_kind, name, save_directory,
                        extraction_status, download_status, queue_position,
                        created_at, updated_at, original_title, name_edited,
                        last_error, retry_count, retry_at, completed_at,
                        selection_mode, settings_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, task_rows)
            if item_rows:
                connection.executemany("""
                    INSERT INTO download_items (
                        id, task_id, source_url, label, output_index, status,
                        estimated_bytes, duration_seconds, valid, output_path,
                        downloaded_bytes, total_bytes, progress_percent, speed_bps,
                        eta_seconds
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, item_rows)

    def save_many(self, tasks: Iterable[Task]) -> None:
        """原子保存已有任务的可变快照。"""
        rows = [(
            task.source_url,
            task.source_kind.value,
            task.name,
            task.save_directory,
            task.extraction_status.value,
            task.download_status.value,
            task.queue_position,
            task.created_at.isoformat(),
            task.updated_at.isoformat(),
            task.original_title,
            int(task.name_edited),
            task.last_error,
            task.retry_count,
            task.retry_at.isoformat() if task.retry_at else None,
            task.completed_at.isoformat() if task.completed_at else None,
            task.selection_mode.value,
            json.dumps(asdict(task.settings), ensure_ascii=False, separators=(",", ":")),
            task.id,
        ) for task in tasks]
        if not rows:
            return
        with self._connect() as connection:
            connection.executemany("""
                UPDATE tasks SET
                    source_url = ?, source_kind = ?, name = ?, save_directory = ?,
                    extraction_status = ?, download_status = ?, queue_position = ?,
                    created_at = ?, updated_at = ?, original_title = ?, name_edited = ?,
                    last_error = ?, retry_count = ?, retry_at = ?, completed_at = ?,
                    selection_mode = ?, settings_json = ?
                WHERE id = ?
            """, rows)

    def list_tasks(self) -> List[Task]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM tasks ORDER BY queue_position, created_at, id"
            ).fetchall()
        return [self._from_row(row) for row in rows]

    def get_task(self, task_id: str) -> Task:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM tasks WHERE id = ?", (task_id,)
            ).fetchone()
        if row is None:
            raise KeyError(task_id)
        return self._from_row(row)

    def add_items(self, items: Iterable[DownloadItem]) -> None:
        rows = [(
            item.id, item.task_id, item.source_url, item.label,
            item.output_index, item.status.value, item.estimated_bytes,
            item.duration_seconds,
            int(item.valid),
            item.output_path, item.downloaded_bytes, item.total_bytes,
            item.progress_percent, item.speed_bps, item.eta_seconds,
        ) for item in items]
        if not rows:
            return
        with self._connect() as connection:
            connection.executemany("""
                INSERT OR IGNORE INTO download_items (
                    id, task_id, source_url, label, output_index, status,
                    estimated_bytes, duration_seconds, valid, output_path,
                    downloaded_bytes, total_bytes, progress_percent, speed_bps,
                    eta_seconds
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, rows)

    def list_items(self, task_id: str) -> List[DownloadItem]:
        with self._connect() as connection:
            rows = connection.execute("""
                SELECT * FROM download_items WHERE task_id = ?
                ORDER BY output_index, id
            """, (task_id,)).fetchall()
        return [DownloadItem(
            id=row["id"], task_id=row["task_id"], source_url=row["source_url"],
            label=row["label"], output_index=int(row["output_index"]),
            status=ItemStatus(row["status"]),
            estimated_bytes=row["estimated_bytes"],
            duration_seconds=row["duration_seconds"],
            valid=bool(row["valid"]),
            output_path=row["output_path"],
            downloaded_bytes=int(row["downloaded_bytes"]),
            total_bytes=int(row["total_bytes"]),
            progress_percent=float(row["progress_percent"]),
            speed_bps=float(row["speed_bps"]),
            eta_seconds=float(row["eta_seconds"]),
        ) for row in rows]

    def save_items(self, items: Iterable[DownloadItem]) -> None:
        rows = [(
            item.source_url, item.label, item.output_index, item.status.value,
            item.estimated_bytes, item.duration_seconds, int(item.valid),
            item.output_path, item.downloaded_bytes, item.total_bytes,
            item.progress_percent, item.speed_bps, item.eta_seconds, item.id,
        ) for item in items]
        if not rows:
            return
        with self._connect() as connection:
            connection.executemany("""
                UPDATE download_items SET source_url = ?, label = ?,
                    output_index = ?, status = ?, estimated_bytes = ?,
                    duration_seconds = ?, valid = ?, output_path = ?,
                    downloaded_bytes = ?, total_bytes = ?, progress_percent = ?,
                    speed_bps = ?, eta_seconds = ? WHERE id = ?
            """, rows)

    def delete_items(self, task_id: str) -> None:
        """删除父任务的旧候选，供用户重新提取网页内容。"""
        with self._connect() as connection:
            connection.execute("DELETE FROM download_items WHERE task_id = ?", (task_id,))

    def update_item_progress(self, item: DownloadItem) -> None:
        """只更新进度列，避免覆盖并发发生的暂停或跳过状态。"""
        with self._connect() as connection:
            connection.execute("""
                UPDATE download_items SET downloaded_bytes = ?, total_bytes = ?,
                    progress_percent = ?, speed_bps = ?, eta_seconds = ?
                WHERE id = ?
            """, (
                item.downloaded_bytes, item.total_bytes, item.progress_percent,
                item.speed_bps, item.eta_seconds, item.id,
            ))

    def append_log(
        self, task_id: str, level: str, category: str, message: str, created_at: datetime
    ) -> None:
        with self._connect() as connection:
            connection.execute("""
                INSERT INTO task_logs(task_id, level, category, message, created_at)
                VALUES (?, ?, ?, ?, ?)
            """, (task_id, level, category, message, created_at.isoformat()))

    def list_logs(self, *, task_id: str | None = None, level: str | None = None) -> List[LogEntry]:
        clauses = []
        values = []
        if task_id is not None:
            clauses.append("task_id = ?")
            values.append(task_id)
        if level is not None:
            clauses.append("level = ?")
            values.append(level)
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM task_logs" + where + " ORDER BY id", values
            ).fetchall()
        return [LogEntry(
            id=int(row["id"]), task_id=row["task_id"], level=row["level"],
            category=row["category"], message=row["message"],
            created_at=datetime.fromisoformat(row["created_at"]),
        ) for row in rows]

    def delete_task(self, task_id: str) -> None:
        with self._connect() as connection:
            connection.execute("DELETE FROM task_logs WHERE task_id = ?", (task_id,))
            connection.execute("DELETE FROM download_items WHERE task_id = ?", (task_id,))
            connection.execute("DELETE FROM tasks WHERE id = ?", (task_id,))

    def purge_logs_before(self, cutoff: datetime) -> int:
        with self._connect() as connection:
            cursor = connection.execute(
                "DELETE FROM task_logs WHERE created_at < ?", (cutoff.isoformat(),)
            )
            return int(cursor.rowcount)

    @staticmethod
    def _from_row(row: sqlite3.Row) -> Task:
        settings = TaskSettings(**json.loads(row["settings_json"]))
        return Task(
            id=row["id"],
            source_url=row["source_url"],
            source_kind=SourceKind(row["source_kind"]),
            name=row["name"],
            save_directory=row["save_directory"],
            extraction_status=ExtractionStatus(row["extraction_status"]),
            download_status=DownloadStatus(row["download_status"]),
            queue_position=int(row["queue_position"]),
            created_at=datetime.fromisoformat(row["created_at"]),
            updated_at=datetime.fromisoformat(row["updated_at"]),
            original_title=row["original_title"],
            name_edited=bool(row["name_edited"]),
            last_error=row["last_error"],
            retry_count=int(row["retry_count"]),
            retry_at=(datetime.fromisoformat(row["retry_at"]) if row["retry_at"] else None),
            completed_at=(
                datetime.fromisoformat(row["completed_at"])
                if row["completed_at"] else None
            ),
            selection_mode=SelectionMode(row["selection_mode"]),
            settings=settings,
        )
