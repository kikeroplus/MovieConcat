"""解像度別フォルダ分け（`{幅}x{高さ}` フォルダへの再配置）。

MovieChecker 同様、現在のフォルダ配置に関わらずどのタイミングで実行しても
動画が正しいグループフォルダに収まる状態にする（繰り返し実行しても安全）。
未分類・既存のグループフォルダを問わず対象とし、既に正しい場所にある動画は
何もしない（no-op）。ロジックの詳細は CLAUDE.md 9 章、既存コードとの差分は
docs/legacy_notes.md 参照。
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from core.probe import OUTPUT_DIR_NAME, VideoInfo
from core.state import StateFile


@dataclass(frozen=True)
class GroupingPlanItem:
    video: VideoInfo
    group_name: str
    destination: Path


@dataclass(frozen=True)
class GroupingPlan:
    items: list[GroupingPlanItem] = field(default_factory=list)
    skipped: list[tuple[VideoInfo, str]] = field(default_factory=list)

    def counts_by_group(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for item in self.items:
            counts[item.group_name] = counts.get(item.group_name, 0) + 1
        return counts


def group_name_for(info: VideoInfo) -> str:
    dims = f"{info.width}x{info.height}"
    if info.path.suffix.lower() == ".wmv":
        return f"{dims}_wmv"
    return dims


def build_plan(
    root: Path,
    candidates: list[VideoInfo],
    is_excluded: Callable[[Path], bool],
) -> GroupingPlan:
    """指定された動画一覧を対象に移動計画を立てる。

    候補一覧の決め方は呼び出し側（GUI）の責務:
    チェックされた行があればそれだけを、なければルート配下（出力動画を除く）
    全体を候補として渡す（CLAUDE.md「操作対象の決め方」のチェック優先ルールに従う）。

    出力動画フォルダ内のファイルは（万一渡されても）常に除外する。
    除外フラグが立っている動画、既に正しい場所にある動画は対象に含めない（no-op）。
    移動先に既存の同名ファイルがある場合（今回の計画内での衝突も含む）はスキップし、
    その動画は元の場所に残す（処理対象にならなかったファイルは放置する）。
    """
    items: list[GroupingPlanItem] = []
    skipped: list[tuple[VideoInfo, str]] = []
    reserved: set[str] = set()

    for info in candidates:
        if info.path.relative_to(root).parts[0] == OUTPUT_DIR_NAME:
            continue
        if is_excluded(info.path):
            continue

        group_name = group_name_for(info)
        destination = root / group_name / info.path.name

        if info.path == destination:
            continue  # 既に正しい場所にある

        key = str(destination).casefold()
        if destination.exists() or key in reserved:
            skipped.append((info, f"移動先に同名ファイルがあります: {destination}"))
            continue

        reserved.add(key)
        items.append(
            GroupingPlanItem(video=info, group_name=group_name, destination=destination)
        )

    return GroupingPlan(items=items, skipped=skipped)


def move_files(moves: list[tuple[Path, Path]]) -> tuple[list[tuple[Path, Path]], list[str]]:
    """(移動元, 移動先) のペア一覧を移動する。

    移動先に同名ファイルがある／移動元が既に無い場合はその項目をスキップして警告を積む。
    移動先が別ドライブの可能性がある（別フォルダへまとめる機能）ため、
    `Path.rename` ではなく `shutil.move` を使う（同一ドライブなら内部的に rename と同等）。
    戻り値: (実際に移動できたペアの一覧, 警告メッセージの一覧)。
    """
    applied: list[tuple[Path, Path]] = []
    warnings: list[str] = []

    for src, dst in moves:
        if not src.exists():
            warnings.append(f"移動元が見つかりません。スキップしました: {src}")
            continue
        if dst.exists():
            warnings.append(f"移動先に同名ファイルがあります。スキップしました: {dst}")
            continue
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(src), str(dst))
        applied.append((src, dst))

    return applied, warnings


def apply_plan(plan: GroupingPlan) -> tuple[list[tuple[Path, Path]], list[str]]:
    moves = [(item.video.path, item.destination) for item in plan.items]
    applied, warnings = move_files(moves)
    skip_warnings = [reason for _info, reason in plan.skipped]
    return applied, warnings + skip_warnings


def undo_last_move(state: StateFile) -> list[str]:
    """state.history の最新エントリが "move" ならそれを逆順に適用して取り除く。

    history が空、または最新エントリが move でない場合は何もせず空リストを返す。
    """
    if not state.history or state.history[-1].get("op") != "move":
        return []

    entry = state.history.pop()
    reverse_moves = [
        (state.decode_path(new_rel), state.decode_path(old_rel))
        for old_rel, new_rel in entry.get("items", [])
    ]
    _applied, warnings = move_files(reverse_moves)

    for src, dst in _applied:
        # dst（元の場所）に戻ったので、excluded のキーも new -> old へ戻す
        state.rename_path(src, dst)

    return warnings
