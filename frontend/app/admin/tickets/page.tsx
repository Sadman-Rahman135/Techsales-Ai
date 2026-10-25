import type { Metadata } from "next";
import { redirect } from "next/navigation";
import { Suspense } from "react";

import { AdminShell } from "@/components/admin/admin-shell";
import { TicketsView, type TicketQuery } from "@/components/admin/tickets-view";
import { isStaff, requireUser } from "@/lib/auth";
import { backendFetch } from "@/lib/backend";
import type { TicketPage } from "@/lib/tickets";

export const metadata: Metadata = { title: "Tickets", robots: { index: false, follow: false } };

const PATH = "/admin/tickets";
const STATUSES = new Set(["open", "in_progress", "resolved"]);

export default function TicketsPage({ searchParams }: PageProps<"/admin/tickets">) {
  return (
    <AdminShell current={PATH}>
      <main id="main" className="mx-auto w-full max-w-5xl flex-1 px-4 py-6 sm:px-6 lg:py-8">
        <Suspense fallback={<p className="text-fg-muted">Loading tickets…</p>}>
          <TicketsSection searchParams={searchParams} />
        </Suspense>
      </main>
    </AdminShell>
  );
}

function first(value: string | string[] | undefined): string {
  return (Array.isArray(value) ? value[0] : value) ?? "";
}

async function TicketsSection({ searchParams }: { searchParams: PageProps<"/admin/tickets">["searchParams"] }) {
  const me = await requireUser(PATH);
  if (!isStaff(me)) redirect("/chat");
  const params = await searchParams;
  const query: TicketQuery = {
    status: STATUSES.has(first(params.status)) ? first(params.status) : "",
    mine: first(params.mine) === "1",
  };
  const api = new URLSearchParams({ limit: "50" });
  if (query.status) api.set("status", query.status);
  if (query.mine) api.set("mine", "true");
  const response = await backendFetch(`/api/v1/tickets?${api}`);
  if (response.status === 401) redirect(`/login?next=${encodeURIComponent(PATH)}`);
  if (!response.ok) throw new Error(`ticket list failed: HTTP ${response.status}`);
  const page = (await response.json()) as TicketPage;
  const now = Date.parse(response.headers.get("date") ?? "") || 0;
  return <TicketsView page={page} query={query} timezone={me.organization.timezone} renderedAt={now} />;
}
