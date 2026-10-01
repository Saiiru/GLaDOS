import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import { runInNewContext } from 'node:vm';
import path from 'node:path';
const require = createRequire(import.meta.url);
const ROOT = path.resolve(import.meta.url.replace('file://',''), '..', '..');

test('Electron renderer is isolated and uses the preload bridge', () => {
  const source = readFileSync(path.join(ROOT, 'electron/main.js'), 'utf8');
  assert.match(source, /nodeIntegration:\s*false/);
  assert.match(source, /contextIsolation:\s*true/);
  assert.match(source, /sandbox:\s*true/);
  assert.match(source, /preload:\s*path\.join\(__dirname, 'preload\.cjs'\)/);
  assert.match(source, /randomBytes\(32\)\.toString\('hex'\)/);
  assert.match(source, /ADA_SOCKET_TOKEN: socketToken/);
  assert.match(source, /ADA_READY_NONCE: backendReadyNonce/);
  assert.match(source, /ADA_BACKEND_READY:/);
  assert.match(source, /startPythonBackend\(\)\s*\.then\(\(\) => waitForBackend\(\)\)/);
  assert.match(source, /senderFrame !== mainWindow\.webContents\.mainFrame/);
  assert.match(source, /new URL\(candidate\)/);
  assert.match(source, /parsedUrl\.origin === allowedOrigin/);
  assert.match(source, /will-redirect/);
});

test('development server binds loopback and never silently switches to an occupied port', () => {
  const source = readFileSync(path.join(ROOT, 'vite.config.mjs'), 'utf8');
  assert.match(source, /host:\s*'127\.0\.0\.1'/);
  assert.match(source, /port:\s*5173/);
  assert.match(source, /strictPort:\s*true/);
});

test('preload exposes only window controls and safe URL opening', () => {
  const source = readFileSync(path.join(ROOT, 'electron/preload.cjs'), 'utf8');
  const exposed = {};
  const sent = [];
  const invoked = [];
  runInNewContext(source, {
    require: (name) => {
      assert.equal(name, 'electron');
      return {
        contextBridge: { exposeInMainWorld: (key, value) => { exposed[key] = value; } },
        ipcRenderer: {
          send: (...args) => sent.push(args),
          invoke: (...args) => { invoked.push(args); return Promise.resolve(true); },
        },
      };
    },
    Object,
  });
  assert.deepEqual(Object.keys(exposed.adaDesktop).sort(), ['getSocketAuth', 'openExternal', 'window']);
  assert.equal('ipcRenderer' in exposed.adaDesktop, false);
  exposed.adaDesktop.window.minimize();
  exposed.adaDesktop.window.maximize();
  exposed.adaDesktop.window.close();
  exposed.adaDesktop.openExternal('https://example.invalid');
  exposed.adaDesktop.getSocketAuth();
  assert.deepEqual(sent.map((item) => item[0]), ['window-minimize', 'window-maximize', 'window-close']);
  assert.deepEqual(invoked[0], ['ada:open-external', 'https://example.invalid']);
  assert.deepEqual(invoked[1], ['ada:socket-auth']);
});

test('external URL bridge rejects non-http schemes', () => {
  const { validateExternalUrl } = require('../electron/safeExternalUrl.cjs');
  assert.equal(validateExternalUrl('https://example.invalid/path'), 'https://example.invalid/path');
  assert.equal(validateExternalUrl('http://127.0.0.1:8000'), 'http://127.0.0.1:8000/');
  for (const value of ['file:///etc/passwd', 'javascript:alert(1)', 'data:text/html,hi', 'https://user:pass@example.invalid']) {
    assert.throws(() => validateExternalUrl(value));
  }
});

test('renderer has no direct Electron module access', () => {
  const app = readFileSync(path.join(ROOT, 'src/App.jsx'), 'utf8');
  const printer = readFileSync(path.join(ROOT, 'src/components/PrinterWindow.jsx'), 'utf8');
  assert.doesNotMatch(app, /window\.require\(['"]electron['"]\)/);
  assert.doesNotMatch(printer, /window\.require\(['"]electron['"]\)/);
});
