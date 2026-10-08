"use client";

import Link from "next/link";
import { fmtMoney, useApi } from "@/lib/api";
import { Badge, Card, ErrorNote, PageHeader, Stat } from "@/components/ui";

type Overview = {
  goals: { id: string; title: string; target_revenue: number; currency: string; period: string; has_strategy: boolean }[];
  revenue_this_month: number;
  tasks_by_status: Record<string, number>;
  leads_by_stage: Record<string, number>;
  pending_approvals: number;
  open_human_tasks: number;
  errors_unresolved: number;
  agents: { key: string; role: string; status: string }[];
  llm_provider: string;
};

export default function OverviewPage() {
  const { data, error } = useApi<Overview>("/overview", 10000);
  const goal = data?.goals[0];
  const leads = data ? Object.values(data.leads_by_stage).reduce((a, b) => a + b, 0) : 0;
  const t = data?.tasks_by_status ?? {};
  return (
    <>
      <PageHeader title="Overview" subtitle="What your agency is doing right now." />
      <ErrorNote error={error} />
      {data?.llm_provider === "mock" && (
        <div className="mb-4 rounded-lg border border-warn/40 bg-warn/10 px-3 py-2 text-sm text-warn">
          Offline mock mode: agents run end to end but do no real work. Add ANTHROPIC_API_KEY and set LLM_PROVIDER=anthropic.
        </div>
      )}
      <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        <Stat label="Revenue this month" value={data ? fmtMoney(data.revenue_this_month, goal?.currency) : "…"}
          hint={goal ? `Goal ${fmtMoney(goal.target_revenue, goal.currency)} / ${goal.period}` : "No goal set"} />
        <Stat label="Waiting on you" value={data ? data.pending_approvals + data.open_human_tasks : "…"}
          hint={data ? `${data.pending_approvals} approvals · ${data.open_human_tasks} to-dos` : undefined}
          tone={data && data.pending_approvals + data.open_human_tasks > 0 ? "warn" : undefined} />
        <Stat label="Leads" value={leads} hint={`${data?.leads_by_stage.qualified ?? 0} qualified`} />
        <Stat label="Errors" value={data?.errors_unresolved ?? "…"} tone={data?.errors_unresolved ? "danger" : "ok"} hint="unresolved" />
      </div>

      <div className="mt-6 grid gap-4 lg:grid-cols-2">
        <Card>
          <h2 className="mb-3 font-medium">Tasks</h2>
          <div className="flex flex-wrap gap-3 text-sm">
            {["queued", "running", "in_review", "blocked", "completed", "failed"].map((s) => (
              <Link key={s} href={`/tasks?status=${s}`} className="flex items-center gap-2"><Badge value={s} /> {t[s] ?? 0}</Link>
            ))}
          </div>
        </Card>
        <Card>
          <h2 className="mb-3 font-medium">Agents</h2>
          <div className="flex flex-wrap gap-2 text-sm">
            {data?.agents.map((a) => (
              <span key={a.key} className="flex items-center gap-2 rounded-lg border border-border px-2 py-1">
                {a.key} <Badge value={a.status} />
              </span>
            ))}
          </div>
        </Card>
      </div>

      <Card className="mt-4">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <div>
            <h2 className="font-medium">{goal ? goal.title : "Set your first revenue goal"}</h2>
            <p className="text-sm text-muted">
              {goal ? (goal.has_strategy ? "The Manager has written a strategy for this goal." : "The Manager is planning this goal.")
                : "The Manager plans everything from your goal."}
            </p>
          </div>
          <div className="flex gap-2">
            <Link href="/goals" className="rounded-lg border border-border px-3 py-1.5 text-sm">Goals</Link>
            <Link href="/chat" className="rounded-lg bg-accent px-3 py-1.5 text-sm text-accent-text">Talk to the Manager</Link>
          </div>
        </div>
      </Card>
    </>
  );
}
