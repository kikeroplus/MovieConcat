"""長時間処理をバックグラウンドで実行するための QThread 基盤。

core/ の中で唯一 PySide6 を import するモジュール。
"""

from __future__ import annotations

from typing import Any, Callable

from PySide6.QtCore import QThread, Signal


class WorkerThread(QThread):
    """fn(worker, *args, **kwargs) を別スレッドで実行する汎用ワーカー。

    fn は self（worker インスタンス）を第一引数で受け取り、進捗報告には
    worker.progress.emit(done, total, message) を、キャンセル確認には
    worker.is_cancelled() を使う。
    """

    progress = Signal(int, int, str)
    finished_ok = Signal(object)
    failed = Signal(str)

    def __init__(
        self, fn: Callable[..., Any], *args: Any, **kwargs: Any
    ) -> None:
        super().__init__()
        self._fn = fn
        self._args = args
        self._kwargs = kwargs
        self._cancelled = False

    def cancel(self) -> None:
        self._cancelled = True

    def is_cancelled(self) -> bool:
        return self._cancelled

    def run(self) -> None:
        try:
            result = self._fn(self, *self._args, **self._kwargs)
        except Exception as e:
            self.failed.emit(str(e))
            return
        if not self._cancelled:
            self.finished_ok.emit(result)
