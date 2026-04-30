# WABA + Bedrock Knowledge Base Webhook

Lambda que recibe webhooks de WhatsApp Business API (Cloud API) y responde usando una Knowledge Base de Amazon Bedrock.

## Arquitectura

```
WhatsApp Cloud API → API Gateway → Lambda → Bedrock Knowledge Base
                                      ↓
                                WhatsApp Cloud API (respuesta)
```

## Configuración

### Variables de entorno requeridas

| Variable | Descripción |
|---|---|
| `WHATSAPP_VERIFY_TOKEN` | Token para verificación del webhook (lo defines tú) |
| `WHATSAPP_ACCESS_TOKEN` | Token de acceso de la app de Meta |
| `WHATSAPP_PHONE_NUMBER_ID` | ID del número de teléfono de WhatsApp Business |
| `BEDROCK_KNOWLEDGE_BASE_ID` | ID de la Knowledge Base de Bedrock |
| `BEDROCK_MODEL_ARN` | ARN del modelo de Bedrock (ej: `arn:aws:bedrock:us-east-1::foundation-model/anthropic.claude-3-haiku-20240307-v1:0`) |

### Despliegue con CDK

```bash
cd infra
npm install
npx cdk deploy --parameters WhatsAppVerifyToken=<tu-token> \
               --parameters WhatsAppAccessToken=<tu-access-token> \
               --parameters WhatsAppPhoneNumberId=<tu-phone-id> \
               --parameters BedrockKnowledgeBaseId=<tu-kb-id> \
               --parameters BedrockModelArn=<model-arn>
```

### Configuración en Meta

1. En el App Dashboard de Meta, ve a **WhatsApp > Configuration**
2. En **Webhook**, configura la Callback URL con la URL del API Gateway
3. Usa el mismo `WHATSAPP_VERIFY_TOKEN` como Verify Token
4. Suscríbete al campo `messages`

## Estructura

```
waba-bedrock-webhook/
├── lambda/
│   ├── handler.py          # Handler principal de la Lambda
│   ├── whatsapp.py         # Cliente de WhatsApp Cloud API
│   ├── bedrock_kb.py       # Cliente de Bedrock Knowledge Base
│   └── requirements.txt    # Dependencias Python
├── infra/
│   ├── bin/app.ts          # Entry point CDK
│   ├── lib/stack.ts        # Stack CDK
│   ├── package.json
│   └── tsconfig.json
└── README.md
```
