import { Lock } from "lucide-react";
import Link from "next/link";
import { Suspense } from "react";

import { AdminShell } from "@/components/admin/admin-shell";
import { ChatApp } from "@/components/chat/chat-app";
import { ChatSkeleton } from "@/components/chat/chat-page";
import { isStaff, requireUser } from "@/lib/auth";
import { backendFetch } from "@/lib/backend";
import { loadChat } from "@/lib/chat-server";
import type { Facets } from "@/lib/documents";

// `/assistant` and `/assistant/[id]` (scope.md §4, employee-assistant.md): signed-in staff only. The API decides
// what each role may retrieve; this page only shows the filter and the Employee-only markings.
export function AssistantPage({ conversationId }: { conversationId: Promise<string | null> }) {
  return (
    <AdminShell current="/assistant">
      <main id="main" tabIndex={-1} className="flex flex-1 flex-col outline-none">
        <Suspense fallback={<ChatSkeleton />}>
          <Assistant conversationId={conversationId} />
        </Suspense>
      </main>
    </AdminShell>
  );
}

async function Assistant({ conversationId }: { conversationId: Promise<string | null> }) {
  const id = await conversationId;
  const me = await requireUser(id ? `/assistant/${id}` : "/assistant");
  if (!isStaff(me)) return <StaffOnly />;
  const [{ history, initial }, facets] = await Promise.all([
    loadChat(id),
    backendFetch("/api/v1/documents/facets").then((r) =>
      r.ok ? (r.json() as Promise<Facets>) : ({ product_models: [], tags: [] } as Facets),
    ),
  ]);
  return (
    <ChatApp
      key={id ?? "new"}
      variant="staff"
      basePath="/assistant"
      signedIn
      initial={initial}
      history={history}
      products={facets.product_models}
      brands={facets.brands ?? []}
      categories={facets.categories ?? []}
    />
  );
}

function StaffOnly() {
  return (
    <div className="mx-auto w-full max-w-3xl px-4 py-10">
      <div className="rounded-card border border-border bg-surface p-6">
        <h1 className="flex items-center gap-2 font-heading text-xl font-semibold">
          <Lock className="size-5 text-fg-muted" aria-hidden="true" />
          For TechCare staff
        </h1>
        <p className="mt-2 text-fg-muted">
          The employee assistant is for store staff. You can ask about products, returns and repairs in the customer
          chat.
        </p>
        <Link href="/chat" className="mt-4 inline-flex min-h-11 items-center font-semibold text-accent underline underline-offset-2">
          Go to the customer chat
        </Link>
      </div>
    </div>
  );
}
