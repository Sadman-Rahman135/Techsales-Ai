"use client";

import { ArrowLeft, Info, LoaderCircle, MessageSquarePlus, TriangleAlert } from "lucide-react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useId, useState, useTransition, type FormEvent } from "react";

import { buttonClass } from "@/components/admin/modal";
import { PriorityLabel, TicketStatusBadge } from "@/components/admin/ticket-badges";
import { ToastProvider, useToast } from "@/components/admin/toast";
import { absoluteTime } from "@/components/admin/when";
import type { ChatError } from "@/lib/chat";
import { TICKET_PRIORITY, TICKET_STATUS, type StaffMember, type TicketDetail } from "@/lib/tickets";

type Props = { ticket: TicketDetail; staff: StaffMember[]; me: string; timezone: string };

export function TicketDetailView(props: Props) {
  return (
    <ToastProvider>
      <Detail {...props} />
    </ToastProvider>
  );
}

const select = "mt-1.5 block min-h-11 w-full rounded-control border border-border-strong bg-surface px-3 text-base text-fg";

function Detail({ ticket, staff, me, timezone }: Props) {
  const id = useId();
  const router = useRouter();
  const toast = useToast();
  const [, startTransition] = useTransition();
  const [saving, setSaving] = useState(false);
  const [note, setNote] = useState("");
  const [noteError, setNoteError] = useState<string | null>(null);
  const refresh = () => startTransition(() => router.refresh());

  async function change(patch: Record<string, string | null>, done: string) {
    setSaving(true);
    const response = await fetch(`/api/tickets/${ticket.id}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(patch),
    }).catch(() => null);
    setSaving(false);
    if (response?.ok) {
      toast("success", done);
      refresh();
      return;
    }
    const body = response ? ((await response.json().catch(() => null)) as { error?: ChatError } | null) : null;
    toast("danger", body?.error?.message ?? "That didn't reach us. Try again.");
  }

  async function addNote(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!note.trim()) {
      setNoteError("Write the note first.");
      return;
    }
    setNoteError(null);
    const response = await fetch(`/api/tickets/${ticket.id}/notes`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ body: note }),
    }).catch(() => null);
    if (response?.ok) {
      setNote("");
      toast("success", "Note added.");
      refresh();
    } else {
      setNoteError("The note wasn't saved. Try again.");
    }
  }

  return (
    <>
      <Link href="/admin/tickets" className="inline-flex min-h-11 items-center gap-1.5 text-sm font-semibold text-accent">
        <ArrowLeft className="size-4" aria-hidden="true" />
        Tickets
      </Link>
      <div className="mt-2">
        <p className="font-mono text-sm text-fg-muted">{ticket.reference}</p>
        <h1 className="font-heading text-2xl font-semibold break-words">{ticket.subject}</h1>
        <div className="mt-2 flex flex-wrap items-center gap-3 text-sm text-fg-muted">
          <TicketStatusBadge status={ticket.status} />
          <PriorityLabel priority={ticket.priority} />
          <span>
            Created by {ticket.created_by ?? "a former employee"} · {absoluteTime(ticket.created_at, timezone)}
          </span>
        </div>
      </div>

      <div className="mt-6 grid gap-6 lg:grid-cols-[1fr_280px]">
        <div className="min-w-0 space-y-6">
          <section aria-labelledby={`${id}-summary`} className="rounded-card border border-border bg-surface p-4">
            <h2 id={`${id}-summary`} className="font-heading text-lg font-semibold">
              Summary
            </h2>
            <p className="mt-2 whitespace-pre-line">{ticket.summary}</p>
            {ticket.summary_drafted ? (
              <p className="mt-3 flex items-start gap-2 text-xs text-fg-muted">
                <Info className="mt-0.5 size-3.5 shrink-0" aria-hidden="true" />
                Drafted by the assistant from the conversation, then reviewed by {ticket.created_by ?? "the employee"}.
              </p>
            ) : null}
          </section>

          <section aria-labelledby={`${id}-notes`}>
            <h2 id={`${id}-notes`} className="font-heading text-lg font-semibold">
              Notes
            </h2>
            {ticket.notes.length === 0 ? (
              <p className="mt-2 text-sm text-fg-muted">No notes yet.</p>
            ) : (
              <ol className="mt-2 space-y-2">
                {ticket.notes.map((n) => (
                  <li key={n.id} className="rounded-card border border-border bg-surface p-3">
                    <p className="whitespace-pre-line text-sm">{n.body}</p>
                    <p className="mt-1 text-xs text-fg-muted">
                      {n.author ?? "A former employee"} · {absoluteTime(n.created_at, timezone)}
                    </p>
                  </li>
                ))}
              </ol>
            )}
            <form onSubmit={addNote} className="mt-3" noValidate>
              <label htmlFor={`${id}-note`} className="block text-sm font-semibold">
                Add a note
              </label>
              <textarea
                id={`${id}-note`}
                value={note}
                onChange={(e) => setNote(e.target.value)}
                maxLength={4000}
                rows={3}
                className="mt-1.5 block w-full rounded-control border border-border-strong bg-surface px-3 py-2 text-base text-fg"
              />
              {noteError ? (
                <p role="alert" className="mt-1 flex items-center gap-1.5 text-sm">
                  <TriangleAlert className="size-4 text-danger" aria-hidden="true" />
                  {noteError}
                </p>
              ) : null}
              <button type="submit" className={`${buttonClass.secondary} mt-2`}>
                <MessageSquarePlus className="size-4" aria-hidden="true" />
                Add note
              </button>
            </form>
          </section>
        </div>

        <aside aria-label="Ticket details" className="space-y-4 rounded-card border border-border bg-surface p-4 lg:self-start">
          <div>
            <label htmlFor={`${id}-status`} className="block text-sm font-semibold">
              Status
            </label>
            <select
              id={`${id}-status`}
              value={ticket.status}
              disabled={saving}
              onChange={(e) => change({ status: e.target.value }, `Status: ${TICKET_STATUS[e.target.value as keyof typeof TICKET_STATUS]}.`)}
              className={select}
            >
              {Object.entries(TICKET_STATUS).map(([value, label]) => (
                <option key={value} value={value}>
                  {label}
                </option>
              ))}
            </select>
          </div>
          <div>
            <label htmlFor={`${id}-priority`} className="block text-sm font-semibold">
              Priority
            </label>
            <select
              id={`${id}-priority`}
              value={ticket.priority}
              disabled={saving}
              onChange={(e) => change({ priority: e.target.value }, "Priority changed.")}
              className={select}
            >
              {Object.entries(TICKET_PRIORITY).map(([value, label]) => (
                <option key={value} value={value}>
                  {label}
                </option>
              ))}
            </select>
          </div>
          <div>
            <label htmlFor={`${id}-assignee`} className="block text-sm font-semibold">
              Assigned to
            </label>
            <select
              id={`${id}-assignee`}
              value={ticket.assigned_to ?? ""}
              disabled={saving}
              onChange={(e) => change({ assigned_to: e.target.value || null }, e.target.value ? "Assigned." : "Unassigned.")}
              className={select}
            >
              <option value="">Unassigned</option>
              {staff.map((s) => (
                <option key={s.user_id} value={s.user_id}>
                  {s.user_id === me ? `${s.name} (you)` : s.name}
                </option>
              ))}
            </select>
            {ticket.assigned_to !== me ? (
              <button
                type="button"
                disabled={saving}
                onClick={() => change({ assigned_to: me }, "Assigned to you.")}
                className={`${buttonClass.quiet} mt-1`}
              >
                Assign to me
              </button>
            ) : null}
          </div>
          <dl className="space-y-2 border-t border-border pt-3 text-sm">
            <div>
              <dt className="text-fg-muted">Order</dt>
              <dd>{ticket.order_number ?? "None"}</dd>
            </div>
            <div>
              <dt className="text-fg-muted">Customer</dt>
              <dd>{ticket.customer_ref ?? "Not linked"}</dd>
            </div>
            {ticket.resolved_at ? (
              <div>
                <dt className="text-fg-muted">Resolved</dt>
                <dd>{absoluteTime(ticket.resolved_at, timezone)}</dd>
              </div>
            ) : null}
          </dl>
          {saving ? (
            <p className="flex items-center gap-2 text-sm text-fg-muted" role="status">
              <LoaderCircle className="size-4 animate-spin" aria-hidden="true" />
              Saving…
            </p>
          ) : null}
        </aside>
      </div>
    </>
  );
}
