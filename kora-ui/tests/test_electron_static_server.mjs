import test from 'node:test';
import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
import { mkdtempSync, mkdirSync, readFileSync, writeFileSync } from 'node:fs';
import http from 'node:http';
import os from 'node:os';
import path from 'node:path';
const require = createRequire(import.meta.url);
const { startStaticServer } = require('../electron/staticServer.cjs');

test('serves local files on loopback, rejects traversal, and closes', async () => {
  const parent = mkdtempSync(path.join(os.tmpdir(), 'ada-static-'));
  const root = path.join(parent, 'dist');
  mkdirSync(path.join(root, 'assets'), { recursive: true });
  mkdirSync(path.join(parent, 'private'), { recursive: true });
  writeFileSync(path.join(root, 'index.html'), '<h1>ADA local</h1>');
  writeFileSync(path.join(root, 'assets', 'app.js'), 'console.log("local")');
  writeFileSync(path.join(parent, 'private', 'secret.txt'), 'not served');

  const service = await startStaticServer(root, { host: '127.0.0.1', port: 0 });
  try {
    assert.match(service.url, /^http:\/\/127\.0\.0\.1:/);
    const page = await fetch(service.url);
    assert.equal(await page.text(), '<h1>ADA local</h1>');

    const asset = await fetch(new URL('assets/app.js', service.url));
    assert.equal(await asset.text(), 'console.log("local")');

    const address = new URL(service.url);
    const traversal = await new Promise((resolve, reject) => {
      const req = http.request({ hostname: address.hostname, port: address.port,
        path: '/%2e%2e%2fprivate%2fsecret.txt', method: 'GET' }, res => {
          let body = '';
          res.on('data', chunk => { body += chunk; });
          res.on('end', () => resolve({ status: res.statusCode, body }));
        });
      req.on('error', reject); req.end();
    });
    assert.ok([403, 404].includes(traversal.status));
    assert.notEqual(traversal.body, 'not served');
  } finally {
    await service.close();
  }
});
