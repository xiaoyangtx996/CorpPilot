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
const out = fs.mkdtempSync(path.join(root, 'corppilot-memory-inputs-')), fixtureDir = path.join(out, 'fixture');
const python = process.env.CORPPILOT_PYTHON || path.join(repo, '.venv', process.platform === 'win32' ? 'Scripts/python.exe' : 'bin/python');
const child = spawn(python, [path.join(repo, 'tests/browser/goal_fixture.py'), '--data-dir', fixtureDir, '--chat-memories'], { cwd: repo, windowsHide: true });
const logs = fs.createWriteStream(path.join(out, 'fixture.log')); child.stdout.pipe(logs, { end: false }); child.stderr.pipe(logs, { end: false });
let childExit, spawnError, browser, page, devServer;
child.on('error', e => { spawnError = e; }); child.on('exit', (code, signal) => { childExit = { code, signal }; });
const report = { passed: false, output: out, pageErrors: [], boundaries: [] };
async function until(check, name, timeout = 30000) { const end = Date.now() + timeout; while (Date.now() < end) { if (await check()) return; await new Promise(r => setTimeout(r, 100)); } throw Error('Timed out: ' + name); }
try {
  await until(() => { if (spawnError || childExit) throw Error('Fixture exited'); return fs.existsSync(path.join(fixtureDir, 'manifest.json')); }, 'startup');
  const manifest = JSON.parse(fs.readFileSync(path.join(fixtureDir, 'manifest.json'))), sample = manifest.context;
  const baseURL = `http://127.0.0.1:${manifest.port}`; let uiURL = baseURL;
  browser = await chromium.launch({ headless: true }); const context = await browser.newContext({ viewport: { width: 1280, height: 960 } });
  page = await context.newPage(); page.setDefaultTimeout(15000); const writes = [], reads = [];
  page.on('pageerror', e => report.pageErrors.push(String(e)));
  await page.route('**/api/workbench/**', route => { if (route.request().method() !== 'GET') { writes.push(route.request().url()); return route.abort(); } reads.push(route.request().url()); return route.continue(); });
  async function api(url) { const r = await context.request.get(baseURL + '/api/workbench' + url, { headers: { Authorization: `Bearer ${manifest.access_token}` } }); assert.equal(r.status(), 200); return r.json(); }
  const dialog = () => page.getByRole('dialog', { name: '查看当次上下文', exact: true });
  const region = () => dialog().getByRole('region', { name: '当次批准记忆输入', exact: true });
  async function openRun(run, model = true) {
    await page.goto(`${uiURL}/#access_token=${manifest.access_token}`);
    const room = await api('/conversations/' + (run.conversation_id || sample.room.id));
    await page.getByRole('button', { name: new RegExp(room.title) }).click();
    await page.getByLabel('查看会话成员', { exact: true }).selectOption(run.agent_id);
    if (model) await page.getByText(/^此身份的模型活动/).click();
    await page.locator('.inspector article').filter({ hasText: run.id }).first().getByRole('button', { name: '查看当次上下文', exact: true }).click();
    if (model) { await dialog().getByText('查看当次批准记忆', { exact: true }).click(); await region().waitFor(); }
  }
  async function refresh() { await region().getByRole('button', { name: '刷新当次批准记忆', exact: true }).click(); await until(async () => !(await region().getByRole('button', { name: '刷新当次批准记忆', exact: true }).isDisabled()), 'refresh'); }
  const frozen = await api(`/runs/${sample.memory_model.id}/memories`);
  assert(frozen.memories.every(m => m.version === 1));
  const refs = Object.fromEntries(frozen.memories.map(m => [m.scope, m]));
  const historyPath = `/memories/agent/${sample.memory_model.agent_id}/history`;
  const history = await api(historyPath), old = history.find(m => m.version === 1);
  assert.equal(history.at(-1).version, 2); assert.equal(history.at(-1).content, '');
  await openRun(sample.memory_model);
  await region().getByRole('button', { name: '查看当次个人记忆正文 · v1', exact: true }).click();
  await region().getByText(old.content, { exact: true }).waitFor();
  await region().getByRole('button', { name: '查看当次项目记忆正文 · v1', exact: true }).click();
  const projectOld = (await api(`/memories/project/${sample.memory_model.conversation_id}/history`)).find(m => m.version === 1);
  await region().getByText(projectOld.content, { exact: true }).waitFor();
  assert.equal(await region().locator('img,script,iframe').count(), 0);
  assert.equal(await page.evaluate(() => window.F80_MEMORY_EXECUTED), undefined);
  await region().getByText('查看固定记忆摘要哈希', { exact: true }).first().click();
  assert((await region().innerText()).includes(refs.agent.sha256));
  await page.screenshot({ path: path.join(out, 'memory-fixed.png') });
  const endpoint = `**/api/workbench/runs/${sample.memory_model.id}/memories`;
  for (const variant of ['kind', 'run_id', 'agent_id', 'conversation_id', 'attempt', 'requirement_version', 'scope', 'missing-scope', 'duplicate-scope', 'zero-hash', '503']) {
    const bad = structuredClone(frozen);
    if (['kind', 'run_id', 'agent_id', 'conversation_id'].includes(variant)) bad[variant] = 'wrong';
    if (['attempt', 'requirement_version'].includes(variant)) bad[variant]++;
    if (variant === 'scope') bad.memories[0].scope_id = 'foreign';
    if (variant === 'missing-scope') bad.memories.pop();
    if (variant === 'duplicate-scope') bad.memories[1] = bad.memories[0];
    if (variant === 'zero-hash') { bad.memories[0].version = 0; bad.memories[0].chars = 0; bad.memories[0].sha256 = '0'.repeat(64); }
    await page.route(endpoint, route => route.fulfill({ status: variant === '503' ? 503 : 200, contentType: 'application/json', body: JSON.stringify(variant === '503' ? { error: 'F80 unavailable' } : bad) }));
    await refresh(); await region().getByRole('alert').waitFor(); assert.equal(await region().getByRole('button', { name: /查看当次个人记忆正文/ }).count(), 0);
    await page.unroute(endpoint); await refresh(); await region().getByRole('button', { name: '查看当次个人记忆正文 · v1', exact: true }).waitFor(); report.boundaries.push(variant);
  }
  for (const variant of ['missing-version', 'duplicate-version', 'changed-content', 'wrong-scope']) {
    const bad = structuredClone(history);
    if (variant === 'missing-version') bad.splice(bad.findIndex(m => m.version === 1), 1);
    if (variant === 'duplicate-version') bad.push(old);
    if (variant === 'changed-content') { const row = bad.find(m => m.version === 1); const chars = Array.from(row.content); chars[0] = chars[0] === 'X' ? 'Y' : 'X'; row.content = chars.join(''); assert.equal(Array.from(row.content).length, refs.agent.chars); }
    if (variant === 'wrong-scope') bad.find(m => m.version === 1).scope_id = 'foreign';
    await page.route('**/api/workbench' + historyPath, route => route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(bad) }));
    await region().getByRole('button', { name: '查看当次个人记忆正文 · v1', exact: true }).click();
    await region().getByRole('alert').waitFor(); assert.equal(await region().getByText(old.content, { exact: true }).count(), 0);
    await page.unroute('**/api/workbench' + historyPath); await refresh(); report.boundaries.push(variant);
  }
  const summaryURL = `**/api/workbench/runs/${sample.memory_model.id}/context-summary`;
  await page.route(summaryURL, route => route.fulfill({ status: 503, contentType: 'application/json', body: JSON.stringify({ error: 'F80 context unavailable' }) }));
  await dialog().getByRole('button', { name: '刷新当次上下文', exact: true }).click(); await dialog().getByText('F80 context unavailable', { exact: true }).waitFor();
  await refresh(); await region().getByRole('button', { name: '查看当次个人记忆正文 · v1', exact: true }).waitFor();
  await page.unroute(summaryURL); report.boundaries.push('independent-context-error');
  let releaseBody, bodyHeld = false, bodyFinished;
  const bodyComplete = new Promise(resolve => { bodyFinished = resolve; });
  await page.route('**/api/workbench' + historyPath, async route => { bodyHeld = true; await new Promise(r => { releaseBody = r; }); await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(history) }).catch(() => {}); bodyFinished(); });
  await region().getByRole('button', { name: '查看当次个人记忆正文 · v1', exact: true }).click(); await until(() => bodyHeld, 'held body');
  await refresh(); releaseBody(); await page.unroute('**/api/workbench' + historyPath);
  await bodyComplete; await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
  assert.equal(await region().getByText(old.content, { exact: true }).count(), 0);
  report.boundaries.push('late-body-after-refresh');
  await page.route(endpoint, route => route.fulfill({ status: 401, contentType: 'application/json', body: JSON.stringify({ error: 'expired' }) }));
  await region().getByRole('button', { name: '刷新当次批准记忆', exact: true }).click(); await page.getByLabel('访问口令', { exact: true }).waitFor();
  await page.unroute(endpoint); await page.getByLabel('访问口令', { exact: true }).fill(manifest.access_token); await page.getByRole('button', { name: '验证并进入', exact: true }).click();
  await openRun(sample.memory_model); report.boundaries.push('401-reauthorization');
  let held = false, release;
  await page.route(endpoint, async route => { held = true; await new Promise(r => { release = r; }); await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(frozen) }).catch(() => {}); });
  await region().getByRole('button', { name: '刷新当次批准记忆', exact: true }).click(); await until(() => held, 'held memory');
  await page.keyboard.press('Escape');
  await page.locator('.inspector article').filter({ hasText: sample.memory_empty.id }).first().getByRole('button', { name: '查看当次上下文', exact: true }).click();
  await dialog().getByText('查看当次批准记忆', { exact: true }).click(); release(); await page.unroute(endpoint);
  await region().getByRole('button', { name: '查看当次个人记忆正文 · v2', exact: true }).click();
  await region().getByText('当次固定版本正文为空。', { exact: true }).waitFor();
  assert.equal(await region().getByText(old.content, { exact: true }).count(), 0); report.boundaries.push('late-response-different-run', 'nonzero-empty');
  await page.keyboard.press('Escape'); const beforeZero = reads.length; await openRun(sample.model); await region().getByText('当次固定为空记忆（v0）。', { exact: true }).waitFor();
  assert(!reads.slice(beforeZero).some(url => /\/memories\/(agent|project)\//.test(url)));
  await page.keyboard.press('Escape'); await openRun(sample.legacy); await region().getByText('没有保存当次批准记忆绑定，实际输入未知。', { exact: true }).waitFor();
  await page.keyboard.press('Escape'); await openRun(sample.memory_dm);
  await region().getByRole('button', { name: '查看当次个人记忆正文 · v1', exact: true }).click(); await region().getByText(old.content, { exact: true }).waitFor();
  assert.equal(await region().getByRole('button', { name: /查看当次项目记忆正文/ }).count(), 0);
  await page.keyboard.press('Escape'); await openRun(sample.execution, false); assert.equal(await dialog().getByText('查看当次批准记忆', { exact: true }).count(), 0);
  report.boundaries.push('version-zero', 'missing-history', 'dm-only-personal', 'cli-existing-view');
  await page.keyboard.press('Escape'); await openRun(sample.memory_model); await page.keyboard.press('Escape');
  await page.setViewportSize({ width: 390, height: 844 }); await page.getByRole('button', { name: 'Agent 视角', exact: true }).click();
  await page.locator('.inspector article').filter({ hasText: sample.memory_model.id }).first().getByRole('button', { name: '查看当次上下文', exact: true }).click(); await dialog().getByText('查看当次批准记忆', { exact: true }).click();
  await region().getByRole('button', { name: '查看当次个人记忆正文 · v1', exact: true }).click(); await region().getByText(old.content, { exact: true }).waitFor();
  assert(await dialog().evaluate(el => el.scrollWidth <= el.clientWidth + 1)); assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth));
  await page.screenshot({ path: path.join(out, 'memory-mobile.png') });
  devServer = await createServer({ root: path.join(repo, 'frontend'), configFile: false, server: { host: '127.0.0.1', port: 0, proxy: { '/api': baseURL } } });
  await devServer.listen(); uiURL = devServer.resolvedUrls.local[0].replace(/\/$/, ''); await page.setViewportSize({ width: 1280, height: 960 });
  await openRun(sample.memory_model); await region().getByRole('button', { name: '查看当次个人记忆正文 · v1', exact: true }).click(); await region().getByText(old.content, { exact: true }).waitFor();
  await page.keyboard.press('Escape'); await until(async () => await page.evaluate(() => document.activeElement?.textContent === '查看当次上下文'), 'StrictMode focus', 2000);
  assert.equal(writes.length, 0); assert.equal(report.pageErrors.length, 0);
  const events = fs.existsSync(path.join(fixtureDir, 'evidence.jsonl')) ? fs.readFileSync(path.join(fixtureDir, 'evidence.jsonl'), 'utf8').trim().split('\n').filter(Boolean).map(JSON.parse) : [];
  assert.equal(events.filter(e => ['model_call', 'cli_call'].includes(e.kind)).length, 0);
  assert.deepEqual(await api(`/runs/${sample.memory_model.id}/memories`), frozen);
  assert(reads.filter(url => /\/memories\/agent\//.test(url)).every(url => url.endsWith('/history')));
  report.passed = true; report.businessWrites = 0; report.externalCalls = 0;
} catch (error) { report.error = String(error.stack || error); process.exitCode = 1; await page?.screenshot({ path: path.join(out, 'failure.png') }).catch(() => {}); }
finally {
  await devServer?.close(); await browser?.close();
  if (fs.existsSync(fixtureDir)) { fs.writeFileSync(path.join(fixtureDir, 'shutdown'), 'stop'); await until(() => childExit || spawnError, 'shutdown', 20000).catch(() => { child.kill(); report.cleanupForced = true; }); }
  report.fixtureExit = childExit; logs.end(); if (report.cleanupForced || childExit?.code !== 0) { report.passed = false; process.exitCode = 1; }
  fs.writeFileSync(path.join(out, 'browser-report.json'), JSON.stringify(report, null, 2)); console.log(JSON.stringify({ passed: report.passed, report: path.join(out, 'browser-report.json'), error: report.error }));
}
