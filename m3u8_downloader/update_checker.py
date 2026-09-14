"""从项目 GitHub 正式发布页检查新版本。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import re

import requests


LATEST_RELEASE_API = (
    "https://api.github.com/repos/cheyne2015/m3u8-downloader/releases/latest"
)
LATEST_RELEASE_PAGE = "https://github.com/cheyne2015/m3u8-downloader/releases/latest"


class UpdateCheckError(RuntimeError):
    pass


@dataclass(frozen=True)
class ReleaseInfo:
    version: str
    url: str
    name: str = ""


def _version_parts(value: str) -> tuple[int, ...]:
    match = re.fullmatch(r"v?(\d+(?:\.\d+)*)", value.strip(), re.IGNORECASE)
    if not match:
        raise ValueError(f"无法识别版本号：{value}")
    return tuple(int(part) for part in match.group(1).split("."))


def is_newer_version(latest: str, current: str) -> bool:
    latest_parts = _version_parts(latest)
    current_parts = _version_parts(current)
    width = max(len(latest_parts), len(current_parts))
    return latest_parts + (0,) * (width - len(latest_parts)) > (
        current_parts + (0,) * (width - len(current_parts))
    )


def should_check_for_updates(
    last_checked_at: str, *, now: datetime | None = None,
) -> bool:
    if not last_checked_at:
        return True
    now = now or datetime.now(timezone.utc)
    try:
        checked = datetime.fromisoformat(last_checked_at)
        if checked.tzinfo is None:
            checked = checked.replace(tzinfo=timezone.utc)
    except ValueError:
        return True
    return now - checked >= timedelta(hours=24)


class UpdateChecker:
    def __init__(self, *, request=requests.get) -> None:
        self._request = request

    def fetch_latest(self, timeout: int = 8) -> ReleaseInfo:
        api_error = None
        try:
            response = self._request(
                LATEST_RELEASE_API,
                timeout=timeout,
                headers={"Accept": "application/vnd.github+json", "User-Agent": "m3u8-downloader"},
            )
            response.raise_for_status()
            payload = response.json()
            version = str(payload.get("tag_name") or "").strip().lstrip("vV")
            url = str(payload.get("html_url") or "").strip()
            _version_parts(version)
            if not url.startswith("https://github.com/cheyne2015/m3u8-downloader/"):
                raise ValueError("发布页地址无效")
            return ReleaseInfo(version, url, str(payload.get("name") or "").strip())
        except Exception as exc:
            api_error = exc

        try:
            response = self._request(
                LATEST_RELEASE_PAGE,
                timeout=timeout,
                allow_redirects=True,
                headers={"User-Agent": "m3u8-downloader"},
            )
            response.raise_for_status()
            url = str(response.url or "").strip()
            prefix = "https://github.com/cheyne2015/m3u8-downloader/releases/tag/"
            if not url.startswith(prefix):
                raise ValueError("发布页没有返回正式版本号")
            version = url[len(prefix):].split("/", 1)[0].strip().lstrip("vV")
            _version_parts(version)
            return ReleaseInfo(version, url)
        except Exception as fallback_error:
            raise UpdateCheckError(
                f"检查更新失败：{fallback_error}（GitHub 接口：{api_error}）"
            ) from fallback_error
