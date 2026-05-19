"""
Conversations API Lambda — reads all chat interactions from DynamoDB.

Provides a REST endpoint to list all conversations grouped by phone number.
"""

import json
import logging
import os

import boto3

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

_table = None


def _get_table():
    """Lazily initialize the DynamoDB table resource."""
    global _table
    if _table is None:
        table_name = os.environ["CONVERSATIONS_TABLE_NAME"]
        dynamodb = boto3.resource("dynamodb")
        _table = dynamodb.Table(table_name)
    return _table


def lambda_handler(event, context):
    """Return all conversations as JSON.
    
    Supports optional query parameter ?phone=NUMBER to filter by phone.
    """
    try:
        table = _get_table()
        
        # Check for phone filter
        params = event.get("queryStringParameters") or {}
        phone_filter = params.get("phone")
        
        if phone_filter:
            # Query by phone number (partition key)
            response = table.query(
                KeyConditionExpression="phone_number = :phone",
                ExpressionAttributeValues={":phone": phone_filter},
            )
            items = response.get("Items", [])
        else:
            # Scan all conversations
            response = table.scan()
            items = response.get("Items", [])

        # Sort by timestamp descending
        items.sort(key=lambda x: x.get("timestamp", ""), reverse=True)

        return {
            "statusCode": 200,
            "headers": {
                "Content-Type": "application/json",
                "Access-Control-Allow-Origin": "*",
                "Access-Control-Allow-Methods": "GET,OPTIONS",
                "Access-Control-Allow-Headers": "Content-Type",
            },
            "body": json.dumps(items, default=str),
        }
    except Exception:
        logger.exception("Error reading conversations")
        return {
            "statusCode": 500,
            "headers": {
                "Content-Type": "application/json",
                "Access-Control-Allow-Origin": "*",
            },
            "body": json.dumps({"error": "Internal server error"}),
        }
