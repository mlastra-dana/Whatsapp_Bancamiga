"""
Unit tests for the event creator module (lambda-calendar/event_creator.py).

Tests cover:
- Successful event creation with confirmation message
- Event creation when slot is already taken (conflict)
- Event creation with time outside business hours (rejected)
- Event creation on weekend (rejected)
- build_event_payload produces correct structure
- format_event_confirmation includes all required fields

Requirements: 2.1, 2.2, 2.3, 2.4, 2.5, 2.6, 2.7
"""

import datetime
import sys
import os
from unittest.mock import MagicMock
from zoneinfo import ZoneInfo

import pytest

# Add the lambda-calendar directory to sys.path
sys.path.insert(
    0, os.path.join(os.path.dirname(__file__), "..", "..", "lambda-calendar")
)

from event_creator import (
    build_event_payload,
    format_event_confirmation,
    create_event,
    ERROR_WEEKEND,
    ERROR_OUTSIDE_HOURS,
    ERROR_SLOT_TAKEN,
)

TIMEZONE = "America/Mexico_City"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_calendar_service(busy_periods=None, created_event=None):
    """Build a mock Google Calendar service with freebusy and events."""
    service = MagicMock()

    # Default: no busy periods
    if busy_periods is None:
        busy_periods = {}

    freebusy_response = {"calendars": busy_periods}
    service.freebusy().query().execute.return_value = freebusy_response
    # Ensure chained calls work
    service.freebusy.return_value.query.return_value.execute.return_value = (
        freebusy_response
    )

    # Default created event
    if created_event is None:
        created_event = {
            "summary": "Test Meeting",
            "start": {"dateTime": "2025-01-14T11:00:00-06:00"},
            "end": {"dateTime": "2025-01-14T11:30:00-06:00"},
            "htmlLink": "https://calendar.google.com/event?eid=abc123",
        }

    service.events().insert().execute.return_value = created_event
    service.events.return_value.insert.return_value.execute.return_value = (
        created_event
    )

    return service


def _freebusy_for_calendars(team_calendars, busy_list=None):
    """Build a freebusy calendars dict for the given team calendars."""
    if busy_list is None:
        busy_list = []
    return {email: {"busy": busy_list} for email in team_calendars}


# ---------------------------------------------------------------------------
# build_event_payload tests
# ---------------------------------------------------------------------------


class TestBuildEventPayload:
    """Tests for build_event_payload."""

    def test_basic_payload_structure(self):
        """Payload contains summary, start, end, and attendees."""
        tz = ZoneInfo(TIMEZONE)
        start = datetime.datetime(2025, 1, 14, 11, 0, tzinfo=tz)
        end = datetime.datetime(2025, 1, 14, 11, 30, tzinfo=tz)
        attendees = ["a@test.com", "b@test.com"]

        payload = build_event_payload(start, end, "My Meeting", attendees, TIMEZONE)

        assert payload["summary"] == "My Meeting"
        assert payload["start"]["dateTime"] == start.isoformat()
        assert payload["start"]["timeZone"] == TIMEZONE
        assert payload["end"]["dateTime"] == end.isoformat()
        assert payload["end"]["timeZone"] == TIMEZONE
        assert payload["attendees"] == [
            {"email": "a@test.com"},
            {"email": "b@test.com"},
        ]

    def test_single_attendee(self):
        """Payload works with a single attendee."""
        tz = ZoneInfo(TIMEZONE)
        start = datetime.datetime(2025, 1, 14, 9, 0, tzinfo=tz)
        end = datetime.datetime(2025, 1, 14, 9, 30, tzinfo=tz)

        payload = build_event_payload(start, end, "Solo", ["solo@test.com"], TIMEZONE)

        assert len(payload["attendees"]) == 1
        assert payload["attendees"][0]["email"] == "solo@test.com"

    def test_end_time_is_30_min_after_start(self):
        """End dateTime reflects the provided end_time (start + 30 min)."""
        tz = ZoneInfo(TIMEZONE)
        start = datetime.datetime(2025, 1, 14, 16, 0, tzinfo=tz)
        end = start + datetime.timedelta(minutes=30)

        payload = build_event_payload(start, end, "Late", ["x@test.com"], TIMEZONE)

        parsed_end = datetime.datetime.fromisoformat(payload["end"]["dateTime"])
        parsed_start = datetime.datetime.fromisoformat(payload["start"]["dateTime"])
        assert (parsed_end - parsed_start) == datetime.timedelta(minutes=30)


# ---------------------------------------------------------------------------
# format_event_confirmation tests
# ---------------------------------------------------------------------------


class TestFormatEventConfirmation:
    """Tests for format_event_confirmation."""

    def test_contains_all_required_fields(self):
        """Confirmation includes date, start time, end time, title, and link."""
        event_data = {
            "summary": "Reunión de seguimiento",
            "start": {"dateTime": "2025-01-14T11:00:00-06:00"},
            "end": {"dateTime": "2025-01-14T11:30:00-06:00"},
            "htmlLink": "https://calendar.google.com/event?eid=abc123",
        }

        result = format_event_confirmation(event_data, TIMEZONE)

        assert "14/01/2025" in result
        assert "11:00" in result
        assert "11:30" in result
        assert "Reunión de seguimiento" in result
        assert "https://calendar.google.com/event?eid=abc123" in result

    def test_times_in_24h_format(self):
        """Times are displayed in HH:MM 24-hour format."""
        event_data = {
            "summary": "Afternoon",
            "start": {"dateTime": "2025-01-14T15:00:00-06:00"},
            "end": {"dateTime": "2025-01-14T15:30:00-06:00"},
            "htmlLink": "https://calendar.google.com/event?eid=xyz",
        }

        result = format_event_confirmation(event_data, TIMEZONE)

        assert "15:00" in result
        assert "15:30" in result


# ---------------------------------------------------------------------------
# create_event tests
# ---------------------------------------------------------------------------


class TestCreateEvent:
    """Tests for the create_event orchestrator."""

    TEAM = ["user1@test.com", "user2@test.com"]

    def test_successful_creation(self):
        """Event is created and confirmation is returned."""
        created_event = {
            "summary": "Test Meeting",
            "start": {"dateTime": "2025-01-14T11:00:00-06:00"},
            "end": {"dateTime": "2025-01-14T11:30:00-06:00"},
            "htmlLink": "https://calendar.google.com/event?eid=ok",
        }
        busy = _freebusy_for_calendars(self.TEAM, busy_list=[])
        service = _make_calendar_service(
            busy_periods=busy, created_event=created_event
        )

        status, msg = create_event(
            "2025-01-14T11:00:00-06:00", "Test Meeting", service, self.TEAM, TIMEZONE
        )

        assert status == 200
        assert "Reunión creada" in msg
        assert "11:00" in msg
        assert "11:30" in msg
        assert "https://calendar.google.com/event?eid=ok" in msg

    def test_slot_already_taken(self):
        """Returns error when the requested slot is busy."""
        busy = _freebusy_for_calendars(
            self.TEAM,
            busy_list=[
                {
                    "start": "2025-01-14T11:00:00-06:00",
                    "end": "2025-01-14T11:30:00-06:00",
                }
            ],
        )
        service = _make_calendar_service(busy_periods=busy)

        status, msg = create_event(
            "2025-01-14T11:00:00-06:00", "Blocked", service, self.TEAM, TIMEZONE
        )

        assert status == 200
        assert "ya no está disponible" in msg

    def test_outside_business_hours(self):
        """Rejects event outside business hours (before 9:00)."""
        busy = _freebusy_for_calendars(self.TEAM)
        service = _make_calendar_service(busy_periods=busy)

        status, msg = create_event(
            "2025-01-14T08:00:00-06:00", "Early", service, self.TEAM, TIMEZONE
        )

        assert status == 200
        assert msg == ERROR_OUTSIDE_HOURS

    def test_outside_business_hours_late(self):
        """Rejects event outside business hours (after 16:30)."""
        busy = _freebusy_for_calendars(self.TEAM)
        service = _make_calendar_service(busy_periods=busy)

        status, msg = create_event(
            "2025-01-14T17:00:00-06:00", "Late", service, self.TEAM, TIMEZONE
        )

        assert status == 200
        assert msg == ERROR_OUTSIDE_HOURS

    def test_weekend_rejected(self):
        """Rejects event on Saturday."""
        busy = _freebusy_for_calendars(self.TEAM)
        service = _make_calendar_service(busy_periods=busy)

        # 2025-01-18 is a Saturday
        status, msg = create_event(
            "2025-01-18T11:00:00-06:00", "Weekend", service, self.TEAM, TIMEZONE
        )

        assert status == 200
        assert msg == ERROR_WEEKEND

    def test_invalid_start_time(self):
        """Returns error for invalid ISO 8601 start_time."""
        service = _make_calendar_service()

        status, msg = create_event(
            "not-a-time", "Title", service, self.TEAM, TIMEZONE
        )

        assert status == 200
        assert "formato de hora" in msg.lower() or "ISO 8601" in msg

    def test_invalid_title_empty(self):
        """Returns error for empty title."""
        service = _make_calendar_service()

        status, msg = create_event(
            "2025-01-14T11:00:00-06:00", "", service, self.TEAM, TIMEZONE
        )

        assert status == 200
        assert "título" in msg.lower() or "1 y 200" in msg

    def test_invalid_title_too_long(self):
        """Returns error for title exceeding 200 characters."""
        service = _make_calendar_service()

        status, msg = create_event(
            "2025-01-14T11:00:00-06:00", "x" * 201, service, self.TEAM, TIMEZONE
        )

        assert status == 200
        assert "título" in msg.lower() or "1 y 200" in msg
