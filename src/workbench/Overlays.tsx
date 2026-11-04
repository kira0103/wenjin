import { useEffect, useRef, useState, type CSSProperties, type ReactNode } from 'react';
import { createPortal } from 'react-dom';
import { X, Check } from 'lucide-react';
import { Modal, useBodyScrollLock } from './Pages';
import {isAppDialogOpen} from './dialogQueue';

const FOCUSABLE = 'button:not([disabled]), [href], input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])';

/** 抽屉/弹窗共用：Esc 关闭、Tab 焦点圈、打开前记录焦点、关闭后恢复。背景滚动锁由 useBodyScrollLock 统一处理。 */
function useDialogFocus(onClose: () => void, panel: React.RefObject<HTMLElement | null>) {
  const closeRef = useRef(onClose);
  closeRef.current = onClose;
  useEffect(() => {
    const previous = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    const focusables = () => panel.current ? Array.from(panel.current.querySelectorAll<HTMLElement>(FOCUSABLE)).filter(item => item.offsetParent !== null) : [];
    const frame = requestAnimationFrame(() => { (focusables()[0] ?? panel.current)?.focus(); });
    const onKey = (event: KeyboardEvent) => {
      if(isAppDialogOpen())return;
      if (event.key === 'Escape') { event.preventDefault(); event.stopPropagation(); closeRef.current(); return; }
      if (event.key !== 'Tab') return;
      const items = focusables();
      if (!items.length) return;
      const first = items[0], last = items[items.length - 1];
      if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus(); }
      else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
    };
    document.addEventListener('keydown', onKey, true);
    return () => {
      cancelAnimationFrame(frame);
      document.removeEventListener('keydown', onKey, true);
      previous?.focus();
    };
  }, [panel]);
}

/**
 * 右侧抽屉：查看/轻编辑“当前对象”的细节，不离开当前页。
 * 用于材料信息、引用详情、执行记录、项目关联等；多步流程请用 Wizard，确认操作请用 Modal。
 */
export function Drawer({ title, onClose, children, footer, width = 440 }: { title: string; onClose(): void; children: ReactNode; footer?: ReactNode; width?: number }) {
  const panel = useRef<HTMLElement | null>(null);
  useDialogFocus(onClose, panel);
  useBodyScrollLock();
  const style = { '--drawer-width': `${width}px` } as CSSProperties;
  const portal = document.querySelector('.portal-workspace,.portal-admin') ? ' portal-modal' : '';
  return createPortal(
    <div className={`drawer-overlay${portal}`} onMouseDown={event => { if (event.target === event.currentTarget) onClose(); }}>
      <aside ref={panel} className="drawer-panel" style={style} role="dialog" aria-modal="true" aria-label={title} tabIndex={-1}>
        <div className="drawer-heading"><h2>{title}</h2><button type="button" className="icon-btn" aria-label="关闭抽屉" onClick={onClose}><X size={18} /></button></div>
        <div className="drawer-body">{children}</div>
        {footer && <div className="drawer-footer">{footer}</div>}
      </aside>
    </div>,
    document.body,
  );
}

export type WizardStep = { label: string; content: ReactNode; nextLabel?: string; nextDisabled?: boolean };

/**
 * 分步向导：3–5 步且步骤间有依赖的任务（新建工作单元、导入文献、首次检索配置、新建投稿）。
 * 复用 Modal 的焦点管理与样式；步骤状态在组件内部维护。
 */
export function Wizard({ title, steps, onClose, onFinish, finishLabel = '完成', busy = false, error = '' }: { title: string; steps: WizardStep[]; onClose(): void; onFinish(): void; finishLabel?: string; busy?: boolean; error?: string }) {
  const [index, setIndex] = useState(0);
  const bodyRef = useRef<HTMLDivElement>(null);
  const step = steps[index];
  const last = index === steps.length - 1;
  useEffect(() => {
    const frame = requestAnimationFrame(() => { bodyRef.current?.querySelector<HTMLElement>('input:not([disabled]),select:not([disabled]),textarea:not([disabled]),button:not([disabled])')?.focus(); });
    return () => cancelAnimationFrame(frame);
  }, [index]);
  return <Modal title={title} onClose={() => { if (!busy) onClose(); }}>
    <div className="wizard-progress" role="progressbar" aria-label="步骤进度" aria-valuemin={1} aria-valuemax={steps.length} aria-valuenow={index + 1}><i style={{ width: `${((index + 1) / steps.length) * 100}%` }} /></div>
    <ol className="wizard-steps">{steps.map((item, stepIndex) => <li key={item.label} className={`wizard-step ${stepIndex === index ? 'is-active' : ''} ${stepIndex < index ? 'is-done' : ''}`} aria-current={stepIndex === index ? 'step' : undefined}><span>{stepIndex < index ? <Check size={16}/> : stepIndex + 1}</span>{item.label}</li>)}</ol>
    <div className="wizard-body" ref={bodyRef}>{step.content}</div>
    {error && <p className="inline-error" role="alert">{error}</p>}
    <div className="modal-actions">
      <button type="button" className="button-secondary" disabled={busy} onClick={onClose}>取消</button>
      {index > 0 && <button type="button" className="button-secondary" disabled={busy} onClick={() => setIndex(value => value - 1)}>上一步</button>}
      <button type="button" className="button-primary" disabled={busy || step.nextDisabled} onClick={() => last ? onFinish() : setIndex(value => value + 1)}>{busy ? '处理中…' : last ? finishLabel : step.nextLabel ?? '下一步'}</button>
    </div>
  </Modal>;
}
