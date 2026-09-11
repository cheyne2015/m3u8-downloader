"""Qt 后台调度控制器测试。"""

import threading

from m3u8_downloader.background_v2 import TaskBackgroundController
from m3u8_downloader.tasking import (
    Candidate,
    CreateTaskRequest,
    ExtractionStatus,
    SQLiteTaskRepository,
    TaskService,
)


class RecordingExtractionCoordinator:
    def __init__(self):
        self.calls = []

    def run_extraction(self, task_id, stop_event):
        self.calls.append(task_id)


class RecordingDownloadCoordinator:
    def __init__(self):
        self.calls = []

    def run_parent(self, task_id, stop_event, on_log):
        self.calls.append(task_id)


def test_controller_dispatches_extraction_and_download_without_blocking_gui(qtbot, tmp_path):
    repository = SQLiteTaskRepository(tmp_path / "tasks.db")
    service = TaskService(repository, id_factory=iter(["page", "direct"]).__next__)
    service.create_tasks(CreateTaskRequest(
        addresses="https://site.example/watch/42\nhttps://cdn.example/video.m3u8",
        save_directory=str(tmp_path),
    ))
    extraction = RecordingExtractionCoordinator()
    download = RecordingDownloadCoordinator()
    controller = TaskBackgroundController(
        service,
        extraction_coordinator=extraction,
        download_coordinator=download,
        poll_interval_ms=10,
    )

    controller.start()
    qtbot.waitUntil(lambda: bool(extraction.calls and download.calls), timeout=1000)
    controller.stop()

    assert extraction.calls[0] == "page"
    assert download.calls[0] == "direct"


def test_restart_waits_for_stopped_extraction_worker_before_clearing_candidates(qtbot, tmp_path):
    repository = SQLiteTaskRepository(tmp_path / "tasks.db")
    service = TaskService(repository, id_factory=lambda: "page")
    task = service.create_tasks(CreateTaskRequest(
        addresses="https://site.example/watch/42", save_directory=str(tmp_path)
    ))[0]

    class BlockingExtraction:
        def __init__(self):
            self.calls = 0
            self.started = threading.Event()
            self.release = threading.Event()

        def run_extraction(self, task_id, stop_event):
            self.calls += 1
            service.start_extraction(task_id)
            if self.calls == 1:
                self.started.set()
                self.release.wait(2)
                service.add_candidates(task_id, [Candidate("https://cdn.example/late-old.m3u8")])
                return
            service.finish_extraction(task_id)

    extraction = BlockingExtraction()
    controller = TaskBackgroundController(
        service,
        extraction_coordinator=extraction,
        download_coordinator=RecordingDownloadCoordinator(),
        poll_interval_ms=10,
    )
    controller.start()
    assert extraction.started.wait(1)
    controller.stop_extraction(task.id)

    restart_started = controller.retry_extraction(task.id)
    extraction.release.set()
    assert restart_started is False
    qtbot.waitUntil(lambda: extraction.calls >= 2, timeout=1500)
    qtbot.waitUntil(
        lambda: service.get_task(task.id).extraction_status is ExtractionStatus.COMPLETED,
        timeout=1500,
    )
    controller.stop()

    assert service.list_items(task.id) == []
