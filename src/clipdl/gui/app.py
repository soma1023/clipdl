"""GUI の起動: python -m clipdl.gui"""
from __future__ import annotations

import logging
import sys

from PySide6.QtWidgets import QApplication, QDialog

from .. import __version__
from ..core.logging_setup import setup_logging
from ..core.settings import TERMS_VERSION, Settings
from . import art, theme
from .i18n import set_language
from .main_window import MainWindow
from .widgets import TermsDialog



def main(argv: list[str] | None = None) -> int:
    setup_logging()  # 通常は __main__ の startup() で設定済み
    app = QApplication(argv if argv is not None else sys.argv)
    app.setApplicationName("clipdl")
    app.setApplicationVersion(__version__)
    settings = Settings.load()
    set_language(settings.language)
    theme.apply(app, settings.ui_design, settings.ui_mode)
    app.setWindowIcon(art.window_icon())
    # 更新直後なら「起動できた」印をすぐ書く（利用規約の再同意を待つと、入れ替え用プログラムが失敗と判断するため）
    from ..core import selfupdate
    try:
        after_update = selfupdate.after_startup()
    except Exception:  # noqa: BLE001 — 更新の後片付けで起動を止めない
        logging.getLogger(__name__).exception("更新の結果を確認できませんでした")
        after_update = None
    if settings.terms_accepted_version != TERMS_VERSION:
        if TermsDialog().exec() != QDialog.DialogCode.Accepted:
            return 0
        settings.terms_accepted_version = TERMS_VERSION
        settings.save()
    win = MainWindow(settings)
    win.restore_geometry()
    win.show()
    win.show_startup_notices(after_update)
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
