"use client";

import Link from "next/link";
import { use } from "react";
import { api, fmtDate, useApi } from "@/lib/api";
import { Badge, Button, Card, ErrorNote, Json, PageHeader } from "@/components/ui";

type Detail = {
  task: { id: string; title: string; description: string; kind: string; status: string; agent: string | null; acceptance_criteria: string[]; output: unknown; feedback: unknown[]; last_error: string | null; attempts: number; max_attempts: number; inputs: unknown };
  runs: { id: string; attempt: number; status: string; tool_calls: number; cost_usd: number; error: string | null; started_at: string; transcript: unknown[] }[];
  reviews: { verdict: string; score: number; feedback: string; reviewer: string; created_at: string }[];
  messages: { type: string; from: string; to: string; payload: unknown; created_at: string }[];
  children: { id: string; title: string; status: string }[];
};

export default function TaskDetail({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);
  const { data, error, reload } = useApi<Detail>(`/tasks/${id}`, 6000);
  const t = data?.task;

  async function act(action: "retry" | "cancel") {
    await api(`/tasks/${id}/${action}`, { method: "POST", body: action === "cancel" ? { reason: "Cancelled by the owner" } : {} });
    reload();
  }

  return (
    <>
      <PageHeader title={t?.title ?? "Task"} subtitle={t ? `${t.kind} · ${t.agent ?? "unassigned"} · attempt ${t.attempts}/${t.max_attempts}` : undefined}
        actions={t && <>
          {["failed", "blocked", "cancelled"].includes(t.status) && <Button onClick={() => act("retry")}>Retry</Button>}
          {["queued", "blocked", "in_review"].includes(t.status) && <Button variant="danger" onClick={() => act("cancel")}>Cancel</Button>}
        </>} />
      <ErrorNote error={error} />
      {t && (
        <div className="space-y-4">
          <Card>
            <div className="mb-2 flex items-center gap-2"><Badge value={t.status} />{t.last_error && <span className="text-sm text-danger">{t.last_error}</span>}</div>
            <p className="whitespace-pre-wrap text-sm">{t.description}</p>
            {t.acceptance_criteria.length > 0 && <ul className="mt-2 list-disc pl-5 text-sm">{t.acceptance_criteria.map((c) => <li key={c}>{c}</li>)}</ul>}
          </Card>
          {t.output != null && <Card><h2 className="mb-2 font-medium">Result</h2><Json value={t.output} /></Card>}
          {data.reviews.length > 0 && (
            <Card><h2 className="mb-2 font-medium">Reviews</h2>
              {data.reviews.map((r, i) => <p key={i} className="text-sm"><Badge value={r.verdict} /> {r.score}/10 — {r.feedback} <span className="text-muted">({r.reviewer}, {fmtDate(r.created_at)})</span></p>)}
            </Card>
          )}
          {data.children.length > 0 && (
            <Card><h2 className="mb-2 font-medium">Sub-tasks</h2>
              {data.children.map((c) => <p key={c.id} className="text-sm"><Link className="hover:text-accent" href={`/tasks/${c.id}`}>{c.title}</Link> <Badge value={c.status} /></p>)}
            </Card>
          )}
          <Card><h2 className="mb-2 font-medium">Messages</h2>
            {data.messages.map((m, i) => (
              <details key={i} className="text-sm"><summary className="cursor-pointer">{m.type}: {m.from} → {m.to} <span className="text-muted">{fmtDate(m.created_at)}</span></summary><Json value={m.payload} /></details>
            ))}
          </Card>
          <Card><h2 className="mb-2 font-medium">Runs</h2>
            {data.runs.map((r) => (
              <details key={r.id} className="mb-2 text-sm">
                <summary className="cursor-pointer">Attempt {r.attempt} <Badge value={r.status} /> · {r.tool_calls} tool calls · ${r.cost_usd.toFixed(4)} · {fmtDate(r.started_at)}{r.error && <span className="text-danger"> · {r.error}</span>}</summary>
                <Json value={r.transcript} />
              </details>
            ))}
          </Card>
        </div>
      )}
    </>
  );
}
