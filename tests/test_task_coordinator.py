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
    TaskSettings,
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


def _no_retry_delay(_event, _delay):
    return False


def test_smart_extraction_falls_back_streams_results_and_applies_threshold(tmp_path):
    repository = SQLiteTaskRepository(tmp_path / "tasks.db")
    service = TaskService(repository, id_factory=lambda: "parent")
    task = service.create_tasks(CreateTaskRequest(
        addresses="https://site.example/watch/42",
        save_directory=str(tmp_path),
    ))[0]
    extractor = FallbackExtractor()

    finished = TaskCoordinator(
        service, extractor=extractor, retry_wait=_no_retry_delay,
    ).run_extraction(task.id)

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
        TaskCoordinator(
            service, extractor=NoM3u8Extractor(), retry_wait=_no_retry_delay,
        ).run_extraction(task.id)

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

    finished = TaskCoordinator(
        service, extractor=extractor, retry_wait=_no_retry_delay,
    ).run_extraction(task.id)

    assert extractor.calls == [True, True, True, False]
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

    finished = TaskCoordinator(
        service, extractor=extractor, retry_wait=_no_retry_delay,
    ).run_extraction(task.id)

    assert extractor.calls == [True, True, True, False]
    assert finished.download_status is DownloadStatus.WAITING
    assert service.list_items(task.id)[0].valid is True


def test_deep_extraction_retries_twice_with_fresh_calls_before_succeeding(tmp_path):
    class FlakyExtractor:
        def __init__(self):
            self.calls = 0

        def extract(self, task, *, deep, on_candidate, on_title, stop_event):
            self.calls += 1
            if self.calls < 3:
                raise TimeoutError("网页临时加载超时")
            on_candidate(Candidate("https://cdn.example/recovered.m3u8"))
            return []

    waits = []
    service = TaskService(
        SQLiteTaskRepository(tmp_path / "tasks.db"), id_factory=lambda: "parent",
    )
    task = service.create_tasks(CreateTaskRequest(
        addresses="https://site.example/watch/42",
        save_directory=str(tmp_path),
        settings=TaskSettings(extraction_mode="deep"),
    ))[0]
    extractor = FlakyExtractor()

    finished = TaskCoordinator(
        service,
        extractor=extractor,
        retry_wait=lambda _event, delay: waits.append(delay) or False,
    ).run_extraction(task.id)

    assert extractor.calls == 3
    assert 2 <= waits[0] < 2.5
    assert 5 <= waits[1] < 5.5
    assert finished.extraction_status is ExtractionStatus.COMPLETED
    messages = [entry.message for entry in service.list_logs(task_id=task.id)]
    assert any("第 1 次自动重试" in message for message in messages)
    assert any("第 2 次自动重试" in message for message in messages)


def test_extraction_pauses_before_download_when_content_matches_another_link(tmp_path):
    ids = iter(["existing", "new"])
    service = TaskService(
        SQLiteTaskRepository(tmp_path / "tasks.db"), id_factory=ids.__next__,
    )

    existing = service.create_tasks(CreateTaskRequest(
        addresses="https://first.example/watch/1", save_directory=str(tmp_path),
    ))[0]
    service.apply_page_title(existing.id, "相同节目 - 站点甲")
    service.add_candidates(existing.id, [Candidate(
        "https://cdn-a.example/video.m3u8", duration_seconds=90,
        segment_count=18, bandwidth=2_000_000,
    )])
    service.finish_extraction(existing.id)
    service.record_content_identity(existing.id)

    new_task = service.create_tasks(CreateTaskRequest(
        addresses="https://second.example/watch/8", save_directory=str(tmp_path),
        settings=TaskSettings(extraction_mode="deep"),
    ))[0]

    class MatchingExtractor:
        def extract(self, task, *, deep, on_candidate, on_title, stop_event):
            on_title("相同节目 - 站点乙")
            on_candidate(Candidate(
                "https://cdn-b.example/video.m3u8", duration_seconds=90,
                segment_count=18, bandwidth=2_000_000,
            ))
            return []

    result = TaskCoordinator(
        service, extractor=MatchingExtractor(), retry_wait=_no_retry_delay,
        content_verifier=type("Verifier", (), {"matches": lambda *_args: True})(),
    ).run_extraction(new_task.id)

    assert result.download_status is DownloadStatus.PENDING_SELECTION
    assert result.last_error == f"疑似重复内容:{existing.id}"
    assert service.list_items(new_task.id)[0].status.value == "unselected"
