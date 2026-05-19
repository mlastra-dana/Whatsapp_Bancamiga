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

VISION_KEYWORDS = ["visión computacional", "analizar imagen", "describir foto"]

VISION_MODE_CONFIRMATION = (
    "🔍 Modo de visión computacional activado. "
    "Por favor, envía la imagen que deseas analizar."
)

VISION_MODE_REMINDER = (
    "📷 Estoy esperando una imagen para analizar. "
    "Por favor, envía una foto."
)

VISION_ERROR_MESSAGE = (
    "Lo siento, no pude analizar la imagen. "
    "Por favor, intenta de nuevo más tarde."
)

# Module-level service instances — initialised on first invocation via
# _init_services() and reused across warm starts.
session_manager = None
prompt_reader = None
bedrock_client = None
whatsapp_client = None
vision_analyzer = None
_conversations_table = None


def _log_conversation(phone_number: str, direction: str, message: str, msg_type: str = "text") -> None:
    """Save a conversation message to DynamoDB.
    
    Args:
        phone_number: WhatsApp phone number.
        direction: "inbound" (user → bot) or "outbound" (bot → user).
        message: The message text.
        msg_type: Message type (text, image, vision, etc.)
    """
    global _conversations_table
    if _conversations_table is None:
        import boto3
        table_name = os.environ.get("CONVERSATIONS_TABLE_NAME")
        if not table_name:
            return
        dynamodb = boto3.resource("dynamodb")
        _conversations_table = dynamodb.Table(table_name)
    
    try:
        from datetime import datetime, timezone
        _conversations_table.put_item(Item={
            "phone_number": phone_number,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "direction": direction,
            "message": message[:1000],  # Truncate very long messages
            "msg_type": msg_type,
        })
    except Exception:
        logger.warning("Failed to log conversation for phone=%s", phone_number)


def _send_and_log(phone_number: str, text: str, msg_type: str = "text") -> None:
    """Send a WhatsApp message and log it as outbound conversation."""
    whatsapp_client.send_text_message(phone_number, text)
    _log_conversation(phone_number, "outbound", text, msg_type)


def _notify_slack(phone_number: str, message: str) -> None:
    """Send a notification to Slack when a new message arrives."""
    slack_url = os.environ.get("SLACK_WEBHOOK_URL")
    if not slack_url:
        return
    try:
        import urllib3
        http = urllib3.PoolManager()
        payload = {
            "text": f"💬 *Nuevo mensaje WhatsApp*\n📱 {phone_number}\n> {message[:200]}"
        }
        http.request(
            "POST", slack_url,
            body=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
        )
    except Exception:
        logger.warning("Failed to send Slack notification")


def _init_services() -> None:
    """Lazily initialise module-level service instances from environment variables.

    Called once on the first Lambda invocation.  Subsequent warm-start
    invocations reuse the already-created instances.
    """
    global session_manager, prompt_reader, bedrock_client, whatsapp_client, vision_analyzer

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

    from vision_analyzer import VisionAnalyzer
    vision_analyzer = VisionAnalyzer()


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


def detect_vision_intent(text: str) -> bool:
    """Check if a text message contains vision-related keywords.

    Performs case-insensitive partial matching against VISION_KEYWORDS.
    """
    text_lower = text.lower()
    return any(keyword in text_lower for keyword in VISION_KEYWORDS)


def extract_image_metadata(msg: dict) -> tuple[str, str] | None:
    """Extract media ID and MIME type from an image message.

    Args:
        msg: A raw message dict from the WhatsApp webhook payload.

    Returns:
        A tuple of (media_id, mime_type) or None if not an image message.
    """
    if msg.get("type") != "image":
        return None
    image_data = msg.get("image", {})
    media_id = image_data.get("id")
    mime_type = image_data.get("mime_type", "image/jpeg")
    if not media_id:
        return None
    return (media_id, mime_type)


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

    Routes each message based on session mode and message type:
    - Image message in vision mode → vision analysis pipeline
    - Text message with vision keyword → enter vision mode
    - Text message in vision mode → send reminder to upload image
    - Otherwise → standard Bedrock Agent flow

    The function always returns HTTP 200 to prevent Meta from retrying
    the webhook delivery.

    Args:
        body: Parsed JSON body of the incoming POST request.

    Returns:
        dict with ``statusCode`` 200 and a JSON ``body``.
    """
    try:
        # Extract raw messages from the webhook payload
        raw_messages = []
        for entry in body.get("entry", []):
            for change in entry.get("changes", []):
                value = change.get("value", {})
                for msg in value.get("messages", []):
                    raw_messages.append(msg)
    except Exception:
        logger.warning("Malformed WhatsApp payload — ignoring")
        return {"statusCode": 200, "body": "ok"}

    for msg in raw_messages:
        phone_number = msg.get("from", "")
        msg_type = msg.get("type", "")

        logger.info("Incoming message phone=%s type=%s", phone_number, msg_type)

        # Log inbound message to conversations table
        inbound_text = ""
        if msg_type == "text":
            inbound_text = msg.get("text", {}).get("body", "")
        elif msg_type == "image":
            inbound_text = msg.get("image", {}).get("caption", "[imagen]") or "[imagen]"
        else:
            inbound_text = f"[{msg_type}]"
        _log_conversation(phone_number, "inbound", inbound_text, msg_type)
        _notify_slack(phone_number, inbound_text)

        try:
            # Get or create session
            session_id = session_manager.get_or_create_session(phone_number)

            # Check current session mode
            current_mode = session_manager.get_mode(phone_number)

            # CASE 1: Image message in vision mode → analyze
            if msg_type == "image" and current_mode == "vision":
                metadata = extract_image_metadata(msg)
                if metadata:
                    media_id, mime_type = metadata
                    try:
                        media_url = whatsapp_client.get_media_url(media_id)
                        image_bytes = whatsapp_client.download_media(media_url)
                        description = vision_analyzer.analyze(image_bytes, mime_type)
                        description = markdown_to_whatsapp(description)
                        _send_and_log(phone_number, description, "vision")
                    except Exception:
                        logger.exception("Vision analysis failed for phone=%s", phone_number)
                        _send_and_log(phone_number, VISION_ERROR_MESSAGE)
                    finally:
                        session_manager.set_mode(phone_number, None)
                else:
                    # Image message but couldn't extract metadata
                    _send_and_log(phone_number, VISION_ERROR_MESSAGE)
                    session_manager.set_mode(phone_number, None)

            # CASE 2: Text message — check for vision keyword or vision mode
            elif msg_type == "text":
                user_text = msg.get("text", {}).get("body", "")

                if detect_vision_intent(user_text):
                    # Vision keyword detected → enter vision mode
                    session_manager.set_mode(phone_number, "vision")
                    _send_and_log(phone_number, VISION_MODE_CONFIRMATION)
                elif current_mode == "vision":
                    # In vision mode but sent text → remind to send image
                    _send_and_log(phone_number, VISION_MODE_REMINDER)
                else:
                    # Standard Bedrock Agent flow
                    prompt_reader.get_prompt()
                    try:
                        start_time = time.time()
                        response_text = bedrock_client.invoke(
                            user_text, session_id,
                            session_attributes={"user_phone": phone_number},
                            prompt_session_attributes={"user_phone": phone_number},
                        )
                        elapsed_ms = int((time.time() - start_time) * 1000)
                        logger.info(
                            "Bedrock Agent invocation session=%s response_time_ms=%d",
                            session_id, elapsed_ms,
                        )
                    except BedrockAgentError:
                        logger.exception(
                            "Bedrock Agent error for phone=%s session=%s",
                            phone_number, session_id,
                        )
                        response_text = DEFAULT_ERROR_MESSAGE

                    response_text = markdown_to_whatsapp(response_text)
                    _send_and_log(phone_number, response_text)

            # CASE 3: Other message types (button, interactive, location, etc.)
            else:
                # Extract text using existing extract_text_messages logic for this single message
                text_msgs = extract_text_messages({"entry": [{"changes": [{"value": {"messages": [msg]}}]}]})
                if text_msgs:
                    user_text = text_msgs[0]["text"]
                    prompt_reader.get_prompt()
                    try:
                        start_time = time.time()
                        response_text = bedrock_client.invoke(
                            user_text, session_id,
                            session_attributes={"user_phone": phone_number},
                            prompt_session_attributes={"user_phone": phone_number},
                        )
                        elapsed_ms = int((time.time() - start_time) * 1000)
                        logger.info(
                            "Bedrock Agent invocation session=%s response_time_ms=%d",
                            session_id, elapsed_ms,
                        )
                    except BedrockAgentError:
                        logger.exception(
                            "Bedrock Agent error for phone=%s session=%s",
                            phone_number, session_id,
                        )
                        response_text = DEFAULT_ERROR_MESSAGE

                    response_text = markdown_to_whatsapp(response_text)
                    _send_and_log(phone_number, response_text)

        except Exception:
            logger.exception(
                "Unhandled error processing message from phone=%s", phone_number
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
