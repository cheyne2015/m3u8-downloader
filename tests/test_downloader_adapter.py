"""现有下载核心到新版协调器的适配测试。"""

from pathlib import Path
import threading
import time

import pytest

from m3u8_downloader.downloader_adapter_v2 import (
    ExistingDownloaderAdapter,
    _SharedGlobalSpeedPool,
)
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


def test_adapter_waits_for_cancelled_downloader_cleanup_before_returning(tmp_path):
    calls = []

    class FakeDownloader:
        def __init__(self, **_kwargs):
            pass

        def download(self):
            calls.append("download")
            raise RuntimeError("已暂停")

        def wait_for_cleanup(self):
            calls.append("cleanup")

    service = TaskService(SQLiteTaskRepository(tmp_path / "tasks.db"))
    task = service.create_tasks(CreateTaskRequest(
        addresses="https://cdn.example/video.m3u8",
        save_directory=str(tmp_path),
    ))[0]
    item = service.list_items(task.id)[0]

    with pytest.raises(RuntimeError, match="已暂停"):
        ExistingDownloaderAdapter(downloader_factory=FakeDownloader).download(
            task,
            item,
            tmp_path / "video.mp4",
            stop_event=None,
            on_progress=lambda _value: None,
            on_log=lambda _message: None,
        )

    assert calls == ["download", "cleanup"]


def test_changing_global_speed_limit_rebases_without_waiting_for_consumer():
    pool = _SharedGlobalSpeedPool(10_000)
    limiter = pool.acquire()
    stopped = threading.Event()

    def consume_until_stopped():
        try:
            limiter.consume(64 * 1024, stopped)
        except Exception:
            pass

    consumer = threading.Thread(target=consume_until_stopped)
    consumer.start()
    time.sleep(0.05)

    changed = threading.Event()
    setter = threading.Thread(
        target=lambda: (pool.set_limit(1_000), changed.set())
    )
    setter.start()

    try:
        assert changed.wait(0.5), "修改限速不能被正在等待的下载线程阻塞"
    finally:
        stopped.set()
        consumer.join(timeout=1)
        setter.join(timeout=1)
