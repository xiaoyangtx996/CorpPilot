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
const outDir = fs.mkdtempSync(path.join(outputRoot, 'corppilot-context-'));
const fixtureDir = path.join(outDir, 'fixture');
const python = process.env.CORPPILOT_PYTHON || path.join(repo, '.venv', process.platform === 'win32' ? 'Scripts/python.exe' : 'bin/python');
const child = spawn(python, [path.join(repo, 'tests/browser/goal_fixture.py'), '--data-dir', fixtureDir, '--context'], { cwd: repo, windowsHide: true });
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
  const manifest = JSON.parse(fs.readFileSync(path.join(fixtureDir, 'manifest.json'))), sample = manifest.context;
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
  const dialog = () => page.getByRole('dialog', { name: '查看当次上下文', exact: true });
  const evidence = () => dialog().getByRole('region', { name: '已准备上下文摘要', exact: true });
  async function api(pathname) {
    const response = await context.request.get(baseURL + '/api/workbench' + pathname, { headers: { Authorization: `Bearer ${manifest.access_token}` } });
    assert.equal(response.status(), 200); return response.json();
  }
  async function openAgent(agentId) {
    await page.goto(`${uiURL}/#access_token=${manifest.access_token}`);
    await page.getByRole('button', { name: /F66 上下文项目/ }).click();
    await page.getByLabel('查看会话成员', { exact: true }).selectOption(agentId);
    await page.getByRole('button', { name: '刷新 Agent 活动', exact: true }).waitFor();
  }
  async function openRun(id, model = false) {
    if (model) await page.getByText(/^此身份的模型活动/).click();
    await page.locator('article').filter({ hasText: id }).first().getByRole('button', { name: '查看当次上下文', exact: true }).click();
  }
  async function refresh() {
    await dialog().getByRole('button', { name: '刷新当次上下文', exact: true }).click();
    await until(async () => !(await dialog().getByRole('button', { name: '刷新当次上下文', exact: true }).isDisabled()), 'context read finished');
  }
  const modelPath = `/runs/${sample.model.id}/context-summary`, cliPath = `/executions/${sample.execution.id}/context-summary`;
  const modelReceipt = await api(modelPath), cliReceipt = await api(cliPath);
  assert.equal(modelReceipt.messages.length, 100); assert.equal(modelReceipt.context_truncated, true);
  assert.equal(cliReceipt.memories.length, 2); assert(cliReceipt.memories.every(m => m.version === 1));
  assert.equal(cliReceipt.input_artifacts.length, 1);
  for (const memory of cliReceipt.memories) assert.equal((await api(`/memories/${memory.scope}/${memory.scope_id}`)).version, 2);
  report.boundaries = [];
  const beforeRuns = { model: await api(`/runs/${sample.model.id}`), cli: await api(`/executions/${sample.execution.id}`) };
  await openAgent(sample.model.agent_id); const beforeRead = reads.length;
  await openRun(sample.model.id, true); await evidence().waitFor();
  assert(!(await dialog().innerText()).includes('F66_PRIVATE_'));
  await dialog().getByText('fixture-context-model', { exact: true }).waitFor();
  assert((await dialog().innerText()).includes('100'));
  assert(reads.slice(beforeRead).every(url => url.endsWith(`/runs/${sample.model.id}`) || url.endsWith(modelPath)));
  await dialog().getByText('查看摘要哈希', { exact: true }).first().click();
  await dialog().getByText(modelReceipt.instructions.sha256, { exact: true }).waitFor();
  await page.screenshot({ path: path.join(outDir, 'context-model.png') });
  await dialog().getByRole('button', { name: '关闭当次上下文', exact: true }).click();
  await openAgent(sample.execution.agent_id); await openRun(sample.execution.id); await evidence().waitFor();
  await dialog().getByRole('region', { name: '当次记忆摘要', exact: true }).waitFor();
  await dialog().getByRole('region', { name: '当次输入成果摘要', exact: true }).waitFor();
  await dialog().getByText('context-proof.txt', { exact: false }).waitFor();
  assert(!(await dialog().innerText()).includes('F66_PRIVATE_'));
  await page.screenshot({ path: path.join(outDir, 'context-cli.png') });
  const memoryView = dialog().getByRole('region', { name: '当次记忆摘要', exact: true });
  assert.equal(await memoryView.locator('article').count(), 2);
  await memoryView.scrollIntoViewIfNeeded();
  await page.screenshot({ path: path.join(outDir, 'context-cli-memory.png') });
  for (const card of await memoryView.locator('article').all()) { assert((await card.innerText()).includes('v1')); assert(!(await card.innerText()).includes('v2')); }
  const cliURL = '**/api/workbench' + cliPath;
  for (const variant of ['memory-scope', 'memory-version', 'artifact-path', 'task-id']) {
    const bad = structuredClone(cliReceipt);
    if (variant === 'memory-scope') bad.memories[0].scope_id = sample.model.agent_id;
    if (variant === 'memory-version') bad.memories[0].version = 0;
    if (variant === 'artifact-path') bad.input_artifacts[0].path = 'C:/private';
    if (variant === 'task-id') bad.task.id = 'wrong-task';
    await page.route(cliURL, route => route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(bad) }));
    await refresh(); await dialog().getByRole('alert').waitFor(); assert.equal(await evidence().count(), 0);
    await page.unroute(cliURL); await refresh(); await evidence().waitFor();
    report.boundaries.push({ variant, passed: true });
  }

  await dialog().getByRole('button', { name: '关闭当次上下文', exact: true }).click();
  await openAgent(sample.legacy.agent_id); await openRun(sample.legacy.id, true);
  await dialog().getByText('没有保存当次上下文回执，实际输入未知。', { exact: true }).waitFor();
  assert.equal(await evidence().count(), 0);
  await dialog().getByRole('button', { name: '关闭当次上下文', exact: true }).click();
  await openAgent(sample.model.agent_id); await openRun(sample.model.id, true); await evidence().waitFor();
  const modelURL = '**/api/workbench' + modelPath;
  for (const variant of ['kind', 'run', 'agent', 'attempt', 'version', 'hash', 'null-message', 'too-many-messages', 'phase', '503']) {
    const bad = structuredClone(modelReceipt);
    if (variant === 'kind') bad.kind = 'cli';
    if (variant === 'run') bad.run_id = sample.legacy.id;
    if (variant === 'agent') bad.agent_id = sample.execution.agent_id;
    if (variant === 'attempt') bad.attempt++;
    if (variant === 'version') bad.requirement_version++;
    if (variant === 'hash') bad.instructions.sha256 = 'bad';
    if (variant === 'null-message') bad.messages[0] = null;
    if (variant === 'too-many-messages') bad.messages.push(bad.messages[0]);
    if (variant === 'phase') bad.phase = 'sent';
    await page.route(modelURL, route => route.fulfill({ status: variant === '503' ? 503 : 200, contentType: 'application/json', body: JSON.stringify(variant === '503' ? { error: 'Read unavailable' } : bad) }));
    await refresh(); await dialog().getByRole('alert').waitFor(); assert.equal(await evidence().count(), 0);
    await page.unroute(modelURL); await refresh(); await evidence().waitFor();
    report.boundaries.push({ variant, passed: true });
  }
  await page.route(modelURL, route => route.fulfill({ status: 401, contentType: 'application/json', body: JSON.stringify({ error: 'Access expired' }) }));
  await dialog().getByRole('button', { name: '刷新当次上下文', exact: true }).click();
  await page.getByLabel('访问口令', { exact: true }).waitFor(); await page.unroute(modelURL);
  await page.getByLabel('访问口令', { exact: true }).fill(manifest.access_token);
  await page.getByRole('button', { name: '验证并进入', exact: true }).click();
  await openAgent(sample.model.agent_id); await openRun(sample.model.id, true); await evidence().waitFor();
  report.boundaries.push({ variant: '401-reauthorization', passed: true });
  let release, held = false;
  await page.route(modelURL, async route => { held = true; await new Promise(resolve => { release = resolve; }); await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(modelReceipt) }).catch(() => {}); });
  await dialog().getByRole('button', { name: '刷新当次上下文', exact: true }).click();
  await until(() => held, 'old context read held');
  await dialog().getByRole('button', { name: '关闭当次上下文', exact: true }).click();
  await page.getByLabel('查看会话成员', { exact: true }).selectOption(sample.execution.agent_id);
  await openRun(sample.execution.id); await evidence().waitFor(); release(); await page.unroute(modelURL);
  await refresh(); assert((await dialog().innerText()).includes(sample.execution.id));
  assert(!(await dialog().innerText()).includes(sample.model.id));
  report.boundaries.push({ variant: 'late-read-after-agent-switch', passed: true });
  devServer = await createServer({ root: path.join(repo, 'frontend'), configFile: false, server: { host: '127.0.0.1', port: 0, proxy: { '/api': baseURL } } });
  await devServer.listen(); uiURL = devServer.resolvedUrls.local[0].replace(/\/$/, '');
  const mainPage = page, devContext = await browser.newContext(); page = await devContext.newPage(); page.setDefaultTimeout(15000); await observe(page);
  await openAgent(sample.model.agent_id); await openRun(sample.model.id, true); await evidence().waitFor(); await refresh();
  await page.keyboard.press('Escape'); assert.equal(await dialog().count(), 0);
  await until(async () => await page.evaluate(() => document.activeElement?.textContent === '查看当次上下文'), 'StrictMode Escape restores context opener', 2000);
  report.devStrictMode = { passed: true, escapeRestoresFocus: true };
  await devContext.close(); await devServer.close(); devServer = null; page = mainPage;
  assert.deepEqual(await api(`/runs/${sample.model.id}`), beforeRuns.model);
  assert.deepEqual(await api(`/executions/${sample.execution.id}`), beforeRuns.cli);
  assert.deepEqual(await api(modelPath), modelReceipt); assert.deepEqual(await api(cliPath), cliReceipt);
  assert.equal(writes.length, 0); assert.equal(events().filter(e => ['cli_call', 'model_call'].includes(e.kind)).length, 0);
  assert.equal(report.pageErrors.length, 0);
  report.passed = true; report.businessWrites = 0; report.externalCalls = 0;
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
