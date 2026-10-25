// Review queue (ADR-030): types of GET /api/v1/admin/reviews and the labels the screen shows.

export type ReviewStatus = "open" | "resolved" | "dismissed";
export type ReviewReason = "unanswered" | "not_helpful";

export type ReviewItem = {
  answer_id: string;
  asked_at: string;
  intent: string | null;
  answer_status: string;
  reasons: ReviewReason[];
  question: string;
  answer: string;
  feedback: { reasons: string[]; note: string | null; created_at: string }[];
  status: ReviewStatus;
  note: string | null;
  document: { id: string; title: string } | null;
  reviewed_by: string | null;
  reviewed_at: string | null;
};

export type ReviewPage = {
  items: ReviewItem[];
  total: number;
  counts: Record<ReviewStatus, number>;
};

export const REVIEW_STATUS: Record<ReviewStatus, string> = {
  open: "To review",
  resolved: "Resolved",
  dismissed: "Dismissed",
};

export const REVIEW_REASON: Record<ReviewReason, string> = {
  unanswered: "Not in documents",
  not_helpful: "Rated not helpful",
};

export const EXPORTS = [
  {
    dataset: "questions",
    label: "Questions",
    hint: "Every question with its outcome, intent and time",
  },
  {
    dataset: "feedback",
    label: "Feedback",
    hint: "Ratings, reasons and notes",
  },
  {
    dataset: "reviews",
    label: "Review decisions",
    hint: "What was resolved or dismissed, and by whom",
  },
  {
    dataset: "tickets",
    label: "Tickets",
    hint: "Ticket status and priority, without summaries",
  },
  {
    dataset: "documents",
    label: "Documents",
    hint: "Every document and its details (not limited by dates)",
  },
] as const;

export type ExportDataset = (typeof EXPORTS)[number]["dataset"];

export function exportUrl(dataset: ExportDataset, start?: string, end?: string): string {
  const params = new URLSearchParams();
  if (start) params.set("start", start);
  if (end) params.set("end", end);
  const search = params.toString();
  return `/api/admin/exports/${dataset}${search ? `?${search}` : ""}`;
}
