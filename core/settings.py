"""アプリ設定の保存・復元。

`%LOCALAPPDATA%\\MovieManager\\settings.json` に保存する。
最後に開いたフォルダ、結合順、エンコーダ設定、ウィンドウ配置を保持する。
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Optional

SETTINGS_DIR_NAME = "MovieManager"
SETTINGS_FILENAME = "settings.json"

DEFAULT_SORT_MODE = "name"
DEFAULT_ENCODER = "libx264"
DEFAULT_CRF = 20


def app_data_dir() -> Path:
    base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
    return Path(base) / SETTINGS_DIR_NAME


def _settings_path() -> Path:
    return app_data_dir() / SETTINGS_FILENAME


class Settings:
    def __init__(self) -> None:
        self.last_root: Optional[str] = None
        self.sort_mode: str = DEFAULT_SORT_MODE
        self.encoder: str = DEFAULT_ENCODER
        self.crf: int = DEFAULT_CRF
        self.window_geometry: Optional[str] = None
        self.window_state: Optional[str] = None
        self._load()

    def _load(self) -> None:
        path = _settings_path()
        if not path.exists():
            return
        try:
            data: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return
        self.last_root = data.get("last_root")
        self.sort_mode = data.get("sort_mode", DEFAULT_SORT_MODE)
        self.encoder = data.get("encoder", DEFAULT_ENCODER)
        self.crf = data.get("crf", DEFAULT_CRF)
        self.window_geometry = data.get("window_geometry")
        self.window_state = data.get("window_state")

    def save(self) -> None:
        data = {
            "last_root": self.last_root,
            "sort_mode": self.sort_mode,
            "encoder": self.encoder,
            "crf": self.crf,
            "window_geometry": self.window_geometry,
            "window_state": self.window_state,
        }
        directory = app_data_dir()
        directory.mkdir(parents=True, exist_ok=True)
        path = _settings_path()
        tmp_path = path.with_name(path.name + ".tmp")
        tmp_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp_path.replace(path)
