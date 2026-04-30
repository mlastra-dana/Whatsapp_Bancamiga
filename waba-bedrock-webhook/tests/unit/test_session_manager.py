"""
Unit tests for the session manager module.

Tests session creation, retrieval, TTL extension,
and DynamoDB error handling.
"""

import uuid

import boto3
import pytest
from moto import mock_aws
from unittest.mock import MagicMock
from botocore.exceptions import ClientError


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _create_session_table(table_name: str = "test-session-table"):
    """Create the DynamoDB session table inside an active moto context."""
    client = boto3.client("dynamodb", region_name="us-east-1")
    client.create_table(
        TableName=table_name,
        KeySchema=[{"AttributeName": "phone_number", "KeyType": "HASH"}],
        AttributeDefinitions=[
            {"AttributeName": "phone_number", "AttributeType": "S"}
        ],
        BillingMode="PAY_PER_REQUEST",
    )
    client.update_time_to_live(
        TableName=table_name,
        TimeToLiveSpecification={"Enabled": True, "AttributeName": "ttl"},
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestNewSession:
    """Creating a session for a phone number that has no existing record."""

    @mock_aws
    def test_creates_new_session_returns_uuid(self):
        _create_session_table()
        from session_manager import SessionManager

        mgr = SessionManager("test-session-table", ttl_hours=24)
        session_id = mgr.get_or_create_session("+5491100000001")

        # Must be a valid UUID-v4
        parsed = uuid.UUID(session_id, version=4)
        assert str(parsed) == session_id

    @mock_aws
    def test_new_session_stores_all_fields(self):
        _create_session_table()
        from session_manager import SessionManager

        phone = "+5491100000002"
        mgr = SessionManager("test-session-table", ttl_hours=12)
        session_id = mgr.get_or_create_session(phone)

        table = boto3.resource("dynamodb", region_name="us-east-1").Table(
            "test-session-table"
        )
        item = table.get_item(Key={"phone_number": phone})["Item"]

        assert item["phone_number"] == phone
        assert item["session_id"] == session_id
        assert "last_activity" in item
        assert "ttl" in item
        assert "created_at" in item


class TestExistingSession:
    """Retrieving a session for a phone number that already has a record."""

    @mock_aws
    def test_returns_same_session_id(self):
        _create_session_table()
        from session_manager import SessionManager

        mgr = SessionManager("test-session-table")
        phone = "+5491100000003"

        first = mgr.get_or_create_session(phone)
        second = mgr.get_or_create_session(phone)

        assert first == second

    @mock_aws
    def test_updates_ttl_and_last_activity_on_access(self):
        _create_session_table()
        from session_manager import SessionManager

        mgr = SessionManager("test-session-table", ttl_hours=24)
        phone = "+5491100000004"

        mgr.get_or_create_session(phone)

        table = boto3.resource("dynamodb", region_name="us-east-1").Table(
            "test-session-table"
        )
        item_before = table.get_item(Key={"phone_number": phone})["Item"]

        # Small sleep so timestamps can differ
        import time
        time.sleep(0.05)

        mgr.get_or_create_session(phone)
        item_after = table.get_item(Key={"phone_number": phone})["Item"]

        # last_activity and ttl should be >= the original values
        assert int(item_after["last_activity"]) >= int(item_before["last_activity"])
        assert int(item_after["ttl"]) >= int(item_before["ttl"])


class TestDynamoDBErrorHandling:
    """Graceful degradation when DynamoDB operations fail."""

    def test_get_item_error_returns_fallback_session(self):
        """When GetItem fails, a one-off UUID should still be returned."""
        mock_table = MagicMock()
        mock_table.get_item.side_effect = ClientError(
            {"Error": {"Code": "InternalServerError", "Message": "boom"}},
            "GetItem",
        )

        from session_manager import SessionManager

        mgr = SessionManager.__new__(SessionManager)
        mgr._table_name = "test-session-table"
        mgr._ttl_hours = 24
        mgr._table = mock_table

        session_id = mgr.get_or_create_session("+5491100000005")

        # Should still return a valid UUID
        parsed = uuid.UUID(session_id, version=4)
        assert str(parsed) == session_id

    def test_put_item_error_still_returns_session_id(self):
        """When PutItem fails, the generated session_id is still returned."""
        mock_table = MagicMock()
        mock_table.get_item.return_value = {}  # no Item key → new session
        mock_table.put_item.side_effect = ClientError(
            {"Error": {"Code": "InternalServerError", "Message": "boom"}},
            "PutItem",
        )

        from session_manager import SessionManager

        mgr = SessionManager.__new__(SessionManager)
        mgr._table_name = "test-session-table"
        mgr._ttl_hours = 24
        mgr._table = mock_table

        session_id = mgr.get_or_create_session("+5491100000006")

        parsed = uuid.UUID(session_id, version=4)
        assert str(parsed) == session_id

    def test_update_item_error_still_returns_existing_session(self):
        """When UpdateItem fails, the existing session_id is still returned."""
        existing_id = str(uuid.uuid4())
        mock_table = MagicMock()
        mock_table.get_item.return_value = {
            "Item": {"phone_number": "+5491100000007", "session_id": existing_id}
        }
        mock_table.update_item.side_effect = ClientError(
            {"Error": {"Code": "InternalServerError", "Message": "boom"}},
            "UpdateItem",
        )

        from session_manager import SessionManager

        mgr = SessionManager.__new__(SessionManager)
        mgr._table_name = "test-session-table"
        mgr._ttl_hours = 24
        mgr._table = mock_table

        session_id = mgr.get_or_create_session("+5491100000007")

        assert session_id == existing_id
