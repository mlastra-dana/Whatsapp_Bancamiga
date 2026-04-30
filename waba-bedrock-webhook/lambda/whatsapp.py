"""
WhatsApp Cloud API client module.

Encapsulates communication with the WhatsApp Cloud API v21.0 for sending
text messages, including retry logic for transient errors (HTTP 429, 5xx).
"""

import json
import logging
import time

import urllib3

logger = logging.getLogger(__name__)


class WhatsAppSendError(Exception):
    """Raised when sending a WhatsApp message fails after retry."""


def build_message_payload(to: str, text: str) -> dict:
    """
    Build the WhatsApp Cloud API message payload.

    This is a standalone helper so it can be tested independently
    (e.g. via property-based tests) without needing a live client.

    Args:
        to: Destination phone number.
        text: Message body text.

    Returns:
        A dict matching the WhatsApp Cloud API send-message schema.
    """
    return {
        "messaging_product": "whatsapp",
        "recipient_type": "individual",
        "to": to,
        "type": "text",
        "text": {"body": text},
    }


class WhatsAppClient:
    """Client for sending messages via the WhatsApp Cloud API v21.0."""

    def __init__(self, phone_number_id: str, access_token: str):
        """
        Initialise the WhatsApp client.

        Args:
            phone_number_id: The WhatsApp Business phone number ID.
            access_token: Meta access token for API authentication.
        """
        self._url = (
            f"https://graph.facebook.com/v21.0/{phone_number_id}/messages"
        )
        self._headers = {
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json",
        }
        self._http = urllib3.PoolManager()

    def send_text_message(self, to: str, text: str) -> dict:
        """
        Send a text message to a WhatsApp number.

        Retries exactly once after a 1-second wait for transient errors
        (HTTP 429 or 5xx). Does not retry for other error codes.

        Args:
            to: Destination phone number.
            text: Message body text.

        Returns:
            Parsed JSON response from the WhatsApp Cloud API.

        Raises:
            WhatsAppSendError: If sending fails after the retry attempt.
        """
        payload = build_message_payload(to, text)
        encoded_body = json.dumps(payload).encode("utf-8")

        response = self._http.request(
            "POST",
            self._url,
            body=encoded_body,
            headers=self._headers,
        )

        if self._is_transient_error(response.status):
            logger.error(
                "WhatsApp API transient error status=%d body=%s — retrying",
                response.status,
                response.data.decode("utf-8", errors="replace"),
            )
            time.sleep(1)
            response = self._http.request(
                "POST",
                self._url,
                body=encoded_body,
                headers=self._headers,
            )

        if response.status < 200 or response.status >= 300:
            body_text = response.data.decode("utf-8", errors="replace")
            logger.error(
                "WhatsApp API send failed status=%d body=%s",
                response.status,
                body_text,
            )
            raise WhatsAppSendError(
                f"WhatsApp send failed with status {response.status}: "
                f"{body_text}"
            )

        return json.loads(response.data.decode("utf-8"))

    @staticmethod
    def _is_transient_error(status: int) -> bool:
        """Return True for HTTP 429 or 5xx status codes."""
        return status == 429 or 500 <= status <= 599
