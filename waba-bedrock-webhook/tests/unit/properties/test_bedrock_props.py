"""
Property-based tests for Bedrock Agent response handling.

Property 7: Concatenación de chunks de respuesta del agente

For any sequence of byte chunks returned by the Bedrock Agent, the extraction
function must produce a string that is the UTF-8 decoded concatenation of all
chunks in order.

Validates: Requirements 4.3
"""

from hypothesis import given, settings
from hypothesis import strategies as st

from bedrock_agent import extract_response_text


# ---------------------------------------------------------------------------
# Strategies
# ---------------------------------------------------------------------------

# Generate valid UTF-8 byte strings by encoding random text.
# We use st.text() to produce arbitrary Unicode strings, then encode them
# to bytes — this guarantees every generated chunk is valid UTF-8.
utf8_chunk_strategy = st.text(min_size=0, max_size=200).map(
    lambda t: t.encode("utf-8")
)

# A list of UTF-8 byte chunks simulating the EventStream response.
chunk_list_strategy = st.lists(utf8_chunk_strategy, min_size=0, max_size=20)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _wrap_as_event_stream(chunks: list[bytes]) -> list[dict]:
    """Wrap raw byte chunks into the EventStream format expected by
    ``extract_response_text``.

    Each chunk becomes ``{"chunk": {"bytes": raw_bytes}}``.
    """
    return [{"chunk": {"bytes": chunk}} for chunk in chunks]


# ---------------------------------------------------------------------------
# Property 7 — Response chunk concatenation
# ---------------------------------------------------------------------------


class TestResponseChunkConcatenation:
    """
    **Validates: Requirements 4.3**

    For any sequence of byte chunks returned by the Bedrock Agent, the
    extraction function must produce a string that is the UTF-8 decoded
    concatenation of all chunks in order.
    """

    @given(chunks=chunk_list_strategy)
    @settings(max_examples=100, deadline=None)
    def test_extract_response_text_concatenates_chunks_in_order(
        self, chunks: list[bytes]
    ):
        """extract_response_text must equal the ordered UTF-8 concatenation."""
        events = _wrap_as_event_stream(chunks)

        result = extract_response_text(events)

        expected = "".join(chunk.decode("utf-8") for chunk in chunks)
        assert result == expected, (
            f"Expected concatenation of {len(chunks)} chunks to be "
            f"{expected!r}, got {result!r}"
        )
