import { useEffect, useRef, useState, type ReactNode } from 'react';
import { accessExpiredEvent, api, restoreAccessToken, saveAccessToken } from './api';

export function AccessGate({ children }: { children: ReactNode }) {
  const [ready, setReady] = useState(false), [busy, setBusy] = useState(true);
  const [value, setValue] = useState(''), [error, setError] = useState('');
  const generation = useRef(0);
  async function verify() {
    const current = ++generation.current;
    setBusy(true); setReady(false); setError('');
    try {
      await api('/agents');
      if (current === generation.current) setReady(true);
    } catch (error) {
      if (current === generation.current) setError(error instanceof Error ? error.message : '无法连接工作台，请检查服务后重试');
    } finally { if (current === generation.current) setBusy(false); }
  }
  function restore() {
    generation.current++; setReady(false); setError('');
    try { if (restoreAccessToken()) void verify(); else { setReady(false); setBusy(false); } }
    catch (error) { setReady(false); setBusy(false); setError(error instanceof Error ? error.message : '读取访问口令失败'); }
  }
  useEffect(() => {
    const expired = () => { generation.current++; setReady(false); setBusy(false); setValue(''); setError('访问口令已失效，请使用本次服务启动时的新口令'); };
    window.addEventListener(accessExpiredEvent, expired);
    window.addEventListener('hashchange', restore);
    restore();
    return () => { generation.current++; window.removeEventListener(accessExpiredEvent, expired); window.removeEventListener('hashchange', restore); };
  }, []);
  if (ready) return children;
  return <main className="empty-workspace"><h1>进入 CorpPilot 工作台</h1><p>请输入本次服务启动时提供的访问口令，或打开启动时提供的访问链接。口令仅保存在当前标签页会话。</p>
    <form onSubmit={event => { event.preventDefault(); if (busy) return; try { saveAccessToken(value.trim()); setValue(''); void verify(); } catch (error) { setError(error instanceof Error ? error.message : '保存访问口令失败'); } }}>
      <label>访问口令<input type="password" autoComplete="off" value={value} disabled={busy} onChange={event => setValue(event.target.value)} /></label>
      <button disabled={busy || !value.trim()}>{busy ? '正在验证访问权限…' : '验证并进入'}</button>
      <button type="button" disabled={busy} onClick={restore}>重新连接</button>
    </form>{error && <p className="error" role="alert">{error}</p>}
  </main>;
}
