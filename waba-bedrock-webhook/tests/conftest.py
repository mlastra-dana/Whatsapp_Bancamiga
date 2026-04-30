"""
Shared test fixtures and mocks for the WABA Bedrock Webhook test suite.

Provides common fixtures for environment variables, mock AWS clients
(DynamoDB, S3, Bedrock Agent Runtime), and WhatsApp API test helpers.
"""

import os
import sys
import pytest
import boto3
from unittest.mock import MagicMock, patch

# Add the lambda/ directory to sys.path so modules can be imported directly
# (the directory name "lambda" is a Python keyword, so normal imports won't work).
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "lambda"))


# ---------------------------------------------------------------------------
# Environment variable fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def env_vars(monkeypatch):
    """Set up all required Lambda environment variables for every test."""
    env = {
        "WHATSAPP_VERIFY_TOKEN": "test-verify-token",
        "WHATSAPP_ACCESS_TOKEN": "test-access-token",
        "WHATSAPP_PHONE_NUMBER_ID": "123456789",
        "BEDROCK_AGENT_ID": "test-agent-id",
        "BEDROCK_AGENT_ALIAS_ID": "test-alias-id",
        "BEDROCK_MODEL_ARN": "arn:aws:bedrock:us-east-1::foundation-model/anthropic.claude-sonnet-4-6",
        "SYSTEM_PROMPT_BUCKET": "test-prompt-bucket",
        "SYSTEM_PROMPT_KEY": "system_prompt.txt",
        "SESSION_TABLE_NAME": "test-session-table",
    }
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    return env


# ---------------------------------------------------------------------------
# AWS mock fixtures (using moto)
# ---------------------------------------------------------------------------

@pytest.fixture
def dynamodb_table():
    """Create a mocked DynamoDB session table using moto."""
    from moto import mock_aws

    with mock_aws():
        client = boto3.client("dynamodb", region_name="us-east-1")
        client.create_table(
            TableName="test-session-table",
            KeySchema=[
                {"AttributeName": "phone_number", "KeyType": "HASH"},
            ],
            AttributeDefinitions=[
                {"AttributeName": "phone_number", "AttributeType": "S"},
            ],
            BillingMode="PAY_PER_REQUEST",
        )
        # Enable TTL
        client.update_time_to_live(
            TableName="test-session-table",
            TimeToLiveSpecification={
                "Enabled": True,
                "AttributeName": "ttl",
            },
        )
        table = boto3.resource("dynamodb", region_name="us-east-1").Table(
            "test-session-table"
        )
        yield table


@pytest.fixture
def s3_prompt_bucket():
    """Create a mocked S3 bucket with a system prompt file using moto."""
    from moto import mock_aws

    with mock_aws():
        client = boto3.client("s3", region_name="us-east-1")
        client.create_bucket(Bucket="test-prompt-bucket")
        client.put_object(
            Bucket="test-prompt-bucket",
            Key="system_prompt.txt",
            Body="Test system prompt content.",
        )
        yield client


# ---------------------------------------------------------------------------
# Mock AWS service clients
# ---------------------------------------------------------------------------

@pytest.fixture
def mock_bedrock_agent_runtime():
    """Return a MagicMock for the bedrock-agent-runtime boto3 client."""
    return MagicMock()


# ---------------------------------------------------------------------------
# WhatsApp test helpers
# ---------------------------------------------------------------------------

def make_whatsapp_text_payload(
    from_number: str = "5491100000000",
    message_text: str = "Hola",
    message_id: str = "wamid.test123",
) -> dict:
    """Build a minimal valid WhatsApp Cloud API webhook payload with a text message."""
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
                                    "id": message_id,
                                    "timestamp": "1700000000",
                                    "type": "text",
                                    "text": {"body": message_text},
                                }
                            ],
                        },
                        "field": "messages",
                    }
                ],
            }
        ],
    }


def make_verification_event(
    mode: str = "subscribe",
    token: str = "test-verify-token",
    challenge: str = "challenge-string-123",
) -> dict:
    """Build an API Gateway GET event for webhook verification."""
    return {
        "httpMethod": "GET",
        "queryStringParameters": {
            "hub.mode": mode,
            "hub.verify_token": token,
            "hub.challenge": challenge,
        },
    }


def make_post_event(body: dict) -> dict:
    """Build an API Gateway POST event wrapping a JSON body."""
    import json

    return {
        "httpMethod": "POST",
        "body": json.dumps(body),
    }
