# Implementation Plan: Google Calendar Scheduling (Action Group)

## Overview

This plan implements a Bedrock Agent Action Group that enables Google Calendar scheduling through the existing WABA Bedrock Webhook system. The implementation follows an incremental bottom-up approach: first setting up the project structure and dependencies, then building Python modules one by one (validators → google_auth_helper → availability → event_creator → handler), then extending the CDK infrastructure, and finally adding tests. Each task builds on the previous ones to ensure no orphaned code.

## Tasks

- [x] 1. Set up project structure and dependencies
  - [x] 1.1 Create lambda-calendar directory and module files
    - Create `waba-bedrock-webhook/lambda-calendar/` directory with empty module files: `handler.py`, `availability.py`, `event_creator.py`, `google_auth_helper.py`, `validators.py`
    - Create `waba-bedrock-webhook/lambda-calendar/requirements.txt` with dependencies: `google-auth>=2.0`, `google-api-python-client>=2.0`, `boto3`
    - _Requirements: 5.1, 5.5_

  - [x] 1.2 Create test file structure for calendar modules
    - Create calendar-specific unit test files in `waba-bedrock-webhook/tests/unit/`: `test_calendar_handler.py`, `test_availability.py`, `test_event_creator.py`, `test_validators.py`, `test_google_auth_helper.py`
    - Create property test files in `waba-bedrock-webhook/tests/unit/properties/`: `test_slot_props.py`, `test_validation_props.py`, `test_event_props.py`, `test_response_props.py`, `test_retry_props.py`, `test_handler_props.py`
    - Update `waba-bedrock-webhook/tests/requirements-test.txt` to include `google-auth>=2.0` and `google-api-python-client>=2.0` if not already present
    - _Requirements: 5.1_

- [x] 2. Implement validators module
  - [x] 2.1 Implement validation functions in `lambda-calendar/validators.py`
    - Implement `validate_date(date_str: str) -> tuple[bool, str, datetime.date | None]` that validates YYYY-MM-DD format and returns parsed date or error message
    - Implement `validate_start_time(start_time_str: str) -> tuple[bool, str, datetime | None]` that validates ISO 8601 format and returns parsed datetime or error message
    - Implement `validate_title(title: str) -> tuple[bool, str]` that validates title is 1-200 characters
    - Implement `is_business_day(date: datetime.date) -> bool` that returns True for Monday-Friday
    - Implement `is_within_business_hours(dt: datetime, timezone_str: str) -> bool` that returns True if the datetime falls within 9:00-16:30 in the configured timezone (16:30 because a 30-minute event must end by 17:00)
    - Implement `slots_overlap(slot_start, slot_end, busy_start, busy_end) -> bool` that returns True if `slot_start < busy_end AND slot_end > busy_start`
    - _Requirements: 1.5, 1.6, 2.4, 2.5, 6.1, 9.1, 9.2, 9.3_

  - [ ]* 2.2 Write property test for weekend rejection (Property 4)
    - **Property 4: Rechazo de fechas de fin de semana**
    - For any date that falls on Saturday or Sunday, `is_business_day` must return False
    - Use Hypothesis to generate random Saturday/Sunday dates
    - **Validates: Requirements 1.6, 2.5**

  - [ ]* 2.3 Write property test for business hours validation (Property 5)
    - **Property 5: Validación de horario laboral para creación de eventos**
    - For any datetime whose local hour (in the configured timezone) is before 9:00 or after 16:30, `is_within_business_hours` must return False
    - Use Hypothesis to generate random datetimes outside business hours
    - **Validates: Requirements 2.4**

  - [ ]* 2.4 Write property test for date validation (Property 11)
    - **Property 11: Validación de formato de fecha**
    - For any string that is not a valid YYYY-MM-DD date (empty strings, wrong formats like DD/MM/YYYY, impossible dates like 2025-02-30), `validate_date` must return `(False, error_message, None)`
    - Use Hypothesis to generate random invalid date strings
    - **Validates: Requirements 9.1**

  - [ ]* 2.5 Write property test for ISO 8601 validation (Property 12)
    - **Property 12: Validación de formato ISO 8601**
    - For any string that is not a valid ISO 8601 timestamp (empty strings, dates without time, wrong formats), `validate_start_time` must return `(False, error_message, None)`
    - Use Hypothesis to generate random invalid timestamp strings
    - **Validates: Requirements 9.2**

  - [ ]* 2.6 Write property test for title validation (Property 13)
    - **Property 13: Validación de título de evento**
    - For any empty string or string with more than 200 characters, `validate_title` must return `(False, error_message)`. For any string of 1-200 characters, it must return `(True, "")`
    - Use Hypothesis to generate strings of various lengths
    - **Validates: Requirements 9.3**

  - [ ]* 2.7 Write unit tests for validators module
    - Test `validate_date` with valid dates, invalid formats (DD/MM/YYYY), impossible dates (2025-02-30), empty string
    - Test `validate_start_time` with valid ISO 8601 timestamps, invalid formats, empty string
    - Test `validate_title` with valid titles, empty string, 201-character string
    - Test `is_business_day` with weekdays and weekends
    - Test `is_within_business_hours` with times inside and outside 9:00-16:30
    - Test `slots_overlap` with overlapping and non-overlapping intervals
    - _Requirements: 9.1, 9.2, 9.3, 1.5, 1.6, 2.4, 2.5_

- [x] 3. Implement Google auth helper module
  - [x] 3.1 Implement authentication functions in `lambda-calendar/google_auth_helper.py`
    - Implement module-level cache variables `_cached_credentials` and `_cached_service` for warm-start reuse
    - Implement `_load_credentials_from_secrets_manager(secret_arn: str) -> dict` that reads and parses the JSON credentials from Secrets Manager using boto3
    - Implement `_build_delegated_credentials(credentials_info: dict, impersonate_email: str) -> Credentials` that constructs Google service account credentials with domain-wide delegation using `google.oauth2.service_account.Credentials.from_service_account_info` with scopes `['https://www.googleapis.com/auth/calendar']` and `with_subject(impersonate_email)`
    - Implement `get_calendar_service(secret_arn: str, impersonate_email: str) -> Resource` that returns a cached or newly built `googleapiclient.discovery.build('calendar', 'v3', credentials=...)` instance
    - Define `GoogleAuthError` exception class for authentication failures
    - Log errors without exposing credentials in logs
    - _Requirements: 3.1, 3.2, 3.3, 3.4, 3.5_

  - [ ]* 3.2 Write unit tests for google_auth_helper module
    - Test successful authentication with mocked Secrets Manager and Google auth
    - Test credential caching behavior (second call reuses cached service)
    - Test Secrets Manager read failure raises GoogleAuthError with error logged
    - Test Google authentication failure raises GoogleAuthError with error logged (no credentials in logs)
    - Use moto to mock Secrets Manager
    - _Requirements: 3.1, 3.2, 3.3, 3.4, 3.5_

- [x] 4. Implement availability module
  - [x] 4.1 Implement availability functions in `lambda-calendar/availability.py`
    - Implement `generate_candidate_slots(date: datetime.date, timezone_str: str) -> list[tuple[datetime, datetime]]` that generates 16 slots of 30 minutes aligned to :00 and :30 within 9:00-17:00 in the configured timezone
    - Implement `filter_available_slots(candidate_slots, busy_periods, now=None) -> list[tuple[datetime, datetime]]` that excludes slots overlapping with any busy period from any calendar, and excludes slots whose start time has already passed if `now` is provided
    - Implement `format_slots_response(slots, timezone_str) -> str` that formats slots as a numbered list with HH:MM 24-hour format (e.g., "1. 09:00 - 09:30")
    - Implement `check_availability(date_str, calendar_service, team_calendars, timezone_str) -> tuple[int, str]` that orchestrates: validate date, check business day, call FreeBusy API, generate candidates, filter available, format response. Handle "no slots available" and "today with past slots" cases
    - _Requirements: 1.1, 1.2, 1.3, 1.4, 1.5, 1.6, 1.7, 6.1, 6.2, 6.3, 6.4, 6.5_

  - [ ]* 4.2 Write property test for slot generation invariants (Property 1)
    - **Property 1: Invariantes de generación de slots**
    - For any business day and any valid timezone, `generate_candidate_slots` must return exactly 16 slots, each of exactly 30 minutes duration, aligned to :00 and :30, with start ≥ 9:00 and end ≤ 17:00 in the configured timezone
    - Use Hypothesis to generate random weekday dates and timezone strings
    - **Validates: Requirements 1.5, 6.1, 6.4**

  - [ ]* 4.3 Write property test for slot filtering correctness (Property 2)
    - **Property 2: Correctitud del filtrado de slots por disponibilidad**
    - For any set of candidate slots and any set of busy periods from multiple calendars, `filter_available_slots` must return only slots that do not overlap with any busy period from any calendar
    - Use Hypothesis to generate random slots and busy periods
    - **Validates: Requirements 1.2, 6.2, 6.3**

  - [ ]* 4.4 Write property test for past slot exclusion (Property 3)
    - **Property 3: Exclusión de slots pasados**
    - For any "now" moment within business hours and any set of candidate slots, `filter_available_slots` with the `now` parameter must return only slots whose start time is ≥ "now"
    - Use Hypothesis to generate random "now" datetimes within business hours
    - **Validates: Requirements 6.5**

  - [ ]* 4.5 Write property test for slot response format (Property 7)
    - **Property 7: Formato de respuesta de slots disponibles**
    - For any non-empty list of slots, `format_slots_response` must produce a string where each slot appears as a sequentially numbered line (starting at 1) with start and end times in HH:MM 24-hour format, and the number of lines equals the number of slots
    - Use Hypothesis to generate random lists of slot tuples
    - **Validates: Requirements 1.3, 8.1, 8.4**

  - [ ]* 4.6 Write unit tests for availability module
    - Test `check_availability` with a date where all slots are free
    - Test `check_availability` with a fully booked date (no slots available)
    - Test `check_availability` with a partially booked date
    - Test `check_availability` with today's date (past slots excluded)
    - Test `check_availability` with a weekend date (rejected)
    - Test `generate_candidate_slots` returns correct number and alignment
    - Test `filter_available_slots` with various busy period configurations
    - _Requirements: 1.1, 1.2, 1.3, 1.4, 1.5, 1.6, 6.1, 6.2, 6.3, 6.5_

- [x] 5. Implement event creator module
  - [x] 5.1 Implement event creation functions in `lambda-calendar/event_creator.py`
    - Implement `build_event_payload(start_time, end_time, title, attendees, timezone_str) -> dict` that constructs the Google Calendar events.insert payload with summary, start/end dateTime and timeZone, and attendees list
    - Implement `format_event_confirmation(event_data, timezone_str) -> str` that formats the confirmation with date, start time (HH:MM), end time (HH:MM), title, and htmlLink
    - Implement `create_event(start_time_str, title, calendar_service, team_calendars, timezone_str) -> tuple[int, str]` that orchestrates: validate start_time and title, check business day and hours, verify slot availability via FreeBusy, create event via events.insert, format confirmation. Handle "slot already taken" case
    - _Requirements: 2.1, 2.2, 2.3, 2.4, 2.5, 2.6, 2.7_

  - [ ]* 5.2 Write property test for event payload construction (Property 6)
    - **Property 6: Construcción correcta del payload de evento**
    - For any valid start time, title of 1-200 characters, and non-empty list of attendee emails, `build_event_payload` must produce a dict with: `summary` equal to the title, `start.dateTime` equal to the start time in ISO 8601, `end.dateTime` equal to start time + exactly 30 minutes, `start.timeZone` and `end.timeZone` equal to the configured timezone, and `attendees` as a list of dicts with `email` for each attendee
    - Use Hypothesis to generate random start times, titles, and email lists
    - **Validates: Requirements 2.2**

  - [ ]* 5.3 Write property test for event confirmation format (Property 8)
    - **Property 8: Formato de confirmación de evento**
    - For any events.insert response containing `htmlLink`, `start.dateTime`, `end.dateTime`, and `summary`, `format_event_confirmation` must produce a string containing the date, start time in HH:MM format, end time in HH:MM format, the event title, and the HTML link
    - Use Hypothesis to generate random event response dicts
    - **Validates: Requirements 2.3, 8.2**

  - [ ]* 5.4 Write unit tests for event creator module
    - Test successful event creation with confirmation message
    - Test event creation when slot is already taken (conflict)
    - Test event creation with time outside business hours (rejected)
    - Test event creation on weekend (rejected)
    - Test `build_event_payload` produces correct structure
    - Test `format_event_confirmation` includes all required fields
    - _Requirements: 2.1, 2.2, 2.3, 2.4, 2.5, 2.6, 2.7_

- [x] 6. Implement Calendar Lambda handler
  - [x] 6.1 Implement handler functions in `lambda-calendar/handler.py`
    - Implement `_parse_parameters(parameters: list[dict]) -> dict` that converts the Action Group parameter list to a dict
    - Implement `_build_response(event: dict, status_code: int, body: str) -> dict` that constructs the Bedrock Action Group response format with `messageVersion`, `response.actionGroup`, `response.apiPath`, `response.httpMethod`, `response.responseCode`, and `response.responseBody`
    - Implement `_route_action(api_path: str, parameters: dict) -> tuple[int, str]` that routes `/check-availability` to `check_availability` and `/create-event` to `create_event`, returning error for unknown actions
    - Implement `lambda_handler(event, context) -> dict` that lazily initializes the Google Calendar service (cached for warm starts), parses the Action Group event, routes to the correct action, and builds the response
    - Add structured logging: log each invocation with action name and parameters (never log credentials), log each Google Calendar API call with endpoint and response time in milliseconds
    - Implement retry logic for Google API calls: retry once after 1-second wait for HTTP 429 or 5xx errors; do not retry for other 4xx errors
    - Wrap all processing in a top-level try/except that catches any unhandled exception, logs the full traceback, and returns a valid Action Group response with a generic error message
    - _Requirements: 4.1, 4.2, 4.3, 4.4, 4.5, 7.1, 7.2, 7.3, 7.4, 7.5, 8.3, 9.4_

  - [ ]* 6.2 Write property test for retry classification (Property 9)
    - **Property 9: Clasificación de reintentos por código HTTP**
    - For any HTTP status code, if the code is 429 or in the range 500-599, the retry logic must execute exactly one retry after 1-second wait; for any other error code (4xx except 429), it must not retry
    - Use Hypothesis to generate random HTTP status codes (100-599)
    - **Validates: Requirements 7.3, 7.4**

  - [ ]* 6.3 Write property test for exception safety (Property 10)
    - **Property 10: Seguridad ante excepciones no controladas**
    - For any exception thrown during Action Group processing, the handler must catch it, log the full traceback, and return a valid Action Group response with a generic error message (never raise the exception to the caller)
    - Use Hypothesis to inject random exceptions
    - **Validates: Requirements 7.5**

  - [ ]* 6.4 Write unit tests for Calendar Lambda handler
    - Test full `check_availability` flow with mocked Google Calendar service
    - Test full `create_event` flow with mocked Google Calendar service
    - Test unknown action returns error message
    - Test parameter parsing from Action Group event format
    - Test response format matches Bedrock Action Group expected structure
    - Test retry on Google API transient error (429, 500)
    - Test no retry on Google API permanent error (400, 403)
    - Test unhandled exception returns valid response with generic error
    - _Requirements: 4.1, 4.2, 4.3, 4.4, 4.5, 7.1, 7.2, 7.3, 7.4, 7.5, 9.4_

- [x] 7. Checkpoint — Verify all Python modules
  - Ensure all unit tests and property tests pass for validators, google_auth_helper, availability, event_creator, and handler modules. Ask the user if questions arise.

- [x] 8. Extend CDK infrastructure for Calendar Action Group
  - [x] 8.1 Add Secrets Manager secret and Calendar Lambda to CDK stack
    - In `waba-bedrock-webhook/infra/lib/waba-bedrock-stack.ts`, add new CfnParameters: `TeamCalendars` (comma-separated email list), `Timezone` (default: `America/Mexico_City`), `ImpersonateEmail` (email for domain-wide delegation)
    - Create a `secretsmanager.Secret` for the Google Service Account credentials JSON
    - Create a new Lambda function `CalendarHandler` with Python 3.12 runtime, 30-second timeout, 256 MB memory, pointing to `lambda-calendar/` directory as code
    - Configure environment variables: `CREDENTIALS_SECRET_ARN`, `TEAM_CALENDARS`, `TIMEZONE`, `IMPERSONATE_EMAIL`
    - Grant the Calendar Lambda IAM permission to read the Secrets Manager secret
    - Package Python dependencies (`google-auth`, `google-api-python-client`) with the Lambda code
    - _Requirements: 5.1, 5.2, 5.3, 5.4, 5.5, 5.6_

  - [x] 8.2 Add Bedrock Agent Action Group to CDK stack
    - Create a `bedrock.CfnAgentActionGroup` associated with the existing Bedrock Agent, with the OpenAPI schema defined inline describing the `check_availability` and `create_event` actions
    - Configure the Calendar Lambda as the Action Group executor
    - Grant Bedrock service permission to invoke the Calendar Lambda (`lambda:InvokeFunction`)
    - _Requirements: 4.1, 4.2, 4.3, 4.4, 4.5_

  - [x] 8.3 Add CloudFormation outputs for new resources
    - Export the Secrets Manager secret ARN for credential configuration
    - Export the Calendar Lambda function name for monitoring
    - _Requirements: 5.2_

  - [ ]* 8.4 Extend CDK infrastructure tests
    - In `waba-bedrock-webhook/tests/infra/test_stack.test.ts`, add tests verifying:
    - Calendar Lambda exists with Python 3.12 runtime, 30s timeout, 256MB memory
    - Secrets Manager secret is created
    - Calendar Lambda has IAM permission to read the secret
    - Bedrock Agent Action Group is created with correct action group name
    - Action Group is associated with the existing Bedrock Agent
    - Bedrock has permission to invoke the Calendar Lambda
    - Calendar Lambda environment variables are configured correctly
    - _Requirements: 4.1, 4.2, 4.5, 5.1, 5.2, 5.3, 5.4, 5.6_

- [x] 9. Final checkpoint — Ensure all tests pass
  - Run all Python tests (pytest) and CDK tests (Jest). Ensure all unit tests, property tests, and infrastructure tests pass. Ask the user if questions arise.

## Notes

- Tasks marked with `*` are optional and can be skipped for faster MVP
- Each task references specific requirements for traceability
- Checkpoints ensure incremental validation
- Property tests validate all 13 universal correctness properties from the design document (Properties 1–13)
- Unit tests validate specific examples and edge cases
- Python modules are implemented bottom-up (validators → google_auth_helper → availability → event_creator → handler) to avoid forward dependencies
- The handler (task 6) wires all modules together as the final integration step
- CDK infrastructure (task 8) is done after Python modules so the Lambda code is ready for deployment
- The existing CDK stack (`WabaBedrockStack`) is extended, not replaced — all existing resources remain unchanged
