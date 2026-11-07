"""GUI の部品のテスト（画面は出さない: offscreen）。"""
import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
QtWidgets = pytest.importorskip("PySide6.QtWidgets")

from clipdl.gui.main_window import split_urls  # noqa: E402
from clipdl.gui.range_bar import MIN_GAP_PX, RangeBar  # noqa: E402


@pytest.fixture(scope="module")
def app():
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


def test_split_urls_handles_newlines_spaces_and_joined():
    text = "https://a.com/x https://b.com/y\nhttps://c.com/zhttps://d.com/w, ほか"
    assert split_urls(text) == ["https://a.com/x", "https://b.com/y", "https://c.com/z", "https://d.com/w"]
    assert split_urls("") == [] and split_urls("not a url") == []


def test_range_bar_keeps_min_gap_for_short_ranges(app):
    bar = RangeBar()
    bar.resize(700, 40)
    bar.set_duration(6 * 3600)
    bar.set_ranges([(4200, 4220), (0, 3600)], 0)
    xs, xe = bar.knob_positions(0)          # 20 秒 / 6 時間 は 1px 未満だが、つまみは重ならない
    assert xe - xs >= MIN_GAP_PX - 1e-6
    xs2, xe2 = bar.knob_positions(1)
    assert xe2 - xs2 > 100                  # 長い範囲は実際の長さで表示


def test_range_bar_drag_respects_min_duration_and_emits(app):
    bar = RangeBar()
    bar.resize(700, 40)
    bar.set_duration(100)
    bar.set_ranges([(10, 20)], 0)
    got = []
    bar.rangeChanged.connect(lambda i, s, e: got.append((i, s, e)))
    from clipdl.gui.range_bar import _Knob
    bar._move_knob(_Knob(0, "start"), 50)   # 終了を越えようとしても、終了 - 最短 で止まる
    assert got[-1] == (0, 19.5, 20)
    bar._move_knob(_Knob(0, "end"), 500)    # 動画の長さを越えない
    assert got[-1] == (0, 19.5, 100)


def test_last_touched_knob_is_drawn_on_top(app):
    from clipdl.gui.range_bar import _Knob
    bar = RangeBar()
    bar.resize(700, 40)
    bar.set_duration(1000)
    bar.set_ranges([(100, 101), (101, 102)], 1)
    bar._last = _Knob(0, "end")
    order = [(k.index, k.side) for k in bar._knobs_in_z_order()]
    assert order[-1] == (1, "end") or order[-1] == (0, "end")
    bar.selected = 0
    assert [(k.index, k.side) for k in bar._knobs_in_z_order()][-1] == (0, "end")


def test_window_title_shows_version_and_beta():
    from clipdl import __version__
    from clipdl.gui import i18n
    try:
        i18n.set_language("ja")
        assert i18n.tr("app.title", version=__version__) == f"clipdl {__version__}（ベータ版）"
        i18n.set_language("en")
        assert "(beta)" in i18n.tr("app.title", version=__version__)
    finally:
        i18n.set_language("ja")
