# -*- mode: python ; coding: utf-8 -*-
import sys
from pathlib import Path

root = Path(SPECPATH)
sys.path.insert(0, str(root / 'scripts'))
from build_support import app_data, deduplicate_ffmpeg

a = Analysis(
    [str(root / 'main.py')], pathex=[str(root)], binaries=[],
    datas=app_data(root, require_ffmpeg=True), hiddenimports=[],
    hookspath=[], hooksconfig={}, runtime_hooks=[], excludes=[],
    noarchive=False, optimize=0,
)
a.binaries = deduplicate_ffmpeg(a.binaries, root)
pyz = PYZ(a.pure)

exe = EXE(
    pyz, a.scripts, a.binaries, a.datas, [],
    name='BiliDownloader-Portable', debug=False,
    bootloader_ignore_signals=False, strip=False, upx=False, console=False,
    disable_windowed_traceback=False, argv_emulation=False, target_arch=None,
    codesign_identity=None, entitlements_file=None, icon=[str(root / 'logo.ico')],
)
