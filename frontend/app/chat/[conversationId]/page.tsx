import type { Metadata } from "next";

import { CustomerChatPage } from "@/components/chat/chat-page";

export const metadata: Metadata = { title: "Ask TechCare", robots: { index: false, follow: false } };

export default function ConversationPage({ params }: PageProps<"/chat/[conversationId]">) {
  return <CustomerChatPage conversationId={params.then((p) => p.conversationId)} />;
}
