"use client";

import { api, fmtDate, useApi } from "@/lib/api";
import { Button, Card, Empty, ErrorNote, Md, PageHeader } from "@/components/ui";

type Report = { id: string; kind: string; content: string; period_start: string; period_end: string; created_at: string };

export default function ReportsPage() {
  const { data, error, reload } = useApi<Report[]>("/reports", 10000);
  async function request(kind: string) {
    await api("/reports", { method: "POST", body: { kind } });
    setTimeout(reload, 3000);
  }
  return (
    <>
      <PageHeader title="Reports" subtitle="Written by the Manager, to you. Figures come straight from the database."
        actions={<><Button variant="ghost" onClick={() => request("daily")}>Daily report now</Button><Button variant="ghost" onClick={() => request("weekly")}>Weekly report now</Button></>} />
      <ErrorNote error={error} />
      <div className="space-y-4">
        {data?.length === 0 && <Empty>No reports yet. A daily report is written every morning.</Empty>}
        {data?.map((r) => (
          <Card key={r.id}>
            <p className="mb-2 text-xs uppercase tracking-wide text-muted">{r.kind} · {fmtDate(r.period_start)} → {fmtDate(r.period_end)}</p>
            <Md>{r.content}</Md>
          </Card>
        ))}
      </div>
    </>
  );
}
