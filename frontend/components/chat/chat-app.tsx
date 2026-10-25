"use client";

import {
  ArrowUp,
  Filter,
  History,
  Hourglass,
  LifeBuoy,
  LoaderCircle,
  MessageSquarePlus,
  Trash2,
  TriangleAlert,
  WifiOff,
  X,
} from "lucide-react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useCallback, useEffect, useId, useRef, useState, type KeyboardEvent } from "react";

import { buttonClass, Modal } from "@/components/admin/modal";
import {
  AnswerActions,
  AnswerText,
  CitationChips,
  EvidenceLine,
  NotFoundActions,
  primaryButton,
  secondaryButton,
  type AnswerView,
} from "@/components/chat/answer-block";
import { SourcePanel } from "@/components/chat/source-panel";
import {
  type AskResult,
  type ChatError,
  type Conversation,
  type ConversationSummary,
  createSseParser,
  retryAfterSeconds,
  sourceCount,
  type Stage,
  stripMarkers,
} from "@/lib/chat";
import { recordNumberIn } from "@/lib/orders";
import { SUPPORT_HREF } from "@/lib/support";
import { OrderCheck, RecordCards } from "@/components/chat/order-check";
import { TicketPanel } from "@/components/chat/ticket-panel";
import { IllosAiChatEmptyState } from "@/components/ui/illos-ai-chat-empty-state";

// design-system/pages/customer-chat.md and employee-assistant.md. One component, two variants:
// customer = low density, no filters; staff = filter bar, Employee-only badges, "Copy for customer".

// The assistant's filters (employee-assistant.md, §10.2): they only narrow what the caller may already see.
export type AskFilters = { product_model?: string; brand?: string; category?: string; file_type?: string };

const FILE_TYPES: Record<string, string> = { pdf: "PDF", docx: "Word", txt: "Text" };

function describeFilters(filters: AskFilters): string {
  return [filters.product_model, filters.brand, filters.category, filters.file_type && FILE_TYPES[filters.file_type]]
    .filter(Boolean)
    .join(" · ");
}

function activeFilters(filters: AskFilters): AskFilters | null {
  const kept = Object.fromEntries(Object.entries(filters).filter(([, v]) => v)) as AskFilters;
  return Object.keys(kept).length ? kept : null;
}

type Turn = {
  key: string;
  question: string;
  filters: AskFilters | null;
  phase: "offline" | "thinking" | "streaming" | "final" | "error";
  stage?: Stage;
  provisional?: string;
  answer?: AnswerView;
  error?: ChatError;
  slow?: boolean;
};

const MAX_CHARS = 1000;
const SLOW_MS = 15_000;

const SUGGESTIONS = {
  customer: [
    "How do I return an opened item?",
    "What does the laptop warranty cover?",
    "What does error code E-04 mean?",
    "When is the repair desk open?",
  ],
  staff: [
    "When can we waive the diagnostic fee?",
    "When should we offer a loaner laptop?",
    "How do I clear a paper jam in the DemoPrinter-X2?",
    "What is the restocking fee for an opened laptop?",
  ],
};

export type ChatAppProps = {
  variant: "customer" | "staff";
  basePath: "/chat" | "/assistant";
  signedIn: boolean;
  initial: Conversation | null;
  history: ConversationSummary[];
  products?: string[];
  brands?: string[];
  categories?: string[];
};

function fromHistory(conversation: Conversation | null): Turn[] {
  if (!conversation) return [];
  const turns: Turn[] = [];
  for (const message of conversation.messages) {
    if (message.role === "user") {
      turns.push({ key: message.id, question: message.content, filters: null, phase: "final" });
    } else if (turns.length && !turns[turns.length - 1].answer) {
      turns[turns.length - 1].answer = {
        answerId: message.answer_id,
        status: message.answer_status ?? "error",
        text: message.content,
        citations: message.citations,
      };
    }
  }
  return turns.map((t) => (t.answer ? t : { ...t, phase: "error", error: undefined }));
}

function errorFrom(code: string, message?: string, requestId = "bff"): ChatError {
  return { code, message: message ?? "", request_id: requestId };
}

export function ChatApp({
  variant,
  basePath,
  signedIn,
  initial,
  history,
  products = [],
  brands = [],
  categories = [],
}: ChatAppProps) {
  const staff = variant === "staff";
  const router = useRouter();
  const composerId = useId();
  const [turns, setTurns] = useState<Turn[]>(() => fromHistory(initial));
  const [conversationId, setConversationId] = useState<string | null>(initial?.id ?? null);
  const conversationRef = useRef<string | null>(initial?.id ?? null);
  const [draft, setDraft] = useState("");
  const [filters, setFilters] = useState<AskFilters>({});
  const [blockedUntil, setBlockedUntil] = useState(0);
  const [now, setNow] = useState(() => Date.now());
  const [online, setOnline] = useState(true);
  const [announcement, setAnnouncement] = useState("");
  const [panel, setPanel] = useState<{ turn: string; position: number } | null>(null);
  const [confirm, setConfirm] = useState<"delete" | "clear" | null>(null);
  const [historyOpen, setHistoryOpen] = useState(false);
  const [ticketOpen, setTicketOpen] = useState(false);
  const opener = useRef<HTMLElement | null>(null);
  const composer = useRef<HTMLTextAreaElement>(null);
  const endRef = useRef<HTMLDivElement>(null);
  const controllers = useRef(new Map<string, AbortController>());

  const cooldown = Math.max(0, Math.ceil((blockedUntil - now) / 1000));
  const busy = turns.some((t) => t.phase === "thinking" || t.phase === "streaming");

  // Countdown for the rate limit (customer-chat.md "Rate-limited": visible text, not just a grey button).
  useEffect(() => {
    if (blockedUntil <= Date.now()) return;
    const timer = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(timer);
  }, [blockedUntil]);

  const update = useCallback((key: string, patch: Partial<Turn>) => {
    setTurns((all) => all.map((t) => (t.key === key ? { ...t, ...patch } : t)));
  }, []);

  const run = useCallback(
    async (key: string, question: string, filters: AskFilters | null) => {
      const controller = new AbortController();
      controllers.current.set(key, controller);
      update(key, { phase: "thinking", stage: "searching", provisional: undefined, error: undefined, slow: false });
      const slow = setTimeout(() => update(key, { slow: true }), SLOW_MS);
      let requestId = "unknown";
      try {
        const response = await fetch("/api/chat/stream", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ question, conversation_id: conversationRef.current, ...filters }),
          signal: controller.signal,
        });
        if (!response.ok || !response.body) {
          const body = (await response.json().catch(() => null)) as { error?: ChatError } | null;
          const error = body?.error ?? errorFrom("BAD_GATEWAY");
          if (response.status === 429) {
            const wait = retryAfterSeconds(error, response.headers.get("retry-after"));
            setBlockedUntil(Date.now() + wait * 1000);
            setNow(Date.now());
          }
          if (response.status === 404 && conversationRef.current) {
            conversationRef.current = null; // the conversation was deleted elsewhere: the next try starts anew
          }
          update(key, { phase: "error", error });
          setAnnouncement(response.status === 429 ? "Too many questions. Wait a moment, then ask again." : "Something went wrong");
          return;
        }
        const reader = response.body.pipeThrough(new TextDecoderStream()).getReader();
        const parse = createSseParser();
        let final: AskResult | null = null;
        for (;;) {
          const { value, done } = await reader.read();
          if (done) break;
          for (const event of parse(value)) {
            if (event.event === "meta") requestId = event.data.request_id;
            else if (event.event === "status") update(key, { stage: event.data.stage });
            else if (event.event === "delta") update(key, { phase: "streaming", provisional: event.data.text });
            else if (event.event === "error") update(key, { phase: "error", error: event.data });
            else if (event.event === "final") final = event.data;
          }
        }
        if (!final) {
          update(key, { phase: "error", error: errorFrom("STREAM_ENDED", undefined, requestId) });
          setAnnouncement("Something went wrong");
          return;
        }
        const result: AskResult = final;
        update(key, {
          phase: "final",
          answer: {
            answerId: result.answer_id,
            status: result.answer_status,
            text: result.answer,
            citations: result.citations,
            nextAction: result.next_action ?? null,
            records: result.records ?? [],
          },
        });
        setAnnouncement(
          result.answer_status === "answered"
            ? `Answer ready, from ${sourceCount(result.citations.length)}`
            : result.answer_status === "insufficient_evidence"
              ? "Not in our documents"
              : "Answer ready",
        );
        if (result.conversation_id !== conversationRef.current) {
          conversationRef.current = result.conversation_id;
          setConversationId(result.conversation_id);
          window.history.replaceState(null, "", `${basePath}/${result.conversation_id}`); // deep link
        }
      } catch (error) {
        if (controller.signal.aborted) return;
        update(key, { phase: "error", error: errorFrom("NETWORK", String(error), requestId) });
        setAnnouncement("Something went wrong");
      } finally {
        clearTimeout(slow);
        controllers.current.delete(key);
      }
    },
    [basePath, update],
  );

  // Offline: hold questions and send them on reconnect (content-guide.md "Offline").
  const turnsRef = useRef(turns);
  useEffect(() => {
    turnsRef.current = turns;
  }, [turns]);
  useEffect(() => {
    const goOnline = () => {
      setOnline(true);
      for (const turn of turnsRef.current) {
        if (turn.phase === "offline") void run(turn.key, turn.question, turn.filters);
      }
    };
    const goOffline = () => setOnline(false);
    if (!navigator.onLine) goOffline();
    window.addEventListener("online", goOnline);
    window.addEventListener("offline", goOffline);
    return () => {
      window.removeEventListener("online", goOnline);
      window.removeEventListener("offline", goOffline);
    };
  }, [run]);

  useEffect(() => {
    endRef.current?.scrollIntoView({ block: "end", behavior: "smooth" });
  }, [turns.length]);

  function ask(text: string, chosen: AskFilters | null = staff ? activeFilters(filters) : null) {
    const question = text.trim();
    if (!question || question.length > MAX_CHARS || cooldown > 0) return;
    const key = crypto.randomUUID();
    setTurns((all) => [...all, { key, question, filters: chosen, phase: online ? "thinking" : "offline" }]);
    setDraft("");
    if (online) void run(key, question, chosen);
  }

  function retry(turn: Turn, chosen = turn.filters) {
    controllers.current.get(turn.key)?.abort();
    if (chosen !== turn.filters) update(turn.key, { filters: chosen });
    void run(turn.key, turn.question, chosen);
  }

  function rephrase(question: string) {
    setDraft(question);
    composer.current?.focus();
  }

  function openSource(turn: string, position: number, element: HTMLElement) {
    opener.current = element;
    setPanel({ turn, position });
  }

  const closeSource = useCallback(() => {
    setPanel(null);
    opener.current?.focus(); // MASTER.md: focus returns to the chip
  }, []);

  function newChat() {
    controllers.current.forEach((c) => c.abort());
    setTurns([]);
    setPanel(null);
    conversationRef.current = null;
    setConversationId(null);
    window.history.replaceState(null, "", basePath);
    composer.current?.focus();
  }

  async function removeConversations(kind: "delete" | "clear") {
    const url = kind === "delete" && conversationId ? `/api/conversations/${conversationId}` : "/api/conversations";
    const response = await fetch(url, { method: "DELETE" }).catch(() => null);
    setConfirm(null);
    if (response && (response.ok || response.status === 404)) {
      newChat();
      router.refresh(); // the history list
    }
  }

  function onKeyDown(event: KeyboardEvent<HTMLTextAreaElement>) {
    if (event.key === "Enter" && !event.shiftKey && !event.nativeEvent.isComposing) {
      event.preventDefault();
      ask(draft);
    }
  }

  const panelTurn = panel ? turns.find((t) => t.key === panel.turn) : undefined;
  const width = staff ? "max-w-[840px]" : "max-w-3xl";

  return (
    <div className="flex min-h-0 flex-1">
      <div className="flex min-w-0 flex-1 flex-col">
        {/* Conversation menu (customer-chat.md): new chat, history for signed-in users, delete / clear. */}
        <div className={`mx-auto flex w-full ${width} flex-wrap items-center gap-2 px-4 pt-4`}>
          {staff ? (
            <AssistantFilters
              options={{ product_model: products, brand: brands, category: categories }}
              value={filters}
              onChange={setFilters}
            />
          ) : null}
          <div className="ml-auto flex flex-wrap items-center gap-1">
            {signedIn && history.length ? (
              <button
                type="button"
                aria-expanded={historyOpen}
                aria-controls="chat-history"
                onClick={() => setHistoryOpen(!historyOpen)}
                className={buttonClass.quiet}
              >
                <History className="size-4" aria-hidden="true" />
                History
              </button>
            ) : null}
            {turns.length ? (
              <button type="button" onClick={newChat} className={buttonClass.quiet}>
                <MessageSquarePlus className="size-4" aria-hidden="true" />
                New chat
              </button>
            ) : null}
            {conversationId ? (
              <button type="button" onClick={() => setConfirm("delete")} className={buttonClass.quiet}>
                <Trash2 className="size-4" aria-hidden="true" />
                Delete chat
              </button>
            ) : null}
          </div>
          {historyOpen ? (
            <nav id="chat-history" aria-label="Your conversations" className="w-full rounded-card border border-border bg-surface p-2">
              <ul className="max-h-64 overflow-y-auto">
                {history.map((c) => (
                  <li key={c.id}>
                    <Link
                      href={`${basePath}/${c.id}`}
                      aria-current={c.id === conversationId ? "page" : undefined}
                      className="flex min-h-11 items-center justify-between gap-3 rounded-control px-3 text-sm hover:bg-surface-muted aria-[current=page]:font-semibold"
                    >
                      <span className="truncate">{c.title}</span>
                      <span className="shrink-0 text-xs text-fg-muted tabular-nums">
                        {Math.ceil(c.message_count / 2)} {c.message_count > 2 ? "questions" : "question"}
                      </span>
                    </Link>
                  </li>
                ))}
              </ul>
              <div className="border-t border-border pt-2">
                <button
                  type="button"
                  onClick={() => setConfirm("clear")}
                  className="inline-flex min-h-11 items-center gap-1.5 rounded-control px-3 text-sm font-semibold text-danger hover:bg-danger/10"
                >
                  <Trash2 className="size-4" aria-hidden="true" />
                  Clear history
                </button>
              </div>
            </nav>
          ) : null}
        </div>

        <div className={`mx-auto w-full ${width} flex-1 px-4 pt-4 pb-40`}>
          {turns.length === 0 ? (
            <EmptyState staff={staff} onPick={(q) => ask(q)} />
          ) : (
            <div role="log" aria-live="off" aria-busy={busy} aria-label="Conversation" className={staff ? "space-y-4" : "space-y-6"}>
              {turns.map((turn) => (
                <TurnView
                  key={turn.key}
                  turn={turn}
                  staff={staff}
                  signedIn={signedIn}
                  conversationId={conversationId}
                  cooldown={cooldown}
                  onCreateTicket={staff && conversationId ? () => setTicketOpen(true) : undefined}
                  onOpen={(position, el) => openSource(turn.key, position, el)}
                  onRetry={(chosen) => retry(turn, chosen)}
                  onRephrase={() => rephrase(turn.question)}
                  onClearFilter={() => {
                    setFilters({});
                    retry(turn, null);
                  }}
                  onKeepWaiting={() => update(turn.key, { slow: false })}
                />
              ))}
            </div>
          )}
          <div ref={endRef} />
          <p className="sr-only" aria-live="polite">
            {announcement}
          </p>
        </div>

        {/* Sticky composer with safe-area padding (customer-chat.md "Layout"). */}
        <div className="sticky bottom-0 z-20 border-t border-border bg-bg/95 pb-[max(env(safe-area-inset-bottom),12px)] backdrop-blur-sm">
          <form
            className={`mx-auto w-full ${width} px-4 pt-3`}
            onSubmit={(e) => {
              e.preventDefault();
              ask(draft);
            }}
          >
            {!online ? (
              <p role="status" className="mb-2 flex items-center gap-2 text-sm text-fg-muted">
                <WifiOff className="size-4" aria-hidden="true" />
                You&apos;re offline. We&apos;ll send your question when you reconnect.
              </p>
            ) : null}
            {cooldown > 0 ? (
              <p role="status" className="mb-2 text-sm text-warning">
                <span className="text-fg">
                  You&apos;ve asked a lot of questions quickly. You can ask again in {cooldown} seconds.
                </span>
              </p>
            ) : null}
            <div className="flex items-end gap-2 rounded-card border border-border-strong bg-surface p-2 focus-within:outline-2 focus-within:outline-offset-2 focus-within:outline-ring">
              <label htmlFor={composerId} className="sr-only">
                {staff ? "Ask about procedures and products" : "Ask TechCare a question"}
              </label>
              <textarea
                id={composerId}
                ref={composer}
                value={draft}
                onChange={(e) => setDraft(e.target.value)}
                onKeyDown={onKeyDown}
                rows={1}
                maxLength={MAX_CHARS}
                placeholder={staff ? "Ask about a procedure, policy or product…" : "Ask about returns, warranties, repairs…"}
                aria-describedby={`${composerId}-hint`}
                className="max-h-40 min-h-11 flex-1 resize-none bg-transparent px-2 py-2.5 text-base text-fg outline-none placeholder:text-fg-muted/80 [field-sizing:content]"
              />
              <button
                type="submit"
                disabled={!draft.trim() || cooldown > 0}
                aria-label="Send question"
                className="grid size-11 shrink-0 place-items-center rounded-control bg-accent text-on-accent transition-colors duration-150 hover:bg-accent/90 disabled:opacity-50"
              >
                {busy ? (
                  <LoaderCircle className="size-5 animate-spin" aria-hidden="true" />
                ) : (
                  <ArrowUp className="size-5" aria-hidden="true" />
                )}
              </button>
            </div>
            <p id={`${composerId}-hint`} className="mt-1.5 flex justify-between gap-3 text-xs text-fg-muted">
              <span>
                {staff
                  ? "Answers come from TechCare documents, including employee-only ones."
                  : "Answers come from TechCare's guides and policies. Check the source before you decide."}
              </span>
              <span className="shrink-0 tabular-nums">
                {draft.length > MAX_CHARS - 200 ? `${draft.length}/${MAX_CHARS}` : "Enter to send"}
              </span>
            </p>
          </form>
        </div>
      </div>

      {panel && panelTurn?.answer ? (
        <>
          <div className="fixed inset-0 z-40 bg-scrim/55 lg:hidden" aria-hidden="true" onClick={closeSource} />
          <div className="fixed inset-x-0 bottom-0 z-50 h-[85dvh] overflow-hidden rounded-t-sheet border-t border-border shadow-lg lg:sticky lg:top-0 lg:z-auto lg:h-dvh lg:w-[420px] lg:shrink-0 lg:rounded-none lg:border-t-0 lg:border-l lg:shadow-none">
            <SourcePanel
              citations={panelTurn.answer.citations}
              position={panel.position}
              onMove={(position) => setPanel({ ...panel, position })}
              onClose={closeSource}
            />
          </div>
        </>
      ) : null}

      <Modal
        open={confirm !== null}
        onClose={() => setConfirm(null)}
        title={confirm === "clear" ? "Clear your chat history?" : "Delete this chat?"}
      >
        <p className="text-sm">
          {confirm === "clear"
            ? "Every conversation you've had is deleted now, with its answers and feedback. This can't be undone."
            : "This conversation is deleted now, with its answers and feedback. This can't be undone."}
        </p>
        <div className="mt-5 flex flex-col-reverse gap-2 sm:flex-row sm:justify-end">
          <button type="button" onClick={() => setConfirm(null)} className={buttonClass.secondary}>
            Keep it
          </button>
          <button type="button" onClick={() => confirm && removeConversations(confirm)} className={buttonClass.danger}>
            {confirm === "clear" ? "Clear history" : "Delete chat"}
          </button>
        </div>
      </Modal>
      {ticketOpen && conversationId ? (
        <TicketPanel conversationId={conversationId} onClose={() => setTicketOpen(false)} />
      ) : null}
    </div>
  );
}

// ---------- pieces ----------

function EmptyState({ staff, onPick }: { staff: boolean; onPick: (question: string) => void }) {
  // The Illos empty state (components/ui), with the copy from content-guide.md. The page's own composer stays
  // below, so the component's is off; a chip asks at once.
  return (
    <IllosAiChatEmptyState
      titleAs="h1"
      title={staff ? "Ask about procedures and products" : "Ask TechCare"}
      description={
        staff ? (
          "Answers come from TechCare's published documents, including employee-only procedures. Employee-only sources are marked, so you know what not to read out to customers."
        ) : (
          <>
            TechCare&apos;s AI assistant answers from our published guides and policies, and shows you the source. It
            can make mistakes. For orders, repairs or anything urgent,{" "}
            <Link href={SUPPORT_HREF} className="font-semibold text-accent underline underline-offset-2">
              contact support
            </Link>
            .
          </>
        )
      }
      suggestions={SUGGESTIONS[staff ? "staff" : "customer"]}
      onSuggestion={onPick}
      showComposer={false}
      className="mt-4 sm:mt-8"
    />
  );
}

type FilterKey = keyof AskFilters;

const FILTER_LABELS: Record<FilterKey, { label: string; all: string }> = {
  product_model: { label: "Product model", all: "All products" },
  brand: { label: "Brand", all: "All brands" },
  category: { label: "Category", all: "All categories" },
  file_type: { label: "File type", all: "All file types" },
};

function AssistantFilters({
  options,
  value,
  onChange,
}: {
  options: Partial<Record<FilterKey, string[]>>;
  value: AskFilters;
  onChange: (v: AskFilters) => void;
}) {
  const id = useId();
  const keys = (["product_model", "brand", "category"] as const).filter((k) => (options[k] ?? []).length > 0);
  const set = (key: FilterKey, v: string) => onChange({ ...value, [key]: v || undefined });
  const chosen = (Object.keys(FILTER_LABELS) as FilterKey[]).filter((k) => value[k]);
  return (
    <div className="flex w-full flex-wrap items-end gap-2" role="group" aria-label="Filters">
      <span className="flex min-h-11 items-center gap-1.5 text-sm font-semibold">
        <Filter className="size-4 text-fg-muted" aria-hidden="true" />
        Filters
      </span>
      {[...keys, "file_type" as const].map((key) => (
        <div key={key} className="flex flex-col">
          <label htmlFor={`${id}-${key}`} className="text-xs text-fg-muted">
            {FILTER_LABELS[key].label}
          </label>
          <select
            id={`${id}-${key}`}
            value={value[key] ?? ""}
            onChange={(e) => set(key, e.target.value)}
            className="min-h-11 rounded-control border border-border-strong bg-surface px-3 text-base text-fg"
          >
            <option value="">{FILTER_LABELS[key].all}</option>
            {key === "file_type"
              ? Object.entries(FILE_TYPES).map(([v, label]) => (
                  <option key={v} value={v}>
                    {label}
                  </option>
                ))
              : (options[key] ?? []).map((v) => (
                  <option key={v} value={v}>
                    {v}
                  </option>
                ))}
          </select>
        </div>
      ))}
      {chosen.map((key) => (
        <span
          key={key}
          className="inline-flex min-h-8 items-center gap-1 rounded-full bg-accent/12 pl-3 text-sm font-semibold text-accent"
        >
          {key === "file_type" ? FILE_TYPES[value[key] ?? ""] : value[key]}
          <button
            type="button"
            onClick={() => set(key, "")}
            aria-label={`Remove the ${FILTER_LABELS[key].label.toLowerCase()} filter`}
            className="grid size-8 place-items-center rounded-full hover:bg-accent/15"
          >
            <X className="size-4" aria-hidden="true" />
          </button>
        </span>
      ))}
    </div>
  );
}

function TurnView({
  turn,
  staff,
  signedIn,
  conversationId,
  cooldown,
  onOpen,
  onRetry,
  onRephrase,
  onClearFilter,
  onKeepWaiting,
  onCreateTicket,
}: {
  turn: Turn;
  staff: boolean;
  signedIn: boolean;
  conversationId: string | null;
  cooldown: number;
  onCreateTicket?: () => void;
  onOpen: (position: number, opener: HTMLElement) => void;
  onRetry: (filters?: AskFilters | null) => void;
  onRephrase: () => void;
  onClearFilter: () => void;
  onKeepWaiting: () => void;
}) {
  return (
    <div className="space-y-3">
      <div className="flex justify-end">
        <p className="max-w-[68ch] rounded-card bg-user-bubble px-4 py-2.5 text-base whitespace-pre-line text-fg">
          <span className="sr-only">You asked: </span>
          {turn.question}
        </p>
      </div>
      <article
        aria-label="TechCare assistant"
        className="space-y-3 rounded-card border border-border bg-surface p-4 shadow-sm"
      >
        <TurnBody
          turn={turn}
          staff={staff}
          signedIn={signedIn}
          conversationId={conversationId}
          cooldown={cooldown}
          onCreateTicket={onCreateTicket}
          onOpen={onOpen}
          onRetry={onRetry}
          onRephrase={onRephrase}
          onClearFilter={onClearFilter}
          onKeepWaiting={onKeepWaiting}
        />
      </article>
    </div>
  );
}

function TurnBody({
  turn,
  staff,
  signedIn,
  conversationId,
  cooldown,
  onOpen,
  onRetry,
  onRephrase,
  onClearFilter,
  onKeepWaiting,
  onCreateTicket,
}: Parameters<typeof TurnView>[0]) {
  if (turn.phase === "offline") {
    return (
      <p className="flex items-center gap-2 text-sm text-fg-muted">
        <WifiOff className="size-4" aria-hidden="true" />
        Waiting for a connection…
      </p>
    );
  }
  if (turn.phase === "thinking" || turn.phase === "streaming") {
    return (
      <>
        <p className="flex items-center gap-2 text-sm text-fg-muted">
          <span className="flex gap-1" aria-hidden="true">
            {[0, 150, 300].map((delay) => (
              <span
                key={delay}
                className="size-1.5 animate-pulse rounded-full bg-fg-muted"
                style={{ animationDelay: `${delay}ms` }}
              />
            ))}
          </span>
          {turn.stage === "checking" ? "Checking sources…" : "Searching TechCare documents…"}
        </p>
        {turn.provisional ? (
          <p className="max-w-[68ch] text-base text-fg-muted">{stripMarkers(turn.provisional)}</p>
        ) : null}
        {turn.slow ? (
          <div className="flex flex-wrap items-center gap-2 rounded-control bg-surface-muted p-3 text-sm">
            <span className="flex-1">This is taking longer than usual.</span>
            <button type="button" onClick={onKeepWaiting} className={buttonClass.quiet}>
              Keep waiting
            </button>
            <button type="button" onClick={() => onRetry()} className={buttonClass.quiet}>
              Try again
            </button>
          </div>
        ) : null}
      </>
    );
  }
  if (turn.phase === "error" || !turn.answer) {
    const limited = turn.error?.code === "RATE_LIMITED";
    return (
      <div role="alert" className="space-y-3">
        {limited ? (
          <p className="flex items-center gap-2 text-sm font-semibold text-warning">
            <Hourglass className="size-4" aria-hidden="true" />
            <span className="text-fg">Please wait a moment</span>
          </p>
        ) : (
          <EvidenceLine status="error" count={0} />
        )}
        <p className="max-w-[68ch] text-base">
          {limited
            ? `You've asked a lot of questions quickly. You can ask again in ${cooldown || "a few"} seconds.`
            : "Something went wrong while answering. Your question is saved, so try again in a moment."}
        </p>
        <div className="flex flex-wrap gap-2">
          <button type="button" onClick={() => onRetry()} disabled={cooldown > 0} className={primaryButton}>
            {cooldown > 0 ? `Try again in ${cooldown} s` : "Try again"}
          </button>
          <Link href={SUPPORT_HREF} className={secondaryButton}>
            <LifeBuoy className="size-4" aria-hidden="true" />
            Contact support
          </Link>
        </div>
        {turn.error && turn.error.request_id !== "bff" ? (
          <details className="text-xs text-fg-muted">
            <summary className="inline-flex min-h-11 cursor-pointer items-center">Details</summary>
            <p className="font-mono">Reference: {turn.error.request_id}</p>
          </details>
        ) : null}
      </div>
    );
  }

  const answer = turn.answer;
  const notFound = answer.status === "insufficient_evidence";
  return (
    <>
      <EvidenceLine
        status={answer.status}
        count={answer.citations.length}
        fromAccount={answer.nextAction === "show_records"}
      />
      {answer.status === "error" ? (
        <p className="flex items-start gap-2 text-base">
          <TriangleAlert className="mt-1 size-4 shrink-0 text-danger" aria-hidden="true" />
          Something went wrong while answering. Your question is saved, so try again in a moment.
        </p>
      ) : (
        <AnswerText text={answer.text} citations={answer.citations} onOpen={onOpen} />
      )}
      <CitationChips citations={answer.citations} staff={staff} onOpen={onOpen} />
      {answer.nextAction === "verify_order" ? (
        <OrderCheck
          signedIn={signedIn && !staff}
          orderNumber={recordNumberIn(turn.question)}
          conversationId={conversationId}
        />
      ) : null}
      {answer.nextAction === "show_records" ? <RecordCards records={answer.records ?? []} /> : null}
      {notFound && staff && turn.filters ? (
        <div className="flex flex-wrap items-center gap-2 rounded-control bg-surface-muted p-3 text-sm">
          <span className="flex-1">No results with these filters: {describeFilters(turn.filters)}.</span>
          <button type="button" onClick={onClearFilter} className={buttonClass.quiet}>
            Search without filters
          </button>
        </div>
      ) : null}
      {notFound ? <NotFoundActions onRephrase={onRephrase} /> : null}
      <AnswerActions
        view={answer}
        staff={staff}
        disabled={false}
        cooldown={cooldown}
        onAskAgain={() => onRetry()}
        onCreateTicket={onCreateTicket}
      />
    </>
  );
}
