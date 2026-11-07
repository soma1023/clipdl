"""署名のパスフレーズを Windows の DPAPI で暗号化して保存する（リリースのたびに入力しなくて済むように）。

- 暗号化の鍵は Windows が管理し、**この PC のこのユーザーアカウント**でしか元に戻せない
  （ファイルだけ別の PC や別のユーザーにコピーしても読めない）
- 保存先: %LOCALAPPDATA%\\clipdl-signing\\passphrase.dpapi（OneDrive・リポジトリの外。読めるのは自分だけ）
- 注意: このユーザーとしてログインできる人（や、このユーザーの権限で動くプログラム）は署名できるようになる
"""
from __future__ import annotations

import ctypes
import getpass
import os
import subprocess
from ctypes import wintypes
from pathlib import Path

STORE = Path(os.environ.get("LOCALAPPDATA", str(Path.home() / "AppData" / "Local"))) / "clipdl-signing" / \
    "passphrase.dpapi"
_ENTROPY = b"clipdl release signing passphrase v1"   # 他のアプリが同じ仕組みで保存したものと区別する
_CRYPTPROTECT_UI_FORBIDDEN = 0x1


class _Blob(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]


def _blob(data: bytes) -> _Blob:
    buf = ctypes.create_string_buffer(data, len(data))
    return _Blob(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char)))


def _call(fn, data: bytes) -> bytes:
    src, ent, out = _blob(data), _blob(_ENTROPY), _Blob()
    if not fn(ctypes.byref(src), None, ctypes.byref(ent), None, None, _CRYPTPROTECT_UI_FORBIDDEN, ctypes.byref(out)):
        raise OSError(ctypes.get_last_error() or "DPAPI の処理に失敗しました")
    try:
        return ctypes.string_at(out.pbData, out.cbData)
    finally:
        ctypes.windll.kernel32.LocalFree(out.pbData)


def protect(data: bytes) -> bytes:
    return _call(ctypes.WinDLL("crypt32", use_last_error=True).CryptProtectData, data)


def unprotect(data: bytes) -> bytes:
    return _call(ctypes.WinDLL("crypt32", use_last_error=True).CryptUnprotectData, data)


def load() -> str | None:
    """保存したパスフレーズ（無い・読めないときは None）。"""
    if not STORE.is_file():
        return None
    try:
        return unprotect(STORE.read_bytes()).decode("utf-8")
    except (OSError, UnicodeDecodeError):
        return None


def save(passphrase: str) -> Path:
    STORE.parent.mkdir(parents=True, exist_ok=True)
    STORE.write_bytes(protect(passphrase.encode("utf-8")))
    user = getpass.getuser()
    for target in (STORE.parent, STORE):   # 読めるのを自分のアカウントだけにする
        subprocess.run(["icacls", str(target), "/inheritance:r", "/grant:r", f"{user}:(F)"],
                       check=False, capture_output=True)
    return STORE


def forget() -> bool:
    """保存したパスフレーズを消す（消したら True）。"""
    if STORE.is_file():
        STORE.unlink()
        return True
    return False
