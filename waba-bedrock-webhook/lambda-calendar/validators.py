"""
Validators Module — Pure validation functions for input parameters.

Provides stateless validation for date strings (YYYY-MM-DD), ISO 8601
timestamps, event titles (1-200 characters), business day checks (Mon-Fri),
business hours checks (9:00-16:30 to allow 30-min events ending by 17:00),
and slot overlap detection for busy period filtering.
"""

import datetime
from zoneinfo import ZoneInfo

# ---------------------------------------------------------------------------
# Error messages (Spanish, matching design document)
# ---------------------------------------------------------------------------

ERROR_INVALID_DATE = (
    "El formato de fecha no es válido. Por favor usa el formato "
    "YYYY-MM-DD (ejemplo: 2025-01-14)."
)
ERROR_INVALID_TIME = (
    "El formato de hora no es válido. Por favor usa formato ISO 8601 "
    "(ejemplo: 2025-01-14T11:00:00-06:00)."
)
ERROR_INVALID_TITLE = (
    "El título del evento debe tener entre 1 y 200 caracteres."
)

# ---------------------------------------------------------------------------
# Business-hours constants
# ---------------------------------------------------------------------------

BUSINESS_START_HOUR = 9
BUSINESS_START_MINUTE = 0
BUSINESS_END_HOUR = 16
BUSINESS_END_MINUTE = 30


# ---------------------------------------------------------------------------
# Validation functions
# ---------------------------------------------------------------------------


def validate_date(date_str: str) -> tuple[bool, str, datetime.date | None]:
    """
    Validate that *date_str* is a valid date in YYYY-MM-DD format.

    Returns:
        (True,  "", parsed_date)  on success
        (False, error_message, None) on failure
    """
    try:
        parsed = datetime.date.fromisoformat(date_str)
        # Ensure the string is strictly YYYY-MM-DD (10 chars, two dashes)
        if len(date_str) != 10 or date_str[4] != "-" or date_str[7] != "-":
            return (False, ERROR_INVALID_DATE, None)
        return (True, "", parsed)
    except (ValueError, TypeError):
        return (False, ERROR_INVALID_DATE, None)


def validate_start_time(
    start_time_str: str,
) -> tuple[bool, str, datetime.datetime | None]:
    """
    Validate that *start_time_str* is a valid ISO 8601 timestamp with
    timezone information (e.g. ``2025-01-14T11:00:00-06:00``).

    Returns:
        (True,  "", parsed_datetime)  on success
        (False, error_message, None) on failure
    """
    try:
        parsed = datetime.datetime.fromisoformat(start_time_str)
        # Must include timezone info to be a complete ISO 8601 timestamp
        if parsed.tzinfo is None:
            return (False, ERROR_INVALID_TIME, None)
        return (True, "", parsed)
    except (ValueError, TypeError):
        return (False, ERROR_INVALID_TIME, None)


def validate_title(title: str) -> tuple[bool, str]:
    """
    Validate that *title* is between 1 and 200 characters (inclusive).

    Returns:
        (True,  "")            on success
        (False, error_message) on failure
    """
    if not isinstance(title, str) or len(title) < 1 or len(title) > 200:
        return (False, ERROR_INVALID_TITLE)
    return (True, "")


def is_business_day(date: datetime.date) -> bool:
    """Return ``True`` if *date* falls on Monday–Friday."""
    return date.weekday() < 5  # 0=Mon … 4=Fri


def is_within_business_hours(
    dt: datetime.datetime,
    timezone_str: str,
) -> bool:
    """
    Return ``True`` if *dt* falls within business hours (9:00–16:30) in the
    given timezone.

    The upper bound is 16:30 (not 17:00) because a 30-minute event starting
    at 16:30 would end at 17:00, which is the actual close of business.
    """
    tz = ZoneInfo(timezone_str)
    local_dt = dt.astimezone(tz)

    start = local_dt.replace(
        hour=BUSINESS_START_HOUR,
        minute=BUSINESS_START_MINUTE,
        second=0,
        microsecond=0,
    )
    end = local_dt.replace(
        hour=BUSINESS_END_HOUR,
        minute=BUSINESS_END_MINUTE,
        second=0,
        microsecond=0,
    )

    return start <= local_dt <= end


def slots_overlap(
    slot_start: datetime.datetime,
    slot_end: datetime.datetime,
    busy_start: datetime.datetime,
    busy_end: datetime.datetime,
) -> bool:
    """
    Return ``True`` if the candidate slot overlaps with the busy period.

    Uses the standard interval overlap formula:
        slot_start < busy_end AND slot_end > busy_start
    """
    return slot_start < busy_end and slot_end > busy_start
