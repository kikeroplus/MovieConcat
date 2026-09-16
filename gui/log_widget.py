"""画面下部のログ表示（core.applog.logger の内容を表示する）。"""

from __future__ import annotations

import logging
from typing import Optional

from PySide6.QtCore import QObject, Signal
from PySide6.QtWidgets import QPlainTextEdit, QWidget


class QtLogHandler(QObject, logging.Handler):
    """logging.Handler を Qt Signal 経由で GUI スレッドへ橋渡しする。

    ワーカースレッドから logger.info() 等が呼ばれても、emit はその呼び出しスレッドで
    実行されるため、直接ウィジェットを操作せず Signal 経由にする
    （mpv のプロパティ監視コールバックと同じパターン）。
    """

    message_logged = Signal(str)

    def __init__(self, parent: Optional[QObject] = None) -> None:
        QObject.__init__(self, parent)
        logging.Handler.__init__(self)

    def emit(self, record: logging.LogRecord) -> None:
        try:
            self.message_logged.emit(self.format(record))
        except Exception:
            pass


class LogView(QPlainTextEdit):
    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setReadOnly(True)
        self.setMaximumBlockCount(2000)
        self.setMaximumHeight(120)

    def append_line(self, text: str) -> None:
        self.appendPlainText(text)
