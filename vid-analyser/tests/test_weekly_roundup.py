from datetime import UTC, datetime
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

from vid_analyser.agent.weekly_roundup import (
    RoundupDeps,
    _format_local_datetime,
    get_roundup_instructions,
)
from vid_analyser.schedule import next_hourly_run, next_weekly_run


LONDON = ZoneInfo("Europe/London")


@pytest.mark.parametrize(
    ("now", "expected"),
    [
        (
            datetime(2026, 7, 5, 20, 59, tzinfo=UTC),
            datetime(2026, 7, 5, 22, 0, tzinfo=LONDON),
        ),
        (
            datetime(2026, 7, 5, 21, 0, tzinfo=UTC),
            datetime(2026, 7, 12, 22, 0, tzinfo=LONDON),
        ),
        (
            datetime(2026, 1, 5, 12, 0, tzinfo=UTC),
            datetime(2026, 1, 11, 22, 0, tzinfo=LONDON),
        ),
    ],
)
def test_next_roundup_time(now: datetime, expected: datetime) -> None:
    assert next_weekly_run(now) == expected


def test_next_roundup_time_requires_aware_datetime() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        next_weekly_run(datetime(2026, 7, 5, 12, 0))


def test_next_hourly_run_is_strictly_after_now() -> None:
    assert next_hourly_run(datetime(2026, 7, 5, 12, 0, tzinfo=UTC)) == datetime(
        2026, 7, 5, 13, 0, tzinfo=UTC
    )
    assert next_hourly_run(datetime(2026, 7, 5, 12, 59, tzinfo=UTC)) == datetime(
        2026, 7, 5, 13, 0, tzinfo=UTC
    )


def test_roundup_system_prompt_can_override_default() -> None:
    ctx = SimpleNamespace(
        deps=RoundupDeps(
            system_prompt="Custom weekly instructions",
            notifier_style=None,
        )
    )

    assert get_roundup_instructions(ctx) == "Custom weekly instructions"


def test_roundup_datetime_includes_authoritative_london_weekday() -> None:
    assert _format_local_datetime("2026-07-05T23:30:00+00:00") == (
        "Monday 6 July 2026 at 00:30 BST"
    )
