"""兼容请求头与浏览器 Cookie 导出格式，不记录登录内容。"""

from http.cookiejar import Cookie
import json
import math
import re
from urllib.parse import urlsplit


_NAME = re.compile(r"^[!#$%&'*+.^_`|~0-9A-Za-z-]+$")


def _validate_pair(name, value):
    if not isinstance(name, str) or not _NAME.fullmatch(name):
        raise ValueError("登录信息中存在无效的 Cookie 名称")
    if not isinstance(value, str) or any(char in value for char in "\r\n;\x00"):
        raise ValueError("登录信息中存在无效的 Cookie 值，请重新导出")


def normalize_cookie_input(value: str) -> str:
    text = value.strip()
    if not text:
        return ""
    if text.startswith(("{", "[")):
        try:
            payload = json.loads(text)
        except (ValueError, TypeError):
            raise ValueError("登录信息 JSON 格式不正确，请重新复制完整内容") from None
        records = payload.get("cookies") if isinstance(payload, dict) else payload
        if not isinstance(records, list):
            raise ValueError("登录信息 JSON 需要包含 cookies 数组")
        for record in records:
            if not isinstance(record, dict):
                raise ValueError("登录信息 cookies 数组内容不正确")
            _validate_pair(record.get("name"), record.get("value"))
            if not isinstance(record.get("domain", ""), str):
                raise ValueError("登录信息中的站点域名不正确")
            if not isinstance(record.get("path", "/"), str) or not record.get("path", "/").startswith("/"):
                raise ValueError("登录信息中的 Cookie 路径不正确")
            expires = record.get("expirationDate", record.get("expires"))
            if expires is not None and (
                not isinstance(expires, (int, float)) or isinstance(expires, bool)
                or not math.isfinite(expires)
            ):
                raise ValueError("登录信息中的有效期不正确")
        return json.dumps(payload, ensure_ascii=True, separators=(",", ":"))
    if text.lower().startswith("cookie:"):
        text = text[7:].strip()
    if any(char in text for char in "\r\n\x00"):
        raise ValueError("登录信息请求头不能包含换行，请只复制 Cookie 的值")
    entries = []
    for entry in text.split(";"):
        if not entry.strip():
            continue
        name, equals, cookie_value = entry.strip().partition("=")
        if not equals:
            raise ValueError("登录信息需要使用 name=value 格式，或粘贴浏览器导出的 JSON")
        _validate_pair(name, cookie_value)
        entries.append(f"{name}={cookie_value}")
    return "; ".join(entries)


def apply_cookie_input(session, value: str, page_url: str) -> None:
    text = normalize_cookie_input(value)
    if not text:
        return
    if not text.startswith(("{", "[")):
        session.headers["Cookie"] = text
        return
    payload = json.loads(text)
    records = payload["cookies"] if isinstance(payload, dict) else payload
    host = (urlsplit(page_url).hostname or "").lower()
    session.headers.pop("Cookie", None)
    browser_cookies = []
    for record in records:
        domain = (record.get("domain") or host).lower()
        bare = domain.lstrip(".")
        host_only = bool(record.get("hostOnly", not domain.startswith(".")))
        if not bare or not (host == bare or (not host_only and host.endswith("." + bare))):
            continue
        path = record.get("path", "/")
        expires = record.get("expirationDate", record.get("expires"))
        if expires is not None and expires <= 0:
            expires = None
        secure = bool(record.get("secure", False))
        session.cookies.set_cookie(Cookie(
            version=0, name=record["name"], value=record["value"], port=None,
            port_specified=False, domain=bare if host_only else domain,
            domain_specified=not host_only,
            domain_initial_dot=not host_only and domain.startswith("."), path=path, path_specified=True,
            secure=secure, expires=int(expires) if expires is not None else None,
            discard=expires is None, comment=None, comment_url=None, rest={},
        ))
        browser_cookie = {
            "name": record["name"], "value": record["value"],
            "domain": bare if host_only else "." + bare, "path": path,
            "secure": secure, "httpOnly": bool(record.get("httpOnly", False)),
        }
        if expires is not None:
            browser_cookie["expires"] = expires
        same_site = str(record.get("sameSite", "")).lower()
        if same_site in {"strict", "lax", "none", "no_restriction"}:
            browser_cookie["sameSite"] = {
                "strict": "Strict", "lax": "Lax", "none": "None", "no_restriction": "None",
            }[same_site]
        browser_cookies.append(browser_cookie)
    session._m3u8_browser_cookies = browser_cookies
