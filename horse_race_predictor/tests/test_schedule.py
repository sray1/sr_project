"""Tests for schedule.active_tracks / inactive_tracks (overlap logic)."""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import schedule  # noqa: E402


def _sched():
    # Minimal synthetic schedule for deterministic tests.
    return {
        "YEAR": [("2026-01-01", "2026-12-31")],       # year-round
        "SUMMER": [("2026-07-03", "2026-09-07")],     # summer only
        "SPRING": [("2026-04-03", "2026-04-24")],     # April only
        "GAP": [("2026-06-03", "2026-06-07"),         # two meets with a gap
                ("2026-07-18", "2026-09-10")],
    }


def test_active_tracks_overlap_inclusive():
    s = _sched()
    # Window 2026-06-20 -> 2026-07-03: SUMMER opens 7/3 (touches end), YEAR covers, GAP's
    # second meet starts 7/18 (after window) so GAP excluded; SPRING excluded.
    assert schedule.active_tracks("2026-06-20", "2026-07-03", s) == ["SUMMER", "YEAR"]


def test_inactive_tracks_complement():
    s = _sched()
    assert schedule.inactive_tracks("2026-06-20", "2026-07-03", s) == ["GAP", "SPRING"]


def test_year_round_always_active():
    s = _sched()
    for w in [("2026-01-01", "2026-01-02"), ("2026-12-30", "2026-12-31"), ("2026-06-15", "2026-06-16")]:
        assert "YEAR" in schedule.active_tracks(*w, s)


def test_spring_only_active_in_april():
    s = _sched()
    assert "SPRING" in schedule.active_tracks("2026-04-10", "2026-04-20", s)
    assert "SPRING" not in schedule.active_tracks("2026-06-20", "2026-07-03", s)


def test_gap_track_first_meet_active_early_june():
    s = _sched()
    # GAP's first meet 6/3-6/7 overlaps a 6/5 window.
    assert "GAP" in schedule.active_tracks("2026-06-05", "2026-06-05", s)
    # But not a mid-June window between its two meets.
    assert "GAP" not in schedule.active_tracks("2026-06-20", "2026-07-03", s)


def test_real_schedule_keys_lowercased_and_present():
    # The real 2026 schedule should include the major tracks and not GG (closed).
    assert "CD" in schedule.SCHEDULE
    assert "SAR" in schedule.SCHEDULE
    assert "GP" in schedule.SCHEDULE
    assert "GG" not in schedule.SCHEDULE  # Golden Gate closed in 2024