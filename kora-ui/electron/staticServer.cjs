const fs = require('node:fs');
const http = require('node:http');
const path = require('node:path');

const MIME = Object.freeze({
  '.html': 'text/html; charset=utf-8',
  '.js': 'text/javascript; charset=utf-8',
  '.mjs': 'text/javascript; charset=utf-8',
  '.css': 'text/css; charset=utf-8',
  '.json': 'application/json; charset=utf-8',
  '.svg': 'image/svg+xml',
  '.png': 'image/png',
  '.jpg': 'image/jpeg',
  '.jpeg': 'image/jpeg',
  '.wasm': 'application/wasm',
  '.task': 'application/octet-stream',
  '.woff2': 'font/woff2',
  '.ico': 'image/x-icon'
});

function inside(root, candidate) {
  const relative = path.relative(root, candidate);
  return relative === '' || (!relative.startsWith(`..${path.sep}`) && relative !== '..' && !path.isAbsolute(relative));
}

function startStaticServer(distDirectory, { host = '127.0.0.1', port = 0 } = {}) {
  const root = fs.realpathSync(path.resolve(distDirectory));
  const server = http.createServer((req, res) => {
    if (req.method !== 'GET' && req.method !== 'HEAD') {
      res.writeHead(405, { 'Allow': 'GET, HEAD', 'X-Content-Type-Options': 'nosniff' });
      return res.end();
    }

    let pathname;
    try {
      pathname = decodeURIComponent(new URL(req.url, 'http://127.0.0.1').pathname);
    } catch {
      res.writeHead(400, { 'X-Content-Type-Options': 'nosniff' });
      return res.end('Bad request');
    }
    if (pathname.includes('\0') || pathname.includes('\\')) {
      res.writeHead(400, { 'X-Content-Type-Options': 'nosniff' });
      return res.end('Bad path');
    }

    const relative = pathname.replace(/^\/+/, '') || 'index.html';
    if (relative.split('/').some(part => part.startsWith('.'))) {
      res.writeHead(404, { 'X-Content-Type-Options': 'nosniff' });
      return res.end('Not found');
    }
    let filePath = path.resolve(root, relative);
    if (!inside(root, filePath)) {
      res.writeHead(403, { 'X-Content-Type-Options': 'nosniff' });
      return res.end('Forbidden');
    }

    try {
      if (fs.statSync(filePath).isDirectory()) filePath = path.join(filePath, 'index.html');
    } catch {
      if (path.extname(relative)) {
        res.writeHead(404, { 'X-Content-Type-Options': 'nosniff' });
        return res.end('Not found');
      }
      filePath = path.join(root, 'index.html');
    }

    try {
      filePath = fs.realpathSync(filePath);
      if (!inside(root, filePath) || !fs.statSync(filePath).isFile()) {
        res.writeHead(403, { 'X-Content-Type-Options': 'nosniff' });
        return res.end('Forbidden');
      }
    } catch {
      res.writeHead(404, { 'X-Content-Type-Options': 'nosniff' });
      return res.end('Not found');
    }

    res.setHeader('Content-Type', MIME[path.extname(filePath).toLowerCase()] || 'application/octet-stream');
    res.setHeader('X-Content-Type-Options', 'nosniff');
    res.setHeader('Cache-Control', 'no-store');
    if (req.method === 'HEAD') return res.end();
    fs.createReadStream(filePath).on('error', () => {
      if (!res.headersSent) res.writeHead(500);
      res.end();
    }).pipe(res);
  });

  return new Promise((resolve, reject) => {
    server.once('error', reject);
    server.listen(port, host, () => {
      server.removeListener('error', reject);
      const address = server.address();
      resolve({
        server,
        url: `http://${host}:${address.port}/`,
        close: () => new Promise((done, fail) => server.close(error => error ? fail(error) : done()))
      });
    });
  });
}

module.exports = { startStaticServer };
