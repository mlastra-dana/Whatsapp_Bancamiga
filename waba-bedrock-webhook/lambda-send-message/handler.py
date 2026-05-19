"""
Send Message API Lambda — sends a WhatsApp message to a phone number.

Provides a REST endpoint for the panel to send messages directly to contacts.
"""

import json
import logging
import os

import urllib3

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

_http = urllib3.PoolManager()


def lambda_handler(event, context):
    """Send a WhatsApp text message to a phone number."""
    try:
        # Parse request body
        body = json.loads(event.get("body", "{}"))
        phone = body.get("phone", "")
        message = body.get("message", "")

        if not phone or not message:
            return {
                "statusCode": 400,
                "headers": {
                    "Content-Type": "application/json",
                    "Access-Control-Allow-Origin": "*",
                },
                "body": json.dumps({"error": "phone and message are required"}),
            }

        # Send via WhatsApp Cloud API
        phone_number_id = os.environ["WHATSAPP_PHONE_NUMBER_ID"]
        access_token = os.environ["WHATSAPP_ACCESS_TOKEN"]

        url = f"https://graph.facebook.com/v21.0/{phone_number_id}/messages"
        headers = {
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json",
        }
        payload = {
            "messaging_product": "whatsapp",
            "recipient_type": "individual",
            "to": phone,
            "type": "text",
            "text": {"body": message},
        }

        response = _http.request(
            "POST", url,
            body=json.dumps(payload).encode("utf-8"),
            headers=headers,
        )

        if response.status < 200 or response.status >= 300:
            body_text = response.data.decode("utf-8", errors="replace")
            logger.error("WhatsApp send failed: status=%d body=%s", response.status, body_text)
            return {
                "statusCode": response.status,
                "headers": {
                    "Content-Type": "application/json",
                    "Access-Control-Allow-Origin": "*",
                },
                "body": json.dumps({"error": f"WhatsApp API error: {body_text}"}),
            }

        # Also log to conversations table
        table_name = os.environ.get("CONVERSATIONS_TABLE_NAME")
        if table_name:
            import boto3
            from datetime import datetime, timezone
            dynamodb = boto3.resource("dynamodb")
            table = dynamodb.Table(table_name)
            table.put_item(Item={
                "phone_number": phone,
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "direction": "outbound",
                "message": message[:1000],
                "msg_type": "manual",
            })

        logger.info("Message sent to %s", phone)
        return {
            "statusCode": 200,
            "headers": {
                "Content-Type": "application/json",
                "Access-Control-Allow-Origin": "*",
            },
            "body": json.dumps({"success": True}),
        }

    except Exception as e:
        logger.exception("Error sending message")
        return {
            "statusCode": 500,
            "headers": {
                "Content-Type": "application/json",
                "Access-Control-Allow-Origin": "*",
            },
            "body": json.dumps({"error": str(e)}),
        }
