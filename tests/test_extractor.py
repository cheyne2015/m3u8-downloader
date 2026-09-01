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
    _scan_text,
    extract_m3u8_from_page,
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


# ===== 深度模式：playwright 缺失降级 =====
def test_deep_extract_without_playwright_raises():
    fake_pw = types.ModuleType("playwright")
    fake_sync = types.ModuleType("playwright.sync_api")
    # 故意不提供 sync_playwright 属性 -> from ... import 触发 ImportError
    with mock.patch.dict(
        sys.modules,
        {"playwright": fake_pw, "playwright.sync_api": fake_sync},
    ):
        with pytest.raises(DeepModeUnavailableError):
            extractor._deep_extract(PAGE_URL)


def test_is_deep_mode_available_returns_bool():
    assert isinstance(extractor.is_deep_mode_available(), bool)


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
