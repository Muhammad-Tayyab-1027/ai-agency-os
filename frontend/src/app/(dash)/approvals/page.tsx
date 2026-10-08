"use client";

import { useState } from "react";
import { api, fmtDate, useApi } from "@/lib/api";
import { Badge, Button, Card, Empty, ErrorNote, Json, PageHeader, TextArea } from "@/components/ui";

type Approval = {
  id: string; status: string; tool: string; agent: string; title: string; reason: string; payload: Record<string, unknown>;
  result: unknown; expires_at: string; created_at: string; decided_by: string | null; decision_note: string | null;
};

function ApprovalCard({ a, onDone }: { a: Approval; onDone: () => void }) {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(JSON.stringify(a.payload, null, 2));
  const [note, setNote] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function decide(kind: "approve" | "reject") {
    setBusy(true);
    setError(null);
    try {
      if (kind === "approve") {
        const edited = editing ? JSON.parse(draft) : undefined;
        await api(`/approvals/${a.id}/approve`, { method: "POST", body: { edited_payload: edited, note: note || undefined } });
      } else {
        if (note.trim().length < 3) throw new Error("Tell the Manager why (at least a few words).");
        await api(`/approvals/${a.id}/reject`, { method: "POST", body: { note } });
      }
      onDone();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <Card>
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div>
          <h3 className="font-medium">{a.title}</h3>
          <p className="text-xs text-muted">Requested by {a.agent} · {fmtDate(a.created_at)} · expires {fmtDate(a.expires_at)}</p>
        </div>
        <Badge value={a.status} />
      </div>
      {a.reason && <p className="mt-2 text-sm"><span className="text-muted">Why: </span>{a.reason}</p>}
      <ErrorNote error={error} />
      <div className="mt-3">
        {editing ? <TextArea rows={10} value={draft} onChange={(e) => setDraft(e.target.value)} className="font-mono text-xs" /> : <Json value={a.payload} />}
      </div>
      {a.status === "pending" ? (
        <div className="mt-3 space-y-2">
          <TextArea rows={2} placeholder="Note to the Manager (required to reject)" value={note} onChange={(e) => setNote(e.target.value)} />
          <div className="flex flex-wrap gap-2">
            <Button onClick={() => decide("approve")} disabled={busy}>{editing ? "Approve edited version" : "Approve"}</Button>
            <Button variant="ghost" onClick={() => setEditing(!editing)} disabled={busy}>{editing ? "Cancel edit" : "Edit"}</Button>
            <Button variant="danger" onClick={() => decide("reject")} disabled={busy}>Reject</Button>
          </div>
        </div>
      ) : (
        <div className="mt-3 text-sm text-muted">
          Decided by {a.decided_by ?? "—"}{a.decision_note ? ` — "${a.decision_note}"` : ""}
          {a.result != null && <div className="mt-2"><Json value={a.result} /></div>}
        </div>
      )}
    </Card>
  );
}

export default function ApprovalsPage() {
  const [status, setStatus] = useState("pending");
  const { data, error, reload } = useApi<Approval[]>(`/approvals?status=${status}`, 8000);
  return (
    <>
      <PageHeader title="Approvals" subtitle="Level-B actions wait here. Approving executes exactly what you see (or your edited version)."
        actions={["pending", "executed", "rejected", "failed", "expired"].map((s) => (
          <Button key={s} variant={s === status ? "primary" : "ghost"} onClick={() => setStatus(s)}>{s}</Button>
        ))} />
      <ErrorNote error={error} />
      <div className="space-y-3">
        {data?.length === 0 && <Empty>Nothing {status}.</Empty>}
        {data?.map((a) => <ApprovalCard key={a.id} a={a} onDone={reload} />)}
      </div>
    </>
  );
}
