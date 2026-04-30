#!/usr/bin/env node
import * as cdk from 'aws-cdk-lib';
import { WabaBedrockStack } from '../lib/waba-bedrock-stack';

const app = new cdk.App();

new WabaBedrockStack(app, 'WabaBedrockWebhookStack', {
  env: {
    region: 'us-east-1',
  },
});
