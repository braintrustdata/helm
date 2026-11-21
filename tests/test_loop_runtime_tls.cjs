const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { installTls } = require('../braintrust/files/loop-runtime-tls.js');
const reference = 'projects/test-project/secrets/loop-client-tls/versions/latest';
const valid = { cert: 'test-client-cert', key: 'test-client-key', ca_cert: 'test-server-ca' };
function responses(bundle, calls = []) {
  return async (url, options) => {
    calls.push({ url, options });
    if (url.startsWith('http://metadata.google.internal/')) return { ok: true, json: async () => ({ access_token: 'test-token' }) };
    return { ok: true, json: async () => ({ payload: { data: Buffer.from(JSON.stringify(bundle)).toString('base64') } }) };
  };
}
function directory(t) {
  const result = fs.mkdtempSync(path.join(os.tmpdir(), 'loop-tls-test-'));
  t.after(() => fs.rmSync(result, { recursive: true, force: true }));
  return result;
}
test('loads only the referenced client bundle using Workload Identity', async t => {
  const root = directory(t), calls = [];
  await installTls(reference, root, responses(valid, calls));
  assert.equal(calls.length, 2);
  assert.equal(calls[0].options.headers['Metadata-Flavor'], 'Google');
  assert.equal(calls[1].url, `https://secretmanager.googleapis.com/v1/${reference}:access`);
  assert.equal(calls[1].options.headers.Authorization, 'Bearer test-token');
  for (const [file, field] of Object.entries({ 'client.pem': 'cert', 'client.key': 'key', 'server-ca.pem': 'ca_cert' })) {
    assert.equal(fs.readFileSync(path.join(root, file), 'utf8'), valid[field]);
    assert.equal(fs.statSync(path.join(root, file)).mode & 0o777, 0o400);
  }
  assert.deepEqual(fs.readdirSync(root).sort(), ['client.key', 'client.pem', 'server-ca.pem']);
});
test('can rerun after read-only credential files were created', async t => {
  const root = directory(t);
  await installTls(reference, root, responses(valid));
  await installTls(reference, root, responses({ ...valid, key: 'replacement-client-key' }));
  assert.equal(fs.readFileSync(path.join(root, 'client.key'), 'utf8'), 'replacement-client-key');
  assert.equal(fs.statSync(path.join(root, 'client.key')).mode & 0o777, 0o400);
});
test('invalid references make no credential requests', async t => {
  const root = directory(t);
  for (const ref of [undefined, '', 'https://example.com/secret', 'projects/p/secrets/s/versions/../other', 'projects/p/secrets/s/versions/latest?x=1']) {
    await assert.rejects(installTls(ref, root, () => { throw new Error('must not fetch'); }), /Invalid sandbox TLS secret reference/);
  }
});
test('malformed bundles cannot create partial credential files', async t => {
  const root = directory(t);
  for (const bad of [null, [], {}, { ...valid, cert: '' }, { ...valid, key: null }, { ...valid, ca_cert: 123 }, { ...valid, key: ' ' }]) {
    await assert.rejects(installTls(reference, root, responses(bad)), /Invalid sandbox TLS bundle/);
    assert.deepEqual(fs.readdirSync(root), []);
  }
});
test('retries IAM propagation and fails without exposing payloads', async t => {
  const root = directory(t);
  t.mock.method(globalThis, 'setTimeout', callback => { queueMicrotask(callback); return {}; });
  let attempts = 0;
  const fetchImpl = async url => {
    if (url.startsWith('http://metadata.')) return { ok: true, json: async () => ({ access_token: 'test-token' }) };
    attempts++;
    return { ok: false, status: 403, json: async () => ({ private: 'must not appear' }) };
  };
  await assert.rejects(installTls(reference, root, fetchImpl), { message: 'TLS credential request failed (403)' });
  assert.equal(attempts, 12);
  assert.deepEqual(fs.readdirSync(root), []);
});
