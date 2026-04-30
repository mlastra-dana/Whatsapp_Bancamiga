"""
Custom Resource Lambda to create the vector index in OpenSearch Serverless.

Uses only boto3 and urllib3 (both available in Lambda runtime) to avoid
external dependencies.
"""

import hashlib
import hmac
import json
import os
import time
import urllib.request
from datetime import datetime, timezone

import boto3
import urllib3

http = urllib3.PoolManager()


def handler(event, context):
    """CloudFormation Custom Resource handler."""
    response_url = event["ResponseURL"]
    request_type = event["RequestType"]
    physical_id = event.get("PhysicalResourceId", "oss-index-creator")

    try:
        if request_type in ("Create", "Update"):
            create_index(event)

        send_response(response_url, event, "SUCCESS", physical_id)
    except Exception as e:
        print(f"Error: {e}")
        send_response(response_url, event, "FAILED", physical_id, str(e))


def _sign(key, msg):
    return hmac.new(key, msg.encode("utf-8"), hashlib.sha256).digest()


def _get_signature_key(key, date_stamp, region, service):
    k_date = _sign(("AWS4" + key).encode("utf-8"), date_stamp)
    k_region = _sign(k_date, region)
    k_service = _sign(k_region, service)
    k_signing = _sign(k_service, "aws4_request")
    return k_signing


def create_index(event):
    """Create the vector index in OpenSearch Serverless using SigV4 signed requests."""
    props = event["ResourceProperties"]
    collection_endpoint = props["CollectionEndpoint"]
    index_name = props["IndexName"]
    region = os.environ.get("AWS_REGION", "us-east-1")

    # Wait for collection to become fully active
    print("Waiting 30s for collection to be fully active...")
    time.sleep(30)

    # Get credentials
    session = boto3.Session()
    credentials = session.get_credentials().get_frozen_credentials()

    host = collection_endpoint.replace("https://", "")

    # Check if index exists first
    exists = _signed_request(
        "HEAD", host, f"/{index_name}", region, credentials
    )
    if exists.status == 200:
        print(f"Index {index_name} already exists, skipping")
        return

    # Create the index
    index_body = {
        "settings": {
            "index": {
                "knn": True,
                "knn.algo_param.ef_search": 512,
            }
        },
        "mappings": {
            "properties": {
                "bedrock-knowledge-base-default-vector": {
                    "type": "knn_vector",
                    "dimension": 1024,
                    "method": {
                        "engine": "faiss",
                        "space_type": "l2",
                        "name": "hnsw",
                        "parameters": {},
                    },
                },
                "AMAZON_BEDROCK_TEXT_CHUNK": {"type": "text"},
                "AMAZON_BEDROCK_METADATA": {"type": "text"},
            }
        },
    }

    response = _signed_request(
        "PUT",
        host,
        f"/{index_name}",
        region,
        credentials,
        json.dumps(index_body),
    )

    body = response.data.decode("utf-8")
    print(f"Create index response: status={response.status} body={body}")

    if response.status not in (200, 201):
        raise Exception(f"Failed to create index: {response.status} {body}")

    # Wait for the index to be fully propagated and visible to Bedrock
    print("Waiting 60s for index propagation...")
    time.sleep(60)

    # Verify the index is accessible
    verify = _signed_request("HEAD", host, f"/{index_name}", region, credentials)
    print(f"Index verification: status={verify.status}")
    if verify.status != 200:
        print("Warning: index not yet visible, waiting another 30s...")
        time.sleep(30)


def _signed_request(method, host, path, region, credentials, body=None):
    """Make a SigV4-signed request to OpenSearch Serverless."""
    service = "aoss"
    now = datetime.now(timezone.utc)
    amz_date = now.strftime("%Y%m%dT%H%M%SZ")
    date_stamp = now.strftime("%Y%m%d")

    canonical_uri = path
    canonical_querystring = ""

    payload = body or ""
    payload_hash = hashlib.sha256(payload.encode("utf-8")).hexdigest()

    headers_to_sign = {
        "host": host,
        "x-amz-date": amz_date,
        "x-amz-content-sha256": payload_hash,
    }

    if credentials.token:
        headers_to_sign["x-amz-security-token"] = credentials.token

    signed_headers = ";".join(sorted(headers_to_sign.keys()))
    canonical_headers = "".join(
        f"{k}:{v}\n" for k, v in sorted(headers_to_sign.items())
    )

    canonical_request = "\n".join([
        method,
        canonical_uri,
        canonical_querystring,
        canonical_headers,
        signed_headers,
        payload_hash,
    ])

    algorithm = "AWS4-HMAC-SHA256"
    credential_scope = f"{date_stamp}/{region}/{service}/aws4_request"
    string_to_sign = "\n".join([
        algorithm,
        amz_date,
        credential_scope,
        hashlib.sha256(canonical_request.encode("utf-8")).hexdigest(),
    ])

    signing_key = _get_signature_key(
        credentials.secret_key, date_stamp, region, service
    )
    signature = hmac.new(
        signing_key, string_to_sign.encode("utf-8"), hashlib.sha256
    ).hexdigest()

    authorization = (
        f"{algorithm} Credential={credentials.access_key}/{credential_scope}, "
        f"SignedHeaders={signed_headers}, Signature={signature}"
    )

    request_headers = {
        "Host": host,
        "X-Amz-Date": amz_date,
        "X-Amz-Content-Sha256": payload_hash,
        "Authorization": authorization,
    }

    if credentials.token:
        request_headers["X-Amz-Security-Token"] = credentials.token

    if body:
        request_headers["Content-Type"] = "application/json"

    url = f"https://{host}{path}"
    return http.request(method, url, body=body, headers=request_headers)


def send_response(url, event, status, physical_id, reason=""):
    """Send response to CloudFormation."""
    body = json.dumps({
        "Status": status,
        "Reason": reason or "See CloudWatch logs",
        "PhysicalResourceId": physical_id,
        "StackId": event["StackId"],
        "RequestId": event["RequestId"],
        "LogicalResourceId": event["LogicalResourceId"],
    }).encode("utf-8")

    req = urllib.request.Request(
        url,
        data=body,
        headers={"Content-Type": "application/json"},
        method="PUT",
    )
    urllib.request.urlopen(req)
