import { Check, Copy, Loader2, X, AlertTriangle, ShieldCheck } from 'lucide-react';
import {
  forwardRef, useEffect, useId, useRef, useState,
  type ButtonHTMLAttributes, type InputHTMLAttributes, type ReactNode, type SelectHTMLAttributes, type TextareaHTMLAttributes,
} from 'react';
import { errorMessage } from '../lib/format';

export const cx = (...c: (string | false | null | undefined)[]) => c.filter(Boolean).join(' ');

type Variant = 'primary' | 'secondary' | 'ghost' | 'danger';
const variants: Record<Variant, string> = {
  primary: 'bg-gradient-to-r from-brand-500 to-accent-500 text-white shadow-lg shadow-brand-500/20 hover:brightness-110',
  secondary: 'border border-white/10 bg-ink-800 text-slate-100 hover:bg-ink-700',
  ghost: 'text-slate-300 hover:bg-white/5 hover:text-white',
  danger: 'border border-rose-500/30 bg-rose-500/10 text-rose-300 hover:bg-rose-500/20',
};
export const Button = forwardRef<HTMLButtonElement, ButtonHTMLAttributes<HTMLButtonElement> & { variant?: Variant; loading?: boolean; size?: 'sm' | 'md' }>(
  ({ variant = 'primary', loading, size = 'md', className, children, disabled, ...rest }, ref) => (
    <button
      ref={ref}
      className={cx('inline-flex items-center justify-center gap-2 rounded-lg font-medium transition disabled:cursor-not-allowed disabled:opacity-50',
        size === 'sm' ? 'px-2.5 py-1.5 text-xs' : 'px-4 py-2 text-sm', variants[variant], className)}
      disabled={disabled || loading}
      aria-busy={loading || undefined}
      {...rest}
    >
      {loading && <Loader2 className="h-4 w-4 animate-spin" aria-hidden />}
      {children}
    </button>
  ),
);
Button.displayName = 'Button';

export function Field({ label, hint, error, children, id }: { label: string; hint?: string; error?: string; children: (id: string) => ReactNode; id?: string }) {
  const auto = useId();
  const fid = id ?? auto;
  return (
    <div>
      <label htmlFor={fid} className="label">{label}</label>
      {children(fid)}
      {hint && !error && <p className="mt-1 text-xs text-slate-500">{hint}</p>}
      {error && <p className="mt-1 text-xs text-rose-400" role="alert">{error}</p>}
    </div>
  );
}
export const Input = forwardRef<HTMLInputElement, InputHTMLAttributes<HTMLInputElement>>((p, ref) => <input ref={ref} {...p} className={cx('input', p.className)} />);
Input.displayName = 'Input';
export const Select = (p: SelectHTMLAttributes<HTMLSelectElement>) => <select {...p} className={cx('input', p.className)} />;
export const Textarea = (p: TextareaHTMLAttributes<HTMLTextAreaElement>) => <textarea {...p} className={cx('input min-h-[96px]', p.className)} />;

const tones = {
  slate: 'bg-slate-500/15 text-slate-300 ring-slate-400/20',
  green: 'bg-emerald-500/15 text-emerald-300 ring-emerald-400/20',
  amber: 'bg-amber-500/15 text-amber-300 ring-amber-400/20',
  red: 'bg-rose-500/15 text-rose-300 ring-rose-400/20',
  blue: 'bg-brand-500/15 text-brand-300 ring-brand-400/20',
  violet: 'bg-accent-500/15 text-accent-400 ring-accent-400/20',
};
export type Tone = keyof typeof tones;
export const Badge = ({ tone = 'slate', children, className }: { tone?: Tone; children: ReactNode; className?: string }) => (
  <span className={cx('inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-xs font-medium ring-1 ring-inset', tones[tone], className)}>{children}</span>
);

export function Card({ title, action, children, className, bodyClass }: { title?: ReactNode; action?: ReactNode; children: ReactNode; className?: string; bodyClass?: string }) {
  return (
    <section className={cx('card', className)}>
      {(title || action) && (
        <header className="flex items-center justify-between gap-3 border-b border-white/[0.06] px-5 py-3.5">
          {title && <h2 className="text-sm font-semibold text-slate-100">{title}</h2>}
          {action}
        </header>
      )}
      <div className={cx('p-5', bodyClass)}>{children}</div>
    </section>
  );
}

export function PageHeader({ title, description, actions }: { title: string; description?: string; actions?: ReactNode }) {
  return (
    <div className="mb-6 flex flex-col gap-3 sm:flex-row sm:items-end sm:justify-between">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight text-white">{title}</h1>
        {description && <p className="mt-1 text-sm text-slate-400">{description}</p>}
      </div>
      {actions && <div className="flex flex-wrap gap-2">{actions}</div>}
    </div>
  );
}

export const Spinner = ({ label = 'Loading' }: { label?: string }) => (
  <div role="status" className="flex items-center justify-center gap-2 py-10 text-sm text-slate-400">
    <Loader2 className="h-4 w-4 animate-spin" aria-hidden /> {label}…
  </div>
);
export const Empty = ({ title, children }: { title: string; children?: ReactNode }) => (
  <div className="flex flex-col items-center justify-center gap-2 rounded-xl border border-dashed border-white/10 px-6 py-12 text-center">
    <p className="text-sm font-medium text-slate-200">{title}</p>
    {children && <div className="text-sm text-slate-400">{children}</div>}
  </div>
);
export const ErrorNote = ({ error }: { error: unknown }) => (
  <div role="alert" className="flex items-start gap-2 rounded-lg border border-rose-500/30 bg-rose-500/10 px-3 py-2 text-sm text-rose-200">
    <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" aria-hidden /> {errorMessage(error)}
  </div>
);

export function CopyButton({ value, label = 'Copy', className }: { value: string; label?: string; className?: string }) {
  const [done, setDone] = useState(false);
  const copy = async () => {
    try { await navigator.clipboard.writeText(value); } catch { /* clipboard blocked */ }
    setDone(true);
    setTimeout(() => setDone(false), 1500);
  };
  return (
    <Button type="button" variant="secondary" size="sm" onClick={copy} className={className} aria-label={done ? 'Copied' : label}>
      {done ? <Check className="h-3.5 w-3.5 text-emerald-400" aria-hidden /> : <Copy className="h-3.5 w-3.5" aria-hidden />}
      <span>{done ? 'Copied' : label}</span>
    </Button>
  );
}

/** A secret the backend returns exactly once. */
export function SecretOnce({ title, value, onDone }: { title: string; value: string; onDone: () => void }) {
  return (
    <div className="rounded-xl border border-amber-400/30 bg-amber-400/[0.07] p-4" role="region" aria-label={title}>
      <div className="mb-2 flex items-center gap-2 text-sm font-semibold text-amber-200"><ShieldCheck className="h-4 w-4" aria-hidden /> {title}</div>
      <p className="mb-3 text-xs text-amber-100/80">Copy it now — for your security it will not be shown again.</p>
      <div className="flex flex-col gap-2 sm:flex-row">
        <code className="flex-1 break-all rounded-lg bg-ink-950 px-3 py-2 font-mono text-xs text-slate-100" data-testid="secret-value">{value}</code>
        <div className="flex gap-2"><CopyButton value={value} /><Button size="sm" variant="ghost" onClick={onDone}>I’ve saved it</Button></div>
      </div>
    </div>
  );
}

function useDialog(open: boolean, onClose: () => void) {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!open) return;
    const prev = document.activeElement as HTMLElement | null;
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose(); };
    document.addEventListener('keydown', onKey);
    const first = ref.current?.querySelector<HTMLElement>('input, select, textarea, button:not([data-close])');
    first?.focus();
    return () => { document.removeEventListener('keydown', onKey); prev?.focus?.(); };
  }, [open, onClose]);
  return ref;
}

export function Modal({ open, onClose, title, children, footer }: { open: boolean; onClose: () => void; title: string; children: ReactNode; footer?: ReactNode }) {
  const ref = useDialog(open, onClose);
  if (!open) return null;
  return (
    <div className="fixed inset-0 z-50 flex items-end justify-center bg-black/60 p-0 backdrop-blur-sm sm:items-center sm:p-4" onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
      <div ref={ref} role="dialog" aria-modal="true" aria-label={title} className="card max-h-[90vh] w-full overflow-y-auto rounded-b-none sm:max-w-lg sm:rounded-2xl">
        <div className="flex items-center justify-between border-b border-white/[0.06] px-5 py-4">
          <h2 className="text-base font-semibold text-white">{title}</h2>
          <button data-close onClick={onClose} className="rounded-md p-1 text-slate-400 hover:bg-white/5 hover:text-white" aria-label="Close"><X className="h-4 w-4" /></button>
        </div>
        <div className="space-y-4 p-5">{children}</div>
        {footer && <div className="flex justify-end gap-2 border-t border-white/[0.06] px-5 py-3">{footer}</div>}
      </div>
    </div>
  );
}

export function Drawer({ open, onClose, title, children }: { open: boolean; onClose: () => void; title: string; children: ReactNode }) {
  const ref = useDialog(open, onClose);
  if (!open) return null;
  return (
    <div className="fixed inset-0 z-50 flex justify-end bg-black/50 backdrop-blur-sm" onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
      <div ref={ref} role="dialog" aria-modal="true" aria-label={title} className="h-full w-full max-w-md overflow-y-auto border-l border-white/10 bg-ink-900 shadow-2xl">
        <div className="sticky top-0 flex items-center justify-between border-b border-white/[0.06] bg-ink-900/95 px-5 py-4 backdrop-blur">
          <h2 className="text-base font-semibold text-white">{title}</h2>
          <button data-close onClick={onClose} className="rounded-md p-1 text-slate-400 hover:bg-white/5 hover:text-white" aria-label="Close"><X className="h-4 w-4" /></button>
        </div>
        <div className="p-5">{children}</div>
      </div>
    </div>
  );
}

export function Tabs<T extends string>({ tabs, value, onChange, label }: { tabs: { key: T; label: string }[]; value: T; onChange: (k: T) => void; label: string }) {
  return (
    <div role="tablist" aria-label={label} className="inline-flex rounded-lg border border-white/10 bg-ink-900 p-0.5">
      {tabs.map((t) => (
        <button key={t.key} role="tab" aria-selected={value === t.key} onClick={() => onChange(t.key)}
          className={cx('rounded-md px-3 py-1.5 text-xs font-medium transition', value === t.key ? 'bg-ink-700 text-white' : 'text-slate-400 hover:text-white')}>
          {t.label}
        </button>
      ))}
    </div>
  );
}

export function useConfirm() {
  return (msg: string) => (typeof window !== 'undefined' && typeof window.confirm === 'function' ? window.confirm(msg) : true);
}
