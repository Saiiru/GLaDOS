function validateExternalUrl(value) {
  if (typeof value !== 'string' || value.length === 0 || value.length > 2048) {
    throw new TypeError('External URL must be a short string');
  }
  let parsed;
  try {
    parsed = new URL(value);
  } catch {
    throw new TypeError('External URL is invalid');
  }
  if (!['http:', 'https:'].includes(parsed.protocol)) {
    throw new TypeError('Only HTTP and HTTPS URLs may be opened externally');
  }
  if (parsed.username || parsed.password) {
    throw new TypeError('URLs containing credentials are not allowed');
  }
  return parsed.href;
}

module.exports = { validateExternalUrl };
