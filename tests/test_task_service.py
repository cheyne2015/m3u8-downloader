"""新版任务管理公共接口测试。"""

from dataclasses import replace
from datetime import datetime
from pathlib import Path

import pytest

from m3u8_downloader.tasking import (
    Candidate,
    CreateTaskRequest,
    DownloadStatus,
    DuplicateSourceError,
    ExtractionStatus,
    ItemStatus,
    OutputPlanner,
    SelectionMode,
    SourceKind,
    SQLiteTaskRepository,
    TaskService,
)


def test_batch_queue_move_preserves_selected_relative_order(tmp_path):
    ids = iter(["one", "two", "three", "four"])
    service = TaskService(SQLiteTaskRepository(tmp_path / "tasks.db"), id_factory=ids.__next__)
    service.create_tasks(CreateTaskRequest(
        addresses=(
            "https://cdn.example/one.m3u8\n"
            "https://cdn.example/two.m3u8\n"
            "https://cdn.example/three.m3u8\n"
            "https://cdn.example/four.m3u8"
        ),
        save_directory=str(tmp_path),
    ))

    service.move_tasks(["two", "three"], "back")
    assert [task.id for task in service.list_tasks()] == ["one", "four", "two", "three"]

    service.move_tasks(["two", "three"], "front")
    assert [task.id for task in service.list_tasks()] == ["two", "three", "one", "four"]


def test_user_can_create_and_restore_page_and_m3u8_tasks(tmp_path):
    """多行新建应去重、识别来源，并从数据库完整恢复父任务。"""
    repository = SQLiteTaskRepository(tmp_path / "tasks.db")
    ids = iter(["task-page", "task-m3u8"])
    service = TaskService(
        repository,
        clock=lambda: datetime(2026, 9, 10, 12, 30, 0),
        id_factory=lambda: next(ids),
    )

    created = service.create_tasks(CreateTaskRequest(
        addresses="""
            https://site.example/watch/42
            https://cdn.example/video/master.m3u8?token=secret
            https://site.example/watch/42
        """,
        save_directory=str(tmp_path / "downloads"),
    ))

    assert [task.id for task in created] == ["task-page", "task-m3u8"]
    assert created[0].source_kind is SourceKind.WEB_PAGE
    assert created[0].extraction_status is ExtractionStatus.WAITING
    assert created[0].download_status is DownloadStatus.NOT_READY
    assert created[0].name == "正在获取标题"
    assert created[1].source_kind is SourceKind.DIRECT_M3U8
    assert created[1].extraction_status is ExtractionStatus.NOT_REQUIRED
    assert created[1].download_status is DownloadStatus.WAITING
    assert created[1].name == "video"
    assert [task.queue_position for task in created] == [1, 2]
    assert all(task.save_directory == str(tmp_path / "downloads") for task in created)
    assert all(task.settings.auto_download_threshold == 3 for task in created)
    assert all(task.settings.segment_threads == 8 for task in created)
    direct_items = repository.list_items("task-m3u8")
    assert len(direct_items) == 1
    assert direct_items[0].source_url == "https://cdn.example/video/master.m3u8?token=secret"
    assert direct_items[0].status is ItemStatus.WAITING

    reopened = SQLiteTaskRepository(tmp_path / "tasks.db")
    restored = reopened.list_tasks()
    assert restored == created


def test_page_title_before_site_separator_becomes_automatic_task_name(tmp_path):
    service = TaskService(
        SQLiteTaskRepository(tmp_path / "tasks.db"), id_factory=lambda: "page",
    )
    task = service.create_tasks(CreateTaskRequest(
        addresses="https://site.example/watch/42",
        save_directory=str(tmp_path),
    ))[0]

    updated = service.apply_page_title(
        task.id, "欢迎来龙餐馆 (2026) 在线观看 - 冷映",
    )

    assert updated.name == "欢迎来龙餐馆 (2026) 在线观看"
    assert updated.original_title == "欢迎来龙餐馆 (2026) 在线观看 - 冷映"


@pytest.mark.parametrize("separator", ["|", "｜"])
def test_pipe_separators_also_remove_the_site_name_from_automatic_task_name(
    tmp_path, separator,
):
    service = TaskService(
        SQLiteTaskRepository(tmp_path / "tasks.db"), id_factory=lambda: "page",
    )
    task = service.create_tasks(CreateTaskRequest(
        addresses="https://site.example/watch/42",
        save_directory=str(tmp_path),
    ))[0]

    updated = service.apply_page_title(task.id, f"影片名称 {separator} 视频网站")

    assert updated.name == "影片名称"


def test_manual_task_name_is_not_replaced_by_shortened_page_title(tmp_path):
    service = TaskService(
        SQLiteTaskRepository(tmp_path / "tasks.db"), id_factory=lambda: "page",
    )
    task = service.create_tasks(CreateTaskRequest(
        addresses="https://site.example/watch/42",
        save_directory=str(tmp_path),
    ))[0]
    service.rename_task(task.id, "用户名称")

    updated = service.apply_page_title(task.id, "网页名称 - 视频网站")

    assert updated.name == "用户名称"
    assert updated.original_title == "网页名称 - 视频网站"


def test_restart_pauses_interrupted_work_without_starting_network(tmp_path):
    """重启恢复只改变运行态，已完成和等待中的任务保持原状态。"""
    repository = SQLiteTaskRepository(tmp_path / "tasks.db")
    ids = iter(["extracting", "downloading", "merging", "retrying", "completed"])
    service = TaskService(repository, id_factory=lambda: next(ids))
    base = service.create_tasks(CreateTaskRequest(
        addresses="\n".join([
            "https://site.example/a",
            "https://cdn.example/b.m3u8",
            "https://cdn.example/c.m3u8",
            "https://cdn.example/d.m3u8",
            "https://cdn.example/e.m3u8",
        ]),
        save_directory=str(tmp_path),
    ))
    repository.save_many([
        replace(base[0], extraction_status=ExtractionStatus.RUNNING),
        replace(base[1], download_status=DownloadStatus.RUNNING),
        replace(base[2], download_status=DownloadStatus.MERGING),
        replace(base[3], download_status=DownloadStatus.RETRY_WAIT),
        replace(base[4], download_status=DownloadStatus.COMPLETED),
    ])

    restored = TaskService(repository).restore_tasks_after_restart()

    assert restored[0].extraction_status is ExtractionStatus.PAUSED
    assert restored[0].download_status is DownloadStatus.NOT_READY
    assert [task.download_status for task in restored[1:]] == [
        DownloadStatus.PAUSED,
        DownloadStatus.PAUSED,
        DownloadStatus.PAUSED,
        DownloadStatus.COMPLETED,
    ]
    assert repository.list_tasks() == restored


def test_finished_extraction_auto_queues_candidates_within_threshold(tmp_path):
    """最终有效候选不超过阈值时应全选，并把父任务送入下载队列。"""
    repository = SQLiteTaskRepository(tmp_path / "tasks.db")
    service = TaskService(repository, id_factory=iter([
        "parent", "item-1", "item-2"
    ]).__next__)
    task = service.create_tasks(CreateTaskRequest(
        addresses="https://site.example/watch/42",
        save_directory=str(tmp_path),
    ))[0]

    added = service.add_candidates(task.id, [
        Candidate("https://cdn.example/720.m3u8", label="720P", duration_seconds=60),
        Candidate("https://cdn.example/1080.m3u8", label="1080P", estimated_bytes=1000),
    ])
    finished = service.finish_extraction(task.id)

    assert [item.status for item in added] == [ItemStatus.UNSELECTED, ItemStatus.UNSELECTED]
    assert finished.extraction_status is ExtractionStatus.COMPLETED
    assert finished.download_status is DownloadStatus.WAITING
    assert [item.status for item in repository.list_items(task.id)] == [
        ItemStatus.WAITING,
        ItemStatus.WAITING,
    ]


def test_unreachable_candidates_do_not_count_toward_auto_download_threshold(tmp_path):
    repository = SQLiteTaskRepository(tmp_path / "tasks.db")
    service = TaskService(repository, id_factory=lambda: "parent")
    task = service.create_tasks(CreateTaskRequest(
        addresses="https://site.example/watch/42", save_directory=str(tmp_path)
    ))[0]
    service.add_candidates(task.id, [
        Candidate("https://cdn.example/1.m3u8"),
        Candidate("https://cdn.example/2.m3u8"),
        Candidate("https://cdn.example/3.m3u8"),
        Candidate("https://cdn.example/broken-1.m3u8", valid=False),
        Candidate("https://cdn.example/broken-2.m3u8", valid=False),
    ])

    finished = service.finish_extraction(task.id)

    assert finished.download_status is DownloadStatus.WAITING
    assert [item.status for item in service.list_items(task.id)] == [
        ItemStatus.WAITING,
        ItemStatus.WAITING,
        ItemStatus.WAITING,
        ItemStatus.UNSELECTED,
        ItemStatus.UNSELECTED,
    ]


def test_same_displayed_duration_keeps_only_largest_candidate_for_threshold(tmp_path):
    repository = SQLiteTaskRepository(tmp_path / "tasks.db")
    service = TaskService(repository, id_factory=lambda: "parent")
    task = service.create_tasks(CreateTaskRequest(
        addresses="https://site.example/watch/42", save_directory=str(tmp_path)
    ))[0]
    service.add_candidates(task.id, [
        Candidate("https://cdn.example/small.m3u8", estimated_bytes=100, duration_seconds=120.1),
        Candidate("https://cdn.example/largest.m3u8", estimated_bytes=500, duration_seconds=120.8),
        Candidate("https://cdn.example/medium.m3u8", estimated_bytes=300, duration_seconds=120.2),
        Candidate("https://cdn.example/other.m3u8", estimated_bytes=200, duration_seconds=240.0),
    ])

    finished = service.finish_extraction(task.id)
    items = {item.source_url.rsplit("/", 1)[-1]: item for item in service.list_items(task.id)}

    assert finished.download_status is DownloadStatus.WAITING
    assert items["largest.m3u8"].status is ItemStatus.WAITING
    assert items["other.m3u8"].status is ItemStatus.WAITING
    assert items["small.m3u8"].status is ItemStatus.UNSELECTED
    assert items["medium.m3u8"].status is ItemStatus.UNSELECTED
    assert items["small.m3u8"].valid is False
    assert items["medium.m3u8"].valid is False


def test_manual_selection_keeps_only_largest_same_duration_candidate(tmp_path):
    service = TaskService(SQLiteTaskRepository(tmp_path / "tasks.db"), id_factory=lambda: "parent")
    task = service.create_tasks(CreateTaskRequest(
        addresses="https://site.example/watch/42", save_directory=str(tmp_path)
    ))[0]
    service.start_extraction(task.id)
    added = service.add_candidates(task.id, [
        Candidate("https://cdn.example/small.m3u8", estimated_bytes=100, duration_seconds=60.1),
        Candidate("https://cdn.example/large.m3u8", estimated_bytes=900, duration_seconds=60.9),
    ])

    service.select_items_for_download(task.id, [item.id for item in added])
    items = {item.source_url.rsplit("/", 1)[-1]: item for item in service.list_items(task.id)}

    assert items["large.m3u8"].status is ItemStatus.WAITING
    assert items["small.m3u8"].status is ItemStatus.UNSELECTED
    assert items["small.m3u8"].valid is False


def test_unknown_duration_candidates_are_not_merged_for_auto_threshold(tmp_path):
    service = TaskService(SQLiteTaskRepository(tmp_path / "tasks.db"), id_factory=lambda: "parent")
    task = service.create_tasks(CreateTaskRequest(
        addresses="https://site.example/watch/42", save_directory=str(tmp_path)
    ))[0]
    service.add_candidates(task.id, [
        Candidate("https://cdn.example/unknown-1.m3u8", estimated_bytes=100),
        Candidate("https://cdn.example/unknown-2.m3u8", estimated_bytes=900),
        Candidate("https://cdn.example/zero.m3u8", estimated_bytes=500, duration_seconds=0),
    ])

    finished = service.finish_extraction(task.id)
    items = service.list_items(task.id)

    assert finished.download_status is DownloadStatus.WAITING
    assert all(item.valid for item in items)
    assert all(item.status is ItemStatus.WAITING for item in items)


def test_manual_selection_can_download_while_extraction_keeps_running(tmp_path):
    """手动接管后只下载所选项，后续流式候选保持未选择。"""
    repository = SQLiteTaskRepository(tmp_path / "tasks.db")
    service = TaskService(repository, id_factory=iter([
        "parent", "item-1", "item-2", "item-3"
    ]).__next__)
    task = service.create_tasks(CreateTaskRequest(
        addresses="https://site.example/watch/42",
        save_directory=str(tmp_path),
    ))[0]
    service.start_extraction(task.id)
    first_items = service.add_candidates(task.id, [
        Candidate("https://cdn.example/1.m3u8"),
        Candidate("https://cdn.example/2.m3u8"),
    ])

    downloading = service.select_items_for_download(task.id, [first_items[0].id])
    service.add_candidates(task.id, [Candidate("https://cdn.example/3.m3u8")])
    finished = service.finish_extraction(task.id)

    assert downloading.extraction_status is ExtractionStatus.RUNNING
    assert downloading.download_status is DownloadStatus.WAITING
    assert downloading.selection_mode is SelectionMode.MANUAL
    assert finished.extraction_status is ExtractionStatus.COMPLETED
    assert [item.status for item in repository.list_items(task.id)] == [
        ItemStatus.WAITING,
        ItemStatus.UNSELECTED,
        ItemStatus.UNSELECTED,
    ]


def test_pause_and_resume_parent_release_and_restore_runnable_work(tmp_path):
    repository = SQLiteTaskRepository(tmp_path / "tasks.db")
    service = TaskService(repository, id_factory=lambda: "parent")
    task = service.create_tasks(CreateTaskRequest(
        addresses="https://site.example/watch/42", save_directory=str(tmp_path)
    ))[0]
    service.start_extraction(task.id)
    items = service.add_candidates(task.id, [Candidate("https://cdn.example/1.m3u8")])
    service.select_items_for_download(task.id, [items[0].id])
    service.start_item(task.id, items[0].id)

    paused = service.pause_task(task.id)

    assert paused.extraction_status is ExtractionStatus.PAUSED
    assert paused.download_status is DownloadStatus.PAUSED
    assert service.get_item(task.id, items[0].id).status is ItemStatus.PAUSED

    resumed = service.resume_task(task.id)

    assert resumed.extraction_status is ExtractionStatus.WAITING
    assert resumed.download_status is DownloadStatus.WAITING
    assert service.get_item(task.id, items[0].id).status is ItemStatus.WAITING


def test_existing_source_requires_explicit_duplicate_creation(tmp_path):
    repository = SQLiteTaskRepository(tmp_path / "tasks.db")
    service = TaskService(repository)
    request = CreateTaskRequest(
        addresses="https://cdn.example/video.m3u8", save_directory=str(tmp_path)
    )
    original = service.create_tasks(request)[0]

    with pytest.raises(DuplicateSourceError) as raised:
        service.create_tasks(request)

    assert raised.value.existing_task_ids == (original.id,)
    duplicate = service.create_tasks(request, allow_duplicates=True)[0]
    assert duplicate.id != original.id
    assert duplicate.queue_position == 2


def test_duplicate_error_lists_most_recent_matching_task_first(tmp_path):
    repository = SQLiteTaskRepository(tmp_path / "tasks.db")
    moments = iter([
        datetime(2026, 9, 12, 9, 0, 0),
        datetime(2026, 9, 12, 9, 0, 0),
    ])
    service = TaskService(
        repository,
        clock=moments.__next__,
        id_factory=iter(["older", "newer"]).__next__,
    )
    request = CreateTaskRequest(
        addresses="https://cdn.example/video.m3u8", save_directory=str(tmp_path)
    )
    service.create_tasks(request)
    service.create_tasks(request, allow_duplicates=True)

    with pytest.raises(DuplicateSourceError) as raised:
        service.create_tasks(request)

    assert raised.value.existing_task_ids == ("newer", "older")


def test_stop_extraction_applies_threshold_to_current_candidates(tmp_path):
    repository = SQLiteTaskRepository(tmp_path / "tasks.db")
    service = TaskService(repository, id_factory=lambda: "parent")
    task = service.create_tasks(CreateTaskRequest(
        addresses="https://site.example/watch/42", save_directory=str(tmp_path)
    ))[0]
    service.start_extraction(task.id)
    service.add_candidates(task.id, [
        Candidate("https://cdn.example/1.m3u8"),
        Candidate("https://cdn.example/2.m3u8"),
    ])

    stopped = service.stop_extraction(task.id)

    assert stopped.extraction_status is ExtractionStatus.COMPLETED
    assert stopped.download_status is DownloadStatus.WAITING
    assert all(item.status is ItemStatus.WAITING for item in service.list_items(task.id))


def test_retry_failed_items_returns_parent_to_download_queue(tmp_path):
    repository = SQLiteTaskRepository(tmp_path / "tasks.db")
    service = TaskService(repository, id_factory=lambda: "parent")
    task = service.create_tasks(CreateTaskRequest(
        addresses="https://cdn.example/video.m3u8", save_directory=str(tmp_path)
    ))[0]
    item = service.list_items(task.id)[0]
    service.fail_item(task.id, item.id, "断线")

    retried = service.retry_failed_items(task.id)

    assert retried.download_status is DownloadStatus.WAITING
    assert retried.last_error == ""
    assert service.get_item(task.id, item.id).status is ItemStatus.WAITING


def test_redownload_item_creates_independent_parent_copy(tmp_path):
    repository = SQLiteTaskRepository(tmp_path / "tasks.db")
    task_ids = iter(["original", "copy"])
    item_ids = iter(["original-item", "copy-item"])
    service = TaskService(
        repository,
        id_factory=lambda: next(task_ids),
        item_id_factory=lambda: next(item_ids),
    )
    task = service.create_tasks(CreateTaskRequest(
        addresses="https://cdn.example/video.m3u8", save_directory=str(tmp_path)
    ))[0]
    original_item = service.list_items(task.id)[0]
    output = tmp_path / "video.mp4"
    output.write_bytes(b"done")
    service.complete_item(task.id, original_item.id, output)
    service.finish_parent_if_handled(task.id)

    copied = service.redownload_item(task.id, original_item.id)

    assert copied.id == "copy"
    assert copied.download_status is DownloadStatus.WAITING
    assert service.get_task(task.id).download_status is DownloadStatus.COMPLETED
    assert service.get_item(task.id, original_item.id).output_path == str(output)
    copy_item = service.list_items(copied.id)[0]
    assert copy_item.id == "copy-item"
    assert copy_item.source_url == original_item.source_url
    assert copy_item.status is ItemStatus.WAITING


def test_redownload_web_task_reuses_selected_items_for_fast_path(tmp_path):
    repository = SQLiteTaskRepository(tmp_path / "tasks.db")
    task_ids = iter(["original", "copy"])
    service = TaskService(repository, id_factory=task_ids.__next__)
    task = service.create_tasks(CreateTaskRequest(
        addresses="https://site.example/watch/42", save_directory=str(tmp_path)
    ))[0]
    service.start_extraction(task.id)
    service.add_candidates(task.id, [Candidate(
        "https://cdn.example/video.m3u8?expires=old"
    )])
    service.finish_extraction(task.id)

    copied = service.redownload_task(task.id)

    assert copied.id == "copy"
    assert copied.source_url == task.source_url
    assert copied.source_kind is SourceKind.WEB_PAGE
    assert copied.extraction_status is ExtractionStatus.NOT_REQUIRED
    assert copied.download_status is DownloadStatus.WAITING
    copied_items = service.list_items(copied.id)
    assert len(copied_items) == 1
    assert copied_items[0].source_url.endswith("?expires=old")


def test_reextract_web_task_refreshes_page_instead_of_reusing_signed_items(tmp_path):
    repository = SQLiteTaskRepository(tmp_path / "tasks.db")
    task_ids = iter(["original", "copy"])
    service = TaskService(repository, id_factory=task_ids.__next__)
    task = service.create_tasks(CreateTaskRequest(
        addresses="https://site.example/watch/42", save_directory=str(tmp_path)
    ))[0]
    service.start_extraction(task.id)
    service.add_candidates(task.id, [Candidate(
        "https://cdn.example/video.m3u8?expires=old"
    )])
    service.finish_extraction(task.id)

    copied = service.reextract_task(task.id)

    assert copied.id == "copy"
    assert copied.source_url == task.source_url
    assert copied.source_kind is SourceKind.WEB_PAGE
    assert copied.extraction_status is ExtractionStatus.WAITING
    assert copied.download_status is DownloadStatus.NOT_READY
    assert copied.selection_mode is SelectionMode.AUTO
    assert copied.settings.allow_content_duplicate is True
    assert service.list_items(copied.id) == []


def test_expired_fast_redownload_automatically_falls_back_to_reextracting_page(tmp_path):
    repository = SQLiteTaskRepository(tmp_path / "tasks.db")
    task_ids = iter(["original", "copy"])
    service = TaskService(repository, id_factory=task_ids.__next__)
    task = service.create_tasks(CreateTaskRequest(
        addresses="https://site.example/watch/42", save_directory=str(tmp_path)
    ))[0]
    service.start_extraction(task.id)
    service.add_candidates(task.id, [Candidate(
        "https://cdn.example/video.m3u8?expires=old"
    )])
    service.finish_extraction(task.id)
    copied = service.redownload_task(task.id)
    copied_item = service.list_items(copied.id)[0]

    refreshed = service.fail_item(
        copied.id, copied_item.id, "400 Client Error: Bad Request"
    )

    assert refreshed.extraction_status is ExtractionStatus.WAITING
    assert refreshed.download_status is DownloadStatus.NOT_READY
    assert refreshed.selection_mode is SelectionMode.AUTO
    assert refreshed.settings.allow_content_duplicate is True
    assert service.list_items(copied.id) == []


def test_retry_failed_legacy_web_redownload_refreshes_expired_candidates(tmp_path):
    repository = SQLiteTaskRepository(tmp_path / "tasks.db")
    service = TaskService(repository, id_factory=lambda: "legacy-copy")
    task = service.create_tasks(CreateTaskRequest(
        addresses="https://site.example/watch/42", save_directory=str(tmp_path)
    ))[0]
    service.start_extraction(task.id)
    items = service.add_candidates(task.id, [Candidate(
        "https://cdn.example/video.m3u8?expires=old"
    )])
    service.finish_extraction(task.id)
    repository.save_items([replace(items[0], status=ItemStatus.FAILED)])
    repository.save_many([replace(
        service.get_task(task.id),
        extraction_status=ExtractionStatus.NOT_REQUIRED,
        download_status=DownloadStatus.PARTIAL_FAILURE,
        last_error="400 Client Error",
    )])

    retried = service.retry_failed_items(task.id)

    assert retried.extraction_status is ExtractionStatus.WAITING
    assert retried.download_status is DownloadStatus.NOT_READY
    assert retried.last_error == ""
    assert retried.selection_mode is SelectionMode.AUTO
    assert retried.settings.allow_content_duplicate is True
    assert service.list_items(task.id) == []


def test_rename_output_file_changes_disk_and_persisted_path(tmp_path):
    repository = SQLiteTaskRepository(tmp_path / "tasks.db")
    service = TaskService(repository, id_factory=lambda: "parent")
    task = service.create_tasks(CreateTaskRequest(
        addresses="https://cdn.example/video.m3u8", save_directory=str(tmp_path)
    ))[0]
    item = service.list_items(task.id)[0]
    output = tmp_path / "video.mp4"
    output.write_bytes(b"done")
    service.complete_item(task.id, item.id, output)

    renamed = service.rename_output_file(task.id, item.id, "新名称")

    assert renamed.output_path == str(tmp_path / "新名称.mp4")
    assert not output.exists()
    assert Path(renamed.output_path).read_bytes() == b"done"


def test_pause_one_item_keeps_other_selected_items_runnable(tmp_path):
    repository = SQLiteTaskRepository(tmp_path / "tasks.db")
    service = TaskService(repository, id_factory=lambda: "parent")
    task = service.create_tasks(CreateTaskRequest(
        addresses="https://site.example/watch/42", save_directory=str(tmp_path)
    ))[0]
    items = service.add_candidates(task.id, [
        Candidate("https://cdn.example/1.m3u8"),
        Candidate("https://cdn.example/2.m3u8"),
    ])
    service.finish_extraction(task.id)
    service.start_item(task.id, items[0].id)

    paused = service.pause_item(task.id, items[0].id)

    assert paused.download_status is DownloadStatus.WAITING
    assert [item.status for item in service.list_items(task.id)] == [
        ItemStatus.PAUSED, ItemStatus.WAITING,
    ]

    service.resume_item(task.id, items[0].id)
    assert all(item.status is ItemStatus.WAITING for item in service.list_items(task.id))


def test_stop_extraction_completes_parent_when_selected_download_already_finished(tmp_path):
    service = TaskService(
        SQLiteTaskRepository(tmp_path / "tasks.db"), id_factory=lambda: "parent"
    )
    task = service.create_tasks(CreateTaskRequest(
        addresses="https://site.example/watch/42", save_directory=str(tmp_path)
    ))[0]
    service.start_extraction(task.id)
    item = service.add_candidates(
        task.id, [Candidate("https://cdn.example/1.m3u8")]
    )[0]
    service.select_items_for_download(task.id, [item.id])
    output = tmp_path / "done.mp4"; output.write_bytes(b"done")
    service.complete_item(task.id, item.id, output)
    assert service.finish_parent_if_handled(task.id).download_status is DownloadStatus.RUNNING

    stopped = service.stop_extraction(task.id)

    assert stopped.download_status is DownloadStatus.COMPLETED


def test_late_title_replans_only_unstarted_outputs(tmp_path):
    service = TaskService(
        SQLiteTaskRepository(tmp_path / "tasks.db"), id_factory=lambda: "parent"
    )
    task = service.create_tasks(CreateTaskRequest(
        addresses="https://site.example/watch/42", save_directory=str(tmp_path)
    ))[0]
    items = service.add_candidates(task.id, [
        Candidate("https://cdn.example/1.m3u8"), Candidate("https://cdn.example/2.m3u8")
    ])
    service.finish_extraction(task.id)
    service.prepare_output_paths(task.id, OutputPlanner())
    service.start_item(task.id, items[0].id)
    before = service.list_items(task.id)

    service.apply_page_title(task.id, "晚到标题")
    after = service.list_items(task.id)

    assert after[0].output_path == before[0].output_path
    assert after[1].output_path == ""


def test_selecting_second_item_moves_completed_single_file_into_numbered_folder(tmp_path):
    service = TaskService(
        SQLiteTaskRepository(tmp_path / "tasks.db"), id_factory=lambda: "parent"
    )
    task = service.create_tasks(CreateTaskRequest(
        addresses="https://site.example/watch/42",
        save_directory=str(tmp_path / "downloads"),
    ))[0]
    service.apply_page_title(task.id, "网页标题")
    service.rename_task(task.id, "任务名称")
    items = service.add_candidates(task.id, [
        Candidate("https://cdn.example/first.m3u8"),
        Candidate("https://cdn.example/second.m3u8"),
    ])
    service.finish_extraction(task.id)
    service.select_items_for_download(task.id, [items[0].id])
    planned = service.prepare_output_paths(task.id, OutputPlanner())
    first_path = Path(next(item for item in planned if item.id == items[0].id).output_path)
    first_path.parent.mkdir(parents=True, exist_ok=True)
    first_path.write_bytes(b"finished-video")
    service.complete_item(task.id, items[0].id, first_path)

    service.select_items_for_download(task.id, [items[1].id])
    replanned = service.prepare_output_paths(task.id, OutputPlanner())
    first = next(item for item in replanned if item.id == items[0].id)
    second = next(item for item in replanned if item.id == items[1].id)

    folder = tmp_path / "downloads" / "网页标题"
    assert Path(first.output_path) == folder / "任务名称_01.mp4"
    assert Path(first.output_path).read_bytes() == b"finished-video"
    assert not first_path.exists()
    assert Path(second.output_path) == folder / "任务名称_02.mp4"


def test_different_links_with_same_title_and_media_structure_are_content_duplicates(tmp_path):
    ids = iter(["first", "second", "different"])
    service = TaskService(
        SQLiteTaskRepository(tmp_path / "tasks.db"), id_factory=ids.__next__,
    )

    def finish(address, segment_count):
        task = service.create_tasks(CreateTaskRequest(
            addresses=address, save_directory=str(tmp_path),
        ))[0]
        service.apply_page_title(task.id, "同一个网页标题 - 站点")
        service.add_candidates(task.id, [Candidate(
            f"https://cdn.example/{task.id}.m3u8",
            estimated_bytes=1000,
            duration_seconds=120.4,
            segment_count=segment_count,
        )])
        service.finish_extraction(task.id)
        service.record_content_identity(task.id)
        return task

    first = finish("https://site-a.example/watch/1", 30)
    second = finish("https://site-b.example/watch/9", 30)
    different = finish("https://site-c.example/watch/2", 31)

    assert service.find_content_duplicate(second.id).id == first.id
    assert service.find_content_duplicate(different.id) is None


def test_reextracting_a_task_clears_its_old_content_identity(tmp_path):
    ids = iter(["first", "second"])
    service = TaskService(
        SQLiteTaskRepository(tmp_path / "tasks.db"), id_factory=ids.__next__,
    )
    tasks = []
    for address in ("https://site-a.example/1", "https://site-b.example/2"):
        task = service.create_tasks(CreateTaskRequest(
            addresses=address, save_directory=str(tmp_path),
        ))[0]
        service.apply_page_title(task.id, "相同标题 - 站点")
        service.add_candidates(task.id, [Candidate(
            f"https://cdn.example/{task.id}.m3u8",
            duration_seconds=60, segment_count=12,
        )])
        service.finish_extraction(task.id)
        service.record_content_identity(task.id)
        tasks.append(task)
    assert service.find_content_duplicate(tasks[1].id).id == tasks[0].id

    service.retry_extraction(tasks[0].id)

    assert service.find_content_duplicate(tasks[1].id) is None
