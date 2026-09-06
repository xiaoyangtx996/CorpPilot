import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { spawn, spawnSync } from 'node:child_process';
import { chromium } from 'playwright';
import { createServer } from 'vite';

const repo = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..');
const outputRoot = process.env.CORPPILOT_BROWSER_OUTPUT || os.tmpdir();
fs.mkdirSync(outputRoot, { recursive: true });
const outDir = fs.mkdtempSync(path.join(outputRoot, 'corppilot-code-integration-'));
const fixtureDir = path.join(outDir, 'fixture');
const python = process.env.CORPPILOT_PYTHON || path.join(repo, '.venv', process.platform === 'win32' ? 'Scripts/python.exe' : 'bin/python');
const child = spawn(python, [path.join(repo, 'tests/browser/goal_fixture.py'), '--data-dir', fixtureDir, '--code-integration'], { cwd: repo, windowsHide: true });
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
  await until(() => { if(spawnError || childExit) throw Error('Fixture exited; inspect fixture.log'); return fs.existsSync(path.join(fixtureDir,'manifest.json')); }, 'native fixture startup',60000);
  const manifest=JSON.parse(fs.readFileSync(path.join(fixtureDir,'manifest.json'))), sample=manifest.code_integration;
  const baseURL=`http://127.0.0.1:${manifest.port}`, base=`/conversations/${sample.room.id}/code-integrations`;
  browser=await chromium.launch({headless:true}); const context=await browser.newContext({viewport:{width:1280,height:960}});
  page=await context.newPage(); page.setDefaultTimeout(15000); page.on('pageerror',e=>report.pageErrors.push(String(e)));
  const key=`corppilot.code-integration-pending.v1.${sample.room.id}`, declarationKey=`corppilot.code-integration-reconciliation.v1.${sample.room.id}`;
  const dialog=()=>page.getByRole('dialog',{name:'代码集成',exact:true});
  const confirm=()=>dialog().getByLabel('我已核对全部来源及最新评审成果，确认独立集成代码',{exact:true});
  const pending=()=>page.evaluate(k=>sessionStorage.getItem(k),key);
  const declarationPending=()=>page.evaluate(k=>sessionStorage.getItem(k),declarationKey);
  async function api(url,data,method=data?'POST':'GET',expected=data?202:200) {
    const r=await context.request.fetch(baseURL+'/api/workbench'+url,{method,headers:{Authorization:`Bearer ${manifest.access_token}`},...(data?{data}:{})});
    assert.equal(r.status(),expected,url);return r.json();
  }
  async function open(url=baseURL, waitForDialog=true) {
    await page.goto(`${url.replace(/\/$/,'')}/#access_token=${manifest.access_token}`);
    await page.getByRole('button',{name:new RegExp(sample.room.title)}).click();
    await page.getByRole('button',{name:'代码集成',exact:true}).click();
    if(waitForDialog) await until(async()=>!(await dialog().getByRole('button',{name:/^(刷新集成历史|只读核对原集成请求)$/}).isDisabled()),'dialog initialized');
  }
  async function select() {
    for(const name of ['alpha','beta']) {
      await dialog().getByText('选择来源 · '+sample.samples[name].task.title,{exact:true}).click();
      await dialog().getByRole('button',{name:'读取来源执行 · '+sample.samples[name].task.title,exact:true}).click();
      await dialog().getByLabel('选择来源执行 '+sample.samples[name].run.id,{exact:true}).check();
    }
  }
  async function preview() {
    await dialog().getByRole('button',{name:'读取集成预览',exact:true}).click();
    await until(async()=>!(await dialog().getByRole('button',{name:'读取集成预览',exact:true}).isDisabled()),'preview finished');
    await dialog().getByText('当前预览允许确认，执行时仍会核对固定来源与权限。',{exact:true}).waitFor();
  }
  async function newAttempt() {
    const previous=(await api(base))[0]?.id;
    await preview(); await dialog().getByLabel('前次集成影响核查说明',{exact:true}).fill('Owner checked prior result and files; explicitly requests a new independent copy.');
    await confirm().check(); await dialog().getByRole('button',{name:'确认新建一次集成',exact:true}).click();
    await until(async()=>{const row=(await api(base))[0];return row&&row.id!==previous;},'new authorization accepted');
  }
  const nativeCount=()=>events().filter(e=>e.kind==='integration_call').length;
  const last=async()=> (await api(base))[0];
  const terminal=()=>until(async()=>{const r=await last();return r&&!['running','stopping'].includes(r.state)?r:null;},'native final result',60000);
  function git(cwd,...args) { const r=spawnSync('git',['-c','core.hooksPath='+os.devNull,...args],{cwd,encoding:'utf8',windowsHide:true});assert.equal(r.status,0,r.stderr);return r.stdout.trim(); }
  await open(); await select(); await preview(); await confirm().check();
  await dialog().getByRole('button',{name:'上移来源 2',exact:true}).click(); assert(await confirm().isDisabled());
  await preview(); await confirm().check();
  const firstPostURL='**/api/workbench'+base;
  await page.route(firstPostURL,route=>route.request().method()==='POST'?route.fulfill({status:400,json:{error:'First integration rejected'}}):route.continue());
  await dialog().getByRole('button',{name:'确认集成代码',exact:true}).click();await dialog().getByRole('alert').first().waitFor();assert(JSON.parse(await pending()).rejected);await page.unroute(firstPostURL);
  await dialog().getByRole('button',{name:'只读核对原集成请求',exact:true}).click();await dialog().getByRole('button',{name:'结束已拒绝集成请求',exact:true}).click();
  assert.equal(await pending(),null);assert.equal(nativeCount(),0);await confirm().check();
  controls({drop_integration:true,integration_get503:true});
  await dialog().getByRole('button',{name:'确认集成代码',exact:true}).click();
  await dialog().getByRole('alert').first().waitFor(); const original=await pending(); assert(original);
  const sent=JSON.parse(original);assert.deepEqual(sent.payload.source_execution_ids,['beta','alpha'].map(n=>sample.samples[n].run.id));assert(!sent.rejected);
  await until(()=>events().some(e=>e.kind==='integration_native_result'),'actual native result',60000);
  await open();assert.equal(await pending(),original);assert.equal(nativeCount(),1);
  controls({});await dialog().getByRole('button',{name:'只读核对原集成请求',exact:true}).click();
  await until(async()=>(await pending())===null,'original request recovered');const first=await terminal();assert.equal(first.state,'completed');
  assert.equal(first.request_id,sent.payload.request_id);assert.equal(nativeCount(),1);
  const target=path.join(fixtureDir,'state',first.destination_relative);
  assert.equal(git(target,'rev-parse','HEAD'),first.result.commit);assert.equal(git(target,'rev-parse','HEAD^'),sample.repository.snapshot.commit);
  assert.equal(git(target,'status','--porcelain'),'');for(const name of ['alpha','beta'])assert.equal(fs.readFileSync(path.join(target,name+'.txt'),'utf8'),`F76 ${name}\n`);
  assert.equal(git(sample.repository.snapshot.source_path,'rev-parse','HEAD'),sample.repository.snapshot.commit);
  assert.equal(git(sample.repository.snapshot.source_path,'status','--porcelain'),'');
  report.native={passed:true,id:first.id,commit:first.result.commit,target,sources:sent.payload.source_execution_ids};
  await page.screenshot({path:path.join(outDir,'integration-desktop.png')});

  // Corrupt or foreign observations cannot erase the original authorization or issue writes.
  const mainPage=page;
  for(const variant of ['bad-storage','get401','wrong-request','wrong-snapshot','wrong-destination','wrong-result','changed-state-valid']) {
    const isolated=await browser.newContext();const raw=variant==='bad-storage'?'{broken':original;
    await isolated.addInitScript(({key,raw})=>sessionStorage.setItem(key,raw),{key,raw});
    page=await isolated.newPage();page.setDefaultTimeout(15000);page.on('pageerror',e=>report.pageErrors.push(String(e)));let writes=0;
    await page.route('**/api/workbench/**',route=>{if(route.request().method()!=='GET'){writes++;return route.abort();}return route.continue();});
    await page.route('**'+base+'/requests/*',route=>{
      if(variant==='get401')return route.fulfill({status:401,json:{error:'Expired'}});
      const r=structuredClone(first);
      if(variant==='wrong-request')r.request_id='foreign';
      if(variant==='wrong-snapshot')r.authorization_snapshot.sources.reverse();
      if(variant==='wrong-destination')r.destination_relative='code-integrations/foreign/repository';
      if(variant==='wrong-result')r.result.source_execution_ids.reverse();
      return route.fulfill({status:200,json:r});
    });
    await open(baseURL,variant!=='get401');
    if(variant==='changed-state-valid')await until(async()=>(await pending())===null,'valid dynamic completion');
    else {await page.getByRole('alert').first().waitFor();assert.equal(await pending(),raw);}
    assert.equal(writes,0);report.boundaries.push({variant,passed:true});await isolated.close();
  }
  page=mainPage;
  await open();await select();await preview();
  await dialog().getByRole('button',{name:'查看最新评审报告 1',exact:true}).click();
  const reviewDialog=()=>page.getByRole('dialog',{name:'任务执行 · 固定代码成果评审',exact:true});
  await reviewDialog().getByRole('button',{name:'刷新执行状态',exact:true}).waitFor();await page.keyboard.press('Escape');
  await until(async()=>await page.evaluate(()=>document.activeElement?.textContent==='查看最新评审报告 1'),'nested report focus');
  await preview();await dialog().getByLabel('前次集成影响核查说明',{exact:true}).fill('Request an independent copy after checking original.');await confirm().check();
  // An unreceived request keeps its original key even if a later retry receives a 4xx.
  const postURL='**/api/workbench'+base;
  await page.route(postURL,route=>route.request().method()==='POST'?route.abort():route.continue());
  await dialog().getByRole('button',{name:'确认新建一次集成',exact:true}).click();await dialog().getByRole('alert').first().waitFor();
  const lost=JSON.parse(await pending());assert(!lost.rejected);await page.unroute(postURL);
  await page.route(postURL,route=>route.request().method()==='POST'?route.fulfill({status:400,json:{error:'Retry temporarily rejected'}}):route.continue());
  await dialog().getByRole('button',{name:'同键重试集成请求',exact:true}).click();await dialog().getByRole('alert').first().waitFor();assert(!JSON.parse(await pending()).rejected);
  assert.equal(await dialog().getByRole('button',{name:'结束已拒绝集成请求',exact:true}).count(),0);await page.unroute(postURL);
  controls({pause_integration:true});await dialog().getByRole('button',{name:'同键重试集成请求',exact:true}).click();
  await until(async()=>(await pending())===null,'same-key accepted');await until(()=>nativeCount()===2,'paused operation');
  await dialog().getByRole('button',{name:'请求停止集成',exact:true}).click();const stopped=await terminal();assert.equal(stopped.state,'cancelled');assert.equal(stopped.request_id,lost.payload.request_id);
  controls({});report.stopAndRetry={passed:true,id:stopped.id};

  await open();await select();controls({integration_unknown:true});await newAttempt();const unknown=await terminal();assert.equal(unknown.state,'unknown');
  assert.equal(nativeCount(),3);assert(fs.existsSync(path.join(fixtureDir,'state',unknown.destination_relative,'.git')));
  controls({});await dialog().getByRole('button',{name:'刷新集成历史',exact:true}).click();
  await dialog().getByRole('button',{name:'人工核查集成 '+unknown.id,exact:true}).click();
  await dialog().getByLabel('我已核实原生 Git 及其子进程均已停止',{exact:true}).check();
  await dialog().getByLabel('我已核查集成文件及其他外部影响',{exact:true}).check();
  await dialog().getByLabel('集成核查依据',{exact:true}).fill('Fixture native call returned and event records actual commit; inspected retained independent checkout.');
  const declarationURL='**/api/workbench/code-integrations/'+unknown.id+'/reconciliation';
  await page.route(declarationURL,route=>route.fulfill({status:400,json:{error:'First declaration rejected before acceptance'}}));
  await dialog().getByRole('button',{name:'保存集成人工核查',exact:true}).click();await dialog().getByRole('alert').first().waitFor();
  assert(JSON.parse(await declarationPending()).rejected);await page.unroute(declarationURL);
  await dialog().getByRole('button',{name:'刷新集成历史',exact:true}).click();
  await dialog().getByRole('button',{name:'结束已拒绝集成核查',exact:true}).click();
  await until(async()=>(await declarationPending())===null,'first rejected declaration released after GET');
  await dialog().getByLabel('我已核实原生 Git 及其子进程均已停止',{exact:true}).check();
  await dialog().getByLabel('我已核查集成文件及其他外部影响',{exact:true}).check();
  await dialog().getByLabel('集成核查依据',{exact:true}).fill('Owner rechecked stopped native operation and actual checkout after rejected declaration.');
  controls({drop_integration:true,integration_get503:true});await dialog().getByRole('button',{name:'保存集成人工核查',exact:true}).click();
  await dialog().getByRole('alert').first().waitFor();const declarationRaw=await declarationPending();assert(declarationRaw);
  await open();assert.equal(await declarationPending(),declarationRaw);controls({});
  await dialog().getByRole('button',{name:'刷新集成历史',exact:true}).click();await until(async()=>(await declarationPending())===null,'declaration independent readback');
  const declared=await api('/code-integrations/'+unknown.id);assert.equal(declared.state,'unknown');assert.equal(declared.reconciliation.source,'owner_declared');assert.deepEqual(declared.reconciliation.request_payload,JSON.parse(declarationRaw).payload);assert.equal(nativeCount(),3);
  report.declaration={passed:true,id:unknown.id};

  // A later reviewer attempt blocks integration until its own entire report is approved.
  const prior=sample.samples.alpha.review;
  const retry=await api(`/tasks/${prior.task_id}/executions`,{request_id:'latest-browser-review',expected_version:1,previous_execution_id:prior.initial_execution_id,reconciliation_note:'Owner explicitly asks for second fixed-source review'});
  await until(async()=>(await api('/executions/'+retry.id)).state==='awaiting_review','new controlled reviewer',60000);
  await open();await select();await dialog().getByRole('button',{name:'读取集成预览',exact:true}).click();
  await dialog().getByText(/最新评审执行：/).filter({hasText:retry.id}).waitFor();assert(await confirm().isDisabled());
  const outputs=await api('/executions/'+retry.id+'/artifacts');
  await api('/executions/'+retry.id+'/review',{request_id:'approve-new-review',expected_version:1,decision:'approved',note:'Owner accepts complete latest report',artifact_ids:outputs.map(a=>a.id)},'POST',201);
  await preview();await dialog().getByText(/最新评审执行：/).filter({hasText:retry.id}).waitFor();
  await newAttempt();const final=await terminal();assert.equal(final.state,'completed');assert.equal(final.authorization_snapshot.sources[0].review_execution.id,retry.id);assert.equal(nativeCount(),4);
  report.latestReview={passed:true,initial:prior.initial_execution_id,latest:retry.id,integration:final.id};
  controls({integration_unknown:true});await newAttempt();const competitor=await terminal();assert.equal(competitor.state,'unknown');assert.equal(nativeCount(),5);controls({});
  await dialog().getByRole('button',{name:'刷新集成历史',exact:true}).click();
  await dialog().getByRole('button',{name:'人工核查集成 '+competitor.id,exact:true}).click();
  await dialog().getByLabel('我已核实原生 Git 及其子进程均已停止',{exact:true}).check();await dialog().getByLabel('我已核查集成文件及其他外部影响',{exact:true}).check();
  await dialog().getByLabel('集成核查依据',{exact:true}).fill('Owner A inspected stopped operation.');
  const conflictURL='**/api/workbench/code-integrations/'+competitor.id+'/reconciliation';
  await page.route(conflictURL,route=>route.abort());await dialog().getByRole('button',{name:'保存集成人工核查',exact:true}).click();await dialog().getByRole('alert').first().waitFor();
  const localDeclaration=JSON.parse(await declarationPending());assert(!localDeclaration.rejected);await page.unroute(conflictURL);
  await page.route(conflictURL,route=>route.fulfill({status:400,json:{error:'Unknown retry rejected'}}));
  await dialog().getByRole('button',{name:'同键重试集成核查',exact:true}).click();await dialog().getByRole('alert').first().waitFor();assert(!JSON.parse(await declarationPending()).rejected);await page.unroute(conflictURL);
  const otherDeclaration={...localDeclaration.payload,request_id:'another-owner-tab',note:'Owner B independently checked stopped native operation and retained checkout.'};
  await api('/code-integrations/'+competitor.id+'/reconciliation',otherDeclaration,'POST',201);
  await dialog().getByRole('button',{name:'刷新集成历史',exact:true}).click();
  await dialog().getByRole('region',{name:'已有其他集成核查声明',exact:true}).waitFor();assert(await declarationPending());
  await dialog().getByRole('button',{name:'接受已有集成核查并结束本地请求',exact:true}).click();await until(async()=>(await declarationPending())===null,'explicit competing declaration acceptance');
  assert.deepEqual((await api('/code-integrations/'+competitor.id)).reconciliation.request_payload,otherDeclaration);assert.equal(nativeCount(),5);
  report.boundaries.push({variant:'declaration-first-rejection-and-competing-owner',passed:true});
  await dialog().getByRole('button',{name:'刷新集成历史',exact:true}).click();
  await page.setViewportSize({width:390,height:844});await page.screenshot({path:path.join(outDir,'integration-mobile.png')});
  assert(await dialog().evaluate(n=>n.scrollWidth<=n.clientWidth+1));assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1));report.mobile={passed:true,width:390};
  await page.setViewportSize({width:1280,height:960});
  devServer=await createServer({root:path.join(repo,'frontend'),configFile:false,server:{host:'127.0.0.1',port:0,proxy:{'/api':baseURL}}});await devServer.listen();await open(devServer.resolvedUrls.local[0]);
  await page.keyboard.press('Escape');await until(async()=>await page.evaluate(()=>document.activeElement?.textContent==='代码集成'),'StrictMode focus');report.devStrictMode={passed:true};
  assert.equal(events().filter(e=>e.kind==='model_call').length,0);assert.equal(report.pageErrors.length,0);report.passed=true;
} catch(error) {
  report.error=String(error.stack||error);process.exitCode=1;await page?.screenshot({path:path.join(outDir,'failure.png')}).catch(()=>{});
  if(page) { report.visible=await page.locator('body').innerText().catch(()=>null);report.pending=await page.evaluate(()=>Object.fromEntries(Object.entries(sessionStorage).filter(([k])=>k.startsWith('corppilot.code-integration')))).catch(()=>null); }
} finally {
  await devServer?.close();await browser?.close();
  if(fs.existsSync(fixtureDir)){controls({});fs.writeFileSync(path.join(fixtureDir,'shutdown'),'stop');await until(()=>childExit||spawnError,'shutdown',20000).catch(()=>{child.kill();report.cleanupForced=true;});}
  report.fixtureExit=childExit;logs.end();if(report.cleanupForced||childExit?.code!==0){report.passed=false;process.exitCode=1;}
  fs.writeFileSync(path.join(outDir,'browser-report.json'),JSON.stringify(report,null,2));
  console.log(JSON.stringify({passed:report.passed,report:path.join(outDir,'browser-report.json'),error:report.error}));
}
