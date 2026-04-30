"""
Lambda handler module — main entry point for the WABA Bedrock Webhook.

Orchestrates the full message processing flow:
- Webhook verification (GET requests)
- Message parsing and validation (POST requests)
- Session management, prompt reading, Bedrock Agent invocation, and WhatsApp response
"""

import json
import logging
import os
import re
import time

from bedrock_agent import BedrockAgentError

# Configure root logger level for Lambda / CloudWatch
logging.getLogger().setLevel(logging.INFO)

logger = logging.getLogger(__name__)

# Default error message sent to the user when Bedrock Agent fails or times out.
DEFAULT_ERROR_MESSAGE = (
    "Lo siento, no pude procesar tu solicitud en este momento. "
    "Por favor, intenta de nuevo más tarde."
)

# Module-level service instances — initialised on first invocation via
# _init_services() and reused across warm starts.
session_manager = None
prompt_reader = None
bedrock_client = None
whatsapp_client = None


def _init_services() -> None:
    """Lazily initialise module-level service instances from environment variables.

    Called once on the first Lambda invocation.  Subsequent warm-start
    invocations reuse the already-created instances.
    """
    global session_manager, prompt_reader, bedrock_client, whatsapp_client

    if session_manager is not None:
        return  # already initialised

    from session_manager import SessionManager
    from prompt_reader import PromptReader
    from bedrock_agent import BedrockAgentClient
    from whatsapp import WhatsAppClient

    session_manager = SessionManager(os.environ["SESSION_TABLE_NAME"])
    prompt_reader = PromptReader(
        os.environ["SYSTEM_PROMPT_BUCKET"],
        os.environ["SYSTEM_PROMPT_KEY"],
    )
    bedrock_client = BedrockAgentClient(
        os.environ["BEDROCK_AGENT_ID"],
        os.environ["BEDROCK_AGENT_ALIAS_ID"],
    )
    whatsapp_client = WhatsAppClient(
        os.environ["WHATSAPP_PHONE_NUMBER_ID"],
        os.environ["WHATSAPP_ACCESS_TOKEN"],
    )


def markdown_to_whatsapp(text: str) -> str:
    """Convert Markdown formatting to WhatsApp formatting.

    WhatsApp uses:
      *bold*  _italic_  ~strikethrough~  ```monospace```

    Markdown uses:
      **bold** or __bold__   *italic* or _italic_   ~~strike~~   `code`
      # headers   [links](url)   numbered lists with periods
    """
    # Remove ### / ## / # headers — keep the text
    text = re.sub(r"^#{1,6}\s+", "", text, flags=re.MULTILINE)

    # **bold** or __bold__ → *bold*
    text = re.sub(r"\*\*(.+?)\*\*", r"*\1*", text)
    text = re.sub(r"__(.+?)__", r"*\1*", text)

    # ~~strikethrough~~ → ~strikethrough~
    text = re.sub(r"~~(.+?)~~", r"~\1~", text)

    # Inline `code` → (leave as-is, WhatsApp doesn't render single backticks well)
    # But triple backtick code blocks → ```code```
    text = re.sub(r"```(\w*)\n(.*?)```", r"```\2```", text, flags=re.DOTALL)

    # [link text](url) → link text (url)
    text = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", r"\1 (\2)", text)

    # Numbered lists: "1. item" → "1. item" (already fine for WhatsApp)
    # Bullet lists: "- item" or "* item" at start of line (already fine)

    return text


def handle_verification(params: dict) -> dict:
    """Handle GET requests for webhook verification.

    Validates the presence of required query string parameters and compares
    the verify token against the WHATSAPP_VERIFY_TOKEN environment variable.

    Args:
        params: Query string parameters from the GET request.

    Returns:
        dict with statusCode and body for the API Gateway response.
    """
    required_params = ["hub.mode", "hub.verify_token", "hub.challenge"]
    for param in required_params:
        if param not in params:
            return {"statusCode": 400, "body": "Missing required parameters"}

    verify_token = os.environ["WHATSAPP_VERIFY_TOKEN"]
    if params["hub.verify_token"] != verify_token:
        return {"statusCode": 403, "body": "Forbidden"}

    return {"statusCode": 200, "body": params["hub.challenge"]}


def extract_text_messages(body: dict) -> list[dict]:
    """Extract processable messages from a WhatsApp Cloud API webhook payload.

    Navigates ``body["entry"][*]["changes"][*]["value"]["messages"]`` and
    extracts text from all supported message types:

    - ``text``: regular text messages
    - ``button``: Quick Reply button taps (button title as text)
    - ``interactive``: interactive list/button replies
    - ``image``, ``video``, ``document``, ``sticker``: caption if present
    - ``location``: formatted as "Ubicación: lat, lon (name)"
    - ``contacts``: formatted as "Contacto: name, phone"
    - ``reaction``: ignored (no actionable text)
    - ``audio``: ignored (would need transcription)
    - ``order``: formatted as summary

    Args:
        body: Parsed JSON body of the incoming POST request.

    Returns:
        A list of dicts, each containing ``from`` (phone number),
        ``text`` (message body) and ``id`` (message ID).
    """
    messages: list[dict] = []
    for entry in body.get("entry", []):
        for change in entry.get("changes", []):
            value = change.get("value", {})
            for msg in value.get("messages", []):
                msg_type = msg.get("type")
                text = None

                if msg_type == "text":
                    text = msg["text"]["body"]

                elif msg_type == "button":
                    text = msg.get("button", {}).get("text")

                elif msg_type == "interactive":
                    interactive = msg.get("interactive", {})
                    reply_type = interactive.get("type")
                    if reply_type == "button_reply":
                        text = interactive.get("button_reply", {}).get("title")
                    elif reply_type == "list_reply":
                        text = interactive.get("list_reply", {}).get("title")

                elif msg_type in ("image", "video", "document", "sticker"):
                    caption = msg.get(msg_type, {}).get("caption")
                    if caption:
                        text = caption
                    else:
                        text = f"[El usuario envió un archivo de tipo {msg_type}]"

                elif msg_type == "location":
                    loc = msg.get("location", {})
                    lat = loc.get("latitude", "")
                    lon = loc.get("longitude", "")
                    name = loc.get("name", "")
                    addr = loc.get("address", "")
                    if name:
                        text = f"[Ubicación compartida: {name}, {addr}] ({lat}, {lon})"
                    else:
                        text = f"[Ubicación compartida: {lat}, {lon}]"

                elif msg_type == "contacts":
                    contacts = msg.get("contacts", [])
                    if contacts:
                        c = contacts[0]
                        name = c.get("name", {}).get("formatted_name", "Desconocido")
                        phones = c.get("phones", [])
                        phone = phones[0].get("phone", "") if phones else ""
                        text = f"[Contacto compartido: {name}, {phone}]"

                elif msg_type == "order":
                    order = msg.get("order", {})
                    items = order.get("product_items", [])
                    text = f"[Pedido con {len(items)} producto(s)]"

                # Skip: audio (needs transcription), reaction (no text),
                # system, unknown types

                if text:
                    messages.append(
                        {
                            "from": msg["from"],
                            "text": text,
                            "id": msg["id"],
                        }
                    )
    return messages


def handle_message(body: dict) -> dict:
    """Process an incoming POST request containing WhatsApp messages.

    For each text message in the payload the function:
    1. Logs the incoming message (phone number and type only — never content).
    2. Gets or creates a conversation session via :pyclass:`SessionManager`.
    3. Reads the system prompt via :pyclass:`PromptReader`.
    4. Invokes the :pyclass:`BedrockAgentClient` with the user's text and
       logs the session ID and response time in milliseconds.
    5. Sends the agent's response back to the user via :pyclass:`WhatsAppClient`
       and logs the HTTP status code.

    If the Bedrock Agent fails or times out, a default error message is
    sent to the user instead.

    The function always returns HTTP 200 to prevent Meta from retrying
    the webhook delivery.

    Args:
        body: Parsed JSON body of the incoming POST request.

    Returns:
        dict with ``statusCode`` 200 and a JSON ``body``.
    """
    try:
        text_messages = extract_text_messages(body)
    except Exception:
        logger.warning("Malformed WhatsApp payload — ignoring")
        return {"statusCode": 200, "body": "ok"}

    for msg in text_messages:
        phone_number = msg["from"]
        user_text = msg["text"]
        msg_type = "text"

        # Structured log: incoming message (never log message content)
        logger.info(
            "Incoming message phone=%s type=%s", phone_number, msg_type
        )

        try:
            # 1. Session
            session_id = session_manager.get_or_create_session(phone_number)

            # 2. System prompt (read once per invocation, cached internally)
            prompt_reader.get_prompt()

            # 3. Bedrock Agent — measure response time
            try:
                start_time = time.time()
                response_text = bedrock_client.invoke(user_text, session_id)
                elapsed_ms = int((time.time() - start_time) * 1000)
                logger.info(
                    "Bedrock Agent invocation session=%s response_time_ms=%d",
                    session_id,
                    elapsed_ms,
                )
            except BedrockAgentError:
                logger.exception(
                    "Bedrock Agent error for phone=%s session=%s",
                    phone_number,
                    session_id,
                )
                response_text = DEFAULT_ERROR_MESSAGE

            # 4. Send response via WhatsApp
            try:
                response_text = markdown_to_whatsapp(response_text)
                result = whatsapp_client.send_text_message(
                    phone_number, response_text
                )
                status = "200"
                logger.info(
                    "WhatsApp send phone=%s status=%s", phone_number, status
                )
            except Exception as send_exc:
                # Extract status from WhatsAppSendError message if available
                status = str(getattr(send_exc, "status", "error"))
                logger.info(
                    "WhatsApp send phone=%s status=%s", phone_number, status
                )
                raise

        except Exception:
            logger.exception(
                "Unhandled error processing message from phone=%s",
                phone_number,
            )

    return {"statusCode": 200, "body": "ok"}


def lambda_handler(event: dict, context) -> dict:
    """AWS Lambda entry point.

    Routes incoming API Gateway events:
    - **GET** requests are forwarded to :func:`handle_verification`.
    - **POST** requests are forwarded to :func:`handle_message`.

    On the first invocation the function lazily initialises the service
    instances (``SessionManager``, ``PromptReader``, ``BedrockAgentClient``,
    ``WhatsAppClient``) so they are reused across warm starts.

    Any unhandled exception is caught, the full traceback is logged, and
    HTTP 200 is returned to prevent Meta from retrying the webhook.

    Args:
        event: API Gateway Lambda Proxy event.
        context: Lambda execution context.

    Returns:
        dict with ``statusCode``, ``headers``, and ``body``.
    """
    try:
        _init_services()

        http_method = event.get("httpMethod", "")

        if http_method == "GET":
            params = event.get("queryStringParameters") or {}
            return handle_verification(params)

        if http_method == "POST":
            body_str = event.get("body", "")
            body = json.loads(body_str) if body_str else {}
            return handle_message(body)

        return {"statusCode": 405, "body": "Method Not Allowed"}

    except Exception:
        logger.exception("Unhandled exception in lambda_handler")
        return {"statusCode": 200, "body": "ok"}
