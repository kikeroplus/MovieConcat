"""動画の結合（互換性判定・concat demuxer・音声正規化・再エンコード）。

対象は各グループフォルダ内の除外中でない動画（1本以下のグループはスキップ）。
グループ内ファイルの互換性を比較し、極力再エンコードしない方式（A/B/C）を選ぶ。
判定の詳細は CLAUDE.md 10 章参照。
"""

from __future__ import annotations

import re
import shutil
import subprocess
import tempfile
from collections import Counter
from dataclasses import dataclass, field
from datetime import date
from enum import Enum
from pathlib import Path
from typing import Callable, Optional

from core.probe import OUTPUT_DIR_NAME, OUTPUT_GROUP_NAME, UNSORTED_GROUP_NAME, VideoInfo
from core.state import StateFile

CREATE_NO_WINDOW = 0x08000000
TEMP_DIR_NAME = "MovieManager"

DEFAULT_ENCODER = "libx264"
DEFAULT_CRF = 20
NORMALIZED_AUDIO_CODEC = "aac"
NORMALIZED_AUDIO_BITRATE = "192k"
NORMALIZED_AUDIO_RATE = 48000
NORMALIZED_AUDIO_CHANNELS = 2

_GROUP_NAME_RE = re.compile(r"^(\d+)x(\d+)(?:_wmv)?$")


class ConcatError(Exception):
    pass


class SortMode(Enum):
    NAME = "name"
    CREATED_AT = "created_at"
    MANUAL = "manual"


class ConcatDecision(Enum):
    IDENTICAL = "A"
    AUDIO_MISMATCH = "B"
    VIDEO_MISMATCH = "C"

    @property
    def label(self) -> str:
        return {
            ConcatDecision.IDENTICAL: "A: 完全一致（無劣化結合）",
            ConcatDecision.AUDIO_MISMATCH: "B: 音声のみ不一致（音声正規化して結合）",
            ConcatDecision.VIDEO_MISMATCH: "C: 映像不一致（再エンコードして結合）",
        }[self]


@dataclass
class ConcatJob:
    group_name: str
    videos: list[VideoInfo]
    output_path: Path
    decision: ConcatDecision

    @property
    def total_duration(self) -> float:
        return sum(v.duration for v in self.videos)


@dataclass
class ConcatPlan:
    jobs: list[ConcatJob] = field(default_factory=list)
    skipped: list[tuple[str, str]] = field(default_factory=list)


# =====================================================================
# 結合順
# =====================================================================


def _natural_sort_key(name: str) -> list[object]:
    parts = re.split(r"(\d+)", name)
    return [int(p) if p.isdigit() else p.lower() for p in parts]


def order_videos(
    videos: list[VideoInfo], mode: SortMode, manual_order: Optional[list[str]] = None
) -> list[VideoInfo]:
    if mode == SortMode.NAME:
        return sorted(videos, key=lambda v: _natural_sort_key(v.path.name))
    if mode == SortMode.CREATED_AT:
        return sorted(videos, key=lambda v: v.creation_time)
    if mode == SortMode.MANUAL:
        registered = manual_order or []
        index = {name: i for i, name in enumerate(registered)}
        known = sorted(
            (v for v in videos if v.path.name in index),
            key=lambda v: index[v.path.name],
        )
        unknown = sorted(
            (v for v in videos if v.path.name not in index),
            key=lambda v: _natural_sort_key(v.path.name),
        )
        return known + unknown
    raise ValueError(f"unknown sort mode: {mode}")


# =====================================================================
# 互換性判定
# =====================================================================


def _video_key(info: VideoInfo) -> tuple:
    return (info.video_codec, info.width, info.height, info.pix_fmt, round(info.fps, 3), info.profile)


def _audio_key(info: VideoInfo) -> tuple:
    return (info.audio_codec, info.audio_sample_rate, info.audio_channels)


def check_compatibility(videos: list[VideoInfo]) -> ConcatDecision:
    first = videos[0]
    if any(_video_key(v) != _video_key(first) for v in videos[1:]):
        return ConcatDecision.VIDEO_MISMATCH
    if any(_audio_key(v) != _audio_key(first) for v in videos[1:]):
        return ConcatDecision.AUDIO_MISMATCH
    return ConcatDecision.IDENTICAL


def parse_group_resolution(group_name: str) -> Optional[tuple[int, int]]:
    m = _GROUP_NAME_RE.match(group_name)
    if not m:
        return None
    return int(m.group(1)), int(m.group(2))


def _most_common_resolution(videos: list[VideoInfo]) -> tuple[int, int]:
    counter = Counter((v.width, v.height) for v in videos)
    return counter.most_common(1)[0][0]


def _most_common_fps(videos: list[VideoInfo]) -> float:
    counter = Counter(round(v.fps, 3) for v in videos)
    return counter.most_common(1)[0][0]


# =====================================================================
# 計画の作成
# =====================================================================


def _build_job(
    root: Path,
    group_name: str,
    videos: list[VideoInfo],
    is_excluded: Callable[[Path], bool],
    sort_mode: SortMode,
    state: StateFile,
    run_date: date,
) -> Optional[ConcatJob]:
    included = [v for v in videos if not is_excluded(v.path)]
    if len(included) <= 1:
        return None

    manual = state.manual_order.get(group_name)
    ordered = order_videos(included, sort_mode, manual)

    is_wmv_group = all(v.path.suffix.lower() == ".wmv" for v in ordered)
    ext = ".wmv" if is_wmv_group else ".mp4"
    output_path = root / OUTPUT_DIR_NAME / f"{group_name}_{run_date:%Y%m%d}{ext}"

    decision = check_compatibility(ordered)
    return ConcatJob(
        group_name=group_name, videos=ordered, output_path=output_path, decision=decision
    )


def build_plan(
    root: Path,
    groups: dict[str, list[VideoInfo]],
    is_excluded: Callable[[Path], bool],
    sort_mode: SortMode,
    state: StateFile,
    run_date: Optional[date] = None,
) -> ConcatPlan:
    """グループフォルダごとに 1 本ずつ結合する（従来どおりの一括処理）。"""
    run_date = run_date or date.today()
    plan = ConcatPlan()

    for group_name, videos in groups.items():
        if group_name in (UNSORTED_GROUP_NAME, OUTPUT_GROUP_NAME):
            continue

        job = _build_job(root, group_name, videos, is_excluded, sort_mode, state, run_date)
        if job is None:
            plan.skipped.append((group_name, "対象が1本以下のためスキップ"))
        else:
            plan.jobs.append(job)

    return plan


def build_plan_for_checked(
    root: Path,
    group_name: str,
    videos: list[VideoInfo],
    is_excluded: Callable[[Path], bool],
    sort_mode: SortMode,
    state: StateFile,
    run_date: Optional[date] = None,
) -> ConcatPlan:
    """チェックされた動画だけを対象に 1 本だけ結合する。

    group_name は出力ファイル名に使う（通常は現在表示中のグループ名）。
    """
    run_date = run_date or date.today()
    plan = ConcatPlan()

    job = _build_job(root, group_name, videos, is_excluded, sort_mode, state, run_date)
    if job is None:
        plan.skipped.append((group_name, "対象が1本以下のためスキップ"))
    else:
        plan.jobs.append(job)

    return plan


# =====================================================================
# ffmpeg 実行
# =====================================================================


def _concat_list_line(path: Path) -> str:
    escaped = str(path.resolve()).replace("'", "'\\''")
    return f"file '{escaped}'"


def _write_concat_list(list_path: Path, files: list[Path]) -> None:
    list_path.write_text(
        "\n".join(_concat_list_line(f) for f in files), encoding="utf-8"
    )


def _parse_ffmpeg_time(value: str) -> Optional[float]:
    try:
        h, m, s = value.split(":")
        return int(h) * 3600 + int(m) * 60 + float(s)
    except (ValueError, AttributeError):
        return None


def _run_ffmpeg_with_progress(
    cmd: list[str],
    total_duration: float,
    on_progress: Callable[[float], None],
    is_cancelled: Callable[[], bool],
) -> bool:
    """ffmpeg を実行し、-progress pipe:1 の出力から on_progress(0.0〜1.0) を呼ぶ。
    キャンセルされたらプロセスを終了して False を返す。
    """
    process = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        creationflags=CREATE_NO_WINDOW,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    try:
        assert process.stdout is not None
        for line in process.stdout:
            if is_cancelled():
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                return False
            line = line.strip()
            if line.startswith("out_time="):
                seconds = _parse_ffmpeg_time(line.split("=", 1)[1])
                if seconds is not None and total_duration > 0:
                    on_progress(min(seconds / total_duration, 1.0))
            elif line == "progress=end":
                on_progress(1.0)
        process.wait()
    finally:
        if process.poll() is None:
            process.terminate()
    return process.returncode == 0


def _concat_copy_cmd(list_path: Path, output_path: Path) -> list[str]:
    return [
        "ffmpeg", "-y",
        "-f", "concat", "-safe", "0",
        "-i", str(list_path),
        "-c", "copy",
        "-progress", "pipe:1", "-nostats",
        str(output_path),
    ]


def _normalize_audio_cmd(src: Path, dst: Path, has_audio: bool) -> list[str]:
    cmd = ["ffmpeg", "-y", "-i", str(src)]
    if not has_audio:
        cmd += ["-f", "lavfi", "-i", "anullsrc=channel_layout=stereo:sample_rate=48000"]
    cmd += ["-map", "0:v:0", "-map", "0:a:0" if has_audio else "1:a:0"]
    if not has_audio:
        cmd += ["-shortest"]
    cmd += [
        "-c:v", "copy",
        "-c:a", NORMALIZED_AUDIO_CODEC,
        "-b:a", NORMALIZED_AUDIO_BITRATE,
        "-ar", str(NORMALIZED_AUDIO_RATE),
        "-ac", str(NORMALIZED_AUDIO_CHANNELS),
        "-progress", "pipe:1", "-nostats",
        str(dst),
    ]
    return cmd


def _reencode_cmd(
    src: Path,
    dst: Path,
    width: int,
    height: int,
    fps: float,
    has_audio: bool,
    encoder: str,
    crf: int,
) -> list[str]:
    vf = (
        f"scale={width}:{height}:force_original_aspect_ratio=decrease,"
        f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2,setsar=1,fps={fps}"
    )
    cmd = ["ffmpeg", "-y", "-i", str(src)]
    if not has_audio:
        cmd += ["-f", "lavfi", "-i", "anullsrc=channel_layout=stereo:sample_rate=48000"]
    cmd += ["-map", "0:v:0", "-map", "0:a:0" if has_audio else "1:a:0"]
    if not has_audio:
        cmd += ["-shortest"]
    cmd += ["-vf", vf, "-pix_fmt", "yuv420p"]
    if encoder == "nvenc":
        cmd += ["-c:v", "h264_nvenc", "-cq", str(crf)]
    else:
        cmd += ["-c:v", "libx264", "-crf", str(crf)]
    cmd += [
        "-c:a", NORMALIZED_AUDIO_CODEC,
        "-b:a", NORMALIZED_AUDIO_BITRATE,
        "-ar", str(NORMALIZED_AUDIO_RATE),
        "-ac", str(NORMALIZED_AUDIO_CHANNELS),
        "-progress", "pipe:1", "-nostats",
        str(dst),
    ]
    return cmd


# =====================================================================
# 実行
# =====================================================================


class _ProgressTracker:
    def __init__(self, total_seconds: float, emit: Callable[[int, int, str], None]) -> None:
        self.total = max(total_seconds, 0.001)
        self.done_base = 0.0
        self._emit = emit

    def step_callback(self, step_seconds: float, message: str) -> Callable[[float], None]:
        def _cb(fraction: float) -> None:
            current = self.done_base + fraction * step_seconds
            self._emit(int(current), int(self.total), message)

        return _cb

    def advance(self, step_seconds: float) -> None:
        self.done_base += step_seconds


def compute_total_work_seconds(plan: ConcatPlan) -> float:
    total = 0.0
    for job in plan.jobs:
        duration = job.total_duration
        if job.decision != ConcatDecision.IDENTICAL:
            total += duration
        total += duration
    return total


def _run_job(
    job: ConcatJob,
    progress: _ProgressTracker,
    is_cancelled: Callable[[], bool],
    encoder: str,
    crf: int,
) -> None:
    tmp_root = Path(tempfile.gettempdir()) / TEMP_DIR_NAME
    tmp_root.mkdir(parents=True, exist_ok=True)
    job_tmp_dir = Path(tempfile.mkdtemp(prefix=f"{job.group_name}_", dir=tmp_root))

    try:
        if job.decision == ConcatDecision.IDENTICAL:
            files_to_concat = [v.path for v in job.videos]
        elif job.decision == ConcatDecision.AUDIO_MISMATCH:
            files_to_concat = []
            for i, video in enumerate(job.videos):
                if is_cancelled():
                    return
                dst = job_tmp_dir / f"{i:04d}{video.path.suffix.lower()}"
                cmd = _normalize_audio_cmd(video.path, dst, video.audio_codec is not None)
                ok = _run_ffmpeg_with_progress(
                    cmd,
                    video.duration,
                    progress.step_callback(video.duration, f"{job.group_name}: 音声正規化 {video.path.name}"),
                    is_cancelled,
                )
                progress.advance(video.duration)
                if not ok:
                    raise ConcatError(f"{job.group_name}: 音声正規化に失敗しました（{video.path.name}）")
                files_to_concat.append(dst)
        else:  # VIDEO_MISMATCH（呼び出し側で再エンコード確認済みの前提）
            resolution = parse_group_resolution(job.group_name) or _most_common_resolution(job.videos)
            fps = _most_common_fps(job.videos)
            files_to_concat = []
            for i, video in enumerate(job.videos):
                if is_cancelled():
                    return
                dst = job_tmp_dir / f"{i:04d}.mp4"
                cmd = _reencode_cmd(
                    video.path, dst, resolution[0], resolution[1], fps,
                    video.audio_codec is not None, encoder, crf,
                )
                ok = _run_ffmpeg_with_progress(
                    cmd,
                    video.duration,
                    progress.step_callback(video.duration, f"{job.group_name}: 再エンコード {video.path.name}"),
                    is_cancelled,
                )
                progress.advance(video.duration)
                if not ok:
                    raise ConcatError(f"{job.group_name}: 再エンコードに失敗しました（{video.path.name}）")
                files_to_concat.append(dst)

        if is_cancelled():
            return

        list_path = job_tmp_dir / "list.txt"
        _write_concat_list(list_path, files_to_concat)
        job.output_path.parent.mkdir(parents=True, exist_ok=True)

        cmd = _concat_copy_cmd(list_path, job.output_path)
        ok = _run_ffmpeg_with_progress(
            cmd,
            job.total_duration,
            progress.step_callback(job.total_duration, f"{job.group_name}: 結合中"),
            is_cancelled,
        )
        progress.advance(job.total_duration)
        if not ok:
            if job.output_path.exists():
                job.output_path.unlink(missing_ok=True)
            if is_cancelled():
                return
            raise ConcatError(f"{job.group_name}: 結合に失敗しました")
    finally:
        shutil.rmtree(job_tmp_dir, ignore_errors=True)


def run_plan(
    plan: ConcatPlan,
    is_cancelled: Callable[[], bool],
    emit_progress: Callable[[int, int, str], None],
    encoder: str = DEFAULT_ENCODER,
    crf: int = DEFAULT_CRF,
) -> list[str]:
    warnings = [f"{name}: {reason}" for name, reason in plan.skipped]
    progress = _ProgressTracker(compute_total_work_seconds(plan), emit_progress)

    for job in plan.jobs:
        if is_cancelled():
            break
        try:
            _run_job(job, progress, is_cancelled, encoder, crf)
        except ConcatError as e:
            warnings.append(str(e))

    return warnings
