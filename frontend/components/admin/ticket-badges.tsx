import { ArrowDown, ArrowUp, CircleCheck, CircleDot, Equal, LoaderCircle } from "lucide-react";

import { TICKET_PRIORITY, TICKET_STATUS, type TicketPriority, type TicketStatus } from "@/lib/tickets";

// Every status and priority is icon + text; colour only reinforces it (MASTER.md).
const pill = "inline-flex items-center gap-1.5 rounded-full px-2.5 py-0.5 text-xs font-semibold whitespace-nowrap";

const STATUS = {
  open: { icon: CircleDot, tone: "bg-accent/12 text-accent" },
  in_progress: { icon: LoaderCircle, tone: "bg-warning/12 text-warning" },
  resolved: { icon: CircleCheck, tone: "bg-success/12 text-success" },
} satisfies Record<TicketStatus, unknown>;

const PRIORITY = {
  low: { icon: ArrowDown, tone: "text-fg-muted" },
  normal: { icon: Equal, tone: "text-fg-muted" },
  high: { icon: ArrowUp, tone: "text-danger" },
} satisfies Record<TicketPriority, unknown>;

export function TicketStatusBadge({ status }: { status: TicketStatus }) {
  const { icon: Icon, tone } = STATUS[status];
  return (
    <span className={`${pill} ${tone}`}>
      <Icon className="size-3.5" aria-hidden="true" />
      {TICKET_STATUS[status]}
    </span>
  );
}

export function PriorityLabel({ priority }: { priority: TicketPriority }) {
  const { icon: Icon, tone } = PRIORITY[priority];
  return (
    <span className={`inline-flex items-center gap-1 text-xs font-semibold ${tone}`}>
      <Icon className="size-3.5" aria-hidden="true" />
      <span className="text-fg">{TICKET_PRIORITY[priority]} priority</span>
    </span>
  );
}
