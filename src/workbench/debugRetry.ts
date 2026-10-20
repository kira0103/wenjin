import type { DebugInput, DebugRun } from './debugApi';

// Carry an explanation only when the user reruns the same sample.
export function debugRetryInput(input: DebugInput, result: DebugRun | null): DebugInput {
  if (result?.status !== 'failed' || !result.retry_feedback ||
      result.input_text !== input.input_text || result.title !== input.title) return input;
  return { ...input, validation_feedback: result.retry_feedback };
}
