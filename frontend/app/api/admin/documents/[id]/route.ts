import type { NextRequest } from "next/server";

import { crossSiteRefused, relay, sameOrigin } from "@/lib/bff";

export async function GET(_request: NextRequest, ctx: RouteContext<"/api/admin/documents/[id]">) {
  const { id } = await ctx.params;
  return relay(`/api/v1/documents/${encodeURIComponent(id)}`);
}

// PATCH: change search metadata (managers and admins; the API checks the role and audits the change).
export async function PATCH(request: NextRequest, ctx: RouteContext<"/api/admin/documents/[id]">) {
  if (!sameOrigin(request)) return crossSiteRefused();
  const { id } = await ctx.params;
  return relay(`/api/v1/documents/${encodeURIComponent(id)}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: await request.text(),
  });
}

export async function DELETE(request: NextRequest, ctx: RouteContext<"/api/admin/documents/[id]">) {
  if (!sameOrigin(request)) return crossSiteRefused();
  const { id } = await ctx.params;
  return relay(`/api/v1/documents/${encodeURIComponent(id)}`, { method: "DELETE" });
}
