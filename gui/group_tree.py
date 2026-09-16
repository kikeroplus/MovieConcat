"""左ペインのグループツリー（未分類 / グループフォルダ / 出力動画）。"""

from __future__ import annotations

from typing import Optional

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QTreeWidget, QTreeWidgetItem, QWidget

from core.probe import OUTPUT_GROUP_NAME, UNSORTED_GROUP_NAME

_NAME_ROLE = Qt.ItemDataRole.UserRole


class GroupTree(QTreeWidget):
    group_selected = Signal(str)

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setHeaderHidden(True)
        self.currentItemChanged.connect(self._on_current_item_changed)

    def set_groups(self, groups: dict[str, list]) -> None:
        previous_name = self.current_group_name()

        self.clear()
        ordered_names = []
        if UNSORTED_GROUP_NAME in groups:
            ordered_names.append(UNSORTED_GROUP_NAME)
        for name in groups:
            if name not in (UNSORTED_GROUP_NAME, OUTPUT_GROUP_NAME):
                ordered_names.append(name)
        if OUTPUT_GROUP_NAME in groups:
            ordered_names.append(OUTPUT_GROUP_NAME)

        restore_item: Optional[QTreeWidgetItem] = None
        for name in ordered_names:
            count = len(groups[name])
            item = QTreeWidgetItem([f"{name} ({count}本)"])
            item.setData(0, _NAME_ROLE, name)
            self.addTopLevelItem(item)
            if name == previous_name:
                restore_item = item

        if restore_item is not None:
            self.setCurrentItem(restore_item)
        elif self.topLevelItemCount() > 0:
            self.setCurrentItem(self.topLevelItem(0))

    def current_group_name(self) -> Optional[str]:
        item = self.currentItem()
        if item is None:
            return None
        return item.data(0, _NAME_ROLE)

    def _on_current_item_changed(
        self, current: Optional[QTreeWidgetItem], previous: Optional[QTreeWidgetItem]
    ) -> None:
        if current is not None:
            self.group_selected.emit(current.data(0, _NAME_ROLE))
