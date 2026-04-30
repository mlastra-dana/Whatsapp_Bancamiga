import * as cdk from 'aws-cdk-lib';
import { Template, Match } from 'aws-cdk-lib/assertions';
import { WabaBedrockStack } from '../../infra/lib/waba-bedrock-stack';

describe('WabaBedrockStack', () => {
  let template: Template;

  beforeAll(() => {
    const app = new cdk.App();
    const stack = new WabaBedrockStack(app, 'TestStack', {
      env: { region: 'us-east-1', account: '123456789012' },
    });
    template = Template.fromStack(stack);
  });

  // ---------------------------------------------------------------
  // Requirement 6.1: Lambda with Python 3.12, 30s timeout, 256MB
  // ---------------------------------------------------------------
  describe('Lambda Function (Req 6.1, 6.4, 6.5)', () => {
    test('creates Lambda with Python 3.12 runtime, 30s timeout, and 256MB memory', () => {
      template.hasResourceProperties('AWS::Lambda::Function', {
        Runtime: 'python3.12',
        Timeout: 30,
        MemorySize: 256,
        Handler: 'handler.lambda_handler',
      });
    });

    test('configures all required environment variables', () => {
      template.hasResourceProperties('AWS::Lambda::Function', {
        Environment: {
          Variables: {
            WHATSAPP_VERIFY_TOKEN: Match.anyValue(),
            WHATSAPP_ACCESS_TOKEN: Match.anyValue(),
            WHATSAPP_PHONE_NUMBER_ID: Match.anyValue(),
            BEDROCK_AGENT_ID: Match.anyValue(),
            BEDROCK_AGENT_ALIAS_ID: Match.anyValue(),
            BEDROCK_MODEL_ARN: Match.anyValue(),
            SYSTEM_PROMPT_BUCKET: Match.anyValue(),
            SYSTEM_PROMPT_KEY: 'system_prompt.txt',
            SESSION_TABLE_NAME: Match.anyValue(),
          },
        },
      });
    });
  });

  // ---------------------------------------------------------------
  // Requirement 6.2, 6.3: API Gateway REST API with /webhook
  // ---------------------------------------------------------------
  describe('API Gateway (Req 6.2, 6.3)', () => {
    test('creates a REST API', () => {
      template.hasResourceProperties('AWS::ApiGateway::RestApi', {
        Name: 'WABA Webhook API',
      });
    });

    test('creates /webhook resource', () => {
      template.hasResourceProperties('AWS::ApiGateway::Resource', {
        PathPart: 'webhook',
      });
    });

    test('creates GET method on /webhook', () => {
      template.hasResourceProperties('AWS::ApiGateway::Method', {
        HttpMethod: 'GET',
        Integration: {
          Type: 'AWS_PROXY',
        },
      });
    });

    test('creates POST method on /webhook', () => {
      template.hasResourceProperties('AWS::ApiGateway::Method', {
        HttpMethod: 'POST',
        Integration: {
          Type: 'AWS_PROXY',
        },
      });
    });
  });

  // ---------------------------------------------------------------
  // Requirement 8.1, 8.2: DynamoDB table
  // ---------------------------------------------------------------
  describe('DynamoDB Session Table (Req 8.1, 8.2)', () => {
    test('creates table with phone_number as partition key', () => {
      template.hasResourceProperties('AWS::DynamoDB::Table', {
        KeySchema: [
          {
            AttributeName: 'phone_number',
            KeyType: 'HASH',
          },
        ],
        AttributeDefinitions: [
          {
            AttributeName: 'phone_number',
            AttributeType: 'S',
          },
        ],
      });
    });

    test('uses PAY_PER_REQUEST billing mode', () => {
      template.hasResourceProperties('AWS::DynamoDB::Table', {
        BillingMode: 'PAY_PER_REQUEST',
      });
    });

    test('enables TTL on the ttl attribute', () => {
      template.hasResourceProperties('AWS::DynamoDB::Table', {
        TimeToLiveSpecification: {
          AttributeName: 'ttl',
          Enabled: true,
        },
      });
    });
  });

  // ---------------------------------------------------------------
  // Requirement 8.3: S3 Buckets
  // ---------------------------------------------------------------
  describe('S3 Buckets (Req 8.3)', () => {
    test('creates at least two S3 buckets (system prompt and documents)', () => {
      const buckets = template.findResources('AWS::S3::Bucket');
      // There should be at least 2 S3 buckets (SystemPromptBucket + DocumentBucket)
      expect(Object.keys(buckets).length).toBeGreaterThanOrEqual(2);
    });
  });

  // ---------------------------------------------------------------
  // Requirement 7.1, 8.5: OpenSearch Serverless
  // ---------------------------------------------------------------
  describe('OpenSearch Serverless (Req 7.1, 8.5)', () => {
    test('creates a VECTORSEARCH collection', () => {
      template.hasResourceProperties(
        'AWS::OpenSearchServerless::Collection',
        {
          Type: 'VECTORSEARCH',
        },
      );
    });

    test('creates encryption security policy', () => {
      template.hasResourceProperties(
        'AWS::OpenSearchServerless::SecurityPolicy',
        {
          Type: 'encryption',
        },
      );
    });

    test('creates network security policy', () => {
      template.hasResourceProperties(
        'AWS::OpenSearchServerless::SecurityPolicy',
        {
          Type: 'network',
        },
      );
    });

    test('creates data access policy', () => {
      template.hasResourceProperties(
        'AWS::OpenSearchServerless::AccessPolicy',
        {
          Type: 'data',
        },
      );
    });
  });

  // ---------------------------------------------------------------
  // Requirement 7.2, 7.3: Bedrock Knowledge Base
  // ---------------------------------------------------------------
  describe('Bedrock Knowledge Base (Req 7.2, 7.3)', () => {
    test('creates a Knowledge Base of type VECTOR', () => {
      template.hasResourceProperties('AWS::Bedrock::KnowledgeBase', {
        KnowledgeBaseConfiguration: {
          Type: 'VECTOR',
          VectorKnowledgeBaseConfiguration: {
            EmbeddingModelArn: Match.anyValue(),
          },
        },
      });
    });

    test('configures OpenSearch Serverless as storage', () => {
      template.hasResourceProperties('AWS::Bedrock::KnowledgeBase', {
        StorageConfiguration: {
          Type: 'OPENSEARCH_SERVERLESS',
          OpensearchServerlessConfiguration: {
            CollectionArn: Match.anyValue(),
            VectorIndexName: Match.anyValue(),
            FieldMapping: {
              VectorField: Match.anyValue(),
              TextField: Match.anyValue(),
              MetadataField: Match.anyValue(),
            },
          },
        },
      });
    });

    test('creates an S3 data source for the Knowledge Base', () => {
      template.hasResourceProperties('AWS::Bedrock::DataSource', {
        DataSourceConfiguration: {
          Type: 'S3',
          S3Configuration: {
            BucketArn: Match.anyValue(),
          },
        },
      });
    });
  });

  // ---------------------------------------------------------------
  // Requirement 7.4: Bedrock Agent
  // ---------------------------------------------------------------
  describe('Bedrock Agent (Req 7.4)', () => {
    test('creates a Bedrock Agent with the correct foundation model', () => {
      template.hasResourceProperties('AWS::Bedrock::Agent', {
        AgentName: 'waba-bedrock-agent',
        FoundationModel: Match.stringLikeRegexp('anthropic\\.claude'),
      });
    });

    test('associates the Knowledge Base with the Agent', () => {
      template.hasResourceProperties('AWS::Bedrock::Agent', {
        KnowledgeBases: Match.arrayWith([
          Match.objectLike({
            KnowledgeBaseId: Match.anyValue(),
            KnowledgeBaseState: 'ENABLED',
          }),
        ]),
      });
    });
  });

  // ---------------------------------------------------------------
  // Requirement 7.5: Bedrock Agent Alias
  // ---------------------------------------------------------------
  describe('Bedrock Agent Alias (Req 7.5)', () => {
    test('creates a Bedrock Agent Alias', () => {
      template.hasResourceProperties('AWS::Bedrock::AgentAlias', {
        AgentAliasName: 'live',
        AgentId: Match.anyValue(),
      });
    });
  });

  // ---------------------------------------------------------------
  // Requirement 8.4: IAM Permissions
  // ---------------------------------------------------------------
  describe('IAM Permissions (Req 8.4)', () => {
    test('grants Lambda DynamoDB read/write access', () => {
      template.hasResourceProperties('AWS::IAM::Policy', {
        PolicyDocument: {
          Statement: Match.arrayWith([
            Match.objectLike({
              Action: Match.arrayWith([
                'dynamodb:BatchGetItem',
                'dynamodb:PutItem',
                'dynamodb:DeleteItem',
              ]),
              Effect: 'Allow',
            }),
          ]),
        },
      });
    });

    test('grants Lambda bedrock:InvokeAgent permission', () => {
      template.hasResourceProperties('AWS::IAM::Policy', {
        PolicyDocument: {
          Statement: Match.arrayWith([
            Match.objectLike({
              Action: 'bedrock:InvokeAgent',
              Effect: 'Allow',
            }),
          ]),
        },
      });
    });
  });

  // ---------------------------------------------------------------
  // Requirement 9.2, 9.3, 9.4: CloudFormation Outputs
  // ---------------------------------------------------------------
  describe('CloudFormation Outputs (Req 9.2, 9.3, 9.4)', () => {
    test('exports webhook endpoint URL', () => {
      template.hasOutput('WebhookEndpointUrl', {
        Description: Match.stringLikeRegexp('[Ww]ebhook'),
      });
    });

    test('exports Bedrock Agent ID', () => {
      template.hasOutput('BedrockAgentId', {
        Description: Match.stringLikeRegexp('[Bb]edrock.*[Aa]gent'),
      });
    });

    test('exports Bedrock Agent Alias ID', () => {
      template.hasOutput('BedrockAgentAliasId', {
        Description: Match.stringLikeRegexp('[Aa]lias'),
      });
    });

    test('exports Document Bucket name', () => {
      template.hasOutput('DocumentBucketName', {
        Description: Match.stringLikeRegexp('[Bb]ucket'),
      });
    });
  });
});
