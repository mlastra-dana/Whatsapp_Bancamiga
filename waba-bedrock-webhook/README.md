# WABA Center - DANAConnect

WABA Center es un panel operativo para atender conversaciones de WhatsApp Business desde DANAConnect. El proyecto centraliza mensajes entrantes, respuestas manuales de asesores, envio de archivos, notas de voz, llamadas por WhatsApp, contactos, plantillas rapidas y trazabilidad de plantillas enviadas desde flujos de DANA.

El objetivo principal es que el equipo pueda operar WhatsApp desde una interfaz web simple, con historial completo por cliente, indicadores de pendientes y soporte para escritorio, iOS y Android como PWA.

## Resumen Ejecutivo

Este repositorio contiene una solucion web + serverless para operar un canal WhatsApp Business:

- Recibe webhooks de Meta WhatsApp Cloud API.
- Guarda mensajes, eventos de llamada, estados y plantillas en DynamoDB.
- Expone una API Lambda para el panel WABA Center.
- Permite responder manualmente desde el panel.
- Permite enviar y recibir medios: imagenes, documentos, stickers, audio y video soportados por Meta.
- Convierte notas de voz grabadas desde navegador con FFmpeg para que sean compatibles con WhatsApp.
- Registra plantillas enviadas desde DANAConnect sin abrir conversaciones masivas hasta que el cliente responda.
- Mantiene contactos con prioridad para nombres guardados/manuales o enviados desde DANA.
- Muestra asesores conectados, pendientes, mensajes no leidos e identificacion visual por asesor.
- Funciona como PWA instalable en escritorio, iPhone y Android.

## Componentes Principales

```text
Meta WhatsApp Cloud API
        |
        | Webhook messages/calls/statuses
        v
AWS Lambda Function URL
manual-chat-lambda/handler.py
        |
        +--> DynamoDB chat-logs   # historial de conversaciones
        +--> DynamoDB chat-state  # contactos, lectura, presencia, plantillas, configuracion
        |
        +--> Meta Graph API       # envio de mensajes, medios y llamadas
        |
        +--> FFmpeg Layer         # conversion de audio de navegador

Amplify Hosting / Static Hosting
panel/waba-center.html
panel/conversaciones.html
panel/manifest.webmanifest
panel/service-worker.js
```

## Estructura del Proyecto

```text
waba-bedrock-webhook/
├── manual-chat-lambda/
│   ├── handler.py          # Backend principal del WABA Center
│   └── README.md           # Notas tecnicas del Lambda manual
├── panel/
│   ├── index.html          # Entrada/login
│   ├── waba-center.html    # URL limpia para WABA Center
│   ├── conversaciones.html # Aplicacion principal
│   ├── config.js           # URL del Lambda/API
│   ├── manifest.webmanifest
│   ├── service-worker.js
│   └── assets/brand/       # Logos e iconos PWA
├── lambda-conversations/   # Lambda legado/simple de conversaciones
├── lambda-send-message/    # Lambda legado/simple de envio
├── infra/                  # CDK heredado del proyecto anterior
├── layers/                 # Assets de capas Lambda
└── tests/
```

> Nota: parte del repositorio conserva componentes heredados del bot anterior con Bedrock/Calendar. La operacion actual de WABA Center vive principalmente en `manual-chat-lambda/` y `panel/`.

## Funcionalidades del Panel

- Login de asesores.
- Bandeja de conversaciones con busqueda por nombre, telefono o mensaje.
- Filtros por fecha.
- Indicador de mensajes sin leer.
- Marcado de lectura al abrir o responder un chat.
- Notificacion visual de mensajes nuevos.
- Identificacion por asesor con iniciales y color.
- Contactos guardados manualmente o recibidos desde DANA.
- Exportacion de conversaciones/contactos.
- Plantillas rapidas editables con emojis.
- Envio de texto, imagenes, documentos, stickers, audio y video soportado.
- Grabacion de notas de voz desde navegador.
- Soporte para llamadas WhatsApp: solicitud de permiso, llamada saliente, aceptar/rechazar/terminar.
- PWA instalable en iOS, Android y escritorio.

## Integracion con DANAConnect

Los flujos de DANAConnect pueden notificar al Lambda cuando se envia una plantilla:

```http
POST /dana/outbound-template
Content-Type: application/json
```

Ejemplo de payload:

```json
{
  "channel": "whatsapp",
  "provider": "dana",
  "direction": "outbound",
  "message_type": "template",
  "source_flow": "Bancamiga",
  "template_id": "2069794663641823",
  "messaging_product": "whatsapp",
  "to": "$s{Telefono}",
  "NombreCliente": "$s{NombreCliente}",
  "Email": "$s{Email}",
  "type": "template",
  "template": {
    "name": "agente_bancario",
    "language": {
      "code": "es",
      "policy": "deterministic"
    },
    "components": []
  }
}
```

El panel registra la plantilla en el historial, pero evita abrir conversaciones masivas solo por mensajes outbound. La conversacion aparece como activa cuando existe interaccion real del cliente o eventos relevantes de atencion.

## API del Lambda Principal

| Metodo | Ruta | Uso |
|---|---|---|
| `GET` | `/` o `/webhook` | Verificacion del webhook de Meta. |
| `POST` | `/` o `/webhook` | Recepcion de mensajes, estados y eventos de WhatsApp. |
| `GET` | `/conversations` | Lista conversaciones normalizadas para el panel. |
| `GET` | `/conversations?phone=...` | Lista una conversacion especifica. |
| `GET` | `/media?id=...` o `/media?url=...` | Proxy seguro de medios de WhatsApp. |
| `POST` | `/send-message` | Envio de texto desde el panel. |
| `POST` | `/send-media` | Envio de medios desde el panel. |
| `POST` | `/calls/request-permission` | Solicita permiso para llamar al cliente. |
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
| `GET` | `/absence-bot` | Configuracion de respuesta de ausencia. |
| `POST` | `/absence-bot` | Actualiza respuesta de ausencia. |
| `POST` | `/dana/outbound-template` | Registra plantilla outbound enviada por DANAConnect. |

## Infraestructura Necesaria

### AWS

- **AWS Lambda** para `manual-chat-lambda/handler.py`.
- **Lambda Function URL** o API Gateway HTTP API para exponer el backend.
- **DynamoDB `chat-logs`** para historial de conversaciones.
  - Partition key: `telefono`
  - Sort key: `timestamp`
- **DynamoDB `chat-state`** para estado operativo.
  - Partition key: `telefono`
  - Guarda contactos, lectura, presencia, plantillas rapidas y configuracion.
- **Lambda Layer con FFmpeg** para convertir audios grabados desde Chrome/Safari.
  - Ruta esperada: `/opt/bin/ffmpeg`
  - Variable: `FFMPEG_PATH=/opt/bin/ffmpeg`
- **Amplify Hosting** o hosting estatico equivalente para `panel/`.
- **CloudWatch Logs** para diagnostico de envios, webhooks y errores de Meta.

### Meta / WhatsApp

- Meta App con WhatsApp Cloud API habilitada.
- WhatsApp Business Account y Phone Number ID.
- Access Token permanente recomendado mediante System User.
- Webhook configurado hacia la Function URL/API.
- Suscripcion a eventos de mensajes y llamadas, segun capacidades habilitadas.
- Permisos/capacidades de WhatsApp Calling si se usa llamada desde panel.

### DANAConnect

- Flujo que envia plantillas aprobadas por WhatsApp.
- Paso API Request hacia `/dana/outbound-template` para registrar en WABA Center lo enviado.
- Campos recomendados en payload:
  - `to` o `Telefono`
  - `NombreCliente`
  - `Email`
  - `template.name`
  - `template_id`
  - `source_flow`
  - parametros dinamicos de botones/URL si aplica.

## Variables de Entorno del Lambda

| Variable | Requerida | Uso |
|---|---:|---|
| `WHATSAPP_TOKEN` o `WHATSAPP_ACCESS_TOKEN` | Si | Token de Meta Graph API. |
| `PHONE_NUMBER_ID` o `WHATSAPP_PHONE_NUMBER_ID` | Si | ID del numero de WhatsApp Business. |
| `WHATSAPP_VERIFY_TOKEN` | Si | Token para verificar webhook. |
| `STATE_TABLE_NAME` | Si | Tabla DynamoDB de estado. Por defecto `chat-state`. |
| `CONVERSATIONS_TABLE_NAME` | Si | Tabla DynamoDB de logs. Por defecto `chat-logs`. |
| `CORS_ORIGIN` | No | Origen permitido. Por defecto `*`. |
| `GRAPH_API_VERSION` | No | Version de Graph API. Por defecto `v20.0`. |
| `MAX_MEDIA_BYTES` | No | Tamano maximo para proxy de medios. |
| `MAX_UPLOAD_MEDIA_BYTES` | No | Tamano maximo para subir medios desde panel. |
| `FFMPEG_PATH` | Recomendado | Ruta de FFmpeg para convertir notas de voz. |
| `WHATSAPP_BUSINESS_NUMBER` | Recomendado | Numero del negocio para resolver eventos de llamadas. |

## Hosting del Panel

El panel es estatico y puede publicarse en Amplify, S3, CloudFront o servirse localmente.

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

- `manifest.webmanifest`
- `service-worker.js`
- iconos en `panel/assets/brand/`
- `waba-center.html` como `start_url`

Consideraciones:

- En iPhone/iPad, se instala desde Safari con "Agregar a inicio".
- En Android/Chrome, se instala desde "Instalar app" o "Agregar a pantalla principal".
- En escritorio, Chrome/Edge muestran "Open in app".
- Si se cambia el icono, puede ser necesario desinstalar y volver a instalar la PWA porque los navegadores cachean el icono.

## Formatos de Medios

El backend valida formatos compatibles con WhatsApp Cloud API:

- Imagen: JPEG, PNG, WEBP.
- Audio: MP3, OGG/Opus, AAC, AMR.
- Documento: PDF, TXT, Word, Excel, PowerPoint.
- Video: MP4, 3GPP.
- Sticker: WEBP.

Los CSV no se envian directamente porque Meta no los acepta como documento soportado. Se recomienda convertirlos a XLSX, PDF o TXT.

## Operacion y Mantenimiento

- Revisar CloudWatch cuando Meta responda con errores de envio.
- Mantener actualizado el token permanente de Meta si cambia el System User.
- Confirmar que el webhook de Meta apunte al Lambda correcto despues de cada cambio de entorno.
- Mantener el Layer de FFmpeg asociado al Lambda para notas de voz desde cualquier navegador.
- Desplegar `panel/` completo cuando cambien PWA, iconos, HTML, JS o `config.js`.
- Validar que Amplify no cachee `manifest.webmanifest` y `service-worker.js` durante cambios de PWA.

## Estado del Proyecto

El proyecto se encuentra orientado a operacion manual de WhatsApp Business con WABA Center. Los componentes heredados de Bedrock/Calendar pueden mantenerse como referencia historica, pero no son necesarios para la operacion actual del panel.
