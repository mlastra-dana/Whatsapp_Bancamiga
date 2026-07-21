# Componentes Heredados Y Capacidades Futuras

Este documento describe codigo que existe en el repositorio, pero que **no forma parte del flujo activo de WABA Center**.

El producto activo hoy es:

```text
Frontend: panel/waba-center.html + panel/conversaciones.html
Backend: manual-chat-lambda/handler.py
Lambda AWS: VZla-Chatt_logger
```

Los componentes descritos aqui se conservan porque podrian servir como base para evolucionar WABA Center hacia un asistente con Bedrock, calendario u otras capacidades. No esta contemplado activarlos en el corto plazo.

## Regla De Lectura

Para evitar confusiones:

- **Activo hoy:** `manual-chat-lambda/handler.py` desplegado manualmente como `VZla-Chatt_logger`.
- **Frontend activo:** `panel/waba-center.html` y `panel/conversaciones.html`.
- **Conservado para futuro:** carpetas `lambda/`, `lambda-calendar/`, `lambda-appointments/`, `lambda-conversations/`, `lambda-send-message/` e `infra/`.
- **No asumir despliegue:** que exista codigo en estas carpetas no significa que este corriendo en AWS ni que el panel lo use.
- **Reactivacion futura:** cualquier reactivacion debe documentarse y hacerse manualmente o mediante el procedimiento que defina el equipo en ese momento.

## Resumen

| Ruta | Estado | Proposito original | Posible uso futuro |
|---|---|---|---|
| `lambda/` | Heredado, no activo | Webhook WhatsApp con Bedrock Agent, sesiones, prompts y vision | Asistente IA para WABA Center |
| `lambda-calendar/` | Heredado, no activo | Action Group de Bedrock para disponibilidad y creacion de eventos en Google Calendar | Agenda/citas desde asistente |
| `lambda-appointments/` | Heredado, no activo | API simple para listar citas guardadas en DynamoDB | Referencia si se reactiva calendario |
| `lambda-conversations/` | Heredado, reemplazado | API simple para leer conversaciones | Referencia historica; ya cubierto por `manual-chat-lambda` |
| `lambda-send-message/` | Heredado, reemplazado | API simple para enviar texto WhatsApp | Referencia historica; ya cubierto por `manual-chat-lambda` |
| `infra/` | Heredado, no despliegue actual | CDK para Bedrock, OpenSearch, S3, API Gateway y Lambdas | Referencia tecnica, no ruta actual de despliegue |

## `lambda/` - Webhook Con Bedrock Agent

Ruta:

```text
lambda/
```

Archivos principales:

- `handler.py`
- `bedrock_agent.py`
- `session_manager.py`
- `prompt_reader.py`
- `vision_analyzer.py`
- `whatsapp.py`

Responsabilidad original:

- Recibir webhooks de WhatsApp.
- Validar webhook de Meta.
- Extraer mensajes de texto, botones, listas, media, ubicaciones y contactos.
- Mantener sesiones por telefono en DynamoDB.
- Leer prompt de sistema desde S3.
- Invocar un Bedrock Agent.
- Convertir Markdown a formato compatible con WhatsApp.
- Enviar respuesta por WhatsApp Cloud API.
- Activar modo de vision computacional por palabras clave.
- Analizar imagenes cuando el usuario estaba en modo vision.
- Opcionalmente notificar a Slack.

Variables relevantes:

- `WHATSAPP_VERIFY_TOKEN`
- `WHATSAPP_PHONE_NUMBER_ID`
- `WHATSAPP_ACCESS_TOKEN`
- `SESSION_TABLE_NAME`
- `SYSTEM_PROMPT_BUCKET`
- `SYSTEM_PROMPT_KEY`
- `BEDROCK_AGENT_ID`
- `BEDROCK_AGENT_ALIAS_ID`
- `CONVERSATIONS_TABLE_NAME`
- `SLACK_WEBHOOK_URL`

Estado actual:

- No se usa en WABA Center.
- WABA Center actual es manual/asistido y usa `manual-chat-lambda/handler.py`.
- No debe apuntarse el webhook productivo de Meta a este Lambda sin un cambio formal de arquitectura.

Posible evolucion:

- Reutilizar esta carpeta para agregar un asistente IA que sugiera respuestas o atienda ciertos flujos.
- Integrarlo como modo opcional dentro de WABA Center, no como reemplazo directo del panel manual.
- Antes de reactivarlo habria que revisar prompts, permisos, tablas, costos de Bedrock y experiencia de escalamiento a asesor.

## `lambda-calendar/` - Action Group De Calendario

Ruta:

```text
lambda-calendar/
```

Archivos principales:

- `handler.py`
- `availability.py`
- `event_creator.py`
- `google_auth_helper.py`
- `validators.py`

Responsabilidad original:

- Servir como Action Group para un Bedrock Agent.
- Consultar disponibilidad en Google Calendar.
- Crear eventos en calendarios de equipo.
- Guardar citas creadas en DynamoDB.
- Manejar credenciales de Google Workspace mediante Secrets Manager/domain-wide delegation.

Variables relevantes:

- `APPOINTMENTS_TABLE_NAME`
- `GOOGLE_CREDENTIALS_SECRET_NAME`
- `TEAM_CALENDARS`
- `TIMEZONE`
- `IMPERSONATE_EMAIL`

Estado actual:

- No se usa en WABA Center.
- No forma parte del flujo de mensajes manuales.

Posible evolucion:

- Reactivar si WABA Center evoluciona a un asistente que agenda citas desde WhatsApp.
- Requeriria validar permisos de Google Workspace, secreto de credenciales, calendarios objetivo y politica de disponibilidad.

## `lambda-appointments/` - API De Citas

Ruta:

```text
lambda-appointments/handler.py
```

Responsabilidad original:

- Leer una tabla DynamoDB de citas.
- Retornar registros ordenados por `created_at`.

Variable relevante:

- `APPOINTMENTS_TABLE_NAME`

Estado actual:

- No se usa en WABA Center.
- Solo seria util si se reactiva el modulo de calendario/citas.

## `lambda-conversations/` - API Simple De Conversaciones

Ruta:

```text
lambda-conversations/handler.py
```

Responsabilidad original:

- Leer mensajes desde DynamoDB usando `CONVERSATIONS_TABLE_NAME`.
- Soportar `GET /conversations` y `GET /conversations?phone=...`.
- Usaba campos historicos como `phone_number`, `direction` y `message`.

Estado actual:

- Reemplazado por `manual-chat-lambda/handler.py`.
- El backend activo normaliza formatos nuevos y antiguos para el panel.

No se recomienda reactivarlo para WABA Center porque no cubre:

- contactos;
- medios;
- plantillas DANA;
- lectura;
- presencia;
- llamadas;
- bot de ausencia;
- proxy de media;
- estados de entrega.

## `lambda-send-message/` - API Simple De Envio

Ruta:

```text
lambda-send-message/handler.py
```

Responsabilidad original:

- Enviar un texto por WhatsApp Cloud API.
- Guardar el mensaje saliente en DynamoDB si `CONVERSATIONS_TABLE_NAME` esta configurado.

Variables relevantes:

- `WHATSAPP_PHONE_NUMBER_ID`
- `WHATSAPP_ACCESS_TOKEN`
- `CONVERSATIONS_TABLE_NAME`

Estado actual:

- Reemplazado por `manual-chat-lambda/handler.py`.

No se recomienda reactivarlo para WABA Center porque no cubre:

- envio optimista con `client_message_id`;
- marcado de lectura;
- datos de asesor;
- errores detallados de Meta;
- medios;
- llamadas;
- estructura actual de `chat-logs`.

## `infra/` - CDK Heredado

Ruta:

```text
infra/
```

Responsabilidad original:

- Provisionar infraestructura para el flujo Bedrock:
  - DynamoDB de sesiones;
  - buckets S3 para prompt y documentos;
  - OpenSearch Serverless para Knowledge Base;
  - Bedrock Knowledge Base;
  - Bedrock Agent y alias;
  - Lambda webhook;
  - API Gateway;
  - Lambda de calendario;
  - Secrets Manager para Google Calendar.

Estado actual:

- No representa el despliegue productivo de WABA Center.
- WABA Center se opera con un Lambda manual en consola llamado `VZla-Chatt_logger` y panel estatico en Amplify/hosting.
- El equipo no tiene contemplado usar CDK como ruta de despliegue para WABA Center en el corto plazo.

Posible evolucion:

- Puede servir para entender la infraestructura del prototipo Bedrock anterior.
- Si en algun momento se decide usar IaC, no deberia asumirse que este CDK esta listo para produccion.
- Antes de usarlo habria que reescribirlo o actualizarlo al alcance actual:
  - Lambda `VZla-Chatt_logger` o equivalente;
  - DynamoDB `chat-logs` y `chat-state`;
  - Function URL o API Gateway HTTP API;
  - layer de FFmpeg;
  - hosting estatico del panel;
  - permisos de Meta/Graph API;
  - componentes Bedrock solo si se reactiva asistente IA.

## Recomendacion Para Reactivacion Futura

Si se decide evolucionar WABA Center hacia un asistente con Bedrock, conviene hacerlo por fases:

1. Mantener `manual-chat-lambda/handler.py` como router y fuente de verdad del historial.
2. Agregar un modo de asistente que sugiera respuestas o atienda solo ciertos intents.
3. Guardar siempre en `chat-logs` lo que haga el asistente para que el panel mantenga trazabilidad.
4. Definir reglas claras de escalamiento a asesor humano.
5. Medir costos, latencia y seguridad antes de automatizar conversaciones reales.

Esta separacion evita que una reactivacion futura de Bedrock rompa la operacion manual actual de WABA Center.
