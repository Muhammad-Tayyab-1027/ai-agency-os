"use client";

import { useEffect, useRef, useState } from "react";
import { api, fmtDate, useApi } from "@/lib/api";
import { Button, ErrorNote, Md, PageHeader, TextArea } from "@/components/ui";

type Msg = { id: string; role: "owner" | "manager"; author: string; content: string; kind: string; created_at: string };

export default function ChatPage() {
  const { data, error, reload } = useApi<Msg[]>("/chat", 4000);
  const [text, setText] = useState("");
  const [sending, setSending] = useState(false);
  const [sendError, setSendError] = useState<string | null>(null);
  const bottom = useRef<HTMLDivElement>(null);
  const messages = data ?? [];
  const waiting = messages.length > 0 && messages[messages.length - 1].role === "owner";

  useEffect(() => bottom.current?.scrollIntoView({ behavior: "smooth" }), [messages.length]);

  async function send() {
    if (!text.trim()) return;
    setSending(true);
    setSendError(null);
    try {
      await api("/chat", { method: "POST", body: { message: text.trim() } });
      setText("");
      await reload();
    } catch (e) {
      setSendError(e instanceof Error ? e.message : String(e));
    } finally {
      setSending(false);
    }
  }

  return (
    <div className="flex h-[calc(100vh-6rem)] flex-col">
      <PageHeader title="Manager chat" subtitle="Ask questions, give instructions, discuss reports. Risky actions still need your approval." />
      <ErrorNote error={error ?? sendError} />
      <div className="flex-1 space-y-3 overflow-y-auto rounded-xl border border-border bg-panel p-4">
        {messages.length === 0 && <p className="text-sm text-muted">No messages yet. Say hello to your Manager.</p>}
        {messages.map((m) => (
          <div key={m.id} className={`flex ${m.role === "owner" ? "justify-end" : "justify-start"}`}>
            <div className={`max-w-[85%] rounded-2xl px-4 py-2 ${m.role === "owner" ? "bg-accent text-accent-text" : "border border-border bg-bg"}`}>
              <div className="mb-1 text-xs opacity-70">
                {m.role === "owner" ? "You" : m.author}{m.kind === "report" ? " · report" : ""} · {fmtDate(m.created_at)}
              </div>
              {m.role === "owner" ? <p className="whitespace-pre-wrap text-sm">{m.content}</p> : <Md>{m.content}</Md>}
            </div>
          </div>
        ))}
        {waiting && <p className="text-sm text-muted">The Manager is working on a reply…</p>}
        <div ref={bottom} />
      </div>
      <div className="mt-3 flex gap-2">
        <TextArea rows={2} value={text} placeholder="Message the Manager… (Enter to send, Shift+Enter for a new line)"
          onChange={(e) => setText(e.target.value)}
          onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); send(); } }} />
        <Button onClick={send} disabled={sending || !text.trim()}>Send</Button>
      </div>
    </div>
  );
}
