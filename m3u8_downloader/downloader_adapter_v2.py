"""把现有下载核心接入新版父任务协调器。"""

from pathlib import Path
import threading

from .downloader import M3U8Downloader, _SpeedLimiter
from .secrets_v2 import unprotect_secret


class _SharedGlobalSpeedPool:
    def __init__(self, limit: int) -> None:
        self._limit = max(0, int(limit))
        self._lock = threading.Lock()
        self._limiter = _SpeedLimiter(self.current_limit)

    def acquire(self):
        return self._limiter

    def release(self) -> None:
        return None

    def current_limit(self) -> int:
        with self._lock:
            return self._limit

    def set_limit(self, limit: int) -> None:
        with self._lock:
            self._limit = max(0, int(limit))
        self._limiter.rebase()


class ExistingDownloaderAdapter:
    def __init__(self, *, downloader_factory=M3U8Downloader, global_speed_limit=0) -> None:
        self._factory = downloader_factory
        self._global_pool = _SharedGlobalSpeedPool(global_speed_limit)

    def set_global_speed_limit(self, limit: int) -> None:
        self._global_pool.set_limit(limit)

    def download(self, task, item, output_path, *, stop_event, on_progress, on_log):
        settings = task.settings
        global_limiter = self._global_pool.acquire()
        try:
            downloader = self._factory(
                url=item.source_url,
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
            session = getattr(downloader, "_session", None)
            if session is not None:
                if settings.referer:
                    session.headers["Referer"] = settings.referer
                if settings.user_agent:
                    session.headers["User-Agent"] = settings.user_agent
                if settings.protected_cookie:
                    session.headers["Cookie"] = unprotect_secret(settings.protected_cookie)
            try:
                result = downloader.download()
                return Path(result)
            finally:
                wait_for_cleanup = getattr(downloader, "wait_for_cleanup", None)
                if wait_for_cleanup is not None:
                    wait_for_cleanup()
        finally:
            self._global_pool.release()
