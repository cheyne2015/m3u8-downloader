"""下载历史去重模块单元测试：URL 规范化、跨会话记录/查询."""

import json
import os

from m3u8_downloader import history


def _tmp_history_file(tmp_path, monkeypatch):
    p = tmp_path / "download_history.json"
    monkeypatch.setattr(history, "HISTORY_FILE", str(p))
    return p


def test_normalize_url_strips_query_and_fragment():
    assert history.normalize_url_for_dedup(
        "https://cdn.example.com/vod/movie-123/1080/index.m3u8?token=abc&expires=1#frag"
    ) == "https://cdn.example.com/vod/movie-123/1080/index.m3u8"
    # 无 query 时原样返回
    assert history.normalize_url_for_dedup(
        "https://cdn.example.com/vod/movie-123/720/index.m3u8"
    ) == "https://cdn.example.com/vod/movie-123/720/index.m3u8"


def test_normalize_url_different_tokens_same_key():
    """同一集带不同 token 应归一化到同一个 key，从而识别为重复."""
    a = history.normalize_url_for_dedup("https://cdn/x/1080/index.m3u8?token=AAA")
    b = history.normalize_url_for_dedup("https://cdn/x/1080/index.m3u8?token=BBB")
    assert a == b == "https://cdn/x/1080/index.m3u8"


def test_normalize_url_invalid_returns_input():
    assert history.normalize_url_for_dedup("") == ""
    # 畸形 URL 也尽量不抛异常
    history.normalize_url_for_dedup("not a url")


def test_is_downloaded_false_when_empty(tmp_path, monkeypatch):
    _tmp_history_file(tmp_path, monkeypatch)
    assert history.is_downloaded("https://cdn/x/1080/index.m3u8") is False


def test_record_and_query_roundtrip(tmp_path, monkeypatch):
    _tmp_history_file(tmp_path, monkeypatch)
    history.record_download("https://cdn/x/1080/index.m3u8?token=AAA")
    # 去 query 后同一 key
    assert history.is_downloaded("https://cdn/x/1080/index.m3u8?token=BBB") is True
    # 不同 path 不是重复
    assert history.is_downloaded("https://cdn/x/720/index.m3u8") is False


def test_record_deduplicates_and_truncates(tmp_path, monkeypatch):
    p = _tmp_history_file(tmp_path, monkeypatch)
    # 重复记录同一条只保留一份
    history.record_download("https://cdn/x/1080/index.m3u8?token=1")
    history.record_download("https://cdn/x/1080/index.m3u8?token=2")
    with open(p, "r", encoding="utf-8") as f:
        urls = json.load(f)["urls"]
    assert urls == ["https://cdn/x/1080/index.m3u8"]

    # 超过上限只保留最近 MAX_HISTORY 条
    monkeypatch.setattr(history, "MAX_HISTORY", 3)
    for i in range(5):
        history.record_download(f"https://cdn/x/{i}/index.m3u8")
    with open(p, "r", encoding="utf-8") as f:
        urls = json.load(f)["urls"]
    assert len(urls) == 3
    assert urls[0] == "https://cdn/x/2/index.m3u8"  # 最旧的被截掉


def test_corrupt_history_degrades_to_empty(tmp_path, monkeypatch):
    p = _tmp_history_file(tmp_path, monkeypatch)
    p.write_text("{not valid json", encoding="utf-8")
    assert history.is_downloaded("https://cdn/x/1080/index.m3u8") is False
