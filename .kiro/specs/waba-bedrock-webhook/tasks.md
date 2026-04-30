# Implementation Plan: WABA Bedrock Webhook

## Overview

This plan implements a serverless WhatsApp Business API webhook handler integrated with Amazon Bedrock Agent and Knowledge Base. The implementation follows an incremental approach: first setting up the CDK infrastructure (TypeScript), then building the Python Lambda modules one by one, wiring them together in the handler, and finally adding tests. Each task builds on the previous ones to ensure no orphaned code.

## Tasks

- [x] 1. Set up project structure and CDK scaffolding
  - [x] 1.1 Initialize CDK project and configure dependencies
    - Create `infra/` directory with CDK TypeScript project (`bin/app.ts`, `lib/waba-bedrock-stack.ts`, `package.json`, `tsconfig.json`, `cdk.json`)
    - Install dependencies: `aws-cdk-lib`, `constructs`, `@aws-cdk/aws-bedrock-alpha`
    - Configure the CDK app entry point in `bin/app.ts` with environment set to `us-east-1`
    - Define stack class skeleton in `lib/waba-bedrock-stack.ts` with stack parameters for `WHATSAPP_VERIFY_TOKEN`, `WHATSAPP_ACCESS_TOKEN`, `WHATSAPP_PHONE_NUMBER_ID`
    - _Requirements: 6.5, 9.1_

  - [x] 1.2 Create Lambda module skeleton and test structure
    - Create `lambda/` directory with empty module files: `handler.py`, `whatsapp.py`, `bedrock_agent.py`, `session_manager.py`, `prompt_reader.py`
    - Create `lambda/requirements.txt` with `boto3` as dev dependency
    - Create `tests/unit/` directory with empty test files: `test_handler.py`, `test_whatsapp.py`, `test_bedrock_agent.py`, `test_session_manager.py`, `test_prompt_reader.py`
    - Create `tests/unit/properties/` directory with empty property test files: `test_verification_props.py`, `test_message_props.py`, `test_session_props.py`, `test_bedrock_props.py`, `test_whatsapp_props.py`, `test_logging_props.py`
    - Create `tests/conftest.py` with shared fixtures and mocks
    - Create `tests/requirements-test.txt` with `pytest>=7.0`, `hypothesis>=6.0`, `moto>=5.0`, `pytest-mock>=3.0`
    - _Requirements: 6.1_

- [x] 2. Implement CDK infrastructure — Storage and Bedrock
  - [x] 2.1 Create DynamoDB session table and S3 buckets
    - In `lib/waba-bedrock-stack.ts`, create the DynamoDB `Session_Table` with `phone_number` (String) as partition key, PAY_PER_REQUEST billing mode, and TTL enabled on the `ttl` attribute
    - Create the `System_Prompt_Bucket` S3 bucket for the system prompt file
    - Create the `Document_Bucket` S3 bucket for PDF documents that feed the Knowledge Base
    - _Requirements: 8.1, 8.2, 8.3_

  - [x] 2.2 Create OpenSearch Serverless collection and Bedrock Knowledge Base
    - Create an OpenSearch Serverless collection of type VECTORSEARCH using `@aws-cdk/aws-bedrock-alpha` constructs
    - Create a Bedrock Knowledge Base configured with the `Document_Bucket` as data source and the OpenSearch Serverless collection as vector store
    - Configure access policies for OpenSearch Serverless to allow the Knowledge Base to index and query
    - _Requirements: 7.1, 7.2, 7.3, 8.5_

  - [x] 2.3 Create Bedrock Agent with Knowledge Base and alias
    - Create a Bedrock Agent configured with the model specified by `BEDROCK_MODEL_ARN` (default: `arn:aws:bedrock:us-east-1::foundation-model/anthropic.claude-sonnet-4-6`)
    - Associate the Knowledge Base with the Bedrock Agent
    - Create a Bedrock Agent Alias for invocation from the Lambda
    - _Requirements: 7.4, 7.5_

- [x] 3. Implement CDK infrastructure — Lambda, API Gateway, and Outputs
  - [x] 3.1 Create Lambda function with environment variables and permissions
    - Create a Lambda function with Python 3.12 runtime, 30-second timeout, and 256 MB memory, pointing to the `lambda/` directory as code
    - Configure all environment variables: `WHATSAPP_VERIFY_TOKEN`, `WHATSAPP_ACCESS_TOKEN`, `WHATSAPP_PHONE_NUMBER_ID`, `BEDROCK_AGENT_ID`, `BEDROCK_AGENT_ALIAS_ID`, `BEDROCK_MODEL_ARN`, `SYSTEM_PROMPT_BUCKET`, `SYSTEM_PROMPT_KEY`, `SESSION_TABLE_NAME`
    - Grant the Lambda IAM permissions to: read/write the Session_Table, read from the System_Prompt_Bucket, and invoke the Bedrock Agent
    - _Requirements: 6.1, 6.4, 6.5, 8.4_

  - [x] 3.2 Create API Gateway REST API with /webhook resource
    - Create a REST API Gateway with a `/webhook` resource accepting GET and POST methods
    - Integrate the API Gateway with the Lambda function using Lambda Proxy integration
    - _Requirements: 6.2, 6.3_

  - [x] 3.3 Add CloudFormation Outputs
    - Export the full webhook endpoint URL (API Gateway URL + `/webhook`)
    - Export the Bedrock Agent ID and Bedrock Agent Alias ID
    - Export the Document_Bucket name for easy PDF upload
    - _Requirements: 9.2, 9.3, 9.4_

  - [x] 3.4 Write CDK infrastructure snapshot tests
    - Create `tests/infra/test_stack.test.ts` using Jest with `@aws-cdk/assertions`
    - Verify the CloudFormation template includes: Lambda with Python 3.12 runtime, 30s timeout, 256MB memory
    - Verify API Gateway REST API with `/webhook` resource and GET/POST methods
    - Verify DynamoDB table with correct partition key, PAY_PER_REQUEST billing, and TTL
    - Verify S3 buckets for system prompt and documents
    - Verify OpenSearch Serverless collection of type VECTORSEARCH
    - Verify Bedrock Agent, Knowledge Base, and Agent Alias resources
    - Verify CloudFormation Outputs for webhook URL, agent IDs, and document bucket name
    - _Requirements: 6.1, 6.2, 6.3, 6.4, 6.5, 7.1, 7.2, 7.3, 7.4, 7.5, 8.1, 8.2, 8.3, 8.4, 8.5, 9.1, 9.2, 9.3, 9.4_

- [x] 4. Checkpoint — Verify CDK infrastructure
  - Ensure CDK synth runs successfully and all infrastructure tests pass. Ask the user if questions arise.

- [x] 5. Implement session manager module
  - [x] 5.1 Implement `SessionManager` class in `session_manager.py`
    - Implement `__init__(self, table_name: str, ttl_hours: int = 24)` that creates a DynamoDB resource
    - Implement `get_or_create_session(self, phone_number: str) -> str` that does a `GetItem` by `phone_number`, returns existing `session_id` if found, or generates a UUID v4, does `PutItem` with `phone_number`, `session_id`, `last_activity`, `ttl`, and `created_at`, and returns the new `session_id`
    - Update `ttl` and `last_activity` on each access to extend active sessions
    - Handle DynamoDB errors gracefully with logging
    - _Requirements: 3.1, 3.2, 3.3, 3.4, 3.5_

  - [x] 5.2 Write property test for session idempotency and uniqueness
    - **Property 6: Idempotencia y unicidad de sesiones**
    - For any phone number, calling `get_or_create_session` twice consecutively must return the same `session_id`; for any pair of distinct phone numbers, the generated `session_id` values must be different
    - Use moto to mock DynamoDB
    - **Validates: Requirements 3.2, 3.3**

  - [x] 5.3 Write unit tests for `SessionManager`
    - Test creating a new session for a new phone number
    - Test retrieving an existing session for a known phone number
    - Test DynamoDB error handling (graceful degradation)
    - Use moto to mock DynamoDB
    - _Requirements: 3.1, 3.2, 3.3, 3.4_

- [x] 6. Implement prompt reader module
  - [x] 6.1 Implement `PromptReader` class in `prompt_reader.py`
    - Implement `__init__(self, bucket: str, key: str)` that creates an S3 client
    - Implement `get_prompt(self) -> str` that reads the system prompt from S3 using `GetObject`, caches it in memory for warm starts, and falls back to the default prompt `"Eres un asistente virtual. Responde las preguntas del usuario basándote en la información disponible en la base de conocimiento."` if S3 fails, logging the error
    - _Requirements: 4.2, 4.5_

  - [x] 6.2 Write unit tests for `PromptReader`
    - Test successful S3 read
    - Test fallback to default prompt when S3 fails
    - Test caching behavior on warm start (second call uses cached value)
    - Use moto to mock S3
    - _Requirements: 4.2, 4.5_

- [x] 7. Implement Bedrock Agent client module
  - [x] 7.1 Implement `BedrockAgentClient` class in `bedrock_agent.py`
    - Implement `__init__(self, agent_id: str, agent_alias_id: str)` that creates a `bedrock-agent-runtime` boto3 client with 25-second timeout
    - Implement `invoke(self, input_text: str, session_id: str) -> str` that calls `invoke_agent(agentId, agentAliasId, sessionId, inputText)`, iterates the EventStream response, concatenates all `completion` chunks decoded as UTF-8, and returns the full response text
    - Raise `BedrockAgentError` on invocation failure or timeout
    - _Requirements: 4.1, 4.3, 4.4_

  - [x] 7.2 Write property test for response chunk concatenation
    - **Property 7: Concatenación de chunks de respuesta del agente**
    - For any sequence of byte chunks returned by the Bedrock Agent, the extraction function must produce a string that is the UTF-8 decoded concatenation of all chunks in order
    - **Validates: Requirements 4.3**

  - [x] 7.3 Write unit tests for `BedrockAgentClient`
    - Test successful invocation with mocked EventStream response
    - Test timeout handling (25-second limit)
    - Test service error handling
    - _Requirements: 4.1, 4.3, 4.4_

- [x] 8. Implement WhatsApp client module
  - [x] 8.1 Implement `WhatsAppClient` class in `whatsapp.py`
    - Implement `__init__(self, phone_number_id: str, access_token: str)` that configures the base URL `https://graph.facebook.com/v21.0/{phone_number_id}/messages` and authorization headers
    - Implement `send_text_message(self, to: str, text: str) -> dict` that sends a POST request with the correct payload structure (`messaging_product`, `recipient_type`, `to`, `type`, `text.body`), using `urllib3`
    - Implement retry logic: retry exactly once after 1-second wait for HTTP 429 or 5xx errors; do not retry for other error codes
    - Raise `WhatsAppSendError` if sending fails after retry
    - Log errors with HTTP status code and response body
    - _Requirements: 5.1, 5.2, 5.3, 5.4_

  - [x] 8.2 Write property test for WhatsApp message payload construction
    - **Property 8: Construcción correcta del payload de envío de WhatsApp**
    - For any destination phone number and response text, the constructed payload must contain `messaging_product` = "whatsapp", `recipient_type` = "individual", `to` = destination number, `type` = "text", and `text.body` = response text
    - **Validates: Requirements 5.2**

  - [x] 8.3 Write property test for transient error retry behavior
    - **Property 9: Reintento en errores transitorios**
    - For any HTTP status code returned by the WhatsApp Cloud API, if the code is 429 or in the range 500-599, the client must retry exactly once; for any other error code (4xx except 429), it must not retry
    - **Validates: Requirements 5.4**

  - [x] 8.4 Write unit tests for `WhatsAppClient`
    - Test successful message send
    - Test retry on HTTP 429 error
    - Test no retry on HTTP 400 error
    - Test retry on HTTP 500 error
    - _Requirements: 5.1, 5.2, 5.3, 5.4_

- [x] 9. Checkpoint — Verify all modules
  - Ensure all unit tests and property tests pass for session_manager, prompt_reader, bedrock_agent, and whatsapp modules. Ask the user if questions arise.

- [x] 10. Implement Lambda handler and wire all modules together
  - [x] 10.1 Implement webhook verification in `handler.py`
    - Implement `handle_verification(params: dict) -> dict` that validates the presence of `hub.mode`, `hub.verify_token`, and `hub.challenge` parameters (return HTTP 400 if missing), compares `hub.verify_token` with the `WHATSAPP_VERIFY_TOKEN` environment variable (return HTTP 403 if mismatch), and returns HTTP 200 with `hub.challenge` as body on success
    - _Requirements: 1.1, 1.2, 1.3, 1.4_

  - [x] 10.2 Write property tests for webhook verification
    - **Property 1: Correctitud de la verificación del webhook**
    - For any pair of tokens and any challenge string, if tokens match the handler must return HTTP 200 with the challenge as body; if tokens don't match, the handler must return HTTP 403
    - **Validates: Requirements 1.2, 1.3**
    - **Property 2: Parámetros de verificación faltantes**
    - For any GET request missing at least one of the required parameters (`hub.mode`, `hub.verify_token`, `hub.challenge`), the handler must return HTTP 400
    - **Validates: Requirements 1.4**

  - [x] 10.3 Implement message extraction and POST handling in `handler.py`
    - Implement `extract_text_messages(body: dict) -> list[dict]` that navigates `entry[].changes[].value.messages[]`, filters for `type == "text"`, and returns a list of dicts with `from`, `text`, and `id` fields
    - Implement `handle_message(body: dict) -> dict` that validates the payload structure, extracts text messages, and for each message: gets/creates a session via `SessionManager`, reads the system prompt via `PromptReader`, invokes the `BedrockAgentClient`, and sends the response via `WhatsAppClient`
    - Handle Bedrock Agent errors/timeouts by sending the default error message to the user
    - Always return HTTP 200 for valid POST requests
    - _Requirements: 2.1, 2.2, 2.3, 2.4, 2.5, 3.1, 3.2, 4.1, 4.2, 4.4, 5.1_

  - [x] 10.4 Write property tests for message extraction and handling
    - **Property 3: Extracción y procesamiento individual de mensajes de texto**
    - For any valid WhatsApp payload containing N text messages (N ≥ 1), the extraction function must return exactly N messages with correct `from`, `text`, and `id` fields
    - **Validates: Requirements 2.1, 2.2**
    - **Property 4: Filtrado de mensajes no-texto**
    - For any WhatsApp payload containing only non-text messages (image, audio, video, document, location), the text extraction function must return an empty list and the handler must respond with HTTP 200
    - **Validates: Requirements 2.3**
    - **Property 5: Manejo graceful de payloads inválidos**
    - For any POST payload that does not contain the expected WhatsApp Cloud API structure, the handler must respond with HTTP 200 without raising exceptions
    - **Validates: Requirements 2.5**

  - [x] 10.5 Implement `lambda_handler` entry point with logging and error handling
    - Implement `lambda_handler(event, context)` that routes GET requests to `handle_verification` and POST requests to `handle_message`
    - Initialize module instances (`WhatsAppClient`, `BedrockAgentClient`, `SessionManager`, `PromptReader`) from environment variables, outside the handler for reuse across warm starts
    - Add structured logging: log each incoming message with sender phone number and message type (never log message content for privacy), log each Bedrock Agent invocation with session ID and response time in milliseconds, log each WhatsApp API send with HTTP status code
    - Wrap all processing in a top-level try/except that catches any unhandled exception, logs the full traceback, and returns HTTP 200
    - _Requirements: 2.4, 10.1, 10.2, 10.3, 10.4_

  - [x] 10.6 Write property tests for logging privacy and exception safety
    - **Property 10: Logging que preserva privacidad**
    - For any incoming message with arbitrary text content, the generated logs must contain the sender's phone number and message type but must never contain the message text content
    - **Validates: Requirements 10.1**
    - **Property 11: Seguridad ante excepciones no controladas**
    - For any exception thrown during message processing, the handler must catch it, log the full traceback, and return HTTP 200
    - **Validates: Requirements 10.4**

  - [x] 10.7 Write unit tests for `lambda_handler`
    - Test full GET verification flow (success and failure)
    - Test full POST flow with a text message (mocking all dependencies)
    - Test POST with empty/invalid payload returns HTTP 200
    - Test unhandled exception returns HTTP 200
    - _Requirements: 1.1, 1.2, 1.3, 1.4, 2.1, 2.2, 2.3, 2.4, 2.5, 10.4_

- [x] 11. Final checkpoint — Ensure all tests pass
  - Run all Python tests (pytest) and CDK tests (Jest). Ensure all unit tests, property tests, and infrastructure tests pass. Ask the user if questions arise.

## Notes

- Tasks marked with `*` are optional and can be skipped for faster MVP
- Each task references specific requirements for traceability
- Checkpoints ensure incremental validation
- Property tests validate universal correctness properties from the design document (Properties 1–11)
- Unit tests validate specific examples and edge cases
- CDK infrastructure is built first (tasks 1–4) so that Lambda code can reference the correct resource names and structure
- Python modules are implemented bottom-up (session_manager → prompt_reader → bedrock_agent → whatsapp → handler) to avoid forward dependencies
- The handler (task 10) wires all modules together as the final integration step
