import { Lock } from "lucide-react";
import Link from "next/link";

// Shown to signed-in staff who lack the role for a screen (the API refuses the data anyway).
export function NoPermission({ what = "manage documents" }: { what?: string }) {
  return (
    <div className="mt-4 rounded-card border border-border bg-surface p-6">
      <h1 className="flex items-center gap-2 font-heading text-xl font-semibold">
        <Lock className="size-5 text-fg-muted" aria-hidden="true" />
        Managers and admins only
      </h1>
      <p className="mt-2 text-fg-muted">
        Your account can use the assistant but can&apos;t {what}. Ask an admin if you need access.
      </p>
      <Link href="/assistant" className="mt-4 inline-flex min-h-11 items-center font-semibold text-accent underline underline-offset-2">
        Go to the assistant
      </Link>
    </div>
  );
}
