"""输出目录和自动文件名规划。"""

from pathlib import Path
import re

from .models import DownloadItem, Task


_INVALID = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def _safe_name(value: str, fallback: str) -> str:
    cleaned = _INVALID.sub("_", value).strip(" ._")
    return cleaned or fallback


class OutputPlanner:
    """只计算路径，不创建、移动或覆盖文件。"""

    def plan(self, task: Task, items: list[DownloadItem]) -> dict[str, Path]:
        base = Path(task.save_directory)
        task_name = _safe_name(task.name, "新建任务")
        multiple = len(items) > 1
        if multiple:
            folder_name = _safe_name(task.original_title, task_name)
            base = base / folder_name
        planned: dict[str, Path] = {}
        reserved: set[Path] = set()
        for item in items:
            if item.output_path:
                planned[item.id] = Path(item.output_path)
                reserved.add(Path(item.output_path))
                continue
            stem = task_name if not multiple else f"{task_name}_{item.output_index:02d}"
            candidate = base / f"{stem}.mp4"
            suffix = 1
            while candidate.exists() or candidate in reserved:
                candidate = base / f"{stem} ({suffix}).mp4"
                suffix += 1
            planned[item.id] = candidate
            reserved.add(candidate)
        return planned
