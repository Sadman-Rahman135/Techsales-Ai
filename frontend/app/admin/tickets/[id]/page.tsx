import type { Metadata } from "next";
import { notFound, redirect } from "next/navigation";
import { Suspense } from "react";

import { AdminShell } from "@/components/admin/admin-shell";
import { TicketDetailView } from "@/components/admin/ticket-detail";
import { isStaff, requireUser } from "@/lib/auth";
import { backendFetch } from "@/lib/backend";
import type { StaffMember, TicketDetail } from "@/lib/tickets";

export const metadata: Metadata = { title: "Ticket", robots: { index: false, follow: false } };

export default function TicketPage({ params }: PageProps<"/admin/tickets/[id]">) {
  return (
    <AdminShell current="/admin/tickets">
      <main id="main" className="mx-auto w-full max-w-4xl flex-1 px-4 py-6 sm:px-6 lg:py-8">
        <Suspense fallback={<p className="text-fg-muted">Loading the ticket…</p>}>
          <TicketSection params={params} />
        </Suspense>
      </main>
    </AdminShell>
  );
}

async function TicketSection({ params }: { params: PageProps<"/admin/tickets/[id]">["params"] }) {
  const { id } = await params;
  const me = await requireUser(`/admin/tickets/${id}`);
  if (!isStaff(me)) redirect("/chat");
  const [response, staffResponse] = await Promise.all([
    backendFetch(`/api/v1/tickets/${encodeURIComponent(id)}`),
    backendFetch("/api/v1/tickets/staff"),
  ]);
  if (response.status === 404 || response.status === 422) notFound();
  if (!response.ok) throw new Error(`ticket failed: HTTP ${response.status}`);
  const ticket = (await response.json()) as TicketDetail;
  const staff = staffResponse.ok ? ((await staffResponse.json()) as StaffMember[]) : [];
  return <TicketDetailView ticket={ticket} staff={staff} me={me.user_id} timezone={me.organization.timezone} />;
}
