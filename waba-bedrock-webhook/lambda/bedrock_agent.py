"""
Amazon Bedrock Agent client module.

Encapsulates invocation of the Bedrock Agent via the bedrock-agent-runtime
API, including EventStream response parsing and chunk concatenation.
"""

import logging

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError, ReadTimeoutError

logger = logging.getLogger(__name__)


class BedrockAgentError(Exception):
    """Raised when a Bedrock Agent invocation fails or times out."""


def extract_response_text(completion_events) -> str:
    """
    Concatenate completion event chunks into a single UTF-8 string.

    This is a standalone helper so it can be tested independently
    (e.g. via property-based tests) without needing a live client.

    Args:
        completion_events: Iterable of EventStream completion events.
            Each event is expected to have the shape
            ``{"chunk": {"bytes": b"..."}}``.

    Returns:
        The full response text decoded from the concatenated bytes.
    """
    parts: list[str] = []
    for event in completion_events:
        chunk = event.get("chunk")
        if chunk and "bytes" in chunk:
            parts.append(chunk["bytes"].decode("utf-8"))
    return "".join(parts)


class BedrockAgentClient:
    """Client for invoking an Amazon Bedrock Agent."""

    def __init__(self, agent_id: str, agent_alias_id: str):
        """
        Initialise the Bedrock Agent client.

        Args:
            agent_id: The Bedrock Agent ID.
            agent_alias_id: The Bedrock Agent Alias ID.
        """
        self._agent_id = agent_id
        self._agent_alias_id = agent_alias_id
        self._client = boto3.client(
            "bedrock-agent-runtime",
            config=Config(
                read_timeout=25,
                connect_timeout=5,
            ),
        )

    def invoke(self, input_text: str, session_id: str) -> str:
        """
        Invoke the Bedrock Agent and return the full response text.

        Args:
            input_text: The user's message text.
            session_id: Session identifier for multi-turn context.

        Returns:
            The agent's complete response as a string.

        Raises:
            BedrockAgentError: If the invocation fails or times out.
        """
        try:
            response = self._client.invoke_agent(
                agentId=self._agent_id,
                agentAliasId=self._agent_alias_id,
                sessionId=session_id,
                inputText=input_text,
            )
            return extract_response_text(response["completion"])
        except ReadTimeoutError as exc:
            logger.exception(
                "Bedrock Agent invocation timed out for session=%s",
                session_id,
            )
            raise BedrockAgentError(
                f"Bedrock Agent timed out for session {session_id}"
            ) from exc
        except ClientError as exc:
            logger.exception(
                "Bedrock Agent invocation failed for session=%s", session_id
            )
            raise BedrockAgentError(
                f"Bedrock Agent error for session {session_id}: {exc}"
            ) from exc
        except Exception as exc:
            logger.exception(
                "Unexpected error invoking Bedrock Agent for session=%s",
                session_id,
            )
            raise BedrockAgentError(
                f"Unexpected Bedrock Agent error for session {session_id}: {exc}"
            ) from exc
