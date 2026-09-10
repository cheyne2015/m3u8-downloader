"""现有下载核心到新版协调器的适配测试。"""

from pathlib import Path

from m3u8_downloader.downloader_adapter_v2 import ExistingDownloaderAdapter
from m3u8_downloader.tasking import (
    CreateTaskRequest,
    SQLiteTaskRepository,
    TaskService,
    TaskSettings,
)


def test_adapter_passes_threads_retry_proxy_and_callbacks(tmp_path):
    captured = {}

    class FakeDownloader:
        def __init__(self, **kwargs):
            captured.update(kwargs)

        def download(self):
            captured["progress_callback"]({"downloaded": 5, "total": 10})
            captured["log_callback"]("正在下载")
            Path(captured["output"]).write_bytes(b"done")
            return captured["output"]

    settings = TaskSettings(
        segment_threads=12,
        request_retries=4,
        proxy="127.0.0.1:7897",
        timeout_seconds=45,
        speed_limit=2_000_000,
    )
    service = TaskService(SQLiteTaskRepository(tmp_path / "tasks.db"))
    task = service.create_tasks(CreateTaskRequest(
        addresses="https://cdn.example/video.m3u8",
        save_directory=str(tmp_path),
        settings=settings,
    ))[0]
    item = service.list_items(task.id)[0]
    progress = []
    logs = []
    output = tmp_path / "video.mp4"

    result = ExistingDownloaderAdapter(downloader_factory=FakeDownloader).download(
        task, item, output, stop_event=None, on_progress=progress.append, on_log=logs.append
    )

    assert captured["url"] == item.source_url
    assert captured["workers"] == 12
    assert captured["max_retries"] == 4
    assert captured["proxy"] == "127.0.0.1:7897"
    assert captured["timeout"] == 45
    assert captured["speed_limit"] == 2_000_000
    assert result == output
    assert progress == [{"downloaded": 5, "total": 10}]
    assert logs == ["正在下载"]
