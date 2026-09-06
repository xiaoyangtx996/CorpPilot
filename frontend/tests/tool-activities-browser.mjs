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
const outDir = fs.mkdtempSync(path.join(outputRoot, 'corppilot-tools-'));
const fixtureDir = path.join(outDir, 'fixture');
const python = process.env.CORPPILOT_PYTHON || path.join(repo, '.venv', process.platform === 'win32' ? 'Scripts/python.exe' : 'bin/python');
const child = spawn(python, [path.join(repo, 'tests/browser/goal_fixture.py'), '--data-dir', fixtureDir, '--tools'], { cwd: repo, windowsHide: true });
const logs = fs.createWriteStream(path.join(outDir, 'fixture.log'));
child.stdout.pipe(logs, { end: false }); child.stderr.pipe(logs, { end: false });
let childExit, spawnError, browser, page, devServer;
child.on('error', error => { spawnError = error; });
child.on('exit', (code, signal) => { childExit = { code, signal }; });
const report = { passed: false, output: outDir, pageErrors: [], boundaries: [] };
async function until(check, description, timeout = 30000) {
  const deadline = Date.now() + timeout;
  while (Date.now() < deadline) { const value = await check(); if (value) return value; await new Promise(r => setTimeout(r, 100)); }
  throw Error(`Timed out: ${description}`);
}
try {
  await until(() => { if (spawnError || childExit) throw Error('Fixture exited'); return fs.existsSync(path.join(fixtureDir, 'manifest.json')); }, 'fixture startup');
  const manifest = JSON.parse(fs.readFileSync(path.join(fixtureDir, 'manifest.json'))), sample = manifest.tools;
  const baseURL = `http://127.0.0.1:${manifest.port}`; let uiURL = baseURL;
  browser = await chromium.launch({ headless: true });
  const context = await browser.newContext({ viewport: { width: 1280, height: 960 } });
  page = await context.newPage(); page.setDefaultTimeout(15000);
  const writes = [], reads = [];
  async function observe(tab) {
    tab.on('pageerror', error => report.pageErrors.push(String(error)));
    await tab.route('**/api/workbench/**', route => {
      if (route.request().method() !== 'GET') { writes.push(route.request().url()); return route.abort(); }
      reads.push(route.request().url()); return route.continue();
    });
  }
  await observe(page);
  const dialog = () => page.getByRole('dialog', { name: '查看工具活动', exact: true });
  const evidence = () => dialog().getByRole('region', { name: '工具活动摘要', exact: true });
  const metric = name => evidence().getByText(name, { exact: true }).evaluate(node => node.nextElementSibling.textContent);
  const cliPath = id => `/executions/${id}/tool-activities`;
  async function api(pathname) {
    const response = await context.request.get(baseURL + '/api/workbench' + pathname, { headers: { Authorization: `Bearer ${manifest.access_token}` } });
    assert.equal(response.status(), 200); return response.json();
  }
  async function openAgent(agentId) {
    await page.goto(`${uiURL}/#access_token=${manifest.access_token}`);
    await page.getByRole('button', { name: /F68 工具观察项目/ }).click();
    await page.getByLabel('查看会话成员', { exact: true }).selectOption(agentId);
    await page.getByRole('button', { name: '刷新 Agent 活动', exact: true }).waitFor();
  }
  async function openRun(run) {
    await page.locator('article').filter({ hasText: run.id }).first().getByRole('button', { name: '查看工具活动', exact: true }).click();
  }
  async function close() { await dialog().getByRole('button', { name: '关闭工具活动', exact: true }).click(); }
  async function refresh() {
    await dialog().getByRole('button', { name: '刷新工具活动', exact: true }).click();
    await until(async () => !(await dialog().getByRole('button', { name: '刷新工具活动', exact: true }).isDisabled()), 'tool read finished');
  }
  const initial = {};
  for (const key of ['observed', 'limited', 'empty', 'legacy']) initial[key] = { run: await api(`/executions/${sample[key].id}`), receipt: await api(cliPath(sample[key].id)) };
  const receipt = initial.observed.receipt;
  assert.equal(receipt.payload.events.length, 8);
  assert.equal(new Set(receipt.payload.events.slice(0, 3).map(row => row.item_sha256)).size, 1);
  assert.equal(initial.limited.receipt.payload.events.length, 500);
  assert.equal(initial.limited.receipt.payload.dropped_events, 1);
  assert.equal(initial.limited.receipt.payload.invalid_lines, 1);
  assert.equal(initial.limited.receipt.payload.unknown_items, 1);
  assert.equal(initial.limited.receipt.payload.output_limited, true);
  assert.equal(initial.empty.receipt.payload.events.length, 0); assert.equal(initial.legacy.receipt, null);
  await openAgent(sample.observed.agent_id); const beforeRead = reads.length;
  await openRun(sample.observed); await evidence().waitFor();
  assert(reads.slice(beforeRead).every(url => url.endsWith(`/executions/${sample.observed.id}`) || url.endsWith(cliPath(sample.observed.id))));
  assert(!(await dialog().innerText()).includes('F68_PRIVATE_'));
  const details = dialog().getByText('查看工具事件明细（8条）', { exact: true });
  assert.equal(await dialog().getByRole('article', { name: '工具事件第1行', exact: true }).isVisible(), false);
  await page.screenshot({ path: path.join(outDir, 'tools-summary.png') });
  await details.click();
  const command = dialog().getByRole('article', { name: '工具事件第3行', exact: true });
  assert((await command.innerText()).includes('7'));
  assert((await command.innerText()).includes('失败'));
  assert((await command.innerText()).includes('完成通知'));
  assert((await dialog().getByRole('article', { name: '工具事件第4行', exact: true }).innerText()).includes('已拒绝'));
  const web = dialog().getByRole('article', { name: '工具事件第8行', exact: true });
  assert((await web.innerText()).includes('未提供'));
  await command.getByText('查看工具内容摘要', { exact: true }).click();
  await command.getByText(receipt.payload.events[2].details.command.sha256, { exact: true }).waitFor();
  await command.scrollIntoViewIfNeeded();
  await page.screenshot({ path: path.join(outDir, 'tools-events.png') });
  await close();
  await openAgent(sample.limited.agent_id); await openRun(sample.limited); await evidence().waitFor();
  await dialog().getByText('查看工具事件明细（500条）', { exact: true }).waitFor();
  assert.equal(await metric('无效输出行'), '1');
  assert.equal(await metric('未知事件或类型'), '1');
  assert.equal(await metric('超限未保留事件'), '1');
  assert.equal(await metric('输出是否受限'), '是');
  assert(!(await dialog().innerText()).includes('F68_PRIVATE_'));
  await page.screenshot({ path: path.join(outDir, 'tools-limited.png') });
  await close(); await openRun(sample.empty); await evidence().waitFor();
  await dialog().getByText('未记录可识别的工具事件，不证明没有使用工具。', { exact: true }).waitFor();
  await close(); await openAgent(sample.legacy.agent_id); await openRun(sample.legacy);
  await dialog().getByText('没有保存工具活动回执，实际工具活动未知。', { exact: true }).waitFor();
  assert.equal(await evidence().count(), 0); await close();
  await openAgent(sample.observed.agent_id); await openRun(sample.observed); await evidence().waitFor();
  const targetURL = '**/api/workbench' + cliPath(sample.observed.id);
  for (const variant of ['run', 'agent', 'attempt', 'requirement', 'raw', 'observation', 'source', 'time', 'bool-counter', 'too-many', 'null-event', 'phase', 'status', 'sequence', 'hash', 'non-command-exit', 'raw-detail', '503']) {
    const bad = structuredClone(receipt), payload = bad.payload;
    if (variant === 'run') bad.execution_id = sample.limited.id;
    if (variant === 'agent') bad.agent_id = sample.limited.agent_id;
    if (variant === 'attempt') bad.attempt++;
    if (variant === 'requirement') bad.requirement_version++;
    if (variant === 'raw') bad.command = 'RAW_PRIVATE';
    if (variant === 'observation') payload.observation = 'live';
    if (variant === 'source') payload.source = 'owner';
    if (variant === 'time') bad.recorded_at = 'bad';
    if (variant === 'bool-counter') payload.invalid_lines = true;
    if (variant === 'too-many') payload.events = Array.from({ length: 501 }, (_, i) => ({ ...payload.events[0], sequence: i + 1 }));
    if (variant === 'null-event') payload.events[0] = null;
    if (variant === 'phase') payload.events[0].phase = 'succeeded';
    if (variant === 'status') payload.events[0].status = 'successful';
    if (variant === 'sequence') payload.events[1].sequence = 1;
    if (variant === 'hash') payload.events[0].item_sha256 = 'bad';
    if (variant === 'non-command-exit') payload.events[7].exit_code = 0;
    if (variant === 'raw-detail') payload.events[0].details.command = 'RAW_PRIVATE';
    await page.route(targetURL, route => route.fulfill({ status: variant === '503' ? 503 : 200, contentType: 'application/json', body: JSON.stringify(variant === '503' ? { error: 'Read unavailable' } : bad) }));
    await refresh(); await dialog().getByRole('alert').waitFor(); assert.equal(await evidence().count(), 0);
    await page.unroute(targetURL); await refresh(); await evidence().waitFor(); report.boundaries.push({ variant, passed: true });
  }
  const runURL = '**/api/workbench/executions/' + sample.observed.id;
  await page.route(runURL, route => route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ ...initial.observed.run, agent_id: sample.limited.agent_id }) }));
  await refresh(); await dialog().getByRole('alert').waitFor(); assert.equal(await evidence().count(), 0);
  await page.unroute(runURL); await refresh(); await evidence().waitFor(); report.boundaries.push({ variant: 'actual-run-binding', passed: true });
  await page.route(targetURL, route => route.fulfill({ status: 401, contentType: 'application/json', body: JSON.stringify({ error: 'Access expired' }) }));
  await dialog().getByRole('button', { name: '刷新工具活动', exact: true }).click();
  await page.getByLabel('访问口令', { exact: true }).waitFor(); await page.unroute(targetURL);
  await page.getByLabel('访问口令', { exact: true }).fill(manifest.access_token);
  await page.getByRole('button', { name: '验证并进入', exact: true }).click();
  await openAgent(sample.observed.agent_id); await openRun(sample.observed); await evidence().waitFor(); report.boundaries.push({ variant: '401', passed: true });
  let release, held = false;
  await page.route(targetURL, async route => { held = true; await new Promise(resolve => { release = resolve; }); await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(receipt) }).catch(() => {}); });
  await dialog().getByRole('button', { name: '刷新工具活动', exact: true }).click();
  await until(() => held, 'old tool read held'); assert.equal(await evidence().count(), 0);
  await close(); await page.getByLabel('查看会话成员', { exact: true }).selectOption(sample.limited.agent_id);
  await openRun(sample.limited); await evidence().waitFor(); release(); await page.unroute(targetURL);
  await refresh(); assert((await dialog().innerText()).includes(sample.limited.id)); assert(!(await dialog().innerText()).includes(sample.observed.id));
  report.boundaries.push({ variant: 'late-read-after-agent-switch', passed: true });
  await close(); await page.setViewportSize({ width: 390, height: 844 });
  await page.getByRole('button', { name: 'Agent 视角', exact: true }).click();
  await openRun(sample.limited); await evidence().waitFor();
  await dialog().getByText('查看工具事件明细（500条）', { exact: true }).click();
  await dialog().getByRole('article', { name: '工具事件第1行', exact: true }).getByText('查看工具内容摘要', { exact: true }).click();
  assert(await dialog().evaluate(node => node.scrollWidth <= node.clientWidth));
  await page.screenshot({ path: path.join(outDir, 'tools-mobile.png') });
  report.mobile = { passed: true, noDialogHorizontalOverflow: true };
  devServer = await createServer({ root: path.join(repo, 'frontend'), configFile: false, server: { host: '127.0.0.1', port: 0, proxy: { '/api': baseURL } } });
  await devServer.listen(); uiURL = devServer.resolvedUrls.local[0].replace(/\/$/, '');
  const mainPage = page, devContext = await browser.newContext(); page = await devContext.newPage(); page.setDefaultTimeout(15000); await observe(page);
  await openAgent(sample.observed.agent_id); await openRun(sample.observed); await evidence().waitFor(); await refresh();
  await page.keyboard.press('Escape'); assert.equal(await dialog().count(), 0);
  await until(async () => await page.evaluate(() => document.activeElement?.textContent === '查看工具活动'), 'StrictMode Escape restores tool opener', 2000);
  report.devStrictMode = { passed: true, escapeRestoresFocus: true };
  await devContext.close(); await devServer.close(); devServer = null; page = mainPage;
  for (const key of ['observed', 'limited', 'empty', 'legacy']) {
    assert.deepEqual(await api(`/executions/${sample[key].id}`), initial[key].run);
    assert.deepEqual(await api(cliPath(sample[key].id)), initial[key].receipt);
  }
  assert.equal(writes.length, 0);
  const logPath = path.join(fixtureDir, 'evidence.jsonl');
  const calls = fs.existsSync(logPath) ? fs.readFileSync(logPath, 'utf8').trim().split('\n').filter(Boolean).map(line => JSON.parse(line)) : [];
  assert.equal(calls.filter(e => ['cli_call', 'model_call'].includes(e.kind)).length, 0);
  assert.equal(report.pageErrors.length, 0); report.passed = true; report.businessWrites = 0; report.externalCalls = 0;
} catch (error) {
  report.error = String(error.stack || error); process.exitCode = 1;
  await page?.screenshot({ path: path.join(outDir, 'failure.png') }).catch(() => {});
} finally {
  await devServer?.close(); await browser?.close();
  if (fs.existsSync(fixtureDir)) { fs.writeFileSync(path.join(fixtureDir, 'shutdown'), 'stop'); await until(() => childExit || spawnError, 'shutdown', 20000).catch(() => { child.kill(); report.cleanupForced = true; }); }
  report.fixtureExit = childExit; logs.end();
  if (report.cleanupForced || childExit?.code !== 0) { report.passed = false; process.exitCode = 1; }
  fs.writeFileSync(path.join(outDir, 'browser-report.json'), JSON.stringify(report, null, 2));
  console.log(JSON.stringify({ passed: report.passed, report: path.join(outDir, 'browser-report.json'), error: report.error }));
}
