"""网页 m3u8 链接抽取模块：从网页 HTML / 外链 JS 中抽出 m3u8 候选并估算大小.

设计要点（见 docs/system_design.md §3.3）：

* 本模块是**门面层**，被 ``cli.py`` / ``gui.py`` 调用；
* 仅抛出 :class:`ExtractError` 的子类（``PageFetchError`` /
  ``DeepModeUnavailableError`` / ``NoCandidateFoundError``），入口层按类型给提示；
* 大小估算委托给 :mod:`m3u8_downloader.estimator`（纯计算层，永不抛异常）；
* 静态解析用 HTML + 递归外链 JS 两条路；深度模式（无头浏览器）只留接口，
  ``playwright`` 缺失时优雅降级为 ``DeepModeUnavailableError``。
"""

import re
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from typing import List, Optional
from urllib.parse import urljoin

import requests

from m3u8_downloader import utils
from m3u8_downloader.estimator import MAX_ESTIMATE_WORKERS, SizeEstimate, estimate_many
from m3u8_downloader.utils import format_duration, format_file_size

# bs4 为可选依赖：缺失时降级为纯正则扫描，功能不缺失（仅 title 更弱）。
try:  # pragma: no cover - 依赖探测
    from bs4 import BeautifulSoup
except ImportError:  # pragma: no cover
    BeautifulSoup = None


# ===== 模块常量（不要散落魔数） =====
MAX_JS_FILES: int = 10                      # 最多递归下载的外链 JS 数量
MAX_PAGE_BYTES: int = 5 * 1024 * 1024       # 网页/JS 单文件读取上限，防超大文件
DEEP_WAIT_MS: int = 5000                    # 深度模式等待网络静默毫秒数

# 外链 JS 黑名单关键词：这些基本不可能是播放器逻辑，直接跳过
_JS_BLACKLIST = ("jquery", "analytics", "gtag", "polyfill")

# 源可信度（去重时保留高可信者）
_SOURCE_RANK = {"html": 0, "inline_js": 1, "js": 2, "deep": 3}

# ===== 正则（两条互补，扫完取并集） =====
M3U8_ABS_RE = re.compile(
    r'https?://[^\s"\'()\[\]<>]+?\.m3u8[^\s"\'()\[\]<>]*', re.I
)
M3U8_QUOTED_RE = re.compile(
    r'["\']([^"\'\s()\[\]<>]+?\.m3u8[^"\'\s()\[\]<>]*)["\']', re.I
)


# ===== 异常体系 =====
class ExtractError(RuntimeError):
    """抽取层所有异常的基类."""


class PageFetchError(ExtractError):
    """网页拉取失败 / 返回的是 m3u8 直链而非 HTML."""


class DeepModeUnavailableError(ExtractError):
    """深度模式不可用（playwright 缺失或浏览器未安装）."""


class NoCandidateFoundError(ExtractError):
    """一个候选都没抽到."""


# ===== 候选数据类 =====
@dataclass
class Candidate:
    """网页中抽到的单个 m3u8 候选链接.

    Attributes:
        url: 绝对 URL（已 ``urljoin`` 归一化，保留 query）.
        title: 来自 label/title/<a> 文本/文件名，可空.
        source: ``"html"`` | ``"inline_js"`` | ``"js"`` | ``"deep"``.
        is_master: 是否是 master playlist（多码率列表）.
        estimated_size: 估计字节数；``0`` 表示未知.
        duration: 时长（秒）；``0.0`` 表示未知.
        bandwidth: 码率（bits per second）；``0`` 表示未知.
        segment_count: TS 片段数量.
        estimate_method: 估算方法（``"bandwidth"`` / ``"segment_head"`` / ``"unknown"``）.
        estimate_error: 估算失败原因（非空即 ``reachable=False``）.
        reachable: playlist 是否成功拉取解析.
    """

    url: str
    title: str = ""
    source: str = "html"
    is_master: bool = False
    estimated_size: int = 0
    duration: float = 0.0
    bandwidth: int = 0
    segment_count: int = 0
    estimate_method: str = "unknown"
    estimate_error: str = ""
    reachable: bool = True

    def apply_estimate(self, est: SizeEstimate) -> None:
        """把估算结果回填到候选字段.

        Args:
            est: :func:`m3u8_downloader.estimator.estimate_size` 的结果.
        """
        self.estimated_size = est.size_bytes
        self.duration = est.duration
        self.bandwidth = est.bandwidth
        self.segment_count = est.segment_count
        self.estimate_method = est.method
        self.estimate_error = est.error
        self.is_master = est.is_master
        if est.error:
            self.reachable = False

    def display_size(self) -> str:
        """展示用估计大小：``≈ 1.23 GB`` / ``未知``."""
        if self.estimated_size > 0:
            return f"≈ {format_file_size(self.estimated_size)}"
        return "未知"

    def display_duration(self) -> str:
        """展示用时长：``format_duration`` 或 ``-``."""
        if self.duration > 0:
            return format_duration(self.duration)
        return "-"

    def display_bandwidth(self) -> str:
        """展示用码率：``2.5 Mbps`` 或 ``-``."""
        if self.bandwidth > 0:
            return f"{self.bandwidth / 1e6:.1f} Mbps"
        return "-"


# ===== 内部工具 =====
def _normalize_candidate_url(raw: str, base_url: str) -> Optional[str]:
    """把原始命中（可能是相对/协议相对路径）归一化为绝对 http(s) URL.

    Args:
        raw: 正则命中到的原始字符串.
        base_url: 用于 ``urljoin`` 的基准（页面 URL 或 JS 自身 URL）.

    Returns:
        归一化后的绝对 URL；不符合规范（非 http/https、超长、非 .m3u8）返回 None.
    """
    if not raw:
        return None
    raw = raw.strip()
    if ".m3u8" not in raw.lower():
        return None
    if len(raw) > 512:
        return None
    joined = urljoin(base_url, raw) if base_url else raw
    if not joined.startswith(("http://", "https://")):
        return None
    return joined


def _new_candidate(url: str, source: str, title: str = "") -> Candidate:
    """构造一个候选."""
    return Candidate(url=url, source=source, title=title or "")


def _scan_text(text: str, base_url: str, source: str) -> List[Candidate]:
    """对一段文本跑两条正则，抽出 m3u8 候选.

    Args:
        text: 待扫描文本（HTML 全文或 JS 文本）.
        base_url: 相对路径归一化基准.
        source: 来源标记（``"html"`` / ``"inline_js"`` / ``"js"`` / ``"deep"``）.

    Returns:
        Candidate 列表（可能含重复 URL，由上层 ``_dedupe`` 合并）.
    """
    candidates: List[Candidate] = []
    for match in M3U8_ABS_RE.finditer(text):
        url = _normalize_candidate_url(match.group(0), base_url)
        if url:
            candidates.append(_new_candidate(url, source))
    for match in M3U8_QUOTED_RE.finditer(text):
        url = _normalize_candidate_url(match.group(1), base_url)
        if url:
            candidates.append(_new_candidate(url, source))
    return candidates


def _title_from_tag(tag) -> str:
    """从标签取一个可读标题：label → title → 标签文本."""
    label = tag.get("label")
    if label and str(label).strip():
        return str(label).strip()
    title = tag.get("title")
    if title and str(title).strip():
        return str(title).strip()
    text = (tag.get_text() or "").strip()
    if text:
        return text
    return ""


def _extract_from_html(html: str, page_url: str) -> List[Candidate]:
    """从 HTML 文本抽取 m3u8 候选.

    bs4 可用：结构化取标签属性 + 内联 script 文本 + 全文正则互补；
    bs4 不可用：退化为全文正则扫描。

    Args:
        html: 网页 HTML 文本.
        page_url: 页面绝对 URL（用于相对路径归一化）.

    Returns:
        Candidate 列表（未去重）.
    """
    candidates: List[Candidate] = []

    if BeautifulSoup is not None:
        try:
            soup = BeautifulSoup(html, "html.parser")
            # 1. 结构化标签：src / href / data-src / data-url
            for tag in soup.find_all(
                ["source", "video", "audio", "a", "iframe", "embed"]
            ):
                for attr in ("src", "href", "data-src", "data-url"):
                    val = tag.get(attr)
                    if not val:
                        continue
                    url = _normalize_candidate_url(str(val), page_url)
                    if url:
                        candidates.append(
                            _new_candidate(url, "html", _title_from_tag(tag))
                        )
            # 2. 内联 <script> 文本（播放器常在这里拼 URL）
            for script in soup.find_all("script"):
                if script.get("src"):
                    continue
                text = script.string or script.get_text() or ""
                if text:
                    candidates.extend(_scan_text(str(text), page_url, "inline_js"))
            # 3. 全文正则互补：兜底 JSON-LD / 其他非标签文本里的链接
            candidates.extend(_scan_text(html, page_url, "html"))
            return candidates
        except Exception:
            # 解析异常 → 降级为纯正则
            pass

    # bs4 缺失或解析失败：全文正则
    return _scan_text(html, page_url, "html")


def _normalize_js_url(raw: str, base_url: str) -> Optional[str]:
    """把外链 JS 的 src（可能相对/协议相对）归一化为绝对 http(s) URL.

    与 :func:`_normalize_candidate_url` 不同：这里不要求含 ``.m3u8``，
    只要求最终是绝对 http(s) 链接。

    Args:
        raw: ``<script src>`` 原始值.
        base_url: 归一化基准（页面 URL）.

    Returns:
        绝对 URL；不符合规范返回 None.
    """
    if not raw:
        return None
    raw = raw.strip()
    if len(raw) > 512:
        return None
    joined = urljoin(base_url, raw) if base_url else raw
    if not joined.startswith(("http://", "https://")):
        return None
    return joined


def _collect_js_urls(html: str, page_url: str, limit: int) -> List[str]:
    """从 HTML 收集外链 JS URL（黑名单过滤、上限 ``limit``）.

    Args:
        html: 网页 HTML 文本.
        page_url: 页面绝对 URL.
        limit: 最多返回数量（``MAX_JS_FILES``）.

    Returns:
        JS 绝对 URL 列表（去重、已过滤黑名单）.
    """
    urls: List[str] = []

    def _accept(url: str) -> bool:
        if not url or not url.lower().endswith(".js"):
            return False
        low = url.lower()
        if any(b in low for b in _JS_BLACKLIST):
            return False
        return True

    if BeautifulSoup is not None:
        try:
            soup = BeautifulSoup(html, "html.parser")
            for script in soup.find_all("script", src=True):
                src = script.get("src") or ""
                if not src:
                    continue
                url = _normalize_js_url(str(src), page_url)
                if url and _accept(url) and url not in urls:
                    urls.append(url)
                    if len(urls) >= limit:
                        return urls
            return urls
        except Exception:
            pass

    # 正则降级
    for match in re.finditer(
        r'<script[^>]+src=["\']([^"\']+\.js)["\']', html, re.I
    ):
        url = _normalize_js_url(match.group(1), page_url)
        if url and _accept(url) and url not in urls:
            urls.append(url)
            if len(urls) >= limit:
                return urls
    return urls


def _fetch_js(session: requests.Session, js_url: str, timeout: int) -> str:
    """下载单个 JS 文件文本（供线程池调用）.

    Args:
        session: HTTP 会话.
        js_url: JS 绝对 URL.
        timeout: 超时秒数.

    Returns:
        JS 文本；HTTP 非 2xx 时抛 RuntimeError（由调用方捕获跳过）.

    Raises:
        RuntimeError: HTTP 状态码非 2xx.
    """
    response = session.get(js_url, timeout=timeout)
    status = int(getattr(response, "status_code", 200) or 200)
    if status < 200 or status >= 300:
        raise RuntimeError(f"HTTP {status}")
    text = _decode_response(response)
    if len(text) > MAX_PAGE_BYTES:
        text = text[:MAX_PAGE_BYTES]
    return str(text)


def _extract_from_js(js_text: str, js_url: str, page_url: str) -> List[Candidate]:
    """从 JS 文本抽取 m3u8 候选（相对路径基准 = JS 自身 URL）.

    Args:
        js_text: JS 文件文本.
        js_url: JS 文件绝对 URL（作为相对路径基准）.
        page_url: 页面 URL（JS 基准失败时的回退）.

    Returns:
        Candidate 列表.
    """
    base = js_url or page_url
    return _scan_text(js_text, base, "js")


def _dedupe(cands: List[Candidate]) -> List[Candidate]:
    """按 URL 去重并保留最高可信来源与已得标题.

    Args:
        cands: 候选列表（可能含重复 URL）.

    Returns:
        去重后的候选列表，保持首次出现顺序.
    """
    by_url: dict = {}
    order: List[str] = []
    for c in cands:
        url = c.url
        if url in by_url:
            existing = by_url[url]
            # 来源可信度更高者覆盖 source
            if _SOURCE_RANK.get(c.source, 9) < _SOURCE_RANK.get(existing.source, 9):
                existing.source = c.source
            # 标题缺失时用对方补全
            if not existing.title and c.title:
                existing.title = c.title
        else:
            by_url[url] = c
            order.append(url)
    return [by_url[u] for u in order]


def _decode_response(response) -> str:
    """把 HTTP 响应体稳定地解码为 str.

    requests 的 ``response.text`` 在响应未声明 charset 时会用 chardet 猜测,
    对中文 UTF-8 页面常误判为 Latin-1, 导致标题/URL 中文乱码（如
    "线路二" 变成 "çº¿è·¯äº"）。这里改为: HTTP 显式声明编码则尊重声明;
    否则优先按 UTF-8 解码（现代中文站标准）, 失败再退回 apparent_encoding,
    最后 latin-1 兜底（不丢字节）。
    """
    raw = getattr(response, "content", None)
    if isinstance(raw, (bytes, bytearray)):
        enc = getattr(response, "charset", None)
        if enc:
            try:
                return raw.decode(enc)
            except (LookupError, UnicodeDecodeError):
                pass
        try:
            return raw.decode("utf-8")
        except UnicodeDecodeError:
            ae = getattr(response, "apparent_encoding", None)
            if ae:
                try:
                    return raw.decode(ae)
                except (LookupError, UnicodeDecodeError):
                    pass
            return raw.decode("latin-1", "replace")
    # 兼容测试 mock / 非标准响应：退回 text
    return getattr(response, "text", "") or ""


def _fetch_page(url: str, session: requests.Session, timeout: int) -> str:
    """拉取网页 HTML 文本，并校验确实是 HTML 而非 m3u8 直链.

    Args:
        url: 页面绝对 URL.
        session: HTTP 会话.
        timeout: 超时秒数.

    Returns:
        HTML 文本（超长则截断到 ``MAX_PAGE_BYTES``）.

    Raises:
        PageFetchError: HTTP 非 2xx，或返回的是 m3u8 直链.
    """
    try:
        response = session.get(url, timeout=timeout)
    except Exception as exc:
        raise PageFetchError(f"网页拉取失败: {exc}") from exc

    status = int(getattr(response, "status_code", 200) or 200)
    if status < 200 or status >= 300:
        raise PageFetchError(f"网页返回 HTTP {status}")

    ctype = (response.headers.get("Content-Type") or "").lower()
    if (
        "video/" in ctype
        or "application/vnd.apple.mpegurl" in ctype
        or "application/x-mpegurl" in ctype
    ):
        raise PageFetchError(
            "该地址返回的是 m3u8 直链（Content-Type 提示），"
            "请去掉 --from-page 直接作为 m3u8 下载"
        )

    text = _decode_response(response)
    if len(text) > MAX_PAGE_BYTES:
        text = text[:MAX_PAGE_BYTES]
    return text


def is_deep_mode_available() -> bool:
    """检测深度模式依赖（playwright）是否可用.

    Returns:
        True 表示 ``import playwright`` 成功.
    """
    try:
        import playwright  # noqa: F401

        return True
    except ImportError:
        return False


def _on_response(resp, collected: List[str]) -> None:
    """playwright response 事件回调：收集含 .m3u8 的响应 URL."""
    try:
        req_url = resp.url or ""
    except Exception:
        return
    if ".m3u8" in req_url.lower():
        collected.append(req_url)


def _deep_extract(
    url: str, timeout: int = 30, wait_ms: int = DEEP_WAIT_MS
) -> List[Candidate]:
    """无头浏览器深度抽取（playwright）.

    v1 实现要点：监听网络响应中的 .m3u8，并扫描最终 DOM 文本；
    playwright 缺失或浏览器启动失败时转成 :class:`DeepModeUnavailableError`。

    Args:
        url: 页面绝对 URL.
        timeout: 导航超时秒数.
        wait_ms: 网络静默后额外等待毫秒数.

    Returns:
        去重后的候选列表（可能为空）.

    Raises:
        DeepModeUnavailableError: playwright 缺失或浏览器执行失败.
    """
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise DeepModeUnavailableError(
            "深度模式需要 playwright，请先执行：\n"
            "  pip install playwright\n"
            "  playwright install chromium"
        ) from exc

    candidates: List[Candidate] = []
    collected: List[str] = []
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page()
            page.on("response", lambda resp: _on_response(resp, collected))
            page.goto(url, wait_until="networkidle", timeout=timeout * 1000)
            page.wait_for_timeout(wait_ms)
            content = page.content()
            browser.close()
    except Exception as exc:
        raise DeepModeUnavailableError(
            f"深度模式执行失败：{exc}\n"
            "若提示缺少浏览器内核，请执行：playwright install chromium"
        ) from exc

    for raw in collected:
        u = _normalize_candidate_url(raw, url)
        if u:
            candidates.append(_new_candidate(u, "deep"))

    # 最终 DOM 文本也扫一遍（兼容脚本里拼接、但未走网络请求的情形）
    candidates.extend(_scan_text(content, url, "deep"))
    return _dedupe(candidates)


# ===== 门面 =====
def extract_m3u8_from_page(
    url: str,
    session: Optional[requests.Session] = None,
    deep: bool = False,
    timeout: int = 30,
    estimate: bool = True,
    max_workers: int = 8,
) -> List[Candidate]:
    """从网页抽取所有 m3u8 候选链接（可选估算大小）.

    **本函数只抛出** :class:`ExtractError` 子类；网络/解析异常会被包装为对应异常，
    绝不向外抛裸 ``requests``/``bs4`` 异常。

    Args:
        url: 网页 URL（可省略协议，内部补 ``https://``）.
        session: 复用 HTTP 会话；为 None 时内部自建并在结束时关闭.
        deep: 是否使用无头浏览器深度模式（隐含静态解析被跳过）.
        timeout: HTTP 超时秒数.
        estimate: 是否并发估算大小（False 时秒出列表，大小显示 ``-``）.
        max_workers: 抽取/估算并发数（内部钳制到 1..MAX_ESTIMATE_WORKERS）.

    Returns:
        候选列表（已去重、排序：reachable 优先 → 大小降序 → url 字典序）.

    Raises:
        PageFetchError: 网页拉取失败 / 这是 m3u8 直链.
        DeepModeUnavailableError: 深度模式依赖缺失.
        NoCandidateFoundError: 一个候选都没抽到.
    """
    own_session = session is None
    if own_session:
        session = utils.create_http_session(timeout)

    page_url = utils.normalize_page_url(url)
    workers = max(1, min(int(max_workers or 1), MAX_ESTIMATE_WORKERS))

    try:
        if deep:
            candidates = _deep_extract(page_url, timeout)
        else:
            try:
                html = _fetch_page(page_url, session, timeout)
            except PageFetchError:
                raise

            candidates = _extract_from_html(html, page_url)

            # 并发下载外链 JS 并扫描
            js_urls = _collect_js_urls(html, page_url, MAX_JS_FILES)
            if js_urls:
                js_workers = min(workers, len(js_urls))
                if js_workers == 1:
                    for js_url in js_urls:
                        try:
                            js_text = _fetch_js(session, js_url, timeout)
                        except Exception:
                            continue
                        candidates.extend(_extract_from_js(js_text, js_url, page_url))
                else:
                    with ThreadPoolExecutor(max_workers=js_workers) as executor:
                        futures = {
                            executor.submit(_fetch_js, session, u, timeout): u
                            for u in js_urls
                        }
                        for future in as_completed(futures):
                            u = futures[future]
                            try:
                                js_text = future.result()
                            except Exception:
                                continue
                            candidates.extend(_extract_from_js(js_text, u, page_url))

            candidates = _dedupe(candidates)

        if not candidates:
            if deep:
                raise NoCandidateFoundError(
                    "深度模式仍未在该页面找到任何 m3u8 链接，"
                    "可能该页面需要登录或存在 Referer 防盗链。"
                )
            raise NoCandidateFoundError(
                "未在该网页中提取到任何 m3u8 链接。"
                "可尝试 --deep 深度模式（需安装 playwright）。"
            )

        # 并发估算大小
        if estimate:
            results = estimate_many(
                [c.url for c in candidates],
                session=session,
                timeout=timeout,
                max_workers=workers,
            )
            for c in candidates:
                est = results.get(c.url)
                if est is not None:
                    c.apply_estimate(est)

        # 排序：reachable 优先 → 大小降序 → url 字典序
        candidates.sort(key=lambda c: (not c.reachable, -c.estimated_size, c.url))
        return candidates
    finally:
        if own_session and session is not None:
            try:
                session.close()
            except Exception:
                pass
