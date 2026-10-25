import type { Metadata } from "next";

import { AssistantPage } from "@/components/chat/assistant-page";

export const metadata: Metadata = { title: "Assistant", robots: { index: false, follow: false } };

export default function Page() {
  return <AssistantPage conversationId={Promise.resolve(null)} />;
}
