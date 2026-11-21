const fs = require('node:fs');
const path = require('node:path');
const { randomUUID } = require('node:crypto');

async function request(fetchImpl, url, options) {
  for (let attempt = 0; attempt < 12; attempt++) {
    const response = await fetchImpl(url, { ...options, signal: AbortSignal.timeout(10000) });
    if (response.ok) return response;
    if (![401, 403, 429, 500, 502, 503, 504].includes(response.status) || attempt === 11) {
      throw new Error(`TLS credential request failed (${response.status})`);
    }
    await new Promise(resolve => setTimeout(resolve, Math.min(1000 * 2 ** attempt, 10000)));
  }
}

async function installTls(secret, directory, fetchImpl = fetch) {
  if (typeof secret !== 'string' || !/^projects\/[A-Za-z0-9.:-]+\/secrets\/[A-Za-z0-9_-]+\/versions\/(latest|[0-9]+)$/.test(secret)) {
    throw new Error('Invalid sandbox TLS secret reference');
  }
  const metadata = await request(fetchImpl,
    'http://metadata.google.internal/computeMetadata/v1/instance/service-accounts/default/token',
    { headers: { 'Metadata-Flavor': 'Google' } });
  let token;
  try { token = (await metadata.json()).access_token; } catch { throw new Error('Invalid Workload Identity response'); }
  if (typeof token !== 'string' || !token) throw new Error('Invalid Workload Identity response');
  const response = await request(fetchImpl, `https://secretmanager.googleapis.com/v1/${secret}:access`,
    { headers: { Authorization: `Bearer ${token}` } });
  let bundle;
  try {
    const payload = await response.json();
    bundle = JSON.parse(Buffer.from(payload.payload.data, 'base64').toString('utf8'));
  } catch { throw new Error('Invalid sandbox TLS bundle'); }
  if (!bundle || typeof bundle !== 'object' || Array.isArray(bundle) ||
      !['cert', 'key', 'ca_cert'].every(key => typeof bundle[key] === 'string' && bundle[key].trim())) {
    throw new Error('Invalid sandbox TLS bundle');
  }
  fs.mkdirSync(directory, { recursive: true, mode: 0o700 });
  for (const [file, field] of Object.entries({ 'client.pem': 'cert', 'client.key': 'key', 'server-ca.pem': 'ca_cert' })) {
    const temporary = path.join(directory, `.${file}.${randomUUID()}`);
    try {
      fs.writeFileSync(temporary, bundle[field], { mode: 0o400 });
      fs.renameSync(temporary, path.join(directory, file));
    } finally {
      if (fs.existsSync(temporary)) fs.unlinkSync(temporary);
    }
  }
}

module.exports = { installTls };
if (process.env.SANDBOX_TLS_BOOTSTRAP === '1') {
  installTls(process.env.SANDBOX_TLS_SECRET, '/etc/sandbox-tls').catch(() => {
    console.error('Sandbox TLS initialization failed; check Workload Identity and the referenced Secret Manager version');
    process.exitCode = 1;
  });
}
