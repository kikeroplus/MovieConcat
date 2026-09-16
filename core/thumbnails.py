"""動画のサムネイル生成・キャッシュ（リレー再生のサムネイル一覧用）。

`%LOCALAPPDATA%\\MovieManager\\thumbnails\\` にパス＋サイズ＋更新日時から
決まるファイル名でキャッシュする（core/probe.py のキャッシュと同じ考え方）。
"""

from __future__ import annotations

import hashlib
import subprocess
from pathlib import Path
from typing import Optional

from core.settings import app_data_dir

CREATE_NO_WINDOW = 0x08000000
THUMB_HEIGHT = 90
THUMB_DIR_NAME = "thumbnails"


def _cache_dir() -> Path:
    directory = app_data_dir() / THUMB_DIR_NAME
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def _cache_key(path: Path, size: int, mtime: float) -> str:
    raw = f"{path}|{size}|{mtime}".encode("utf-8")
    return hashlib.sha1(raw).hexdigest()


def _cache_path_for_stat(path: Path, size: int, mtime: float) -> Path:
    return _cache_dir() / f"{_cache_key(path, size, mtime)}.jpg"


def cached_thumbnail_path(path: Path) -> Optional[Path]:
    """既にキャッシュ済みのサムネイルがあればそのパスを返す（無ければ None）。"""
    try:
        stat = path.stat()
    except OSError:
        return None
    cached = _cache_path_for_stat(path, stat.st_size, stat.st_mtime)
    return cached if cached.exists() else None


def generate_thumbnail(path: Path, duration: float) -> Optional[Path]:
    """動画からサムネイルを生成してキャッシュし、そのパスを返す（失敗したら None）。

    先頭付近だと単色コマになりがちなため、動画の 10% 地点付近から抜き出す。
    """
    try:
        stat = path.stat()
    except OSError:
        return None

    output = _cache_path_for_stat(path, stat.st_size, stat.st_mtime)
    if output.exists():
        return output

    seek = 0.0
    if duration and duration > 0.2:
        seek = min(duration * 0.1, duration - 0.1)

    cmd = [
        "ffmpeg", "-y",
        "-ss", f"{seek:.2f}",
        "-i", str(path),
        "-frames:v", "1",
        "-vf", f"scale=-2:{THUMB_HEIGHT}",
        str(output),
    ]
    try:
        result = subprocess.run(
            cmd, capture_output=True, creationflags=CREATE_NO_WINDOW
        )
    except FileNotFoundError:
        return None

    if result.returncode != 0 or not output.exists():
        return None
    return output
