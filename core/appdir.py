"""アプリの実体（exe またはソース）が置かれているディレクトリを解決する。

PyInstaller (onedir) でビルドすると、同梱した DLL 等は `MovieManager.exe` と
同じ階層ではなく `_internal` フォルダに置かれる（PyInstaller 6 系の既定構成）。
このフォルダは `sys._MEIPASS` が指す場所と一致するため、`sys.frozen` が
立っているときはそちらを優先する（onefile 実行時の展開先とも一致する）。
"""

from __future__ import annotations

import sys
from pathlib import Path


def get_app_dir() -> Path:
    if getattr(sys, "frozen", False):
        meipass = getattr(sys, "_MEIPASS", None)
        if meipass:
            return Path(meipass)
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent
