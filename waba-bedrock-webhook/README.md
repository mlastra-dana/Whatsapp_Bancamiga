# WABA Center - DANAConnect

WABA Center es el panel operativo de DANAConnect para atender conversaciones de WhatsApp Business desde una interfaz web. El proyecto centraliza mensajes entrantes, respuestas manuales de asesores, archivos, notas de voz, llamadas por WhatsApp, contactos, plantillas rapidas, lectura de chats y trazabilidad de plantillas enviadas desde flujos de DANA.

El backend activo de este proyecto es un Lambda desplegado manualmente en la consola de AWS con el nombre:

```text
VZla-Chatt_logger
```

El codigo fuente que corresponde a ese Lambda vive en:

```text
manual-chat-lambda/handler.py
```

## Que Se Usa Hoy En Produccion/Demo

Para operar WABA Center hoy solo se necesita este flujo:

```text
panel/                    # frontend estatico publicado en Amplify/hosting
manual-chat-lambda/        # backend activo
manual-chat-lambda/handler.py
AWS Lambda VZla-Chatt_logger
DynamoDB chat-logs
DynamoDB chat-state
Meta WhatsApp Cloud API
```

Los demas Lambdas y carpetas se conservan como codigo historico o base futura. No deben considerarse parte del despliegue activo si no hay una decision explicita de reactivarlos.

## Alcance Actual

Este repositorio documenta y mantiene WABA Center. Lo que esta en alcance:

- Panel web estatico para operacion de WhatsApp Business.
- Recepcion de webhooks de Meta WhatsApp Cloud API.
- Historial de conversaciones en DynamoDB.
- Respuestas manuales desde el panel.
- Envio y recepcion de medios compatibles con WhatsApp.
- Grabacion de notas de voz desde navegador y conversion con FFmpeg.
- Llamadas por WhatsApp desde el panel cuando la cuenta/WABA tiene la capacidad habilitada.
- Contactos guardados manualmente y contactos recibidos desde payloads de DANA.
- Registro de plantillas outbound enviadas desde DANAConnect.
- Bot de ausencia configurable.
- Presencia de asesores, mensajes sin leer y notificaciones visuales.
- PWA instalable en escritorio, iOS y Android.

Fuera del alcance actual de WABA Center:

- Bot anterior con Bedrock.
- Modulos de calendario/citas.
- Lambdas historicas de prueba o prototipo.
- CDK heredado como fuente de despliegue productivo.

Esos componentes se conservan dentro del repo como referencia tecnica por si WABA Center evoluciona a un asistente con Bedrock o reactiva agenda/citas en el futuro. No esta contemplado activarlos en el corto plazo. Ver [docs/legacy-components.md](docs/legacy-components.md).

## Arquitectura

```text
Meta WhatsApp Cloud API
        |
        | Webhooks: messages, statuses, calls
        v
AWS Lambda Function URL
VZla-Chatt_logger
manual-chat-lambda/handler.py
        |
        +--> DynamoDB chat-logs   # historial de conversaciones
        +--> DynamoDB chat-state  # contactos, lectura, presencia y configuracion
        |
        +--> Meta Graph API       # envio de mensajes, medios y llamadas
        |
        +--> FFmpeg Lambda Layer  # conversion de audio grabado en navegador

Amplify Hosting / hosting estatico
panel/index.html
panel/waba-center.html
panel/conversaciones.html
panel/manifest.webmanifest
panel/service-worker.js
```

## Estructura Relevante

```text
waba-bedrock-webhook/
├── manual-chat-lambda/
│   ├── handler.py          # Backend activo de WABA Center / VZla-Chatt_logger
│   ├── README.md           # Notas tecnicas del Lambda
│   └── template.yaml       # Referencia SAM, no es el despliegue productivo actual
├── panel/
│   ├── index.html          # Login/entrada
│   ├── waba-center.html    # URL limpia del producto
│   ├── conversaciones.html # Aplicacion principal WABA Center
│   ├── config.js           # URL del Lambda
│   ├── manifest.webmanifest
│   ├── service-worker.js
│   └── assets/brand/       # Logos e iconos PWA
├── docs/
│   ├── chattlogger-overview.md # Documento historico/funcional de referencia
│   └── legacy-components.md    # Inventario de Lambdas heredados y posible reactivacion futura
├── lambda*/                # Componentes heredados fuera del flujo activo
├── infra/                  # CDK heredado fuera del despliegue actual
└── tests/                  # Pruebas heredadas y utilidades
```

## Funcionalidades Del Panel

- Login simple de asesores para la demo.
- Bandeja de conversaciones con busqueda global por chats y mensajes.
- Busqueda interna dentro del chat abierto.
- Filtros por fecha.
- Indicador de mensajes sin leer.
- Marcado de lectura al abrir o responder un chat.
- Notificacion visual de mensajes nuevos.
- Identificacion visual por asesor con iniciales y color.
- Envio optimista de mensajes manuales para que aparezcan inmediatamente en el chat.
- Contactos guardados y actualizados desde DANA cuando aplica.
- Exportacion de conversaciones.
- Plantillas rapidas editables.
- Envio de texto, imagenes, documentos, stickers, audio y video soportado.
- Grabacion de notas de voz desde navegador.
- Proxy seguro de medios temporales de WhatsApp.
- Soporte de eventos de llamada: entrante, contestar, rechazar, colgar y registrar historial.
- PWA instalable en iOS, Android y escritorio.

## Flujo De Mensajes

### Mensaje entrante

1. El cliente escribe por WhatsApp.
2. Meta llama el webhook configurado hacia `VZla-Chatt_logger`.
3. `handle_whatsapp_webhook` recibe el payload.
4. `extraer_mensaje` normaliza el contenido segun tipo: texto, boton, lista, imagen, audio, documento, ubicacion, contacto, sticker, video o llamada.
5. `guardar_mensaje` guarda el evento en `chat-logs`.
6. El panel consulta `GET /conversations` y muestra el mensaje.

### Respuesta manual

1. El asesor responde desde WABA Center.
2. El panel llama `POST /send-message`.
3. El Lambda envia el mensaje por Meta Graph API.
4. El Lambda guarda el mensaje saliente en `chat-logs`.
5. El panel muestra el mensaje inmediatamente usando `client_message_id` y luego lo reconcilia con el registro remoto.
6. `mark_conversation_attended` marca el ultimo mensaje entrante como leido en `chat-state`.

### Botones y listas de WhatsApp

Para respuestas rapidas y listas, no se consulta una API adicional. Meta envia la seleccion del usuario directamente en el webhook de mensajes.

Campos capturados:

```text
messages[0].interactive.button_reply.title
messages[0].interactive.button_reply.id
messages[0].interactive.list_reply.title
messages[0].interactive.list_reply.id
messages[0].button.text
```

Nota: los botones tipo URL no generan un webhook de "clic". Para medir clics en URL se necesita un enlace trackeable propio.

### Plantillas enviadas desde DANAConnect

DANA envia la plantilla real a WhatsApp. Luego hace un POST al Lambda para que WABA Center registre ese envio en el historial:

```http
POST /dana/outbound-template
Content-Type: application/json
```

Campos recomendados:

```json
{
  "channel": "whatsapp",
  "provider": "dana",
  "direction": "outbound",
  "message_type": "template",
  "source_flow": "Demo",
  "template_id": "870991519000923",
  "to": "$s{Telefono}",
  "NombreCliente": "$s{NombreCliente}",
  "Email": "$s{Email}",
  "template": {
    "name": "notificacion_cotizacion",
    "language": {
      "code": "es",
      "policy": "deterministic"
    },
    "components": []
  }
}
```

El Lambda puede consultar la definicion de la plantilla en Meta usando `template_id`, renderizar el texto base, conservar botones dinamicos y guardar el payload original. El panel evita que campanas masivas outbound abran miles de chats activos si el cliente no ha respondido.

## API Del Lambda `VZla-Chatt_logger`

| Metodo | Ruta | Uso |
|---|---|---|
| `GET` | `/` o `/webhook` | Verificacion del webhook de Meta. |
| `POST` | `/` o `/webhook` | Recepcion de mensajes, estados y llamadas de WhatsApp. |
| `GET` | `/conversations` | Lista conversaciones normalizadas para el panel. |
| `GET` | `/conversations?phone=...` | Lista una conversacion especifica. |
| `GET` | `/media?id=...` o `/media?url=...` | Proxy seguro de medios temporales de WhatsApp. |
| `POST` | `/send-message` | Envio de texto desde el panel. |
| `POST` | `/send-media` | Envio de medios desde el panel. |
| `POST` | `/calls/request-permission` | Solicita permiso para llamada saliente. |
| `POST` | `/calls/connect` | Inicia llamada WhatsApp desde el panel. |
| `POST` | `/calls/accept` | Acepta/preacepta llamada entrante. |
| `POST` | `/calls/reject` | Rechaza llamada entrante. |
| `POST` | `/calls/terminate` | Finaliza una llamada. |
| `GET` | `/contacts` | Lista contactos guardados. |
| `POST` | `/contacts` | Guarda o actualiza contacto manual. |
| `GET` | `/templates` | Lista plantillas rapidas del panel. |
| `POST` | `/templates` | Guarda plantillas rapidas del panel. |
| `GET` | `/read-state` | Estado de lectura por conversacion. |
| `POST` | `/read-state` | Actualiza lectura de una conversacion. |
| `GET` | `/agents` | Lista asesores conectados. |
| `POST` | `/agent-presence` | Actualiza presencia de asesor. |
| `GET` | `/absence-bot` | Lee configuracion del bot de ausencia. |
| `POST` | `/absence-bot` | Actualiza bot de ausencia. |
| `POST` | `/dana/outbound-template` | Registra plantilla outbound enviada por DANAConnect. |

## Infraestructura Necesaria

### AWS

- **Lambda `VZla-Chatt_logger`** con codigo de `manual-chat-lambda/handler.py`.
- **Lambda Function URL** habilitada para recibir webhooks y llamadas del panel.
- **DynamoDB `chat-logs`**
  - Partition key: `telefono`
  - Sort key: `timestamp`
- **DynamoDB `chat-state`**
  - Partition key: `telefono`
  - Guarda contactos, lectura, presencia, plantillas rapidas y configuracion.
- **Lambda Layer con FFmpeg**
  - Ruta esperada: `/opt/bin/ffmpeg`
  - Necesario para convertir audios grabados desde Chrome/Safari.
- **Amplify Hosting** o hosting estatico equivalente para `panel/`.
- **CloudWatch Logs** para diagnostico.

### Meta / WhatsApp

- Meta App con WhatsApp Cloud API habilitada.
- WhatsApp Business Account.
- Phone Number ID.
- Access Token de Meta Graph API.
- Webhook configurado hacia la Function URL del Lambda `VZla-Chatt_logger`.
- Suscripcion a eventos de mensajes, estados y llamadas segun capacidades habilitadas.
- Permisos/capacidades de WhatsApp Calling si se usa llamada desde panel.

### DANAConnect

- Flujo que envia plantillas aprobadas por WhatsApp.
- Paso API Request hacia `/dana/outbound-template` para registrar el outbound en WABA Center.
- Campos de contacto y plantilla en el payload, especialmente telefono, nombre, `template.name`, `template_id` y parametros dinamicos.

## Variables De Entorno Del Lambda

| Variable | Requerida | Uso |
|---|---:|---|
| `WHATSAPP_TOKEN` o `WHATSAPP_ACCESS_TOKEN` | Si | Token de Meta Graph API. |
| `PHONE_NUMBER_ID` o `WHATSAPP_PHONE_NUMBER_ID` | Si | ID del numero de WhatsApp Business. |
| `WHATSAPP_VERIFY_TOKEN` | Si | Token de verificacion del webhook de Meta. |
| `STATE_TABLE_NAME` | Si | Tabla DynamoDB de estado. Por defecto `chat-state`. |
| `CONVERSATIONS_TABLE_NAME` | Si | Tabla DynamoDB de logs. Por defecto `chat-logs`. |
| `CORS_ORIGIN` | No | Origen permitido para el panel. Por defecto `*`. |
| `GRAPH_API_VERSION` | No | Version de Graph API. Por defecto `v20.0`. |
| `MAX_MEDIA_BYTES` | No | Tamano maximo para proxy de medios. |
| `MAX_UPLOAD_MEDIA_BYTES` | No | Tamano maximo para subir medios desde panel. |
| `FFMPEG_PATH` | Recomendado | Ruta de FFmpeg. Por defecto `/opt/bin/ffmpeg`. |
| `WHATSAPP_BUSINESS_NUMBER` o `BUSINESS_PHONE_NUMBER` | Recomendado | Numero del negocio para resolver eventos de llamadas. |

## Hosting Del Panel

El panel es estatico y puede publicarse en Amplify, S3/CloudFront o servirse localmente.

Archivo principal recomendado:

```text
panel/waba-center.html
```

Para probar local:

```bash
cd waba-bedrock-webhook/panel
python3 -m http.server 8092
```

Abrir:

```text
http://127.0.0.1:8092/waba-center.html
```

Configurar `panel/config.js` con la URL del Lambda:

```js
window.CHATTLOGGER_API_URL = 'https://<lambda-function-url>';
```

## PWA

La PWA usa:

- `panel/manifest.webmanifest`
- `panel/service-worker.js`
- iconos en `panel/assets/brand/`
- `waba-center.html` como entrada recomendada

Consideraciones:

- En iPhone/iPad se instala desde Safari con "Agregar a inicio".
- En Android/Chrome se instala desde "Instalar app" o "Agregar a pantalla principal".
- En escritorio Chrome/Edge muestran "Open in app".
- Si se cambia el icono, puede ser necesario desinstalar y volver a instalar la PWA porque los navegadores cachean iconos.

## Medios Soportados

El backend valida formatos compatibles con WhatsApp Cloud API:

- Imagen: JPEG, PNG, WEBP.
- Audio: MP3, OGG/Opus, AAC, AMR.
- Documento: PDF, TXT, Word, Excel, PowerPoint.
- Video: MP4, 3GPP.
- Sticker: WEBP.

Los CSV no se envian directamente porque Meta no los acepta como documento soportado. Se recomienda convertirlos a XLSX, PDF o TXT.

## Operacion Y Mantenimiento

- Desplegar cambios del backend copiando/actualizando `manual-chat-lambda/handler.py` en el Lambda `VZla-Chatt_logger` o mediante el mecanismo manual vigente del equipo.
- Verificar que el Lambda conserve sus variables de entorno y el layer de FFmpeg.
- Revisar CloudWatch Logs cuando Meta responda con errores de envio o webhook.
- Confirmar que el webhook de Meta apunte al Lambda correcto despues de cambios de entorno.
- Desplegar `panel/` completo cuando cambien HTML, JS, PWA, iconos o `config.js`.
- Validar que Amplify no sirva versiones viejas de `manifest.webmanifest` y `service-worker.js`.

## Estado Del Proyecto

WABA Center esta orientado a operacion manual/asistida de WhatsApp Business. La fuente activa del backend es `manual-chat-lambda/handler.py` y el Lambda productivo se llama `VZla-Chatt_logger`.

El resto de modulos historicos del repo pueden mantenerse para referencia, pero no deben considerarse parte del alcance activo salvo que se reactive explicitamente.
