"""新版 GUI 专用启动入口。"""

from m3u8_downloader.gui_v2 import run_gui_v2
from m3u8_downloader.runtime_v2 import build_task_service

if __name__ == "__main__":
    raise SystemExit(run_gui_v2(build_task_service()))
