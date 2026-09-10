"""任务删除与彻底删除边界测试。"""

from pathlib import Path

from m3u8_downloader.tasking import (
    CreateTaskRequest,
    SQLiteTaskRepository,
    TaskService,
)


def _completed_direct_task(tmp_path):
    repository = SQLiteTaskRepository(tmp_path / "tasks.db")
    service = TaskService(repository, id_factory=lambda: "task")
    task = service.create_tasks(CreateTaskRequest(
        addresses="https://cdn.example/video.m3u8",
        save_directory=str(tmp_path / "downloads"),
    ))[0]
    output = tmp_path / "downloads" / "video.mp4"
    output.parent.mkdir(parents=True)
    output.write_bytes(b"video-data")
    item = service.list_items(task.id)[0]
    service.complete_item(task.id, item.id, output)
    service.finish_parent_if_handled(task.id)
    return service, task, output


def test_delete_task_keeps_completed_output_but_removes_record(tmp_path):
    service, task, output = _completed_direct_task(tmp_path)

    preview = service.delete_task(task.id, delete_outputs=False)

    assert preview.output_files == (output,)
    assert output.is_file()
    assert service.list_tasks() == []


def test_permanent_delete_removes_only_exact_output_not_shared_directory(tmp_path):
    service, task, output = _completed_direct_task(tmp_path)
    unrelated = output.parent / "保留.txt"
    unrelated.write_text("keep", encoding="utf-8")

    preview = service.delete_task(task.id, delete_outputs=True)

    assert preview.total_bytes == len(b"video-data")
    assert not output.exists()
    assert unrelated.is_file()
    assert output.parent.is_dir()

