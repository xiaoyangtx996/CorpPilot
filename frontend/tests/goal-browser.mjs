import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { spawn } from 'node:child_process';
import { createHash } from 'node:crypto';
import { chromium } from 'playwright';
import { createServer } from 'vite';
import { runBoundaries } from './goal-boundaries.mjs';

const repo = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..');
const outputRoot = process.env.CORPPILOT_BROWSER_OUTPUT || os.tmpdir();
fs.mkdirSync(outputRoot, { recursive: true });
const outDir = fs.mkdtempSync(path.join(outputRoot, 'corppilot-browser-'));
const fixtureDir = path.join(outDir, 'fixture');
const python = process.env.CORPPILOT_PYTHON || path.join(repo, '.venv', process.platform === 'win32' ? 'Scripts/python.exe' : 'bin/python');
const child = spawn(python, [path.join(repo, 'tests/browser/goal_fixture.py'), '--data-dir', fixtureDir], { cwd: repo, windowsHide: true });
const logs = fs.createWriteStream(path.join(outDir, 'fixture.log'));
child.stdout.pipe(logs, { end: false }); child.stderr.pipe(logs, { end: false });
let childExit, spawnError, browser, page, devServer;
child.on('error', error => { spawnError = error; });
child.on('exit', (code, signal) => { childExit = { code, signal }; });
const report = { passed: false, output: outDir, pageErrors: [], consoleErrors: [], errorResponses: [] };
const delay = ms => new Promise(resolve => setTimeout(resolve, ms));
async function until(check, description, timeout = 30000) {
  const deadline = Date.now() + timeout;
  while (Date.now() < deadline) { const value = await check(); if (value) return value; await delay(100); }
  throw Error(`Timed out: ${description}`);
}
function controls(value) {
  const target = path.join(fixtureDir, 'control.json');
  fs.writeFileSync(target + '.tmp', JSON.stringify(value)); fs.renameSync(target + '.tmp', target);
}
function events() {
  const target = path.join(fixtureDir, 'evidence.jsonl');
  return fs.existsSync(target) ? fs.readFileSync(target, 'utf8').trim().split('\n').filter(Boolean).map(line => JSON.parse(line)) : [];
}
try {
  await until(() => {
    if (spawnError) throw spawnError;
    if (childExit) throw Error(`Fixture exited: ${JSON.stringify(childExit)}`);
    return fs.existsSync(path.join(fixtureDir, 'manifest.json'));
  }, 'fixture startup');
  const manifest = JSON.parse(fs.readFileSync(path.join(fixtureDir, 'manifest.json')));
  const baseURL = `http://127.0.0.1:${manifest.port}`;
  browser = await chromium.launch({ headless: true, ...(process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE ? { executablePath: process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE } : {}) });
  devServer = await createServer({ root: path.join(repo, 'frontend'), configFile: false, server: { host: '127.0.0.1', port: 0, proxy: { '/api': baseURL } } });
  await devServer.listen();
  const devContext = await browser.newContext();
  const devPage = await devContext.newPage();
  devPage.on('pageerror', error => report.pageErrors.push(String(error)));
  await devPage.goto(`${devServer.resolvedUrls.local[0]}#access_token=${manifest.access_token}`);
  await devPage.getByRole('button', { name: /F53 QA Secretary/ }).click();
  await devPage.locator(`#message-${manifest.source_message_id}`).getByRole('button', { name: '交给秘书组织并执行' }).click();
  await devPage.getByLabel('明确共享给秘书与项目的摘要').fill('StrictMode form is ready without authorization.');
  await devPage.getByRole('button', { name: '刷新目标状态', exact: true }).click();
  await devPage.getByLabel('明确共享给秘书与项目的摘要').fill('StrictMode refresh remains usable.');
  assert.equal(events().filter(e => e.kind === 'goal_post').length, 0);
  await devPage.screenshot({ path: path.join(outDir, 'goal-dev-strictmode.png') });
  report.devStrictMode = { passed: true, goalPosts: 0 };
  await devContext.close(); await devServer.close(); devServer = null;
  const context = await browser.newContext({ viewport: { width: 1280, height: 900 } });
  page = await context.newPage(); page.setDefaultTimeout(15000);
  page.on('pageerror', error => report.pageErrors.push(String(error)));
  page.on('console', message => { if (message.type() === 'error') report.consoleErrors.push(message.text()); });
  page.on('response', response => { if (response.status() >= 400) report.errorResponses.push({ url: response.url(), status: response.status() }); });
  const pendingKey = 'corppilot.goal-execution-pending.v1';
  const modal = () => page.getByRole('dialog', { name: '目标执行与恢复', exact: true });
  const pending = () => page.evaluate(key => JSON.parse(sessionStorage.getItem(key) || 'null'), pendingKey);
  async function readGoal(id) {
    const response = await context.request.get(`${baseURL}/api/workbench/goal-executions/${id}`, { headers: { Authorization: `Bearer ${manifest.access_token}` } });
    assert.equal(response.status(), 200); return response.json();
  }
  async function openTarget() {
    await page.goto(`${baseURL}/#access_token=${manifest.access_token}`);
    await page.getByRole('button', { name: /F53 QA Secretary/ }).click();
    await page.locator(`#message-${manifest.source_message_id}`).getByRole('button', { name: '交给秘书组织并执行' }).click();
  }
  async function fillGoal(brief) {
    const dialog = modal();
    const summary = dialog.getByLabel('明确共享给秘书与项目的摘要');
    assert.equal(await summary.inputValue(), '');
    await summary.fill(brief);
    await dialog.getByLabel('目标协调人').selectOption(manifest.coordinator_id);
    for (const name of manifest.agent_names) await dialog.getByRole('checkbox', { name, exact: true }).check();
    await dialog.getByLabel('最多任务数').fill('3');
    await dialog.getByRole('checkbox', { name: /授权固定首批成果按依赖/ }).check();
    await dialog.getByRole('checkbox', { name: /我确认共享上述摘要/ }).check();
    await dialog.getByLabel('最多任务数').fill('2');
    assert.equal(await dialog.getByRole('checkbox', { name: /我确认共享上述摘要/ }).isChecked(), false);
    await dialog.getByLabel('最多任务数').fill('3');
    await dialog.getByRole('checkbox', { name: /我确认共享上述摘要/ }).check();
  }

  controls({ reject_first: true, pause_runner: true });
  await openTarget(); await fillGoal('F53 EXPLICIT SHARED: produce three linked evidence files.');
  await page.screenshot({ path: path.join(outDir, 'goal-draft.png') });
  await modal().getByRole('button', { name: '授权秘书组织并执行', exact: true }).click();
  await modal().getByRole('button', { name: '修改首次未接受的目标授权' }).waitFor();
  const original = await pending(); assert(original?.rejected);
  controls({ drop_goal: true, get503: true, pause_runner: true });
  await modal().getByRole('button', { name: '同键核对原目标授权' }).click();
  await until(() => events().find(e => e.kind === 'goal_accepted'), 'accepted goal before dropped response');
  await page.reload(); await page.getByRole('button', { name: '目标执行与恢复', exact: true }).click();
  assert.deepEqual((await pending()).payload, original.payload);
  assert.equal((await pending()).rejected, undefined);
  await page.screenshot({ path: path.join(outDir, 'goal-recovery.png') });
  controls({});
  await modal().getByRole('button', { name: '刷新目标状态' }).click();
  const accepted = events().find(e => e.kind === 'goal_accepted').goal_id;
  let receipt = await until(async () => {
    const row = await readGoal(accepted);
    return row.batch?.items.every(i => i.execution.state === 'awaiting_review') ? row : null;
  }, 'automatic three-task handoff');
  await modal().getByRole('button', { name: '确认已读回并结束原请求核对' }).click();
  assert.equal(await pending(), null);
  assert.equal(events().filter(e => e.kind === 'model_call').length, 1);
  const calls = events().filter(e => e.kind === 'cli_call');
  assert.equal(new Set(calls.map(c => c.execution_id)).size, 3);
  assert.deepEqual(calls.map(c => c.input_ids.length), [0, 1, 1]);
  assert(calls.every(c => !c.private_leaked));
  assert(!JSON.stringify(events().filter(e => e.kind === 'model_call')).includes('F53_PRIVATE_'));
  assert(receipt.batch.items.every(i => i.review === null));
  assert.equal(receipt.launch.request_payload.plan.shared_brief, original.payload.shared_brief);
  await modal().getByRole('button', { name: '查看此目标固定批次' }).waitFor();
  await page.screenshot({ path: path.join(outDir, 'goal-chain.png') });
  await modal().getByRole('button', { name: '查看此目标固定批次' }).click();
  await until(async () => (await page.getByRole('dialog').last().innerText()).includes(receipt.batch.id), 'fixed batch readback in dialog');
  await page.getByRole('dialog').last().getByRole('button', { name: '关闭', exact: true }).click();
  await modal().getByRole('button', { name: '打开此目标的项目群' }).click();
  await page.getByRole('heading', { name: 'F53 自动协作成果', exact: true }).waitFor();
  const finalId = receipt.batch.items.at(-1).execution.id;
  const artifactsResponse = await context.request.get(`${baseURL}/api/workbench/executions/${finalId}/artifacts`, { headers: { Authorization: `Bearer ${manifest.access_token}` } });
  assert.equal(artifactsResponse.status(), 200);
  const artifacts = await artifactsResponse.json(); assert.equal(artifacts.length, 1);
  const download = await context.request.get(`${baseURL}/api/workbench/artifacts/${artifacts[0].id}/download`, { headers: { Authorization: `Bearer ${manifest.access_token}` } });
  assert.equal(download.status(), 200);
  const bytes = await download.body();
  assert.equal(createHash('sha256').update(bytes).digest('hex'), artifacts[0].sha256);
  assert(bytes.toString().includes(finalId));
  const card = page.locator('article.task-card').filter({ has: page.getByRole('heading', { name: 'F53 汇总', exact: true }) });
  await card.getByRole('button', { name: '执行记录与控制', exact: true }).click();
  const reviewDialog = page.getByRole('dialog', { name: '任务执行 · F53 汇总', exact: true });
  await reviewDialog.getByText('成果与 Owner 评审', { exact: true }).click();
  await reviewDialog.getByLabel('评审理由', { exact: true }).fill('Fixture final artifact downloaded; SHA-256 and execution identity verified.');
  await reviewDialog.getByLabel('我已查验全部成果，确认符合本次需求与验收标准').check();
  await reviewDialog.getByRole('button', { name: '确认提交评审', exact: true }).click();
  await reviewDialog.getByLabel('Owner 评审决定').waitFor();
  receipt = await readGoal(accepted);
  assert.deepEqual(receipt.batch.items.map(item => item.review?.decision ?? null), [null, null, 'approved']);
  await page.screenshot({ path: path.join(outDir, 'goal-final-review.png') });
  await reviewDialog.getByRole('button', { name: '关闭', exact: true }).click();
  report.main = { goalId: accepted, planningRunId: receipt.planning_run_id, launchId: receipt.launch_id, batchId: receipt.batch.id, modelCalls: 1, cliCalls: 3, finalArtifacts: artifacts };
  fs.writeFileSync(path.join(outDir, 'goal-receipt.json'), JSON.stringify(receipt, null, 2));
  report.boundaries = await runBoundaries({ browser, baseURL, manifest, receipt, outDir });
  report.stops = [];
  for (const phase of ['model', 'execution']) {
    controls({ pause_model: phase === 'model', pause_runner: phase === 'execution' });
    await openTarget(); await fillGoal(`F53 EXPLICIT SHARED: stop during ${phase}.`);
    const acceptedBefore = events().filter(e => e.kind === 'goal_accepted').length;
    await modal().getByRole('button', { name: '授权秘书组织并执行', exact: true }).click();
    await until(() => events().filter(e => e.kind === 'goal_accepted').length > acceptedBefore, 'new stop scenario accepted');
    const goalId = events().filter(e => e.kind === 'goal_accepted').at(-1).goal_id;
    await until(async () => {
      const row = await readGoal(goalId);
      return phase === 'model' ? row.planning.state === 'running' : row.batch?.items.some(i => i.execution.state === 'running');
    }, `goal running during ${phase}`);
    await modal().getByRole('checkbox', { name: /我确认停止此目标的后续调度/ }).check();
    controls({ pause_model: phase === 'model', pause_runner: phase === 'execution', drop_stop: true, get503: true });
    await modal().getByRole('button', { name: '确认停止此目标', exact: true }).click();
    await until(() => events().some(e => e.kind === 'stop_accepted' && e.goal_id === goalId && e.dropped), 'stop response actually dropped');
    const stopping = await page.evaluate(() => sessionStorage.getItem('corppilot.goal-stop-pending.v1'));
    assert.equal(JSON.parse(stopping).goal_id, goalId);
    controls({ get503: true, pause_model: phase === 'model', pause_runner: phase === 'execution' });
    await page.reload();
    await page.getByRole('button', { name: '目标执行与恢复', exact: true }).click();
    assert.equal((await pending()).goal_id, goalId);
    assert.equal(await page.evaluate(() => sessionStorage.getItem('corppilot.goal-stop-pending.v1')), stopping);
    controls({ pause_runner: phase === 'execution' });
    await modal().getByRole('button', { name: '刷新目标状态' }).click();
    await until(async () => (await readGoal(goalId)).stop_requested, 'durable stop after lost response');
    const stopped = await until(async () => {
      const row = await readGoal(goalId);
      return phase === 'model' ? !['queued', 'running'].includes(row.planning.state) && row : row.batch?.items.every(i => i.execution.state === 'cancelled') && row;
    }, `stopped ${phase}`);
    if (phase === 'model') assert.equal(stopped.launch, null);
    assert.equal(events().filter(e => e.kind === 'stop_post' && e.path.includes(goalId)).length, 1);
    await modal().getByRole('button', { name: '确认已读回并结束原请求核对' }).click();
    assert.equal(await pending(), null);
    report.stops.push({ phase, goalId, state: stopped.state, planning: stopped.planning.state, batchStates: stopped.batch?.items.map(i => i.execution.state) });
    await page.screenshot({ path: path.join(outDir, `goal-stop-${phase}.png`) });
  }
  assert.equal(events().filter(e => e.kind === 'model_call').length, 3);
  assert.equal(events().filter(e => e.kind === 'cli_call').length, 4);
  await modal().getByRole('button', { name: '关闭', exact: true }).click();
  await page.getByRole('button', { name: 'CLI 设置', exact: true }).click();
  let settingsDialog = page.getByRole('dialog', { name: 'CLI 设置', exact: true });
  await settingsDialog.getByLabel('启用本机资源准入', { exact: true }).check();
  await settingsDialog.getByLabel('为宿主保留内存（MiB）').fill('2048');
  await settingsDialog.getByLabel('每个本地执行预留内存（MiB）').fill('1536');
  await settingsDialog.getByLabel('每个本地执行预留逻辑 CPU 数').fill('2');
  await settingsDialog.getByRole('button', { name: '保存配置', exact: true }).click();
  await settingsDialog.getByText('CLI 配置已保存，后续调度使用新配置。', { exact: true }).waitFor();
  await settingsDialog.getByRole('button', { name: '关闭', exact: true }).first().click();
  await page.getByRole('button', { name: 'CLI 设置', exact: true }).click();
  settingsDialog = page.getByRole('dialog', { name: 'CLI 设置', exact: true });
  assert.equal(await settingsDialog.getByLabel('为宿主保留内存（MiB）').inputValue(), '2048');
  assert.equal(await settingsDialog.getByLabel('启用本机资源准入', { exact: true }).isChecked(), true);
  await settingsDialog.getByRole('button', { name: '刷新本机资源', exact: true }).click();
  const resourceResponse = await context.request.get(`${baseURL}/api/workbench/cli-runtime`, { headers: { Authorization: `Bearer ${manifest.access_token}` } });
  assert.equal(resourceResponse.status(), 200);
  const resourceState = (await resourceResponse.json()).resource_admission;
  assert.equal(resourceState.enabled, true);
  assert(resourceState.available_memory_mb > 0 && resourceState.cpu_count > 0);
  assert.equal(resourceState.reserved_memory_mb, 0);
  assert.equal(resourceState.reserved_cpus, 0);
  report.resources = { passed: true, snapshot: resourceState };
  await page.screenshot({ path: path.join(outDir, 'resource-settings.png') });
  assert.equal(events().filter(e => e.kind === 'model_call').length, 3);
  assert.equal(events().filter(e => e.kind === 'cli_call').length, 4);
  assert.equal(report.pageErrors.length, 0);
  assert(report.errorResponses.every(row => [400, 503].includes(row.status)), 'Unexpected HTTP failure');
  assert(report.consoleErrors.every(message => /Failed to load resource:.*(?:400|503|ERR_EMPTY_RESPONSE)/.test(message)), 'Unexpected console error');
  report.passed = true;
} catch (error) {
  report.error = String(error.stack || error); process.exitCode = 1;
  if (page) await page.screenshot({ path: path.join(outDir, 'failure.png') }).catch(() => {});
} finally {
  await devServer?.close();
  await browser?.close();
  if (fs.existsSync(fixtureDir)) {
    controls({}); fs.writeFileSync(path.join(fixtureDir, 'shutdown'), 'stop');
    await until(() => childExit || spawnError, 'fixture shutdown', 20000).catch(() => { child.kill(); report.cleanupForced = true; });
  }
  report.fixtureExit = childExit; report.fixtureSpawnError = spawnError?.message;
  logs.end();
  if (report.cleanupForced || childExit?.code !== 0) { report.passed = false; process.exitCode = 1; }
  fs.writeFileSync(path.join(outDir, 'browser-report.json'), JSON.stringify(report, null, 2));
  console.log(JSON.stringify({ passed: report.passed, report: path.join(outDir, 'browser-report.json'), error: report.error }));
}
