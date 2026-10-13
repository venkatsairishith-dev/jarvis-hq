import { useEffect, useRef, type ReactNode } from "react";

type Tone = "idle" | "run" | "wait" | "err" | "done" | "accent";
const TONES: Record<Tone, string> = {
  idle: "bg-idle-bg text-idle-fg", run: "bg-run-bg text-run-fg", wait: "bg-wait-bg text-wait-fg",
  err: "bg-err-bg text-err-fg", done: "bg-done-bg text-done-fg", accent: "bg-accent-bg text-accent-fg",
};

export function Badge({ tone = "idle", children, title }: { tone?: Tone; children: ReactNode; title?: string }) {
  return <span title={title} className={`inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-xs font-medium ${TONES[tone]}`}>{children}</span>;
}

export function stateTone(s: string): Tone {
  if (["completed", "done", "active", "live", "fired", "approved", "ok"].includes(s)) return "done";
  if (["failed", "cancelled", "refused", "unknown", "terminated", "denied", "error"].includes(s)) return "err";
  if (["running", "verifying", "repairing", "queued", "firing", "scheduled"].includes(s)) return "run";
  if (["paused", "waiting_approval", "claimed", "draft", "skipped_overlap", "skipped_missed", "skipped_limit"].includes(s)) return "wait";
  return "idle";
}

export function Button({ children, onClick, kind = "default", disabled, title, type = "button", className = "", ...rest }:
  { children: ReactNode; onClick?: () => void; kind?: "default" | "primary" | "danger" | "ghost"; disabled?: boolean; title?: string;
    type?: "button" | "submit"; className?: string } & Record<string, unknown>) {
  const k = {
    default: "border border-edge bg-surface hover:bg-hover active:bg-active",
    primary: "bg-accent text-white hover:bg-accent-h border border-transparent",
    danger: "border border-err/60 bg-err-bg text-err-fg hover:brightness-110",
    ghost: "border border-transparent hover:bg-hover",
  }[kind];
  return (
    <button type={type} title={title} disabled={disabled} onClick={onClick} {...rest}
      className={`inline-flex min-h-9 items-center justify-center gap-2 rounded-lg px-3 text-sm font-medium transition-colors disabled:cursor-not-allowed disabled:opacity-50 ${k} ${className}`}>
      {children}
    </button>
  );
}

export function Card({ title, actions, children, className = "" }: { title?: ReactNode; actions?: ReactNode; children: ReactNode; className?: string }) {
  return (
    <section className={`rounded-xl border border-line bg-surface ${className}`}>
      {(title || actions) && (
        <header className="flex items-center justify-between gap-3 border-b border-line px-4 py-3">
          <h2 className="text-sm font-semibold text-fg">{title}</h2>
          <div className="flex items-center gap-2">{actions}</div>
        </header>
      )}
      <div className="p-4">{children}</div>
    </section>
  );
}

export function Empty({ title, hint, action }: { title: string; hint?: string; action?: ReactNode }) {
  return (
    <div className="flex flex-col items-center gap-2 px-4 py-10 text-center">
      <p className="text-sm font-medium text-fg2">{title}</p>
      {hint && <p className="max-w-md text-sm text-fg3">{hint}</p>}
      {action}
    </div>
  );
}

export function ErrorNote({ children }: { children: ReactNode }) {
  return <div role="alert" className="rounded-lg border border-err/50 bg-err-bg px-3 py-2 text-sm text-err-fg">{children}</div>;
}

export function Spinner({ label = "Loading" }: { label?: string }) {
  return <span role="status" className="inline-flex items-center gap-2 text-sm text-fg3"><span className="size-3 animate-spin rounded-full border-2 border-edge border-t-accent" />{label}</span>;
}

export function Field({ label, children, hint }: { label: string; children: ReactNode; hint?: string }) {
  return (
    <label className="block">
      <span className="mb-1 block text-xs font-medium text-fg2">{label}</span>
      {children}
      {hint && <span className="mt-1 block text-xs text-fg3">{hint}</span>}
    </label>
  );
}

export const inputCls = "w-full rounded-lg border border-input bg-bg2 px-3 py-2 text-sm text-fg placeholder:text-off focus:border-accent";

/** A modal on the native <dialog> element: focus is trapped and Escape closes it. */
export function Dialog({ open, onClose, title, children, footer }: { open: boolean; onClose: () => void; title: string; children: ReactNode; footer?: ReactNode }) {
  const ref = useRef<HTMLDialogElement>(null);
  useEffect(() => {
    const d = ref.current;
    if (!d) return;
    if (open && !d.open) d.showModal();
    if (!open && d.open) d.close();
  }, [open]);
  return (
    <dialog ref={ref} onClose={onClose} onCancel={onClose} aria-label={title} className="w-[min(560px,92vw)]">
      <div className="p-5">
        <h2 className="mb-3 text-base font-semibold">{title}</h2>
        {children}
      </div>
      {footer && <div className="flex justify-end gap-2 border-t border-line px-5 py-3">{footer}</div>}
    </dialog>
  );
}

export function Tabs<T extends string>({ value, onChange, items }: { value: T; onChange: (v: T) => void; items: { id: T; label: string; count?: number }[] }) {
  return (
    <div role="tablist" className="flex flex-wrap gap-1 rounded-lg border border-line bg-bg2 p-1">
      {items.map((it) => (
        <button key={it.id} role="tab" aria-selected={value === it.id} onClick={() => onChange(it.id)}
          className={`rounded-md px-3 py-1.5 text-sm ${value === it.id ? "bg-surface font-medium text-fg shadow-sm" : "text-fg3 hover:text-fg"}`}>
          {it.label}{it.count !== undefined && <span className="num ml-1.5 text-xs text-fg3">{it.count}</span>}
        </button>
      ))}
    </div>
  );
}

export function Kbd({ children }: { children: ReactNode }) {
  return <kbd className="rounded border border-edge bg-bg2 px-1.5 py-0.5 font-mono text-[11px] text-fg2">{children}</kbd>;
}
