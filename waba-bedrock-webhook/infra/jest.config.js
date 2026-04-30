const path = require('path');

/** @type {import('jest').Config} */
module.exports = {
  testEnvironment: 'node',
  roots: ['<rootDir>/../tests/infra'],
  testMatch: ['**/*.test.ts'],
  transform: {
    '^.+\\.tsx?$': ['ts-jest', {
      tsconfig: path.resolve(__dirname, 'tsconfig.test.json'),
    }],
  },
  modulePaths: [path.resolve(__dirname, 'node_modules')],
};
