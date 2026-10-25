import { Archive, BadgeCheck, CircleDashed, CircleSlash, Globe, Loader, Lock, TriangleAlert } from "lucide-react";

import type { Audience, DocumentStatus } from "@/lib/documents";

// design-system/pages/admin-documents.md: every status is icon + text; color only reinforces it.
const STATUS = {
  processing: { label: "Processing", icon: Loader, tone: "bg-accent/12 text-accent", spin: true },
  ready: { label: "Ready to publish", icon: CircleDashed, tone: "bg-warning/12 text-warning", spin: false },
  draft: { label: "Draft", icon: CircleDashed, tone: "bg-surface-muted text-fg-muted", spin: false },
  published: { label: "Published", icon: BadgeCheck, tone: "bg-success/12 text-success", spin: false },
  failed: { label: "Failed", icon: TriangleAlert, tone: "bg-danger/12 text-danger", spin: false },
  cancelled: { label: "Cancelled", icon: CircleSlash, tone: "bg-surface-muted text-fg-muted", spin: false },
  archived: { label: "Archived", icon: Archive, tone: "bg-surface-muted text-fg-muted", spin: false },
} satisfies Record<DocumentStatus, unknown>;

const pill = "inline-flex items-center gap-1.5 rounded-full px-2.5 py-0.5 text-xs font-semibold whitespace-nowrap";

export function StatusBadge({ status }: { status: DocumentStatus }) {
  const { label, icon: Icon, tone, spin } = STATUS[status];
  return (
    <span className={`${pill} ${tone}`}>
      <Icon className={`size-3.5 ${spin ? "animate-spin [animation-duration:2s]" : ""}`} aria-hidden="true" />
      {label}
    </span>
  );
}

export function AudienceBadge({ audience }: { audience: Audience }) {
  const employee = audience === "employee";
  const Icon = employee ? Lock : Globe;
  return (
    <span className={`${pill} border border-border text-fg-muted`}>
      <Icon className="size-3.5" aria-hidden="true" />
      {employee ? "Employee" : "Public"}
    </span>
  );
}
