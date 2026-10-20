import { describe, expect, it } from 'vitest';
import { insertedPunctuation, paragraphCorrespondence } from './VolumeCompare';
describe('current-volume comparison', () => {
  it('aligns unchanged paragraph boundaries after punctuation insertion', () => {
    expect(paragraphCorrespondence('卷五\n天地玄黄\n\n日月', '卷五\n天地，玄黄。\n\n日月。')).toBe(true);
    expect(paragraphCorrespondence('卷五\n天地玄黄', '卷五\n天地\n玄黄。')).toBe(false);
  });
  it('does not call manually rewritten characters a punctuation-only result', () => {
    expect(insertedPunctuation('天地', '天大地。')).toBeNull();
    expect(insertedPunctuation('天地\n', '天地。')).toBeNull();
    expect(paragraphCorrespondence('天地', '天大地。')).toBe(false);
  });
  it('tracks added marks without consuming original punctuation or non-BMP glyphs', () => {
    const spans = insertedPunctuation('𠀀天。地\n', '𠀀，天。地！\n');
    expect(spans?.filter(s => s.inserted).map(s => s.text).join('')).toBe('，！');
    expect(spans?.filter(s => !s.inserted).map(s => s.text).join('')).toBe('𠀀天。地\n');
  });
});
