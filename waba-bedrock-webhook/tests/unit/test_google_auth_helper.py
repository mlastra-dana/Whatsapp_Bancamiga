"""
Unit tests for the Google auth helper module (lambda-calendar/google_auth_helper.py).

Tests cover:
- Successful authentication with mocked Secrets Manager and Google auth
- Credential caching behavior (second call reuses cached service)
- Secrets Manager read failure raises GoogleAuthError with error logged
- Google authentication failure raises GoogleAuthError with error logged (no credentials in logs)

Requirements: 3.1, 3.2, 3.3, 3.4, 3.5
"""

import json
import os
import sys
import logging

import pytest
from unittest.mock import patch, MagicMock

# Add the lambda-calendar directory to sys.path
sys.path.insert(
    0, os.path.join(os.path.dirname(__file__), "..", "..", "lambda-calendar")
)

import google_auth_helper
from google_auth_helper import (
    GoogleAuthError,
    _load_credentials_from_secrets_manager,
    _build_delegated_credentials,
    get_calendar_service,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

FAKE_SECRET_ARN = "arn:aws:secretsmanager:us-east-1:123456789012:secret:test-secret"
FAKE_EMAIL = "admin@example.com"

FAKE_CREDENTIALS_INFO = {
    "type": "service_account",
    "project_id": "test-project",
    "private_key_id": "key-id-123",
    "private_key": "-----BEGIN RSA PRIVATE KEY-----\nMIIEpAIBAAKCAQEA0Z3VS5JJcds3xfn/ygWyF8PbnGy0AHB7MhgHcTz6sE2I2yPB\naFDrBz9vFqU4yBBLsXF0MwAqNMOGX1pJsGDzMiJHHPOIIFGEBLOL0K/5bFMwTl9\nFMOGX1pJsGDzMiJHHPOIIFGEBLOL0K/5bFMwTl9FMOGXaFDrBz9vFqU4yBBLsXF0\nMwAqNMOGX1pJsGDzMiJHHPOIIFGEBLOL0K/5bFMwTl9FMOGXaFDrBz9vFqU4yBBL\nsXF0MwAqNMOGX1pJsGDzMiJHHPOIIFGEBLOL0K/5bFMwTl9FMOGXaFDrBz9vFqU4\nyBBLsXF0MwAqNMOGX1pJsGDzMiJHHPOIIFGEBLOL0K/5bFMwTl9FMOGXaFDrBz9v\nFqU4yBBLsXF0MwAqNMOGX1pJsGDzMiJHHPOIIFGEBLOL0K/5bFMwTl9FMOGXaFDr\nBz9vFqU4yBBLsXF0MwAqNMOGX1pJsGDzMiJHHPOIIFGEBLOL0K/5bFMwTl9FMOGX\naFDrBz9vFqU4yBBLsXF0MwAqNMOGX1pJsGDzMiJHHPOIIFGEBLOL0K/5bFMwTl9F\nMOGXaFDrBz9vFqU4yBBLsXF0MwAqNMOGX1pJsGDzMiJHHPOIIFGEBLOL0K/5bFMw\nTl9FMOGXaFDrBz9vFqU4yBBLsXF0MwAqNMOGX1pJsGDzMiJHHPOIIFGEBLOL0K/5\nbFMwTl9FMOGXaFDrBz9vFqU4yBBLsXF0MwAqNMOGX1pJsGDzMiJHHPOIIFGEBLOL\n0K/5bFMwTl9FMOGXaFDrBz9vFqU4yBBLsXF0MwAqNMOGX1pJsGDzMiJHHPOIIFGE\nBLOL0K/5bFMwTl9FMOGXaFDrBz9vFqU4yBBLsXF0MwAqNMOGX1pJsGDzMiJHHPOI\nIFGEBLOL0K/5bFMwTl9FMOGXaFDrBz9vFqU4yBBLsXF0MwAqNMOGX1pJsGDzMiJH\nHPOIIFGEBLOL0K/5bFMwTl9FMOGXaFDrBz9vFqU4yBBLsXF0MwAqNMOGX1pJsGDz\nMiJHHPOIIFGEBLOL0K/5bFMwTl9FMOGXaFDrBz9vFqU4yBBLsXF0MwAqNMOGX1pJ\nsGDzMiJHHPOIIFGEBLOL0K/5bFMwTl9FMOGXaFDrBz9vFqU4yBBLsXF0MwAqNMOG\nX1pJsGDzMiJHHPOIIFGEBLOL0K/5bFMwTl9FMOGXaFDrBz9vFqU4yBBLsXF0MwAq\nNMOGX1pJsGDzMiJHHPOIIFGEBLOL0K/5bFMwTl9FMOGXaFDrBz9vFqU4yBBLsXF0\nMwAqNMOGX1pJsGDzMiJHHPOIIFGEBLOL0K/5bFMwTl9FMOGXaFDrBz9vFqU4yBBL\nsXF0MwAqNMOGX1pJsGDzMiJHHPOIIFGEBLOL0K/5bFMwTl9FMOGXaFDrBz9vFqU4\nyBBLsXF0MwAqNMOGX1pJsGDzMiJHHPOIIFGEBLOL0K/5bFMwTl9FMOGXaFDrBz9v\nFqU4yBBLsXF0MwAqNMOGX1pJsGDzMiJHHPOIIFGEBLOL0K/5bFMwTl9FMOGXaFDr\nBz9vFqU4yBBLsXF0MwAqNMOGX1pJsGDzMiJHHPOIIFGEBLOL0K/5bFMwTl9FMOGX\naFDrBz9vFqU4yBBLsXF0MwAqNMOGX1pJsGDzMiJHHPOIIFGEBLOL0K/5bFMwTl9F\nMOGXaFDrBz9vFqU4yBBLsXF0MwAqNMOGX1pJsGDzMiJHHPOIIFGEBLOL0K/5bFMw\nTl9FMOGXaFDrBz9vFqU4yBBLsXF0MwAqNMOGX1pJsGDzMiJHHPOIIFGEBLOL0K/5\nbFMwTl9FMOGXaFDrBz9vFqU4yBBLsXF0MwAqNMOGX1pJsGDzMiJHHPOIIFGEBLOL\n-----END RSA PRIVATE KEY-----\n",
    "client_email": "test@test-project.iam.gserviceaccount.com",
    "client_id": "123456789",
    "auth_uri": "https://accounts.google.com/o/oauth2/auth",
    "token_uri": "https://oauth2.googleapis.com/token",
}


@pytest.fixture(autouse=True)
def _reset_cache():
    """Reset module-level cache before each test."""
    google_auth_helper._cached_credentials = None
    google_auth_helper._cached_service = None
    yield
    google_auth_helper._cached_credentials = None
    google_auth_helper._cached_service = None


# ---------------------------------------------------------------------------
# Tests: _load_credentials_from_secrets_manager
# ---------------------------------------------------------------------------


class TestLoadCredentialsFromSecretsManager:
    """Tests for reading credentials from Secrets Manager."""

    @patch("google_auth_helper.boto3.client")
    def test_successful_read(self, mock_boto_client):
        """Secrets Manager returns valid JSON — parsed dict is returned."""
        mock_sm = MagicMock()
        mock_sm.get_secret_value.return_value = {
            "SecretString": json.dumps(FAKE_CREDENTIALS_INFO)
        }
        mock_boto_client.return_value = mock_sm

        result = _load_credentials_from_secrets_manager(FAKE_SECRET_ARN)

        assert result == FAKE_CREDENTIALS_INFO
        mock_sm.get_secret_value.assert_called_once_with(SecretId=FAKE_SECRET_ARN)

    @patch("google_auth_helper.boto3.client")
    def test_client_error_raises_google_auth_error(self, mock_boto_client):
        """Secrets Manager ClientError raises GoogleAuthError."""
        from botocore.exceptions import ClientError

        mock_sm = MagicMock()
        mock_sm.get_secret_value.side_effect = ClientError(
            {"Error": {"Code": "ResourceNotFoundException", "Message": "not found"}},
            "GetSecretValue",
        )
        mock_boto_client.return_value = mock_sm

        with pytest.raises(GoogleAuthError, match="ResourceNotFoundException"):
            _load_credentials_from_secrets_manager(FAKE_SECRET_ARN)

    @patch("google_auth_helper.boto3.client")
    def test_invalid_json_raises_google_auth_error(self, mock_boto_client):
        """Malformed JSON in secret raises GoogleAuthError."""
        mock_sm = MagicMock()
        mock_sm.get_secret_value.return_value = {
            "SecretString": "not-valid-json{{"
        }
        mock_boto_client.return_value = mock_sm

        with pytest.raises(GoogleAuthError, match="parse"):
            _load_credentials_from_secrets_manager(FAKE_SECRET_ARN)


# ---------------------------------------------------------------------------
# Tests: _build_delegated_credentials
# ---------------------------------------------------------------------------


class TestBuildDelegatedCredentials:
    """Tests for constructing delegated Google credentials."""

    @patch("google_auth_helper.Credentials.from_service_account_info")
    def test_successful_build(self, mock_from_info):
        """Valid credentials info produces Credentials with correct subject and scopes."""
        mock_creds = MagicMock()
        mock_delegated = MagicMock()
        mock_creds.with_subject.return_value = mock_delegated
        mock_from_info.return_value = mock_creds

        result = _build_delegated_credentials(FAKE_CREDENTIALS_INFO, FAKE_EMAIL)

        assert result is mock_delegated
        mock_from_info.assert_called_once_with(
            FAKE_CREDENTIALS_INFO,
            scopes=["https://www.googleapis.com/auth/calendar"],
        )
        mock_creds.with_subject.assert_called_once_with(FAKE_EMAIL)

    def test_invalid_credentials_info_raises_google_auth_error(self):
        """Missing required fields in credentials info raises GoogleAuthError."""
        with pytest.raises(GoogleAuthError, match="Failed to build"):
            _build_delegated_credentials({}, FAKE_EMAIL)


# ---------------------------------------------------------------------------
# Tests: get_calendar_service
# ---------------------------------------------------------------------------


class TestGetCalendarService:
    """Tests for the cached Calendar service builder."""

    @patch("google_auth_helper.build")
    @patch("google_auth_helper._build_delegated_credentials")
    @patch("google_auth_helper._load_credentials_from_secrets_manager")
    def test_successful_service_creation(
        self, mock_load, mock_build_creds, mock_build_service
    ):
        """First call creates and returns a new service instance."""
        mock_load.return_value = FAKE_CREDENTIALS_INFO
        mock_creds = MagicMock()
        mock_build_creds.return_value = mock_creds
        mock_service = MagicMock()
        mock_build_service.return_value = mock_service

        result = get_calendar_service(FAKE_SECRET_ARN, FAKE_EMAIL)

        assert result is mock_service
        mock_load.assert_called_once_with(FAKE_SECRET_ARN)
        mock_build_creds.assert_called_once_with(FAKE_CREDENTIALS_INFO, FAKE_EMAIL)
        mock_build_service.assert_called_once_with(
            "calendar", "v3", credentials=mock_creds
        )

    @patch("google_auth_helper.build")
    @patch("google_auth_helper._build_delegated_credentials")
    @patch("google_auth_helper._load_credentials_from_secrets_manager")
    def test_caching_returns_same_service(
        self, mock_load, mock_build_creds, mock_build_service
    ):
        """Second call returns the cached service without rebuilding."""
        mock_load.return_value = FAKE_CREDENTIALS_INFO
        mock_creds = MagicMock()
        mock_build_creds.return_value = mock_creds
        mock_service = MagicMock()
        mock_build_service.return_value = mock_service

        first = get_calendar_service(FAKE_SECRET_ARN, FAKE_EMAIL)
        second = get_calendar_service(FAKE_SECRET_ARN, FAKE_EMAIL)

        assert first is second
        # Secrets Manager and credential building should only happen once
        mock_load.assert_called_once()
        mock_build_creds.assert_called_once()
        mock_build_service.assert_called_once()

    @patch("google_auth_helper._load_credentials_from_secrets_manager")
    def test_secrets_manager_failure_propagates(self, mock_load):
        """GoogleAuthError from Secrets Manager propagates to caller."""
        mock_load.side_effect = GoogleAuthError("SM failure")

        with pytest.raises(GoogleAuthError, match="SM failure"):
            get_calendar_service(FAKE_SECRET_ARN, FAKE_EMAIL)

    @patch("google_auth_helper.build")
    @patch("google_auth_helper._build_delegated_credentials")
    @patch("google_auth_helper._load_credentials_from_secrets_manager")
    def test_build_service_failure_raises_google_auth_error(
        self, mock_load, mock_build_creds, mock_build_service
    ):
        """Exception during discovery.build raises GoogleAuthError."""
        mock_load.return_value = FAKE_CREDENTIALS_INFO
        mock_build_creds.return_value = MagicMock()
        mock_build_service.side_effect = Exception("discovery failed")

        with pytest.raises(GoogleAuthError, match="Failed to build Google Calendar"):
            get_calendar_service(FAKE_SECRET_ARN, FAKE_EMAIL)


# ---------------------------------------------------------------------------
# Tests: Logging safety — credentials never appear in logs
# ---------------------------------------------------------------------------


class TestLoggingSafety:
    """Verify that credential values are never logged."""

    @patch("google_auth_helper.boto3.client")
    def test_secrets_manager_error_does_not_log_credentials(
        self, mock_boto_client, caplog
    ):
        """On SM failure, only the error code is logged, not the secret value."""
        from botocore.exceptions import ClientError

        mock_sm = MagicMock()
        mock_sm.get_secret_value.side_effect = ClientError(
            {"Error": {"Code": "AccessDeniedException", "Message": "denied"}},
            "GetSecretValue",
        )
        mock_boto_client.return_value = mock_sm

        with caplog.at_level(logging.ERROR):
            with pytest.raises(GoogleAuthError):
                _load_credentials_from_secrets_manager(FAKE_SECRET_ARN)

        # The error code should be logged
        assert "AccessDeniedException" in caplog.text
        # Credential values must NOT appear in logs
        assert "private_key" not in caplog.text
        assert "BEGIN RSA" not in caplog.text

    def test_google_auth_error_does_not_log_credentials(self, caplog):
        """On Google auth failure, credential values are not logged."""
        with caplog.at_level(logging.ERROR):
            with pytest.raises(GoogleAuthError):
                _build_delegated_credentials({}, FAKE_EMAIL)

        assert "private_key" not in caplog.text
        assert "BEGIN RSA" not in caplog.text
