"""mpv 埋め込み再生ウィジェット（python-mpv + libmpv-2.dll）。"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

# libmpv-2.dll はアプリフォルダ直下に配置する前提。`import mpv` の前に PATH へ追加する。
_APP_DIR = Path(__file__).resolve().parent.parent
_app_dir_str = str(_APP_DIR)
if _app_dir_str not in os.environ.get("PATH", "").split(os.pathsep):
    os.environ["PATH"] = _app_dir_str + os.pathsep + os.environ.get("PATH", "")

import mpv  # noqa: E402  (PATH 設定後に import する必要がある)
from PySide6.QtCore import Qt, Signal  # noqa: E402
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


def _format_time(seconds: float) -> str:
    total = int(seconds or 0)
    minutes, secs = divmod(total, 60)
    return f"{minutes:02d}:{secs:02d}"


class PlayerWidget(QWidget):
    """右ペインのプレイヤー。シークバー・音量つき。"""

    time_pos_changed = Signal(float)
    duration_changed = Signal(float)
    playback_finished = Signal()

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)

        self._video_frame = QWidget(self)
        self._video_frame.setAttribute(Qt.WidgetAttribute.WA_NativeWindow, True)
        self._video_frame.setStyleSheet("background-color: black;")

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

        # リレー再生用: ファイルが自然に最後まで再生された（reason == EOF）ときだけ通知する。
        # ユーザーが unload()/stop で止めた場合（reason == STOP）等は対象外。
        @self._mpv.event_callback("end-file")
        def _on_end_file(event: object) -> None:
            data = getattr(event, "data", None)
            if data is not None and getattr(data, "reason", None) == mpv.MpvEventEndFile.EOF:
                self.playback_finished.emit()

        self._end_file_callback = _on_end_file  # GC 防止のため参照を保持

        self._play_button.clicked.connect(self.toggle_pause)
        self._volume_slider.valueChanged.connect(self._on_volume_changed)
        self._seek_slider.sliderPressed.connect(self._on_seek_pressed)
        self._seek_slider.sliderReleased.connect(self._on_seek_released)
        self._loop_checkbox.toggled.connect(self._on_loop_toggled)

    def load(self, path: Path) -> None:
        self._current_path = path
        self._duration = 0.0
        self._mpv.play(str(path))
        self._mpv.pause = False

    def unload(self) -> None:
        if self._current_path is None:
            return
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

    def _on_volume_changed(self, value: int) -> None:
        self._mpv.volume = value

    def _on_loop_toggled(self, checked: bool) -> None:
        self._mpv.loop_file = "inf" if checked else "no"

    def set_relay_mode(self, active: bool) -> None:
        """リレー再生中は 1 本ずつの自動ループを止め、次の動画へ進めるようにする。

        ループ再生チェックボックスは操作できないようにし（見た目も無効化）、
        リレー終了時はチェックボックスの状態に応じたループ設定へ戻す。
        """
        self._loop_checkbox.setEnabled(not active)
        if active:
            self._mpv.loop_file = "no"
        else:
            self._mpv.loop_file = "inf" if self._loop_checkbox.isChecked() else "no"

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
