"use client";

import Markdown from "react-markdown";
import type { ReactNode } from "react";

export function PageHeader({ title, subtitle, actions }: { title: string; subtitle?: string; actions?: ReactNode }) {
  return (
    <div className="mb-6 flex flex-wrap items-end justify-between gap-3">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">{title}</h1>
        {subtitle && <p className="mt-1 text-sm text-muted">{subtitle}</p>}
      </div>
      {actions && <div className="flex gap-2">{actions}</div>}
    </div>
  );
}

export function Card({ children, className = "" }: { children: ReactNode; className?: string }) {
  return <div className={`rounded-xl border border-border bg-panel p-4 ${className}`}>{children}</div>;
}

export function Stat({ label, value, hint, tone }: { label: string; value: ReactNode; hint?: string; tone?: "warn" | "danger" | "ok" }) {
  const color = tone === "danger" ? "text-danger" : tone === "warn" ? "text-warn" : tone === "ok" ? "text-ok" : "";
  return (
    <Card>
      <div className="text-xs uppercase tracking-wide text-muted">{label}</div>
      <div className={`mt-1 text-2xl font-semibold ${color}`}>{value}</div>
      {hint && <div className="mt-1 text-xs text-muted">{hint}</div>}
    </Card>
  );
}

const TONES: Record<string, string> = {
  completed: "ok", executed: "ok", active: "ok", done: "ok", won: "ok", succeeded: "ok", approved: "ok",
  failed: "danger", rejected: "danger", lost: "danger", retired: "danger", error: "danger", denied: "danger",
  pending: "warn", blocked: "warn", in_review: "warn", paused: "warn", open: "warn", expired: "warn", aborted: "warn",
};

export function Badge({ value }: { value: string | null | undefined }) {
  const tone = TONES[value ?? ""] ?? "neutral";
  const cls =
    tone === "ok"
      ? "bg-ok/15 text-ok"
      : tone === "danger"
        ? "bg-danger/15 text-danger"
        : tone === "warn"
          ? "bg-warn/15 text-warn"
          : "bg-border text-muted";
  return <span className={`inline-block rounded-full px-2 py-0.5 text-xs font-medium ${cls}`}>{(value ?? "—").replaceAll("_", " ")}</span>;
}

export function Button({
  children, onClick, variant = "primary", disabled, type = "button",
}: { children: ReactNode; onClick?: () => void; variant?: "primary" | "ghost" | "danger"; disabled?: boolean; type?: "button" | "submit" }) {
  const cls =
    variant === "primary"
      ? "bg-accent text-accent-text hover:opacity-90"
      : variant === "danger"
        ? "border border-danger text-danger hover:bg-danger/10"
        : "border border-border hover:bg-border/50";
  return (
    <button type={type} onClick={onClick} disabled={disabled}
      className={`rounded-lg px-3 py-1.5 text-sm font-medium transition disabled:opacity-50 ${cls}`}>
      {children}
    </button>
  );
}

export function Input(props: React.InputHTMLAttributes<HTMLInputElement>) {
  return <input {...props} className={`w-full rounded-lg border border-border bg-panel px-3 py-2 text-sm outline-none focus:border-accent ${props.className ?? ""}`} />;
}

export function TextArea(props: React.TextareaHTMLAttributes<HTMLTextAreaElement>) {
  return <textarea {...props} className={`w-full rounded-lg border border-border bg-panel px-3 py-2 text-sm outline-none focus:border-accent ${props.className ?? ""}`} />;
}

export function Empty({ children }: { children: ReactNode }) {
  return <div className="rounded-xl border border-dashed border-border p-8 text-center text-sm text-muted">{children}</div>;
}

export function ErrorNote({ error }: { error: string | null }) {
  if (!error) return null;
  return <div className="mb-4 rounded-lg border border-danger/40 bg-danger/10 px-3 py-2 text-sm text-danger">{error}</div>;
}

export function Json({ value }: { value: unknown }) {
  return (
    <pre className="max-h-96 overflow-auto rounded-lg bg-bg p-3 text-xs leading-relaxed">{JSON.stringify(value, null, 2)}</pre>
  );
}

export function Md({ children }: { children: string }) {
  return (
    <div className="prose-chat text-sm leading-relaxed">
      <Markdown>{children}</Markdown>
    </div>
  );
}

export function Table({ head, children }: { head: string[]; children: ReactNode }) {
  return (
    <div className="overflow-x-auto rounded-xl border border-border bg-panel">
      <table className="w-full text-left text-sm">
        <thead className="border-b border-border text-xs uppercase tracking-wide text-muted">
          <tr>{head.map((h) => <th key={h} className="px-3 py-2 font-medium">{h}</th>)}</tr>
        </thead>
        <tbody className="divide-y divide-border">{children}</tbody>
      </table>
    </div>
  );
}

export function Td({ children, className = "" }: { children: ReactNode; className?: string }) {
  return <td className={`px-3 py-2 align-top ${className}`}>{children}</td>;
}
