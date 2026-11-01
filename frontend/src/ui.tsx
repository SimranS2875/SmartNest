import type { ButtonHTMLAttributes, ReactNode } from "react";

type Tone = "neutral" | "ok" | "warn" | "bad" | "brand";

const TONES: Record<Tone, string> = {
  neutral: "bg-stone-100 text-stone-700 border-stone-200",
  ok: "bg-ok-soft text-ok border-green-200",
  warn: "bg-warn-soft text-warn border-amber-200",
  bad: "bg-bad-soft text-bad border-red-200",
  brand: "bg-brand-soft text-brand-dark border-teal-200",
};

export function Badge({ tone = "neutral", children, title }: { tone?: Tone; children: ReactNode; title?: string }) {
  return (
    <span title={title} className={`inline-flex shrink-0 items-center gap-1 whitespace-nowrap rounded-full border px-2 py-0.5 text-xs font-medium ${TONES[tone]}`}>
      {children}
    </span>
  );
}

export function Card({ children, className = "" }: { children: ReactNode; className?: string }) {
  return <section className={`rounded-xl border border-line bg-card shadow-sm ${className}`}>{children}</section>;
}

type BtnProps = ButtonHTMLAttributes<HTMLButtonElement> & { variant?: "primary" | "ghost" | "danger" };

export function Button({ variant = "ghost", className = "", ...rest }: BtnProps) {
  const style = {
    primary: "bg-brand text-white hover:bg-brand-dark border-transparent",
    ghost: "bg-white text-ink hover:bg-stone-50 border-line",
    danger: "bg-white text-bad hover:bg-bad-soft border-line",
  }[variant];
  return (
    <button type="button" {...rest}
      className={`inline-flex items-center justify-center gap-1.5 rounded-lg border px-3 py-1.5 text-sm font-medium transition-colors ${style} ${className}`} />
  );
}

export function Stat({ label, value, hint, tone }: { label: string; value: ReactNode; hint?: ReactNode; tone?: Tone }) {
  return (
    <div className="rounded-xl border border-line bg-card p-4">
      <div className="text-xs font-medium uppercase tracking-wide text-muted">{label}</div>
      <div className={`mt-1 text-2xl font-semibold tabular-nums ${tone === "bad" ? "text-bad" : tone === "ok" ? "text-ok" : ""}`}>{value}</div>
      {hint && <div className="mt-0.5 text-xs text-muted">{hint}</div>}
    </div>
  );
}

export function ScoreBar({ value, label }: { value: number | null; label?: string }) {
  if (value === null) return null;
  const pct = Math.max(0, Math.min(100, value));
  return (
    <div role="meter" aria-valuemin={0} aria-valuemax={100} aria-valuenow={Math.round(pct)} aria-label={label ?? "Utility score"}
      className="h-1.5 w-full overflow-hidden rounded-full bg-stone-100">
      <div className="h-full rounded-full bg-brand" style={{ width: `${pct}%` }} />
    </div>
  );
}

export function Empty({ title, children }: { title: string; children?: ReactNode }) {
  return (
    <div className="rounded-xl border border-dashed border-line bg-white/60 p-8 text-center">
      <div className="font-medium">{title}</div>
      {children && <div className="mt-1 text-sm text-muted">{children}</div>}
    </div>
  );
}
