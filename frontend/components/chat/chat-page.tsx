import { Suspense } from "react";

import { ChatApp } from "@/components/chat/chat-app";
import { ChatHeader } from "@/components/chat/chat-header";
import { loadChat } from "@/lib/chat-server";

// `/chat` and `/chat/[conversationId]` (scope.md §4): the public chat, for guests and signed-in customers.
export function CustomerChatPage({ conversationId }: { conversationId: Promise<string | null> }) {
  return (
    <div className="flex min-h-dvh flex-col">
      <ChatHeader />
      <main id="main" tabIndex={-1} className="flex flex-1 flex-col outline-none">
        <Suspense fallback={<ChatSkeleton />}>
          <CustomerChat conversationId={conversationId} />
        </Suspense>
      </main>
    </div>
  );
}

async function CustomerChat({ conversationId }: { conversationId: Promise<string | null> }) {
  const id = await conversationId;
  const { me, history, initial } = await loadChat(id);
  return (
    <ChatApp
      key={id ?? "new"}
      variant="customer"
      basePath="/chat"
      signedIn={me !== null}
      initial={initial}
      history={history}
    />
  );
}

export function ChatSkeleton() {
  return (
    <div aria-hidden="true" className="mx-auto w-full max-w-3xl space-y-4 px-4 pt-12">
      <div className="h-9 w-56 animate-pulse rounded-control bg-surface-muted" />
      <div className="h-5 w-full max-w-lg animate-pulse rounded-control bg-surface-muted" />
      <div className="flex gap-2 pt-6">
        {[40, 52, 44].map((w) => (
          <div key={w} className="h-11 animate-pulse rounded-full bg-surface-muted" style={{ width: `${w * 4}px` }} />
        ))}
      </div>
    </div>
  );
}
