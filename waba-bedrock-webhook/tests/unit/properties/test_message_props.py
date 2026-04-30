"""
Property-based tests for message extraction and handling.

Property 3: Extracción y procesamiento individual de mensajes de texto
Property 4: Filtrado de mensajes no-texto
Property 5: Manejo graceful de payloads inválidos

Validates: Requirements 2.1, 2.2, 2.3, 2.5
"""

from unittest.mock import MagicMock, patch

from hypothesis import given, settings
from hypothesis import strategies as st

from handler import extract_text_messages, handle_message


# ---------------------------------------------------------------------------
# Strategies
# ---------------------------------------------------------------------------

# Phone numbers: digits only, 10-15 chars (E.164 without +)
phone_strategy = st.text(
    alphabet="0123456789",
    min_size=10,
    max_size=15,
)

# Message text: non-empty printable strings
text_strategy = st.text(
    alphabet=st.characters(whitelist_categories=("L", "N", "P", "S", "Z")),
    min_size=1,
    max_size=200,
)

# Message IDs: alphanumeric with dots, like "wamid.abc123"
message_id_strategy = st.from_regex(r"wamid\.[A-Za-z0-9]{5,30}", fullmatch=True)

# Non-text message types
NON_TEXT_TYPES = ["image", "audio", "video", "document", "location"]


def _build_text_message(from_number: str, body: str, msg_id: str) -> dict:
    """Build a single WhatsApp text message object."""
    return {
        "from": from_number,
        "id": msg_id,
        "timestamp": "1700000000",
        "type": "text",
        "text": {"body": body},
    }


def _build_non_text_message(msg_type: str, msg_id: str) -> dict:
    """Build a single WhatsApp non-text message object."""
    msg = {
        "from": "5491100000000",
        "id": msg_id,
        "timestamp": "1700000000",
        "type": msg_type,
    }
    # Add type-specific payload stubs
    if msg_type == "image":
        msg["image"] = {"mime_type": "image/jpeg", "id": "media_id"}
    elif msg_type == "audio":
        msg["audio"] = {"mime_type": "audio/ogg", "id": "media_id"}
    elif msg_type == "video":
        msg["video"] = {"mime_type": "video/mp4", "id": "media_id"}
    elif msg_type == "document":
        msg["document"] = {"mime_type": "application/pdf", "id": "media_id"}
    elif msg_type == "location":
        msg["location"] = {"latitude": -34.6, "longitude": -58.4}
    return msg


def _wrap_messages_in_payload(messages: list[dict]) -> dict:
    """Wrap a list of message objects in a valid WhatsApp Cloud API payload."""
    return {
        "object": "whatsapp_business_account",
        "entry": [
            {
                "id": "BUSINESS_ACCOUNT_ID",
                "changes": [
                    {
                        "value": {
                            "messaging_product": "whatsapp",
                            "metadata": {
                                "display_phone_number": "15550000000",
                                "phone_number_id": "123456789",
                            },
                            "messages": messages,
                        },
                        "field": "messages",
                    }
                ],
            }
        ],
    }


# Strategy: a single text message as a tuple (from, text, id)
text_message_tuple = st.tuples(phone_strategy, text_strategy, message_id_strategy)


# ---------------------------------------------------------------------------
# Property 3 — Extracción y procesamiento individual de mensajes de texto
# ---------------------------------------------------------------------------


class TestTextMessageExtraction:
    """
    **Validates: Requirements 2.1, 2.2**

    For any valid WhatsApp payload containing N text messages (N >= 1),
    the extraction function must return exactly N messages with correct
    ``from``, ``text``, and ``id`` fields.
    """

    @given(
        message_tuples=st.lists(
            text_message_tuple,
            min_size=1,
            max_size=10,
        ),
    )
    @settings(max_examples=100, deadline=None)
    def test_extracts_all_text_messages_with_correct_fields(
        self, message_tuples: list[tuple[str, str, str]]
    ):
        """extract_text_messages returns exactly N messages with matching fields."""
        # Build the payload
        raw_messages = [
            _build_text_message(phone, body, mid)
            for phone, body, mid in message_tuples
        ]
        payload = _wrap_messages_in_payload(raw_messages)

        result = extract_text_messages(payload)

        # Must return exactly N messages
        assert len(result) == len(message_tuples), (
            f"Expected {len(message_tuples)} messages, got {len(result)}"
        )

        # Each extracted message must have the correct fields
        for extracted, (expected_phone, expected_text, expected_id) in zip(
            result, message_tuples
        ):
            assert extracted["from"] == expected_phone, (
                f"Expected from={expected_phone!r}, got {extracted['from']!r}"
            )
            assert extracted["text"] == expected_text, (
                f"Expected text={expected_text!r}, got {extracted['text']!r}"
            )
            assert extracted["id"] == expected_id, (
                f"Expected id={expected_id!r}, got {extracted['id']!r}"
            )


# ---------------------------------------------------------------------------
# Property 4 — Filtrado de mensajes no-texto
# ---------------------------------------------------------------------------


class TestNonTextMessageFiltering:
    """
    **Validates: Requirements 2.3**

    For any WhatsApp payload containing only non-text messages (image,
    audio, video, document, location), the text extraction function must
    return an empty list and the handler must respond with HTTP 200.
    """

    @given(
        msg_types=st.lists(
            st.sampled_from(NON_TEXT_TYPES),
            min_size=1,
            max_size=10,
        ),
        msg_ids=st.lists(
            message_id_strategy,
            min_size=10,
            max_size=10,
        ),
    )
    @settings(max_examples=100, deadline=None)
    def test_non_text_messages_yield_empty_extraction(
        self, msg_types: list[str], msg_ids: list[str]
    ):
        """extract_text_messages returns [] for payloads with only non-text messages."""
        raw_messages = [
            _build_non_text_message(t, msg_ids[i])
            for i, t in enumerate(msg_types)
        ]
        payload = _wrap_messages_in_payload(raw_messages)

        result = extract_text_messages(payload)

        assert result == [], (
            f"Expected empty list for non-text messages, got {result}"
        )

    @given(
        msg_types=st.lists(
            st.sampled_from(NON_TEXT_TYPES),
            min_size=1,
            max_size=5,
        ),
        msg_ids=st.lists(
            message_id_strategy,
            min_size=5,
            max_size=5,
        ),
    )
    @settings(max_examples=100, deadline=None)
    def test_non_text_messages_handler_returns_200(
        self, msg_types: list[str], msg_ids: list[str]
    ):
        """handle_message returns HTTP 200 for payloads with only non-text messages."""
        raw_messages = [
            _build_non_text_message(t, msg_ids[i])
            for i, t in enumerate(msg_types)
        ]
        payload = _wrap_messages_in_payload(raw_messages)

        # Mock module-level dependencies so handle_message doesn't hit real services
        mock_session = MagicMock()
        mock_prompt = MagicMock()
        mock_bedrock = MagicMock()
        mock_whatsapp = MagicMock()

        with (
            patch("handler.session_manager", mock_session),
            patch("handler.prompt_reader", mock_prompt),
            patch("handler.bedrock_client", mock_bedrock),
            patch("handler.whatsapp_client", mock_whatsapp),
        ):
            result = handle_message(payload)

        assert result["statusCode"] == 200, (
            f"Expected HTTP 200, got {result['statusCode']}"
        )


# ---------------------------------------------------------------------------
# Property 5 — Manejo graceful de payloads inválidos
# ---------------------------------------------------------------------------


# Strategy: arbitrary dicts that are unlikely to match WhatsApp structure
arbitrary_dict_strategy = st.dictionaries(
    keys=st.text(min_size=0, max_size=20),
    values=st.one_of(
        st.none(),
        st.booleans(),
        st.integers(),
        st.text(max_size=50),
        st.lists(st.integers(), max_size=5),
    ),
    max_size=10,
)


class TestInvalidPayloadHandling:
    """
    **Validates: Requirements 2.5**

    For any POST payload that does not contain the expected WhatsApp
    Cloud API structure, the handler must respond with HTTP 200 without
    raising exceptions.
    """

    @given(body=arbitrary_dict_strategy)
    @settings(max_examples=100, deadline=None)
    def test_invalid_payload_returns_200_without_exception(self, body: dict):
        """handle_message returns HTTP 200 for any arbitrary dict payload."""
        # Mock module-level dependencies
        mock_session = MagicMock()
        mock_prompt = MagicMock()
        mock_bedrock = MagicMock()
        mock_whatsapp = MagicMock()

        with (
            patch("handler.session_manager", mock_session),
            patch("handler.prompt_reader", mock_prompt),
            patch("handler.bedrock_client", mock_bedrock),
            patch("handler.whatsapp_client", mock_whatsapp),
        ):
            result = handle_message(body)

        assert result["statusCode"] == 200, (
            f"Expected HTTP 200 for invalid payload, got {result['statusCode']}"
        )
