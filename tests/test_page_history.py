"""网页下载记录（功能二）模块单元测试：新到旧、状态、去重、500 上限、往返."""

import json

from m3u8_downloader import page_history


def _tmp_file(tmp_path, monkeypatch):
    p = tmp_path / "page_history.json"
    monkeypatch.setattr(page_history, "PAGE_HISTORY_FILE", str(p))
    return p


def test_empty_history_lists_nothing(tmp_path, monkeypatch):
    _tmp_file(tmp_path, monkeypatch)
    assert page_history.list_records() == []


def test_extracted_record_is_newest_first(tmp_path, monkeypatch):
    _tmp_file(tmp_path, monkeypatch)
    page_history.record_page_extracted("https://x/page-a")
    page_history.record_page_extracted("https://x/page-b")
    records = page_history.list_records()
    assert [r["page_url"] for r in records] == [
        "https://x/page-b", "https://x/page-a",
    ]
    assert records[0]["status"] == page_history.STATUS_EXTRACTED
    assert records[0]["timestamp"]


def test_downloaded_record_carries_m3u8_url(tmp_path, monkeypatch):
    _tmp_file(tmp_path, monkeypatch)
    page_history.record_page_extracted("https://x/page")
    page_history.record_page_downloaded("https://x/page", "https://cdn/x/b.m3u8")
    records = page_history.list_records()
    assert len(records) == 1
    assert records[0]["page_url"] == "https://x/page"
    assert records[0]["status"] == page_history.STATUS_DOWNLOADED
    assert records[0]["m3u8_url"] == "https://cdn/x/b.m3u8"


def test_failed_record_overwrites_extracted(tmp_path, monkeypatch):
    _tmp_file(tmp_path, monkeypatch)
    page_history.record_page_extracted("https://x/page")
    page_history.record_page_failed("https://x/page")
    records = page_history.list_records()
    assert len(records) == 1
    assert records[0]["status"] == page_history.STATUS_FAILED


def test_same_page_keeps_one_latest_entry(tmp_path, monkeypatch):
    """同一网页反复提取只保留最新一条（记录键为 page_url）. """
    _tmp_file(tmp_path, monkeypatch)
    page_history.record_page_extracted("https://x/page-a")
    page_history.record_page_extracted("https://x/page-b")
    page_history.record_page_downloaded("https://x/page-a", "https://cdn/a.m3u8")
    records = page_history.list_records()
    assert [r["page_url"] for r in records] == [
        "https://x/page-a", "https://x/page-b",
    ]
    assert records[0]["status"] == page_history.STATUS_DOWNLOADED


def test_truncates_to_max_500_dropping_oldest(tmp_path, monkeypatch):
    _tmp_file(tmp_path, monkeypatch)
    monkeypatch.setattr(page_history, "MAX_PAGE_HISTORY", 3)
    for i in range(6):
        page_history.record_page_extracted(f"https://x/page-{i}")
    records = page_history.list_records()
    assert len(records) == 3
    # 新→旧：最新 3 条是 page-5/4/3；最旧的 page-0..2 被丢弃
    assert [r["page_url"] for r in records] == [
        "https://x/page-5", "https://x/page-4", "https://x/page-3",
    ]


def test_persist_round_trip(tmp_path, monkeypatch):
    """写盘后重新加载仍能恢复（同一文件，另起进程级读取）。"""
    p = _tmp_file(tmp_path, monkeypatch)
    page_history.record_page_downloaded("https://x/page", "https://cdn/x/a.m3u8")
    page_history.record_page_extracted("https://x/other")
    raw = json.loads(p.read_text(encoding="utf-8"))
    assert len(raw["records"]) == 2
    assert raw["records"][0]["page_url"] == "https://x/other"


def test_corrupt_file_degrades_to_empty(tmp_path, monkeypatch):
    p = _tmp_file(tmp_path, monkeypatch)
    p.write_text("{not json", encoding="utf-8")
    assert page_history.list_records() == []
    # 损坏后写入也应安全（不抛异常）
    page_history.record_page_extracted("https://x/ok")
    assert page_history.list_records()[0]["page_url"] == "https://x/ok"


def test_blank_page_url_is_ignored(tmp_path, monkeypatch):
    _tmp_file(tmp_path, monkeypatch)
    page_history.record_page_extracted("")
    page_history.record_page_extracted("   ")
    assert page_history.list_records() == []
