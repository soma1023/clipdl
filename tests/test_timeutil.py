import pytest

from clipdl.core.models import ClipRange
from clipdl.core.timeutil import (TimeFormatError, format_ass_time, format_time, parse_range, parse_time,
                                  validate_ranges)


@pytest.mark.parametrize("text,expected", [
    ("0", 0), ("45", 45), ("01:30", 90), ("1:02:03", 3723), ("10:00:00", 36000),
    ("00:00:01.5", 1.5), ("90:00", 5400), (" 00:01:00 ", 60),
])
def test_parse_time(text, expected):
    assert parse_time(text) == pytest.approx(expected)


@pytest.mark.parametrize("text", ["", "abc", "1:2:3:4", "00:61", "00:60:00", "-1", "1::2"])
def test_parse_time_invalid(text):
    with pytest.raises(TimeFormatError):
        parse_time(text)


@pytest.mark.parametrize("sec,expected", [(0, "00:00:00"), (59.4, "00:00:59"), (3723, "01:02:03"),
                                          (36000, "10:00:00")])
def test_format_time(sec, expected):
    assert format_time(sec) == expected


def test_format_time_millis_and_sep():
    assert format_time(3723.0456, millis=True) == "01:02:03.046"
    assert format_time(3723, sep="") == "010203"


def test_format_time_rounds_up_into_next_second():
    assert format_time(59.9996, millis=True) == "00:01:00.000"


@pytest.mark.parametrize("sec,expected", [(0, "0:00:00.00"), (1.234, "0:00:01.23"), (1.235, "0:00:01.24"),
                                          (3723.5, "1:02:03.50"), (-3, "0:00:00.00"), (59.996, "0:01:00.00")])
def test_format_ass_time(sec, expected):
    assert format_ass_time(sec) == expected


def test_format_short():
    from clipdl.core.timeutil import format_short
    assert [format_short(x) for x in (0, 5, 20, 65, 600, 3599.6, 3600, 23151)] == \
        ["0:00", "0:05", "0:20", "1:05", "10:00", "1:00:00", "1:00:00", "6:25:51"]


def test_roundtrip():
    for s in (0, 1, 61, 3599, 3600, 86399):
        assert parse_time(format_time(s)) == s


def test_parse_range():
    r = parse_range("00:01:00-00:02:30", index=3)
    assert (r.start, r.end, r.index) == (60, 150, 3)
    assert parse_range("1:00 〜 2:00").end == 120


@pytest.mark.parametrize("text", ["00:02:00-00:01:00", "00:01:00-00:01:00", "00:01:00", "-00:01:00"])
def test_parse_range_invalid(text):
    with pytest.raises(TimeFormatError):
        parse_range(text)


def test_validate_ranges_sorts_renumbers_and_clamps():
    rs = [ClipRange(100, 200, 1), ClipRange(10, 20, 2), ClipRange(250, 999, 3)]
    out = validate_ranges(rs, duration=300)
    assert [(r.start, r.end, r.index) for r in out] == [(10, 20, 1), (100, 200, 2), (250, 300, 3)]


def test_validate_ranges_start_after_duration():
    with pytest.raises(TimeFormatError):
        validate_ranges([ClipRange(400, 500)], duration=300)


def test_clip_tag():
    assert ClipRange(60, 150, 2).tag() == "c02_000100-000230"
