import { Wrench } from "lucide-react";
import Link from "next/link";

export function SiteHeader({ badge }: { badge?: string }) {
  return (
    <header className="sticky top-0 z-20 flex min-h-15 items-center gap-3 border-b border-border bg-surface px-4">
      <Link href="/" className="flex min-h-11 items-center gap-2 font-heading text-lg font-semibold text-fg">
        <span aria-hidden="true" className="grid size-8 place-items-center rounded-control bg-fg text-bg">
          <Wrench className="size-[18px]" strokeWidth={2} />
        </span>
        TechCare
      </Link>
      {badge ? (
        <span className="rounded-full border border-border-strong px-2.5 text-xs leading-5 font-semibold text-fg-muted">
          {badge}
        </span>
      ) : null}
    </header>
  );
}
