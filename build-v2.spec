# -*- mode: python ; coding: utf-8 -*-
"""m3u8 下载器 v2 文件夹式候选包。"""

import shutil
from pathlib import Path
from PyInstaller.utils.hooks import collect_submodules

ffmpeg = shutil.which("ffmpeg")
if not ffmpeg:
    raise SystemExit("未找到 ffmpeg.exe，不能构建正式候选包")

hiddenimports = [
    "m3u8_downloader.background_v2",
    "m3u8_downloader.downloader_adapter_v2",
    "m3u8_downloader.extractor_adapter_v2",
    "m3u8_downloader.ffmpeg_v2",
    "m3u8_downloader.gui_v2",
    "m3u8_downloader.runtime_v2",
    "m3u8_downloader.secrets_v2",
    "m3u8_downloader.windows_v2",
    "Crypto", "Crypto.Cipher", "Crypto.Cipher.AES",
    "Crypto.Util", "Crypto.Util.Padding", "Crypto.Util.strxor",
] + collect_submodules("m3u8_downloader.tasking")

a = Analysis(
    ["m3u8_downloader/gui_launcher.py"],
    pathex=[],
    binaries=[(ffmpeg, ".")],
    datas=[
        ("m3u8_downloader/deep_worker.py", "m3u8_downloader"),
        ("m3u8_downloader/assets", "m3u8_downloader/assets"),
    ],
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["playwright", "tkinter"],
    noarchive=False,
)
# Codex 运行环境会把 Poppler 的 ICU 78 放进 PATH。PyInstaller 会误把它当作
# Qt 依赖收进包中，但当前 Qt 使用 Windows 自带 ICU 接口，混用后 QtCore
# 会报 WinError 127。过滤这两个误收集文件，让系统加载兼容版本。
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
    name="m3u8-dl-v2",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    icon="m3u8_downloader/assets/m3u8-downloader.ico",
)
app = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="m3u8-downloader-v2",
)
