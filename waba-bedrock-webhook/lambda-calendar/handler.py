"""
Calendar Lambda Handler — Entry point for the Bedrock Agent Action Group.

Processes invocations from the Bedrock Agent's Calendar Action Group,
routing requests to the appropriate action (check_availability or create_event).
Parses Action Group event parameters, builds responses in the expected Bedrock
format, and handles structured logging and top-level error handling.
"""

import json
import logging
import os
import time
import traceback

try:
    from googleapiclient.errors import HttpError
    from availability import check_availability
    from event_creator import create_event
    from google_auth_helper import get_calendar_service, GoogleAuthError
    _DEPENDENCIES_AVAILABLE = True
except (ImportError, Exception) as _import_err:
    import traceback as _tb
    print(f"IMPORT ERROR: {_import_err}")
    print(_tb.format_exc())
    _DEPENDENCIES_AVAILABLE = False
    HttpError = Exception  # fallback for type references

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

# ---------------------------------------------------------------------------
# Error messages (Spanish, matching design document)
# ---------------------------------------------------------------------------

ERROR_GENERIC = (
    "Ocurrió un error al procesar tu solicitud de calendario. "
    "Por favor intenta de nuevo."
)
ERROR_UNKNOWN_ACTION = (
    "La acción solicitada no es soportada por el servicio de calendario."
)
ERROR_SERVICE_UNAVAILABLE = (
    "El servicio de calendario no está disponible temporalmente. "
    "Por favor intenta más tarde."
)

# ---------------------------------------------------------------------------
# Module-level cache for warm-start reuse
# ---------------------------------------------------------------------------

_calendar_service = None
_team_calendars: list[str] | None = None
_timezone: str | None = None


# ---------------------------------------------------------------------------
# DynamoDB — save appointments
# ---------------------------------------------------------------------------

import boto3
import uuid
from datetime import datetime, timezone

_dynamodb_table = None


def _get_appointments_table():
    """Lazily initialize the DynamoDB appointments table resource."""
    global _dynamodb_table
    if _dynamodb_table is None:
        table_name = os.environ.get("APPOINTMENTS_TABLE_NAME")
        if table_name:
            dynamodb = boto3.resource("dynamodb")
            _dynamodb_table = dynamodb.Table(table_name)
    return _dynamodb_table


def _save_appointment(
    start_time_str: str,
    title: str,
    contact_name: str | None = None,
    contact_email: str | None = None,
    contact_company: str | None = None,
    contact_role: str | None = None,
    contact_phone: str | None = None,
    meeting_reason: str | None = None,
) -> None:
    """Save an appointment record to DynamoDB."""
    table = _get_appointments_table()
    if table is None:
        logger.warning("Appointments table not configured, skipping save")
        return

    try:
        item = {
            "appointment_id": str(uuid.uuid4()),
            "start_time": start_time_str,
            "title": title,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        if contact_name:
            item["contact_name"] = contact_name
        if contact_email:
            item["contact_email"] = contact_email
        if contact_company:
            item["contact_company"] = contact_company
        if contact_role:
            item["contact_role"] = contact_role
        if contact_phone:
            item["contact_phone"] = contact_phone
        if meeting_reason:
            item["meeting_reason"] = meeting_reason

        table.put_item(Item=item)
        logger.info("Appointment saved: %s", item["appointment_id"])
    except Exception:
        logger.exception("Failed to save appointment to DynamoDB")

# ---------------------------------------------------------------------------
# Retry logic
# ---------------------------------------------------------------------------

MAX_RETRIES = 1
RETRY_DELAY_SECONDS = 1


def should_retry(status_code: int) -> bool:
    """Return True if the HTTP status code warrants a retry.

    Retries are appropriate for:
    - 429 (Too Many Requests / rate limiting)
    - 5xx (server errors)

    All other 4xx errors are considered permanent and should NOT be retried.
    """
    if status_code == 429:
        return True
    if 500 <= status_code <= 599:
        return True
    return False


def _call_with_retry(fn):
    """Call *fn* and retry once on transient Google API errors.

    If *fn* raises ``HttpError`` with a status code that ``should_retry``
    deems transient (429 or 5xx), waits ``RETRY_DELAY_SECONDS`` and retries
    exactly once.  Permanent errors (other 4xx) are re-raised immediately.

    Args:
        fn: A zero-argument callable that performs a Google Calendar API call.

    Returns:
        The return value of *fn*.

    Raises:
        HttpError: If the retry also fails, or if the error is permanent.
    """
    try:
        return fn()
    except HttpError as exc:
        status = exc.resp.status
        if should_retry(status):
            logger.warning(
                "Transient Google API error (HTTP %d), retrying in %ds…",
                status,
                RETRY_DELAY_SECONDS,
            )
            time.sleep(RETRY_DELAY_SECONDS)
            return fn()
        raise


# ---------------------------------------------------------------------------
# Parameter parsing
# ---------------------------------------------------------------------------


def _parse_parameters(parameters: list[dict]) -> dict:
    """Convert the Action Group parameter list to a flat dict.

    Each element in *parameters* has the shape
    ``{"name": str, "type": str, "value": str}``.

    Returns:
        Dict mapping parameter name → value.
    """
    if not parameters:
        return {}
    return {p["name"]: p["value"] for p in parameters}


# ---------------------------------------------------------------------------
# Response builder
# ---------------------------------------------------------------------------


def _build_response(event: dict, status_code: int, body: str) -> dict:
    """Construct the Bedrock Action Group response envelope.

    Args:
        event: The original Action Group invocation event.
        status_code: HTTP-style status code for the response.
        body: Human-readable response message.

    Returns:
        Dict matching the Bedrock Action Group response schema.
    """
    return {
        "messageVersion": "1.0",
        "response": {
            "actionGroup": event.get("actionGroup", ""),
            "apiPath": event.get("apiPath", ""),
            "httpMethod": event.get("httpMethod", "POST"),
            "responseCode": status_code,
            "responseBody": {
                "application/json": {
                    "body": json.dumps({"message": body})
                }
            },
        },
    }


# ---------------------------------------------------------------------------
# Action routing
# ---------------------------------------------------------------------------


def _route_action(
    api_path: str,
    parameters: dict,
    calendar_service,
    team_calendars: list[str],
    timezone_str: str,
) -> tuple[int, str]:
    """Route the request to the appropriate calendar action.

    Wraps the underlying ``check_availability`` / ``create_event`` calls
    with retry logic so that transient Google API errors are retried once.

    Args:
        api_path: The API path from the Action Group event
            (e.g. ``"/check-availability"``).
        parameters: Parsed parameter dict.
        calendar_service: Authenticated Google Calendar API service.
        team_calendars: List of team member email addresses.
        timezone_str: IANA timezone string.

    Returns:
        Tuple of (status_code, response_message).
    """
    if api_path == "/check-availability":
        date_str = parameters.get("date", "")
        start_time = time.time()

        def _do_check():
            return check_availability(
                date_str, calendar_service, team_calendars, timezone_str,
            )

        try:
            result = _call_with_retry(_do_check)
        except HttpError as exc:
            logger.error(
                "Google API error during check_availability: HTTP %d",
                exc.resp.status,
            )
            return (200, ERROR_SERVICE_UNAVAILABLE)

        elapsed_ms = (time.time() - start_time) * 1000
        logger.info(
            "Google Calendar API call: endpoint=freebusy.query, "
            "response_time_ms=%.1f",
            elapsed_ms,
        )
        return result

    elif api_path == "/create-event":
        start_time_param = parameters.get("start_time", "")
        title = parameters.get("title", "")
        contact_email = parameters.get("contact_email", "") or None
        contact_name = parameters.get("contact_name", "") or None
        contact_company = parameters.get("contact_company", "") or None
        contact_role = parameters.get("contact_role", "") or None
        contact_phone = parameters.get("contact_phone", "") or None
        meeting_reason = parameters.get("meeting_reason", "") or None
        start_time = time.time()

        def _do_create():
            return create_event(
                start_time_param, title,
                calendar_service, team_calendars, timezone_str,
                contact_email=contact_email,
            )

        try:
            result = _call_with_retry(_do_create)
        except HttpError as exc:
            logger.error(
                "Google API error during create_event: HTTP %d",
                exc.resp.status,
            )
            return (200, ERROR_SERVICE_UNAVAILABLE)

        elapsed_ms = (time.time() - start_time) * 1000
        logger.info(
            "Google Calendar API call: endpoint=events.insert, "
            "response_time_ms=%.1f",
            elapsed_ms,
        )

        # Save appointment to DynamoDB if event was created successfully
        status_code, body = result
        if status_code == 200 and "Reunión creada" in body:
            _save_appointment(
                start_time_str=start_time_param,
                title=title,
                contact_name=contact_name,
                contact_email=contact_email,
                contact_company=contact_company,
                contact_role=contact_role,
                contact_phone=contact_phone,
                meeting_reason=meeting_reason,
            )

        return result

    else:
        logger.warning("Unknown action requested: %s", api_path)
        return (200, ERROR_UNKNOWN_ACTION)


# ---------------------------------------------------------------------------
# Lambda entry point
# ---------------------------------------------------------------------------


def lambda_handler(event: dict, context) -> dict:
    """Main entry point for the Calendar Lambda.

    Lazily initialises the Google Calendar service (cached for warm starts),
    parses the Action Group event, routes to the correct action, and builds
    the response.  A top-level try/except ensures that *any* unhandled
    exception is logged and a valid Action Group response is returned.

    Args:
        event: Bedrock Action Group invocation event.
        context: Lambda execution context (unused).

    Returns:
        Dict with the Bedrock Action Group response.
    """
    global _calendar_service, _team_calendars, _timezone

    try:
        # --- Check dependencies are available ------------------------------
        if not _DEPENDENCIES_AVAILABLE:
            logger.error("Calendar Lambda dependencies not available")
            return _build_response(event, 200, ERROR_SERVICE_UNAVAILABLE)

        # --- Lazy initialisation (cached across warm starts) ---------------
        if _calendar_service is None:
            secret_arn = os.environ["CREDENTIALS_SECRET_ARN"]
            impersonate_email = os.environ["IMPERSONATE_EMAIL"]
            _calendar_service = get_calendar_service(secret_arn, impersonate_email)

        if _team_calendars is None:
            _team_calendars = [
                email.strip()
                for email in os.environ["TEAM_CALENDARS"].split(",")
                if email.strip()
            ]

        if _timezone is None:
            _timezone = os.environ.get("TIMEZONE", "America/Mexico_City")

        # --- Parse event ---------------------------------------------------
        api_path = event.get("apiPath", "")
        parameters = _parse_parameters(event.get("parameters", []))

        # --- Structured logging (never log credentials) --------------------
        logger.info(
            "Calendar Lambda invoked: action=%s, parameters=%s",
            api_path,
            json.dumps(parameters),
        )

        # --- Route to action -----------------------------------------------
        status_code, body = _route_action(
            api_path, parameters,
            _calendar_service, _team_calendars, _timezone,
        )

        return _build_response(event, status_code, body)

    except Exception:
        logger.error(
            "Unhandled exception in Calendar Lambda:\n%s",
            traceback.format_exc(),
        )
        return _build_response(event, 200, ERROR_GENERIC)
