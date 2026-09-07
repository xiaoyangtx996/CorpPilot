// Isolated browser contexts mutate only their own storage and mocked GET replies.
// Every non-GET API request is blocked, including any accidental model/CLI action.
import assert from 'node:assert/strict';
import { openManagement } from './navigation.mjs';
import { randomUUID } from 'node:crypto';
import { mkdir, writeFile } from 'node:fs/promises';
import { join } from 'node:path';

export async function runBoundaries({ browser, baseURL, manifest, receipt, outDir }) {
  const pendingKey = 'corppilot.goal-execution-pending.v1';
  const stopKey = 'corppilot.goal-stop-pending.v1';
  const token = manifest.access_token;
  const detailPath = `/api/workbench/goal-executions/${receipt.id}`;
  const listPath = `/api/workbench/conversations/${receipt.source_conversation_id}/goal-executions`;
  const original = { source_id: receipt.source_conversation_id, payload: receipt.request_payload,
    goal_id: receipt.id, planning_run_id: receipt.planning_run_id, launch_request_id: receipt.launch_request_id,
    ...(receipt.launch_id ? { launch_id: receipt.launch_id } : {}),
    ...(receipt.batch ? { batch_id: receipt.batch.id } : {}), stop_requested: receipt.stop_requested };
  const report = { scope: 'Context-only GET faults and sessionStorage; no real model or CLI writes',
    tests: [], blocked_writes: [], console_errors: [], page_errors: [], expected_401_errors: 0, screenshots: [], passed: false };
  const contexts = new Set();
  const safe = text => String(text).split(token).join('[redacted]');
  const json = (route, body, status = 200) => route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(body) });
  await mkdir(outDir, { recursive: true });

  async function start(seed, fault, stopSeed) {
    const context = await browser.newContext({ viewport: { width: 1280, height: 900 } });
    contexts.add(context);
    await context.route('**/api/workbench/**', async route => {
      const request = route.request(), path = new URL(request.url()).pathname;
      if (request.method() !== 'GET') {
        report.blocked_writes.push({ method: request.method(), path, forwarded: false });
        await route.abort('blockedbyclient'); return;
      }
      if (fault && await fault(route, path)) return;
      await route.continue();
    });
    await context.addInitScript(({ origin, key, seed, stopKey, stopSeed }) => {
      if (location.origin === origin) {
        sessionStorage.setItem(key, seed);
        if (stopSeed !== undefined) sessionStorage.setItem(stopKey, stopSeed);
      }
    }, { origin: new URL(baseURL).origin, key: pendingKey, seed: typeof seed === 'string' ? seed : JSON.stringify(seed),
      stopKey, stopSeed: stopSeed === undefined ? undefined : JSON.stringify(stopSeed) });
    const page = await context.newPage();
    page.setDefaultTimeout(12000);
    page.on('pageerror', error => report.page_errors.push(safe(error.message)));
    page.on('console', message => {
      if (message.type() !== 'error') return;
      const text = safe(message.text());
      if (text.includes('401')) report.expected_401_errors++;
      else report.console_errors.push(text);
    });
    await page.goto(`${baseURL}/#access_token=${token}`, { waitUntil: 'networkidle' });
    await openManagement(page); await page.getByRole('button', { name: '目标执行与恢复', exact: true }).click();
    const panel = page.getByRole('dialog', { name: '目标执行与恢复', exact: true });
    return { context, page, panel };
  }
  async function close(test) { contexts.delete(test.context); await test.context.close(); }
  async function pending(test) { return test.page.evaluate(key => sessionStorage.getItem(key), pendingKey); }
  async function screenshot(test, name, width, height) {
    await test.page.setViewportSize({ width, height });
    const box = await test.panel.boundingBox();
    assert(box && box.x >= 0 && box.y >= 0 && box.x + box.width <= width + 1 && box.y + box.height <= height + 1);
    await test.page.screenshot({ path: join(outDir, name) });
    report.screenshots.push({ file: name, width, height, dialog_within_viewport: true });
  }

  try {
    assert(receipt.launch && receipt.batch && receipt.planning.state === 'completed', 'Use the completed real goal receipt for boundary fixtures');
    const changes = {
      request_id: p => { p.request_id = randomUUID(); },
      source_message_id: p => { p.source_message_id = randomUUID(); },
      agent_id: p => { p.agent_id = p.candidate_ids.find(id => id !== p.agent_id) ?? randomUUID(); },
      candidate_ids: p => { p.candidate_ids = [...p.candidate_ids, randomUUID()]; },
      shared_brief: p => { p.shared_brief += ' Unauthorized change'; },
      max_tasks: p => { p.max_tasks = p.max_tasks === 16 ? 15 : p.max_tasks + 1; },
      confirm_execution: p => { p.confirm_execution = false; },
      confirm_handoff: p => { p.confirm_handoff = !p.confirm_handoff; },
    };
    const violations = Object.entries(changes).map(([name, mutate]) => ({ name: `request_payload.${name}`, mutate: row => mutate(row.request_payload) }));
    violations.push(
      { name: 'planning_run_id', mutate: row => { row.planning_run_id = randomUUID(); } },
      { name: 'launch_request_id', mutate: row => { row.launch_request_id = randomUUID(); } },
      { name: 'batch_id', mutate: row => { row.batch.id = randomUUID(); } },
      { name: 'execution_mapping', mutate: row => { row.launch.batch.tasks[0].execution_id = randomUUID(); } },
    );
    for (const violation of violations) {
      const wrong = structuredClone(receipt);
      violation.mutate(wrong);
      const test = await start(original, async (route, path) => {
        if (path !== detailPath) return false;
        await json(route, wrong); return true;
      });
      await test.panel.getByRole('alert').filter({ hasText: '目标回执与原授权、规划或固定批次不一致' }).waitFor();
      assert.deepEqual(JSON.parse(await pending(test)), original);
      assert.equal(await test.panel.getByRole('button', { name: '确认已读回并结束原请求核对', exact: true }).count(), 0);
      const retry = test.panel.getByRole('button', { name: '读取原目标回执（不再规划）', exact: true });
      assert.equal(await retry.isEnabled(), false, 'Invalid association must lock dependent actions');
      if (violation.name === 'request_payload.confirm_handoff') await screenshot(test, 'goal-boundary-authority-desktop.png', 1280, 900);
      report.tests.push({ name: `Reject altered ${violation.name}`, passed: true, original_pending_preserved: true, cannot_release: true });
      await close(test);
    }

    const damaged = await start('{broken');
    await damaged.panel.getByRole('alert').filter({ hasText: '恢复存储前不能新建或停止' }).waitFor();
    assert.equal(await pending(damaged), '{broken');
    assert.equal(await damaged.panel.getByRole('button', { name: '确认已读回并结束原请求核对', exact: true }).count(), 0);
    assert.equal(await damaged.panel.getByRole('button', { name: '授权秘书组织并执行', exact: true }).count(), 0);
    report.tests.push({ name: 'Malformed pending stays unchanged and locked', passed: true });
    await close(damaged);

    for (const conflict of ['authorization', 'launch_id']) {
      const conflictingStop = structuredClone(original);
      if (conflict === 'authorization') conflictingStop.payload.shared_brief += ' Different stop authorization';
      else conflictingStop.launch_id = randomUUID();
      const stoppedReceipt = { ...structuredClone(receipt), stop_requested: true, state: 'stopped' };
      const test = await start(original, async (route, path) => {
        if (path !== detailPath) return false;
        await json(route, stoppedReceipt); return true;
      }, conflictingStop);
      await test.panel.getByRole('alert').filter({ hasText: /不一致|冲突/ }).waitFor();
      assert.deepEqual(JSON.parse(await pending(test)), original, 'Goal pending must not be rewritten before both records are verified');
      assert.deepEqual(JSON.parse(await test.page.evaluate(key => sessionStorage.getItem(key), stopKey)), conflictingStop,
        'A matching goal ID does not authorize releasing a different stop record');
      assert.equal(await test.panel.getByRole('button', { name: '确认已读回并结束原请求核对', exact: true }).count(), 0);
      assert.equal(await test.panel.getByRole('button', { name: '读取原目标回执（不再规划）', exact: true }).isEnabled(), false);
      const stop = test.panel.getByRole('button', { name: '确认停止此目标', exact: true });
      assert.equal(await stop.count() ? await stop.isEnabled() : false, false);
      report.tests.push({ name: `Same goal ID with conflicting stop ${conflict} preserves both records and locks recovery`, passed: true });
      await close(test);
    }

    const unknown = structuredClone(receipt);
    Object.assign(unknown, { state: 'unknown', launch_id: null, launch: null, batch: null, error: 'Boundary fixture: provider outcome unknown', stop_requested: false });
    Object.assign(unknown.planning, { state: 'unknown', proposal: null, error: 'Boundary fixture: do not retry automatically' });
    const unknownSeed = { source_id: original.source_id, payload: original.payload, goal_id: receipt.id,
      planning_run_id: receipt.planning_run_id, launch_request_id: receipt.launch_request_id, stop_requested: false };
    let unknownReads = 0;
    const uncertain = await start(unknownSeed, async (route, path) => {
      if (path === detailPath) { unknownReads++; await json(route, unknown); return true; }
      if (path === listPath) { await json(route, [unknown]); return true; }
      return false;
    });
    await uncertain.panel.getByRole('heading', { name: '结果待核查', exact: true }).waitFor();
    await uncertain.panel.getByRole('button', { name: '核查原模型调用', exact: true }).waitFor();
    await uncertain.panel.getByRole('button', { name: '确认已读回并结束原请求核对', exact: true }).waitFor();
    assert.deepEqual(JSON.parse(await pending(uncertain)), unknownSeed);
    assert.equal(await uncertain.panel.getByRole('button', { name: '打开此目标的项目群', exact: true }).count(), 0);
    assert.equal(await uncertain.panel.getByRole('button', { name: '读取原目标回执（不再规划）', exact: true }).count(), 1);
    // Observe beyond the UI polling interval: unknown must not initiate a new operation.
    await new Promise(resolve => setTimeout(resolve, 3300));
    assert.deepEqual(JSON.parse(await pending(uncertain)), unknownSeed);
    assert.equal(report.blocked_writes.length, 0);
    await screenshot(uncertain, 'goal-boundary-unknown-mobile.png', 390, 844);
    report.tests.push({ name: 'Unknown shows the original model check with no automatic POST', passed: true, detail_gets: unknownReads });
    await close(uncertain);

    let unauthorized = true, denials = 0;
    const expired = await start(original, async (route, path) => {
      if (path === detailPath && unauthorized) { denials++; await json(route, { error: 'Fixture access expired' }, 401); return true; }
      return false;
    });
    await expired.page.getByRole('heading', { name: '进入 CorpPilot 工作台', exact: true }).waitFor();
    assert(denials > 0);
    assert.deepEqual(JSON.parse(await pending(expired)), original);
    unauthorized = false;
    await expired.page.getByLabel('访问口令', { exact: true }).fill(token);
    await expired.page.getByRole('button', { name: '验证并进入', exact: true }).click();
    await openManagement(expired.page); await expired.page.getByRole('button', { name: '目标执行与恢复', exact: true }).click();
    await expired.panel.getByRole('button', { name: '确认已读回并结束原请求核对', exact: true }).waitFor();
    const restored = JSON.parse(await pending(expired));
    assert.deepEqual(restored, original);
    assert.equal(await expired.panel.getByRole('button', { name: '读取原目标回执（不再规划）', exact: true }).count(), 1);
    await expired.panel.getByText(`目标 ${receipt.id} · 规划 ${receipt.planning_run_id}`, { exact: true }).waitFor();
    report.tests.push({ name: '401 reauthentication in the same context restores exact original goal and key', passed: true, unauthorized_gets: denials });
    await close(expired);

    assert.equal(report.tests.length, 17);
    assert.deepEqual(report.blocked_writes, []);
    assert.deepEqual(report.console_errors, []);
    assert.deepEqual(report.page_errors, []);
    report.passed = true;
    return report;
  } catch (error) {
    report.error = safe(error.stack ?? error.message);
    throw error;
  } finally {
    for (const context of contexts) await context.close();
    await writeFile(join(outDir, 'goal-boundaries.json'), JSON.stringify(report, null, 2));
  }
}
