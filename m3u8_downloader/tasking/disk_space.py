"""下载前和下载中的磁盘空间门槛。"""

from dataclasses import dataclass
from pathlib import Path
import shutil


_FLOOR = 500 * 1024 ** 2


@dataclass(frozen=True)
class DiskSpaceDecision:
    can_start: bool
    free_bytes: int
    required_bytes: int


def _free_space(path) -> int:
    current = Path(path).resolve()
    while not current.exists() and current != current.parent:
        current = current.parent
    return shutil.disk_usage(current).free


class DiskSpaceGuard:
    def __init__(self, *, free_space=_free_space) -> None:
        self._free_space = free_space

    def check(self, directory, items) -> DiskSpaceDecision:
        estimates = [int(item.estimated_bytes or 0) for item in items]
        known_total = sum(estimates)
        required = known_total + known_total // 10 + _FLOOR if known_total else _FLOOR
        free = int(self._free_space(directory))
        return DiskSpaceDecision(free >= required, free, required)

