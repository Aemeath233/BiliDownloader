# -*- mode: python ; coding: utf-8 -*-
from pathlib import Path

from PyInstaller.utils.hooks import collect_all


datas = [('logo.ico', '.')]
binaries = []
hiddenimports = []

ffmpeg_bin = Path('vendor') / 'ffmpeg' / 'bin'
if ffmpeg_bin.exists():
    for ffmpeg_file in ffmpeg_bin.iterdir():
        if ffmpeg_file.suffix.lower() in {'.exe', '.dll'}:
            binaries.append((str(ffmpeg_file), 'ffmpeg'))

ffmpeg_license = Path('vendor') / 'ffmpeg' / 'LICENSE.txt'
if ffmpeg_license.exists():
    datas.append((str(ffmpeg_license), 'ffmpeg'))

for package_name in ('customtkinter', 'httpx', 'darkdetect'):
    collected = collect_all(package_name)
    datas += collected[0]
    binaries += collected[1]
    hiddenimports += collected[2]


a = Analysis(
    ['main.py'],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='BiliDownloader',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=['logo.ico'],
)
