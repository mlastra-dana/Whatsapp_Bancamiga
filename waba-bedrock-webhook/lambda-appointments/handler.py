"""
Appointments API Lambda — reads appointment records from DynamoDB.

Provides a simple REST endpoint to list all appointments, sorted by
creation date (most recent first).
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
        table_name = os.environ["APPOINTMENTS_TABLE_NAME"]
        dynamodb = boto3.resource("dynamodb")
        _table = dynamodb.Table(table_name)
    return _table


def lambda_handler(event, context):
    """Return all appointments as JSON."""
    try:
        table = _get_table()
        response = table.scan()
        items = response.get("Items", [])

        # Sort by created_at descending (most recent first)
        items.sort(key=lambda x: x.get("created_at", ""), reverse=True)

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
        logger.exception("Error reading appointments")
        return {
            "statusCode": 500,
            "headers": {
                "Content-Type": "application/json",
                "Access-Control-Allow-Origin": "*",
            },
            "body": json.dumps({"error": "Internal server error"}),
        }
