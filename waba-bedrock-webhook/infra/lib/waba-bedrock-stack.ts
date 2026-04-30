import * as path from 'path';
import * as cdk from 'aws-cdk-lib';
import * as apigateway from 'aws-cdk-lib/aws-apigateway';
import * as bedrock from 'aws-cdk-lib/aws-bedrock';
import * as dynamodb from 'aws-cdk-lib/aws-dynamodb';
import * as iam from 'aws-cdk-lib/aws-iam';
import * as lambda from 'aws-cdk-lib/aws-lambda';
import * as opensearchserverless from 'aws-cdk-lib/aws-opensearchserverless';
import * as s3 from 'aws-cdk-lib/aws-s3';
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
      instruction: 'Eres un asistente virtual. Responde las preguntas del usuario basándote en la información disponible en la base de conocimiento.',
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
      description: 'Live alias for Lambda invocation',
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
  }
}
