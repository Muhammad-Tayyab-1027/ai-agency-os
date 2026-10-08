"use client";

import { useState } from "react";
import { api, fmtDate, fmtMoney, useApi } from "@/lib/api";
import { Badge, Button, Card, Empty, ErrorNote, Input, Json, PageHeader, TextArea } from "@/components/ui";

type Goal = { id: string; title: string; target_revenue: number; currency: string; period: string; priority_services: string[]; notes: string | null; strategy: Record<string, unknown>; status: string; created_at: string };
type Service = { key: string; name: string; base_price: number; currency: string };

export default function GoalsPage() {
  const { data, error, reload } = useApi<Goal[]>("/goals", 10000);
  const { data: services } = useApi<Service[]>("/services");
  const [title, setTitle] = useState("Reach $5,000/month, websites first");
  const [target, setTarget] = useState("5000");
  const [period, setPeriod] = useState("monthly");
  const [picked, setPicked] = useState<string[]>(["website"]);
  const [notes, setNotes] = useState("");
  const [formError, setFormError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function create() {
    setBusy(true);
    setFormError(null);
    try {
      await api("/goals", { method: "POST", body: { title, target_revenue: Number(target), period, priority_services: picked, notes: notes || null } });
      await reload();
    } catch (e) {
      setFormError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <>
      <PageHeader title="Goals" subtitle="The Manager plans everything from your revenue goal." />
      <ErrorNote error={error} />
      <Card className="mb-6">
        <h2 className="mb-3 font-medium">New goal</h2>
        <ErrorNote error={formError} />
        <div className="grid gap-3 md:grid-cols-3">
          <Input value={title} onChange={(e) => setTitle(e.target.value)} placeholder="Title" className="md:col-span-3" />
          <Input type="number" min={1} value={target} onChange={(e) => setTarget(e.target.value)} placeholder="Target revenue (USD)" />
          <select value={period} onChange={(e) => setPeriod(e.target.value)} className="rounded-lg border border-border bg-panel px-3 py-2 text-sm">
            <option value="monthly">per month</option><option value="quarterly">per quarter</option><option value="yearly">per year</option>
          </select>
        </div>
        <div className="mt-3 flex flex-wrap gap-2">
          {services?.map((s) => (
            <button key={s.key} onClick={() => setPicked(picked.includes(s.key) ? picked.filter((k) => k !== s.key) : [...picked, s.key])}
              className={`rounded-full border px-3 py-1 text-xs ${picked.includes(s.key) ? "border-accent bg-accent/10 text-accent" : "border-border text-muted"}`}>
              {s.name} · {fmtMoney(s.base_price, s.currency)}
            </button>
          ))}
        </div>
        <TextArea rows={2} className="mt-3" placeholder="Anything the Manager should know (optional)" value={notes} onChange={(e) => setNotes(e.target.value)} />
        <div className="mt-3"><Button onClick={create} disabled={busy}>Create goal & start planning</Button></div>
      </Card>
      <div className="space-y-3">
        {data?.length === 0 && <Empty>No goals yet.</Empty>}
        {data?.map((g) => (
          <Card key={g.id}>
            <div className="flex flex-wrap items-start justify-between gap-2">
              <div>
                <h3 className="font-medium">{g.title}</h3>
                <p className="text-xs text-muted">{fmtMoney(g.target_revenue, g.currency)} {g.period} · {g.priority_services.join(", ") || "any service"} · {fmtDate(g.created_at)}</p>
              </div>
              <Badge value={g.status} />
            </div>
            {Object.keys(g.strategy).length > 0 ? (
              <details className="mt-3"><summary className="cursor-pointer text-sm text-muted">Strategy</summary><Json value={g.strategy} /></details>
            ) : <p className="mt-2 text-sm text-muted">Strategy not written yet.</p>}
          </Card>
        ))}
      </div>
    </>
  );
}
