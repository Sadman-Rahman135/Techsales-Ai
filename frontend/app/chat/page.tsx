import type { Metadata } from "next";

import { CustomerChatPage } from "@/components/chat/chat-page";

export const metadata: Metadata = { title: "Ask TechCare" };

export default function ChatPage() {
  return <CustomerChatPage conversationId={Promise.resolve(null)} />;
}
