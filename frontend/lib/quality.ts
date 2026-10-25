// Quality dashboard data (backend/app/api/analytics.py) and pure helpers. No imports: `npm test` loads it directly.

export type Totals = {
  questions: number;
  answered: number;
  insufficient_evidence: number;
  escalated: number;
  error: number;
  direct: number;
};

export type Daily = { date: string } & Omit<Totals, "questions">;

export type Analytics = {
  range: { start: string; end: string; timezone: string; label: string };
  totals: Totals;
  latency_ms: { p50: number | null; p95: number | null };
  daily: Daily[];
  intents: { intent: string; count: number }[];
  most_cited: { document_id: string; title: string; citations: number }[];
  unanswered: { answer_id: string; asked_at: string; intent: string | null; question: string; conversation: string }[];
  missing_topics: { term: string; count: number }[];
  feedback: {
    helpful: number;
    not_helpful: number;
    recent_not_helpful: { answer_id: string; created_at: string; reasons: string[]; note: string | null; question: string }[];
  };
  feedback_daily: { date: string; helpful: number; not_helpful: number }[];
  ingestion_failures: {
    job_id: string;
    document_id: string;
    title: string;
    version_number: number;
    error_code: string | null;
    error_message: string | null;
    failed_stage: string | null;
    finished_at: string;
  }[];
};

export type Preset = "today" | "7d" | "30d" | "custom";
export type DateRange = { start: string; end: string; preset: Preset };

const DAY_MS = 86_400_000;
const ISO_DATE = /^\d{4}-\d{2}-\d{2}$/;

/** "2026-10-19" ± days, on calendar dates (no time zone involved). */
export function addDays(iso: string, days: number): string {
  return new Date(Date.parse(`${iso}T00:00:00Z`) + days * DAY_MS).toISOString().slice(0, 10);
}

export function daysBetween(start: string, end: string): number {
  return Math.round((Date.parse(`${end}T00:00:00Z`) - Date.parse(`${start}T00:00:00Z`)) / DAY_MS) + 1;
}

/** The range in the URL (quality-dashboard.md: "The range is stored in the URL"), resolved against `today`. */
export function resolveRange(params: { range?: string; start?: string; end?: string }, today: string): DateRange {
  if (params.range === "custom" && params.start && params.end && ISO_DATE.test(params.start) && ISO_DATE.test(params.end)) {
    const [start, end] = params.start <= params.end ? [params.start, params.end] : [params.end, params.start];
    return { start, end, preset: "custom" };
  }
  if (params.range === "today") return { start: today, end: today, preset: "today" };
  if (params.range === "30d") return { start: addDays(today, -29), end: today, preset: "30d" };
  return { start: addDays(today, -6), end: today, preset: "7d" };
}

/** The same number of days immediately before, for "change vs the previous period". */
export function previousRange(range: { start: string; end: string }): { start: string; end: string } {
  const length = daysBetween(range.start, range.end);
  return { start: addDays(range.start, -length), end: addDays(range.start, -1) };
}

/** Share of questions, as a whole percent; null when there were no questions. */
export function percent(part: number, whole: number): number | null {
  return whole > 0 ? Math.round((part / whole) * 100) : null;
}

export type Change = { direction: "up" | "down" | "same"; text: string } | null;

/** "+12" / "−3" for counts, "+4 pts" for percentages; never color alone (the caller adds an icon). */
export function change(current: number | null, previous: number | null, unit: "count" | "points"): Change {
  if (current === null || previous === null) return null;
  const delta = current - previous;
  const suffix = unit === "points" ? " pts" : "";
  if (delta === 0) return { direction: "same", text: `No change` };
  return { direction: delta > 0 ? "up" : "down", text: `${delta > 0 ? "+" : "−"}${Math.abs(delta)}${suffix}` };
}

/** Day label for chart axes and tables: "13 Oct". */
export function shortDay(iso: string): string {
  const [, month, day] = iso.split("-").map(Number);
  const names = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
  return `${day} ${names[month - 1]}`;
}

export const INTENT_LABEL: Record<string, string> = {
  knowledge: "Product and policy questions",
  smalltalk: "Greetings and small talk",
  order: "Order status",
  support: "Asked for a person",
};
