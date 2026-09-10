"""磁盘空间启动门槛测试。"""

from types import SimpleNamespace

from m3u8_downloader.tasking import DiskSpaceGuard


def test_known_sizes_require_total_plus_ten_percent_and_500mb(tmp_path):
    gib = 1024 ** 3
    guard = DiskSpaceGuard(free_space=lambda _path: int(1.5 * gib))
    items = [SimpleNamespace(estimated_bytes=gib)]

    decision = guard.check(tmp_path, items)

    assert decision.required_bytes == int(1.1 * gib) + 500 * 1024 ** 2
    assert decision.can_start is False


def test_unknown_sizes_can_start_only_above_500mb_floor(tmp_path):
    guard = DiskSpaceGuard(free_space=lambda _path: 600 * 1024 ** 2)
    decision = guard.check(tmp_path, [SimpleNamespace(estimated_bytes=None)])
    assert decision.required_bytes == 500 * 1024 ** 2
    assert decision.can_start is True

