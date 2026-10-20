import { expect, test } from 'vitest';
import { sourceRange } from './sourceOffsets';
test('maps JavaScript selection offsets to source Unicode code points', () => {
  expect(sourceRange('甲𠀀乙𠮷丙', '乙𠮷')).toEqual({ start: 2, end: 4 });
  expect(sourceRange('甲𠀀乙')).toEqual({ start: 0, end: 3 });
  expect(sourceRange('甲𠀀乙', 'missing')).toEqual({ start: 0, end: 3 });
});
