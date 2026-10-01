import test from 'node:test';
import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
import { mkdtempSync, mkdirSync, readFileSync, writeFileSync } from 'node:fs';
import os from 'node:os';
import path from 'node:path';
const require = createRequire(import.meta.url);
const { copyMediaPipeWasm } = require('../scripts/copy-mediapipe-wasm.cjs');

test('copies only the installed MediaPipe WASM loader and binaries', () => {
  const root = mkdtempSync(path.join(os.tmpdir(), 'ada-mediapipe-'));
  const source = path.join(root, 'package', 'wasm');
  const destination = path.join(root, 'public', 'mediapipe', 'wasm');
  mkdirSync(source, { recursive: true });
  const required = [
    'vision_wasm_internal.js',
    'vision_wasm_internal.wasm',
    'vision_wasm_nosimd_internal.js',
    'vision_wasm_nosimd_internal.wasm'
  ];
  for (const file of required) writeFileSync(path.join(source, file), `asset:${file}`);
  writeFileSync(path.join(source, 'unused.txt'), 'do not copy');

  const copied = copyMediaPipeWasm({ sourceDir: source, destinationDir: destination });
  assert.deepEqual(copied.sort(), required.sort());
  for (const file of required) assert.equal(readFileSync(path.join(destination, file), 'utf8'), `asset:${file}`);
});
