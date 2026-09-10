"""随包与系统 ffmpeg 的解析规则。"""

from pathlib import Path
import shutil
import sys


def resolve_ffmpeg_executable(executable_path=None, which=shutil.which) -> str:
    executable = Path(executable_path or sys.executable).resolve()
    for bundled in (
        executable.parent / "ffmpeg.exe",
        executable.parent / "_internal" / "ffmpeg.exe",
    ):
        if bundled.is_file():
            return str(bundled)
    return which("ffmpeg") or "ffmpeg"
