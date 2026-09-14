import clsx from "clsx";
import { AlertCircle, Check, Inbox, Loader2, X } from "lucide-react";
import { createContext, useCallback, useContext, useEffect, useState, type ReactNode } from "react";

// ----------------------------------------------------------------------------- status chips

const STATUS_STYLES: Record<string, string> = {
  VERIFIED: "bg-emerald-500/15 text-emerald-700 dark:text-emerald-300",
  SUPPORTED: "bg-sky-500/15 text-sky-700 dark:text-sky-300",
  CANDIDATE: "bg-amber-500/15 text-amber-700 dark:text-amber-300",
  EXTRACTED: "bg-slate-500/15 text-slate-700 dark:text-slate-300",
  CONFLICTED: "bg-rose-500/15 text-rose-700 dark:text-rose-300",
  STALE: "bg-orange-500/15 text-orange-700 dark:text-orange-300",
  REJECTED: "bg-zinc-500/15 text-zinc-600 dark:text-zinc-400 line-through",
  SUPERSEDED: "bg-violet-500/15 text-violet-700 dark:text-violet-300",
  // sources / docs / jobs / runs
  ACTIVE: "bg-emerald-500/15 text-emerald-700 dark:text-emerald-300",
  PAUSED: "bg-amber-500/15 text-amber-700 dark:text-amber-300",
  BLOCKED: "bg-rose-500/15 text-rose-700 dark:text-rose-300",
  FETCHED: "bg-sky-500/15 text-sky-700 dark:text-sky-300",
  FAILED: "bg-rose-500/15 text-rose-700 dark:text-rose-300",
  SKIPPED: "bg-zinc-500/15 text-zinc-600 dark:text-zinc-400",
  QUEUED: "bg-amber-500/15 text-amber-700 dark:text-amber-300",
  RUNNING: "bg-sky-500/15 text-sky-700 dark:text-sky-300",
  DONE: "bg-emerald-500/15 text-emerald-700 dark:text-emerald-300",
  DEAD: "bg-rose-500/15 text-rose-700 dark:text-rose-300",
  OPEN: "bg-rose-500/15 text-rose-700 dark:text-rose-300",
  RESOLVED: "bg-emerald-500/15 text-emerald-700 dark:text-emerald-300",
};

export function StatusChip({ status, className }: { status: string; className?: string }) {
  return (
    <span className={clsx("chip", STATUS_STYLES[status] ?? "bg-slate-500/15 text-slate-600", className)}>
      {status}
    </span>
  );
}

const PROVENANCE_STYLES: Record<string, string> = {
  OFFICIAL: "bg-emerald-500/12 text-emerald-700 dark:text-emerald-300",
  EXTERNAL: "bg-sky-500/12 text-sky-700 dark:text-sky-300",
  COMMUNITY: "bg-amber-500/12 text-amber-700 dark:text-amber-300",
  USER: "bg-fuchsia-500/12 text-fuchsia-700 dark:text-fuchsia-300",
  ORGANIZATION: "bg-violet-500/12 text-violet-700 dark:text-violet-300",
  DERIVED: "bg-slate-500/12 text-slate-600 dark:text-slate-300",
};

/** Where the knowledge came from (req. 9): official docs, external site, community, a user, the organisation. */
export function ProvenanceChip({ provenance }: { provenance: string }) {
  return (
    <span className={clsx("chip", PROVENANCE_STYLES[provenance] ?? "bg-slate-500/12 text-slate-600")} title="provenance level">
      {provenance.toLowerCase()}
    </span>
  );
}

const ORIGIN_LABEL: Record<string, string> = { DIRECT: "direct", DERIVED: "derived", SYNTHESIZED: "synthesized", EXPERIMENTALLY_VALIDATED: "validated" };

/** How it was obtained (req. 8). */
export function OriginChip({ origin }: { origin: string }) {
  const cls = origin === "EXPERIMENTALLY_VALIDATED" ? "bg-emerald-500/15 text-emerald-700 dark:text-emerald-300" : "bg-slate-500/12 text-slate-600 dark:text-slate-300";
  return <span className={clsx("chip", cls)} title="knowledge origin">{ORIGIN_LABEL[origin] ?? origin}</span>;
}

export function PolarityChip({ polarity }: { polarity: string }) {
  if (polarity !== "negative") return null;
  return <span className="chip bg-rose-500/12 text-rose-700 dark:text-rose-300" title="negative knowledge: what does not work">does not work</span>;
}

/** Colour by the plugin's declared semantics (polarity / role), falling back to the conventional vocabulary. */
export function TypeChip({ type, spec }: { type: string; spec?: { polarity?: string; role?: string } }) {
  const negative = spec ? spec.polarity === "negative" : ["limitation", "warning", "anti_pattern", "common_mistake", "pitfall"].includes(type);
  const example = spec ? spec.role === "example" : type === "example";
  const cls = negative ? "bg-rose-500/12 text-rose-700 dark:text-rose-300" : example ? "bg-teal-500/12 text-teal-700 dark:text-teal-300" : "bg-indigo-500/12 text-indigo-700 dark:text-indigo-300";
  return <span className={`chip ${cls}`}>{type.replace(/_/g, " ")}</span>;
}

// ----------------------------------------------------------------------------- confidence

export function Confidence({ value, level, compact }: { value: number; level?: number; compact?: boolean }) {
  const pctv = Math.round(value * 100);
  const color = value >= 0.75 ? "bg-emerald-500" : value >= 0.5 ? "bg-sky-500" : value >= 0.3 ? "bg-amber-500" : "bg-rose-500";
  return (
    <div className={clsx("flex items-center gap-2", compact ? "w-28" : "w-40")} title={`confidence ${pctv}%${level !== undefined ? `, verification level ${level}` : ""}`}>
      <div className="h-1.5 flex-1 rounded-full panel-2 overflow-hidden">
        <div className={clsx("h-full rounded-full transition-all", color)} style={{ width: `${pctv}%` }} />
      </div>
      <span className="mono text-xs tabular-nums w-9 text-right">{pctv}%</span>
      {level !== undefined && !compact && <span className="kbd">L{level}</span>}
    </div>
  );
}

// ----------------------------------------------------------------------------- layout bits

export function PageHeader({ title, subtitle, actions }: { title: string; subtitle?: ReactNode; actions?: ReactNode }) {
  return (
    <div className="flex flex-wrap items-end justify-between gap-3 mb-5">
      <div>
        <h1 className="text-xl font-semibold tracking-tight">{title}</h1>
        {subtitle && <p className="muted text-sm mt-0.5">{subtitle}</p>}
      </div>
      {actions && <div className="flex items-center gap-2">{actions}</div>}
    </div>
  );
}

export function Card({ title, children, className, actions }: { title?: ReactNode; children: ReactNode; className?: string; actions?: ReactNode }) {
  return (
    <section className={clsx("panel p-4", className)}>
      {(title || actions) && (
        <header className="flex items-center justify-between mb-3">
          <h2 className="text-sm font-semibold">{title}</h2>
          {actions}
        </header>
      )}
      {children}
    </section>
  );
}

export function Stat({ label, value, hint, accent }: { label: string; value: ReactNode; hint?: ReactNode; accent?: string }) {
  return (
    <div className="panel p-4 fade-in">
      <div className="muted text-xs font-medium uppercase tracking-wider">{label}</div>
      <div className={clsx("text-2xl font-semibold mt-1 tabular-nums", accent)}>{value}</div>
      {hint && <div className="muted text-xs mt-1">{hint}</div>}
    </div>
  );
}

export function Empty({ icon, title, hint, action }: { icon?: ReactNode; title: string; hint?: ReactNode; action?: ReactNode }) {
  return (
    <div className="flex flex-col items-center justify-center text-center py-14 px-6">
      <div className="muted mb-3">{icon ?? <Inbox size={28} />}</div>
      <div className="font-medium">{title}</div>
      {hint && <div className="muted text-sm mt-1 max-w-md">{hint}</div>}
      {action && <div className="mt-4">{action}</div>}
    </div>
  );
}

export function Spinner({ className }: { className?: string }) {
  return <Loader2 className={clsx("spin", className)} size={16} />;
}

export function Loading({ label = "Loading…" }: { label?: string }) {
  return (
    <div className="flex items-center gap-2 muted text-sm py-10 justify-center">
      <Spinner /> {label}
    </div>
  );
}

export function ErrorBox({ error }: { error: unknown }) {
  const msg = error instanceof Error ? error.message : String(error);
  return (
    <div className="flex items-start gap-2 rounded-xl border border-rose-300/60 bg-rose-500/10 text-rose-700 dark:text-rose-300 p-3 text-sm">
      <AlertCircle size={16} className="mt-0.5 shrink-0" />
      <div>{msg}</div>
    </div>
  );
}

export function Pagination({ page, pageSize, total, onChange }: { page: number; pageSize: number; total: number; onChange: (p: number) => void }) {
  const pages = Math.max(1, Math.ceil(total / pageSize));
  if (pages <= 1) return null;
  return (
    <div className="flex items-center justify-between mt-3 text-sm">
      <span className="muted">
        {(page - 1) * pageSize + 1}–{Math.min(page * pageSize, total)} of {total}
      </span>
      <div className="flex gap-1">
        <button className="btn btn-sm" disabled={page <= 1} onClick={() => onChange(page - 1)}>
          Previous
        </button>
        <span className="btn btn-sm pointer-events-none">
          {page} / {pages}
        </span>
        <button className="btn btn-sm" disabled={page >= pages} onClick={() => onChange(page + 1)}>
          Next
        </button>
      </div>
    </div>
  );
}

export function Drawer({ open, onClose, title, children, wide }: { open: boolean; onClose: () => void; title?: ReactNode; children: ReactNode; wide?: boolean }) {
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, onClose]);
  if (!open) return null;
  return (
    <div className="fixed inset-0 z-40">
      <div className="absolute inset-0 bg-black/40 backdrop-blur-[1px]" onClick={onClose} />
      <aside
        className={clsx(
          "absolute right-0 top-0 h-full panel rounded-none border-y-0 border-r-0 shadow-2xl flex flex-col fade-in",
          wide ? "w-[min(960px,100vw)]" : "w-[min(640px,100vw)]",
        )}
      >
        <header className="flex items-center justify-between px-5 py-3 border-b border-line">
          <div className="font-semibold truncate pr-4">{title}</div>
          <button className="btn btn-sm" onClick={onClose} aria-label="Close">
            <X size={14} />
          </button>
        </header>
        <div className="overflow-y-auto p-5 flex-1">{children}</div>
      </aside>
    </div>
  );
}

// ----------------------------------------------------------------------------- toasts

type Toast = { id: number; kind: "ok" | "err"; text: string };
const ToastCtx = createContext<(kind: Toast["kind"], text: string) => void>(() => {});

export function ToastProvider({ children }: { children: ReactNode }) {
  const [toasts, setToasts] = useState<Toast[]>([]);
  const push = useCallback((kind: Toast["kind"], text: string) => {
    const id = Date.now() + Math.random();
    setToasts((t) => [...t, { id, kind, text }]);
    setTimeout(() => setToasts((t) => t.filter((x) => x.id !== id)), 4500);
  }, []);
  return (
    <ToastCtx.Provider value={push}>
      {children}
      <div className="fixed bottom-4 right-4 z-50 flex flex-col gap-2">
        {toasts.map((t) => (
          <div
            key={t.id}
            className={clsx(
              "panel px-4 py-3 text-sm shadow-xl flex items-center gap-2 fade-in",
              t.kind === "ok" ? "border-emerald-400/50" : "border-rose-400/50",
            )}
          >
            {t.kind === "ok" ? <Check size={14} className="text-emerald-500" /> : <AlertCircle size={14} className="text-rose-500" />}
            {t.text}
          </div>
        ))}
      </div>
    </ToastCtx.Provider>
  );
}

export function useToast() {
  return useContext(ToastCtx);
}

export function KV({ k, v, mono }: { k: string; v: ReactNode; mono?: boolean }) {
  return (
    <div className="flex justify-between gap-4 py-1.5 border-b border-line last:border-b-0 text-sm">
      <span className="muted shrink-0">{k}</span>
      <span className={clsx("text-right break-all", mono && "mono text-xs")}>{v}</span>
    </div>
  );
}
