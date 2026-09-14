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
    SiteCompatibilityRecord,
    SiteExtractionProfile,
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
            self._ensure_item_column(connection, "segment_count", "INTEGER NOT NULL DEFAULT 0")
            self._ensure_item_column(connection, "bandwidth", "INTEGER NOT NULL DEFAULT 0")
            self._ensure_item_column(
                connection, "duration_backup", "INTEGER NOT NULL DEFAULT 0"
            )
            connection.execute("""
                CREATE TABLE IF NOT EXISTS task_content_identities (
                    task_id TEXT PRIMARY KEY,
                    fingerprint TEXT NOT NULL,
                    created_at TEXT NOT NULL
                )
            """)
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_content_identity_fingerprint "
                "ON task_content_identities(fingerprint)"
            )
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
            connection.execute("""
                CREATE TABLE IF NOT EXISTS site_profiles (
                    hostname TEXT PRIMARY KEY,
                    settings_json TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
            """)
            connection.execute("""
                CREATE TABLE IF NOT EXISTS site_compatibility (
                    hostname TEXT PRIMARY KEY,
                    attempts INTEGER NOT NULL DEFAULT 0,
                    successes INTEGER NOT NULL DEFAULT 0,
                    failures INTEGER NOT NULL DEFAULT 0,
                    deep_successes INTEGER NOT NULL DEFAULT 0,
                    normal_successes INTEGER NOT NULL DEFAULT 0,
                    total_elapsed_seconds REAL NOT NULL DEFAULT 0,
                    last_result TEXT NOT NULL DEFAULT '',
                    last_mode TEXT NOT NULL DEFAULT '',
                    last_candidate_count INTEGER NOT NULL DEFAULT 0,
                    last_error TEXT NOT NULL DEFAULT '',
                    updated_at TEXT NOT NULL
                )
            """)

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
        if row is None:
            return AppSettings()
        values = json.loads(row["settings_json"])
        legacy_close_to_tray = values.pop("close_to_tray", None)
        if "close_rule_enabled" not in values and legacy_close_to_tray is not None:
            values["close_rule_enabled"] = bool(legacy_close_to_tray)
        return AppSettings(**values)

    def save_app_settings(self, settings: AppSettings) -> None:
        payload = json.dumps(asdict(settings), ensure_ascii=False, separators=(",", ":"))
        with self._connect() as connection:
            connection.execute("""
                INSERT INTO app_settings(singleton, settings_json) VALUES(1, ?)
                ON CONFLICT(singleton) DO UPDATE SET settings_json = excluded.settings_json
            """, (payload,))

    def save_site_profile(self, profile: SiteExtractionProfile) -> None:
        payload = json.dumps({
            "extraction_mode": profile.extraction_mode, "proxy": profile.proxy,
            "preferred_extraction_mode": profile.preferred_extraction_mode,
            "referer": profile.referer, "user_agent": profile.user_agent,
            "protected_cookie": profile.protected_cookie,
            "timeout_seconds": profile.timeout_seconds,
            "request_retries": profile.request_retries,
        }, ensure_ascii=False, separators=(",", ":"))
        with self._connect() as connection:
            connection.execute("""
                INSERT INTO site_profiles(hostname, settings_json, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(hostname) DO UPDATE SET
                    settings_json=excluded.settings_json, updated_at=excluded.updated_at
            """, (profile.hostname, payload, profile.updated_at.isoformat()))

    def get_site_profile(self, hostname: str) -> SiteExtractionProfile | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM site_profiles WHERE hostname = ?", (hostname,)
            ).fetchone()
        return None if row is None else self._site_profile_from_row(row)

    def list_site_profiles(self) -> List[SiteExtractionProfile]:
        with self._connect() as connection:
            rows = connection.execute("SELECT * FROM site_profiles ORDER BY hostname").fetchall()
        return [self._site_profile_from_row(row) for row in rows]

    def delete_site_profile(self, hostname: str) -> None:
        with self._connect() as connection:
            connection.execute("DELETE FROM site_profiles WHERE hostname = ?", (hostname,))

    @staticmethod
    def _site_profile_from_row(row) -> SiteExtractionProfile:
        values = json.loads(row["settings_json"])
        values.setdefault("preferred_extraction_mode", "")
        return SiteExtractionProfile(
            hostname=row["hostname"], updated_at=datetime.fromisoformat(row["updated_at"]),
            **values,
        )

    def record_site_compatibility(
        self, hostname: str, *, success: bool, mode: str, elapsed_seconds: float,
        candidate_count: int, error: str, updated_at: datetime,
    ) -> None:
        with self._connect() as connection:
            connection.execute("""
                INSERT INTO site_compatibility(
                    hostname, attempts, successes, failures, deep_successes,
                    normal_successes, total_elapsed_seconds, last_result,
                    last_mode, last_candidate_count, last_error, updated_at
                ) VALUES (?, 1, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(hostname) DO UPDATE SET
                    attempts=attempts+1, successes=successes+excluded.successes,
                    failures=failures+excluded.failures,
                    deep_successes=deep_successes+excluded.deep_successes,
                    normal_successes=normal_successes+excluded.normal_successes,
                    total_elapsed_seconds=total_elapsed_seconds+excluded.total_elapsed_seconds,
                    last_result=excluded.last_result, last_mode=excluded.last_mode,
                    last_candidate_count=excluded.last_candidate_count,
                    last_error=excluded.last_error, updated_at=excluded.updated_at
            """, (
                hostname, int(success), int(not success), int(success and mode == "deep"),
                int(success and mode == "normal"), max(0.0, float(elapsed_seconds)),
                "成功" if success else "失败", mode, max(0, int(candidate_count)),
                str(error), updated_at.isoformat(),
            ))

    def list_site_compatibility(self) -> List[SiteCompatibilityRecord]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM site_compatibility ORDER BY updated_at DESC, hostname"
            ).fetchall()
        return [SiteCompatibilityRecord(
            hostname=row["hostname"], attempts=int(row["attempts"]),
            successes=int(row["successes"]), failures=int(row["failures"]),
            deep_successes=int(row["deep_successes"]),
            normal_successes=int(row["normal_successes"]),
            total_elapsed_seconds=float(row["total_elapsed_seconds"]),
            last_result=row["last_result"], last_mode=row["last_mode"],
            last_candidate_count=int(row["last_candidate_count"]),
            last_error=row["last_error"],
            updated_at=datetime.fromisoformat(row["updated_at"]),
        ) for row in rows]

    def clear_site_compatibility(self) -> None:
        with self._connect() as connection:
            connection.execute("DELETE FROM site_compatibility")

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
            item.speed_bps, item.eta_seconds, item.segment_count, item.bandwidth,
            int(item.duration_backup),
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
                        eta_seconds, segment_count, bandwidth, duration_backup
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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

    def list_tasks_by_source_urls(self, source_urls: Iterable[str]) -> List[Task]:
        urls = tuple(dict.fromkeys(source_urls))
        if not urls:
            return []
        placeholders = ", ".join("?" for _ in urls)
        with self._connect() as connection:
            rows = connection.execute(
                f"SELECT * FROM tasks WHERE source_url IN ({placeholders}) "
                "ORDER BY created_at DESC, rowid DESC",
                urls,
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
            item.segment_count, item.bandwidth, int(item.duration_backup),
        ) for item in items]
        if not rows:
            return
        with self._connect() as connection:
            connection.executemany("""
                INSERT OR IGNORE INTO download_items (
                    id, task_id, source_url, label, output_index, status,
                    estimated_bytes, duration_seconds, valid, output_path,
                    downloaded_bytes, total_bytes, progress_percent, speed_bps,
                    eta_seconds, segment_count, bandwidth, duration_backup
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
            segment_count=int(row["segment_count"]),
            bandwidth=int(row["bandwidth"]),
            duration_backup=bool(row["duration_backup"]),
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
            item.progress_percent, item.speed_bps, item.eta_seconds,
            item.segment_count, item.bandwidth, int(item.duration_backup), item.id,
        ) for item in items]
        if not rows:
            return
        with self._connect() as connection:
            connection.executemany("""
                UPDATE download_items SET source_url = ?, label = ?,
                    output_index = ?, status = ?, estimated_bytes = ?,
                    duration_seconds = ?, valid = ?, output_path = ?,
                    downloaded_bytes = ?, total_bytes = ?, progress_percent = ?,
                    speed_bps = ?, eta_seconds = ?, segment_count = ?, bandwidth = ?,
                    duration_backup = ?
                    WHERE id = ?
            """, rows)

    def delete_items(self, task_id: str) -> None:
        """删除父任务的旧候选，供用户重新提取网页内容。"""
        with self._connect() as connection:
            connection.execute(
                "DELETE FROM task_content_identities WHERE task_id = ?", (task_id,)
            )
            connection.execute("DELETE FROM download_items WHERE task_id = ?", (task_id,))

    def save_content_identity(
        self, task_id: str, fingerprint: str, created_at: datetime,
    ) -> None:
        with self._connect() as connection:
            connection.execute("""
                INSERT INTO task_content_identities(task_id, fingerprint, created_at)
                VALUES (?, ?, ?)
                ON CONFLICT(task_id) DO UPDATE SET
                    fingerprint = excluded.fingerprint,
                    created_at = excluded.created_at
            """, (task_id, fingerprint, created_at.isoformat()))

    def get_content_identity(self, task_id: str) -> str:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT fingerprint FROM task_content_identities WHERE task_id = ?",
                (task_id,),
            ).fetchone()
        return "" if row is None else str(row["fingerprint"])

    def find_task_by_content_identity(
        self, fingerprint: str, *, excluding_task_id: str,
    ) -> Task | None:
        with self._connect() as connection:
            row = connection.execute("""
                SELECT tasks.* FROM task_content_identities
                JOIN tasks ON tasks.id = task_content_identities.task_id
                WHERE task_content_identities.fingerprint = ? AND tasks.id != ?
                ORDER BY tasks.created_at DESC, tasks.rowid DESC
                LIMIT 1
            """, (fingerprint, excluding_task_id)).fetchone()
        return None if row is None else self._from_row(row)

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

    def latest_log_id(self) -> int:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT COALESCE(MAX(id), 0) AS value FROM task_logs"
            ).fetchone()
        return int(row["value"])

    def list_logs(
        self, *, task_id: str | None = None, level: str | None = None,
        after_id: int | None = None,
    ) -> List[LogEntry]:
        clauses = []
        values = []
        if task_id is not None:
            clauses.append("task_id = ?")
            values.append(task_id)
        if level is not None:
            clauses.append("level = ?")
            values.append(level)
        if after_id is not None:
            clauses.append("id > ?")
            values.append(after_id)
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
            connection.execute(
                "DELETE FROM task_content_identities WHERE task_id = ?", (task_id,)
            )
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
