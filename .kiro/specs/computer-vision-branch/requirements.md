# Requirements Document

## Introduction

This feature adds a computer vision branch to the WhatsApp webhook system. When a user expresses intent to analyze an image (via keywords or Bedrock Agent suggestion), the system enters a "vision" mode. In this mode, the user is prompted to send an image. Upon receiving the image, the Lambda downloads it from the WhatsApp Media API, sends it to a multimodal model (Claude with vision via Bedrock), and returns a detailed description of the image content back to the user via WhatsApp. All interactions are in Spanish.

## Glossary

- **Handler**: The Lambda function entry point (`handler.py`) that orchestrates message processing, routing, and response delivery.
- **Session_Manager**: The module (`session_manager.py`) that manages DynamoDB-backed conversation sessions, mapping phone numbers to session state.
- **WhatsApp_Client**: The module (`whatsapp.py`) that communicates with the WhatsApp Cloud API for sending messages and downloading media.
- **Vision_Analyzer**: A new module responsible for invoking the Bedrock Runtime multimodal model with image data and returning a textual description.
- **Intent_Detector**: Logic within the Handler that determines whether a user message expresses intent to enter computer vision mode, using keyword matching as fast-path and Bedrock Agent as fallback.
- **SessionTable**: The existing DynamoDB table with `phone_number` as partition key and TTL, extended with a `mode` field.
- **Vision_Mode**: A session state where `mode` is set to `"vision"`, indicating the system is awaiting an image from the user.
- **WhatsApp_Media_API**: The Meta Graph API endpoints for retrieving media URLs and downloading binary media content.

## Requirements

### Requirement 1: Vision Intent Detection

**User Story:** As a WhatsApp user, I want to tell the system I want to analyze an image using natural keywords in Spanish, so that the system enters vision mode without requiring exact commands.

#### Acceptance Criteria

1. WHEN a text message containing any of the keywords "visión computacional", "analizar imagen", or "describir foto" (case-insensitive) is received, THE Intent_Detector SHALL set the session mode to "vision" and respond with a prompt asking the user to send an image.
2. WHEN a text message does not match any vision keyword and the Bedrock Agent response suggests entering vision mode, THE Handler SHALL set the session mode to "vision" and respond with a prompt asking the user to send an image.
3. WHEN a text message does not match any vision keyword and the Bedrock Agent does not suggest vision mode, THE Handler SHALL process the message through the standard Bedrock Agent flow.

### Requirement 2: Session Mode Management

**User Story:** As a system operator, I want the session to track whether a user is in vision mode, so that subsequent image messages are routed to the vision analysis pipeline.

#### Acceptance Criteria

1. WHEN the Intent_Detector activates vision mode, THE Session_Manager SHALL store the value "vision" in the `mode` field of the SessionTable record for the corresponding phone number.
2. WHEN vision analysis completes successfully, THE Session_Manager SHALL reset the `mode` field to null for the corresponding phone number.
3. IF vision analysis fails, THEN THE Session_Manager SHALL reset the `mode` field to null for the corresponding phone number.
4. WHILE no explicit mode is set, THE Session_Manager SHALL treat the session as being in default mode (standard Bedrock Agent routing).

### Requirement 3: Image Reception and Routing

**User Story:** As a WhatsApp user in vision mode, I want to send any image and have it analyzed automatically, so that I receive a description without additional steps.

#### Acceptance Criteria

1. WHILE the session mode is "vision" and the user sends a message of type "image", THE Handler SHALL route the message to the vision analysis pipeline instead of the Bedrock Agent.
2. WHILE the session mode is "vision" and the user sends a text message instead of an image, THE Handler SHALL respond with a message reminding the user to send an image.
3. WHEN the session mode is not "vision" and the user sends an image, THE Handler SHALL process the image message through the standard Bedrock Agent flow (existing placeholder behavior).

### Requirement 4: Image Download from WhatsApp Media API

**User Story:** As the system, I need to download the binary image data from WhatsApp so that it can be sent to the multimodal model for analysis.

#### Acceptance Criteria

1. WHEN an image message is received in vision mode, THE WhatsApp_Client SHALL retrieve the media URL by calling the WhatsApp Media API with the image media ID.
2. WHEN the media URL is obtained, THE WhatsApp_Client SHALL download the binary image content using an authenticated HTTP GET request.
3. IF the media URL retrieval fails, THEN THE WhatsApp_Client SHALL raise an error that the Handler can catch and report to the user.
4. IF the image download fails, THEN THE WhatsApp_Client SHALL raise an error that the Handler can catch and report to the user.

### Requirement 5: Multimodal Vision Analysis

**User Story:** As a WhatsApp user, I want to receive a detailed description of the image I sent, so that I understand what the model sees in the image.

#### Acceptance Criteria

1. WHEN image binary data is available, THE Vision_Analyzer SHALL encode the image as base64 and invoke the Bedrock Runtime `invoke_model` API with a multimodal Claude model.
2. THE Vision_Analyzer SHALL include a Spanish-language prompt instructing the model to provide a detailed description of the image content.
3. WHEN the model returns a response, THE Vision_Analyzer SHALL extract the text content and return it to the Handler.
4. IF the Bedrock Runtime invocation fails or times out, THEN THE Vision_Analyzer SHALL raise an error that the Handler can catch and report to the user.

### Requirement 6: Response Delivery and Mode Reset

**User Story:** As a WhatsApp user, I want to receive the image description as a WhatsApp message and have the system return to normal mode, so that subsequent messages are handled normally.

#### Acceptance Criteria

1. WHEN the Vision_Analyzer returns a description, THE Handler SHALL send the description text to the user via the WhatsApp_Client.
2. WHEN the description is sent successfully, THE Handler SHALL reset the session mode to null via the Session_Manager.
3. IF the vision analysis fails at any stage (download, model invocation, or send), THEN THE Handler SHALL send an error message in Spanish to the user and reset the session mode to null.

### Requirement 7: Vision Mode Entry Confirmation

**User Story:** As a WhatsApp user, I want to receive a clear confirmation when the system enters vision mode, so that I know to send an image.

#### Acceptance Criteria

1. WHEN the session mode is set to "vision", THE Handler SHALL send a Spanish-language message to the user indicating that vision mode is active and requesting an image upload.
2. THE Handler SHALL send the vision mode confirmation message before waiting for the image.

### Requirement 8: IAM Permissions for Multimodal Model

**User Story:** As a system operator, I want the Lambda function to have the necessary IAM permissions to invoke the multimodal model, so that vision analysis requests are authorized.

#### Acceptance Criteria

1. THE CDK stack SHALL grant the webhook Lambda function an IAM policy with `bedrock:InvokeModel` permission for the multimodal Claude model resource.
2. THE CDK stack SHALL specify the model resource ARN using the same region as the stack deployment.

### Requirement 9: Image Message Metadata Extraction

**User Story:** As the system, I need to extract the media ID and MIME type from incoming image messages, so that the correct media can be downloaded and processed.

#### Acceptance Criteria

1. WHEN an image message is received, THE Handler SHALL extract the `id` field (media ID) from the image message payload.
2. WHEN an image message is received, THE Handler SHALL extract the `mime_type` field from the image message payload for use in the multimodal model request.
