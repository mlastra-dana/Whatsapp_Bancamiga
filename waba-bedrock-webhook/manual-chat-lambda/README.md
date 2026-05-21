# Manual Chat Lambda

Backend independiente para el panel de conversaciones manuales.

Este Lambda reemplaza el flujo del bot para este proyecto nuevo:

- recibe el webhook de WhatsApp y guarda mensajes entrantes;
- no llama a Bedrock;
- no envía respuestas automáticas;
- expone `GET /conversations` para el panel;
- expone `POST /send-message` para respuestas manuales desde el panel.

## Variables de entorno

| Variable | Uso |
|---|---|
| `CONVERSATIONS_TABLE_NAME` | Tabla DynamoDB con partition key `phone_number` y sort key `timestamp`. |
| `WHATSAPP_PHONE_NUMBER_ID` | Phone Number ID de WhatsApp Cloud API. |
| `WHATSAPP_ACCESS_TOKEN` | Token de Meta para enviar mensajes. |
| `WHATSAPP_VERIFY_TOKEN` | Token para verificar el webhook de Meta. |
| `CORS_ORIGIN` | Opcional. Origen permitido para el panel, o `*`. |

## Rutas

| Ruta | Metodo | Uso |
|---|---|---|
| `/` o `/webhook` | `GET` | Verificacion del webhook de WhatsApp. |
| `/` o `/webhook` | `POST` | Recibe mensajes de WhatsApp y los guarda. |
| `/conversations` | `GET` | Lista todas las conversaciones. |
| `/conversations?phone=NUMBER` | `GET` | Lista una conversacion. |
| `/send-message` | `POST` | Envia mensaje manual: `{"phone":"...","message":"..."}`. |

## Nota

La Function URL debe permitir CORS y estar configurada como webhook en Meta. El panel de Amplify debe usar esa URL en la variable `VITE_API_URL`.

## Despliegue opcional con SAM

Desde esta carpeta:

```bash
sam deploy --guided
```

El output `ManualChatFunctionUrl` es el valor que va en Amplify como `VITE_API_URL`.
