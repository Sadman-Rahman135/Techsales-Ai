import type { NextRequest } from "next/server";

import { relay } from "@/lib/bff";

// Polled every 2 s by the upload stepper while a job runs.
export async function GET(_request: NextRequest, ctx: RouteContext<"/api/admin/ingestion-jobs/[id]">) {
  const { id } = await ctx.params;
  return relay(`/api/v1/ingestion-jobs/${encodeURIComponent(id)}`);
}
