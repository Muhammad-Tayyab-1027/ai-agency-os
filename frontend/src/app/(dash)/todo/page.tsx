"use client";

import { useState } from "react";
import { api, fmtDate, useApi } from "@/lib/api";
import { Badge, Button, Card, Empty, ErrorNote, PageHeader, TextArea } from "@/components/ui";

type HumanTask = { id: string; title: string; reason: string; steps: string[]; status: string; created_by: string; created_at: string; response_note: string | null };

function TodoCard({ h, onDone }: { h: HumanTask; onDone: () => void }) {
  const [note, setNote] = useState("");
  const [error, setError] = useState<string | null>(null);
  async function finish(outcome: "done" | "cancelled") {
    try {
      await api(`/human-tasks/${h.id}/complete`, { method: "POST", body: { note, outcome } });
      onDone();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }
  return (
    <Card>
      <div className="flex items-start justify-between gap-2">
        <div>
          <h3 className="font-medium">{h.title}</h3>
          <p className="text-xs text-muted">From {h.created_by} · {fmtDate(h.created_at)}</p>
        </div>
        <Badge value={h.status} />
      </div>
      <p className="mt-2 text-sm">{h.reason}</p>
      <ol className="mt-3 list-decimal space-y-1 pl-5 text-sm">{h.steps.map((s, i) => <li key={i}>{s}</li>)}</ol>
      <ErrorNote error={error} />
      {h.status === "open" ? (
        <div className="mt-3 space-y-2">
          <TextArea rows={2} placeholder="Optional note for the Manager. Never paste passwords or API keys here." value={note} onChange={(e) => setNote(e.target.value)} />
          <div className="flex gap-2">
            <Button onClick={() => finish("done")}>I've done this</Button>
            <Button variant="ghost" onClick={() => finish("cancelled")}>Won't do</Button>
          </div>
        </div>
      ) : h.response_note && <p className="mt-2 text-sm text-muted">Your note: {h.response_note}</p>}
    </Card>
  );
}

export default function TodoPage() {
  const [status, setStatus] = useState("open");
  const { data, error, reload } = useApi<HumanTask[]>(`/human-tasks?status=${status}`, 8000);
  return (
    <>
      <PageHeader title="Your to-dos" subtitle="Steps only you can take: sign-ups, API keys, verification, contracts, payments, closing deals."
        actions={["open", "done", "cancelled"].map((s) => <Button key={s} variant={s === status ? "primary" : "ghost"} onClick={() => setStatus(s)}>{s}</Button>)} />
      <ErrorNote error={error} />
      <div className="space-y-3">
        {data?.length === 0 && <Empty>Nothing here.</Empty>}
        {data?.map((h) => <TodoCard key={h.id} h={h} onDone={reload} />)}
      </div>
    </>
  );
}
