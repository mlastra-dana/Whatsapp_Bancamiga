"""
Vision analyzer module for multimodal image analysis via Bedrock Runtime.

Encapsulates invocation of a multimodal model with vision capabilities to
produce detailed image descriptions in Spanish. Uses Amazon Nova Lite which
supports vision natively without requiring Marketplace subscriptions.
"""

import base64
import json
import logging

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError, ReadTimeoutError

logger = logging.getLogger(__name__)

VISION_MODEL_ID = "amazon.nova-lite-v1:0"

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

    Uses the Amazon Nova Messages API format with image content.

    Args:
        image_bytes: Raw binary image data.
        mime_type: MIME type of the image (e.g. "image/jpeg").

    Returns:
        A dict matching the Amazon Nova Messages API schema with image content.
    """
    image_b64 = base64.b64encode(image_bytes).decode("utf-8")

    # Map MIME types to Nova's supported format names
    format_map = {
        "image/jpeg": "jpeg",
        "image/jpg": "jpeg",
        "image/png": "png",
        "image/gif": "gif",
        "image/webp": "webp",
    }
    image_format = format_map.get(mime_type, "jpeg")

    return {
        "messages": [
            {
                "role": "user",
                "content": [
                    {
                        "image": {
                            "format": image_format,
                            "source": {
                                "bytes": image_b64,
                            },
                        },
                    },
                    {
                        "text": VISION_PROMPT,
                    },
                ],
            }
        ],
        "inferenceConfig": {
            "maxTokens": 1024,
        },
    }


def extract_vision_response(response_body: dict) -> str:
    """
    Extract the text content from a Bedrock Nova Messages API response.

    Handles both Amazon Nova format and Claude format for flexibility.

    Args:
        response_body: Parsed JSON response from invoke_model.

    Returns:
        The concatenated text content from the response.
    """
    # Amazon Nova format
    output = response_body.get("output", {})
    message = output.get("message", {})
    content = message.get("content", [])
    if content:
        parts: list[str] = []
        for block in content:
            if "text" in block:
                parts.append(block["text"])
        if parts:
            return "".join(parts)

    # Fallback: Claude format
    for block in response_body.get("content", []):
        if block.get("type") == "text":
            return block["text"]

    return ""


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
