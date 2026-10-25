import type { NextRequest } from "next/server";

import { relay } from "@/lib/bff";

// GET ?version_id=…: chunk diagnostics for one version (managers and admins; the API enforces it).
export async function GET(request: NextRequest, ctx: RouteContext<"/api/admin/documents/[id]/chunks">) {
  const { id } = await ctx.params;
  const version = request.nextUrl.searchParams.get("version_id") ?? "";
  return relay(`/api/v1/documents/${encodeURIComponent(id)}/versions/${encodeURIComponent(version)}/chunks`);
}
