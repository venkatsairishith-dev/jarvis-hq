// The host bridge static/app/app-react-chat-window/jarvis-task-input.js (plain script, loaded by index.html and chat.html):
// polling, mapping to the taskPrompt prop, answer / cancel requests through the guarded card-action route, CSRF retry,
// offline honesty, and that answer values never stay in the module's state.
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { chatWindowPropsSchema } from './message-schema';

// The script lives outside this package (Vite's fs.allow blocks ?raw imports from there): read it with Node at test time.
// A non-literal module name keeps tsc from needing Node's type declarations, which this package does not install.
const FS_MODULE = 'node:fs';
const { readFileSync } = (await import(/* @vite-ignore */ FS_MODULE)) as { readFileSync: (p: string, enc: string) => string };
const cwd = (globalThis as unknown as { process: { cwd: () => string } }).process.cwd();   // vitest runs in this package
const SCRIPT = readFileSync(`${cwd}/../../static/app/app-react-chat-window/jarvis-task-input.js`, 'utf8');
const FAKE = 'gk-' + 'Zq9'.repeat(12);

const ROW = {
  run_id: 'r1', task: 'configure the provider',
  request: { id: 'q-abc123', step: 's1', title: 'Gemini API key', prompt: 'Paste the key', status: 'open',
             fields: [{ name: 'api_key', kind: 'secret', label: 'API key', required: true, max_len: 4096 },
                      { name: 'role', kind: 'choice', label: 'Role', required: true,
                        options: [{ value: 'primary', label: 'Primary' }, { value: 'fallback', label: 'Fallback' }] }] },
};

type Call = { url: string; body: Record<string, unknown>; headers: Record<string, string> };
type W = Window & Record<string, any>;

function load(responder: (url: string, body: Record<string, unknown>) => { status: number; json: unknown }) {
  const w = window as W;
  const calls: Call[] = [];
  w.__appReactChatWindowParts = { renderWindow: vi.fn() };
  w.lanlan_config = { lanlan_name: 'Neko' };
  w.nekoLocalMutationSecurity = { getMutationHeaders: vi.fn(async () => ({ 'X-CSRF-Token': 'tok' })), refreshToken: vi.fn(async () => {}) };
  w.showStatusToast = vi.fn();
  w.fetch = vi.fn(async (url: string, init: RequestInit) => {
    const body = JSON.parse(String(init.body));
    calls.push({ url, body, headers: init.headers as Record<string, string> });
    const r = responder(url, body);
    return { ok: r.status < 400, status: r.status, json: async () => r.json } as Response;
  }) as unknown as typeof fetch;
  // eslint-disable-next-line no-new-func
  new Function(SCRIPT)();
  return { w, calls, I: w.__appReactChatWindowParts, hooks: w.__appReactChatWindowParts.__jarvisTaskInputTestHooks };
}

describe('jarvis-task-input host bridge', () => {
  beforeEach(() => { vi.useFakeTimers(); delete (window as W).__NEKO_MULTI_WINDOW__; });
  afterEach(() => { vi.useRealTimers(); });

  it('polls pending_inputs through the guarded card route and maps the newest question to a valid prop', async () => {
    const { I, calls } = load(() => ({ status: 200, json: { result: { count: 1, requests: [ROW] } } }));
    await vi.runOnlyPendingTimersAsync();
    expect(calls[0].url).toBe('/api/plugin-cards/main_agent/action/pending_inputs');
    expect(calls[0].headers['X-CSRF-Token']).toBe('tok');
    expect(calls[0].body).toMatchObject({ card_id: 'jarvis-task-input', target_lanlan: 'Neko', args: {} });
    const prompt = I.getJarvisTaskPrompt();
    expect(prompt).toMatchObject({ requestId: 'q-abc123', runId: 'r1', status: 'open', count: 1 });
    expect(prompt.fields[0]).toMatchObject({ name: 'api_key', kind: 'secret', maxLen: 4096 });
    expect(chatWindowPropsSchema.parse({ taskPrompt: prompt }).taskPrompt?.requestId).toBe('q-abc123');
    expect(I.renderWindow).toHaveBeenCalled();
  });

  it('sends an answer for the right run and request, and keeps no value afterwards', async () => {
    const { I, calls, hooks } = load((url) => url.endsWith('answer_input')
      ? { status: 200, json: { result: { state: 'answered', message: 'Answer received' } } }
      : { status: 200, json: { result: { count: 1, requests: [ROW] } } });
    await vi.runOnlyPendingTimersAsync();
    await I.handleTaskPromptSubmit('q-abc123', { api_key: FAKE, role: 'primary' });
    const answer = calls.find(c => c.url.endsWith('answer_input'))!;
    expect(answer.body.args).toEqual({ run_id: 'r1', request_id: 'q-abc123', values: { api_key: FAKE, role: 'primary' } });
    expect(JSON.stringify(hooks.state)).not.toContain(FAKE);
    expect(JSON.stringify((window as W).showStatusToast.mock.calls)).not.toContain(FAKE);
    expect(I.getJarvisTaskPrompt()).toBeNull();                       // gone until the backend says otherwise
  });

  it('shows the backend refusal on the card and keeps the question', async () => {
    const { I } = load((url) => url.endsWith('answer_input')
      ? { status: 400, json: { detail: { message: 'That question expired. Resume the task to be asked again.' } } }
      : { status: 200, json: { result: { count: 1, requests: [ROW] } } });
    await vi.runOnlyPendingTimersAsync();
    await I.handleTaskPromptSubmit('q-abc123', { api_key: FAKE, role: 'primary' });
    const prompt = I.getJarvisTaskPrompt();
    expect(prompt.status).toBe('error');
    expect(prompt.error).toContain('expired');
    expect(JSON.stringify(prompt)).not.toContain(FAKE);
  });

  it('refreshes the CSRF token once and retries', async () => {
    let first = true;
    const { calls, w } = load(() => {
      if (first) { first = false; return { status: 403, json: { error_code: 'csrf_validation_failed' } }; }
      return { status: 200, json: { result: { count: 0, requests: [] } } };
    });
    await vi.advanceTimersByTimeAsync(0);
    expect(w['nekoLocalMutationSecurity']!.refreshToken).toHaveBeenCalledTimes(1);   // once: no retry loop
    expect(calls[0].url).toBe(calls[1].url);
    expect(calls[1].headers['X-CSRF-Token']).toBe('tok');
  });

  it('says "offline" instead of showing old data as live, and backs off', async () => {
    let up = true;
    const { I } = load(() => (up ? { status: 200, json: { result: { count: 1, requests: [ROW] } } } : { status: 502, json: {} }));
    await vi.runOnlyPendingTimersAsync();
    up = false;
    await vi.advanceTimersByTimeAsync(5000);
    expect(I.getJarvisTaskPrompt().status).toBe('offline');
  });

  it('cancel and collapse go to the same request', async () => {
    const { I, calls } = load((url) => url.endsWith('cancel_input')
      ? { status: 200, json: { result: { state: 'cancelled' } } }
      : { status: 200, json: { result: { count: 1, requests: [ROW] } } });
    await vi.runOnlyPendingTimersAsync();
    I.handleTaskPromptCollapse('q-abc123', true);
    expect(I.getJarvisTaskPrompt().collapsed).toBe(true);
    await I.handleTaskPromptCancel('q-abc123');
    expect(calls.find(c => c.url.endsWith('cancel_input'))!.body.args).toEqual({ run_id: 'r1', request_id: 'q-abc123' });
  });

  it('draws nothing in the Electron pet window (multi-window, non-chat path)', async () => {
    (window as W).__NEKO_MULTI_WINDOW__ = true;
    const { I, calls } = load(() => ({ status: 200, json: { result: { count: 1, requests: [ROW] } } }));
    await vi.runOnlyPendingTimersAsync();
    expect(calls.length).toBe(0);
    expect(I.getJarvisTaskPrompt()).toBeNull();
  });
});
