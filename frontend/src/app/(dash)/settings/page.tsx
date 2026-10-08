"use client";

import { useEffect, useState } from "react";
import { api, useApi } from "@/lib/api";
import { Button, Card, ErrorNote, Input, PageHeader } from "@/components/ui";

type Settings = {
  runtime: { paused: boolean; max_agents: number; daily_llm_budget_usd: number; outreach_daily_cap: number; max_open_tasks: number };
  static: { llm_provider: string; manager_model: string; specialist_model: string; anthropic_key_configured: boolean; target_markets: string[]; target_niches: string[]; approval_ttl_hours: number };
};

export default function SettingsPage() {
  const { data, error, reload } = useApi<Settings>("/settings");
  const [form, setForm] = useState<Settings["runtime"] | null>(null);
  const [saveError, setSaveError] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);
  useEffect(() => { if (data) setForm(data.runtime); }, [data]);

  async function save(values: Partial<Settings["runtime"]>) {
    setSaveError(null);
    setSaved(false);
    try {
      await api("/settings", { method: "PUT", body: { values } });
      setSaved(true);
      reload();
    } catch (e) {
      setSaveError(e instanceof Error ? e.message : String(e));
    }
  }

  const num = (key: keyof Settings["runtime"], label: string) => form && (
    <label className="block text-sm"><span className="text-muted">{label}</span>
      <Input type="number" value={String(form[key])} onChange={(e) => setForm({ ...form, [key]: Number(e.target.value) })} />
    </label>
  );

  return (
    <>
      <PageHeader title="Settings" subtitle="Guardrails and the global kill switch." />
      <ErrorNote error={error ?? saveError} />
      {data && form && (
        <div className="space-y-4">
          <Card>
            <h2 className="font-medium">Kill switch</h2>
            <p className="mb-3 text-sm text-muted">Pausing stops every agent action immediately. Nothing runs until you resume.</p>
            {data.runtime.paused
              ? <Button onClick={() => save({ paused: false })}>Resume the agency</Button>
              : <Button variant="danger" onClick={() => save({ paused: true })}>Pause everything</Button>}
          </Card>
          <Card>
            <h2 className="mb-3 font-medium">Limits</h2>
            <div className="grid gap-3 md:grid-cols-2">
              {num("max_agents", "Maximum number of agents")}
              {num("daily_llm_budget_usd", "Daily AI budget (USD)")}
              {num("outreach_daily_cap", "Outreach messages per day")}
              {num("max_open_tasks", "Maximum open tasks")}
            </div>
            <div className="mt-3 flex items-center gap-3">
              <Button onClick={() => save({ max_agents: form.max_agents, daily_llm_budget_usd: form.daily_llm_budget_usd, outreach_daily_cap: form.outreach_daily_cap, max_open_tasks: form.max_open_tasks })}>Save limits</Button>
              {saved && <span className="text-sm text-ok">Saved.</span>}
            </div>
          </Card>
          <Card>
            <h2 className="mb-2 font-medium">Configuration (set in the server's .env)</h2>
            <dl className="grid grid-cols-[auto_1fr] gap-x-4 gap-y-1 text-sm">
              <dt className="text-muted">AI provider</dt><dd>{data.static.llm_provider}{data.static.llm_provider === "mock" && " (offline — no real work)"}</dd>
              <dt className="text-muted">Anthropic key</dt><dd>{data.static.anthropic_key_configured ? "configured" : "not configured"}</dd>
              <dt className="text-muted">Manager model</dt><dd>{data.static.manager_model}</dd>
              <dt className="text-muted">Specialist model</dt><dd>{data.static.specialist_model}</dd>
              <dt className="text-muted">Markets</dt><dd>{data.static.target_markets.join(", ")}</dd>
              <dt className="text-muted">Niches</dt><dd>{data.static.target_niches.join("; ")}</dd>
              <dt className="text-muted">Approvals expire after</dt><dd>{data.static.approval_ttl_hours} hours</dd>
            </dl>
          </Card>
        </div>
      )}
    </>
  );
}
