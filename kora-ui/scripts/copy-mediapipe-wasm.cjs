const fs = require('node:fs');
const path = require('node:path');

const REQUIRED_FILES = Object.freeze([
  'vision_wasm_internal.js',
  'vision_wasm_internal.wasm',
  'vision_wasm_nosimd_internal.js',
  'vision_wasm_nosimd_internal.wasm'
]);

function copyMediaPipeWasm({ sourceDir, destinationDir } = {}) {
  const source = path.resolve(sourceDir || path.join(__dirname, '../node_modules/@mediapipe/tasks-vision/wasm'));
  const destination = path.resolve(destinationDir || path.join(__dirname, '../public/mediapipe/wasm'));
  const missing = REQUIRED_FILES.filter(file => {
    const candidate = path.join(source, file);
    return !fs.existsSync(candidate) || !fs.statSync(candidate).isFile();
  });
  if (missing.length) throw new Error(`MediaPipe WASM files missing from ${source}: ${missing.join(', ')}`);
  fs.mkdirSync(destination, { recursive: true });
  for (const file of REQUIRED_FILES) fs.copyFileSync(path.join(source, file), path.join(destination, file));
  return [...REQUIRED_FILES];
}

if (require.main === module) {
  const copied = copyMediaPipeWasm();
  console.log(`Copied ${copied.length} local MediaPipe WASM assets.`);
}

module.exports = { copyMediaPipeWasm, REQUIRED_FILES };
