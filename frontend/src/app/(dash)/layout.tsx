"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useState } from "react";
import { api, useApi } from "@/lib/api";

type Overview = { pending_approvals: number; open_human_tasks: number; errors_unresolved: number; settings: { paused: boolean } };

const NAV: { href: string; label: string; badge?: keyof Overview }[] = [
  { href: "/", label: "Overview" },
  { href: "/chat", label: "Manager chat" },
  { href: "/approvals", label: "Approvals", badge: "pending_approvals" },
  { href: "/todo", label: "Your to-dos", badge: "open_human_tasks" },
  { href: "/goals", label: "Goals" },
  { href: "/pipeline", label: "Leads & pipeline" },
  { href: "/clients", label: "Clients & revenue" },
  { href: "/agents", label: "Agents" },
  { href: "/tasks", label: "Tasks" },
  { href: "/reports", label: "Reports" },
  { href: "/knowledge", label: "Knowledge" },
  { href: "/logs", label: "Logs & errors", badge: "errors_unresolved" },
  { href: "/settings", label: "Settings" },
];

export default function DashLayout({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  const { data } = useApi<Overview>("/overview", 10000);
  const [open, setOpen] = useState(false);

  const nav = (
    <nav className="space-y-0.5">
      {NAV.map((item) => {
        const active = item.href === "/" ? pathname === "/" : pathname.startsWith(item.href);
        const count = item.badge && data ? Number(data[item.badge]) : 0;
        return (
          <Link key={item.href} href={item.href} onClick={() => setOpen(false)}
            className={`flex items-center justify-between rounded-lg px-3 py-2 text-sm ${active ? "bg-accent/10 font-medium text-accent" : "text-muted hover:bg-border/50 hover:text-text"}`}>
            {item.label}
            {count > 0 && <span className="rounded-full bg-warn/20 px-2 text-xs text-warn">{count}</span>}
          </Link>
        );
      })}
    </nav>
  );

  return (
    <div className="min-h-screen md:flex">
      <aside className="hidden w-60 shrink-0 border-r border-border bg-panel p-4 md:block">
        <div className="mb-6 px-3 text-lg font-semibold">Agency OS</div>
        {nav}
        <button className="mt-6 px-3 text-xs text-muted hover:text-text"
          onClick={async () => { await api("/auth/logout", { method: "POST" }); window.location.href = "/login"; }}>
          Sign out
        </button>
      </aside>
      <div className="flex items-center justify-between border-b border-border bg-panel px-4 py-3 md:hidden">
        <span className="font-semibold">Agency OS</span>
        <button className="text-sm" onClick={() => setOpen(!open)}>{open ? "Close" : "Menu"}</button>
      </div>
      {open && <div className="border-b border-border bg-panel p-3 md:hidden">{nav}</div>}
      <main className="min-w-0 flex-1 px-4 py-6 md:px-8">
        {data?.settings?.paused && (
          <div className="mb-4 rounded-lg border border-danger/40 bg-danger/10 px-3 py-2 text-sm text-danger">
            The agency is paused (kill switch). No agent actions will run. Resume it in Settings.
          </div>
        )}
        {children}
      </main>
    </div>
  );
}
