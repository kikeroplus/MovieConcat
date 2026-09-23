"""mpv 埋め込み再生ウィジェット（python-mpv + libmpv-2.dll）。"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

from core.appdir import get_app_dir

# libmpv-2.dll はアプリフォルダ直下に配置する前提。`import mpv` の前に PATH へ追加する。
_APP_DIR = get_app_dir()
_app_dir_str = str(_APP_DIR)
if _app_dir_str not in os.environ.get("PATH", "").split(os.pathsep):
    os.environ["PATH"] = _app_dir_str + os.pathsep + os.environ.get("PATH", "")

import mpv  # noqa: E402  (PATH 設定後に import する必要がある)
from PySide6.QtCore import QEvent, Qt, Signal  # noqa: E402
from PySide6.QtWidgets import (  # noqa: E402
    QCheckBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSlider,
    QVBoxLayout,
    QWidget,
)

_SEEK_SLIDER_RESOLUTION = 1000
_WHEEL_SEEK_SECONDS = 5.0


def _format_time(seconds: float) -> str:
    total = int(seconds or 0)
    minutes, secs = divmod(total, 60)
    return f"{minutes:02d}:{secs:02d}"


class PlayerWidget(QWidget):
    """右ペインのプレイヤー。シークバー・音量つき。"""

    time_pos_changed = Signal(float)
    duration_changed = Signal(float)
    playlist_index_changed = Signal(int)
    playlist_finished = Signal()
    _playlist_pos_raw_changed = Signal(object)

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)

        self._video_frame = QWidget(self)
        self._video_frame.setAttribute(Qt.WidgetAttribute.WA_NativeWindow, True)
        self._video_frame.setStyleSheet("background-color: black;")
        # 動画窓の上でホイールを回すとシークする（上へ回す=巻き戻し、下へ回す=早送り）。
        self._video_frame.installEventFilter(self)

        self._play_button = QPushButton("再生 / 一時停止")
        self._seek_slider = QSlider(Qt.Orientation.Horizontal)
        self._seek_slider.setRange(0, _SEEK_SLIDER_RESOLUTION)
        self._time_label = QLabel("00:00 / 00:00")
        self._volume_slider = QSlider(Qt.Orientation.Horizontal)
        self._volume_slider.setRange(0, 100)
        self._volume_slider.setValue(100)
        self._volume_slider.setMaximumWidth(120)
        self._loop_checkbox = QCheckBox("ループ再生")
        self._loop_checkbox.setChecked(True)

        controls = QHBoxLayout()
        controls.addWidget(self._play_button)
        controls.addWidget(self._seek_slider, 1)
        controls.addWidget(self._time_label)
        controls.addWidget(QLabel("音量"))
        controls.addWidget(self._volume_slider)
        controls.addWidget(self._loop_checkbox)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self._video_frame, 1)
        layout.addLayout(controls)

        self._duration = 0.0
        self._current_path: Optional[Path] = None
        self._seek_slider_pressed = False

        # リレー再生（プレイリスト）用の状態。
        self._playlist_paths: list[Path] = []
        self._playlist_running = False  # load_playlist() 中かどうか
        self._playlist_started = False  # 実際に再生が始まった（pos >= 0 を一度でも観測した）か

        self._mpv = mpv.MPV(
            wid=str(int(self._video_frame.winId())),
            input_default_bindings=False,
            input_vo_keyboard=False,
            osc=False,
        )
        self._mpv.volume = self._volume_slider.value()
        self._mpv.loop_file = "inf" if self._loop_checkbox.isChecked() else "no"

        # mpv のプロパティ監視コールバックは mpv 側のスレッドから呼ばれるため、
        # 直接ウィジェットを操作せず Signal 経由で GUI スレッドへ橋渡しする。
        # time-pos はファイル終端（ループ再生 OFF で再生が終わったとき等）で None に
        # なることがある。ここで 0.0 に丸めるとシークバーが 00:00 に巻き戻って見えて
        # しまうため、None のときは何もせず直前の表示を保持する。
        self._mpv.observe_property(
            "time-pos",
            lambda name, value: self.time_pos_changed.emit(value)
            if value is not None
            else None,
        )
        self._mpv.observe_property(
            "duration", lambda name, value: self.duration_changed.emit(value or 0.0)
        )
        self.time_pos_changed.connect(self._on_time_pos)
        self.duration_changed.connect(self._on_duration)

        # リレー再生（プレイリスト）用: mpv 自身のプレイリスト機能で次の動画へ自動的に
        # 進ませる（Python 側で毎回 load() し直すと、EOF 検知～再読み込みの往復の間に
        # 一瞬映像が途切れやすいため）。playlist-pos の変化を Signal 経由で橋渡しする。
        self._mpv.observe_property(
            "playlist-pos",
            lambda name, value: self._playlist_pos_raw_changed.emit(value),
        )
        self._playlist_pos_raw_changed.connect(self._on_playlist_pos_changed)

        self._play_button.clicked.connect(self.toggle_pause)
        self._volume_slider.valueChanged.connect(self._on_volume_changed)
        self._seek_slider.sliderPressed.connect(self._on_seek_pressed)
        self._seek_slider.sliderReleased.connect(self._on_seek_released)
        self._loop_checkbox.toggled.connect(self._on_loop_toggled)

    def load(self, path: Path) -> None:
        """通常の単発再生（テーブルの行選択など）。プレイリスト状態は解除する。"""
        self._playlist_running = False
        self._playlist_paths = []
        self._current_path = path
        self._duration = 0.0
        self._mpv.play(str(path))
        self._mpv.pause = False

    def unload(self) -> None:
        if self._current_path is None and not self._playlist_running:
            return
        self._playlist_running = False
        self._playlist_paths = []
        self._current_path = None
        self._duration = 0.0
        self._mpv.command("stop")
        # command("stop") はコアへの指示投入のみで即座に戻り、ファイルの実際のクローズは
        # 少し遅れて行われる。ここで完了を待たないと、直後にプロパティストアを
        # 書き込みで開いたときに PermissionError になる（他プロセスが使用中）ことがある。
        try:
            self._mpv.wait_for_property("core-idle", timeout=2.0)
        except Exception:
            pass

    def load_playlist(self, paths: list[Path], loop: bool) -> None:
        """リレー再生用: mpv 自身のプレイリストに全曲を積み、内部で自動的に
        次の動画へ進ませる（Python 側で毎回読み込み直すより切り替えが速く、
        映像が途切れにくい）。

        単体の「ループ再生」チェックボックスは無効化し、個々の動画が
        ループして次へ進めなくなるのを防ぐ（プレイリスト全体のループは
        loop 引数 / set_playlist_loop() で別途制御する）。
        """
        self._loop_checkbox.setEnabled(False)
        self._mpv.loop_file = "no"

        # プレイリストの 1 本目が、直前まで通常再生（テーブル行選択）していたのと
        # 同じファイルだと、mpv 内部でそのまま再生が継続してしまい playlist-pos の
        # "0" への変化が観測できない（再生位置も先頭に戻らない）ことがある。
        # 一旦完全に停止してから組み直すことで、必ず新規に 0 番から始まるようにする。
        self._mpv.command("stop")
        try:
            self._mpv.wait_for_property("core-idle", timeout=1.0)
        except Exception:
            pass

        self._playlist_paths = list(paths)
        self._playlist_running = True
        self._playlist_started = False
        self._duration = 0.0
        self._current_path = paths[0] if paths else None

        self._mpv.loop_playlist = "inf" if loop else "no"
        self._mpv.playlist_clear()
        if not paths:
            return
        self._mpv.loadfile(str(paths[0]), "replace")
        for path in paths[1:]:
            self._mpv.loadfile(str(path), "append")
        self._mpv.pause = False

    def set_playlist_loop(self, loop: bool) -> None:
        if self._playlist_running:
            self._mpv.loop_playlist = "inf" if loop else "no"

    def jump_to_playlist_index(self, index: int) -> None:
        if not self._playlist_running or not (0 <= index < len(self._playlist_paths)):
            return
        self._mpv.playlist_play_index(index)

    def delete_current_playlist_item(
        self, expected_path: Path
    ) -> tuple[Optional[Path], Optional[int]]:
        """リレー再生中、今再生中の項目をプレイリストから取り除く。

        mpv の `playlist-remove current` を使う。`load_playlist()`（stop して
        プレイリスト全体を作り直す方式）を稼働中の MPV インスタンスに対して短時間に
        繰り返し発行すると、python-mpv の ctypes 層でまれにネイティブのアクセス
        違反（クラッシュ）を起こすことを実機検証で確認したため、削除のたびに
        `load_playlist()` を呼び直す方式は採用していない（`_begin_relay_sequence`
        による作り直し自体は「フォルダ切り替え」等、頻度が低い操作向けとして残す）。
        `playlist-remove` は単発の軽い命令のため、繰り返し発行してもクラッシュしない
        ことを確認済み。

        ただし `playlist-remove current` で末尾以外の項目を消した場合、削除後に
        繰り上がった項目がそのまま同じ playlist-pos 番号で再生を続けるため、
        mpv 側の playlist-pos の値自体は変化しない（＝ observe_property の
        コールバックが発火せず、`_on_playlist_pos_changed` 経由の自動更新が
        効かない）。そのため呼び出し側の状態は、このメソッドの戻り値を使って
        自前で更新すること。末尾の項目を削除した場合は playlist-pos が実際に
        変化する（ループ ON なら 0 へ折り返し、OFF なら -1 になる）ため、
        既存の observe_property の経路がそのまま処理する。

        Returns:
            (削除した項目のパス, 削除後にそこへ繰り上がった項目の新しいインデックス)。
            末尾の項目を削除した場合、2 番目の要素は None
            （呼び出し側は何もせず既存の playlist-pos 監視に任せてよい）。
            今再生中の項目が `expected_path` と一致しない場合（呼び出し側の状態と
            食い違っている場合）は `(None, None)`。
        """
        if not self._playlist_running or self._current_path != expected_path:
            return None, None
        try:
            removed_index = self._playlist_paths.index(expected_path)
        except ValueError:
            return None, None

        was_last = removed_index == len(self._playlist_paths) - 1
        self._mpv.command("playlist-remove", "current")
        del self._playlist_paths[removed_index]

        if was_last:
            return expected_path, None

        self._current_path = self._playlist_paths[removed_index]
        return expected_path, removed_index

    def stop_playlist(self) -> None:
        """リレー再生の終了。単発再生用のループ設定に戻す。"""
        self._playlist_running = False
        self._playlist_paths = []
        self.unload()
        try:
            self._mpv.playlist_clear()
        except Exception:
            pass
        self._loop_checkbox.setEnabled(True)
        self._mpv.loop_file = "inf" if self._loop_checkbox.isChecked() else "no"

    def current_path(self) -> Optional[Path]:
        return self._current_path

    def toggle_pause(self) -> None:
        if self._current_path is None:
            return
        self._mpv.pause = not self._mpv.pause

    def seek_relative(self, seconds: float) -> None:
        if self._current_path is None:
            return
        self._mpv.command("seek", str(seconds), "relative")

    def shutdown(self) -> None:
        self._mpv.terminate()

    def eventFilter(self, obj, event) -> bool:  # noqa: N802 (Qt overrideの命名規則)
        if obj is self._video_frame:
            if event.type() == QEvent.Type.Wheel:
                delta = event.angleDelta().y()
                if delta > 0:
                    self.seek_relative(-_WHEEL_SEEK_SECONDS)
                elif delta < 0:
                    self.seek_relative(_WHEEL_SEEK_SECONDS)
                return True
            if (
                event.type() == QEvent.Type.MouseButtonRelease
                and event.button() == Qt.MouseButton.LeftButton
            ):
                # 動画窓を左クリックすると再生/一時停止（通常再生・リレー再生共通）。
                self.toggle_pause()
                return True
        return super().eventFilter(obj, event)

    def _on_volume_changed(self, value: int) -> None:
        self._mpv.volume = value

    def _on_loop_toggled(self, checked: bool) -> None:
        if not self._playlist_running:
            self._mpv.loop_file = "inf" if checked else "no"

    def _on_playlist_pos_changed(self, value: object) -> None:
        index = value if isinstance(value, int) else -1
        if index >= 0:
            self._playlist_started = True
            if 0 <= index < len(self._playlist_paths):
                self._current_path = self._playlist_paths[index]
            self.playlist_index_changed.emit(index)
        elif self._playlist_running and self._playlist_started:
            # 末尾まで到達しループもしないため mpv がアイドルに戻った
            # （ユーザーの手動停止は unload()/stop_playlist() 側で先に
            # _playlist_running を False にしているのでここには来ない）。
            self._playlist_started = False
            self._current_path = None
            self.playlist_finished.emit()

    def _on_seek_pressed(self) -> None:
        self._seek_slider_pressed = True

    def _on_seek_released(self) -> None:
        self._seek_slider_pressed = False
        if self._duration > 0:
            fraction = self._seek_slider.value() / _SEEK_SLIDER_RESOLUTION
            self._mpv.seek(fraction * self._duration, reference="absolute")

    def _on_time_pos(self, value: float) -> None:
        if not self._seek_slider_pressed and self._duration > 0:
            self._seek_slider.blockSignals(True)
            self._seek_slider.setValue(
                int(value / self._duration * _SEEK_SLIDER_RESOLUTION)
            )
            self._seek_slider.blockSignals(False)
        self._time_label.setText(f"{_format_time(value)} / {_format_time(self._duration)}")

    def _on_duration(self, value: float) -> None:
        self._duration = value
