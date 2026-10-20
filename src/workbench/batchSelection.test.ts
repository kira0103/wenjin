import { expect, it } from 'vitest';
import { nextBatchIds } from './batchSelection';

it('changes 20 to 10 in source order while skipping completed and edited volumes', () => {
  const choices = Array.from({ length: 367 }, (_, i) => ({ id: String(i), processed: i === 1 || i === 2, manual_modified: i === 0 }));
  expect(nextBatchIds(choices, '20')).toHaveLength(20);
  expect(nextBatchIds(choices, '10')).toEqual(Array.from({ length: 10 }, (_, i) => String(i + 3)));
  expect(nextBatchIds(choices, '3')).toEqual(['3', '4', '5']);
  expect(nextBatchIds(choices, '')).toBeNull();
  expect(nextBatchIds(choices, '5001')).toBeNull();
});
