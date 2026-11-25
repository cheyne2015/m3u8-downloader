from m3u8_downloader.tasking import CreateTaskRequest, SQLiteTaskRepository, TaskService
from m3u8_downloader.youtube import supported_video_url


def test_create_task_from_complete_douyin_share_text(tmp_path):
    service = TaskService(SQLiteTaskRepository(tmp_path / "tasks.db"))
    tasks = service.create_tasks(CreateTaskRequest(
        addresses=r"3.38 复制打开抖音，看看作品 https\://v.douyin.com/ZUW8si0UXko/ 11/01 口令",
        save_directory=str(tmp_path),
    ))
    assert [task.source_url for task in tasks] == ["https://v.douyin.com/ZUW8si0UXko/"]


def test_tiktok_video_and_short_links_route_to_media_downloader():
    assert supported_video_url("https://vm.tiktok.com/Zexample/") == "https://vm.tiktok.com/Zexample/"
    assert supported_video_url("https://www.tiktok.com/@author/video/123?share=1") == (
        "https://www.tiktok.com/@author/video/123"
    )
    assert supported_video_url("https://www.tiktok.com/@author") is None
