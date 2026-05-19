"""
Property-based tests for response formatting (availability module).

Properties covered:
- P7: Formato de respuesta de slots disponibles
  For any non-empty list of slots, format_slots_response must produce a string where
  each slot appears as a sequentially numbered line (starting at 1) with start and end
  times in HH:MM 24-hour format, and the number of lines equals the number of slots.
  Validates: Requirements 1.3, 8.1, 8.4

Feature: google-calendar-scheduling
"""
