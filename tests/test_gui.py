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


def test_message_bar_shows_several_actions_and_clears_after_click(app):
    from clipdl.gui.widgets import MessageBar
    bar = MessageBar()
    clicked = []
    bar.show_info("新しい版 v9 があります", actions=[("更新する", lambda: clicked.append("u")),
                                                  ("ダウンロードページを開く", lambda: clicked.append("o"))])
    assert [b.text() for b in bar.action_buttons] == ["更新する", "ダウンロードページを開く"]
    bar.action_buttons[1].click()
    assert clicked == ["o"] and bar.action_buttons == []
    bar.show_info("x", "ボタン", lambda: clicked.append("single"))   # 従来の 1 つだけの形も使える
    bar.action_btn.click()
    assert clicked[-1] == "single"
    bar.show_error("err")
    assert bar.action_buttons == []


# ---------------------------------------------------------------------------- 見た目（デザインと明るさ）


@pytest.fixture
def window(app, tmp_path, monkeypatch):
    for k in ("CLIPDL_CONFIG_DIR", "CLIPDL_CACHE_DIR", "CLIPDL_DATA_DIR", "CLIPDL_LOG_DIR"):
        monkeypatch.setenv(k, str(tmp_path / k))
    from clipdl.core.settings import Settings
    from clipdl.gui import theme
    from clipdl.gui.main_window import MainWindow
    s = Settings()
    theme.apply(app, s.ui_design, s.ui_mode)
    w = MainWindow(s)
    w._fetch_timer.timeout.disconnect()   # 動画情報を取りに行かない
    yield w
    w.bridge.shutdown()
    w.deleteLater()
    theme.apply(app, "studio", "auto")


@pytest.mark.parametrize("design", ["studio", "pop", "minimal"])
@pytest.mark.parametrize("mode", ["dark", "light"])
def test_every_design_and_mode_builds_a_stylesheet(app, design, mode):
    from clipdl.gui import theme
    theme.apply(app, design, mode)
    d, c = theme.current()
    assert d.key == design and c.dark == (mode == "dark") and theme.is_dark() == (mode == "dark")
    css = app.styleSheet()
    assert c.accent in css and "QPushButton#primary" in css and "QFrame#drop" in css
    assert app.palette().color(app.palette().ColorRole.Window).name().upper() == c.bg.upper()
    theme.apply(app, "studio", "auto")


def test_unknown_design_falls_back_and_auto_follows_os(app):
    from clipdl.gui import theme
    theme.apply(app, "nope", "nope")
    d, c = theme.current()
    assert d.key == "studio" and c.dark == theme.os_is_dark()


def test_url_area_switches_between_drop_and_input(window):
    w = window
    w.show()
    assert w.drop.isVisible() and not w.url_edit.isVisible()          # 空なら大きな貼り付け欄
    assert not w.dl_btn.isEnabled()
    w.drop.pasted.emit("https://www.youtube.com/watch?v=abc")
    assert w.url_edit.isVisible() and not w.drop.isVisible()
    assert w.dl_btn.isEnabled() and w.dl_btn.text() == "動画全体をダウンロード"
    w.url_edit.clear()
    w.url_edit.clearFocus()
    w._on_url_changed("")
    assert w.drop.isVisible()
    w.drop.typeRequested.emit()                                        # クリックや入力で入力欄に切り替わる
    assert w.url_edit.isVisible()


def test_download_button_text_follows_range_count(window):
    w = window
    w.url_edit.setText("https://www.youtube.com/watch?v=abc")
    w._add_range()
    assert w.dl_btn.text() == "ダウンロード"
    w._add_range()
    w._add_range()
    assert w.dl_btn.text() == "3 つの範囲をダウンロード"
    w.url_edit.setText("https://a.com/x https://b.com/y")
    assert w.dl_btn.text() == "ダウンロード"


def test_changing_design_in_settings_applies_and_saves(window):
    from clipdl.core.settings import Settings
    from clipdl.gui import theme
    w = window
    w.design.setCurrentIndex(w.design.findData("pop"))
    w.mode.setCurrentIndex(w.mode.findData("light"))
    d, c = theme.current()
    assert d.key == "pop" and not c.dark
    assert w._dl_full_slot.indexOf(w.dl_btn) >= 0                     # ポップはボタンを横幅いっぱいに
    saved = Settings.load()
    assert saved.ui_design == "pop" and saved.ui_mode == "light"
    w.design.setCurrentIndex(w.design.findData("minimal"))
    assert w._dl_row.indexOf(w.dl_btn) >= 0


def test_selected_range_row_is_marked_by_property(window):
    w = window
    w._add_range()
    w._add_range()
    w._select_range(1)
    assert [r.property("selected") for r in w.rows] == [False, True]


def test_eta_clock_estimates_from_progress_rate():
    from clipdl.gui.widgets import _EtaClock
    now = [0.0]
    c = _EtaClock(clock=lambda: now[0])
    assert c.update(("video", 1), 0.10) is None          # 最初は測れない
    now[0] = 10.0
    assert c.update(("video", 1), 0.30) == pytest.approx(35.0)   # 10 秒で 20% → 残り 70% は 35 秒
    now[0] = 11.0
    assert c.update(("encode", 1), 0.05) is None         # 段階が変わったら測り直す
    assert c.update(("encode", 1), None) is None


def test_queue_card_states(app, tmp_path):
    from clipdl.core.downloader import DownloadOptions
    from clipdl.core.models import Progress
    from clipdl.core.pipeline import JobResult, JobSpec
    from clipdl.core.queue import Job, JobState
    from clipdl.gui.widgets import QueueRow
    job = Job(JobSpec(url="https://example.com/v", ranges=[], download=DownloadOptions(output_dir=tmp_path)))
    row = QueueRow(job, "テスト")
    assert row.thumb.pixmap() is not None and not row.thumb.pixmap().isNull()   # サムネが無くても代わりを描く
    job.state, job.progress = JobState.RUNNING, Progress("video", 0.5)
    row.refresh()
    assert row.pct.text() == "50%" and row.bar.isVisibleTo(row)
    assert row.remain.text() == "残り時間を計算中…"                      # 測れるまでは空欄にしない
    row._eta.t0 -= 20
    job.progress = Progress("video", 0.75)
    row.refresh()
    assert row.remain.text().startswith("残り 約")
    job.state = JobState.PAUSED
    row.refresh()
    assert row.remain.text() == ""                                      # 動いていないときは出さない
    job.state, job.result = JobState.DONE, JobResult(files=[tmp_path / "a"])
    row.refresh()
    assert row.status.property("state") == "done" and not row.bar.isVisibleTo(row)
    job.state = JobState.FAILED
    row.refresh()
    assert row.status.property("state") == "failed"


def test_app_icon_file_has_all_sizes():
    import struct
    from clipdl.gui.art import ASSET_DIR, ICON_SIZES
    data = (ASSET_DIR / "clipdl.ico").read_bytes()
    reserved, kind, count = struct.unpack("<HHH", data[:6])
    assert (reserved, kind, count) == (0, 1, len(ICON_SIZES))
    sizes = sorted(data[6 + 16 * i] or 256 for i in range(count))
    assert sizes == sorted(ICON_SIZES)
