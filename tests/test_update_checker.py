from datetime import datetime, timezone

import pytest

from m3u8_downloader.update_checker import (
    ReleaseInfo,
    UpdateCheckError,
    UpdateChecker,
    is_newer_version,
    should_check_for_updates,
)


@pytest.mark.parametrize(("latest", "current", "expected"), [
    ("v2.0.3", "2.0.2", True),
    ("2.1.0", "2.0.9", True),
    ("v2.0.2", "2.0.2", False),
    ("v1.9.9", "2.0.2", False),
])
def test_version_comparison(latest, current, expected):
    assert is_newer_version(latest, current) is expected


def test_fetch_latest_release_uses_official_repository_and_parses_response():
    observed = {}

    class Response:
        def raise_for_status(self):
            return None

        def json(self):
            return {
                "tag_name": "v2.0.3",
                "html_url": "https://github.com/cheyne2015/m3u8-downloader/releases/tag/v2.0.3",
                "name": "2.0.3",
            }

    def request(url, **kwargs):
        observed.update(url=url, kwargs=kwargs)
        return Response()

    release = UpdateChecker(request=request).fetch_latest()

    assert release == ReleaseInfo(
        version="2.0.3",
        url="https://github.com/cheyne2015/m3u8-downloader/releases/tag/v2.0.3",
        name="2.0.3",
    )
    assert observed["url"].endswith("/repos/cheyne2015/m3u8-downloader/releases/latest")
    assert observed["kwargs"]["timeout"] == 8


def test_fetch_latest_release_reports_invalid_response():
    class Response:
        def raise_for_status(self):
            return None

        def json(self):
            return {"tag_name": "", "html_url": ""}

    with pytest.raises(UpdateCheckError):
        UpdateChecker(request=lambda *_args, **_kwargs: Response()).fetch_latest()


def test_fetch_latest_release_falls_back_to_release_page_when_api_is_limited():
    calls = []

    class ApiResponse:
        def raise_for_status(self):
            raise RuntimeError("403 rate limit")

    class PageResponse:
        url = "https://github.com/cheyne2015/m3u8-downloader/releases/tag/v2.0.2"

        def raise_for_status(self):
            return None

    def request(url, **_kwargs):
        calls.append(url)
        return ApiResponse() if "api.github.com" in url else PageResponse()

    release = UpdateChecker(request=request).fetch_latest()

    assert release.version == "2.0.2"
    assert release.url.endswith("/releases/tag/v2.0.2")
    assert calls == [
        "https://api.github.com/repos/cheyne2015/m3u8-downloader/releases/latest",
        "https://github.com/cheyne2015/m3u8-downloader/releases/latest",
    ]


def test_automatic_check_waits_24_hours_but_manual_check_does_not():
    now = datetime(2026, 9, 14, 12, tzinfo=timezone.utc)
    assert should_check_for_updates("2026-09-13T11:59:59+00:00", now=now)
    assert not should_check_for_updates("2026-09-13T12:00:01+00:00", now=now)
    assert should_check_for_updates("", now=now)
