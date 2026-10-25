import type { NextRequest } from "next/server";

import { crossSiteRefused, relay, sameOrigin } from "@/lib/bff";

export async function POST(request: NextRequest, ctx: RouteContext<"/api/admin/documents/[id]/publish">) {
  if (!sameOrigin(request)) return crossSiteRefused();
  const { id } = await ctx.params;
  const { version_id } = (await request.json()) as { version_id?: unknown };
  return relay(`/api/v1/documents/${encodeURIComponent(id)}/publish`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ version_id: String(version_id ?? "") }),
  });
}
