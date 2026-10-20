import { useEffect, useRef, useState, type RefObject } from 'react';
import { batchApi, type Baseline, type TextComparison, type DiffSpan } from './batchApi';
const marks = /[。，、；：？！“”‘’《》〈〉·—…,.!?;:"'（）()]/g;
const normalize = (text: string) => text.replace(marks, '');
export function paragraphCorrespondence(baseline: string, content: string) {
  const a = baseline.split('\n'), b = content.split('\n');
  return a.length === b.length && a.every((line, i) => normalize(line) === normalize(b[i]));
}
export function insertedPunctuation(baseline: string, content: string): { text: string; inserted: boolean }[] | null {
  const source = Array.from(baseline), result = Array.from(content), spans: { text: string; inserted: boolean }[] = [];
  let i = 0;
  for (const char of result) {
    const inserted = source[i] !== char;
    if (!inserted) i++;
    else if (!/^[。，、；：？！“”‘’《》〈〉·—…,.!?;:"'（）()]$/.test(char)) return null;
    const last = spans[spans.length - 1];
    if (last?.inserted === inserted) last.text += char;
    else spans.push({ text: char, inserted });
  }
  return i === source.length ? spans : null;
}
export function VolumeBaseline({ baseline, content, mode, editorRef }: { baseline: Baseline | null; content: string; mode: 'baseline' | 'original'; editorRef: RefObject<HTMLTextAreaElement> }) {
  const reader = useRef<HTMLDivElement>(null), mirror = useRef<HTMLDivElement>(null), syncing = useRef(false);
  const aligned = !!baseline && mode === 'baseline' && paragraphCorrespondence(baseline.baseline, content);
  useEffect(() => {
    const editor = editorRef.current, left = reader.current, right = mirror.current;
    if (!editor || !left || !right || !aligned) return;
    const measure = () => {
      const css = getComputedStyle(editor);
      Object.assign(right.style, { width: `${editor.clientWidth}px`, font: css.font, lineHeight: css.lineHeight, padding: css.padding, letterSpacing: css.letterSpacing, wordBreak: css.wordBreak, overflowWrap: css.overflowWrap });
    };
    const observer = new ResizeObserver(measure); observer.observe(editor); measure();
    const synchronize = (from: HTMLElement, to: HTMLElement, anchors: HTMLElement, targetAnchors: HTMLElement) => {
      if (syncing.current) return;
      const paragraphs = Array.from(anchors.querySelectorAll<HTMLElement>('[data-paragraph]'));
      let index = 0;
      for (let i = 0; i < paragraphs.length; i++) { if (paragraphs[i].getBoundingClientRect().top - anchors.getBoundingClientRect().top + anchors.scrollTop <= from.scrollTop + 20) index = i; else break; }
      const target = targetAnchors.querySelector<HTMLElement>(`[data-paragraph="${index}"]`);
      if (!target) return;
      syncing.current = true;
      to.scrollTop = Math.max(0, target.getBoundingClientRect().top - targetAnchors.getBoundingClientRect().top + targetAnchors.scrollTop - 16);
      requestAnimationFrame(() => { syncing.current = false; });
    };
    const onLeft = () => synchronize(left, editor, left, right);
    const onRight = () => synchronize(editor, left, right, left);
    left.addEventListener('scroll', onLeft); editor.addEventListener('scroll', onRight);
    return () => { observer.disconnect(); left.removeEventListener('scroll', onLeft); editor.removeEventListener('scroll', onRight); };
  }, [aligned, content, baseline?.baseline, editorRef]);
  if (!baseline) return <div className="page-loading">正在读取本卷底文…</div>;
  const text = mode === 'baseline' ? baseline.baseline : baseline.original;
  return <div className="volume-baseline"><header><strong>{baseline.title} · {mode === 'baseline' ? '本卷清洗底文' : '本卷原始录文'}</strong><small>{aligned ? '按段落同步定位；本卷底文保持不变' : mode === 'original' ? '清洗前原文，仅本卷范围' : '存在人工正文或段落改动，暂停同步；请结合差异核对'}</small></header>
    <div className="baseline-text" ref={reader} aria-label="本卷对照底文">{text.split('\n').map((line, i) => <div data-paragraph={i} key={i}>{line}</div>)}</div>
    <div className="editor-paragraph-mirror" ref={mirror} aria-hidden="true">{content.split('\n').map((line, i) => <div data-paragraph={i} key={i}>{line}</div>)}</div>
  </div>;
}
export function PunctuationComparison({ baseline, content }: { baseline: string; content: string }) {
  const spans = insertedPunctuation(baseline, content);
  const [diff, setDiff] = useState<{ baseline: string; content: string; value: TextComparison } | null>(null);
  const [error, setError] = useState('');
  useEffect(() => {
    setError('');
    if (insertedPunctuation(baseline, content)) return;
    let cancelled = false;
    const timer = window.setTimeout(() => { void batchApi.compare(baseline, content).then(value => {
      if (!cancelled) setDiff({ baseline, content, value });
    }).catch(e => { if (!cancelled) setError(e.message); }); }, 200);
    return () => { cancelled = true; window.clearTimeout(timer); };
  }, [baseline, content]);
  const value = diff?.baseline === baseline && diff.content === content ? diff.value : null;
  const render = (parts: DiffSpan[]) => parts.map((part, i) => part.kind === 'unchanged' ? <span key={i}>{part.text}</span> :
    <mark key={i} className={`diff-${part.kind}`} data-change-kind={part.kind} title={part.peer ? `对照文字：${part.peer}` : part.kind === 'removed' ? '原文此处文字被删除或移至别处' : part.kind === 'added' ? '新增文字或从别处移入' : '新增标点'}>{part.text}</mark>);
  return <><p className="scope-note">{spans ? '新增标点已高亮；底本文字与空白顺序保留。' : value ? `有 ${value.changed_groups} 处文字差异，待人工复核。重排按原位置删除和目标位置新增显示；空白与换行差异也保留。` : error || '正在计算文字差异…'}</p>
    <div className="diff-legend"><span className="diff-punctuation">新增标点</span><span className="diff-changed">改字</span><span className="diff-added">新增文字</span><span className="diff-removed">删除的原字</span></div>
    <div className="batch-candidate-compare"><div><h3>本卷清洗底文</h3><pre>{value && !spans ? render(value.source_spans) : baseline}</pre></div><div><h3>当前点校稿</h3><pre>{spans ? spans.map((span, i) => span.inserted ? <mark className="diff-punctuation" key={i}>{span.text}</mark> : <span key={i}>{span.text}</span>) : value ? render(value.result_spans) : content}</pre></div></div></>;
}
