"""ログ設定。画面下部とファイル（%LOCALAPPDATA%\\MovieManager\\app.log）の両方に出す。

画面への出力（QtLogHandler）は gui 側で logger にハンドラを追加する形で行う
（core/ は GUI に依存しないため、ここではファイル出力のみを設定する）。
"""

from __future__ import annotations

import logging
from pathlib import Path

from core.settings import app_data_dir

LOG_FILENAME = "app.log"

logger = logging.getLogger("moviemanager")


def setup_file_logging() -> Path:
    """ファイルへのロギングを設定し、ログファイルのパスを返す。"""
    directory = app_data_dir()
    directory.mkdir(parents=True, exist_ok=True)
    log_path = directory / LOG_FILENAME

    logger.setLevel(logging.INFO)
    if not any(isinstance(h, logging.FileHandler) for h in logger.handlers):
        handler = logging.FileHandler(log_path, encoding="utf-8")
        handler.setFormatter(
            logging.Formatter("%(asctime)s [%(levelname)s] %(message)s")
        )
        logger.addHandler(handler)

    return log_path
