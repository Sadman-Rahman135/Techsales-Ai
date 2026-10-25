import type { NextRequest } from "next/server";

import { crossSiteRefused, relay, sameOrigin } from "@/lib/bff";

// POST: multipart upload of a new version, streamed to the API unchanged (like /api/admin/documents).
export async function POST(request: NextRequest, ctx: RouteContext<"/api/admin/documents/[id]/versions">) {
  if (!sameOrigin(request)) return crossSiteRefused();
  const { id } = await ctx.params;
  const headers: Record<string, string> = { "Content-Type": request.headers.get("content-type") ?? "" };
  const length = request.headers.get("content-length");
  if (length) headers["Content-Length"] = length;
  return relay(`/api/v1/documents/${encodeURIComponent(id)}/versions`, {
    method: "POST",
    headers,
    body: request.body,
    duplex: "half",
  });
}
