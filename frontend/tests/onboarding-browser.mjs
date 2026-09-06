import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { spawn } from 'node:child_process';
import { createHash } from 'node:crypto';
import { chromium } from 'playwright';

const repo = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..');
const root = process.env.CORPPILOT_BROWSER_OUTPUT || os.tmpdir(); fs.mkdirSync(root, { recursive: true });
const out = fs.mkdtempSync(path.join(root, 'corppilot-onboarding-')), fixture = path.join(out, 'fixture');
const python = process.env.CORPPILOT_PYTHON || path.join(repo, '.venv', process.platform === 'win32' ? 'Scripts/python.exe' : 'bin/python');
const child = spawn(python, [path.join(repo, 'tests/browser/goal_fixture.py'), '--data-dir', fixture, '--onboarding'], { cwd: repo, windowsHide: true });
const logs = fs.createWriteStream(path.join(out, 'fixture.log')); child.stdout.pipe(logs, { end: false }); child.stderr.pipe(logs, { end: false });
let childExit, spawnError, browser, page;
child.on('error', e => { spawnError = e; }); child.on('exit', (code, signal) => { childExit = { code, signal }; });
const report = { passed: false, output: out, runtime: 'Real browser and Owner HTTP; controlled loopback model and CLI fixture, no real CLI/Docker or paid model', pageErrors: [], errorResponses: [], uiWrites: [], boundaries: [] };
async function until(check, label, timeout = 30000) {
  const end = Date.now() + timeout;
  while (Date.now() < end) { const value = await check(); if (value) return value; await new Promise(r => setTimeout(r, 100)); }
  throw Error('Timed out: ' + label);
}
function events() { const p = path.join(fixture, 'evidence.jsonl'); return fs.existsSync(p) ? fs.readFileSync(p, 'utf8').trim().split('\n').filter(Boolean).map(JSON.parse) : []; }
try {
  await until(() => { if (spawnError || childExit) throw Error('Fixture exited'); return fs.existsSync(path.join(fixture, 'manifest.json')); }, 'startup');
  const manifest = JSON.parse(fs.readFileSync(path.join(fixture, 'manifest.json'))), baseURL = `http://127.0.0.1:${manifest.port}`;
  browser = await chromium.launch({ headless: true, ...(process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE ? { executablePath: process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE } : {}) });
  const context = await browser.newContext({ viewport: { width: 1440, height: 1000 } });
  page = await context.newPage(); page.setDefaultTimeout(15000);
  page.on('pageerror', e => report.pageErrors.push(String(e)));
  page.on('response', r => { if (r.status() >= 400) report.errorResponses.push({ url: r.url(), status: r.status() }); });
  page.on('request', r => { if (r.url().includes('/api/workbench/') && r.method() !== 'GET') report.uiWrites.push({ path: new URL(r.url()).pathname, method: r.method(), payload: r.postDataJSON() }); });
  // All mutations above are observed page requests; the API helper deliberately supports GET only.
  async function get(route) { const r = await context.request.get(baseURL + '/api/workbench' + route, { headers: { Authorization: `Bearer ${manifest.access_token}` } }); assert.equal(r.status(), 200); return r.json(); }
  await page.goto(`${baseURL}/#access_token=${manifest.access_token}`);
  const before = await get('/agents');
  const identities = [];
  for (const name of ['F86 协调人', 'F86 执行者']) {
    await page.getByRole('button', { name: '新建', exact: true }).click();
    const form = page.getByRole('dialog', { name: '创建 Agent', exact: true });
    await form.getByLabel('名字', { exact: true }).fill(name);
    assert.equal(await form.getByRole('checkbox', { name: '读取', exact: true }).isChecked(), true);
    for (const label of ['写入', '执行工具', '委派任务']) assert.equal(await form.getByRole('checkbox', { name: label, exact: true }).isChecked(), false);
    await form.getByRole('button', { name: '保存', exact: true }).click(); await form.waitFor({ state: 'detached' });
    const found = (await get('/agents')).filter(a => a.name === name); assert.equal(found.length, 1); assert.deepEqual(found[0].tools, ['read']); identities.push(found[0]);
  }
  assert.equal((await get('/agents')).length, before.length + 2);
  report.boundaries.push('two-real-UI-creations-default-read-only');
  for (const [index, person] of identities.entries()) {
    await page.locator('button.person').filter({ hasText: person.name }).click();
    await page.getByRole('button', { name: '编辑配置', exact: true }).click();
    const form = page.getByRole('dialog', { name: '编辑 Agent', exact: true });
    for (const label of index === 0 ? ['委派任务'] : ['写入', '执行工具']) await form.getByRole('checkbox', { name: label, exact: true }).check();
    await form.getByRole('button', { name: '保存', exact: true }).click(); await form.waitFor({ state: 'detached' });
    assert.deepEqual((await get('/agents/' + person.id)).tools, index === 0 ? ['read', 'delegate'] : ['read', 'write', 'execute']);
  }
  const creates = report.uiWrites.filter(r => r.path.endsWith('/agents') && r.method === 'POST');
  const edits = report.uiWrites.filter(r => r.method === 'PATCH');
  assert.equal(creates.length, 2); assert(creates.every(r => JSON.stringify(r.payload.tools) === '["read"]'));
  assert.equal(edits.length, 2); assert.equal(events().filter(e => ['model_call', 'cli_call'].includes(e.kind)).length, 0);
  report.boundaries.push('explicit-UI-grants-coordinator-only-delegate-worker-no-delegate');
  await page.locator('button.person').filter({ hasText: identities[0].name }).click();
  await page.getByPlaceholder('向会话发送消息…').fill('F86 PRIVATE Owner asks the new team to produce one deliverable.');
  await page.getByRole('button', { name: '发送', exact: true }).click();
  await page.getByRole('button', { name: '交给秘书组织并执行', exact: true }).click();
  const goal = page.getByRole('dialog', { name: '目标执行与恢复', exact: true });
  await goal.getByLabel('明确共享给秘书与项目的摘要').fill('F86 explicitly shared: create one evidence file.');
  await goal.getByLabel('目标协调人').selectOption(identities[0].id);
  for (const person of identities) await goal.getByRole('checkbox', { name: person.name, exact: true }).check();
  await goal.getByLabel('最多任务数').fill('1');
  await goal.getByRole('checkbox', { name: /我确认共享上述摘要/ }).check();
  await goal.getByRole('button', { name: '授权秘书组织并执行', exact: true }).click();
  const accepted = await until(() => events().find(e => e.kind === 'goal_accepted'), 'goal accepted');
  let receipt = await until(async () => { const r = await get('/goal-executions/' + accepted.goal_id); return r.batch?.items.length === 1 && r.batch.items[0].execution.state === 'awaiting_review' ? r : null; }, 'deliverable');
  assert.equal(receipt.planning.agent_id, identities[0].id); assert.equal(receipt.batch.items[0].execution.agent_id, identities[1].id);
  const execution = receipt.batch.items[0].execution.id;
  const artifacts = await get('/executions/' + execution + '/artifacts'); assert.equal(artifacts.length, 1);
  const download = await context.request.get(baseURL + '/api/workbench/artifacts/' + artifacts[0].id + '/download', { headers: { Authorization: `Bearer ${manifest.access_token}` } });
  assert.equal(download.status(), 200); const bytes = await download.body();
  assert.equal(createHash('sha256').update(bytes).digest('hex'), artifacts[0].sha256); assert(bytes.toString().includes(execution));
  await goal.getByRole('button', { name: '确认已读回并结束原请求核对', exact: true }).click();
  await goal.getByRole('button', { name: '打开此目标的项目群', exact: true }).click();
  await page.locator('article.task-card').filter({ has: page.getByRole('heading', { name: 'F86 交付', exact: true }) }).getByRole('button', { name: '执行记录与控制', exact: true }).click();
  const review = page.getByRole('dialog', { name: '任务执行 · F86 交付', exact: true });
  await review.getByText('成果与 Owner 评审', { exact: true }).click();
  await review.getByLabel('评审理由', { exact: true }).fill('Downloaded original artifact; execution identity and SHA-256 verified.');
  await review.getByLabel('我已查验全部成果，确认符合本次需求与验收标准').check();
  await review.getByRole('button', { name: '确认提交评审', exact: true }).click();
  await review.getByLabel('Owner 评审决定').waitFor();
  receipt = await get('/goal-executions/' + accepted.goal_id); assert.equal(receipt.batch.items[0].review.decision, 'approved');
  assert.equal(events().filter(e => e.kind === 'model_call').length, 1); assert.equal(events().filter(e => e.kind === 'cli_call').length, 1);
  assert(!JSON.stringify(events().filter(e => e.kind === 'model_call')).includes('F86 PRIVATE'));
  assert.equal(report.pageErrors.length, 0); assert.equal(report.errorResponses.length, 0);
  report.identities = identities.map(a => a.id); report.goalId = accepted.goal_id; report.artifact = { id: artifacts[0].id, sha256: artifacts[0].sha256 };
  report.boundaries.push('new-identities-private-goal-one-model-one-CLI-original-artifact-Owner-approved');
  await page.screenshot({ path: path.join(out, 'owner-approved.png') }); report.passed = true;
} catch (error) {
  report.error = String(error.stack || error); process.exitCode = 1;
  if (page) { report.failureText = await page.locator('body').innerText().catch(() => ''); await page.screenshot({ path: path.join(out, 'failure.png') }).catch(() => {}); }
} finally {
  await browser?.close();
  if (fs.existsSync(fixture)) { fs.writeFileSync(path.join(fixture, 'shutdown'), 'stop'); await until(() => childExit || spawnError, 'shutdown', 20000).catch(() => { child.kill(); report.cleanupForced = true; }); }
  report.fixtureExit = childExit; report.fixtureSpawnError = spawnError?.message; logs.end();
  if (report.cleanupForced || childExit?.code !== 0) { report.passed = false; process.exitCode = 1; }
  fs.writeFileSync(path.join(out, 'browser-report.json'), JSON.stringify(report, null, 2)); console.log(JSON.stringify({ passed: report.passed, report: path.join(out, 'browser-report.json'), error: report.error }));
}
