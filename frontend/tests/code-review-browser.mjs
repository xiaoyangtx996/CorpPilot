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
const outDir = fs.mkdtempSync(path.join(outputRoot, 'corppilot-code-review-'));
const fixtureDir = path.join(outDir, 'fixture');
const python = process.env.CORPPILOT_PYTHON || path.join(repo, '.venv', process.platform === 'win32' ? 'Scripts/python.exe' : 'bin/python');
const child = spawn(python, [path.join(repo, 'tests/browser/goal_fixture.py'), '--data-dir', fixtureDir, '--code-review'], { cwd: repo, windowsHide: true });
const logs = fs.createWriteStream(path.join(outDir, 'fixture.log'));
child.stdout.pipe(logs, { end: false }); child.stderr.pipe(logs, { end: false });
let childExit, spawnError, browser, page, devServer;
child.on('error', error => { spawnError = error; }); child.on('exit', (code, signal) => { childExit = { code, signal }; });
const report = { passed: false, output: outDir, browserPath: 'Browser plugin not available; project Playwright', pageErrors: [], boundaries: [] };
async function until(check, description, timeout = 30000) {
  const deadline = Date.now() + timeout;
  while (Date.now() < deadline) { const value = await check(); if (value) return value; await new Promise(r => setTimeout(r, 100)); }
  throw Error(`Timed out: ${description}`);
}
function controls(value) { fs.writeFileSync(path.join(fixtureDir, 'control.json'), JSON.stringify(value)); }
function events() { const file = path.join(fixtureDir, 'evidence.jsonl'); return fs.existsSync(file) ? fs.readFileSync(file, 'utf8').trim().split('\n').filter(Boolean).map(line => JSON.parse(line)) : []; }
try {
  await until(() => { if (spawnError || childExit) throw Error('Fixture exited'); return fs.existsSync(path.join(fixtureDir, 'manifest.json')); }, 'native Git fixture startup', 60000);
  const manifest = JSON.parse(fs.readFileSync(path.join(fixtureDir, 'manifest.json'))), sample = manifest.code_review;
  const baseURL = `http://127.0.0.1:${manifest.port}`;
  browser = await chromium.launch({ headless: true });
  const context = await browser.newContext({ viewport: { width: 1280, height: 960 } });
  page = await context.newPage(); page.setDefaultTimeout(15000); page.on('pageerror', error => report.pageErrors.push(String(error)));
  const key = id => `corppilot.code-review-pending.v1.${id}`;
  const source = name => sample.samples[name].run.id;
  const endpoint = name => `/executions/${source(name)}/code-review`;
  const dialog = () => page.getByRole('dialog', { name: '交指定集成人评审', exact: true });
  const submit = () => dialog().getByRole('button', { name: '确认交集成人评审', exact: true });
  const confirm = () => dialog().getByLabel('我确认交给原指定集成人执行代码评审，可能产生费用；不自动批准或合入', { exact: true });
  const pending = name => page.evaluate(k => sessionStorage.getItem(k), key(source(name)));
  async function api(url, data, method = data ? 'POST' : 'GET', expected = data ? 202 : 200) {
    const response = await context.request.fetch(baseURL + '/api/workbench' + url, { method, headers: { Authorization: `Bearer ${manifest.access_token}` }, ...(data ? { data } : {}) });
    assert.equal(response.status(), expected, url); return response.json();
  }
  async function enter(name) {
    await page.getByRole('button', { name: new RegExp(sample.room.title) }).click();
    const card = page.locator('article.task-card').filter({ has: page.getByRole('heading', { name: sample.samples[name].task.title, exact: true }) });
    await card.getByRole('button', { name: '执行记录与控制', exact: true }).click();
    const runDialog = page.getByRole('dialog', { name: '任务执行 · '+sample.samples[name].task.title, exact: true });
    await runDialog.getByText('成果与 Owner 评审', { exact: true }).click();
    await runDialog.getByRole('button', { name: '交指定集成人评审', exact: true }).click();
  }
  async function open(name = 'primary', url = baseURL) {
    await page.goto(`${url.replace(/\/$/, '')}/#access_token=${manifest.access_token}`);
    await enter(name);
  }
  await open('unapproved'); await dialog().getByText(/须先由 Owner 批准/).waitFor(); assert(await submit().isDisabled());
  assert(page.url().startsWith(baseURL));assert.match(await page.title(),/CorpPilot/);
  assert.equal(events().filter(e => e.kind === 'code_review_post').length, 0);
  report.boundaries.push({ variant: 'unapproved-is-blocked-preview', passed: true });
  const goodPreview = await api(endpoint('primary')+'-preview');
  const previewURL = '**'+endpoint('primary')+'-preview';
  for (const [variant, mutate] of [
    ['wrong-source', v => { v.snapshot.source_execution.id = source('other'); }],
    ['zero-attempt', v => { v.snapshot.source_execution.attempt = 0; }],
    ['wrong-project', v => { v.snapshot.source_task.conversation_id = manifest.source_conversation_id; }],
    ['wrong-repository', v => { v.snapshot.repository.conversation_id = manifest.source_conversation_id; }],
    ['wrong-integrator', v => { v.snapshot.integrator.id = manifest.agent_ids[2]; }],
    ['wrong-manifest', v => { v.snapshot.manifest.execution_id = source('other'); }],
    ['missing-approved-artifact', v => { v.snapshot.owner_review.artifact_ids.pop(); }],
    ['invalid-fingerprint', v => { v.fingerprint = 'bad'; }],
  ]) {
    const bad = structuredClone(goodPreview); mutate(bad);
    await page.route(previewURL, route => route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(bad) }));
    await open(); await dialog().getByRole('alert').first().waitFor(); assert(await submit().isDisabled());
    await page.unroute(previewURL); report.boundaries.push({ variant, passed: true });
  }
  // Declared failure can release only after the source-specific receipt is null.
  await open(); await confirm().check();
  await api(`/agents/${manifest.agent_ids[1]}`, { enabled: false }, 'PATCH', 200);
  await submit().click(); await until(async () => JSON.parse(await pending('primary'))?.rejected === true, 'explicit rejection retained');
  await api(`/agents/${manifest.agent_ids[1]}`, { enabled: true }, 'PATCH', 200);
  await dialog().getByRole('button', { name: '只读核对原代码评审请求', exact: true }).click();
  await dialog().getByRole('button', { name: '结束已拒绝请求并重新预览', exact: true }).click();
  assert.equal(await pending('primary'), null);
  await api(`/conversations/${sample.room.id}/repository`, {request_id:'later-detach',expected_revision:1,source_path:null,commit:null,integration_agent_id:null,confirm:true}, 'POST', 201);
  // Original request survives a dropped response and an unavailable readback.
  await confirm().check(); controls({ drop_code_review: true, code_review_get503: true, pause_runner: true });
  await submit().click(); await until(() => events().some(e => e.kind === 'code_review_accepted'), 'accepted code review');
  const original = await pending('primary'); assert(original);
  const receipt = events().find(e => e.kind === 'code_review_accepted').receipt;
  await open(); await dialog().getByRole('alert').first().waitFor(); assert.equal(await pending('primary'), original);
  controls({ pause_runner: true });
  await dialog().getByRole('button', { name: '只读核对原代码评审请求', exact: true }).click();
  await until(async () => (await pending('primary')) === null, 'exact original receipt');
  assert.equal(events().filter(e => e.kind === 'code_review_accepted').length, 1);
  const originalSource = await api(`/executions/${source('primary')}`);
  controls({});
  await until(async () => (await api(`/executions/${receipt.initial_execution_id}`)).state === 'awaiting_review', 'reviewer report', 60000);
  const input = events().find(e => e.kind === 'code_review_inputs' && e.execution_id === receipt.initial_execution_id);
  assert.deepEqual(input.artifacts.sort((a,b)=>a.id.localeCompare(b.id)), receipt.source_snapshot.artifacts.sort((a,b)=>a.id.localeCompare(b.id)));
  assert.deepEqual(input.repository, sample.repository.snapshot); assert.equal(input.base_text, 'F70 fixed source\n');
  assert.equal(await api(`/executions/${receipt.initial_execution_id}/review`), null);
  const result = await api(`/executions/${receipt.initial_execution_id}/artifacts`); assert(result.some(a => a.path === 'review.md'));
  await dialog().getByRole('button', { name: '打开评审任务', exact: true }).click();
  const taskDialog = () => page.getByRole('dialog', { name: '任务执行 · 固定代码成果评审', exact: true });
  await taskDialog().getByText('成果与 Owner 评审', { exact: true }).click();
  await taskDialog().getByRole('button', { name: 'review.md', exact: true }).waitFor();
  assert.equal(await taskDialog().getByRole('button', { name: '交指定集成人评审', exact: true }).count(), 0);
  await page.screenshot({ path: path.join(outDir, 'review-report.png') });
  await taskDialog().getByRole('button', { name: '关闭', exact: true }).click();
  await page.screenshot({ path: path.join(outDir, 'review-receipt.png') });
  assert.deepEqual(await api(`/executions/${source('primary')}`), originalSource);
  report.delivery = { passed: true, initialExecution: receipt.initial_execution_id, controlledCLI: true };

  // Invalid history and pending storage must never unlock a new authorization.
  const mainPage = page;
  for (const variant of ['bad-storage','get401','wrong-request','wrong-snapshot','wrong-task','wrong-initial-run','wrong-initial-agent','wrong-initial-attempt']) {
    const isolated = await browser.newContext();
    const raw = variant === 'bad-storage' ? '{invalid' : original;
    await isolated.addInitScript(({k,raw}) => sessionStorage.setItem(k,raw), { k:key(source('primary')),raw });
    page = await isolated.newPage(); page.setDefaultTimeout(15000); page.on('pageerror', e => report.pageErrors.push(String(e)));
    let writes = 0;
    await page.route('**/api/workbench/**', route => { if(route.request().method()!=='GET'){writes++;return route.abort();}return route.continue(); });
    const exactURL = '**'+endpoint('primary');
    const initialURL = '**/executions/'+receipt.initial_execution_id;
    if (variant !== 'bad-storage' && !variant.startsWith('wrong-initial-a')) await page.route(exactURL, route => {
      if(variant==='get401') return route.fulfill({status:401,contentType:'application/json',body:JSON.stringify({error:'Expired'})});
      const bad=structuredClone(receipt);
      if(variant==='wrong-request')bad.request_id='another';
      if(variant==='wrong-snapshot')bad.source_snapshot.manifest.target_tree='a'.repeat(40);
      if(variant==='wrong-task')bad.task_id=sample.samples.other.task.id;
      if(variant==='wrong-initial-run')bad.initial_execution_id=source('other');
      return route.fulfill({status:200,contentType:'application/json',body:JSON.stringify(bad)});
    });
    if(variant.startsWith('wrong-initial-a')) {
      const bad=await api(`/executions/${receipt.initial_execution_id}`);
      if(variant==='wrong-initial-agent')bad.agent_id=manifest.agent_ids[2];else bad.attempt=2;
      await page.route(initialURL,route=>route.fulfill({status:200,contentType:'application/json',body:JSON.stringify(bad)}));
    }
    await open(); await page.getByRole('alert').first().waitFor(); assert.equal(await pending('primary'),raw); assert.equal(writes,0);
    if(variant==='get401') {
      await page.unroute(exactURL); await page.getByLabel('访问口令',{exact:true}).fill(manifest.access_token);
      await page.getByRole('button',{name:'验证并进入',exact:true}).click(); await enter('primary');
      await until(async()=>(await pending('primary'))===null,'reauthorized code review'); assert.equal(writes,0);
    }
    report.boundaries.push({variant,passed:true}); await isolated.close();
  }
  page=mainPage;
  // An unreceived request may be explicitly retried with exactly the same key.
  await open('retry');await confirm().check();
  const retryURL='**'+endpoint('retry');
  await page.route(retryURL,route=>route.request().method()==='POST'?route.abort():route.continue());
  await submit().click();await dialog().getByRole('alert').first().waitFor();
  const retryPending=JSON.parse(await pending('retry'));assert(retryPending&&!retryPending.rejected);
  await page.unroute(retryURL);
  await dialog().getByRole('button',{name:'同键重试代码评审请求',exact:true}).click();
  await until(async()=>(await pending('retry'))===null,'same-key retry readback');
  assert.equal((await api(endpoint('retry'))).request_id,retryPending.payload.request_id);
  report.boundaries.push({variant:'unreceived-same-key-retry',passed:true});
  // A different tab winning the unique source authorization needs explicit acceptance.
  await open('other');await confirm().check();
  const otherURL='**'+endpoint('other');
  await page.route(otherURL,route=>route.request().method()==='POST'?route.abort():route.continue());
  await submit().click();await dialog().getByRole('alert').first().waitFor();
  const abandoned=await pending('other');const value=JSON.parse(abandoned);
  await page.unroute(otherURL);
  const foreign=await api(endpoint('other'),{...value.payload,request_id:'another-tab'});
  await dialog().getByRole('button',{name:'只读核对原代码评审请求',exact:true}).click();
  await dialog().getByText('其他请求已创建评审任务',{exact:true}).waitFor();
  assert.equal(await pending('other'),abandoned);
  await dialog().getByRole('button',{name:'接受已有评审回执并结束本地请求',exact:true}).click();
  await until(async()=>(await pending('other'))===null,'explicit foreign receipt acceptance');
  assert.equal((await api(endpoint('other'))).id,foreign.id);
  report.boundaries.push({variant:'competing-tab-explicit-recovery',passed:true});
  // Existing controls stop only the reviewer; explicit retry creates its second run.
  await open('stop'); await confirm().check(); controls({pause_runner:true}); await submit().click();
  const stopped=await until(async()=>await api(endpoint('stop')),'stop receipt');
  await until(async()=>(await api(`/executions/${stopped.initial_execution_id}`)).state==='running','reviewer running');
  await dialog().getByRole('button',{name:'打开评审任务',exact:true}).click();
  await taskDialog().getByRole('button',{name:'请求停止执行',exact:true}).click();
  await until(async()=>(await api(`/executions/${stopped.initial_execution_id}`)).state==='cancelled','reviewer stopped');
  controls({}); await taskDialog().getByRole('button',{name:'刷新执行状态',exact:true}).click();
  await taskDialog().getByLabel('前次结果与副作用核查说明',{exact:true}).fill('Controlled reviewer stopped; original code unchanged; retry authorized.');
  await taskDialog().getByLabel(/我确认按需求 v1 调用 CLI 和模型执行/).check();
  await taskDialog().getByRole('button',{name:'确认再次执行任务',exact:true}).click();
  const retry=await until(async()=>{const list=await api(`/tasks/${stopped.task_id}/executions`);return list.length===2&&list[0].state==='awaiting_review'?list[0]:null;},'review retry report',60000);
  assert.notEqual(retry.id,stopped.initial_execution_id);assert.equal(retry.attempt,2);
  assert.deepEqual(await api(endpoint('stop')),stopped);
  assert.equal((await api(`/tasks/${sample.samples.stop.task.id}/executions`)).length,1);
  report.stopRetry={passed:true,initial:stopped.initial_execution_id,retry:retry.id};

  await open();await dialog().getByRole('button',{name:'打开评审任务',exact:true}).waitFor();
  let held;
  await page.route(previewURL,route=>{held=route;});
  await dialog().getByRole('button',{name:'刷新代码评审',exact:true}).click();await until(()=>held,'held old source read');
  await dialog().getByRole('button',{name:'关闭代码评审',exact:true}).click();
  await page.getByRole('dialog',{name:'任务执行 · '+sample.samples.primary.task.title,exact:true}).getByRole('button',{name:'关闭',exact:true}).click();
  await enter('other');await dialog().getByRole('button',{name:'打开评审任务',exact:true}).waitFor();
  await held.fulfill({status:200,contentType:'application/json',body:JSON.stringify(goodPreview)});await page.unroute(previewURL);
  assert((await dialog().innerText()).includes(source('other')));assert(!(await dialog().innerText()).includes(source('primary')));
  report.boundaries.push({variant:'late-after-source-switch',passed:true});

  const changed={...sample.samples.primary.task,title:'F73 已修改的来源任务'};
  await api(`/tasks/${changed.id}`,{expected_version:1,...Object.fromEntries(['title','scope','acceptance','agent_id'].map(k=>[k,changed[k]]))},'PATCH',200);
  sample.samples.primary.task.title=changed.title;
  await api(`/agents/${manifest.agent_ids[1]}`,{enabled:false},'PATCH',200);
  await open();await dialog().getByRole('heading',{name:'原代码评审回执',exact:true}).waitFor();
  await dialog().getByText(/原绑定集成人须仍在项目内/).waitFor();
  assert((await dialog().innerText()).includes(receipt.initial_execution_id));
  report.boundaries.push({variant:'history-survives-source-revision-and-revocation',passed:true});

  await open(); await page.setViewportSize({width:390,height:844});
  await dialog().getByRole('button',{name:'打开评审任务',exact:true}).waitFor();
  await page.screenshot({path:path.join(outDir,'review-mobile.png')});
  assert(await dialog().evaluate(n=>n.scrollWidth<=n.clientWidth+1));assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1));
  report.mobile={passed:true,width:390};await page.setViewportSize({width:1280,height:960});
  devServer=await createServer({root:path.join(repo,'frontend'),configFile:false,server:{host:'127.0.0.1',port:0,proxy:{'/api':baseURL}}});
  await devServer.listen();await open('primary',devServer.resolvedUrls.local[0]);
  await dialog().getByRole('button',{name:'打开评审任务',exact:true}).click();
  await taskDialog().getByRole('button',{name:'刷新执行状态',exact:true}).waitFor();await page.keyboard.press('Escape');
  await until(async()=>await page.evaluate(()=>document.activeElement?.textContent==='打开评审任务'),'StrictMode nested task focus');
  await page.keyboard.press('Escape');
  assert.equal(await dialog().count(),0);
  await until(async()=>await page.evaluate(()=>document.activeElement?.textContent==='交指定集成人评审'),'StrictMode focus');
  report.devStrictMode={passed:true};assert.equal(events().filter(e=>e.kind==='model_call').length,0);
  assert.equal(report.pageErrors.length,0);report.passed=true;
} catch(error) {
  report.error=String(error.stack||error);process.exitCode=1;await page?.screenshot({path:path.join(outDir,'failure.png')}).catch(()=>{});
} finally {
  await devServer?.close();await browser?.close();
  if(fs.existsSync(fixtureDir)){controls({});fs.writeFileSync(path.join(fixtureDir,'shutdown'),'stop');await until(()=>childExit||spawnError,'shutdown',20000).catch(()=>{child.kill();report.cleanupForced=true;});}
  report.fixtureExit=childExit;logs.end();if(report.cleanupForced||childExit?.code!==0){report.passed=false;process.exitCode=1;}
  fs.writeFileSync(path.join(outDir,'browser-report.json'),JSON.stringify(report,null,2));
  console.log(JSON.stringify({passed:report.passed,report:path.join(outDir,'browser-report.json'),error:report.error}));
}
