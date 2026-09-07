#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""深度模式子进程 worker：用系统 Python 的 playwright 抓取页面中的 m3u8 链接.

背景
----
冻结 EXE（PyInstaller）内**无法** import 外部 site-packages（FrozenImporter 使
PathFinder 无法接管），因此 :func:`m3u8_downloader.extractor._deep_extract` 在
冻结环境下改为「调用系统 Python 执行本脚本」：本脚本用 playwright 打开页面、
收集 m3u8 链接，结果以 JSON 打印到 stdout，EXE 侧解析并构造候选。

用法::

    <python> deep_worker.py --url <页面URL> [--timeout 30] [--wait-ms 5000]
                            [--browsers-path <路径>]

协议
----
* 成功：stdout 输出单行 ``{"urls": [...], "title": "..."}``，退出码 ``0``；
* ``--stream``：发现链接时先输出 ``{"event": "candidate", "url": "..."}``，
  每行立即刷新，最后仍输出完整结果；默认协议不变。
* 失败：原因写到 stderr，退出码非 0：

  * ``2`` —— 缺少 playwright（``ModuleNotFoundError``）；
  * ``3`` —— 缺少浏览器内核（``Executable doesn't exist``）；
  * ``4`` —— 其他执行失败。

设计约束
--------
* 除 playwright 外**只依赖标准库**，且**不在模块级 import playwright**
  （否则无法区分「模块缺失」与「运行失败」）；
* 不依赖 ``m3u8_downloader`` 包：可用时复用 :mod:`m3u8_downloader.extractor`
  的判定函数（单一事实来源），不可用时（典型为冻结 EXE，包内只有本文件）
  回退到内联副本，见 :func:`_load_shared_matchers`；
* 所有诊断信息走 **stderr**，stdout 只允许出现 JSON，便于父进程解析。
"""

import argparse
import json
import os
import re
import sys
import time
from typing import Callable, List, Optional, Tuple
from urllib.parse import urljoin

# 浏览器目录默认值：与 extractor._ensure_playwright_browsers_path() 保持一致，
# 仅在环境变量与命令行参数都未指定时使用（setdefault 语义，不覆盖用户设置）。
DEFAULT_BROWSERS_PATH = r"F:\gadgets\playwright-browsers"

EXIT_OK = 0
EXIT_NO_PLAYWRIGHT = 2
EXIT_NO_BROWSER = 3
EXIT_RUNTIME_ERROR = 4

# DOM 就绪后轮询等待的间隔（毫秒）。总预算仍是 --wait-ms，收集到 m3u8 即提前返回。
_POLL_INTERVAL_MS = 500

# 深度模式请求拦截：仅当 media 类型 URL 以这些分片扩展名结尾时才 abort
_SEGMENT_EXTS = frozenset({".ts", ".mp4", ".m4s", ".m4a", ".aac", ".webm"})
# 收集到 m3u8 后，再静默等待的毫秒数（确认没有新的 m3u8 才收工）
_SETTLE_MS = 2500
# 至少收集的毫秒数（避免页面刚打开时的瞬间早期请求造成过早停等）
_MIN_COLLECT_MS = 800


# ===== 判定逻辑（优先复用 extractor，回退内联副本） =====
# ⚠ 以下三处与 m3u8_downloader/extractor.py 中的同名实现保持同步，
#   tests/test_deep_worker.py 有「两份实现一致性」的断言，改动需同步两处。
_FALLBACK_M3U8_ABS_RE = re.compile(
    r'https?://[^\s"\'()\[\]<>\\]+?\.m3u8[^\s"\'()\[\]<>\\]*', re.I
)
_FALLBACK_M3U8_QUOTED_RE = re.compile(
    r'["\']([^"\'\s()\[\]<>\\]+?\.m3u8[^"\'\s()\[\]<>\\]*)["\']', re.I
)

# URL 长度上限，与 extractor._is_m3u8_like 保持一致
_MAX_URL_LEN = 512


def _fallback_is_m3u8_like(raw: str) -> Optional[str]:
    """判定并清洗「疑似 m3u8 URL」（内联副本，仅在无法 import extractor 时使用）.

    Args:
        raw: 原始命中串（可能带反斜杠/句点/逗号等尾部垃圾）.

    Returns:
        清洗后的原始串；不是疑似 m3u8 URL 时返回 None.
    """
    if not raw:
        return None
    cleaned = raw.strip().rstrip("\\.,;:!")
    if ".m3u8" not in cleaned.lower():
        return None
    if len(cleaned) > _MAX_URL_LEN:
        return None
    return cleaned


def _load_shared_matchers() -> Tuple[re.Pattern, re.Pattern, Callable[[str], Optional[str]]]:
    """尽量复用 extractor 的判定逻辑，拿不到时回退到内联副本.

    冻结 EXE 场景下 ``sys._MEIPASS/m3u8_downloader/`` 下只有本脚本一个文件，
    ``m3u8_downloader.extractor`` 不可导入，此时使用内联副本。

    Returns:
        ``(M3U8_ABS_RE, M3U8_QUOTED_RE, _is_m3u8_like)`` 三元组.
    """
    package_parent = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if package_parent and package_parent not in sys.path:
        # append 而非 insert：避免包目录内的 cli/utils 等模块遮蔽同名的标准库/依赖
        sys.path.append(package_parent)
    try:
        from m3u8_downloader.extractor import (  # type: ignore[import-not-found]
            M3U8_ABS_RE,
            M3U8_QUOTED_RE,
            _is_m3u8_like,
        )

        return M3U8_ABS_RE, M3U8_QUOTED_RE, _is_m3u8_like
    except Exception:
        return _FALLBACK_M3U8_ABS_RE, _FALLBACK_M3U8_QUOTED_RE, _fallback_is_m3u8_like


# 模块级解析一次即可（本次进程内恒定）
M3U8_ABS_RE, M3U8_QUOTED_RE, _is_m3u8_like = _load_shared_matchers()


def _log(message: str) -> None:
    """诊断信息统一走 stderr，保证 stdout 只有 JSON."""
    print(message, file=sys.stderr, flush=True)


def _normalize_proxy(proxy: str) -> str:
    """把用户输入的代理地址规范化为带协议头的完整 URL（与父进程一致）.

    Args:
        proxy: 用户输入的代理字符串（可能缺协议头）.

    Returns:
        带协议头的代理 URL；空输入返回空字符串.
    """
    proxy = (proxy or "").strip()
    if not proxy:
        return ""
    if proxy.startswith(("http://", "https://", "socks5://", "socks5h://", "socks4://")):
        return proxy
    return "http://" + proxy


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


def _collect_urls(url: str, timeout: int, wait_ms: int, proxy: str = "",
                  on_candidate: Optional[Callable[[str], None]] = None,
                  on_title: Optional[Callable[[str], None]] = None) -> Tuple[List[str], str]:
    """用 playwright 打开页面并收集 m3u8 链接.

    收集两条路：网络响应中的 URL + 最终 DOM 文本扫描（兼容脚本拼接但
    未真正发起请求的情形）。

    Args:
        url: 页面绝对 URL.
        timeout: 导航超时秒数.
        wait_ms: 网络静默后额外等待毫秒数.
        proxy: 手动代理地址（如 ``127.0.0.1:7897``）；非空时浏览器走代理.
        on_candidate: 每发现一条候选时回调（流式）。
        on_title: 标题一旦可用即回调（在导航 commit 后立即触发，早于候选链接）.

    Returns:
        ``(found, title)`` 元组：去重后的原始 URL 字符串列表（可能含相对路径，
        交给父进程 urljoin 归一化）+ 页面标题（``page.title()``，可能为空）。

    Raises:
        ImportError: 缺少 playwright.
        Exception: playwright / 浏览器执行失败（含浏览器内核缺失）.
    """
    # 延迟导入：让「模块缺失」与「运行失败」可区分
    from playwright.sync_api import sync_playwright  # type: ignore[import-not-found]

    found: List[str] = []
    seen: set = set()
    title = ""
    # 最近一次「新发现 m3u8」的时间戳（静默窗口判定用），用列表包一层便于闭包改写。
    _last_new: List[float] = [time.time()]

    def _add(raw: str) -> None:
        """加入一条命中（清洗 + 去重），回调里抛异常会被 playwright 吞掉."""
        try:
            cleaned = _is_m3u8_like(raw or "")
            if cleaned:
                cleaned = urljoin(url, cleaned)
        except Exception:
            return
        if cleaned and cleaned not in seen:
            seen.add(cleaned)
            found.append(cleaned)
            _last_new[0] = time.time()
            if on_candidate:
                on_candidate(cleaned)

    content = ""
    with sync_playwright() as p:
        # 精简的启动参数：禁用 GPU / dev-shm / 扩展 / 首次运行提示；
        # 刻意不加 --no-sandbox：用户在 GUI 里粘贴的是不可信网页，沙箱是必要的
        # 安全边界，且 Windows 下以当前用户启动 chromium 本就无需 --no-sandbox。
        browser = p.chromium.launch(
            headless=True,
            args=["--disable-gpu", "--disable-dev-shm-usage", "--disable-extensions", "--no-first-run"],
        )
        try:
            proxy_cfg: dict = {}
            if proxy:
                proxy_cfg["proxy"] = {"server": _normalize_proxy(proxy)}
            page = browser.new_page(**proxy_cfg)

            # 「安全」请求拦截：只拦无助于 m3u8 发现的静态资源与分片，
            # 其余一律放行，避免误杀无扩展名的 m3u8 播放列表。
            try:
                page.route("**/*", _safe_abort)
            except Exception:
                pass

            def _on_response(resp) -> None:
                try:
                    _add(resp.url or "")
                except Exception:
                    pass

            page.on("response", _on_response)
            # 用 commit 而非 domcontentloaded：只要浏览器开始加载页面即着手收集，
            # 更早拿到首屏发起的 m3u8 请求；导航失败（404/超时/无网）也不直接抛
            # 异常，而是带着已收集到的候选返回，尽可能多给结果。
            try:
                page.goto(url, wait_until="commit", timeout=int(timeout) * 1000)
            except Exception as goto_exc:
                _log(f"[deep_worker] 导航未完成，仍返回已收集的候选：{goto_exc}")
            # 标题尽早回传：导航 commit 后立即取一次；若为空，在静默收集循环里
            # 持续重试，一旦拿到非空标题就立即回传，保证标题不晚于候选链接到达。
            # （commit 时 <title> 可能尚未解析完成，page.title() 会短暂返回空。）
            def _try_emit_title() -> None:
                nonlocal title
                if title or not on_title:
                    return
                try:
                    t = page.title() or ""
                except Exception:
                    t = ""
                if t:
                    title = t
                    on_title(t)

            _try_emit_title()
            # 静默窗口收集：首次收集到 m3u8 后，再静默 _SETTLE_MS 毫秒确认没有
            # 新的 m3u8 才收工；同时保证至少收集 _MIN_COLLECT_MS 毫秒，避免页面刚
            # 打开时瞬间的早期请求造成过早停等。wait_ms 作为总预算上限。
            deadline = time.time() + int(wait_ms) / 1000.0
            start = time.time()
            while time.time() < deadline:
                _try_emit_title()
                quiet_ms = (time.time() - _last_new[0]) * 1000
                if (
                    found
                    and quiet_ms >= _SETTLE_MS
                    and (time.time() - start) * 1000 >= _MIN_COLLECT_MS
                ):
                    break
                try:
                    page.wait_for_timeout(_POLL_INTERVAL_MS)
                except Exception:
                    pass
            try:
                content = page.content() or ""
            except Exception:
                content = ""
            _try_emit_title()
            try:
                title = page.title() or title
            except Exception:
                pass
        finally:
            try:
                browser.close()
            except Exception:
                pass

    # 最终 DOM 文本再扫一遍（兼容脚本里拼接、但未走网络请求的情形）
    for match in M3U8_ABS_RE.finditer(content):
        _add(match.group(0))
    for match in M3U8_QUOTED_RE.finditer(content):
        _add(match.group(1))
    return found, title


def _build_parser() -> argparse.ArgumentParser:
    """构造命令行参数解析器."""
    parser = argparse.ArgumentParser(
        prog="deep_worker.py",
        description="深度模式子进程：用 playwright 抓取页面中的 m3u8 链接（JSON 输出）",
    )
    parser.add_argument("--url", required=True, help="待抓取的页面 URL")
    parser.add_argument("--stream", action="store_true", help="逐行输出候选事件，最后输出完整结果")
    parser.add_argument("--timeout", type=int, default=30, help="导航超时秒数（默认 30）")
    parser.add_argument(
        "--wait-ms", type=int, default=5000, help="网络静默后额外等待毫秒数（默认 5000）"
    )
    parser.add_argument(
        "--browsers-path",
        default="",
        help="playwright 浏览器目录（缺省时用环境变量，再缺省用 F:\\gadgets\\playwright-browsers）",
    )
    parser.add_argument(
        "--proxy",
        default="",
        help="手动代理地址（如 127.0.0.1:7897），浏览器走代理访问页面",
    )
    return parser


def main(argv: Optional[List[str]] = None) -> int:
    """进程入口.

    Args:
        argv: 命令行参数（默认取 ``sys.argv[1:]``）.

    Returns:
        进程退出码（0 成功 / 2 缺 playwright / 3 缺浏览器内核 / 4 其他失败）.
    """
    args = _build_parser().parse_args(argv if argv is not None else sys.argv[1:])

    # 显式 UTF-8：中文 Windows 下 stderr 默认按 GBK 编码，父进程按 UTF-8 解码
    # 会产生乱码（如「执行失败」变成「ִʧܣ」）。放在 main 内而非模块级，
    # 确保任何早期失败路径也已生效。
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
        except Exception:
            pass

    # setdefault 语义：命令行参数 > 已有环境变量 > 本机默认目录
    if args.browsers_path:
        os.environ.setdefault("PLAYWRIGHT_BROWSERS_PATH", args.browsers_path)
    os.environ.setdefault("PLAYWRIGHT_BROWSERS_PATH", DEFAULT_BROWSERS_PATH)

    # 代理地址：命令行 --proxy > 环境变量 M3U8_DEEP_PROXY（父进程透传）；
    # 父进程在 no_proxy 时已不会设置该环境变量，故此处无需再判 no_proxy。
    proxy = (args.proxy or os.environ.get("M3U8_DEEP_PROXY", "")).strip()

    try:
        def emit_candidate(raw: str) -> None:
            print(json.dumps({"event": "candidate", "url": raw}, ensure_ascii=False), flush=True)

        def emit_title(t: str) -> None:
            print(json.dumps({"event": "title", "title": t}, ensure_ascii=False), flush=True)

        urls, title = _collect_urls(
            args.url, args.timeout, args.wait_ms, proxy=proxy,
            **({"on_candidate": emit_candidate, "on_title": emit_title} if args.stream else {}),
        )
    except ImportError as exc:
        _log(f"[deep_worker] 缺少 playwright：{exc}")
        _log("[deep_worker] 请在系统 Python 中执行：pip install playwright")
        return EXIT_NO_PLAYWRIGHT
    except Exception as exc:  # noqa: BLE001 - 需要把任意失败转成可读退出码
        message = str(exc)
        if "Executable doesn't exist" in message or "playwright install" in message:
            _log(f"[deep_worker] 缺少浏览器内核：{message}")
            _log("[deep_worker] 请在系统 Python 中执行：playwright install chromium")
            return EXIT_NO_BROWSER
        _log(f"[deep_worker] 执行失败：{exc}")
        return EXIT_RUNTIME_ERROR

    # 默认保持单个 JSON 的旧协议；--stream 时前面可以有逐行候选事件。
    print(json.dumps({"urls": urls, "title": title}, ensure_ascii=False), flush=True)
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
