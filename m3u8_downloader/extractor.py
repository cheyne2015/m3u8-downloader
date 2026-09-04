"""网页 m3u8 链接抽取模块：从网页 HTML / 外链 JS 中抽出 m3u8 候选并估算大小.

设计要点（见 docs/system_design.md §3.3）：

* 本模块是**门面层**，被 ``cli.py`` / ``gui.py`` 调用；
* 仅抛出 :class:`ExtractError` 的子类（``PageFetchError`` /
  ``DeepModeUnavailableError`` / ``NoCandidateFoundError``），入口层按类型给提示；
* 大小估算委托给 :mod:`m3u8_downloader.estimator`（纯计算层，永不抛异常）；
* 静态解析用 HTML + 递归外链 JS 两条路；
* 深度模式（无头浏览器）有两条路线，按顺序择优（见 :func:`_deep_extract`）：

  1. **进程内**：本进程能 import playwright（源码运行 / 冻结 EXE 注入成功）；
  2. **子进程**：冻结 EXE 内 import 外部 site-packages 不可行，改为调用系统
     Python 执行随包分发的 :mod:`m3u8_downloader.deep_worker`，解析其 JSON 输出。

  两条路都不可用时才抛 ``DeepModeUnavailableError``（提示按原因区分，见
  :func:`_explain_worker_failure`）。
"""

import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from typing import Callable, List, Optional, Tuple
from urllib.parse import urljoin

import requests

from m3u8_downloader import utils
from m3u8_downloader.estimator import MAX_ESTIMATE_WORKERS, SizeEstimate, estimate_many, ESTIMATE_TIMEOUT
from m3u8_downloader.utils import format_duration, format_file_size

# bs4 为可选依赖：缺失时降级为纯正则扫描，功能不缺失（仅 title 更弱）。
try:  # pragma: no cover - 依赖探测
    from bs4 import BeautifulSoup
except ImportError:  # pragma: no cover
    BeautifulSoup = None


def _ensure_playwright_browsers_path() -> None:
    """深度模式启动前确保浏览器目录已设置（用户本机默认 F:\\gadgets\\playwright-browsers）。

    若用户已自行设置 PLAYWRIGHT_BROWSERS_PATH 环境变量则尊重，不覆盖。
    playwright 会在启动时自动读取该环境变量定位浏览器内核。
    """
    if "PLAYWRIGHT_BROWSERS_PATH" not in os.environ:
        os.environ["PLAYWRIGHT_BROWSERS_PATH"] = DEFAULT_PLAYWRIGHT_BROWSERS_PATH


# 冻结 EXE 未打包 playwright（build.spec 显式 exclude，避免体积爆炸）。
# 本机若已安装 playwright，则把系统 Python 的 site-packages 注入 sys.path，
# 让冻结 EXE 直接复用用户的 playwright（含 node 驱动与 PLAYWRIGHT_BROWSERS_PATH 的 Chromium）。
_SYSTEM_PLAYWRIGHT_INJECTED = False


def _playwright_importable() -> bool:
    try:
        import playwright  # noqa: F401

        return True
    except Exception:
        return False


def _inject_system_playwright() -> bool:
    """尝试从本机系统 Python 注入 playwright，使深度模式在冻结 EXE 中可用。

    仅在本机已安装 playwright 时生效。成功返回 True（此后 ``import playwright`` 可用）。

    Returns:
        是否成功注入（即当前 ``import playwright`` 可用）.
    """
    global _SYSTEM_PLAYWRIGHT_INJECTED
    if _SYSTEM_PLAYWRIGHT_INJECTED:
        return _playwright_importable()

    import shutil
    import subprocess

    candidates: List[str] = []

    # 1) 通过 py 启动器探测 Python 3.13 的 site-packages（最常见安装位置）
    py = shutil.which("py") or shutil.which("py.exe")
    if py:
        try:
            out = subprocess.run(
                [py, "-3.13", "-c",
                 "import site; sp=site.getsitepackages(); print(sp[0] if sp else '')"],
                capture_output=True, text=True, timeout=15,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            ).stdout.strip()
            if out and os.path.isdir(out):
                candidates.append(out)
        except Exception:
            pass

    # 2) 用户目录下的 Python 安装（扫描 Lib/site-packages）
    local_programs = os.path.join(
        os.environ.get("LOCALAPPDATA", r"C:\Users\cheyn\AppData\Local"),
        "Programs", "Python",
    )
    if os.path.isdir(local_programs):
        for name in sorted(os.listdir(local_programs)):
            sp = os.path.join(local_programs, name, "Lib", "site-packages")
            if os.path.isdir(sp):
                candidates.append(sp)

    # 3) 兜底硬编码路径
    for base in (
        r"C:\Users\cheyn\AppData\Local\Programs\Python\Python313\Lib\site-packages",
        r"C:\Users\cheyn\AppData\Local\Programs\Python\Python312\Lib\site-packages",
        r"C:\Python313\Lib\site-packages",
    ):
        if os.path.isdir(base):
            candidates.append(base)

    for sp in candidates:
        if sp in sys.path:
            continue
        if os.path.isdir(os.path.join(sp, "playwright")):
            sys.path.insert(0, sp)
            if _playwright_importable():
                _SYSTEM_PLAYWRIGHT_INJECTED = True
                return True
            sys.path.remove(sp)
    return False


# ===== 模块常量（不要散落魔数） =====
MAX_JS_FILES: int = 10                      # 最多递归下载的外链 JS 数量
MAX_PAGE_BYTES: int = 5 * 1024 * 1024       # 网页/JS 单文件读取上限，防超大文件
DEEP_WAIT_MS: int = 5000                    # 深度模式等待网络静默毫秒数
DEEP_SUBPROCESS_MARGIN_SEC: int = 60        # 子进程路线在导航超时外预留的启动/收尾时间
DEEP_WORKER_NAME: str = "deep_worker.py"    # 随包分发的深度模式子进程脚本

# 深度模式请求拦截：仅当 media 类型 URL 以这些分片扩展名结尾时才 abort
_SEGMENT_EXTS = frozenset({".ts", ".mp4", ".m4s", ".m4a", ".aac", ".webm"})
# 收集到 m3u8 后，再静默等待的毫秒数（确认没有新的 m3u8 才收工）
_SETTLE_MS = 2500
# 至少收集的毫秒数（避免页面刚打开时的瞬间早期请求造成过早停等）
_MIN_COLLECT_MS = 800
DEFAULT_PLAYWRIGHT_BROWSERS_PATH: str = r"F:\gadgets\playwright-browsers"
MAX_CANDIDATE_URL_LEN: int = 512            # 候选 URL 长度上限，防超长脏串
# 兜底：显式列出的系统 Python 解释器（无任何命令能解析到时使用）
_FALLBACK_PYTHON_PATHS = (
    r"C:\Users\cheyn\AppData\Local\Programs\Python\Python313\python.exe",
)

# 外链 JS 黑名单关键词：这些基本不可能是播放器逻辑，直接跳过
_JS_BLACKLIST = ("jquery", "analytics", "gtag", "polyfill")

# 源可信度（去重时保留高可信者）
_SOURCE_RANK = {"html": 0, "inline_js": 1, "js": 2, "deep": 3}

# ===== 正则（两条互补，扫完取并集） =====
# 排除字符集额外去掉反斜杠 \\：否则 index.m3u8\ 会被整段当成 URL 抓进去，
# 导致请求非法路径、服务端断 TLS（SSLEOFError）。
M3U8_ABS_RE = re.compile(
    r'https?://[^\s"\'()\[\]<>\\]+?\.m3u8[^\s"\'()\[\]<>\\]*', re.I
)
M3U8_QUOTED_RE = re.compile(
    r'["\']([^"\'\s()\[\]<>\\]+?\.m3u8[^"\'\s()\[\]<>\\]*)["\']', re.I
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
        deep: 是否来自深度模式（无头浏览器）抽取；等价于 ``source == "deep"``.
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

    @property
    def deep(self) -> bool:
        """是否来自深度模式（无头浏览器）抽取；等价于 ``source == "deep"``.

        作为属性而非字段，避免与 :func:`_dedupe` 在合并时改写 ``source`` 后脱节。
        """
        return self.source == "deep"

    def display_mode(self) -> str:
        """展示用提取模式标签：``深度``（无头浏览器）/ ``普通``（HTML + JS 静态扫描）."""
        return "深度" if self.deep else "普通"


# ===== 内部工具 =====
def _is_m3u8_like(raw: str) -> Optional[str]:
    """判定一段文本是否为「疑似 m3u8 URL」，并清洗掉尾部垃圾字符.

    这是**单一事实来源**：静态扫描（:func:`_scan_text`）与深度模式子进程
    （``m3u8_downloader/deep_worker.py``）共用同一套判定，避免两份逻辑漂移。
    worker 在冻结 EXE 中无法 import 本模块时，会回退到自己的内联副本，
    由 ``tests/test_deep_worker.py`` 的一致性断言兜底。

    Args:
        raw: 原始命中串（可能带反斜杠 / 句点 / 逗号 / 叹号等尾部垃圾）.

    Returns:
        清洗后的原始串；不是疑似 m3u8 URL（空、不含 ``.m3u8``、超长）时返回 None.
    """
    if not raw:
        return None
    # 剥掉尾部非法字符：正则/网络响应可能把反斜杠、句点、逗号等尾部垃圾一起捕获，
    # 不清理会导致请求非法路径、服务端断 TLS（SSLEOFError）。
    cleaned = raw.strip().rstrip("\\.,;:!")
    if ".m3u8" not in cleaned.lower():
        return None
    if len(cleaned) > MAX_CANDIDATE_URL_LEN:
        return None
    return cleaned


def _normalize_candidate_url(raw: str, base_url: str) -> Optional[str]:
    """把原始命中（可能是相对/协议相对路径）归一化为绝对 http(s) URL.

    Args:
        raw: 正则命中到的原始字符串.
        base_url: 用于 ``urljoin`` 的基准（页面 URL 或 JS 自身 URL）.

    Returns:
        归一化后的绝对 URL；不符合规范（非 http/https、超长、非 .m3u8）返回 None.
    """
    cleaned = _is_m3u8_like(raw)
    if cleaned is None:
        return None
    joined = urljoin(base_url, cleaned) if base_url else cleaned
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


def fetch_page_title(
    page_url: str,
    no_proxy: bool = False,
    proxy: Optional[str] = None,
    timeout: int = 30,
) -> str:
    """抓取网页标题（``<title>`` 优先，回退 ``og:title`` / ``twitter:title``）.

    仅在静态模式下用于自动填充输出文件名；与 :func:`extract_m3u8_from_page`
    共享同一套 HTTP 配置（代理 / 直连 / UA）。

    Args:
        page_url: 网页绝对 URL.
        no_proxy: 为 True 时直连、跳过系统代理环境变量.
        proxy: 手动代理地址（如 ``127.0.0.1:7897``）；与 no_proxy 互斥.
        timeout: HTTP 超时秒数.

    Returns:
        标题文本（已 ``strip``）；抓取或解析失败时返回空字符串（调用方回退默认名）.
    """
    session = utils.create_http_session(timeout=timeout, no_proxy=no_proxy, proxy=proxy)
    try:
        try:
            html = _fetch_page(page_url, session, timeout)
        except Exception:
            return ""
        return _parse_page_title(html)
    finally:
        try:
            session.close()
        except Exception:
            pass


def _parse_page_title(html: str) -> str:
    """从 HTML 文本里抽出页面标题，优先 ``<title>``，回退 og/twitter meta.

    Args:
        html: 网页 HTML 文本.

    Returns:
        标题字符串（已 strip）；无法取得时返回空字符串.
    """
    if not html:
        return ""
    if BeautifulSoup is not None:
        try:
            soup = BeautifulSoup(html, "html.parser")
            title_tag = soup.find("title")
            if title_tag and (title_tag.get_text() or "").strip():
                return title_tag.get_text().strip()
            for prop in ("og:title", "twitter:title"):
                meta = soup.find("meta", attrs={"property": prop}) or soup.find(
                    "meta", attrs={"name": prop}
                )
                if meta and (meta.get("content") or "").strip():
                    return meta.get("content").strip()
        except Exception:
            pass
    # bs4 缺失或解析失败：退化为简单的 <title>...</title> 正则
    m = re.search(r"<title[^>]*>(.*?)</title>", html, re.IGNORECASE | re.DOTALL)
    if m:
        return m.group(1).strip()
    return ""


def _find_system_python() -> Optional[List[str]]:
    """定位可用于运行深度模式 worker 的系统 Python 解释器.

    冻结 EXE 内无法 import 外部 site-packages，深度模式改为**调用系统 Python
    执行随包分发的** ``deep_worker.py``。按顺序探测：

    1. ``py -3.13``（Windows Python 启动器，首选）；
    2. ``python``（PATH 上的默认解释器）；
    3. ``LOCALAPPDATA\\Programs\\Python\\Python3xx\\python.exe`` 与显式兜底路径。

    Returns:
        可直接交给 :func:`subprocess.run` 的命令列表（解释器路径 + 版本参数）；
        一个可用解释器都找不到时返回 None.
    """
    py = shutil.which("py") or shutil.which("py.exe")
    if py and os.path.isfile(py):
        return [py, "-3.13"]

    python = shutil.which("python") or shutil.which("python.exe")
    if python and os.path.isfile(python):
        return [python]

    candidates: List[str] = list(_FALLBACK_PYTHON_PATHS)
    local_appdata = os.environ.get("LOCALAPPDATA", "")
    if local_appdata:
        base = os.path.join(local_appdata, "Programs", "Python")
        for name in ("Python313", "Python312", "Python311"):
            candidates.append(os.path.join(base, name, "python.exe"))

    for path in candidates:
        if os.path.isfile(path):
            return [path]
    return None


def _deep_worker_path() -> str:
    """深度模式 worker 脚本的绝对路径.

    * 冻结 EXE：``sys._MEIPASS\\m3u8_downloader\\deep_worker.py``（build.spec 随包分发）；
    * 源码运行：与 ``extractor.py`` 同目录.

    Returns:
        worker 脚本绝对路径（不保证存在）.
    """
    meipass = getattr(sys, "_MEIPASS", "")
    if meipass:
        return os.path.join(meipass, "m3u8_downloader", DEEP_WORKER_NAME)
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), DEEP_WORKER_NAME)


def _deep_worker_available() -> bool:
    """子进程路线是否具备条件.

    只做**文件存在性**探测（解释器 + worker 脚本），既不 import playwright
    也不启动子进程，因此可以安全地被 GUI 在启动时调用。

    Returns:
        True 表示可以用系统 Python 跑 worker 完成深度模式.
    """
    return _find_system_python() is not None and os.path.isfile(_deep_worker_path())


def _try_import_sync_playwright() -> Optional[Callable]:
    """尝试导入 ``playwright.sync_api.sync_playwright``.

    Returns:
        成功返回该可调用对象；失败（未安装 / 依赖不全）返回 None.
    """
    try:
        from playwright.sync_api import sync_playwright

        return sync_playwright
    except Exception:
        return None


def _explain_worker_failure(returncode: int, stderr: str) -> str:
    """把 worker 的非 0 退出码翻译成可操作的人话提示.

    Args:
        returncode: worker 进程退出码.
        stderr: worker 的 stderr 全文.

    Returns:
        面向用户的中文提示（含具体修复命令）.
    """
    low = (stderr or "").lower()
    # 注意顺序：浏览器内核缺失的报错原文里含 "playwright install chromium"，
    # 必须先于「缺 playwright 模块」判定，否则会被误判为缺模块。
    if (
        "executable doesn't exist" in low
        or "playwright install" in low
        or ("executable" in low and "browsertype.launch" in low)
    ):
        return (
            "深度模式缺少 Chromium 浏览器内核，请在系统 Python 中执行：\n"
            "  playwright install chromium"
        )
    if (
        "modulenotfounderror" in low
        or "no module named" in low
        or "缺少 playwright" in (stderr or "")
    ):
        return (
            "深度模式需要 playwright，请在系统 Python 中执行：\n"
            "  pip install playwright"
        )
    tail = (stderr or "").strip().splitlines()[-5:]
    detail = "\n".join(tail) if tail else f"（退出码 {returncode}，无错误输出）"
    return f"深度模式执行失败（退出码 {returncode}）：\n{detail}"


def _terminate_process(proc) -> None:
    """尽力终止子进程（先 terminate，超时后 kill），不抛异常."""
    try:
        proc.terminate()
    except Exception:
        pass
    try:
        proc.wait(timeout=5)
    except Exception:
        try:
            proc.kill()
        except Exception:
            pass


def _deep_extract_subprocess(
    url: str,
    timeout: int = 30,
    wait_ms: int = DEEP_WAIT_MS,
    proxy: Optional[str] = None,
    stop_event: Optional[threading.Event] = None,
) -> Tuple[List[Candidate], str]:
    """子进程路线深度抽取：调用系统 Python 执行随包分发的 ``deep_worker.py``.

    冻结 EXE 内无法 import 外部 site-packages，这是**冻结环境下唯一可用**的
    深度模式路线；worker 用 playwright 抓页面并把 URL 列表以 JSON 打印到
    stdout，本函数解析后构造候选。

    Args:
        url: 页面绝对 URL.
        timeout: 导航超时秒数.
        wait_ms: 网络静默后额外等待毫秒数.
        stop_event: 可选停止信号；被设置时立即终止子进程并抛
            :class:`DeepModeUnavailableError`（用于「停止提取」）。

    Returns:
        ``(candidates, title)`` 元组：去重后的候选列表（可能为空）+ 页面标题
        （由 worker 从 ``page.title()`` 带出，可能为空字符串）.

    Raises:
        DeepModeUnavailableError: 无可用解释器 / worker 缺失 / worker 失败 / 输出无法解析
            / 被停止 / 超时.
    """
    python_cmd = _find_system_python()
    if not python_cmd:
        raise DeepModeUnavailableError(
            "深度模式需要一个系统 Python 来运行内置抓取脚本，但未找到可用解释器。\n"
            "请安装 Python（或确保 py -3.13 / python 在 PATH 中）后重试。"
        )

    worker = _deep_worker_path()
    if not os.path.isfile(worker):
        raise DeepModeUnavailableError(
            f"深度模式抓取脚本缺失：{worker}\n"
            "安装包可能已损坏，请重新下载安装本程序。"
        )

    # 浏览器目录：尊重用户自定义环境变量，未设置时用本机默认目录（setdefault 语义）
    _ensure_playwright_browsers_path()
    cmd: List[str] = list(python_cmd) + [
        worker,
        "--url", url,
        "--timeout", str(int(timeout)),
        "--wait-ms", str(int(wait_ms)),
        "--browsers-path", os.environ["PLAYWRIGHT_BROWSERS_PATH"],
    ]
    total_timeout = int(timeout) + int(wait_ms) // 1000 + DEEP_SUBPROCESS_MARGIN_SEC

    # 强制子进程以 UTF-8 输出：中文 Windows 下子进程 stderr 默认用 GBK 写，
    # 而本侧按 UTF-8 解码会导致中文报错乱码（与 worker 内的 reconfigure 双保险）。
    env = os.environ.copy()
    env.setdefault("PYTHONIOENCODING", "utf-8")
    # 把手动代理透传给子进程 worker（no_proxy 时 extract_m3u8_from_page 已置 proxy=None）
    if proxy:
        env["M3U8_DEEP_PROXY"] = utils._normalize_proxy(proxy)

    try:
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=env,
            creationflags=(
                getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
            ),
        )
    except OSError as exc:
        raise DeepModeUnavailableError(
            f"无法启动系统 Python（{' '.join(python_cmd)}）：{exc}"
        ) from exc

    # 轮询等待进程结束，支持超时与外部停止信号（停止提取时能及时 kill 掉 worker）
    deadline = time.time() + total_timeout
    while proc.poll() is None:
        if stop_event is not None and stop_event.is_set():
            _terminate_process(proc)
            raise DeepModeUnavailableError("深度模式已停止")
        if time.time() > deadline:
            _terminate_process(proc)
            raise DeepModeUnavailableError(
                f"深度模式执行超时（超过 {total_timeout} 秒仍未返回），"
                "可增大 --timeout 或检查网络连通性。"
            )
        time.sleep(0.1)

    stdout = (proc.stdout.read() if proc.stdout else "") or ""
    stderr = (proc.stderr.read() if proc.stderr else "") or ""

    if proc.returncode != 0:
        raise DeepModeUnavailableError(
            _explain_worker_failure(int(proc.returncode), stderr)
        )

    raw_urls: List[str] = []
    title = ""
    try:
        parsed = json.loads(stdout.strip() or "[]")
        if isinstance(parsed, list):
            # 兼容旧版 worker（纯 URL 列表，无标题）
            raw_urls = [str(item) for item in parsed]
        elif isinstance(parsed, dict):
            raw_urls = [str(item) for item in (parsed.get("urls") or [])]
            title = str(parsed.get("title") or "").strip()
    except (ValueError, TypeError) as exc:
        raise DeepModeUnavailableError(
            "深度模式抓取脚本返回了无法解析的输出（可能版本不匹配）：\n"
            f"{stdout[:200]}"
        ) from exc

    candidates: List[Candidate] = []
    for raw in raw_urls:
        normalized = _normalize_candidate_url(raw, url)
        if normalized:
            candidates.append(_new_candidate(normalized, "deep"))
    return _dedupe(candidates), title


# 深度模式可用性缓存（GUI 启动时会调用，避免重复探测）
_DEEP_AVAILABLE_CACHE: Optional[bool] = None


def is_deep_mode_available() -> bool:
    """检测深度模式是否可用.

    两条路线任一可用即为可用：

    1. **进程内**：``import playwright`` 成功（源码运行 / 冻结 EXE 从系统 Python
       注入成功，见 :func:`_inject_system_playwright`）；
    2. **子进程**：能找到系统 Python 解释器且随包的 ``deep_worker.py`` 存在
       （冻结 EXE 的典型路线，见 :func:`_deep_extract_subprocess`）。

    结果会被缓存：探测只做文件存在性检查，不 import playwright、不跑子进程。

    Returns:
        True 表示深度模式可用.
    """
    global _DEEP_AVAILABLE_CACHE
    if _DEEP_AVAILABLE_CACHE is not None:
        return _DEEP_AVAILABLE_CACHE

    result = False
    if _playwright_importable():
        result = True
    elif _deep_worker_available():
        result = True
    else:
        # 最后才尝试注入（会起一次轻量子进程探测 site-packages 路径）
        result = _inject_system_playwright()
    _DEEP_AVAILABLE_CACHE = result
    return result


def _on_response(resp, collected: List[str]) -> None:
    """playwright response 事件回调：收集含 .m3u8 的响应 URL."""
    try:
        req_url = resp.url or ""
    except Exception:
        return
    if ".m3u8" in req_url.lower():
        collected.append(req_url)


def _safe_abort(route) -> None:
    """「安全」请求拦截：只拦无助于 m3u8 发现的静态资源与分片，其余放行.

    仅 abort ``image`` / ``font`` / ``stylesheet``；对 ``media`` 类型只在 URL 以常见
    分片扩展名（.ts/.mp4/.m4s/.m4a/.aac/.webm）结尾时才拦截，避免误杀没有 ``.m3u8``
    后缀的播放列表（有些站点用 /playlist/xxxx 这种地址提供 m3u8）。
    """
    try:
        resource_type = (route.request.resource_type or "").lower()
    except Exception:
        resource_type = ""
    if resource_type in ("image", "font", "stylesheet"):
        try:
            route.abort()
        except Exception:
            pass
        return
    if resource_type == "media":
        try:
            url_lower = (route.request.url or "").lower()
        except Exception:
            url_lower = ""
        if any(url_lower.endswith(ext) for ext in _SEGMENT_EXTS):
            try:
                route.abort()
            except Exception:
                pass
            return
    try:
        route.continue_()
    except Exception:
        pass


def _deep_extract_inprocess(
    sync_playwright,
    url: str,
    timeout: int = 30,
    wait_ms: int = DEEP_WAIT_MS,
    proxy: Optional[str] = None,
    stop_event: Optional[threading.Event] = None,
) -> Tuple[List[Candidate], str]:
    """进程内深度抽取（当前进程已能 import playwright 时走这条路，最快）.

    Args:
        sync_playwright: ``playwright.sync_api.sync_playwright`` 可调用对象.
        url: 页面绝对 URL.
        timeout: 导航超时秒数.
        wait_ms: 网络静默后额外等待毫秒数.
        proxy: 手动代理地址（如 ``127.0.0.1:7897``）；非空时浏览器走代理.
        stop_event: 可选停止信号；被设置时提前结束静默等待并关闭浏览器（用于「停止提取」）.

    Returns:
        ``(candidates, title)`` 元组：去重后的候选列表（可能为空）+ 页面标题
        （``page.title()``，可能为空字符串）.

    Raises:
        DeepModeUnavailableError: 浏览器启动或页面执行失败.
    """
    # 深度模式启动前确保浏览器目录已设置（尊重用户自定义的环境变量）
    _ensure_playwright_browsers_path()

    candidates: List[Candidate] = []
    collected: List[str] = []
    _last_new = [time.time()]
    content = ""
    title = ""
    try:
        with sync_playwright() as p:
            # 精简启动参数：禁用 GPU / dev-shm / 扩展 / 首次运行提示；
            # 刻意不加 --no-sandbox：用户粘贴的是不可信网页，沙箱是必要安全边界。
            browser = p.chromium.launch(
                headless=True,
                args=["--disable-gpu", "--disable-dev-shm-usage", "--disable-extensions", "--no-first-run"],
            )
            proxy_cfg: dict = {}
            if proxy:
                proxy_cfg["proxy"] = {"server": utils._normalize_proxy(proxy)}
            page = browser.new_page(**proxy_cfg)

            # 「安全」请求拦截：只拦无助于 m3u8 发现的静态资源与分片。
            try:
                page.route("**/*", _safe_abort)
            except Exception:
                pass

            def _on_response_cb(resp) -> None:
                _on_response(resp, collected)
                if ".m3u8" in (resp.url or "").lower():
                    _last_new[0] = time.time()

            page.on("response", _on_response_cb)
            # 用 commit 而非 domcontentloaded：更早着手收集；导航失败也不直接抛异常，
            # 带着已收集的候选返回，尽可能多给结果。
            try:
                page.goto(url, wait_until="commit", timeout=timeout * 1000)
            except Exception as goto_exc:
                print(
                    f"深度模式导航未完成，仍返回已收集的候选：{goto_exc}",
                    file=sys.stderr, flush=True,
                )
            # 静默窗口收集：收集到 m3u8 后继续静默 _SETTLE_MS 毫秒确认无新请求，
            # 且至少收集 _MIN_COLLECT_MS 毫秒；wait_ms 为总预算上限。
            deadline = time.time() + int(wait_ms) / 1000.0
            start = time.time()
            while time.time() < deadline:
                if stop_event is not None and stop_event.is_set():
                    break  # 被停止：提前结束静默等待
                quiet_ms = (time.time() - _last_new[0]) * 1000
                if (
                    collected
                    and quiet_ms >= _SETTLE_MS
                    and (time.time() - start) * 1000 >= _MIN_COLLECT_MS
                ):
                    break
                try:
                    page.wait_for_timeout(500)
                except Exception:
                    pass
            try:
                content = page.content() or ""
            except Exception:
                content = ""
            try:
                title = page.title() or ""
            except Exception:
                title = ""
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
    return _dedupe(candidates), title


def _deep_extract_with_title(
    url: str,
    timeout: int = 30,
    wait_ms: int = DEEP_WAIT_MS,
    proxy: Optional[str] = None,
    stop_event: Optional[threading.Event] = None,
) -> Tuple[List[Candidate], str]:
    """无头浏览器深度抽取（playwright），额外返回页面标题.

    两条路线，按顺序择优：

    1. **进程内**：当前进程能 import playwright（源码运行 / 冻结 EXE 从系统
       Python 注入成功）时直接跑，最快；
    2. **子进程**：进程内不可用时（**冻结 EXE 的典型情况**），调用系统 Python
       执行随包分发的 ``deep_worker.py``，解析其 JSON 输出。

    Args:
        url: 页面绝对 URL.
        timeout: 导航超时秒数.
        wait_ms: 网络静默后额外等待毫秒数.
        stop_event: 可选停止信号（用于「停止提取」），透传给底层路线.

    Returns:
        ``(candidates, title)`` 元组：去重后的候选列表（可能为空）+ 页面标题（可能为空）.

    Raises:
        DeepModeUnavailableError: playwright 缺失、浏览器未安装或执行失败.
    """
    if not _playwright_importable():
        # 冻结 EXE 未打包 playwright：先尝试从本机系统 Python 注入，复用用户已装 playwright
        _inject_system_playwright()

    sync_playwright = _try_import_sync_playwright()
    if sync_playwright is not None:
        return _deep_extract_inprocess(
            sync_playwright, url, timeout, wait_ms, proxy=proxy, stop_event=stop_event
        )

    # 进程内跑不通 → 无条件回退子进程。
    #
    # 注意：早期实现会在「playwright 可 import 但 sync_api 导入失败」时直接抛
    # 「依赖不完整」而拒绝回退，这在 GUI 中是致命的——GUI 启动时调用
    # is_deep_mode_available() 已经执行过 _inject_system_playwright()，使
    # _playwright_importable() 此后恒为 True，于是 GUI 永远走不到子进程；
    # 而 CLI 是干净进程、未被注入副作用污染，反而能正常回退（表现为 CLI 可用、
    # GUI 不可用）。子进程路线用的同样是系统 Python，不存在"掩盖问题"的顾虑。
    # 无条件回退子进程。「无可用解释器」「worker 缺失」等具体诊断由
    # _deep_extract_subprocess 给出，比在此处笼统提示更精确。
    return _deep_extract_subprocess(url, timeout, wait_ms, proxy=proxy, stop_event=stop_event)


def _deep_extract(
    url: str,
    timeout: int = 30,
    wait_ms: int = DEEP_WAIT_MS,
    proxy: Optional[str] = None,
    stop_event: Optional[threading.Event] = None,
) -> List[Candidate]:
    """无头浏览器深度抽取（playwright），仅返回候选列表（丢弃页面标题）.

    内部复用 :func:`_deep_extract_with_title`；需要标题时请直接调用后者。
    """
    candidates, _ = _deep_extract_with_title(url, timeout, wait_ms, proxy=proxy, stop_event=stop_event)
    return candidates


# ===== 门面 =====
def extract_m3u8_from_page_with_title(
    url: str,
    session: Optional[requests.Session] = None,
    deep: bool = False,
    timeout: int = 30,
    estimate: bool = True,
    max_workers: int = 8,
    no_proxy: bool = False,
    proxy: Optional[str] = None,
    stop_event: Optional[threading.Event] = None,
) -> Tuple[List[Candidate], str]:
    """从网页抽取所有 m3u8 候选链接 + 页面标题（可选估算大小）.

    与 :func:`extract_m3u8_from_page` 等价，但额外返回页面标题。标题的取得**零额外
    请求**：深度模式来自 playwright ``page.title()``，普通模式复用已抓取的 HTML
    解析 ``<title>``（避免二次抓取导致慢 / 拿不到）。

    **本函数只抛出** :class:`ExtractError` 子类；网络/解析异常会被包装为对应异常，
    绝不向外抛裸 ``requests``/``bs4`` 异常。

    Args:
        url: 网页 URL（可省略协议，内部补 ``https://``）.
        session: 复用 HTTP 会话；为 None 时内部自建并在结束时关闭.
        deep: 是否使用无头浏览器深度模式（隐含静态解析被跳过）.
        timeout: HTTP 超时秒数.
        estimate: 是否并发估算大小（False 时秒出列表，大小显示 ``-``）.
        max_workers: 抽取/估算并发数（内部钳制到 1..MAX_ESTIMATE_WORKERS）.
        no_proxy: 为 True 时所有请求直连、跳过系统代理环境变量.
        proxy: 手动指定代理地址（如 ``127.0.0.1:7897``）；与 no_proxy 互斥，
            同时传入时 no_proxy 优先（直连）。
        stop_event: 可选停止信号（用于「停止提取」）；深度模式下被设置时终止抓取。

    Returns:
        ``(candidates, title)`` 元组：候选列表（已去重、排序：reachable 优先 →
        大小降序 → url 字典序）+ 页面标题（可能为空字符串）.

    Raises:
        PageFetchError: 网页拉取失败 / 这是 m3u8 直链.
        DeepModeUnavailableError: 深度模式依赖缺失.
        NoCandidateFoundError: 一个候选都没抽到.
    """
    own_session = session is None
    if own_session:
        session = utils.create_http_session(
            timeout, no_proxy=no_proxy, proxy=proxy
        )

    page_url = utils.normalize_page_url(url)
    workers = max(1, min(int(max_workers or 1), MAX_ESTIMATE_WORKERS))
    title = ""

    try:
        if deep:
            # no_proxy 优先：直连时不给 headless 浏览器传代理
            proxy_for_deep = None if no_proxy else proxy
            candidates, title = _deep_extract_with_title(
                page_url, timeout, proxy=proxy_for_deep, stop_event=stop_event
            )
        else:
            try:
                html = _fetch_page(page_url, session, timeout)
            except PageFetchError:
                raise

            # 复用已抓取的 HTML 解析标题，避免二次抓取（快 + 对反爬更鲁棒）
            title = _parse_page_title(html)
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

        # 并发估算大小（估算用独立的短超时，避免拖慢提取结果呈现）
        if estimate:
            results = estimate_many(
                [c.url for c in candidates],
                session=session,
                timeout=min(int(timeout or ESTIMATE_TIMEOUT), ESTIMATE_TIMEOUT),
                max_workers=workers,
            )
            for c in candidates:
                est = results.get(c.url)
                if est is not None:
                    c.apply_estimate(est)

        # 排序：reachable 优先 → 大小降序 → url 字典序
        candidates.sort(key=lambda c: (not c.reachable, -c.estimated_size, c.url))
        return candidates, title
    finally:
        if own_session and session is not None:
            try:
                session.close()
            except Exception:
                pass


def extract_m3u8_from_page(
    url: str,
    session: Optional[requests.Session] = None,
    deep: bool = False,
    timeout: int = 30,
    estimate: bool = True,
    max_workers: int = 8,
    no_proxy: bool = False,
    proxy: Optional[str] = None,
    stop_event: Optional[threading.Event] = None,
) -> List[Candidate]:
    """从网页抽取所有 m3u8 候选链接（可选估算大小），仅返回候选列表.

    需要标题时请改用 :func:`extract_m3u8_from_page_with_title`。
    """
    candidates, _ = extract_m3u8_from_page_with_title(
        url,
        session=session,
        deep=deep,
        timeout=timeout,
        estimate=estimate,
        max_workers=max_workers,
        no_proxy=no_proxy,
        proxy=proxy,
        stop_event=stop_event,
    )
    return candidates
