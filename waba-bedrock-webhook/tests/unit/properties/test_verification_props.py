"""
Property-based tests for webhook verification.

Property 1: Correctitud de la verificación del webhook
Property 2: Parámetros de verificación faltantes

Validates: Requirements 1.2, 1.3, 1.4
"""

import os

from hypothesis import given, settings, assume
from hypothesis import strategies as st

from handler import handle_verification


# ---------------------------------------------------------------------------
# Strategies
# ---------------------------------------------------------------------------

# Non-empty printable strings for tokens and challenges
token_strategy = st.text(
    alphabet=st.characters(whitelist_categories=("L", "N", "P", "S")),
    min_size=1,
    max_size=100,
)

challenge_strategy = st.text(
    alphabet=st.characters(whitelist_categories=("L", "N", "P", "S")),
    min_size=1,
    max_size=200,
)

# The three required parameter keys
REQUIRED_PARAMS = ["hub.mode", "hub.verify_token", "hub.challenge"]


# ---------------------------------------------------------------------------
# Property 1 — Correctitud de la verificación del webhook
# ---------------------------------------------------------------------------


class TestVerificationCorrectness:
    """
    **Validates: Requirements 1.2, 1.3**

    For any pair of tokens and any challenge string, if tokens match
    the handler must return HTTP 200 with the challenge as body;
    if tokens don't match, the handler must return HTTP 403.
    """

    @given(
        env_token=token_strategy,
        challenge=challenge_strategy,
    )
    @settings(max_examples=100, deadline=None)
    def test_matching_tokens_return_200_with_challenge(
        self, env_token: str, challenge: str
    ):
        """When verify_token matches WHATSAPP_VERIFY_TOKEN, return 200 + challenge."""
        original = os.environ.get("WHATSAPP_VERIFY_TOKEN")
        try:
            os.environ["WHATSAPP_VERIFY_TOKEN"] = env_token

            params = {
                "hub.mode": "subscribe",
                "hub.verify_token": env_token,
                "hub.challenge": challenge,
            }

            result = handle_verification(params)

            assert result["statusCode"] == 200, (
                f"Expected 200 for matching tokens, got {result['statusCode']}"
            )
            assert result["body"] == challenge, (
                f"Expected challenge {challenge!r} as body, got {result['body']!r}"
            )
        finally:
            if original is not None:
                os.environ["WHATSAPP_VERIFY_TOKEN"] = original
            else:
                os.environ.pop("WHATSAPP_VERIFY_TOKEN", None)

    @given(
        env_token=token_strategy,
        request_token=token_strategy,
        challenge=challenge_strategy,
    )
    @settings(max_examples=100, deadline=None)
    def test_mismatched_tokens_return_403(
        self, env_token: str, request_token: str, challenge: str
    ):
        """When verify_token does not match WHATSAPP_VERIFY_TOKEN, return 403."""
        assume(env_token != request_token)

        original = os.environ.get("WHATSAPP_VERIFY_TOKEN")
        try:
            os.environ["WHATSAPP_VERIFY_TOKEN"] = env_token

            params = {
                "hub.mode": "subscribe",
                "hub.verify_token": request_token,
                "hub.challenge": challenge,
            }

            result = handle_verification(params)

            assert result["statusCode"] == 403, (
                f"Expected 403 for mismatched tokens, got {result['statusCode']}"
            )
        finally:
            if original is not None:
                os.environ["WHATSAPP_VERIFY_TOKEN"] = original
            else:
                os.environ.pop("WHATSAPP_VERIFY_TOKEN", None)


# ---------------------------------------------------------------------------
# Property 2 — Parámetros de verificación faltantes
# ---------------------------------------------------------------------------


class TestMissingVerificationParams:
    """
    **Validates: Requirements 1.4**

    For any GET request missing at least one of the required parameters
    (hub.mode, hub.verify_token, hub.challenge), the handler must return HTTP 400.
    """

    @given(
        present_keys=st.lists(
            st.sampled_from(REQUIRED_PARAMS),
            min_size=0,
            max_size=2,
            unique=True,
        ).filter(lambda keys: set(keys) != set(REQUIRED_PARAMS)),
        token_value=token_strategy,
        challenge_value=challenge_strategy,
    )
    @settings(max_examples=100, deadline=None)
    def test_missing_params_return_400(
        self,
        present_keys: list,
        token_value: str,
        challenge_value: str,
    ):
        """Any subset of required params that is not all three must yield 400."""
        original = os.environ.get("WHATSAPP_VERIFY_TOKEN")
        try:
            os.environ["WHATSAPP_VERIFY_TOKEN"] = token_value

            # Build params dict with only the present keys
            value_map = {
                "hub.mode": "subscribe",
                "hub.verify_token": token_value,
                "hub.challenge": challenge_value,
            }
            params = {k: value_map[k] for k in present_keys}

            result = handle_verification(params)

            assert result["statusCode"] == 400, (
                f"Expected 400 when params are {present_keys}, "
                f"got {result['statusCode']}"
            )
        finally:
            if original is not None:
                os.environ["WHATSAPP_VERIFY_TOKEN"] = original
            else:
                os.environ.pop("WHATSAPP_VERIFY_TOKEN", None)
