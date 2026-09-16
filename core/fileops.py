"""ごみ箱・移動・コピー・元に戻す。

移動・コピー先はルート外（任意のフォルダ）を許容する。history への記録・
excluded キーの更新は core/state.py（encode_path/decode_path）に委譲する。
"""

from __future__ import annotations

import shutil
from pathlib import Path

import send2trash

from core import grouping
from core.state import StateFile


def trash_files(paths: list[Path]) -> tuple[list[Path], list[str]]:
    """ごみ箱へ移動する。元に戻す（history）の対象にはしない
    （ごみ箱からの復元は手動で行う想定）。
    """
    applied: list[Path] = []
    warnings: list[str] = []

    for path in paths:
        if not path.exists():
            warnings.append(f"見つかりません。スキップしました: {path}")
            continue
        try:
            send2trash.send2trash(str(path))
            applied.append(path)
        except OSError as e:
            warnings.append(f"ごみ箱への移動に失敗しました: {path.name}: {e}")

    return applied, warnings


def move_files(items: list[tuple[Path, Path]]) -> tuple[list[tuple[Path, Path]], list[str]]:
    """移動先に同名ファイルがあればスキップして警告する（grouping.move_files を再利用）。"""
    return grouping.move_files(items)


def copy_files(items: list[tuple[Path, Path]]) -> tuple[list[tuple[Path, Path]], list[str]]:
    """コピー先に同名ファイルがあればスキップして警告する。"""
    applied: list[tuple[Path, Path]] = []
    warnings: list[str] = []

    for src, dst in items:
        if not src.exists():
            warnings.append(f"コピー元が見つかりません。スキップしました: {src}")
            continue
        if dst.exists():
            warnings.append(f"コピー先に同名ファイルがあります。スキップしました: {dst}")
            continue
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(str(src), str(dst))
        applied.append((src, dst))

    return applied, warnings


def undo_last(state: StateFile) -> list[str]:
    """state.history の最新エントリを種別に応じて元に戻す。

    move: 逆方向に移動し直す。
    copy: コピー先をごみ箱へ移動する（コピー元には触れない）。
    """
    if not state.history:
        return []

    op = state.history[-1].get("op")
    if op == "move":
        return grouping.undo_last_move(state)
    if op == "copy":
        return _undo_last_copy(state)
    return []


def _undo_last_copy(state: StateFile) -> list[str]:
    entry = state.history.pop()
    dest_paths = [state.decode_path(new_rel) for _old_rel, new_rel in entry.get("items", [])]
    _trashed, warnings = trash_files(dest_paths)
    return warnings
