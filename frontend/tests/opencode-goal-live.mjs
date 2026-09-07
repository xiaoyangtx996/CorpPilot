import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { createHash, randomUUID } from 'node:crypto';
import { chromium } from 'playwright';

// Live acceptance: one authorized secretary plan and its single CLI task.
// All mutations use product UI. The helper is GET-only; failed calls are never retried.
const root = process.env.CORPPILOT_BROWSER_OUTPUT || os.tmpdir();
fs.mkdirSync(root, { recursive: true });
const out = fs.mkdtempSync(path.join(root, 'corppilot-opencode-goal-live-'));
const token = process.env.CORPPILOT_LIVE_TOKEN || '';
const sanitize = value => String(value).split(token || '\0').join('[redacted]').replace(/sk-[A-Za-z0-9_-]+/g, '[redacted]');
const report = { passed: false, startedAt: new Date().toISOString(), output: out, runtime: 'Live Workbench; OpenCode Zen pure-text planning and local OpenCode CLI execution', steps: [], mutations: [], pageErrors: [], httpErrors: [] };
let browser, page, authenticated = false;
async function until(check, name, timeout = 30000) {
  const end = Date.now() + timeout;
  while (Date.now() < end) { const result = await check(); if (result) return result; await new Promise(resolve => setTimeout(resolve, 750)); }
  throw Error(`Timed out: ${name}. No planning or execution retry was sent.`);
}
try {
  const resumePath = process.env.CORPPILOT_LIVE_RESUME_REPORT;
  const resume = resumePath ? JSON.parse(fs.readFileSync(resumePath, 'utf8')) : null;
  if (resume) {
    for (const key of ['goalId', 'planningRunId', 'executionId', 'coordinatorId', 'workerId']) assert(/^[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}$/i.test(resume[key]), `Invalid original ${key}`);
    assert(/^CORPPILOT_GOAL_[0-9a-f-]{36}$/.test(resume.nonce), 'Invalid original nonce');
    report.resumedFrom = path.resolve(resumePath); report.runtime = 'Read existing live goal and artifacts; UI review only; no configuration, planning or execution writes';
  }
  const url = new URL(process.env.CORPPILOT_LIVE_URL || '');
  assert(['http:', 'https:'].includes(url.protocol) && ['127.0.0.1', 'localhost', '[::1]'].includes(url.hostname), 'Live URL must address the local Workbench');
  assert(!url.username && !url.password && !url.search && !url.hash && url.pathname === '/', 'Live URL must be an origin without credentials, query or fragment');
  assert(/^[A-Za-z0-9_-]{43}$/.test(token), 'A valid owner access token is required');
  const base = url.origin, executable = process.env.CORPPILOT_LIVE_OPENCODE_EXE || '';
  const keyEnv = process.env.CORPPILOT_LIVE_KEY_ENV || 'OPENCODE_API_KEY', model = process.env.CORPPILOT_LIVE_MODEL || 'opencode/big-pickle';
  assert(resume || /^(?:[A-Za-z]:[\\/]|\\\\).+\.exe$/i.test(executable), 'An absolute OpenCode executable path is required');
  assert(/^[A-Za-z_][A-Za-z0-9_]*$/.test(keyEnv), 'Key setting must be an environment variable name');
  assert(/^opencode\/[A-Za-z0-9._/-]+$/.test(model), 'An official Zen model identifier is required');
  browser = await chromium.launch({ headless: true });
  const context = await browser.newContext({ viewport: { width: 1440, height: 1000 } });
  page = await context.newPage(); page.setDefaultTimeout(30000);
  page.on('pageerror', error => report.pageErrors.push(sanitize(error.message)));
  page.on('request', request => {
    const parsed = new URL(request.url());
    if (parsed.origin === base && parsed.pathname.startsWith('/api/workbench/') && request.method() !== 'GET') report.mutations.push({ method: request.method(), path: parsed.pathname });
  });
  page.on('response', response => {
    const parsed = new URL(response.url());
    if (parsed.origin === base && response.status() >= 400) report.httpErrors.push({ status: response.status(), path: parsed.pathname });
  });
  async function read(endpoint, bytes = false) {
    assert(endpoint.startsWith('/') && !endpoint.includes('?') && !endpoint.includes('#'), 'GET path must be relative to Workbench');
    const response = await context.request.get(`${base}/api/workbench${endpoint}`, { headers: { Authorization: `Bearer ${token}` } });
    assert.equal(response.status(), 200, `GET ${endpoint} failed`);
    return bytes ? response.body() : response.json();
  }
  const screenshot = name => page.screenshot({ path: path.join(out, name) });
  await page.goto(base);
  await page.getByLabel('访问口令', { exact: true }).fill(token);
  await page.getByRole('button', { name: '验证并进入', exact: true }).click();
  await page.getByRole('button', { name: '模型设置', exact: true }).waitFor(); authenticated = true;
  let nonce, identities, coordinator, worker, brief, source, goal, goalId, receipt;
  if (!resume) {
  for (const endpoint of ['/runtime', '/cli-runtime']) {
    const runtime = await read(endpoint);
    assert.equal(runtime.active_requests, 0, 'Live server must have no active requests before acceptance');
    assert(runtime.running && !runtime.error, `Runtime ${endpoint} must be running`);
  }
  await page.getByRole('button', { name: '模型设置', exact: true }).click();
  const modelSettings = page.getByRole('dialog', { name: '模型设置', exact: true });
  await modelSettings.getByRole('combobox', { name: /^模型通道/ }).selectOption('opencode');
  await modelSettings.getByRole('textbox', { name: /^OpenCode 可执行文件路径/ }).fill(executable);
  await modelSettings.getByRole('textbox', { name: /^模型标识/ }).fill(model);
  await modelSettings.getByRole('textbox', { name: /^密钥环境变量名/ }).fill(keyEnv);
  for (const [name, value] of [[/^最大输出 token 数/, '4096'], [/^请求超时/, '120'], [/^每分钟请求上限/, '10'], [/^最大并发请求数/, '1']]) await modelSettings.getByRole('spinbutton', { name }).fill(value);
  await modelSettings.getByRole('checkbox', { name: '启用模型配置', exact: true }).check();
  await modelSettings.getByRole('button', { name: '保存配置', exact: true }).click();
  await modelSettings.getByText('配置已保存，未发起模型调用。', { exact: true }).waitFor();
  const modelConfig = await read('/model-settings');
  assert(modelConfig.enabled && modelConfig.configured && modelConfig.credential_available && modelConfig.executable_available && modelConfig.platform_supported);
  assert.equal(modelConfig.transport, 'opencode'); assert.equal(modelConfig.executable, executable); assert.equal(modelConfig.model, model); assert.equal(modelConfig.api_key_env, keyEnv);
  for (const [key, value] of Object.entries({ max_output_tokens: 4096, timeout_seconds: 120, rpm: 10, max_concurrency: 1 })) assert.equal(modelConfig[key], value);
  await screenshot('model-puretext-settings.png');
  await modelSettings.getByRole('button', { name: '关闭', exact: true }).first().click();
  await page.getByRole('button', { name: 'CLI 设置', exact: true }).click();
  const cliSettings = page.getByRole('dialog', { name: 'CLI 设置', exact: true });
  await cliSettings.getByRole('combobox', { name: /^CLI 工具/ }).selectOption('opencode');
  await cliSettings.getByRole('combobox', { name: /^执行后端/ }).selectOption('local');
  await cliSettings.getByRole('textbox', { name: /^CLI 可执行文件路径/ }).fill(executable);
  await cliSettings.getByRole('textbox', { name: /^模型标识/ }).fill(model);
  await cliSettings.getByRole('textbox', { name: /^密钥环境变量名/ }).fill(keyEnv);
  await cliSettings.getByRole('checkbox', { name: '启用 CLI 配置', exact: true }).check();
  await cliSettings.getByRole('button', { name: '保存配置', exact: true }).click();
  await cliSettings.getByText('CLI 配置已保存，后续调度使用新配置。', { exact: true }).waitFor();
  const cliConfig = await read('/cli-settings');
  assert(cliConfig.enabled && cliConfig.configured && cliConfig.credential_available && cliConfig.executable_available && cliConfig.platform_supported);
  assert.equal(cliConfig.engine, 'opencode'); assert.equal(cliConfig.backend, 'local'); assert.equal(cliConfig.model, model); assert.equal(cliConfig.executable, executable); assert.equal(cliConfig.api_key_env, keyEnv);
  await cliSettings.getByRole('button', { name: '关闭', exact: true }).first().click();
  report.configuration = { modelTransport: modelConfig.transport, cliEngine: cliConfig.engine, cliBackend: cliConfig.backend, model, maxOutputTokens: modelConfig.max_output_tokens };
  report.steps.push('UI configured and enabled separate pure-text planning and file execution channels');

  nonce = `CORPPILOT_GOAL_${randomUUID()}`; const suffix = nonce.slice(-8); report.nonce = nonce;
  identities = []; const beforeAgents = await read('/agents');
  for (const [name, granted] of [[`Zen 协调人 ${suffix}`, ['read', 'delegate']], [`Zen 执行者 ${suffix}`, ['read', 'write', 'execute']]]) {
    await page.getByRole('button', { name: '新建', exact: true }).click();
    const editor = page.getByRole('dialog', { name: '创建 Agent', exact: true });
    await editor.getByRole('textbox', { name: /^名字/ }).fill(name);
    await editor.getByRole('textbox', { name: /^模型路由/ }).fill('default');
    for (const [permission, label] of Object.entries({ read: '读取', write: '写入', execute: '执行工具', delegate: '委派任务' })) await editor.getByRole('checkbox', { name: label, exact: true }).setChecked(granted.includes(permission));
    await editor.getByRole('button', { name: '保存', exact: true }).click(); await editor.waitFor({ state: 'hidden' });
    const found = (await read('/agents')).filter(row => row.name === name);
    assert.equal(found.length, 1); assert(found[0].enabled); assert.deepEqual([...found[0].tools].sort(), [...granted].sort()); identities.push(found[0]);
  }
  assert.equal((await read('/agents')).length, beforeAgents.length + 2);
  [coordinator, worker] = identities;
  report.coordinatorId = coordinator.id; report.workerId = worker.id;
  brief = `Create exactly ONE task assigned ONLY to the executor ${worker.name} (agent_id ${worker.id}). The coordinator ${coordinator.name} (agent_id ${coordinator.id}) plans but must not receive an execution task. No dependencies and no additional tasks. Task scope: use only the built-in write tool to create artifacts/goal-proof.txt in the execution workspace. Its exact UTF-8 content must be ${nonce} followed by one LF newline. Do not use shell, network tools, delegation, or other tools; do not create other files. Acceptance: the single saved goal-proof.txt artifact must download with these exact bytes and matching SHA-256. Return a valid collaboration plan in the required schema, preserving this exact nonce and file path in task scope and acceptance.`;
  await page.locator('button.person').filter({ hasText: coordinator.name }).click();
  await page.getByPlaceholder('向会话发送消息…').fill(`请组织执行者完成单文件交付，最后由我验收。验收标识：${nonce}`);
  await page.getByRole('button', { name: '发送', exact: true }).click();
  source = (await read('/conversations')).find(row => row.type === 'dm' && row.member_ids.includes(coordinator.id)); assert(source);
  assert.deepEqual(await read(`/conversations/${source.id}/goal-executions`), []);
  assert.deepEqual(await read(`/conversations/${source.id}/collaboration-proposals`), []);
  await page.locator('article.message.owner').filter({ hasText: nonce }).getByRole('button', { name: '交给秘书组织并执行', exact: true }).click();
  goal = page.getByRole('dialog', { name: '目标执行与恢复', exact: true });
  await goal.getByRole('textbox', { name: /^明确共享给秘书与项目的摘要/ }).fill(brief);
  await goal.getByRole('combobox', { name: /^目标协调人/ }).selectOption(coordinator.id);
  for (const identity of identities) await goal.getByRole('checkbox', { name: identity.name, exact: true }).check();
  assert.equal(await goal.locator('.member-choices input:checked').count(), 2);
  await goal.getByRole('spinbutton', { name: /^最多任务数/ }).fill('1');
  await goal.getByRole('checkbox', { name: /授权固定首批成果按依赖/ }).uncheck();
  await goal.getByRole('checkbox', { name: /我确认共享上述摘要/ }).check();
  await screenshot('goal-authorization.png');
  const [acceptedResponse] = await Promise.all([
    page.waitForResponse(response => response.url() === `${base}/api/workbench/conversations/${source.id}/goal-executions` && response.request().method() === 'POST'),
    goal.getByRole('button', { name: '授权秘书组织并执行', exact: true }).click(),
  ]);
  assert(acceptedResponse.ok(), `Goal authorization returned HTTP ${acceptedResponse.status()}`);
  const accepted = await acceptedResponse.json(); assert(typeof accepted.id === 'string' && accepted.id);
  goalId = accepted.id; report.goalId = goalId; report.planningRunId = accepted.planning_run_id;
  report.steps.push('UI authorized exactly one secretary plan and at most one task for the two selected identities');
  console.log(JSON.stringify({ stage: 'goal-authorized', goalId, planningRunId: report.planningRunId }));
  receipt = await until(async () => {
    const row = await read(`/goal-executions/${goalId}`);
    assert.equal(row.id, goalId); assert.equal(row.planning_run_id, report.planningRunId);
    report.lastState = { goal: row.state, planning: row.planning.state, execution: row.batch?.items.map(item => item.execution.state) ?? [] };
    if (['failed', 'unknown', 'cancelled', 'stop_requested', 'stopped'].includes(row.state) || ['failed', 'unknown', 'cancelled'].includes(row.planning.state)) throw Error(`Original goal stopped: ${row.state}; planning ${row.planning.state}. No retry sent.`);
    if (!row.batch) return null;
    assert.equal(row.batch.items.length, 1, 'Goal must contain exactly one task execution');
    const execution = row.batch.items[0].execution; report.executionId = execution.id;
    if (['queued', 'running', 'stopping'].includes(execution.state)) return null;
    assert.equal(execution.state, 'awaiting_review', 'Original CLI execution failed; no retry sent');
    return row;
  }, 'original secretary plan and single CLI task', 360000);
  } else {
    goalId = resume.goalId; nonce = resume.nonce;
    receipt = await read(`/goal-executions/${goalId}`);
    assert.equal(receipt.id, goalId); assert.equal(receipt.planning_run_id, resume.planningRunId);
    assert.equal(receipt.state, 'launched'); assert.equal(receipt.batch?.items.length, 1);
    assert.equal(receipt.batch.items[0].execution.id, resume.executionId);
    assert.equal(receipt.batch.items[0].execution.state, 'awaiting_review');
    coordinator = await read(`/agents/${resume.coordinatorId}`); worker = await read(`/agents/${resume.workerId}`);
    assert.equal(coordinator.id, resume.coordinatorId); assert.equal(worker.id, resume.workerId);
    assert.deepEqual([...coordinator.tools].sort(), ['delegate', 'read']); assert.deepEqual([...worker.tools].sort(), ['execute', 'read', 'write']);
    assert(coordinator.enabled && worker.enabled); identities = [coordinator, worker];
    source = await read(`/conversations/${receipt.source_conversation_id}`);
    assert.equal(source.type, 'dm'); assert(source.member_ids.includes(coordinator.id));
    brief = receipt.request_payload.shared_brief;
    assert(brief.includes(nonce) && brief.includes(worker.id) && brief.includes('artifacts/goal-proof.txt'));
    assert(receipt.batch.items[0].task.scope.includes(nonce));
    Object.assign(report, { goalId, nonce, planningRunId: resume.planningRunId, executionId: resume.executionId, coordinatorId: coordinator.id, workerId: worker.id });
    report.steps.push('GET verified original goal, planning, execution and identities from preserved failed report; no new model call');
  }
  const item = receipt.batch.items[0], run = item.execution;
  assert.equal(receipt.planning.state, 'completed'); assert.equal(receipt.planning.id, receipt.planning_run_id); assert.equal(receipt.planning.agent_id, coordinator.id);
  assert.equal(receipt.request_payload.shared_brief, brief); assert.equal(receipt.request_payload.max_tasks, 1); assert.equal(receipt.request_payload.confirm_handoff, false);
  assert.deepEqual([...receipt.request_payload.candidate_ids].sort(), identities.map(row => row.id).sort());
  assert.equal(run.agent_id, worker.id); assert.equal(run.task_id, item.task.id); assert.equal(run.attempt, 1); assert.equal(run.requirement_version, 1); assert.equal(run.exit_code, 0);
  const plans = await read(`/conversations/${source.id}/collaboration-proposals`), runs = await read(`/tasks/${item.task.id}/executions`);
  // Goal-owned planning is intentionally excluded from the standalone proposal list.
  assert.equal(plans.length, 0); assert.equal(runs.length, 1); assert.equal(runs[0].id, run.id);
  const artifacts = await read(`/executions/${run.id}/artifacts`); assert.equal(artifacts.length, 1);
  const artifact = artifacts[0]; assert(['goal-proof.txt', 'artifacts/goal-proof.txt'].includes(artifact.path));
  const bytes = await read(`/artifacts/${artifact.id}/download`, true);
  assert.equal(bytes.toString('utf8'), `${nonce}\n`); assert.equal(bytes.length, artifact.size); assert.equal(createHash('sha256').update(bytes).digest('hex'), artifact.sha256);
  const tools = await read(`/executions/${run.id}/tool-activities`);
  assert.equal(tools.payload.source, 'opencode_jsonl'); assert.equal(tools.execution_id, run.id); assert.equal(tools.agent_id, worker.id);
  assert(tools.payload.events.length > 0 && tools.payload.events.every(row => row.type === 'opencode_tool' && row.status === 'completed'));
  assert(tools.payload.events.every(row => row.details.tool?.sha256 === createHash('sha256').update('write').digest('hex')), 'Only the authorized write tool may be used');
  for (const field of ['invalid_lines', 'unknown_items', 'dropped_events']) assert.equal(tools.payload[field], 0);
  assert.equal(tools.payload.output_limited, false);
  fs.writeFileSync(path.join(out, 'goal-proof.txt'), bytes);
  report.planning = { id: receipt.planning_run_id, state: receipt.planning.state, model: receipt.planning.model, usage: receipt.planning.usage };
  report.execution = { id: run.id, state: run.state, taskId: item.task.id, agentId: run.agent_id, usage: run.usage };
  report.batchId = receipt.batch.id; report.projectId = receipt.launch.collaboration.project_conversation_id; report.artifact = artifact; report.toolEventCount = tools.payload.events.length;
  if (resume) {
    const project = await read(`/conversations/${report.projectId}`);
    assert.equal(project.id, report.projectId); assert.equal(project.type, 'project'); assert.equal(item.task.conversation_id, project.id);
    assert.deepEqual([...project.member_ids].sort(), identities.map(row => row.id).sort());
    const projectButton = page.locator('button.conversation-item').filter({ has: page.getByText(project.title, { exact: true }) });
    assert.equal(await projectButton.count(), 1, 'Original project must be uniquely identifiable in UI'); await projectButton.click();
  } else {
    await goal.getByRole('button', { name: '确认已读回并结束原请求核对', exact: true }).click();
    await goal.getByRole('button', { name: '打开此目标的项目群', exact: true }).click();
  }
  const card = page.locator('article.task-card').filter({ has: page.getByRole('heading', { name: item.task.title, exact: true }) });
  await card.getByRole('button', { name: '执行记录与控制', exact: true }).click();
  const reviewDialog = page.getByRole('dialog', { name: `任务执行 · ${item.task.title}`, exact: true });
  await reviewDialog.getByText('成果与 Owner 评审', { exact: true }).click();
  if (!item.review) {
    await reviewDialog.getByRole('textbox', { name: /^评审理由/ }).fill(`Downloaded the sole requested artifact; exact nonce with LF, size and SHA-256 verified. Native OpenCode tool receipt and original secretary/task identities confirmed. ${nonce}`);
    await reviewDialog.getByRole('checkbox', { name: '我已查验全部成果，确认符合本次需求与验收标准', exact: true }).check();
    await reviewDialog.getByRole('button', { name: '确认提交评审', exact: true }).click();
  }
  await reviewDialog.getByRole('region', { name: 'Owner 评审决定', exact: true }).waitFor();
  receipt = await read(`/goal-executions/${goalId}`);
  assert.equal(receipt.batch.items[0].review.decision, 'approved'); assert.equal(receipt.batch.items[0].review.execution_id, run.id); assert.deepEqual(receipt.batch.items[0].review.artifact_ids, [artifact.id]);
  report.review = { decision: 'approved', executionId: run.id, artifactIds: [artifact.id] };
  assert.equal((await read(`/conversations/${source.id}/goal-executions`)).length, 1);
  assert.equal((await read(`/conversations/${source.id}/collaboration-proposals`)).length, 0); assert.equal((await read(`/tasks/${item.task.id}/executions`)).length, 1);
  assert.equal(report.mutations.filter(row => row.method === 'POST' && row.path.endsWith('/goal-executions')).length, resume ? 0 : 1);
  if (resume) assert(report.mutations.every(row => row.method === 'POST' && row.path === `/api/workbench/executions/${run.id}/review`), 'Resume may only submit the original execution review');
  assert(!report.mutations.some(row => row.method === 'POST' && /\/tasks\/[^/]+\/executions$|\/collaboration-proposals$|\/runs$/.test(row.path)), 'Unexpected separate planning or execution submission');
  assert.equal(report.pageErrors.length, 0); assert.equal(report.httpErrors.length, 0);
  await screenshot('owner-approved.png');
  report.steps.push('One real secretary plan and one CLI task verified; downloaded nonce/SHA-256 proof then approved original project task through UI');
  report.passed = true;
} catch (error) {
  report.error = sanitize(error.stack || error); process.exitCode = 1;
  if (page && authenticated) await page.screenshot({ path: path.join(out, 'failure.png') }).catch(() => {});
} finally {
  await browser?.close(); report.finishedAt = new Date().toISOString();
  const reportPath = path.join(out, 'browser-report.json'); fs.writeFileSync(reportPath, sanitize(JSON.stringify(report, null, 2)));
  console.log(JSON.stringify({ passed: report.passed, report: reportPath, goalId: report.goalId, planningRunId: report.planningRunId, executionId: report.executionId, error: report.error }));
}
