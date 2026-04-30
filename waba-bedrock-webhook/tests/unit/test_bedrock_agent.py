"""
Unit tests for the Bedrock Agent client module.

Tests agent invocation, EventStream response parsing,
timeout handling, and service error handling.
"""

from unittest.mock import MagicMock, patch

import pytest
from botocore.exceptions import ClientError, ReadTimeoutError

from bedrock_agent import BedrockAgentClient, BedrockAgentError


@pytest.fixture
def mock_boto3_client():
    """Patch boto3.client so BedrockAgentClient uses a MagicMock."""
    with patch("bedrock_agent.boto3.client") as mock_client_ctor:
        mock_runtime = MagicMock()
        mock_client_ctor.return_value = mock_runtime
        yield mock_runtime


# ---- Successful invocation ------------------------------------------------


class TestInvokeSuccess:
    """Requirement 4.1, 4.3 – successful agent invocation and response parsing."""

    def test_returns_concatenated_chunks(self, mock_boto3_client):
        """invoke() concatenates all EventStream completion chunks into one string."""
        mock_boto3_client.invoke_agent.return_value = {
            "completion": [
                {"chunk": {"bytes": "Hola ".encode("utf-8")}},
                {"chunk": {"bytes": "mundo".encode("utf-8")}},
            ],
        }

        client = BedrockAgentClient(
            agent_id="agent-123", agent_alias_id="alias-456"
        )
        result = client.invoke(input_text="Hola", session_id="sess-001")

        assert result == "Hola mundo"
        mock_boto3_client.invoke_agent.assert_called_once_with(
            agentId="agent-123",
            agentAliasId="alias-456",
            sessionId="sess-001",
            inputText="Hola",
        )

    def test_single_chunk_response(self, mock_boto3_client):
        """invoke() works correctly with a single completion chunk."""
        mock_boto3_client.invoke_agent.return_value = {
            "completion": [
                {"chunk": {"bytes": "Respuesta completa".encode("utf-8")}},
            ],
        }

        client = BedrockAgentClient(
            agent_id="agent-123", agent_alias_id="alias-456"
        )
        result = client.invoke(input_text="Pregunta", session_id="sess-002")

        assert result == "Respuesta completa"

    def test_empty_completion_returns_empty_string(self, mock_boto3_client):
        """invoke() returns an empty string when there are no completion chunks."""
        mock_boto3_client.invoke_agent.return_value = {
            "completion": [],
        }

        client = BedrockAgentClient(
            agent_id="agent-123", agent_alias_id="alias-456"
        )
        result = client.invoke(input_text="Hola", session_id="sess-003")

        assert result == ""


# ---- Timeout handling ------------------------------------------------------


class TestInvokeTimeout:
    """Requirement 4.4 – timeout handling (25-second read timeout)."""

    def test_read_timeout_raises_bedrock_agent_error(self, mock_boto3_client):
        """invoke() wraps ReadTimeoutError in BedrockAgentError."""
        mock_boto3_client.invoke_agent.side_effect = ReadTimeoutError(
            endpoint_url="https://bedrock-agent-runtime.us-east-1.amazonaws.com"
        )

        client = BedrockAgentClient(
            agent_id="agent-123", agent_alias_id="alias-456"
        )

        with pytest.raises(BedrockAgentError, match="timed out"):
            client.invoke(input_text="Hola", session_id="sess-timeout")

    def test_timeout_error_includes_session_id(self, mock_boto3_client):
        """The BedrockAgentError message includes the session ID for debugging."""
        mock_boto3_client.invoke_agent.side_effect = ReadTimeoutError(
            endpoint_url="https://bedrock-agent-runtime.us-east-1.amazonaws.com"
        )

        client = BedrockAgentClient(
            agent_id="agent-123", agent_alias_id="alias-456"
        )

        with pytest.raises(BedrockAgentError, match="sess-debug"):
            client.invoke(input_text="Hola", session_id="sess-debug")


# ---- Service error handling ------------------------------------------------


class TestInvokeServiceError:
    """Requirement 4.4 – Bedrock service error handling."""

    def test_client_error_raises_bedrock_agent_error(self, mock_boto3_client):
        """invoke() wraps botocore ClientError in BedrockAgentError."""
        mock_boto3_client.invoke_agent.side_effect = ClientError(
            error_response={
                "Error": {
                    "Code": "ThrottlingException",
                    "Message": "Rate exceeded",
                }
            },
            operation_name="InvokeAgent",
        )

        client = BedrockAgentClient(
            agent_id="agent-123", agent_alias_id="alias-456"
        )

        with pytest.raises(BedrockAgentError, match="Bedrock Agent error"):
            client.invoke(input_text="Hola", session_id="sess-err")

    def test_unexpected_exception_raises_bedrock_agent_error(
        self, mock_boto3_client
    ):
        """invoke() wraps any unexpected exception in BedrockAgentError."""
        mock_boto3_client.invoke_agent.side_effect = RuntimeError(
            "Something unexpected"
        )

        client = BedrockAgentClient(
            agent_id="agent-123", agent_alias_id="alias-456"
        )

        with pytest.raises(BedrockAgentError, match="Unexpected"):
            client.invoke(input_text="Hola", session_id="sess-unexpected")

    def test_service_error_preserves_original_cause(self, mock_boto3_client):
        """The raised BedrockAgentError chains the original exception via __cause__."""
        original = ClientError(
            error_response={
                "Error": {
                    "Code": "InternalServerError",
                    "Message": "Internal failure",
                }
            },
            operation_name="InvokeAgent",
        )
        mock_boto3_client.invoke_agent.side_effect = original

        client = BedrockAgentClient(
            agent_id="agent-123", agent_alias_id="alias-456"
        )

        with pytest.raises(BedrockAgentError) as exc_info:
            client.invoke(input_text="Hola", session_id="sess-chain")

        assert exc_info.value.__cause__ is original
