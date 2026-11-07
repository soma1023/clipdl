"""見た目: OS のダーク / ライトに追従し、装飾は最小限にする。"""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QGuiApplication, QPalette
from PySide6.QtWidgets import QApplication, QStyleFactory


def is_dark() -> bool:
    return QGuiApplication.styleHints().colorScheme() == Qt.ColorScheme.Dark


def accent() -> QColor:
    return QApplication.palette().color(QPalette.ColorRole.Accent)


def apply(app: QApplication) -> None:
    """Qt 6 の Windows 11 スタイルは OS の配色に追従する。使えない環境では Fusion にする。"""
    if "windows11" in [s.lower() for s in QStyleFactory.keys()]:
        app.setStyle("windows11")
    else:
        app.setStyle("Fusion")
    app.setStyleSheet(STYLESHEET)


# 色はパレット（OS 追従）から取り、ここでは余白・角丸・補助的な色だけを決める
STYLESHEET = """
QFrame#card { border: 1px solid palette(mid); border-radius: 6px; }
QLabel#sub { color: palette(placeholder-text); font-size: 11px; }
QLabel#step { min-width: 18px; max-width: 18px; min-height: 18px; max-height: 18px; border-radius: 9px;
              background: palette(accent); color: palette(highlighted-text); qproperty-alignment: AlignCenter;
              font-size: 11px; }
QLabel#title { font-size: 14px; }
QPushButton#primary { background: palette(accent); color: palette(highlighted-text); border: none;
                      border-radius: 4px; padding: 6px 26px; font-size: 14px; }
QPushButton#primary:hover { background: palette(highlight); }
QFrame#confirm { background: palette(highlight); border-radius: 4px; }
QFrame#confirm QLabel { color: palette(highlighted-text); }
QFrame#errorbar { background: #3d1f1f; }
QFrame#errorbar[light="true"] { background: #fde7e9; }
QLabel#errtext { color: #ff9a94; }
QLabel#errtext[light="true"] { color: #b3261e; }
QPlainTextEdit#errdetail { font-family: Consolas, monospace; font-size: 11px; }
QLineEdit[invalid="true"] { border: 1px solid #d13438; }
"""
