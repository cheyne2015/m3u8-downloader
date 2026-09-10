"""Windows 单实例通信测试。"""

import uuid

from m3u8_downloader.windows_v2 import SingleInstanceGuard


def test_second_instance_reports_existing_process(qtbot):
    key = "m3u8-downloader-test-" + uuid.uuid4().hex
    first = SingleInstanceGuard(key)
    second = SingleInstanceGuard(key)
    try:
        assert first.acquire() is True
        assert second.acquire() is False
    finally:
        second.close()
        first.close()

