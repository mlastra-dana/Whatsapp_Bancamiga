"""
Property-based tests for retry logic (handler module).

Properties covered:
- P9: Clasificación de reintentos por código HTTP
  For any HTTP status code, if the code is 429 or in the range 500-599, the retry logic
  must execute exactly one retry after 1-second wait; for any other error code (4xx except
  429), it must not retry.
  Validates: Requirements 7.3, 7.4

Feature: google-calendar-scheduling
"""
