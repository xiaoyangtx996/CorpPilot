import { useEffect, useRef, useState } from 'react';
import { api, type Agent, type Conversation, type Message } from './api';

const failure = (error: unknown) => error instanceof Error ? error.message : '请求失败，请重试';
export type Draft = { content: string; request_id: string; sentContent: string };

export function ConversationEditor({ conversation, agents, onClose, onSaved }: {
  conversation: Conversation | null; agents: Agent[]; onClose: () => void; onSaved: (value: Conversation) => void;
}) {
  const dialog = useRef<HTMLDialogElement>(null);
  const [current, setCurrent] = useState(conversation);
  const [title, setTitle] = useState(conversation?.title ?? '');
  const [type, setType] = useState<'board' | 'project'>('board');
  const [members, setMembers] = useState<string[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  useEffect(() => {
    const previous = document.activeElement;
    dialog.current?.showModal();
    return () => { if (previous instanceof HTMLElement) previous.focus(); };
  }, []);
  async function save(event: React.FormEvent) {
    event.preventDefault(); if (busy) return;
    setBusy(true); setError('');
    try {
      const value = await api<Conversation>(current ? `/conversations/${current.id}` : '/conversations', current ? 'PATCH' : 'POST', current ? { title } : { title, type, member_ids: members });
      onSaved(value); onClose();
    } catch (error) { setError(failure(error)); } finally { setBusy(false); }
  }
  async function member(agent: Agent, joined: boolean) {
    if (!current || busy) return;
    setBusy(true); setError('');
    try {
      const value = await api<Conversation>(`/conversations/${current.id}/members/${agent.id}`, 'PATCH', { joined });
      setCurrent(value); onSaved(value);
    } catch (error) { setError(failure(error)); } finally { setBusy(false); }
  }
  return <dialog ref={dialog} aria-labelledby="conversation-editor-title" onCancel={event => { event.preventDefault(); if (!busy) onClose(); }}>
    <form onSubmit={save}><header><h2 id="conversation-editor-title">{current ? '管理会话' : '新建群聊'}</h2><button type="button" disabled={busy} onClick={onClose}>关闭</button></header>
      <label>会话名称<input autoFocus required maxLength={120} value={title} onChange={event => setTitle(event.target.value)} /></label>
      {!current && <label>会话类型<select value={type} onChange={event => setType(event.target.value as 'board' | 'project')}><option value="board">董事会</option><option value="project">项目群</option></select></label>}
      <fieldset disabled={busy || current?.archived || current?.type === 'dm'} className="member-choices"><legend>Agent 成员{current ? '（勾选即时保存）' : '（至少一位）'}</legend>
        {agents.map(agent => <label className="check" key={agent.id}><input type="checkbox" checked={current ? current.member_ids.includes(agent.id) : members.includes(agent.id)} disabled={!agent.enabled && !(current?.member_ids.includes(agent.id) || members.includes(agent.id))} onChange={event => current ? void member(agent, event.target.checked) : setMembers(event.target.checked ? [...members, agent.id] : members.filter(id => id !== agent.id))} />{agent.name}{!agent.enabled && ' · 已停用'}</label>)}
      </fieldset>
      {current?.type === 'dm' && <p className="muted">私聊成员固定，身份配置可在 Agent 视角中编辑。</p>}
      {current?.archived && <p className="muted">恢复会话后可以调整成员。</p>}
      {error && <p className="error" role="alert">{error}</p>}
      <footer><button type="button" disabled={busy} onClick={onClose}>取消</button><button className="primary" disabled={busy || (!current && !members.length)}>{busy ? '保存中…' : current ? '保存名称' : '创建群聊'}</button></footer>
    </form>
  </dialog>;
}

export function ConversationMessages({ conversation, agents, draft, onMessage }: {
  conversation: Conversation; agents: Agent[]; draft: Draft; onMessage: (message: Message) => void;
}) {
  const [messages, setMessages] = useState<Message[]>([]);
  const [content, setContent] = useState(draft.content);
  const [loading, setLoading] = useState(true);
  const [sending, setSending] = useState(false);
  const [loadError, setLoadError] = useState('');
  const [sendError, setSendError] = useState('');
  const [sent, setSent] = useState(false);
  const [more, setMore] = useState(false);
  const cursor = useRef(0);
  const active = useRef(true);
  const reading = useRef(false);
  const posting = useRef(false);
  const composing = useRef(false);
  const input = useRef<HTMLTextAreaElement>(null);
  useEffect(() => { if (sent && !sending) input.current?.focus(); }, [sent, sending]);
  function merge(incoming: Message[]) {
    setMessages(current => [...new Map([...current, ...incoming].map(message => [message.id, message])).values()].sort((a, b) => a.sequence - b.sequence));
  }
  async function load() {
    if (reading.current) return;
    reading.current = true; setLoading(true); setLoadError('');
    try {
      const rows = await api<Message[]>(`/conversations/${conversation.id}/messages?after=${cursor.current}&limit=50`);
      if (!active.current) return;
      merge(rows); cursor.current = rows.at(-1)?.sequence ?? cursor.current; setMore(rows.length === 50);
    } catch (error) { if (active.current) setLoadError(failure(error)); }
    finally { reading.current = false; if (active.current) setLoading(false); }
  }
  useEffect(() => { active.current = true; void load(); return () => { active.current = false; }; }, []);
  async function send() {
    const text = content.trim();
    if (!text || posting.current || conversation.archived) return;
    posting.current = true; setSending(true); setSendError(''); setSent(false);
    if (!draft.request_id || draft.sentContent !== text) { draft.request_id = crypto.randomUUID(); draft.sentContent = text; }
    try {
      const message = await api<Message>(`/conversations/${conversation.id}/messages`, 'POST', { content: text, request_id: draft.request_id });
      // Keep an unmounted sender's key: returning to its draft can safely retry a lost response.
      if (active.current) { draft.content = ''; draft.request_id = ''; draft.sentContent = ''; }
      onMessage(message);
      if (active.current) { merge([message]); setContent(''); setSent(true); }
    } catch (error) { if (active.current) setSendError(failure(error)); }
    finally { posting.current = false; if (active.current) setSending(false); }
  }
  return <>
    <div className="message-history" aria-label="消息历史" aria-busy={loading}>
      {messages.length === 0 && <p className="muted">{loading ? '正在读取消息…' : '暂无消息，发送第一条消息开始讨论。'}</p>}
      {messages.map(message => <article className={`message ${message.sender_kind}`} key={message.id}><header><strong>{message.sender_kind === 'owner' ? '你 · Owner' : agents.find(agent => agent.id === message.sender_id)?.name ?? 'Agent'}</strong><time dateTime={message.created_at}>{new Date(message.created_at).toLocaleString()}</time></header><p>{message.content}</p></article>)}
      {loadError && <p className="error" role="alert">消息读取失败：{loadError}</p>}
      <button className="history-more" disabled={loading} onClick={() => void load()}>{loading ? '读取中…' : loadError ? '重试读取消息' : more ? '继续加载历史（按时间正序）' : '刷新消息'}</button>
    </div>
    <form className="composer" onSubmit={event => { event.preventDefault(); void send(); }}>
      <p className="muted">模型回复尚未接入。发送仅保存 Owner 消息，不会启动 Agent 执行。</p>
      {conversation.archived && <p className="archive-notice">此会话已归档，恢复后可继续发送。</p>}
      <label className="sr-only" htmlFor="message-content">消息内容</label>
      <textarea ref={input} id="message-content" placeholder="向会话发送消息…" maxLength={16000} rows={3} value={content} disabled={sending || conversation.archived} onChange={event => { draft.content = event.target.value; setContent(event.target.value); }} onCompositionStart={() => { composing.current = true; }} onCompositionEnd={() => { composing.current = false; }} onKeyDown={event => { if (event.key === 'Enter' && !event.shiftKey && !composing.current && !event.nativeEvent.isComposing && event.keyCode !== 229) { event.preventDefault(); void send(); } }} />
      {sendError && <p className="error" role="alert">发送失败：{sendError}。草稿已保留，原内容重试不会重复保存。</p>}
      <span className="sr-only" role="status">{sent ? '消息已保存' : ''}</span>
      <footer><small>Enter 发送 · Shift+Enter 换行</small><button className="primary" disabled={sending || conversation.archived || !content.trim()}>{sending ? '发送中…' : sendError ? '重试发送' : '发送'}</button></footer>
    </form>
  </>;
}
