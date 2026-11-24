"""从项目 GitHub 正式发布页检查新版本。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import re
import json
from pathlib import Path

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
    main_download: str = ""
    deep_version: str = ""
    deep_download: str = ""


def installed_deep_version(program_directory: Path) -> str | None:
    runtime = program_directory / "deep-runtime"
    if not runtime.is_dir():
        return None
    try:
        if not (runtime / "deep-worker.exe").is_file() or not (runtime / "browsers").is_dir():
            return ""
        version = json.loads((runtime / "runtime.json").read_text(encoding="utf-8-sig"))["runtime_version"]
        _version_parts(version)
        return version
    except (OSError, ValueError, KeyError, AttributeError, TypeError):
        return ""


def deep_update_available(release: ReleaseInfo, installed: str | None) -> bool:
    return bool(release.deep_version and installed is not None and (
        not installed or is_newer_version(release.deep_version, installed)
    ))


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
            main_download = deep_download = deep_version = ""
            base = f"https://github.com/cheyne2015/m3u8-downloader/releases/download/{payload['tag_name']}/"
            for asset in payload.get("assets") or []:
                name = asset.get("name", "")
                address = asset.get("browser_download_url", "")
                if address != base + name:
                    continue
                if name == f"m3u8-downloader-{version}-windows-x64.zip":
                    main_download = address
                match = re.fullmatch(r"m3u8-downloader-deep-runtime-(\d+(?:\.\d+)+)-windows-x64\.zip", name)
                if match and (not deep_version or is_newer_version(match[1], deep_version)):
                    deep_version, deep_download = match[1], address
            return ReleaseInfo(version, url, str(payload.get("name") or "").strip(),
                               main_download, deep_version, deep_download)
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
