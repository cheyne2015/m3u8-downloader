"""Qt 后台调度控制器测试。"""

import threading
from dataclasses import replace

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


def test_controller_uses_independent_configured_extraction_and_download_limits(qtbot, tmp_path):
    repository = SQLiteTaskRepository(tmp_path / "tasks.db")
    service = TaskService(
        repository,
        id_factory=iter([
            "page-1", "page-2", "page-3", "page-4",
            "direct-1", "direct-2", "direct-3", "direct-4",
        ]).__next__,
    )
    service.create_tasks(CreateTaskRequest(
        addresses="\n".join([
            "https://site.example/1",
            "https://site.example/2",
            "https://site.example/3",
            "https://site.example/4",
            "https://cdn.example/1.m3u8",
            "https://cdn.example/2.m3u8",
            "https://cdn.example/3.m3u8",
            "https://cdn.example/4.m3u8",
        ]),
        save_directory=str(tmp_path),
    ))
    service.save_app_settings(replace(
        service.load_app_settings(),
        extraction_task_limit=2,
        download_task_limit=4,
    ))

    class BlockingCoordinator:
        def __init__(self):
            self.calls = []
            self.release = threading.Event()

        def run_extraction(self, task_id, stop_event):
            self.calls.append(task_id)
            service.start_extraction(task_id)
            self.release.wait(2)

        def run_parent(self, task_id, stop_event, on_log):
            self.calls.append(task_id)
            item = service.list_items(task_id)[0]
            service.start_item(task_id, item.id)
            self.release.wait(2)

    extraction = BlockingCoordinator()
    download = BlockingCoordinator()
    controller = TaskBackgroundController(
        service,
        extraction_coordinator=extraction,
        download_coordinator=download,
        poll_interval_ms=10,
    )
    controller.start()
    qtbot.waitUntil(
        lambda: len(extraction.calls) == 2 and len(download.calls) == 4,
        timeout=1000,
    )
    qtbot.wait(100)

    assert len(extraction.calls) == 2
    assert len(download.calls) == 4

    extraction.release.set()
    download.release.set()
    controller.stop()


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


def test_controller_counts_submitted_extractions_as_occupied_slots(qtbot, tmp_path):
    """持久化状态先变化时，尚未退出的工作线程仍必须占用提取槽位。"""
    repository = SQLiteTaskRepository(tmp_path / "tasks.db")
    service = TaskService(
        repository,
        id_factory=iter(["one", "two", "three", "four"]).__next__,
    )
    tasks = service.create_tasks(CreateTaskRequest(
        addresses="\n".join([
            "https://site.example/1",
            "https://site.example/2",
            "https://site.example/3",
            "https://site.example/4",
        ]),
        save_directory=str(tmp_path),
    ))

    class BlockingExtraction:
        def __init__(self):
            self.calls = []
            self.release = threading.Event()

        def run_extraction(self, task_id, stop_event):
            self.calls.append(task_id)
            service.start_extraction(task_id)
            self.release.wait(2)

    extraction = BlockingExtraction()
    controller = TaskBackgroundController(
        service,
        extraction_coordinator=extraction,
        download_coordinator=RecordingDownloadCoordinator(),
        poll_interval_ms=10,
    )
    controller.start()
    qtbot.waitUntil(lambda: len(extraction.calls) == 3, timeout=1000)

    # 暂停会先改变数据库状态，旧工作线程收到停止信号后仍需一点时间退出。
    # 这一小段时间内不能把第四个任务也提交到线程池。
    controller.pause_task(tasks[0].id)
    qtbot.wait(100)

    assert len(extraction.calls) == 3
    assert service.get_task(tasks[3].id).extraction_status is ExtractionStatus.WAITING

    extraction.release.set()
    controller.stop()


def test_controller_counts_stopping_downloads_as_occupied_slots(qtbot, tmp_path):
    """暂停已落库但线程未退出时，不得提前启动第四个父下载任务。"""
    repository = SQLiteTaskRepository(tmp_path / "tasks.db")
    service = TaskService(
        repository,
        id_factory=iter(["one", "two", "three", "four"]).__next__,
    )
    tasks = service.create_tasks(CreateTaskRequest(
        addresses="\n".join([
            "https://cdn.example/1.m3u8",
            "https://cdn.example/2.m3u8",
            "https://cdn.example/3.m3u8",
            "https://cdn.example/4.m3u8",
        ]),
        save_directory=str(tmp_path),
    ))

    class BlockingDownload:
        def __init__(self):
            self.calls = []
            self.release = threading.Event()

        def run_parent(self, task_id, stop_event, on_log):
            self.calls.append(task_id)
            item = service.list_items(task_id)[0]
            service.start_item(task_id, item.id)
            self.release.wait(2)

    download = BlockingDownload()
    controller = TaskBackgroundController(
        service,
        extraction_coordinator=RecordingExtractionCoordinator(),
        download_coordinator=download,
        poll_interval_ms=10,
    )
    controller.start()
    qtbot.waitUntil(lambda: len(download.calls) == 3, timeout=1000)

    controller.pause_task(tasks[0].id)
    qtbot.wait(100)

    assert len(download.calls) == 3
    assert service.get_task(tasks[3].id).download_status.value == "waiting"

    download.release.set()
    controller.stop()
