"""判断并归一化指向单个 YouTube 作品的地址。"""

import re
import json
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from urllib.parse import parse_qs, urlsplit


def canonical_video_url(url: str) -> str | None:
    parsed = urlsplit(url)
    if parsed.scheme not in {"http", "https"}:
        return None
    parts = parsed.path.strip("/").split("/")
    if parsed.hostname == "youtu.be" and len(parts) == 1:
        video_id = parts[0]
    elif parsed.hostname in {"youtube.com", "www.youtube.com", "m.youtube.com"}:
        if parts == ["watch"]:
            values = parse_qs(parsed.query).get("v", [])
            video_id = values[0] if len(values) == 1 else ""
        elif len(parts) == 2 and parts[0] in {"shorts", "live", "embed"}:
            video_id = parts[1]
        else:
            return None
    else:
        return None
    if re.fullmatch(r"[A-Za-z0-9_-]{11}", video_id):
        return f"https://www.youtube.com/watch?v={video_id}"
    return None


def run_youtube(url, arguments, *, stop_event, timeout=None, proxy=""):
    from .downloader import DownloadCancelled

    address = canonical_video_url(url)
    if address is None:
        raise ValueError("请选择单个 YouTube 视频")
    bundled = Path(sys.executable).parent / "yt-dlp.exe"
    executable = str(bundled) if bundled.is_file() else shutil.which("yt-dlp")
    if not executable:
        raise RuntimeError("缺少 YouTube 下载组件 yt-dlp")
    command = [executable, "--ignore-config", "--no-playlist", "--no-progress",
               "--match-filter", "!is_live"]
    if node := shutil.which("node"):
        command.extend(["--js-runtimes", f"node:{node}"])
    if proxy:
        command.extend(["--proxy", proxy])
    if stop_event.is_set():
        raise DownloadCancelled("用户停止")
    with tempfile.TemporaryFile() as stdout, tempfile.TemporaryFile() as stderr:
        with subprocess.Popen(
            [*command, *arguments, "--", address], stdout=stdout, stderr=stderr,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        ) as process:
            start = time.monotonic()
            try:
                while process.poll() is None:
                    if stop_event.wait(0.2):
                        raise DownloadCancelled("用户停止")
                    if timeout is not None and time.monotonic() - start >= timeout:
                        raise TimeoutError("YouTube 提取超时")
                if process.returncode:
                    raise RuntimeError("YouTube 组件未能处理此视频")
                stdout.seek(0)
                return stdout.read().decode("utf-8", errors="replace")
            finally:
                if process.poll() is None:
                    process.terminate()
                    try:
                        process.wait(timeout=3)
                    except subprocess.TimeoutExpired:
                        process.kill()


def inspect_video(url, *, stop_event, timeout, proxy=""):
    data = run_youtube(url, ["--dump-single-json", "--skip-download"],
                       stop_event=stop_event, timeout=timeout, proxy=proxy)
    return json.loads(data)
