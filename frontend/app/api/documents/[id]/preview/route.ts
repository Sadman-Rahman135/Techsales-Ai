import type { NextRequest } from "next/server";

import { backendFetch } from "@/lib/backend";
import { apiUnavailable } from "@/lib/bff";
import { bffFileUrl } from "@/lib/chat";
import { chatIdentity } from "@/lib/guest";

// GET: an authorized, short-lived link to a cited source (ADR-018). The API checks the caller may see that
// exact version; the link it returns is rewritten to the BFF's file route, the only one the browser can reach.
export async function GET(request: NextRequest, ctx: RouteContext<"/api/documents/[id]/preview">) {
  const { id } = await ctx.params;
  const identity = (await chatIdentity(false)) ?? {};
  const query = new URLSearchParams();
  for (const key of ["version_id", "page"]) {
    const value = request.nextUrl.searchParams.get(key);
    if (value) query.set(key, value);
  }
  let upstream: Response;
  try {
    upstream = await backendFetch(`/api/v1/documents/${encodeURIComponent(id)}/preview?${query}`, {
      headers: identity,
    });
  } catch {
    return apiUnavailable();
  }
  const body = (await upstream.json().catch(() => null)) as { url?: string } | null;
  if (upstream.ok && body?.url) body.url = bffFileUrl(body.url);
  return Response.json(body, { status: upstream.status });
}
