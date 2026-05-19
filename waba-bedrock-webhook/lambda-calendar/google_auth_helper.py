"""
Google Auth Helper Module — Authenticates with Google Calendar API.

Handles authentication using a Google Cloud service account with domain-wide
delegation. Reads credentials from AWS Secrets Manager, builds delegated
credentials that impersonate a domain user, and constructs an authenticated
Google Calendar API v3 service instance. Credentials are cached in memory
for Lambda warm-start reuse.
"""

import json
import logging

import boto3
from botocore.exceptions import ClientError
from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build, Resource

logger = logging.getLogger(__name__)

# Scopes required for Google Calendar API access
_CALENDAR_SCOPES = ["https://www.googleapis.com/auth/calendar"]

# Module-level cache for Lambda warm-start reuse
_cached_credentials: Credentials | None = None
_cached_service: Resource | None = None


class GoogleAuthError(Exception):
    """Raised when authentication with Google Calendar API fails.

    Covers both Secrets Manager read failures and Google auth failures.
    """


def _load_credentials_from_secrets_manager(secret_arn: str) -> dict:
    """Read and parse the JSON credentials from AWS Secrets Manager.

    Args:
        secret_arn: ARN of the Secrets Manager secret containing the
            Google service account JSON credentials.

    Returns:
        Parsed dict of the service account credentials JSON.

    Raises:
        GoogleAuthError: If the secret cannot be read or parsed.
    """
    try:
        client = boto3.client("secretsmanager")
        response = client.get_secret_value(SecretId=secret_arn)
        secret_string = response["SecretString"]
        return json.loads(secret_string)
    except ClientError as exc:
        error_code = exc.response["Error"]["Code"]
        logger.error(
            "Failed to read credentials from Secrets Manager: %s", error_code
        )
        raise GoogleAuthError(
            f"Failed to read credentials from Secrets Manager: {error_code}"
        ) from exc
    except (json.JSONDecodeError, KeyError) as exc:
        logger.error("Failed to parse credentials from Secrets Manager")
        raise GoogleAuthError(
            "Failed to parse credentials from Secrets Manager"
        ) from exc


def _build_delegated_credentials(
    credentials_info: dict, impersonate_email: str
) -> Credentials:
    """Construct Google service account credentials with domain-wide delegation.

    Args:
        credentials_info: Parsed service account JSON credentials dict.
        impersonate_email: Email of the domain user to impersonate.

    Returns:
        Google OAuth2 Credentials configured with calendar scopes and
        domain-wide delegation for the specified user.

    Raises:
        GoogleAuthError: If credential construction fails.
    """
    try:
        credentials = Credentials.from_service_account_info(
            credentials_info, scopes=_CALENDAR_SCOPES
        )
        delegated_credentials = credentials.with_subject(impersonate_email)
        return delegated_credentials
    except (ValueError, KeyError) as exc:
        logger.error("Failed to build Google credentials: %s", type(exc).__name__)
        raise GoogleAuthError(
            "Failed to build Google credentials"
        ) from exc


def get_calendar_service(secret_arn: str, impersonate_email: str) -> Resource:
    """Return a cached or newly built Google Calendar API v3 service instance.

    Credentials and the service object are cached at module level so that
    subsequent invocations on the same Lambda container (warm starts) skip
    the Secrets Manager call and credential construction.

    Args:
        secret_arn: ARN of the Secrets Manager secret with service account JSON.
        impersonate_email: Email of the domain user to impersonate.

    Returns:
        Authenticated ``googleapiclient.discovery.Resource`` for Calendar API v3.

    Raises:
        GoogleAuthError: If credential loading or service construction fails.
    """
    global _cached_credentials, _cached_service

    if _cached_service is not None:
        return _cached_service

    credentials_info = _load_credentials_from_secrets_manager(secret_arn)
    _cached_credentials = _build_delegated_credentials(
        credentials_info, impersonate_email
    )

    try:
        _cached_service = build("calendar", "v3", credentials=_cached_credentials)
    except Exception as exc:
        logger.error(
            "Failed to build Google Calendar service: %s", type(exc).__name__
        )
        raise GoogleAuthError(
            "Failed to build Google Calendar service"
        ) from exc

    return _cached_service
