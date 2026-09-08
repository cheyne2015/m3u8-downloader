"""网页下载记录：记录每次「提取网页」过的页面及其下载结果.

独立于 :mod:`m3u8_downloader.history`（已下载 m3u8 直链去重）与 GUI 的
「待处理预载队列」。本模块持久化到 ``~/.m3u8-downloader/page_history.json``，
上限 :data:`MAX_PAGE_HISTORY`（500）条，超出自动丢弃最旧；文件损坏/缺失时
安全降级为空列表，绝不影响下载主流程。

条目结构（dict）：
    page_url: 网页 URL（记录键，同一页只保留最新一条状态）。
    status:   状态标签：
              - ``"extracted"``  —— 提取过网页，尚未下载；
              - ``"downloaded"`` —— 从该页发起的下载成功（附 m3u8_url）；
              - ``"failed"``     —— 从该页发起的下载以失败结束。
    m3u8_url: 实际下载的 m3u8 直链（可空；多条时以换行追加）。
    timestamp: 最近一次更新/插入的本地时间字符串。

排序：新到旧由插入位置决定（每次更新都移到列表头部）。
"""

import json
import os
import time
from typing import Dict, List

# 网页下载记录文件（与 gui_config.json / download_history.json 同目录）
PAGE_HISTORY_FILE = os.path.join(
    os.path.expanduser("~"), ".m3u8-downloader", "page_history.json"
)
# 最多保留条数：超出自动丢弃最旧（需求规格确认的上限）
MAX_PAGE_HISTORY = 500

# 状态常量
STATUS_EXTRACTED = "extracted"
STATUS_DOWNLOADED = "downloaded"
STATUS_FAILED = "failed"


def _load() -> List[Dict[str, str]]:
    """读取记录列表（新→旧；损坏/缺失时安全降级为空列表）.

    Returns:
        按新到旧排序的记录 dict 列表。
    """
    try:
        with open(PAGE_HISTORY_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        records = data.get("records", []) if isinstance(data, dict) else []
        cleaned: List[Dict[str, str]] = []
        for r in records:
            if not isinstance(r, dict) or not str(r.get("page_url") or "").strip():
                continue
            cleaned.append({
                "page_url": str(r.get("page_url", "")),
                "status": str(r.get("status", STATUS_EXTRACTED)),
                "m3u8_url": str(r.get("m3u8_url") or ""),
                "timestamp": str(r.get("timestamp", "") or ""),
            })
        return cleaned
    except (OSError, ValueError):
        return []


def _save(records: List[Dict[str, str]]) -> None:
    """写回记录列表，截断到上限；失败静默（不影响下载主流程）."""
    records = records[:MAX_PAGE_HISTORY]
    try:
        os.makedirs(os.path.dirname(PAGE_HISTORY_FILE), exist_ok=True)
        with open(PAGE_HISTORY_FILE, "w", encoding="utf-8") as f:
            json.dump({"records": records}, f, ensure_ascii=False)
    except OSError:
        pass


def _upsert(page_url: str, *, status: str, m3u8_url: str = "") -> None:
    """把 ``page_url`` 的最新状态写入记录头部（同页旧记录被替换）.

    同一网页只保留最新一条：再次提取会刷新到最前并重置为 extracted；
    下载成功/失败则把对应页更新为 downloaded / failed。

    Args:
        page_url: 网页 URL.
        status: 目标状态（extracted / downloaded / failed）.
        m3u8_url: 实际下载的 m3u8 直链（仅 downloaded 有意义）.
    """
    key = str(page_url or "").strip()
    if not key:
        return
    records = [r for r in _load() if r.get("page_url") != key]
    if m3u8_url:
        # 同一页再次下载不同 m3u8 时，多行追加便于回看
        m3u8_url = m3u8_url
    record: Dict[str, str] = {
        "page_url": key,
        "status": status,
        "m3u8_url": m3u8_url,
        "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
    }
    records.insert(0, record)
    _save(records)


def list_records() -> List[Dict[str, str]]:
    """返回网页下载记录（新→旧）.

    Returns:
        记录 dict 列表；无记录时为空列表。
    """
    return _load()


def record_page_extracted(page_url: str) -> None:
    """记录「提取网页」事件（含预载队列中的预载完成）.

    Args:
        page_url: 本次提取的网页 URL.
    """
    _upsert(page_url, status=STATUS_EXTRACTED)


def record_page_downloaded(page_url: str, m3u8_url: str) -> None:
    """记录从某网页提取结果真正发起的下载成功.

    Args:
        page_url: 所属网页 URL（可为空串，空则不写）.
        m3u8_url: 本次实际下载的 m3u8 直链.
    """
    _upsert(page_url, status=STATUS_DOWNLOADED, m3u8_url=str(m3u8_url or "").strip())


def record_page_failed(page_url: str) -> None:
    """记录从某网页发起的下载失败（含自动连播中被跳过的那页）.

    Args:
        page_url: 所属网页 URL（可为空串，空则不写）.
    """
    _upsert(page_url, status=STATUS_FAILED)
