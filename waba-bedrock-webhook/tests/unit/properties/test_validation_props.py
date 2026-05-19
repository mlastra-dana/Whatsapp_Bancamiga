"""
Property-based tests for input validation (validators module).

Properties covered:
- P4: Rechazo de fechas de fin de semana
  For any date that falls on Saturday or Sunday, is_business_day must return False.
  Validates: Requirements 1.6, 2.5

- P5: Validación de horario laboral para creación de eventos
  For any datetime whose local hour (in the configured timezone) is before 9:00 or
  after 16:30, is_within_business_hours must return False.
  Validates: Requirements 2.4

- P11: Validación de formato de fecha
  For any string that is not a valid YYYY-MM-DD date, validate_date must return
  (False, error_message, None).
  Validates: Requirements 9.1

- P12: Validación de formato ISO 8601
  For any string that is not a valid ISO 8601 timestamp, validate_start_time must
  return (False, error_message, None).
  Validates: Requirements 9.2

- P13: Validación de título de evento
  For any empty string or string with more than 200 characters, validate_title must
  return (False, error_message). For any string of 1-200 characters, it must return
  (True, "").
  Validates: Requirements 9.3

Feature: google-calendar-scheduling
"""
