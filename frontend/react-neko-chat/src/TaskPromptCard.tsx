import { useEffect, useRef, useState } from 'react';
import type { FormEvent } from 'react';
import { i18n } from './i18n';
import type { TaskPrompt, TaskPromptField, TaskPromptValues } from './message-schema';

// A held JARVIS task asks the owner something (pending-input contract, plugin/plugins/main_agent/input_request.py).
// Rendered above the composer; never takes focus by itself (the owner may be typing elsewhere); collapsing keeps the
// question and the typed values. Secret fields are password inputs, never echoed, and cleared after every submission.

type Props = {
  prompt: TaskPrompt;
  onSubmit?: (requestId: string, values: TaskPromptValues) => void;
  onCancel?: (requestId: string) => void;
  onCollapse?: (requestId: string, collapsed: boolean) => void;
};

type Draft = Record<string, string | boolean | string[] | undefined>;

function missingRequired(field: TaskPromptField, value: Draft[string]): boolean {
  if (field.required === false) return false;
  if (field.kind === 'bool') return typeof value !== 'boolean';
  if (field.kind === 'multi') return !Array.isArray(value) || value.length === 0;
  return typeof value !== 'string' || !value.trim();
}

export default function TaskPromptCard({ prompt, onSubmit, onCancel, onCollapse }: Props) {
  const [draft, setDraft] = useState<Draft>({});
  const [localError, setLocalError] = useState('');
  const [confirmCancel, setConfirmCancel] = useState(false);
  const requestRef = useRef(prompt.requestId);

  // A new question replaces the old one: its draft (and any secret typed for it) is dropped.
  useEffect(() => {
    if (requestRef.current !== prompt.requestId) {
      requestRef.current = prompt.requestId;
      setDraft({});
      setLocalError('');
      setConfirmCancel(false);
    }
  }, [prompt.requestId]);

  const submitting = prompt.status === 'submitting';
  const collapsed = !!prompt.collapsed;
  const setField = (name: string, value: Draft[string]) => setDraft(prev => ({ ...prev, [name]: value }));

  const submit = (event: FormEvent) => {
    event.preventDefault();
    if (submitting) return;
    const missing = prompt.fields.filter(f => missingRequired(f, draft[f.name]));
    if (missing.length) {
      setLocalError(i18n('chat.taskPromptRequired', 'Please fill in: {{fields}}', { fields: missing.map(f => f.label).join(', ') }));
      return;
    }
    const values: TaskPromptValues = {};
    for (const f of prompt.fields) {
      const v = draft[f.name];
      if (v === undefined || (typeof v === 'string' && !v.trim()) || (Array.isArray(v) && !v.length)) continue;
      values[f.name] = typeof v === 'string' ? v.trim() : v;
    }
    setLocalError('');
    onSubmit?.(prompt.requestId, values);
    // secrets leave the component the moment they are sent; a refused answer asks for them again
    setDraft(prev => {
      const next = { ...prev };
      for (const f of prompt.fields) if (f.kind === 'secret') delete next[f.name];
      return next;
    });
  };

  const title = prompt.title || i18n('chat.taskPromptTitle', 'JARVIS needs your input');
  const countBadge = prompt.count && prompt.count > 1
    ? i18n('chat.taskPromptMore', '+{{n}} more waiting', { n: String(prompt.count - 1) }) : '';

  if (collapsed) {
    return (
      <div className="composer-task-prompt is-collapsed" role="status" data-task-prompt-request={prompt.requestId}>
        <span className="composer-task-prompt-dot" aria-hidden="true" />
        <span className="composer-task-prompt-collapsed-label">{title}</span>
        {countBadge ? <span className="composer-task-prompt-count">{countBadge}</span> : null}
        <button type="button" className="composer-task-prompt-btn is-secondary"
          onClick={() => onCollapse?.(prompt.requestId, false)}
          title={i18n('chat.taskPromptExpandAriaLabel', 'Show the waiting question')}>
          {i18n('chat.taskPromptExpand', 'Answer')}
        </button>
      </div>
    );
  }

  const fieldId = (name: string) => `task-prompt-${prompt.requestId}-${name}`;
  const shownError = localError || (prompt.status === 'error' ? prompt.error || '' : '');

  return (
    <form className={`composer-task-prompt is-${prompt.status ?? 'open'}`} onSubmit={submit} noValidate
      aria-label={title} data-task-prompt-request={prompt.requestId}>
      <div className="composer-task-prompt-head">
        <span className="composer-task-prompt-dot" aria-hidden="true" />
        <strong className="composer-task-prompt-title">{title}</strong>
        {countBadge ? <span className="composer-task-prompt-count">{countBadge}</span> : null}
        <button type="button" className="composer-task-prompt-btn is-ghost" onClick={() => onCollapse?.(prompt.requestId, true)}
          title={i18n('chat.taskPromptCollapseAriaLabel', 'Hide the question for now (it keeps waiting)')}>
          {i18n('chat.taskPromptLater', 'Later')}
        </button>
      </div>
      <p className="composer-task-prompt-text">{prompt.prompt}</p>
      {prompt.task ? <p className="composer-task-prompt-task">{i18n('chat.taskPromptForTask', 'For: {{task}}', { task: prompt.task })}</p> : null}
      {prompt.status === 'offline' ? (
        <p className="composer-task-prompt-note" role="status">
          {i18n('chat.taskPromptOffline', 'Not connected: this question may be out of date.')}
        </p>
      ) : null}
      <div className="composer-task-prompt-fields">
        {prompt.fields.map(field => {
          const id = fieldId(field.name);
          const value = draft[field.name];
          if (field.kind === 'text' || field.kind === 'secret') {
            return (
              <label key={field.name} className="composer-task-prompt-field" htmlFor={id}>
                <span className="composer-task-prompt-label">{field.label}{field.required === false ? '' : ' *'}</span>
                <input id={id} name={field.name} type={field.kind === 'secret' ? 'password' : 'text'}
                  className="composer-task-prompt-input" maxLength={field.maxLen} disabled={submitting}
                  autoComplete={field.kind === 'secret' ? 'off' : undefined} spellCheck={field.kind === 'secret' ? false : undefined}
                  value={typeof value === 'string' ? value : ''} onChange={e => setField(field.name, e.target.value)} />
                {field.help ? <span className="composer-task-prompt-help">{field.help}</span> : null}
              </label>
            );
          }
          if (field.kind === 'bool') {
            return (
              <fieldset key={field.name} className="composer-task-prompt-field" disabled={submitting}>
                <legend className="composer-task-prompt-label">{field.label}</legend>
                <div className="composer-task-prompt-options" role="radiogroup">
                  {[true, false].map(v => (
                    <label key={String(v)} className={`composer-task-prompt-option${value === v ? ' is-selected' : ''}`}>
                      <input type="radio" name={id} checked={value === v} onChange={() => setField(field.name, v)} />
                      <span>{v ? i18n('chat.taskPromptYes', 'Yes') : i18n('chat.taskPromptNo', 'No')}</span>
                    </label>
                  ))}
                </div>
              </fieldset>
            );
          }
          const multi = field.kind === 'multi';
          const picked = multi ? (Array.isArray(value) ? value : []) : value;
          return (
            <fieldset key={field.name} className="composer-task-prompt-field" disabled={submitting}>
              <legend className="composer-task-prompt-label">{field.label}</legend>
              <div className="composer-task-prompt-options" role={multi ? 'group' : 'radiogroup'}>
                {(field.options || []).map(o => {
                  const on = multi ? (picked as string[]).includes(o.value) : picked === o.value;
                  return (
                    <label key={o.value} className={`composer-task-prompt-option${on ? ' is-selected' : ''}`}>
                      <input type={multi ? 'checkbox' : 'radio'} name={id} value={o.value} checked={on}
                        onChange={() => setField(field.name, multi
                          ? (on ? (picked as string[]).filter(x => x !== o.value) : [...(picked as string[]), o.value])
                          : o.value)} />
                      <span>{o.label}</span>
                    </label>
                  );
                })}
              </div>
            </fieldset>
          );
        })}
      </div>
      {shownError ? <p className="composer-task-prompt-error" role="alert">{shownError}</p> : null}
      <div className="composer-task-prompt-actions">
        <button type="submit" className="composer-task-prompt-btn is-primary" disabled={submitting}>
          {submitting ? i18n('chat.taskPromptSending', 'Sending…') : i18n('chat.taskPromptSubmit', 'Submit')}
        </button>
        {confirmCancel ? (
          <>
            <span className="composer-task-prompt-confirm">{i18n('chat.taskPromptCancelConfirm', 'Stop that task?')}</span>
            <button type="button" className="composer-task-prompt-btn is-danger" disabled={submitting}
              onClick={() => { setConfirmCancel(false); onCancel?.(prompt.requestId); }}>
              {i18n('chat.taskPromptCancelYes', 'Yes, cancel it')}
            </button>
            <button type="button" className="composer-task-prompt-btn is-ghost" onClick={() => setConfirmCancel(false)}>
              {i18n('chat.taskPromptCancelNo', 'Keep it')}
            </button>
          </>
        ) : (
          <button type="button" className="composer-task-prompt-btn is-secondary" disabled={submitting}
            onClick={() => setConfirmCancel(true)}>
            {i18n('chat.taskPromptCancel', 'Cancel question')}
          </button>
        )}
      </div>
    </form>
  );
}
