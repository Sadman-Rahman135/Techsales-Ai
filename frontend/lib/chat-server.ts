import "server-only";

import { cookies } from "next/headers";
import { notFound } from "next/navigation";

import { getCurrentUser, type Me } from "@/lib/auth";
import { backendFetch } from "@/lib/backend";
import type { Conversation, ConversationSummary, StoredMessage } from "@/lib/chat";
import { GUEST_COOKIE } from "@/lib/guest";

export type ChatData = { me: Me | null; history: ConversationSummary[]; initial: Conversation | null };

/** Everything a chat page needs on first render: who is asking, their history, and the opened conversation. */
export async function loadChat(conversationId: string | null): Promise<ChatData> {
  const me = await getCurrentUser();
  const guest = me ? null : ((await cookies()).get(GUEST_COOKIE)?.value ?? null);
  const identity: Record<string, string> = guest ? { "X-Session-Ref": guest } : {};

  const history: ConversationSummary[] = me
    ? await backendFetch("/api/v1/conversations").then((r) => (r.ok ? (r.json() as Promise<ConversationSummary[]>) : []))
    : [];

  let initial: Conversation | null = null;
  if (conversationId) {
    if (!me && !guest) notFound();
    const response = await backendFetch(`/api/v1/conversations/${encodeURIComponent(conversationId)}`, {
      headers: identity,
    });
    if (response.status === 404 || response.status === 422) notFound(); // someone else's, or not an ID
    if (!response.ok) throw new Error(`conversation load failed: HTTP ${response.status}`);
    const raw = (await response.json()) as Conversation;
    initial = { ...raw, messages: raw.messages.map((m: StoredMessage) => ({ ...m, citations: m.citations ?? [] })) };
  }
  return { me, history, initial };
}
