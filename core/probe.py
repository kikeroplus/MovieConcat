"""ffprobe 呼び出し・結果キャッシュ・VideoInfo データクラス。

core/ は GUI に依存しない。scan_root は進捗報告・キャンセルのために
`worker` オブジェクト（core/worker.py の WorkerThread 相当）を受け取るが、
PySide6 は import しない（worker が None の場合は同期的に動作する）。
"""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

CREATE_NO_WINDOW = 0x08000000
TARGET_EXTENSIONS = {".mp4", ".m4v", ".wmv"}
CACHE_FILENAME = ".moviemanager_cache.json"
OUTPUT_DIR_NAME = "_output"
UNSORTED_GROUP_NAME = "未分類"
OUTPUT_GROUP_NAME = "出力動画"


class ProbeError(Exception):
    """ffprobe の実行・解析に失敗したときに送出する。"""


@dataclass(frozen=True)
class VideoInfo:
    path: Path
    size: int
    mtime: float
    width: int
    height: int
    video_codec: str
    pix_fmt: str
    fps: float
    profile: str
    audio_codec: Optional[str]
    audio_sample_rate: Optional[int]
    audio_channels: Optional[int]
    duration: float
    creation_time: float
    creation_time_is_fallback: bool

    def to_cache_dict(self) -> dict[str, Any]:
        return {
            "size": self.size,
            "mtime": self.mtime,
            "width": self.width,
            "height": self.height,
            "video_codec": self.video_codec,
            "pix_fmt": self.pix_fmt,
            "fps": self.fps,
            "profile": self.profile,
            "audio_codec": self.audio_codec,
            "audio_sample_rate": self.audio_sample_rate,
            "audio_channels": self.audio_channels,
            "duration": self.duration,
            "creation_time": self.creation_time,
            "creation_time_is_fallback": self.creation_time_is_fallback,
        }

    @classmethod
    def from_cache_dict(cls, path: Path, data: dict[str, Any]) -> "VideoInfo":
        return cls(
            path=path,
            size=data["size"],
            mtime=data["mtime"],
            width=data["width"],
            height=data["height"],
            video_codec=data["video_codec"],
            pix_fmt=data["pix_fmt"],
            fps=data["fps"],
            profile=data["profile"],
            audio_codec=data["audio_codec"],
            audio_sample_rate=data["audio_sample_rate"],
            audio_channels=data["audio_channels"],
            duration=data["duration"],
            creation_time=data["creation_time"],
            creation_time_is_fallback=data["creation_time_is_fallback"],
        )


def _parse_rate(rate: Optional[str]) -> float:
    if not rate or rate == "0/0":
        return 0.0
    if "/" in rate:
        num, _, den = rate.partition("/")
        try:
            denom = float(den)
        except ValueError:
            return 0.0
        if denom == 0:
            return 0.0
        try:
            return float(num) / denom
        except ValueError:
            return 0.0
    try:
        return float(rate)
    except ValueError:
        return 0.0


def _safe_int(value: Any) -> Optional[int]:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _safe_float(value: Any) -> Optional[float]:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _rotation_degrees(video_stream: dict[str, Any]) -> int:
    """rotate タグ（旧式）と side_data_list の displaymatrix（新式）の両方を見る。"""
    tags = video_stream.get("tags") or {}
    if "rotate" in tags:
        degrees = _safe_int(tags["rotate"])
        if degrees is not None:
            return degrees % 360

    for side_data in video_stream.get("side_data_list") or []:
        if "rotation" in side_data:
            degrees = _safe_float(side_data["rotation"])
            if degrees is not None:
                return int(round(degrees)) % 360

    return 0


def _extract_creation_time(
    format_info: dict[str, Any], fallback_mtime: float
) -> tuple[float, bool]:
    tags = format_info.get("tags") or {}
    raw = tags.get("creation_time")
    if raw:
        try:
            dt = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
            return dt.timestamp(), False
        except ValueError:
            pass
    return fallback_mtime, True


def probe_video(path: Path) -> VideoInfo:
    """ffprobe でファイルを解析して VideoInfo を返す。失敗時は ProbeError。"""
    stat = path.stat()

    cmd = [
        "ffprobe",
        "-v",
        "error",
        "-print_format",
        "json",
        "-show_format",
        "-show_streams",
        str(path),
    ]
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            creationflags=CREATE_NO_WINDOW,
        )
    except FileNotFoundError as e:
        raise ProbeError("ffprobe が見つかりません。PATH を確認してください。") from e

    if result.returncode != 0:
        message = result.stderr.decode("utf-8", errors="replace")
        raise ProbeError(f"ffprobe に失敗しました: {path}: {message}")

    try:
        data = json.loads(result.stdout.decode("utf-8", errors="replace"))
    except json.JSONDecodeError as e:
        raise ProbeError(f"ffprobe の出力を解析できませんでした: {path}") from e

    streams = data.get("streams") or []
    video_stream = next((s for s in streams if s.get("codec_type") == "video"), None)
    if video_stream is None:
        raise ProbeError(f"映像ストリームが見つかりません: {path}")
    audio_stream = next((s for s in streams if s.get("codec_type") == "audio"), None)

    width = _safe_int(video_stream.get("width")) or 0
    height = _safe_int(video_stream.get("height")) or 0
    if _rotation_degrees(video_stream) in (90, 270):
        width, height = height, width

    format_info = data.get("format") or {}
    duration = (
        _safe_float(format_info.get("duration"))
        or _safe_float(video_stream.get("duration"))
        or 0.0
    )
    creation_time, creation_time_is_fallback = _extract_creation_time(
        format_info, stat.st_mtime
    )

    return VideoInfo(
        path=path,
        size=stat.st_size,
        mtime=stat.st_mtime,
        width=width,
        height=height,
        video_codec=video_stream.get("codec_name", ""),
        pix_fmt=video_stream.get("pix_fmt", ""),
        fps=_parse_rate(video_stream.get("r_frame_rate")),
        profile=video_stream.get("profile", ""),
        audio_codec=audio_stream.get("codec_name") if audio_stream else None,
        audio_sample_rate=(
            _safe_int(audio_stream.get("sample_rate")) if audio_stream else None
        ),
        audio_channels=(
            _safe_int(audio_stream.get("channels")) if audio_stream else None
        ),
        duration=duration,
        creation_time=creation_time,
        creation_time_is_fallback=creation_time_is_fallback,
    )


class ProbeCache:
    """パス＋サイズ＋更新日時をキーに VideoInfo をキャッシュする。"""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.cache_path = root / CACHE_FILENAME
        self._data: dict[str, dict[str, Any]] = {}
        self._load()

    def _load(self) -> None:
        if not self.cache_path.exists():
            return
        try:
            self._data = json.loads(self.cache_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            self._data = {}

    def _key(self, path: Path) -> str:
        return path.relative_to(self.root).as_posix()

    def get(self, path: Path) -> Optional[VideoInfo]:
        entry = self._data.get(self._key(path))
        if entry is None:
            return None
        try:
            stat = path.stat()
        except OSError:
            return None
        if entry.get("size") != stat.st_size or entry.get("mtime") != stat.st_mtime:
            return None
        try:
            return VideoInfo.from_cache_dict(path, entry)
        except KeyError:
            return None

    def put(self, path: Path, info: VideoInfo) -> None:
        self._data[self._key(path)] = info.to_cache_dict()

    def save(self) -> None:
        tmp_path = self.cache_path.with_name(self.cache_path.name + ".tmp")
        tmp_path.write_text(
            json.dumps(self._data, ensure_ascii=False), encoding="utf-8"
        )
        tmp_path.replace(self.cache_path)


def is_target_file(path: Path) -> bool:
    return path.is_file() and path.suffix.lower() in TARGET_EXTENSIONS


def list_group_folders(root: Path) -> list[Path]:
    """ルート直下のグループフォルダ（_output と . 始まりを除く）を列挙する。"""
    folders = []
    for entry in sorted(root.iterdir(), key=lambda p: p.name.lower()):
        if not entry.is_dir():
            continue
        if entry.name == OUTPUT_DIR_NAME or entry.name.startswith("."):
            continue
        folders.append(entry)
    return folders


def scan_root(
    root: Path, cache: ProbeCache, worker: Any = None
) -> dict[str, list[VideoInfo]]:
    """未分類・各グループフォルダ・出力動画の VideoInfo 一覧を返す。

    scan_range: ルート直下 + グループフォルダ直下のみ（それより深い階層は見ない）。
    worker が渡された場合は is_cancelled()/progress で進捗・キャンセルに対応する
    （worker は PySide6 の WorkerThread に限らず、そのインターフェースを満たす任意のオブジェクトでよい）。
    """
    target_paths: dict[str, list[Path]] = {
        UNSORTED_GROUP_NAME: sorted(
            (p for p in root.iterdir() if is_target_file(p)),
            key=lambda p: p.name.lower(),
        )
    }
    for folder in list_group_folders(root):
        target_paths[folder.name] = sorted(
            (p for p in folder.iterdir() if is_target_file(p)),
            key=lambda p: p.name.lower(),
        )
    output_dir = root / OUTPUT_DIR_NAME
    if output_dir.is_dir():
        target_paths[OUTPUT_GROUP_NAME] = sorted(
            (p for p in output_dir.iterdir() if is_target_file(p)),
            key=lambda p: p.name.lower(),
        )

    total = sum(len(paths) for paths in target_paths.values())
    done = 0
    result: dict[str, list[VideoInfo]] = {}

    for name, paths in target_paths.items():
        infos: list[VideoInfo] = []
        for path in paths:
            if worker is not None and worker.is_cancelled():
                return result
            info = cache.get(path)
            if info is None:
                info = probe_video(path)
                cache.put(path, info)
            infos.append(info)
            done += 1
            if worker is not None:
                worker.progress.emit(done, total, path.name)
        result[name] = infos

    cache.save()
    return result
