import assert from 'node:assert/strict';
import { createServer as createNodeServer, request } from 'node:http';
import { after, before, test } from 'node:test';
import { Client, StreamableHTTPClientTransport } from '@modelcontextprotocol/client';
import { CoreClient } from '../dist/core.js';
import { createHttpServer } from '../dist/http.js';

const token = 'test-token-only';
let core, listener, url;
before(async () => {
  core = createNodeServer((req, res) => {
    if (req.headers.authorization !== `Bearer ${token}`) { res.writeHead(401).end(); return; }
    res.writeHead(200, { 'content-type': 'application/json' });
    res.end(JSON.stringify({ connected: true, simulated: false, client: 'device', device: { battery_pct: 42 }, halted: false }));
  });
  await new Promise(resolve => core.listen(0, '127.0.0.1', resolve));
  const coreClient = new CoreClient({ url: `http://127.0.0.1:${core.address().port}`, token, agent: '' });
  listener = createHttpServer(coreClient, token);
  await new Promise(resolve => listener.listen(0, '127.0.0.1', resolve));
  url = `http://127.0.0.1:${listener.address().port}/mcp`;
});
after(async () => { await new Promise(resolve => listener.close(resolve)); await new Promise(resolve => core.close(resolve)); });

test('HTTP rejects missing and bad authentication before accepting MCP requests', async () => {
  for (const headers of [{}, { Authorization: 'Bearer wrong' }]) {
    const res = await fetch(url, { method: 'POST', headers });
    assert.equal(res.status, 401);
  }
});

test('authenticated MCP lists status tools and reports both device states', async () => {
  const client = new Client({ name: 'test', version: '1' });
  await client.connect(new StreamableHTTPClientTransport(new URL(url), { requestInit: { headers: { Authorization: `Bearer ${token}` } } }));
  try {
    const { tools } = await client.listTools();
    assert.deepEqual(tools.map(t => t.name).sort(), ['piblo_device_state', 'piblo_devices', 'piblo_status']);
    const result = await client.callTool({ name: 'piblo_devices', arguments: {} });
    const data = JSON.parse(result.content[0].text);
    assert.equal(data.devices[0].state, 'connected');
    assert.equal(data.devices[1].state, 'unknown');
  } finally { await client.close(); }
});

test('no token cannot start the HTTP server', () => {
  assert.throws(() => createHttpServer(new CoreClient({ url: 'http://127.0.0.1:9', token: '', agent: '' }), ''), /token/i);
});

test('unreachable Core fails closed with a clear tool error', async () => {
  const offline = createHttpServer(new CoreClient({ url: 'http://127.0.0.1:9', token, agent: '' }), token);
  await new Promise(resolve => offline.listen(0, '127.0.0.1', resolve));
  const client = new Client({ name: 'test-offline', version: '1' });
  try {
    await client.connect(new StreamableHTTPClientTransport(new URL(`http://127.0.0.1:${offline.address().port}/mcp`), { requestInit: { headers: { Authorization: `Bearer ${token}` } } }));
    const result = await client.callTool({ name: 'piblo_devices', arguments: {} });
    assert.equal(result.isError, true);
    assert.equal(JSON.parse(result.content[0].text).error, 'core_unreachable');
  } finally { await client.close(); await new Promise(resolve => offline.close(resolve)); }
});

test('rejects a foreign Host header even with a valid token', async () => {
  const status = await new Promise((resolve, reject) => {
    const req = request(url, { method: 'POST', headers: { Authorization: `Bearer ${token}`, Host: 'attacker.example' } }, res => { res.resume(); resolve(res.statusCode); });
    req.on('error', reject); req.end();
  });
  assert.equal(status, 403);
});

test('reports a configured display portal as reachable but identity unverified', async () => {
  const portal = createNodeServer((req, res) => { res.writeHead(200, { 'content-type': 'text/html' }); res.end('<title>Set up this Piblo board.</title>'); });
  await new Promise(resolve => portal.listen(0, '127.0.0.1', resolve));
  const configured = createHttpServer(new CoreClient({ url: `http://127.0.0.1:${core.address().port}`, token, agent: '', displayUrl: `http://127.0.0.1:${portal.address().port}` }), token);
  await new Promise(resolve => configured.listen(0, '127.0.0.1', resolve));
  const client = new Client({ name: 'display-test', version: '1' });
  try {
    await client.connect(new StreamableHTTPClientTransport(new URL(`http://127.0.0.1:${configured.address().port}/mcp`), { requestInit: { headers: { Authorization: `Bearer ${token}` } } }));
    const result = await client.callTool({ name: 'piblo_devices', arguments: {} });
    assert.equal(JSON.parse(result.content[0].text).devices[1].state, 'reachable_unverified');
  } finally { await client.close(); await new Promise(resolve => configured.close(resolve)); await new Promise(resolve => portal.close(resolve)); }
});

test('display probe rejects public addresses and reports offline', async () => {
  const denied = new CoreClient({ url: 'http://127.0.0.1:9', token, agent: '', displayUrl: 'http://8.8.8.8/' });
  assert.equal((await denied.displayStatus()).state, 'error');
  const offline = new CoreClient({ url: 'http://127.0.0.1:9', token, agent: '', displayUrl: 'http://127.0.0.1:9' });
  assert.equal((await offline.displayStatus()).state, 'offline');
});

test('Core authentication failure remains a tool error through HTTP', async () => {
  const wrong = 'wrong-core-token';
  const server = createHttpServer(new CoreClient({ url: `http://127.0.0.1:${core.address().port}`, token: wrong, agent: '' }), wrong);
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  const client = new Client({ name: 'bad-core-token', version: '1' });
  try {
    await client.connect(new StreamableHTTPClientTransport(new URL(`http://127.0.0.1:${server.address().port}/mcp`), { requestInit: { headers: { Authorization: `Bearer ${wrong}` } } }));
    const result = await client.callTool({ name: 'piblo_devices', arguments: {} });
    assert.equal(result.isError, true);
    assert.equal(JSON.parse(result.content[0].text).error, 'unauthorized');
  } finally { await client.close(); await new Promise(resolve => server.close(resolve)); }
});

test('display portal identity marker may arrive across network chunks', async () => {
  const portal = createNodeServer((req, res) => {
    res.writeHead(200, { 'content-type': 'text/html' });
    res.write('<title>Set up this ');
    setTimeout(() => res.end('Piblo board.</title>'), 20);
  });
  await new Promise(resolve => portal.listen(0, '127.0.0.1', resolve));
  try {
    const client = new CoreClient({ url: 'http://127.0.0.1:9', token, agent: '', displayUrl: `http://127.0.0.1:${portal.address().port}` });
    assert.equal((await client.displayStatus()).state, 'reachable_unverified');
  } finally { await new Promise(resolve => portal.close(resolve)); }
});

test('malformed Core connectivity cannot be reported as connected', async () => {
  const badCore = createNodeServer((req, res) => {
    res.writeHead(200, { 'content-type': 'application/json' });
    res.end(JSON.stringify({ connected: 'false', simulated: false, device: {} }));
  });
  await new Promise(resolve => badCore.listen(0, '127.0.0.1', resolve));
  const server = createHttpServer(new CoreClient({ url: `http://127.0.0.1:${badCore.address().port}`, token, agent: '' }), token);
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  const client = new Client({ name: 'invalid-status', version: '1' });
  try {
    await client.connect(new StreamableHTTPClientTransport(new URL(`http://127.0.0.1:${server.address().port}/mcp`), { requestInit: { headers: { Authorization: `Bearer ${token}` } } }));
    for (const name of ['piblo_devices', 'piblo_status', 'piblo_device_state']) {
      const result = await client.callTool({ name, arguments: {} });
      assert.equal(result.isError, true);
      assert.equal(JSON.parse(result.content[0].text).error, 'invalid_status');
    }
  } finally { await client.close(); await new Promise(resolve => server.close(resolve)); await new Promise(resolve => badCore.close(resolve)); }
});
