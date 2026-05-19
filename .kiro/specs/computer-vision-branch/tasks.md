# Implementation Plan: Computer Vision Branch

## Overview

Add a computer vision branch to the WhatsApp webhook system. The implementation follows a bottom-up approach: first creating the standalone `vision_analyzer.py` module, then extending `session_manager.py` and `whatsapp.py` with new methods, then modifying `handler.py` to wire the routing logic, and finally updating the CDK stack for IAM permissions.

## Tasks

- [x] 1. Implement the Vision Analyzer module
  - [x] 1.1 Create `waba-bedrock-webhook/lambda/vision_analyzer.py`
    - Implement `VisionAnalyzerError` exception class
    - Implement `build_vision_payload(image_bytes, mime_type)` function that base64-encodes image data and builds the Claude Messages API payload with the Spanish-language vision prompt
    - Implement `extract_vision_response(response_body)` function that extracts and concatenates text content blocks from the Bedrock response
    - Implement `VisionAnalyzer` class with `__init__(model_id)` and `analyze(image_bytes, mime_type)` method using `boto3` bedrock-runtime client with timeout config
    - Handle `ReadTimeoutError`, `ClientError`, and generic exceptions by raising `VisionAnalyzerError`
    - _Requirements: 5.1, 5.2, 5.3, 5.4_

  - [ ]* 1.2 Write property test for base64 encoding round-trip
    - **Property 4: Base64 encoding round-trip in vision payload**
    - Create `waba-bedrock-webhook/tests/unit/properties/test_vision_props.py`
    - Use Hypothesis to generate arbitrary bytes and valid MIME types, verify that decoding the base64 `data` field from `build_vision_payload` output yields the original bytes and `media_type` matches input
    - **Validates: Requirements 5.1**

  - [ ]* 1.3 Write property test for vision response text extraction
    - **Property 5: Vision response text extraction**
    - Use Hypothesis to generate response dicts with varying numbers of text content blocks, verify `extract_vision_response` returns the concatenation of all text values in order
    - **Validates: Requirements 5.3**

  - [ ]* 1.4 Write unit tests for VisionAnalyzer
    - Create `waba-bedrock-webhook/tests/unit/test_vision_analyzer.py`
    - Test successful analysis with mocked boto3 client
    - Test `ReadTimeoutError` raises `VisionAnalyzerError`
    - Test `ClientError` raises `VisionAnalyzerError`
    - _Requirements: 5.1, 5.3, 5.4_

- [x] 2. Extend Session Manager with mode methods
  - [x] 2.1 Add `set_mode` and `get_mode` methods to `SessionManager` in `waba-bedrock-webhook/lambda/session_manager.py`
    - `set_mode(phone_number, mode)`: uses DynamoDB `update_item` to SET mode or REMOVE mode when None
    - `get_mode(phone_number)`: uses DynamoDB `get_item` with ProjectionExpression to return mode or None
    - Both methods handle `ClientError` gracefully with logging
    - _Requirements: 2.1, 2.2, 2.3, 2.4_

  - [ ]* 2.2 Write property test for session mode round-trip
    - **Property 2: Session mode round-trip**
    - Add tests to `waba-bedrock-webhook/tests/unit/properties/test_session_props.py`
    - Use Hypothesis to generate phone numbers, verify set_mode("vision") followed by get_mode returns "vision", and set_mode(None) followed by get_mode returns None
    - **Validates: Requirements 2.1, 2.2, 2.3, 2.4**

  - [ ]* 2.3 Write unit tests for set_mode and get_mode
    - Add tests to `waba-bedrock-webhook/tests/unit/test_session_manager.py`
    - Test setting mode to "vision" and reading it back
    - Test clearing mode (set to None) and reading it back
    - Test graceful handling of DynamoDB errors
    - _Requirements: 2.1, 2.2, 2.3, 2.4_

- [x] 3. Extend WhatsApp Client with media methods
  - [x] 3.1 Add `WhatsAppMediaError`, `get_media_url`, and `download_media` to `WhatsAppClient` in `waba-bedrock-webhook/lambda/whatsapp.py`
    - `WhatsAppMediaError` exception class for media retrieval failures
    - `get_media_url(media_id)`: calls GET `https://graph.facebook.com/v21.0/{media_id}` with auth header, returns URL or raises `WhatsAppMediaError`
    - `download_media(media_url)`: downloads binary content with auth header, returns bytes or raises `WhatsAppMediaError`
    - _Requirements: 4.1, 4.2, 4.3, 4.4_

  - [ ]* 3.2 Write unit tests for get_media_url and download_media
    - Add tests to `waba-bedrock-webhook/tests/unit/test_whatsapp.py`
    - Test successful media URL retrieval with mocked HTTP response
    - Test media URL retrieval failure (non-2xx status) raises `WhatsAppMediaError`
    - Test successful media download returns bytes
    - Test media download failure raises `WhatsAppMediaError`
    - _Requirements: 4.1, 4.2, 4.3, 4.4_

- [x] 4. Checkpoint - Ensure all tests pass
  - Ensure all tests pass, ask the user if questions arise.

- [x] 5. Modify Handler with vision routing logic
  - [x] 5.1 Add vision constants and helper functions to `waba-bedrock-webhook/lambda/handler.py`
    - Add `VISION_KEYWORDS` list, `VISION_MODE_CONFIRMATION`, `VISION_MODE_REMINDER`, and `VISION_ERROR_MESSAGE` constants
    - Implement `detect_vision_intent(text)` function for case-insensitive keyword matching
    - Implement `extract_image_metadata(msg)` function that returns `(media_id, mime_type)` tuple or None
    - Add `vision_analyzer` to module-level service instances and `_init_services()`
    - _Requirements: 1.1, 9.1, 9.2_

  - [x] 5.2 Modify `handle_message` in `waba-bedrock-webhook/lambda/handler.py` to add vision routing
    - Access raw messages from the webhook payload to check message type before text extraction
    - For each message: get session mode via `session_manager.get_mode(phone_number)`
    - CASE 1: Image message + mode is "vision" → extract metadata, download media, analyze, send description, reset mode
    - CASE 2: Text message with vision keyword → set mode to "vision", send confirmation
    - CASE 3: Text message + mode is "vision" → send reminder
    - CASE 4: Otherwise → existing Bedrock Agent flow
    - Wrap vision pipeline in try/except, send error message and reset mode on any failure
    - _Requirements: 1.1, 1.3, 2.1, 2.2, 2.3, 3.1, 3.2, 3.3, 4.1, 4.2, 6.1, 6.2, 6.3, 7.1, 7.2_

  - [ ]* 5.3 Write property test for vision keyword detection
    - **Property 1: Vision keyword detection is case-insensitive and partial**
    - Add to `waba-bedrock-webhook/tests/unit/properties/test_handler_props.py`
    - Use Hypothesis to generate text containing keywords in random case with surrounding text, verify `detect_vision_intent` returns True; generate text without any keyword, verify it returns False
    - **Validates: Requirements 1.1**

  - [ ]* 5.4 Write property test for image metadata extraction
    - **Property 7: Image metadata extraction**
    - Add to `waba-bedrock-webhook/tests/unit/properties/test_handler_props.py`
    - Use Hypothesis to generate image message dicts with `id` and `mime_type` fields, verify `extract_image_metadata` returns the exact values
    - **Validates: Requirements 9.1, 9.2**

  - [ ]* 5.5 Write property test for vision mode routing correctness
    - **Property 3: Vision mode routing correctness**
    - Add to `waba-bedrock-webhook/tests/unit/properties/test_handler_props.py`
    - Verify that image messages in vision mode route to vision pipeline (not Bedrock Agent), and text messages in vision mode trigger reminder (not Bedrock Agent)
    - **Validates: Requirements 3.1, 3.2**

  - [ ]* 5.6 Write property test for error recovery resets mode
    - **Property 6: Error recovery resets mode**
    - Add to `waba-bedrock-webhook/tests/unit/properties/test_handler_props.py`
    - Verify that any failure during vision pipeline results in error message sent to user AND mode reset to None
    - **Validates: Requirements 6.3, 2.2, 2.3**

  - [ ]* 5.7 Write unit tests for handler vision routing
    - Add tests to `waba-bedrock-webhook/tests/unit/test_handler.py`
    - Test vision keyword triggers mode change and confirmation message
    - Test image in vision mode triggers analysis pipeline
    - Test text in vision mode triggers reminder
    - Test vision analysis error sends error message and resets mode
    - _Requirements: 1.1, 3.1, 3.2, 6.3_

- [x] 6. Update CDK stack for vision model IAM permissions
  - [x] 6.1 Add `bedrock:InvokeModel` IAM policy to `waba-bedrock-webhook/infra/lib/waba-bedrock-stack.ts`
    - Add a new `PolicyStatement` granting the webhook Lambda `bedrock:InvokeModel` permission
    - Resource ARN should reference the inference profile and foundation model for `anthropic.claude-sonnet-4-6`
    - _Requirements: 8.1, 8.2_

- [x] 7. Final checkpoint - Ensure all tests pass
  - Ensure all tests pass, ask the user if questions arise.

## Notes

- Tasks marked with `*` are optional and can be skipped for faster MVP
- Each task references specific requirements for traceability
- Checkpoints ensure incremental validation
- Property tests validate universal correctness properties from the design document
- Unit tests validate specific examples and edge cases
- The project uses pytest with Hypothesis for property-based testing
- Test files are located in `waba-bedrock-webhook/tests/unit/` (unit) and `waba-bedrock-webhook/tests/unit/properties/` (property-based)

## Task Dependency Graph

```json
{
  "waves": [
    { "id": 0, "tasks": ["1.1", "2.1", "3.1"] },
    { "id": 1, "tasks": ["1.2", "1.3", "1.4", "2.2", "2.3", "3.2"] },
    { "id": 2, "tasks": ["5.1"] },
    { "id": 3, "tasks": ["5.2"] },
    { "id": 4, "tasks": ["5.3", "5.4", "5.5", "5.6", "5.7", "6.1"] }
  ]
}
```
