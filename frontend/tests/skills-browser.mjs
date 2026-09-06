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
const outDir = fs.mkdtempSync(path.join(outputRoot, 'corppilot-skills-'));
const fixtureDir = path.join(outDir, 'fixture');
const python = process.env.CORPPILOT_PYTHON || path.join(repo, '.venv', process.platform === 'win32' ? 'Scripts/python.exe' : 'bin/python');
const child = spawn(python, [path.join(repo, 'tests/browser/goal_fixture.py'), '--data-dir', fixtureDir, '--skills'], { cwd: repo, windowsHide: true });
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
      const request = route.request();
      if (request.method() !== 'GET') {
        writes.push({ method: request.method(), url: request.url() });
        if (!/\/agents(?:\/[^/]+)?$/.test(request.url())) return route.abort();
      } else reads.push(request.url());
      return route.continue();
    });
  }
  await observe(page);
  async function api(url) { const r = await context.request.get(baseURL + '/api/workbench' + url, { headers: { Authorization: `Bearer ${manifest.access_token}` } }); assert.equal(r.status(), 200); return r.json(); }
  async function openAgent(id) {
    await page.goto(`${uiURL}/#access_token=${manifest.access_token}`);
    await page.getByRole('button', { name: /F66 上下文项目/ }).click();
    await page.getByLabel('查看会话成员', { exact: true }).selectOption(id);
    await page.getByRole('button', { name: '刷新 Agent 活动', exact: true }).waitFor();
  }
  const dialog = () => page.getByRole('dialog', { name: '查看当次上下文', exact: true });
  const editor = () => page.getByRole('dialog', { name: '编辑 Agent', exact: true });
  async function openRun(run, model = true) {
    await openAgent(run.agent_id);
    if (model) await page.getByText(/^此身份的模型活动/).click();
    await page.locator('article').filter({ hasText: run.id }).first().getByRole('button', { name: '查看当次上下文', exact: true }).click();
    await dialog().getByText('查看当次技能输入', { exact: true }).click();
    await until(async () => !(await dialog().getByRole('button', { name: '刷新当次技能输入', exact: true }).isDisabled()), 'skill read');
  }
  async function refreshSkills() {
    await dialog().getByRole('button', { name: '刷新当次技能输入', exact: true }).click();
    await until(async () => !(await dialog().getByRole('button', { name: '刷新当次技能输入', exact: true }).isDisabled()), 'skill refresh');
  }
  const catalog = await api('/skills');
  const frozen = await api(`/runs/${sample.model.id}/skills`);
  assert.deepEqual(frozen.skills.map(s => s.id), ['coding']);
  assert.deepEqual((await api('/agents')).find(a => a.id === sample.model.agent_id).skills, ['demo-generator']);
  await openAgent(sample.model.agent_id);
  await page.getByRole('button', { name: '编辑配置', exact: true }).click();
  await editor().getByLabel('选择技能 coding', { exact: true }).waitFor();
  assert.equal(await editor().getByLabel('选择技能 demo-generator', { exact: true }).isChecked(), true);
  await editor().getByText('查看技能正文 · coding', { exact: true }).click();
  assert((await editor().innerText()).includes(catalog.find(s => s.id === 'coding').version));
  await editor().getByLabel('选择技能 coding', { exact: true }).check();
  await editor().getByRole('button', { name: '保存', exact: true }).click();
  await until(async () => await editor().count() === 0, 'saved editor');
  assert.deepEqual((await api('/agents')).find(a => a.id === sample.model.agent_id).skills, ['demo-generator', 'coding']);
  await openAgent(sample.model.agent_id); await page.getByRole('button', { name: '编辑配置', exact: true }).click();
  await editor().getByLabel('选择技能 coding', { exact: true }).waitFor();
  assert.equal(await editor().getByLabel('选择技能 coding', { exact: true }).isChecked(), true);
  await page.screenshot({ path: path.join(outDir, 'skills-editor.png') });
  await page.keyboard.press('Escape');
  const legacyAgent = sample.room.member_ids.find(id => id !== sample.model.agent_id && id !== sample.execution.agent_id);
  await openAgent(legacyAgent); await page.getByRole('button', { name: '编辑配置', exact: true }).click();
  await editor().getByRole('button', { name: '移除不可用技能 legacy-unknown', exact: true }).waitFor();
  await editor().getByLabel('名字', { exact: true }).fill('F78 保留旧技能身份');
  await editor().getByRole('button', { name: '保存', exact: true }).click();
  await until(async () => await editor().count() === 0, 'legacy rename');
  assert.deepEqual((await api('/agents')).find(a => a.id === legacyAgent).skills, ['legacy-unknown']);
  await page.getByRole('button', { name: '编辑配置', exact: true }).click();
  await editor().getByLabel('选择技能 coding', { exact: true }).check();
  assert.equal(await editor().getByRole('button', { name: '保存', exact: true }).isDisabled(), true);
  await editor().getByRole('button', { name: '移除不可用技能 legacy-unknown', exact: true }).click();
  await editor().getByRole('button', { name: '保存', exact: true }).click();
  await until(async () => await editor().count() === 0, 'explicit legacy removal');
  assert.deepEqual((await api('/agents')).find(a => a.id === legacyAgent).skills, ['coding']);
  report.boundaries.push('save-persist-reload', 'legacy-rename-and-explicit-removal');
  await page.route('**/api/workbench/skills', route => route.fulfill({ status: 503, contentType: 'application/json', body: JSON.stringify({ error: 'F78 catalog unavailable' }) }));
  await page.getByRole('button', { name: '编辑配置', exact: true }).click();
  await editor().getByRole('alert').waitFor();
  await editor().getByLabel('名字', { exact: true }).fill('F78 目录失败仍保留草稿');
  await page.unroute('**/api/workbench/skills');
  await editor().getByRole('button', { name: '重新读取技能目录', exact: true }).click();
  await editor().getByLabel('选择技能 coding', { exact: true }).waitFor();
  assert.equal(await editor().getByLabel('名字', { exact: true }).inputValue(), 'F78 目录失败仍保留草稿');
  assert.equal(await editor().getByLabel('选择技能 coding', { exact: true }).isChecked(), true);
  await page.keyboard.press('Escape'); report.boundaries.push('catalog-failure-preserves-draft');
  const historyStart = reads.length, writesBeforeHistory = writes.length;
  await openRun(sample.model);
  await dialog().getByText('查看固定技能正文 · coding', { exact: true }).click();
  assert((await dialog().innerText()).includes(frozen.skills[0].version));
  assert.equal(await dialog().getByText('查看固定技能正文 · demo-generator', { exact: true }).count(), 0);
  await page.screenshot({ path: path.join(outDir, 'skills-history.png') });
  const historyURL = `**/api/workbench/runs/${sample.model.id}/skills`;
  for (const variant of ['kind', 'run_id', 'agent_id', 'attempt', 'requirement_version', 'hash', 'bytes', 'source', '503']) {
    const bad = structuredClone(frozen);
    if (['kind', 'run_id', 'agent_id'].includes(variant)) bad[variant] = 'wrong';
    if (['attempt', 'requirement_version'].includes(variant)) bad[variant]++;
    if (variant === 'hash') bad.skills[0].version = '0'.repeat(64);
    if (variant === 'bytes') bad.skills[0].bytes++;
    if (variant === 'source') bad.skills[0].source = '../private';
    await page.route(historyURL, route => route.fulfill({ status: variant === '503' ? 503 : 200, contentType: 'application/json', body: JSON.stringify(variant === '503' ? { error: 'F78 unavailable' } : bad) }));
    await refreshSkills(); await dialog().getByRole('alert').waitFor();
    assert.equal(await dialog().getByText('查看固定技能正文 · coding', { exact: true }).count(), 0);
    await page.unroute(historyURL); await refreshSkills();
    await dialog().getByText('查看固定技能正文 · coding', { exact: true }).waitFor(); report.boundaries.push(variant);
  }
  const contextURL = `**/api/workbench/runs/${sample.model.id}/context-summary`;
  await page.route(contextURL, route => route.fulfill({ status: 503, contentType: 'application/json', body: JSON.stringify({ error: 'F78 context unavailable' }) }));
  await dialog().getByRole('button', { name: '刷新当次上下文', exact: true }).click();
  await dialog().getByText('F78 context unavailable', { exact: true }).waitFor();
  await refreshSkills(); await dialog().getByText('查看固定技能正文 · coding', { exact: true }).waitFor();
  await page.unroute(contextURL); report.boundaries.push('skills-independent-of-context-error');
  await page.route(historyURL, route => route.fulfill({ status: 401, contentType: 'application/json', body: JSON.stringify({ error: 'F78 expired' }) }));
  await dialog().getByRole('button', { name: '刷新当次技能输入', exact: true }).click();
  await page.getByLabel('访问口令', { exact: true }).waitFor(); await page.unroute(historyURL);
  await page.getByLabel('访问口令', { exact: true }).fill(manifest.access_token);
  await page.getByRole('button', { name: '验证并进入', exact: true }).click();
  await openRun(sample.model); await dialog().getByText('查看固定技能正文 · coding', { exact: true }).waitFor();
  report.boundaries.push('401-reauthorization');
  let release, held = false;
  await page.route(historyURL, async route => { held = true; await new Promise(resolve => { release = resolve; }); await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(frozen) }).catch(() => {}); });
  await dialog().getByRole('button', { name: '刷新当次技能输入', exact: true }).click();
  await until(() => held, 'held history read'); await page.keyboard.press('Escape');
  await page.getByLabel('查看会话成员', { exact: true }).selectOption(sample.execution.agent_id);
  await page.locator('article').filter({ hasText: sample.execution.id }).first().getByRole('button', { name: '查看当次上下文', exact: true }).click();
  await dialog().getByText('查看当次技能输入', { exact: true }).click();
  await dialog().getByText('查看固定技能正文 · demo-generator', { exact: true }).waitFor();
  release(); await page.unroute(historyURL); await refreshSkills();
  assert.equal(await dialog().getByText('查看固定技能正文 · coding', { exact: true }).count(), 0);
  report.boundaries.push('late-response-after-agent-switch');
  await page.keyboard.press('Escape'); await openRun(sample.execution, false);
  await dialog().getByText('查看固定技能正文 · demo-generator', { exact: true }).click();
  assert((await dialog().innerText()).includes((await api(`/executions/${sample.execution.id}/skills`)).skills[0].version));
  await page.keyboard.press('Escape'); await openRun(sample.legacy);
  await dialog().getByText('没有保存当次技能输入，无法确认当时内容。', { exact: true }).waitFor();
  assert.equal(await dialog().getByText(/^查看固定技能正文/).count(), 0);
  report.legacyText = await dialog().innerText();
  await page.keyboard.press('Escape'); await openRun(sample.empty);
  await dialog().getByText('当次已固定为空技能选择。', { exact: true }).waitFor();
  assert.equal(await dialog().getByText(/^查看固定技能正文/).count(), 0);
  report.emptyText = await dialog().innerText(); assert.notEqual(report.legacyText, report.emptyText);
  await page.keyboard.press('Escape'); await openRun(sample.model);
  await page.keyboard.press('Escape');
  await page.setViewportSize({ width: 390, height: 844 });
  await page.getByRole('button', { name: 'Agent 视角', exact: true }).click();
  await page.locator('article').filter({ hasText: sample.model.id }).first().getByRole('button', { name: '查看当次上下文', exact: true }).click();
  await dialog().getByText('查看当次技能输入', { exact: true }).click();
  await dialog().getByText('查看固定技能正文 · coding', { exact: true }).click();
  assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth));
  assert(await dialog().evaluate(el => el.scrollWidth <= el.clientWidth + 1));
  await page.screenshot({ path: path.join(outDir, 'skills-mobile.png') });
  assert(!reads.slice(historyStart).some(url => url.endsWith('/skills') && !/\/(runs|executions)\//.test(url)));
  assert.equal(writes.length, writesBeforeHistory);
  report.boundaries.push('fixed-history-after-binding-change', 'cli-history', 'null-versus-empty', 'mobile-wrap', 'history-no-catalog-or-writes');
  devServer = await createServer({ root: path.join(repo, 'frontend'), configFile: false, server: { host: '127.0.0.1', port: 0, proxy: { '/api': baseURL } } });
  await devServer.listen(); uiURL = devServer.resolvedUrls.local[0].replace(/\/$/, '');
  await page.setViewportSize({ width: 1280, height: 960 });
  await openRun(sample.model); await dialog().getByText('查看固定技能正文 · coding', { exact: true }).waitFor();
  await page.keyboard.press('Escape');
  await until(async () => await page.evaluate(() => document.activeElement?.textContent === '查看当次上下文'), 'StrictMode return focus', 2000);
  assert.equal(report.pageErrors.length, 0);
  const events = fs.existsSync(path.join(fixtureDir, 'evidence.jsonl')) ? fs.readFileSync(path.join(fixtureDir, 'evidence.jsonl'), 'utf8').trim().split('\n').filter(Boolean).map(JSON.parse) : [];
  assert.equal(events.filter(e => ['cli_call', 'model_call'].includes(e.kind)).length, 0);
  report.passed = true; report.externalCalls = 0; report.writes = writes;
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
