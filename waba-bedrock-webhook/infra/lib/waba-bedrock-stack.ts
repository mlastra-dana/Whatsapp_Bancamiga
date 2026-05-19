import * as path from 'path';
import * as cdk from 'aws-cdk-lib';
import * as apigateway from 'aws-cdk-lib/aws-apigateway';
import * as bedrock from 'aws-cdk-lib/aws-bedrock';
import * as dynamodb from 'aws-cdk-lib/aws-dynamodb';
import * as iam from 'aws-cdk-lib/aws-iam';
import * as lambda from 'aws-cdk-lib/aws-lambda';
import * as opensearchserverless from 'aws-cdk-lib/aws-opensearchserverless';
import * as s3 from 'aws-cdk-lib/aws-s3';
import * as secretsmanager from 'aws-cdk-lib/aws-secretsmanager';
import { Construct } from 'constructs';

export class WabaBedrockStack extends cdk.Stack {
  /** DynamoDB table mapping phone numbers to Bedrock Agent session IDs */
  public readonly sessionTable: dynamodb.Table;
  /** S3 bucket storing the system prompt file */
  public readonly systemPromptBucket: s3.Bucket;
  /** S3 bucket storing PDF documents for the Knowledge Base */
  public readonly documentBucket: s3.Bucket;
  /** Bedrock Knowledge Base backed by OpenSearch Serverless */
  public readonly knowledgeBase: bedrock.CfnKnowledgeBase;
  /** Bedrock Agent configured with the Knowledge Base */
  public readonly bedrockAgent: bedrock.CfnAgent;
  /** Bedrock Agent Alias for stable invocation from Lambda */
  public readonly bedrockAgentAlias: bedrock.CfnAgentAlias;
  /** Lambda function that handles WhatsApp webhook requests */
  public readonly webhookHandler: lambda.Function;
  /** API Gateway REST API exposing the /webhook endpoint */
  public readonly api: apigateway.RestApi;
  /** Lambda function that handles Calendar Action Group requests */
  public readonly calendarHandler: lambda.Function;
  /** Secrets Manager secret storing Google Service Account credentials */
  public readonly calendarCredentialsSecret: secretsmanager.Secret;
  /** Bedrock Agent Action Group for calendar operations */
  public readonly calendarActionGroup: bedrock.CfnAgent.AgentActionGroupProperty;

  constructor(scope: Construct, id: string, props?: cdk.StackProps) {
    super(scope, id, props);

    // Stack parameters for sensitive WhatsApp configuration values
    const whatsappVerifyToken = new cdk.CfnParameter(this, 'WhatsAppVerifyToken', {
      type: 'String',
      description: 'Token for WhatsApp webhook verification',
      noEcho: true,
    });

    const whatsappAccessToken = new cdk.CfnParameter(this, 'WhatsAppAccessToken', {
      type: 'String',
      description: 'Meta access token for WhatsApp Cloud API',
      noEcho: true,
    });

    const whatsappPhoneNumberId = new cdk.CfnParameter(this, 'WhatsAppPhoneNumberId', {
      type: 'String',
      description: 'WhatsApp Business phone number ID',
    });

    // Stack parameters for Calendar Action Group configuration
    const teamCalendars = new cdk.CfnParameter(this, 'TeamCalendars', {
      type: 'String',
      description: 'Comma-separated list of team member email addresses for calendar availability',
    });

    const timezone = new cdk.CfnParameter(this, 'Timezone', {
      type: 'String',
      default: 'America/Mexico_City',
      description: 'Timezone for business hours and slot display',
    });

    const impersonateEmail = new cdk.CfnParameter(this, 'ImpersonateEmail', {
      type: 'String',
      description: 'Email address for Google Workspace domain-wide delegation',
    });

    // ---------------------------------------------------------------
    // DynamoDB Session Table (Requirement 8.1, 8.2)
    // ---------------------------------------------------------------
    this.sessionTable = new dynamodb.Table(this, 'SessionTable', {
      partitionKey: { name: 'phone_number', type: dynamodb.AttributeType.STRING },
      billingMode: dynamodb.BillingMode.PAY_PER_REQUEST,
      timeToLiveAttribute: 'ttl',
      removalPolicy: cdk.RemovalPolicy.DESTROY,
    });

    // ---------------------------------------------------------------
    // S3 Buckets (Requirement 8.3)
    // ---------------------------------------------------------------
    this.systemPromptBucket = new s3.Bucket(this, 'SystemPromptBucket', {
      removalPolicy: cdk.RemovalPolicy.DESTROY,
      autoDeleteObjects: true,
    });

    this.documentBucket = new s3.Bucket(this, 'DocumentBucket', {
      removalPolicy: cdk.RemovalPolicy.DESTROY,
      autoDeleteObjects: true,
    });

    // ---------------------------------------------------------------
    // OpenSearch Serverless Collection — Vector Store (Requirement 7.1, 8.5)
    // ---------------------------------------------------------------

    // Encryption policy (required before creating the collection)
    const ossEncryptionPolicy = new opensearchserverless.CfnSecurityPolicy(this, 'OssEncryptionPolicy', {
      name: 'waba-kb-enc-policy',
      type: 'encryption',
      policy: JSON.stringify({
        Rules: [{ ResourceType: 'collection', Resource: ['collection/waba-kb-vectors'] }],
        AWSOwnedKey: true,
      }),
    });

    // Network policy — allow public access so Bedrock can reach the collection
    const ossNetworkPolicy = new opensearchserverless.CfnSecurityPolicy(this, 'OssNetworkPolicy', {
      name: 'waba-kb-net-policy',
      type: 'network',
      policy: JSON.stringify([
        {
          Rules: [
            { ResourceType: 'collection', Resource: ['collection/waba-kb-vectors'] },
            { ResourceType: 'dashboard', Resource: ['collection/waba-kb-vectors'] },
          ],
          AllowFromPublic: true,
        },
      ]),
    });

    // OpenSearch Serverless collection of type VECTORSEARCH
    const ossCollection = new opensearchserverless.CfnCollection(this, 'OssVectorCollection', {
      name: 'waba-kb-vectors',
      type: 'VECTORSEARCH',
      standbyReplicas: 'DISABLED',
      description: 'Vector store for WABA Bedrock Knowledge Base',
    });

    // Ensure policies are created before the collection
    ossCollection.addDependency(ossEncryptionPolicy);
    ossCollection.addDependency(ossNetworkPolicy);

    // ---------------------------------------------------------------
    // Knowledge Base IAM Role (Requirement 7.3, 8.5)
    // ---------------------------------------------------------------
    const kbRole = new iam.Role(this, 'KnowledgeBaseRole', {
      assumedBy: new iam.ServicePrincipal('bedrock.amazonaws.com', {
        conditions: {
          StringEquals: {
            'aws:SourceAccount': this.account,
          },
        },
      }),
      description: 'IAM role for Bedrock Knowledge Base to access OpenSearch Serverless and S3',
    });

    // Allow the KB role to invoke the embedding model
    kbRole.addToPolicy(new iam.PolicyStatement({
      actions: ['bedrock:InvokeModel'],
      resources: [
        `arn:aws:bedrock:${this.region}::foundation-model/amazon.titan-embed-text-v2:0`,
      ],
    }));

    // Allow the KB role to access the OpenSearch Serverless collection via API
    kbRole.addToPolicy(new iam.PolicyStatement({
      actions: ['aoss:APIAccessAll'],
      resources: [ossCollection.attrArn],
    }));

    // Allow the KB role to read from the document bucket
    this.documentBucket.grantRead(kbRole);

    // ---------------------------------------------------------------
    // Custom Resource — Create Vector Index in OpenSearch Serverless
    // ---------------------------------------------------------------
    const indexCreatorFn = new lambda.Function(this, 'OssIndexCreator', {
      runtime: lambda.Runtime.PYTHON_3_12,
      handler: 'index.handler',
      code: lambda.Code.fromAsset(path.join(__dirname, 'oss-index-creator')),
      timeout: cdk.Duration.minutes(5),
      memorySize: 256,
      description: 'Custom Resource: creates vector index in OpenSearch Serverless',
    });

    // Grant the index creator Lambda access to the OpenSearch Serverless collection
    indexCreatorFn.addToRolePolicy(new iam.PolicyStatement({
      actions: ['aoss:APIAccessAll'],
      resources: [ossCollection.attrArn],
    }));

    // Data access policy — grant BOTH the KB role AND the index creator Lambda
    const ossDataAccessPolicy = new opensearchserverless.CfnAccessPolicy(this, 'OssDataAccessPolicy', {
      name: 'waba-kb-data-access',
      type: 'data',
      policy: JSON.stringify([
        {
          Rules: [
            {
              ResourceType: 'index',
              Resource: ['index/waba-kb-vectors/*'],
              Permission: [
                'aoss:CreateIndex',
                'aoss:UpdateIndex',
                'aoss:DescribeIndex',
                'aoss:ReadDocument',
                'aoss:WriteDocument',
              ],
            },
            {
              ResourceType: 'collection',
              Resource: ['collection/waba-kb-vectors'],
              Permission: [
                'aoss:CreateCollectionItems',
                'aoss:UpdateCollectionItems',
                'aoss:DescribeCollectionItems',
              ],
            },
          ],
          Principal: [kbRole.roleArn, indexCreatorFn.role!.roleArn],
        },
      ]),
    });

    // Custom Resource that triggers the index creation
    const vectorIndexName = 'bedrock-knowledge-base-default-index';

    const indexCreatorCr = new cdk.CustomResource(this, 'OssIndexCreatorCR', {
      serviceToken: indexCreatorFn.functionArn,
      properties: {
        CollectionEndpoint: ossCollection.attrCollectionEndpoint,
        IndexName: vectorIndexName,
      },
    });

    // Ensure the collection, access policy, and index creator role exist first
    indexCreatorCr.node.addDependency(ossCollection);
    indexCreatorCr.node.addDependency(ossDataAccessPolicy);

    // ---------------------------------------------------------------
    // Bedrock Knowledge Base (Requirement 7.2, 7.3)
    // ---------------------------------------------------------------
    this.knowledgeBase = new bedrock.CfnKnowledgeBase(this, 'KnowledgeBase', {
      name: 'waba-knowledge-base',
      description: 'Knowledge Base for WABA Bedrock Webhook — indexes PDF documents from S3',
      roleArn: kbRole.roleArn,
      knowledgeBaseConfiguration: {
        type: 'VECTOR',
        vectorKnowledgeBaseConfiguration: {
          embeddingModelArn: `arn:aws:bedrock:${this.region}::foundation-model/amazon.titan-embed-text-v2:0`,
        },
      },
      storageConfiguration: {
        type: 'OPENSEARCH_SERVERLESS',
        opensearchServerlessConfiguration: {
          collectionArn: ossCollection.attrArn,
          vectorIndexName,
          fieldMapping: {
            vectorField: 'bedrock-knowledge-base-default-vector',
            textField: 'AMAZON_BEDROCK_TEXT_CHUNK',
            metadataField: 'AMAZON_BEDROCK_METADATA',
          },
        },
      },
    });

    // Ensure the collection, access policy, and vector index exist before the KB
    this.knowledgeBase.addDependency(ossCollection);
    this.knowledgeBase.addDependency(ossDataAccessPolicy);
    // Add explicit CloudFormation DependsOn for the Custom Resource
    const crDefaultChild = indexCreatorCr.node.defaultChild as cdk.CfnResource;
    this.knowledgeBase.addDependency(crDefaultChild);

    // ---------------------------------------------------------------
    // Knowledge Base S3 Data Source (Requirement 7.2)
    // ---------------------------------------------------------------
    const kbDataSource = new bedrock.CfnDataSource(this, 'KnowledgeBaseDataSource', {
      name: 'waba-document-bucket-source',
      knowledgeBaseId: this.knowledgeBase.attrKnowledgeBaseId,
      dataSourceConfiguration: {
        type: 'S3',
        s3Configuration: {
          bucketArn: this.documentBucket.bucketArn,
        },
      },
    });

    // ---------------------------------------------------------------
    // Bedrock Agent (Requirement 7.4)
    // ---------------------------------------------------------------
    const bedrockModelArn = 'us.anthropic.claude-sonnet-4-6';

    // IAM role for the Bedrock Agent to invoke the foundation model and use the KB
    const agentRole = new iam.Role(this, 'BedrockAgentRole', {
      assumedBy: new iam.ServicePrincipal('bedrock.amazonaws.com', {
        conditions: {
          StringEquals: {
            'aws:SourceAccount': this.account,
          },
        },
      }),
      description: 'IAM role for Bedrock Agent to invoke the foundation model and query the Knowledge Base',
    });

    // Allow the agent role to invoke the foundation model (via inference profile)
    agentRole.addToPolicy(new iam.PolicyStatement({
      actions: ['bedrock:InvokeModel'],
      resources: [
        `arn:aws:bedrock:${this.region}:${this.account}:inference-profile/${bedrockModelArn}`,
        `arn:aws:bedrock:*::foundation-model/anthropic.claude-sonnet-4-6`,
      ],
    }));

    // Allow the agent role to retrieve from the Knowledge Base
    agentRole.addToPolicy(new iam.PolicyStatement({
      actions: ['bedrock:Retrieve'],
      resources: [this.knowledgeBase.attrKnowledgeBaseArn],
    }));

    this.bedrockAgent = new bedrock.CfnAgent(this, 'BedrockAgent', {
      agentName: 'waba-bedrock-agent',
      agentResourceRoleArn: agentRole.roleArn,
      foundationModel: bedrockModelArn,
      description: 'Bedrock Agent for WABA Webhook — answers user questions using the Knowledge Base',
      instruction: `Eres el asistente virtual de DANAconnect para NEXT GEN. Tu función principal es responder preguntas de los usuarios basándote en la información disponible en la base de conocimiento.

PRIORIDAD DE RESPUESTA:
1. PRIMERO intenta responder usando la base de conocimiento (información del evento, horarios, ubicación, speakers, agenda, etc.)
2. SOLO usa el calendario cuando el usuario EXPLÍCITAMENTE quiera agendar una reunión de seguimiento

CUÁNDO USAR EL CALENDARIO (Action Group):
- SOLO cuando el usuario diga explícitamente que quiere "agendar", "programar", "reservar" una reunión o cita
- Palabras clave que activan el calendario: "agendar", "agendar reunión", "quiero una cita", "reservar horario", "programar reunión"
- NO uses el calendario para preguntas sobre horarios del evento, agenda, speakers, o información general

CUÁNDO USAR LA BASE DE CONOCIMIENTO:
- Preguntas sobre el evento NEXT GEN (horarios, agenda, ubicación, speakers, temas, patrocinadores)
- Cualquier pregunta informativa que NO sea agendar una reunión
- NO hay información de productos o servicios de DANAconnect en la base de conocimiento, solo información del evento NEXT GEN

FECHA ACTUAL: ${new Date().toISOString().split('T')[0]}

REGLAS PARA AGENDAR REUNIONES (solo cuando el usuario lo pida explícitamente):

1. ANTES de agendar, SIEMPRE pregunta estos datos al usuario (uno por uno si es necesario):
   - Nombre completo
   - Email
   - Empresa
   - Cargo
   - Motivo de la reunión (breve descripción de qué quiere tratar)
   El teléfono ya lo tienes del WhatsApp, no lo preguntes.

2. Para las fechas:
   - Interpreta cualquier formato: "14 de enero", "enero 14", "14/01", "el martes", "mañana", "próximo lunes"
   - Si no especifica año, asume 2026
   - Si dice un día de la semana, calcula la fecha del próximo día a partir de HOY (${new Date().toISOString().split('T')[0]})
   - Nunca pidas un formato específico de fecha
   - Convierte a YYYY-MM-DD para el Action Group

3. Para la hora:
   - Conviértela a ISO 8601 con timezone America/New_York (-04:00 en verano, -05:00 en invierno)
   - Si dice "a las 11", usa la fecha calculada + "T11:00:00-04:00"

4. Para el título del evento:
   - Usa el formato: "Reunión - [Nombre] ([Empresa])"
   - Ejemplo: "Reunión - Juan Pérez (Acme Corp)"

5. Flujo completo:
   a. Usuario expresa intención de agendar
   b. Pregunta datos del contacto (nombre, email, empresa, cargo, motivo)
   c. Consulta disponibilidad para la fecha solicitada
   d. Presenta los horarios disponibles
   e. Usuario elige horario
   f. ANTES DE AGENDAR: Muestra un resumen completo con TODOS los datos y pregunta "¿Confirmo la cita?"
   g. Solo si el usuario confirma explícitamente (sí, confirmo, dale, ok), crea el evento

6. NUNCA agendes sin tener nombre, email, empresa, cargo y motivo del usuario.

7. NUNCA agendes sin mostrar el resumen y recibir confirmación explícita del usuario.

8. El teléfono del usuario está disponible en los session attributes como "user_phone". Úsalo como contact_phone al crear el evento. NO le preguntes el teléfono al usuario.

9. El resumen antes de confirmar debe verse así:
   📋 *Resumen de la cita:*
   - Nombre: [nombre]
   - Email: [email]
   - Empresa: [empresa]
   - Cargo: [cargo]
   - Teléfono: [user_phone de session attributes]
   - Fecha: [fecha]
   - Hora: [hora]
   - Motivo: [motivo]
   
   ¿Confirmo la cita?`,
      autoPrepare: true,
      idleSessionTtlInSeconds: 1800,
      knowledgeBases: [
        {
          knowledgeBaseId: this.knowledgeBase.attrKnowledgeBaseId,
          description: 'Knowledge Base with indexed PDF documents for answering user questions',
          knowledgeBaseState: 'ENABLED',
        },
      ],
    });

    // ---------------------------------------------------------------
    // Bedrock Agent Alias (Requirement 7.5)
    // ---------------------------------------------------------------
    this.bedrockAgentAlias = new bedrock.CfnAgentAlias(this, 'BedrockAgentAlias', {
      agentAliasName: 'live',
      agentId: this.bedrockAgent.attrAgentId,
      description: `Live alias - updated ${new Date().toISOString()}`,
    });

    // ---------------------------------------------------------------
    // Lambda Function — Webhook Handler (Requirement 6.1, 6.4, 6.5)
    // ---------------------------------------------------------------
    this.webhookHandler = new lambda.Function(this, 'WebhookHandler', {
      runtime: lambda.Runtime.PYTHON_3_12,
      handler: 'handler.lambda_handler',
      code: lambda.Code.fromAsset(path.join(__dirname, '../../lambda')),
      timeout: cdk.Duration.seconds(30),
      memorySize: 256,
      description: 'WhatsApp webhook handler integrated with Bedrock Agent',
      environment: {
        WHATSAPP_VERIFY_TOKEN: whatsappVerifyToken.valueAsString,
        WHATSAPP_ACCESS_TOKEN: whatsappAccessToken.valueAsString,
        WHATSAPP_PHONE_NUMBER_ID: whatsappPhoneNumberId.valueAsString,
        BEDROCK_AGENT_ID: this.bedrockAgent.attrAgentId,
        BEDROCK_AGENT_ALIAS_ID: this.bedrockAgentAlias.attrAgentAliasId,
        BEDROCK_MODEL_ARN: bedrockModelArn,
        SYSTEM_PROMPT_BUCKET: this.systemPromptBucket.bucketName,
        SYSTEM_PROMPT_KEY: 'system_prompt.txt',
        SESSION_TABLE_NAME: this.sessionTable.tableName,
        SLACK_WEBHOOK_URL: 'https://hooks.slack.com/triggers/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',
      },
    });

    // Grant Lambda read/write access to the DynamoDB Session Table (Requirement 8.4)
    this.sessionTable.grantReadWriteData(this.webhookHandler);

    // Grant Lambda read access to the System Prompt Bucket (Requirement 8.4)
    this.systemPromptBucket.grantRead(this.webhookHandler);

    // Grant Lambda permission to invoke the Bedrock Agent (Requirement 8.4)
    this.webhookHandler.addToRolePolicy(new iam.PolicyStatement({
      actions: ['bedrock:InvokeAgent'],
      resources: [
        `arn:aws:bedrock:${this.region}:${this.account}:agent-alias/${this.bedrockAgent.attrAgentId}/${this.bedrockAgentAlias.attrAgentAliasId}`,
      ],
    }));

    // Grant Lambda permission to invoke the multimodal model for vision analysis
    this.webhookHandler.addToRolePolicy(new iam.PolicyStatement({
      actions: ['bedrock:InvokeModel'],
      resources: [
        `arn:aws:bedrock:${this.region}:${this.account}:inference-profile/${bedrockModelArn}`,
        `arn:aws:bedrock:*::foundation-model/anthropic.claude-sonnet-4-6`,
        `arn:aws:bedrock:${this.region}::foundation-model/amazon.nova-lite-v1:0`,
      ],
    }));

    // Grant Lambda AWS Marketplace permissions required for Bedrock model access
    this.webhookHandler.addToRolePolicy(new iam.PolicyStatement({
      actions: [
        'aws-marketplace:ViewSubscriptions',
        'aws-marketplace:Subscribe',
      ],
      resources: ['*'],
    }));

    // ---------------------------------------------------------------
    // API Gateway REST API (Requirement 6.2, 6.3)
    // ---------------------------------------------------------------
    this.api = new apigateway.RestApi(this, 'WebhookApi', {
      restApiName: 'WABA Webhook API',
      description: 'REST API for WhatsApp Business webhook integration',
    });

    const webhookResource = this.api.root.addResource('webhook');
    const lambdaIntegration = new apigateway.LambdaIntegration(this.webhookHandler);

    webhookResource.addMethod('GET', lambdaIntegration);
    webhookResource.addMethod('POST', lambdaIntegration);

    // ---------------------------------------------------------------
    // Conversations Table — stores all chat interactions for analytics
    // ---------------------------------------------------------------
    const conversationsTable = new dynamodb.Table(this, 'ConversationsTable', {
      partitionKey: { name: 'phone_number', type: dynamodb.AttributeType.STRING },
      sortKey: { name: 'timestamp', type: dynamodb.AttributeType.STRING },
      billingMode: dynamodb.BillingMode.PAY_PER_REQUEST,
      removalPolicy: cdk.RemovalPolicy.DESTROY,
    });

    // Grant webhook handler write access to conversations table
    conversationsTable.grantWriteData(this.webhookHandler);
    this.webhookHandler.addEnvironment('CONVERSATIONS_TABLE_NAME', conversationsTable.tableName);

    // ---------------------------------------------------------------
    // Calendar Action Group Infrastructure (Requirement 5.1, 5.2, 5.3, 5.4, 5.5)
    // ---------------------------------------------------------------

    // Secrets Manager secret for Google Service Account credentials JSON
    this.calendarCredentialsSecret = new secretsmanager.Secret(this, 'CalendarCredentialsSecret', {
      description: 'Google Service Account JSON credentials for Calendar API domain-wide delegation',
    });

    // Calendar Lambda function — handles Bedrock Agent Action Group requests
    this.calendarHandler = new lambda.Function(this, 'CalendarHandler', {
      runtime: lambda.Runtime.PYTHON_3_12,
      handler: 'handler.lambda_handler',
      code: lambda.Code.fromAsset(path.join(__dirname, '../../lambda-calendar')),
      timeout: cdk.Duration.seconds(30),
      memorySize: 256,
      description: 'Calendar Action Group handler for Bedrock Agent — check availability and create events',
      environment: {
        CREDENTIALS_SECRET_ARN: this.calendarCredentialsSecret.secretArn,
        TEAM_CALENDARS: teamCalendars.valueAsString,
        TIMEZONE: timezone.valueAsString,
        IMPERSONATE_EMAIL: impersonateEmail.valueAsString,
      },
    });

    // Grant Calendar Lambda read access to the Secrets Manager secret
    this.calendarCredentialsSecret.grantRead(this.calendarHandler);

    // DynamoDB table for storing appointment records
    const appointmentsTable = new dynamodb.Table(this, 'AppointmentsTable', {
      partitionKey: { name: 'appointment_id', type: dynamodb.AttributeType.STRING },
      sortKey: { name: 'start_time', type: dynamodb.AttributeType.STRING },
      billingMode: dynamodb.BillingMode.PAY_PER_REQUEST,
      removalPolicy: cdk.RemovalPolicy.DESTROY,
    });

    // Grant Calendar Lambda write access to the appointments table
    appointmentsTable.grantWriteData(this.calendarHandler);

    // Add appointments table name to Calendar Lambda environment
    this.calendarHandler.addEnvironment('APPOINTMENTS_TABLE_NAME', appointmentsTable.tableName);

    // Bedrock Agent Action Group — calendar operations (Requirement 4.1, 4.2, 4.3, 4.4, 4.5)
    this.calendarActionGroup = {
      actionGroupName: 'calendar-action-group',
      description: 'Action Group para consultar disponibilidad y crear reuniones en Google Calendar',
      actionGroupExecutor: {
        lambda: this.calendarHandler.functionArn,
      },
      apiSchema: {
        payload: JSON.stringify({
          openapi: '3.0.0',
          info: {
            title: 'Google Calendar Scheduling API',
            version: '1.0.0',
            description: 'Action Group para consultar disponibilidad y crear reuniones en Google Calendar',
          },
          paths: {
            '/check-availability': {
              post: {
                operationId: 'checkAvailability',
                description: 'Consulta la disponibilidad del equipo para una fecha específica. Retorna los slots de 30 minutos disponibles dentro del horario laboral (lunes a viernes, 9:00-17:00). Usar cuando el usuario quiere saber qué horarios hay disponibles para agendar una reunión.',
                parameters: [
                  {
                    name: 'date',
                    in: 'query',
                    description: 'Fecha para consultar disponibilidad en formato YYYY-MM-DD',
                    required: true,
                    schema: { type: 'string', format: 'date' },
                  },
                ],
                responses: {
                  '200': {
                    description: 'Lista de slots disponibles o mensaje informativo',
                    content: {
                      'application/json': {
                        schema: {
                          type: 'object',
                          properties: {
                            message: { type: 'string', description: 'Lista numerada de slots disponibles o mensaje informativo' },
                          },
                        },
                      },
                    },
                  },
                },
              },
            },
            '/create-event': {
              post: {
                operationId: 'createEvent',
                description: 'Crea una reunión de 30 minutos en Google Calendar con el equipo. Verifica que el slot esté disponible antes de crear el evento. Usar cuando el usuario ha elegido un horario y quiere confirmar la reunión.',
                parameters: [
                  {
                    name: 'start_time',
                    in: 'query',
                    description: 'Hora de inicio de la reunión en formato ISO 8601 (ej: 2026-05-08T11:00:00-04:00)',
                    required: true,
                    schema: { type: 'string', format: 'date-time' },
                  },
                  {
                    name: 'title',
                    in: 'query',
                    description: 'Título de la reunión en formato "Reunión - Nombre (Empresa)" (máximo 200 caracteres)',
                    required: true,
                    schema: { type: 'string', maxLength: 200 },
                  },
                  {
                    name: 'contact_email',
                    in: 'query',
                    description: 'Email del contacto que quiere agendar la reunión. Se agrega como asistente al evento y recibe notificación.',
                    required: false,
                    schema: { type: 'string', format: 'email' },
                  },
                  {
                    name: 'contact_name',
                    in: 'query',
                    description: 'Nombre completo del contacto que quiere agendar la reunión.',
                    required: false,
                    schema: { type: 'string' },
                  },
                  {
                    name: 'contact_company',
                    in: 'query',
                    description: 'Empresa del contacto que quiere agendar la reunión.',
                    required: false,
                    schema: { type: 'string' },
                  },
                  {
                    name: 'contact_role',
                    in: 'query',
                    description: 'Cargo del contacto que quiere agendar la reunión.',
                    required: false,
                    schema: { type: 'string' },
                  },
                  {
                    name: 'contact_phone',
                    in: 'query',
                    description: 'Número de teléfono WhatsApp del contacto.',
                    required: false,
                    schema: { type: 'string' },
                  },
                  {
                    name: 'meeting_reason',
                    in: 'query',
                    description: 'Motivo o tema de la reunión según lo indicó el contacto.',
                    required: false,
                    schema: { type: 'string' },
                  },
                ],
                responses: {
                  '200': {
                    description: 'Confirmación de la reunión creada o mensaje de error',
                    content: {
                      'application/json': {
                        schema: {
                          type: 'object',
                          properties: {
                            message: { type: 'string', description: 'Confirmación con fecha, hora, título y enlace, o mensaje de error' },
                          },
                        },
                      },
                    },
                  },
                },
              },
            },
          },
        }),
      },
    };

    // Add the action group to the existing Bedrock Agent via property override
    // CloudFormation expects PascalCase property names in raw overrides
    this.bedrockAgent.addPropertyOverride('ActionGroups', [
      {
        ActionGroupName: 'calendar-action-group',
        Description: 'Action Group para consultar disponibilidad y crear reuniones en Google Calendar',
        ActionGroupExecutor: {
          Lambda: this.calendarHandler.functionArn,
        },
        ApiSchema: {
          Payload: (this.calendarActionGroup as any).apiSchema.payload,
        },
      },
    ]);

    // Grant Bedrock permission to invoke the Calendar Lambda (Requirement 5.6)
    this.calendarHandler.addPermission('BedrockInvokeCalendarLambda', {
      principal: new iam.ServicePrincipal('bedrock.amazonaws.com'),
      action: 'lambda:InvokeFunction',
      sourceArn: `arn:aws:bedrock:${this.region}:${this.account}:agent/${this.bedrockAgent.attrAgentId}`,
    });

    // ---------------------------------------------------------------
    // Appointments Panel — API + Static Site
    // ---------------------------------------------------------------

    // Lambda to read appointments from DynamoDB
    const appointmentsHandler = new lambda.Function(this, 'AppointmentsHandler', {
      runtime: lambda.Runtime.PYTHON_3_12,
      handler: 'handler.lambda_handler',
      code: lambda.Code.fromAsset(path.join(__dirname, '../../lambda-appointments')),
      timeout: cdk.Duration.seconds(10),
      memorySize: 128,
      description: 'Reads appointment records from DynamoDB for the panel',
      environment: {
        APPOINTMENTS_TABLE_NAME: appointmentsTable.tableName,
      },
    });

    // Grant read access to appointments table
    appointmentsTable.grantReadData(appointmentsHandler);

    // Add /appointments endpoint to the existing API Gateway
    const appointmentsResource = this.api.root.addResource('appointments');
    const appointmentsIntegration = new apigateway.LambdaIntegration(appointmentsHandler);
    appointmentsResource.addMethod('GET', appointmentsIntegration);
    appointmentsResource.addCorsPreflight({
      allowOrigins: ['*'],
      allowMethods: ['GET', 'OPTIONS'],
    });

    // S3 bucket for the static panel site (hosted externally via Amplify)
    // Panel HTML is at waba-bedrock-webhook/panel/index.html

    // Lambda to read conversations from DynamoDB
    const conversationsHandler = new lambda.Function(this, 'ConversationsHandler', {
      runtime: lambda.Runtime.PYTHON_3_12,
      handler: 'handler.lambda_handler',
      code: lambda.Code.fromAsset(path.join(__dirname, '../../lambda-conversations')),
      timeout: cdk.Duration.seconds(10),
      memorySize: 128,
      description: 'Reads conversation records from DynamoDB for the panel',
      environment: {
        CONVERSATIONS_TABLE_NAME: conversationsTable.tableName,
      },
    });

    // Grant read access to conversations table
    conversationsTable.grantReadData(conversationsHandler);

    // Add /conversations endpoint to the existing API Gateway
    const conversationsResource = this.api.root.addResource('conversations');
    const conversationsIntegration = new apigateway.LambdaIntegration(conversationsHandler);
    conversationsResource.addMethod('GET', conversationsIntegration);
    conversationsResource.addCorsPreflight({
      allowOrigins: ['*'],
      allowMethods: ['GET', 'OPTIONS'],
    });

    // Lambda to send WhatsApp messages from the panel
    const sendMessageHandler = new lambda.Function(this, 'SendMessageHandler', {
      runtime: lambda.Runtime.PYTHON_3_12,
      handler: 'handler.lambda_handler',
      code: lambda.Code.fromAsset(path.join(__dirname, '../../lambda-send-message')),
      timeout: cdk.Duration.seconds(10),
      memorySize: 128,
      description: 'Sends WhatsApp messages from the panel',
      environment: {
        WHATSAPP_PHONE_NUMBER_ID: whatsappPhoneNumberId.valueAsString,
        WHATSAPP_ACCESS_TOKEN: whatsappAccessToken.valueAsString,
        CONVERSATIONS_TABLE_NAME: conversationsTable.tableName,
      },
    });

    // Grant write access to conversations table (to log sent messages)
    conversationsTable.grantWriteData(sendMessageHandler);

    // Add /send-message endpoint
    const sendMessageResource = this.api.root.addResource('send-message');
    const sendMessageIntegration = new apigateway.LambdaIntegration(sendMessageHandler);
    sendMessageResource.addMethod('POST', sendMessageIntegration);
    sendMessageResource.addCorsPreflight({
      allowOrigins: ['*'],
      allowMethods: ['POST', 'OPTIONS'],
      allowHeaders: ['Content-Type'],
    });

    // ---------------------------------------------------------------
    // CloudFormation Outputs (Requirement 9.2, 9.3, 9.4)
    // ---------------------------------------------------------------

    // Full webhook endpoint URL (Requirement 9.2)
    new cdk.CfnOutput(this, 'WebhookEndpointUrl', {
      value: `${this.api.url}webhook`,
      description: 'Full URL of the WhatsApp webhook endpoint',
    });

    // Bedrock Agent ID (Requirement 9.3)
    new cdk.CfnOutput(this, 'BedrockAgentId', {
      value: this.bedrockAgent.attrAgentId,
      description: 'ID of the Bedrock Agent',
    });

    // Bedrock Agent Alias ID (Requirement 9.3)
    new cdk.CfnOutput(this, 'BedrockAgentAliasId', {
      value: this.bedrockAgentAlias.attrAgentAliasId,
      description: 'ID of the Bedrock Agent Alias',
    });

    // Document Bucket name (Requirement 9.4)
    new cdk.CfnOutput(this, 'DocumentBucketName', {
      value: this.documentBucket.bucketName,
      description: 'Name of the S3 bucket for uploading PDF documents to the Knowledge Base',
    });

    // Calendar Credentials Secret ARN (Requirement 5.2)
    new cdk.CfnOutput(this, 'CalendarCredentialsSecretArn', {
      value: this.calendarCredentialsSecret.secretArn,
      description: 'ARN of the Secrets Manager secret for Google Service Account credentials',
    });

    // Calendar Lambda function name (Requirement 5.2)
    new cdk.CfnOutput(this, 'CalendarHandlerFunctionName', {
      value: this.calendarHandler.functionName,
      description: 'Name of the Calendar Lambda function for monitoring',
    });

    // Appointments table name
    new cdk.CfnOutput(this, 'AppointmentsTableName', {
      value: appointmentsTable.tableName,
      description: 'Name of the DynamoDB table storing appointment records',
    });

    // Conversations table name
    new cdk.CfnOutput(this, 'ConversationsTableName', {
      value: conversationsTable.tableName,
      description: 'Name of the DynamoDB table storing all chat interactions',
    });

    // Conversations API endpoint
    new cdk.CfnOutput(this, 'ConversationsApiUrl', {
      value: `${this.api.url}conversations`,
      description: 'URL of the conversations API endpoint',
    });

    // Appointments API endpoint
    new cdk.CfnOutput(this, 'AppointmentsApiUrl', {
      value: `${this.api.url}appointments`,
      description: 'URL of the appointments API endpoint',
    });
  }
}
