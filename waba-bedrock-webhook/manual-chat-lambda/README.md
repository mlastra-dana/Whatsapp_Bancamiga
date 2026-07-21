# Manual Chat Lambda - VZla-Chatt_logger

Este directorio contiene el backend activo de WABA Center.

En AWS Lambda, la funcion productiva se despliega manualmente en la consola con el nombre:

```text
VZla-Chatt_logger
```

Archivo fuente:

```text
manual-chat-lambda/handler.py
```

## Responsabilidad

Este Lambda concentra la API del panel WABA Center y el webhook de WhatsApp Cloud API.

Hace lo siguiente:

- Recibe webhooks de Meta WhatsApp Cloud API.
- Normaliza mensajes entrantes de texto, botones, listas, media, ubicacion, contactos, stickers, estados y llamadas.
- Guarda historial en DynamoDB `chat-logs`.
- Guarda estado operativo en DynamoDB `chat-state`.
- Envia mensajes manuales desde el panel.
- Envia medios desde el panel.
- Convierte notas de voz de navegador con FFmpeg cuando el formato no es confiable para WhatsApp.
- Hace proxy seguro de medios temporales de Meta.
- Registra plantillas outbound enviadas desde DANAConnect.
- Guarda contactos enviados manualmente o recibidos desde payloads de DANA.
- Gestiona plantillas rapidas del panel.
- Gestiona estado de lectura y presencia de asesores.
- Gestiona configuracion del bot de ausencia.
- Expone endpoints de llamadas WhatsApp: permiso, conectar, aceptar, rechazar y terminar.

No usa Bedrock. No depende de los modulos historicos de calendario/citas.

## Despliegue Actual

El despliegue productivo no se hace desde CDK en este repo. El procedimiento vigente es manual:

1. Abrir AWS Lambda en la consola.
2. Buscar la funcion `VZla-Chatt_logger`.
3. Actualizar el codigo con el contenido de `manual-chat-lambda/handler.py`.
4. Confirmar variables de entorno.
5. Confirmar que el layer de FFmpeg siga asociado si se usan notas de voz.
6. Probar endpoints principales desde el panel o CloudWatch.

`template.yaml` queda como referencia SAM, pero no representa el despliegue productivo actual si el equipo sigue actualizando desde consola.

## Variables De Entorno

| Variable | Requerida | Uso |
|---|---:|---|
| `WHATSAPP_TOKEN` o `WHATSAPP_ACCESS_TOKEN` | Si | Token de Meta Graph API. |
| `PHONE_NUMBER_ID` o `WHATSAPP_PHONE_NUMBER_ID` | Si | ID del numero de WhatsApp Business. |
| `WHATSAPP_VERIFY_TOKEN` | Si | Token usado por Meta para verificar webhook. |
| `STATE_TABLE_NAME` | Si | Tabla DynamoDB de estado. Por defecto `chat-state`. |
| `CONVERSATIONS_TABLE_NAME` | Si | Tabla DynamoDB de historial. Por defecto `chat-logs`. |
| `CORS_ORIGIN` | No | Origen permitido para el panel. Por defecto `*`. |
| `GRAPH_API_VERSION` | No | Version de Graph API. Por defecto `v20.0`. |
| `MAX_MEDIA_BYTES` | No | Limite de descarga/proxy de medios. |
| `MAX_UPLOAD_MEDIA_BYTES` | No | Limite de subida de medios desde panel. |
| `FFMPEG_PATH` | Recomendado | Ruta de FFmpeg. Por defecto `/opt/bin/ffmpeg`. |
| `WHATSAPP_BUSINESS_NUMBER` o `BUSINESS_PHONE_NUMBER` | Recomendado | Numero del negocio para resolver eventos de llamadas. |

## Tablas DynamoDB

### `chat-logs`

Variable:

```text
CONVERSATIONS_TABLE_NAME=chat-logs
```

Keys:

- Partition key: `telefono`
- Sort key: `timestamp`

Uso:

- Mensajes entrantes.
- Mensajes manuales salientes.
- Medios.
- Estados de entrega fallida.
- Eventos de llamada.
- Plantillas outbound de DANA.
- Bot de ausencia.

Campos comunes:

- `telefono`
- `timestamp`
- `mensaje`
- `tipo`
- `canal`
- `msg_type`
- `agent_username`
- `agent_name`
- `client_message_id`
- `call_id`
- `call_payload`
- `template_name`
- `template_id`
- `template_buttons`
- `template_payload`

### `chat-state`

Variable:

```text
STATE_TABLE_NAME=chat-state
```

Key:

- Partition key: `telefono`

Registros especiales:

- `contact#{phone}`: contacto guardado.
- `read#{phone}`: ultimo inbound leido.
- `agent#{username}`: presencia de asesor.
- `__quick_templates__`: plantillas rapidas.
- `__absence_bot__`: configuracion del bot de ausencia.

## Rutas

| Metodo | Ruta | Uso |
|---|---|---|
| `GET` | `/` o `/webhook` | Verificacion del webhook de Meta. |
| `POST` | `/` o `/webhook` | Recibe mensajes, estados y llamadas de WhatsApp. |
| `GET` | `/conversations` | Lista conversaciones normalizadas. |
| `GET` | `/conversations?phone=...` | Lista una conversacion. |
| `GET` | `/media?id=...` o `/media?url=...` | Proxy seguro de medios temporales. |
| `POST` | `/send-message` | Envia texto manual desde el panel. |
| `POST` | `/send-media` | Envia medios desde el panel. |
| `POST` | `/calls/request-permission` | Solicita permiso de llamada saliente. |
| `POST` | `/calls/connect` | Inicia llamada WhatsApp. |
| `POST` | `/calls/accept` | Acepta/preacepta llamada entrante. |
| `POST` | `/calls/reject` | Rechaza llamada entrante. |
| `POST` | `/calls/terminate` | Termina llamada. |
| `GET` | `/contacts` | Lista contactos guardados. |
| `POST` | `/contacts` | Guarda/actualiza contacto. |
| `GET` | `/templates` | Lista plantillas rapidas. |
| `POST` | `/templates` | Guarda plantillas rapidas. |
| `GET` | `/read-state` | Lista estado de lectura. |
| `POST` | `/read-state` | Marca conversacion como leida. |
| `GET` | `/agents` | Lista asesores conectados. |
| `POST` | `/agent-presence` | Actualiza presencia del asesor. |
| `GET` | `/absence-bot` | Lee configuracion del bot de ausencia. |
| `POST` | `/absence-bot` | Actualiza bot de ausencia. |
| `POST` | `/dana/outbound-template` | Registra plantilla outbound enviada por DANA. |

## Webhook De WhatsApp

Meta debe apuntar el webhook hacia la Function URL del Lambda `VZla-Chatt_logger`.

El Lambda procesa:

- `messages`: mensajes entrantes.
- `statuses`: estados de entrega, incluyendo errores como ventana de 24 horas cerrada.
- `calls`: eventos de llamada WhatsApp.

Para respuestas de botones/listas, Meta envia la seleccion dentro del webhook de `messages`. No se consulta otra API.

Campos relevantes:

```text
messages[0].interactive.button_reply.title
messages[0].interactive.button_reply.id
messages[0].interactive.list_reply.title
messages[0].interactive.list_reply.id
messages[0].button.text
```

Los botones tipo URL no generan webhook de clic. Para tracking de clics se requiere URL propia con tracking.

## Integracion Con DANAConnect

DANA envia el mensaje de plantilla a WhatsApp y luego notifica al Lambda:

```http
POST /dana/outbound-template
```

El Lambda guarda esa plantilla como outbound en `chat-logs`, conserva `template_payload` y, si hay `template_id`, puede consultar Meta para reconstruir texto, botones y header.

Campos recomendados:

- `to`, `phone` o `Telefono`
- `NombreCliente`
- `Email`
- `template.name`
- `template_id` o `meta_template_id`
- `source_flow`
- componentes dinamicos de body/header/botones

## Notas Operativas

- El panel usa `panel/config.js` para conocer la URL del Lambda:

```js
window.CHATTLOGGER_API_URL = 'https://<lambda-function-url>';
```

- Si cambian variables de entorno o layer, verificar CloudWatch tras una prueba real.
- Para audios grabados desde navegador, mantener disponible FFmpeg en `/opt/bin/ffmpeg`.
- Si Meta devuelve errores de envio, el Lambda registra eventos de estado para que el panel muestre el motivo.
