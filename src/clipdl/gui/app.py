"""GUI の起動: python -m clipdl.gui"""
from __future__ import annotations

import sys

from PySide6.QtWidgets import QApplication, QDialog

from .. import __version__
from ..core.logging_setup import setup_logging
from ..core.settings import TERMS_VERSION, Settings
from . import theme
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
    theme.apply(app)
    if settings.terms_accepted_version != TERMS_VERSION:
        if TermsDialog().exec() != QDialog.DialogCode.Accepted:
            return 0
        settings.terms_accepted_version = TERMS_VERSION
        settings.save()
    win = MainWindow(settings)
    win.restore_geometry()
    win.show()
    win.show_startup_notices()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
