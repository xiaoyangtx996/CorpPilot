import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { spawn } from 'node:child_process';
import { chromium } from 'playwright';

const repo = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..');
const outputRoot = process.env.CORPPILOT_BROWSER_OUTPUT || os.tmpdir();
fs.mkdirSync(outputRoot, { recursive: true });
const outDir = fs.mkdtempSync(path.join(outputRoot, 'corppilot-checkpoint-'));
const fixtureDir = path.join(outDir, 'fixture');
const python = process.env.CORPPILOT_PYTHON || path.join(repo, '.venv', process.platform === 'win32' ? 'Scripts/python.exe' : 'bin/python');
const child = spawn(python, [path.join(repo, 'tests/browser/goal_fixture.py'), '--data-dir', fixtureDir, '--checkpoint'], { cwd: repo, windowsHide: true });
const logs = fs.createWriteStream(path.join(outDir, 'fixture.log'));
child.stdout.pipe(logs, { end: false }); child.stderr.pipe(logs, { end: false });
let childExit, spawnError, browser, page;
child.on('error', error => { spawnError = error; });
child.on('exit', (code, signal) => { childExit = { code, signal }; });
const report = { passed: false, output: outDir, pageErrors: [] };
async function until(check, description, timeout = 30000) {
  const deadline = Date.now() + timeout;
  while (Date.now() < deadline) { const value = await check(); if (value) return value; await new Promise(r => setTimeout(r, 100)); }
  throw Error(`Timed out: ${description}`);
}
function controls(value) { const target = path.join(fixtureDir, 'control.json'); fs.writeFileSync(target + '.tmp', JSON.stringify(value)); fs.renameSync(target + '.tmp', target); }
function events() { return fs.readFileSync(path.join(fixtureDir, 'evidence.jsonl'), 'utf8').trim().split('\n').filter(Boolean).map(line => JSON.parse(line)); }
try {
  await until(() => { if (spawnError || childExit) throw Error('Fixture exited'); return fs.existsSync(path.join(fixtureDir, 'manifest.json')); }, 'fixture startup');
  const manifest = JSON.parse(fs.readFileSync(path.join(fixtureDir, 'manifest.json')));
  const baseURL = `http://127.0.0.1:${manifest.port}`;
  browser = await chromium.launch({ headless: true });
  const context = await browser.newContext({ viewport: { width: 1280, height: 900 } });
  page = await context.newPage(); page.setDefaultTimeout(15000);
  page.on('pageerror', error => report.pageErrors.push(String(error)));
  const sourceId = manifest.checkpoint.batch.id;
  const pendingKey = `corppilot.checkpoint-recovery-pending.v1.${sourceId}`;
  const recovery = () => page.getByRole('dialog', { name: '检查点恢复', exact: true });
  const batch = () => page.getByRole('dialog', { name: '批量执行协作项目 · F60 检查点项目', exact: true });
  async function get(url) { const res = await context.request.get(baseURL + '/api/workbench' + url, { headers: { Authorization: `Bearer ${manifest.access_token}` } }); assert.equal(res.status(), 200); return res.json(); }
  async function open(source = sourceId) {
    await page.goto(`${baseURL}/#access_token=${manifest.access_token}`);
    await page.getByRole('button', { name: /F60 检查点项目/ }).click();
    await page.getByRole('button', { name: '协作计划与恢复', exact: true }).click();
    await page.getByRole('button', { name: '批量执行当前项目任务', exact: true }).click();
    await batch().getByText('批量执行历史', { exact: true }).click();
    await batch().getByRole('button', { name: `查看批次 ${source}`, exact: true }).click();
    await batch().getByRole('button', { name: '从本批检查点恢复', exact: true }).click();
  }
  await open();
  const submit = () => recovery().getByRole('button', { name: '确认从检查点创建新批次', exact: true });
  await recovery().getByLabel('恢复核查说明', { exact: true }).fill('Original effects inspected; restore only failed work.');
  assert(await submit().isDisabled());
  await recovery().getByLabel('我已核查原实例与外部影响，并授权重试节点产生新的 CLI、模型调用及费用', { exact: true }).check();
  controls({ pause_runner: true, drop_checkpoint: true, checkpoint_get503: true });
  await submit().click();
  await until(() => events().find(e => e.kind === 'checkpoint_accepted'), 'checkpoint accepted once');
  const saved = await page.evaluate(key => JSON.parse(sessionStorage.getItem(key)), pendingKey);
  assert(saved?.payload?.request_id);
  await open();
  assert.deepEqual(await page.evaluate(key => JSON.parse(sessionStorage.getItem(key)), pendingKey), saved);
  assert.equal(events().filter(e => e.kind === 'checkpoint_post').length, 1);
  controls({ pause_runner: true });
  await recovery().getByRole('button', { name: '只读取原恢复回执', exact: true }).click();
  await recovery().getByRole('button', { name: '打开新恢复批次', exact: true }).waitFor();
  const first = (await get(`/project-executions/${sourceId}/checkpoint-recoveries`))[0];
  assert.equal(first.request_payload.request_id, saved.payload.request_id);
  assert.equal(first.batch.tasks.length, 2);
  assert(!first.batch.tasks.some(t => t.execution_id === manifest.checkpoint.retained_execution_id));
  await page.screenshot({ path: path.join(outDir, 'checkpoint-recovered.png') });
  await recovery().getByRole('button', { name: '打开新恢复批次', exact: true }).click();
  await until(() => events().some(e => e.kind === 'cli_call' && first.batch.tasks.some(t => t.execution_id === e.execution_id)), 'recovered worker actually entered runner');
  await batch().getByLabel('我确认只请求停止本批绑定的实例，等待实际退出核实', { exact: true }).check();
  await batch().getByRole('button', { name: '确认停止本批执行', exact: true }).click();
  await until(async () => (await get(`/project-executions/${first.batch_id}`)).items.every(i => i.execution.state === 'cancelled'), 'new batch stopped');
  assert.equal((await get(`/executions/${manifest.checkpoint.retained_execution_id}`)).state, 'awaiting_review');
  controls({});
  await open(first.batch_id);
  await recovery().getByLabel('恢复核查说明', { exact: true }).fill('Stopped recovery checked; complete remaining work.');
  await recovery().getByLabel('我已核查原实例与外部影响，并授权重试节点产生新的 CLI、模型调用及费用', { exact: true }).check();
  const detailRoute = '**/api/workbench/checkpoint-recoveries/*';
  await page.route(detailRoute, route => route.fulfill({ status: 400, contentType: 'application/json', body: JSON.stringify({ error: 'Fixture detail failure after POST accepted' }) }));
  await submit().click();
  await recovery().getByRole('alert').filter({ hasText: 'Fixture detail failure after POST accepted' }).waitFor();
  const acceptedKey = `corppilot.checkpoint-recovery-pending.v1.${first.batch_id}`;
  const accepted = await page.evaluate(key => JSON.parse(sessionStorage.getItem(key)), acceptedKey);
  assert(accepted.recovery_id && accepted.batch_id && accepted.receipt);
  assert(!accepted.rejected);
  assert.equal(await recovery().getByRole('button', { name: '重新读取并授权未受理请求', exact: true }).count(), 0);
  assert(await recovery().getByRole('button', { name: '使用同一请求重试恢复', exact: true }).isDisabled());
  assert.equal(events().filter(e => e.kind === 'checkpoint_post').length, 2);
  await page.unroute(detailRoute);
  await recovery().getByRole('button', { name: '只读取原恢复回执', exact: true }).click();
  await recovery().getByRole('button', { name: '打开新恢复批次', exact: true }).waitFor();
  assert.equal(events().filter(e => e.kind === 'checkpoint_post').length, 2);
  assert.equal(await page.evaluate(key => sessionStorage.getItem(key), acceptedKey), null);
  report.postAcceptedThenGet400 = { passed: true, recovery_id: accepted.recovery_id, readsOnlyAfterFailure: true };
  const second = (await get(`/project-executions/${first.batch_id}/checkpoint-recoveries`))[0];
  await until(async () => (await get(`/project-executions/${second.batch_id}`)).items.some(i => i.execution.state === 'awaiting_review'), 'first recovered task finishes');
  await page.goto(`${baseURL}/#access_token=${manifest.access_token}`);
  await page.getByRole('button', { name: /F60 检查点项目/ }).click();
  async function approve(title) {
    const card = page.locator('article.task-card').filter({ has: page.getByRole('heading', { name: title, exact: true }) });
    await card.getByRole('button', { name: '执行记录与控制', exact: true }).click();
    const dialog = page.getByRole('dialog', { name: `任务执行 · ${title}`, exact: true });
    const item = dialog.locator('article.task-card').first();
    await item.getByText('成果与 Owner 评审', { exact: true }).click();
    await item.getByLabel('评审理由', { exact: true }).fill('Inspected fixture artifact and checkpoint binding.');
    await item.getByLabel('我已查验全部成果，确认符合本次需求与验收标准').check();
    await item.getByRole('button', { name: '确认提交评审', exact: true }).click();
    await item.getByLabel('Owner 评审决定').waitFor();
    await dialog.getByRole('button', { name: '关闭', exact: true }).click();
  }
  await approve('F53 审阅');
  await until(async () => (await get(`/project-executions/${second.batch_id}`)).items.every(i => i.execution.state === 'awaiting_review'), 'downstream finishes after new approval');
  await approve('F53 汇总');
  const final = await get(`/project-executions/${second.batch_id}`);
  assert(final.items.every(i => i.review?.decision === 'approved'));
  assert.equal(events().filter(e => e.kind === 'model_call').length, 0);
  assert.equal(events().filter(e => e.kind === 'checkpoint_post').length, 2);
  assert.equal(events().filter(e => e.kind === 'cli_call').length, 3);
  assert(events().filter(e => e.kind === 'cli_call').every(e => e.execution_id !== manifest.checkpoint.retained_execution_id && e.input_ids.length === 1));
  assert.equal(report.pageErrors.length, 0);
  report.recoveries = [first.id, second.id];
  report.boundaries = [];
  const mainPage = page;
  for (const variant of ['get400', 'wrong-batch', 'wrong-payload', 'bad-storage']) {
    const extra = await browser.newContext();
    await extra.addInitScript(({ key, value }) => { if (!sessionStorage.getItem('fixture-seeded')) { sessionStorage.setItem(key, value); sessionStorage.setItem('fixture-seeded', 'yes'); } }, { key: pendingKey, value: variant === 'bad-storage' ? '{invalid' : JSON.stringify(saved) });
    page = await extra.newPage(); page.setDefaultTimeout(15000);
    page.on('pageerror', error => report.pageErrors.push(String(error)));
    const writes = [];
    await page.route('**/api/workbench/**', route => { if (route.request().method() !== 'GET') { writes.push(route.request().url()); return route.abort(); } return route.continue(); });
    await page.route(`**/api/workbench/checkpoint-recoveries/${first.id}`, async route => {
      if (variant === 'get400') return route.fulfill({ status: 400, contentType: 'application/json', body: JSON.stringify({ error: 'Fixture detail failure after acceptance' }) });
      const value = await get(`/checkpoint-recoveries/${first.id}`);
      if (variant === 'wrong-batch') value.batch_id = second.batch_id;
      if (variant === 'wrong-payload') value.request_payload.confirm = false;
      return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(value) });
    });
    await open();
    await recovery().getByRole('alert').first().waitFor();
    const retained = await page.evaluate(key => sessionStorage.getItem(key), pendingKey);
    assert(retained, `${variant}: pending must remain`);
    assert.equal(await recovery().getByRole('button', { name: '打开新恢复批次', exact: true }).count(), 0);
    if (variant === 'get400') { const value = JSON.parse(retained); assert.equal(value.recovery_id, first.id); assert(!value.rejected); }
    if (variant === 'bad-storage') assert.equal(retained, '{invalid');
    assert.equal(writes.length, 0);
    report.boundaries.push({ variant, passed: true });
    await extra.close();
  }
  page = mainPage;
  assert.equal(report.pageErrors.length, 0);
  report.passed = true;
} catch (error) {
  report.error = String(error.stack || error); process.exitCode = 1;
  await page?.screenshot({ path: path.join(outDir, 'failure.png') }).catch(() => {});
} finally {
  await browser?.close();
  if (fs.existsSync(fixtureDir)) { controls({}); fs.writeFileSync(path.join(fixtureDir, 'shutdown'), 'stop'); await until(() => childExit || spawnError, 'shutdown', 20000).catch(() => { child.kill(); report.cleanupForced = true; }); }
  report.fixtureExit = childExit; logs.end();
  if (report.cleanupForced || childExit?.code !== 0) { report.passed = false; process.exitCode = 1; }
  fs.writeFileSync(path.join(outDir, 'browser-report.json'), JSON.stringify(report, null, 2));
  console.log(JSON.stringify({ passed: report.passed, report: path.join(outDir, 'browser-report.json'), error: report.error }));
}
