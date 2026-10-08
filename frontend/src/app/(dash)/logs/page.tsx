"use client";

import { useState } from "react";
import { api, fmtDate, useApi } from "@/lib/api";
import { Badge, Button, Empty, ErrorNote, PageHeader, Table, Td } from "@/components/ui";

type Err = { id: string; source: string; message: string; task_id: string | null; created_at: string };
type Log = { id: number; ts: string; actor_type: string; actor: string; action: string; target_type: string | null; target_id: string | null; outcome: string; data: unknown };

export default function LogsPage() {
  const [tab, setTab] = useState<"errors" | "audit">("errors");
  const errors = useApi<Err[]>("/errors", 10000);
  const logs = useApi<Log[]>("/audit?limit=300", 10000);
  const chain = useApi<{ valid: boolean; checked: number }>("/audit/verify");

  return (
    <>
      <PageHeader title="Logs & errors" subtitle="Every action is recorded in a tamper-evident audit log."
        actions={<><Button variant={tab === "errors" ? "primary" : "ghost"} onClick={() => setTab("errors")}>Errors</Button><Button variant={tab === "audit" ? "primary" : "ghost"} onClick={() => setTab("audit")}>Audit log</Button></>} />
      <ErrorNote error={errors.error ?? logs.error} />
      {tab === "errors" ? (
        errors.data?.length === 0 ? <Empty>No unresolved errors.</Empty> : (
          <Table head={["When", "Source", "Message", ""]}>
            {errors.data?.map((e) => (
              <tr key={e.id}>
                <Td className="whitespace-nowrap text-muted">{fmtDate(e.created_at)}</Td>
                <Td>{e.source}</Td>
                <Td className="text-danger">{e.message}{e.task_id && <a href={`/tasks/${e.task_id}`} className="ml-2 text-xs text-accent">task</a>}</Td>
                <Td><Button variant="ghost" onClick={async () => { await api(`/errors/${e.id}/resolve`, { method: "POST" }); errors.reload(); }}>Resolve</Button></Td>
              </tr>
            ))}
          </Table>
        )
      ) : (
        <>
          <p className="mb-3 text-sm">{chain.data ? (chain.data.valid ? <span className="text-ok">Hash chain verified ({chain.data.checked} entries).</span> : <span className="text-danger">Audit chain is BROKEN — investigate.</span>) : "Verifying…"}</p>
          <Table head={["When", "Actor", "Action", "Target", "Outcome"]}>
            {logs.data?.map((l) => (
              <tr key={l.id}>
                <Td className="whitespace-nowrap text-muted">{fmtDate(l.ts)}</Td>
                <Td>{l.actor}</Td>
                <Td><details><summary className="cursor-pointer">{l.action}</summary><pre className="mt-1 max-w-lg overflow-auto text-xs">{JSON.stringify(l.data, null, 2)}</pre></details></Td>
                <Td className="text-xs text-muted">{l.target_type} {l.target_id?.slice(0, 8)}</Td>
                <Td><Badge value={l.outcome} /></Td>
              </tr>
            ))}
          </Table>
        </>
      )}
    </>
  );
}
