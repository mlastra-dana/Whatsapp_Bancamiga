import json
import logging
import os
import re
import unicodedata
import urllib.error
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


def get_template_header_image_url(sent_template, template_definition):
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

    if not mensaje and template_id:
        meta_template = fetch_meta_template(template_id)
        mensaje = get_template_body_text(meta_template)
    else:
        meta_template = {}

    mensaje = format_template_message_text(mensaje)

    image_url = get_template_header_image_url(template, meta_template)
    if image_url:
        mensaje = f"[Imagen]: {image_url}\n\n{mensaje}".strip()

    mensaje = str(mensaje or f"[Plantilla]: {template_name}")[:5000]

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
            "template_payload": body,
        }
    )

    return response(200, {"success": True, "phone": telefono, "template_name": template_name})


def normalizar_log_para_panel(item):
    tipo = item.get("tipo") or item.get("direction") or "entrada"
    direction = "outbound" if tipo in {"salida", "outbound", "manual"} else "inbound"
    return {
        "phone_number": item.get("telefono") or item.get("phone_number") or "",
        "timestamp": item.get("timestamp", ""),
        "direction": direction,
        "message": item.get("mensaje") or item.get("message") or "",
        "msg_type": item.get("msg_type") or tipo,
        "canal": item.get("canal", "whatsapp"),
        "agent_username": item.get("agent_username") or "",
        "agent_name": item.get("agent_name") or "",
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


def obtener_url_imagen(image_id):
    url = f"https://graph.facebook.com/v19.0/{image_id}"
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
        if interactive.get("type") == "button_reply":
            return interactive.get("button_reply", {}).get("title", ""), "interactive"
        if interactive.get("type") == "list_reply":
            return interactive.get("list_reply", {}).get("title", ""), "interactive"

    if msg_type == "button":
        return msg.get("button", {}).get("text", ""), "button"

    if msg_type == "image":
        image_id = msg.get("image", {}).get("id")
        caption = msg.get("image", {}).get("caption")
        image_url = obtener_url_imagen(image_id) if image_id else ""
        return caption or f"[Imagen]: {image_url}", "image"

    if msg_type == "location":
        location = msg.get("location", {})
        lat = location.get("latitude", "")
        lng = location.get("longitude", "")
        name = location.get("name") or location.get("address") or ""
        return f"[Ubicacion]: {lat}, {lng} {name}".strip(), "location"

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
        return response(200, {"success": True, "result": result})
    except urllib.error.HTTPError as exc:
        error_body = exc.read().decode("utf-8", errors="replace")
        logger.error("WhatsApp send failed status=%s body=%s", exc.code, error_body)
        return response(exc.code, {"error": error_body})


def handle_whatsapp_webhook(event):
    try:
        body = parse_body(event)
        value = body["entry"][0]["changes"][0]["value"]
        messages = value.get("messages")
        if not messages:
            return response(200, {"success": True})

        msg = messages[0]
        telefono = msg["from"]
        mensaje, msg_type = extraer_mensaje(msg)
        guardar_mensaje(telefono, mensaje, "entrada", msg_type)

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
        if method == "POST" and path == "/send-message":
            return send_message_from_panel(event)
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
