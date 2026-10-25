"use client";

import { Inbox, Ticket } from "lucide-react";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useTransition } from "react";

import { PriorityLabel, TicketStatusBadge } from "@/components/admin/ticket-badges";
import { When } from "@/components/admin/when";
import { TICKET_STATUS, type TicketPage, type TicketStatus } from "@/lib/tickets";

export type TicketQuery = { status: string; mine: boolean };

const TABS: { value: "" | TicketStatus; label: string }[] = [
  { value: "", label: "All" },
  { value: "open", label: TICKET_STATUS.open },
  { value: "in_progress", label: TICKET_STATUS.in_progress },
  { value: "resolved", label: TICKET_STATUS.resolved },
];

export function TicketsView({
  page,
  query,
  timezone,
  renderedAt,
}: {
  page: TicketPage;
  query: TicketQuery;
  timezone: string;
  renderedAt: number;
}) {
  const router = useRouter();
  const pathname = usePathname();
  const [, startTransition] = useTransition();

  function navigate(next: Partial<TicketQuery>) {
    const merged = { ...query, ...next };
    const params = new URLSearchParams();
    if (merged.status) params.set("status", merged.status);
    if (merged.mine) params.set("mine", "1");
    const search = params.toString();
    startTransition(() => router.replace(search ? `${pathname}?${search}` : pathname, { scroll: false }));
  }

  const all = Object.values(page.counts).reduce((a, b) => a + b, 0);
  return (
    <>
      <div className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <h1 className="font-heading text-2xl font-semibold">Tickets</h1>
          <p className="mt-1 text-sm text-fg-muted">
            Cases handed over from the assistant. Create one from an answer with <strong>Create ticket</strong>.
          </p>
        </div>
      </div>
      <div className="mt-6 flex flex-wrap items-center justify-between gap-3">
        <div role="group" aria-label="Status" className="flex flex-wrap gap-1 rounded-control bg-surface-muted p-1">
          {TABS.map((tab) => {
            const count = tab.value ? page.counts[tab.value] : all;
            const active = query.status === tab.value;
            return (
              <button
                key={tab.label}
                type="button"
                aria-pressed={active}
                onClick={() => navigate({ status: tab.value })}
                className={`inline-flex min-h-10 items-center gap-1.5 rounded-control px-3 text-sm font-semibold ${
                  active ? "bg-surface text-fg shadow-sm" : "text-fg-muted hover:text-fg"
                }`}
              >
                {tab.label}
                <span className="tabular-nums text-fg-muted">{count}</span>
              </button>
            );
          })}
        </div>
        <label className="inline-flex min-h-11 items-center gap-2 text-sm font-semibold">
          <input
            type="checkbox"
            checked={query.mine}
            onChange={(e) => navigate({ mine: e.target.checked })}
            className="size-4 accent-accent"
          />
          Assigned to me
        </label>
      </div>
      {page.items.length === 0 ? (
        <div className="mt-6 rounded-card border border-border bg-surface p-8 text-center">
          <Inbox className="mx-auto size-6 text-fg-muted" aria-hidden="true" />
          <p className="mt-2 font-semibold">No tickets here</p>
          <p className="mt-1 text-sm text-fg-muted">
            {query.status || query.mine ? "Try another status or show everyone's tickets." : "Nothing has been handed over yet."}
          </p>
        </div>
      ) : (
        <ul className="mt-4 divide-y divide-border rounded-card border border-border bg-surface">
          {page.items.map((ticket) => (
            <li key={ticket.id}>
              <Link
                href={`/admin/tickets/${ticket.id}`}
                className="flex flex-col gap-2 p-4 hover:bg-surface-muted sm:flex-row sm:items-center sm:justify-between"
              >
                <div className="min-w-0">
                  <p className="flex items-center gap-2 font-semibold">
                    <Ticket className="size-4 shrink-0 text-fg-muted" aria-hidden="true" />
                    <span className="shrink-0 font-mono text-sm whitespace-nowrap text-fg-muted">{ticket.reference}</span>
                    <span className="truncate">{ticket.subject}</span>
                  </p>
                  <p className="mt-1 text-xs text-fg-muted">
                    {ticket.assignee ? `Assigned to ${ticket.assignee}` : "Unassigned"}
                    {ticket.order_number ? ` · ${ticket.order_number}` : ""}
                    {" · updated "}
                    <When iso={ticket.updated_at} timezone={timezone} now={renderedAt} />
                  </p>
                </div>
                <div className="flex shrink-0 items-center gap-3">
                  <PriorityLabel priority={ticket.priority} />
                  <TicketStatusBadge status={ticket.status} />
                </div>
              </Link>
            </li>
          ))}
        </ul>
      )}
      <p className="mt-3 text-sm text-fg-muted">
        {page.total} {page.total === 1 ? "ticket" : "tickets"}
      </p>
    </>
  );
}
