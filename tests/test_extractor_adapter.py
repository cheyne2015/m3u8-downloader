"""现有提取核心到新版任务模型的适配测试。"""

from types import SimpleNamespace

from m3u8_downloader.extractor_adapter_v2 import ExistingExtractorAdapter
from m3u8_downloader.tasking import (
    CreateTaskRequest,
    SQLiteTaskRepository,
    TaskService,
    TaskSettings,
)


def test_adapter_passes_task_network_settings_and_maps_candidate(tmp_path):
    captured = {}

    def fake_extract(url, **kwargs):
        captured.update(kwargs)
        candidate = SimpleNamespace(
            url="https://cdn.example/stream.m3u8",
            title="1080P",
            estimated_size=2048,
            duration=90.0,
            reachable=True,
        )
        kwargs["on_candidate"](candidate)
        return [candidate], "网页标题"

    settings = TaskSettings(
        proxy="127.0.0.1:7897",
        referer="https://site.example/",
        user_agent="测试浏览器",
    )
    service = TaskService(SQLiteTaskRepository(tmp_path / "tasks.db"))
    task = service.create_tasks(CreateTaskRequest(
        addresses="https://site.example/watch/42",
        save_directory=str(tmp_path),
        settings=settings,
    ))[0]
    streamed = []
    titles = []

    result = ExistingExtractorAdapter(extract_function=fake_extract).extract(
        task,
        deep=True,
        on_candidate=streamed.append,
        on_title=titles.append,
        stop_event=None,
    )

    assert captured["deep"] is True
    assert captured["proxy"] == "127.0.0.1:7897"
    assert captured["session"].headers["Referer"] == "https://site.example/"
    assert captured["session"].headers["User-Agent"] == "测试浏览器"
    assert titles == ["网页标题"]
    assert streamed == result
    assert result[0].estimated_bytes == 2048
    assert result[0].duration_seconds == 90.0
    assert result[0].valid is True

