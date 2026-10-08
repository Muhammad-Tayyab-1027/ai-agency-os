"use client";

import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { Suspense, useState } from "react";
import { fmtDate, useApi } from "@/lib/api";
import { Badge, Button, Empty, ErrorNote, PageHeader, Table, Td } from "@/components/ui";

type Task = { id: string; kind: string; title: string; status: string; agent: string | null; required_role: string | null; priority: number; attempts: number; last_error: string | null; updated_at: string };

function TaskList() {
  const params = useSearchParams();
  const [status, setStatus] = useState(params.get("status") ?? "");
  const { data, error } = useApi<Task[]>(`/tasks${status ? `?status=${status}` : ""}`, 6000);
  return (
    <>
      <PageHeader title="Tasks" subtitle="Everything the agents are doing, with full transcripts."
        actions={["", "queued", "running", "in_review", "blocked", "completed", "failed"].map((s) => (
          <Button key={s || "all"} variant={s === status ? "primary" : "ghost"} onClick={() => setStatus(s)}>{s || "all"}</Button>
        ))} />
      <ErrorNote error={error} />
      {data?.length === 0 ? <Empty>No tasks.</Empty> : (
        <Table head={["Task", "Kind", "Agent", "Status", "Attempts", "Updated"]}>
          {data?.map((t) => (
            <tr key={t.id}>
              <Td><Link href={`/tasks/${t.id}`} className="font-medium hover:text-accent">{t.title}</Link>
                {t.last_error && <div className="mt-1 max-w-md truncate text-xs text-danger">{t.last_error}</div>}</Td>
              <Td>{t.kind}</Td>
              <Td>{t.agent ?? <span className="text-warn">needs {t.required_role}</span>}</Td>
              <Td><Badge value={t.status} /></Td>
              <Td>{t.attempts}</Td>
              <Td className="whitespace-nowrap text-muted">{fmtDate(t.updated_at)}</Td>
            </tr>
          ))}
        </Table>
      )}
    </>
  );
}

export default function TasksPage() {
  return <Suspense><TaskList /></Suspense>;
}
