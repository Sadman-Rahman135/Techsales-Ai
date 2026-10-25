import {
  Activity,
  BadgeCheck,
  BarChart3,
  ClipboardCheck,
  FileText,
  LogOut,
  MessagesSquare,
  Ticket,
  Wrench,
} from "lucide-react";
import Link from "next/link";
import type { ReactNode } from "react";
import { Suspense } from "react";

import { signOut } from "@/app/login/actions";
import { canManageDocuments, getCurrentUser, type Me } from "@/lib/auth";

// design-system/MASTER.md §8: sidebar at ≥1024px, top bar below. Only screens the role may open are listed
// (scope.md: no teasers or dead links).
const NAV = [
  { href: "/assistant", label: "Assistant", icon: MessagesSquare, managers: false },
  { href: "/admin/tickets", label: "Tickets", icon: Ticket, managers: false },
  { href: "/admin/documents", label: "Documents", icon: FileText, managers: true },
  { href: "/admin/quality", label: "Quality", icon: BarChart3, managers: true },
  { href: "/admin/reviews", label: "Review", icon: ClipboardCheck, managers: true },
  { href: "/admin/operations", label: "Operations", icon: Activity, managers: true },
] as const;

function itemsFor(me: Me | null) {
  return NAV.filter((item) => !item.managers || (me !== null && canManageDocuments(me)));
}

export function AdminShell({ current, children }: { current: string; children: ReactNode }) {
  return (
    <div className="flex min-h-dvh flex-col lg:flex-row">
      <aside className="sticky top-0 hidden h-dvh w-60 shrink-0 flex-col border-r border-border bg-surface lg:flex">
        <Brand />
        <nav aria-label="Staff" className="flex-1 px-3 py-2">
          <Suspense fallback={null}>
            <NavList current={current} />
          </Suspense>
        </nav>
        <div className="border-t border-border p-3">
          <Suspense fallback={<div className="h-20" aria-hidden="true" />}>
            <UserCard />
          </Suspense>
        </div>
      </aside>

      <header className="sticky top-0 z-20 border-b border-border bg-surface lg:hidden">
        <div className="flex min-h-15 items-center justify-between gap-3 px-4">
          <Brand compact />
          <Suspense fallback={null}>
            <UserCard compact />
          </Suspense>
        </div>
        <nav aria-label="Staff" className="flex gap-1 overflow-x-auto px-2 pb-2">
          <Suspense fallback={null}>
            <NavList current={current} inline />
          </Suspense>
        </nav>
      </header>

      <div className="flex min-w-0 flex-1 flex-col">{children}</div>
    </div>
  );
}

function Brand({ compact = false }: { compact?: boolean }) {
  return (
    <Link
      href="/assistant"
      className={`flex min-h-11 items-center gap-2 font-heading text-lg font-semibold text-fg ${compact ? "" : "px-4 pt-4 pb-3"}`}
    >
      <span aria-hidden="true" className="grid size-8 place-items-center rounded-control bg-fg text-bg">
        <Wrench className="size-[18px]" strokeWidth={2} />
      </span>
      TechCare
      <span className="rounded-full border border-border-strong px-2 text-xs leading-5 font-semibold text-fg-muted">
        Staff
      </span>
    </Link>
  );
}

async function NavList({ current, inline = false }: { current: string; inline?: boolean }) {
  const items = itemsFor(await getCurrentUser());
  const links = items.map((item) => {
    const Icon = item.icon;
    const active = current === item.href;
    return (
      <Link
        key={item.href}
        href={item.href}
        aria-current={active ? "page" : undefined}
        className={`flex min-h-11 items-center gap-3 rounded-control px-3 text-sm font-semibold whitespace-nowrap transition-colors duration-150 ${active ? "bg-accent/12 text-accent" : "text-fg hover:bg-surface-muted"}`}
      >
        <Icon className="size-5" aria-hidden="true" />
        {item.label}
      </Link>
    );
  });
  if (inline) return <>{links}</>;
  return (
    <ul className="space-y-1">
      {links.map((link) => (
        <li key={link.key}>{link}</li>
      ))}
    </ul>
  );
}

const ROLE_LABEL = { admin: "Admin", manager: "Manager", employee: "Employee", customer: "Customer" } as const;

function RoleBadge({ me }: { me: Me }) {
  return (
    <span className="inline-flex items-center gap-1 rounded-full bg-success/12 px-2 py-0.5 text-xs font-semibold">
      <BadgeCheck className="size-3.5 text-success" aria-hidden="true" />
      <span className="text-fg">
        {ROLE_LABEL[me.role]} · {me.organization.name}
      </span>
    </span>
  );
}

async function UserCard({ compact = false }: { compact?: boolean }) {
  const me = await getCurrentUser();
  if (!me) return null;
  const signOutButton = (
    <form action={signOut}>
      <button
        type="submit"
        className="inline-flex min-h-11 items-center gap-2 rounded-control px-3 text-sm font-semibold text-fg-muted transition-colors duration-150 hover:bg-surface-muted hover:text-fg"
      >
        <LogOut className="size-4" aria-hidden="true" />
        Sign out
      </button>
    </form>
  );
  if (compact) {
    return (
      <div className="flex items-center gap-1">
        <span className="max-sm:hidden">
          <RoleBadge me={me} />
        </span>
        {signOutButton}
      </div>
    );
  }
  return (
    <div>
      <p className="truncate px-3 text-sm font-semibold">{me.display_name ?? me.email}</p>
      <p className="mt-1 px-3">
        <RoleBadge me={me} />
      </p>
      <div className="mt-2">{signOutButton}</div>
    </div>
  );
}
