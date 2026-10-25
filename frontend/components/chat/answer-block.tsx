"use client";

import {
  BadgeCheck,
  Check,
  Copy,
  FileText,
  Info,
  LifeBuoy,
  LoaderCircle,
  Lock,
  RotateCw,
  ShieldCheck,
  Ticket,
  SearchX,
  ThumbsDown,
  ThumbsUp,
  TriangleAlert,
} from "lucide-react";
import Link from "next/link";
import { useId, useState } from "react";

import { type AnswerStatus, type Citation, customerCopy, pageLabel, publicFirst, segments, sourceCount } from "@/lib/chat";
import type { AccountRecord } from "@/lib/orders";
import { SUPPORT_HREF } from "@/lib/support";

// design-system/MASTER.md §7 "Answer block": evidence status line → answer → numbered citation chips → actions.
// Nothing here renders a citation the API didn't return.

export type AnswerView = {
  answerId: string | null;
  status: AnswerStatus;
  text: string;
  citations: Citation[];
  nextAction?: "verify_order" | "show_records" | null;
  records?: AccountRecord[];
};

const chip =
  "inline-flex min-h-11 items-center gap-1.5 rounded-full border px-3 text-sm font-semibold transition-colors duration-150";

export function EvidenceLine({
  status,
  count,
  fromAccount = false,
}: {
  status: AnswerStatus;
  count: number;
  fromAccount?: boolean;
}) {
  if (fromAccount) {
    // ADR-031: answered from the caller's own orders/repairs, shown in the cards below.
    return (
      <p className="flex items-center gap-2 text-sm font-semibold text-accent">
        <ShieldCheck className="size-4" aria-hidden="true" />
        <span className="text-fg">From your TechCare account</span>
      </p>
    );
  }
  if (status === "answered") {
    return (
      <p className="flex items-center gap-2 text-sm font-semibold text-success">
        <BadgeCheck className="size-4" aria-hidden="true" />
        <span className="text-fg">Answered from {sourceCount(count)}</span>
      </p>
    );
  }
  if (status === "insufficient_evidence") {
    return (
      <p className="flex items-center gap-2 text-sm font-semibold text-warning">
        <SearchX className="size-4" aria-hidden="true" />
        <span className="text-fg">Not in our documents</span>
      </p>
    );
  }
  if (status === "direct" || status === "escalated") {
    return (
      <p className="flex items-center gap-2 text-sm font-semibold text-fg-muted">
        <Info className="size-4" aria-hidden="true" />
        General reply, no sources needed
      </p>
    );
  }
  return (
    <p className="flex items-center gap-2 text-sm font-semibold text-danger">
      <TriangleAlert className="size-4" aria-hidden="true" />
      <span className="text-fg">Something went wrong</span>
    </p>
  );
}

/** The final answer: sentences with their [n] markers as buttons that open the source. */
export function AnswerText({
  text,
  citations,
  onOpen,
}: {
  text: string;
  citations: Citation[];
  onOpen: (position: number, opener: HTMLElement) => void;
}) {
  const known = new Set(citations.map((c) => c.position));
  return (
    <p className="max-w-[68ch] text-base whitespace-pre-line text-fg">
      {segments(text).map((segment, i) => (
        <span key={i}>
          {segment.text}
          {segment.refs.filter((r) => known.has(r)).map((ref) => {
            const citation = citations.find((c) => c.position === ref);
            return (
              <button
                key={ref}
                type="button"
                onClick={(e) => onOpen(ref, e.currentTarget)}
                aria-label={`Source ${ref}: ${citation?.title ?? "document"}`}
                className="mx-0.5 inline-grid min-w-6 place-items-center rounded-full border border-accent/50 px-1 align-super text-xs leading-5 font-semibold text-accent hover:bg-accent/10"
              >
                {ref}
              </button>
            );
          })}
        </span>
      ))}
    </p>
  );
}

export function CitationChips({
  citations,
  staff,
  onOpen,
}: {
  citations: Citation[];
  staff: boolean;
  onOpen: (position: number, opener: HTMLElement) => void;
}) {
  if (citations.length === 0) return null;
  return (
    <ul className="flex flex-wrap gap-2" aria-label="Sources">
      {(staff ? publicFirst(citations) : citations).map((c) => (
        <li key={c.position} className="flex flex-wrap items-center gap-1.5">
          <button
            type="button"
            onClick={(e) => onOpen(c.position, e.currentTarget)}
            className={`${chip} border-accent/60 text-accent hover:bg-accent/10`}
          >
            <FileText className="size-4 shrink-0" aria-hidden="true" />
            <span>
              [{c.position}] {c.title ?? "Removed document"}
              {pageLabel(c) ? ` · ${pageLabel(c)}` : ""}
            </span>
          </button>
          {staff && c.audience === "employee" ? (
            <span className="inline-flex items-center gap-1 rounded-full bg-warning/12 px-2 py-0.5 text-xs font-semibold text-warning">
              <Lock className="size-3.5" aria-hidden="true" />
              <span className="text-fg">Employee only</span>
            </span>
          ) : null}
        </li>
      ))}
    </ul>
  );
}

export function NotFoundActions({ onRephrase }: { onRephrase: () => void }) {
  return (
    <div className="flex flex-wrap gap-2">
      <Link href={SUPPORT_HREF} className={primaryButton}>
        <LifeBuoy className="size-4" aria-hidden="true" />
        Contact support
      </Link>
      <button type="button" onClick={onRephrase} className={secondaryButton}>
        Rephrase question
      </button>
    </div>
  );
}

export const primaryButton =
  "inline-flex min-h-11 items-center justify-center gap-2 rounded-control bg-accent px-4 font-semibold text-on-accent transition-colors duration-150 hover:bg-accent/90 disabled:opacity-60";
export const secondaryButton =
  "inline-flex min-h-11 items-center justify-center gap-2 rounded-control border border-border-strong bg-surface px-4 font-semibold text-fg transition-colors duration-150 hover:bg-surface-muted disabled:opacity-60";
const quietButton =
  "inline-flex min-h-11 items-center gap-1.5 rounded-control px-3 text-sm font-semibold text-fg-muted transition-colors duration-150 hover:bg-surface-muted hover:text-fg disabled:opacity-50";

/** Copy, ask again, and feedback. Disabled while the answer is provisional (customer-chat.md "Streaming"). */
export function AnswerActions({
  view,
  staff,
  disabled,
  cooldown,
  onAskAgain,
  onCreateTicket,
}: {
  view: AnswerView;
  staff: boolean;
  disabled: boolean;
  cooldown: number;
  onAskAgain: () => void;
  onCreateTicket?: () => void; // staff only (ADR-029)
}) {
  const [copied, setCopied] = useState(false);
  const forCustomer = staff ? customerCopy({ answer: view.text, citations: view.citations }) : null;

  async function copy() {
    const text = staff
      ? forCustomer
      : `${view.text}\n\n${view.citations.map((c) => `[${c.position}] ${[c.title, pageLabel(c)].filter(Boolean).join(", ")}`).join("\n")}`.trim();
    if (!text) return;
    try {
      await navigator.clipboard.writeText(text);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    } catch {
      setCopied(false);
    }
  }

  return (
    <div className="flex flex-wrap items-center gap-1 border-t border-border pt-2">
      <button
        type="button"
        onClick={copy}
        disabled={disabled || (staff && !forCustomer)}
        className={quietButton}
        title={staff && !forCustomer ? "No sentence here comes only from public sources." : undefined}
      >
        {copied ? <Check className="size-4" aria-hidden="true" /> : <Copy className="size-4" aria-hidden="true" />}
        {copied ? "Copied" : staff ? "Copy for customer" : "Copy answer"}
      </button>
      <button type="button" onClick={onAskAgain} disabled={disabled || cooldown > 0} className={quietButton}>
        <RotateCw className="size-4" aria-hidden="true" />
        {cooldown > 0 ? `Ask again in ${cooldown} s` : "Ask again"}
      </button>
      {staff && onCreateTicket ? (
        <button type="button" onClick={onCreateTicket} disabled={disabled} className={quietButton}>
          <Ticket className="size-4" aria-hidden="true" />
          Create ticket
        </button>
      ) : null}
      {view.answerId ? <Feedback answerId={view.answerId} disabled={disabled} /> : null}
    </div>
  );
}

const REASONS = ["Wrong", "Outdated", "Incomplete", "Didn't answer"] as const;

function Feedback({ answerId, disabled }: { answerId: string; disabled: boolean }) {
  const id = useId();
  const [state, setState] = useState<"idle" | "explaining" | "sending" | "sent" | "failed">("idle");
  const [rating, setRating] = useState<"helpful" | "not_helpful" | null>(null);
  const [reasons, setReasons] = useState<string[]>([]);
  const [note, setNote] = useState("");

  async function send(value: "helpful" | "not_helpful", extra?: { reasons: string[]; note: string }) {
    setRating(value);
    setState("sending");
    const response = await fetch(`/api/answers/${answerId}/feedback`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ rating: value, reasons: extra?.reasons ?? [], note: extra?.note.trim() || null }),
    }).catch(() => null);
    setState(response?.ok ? "sent" : "failed");
  }

  if (state === "sent") {
    return (
      <p role="status" className="ml-auto flex items-center gap-1.5 px-3 text-sm text-fg-muted">
        <Check className="size-4 text-success" aria-hidden="true" />
        Thanks for your feedback.
      </p>
    );
  }

  return (
    <div className="ml-auto flex flex-wrap items-center gap-1">
      <button
        type="button"
        aria-pressed={rating === "helpful"}
        disabled={disabled || state === "sending"}
        onClick={() => send("helpful")}
        className={quietButton}
      >
        <ThumbsUp className="size-4" aria-hidden="true" />
        Helpful
      </button>
      <button
        type="button"
        aria-pressed={state === "explaining"}
        aria-expanded={state === "explaining"}
        aria-controls={`${id}-why`}
        disabled={disabled || state === "sending"}
        onClick={() => setState(state === "explaining" ? "idle" : "explaining")}
        className={quietButton}
      >
        <ThumbsDown className="size-4" aria-hidden="true" />
        Not helpful
      </button>
      {state === "failed" ? (
        <p role="alert" className="w-full px-3 text-sm text-danger">
          We couldn&apos;t send your feedback. Try again.
        </p>
      ) : null}
      {state === "explaining" || (state === "sending" && rating === "not_helpful") ? (
        <form
          id={`${id}-why`}
          className="mt-2 w-full space-y-3 rounded-control border border-border bg-surface-muted p-3"
          onSubmit={(e) => {
            e.preventDefault();
            void send("not_helpful", { reasons, note });
          }}
        >
          <p className="text-sm">Sorry this didn&apos;t help. Want to tell us what was missing?</p>
          <fieldset>
            <legend className="sr-only">What was wrong</legend>
            <div className="flex flex-wrap gap-2">
              {REASONS.map((reason) => {
                const on = reasons.includes(reason);
                return (
                  <button
                    key={reason}
                    type="button"
                    aria-pressed={on}
                    onClick={() => setReasons(on ? reasons.filter((r) => r !== reason) : [...reasons, reason])}
                    className={`${chip} ${on ? "border-accent bg-accent/12 text-accent" : "border-border-strong text-fg"}`}
                  >
                    {on ? <Check className="size-4" aria-hidden="true" /> : null}
                    {reason}
                  </button>
                );
              })}
            </div>
          </fieldset>
          <div>
            <label htmlFor={`${id}-note`} className="text-sm font-semibold">
              Details <span className="font-normal text-fg-muted">(optional)</span>
            </label>
            <textarea
              id={`${id}-note`}
              value={note}
              onChange={(e) => setNote(e.target.value)}
              maxLength={2000}
              rows={3}
              className="mt-1.5 block w-full rounded-control border border-border-strong bg-surface p-3 text-base text-fg"
            />
          </div>
          <div className="flex flex-wrap items-center gap-2">
            <button type="submit" disabled={state === "sending"} className={secondaryButton}>
              {state === "sending" ? <LoaderCircle className="size-4 animate-spin" aria-hidden="true" /> : null}
              Send feedback
            </button>
            <span className="text-sm text-fg-muted">or</span>
            <Link href={SUPPORT_HREF} className="text-sm font-semibold text-accent underline-offset-2 hover:underline">
              Contact support
            </Link>
          </div>
        </form>
      ) : null}
    </div>
  );
}
