"use client";

import { useState } from "react";
import { api, fmtDate, fmtMoney, useApi } from "@/lib/api";
import { Button, Card, Empty, ErrorNote, Input, PageHeader, Table, Td } from "@/components/ui";

type Client = { id: string; name: string; email: string | null; status: string; created_at: string };
type Revenue = { events: { id: string; amount: number; currency: string; service_key: string | null; note: string | null; occurred_at: string }[]; by_service: Record<string, number> };
type Service = { key: string; name: string };

export default function ClientsPage() {
  const clients = useApi<Client[]>("/clients", 15000);
  const revenue = useApi<Revenue>("/revenue", 15000);
  const { data: services } = useApi<Service[]>("/services");
  const [name, setName] = useState("");
  const [email, setEmail] = useState("");
  const [amount, setAmount] = useState("");
  const [service, setService] = useState("website");
  const [clientId, setClientId] = useState("");
  const [error, setError] = useState<string | null>(null);

  async function run(fn: () => Promise<unknown>) {
    setError(null);
    try { await fn(); clients.reload(); revenue.reload(); } catch (e) { setError(e instanceof Error ? e.message : String(e)); }
  }

  return (
    <>
      <PageHeader title="Clients & revenue" subtitle="Record money you actually received; the Manager shifts effort toward what earns." />
      <ErrorNote error={error ?? clients.error ?? revenue.error} />
      <div className="grid gap-4 lg:grid-cols-2">
        <Card>
          <h2 className="mb-3 font-medium">Add client</h2>
          <div className="space-y-2">
            <Input placeholder="Business name" value={name} onChange={(e) => setName(e.target.value)} />
            <Input placeholder="Email (optional)" value={email} onChange={(e) => setEmail(e.target.value)} />
            <Button disabled={name.length < 2} onClick={() => run(async () => { await api("/clients", { method: "POST", body: { name, email: email || null } }); setName(""); setEmail(""); })}>Add client</Button>
          </div>
        </Card>
        <Card>
          <h2 className="mb-3 font-medium">Record revenue received</h2>
          <div className="space-y-2">
            <Input type="number" min={1} placeholder="Amount (USD)" value={amount} onChange={(e) => setAmount(e.target.value)} />
            <select value={service} onChange={(e) => setService(e.target.value)} className="w-full rounded-lg border border-border bg-panel px-3 py-2 text-sm">
              {services?.map((s) => <option key={s.key} value={s.key}>{s.name}</option>)}
            </select>
            <select value={clientId} onChange={(e) => setClientId(e.target.value)} className="w-full rounded-lg border border-border bg-panel px-3 py-2 text-sm">
              <option value="">No client</option>
              {clients.data?.map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}
            </select>
            <Button disabled={!Number(amount)} onClick={() => run(async () => { await api("/revenue", { method: "POST", body: { amount: Number(amount), service_key: service, client_id: clientId || null } }); setAmount(""); })}>Record</Button>
          </div>
        </Card>
      </div>
      <h2 className="mb-3 mt-6 font-medium">Revenue by service</h2>
      <div className="mb-6 flex flex-wrap gap-2">
        {revenue.data && Object.entries(revenue.data.by_service).map(([k, v]) => (
          <Card key={k} className="py-2"><div className="text-xs text-muted">{k}</div><div className="font-semibold">{fmtMoney(v)}</div></Card>
        ))}
        {revenue.data && Object.keys(revenue.data.by_service).length === 0 && <p className="text-sm text-muted">No revenue recorded yet.</p>}
      </div>
      <h2 className="mb-3 font-medium">Clients</h2>
      {clients.data?.length === 0 ? <Empty>No clients yet.</Empty> : (
        <Table head={["Client", "Email", "Status", "Since"]}>
          {clients.data?.map((c) => <tr key={c.id}><Td className="font-medium">{c.name}</Td><Td>{c.email ?? "—"}</Td><Td>{c.status}</Td><Td>{fmtDate(c.created_at)}</Td></tr>)}
        </Table>
      )}
    </>
  );
}
