import { describe, expect, it } from 'vitest';
import { debugSample } from './debugSample';
describe('调试取样保持实际范围', () => {
  it('优先停在完整行，避免把卷首取样误当模型截断', () => {
    const source = '卷一\n' + '天地玄黄宇宙洪荒\n'.repeat(120);
    const sample = debugSample(source);
    expect(sample.text.endsWith('\n')).toBe(true);
    expect(Array.from(sample.text).length).toBeLessThanOrEqual(800);
    expect(source.startsWith(sample.text)).toBe(true);
    expect(sample.truncated).toBe(true); expect(sample.completeLine).toBe(true);
  });
  it('超长行保留 Unicode 字符，不默默宣称完整', () => {
    const sample = debugSample('𠀀'.repeat(900));
    expect(Array.from(sample.text).length).toBe(800);
    expect(sample.text).toBe('𠀀'.repeat(800));
    expect(sample.completeLine).toBe(false);
  });
  it('短文本保持首尾空白和原字符', () => {
    const source = '　卷五\n天地\n ';
    expect(debugSample(source)).toEqual({ text: source, truncated: false, completeLine: true });
  });
});
