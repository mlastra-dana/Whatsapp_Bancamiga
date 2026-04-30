"""
Property-based tests for session management.

Property 6: Idempotencia y unicidad de sesiones

For any phone number, calling get_or_create_session twice consecutively
must return the same session_id (idempotency). For any pair of distinct
phone numbers, the generated session_id values must be different (uniqueness).

Validates: Requirements 3.2, 3.3
"""

import boto3
from hypothesis import given, settings, assume
from hypothesis import strategies as st
from moto import mock_aws

from session_manager import SessionManager


# ---------------------------------------------------------------------------
# Strategies
# ---------------------------------------------------------------------------

# Generate phone numbers as strings of 8-15 digits with a leading "+"
phone_number_strategy = st.from_regex(r"\+[1-9][0-9]{7,14}", fullmatch=True)


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
# Property 6 — Idempotency
# ---------------------------------------------------------------------------


class TestSessionIdempotency:
    """
    **Validates: Requirements 3.2**

    For any phone number, calling get_or_create_session twice consecutively
    must return the same session_id.
    """

    @given(phone=phone_number_strategy)
    @settings(max_examples=100, deadline=None)
    def test_same_phone_returns_same_session_id(self, phone: str):
        """get_or_create_session is idempotent for the same phone number."""
        with mock_aws():
            _create_session_table()
            mgr = SessionManager("test-session-table", ttl_hours=24)

            first = mgr.get_or_create_session(phone)
            second = mgr.get_or_create_session(phone)

            assert first == second, (
                f"Expected same session_id for phone {phone!r}, "
                f"got {first!r} and {second!r}"
            )


# ---------------------------------------------------------------------------
# Property 6 — Uniqueness
# ---------------------------------------------------------------------------


class TestSessionUniqueness:
    """
    **Validates: Requirements 3.3**

    For any pair of distinct phone numbers, the generated session_id
    values must be different.
    """

    @given(
        phone_a=phone_number_strategy,
        phone_b=phone_number_strategy,
    )
    @settings(max_examples=100, deadline=None)
    def test_different_phones_get_different_session_ids(
        self, phone_a: str, phone_b: str
    ):
        """Distinct phone numbers must produce distinct session_ids."""
        assume(phone_a != phone_b)

        with mock_aws():
            _create_session_table()
            mgr = SessionManager("test-session-table", ttl_hours=24)

            session_a = mgr.get_or_create_session(phone_a)
            session_b = mgr.get_or_create_session(phone_b)

            assert session_a != session_b, (
                f"Expected different session_ids for phones "
                f"{phone_a!r} and {phone_b!r}, but both got {session_a!r}"
            )
