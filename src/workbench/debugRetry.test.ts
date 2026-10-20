import { describe, expect, it } from 'vitest';
import type { DebugInput, DebugRun } from './debugApi';
import { debugRetryInput } from './debugRetry';

const input: DebugInput = { config: { system_prompt: '只加标点', max_tokens: 8192, temperature: 0.2, thinking: 'disabled' }, input_text: '春秋説题', title: '卷首', lead: '' };
const result = { status: 'failed', input_text: input.input_text, title: input.title, retry_feedback: '保留説，不要改成说' } as DebugRun;

describe('manual debug retry', () => {
  it('includes exact feedback on the same sample without altering the editor draft', () => {
    expect(debugRetryInput(input, result).validation_feedback).toBe(result.retry_feedback);
    expect(input.validation_feedback).toBeUndefined();
  });
  it('does not reuse a failure from another sample or a non-fidelity result', () => {
    const edited = { ...input, input_text: '新样本' };
    expect(debugRetryInput(edited, result)).toBe(edited);
    expect(debugRetryInput(input, { ...result, title: '另一卷' })).toBe(input);
    expect(debugRetryInput(input, { ...result, status: 'running' })).toBe(input);
    expect(debugRetryInput(input, { ...result, retry_feedback: '' })).toBe(input);
  });
});
