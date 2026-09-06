import assert from 'node:assert/strict';
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
const outDir = fs.mkdtempSync(path.join(outputRoot, 'corppilot-budget-'));
const fixtureDir = path.join(outDir, 'fixture');
const python = process.env.CORPPILOT_PYTHON || path.join(repo, '.venv', process.platform === 'win32' ? 'Scripts/python.exe' : 'bin/python');
const child = spawn(python, [path.join(repo, 'tests/browser/goal_fixture.py'), '--data-dir', fixtureDir, '--budget'], { cwd: repo, windowsHide: true });
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
  browser = await chromium.launch({ headless: true });
  devServer = await createServer({ root: path.join(repo, 'frontend'), configFile: false, server: { host: '127.0.0.1', port: 0, proxy: { '/api': baseURL } } });
  await devServer.listen();
  const devContext = await browser.newContext();
  const devPage = await devContext.newPage();
  devPage.on('pageerror', error => report.pageErrors.push(String(error)));
  await devPage.goto(`${devServer.resolvedUrls.local[0]}#access_token=${manifest.access_token}`);
  await devPage.getByRole('button', { name: '预算与预留', exact: true }).click();
  const devDialog = devPage.getByRole('dialog', { name: '预算与预留', exact: true });
  await devDialog.getByLabel('总额度（USD）', { exact: true }).fill('0.000001');
  await devDialog.getByRole('button', { name: '刷新预算与预留', exact: true }).click();
  await devDialog.getByLabel('总额度（USD）', { exact: true }).fill('0.000002');
  await devPage.keyboard.press('Escape');
  assert.equal(await devDialog.count(), 0);
  await until(async () => await devPage.evaluate(() => document.activeElement?.textContent === '预算与预留'), 'Escape restores opener focus', 2000);
  assert.equal(events().filter(e => e.kind === 'budget_patch').length, 0);
  report.devStrictMode = { passed: true, budgetPatches: 0, escapeRestoresFocus: true };
  await devContext.close(); await devServer.close(); devServer = null;
  const context = await browser.newContext({ viewport: { width: 1280, height: 900 } });
  page = await context.newPage(); page.setDefaultTimeout(15000);
  page.on('pageerror', error => report.pageErrors.push(String(error)));
  const key = 'corppilot.budget-settings-pending.v1';
  const dialog = () => page.getByRole('dialog', { name: '预算与预留', exact: true });
  const save = () => dialog().getByRole('button', { name: '确认保存预算配置', exact: true });
  const confirm = () => dialog().getByLabel('我确认修改可能放行现有排队任务，关闭准入将取消后续预算门禁', { exact: true });
  const patches = () => events().filter(e => e.kind === 'budget_patch');
  const calls = () => events().filter(e => e.kind === 'cli_call');
  async function api(pathname, data) {
    const response = await context.request.fetch(baseURL + '/api/workbench' + pathname, { method: data ? 'PATCH' : 'GET', headers: { Authorization: `Bearer ${manifest.access_token}` }, ...(data ? { data } : {}) });
    assert.equal(response.status(), 200); return response.json();
  }
  async function open() {
    await page.goto(`${baseURL}/#access_token=${manifest.access_token}`);
    await page.getByRole('button', { name: '预算与预留', exact: true }).click();
  }
  async function read() { await dialog().getByRole('button', { name: /^(只读核对预算配置|刷新预算与预留)$/ }).click(); }
  async function setTotal(value) { await dialog().getByLabel('总额度（USD）', { exact: true }).fill(value); }
  async function submit() {
    await confirm().check(); await save().click();
    await until(async () => await page.evaluate(key => sessionStorage.getItem(key), key) === null, 'independent readback clears pending');
  }
  await open();
  await dialog().getByLabel('总额度（USD）', { exact: true }).waitFor();
  assert.equal(calls().length, 0);
  assert(await save().isDisabled());
  for (const value of ['-1', '1e3', '0.0000001', '1000000.000001']) {
    await setTotal(value); await confirm().check(); await save().click(); await dialog().getByRole('alert').first().waitFor(); assert.equal(patches().length, 0, value);
  }
  await setTotal('1.000001');
  assert(!(await confirm().isChecked()));
  await dialog().getByLabel('每次模型请求预留（USD）', { exact: true }).fill('0');
  await confirm().check(); await save().click(); await dialog().getByRole('alert').first().waitFor(); assert.equal(patches().length, 0);
  await dialog().getByLabel('每次模型请求预留（USD）', { exact: true }).fill('0.000001');
  await dialog().getByLabel('每次 CLI 执行预留（USD）', { exact: true }).fill('1.000001');
  assert(!(await confirm().isChecked())); assert.equal(patches().length, 0);
  await submit();
  await until(() => calls().length === 1, 'first worker budget admitted');
  assert.deepEqual(patches()[0].payload, { enabled: true, total_micro_usd: 1000001, model_reserve_micro_usd: 1, cli_reserve_micro_usd: 1000001 });
  await until(async () => (await api(`/project-executions/${manifest.budget.batch.id}`)).items.some(i => i.execution.state === 'awaiting_review'), 'first worker done');
  assert.equal(calls().length, 1);
  assert.equal((await api('/budget-settings')).available_micro_usd, 0);
  assert((await api('/cli-runtime')).error.includes('预算'));
  await read();
  await setTotal('2.000002'); await submit();
  await until(async () => (await api(`/project-executions/${manifest.budget.batch.id}`)).items.every(i => i.execution.state === 'awaiting_review'), 'second worker after budget increase');
  assert.equal(calls().length, 2);
  await setTotal('0');
  await dialog().getByLabel('每次模型请求预留（USD）', { exact: true }).fill('1.234567');
  await submit();
  assert.equal((await api('/budget-settings')).available_micro_usd, -2000002);
  assert.equal(patches()[2].payload.model_reserve_micro_usd, 1234567);
  await dialog().getByText('-2.000002 USD', { exact: true }).waitFor();
  await dialog().getByRole('heading', { name: '预算与预留', exact: true }).scrollIntoViewIfNeeded();
  await page.screenshot({ path: path.join(outDir, 'budget-negative.png') });
  await dialog().getByLabel('启用预算准入', { exact: true }).uncheck(); await submit();
  assert.equal((await api('/budget-settings')).reserved_micro_usd, 2000002);
  await dialog().getByRole('button', { name: '关闭', exact: true }).click();
  await page.getByRole('button', { name: /F62 预算项目/ }).click();
  const observations = [];
  for (const agentId of manifest.agent_ids) {
    await page.getByLabel('查看会话成员', { exact: true }).selectOption(agentId);
    await page.getByRole('button', { name: '查看预算预留', exact: true }).click();
    const view = page.getByRole('dialog', { name: 'Agent 预算预留', exact: true });
    await view.getByLabel('预留记录数', { exact: true }).waitFor();
    const records = await api(`/agents/${agentId}/budget-reservations`);
    for (const record of records) await view.getByText(record.run_id, { exact: false }).waitFor();
    assert.equal(await view.getByRole('button', { name: '确认保存预算配置', exact: true }).count(), 0);
    const others = (await api('/budget-reservations')).filter(r => r.agent_id !== agentId);
    for (const other of others) assert(!(await view.innerText()).includes(other.run_id));
    observations.push({ agent_id: agentId, records: records.length });
    await view.getByRole('button', { name: '关闭', exact: true }).click();
  }
  assert.equal(patches().length, 4); assert.equal(calls().length, 2);
  await page.getByRole('button', { name: '预算与预留', exact: true }).click();
  await setTotal('3.000003'); await dialog().getByLabel('启用预算准入', { exact: true }).check(); await confirm().check();
  controls({ drop_budget: true, budget_get503: true });
  await save().click();
  await until(() => events().filter(e => e.kind === 'budget_accepted').length === 5, 'fifth PATCH accepted then dropped');
  const pending = await page.evaluate(key => sessionStorage.getItem(key), key);
  assert(pending);
  await open();
  await dialog().getByRole('alert').first().waitFor();
  assert.equal(await page.evaluate(key => sessionStorage.getItem(key), key), pending);
  assert.equal(patches().length, 5);
  controls({}); await read();
  await until(async () => await page.evaluate(key => sessionStorage.getItem(key), key) === null, 'GET confirms current desired configuration');
  assert.equal(patches().length, 5);
  const current = await api('/budget-settings');
  await api('/budget-settings', { enabled: true, total_micro_usd: 4000004, model_reserve_micro_usd: current.model_reserve_micro_usd, cli_reserve_micro_usd: current.cli_reserve_micro_usd });
  await page.evaluate(({ key, pending }) => sessionStorage.setItem(key, pending), { key, pending });
  await open();
  await dialog().getByRole('button', { name: '采用当前配置并结束核对', exact: true }).click();
  assert.equal(await page.evaluate(key => sessionStorage.getItem(key), key), null);
  assert.equal(patches().length, 6);
  assert.equal(await dialog().getByLabel('总额度（USD）', { exact: true }).inputValue(), '4.000004');
  // Maximum precision payload is intercepted before server: no actual extra authorization.
  let maximum;
  await page.route('**/api/workbench/budget-settings', route => {
    if (route.request().method() === 'PATCH') { maximum = route.request().postDataJSON(); return route.fulfill({ status: 400, contentType: 'application/json', body: JSON.stringify({ error: 'Precision fixture rejected before write' }) }); }
    return route.continue();
  });
  await setTotal('1000000'); await confirm().check(); await save().click();
  await until(() => maximum, 'maximum exact payload');
  assert.equal(maximum.total_micro_usd, 1000000000000);
  assert.equal(patches().length, 6);
  await page.unroute('**/api/workbench/budget-settings');
  await read();
  await dialog().getByRole('button', { name: '采用当前配置并结束核对', exact: true }).click();
  assert.equal(await page.evaluate(key => sessionStorage.getItem(key), key), null);
  const declaredId = manifest.budget.batch.tasks[0].execution_id;
  const feeResponse = await context.request.post(`${baseURL}/api/workbench/budget-settlements/cli/${declaredId}`, {
    headers: { Authorization: `Bearer ${manifest.access_token}` },
    data: { request_id: 'browser-owner-fee', expected_revision: 0, attempt: 1, requirement_version: 1,
      amount_micro_usd: 500000, evidence_reference: 'Controlled fixture invoice', note: 'Owner fee declaration fixture, not provider verification', confirm: true }
  });
  assert.equal(feeResponse.status(), 201);
  const declared = await feeResponse.json(); assert.equal(declared.source, 'owner_declared');
  await read();
  await dialog().getByLabel('Owner 声明费用', { exact: true }).waitFor();
  assert.equal(await dialog().getByLabel('Owner 声明费用', { exact: true }).innerText(), '0.5 USD');
  assert.equal(await dialog().getByLabel('总预留', { exact: true }).innerText(), '2.000002 USD');
  assert.equal(await dialog().getByLabel('未核销预留', { exact: true }).innerText(), '1.000001 USD');
  assert.equal(await dialog().getByLabel('当前预算占用', { exact: true }).innerText(), '1.500001 USD');
  assert.equal(await dialog().getByLabel('可用额度', { exact: true }).innerText(), '2.500003 USD');
  await dialog().getByRole('heading', { name: '预算与预留', exact: true }).scrollIntoViewIfNeeded();
  await page.screenshot({ path: path.join(outDir, 'budget-owner-declaration.png') });
  report.ownerFeeDeclaration = { run_id: declaredId, amount_micro_usd: 500000, passed: true };
  report.observations = observations;
  report.boundaries = [];
  const mainPage = page;
  for (const variant of ['bad-storage', 'get401', 'wrong-currency']) {
    const extra = await browser.newContext();
    await extra.addInitScript(({ key, pending }) => sessionStorage.setItem(key, pending), { key, pending: variant === 'bad-storage' ? '{invalid' : pending });
    page = await extra.newPage(); page.setDefaultTimeout(15000);
    page.on('pageerror', error => report.pageErrors.push(String(error)));
    const writes = [];
    await page.route('**/api/workbench/**', route => { if (route.request().method() !== 'GET') { writes.push(route.request().url()); return route.abort(); } return route.continue(); });
    if (variant !== 'bad-storage') await page.route('**/api/workbench/budget-settings', async route => {
      if (variant === 'get401') return route.fulfill({ status: 401, contentType: 'application/json', body: JSON.stringify({ error: 'Fixture access expired' }) });
      const value = await api('/budget-settings'); value.currency = 'CNY';
      return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(value) });
    });
    await open();
    await page.getByRole('alert').first().waitFor();
    assert.equal(await page.evaluate(key => sessionStorage.getItem(key), key), variant === 'bad-storage' ? '{invalid' : pending);
    assert.equal(writes.length, 0);
    if (variant === 'get401') {
      await page.unroute('**/api/workbench/budget-settings');
      await page.getByLabel('访问口令', { exact: true }).fill(manifest.access_token);
      await page.getByRole('button', { name: '验证并进入', exact: true }).click();
      await page.getByRole('button', { name: '预算与预留', exact: true }).click();
      await dialog().getByRole('button', { name: '采用当前配置并结束核对', exact: true }).click();
      assert.equal(await page.evaluate(key => sessionStorage.getItem(key), key), null);
      assert.equal(writes.length, 0);
    }
    report.boundaries.push({ variant, passed: true });
    await extra.close();
  }
  page = mainPage;
  assert.equal(events().filter(e => e.kind === 'model_call').length, 0);
  assert.equal(calls().length, 2); assert.equal(patches().length, 6);
  assert.equal(report.pageErrors.length, 0); report.passed = true;
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
