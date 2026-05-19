"""
Property-based tests for slot generation and filtering (availability module).

Properties covered:
- P1: Invariantes de generación de slots
  For any business day and valid timezone, generate_candidate_slots must return exactly
  16 slots, each of exactly 30 minutes duration, aligned to :00 and :30, with
  start >= 9:00 and end <= 17:00 in the configured timezone.
  Validates: Requirements 1.5, 6.1, 6.4

- P2: Correctitud del filtrado de slots por disponibilidad
  For any set of candidate slots and any set of busy periods from multiple calendars,
  filter_available_slots must return only slots that do not overlap with any busy period
  from any calendar.
  Validates: Requirements 1.2, 6.2, 6.3

- P3: Exclusión de slots pasados
  For any "now" moment within business hours and any set of candidate slots,
  filter_available_slots with the now parameter must return only slots whose start
  time is >= "now".
  Validates: Requirements 6.5

Feature: google-calendar-scheduling
"""
