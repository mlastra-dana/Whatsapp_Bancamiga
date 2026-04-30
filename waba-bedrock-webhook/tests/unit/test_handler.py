"""
Unit tests for the Lambda handler module.

Tests webhook verification (GET), message processing (POST),
error handling, and the full lambda_handler entry point.
"""

from unittest.mock import MagicMock, patch

import pytest

from handler import (
    DEFAULT_ERROR_MESSAGE,
    extract_text_messages,
    handle_message,
    handle_verification,
)
from bedrock_agent import BedrockAgentError


# ---------------------------------------------------------------------------
# extract_text_messages
# ---------------------------------------------------------------------------


class TestExtractTextMessages:
    """Tests for extract_text_messages."""

    def test_single_text_message(self):
        """A payload with one text message returns a single-element list."""
        body = {
            "entry": [
                {
                    "changes": [
                        {
                            "value": {
                                "messages": [
                                    {
                                        "from": "5491100000000",
                                        "id": "wamid.abc",
                                        "type": "text",
                                        "text": {"body": "Hola"},
                                    }
                                ]
                            }
                        }
                    ]
                }
            ]
        }
        result = extract_text_messages(body)
        assert result == [
            {"from": "5491100000000", "text": "Hola", "id": "wamid.abc"}
        ]

    def test_multiple_text_messages(self):
        """Multiple text messages across entries are all extracted."""
        body = {
            "entry": [
                {
                    "changes": [
                        {
                            "value": {
                                "messages": [
                                    {
                                        "from": "111",
                                        "id": "m1",
                                        "type": "text",
                                        "text": {"body": "A"},
                                    },
                                    {
                                        "from": "222",
                                        "id": "m2",
                                        "type": "text",
                                        "text": {"body": "B"},
                                    },
                                ]
                            }
                        }
                    ]
                }
            ]
        }
        result = extract_text_messages(body)
        assert len(result) == 2
        assert result[0]["from"] == "111"
        assert result[1]["from"] == "222"

    def test_non_text_messages_filtered(self):
        """Non-text messages (image, audio, etc.) are excluded."""
        body = {
            "entry": [
                {
                    "changes": [
                        {
                            "value": {
                                "messages": [
                                    {
                                        "from": "111",
                                        "id": "m1",
                                        "type": "image",
                                        "image": {"id": "img1"},
                                    },
                                    {
                                        "from": "222",
                                        "id": "m2",
                                        "type": "text",
                                        "text": {"body": "Hello"},
                                    },
                                ]
                            }
                        }
                    ]
                }
            ]
        }
        result = extract_text_messages(body)
        assert len(result) == 1
        assert result[0]["from"] == "222"

    def test_empty_entry(self):
        """A payload with no entries returns an empty list."""
        assert extract_text_messages({"entry": []}) == []

    def test_no_messages_key(self):
        """A change value without a messages key returns an empty list."""
        body = {"entry": [{"changes": [{"value": {}}]}]}
        assert extract_text_messages(body) == []

    def test_empty_body(self):
        """An empty dict returns an empty list."""
        assert extract_text_messages({}) == []


# ---------------------------------------------------------------------------
# handle_message
# ---------------------------------------------------------------------------


class TestHandleMessage:
    """Tests for handle_message."""

    @pytest.fixture(autouse=True)
    def _setup_mocks(self, monkeypatch):
        """Inject mock service instances into the handler module."""
        import handler

        self.mock_session_mgr = MagicMock()
        self.mock_session_mgr.get_or_create_session.return_value = "sess-123"

        self.mock_prompt = MagicMock()
        self.mock_prompt.get_prompt.return_value = "System prompt"

        self.mock_bedrock = MagicMock()
        self.mock_bedrock.invoke.return_value = "Agent response"

        self.mock_whatsapp = MagicMock()

        monkeypatch.setattr(handler, "session_manager", self.mock_session_mgr)
        monkeypatch.setattr(handler, "prompt_reader", self.mock_prompt)
        monkeypatch.setattr(handler, "bedrock_client", self.mock_bedrock)
        monkeypatch.setattr(handler, "whatsapp_client", self.mock_whatsapp)

    def _make_text_payload(self, from_number="5491100000000", text="Hola"):
        return {
            "entry": [
                {
                    "changes": [
                        {
                            "value": {
                                "messages": [
                                    {
                                        "from": from_number,
                                        "id": "wamid.test",
                                        "type": "text",
                                        "text": {"body": text},
                                    }
                                ]
                            }
                        }
                    ]
                }
            ]
        }

    def test_returns_200(self):
        """handle_message always returns HTTP 200."""
        result = handle_message(self._make_text_payload())
        assert result["statusCode"] == 200

    def test_processes_text_message(self):
        """A text message triggers session, prompt, bedrock, and whatsapp calls."""
        handle_message(self._make_text_payload(text="¿Qué es CDK?"))

        self.mock_session_mgr.get_or_create_session.assert_called_once_with(
            "5491100000000"
        )
        self.mock_prompt.get_prompt.assert_called_once()
        self.mock_bedrock.invoke.assert_called_once_with(
            "¿Qué es CDK?", "sess-123"
        )
        self.mock_whatsapp.send_text_message.assert_called_once_with(
            "5491100000000", "Agent response"
        )

    def test_bedrock_error_sends_default_message(self):
        """When Bedrock Agent fails, the default error message is sent."""
        self.mock_bedrock.invoke.side_effect = BedrockAgentError("timeout")

        result = handle_message(self._make_text_payload())

        assert result["statusCode"] == 200
        self.mock_whatsapp.send_text_message.assert_called_once_with(
            "5491100000000", DEFAULT_ERROR_MESSAGE
        )

    def test_malformed_payload_returns_200(self):
        """A completely malformed payload still returns HTTP 200."""
        result = handle_message({})
        assert result["statusCode"] == 200

    def test_non_text_only_returns_200(self):
        """A payload with only non-text messages returns HTTP 200."""
        body = {
            "entry": [
                {
                    "changes": [
                        {
                            "value": {
                                "messages": [
                                    {
                                        "from": "111",
                                        "id": "m1",
                                        "type": "image",
                                        "image": {"id": "img1"},
                                    }
                                ]
                            }
                        }
                    ]
                }
            ]
        }
        result = handle_message(body)
        assert result["statusCode"] == 200
        self.mock_bedrock.invoke.assert_not_called()
        self.mock_whatsapp.send_text_message.assert_not_called()

    def test_multiple_messages_processed_individually(self):
        """Each text message in the payload is processed separately."""
        body = {
            "entry": [
                {
                    "changes": [
                        {
                            "value": {
                                "messages": [
                                    {
                                        "from": "111",
                                        "id": "m1",
                                        "type": "text",
                                        "text": {"body": "A"},
                                    },
                                    {
                                        "from": "222",
                                        "id": "m2",
                                        "type": "text",
                                        "text": {"body": "B"},
                                    },
                                ]
                            }
                        }
                    ]
                }
            ]
        }
        handle_message(body)

        assert self.mock_session_mgr.get_or_create_session.call_count == 2
        assert self.mock_bedrock.invoke.call_count == 2
        assert self.mock_whatsapp.send_text_message.call_count == 2

    def test_whatsapp_send_error_does_not_crash(self):
        """If WhatsApp send fails, the handler still returns 200."""
        self.mock_whatsapp.send_text_message.side_effect = Exception("send failed")

        result = handle_message(self._make_text_payload())
        assert result["statusCode"] == 200


# ---------------------------------------------------------------------------
# lambda_handler
# ---------------------------------------------------------------------------


class TestLambdaHandler:
    """Tests for the lambda_handler entry point."""

    @pytest.fixture(autouse=True)
    def _reset_handler_services(self, monkeypatch):
        """Reset module-level service instances before each test."""
        import handler

        monkeypatch.setattr(handler, "session_manager", None)
        monkeypatch.setattr(handler, "prompt_reader", None)
        monkeypatch.setattr(handler, "bedrock_client", None)
        monkeypatch.setattr(handler, "whatsapp_client", None)

    # -- GET verification flow -----------------------------------------------

    def test_get_verification_success(self):
        """GET with matching token returns 200 and the challenge string."""
        from handler import lambda_handler
        from conftest import make_verification_event

        event = make_verification_event(
            token="test-verify-token", challenge="my-challenge-42"
        )

        with patch("handler._init_services"):
            result = lambda_handler(event, None)

        assert result["statusCode"] == 200
        assert result["body"] == "my-challenge-42"

    def test_get_verification_failure(self):
        """GET with mismatched token returns 403."""
        from handler import lambda_handler
        from conftest import make_verification_event

        event = make_verification_event(token="wrong-token")

        with patch("handler._init_services"):
            result = lambda_handler(event, None)

        assert result["statusCode"] == 403

    # -- POST flow -----------------------------------------------------------

    def test_post_text_message_full_flow(self):
        """POST with a valid text message invokes all dependencies and returns 200."""
        import handler
        from handler import lambda_handler
        from conftest import make_post_event, make_whatsapp_text_payload

        mock_session_mgr = MagicMock()
        mock_session_mgr.get_or_create_session.return_value = "sess-abc"

        mock_prompt = MagicMock()
        mock_prompt.get_prompt.return_value = "System prompt"

        mock_bedrock = MagicMock()
        mock_bedrock.invoke.return_value = "Agent reply"

        mock_whatsapp = MagicMock()

        payload = make_whatsapp_text_payload(
            from_number="5491155550000", message_text="Hola mundo"
        )
        event = make_post_event(payload)

        with patch("handler._init_services") as mock_init:
            # Simulate _init_services setting the module-level variables
            def _fake_init():
                handler.session_manager = mock_session_mgr
                handler.prompt_reader = mock_prompt
                handler.bedrock_client = mock_bedrock
                handler.whatsapp_client = mock_whatsapp

            mock_init.side_effect = _fake_init

            result = lambda_handler(event, None)

        assert result["statusCode"] == 200
        mock_session_mgr.get_or_create_session.assert_called_once_with(
            "5491155550000"
        )
        mock_prompt.get_prompt.assert_called_once()
        mock_bedrock.invoke.assert_called_once_with("Hola mundo", "sess-abc")
        mock_whatsapp.send_text_message.assert_called_once_with(
            "5491155550000", "Agent reply"
        )

    def test_post_empty_payload_returns_200(self):
        """POST with an empty body returns HTTP 200."""
        from handler import lambda_handler

        event = {"httpMethod": "POST", "body": ""}

        with patch("handler._init_services"):
            result = lambda_handler(event, None)

        assert result["statusCode"] == 200

    def test_post_invalid_payload_returns_200(self):
        """POST with an invalid (non-WhatsApp) JSON payload returns HTTP 200."""
        import json
        from handler import lambda_handler

        event = {"httpMethod": "POST", "body": json.dumps({"random": "data"})}

        with patch("handler._init_services"):
            result = lambda_handler(event, None)

        assert result["statusCode"] == 200

    # -- Exception safety ----------------------------------------------------

    def test_unhandled_exception_returns_200(self):
        """Any unhandled exception in _init_services still returns HTTP 200."""
        from handler import lambda_handler
        from conftest import make_post_event, make_whatsapp_text_payload

        payload = make_whatsapp_text_payload()
        event = make_post_event(payload)

        with patch(
            "handler._init_services",
            side_effect=RuntimeError("boom"),
        ):
            result = lambda_handler(event, None)

        assert result["statusCode"] == 200
