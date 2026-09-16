"""メインウィンドウ（3 ペイン: グループツリー / 動画テーブル / プレイヤー）。"""

from __future__ import annotations

import base64
import logging
from pathlib import Path
from typing import Optional

from PySide6.QtCore import QByteArray, QEvent, QItemSelectionModel, QTimer, Qt
from PySide6.QtGui import QKeySequence
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QCheckBox,
    QComboBox,
    QFileDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMainWindow,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QSpinBox,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from core import concat, fileops, grouping
from core.applog import logger
from core.concat import SortMode
from core.probe import OUTPUT_GROUP_NAME, ProbeCache, VideoInfo, scan_root
from core.rating import RatingError, write_rating
from core.settings import Settings
from core.state import StateFile
from core.worker import WorkerThread
from gui import dialogs
from gui.group_tree import GroupTree
from gui.log_widget import LogView, QtLogHandler
from gui.player import PlayerWidget
from gui.thumbnail_strip import ThumbnailStrip
from gui.video_table import Column, FilterMode, VideoFilterProxyModel, VideoTableModel, VideoTableView


def _run_scan(worker: WorkerThread, root: Path) -> dict[str, list[VideoInfo]]:
    cache = ProbeCache(root)
    return scan_root(root, cache, worker)


def _run_grouping(
    worker: WorkerThread, root: Path, plan: grouping.GroupingPlan, state: StateFile
) -> list[str]:
    applied, warnings = grouping.apply_plan(plan)
    state.record_move(applied)
    state.save()
    return warnings


def _run_undo(worker: WorkerThread, root: Path, state: StateFile) -> list[str]:
    warnings = fileops.undo_last(state)
    state.save()
    return warnings


def _run_concat(
    worker: WorkerThread, plan: concat.ConcatPlan, encoder: str, crf: int
) -> list[str]:
    def emit_progress(done: int, total: int, message: str) -> None:
        worker.progress.emit(done, total, message)

    return concat.run_plan(plan, worker.is_cancelled, emit_progress, encoder, crf)


def _run_trash(worker: WorkerThread, videos: list[VideoInfo], state: StateFile) -> list[str]:
    applied, warnings = fileops.trash_files([v.path for v in videos])
    for path in applied:
        state.set_excluded(path, False)
    state.save()
    return warnings


def _run_move_or_copy(
    worker: WorkerThread,
    items: list[tuple[Path, Path]],
    mode: str,
    state: StateFile,
) -> list[str]:
    if mode == "move":
        applied, warnings = fileops.move_files(items)
        state.record_move(applied)
    else:
        applied, warnings = fileops.copy_files(items)
        state.record_copy(applied)
    state.save()
    return warnings


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("MovieManager")
        self.resize(1440, 860)

        self._root: Optional[Path] = None
        self._groups: dict[str, list[VideoInfo]] = {}
        self._scan_thread: Optional[WorkerThread] = None
        self._maintenance_thread: Optional[WorkerThread] = None
        self._maintenance_ok = False
        self._maintenance_warnings: list[str] = []
        self._state: Optional[StateFile] = None
        self._settings = Settings()
        try:
            self._sort_mode = SortMode(self._settings.sort_mode)
        except ValueError:
            self._sort_mode = SortMode.NAME

        self._relay_active = False
        self._relay_videos: list[VideoInfo] = []
        self._relay_index = 0

        self._group_tree = GroupTree()
        self._table_model = VideoTableModel()
        self._proxy_model = VideoFilterProxyModel()
        self._proxy_model.setSourceModel(self._table_model)

        self._table_view = VideoTableView()
        self._table_view.setModel(self._proxy_model)
        self._table_view.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._table_view.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self._table_view.setSortingEnabled(True)
        self._table_view.sortByColumn(Column.NAME, Qt.SortOrder.AscendingOrder)
        self._table_view.verticalHeader().setVisible(False)
        self._table_view.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.Interactive
        )
        self._table_view.horizontalHeader().setStretchLastSection(True)
        self._table_view.horizontalHeader().setSectionResizeMode(
            Column.CHECK, QHeaderView.ResizeMode.Fixed
        )
        self._table_view.horizontalHeader().resizeSection(Column.CHECK, 28)

        self._player = PlayerWidget()

        self._center_widget = QWidget()
        center_layout = QVBoxLayout(self._center_widget)
        center_layout.setContentsMargins(0, 0, 0, 0)
        center_layout.addLayout(self._build_filter_bar())
        center_layout.addWidget(self._table_view, 1)

        self._splitter = QSplitter(Qt.Orientation.Horizontal)
        self._splitter.addWidget(self._group_tree)
        self._splitter.addWidget(self._center_widget)
        self._splitter.addWidget(self._player)
        self._splitter.setStretchFactor(0, 1)
        self._splitter.setStretchFactor(1, 3)
        self._splitter.setStretchFactor(2, 3)

        self._progress_bar = QProgressBar()
        self._progress_bar.setVisible(False)
        self._cancel_button = QPushButton("キャンセル")
        self._cancel_button.setVisible(False)
        bottom_bar = QHBoxLayout()
        bottom_bar.addWidget(self._progress_bar, 1)
        bottom_bar.addWidget(self._cancel_button)

        self._thumbnail_strip = ThumbnailStrip()
        self._thumbnail_strip.setVisible(False)

        self._log_view = LogView()
        self._log_handler = QtLogHandler(self)
        self._log_handler.setFormatter(logging.Formatter("%(asctime)s %(message)s", datefmt="%H:%M:%S"))
        self._log_handler.message_logged.connect(self._log_view.append_line)
        logger.addHandler(self._log_handler)

        central_widget = QWidget()
        central_layout = QVBoxLayout(central_widget)
        central_layout.setContentsMargins(0, 0, 0, 0)
        central_layout.addWidget(self._splitter, 1)
        central_layout.addWidget(self._thumbnail_strip)
        central_layout.addLayout(bottom_bar)
        central_layout.addWidget(self._log_view)
        self.setCentralWidget(central_widget)

        toolbar = self.addToolBar("main")
        self._open_action = toolbar.addAction("フォルダ選択")
        self._rescan_action = toolbar.addAction("再スキャン")
        self._rescan_action.setEnabled(False)
        self._group_action = toolbar.addAction("フォルダ分け")
        self._group_action.setEnabled(False)
        self._concat_action = toolbar.addAction("結合実行")
        self._concat_action.setEnabled(False)
        self._undo_action = toolbar.addAction("元に戻す")
        self._undo_action.setEnabled(False)
        self._undo_action.setShortcut(QKeySequence.StandardKey.Undo)
        self._trash_action = toolbar.addAction("ごみ箱へ")
        self._trash_action.setEnabled(False)
        self._move_to_folder_action = toolbar.addAction("別フォルダへまとめる")
        self._move_to_folder_action.setEnabled(False)
        self._settings_action = toolbar.addAction("設定")

        self._relay_action = toolbar.addAction("リレー再生")
        self._relay_action.setCheckable(True)
        self._relay_action.setEnabled(False)
        self._relay_loop_checkbox = QCheckBox("最後まで再生したらループ")
        toolbar.addWidget(self._relay_loop_checkbox)

        toolbar.addWidget(QLabel(" 結合順: "))
        self._sort_combo = QComboBox()
        self._sort_combo.addItem("ファイル名順", SortMode.NAME)
        self._sort_combo.addItem("作成日時順", SortMode.CREATED_AT)
        self._sort_combo.addItem("手動", SortMode.MANUAL)
        self._sort_combo.setCurrentIndex(max(self._sort_combo.findData(self._sort_mode), 0))
        toolbar.addWidget(self._sort_combo)

        self._status_bar = self.statusBar()
        self._status_bar.showMessage("フォルダを選択してください")

        self._open_action.triggered.connect(self._choose_folder)
        self._rescan_action.triggered.connect(self._start_rescan)
        self._group_action.triggered.connect(self._on_group_action)
        self._concat_action.triggered.connect(self._on_concat_action)
        self._undo_action.triggered.connect(self._on_undo_action)
        self._trash_action.triggered.connect(self._on_delete_requested)
        self._move_to_folder_action.triggered.connect(self._on_move_to_folder_action)
        self._settings_action.triggered.connect(self._on_settings_action)
        self._relay_action.toggled.connect(self._on_relay_toggled)
        self._relay_loop_checkbox.toggled.connect(self._on_relay_loop_toggled)
        self._player.playlist_index_changed.connect(self._on_playlist_index_changed)
        self._player.playlist_finished.connect(self._on_playlist_finished)
        self._thumbnail_strip.thumbnail_clicked.connect(self._on_thumbnail_clicked)
        self._thumbnail_strip.wheel_navigate.connect(self._on_thumbnail_wheel_navigate)
        self._sort_combo.currentIndexChanged.connect(self._on_sort_mode_changed)
        self._cancel_button.clicked.connect(self._on_cancel_clicked)
        self._group_tree.group_selected.connect(self._on_group_selected)
        self._table_view.selectionModel().currentRowChanged.connect(
            self._on_current_row_changed
        )
        self._table_view.rate_requested.connect(self._on_rate_requested)
        self._table_view.clear_rating_requested.connect(lambda: self._on_rate_requested(0))
        self._table_view.play_pause_requested.connect(self._player.toggle_pause)
        self._table_view.seek_requested.connect(self._player.seek_relative)
        self._table_view.toggle_exclude_requested.connect(self._on_toggle_exclude_requested)
        self._table_view.delete_requested.connect(self._on_delete_requested)

        # restoreGeometry() はウィンドウがまだ表示されずレイアウトも確定していない時点で
        # 呼ぶとサイズが正しく復元されないことがあるため、show() 後の最初のイベントループで
        # 実行されるよう遅延させる（QTimer.singleShot(0, ...)）。
        QTimer.singleShot(0, self._restore_window_geometry)
        self._restore_last_root()

        # リレー再生中は Space キーで再生/一時停止できるようにする。フォーカスが
        # どのウィジェット（サムネイルのボタン等）にあっても確実に割り込めるよう、
        # アプリ全体にイベントフィルタを仕込む（通常時はテーブル側の Space 処理のみ）。
        app_instance = QApplication.instance()
        if app_instance is not None:
            app_instance.installEventFilter(self)

    def eventFilter(self, obj, event) -> bool:  # noqa: N802 (Qt overrideの命名規則)
        if (
            self._relay_active
            and event.type() == QEvent.Type.KeyPress
            and event.key() == Qt.Key.Key_Space
        ):
            self._player.toggle_pause()
            return True
        return super().eventFilter(obj, event)

    def _restore_window_geometry(self) -> None:
        if self._settings.window_geometry:
            try:
                self.restoreGeometry(
                    QByteArray(base64.b64decode(self._settings.window_geometry))
                )
            except Exception:
                pass
        if self._settings.window_state:
            try:
                self.restoreState(QByteArray(base64.b64decode(self._settings.window_state)))
            except Exception:
                pass

    def _restore_last_root(self) -> None:
        if not self._settings.last_root:
            return
        candidate = Path(self._settings.last_root)
        if candidate.is_dir():
            self._root = candidate
            self._start_scan()

    def _build_filter_bar(self) -> QHBoxLayout:
        bar = QHBoxLayout()

        bar.addWidget(QLabel("フィルタ:"))
        self._filter_combo = QComboBox()
        self._filter_combo.addItem("すべて", FilterMode.ALL)
        self._filter_combo.addItem("評価済み(★1以上)", FilterMode.RATED)
        self._filter_combo.addItem("★N以上", FilterMode.MIN_STARS)
        self._filter_combo.addItem("未評価", FilterMode.UNRATED)
        self._filter_combo.addItem("除外中", FilterMode.EXCLUDED)
        bar.addWidget(self._filter_combo)

        self._filter_stars_spin = QSpinBox()
        self._filter_stars_spin.setRange(1, 5)
        self._filter_stars_spin.setValue(3)
        self._filter_stars_spin.setEnabled(False)
        bar.addWidget(self._filter_stars_spin)

        bar.addStretch(1)

        self._move_up_button = QPushButton("上へ")
        self._move_down_button = QPushButton("下へ")
        self._move_up_button.setVisible(False)
        self._move_down_button.setVisible(False)
        bar.addWidget(self._move_up_button)
        bar.addWidget(self._move_down_button)

        select_all_button = QPushButton("すべて選択")
        select_none_button = QPushButton("解除")
        select_invert_button = QPushButton("反転")
        select_visible_button = QPushButton("表示中のみ全選択")
        bar.addWidget(select_all_button)
        bar.addWidget(select_none_button)
        bar.addWidget(select_invert_button)
        bar.addWidget(select_visible_button)

        self._filter_combo.currentIndexChanged.connect(self._on_filter_changed)
        self._filter_stars_spin.valueChanged.connect(self._on_filter_changed)
        self._move_up_button.clicked.connect(lambda: self._on_move_row(-1))
        self._move_down_button.clicked.connect(lambda: self._on_move_row(1))
        select_all_button.clicked.connect(self._on_select_all)
        select_none_button.clicked.connect(self._on_select_none)
        select_invert_button.clicked.connect(self._on_select_invert)
        select_visible_button.clicked.connect(self._on_select_visible)

        return bar

    def closeEvent(self, event) -> None:  # noqa: N802 (Qt overrideの命名規則)
        self._settings.window_geometry = base64.b64encode(
            bytes(self.saveGeometry())
        ).decode("ascii")
        self._settings.window_state = base64.b64encode(bytes(self.saveState())).decode(
            "ascii"
        )
        self._settings.save()
        app_instance = QApplication.instance()
        if app_instance is not None:
            app_instance.removeEventFilter(self)
        self._thumbnail_strip.shutdown()
        self._player.shutdown()
        super().closeEvent(event)

    def _choose_folder(self) -> None:
        directory = QFileDialog.getExistingDirectory(self, "フォルダを選択")
        if not directory:
            return
        self._root = Path(directory)
        self._settings.last_root = str(self._root)
        self._settings.save()
        self._start_scan()

    def _start_rescan(self) -> None:
        if self._root is not None:
            self._start_scan()

    def _is_busy(self) -> bool:
        return self._scan_thread is not None or self._maintenance_thread is not None

    def _start_scan(self) -> None:
        if self._root is None or self._is_busy():
            return

        self._state = StateFile(self._root)
        self._set_busy_ui(True)
        self._status_bar.showMessage(f"スキャン中: {self._root}")

        self._scan_thread = WorkerThread(_run_scan, self._root)
        self._scan_thread.progress.connect(self._on_scan_progress)
        self._scan_thread.finished_ok.connect(self._on_scan_finished)
        self._scan_thread.failed.connect(self._on_scan_failed)
        self._scan_thread.finished.connect(self._on_scan_thread_finished)
        self._scan_thread.start()

    def _set_busy_ui(self, busy: bool) -> None:
        self._open_action.setEnabled(not busy)
        self._rescan_action.setEnabled(not busy and self._root is not None)
        self._group_action.setEnabled(not busy and self._root is not None)
        self._concat_action.setEnabled(not busy and self._root is not None)
        self._undo_action.setEnabled(
            not busy and self._state is not None and bool(self._state.history)
        )
        self._trash_action.setEnabled(not busy and self._root is not None)
        self._move_to_folder_action.setEnabled(not busy and self._root is not None)
        self._relay_action.setEnabled(not busy and self._proxy_model.rowCount() > 0)
        self._progress_bar.setVisible(busy)
        self._cancel_button.setVisible(busy)
        if busy:
            self._progress_bar.setRange(0, 0)
            self._progress_bar.setValue(0)

    def _on_scan_progress(self, done: int, total: int, name: str) -> None:
        self._status_bar.showMessage(f"スキャン中 ({done}/{total}): {name}")
        self._update_progress_bar(done, total)

    def _update_progress_bar(self, done: int, total: int) -> None:
        if total <= 0:
            self._progress_bar.setRange(0, 0)
            return
        self._progress_bar.setRange(0, total)
        self._progress_bar.setValue(min(done, total))

    def _on_cancel_clicked(self) -> None:
        if self._maintenance_thread is not None:
            self._maintenance_thread.cancel()
        elif self._scan_thread is not None:
            self._scan_thread.cancel()
        self._status_bar.showMessage("キャンセル中...")

    def _on_settings_action(self) -> None:
        if dialogs.show_settings_dialog(self, self._settings):
            self._settings.save()
            logger.info(
                "設定を変更しました: encoder=%s crf=%s",
                self._settings.encoder,
                self._settings.crf,
            )

    def _on_relay_toggled(self, checked: bool) -> None:
        if checked:
            self._start_relay()
        else:
            self._stop_relay()

    def _relay_videos_from_table(self) -> list[VideoInfo]:
        return [
            self._table_model.video_at(
                self._proxy_model.mapToSource(self._proxy_model.index(row, 0)).row()
            )
            for row in range(self._proxy_model.rowCount())
        ]

    def _start_relay(self) -> None:
        videos = self._relay_videos_from_table()
        if not videos:
            QMessageBox.information(self, "リレー再生", "再生する動画がありません。")
            self._relay_action.setChecked(False)
            return

        logger.info("リレー再生を開始: %d本", len(videos))
        self._relay_active = True
        self._apply_relay_layout(True)
        self._begin_relay_sequence(videos)

    def _begin_relay_sequence(self, videos: list[VideoInfo]) -> None:
        # 動画の切り替え自体は mpv 自身のプレイリスト機能に任せる（Python 側で
        # 毎回 load() し直すより切り替えが速く、映像が途切れにくいため）。
        self._relay_videos = videos
        self._relay_index = 0
        self._thumbnail_strip.set_videos(videos)
        self._player.load_playlist(
            [v.path for v in videos], loop=self._relay_loop_checkbox.isChecked()
        )

    def _stop_relay(self) -> None:
        if not self._relay_active:
            return
        logger.info("リレー再生を終了")
        self._relay_active = False
        self._relay_videos = []
        self._player.stop_playlist()
        self._thumbnail_strip.clear()
        self._apply_relay_layout(False)
        self._relay_action.setChecked(False)

    def _apply_relay_layout(self, active: bool) -> None:
        # グループツリー（フォルダ選択）はリレー中も表示したままにし、
        # 別フォルダへすぐ切り替えられるようにする。隠すのは動画テーブルのみ。
        self._center_widget.setVisible(not active)
        self._thumbnail_strip.setVisible(active)
        if active:
            total = sum(self._splitter.sizes()) or 1
            self._splitter.setSizes([total // 6, 0, total * 5 // 6])
        else:
            total = sum(self._splitter.sizes()) or 1
            self._splitter.setSizes([total // 5, total * 2 // 5, total * 2 // 5])

    def _on_playlist_index_changed(self, index: int) -> None:
        if not self._relay_active or not (0 <= index < len(self._relay_videos)):
            return
        self._relay_index = index
        info = self._relay_videos[index]
        self._thumbnail_strip.set_current_index(index)
        self._status_bar.showMessage(
            f"リレー再生中 ({index + 1}/{len(self._relay_videos)}): {info.path.name}"
        )

    def _on_playlist_finished(self) -> None:
        # ループ OFF で末尾まで再生し終えた（mpv 内部で自動的にここまで到達した）。
        if self._relay_active:
            self._stop_relay()

    def _on_relay_loop_toggled(self, checked: bool) -> None:
        self._player.set_playlist_loop(checked)

    def _on_thumbnail_clicked(self, index: int) -> None:
        if not self._relay_active:
            return
        self._player.jump_to_playlist_index(index)

    def _on_thumbnail_wheel_navigate(self, direction: int) -> None:
        if not self._relay_active:
            return
        new_index = self._relay_index + direction
        if not (0 <= new_index < len(self._relay_videos)):
            return
        self._player.jump_to_playlist_index(new_index)

    def _on_scan_finished(self, groups: dict[str, list[VideoInfo]]) -> None:
        self._groups = groups
        self._group_tree.set_groups(groups)
        total = sum(len(v) for v in groups.values())
        logger.info("スキャン完了: %d本 (%s)", total, self._root)
        self._status_bar.showMessage(f"スキャン完了: {total} 本 ({self._root})")

    def _on_scan_failed(self, message: str) -> None:
        logger.error("スキャンに失敗しました: %s", message)
        QMessageBox.critical(self, "スキャンに失敗しました", message)
        self._status_bar.showMessage("スキャンに失敗しました")

    def _on_scan_thread_finished(self) -> None:
        self._scan_thread = None
        self._set_busy_ui(False)

    def _on_group_selected(self, name: str) -> None:
        videos = self._groups.get(name, [])
        if self._sort_mode == SortMode.MANUAL and self._state is not None:
            manual = self._state.manual_order.get(name)
            videos = concat.order_videos(videos, SortMode.MANUAL, manual)
        self._table_model.set_videos(videos, self._state)
        if self._proxy_model.rowCount() > 0:
            first_index = self._proxy_model.index(0, 0)
            self._select_row(first_index)
        self._table_view.setFocus()
        self._relay_action.setEnabled(not self._is_busy() and self._proxy_model.rowCount() > 0)

        if self._relay_active:
            # リレー再生中にグループツリーで別フォルダを選んだ場合、
            # そのフォルダの動画でリレーを続ける（先頭から）。
            new_videos = self._relay_videos_from_table()
            if not new_videos:
                self._status_bar.showMessage(f"「{name}」には再生できる動画がありません。")
                return
            logger.info("リレー再生の対象フォルダを切り替え: %s (%d本)", name, len(new_videos))
            self._begin_relay_sequence(new_videos)

    def _on_sort_mode_changed(self) -> None:
        self._sort_mode = self._sort_combo.currentData()
        self._settings.sort_mode = self._sort_mode.value
        self._settings.save()
        manual = self._sort_mode == SortMode.MANUAL
        self._move_up_button.setVisible(manual)
        self._move_down_button.setVisible(manual)
        # 手動モードでは並び替えた行を見失わないよう、クリックソートは無効化する
        self._table_view.setSortingEnabled(not manual)
        current_group = self._group_tree.current_group_name()
        if current_group is not None:
            self._on_group_selected(current_group)

    def _on_move_row(self, offset: int) -> None:
        if self._sort_mode != SortMode.MANUAL or self._state is None:
            return
        current = self._table_view.currentIndex()
        if not current.isValid():
            return
        source_row = self._proxy_model.mapToSource(current).row()
        new_row = self._table_model.move_row(source_row, offset)
        if new_row is None:
            return
        self._select_row(self._proxy_model.mapFromSource(self._table_model.index(new_row, 0)))

        group_name = self._group_tree.current_group_name()
        if group_name is not None:
            self._state.manual_order[group_name] = self._table_model.ordered_names()
            self._state.save()
        self._table_view.setFocus()

    def _on_current_row_changed(self, current, previous) -> None:
        if not current.isValid():
            return
        source_index = self._proxy_model.mapToSource(current)
        info = self._table_model.video_at(source_index.row())
        try:
            self._player.load(info.path)
        except Exception as e:
            logger.warning("再生に失敗しました: %s: %s", info.path, e)
            self._status_bar.showMessage(f"再生に失敗しました: {info.path.name}: {e}")

    def _select_row(self, proxy_index) -> None:
        self._table_view.setCurrentIndex(proxy_index)
        self._table_view.selectionModel().select(
            proxy_index,
            QItemSelectionModel.SelectionFlag.ClearAndSelect
            | QItemSelectionModel.SelectionFlag.Rows,
        )

    def _on_filter_changed(self) -> None:
        mode = self._filter_combo.currentData()
        self._filter_stars_spin.setEnabled(mode == FilterMode.MIN_STARS)
        self._proxy_model.set_filter(mode, self._filter_stars_spin.value())
        self._relay_action.setEnabled(not self._is_busy() and self._proxy_model.rowCount() > 0)

    def _on_select_all(self) -> None:
        self._table_model.set_all_checked(True)
        self._table_view.setFocus()

    def _on_select_none(self) -> None:
        self._table_model.set_all_checked(False)
        self._table_view.setFocus()

    def _on_select_invert(self) -> None:
        self._table_model.invert_all_checked()
        self._table_view.setFocus()

    def _on_select_visible(self) -> None:
        for proxy_row in range(self._proxy_model.rowCount()):
            source_index = self._proxy_model.mapToSource(
                self._proxy_model.index(proxy_row, 0)
            )
            self._table_model.set_checked(source_index.row(), True)
        self._table_view.setFocus()

    def _target_rows(self) -> list[int]:
        """一括操作の対象行（ソースモデル基準）。チェック優先、なければ選択行。"""
        checked = self._table_model.checked_rows()
        if checked:
            return checked
        rows = {
            self._proxy_model.mapToSource(index).row()
            for index in self._table_view.selectionModel().selectedRows()
        }
        return sorted(rows)

    def _on_rate_requested(self, stars: int) -> None:
        rows = self._target_rows()
        if not rows:
            return

        failures: list[str] = []
        for row in rows:
            info = self._table_model.video_at(row)
            if self._player.current_path() == info.path:
                self._player.unload()
            try:
                write_rating(info.path, stars)
            except RatingError as e:
                failures.append(f"{info.path.name}: {e}")
            self._table_model.refresh_rating(row)

        if failures:
            logger.warning("評価の書き込みに失敗: %s", "; ".join(failures))
            QMessageBox.warning(self, "評価の書き込みに失敗しました", "\n".join(failures))

        if stars != 0 and len(rows) == 1:
            self._advance_to_next_row(rows[0])

    def _advance_to_next_row(self, source_row: int) -> None:
        proxy_index = self._proxy_model.mapFromSource(
            self._table_model.index(source_row, 0)
        )
        next_row = proxy_index.row() + 1
        if 0 <= next_row < self._proxy_model.rowCount():
            self._select_row(self._proxy_model.index(next_row, 0))

    def _on_toggle_exclude_requested(self) -> None:
        if self._state is None:
            return
        rows = self._target_rows()
        if not rows:
            return

        for row in rows:
            info = self._table_model.video_at(row)
            self._state.set_excluded(info.path, not self._state.is_excluded(info.path))
            self._table_model.refresh_status(row)

        self._state.save()
        self._proxy_model.invalidateFilter()

    def _unload_if_playing(self, videos: list[VideoInfo]) -> None:
        current = self._player.current_path()
        if current is not None and any(v.path == current for v in videos):
            self._player.unload()

    def _on_delete_requested(self) -> None:
        if self._root is None or self._state is None or self._is_busy():
            return
        rows = self._target_rows()
        if not rows:
            return
        videos = [self._table_model.video_at(row) for row in rows]

        if not dialogs.confirm_trash(self, len(videos)):
            return

        self._unload_if_playing(videos)
        self._start_maintenance_thread(_run_trash, "ごみ箱へ移動中...", videos, self._state)

    def _on_move_to_folder_action(self) -> None:
        if self._root is None or self._state is None or self._is_busy():
            return
        rows = self._target_rows()
        if not rows:
            return
        videos = [self._table_model.video_at(row) for row in rows]

        destination_dir = QFileDialog.getExistingDirectory(self, "移動先フォルダを選択")
        if not destination_dir:
            return
        destination_root = Path(destination_dir)

        mode = dialogs.choose_move_or_copy(self, len(videos))
        if mode is None:
            return

        self._unload_if_playing(videos)
        items = [(v.path, destination_root / v.path.name) for v in videos]
        verb = "移動" if mode == "move" else "コピー"
        self._start_maintenance_thread(
            _run_move_or_copy, f"{verb}中...", items, mode, self._state
        )

    def _grouping_candidates(self) -> list[VideoInfo]:
        """フォルダ分けの候補一覧。チェックされた行があればそれだけ（今表示中のグループ内に限る）、
        なければルート配下（出力動画を除く）全体。"""
        checked_rows = self._table_model.checked_rows()
        if checked_rows:
            return [self._table_model.video_at(row) for row in checked_rows]
        return [
            info
            for name, videos in self._groups.items()
            if name != OUTPUT_GROUP_NAME
            for info in videos
        ]

    def _on_group_action(self) -> None:
        if self._root is None or self._state is None or self._is_busy():
            return

        is_partial = bool(self._table_model.checked_rows())
        candidates = self._grouping_candidates()
        plan = grouping.build_plan(self._root, candidates, self._state.is_excluded)
        skip_reasons = [reason for _info, reason in plan.skipped]
        if not dialogs.confirm_grouping(self, plan.counts_by_group(), skip_reasons, is_partial):
            return

        self._player.unload()
        self._start_maintenance_thread(_run_grouping, "フォルダ分け中...", self._root, plan, self._state)

    def _on_undo_action(self) -> None:
        if self._root is None or self._state is None or self._is_busy():
            return
        if not self._state.history:
            QMessageBox.information(self, "元に戻す", "元に戻す操作がありません。")
            return

        self._player.unload()
        self._start_maintenance_thread(_run_undo, "元に戻しています...", self._root, self._state)

    def _on_concat_action(self) -> None:
        if self._root is None or self._state is None or self._is_busy():
            return

        checked_rows = self._table_model.checked_rows()
        is_partial = bool(checked_rows)
        if is_partial:
            videos = [self._table_model.video_at(row) for row in checked_rows]
            group_name = self._group_tree.current_group_name() or "選択動画"
            plan = concat.build_plan_for_checked(
                self._root, group_name, videos, self._state.is_excluded, self._sort_mode, self._state
            )
        else:
            plan = concat.build_plan(
                self._root, self._groups, self._state.is_excluded, self._sort_mode, self._state
            )

        for job in list(plan.jobs):
            if job.decision != concat.ConcatDecision.VIDEO_MISMATCH:
                continue
            if is_partial:
                # チェック分の結合では再エンコード確認は出さず、フォーマット不一致として
                # 「結合できません」で終わらせる（確認ダイアログを挟まない軽い操作にするため）。
                plan.jobs.remove(job)
                plan.skipped.append(
                    (job.group_name, "選択した動画はフォーマットが一致しないため結合できません")
                )
                continue
            if not dialogs.confirm_video_mismatch_reencode(self, job.group_name):
                plan.jobs.remove(job)
                plan.skipped.append((job.group_name, "映像不一致のため再エンコードせずスキップ"))

        if not dialogs.confirm_concat_plan(self, plan, is_partial):
            return

        self._player.unload()
        self._start_maintenance_thread(
            _run_concat, "結合中...", plan, self._settings.encoder, self._settings.crf
        )

    def _start_maintenance_thread(self, fn, status_message: str, *args) -> None:
        self._set_busy_ui(True)
        self._status_bar.showMessage(status_message)

        self._maintenance_thread = WorkerThread(fn, *args)
        self._maintenance_thread.progress.connect(self._on_maintenance_progress)
        self._maintenance_thread.finished_ok.connect(self._on_maintenance_finished)
        self._maintenance_thread.failed.connect(self._on_maintenance_failed)
        self._maintenance_thread.finished.connect(self._on_maintenance_thread_finished)
        self._maintenance_thread.start()

    def _on_maintenance_progress(self, done: int, total: int, message: str) -> None:
        self._status_bar.showMessage(message)
        self._update_progress_bar(done, total)

    def _on_maintenance_finished(self, warnings: list[str]) -> None:
        # finished_ok は QThread.finished より先に届く。ここで即 _start_scan() すると
        # self._maintenance_thread がまだ残っていて _is_busy() に弾かれるため、
        # 実際の再スキャンは _on_maintenance_thread_finished（スレッド完全終了後）で行う。
        self._maintenance_ok = True
        self._maintenance_warnings = warnings

    def _on_maintenance_failed(self, message: str) -> None:
        self._maintenance_ok = False
        logger.error("処理に失敗しました: %s", message)
        QMessageBox.critical(self, "処理に失敗しました", message)
        self._status_bar.showMessage("処理に失敗しました")

    def _on_maintenance_thread_finished(self) -> None:
        self._maintenance_thread = None
        self._set_busy_ui(False)

        if not self._maintenance_ok:
            self._maintenance_ok = False
            return

        warnings, self._maintenance_warnings = self._maintenance_warnings, []
        self._maintenance_ok = False
        if warnings:
            logger.warning("完了しましたが警告があります: %s", "; ".join(warnings))
            QMessageBox.warning(self, "警告", "\n".join(warnings))
        self._status_bar.showMessage("完了。再スキャンします...")
        self._start_scan()
