"""深度模式子进程路线单元测试：worker 判定一致性、子进程错误分支、可用性探测.

全部用例零网络、零真实浏览器：子进程用 monkeypatch 拦截，判定逻辑直接比对
``extractor`` 与 ``deep_worker`` 两份实现（冻结 EXE 中 worker 无法 import
extractor，只能走内联副本，故必须用断言锁死两份逻辑不漂移）。
"""

import json
import os
import subprocess
import sys
from unittest import mock

import pytest

from m3u8_downloader import deep_worker, extractor
from m3u8_downloader.extractor import DeepModeUnavailableError

PAGE_URL = "https://www.example.com/play/123"

# 判定样本：含被反斜杠 / 句点 / 叹号污染、相对路径、超长等边界
URL_SAMPLES = [
    "",
    "   ",
    "https://p.com/d/index.m3u8",
    "https://p.com/d/index.m3u8\\",      # 反斜杠污染（JS 字符串转义）
    "https://p.com/e/v.m3u8.",           # 句点污染
    "https://p.com/f/w.m3u8!",           # 叹号污染
    "https://p.com/g/x.m3u8;",           # 分号污染
    "  https://p.com/h/y.m3u8?token=1&x=2  ",  # 首尾空白
    "/hls/480/index.m3u8",               # 相对路径（不该被判定函数拒绝）
    "//cdn.example.com/embed/360/index.m3u8",  # 协议相对
    "https://p.com/no/ext",              # 非 m3u8
    "https://p.com/M3U8_UPPER.M3U8",     # 大小写
    "https://p.com/" + "a" * 600 + ".m3u8",    # 超长
]


@pytest.fixture
def force_subprocess(monkeypatch):
    """强制 ``_deep_extract`` 走子进程路线（禁用进程内 playwright）."""
    monkeypatch.setattr(extractor, "_playwright_importable", lambda: False)
    monkeypatch.setattr(extractor, "_inject_system_playwright", lambda: False)
    monkeypatch.setattr(extractor, "_try_import_sync_playwright", lambda: None)
    monkeypatch.setenv("PLAYWRIGHT_BROWSERS_PATH", r"F:\gadgets\playwright-browsers")


def _completed(returncode: int = 0, stdout: str = "", stderr: str = ""):
    """构造一个 ``subprocess.CompletedProcess``（供 mock 返回）."""
    return subprocess.CompletedProcess(args=[], returncode=returncode,
                                       stdout=stdout, stderr=stderr)


# ===== 1. worker 与 extractor 的判定逻辑一致 =====
def test_worker_reuses_extractor_matchers_when_importable():
    """源码运行时 worker 必须复用 extractor 的实现（单一事实来源）."""
    assert deep_worker.M3U8_ABS_RE is extractor.M3U8_ABS_RE
    assert deep_worker.M3U8_QUOTED_RE is extractor.M3U8_QUOTED_RE
    assert deep_worker._is_m3u8_like is extractor._is_m3u8_like


def test_worker_fallback_regex_patterns_match_extractor():
    """冻结 EXE 里 worker 用内联副本，正则图案必须与 extractor 逐字一致."""
    assert deep_worker._FALLBACK_M3U8_ABS_RE.pattern == extractor.M3U8_ABS_RE.pattern
    assert deep_worker._FALLBACK_M3U8_ABS_RE.flags == extractor.M3U8_ABS_RE.flags
    assert deep_worker._FALLBACK_M3U8_QUOTED_RE.pattern == extractor.M3U8_QUOTED_RE.pattern
    assert deep_worker._FALLBACK_M3U8_QUOTED_RE.flags == extractor.M3U8_QUOTED_RE.flags


@pytest.mark.parametrize("sample", URL_SAMPLES)
def test_worker_fallback_is_m3u8_like_matches_extractor(sample):
    """内联副本与 extractor 的判定结果必须逐样本一致（含污染/超长边界）."""
    assert deep_worker._fallback_is_m3u8_like(sample) == extractor._is_m3u8_like(sample)


def test_is_m3u8_like_strips_pollution_and_rejects_bad():
    """共享判定函数：清洗尾部垃圾、拒绝非 m3u8 与超长串."""
    assert extractor._is_m3u8_like("https://p.com/d/index.m3u8\\") == \
        "https://p.com/d/index.m3u8"
    assert extractor._is_m3u8_like("https://p.com/e/v.m3u8.,;:!") == \
        "https://p.com/e/v.m3u8"
    assert extractor._is_m3u8_like("https://p.com/no/ext") is None
    assert extractor._is_m3u8_like("") is None
    assert extractor._is_m3u8_like("https://p.com/" + "a" * 600 + ".m3u8") is None


def test_normalize_candidate_url_still_uses_shared_matcher():
    """重构后归一化行为不变：污染串也能得到干净的绝对 URL."""
    assert extractor._normalize_candidate_url(
        "https://p.com/d/index.m3u8\\", PAGE_URL
    ) == "https://p.com/d/index.m3u8"
    assert extractor._normalize_candidate_url("/hls/480/index.m3u8", PAGE_URL) == \
        "https://www.example.com/hls/480/index.m3u8"
    assert extractor._normalize_candidate_url("ftp://x/y.m3u8", PAGE_URL) is None


# ===== 2. worker 脚本自身可运行（不依赖 playwright 的参数解析） =====
def test_worker_cli_help_exits_zero():
    """worker 能被系统 Python 正常解析参数（不触发 playwright 导入）."""
    script = os.path.join(
        os.path.dirname(os.path.abspath(extractor.__file__)), "deep_worker.py"
    )
    assert os.path.isfile(script), f"worker 脚本缺失：{script}"
    proc = subprocess.run(
        [sys.executable, script, "--help"],
        capture_output=True, text=True, timeout=60,
    )
    assert proc.returncode == 0, proc.stderr
    assert "--browsers-path" in proc.stdout


# ===== 3. 子进程路线：路径解析 =====
def test_deep_worker_path_source_mode():
    """源码运行：worker 与 extractor 同目录，且文件真实存在."""
    path = extractor._deep_worker_path()
    assert path.endswith("deep_worker.py")
    assert os.path.isfile(path)


def test_deep_worker_path_frozen_mode(monkeypatch, tmp_path):
    """冻结运行：worker 来自 sys._MEIPASS\\m3u8_downloader\\deep_worker.py."""
    pkg_dir = tmp_path / "m3u8_downloader"
    pkg_dir.mkdir()
    worker_file = pkg_dir / "deep_worker.py"
    worker_file.write_text("# frozen worker\n", encoding="utf-8")

    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "_MEIPASS", str(tmp_path), raising=False)
    assert extractor._deep_worker_path() == str(worker_file)
    assert extractor._deep_worker_available() is True


# ===== 4. 子进程路线：错误分支 =====
def test_deep_extract_no_python_raises(force_subprocess, monkeypatch):
    """找不到系统 Python 解释器 → 报错且提示中含 Python."""
    monkeypatch.setattr(extractor, "_find_system_python", lambda: None)
    with pytest.raises(DeepModeUnavailableError) as excinfo:
        extractor._deep_extract(PAGE_URL)
    message = str(excinfo.value)
    assert "Python" in message
    assert "py" in message or "python" in message


def test_deep_extract_missing_worker_raises(force_subprocess, monkeypatch):
    """worker 脚本缺失 → 提示文件损坏，而不是笼统的「需要 playwright」."""
    monkeypatch.setattr(extractor, "_find_system_python", lambda: [r"C:\py\python.exe"])
    monkeypatch.setattr(extractor, "_deep_worker_path", lambda: r"X:\nope\deep_worker.py")
    with pytest.raises(DeepModeUnavailableError) as excinfo:
        extractor._deep_extract(PAGE_URL)
    message = str(excinfo.value)
    assert "deep_worker" in message
    assert "pip install playwright" not in message


def test_deep_extract_subprocess_parses_json_and_normalizes(force_subprocess, monkeypatch):
    """子进程返回 JSON 数组 → 归一化 + 去重 + source=deep."""
    monkeypatch.setattr(extractor, "_find_system_python", lambda: [r"C:\py\python.exe"])
    stdout = json.dumps([
        "https://cdn.example.com/hls/1080/index.m3u8?token=abc",
        "https://cdn.example.com/hls/1080/index.m3u8?token=abc\\",  # 污染副本 → 去重
        "/hls/480/index.m3u8",                                       # 相对 → urljoin
        "https://bad.example.com/no.mp4",                            # 非 m3u8 → 丢弃
        "https://p.com/" + "a" * 600 + ".m3u8",                      # 超长 → 丢弃
    ], ensure_ascii=False)

    with mock.patch(
        "m3u8_downloader.extractor.subprocess.run", return_value=_completed(0, stdout)
    ) as run_mock:
        cands = extractor._deep_extract(PAGE_URL, timeout=10, wait_ms=1000)

    urls = [c.url for c in cands]
    assert urls == [
        "https://cdn.example.com/hls/1080/index.m3u8?token=abc",
        "https://www.example.com/hls/480/index.m3u8",
    ]
    assert all(c.source == "deep" for c in cands)

    # 命令必须传全参数，且带浏览器目录
    cmd = run_mock.call_args[0][0]
    assert "--url" in cmd and PAGE_URL in cmd
    assert "--timeout" in cmd and "10" in cmd
    assert "--wait-ms" in cmd and "1000" in cmd
    assert "--browsers-path" in cmd


def test_deep_extract_subprocess_error_playwright_missing(force_subprocess, monkeypatch):
    """stderr 缺 playwright → 提示 pip install playwright."""
    monkeypatch.setattr(extractor, "_find_system_python", lambda: [r"C:\py\python.exe"])
    stderr = (
        "[deep_worker] 缺少 playwright：No module named 'playwright'\n"
        "[deep_worker] 请在系统 Python 中执行：pip install playwright\n"
    )
    with mock.patch(
        "m3u8_downloader.extractor.subprocess.run",
        return_value=_completed(2, "", stderr),
    ):
        with pytest.raises(DeepModeUnavailableError) as excinfo:
            extractor._deep_extract(PAGE_URL)
    message = str(excinfo.value)
    assert "pip install playwright" in message
    assert "chromium" not in message


def test_deep_extract_subprocess_error_browser_missing(force_subprocess, monkeypatch):
    """stderr 缺浏览器内核 → 提示 playwright install chromium（不能被误判成缺模块）."""
    monkeypatch.setattr(extractor, "_find_system_python", lambda: [r"C:\py\python.exe"])
    stderr = (
        "[deep_worker] 缺少浏览器内核：Executable doesn't exist at "
        "F:\\gadgets\\playwright-browsers\\chromium-1\\chrome.exe\n"
        "Please run: playwright install chromium\n"
    )
    with mock.patch(
        "m3u8_downloader.extractor.subprocess.run",
        return_value=_completed(3, "", stderr),
    ):
        with pytest.raises(DeepModeUnavailableError) as excinfo:
            extractor._deep_extract(PAGE_URL)
    message = str(excinfo.value)
    assert "playwright install chromium" in message
    assert "pip install playwright" not in message


def test_deep_extract_subprocess_error_unknown_keeps_stderr(force_subprocess, monkeypatch):
    """未知失败 → 原样附上 stderr 尾部，不吞掉信息."""
    monkeypatch.setattr(extractor, "_find_system_python", lambda: [r"C:\py\python.exe"])
    stderr = "line1\nline2\n[deep_worker] 执行失败：net::ERR_NAME_NOT_RESOLVED\n"
    with mock.patch(
        "m3u8_downloader.extractor.subprocess.run",
        return_value=_completed(4, "", stderr),
    ):
        with pytest.raises(DeepModeUnavailableError) as excinfo:
            extractor._deep_extract(PAGE_URL)
    message = str(excinfo.value)
    assert "退出码 4" in message
    assert "ERR_NAME_NOT_RESOLVED" in message


def test_deep_extract_subprocess_timeout_raises(force_subprocess, monkeypatch):
    """子进程超时 → 转成 DeepModeUnavailableError，不抛裸异常."""
    monkeypatch.setattr(extractor, "_find_system_python", lambda: [r"C:\py\python.exe"])
    with mock.patch(
        "m3u8_downloader.extractor.subprocess.run",
        side_effect=subprocess.TimeoutExpired(cmd="worker", timeout=90),
    ):
        with pytest.raises(DeepModeUnavailableError) as excinfo:
            extractor._deep_extract(PAGE_URL)
    assert "超时" in str(excinfo.value)


def test_deep_extract_subprocess_bad_json_raises(force_subprocess, monkeypatch):
    """stdout 不是合法 JSON → 明确提示，而非静默返回空列表."""
    monkeypatch.setattr(extractor, "_find_system_python", lambda: [r"C:\py\python.exe"])
    with mock.patch(
        "m3u8_downloader.extractor.subprocess.run",
        return_value=_completed(0, "<html>oops</html>"),
    ):
        with pytest.raises(DeepModeUnavailableError) as excinfo:
            extractor._deep_extract(PAGE_URL)
    assert "无法解析" in str(excinfo.value)


# ===== 5. is_deep_mode_available 可用性探测 =====
def test_is_deep_mode_available_true_with_frozen_worker(monkeypatch, tmp_path):
    """冻结场景：进程内无 playwright，但系统 Python + worker 齐全 → 可用."""
    pkg_dir = tmp_path / "m3u8_downloader"
    pkg_dir.mkdir()
    (pkg_dir / "deep_worker.py").write_text("# frozen worker\n", encoding="utf-8")

    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "_MEIPASS", str(tmp_path), raising=False)
    monkeypatch.setattr(extractor, "_DEEP_AVAILABLE_CACHE", None)
    monkeypatch.setattr(extractor, "_playwright_importable", lambda: False)
    monkeypatch.setattr(extractor, "_inject_system_playwright", lambda: False)
    monkeypatch.setattr(
        extractor, "_find_system_python", lambda: [r"C:\py\python.exe", "-3.13"]
    )
    assert extractor.is_deep_mode_available() is True


def test_is_deep_mode_available_false_when_nothing_available(monkeypatch):
    """两条路线都不可用 → False."""
    monkeypatch.setattr(extractor, "_DEEP_AVAILABLE_CACHE", None)
    monkeypatch.setattr(extractor, "_playwright_importable", lambda: False)
    monkeypatch.setattr(extractor, "_inject_system_playwright", lambda: False)
    monkeypatch.setattr(extractor, "_find_system_python", lambda: None)
    assert extractor.is_deep_mode_available() is False


def test_is_deep_mode_available_uses_cache(monkeypatch):
    """GUI 每次调用都要便宜：命中缓存后不再做任何探测."""
    calls = {"n": 0}

    def _count() -> bool:
        calls["n"] += 1
        return True

    monkeypatch.setattr(extractor, "_DEEP_AVAILABLE_CACHE", True)
    monkeypatch.setattr(extractor, "_playwright_importable", _count)
    assert extractor.is_deep_mode_available() is True
    assert calls["n"] == 0, "缓存命中时不应再探测"
