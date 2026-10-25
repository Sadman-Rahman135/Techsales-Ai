import type { NextRequest } from "next/server";

import { crossSiteRefused, relay, sameOrigin } from "@/lib/bff";

// Review queue decisions (ADR-030). PUT resolves or dismisses, DELETE reopens; the API checks the role and audits.
export async function PUT(request: NextRequest, ctx: RouteContext<"/api/admin/reviews/[answerId]">) {
  if (!sameOrigin(request)) return crossSiteRefused();
  const { answerId } = await ctx.params;
  return relay(`/api/v1/admin/reviews/${encodeURIComponent(answerId)}`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: await request.text(),
  });
}

export async function DELETE(request: NextRequest, ctx: RouteContext<"/api/admin/reviews/[answerId]">) {
  if (!sameOrigin(request)) return crossSiteRefused();
  const { answerId } = await ctx.params;
  return relay(`/api/v1/admin/reviews/${encodeURIComponent(answerId)}`, {
    method: "DELETE",
  });
}
