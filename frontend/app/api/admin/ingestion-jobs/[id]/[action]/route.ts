import type { NextRequest } from "next/server";

import { crossSiteRefused, relay, sameOrigin } from "@/lib/bff";

const ACTIONS = new Set(["cancel", "retry"]);

export async function POST(request: NextRequest, ctx: RouteContext<"/api/admin/ingestion-jobs/[id]/[action]">) {
  if (!sameOrigin(request)) return crossSiteRefused();
  const { id, action } = await ctx.params;
  if (!ACTIONS.has(action))
    return Response.json(
      { error: { code: "NOT_FOUND", message: "Unknown action.", request_id: "bff" } },
      { status: 404 },
    );
  return relay(`/api/v1/ingestion-jobs/${encodeURIComponent(id)}/${action}`, { method: "POST" });
}
