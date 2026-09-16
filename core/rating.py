"""Windows 標準の評価（System.Rating）の読み書き。

pywin32 の win32comext.propsys は、SHGetPropertyStoreFromParsingName など
IPropertyStore を返す関数全般で「登録されたインターフェースが見つからない」エラーが
常に発生し使用できなかった（pip 版 pywin32 312 系・308 系の両方で実機確認済み）。
そのため comtypes で shell32.dll の API を直接呼び出す。詳細は CLAUDE.md 7 章。
"""

from __future__ import annotations

import ctypes
import time
from ctypes import POINTER, byref, c_ulong, c_void_p, c_wchar_p
from pathlib import Path
from typing import Optional

import comtypes
from comtypes import COMMETHOD, GUID, HRESULT, IUnknown
from comtypes.automation import VARIANT, VT_UI4

# 書き込みオープンのリトライ設定。mpv の command("stop") はコアへの指示投入のみで
# 即座に戻り、ファイルの実際のクローズは少し遅れて行われることがあるため、
# 「アンロード直後の書き込み」が一時的な共有違反 (PermissionError) になることがある。
_WRITE_OPEN_RETRY_ATTEMPTS = 20
_WRITE_OPEN_RETRY_DELAY_SECONDS = 0.1


class PROPERTYKEY(ctypes.Structure):
    _fields_ = [("fmtid", GUID), ("pid", ctypes.c_ulong)]


PKEY_RATING = PROPERTYKEY(GUID("{64440492-4C8B-11D1-8B70-080036B11A03}"), 9)

GPS_DEFAULT = 0x0
GPS_READWRITE = 0x2

_STARS_TO_VALUE = {1: 1, 2: 25, 3: 50, 4: 75, 5: 99}


class IPropertyStore(IUnknown):
    _iid_ = GUID("{886D8EEB-8CF2-4446-8D02-CDBA1DBDCF99}")
    _methods_ = [
        COMMETHOD([], HRESULT, "GetCount", (["out"], POINTER(c_ulong), "cProps")),
        COMMETHOD(
            [],
            HRESULT,
            "GetAt",
            (["in"], c_ulong, "iProp"),
            (["out"], POINTER(PROPERTYKEY), "pkey"),
        ),
        COMMETHOD(
            [],
            HRESULT,
            "GetValue",
            (["in"], POINTER(PROPERTYKEY), "key"),
            (["out"], POINTER(VARIANT), "pv"),
        ),
        COMMETHOD(
            [],
            HRESULT,
            "SetValue",
            (["in"], POINTER(PROPERTYKEY), "key"),
            (["in"], POINTER(VARIANT), "propvar"),
        ),
        COMMETHOD([], HRESULT, "Commit"),
    ]


_shell32 = ctypes.OleDLL("shell32.dll")
_SHGetPropertyStoreFromParsingName = _shell32.SHGetPropertyStoreFromParsingName
_SHGetPropertyStoreFromParsingName.argtypes = [
    c_wchar_p,
    c_void_p,
    ctypes.c_ulong,
    POINTER(GUID),
    POINTER(POINTER(IPropertyStore)),
]
_SHGetPropertyStoreFromParsingName.restype = ctypes.HRESULT


class RatingError(Exception):
    """評価の読み書きに失敗したときに送出する。"""


def _ensure_com_initialized() -> None:
    try:
        comtypes.CoInitialize()
    except OSError:
        pass


def _get_property_store(path: Path, readwrite: bool = False) -> IPropertyStore:
    # restype を ctypes.HRESULT にしているため、失敗（非 S_OK）は戻り値ではなく
    # OSError（PermissionError 等のサブクラス）として送出される。
    _ensure_com_initialized()
    flags = GPS_READWRITE if readwrite else GPS_DEFAULT
    store = POINTER(IPropertyStore)()
    try:
        _SHGetPropertyStoreFromParsingName(
            str(path), None, flags, byref(IPropertyStore._iid_), byref(store)
        )
    except OSError as e:
        raise RatingError(f"プロパティストアを開けませんでした: {path}: {e}") from e
    return store


def _value_to_stars(value: Optional[int]) -> int:
    if not value:
        return 0
    if value <= 12:
        return 1
    if value <= 37:
        return 2
    if value <= 62:
        return 3
    if value <= 87:
        return 4
    return 5


def _open_for_write_with_retry(path: Path) -> IPropertyStore:
    last_error: Optional[RatingError] = None
    for attempt in range(_WRITE_OPEN_RETRY_ATTEMPTS):
        try:
            return _get_property_store(path, readwrite=True)
        except RatingError as e:
            last_error = e
            if attempt < _WRITE_OPEN_RETRY_ATTEMPTS - 1:
                time.sleep(_WRITE_OPEN_RETRY_DELAY_SECONDS)
    assert last_error is not None
    raise last_error


def read_rating(path: Path) -> int:
    """0（未評価）〜5 の★数を返す。読み取りに失敗したら RatingError。"""
    try:
        store = _get_property_store(path)
        value = store.GetValue(byref(PKEY_RATING))
        del store
    except comtypes.COMError as e:
        raise RatingError(f"評価の読み取りに失敗しました: {path}: {e}") from e
    return _value_to_stars(value)


def write_rating(path: Path, stars: int) -> None:
    """評価を書き込む。stars=0 で評価を消す（プロパティを空にする）。

    再生中のファイルには書き込めない。呼び出し側で先にプレイヤーをアンロードすること。
    """
    if stars not in (0, 1, 2, 3, 4, 5):
        raise ValueError(f"stars は 0〜5 で指定してください: {stars}")

    try:
        store = _open_for_write_with_retry(path)
        try:
            var = VARIANT()
            if stars != 0:
                var.vt = VT_UI4
                var._.VT_UI4 = _STARS_TO_VALUE[stars]
            store.SetValue(byref(PKEY_RATING), byref(var))
            store.Commit()
        finally:
            del store
    except comtypes.COMError as e:
        raise RatingError(f"評価の書き込みに失敗しました: {path}: {e}") from e
