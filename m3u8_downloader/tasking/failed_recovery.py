"""将新提取的候选保守匹配到原失败项，不依赖易变的排列顺序。"""

from collections import Counter
from urllib.parse import parse_qsl, urlencode, urlsplit


_TEMPORARY_KEYS = {'token', 'sign', 'signature', 'sig', 'sk', 'se', 'expires',
                   'expire', 'exp', 'timestamp', 'ts', 'auth_key', 'ran',
                   'pid', 'playerid', 'client', 'cts'}


def _identity(url):
    parts = urlsplit(url)
    query = sorted((key, value) for key, value in parse_qsl(parts.query, keep_blank_values=True)
                   if key.casefold() not in _TEMPORARY_KEYS)
    return parts.netloc.casefold(), parts.path, urlencode(query)


def _structure(item):
    if not item.duration_seconds or item.duration_seconds <= 0:
        return None
    return round(item.duration_seconds, 1), item.segment_count, item.bandwidth


def match_failed_candidates(items, candidates, target_ids):
    candidates = list({candidate.url: candidate for candidate in candidates if candidate.valid}.values())
    used = set()
    matches = {}
    old_shapes = Counter(_structure(item) for item in items if _structure(item) is not None)
    old_labels = Counter(item.label for item in items if item.label)
    # 已完成或未要求重试的项占用自己的候选，避免新链接被错误分配。
    for item in items:
        if item.id not in target_ids:
            used.update(i for i, candidate in enumerate(candidates)
                        if _identity(candidate.url) == _identity(item.source_url))
    for item in items:
        if item.id not in target_ids:
            continue
        options = [i for i, c in enumerate(candidates) if i not in used
                   and _identity(c.url) == _identity(item.source_url)]
        if not options and item.label and old_labels[item.label] == 1:
            options = [i for i, c in enumerate(candidates) if i not in used and c.label == item.label]
        shape = _structure(item)
        if not options and shape is not None and old_shapes[shape] == 1:
            options = [i for i, c in enumerate(candidates) if i not in used and _structure(c) == shape]
        if not options and len(items) == len(candidates) == 1:
            options = [0]
        if len(options) == 1:
            index = options[0]
            matches[item.id] = candidates[index]
            used.add(index)
    return matches
