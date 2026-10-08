"use client";

import { api, useApi } from "@/lib/api";
import { Badge, Button, Card, ErrorNote, PageHeader } from "@/components/ui";

type Metrics = { completed: number; failed: number; open: number; success_rate: number | null; avg_review_score: number | null; cost_usd: number } | null;
type Agent = { id: string; key: string; name: string; role: string; purpose: string; model: string; tools: string[]; scopes: string[]; success_criteria: string[]; status: string; created_by: string; metrics: Metrics };
type Template = { role: string; name: string; purpose: string; tools: string[]; planned_tools: string[] };

export default function AgentsPage() {
  const { data, error, reload } = useApi<Agent[]>("/agents", 10000);
  const { data: templates } = useApi<Template[]>("/agent-templates");
  const existing = new Set(data?.map((a) => a.role));

  async function setStatus(id: string, status: string) {
    await api(`/agents/${id}/status`, { method: "POST", body: { status } });
    reload();
  }

  return (
    <>
      <PageHeader title="Agents" subtitle="You created the Manager. It proposes every other agent (with your approval)." />
      <ErrorNote error={error} />
      <div className="grid gap-3 lg:grid-cols-2">
        {data?.map((a) => (
          <Card key={a.id}>
            <div className="flex items-start justify-between gap-2">
              <div>
                <h3 className="font-medium">{a.name} <span className="text-xs text-muted">({a.key})</span></h3>
                <p className="text-xs text-muted">{a.model} · created by {a.created_by}</p>
              </div>
              <Badge value={a.status} />
            </div>
            <p className="mt-2 text-sm">{a.purpose}</p>
            <div className="mt-3 grid grid-cols-4 gap-2 text-center text-xs">
              <div><div className="text-lg font-semibold">{a.metrics?.completed ?? 0}</div>done</div>
              <div><div className="text-lg font-semibold">{a.metrics?.failed ?? 0}</div>failed</div>
              <div><div className="text-lg font-semibold">{a.metrics?.avg_review_score ?? "—"}</div>avg score</div>
              <div><div className="text-lg font-semibold">${(a.metrics?.cost_usd ?? 0).toFixed(2)}</div>AI cost</div>
            </div>
            <details className="mt-3 text-sm">
              <summary className="cursor-pointer text-muted">Tools, permissions & success criteria</summary>
              <p className="mt-2"><span className="text-muted">Tools:</span> {a.tools.join(", ")}</p>
              <p className="mt-1"><span className="text-muted">Scopes:</span> {a.scopes.join(", ")}</p>
              <ul className="mt-1 list-disc pl-5">{a.success_criteria.map((c) => <li key={c}>{c}</li>)}</ul>
            </details>
            <div className="mt-3 flex gap-2">
              {a.status === "active" && <Button variant="ghost" onClick={() => setStatus(a.id, "paused")}>Pause</Button>}
              {a.status === "paused" && <Button onClick={() => setStatus(a.id, "active")}>Resume</Button>}
              {a.role !== "manager" && a.status !== "retired" && <Button variant="danger" onClick={() => setStatus(a.id, "retired")}>Retire</Button>}
            </div>
          </Card>
        ))}
      </div>
      <h2 className="mb-3 mt-8 font-medium">Roles the Manager can create</h2>
      <div className="grid gap-3 md:grid-cols-2 lg:grid-cols-3">
        {templates?.filter((t) => t.role !== "manager").map((t) => (
          <Card key={t.role}>
            <div className="flex items-center justify-between"><h3 className="font-medium">{t.name}</h3>{existing.has(t.role) && <Badge value="active" />}</div>
            <p className="mt-1 text-sm text-muted">{t.purpose}</p>
            {t.planned_tools.length > 0 && <p className="mt-2 text-xs text-muted">Coming next: {t.planned_tools.join(", ")}</p>}
          </Card>
        ))}
      </div>
    </>
  );
}
