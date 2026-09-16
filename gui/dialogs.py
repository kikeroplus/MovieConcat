"""確認ダイアログ類。"""

from __future__ import annotations

from typing import Optional

from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QMessageBox,
    QSpinBox,
    QWidget,
)

from core import concat
from core.settings import Settings


def confirm_grouping(
    parent: QWidget, counts: dict[str, int], skip_reasons: list[str], is_partial: bool
) -> bool:
    """フォルダ分け実行前の確認ダイアログ。OK なら True。

    is_partial: チェックされた行だけを対象にした場合 True（対象範囲を明示する）。
    """
    scope_note = (
        "（表示中のグループでチェックした動画のみが対象です）"
        if is_partial
        else "（ルート配下全体が対象です）"
    )

    if not counts:
        if skip_reasons:
            QMessageBox.information(
                parent,
                "フォルダ分け",
                f"移動対象はありませんでした{scope_note}。\n\n以下はスキップされました:\n"
                + "\n".join(skip_reasons),
            )
        else:
            QMessageBox.information(
                parent, "フォルダ分け", f"すべて整理済みです{scope_note}。移動対象はありません。"
            )
        return False

    lines = [f"{name}: {count}本" for name, count in sorted(counts.items())]
    text = f"以下の内容でフォルダ分けを実行します{scope_note}。\n\n" + "\n".join(lines)
    if skip_reasons:
        text += "\n\n以下はスキップされます:\n" + "\n".join(skip_reasons)

    box = QMessageBox(parent)
    box.setWindowTitle("フォルダ分けの確認")
    box.setIcon(QMessageBox.Icon.Question)
    box.setText(text)
    box.setStandardButtons(
        QMessageBox.StandardButton.Ok | QMessageBox.StandardButton.Cancel
    )
    box.setDefaultButton(QMessageBox.StandardButton.Cancel)
    return box.exec() == QMessageBox.StandardButton.Ok


def _format_duration(seconds: float) -> str:
    total = int(seconds or 0)
    hours, remainder = divmod(total, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes:02d}:{secs:02d}"


def confirm_video_mismatch_reencode(parent: QWidget, group_name: str) -> bool:
    """判定 C（映像不一致）のグループごとに再エンコードするか確認する。"""
    result = QMessageBox.question(
        parent,
        "映像が一致しません",
        f"「{group_name}」内の動画は映像（コーデック・解像度・フレームレート等）が"
        "一致していません。\n再エンコードして結合しますか？\n"
        "（「いいえ」を選ぶとこのグループはスキップされます）",
        QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        QMessageBox.StandardButton.No,
    )
    return result == QMessageBox.StandardButton.Yes


def confirm_concat_plan(
    parent: QWidget, plan: "concat.ConcatPlan", is_partial: bool = False
) -> bool:
    """結合実行前の最終確認ダイアログ（本数・合計時間・判定結果）。OK なら True。

    is_partial: チェックされた動画だけを対象にした場合 True（対象範囲を明示する）。
    """
    scope_note = (
        "（表示中のグループでチェックした動画のみが対象です）" if is_partial else ""
    )

    if not plan.jobs:
        if plan.skipped:
            reasons = "\n".join(f"{name}: {reason}" for name, reason in plan.skipped)
            QMessageBox.information(
                parent, "結合", f"結合対象がありませんでした{scope_note}。\n\n{reasons}"
            )
        else:
            QMessageBox.information(parent, "結合", f"結合対象がありません{scope_note}。")
        return False

    lines = []
    for job in sorted(plan.jobs, key=lambda j: j.group_name):
        lines.append(
            f"{job.group_name}: {len(job.videos)}本 / "
            f"合計{_format_duration(job.total_duration)} / {job.decision.label}"
        )
    text = f"以下の内容で結合を実行します{scope_note}。\n\n" + "\n".join(lines)
    if plan.skipped:
        text += "\n\nスキップ:\n" + "\n".join(
            f"{name}: {reason}" for name, reason in plan.skipped
        )

    box = QMessageBox(parent)
    box.setWindowTitle("結合の確認")
    box.setIcon(QMessageBox.Icon.Question)
    box.setText(text)
    box.setStandardButtons(
        QMessageBox.StandardButton.Ok | QMessageBox.StandardButton.Cancel
    )
    box.setDefaultButton(QMessageBox.StandardButton.Cancel)
    return box.exec() == QMessageBox.StandardButton.Ok


def confirm_trash(parent: QWidget, count: int) -> bool:
    """ごみ箱へ移動する前の確認ダイアログ。OK なら True。"""
    result = QMessageBox.question(
        parent,
        "ごみ箱へ移動",
        f"{count} 本の動画をごみ箱へ移動します。よろしいですか？\n\n"
        "（ごみ箱からの復元は手動で行ってください。アプリの「元に戻す」の対象にはなりません）",
        QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        QMessageBox.StandardButton.No,
    )
    return result == QMessageBox.StandardButton.Yes


def choose_move_or_copy(parent: QWidget, count: int) -> Optional[str]:
    """「別フォルダへまとめる」の移動/コピー選択。"move"/"copy"/None（キャンセル）を返す。"""
    box = QMessageBox(parent)
    box.setWindowTitle("別フォルダへまとめる")
    box.setIcon(QMessageBox.Icon.Question)
    box.setText(f"{count} 本の動画を選択したフォルダへ移動しますか、コピーしますか？")
    move_button = box.addButton("移動", QMessageBox.ButtonRole.AcceptRole)
    copy_button = box.addButton("コピー", QMessageBox.ButtonRole.AcceptRole)
    box.addButton("キャンセル", QMessageBox.ButtonRole.RejectRole)
    box.setDefaultButton(move_button)
    box.exec()

    clicked = box.clickedButton()
    if clicked is move_button:
        return "move"
    if clicked is copy_button:
        return "copy"
    return None


def show_missing_requirements(parent: Optional[QWidget], missing: list[str]) -> None:
    """ffmpeg / ffprobe / libmpv-2.dll が見つからないときの案内ダイアログ。"""
    QMessageBox.warning(
        parent,
        "必要なファイルが見つかりません",
        "以下が見つからないため、一部の機能が使えません。\n\n"
        + "\n".join(f"・{m}" for m in missing)
        + "\n\nffmpeg / ffprobe は Windows の PATH に追加してください。\n"
        "libmpv-2.dll はアプリの実行フォルダ（main.py と同じ場所）に配置してください。",
    )


def show_libmpv_missing_and_exit(parent: Optional[QWidget]) -> None:
    """libmpv-2.dll が無く再生機能を起動できない致命的なケース。"""
    QMessageBox.critical(
        parent,
        "libmpv-2.dll が見つかりません",
        "動画再生に必要な libmpv-2.dll が見つからないため、アプリを起動できません。\n\n"
        "libmpv-2.dll をアプリの実行フォルダ（main.py と同じ場所）に配置してから、"
        "もう一度起動してください。",
    )


class SettingsDialog(QDialog):
    """エンコーダ・品質（CRF）の設定ダイアログ。"""

    _ENCODERS = [("libx264 (CPU)", "libx264"), ("NVENC (NVIDIA GPU)", "nvenc")]

    def __init__(self, parent: Optional[QWidget], settings: Settings) -> None:
        super().__init__(parent)
        self.setWindowTitle("設定")
        self._settings = settings

        self._encoder_combo = QComboBox()
        for label, value in self._ENCODERS:
            self._encoder_combo.addItem(label, value)
        index = self._encoder_combo.findData(settings.encoder)
        self._encoder_combo.setCurrentIndex(max(index, 0))

        self._crf_spin = QSpinBox()
        self._crf_spin.setRange(0, 51)
        self._crf_spin.setValue(settings.crf)
        self._crf_spin.setToolTip("小さいほど高画質・大きいファイル（再エンコード時のみ使用）")

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)

        layout = QFormLayout(self)
        layout.addRow("再エンコード時のエンコーダ:", self._encoder_combo)
        layout.addRow("品質 (CRF):", self._crf_spin)
        layout.addRow(buttons)

    def apply_to(self, settings: Settings) -> None:
        settings.encoder = self._encoder_combo.currentData()
        settings.crf = self._crf_spin.value()


def show_settings_dialog(parent: QWidget, settings: Settings) -> bool:
    """設定ダイアログを開く。OK なら settings を更新して True を返す。"""
    dialog = SettingsDialog(parent, settings)
    if dialog.exec() == QDialog.DialogCode.Accepted:
        dialog.apply_to(settings)
        return True
    return False
