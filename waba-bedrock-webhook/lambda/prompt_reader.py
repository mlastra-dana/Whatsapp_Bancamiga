"""
System prompt reader module.

Reads the system prompt from an S3 bucket with in-memory caching for
Lambda warm starts and fallback to a default prompt on failure.
"""

import logging

import boto3
from botocore.exceptions import ClientError

logger = logging.getLogger(__name__)

DEFAULT_SYSTEM_PROMPT = (
    "Eres un asistente virtual. Responde las preguntas del usuario "
    "basándote en la información disponible en la base de conocimiento."
)


class PromptReader:
    """Reads and caches the system prompt from S3."""

    def __init__(self, bucket: str, key: str):
        """
        Initialise the prompt reader.

        Args:
            bucket: Name of the S3 bucket containing the prompt file.
            key: Object key of the prompt file inside the bucket.
        """
        self._bucket = bucket
        self._key = key
        self._client = boto3.client("s3")
        self._cached_prompt: str | None = None

    def get_prompt(self) -> str:
        """
        Return the system prompt, reading from S3 on first call.

        The prompt is cached in memory so subsequent calls during the
        same Lambda execution (warm start) skip the S3 request.

        If the S3 read fails for any reason the default prompt is
        returned and the error is logged.

        Returns:
            The system prompt text.
        """
        if self._cached_prompt is not None:
            return self._cached_prompt

        try:
            response = self._client.get_object(
                Bucket=self._bucket, Key=self._key
            )
            self._cached_prompt = response["Body"].read().decode("utf-8")
        except (ClientError, Exception):
            logger.exception(
                "Failed to read system prompt from s3://%s/%s — using default",
                self._bucket,
                self._key,
            )
            self._cached_prompt = DEFAULT_SYSTEM_PROMPT

        return self._cached_prompt
