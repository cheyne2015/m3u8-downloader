"""把现有下载核心接入新版父任务协调器。"""

from pathlib import Path

from .downloader import M3U8Downloader, PlaylistFetchError, _SpeedLimiter
from .secrets_v2 import unprotect_secret


class _SharedGlobalSpeedPool:
    def __init__(self, limit: int) -> None:
        self._limiter = _SpeedLimiter(limit)

    def acquire(self):
        return self._limiter

    def release(self) -> None:
        return None

    def current_limit(self) -> int:
        return self._limiter.current_rate()

    def set_limit(self, limit: int) -> None:
        self._limiter.set_rate(limit)


class ExistingDownloaderAdapter:
    def __init__(
        self,
        *,
        downloader_factory=M3U8Downloader,
        global_speed_limit=0,
        access_refresher=None,
    ) -> None:
        self._factory = downloader_factory
        self._global_pool = _SharedGlobalSpeedPool(global_speed_limit)
        self._access_refresher = access_refresher or self._refresh_page_access

    def set_global_speed_limit(self, limit: int) -> None:
        self._global_pool.set_limit(limit)

    @staticmethod
    def _refresh_page_access(task, stop_event) -> list:
        """重新访问来源页，为按来源页临时放行的媒体站刷新访问窗口。"""
        from .extractor import extract_m3u8_from_page_with_title

        settings = task.settings
        candidates, _title = extract_m3u8_from_page_with_title(
            task.source_url,
            deep=True,
            timeout=settings.timeout_seconds,
            estimate=False,
            max_workers=1,
            proxy=settings.proxy or None,
            stop_event=stop_event,
        )
        return candidates

    @staticmethod
    def _is_web_task(task) -> bool:
        return getattr(task.source_kind, "value", task.source_kind) == "web_page"

    @staticmethod
    def _configure_session(downloader, settings) -> None:
        session = getattr(downloader, "_session", None)
        if session is None:
            return
        if settings.referer:
            session.headers["Referer"] = settings.referer
        if settings.user_agent:
            session.headers["User-Agent"] = settings.user_agent
        if settings.protected_cookie:
            session.headers["Cookie"] = unprotect_secret(settings.protected_cookie)

    @staticmethod
    def _run_and_wait(downloader):
        try:
            return downloader.download()
        finally:
            wait_for_cleanup = getattr(downloader, "wait_for_cleanup", None)
            if wait_for_cleanup is not None:
                wait_for_cleanup()

    def download(self, task, item, output_path, *, stop_event, on_progress, on_log):
        settings = task.settings
        global_limiter = self._global_pool.acquire()
        download_url = item.source_url
        try:
            for attempt in range(2):
                downloader = self._factory(
                    url=download_url,
                    output=str(output_path),
                    workers=settings.segment_threads,
                    max_retries=settings.request_retries,
                    timeout=settings.timeout_seconds,
                    speed_limit=settings.speed_limit,
                    speed_limiter=global_limiter,
                    proxy=settings.proxy or None,
                    stop_event=stop_event,
                    progress_callback=on_progress,
                    log_callback=on_log,
                )
                self._configure_session(downloader, settings)
                try:
                    return Path(self._run_and_wait(downloader))
                except PlaylistFetchError as exc:
                    can_refresh = (
                        attempt == 0
                        and exc.status_code == 403
                        and self._is_web_task(task)
                    )
                    if not can_refresh:
                        raise
                    on_log("媒体站拒绝访问，正在重新访问来源页后重试一次")
                    refreshed = self._access_refresher(task, stop_event) or []
                    if len(refreshed) == 1:
                        fresh_url = getattr(refreshed[0], "url", "")
                        if fresh_url:
                            download_url = fresh_url
                    on_log("来源页访问已刷新，重新请求媒体清单")
        finally:
            self._global_pool.release()
