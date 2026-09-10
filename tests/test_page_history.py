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


# ===========================================================================
# 新增字段：title（网页名）/ output_path（打开位置）+ 只展示最近一次 m3u8
# ===========================================================================


def test_extracted_writes_title(tmp_path, monkeypatch):
    """record_page_extracted(title=) 写入网页名."""
    _tmp_file(tmp_path, monkeypatch)
    page_history.record_page_extracted("https://x/page", title="仙界法务部 第55集")
    records = page_history.list_records()
    assert records[0]["title"] == "仙界法务部 第55集"
    assert records[0]["output_path"] == ""


def test_extracted_blank_title_does_not_overwrite_existing(tmp_path, monkeypatch):
    """空标题不覆盖已有标题（避免提取标题失败时把旧标题抹掉）。"""
    _tmp_file(tmp_path, monkeypatch)
    page_history.record_page_extracted("https://x/page", title="原标题")
    page_history.record_page_extracted("https://x/page")  # 未传 title
    page_history.record_page_extracted("https://x/page", title="   ")  # 空白
    records = page_history.list_records()
    assert records[0]["title"] == "原标题"


def test_extracted_non_blank_title_updates_existing(tmp_path, monkeypatch):
    """同一页重新提取拿到新标题 → 更新为该标题."""
    _tmp_file(tmp_path, monkeypatch)
    page_history.record_page_extracted("https://x/page", title="旧标题")
    page_history.record_page_extracted("https://x/page", title="新标题")
    assert page_history.list_records()[0]["title"] == "新标题"


def test_downloaded_writes_output_path(tmp_path, monkeypatch):
    """record_page_downloaded(output_path=) 写入最近一次成功下载的输出路径。"""
    _tmp_file(tmp_path, monkeypatch)
    page_history.record_page_downloaded(
        "https://x/page", "https://cdn/x/a.m3u8", output_path="D:\\out\\a.mp4"
    )
    records = page_history.list_records()
    assert records[0]["output_path"] == "D:\\out\\a.mp4"


def test_output_path_keeps_latest_download(tmp_path, monkeypatch):
    """多次下载 → output_path 始终为最近一次下载的输出路径。"""
    _tmp_file(tmp_path, monkeypatch)
    page_history.record_page_downloaded(
        "https://x/page", "https://cdn/x/a.m3u8", output_path="D:\\out\\first.mp4"
    )
    page_history.record_page_downloaded(
        "https://x/page", "https://cdn/x/b.m3u8", output_path="D:\\out\\second.mp4"
    )
    assert page_history.list_records()[0]["output_path"] == "D:\\out\\second.mp4"


def test_blank_output_path_does_not_overwrite_existing(tmp_path, monkeypatch):
    """未传/空 output_path 不覆盖已有路径（记录仍可定位）。"""
    _tmp_file(tmp_path, monkeypatch)
    page_history.record_page_downloaded(
        "https://x/page", "https://cdn/x/a.m3u8", output_path="D:\\out\\a.mp4"
    )
    page_history.record_page_downloaded("https://x/page", "https://cdn/x/b.m3u8")
    assert page_history.list_records()[0]["output_path"] == "D:\\out\\a.mp4"


def test_title_and_output_path_persist_round_trip(tmp_path, monkeypatch):
    """title / output_path 持久化往返（写盘后重载仍在）。"""
    p = _tmp_file(tmp_path, monkeypatch)
    page_history.record_page_extracted("https://x/page", title="网页名A")
    page_history.record_page_downloaded(
        "https://x/page", "https://cdn/x/a.m3u8", output_path="D:\\out\\a.mp4"
    )
    raw = json.loads(p.read_text(encoding="utf-8"))
    entry = raw["records"][0]
    assert entry["title"] == "网页名A"
    assert entry["output_path"] == "D:\\out\\a.mp4"
    records = page_history.list_records()
    assert records[0]["title"] == "网页名A"
    assert records[0]["output_path"] == "D:\\out\\a.mp4"


def test_legacy_record_without_new_fields_degrades_to_empty(tmp_path, monkeypatch):
    """旧记录（无 title / output_path）读取不报错，安全降级为空串。"""
    p = _tmp_file(tmp_path, monkeypatch)
    p.write_text(json.dumps({"records": [
        {"page_url": "https://x/old", "status": "downloaded",
         "m3u8_url": "https://cdn/x/a.m3u8", "timestamp": "2026-01-01 00:00:00"},
        {"page_url": "https://x/older", "status": "extracted",
         "timestamp": "2025-12-31 00:00:00"},
    ]}), encoding="utf-8")
    records = page_history.list_records()
    assert len(records) == 2
    for record in records:
        assert record["title"] == ""
        assert record["output_path"] == ""
    # 旧记录仍可正常读写：追加下载不应报错，且新字段被补齐
    page_history.record_page_downloaded(
        "https://x/old", "https://cdn/x/b.m3u8", output_path="D:\\out\\b.mp4"
    )
    after = {r["page_url"]: r for r in page_history.list_records()}
    assert after["https://x/old"]["output_path"] == "D:\\out\\b.mp4"
    assert after["https://x/old"]["title"] == ""
    # 状态/历史 m3u8 不受影响
    assert after["https://x/old"]["status"] == page_history.STATUS_DOWNLOADED
    assert len(after["https://x/old"]["downloads"]) == 2


def test_legacy_record_failed_path_keeps_new_fields(tmp_path, monkeypatch):
    """旧记录走 record_page_failed 分支也不丢新字段（不抛 KeyError）。"""
    p = _tmp_file(tmp_path, monkeypatch)
    p.write_text(json.dumps({"records": [
        {"page_url": "https://x/old", "status": "extracted",
         "timestamp": "2026-01-01 00:00:00"},
    ]}), encoding="utf-8")
    page_history.record_page_failed("https://x/old")
    records = page_history.list_records()
    assert records[0]["status"] == page_history.STATUS_FAILED
    assert records[0]["title"] == ""
    assert records[0]["output_path"] == ""


def test_latest_m3u8_url_returns_most_recent(tmp_path, monkeypatch):
    """只取最近一次下载的 m3u8（downloads 末项），不是全部拼接。"""
    _tmp_file(tmp_path, monkeypatch)
    page_history.record_page_downloaded("https://x/page", "https://cdn/x/hd.m3u8")
    page_history.record_page_downloaded("https://x/page", "https://cdn/x/sd.m3u8")
    page_history.record_page_downloaded("https://x/page", "https://cdn/x/latest.m3u8")
    record = page_history.list_records()[0]
    # 记录本身仍完整保留全部历史（不破坏观察 D）
    assert len(record["downloads"]) == 3
    assert page_history.latest_m3u8_url(record) == "https://cdn/x/latest.m3u8"
    # 最近一次不能带出其它 m3u8（旧行为用「 | 」/换行拼接全部）
    assert "|" not in page_history.latest_m3u8_url(record)
    assert "\n" not in page_history.latest_m3u8_url(record)


def test_latest_m3u8_url_falls_back_to_legacy_field():
    """无 downloads（旧记录）时回退 m3u8_url 字段第一行。"""
    assert page_history.latest_m3u8_url({
        "page_url": "https://x/page", "downloads": [],
        "m3u8_url": "https://cdn/x/a.m3u8\nhttps://cdn/x/b.m3u8",
    }) == "https://cdn/x/a.m3u8"


def test_latest_m3u8_url_edge_cases():
    """空记录 / 空 downloads / 非法入参 → 空串，不抛异常。"""
    assert page_history.latest_m3u8_url({}) == ""
    assert page_history.latest_m3u8_url({"downloads": [], "m3u8_url": ""}) == ""
    assert page_history.latest_m3u8_url(None) == ""
    assert page_history.latest_m3u8_url("not-a-dict") == ""


# ---------------------------------------------------------------------------
# 回归：m3u8 ↔ 输出文件必须一一对应（条目级 output_path + 按时间戳判定最近一次）
# ---------------------------------------------------------------------------


def test_download_entry_carries_its_own_output_path(tmp_path, monkeypatch):
    """每次下载的 output_path 写进它自己的 downloads 条目（不只是记录级）。"""
    _tmp_file(tmp_path, monkeypatch)
    page_history.record_page_downloaded(
        "https://x/page", "https://cdn/x/a.m3u8", output_path="D:\\out\\a.mp4"
    )
    page_history.record_page_downloaded(
        "https://x/page", "https://cdn/x/b.m3u8", output_path="D:\\out\\b.mp4"
    )
    record = page_history.list_records()[0]
    paths = {d["m3u8_url"]: d["output_path"] for d in record["downloads"]}
    assert paths == {
        "https://cdn/x/a.m3u8": "D:\\out\\a.mp4",
        "https://cdn/x/b.m3u8": "D:\\out\\b.mp4",
    }


def test_redownload_moves_entry_to_end_and_updates_path(tmp_path, monkeypatch):
    """A → B → 再次 A：最近一次必须是 A（含新路径），不是列表原末位 B。

    这是「下载记录里 m3u8 与『打开位置』打开的文件不对应」的核心回归：
    旧实现重下 A 时只就地刷新时间戳，downloads 末位仍是 B，展示层取末位 →
    显示 B 而记录级 output_path 却是 A 的文件。
    """
    _tmp_file(tmp_path, monkeypatch)
    page_history.record_page_downloaded(
        "https://x/page", "https://cdn/x/a.m3u8", output_path="D:\\out\\a.mp4"
    )
    page_history.record_page_downloaded(
        "https://x/page", "https://cdn/x/b.m3u8", output_path="D:\\out\\b.mp4"
    )
    page_history.record_page_downloaded(
        "https://x/page", "https://cdn/x/a.m3u8", output_path="D:\\out\\a-2.mp4"
    )
    record = page_history.list_records()[0]
    # 不重复累积：仍是两条；且重下的 A 被移到末尾（列表维持旧→新）
    assert [d["m3u8_url"] for d in record["downloads"]] == [
        "https://cdn/x/b.m3u8", "https://cdn/x/a.m3u8",
    ]
    latest = page_history.latest_download(record)
    assert latest["m3u8_url"] == "https://cdn/x/a.m3u8"
    assert latest["output_path"] == "D:\\out\\a-2.mp4"
    assert page_history.latest_m3u8_url(record) == "https://cdn/x/a.m3u8"
    # 记录级兜底路径同步为本次文件（展示层回退用）
    assert record["output_path"] == "D:\\out\\a-2.mp4"
    # 展示的 m3u8 与「打开位置」的文件同源，严格对应
    assert latest["output_path"] == "D:\\out\\a-2.mp4"


def test_latest_download_uses_timestamp_not_list_position(tmp_path, monkeypatch):
    """最近一次按时间戳最大判定，即使旧文件里它不在列表末位（旧版遗留顺序）。"""
    p = _tmp_file(tmp_path, monkeypatch)
    p.write_text(json.dumps({"records": [{
        "page_url": "https://x/page",
        "status": "downloaded",
        "timestamp": "2026-01-03 10:00:00",
        "title": "页A",
        "output_path": "D:\\out\\a-2.mp4",
        "downloads": [
            {"m3u8_url": "https://cdn/x/a.m3u8",
             "timestamp": "2026-01-03 10:00:00",
             "output_path": "D:\\out\\a-2.mp4"},
            {"m3u8_url": "https://cdn/x/b.m3u8",
             "timestamp": "2026-01-02 10:00:00",
             "output_path": "D:\\out\\b.mp4"},
        ],
    }]}), encoding="utf-8")
    record = page_history.list_records()[0]
    latest = page_history.latest_download(record)
    assert latest["m3u8_url"] == "https://cdn/x/a.m3u8"
    assert latest["output_path"] == "D:\\out\\a-2.mp4"
    assert page_history.latest_m3u8_url(record) == "https://cdn/x/a.m3u8"


def test_download_entry_output_path_round_trips_through_disk(tmp_path, monkeypatch):
    """条目级 output_path 持久化往返（写盘后重载仍在）。"""
    p = _tmp_file(tmp_path, monkeypatch)
    page_history.record_page_downloaded(
        "https://x/page", "https://cdn/x/a.m3u8", output_path="D:\\out\\a.mp4"
    )
    raw = json.loads(p.read_text(encoding="utf-8"))
    assert raw["records"][0]["downloads"][0]["output_path"] == "D:\\out\\a.mp4"
    reloaded = page_history.list_records()[0]
    assert reloaded["downloads"][0]["output_path"] == "D:\\out\\a.mp4"


def test_legacy_download_entry_without_output_path_degrades(tmp_path, monkeypatch):
    """旧格式条目（无 output_path）读取不报错 → 降级空串，可继续重下更新。"""
    p = _tmp_file(tmp_path, monkeypatch)
    p.write_text(json.dumps({"records": [{
        "page_url": "https://x/old",
        "status": "downloaded",
        "timestamp": "2026-01-01 00:00:00",
        "output_path": "D:\\out\\legacy.mp4",
        "downloads": [
            {"m3u8_url": "https://cdn/x/a.m3u8", "timestamp": "2026-01-01 00:00:00"},
        ],
    }]}), encoding="utf-8")
    record = page_history.list_records()[0]
    assert record["downloads"][0]["output_path"] == ""
    # 条目级路径为空 → 记录级兜底仍可用（展示层回退）
    assert record["output_path"] == "D:\\out\\legacy.mp4"
    latest = page_history.latest_download(record)
    assert latest["m3u8_url"] == "https://cdn/x/a.m3u8"
    assert latest["output_path"] == ""
    # 旧条目重下 → 补上条目级路径，且不重复累积
    page_history.record_page_downloaded(
        "https://x/old", "https://cdn/x/a.m3u8", output_path="D:\\out\\new.mp4"
    )
    updated = page_history.list_records()[0]
    assert len(updated["downloads"]) == 1
    assert updated["downloads"][0]["output_path"] == "D:\\out\\new.mp4"


def test_blank_output_path_does_not_clear_download_entry_path(tmp_path, monkeypatch):
    """重下时未传 output_path → 条目已有路径不被抹掉。"""
    _tmp_file(tmp_path, monkeypatch)
    page_history.record_page_downloaded(
        "https://x/page", "https://cdn/x/a.m3u8", output_path="D:\\out\\a.mp4"
    )
    page_history.record_page_downloaded("https://x/page", "https://cdn/x/a.m3u8")
    record = page_history.list_records()[0]
    assert record["downloads"][0]["output_path"] == "D:\\out\\a.mp4"


def test_latest_download_edge_cases():
    """空记录 / 空 downloads / 非法入参 → 空 dict，不抛异常。"""
    assert page_history.latest_download({}) == {}
    assert page_history.latest_download({"downloads": []}) == {}
    assert page_history.latest_download({"downloads": "bad"}) == {}
    assert page_history.latest_download({"downloads": [None, 1, "x"]}) == {}
    assert page_history.latest_download(None) == {}
    assert page_history.latest_download("not-a-dict") == {}


def test_latest_download_returns_copy_not_internal_dict(tmp_path, monkeypatch):
    """返回副本：调用方改动不会污染记录内部结构。"""
    _tmp_file(tmp_path, monkeypatch)
    page_history.record_page_downloaded(
        "https://x/page", "https://cdn/x/a.m3u8", output_path="D:\\out\\a.mp4"
    )
    record = page_history.list_records()[0]
    latest = page_history.latest_download(record)
    latest["output_path"] = "TAMPERED"
    assert record["downloads"][0]["output_path"] == "D:\\out\\a.mp4"
