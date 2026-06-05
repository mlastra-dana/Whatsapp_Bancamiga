# ChattLogger - Documentacion funcional y tecnica

## Resumen general

ChattLogger es un panel de auditoria y atencion manual para conversaciones de WhatsApp. La aplicacion permite:

- ver conversaciones entrantes y salientes por numero/contacto;
- responder manualmente desde el panel;
- registrar plantillas enviadas desde DANA/Meta como mensajes salientes;
- ver media entrante cuando la URL temporal de Meta sigue disponible;
- identificar asesores conectados en la demo;
- marcar chats pendientes/leidos de forma global para todos los usuarios;
- guardar contactos y plantillas rapidas compartidas entre usuarios.

Para esta version, el flujo principal no usa Bedrock ni las Lambdas historicas del proyecto. La app usa el panel estatico `panel/conversaciones.html` y un unico Lambda actual en `manual-chat-lambda/handler.py`.

## Componentes activos

### Frontend

Archivo principal:

- `waba-bedrock-webhook/panel/conversaciones.html`

Responsabilidades:

- Login demo para dos asesores:
  - `ssalas` / Sarina Salas
  - `mlastra` / Maria Lastra
- Panel de conversaciones con polling cada 2 segundos.
- Dashboard simple:
  - asesor actual;
  - asesores conectados;
  - chats pendientes.
- Envio de mensajes manuales.
- Boton de ausencia/chatbot.
- Gestion de contactos.
- Gestion de plantillas rapidas.
- Render de mensajes enriquecidos:
  - imagenes;
  - audios;
  - documentos;
  - ubicaciones;
  - plantillas DANA;
  - mensajes del bot de ausencia.

La URL del backend se toma desde:

```js
window.CHATTLOGGER_API_URL
```

Si esta configurada, el panel usa esa base URL para llamar al Lambda.

### Backend

Archivo principal:

- `waba-bedrock-webhook/manual-chat-lambda/handler.py`

Responsabilidades:

- Recibir webhooks de WhatsApp Cloud API.
- Guardar mensajes entrantes en DynamoDB.
- Enviar mensajes manuales a WhatsApp desde el panel.
- Guardar mensajes salientes manuales.
- Guardar plantillas enviadas desde DANA.
- Consultar definiciones de plantilla en Meta cuando llega `template_id`.
- Hacer proxy de media temporal de Meta usando el token de WhatsApp.
- Persistir contactos, plantillas rapidas, estado de lectura y presencia de asesores.

## Flujo principal de mensajes

### Mensajes entrantes de WhatsApp

1. El cliente escribe o envia media por WhatsApp.
2. Meta llama el webhook del Lambda (`POST /webhook`).
3. `handle_whatsapp_webhook` extrae el mensaje.
4. `extraer_mensaje` normaliza el contenido segun tipo:
   - texto;
   - interactivo;
   - boton;
   - imagen;
   - audio;
   - documento;
   - ubicacion.
5. `guardar_mensaje` guarda el mensaje en `chat-logs`.
6. El panel consulta `GET /conversations` y renderiza la conversacion.

### Respuestas manuales desde el panel

1. El asesor escribe una respuesta.
2. El panel llama `POST /send-message`.
3. El Lambda envia el mensaje a WhatsApp Cloud API.
4. El Lambda guarda el mensaje saliente en `chat-logs`.
5. `mark_conversation_attended` marca el ultimo inbound de ese telefono como leido en `chat-state`.
6. Los demas usuarios dejan de ver ese chat como pendiente en el siguiente polling.

### Plantillas enviadas desde DANA

El envio real de la plantilla ocurre desde DANA contra Meta. Luego DANA hace un `POST` al Lambda:

```http
POST /dana/outbound-template
```

El Lambda guarda ese envio como mensaje saliente para que aparezca en el historial del chat.

Si el body incluye `template_id` o `meta_template_id`, el Lambda consulta Meta:

```http
GET https://graph.facebook.com/v20.0/{TEMPLATE_ID}
```

Con esa respuesta obtiene:

- texto del body de la plantilla;
- imagen de header si existe;
- nombre de la plantilla;
- payload original.

Los placeholders como `{{1}}` se reemplazan por `NombreCliente` para que el historial identifique que habia una variable sin depender del cliente especifico.

## Endpoints activos del Lambda

| Metodo | Ruta | Uso |
|---|---|---|
| `GET` | `/` o `/webhook` | Verificacion del webhook de Meta. |
| `POST` | `/` o `/webhook` | Recepcion de mensajes WhatsApp. |
| `GET` | `/conversations` | Lista conversaciones. |
| `GET` | `/conversations?phone=...` | Lista mensajes de un telefono. |
| `POST` | `/send-message` | Envia respuesta manual desde el panel. |
| `GET` | `/media?url=...` | Proxy temporal de media Meta. |
| `GET` | `/contacts` | Lista contactos guardados. |
| `POST` | `/contacts` | Crea/actualiza contacto. |
| `GET` | `/templates` | Lista plantillas rapidas del panel. |
| `POST` | `/templates` | Guarda plantillas rapidas del panel. |
| `GET` | `/read-state` | Lista estado global de lectura. |
| `POST` | `/read-state` | Marca conversacion como leida. |
| `GET` | `/agents` | Lista asesores y estado online. |
| `POST` | `/agent-presence` | Actualiza presencia de asesor. |
| `POST` | `/dana/outbound-template` | Guarda plantilla outbound enviada desde DANA. |
| `GET` | `/absence-bot` | Lee configuracion del bot de ausencia. |
| `POST` | `/absence-bot` | Actualiza configuracion del bot de ausencia. |

## Tablas DynamoDB usadas

Solo se usan dos tablas para esta version:

### `chat-logs`

Variable de entorno:

```txt
CONVERSATIONS_TABLE_NAME=chat-logs
```

Uso:

- mensajes entrantes;
- mensajes salientes manuales;
- respuestas del bot de ausencia;
- plantillas DANA;
- payload de plantilla cuando aplica.

Campos relevantes:

- `telefono`
- `timestamp`
- `mensaje`
- `tipo` (`entrada` o `salida`)
- `canal`
- `msg_type`
- `agent_username`
- `agent_name`
- `template_name`
- `template_id`
- `template_payload`

### `chat-state`

Variable de entorno:

```txt
STATE_TABLE_NAME=chat-state
```

Uso:

- estado simple de bot por telefono;
- contactos guardados;
- plantillas rapidas;
- estado de lectura global;
- presencia de asesores;
- configuracion del bot de ausencia.

Keys especiales:

- `contact#{phone}`: contacto guardado.
- `read#{phone}`: ultimo inbound leido.
- `agent#{username}`: presencia del asesor.
- `__quick_templates__`: plantillas rapidas del panel.
- `__absence_bot__`: configuracion del bot de ausencia.

## Media: imagenes, audios y documentos

WhatsApp/Meta no envia directamente el archivo al webhook. Para imagenes, audios y documentos, Meta envia un `media_id`. El Lambda usa ese ID para pedir a Meta una URL temporal.

El panel no accede directamente a la URL privada de Meta. Usa:

```http
GET /media?url={URL_TEMPORAL_META}
```

El Lambda agrega:

```http
Authorization: Bearer {WHATSAPP_TOKEN}
```

y devuelve el archivo al navegador.

### Limitacion importante

Las URLs de media de Meta son temporales. Cuando expiran:

- las imagenes lo detectan al intentar cargar y muestran `Imagen expirada`;
- los audios se marcan como `Audio expirado` cuando el reproductor falla;
- los documentos se marcan como `Documento expirado` cuando el usuario intenta abrirlos y el proxy ya no puede descargarlos.

Esto es aceptable para la demo. Para produccion, la recomendacion es descargar la media al momento del webhook y guardarla en storage propio, por ejemplo S3. Asi el historial podria mostrar archivos de forma permanente.

## Chats pendientes y lectura global

El criterio de pendiente es global, no por usuario.

Un chat aparece pendiente cuando:

- tiene mensajes entrantes mas recientes que el ultimo `read#{phone}` guardado;
- y ningun asesor lo ha leido/atendido despues de ese inbound.

Se marca como atendido cuando:

- un asesor abre/lee el chat desde el panel;
- o un asesor responde manualmente desde `/send-message`.

Cuando un asesor responde, `mark_conversation_attended` actualiza `read#{phone}` con el timestamp del ultimo inbound. Por eso el pendiente desaparece para todos los usuarios, no solo para quien respondio.

## Presencia de asesores

El panel envia heartbeats a:

```http
POST /agent-presence
```

El Lambda guarda:

- username;
- nombre;
- `last_seen`;
- `online`.

Un asesor se considera conectado si:

- `online=true`;
- y `last_seen` tiene menos de 90 segundos.

Al hacer click en `Salir`, el panel envia `online=false`.

## Bot de ausencia

El bot de ausencia es el unico componente automatico que puede responder cuando esta activado.

Endpoints:

- `GET /absence-bot`
- `POST /absence-bot`

Si esta activo, cuando llega un inbound:

1. el Lambda guarda el inbound;
2. envia el mensaje configurado;
3. guarda esa salida como `msg_type=absence`;
4. el panel lo muestra como `Bot`.

## Flujos heredados que no forman parte de esta version

El repo conserva carpetas y codigo de versiones anteriores:

- `lambda/`
- `lambda-calendar/`
- `lambda-appointments/`
- `lambda-conversations/`
- `lambda-send-message/`
- `infra/`

Para esta version de ChattLogger no se deben considerar como backend activo. El panel debe apuntar solamente al endpoint del Lambda actual en:

```txt
waba-bedrock-webhook/manual-chat-lambda/handler.py
```

## Recomendaciones para evolucion futura

- Persistir media en S3 cuando llega el webhook.
- Agregar previsualizacion real de PDF/documentos si se guarda la media.
- Mover usuarios demo a autenticacion real si el panel deja de ser demo.
- Evaluar SSE o WebSocket para notificaciones en tiempo real sin polling.
- Agregar roles, por ejemplo asesor y administrador.
- Separar configuracion por ambiente: local, demo y produccion.
