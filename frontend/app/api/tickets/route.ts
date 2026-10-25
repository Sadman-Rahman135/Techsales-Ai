import type { NextRequest } from "next/server";

import { crossSiteRefused, relay, sameOrigin } from "@/lib/bff";

// Staff ticket queue (ADR-029): GET lists (query string passed through), POST files a reviewed ticket.
export async function GET(request: NextRequest) {
  return relay(`/api/v1/tickets${request.nextUrl.search}`);
}

export async function POST(request: NextRequest) {
  if (!sameOrigin(request)) return crossSiteRefused();
  return relay("/api/v1/tickets", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: await request.text(),
  });
}
