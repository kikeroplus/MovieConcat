"""起動時の外部ツール（ffmpeg / ffprobe / libmpv-2.dll）検出。"""

from __future__ import annotations

import shutil
from pathlib import Path


def missing_requirements(app_dir: Path) -> list[str]:
    """不足している外部ツールの説明文一覧を返す（空なら全部揃っている）。"""
    missing: list[str] = []
    if shutil.which("ffmpeg") is None:
        missing.append("ffmpeg（PATH に見つかりません）")
    if shutil.which("ffprobe") is None:
        missing.append("ffprobe（PATH に見つかりません）")
    if not (app_dir / "libmpv-2.dll").exists():
        missing.append("libmpv-2.dll（アプリフォルダに見つかりません）")
    return missing


def libmpv_missing(app_dir: Path) -> bool:
    return not (app_dir / "libmpv-2.dll").exists()
