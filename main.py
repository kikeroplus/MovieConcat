"""MovieManager エントリーポイント。"""

from __future__ import annotations

import sys

from PySide6.QtWidgets import QApplication, QMessageBox

from core import envcheck
from core.appdir import get_app_dir
from core.applog import logger, setup_file_logging

APP_DIR = get_app_dir()


def _is_mpv_command_timing_error(exc_type, exc_value) -> bool:
    """mpv コマンドがタイミング良く失敗しただけの例外か判定する。

    シークバーのドラッグやホイールでのシーク・スキップを勢いよく連続して行うと、
    ちょうど動画の切り替わり（リレー再生でリストの末尾に達した瞬間など）と重なって
    python-mpv 側のコマンドがまれに失敗する（`SystemError: Error running mpv
    command, -12, ...`）。実害のない一時的なタイミングの問題であり、操作を続ければ
    特に問題なく動作するため、ユーザーには「予期しないエラー」ダイアログで毎回
    知らせず、ログにだけ残す。
    """
    return exc_type is SystemError and bool(exc_value.args) and exc_value.args[0] == "Error running mpv command"


def _install_exception_hook() -> None:
    def handle_exception(exc_type, exc_value, exc_tb) -> None:
        logger.error("予期しない例外", exc_info=(exc_type, exc_value, exc_tb))
        if _is_mpv_command_timing_error(exc_type, exc_value):
            return
        QMessageBox.critical(
            None, "予期しないエラー", f"{exc_type.__name__}: {exc_value}"
        )

    sys.excepthook = handle_exception


def main() -> int:
    log_path = setup_file_logging()
    logger.info("MovieManager 起動 (log: %s)", log_path)
    _install_exception_hook()

    app = QApplication(sys.argv)

    if envcheck.libmpv_missing(APP_DIR):
        from gui.dialogs import show_libmpv_missing_and_exit

        logger.error("libmpv-2.dll が見つからないため起動を中止します")
        show_libmpv_missing_and_exit(None)
        return 1

    missing = envcheck.missing_requirements(APP_DIR)
    if missing:
        from gui.dialogs import show_missing_requirements

        logger.warning("不足している外部ツール: %s", ", ".join(missing))
        show_missing_requirements(None, missing)

    from gui.main_window import MainWindow

    window = MainWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
