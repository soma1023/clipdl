"""見た目: 3 つのデザイン（スタジオ / ポップ / ミニマル）× ダーク / ライト。

- 色と形はここだけで決める。部品は objectName とプロパティで見た目を選び、自前で描く部品
  （範囲バー・番号・アイコン）は current() から色を読む
- ダーク / ライトは「OS に合わせる」を選ぶと、OS の切り替えにその場で追従する
- 新しいパッケージは使わず、Fusion スタイル＋パレット＋スタイルシートで描く
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import QObject, QPointF, Qt, Signal
from PySide6.QtGui import QColor, QGuiApplication, QPainter, QPalette, QPen, QPixmap, QPolygonF
from PySide6.QtWidgets import QApplication

JP_FONT = "Yu Gothic UI"
NUM_FONT = "Segoe UI Variable Display"

DESIGNS = ("studio", "pop", "minimal")
MODES = ("auto", "dark", "light")


@dataclass(frozen=True)
class Colors:
    bg: str
    card: str
    border: str
    input: str
    text: str
    muted: str
    accent: str
    accent_hi: str      # グラデーションの明るい側・ホバー
    on_accent: str
    tint: str           # 選択中の行などの薄い色（rgba）
    track: str
    ok: str
    error: str
    error_bg: str
    dark: bool


@dataclass(frozen=True)
class Design:
    key: str
    radius: int
    cards: bool          # 枠付きのカードで区切る（False なら横線だけ）
    step: str            # 番号の見せ方: "number" / "circle" / "padded"
    knob: str            # 範囲バーのつまみ: "pill" / "circle" / "bar"
    track_h: int
    row_select: str      # 選択中の範囲の行: "outline" / "leftbar" / "thinbar"
    button_full: bool    # ダウンロードボタンを横幅いっぱいにする
    gradient: bool
    colors: dict[str, Colors]


DESIGN_SPECS: dict[str, Design] = {
    # A: 落ち着いたプロ向け。グレーに青 1 色
    "studio": Design(
        "studio", radius=8, cards=True, step="number", knob="pill", track_h=8, row_select="outline",
        button_full=False, gradient=False, colors={
            "dark": Colors("#111418", "#191D23", "#2A3039", "#12161B", "#E8EAED", "#8C95A3", "#3B82F6",
                           "#5B9BFF", "#FFFFFF", "rgba(59,130,246,0.12)", "#2A313B", "#34C26B", "#FF8A84",
                           "#3A1F22", True),
            "light": Colors("#F2F4F7", "#FFFFFF", "#DCE1E8", "#F8FAFC", "#18202B", "#667085", "#2563EB",
                            "#3B7BFF", "#FFFFFF", "rgba(37,99,235,0.08)", "#E1E6ED", "#1F9D55", "#B42318",
                            "#FDECEA", False)}),
    # B: 配信者らしいポップ。紫 1 色（ボタンなどはグラデーション）
    "pop": Design(
        "pop", radius=14, cards=True, step="circle", knob="circle", track_h=12, row_select="leftbar",
        button_full=True, gradient=True, colors={
            "dark": Colors("#15121D", "#1F1A2B", "#2F2843", "#17131F", "#F3EFFB", "#A49BBE", "#8B5CF6",
                           "#B47CFF", "#FFFFFF", "rgba(139,92,246,0.16)", "#2E2741", "#3DD68C", "#FF8FB1",
                           "#3B1C2C", True),
            "light": Colors("#F6F2FD", "#FFFFFF", "#E7DEF8", "#FBF9FF", "#231A35", "#7B7095", "#7C3AED",
                            "#A066FF", "#FFFFFF", "rgba(124,58,237,0.09)", "#ECE5F8", "#16A34A", "#BE123C",
                            "#FDECF1", False)}),
    # C: ミニマル。白黒にオレンジ 1 色、枠なし
    "minimal": Design(
        "minimal", radius=4, cards=False, step="padded", knob="bar", track_h=6, row_select="thinbar",
        button_full=False, gradient=False, colors={
            "dark": Colors("#101010", "#101010", "#2A2A2A", "#181818", "#EDEDED", "#8A8A8A", "#FF6A3D",
                           "#FF8A63", "#111111", "rgba(255,106,61,0.10)", "#2C2C2C", "#4ADE80", "#FF8A84",
                           "#2A1714", True),
            "light": Colors("#FAFAF9", "#FAFAF9", "#E5E4E2", "#FFFFFF", "#1C1917", "#78716C", "#EA580C",
                            "#F97316", "#FFFFFF", "rgba(234,88,12,0.08)", "#E7E5E4", "#15803D", "#B42318",
                            "#FDECEA", False)}),
}

# アプリのアイコン（C 案に統一。どのデザインでも同じ）
ICON_BG = "#141414"
ICON_ACCENT = "#FF6A3D"


class _Notifier(QObject):
    changed = Signal()


notifier = _Notifier()
_state = {"design": "studio", "mode": "auto", "app": None}


def os_is_dark() -> bool:
    return QGuiApplication.styleHints().colorScheme() == Qt.ColorScheme.Dark


def is_dark() -> bool:
    mode = _state["mode"]
    return os_is_dark() if mode == "auto" else mode == "dark"


def current() -> tuple[Design, Colors]:
    d = DESIGN_SPECS.get(_state["design"], DESIGN_SPECS["studio"])
    return d, d.colors["dark" if is_dark() else "light"]


def accent() -> QColor:
    return QColor(current()[1].accent)


def apply(app: QApplication, design: str = "studio", mode: str = "auto") -> None:
    """デザインとダーク / ライトを決めて、アプリ全体に反映する（起動後に呼んでもその場で変わる）。"""
    _state["design"] = design if design in DESIGN_SPECS else "studio"
    _state["mode"] = mode if mode in MODES else "auto"
    if _state["app"] is None:
        _state["app"] = app
        app.setStyle("Fusion")   # 自前の色で描くため（Windows 11 スタイルはパレットを一部無視する）
        QGuiApplication.styleHints().colorSchemeChanged.connect(_on_os_scheme_changed)
    d, c = current()
    app.setPalette(palette(c))
    app.setStyleSheet(stylesheet(d, c))
    notifier.changed.emit()


def _on_os_scheme_changed(*_):
    if _state["mode"] == "auto" and _state["app"] is not None:
        apply(_state["app"], _state["design"], "auto")


def palette(c: Colors) -> QPalette:
    """Fusion が自分で描く部分（スクロールバー・メニュー・ツールチップなど）の色。"""
    p = QPalette()
    roles = {
        QPalette.ColorRole.Window: c.bg, QPalette.ColorRole.WindowText: c.text,
        QPalette.ColorRole.Base: c.input, QPalette.ColorRole.AlternateBase: c.card,
        QPalette.ColorRole.Text: c.text, QPalette.ColorRole.Button: c.card,
        QPalette.ColorRole.ButtonText: c.text, QPalette.ColorRole.Highlight: c.accent,
        QPalette.ColorRole.HighlightedText: c.on_accent, QPalette.ColorRole.PlaceholderText: c.muted,
        QPalette.ColorRole.ToolTipBase: c.card, QPalette.ColorRole.ToolTipText: c.text,
        QPalette.ColorRole.Mid: c.border, QPalette.ColorRole.Accent: c.accent,
        QPalette.ColorRole.Link: c.accent, QPalette.ColorRole.Light: c.card, QPalette.ColorRole.Dark: c.border,
    }
    for role, col in roles.items():
        p.setColor(role, QColor(col))
    for role in (QPalette.ColorRole.Text, QPalette.ColorRole.WindowText, QPalette.ColorRole.ButtonText):
        p.setColor(QPalette.ColorGroup.Disabled, role, QColor(c.muted))
    return p


# ---------------------------------------------------------------------------- スタイルシート用の小さな画像


def _asset_dir() -> Path:
    from ..core.paths import cache_dir
    d = cache_dir() / "ui"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _draw_png(name: str, size: int, draw) -> str:
    path = _asset_dir() / f"{name}.png"
    if not path.is_file():
        pm = QPixmap(size, size)
        pm.fill(Qt.GlobalColor.transparent)
        p = QPainter(pm)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        draw(p)
        p.end()
        pm.save(str(path))
    return path.as_posix()


def _arrow(color: str) -> str:
    def draw(p: QPainter):
        p.setPen(QPen(QColor(color), 1.8, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap,
                      Qt.PenJoinStyle.RoundJoin))
        p.drawPolyline(QPolygonF([QPointF(2.5, 4.5), QPointF(6, 8), QPointF(9.5, 4.5)]))
    return _draw_png(f"arrow_{color.strip('#')}", 12, draw)


def _check(color: str) -> str:
    def draw(p: QPainter):
        p.setPen(QPen(QColor(color), 2.0, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap,
                      Qt.PenJoinStyle.RoundJoin))
        p.drawPolyline(QPolygonF([QPointF(3, 7.3), QPointF(6, 10.2), QPointF(11, 4)]))
    return _draw_png(f"check_{color.strip('#')}", 14, draw)


def stylesheet(d: Design, c: Colors) -> str:
    r = d.radius
    try:
        arrow, check = _arrow(c.muted), _check(c.on_accent)
    except OSError:   # キャッシュに書けないときは、矢印とチェックだけ Fusion の標準で描く
        arrow = check = ""
    card = (f"background: {c.card}; border: 1px solid {c.border}; border-radius: {r + 2}px;" if d.cards
            else f"background: transparent; border: none; border-top: 1px solid {c.border}; border-radius: 0;")
    if d.gradient:
        primary = f"qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 {c.accent}, stop:1 {c.accent_hi})"
    else:
        primary = c.accent
    sel = {
        "outline": f"background: {c.tint}; border: 1px solid {c.accent}; border-radius: {r}px;",
        "leftbar": f"background: {c.tint}; border: none; border-left: 4px solid {c.accent}; border-radius: {r}px;",
        "thinbar": f"background: transparent; border: none; border-left: 2px solid {c.accent}; border-radius: 0;",
    }[d.row_select]
    job = (f"background: {c.input}; border: 1px solid {c.border}; border-radius: {r}px;" if d.cards
           else f"background: transparent; border: none; border-bottom: 1px solid {c.border}; border-radius: 0;")
    arrow_css = f"QComboBox::down-arrow {{ image: url({arrow}); width: 12px; height: 12px; }}" if arrow else ""
    check_css = f"image: url({check});" if check else ""
    return f"""
* {{ font-family: "{JP_FONT}"; }}
QMainWindow, QDialog, QWidget#body {{ background: {c.bg}; }}
QScrollArea {{ background: {c.bg}; border: none; }}
QLabel {{ background: transparent; color: {c.text}; }}
QFrame#header {{ background: {c.bg}; border: none; }}
QLabel#appname {{ font-family: "{NUM_FONT}"; font-size: 17px; font-weight: 700; }}
QLabel#beta {{ background: {c.tint}; color: {c.accent}; border-radius: 9px; padding: 2px 9px; font-size: 11px;
              font-weight: 600; }}
QFrame#card {{ {card} }}
QLabel#steptitle {{ font-size: 16px; font-weight: 700; }}
QLabel#sub, QLabel#hint, QLabel#meta {{ color: {c.muted}; font-size: 12px; }}
QLabel#title {{ font-size: 15px; font-weight: 700; }}
QLabel#chip {{ background: {c.track}; color: {c.muted}; border-radius: {min(r, 9)}px; padding: 1px 7px;
              font-size: 11px; font-weight: 600; }}
QLabel#thumb {{ background: {c.track}; border-radius: {r}px; }}
QLineEdit, QComboBox {{ background: {c.input}; color: {c.text}; border: 1px solid {c.border};
                       border-radius: {r}px; padding: 6px 10px; font-size: 13px;
                       selection-background-color: {c.accent}; selection-color: {c.on_accent}; }}
QLineEdit:focus, QComboBox:focus, QComboBox:on {{ border: 1px solid {c.accent}; }}
QLineEdit:disabled, QComboBox:disabled {{ color: {c.muted}; }}
QLineEdit[invalid="true"] {{ border: 1px solid {c.error}; }}
QLineEdit#time {{ font-family: "{NUM_FONT}"; font-size: 14px; padding: 4px 6px; }}
QComboBox::drop-down {{ border: none; width: 26px; }}
{arrow_css}
QComboBox QAbstractItemView {{ background: {c.card}; color: {c.text}; border: 1px solid {c.border};
                              selection-background-color: {c.tint}; selection-color: {c.text}; outline: none; }}
QCheckBox {{ font-size: 13px; spacing: 8px; color: {c.text}; background: transparent; }}
QCheckBox::indicator {{ width: 18px; height: 18px; border-radius: {min(r, 5)}px; border: 1px solid {c.border};
                       background: {c.input}; }}
QCheckBox::indicator:checked {{ background: {c.accent}; border: 1px solid {c.accent}; {check_css} }}
QPushButton {{ background: transparent; color: {c.text}; border: 1px solid {c.border}; border-radius: {r}px;
              padding: 6px 14px; font-size: 13px; }}
QPushButton:hover {{ background: {c.tint}; }}
QPushButton:disabled {{ color: {c.muted}; }}
QPushButton#ghost {{ border: 1px dashed {c.border}; color: {c.muted}; }}
QPushButton#ghost:hover {{ color: {c.text}; }}
QPushButton#primary {{ background: {primary}; color: {c.on_accent}; border: none; border-radius: {r + 2}px;
                      padding: 12px 36px; font-size: 16px; font-weight: 700; }}
QPushButton#primary:hover {{ background: {c.accent_hi}; }}
QPushButton#primary:disabled {{ background: {c.track}; color: {c.muted}; }}
QPushButton#paste {{ background: {c.accent}; color: {c.on_accent}; border: none; border-radius: {r}px;
                    padding: 9px 22px; font-size: 14px; font-weight: 700; }}
QPushButton#paste:hover {{ background: {c.accent_hi}; }}
QToolButton {{ background: transparent; color: {c.muted}; border: none; border-radius: {min(r, 8)}px;
              font-size: 13px; min-width: 28px; min-height: 28px; }}
QToolButton:hover {{ background: {c.tint}; color: {c.text}; }}
QToolButton#adv {{ padding: 4px 6px; }}
QFrame#range {{ background: transparent; border: 1px solid transparent; border-radius: {r}px; }}
QFrame#range[selected="true"] {{ {sel} }}
QLabel#idx {{ font-family: "{NUM_FONT}"; font-weight: 700; color: {c.muted}; min-width: 20px; }}
QFrame#range[selected="true"] QLabel#idx {{ color: {c.accent}; }}
QLabel#dur {{ font-family: "{NUM_FONT}"; color: {c.muted}; font-size: 13px; }}
QFrame#job {{ {job} }}
QLabel#jobtitle {{ font-size: 14px; font-weight: 700; }}
QLabel#pct {{ font-family: "{NUM_FONT}"; font-size: 18px; font-weight: 700; }}
QLabel#jobstatus {{ color: {c.muted}; font-size: 12px; }}
QLabel#jobstatus[state="done"] {{ color: {c.ok}; font-weight: 600; }}
QLabel#jobstatus[state="failed"] {{ color: {c.error}; font-weight: 600; }}
QProgressBar {{ background: {c.track}; border: none; border-radius: {d.track_h // 2}px; }}
QProgressBar::chunk {{ background: {primary}; border-radius: {d.track_h // 2}px; }}
QFrame#drop {{ background: {c.tint}; border: 2px dashed {c.accent}; border-radius: {r + 4}px; }}
QFrame#drop:focus {{ border: 2px solid {c.accent}; }}
QLabel#droptitle {{ font-size: 20px; font-weight: 700; }}
QFrame#confirm {{ background: {c.tint}; border: 1px solid {c.accent}; border-radius: {r}px; }}
QFrame#errorbar {{ background: {c.card}; border: none; border-top: 1px solid {c.border}; }}
QFrame#errorbar[info="false"] {{ background: {c.error_bg}; }}
QLabel#errtext {{ color: {c.text}; }}
QFrame#errorbar[info="false"] QLabel#errtext {{ color: {c.error}; }}
QPlainTextEdit#errdetail {{ background: {c.input}; color: {c.text}; border: 1px solid {c.border};
                           font-family: Consolas, monospace; font-size: 11px; }}
QToolTip {{ background: {c.card}; color: {c.text}; border: 1px solid {c.border}; }}
"""
