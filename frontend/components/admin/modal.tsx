"use client";

import { X } from "lucide-react";
import { useEffect, useId, useRef, type ReactNode } from "react";

// Native <dialog> with showModal(): the page behind becomes inert (focus stays inside), Esc closes, and focus
// returns to the opener on close. MASTER.md §7: scrim 55%, radius 16, title states the action.
export function Modal({
  open,
  onClose,
  title,
  description,
  children,
  wide = false,
}: {
  open: boolean;
  onClose: () => void;
  title: string;
  description?: string;
  children: ReactNode;
  wide?: boolean;
}) {
  const ref = useRef<HTMLDialogElement>(null);
  const id = useId(); // unique per dialog: two modals on one page must not share label ids

  useEffect(() => {
    const dialog = ref.current;
    if (!dialog) return;
    if (open && !dialog.open) dialog.showModal();
    if (!open && dialog.open) dialog.close();
  }, [open]);

  return (
    <dialog
      ref={ref}
      onClose={onClose}
      aria-labelledby={`${id}-title`}
      aria-describedby={description ? `${id}-description` : undefined}
      className={`m-auto w-[calc(100%-2rem)] ${wide ? "max-w-xl" : "max-w-md"} rounded-sheet border border-border bg-surface p-0 text-fg shadow-xl backdrop:bg-scrim/55`}
    >
      <div className="flex items-start justify-between gap-4 border-b border-border px-5 pt-5 pb-4">
        <div>
          <h2 id={`${id}-title`} className="font-heading text-lg font-semibold">
            {title}
          </h2>
          {description ? (
            <p id={`${id}-description`} className="mt-1 text-sm text-fg-muted">
              {description}
            </p>
          ) : null}
        </div>
        <button
          type="button"
          onClick={onClose}
          aria-label="Close"
          className="-mt-2 -mr-2 grid size-11 shrink-0 place-items-center rounded-control text-fg-muted hover:bg-surface-muted hover:text-fg"
        >
          <X className="size-5" aria-hidden="true" />
        </button>
      </div>
      <div className="px-5 py-4">{children}</div>
    </dialog>
  );
}

export const buttonClass = {
  primary:
    "inline-flex min-h-11 items-center justify-center gap-2 rounded-control bg-accent px-4 font-semibold text-on-accent transition-colors duration-150 hover:bg-accent/90 disabled:opacity-60",
  secondary:
    "inline-flex min-h-11 items-center justify-center gap-2 rounded-control border border-border-strong bg-surface px-4 font-semibold text-fg transition-colors duration-150 hover:bg-surface-muted disabled:opacity-60",
  danger:
    "inline-flex min-h-11 items-center justify-center gap-2 rounded-control bg-danger px-4 font-semibold text-on-accent transition-colors duration-150 hover:bg-danger/90 disabled:opacity-60",
  quiet:
    "inline-flex min-h-10 items-center justify-center gap-1.5 rounded-control px-3 text-sm font-semibold text-accent transition-colors duration-150 hover:bg-accent/10 disabled:opacity-60",
};
