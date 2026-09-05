"""Real browser and local HTTP fixtures for incremental extraction."""

import threading
import time
import json
import subprocess
import sys
from pathlib import Path
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from m3u8_downloader.extractor import extract_m3u8_from_page_with_title


@pytest.fixture
def video_page():
    download_started = threading.Event()
    release_download = threading.Event()
    release_download.set()
    segment = b"\x47" + b"\x00" * 187
    estimate_started = threading.Event()
    release_estimate = threading.Event()
    release_estimate.set()
    preload_started = threading.Event()
    release_preload = threading.Event()
    release_preload.set()
    navigation_started = threading.Event()
    release_navigation = threading.Event()
    release_navigation.set()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            hold = self.headers.get("X-Hold-Estimate")
            if hold and (hold != "second" or self.path == "/second.m3u8"):
                estimate_started.set()
                release_estimate.wait(10)
            if self.path == "/?hang":
                navigation_started.set()
                release_navigation.wait(20)
                body = b"<title>Slow navigation</title>"
                kind = "text/html"
            elif self.path == "/?next":
                preload_started.set()
                release_preload.wait(20)
                body = b"<title>Next episode</title><script>fetch('/next.m3u8')</script>"
                kind = "text/html"
            elif self.path == "/":
                body = b'''<title>Streaming test</title><script>
                fetch('/first.m3u8');
                setTimeout(() => fetch('/second.m3u8'), 900);
                </script>'''
                kind = "text/html"
            elif self.path == "/segment.ts":
                download_started.set()
                release_download.wait(20)
                body = segment
                kind = "video/mp2t"
            else:
                body = b"#EXTM3U\n#EXT-X-TARGETDURATION:1\n#EXTINF:1,\nsegment.ts\n#EXT-X-ENDLIST\n"
                kind = "application/vnd.apple.mpegurl"
            self.send_response(200)
            self.send_header("Content-Type", kind)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            try:
                self.wfile.write(body)
            except (BrokenPipeError, ConnectionResetError):
                pass

        def do_HEAD(self):
            self.send_response(200)
            self.send_header("Content-Length", str(len(segment)))
            self.end_headers()

    class QuietServer(ThreadingHTTPServer):
        def handle_error(self, request, client_address):
            pass

    server = QuietServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    class PageURL(str):
        pass

    url = PageURL(f"http://127.0.0.1:{server.server_port}/")
    url.download_started = download_started
    url.release_download = release_download
    url.segment = segment
    url.estimate_started = estimate_started
    url.release_estimate = release_estimate
    url.preload_started = preload_started
    url.release_preload = release_preload
    url.navigation_started = navigation_started
    url.release_navigation = release_navigation
    yield url
    release_download.set()
    release_estimate.set()
    release_preload.set()
    release_navigation.set()
    server.shutdown()
    server.server_close()
    thread.join()


def test_first_candidate_arrives_before_scan_finishes(video_page):
    received = []
    started = time.monotonic()
    candidates, title = extract_m3u8_from_page_with_title(
        video_page, deep=True, estimate=False, no_proxy=True,
        on_candidate=lambda candidate: received.append((candidate.url, time.monotonic())),
    )
    finished = time.monotonic()
    assert [url for url, _ in received] == [
        video_page + "first.m3u8", video_page + "second.m3u8",
    ]
    assert {c.url for c in candidates} == {url for url, _ in received}
    assert title == "Streaming test"
    assert finished - received[0][1] >= 0.5
    print(f"first={received[0][1] - started:.3f}s total={finished - started:.3f}s")


def test_worker_streams_candidates_before_final_json(video_page):
    worker = Path(__file__).resolve().parents[1] / "m3u8_downloader" / "deep_worker.py"
    with subprocess.Popen(
        [sys.executable, str(worker), "--url", video_page, "--stream"],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8",
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    ) as process:
        try:
            first_line = process.stdout.readline()
            assert first_line, process.stderr.read()
            first = json.loads(first_line)
            assert first == {"event": "candidate", "url": video_page + "first.m3u8"}
            assert process.poll() is None
            stdout, stderr = process.communicate(timeout=25)
            assert process.returncode == 0, stderr
            final = json.loads(stdout.splitlines()[-1])
            assert final["urls"] == [video_page + "first.m3u8", video_page + "second.m3u8"]
            assert final["title"] == "Streaming test"
        finally:
            if process.poll() is None:
                process.kill()
                process.wait()


def widgets(parent):
    for child in parent.winfo_children():
        yield child
        yield from widgets(child)


def button(root, label):
    from tkinter import ttk
    return next(w for w in widgets(root) if isinstance(w, ttk.Button) and w.cget("text") == label)


def visible_text(root):
    from tkinter import ttk
    result = []
    for widget in widgets(root):
        if isinstance(widget, ttk.Label):
            variable = widget.cget("textvariable")
            result.append(str(root.getvar(variable)) if variable else str(widget.cget("text")))
    return result


def pump_until(root, predicate, timeout=25):
    deadline = time.monotonic() + timeout
    outcome = []

    def check():
        if predicate() or time.monotonic() >= deadline:
            outcome.append(predicate())
            root.quit()
            return
        root.after(10, check)

    root.after(0, check)
    root.mainloop()
    assert outcome == [True], "GUI did not reach the expected state"


@pytest.fixture(scope="module")
def tk_runtime():
    import tkinter as tk
    root = tk.Tk()
    root.withdraw()
    yield root
    root.destroy()


@pytest.fixture
def desktop_gui(tmp_path, monkeypatch, tk_runtime):
    import tkinter as tk
    from m3u8_downloader import gui, history
    monkeypatch.setattr(gui, "GUI_CONFIG_PATH", tmp_path / "gui.json")
    monkeypatch.setattr(history, "HISTORY_FILE", str(tmp_path / "history.json"))
    root = tk.Toplevel(tk_runtime)
    root.withdraw()
    app = gui.M3U8DownloaderGUI(root)
    yield root, app
    if app._extracting:
        button(root, "停止提取").invoke()
        pump_until(root, lambda: not app._extracting)
    if app._download_thread is not None:
        app._download_thread.join(timeout=3)
    # Release Tk variables on the owning thread, before later browser threads trigger GC.
    for name, value in list(vars(app).items()):
        if isinstance(value, tk.Variable):
            setattr(app, name, None)
    value = None
    for callback in root.tk.call("after", "info"):
        root.after_cancel(callback)
    root.destroy()


def start_deep_scan(root, url, deep=True):
    from tkinter import ttk
    entries = [w for w in widgets(root) if isinstance(w, ttk.Entry)]
    entries[0].delete(0, "end")
    entries[0].insert(0, url)
    deep_check = next(w for w in widgets(root) if isinstance(w, ttk.Checkbutton) and "深度" in w.cget("text"))
    root.setvar(deep_check.cget("variable"), deep)
    button(root, "提取网页").invoke()


def test_gui_shows_first_result_while_scanning_and_preserves_selection(video_page, desktop_gui):
    from tkinter import ttk
    root, _ = desktop_gui
    tree = next(w for w in widgets(root) if isinstance(w, ttk.Treeview))
    start_deep_scan(root, video_page)
    pump_until(root, lambda: bool(tree.get_children()))
    assert str(button(root, "提取网页").cget("state")) == "disabled"
    assert str(button(root, "下载选中").cget("state")) == "normal"
    first = tree.get_children()[0]
    tree.selection_set(first)
    pump_until(root, lambda: str(button(root, "提取网页").cget("state")) == "normal")
    assert len(tree.get_children()) == 2
    assert tree.selection() == (first,)
    assert tree.item(first, "values")[-1] == video_page + "first.m3u8"


def test_result_toolbar_selects_clears_and_copies_links(video_page, desktop_gui):
    from tkinter import ttk
    root, _ = desktop_gui
    tree = next(w for w in widgets(root) if isinstance(w, ttk.Treeview))
    start_deep_scan(root, video_page)
    pump_until(root, lambda: str(button(root, "提取网页").cget("state")) == "normal")
    assert "共 2 条，已选 0 条" in visible_text(root)

    button(root, "全选").invoke()
    root.update()
    assert len(tree.selection()) == 2
    assert "共 2 条，已选 2 条" in visible_text(root)

    button(root, "复制链接").invoke()
    root.update()
    assert root.clipboard_get().splitlines() == [
        video_page + "first.m3u8", video_page + "second.m3u8",
    ]
    button(root, "取消选择").invoke()
    root.update()
    assert tree.selection() == ()
    assert "共 2 条，已选 0 条" in visible_text(root)


def test_stop_scan_keeps_result_and_active_download(video_page, desktop_gui, tmp_path, monkeypatch):
    from tkinter import ttk, messagebox
    root, _ = desktop_gui
    # Only suppress the external modal notification, keeping the real download and merge.
    monkeypatch.setattr(messagebox, "showinfo", lambda *a, **kw: None)
    entries = [w for w in widgets(root) if isinstance(w, ttk.Entry)]
    entries[1].delete(0, "end")
    entries[1].insert(0, str(tmp_path))
    entries[2].delete(0, "end")
    entries[2].insert(0, "stream-test.mp4")
    ffmpeg = next(w for w in widgets(root) if isinstance(w, ttk.Checkbutton) and "ffmpeg" in w.cget("text"))
    root.setvar(ffmpeg.cget("variable"), False)
    tree = next(w for w in widgets(root) if isinstance(w, ttk.Treeview))
    video_page.release_download.clear()
    try:
        start_deep_scan(root, video_page)
        pump_until(root, lambda: bool(tree.get_children()))
        first = tree.get_children()[0]
        tree.selection_set(first)
        button(root, "下载选中").invoke()
        pump_until(root, video_page.download_started.is_set)
        labels = visible_text(root)
        assert "当前标题：stream-test" in labels
        assert "保存文件：stream-test.mp4" in labels
        assert str(button(root, "停止提取").cget("state")) == "normal"
        button(root, "停止提取").invoke()
        pump_until(root, lambda: str(button(root, "提取网页").cget("state")) == "normal", timeout=3)
        assert tree.selection() == (first,)
        assert tree.item(first, "values")[-1] == video_page + "first.m3u8"
        assert str(button(root, "停止下载").cget("state")) == "normal"
    finally:
        video_page.release_download.set()
    pump_until(root, lambda: str(button(root, "停止下载").cget("state")) == "disabled")
    assert (tmp_path / "stream-test.mp4").read_bytes() == video_page.segment


def test_public_extractor_subprocess_retains_candidate_on_stop(video_page, monkeypatch):
    # Simulate the frozen caller lacking sync_api; the standalone worker has real Playwright.
    monkeypatch.setitem(sys.modules, "playwright.sync_api", None)
    stopped = threading.Event()
    received = []

    def accept(candidate):
        received.append(candidate.url)
        stopped.set()

    candidates, _ = extract_m3u8_from_page_with_title(
        video_page, deep=True, no_proxy=True, on_candidate=accept, stop_event=stopped,
    )
    assert received == [video_page + "first.m3u8"]
    assert [c.url for c in candidates] == received


def test_stop_interrupts_navigation_promptly(video_page):
    stopped = threading.Event()
    result = []
    failure = []
    video_page.release_navigation.clear()

    def extract():
        try:
            result.extend(extract_m3u8_from_page_with_title(
                video_page + "?hang", deep=True, no_proxy=True,
                stop_event=stopped, on_candidate=lambda _candidate: None,
            )[0])
        except Exception as exc:
            failure.append(exc)

    thread = threading.Thread(target=extract)
    thread.start()
    try:
        assert video_page.navigation_started.wait(20)
        started = time.monotonic()
        stopped.set()
        thread.join(timeout=2)
        assert not thread.is_alive()
        assert time.monotonic() - started < 2
        assert result == []
        assert failure == []
    finally:
        video_page.release_navigation.set()
        thread.join(timeout=10)


def test_stop_during_slow_estimate_returns_existing_candidates_promptly(video_page):
    import requests
    stopped = threading.Event()
    result = []
    failures = []
    session = requests.Session()
    session.trust_env = False
    session.headers["X-Hold-Estimate"] = "1"
    video_page.release_estimate.clear()

    def extract():
        try:
            result.extend(extract_m3u8_from_page_with_title(
                video_page, session=session, deep=True, no_proxy=True,
                on_candidate=lambda c: None, stop_event=stopped,
            )[0])
        except Exception as exc:
            failures.append(exc)

    thread = threading.Thread(target=extract)
    thread.start()
    try:
        assert video_page.estimate_started.wait(20)
        stopped.set()
        thread.join(timeout=1.5)
        assert not thread.is_alive(), "Stop waited for the slow metadata server"
        assert not failures
        assert {c.url for c in result} == {video_page + "first.m3u8", video_page + "second.m3u8"}
    finally:
        video_page.release_estimate.set()
        thread.join(timeout=15)
        session.close()


def test_fast_metadata_is_reported_without_waiting_for_slow_candidate(video_page):
    import requests
    snapshots = []
    video_page.release_estimate.clear()

    def release_later():
        if video_page.estimate_started.wait(20):
            time.sleep(1)
        video_page.release_estimate.set()

    releaser = threading.Thread(target=release_later, daemon=True)
    releaser.start()
    try:
        with requests.Session() as session:
            session.trust_env = False
            session.headers["X-Hold-Estimate"] = "second"
            extract_m3u8_from_page_with_title(
                video_page, session=session, deep=True, no_proxy=True,
                on_candidate=lambda c: snapshots.append(
                    (c.url, c.estimated_size, c.duration, video_page.release_estimate.is_set())
                ),
            )
        assert (video_page + "first.m3u8", 188, 1.0, False) in snapshots
        assert (video_page + "second.m3u8", 188, 1.0, True) in snapshots
    finally:
        video_page.release_estimate.set()
        releaser.join(timeout=2)


@pytest.mark.parametrize("finish_order", ["preload_first", "download_first"])
@pytest.mark.parametrize("deep", [True, False], ids=["deep", "ordinary"])
def test_preload_swaps_list_and_title_only_after_download(
    video_page, desktop_gui, tmp_path, monkeypatch, finish_order, deep,
):
    from tkinter import ttk, messagebox
    root, _ = desktop_gui
    monkeypatch.setattr(messagebox, "showinfo", lambda *a, **kw: None)
    entries = [w for w in widgets(root) if isinstance(w, ttk.Entry)]
    entries[1].delete(0, "end")
    entries[1].insert(0, str(tmp_path))
    ffmpeg = next(w for w in widgets(root) if isinstance(w, ttk.Checkbutton) and "ffmpeg" in w.cget("text"))
    root.setvar(ffmpeg.cget("variable"), False)
    tree = next(w for w in widgets(root) if isinstance(w, ttk.Treeview))
    start_deep_scan(root, video_page)
    pump_until(root, lambda: str(button(root, "提取网页").cget("state")) == "normal")
    original_rows = [tree.item(item, "values") for item in tree.get_children()]
    selected = tree.get_children()[0]
    tree.selection_set(selected)
    entries[2].delete(0, "end")
    entries[2].insert(0, "current.mp4")
    video_page.release_download.clear()
    if finish_order == "download_first":
        video_page.release_preload.clear()
    try:
        button(root, "下载选中").invoke()
        pump_until(root, video_page.download_started.is_set)
        start_deep_scan(root, video_page + "?next", deep=deep)
        pump_until(root, video_page.preload_started.is_set)
        assert "预载：正在提取下一网页…" in visible_text(root)
        if finish_order == "preload_first":
            pump_until(root, lambda: str(button(root, "提取网页").cget("state")) == "normal")
            assert "预载：成功，找到 1 条，等待当前下载结束" in visible_text(root)
        preserved = (
            [tree.item(item, "values") for item in tree.get_children()] == original_rows
            and tree.selection() == (selected,)
            and entries[2].get() == "current.mp4"
        )
    finally:
        video_page.release_download.set()
    pump_until(root, lambda: str(button(root, "停止下载").cget("state")) == "disabled")
    video_page.release_preload.set()
    pump_until(root, lambda: str(button(root, "提取网页").cget("state")) == "normal")
    assert entries[2].get() == "Next episode", "Preloaded title was lost"
    assert "预载：已载入 1 条结果" in visible_text(root)
    assert [tree.item(item, "values")[-1] for item in tree.get_children()] == [video_page + "next.m3u8"]
    assert preserved, "Preload changed the active download's list, selection or filename"
    assert (tmp_path / "current.mp4").read_bytes() == video_page.segment
