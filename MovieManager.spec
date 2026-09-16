# -*- mode: python ; coding: utf-8 -*-
# PyInstaller ビルド定義。
#
# libmpv-2.dll は --add-binary で dist/MovieManager/ 直下（exe と同じ場所）に
# 配置する。core/appdir.py が sys.frozen 時に exe の場所を app_dir として
# 使うため、実行時の探索先と一致する。
#
# ffmpeg / ffprobe は同梱しない（CLAUDE.md の仕様どおり PATH 上にある前提。
# 起動時に core/envcheck.py が存在チェックし、無ければ警告ダイアログを出す）。

from pathlib import Path

block_cipher = None

project_root = Path(SPECPATH)
libmpv_path = project_root / "libmpv-2.dll"

binaries = []
if libmpv_path.exists():
    binaries.append((str(libmpv_path), "."))

a = Analysis(
    ["main.py"],
    pathex=[str(project_root)],
    binaries=binaries,
    datas=[],
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    cipher=block_cipher,
)
pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="MovieManager",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    icon=str(project_root / "icon.ico"),
)
coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    name="MovieManager",
)
