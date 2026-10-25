// Support tickets (ADR-029): the API's shapes and labels for the staff queue.

export type TicketStatus = "open" | "in_progress" | "resolved";
export type TicketPriority = "low" | "normal" | "high";

export type Ticket = {
  id: string;
  number: number;
  reference: string;
  subject: string;
  summary: string;
  summary_drafted: boolean;
  status: TicketStatus;
  priority: TicketPriority;
  order_number: string | null;
  customer_ref: string | null;
  conversation_id: string | null;
  created_by: string | null;
  assigned_to: string | null;
  assignee: string | null;
  created_at: string;
  updated_at: string;
  resolved_at: string | null;
};

export type TicketNote = { id: string; author: string | null; body: string; created_at: string };
export type TicketDetail = Ticket & { notes: TicketNote[] };
export type TicketPage = { items: Ticket[]; total: number; counts: Record<TicketStatus, number> };
export type StaffMember = { user_id: string; name: string };
export type TicketDraft = { subject: string; summary: string; drafted_by_model: boolean };

export const TICKET_STATUS: Record<TicketStatus, string> = {
  open: "Open",
  in_progress: "In progress",
  resolved: "Resolved",
};

export const TICKET_PRIORITY: Record<TicketPriority, string> = { low: "Low", normal: "Normal", high: "High" };
