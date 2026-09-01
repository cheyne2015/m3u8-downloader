# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller 打包配置：将 m3u8-downloader 打包为 CLI 与 GUI 两个 EXE 文件."""

import sys
from PyInstaller.utils.hooks import collect_submodules

crypto_hiddenimports = collect_submodules('Crypto')

block_cipher = None

hiddenimports = [
    'Crypto', 'Crypto.Cipher', 'Crypto.Cipher.AES',
    'Crypto.Util', 'Crypto.Util.Padding', 'Crypto.Util.strxor',
    'tqdm', 'tqdm.auto', 'tqdm.std', 'tqdm.utils',
    'socks',
    'm3u8_downloader', 'm3u8_downloader.parser',
    'm3u8_downloader.downloader', 'm3u8_downloader.merger',
    'm3u8_downloader.utils', 'm3u8_downloader.cli', 'm3u8_downloader.gui',
] + crypto_hiddenimports

# CLI 入口（保留控制台，用于命令行进度输出）
a_cli = Analysis(
    ['m3u8_downloader/__main__.py'],
    pathex=[], binaries=[], datas=[],
    hiddenimports=hiddenimports,
    hookspath=[], hooksconfig={}, runtime_hooks=[],
    excludes=[], win_no_prefer_redirects=False, win_private_assemblies=False,
    cipher=block_cipher, noarchive=False,
)

# GUI 入口（无控制台，双击运行不弹黑窗口）
a_gui = Analysis(
    ['m3u8_downloader/gui_launcher.py'],
    pathex=[], binaries=[], datas=[],
    hiddenimports=hiddenimports,
    hookspath=[], hooksconfig={}, runtime_hooks=[],
    excludes=[], win_no_prefer_redirects=False, win_private_assemblies=False,
    cipher=block_cipher, noarchive=False,
)

pyz_cli = PYZ(a_cli.pure, a_cli.zipped_data, cipher=block_cipher)
pyz_gui = PYZ(a_gui.pure, a_gui.zipped_data, cipher=block_cipher)

exe_cli = EXE(
    pyz_cli, a_cli.scripts, a_cli.binaries, a_cli.zipfiles, a_cli.datas, [],
    name='m3u8-dl-cli', debug=False, bootloader_ignore_signals=False,
    strip=False, upx=True, upx_exclude=[], runtime_tmpdir=None,
    console=True, disable_windowed_traceback=False, argv_emulation=False,
    target_arch=None, codesign_identity=None, entitlements_file=None,
)

# GUI 版固定命名为 m3u8-dl.exe（无控制台，双击运行不弹黑窗口）
exe_gui = EXE(
    pyz_gui, a_gui.scripts, a_gui.binaries, a_gui.zipfiles, a_gui.datas, [],
    name='m3u8-dl', debug=False, bootloader_ignore_signals=False,
    strip=False, upx=True, upx_exclude=[], runtime_tmpdir=None,
    console=False, disable_windowed_traceback=False, argv_emulation=False,
    target_arch=None, codesign_identity=None, entitlements_file=None,
)
