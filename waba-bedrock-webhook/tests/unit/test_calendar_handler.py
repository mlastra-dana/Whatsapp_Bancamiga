"""
Unit tests for the Calendar Lambda handler module (lambda-calendar/handler.py).

Tests cover:
- Full check_availability flow with mocked Google Calendar service
- Full create_event flow with mocked Google Calendar service
- Unknown action returns error message
- Parameter parsing from Action Group event format
- Response format matches Bedrock Action Group expected structure
- Retry on Google API transient error (429, 500)
- No retry on Google API permanent error (400, 403)
- Unhandled exception returns valid response with generic error

Requirements: 4.1, 4.2, 4.3, 4.4, 4.5, 7.1, 7.2, 7.3, 7.4, 7.5, 9.4
"""
