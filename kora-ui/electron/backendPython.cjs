const fs = require('fs');
const os = require('os');
const path = require('path');

function resolveBackendPython({
  env = process.env,
  platform = process.platform,
  home = os.homedir(),
  exists = fs.existsSync,
} = {}) {
  const override = typeof env.ADA_PYTHON === 'string' ? env.ADA_PYTHON.trim() : '';
  if (override) return override;

  const relative = platform === 'win32'
    ? path.join('.local', 'share', 'ada-v2-local', 'app-venv', 'Scripts', 'python.exe')
    : path.join('.local', 'share', 'ada-v2-local', 'app-venv', 'bin', 'python');
  const isolatedPython = path.join(home, relative);
  if (exists(isolatedPython)) return isolatedPython;

  return platform === 'win32' ? 'python' : 'python3';
}

module.exports = { resolveBackendPython };
