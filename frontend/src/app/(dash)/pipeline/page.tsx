"use client";

import { useState } from "react";
import { api, useApi } from "@/lib/api";
import { Badge, Button, Card, Empty, ErrorNote, Input, PageHeader, Table, Td } from "@/components/ui";

type Lead = { id: string; business_name: string; website: string | null; email: string | null; city: string | null; country: string | null; category: string | null; stage: string; score: number | null; score_reasons: string[]; evidence: string[]; recommended_service: string | null; opted_out: boolean };
type Stage = { stage: string; count: number };

export default function PipelinePage() {
  const [stage, setStage] = useState("");
  const { data: stages } = useApi<Stage[]>("/pipeline", 10000);
  const { data, error, reload } = useApi<Lead[]>(`/leads${stage ? `?stage=${stage}` : ""}`, 10000);
  const [optOut, setOptOut] = useState("");

  async function move(id: string, to: string) {
    await api(`/leads/${id}/stage`, { method: "POST", body: { stage: to, note: "Moved by owner" } });
    reload();
  }
  async function suppress() {
    await api("/suppression", { method: "POST", body: { value: optOut, reason: "opt-out" } });
    setOptOut("");
    reload();
  }

  return (
    <>
      <PageHeader title="Leads & pipeline" subtitle="Researched leads with scores and evidence. Only you can mark a deal as won." />
      <ErrorNote error={error} />
      <div className="mb-4 flex flex-wrap gap-2">
        <Button variant={stage === "" ? "primary" : "ghost"} onClick={() => setStage("")}>all</Button>
        {stages?.map((s) => (
          <Button key={s.stage} variant={s.stage === stage ? "primary" : "ghost"} onClick={() => setStage(s.stage)}>
            {s.stage.replaceAll("_", " ")} ({s.count})
          </Button>
        ))}
      </div>
      {data?.length === 0 ? <Empty>No leads yet. They appear once the Lead Research agent starts working.</Empty> : (
        <Table head={["Business", "Score & reasons", "Service", "Stage", ""]}>
          {data?.map((l) => (
            <tr key={l.id}>
              <Td>
                <div className="font-medium">{l.business_name} {l.opted_out && <Badge value="opted out" />}</div>
                <div className="text-xs text-muted">{[l.category, l.city, l.country].filter(Boolean).join(" · ")}</div>
                {l.website && <a href={l.website} target="_blank" rel="noreferrer noopener" className="text-xs text-accent">{l.website}</a>}
              </Td>
              <Td>
                <div className="font-semibold">{l.score ?? "—"}</div>
                <ul className="list-disc pl-4 text-xs text-muted">{l.score_reasons.map((r, i) => (
                  <li key={i}>{l.evidence[i] ? <a href={l.evidence[i]} target="_blank" rel="noreferrer noopener" className="hover:text-accent">{r}</a> : r}</li>
                ))}</ul>
              </Td>
              <Td>{l.recommended_service ?? "—"}</Td>
              <Td><Badge value={l.stage} /></Td>
              <Td>
                <select value={l.stage} onChange={(e) => move(l.id, e.target.value)} className="rounded border border-border bg-panel px-2 py-1 text-xs">
                  {stages?.map((s) => <option key={s.stage} value={s.stage}>{s.stage.replaceAll("_", " ")}</option>)}
                </select>
              </Td>
            </tr>
          ))}
        </Table>
      )}
      <Card className="mt-6">
        <h2 className="mb-2 font-medium">Opt-outs & suppression list</h2>
        <p className="mb-2 text-sm text-muted">Anyone added here is never contacted again. Use an email address or @domain.</p>
        <div className="flex gap-2"><Input value={optOut} onChange={(e) => setOptOut(e.target.value)} placeholder="name@business.com or @business.com" /><Button onClick={suppress} disabled={!optOut.includes("@")}>Add</Button></div>
      </Card>
    </>
  );
}
