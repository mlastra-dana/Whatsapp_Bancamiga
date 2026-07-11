import base64
import json
import logging
import os
import re
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

import boto3
from boto3.dynamodb.conditions import Key

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

# Existing names from your Lambda are supported.
WHATSAPP_TOKEN = os.environ.get("WHATSAPP_TOKEN") or os.environ.get("WHATSAPP_ACCESS_TOKEN")
PHONE_NUMBER_ID = os.environ.get("PHONE_NUMBER_ID") or os.environ.get("WHATSAPP_PHONE_NUMBER_ID")
VERIFY_TOKEN = os.environ.get("WHATSAPP_VERIFY_TOKEN", "")
STATE_TABLE_NAME = os.environ.get("STATE_TABLE_NAME", "chat-state")
CONVERSATIONS_TABLE_NAME = os.environ.get("CONVERSATIONS_TABLE_NAME", "chat-logs")
CORS_ORIGIN = os.environ.get("CORS_ORIGIN", "*")
GRAPH_API_VERSION = os.environ.get("GRAPH_API_VERSION", "v20.0")
MAX_MEDIA_BYTES = int(os.environ.get("MAX_MEDIA_BYTES", str(10 * 1024 * 1024)))
MAX_UPLOAD_MEDIA_BYTES = int(os.environ.get("MAX_UPLOAD_MEDIA_BYTES", str(5 * 1024 * 1024)))
ABSENCE_BOT_STATE_KEY = "__absence_bot__"
TEMPLATES_STATE_KEY = "__quick_templates__"
CONTACT_KEY_PREFIX = "contact#"
READ_STATE_KEY_PREFIX = "read#"
AGENT_KEY_PREFIX = "agent#"
DEFAULT_ABSENCE_MESSAGE = (
    "Gracias por escribirnos. En este momento no hay un asesor disponible. "
    "Te responderemos apenas retomemos la atencion."
)

dynamodb = boto3.resource("dynamodb")
state_table = dynamodb.Table(STATE_TABLE_NAME)
conversations_table = dynamodb.Table(CONVERSATIONS_TABLE_NAME) if CONVERSATIONS_TABLE_NAME else None
template_definition_cache = {}


def response(status_code: int, body: Any, content_type: str = "application/json") -> dict:
    if content_type == "application/json" and not isinstance(body, str):
        payload = json.dumps(body, default=json_default)
    else:
        payload = body if isinstance(body, str) else str(body)

    return {
        "statusCode": status_code,
        "headers": {
            "Content-Type": content_type,
            "Access-Control-Allow-Origin": CORS_ORIGIN,
            "Access-Control-Allow-Methods": "GET,POST,OPTIONS",
            "Access-Control-Allow-Headers": "Content-Type,Authorization",
        },
        "body": payload,
    }


def binary_response(status_code: int, data: bytes, content_type: str) -> dict:
    return {
        "statusCode": status_code,
        "headers": {
            "Content-Type": content_type or "application/octet-stream",
            "Access-Control-Allow-Origin": CORS_ORIGIN,
            "Access-Control-Allow-Methods": "GET,POST,OPTIONS",
            "Access-Control-Allow-Headers": "Content-Type,Authorization",
            "Cache-Control": "private, max-age=300",
        },
        "isBase64Encoded": True,
        "body": base64.b64encode(data).decode("ascii"),
    }


def json_default(value: Any) -> Any:
    if isinstance(value, Decimal):
        return int(value) if value % 1 == 0 else float(value)
    return str(value)


def path_and_method(event: dict) -> tuple[str, str]:
    http = event.get("requestContext", {}).get("http", {})
    method = http.get("method") or event.get("httpMethod") or "GET"
    path = event.get("rawPath") or event.get("path") or "/"
    return path.rstrip("/") or "/", method.upper()


def parse_body(event: dict) -> dict:
    raw_body = event.get("body") or "{}"
    if isinstance(raw_body, dict):
        return raw_body
    try:
        return json.loads(raw_body)
    except json.JSONDecodeError:
        return {}


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def normalize_text(value: str) -> str:
    text = str(value or "").lower()
    return "".join(
        char for char in unicodedata.normalize("NFD", text)
        if unicodedata.category(char) != "Mn"
    )


def get_user(telefono):
    try:
        res = state_table.get_item(Key={"telefono": telefono})
        return res.get("Item", {"estado": "default", "historial": []})
    except Exception:
        logger.exception("Could not read user state")
        return {"estado": "default", "historial": []}


def save_user(telefono, estado, historial):
    state_table.put_item(
        Item={
            "telefono": telefono,
            "estado": estado,
            "historial": historial[-6:] if isinstance(historial, list) else historial,
        }
    )


def get_absence_bot_config():
    try:
        res = state_table.get_item(Key={"telefono": ABSENCE_BOT_STATE_KEY})
        item = res.get("Item") or {}
    except Exception:
        logger.exception("Could not read absence bot config")
        item = {}

    return {
        "enabled": bool(item.get("enabled", False)),
        "message": item.get("message") or DEFAULT_ABSENCE_MESSAGE,
        "updated_at": item.get("updated_at", ""),
    }


def save_absence_bot_config(enabled, message):
    clean_message = str(message or "").strip() or DEFAULT_ABSENCE_MESSAGE
    config = {
        "telefono": ABSENCE_BOT_STATE_KEY,
        "enabled": bool(enabled),
        "message": clean_message[:1000],
        "updated_at": now_iso(),
    }
    state_table.put_item(Item=config)
    return {
        "enabled": config["enabled"],
        "message": config["message"],
        "updated_at": config["updated_at"],
    }


def normalize_phone(value):
    return "".join(char for char in str(value or "") if char.isdigit())


def get_business_phone_from_webhook(value):
    metadata = value.get("metadata") if isinstance(value, dict) and isinstance(value.get("metadata"), dict) else {}
    return normalize_phone(
        os.environ.get("WHATSAPP_BUSINESS_NUMBER")
        or os.environ.get("BUSINESS_PHONE_NUMBER")
        or metadata.get("display_phone_number")
        or metadata.get("phone_number")
    )


def get_call_counterparty_phone(call, fallback_phone, business_phone, call_direction):
    from_phone = normalize_phone(call.get("from"))
    to_phone = normalize_phone(call.get("to"))
    wa_phone = normalize_phone(call.get("wa_id"))

    if business_phone:
        if from_phone and from_phone != business_phone:
            return from_phone
        if to_phone and to_phone != business_phone:
            return to_phone

    if call_direction == "BUSINESS_INITIATED":
        return to_phone or wa_phone or fallback_phone
    if call_direction == "USER_INITIATED":
        return from_phone or wa_phone or fallback_phone

    return from_phone or to_phone or wa_phone or fallback_phone


def list_contacts(event):
    contacts = []
    scan_kwargs = {}

    while True:
        result = state_table.scan(**scan_kwargs)
        for item in result.get("Items", []):
            key = str(item.get("telefono") or "")
            if not key.startswith(CONTACT_KEY_PREFIX):
                continue
            phone = key[len(CONTACT_KEY_PREFIX):]
            contacts.append({
                "phone": phone,
                "name": item.get("name") or item.get("nombre") or "",
                "updated_at": item.get("updated_at", ""),
            })

        last_key = result.get("LastEvaluatedKey")
        if not last_key:
            break
        scan_kwargs["ExclusiveStartKey"] = last_key

    contacts.sort(key=lambda item: item.get("name") or item.get("phone"))
    return response(200, contacts)


def save_contact(event):
    body = parse_body(event)
    phone = normalize_phone(body.get("phone") or body.get("telefono"))
    name = str(body.get("name") or body.get("nombre") or "").strip()

    if not phone or not name:
        return response(400, {"error": "phone and name are required"})

    contact = {
        "telefono": f"{CONTACT_KEY_PREFIX}{phone}",
        "phone": phone,
        "name": name[:160],
        "updated_at": now_iso(),
    }
    state_table.put_item(Item=contact)
    return response(200, {"success": True, "contact": {
        "phone": contact["phone"],
        "name": contact["name"],
        "updated_at": contact["updated_at"],
    }})


def normalize_quick_templates(value):
    if not isinstance(value, list):
        return []

    templates = []
    for template in value:
        if not isinstance(template, dict):
            continue
        title = str(template.get("title") or "").strip()
        body = str(template.get("body") or "").strip()
        if not title or not body:
            continue
        templates.append({
            "title": title[:120],
            "body": body[:2000],
        })
    return templates[:50]


def list_templates(event):
    try:
        res = state_table.get_item(Key={"telefono": TEMPLATES_STATE_KEY})
        item = res.get("Item") or {}
    except Exception:
        logger.exception("Could not read quick templates")
        item = {}

    return response(200, {
        "templates": normalize_quick_templates(item.get("templates")),
        "updated_at": item.get("updated_at", ""),
    })


def save_templates(event):
    body = parse_body(event)
    templates = normalize_quick_templates(body.get("templates"))
    item = {
        "telefono": TEMPLATES_STATE_KEY,
        "templates": templates,
        "updated_at": now_iso(),
    }
    state_table.put_item(Item=item)
    return response(200, {
        "success": True,
        "templates": templates,
        "updated_at": item["updated_at"],
    })


def list_read_state(event):
    read_state = {}
    scan_kwargs = {}

    while True:
        result = state_table.scan(**scan_kwargs)
        for item in result.get("Items", []):
            key = str(item.get("telefono") or "")
            if not key.startswith(READ_STATE_KEY_PREFIX):
                continue
            phone = key[len(READ_STATE_KEY_PREFIX):]
            last_read_at = str(item.get("last_read_at") or "")
            if phone and last_read_at:
                read_state[phone] = last_read_at

        last_key = result.get("LastEvaluatedKey")
        if not last_key:
            break
        scan_kwargs["ExclusiveStartKey"] = last_key

    return response(200, read_state)


def save_read_state(event):
    body = parse_body(event)
    phone = normalize_phone(body.get("phone") or body.get("telefono"))
    last_read_at = str(body.get("last_read_at") or body.get("timestamp") or "").strip()

    if not phone or not last_read_at:
        return response(400, {"error": "phone and last_read_at are required"})

    state_table.put_item(
        Item={
            "telefono": f"{READ_STATE_KEY_PREFIX}{phone}",
            "phone": phone,
            "last_read_at": last_read_at,
            "updated_at": now_iso(),
        }
    )
    return response(200, {"success": True, "phone": phone, "last_read_at": last_read_at})


def list_agents(event):
    now_ts = datetime.now(timezone.utc).timestamp()
    agents = []
    scan_kwargs = {}

    while True:
        result = state_table.scan(**scan_kwargs)
        for item in result.get("Items", []):
            key = str(item.get("telefono") or "")
            if not key.startswith(AGENT_KEY_PREFIX):
                continue
            username = key[len(AGENT_KEY_PREFIX):]
            last_seen = str(item.get("last_seen") or "")
            try:
                last_seen_ts = datetime.fromisoformat(last_seen).timestamp()
            except ValueError:
                last_seen_ts = 0
            is_online = bool(item.get("online", True)) and (now_ts - last_seen_ts) <= 90
            agents.append({
                "username": username,
                "name": item.get("name") or username,
                "last_seen": last_seen,
                "online": is_online,
            })

        last_key = result.get("LastEvaluatedKey")
        if not last_key:
            break
        scan_kwargs["ExclusiveStartKey"] = last_key

    agents.sort(key=lambda item: (not item["online"], item["name"]))
    return response(200, agents)


def save_agent_presence(event):
    body = parse_body(event)
    username = str(body.get("username") or body.get("agent_username") or "").strip().lower()
    name = str(body.get("name") or body.get("agent_name") or username).strip()
    online = bool(body.get("online", True))

    if not username:
        return response(400, {"error": "username is required"})

    item = {
        "telefono": f"{AGENT_KEY_PREFIX}{username}",
        "username": username,
        "name": name[:160],
        "last_seen": now_iso(),
        "online": online,
    }
    state_table.put_item(Item=item)
    return response(200, {"success": True, "agent": {
        "username": item["username"],
        "name": item["name"],
        "last_seen": item["last_seen"],
        "online": item["online"],
    }})


def is_allowed_media_url(url):
    try:
        parsed = urllib.parse.urlparse(url)
    except Exception:
        return False

    if parsed.scheme != "https":
        return False

    host = (parsed.hostname or "").lower()
    return (
        host == "lookaside.fbsbx.com"
        or host.endswith(".lookaside.fbsbx.com")
        or host == "lookaside.facebook.com"
        or host.endswith(".lookaside.facebook.com")
        or host == "scontent.whatsapp.net"
        or host.endswith(".scontent.whatsapp.net")
    )


def proxy_media(event):
    if not WHATSAPP_TOKEN:
        return response(500, {"error": "WHATSAPP_TOKEN/WHATSAPP_ACCESS_TOKEN is not configured"})

    params = event.get("queryStringParameters") or {}
    media_url = str(params.get("url") or "").strip()

    if not media_url:
        return response(400, {"error": "url is required"})
    if not is_allowed_media_url(media_url):
        return response(400, {"error": "media url is not allowed"})

    req = urllib.request.Request(media_url)
    req.add_header("Authorization", f"Bearer {WHATSAPP_TOKEN}")

    try:
        with urllib.request.urlopen(req, timeout=20) as res:
            content_type = res.headers.get("Content-Type") or "application/octet-stream"
            data = res.read(MAX_MEDIA_BYTES + 1)
            if len(data) > MAX_MEDIA_BYTES:
                return response(413, {"error": "media is too large"})
            return binary_response(200, data, content_type)
    except urllib.error.HTTPError as exc:
        error_body = exc.read().decode("utf-8", errors="replace")
        logger.error("Media proxy failed status=%s body=%s", exc.code, error_body[:1000])
        return response(exc.code, {"error": "media is not available"})
    except Exception:
        logger.exception("Media proxy failed")
        return response(502, {"error": "media proxy failed"})


def get_absence_bot(event):
    return response(200, get_absence_bot_config())


def update_absence_bot(event):
    body = parse_body(event)
    enabled = bool(body.get("enabled", False))
    message = body.get("message", DEFAULT_ABSENCE_MESSAGE)
    return response(200, save_absence_bot_config(enabled, message))


def guardar_mensaje(telefono, mensaje, direccion, msg_type="text", agent_username="", agent_name=""):
    if conversations_table is None:
        logger.warning("CONVERSATIONS_TABLE_NAME is not configured; message was not logged")
        return

    tipo = "entrada" if direccion in {"entrada", "inbound"} else "salida"
    item = {
        "telefono": telefono,
        "timestamp": now_iso(),
        "mensaje": str(mensaje)[:1000],
        "tipo": tipo,
        "canal": "whatsapp",
        "msg_type": msg_type,
    }
    if agent_username:
        item["agent_username"] = str(agent_username)[:80]
    if agent_name:
        item["agent_name"] = str(agent_name)[:160]
    conversations_table.put_item(Item=item)


def guardar_evento_llamada(telefono, descripcion, direccion="salida", call_id="", agent_username="", agent_name="", payload=None):
    if conversations_table is None:
        logger.warning("CONVERSATIONS_TABLE_NAME is not configured; call event was not logged")
        return

    tipo = "entrada" if direccion in {"entrada", "inbound"} else "salida"
    item = {
        "telefono": telefono,
        "timestamp": now_iso(),
        "mensaje": str(descripcion or "[Llamada]")[:1000],
        "tipo": tipo,
        "canal": "whatsapp",
        "msg_type": "call",
    }
    if call_id:
        item["call_id"] = str(call_id)[:160]
    if agent_username:
        item["agent_username"] = str(agent_username)[:80]
    if agent_name:
        item["agent_name"] = str(agent_name)[:160]
    if payload is not None:
        item["call_payload"] = payload
    conversations_table.put_item(Item=item)


def call_meta_api(payload):
    if not WHATSAPP_TOKEN or not PHONE_NUMBER_ID:
        raise RuntimeError("Missing WHATSAPP_TOKEN/PHONE_NUMBER_ID environment variables")

    url = f"https://graph.facebook.com/{GRAPH_API_VERSION}/{PHONE_NUMBER_ID}/calls"
    req = urllib.request.Request(url, method="POST")
    req.add_header("Content-Type", "application/json")
    req.add_header("Authorization", f"Bearer {WHATSAPP_TOKEN}")

    with urllib.request.urlopen(req, data=json.dumps(payload).encode("utf-8"), timeout=20) as res:
        return json.loads(res.read().decode("utf-8") or "{}")


def call_meta_get(path, params=None):
    if not WHATSAPP_TOKEN:
        raise RuntimeError("Missing WHATSAPP_TOKEN environment variable")

    query = urllib.parse.urlencode(params or {})
    url = f"https://graph.facebook.com/{GRAPH_API_VERSION}/{path.lstrip('/')}"
    if query:
        url = f"{url}?{query}"

    req = urllib.request.Request(url)
    req.add_header("Authorization", f"Bearer {WHATSAPP_TOKEN}")

    with urllib.request.urlopen(req, timeout=20) as res:
        return json.loads(res.read().decode("utf-8") or "{}")


def send_whatsapp_payload(payload):
    if not WHATSAPP_TOKEN or not PHONE_NUMBER_ID:
        raise RuntimeError("Missing WHATSAPP_TOKEN/PHONE_NUMBER_ID environment variables")

    url = f"https://graph.facebook.com/{GRAPH_API_VERSION}/{PHONE_NUMBER_ID}/messages"
    req = urllib.request.Request(url, method="POST")
    req.add_header("Content-Type", "application/json")
    req.add_header("Authorization", f"Bearer {WHATSAPP_TOKEN}")

    with urllib.request.urlopen(req, data=json.dumps(payload).encode("utf-8"), timeout=20) as res:
        return json.loads(res.read().decode("utf-8") or "{}")


def extract_call_id(value):
    if isinstance(value, dict):
        for key in ("id", "call_id", "callId"):
            if value.get(key):
                return str(value[key])
        for nested in value.values():
            found = extract_call_id(nested)
            if found:
                return found
    if isinstance(value, list):
        for item in value:
            found = extract_call_id(item)
            if found:
                return found
    return ""


def can_start_business_call(permission_response):
    candidates = []
    if isinstance(permission_response, dict):
        candidates.append(permission_response)
        data = permission_response.get("data")
        if isinstance(data, list):
            candidates.extend(item for item in data if isinstance(item, dict))

    for item in candidates:
        status = normalize_text(item.get("status") or item.get("call_permission_status") or item.get("permission_status"))
        if status in {"granted", "allowed", "active", "permanent", "temporary", "accepted", "approved", "enabled", "available"}:
            return True
        actions = item.get("actions") or item.get("available_actions") or {}
        if isinstance(actions, dict):
            for key in ("start_call", "connect", "call"):
                action = actions.get(key)
                if isinstance(action, dict) and bool(action.get("can_perform_action", False)):
                    return True
                if action is True:
                    return True
        for key in ("can_call", "can_start_call", "start_call", "is_call_allowed"):
            if item.get(key) is True:
                return True
    return False


def get_reusable_call_permission_reason(error_body):
    text = str(error_body or "")
    if "138009" in text or "Limit reached for call permission request" in text:
        return "permission_limit_reached"
    if "138017" in text or "can already call this consumer" in text:
        return "permission_already_available"

    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return ""

    error = data.get("error") if isinstance(data, dict) else {}
    if not isinstance(error, dict):
        return ""

    code = str(error.get("code") or "")
    if code == "138009":
        return "permission_limit_reached"
    if code == "138017":
        return "permission_already_available"
    return ""


def request_call_permission_message(telefono):
    payload = {
        "messaging_product": "whatsapp",
        "to": telefono,
        "type": "interactive",
        "interactive": {
            "type": "call_permission_request",
            "body": {
                "text": "Para llamarte desde este canal, necesitamos tu permiso. Puedes aceptarlo directamente en WhatsApp.",
            },
            "action": {
                "name": "call_permission_request",
            },
        },
    }
    return send_whatsapp_payload(payload)


def request_whatsapp_call_permission(event):
    body = parse_body(event)
    telefono = normalize_phone(body.get("phone") or body.get("to") or body.get("telefono"))
    agent_username = str(body.get("agent_username") or "").strip()
    agent_name = str(body.get("agent_name") or agent_username or "").strip()
    check_only = bool(body.get("check_only", False))

    if not telefono:
        return response(400, {"error": "phone is required"})

    permission_response = {}
    try:
        permission_response = call_meta_get(f"{PHONE_NUMBER_ID}/call_permissions", {"user_wa_id": telefono})
        if can_start_business_call(permission_response):
            return response(200, {
                "success": True,
                "can_call": True,
                "permission": permission_response,
            })

        if check_only:
            return response(200, {
                "success": True,
                "can_call": False,
                "permission": permission_response,
            })

        request_result = request_call_permission_message(telefono)
        guardar_evento_llamada(
            telefono,
            "[Llamada]: solicitud de permiso para llamar enviada",
            "salida",
            "",
            agent_username,
            agent_name,
            {"permission": permission_response, "request_result": request_result},
        )
        return response(200, {
            "success": True,
            "can_call": False,
            "permission_requested": True,
            "permission": permission_response,
            "result": request_result,
        })
    except urllib.error.HTTPError as exc:
        error_body = exc.read().decode("utf-8", errors="replace")
        logger.error("WhatsApp call permission failed status=%s body=%s", exc.code, error_body[:5000])
        reusable_reason = get_reusable_call_permission_reason(error_body)
        if reusable_reason:
            return response(200, {
                "success": True,
                "can_call": True,
                reusable_reason: True,
                "message": "Call permission is already reusable; trying direct call.",
                "error": error_body,
                "permission": permission_response,
            })
        return response(exc.code, {"error": error_body, "permission": permission_response})


def connect_whatsapp_call(event):
    body = parse_body(event)
    telefono = normalize_phone(body.get("phone") or body.get("to") or body.get("telefono"))
    sdp = str(body.get("sdp") or "").strip()
    agent_username = str(body.get("agent_username") or "").strip()
    agent_name = str(body.get("agent_name") or agent_username or "").strip()

    if not telefono or not sdp:
        return response(400, {"error": "phone and sdp are required"})

    payload = {
        "messaging_product": "whatsapp",
        "to": telefono,
        "action": "connect",
        "session": {
            "sdp_type": "offer",
            "sdp": sdp,
        },
    }

    try:
        result = call_meta_api(payload)
        call_id = extract_call_id(result)
        guardar_evento_llamada(
            telefono,
            "[Llamada]: llamada saliente iniciada desde el panel",
            "salida",
            call_id,
            agent_username,
            agent_name,
            {"request": payload, "response": result},
        )
        return response(200, {"success": True, "result": result, "call_id": call_id})
    except urllib.error.HTTPError as exc:
        error_body = exc.read().decode("utf-8", errors="replace")
        logger.error("WhatsApp call connect failed status=%s body=%s", exc.code, error_body[:5000])
        guardar_evento_llamada(
            telefono,
            "[Llamada]: no se pudo iniciar la llamada desde el panel",
            "salida",
            "",
            agent_username,
            agent_name,
            {"error": error_body},
        )
        return response(exc.code, {"error": error_body})


def terminate_whatsapp_call(event):
    body = parse_body(event)
    telefono = normalize_phone(body.get("phone") or body.get("to") or body.get("telefono"))
    call_id = str(body.get("call_id") or body.get("id") or "").strip()
    agent_username = str(body.get("agent_username") or "").strip()
    agent_name = str(body.get("agent_name") or agent_username or "").strip()

    if not call_id:
        return response(400, {"error": "call_id is required"})

    payload = {
        "messaging_product": "whatsapp",
        "call_id": call_id,
        "action": "terminate",
    }

    try:
        result = call_meta_api(payload)
        if telefono:
            guardar_evento_llamada(
                telefono,
                "[Llamada]: llamada finalizada desde el panel",
                "salida",
                call_id,
                agent_username,
                agent_name,
                {"request": payload, "response": result},
            )
        return response(200, {"success": True, "result": result})
    except urllib.error.HTTPError as exc:
        error_body = exc.read().decode("utf-8", errors="replace")
        logger.error("WhatsApp call terminate failed status=%s body=%s", exc.code, error_body[:5000])
        return response(exc.code, {"error": error_body})


def reject_whatsapp_call(event):
    body = parse_body(event)
    telefono = normalize_phone(body.get("phone") or body.get("to") or body.get("telefono"))
    call_id = str(body.get("call_id") or body.get("id") or "").strip()
    agent_username = str(body.get("agent_username") or "").strip()
    agent_name = str(body.get("agent_name") or agent_username or "").strip()

    if not call_id:
        return response(400, {"error": "call_id is required"})

    payload = {
        "messaging_product": "whatsapp",
        "call_id": call_id,
        "action": "reject",
    }

    try:
        result = call_meta_api(payload)
        if telefono:
            guardar_evento_llamada(
                telefono,
                "[Llamada]: llamada rechazada desde el panel",
                "salida",
                call_id,
                agent_username,
                agent_name,
                {"request": payload, "response": result},
            )
        return response(200, {"success": True, "result": result, "call_id": call_id})
    except urllib.error.HTTPError as exc:
        error_body = exc.read().decode("utf-8", errors="replace")
        logger.error("WhatsApp call reject failed status=%s body=%s", exc.code, error_body[:5000])
        return response(exc.code, {"error": error_body})


def accept_whatsapp_call(event):
    body = parse_body(event)
    telefono = normalize_phone(body.get("phone") or body.get("to") or body.get("telefono"))
    call_id = str(body.get("call_id") or body.get("id") or "").strip()
    sdp = str(body.get("sdp") or "").strip()
    action = str(body.get("action") or "accept").strip().lower()
    agent_username = str(body.get("agent_username") or "").strip()
    agent_name = str(body.get("agent_name") or agent_username or "").strip()

    if action not in {"pre_accept", "accept"}:
        return response(400, {"error": "action must be pre_accept or accept"})
    if not call_id or not sdp:
        return response(400, {"error": "call_id and sdp are required"})

    payload = {
        "messaging_product": "whatsapp",
        "call_id": call_id,
        "action": action,
        "session": {
            "sdp_type": "answer",
            "sdp": sdp,
        },
    }

    try:
        result = call_meta_api(payload)
        if telefono and action == "accept":
            guardar_evento_llamada(
                telefono,
                "[Llamada]: llamada contestada desde el panel",
                "salida",
                call_id,
                agent_username,
                agent_name,
                {"request": payload, "response": result},
            )
        return response(200, {"success": True, "result": result, "call_id": call_id})
    except urllib.error.HTTPError as exc:
        error_body = exc.read().decode("utf-8", errors="replace")
        logger.error("WhatsApp call accept failed status=%s body=%s", exc.code, error_body[:5000])
        return response(exc.code, {"error": error_body})


def mark_conversation_attended(telefono):
    if conversations_table is None:
        return

    try:
        result = conversations_table.query(
            KeyConditionExpression=Key("telefono").eq(telefono),
            ScanIndexForward=False,
            Limit=50,
        )
        latest_inbound = next(
            (
                item for item in result.get("Items", [])
                if (item.get("tipo") or item.get("direction")) in {"entrada", "inbound"}
            ),
            None,
        )
        if not latest_inbound or not latest_inbound.get("timestamp"):
            return

        phone = normalize_phone(telefono)
        state_table.put_item(
            Item={
                "telefono": f"{READ_STATE_KEY_PREFIX}{phone}",
                "phone": phone,
                "last_read_at": latest_inbound["timestamp"],
                "updated_at": now_iso(),
            }
        )
    except Exception:
        logger.exception("Could not mark conversation as attended")


def strip_gateway_status_prefix(value):
    text = str(value or "").strip()
    if len(text) > 4 and text[:3].isdigit() and text[3] == ":":
        return text[4:].strip()
    return text


def get_template_body_text(template_definition):
    components = template_definition.get("components") if isinstance(template_definition, dict) else []
    if not isinstance(components, list):
        return ""

    for component in components:
        if not isinstance(component, dict):
            continue
        if str(component.get("type") or "").upper() == "BODY":
            return str(component.get("text") or "").strip()
    return ""


def format_template_message_text(text):
    return re.sub(r"\{\{\s*\d+\s*\}\}", "NombreCliente", str(text or "")).strip()


def get_public_template_image_url(body):
    for key in ("url_imagen", "image_url", "header_image_url", "media_url"):
        value = str(body.get(key) or "").strip()
        if value:
            return value

    image = body.get("image") if isinstance(body.get("image"), dict) else {}
    link = str(image.get("link") or image.get("url") or "").strip()
    if link:
        return link

    return ""


def get_template_header_image_url(body, sent_template, template_definition):
    public_image_url = get_public_template_image_url(body)
    if public_image_url:
        return public_image_url

    sent_components = sent_template.get("components") if isinstance(sent_template, dict) else []
    if isinstance(sent_components, list):
        for component in sent_components:
            if not isinstance(component, dict):
                continue
            if str(component.get("type") or "").lower() != "header":
                continue
            parameters = component.get("parameters")
            if not isinstance(parameters, list):
                continue
            for parameter in parameters:
                if not isinstance(parameter, dict):
                    continue
                image = parameter.get("image") if isinstance(parameter.get("image"), dict) else {}
                link = str(image.get("link") or "").strip()
                if link:
                    return link

    meta_components = template_definition.get("components") if isinstance(template_definition, dict) else []
    if isinstance(meta_components, list):
        for component in meta_components:
            if not isinstance(component, dict):
                continue
            if str(component.get("type") or "").upper() != "HEADER":
                continue
            example = component.get("example") if isinstance(component.get("example"), dict) else {}
            header_handles = example.get("header_handle")
            if isinstance(header_handles, list) and header_handles:
                return str(header_handles[0] or "").strip()

    return ""


def get_sent_template_button_params(sent_template):
    sent_components = sent_template.get("components") if isinstance(sent_template, dict) else []
    buttons = {}

    if not isinstance(sent_components, list):
        return buttons

    for component in sent_components:
        if not isinstance(component, dict):
            continue
        if str(component.get("type") or "").lower() != "button":
            continue

        index = str(component.get("index") if component.get("index") is not None else len(buttons))
        parameters = component.get("parameters") if isinstance(component.get("parameters"), list) else []
        dynamic_value = ""
        for parameter in parameters:
            if not isinstance(parameter, dict):
                continue
            if str(parameter.get("type") or "").lower() == "text":
                dynamic_value = str(parameter.get("text") or "").strip()
                break

        buttons[index] = {
            "index": index,
            "type": str(component.get("sub_type") or "").upper(),
            "text": "",
            "url": "",
            "phone_number": "",
            "value": dynamic_value,
        }

    return buttons


def resolve_template_button_url(url, dynamic_value):
    clean_url = str(url or "").strip()
    clean_value = str(dynamic_value or "").strip()

    if not clean_url:
        return clean_value if clean_value.startswith(("http://", "https://")) else ""
    if clean_value:
        return re.sub(r"\{\{\s*\d+\s*\}\}", clean_value, clean_url)
    return clean_url


def get_template_button_actions(sent_template, template_definition):
    sent_buttons = get_sent_template_button_params(sent_template)
    actions = []

    meta_components = template_definition.get("components") if isinstance(template_definition, dict) else []
    if isinstance(meta_components, list):
        for component in meta_components:
            if not isinstance(component, dict):
                continue
            if str(component.get("type") or "").upper() != "BUTTONS":
                continue

            meta_buttons = component.get("buttons") if isinstance(component.get("buttons"), list) else []
            for index, button in enumerate(meta_buttons):
                if not isinstance(button, dict):
                    continue
                key = str(index)
                sent = sent_buttons.pop(key, {})
                dynamic_value = sent.get("value") or ""
                button_type = str(button.get("type") or sent.get("type") or "").upper()
                text = str(button.get("text") or "").strip()
                url = resolve_template_button_url(button.get("url"), dynamic_value)

                actions.append({
                    "index": key,
                    "type": button_type,
                    "text": text or "Abrir enlace" if button_type == "URL" else text or "Opcion",
                    "url": url,
                    "phone_number": str(button.get("phone_number") or "").strip(),
                    "value": dynamic_value,
                })

    for key, sent in sent_buttons.items():
        button_type = str(sent.get("type") or "").upper()
        value = str(sent.get("value") or "").strip()
        actions.append({
            "index": key,
            "type": button_type,
            "text": "Abrir enlace" if button_type == "URL" else "Opcion",
            "url": resolve_template_button_url("", value),
            "phone_number": "",
            "value": value,
        })

    return actions[:3]


def fetch_meta_template(template_id):
    if not WHATSAPP_TOKEN:
        logger.warning("WHATSAPP_TOKEN/WHATSAPP_ACCESS_TOKEN is not configured; cannot fetch template")
        return {}

    clean_template_id = str(template_id or "").strip()
    if not clean_template_id:
        return {}

    url = f"https://graph.facebook.com/{GRAPH_API_VERSION}/{clean_template_id}"
    req = urllib.request.Request(url)
    req.add_header("Authorization", f"Bearer {WHATSAPP_TOKEN}")

    try:
        with urllib.request.urlopen(req, timeout=15) as res:
            return json.loads(res.read().decode("utf-8") or "{}")
    except urllib.error.HTTPError as exc:
        error_body = exc.read().decode("utf-8", errors="replace")
        logger.error("Meta template fetch failed status=%s body=%s", exc.code, error_body[:5000])
    except Exception:
        logger.exception("Meta template fetch failed")
    return {}


def fetch_meta_template_cached(template_id):
    clean_template_id = str(template_id or "").strip()
    if not clean_template_id:
        return {}
    if clean_template_id not in template_definition_cache:
        template_definition_cache[clean_template_id] = fetch_meta_template(clean_template_id)
    return template_definition_cache.get(clean_template_id) or {}


def guardar_template_dana(event):
    if conversations_table is None:
        return response(500, {"error": "CONVERSATIONS_TABLE_NAME is not configured"})

    body = parse_body(event)
    telefono = str(body.get("to") or body.get("phone") or body.get("telefono") or "").strip()
    template = body.get("template") if isinstance(body.get("template"), dict) else {}
    template_name = str(template.get("name") or body.get("template_name") or "").strip()
    logger.info(
        "DANA outbound-template received phone=%s template=%s has_message=%s message_preview=%s",
        telefono,
        template_name,
        "message" in body,
        str(body.get("message") or "")[:120],
    )

    if not telefono or not template_name:
        return response(400, {"error": "to and template.name are required"})

    timestamp = str(body.get("sent_at") or now_iso())
    mensaje = strip_gateway_status_prefix(body.get("message"))
    template_id = body.get("template_id") or body.get("meta_template_id")
    meta_template = fetch_meta_template_cached(template_id) if template_id else {}

    if not mensaje and meta_template:
        mensaje = get_template_body_text(meta_template)

    mensaje = format_template_message_text(mensaje)

    image_url = get_template_header_image_url(body, template, meta_template)
    if image_url:
        mensaje = f"[Imagen]: {image_url}\n\n{mensaje}".strip()

    mensaje = str(mensaje or f"[Plantilla]: {template_name}")[:5000]
    template_buttons = get_template_button_actions(template, meta_template)

    conversations_table.put_item(
        Item={
            "telefono": telefono,
            "timestamp": timestamp,
            "mensaje": mensaje,
            "tipo": "salida",
            "canal": body.get("channel") or "whatsapp",
            "msg_type": "template",
            "provider": body.get("provider") or "dana",
            "source_flow": body.get("source_flow") or "",
            "template_name": template_name,
            "template_id": str(template_id or ""),
            "template_buttons": template_buttons,
            "template_payload": body,
        }
    )

    return response(200, {"success": True, "phone": telefono, "template_name": template_name})


def normalizar_log_para_panel(item):
    tipo = item.get("tipo") or item.get("direction") or "entrada"
    direction = "outbound" if tipo in {"salida", "outbound", "manual"} else "inbound"
    template_payload = item.get("template_payload") or {}
    sent_template = template_payload.get("template") if isinstance(template_payload, dict) else {}
    template_buttons = item.get("template_buttons") or []
    template_id = item.get("template_id") or ""
    if not template_buttons and template_id and isinstance(sent_template, dict):
        template_buttons = get_template_button_actions(
            sent_template,
            fetch_meta_template_cached(template_id),
        )
    return {
        "phone_number": item.get("telefono") or item.get("phone_number") or "",
        "timestamp": item.get("timestamp", ""),
        "direction": direction,
        "message": item.get("mensaje") or item.get("message") or "",
        "msg_type": item.get("msg_type") or tipo,
        "canal": item.get("canal", "whatsapp"),
        "agent_username": item.get("agent_username") or "",
        "agent_name": item.get("agent_name") or "",
        "call_id": item.get("call_id") or "",
        "call_payload": item.get("call_payload") or {},
        "template_name": item.get("template_name") or "",
        "template_id": template_id,
        "source_flow": item.get("source_flow") or "",
        "template_buttons": template_buttons,
        "template_payload": template_payload,
    }


def enviar_whatsapp(telefono, mensaje):
    if not WHATSAPP_TOKEN or not PHONE_NUMBER_ID:
        raise RuntimeError("Missing WHATSAPP_TOKEN/PHONE_NUMBER_ID environment variables")

    url = f"https://graph.facebook.com/v19.0/{PHONE_NUMBER_ID}/messages"
    payload = {
        "messaging_product": "whatsapp",
        "to": telefono,
        "type": "text",
        "text": {"body": mensaje},
    }

    req = urllib.request.Request(url, method="POST")
    req.add_header("Content-Type", "application/json")
    req.add_header("Authorization", f"Bearer {WHATSAPP_TOKEN}")

    with urllib.request.urlopen(req, data=json.dumps(payload).encode("utf-8"), timeout=15) as res:
        return json.loads(res.read().decode("utf-8") or "{}")


def upload_media_to_meta(data, filename, mime_type):
    if not WHATSAPP_TOKEN or not PHONE_NUMBER_ID:
        raise RuntimeError("Missing WHATSAPP_TOKEN/PHONE_NUMBER_ID environment variables")

    boundary = f"----chattlogger{datetime.now(timezone.utc).timestamp()}".replace(".", "")
    safe_filename = str(filename or "archivo").replace('"', "").replace("\r", "").replace("\n", "")[:180]
    content_type = str(mime_type or "application/octet-stream").replace("\r", "").replace("\n", "")[:120]

    parts = [
        f"--{boundary}\r\n"
        'Content-Disposition: form-data; name="messaging_product"\r\n\r\n'
        "whatsapp\r\n",
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="file"; filename="{safe_filename}"\r\n'
        f"Content-Type: {content_type}\r\n\r\n",
    ]
    body = b"".join(part.encode("utf-8") for part in parts) + data + f"\r\n--{boundary}--\r\n".encode("utf-8")

    url = f"https://graph.facebook.com/{GRAPH_API_VERSION}/{PHONE_NUMBER_ID}/media"
    req = urllib.request.Request(url, method="POST")
    req.add_header("Content-Type", f"multipart/form-data; boundary={boundary}")
    req.add_header("Authorization", f"Bearer {WHATSAPP_TOKEN}")

    with urllib.request.urlopen(req, data=body, timeout=30) as res:
        return json.loads(res.read().decode("utf-8") or "{}")


def enviar_whatsapp_media(telefono, media_type, media_id, filename="", caption=""):
    if not WHATSAPP_TOKEN or not PHONE_NUMBER_ID:
        raise RuntimeError("Missing WHATSAPP_TOKEN/PHONE_NUMBER_ID environment variables")

    clean_type = str(media_type or "").strip().lower()
    if clean_type not in {"image", "video", "audio", "document", "sticker"}:
        raise ValueError("Unsupported media_type")

    media_payload = {"id": media_id}
    if clean_type in {"image", "video", "document"} and caption:
        media_payload["caption"] = str(caption)[:1024]
    if clean_type == "document" and filename:
        media_payload["filename"] = str(filename)[:240]

    payload = {
        "messaging_product": "whatsapp",
        "to": telefono,
        "type": clean_type,
        clean_type: media_payload,
    }
    return send_whatsapp_payload(payload)


def obtener_url_media(media_id):
    url = f"https://graph.facebook.com/v19.0/{media_id}"
    req = urllib.request.Request(url)
    req.add_header("Authorization", f"Bearer {WHATSAPP_TOKEN}")
    response_body = urllib.request.urlopen(req, timeout=15).read().decode("utf-8")
    data = json.loads(response_body)
    return data.get("url")


def enviar_botones(telefono, texto, botones):
    if not WHATSAPP_TOKEN or not PHONE_NUMBER_ID:
        raise RuntimeError("Missing WHATSAPP_TOKEN/PHONE_NUMBER_ID environment variables")

    url = f"https://graph.facebook.com/v19.0/{PHONE_NUMBER_ID}/messages"
    payload = {
        "messaging_product": "whatsapp",
        "to": telefono,
        "type": "interactive",
        "interactive": {
            "type": "button",
            "body": {"text": texto},
            "action": {
                "buttons": [
                    {"type": "reply", "reply": {"id": str(i + 1), "title": b}}
                    for i, b in enumerate(botones)
                ]
            },
        },
    }

    req = urllib.request.Request(url, method="POST")
    req.add_header("Content-Type", "application/json")
    req.add_header("Authorization", f"Bearer {WHATSAPP_TOKEN}")

    with urllib.request.urlopen(req, data=json.dumps(payload).encode("utf-8"), timeout=15) as res:
        return json.loads(res.read().decode("utf-8") or "{}")


def extraer_mensaje(msg):
    msg_type = msg.get("type")

    if msg_type == "text":
        return msg.get("text", {}).get("body", ""), "text"

    if msg_type == "interactive":
        interactive = msg.get("interactive", {})
        interactive_type = str(interactive.get("type") or "").strip()
        if interactive_type == "button_reply":
            return interactive.get("button_reply", {}).get("title", ""), "interactive"
        if interactive_type == "list_reply":
            return interactive.get("list_reply", {}).get("title", ""), "interactive"
        if "call_permission" in interactive_type:
            permission_payload = interactive.get(interactive_type)
            status = ""
            if isinstance(permission_payload, dict):
                status = str(
                    permission_payload.get("response")
                    or permission_payload.get("status")
                    or permission_payload.get("action")
                    or ""
                ).strip()
            detail = f" {status}" if status else ""
            return f"[Llamada]: permiso de llamada actualizado{detail}", "call"
        return "[Respuesta interactiva]", "interactive"

    if msg_type == "button":
        return msg.get("button", {}).get("text", ""), "button"

    if msg_type == "image":
        image_id = msg.get("image", {}).get("id")
        caption = str(msg.get("image", {}).get("caption") or "").strip()
        image_url = obtener_url_media(image_id) if image_id else ""
        message = f"[Imagen]: {image_url}"
        if caption:
            message = f"{message}\n{caption}"
        return message, "image"

    if msg_type == "audio":
        audio_id = msg.get("audio", {}).get("id")
        audio_url = obtener_url_media(audio_id) if audio_id else ""
        return f"[Audio]: {audio_url}", "audio"

    if msg_type == "video":
        video_id = msg.get("video", {}).get("id")
        caption = str(msg.get("video", {}).get("caption") or "").strip()
        video_url = obtener_url_media(video_id) if video_id else ""
        message = f"[Video]: {video_url}"
        if caption:
            message = f"{message}\n{caption}"
        return message, "video"

    if msg_type == "sticker":
        sticker_id = msg.get("sticker", {}).get("id")
        sticker_url = obtener_url_media(sticker_id) if sticker_id else ""
        return f"[Sticker]: {sticker_url}", "sticker"

    if msg_type == "document":
        document = msg.get("document", {})
        document_id = document.get("id")
        document_url = obtener_url_media(document_id) if document_id else ""
        filename = str(document.get("filename") or "documento").replace("|", "-").strip()
        mime_type = str(document.get("mime_type") or "application/octet-stream").replace("|", "-").strip()
        caption = str(document.get("caption") or "").strip()
        message = f"[Documento]: {filename} | {mime_type} | {document_url}"
        if caption:
            message = f"{message}\n{caption}"
        return message, "document"

    if msg_type == "location":
        location = msg.get("location", {})
        lat = location.get("latitude", "")
        lng = location.get("longitude", "")
        name = location.get("name") or location.get("address") or ""
        return f"[Ubicacion]: {lat}, {lng} {name}".strip(), "location"

    if msg_type == "contacts":
        contacts = msg.get("contacts") if isinstance(msg.get("contacts"), list) else []
        lines = []
        for contact in contacts[:5]:
            if not isinstance(contact, dict):
                continue
            name_data = contact.get("name") if isinstance(contact.get("name"), dict) else {}
            display_name = str(
                name_data.get("formatted_name")
                or " ".join(
                    part for part in [
                        str(name_data.get("first_name") or "").strip(),
                        str(name_data.get("last_name") or "").strip(),
                    ]
                    if part
                )
                or "Contacto"
            ).strip()
            phones = contact.get("phones") if isinstance(contact.get("phones"), list) else []
            phone_values = []
            for phone in phones[:3]:
                if not isinstance(phone, dict):
                    continue
                phone_value = str(phone.get("wa_id") or phone.get("phone") or "").strip()
                if phone_value:
                    phone_values.append(phone_value)
            line = display_name
            if phone_values:
                line = f"{line} | {', '.join(phone_values)}"
            lines.append(line)
        if lines:
            return "[Contacto]: " + "\n".join(lines), "contacts"
        return "[Contacto]: Contacto recibido", "contacts"

    return "[Mensaje no soportado]", msg_type or "unknown"


def handle_verification(event):
    params = event.get("queryStringParameters") or {}
    challenge = params.get("hub.challenge")
    token = params.get("hub.verify_token")

    if not challenge:
        return response(200, {"ok": True, "service": "bancamiga-manual-router"})

    if VERIFY_TOKEN and token != VERIFY_TOKEN:
        return response(403, "Forbidden", "text/plain")

    return response(200, challenge, "text/plain")


def list_conversations(event):
    if conversations_table is None:
        return response(500, {"error": "CONVERSATIONS_TABLE_NAME is not configured"})

    params = event.get("queryStringParameters") or {}
    phone = params.get("phone")

    if phone:
        result = conversations_table.query(
            KeyConditionExpression=Key("telefono").eq(phone),
            ScanIndexForward=False,
        )
        items = result.get("Items", [])
    else:
        items = []
        scan_kwargs = {}
        while True:
            result = conversations_table.scan(**scan_kwargs)
            items.extend(result.get("Items", []))
            last_key = result.get("LastEvaluatedKey")
            if not last_key:
                break
            scan_kwargs["ExclusiveStartKey"] = last_key

    normalized_items = [normalizar_log_para_panel(item) for item in items]
    normalized_items.sort(key=lambda item: item.get("timestamp", ""), reverse=True)
    return response(200, normalized_items)


def send_message_from_panel(event):
    body = parse_body(event)
    telefono = str(body.get("phone", "")).strip()
    mensaje = str(body.get("message", "")).strip()
    agent_username = str(body.get("agent_username") or "").strip()
    agent_name = str(body.get("agent_name") or agent_username or "").strip()

    if not telefono or not mensaje:
        return response(400, {"error": "phone and message are required"})

    try:
        result = enviar_whatsapp(telefono, mensaje)
        guardar_mensaje(telefono, mensaje, "salida", "manual", agent_username, agent_name)
        mark_conversation_attended(telefono)
        return response(200, {"success": True, "result": result})
    except urllib.error.HTTPError as exc:
        error_body = exc.read().decode("utf-8", errors="replace")
        logger.error("WhatsApp send failed status=%s body=%s", exc.code, error_body)
        return response(exc.code, {"error": error_body})


def normalize_outbound_media_type(value, mime_type):
    requested = str(value or "").strip().lower()
    if requested in {"image", "video", "audio", "document", "sticker"}:
        return requested

    mime = str(mime_type or "").lower()
    if mime == "image/webp":
        return "sticker"
    if mime.startswith("image/"):
        return "image"
    if mime.startswith("video/"):
        return "video"
    if mime.startswith("audio/"):
        return "audio"
    return "document"


def is_supported_whatsapp_audio(mime_type):
    clean_mime = str(mime_type or "").split(";", 1)[0].strip().lower()
    return clean_mime in {
        "audio/aac",
        "audio/mp4",
        "audio/mpeg",
        "audio/amr",
        "audio/ogg",
    }


def format_outbound_media_log(media_type, media_url, filename, mime_type, caption):
    clean_caption = str(caption or "").strip()
    if media_type == "image":
        message = f"[Imagen]: {media_url}"
        return f"{message}\n{clean_caption}".strip() if clean_caption else message
    if media_type == "video":
        message = f"[Video]: {media_url}"
        return f"{message}\n{clean_caption}".strip() if clean_caption else message
    if media_type == "audio":
        return f"[Audio]: {media_url}"
    if media_type == "sticker":
        return f"[Sticker]: {media_url}"
    safe_filename = str(filename or "documento").replace("|", "-").strip()
    safe_mime = str(mime_type or "application/octet-stream").replace("|", "-").strip()
    message = f"[Documento]: {safe_filename} | {safe_mime} | {media_url}"
    return f"{message}\n{clean_caption}".strip() if clean_caption else message


def send_media_from_panel(event):
    body = parse_body(event)
    telefono = normalize_phone(body.get("phone") or body.get("to") or body.get("telefono"))
    filename = str(body.get("filename") or "archivo").strip()
    mime_type = str(body.get("mime_type") or body.get("content_type") or "application/octet-stream").strip()
    caption = str(body.get("caption") or "").strip()
    media_type = normalize_outbound_media_type(body.get("media_type"), mime_type)
    data_base64 = str(body.get("data_base64") or body.get("base64") or "").strip()
    agent_username = str(body.get("agent_username") or "").strip()
    agent_name = str(body.get("agent_name") or agent_username or "").strip()

    if not telefono or not data_base64:
        return response(400, {"error": "phone and data_base64 are required"})

    if media_type == "audio" and not is_supported_whatsapp_audio(mime_type):
        return response(415, {
            "error": "audio format is not supported by WhatsApp",
            "mime_type": mime_type,
        })

    if "," in data_base64 and data_base64.lower().startswith("data:"):
        data_base64 = data_base64.split(",", 1)[1]

    try:
        data = base64.b64decode(data_base64, validate=True)
    except Exception:
        return response(400, {"error": "data_base64 is invalid"})

    if len(data) > MAX_UPLOAD_MEDIA_BYTES:
        return response(413, {"error": "media is too large"})

    try:
        upload_result = upload_media_to_meta(data, filename, mime_type)
        media_id = str(upload_result.get("id") or "").strip()
        if not media_id:
            return response(502, {"error": "media upload did not return an id", "result": upload_result})

        send_result = enviar_whatsapp_media(telefono, media_type, media_id, filename, caption)
        media_url = obtener_url_media(media_id) or f"meta-media:{media_id}"
        log_message = format_outbound_media_log(media_type, media_url, filename, mime_type, caption)
        guardar_mensaje(telefono, log_message, "salida", media_type, agent_username, agent_name)
        mark_conversation_attended(telefono)
        return response(200, {
            "success": True,
            "media_type": media_type,
            "media_id": media_id,
            "media_url": media_url,
            "upload": upload_result,
            "result": send_result,
        })
    except urllib.error.HTTPError as exc:
        error_body = exc.read().decode("utf-8", errors="replace")
        logger.error("WhatsApp media send failed status=%s body=%s", exc.code, error_body[:5000])
        return response(exc.code, {"error": error_body})


def describe_call_event(call):
    status = call.get("status") or call.get("event") or call.get("action") or "evento"
    direction = call.get("direction") or ""
    duration = call.get("duration") or call.get("duration_seconds") or ""
    parts = [f"[Llamada]: {status}"]
    if direction:
        parts.append(str(direction))
    if duration:
        parts.append(f"{duration}s")
    return " · ".join(parts)


def handle_call_webhook(value):
    calls = value.get("calls") if isinstance(value, dict) else None
    if not isinstance(calls, list) or not calls:
        return False

    contacts = value.get("contacts") if isinstance(value.get("contacts"), list) else []
    fallback_phone = ""
    if contacts:
        fallback_phone = normalize_phone(contacts[0].get("wa_id") or contacts[0].get("input"))
    business_phone = get_business_phone_from_webhook(value)

    for call in calls:
        if not isinstance(call, dict):
            continue
        call_direction = str(call.get("direction") or "").upper()
        telefono = get_call_counterparty_phone(call, fallback_phone, business_phone, call_direction)
        if call_direction == "BUSINESS_INITIATED":
            direction = "salida"
        elif call_direction == "USER_INITIATED":
            direction = "entrada"
        else:
            direction = "entrada" if call.get("from") else "salida"
        if not telefono:
            continue
        guardar_evento_llamada(
            telefono,
            describe_call_event(call),
            direction,
            str(call.get("id") or call.get("call_id") or ""),
            payload=call,
        )
    return True


def handle_whatsapp_webhook(event):
    try:
        body = parse_body(event)
        value = body["entry"][0]["changes"][0]["value"]
        if handle_call_webhook(value):
            return response(200, {"success": True, "mode": "calls"})

        messages = value.get("messages")
        if not messages:
            return response(200, {"success": True})

        msg = messages[0]
        telefono = msg["from"]
        mensaje, msg_type = extraer_mensaje(msg)
        guardar_mensaje(telefono, mensaje, "entrada", msg_type)
        if msg_type == "call":
            return response(200, {"success": True, "mode": "call_message"})

    except Exception:
        logger.exception("Could not parse WhatsApp webhook")
        return response(200, {"success": True})

    absence_config = get_absence_bot_config()
    if absence_config["enabled"]:
        try:
            enviar_whatsapp(telefono, absence_config["message"])
            guardar_mensaje(telefono, absence_config["message"], "salida", "absence")
        except Exception:
            logger.exception("Absence bot reply failed")
        return response(200, {"success": True, "mode": "absence_bot"})

    mensaje_lower = normalize_text(mensaje)

    # Bancamiga is manual-only: log incoming messages and do not auto-reply.
    if "bancamiga" in mensaje_lower:
        save_user(telefono, "bancamiga", [])
        return response(200, {"success": True, "mode": "bancamiga"})

    # Logistica keeps the existing bot flow.
    if "logistica" in mensaje_lower:
        save_user(telefono, "logistica", {"step": "menu"})
        enviar_botones(
            telefono,
            "🚚 *Asistente de Logística*\n\nHola 👋\n¿Qué deseas hacer?",
            ["📦 Iniciar despacho", "🔄 Actualizar etapa"],
        )
        return response(200, {"success": True, "mode": "logistica"})

    user = get_user(telefono)
    estado = user.get("estado", "default")
    historial = user.get("historial", [])

    if estado == "bancamiga":
        if mensaje_lower.strip() == "salir":
            save_user(telefono, "default", [])
        return response(200, {"success": True, "mode": "bancamiga"})

    if estado == "ia":
        # Legacy safety: old users may still have estado=ia in DynamoDB.
        # Bancamiga must be manual-only, so we reset the state and do not auto-reply.
        save_user(telefono, "bancamiga", [])
        return response(200, {"success": True, "mode": "bancamiga"})

    if estado == "logistica":
        step = historial.get("step", "menu") if isinstance(historial, dict) else "menu"

        if mensaje_lower.strip() == "salir":
            historial = {"step": "menu"}
            save_user(telefono, "logistica", historial)
            enviar_botones(
                telefono,
                "🔄 Proceso reiniciado.\n\n🚚 Volviendo al inicio...\n\n¿Qué deseas hacer?",
                ["📦 Iniciar despacho", "🔄 Actualizar etapa"],
            )
            return response(200, {"success": True})

        if step == "menu":
            if "iniciar despacho" in mensaje_lower:
                historial["step"] = "documento"
                save_user(telefono, "logistica", historial)
                enviar_whatsapp(
                    telefono,
                    "📄 *Registrar despacho*\n\nAdjunta la factura o guía para continuar.",
                )

            elif "actualizar etapa" in mensaje_lower:
                historial["step"] = "guia"
                save_user(telefono, "logistica", historial)
                enviar_whatsapp(
                    telefono,
                    "🔄 *Actualización de despacho*\n\nIngresa el número de guía.",
                )

            return response(200, {"success": True})

        if step == "documento":
            historial["step"] = "location"
            save_user(telefono, "logistica", historial)
            enviar_whatsapp(
                telefono,
                "📍 *Registrar ubicación*\n\nComparte tu ubicación actual para continuar.",
            )
            return response(200, {"success": True})

        if step == "guia":
            historial["step"] = "evidencia"
            save_user(telefono, "logistica", historial)
            enviar_whatsapp(
                telefono,
                "📸 *Evidencia de despacho*\n\nAdjunta una imagen del estado del envío.",
            )
            return response(200, {"success": True})

        if step == "evidencia":
            historial["step"] = "location"
            save_user(telefono, "logistica", historial)
            enviar_whatsapp(
                telefono,
                "✅ Evidencia recibida.\n\n📍 Comparte tu ubicación para continuar.",
            )
            return response(200, {"success": True})

        if step == "location":
            historial["step"] = "fin"
            save_user(telefono, "logistica", historial)
            enviar_whatsapp(
                telefono,
                "✅ *Registro completado con éxito*\n\n🚚📍 La información del despacho ha sido actualizada.",
            )
            enviar_botones(
                telefono,
                "🙌 Gracias por tu gestión\n\n🔄 ¿Deseas realizar otra operación?",
                ["SI", "NO"],
            )
            return response(200, {"success": True})

        if step == "fin":
            if "si" in mensaje_lower:
                historial["step"] = "menu"
                save_user(telefono, "logistica", historial)
                enviar_botones(
                    telefono,
                    "🚚 *Asistente de Logística*\n\nHola 👋\n¿Qué deseas hacer?",
                    ["📦 Iniciar despacho", "🔄 Actualizar etapa"],
                )

            elif "no" in mensaje_lower:
                historial["step"] = "menu"
                save_user(telefono, "logistica", historial)
                enviar_whatsapp(
                    telefono,
                    "👋 Perfecto, gracias por usar el asistente de logística.\n\n🚚 ¡Que tengas un excelente día!",
                )

            return response(200, {"success": True})

    enviar_whatsapp(telefono, "Hola. Escribe BANCAMIGA o LOGISTICA para iniciar una conversacion.")
    return response(200, {"success": True})


def lambda_handler(event, context):
    path, method = path_and_method(event)
    logger.info("Request method=%s path=%s", method, path)

    if method == "OPTIONS":
        return response(204, "")

    try:
        if method == "GET" and path in {"/", "/webhook"}:
            return handle_verification(event)
        if method == "GET" and path == "/conversations":
            return list_conversations(event)
        if method == "GET" and path == "/media":
            return proxy_media(event)
        if method == "POST" and path == "/send-message":
            return send_message_from_panel(event)
        if method == "POST" and path == "/send-media":
            return send_media_from_panel(event)
        if method == "POST" and path == "/calls/connect":
            return connect_whatsapp_call(event)
        if method == "POST" and path == "/calls/request-permission":
            return request_whatsapp_call_permission(event)
        if method == "POST" and path == "/calls/accept":
            return accept_whatsapp_call(event)
        if method == "POST" and path == "/calls/reject":
            return reject_whatsapp_call(event)
        if method == "POST" and path == "/calls/terminate":
            return terminate_whatsapp_call(event)
        if method == "GET" and path == "/contacts":
            return list_contacts(event)
        if method == "POST" and path == "/contacts":
            return save_contact(event)
        if method == "GET" and path == "/templates":
            return list_templates(event)
        if method == "POST" and path == "/templates":
            return save_templates(event)
        if method == "GET" and path == "/read-state":
            return list_read_state(event)
        if method == "POST" and path == "/read-state":
            return save_read_state(event)
        if method == "GET" and path == "/agents":
            return list_agents(event)
        if method == "POST" and path == "/agent-presence":
            return save_agent_presence(event)
        if method == "POST" and path == "/dana/outbound-template":
            return guardar_template_dana(event)
        if method == "GET" and path == "/absence-bot":
            return get_absence_bot(event)
        if method == "POST" and path == "/absence-bot":
            return update_absence_bot(event)
        if method == "POST" and path in {"/", "/webhook"}:
            return handle_whatsapp_webhook(event)
        return response(404, {"error": "Not found"})
    except Exception as exc:
        logger.exception("Unhandled Lambda error")
        return response(500, {"error": str(exc)})
