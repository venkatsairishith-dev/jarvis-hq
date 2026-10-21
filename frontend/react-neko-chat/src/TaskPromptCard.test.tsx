import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import TaskPromptCard from './TaskPromptCard';
import { chatWindowPropsSchema, type TaskPrompt } from './message-schema';

const providerPrompt: TaskPrompt = {
  requestId: 'q-abc123', runId: 'r1', title: 'Gemini API key', prompt: 'Paste the Gemini API key to test it.',
  task: 'configure the best free provider',
  fields: [
    { name: 'api_key', kind: 'secret', label: 'API key', required: true },
    { name: 'role', kind: 'choice', label: 'Use it as', required: true,
      options: [{ value: 'primary', label: 'Primary' }, { value: 'fallback', label: 'Fallback' }] },
    { name: 'test', kind: 'bool', label: 'Run a connectivity test', required: false },
    { name: 'extras', kind: 'multi', label: 'Also enable', required: false,
      options: [{ value: 'vision', label: 'Vision' }, { value: 'tools', label: 'Tools' }] },
  ],
  status: 'open',
};
const FAKE = 'gk-' + 'Zq9'.repeat(12);

describe('TaskPromptCard', () => {
  it('renders every field kind with native controls and never takes focus by itself', () => {
    render(<TaskPromptCard prompt={providerPrompt} />);
    expect(screen.getByText('Paste the Gemini API key to test it.')).toBeTruthy();
    const key = screen.getByLabelText(/^API key/) as HTMLInputElement;
    expect(key.type).toBe('password');
    expect(key.autocomplete).toBe('off');
    expect(screen.getAllByRole('radio').length).toBe(4);           // 2 choice + yes/no
    expect(screen.getAllByRole('checkbox').length).toBe(2);
    expect(document.activeElement).toBe(document.body);
  });

  it('validates required fields before sending anything', () => {
    const onSubmit = vi.fn();
    render(<TaskPromptCard prompt={providerPrompt} onSubmit={onSubmit} />);
    fireEvent.click(screen.getByText('Submit'));
    expect(onSubmit).not.toHaveBeenCalled();
    expect(screen.getByRole('alert').textContent).toContain('API key');
    expect(screen.getByRole('alert').textContent).toContain('Use it as');
  });

  it('submits typed values for the right request and clears the secret afterwards', () => {
    const onSubmit = vi.fn();
    render(<TaskPromptCard prompt={providerPrompt} onSubmit={onSubmit} />);
    fireEvent.change(screen.getByLabelText(/^API key/), { target: { value: `  ${FAKE}  ` } });
    fireEvent.click(screen.getByLabelText('Fallback'));
    fireEvent.click(screen.getByLabelText('Yes'));
    fireEvent.click(screen.getByLabelText('Tools'));
    fireEvent.click(screen.getByText('Submit'));
    expect(onSubmit).toHaveBeenCalledWith('q-abc123', { api_key: FAKE, role: 'fallback', test: true, extras: ['tools'] });
    expect((screen.getByLabelText(/^API key/) as HTMLInputElement).value).toBe('');
    expect(document.body.innerHTML).not.toContain(FAKE);
  });

  it('collapses without losing typed values, and shows a pending indicator', () => {
    const onCollapse = vi.fn();
    const { rerender } = render(<TaskPromptCard prompt={providerPrompt} onCollapse={onCollapse} />);
    fireEvent.click(screen.getByLabelText('Primary'));
    fireEvent.click(screen.getByText('Later'));
    expect(onCollapse).toHaveBeenCalledWith('q-abc123', true);
    rerender(<TaskPromptCard prompt={{ ...providerPrompt, collapsed: true, count: 2 }} onCollapse={onCollapse} />);
    expect(screen.getByRole('status').textContent).toContain('Gemini API key');
    expect(screen.getByText('+1 more waiting')).toBeTruthy();
    rerender(<TaskPromptCard prompt={{ ...providerPrompt, collapsed: false }} onCollapse={onCollapse} />);
    expect((screen.getByLabelText('Primary') as HTMLInputElement).checked).toBe(true);
  });

  it('asks before cancelling, and a new question drops the old draft', () => {
    const onCancel = vi.fn();
    const { rerender } = render(<TaskPromptCard prompt={providerPrompt} onCancel={onCancel} />);
    fireEvent.click(screen.getByText('Cancel question'));
    expect(onCancel).not.toHaveBeenCalled();
    fireEvent.click(screen.getByText('Yes, cancel it'));
    expect(onCancel).toHaveBeenCalledWith('q-abc123');
    fireEvent.click(screen.getByLabelText('Primary'));
    rerender(<TaskPromptCard prompt={{ ...providerPrompt, requestId: 'q-new000' }} onCancel={onCancel} />);
    expect((screen.getByLabelText('Primary') as HTMLInputElement).checked).toBe(false);
  });

  it('shows the backend refusal and blocks double submission while sending', () => {
    const onSubmit = vi.fn();
    const { rerender } = render(<TaskPromptCard prompt={{ ...providerPrompt, status: 'error', error: 'That question expired.' }} />);
    expect(screen.getByRole('alert').textContent).toBe('That question expired.');
    rerender(<TaskPromptCard prompt={{ ...providerPrompt, status: 'submitting' }} onSubmit={onSubmit} />);
    expect((screen.getByText('Sending…') as HTMLButtonElement).disabled).toBe(true);
    rerender(<TaskPromptCard prompt={{ ...providerPrompt, status: 'offline' }} />);
    expect(screen.getByText(/Not connected/)).toBeTruthy();
  });

  it('is part of the props schema so the host value is not stripped', () => {
    const parsed = chatWindowPropsSchema.parse({ taskPrompt: providerPrompt });
    expect(parsed.taskPrompt?.requestId).toBe('q-abc123');
    expect(() => chatWindowPropsSchema.parse({ taskPrompt: { ...providerPrompt, fields: [] } })).toThrow();
  });
});

describe('TaskPromptCard accessibility', () => {
  it('buttons are named by their visible text (label in name), explanations are tooltips', () => {
    const { rerender } = render(<TaskPromptCard prompt={providerPrompt} />);
    const later = screen.getByRole('button', { name: 'Later' });
    expect(later.getAttribute('title')).toContain('keeps waiting');
    rerender(<TaskPromptCard prompt={{ ...providerPrompt, collapsed: true }} />);
    expect(screen.getByRole('button', { name: 'Answer' }).getAttribute('title')).toContain('waiting question');
  });
});
