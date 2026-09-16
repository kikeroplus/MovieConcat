"""状態ファイル（除外・並び順・移動履歴）の読み書き。

`ルート\\.moviemanager.json`（UTF-8）に保存する。パスは原則ルートからの相対パスだが、
移動・コピー先がルート外の場合（別フォルダへまとめる機能）は絶対パス文字列で保存する
（encode_path/decode_path 参照）。manual_order（Phase 4 で使用）はここでは読み書きせず、
既存の値をそのまま保持して書き戻す（前方互換）。history には move（フォルダ分け・
別フォルダへまとめる）と copy（別フォルダへまとめる）の操作を記録する。
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

STATE_FILENAME = ".moviemanager.json"
STATE_VERSION = 1


class StateFile:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.path = root / STATE_FILENAME
        self.excluded: set[str] = set()
        self.manual_order: dict[str, list[str]] = {}
        self.history: list[dict[str, Any]] = []
        self._load()

    def _load(self) -> None:
        if not self.path.exists():
            return
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return
        self.excluded = set(data.get("excluded", []))
        self.manual_order = data.get("manual_order", {})
        self.history = data.get("history", [])

    def save(self) -> None:
        data = {
            "version": STATE_VERSION,
            "excluded": sorted(self.excluded),
            "manual_order": self.manual_order,
            "history": self.history,
        }
        tmp_path = self.path.with_name(self.path.name + ".tmp")
        tmp_path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        tmp_path.replace(self.path)

    def relative_key(self, path: Path) -> str:
        return path.relative_to(self.root).as_posix()

    def encode_path(self, path: Path) -> str:
        """root 配下なら相対パス、外側（別フォルダへまとめる等）なら絶対パス文字列にする。"""
        try:
            return path.relative_to(self.root).as_posix()
        except ValueError:
            return path.resolve().as_posix()

    def decode_path(self, encoded: str) -> Path:
        """encode_path の逆変換。絶対パス文字列ならそのまま、相対ならルート基準で解決する。"""
        candidate = Path(encoded)
        if candidate.is_absolute():
            return candidate
        return self.root / encoded

    def is_excluded(self, path: Path) -> bool:
        return self.relative_key(path) in self.excluded

    def set_excluded(self, path: Path, excluded: bool) -> None:
        key = self.relative_key(path)
        if excluded:
            self.excluded.add(key)
        else:
            self.excluded.discard(key)

    def rename_path(self, old_path: Path, new_path: Path) -> None:
        """ファイルの移動・改名に合わせて excluded のキーを更新する。

        old_path・new_path のどちらが root 外でも例外にならないよう encode_path を使う
        （元に戻す処理では「ルート外にあった移動先」が old_path 側に来ることがあるため）。
        excluded には常に相対キーしか入らないので、old_path が root 外（＝絶対パス表記に
        なる）場合は該当キーが存在せず自然に no-op になる。
        new_path が root 外の場合は除外フラグの管理対象から単に外す
        （ルート外へ移動・削除したファイルは excluded / manual_order から取り除く）。
        """
        old_key = self.encode_path(old_path)
        if old_key not in self.excluded:
            return
        self.excluded.discard(old_key)
        try:
            new_key = self.relative_key(new_path)
        except ValueError:
            return
        self.excluded.add(new_key)

    def record_move(self, moves: list[tuple[Path, Path]]) -> None:
        """ファイル移動を history に 1 操作として記録し、excluded のキーを更新する。"""
        if not moves:
            return
        items: list[list[str]] = []
        for old_path, new_path in moves:
            old_key = self.relative_key(old_path)
            new_key = self.encode_path(new_path)
            self.rename_path(old_path, new_path)
            items.append([old_key, new_key])
        self.history.append(
            {
                "op": "move",
                "items": items,
                "time": datetime.now(timezone.utc).isoformat(),
            }
        )

    def record_copy(self, copies: list[tuple[Path, Path]]) -> None:
        """ファイルコピーを history に 1 操作として記録する。

        コピー元は変化しないため excluded は更新しない
        （コピーの取り消しは「コピー先をごみ箱へ」で行う。core/fileops.py 参照）。
        """
        if not copies:
            return
        items = [[self.relative_key(src), self.encode_path(dst)] for src, dst in copies]
        self.history.append(
            {
                "op": "copy",
                "items": items,
                "time": datetime.now(timezone.utc).isoformat(),
            }
        )
