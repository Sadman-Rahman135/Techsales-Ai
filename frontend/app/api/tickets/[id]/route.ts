import type { NextRequest } from "next/server";

import { crossSiteRefused, relay, sameOrigin } from "@/lib/bff";

export async function GET(_request: NextRequest, ctx: RouteContext<"/api/tickets/[id]">) {
  const { id } = await ctx.params;
  return relay(`/api/v1/tickets/${encodeURIComponent(id)}`);
}

// PATCH: status, priority or assignee (the API checks the role and audits the change).
export async function PATCH(request: NextRequest, ctx: RouteContext<"/api/tickets/[id]">) {
  if (!sameOrigin(request)) return crossSiteRefused();
  const { id } = await ctx.params;
  return relay(`/api/v1/tickets/${encodeURIComponent(id)}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: await request.text(),
  });
}
