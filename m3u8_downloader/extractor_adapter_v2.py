"""把现有网页提取核心接入新版任务协调器。"""

from __future__ import annotations

from . import extractor, utils
from .tasking import Candidate


def _map_candidate(candidate) -> Candidate:
    return Candidate(
        url=candidate.url,
        label=getattr(candidate, "title", "") or "",
        estimated_bytes=getattr(candidate, "estimated_size", 0) or None,
        duration_seconds=getattr(candidate, "duration", 0.0) or None,
        valid=bool(getattr(candidate, "reachable", True)),
    )


class ExistingExtractorAdapter:
    def __init__(self, *, extract_function=None) -> None:
        self._extract = extract_function or extractor.extract_m3u8_from_page_with_title

    def extract(self, task, *, deep, on_candidate, on_title, stop_event):
        settings = task.settings
        session = utils.create_http_session(
            settings.timeout_seconds,
            proxy=settings.proxy or None,
        )
        if settings.referer:
            session.headers["Referer"] = settings.referer
        if settings.user_agent:
            session.headers["User-Agent"] = settings.user_agent

        def report(candidate) -> None:
            on_candidate(_map_candidate(candidate))

        try:
            candidates, title = self._extract(
                task.source_url,
                session=session,
                deep=deep,
                timeout=settings.timeout_seconds,
                estimate=True,
                max_workers=settings.segment_threads,
                proxy=settings.proxy or None,
                stop_event=stop_event,
                on_candidate=report,
                on_title=on_title,
            )
            if title:
                on_title(title)
            return [_map_candidate(candidate) for candidate in candidates]
        finally:
            session.close()

