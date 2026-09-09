"""网页下载记录：记录每个「提取网页」页面及其下载结果.

独立于 :mod:`m3u8_downloader.history`（已下载 m3u8 直链去重）与 GUI 的
「待处理预载队列」。本模块持久化到 ``~/.m3u8-downloader/page_history.json``，
上限 :data:`MAX_PAGE_HISTORY`（500）页，超出自动丢弃最旧；文件损坏/缺失时
安全降级为空列表，绝不影响下载主流程。

同一网页只保留**一条**记录（记录键 = page_url），但会**累积**该页历次成功
下载过的 m3u8 直链（观察 D）：再次提取 / 再次下载都不会覆盖已下载的 m3u8，
只会追加新的 m3u8。列表按页的最近活动时间新→旧排序。

条目结构（文件内 dict）：
    page_url:    网页 URL（记录键，同一页只保留最新一条）。
    status:      状态标签：
                 - ``"extracted"``  —— 提取过网页，从未成功下载；
                 - ``"downloaded"`` —— 从该页至少成功下载过一次（含 m3u8 列表）；
                 - ``"failed"``     —— 从该页发起的下载失败且从未成功下载过。
    timestamp:   最近一次更新/插入的本地时间字符串。
    title:       网页名/网页标题（页维度，``record_page_extracted(title=)`` 写入；
                 旧记录缺失时降级为空串，展示层回退显示网页 URL）。
    output_path: 最近一次成功下载的输出文件完整路径（供 GUI「打开位置」定位）；
                 旧记录缺失时降级为空串。
    downloads:   该页历次成功下载的 m3u8 列表：``[{"m3u8_url", "timestamp"}, ...]``
                 （同一 m3u8 去重，仅更新时间；按时间旧→新排列）。
    m3u8_url:    冗余兼容字段 = 全部已下载 m3u8 的换行拼接（供旧版本读取/展示）。
                 展示「最近一次下载的 m3u8」请用 :func:`latest_m3u8_url`，
                 不要直接用本字段（它包含历史全部）。

语义要点：
- ``record_page_extracted`` 只做「触达」：已有 downloaded 记录时状态与 m3u8 列表
  原样保留（不重置为 extracted、不丢 m3u8），仅把该页移到列表头部刷新时间；
  从未成功下载的页重新提取时才保持/回到 extracted。
- ``record_page_downloaded`` 把本次 m3u8 追加进 ``downloads``（同一 m3u8 只更新
  时间），状态置 downloaded，永不清空已有 m3u8。
- ``record_page_failed`` 仅当该页从未成功下载时把状态置 failed；已有成功下载
  记录时保持 downloaded（不抹掉已下载的 m3u8）。
"""

import json
import os
import time
from typing import Dict, List

# 网页下载记录文件（与 gui_config.json / download_history.json 同目录）
PAGE_HISTORY_FILE = os.path.join(
    os.path.expanduser("~"), ".m3u8-downloader", "page_history.json"
)
# 最多保留页数：超出自动丢弃最旧（需求规格确认的上限）
MAX_PAGE_HISTORY = 500

# 状态常量
STATUS_EXTRACTED = "extracted"
STATUS_DOWNLOADED = "downloaded"
STATUS_FAILED = "failed"


def _now() -> str:
    """当前本地时间字符串."""
    return time.strftime("%Y-%m-%d %H:%M:%S")


def _clean_text(value) -> str:
    """转为去除首尾空白的字符串."""
    return str(value or "").strip()


def _clean_path(value) -> str:
    """转为去除首尾空白的路径字符串（缺字段时安全降级为空串）."""
    return str(value or "").strip()


def _normalize(record) -> "Dict[str, object]":
    """把一条原始 dict 归一化为内存结构；结构非法时返回 None.

    向后兼容：旧版记录缺 ``title`` / ``output_path`` 字段时安全降级为空串。

    Returns:
        含 ``page_url/status/timestamp/title/output_path/downloads`` 的记录；
        page_url 为空时返回 None.
    """
    if not isinstance(record, dict):
        return None
    page_url = _clean_text(record.get("page_url"))
    if not page_url:
        return None
    status = _clean_text(record.get("status")) or STATUS_EXTRACTED
    timestamp = str(record.get("timestamp") or "")
    downloads: List[Dict[str, str]] = []
    raw_downloads = record.get("downloads")
    if isinstance(raw_downloads, list):
        seen = set()
        for item in raw_downloads:
            if not isinstance(item, dict):
                continue
            url = _clean_text(item.get("m3u8_url") or item.get("url"))
            if not url or url in seen:
                continue
            seen.add(url)
            downloads.append({
                "m3u8_url": url,
                "timestamp": str(item.get("timestamp") or ""),
            })
    else:
        # 旧版单 m3u8_url 字段 → 迁移为 downloads 列表
        url = _clean_text(record.get("m3u8_url"))
        if url:
            downloads.append({"m3u8_url": url, "timestamp": timestamp})
    return {
        "page_url": page_url,
        "status": status,
        "timestamp": timestamp,
        "title": _clean_text(record.get("title")),
        "output_path": _clean_path(record.get("output_path")),
        "downloads": downloads,
    }


def _load() -> List[Dict[str, object]]:
    """读取记录列表（新→旧；损坏/缺失时安全降级为空列表）.

    Returns:
        按新到旧排序的归一化记录列表。
    """
    try:
        with open(PAGE_HISTORY_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        records = data.get("records", []) if isinstance(data, dict) else []
    except (OSError, ValueError):
        return []
    cleaned: List[Dict[str, object]] = []
    for r in records:
        norm = _normalize(r)
        if norm is not None:
            cleaned.append(norm)
    return cleaned


def _save(records: List[Dict[str, object]]) -> None:
    """写回记录列表，截断到上限；失败静默（不影响下载主流程）."""
    records = records[:MAX_PAGE_HISTORY]
    try:
        os.makedirs(os.path.dirname(PAGE_HISTORY_FILE), exist_ok=True)
        out = []
        for r in records:
            downloads = [
                {"m3u8_url": d["m3u8_url"], "timestamp": d["timestamp"]}
                for d in (r.get("downloads") or [])
            ]
            out.append({
                "page_url": r["page_url"],
                "status": r["status"],
                "timestamp": r["timestamp"],
                "title": _clean_text(r.get("title")),
                "output_path": _clean_path(r.get("output_path")),
                "downloads": downloads,
                # 冗余兼容字段：全部已下载 m3u8 换行拼接（旧版本读取/展示用）
                "m3u8_url": "\n".join(d["m3u8_url"] for d in downloads),
            })
        with open(PAGE_HISTORY_FILE, "w", encoding="utf-8") as f:
            json.dump({"records": out}, f, ensure_ascii=False)
    except OSError:
        pass


def _pop_existing(records: List[Dict[str, object]], key: str) -> "Dict[str, object]":
    """取出（并从原列表移除）与 ``key`` 同页的记录；没有则返回 None."""
    for i, r in enumerate(records):
        if r.get("page_url") == key:
            return records.pop(i)
    return None


def _display(records: List[Dict[str, object]]) -> List[Dict[str, str]]:
    """把内部记录转为供 GUI / 调用方使用的展示 dict（含 m3u8_url 拼接字段）."""
    out: List[Dict[str, str]] = []
    for r in records:
        downloads = r.get("downloads") or []
        out.append({
            "page_url": str(r.get("page_url", "")),
            "status": str(r.get("status", STATUS_EXTRACTED)),
            "timestamp": str(r.get("timestamp", "") or ""),
            "title": _clean_text(r.get("title")),
            "output_path": _clean_path(r.get("output_path")),
            "downloads": [dict(d) for d in downloads],
            "m3u8_url": "\n".join(str(d.get("m3u8_url", "")) for d in downloads),
        })
    return out


def latest_m3u8_url(record) -> str:
    """取该页**最近一次**下载的 m3u8 直链（展示用，单条）.

    优先取 ``downloads`` 列表最后一项（按时间旧→新排列，即最新一次下载）；
    没有 ``downloads`` 时回退取冗余 ``m3u8_url`` 字段的第一行。

    Args:
        record: :func:`list_records` 返回的展示记录 dict（或内部记录 dict）。

    Returns:
        最近一次下载的 m3u8 直链；取不到时返回空串。
    """
    if not isinstance(record, dict):
        return ""
    downloads = record.get("downloads") or []
    if isinstance(downloads, list) and downloads:
        last = downloads[-1]
        if isinstance(last, dict):
            return _clean_text(last.get("m3u8_url"))
    joined = str(record.get("m3u8_url") or "")
    if joined:
        return _clean_text(joined.split("\n")[0])
    return ""


def list_records() -> List[Dict[str, str]]:
    """返回网页下载记录（新→旧）.

    Returns:
        记录 dict 列表；无记录时为空列表。每条含 ``page_url/status/timestamp/
        title/output_path/downloads/m3u8_url``（m3u8_url 为该页全部已下载 m3u8
        的换行拼接；只要「最近一次」请用 :func:`latest_m3u8_url`）。
    """
    return _display(_load())


def record_page_extracted(page_url: str, title: str = "") -> None:
    """记录「提取网页」事件（含预载队列中的预载完成）.

    只做触达（移到头部、刷新时间），**不覆盖**已下载的 m3u8 与 downloaded 状态
    （观察 D）；从未成功下载的页保持/回到 extracted。

    Args:
        page_url: 本次提取的网页 URL.
        title: 网页名/网页标题（可选）；仅当非空时写入/更新记录的 ``title``，
               空值不会覆盖已有标题（避免提取标题失败时把旧标题抹掉）。
    """
    key = _clean_text(page_url)
    if not key:
        return
    new_title = _clean_text(title)
    records = _load()
    rec = _pop_existing(records, key)
    if rec is None:
        rec = {
            "page_url": key,
            "status": STATUS_EXTRACTED,
            "timestamp": _now(),
            "title": new_title,
            "output_path": "",
            "downloads": [],
        }
    else:
        # 已成功下载过的页：状态与 m3u8 原样保留；从未下载过的才回到 extracted。
        if not (rec.get("downloads") or []):
            rec["status"] = STATUS_EXTRACTED
        rec["timestamp"] = _now()
        # 仅在拿到有效标题时更新，不用空值覆盖已有标题。
        if new_title:
            rec["title"] = new_title
        # 向后兼容：旧记录可能缺这两个字段，这里补齐保证后续读写一致。
        if "title" not in rec:
            rec["title"] = ""
        if "output_path" not in rec:
            rec["output_path"] = ""
    records.insert(0, rec)
    _save(records)


def record_page_downloaded(page_url: str, m3u8_url: str, output_path: str = "") -> None:
    """记录从某网页提取结果真正发起的下载成功（追加/累积 m3u8，不覆盖旧记录）.

    Args:
        page_url: 所属网页 URL（可为空串，空则不写）.
        m3u8_url: 本次实际下载的 m3u8 直链.
        output_path: 本次下载输出文件的完整路径（可选）；仅当非空时更新记录的
                     ``output_path``（即始终保存最近一次成功下载的位置，供
                     GUI「打开位置」定位）。空值不覆盖已有路径。
    """
    key = _clean_text(page_url)
    url = _clean_text(m3u8_url)
    if not key or not url:
        return
    new_output_path = _clean_path(output_path)
    records = _load()
    rec = _pop_existing(records, key)
    if rec is None:
        rec = {
            "page_url": key,
            "status": STATUS_EXTRACTED,
            "timestamp": _now(),
            "title": "",
            "output_path": new_output_path,
            "downloads": [],
        }
    downloads = list(rec.get("downloads") or [])
    replaced = False
    for d in downloads:
        if d.get("m3u8_url") == url:
            d["timestamp"] = _now()
            replaced = True
            break
    if not replaced:
        downloads.append({"m3u8_url": url, "timestamp": _now()})
    rec["downloads"] = downloads
    rec["status"] = STATUS_DOWNLOADED
    rec["timestamp"] = _now()
    # 仅当本次拿到有效输出路径时更新（始终保存最近一次成功下载的位置）。
    if new_output_path:
        rec["output_path"] = new_output_path
    # 向后兼容：旧记录可能缺这两个字段，这里补齐保证后续读写一致。
    if "title" not in rec:
        rec["title"] = ""
    if "output_path" not in rec:
        rec["output_path"] = ""
    records.insert(0, rec)
    _save(records)


def record_page_failed(page_url: str) -> None:
    """记录从某网页发起的下载失败（含自动连播中被跳过的那页）.

    该页从未成功下载过才置为 failed；已有成功下载记录时保持 downloaded
    （不抹掉已下载的 m3u8，观察 D）。

    Args:
        page_url: 所属网页 URL（可为空串，空则不写）.
    """
    key = _clean_text(page_url)
    if not key:
        return
    records = _load()
    rec = _pop_existing(records, key)
    if rec is None:
        rec = {
            "page_url": key,
            "status": STATUS_FAILED,
            "timestamp": _now(),
            "title": "",
            "output_path": "",
            "downloads": [],
        }
    else:
        if rec.get("downloads") or []:
            # 已有成功下载：保留 downloaded，仅刷新活动时间。
            rec["status"] = STATUS_DOWNLOADED
        else:
            rec["status"] = STATUS_FAILED
        rec["timestamp"] = _now()
        # 向后兼容：旧记录可能缺这两个字段，这里补齐保证后续读写一致。
        if "title" not in rec:
            rec["title"] = ""
        if "output_path" not in rec:
            rec["output_path"] = ""
    records.insert(0, rec)
    _save(records)
