"""中央ペインの動画テーブル。

- VideoTableModel: QAbstractTableModel。チェック列・評価・除外状態を保持する。
- VideoFilterProxyModel: QSortFilterProxyModel。評価・除外によるフィルタを行う。
- VideoTableView: QTableView。0〜5 / Space / ←→ / E のキー操作を Signal に変換する
  （↑↓ は QTableView 標準の行移動をそのまま使うので upgrade しない）。
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum, IntEnum
from typing import Any, Optional

from PySide6.QtCore import QAbstractTableModel, QModelIndex, QPersistentModelIndex, QSortFilterProxyModel, Qt, Signal
from PySide6.QtWidgets import QTableView

from core.probe import VideoInfo
from core.rating import RatingError, read_rating
from core.state import StateFile


class Column(IntEnum):
    CHECK = 0
    NAME = 1
    DURATION = 2
    RESOLUTION = 3
    VIDEO_CODEC = 4
    AUDIO = 5
    CREATED_AT = 6
    RATING = 7
    STATUS = 8


_HEADERS = {
    Column.CHECK: "",
    Column.NAME: "ファイル名",
    Column.DURATION: "長さ",
    Column.RESOLUTION: "解像度",
    Column.VIDEO_CODEC: "映像コーデック",
    Column.AUDIO: "音声",
    Column.CREATED_AT: "作成日時",
    Column.RATING: "★評価",
    Column.STATUS: "状態",
}


def _format_duration(seconds: float) -> str:
    total = int(seconds or 0)
    hours, remainder = divmod(total, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes:02d}:{secs:02d}"


def _format_audio(info: VideoInfo) -> str:
    if info.audio_codec is None:
        return "(音声なし)"
    parts = [info.audio_codec]
    if info.audio_sample_rate:
        parts.append(f"{info.audio_sample_rate}Hz")
    if info.audio_channels:
        parts.append(f"{info.audio_channels}ch")
    return " ".join(parts)


def _format_created_at(info: VideoInfo) -> str:
    text = datetime.fromtimestamp(info.creation_time).strftime("%Y-%m-%d %H:%M:%S")
    if info.creation_time_is_fallback:
        text += "（更新日時）"
    return text


def _format_rating(stars: int) -> str:
    return "★" * stars if stars else ""


class VideoTableModel(QAbstractTableModel):
    def __init__(self, parent: Optional[Any] = None) -> None:
        super().__init__(parent)
        self._rows: list[VideoInfo] = []
        self._ratings: list[int] = []
        self._checked: list[bool] = []
        self._state: Optional[StateFile] = None

    def set_videos(
        self, videos: list[VideoInfo], state: Optional[StateFile] = None
    ) -> None:
        self.beginResetModel()
        self._rows = list(videos)
        self._state = state
        self._checked = [False] * len(self._rows)
        self._ratings = []
        for info in self._rows:
            try:
                self._ratings.append(read_rating(info.path))
            except RatingError:
                self._ratings.append(0)
        self.endResetModel()

    def video_at(self, row: int) -> VideoInfo:
        return self._rows[row]

    def rating_at(self, row: int) -> int:
        return self._ratings[row]

    def is_excluded_at(self, row: int) -> bool:
        if self._state is None:
            return False
        return self._state.is_excluded(self._rows[row].path)

    def refresh_rating(self, row: int) -> None:
        try:
            self._ratings[row] = read_rating(self._rows[row].path)
        except RatingError:
            self._ratings[row] = 0
        index = self.index(row, Column.RATING)
        self.dataChanged.emit(index, index, [Qt.ItemDataRole.DisplayRole])

    def refresh_status(self, row: int) -> None:
        index = self.index(row, Column.STATUS)
        self.dataChanged.emit(index, index, [Qt.ItemDataRole.DisplayRole])

    def checked_rows(self) -> list[int]:
        return [row for row, checked in enumerate(self._checked) if checked]

    def set_checked(self, row: int, checked: bool) -> None:
        if self._checked[row] == checked:
            return
        self._checked[row] = checked
        index = self.index(row, Column.CHECK)
        self.dataChanged.emit(index, index, [Qt.ItemDataRole.CheckStateRole])

    def set_all_checked(self, checked: bool) -> None:
        for row in range(len(self._checked)):
            self.set_checked(row, checked)

    def invert_all_checked(self) -> None:
        for row in range(len(self._checked)):
            self.set_checked(row, not self._checked[row])

    def ordered_names(self) -> list[str]:
        return [info.path.name for info in self._rows]

    def move_row(self, row: int, offset: int) -> Optional[int]:
        """手動並び替え用。row の行を offset 分ずらす。移動後の行番号を返す（移動できなければ None）。"""
        target = row + offset
        if not (0 <= target < len(self._rows)):
            return None
        self.beginResetModel()
        self._rows[row], self._rows[target] = self._rows[target], self._rows[row]
        self._ratings[row], self._ratings[target] = self._ratings[target], self._ratings[row]
        self._checked[row], self._checked[target] = self._checked[target], self._checked[row]
        self.endResetModel()
        return target

    def rowCount(
        self, parent: QModelIndex | QPersistentModelIndex = QModelIndex()
    ) -> int:
        return 0 if parent.isValid() else len(self._rows)

    def columnCount(
        self, parent: QModelIndex | QPersistentModelIndex = QModelIndex()
    ) -> int:
        return 0 if parent.isValid() else len(_HEADERS)

    def flags(self, index: QModelIndex | QPersistentModelIndex) -> Qt.ItemFlag:
        if not index.isValid():
            return Qt.ItemFlag.NoItemFlags
        base = super().flags(index)
        if Column(index.column()) == Column.CHECK:
            return base | Qt.ItemFlag.ItemIsUserCheckable
        return base

    def headerData(
        self,
        section: int,
        orientation: Qt.Orientation,
        role: int = Qt.ItemDataRole.DisplayRole,
    ) -> Any:
        if role != Qt.ItemDataRole.DisplayRole or orientation != Qt.Orientation.Horizontal:
            return None
        return _HEADERS[Column(section)]

    def data(
        self,
        index: QModelIndex | QPersistentModelIndex,
        role: int = Qt.ItemDataRole.DisplayRole,
    ) -> Any:
        if not index.isValid():
            return None
        row = index.row()
        column = Column(index.column())

        if role == Qt.ItemDataRole.CheckStateRole:
            if column == Column.CHECK:
                return (
                    Qt.CheckState.Checked
                    if self._checked[row]
                    else Qt.CheckState.Unchecked
                )
            return None

        if role != Qt.ItemDataRole.DisplayRole:
            return None

        if column == Column.STATUS:
            return "除外中" if self.is_excluded_at(row) else ""
        if column == Column.RATING:
            return _format_rating(self._ratings[row])

        info = self._rows[row]
        if column == Column.NAME:
            return info.path.name
        if column == Column.DURATION:
            return _format_duration(info.duration)
        if column == Column.RESOLUTION:
            return f"{info.width}x{info.height}"
        if column == Column.VIDEO_CODEC:
            return info.video_codec
        if column == Column.AUDIO:
            return _format_audio(info)
        if column == Column.CREATED_AT:
            return _format_created_at(info)
        return None

    def setData(
        self,
        index: QModelIndex | QPersistentModelIndex,
        value: Any,
        role: int = Qt.ItemDataRole.EditRole,
    ) -> bool:
        if role == Qt.ItemDataRole.CheckStateRole and Column(index.column()) == Column.CHECK:
            checked = int(value) == int(Qt.CheckState.Checked.value)
            self.set_checked(index.row(), checked)
            return True
        return False


class FilterMode(Enum):
    ALL = "all"
    RATED = "rated"
    MIN_STARS = "min_stars"
    UNRATED = "unrated"
    EXCLUDED = "excluded"


class VideoFilterProxyModel(QSortFilterProxyModel):
    def __init__(self, parent: Optional[Any] = None) -> None:
        super().__init__(parent)
        self._mode = FilterMode.ALL
        self._min_stars = 1

    def set_filter(self, mode: FilterMode, min_stars: int = 1) -> None:
        self._mode = mode
        self._min_stars = min_stars
        self.invalidateFilter()

    def filterAcceptsRow(
        self, source_row: int, source_parent: QModelIndex | QPersistentModelIndex
    ) -> bool:
        model: VideoTableModel = self.sourceModel()
        if self._mode == FilterMode.ALL:
            return True
        if self._mode == FilterMode.RATED:
            return model.rating_at(source_row) >= 1
        if self._mode == FilterMode.MIN_STARS:
            return model.rating_at(source_row) >= self._min_stars
        if self._mode == FilterMode.UNRATED:
            return model.rating_at(source_row) == 0
        if self._mode == FilterMode.EXCLUDED:
            return model.is_excluded_at(source_row)
        return True


class VideoTableView(QTableView):
    """0〜5・Space・←→(Shiftで30秒)・E・Delete キーを Signal に変換する。

    ↑↓ は基底クラス標準の行移動をそのまま使うため super().keyPressEvent() に委ねる。
    """

    rate_requested = Signal(int)
    clear_rating_requested = Signal()
    play_pause_requested = Signal()
    seek_requested = Signal(float)
    toggle_exclude_requested = Signal()
    delete_requested = Signal()

    _RATING_KEYS = {
        Qt.Key.Key_1: 1,
        Qt.Key.Key_2: 2,
        Qt.Key.Key_3: 3,
        Qt.Key.Key_4: 4,
        Qt.Key.Key_5: 5,
    }

    def keyPressEvent(self, event) -> None:
        key = event.key()

        if key in self._RATING_KEYS:
            self.rate_requested.emit(self._RATING_KEYS[key])
            event.accept()
            return
        if key == Qt.Key.Key_0:
            self.clear_rating_requested.emit()
            event.accept()
            return
        if key == Qt.Key.Key_Space:
            self.play_pause_requested.emit()
            event.accept()
            return
        if key in (Qt.Key.Key_Left, Qt.Key.Key_Right):
            shift = bool(event.modifiers() & Qt.KeyboardModifier.ShiftModifier)
            magnitude = 30.0 if shift else 5.0
            seconds = -magnitude if key == Qt.Key.Key_Left else magnitude
            self.seek_requested.emit(seconds)
            event.accept()
            return
        if key == Qt.Key.Key_E:
            self.toggle_exclude_requested.emit()
            event.accept()
            return
        if key == Qt.Key.Key_Delete:
            self.delete_requested.emit()
            event.accept()
            return

        super().keyPressEvent(event)
