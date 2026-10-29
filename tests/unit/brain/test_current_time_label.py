"""The per-turn time line names its zone, so a UTC server is not read as Rome."""
from datetime import datetime, timedelta, timezone

from dodaios.brain.manager import _current_time_label


def test_label_carries_zone_and_offset() -> None:
    cet = timezone(timedelta(hours=1), "CET")
    label = _current_time_label(datetime(2026, 10, 29, 22, 4, tzinfo=cet))
    assert label == "[Current date and time: Thursday, 2026-10-29 22:04 CET (UTC+01:00)]"


def test_utc_server_is_labelled_utc() -> None:
    label = _current_time_label(datetime(2026, 10, 29, 21, 2, tzinfo=timezone.utc))
    assert label == "[Current date and time: Thursday, 2026-10-29 21:02 UTC (UTC+00:00)]"


def test_negative_offset_and_english_weekday() -> None:
    est = timezone(timedelta(hours=-5), "EST")
    assert "Sunday, 2026-11-01 08:00 EST (UTC-05:00)" in _current_time_label(
        datetime(2026, 11, 1, 8, 0, tzinfo=est)
    )
