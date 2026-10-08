"use client";

import { fmtDate, useApi } from "@/lib/api";
import { Card, Empty, ErrorNote, PageHeader } from "@/components/ui";

type Doc = { id: string; title: string; content: string; tags: string[]; client_id: string | null; created_by: string; created_at: string };

export default function KnowledgePage() {
  const { data, error } = useApi<Doc[]>("/knowledge", 15000);
  return (
    <>
      <PageHeader title="Knowledge base" subtitle="What the agents have learned. Client notes are only visible to work for that client." />
      <ErrorNote error={error} />
      <div className="space-y-3">
        {data?.length === 0 && <Empty>Nothing saved yet.</Empty>}
        {data?.map((d) => (
          <Card key={d.id}>
            <h3 className="font-medium">{d.title}</h3>
            <p className="text-xs text-muted">{d.created_by} · {fmtDate(d.created_at)} · {d.client_id ? "client-specific" : "agency-wide"} {d.tags.length ? `· ${d.tags.join(", ")}` : ""}</p>
            <p className="mt-2 whitespace-pre-wrap text-sm">{d.content}</p>
          </Card>
        ))}
      </div>
    </>
  );
}
