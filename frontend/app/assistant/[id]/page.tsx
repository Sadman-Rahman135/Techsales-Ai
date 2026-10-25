import type { Metadata } from "next";

import { AssistantPage } from "@/components/chat/assistant-page";

export const metadata: Metadata = { title: "Assistant", robots: { index: false, follow: false } };

export default function Page({ params }: PageProps<"/assistant/[id]">) {
  return <AssistantPage conversationId={params.then((p) => p.id)} />;
}
