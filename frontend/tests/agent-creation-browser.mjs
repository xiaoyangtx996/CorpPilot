import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { spawn } from 'node:child_process';
import { chromium } from 'playwright';
import { createServer } from 'vite';

const repo = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..');
const root = process.env.CORPPILOT_BROWSER_OUTPUT || os.tmpdir(); fs.mkdirSync(root, { recursive: true });
const out = fs.mkdtempSync(path.join(root, 'corppilot-agent-creation-')), fixture = path.join(out, 'fixture');
const python = process.env.CORPPILOT_PYTHON || path.join(repo, '.venv', process.platform === 'win32' ? 'Scripts/python.exe' : 'bin/python');
const child = spawn(python, [path.join(repo, 'tests/browser/goal_fixture.py'), '--data-dir', fixture], { cwd: repo, windowsHide: true });
const logs = fs.createWriteStream(path.join(out, 'fixture.log')); child.stdout.pipe(logs, { end: false }); child.stderr.pipe(logs, { end: false });
let childExit, spawnError, browser, page, devServer;
child.on('error', e => { spawnError = e; }); child.on('exit', (code, signal) => { childExit = { code, signal }; });
const report = { passed: false, output: out, pageErrors: [], boundaries: [] };
const key = 'corppilot.agent-create-pending.v1';
async function until(check, name, timeout = 30000) { const end = Date.now() + timeout; while (Date.now() < end) { if (await check()) return; await new Promise(r => setTimeout(r, 100)); } throw Error('Timed out: ' + name); }
try {
  await until(() => { if (spawnError || childExit) throw Error('Fixture exited'); return fs.existsSync(path.join(fixture, 'manifest.json')); }, 'startup');
  const manifest = JSON.parse(fs.readFileSync(path.join(fixture, 'manifest.json'))), baseURL = `http://127.0.0.1:${manifest.port}`; let uiURL = baseURL;
  browser = await chromium.launch({ headless: true }); const context = await browser.newContext({ viewport: { width: 1280, height: 960 } });
  page = await context.newPage(); page.setDefaultTimeout(15000); const posts = [], otherWrites = [];
  let mode = 'normal', blockRead = false;
  page.on('pageerror', e => report.pageErrors.push(String(e)));
  await page.route('**/api/workbench/**', async route => {
    const request = route.request(), url = request.url();
    if (request.method() !== 'GET') {
      if (request.method() === 'POST' && url.endsWith('/agents')) {
        posts.push(request.postDataJSON());
        if (mode === 'drop-before') return route.abort();
        if (mode === 'reject') return route.fulfill({ status: 400, contentType: 'application/json', body: JSON.stringify({ error: 'F82 explicit rejection' }) });
        if (mode === 'drop-after') { const result = await route.fetch(); if (result.status() !== 201) { report.routeFailure = { status: result.status(), body: await result.text(), payload: request.postDataJSON() }; return route.fulfill({ response: result }); } return route.abort(); }
      } else { otherWrites.push(url); return route.abort(); }
    }
    if (blockRead && /\/agent-requests\//.test(url)) return route.fulfill({ status: 503, contentType: 'application/json', body: JSON.stringify({ error: 'F82 receipt unavailable' }) });
    return route.continue();
  });
  async function api(url, method = 'GET', data) {
    const response = await context.request.fetch(baseURL + '/api/workbench' + url, { method, data, headers: { Authorization: `Bearer ${manifest.access_token}` } });
    assert.equal(response.status(), 200); return response.json();
  }
  const editor = () => page.getByRole('dialog', { name: '创建 Agent', exact: true });
  async function open() { await page.getByRole('button', { name: '新建', exact: true }).click(); await editor().waitFor(); }
  async function start(name) { await open(); await editor().getByLabel('名字', { exact: true }).fill(name); await editor().getByRole('button', { name: '保存', exact: true }).click(); }
  async function idle() { await until(async () => !(await editor().getByRole('button', { name: '只读核对原身份请求', exact: true }).isDisabled()), 'pending idle'); }
  async function pending() { return page.evaluate(k => JSON.parse(sessionStorage.getItem(k)), key); }
  async function recovered() { await until(async () => await editor().count() === 0, 'recovered'); assert.equal(await pending(), null); }
  await page.goto(`${uiURL}/#access_token=${manifest.access_token}`);
  const initialCount = (await api('/agents')).length;
  await start('F82 正常创建'); await recovered(); assert.equal((await api('/agents')).length, initialCount + 1);
  assert.match(posts[0].request_id, /^[0-9a-f-]{36}$/); report.boundaries.push('normal-idempotent-create');

  mode = 'drop-after'; blockRead = true;
  await start('F82 丢失创建回包'); await idle(); const original = await pending();
  assert.equal(await editor().getByLabel('名字', { exact: true }).isDisabled(), true);
  assert.equal((await api('/agents')).length, initialCount + 2);
  const receipt = await api('/agent-requests/' + original.request_id);
  await api('/agents/' + receipt.agent.id, 'PATCH', { name: 'F82 其他窗口已改名', enabled: false, skills: ['coding'] });
  const postCount = posts.length;
  await editor().getByRole('button', { name: '关闭', exact: true }).click(); await open(); await idle();
  assert.equal((await pending()).request_id, original.request_id); assert.equal(posts.length, postCount);
  await page.screenshot({ path: path.join(out, 'creation-pending.png') });
  await page.reload(); await open(); await idle(); assert.equal(posts.length, postCount);
  blockRead = false; mode = 'normal';
  await editor().getByRole('button', { name: '只读核对原身份请求', exact: true }).click(); await recovered();
  assert.equal(posts.length, postCount); await page.getByText('F82 其他窗口已改名', { exact: true }).first().waitFor();
  await page.locator('button.person').filter({ hasText: 'F82 其他窗口已改名' }).getByText('已停用', { exact: true }).waitFor();
  assert.equal((await api('/agents/' + receipt.agent.id)).enabled, false);
  report.boundaries.push('drop-response-reopen-refresh', 'current-agent-not-original-snapshot');

  mode = 'drop-before'; await start('F82 原请求重试'); await idle(); const unsent = await pending();
  assert.equal(await api('/agent-requests/' + unsent.request_id), null);
  await page.reload(); await open(); await idle(); assert.equal((await pending()).request_id, unsent.request_id);
  const beforeRetry = posts.length; mode = 'normal';
  await editor().getByRole('button', { name: '同键重试身份创建', exact: true }).click(); await recovered();
  assert.equal(posts.length, beforeRetry + 1); assert.deepEqual(posts.at(-1), posts.at(-2));
  report.boundaries.push('null-keeps-request-explicit-same-key-retry');

  mode = 'drop-after'; blockRead = true; await start('F82 回执验证'); await idle(); const checkPending = await pending();
  const correct = await api('/agent-requests/' + checkPending.request_id), requestURL = '**/api/workbench/agent-requests/' + checkPending.request_id;
  mode = 'normal'; blockRead = false;
  for (const variant of ['request', 'payload', 'original-agent']) {
    const bad = structuredClone(correct);
    if (variant === 'request') bad.request_id = crypto.randomUUID();
    if (variant === 'payload') bad.payload.name += ' changed';
    if (variant === 'original-agent') bad.agent.name += ' changed';
    await page.route(requestURL, route => route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(bad) }));
    await editor().getByRole('button', { name: '只读核对原身份请求', exact: true }).click(); await idle();
    assert((await pending())?.request_id === checkPending.request_id); await editor().getByRole('alert').first().waitFor();
    await page.unroute(requestURL); report.boundaries.push('bad-' + variant);
  }
  const agentURL = '**/api/workbench/agents/' + correct.agent.id;
  await page.route(agentURL, route => route.fulfill({ status: 503, contentType: 'application/json', body: JSON.stringify({ error: 'F82 current unavailable' }) }));
  await editor().getByRole('button', { name: '只读核对原身份请求', exact: true }).click(); await idle();
  assert((await pending()).receipt); const fixedPending = await pending();
  await page.unroute(agentURL);
  await page.route(agentURL, route => route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ ...correct.agent, id: crypto.randomUUID() }) }));
  await editor().getByRole('button', { name: '只读核对原身份请求', exact: true }).click(); await idle();
  assert.deepEqual((await pending()).receipt, fixedPending.receipt); await page.unroute(agentURL);
  const changedOriginal = structuredClone(correct); changedOriginal.agent.created_at = '2000-01-01T00:00:00.000Z';
  await page.route(requestURL, route => route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(changedOriginal) }));
  await editor().getByRole('button', { name: '只读核对原身份请求', exact: true }).click(); await idle();
  assert.deepEqual((await pending()).receipt, fixedPending.receipt); await page.unroute(requestURL);
  await page.route(requestURL, route => route.fulfill({ status: 401, contentType: 'application/json', body: JSON.stringify({ error: 'F82 expired' }) }));
  await editor().getByRole('button', { name: '只读核对原身份请求', exact: true }).click(); await page.getByLabel('访问口令', { exact: true }).waitFor();
  assert.equal((await pending()).request_id, checkPending.request_id); await page.unroute(requestURL);
  await page.getByLabel('访问口令', { exact: true }).fill(manifest.access_token); await page.getByRole('button', { name: '验证并进入', exact: true }).click();
  await open(); await recovered(); report.boundaries.push('known-receipt-current-error', 'wrong-current-id', 'known-receipt-immutable', '401-preserves-pending');

  mode = 'reject'; await start('F82 首次拒绝'); await idle();
  await editor().getByRole('button', { name: '结束已拒绝身份请求', exact: true }).click();
  await until(async () => await pending() === null, 'finish rejected');
  if (await editor().count()) await editor().getByRole('button', { name: '关闭', exact: true }).click();
  mode = 'drop-before'; await start('F82 未知后拒绝'); await idle(); const unknown = await pending();
  mode = 'reject'; await editor().getByRole('button', { name: '同键重试身份创建', exact: true }).click(); await idle();
  const end = editor().getByRole('button', { name: '结束已拒绝身份请求', exact: true }); assert(await end.count() === 0 || await end.isDisabled());
  assert.equal((await pending()).request_id, unknown.request_id); mode = 'normal';
  await editor().getByRole('button', { name: '同键重试身份创建', exact: true }).click(); await recovered();
  report.boundaries.push('first-rejection-close', 'unknown-retry-rejection-not-downgraded');

  const countBeforeStorage = posts.length;
  await page.evaluate(k => { window.restoreCreateStorage = Storage.prototype.setItem; Storage.prototype.setItem = function(name, value) { if (name === k) throw Error('blocked'); return window.restoreCreateStorage.call(this, name, value); }; }, key);
  await start('F82 存储失败'); await editor().getByRole('alert').first().waitFor(); assert.equal(posts.length, countBeforeStorage);
  await page.evaluate(() => { Storage.prototype.setItem = window.restoreCreateStorage; });
  await editor().getByRole('button', { name: '关闭', exact: true }).click();
  await page.evaluate(k => sessionStorage.setItem(k, '{bad'), key); await open(); await editor().getByRole('alert').first().waitFor();
  assert.equal(await page.evaluate(k => sessionStorage.getItem(k), key), '{bad'); assert.equal(posts.length, countBeforeStorage);
  await editor().getByRole('button', { name: '关闭', exact: true }).click();
  // Only discard deliberately corrupt fixture data; never use this as the product recovery path.
  await page.evaluate(k => sessionStorage.removeItem(k), key); report.boundaries.push('storage-failure-no-post', 'bad-storage-not-cleared');

  await page.evaluate(k => { window.restoreCreateStorage = Storage.prototype.setItem; Storage.prototype.setItem = function(name, value) { if (name === k && JSON.parse(value).receipt) throw Error('receipt blocked'); return window.restoreCreateStorage.call(this, name, value); }; }, key);
  mode = 'normal'; const beforeReceiptFailure = posts.length;
  await start('F82 回执存储失败'); await idle(); const receiptFailure = await pending();
  assert(receiptFailure && !receiptFailure.receipt); assert.equal(posts.length, beforeReceiptFailure + 1);
  assert((await api('/agent-requests/' + receiptFailure.request_id)).agent);
  await page.evaluate(() => { Storage.prototype.setItem = window.restoreCreateStorage; });
  await editor().getByRole('button', { name: '只读核对原身份请求', exact: true }).click(); await recovered();
  assert.equal(posts.length, beforeReceiptFailure + 1); report.boundaries.push('receipt-storage-failure-preserves-key');

  mode = 'drop-before'; await start('F82 无目录恢复'); await idle(); const withoutCatalog = await pending();
  await page.route('**/api/workbench/templates', route => route.fulfill({ status: 200, contentType: 'application/json', body: '[]' }));
  await page.route('**/api/workbench/skills', route => route.fulfill({ status: 503, contentType: 'application/json', body: '{"error":"catalog unavailable"}' }));
  await page.reload(); await open(); await idle(); assert.equal((await pending()).request_id, withoutCatalog.request_id);
  const catalogPosts = posts.length; mode = 'normal'; await editor().getByRole('button', { name: '同键重试身份创建', exact: true }).click(); await recovered();
  assert.equal(posts.length, catalogPosts + 1); assert.deepEqual(posts.at(-1), { ...withoutCatalog.payload, request_id: withoutCatalog.request_id });
  await page.unroute('**/api/workbench/templates'); await page.unroute('**/api/workbench/skills'); await page.reload();
  report.boundaries.push('missing-catalog-recovery');

  mode = 'drop-after'; blockRead = true; await start('F82 迟到回执'); await idle(); const latePending = await pending();
  const lateReceipt = await api('/agent-requests/' + latePending.request_id), lateURL = '**/api/workbench/agent-requests/' + latePending.request_id;
  let releaseLate, lateStarted = false, lateDone = false; blockRead = false;
  await page.route(lateURL, async route => { lateStarted = true; await new Promise(resolve => { releaseLate = resolve; }); await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(lateReceipt) }); lateDone = true; });
  await editor().getByRole('button', { name: '只读核对原身份请求', exact: true }).click(); await until(() => lateStarted, 'held creation receipt');
  await page.evaluate(() => window.dispatchEvent(new Event('corppilot-access-expired'))); await page.getByLabel('访问口令', { exact: true }).waitFor();
  releaseLate(); await until(() => lateDone, 'late receipt finished'); await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
  assert.deepEqual(await pending(), latePending); await page.unroute(lateURL); mode = 'normal';
  await page.getByLabel('访问口令', { exact: true }).fill(manifest.access_token); await page.getByRole('button', { name: '验证并进入', exact: true }).click();
  await open(); await recovered(); report.boundaries.push('late-receipt-after-auth-unmount');

  mode = 'drop-before'; await start('F82 手机恢复'); await idle();
  await page.setViewportSize({ width: 390, height: 844 });
  assert(await editor().evaluate(el => el.scrollWidth <= el.clientWidth + 1)); assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth));
  await page.screenshot({ path: path.join(out, 'creation-mobile.png') });
  mode = 'normal'; await editor().getByRole('button', { name: '同键重试身份创建', exact: true }).click(); await recovered();
  await page.setViewportSize({ width: 1280, height: 960 });
  mode = 'drop-after'; blockRead = true; await start('F82 StrictMode 恢复'); await idle(); const strictPending = await pending(), strictPosts = posts.length;
  devServer = await createServer({ root: path.join(repo, 'frontend'), configFile: false, server: { host: '127.0.0.1', port: 0, proxy: { '/api': baseURL } } });
  await devServer.listen(); uiURL = devServer.resolvedUrls.local[0].replace(/\/$/, ''); await page.setViewportSize({ width: 1280, height: 960 });
  await page.goto(`${uiURL}/#access_token=${manifest.access_token}`); await open(); await page.keyboard.press('Escape');
  await until(async () => await page.evaluate(() => document.activeElement?.textContent === '新建'), 'StrictMode focus', 2000);
  // Transfer this real fixture request to the separate development origin; recovery is GET-only.
  await page.evaluate(({ key, value }) => sessionStorage.setItem(key, JSON.stringify(value)), { key, value: strictPending });
  await page.reload(); await open(); await idle(); assert.equal((await pending()).request_id, strictPending.request_id); assert.equal(posts.length, strictPosts);
  blockRead = false; mode = 'normal'; await editor().getByRole('button', { name: '只读核对原身份请求', exact: true }).click(); await recovered();
  assert.equal(posts.length, strictPosts); report.boundaries.push('mobile-layout', 'strictmode-focus-and-pending-recovery');
  assert.equal(report.pageErrors.length, 0); assert.equal(otherWrites.length, 0);
  const events = fs.existsSync(path.join(fixture, 'evidence.jsonl')) ? fs.readFileSync(path.join(fixture, 'evidence.jsonl'), 'utf8').trim().split('\n').filter(Boolean).map(JSON.parse) : [];
  assert.equal(events.filter(e => ['model_call', 'cli_call'].includes(e.kind)).length, 0);
  report.passed = true; report.postAttempts = posts.length; report.externalCalls = 0;
} catch (e) { report.error = String(e.stack || e); process.exitCode = 1; await page?.screenshot({ path: path.join(out, 'failure.png') }).catch(() => {}); }
finally {
  await devServer?.close(); await browser?.close();
  if (fs.existsSync(fixture)) { fs.writeFileSync(path.join(fixture, 'shutdown'), 'stop'); await until(() => childExit || spawnError, 'shutdown', 20000).catch(() => { child.kill(); report.cleanupForced = true; }); }
  report.fixtureExit = childExit; logs.end(); if (report.cleanupForced || childExit?.code !== 0) { report.passed = false; process.exitCode = 1; }
  fs.writeFileSync(path.join(out, 'browser-report.json'), JSON.stringify(report, null, 2)); console.log(JSON.stringify({ passed: report.passed, report: path.join(out, 'browser-report.json'), error: report.error }));
}
