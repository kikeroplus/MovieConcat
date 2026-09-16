"""リレー再生モード下部のサムネイル一覧（横方向・クリックでスキップ）。"""

from __future__ import annotations

from typing import Optional

from PySide6.QtCore import QSize, Qt, QThread, Signal
from PySide6.QtGui import QIcon, QPixmap
from PySide6.QtWidgets import (
    QButtonGroup,
    QHBoxLayout,
    QScrollArea,
    QSizePolicy,
    QToolButton,
    QWidget,
)

from core.probe import VideoInfo
from core.thumbnails import cached_thumbnail_path, generate_thumbnail

_THUMB_WIDTH = 160
_THUMB_HEIGHT = 90


class ThumbnailWorker(QThread):
    """サムネイルをバックグラウンドで生成し、できたものから順に通知する。"""

    thumbnail_ready = Signal(int, str)

    def __init__(self, videos: list[VideoInfo], parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._videos = videos
        self._cancelled = False

    def cancel(self) -> None:
        self._cancelled = True

    def run(self) -> None:
        for index, info in enumerate(self._videos):
            if self._cancelled:
                return
            path = cached_thumbnail_path(info.path) or generate_thumbnail(
                info.path, info.duration
            )
            if path is not None and not self._cancelled:
                self.thumbnail_ready.emit(index, str(path))


class ThumbnailStrip(QScrollArea):
    thumbnail_clicked = Signal(int)

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setWidgetResizable(True)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setFixedHeight(_THUMB_HEIGHT + 40)

        self._content = QWidget()
        self._layout = QHBoxLayout(self._content)
        self._layout.setContentsMargins(4, 4, 4, 4)
        self._layout.setSpacing(4)
        self._layout.addStretch(1)
        self.setWidget(self._content)

        self._buttons: list[QToolButton] = []
        self._button_group = QButtonGroup(self)
        self._button_group.setExclusive(True)
        self._button_group.idClicked.connect(self.thumbnail_clicked)

        self._worker: Optional[ThumbnailWorker] = None

    def set_videos(self, videos: list[VideoInfo]) -> None:
        self._stop_worker()

        for button in self._buttons:
            self._button_group.removeButton(button)
            button.deleteLater()
        self._buttons = []

        stretch_item = self._layout.takeAt(self._layout.count() - 1)

        for index, info in enumerate(videos):
            button = QToolButton()
            button.setCheckable(True)
            button.setAutoRaise(True)
            button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextUnderIcon)
            button.setIconSize(QSize(_THUMB_WIDTH, _THUMB_HEIGHT))
            button.setFixedSize(_THUMB_WIDTH + 12, _THUMB_HEIGHT + 32)
            button.setText(info.path.name)
            button.setToolTip(info.path.name)
            button.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
            self._layout.addWidget(button)
            self._button_group.addButton(button, index)
            self._buttons.append(button)

            cached = cached_thumbnail_path(info.path)
            if cached is not None:
                self._apply_thumbnail(index, cached)

        if stretch_item is not None:
            self._layout.addItem(stretch_item)
        else:
            self._layout.addStretch(1)

        self._worker = ThumbnailWorker(videos, self)
        self._worker.thumbnail_ready.connect(self._on_thumbnail_ready)
        self._worker.start()

    def set_current_index(self, index: int) -> None:
        if not (0 <= index < len(self._buttons)):
            return
        self._buttons[index].setChecked(True)
        self.ensureWidgetVisible(self._buttons[index], 40, 0)

    def clear(self) -> None:
        self.set_videos([])

    def shutdown(self) -> None:
        self._stop_worker()

    def _stop_worker(self) -> None:
        if self._worker is not None:
            self._worker.cancel()
            self._worker.wait(2000)
            self._worker = None

    def _on_thumbnail_ready(self, index: int, path: str) -> None:
        self._apply_thumbnail(index, path)

    def _apply_thumbnail(self, index: int, path) -> None:
        if not (0 <= index < len(self._buttons)):
            return
        pixmap = QPixmap(str(path))
        if pixmap.isNull():
            return
        self._buttons[index].setIcon(QIcon(pixmap))
