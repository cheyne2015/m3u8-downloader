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
            "https://site.example/active",
            "https://site.example/both",
            "https://site.example/wait",
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

