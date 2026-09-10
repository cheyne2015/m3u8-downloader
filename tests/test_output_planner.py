"""输出文件命名规则测试。"""

from m3u8_downloader.tasking import (
    Candidate,
    CreateTaskRequest,
    OutputPlanner,
    SQLiteTaskRepository,
    TaskService,
)


def test_single_m3u8_uses_renamed_parent_as_mp4_name(tmp_path):
    service = TaskService(SQLiteTaskRepository(tmp_path / "tasks.db"), id_factory=lambda: "task")
    task = service.create_tasks(CreateTaskRequest(
        addresses="https://cdn.example/video/master.m3u8",
        save_directory=str(tmp_path / "downloads"),
    ))[0]
    task = service.rename_task(task.id, "我的视频")

    paths = OutputPlanner().plan(task, service.list_items(task.id))

    assert list(paths.values()) == [tmp_path / "downloads" / "我的视频.mp4"]


def test_multiple_m3u8_use_original_title_folder_and_renamed_numbered_files(tmp_path):
    service = TaskService(SQLiteTaskRepository(tmp_path / "tasks.db"), id_factory=lambda: "task")
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
    task = service.get_task(task.id)
    folder = tmp_path / "downloads" / "网页标题"
    folder.mkdir(parents=True)
    (folder / "重命名_01.mp4").touch()

    paths = OutputPlanner().plan(task, service.list_items(task.id))

    assert list(paths.values()) == [
        folder / "重命名_01 (1).mp4",
        folder / "重命名_02.mp4",
    ]

