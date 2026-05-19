# Design Document: Computer Vision Branch

## Architecture Overview

The computer vision branch extends the existing webhook handler flow by introducing a **mode-based routing layer** between message reception and processing. The architecture follows the existing pattern of module separation (handler → service clients) and adds a new `vision_analyzer.py` module alongside modifications to `handler.py`, `session_manager.py`, and `whatsapp.py`.

### High-Level Flow

```
WhatsApp User
     │
     ▼
API Gateway → Lambda handler.py
     │
     ├─ Text message received
     │    ├─ Keyword match? → set mode="vision", send confirmation
     │    ├─ Mode is "vision"? → send reminder ("please send image")
     │    └─ No keyword → Bedrock Agent (existing flow)
     │
     └─ Image message received
          ├─ Mode is "vision"?
          │    ├─ Extract media_id + mime_type
          │    ├─ WhatsApp Media API → get URL → download binary
          │    ├─ vision_analyzer.py → base64 encode → Bedrock invoke_model
          │    ├─ Send description to user
          │    └─ Reset mode to null
          └─ Mode is NOT "vision"? → existing placeholder flow
```

### Integration Points

1. **handler.py** — New routing logic inserted *before* the Bedrock Agent invocation. Checks keywords first (fast path), then checks session mode for image routing.
2. **session_manager.py** — New `set_mode()` and `get_mode()` methods on the existing `SessionManager` class.
3. **whatsapp.py** — New `get_media_url()` and `download_media()` methods on the existing `WhatsAppClient` class.
4. **vision_analyzer.py** — New module using `bedrock-runtime` client with `invoke_model`.
5. **CDK stack** — Additional IAM policy statement for `bedrock:InvokeModel` on the multimodal model.

---

## Components

### New Module: `vision_analyzer.py`

Responsible for invoking the Bedrock Runtime multimodal model with base64-encoded image data and a Spanish-language prompt.

```python
"""
Vision analyzer module for multimodal image analysis via Bedrock Runtime.

Encapsulates invocation of Claude with vision capabilities to produce
detailed image descriptions in Spanish.
"""

import base64
import json
import logging

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError, ReadTimeoutError

logger = logging.getLogger(__name__)

VISION_MODEL_ID = "us.anthropic.claude-sonnet-4-6"

VISION_PROMPT = (
    "Eres un asistente de visión computacional. Analiza la imagen proporcionada "
    "y genera una descripción detallada en español de todo lo que observas: "
    "objetos, personas, colores, texto visible, contexto y cualquier detalle relevante."
)


class VisionAnalyzerError(Exception):
    """Raised when vision analysis fails or times out."""


def build_vision_payload(image_bytes: bytes, mime_type: str) -> dict:
    """
    Build the Bedrock invoke_model payload for multimodal analysis.

    Args:
        image_bytes: Raw binary image data.
        mime_type: MIME type of the image (e.g. "image/jpeg").

    Returns:
        A dict matching the Claude Messages API schema with image content.
    """
    image_b64 = base64.b64encode(image_bytes).decode("utf-8")
    return {
        "anthropic_version": "bedrock-2023-05-31",
        "max_tokens": 1024,
        "messages": [
            {
                "role": "user",
                "content": [
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": mime_type,
                            "data": image_b64,
                        },
                    },
                    {
                        "type": "text",
                        "text": VISION_PROMPT,
                    },
                ],
            }
        ],
    }


def extract_vision_response(response_body: dict) -> str:
    """
    Extract the text content from a Bedrock Claude Messages API response.

    Args:
        response_body: Parsed JSON response from invoke_model.

    Returns:
        The concatenated text content from the response.
    """
    parts: list[str] = []
    for block in response_body.get("content", []):
        if block.get("type") == "text":
            parts.append(block["text"])
    return "".join(parts)


class VisionAnalyzer:
    """Client for multimodal image analysis via Bedrock Runtime."""

    def __init__(self, model_id: str = VISION_MODEL_ID):
        """
        Initialise the vision analyzer.

        Args:
            model_id: The Bedrock model ID for multimodal analysis.
        """
        self._model_id = model_id
        self._client = boto3.client(
            "bedrock-runtime",
            config=Config(read_timeout=25, connect_timeout=5),
        )

    def analyze(self, image_bytes: bytes, mime_type: str) -> str:
        """
        Analyze an image and return a Spanish-language description.

        Args:
            image_bytes: Raw binary image data.
            mime_type: MIME type of the image.

        Returns:
            A detailed description of the image content in Spanish.

        Raises:
            VisionAnalyzerError: If the invocation fails or times out.
        """
        payload = build_vision_payload(image_bytes, mime_type)
        try:
            response = self._client.invoke_model(
                modelId=self._model_id,
                contentType="application/json",
                accept="application/json",
                body=json.dumps(payload).encode("utf-8"),
            )
            response_body = json.loads(response["body"].read())
            return extract_vision_response(response_body)
        except ReadTimeoutError as exc:
            logger.exception("Vision model invocation timed out")
            raise VisionAnalyzerError("Vision model timed out") from exc
        except ClientError as exc:
            logger.exception("Vision model invocation failed")
            raise VisionAnalyzerError(f"Vision model error: {exc}") from exc
        except Exception as exc:
            logger.exception("Unexpected error in vision analysis")
            raise VisionAnalyzerError(
                f"Unexpected vision error: {exc}"
            ) from exc
```

### Modified Module: `session_manager.py`

Add `set_mode()` and `get_mode()` methods to the existing `SessionManager` class.

```python
def set_mode(self, phone_number: str, mode: str | None) -> None:
    """
    Set or clear the session mode for a phone number.

    Args:
        phone_number: WhatsApp phone number (partition key).
        mode: The mode value to set (e.g. "vision"), or None to clear.
    """
    try:
        if mode is None:
            self._table.update_item(
                Key={"phone_number": phone_number},
                UpdateExpression="REMOVE #mode",
                ExpressionAttributeNames={"#mode": "mode"},
            )
        else:
            self._table.update_item(
                Key={"phone_number": phone_number},
                UpdateExpression="SET #mode = :mode",
                ExpressionAttributeNames={"#mode": "mode"},
                ExpressionAttributeValues={":mode": mode},
            )
    except ClientError:
        logger.exception(
            "DynamoDB set_mode error for phone_number=%s mode=%s",
            phone_number,
            mode,
        )


def get_mode(self, phone_number: str) -> str | None:
    """
    Get the current session mode for a phone number.

    Args:
        phone_number: WhatsApp phone number (partition key).

    Returns:
        The mode string (e.g. "vision") or None if not set.
    """
    try:
        response = self._table.get_item(
            Key={"phone_number": phone_number},
            ProjectionExpression="#mode",
            ExpressionAttributeNames={"#mode": "mode"},
        )
        item = response.get("Item", {})
        return item.get("mode")
    except ClientError:
        logger.exception(
            "DynamoDB get_mode error for phone_number=%s", phone_number
        )
        return None
```

### Modified Module: `whatsapp.py`

Add `get_media_url()` and `download_media()` methods to the existing `WhatsAppClient` class.

```python
class WhatsAppMediaError(Exception):
    """Raised when media retrieval or download fails."""


def get_media_url(self, media_id: str) -> str:
    """
    Retrieve the download URL for a WhatsApp media item.

    Calls GET https://graph.facebook.com/v21.0/{media_id} with auth header.

    Args:
        media_id: The WhatsApp media ID from the incoming message.

    Returns:
        The media download URL.

    Raises:
        WhatsAppMediaError: If the API call fails.
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
```

### Modified Module: `handler.py`

Key changes to the message processing flow:

```python
# --- New constants ---
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


# --- New function ---
def detect_vision_intent(text: str) -> bool:
    """
    Check if a text message contains vision-related keywords.

    Performs case-insensitive partial matching against VISION_KEYWORDS.

    Args:
        text: The user's message text.

    Returns:
        True if any vision keyword is found in the text.
    """
    text_lower = text.lower()
    return any(keyword in text_lower for keyword in VISION_KEYWORDS)


# --- New function ---
def extract_image_metadata(msg: dict) -> tuple[str, str] | None:
    """
    Extract media ID and MIME type from an image message.

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


# --- Modified handle_message flow (pseudocode for routing) ---
def handle_message(body: dict) -> dict:
    """Updated routing logic within handle_message."""
    # ... existing message extraction ...

    for msg in raw_messages:
        phone_number = msg["from"]
        msg_type = msg.get("type")
        session_id = session_manager.get_or_create_session(phone_number)
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
                    whatsapp_client.send_text_message(phone_number, description)
                except Exception:
                    whatsapp_client.send_text_message(
                        phone_number, VISION_ERROR_MESSAGE
                    )
                finally:
                    session_manager.set_mode(phone_number, None)

        # CASE 2: Text message with vision keyword → enter vision mode
        elif msg_type == "text":
            user_text = msg["text"]["body"]
            if detect_vision_intent(user_text):
                session_manager.set_mode(phone_number, "vision")
                whatsapp_client.send_text_message(
                    phone_number, VISION_MODE_CONFIRMATION
                )
            elif current_mode == "vision":
                # In vision mode but sent text → remind to send image
                whatsapp_client.send_text_message(
                    phone_number, VISION_MODE_REMINDER
                )
            else:
                # Standard Bedrock Agent flow (existing)
                # ... existing code ...
                pass

        # CASE 3: Image message NOT in vision mode → existing placeholder
        elif msg_type == "image" and current_mode != "vision":
            # Existing behavior: extract caption or placeholder text
            # ... existing code ...
            pass

    return {"statusCode": 200, "body": "ok"}
```

### CDK Stack Changes

Add IAM permission for `bedrock:InvokeModel` on the multimodal model:

```typescript
// Grant Lambda permission to invoke the multimodal model for vision analysis
this.webhookHandler.addToRolePolicy(new iam.PolicyStatement({
  actions: ['bedrock:InvokeModel'],
  resources: [
    `arn:aws:bedrock:${this.region}:${this.account}:inference-profile/${bedrockModelArn}`,
    `arn:aws:bedrock:*::foundation-model/anthropic.claude-sonnet-4-6`,
  ],
}));
```

---

## Data Models

### DynamoDB Session Record (Extended)

| Field | Type | Description |
|-------|------|-------------|
| `phone_number` | String (PK) | WhatsApp phone number |
| `session_id` | String | UUID-v4 Bedrock Agent session ID |
| `mode` | String \| null | Current session mode ("vision" or absent) |
| `last_activity` | Number | Unix timestamp of last activity |
| `ttl` | Number | Unix timestamp for DynamoDB TTL expiration |
| `created_at` | String | ISO 8601 creation timestamp |

### WhatsApp Media API — Get Media URL

**Request:**
```
GET https://graph.facebook.com/v21.0/{media_id}
Authorization: Bearer {access_token}
```

**Response:**
```json
{
  "id": "media_id_string",
  "url": "https://lookaside.fbsbx.com/...",
  "mime_type": "image/jpeg",
  "sha256": "...",
  "file_size": 12345
}
```

### WhatsApp Incoming Image Message Payload

```json
{
  "from": "5215512345678",
  "id": "wamid.abc123",
  "type": "image",
  "image": {
    "id": "media_id_string",
    "mime_type": "image/jpeg",
    "sha256": "...",
    "caption": "optional caption text"
  }
}
```

### Bedrock invoke_model Request (Multimodal)

```json
{
  "anthropic_version": "bedrock-2023-05-31",
  "max_tokens": 1024,
  "messages": [
    {
      "role": "user",
      "content": [
        {
          "type": "image",
          "source": {
            "type": "base64",
            "media_type": "image/jpeg",
            "data": "<base64_encoded_image>"
          }
        },
        {
          "type": "text",
          "text": "Eres un asistente de visión computacional. Analiza la imagen..."
        }
      ]
    }
  ]
}
```

### Bedrock invoke_model Response

```json
{
  "id": "msg_...",
  "type": "message",
  "role": "assistant",
  "content": [
    {
      "type": "text",
      "text": "La imagen muestra..."
    }
  ],
  "stop_reason": "end_turn",
  "usage": {"input_tokens": 1500, "output_tokens": 200}
}
```

---

## Interfaces

### `vision_analyzer.py`

| Function | Signature | Description |
|----------|-----------|-------------|
| `build_vision_payload` | `(image_bytes: bytes, mime_type: str) -> dict` | Builds the Bedrock Messages API payload with base64 image |
| `extract_vision_response` | `(response_body: dict) -> str` | Extracts concatenated text from model response |
| `VisionAnalyzer.analyze` | `(self, image_bytes: bytes, mime_type: str) -> str` | Full analysis pipeline: encode → invoke → extract |

### `session_manager.py` (new methods)

| Function | Signature | Description |
|----------|-----------|-------------|
| `SessionManager.set_mode` | `(self, phone_number: str, mode: str \| None) -> None` | Sets or clears the session mode |
| `SessionManager.get_mode` | `(self, phone_number: str) -> str \| None` | Returns current mode or None |

### `whatsapp.py` (new methods)

| Function | Signature | Description |
|----------|-----------|-------------|
| `WhatsAppClient.get_media_url` | `(self, media_id: str) -> str` | Retrieves download URL from Media API |
| `WhatsAppClient.download_media` | `(self, media_url: str) -> bytes` | Downloads binary media content |

### `handler.py` (new functions)

| Function | Signature | Description |
|----------|-----------|-------------|
| `detect_vision_intent` | `(text: str) -> bool` | Keyword-based vision intent detection |
| `extract_image_metadata` | `(msg: dict) -> tuple[str, str] \| None` | Extracts (media_id, mime_type) from image message |

---

## Error Handling

| Failure Point | Error Type | User Impact | Recovery |
|---------------|-----------|-------------|----------|
| Media URL retrieval (4xx/5xx from Graph API) | `WhatsAppMediaError` | User receives Spanish error message | Mode reset to null |
| Media download (network/timeout) | `WhatsAppMediaError` | User receives Spanish error message | Mode reset to null |
| Bedrock invoke_model timeout | `VisionAnalyzerError` | User receives Spanish error message | Mode reset to null |
| Bedrock invoke_model ClientError | `VisionAnalyzerError` | User receives Spanish error message | Mode reset to null |
| WhatsApp send failure (response delivery) | `WhatsAppSendError` | Message not delivered, logged | Mode reset to null |
| DynamoDB set_mode failure | Logged, swallowed | Mode may be stale; next message re-evaluates | Graceful degradation |

**Key invariant:** The session mode is ALWAYS reset to null after a vision analysis attempt, regardless of success or failure. This prevents users from being stuck in vision mode.

---

## Correctness Properties

*A property is a characteristic or behavior that should hold true across all valid executions of a system — essentially, a formal statement about what the system should do. Properties serve as the bridge between human-readable specifications and machine-verifiable correctness guarantees.*

### Property 1: Vision keyword detection is case-insensitive and partial

*For any* text string that contains one of the vision keywords ("visión computacional", "analizar imagen", "describir foto") in any combination of upper/lower case and surrounded by arbitrary text, `detect_vision_intent` SHALL return True. *For any* text string that does not contain any of these keywords (regardless of case), it SHALL return False.

**Validates: Requirements 1.1**

### Property 2: Session mode round-trip

*For any* valid phone number, setting the session mode to "vision" via `set_mode` and then reading it via `get_mode` SHALL return "vision". Subsequently resetting the mode to None via `set_mode` and reading it via `get_mode` SHALL return None.

**Validates: Requirements 2.1, 2.2, 2.3, 2.4**

### Property 3: Vision mode routing correctness

*For any* message received when the session mode is "vision": if the message type is "image", the message SHALL be routed to the vision analysis pipeline (not the Bedrock Agent); if the message type is "text", a reminder message SHALL be sent to the user (not routed to vision analysis or Bedrock Agent).

**Validates: Requirements 3.1, 3.2**

### Property 4: Base64 encoding round-trip in vision payload

*For any* sequence of bytes and valid MIME type, `build_vision_payload` SHALL produce a payload where decoding the base64 `data` field yields the original bytes, and the `media_type` field matches the input MIME type.

**Validates: Requirements 5.1**

### Property 5: Vision response text extraction

*For any* valid Bedrock Claude Messages API response containing one or more text content blocks, `extract_vision_response` SHALL return the concatenation of all text block values in order.

**Validates: Requirements 5.3**

### Property 6: Error recovery resets mode

*For any* failure during the vision analysis pipeline (media download error, model invocation error, or send error), the handler SHALL send an error message to the user AND reset the session mode to None.

**Validates: Requirements 6.3, 2.2, 2.3**

### Property 7: Image metadata extraction

*For any* WhatsApp webhook message of type "image" containing an `image` object with `id` and `mime_type` fields, `extract_image_metadata` SHALL return a tuple containing the exact `id` value and the exact `mime_type` value from the payload.

**Validates: Requirements 9.1, 9.2**
