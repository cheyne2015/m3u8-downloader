"""父任务内部下载协调测试。"""

from pathlib import Path
from datetime import datetime, timedelta

import pytest

from m3u8_downloader.tasking import (
    Candidate,
    CreateTaskRequest,
    DownloadCoordinator,
    DownloadStatus,
    ItemStatus,
    OutputPlanner,
    SQLiteTaskRepository,
    TaskService,
    TaskSettings,
)


class RecordingDownloader:
    def __init__(self):
        self.calls = []

    def download(self, task, item, output_path, *, stop_event, on_progress, on_log):
        self.calls.append(item.source_url)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(item.source_url.encode("utf-8"))
        on_progress({"downloaded": output_path.stat().st_size, "total": output_path.stat().st_size})
        return output_path


@pytest.mark.parametrize('error', [RuntimeError('无法读取媒体'), ConnectionError('连接失败')])
def test_failed_middle_item_does_not_block_remaining_downloads(tmp_path, error):
    service = TaskService(SQLiteTaskRepository(tmp_path / 'tasks.db'))
    task = service.create_tasks(CreateTaskRequest(
        addresses='https://example.test/watch', save_directory=str(tmp_path),
        settings=TaskSettings(task_retries=0),
    ))[0]
    service.add_candidates(task.id, [Candidate(f'https://cdn.test/{i}.m3u8') for i in range(3)])
    service.finish_extraction(task.id)

    class FailingMiddle(RecordingDownloader):
        def download(self, task, item, output_path, **kwargs):
            if item.source_url == 'https://cdn.test/1.m3u8':
                self.calls.append(item.source_url)
                raise error
            return super().download(task, item, output_path, **kwargs)

    downloader = FailingMiddle()
    result = DownloadCoordinator(service, downloader=downloader).run_parent(task.id)
    assert downloader.calls == ['https://cdn.test/0.m3u8', 'https://cdn.test/1.m3u8',
                                'https://cdn.test/2.m3u8']
    assert [item.status for item in service.list_items(task.id)] == [
        ItemStatus.COMPLETED, ItemStatus.FAILED, ItemStatus.COMPLETED,
    ]
    assert result.download_status is DownloadStatus.PARTIAL_FAILURE


def test_restart_requeues_only_remaining_items_of_a_stalled_partial_failure(tmp_path):
    repository = SQLiteTaskRepository(tmp_path / 'tasks.db')
    service = TaskService(repository)
    task = service.create_tasks(CreateTaskRequest(
        addresses='https://example.test/watch', save_directory=str(tmp_path),
    ))[0]
    service.add_candidates(task.id, [Candidate(f'https://cdn.test/{i}.m3u8') for i in range(3)])
    service.finish_extraction(task.id)
    first, second, third = service.list_items(task.id)
    service.complete_item(task.id, first.id, tmp_path / 'first.mp4')
    service.fail_item(task.id, second.id, '失败')
    restored = TaskService(repository)
    restored.restore_tasks_after_restart()
    assert restored.get_task(task.id).download_status is DownloadStatus.WAITING
    downloader = RecordingDownloader()
    result = DownloadCoordinator(restored, downloader=downloader).run_parent(task.id)
    assert downloader.calls == [third.source_url]
    assert restored.get_item(task.id, second.id).status is ItemStatus.FAILED
    assert result.download_status is DownloadStatus.PARTIAL_FAILURE


@pytest.mark.parametrize('first_transient', [False, True])
def test_later_items_keep_their_own_retry_budget_after_an_earlier_failure(tmp_path, first_transient):
    now = [datetime(2026, 11, 4, 12)]
    service = TaskService(SQLiteTaskRepository(tmp_path / 'tasks.db'), clock=lambda: now[0])
    task = service.create_tasks(CreateTaskRequest(
        addresses='https://example.test/watch', save_directory=str(tmp_path),
        settings=TaskSettings(task_retries=1, retry_delay_seconds=30),
    ))[0]
    service.add_candidates(task.id, [Candidate(f'https://cdn.test/{i}.m3u8') for i in range(3)])
    service.finish_extraction(task.id)

    class FailingDownloader(RecordingDownloader):
        def download(self, task, item, output_path, **kwargs):
            attempts = self.calls.count(item.source_url)
            if item.source_url.endswith('/0.m3u8'):
                self.calls.append(item.source_url)
                raise ConnectionError('连接失败') if first_transient else RuntimeError('媒体无效')
            if item.source_url.endswith('/1.m3u8') and attempts == 0:
                self.calls.append(item.source_url)
                raise ConnectionError('暂时断线')
            return super().download(task, item, output_path, **kwargs)

    downloader = FailingDownloader()
    coordinator = DownloadCoordinator(service, downloader=downloader)
    for _ in range(2 if first_transient else 1):
        assert coordinator.run_parent(task.id).download_status is DownloadStatus.RETRY_WAIT
        now[0] += timedelta(seconds=30)
        assert [task.id for task in service.promote_due_retries()] == [task.id]
    result = coordinator.run_parent(task.id)
    assert downloader.calls.count('https://cdn.test/1.m3u8') == 2
    assert [item.status for item in service.list_items(task.id)] == [
        ItemStatus.FAILED, ItemStatus.COMPLETED, ItemStatus.COMPLETED,
    ]
    assert result.download_status is DownloadStatus.PARTIAL_FAILURE


def test_parent_downloads_selected_children_one_by_one_then_completes(tmp_path):
    repository = SQLiteTaskRepository(tmp_path / "tasks.db")
    service = TaskService(repository, id_factory=lambda: "parent")
    task = service.create_tasks(CreateTaskRequest(
        addresses="https://site.example/watch/42",
        save_directory=str(tmp_path / "downloads"),
    ))[0]
    service.apply_page_title(task.id, "网页标题")
    service.rename_task(task.id, "重命名")
    service.add_candidates(task.id, [
        Candidate("https://cdn.example/1.m3u8"),
        Candidate("https://cdn.example/2.m3u8"),
    ])
    service.finish_extraction(task.id)
    downloader = RecordingDownloader()

    finished = DownloadCoordinator(service, downloader=downloader).run_parent(task.id)

    assert downloader.calls == [
        "https://cdn.example/1.m3u8",
        "https://cdn.example/2.m3u8",
    ]
    assert finished.download_status is DownloadStatus.COMPLETED
    items = service.list_items(task.id)
    assert [item.status for item in items] == [ItemStatus.COMPLETED, ItemStatus.COMPLETED]
    assert [Path(item.output_path).name for item in items] == [
        "重命名_01.mp4", "重命名_02.mp4"
    ]
    assert all(Path(item.output_path).is_file() for item in items)


def test_completed_item_validation_repairs_through_temp_file_then_atomically_replaces(
    tmp_path, monkeypatch,
):
    service = TaskService(SQLiteTaskRepository(tmp_path / "tasks.db"))
    task = service.create_tasks(CreateTaskRequest(
        addresses="https://cdn.example/video.m3u8", save_directory=str(tmp_path),
    ))[0]
    item = service.list_items(task.id)[0]
    output = tmp_path / "video.mp4"
    output.write_bytes(b"old-broken-file")
    service.complete_item(task.id, item.id, output)
    service.finish_parent_if_handled(task.id)
    downloader = RecordingDownloader()
    checked = []

    def validate(path):
        checked.append(Path(path))
        if Path(path) == output:
            raise RuntimeError("文件无法解码")

    monkeypatch.setattr(
        "m3u8_downloader.tasking.download_coordinator.validate_media_file", validate,
    )
    coordinator = DownloadCoordinator(service, downloader=downloader)

    result = coordinator.validate_and_repair(task.id, item.id)

    assert result == "repaired"
    assert output.read_bytes() == item.source_url.encode("utf-8")
    assert checked[0] == output
    assert checked[1] != output
    assert checked[1].parent == output.parent
    assert not checked[1].exists()


def test_failed_completed_item_repair_preserves_the_original_file(tmp_path, monkeypatch):
    service = TaskService(SQLiteTaskRepository(tmp_path / "tasks.db"))
    task = service.create_tasks(CreateTaskRequest(
        addresses="https://cdn.example/video.m3u8", save_directory=str(tmp_path),
    ))[0]
    item = service.list_items(task.id)[0]
    output = tmp_path / "video.mp4"
    output.write_bytes(b"old-broken-file")
    service.complete_item(task.id, item.id, output)

    class FailedRepairDownloader:
        def download(self, task, item, output_path, **_kwargs):
            output_path.write_bytes(b"incomplete-repair")
            raise RuntimeError("重新下载失败")

    monkeypatch.setattr(
        "m3u8_downloader.tasking.download_coordinator.validate_media_file",
        lambda _path: (_ for _ in ()).throw(RuntimeError("文件无法解码")),
    )

    with pytest.raises(RuntimeError, match="重新下载失败"):
        DownloadCoordinator(
            service, downloader=FailedRepairDownloader(),
        ).validate_and_repair(task.id, item.id)

    assert output.read_bytes() == b"old-broken-file"
    assert not list(tmp_path.glob(".*.repair.mp4"))


def test_user_skipped_child_counts_as_handled_for_parent_completion(tmp_path):
    service = TaskService(SQLiteTaskRepository(tmp_path / "tasks.db"), id_factory=lambda: "parent")
    task = service.create_tasks(CreateTaskRequest(
        addresses="https://site.example/watch/42", save_directory=str(tmp_path)
    ))[0]
    service.add_candidates(task.id, [
        Candidate("https://cdn.example/1.m3u8"),
        Candidate("https://cdn.example/2.m3u8"),
    ])
    service.finish_extraction(task.id)
    service.prepare_output_paths(task.id, OutputPlanner())
    items = service.list_items(task.id)
    first_output = Path(items[0].output_path)
    first_output.parent.mkdir(parents=True)
    first_output.write_bytes(b"done")
    service.complete_item(task.id, items[0].id, first_output)

    finished = service.skip_item(task.id, items[1].id)

    assert finished.download_status is DownloadStatus.COMPLETED
    assert service.get_item(task.id, items[1].id).status is ItemStatus.SKIPPED


def test_transient_failure_waits_without_holding_download_slot(tmp_path):
    now = [datetime(2026, 9, 10, 12, 0, 0)]
    service = TaskService(
        SQLiteTaskRepository(tmp_path / "tasks.db"),
        clock=lambda: now[0],
        id_factory=lambda: "parent",
    )
    task = service.create_tasks(CreateTaskRequest(
        addresses="https://cdn.example/video.m3u8",
        save_directory=str(tmp_path),
        settings=TaskSettings(task_retries=1, retry_delay_seconds=30),
    ))[0]

    class FailingDownloader:
        def download(self, *_args, **_kwargs):
            raise ConnectionError("临时断线")

    waiting = DownloadCoordinator(service, downloader=FailingDownloader()).run_parent(task.id)

    assert waiting.download_status is DownloadStatus.RETRY_WAIT
    assert service.list_items(task.id)[0].status is ItemStatus.RETRY_WAIT
    assert service.promote_due_retries() == []

    now[0] += timedelta(seconds=30)
    promoted = service.promote_due_retries()

    assert [candidate.id for candidate in promoted] == [task.id]
    assert service.get_task(task.id).download_status is DownloadStatus.WAITING


def test_low_space_during_progress_pauses_parent(tmp_path):
    service = TaskService(
        SQLiteTaskRepository(tmp_path / "tasks.db"), id_factory=lambda: "parent"
    )
    task = service.create_tasks(CreateTaskRequest(
        addresses="https://cdn.example/video.m3u8", save_directory=str(tmp_path)
    ))[0]

    class ChangingGuard:
        def __init__(self):
            self.calls = 0

        def check(self, _directory, _items):
            from types import SimpleNamespace
            self.calls += 1
            return SimpleNamespace(
                can_start=self.calls == 1,
                free_bytes=100,
                required_bytes=500,
            )

    finished = DownloadCoordinator(
        service, downloader=RecordingDownloader(), disk_guard=ChangingGuard()
    ).run_parent(task.id)

    assert finished.download_status is DownloadStatus.PAUSED
    assert "磁盘空间不足" in finished.last_error


def test_failed_largest_same_duration_candidate_automatically_uses_backup(tmp_path):
    service = TaskService(
        SQLiteTaskRepository(tmp_path / "tasks.db"), id_factory=lambda: "parent"
    )
    task = service.create_tasks(CreateTaskRequest(
        addresses="https://site.example/watch/42", save_directory=str(tmp_path)
    ))[0]
    service.add_candidates(task.id, [
        Candidate(
            "https://cdn.example/smaller.m3u8",
            estimated_bytes=100,
            duration_seconds=60.2,
        ),
        Candidate(
            "https://cdn.example/largest.m3u8",
            estimated_bytes=900,
            duration_seconds=60.8,
        ),
    ])
    service.finish_extraction(task.id)

    class FallbackDownloader(RecordingDownloader):
        def download(self, task, item, output_path, **kwargs):
            self.calls.append(item.source_url)
            if "largest" in item.source_url:
                raise RuntimeError("媒体文件校验失败")
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_bytes(b"valid-backup")
            return output_path

    downloader = FallbackDownloader()
    finished = DownloadCoordinator(service, downloader=downloader).run_parent(task.id)

    assert downloader.calls == [
        "https://cdn.example/largest.m3u8",
        "https://cdn.example/smaller.m3u8",
    ]
    assert finished.download_status is DownloadStatus.COMPLETED
    completed = [item for item in service.list_items(task.id) if item.status is ItemStatus.COMPLETED]
    assert len(completed) == 1
    assert completed[0].source_url.endswith("smaller.m3u8")


def test_unreachable_same_duration_candidate_is_not_used_as_a_backup(tmp_path):
    service = TaskService(SQLiteTaskRepository(tmp_path / "tasks.db"))
    task = service.create_tasks(CreateTaskRequest(
        addresses="https://site.example/watch/42",
        save_directory=str(tmp_path),
        settings=TaskSettings(task_retries=0),
    ))[0]
    service.add_candidates(task.id, [
        Candidate(
            "https://cdn.example/primary.m3u8", estimated_bytes=2000,
            duration_seconds=60, valid=True,
        ),
        Candidate(
            "https://cdn.example/unreachable.m3u8", estimated_bytes=1000,
            duration_seconds=60, valid=False,
        ),
    ])
    service.finish_extraction(task.id)

    class AlwaysFails:
        def __init__(self):
            self.calls = []

        def download(self, task, item, output_path, **_kwargs):
            self.calls.append(item.source_url)
            raise RuntimeError("媒体数据损坏")

    downloader = AlwaysFails()
    result = DownloadCoordinator(service, downloader=downloader).run_parent(task.id)

    assert downloader.calls == ["https://cdn.example/primary.m3u8"]
    assert result.download_status is DownloadStatus.PARTIAL_FAILURE
    assert service.list_items(task.id)[1].valid is False
