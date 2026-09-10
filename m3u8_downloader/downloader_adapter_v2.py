"""把现有下载核心接入新版父任务协调器。"""

from pathlib import Path

from .downloader import M3U8Downloader


class ExistingDownloaderAdapter:
    def __init__(self, *, downloader_factory=M3U8Downloader) -> None:
        self._factory = downloader_factory

    def download(self, task, item, output_path, *, stop_event, on_progress, on_log):
        settings = task.settings
        downloader = self._factory(
            url=item.source_url,
            output=str(output_path),
            workers=settings.segment_threads,
            max_retries=settings.request_retries,
            timeout=settings.timeout_seconds,
            proxy=settings.proxy or None,
            stop_event=stop_event,
            progress_callback=on_progress,
            log_callback=on_log,
        )
        result = downloader.download()
        return Path(result)

