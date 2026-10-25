import type { NextRequest } from "next/server";

import { crossSiteRefused, relay, sameOrigin } from "@/lib/bff";
import { chatIdentity } from "@/lib/guest";

// POST: verify a repair job (ADR-031), exactly like an order: the API answers 404 for any mismatch and locks
// repeated failures, so this only passes the request through.
export async function POST(request: NextRequest) {
  if (!sameOrigin(request)) return crossSiteRefused();
  const identity = (await chatIdentity(true)) ?? {};
  return relay("/api/v1/repairs/lookup", {
    method: "POST",
    headers: { ...identity, "Content-Type": "application/json" },
    body: await request.text(),
  });
}
