import { Clock, Mail, MapPin, MessagesSquare, Phone } from "lucide-react";
import type { Metadata } from "next";
import Link from "next/link";

import { ChatHeader } from "@/components/chat/chat-header";

export const metadata: Metadata = { title: "Contact support" };

// The "Contact support" destination (content-guide.md "Escalation CTA"). Fictional TechCare details (the project rules:
// demo data only); a deployment sets NEXT_PUBLIC_SUPPORT_URL to its real help desk instead.
const CHANNELS = [
  { icon: Phone, label: "Call us", value: "(555) 010-0199", note: "Mon–Sat, 9 am–6 pm" },
  { icon: Mail, label: "Email", value: "support@techcare.example", note: "We reply within one business day" },
  { icon: MapPin, label: "Visit the repair desk", value: "TechCare Downtown, 100 Example Street", note: "Walk-ins welcome" },
];

export default function SupportPage() {
  return (
    <div className="flex min-h-dvh flex-col">
      <ChatHeader />
      <main id="main" className="mx-auto w-full max-w-3xl flex-1 px-4 py-10">
        <h1 className="font-heading text-3xl font-semibold tracking-tight">Contact support</h1>
        <p className="mt-3 max-w-[60ch] text-fg-muted">
          A person on our team can help with orders, repairs, warranty decisions and anything the assistant
          couldn&apos;t answer. If you saw a reference like <span className="font-mono">req-…</span>, include it: it
          helps us find the answer you got without sharing your conversation.
        </p>
        <ul className="mt-8 grid gap-4 sm:grid-cols-2">
          {CHANNELS.map(({ icon: Icon, label, value, note }) => (
            <li key={label} className="rounded-card border border-border bg-surface p-5">
              <p className="flex items-center gap-2 font-semibold">
                <Icon className="size-5 text-accent" aria-hidden="true" />
                {label}
              </p>
              <p className="mt-2 text-fg">{value}</p>
              <p className="mt-1 flex items-center gap-1.5 text-sm text-fg-muted">
                <Clock className="size-4" aria-hidden="true" />
                {note}
              </p>
            </li>
          ))}
        </ul>
        <Link
          href="/chat"
          className="mt-8 inline-flex min-h-11 items-center gap-2 rounded-control border border-border-strong px-4 font-semibold text-fg hover:bg-surface-muted"
        >
          <MessagesSquare className="size-4" aria-hidden="true" />
          Back to the assistant
        </Link>
      </main>
    </div>
  );
}
