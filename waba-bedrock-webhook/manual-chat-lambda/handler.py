import json
import logging
import os
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


def guardar_mensaje(telefono, mensaje, direccion, msg_type="text"):
    if conversations_table is None:
        logger.warning("CONVERSATIONS_TABLE_NAME is not configured; message was not logged")
        return

    tipo = "entrada" if direccion in {"entrada", "inbound"} else "salida"
    conversations_table.put_item(
        Item={
            "telefono": telefono,
            "timestamp": now_iso(),
            "mensaje": str(mensaje)[:1000],
            "tipo": tipo,
            "canal": "whatsapp",
            "msg_type": msg_type,
        }
    )


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

    if not telefono or not mensaje:
        return response(400, {"error": "phone and message are required"})

    try:
        result = enviar_whatsapp(telefono, mensaje)
        guardar_mensaje(telefono, mensaje, "salida", "manual")
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
        if method == "POST" and path in {"/", "/webhook"}:
            return handle_whatsapp_webhook(event)
        return response(404, {"error": "Not found"})
    except Exception as exc:
        logger.exception("Unhandled Lambda error")
        return response(500, {"error": str(exc)})
