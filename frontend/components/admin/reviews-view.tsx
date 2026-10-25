"use client";

import {
  CircleCheck,
  CircleSlash,
  FileText,
  Inbox,
  LoaderCircle,
  RotateCcw,
  SearchX,
  ThumbsDown,
  TriangleAlert,
} from "lucide-react";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useState, useTransition } from "react";

import { buttonClass, Modal } from "@/components/admin/modal";
import { ToastProvider, useToast } from "@/components/admin/toast";
import { When } from "@/components/admin/when";
import type { ChatError } from "@/lib/chat";
import { REVIEW_REASON, REVIEW_STATUS, type ReviewItem, type ReviewPage, type ReviewStatus } from "@/lib/reviews";

// Review queue (ADR-030): unanswered questions and answers rated not helpful, for a manager to resolve (usually by
// adding or fixing a document) or dismiss. Question, answer and note text arrive redacted from the API.

export type ReviewQuery = {
  status: ReviewStatus;
  reason: "" | "unanswered" | "not_helpful";
};
type DocumentOption = { id: string; title: string };

const STATUS_TABS: ReviewStatus[] = ["open", "resolved", "dismissed"];
const REASON_FILTERS: { value: ReviewQuery["reason"]; label: string }[] = [
  { value: "", label: "All reasons" },
  { value: "unanswered", label: REVIEW_REASON.unanswered },
  { value: "not_helpful", label: REVIEW_REASON.not_helpful },
];

export function ReviewsView(props: {
  page: ReviewPage;
  query: ReviewQuery;
  documents: DocumentOption[];
  timezone: string;
  renderedAt: number;
}) {
  return (
    <ToastProvider>
      <Queue {...props} />
    </ToastProvider>
  );
}

function Queue({
  page,
  query,
  documents,
  timezone,
  renderedAt,
}: {
  page: ReviewPage;
  query: ReviewQuery;
  documents: DocumentOption[];
  timezone: string;
  renderedAt: number;
}) {
  const router = useRouter();
  const pathname = usePathname();
  const [, startTransition] = useTransition();
  const [deciding, setDeciding] = useState<{
    item: ReviewItem;
    status: "resolved" | "dismissed";
  } | null>(null);

  function navigate(next: Partial<ReviewQuery>) {
    const merged = { ...query, ...next };
    const params = new URLSearchParams();
    if (merged.status !== "open") params.set("status", merged.status);
    if (merged.reason) params.set("reason", merged.reason);
    const search = params.toString();
    startTransition(() =>
      router.replace(search ? `${pathname}?${search}` : pathname, {
        scroll: false,
      }),
    );
  }

  return (
    <>
      <h1 className="font-heading text-2xl font-semibold">Review</h1>
      <p className="mt-1 text-sm text-fg-muted">
        Questions the documents didn&apos;t cover and answers people rated not helpful. Add or fix a document, then mark
        the question resolved. Personal details are removed before they reach this page.
      </p>
      <div className="mt-6 flex flex-wrap items-center justify-between gap-3">
        <div role="group" aria-label="Status" className="flex flex-wrap gap-1 rounded-control bg-surface-muted p-1">
          {STATUS_TABS.map((status) => {
            const active = query.status === status;
            return (
              <button
                key={status}
                type="button"
                aria-pressed={active}
                onClick={() => navigate({ status })}
                className={`inline-flex min-h-10 items-center gap-1.5 rounded-control px-3 text-sm font-semibold ${
                  active ? "bg-surface text-fg shadow-sm" : "text-fg-muted hover:text-fg"
                }`}
              >
                {REVIEW_STATUS[status]}
                <span className="tabular-nums text-fg-muted">{page.counts[status]}</span>
              </button>
            );
          })}
        </div>
        <label className="flex items-center gap-2 text-sm font-semibold">
          Reason
          <select
            value={query.reason}
            onChange={(e) => navigate({ reason: e.target.value as ReviewQuery["reason"] })}
            className="min-h-11 rounded-control border border-border-strong bg-surface px-3 text-sm font-normal text-fg"
          >
            {REASON_FILTERS.map((f) => (
              <option key={f.label} value={f.value}>
                {f.label}
              </option>
            ))}
          </select>
        </label>
      </div>

      {page.items.length === 0 ? (
        <div className="mt-6 rounded-card border border-border bg-surface p-8 text-center">
          <Inbox className="mx-auto size-6 text-fg-muted" aria-hidden="true" />
          <p className="mt-2 font-semibold">{query.status === "open" ? "Nothing to review" : "Nothing here yet"}</p>
          <p className="mt-1 text-sm text-fg-muted">
            {query.status === "open"
              ? "Every unanswered question and not-helpful answer has been dealt with."
              : "Decisions you make in the review queue appear here."}
          </p>
        </div>
      ) : (
        <ul className="mt-4 space-y-3">
          {page.items.map((item) => (
            <ReviewCard
              key={item.answer_id}
              item={item}
              timezone={timezone}
              renderedAt={renderedAt}
              onDecide={(status) => setDeciding({ item, status })}
            />
          ))}
        </ul>
      )}
      {page.total > page.items.length ? (
        <p className="mt-3 text-sm text-fg-muted">
          Showing the newest {page.items.length} of {page.total}.
        </p>
      ) : null}

      {deciding ? (
        <DecisionDialog
          item={deciding.item}
          status={deciding.status}
          documents={documents}
          onClose={() => setDeciding(null)}
          onDone={() => {
            setDeciding(null);
            router.refresh();
          }}
        />
      ) : null}
    </>
  );
}

function ReasonBadge({ reason }: { reason: ReviewItem["reasons"][number] }) {
  const Icon = reason === "unanswered" ? SearchX : ThumbsDown;
  return (
    <span className="inline-flex items-center gap-1.5 rounded-full bg-surface-muted px-2.5 py-1 text-xs font-semibold">
      <Icon className={`size-3.5 ${reason === "unanswered" ? "text-warning" : "text-danger"}`} aria-hidden="true" />
      {REVIEW_REASON[reason]}
    </span>
  );
}

function ReviewCard({
  item,
  timezone,
  renderedAt,
  onDecide,
}: {
  item: ReviewItem;
  timezone: string;
  renderedAt: number;
  onDecide: (status: "resolved" | "dismissed") => void;
}) {
  const toast = useToast();
  const router = useRouter();
  const [busy, setBusy] = useState(false);

  async function reopen() {
    setBusy(true);
    const response = await fetch(`/api/admin/reviews/${item.answer_id}`, {
      method: "DELETE",
    }).catch(() => null);
    setBusy(false);
    if (response?.ok) {
      toast("success", "Moved back to review.");
      router.refresh();
    } else {
      toast("danger", "That didn't reach us. Try again.");
    }
  }

  return (
    <li className="rounded-card border border-border bg-surface p-4">
      <div className="flex flex-wrap items-center gap-2">
        {item.reasons.map((reason) => (
          <ReasonBadge key={reason} reason={reason} />
        ))}
        <span className="text-xs text-fg-muted">
          Asked <When iso={item.asked_at} timezone={timezone} now={renderedAt} />
        </span>
      </div>
      <p className="mt-2 font-semibold">&ldquo;{item.question || "(no question text)"}&rdquo;</p>

      {item.feedback.map((f) => (
        <p key={f.created_at} className="mt-1 text-sm text-fg-muted">
          {[f.reasons.join(", "), f.note ? `“${f.note}”` : null].filter(Boolean).join(" · ") || "No reason given"}
        </p>
      ))}

      {item.answer ? (
        <details className="mt-2 text-sm">
          <summary className="inline-flex min-h-11 cursor-pointer items-center font-semibold text-accent">
            What the assistant said
          </summary>
          <p className="mt-1 whitespace-pre-line rounded-control bg-surface-muted p-3">{item.answer}</p>
        </details>
      ) : null}

      {item.status !== "open" ? (
        <div className="mt-3 rounded-control border border-border p-3 text-sm">
          <p className="flex items-center gap-2 font-semibold">
            {item.status === "resolved" ? (
              <CircleCheck className="size-4 text-success" aria-hidden="true" />
            ) : (
              <CircleSlash className="size-4 text-fg-muted" aria-hidden="true" />
            )}
            {REVIEW_STATUS[item.status]}
            {item.reviewed_by ? <span className="font-normal text-fg-muted">by {item.reviewed_by}</span> : null}
            {item.reviewed_at ? (
              <span className="font-normal text-fg-muted">
                · <When iso={item.reviewed_at} timezone={timezone} now={renderedAt} />
              </span>
            ) : null}
          </p>
          {item.document ? (
            <p className="mt-1 flex items-center gap-2">
              <FileText className="size-4 shrink-0 text-fg-muted" aria-hidden="true" />
              <Link
                href={`/admin/documents/${item.document.id}`}
                className="text-accent underline-offset-2 hover:underline"
              >
                {item.document.title}
              </Link>
            </p>
          ) : null}
          {item.note ? <p className="mt-1 text-fg-muted">{item.note}</p> : null}
        </div>
      ) : null}

      <div className="mt-3 flex flex-wrap gap-2">
        {item.status === "open" ? (
          <>
            <button type="button" onClick={() => onDecide("resolved")} className={buttonClass.secondary}>
              <CircleCheck className="size-4" aria-hidden="true" />
              Resolve
            </button>
            <button type="button" onClick={() => onDecide("dismissed")} className={buttonClass.quiet}>
              Dismiss
            </button>
          </>
        ) : (
          <button type="button" onClick={() => void reopen()} disabled={busy} className={buttonClass.quiet}>
            {busy ? (
              <LoaderCircle className="size-4 animate-spin" aria-hidden="true" />
            ) : (
              <RotateCcw className="size-4" aria-hidden="true" />
            )}
            Reopen
          </button>
        )}
      </div>
    </li>
  );
}

function DecisionDialog({
  item,
  status,
  documents,
  onClose,
  onDone,
}: {
  item: ReviewItem;
  status: "resolved" | "dismissed";
  documents: DocumentOption[];
  onClose: () => void;
  onDone: () => void;
}) {
  const toast = useToast();
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const resolving = status === "resolved";

  async function save(form: HTMLFormElement) {
    const data = new FormData(form);
    const note = String(data.get("note") ?? "").trim();
    const documentId = String(data.get("document_id") ?? "");
    setSaving(true);
    setError(null);
    const response = await fetch(`/api/admin/reviews/${item.answer_id}`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        status,
        note: note || null,
        document_id: documentId || null,
      }),
    }).catch(() => null);
    setSaving(false);
    if (response?.ok) {
      toast("success", resolving ? "Marked resolved." : "Dismissed.");
      onDone();
      return;
    }
    const body = response
      ? ((await response.json().catch(() => null)) as {
          error?: ChatError;
        } | null)
      : null;
    setError(body?.error?.message ?? "That didn't reach us. Try again.");
  }

  const input = "mt-1.5 block w-full rounded-control border border-border-strong bg-surface px-3 text-base text-fg";
  return (
    <Modal
      open
      onClose={onClose}
      title={resolving ? "Resolve this question" : "Dismiss this question"}
      description={
        resolving
          ? "Say which document now answers it, if one does. The decision is recorded with your name."
          : "Use this when nothing needs to change, for example a question outside what TechCare covers."
      }
    >
      <form
        className="space-y-4"
        onSubmit={(event) => {
          event.preventDefault();
          void save(event.currentTarget);
        }}
      >
        <p className="rounded-control bg-surface-muted p-3 text-sm">&ldquo;{item.question}&rdquo;</p>
        {resolving ? (
          <label className="block text-sm font-semibold">
            Document that answers it <span className="font-normal text-fg-muted">(optional)</span>
            <select name="document_id" defaultValue="" className={`${input} min-h-11`}>
              <option value="">No document</option>
              {documents.map((d) => (
                <option key={d.id} value={d.id}>
                  {d.title}
                </option>
              ))}
            </select>
            <span className="mt-1 block text-xs font-normal text-fg-muted">
              Not there yet?{" "}
              <Link href="/admin/documents" className="text-accent underline-offset-2 hover:underline">
                Upload it in Documents
              </Link>
              , then come back.
            </span>
          </label>
        ) : null}
        <label className="block text-sm font-semibold">
          Note <span className="font-normal text-fg-muted">(optional)</span>
          <textarea name="note" rows={3} maxLength={1000} className={`${input} py-2`} />
        </label>
        {error ? (
          <p role="alert" className="flex items-start gap-2 text-sm">
            <TriangleAlert className="mt-0.5 size-4 shrink-0 text-danger" aria-hidden="true" />
            <span>{error}</span>
          </p>
        ) : null}
        <div className="flex flex-col-reverse gap-2 border-t border-border pt-4 sm:flex-row sm:justify-end">
          <button type="button" onClick={onClose} className={buttonClass.secondary}>
            Cancel
          </button>
          <button type="submit" disabled={saving} className={buttonClass.primary}>
            {saving ? <LoaderCircle className="size-4 animate-spin" aria-hidden="true" /> : null}
            {saving ? "Saving…" : resolving ? "Mark resolved" : "Dismiss"}
          </button>
        </div>
      </form>
    </Modal>
  );
}
