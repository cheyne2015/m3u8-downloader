"""extractor.py 单元测试：HTML/JS 抽取、去重、深度模式降级，全程 mock 零网络."""

import os
import sys
import types
from unittest import mock

import pytest

from m3u8_downloader import extractor
from m3u8_downloader.extractor import (
    Candidate,
    DeepModeUnavailableError,
    NoCandidateFoundError,
    _collect_js_urls,
    _dedupe,
    _extract_from_html,
    _parse_page_title,
    _scan_text,
    extract_m3u8_from_page,
    fetch_page_title,
)

FIXTURE_DIR = os.path.join(os.path.dirname(__file__), "fixtures")


def _load(name: str) -> str:
    with open(os.path.join(FIXTURE_DIR, name), "r", encoding="utf-8") as f:
        return f.read()


PAGE_URL = "https://www.example.com/play/123"
HTML = _load("sample_page.html")
PLAYER_JS = _load("player.js")


# ===== 相对 / 协议相对路径归一化 =====
def test_scan_text_normalizes_relative_and_protocol_relative():
    text = (
        '<a href="/x/a.m3u8">x</a>'
        '<script>var u="//c/v/b.m3u8"</script>'
        '<source src="https://abs.example.com/d/e.m3u8">'
    )
    cands = _scan_text(text, "https://p.com/dir/", "html")
    urls = {c.url for c in cands}
    assert "https://p.com/x/a.m3u8" in urls          # 相对路径
    assert "https://c/v/b.m3u8" in urls              # 协议相对路径继承 scheme
    assert "https://abs.example.com/d/e.m3u8" in urls  # 绝对路径原样


def test_scan_text_rejects_non_http_and_oversized():
    text = 'ftp://x/y.m3u8 "x://bad/a.m3u8"'
    long_url = "https://x.com/" + "a" * 600 + ".m3u8"
    cands = _scan_text(text + " " + long_url, "https://p.com", "html")
    urls = {c.url for c in cands}
    assert not any(u.startswith("ftp://") for u in urls)
    assert not any(len(u) > 512 for u in urls)


def test_scan_text_strips_trailing_backslash_and_junk():
    # JS 源码里常见 "...index.m3u8\\" 或 HTML 里 "index.m3u8." 这类尾部垃圾，
    # 必须剥掉，否则请求非法路径会触发服务端断 TLS（SSLEOFError）。
    text = (
        'var u="https://p.com/d/index.m3u8\\";'   # 反斜杠
        '<a href="https://p.com/e/v.m3u8.">x</a>'  # 尾部句点
        '<a href="https://p.com/f/w.m3u8!">x</a>'  # 尾部叹号
    )
    cands = _scan_text(text, "https://p.com", "html")
    urls = {c.url for c in cands}
    assert "https://p.com/d/index.m3u8" in urls
    assert "https://p.com/e/v.m3u8" in urls
    assert "https://p.com/f/w.m3u8" in urls
    assert not any(u.endswith(("\\", ".", "!", ";", ":", ",")) for u in urls)


# ===== 去重与 source 优先级 =====
def test_dedupe_keeps_higher_trust_source_and_title():
    c1 = Candidate(url="https://x/a.m3u8", source="js")
    c2 = Candidate(url="https://x/a.m3u8", source="html", title="HD")
    out = _dedupe([c1, c2])
    assert len(out) == 1
    assert out[0].source == "html"   # 高可信来源胜出
    assert out[0].title == "HD"      # 标题被补全


def test_dedupe_preserves_order():
    cands = [
        Candidate(url="https://x/b.m3u8"),
        Candidate(url="https://x/a.m3u8"),
        Candidate(url="https://x/b.m3u8"),  # 重复
    ]
    out = _dedupe(cands)
    assert [c.url for c in out] == ["https://x/b.m3u8", "https://x/a.m3u8"]


# ===== JS 黑名单与上限 =====
def test_collect_js_urls_filters_blacklist():
    js = _collect_js_urls(HTML, PAGE_URL, extractor.MAX_JS_FILES)
    assert not any("jquery" in u.lower() for u in js), "jquery 应被黑名单过滤"
    assert any(u.endswith("player.js") for u in js), "player.js 应被保留"


def test_collect_js_urls_respects_limit():
    many = "".join(
        f'<script src="https://cdn.example.com/js/{i}.js"></script>'
        for i in range(15)
    )
    js = _collect_js_urls(many, PAGE_URL, extractor.MAX_JS_FILES)
    assert len(js) <= extractor.MAX_JS_FILES


# ===== 静态抽取命中（bs4 或有/无都应是同一个 URL 集合） =====
def test_extract_from_html_finds_expected_urls():
    cands = _extract_from_html(HTML, PAGE_URL)
    urls = {c.url for c in cands}
    assert "https://cdn.example.com/hls/1080/index.m3u8" in urls
    assert "https://www.example.com/play/hls/480/index.m3u8" in urls  # 相对
    assert "https://cdn.example.com/embed/360/index.m3u8" in urls     # 协议相对
    assert "https://cdn.example.com/hls/audio/index.m3u8" in urls     # 内联 script
    assert not any("jquery" in u.lower() for u in urls)


# ===== 门面：estimate=False 不发网络估算 =====
def test_extract_from_page_no_estimate_skips_estimate_call():
    with mock.patch(
        "m3u8_downloader.extractor._fetch_page", return_value=HTML
    ), mock.patch(
        "m3u8_downloader.extractor._fetch_js", return_value=PLAYER_JS
    ), mock.patch("m3u8_downloader.estimator.estimate_many") as em:
        cands = extract_m3u8_from_page(PAGE_URL, estimate=False, deep=False)
    em.assert_not_called()
    urls = {c.url for c in cands}
    # 页面 + 外链 JS 都应命中
    assert "https://cdn.example.com/hls/1080/index.m3u8" in urls
    # player.js 中 BASE+"144/index.m3u8" 是运行时拼接，静态只能拿到字面量，
    # 按设计以 player.js 自身 URL 为基准解析 -> .../player/144/index.m3u8
    assert "https://cdn.example.com/player/144/index.m3u8" in urls
    assert "https://cdn.example.com/hls/4k/index.m3u8" in urls    # 来自 player.js（绝对）
    assert "https://cdn.example.com/hls/144p/index.m3u8" in urls  # 协议相对
    # estimate=False：未回填，reachable 默认 True，大小未知
    assert all(c.reachable for c in cands)
    assert all(c.estimated_size == 0 for c in cands)


# ===== 无候选抛 NoCandidateFoundError =====
def test_extract_from_page_no_candidate_raises():
    empty_html = "<html><body><p>no m3u8 here</p></body></html>"
    with mock.patch(
        "m3u8_downloader.extractor._fetch_page", return_value=empty_html
    ):
        with pytest.raises(NoCandidateFoundError):
            extract_m3u8_from_page(PAGE_URL, estimate=False, deep=False)


# ===== 深度模式：playwright 缺失 / 不可导入时回退子进程 =====
def test_deep_extract_without_playwright_falls_back_to_subprocess(monkeypatch):
    """playwright 可 import 但 sync_api 导入失败 → 必须回退子进程，不得直接抛错.

    回归背景：GUI 启动时调用 is_deep_mode_available() 会注入系统 site-packages，
    使 _playwright_importable() 此后恒为 True；早期实现据此判定「用户环境损坏」
    并抛「依赖不完整」，导致 GUI 永远走不到子进程（CLI 却正常）。
    """
    fake_pw = types.ModuleType("playwright")
    fake_sync = types.ModuleType("playwright.sync_api")
    # 故意不提供 sync_playwright 属性 -> from ... import 触发 ImportError

    sentinel = {"called": False}

    def _fake_subprocess(url, timeout=30, wait_ms=5000, proxy=None):
        sentinel["called"] = True
        sentinel["url"] = url
        sentinel["proxy"] = proxy
        return []

    monkeypatch.setattr(extractor, "_deep_extract_subprocess", _fake_subprocess)
    with mock.patch.dict(
        sys.modules,
        {"playwright": fake_pw, "playwright.sync_api": fake_sync},
    ):
        extractor._deep_extract(PAGE_URL)

    assert sentinel["called"] is True, "应回退到子进程路线，而不是直接抛错"
    assert sentinel["url"] == PAGE_URL


def test_deep_extract_inprocess_used_when_sync_api_importable(monkeypatch):
    """sync_api 可正常导入 → 走进程内路线（最快路径不被回退逻辑破坏）."""
    called = {"subprocess": False, "inprocess": False}

    def _fake_inprocess(sync_playwright, url, timeout, wait_ms, proxy=None):
        called["inprocess"] = True
        called["proxy"] = proxy
        return []

    monkeypatch.setattr(extractor, "_deep_extract_inprocess", _fake_inprocess)
    monkeypatch.setattr(
        extractor,
        "_deep_extract_subprocess",
        lambda url, timeout=30, wait_ms=5000, proxy=None: (
            called.__setitem__("subprocess", True) or []
        ),
    )
    monkeypatch.setattr(extractor, "_try_import_sync_playwright", lambda: object())

    extractor._deep_extract(PAGE_URL)

    assert called["inprocess"] is True
    assert called["subprocess"] is False


def test_is_deep_mode_available_returns_bool():
    assert isinstance(extractor.is_deep_mode_available(), bool)


# ===== _ensure_playwright_browsers_path =====
def test_ensure_playwright_browsers_path(monkeypatch):
    # 用户未设置时，应被设为默认浏览器目录
    monkeypatch.delenv("PLAYWRIGHT_BROWSERS_PATH", raising=False)
    extractor._ensure_playwright_browsers_path()
    assert os.environ["PLAYWRIGHT_BROWSERS_PATH"] == r"F:\gadgets\playwright-browsers"


def test_ensure_playwright_browsers_path_respects_existing(monkeypatch):
    # 用户已设置时，应尊重原值、不覆盖
    monkeypatch.setenv("PLAYWRIGHT_BROWSERS_PATH", "/custom/path")
    extractor._ensure_playwright_browsers_path()
    assert os.environ["PLAYWRIGHT_BROWSERS_PATH"] == "/custom/path"


# ===== 冻结 EXE 从本机注入 playwright =====
def test_inject_system_playwright_finds_installed(monkeypatch):
    """模拟冻结 EXE（playwright 不在 sys.path）：应从本机系统 Python 注入并可用。

    本机未安装 playwright 时跳过（不视为失败）。
    """
    import sys as _sys
    import shutil as _shutil
    import subprocess as _sp

    py = _shutil.which("py") or _shutil.which("py.exe")
    have_system = False
    if py:
        try:
            out = _sp.run(
                [py, "-3.13", "-c", "import site; print(site.getsitepackages()[0])"],
                capture_output=True, text=True, timeout=15,
            ).stdout.strip()
            have_system = bool(out) and os.path.isdir(os.path.join(out, "playwright"))
        except Exception:
            have_system = False
    if not have_system:
        pytest.skip("本机未安装 system playwright，跳过注入验证")

    monkeypatch.setattr(extractor, "_SYSTEM_PLAYWRIGHT_INJECTED", False)
    saved = list(_sys.path)
    # 模拟冻结：移除所有含 playwright 包的站点目录
    _sys.path[:] = [p for p in _sys.path if not os.path.isdir(os.path.join(p, "playwright"))]
    try:
        assert extractor._playwright_importable() is False
        assert extractor._inject_system_playwright() is True
        assert extractor._playwright_importable() is True
    finally:
        _sys.path[:] = saved


# ===== bs4 缺失时降级仍能抽到 =====
def test_extract_works_without_bs4():
    with mock.patch.object(extractor, "BeautifulSoup", None), mock.patch(
        "m3u8_downloader.extractor._fetch_page", return_value=HTML
    ), mock.patch(
        "m3u8_downloader.extractor._fetch_js", return_value=PLAYER_JS
    ):
        cands = extract_m3u8_from_page(PAGE_URL, estimate=False, deep=False)
    urls = {c.url for c in cands}
    assert "https://cdn.example.com/hls/1080/index.m3u8" in urls
    assert "https://www.example.com/play/hls/480/index.m3u8" in urls
    assert not any("jquery" in u.lower() for u in urls)


# ===== 网页标题解析 (用于自动命名) =====
def test_parse_page_title_from_title_tag():
    html = "<html><head><title>仙界法务部 第55集 (2026) - 动漫 - 在线免费观看</title></head></html>"
    assert _parse_page_title(html) == "仙界法务部 第55集 (2026) - 动漫 - 在线免费观看"


def test_parse_page_title_falls_back_to_og_title():
    html = '<html><head><meta property="og:title" content="OG 标题示例"></head></html>'
    assert _parse_page_title(html) == "OG 标题示例"


def test_parse_page_title_empty_when_absent():
    assert _parse_page_title("<html><body>no title</body></html>") == ""
    assert _parse_page_title("") == ""


def test_fetch_page_title_uses_fetch_page(monkeypatch):
    """fetch_page_title 应复用 _fetch_page 解析标题（零网络 mock）."""
    html = "<title>示例剧集 第3话 - 某站点</title>"
    monkeypatch.setattr(extractor, "_fetch_page", lambda url, session, timeout: html)
    # 同时避免 session 真正创建（已被上面的 mock 短路，但保险起见）
    title = fetch_page_title("https://example.com/x", timeout=5)
    assert title == "示例剧集 第3话 - 某站点"


def test_fetch_page_title_handles_fetch_error(monkeypatch):
    def _boom(url, session, timeout):
        raise RuntimeError("network down")

    monkeypatch.setattr(extractor, "_fetch_page", _boom)
    assert fetch_page_title("https://example.com/x") == ""


# ===== 深度模式代理透传 =====
def test_deep_subprocess_receives_proxy_env(monkeypatch):
    """深度模式子进程路线应把手动代理写入 M3U8_DEEP_PROXY 环境变量."""
    import subprocess as _sp

    captured = {}

    def _fake_run(cmd, **kwargs):
        captured["env"] = kwargs.get("env", {})
        fake = _sp.CompletedProcess(cmd, 0, stdout="[]", stderr="")
        return fake

    monkeypatch.setattr(extractor, "_find_system_python", lambda: ["py", "-3.13"])
    monkeypatch.setattr(extractor, "_deep_worker_path", lambda: "worker.py")
    monkeypatch.setattr(extractor.os.path, "isfile", lambda p: True)
    monkeypatch.setattr(extractor, "_ensure_playwright_browsers_path", lambda: None)
    monkeypatch.setattr(extractor.subprocess, "run", _fake_run)

    extractor._deep_extract_subprocess(PAGE_URL, proxy="127.0.0.1:7897")
    assert captured["env"].get("M3U8_DEEP_PROXY") == "http://127.0.0.1:7897"


def test_deep_mode_proxy_nulled_when_no_proxy(monkeypatch):
    """no_proxy=True 时，深度模式不应拿到代理（直连优先）."""
    called = {"proxy": "UNSET"}

    def _fake_deep(url, timeout=30, wait_ms=5000, proxy=None):
        called["proxy"] = proxy
        return [extractor._new_candidate(url, "deep")]

    monkeypatch.setattr(extractor, "_deep_extract", _fake_deep)
    with mock.patch.object(extractor, "is_deep_mode_available", return_value=True), \
         mock.patch.object(extractor, "_fetch_page", side_effect=Exception("unreachable")):
        extractor.extract_m3u8_from_page(
            PAGE_URL, deep=True, no_proxy=True, proxy="127.0.0.1:7897"
        )
    assert called["proxy"] is None, "no_proxy 时深度模式应拿不到代理"


def test_deep_mode_proxy_forwarded_when_no_proxy_false(monkeypatch):
    """no_proxy=False 且有代理时，深度模式应收到代理."""
    called = {"proxy": "UNSET"}

    def _fake_deep(url, timeout=30, wait_ms=5000, proxy=None):
        called["proxy"] = proxy
        return [extractor._new_candidate(url, "deep")]

    monkeypatch.setattr(extractor, "_deep_extract", _fake_deep)
    with mock.patch.object(extractor, "is_deep_mode_available", return_value=True), \
         mock.patch.object(extractor, "_fetch_page", side_effect=Exception("unreachable")):
        extractor.extract_m3u8_from_page(
            PAGE_URL, deep=True, no_proxy=False, proxy="127.0.0.1:7897"
        )
    assert called["proxy"] == "127.0.0.1:7897"


class TestCandidateDeepMode:
    """Candidate.deep / display_mode 应与 source 保持一致，且不因 _dedupe 改写 source 而脱节."""

    def test_deep_true_when_source_deep(self):
        c = Candidate(url="https://x/a.m3u8", source="deep")
        assert c.deep is True
        assert c.display_mode() == "深度"

    def test_deep_false_for_static_sources(self):
        for src in ("html", "inline_js", "js"):
            c = Candidate(url="https://x/a.m3u8", source=src)
            assert c.deep is False
            assert c.display_mode() == "普通"

    def test_deep_follows_source_after_dedupe_override(self):
        """_dedupe 会把 existing.source 改写为更可信来源（如 "deep"）。
        deep 作为属性应随之变化，而非停留在构造时的 False。"""
        existing = Candidate(url="https://x/a.m3u8", source="html")
        assert existing.deep is False
        # 模拟 _dedupe 在 deep 来源更可信时的 source 覆盖
        existing.source = "deep"
        assert existing.deep is True
        assert existing.display_mode() == "深度"
