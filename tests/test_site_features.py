from m3u8_downloader.resource_stats import RuntimeStatisticsTracker
from m3u8_downloader.tasking import (
    Candidate,
    CreateTaskRequest,
    SQLiteTaskRepository,
    TaskCoordinator,
    TaskService,
    TaskSettings,
)


def _service(tmp_path):
    return TaskService(SQLiteTaskRepository(tmp_path / "tasks.db"))


def test_saved_site_profile_applies_only_extraction_settings(tmp_path):
    service = _service(tmp_path)
    profile = TaskSettings(
        extraction_mode="normal", proxy="http://127.0.0.1:7890",
        referer="https://video.example/", user_agent="站点浏览器标识",
        protected_cookie="encrypted", timeout_seconds=75, request_retries=6,
        segment_threads=20, speed_limit=1024,
    )
    service.save_site_profile("https://www.video.example/watch/1", profile)

    defaults = TaskSettings(segment_threads=7, speed_limit=4096)
    matching = service.create_tasks(CreateTaskRequest(
        addresses="https://www.video.example/watch/2",
        save_directory=str(tmp_path), settings=defaults,
    ))[0]
    other = service.create_tasks(CreateTaskRequest(
        addresses="https://other.example/watch/2",
        save_directory=str(tmp_path), settings=defaults,
    ))[0]

    assert matching.settings.extraction_mode == "normal"
    assert matching.settings.proxy == "http://127.0.0.1:7890"
    assert matching.settings.timeout_seconds == 75
    assert matching.settings.request_retries == 6
    assert matching.settings.segment_threads == 7
    assert matching.settings.speed_limit == 4096
    assert other.settings == defaults
    assert service.list_site_profiles()[0].hostname == "www.video.example"


def test_site_compatibility_accumulates_final_results(tmp_path):
    service = _service(tmp_path)
    task = service.create_tasks(CreateTaskRequest(
        addresses="https://video.example/watch/1",
        save_directory=str(tmp_path), settings=TaskSettings(),
    ))[0]

    service.record_site_extraction(
        task.id, success=True, effective_mode="deep", elapsed_seconds=2.5,
        candidate_count=3,
    )
    service.record_site_extraction(
        task.id, success=False, effective_mode="normal", elapsed_seconds=1.5,
        candidate_count=0, error="未找到链接",
    )

    record = service.list_site_compatibility()[0]
    assert record.hostname == "video.example"
    assert record.attempts == 2
    assert record.successes == 1
    assert record.failures == 1
    assert record.deep_successes == 1
    assert record.normal_successes == 0
    assert record.last_result == "失败"
    assert record.last_error == "未找到链接"
    assert record.average_elapsed_seconds == 2.0


def test_task_coordinator_records_the_effective_successful_mode(tmp_path):
    service = _service(tmp_path)
    task = service.create_tasks(CreateTaskRequest(
        addresses="https://video.example/watch/3", save_directory=str(tmp_path),
        settings=TaskSettings(extraction_mode="deep"),
    ))[0]

    class Extractor:
        def extract(self, _task, **kwargs):
            kwargs["on_candidate"](Candidate("https://cdn.example/video.m3u8"))
            return []

    TaskCoordinator(service, extractor=Extractor()).run_extraction(task.id)

    record = service.list_site_compatibility()[0]
    assert record.attempts == 1
    assert record.successes == 1
    assert record.last_mode == "deep"
    assert record.last_candidate_count == 1
    profile = service.list_site_profiles()[0]
    assert profile.extraction_mode == "deep"
    assert profile.preferred_extraction_mode == "deep"


def test_smart_site_learning_reuses_parameters_and_prefers_last_successful_mode(tmp_path):
    service = _service(tmp_path)
    task = service.create_tasks(CreateTaskRequest(
        addresses="https://video.example/watch/4", save_directory=str(tmp_path),
        settings=TaskSettings(
            extraction_mode="smart", proxy="http://127.0.0.1:7890",
            timeout_seconds=88, segment_threads=5,
        ),
    ))[0]

    service.record_site_extraction(
        task.id, success=True, effective_mode="normal", elapsed_seconds=1,
        candidate_count=1,
    )
    learned = service.list_site_profiles()[0]
    assert learned.extraction_mode == "smart"
    assert learned.preferred_extraction_mode == "normal"

    next_task = service.create_tasks(CreateTaskRequest(
        addresses="https://video.example/watch/5", save_directory=str(tmp_path),
        settings=TaskSettings(segment_threads=9),
    ))[0]
    assert next_task.settings.extraction_mode == "smart"
    assert next_task.settings.preferred_extraction_mode == "normal"
    assert next_task.settings.proxy == "http://127.0.0.1:7890"
    assert next_task.settings.timeout_seconds == 88
    assert next_task.settings.segment_threads == 9


def test_failed_extraction_does_not_replace_successful_site_profile(tmp_path):
    service = _service(tmp_path)
    task = service.create_tasks(CreateTaskRequest(
        addresses="https://video.example/watch/6", save_directory=str(tmp_path),
        settings=TaskSettings(extraction_mode="smart", timeout_seconds=66),
    ))[0]
    service.record_site_extraction(
        task.id, success=True, effective_mode="deep", elapsed_seconds=1,
        candidate_count=1,
    )
    service.update_task_settings(task.id, TaskSettings(timeout_seconds=10))
    service.record_site_extraction(
        task.id, success=False, effective_mode="normal", elapsed_seconds=2,
        candidate_count=0, error="失败",
    )

    profile = service.list_site_profiles()[0]
    assert profile.timeout_seconds == 66
    assert profile.preferred_extraction_mode == "deep"


def test_smart_coordinator_uses_learned_mode_first_and_keeps_fallback(tmp_path):
    service = _service(tmp_path)
    seed = service.create_tasks(CreateTaskRequest(
        addresses="https://video.example/seed", save_directory=str(tmp_path),
        settings=TaskSettings(extraction_mode="smart"),
    ))[0]
    service.record_site_extraction(
        seed.id, success=True, effective_mode="normal", elapsed_seconds=1,
        candidate_count=1,
    )
    task = service.create_tasks(CreateTaskRequest(
        addresses="https://video.example/watch/7", save_directory=str(tmp_path),
    ))[0]
    calls = []

    class Extractor:
        def extract(self, _task, **kwargs):
            calls.append(kwargs["deep"])
            if kwargs["deep"]:
                kwargs["on_candidate"](Candidate("https://cdn.example/video.m3u8"))
            return []

    TaskCoordinator(
        service, extractor=Extractor(), retry_wait=lambda *_: False,
    ).run_extraction(task.id)

    assert calls[:3] == [False, False, False]
    assert calls[3] is True


def test_runtime_statistics_tracks_speed_session_bytes_and_resources():
    samples = iter([(100.0, 200), (130.0, 260), (150.0, 250)])
    tracker = RuntimeStatisticsTracker(resource_provider=lambda: next(samples))

    first = tracker.update(
        total_downloaded_bytes=1000, speed_bps=12.0,
        active_downloads=2, active_extractions=1,
    )
    second = tracker.update(
        total_downloaded_bytes=1600, speed_bps=20.0,
        active_downloads=1, active_extractions=0,
    )
    third = tracker.update(
        total_downloaded_bytes=200, speed_bps=0.0,
        active_downloads=0, active_extractions=0,
    )

    assert first.session_downloaded_bytes == 0
    assert second.session_downloaded_bytes == 600
    assert third.session_downloaded_bytes == 600
    assert second.cpu_percent == 130.0
    assert second.memory_bytes == 260
    assert second.active_downloads == 1
