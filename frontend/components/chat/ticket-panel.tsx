"use client";

import { Info, LoaderCircle, Ticket, TriangleAlert } from "lucide-react";
import Link from "next/link";
import { useEffect, useId, useState, type FormEvent } from "react";

import { buttonClass, Modal } from "@/components/admin/modal";
import type { ChatError } from "@/lib/chat";
import type { Ticket as TicketRecord, TicketDraft } from "@/lib/tickets";

// Ticket handoff (employee-assistant.md, content-guide.md "Employee ticket"): the assistant drafts a summary of
// this conversation; the employee reviews and edits it before anything is saved (ADR-029).

const input = "mt-1.5 block w-full rounded-control border border-border-strong bg-surface px-3 text-base text-fg";

export function TicketPanel({ conversationId, onClose }: { conversationId: string; onClose: () => void }) {
  const id = useId();
  const [draft, setDraft] = useState<TicketDraft | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [created, setCreated] = useState<TicketRecord | null>(null);

  useEffect(() => {
    let cancelled = false;
    fetch("/api/tickets/draft", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ conversation_id: conversationId }),
    })
      .then(async (r) => {
        const body = (await r.json()) as TicketDraft | { error: ChatError };
        if (cancelled) return;
        if ("error" in body) setLoadError(body.error.message);
        else setDraft(body);
      })
      .catch(() => {
        if (!cancelled) setLoadError("That didn't reach us. Close this and try again.");
      });
    return () => {
      cancelled = true;
    };
  }, [conversationId]);

  async function save(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    setSaving(true);
    setError(null);
    const response = await fetch("/api/tickets", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        subject: String(form.get("subject") ?? "").trim(),
        summary: String(form.get("summary") ?? "").trim(),
        priority: String(form.get("priority") ?? "normal"),
        order_number: String(form.get("order_number") ?? "").trim() || null,
        conversation_id: conversationId,
        summary_drafted: Boolean(draft?.drafted_by_model),
      }),
    }).catch(() => null);
    setSaving(false);
    const body = response ? ((await response.json().catch(() => null)) as TicketRecord | { error: ChatError } | null) : null;
    if (response?.ok && body && !("error" in body)) {
      setCreated(body);
      return;
    }
    setError(
      response?.status === 422
        ? "Add a subject and a summary."
        : (body && "error" in body ? body.error.message : null) ?? "That didn't reach us. Try again.",
    );
  }

  return (
    <Modal
      open
      onClose={onClose}
      wide
      title={created ? `Ticket ${created.reference} created` : "Create ticket"}
      description={created ? undefined : "Review the summary before sending. The support team sees what you send."}
    >
      {created ? (
        <div className="space-y-4">
          <p className="flex items-start gap-2">
            <Ticket className="mt-0.5 size-5 shrink-0 text-success" aria-hidden="true" />
            <span>
              {created.reference} is in the support queue as {created.priority} priority.
            </span>
          </p>
          <div className="flex flex-col-reverse gap-2 sm:flex-row sm:justify-end">
            <button type="button" onClick={onClose} className={buttonClass.secondary}>
              Back to the conversation
            </button>
            <Link href={`/admin/tickets/${created.id}`} className={buttonClass.primary}>
              Open the ticket
            </Link>
          </div>
        </div>
      ) : loadError ? (
        <p role="alert" className="flex items-start gap-2 text-sm">
          <TriangleAlert className="mt-0.5 size-4 shrink-0 text-danger" aria-hidden="true" />
          {loadError}
        </p>
      ) : !draft ? (
        <p className="flex items-center gap-2 text-sm text-fg-muted" role="status">
          <LoaderCircle className="size-4 animate-spin" aria-hidden="true" />
          Drafting a summary of this conversation…
        </p>
      ) : (
        <form onSubmit={save} className="space-y-4" noValidate>
          <p className="flex items-start gap-2 rounded-control bg-surface-muted p-3 text-sm">
            <Info className="mt-0.5 size-4 shrink-0 text-accent" aria-hidden="true" />
            {draft.drafted_by_model
              ? "The assistant drafted this from the conversation. Check it says what happened, and edit anything that's wrong."
              : "The assistant couldn't draft a summary just now, so this is a plain outline. Edit it before saving."}
          </p>
          <div>
            <label htmlFor={`${id}-subject`} className="block text-sm font-semibold">
              Subject
            </label>
            <input
              id={`${id}-subject`}
              name="subject"
              required
              maxLength={200}
              defaultValue={draft.subject}
              className={`${input} min-h-11`}
            />
          </div>
          <div>
            <label htmlFor={`${id}-summary`} className="block text-sm font-semibold">
              Summary
            </label>
            <textarea
              id={`${id}-summary`}
              name="summary"
              required
              maxLength={4000}
              rows={7}
              defaultValue={draft.summary}
              className={`${input} py-2`}
            />
          </div>
          <div className="grid gap-4 sm:grid-cols-2">
            <div>
              <label htmlFor={`${id}-priority`} className="block text-sm font-semibold">
                Priority
              </label>
              <select id={`${id}-priority`} name="priority" defaultValue="normal" className={`${input} min-h-11`}>
                <option value="low">Low</option>
                <option value="normal">Normal</option>
                <option value="high">High</option>
              </select>
            </div>
            <div>
              <label htmlFor={`${id}-order`} className="block text-sm font-semibold">
                Order number
              </label>
              <input
                id={`${id}-order`}
                name="order_number"
                maxLength={20}
                placeholder="Optional, e.g. TC-123456"
                className={`${input} min-h-11`}
              />
            </div>
          </div>
          {error ? (
            <p role="alert" className="flex items-start gap-2 text-sm">
              <TriangleAlert className="mt-0.5 size-4 shrink-0 text-danger" aria-hidden="true" />
              {error}
            </p>
          ) : null}
          <div className="flex flex-col-reverse gap-2 border-t border-border pt-4 sm:flex-row sm:justify-end">
            <button type="button" onClick={onClose} className={buttonClass.secondary}>
              Cancel
            </button>
            <button type="submit" disabled={saving} className={buttonClass.primary}>
              {saving ? <LoaderCircle className="size-4 animate-spin" aria-hidden="true" /> : <Ticket className="size-4" aria-hidden="true" />}
              {saving ? "Sending…" : "Send to support"}
            </button>
          </div>
        </form>
      )}
    </Modal>
  );
}
