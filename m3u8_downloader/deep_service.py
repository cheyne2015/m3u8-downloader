"""常驻深度提取子进程客户端。"""

from __future__ import annotations

import atexit
import json
import queue
import subprocess
import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Callable, Optional, Tuple, List


class DeepServiceError(RuntimeError):
    """常驻深度服务启动或通信失败。"""


@dataclass
class _Request:
    messages: queue.Queue = field(default_factory=queue.Queue)


class DeepWorkerService:
    """线程安全地复用一个 deep-worker 进程和其中的 Chromium。"""

    def __init__(self, idle_timeout: float = 60.0) -> None:
        self._lock = threading.RLock()
        self._write_lock = threading.Lock()
        self._process = None
        self._signature = None
        self._requests: dict[str, _Request] = {}
        self._ready = threading.Event()
        self._ready_ok = False
        self._stderr = ""
        self._reader = None
        self._error_reader = None
        self._starts = 0
        self._leases = 0
        self._idle_timeout = max(0.0, float(idle_timeout))
        self._idle_timer = None
        atexit.register(self.shutdown)

    def _start(self, cmd: List[str], env: dict) -> None:
        signature = (tuple(cmd), env.get("PLAYWRIGHT_BROWSERS_PATH", ""))
        with self._lock:
            if self._idle_timer is not None:
                self._idle_timer.cancel()
                self._idle_timer = None
            if (
                self._process is not None
                and self._process.poll() is None
                and self._signature == signature
            ):
                return
            if self._process is not None and self._process.poll() is None:
                raise DeepServiceError("深度提取组件配置已变化，请等待当前提取完成后重试")
            self._ready.clear()
            self._ready_ok = False
            self._stderr = ""
            try:
                self._process = subprocess.Popen(
                    cmd,
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    env=env,
                    bufsize=1,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                )
            except OSError as exc:
                raise DeepServiceError(f"无法启动常驻深度提取组件：{exc}") from exc
            self._signature = signature
            self._starts += 1
            proc = self._process
            self._reader = threading.Thread(target=self._read_stdout, args=(proc,), daemon=True)
            self._error_reader = threading.Thread(target=self._read_stderr, args=(proc,), daemon=True)
            self._reader.start()
            self._error_reader.start()
        if not self._ready.wait(15):
            detail = self._stderr.strip()[-2000:]
            self.shutdown()
            raise DeepServiceError(detail or "常驻深度提取组件启动超时")
        if not self._ready_ok:
            detail = self._stderr.strip()[-2000:]
            self.shutdown()
            raise DeepServiceError(detail or "常驻深度提取组件未能完成启动")

    def _read_stdout(self, proc) -> None:
        try:
            for line in iter(proc.stdout.readline, ""):
                try:
                    payload = json.loads(line)
                except (ValueError, TypeError):
                    continue
                if payload.get("event") == "ready":
                    self._ready_ok = True
                    self._ready.set()
                    continue
                request_id = str(payload.get("request_id") or "")
                with self._lock:
                    request = self._requests.get(request_id)
                if request is not None:
                    request.messages.put(payload)
        finally:
            with self._lock:
                is_current = self._process is proc
                requests = list(self._requests.values()) if is_current else []
            if is_current:
                self._ready.set()
            for request in requests:
                request.messages.put({"event": "service_stopped"})

    def _read_stderr(self, proc) -> None:
        for line in iter(proc.stderr.readline, ""):
            with self._lock:
                self._stderr = (self._stderr + line)[-8000:]

    def _send(self, payload: dict) -> None:
        line = json.dumps(payload, ensure_ascii=False) + "\n"
        with self._write_lock:
            proc = self._process
            if proc is None or proc.poll() is not None or proc.stdin is None:
                raise DeepServiceError("常驻深度提取组件已经退出")
            try:
                proc.stdin.write(line)
                proc.stdin.flush()
            except (OSError, ValueError) as exc:
                raise DeepServiceError("无法向常驻深度提取组件发送任务") from exc

    def extract(
        self, cmd: List[str], env: dict, *, url: str, timeout: int, wait_ms: int,
        proxy: str = "", stop_event: Optional[threading.Event] = None,
        on_candidate: Optional[Callable[[str], None]] = None,
        on_title: Optional[Callable[[str], None]] = None,
    ) -> Tuple[List[str], str]:
        with self._lock:
            self._leases += 1
            if self._idle_timer is not None:
                self._idle_timer.cancel()
                self._idle_timer = None
        try:
            self._start(cmd, env)
        except Exception:
            self._release_lease()
            raise
        request_id = uuid.uuid4().hex
        request = _Request()
        with self._lock:
            self._requests[request_id] = request
        found: dict[str, None] = {}
        title = ""
        deadline = time.monotonic() + int(timeout) + int(wait_ms) / 1000 + 60
        cancel_sent = False
        try:
            self._send({
                "command": "extract", "request_id": request_id, "url": url,
                "timeout": int(timeout), "wait_ms": int(wait_ms), "proxy": proxy,
            })
            while True:
                if stop_event is not None and stop_event.is_set():
                    if not cancel_sent:
                        try:
                            self._send({"command": "cancel", "request_id": request_id})
                        except DeepServiceError:
                            pass
                        cancel_sent = True
                    return list(found), title
                if time.monotonic() >= deadline:
                    try:
                        self._send({"command": "cancel", "request_id": request_id})
                    except DeepServiceError:
                        pass
                    raise DeepServiceError("深度模式执行超时")
                try:
                    payload = request.messages.get(timeout=0.1)
                except queue.Empty:
                    continue
                event = payload.get("event")
                if event == "candidate":
                    value = str(payload.get("url") or "")
                    if value and value not in found:
                        found[value] = None
                        if on_candidate:
                            on_candidate(value)
                elif event == "title":
                    title = str(payload.get("title") or "")
                    if title and on_title:
                        on_title(title)
                elif event == "result":
                    for value in payload.get("urls") or []:
                        value = str(value)
                        if value and value not in found:
                            found[value] = None
                            if on_candidate:
                                on_candidate(value)
                    title = str(payload.get("title") or title)
                    return list(found), title
                elif event == "cancelled":
                    return list(found), title
                elif event == "error":
                    raise DeepServiceError(str(payload.get("message") or "深度提取失败"))
                elif event == "service_stopped":
                    detail = self._stderr.strip()[-2000:]
                    raise DeepServiceError(detail or "常驻深度提取组件意外退出")
        finally:
            with self._lock:
                self._requests.pop(request_id, None)
            self._release_lease()

    def _release_lease(self) -> None:
        with self._lock:
            self._leases = max(0, self._leases - 1)
            if not self._leases and not self._requests and self._idle_timeout:
                self._idle_timer = threading.Timer(
                    self._idle_timeout, self._shutdown_if_idle
                )
                self._idle_timer.daemon = True
                self._idle_timer.start()

    def _shutdown_if_idle(self) -> None:
        with self._lock:
            if self._leases or self._requests:
                return
            self._idle_timer = None
            self.shutdown()

    def shutdown(self) -> None:
        with self._lock:
            if self._idle_timer is not None:
                self._idle_timer.cancel()
                self._idle_timer = None
            proc = self._process
            self._process = None
            self._signature = None
        if proc is None:
            return
        if proc.poll() is None:
            try:
                if proc.stdin is not None:
                    proc.stdin.write(json.dumps({"command": "shutdown"}) + "\n")
                    proc.stdin.flush()
                proc.wait(timeout=5)
            except Exception:
                try:
                    proc.terminate()
                    proc.wait(timeout=3)
                except Exception:
                    try:
                        proc.kill()
                    except Exception:
                        pass

    def status(self) -> dict:
        with self._lock:
            return {
                "running": self._process is not None and self._process.poll() is None,
                "process_starts": self._starts,
                "active_requests": len(self._requests),
            }


service = DeepWorkerService()
