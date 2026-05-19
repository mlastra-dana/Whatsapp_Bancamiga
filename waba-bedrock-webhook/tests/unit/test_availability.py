"""
Unit tests for the availability module (lambda-calendar/availability.py).

Tests cover:
- check_availability with a date where all slots are free
- check_availability with a fully booked date (no slots available)
- check_availability with a partially booked date
- check_availability with today's date (past slots excluded)
- check_availability with a weekend date (rejected)
- generate_candidate_slots returns correct number and alignment
- filter_available_slots with various busy period configurations

Requirements: 1.1, 1.2, 1.3, 1.4, 1.5, 1.6, 6.1, 6.2, 6.3, 6.5
"""

import datetime
import sys
import os
from unittest.mock import MagicMock, patch
from zoneinfo import ZoneInfo

import pytest

# Add lambda-calendar to the path so we can import modules directly
sys.path.insert(
    0, os.path.join(os.path.dirname(__file__), "..", "..", "lambda-calendar")
)

from availability import (
    generate_candidate_slots,
    filter_available_slots,
    format_slots_response,
    check_availability,
    ERROR_WEEKEND,
    ERROR_NO_SLOTS,
)
from validators import ERROR_INVALID_DATE

TIMEZONE = "America/Mexico_City"
TZ = ZoneInfo(TIMEZONE)


# ---------------------------------------------------------------------------
# generate_candidate_slots
# ---------------------------------------------------------------------------


class TestGenerateCandidateSlots:
    """Tests for generate_candidate_slots."""

    def test_returns_16_slots_for_business_day(self):
        """A weekday should produce exactly 16 half-hour slots."""
        date = datetime.date(2025, 1, 14)  # Tuesday
        slots = generate_candidate_slots(date, TIMEZONE)
        assert len(slots) == 16

    def test_slots_are_30_minutes(self):
        """Every slot must be exactly 30 minutes long."""
        date = datetime.date(2025, 1, 14)
        slots = generate_candidate_slots(date, TIMEZONE)
        for start, end in slots:
            assert (end - start) == datetime.timedelta(minutes=30)

    def test_slots_aligned_to_half_hour(self):
        """Start times must be on :00 or :30."""
        date = datetime.date(2025, 1, 14)
        slots = generate_candidate_slots(date, TIMEZONE)
        for start, _end in slots:
            local = start.astimezone(TZ)
            assert local.minute in (0, 30)
            assert local.second == 0
            assert local.microsecond == 0

    def test_first_slot_starts_at_9(self):
        """First slot should start at 09:00."""
        date = datetime.date(2025, 1, 14)
        slots = generate_candidate_slots(date, TIMEZONE)
        first_local = slots[0][0].astimezone(TZ)
        assert first_local.hour == 9
        assert first_local.minute == 0

    def test_last_slot_ends_at_17(self):
        """Last slot should end at 17:00."""
        date = datetime.date(2025, 1, 14)
        slots = generate_candidate_slots(date, TIMEZONE)
        last_local = slots[-1][1].astimezone(TZ)
        assert last_local.hour == 17
        assert last_local.minute == 0

    def test_slots_within_business_hours(self):
        """All slots must be within 9:00–17:00."""
        date = datetime.date(2025, 1, 14)
        slots = generate_candidate_slots(date, TIMEZONE)
        for start, end in slots:
            local_start = start.astimezone(TZ)
            local_end = end.astimezone(TZ)
            assert local_start.hour >= 9
            assert local_end.hour <= 17


# ---------------------------------------------------------------------------
# filter_available_slots
# ---------------------------------------------------------------------------


class TestFilterAvailableSlots:
    """Tests for filter_available_slots."""

    def _make_slots(self, date):
        return generate_candidate_slots(date, TIMEZONE)

    def test_no_busy_periods_returns_all(self):
        """With no busy periods, all candidate slots are available."""
        date = datetime.date(2025, 1, 14)
        slots = self._make_slots(date)
        result = filter_available_slots(slots, {})
        assert len(result) == 16

    def test_single_busy_period_removes_overlapping_slot(self):
        """A busy period covering 09:00–09:30 should remove that slot."""
        date = datetime.date(2025, 1, 14)
        slots = self._make_slots(date)
        busy = {
            "user@example.com": [
                {
                    "start": datetime.datetime(2025, 1, 14, 9, 0, tzinfo=TZ).isoformat(),
                    "end": datetime.datetime(2025, 1, 14, 9, 30, tzinfo=TZ).isoformat(),
                }
            ]
        }
        result = filter_available_slots(slots, busy)
        assert len(result) == 15
        # The 09:00 slot should be gone
        starts = [s.astimezone(TZ).hour * 60 + s.astimezone(TZ).minute for s, _ in result]
        assert 9 * 60 not in starts

    def test_multiple_calendars_busy(self):
        """Busy periods from different calendars should all be excluded."""
        date = datetime.date(2025, 1, 14)
        slots = self._make_slots(date)
        busy = {
            "user1@example.com": [
                {
                    "start": datetime.datetime(2025, 1, 14, 9, 0, tzinfo=TZ).isoformat(),
                    "end": datetime.datetime(2025, 1, 14, 9, 30, tzinfo=TZ).isoformat(),
                }
            ],
            "user2@example.com": [
                {
                    "start": datetime.datetime(2025, 1, 14, 10, 0, tzinfo=TZ).isoformat(),
                    "end": datetime.datetime(2025, 1, 14, 10, 30, tzinfo=TZ).isoformat(),
                }
            ],
        }
        result = filter_available_slots(slots, busy)
        assert len(result) == 14

    def test_past_slots_excluded_with_now(self):
        """Slots before 'now' should be excluded."""
        date = datetime.date(2025, 1, 14)
        slots = self._make_slots(date)
        now = datetime.datetime(2025, 1, 14, 12, 0, tzinfo=TZ)
        result = filter_available_slots(slots, {}, now=now)
        for start, _end in result:
            assert start >= now

    def test_now_none_keeps_all_slots(self):
        """When now is None, no slots are excluded for being in the past."""
        date = datetime.date(2025, 1, 14)
        slots = self._make_slots(date)
        result = filter_available_slots(slots, {}, now=None)
        assert len(result) == 16

    def test_fully_booked_returns_empty(self):
        """When all slots are busy, result should be empty."""
        date = datetime.date(2025, 1, 14)
        slots = self._make_slots(date)
        busy = {
            "user@example.com": [
                {
                    "start": datetime.datetime(2025, 1, 14, 9, 0, tzinfo=TZ).isoformat(),
                    "end": datetime.datetime(2025, 1, 14, 17, 0, tzinfo=TZ).isoformat(),
                }
            ]
        }
        result = filter_available_slots(slots, busy)
        assert len(result) == 0


# ---------------------------------------------------------------------------
# format_slots_response
# ---------------------------------------------------------------------------


class TestFormatSlotsResponse:
    """Tests for format_slots_response."""

    def test_numbered_list_format(self):
        """Output should be a numbered list with HH:MM format."""
        date = datetime.date(2025, 1, 14)
        slots = generate_candidate_slots(date, TIMEZONE)[:3]
        result = format_slots_response(slots, TIMEZONE)
        lines = result.strip().split("\n")
        assert len(lines) == 3
        assert lines[0] == "1. 09:00 - 09:30"
        assert lines[1] == "2. 09:30 - 10:00"
        assert lines[2] == "3. 10:00 - 10:30"

    def test_empty_slots_returns_empty_string(self):
        """No slots should produce an empty string."""
        result = format_slots_response([], TIMEZONE)
        assert result == ""

    def test_single_slot(self):
        """A single slot should produce one numbered line."""
        slot_start = datetime.datetime(2025, 1, 14, 11, 0, tzinfo=TZ)
        slot_end = datetime.datetime(2025, 1, 14, 11, 30, tzinfo=TZ)
        result = format_slots_response([(slot_start, slot_end)], TIMEZONE)
        assert result == "1. 11:00 - 11:30"


# ---------------------------------------------------------------------------
# check_availability (integration-style with mocked Google API)
# ---------------------------------------------------------------------------


class TestCheckAvailability:
    """Tests for the check_availability orchestrator."""

    def _make_mock_service(self, freebusy_response):
        """Build a mock calendar_service that returns the given FreeBusy response."""
        mock_service = MagicMock()
        mock_service.freebusy.return_value.query.return_value.execute.return_value = (
            freebusy_response
        )
        return mock_service

    def _freebusy_response(self, calendars_busy):
        """Build a FreeBusy-style response dict."""
        return {
            "calendars": {
                email: {"busy": busy_list}
                for email, busy_list in calendars_busy.items()
            }
        }

    def test_all_slots_free(self):
        """When no one is busy, all 16 slots should be returned."""
        team = ["a@example.com", "b@example.com"]
        fb = self._freebusy_response({e: [] for e in team})
        svc = self._make_mock_service(fb)

        code, msg = check_availability("2025-01-14", svc, team, TIMEZONE)
        assert code == 200
        lines = msg.strip().split("\n")
        assert len(lines) == 16
        assert lines[0] == "1. 09:00 - 09:30"

    def test_fully_booked(self):
        """When the entire day is busy, the 'no slots' message is returned."""
        team = ["a@example.com"]
        fb = self._freebusy_response({
            "a@example.com": [
                {
                    "start": datetime.datetime(2025, 1, 14, 9, 0, tzinfo=TZ).isoformat(),
                    "end": datetime.datetime(2025, 1, 14, 17, 0, tzinfo=TZ).isoformat(),
                }
            ]
        })
        svc = self._make_mock_service(fb)

        code, msg = check_availability("2025-01-14", svc, team, TIMEZONE)
        assert code == 200
        assert "No hay horarios disponibles" in msg
        assert "2025-01-14" in msg

    def test_partially_booked(self):
        """Some busy periods should reduce the number of available slots."""
        team = ["a@example.com"]
        fb = self._freebusy_response({
            "a@example.com": [
                {
                    "start": datetime.datetime(2025, 1, 14, 9, 0, tzinfo=TZ).isoformat(),
                    "end": datetime.datetime(2025, 1, 14, 10, 0, tzinfo=TZ).isoformat(),
                }
            ]
        })
        svc = self._make_mock_service(fb)

        code, msg = check_availability("2025-01-14", svc, team, TIMEZONE)
        assert code == 200
        lines = msg.strip().split("\n")
        # 2 slots removed (09:00 and 09:30)
        assert len(lines) == 14
        assert "09:00" not in msg
        assert "09:30" not in msg

    def test_weekend_rejected(self):
        """A Saturday date should return the weekend error message."""
        svc = MagicMock()
        team = ["a@example.com"]

        code, msg = check_availability("2025-01-18", svc, team, TIMEZONE)
        assert code == 200
        assert msg == ERROR_WEEKEND

    def test_invalid_date_format(self):
        """An invalid date string should return the date format error."""
        svc = MagicMock()
        team = ["a@example.com"]

        code, msg = check_availability("not-a-date", svc, team, TIMEZONE)
        assert code == 200
        assert msg == ERROR_INVALID_DATE

    def test_today_excludes_past_slots(self):
        """When checking today's date, past slots should be excluded."""
        team = ["a@example.com"]
        # Use a fixed "now" at 14:00 on 2025-01-14
        fixed_now = datetime.datetime(2025, 1, 14, 14, 0, tzinfo=TZ)
        today_str = "2025-01-14"

        fb = self._freebusy_response({"a@example.com": []})
        svc = self._make_mock_service(fb)

        with patch("availability.datetime") as mock_dt:
            # Make datetime.datetime.now() return our fixed time
            mock_dt.datetime.now.return_value = fixed_now
            # Keep real date operations working
            mock_dt.datetime.fromisoformat = datetime.datetime.fromisoformat
            mock_dt.datetime.side_effect = lambda *a, **kw: datetime.datetime(*a, **kw)
            mock_dt.date = datetime.date
            mock_dt.timedelta = datetime.timedelta

            code, msg = check_availability(today_str, svc, team, TIMEZONE)

        assert code == 200
        # At 14:00, slots from 14:00 onward should remain: 14:00, 14:30, 15:00, 15:30, 16:00, 16:30 = 6 slots
        lines = msg.strip().split("\n")
        assert len(lines) == 6
        # First available slot should be 14:00
        assert "14:00" in lines[0]
