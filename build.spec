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
    # 静态 HTML 解析（纯 Python，使用内置 html.parser，不引 lxml）
    'bs4', 'soupsieve', 'html.parser',
    'm3u8_downloader', 'm3u8_downloader.parser',
    'm3u8_downloader.downloader', 'm3u8_downloader.merger',
    'm3u8_downloader.utils', 'm3u8_downloader.cli', 'm3u8_downloader.gui',
    'm3u8_downloader.extractor', 'm3u8_downloader.estimator',
] + crypto_hiddenimports

# 深度模式依赖（playwright + Chromium 内核）体积巨大且打包易失败，显式排除，
# 保持双 EXE 绿色小巧；用户需要时用 pip install -r requirements-deep.txt 单独安装。
excludes = ['playwright']

# 深度模式子进程 worker：以「数据文件」随包分发（不参与 import，故不进 hiddenimports）。
# 冻结 EXE 内无法 import 外部 site-packages，深度模式改为调用系统 Python 执行该脚本，
# 复用本机 playwright 与浏览器，体积只增加几 KB。
# 注意：不要把 'm3u8_downloader.deep_worker' 加进 hiddenimports，否则会被编译进 PYZ，
#       导致 sys._MEIPASS/m3u8_downloader/deep_worker.py 文件不存在、子进程路线失效。
deep_worker_datas = [('m3u8_downloader/deep_worker.py', 'm3u8_downloader')]

# CLI 入口（保留控制台，用于命令行进度输出）
a_cli = Analysis(
    ['m3u8_downloader/__main__.py'],
    pathex=[], binaries=[], datas=deep_worker_datas,
    hiddenimports=hiddenimports,
    hookspath=[], hooksconfig={}, runtime_hooks=[],
    excludes=excludes, win_no_prefer_redirects=False, win_private_assemblies=False,
    cipher=block_cipher, noarchive=False,
)

# GUI 入口（无控制台，双击运行不弹黑窗口）
a_gui = Analysis(
    ['m3u8_downloader/gui_launcher.py'],
    pathex=[], binaries=[], datas=deep_worker_datas,
    hiddenimports=hiddenimports,
    hookspath=[], hooksconfig={}, runtime_hooks=[],
    excludes=excludes, win_no_prefer_redirects=False, win_private_assemblies=False,
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
