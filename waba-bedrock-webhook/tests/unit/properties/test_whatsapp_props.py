"""
Property-based tests for WhatsApp client.

Property 8: Construcción correcta del payload de envío de WhatsApp
Property 9: Reintento en errores transitorios

Validates: Requirements 5.2, 5.4
"""

from hypothesis import given, settings
from hypothesis import strategies as st

from whatsapp import build_message_payload


# ---------------------------------------------------------------------------
# Strategies
# ---------------------------------------------------------------------------

# Phone numbers: strings of digits with an optional leading '+' prefix.
phone_number_strategy = st.from_regex(r"\+?\d{1,15}", fullmatch=True)

# Message body: arbitrary non-empty text strings.
message_text_strategy = st.text(min_size=1, max_size=500)


# ---------------------------------------------------------------------------
# Property 8 — WhatsApp message payload construction
# ---------------------------------------------------------------------------


class TestWhatsAppPayloadConstruction:
    """
    **Validates: Requirements 5.2**

    For any destination phone number and response text, the constructed
    payload must contain ``messaging_product`` = "whatsapp",
    ``recipient_type`` = "individual", ``to`` = destination number,
    ``type`` = "text", and ``text.body`` = response text.
    """

    @given(to=phone_number_strategy, text=message_text_strategy)
    @settings(max_examples=100, deadline=None)
    def test_payload_contains_correct_messaging_product(self, to: str, text: str):
        """messaging_product must always be 'whatsapp'."""
        payload = build_message_payload(to, text)
        assert payload["messaging_product"] == "whatsapp"

    @given(to=phone_number_strategy, text=message_text_strategy)
    @settings(max_examples=100, deadline=None)
    def test_payload_contains_correct_recipient_type(self, to: str, text: str):
        """recipient_type must always be 'individual'."""
        payload = build_message_payload(to, text)
        assert payload["recipient_type"] == "individual"

    @given(to=phone_number_strategy, text=message_text_strategy)
    @settings(max_examples=100, deadline=None)
    def test_payload_to_matches_destination_number(self, to: str, text: str):
        """to field must equal the destination phone number."""
        payload = build_message_payload(to, text)
        assert payload["to"] == to

    @given(to=phone_number_strategy, text=message_text_strategy)
    @settings(max_examples=100, deadline=None)
    def test_payload_type_is_text(self, to: str, text: str):
        """type must always be 'text'."""
        payload = build_message_payload(to, text)
        assert payload["type"] == "text"

    @given(to=phone_number_strategy, text=message_text_strategy)
    @settings(max_examples=100, deadline=None)
    def test_payload_text_body_matches_response(self, to: str, text: str):
        """text.body must equal the response text."""
        payload = build_message_payload(to, text)
        assert payload["text"]["body"] == text

    @given(to=phone_number_strategy, text=message_text_strategy)
    @settings(max_examples=100, deadline=None)
    def test_payload_has_exactly_expected_keys(self, to: str, text: str):
        """Payload must contain exactly the five required top-level keys."""
        payload = build_message_payload(to, text)
        expected_keys = {"messaging_product", "recipient_type", "to", "type", "text"}
        assert set(payload.keys()) == expected_keys


# ---------------------------------------------------------------------------
# Property 9 — Transient error retry behaviour
# ---------------------------------------------------------------------------

from unittest.mock import MagicMock, patch

from whatsapp import WhatsAppClient, WhatsAppSendError


# Strategies for HTTP status codes
transient_status_strategy = st.one_of(
    st.just(429),
    st.integers(min_value=500, max_value=599),
)

non_transient_error_status_strategy = st.one_of(
    st.integers(min_value=400, max_value=428),
    st.integers(min_value=430, max_value=499),
)


def _make_mock_response(status: int) -> MagicMock:
    """Create a mock urllib3 response with the given status code."""
    resp = MagicMock()
    resp.status = status
    resp.data = b'{"error": "mock"}'
    return resp


class TestTransientErrorRetry:
    """
    **Validates: Requirements 5.4**

    For any HTTP status code returned by the WhatsApp Cloud API, if the
    code is 429 or in the range 500-599, the client must retry exactly
    once; for any other error code (4xx except 429), it must not retry.
    """

    @given(status=transient_status_strategy)
    @settings(max_examples=100, deadline=None)
    def test_transient_errors_trigger_exactly_one_retry(self, status: int):
        """Transient errors (429, 5xx) must cause exactly one retry (2 calls total)."""
        client = WhatsAppClient("phone-id", "token")

        mock_response = _make_mock_response(status)

        with patch.object(client, "_http") as mock_http, \
             patch("whatsapp.time.sleep") as mock_sleep:
            mock_http.request.return_value = mock_response

            try:
                client.send_text_message("+1234567890", "hello")
            except WhatsAppSendError:
                pass  # Expected — both attempts return the error status

            assert mock_http.request.call_count == 2, (
                f"Expected 2 calls (initial + retry) for transient status {status}, "
                f"got {mock_http.request.call_count}"
            )
            mock_sleep.assert_called_once_with(1)

    @given(status=non_transient_error_status_strategy)
    @settings(max_examples=100, deadline=None)
    def test_non_transient_errors_do_not_retry(self, status: int):
        """Non-transient errors (4xx except 429) must not trigger a retry (1 call total)."""
        client = WhatsAppClient("phone-id", "token")

        mock_response = _make_mock_response(status)

        with patch.object(client, "_http") as mock_http, \
             patch("whatsapp.time.sleep") as mock_sleep:
            mock_http.request.return_value = mock_response

            try:
                client.send_text_message("+1234567890", "hello")
            except WhatsAppSendError:
                pass  # Expected — the error status triggers the exception

            assert mock_http.request.call_count == 1, (
                f"Expected 1 call (no retry) for non-transient status {status}, "
                f"got {mock_http.request.call_count}"
            )
            mock_sleep.assert_not_called()
