"""下载历史记录：跨会话去重已下载的 m3u8 链接.

用于「重复链接提醒」：把「去 query/fragment 后」的 URL 持久化到
``~/.m3u8-downloader/download_history.json``，下次打开仍记得下载过什么。
"""

import json
import os
from typing import List
from urllib.parse import urlsplit, urlunsplit

# 历史记录文件（与 gui_config.json 同目录）
HISTORY_FILE = os.path.join(
    os.path.expanduser("~"), ".m3u8-downloader", "download_history.json"
)
# 最多保留条数，防止无限增长
MAX_HISTORY = 2000


def normalize_url_for_dedup(url: str) -> str:
    """去 query/fragment 后规范化 URL，用于跨会话去重比对.

    ``https://cdn/x/1080/index.m3u8?token=abc#frag`` → ``https://cdn/x/1080/index.m3u8``

    视频站的一次性 token 每次都在 query 里变化，去掉后同一集能稳定识别为重复。
    清晰度通常体现在 path（/1080/ vs /720/），去 query 误判风险低。
    """
    try:
        parts = urlsplit(url or "")
        return urlunsplit((parts.scheme, parts.netloc, parts.path, "", ""))
    except Exception:
        return url or ""


def _load() -> List[str]:
    """读取历史记录（损坏/缺失时安全降级为空列表）."""
    try:
        with open(HISTORY_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        urls = data.get("urls", []) if isinstance(data, dict) else []
        return [str(u) for u in urls if u]
    except (OSError, ValueError):
        return []


def _save(urls: List[str]) -> None:
    """写回历史记录，截断到上限；失败静默（不影响下载主流程）."""
    urls = urls[-MAX_HISTORY:]
    try:
        os.makedirs(os.path.dirname(HISTORY_FILE), exist_ok=True)
        with open(HISTORY_FILE, "w", encoding="utf-8") as f:
            json.dump({"urls": urls}, f, ensure_ascii=False)
    except OSError:
        pass


def is_downloaded(url: str) -> bool:
    """判断 URL（去 query 后）是否已在历史记录里."""
    key = normalize_url_for_dedup(url)
    if not key:
        return False
    return key in _load()


def record_download(url: str) -> None:
    """记录一个已下载的 URL（去 query 后），自动去重并截断到上限."""
    key = normalize_url_for_dedup(url)
    if not key:
        return
    urls = _load()
    if key in urls:
        return
    urls.append(key)
    _save(urls)
