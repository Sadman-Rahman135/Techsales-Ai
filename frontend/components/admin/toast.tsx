"use client";

import { CircleCheck, TriangleAlert, X } from "lucide-react";
import { createContext, use, useCallback, useEffect, useState, type ReactNode } from "react";

// MASTER.md §7 Toast: bottom-center on mobile, bottom-right on desktop, aria-live polite, 4 s, never steals focus.
type Toast = { id: number; tone: "success" | "danger"; text: string };
const ToastContext = createContext<((tone: Toast["tone"], text: string) => void) | null>(null);

export function ToastProvider({ children }: { children: ReactNode }) {
  const [toasts, setToasts] = useState<Toast[]>([]);
  const push = useCallback((tone: Toast["tone"], text: string) => {
    setToasts((all) => [...all, { id: Date.now() + Math.random(), tone, text }]);
  }, []);
  const dismiss = useCallback((id: number) => setToasts((all) => all.filter((t) => t.id !== id)), []);

  return (
    <ToastContext value={push}>
      {children}
      <div
        aria-live="polite"
        className="pointer-events-none fixed inset-x-0 bottom-4 z-[70] flex flex-col items-center gap-2 px-4 sm:inset-x-auto sm:right-6 sm:items-end"
      >
        {toasts.map((toast) => (
          <ToastItem key={toast.id} toast={toast} onDone={dismiss} />
        ))}
      </div>
    </ToastContext>
  );
}

function ToastItem({ toast, onDone }: { toast: Toast; onDone: (id: number) => void }) {
  useEffect(() => {
    const timer = setTimeout(() => onDone(toast.id), 4000);
    return () => clearTimeout(timer);
  }, [toast.id, onDone]);
  const Icon = toast.tone === "success" ? CircleCheck : TriangleAlert;
  return (
    <div className="pointer-events-auto flex w-full max-w-sm items-start gap-3 rounded-card border border-border bg-surface p-3 shadow-lg">
      <Icon
        className={`mt-0.5 size-5 shrink-0 ${toast.tone === "success" ? "text-success" : "text-danger"}`}
        aria-hidden="true"
      />
      <p className="flex-1 text-sm">{toast.text}</p>
      <button
        type="button"
        onClick={() => onDone(toast.id)}
        aria-label="Dismiss notification"
        className="-m-2 grid size-11 place-items-center rounded-control text-fg-muted hover:text-fg"
      >
        <X className="size-4" aria-hidden="true" />
      </button>
    </div>
  );
}

export function useToast() {
  const push = use(ToastContext);
  if (!push) throw new Error("useToast must be used inside ToastProvider");
  return push;
}
