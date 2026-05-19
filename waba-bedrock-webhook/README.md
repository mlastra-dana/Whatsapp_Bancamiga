# WABA Bedrock Webhook — NEXT GEN Event Bot

Chatbot de WhatsApp para el evento NEXT GEN de DANAconnect. Integra Amazon Bedrock Agent con Knowledge Base, Google Calendar para agendamiento, visión computacional con Amazon Nova Lite, y un panel web para monitoreo en tiempo real.

## Arquitectura

```
WhatsApp Cloud API → API Gateway → Webhook Lambda → Bedrock Agent (Knowledge Base + Calendar Action Group)
                                       ↓
                                  ┌────┴────┐
                                  │ Routing │
                                  └────┬────┘
                          ┌────────────┼────────────────┐
                          ↓            ↓                ↓
                   Bedrock Agent   Vision Analyzer   Calendar Lambda
                   (KB + Calendar)  (Nova Lite)      (Google Calendar API)
                          ↓            ↓                ↓
                   WhatsApp Response  Image Description  Event Creation
```

## Features

- **Knowledge Base**: Responde preguntas sobre el evento NEXT GEN (agenda, speakers, horarios, ubicación)
- **Agendamiento**: Permite agendar reuniones de seguimiento en Google Calendar con recopilación de datos del contacto
- **Visión Computacional**: Analiza imágenes enviadas por WhatsApp usando Amazon Nova Lite
- **Panel de Citas**: Interfaz web para ver las reuniones agendadas
- **Panel de Conversaciones**: Interfaz web para ver todas las interacciones del bot con identificación de contactos
- **Envío de mensajes**: Permite enviar mensajes WhatsApp directamente desde el panel
- **Notificaciones**: Notificaciones del navegador + Slack cuando llegan mensajes nuevos
- **Registro completo**: Guarda todas las conversaciones en DynamoDB para análisis posterior

## Estructura del Proyecto

```
waba-bedrock-webhook/
├── lambda/                      # Webhook Lambda (handler principal)
│   ├── handler.py               # Entry point — routing, visión, Bedrock Agent
│   ├── bedrock_agent.py         # Cliente de Bedrock Agent
│   ├── whatsapp.py              # Cliente WhatsApp Cloud API (envío + media)
│   ├── session_manager.py       # Gestión de sesiones DynamoDB (con modo visión)
│   ├── vision_analyzer.py       # Análisis de imágenes con Amazon Nova Lite
│   ├── prompt_reader.py         # Lectura del system prompt desde S3
│   └── requirements.txt
├── lambda-calendar/             # Calendar Action Group Lambda
│   ├── handler.py               # Routing de acciones del Action Group
│   ├── availability.py          # Consulta de disponibilidad (FreeBusy API)
│   ├── event_creator.py         # Creación de eventos en Google Calendar
│   ├── google_auth_helper.py    # Autenticación con cuenta de servicio
│   ├── validators.py            # Validación de fechas, horas, títulos
│   └── requirements.txt         # + dependencias instaladas para Linux
├── lambda-appointments/         # API para leer citas desde DynamoDB
│   └── handler.py
├── lambda-conversations/        # API para leer conversaciones desde DynamoDB
│   └── handler.py
├── lambda-send-message/         # API para enviar mensajes WhatsApp desde el panel
│   └── handler.py
├── panel/                       # Interfaces web (HTML standalone)
│   ├── citas.html               # Panel de citas agendadas
│   ├── conversaciones.html      # Panel de conversaciones con envío de mensajes
│   ├── attendees.json           # Lista de asistentes al evento (parte 1)
│   └── attendees2.json          # Lista de asistentes al evento (parte 2)
├── infra/                       # CDK Infrastructure
│   ├── lib/waba-bedrock-stack.ts  # Stack completo
│   ├── package.json
│   └── tsconfig.json
└── tests/                       # Tests unitarios y property-based
    └── unit/
```

## Despliegue

### Prerrequisitos

- AWS CLI configurado
- Node.js 18+
- Python 3.12
- Google Cloud Service Account con delegación de dominio (para Calendar)

### Variables de entorno / Parámetros CDK

| Parámetro | Descripción |
|---|---|
| `WhatsAppVerifyToken` | Token para verificación del webhook |
| `WhatsAppAccessToken` | Token de acceso de Meta |
| `WhatsAppPhoneNumberId` | ID del número de WhatsApp Business |
| `TeamCalendars` | Emails de calendarios a consultar (separados por coma) |
| `ImpersonateEmail` | Email para delegación de dominio de Google |
| `Timezone` | Zona horaria (ej: `America/New_York`) |

### Deploy

```bash
cd infra
npm install
npx cdk deploy \
  --parameters TeamCalendars="email1@domain.com,email2@domain.com" \
  --parameters ImpersonateEmail="admin@domain.com" \
  --parameters Timezone="America/New_York"
```

Los parámetros de WhatsApp se mantienen del deploy anterior si ya están configurados.

### Configurar Google Calendar

1. Crear Service Account en Google Cloud Console
2. Habilitar Google Calendar API
3. Configurar delegación de dominio en admin.google.com
4. Subir credenciales JSON a Secrets Manager:
```bash
aws secretsmanager put-secret-value \
  --secret-id <CalendarCredentialsSecretArn> \
  --secret-string file://service-account-key.json
```

## APIs Disponibles

| Endpoint | Método | Descripción |
|---|---|---|
| `/webhook` | GET/POST | Webhook de WhatsApp |
| `/appointments` | GET | Lista de citas agendadas |
| `/conversations` | GET | Todas las conversaciones (filtrable por `?phone=`) |
| `/send-message` | POST | Enviar mensaje WhatsApp (`{"phone":"...", "message":"..."}`) |

## Panel Web

Los archivos HTML en `panel/` son standalone y se pueden hostear en cualquier servidor estático (Amplify, S3, localhost). Se conectan a los endpoints del API Gateway.

Para probar localmente:
```bash
cd panel
python3 -m http.server 8080
# Abrir http://localhost:8080/conversaciones.html
```

## Intenciones del Bot

| Intención | Trigger | Acción |
|---|---|---|
| Preguntas del evento | Cualquier pregunta informativa | Consulta Knowledge Base |
| Agendar reunión | "agendar", "programar", "reservar" | Recopila datos → Calendar API |
| Visión computacional | "visión computacional", "analizar imagen", "describir foto" | Pide imagen → Nova Lite → descripción |
