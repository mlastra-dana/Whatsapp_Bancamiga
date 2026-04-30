"""
Unit tests for the WhatsApp Cloud API client module.

Tests message sending, retry logic for transient errors,
and payload construction.
"""

import json
from unittest.mock import MagicMock, patch

import pytest

from whatsapp import WhatsAppClient, WhatsAppSendError


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_response(status: int, body: dict | None = None) -> MagicMock:
    """Build a fake urllib3 response with the given status and JSON body."""
    resp = MagicMock()
    resp.status = status
    payload = body if body is not None else {"messages": [{"id": "wamid.abc"}]}
    resp.data = json.dumps(payload).encode("utf-8")
    return resp


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestWhatsAppClientSendSuccess:
    """Requirement 5.1, 5.2 — successful message send."""

    def test_send_text_message_returns_parsed_json(self):
        """A 200 response should be parsed and returned as a dict."""
        client = WhatsAppClient("phone-id-1", "tok-abc")
        expected_body = {"messages": [{"id": "wamid.xyz"}]}

        with patch.object(client._http, "request", return_value=_make_response(200, expected_body)) as mock_req, \
             patch("time.sleep"):
            result = client.send_text_message("+5491100000000", "Hola")

        assert result == expected_body
        # Should have been called exactly once (no retry needed)
        assert mock_req.call_count == 1

    def test_send_text_message_posts_correct_url_and_headers(self):
        """The request must target the v21.0 messages endpoint with auth header."""
        client = WhatsAppClient("12345", "my-token")

        with patch.object(client._http, "request", return_value=_make_response(200)) as mock_req, \
             patch("time.sleep"):
            client.send_text_message("+1234567890", "Hello")

        args, kwargs = mock_req.call_args
        assert args[0] == "POST"
        assert args[1] == "https://graph.facebook.com/v21.0/12345/messages"
        assert kwargs["headers"]["Authorization"] == "Bearer my-token"
        assert kwargs["headers"]["Content-Type"] == "application/json"

    def test_send_text_message_posts_correct_payload(self):
        """The JSON body must match the WhatsApp Cloud API schema."""
        client = WhatsAppClient("phone-1", "tok")

        with patch.object(client._http, "request", return_value=_make_response(200)) as mock_req, \
             patch("time.sleep"):
            client.send_text_message("+549111", "Hola mundo")

        _, kwargs = mock_req.call_args
        sent_payload = json.loads(kwargs["body"])
        assert sent_payload == {
            "messaging_product": "whatsapp",
            "recipient_type": "individual",
            "to": "+549111",
            "type": "text",
            "text": {"body": "Hola mundo"},
        }


class TestWhatsAppClientRetry429:
    """Requirement 5.4 — retry exactly once on HTTP 429."""

    def test_retry_on_429_then_success(self):
        """A 429 followed by a 200 should succeed after one retry."""
        client = WhatsAppClient("pid", "tok")
        success_resp = _make_response(200, {"messages": [{"id": "wamid.ok"}]})

        with patch.object(
            client._http, "request",
            side_effect=[_make_response(429, {"error": "rate limited"}), success_resp],
        ) as mock_req, patch("time.sleep") as mock_sleep:
            result = client.send_text_message("+1111", "retry me")

        assert result == {"messages": [{"id": "wamid.ok"}]}
        assert mock_req.call_count == 2
        mock_sleep.assert_called_once_with(1)

    def test_retry_on_429_then_429_raises(self):
        """Two consecutive 429s should raise WhatsAppSendError."""
        client = WhatsAppClient("pid", "tok")

        with patch.object(
            client._http, "request",
            side_effect=[_make_response(429, {"error": "rate limited"}),
                         _make_response(429, {"error": "still limited"})],
        ), patch("time.sleep"):
            with pytest.raises(WhatsAppSendError, match="429"):
                client.send_text_message("+1111", "fail twice")


class TestWhatsAppClientNoRetry400:
    """Requirement 5.4 — no retry on HTTP 400 (client error, not transient)."""

    def test_400_raises_immediately_without_retry(self):
        """A 400 error should raise immediately with no retry attempt."""
        client = WhatsAppClient("pid", "tok")

        with patch.object(
            client._http, "request",
            return_value=_make_response(400, {"error": "bad request"}),
        ) as mock_req, patch("time.sleep") as mock_sleep:
            with pytest.raises(WhatsAppSendError, match="400"):
                client.send_text_message("+2222", "bad")

        # Only one request — no retry
        assert mock_req.call_count == 1
        mock_sleep.assert_not_called()

    def test_403_raises_immediately_without_retry(self):
        """A 403 error should also not trigger a retry."""
        client = WhatsAppClient("pid", "tok")

        with patch.object(
            client._http, "request",
            return_value=_make_response(403, {"error": "forbidden"}),
        ) as mock_req, patch("time.sleep") as mock_sleep:
            with pytest.raises(WhatsAppSendError, match="403"):
                client.send_text_message("+3333", "nope")

        assert mock_req.call_count == 1
        mock_sleep.assert_not_called()


class TestWhatsAppClientRetry500:
    """Requirement 5.4 — retry exactly once on HTTP 500 (server error)."""

    def test_retry_on_500_then_success(self):
        """A 500 followed by a 200 should succeed after one retry."""
        client = WhatsAppClient("pid", "tok")
        success_resp = _make_response(200, {"messages": [{"id": "wamid.ok2"}]})

        with patch.object(
            client._http, "request",
            side_effect=[_make_response(500, {"error": "internal"}), success_resp],
        ) as mock_req, patch("time.sleep") as mock_sleep:
            result = client.send_text_message("+4444", "server err")

        assert result == {"messages": [{"id": "wamid.ok2"}]}
        assert mock_req.call_count == 2
        mock_sleep.assert_called_once_with(1)

    def test_retry_on_502_then_success(self):
        """A 502 (bad gateway) should also trigger a retry."""
        client = WhatsAppClient("pid", "tok")
        success_resp = _make_response(200, {"messages": [{"id": "wamid.gw"}]})

        with patch.object(
            client._http, "request",
            side_effect=[_make_response(502, {"error": "bad gateway"}), success_resp],
        ) as mock_req, patch("time.sleep") as mock_sleep:
            result = client.send_text_message("+5555", "gw err")

        assert result == {"messages": [{"id": "wamid.gw"}]}
        assert mock_req.call_count == 2
        mock_sleep.assert_called_once_with(1)

    def test_retry_on_500_then_500_raises(self):
        """Two consecutive 500s should raise WhatsAppSendError."""
        client = WhatsAppClient("pid", "tok")

        with patch.object(
            client._http, "request",
            side_effect=[_make_response(500, {"error": "internal"}),
                         _make_response(500, {"error": "still broken"})],
        ), patch("time.sleep"):
            with pytest.raises(WhatsAppSendError, match="500"):
                client.send_text_message("+6666", "double fail")
