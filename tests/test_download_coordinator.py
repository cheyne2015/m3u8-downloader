"""父任务内部下载协调测试。"""

from pathlib import Path

from m3u8_downloader.tasking import (
    Candidate,
    CreateTaskRequest,
    DownloadCoordinator,
    DownloadStatus,
    ItemStatus,
    OutputPlanner,
    SQLiteTaskRepository,
    TaskService,
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
