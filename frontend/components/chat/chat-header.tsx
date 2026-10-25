import { LifeBuoy, LogOut, Wrench } from "lucide-react";
import Link from "next/link";
import { Suspense } from "react";

import { signOut } from "@/app/login/actions";
import { getCurrentUser, homeFor } from "@/lib/auth";
import { SUPPORT_HREF } from "@/lib/support";

// MASTER.md §8 "Customer chat": no sidebar. Logo, "AI assistant" disclosure badge, "Contact support" always.
export function ChatHeader() {
  return (
    <header className="sticky top-0 z-20 border-b border-border bg-surface">
      <div className="mx-auto flex min-h-15 w-full max-w-5xl flex-wrap items-center gap-x-3 gap-y-1 px-4 py-2">
        <Link href="/chat" className="flex min-h-11 items-center gap-2 font-heading text-lg font-semibold text-fg">
          <span aria-hidden="true" className="grid size-8 place-items-center rounded-control bg-fg text-bg">
            <Wrench className="size-[18px]" strokeWidth={2} />
          </span>
          TechCare
        </Link>
        <span className="rounded-full border border-border-strong px-2.5 text-xs leading-5 font-semibold text-fg-muted">
          AI assistant
        </span>
        <nav aria-label="Help and account" className="ml-auto flex items-center gap-1">
          <Link
            href={SUPPORT_HREF}
            className="inline-flex min-h-11 items-center gap-2 rounded-control px-3 text-sm font-semibold text-accent hover:bg-accent/10"
          >
            <LifeBuoy className="size-4" aria-hidden="true" />
            Contact support
          </Link>
          <Suspense fallback={<span className="w-20" aria-hidden="true" />}>
            <Account />
          </Suspense>
        </nav>
      </div>
    </header>
  );
}

async function Account() {
  const me = await getCurrentUser();
  const quiet =
    "inline-flex min-h-11 items-center gap-2 rounded-control px-3 text-sm font-semibold text-fg-muted hover:bg-surface-muted hover:text-fg";
  if (!me) {
    return (
      <Link href="/login?next=%2Fchat" className={quiet}>
        Sign in
      </Link>
    );
  }
  return (
    <>
      {me.role !== "customer" ? (
        <Link href={homeFor(me.role)} className={quiet}>
          Staff area
        </Link>
      ) : null}
      <form action={signOut}>
        <button type="submit" className={quiet}>
          <LogOut className="size-4" aria-hidden="true" />
          <span className="max-sm:sr-only">Sign out</span>
        </button>
      </form>
    </>
  );
}
