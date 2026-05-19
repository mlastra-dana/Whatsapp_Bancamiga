"""
Property-based tests for event creation (event_creator module).

Properties covered:
- P6: Construcción correcta del payload de evento
  For any valid start time, title of 1-200 characters, and non-empty list of attendee
  emails, build_event_payload must produce a dict with: summary equal to the title,
  start.dateTime equal to the start time in ISO 8601, end.dateTime equal to start time
  + exactly 30 minutes, start.timeZone and end.timeZone equal to the configured timezone,
  and attendees as a list of dicts with email for each attendee.
  Validates: Requirements 2.2

- P8: Formato de confirmación de evento
  For any events.insert response containing htmlLink, start.dateTime, end.dateTime, and
  summary, format_event_confirmation must produce a string containing the date, start time
  in HH:MM format, end time in HH:MM format, the event title, and the HTML link.
  Validates: Requirements 2.3, 8.2

Feature: google-calendar-scheduling
"""
