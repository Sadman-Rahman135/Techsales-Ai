import type { NextRequest } from "next/server";

import { backendFetch } from "@/lib/backend";
import { apiUnavailable } from "@/lib/bff";

const DATASETS = new Set(["questions", "feedback", "reviews", "tickets", "documents"]);

// GET: a CSV export (ADR-030), streamed from the API with its file name. The API checks the role, redacts and
// audits; the BFF only forwards the date range.
export async function GET(request: NextRequest, ctx: RouteContext<"/api/admin/exports/[dataset]">) {
  const { dataset } = await ctx.params;
  if (!DATASETS.has(dataset)) {
    return Response.json(
      {
        error: {
          code: "NOT_FOUND",
          message: "There's no export with that name.",
          request_id: "bff",
        },
      },
      { status: 404 },
    );
  }
  const params = new URLSearchParams();
  for (const key of ["start", "end"]) {
    const value = request.nextUrl.searchParams.get(key);
    if (value) params.set(key, value);
  }
  let upstream: Response;
  try {
    upstream = await backendFetch(`/api/v1/admin/exports/${dataset}${params.size ? `?${params}` : ""}`);
  } catch {
    return apiUnavailable();
  }
  if (!upstream.ok || !upstream.body) {
    return new Response(await upstream.text(), {
      status: upstream.status,
      headers: { "Content-Type": "application/json" },
    });
  }
  const headers = new Headers({
    "Content-Type": upstream.headers.get("content-type") ?? "text/csv; charset=utf-8",
    "Cache-Control": "private, no-store",
    "X-Content-Type-Options": "nosniff",
  });
  const disposition = upstream.headers.get("content-disposition");
  if (disposition) headers.set("Content-Disposition", disposition);
  return new Response(upstream.body, { headers });
}
