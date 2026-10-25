// Chat shapes and pure helpers (backend/app/rag/schemas.py, app/api/chat.py). Type-only imports, so the Node
// test runner can load this file directly (`npm test`), and it is safe in client and server code alike.

import type { AccountRecord } from "./orders.ts";

export type AnswerStatus = "answered" | "insufficient_evidence" | "escalated" | "error" | "direct";
export type Stage = "searching" | "rewriting" | "checking";

export type Citation = {
  position: number;
  document_id: string | null;
  version_id: string | null;
  title: string | null;
  page_start: number | null;
  page_end: number | null;
  section?: string | null;
  quote: string | null;
  audience: string | null;
  preview_url: string | null;
};

export type AskResult = {
  request_id: string;
  trace_id: string;
  conversation_id: string;
  answer_id: string;
  answer_status: AnswerStatus;
  grounded: boolean;
  answer: string;
  citations: Citation[];
  suggested_followups: string[];
  // "verify_order": show the verification card (US-05); "show_records": the answer came from the caller's own
  // orders/repairs in `records` (ADR-031).
  next_action?: "verify_order" | "show_records" | null;
  records?: AccountRecord[];
};

export type ChatError = {
  code: string;
  message: string;
  request_id: string;
  details?: Record<string, unknown>;
  retryable?: boolean;
};

export type StreamEvent =
  | { event: "meta"; data: { request_id: string; trace_id: string } }
  | { event: "status"; data: { stage: Stage } }
  | { event: "delta"; data: { text: string } }
  | { event: "final"; data: AskResult }
  | { event: "error"; data: ChatError };

export type ConversationSummary = { id: string; title: string; updated_at: string; message_count: number };

export type StoredMessage = {
  id: string;
  role: "user" | "assistant";
  content: string;
  created_at: string;
  answer_id: string | null;
  answer_status: AnswerStatus | null;
  grounded: boolean | null;
  citations: Citation[];
};

export type Conversation = { id: string; created_at: string; updated_at: string; messages: StoredMessage[] };

// ---------- SSE (text/event-stream) ----------

const EVENTS = new Set(["meta", "status", "delta", "final", "error"]);

/** Incremental parser: feed it decoded text as it arrives; it returns the complete events so far. */
export function createSseParser(): (chunk: string) => StreamEvent[] {
  let buffer = "";
  return (chunk) => {
    buffer += chunk.replaceAll("\r\n", "\n");
    const events: StreamEvent[] = [];
    let boundary = buffer.indexOf("\n\n");
    while (boundary !== -1) {
      const block = buffer.slice(0, boundary);
      buffer = buffer.slice(boundary + 2);
      boundary = buffer.indexOf("\n\n");
      let name = "message";
      const data: string[] = [];
      for (const line of block.split("\n")) {
        if (line.startsWith(":")) continue; // comment / keep-alive
        const colon = line.indexOf(":");
        const field = colon === -1 ? line : line.slice(0, colon);
        const value = colon === -1 ? "" : line.slice(colon + 1).replace(/^ /, "");
        if (field === "event") name = value;
        else if (field === "data") data.push(value);
      }
      if (!EVENTS.has(name) || data.length === 0) continue;
      try {
        events.push({ event: name, data: JSON.parse(data.join("\n")) } as StreamEvent);
      } catch {
        // A malformed event is dropped; only `final` is authoritative anyway (ADR-006).
      }
    }
    return events;
  };
}

// ---------- answer text and citations ----------

export type Segment = { text: string; refs: number[] };

const MARKER = /\s*\[(?:S)?(\d+)\]/g;

/** "A. [1] B. [1][2]" → [{text: "A.", refs: [1]}, {text: " B.", refs: [1, 2]}]. */
export function segments(answer: string): Segment[] {
  const out: Segment[] = [];
  let last = 0;
  let current: Segment | null = null;
  for (const match of answer.matchAll(MARKER)) {
    const index = match.index ?? 0;
    const text = answer.slice(last, index);
    if (text.trim() || !current) {
      current = { text, refs: [] };
      out.push(current);
    }
    current.refs.push(Number(match[1]));
    last = index + match[0].length;
  }
  const tail = answer.slice(last);
  if (tail.trim()) out.push({ text: tail, refs: [] });
  return out;
}

/** Provisional `delta` text still carries the model's [S1] markers: hide them until `final`. */
export function stripMarkers(text: string): string {
  return text.replace(MARKER, "").trim();
}

/** "p. 17" or "p. 17–18" (content-guide.md "Citations"). */
export function pageLabel(citation: Pick<Citation, "page_start" | "page_end">): string | null {
  const { page_start: start, page_end: end } = citation;
  if (!start) return null;
  return end && end !== start ? `p. ${start}–${end}` : `p. ${start}`;
}

export function sourceCount(n: number): string {
  return n === 1 ? "1 source" : `${n} sources`;
}

/** Public sources first (employee-assistant.md), keeping each citation's number. */
export function publicFirst(citations: Citation[]): Citation[] {
  return [...citations].sort(
    (a, b) => Number(a.audience === "employee") - Number(b.audience === "employee") || a.position - b.position,
  );
}

/**
 * "Copy for customer" (employee-assistant.md): only sentences backed solely by public sources, plus those
 * sources. Sentences without a marker are left out too: nothing proves they aren't from an internal source.
 */
export function customerCopy(result: Pick<AskResult, "answer" | "citations">): string | null {
  const publicRefs = new Set(result.citations.filter((c) => c.audience === "public").map((c) => c.position));
  const kept = segments(result.answer).filter((s) => s.refs.length > 0 && s.refs.every((r) => publicRefs.has(r)));
  if (kept.length === 0) return null;
  const used = [...new Set(kept.flatMap((s) => s.refs))].sort((a, b) => a - b);
  const text = kept.map((s) => `${s.text.trim()} ${s.refs.map((r) => `[${r}]`).join("")}`).join(" ");
  const sources = used
    .map((position) => result.citations.find((c) => c.position === position))
    .filter((c): c is Citation => c !== undefined)
    .map((c) => `[${c.position}] ${[c.title, pageLabel(c)].filter(Boolean).join(", ")}`);
  return `${text}\n\nSources:\n${sources.join("\n")}`;
}

/** Seconds to wait from a 429 body, else from Retry-After, else a safe default. */
export function retryAfterSeconds(error: ChatError | null, header: string | null): number {
  const fromBody = Number(error?.details?.retry_after_seconds);
  if (Number.isFinite(fromBody) && fromBody > 0) return Math.ceil(fromBody);
  const fromHeader = Number(header);
  return Number.isFinite(fromHeader) && fromHeader > 0 ? Math.ceil(fromHeader) : 30;
}

/** The BFF serves files under /api/files; the API's link is /api/v1/files. */
export function bffFileUrl(apiUrl: string): string {
  return apiUrl.replace(/^\/api\/v1\/files\//, "/api/files/");
}

/** `/api/v1/documents/{id}/preview?…` (citation `preview_url`) → the BFF route the browser may call. */
export function bffPreviewUrl(apiUrl: string): string | null {
  const match = /^\/api\/v1\/documents\/([0-9a-f-]{36})\/preview(\?[^#]*)?$/.exec(apiUrl);
  return match ? `/api/documents/${match[1]}/preview${match[2] ?? ""}` : null;
}
