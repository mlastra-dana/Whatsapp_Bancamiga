"""
Session manager module for DynamoDB-backed conversation sessions.

Maps WhatsApp phone numbers to Bedrock Agent session IDs, supporting
multi-turn conversations with automatic TTL-based expiration.
"""

import logging
import time
import uuid
from datetime import datetime, timezone

import boto3
from botocore.exceptions import ClientError

logger = logging.getLogger(__name__)


class SessionManager:
    """Manages phone-number-to-session-ID mapping in DynamoDB."""

    def __init__(self, table_name: str, ttl_hours: int = 24):
        """
        Initialise the session manager.

        Args:
            table_name: Name of the DynamoDB table.
            ttl_hours: Hours until a session expires (default 24).
        """
        self._table_name = table_name
        self._ttl_hours = ttl_hours
        dynamodb = boto3.resource("dynamodb")
        self._table = dynamodb.Table(table_name)

    def get_or_create_session(self, phone_number: str) -> str:
        """
        Return an existing session ID or create a new one.

        If a record already exists for *phone_number*, its ``ttl`` and
        ``last_activity`` are updated to extend the session, and the
        existing ``session_id`` is returned.

        Otherwise a new UUID-v4 session ID is generated, stored with
        ``phone_number``, ``session_id``, ``last_activity``, ``ttl``,
        and ``created_at``, and returned.

        Args:
            phone_number: WhatsApp phone number (partition key).

        Returns:
            A UUID-v4 string to use as the Bedrock Agent session ID.
        """
        now = int(time.time())
        new_ttl = now + self._ttl_hours * 3600

        try:
            response = self._table.get_item(Key={"phone_number": phone_number})
        except ClientError:
            logger.exception(
                "DynamoDB GetItem error for phone_number=%s", phone_number
            )
            # Graceful degradation: generate a one-off session ID so the
            # caller can still invoke the Bedrock Agent.
            return str(uuid.uuid4())

        item = response.get("Item")

        if item:
            session_id = item["session_id"]
            # Extend the active session
            try:
                self._table.update_item(
                    Key={"phone_number": phone_number},
                    UpdateExpression="SET #ttl = :ttl, last_activity = :la",
                    ExpressionAttributeNames={"#ttl": "ttl"},
                    ExpressionAttributeValues={
                        ":ttl": new_ttl,
                        ":la": now,
                    },
                )
            except ClientError:
                logger.exception(
                    "DynamoDB UpdateItem error for phone_number=%s",
                    phone_number,
                )
            return session_id

        # No existing session — create a new one
        session_id = str(uuid.uuid4())
        try:
            self._table.put_item(
                Item={
                    "phone_number": phone_number,
                    "session_id": session_id,
                    "last_activity": now,
                    "ttl": new_ttl,
                    "created_at": datetime.now(timezone.utc).isoformat(),
                }
            )
        except ClientError:
            logger.exception(
                "DynamoDB PutItem error for phone_number=%s", phone_number
            )

        return session_id
