const fs = require('node:fs');
const path = require('node:path');
const { spawnSync } = require('node:child_process');

const testDirectory = path.resolve(process.cwd(), 'tests');
const files = fs.readdirSync(testDirectory)
  .filter(name => /^test_.*\.mjs$/.test(name))
  .sort()
  .map(name => path.join('tests', name));

if (files.length === 0) {
  console.error('No UI regression tests found under tests/*.mjs');
  process.exit(1);
}

const result = spawnSync(process.execPath, ['--test', ...files], { stdio: 'inherit' });
if (result.error) {
  console.error(result.error);
  process.exit(1);
}
process.exit(result.status ?? 1);
