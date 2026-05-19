"""
Unit tests for the validators module (lambda-calendar/validators.py).

Tests cover:
- validate_date with valid dates, invalid formats (DD/MM/YYYY), impossible dates (2025-02-30), empty string
- validate_start_time with valid ISO 8601 timestamps, invalid formats, empty string
- validate_title with valid titles, empty string, 201-character string
- is_business_day with weekdays and weekends
- is_within_business_hours with times inside and outside 9:00-16:30
- slots_overlap with overlapping and non-overlapping intervals

Requirements: 9.1, 9.2, 9.3, 1.5, 1.6, 2.4, 2.5
"""

import datetime
import sys
import os

import pytest

# Add lambda-calendar to the path so we can import validators directly
sys.path.insert(
    0, os.path.join(os.path.dirname(__file__), "..", "..", "lambda-calendar")
)

from validators import (
    validate_date,
    validate_start_time,
    validate_title,
    is_business_day,
    is_within_business_hours,
    slots_overlap,
    ERROR_INVALID_DATE,
    ERROR_INVALID_TIME,
    ERROR_INVALID_TITLE,
)


# ===================================================================
# validate_date
# ===================================================================


class TestValidateDate:
    """Tests for validate_date — Requirement 9.1."""

    def test_valid_date(self):
        ok, msg, parsed = validate_date("2025-01-14")
        assert ok is True
        assert msg == ""
        assert parsed == datetime.date(2025, 1, 14)

    def test_valid_leap_year_date(self):
        ok, msg, parsed = validate_date("2024-02-29")
        assert ok is True
        assert parsed == datetime.date(2024, 2, 29)

    def test_invalid_format_dd_mm_yyyy(self):
        ok, msg, parsed = validate_date("14/01/2025")
        assert ok is False
        assert msg == ERROR_INVALID_DATE
        assert parsed is None

    def test_impossible_date_feb_30(self):
        ok, msg, parsed = validate_date("2025-02-30")
        assert ok is False
        assert msg == ERROR_INVALID_DATE
        assert parsed is None

    def test_empty_string(self):
        ok, msg, parsed = validate_date("")
        assert ok is False
        assert msg == ERROR_INVALID_DATE
        assert parsed is None

    def test_non_date_string(self):
        ok, msg, parsed = validate_date("not-a-date")
        assert ok is False
        assert msg == ERROR_INVALID_DATE
        assert parsed is None

    def test_date_with_time(self):
        """A full datetime string should be rejected (not strict YYYY-MM-DD)."""
        ok, msg, parsed = validate_date("2025-01-14T11:00:00")
        assert ok is False
        assert msg == ERROR_INVALID_DATE
        assert parsed is None


# ===================================================================
# validate_start_time
# ===================================================================


class TestValidateStartTime:
    """Tests for validate_start_time — Requirement 9.2."""

    def test_valid_iso8601_with_offset(self):
        ok, msg, parsed = validate_start_time("2025-01-14T11:00:00-06:00")
        assert ok is True
        assert msg == ""
        assert parsed is not None
        assert parsed.tzinfo is not None

    def test_valid_iso8601_utc(self):
        ok, msg, parsed = validate_start_time("2025-01-14T17:00:00+00:00")
        assert ok is True
        assert parsed is not None

    def test_invalid_no_timezone(self):
        """ISO 8601 without timezone info should be rejected."""
        ok, msg, parsed = validate_start_time("2025-01-14T11:00:00")
        assert ok is False
        assert msg == ERROR_INVALID_TIME
        assert parsed is None

    def test_invalid_date_only(self):
        ok, msg, parsed = validate_start_time("2025-01-14")
        assert ok is False
        assert msg == ERROR_INVALID_TIME
        assert parsed is None

    def test_empty_string(self):
        ok, msg, parsed = validate_start_time("")
        assert ok is False
        assert msg == ERROR_INVALID_TIME
        assert parsed is None

    def test_garbage_string(self):
        ok, msg, parsed = validate_start_time("hello world")
        assert ok is False
        assert msg == ERROR_INVALID_TIME
        assert parsed is None


# ===================================================================
# validate_title
# ===================================================================


class TestValidateTitle:
    """Tests for validate_title — Requirement 9.3."""

    def test_valid_short_title(self):
        ok, msg = validate_title("Reunión")
        assert ok is True
        assert msg == ""

    def test_valid_max_length_title(self):
        ok, msg = validate_title("A" * 200)
        assert ok is True
        assert msg == ""

    def test_single_char_title(self):
        ok, msg = validate_title("X")
        assert ok is True
        assert msg == ""

    def test_empty_title(self):
        ok, msg = validate_title("")
        assert ok is False
        assert msg == ERROR_INVALID_TITLE

    def test_too_long_title(self):
        ok, msg = validate_title("A" * 201)
        assert ok is False
        assert msg == ERROR_INVALID_TITLE


# ===================================================================
# is_business_day
# ===================================================================


class TestIsBusinessDay:
    """Tests for is_business_day — Requirements 1.6, 2.5."""

    @pytest.mark.parametrize(
        "date",
        [
            datetime.date(2025, 1, 13),  # Monday
            datetime.date(2025, 1, 14),  # Tuesday
            datetime.date(2025, 1, 15),  # Wednesday
            datetime.date(2025, 1, 16),  # Thursday
            datetime.date(2025, 1, 17),  # Friday
        ],
    )
    def test_weekdays_are_business_days(self, date):
        assert is_business_day(date) is True

    @pytest.mark.parametrize(
        "date",
        [
            datetime.date(2025, 1, 18),  # Saturday
            datetime.date(2025, 1, 19),  # Sunday
        ],
    )
    def test_weekends_are_not_business_days(self, date):
        assert is_business_day(date) is False


# ===================================================================
# is_within_business_hours
# ===================================================================


class TestIsWithinBusinessHours:
    """Tests for is_within_business_hours — Requirement 2.4."""

    TZ = "America/Mexico_City"

    def test_9am_is_within(self):
        from zoneinfo import ZoneInfo

        dt = datetime.datetime(2025, 1, 14, 9, 0, 0, tzinfo=ZoneInfo(self.TZ))
        assert is_within_business_hours(dt, self.TZ) is True

    def test_1630_is_within(self):
        """16:30 is the last valid start time (event ends at 17:00)."""
        from zoneinfo import ZoneInfo

        dt = datetime.datetime(2025, 1, 14, 16, 30, 0, tzinfo=ZoneInfo(self.TZ))
        assert is_within_business_hours(dt, self.TZ) is True

    def test_1631_is_outside(self):
        from zoneinfo import ZoneInfo

        dt = datetime.datetime(2025, 1, 14, 16, 31, 0, tzinfo=ZoneInfo(self.TZ))
        assert is_within_business_hours(dt, self.TZ) is False

    def test_8am_is_outside(self):
        from zoneinfo import ZoneInfo

        dt = datetime.datetime(2025, 1, 14, 8, 0, 0, tzinfo=ZoneInfo(self.TZ))
        assert is_within_business_hours(dt, self.TZ) is False

    def test_1700_is_outside(self):
        from zoneinfo import ZoneInfo

        dt = datetime.datetime(2025, 1, 14, 17, 0, 0, tzinfo=ZoneInfo(self.TZ))
        assert is_within_business_hours(dt, self.TZ) is False

    def test_noon_is_within(self):
        from zoneinfo import ZoneInfo

        dt = datetime.datetime(2025, 1, 14, 12, 0, 0, tzinfo=ZoneInfo(self.TZ))
        assert is_within_business_hours(dt, self.TZ) is True

    def test_different_timezone_conversion(self):
        """A time that is 9:00 in Mexico City but expressed in UTC."""
        from zoneinfo import ZoneInfo

        # Mexico City is UTC-6, so 9:00 CST = 15:00 UTC
        dt = datetime.datetime(2025, 1, 14, 15, 0, 0, tzinfo=ZoneInfo("UTC"))
        assert is_within_business_hours(dt, self.TZ) is True


# ===================================================================
# slots_overlap
# ===================================================================


class TestSlotsOverlap:
    """Tests for slots_overlap — Requirement 6.1."""

    def _dt(self, hour, minute=0):
        """Helper to create a timezone-aware datetime."""
        from zoneinfo import ZoneInfo

        return datetime.datetime(
            2025, 1, 14, hour, minute, 0, tzinfo=ZoneInfo("America/Mexico_City")
        )

    def test_overlapping_slots(self):
        # Slot 9:00-9:30 overlaps with busy 9:00-10:00
        assert slots_overlap(self._dt(9), self._dt(9, 30), self._dt(9), self._dt(10)) is True

    def test_partial_overlap_start(self):
        # Slot 9:00-9:30 overlaps with busy 9:15-10:00
        assert slots_overlap(self._dt(9), self._dt(9, 30), self._dt(9, 15), self._dt(10)) is True

    def test_partial_overlap_end(self):
        # Slot 10:00-10:30 overlaps with busy 9:00-10:15
        assert slots_overlap(self._dt(10), self._dt(10, 30), self._dt(9), self._dt(10, 15)) is True

    def test_no_overlap_before(self):
        # Slot 9:00-9:30 does not overlap with busy 10:00-11:00
        assert slots_overlap(self._dt(9), self._dt(9, 30), self._dt(10), self._dt(11)) is False

    def test_no_overlap_after(self):
        # Slot 11:00-11:30 does not overlap with busy 9:00-10:00
        assert slots_overlap(self._dt(11), self._dt(11, 30), self._dt(9), self._dt(10)) is False

    def test_adjacent_no_overlap(self):
        # Slot 9:30-10:00 is adjacent to busy 9:00-9:30 — no overlap
        assert slots_overlap(self._dt(9, 30), self._dt(10), self._dt(9), self._dt(9, 30)) is False

    def test_busy_contains_slot(self):
        # Busy 9:00-12:00 fully contains slot 10:00-10:30
        assert slots_overlap(self._dt(10), self._dt(10, 30), self._dt(9), self._dt(12)) is True
