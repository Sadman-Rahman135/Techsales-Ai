import type { NextRequest } from "next/server";

import { crossSiteRefused, relay, sameOrigin } from "@/lib/bff";

// POST: an internal note on a ticket.
export async function POST(request: NextRequest, ctx: RouteContext<"/api/tickets/[id]/notes">) {
  if (!sameOrigin(request)) return crossSiteRefused();
  const { id } = await ctx.params;
  return relay(`/api/v1/tickets/${encodeURIComponent(id)}/notes`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: await request.text(),
  });
}
