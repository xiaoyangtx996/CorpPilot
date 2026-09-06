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
const outDir = fs.mkdtempSync(path.join(outputRoot, 'corppilot-repository-'));
const fixtureDir = path.join(outDir, 'fixture');
const python = process.env.CORPPILOT_PYTHON || path.join(repo, '.venv', process.platform === 'win32' ? 'Scripts/python.exe' : 'bin/python');
const child = spawn(python, [path.join(repo, 'tests/browser/goal_fixture.py'), '--data-dir', fixtureDir, '--repository'], { cwd: repo, windowsHide: true });
const logs = fs.createWriteStream(path.join(outDir, 'fixture.log'));
child.stdout.pipe(logs, { end: false }); child.stderr.pipe(logs, { end: false });
let childExit, spawnError, browser, page, devServer;
child.on('error', error => { spawnError = error; }); child.on('exit', (code, signal) => { childExit = { code, signal }; });
const report = { passed: false, output: outDir, pageErrors: [], boundaries: [] };
async function until(check, description, timeout = 30000) {
  const deadline = Date.now() + timeout;
  while (Date.now() < deadline) { const value = await check(); if (value) return value; await new Promise(r => setTimeout(r, 100)); }
  throw Error(`Timed out: ${description}`);
}
function controls(value) { const target = path.join(fixtureDir, 'control.json'); fs.writeFileSync(target + '.tmp', JSON.stringify(value)); fs.renameSync(target + '.tmp', target); }
function events() { const file = path.join(fixtureDir, 'evidence.jsonl'); return fs.existsSync(file) ? fs.readFileSync(file, 'utf8').trim().split('\n').filter(Boolean).map(line => JSON.parse(line)) : []; }
try {
  await until(() => { if (spawnError || childExit) throw Error('Fixture exited'); return fs.existsSync(path.join(fixtureDir, 'manifest.json')); }, 'fixture startup');
  const manifest = JSON.parse(fs.readFileSync(path.join(fixtureDir, 'manifest.json'))), sample = manifest.repository;
  const baseURL = `http://127.0.0.1:${manifest.port}`;
  browser = await chromium.launch({ headless: true });
  const context = await browser.newContext({ viewport: { width: 1280, height: 960 } });
  page = await context.newPage(); page.setDefaultTimeout(15000);
  page.on('pageerror', error => report.pageErrors.push(String(error)));
  const writes = [];
  await page.route('**/api/workbench/**', route => {
    if (route.request().method() !== 'GET') {
      writes.push(route.request().url());
      if (!route.request().url().endsWith('/repository')) return route.abort();
    }
    return route.continue();
  });
  const key = id => `corppilot.repository-pending.v1.${id}`;
  const base = id => `/conversations/${id}/repository`;
  const dialog = () => page.getByRole('dialog', { name: '项目代码仓库', exact: true });
  const confirm = () => dialog().getByLabel('我确认新执行使用此固定提交，已有执行绑定不变', { exact: true });
  const save = () => dialog().getByRole('button', { name: '确认保存仓库绑定', exact: true });
  const pending = id => page.evaluate(k => sessionStorage.getItem(k), key(id));
  const posts = () => events().filter(e => e.kind === 'repository_post');
  const accepted = () => events().filter(e => e.kind === 'repository_accepted');
  async function api(pathname, data, method = data ? 'POST' : 'GET', expected = data ? 201 : 200) {
    const response = await context.request.fetch(baseURL + '/api/workbench' + pathname, { method, headers: { Authorization: `Bearer ${manifest.access_token}` }, ...(data ? { data } : {}) });
    assert.equal(response.status(), expected, pathname); return response.json();
  }
  async function open(room = sample.other_room, url = baseURL) {
    await page.goto(`${url.replace(/\/$/, '')}/#access_token=${manifest.access_token}`);
    await page.getByRole('button', { name: new RegExp(room.title) }).click();
    await page.getByRole('button', { name: '项目代码仓库', exact: true }).click();
  }
  async function refresh() {
    const button = dialog().getByRole('button', { name: /^(只读核对原仓库请求|刷新项目仓库)$/ });
    await button.click(); await until(async () => !(await button.isDisabled()), 'repository read complete');
  }
  async function fill(actor = manifest.agent_ids[1]) {
    await dialog().getByLabel('本机仓库路径', { exact: true }).fill(sample.source_path.replaceAll('\\', '/'));
    await dialog().getByLabel('完整提交 SHA', { exact: true }).fill(sample.commit);
    await dialog().getByLabel('代码集成人', { exact: true }).selectOption(actor);
  }
  const frozenBefore = await api(`/executions/${sample.bound_run.id}/repository`);
  await open(); await dialog().getByLabel('本机仓库路径', { exact: true }).waitFor();
  assert(await save().isDisabled());
  await fill(); await dialog().getByLabel('完整提交 SHA', { exact: true }).fill('HEAD'); await confirm().check(); await save().click();
  await dialog().getByRole('alert').first().waitFor(); assert.equal(posts().length, 0);
  await fill(); assert(!(await confirm().isChecked()));
  controls({ drop_repository: true, repository_get503: true });
  await confirm().check(); await save().click(); await until(() => accepted().length === 1, 'real Git binding accepted then response dropped');
  await until(async () => !(await dialog().getByRole('button', { name: '只读核对原仓库请求', exact: true }).isDisabled()), 'dropped POST handled');
  const original = await pending(sample.other_room.id); assert(original);
  assert.equal(JSON.parse(original).payload.expected_revision, 0);
  controls({});
  const v1 = await api(base(sample.other_room.id));
  const v2 = await api(base(sample.other_room.id), { ...JSON.parse(original).payload, request_id: 'external-v2', expected_revision: 1, integration_agent_id: manifest.agent_ids[2] });
  controls({ repository_get503: true }); await open(); await dialog().getByRole('alert').first().waitFor();
  assert.equal(await pending(sample.other_room.id), original);
  controls({}); await refresh(); await until(async () => (await pending(sample.other_room.id)) === null, 'original v1 receipt confirmed independently of v2');
  assert((await dialog().innerText()).includes(v1.request_id)); assert((await dialog().innerText()).includes(v2.request_id));
  assert.equal(posts().length, 2);
  await page.screenshot({ path: path.join(outDir, 'repository-original-and-latest.png') });
  // An explicitly rejected CAS can only be released after original-key absence and a fresh latest read.
  await fill();
  await api(base(sample.other_room.id), { ...JSON.parse(original).payload, request_id: 'external-v3', expected_revision: 2 });
  await confirm().check(); await save().click(); await dialog().getByRole('alert').first().waitFor();
  await until(async () => JSON.parse(await pending(sample.other_room.id))?.rejected === true, 'CAS rejection frozen');
  await refresh(); await dialog().getByRole('button', { name: '结束已拒绝请求并重新核查', exact: true }).click();
  assert.equal(await pending(sample.other_room.id), null);
  // A request lost before reaching the server can be retried with exactly the saved payload.
  let intercepted;
  await page.route('**' + base(sample.other_room.id), route => {
    if (route.request().method() === 'POST') { intercepted = route.request().postDataJSON(); return route.abort(); }
    return route.continue();
  });
  await fill(); await confirm().check(); await save().click();
  await until(() => intercepted, 'request intercepted before server');
  await page.unroute('**' + base(sample.other_room.id)); await refresh();
  const replay = JSON.parse(await pending(sample.other_room.id)); assert.deepEqual(replay.payload, intercepted);
  await dialog().getByRole('button', { name: '同键重试原仓库请求', exact: true }).click();
  await until(async () => (await pending(sample.other_room.id)) === null, 'same-key retry confirmed');
  assert.deepEqual(posts().at(-1).payload, intercepted);
  assert.equal((await api(base(sample.other_room.id))).revision, 4);
  // Actual detach changes only future configuration, preserving the original bound run.
  await dialog().getByLabel('停用后续执行的仓库绑定', { exact: true }).check();
  await confirm().check(); await save().click(); await until(async () => (await pending(sample.other_room.id)) === null, 'detach confirmed');
  assert.equal((await api(base(sample.other_room.id))).snapshot, null);
  const detached = posts().at(-1).payload; assert.equal(detached.source_path, null); assert.equal(detached.commit, null); assert.equal(detached.integration_agent_id, null);
  assert.deepEqual(await api(`/executions/${sample.bound_run.id}/repository`), frozenBefore);
  for (const variant of ['disabled-integrator', 'removed-integrator', 'archived-project']) {
    await dialog().getByLabel('停用后续执行的仓库绑定', { exact: true }).uncheck(); await fill(manifest.agent_ids[2]);
    const target = variant === 'disabled-integrator' ? `/agents/${manifest.agent_ids[2]}` : variant === 'removed-integrator' ? `/conversations/${sample.other_room.id}/members/${manifest.agent_ids[2]}` : `/conversations/${sample.other_room.id}`;
    const field = variant === 'disabled-integrator' ? 'enabled' : variant === 'removed-integrator' ? 'joined' : 'archived';
    await api(target, { [field]: variant === 'archived-project' }, 'PATCH', 200);
    await confirm().check(); await save().click(); await dialog().getByRole('alert').first().waitFor();
    await until(async () => JSON.parse(await pending(sample.other_room.id))?.rejected === true, variant + ' explicitly rejected');
    await api(target, { [field]: variant !== 'archived-project' }, 'PATCH', 200);
    await refresh(); await dialog().getByRole('button', { name: '结束已拒绝请求并重新核查', exact: true }).click();
    assert.equal(await pending(sample.other_room.id), null); assert.equal((await api(base(sample.other_room.id))).revision, 5);
    report.boundaries.push({ variant, passed: true });
  }
  report.writeFlows = { droppedThenExactRead: true, latestSeparate: true, staleCAS: true, sameKeyRetry: true, detach: true };
  await api(base(sample.room.id), { ...JSON.parse(original).payload, request_id: 'bound-project-new', expected_revision: 1, integration_agent_id: manifest.agent_ids[2] });
  assert.deepEqual(await api(`/executions/${sample.bound_run.id}/repository`), frozenBefore);
  await dialog().getByRole('button', { name: '关闭项目代码仓库', exact: true }).click();
  // Actual immutable run binding is observed, never reconstructed by writing.
  await page.getByRole('button', { name: new RegExp(sample.room.title) }).click();
  await page.getByLabel('查看会话成员', { exact: true }).selectOption(sample.bound_run.agent_id);
  const view = () => page.getByRole('dialog', { name: '查看当次代码仓库', exact: true });
  async function openRun(run) { await page.locator('article').filter({ hasText: run.id }).first().getByRole('button', { name: '查看当次代码仓库', exact: true }).click(); }
  const observedWrites = writes.length;
  await openRun(sample.bound_run); await view().getByText('查看固定提交与树', { exact: true }).click();
  await view().getByText('提交 ' + sample.commit, { exact: true }).waitFor();
  assert((await view().innerText()).includes('绑定版本 1')); assert(!(await view().innerText()).includes('bound-project-new'));
  await page.screenshot({ path: path.join(outDir, 'repository-fixed-run.png') });
  await view().getByRole('button', { name: '关闭当次代码仓库', exact: true }).click();
  await openRun(sample.old_run); await view().getByText(/未冻结代码仓库绑定/).waitFor();
  await view().getByRole('button', { name: '关闭当次代码仓库', exact: true }).click();
  assert.equal(writes.length, observedWrites);
  const mainPage = page;
  for (const variant of ['bad-storage', 'get401', 'wrong-original-request', 'wrong-original-source', 'wrong-original-integrator', 'changed-post-receipt']) {
    const isolated = await browser.newContext();
    const saved = variant === 'bad-storage' ? '{invalid' : JSON.stringify({ ...JSON.parse(original), ...(variant === 'changed-post-receipt' ? { receipt: v1 } : {}) });
    await isolated.addInitScript(({ key, saved }) => sessionStorage.setItem(key, saved), { key: key(sample.other_room.id), saved });
    page = await isolated.newPage(); page.setDefaultTimeout(15000); page.on('pageerror', error => report.pageErrors.push(String(error)));
    const extraWrites = [];
    await page.route('**/api/workbench/**', route => { if (route.request().method() !== 'GET') { extraWrites.push(route.request().url()); return route.abort(); } return route.continue(); });
    const exactURL = '**' + base(sample.other_room.id) + '/requests/' + v1.request_id;
    if (variant !== 'bad-storage') await page.route(exactURL, route => {
      if (variant === 'get401') return route.fulfill({ status: 401, contentType: 'application/json', body: JSON.stringify({ error: 'Fixture expired' }) });
      const changed = structuredClone(v1);
      if (variant === 'wrong-original-request') changed.request_id = 'another-request';
      if (variant === 'wrong-original-source') changed.snapshot.source_path += '\\another';
      if (variant === 'wrong-original-integrator') changed.snapshot.integration_agent_id = manifest.agent_ids[2];
      if (variant === 'changed-post-receipt') changed.snapshot.tree = 'a'.repeat(40);
      return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(changed) });
    });
    await open(); await page.getByRole('alert').first().waitFor();
    assert.equal(await pending(sample.other_room.id), saved); assert.equal(extraWrites.length, 0);
    if (variant === 'get401') {
      await page.unroute(exactURL); await page.getByLabel('访问口令', { exact: true }).fill(manifest.access_token);
      await page.getByRole('button', { name: '验证并进入', exact: true }).click();
      await page.getByRole('button', { name: new RegExp(sample.other_room.title) }).click();
      await page.getByRole('button', { name: '项目代码仓库', exact: true }).click();
      await until(async () => (await pending(sample.other_room.id)) === null, 'reauthorized exact original readback');
      assert.equal(extraWrites.length, 0);
    }
    report.boundaries.push({ variant, passed: true }); await isolated.close();
  }
  page = mainPage;
  const runURL = '**/executions/' + sample.bound_run.id + '/repository';
  for (const [variant, change] of [
    ['wrong-run', r => { r.execution_id = sample.old_run.id; }], ['wrong-agent', r => { r.agent_id = manifest.agent_ids[2]; }],
    ['wrong-attempt', r => { r.attempt += 1; }], ['wrong-requirement', r => { r.requirement_version += 1; }],
    ['extra-run-field', r => { r.raw = 'private'; }], ['detached-execution', r => { r.repository.snapshot = null; }],
  ]) {
    const bad = structuredClone(frozenBefore); change(bad);
    await page.route(runURL, route => route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(bad) }));
    await openRun(sample.bound_run); await view().getByRole('alert').waitFor();
    assert.equal(await view().getByRole('region', { name: '当次冻结仓库', exact: true }).count(), 0);
    await view().getByRole('button', { name: '关闭当次代码仓库', exact: true }).click();
    await page.unroute(runURL); report.boundaries.push({ variant, passed: true });
  }
  // Strict validation of repository receipts must clear stale views and never write.
  const currentURL = '**' + base(sample.room.id);
  for (const [variant, change] of [
    ['wrong-project', r => { r.conversation_id = sample.other_room.id; }], ['extra-field', r => { r.raw = 'private'; }],
    ['bad-revision', r => { r.revision = true; }], ['bad-sha', r => { r.snapshot.commit = 'HEAD'; }],
    ['wrong-tree-length', r => { r.snapshot.tree = 'a'.repeat(64); }], ['negative-files', r => { r.snapshot.files = -1; }],
    ['oversize', r => { r.snapshot.total_bytes = 256 * 1024 * 1024 + 1; }], ['extra-snapshot', r => { r.snapshot.raw = 'private'; }],
    ['bad-time', r => { r.created_at = 'not-time'; }],
  ]) {
    const bad = structuredClone(sample.initial_binding); change(bad);
    await page.route(currentURL, route => route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(bad) }));
    await open(sample.room); await dialog().getByRole('alert').first().waitFor(); assert(await save().isDisabled());
    await page.unroute(currentURL); report.boundaries.push({ variant, passed: true });
  }
  await open(sample.room); await dialog().getByLabel('本机仓库路径', { exact: true }).waitFor();
  let held;
  await page.route(currentURL, route => { held = route; });
  await dialog().getByRole('button', { name: '刷新项目仓库', exact: true }).click();
  await until(() => held, 'old project request held');
  await page.keyboard.press('Escape');
  await page.getByRole('button', { name: new RegExp(sample.other_room.title) }).click();
  await page.getByRole('button', { name: '项目代码仓库', exact: true }).click();
  await dialog().getByText('此版本已停用后续仓库绑定。', { exact: true }).waitFor();
  await held.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(sample.initial_binding) });
  await page.unroute(currentURL); await refresh();
  assert(!(await dialog().innerText()).includes(sample.initial_binding.request_id));
  report.boundaries.push({ variant: 'late-after-project-switch', passed: true });
  await open(sample.room); await dialog().getByLabel('本机仓库路径', { exact: true }).waitFor();
  await page.setViewportSize({ width: 390, height: 844 });
  await page.screenshot({ path: path.join(outDir, 'repository-mobile.png') });
  assert(await dialog().evaluate(n => n.scrollWidth <= n.clientWidth + 1));
  assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1));
  report.mobile = { passed: true, width: 390 };
  await dialog().getByRole('button', { name: '关闭项目代码仓库', exact: true }).click();
  await page.setViewportSize({ width: 1280, height: 960 });
  devServer = await createServer({ root: path.join(repo, 'frontend'), configFile: false, server: { host: '127.0.0.1', port: 0, proxy: { '/api': baseURL } } });
  await devServer.listen(); await open(sample.room, devServer.resolvedUrls.local[0]);
  await dialog().getByLabel('本机仓库路径', { exact: true }).waitFor(); await page.keyboard.press('Escape');
  assert.equal(await dialog().count(), 0);
  await until(async () => await page.evaluate(() => document.activeElement?.textContent === '项目代码仓库'), 'StrictMode Escape restores focus');
  report.devStrictMode = { passed: true, escapeRestoresFocus: true };
  assert.equal(events().filter(e => ['model_call', 'cli_call'].includes(e.kind)).length, 0);
  assert.equal(fs.existsSync(path.join(fixtureDir, 'state', 'execution-workspaces')), false);
  assert.equal(report.pageErrors.length, 0); report.externalCalls = 0; report.passed = true;
} catch (error) {
  report.error = String(error.stack || error); process.exitCode = 1;
  await page?.screenshot({ path: path.join(outDir, 'failure.png') }).catch(() => {});
} finally {
  await devServer?.close(); await browser?.close();
  if (fs.existsSync(fixtureDir)) { controls({}); fs.writeFileSync(path.join(fixtureDir, 'shutdown'), 'stop'); await until(() => childExit || spawnError, 'shutdown', 20000).catch(() => { child.kill(); report.cleanupForced = true; }); }
  report.fixtureExit = childExit; logs.end();
  if (report.cleanupForced || childExit?.code !== 0) { report.passed = false; process.exitCode = 1; }
  fs.writeFileSync(path.join(outDir, 'browser-report.json'), JSON.stringify(report, null, 2));
  console.log(JSON.stringify({ passed: report.passed, report: path.join(outDir, 'browser-report.json'), error: report.error }));
}
