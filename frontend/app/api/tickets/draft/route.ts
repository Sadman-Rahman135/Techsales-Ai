import type { NextRequest } from "next/server";

import { crossSiteRefused, relay, sameOrigin } from "@/lib/bff";

// POST: a draft subject and summary from the employee's own conversation; nothing is saved.
export async function POST(request: NextRequest) {
  if (!sameOrigin(request)) return crossSiteRefused();
  return relay("/api/v1/tickets/draft", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: await request.text(),
  });
}
