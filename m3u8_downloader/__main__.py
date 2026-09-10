"""支持 python -m m3u8_downloader 方式运行。"""

import sys

from m3u8_downloader.cli import main


if __name__ == "__main__":
    if "--gui" in sys.argv or len(sys.argv) == 1:
        from m3u8_downloader.gui import run_gui
        run_gui()
    else:
        main()
