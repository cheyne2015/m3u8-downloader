"""轻量级运行资源统计，不增加第三方依赖。"""

from __future__ import annotations

import ctypes
import os
import time
from dataclasses import dataclass
from ctypes import wintypes
from typing import Callable


@dataclass(frozen=True)
class RuntimeStatistics:
    speed_bps: float
    session_downloaded_bytes: int
    active_downloads: int
    active_extractions: int
    cpu_percent: float
    memory_bytes: int


class _ProcessResourceSampler:
    def __init__(self) -> None:
        self._last_wall = time.monotonic()
        self._last_cpu = None
        self._last_sample_wall = 0.0
        self._cached = (0.0, 0)

    def __call__(self) -> tuple[float, int]:
        now = time.monotonic()
        if now - self._last_sample_wall < 1.0:
            return self._cached
        total_cpu, memory = _windows_process_tree_resources(os.getpid())
        if self._last_cpu is None:
            percent = 0.0
        else:
            elapsed = max(0.001, now - self._last_wall)
            delta = max(0.0, total_cpu - self._last_cpu)
            percent = delta * 100.0 / elapsed / max(1, os.cpu_count() or 1)
        self._last_cpu, self._last_wall = total_cpu, now
        self._last_sample_wall = now
        self._cached = (min(100.0, percent), memory)
        return self._cached


class RuntimeStatisticsTracker:
    def __init__(self, resource_provider: Callable[[], tuple[float, int]] | None = None):
        self._resource_provider = resource_provider or _ProcessResourceSampler()
        self._last_total = None
        self._session_total = 0

    def update(
        self, *, total_downloaded_bytes: int, speed_bps: float,
        active_downloads: int, active_extractions: int,
    ) -> RuntimeStatistics:
        current = max(0, int(total_downloaded_bytes))
        if self._last_total is not None and current > self._last_total:
            self._session_total += current - self._last_total
        self._last_total = current
        cpu, memory = self._resource_provider()
        return RuntimeStatistics(
            speed_bps=max(0.0, float(speed_bps)),
            session_downloaded_bytes=self._session_total,
            active_downloads=max(0, int(active_downloads)),
            active_extractions=max(0, int(active_extractions)),
            cpu_percent=max(0.0, float(cpu)), memory_bytes=max(0, int(memory)),
        )


def _windows_process_tree_resources(root_pid: int) -> tuple[float, int]:
    if os.name != "nt":
        return time.process_time(), 0
    kernel32 = ctypes.windll.kernel32
    psapi = ctypes.windll.psapi
    kernel32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    kernel32.OpenProcess.restype = wintypes.HANDLE

    class PROCESSENTRY32W(ctypes.Structure):
        _fields_ = [
            ("dwSize", wintypes.DWORD), ("cntUsage", wintypes.DWORD),
            ("th32ProcessID", wintypes.DWORD), ("th32DefaultHeapID", ctypes.c_size_t),
            ("th32ModuleID", wintypes.DWORD), ("cntThreads", wintypes.DWORD),
            ("th32ParentProcessID", wintypes.DWORD), ("pcPriClassBase", wintypes.LONG),
            ("dwFlags", wintypes.DWORD), ("szExeFile", wintypes.WCHAR * 260),
        ]

    snapshot = kernel32.CreateToolhelp32Snapshot(0x00000002, 0)
    if snapshot == ctypes.c_void_p(-1).value:
        return time.process_time(), 0
    parents = {}
    entry = PROCESSENTRY32W(); entry.dwSize = ctypes.sizeof(entry)
    try:
        ok = kernel32.Process32FirstW(snapshot, ctypes.byref(entry))
        while ok:
            parents[int(entry.th32ProcessID)] = int(entry.th32ParentProcessID)
            ok = kernel32.Process32NextW(snapshot, ctypes.byref(entry))
    finally:
        kernel32.CloseHandle(snapshot)
    selected = {int(root_pid)}
    changed = True
    while changed:
        changed = False
        for pid, parent in parents.items():
            if parent in selected and pid not in selected:
                selected.add(pid); changed = True

    class MEMORY_COUNTERS(ctypes.Structure):
        _fields_ = [
            ("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD),
            ("PeakWorkingSetSize", ctypes.c_size_t), ("WorkingSetSize", ctypes.c_size_t),
            ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
            ("QuotaPagedPoolUsage", ctypes.c_size_t),
            ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
            ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
            ("PagefileUsage", ctypes.c_size_t), ("PeakPagefileUsage", ctypes.c_size_t),
        ]

    def filetime_value(value) -> int:
        return (int(value.dwHighDateTime) << 32) | int(value.dwLowDateTime)

    total_cpu = 0.0; total_memory = 0
    for pid in selected:
        handle = kernel32.OpenProcess(0x1000 | 0x0400, False, pid)
        if not handle:
            continue
        try:
            counters = MEMORY_COUNTERS(); counters.cb = ctypes.sizeof(counters)
            if psapi.GetProcessMemoryInfo(handle, ctypes.byref(counters), counters.cb):
                total_memory += int(counters.WorkingSetSize)
            created = wintypes.FILETIME(); exited = wintypes.FILETIME()
            kernel = wintypes.FILETIME(); user = wintypes.FILETIME()
            if kernel32.GetProcessTimes(
                handle, ctypes.byref(created), ctypes.byref(exited),
                ctypes.byref(kernel), ctypes.byref(user),
            ):
                total_cpu += (filetime_value(kernel) + filetime_value(user)) / 10_000_000
        finally:
            kernel32.CloseHandle(handle)
    return total_cpu, total_memory
