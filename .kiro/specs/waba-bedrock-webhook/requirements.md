# Documento de Requisitos — WABA Bedrock Webhook

## Introducción

Este sistema es un handler serverless (AWS Lambda) que recibe mensajes de la WhatsApp Business API (Cloud API), los procesa a través de un Amazon Bedrock Agent respaldado por una Knowledge Base, y envía las respuestas de vuelta al usuario vía WhatsApp. El sistema soporta conversaciones multi-turno mediante gestión de sesiones en DynamoDB, y lee el system prompt desde un archivo en S3 para permitir modificaciones sin redespliegue. La infraestructura se despliega completamente con CDK en TypeScript, incluyendo la Knowledge Base, el vector store (OpenSearch Serverless), buckets S3, tabla DynamoDB, Lambda y API Gateway. Está diseñado como demo para un evento.

## Glosario

- **Webhook_Handler**: Función AWS Lambda en Python que recibe y procesa las solicitudes HTTP provenientes de la WhatsApp Cloud API.
- **API_Gateway**: Recurso de Amazon API Gateway (REST API) que expone el endpoint HTTP público para recibir webhooks de WhatsApp.
- **Bedrock_Agent**: Agente de Amazon Bedrock que orquesta las consultas a la Knowledge Base y ejecuta action groups.
- **Knowledge_Base**: Base de conocimiento de Amazon Bedrock que indexa documentos PDF almacenados en S3 usando un vector store de OpenSearch Serverless.
- **Vector_Store**: Colección de Amazon OpenSearch Serverless que almacena los embeddings vectoriales de los documentos de la Knowledge Base.
- **Session_Table**: Tabla de Amazon DynamoDB que almacena las sesiones de conversación multi-turno, asociando cada número de teléfono de WhatsApp con un identificador de sesión del Bedrock Agent.
- **System_Prompt_Bucket**: Bucket de Amazon S3 que contiene el archivo de texto con el system prompt del Bedrock Agent.
- **Document_Bucket**: Bucket de Amazon S3 que almacena los documentos PDF que alimentan la Knowledge Base.
- **WhatsApp_Cloud_API**: API REST de Meta (Facebook) para enviar y recibir mensajes de WhatsApp Business.
- **CDK_Stack**: Stack de AWS CDK en TypeScript que define y despliega toda la infraestructura del sistema.
- **Mensaje_Entrante**: Mensaje de texto enviado por un usuario de WhatsApp al número de negocio configurado.
- **Mensaje_Saliente**: Mensaje de texto enviado desde el sistema al usuario de WhatsApp como respuesta.

## Requisitos

### Requisito 1: Verificación del Webhook de WhatsApp

**Historia de Usuario:** Como integrador de la plataforma Meta, quiero que el endpoint verifique correctamente las solicitudes de suscripción de webhook, para que la WhatsApp Cloud API pueda confirmar la validez del endpoint.

#### Criterios de Aceptación

1. WHEN una solicitud GET es recibida con los parámetros `hub.mode=subscribe`, `hub.verify_token` y `hub.challenge`, THE Webhook_Handler SHALL comparar el valor de `hub.verify_token` con la variable de entorno `WHATSAPP_VERIFY_TOKEN`.
2. WHEN el valor de `hub.verify_token` coincide con la variable de entorno `WHATSAPP_VERIFY_TOKEN`, THE Webhook_Handler SHALL responder con código HTTP 200 y el valor de `hub.challenge` como cuerpo de la respuesta.
3. WHEN el valor de `hub.verify_token` no coincide con la variable de entorno `WHATSAPP_VERIFY_TOKEN`, THE Webhook_Handler SHALL responder con código HTTP 403.
4. IF una solicitud GET es recibida sin los parámetros requeridos (`hub.mode`, `hub.verify_token`, `hub.challenge`), THEN THE Webhook_Handler SHALL responder con código HTTP 400.

### Requisito 2: Recepción de Mensajes Entrantes de WhatsApp

**Historia de Usuario:** Como usuario de WhatsApp, quiero enviar mensajes de texto al número de negocio, para que el sistema los procese y me responda.

#### Criterios de Aceptación

1. WHEN una solicitud POST es recibida con un payload válido de la WhatsApp Cloud API, THE Webhook_Handler SHALL extraer los mensajes del array `entry[].changes[].value.messages[]`.
2. WHEN el payload contiene uno o más mensajes de tipo texto, THE Webhook_Handler SHALL procesar cada mensaje de texto individualmente.
3. WHEN el payload contiene mensajes de tipo diferente a texto (imagen, audio, video, documento, ubicación), THE Webhook_Handler SHALL ignorar esos mensajes y responder con código HTTP 200.
4. THE Webhook_Handler SHALL responder con código HTTP 200 a todas las solicitudes POST válidas de WhatsApp dentro de los primeros 5 segundos para evitar reintentos de la plataforma Meta.
5. IF el payload POST no contiene la estructura esperada de la WhatsApp Cloud API, THEN THE Webhook_Handler SHALL responder con código HTTP 200 y registrar un log de advertencia.

### Requisito 3: Gestión de Sesiones de Conversación

**Historia de Usuario:** Como usuario de WhatsApp, quiero que el bot recuerde el contexto de mi conversación, para que pueda tener interacciones multi-turno coherentes.

#### Criterios de Aceptación

1. WHEN un Mensaje_Entrante es recibido de un número de teléfono, THE Webhook_Handler SHALL buscar una sesión existente en la Session_Table usando el número de teléfono como clave de partición.
2. WHEN existe una sesión activa para el número de teléfono, THE Webhook_Handler SHALL reutilizar el identificador de sesión existente al invocar el Bedrock_Agent.
3. WHEN no existe una sesión para el número de teléfono, THE Webhook_Handler SHALL crear un nuevo registro en la Session_Table con un identificador de sesión único y el número de teléfono como clave.
4. THE Session_Table SHALL almacenar como mínimo los campos: número de teléfono (clave de partición), identificador de sesión, y timestamp de última actividad.
5. THE Session_Table SHALL configurar un TTL (Time To Live) para expirar sesiones inactivas automáticamente.

### Requisito 4: Procesamiento de Mensajes con Bedrock Agent

**Historia de Usuario:** Como usuario de WhatsApp, quiero recibir respuestas inteligentes basadas en la base de conocimiento, para que pueda obtener información precisa sobre los temas documentados.

#### Criterios de Aceptación

1. WHEN un mensaje de texto es extraído del payload de WhatsApp, THE Webhook_Handler SHALL invocar el Bedrock_Agent usando el texto del mensaje como entrada y el identificador de sesión correspondiente.
2. THE Webhook_Handler SHALL leer el system prompt desde el archivo ubicado en el System_Prompt_Bucket en la clave especificada por la variable de entorno `SYSTEM_PROMPT_KEY`.
3. WHEN el Bedrock_Agent retorna una respuesta, THE Webhook_Handler SHALL extraer el texto de la respuesta para enviarlo al usuario.
4. IF el Bedrock_Agent retorna un error o no responde dentro de 25 segundos, THEN THE Webhook_Handler SHALL enviar un mensaje predeterminado de error al usuario indicando que no se pudo procesar la solicitud.
5. IF la lectura del system prompt desde S3 falla, THEN THE Webhook_Handler SHALL usar un system prompt predeterminado codificado en la Lambda y registrar un log de error.

### Requisito 5: Envío de Respuestas vía WhatsApp

**Historia de Usuario:** Como usuario de WhatsApp, quiero recibir la respuesta del bot en mi chat de WhatsApp, para que pueda leer la información solicitada.

#### Criterios de Aceptación

1. WHEN el Bedrock_Agent genera una respuesta, THE Webhook_Handler SHALL enviar un mensaje de texto al usuario mediante una solicitud POST a `https://graph.facebook.com/v21.0/{WHATSAPP_PHONE_NUMBER_ID}/messages` con el token de acceso configurado.
2. THE Webhook_Handler SHALL incluir en la solicitud de envío el número de teléfono del remitente original como destinatario, el tipo de mensaje como "text", y el cuerpo del texto con la respuesta del Bedrock_Agent.
3. IF el envío del mensaje a la WhatsApp_Cloud_API falla, THEN THE Webhook_Handler SHALL registrar un log de error con el código de estado HTTP y el cuerpo de la respuesta de error.
4. IF el envío del mensaje falla con un error transitorio (código HTTP 429 o 5xx), THEN THE Webhook_Handler SHALL reintentar el envío una vez después de una espera de 1 segundo.

### Requisito 6: Infraestructura CDK — Lambda y API Gateway

**Historia de Usuario:** Como desarrollador, quiero que toda la infraestructura se despliegue con CDK, para que pueda recrear el entorno de forma reproducible.

#### Criterios de Aceptación

1. THE CDK_Stack SHALL crear una función Lambda con runtime Python 3.12, un timeout de 30 segundos, y 256 MB de memoria.
2. THE CDK_Stack SHALL crear un API_Gateway de tipo REST API con un recurso `/webhook` que acepte métodos GET y POST.
3. THE CDK_Stack SHALL integrar el API_Gateway con la función Lambda mediante una integración de tipo Lambda Proxy.
4. THE CDK_Stack SHALL configurar las variables de entorno de la Lambda: `WHATSAPP_VERIFY_TOKEN`, `WHATSAPP_ACCESS_TOKEN`, `WHATSAPP_PHONE_NUMBER_ID`, `BEDROCK_AGENT_ID`, `BEDROCK_AGENT_ALIAS_ID`, `BEDROCK_MODEL_ARN`, `SYSTEM_PROMPT_BUCKET`, `SYSTEM_PROMPT_KEY`, y `SESSION_TABLE_NAME`.
5. THE CDK_Stack SHALL aceptar los valores sensibles (`WHATSAPP_VERIFY_TOKEN`, `WHATSAPP_ACCESS_TOKEN`, `WHATSAPP_PHONE_NUMBER_ID`) como parámetros del stack de CDK.

### Requisito 7: Infraestructura CDK — Bedrock Knowledge Base y Vector Store

**Historia de Usuario:** Como desarrollador, quiero que la Knowledge Base y el vector store se creen automáticamente con CDK, para que no tenga que configurarlos manualmente.

#### Criterios de Aceptación

1. THE CDK_Stack SHALL crear una colección de OpenSearch Serverless de tipo VECTORSEARCH como Vector_Store.
2. THE CDK_Stack SHALL crear un Document_Bucket de S3 para almacenar los documentos PDF que alimentan la Knowledge Base.
3. THE CDK_Stack SHALL crear una Knowledge_Base de Amazon Bedrock configurada con el Document_Bucket como fuente de datos y el Vector_Store como almacenamiento de embeddings.
4. THE CDK_Stack SHALL crear un Bedrock_Agent configurado con el modelo especificado por la variable `BEDROCK_MODEL_ARN` (valor predeterminado: `arn:aws:bedrock:us-east-1::foundation-model/anthropic.claude-sonnet-4-6`) y asociado a la Knowledge_Base.
5. THE CDK_Stack SHALL crear un alias del Bedrock_Agent para permitir la invocación desde la Lambda.

### Requisito 8: Infraestructura CDK — DynamoDB y S3

**Historia de Usuario:** Como desarrollador, quiero que la tabla de sesiones y los buckets se creen con CDK, para que la infraestructura de almacenamiento esté completamente automatizada.

#### Criterios de Aceptación

1. THE CDK_Stack SHALL crear una Session_Table en DynamoDB con el número de teléfono como clave de partición (tipo String) y modo de facturación PAY_PER_REQUEST.
2. THE CDK_Stack SHALL habilitar TTL en la Session_Table usando un atributo denominado `ttl`.
3. THE CDK_Stack SHALL crear un System_Prompt_Bucket de S3 para almacenar el archivo del system prompt.
4. THE CDK_Stack SHALL configurar los permisos IAM de la Lambda para: leer de la Session_Table, escribir en la Session_Table, leer del System_Prompt_Bucket, e invocar el Bedrock_Agent.
5. THE CDK_Stack SHALL configurar las políticas de acceso de OpenSearch Serverless para permitir que la Knowledge_Base indexe y consulte el Vector_Store.

### Requisito 9: Infraestructura CDK — Región y Outputs

**Historia de Usuario:** Como desarrollador, quiero que el stack se despliegue en us-east-1 y me muestre las URLs y IDs relevantes, para que pueda configurar la integración con Meta fácilmente.

#### Criterios de Aceptación

1. THE CDK_Stack SHALL configurar el entorno de despliegue en la región `us-east-1`.
2. THE CDK_Stack SHALL exportar como CloudFormation Output la URL completa del endpoint del webhook (URL del API_Gateway + `/webhook`).
3. THE CDK_Stack SHALL exportar como CloudFormation Output el ID del Bedrock_Agent y el ID del alias del Bedrock_Agent.
4. THE CDK_Stack SHALL exportar como CloudFormation Output el nombre del Document_Bucket para facilitar la carga de documentos PDF.

### Requisito 10: Manejo de Errores y Logging

**Historia de Usuario:** Como desarrollador, quiero que el sistema registre logs detallados y maneje errores de forma robusta, para que pueda diagnosticar problemas durante la demo.

#### Criterios de Aceptación

1. THE Webhook_Handler SHALL registrar en CloudWatch Logs cada mensaje entrante recibido, incluyendo el número de teléfono del remitente y el tipo de mensaje (sin incluir el contenido del mensaje por privacidad).
2. THE Webhook_Handler SHALL registrar en CloudWatch Logs cada invocación al Bedrock_Agent, incluyendo el identificador de sesión y el tiempo de respuesta en milisegundos.
3. THE Webhook_Handler SHALL registrar en CloudWatch Logs cada envío de mensaje a la WhatsApp_Cloud_API, incluyendo el código de estado HTTP de la respuesta.
4. IF una excepción no controlada ocurre durante el procesamiento, THEN THE Webhook_Handler SHALL registrar el traceback completo en CloudWatch Logs y responder con código HTTP 200 para evitar reintentos de la plataforma Meta.
