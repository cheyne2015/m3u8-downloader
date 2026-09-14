"""新版父任务调度器公共行为测试。"""

from dataclasses import replace

from m3u8_downloader.tasking import (
    CreateTaskRequest,
    DownloadStatus,
    ExtractionStatus,
    SQLiteTaskRepository,
    TaskScheduler,
    TaskService,
)


def test_download_and_extraction_use_separate_fifo_capacity(tmp_path):
    repository = SQLiteTaskRepository(tmp_path / "tasks.db")
    service = TaskService(repository, id_factory=iter([
        "extract-active", "both", "extract-wait", "download-active",
        "download-wait-1", "download-wait-2", "download-wait-3",
    ]).__next__)
    tasks = service.create_tasks(CreateTaskRequest(
        addresses="\n".join([
            "https://active.example/active",
            "https://both.example/both",
            "https://wait.example/wait",
            "https://cdn.example/active.m3u8",
            "https://cdn.example/1.m3u8",
            "https://cdn.example/2.m3u8",
            "https://cdn.example/3.m3u8",
        ]),
        save_directory=str(tmp_path),
    ))
    tasks = [
        replace(tasks[0], extraction_status=ExtractionStatus.RUNNING),
        replace(
            tasks[1],
            extraction_status=ExtractionStatus.RUNNING,
            download_status=DownloadStatus.WAITING,
        ),
        tasks[2],
        replace(tasks[3], download_status=DownloadStatus.RUNNING),
        *tasks[4:],
    ]

    plan = TaskScheduler().plan(tasks, download_limit=3, extraction_limit=3)

    assert plan.start_extraction == ("extract-wait",)
    assert plan.start_download == ("both", "download-wait-1")


def test_same_website_extractions_share_global_parallel_capacity(tmp_path):
    repository = SQLiteTaskRepository(tmp_path / "tasks.db")
    service = TaskService(repository, id_factory=iter([
        "same-running", "same-waiting", "other-waiting",
    ]).__next__)
    tasks = service.create_tasks(CreateTaskRequest(
        addresses="\n".join([
            "https://video.example/watch/1",
            "https://video.example/watch/2",
            "https://another.example/watch/3",
        ]),
        save_directory=str(tmp_path),
    ))
    first_plan = TaskScheduler().plan(tasks, download_limit=3, extraction_limit=3)
    assert first_plan.start_extraction == (
        "same-running", "same-waiting", "other-waiting",
    )

    tasks = [replace(tasks[0], extraction_status=ExtractionStatus.RUNNING), *tasks[1:]]

    plan = TaskScheduler().plan(tasks, download_limit=3, extraction_limit=3)

    assert plan.start_extraction == ("same-waiting", "other-waiting")


def test_move_to_front_persists_and_changes_next_waiting_task(tmp_path):
    repository = SQLiteTaskRepository(tmp_path / "tasks.db")
    service = TaskService(repository, id_factory=iter(["one", "two", "three"]).__next__)
    service.create_tasks(CreateTaskRequest(
        addresses="\n".join([
            "https://cdn.example/1.m3u8",
            "https://cdn.example/2.m3u8",
            "https://cdn.example/3.m3u8",
        ]),
        save_directory=str(tmp_path),
    ))

    service.move_task("three", "front")

    restored = SQLiteTaskRepository(tmp_path / "tasks.db").list_tasks()
    assert [task.id for task in restored] == ["three", "one", "two"]
    plan = TaskScheduler().plan(restored, download_limit=1, extraction_limit=3)
    assert plan.start_download == ("three",)
