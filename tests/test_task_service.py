"""新版任务管理公共接口测试。"""

from dataclasses import replace
from datetime import datetime

from m3u8_downloader.tasking import (
    Candidate,
    CreateTaskRequest,
    DownloadStatus,
    ExtractionStatus,
    ItemStatus,
    SelectionMode,
    SourceKind,
    SQLiteTaskRepository,
    TaskService,
)


def test_user_can_create_and_restore_page_and_m3u8_tasks(tmp_path):
    """多行新建应去重、识别来源，并从数据库完整恢复父任务。"""
    repository = SQLiteTaskRepository(tmp_path / "tasks.db")
    ids = iter(["task-page", "task-m3u8"])
    service = TaskService(
        repository,
        clock=lambda: datetime(2026, 9, 10, 12, 30, 0),
        id_factory=lambda: next(ids),
    )

    created = service.create_tasks(CreateTaskRequest(
        addresses="""
            https://site.example/watch/42
            https://cdn.example/video/master.m3u8?token=secret
            https://site.example/watch/42
        """,
        save_directory=str(tmp_path / "downloads"),
    ))

    assert [task.id for task in created] == ["task-page", "task-m3u8"]
    assert created[0].source_kind is SourceKind.WEB_PAGE
    assert created[0].extraction_status is ExtractionStatus.WAITING
    assert created[0].download_status is DownloadStatus.NOT_READY
    assert created[0].name == "正在获取标题"
    assert created[1].source_kind is SourceKind.DIRECT_M3U8
    assert created[1].extraction_status is ExtractionStatus.NOT_REQUIRED
    assert created[1].download_status is DownloadStatus.WAITING
    assert created[1].name == "video"
    assert [task.queue_position for task in created] == [1, 2]
    assert all(task.save_directory == str(tmp_path / "downloads") for task in created)
    assert all(task.settings.auto_download_threshold == 3 for task in created)
    assert all(task.settings.segment_threads == 8 for task in created)
    direct_items = repository.list_items("task-m3u8")
    assert len(direct_items) == 1
    assert direct_items[0].source_url == "https://cdn.example/video/master.m3u8?token=secret"
    assert direct_items[0].status is ItemStatus.WAITING

    reopened = SQLiteTaskRepository(tmp_path / "tasks.db")
    restored = reopened.list_tasks()
    assert restored == created


def test_restart_pauses_interrupted_work_without_starting_network(tmp_path):
    """重启恢复只改变运行态，已完成和等待中的任务保持原状态。"""
    repository = SQLiteTaskRepository(tmp_path / "tasks.db")
    ids = iter(["extracting", "downloading", "merging", "retrying", "completed"])
    service = TaskService(repository, id_factory=lambda: next(ids))
    base = service.create_tasks(CreateTaskRequest(
        addresses="\n".join([
            "https://site.example/a",
            "https://cdn.example/b.m3u8",
            "https://cdn.example/c.m3u8",
            "https://cdn.example/d.m3u8",
            "https://cdn.example/e.m3u8",
        ]),
        save_directory=str(tmp_path),
    ))
    repository.save_many([
        replace(base[0], extraction_status=ExtractionStatus.RUNNING),
        replace(base[1], download_status=DownloadStatus.RUNNING),
        replace(base[2], download_status=DownloadStatus.MERGING),
        replace(base[3], download_status=DownloadStatus.RETRY_WAIT),
        replace(base[4], download_status=DownloadStatus.COMPLETED),
    ])

    restored = TaskService(repository).restore_tasks_after_restart()

    assert restored[0].extraction_status is ExtractionStatus.PAUSED
    assert restored[0].download_status is DownloadStatus.NOT_READY
    assert [task.download_status for task in restored[1:]] == [
        DownloadStatus.PAUSED,
        DownloadStatus.PAUSED,
        DownloadStatus.PAUSED,
        DownloadStatus.COMPLETED,
    ]
    assert repository.list_tasks() == restored


def test_finished_extraction_auto_queues_candidates_within_threshold(tmp_path):
    """最终有效候选不超过阈值时应全选，并把父任务送入下载队列。"""
    repository = SQLiteTaskRepository(tmp_path / "tasks.db")
    service = TaskService(repository, id_factory=iter([
        "parent", "item-1", "item-2"
    ]).__next__)
    task = service.create_tasks(CreateTaskRequest(
        addresses="https://site.example/watch/42",
        save_directory=str(tmp_path),
    ))[0]

    added = service.add_candidates(task.id, [
        Candidate("https://cdn.example/720.m3u8", label="720P", duration_seconds=60),
        Candidate("https://cdn.example/1080.m3u8", label="1080P", estimated_bytes=1000),
    ])
    finished = service.finish_extraction(task.id)

    assert [item.status for item in added] == [ItemStatus.UNSELECTED, ItemStatus.UNSELECTED]
    assert finished.extraction_status is ExtractionStatus.COMPLETED
    assert finished.download_status is DownloadStatus.WAITING
    assert [item.status for item in repository.list_items(task.id)] == [
        ItemStatus.WAITING,
        ItemStatus.WAITING,
    ]


def test_unreachable_candidates_do_not_count_toward_auto_download_threshold(tmp_path):
    repository = SQLiteTaskRepository(tmp_path / "tasks.db")
    service = TaskService(repository, id_factory=lambda: "parent")
    task = service.create_tasks(CreateTaskRequest(
        addresses="https://site.example/watch/42", save_directory=str(tmp_path)
    ))[0]
    service.add_candidates(task.id, [
        Candidate("https://cdn.example/1.m3u8"),
        Candidate("https://cdn.example/2.m3u8"),
        Candidate("https://cdn.example/3.m3u8"),
        Candidate("https://cdn.example/broken-1.m3u8", valid=False),
        Candidate("https://cdn.example/broken-2.m3u8", valid=False),
    ])

    finished = service.finish_extraction(task.id)

    assert finished.download_status is DownloadStatus.WAITING
    assert [item.status for item in service.list_items(task.id)] == [
        ItemStatus.WAITING,
        ItemStatus.WAITING,
        ItemStatus.WAITING,
        ItemStatus.UNSELECTED,
        ItemStatus.UNSELECTED,
    ]


def test_manual_selection_can_download_while_extraction_keeps_running(tmp_path):
    """手动接管后只下载所选项，后续流式候选保持未选择。"""
    repository = SQLiteTaskRepository(tmp_path / "tasks.db")
    service = TaskService(repository, id_factory=iter([
        "parent", "item-1", "item-2", "item-3"
    ]).__next__)
    task = service.create_tasks(CreateTaskRequest(
        addresses="https://site.example/watch/42",
        save_directory=str(tmp_path),
    ))[0]
    service.start_extraction(task.id)
    first_items = service.add_candidates(task.id, [
        Candidate("https://cdn.example/1.m3u8"),
        Candidate("https://cdn.example/2.m3u8"),
    ])

    downloading = service.select_items_for_download(task.id, [first_items[0].id])
    service.add_candidates(task.id, [Candidate("https://cdn.example/3.m3u8")])
    finished = service.finish_extraction(task.id)

    assert downloading.extraction_status is ExtractionStatus.RUNNING
    assert downloading.download_status is DownloadStatus.WAITING
    assert downloading.selection_mode is SelectionMode.MANUAL
    assert finished.extraction_status is ExtractionStatus.COMPLETED
    assert [item.status for item in repository.list_items(task.id)] == [
        ItemStatus.WAITING,
        ItemStatus.UNSELECTED,
        ItemStatus.UNSELECTED,
    ]
