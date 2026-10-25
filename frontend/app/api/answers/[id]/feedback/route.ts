import type { NextRequest } from "next/server";

import { crossSiteRefused, relay, sameOrigin } from "@/lib/bff";
import { chatIdentity } from "@/lib/guest";

export async function POST(request: NextRequest, ctx: RouteContext<"/api/answers/[id]/feedback">) {
  if (!sameOrigin(request)) return crossSiteRefused();
  const { id } = await ctx.params;
  const identity = await chatIdentity(false);
  if (!identity) {
    return Response.json(
      { error: { code: "NOT_FOUND", message: "Answer not found.", request_id: "bff" } },
      { status: 404 },
    );
  }
  return relay(`/api/v1/answers/${encodeURIComponent(id)}/feedback`, {
    method: "POST",
    headers: { ...identity, "Content-Type": "application/json" },
    body: await request.text(),
  });
}
