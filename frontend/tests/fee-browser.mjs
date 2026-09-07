import assert from 'node:assert/strict';
import { openManagement } from './navigation.mjs';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { spawn } from 'node:child_process';
import { chromium } from 'playwright';
import { createServer } from 'vite';

const repo = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..');
const outputRoot = process.env.CORPPILOT_BROWSER_OUTPUT || os.tmpdir();
fs.mkdirSync(outputRoot, { recursive: true });
const outDir = fs.mkdtempSync(path.join(outputRoot, 'corppilot-fees-'));
const fixtureDir = path.join(outDir, 'fixture');
const python = process.env.CORPPILOT_PYTHON || path.join(repo, '.venv', process.platform === 'win32' ? 'Scripts/python.exe' : 'bin/python');
const child = spawn(python, [path.join(repo, 'tests/browser/goal_fixture.py'), '--data-dir', fixtureDir, '--fees'], { cwd: repo, windowsHide: true });
const logs = fs.createWriteStream(path.join(outDir, 'fixture.log'));
child.stdout.pipe(logs, { end: false }); child.stderr.pipe(logs, { end: false });
let childExit, spawnError, browser, page, devServer;
child.on('error', error => { spawnError = error; });
child.on('exit', (code, signal) => { childExit = { code, signal }; });
const report = { passed: false, output: outDir, pageErrors: [] };
async function until(check, description, timeout = 30000) {
  const deadline = Date.now() + timeout;
  while (Date.now() < deadline) { const value = await check(); if (value) return value; await new Promise(r => setTimeout(r, 100)); }
  throw Error(`Timed out: ${description}`);
}
function controls(value) { const target = path.join(fixtureDir, 'control.json'); fs.writeFileSync(target + '.tmp', JSON.stringify(value)); fs.renameSync(target + '.tmp', target); }
function events() { if (!fs.existsSync(path.join(fixtureDir, 'evidence.jsonl'))) return []; return fs.readFileSync(path.join(fixtureDir, 'evidence.jsonl'), 'utf8').trim().split('\n').filter(Boolean).map(line => JSON.parse(line)); }
try {
  await until(() => { if (spawnError || childExit) throw Error('Fixture exited'); return fs.existsSync(path.join(fixtureDir, 'manifest.json')); }, 'fixture startup');
  const manifest = JSON.parse(fs.readFileSync(path.join(fixtureDir, 'manifest.json')));
  const baseURL = `http://127.0.0.1:${manifest.port}`;
  let uiURL = baseURL;
  browser = await chromium.launch({ headless: true });
  const context = await browser.newContext({ viewport: { width: 1280, height: 960 } });
  page = await context.newPage(); page.setDefaultTimeout(15000);
  page.on('pageerror', error => report.pageErrors.push(String(error)));
  const calls = () => events().filter(e => e.kind === 'cli_call');
  const posts = () => events().filter(e => e.kind === 'fee_post');
  async function api(pathname, data, method = 'GET') {
    const response = await context.request.fetch(baseURL + '/api/workbench' + pathname, { method, headers: { Authorization: `Bearer ${manifest.access_token}` }, ...(data ? { data } : {}) });
    assert([200, 201].includes(response.status()), `${method} ${pathname}: ${response.status()} ${await response.text()}`); return response.json();
  }
  async function openAgent(agentId, room = 'F62 预算项目') {
    await page.goto(`${uiURL}/#access_token=${manifest.access_token}`);
    await page.getByRole('button', { name: new RegExp(room) }).click();
    await page.getByLabel('查看会话成员', { exact: true }).selectOption(agentId);
    await page.getByRole('button', { name: '刷新 Agent 活动', exact: true }).waitFor();
  }
  await page.goto(`${uiURL}/#access_token=${manifest.access_token}`);
  await openManagement(page); await page.getByRole('button', { name: '预算与预留', exact: true }).click();
  const budget = page.getByRole('dialog', { name: '预算与预留', exact: true });
  await budget.getByLabel('总额度（USD）', { exact: true }).fill('1');
  await budget.getByLabel('我确认修改可能放行现有排队任务，关闭准入将取消后续预算门禁', { exact: true }).check();
  await budget.getByRole('button', { name: '确认保存预算配置', exact: true }).click();
  await until(async () => (await api(`/project-executions/${manifest.budget.batch.id}`)).items.some(i => i.execution.state === 'awaiting_review'), 'first worker complete');
  assert.equal(calls().length, 1);
  const firstId = calls()[0].execution_id;
  const firstRun = (await api(`/project-executions/${manifest.budget.batch.id}`)).items.find(i => i.execution.id === firstId).execution;
  await budget.getByRole('button', { name: '关闭', exact: true }).click();
  await openAgent(firstRun.agent_id);
  const fee = () => page.getByRole('dialog', { name: '费用声明与更正', exact: true });
  const keyFor = (kind, id) => `corppilot.fee-settlement-pending.v1.${kind}.${id}`;
  const confirmation = () => fee().getByLabel('我确认这是 Owner 核查声明，并理解减少占用可能启动已授权队列', { exact: true });
  const submit = () => fee().getByRole('button', { name: '确认提交费用声明', exact: true });
  async function openFee(id, model = false) {
    if (model) await page.getByText(/^此身份的模型活动/).click();
    await page.locator('article').filter({ hasText: id }).first().getByRole('button', { name: '费用声明与更正', exact: true }).click();
    await fee().getByLabel('本次实例完整费用（USD）', { exact: true }).waitFor();
  }
  async function fill(amount) {
    await fee().getByLabel('本次实例完整费用（USD）', { exact: true }).fill(amount);
    await fee().getByLabel('费用证据参考', { exact: true }).fill('F64 controlled invoice reference');
    await fee().getByLabel('费用核查说明', { exact: true }).fill('Explicit Owner declaration; fixture is not a provider-verified invoice');
  }
  async function saveFee(kind, id) {
    await confirmation().check(); await submit().click();
    await until(async () => await page.evaluate(key => sessionStorage.getItem(key), keyFor(kind, id)) === null, 'exact fee receipt independently confirmed');
    await fee().getByText('已核对的原请求回执', { exact: true }).waitFor();
  }
  await openFee(firstId);
  assert.equal(await fee().getByLabel('本次实例完整费用（USD）', { exact: true }).inputValue(), '');
  assert(await submit().isDisabled());
  for (const value of ['', '-1', '1e3', '0.0000001', '1000000.000001']) {
    await fill(value); await confirmation().check(); await submit().click();
    if (value === '') assert(await fee().getByLabel('本次实例完整费用（USD）', { exact: true }).evaluate(node => !node.validity.valid));
    else await fee().getByRole('alert').first().waitFor();
    assert.equal(posts().length, 0, value);
  }
  await fill('0'); assert(!(await confirmation().isChecked())); await saveFee('cli', firstId);
  await until(() => calls().length === 2, 'zero declaration releases exactly one further worker');
  await until(async () => (await api(`/project-executions/${manifest.budget.batch.id}`)).items.filter(i => i.execution.state === 'awaiting_review').length === 2, 'second worker completes');
  assert.equal((await api('/budget-settings')).committed_micro_usd, 1000000);
  await fill('2'); await saveFee('cli', firstId);
  assert.equal((await api('/budget-settings')).available_micro_usd, -2000000);
  assert.equal(calls().length, 2);
  const after = (await api(`/project-executions/${manifest.budget.batch.id}`)).items;
  assert.deepEqual(after.find(i => i.execution.id === firstId).execution, firstRun);
  assert.equal(after.filter(i => i.execution.state === 'queued').length, 1);
  await page.screenshot({ path: path.join(outDir, 'fee-correction.png') });
  report.budgetDispatch = { passed: true, calls: 2, queued: 1, available_micro_usd: -2000000, originalExecutionUnchanged: true };
  await fee().getByRole('button', { name: '关闭费用声明', exact: true }).click();
  const model = manifest.fee_model;
  await openAgent(model.agent_id); await openFee(model.id, true);
  await fill('0.000001'); await confirmation().check();
  controls({ drop_fee: true, fee_get503: true }); await submit().click();
  await until(() => events().filter(e => e.kind === 'fee_accepted').length === 3, 'model declaration committed before dropped response');
  const key = keyFor('model', model.id);
  const pending = await page.evaluate(key => sessionStorage.getItem(key), key); assert(pending);
  await openAgent(model.agent_id); await openFee(model.id, true);
  await fee().getByRole('alert').first().waitFor();
  assert.equal(await page.evaluate(key => sessionStorage.getItem(key), key), pending);
  assert.equal(posts().length, 3);
  controls({});
  const original = await api(`/budget-settlements/model/${model.id}`);
  assert.equal(original.amount_micro_usd, 1);
  const correction = { ...original.request_payload, request_id: 'other-client-revision2', expected_revision: 1, amount_micro_usd: 200000 };
  await api(`/budget-settlements/model/${model.id}`, correction, 'POST');
  const requestURL = `**/api/workbench/budget-settlements/model/${model.id}/requests/*`;
  report.boundaries = [];
  for (const variant of ['kind', 'agent', 'revision', 'payload']) {
    const bad = structuredClone(original);
    if (variant === 'kind') bad.kind = 'cli';
    if (variant === 'agent') bad.agent_id = manifest.agent_ids[1];
    if (variant === 'revision') bad.revision = 99;
    if (variant === 'payload') bad.request_payload.note = 'changed';
    await page.route(requestURL, route => route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(bad) }));
    await fee().getByRole('button', { name: '只读核对原费用请求', exact: true }).click();
    await fee().getByRole('alert').first().waitFor();
    assert.equal(await page.evaluate(key => sessionStorage.getItem(key), key), pending);
    assert.equal(posts().length, 4);
    await page.unroute(requestURL);
    report.boundaries.push({ variant: `wrong-${variant}`, passed: true });
  }
  await page.route(requestURL, route => route.fulfill({ status: 401, contentType: 'application/json', body: JSON.stringify({ error: 'Fixture access expired' }) }));
  await fee().getByRole('button', { name: '只读核对原费用请求', exact: true }).click();
  await page.getByLabel('访问口令', { exact: true }).waitFor();
  assert.equal(await page.evaluate(key => sessionStorage.getItem(key), key), pending);
  await page.unroute(requestURL);
  await page.getByLabel('访问口令', { exact: true }).fill(manifest.access_token);
  await page.getByRole('button', { name: '验证并进入', exact: true }).click();
  await openAgent(model.agent_id); await openFee(model.id, true);
  await until(async () => await page.evaluate(key => sessionStorage.getItem(key), key) === null, 'reauthorized exact old request recovery');
  await fee().getByText('已核对的原请求回执', { exact: true }).waitFor();
  assert((await fee().innerText()).includes('0.000001'));
  assert((await fee().innerText()).includes('0.2'));
  assert.equal((await api(`/budget-settlements/model/${model.id}`)).revision, 2);
  assert.equal(posts().length, 4);
  await page.screenshot({ path: path.join(outDir, 'fee-original-and-current.png') });
  report.exactRecovery = { passed: true, originalRevision: 1, latestRevision: 2, noAutomaticPosts: true, reauthorization: true };
  await fee().getByRole('button', { name: '关闭费用声明', exact: true }).click();
  const mainPage = page;
  const extra = await browser.newContext();
  await extra.addInitScript(({ key }) => sessionStorage.setItem(key, '{invalid'), { key });
  page = await extra.newPage(); page.setDefaultTimeout(15000);
  page.on('pageerror', error => report.pageErrors.push(String(error)));
  const unwantedWrites = [];
  await page.route('**/api/workbench/**', route => {
    if (route.request().method() !== 'GET') { unwantedWrites.push(route.request().url()); return route.abort(); }
    return route.continue();
  });
  await openAgent(model.agent_id); await openFee(model.id, true);
  await fee().getByRole('alert').first().waitFor();
  assert.equal(await page.evaluate(key => sessionStorage.getItem(key), key), '{invalid');
  assert(await submit().isDisabled()); assert.equal(unwantedWrites.length, 0);
  report.boundaries.push({ variant: 'bad-storage', passed: true });
  await extra.close(); page = mainPage;
  // An old pending GET must not clear storage or update a different identity after unmount.
  await page.evaluate(({ key, pending }) => sessionStorage.setItem(key, pending), { key, pending });
  let releaseRead, delayed = false;
  await page.route(requestURL, async route => {
    delayed = true; await new Promise(resolve => { releaseRead = resolve; });
    await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(original) }).catch(() => {});
  });
  await openAgent(model.agent_id); await openFee(model.id, true);
  await until(() => delayed, 'old exact request GET held');
  await fee().getByRole('button', { name: '关闭费用声明', exact: true }).click();
  await page.getByLabel('查看会话成员', { exact: true }).selectOption(firstRun.agent_id);
  await openFee(firstId);
  releaseRead(); await page.unroute(requestURL);
  await fee().getByRole('button', { name: '刷新费用与历史', exact: true }).click();
  await until(async () => !(await fee().getByRole('button', { name: '刷新费用与历史', exact: true }).isDisabled()), 'current fee refresh finishes');
  assert.equal(await page.evaluate(key => sessionStorage.getItem(key), key), pending);
  assert((await fee().innerText()).includes(firstId));
  assert(!(await fee().innerText()).includes(model.id));
  assert.equal(posts().length, 4);
  report.boundaries.push({ variant: 'unmounted-old-read', passed: true });
  await fee().getByRole('button', { name: '关闭费用声明', exact: true }).click();
  await openAgent(model.agent_id); await openFee(model.id, true);
  await until(async () => await page.evaluate(key => sessionStorage.getItem(key), key) === null, 'pending recovers only when original instance reopened');
  const newer = { ...correction, request_id: 'other-client-revision3', expected_revision: 2, amount_micro_usd: 300000 };
  await api(`/budget-settlements/model/${model.id}`, newer, 'POST');
  await fill('0.4'); await confirmation().check(); await submit().click();
  await fee().getByRole('alert').first().waitFor();
  assert.equal((await api(`/budget-settlements/model/${model.id}`)).revision, 3);
  await fee().getByRole('button', { name: '只读核对原费用请求', exact: true }).click();
  await fee().getByRole('button', { name: '结束已拒绝请求并重新核查', exact: true }).click();
  assert.equal(await page.evaluate(key => sessionStorage.getItem(key), key), null);
  assert(!(await confirmation().isChecked()));
  await fill('0.4'); await saveFee('model', model.id);
  assert.equal((await api(`/budget-settlements/model/${model.id}`)).revision, 4);
  assert.equal(posts().length, 7);
  report.boundaries.push({ variant: 'concurrent-revision-requires-new-confirmation', passed: true });
  let maximum;
  const modelURL = `**/api/workbench/budget-settlements/model/${model.id}`;
  await page.route(modelURL, route => {
    if (route.request().method() === 'POST') { maximum = route.request().postDataJSON(); return route.fulfill({ status: 400, contentType: 'application/json', body: JSON.stringify({ error: 'Maximum precision rejected before write' }) }); }
    return route.continue();
  });
  await fill('1000000'); await confirmation().check(); await submit().click();
  await until(() => maximum, 'maximum exact fee payload');
  assert.equal(maximum.amount_micro_usd, 1000000000000); assert.equal(posts().length, 7);
  await page.unroute(modelURL);
  await fee().getByRole('button', { name: '只读核对原费用请求', exact: true }).click();
  await fee().getByRole('button', { name: '结束已拒绝请求并重新核查', exact: true }).click();
  report.boundaries.push({ variant: 'maximum-six-decimal-integer', passed: true });
  await page.route(requestURL, route => route.fulfill({ status: 400, contentType: 'application/json', body: JSON.stringify({ error: 'Exact read rejected after accepted POST' }) }));
  await fill('0.5'); await confirmation().check(); await submit().click();
  await fee().getByRole('alert').first().waitFor();
  const acceptedPending = JSON.parse(await page.evaluate(key => sessionStorage.getItem(key), key));
  assert.equal(acceptedPending.receipt.revision, 5); assert(!acceptedPending.rejected);
  assert.equal(await fee().getByRole('button', { name: '结束已拒绝请求并重新核查', exact: true }).count(), 0);
  assert.equal(posts().length, 8);
  await page.unroute(requestURL);
  await fee().getByRole('button', { name: '只读核对原费用请求', exact: true }).click();
  await until(async () => await page.evaluate(key => sessionStorage.getItem(key), key) === null, 'POST201 GET400 recovers without new POST');
  report.boundaries.push({ variant: 'post201-then-get400-keeps-accepted-request', passed: true });

  await fee().getByRole('button', { name: '关闭费用声明', exact: true }).click();
  const queued = after.find(i => i.execution.state === 'queued').execution;
  await openAgent(queued.agent_id); await openFee(queued.id);
  await until(async () => (await fee().innerText()).includes('活动实例不能声明费用'), 'queued state loaded');
  assert(await submit().isDisabled()); assert.equal(posts().length, 8);
  await fee().getByRole('button', { name: '关闭费用声明', exact: true }).click();
  report.boundaries.push({ variant: 'queued-instance-readonly', passed: true });
  await openAgent(model.agent_id);
  const feeListLabel = '此身份的费用声明（最近100个实例）';
  await page.getByText(feeListLabel, { exact: true }).click();
  let feeList = page.getByText(feeListLabel, { exact: true }).locator('..');
  await feeList.getByText(new RegExp(model.id)).waitFor();
  assert(!(await feeList.innerText()).includes(firstId));
  assert((await feeList.innerText()).includes('修订 5'));
  await feeList.getByRole('button', { name: '费用声明与更正', exact: true }).click();
  await fee().getByLabel('本次实例完整费用（USD）', { exact: true }).waitFor();
  assert((await fee().innerText()).includes(model.id));
  await fee().getByRole('button', { name: '关闭费用声明', exact: true }).click();
  await openAgent(firstRun.agent_id);
  await page.getByText(feeListLabel, { exact: true }).click();
  feeList = page.getByText(feeListLabel, { exact: true }).locator('..');
  await feeList.getByText(new RegExp(firstId)).waitFor();
  assert(!(await feeList.innerText()).includes(model.id));
  const feeListURL = `**/api/workbench/agents/${model.agent_id}/budget-settlements`;
  await page.route(feeListURL, route => route.fulfill({ status: 200, contentType: 'application/json', body: '[null]' }));
  await openAgent(model.agent_id);
  await page.getByText(feeListLabel, { exact: true }).click();
  await page.getByRole('alert').filter({ hasText: '费用列表' }).waitFor();
  await openFee(model.id, true);
  await fee().getByRole('button', { name: '关闭费用声明', exact: true }).click();
  assert.equal(posts().length, 8); await page.unroute(feeListURL);
  report.boundaries.push({ variant: 'agent-fee-scope-and-malformed-list', passed: true });
  devServer = await createServer({ root: path.join(repo, 'frontend'), configFile: false, server: { host: '127.0.0.1', port: 0, proxy: { '/api': baseURL } } });
  await devServer.listen(); uiURL = devServer.resolvedUrls.local[0].replace(/\/$/, '');
  const devContext = await browser.newContext(); page = await devContext.newPage(); page.setDefaultTimeout(15000);
  page.on('pageerror', error => report.pageErrors.push(String(error)));
  await openAgent(model.agent_id); await openFee(model.id, true);
  await fill('0.1'); await fee().getByRole('button', { name: '刷新费用与历史', exact: true }).click();
  await fee().getByLabel('本次实例完整费用（USD）', { exact: true }).fill('0.2');
  await page.keyboard.press('Escape');
  assert.equal(await fee().count(), 0);
  await until(async () => await page.evaluate(() => document.activeElement?.textContent === '费用声明与更正'), 'StrictMode Escape restores fee opener focus', 2000);
  assert.equal(posts().length, 8);
  report.devStrictMode = { passed: true, escapeRestoresFocus: true, writes: 0 };
  await devContext.close(); await devServer.close(); devServer = null; page = mainPage;
  assert.equal(events().filter(e => e.kind === 'model_call').length, 0);
  assert.equal(calls().length, 2); assert.equal(report.pageErrors.length, 0);
  report.passed = true;
} catch (error) {
  report.error = String(error.stack || error); process.exitCode = 1;
  await page?.screenshot({ path: path.join(outDir, 'failure.png') }).catch(() => {});
} finally {
  await devServer?.close();
  await browser?.close();
  if (fs.existsSync(fixtureDir)) { controls({}); fs.writeFileSync(path.join(fixtureDir, 'shutdown'), 'stop'); await until(() => childExit || spawnError, 'shutdown', 20000).catch(() => { child.kill(); report.cleanupForced = true; }); }
  report.fixtureExit = childExit; logs.end();
  if (report.cleanupForced || childExit?.code !== 0) { report.passed = false; process.exitCode = 1; }
  fs.writeFileSync(path.join(outDir, 'browser-report.json'), JSON.stringify(report, null, 2));
  console.log(JSON.stringify({ passed: report.passed, report: path.join(outDir, 'browser-report.json'), error: report.error }));
}
