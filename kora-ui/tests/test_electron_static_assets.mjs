import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import path from 'node:path';
const root=path.resolve(path.dirname(fileURLToPath(import.meta.url)),'..');

test('production Electron uses the local asset server instead of file URLs',()=>{
  const main=readFileSync(path.join(root,'electron/main.js'),'utf8');
  assert.match(main,/startStaticServer/);
  assert.match(main,/staticServerHandle\.url/);
  assert.doesNotMatch(main,/mainWindow\.loadFile\(/);
});
