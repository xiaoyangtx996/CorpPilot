import { useEffect, useRef, useState } from 'react';
import { api } from './api';
import { checkRepository, RepositoryEvidence, type RepositoryBinding } from './RepositorySettings';

export function RepositorySummary({ executionId, agentId, conversationId, onClose }: { executionId: string; agentId: string; conversationId: string; onClose: () => void }) {
  const dialog = useRef<HTMLDialogElement>(null), opener = useRef(document.activeElement), serial = useRef(0);
  const [binding, setBinding] = useState<RepositoryBinding | null>(null), [identity, setIdentity] = useState<{ attempt: number; requirement_version: number } | null>(null), [loaded, setLoaded] = useState(false), [busy, setBusy] = useState(false), [error, setError] = useState('');
  async function load() {
    const token = ++serial.current; setBinding(null); setIdentity(null); setLoaded(false); setBusy(true); setError('');
    try {
      const [run, row] = await Promise.all([api<{ id: string; agent_id: string; attempt: number; requirement_version: number }>(`/executions/${executionId}`), api<{ execution_id: string; agent_id: string; attempt: number; requirement_version: number; repository: unknown }>(`/executions/${executionId}/repository`)]);
      if (token !== serial.current) return;
      if (!run || !row || typeof run !== 'object' || typeof row !== 'object' || Object.keys(row).sort().join() !== 'agent_id,attempt,execution_id,repository,requirement_version' || run.id !== executionId || run.agent_id !== agentId || row.execution_id !== executionId || row.agent_id !== agentId || !Number.isSafeInteger(run.attempt) || run.attempt < 1 || !Number.isSafeInteger(run.requirement_version) || run.requirement_version < 1 || row.attempt !== run.attempt || row.requirement_version !== run.requirement_version) throw Error('执行仓库回执与实际实例不一致。');
      const saved = row.repository === null ? null : checkRepository(row.repository, conversationId);
      if (saved && saved.snapshot === null) throw Error('执行不能绑定停用版本的仓库。');
      setBinding(saved); setIdentity({ attempt: run.attempt, requirement_version: run.requirement_version }); setLoaded(true);
    } catch (e) { if (token === serial.current) setError(e instanceof Error ? e.message : '读取执行仓库失败'); }
    finally { if (token === serial.current) setBusy(false); }
  }
  useEffect(() => { const node = dialog.current; node?.showModal(); void load(); return () => { serial.current++; node?.close(); if (opener.current instanceof HTMLElement) opener.current.focus(); }; }, [executionId, agentId, conversationId]);
  return <dialog ref={dialog} aria-label="查看当次代码仓库" onCancel={e => { e.preventDefault(); onClose(); }}><header><h2>查看当次代码仓库</h2><button onClick={onClose}>关闭当次代码仓库</button></header>
    <p style={{ overflowWrap: 'anywhere' }}>执行 {executionId}</p><p>只读入队时冻结的仓库授权，不代表 checkout 已准备、代码已修改或已经合入。刷新不会创建副本。</p>
    <button disabled={busy} onClick={() => void load()}>刷新当次代码仓库</button>{error && <p role="alert" className="error">{error}</p>}{busy && <p role="status">读取当次代码仓库中…</p>}
    {identity && <p>第{identity.attempt}次 · 需求 v{identity.requirement_version}</p>}
    {loaded && (binding ? <section aria-label="当次冻结仓库"><RepositoryEvidence row={binding} /><p style={{ overflowWrap: 'anywhere' }}>准备目标：work/repository · 分支 corppilot/run-{executionId}</p><p>生成提交身份：CorpPilot Worker / worker@corppilot.local。不是源库作者，也没有自动推送。</p></section> : <p>该执行未冻结代码仓库绑定；后续项目设置不会追溯加入。</p>)}
  </dialog>;
}
