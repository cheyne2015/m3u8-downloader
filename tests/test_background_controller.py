"""Qt 后台调度控制器测试。"""

from m3u8_downloader.background_v2 import TaskBackgroundController
from m3u8_downloader.tasking import CreateTaskRequest, SQLiteTaskRepository, TaskService


class RecordingExtractionCoordinator:
    def __init__(self):
        self.calls = []

    def run_extraction(self, task_id):
        self.calls.append(task_id)


class RecordingDownloadCoordinator:
    def __init__(self):
        self.calls = []

    def run_parent(self, task_id, on_log):
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
