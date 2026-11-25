"""识别抖音作品地址并从结构化播放数据绑定目标作品。"""

import json
from urllib.parse import parse_qs, unquote, urlsplit


def is_share_link(url: str) -> bool:
    parsed = urlsplit(url)
    return parsed.scheme in {"http", "https"} and parsed.hostname == "v.douyin.com"


def find_rendered_video(text: str, target_id: str) -> dict | None:
    if not text or len(text) > 4 * 1024 * 1024:
        return None
    try:
        payload = json.loads(unquote(text) if text.lstrip().startswith("%") else text)
    except ValueError:
        return None
    return find_video_record(payload, target_id)


def video_id(url: str) -> str | None:
    parsed = urlsplit(url)
    if parsed.scheme not in {"http", "https"} or parsed.hostname not in {
        "douyin.com", "www.douyin.com",
    }:
        return None
    path = parsed.path.strip("/").split("/")
    if len(path) == 2 and path[0] == "video":
        value = path[1]
    elif path == ["jingxuan"]:
        values = parse_qs(parsed.query).get("modal_id", [])
        value = values[0] if len(values) == 1 else ""
    else:
        return None
    return value if value.isascii() and value.isdecimal() else None


def find_video_record(payload, target_id: str) -> dict | None:
    """精选流内可能有多个作品，只返回编号明确匹配且含视频信息的对象。"""
    pending = [payload]
    while pending:
        node = pending.pop()
        if isinstance(node, dict):
            if any(str(node.get(key, "")) == target_id for key in ("aweme_id", "awemeId")):
                if isinstance(node.get("video"), dict):
                    return node
            pending.extend(node.values())
        elif isinstance(node, list):
            pending.extend(node)
    return None


def playback_urls(record: dict) -> list[str]:
    """读取目标作品的普通播放地址，保留服务端给出的完整查询参数。"""
    video = record["video"]
    address = video.get("play_addr") or video.get("playAddr")
    if isinstance(address, dict):
        values = address.get("url_list") or address.get("urlList") or []
    elif isinstance(address, list):
        values = [entry.get("src") for entry in address if isinstance(entry, dict)]
    else:
        return []
    if not isinstance(values, list):
        return []
    urls = ("https:" + url if url.startswith("//") else url for url in values if isinstance(url, str))
    return list(dict.fromkeys(url for url in urls if is_playback_url(url)))


def is_playback_url(url: str) -> bool:
    parsed = urlsplit(url)
    host = parsed.hostname or ""
    return parsed.scheme == "https" and host.endswith(".douyinvod.com")
