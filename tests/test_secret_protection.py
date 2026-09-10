"""Windows 当前用户级敏感信息保护测试。"""

import json
import sqlite3

from m3u8_downloader.secrets_v2 import protect_secret, unprotect_secret
from m3u8_downloader.tasking import (
    CreateTaskRequest,
    SQLiteTaskRepository,
    TaskService,
    TaskSettings,
)


def test_cookie_is_encrypted_at_rest_and_can_be_restored_for_request(tmp_path):
    plain = "session_id=top-secret-token"
    protected = protect_secret(plain)
    assert protected != plain
    assert unprotect_secret(protected) == plain

    database = tmp_path / "tasks.db"
    service = TaskService(SQLiteTaskRepository(database))
    service.create_tasks(CreateTaskRequest(
        addresses="https://site.example/watch/42",
        save_directory=str(tmp_path),
        settings=TaskSettings(protected_cookie=protected),
    ))
    with sqlite3.connect(database) as connection:
        payload = connection.execute("SELECT settings_json FROM tasks").fetchone()[0]
    assert plain not in payload
    assert json.loads(payload)["protected_cookie"] == protected

