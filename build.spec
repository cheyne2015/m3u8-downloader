# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller 打包配置文件：将 m3u8-downloader 打包为单个 EXE 文件."""

import sys
from PyInstaller.utils.hooks import collect_submodules

# 收集 pycryptodome 的所有子模块
crypto_hiddenimports = collect_submodules('Crypto')

block_cipher = None

a = Analysis(
    ['m3u8_downloader/__main__.py'],
    pathex=[],
    binaries=[],
    datas=[],
    hiddenimports=[
        # pycodome 相关
        'Crypto',
        'Crypto.Cipher',
        'Crypto.Cipher.AES',
        'Crypto.Util',
        'Crypto.Util.Padding',
        'Crypto.Util.strxor',
        # tqdm 相关
        'tqdm',
        'tqdm.auto',
        'tqdm.std',
        'tqdm.utils',
        # SOCKS 代理支持
        'socks',
        # 项目自身
        'm3u8_downloader',
        'm3u8_downloader.parser',
        'm3u8_downloader.downloader',
        'm3u8_downloader.merger',
        'm3u8_downloader.utils',
        'm3u8_downloader.cli',
        'm3u8_downloader.gui',
    ] + crypto_hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name='m3u8-dl',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
