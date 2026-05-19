"""
Property-based tests for handler exception safety (handler module).

Properties covered:
- P10: Seguridad ante excepciones no controladas
  For any exception thrown during Action Group processing, the handler must catch it,
  log the full traceback, and return a valid Action Group response with a generic error
  message (never raise the exception to the caller).
  Validates: Requirements 7.5

Feature: google-calendar-scheduling
"""
