"""
Property-based tests for logging and exception safety.

Property 10: Logging que preserva privacidad
Property 11: Seguridad ante excepciones no controladas

Validates: Requirements 10.1, 10.4
"""

import json
import logging
from unittest.mock import MagicMock, patch

from hypothesis import given, settings
from hypothesis import strategies as st

import handler
from handler import handle_message, lambda_handler


# ---------------------------------------------------------------------------
# Strategies
# ---------------------------------------------------------------------------

# Phone numbers: digits only, 10-15 chars (E.164 without +)
phone_strategy = st.text(
    alphabet="0123456789",
    min_size=10,
    max_size=15,
)

# Message text with a unique marker prefix so we can reliably detect it in logs.
# The marker is a UUID-like hex string that won't appear in log format strings.
_MARKER = "PRIVMSG_"

text_strategy = st.builds(
    lambda suffix: _MARKER + suffix,
    st.text(
        alphabet=st.characters(whitelist_categories=("L", "N")),
        min_size=5,
        max_size=100,
    ),
)

# Message IDs
message_id_strategy = st.from_regex(r"wamid\.[A-Za-z0-9]{5,30}", fullmatch=True)


def _build_payload(from_number: str, body: str, msg_id: str) -> dict:
    """Build a valid WhatsApp Cloud API payload with a single text message."""
    return {
        "object": "whatsapp_business_account",
        "entry": [
            {
                "id": "BUSINESS_ACCOUNT_ID",
                "changes": [
                    {
                        "value": {
                            "messaging_product": "whatsapp",
                            "metadata": {
                                "display_phone_number": "15550000000",
                                "phone_number_id": "123456789",
                            },
                            "contacts": [
                                {
                                    "profile": {"name": "Test User"},
                                    "wa_id": from_number,
                                }
                            ],
                            "messages": [
                                {
                                    "from": from_number,
                                    "id": msg_id,
                                    "timestamp": "1700000000",
                                    "type": "text",
                                    "text": {"body": body},
                                }
                            ],
                        },
                        "field": "messages",
                    }
                ],
            }
        ],
    }


def _make_post_event(body: dict) -> dict:
    """Wrap a payload dict into an API Gateway POST event."""
    return {
        "httpMethod": "POST",
        "body": json.dumps(body),
    }


class _LogCapture(logging.Handler):
    """Lightweight log handler that collects formatted records in memory."""

    def __init__(self):
        super().__init__()
        self.records: list[logging.LogRecord] = []

    def emit(self, record: logging.LogRecord):
        self.records.append(record)

    def get_all_text(self) -> str:
        """Return all log messages (including exc_text) joined as one string."""
        parts = []
        for r in self.records:
            parts.append(r.getMessage())
            if r.exc_text:
                parts.append(r.exc_text)
        return " ".join(parts)

    def clear(self):
        self.records.clear()


# ---------------------------------------------------------------------------
# Property 10 — Logging que preserva privacidad
# ---------------------------------------------------------------------------


class TestLoggingPrivacy:
    """
    **Validates: Requirements 10.1**

    For any incoming message with arbitrary text content, the generated
    logs must contain the sender's phone number and message type but must
    never contain the message text content.
    """

    @given(
        from_number=phone_strategy,
        msg_text=text_strategy,
        msg_id=message_id_strategy,
    )
    @settings(max_examples=100, deadline=None)
    def test_logs_contain_phone_and_type_but_not_message_content(
        self, from_number: str, msg_text: str, msg_id: str
    ):
        """Logs include phone number and message type, never message text."""
        payload = _build_payload(from_number, msg_text, msg_id)

        # Mock all module-level service dependencies
        mock_session = MagicMock()
        mock_session.get_or_create_session.return_value = "sess-test-123"

        mock_prompt = MagicMock()
        mock_prompt.get_prompt.return_value = "System prompt"

        mock_bedrock = MagicMock()
        mock_bedrock.invoke.return_value = "Agent response"

        mock_whatsapp = MagicMock()

        # Capture logs from the handler logger
        log_capture = _LogCapture()
        handler_logger = logging.getLogger("handler")
        handler_logger.addHandler(log_capture)
        handler_logger.setLevel(logging.DEBUG)

        try:
            log_capture.clear()

            with (
                patch.object(handler, "session_manager", mock_session),
                patch.object(handler, "prompt_reader", mock_prompt),
                patch.object(handler, "bedrock_client", mock_bedrock),
                patch.object(handler, "whatsapp_client", mock_whatsapp),
            ):
                handle_message(payload)

            all_log_text = log_capture.get_all_text()

            # Logs MUST contain the phone number
            assert from_number in all_log_text, (
                f"Expected phone number {from_number!r} in logs, "
                f"got: {all_log_text!r}"
            )

            # Logs MUST contain the message type
            assert "text" in all_log_text.lower(), (
                f"Expected message type 'text' in logs, got: {all_log_text!r}"
            )

            # Logs MUST NOT contain the message text content
            assert msg_text not in all_log_text, (
                f"Message content {msg_text!r} leaked into logs: "
                f"{all_log_text!r}"
            )
        finally:
            handler_logger.removeHandler(log_capture)


# ---------------------------------------------------------------------------
# Property 11 — Seguridad ante excepciones no controladas
# ---------------------------------------------------------------------------

# Strategy: generate exception types and messages
exception_type_strategy = st.sampled_from([
    ValueError,
    TypeError,
    RuntimeError,
    KeyError,
    AttributeError,
    IOError,
    ZeroDivisionError,
    IndexError,
    OverflowError,
    ConnectionError,
])

exception_message_strategy = st.text(
    alphabet=st.characters(whitelist_categories=("L", "N", "P", "Z")),
    min_size=1,
    max_size=100,
)


class TestExceptionSafety:
    """
    **Validates: Requirements 10.4**

    For any exception thrown during message processing, the handler must
    catch it, log the full traceback, and return HTTP 200.
    """

    @given(
        exc_type=exception_type_strategy,
        exc_msg=exception_message_strategy,
    )
    @settings(max_examples=100, deadline=None)
    def test_unhandled_exception_returns_200_and_logs_traceback(
        self, exc_type: type, exc_msg: str
    ):
        """lambda_handler catches any exception, logs traceback, returns 200."""
        # Build a valid POST event so the handler reaches message processing
        payload = _build_payload("5491100000000", "Hello", "wamid.test123")
        event = _make_post_event(payload)

        # Make _init_services set up mocks, then have session_manager raise
        mock_session = MagicMock()
        mock_session.get_or_create_session.side_effect = exc_type(exc_msg)

        mock_prompt = MagicMock()
        mock_bedrock = MagicMock()
        mock_whatsapp = MagicMock()

        def fake_init():
            handler.session_manager = mock_session
            handler.prompt_reader = mock_prompt
            handler.bedrock_client = mock_bedrock
            handler.whatsapp_client = mock_whatsapp

        # Capture logs from the handler logger
        log_capture = _LogCapture()
        handler_logger = logging.getLogger("handler")
        handler_logger.addHandler(log_capture)
        handler_logger.setLevel(logging.DEBUG)

        try:
            log_capture.clear()

            with patch.object(handler, "_init_services", side_effect=fake_init):
                result = lambda_handler(event, None)

            # Must return HTTP 200
            assert result["statusCode"] == 200, (
                f"Expected HTTP 200, got {result['statusCode']}"
            )

            # Must log the traceback — look for the exception type name or
            # "Traceback" in the log records (logger.exception includes traceback)
            all_log_text = log_capture.get_all_text()

            exc_type_name = exc_type.__name__
            has_traceback = (
                "Traceback" in all_log_text
                or "traceback" in all_log_text
                or exc_type_name in all_log_text
            )
            assert has_traceback, (
                f"Expected traceback or {exc_type_name!r} in logs, "
                f"got: {all_log_text!r}"
            )
        finally:
            handler_logger.removeHandler(log_capture)
