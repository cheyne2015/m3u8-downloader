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


# ===========================================================================
# 观察 D：每页累积所有曾下载的 m3u8；重新提取/再次下载不覆盖 downloaded 记录
# ===========================================================================


def test_re_extract_preserves_downloaded_m3u8(tmp_path, monkeypatch):
    """下载成功后再次提取同一页 → 不覆盖 downloaded 状态与已下载的 m3u8。"""
    _tmp_file(tmp_path, monkeypatch)
    page_history.record_page_extracted("https://x/page")
    page_history.record_page_downloaded("https://x/page", "https://cdn/x/b.m3u8")
    page_history.record_page_extracted("https://x/page")  # 重新提取
    records = page_history.list_records()
    assert len(records) == 1
    assert records[0]["page_url"] == "https://x/page"
    assert records[0]["status"] == page_history.STATUS_DOWNLOADED
    assert records[0]["m3u8_url"] == "https://cdn/x/b.m3u8"
    assert len(records[0]["downloads"]) == 1


def test_accumulates_multiple_m3u8_per_page(tmp_path, monkeypatch):
    """同一页多次下载不同 m3u8（不同清晰度）→ 全部累积，不互相覆盖。"""
    _tmp_file(tmp_path, monkeypatch)
    page_history.record_page_extracted("https://x/page")
    page_history.record_page_downloaded("https://x/page", "https://cdn/x/hd.m3u8")
    page_history.record_page_downloaded("https://x/page", "https://cdn/x/sd.m3u8")
    page_history.record_page_extracted("https://x/page")  # 中间再提取一次也不丢
    page_history.record_page_downloaded("https://x/page", "https://cdn/x/uhd.m3u8")
    records = page_history.list_records()
    assert len(records) == 1
    assert records[0]["status"] == page_history.STATUS_DOWNLOADED
    urls = {d["m3u8_url"] for d in records[0]["downloads"]}
    assert urls == {
        "https://cdn/x/hd.m3u8",
        "https://cdn/x/sd.m3u8",
        "https://cdn/x/uhd.m3u8",
    }
    # m3u8_url 为全部已下载 m3u8 的换行拼接（向后兼容展示字段）
    joined = records[0]["m3u8_url"].split("\n")
    assert set(joined) == urls


def test_redownload_same_m3u8_not_duplicated(tmp_path, monkeypatch):
    """同一 m3u8 再次下载 → 不重复累积，仅保留一条（更新时间）。"""
    _tmp_file(tmp_path, monkeypatch)
    page_history.record_page_downloaded("https://x/page", "https://cdn/x/b.m3u8")
    page_history.record_page_downloaded("https://x/page", "https://cdn/x/b.m3u8")
    records = page_history.list_records()
    assert len(records[0]["downloads"]) == 1


def test_failed_after_download_keeps_downloaded_status(tmp_path, monkeypatch):
    """先成功后失败 → 状态仍保留 downloaded，已下载 m3u8 不被失败抹掉。"""
    _tmp_file(tmp_path, monkeypatch)
    page_history.record_page_downloaded("https://x/page", "https://cdn/x/b.m3u8")
    page_history.record_page_failed("https://x/page")
    records = page_history.list_records()
    assert len(records) == 1
    assert records[0]["status"] == page_history.STATUS_DOWNLOADED
    assert records[0]["m3u8_url"] == "https://cdn/x/b.m3u8"


def test_downloads_field_round_trips_through_disk(tmp_path, monkeypatch):
    """downloads 列表持久化往返：写盘后重载仍保留多个 m3u8。"""
    p = _tmp_file(tmp_path, monkeypatch)
    page_history.record_page_downloaded("https://x/page", "https://cdn/x/a.m3u8")
    page_history.record_page_downloaded("https://x/page", "https://cdn/x/b.m3u8")
    raw = json.loads(p.read_text(encoding="utf-8"))
    entry = raw["records"][0]
    assert [d["m3u8_url"] for d in entry["downloads"]] == [
        "https://cdn/x/a.m3u8", "https://cdn/x/b.m3u8",
    ]
    # 冗余兼容字段也保留
    assert "https://cdn/x/a.m3u8" in entry["m3u8_url"]
    records = page_history.list_records()
    assert len(records[0]["downloads"]) == 2


def test_legacy_single_m3u8_field_migrates_to_downloads(tmp_path, monkeypatch):
    """旧版仅含 m3u8_url 字段的记录 → 读取时迁移进 downloads 列表。"""
    p = _tmp_file(tmp_path, monkeypatch)
    p.write_text(json.dumps({"records": [
        {"page_url": "https://x/page", "status": "downloaded",
         "m3u8_url": "https://cdn/x/a.m3u8", "timestamp": "2026-01-01 00:00:00"},
    ]}), encoding="utf-8")
    records = page_history.list_records()
    assert len(records[0]["downloads"]) == 1
    assert records[0]["downloads"][0]["m3u8_url"] == "https://cdn/x/a.m3u8"
    assert records[0]["m3u8_url"] == "https://cdn/x/a.m3u8"
