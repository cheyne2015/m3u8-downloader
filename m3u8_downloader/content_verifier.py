"""只在媒体结构疑似重复时抽样比较远程媒体内容。"""

from __future__ import annotations

import hashlib
import json

from . import utils
from .parser import M3U8Parser, select_best_stream
from .secrets_v2 import unprotect_secret


class ContentDuplicateVerifier:
    SAMPLE_BYTES = 32 * 1024

    def __init__(self, *, session_factory=None) -> None:
        self._session_factory = session_factory or utils.create_http_session

    def matches(self, first_task, first_items, second_task, second_items) -> bool | None:
        """返回是否相同；站点拒绝抽样时返回 None，交给界面保守提示。"""
        try:
            first = self._sample_task(first_task, first_items)
            second = self._sample_task(second_task, second_items)
        except Exception:
            return None
        if not first or not second:
            return None
        return first == second

    def _sample_task(self, task, items) -> tuple[str, ...] | None:
        candidates = [
            item for item in items
            if item.valid and item.duration_seconds and item.segment_count > 0
        ]
        if not candidates:
            return None
        item = max(
            candidates,
            key=lambda candidate: (
                candidate.duration_seconds or 0,
                candidate.estimated_bytes or 0,
            ),
        )
        session = self._session_factory(
            task.settings.timeout_seconds,
            proxy=task.settings.proxy or None,
        )
        if task.settings.referer:
            session.headers["Referer"] = task.settings.referer
        if task.settings.user_agent:
            session.headers["User-Agent"] = task.settings.user_agent
        if task.settings.protected_cookie:
            session.headers["Cookie"] = unprotect_secret(task.settings.protected_cookie)
        try:
            playlist, resolution = self._load_media_playlist(
                session, item.source_url, task.settings.timeout_seconds,
            )
            if not playlist.segments:
                return None
            indexes = sorted({0, len(playlist.segments) // 2, len(playlist.segments) - 1})
            duration_signature = hashlib.sha256(json.dumps(
                [round(segment.duration, 3) for segment in playlist.segments],
                separators=(",", ":"),
            ).encode("ascii")).hexdigest()
            samples = tuple(
                self._sample_url(
                    session, playlist.segments[index].url, task.settings.timeout_seconds,
                )
                for index in indexes
            )
            return (resolution, duration_signature, *samples)
        finally:
            session.close()

    @staticmethod
    def _load_media_playlist(session, url: str, timeout: int):
        current_url = url
        resolution = ""
        for _depth in range(3):
            response = session.get(current_url, timeout=timeout)
            response.raise_for_status()
            playlist = M3U8Parser(response.text, current_url).parse()
            if not playlist.is_master:
                return playlist, resolution
            stream = select_best_stream(playlist)
            resolution = stream.resolution or resolution
            current_url = stream.url
        raise RuntimeError("媒体清单嵌套层级过深")

    def _sample_url(self, session, url: str, timeout: int) -> str:
        response = session.get(
            url,
            timeout=timeout,
            headers={"Range": f"bytes=0-{self.SAMPLE_BYTES - 1}"},
            stream=True,
        )
        response.raise_for_status()
        digest = hashlib.sha256()
        remaining = self.SAMPLE_BYTES
        for chunk in response.iter_content(chunk_size=min(8192, self.SAMPLE_BYTES)):
            if not chunk:
                continue
            digest.update(chunk[:remaining])
            remaining -= len(chunk[:remaining])
            if remaining <= 0:
                break
        if remaining == self.SAMPLE_BYTES:
            raise RuntimeError("媒体抽样返回空内容")
        return digest.hexdigest()
