"""
Unit tests for the prompt reader module.

Tests S3 prompt reading, fallback to default prompt,
and in-memory caching for warm starts.
"""

import boto3
import pytest
from moto import mock_aws

from prompt_reader import DEFAULT_SYSTEM_PROMPT, PromptReader


@pytest.fixture
def s3_bucket():
    """Create a mocked S3 bucket with a system prompt file."""
    with mock_aws():
        client = boto3.client("s3", region_name="us-east-1")
        client.create_bucket(Bucket="test-prompt-bucket")
        client.put_object(
            Bucket="test-prompt-bucket",
            Key="system_prompt.txt",
            Body="Custom system prompt from S3.",
        )
        yield


@pytest.fixture
def s3_empty_bucket():
    """Create a mocked S3 bucket without a prompt file."""
    with mock_aws():
        client = boto3.client("s3", region_name="us-east-1")
        client.create_bucket(Bucket="test-prompt-bucket")
        yield


class TestPromptReaderSuccessfulRead:
    """Test successful S3 read — Validates: Requirements 4.2"""

    def test_returns_prompt_from_s3(self, s3_bucket):
        reader = PromptReader(bucket="test-prompt-bucket", key="system_prompt.txt")
        prompt = reader.get_prompt()

        assert prompt == "Custom system prompt from S3."

    def test_returns_string_type(self, s3_bucket):
        reader = PromptReader(bucket="test-prompt-bucket", key="system_prompt.txt")
        prompt = reader.get_prompt()

        assert isinstance(prompt, str)


class TestPromptReaderFallback:
    """Test fallback to default prompt when S3 fails — Validates: Requirements 4.5"""

    def test_returns_default_prompt_when_key_missing(self, s3_empty_bucket):
        reader = PromptReader(bucket="test-prompt-bucket", key="missing_key.txt")
        prompt = reader.get_prompt()

        assert prompt == DEFAULT_SYSTEM_PROMPT

    def test_returns_default_prompt_when_bucket_missing(self):
        with mock_aws():
            reader = PromptReader(bucket="nonexistent-bucket", key="system_prompt.txt")
            prompt = reader.get_prompt()

            assert prompt == DEFAULT_SYSTEM_PROMPT

    def test_logs_error_on_s3_failure(self, s3_empty_bucket, caplog):
        import logging

        with caplog.at_level(logging.ERROR):
            reader = PromptReader(bucket="test-prompt-bucket", key="missing_key.txt")
            reader.get_prompt()

        assert "Failed to read system prompt" in caplog.text


class TestPromptReaderCaching:
    """Test caching behavior on warm start — Validates: Requirements 4.2, 4.5"""

    def test_second_call_returns_cached_value(self, s3_bucket):
        reader = PromptReader(bucket="test-prompt-bucket", key="system_prompt.txt")

        first_call = reader.get_prompt()
        second_call = reader.get_prompt()

        assert first_call == second_call
        assert first_call == "Custom system prompt from S3."

    def test_cached_value_survives_s3_becoming_unavailable(self, s3_bucket):
        reader = PromptReader(bucket="test-prompt-bucket", key="system_prompt.txt")

        # First call reads from S3
        first_call = reader.get_prompt()
        assert first_call == "Custom system prompt from S3."

        # Delete the object from S3 to simulate S3 becoming unavailable
        client = boto3.client("s3", region_name="us-east-1")
        client.delete_object(Bucket="test-prompt-bucket", Key="system_prompt.txt")

        # Second call should still return the cached value
        second_call = reader.get_prompt()
        assert second_call == "Custom system prompt from S3."

    def test_fallback_prompt_is_also_cached(self, s3_empty_bucket):
        reader = PromptReader(bucket="test-prompt-bucket", key="missing_key.txt")

        first_call = reader.get_prompt()
        second_call = reader.get_prompt()

        assert first_call == DEFAULT_SYSTEM_PROMPT
        assert second_call == DEFAULT_SYSTEM_PROMPT
