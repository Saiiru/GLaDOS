import test from 'node:test';
import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
const require = createRequire(import.meta.url);
const { resolveBackendPython } = require('../electron/backendPython.cjs');

test('uses an explicit ADA_PYTHON override first', () => {
  assert.equal(resolveBackendPython({ env: { ADA_PYTHON: '/opt/ada/python' }, home: '/home/test', exists: () => false }), '/opt/ada/python');
});

test('prefers the ADA user-local virtualenv when present', () => {
  const expected = '/home/test/.local/share/ada-v2-local/app-venv/bin/python';
  assert.equal(resolveBackendPython({ env: {}, home: '/home/test', exists: path => path === expected }), expected);
});

test('uses platform Python fallback only when no isolated interpreter exists', () => {
  assert.equal(resolveBackendPython({ env: {}, home: '/home/test', platform: 'linux', exists: () => false }), 'python3');
  assert.equal(resolveBackendPython({ env: {}, home: 'C:\\Users\\Test', platform: 'win32', exists: () => false }), 'python');
});
