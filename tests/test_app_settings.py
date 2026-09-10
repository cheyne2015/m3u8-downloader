"""应用设置的持久化和边界测试。"""

from dataclasses import replace

import pytest

from m3u8_downloader.tasking import AppSettings, SQLiteTaskRepository


def test_app_settings_have_confirmed_defaults_and_persist(tmp_path):
    repository = SQLiteTaskRepository(tmp_path / "tasks.db")
    defaults = repository.load_app_settings()

    assert defaults == AppSettings()
    assert defaults.download_task_limit == 3
    assert defaults.extraction_task_limit == 3
    assert defaults.auto_download_threshold == 3
    assert defaults.segment_threads == 8
    assert defaults.completion_notification is False

    changed = replace(
        defaults,
        download_task_limit=6,
        auto_download_threshold=10,
        theme="system",
        completion_notification=True,
    )
    repository.save_app_settings(changed)
    assert SQLiteTaskRepository(tmp_path / "tasks.db").load_app_settings() == changed


@pytest.mark.parametrize("field,value", [
    ("download_task_limit", 7),
    ("extraction_task_limit", 0),
    ("auto_download_threshold", 21),
    ("segment_threads", 33),
    ("task_retries", 6),
])
def test_app_settings_reject_values_outside_confirmed_ranges(field, value):
    with pytest.raises(ValueError):
        AppSettings(**{field: value})

