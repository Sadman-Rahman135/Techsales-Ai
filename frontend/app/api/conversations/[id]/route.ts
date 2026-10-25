import type { NextRequest } from "next/server";

import { crossSiteRefused, relay, sameOrigin } from "@/lib/bff";
import { chatIdentity } from "@/lib/guest";

const NOT_FOUND = { error: { code: "NOT_FOUND", message: "Conversation not found.", request_id: "bff" } };

export async function GET(_request: NextRequest, ctx: RouteContext<"/api/conversations/[id]">) {
  const { id } = await ctx.params;
  const identity = await chatIdentity(false);
  if (!identity) return Response.json(NOT_FOUND, { status: 404 });
  return relay(`/api/v1/conversations/${encodeURIComponent(id)}`, { headers: identity });
}

export async function DELETE(request: NextRequest, ctx: RouteContext<"/api/conversations/[id]">) {
  if (!sameOrigin(request)) return crossSiteRefused();
  const { id } = await ctx.params;
  const identity = await chatIdentity(false);
  if (!identity) return Response.json(NOT_FOUND, { status: 404 });
  return relay(`/api/v1/conversations/${encodeURIComponent(id)}`, { method: "DELETE", headers: identity });
}
