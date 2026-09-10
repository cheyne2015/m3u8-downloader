"""任务日志持久化测试。"""

from datetime import datetime, timedelta

from m3u8_downloader.tasking import SQLiteTaskRepository, TaskService


def test_logs_persist_and_can_filter_by_task_and_level(tmp_path):
    repository = SQLiteTaskRepository(tmp_path / "tasks.db")
    service = TaskService(
        repository, clock=lambda: datetime(2026, 9, 10, 18, 30, 0)
    )
    service.add_log("task-a", "信息", "下载", "开始下载")
    service.add_log("task-b", "错误", "提取", "提取失败")
    service.add_log("task-a", "错误", "下载", "网络中断")

    assert [entry.message for entry in service.list_logs(task_id="task-a")] == [
        "开始下载", "网络中断"
    ]
    assert [entry.message for entry in service.list_logs(level="错误")] == [
        "提取失败", "网络中断"
    ]
    assert SQLiteTaskRepository(tmp_path / "tasks.db").list_logs()[0].created_at == datetime(
        2026, 9, 10, 18, 30, 0
    )


def test_default_retention_removes_logs_older_than_thirty_days(tmp_path):
    repository = SQLiteTaskRepository(tmp_path / "tasks.db")
    now = datetime(2026, 9, 10, 12, 0, 0)
    repository.append_log("task", "信息", "任务", "旧日志", now - timedelta(days=31))
    repository.append_log("task", "信息", "任务", "新日志", now - timedelta(days=29))
    service = TaskService(repository, clock=lambda: now)

    service.purge_expired_logs()

    assert [entry.message for entry in service.list_logs()] == ["新日志"]
