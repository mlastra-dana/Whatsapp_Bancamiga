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


class WhatsAppMediaError(Exception):
    """Raised when media retrieval or download fails."""


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

    def get_media_url(self, media_id: str) -> str:
        """
        Retrieve the download URL for a WhatsApp media item.

        Calls GET https://graph.facebook.com/v21.0/{media_id} with auth header.

        Args:
            media_id: The WhatsApp media ID from the incoming message.

        Returns:
            The media download URL.

        Raises:
            WhatsAppMediaError: If the API call fails or no URL is returned.
        """
        url = f"https://graph.facebook.com/v21.0/{media_id}"
        response = self._http.request("GET", url, headers=self._headers)

        if response.status < 200 or response.status >= 300:
            body_text = response.data.decode("utf-8", errors="replace")
            raise WhatsAppMediaError(
                f"Media URL retrieval failed status={response.status}: {body_text}"
            )

        data = json.loads(response.data.decode("utf-8"))
        media_url = data.get("url")
        if not media_url:
            raise WhatsAppMediaError("No URL in media API response")
        return media_url

    def download_media(self, media_url: str) -> bytes:
        """
        Download binary media content from a WhatsApp media URL.

        Args:
            media_url: The URL returned by get_media_url().

        Returns:
            Raw binary content of the media file.

        Raises:
            WhatsAppMediaError: If the download fails.
        """
        response = self._http.request(
            "GET",
            media_url,
            headers={"Authorization": self._headers["Authorization"]},
        )

        if response.status < 200 or response.status >= 300:
            raise WhatsAppMediaError(
                f"Media download failed status={response.status}"
            )

        return response.data
