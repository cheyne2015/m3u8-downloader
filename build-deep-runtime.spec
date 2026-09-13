# -*- mode: python ; coding: utf-8 -*-
"""一次性发布的深度提取组件：独立 worker、Playwright 与 Chromium 无界面内核。"""

import json
import os
from importlib.metadata import version
from pathlib import Path

from PyInstaller.utils.hooks import collect_all


browser_root = Path(os.environ.get("M3U8_PLAYWRIGHT_BROWSERS_PATH", ""))
headless_candidates = sorted(browser_root.glob("chromium_headless_shell-*"))
if not browser_root.is_dir() or not headless_candidates:
    raise SystemExit(
        "请通过 M3U8_PLAYWRIGHT_BROWSERS_PATH 指定包含 chromium_headless_shell-* 的目录"
    )
headless = headless_candidates[-1]
chromium_revision = headless.name.rsplit("-", 1)[-1]

playwright_datas, playwright_binaries, playwright_hiddenimports = collect_all("playwright")

a = Analysis(
    ["m3u8_downloader/deep_worker.py"],
    pathex=[],
    binaries=playwright_binaries,
    datas=playwright_datas,
    hiddenimports=playwright_hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["PySide6", "tkinter"],
    noarchive=False,
)
incompatible_icu = {"icuuc.dll", "icudt78.dll"}
a.binaries = [
    entry for entry in a.binaries
    if Path(entry[0]).name.lower() not in incompatible_icu
]
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="deep-worker",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,
    contents_directory=".",
)
runtime = COLLECT(
    exe,
    a.binaries,
    a.datas,
    Tree(str(headless), prefix=f"browsers/{headless.name}"),
    strip=False,
    upx=False,
    name="deep-runtime",
)

manifest = {
    "runtime_version": "1.0.0",
    "protocol_version": 1,
    "playwright_version": version("playwright"),
    "chromium_revision": chromium_revision,
}
(Path(DISTPATH) / "deep-runtime" / "runtime.json").write_text(
    json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
    encoding="utf-8",
)
