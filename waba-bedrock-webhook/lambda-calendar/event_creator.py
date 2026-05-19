"""
Event Creator Module — Creates 30-minute events in Google Calendar.

Encapsulates the logic for building Google Calendar event payloads,
verifying slot availability before creation, inserting events via the
Google Calendar API (events.insert), and formatting confirmation messages
with date, time, title, and event link.
"""

import datetime
from zoneinfo import ZoneInfo

from validators import (
    validate_start_time,
    validate_title,
    is_business_day,
    is_within_business_hours,
    slots_overlap,
)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

SLOT_DURATION_MINUTES = 30
BUSINESS_START_HOUR = 9
BUSINESS_START_MINUTE = 0
BUSINESS_END_HOUR = 17
BUSINESS_END_MINUTE = 0

# Error messages (Spanish, matching design document)
ERROR_WEEKEND = (
    "La fecha seleccionada cae en fin de semana. "
    "Las reuniones se agendan únicamente de lunes a viernes."
)
ERROR_OUTSIDE_HOURS = (
    "La hora seleccionada está fuera del horario laboral. "
    "Las reuniones se agendan de 9:00 a 17:00."
)
ERROR_SLOT_TAKEN = (
    "El horario {time} ya no está disponible. "
    "Por favor consulta la disponibilidad nuevamente."
)


# ---------------------------------------------------------------------------
# Payload construction
# ---------------------------------------------------------------------------


def build_event_payload(
    start_time: datetime.datetime,
    end_time: datetime.datetime,
    title: str,
    attendees: list[str],
    timezone_str: str,
    contact_email: str | None = None,
    description: str = "",
) -> dict:
    """
    Construct the Google Calendar events.insert payload.

    Args:
        start_time: Event start as a timezone-aware datetime.
        end_time: Event end as a timezone-aware datetime.
        title: Event summary / title.
        attendees: List of team attendee email addresses.
        timezone_str: IANA timezone string (e.g. "America/Mexico_City").
        contact_email: Optional email of the external contact to add as attendee.
        description: Optional event description with contact details.

    Returns:
        Dict matching the Google Calendar API events.insert body schema.
    """
    all_attendees = [{"email": email} for email in attendees]
    if contact_email:
        all_attendees.append({"email": contact_email})

    payload = {
        "summary": title,
        "start": {
            "dateTime": start_time.isoformat(),
            "timeZone": timezone_str,
        },
        "end": {
            "dateTime": end_time.isoformat(),
            "timeZone": timezone_str,
        },
        "attendees": all_attendees,
    }
    if description:
        payload["description"] = description
    return payload


# ---------------------------------------------------------------------------
# Confirmation formatting
# ---------------------------------------------------------------------------


def format_event_confirmation(
    event_data: dict,
    timezone_str: str,
) -> str:
    """
    Format the confirmation message after a successful event creation.

    Extracts summary, start/end dateTime, and htmlLink from the
    ``events.insert`` response and presents them in a human-readable
    format with HH:MM 24-hour times.

    Args:
        event_data: Response dict from Google Calendar events.insert.
        timezone_str: IANA timezone for display.

    Returns:
        Formatted confirmation string with date, times, title, and link.
    """
    tz = ZoneInfo(timezone_str)

    title = event_data["summary"]
    start_dt = datetime.datetime.fromisoformat(
        event_data["start"]["dateTime"]
    ).astimezone(tz)
    end_dt = datetime.datetime.fromisoformat(
        event_data["end"]["dateTime"]
    ).astimezone(tz)
    link = event_data["htmlLink"]

    date_str = start_dt.strftime("%d/%m/%Y")
    start_str = start_dt.strftime("%H:%M")
    end_str = end_dt.strftime("%H:%M")

    return (
        f"Reunión creada:\n"
        f"Fecha: {date_str}\n"
        f"Hora: {start_str} - {end_str}\n"
        f"Título: {title}\n"
        f"Enlace: {link}"
    )


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------


def create_event(
    start_time_str: str,
    title: str,
    calendar_service,
    team_calendars: list[str],
    timezone_str: str,
    contact_email: str | None = None,
) -> tuple[int, str]:
    """
    Orchestrate the full event creation flow.

    Steps:
        1. Validate start_time and title.
        2. Check that the date is a business day.
        3. Check that the time is within business hours.
        4. Verify slot availability via FreeBusy API.
        5. Create the event via events.insert.
        6. Format and return the confirmation.

    Args:
        start_time_str: Start time in ISO 8601 format.
        title: Event title (1-200 characters).
        calendar_service: Authenticated Google Calendar API service instance.
        team_calendars: List of team member email addresses (attendees).
        timezone_str: IANA timezone string.

    Returns:
        Tuple of (status_code, response_message).
    """
    # 1. Validate start_time
    valid, error_msg, parsed_start = validate_start_time(start_time_str)
    if not valid:
        return (200, error_msg)

    # 2. Validate title
    valid_title, title_error = validate_title(title)
    if not valid_title:
        return (200, title_error)

    # 3. Check business day
    tz = ZoneInfo(timezone_str)
    local_start = parsed_start.astimezone(tz)
    if not is_business_day(local_start.date()):
        return (200, ERROR_WEEKEND)

    # 4. Check business hours
    if not is_within_business_hours(parsed_start, timezone_str):
        return (200, ERROR_OUTSIDE_HOURS)

    # 5. Compute end time (start + 30 minutes)
    end_time = parsed_start + datetime.timedelta(minutes=SLOT_DURATION_MINUTES)

    # 6. Verify slot availability via FreeBusy API
    freebusy_body = {
        "timeMin": parsed_start.isoformat(),
        "timeMax": end_time.isoformat(),
        "timeZone": timezone_str,
        "items": [{"id": email} for email in team_calendars],
    }

    freebusy_response = (
        calendar_service.freebusy().query(body=freebusy_body).execute()
    )

    # Check if any calendar has a busy period overlapping the requested slot
    for email in team_calendars:
        busy_list = freebusy_response["calendars"][email]["busy"]
        for period in busy_list:
            busy_start = datetime.datetime.fromisoformat(period["start"])
            busy_end = datetime.datetime.fromisoformat(period["end"])
            if slots_overlap(parsed_start, end_time, busy_start, busy_end):
                time_str = (
                    f"{local_start.strftime('%H:%M')} - "
                    f"{end_time.astimezone(tz).strftime('%H:%M')}"
                )
                return (200, ERROR_SLOT_TAKEN.format(time=time_str))

    # 7. Build event payload and create event
    payload = build_event_payload(
        parsed_start, end_time, title, team_calendars, timezone_str,
        contact_email=contact_email,
    )

    event_data = (
        calendar_service.events()
        .insert(calendarId="primary", body=payload, sendUpdates="all")
        .execute()
    )

    # 8. Format confirmation
    confirmation = format_event_confirmation(event_data, timezone_str)
    return (200, confirmation)
