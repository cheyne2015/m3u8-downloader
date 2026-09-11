"""提取协调器的端到端状态转换测试。"""

import pytest

from m3u8_downloader.tasking import (
    Candidate,
    CreateTaskRequest,
    DownloadStatus,
    ExtractionStatus,
    SQLiteTaskRepository,
    TaskCoordinator,
    TaskService,
)


class FallbackExtractor:
    def __init__(self):
        self.calls = []

    def extract(self, task, *, deep, on_candidate, on_title, stop_event):
        self.calls.append(deep)
        if deep:
            raise RuntimeError("深度浏览器不可用")
        on_title("示例视频 - 网站")
        on_candidate(Candidate("https://cdn.example/720.m3u8", label="720P"))
        on_candidate(Candidate("https://cdn.example/1080.m3u8", label="1080P"))
        return []


def test_smart_extraction_falls_back_streams_results_and_applies_threshold(tmp_path):
    repository = SQLiteTaskRepository(tmp_path / "tasks.db")
    service = TaskService(repository, id_factory=lambda: "parent")
    task = service.create_tasks(CreateTaskRequest(
        addresses="https://site.example/watch/42",
        save_directory=str(tmp_path),
    ))[0]
    extractor = FallbackExtractor()

    finished = TaskCoordinator(service, extractor=extractor).run_extraction(task.id)

    assert extractor.calls == [True, False]
    assert finished.name == "示例视频"
    assert finished.original_title == "示例视频 - 网站"
    assert finished.extraction_status is ExtractionStatus.COMPLETED
    assert finished.download_status is DownloadStatus.WAITING
    assert [item.label for item in service.list_items(task.id)] == ["720P", "1080P"]
    messages = [entry.message for entry in service.list_logs(task_id=task.id)]
    assert any("正在使用深度模式" in message for message in messages)
    assert any("自动尝试普通模式" in message for message in messages)


def test_smart_extraction_failure_reports_that_both_modes_were_attempted(tmp_path):
    class NoM3u8Extractor:
        def extract(self, task, *, deep, on_candidate, on_title, stop_event):
            if deep:
                raise RuntimeError("深度模式仍未找到任何 m3u8")
            raise RuntimeError("未找到任何 m3u8，可尝试 --deep 深度模式")

    service = TaskService(
        SQLiteTaskRepository(tmp_path / "tasks.db"), id_factory=lambda: "parent",
    )
    task = service.create_tasks(CreateTaskRequest(
        addresses="https://site.example/watch/42", save_directory=str(tmp_path),
    ))[0]

    with pytest.raises(RuntimeError, match="已依次尝试深度模式和普通模式"):
        TaskCoordinator(service, extractor=NoM3u8Extractor()).run_extraction(task.id)

    failed = service.get_task(task.id)
    assert failed.extraction_status is ExtractionStatus.FAILED
    assert "已依次尝试深度模式和普通模式" in failed.last_error
    assert "--deep" not in failed.last_error
    messages = [entry.message for entry in service.list_logs(task_id=task.id)]
    assert any("正在使用深度模式" in message for message in messages)
    assert any("自动尝试普通模式" in message for message in messages)


def test_smart_extraction_falls_back_when_deep_finds_nothing(tmp_path):
    class EmptyThenNormal(FallbackExtractor):
        def extract(self, task, *, deep, on_candidate, on_title, stop_event):
            self.calls.append(deep)
            if deep:
                return []
            on_candidate(Candidate("https://cdn.example/found.m3u8"))
            return []

    service = TaskService(
        SQLiteTaskRepository(tmp_path / "tasks.db"), id_factory=lambda: "parent"
    )
    task = service.create_tasks(CreateTaskRequest(
        addresses="https://site.example/watch/42", save_directory=str(tmp_path)
    ))[0]
    extractor = EmptyThenNormal()

    finished = TaskCoordinator(service, extractor=extractor).run_extraction(task.id)

    assert extractor.calls == [True, False]
    assert finished.download_status is DownloadStatus.WAITING


def test_smart_extraction_falls_back_when_deep_only_finds_invalid_items(tmp_path):
    class InvalidThenValid(FallbackExtractor):
        def extract(self, task, *, deep, on_candidate, on_title, stop_event):
            self.calls.append(deep)
            on_candidate(Candidate(
                "https://cdn.example/same.m3u8", valid=not deep
            ))
            return []

    service = TaskService(
        SQLiteTaskRepository(tmp_path / "tasks.db"), id_factory=lambda: "parent"
    )
    task = service.create_tasks(CreateTaskRequest(
        addresses="https://site.example/watch/42", save_directory=str(tmp_path)
    ))[0]
    extractor = InvalidThenValid()

    finished = TaskCoordinator(service, extractor=extractor).run_extraction(task.id)

    assert extractor.calls == [True, False]
    assert finished.download_status is DownloadStatus.WAITING
    assert service.list_items(task.id)[0].valid is True
